"""Region-agnostic Closeology core: screen a synthetic staking grid against
claims / reserves / parks, find occurrences beside open ground, enrich, output."""
import os
import json
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box
from config import (metal_bucket, score_lead, METAL_ABBR, METAL_ORDER,
                    GRID_M, NEIGHBOR_M, TOP_N)

STATUS_CAND = ("produc", "prospect", "deposit", "discovery", "developed")


def _rd(p):
    return gpd.read_parquet(p) if os.path.exists(p) else None


def prep(occ, facts):
    occ = occ.copy()
    occ["status"] = occ["status"].fillna("")
    # metals
    def buckets(cs):
        bs = []
        for c in (cs if isinstance(cs, (list, np.ndarray)) else []):
            b = metal_bucket(c)
            if b not in bs:
                bs.append(b)
        return bs or ["Other metallic"]
    occ["metal_buckets"] = occ["commodities"].map(buckets)

    def _primary(cs):
        for c in (cs if isinstance(cs, (list, np.ndarray)) else []):
            return metal_bucket(c)     # first listed commodity = the primary
        return "Other metallic"
    occ["primary_metal"] = occ["commodities"].map(_primary)
    occ["metals_abbr"] = occ["metal_buckets"].map(lambda b: "-".join(METAL_ABBR.get(m, m[:2]) for m in b[:4]))
    if facts is not None:
        occ["key"] = occ["minfile"].astype(str).str.replace(" ", "", regex=False).str.upper()
        occ = occ.merge(facts, on="key", how="left")
    for c in ["grade_str", "tonnes_str", "resource_cat", "drill_highlights", "capsule"]:
        if c not in occ:
            occ[c] = ""
        occ[c] = occ[c].fillna("")
    if "tonnes" not in occ:
        occ["tonnes"] = np.nan
    if "has_resource" not in occ:
        occ["has_resource"] = False
    occ["has_resource"] = occ["has_resource"].fillna(False)
    occ["n_metals"] = occ["metal_buckets"].map(len)
    occ["base_score"] = occ.apply(lambda r: score_lead(
        r["status"], False, r.get("grade_str", ""), r.get("tonnes_str", ""),
        bool(r.get("drill_highlights"))), axis=1)
    return occ


def _cellij(x, y):
    return (np.floor(x / GRID_M).astype(int), np.floor(y / GRID_M).astype(int))


