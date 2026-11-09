"""Every drill result released in the last N days, straight from MiningNewsTerminal.

This drives the email's "Act now" section. It does NOT depend on a release having
collar coordinates (most don't): every MNT drill release in the window is listed.
When Closeology can place the drilling and finds open, stakeable ground beside it
(an edge play from news.py / drill_edges.py), the open hectares + map are attached.

Jurisdiction, best available first:
  1. the matching edge play's region
  2. the drill bank's geolocated holes / province for the same release URL
  3. the province where the announcing company holds the most claims
  4. the bank's country label (Nevada, Mexico, ...) or "INTL"
Falls back to the drill bank (data/keep/drillbank.sqlite) if the MNT API is down.
"""
import os
import re
import json
import sqlite3
import datetime
import urllib.parse
import urllib.request

API = "https://miningnewsterminal.com/api/v1/drills"
DB = "data/keep/drillbank.sqlite"
REGISTRY = "data/keep/claim_registry.parquet"

PROV = {"British Columbia": "BC", "Ontario": "ON", "Quebec": "QC", "Québec": "QC", "Yukon": "YK",
        "Newfoundland & Labrador": "NL", "Newfoundland and Labrador": "NL", "Newfoundland": "NL",
        "Labrador": "NL", "Saskatchewan": "SK", "Manitoba": "MB", "Northwest Territories": "NT",
        "Nova Scotia": "NS", "New Brunswick": "NB", "Alberta": "AB", "Nunavut": "NU",
        "Prince Edward Island": "PE"}
CANADA = set(PROV.values())
SLUG2AB = {"bc": "BC", "on": "ON", "qc": "QC", "yk": "YK", "nl": "NL", "sk": "SK", "mb": "MB",
           "nt": "NT", "ns": "NS", "nb": "NB", "ab": "AB", "nu": "NU"}
AB2SLUG = {v: k for k, v in SLUG2AB.items()}
US = {"Nevada": "NV", "Alaska": "AK", "Idaho": "ID", "Arizona": "AZ", "Montana": "MT",
      "Utah": "UT", "Wyoming": "WY", "Colorado": "CO", "California": "CA", "Washington": "WA",
      "Oregon": "OR", "New Mexico": "NM", "South Dakota": "SD", "Minnesota": "MN"}


# place name -> jurisdiction label (Canadian provinces -> 2-letter, US states -> 2-letter,
# everything else -> country name). Longest phrases are tested first.
LOC = dict(PROV)
LOC.update(US)
LOC.update({k: k for k in [
    "Mexico", "Chile", "Peru", "Argentina", "Brazil", "Colombia", "Ecuador", "Bolivia", "Guyana",
    "Australia", "Finland", "Sweden", "Norway", "Serbia", "Ireland", "Spain", "Portugal", "Greenland",
    "Ghana", "Mali", "Burkina Faso", "Côte d'Ivoire", "Cote d'Ivoire", "Tanzania", "Namibia",
    "Zambia", "Botswana", "Saudi Arabia", "Kazakhstan", "Mongolia", "Philippines", "Indonesia",
    "Papua New Guinea", "New Zealand", "Guatemala", "Honduras", "Nicaragua", "Panama",
    "Dominican Republic", "Turkey", "Türkiye", "Bosnia", "Armenia", "Morocco", "Egypt",
    "Senegal", "Guinea", "Niger", "Nigeria", "Kenya", "Ethiopia", "Eritrea", "Sudan", "Liberia",
    "Sierra Leone", "Democratic Republic of the Congo", "DRC", "Congo", "South Africa", "Zimbabwe",
    "Madagascar", "Mozambique", "Malawi", "Uganda", "Rwanda", "Angola", "Cameroon", "Gabon",
    "Uzbekistan", "Kyrgyzstan", "Kyrgyz Republic", "Vietnam", "Laos", "Japan", "China", "Fiji",
    "Solomon Islands", "Uruguay", "Paraguay", "Venezuela", "El Salvador", "Costa Rica", "Jamaica",
    "Cuba", "Poland", "Germany", "France", "Italy", "Greece", "Bulgaria", "Romania", "Slovakia",
    "Czech Republic", "Austria", "Scotland", "Wales", "England", "Northern Ireland", "Cyprus", "Oman",
    "Kosovo", "North Macedonia", "Montenegro", "Albania", "Georgia", "Malaysia", "Thailand", "India",
    "Pakistan", "Afghanistan", "Iran", "Iraq", "Jordan", "Israel"]})
