"""Drill Radar — Project Closeology surface over the newswire drill bank.

Reads data/keep/drillbank.sqlite and builds site/drill_radar.html: a map + table
of recent, geolocated drill results (company, project, best intercept, location,
link to the release), and flags results that sit on or beside open ground so
they can seed Closeology leads. Also writes site/drill_radar.json for reuse.

Runs after the newswire crawl in build_all; safe to build from an empty bank."""
import os
import re
import json
import sqlite3
import site_theme as T

_VEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")


def _ven(p):
    try:
        return open(os.path.join(_VEN, p)).read()
    except Exception:
        return ""

DB = os.path.join("data", "keep", "drillbank.sqlite")
# rough in-situ value weights ($/unit) just to rank "best" intercepts across
# elements — NOT a resource estimate, only for sorting what to show first
_VAL = {"Au": 75.0, "AuEq": 75.0, "Ag": 0.9, "Pd": 40.0, "Pt": 35.0, "Cu": 90.0,
        "Ni": 180.0, "Co": 300.0, "Zn": 28.0, "Pb": 20.0, "U": 130.0, "U3O8": 110.0,
        "Li": 90.0, "Li2O": 90.0, "Sn": 250.0, "Mo": 400.0, "CuEq": 90.0, "AgEq": 0.9}


def _score_interval(el, grade, length, unit):
    if grade is None or length is None:
        return 0.0
    v = _VAL.get(el, 5.0)
    g = grade / 100.0 if unit == "%" else (grade / 1000.0 if unit == "ppm" else grade)
    return v * g * length


def _load(con):
    rows = con.execute("""
        SELECT r.id,r.source,r.url,r.company,r.title,r.published,r.country,r.project,
               r.n_holes,r.n_intervals
        FROM releases r WHERE r.status='ok'
        ORDER BY COALESCE(r.published,'') DESC, r.fetched_at DESC""").fetchall()
    items = []
    for (rid, source, url, company, title, pub, country, project, nh, ni) in rows:
        holes = con.execute("SELECT hole_id,lat,lon,depth_m,azimuth,dip FROM holes "
                            "WHERE release_id=? AND lat IS NOT NULL", (rid,)).fetchall()
        ivs = con.execute("SELECT hole_id,from_m,to_m,length_m,element,grade,unit,is_subinterval "
                          "FROM intervals WHERE release_id=?", (rid,)).fetchall()
        # per-hole: best interval (for ranking) AND the FULL list of intervals
        by_hole, all_by_hole = {}, {}
        best, best_s = None, -1
        for (hid, fr, to, ln, el, gr, un, sub) in ivs:
            all_by_hole.setdefault(hid, []).append(
                {"from": fr, "to": to, "len": ln, "el": el, "grade": gr, "unit": un, "sub": bool(sub)})
            if sub:
                continue
            sc = _score_interval(el, gr, ln, un)
            token = {"from": fr, "to": to, "len": ln, "el": el, "grade": gr, "unit": un, "sc": sc}
            b = by_hole.get(hid)
            if b is None or sc > b["sc"]:
                by_hole[hid] = token
            if sc > best_s:
                best_s = sc
                best = {"hole": hid, "len": ln, "el": el, "grade": gr, "unit": un}
        holes_d = []
        for (hid, lat, lon, depth, az, dip) in holes:
            holes_d.append({"hole": hid, "lat": lat, "lon": lon, "depth_m": depth,
                            "azimuth": az, "dip": dip, "best": by_hole.get(hid),
                            "intervals": all_by_hole.get(hid, [])})
        pt = None
        if holes:
            clat = sum(h[1] for h in holes) / len(holes)
            clon = sum(h[2] for h in holes) / len(holes)
            pt = {"lat": round(clat, 5), "lon": round(clon, 5), "n": len(holes)}
        items.append({"id": rid, "source": source, "url": url,
                      "company": company or (title or "")[:60], "title": title,
                      "published": pub, "country": country, "project": project,
                      "n_holes": nh, "n_intervals": ni, "best": best, "pt": pt,
                      "holes": holes_d, "geo": bool(holes)})
    return items


