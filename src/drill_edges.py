"""Edge-play detector (both provinces).

The only drilling/work that creates a staking opportunity is a *recent* effort
that sits ON a held claim with genuinely open, stakeable ground immediately next
to it — because that is the one geometry where the mineralization can run off the
claim onto ground you could still peg. Work in the middle of a large claim block
is irrelevant: everything around it is already held.

Point source per region (auto-detected):
  • Ontario  -> drillholes.parquet  (OGS Ontario Drill Hole Database). Carries the
               operator, property, year, commodity (ELEMENTS) and — when logged —
               an assay intercept and an AFRI assessment-file reference in COMMENTS.
  • BC       -> spend_reports.parquet (ARIS assessment reports). Carries the operator,
               year, dollars spent and a DIRECT source URL to the report (which holds
               the assays); commodity comes from the linked MINFILE if present.

For each recent point we draw an ~800 m halo, subtract every held claim + lease +
park + reserve, and keep it only if a meaningful slice of the halo is open ground
(>=12%) yet the point is not effectively standing on open ground (<=97%). Survivors
are clustered by operator + property into one 'play', with the open ground quantified
(hectares + bearing), the assay/commodity surfaced, and the data source linked so you
can judge immediately whether it is worth chasing.
"""
import os
import re
import math
import datetime
import pandas as pd
import geopandas as gpd
from shapely.ops import unary_union
from shapely import make_valid

HALO_M = 800.0
OPEN_MIN = 0.12
OPEN_MAX = 0.97

# assay intercepts logged in free text, e.g. "11.1 m @ 115.19 g/t Au", "2.3 g/t Au over 5m"
_ASSAY = re.compile(
    r"\d+(?:\.\d+)?\s*m\s*@\s*\d+(?:\.\d+)?\s*(?:g/?t|gpt|%|ppm|oz/?t|opt)\s*[A-Za-z]{0,3}"
    r"|\d+(?:\.\d+)?\s*(?:g/?t|gpt|%|ppm|oz/?t|opt)\s*[A-Za-z]{0,3}\s*over\s*\d+(?:\.\d+)?\s*m",
    re.I)
_AFRI = re.compile(r"\bAFRI\s*([0-9][0-9A-Z]{4,})\b", re.I)


def _valid(p):
    if p is None:
        return None
    return p if p.is_valid else make_valid(p)


def _union(polys):
    gg = [_valid(p) for p in polys if p is not None and not p.is_empty]
    gg = [g for g in gg if g is not None and not g.is_empty]
    return unary_union(gg) if gg else None