LOC.update({"Rouyn-Noranda": "QC", "Val-d'Or": "QC", "Val-d’Or": "QC", "Chibougamau": "QC",
            "Duparquet": "QC", "Matagami": "QC", "Nunavik": "QC", "Gaspé": "QC", "Témiscamingue": "QC",
            "Kirkland Lake": "ON", "Sudbury": "ON", "Thunder Bay": "ON", "Wawa": "ON", "Dryden": "ON",
            "Beardmore": "ON", "Geraldton": "ON", "Hemlo": "ON", "Ring of Fire": "ON", "Cobalt, Ontario": "ON",
            "Flin Flon": "MB", "Snow Lake": "MB", "Thompson Nickel Belt": "MB", "La Ronge": "SK",
            "Kivalliq": "NU", "Kitikmeot": "NU", "Baffin Island": "NU", "Dawson": "YK", "Whitehorse": "YK",
            "Mayo": "YK", "Klondike": "YK", "Smithers": "BC", "Stewart": "BC", "Kamloops": "BC",
            "Quesnel": "BC", "Toodoggone": "BC", "Baie Verte": "NL", "Gander": "NL", "Bathurst": "NB",
            "Bathurst Mining Camp": "NB"})
LOC.update({"Sonora": "Mexico", "Durango": "Mexico", "Chihuahua": "Mexico", "Sinaloa": "Mexico",
            "Zacatecas": "Mexico", "Jalisco": "Mexico", "Guerrero": "Mexico", "Oaxaca": "Mexico",
            "Lapland": "Finland", "Western Australia": "Australia", "Queensland": "Australia",
            "New South Wales": "Australia", "Yellowknife": "NT", "Red Lake": "ON", "Timmins": "ON",
            "Abitibi": "QC", "James Bay": "QC", "Eeyou Istchee": "QC", "Golden Triangle": "BC",
            "Atlin": "BC", "Cariboo": "BC", "Labrador Trough": "QC", "Athabasca Basin": "SK"})
_LOC_RE = [(re.compile(r"\b" + re.escape(k) + r"\b"), v) for k, v in
           sorted(LOC.items(), key=lambda kv: -len(kv[0]))]


def _strip_boiler(text):
    """Drop the dateline (head-office city) and the About/QP/forward-looking tail so a
    Vancouver head office doesn't make a Chilean project look like BC."""
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"--\)|/CNW/|\(Newsfile Corp\.[^)]*\)|/PRNewswire/|GLOBE NEWSWIRE\)|ACCESS Newswire|"
                  r"\(BUSINESS WIRE\)--", t[:600])
    if m:
        t = t[m.end():]
    cut = re.search(r"\b(About [A-Z][^.]{0,60}(Corp|Ltd|Inc|Limited|Resources|Metals|Gold|Mining)|"
                    r"Qualified Person|Forward[- ]Looking (Statements|Information)|Cautionary)", t)
    if cut and cut.start() > 400:
        t = t[:cut.start()]
    return t


def _where(headline, body):
    """Most-mentioned jurisdiction in the headline (x5) + body (boilerplate removed)."""
    score = {}
    for txt, w in ((headline or "", 5), (_strip_boiler(re.sub(r"<[^>]+>", " ", body or "")), 1)):
        work = txt
        for rx, lab in _LOC_RE:
            n = len(rx.findall(work))
            if n:
                score[lab] = score.get(lab, 0) + n * w
                work = rx.sub(" ", work)      # 'Western Australia' doesn't also count 'Australia'
    if not score:
        return ""
    best = sorted(score.items(), key=lambda kv: -kv[1])
    if len(best) > 1 and best[0][1] == best[1][1]:
        return ""                             # a tie is not an answer
    return best[0][0]