_NAME2SLUG = {"Ontario": "on", "Quebec": "qc", "British Columbia": "bc", "Yukon": "yk",
              "Newfoundland & Labrador": "nl", "Newfoundland": "nl", "Labrador": "nl",
              "Saskatchewan": "sk", "Manitoba": "mb", "Northwest Territories": "nt",
              "Nova Scotia": "ns", "New Brunswick": "nb", "Alberta": "ab", "Nunavut": "nu"}


def _drill_open_ground(items, halo_m=1000):
    """For each drill program, the OPEN stakeable ground around the whole cluster
    of holes: tile a halo over all the holes and drop any cell that a currently
    active claim touches. This is what you could peg on the geology they just
    drilled. Uses the holes' own jurisdiction's live claim fabric."""
    import math
    import geopandas as gpd
    from shapely.geometry import box
    from shapely.geometry import mapping as _map
    from newswire import geolocate
    from config import GRID_M
    feats, cache = [], {}
    for it in items:
        holes = [(h["lat"], h["lon"]) for h in it.get("holes", []) if h.get("lat") is not None]
        if not holes:
            continue
        prov = geolocate.region_from_latlon(holes[0][0], holes[0][1])
        slug = _NAME2SLUG.get(prov)
        if not slug:
            continue
        if slug not in cache:
            cp = os.path.join("data", slug, "claims.parquet")
            try:
                cache[slug] = gpd.read_parquet(cp) if os.path.exists(cp) else None
            except Exception:
                cache[slug] = None
        claims = cache[slug]
        ref = sum(h[0] for h in holes) / len(holes)
        dlat = GRID_M / 111320.0
        dlon = GRID_M / (111320.0 * max(0.2, math.cos(math.radians(ref))))
        steps = int(halo_m // GRID_M) + 1
        cells = set()
        for (la, lo) in holes:
            ci, cj = int(lo // dlon), int(la // dlat)
            for di in range(-steps, steps + 1):
                for dj in range(-steps, steps + 1):
                    cells.add((ci + di, cj + dj))
        cells = list(cells)
        polys = [box(i * dlon, j * dlat, (i + 1) * dlon, (j + 1) * dlat) for (i, j) in cells]
        openmask = [True] * len(polys)
        if claims is not None and len(claims):
            try:
                cg = gpd.GeoDataFrame(geometry=polys, crs="EPSG:4326")
                cl = claims.to_crs("EPSG:4326")
                hit = gpd.sjoin(cg, cl[[cl.geometry.name]], predicate="intersects", how="left")
                taken = set(hit[hit.index_right.notna()].index.tolist())
                openmask = [i not in taken for i in range(len(polys))]
            except Exception:
                pass
        for pg, ok in zip(polys, openmask):
            if ok:
                feats.append({"type": "Feature", "properties": {"rid": it["id"]},
                              "geometry": _map(pg)})
    return {"type": "FeatureCollection", "features": feats}


_SUFFIX = {"ltd", "ltée", "ltee", "limited", "inc", "incorporated", "corp", "corporation",
           "co", "company", "ulc", "llc", "lp", "plc", "sa", "nl",
           "resources", "resource", "minerals", "mineral", "metals", "metal", "mining",
           "mines", "mine", "gold", "silver", "copper", "exploration", "explorations",
           "royalties", "royalty", "ventures", "venture", "corporation minière", "minière",
           "canada", "canadian", "usa", "holdings", "holding", "capital", "group", "and",
           "the", "de", "of", "energy", "uranium", "lithium", "nickel", "zinc"}


def _name_tokens(name):
    """Distinctive uppercase tokens of a company/owner name, dropping corporate
    boilerplate and ownership %/qualifiers so 'Brixton Metals Corp.' and
    'BRIXTON METALS CORPORATION - 100%' both reduce to {BRIXTON}."""
    s = re.sub(r"\([^)]*\)", " ", str(name or ""))           # (34%) etc.
    s = re.sub(r"[:\-]?\s*\d+(?:\.\d+)?\s*%", " ", s)          # 80.000% / - 100%
    s = re.sub(r"[^A-Za-zÀ-ÿ ]", " ", s).lower()
    toks = [t for t in s.split() if t not in _SUFFIX and len(t) >= 3]
    return set(toks)


def _owner_parties(owner):
    """Split a multi-holder owner string into individual parties' token sets."""
    parts = re.split(r"[;/]", str(owner or ""))
    return [_name_tokens(p) for p in parts if p.strip()]


def _drill_company_claims(items, bbox_km=35, seed_m=1500, cap=1500):
    """The drilling company's PROPERTY — the full contiguous claim block the holes
    sit in, so you can see where their ground starts and ends and judge what's open
    to stake around it. Claim cells are contiguous, so we dissolve the nearby claim
    fabric and keep the connected block(s) the holes touch (works in every province,
    including Ontario where no holder is published). Where OWNER_NAME exists and
    matches the company, we tighten the block to that holder so it can't bleed into
    a neighbour's abutting ground. Emits the individual cells (hover = tenure/expiry)
    plus a dissolved outline feature per program (the property boundary + area)."""
    import math
    import geopandas as gpd
    from shapely.geometry import mapping as _map
    from shapely.ops import unary_union
    from newswire import geolocate
    feats, cache = [], {}
    for it in items:
        holes = [(h["lat"], h["lon"]) for h in it.get("holes", []) if h.get("lat") is not None]
        if not holes:
            continue
        prov = geolocate.region_from_latlon(holes[0][0], holes[0][1])
        slug = _NAME2SLUG.get(prov)
        if not slug:
            continue
        if slug not in cache:
            cp = os.path.join("data", slug, "claims.parquet")
            try:
                cache[slug] = gpd.read_parquet(cp) if os.path.exists(cp) else None
            except Exception:
                cache[slug] = None
        claims = cache[slug]
        if claims is None or not len(claims):
            continue
        clat = sum(h[0] for h in holes) / len(holes)
        clon = sum(h[1] for h in holes) / len(holes)
        dlat = bbox_km * 1000 / 111320.0
        dlon = bbox_km * 1000 / (111320.0 * max(0.2, math.cos(math.radians(clat))))
        try:
            cand = claims.cx[clon - dlon:clon + dlon, clat - dlat:clat + dlat].to_crs("EPSG:4326").reset_index(drop=True)
        except Exception:
            continue
        if not len(cand):
            continue
        try:
            candm = cand.to_crs(3347)
            hpm = gpd.GeoSeries(gpd.points_from_xy([h[1] for h in holes], [h[0] for h in holes]),
                                crs="EPSG:4326").to_crs(3347)
            seed = hpm.buffer(seed_m).union_all()
            # dissolve the local claim fabric; keep the connected component(s) the
            # drilled block touches — that contiguous block traces the property.
            merged = unary_union(list(candm.geometry.values))
            comps = list(getattr(merged, "geoms", [merged]))
            block = unary_union([g for g in comps if g.intersects(seed)])
            if block.is_empty:
                continue
            in_block = candm.geometry.intersects(block).values
        except Exception:
            continue
        owncol = next((c for c in ["OWNER_NAME", "owner", "HOLDER", "CLIENT_NAME"] if c in cand.columns), None)
        idcol = next((c for c in ["TENURE_NUMBER_ID", "claim", "tenure", "TENURE_ID"] if c in cand.columns), None)
        namecol = next((c for c in ["CLAIM_NAME", "NAME", "claim_name"] if c in cand.columns), None)
        expcol = next((c for c in ["GOOD_TO_DATE", "EXPIRY_DATE", "expiry"] if c in cand.columns), None)
        ctoks = _name_tokens(it.get("company"))
        idx_block = [i for i in range(len(cand)) if in_block[i]]
        # Property definition. Where the province PUBLISHES the holder (BC/YK/SK/QC/
        # NL/MB) and it matches the driller, the property is exactly that holder's
        # cells — precise start/end. Where it doesn't (Ontario), we can only trace
        # the contiguous staked block the holes sit in; if that block is really a
        # multi-company camp (implausibly large) we clip it to the local footprint
        # and flag it as unverified rather than drawing a giant wrong boundary.
        owner_idx = []
        if owncol and ctoks:
            for i in range(len(cand)):
                for party in _owner_parties(str(cand.iloc[i][owncol])):
                    if ctoks & party:
                        owner_idx.append(i)
                        break
        bounded, holder = False, None
        if owner_idx:
            sel, matched = owner_idx, "owner"
            import collections
            names = collections.Counter(
                re.sub(r"\s*[-:]?\s*\d+(?:\.\d+)?%\s*$", "", str(cand.iloc[i][owncol])).strip()
                for i in owner_idx if str(cand.iloc[i][owncol]).strip())
            holder = names.most_common(1)[0][0] if names else None
        else:
            sel, matched = idx_block, "block"
            if not sel:
                continue
            if unary_union(list(candm.iloc[sel].geometry.values)).area / 1e4 > 12000:
                tight = hpm.buffer(4000).union_all()
                sel = [i for i in idx_block if candm.iloc[i].geometry.intersects(tight)]
                bounded = True
        if not sel:
            continue
        selm = candm.iloc[sel]
        prop_geom = unary_union(list(selm.geometry.values))
        area_ha = round(prop_geom.area / 10000.0)
        # dissolved outline (the property boundary + size) — drawn as a bold border
        try:
            outline = gpd.GeoSeries([prop_geom], crs=3347).to_crs(4326).iloc[0]
            feats.append({"type": "Feature",
                          "properties": {"rid": it["id"], "matched": "outline",
                                         "area_ha": area_ha, "n_cells": len(sel),
                                         "company": it.get("company"), "holder": holder,
                                         "holder_known": bool(owner_idx), "bounded": bounded},
                          "geometry": _map(outline)})
        except Exception:
            pass
        added = 0
        for i in sel:
            r = cand.iloc[i]
            owner = str(r[owncol]) if owncol else ""
            props = {"rid": it["id"], "matched": matched,
                     "owner": re.sub(r"\s*[-:]?\s*\d+(?:\.\d+)?%\s*$", "", owner).strip() or None,
                     "claim": str(r[idcol]) if idcol and r[idcol] is not None else None,
                     "cname": str(r[namecol]) if namecol and r[namecol] is not None else None,
                     "expiry": str(r[expcol]) if expcol and r[expcol] is not None else None}
            try:
                feats.append({"type": "Feature", "properties": props, "geometry": _map(r.geometry)})
                added += 1
            except Exception:
                pass
            if added >= cap:
                break
    return {"type": "FeatureCollection", "features": feats}


def _holes_geojson(items):
    feats = []
    for it in items:
        for h in it.get("holes", []):
            b = h.get("best")
            assay = (f"{b['len']:g} m @ {b['grade']:g} {b['unit']} {b['el']}"
                     if b and b.get("grade") is not None and b.get("len") is not None else "")
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [h["lon"], h["lat"]]},
                          "properties": {"company": it["company"], "project": it.get("project") or "",
                                         "region": it.get("country") or "", "hole": h["hole"],
                                         "depth_m": h.get("depth_m"), "azimuth": h.get("azimuth"),
                                         "dip": h.get("dip"), "assay": assay,
                                         "rid": it["id"],
                                         "intervals": [
                                             {"f": iv.get("from"), "t": iv.get("to"), "l": iv.get("len"),
                                              "e": iv.get("el"), "g": iv.get("grade"), "u": iv.get("unit"),
                                              "s": iv.get("sub")} for iv in h.get("intervals", [])],
                                         "date": (it.get("published") or "")[:10], "url": it["url"]}})
    return {"type": "FeatureCollection", "features": feats}