def screen(cands_ll, nostake_layers, metric):
    """cands_ll: occurrence points in EPSG:4326. Grid cells are aligned to a
    lat/long lattice (so they run parallel to the real MTO / MLAS claim cells,
    not skewed to a projection's axes). nostake_layers: held/protected layers in
    `metric`. Returns (per-occurrence summary, {dlon, dlat}) in degrees."""
    import math
    G, R = GRID_M, NEIGHBOR_M
    xs = cands_ll.geometry.x.values      # lon
    ys = cands_ll.geometry.y.values      # lat
    ref_lat = float(np.nanmean(ys))
    dlat = G / 111320.0
    dlon = G / (111320.0 * max(0.2, math.cos(math.radians(ref_lat))))
    steps = int(R // G)
    offs = [(dx, dy) for dx in range(-steps, steps + 1) for dy in range(-steps, steps + 1)
            if (dx * G) ** 2 + (dy * G) ** 2 <= R * R]
    ci = np.floor(xs / dlon).astype(int)
    cj = np.floor(ys / dlat).astype(int)
    cellset, per_occ = {}, []
    for k in range(len(cands_ll)):
        own = (int(ci[k]), int(cj[k]))
        neigh = [(own[0] + dx, own[1] + dy) for dx, dy in offs]
        per_occ.append((own, neigh))
        for c in neigh:
            cellset[c] = None
    cells = list(cellset.keys())
    # test the whole cell polygon: a cell is open only if it does NOT overlap any
    # claimed / leased / patented / park ground (not merely miss its centre).
    polys = [box(i * dlon, j * dlat, (i + 1) * dlon, (j + 1) * dlat) for i, j in cells]
    cg = gpd.GeoDataFrame(geometry=polys, crs="EPSG:4326").to_crs(metric)
    occupied = np.zeros(len(cg), dtype=bool)
    for layer in nostake_layers:
        if layer is None or not len(layer):
            continue
        hit = gpd.sjoin(cg[["geometry"]], layer[["geometry"]], predicate="intersects", how="left")
        idx = hit[hit.index_right.notna()].index.unique()
        occupied[cg.index.isin(idx)] = True
    open_map = {cells[n]: (not occupied[n]) for n in range(len(cells))}
    out = []
    for own, neigh in per_occ:
        opens = [f"{i}_{j}" for (i, j) in neigh if open_map.get((i, j), False)]
        out.append({"deposit_open": open_map.get(own, False), "core_cell": f"{own[0]}_{own[1]}",
                    "open_cells": opens, "n_cells": len(opens),
                    "cells_area_ha": round(len(opens) * (G * G) / 1e4, 1)})
    return out, {"dlon": dlon, "dlat": dlat}


COND = ("conditional", "release required", "designated placer")


def enrich(leads, communities, reserves, parks, metric):
    lm = leads.to_crs(metric)
    pts = gpd.GeoDataFrame(geometry=lm.geometry, crs=metric)
    # nearest community
    if communities is not None and len(communities):
        cm = communities.to_crs(metric)[["name", "type", "geometry"]]
        j = gpd.sjoin_nearest(pts, cm, distance_col="_d", how="left")
        j = j[~j.index.duplicated(keep="first")].reindex(pts.index)
        leads["nearest_community"] = j["name"].values
        leads["community_type"] = j["type"].values
        leads["community_km"] = (j["_d"].values / 1000).round(1)
    else:
        leads["nearest_community"] = ""; leads["community_km"] = None; leads["community_type"] = ""
    # encumbrance flags
    notes = [[] for _ in range(len(lm))]
    if reserves is not None and len(reserves):
        r = reserves.copy()
        r["restr"] = r.get("MTA_SITE_ORDER_RESTR_DESC", "").fillna("").astype(str)
        r["reason"] = r.get("MTA_SITE_REASON_DESCRIPTION", "").fillna("").astype(str)
        rc = r[r["restr"].str.lower().str.contains("|".join(COND))].to_crs(metric)
        if len(rc):
            si = rc.sindex
            for i, geom in enumerate(lm.geometry.values):
                for ri in si.query(geom, predicate="intersects"):
                    rr = rc.iloc[int(ri)]
                    if geom.intersects(rr.geometry):
                        notes[i].append(f"{rr['restr'].strip()} ({rr['reason'].strip()}) — special process")
                        break
    if parks is not None and len(parks):
        pk = parks[parks.geometry.notna()].to_crs(metric)
        si = pk.sindex
        namecol = "PROTECTED_LANDS_NAME" if "PROTECTED_LANDS_NAME" in pk.columns else pk.columns[0]
        for i, geom in enumerate(lm.geometry.values):
            halo = geom.buffer(250)
            for pi in si.query(halo, predicate="intersects"):
                pg = pk.iloc[int(pi)]
                if halo.intersects(pg.geometry):
                    notes[i].append(f"abuts {str(pg[namecol]).strip().title()} boundary")
                    break
    leads["encumbrances"] = [" · ".join(dict.fromkeys(n)) for n in notes]
    leads["hard_to_stake"] = [bool(n) for n in notes]

    def size_note(r):
        bits = []
        if r.get("tonnes_str"):
            cat = (r.get("resource_cat") or "").strip()
            bits.append(f"{r['tonnes_str']}" + (f" ({cat.lower()})" if cat else ""))
        elif r.get("has_resource"):
            bits.append((r.get("resource_cat") or "resource").strip() + " resource")
        return "; ".join(bits) if bits else "no tonnage on record"

    def basis(r):
        who = "Deposit itself unclaimed" if r["deposit_open"] else "Open ground adjacent to a staked deposit"
        return f"{r['status']} ({r.get('metals_abbr') or r['primary_metal']}). {who}."
    leads["deposit_size"] = leads.apply(size_note, axis=1)
    leads["basis"] = leads.apply(basis, axis=1)
    return leads


def attach_spend(leads, reports, metric, R=1500):
    """Per-lead exploration spend: sum $ of assessment reports/work areas within R
    (and, for BC, reports whose MINFILE list names this occurrence)."""
    if reports is None or not len(reports):
        leads["exploration_spend"] = 0.0
        leads["n_reports"] = 0
        leads["last_work_year"] = None
        leads["operators"] = ""
        return leads
    lm = leads.to_crs(metric)
    rm = reports.to_crs(metric).reset_index(drop=True)
    rm["spend"] = pd.to_numeric(rm["spend"], errors="coerce").fillna(0.0)
    rep_by_mf = {}
    if "minfile" in rm.columns:
        for idx, mf in zip(rm.index, rm["minfile"].astype(str)):
            for tok in mf.split(";"):
                k = tok.replace(" ", "").upper().strip()
                if k:
                    rep_by_mf.setdefault(k, set()).add(idx)
    key = leads["minfile"].astype(str).str.replace(" ", "", regex=False).str.upper()
    si = rm.sindex
    spends, ncnt, yrs, ops = [], [], [], []
    geoms = lm.geometry.values
    for i in range(len(lm)):
        halo = geoms[i].buffer(R)
        idxs = set()
        for j in si.query(halo, predicate="intersects"):
            if halo.intersects(rm.geometry.iloc[int(j)]):
                idxs.add(int(j))
        idxs |= rep_by_mf.get(key.iloc[i], set())
        sub = rm.loc[list(idxs)] if idxs else rm.iloc[0:0]
        spends.append(float(sub["spend"].sum()))
        ncnt.append(int(len(sub)))
        y = pd.to_numeric(sub["year"], errors="coerce").dropna() if len(sub) else pd.Series([], dtype=float)
        yrs.append(int(y.max()) if len(y) else None)
        seen = []
        for o in (list(sub.sort_values("spend", ascending=False)["operator"]) if len(sub) else []):
            for part in str(o).split(","):
                p = part.strip()
                if p and p not in seen and p.lower() != "nan":
                    seen.append(p)
        ops.append(", ".join(seen[:3]))
    leads["exploration_spend"] = spends
    leads["n_reports"] = ncnt
    leads["last_work_year"] = yrs
    leads["operators"] = ops
    return leads


def _spend_str(v):
    if not v or v != v:
        return ""
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}k"
    return f"${v:.0f}"