def _bearing(dx, dy):
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((ang + 22.5) // 45) % 8]


def _s(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _assay_from(comment):
    c = _s(comment)
    if not c:
        return "", ""
    hits = _ASSAY.findall(c) if False else [m.group(0) for m in _ASSAY.finditer(c)]
    assay = "; ".join(dict.fromkeys(h.strip() for h in hits))[:120]
    afri = ""
    m = _AFRI.search(c)
    if m:
        afri = m.group(1).upper()
    return assay, afri


def _layers(region_dir, metric):
    """held (claims + leases) and nostake (parks/national parks/reserves)."""
    def rd(fn):
        p = os.path.join(region_dir, fn)
        return gpd.read_parquet(p).to_crs(metric) if os.path.exists(p) else None
    claims = rd("claims.parquet")
    held_parts = [claims[["geometry"]]] if claims is not None else []
    lease = rd("leases.parquet")
    if lease is not None:
        held_parts.append(lease[["geometry"]])
    held = gpd.GeoDataFrame(pd.concat(held_parts, ignore_index=True), geometry="geometry", crs=metric) if held_parts else None
    nostake = []
    for fn in ("parks.parquet", "national_parks.parquet", "reserves.parquet"):
        g = rd(fn)
        if g is not None and len(g):
            nostake.append(g[["geometry"]])
    nostake = gpd.GeoDataFrame(pd.concat(nostake, ignore_index=True), geometry="geometry", crs=metric) if nostake else None
    return claims, held, nostake


def _source_cfg(region_dir):
    """Pick the recent-activity point source; return (df4326, cfg) or (None, None)."""
    on = os.path.join(region_dir, "drillholes.parquet")
    if os.path.exists(on):
        d = gpd.read_parquet(on)
        return d, {
            "kind": "drill", "recent": 3, "hot": 1,
            "company": "COMPANY_NAME", "prop": "PROPERTY_NAME", "year": "YEAR_DRILLED",
            "elems": "ELEMENTS", "comments": "COMMENTS", "spend": None,
            "source": "OGS Ontario Drill Hole Database",
            "source_url": "https://www.geologyontario.mines.gov.on.ca/",
        }
    bc = os.path.join(region_dir, "spend_reports.parquet")
    if os.path.exists(bc):
        d = gpd.read_parquet(bc)
        return d, {
            "kind": "aris", "recent": 2, "hot": 1, "min_spend": 50000,
            "company": "operator", "prop": None, "year": "year",
            "elems": None, "comments": None, "spend": "spend", "url": "url",
            "source": "BC ARIS assessment report",
            "source_url": "https://apps.nrs.gov.bc.ca/pub/aris/",
        }
    return None, None


def _claim_id(claims):
    if claims is None:
        return None
    for c in ("claim", "TENURE_NUMBER_ID", "CLAIM_NAME"):
        if c in claims.columns:
            return c
    return None


def find(region_dir, metric):
    empty = {"plays": [], "point_feats": [], "open_feats": []}
    d, cfg = _source_cfg(region_dir)
    if d is None:
        return empty
    yr = datetime.date.today().year
    d = d.copy()
    d["_Y"] = pd.to_numeric(d.get(cfg["year"]), errors="coerce")
    rec = d[(d["_Y"] >= yr - cfg["recent"]) & (d["_Y"] <= yr)].copy()
    if cfg.get("min_spend") and cfg.get("spend") in rec.columns:
        rec = rec[pd.to_numeric(rec[cfg["spend"]], errors="coerce").fillna(0) >= cfg["min_spend"]]
    if cfg["kind"] == "aris":     # drop reports with no named operator (can't judge them)
        rec = rec[rec[cfg["company"]].apply(lambda v: bool(_s(v)))]
    if not len(rec):
        return empty
    rec = gpd.GeoDataFrame(rec, geometry="geometry", crs="EPSG:4326").to_crs(metric).reset_index(drop=True)

    claims, held, nostake = _layers(region_dir, metric)
    if claims is None or held is None:
        return empty
    cid = _claim_id(claims)
    csi, hsi = claims.sindex, held.sindex
    nsi = nostake.sindex if nostake is not None else None

    def _clip(geom, buf):
        try:
            inter = geom.intersection(buf)
        except Exception:
            g = _valid(geom)
            inter = g.intersection(buf) if g is not None else None
        return inter if (inter is not None and not inter.is_empty) else None

    rows = []
    for _, row in rec.iterrows():
        pt = row.geometry
        if pt is None or pt.is_empty:
            continue
        claim_id = None
        for i in csi.query(pt, predicate="intersects"):   # claims that contain this point
            cg = claims.geometry.iloc[int(i)]
            if cg is not None and cg.contains(pt):
                claim_id = _s(claims[cid].iloc[int(i)]) if cid else "held"
                break
        if claim_id is None:                 # not on held ground -> not an edge-of-block play
            continue
        buf = pt.buffer(HALO_M)
        A = buf.area
        # clip each candidate to the small halo FIRST, then union the tiny pieces (fast)
        pieces = [_clip(held.geometry.iloc[int(i)], buf) for i in hsi.query(buf, predicate="intersects")]
        if nsi is not None:
            pieces += [_clip(nostake.geometry.iloc[int(i)], buf) for i in nsi.query(buf, predicate="intersects")]
        covered = _union([p for p in pieces if p is not None])
        openpoly = buf.difference(covered) if covered is not None else buf
        if openpoly.is_empty:
            continue
        f = max(0.0, openpoly.area) / A
        if not (OPEN_MIN <= f <= OPEN_MAX):
            continue
        oc = openpoly.centroid
        assay, afri = _assay_from(row.get(cfg["comments"])) if cfg["comments"] else ("", "")
        rows.append({
            "x": pt.x, "y": pt.y, "claim": claim_id,
            "company": _s(row.get(cfg["company"])) or "Unknown operator",
            "prop": _s(row.get(cfg["prop"])) if cfg["prop"] else "",
            "year": int(row["_Y"]), "elems": _s(row.get(cfg["elems"]))[:60] if cfg["elems"] else "",
            "assay": assay, "afri": afri,
            "spend": (lambda v: float(v) if v == v and v else 0.0)(pd.to_numeric(row.get(cfg["spend"]), errors="coerce")) if cfg["spend"] else 0.0,
            "url": _s(row.get(cfg["url"])) if cfg.get("url") else "",
            "open_ha": openpoly.area / 1e4, "open_dir": _bearing(oc.x - pt.x, oc.y - pt.y),
            "openpoly": openpoly,
        })
    if not rows:
        return empty
    edf = pd.DataFrame(rows)

    leads = gpd.read_file(os.path.join(region_dir, "out", "leads.geojson")).to_crs(metric)
    lm = gpd.GeoDataFrame(geometry=leads.geometry, crs=metric)
    lm["rank"] = leads["rank"].values
    lm["name"] = leads["name"].values
    lsi = lm.sindex
    from shapely.geometry import Point

    def nearest_lead(x, y):
        p = Point(x, y)
        try:
            i = list(lsi.nearest(p, return_all=False))[1][0]
            r = lm.iloc[int(i)]
            return int(r["rank"]), str(r["name"]), round(p.distance(r.geometry) / 1000, 1)
        except Exception:
            return None, "", None

    plays, open_gdf_rows = [], []
    edf["grp"] = edf["company"].str.lower() + "||" + edf["prop"].str.lower()
    for _, g in edf.groupby("grp"):
        g = g.sort_values("year", ascending=False)
        cx, cy = g["x"].mean(), g["y"].mean()
        union_open = _union(list(g["openpoly"].values))
        open_ha = round(union_open.area / 1e4, 1) if union_open is not None else round(g["open_ha"].sum(), 1)
        yr_max = int(g["year"].max())
        elems = sorted({e for e in g["elems"] if e})
        assays = [a for a in g["assay"] if a]
        afris = sorted({a for a in g["afri"] if a})
        claims_hit = sorted({c for c in g["claim"] if c and c != "held"})
        rank, lname, lkm = nearest_lead(cx, cy)
        src_url = ""
        for u in g["url"]:
            if u:
                src_url = u
                break
        play = {
            "company": g.iloc[0]["company"],
            "property": g.iloc[0]["prop"] or "(unnamed property)",
            "year": yr_max, "hot": yr_max >= yr - cfg["hot"],
            "n_holes": int(len(g)),
            "commodity": ", ".join(elems)[:70],
            "assay": assays[0] if assays else "",
            "afri": afris[0] if afris else "",
            "spend": round(float(g["spend"].max()), 0),
            "source": cfg["source"], "source_url": src_url or cfg["source_url"],
            "open_ha": open_ha,
            "open_dir": g.sort_values("open_ha", ascending=False).iloc[0]["open_dir"],
            "claims": claims_hit[:6], "n_claims": len(claims_hit),
            "near_rank": rank, "near_lead": lname, "near_km": lkm,
            "lon": None, "lat": None, "_cx": cx, "_cy": cy,
        }
        plays.append(play)
        if union_open is not None and not union_open.is_empty:
            open_gdf_rows.append({"geometry": union_open, "pidx": len(plays) - 1})

    cen = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([p["_cx"] for p in plays], [p["_cy"] for p in plays]),
        crs=metric).to_crs("EPSG:4326")
    point_feats = []
    for p, geom in zip(plays, cen.geometry):
        p["lon"], p["lat"] = round(geom.x, 5), round(geom.y, 5)
        p.pop("_cx", None); p.pop("_cy", None)
        point_feats.append({"type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                            "properties": dict(p)})
    open_feats = []
    if open_gdf_rows:
        og = gpd.GeoDataFrame(open_gdf_rows, geometry="geometry", crs=metric).to_crs("EPSG:4326")
        for _, r in og.iterrows():
            open_feats.append({"type": "Feature", "geometry": r.geometry.__geo_interface__,
                               "properties": {"pidx": int(r["pidx"])}})

    order = sorted(range(len(plays)), key=lambda k: (not plays[k]["hot"], -plays[k]["year"],
                                                     -(plays[k]["assay"] != ""), -plays[k]["open_ha"]))
    remap = {old: new for new, old in enumerate(order)}
    for f in open_feats:
        f["properties"]["pidx"] = remap.get(f["properties"]["pidx"], f["properties"]["pidx"])
    plays = [plays[k] for k in order]
    point_feats = [point_feats[k] for k in order]
    return {"plays": plays, "point_feats": point_feats, "open_feats": open_feats}