_CA_REGIONS = {
    "canada", "ontario", "quebec", "québec", "british columbia", "alberta",
    "saskatchewan", "manitoba", "yukon", "nunavut", "northwest territories",
    "nova scotia", "new brunswick", "prince edward island",
    "newfoundland", "newfoundland & labrador", "newfoundland and labrador", "labrador",
}


def _is_canada(country):
    c = (country or "").strip().lower()
    if not c:
        return False
    if c in _CA_REGIONS:
        return True
    # tolerate "..., Canada" and province substrings
    return "canada" in c or any(r in c for r in _CA_REGIONS if len(r) > 6)


def build(site_dir="site"):
    from newswire import store as _store
    if not os.path.exists(_store.DB_PATH):
        _write(site_dir, [], {"releases": 0})
        return
    con = sqlite3.connect(_store.DB_PATH)
    try:
        items = _load(con)
        st = _store.stats(con)
    finally:
        con.close()
    # Closeology is Canada-only for now, and every radar item must be viewable on
    # the map — so the radar/map/open-ground use only Canadian, geolocated items.
    # (The bank + MineModelingPro keep everything, worldwide.)
    items = [i for i in items if _is_canada(i.get("country")) and i.get("geo") and i.get("pt")]
    json.dump(_holes_geojson(items), open(os.path.join(site_dir, "drill_holes.geojson"), "w"))
    try:
        og = _drill_open_ground(items)
        json.dump(og, open(os.path.join(site_dir, "drill_open.geojson"), "w"))
        print(f"[drill_radar] open ground around drilling: {len(og['features'])} cells")
    except Exception as e:
        print("[drill_radar] open-ground calc skipped:", str(e)[:120])
    try:
        cc = _drill_company_claims(items)
        json.dump(cc, open(os.path.join(site_dir, "drill_claims.geojson"), "w"))
        n_own = sum(1 for f in cc["features"] if f["properties"].get("matched") == "owner")
        print(f"[drill_radar] drilling-company claims: {len(cc['features'])} cells "
              f"({n_own} owner-matched)")
    except Exception as e:
        print("[drill_radar] company-claims calc skipped:", str(e)[:120])
    _write(site_dir, items, st)