def _article(event_id, timeout=30):
    try:
        req = urllib.request.Request(f"https://miningnewsterminal.com/api/v1/news/article/{event_id}",
                                     headers={"User-Agent": "closeology-daily/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return ((d.get("article") or {}).get("body_html")) or ""
    except Exception:
        return ""


def _url_key(u):
    if not u:
        return ""
    p = urllib.parse.urlsplit(str(u).strip().lower())
    return (p.netloc.replace("www.", "") + p.path).rstrip("/")


def _fetch(since, timeout=40):
    out, page, pages = [], 1, 1
    while page <= pages and page <= 20:
        q = f"{API}?limit=200&page={page}&include=intervals&since={since}"
        req = urllib.request.Request(q, headers={"User-Agent": "closeology-daily/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        if not d.get("ok"):
            raise RuntimeError("MNT api not ok")
        out += d.get("items", []) or []
        pages = int(d.get("pages") or 1)
        page += 1
    return out


def _bank_fallback(since, db=DB):
    """Same shape as the MNT feed, from the drill bank, if the API is unreachable."""
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute("""select id, company, project, published, url, title from releases
                          where published >= ? and n_intervals > 0 order by published desc""",
                       (since,)).fetchall()
    out = []
    for r in rows:
        iv = con.execute("""select hole_id, length_m, grade, unit, element from intervals
                            where release_id=? and grade is not null
                            order by (length_m * grade) desc limit 1""", (r["id"],)).fetchone()
        best = None
        if iv:
            best = {"hole_id": iv["hole_id"], "length_m": iv["length_m"], "grade": iv["grade"],
                    "unit": iv["unit"], "metal": iv["element"]}
        out.append({"company": r["company"], "project": r["project"], "date": r["published"],
                    "headline": r["title"], "source_url": r["url"], "release_url": r["url"],
                    "best": best})
    con.close()
    return out


def _bank_index(db=DB):
    """url-key -> (province/country label, lat, lon) from the drill bank."""
    idx = {}
    if not os.path.exists(db):
        return idx
    con = sqlite3.connect(db)
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    for rid, url, country, comp, pub in con.execute(
            "select id, url, country, company, published from releases where published >= ?", (cut,)):
        h = con.execute("""select avg(lat), avg(lon) from holes where release_id=?
                           and lat is not null and lon is not null""", (rid,)).fetchone()
        v = (country, h[0], h[1])
        idx[_url_key(url)] = v
        k = (_norm(comp), str(pub or "")[:10])
        if k[0] and (k not in idx or (h[0] is not None and idx[k][1] is None)):
            idx[k] = v
    con.close()
    return idx


_SUFFIX = {"corp", "corporation", "ltd", "limited", "inc", "incorporated", "ulc", "lp", "llc",
           "co", "company", "holdings", "the", "plc", "sa", "nl", "pty", "canada"}


def _norm(name):
    return " ".join(t for t in re.sub(r"[^a-z0-9 ]", " ", str(name or "").lower()).split()
                    if t not in _SUFFIX)


def _registry_place(entries, registry=REGISTRY):
    """For items with a Canadian jurisdiction but no coordinates: a map position from
    the claim registry — claims NAMED after the project (option-proof) first, else the
    median of the announcing company's own claims in that province. Returns
    {i: (juris_or_None, lat, lon)}; juris is filled only when it was unknown."""
    if not entries or not os.path.exists(registry):
        return {}
    import pandas as pd
    r = pd.read_parquet(registry, columns=["prov", "owner", "name", "lat", "lon"])
    r = r[r["lat"].notna() & r["lon"].notna()]
    own = r["owner"].fillna("").astype(str).str.replace(r"\(?\d+(\.\d+)?\s*%\)?", "", regex=True)
    r = r.assign(_own=own.map(lambda o: tuple(_norm(p) for p in re.split(r"[;/]", o))),
                 _name=r["name"].fillna("").astype(str).str.lower())
    out = {}
    for i, (comp, proj, juris) in entries.items():
        slug = AB2SLUG.get(juris) if juris else None
        sub = r[r["prov"] == slug] if slug else r
        c = _norm(comp)
        mine = sub[sub["_own"].map(lambda t: c in t)] if c else sub.iloc[0:0]
        pt = [t for t in _norm(proj).split() if len(t) >= 4 and t not in
              {"gold", "copper", "silver", "project", "property", "zone", "mine", "lake", "creek", "river"}]
        hit = mine.iloc[0:0]
        if pt:
            pool = mine if len(mine) else sub
            hit = pool[pool["_name"].map(lambda n: all(t in n for t in pt))]
            if not len(mine) and len(hit) > 400:     # too generic without the owner
                hit = hit.iloc[0:0]
        if not len(hit):
            if not juris:          # owner name alone can't say WHERE (Senegal project, BC claims)
                continue
            hit = mine
        if not len(hit):
            continue
        if not slug:
            slug = hit["prov"].value_counts().index[0]
            hit = hit[hit["prov"] == slug]
        out[i] = (None if juris else SLUG2AB.get(slug, slug.upper()),
                  float(hit["lat"].median()), float(hit["lon"].median()))
    return out


def _best_txt(b):
    if not b:
        return ""
    if b.get("summary") and not b.get("length_m"):
        return str(b["summary"])
    parts = []
    try:
        if b.get("length_m"):
            parts.append(f"{float(b['length_m']):g} m")
    except Exception:
        pass
    try:
        if b.get("grade") is not None:
            parts.append(f"@ {float(b['grade']):g} {b.get('unit') or ''} {b.get('metal') or ''}".strip())
    except Exception:
        pass
    s = " ".join(parts)
    if b.get("hole_id"):
        s = f"{s} ({b['hole_id']})" if s else str(b["hole_id"])
    return s


CACHE = "site/mnt_drills.json"


def collect(days=3, fetch=True, cache=CACHE):
    """Pull every drill result released in the last `days` days and work out WHERE
    each one is. Writes `cache` and returns the list. Run BEFORE the region builds so
    located Canadian items can be edge-tested for open ground (drillbank_news)."""
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=days)).isoformat()
    src = "miningnewsterminal"
    items = []
    if fetch:
        try:
            items = _fetch(since)
        except Exception as e:
            print("[mnt_drills] MNT API unavailable, using drill bank:", str(e)[:120])
            items, src = _bank_fallback(since), "drillbank"
    else:
        items, src = _bank_fallback(since), "drillbank"
    items = [i for i in items if str(i.get("date") or "")[:10] >= since]
    bank = _bank_index()

    rows, seen = [], set()
    for it in items:
        comp = (it.get("company") or "").strip() or "Unknown company"
        proj = (it.get("project") or "").strip()
        date = str(it.get("date") or "")[:10]
        key = (_norm(comp), _norm(proj), date)
        if key in seen:
            continue
        seen.add(key)
        b = bank.get(_url_key(it.get("source_url"))) or bank.get((_norm(comp), date))
        juris, lat, lon, how = "", None, None, ""
        if b and b[1] is not None:                       # our own geolocated collars
            lat, lon, how = float(b[1]), float(b[2]), "collars"
            juris = PROV.get(b[0] or "", "") or US.get(b[0] or "", "") or (b[0] or "")
        if not juris:                                    # what the release itself says
            body = _article(it["event_id"]) if (fetch and it.get("event_id")) else ""
            juris = _where(it.get("headline"), body)
            if fetch and it.get("event_id"):
                import time; time.sleep(1.1)             # stay well under MNT's 60 req/min
        if not juris and b and b[0]:
            juris = PROV.get(b[0], "") or US.get(b[0], "") or b[0]
        rows.append({"company": comp, "project": proj, "date": date,
                     "headline": it.get("headline") or "", "best": _best_txt(it.get("best")),
                     "url": it.get("release_url") or it.get("source_url") or "",
                     "source_url": it.get("source_url") or "",
                     "juris": juris, "lat": lat, "lon": lon, "placed_by": how})

    # Canadian (or unknown) items without coordinates: place from the claim registry
    need = {i: (r["company"], r["project"], r["juris"]) for i, r in enumerate(rows)
            if r["lat"] is None and (r["juris"] in CANADA or not r["juris"])}
    for i, (jur, la, lo) in _registry_place(need).items():
        r = rows[i]
        r["lat"], r["lon"] = la, lo
        r["placed_by"] = "claims"
        if jur:
            r["juris"] = jur
    meta = {"source": src, "since": since, "n": len(rows),
            "n_canada": sum(1 for r in rows if r["juris"] in CANADA),
            "n_located": sum(1 for r in rows if r["lat"] is not None)}
    print(f"[mnt_drills] collected {meta}")
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump({"meta": meta, "items": rows}, open(cache, "w"))
    except Exception:
        pass
    return rows


def news_items(rows):
    """Located Canadian rows -> {slug: [news_items.json items]} for news.py's edge test."""
    out = {}
    for r in rows or []:
        slug = AB2SLUG.get(r.get("juris"))
        if not slug:
            continue
        it = {"date": r["date"], "company": r["company"], "ticker": "", "project": r["project"],
              "location": r["juris"], "highlight": r["best"], "url": r["source_url"] or r["url"]}
        # only real collar coordinates are passed as a point; a claims-registry position
        # is approximate, so news.py places those by project/occurrence/driller instead
        if r.get("lat") is not None and r.get("placed_by") == "collars":
            it["lat"], it["lon"] = round(r["lat"], 6), round(r["lon"], 6)
        out.setdefault(slug, []).append(it)
    return out


def lines(regions, site="", cache=CACHE, rows=None, limit=60):
    """Email rows for the 'Act now' section: every collected drill result, with the
    open-ground edge attached when the build found one."""
    if rows is None:
        try:
            rows = json.load(open(cache)).get("items", [])
        except Exception:
            rows = []
    edges_by_url, edges_by_co = {}, {}
    for rg in regions or []:
        ab = SLUG2AB.get(rg.get("slug"), (rg.get("slug") or "").upper())
        for e in rg.get("edges", []) or []:
            if not e.get("date") or (e.get("open_ha") or 0) <= 0:
                continue
            edges_by_url[_url_key(e.get("source_url"))] = (ab, e)
            edges_by_co[(_norm(e.get("company")), str(e["date"])[:10])] = (ab, e)

    def _abs(u):
        return (site + u) if (u and site and not u.startswith("http")) else u

    out = []
    for r in rows:
        hit = edges_by_url.get(_url_key(r.get("source_url"))) or edges_by_co.get((_norm(r["company"]), r["date"]))
        juris = (hit[0] if hit else r.get("juris")) or "—"
        open_ha, open_dir, map_url = 0, "", ""
        if hit:
            e = hit[1]
            open_ha, open_dir, map_url = e.get("open_ha") or 0, e.get("open_dir") or "", e.get("map_url") or ""
        elif r.get("lat") is not None and juris in CANADA:
            map_url = (f"app.html?lat={r['lat']:.5f}&lon={r['lon']:.5f}&z={12 if r.get('placed_by') == 'collars' else 11}"
                       f"&region={AB2SLUG.get(juris, '')}&label={urllib.parse.quote(r['company'])}&kind=drill")
        comp, proj = r["company"], r["project"]
        txt = comp + (f" — {proj}" if proj and _norm(proj) not in _norm(comp) else "")
        if r.get("best"):
            txt += f" · {r['best']}"
        if open_ha:
            txt += f" · {round(float(open_ha))} ha open{(' to the ' + open_dir) if open_dir else ''}"
        txt += f" · {r['date']}"
        out.append({"juris": juris if len(juris) <= 3 else juris, "text": txt, "date": r["date"],
                    "hot": bool(open_ha), "open_ha": open_ha, "url": r.get("url", ""),
                    "map_url": _abs(map_url)})
    # open ground first, then the rest of Canada, then everywhere else; newest first
    out.sort(key=lambda l: (0 if l["open_ha"] else 1, 0 if l["juris"] in CANADA else 1,
                            [-ord(c) for c in l["date"]]))
    return out[:limit]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "src")
    regs = []
    if os.path.exists("site/daily_email.json"):
        regs = json.load(open("site/daily_email.json")).get("regions", [])
    rows = collect(days=int(sys.argv[1]) if len(sys.argv) > 1 else 3, cache="/tmp/mnt_drills_test.json")
    for l in lines(regs, rows=rows):
        print(f"  {l['juris']:6} {l['text']}  {'[map]' if l['map_url'] else ''}")
