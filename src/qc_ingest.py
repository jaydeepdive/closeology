"""Quebec ingest. SIGEOM geoscience + GESTIM titles are served differently from the
other provinces (no ArcGIS REST), so Quebec has its own module:
  • occurrences  -> from the SIGEOM GPKG (F4E02 mineralized bodies + F4R21 elements);
                    grade/assay text is carried in `capsule` so the pipeline's text
                    grade-extractor gives Quebec real value-based scores.
  • claims       -> GESTIM 'Titres miniers actifs' via the public WFS (paged).
  • reserves     -> 'Contraintes majeures' (withdrawn/restricted) via the same WFS.
"""
import os
import io
import time
import zipfile
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape

UA = {"User-Agent": "closeology-qc/1.0"}
GPKG_ZIP = ("https://gq.mines.gouv.qc.ca/documents/SIGEOM/TOUTQC/FRA/GPKG/"
            "SIGEOM_QC_Indices_gites_mines_et_carrieres_GPKG.zip")
WFS = "https://servicesvectoriels.atlas.gouv.qc.ca/IDS_SGM_WFS/service.svc/get"

# SIGEOM 'état du corps minéralisé' code -> pipeline status
ETAT = {"I": "Showing", "P": "Prospect", "G": "Deposit",
        "MF": "Past Producer", "MA": "Producer"}     # MA (active mine) is excluded by the pipeline
# chemical symbol -> commodity name (so metal_bucket / value scoring resolve them)
SYM = {
    "Au": "Gold", "Ag": "Silver", "Cu": "Copper", "Pb": "Lead", "Zn": "Zinc",
    "Ni": "Nickel", "Co": "Cobalt", "Mo": "Molybdenum", "W": "Tungsten", "Sn": "Tin",
    "Fe": "Iron", "Mn": "Manganese", "Cr": "Chromium", "Ti": "Titanium", "V": "Vanadium",
    "U": "Uranium", "Th": "Thorium", "Li": "Lithium", "Be": "Beryllium", "Ta": "Tantalum",
    "Nb": "Niobium", "REE": "Rare earths", "Pt": "Platinum", "Pd": "Palladium",
    "Sb": "Antimony", "Bi": "Bismuth", "As": "Arsenic", "Ga": "Gallium", "Ge": "Germanium",
    "In": "Indium", "Te": "Tellurium", "Se": "Selenium", "Cd": "Cadmium", "Hg": "Mercury",
    "P": "Phosphate", "S": "Sulphur", "Ba": "Barium", "F": "Fluorite", "Graphite": "Graphite",
}


def _gpkg_path():
    p = "data/qc/_gpkg/sigeom.gpkg"
    if os.path.exists(p):
        return p
    os.makedirs("data/qc/_gpkg", exist_ok=True)
    r = requests.get(GPKG_ZIP, headers=UA, timeout=300)
    r.raise_for_status()
    zipfile.ZipFile(io.BytesIO(r.content)).extractall("data/qc/_gpkg")
    return p


# precious metals reported as g/t; everything else as %
_PRECIOUS_SYM = {"Au", "Ag", "Pt", "Pd"}
# sample-type -> grade confidence (drill > channel/other > grab)
_TYPE_CONF = {"D": 0.8, "R": 0.7, "C": 0.7, "T": 0.6, "V": 0.6, "G": 0.5}


def _grade_token(sym, tenr, unit):
    """(symbol, value, unit) -> ('Au 5.20 g/t' | 'Cu 1.08%', is_gpt)."""
    u = str(unit or "").lower()
    try:
        v = float(tenr)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if sym in _PRECIOUS_SYM:
        gpt = v / 1000.0 if "ppb" in u else (v * 10000.0 if "%" in u else v)   # ppm≈g/t
        return f"{sym} {gpt:.2f} g/t"
    pct = v if "%" in u else (v / 1e4 if "ppm" in u else v / 1e7)
    return f"{sym} {pct:.3g}%"


