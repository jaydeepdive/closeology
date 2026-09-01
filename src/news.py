"""Fresh drill news -> geolocated edge plays.

Government drill layers (OGS drill DB, BC ARIS) lag by 1-3 years, so the freshest
and richest signal — actual assay intercepts, dated to the week — comes from
company news releases. A fetch step (the daily Action / a trigger) writes a
normalized items file; this module geolocates each item and runs the SAME
open-ground edge test on it, so a press-release assay becomes a top-priority,
dated edge play with the number attached and the release linked.

items file: data/<region>/news_items.json
  {"items":[{"date":"YYYY-MM-DD","company":"...","ticker":"...","project":"...",
             "location":"...","highlight":"<assay/interval>","url":"...",
             "lat":<opt>,"lon":<opt>}]}

Geolocation, best available first:
  1. explicit lat/lon on the item
  2. company -> current claim OWNER (BC claims carry OWNER_NAME) -> that owner's
     claim block, tested for open ground on its boundary
  3. company -> recent operator in the drill/ARIS layer -> their work point(s)
  4. project -> occurrence/MDI name -> that occurrence point
An item that cannot be placed still shows in the plain news list.
"""
import os
import re
import json
import datetime
import pandas as pd
import geopandas as gpd
from shapely.ops import unary_union
import drill_edges as de

FRESH_DAYS = 120          # 'hot' if the release is this recent
BLOCK_HALO_M = de.HALO_M  # open-ground halo around a claim block / point

_SUFFIX = {"corp", "corporation", "ltd", "limited", "inc", "incorporated", "ulc", "lp",
           "llc", "co", "company", "holdings", "the"}
_GENERIC = {"mining", "minerals", "mineral", "metals", "resources", "resource",
            "exploration", "explorations", "ventures", "capital", "energy", "materials",
            "royalty", "royalties", "gold", "copper", "silver"}


def _toks(name):
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", str(name).lower()).split() if t not in _SUFFIX]


def _distinctive(name):
    return set(t for t in _toks(name) if t not in _GENERIC)


def _norm(name):
    return " ".join(_toks(name))


def _company_match(company, target_name):
    """Announcer/operator match: all distinctive company tokens present, and either
    >=2 such tokens or the company name is a prefix of the target (so 'Dryden Gold'
    matches 'Dryden Gold Corp' but never 'Dryden Resources Corp')."""
    dc = _distinctive(company)
    if not dc:
        return False
    tt = set(_toks(target_name))
    if not dc.issubset(tt):
        return False
    if len(dc) >= 2:
        return True
    return _norm(target_name).startswith(_norm(company))


def _project_match(project, target_name):
    """Project/property match — option-proof (a project keeps its name when optioned).
    All distinctive project tokens present, and either >=2 tokens or one specific
    token (>=4 chars) so 'Spyglass'/'Wicheeda' match but a lone generic word won't."""
    pj = _distinctive(project)
    if not pj:
        return False
    tt = set(_toks(target_name))
    if not pj.issubset(tt):
        return False
    return len(pj) >= 2 or any(len(t) >= 4 for t in pj)


def _match_owner(company, owner_tokens):
    dc = _distinctive(company)
    if not dc:
        return None
    best = None
    for owner, ot in owner_tokens.items():
        if dc.issubset(ot) and (len(dc) >= 2 or _norm(owner).startswith(_norm(company))):
            if best is None or len(ot) < best[1]:
                best = (owner, len(ot))
    return best[0] if best else None


def _open_around(geom, held, nostake, hsi, nsi, bound=None):
    """Open, stakeable halo around a geometry (block or point buffer), clipped to
    the province so nothing outside the jurisdiction counts as open."""
    buf = geom.buffer(BLOCK_HALO_M) if geom.geom_type == "Point" else geom.buffer(BLOCK_HALO_M).difference(geom)
    if buf.is_empty:
        return None, 0.0, 0.0
    A = buf.area
    pieces = []
    for i in hsi.query(buf, predicate="intersects"):
        g = held.geometry.iloc[int(i)]
        try:
            inter = g.intersection(buf)
        except Exception:
            gv = de._valid(g); inter = gv.intersection(buf) if gv is not None else None
        if inter is not None and not inter.is_empty:
            pieces.append(inter)
    if nsi is not None:
        for i in nsi.query(buf, predicate="intersects"):
            g = nostake.geometry.iloc[int(i)]
            try:
                inter = g.intersection(buf)
            except Exception:
                gv = de._valid(g); inter = gv.intersection(buf) if gv is not None else None
            if inter is not None and not inter.is_empty:
                pieces.append(inter)
    covered = de._union(pieces)
    openpoly = buf.difference(covered) if covered is not None else buf
    if bound is not None:
        try:
            openpoly = openpoly.intersection(bound)
        except Exception:
            openpoly = openpoly.intersection(de._valid(bound))
    if openpoly.is_empty:
        return None, 0.0, 0.0
    return openpoly, openpoly.area / 1e4, openpoly.area / A