def _best_str(b):
    if not b:
        return ""
    g = f"{b['grade']:g}".rstrip("0").rstrip(".") if b.get("grade") is not None else "?"
    ln = f"{b['len']:g}" if b.get("len") is not None else "?"
    return f"{ln} m @ {g} {b['unit']} {b['el']}"


def _write(site_dir, items, st):
    os.makedirs(site_dir, exist_ok=True)
    geo = [i for i in items if i["geo"] and i["pt"]]
    json.dump({"generated_items": len(items), "geolocated": len(geo), "stats": st,
               "items": items[:1000]},
              open(os.path.join(site_dir, "drill_radar.json"), "w"))
    pts = [{"lat": i["pt"]["lat"], "lon": i["pt"]["lon"], "c": i["company"],
            "b": _best_str(i["best"]), "u": i["url"], "n": i["pt"]["n"]} for i in geo[:1500]]
    def _map_link(i):
        if not (i["geo"] and i["pt"]):
            return ""
        from urllib.parse import quote
        u = (f"app.html?lat={i['pt']['lat']}&lon={i['pt']['lon']}&z=13&kind=drill"
             f"&label={quote((i['company'] or 'Drill')[:50])}")
        return f'<a class="mapbtn" href="{u}">🗺 map</a>'
    rows = "".join(
        f'<tr><td class=d>{(i["published"] or "")[:10]}</td><td>{_esc(i["company"])}'
        f'<div class=t>{_esc((i["title"] or "")[:110])}</div></td>'
        f'<td>{_esc(i["country"] or "")}</td>'
        f'<td class=b>{_esc(_best_str(i["best"]))}</td>'
        f'<td class=n>{i["n_holes"] or "—"}</td>'
        f'<td>{_map_link(i)}</td>'
        f'<td><a href="{_esc(i["url"])}" target=_blank rel=noopener>release ↗</a></td></tr>'
        for i in items[:400])
    empty = ("<p class=note>The drill bank is still filling — the daily crawl adds new "
             "mining releases each build. Come back after the next run.</p>" if not items else "")
    html = _PAGE.format(fonts=T.FONTS, css=T.THEME_CSS, header=T.header("drill_radar.html"),
                        footer=T.footer(), rows=rows, empty=empty,
                        n=len(items), geo=len(geo),
                        holes=st.get("holes", 0), ivals=st.get("intervals", 0),
                        leaflet_css=_ven("leaflet.css"), leaflet_js=_ven("leaflet.js"),
                        pts=json.dumps(pts, separators=(",", ":")))
    open(os.path.join(site_dir, "drill_radar.html"), "w").write(html)
    print(f"[drill_radar] drill_radar.html — {len(items)} releases, {len(geo)} geolocated, "
          f"{st.get('holes',0)} holes, {st.get('intervals',0)} intervals")