def _qc_grades(gpkg):
    """Best grade per (corps, element) from F4E14, preferring drill over grab; returns
    {corps: (grade_str, grade_conf, has_drill)}."""
    t = pd.DataFrame(gpd.read_file(gpkg, layer="F4E14_SUBSTANCE_TENEUR").drop(
        columns="geometry", errors="ignore"))
    t = t.dropna(subset=["NUMR_CORPS_MINR", "CODE_ELMN_CHIM", "TENR"]).copy()
    t["TENR"] = pd.to_numeric(t["TENR"], errors="coerce")
    t = t[t["TENR"] > 0]
    t["conf"] = t["CODE_TYPE_ECHN_MINR"].map(_TYPE_CONF).fillna(0.5)
    out = {}
    from config import PRICE_KG
    import enrich_facts as EF
    for cid, grp in t.groupby("NUMR_CORPS_MINR"):
        has_drill = bool((grp["CODE_TYPE_ECHN_MINR"] == "D").any())
        toks = []
        for sym, g2 in grp.groupby("CODE_ELMN_CHIM"):
            g2 = g2.sort_values("TENR", ascending=False)
            best = g2.iloc[0]
            tok = _grade_token(str(sym), best["TENR"], best["CODE_UNITE_TENR"])
            if not tok:
                continue
            # value for ranking (0 for unpriced elements, still displayed)
            val, _ = EF.value_parts(tok)
            toks.append((val, float(best["conf"]), tok))
        if not toks:
            continue
        toks.sort(key=lambda x: -x[0])
        grade_str = ", ".join(t3 for _, _, t3 in toks[:5])
        # confidence of the value-dominant element (fall back to best available)
        conf = toks[0][1] if toks[0][0] > 0 else max(c for _, c, _ in toks)
        out[cid] = (grade_str, conf, has_drill)
    return out


def _qc_production(gpkg):
    """{corps: (y0, y1, tonnes)} from F4E06 production periods (YYYYMMDD dates)."""
    p = pd.DataFrame(gpd.read_file(gpkg, layer="F4E06_PERIODE_PRODUCTION").drop(
        columns="geometry", errors="ignore"))
    p = p.dropna(subset=["NUMR_CORPS_MINR"]).copy()

    def _yr(v):
        s = "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()
        if s.endswith(".0"):
            s = s[:-2]
        return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() else None
    out = {}
    for cid, grp in p.groupby("NUMR_CORPS_MINR"):
        ys = [y for v in list(grp["DATE_DEBUT_PROD"]) + list(grp["DATE_FIN_PROD"])
              for y in [_yr(v)] if y]
        ton = pd.to_numeric(grp["F4E06_TONNA"], errors="coerce").sum()
        out[cid] = (min(ys) if ys else None, max(ys) if ys else None,
                    float(ton) if ton == ton else 0.0)
    return out


def _fmt_t(t):
    if not t:
        return ""
    if t >= 1e6:
        return f"{t/1e6:.1f} Mt"
    if t >= 1e3:
        return f"{t/1e3:.0f} kt"
    return f"{t:.0f} t"