def _fresh(datestr):
    try:
        d = pd.to_datetime(str(datestr)).date()
        return (datetime.date.today() - d).days <= FRESH_DAYS, d.isoformat()
    except Exception:
        return False, str(datestr or "")


def load_items(region_dir):
    p = os.path.join(region_dir, "news_items.json")
    if not os.path.exists(p):
        return []
    try:
        return json.load(open(p)).get("items", [])
    except Exception:
        return []


def find(region_dir, metric):
    """Return dict(plays=[...], point_feats=[...], open_feats=[...], unplaced=[...])."""
    empty = {"plays": [], "point_feats": [], "open_feats": [], "unplaced": []}
    items = load_items(region_dir)
    if not items:
        return empty

    claims, held, nostake = de._layers(region_dir, metric)
    if held is None:
        return empty
    hsi = held.sindex
    nsi = nostake.sindex if nostake is not None else None
    csi = claims.sindex if claims is not None else None
    bound = de.boundary(region_dir, metric)
    cid = None
    if claims is not None:
        for c in ("claim", "TENURE_NUMBER_ID", "CLAIM_NAME"):
            if c in claims.columns:
                cid = c
                break

    # recent-operator/property point layer (drill holes / ARIS) — the driller anchor,
    # which tracks the announcer + the actual ground even when a project is optioned.
    src_df, cfg = de._source_cfg(region_dir)
    op_pts = None
    if src_df is not None:
        s = gpd.GeoDataFrame(src_df.copy(), geometry="geometry", crs="EPSG:4326").to_crs(metric)
        s["_op"] = s.get(cfg["company"]).apply(de._s) if cfg.get("company") else ""
        s["_prop"] = s.get(cfg["prop"]).apply(de._s) if cfg.get("prop") else ""
        op_pts = s[(s["_op"] != "") | (s["_prop"] != "")]

    occ = None
    occp = os.path.join(region_dir, "out", "occurrences_all.geojson")
    if os.path.exists(occp):
        try:
            occ = gpd.read_file(occp).to_crs(metric)
        except Exception:
            occ = None

    plays, unplaced, open_rows, held_rows = [], [], [], []
    from shapely.geometry import Point
    for it in items:
        company = de._s(it.get("company"))
        proj = de._s(it.get("project"))
        highlight = de._s(it.get("highlight"))
        url = de._s(it.get("url"))
        hot, dstr = _fresh(it.get("date"))
        if not dstr:            # undated item from a live newest-first feed -> treat as fresh
            hot = True
        geom = None
        placed_by = ""
        claim_hit = ""
        # 1. explicit coordinates
        try:
            if it.get("lat") is not None and it.get("lon") is not None:
                geom = gpd.GeoSeries([Point(float(it["lon"]), float(it["lat"]))], crs="EPSG:4326").to_crs(metric).iloc[0]
                placed_by = "coordinates"
        except Exception:
            geom = None
        # 2. project -> drill PROPERTY_NAME  (precise + option-proof)
        if geom is None and op_pts is not None and proj is not None and "_prop" in op_pts.columns:
            hit = op_pts[op_pts["_prop"].apply(lambda n: bool(n) and _project_match(proj, n))]
            if len(hit):
                geom = unary_union(list(hit.geometry.values)).centroid if len(hit) > 1 else hit.geometry.iloc[0]
                placed_by = "project → drilled property"
        # 3. project -> occurrence / deposit name  (option-proof)
        if geom is None and occ is not None and proj and "name" in occ.columns:
            m = occ[occ["name"].apply(lambda n: _project_match(proj, n))]
            if len(m):
                geom = m.geometry.iloc[0]
                placed_by = "project → known occurrence"
        # 4. company -> driller/operator  (announcer usually = the driller)
        if geom is None and op_pts is not None and company:
            hit = op_pts[op_pts["_op"].apply(lambda o: bool(o) and _company_match(company, o))]
            if len(hit):
                geom = unary_union(list(hit.geometry.values)).centroid if len(hit) > 1 else hit.geometry.iloc[0]
                placed_by = "company → recent driller"
        # NB: no claim-holder-name placement. A drill result is usually announced by
        # the optionee/operator while the claims still sit in the optionor's name, so
        # holder-name matching would both miss real plays and assert false ones. We
        # only place by where the drilling/project actually is.
        if geom is None:
            unplaced.append({"company": company, "project": proj, "date": dstr,
                             "highlight": highlight, "url": url, "location": de._s(it.get("location")),
                             "note": "could not place (no project/driller match)"})
            continue

        # the located point must actually sit ON a held claim with open, stakeable
        # ground beside it — the same edge test as the government scan.
        rep = geom.centroid if geom.geom_type != "Point" else geom
        on_claim = False
        if csi is not None:
            for i in csi.query(rep, predicate="intersects"):
                cg = claims.geometry.iloc[int(i)]
                if cg is not None and cg.contains(rep):
                    on_claim = True
                    claim_hit = de._s(claims[cid].iloc[int(i)]) if cid else ""
                    break
        openpoly, open_ha, frac = _open_around(rep, held, nostake, hsi, nsi, bound)
        if not on_claim or openpoly is None or not (de.OPEN_MIN <= frac <= de.OPEN_MAX):
            unplaced.append({"company": company, "project": proj, "date": dstr,
                             "highlight": highlight, "url": url, "location": de._s(it.get("location")),
                             "note": ("drilling is mid-block — no open ground on the boundary"
                                      if on_claim else "drilling not on a stakeable claim boundary")})
            continue
        oc = openpoly.centroid
        plays.append({
            "company": company or "Unknown", "property": proj or "(from news)",
            "year": None, "date": dstr, "hot": hot, "n_holes": 1,
            "commodity": "", "assay": highlight, "afri": "",
            "spend": 0.0, "source": "News release", "source_url": url,
            "open_ha": round(open_ha, 1), "open_dir": de._bearing(oc.x - rep.x, oc.y - rep.y),
            "claims": [claim_hit] if claim_hit else [], "n_claims": 1 if claim_hit else 0,
            "placed_by": placed_by, "near_rank": None, "near_lead": "", "near_km": None,
            "lon": None, "lat": None, "_cx": rep.x, "_cy": rep.y,
        })
        pidx = len(plays) - 1
        open_rows.append({"geometry": openpoly, "pidx": pidx})
        # staked ground around the release: actual claim polygons near the point
        halo = rep.buffer(de.HALO_M * 1.4)
        if csi is not None:
            seen = 0
            for i in csi.query(halo, predicate="intersects"):
                cg = claims.geometry.iloc[int(i)]
                if cg is None or not cg.intersects(halo):
                    continue
                cnum = de._s(claims[cid].iloc[int(i)]) if cid else ""
                held_rows.append({"geometry": cg, "pidx": pidx, "claim": cnum,
                                  "drilled": bool(claim_hit) and cnum == claim_hit})
                seen += 1
                if seen >= 60:
                    break

    if plays:
        cen = gpd.GeoDataFrame(
            geometry=gpd.points_from_xy([p["_cx"] for p in plays], [p["_cy"] for p in plays]),
            crs=metric).to_crs("EPSG:4326")
        point_feats = []
        for p, g in zip(plays, cen.geometry):
            p["lon"], p["lat"] = round(g.x, 5), round(g.y, 5)
            p.pop("_cx", None); p.pop("_cy", None)
            point_feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                                "properties": dict(p)})
        open_feats = []
        if open_rows:
            og = gpd.GeoDataFrame(open_rows, geometry="geometry", crs=metric).to_crs("EPSG:4326")
            for _, r in og.iterrows():
                open_feats.append({"type": "Feature", "geometry": r.geometry.__geo_interface__,
                                   "properties": {"pidx": int(r["pidx"])}})
        held_feats = []
        if held_rows:
            hg = gpd.GeoDataFrame(held_rows, geometry="geometry", crs=metric).to_crs("EPSG:4326")
            for _, r in hg.iterrows():
                held_feats.append({"type": "Feature", "geometry": r.geometry.__geo_interface__,
                                   "properties": {"pidx": int(r["pidx"]), "claim": r["claim"], "drilled": bool(r["drilled"])}})
    else:
        point_feats, open_feats, held_feats = [], [], []
    return {"plays": plays, "point_feats": point_feats, "open_feats": open_feats,
            "held_feats": held_feats, "unplaced": unplaced}
