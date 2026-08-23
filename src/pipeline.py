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
        r["status"], False, bool(r["grade_str"]), bool(r.get("tonnes_str")),
        r["n_metals"], r.get("tonnes")), axis=1)
    return occ


def _cellij(x, y):
    return (np.floor(x / GRID_M).astype(int), np.floor(y / GRID_M).astype(int))


def screen(cands_m, nostake_layers):
    """cands_m: metric occurrence points. nostake_layers: list of held/protected
    GeoDataFrames (metric). Returns per-occurrence open-ground summary."""
    G, R = GRID_M, NEIGHBOR_M
    steps = int(R // G)
    offs = [(dx, dy) for dx in range(-steps, steps + 1) for dy in range(-steps, steps + 1)
            if (dx * G) ** 2 + (dy * G) ** 2 <= R * R]
    # collect unique candidate cells
    xs = cands_m.geometry.x.values
    ys = cands_m.geometry.y.values
    ci, cj = _cellij(xs, ys)
    cellset = {}
    per_occ = []
    for k in range(len(cands_m)):
        own = (int(ci[k]), int(cj[k]))
        neigh = [(own[0] + dx, own[1] + dy) for dx, dy in offs]
        per_occ.append((own, neigh))
        for c in neigh:
            cellset[c] = None
    cells = list(cellset.keys())
    polys = [box(i * G, j * G, (i + 1) * G, (j + 1) * G) for i, j in cells]
    cg = gpd.GeoDataFrame({"i": [c[0] for c in cells], "j": [c[1] for c in cells]},
                          geometry=[p.centroid for p in polys], crs=cands_m.crs)
    occupied = np.zeros(len(cg), dtype=bool)
    for layer in nostake_layers:
        if layer is None or not len(layer):
            continue
        hit = gpd.sjoin(cg[["geometry"]], layer[["geometry"]], predicate="within", how="left")
        idx = hit[hit.index_right.notna()].index.unique()
        occupied[cg.index.isin(idx)] = True
    open_map = {(cells[n]): (not occupied[n]) for n in range(len(cells))}
    out = []
    for own, neigh in per_occ:
        opens = [f"{i}_{j}" for (i, j) in neigh if open_map.get((i, j), False)]
        out.append({"deposit_open": open_map.get(own, False), "core_cell": f"{own[0]}_{own[1]}",
                    "open_cells": opens, "n_cells": len(opens),
                    "cells_area_ha": round(len(opens) * (G * G) / 1e4, 1)})
    return out


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
    cand_m = cand.to_crs(metric)
    sc = screen(cand_m, [cm, noreg, pk, npk, lz])
    scdf = pd.DataFrame(sc)
    for c in scdf.columns:
        cand[c] = scdf[c].values
    leads = cand[cand["n_cells"] > 0].copy().reset_index(drop=True)
    leads["deposit_open"] = leads["deposit_open"].astype(bool)
    leads["score"] = leads.apply(lambda r: score_lead(
        r["status"], r["deposit_open"], bool(r["grade_str"]), bool(r.get("tonnes_str")),
        r["n_metals"], r.get("tonnes")), axis=1)
    leads = leads.sort_values(["deposit_open", "score"], ascending=False).reset_index(drop=True)
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
            "drill_highlights", "basis", "encumbrances", "core_cell", "n_cells",
            "cells_area_ha", "score", "lat", "lon", "cell_ids", "minfile_url", "capsule"]
    cols = [c for c in cols if c in leads.columns]
    leads[cols].to_csv(os.path.join(out_dir, "leads.csv"), index=False)
    leads[cols + ["metal_buckets", "geometry"]].to_file(os.path.join(out_dir, "leads.geojson"), driver="GeoJSON")

    # open stakeable cells as polygons (the actual ground to stake)
    G = GRID_M
    cellfeats = []
    for _, r in leads.iterrows():
        for cid in (r["open_cells"] or []):
            i, j = map(int, cid.split("_"))
            cellfeats.append({"lead_id": r["lead_id"], "rank": int(r["rank"]),
                              "name": r["name"], "geometry": box(i * G, j * G, (i + 1) * G, (j + 1) * G)})
    if cellfeats:
        gpd.GeoDataFrame(cellfeats, geometry="geometry", crs=metric).to_crs("EPSG:4326").to_file(
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

    stats = {
        "region": region["name"], "generated": region.get("today", ""),
        "n_leads": len(leads), "n_deposit_open": int(leads["deposit_open"].sum()),
        "n_with_drill_highlights": int((leads["drill_highlights"].str.len() > 0).sum()),
        "n_hard_to_stake": int(leads["hard_to_stake"].sum()),
        "n_producers": int(occ["status"].str.lower().str.contains("produc").sum()),
        "n_with_resource": int(leads["has_resource"].sum()) if "has_resource" in leads else 0,
        "n_candidate_leads": int(len(cand)), "top_n_examined": TOP_N,
        "n_occurrences": len(occ), "n_claims_active": len(claims),
        "attribution": region["attribution"],
    }
    json.dump(stats, open(os.path.join(out_dir, "stats.json"), "w"), indent=2)
    print(f"[{region['name']}] leads={len(leads)} open={stats['n_deposit_open']} "
          f"drill={stats['n_with_drill_highlights']} hard={stats['n_hard_to_stake']}")
    return stats