def fetch_occurrences():
    g = _gpkg_path()
    occ = gpd.read_file(g, layer="F4E02_CORPS_MINERALISE").to_crs("EPSG:4326")
    el = pd.DataFrame(gpd.read_file(g, layer="F4R21_CORPS_MINR_ELMN_CHIMIQUE").drop(
        columns="geometry", errors="ignore"))
    el = el.dropna(subset=["NUMR_CORPS_MINR"]).copy()
    el["ord"] = (el["CODE_INDC_PRIN_SECN"].astype(str) != "P").astype(int)
    comm = {}
    for cid, grp in el.sort_values("ord").groupby("NUMR_CORPS_MINR"):
        names = []
        for sym in grp["CODE_ELMN_CHIM_PERD"].astype(str):
            nm = SYM.get(sym, sym.title() if len(sym) > 2 else sym)
            if nm not in names:
                names.append(nm)
        comm[cid] = names
    grades = _qc_grades(g)          # real assays w/ confidence + drill flag
    prod = _qc_production(g)        # production years + tonnage

    rows, geoms = [], []
    for _, r in occ.iterrows():
        gm = r.geometry
        if gm is None or gm.is_empty:
            continue
        pt = gm if gm.geom_type == "Point" else gm.representative_point()
        cid = r.get("NUMR_CORPS_MINR")
        cl = comm.get(cid, [])
        status = ETAT.get(str(r.get("CODE_ETAT_CORPS_MINR") or "").strip(), "Occurrence")
        text = " ".join(str(r.get(c) or "") for c in
                        ("COMN_SUBS", "COMN_MINR", "COMN_PROD", "COMN_DECV"))
        nid = r.get("NUMR_INTER")
        url = f"https://sigeom.mines.gouv.qc.ca/signet/classes/I1102_afchDetlLien?l=f&entt=I0001_corpsMineralise&noSeqnEntt={int(nid)}" if pd.notna(nid) else ""
        gs, gconf, hdrill = grades.get(cid, ("", 1.0, False))
        y0, y1, ton = prod.get(cid, (None, None, 0.0))
        production = ""
        if y1:
            production = f"Produced {y0}–{y1}" + (f": {_fmt_t(ton)}" if ton else "")
        rows.append({"minfile": str(r.get("NUMR_IDNT_CORPS_MINR") or cid or "").strip(),
                     "name": str(r.get("NOM_CORPS_MINR") or "").strip() or "(sans nom)",
                     "status": status, "commodities": cl, "commodity": ", ".join(cl),
                     "deposit_type": "", "minfile_url": url,
                     "prod_ind": "Y" if status in ("Past Producer", "Producer") else "N",
                     "township": "", "capsule": text[:1500],
                     "grade_str": gs, "grade_conf": gconf,
                     "drill_highlights": ("drill-tested" if hdrill else ""),
                     "tonnes": ton or None, "tonnes_str": _fmt_t(ton) if ton else "",
                     "production": production, "last_prod_year": y1})
        geoms.append(pt)
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    gdf.to_parquet("data/qc/occurrences.parquet")
    ng = int((gdf.grade_str.str.len() > 0).sum())
    print(f"[qc] occurrences {len(gdf)} (producers {int((gdf.prod_ind=='Y').sum())}, graded {ng})")


def _wfs_all(typename, colmap, out, count=40000):
    """Single-shot WFS fetch (this server rejects startIndex paging). Used for the
    smaller layers (reserves) that fit one response."""
    params = {"service": "wfs", "version": "2.0.0", "request": "GetFeature",
              "typeNames": typename, "count": count,
              "outputFormat": "application/json", "srsName": "EPSG:4326"}
    for a in range(4):
        try:
            r = requests.get(WFS, params=params, headers=UA, timeout=300)
            r.raise_for_status()
            d = r.json()
            break
        except Exception:
            if a == 3:
                raise
            time.sleep(3 * (a + 1))
    feats = d.get("features", [])
    rows, geoms = [], []
    for f in feats:
        gj = f.get("geometry")
        if not gj:
            continue
        try:
            gm = shape(gj)
        except Exception:
            continue
        if gm.is_empty:
            continue
        p = f.get("properties", {})
        rows.append({k: p.get(v) for k, v in colmap.items()})
        geoms.append(gm)
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    gdf.to_parquet(out)
    print(f"[qc] {os.path.basename(out)} {len(gdf)}")
    return gdf


def _wfs_bbox(typename, bbox, count=8000):
    """One WFS GetFeature over a bbox (lon,lat order + EPSG:4326). The server rejects
    startIndex paging, so we tile instead — each tile is small enough to fit one page."""
    p = {"service": "wfs", "version": "2.0.0", "request": "GetFeature",
         "typeNames": typename, "count": count, "outputFormat": "application/json",
         "srsName": "EPSG:4326", "bbox": "%f,%f,%f,%f,EPSG:4326" % bbox}
    for a in range(4):
        try:
            r = requests.get(WFS, params=p, headers=UA, timeout=180)
            r.raise_for_status()
            return r.json().get("features", [])
        except Exception:
            if a == 3:
                raise
            time.sleep(3 * (a + 1))