def _esc(s):
    return re.sub(r"[<>&]", lambda m: {"<": "&lt;", ">": "&gt;", "&": "&amp;"}[m.group()], str(s or ""))


_PAGE = r"""<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Drill Radar · Project Closeology</title>{fonts}
<style>{leaflet_css}</style><script>{leaflet_js}</script>
<style>{css}
.wrap2{{max-width:1180px;margin:0 auto;padding:20px 22px 60px;}}
.kpis{{display:flex;gap:26px;margin:8px 0 16px;flex-wrap:wrap;}}
.kpis div b{{font-family:'Bitter',serif;font-size:22px;color:var(--red);display:block;}}
.kpis div span{{font-size:12px;color:var(--mut);}}
#dmap{{height:420px;border:1px solid var(--line);border-radius:12px;margin-bottom:18px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;}}
th{{font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);}}
td.d{{white-space:nowrap;color:var(--mut);}} td.b{{font-weight:600;}} td.n{{text-align:center;}}
.t{{color:var(--mut);font-size:11.5px;margin-top:2px;}}
.note{{color:var(--mut);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;}}
.mapbtn{{display:inline-block;font-weight:600;font-size:12px;color:#fff !important;background:var(--red);padding:2px 9px;border-radius:6px;text-decoration:none;white-space:nowrap;}}
.mapbtn:hover{{opacity:.9;}}
.lead2{{color:var(--mut);max-width:820px;}}
</style></head><body>
{header}
<div class="wrap2">
  <div class="hero"><h1>Drill Radar</h1><div class="rule"></div>
  <p class="lead2">Fresh drill-hole results pulled from the mining newswires, parsed for
  collar coordinates and assay intercepts, and placed on the map where the geology allows.
  Feeds Project Closeology's open-ground screen and banks every collar + assay for
  MineModelingPro deposit modelling.</p></div>
  <div class="kpis">
    <div><b>{n}</b><span>releases parsed</span></div>
    <div><b>{geo}</b><span>geolocated</span></div>
    <div><b>{holes}</b><span>drill holes banked</span></div>
    <div><b>{ivals}</b><span>assay intervals</span></div>
  </div>
  {empty}
  <div id=dmap></div>
  <table><thead><tr><th>Date</th><th>Company / release</th><th>Region</th>
    <th>Best intercept</th><th>Holes</th><th>Map</th><th></th></tr></thead>
  <tbody>{rows}</tbody></table>
</div>
{footer}
<script>
const PTS={pts};
const map=L.map('dmap').setView([58,-96],3);
L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:17,attribution:'&copy; OpenTopoMap'}}).addTo(map);
const g=[];
PTS.forEach(p=>{{const m=L.circleMarker([p.lat,p.lon],{{radius:6,color:'#7a1620',weight:1,fillColor:'#D71920',fillOpacity:.7}});
  m.bindPopup(`<b>${{p.c}}</b><br>${{p.b||''}}<br>${{p.n}} hole(s) located<br>`+
    `<a href="app.html?lat=${{p.lat}}&lon=${{p.lon}}&z=13&kind=drill&label=${{encodeURIComponent(p.c||'Drill')}}">🗺 see on the map (vs open ground)</a> · `+
    `<a href="${{p.u}}" target=_blank>release ↗</a>`);
  m.addTo(map); g.push([p.lat,p.lon]);}});
if(g.length) map.fitBounds(g,{{padding:[30,30],maxZoom:9}});
</script></body></html>
"""
