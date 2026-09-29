"""Bridge: fresh geolocated drill-result releases in the drill bank
(data/keep/drillbank.sqlite) -> per-region news_items.json, which news.py then
geolocates (path #1: explicit lat/lon) and runs the open-ground edge test on.

Why this exists: the drill bank (filled by the newswire crawler — drillbank.yml /
miningnewsterminal.yml) is the freshest, richest drill-news source (assays dated
to the day). It used to feed only the Drill Radar page; the DAILY EMAIL's edge
plays still expected the old fetch_news.py WordPress/RSS output (now dead), so the
email fell back to the 1-3 year lagging government drill layers and repeated the
same handful of plays for days. This module routes the bank's recent, located
releases into the email pipeline so the email shows CURRENT drilling. Read-only on
the bank; it only writes news_items.json.
"""
import os
import json
import sqlite3
import datetime

DB = "data/keep/drillbank.sqlite"
RECENT_DAYS = 120           # match news._fresh() 'hot' window
MAX_PER_REGION = 80

# province/territory name (from geolocate.region_from_latlon) -> Closeology slug
NAME2SLUG = {
    "Ontario": "on", "Quebec": "qc", "Québec": "qc", "British Columbia": "bc",
    "Yukon": "yk", "Saskatchewan": "sk", "Manitoba": "mb", "Alberta": "ab",
    "Nunavut": "nu", "Northwest Territories": "nt", "Nova Scotia": "ns",
    "New Brunswick": "nb", "Newfoundland & Labrador": "nl",
    "Newfoundland and Labrador": "nl", "Prince Edward Island": "pe",
}

# element headline priority when a release has several
_ELEM_RANK = {"Au": 0, "AuEq": 1, "AgEq": 2, "Ag": 3, "Cu": 4, "Ni": 5,
              "Zn": 6, "Pb": 7, "Co": 8, "U": 9, "Li": 10, "Mo": 11}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _headline(ivs):
    """Compose the single most notable interval as 'H m @ G unit El (hole)'.
    Prefer gold/gold-equiv, then the longest interval; fall back to raw."""
    best = None
    best_key = None
    for r in ivs:
        el = (r["element"] or "").strip()
        length = _num(r["length_m"]) or 0.0
        grade = _num(r["grade"])
        if grade is None:
            continue
        key = (_ELEM_RANK.get(el, 50), -length)   # better element, then longer
        if best_key is None or key < best_key:
            best_key, best = key, r
    if best is None:
        return ""
    el = (best["element"] or "").strip()
    length = _num(best["length_m"])
    grade = _num(best["grade"])
    unit = (best["unit"] or "").strip()
    hole = (best["hole_id"] or "").strip()
    parts = []
    if length:
        parts.append(f"{length:g} m")
    if grade is not None:
        parts.append(f"@ {grade:g} {unit} {el}".rstrip())
    s = " ".join(parts).strip()
    if hole:
        s = f"{s} ({hole})" if s else hole
    return s


def build(region_slugs, db=DB, out_root="data"):
    """Write data/<slug>/news_items.json for every slug in region_slugs from the
    bank's releases published within RECENT_DAYS that have >=1 geolocated hole.
    Returns {slug: n_items}. Regions with no fresh located release get an EMPTY
    items file (so a stale prior file can't linger)."""
    counts = {s: 0 for s in region_slugs}
    if not os.path.exists(db):
        print("[drillbank_news] no bank at", db, "- skipping")
        return counts
    import sys
    if "src" not in sys.path:
        sys.path.insert(0, "src")
    from newswire import geolocate as G

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cutoff = (datetime.date.today() - datetime.timedelta(days=RECENT_DAYS)).isoformat()
    cur.execute("""select id, company, ticker, project, published, url, title
                   from releases where published >= ? order by published desc""", (cutoff,))
    rels = cur.fetchall()

    buckets = {s: [] for s in region_slugs}
    seen = {s: set() for s in region_slugs}     # de-dup by (company, project, date)
    for r in rels:
        h = con.execute("""select lat, lon from holes
                           where release_id=? and lat is not null and lon is not null""",
                        (r["id"],)).fetchall()
        if not h:
            continue
        lat = sum(x["lat"] for x in h) / len(h)
        lon = sum(x["lon"] for x in h) / len(h)
        prov = G.region_from_latlon(lat, lon)
        slug = NAME2SLUG.get(prov)
        if slug not in buckets:
            continue
        ivs = con.execute("""select hole_id, from_m, to_m, length_m, element, grade, unit, raw
                             from intervals where release_id=?""", (r["id"],)).fetchall()
        item = {
            "date": r["published"], "company": r["company"] or "",
            "ticker": r["ticker"] or "", "project": r["project"] or "",
            "location": prov or "", "highlight": _headline(ivs),
            "url": r["url"] or "", "lat": round(lat, 6), "lon": round(lon, 6),
        }
        key = (item["company"].lower(), item["project"].lower(), item["date"])
        if key in seen[slug]:
            continue
        seen[slug].add(key)
        buckets[slug].append(item)

    for slug in region_slugs:
        d = os.path.join(out_root, slug)
        if not os.path.isdir(d):
            continue
        items = buckets[slug][:MAX_PER_REGION]
        json.dump({"items": items}, open(os.path.join(d, "news_items.json"), "w"))
        counts[slug] = len(items)
    con.close()
    total = sum(counts.values())
    print(f"[drillbank_news] wrote news_items.json for {len([s for s in counts if counts[s]])} "
          f"region(s), {total} fresh located releases: "
          + ", ".join(f"{s}={n}" for s, n in counts.items() if n))
    return counts


if __name__ == "__main__":
    import sys
    build(sys.argv[1:] or ["bc", "on", "yk", "qc", "sk", "mb", "nl", "nt", "ns", "ab", "nu"])