def track_drops(d, claims, leads, metric):
    """Snapshot active claims; diff vs the committed prior snapshot to find claims
    that DROPPED since the last run (prior owner + expiry + nearest lead)."""
    slug = os.path.basename(d.rstrip("/"))
    snap = os.path.join("data", "keep", f"{slug}_claims_snapshot.parquet")
    c = claims.copy()
    if "TENURE_NUMBER_ID" in c.columns:      # BC
        idf, own, dte, area = "TENURE_NUMBER_ID", "OWNER_NAME", "GOOD_TO_DATE", "AREA_IN_HECTARES"
    elif "claim" in c.columns:               # Ontario cells
        idf, own, dte, area = "claim", None, None, None
    else:
        return 0
    cen = c.to_crs(metric).geometry.representative_point().to_crs("EPSG:4326")
    cur = pd.DataFrame({"id": c[idf].astype(str),
                        "owner": c[own].astype(str) if own else "",
                        "good_to": c[dte].astype(str) if dte else "",
                        "area_ha": pd.to_numeric(c[area], errors="coerce") if area else 0.0,
                        "lon": cen.x.round(5), "lat": cen.y.round(5)}).drop_duplicates("id")
    dropped = []
    if os.path.exists(snap):
        prev = pd.read_parquet(snap)
        pg = prev[prev["id"].isin(set(prev["id"]) - set(cur["id"]))].copy()
        if len(pg) and len(leads):
            pgm = gpd.GeoDataFrame(pg, geometry=gpd.points_from_xy(pg.lon, pg.lat), crs="EPSG:4326").to_crs(metric)
            lg = gpd.GeoDataFrame(leads[["name", "rank"]].reset_index(drop=True),
                                  geometry=leads.to_crs(metric).geometry.values, crs=metric)
            j = gpd.sjoin_nearest(pgm, lg, distance_col="_d")
            j = j[~j.index.duplicated(keep="first")]
            for _, r in j.iterrows():
                dropped.append({"id": r["id"], "owner": r.get("owner", ""), "good_to": str(r.get("good_to", ""))[:10],
                                "area_ha": round(float(r.get("area_ha") or 0), 1), "lon": r["lon"], "lat": r["lat"],
                                "near_lead": r.get("name", ""), "near_rank": int(r.get("rank", 0)), "near_km": round(r["_d"] / 1000, 1)})
            dropped.sort(key=lambda x: x["near_km"])
    json.dump({"n": len(dropped), "dropped": dropped[:200]},
              open(os.path.join(d, "out", "dropped.json"), "w"))
    os.makedirs(os.path.join("data", "keep"), exist_ok=True)
    cur.to_parquet(snap)
    return len(dropped)