def _candidate_points():
    """Reproduce the pipeline's candidate selection so we only fetch the 237k-claim
    layer around ground we'll actually screen."""
    import pipeline as P
    occ = gpd.read_parquet("data/qc/occurrences.parquet")
    occ = P.prep(occ, None)
    st = occ["status"].str.lower()
    active = (st.str.contains("producing") & ~st.str.contains("past")) | (st.str.strip() == "producer")
    hit = st.str.contains("|".join(P.STATUS_CAND)) | occ["has_resource"]
    if not hit.any():
        hit = pd.Series(True, index=occ.index)
    cand = occ[hit & (occ["primary_metal"] != "Industrial") & ~active]
    cand = cand.sort_values("base_score", ascending=False).head(P.TOP_N)
    return cand


def fetch_claims():
    """Tile the WFS 'Actifs' claim layer only around candidate occurrences."""
    cand = _candidate_points()
    T, PAD = 0.25, 0.05                       # ~20 km tiles, ~5 km pad at edges
    tiles = set()
    for pt in cand.geometry:
        for bx in range(int((pt.x - PAD) // T), int((pt.x + PAD) // T) + 1):
            for by in range(int((pt.y - PAD) // T), int((pt.y + PAD) // T) + 1):
                tiles.add((bx, by))
    print(f"[qc] fetching claims over {len(tiles)} tiles near {len(cand)} candidates…")
    seen, rows, geoms = set(), [], []
    for k, (bx, by) in enumerate(sorted(tiles)):
        bbox = (bx * T, by * T, (bx + 1) * T, (by + 1) * T)
        try:
            feats = _wfs_bbox("SGM:Actifs", bbox)
        except Exception as e:
            print(f"[qc] tile {bx},{by} skipped ({str(e)[:40]})")
            continue
        for f in feats:
            p = f.get("properties", {})
            no = str(p.get("NO_TITRE") or "")
            if no in seen or not f.get("geometry"):
                continue
            try:
                gm = shape(f["geometry"])
            except Exception:
                continue
            if gm.is_empty:
                continue
            seen.add(no)
            rows.append({"TENURE_NUMBER_ID": no, "OWNER_NAME": str(p.get("TITULAIRE") or ""),
                         "CLAIM_NAME": "", "ISSUE_DATE": "", "GOOD_TO_DATE": ""})
            geoms.append(gm)
    g = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    g.to_parquet("data/qc/claims.parquet")
    print(f"[qc] claims {len(g)} (over {len(tiles)} tiles near {len(cand)} candidates)")


def fetch_reserves():
    _wfs_all("SGM:Contraintes_majeures", {"name": "CMI_NOM"}, "data/qc/reserves.parquet")


def boundary():
    dst = "data/keep/qc_boundary.parquet"
    if os.path.exists(dst):
        return
    os.makedirs("data/keep", exist_ok=True)
    d = requests.get("https://raw.githubusercontent.com/codeforgermany/click_that_hood/"
                     "main/public/data/canada.geojson", headers=UA, timeout=90).json()
    for f in d["features"]:
        if f["properties"].get("name") == "Quebec":
            gpd.GeoDataFrame({"name": ["Quebec"]}, geometry=[shape(f["geometry"])],
                             crs="EPSG:4326").to_parquet(dst)
            print("[qc] boundary saved")
            return


def run():
    os.makedirs("data/qc", exist_ok=True)
    boundary()
    fetch_occurrences()
    # QC 'Contraintes majeures' (withdrawn areas) WFS is pathologically slow server-side
    # (tens of seconds per small tile). Use a cached copy in data/keep if present; else
    # run claims-only (like NB) — held claims still drive the open-ground screen.
    cached = "data/keep/qc_reserves.parquet"
    if os.path.exists(cached):
        import shutil
        shutil.copy(cached, "data/qc/reserves.parquet")
        print("[qc] reserves from cache")
    fetch_claims()      # tiled around candidates (fast: ~1s/tile)


if __name__ == "__main__":
    run()