def run_region(region):
    d = region["dir"]
    metric = region["metric_crs"]
    out_dir = os.path.join(d, "out")
    os.makedirs(out_dir, exist_ok=True)
    occ = _rd(os.path.join(d, "occurrences.parquet"))
    claims = _rd(os.path.join(d, "claims.parquet"))
    reserves = _rd(os.path.join(d, "reserves.parquet"))
    parks = _rd(os.path.join(d, "parks.parquet"))
    natparks = _rd(os.path.join(d, "national_parks.parquet"))
    communities = _rd(os.path.join(d, "communities.parquet"))
    facts = None
    fp = os.path.join(d, "minfile_facts.parquet")
    if os.path.exists(fp):
        facts = pd.read_parquet(fp)

    occ = prep(occ, facts)
    st = occ["status"].str.lower()
    # a currently-operating mine/quarry can't be staked — exclude (keep PAST producers)
    active = (st.str.contains("producing") & ~st.str.contains("past")) | (st.str.strip() == "producer")
    cand = occ[(st.str.contains("|".join(STATUS_CAND)) | occ["has_resource"])
               & (occ["primary_metal"] != "Industrial") & ~active].copy()
    cand = cand.sort_values("base_score", ascending=False).head(TOP_N).reset_index(drop=True)

    cm = claims.to_crs(metric)
    rz = reserves.copy() if reserves is not None else None
    noreg = None
    if rz is not None:
        rz["restr"] = rz.get("MTA_SITE_ORDER_RESTR_DESC", "").fillna("").astype(str)
        noreg = rz[rz["restr"].str.lower().str.contains("no registration")].to_crs(metric)
    pk = parks.to_crs(metric) if parks is not None else None
    npk = natparks.to_crs(metric) if natparks is not None else None
    leases = _rd(os.path.join(d, "leases.parquet"))     # held mining leases / patents (Ontario)
    lz = leases.to_crs(metric) if leases is not None else None
    sc, gridmeta = screen(cand.to_crs("EPSG:4326"), [cm, noreg, pk, npk, lz], metric)
    scdf = pd.DataFrame(sc)
    for c in scdf.columns:
        cand[c] = scdf[c].values
    leads = cand[cand["n_cells"] > 0].copy().reset_index(drop=True)
    leads["deposit_open"] = leads["deposit_open"].astype(bool)
    leads = attach_spend(leads, _rd(os.path.join(d, "spend_reports.parquet")), metric)
    leads["exploration_spend_str"] = leads["exploration_spend"].map(_spend_str)
    leads["score"] = leads.apply(lambda r: score_lead(
        r["status"], r["deposit_open"], r.get("grade_str", ""), r.get("tonnes_str", ""),
        bool(r.get("drill_highlights")), r.get("exploration_spend", 0)), axis=1)
    leads = leads.sort_values(["score", "deposit_open", "exploration_spend"], ascending=False).reset_index(drop=True)
    leads.insert(0, "rank", range(1, len(leads) + 1))
    leads["lead_id"] = ["L%04d" % i for i in leads["rank"]]
    leads["cell_ids"] = leads["open_cells"].map(lambda x: ";".join(x))

    leads = enrich(leads, communities, reserves, parks, metric)
    leads = leads.to_crs("EPSG:4326")
    cp = leads.geometry
    leads["lat"] = cp.y.round(5); leads["lon"] = cp.x.round(5)

    cols = ["rank", "lead_id", "name", "minfile", "primary_metal", "metals_abbr", "commodity",
            "status", "deposit_open", "hard_to_stake", "nearest_community", "community_type",
            "community_km", "deposit_size", "grade_str", "tonnes_str", "resource_cat",
            "drill_highlights", "exploration_spend", "exploration_spend_str", "n_reports",
            "last_work_year", "operators", "basis", "encumbrances", "core_cell", "n_cells",
            "cells_area_ha", "score", "lat", "lon", "cell_ids", "minfile_url", "capsule"]
    cols = [c for c in cols if c in leads.columns]
    leads[cols].to_csv(os.path.join(out_dir, "leads.csv"), index=False)
    leads[cols + ["metal_buckets", "geometry"]].to_file(os.path.join(out_dir, "leads.geojson"), driver="GeoJSON")

    # open stakeable cells as polygons (lat/long lattice, parallel to real claim cells)
    dlon, dlat = gridmeta["dlon"], gridmeta["dlat"]
    cellfeats = []
    for _, r in leads.iterrows():
        for cid in (r["open_cells"] or []):
            i, j = map(int, cid.split("_"))
            cellfeats.append({"lead_id": r["lead_id"], "rank": int(r["rank"]), "name": r["name"],
                              "geometry": box(i * dlon, j * dlat, (i + 1) * dlon, (j + 1) * dlat)})
    if cellfeats:
        gpd.GeoDataFrame(cellfeats, geometry="geometry", crs="EPSG:4326").to_file(
            os.path.join(out_dir, "opencells.geojson"), driver="GeoJSON")

    # claim-cell context near leads (regions without a live claims WMS)
    if region.get("inline_claims") and claims is not None:
        lm = leads.to_crs(metric)
        halo = gpd.GeoDataFrame(geometry=[lm.geometry.buffer(2500).union_all()], crs=metric)
        cnear = gpd.sjoin(claims.to_crs(metric), halo, predicate="intersects", how="inner")
        cnear = claims.loc[cnear.index.unique()].to_crs("EPSG:4326")
        idcol = "claim" if "claim" in cnear.columns else cnear.columns[0]
        cnear[[idcol, "geometry"]].rename(columns={idcol: "claim"}).to_file(
            os.path.join(out_dir, "claims_near.geojson"), driver="GeoJSON")

    # light occurrences layer for the map
    o = occ.copy()
    feats = []
    og = o.to_crs("EPSG:4326")
    for _, r in og.iterrows():
        g = r.geometry
        if g is None:
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(g.x, 5), round(g.y, 5)]},
                      "properties": {"n": r["name"], "mf": r["minfile"], "c": r["commodity"],
                                     "st": r["status"], "p": 1 if str(r.get("prod_ind")).strip() in ("Y", "1", "True") else 0,
                                     "u": r.get("minfile_url", "")}})
    json.dump({"type": "FeatureCollection", "features": feats},
              open(os.path.join(out_dir, "occurrences_all.geojson"), "w"))

    n_dropped = track_drops(d, claims, leads, metric)

    stats = {
        "region": region["name"], "generated": region.get("today", ""),
        "n_dropped": n_dropped,
        "n_leads": len(leads), "n_deposit_open": int(leads["deposit_open"].sum()),
        "n_with_drill_highlights": int((leads["drill_highlights"].str.len() > 0).sum()),
        "n_hard_to_stake": int(leads["hard_to_stake"].sum()),
        "n_producers": int(occ["status"].str.lower().str.contains("produc").sum()),
        "n_with_resource": int(leads["has_resource"].sum()) if "has_resource" in leads else 0,
        "n_candidate_leads": int(len(cand)), "top_n_examined": TOP_N,
        "n_occurrences": len(occ), "n_claims_active": len(claims),
        "grid_dlon": gridmeta["dlon"], "grid_dlat": gridmeta["dlat"],
        "n_with_spend": int((leads["exploration_spend"] > 0).sum()),
        "attribution": region["attribution"],
    }
    json.dump(stats, open(os.path.join(out_dir, "stats.json"), "w"), indent=2)
    print(f"[{region['name']}] leads={len(leads)} open={stats['n_deposit_open']} "
          f"drill={stats['n_with_drill_highlights']} hard={stats['n_hard_to_stake']}")
    return stats
