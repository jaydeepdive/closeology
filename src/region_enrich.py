"""Per-jurisdiction grade/merit enrichment that goes beyond the base occurrence
layer — assay tables, development-history fields, detail pages — so leads in the
data-poorer provinces are properly qualified (grade + confidence + real status)."""
import geopandas as gpd
import pandas as pd
import arcgis_common as A
import samples as S
import enrich_facts as E


# ---------------------------------------------------------------- Nova Scotia
_NS_FS = ("https://dawson.novascotia.ca/arcgis/rest/services/Hosted/"
          "mineral_occurrence_database_d002ns_UT83/FeatureServer")


def _fetch_table(url, out_fields):
    """Page a non-spatial ArcGIS table as attribute rows (JSON, no geometry)."""
    import requests
    feats, off, page = [], 0, A._max_record_count(url)
    while True:
        q = {"where": "1=1", "outFields": out_fields, "returnGeometry": "false",
             "resultOffset": off, "resultRecordCount": page, "f": "json"}
        d = A._get(url + "/query", q)
        b = d.get("features", [])
        feats += [f["attributes"] for f in b]
        if len(b) < page:
            break
        off += len(b)
    return pd.DataFrame(feats)


def nova_scotia(out_dir="data/ns"):
    occ = gpd.read_parquet(f"{out_dir}/occurrences.parquet")
    ana = _fetch_table(f"{_NS_FS}/4", "occ_number,sample_id,element,concen,unit,qualifier")
    smp = _fetch_table(f"{_NS_FS}/8", "occ_number,sample_id,sam_type")
    typ = dict(zip(smp["sample_id"].astype(str), smp["sam_type"]))
    rows = []
    for _, r in ana.iterrows():
        st = typ.get(str(r.get("sample_id")), "")
        conf = S.type_conf(st)
        rows.append({"occ": str(r.get("occ_number")), "elem": r.get("element"),
                     "val": r.get("concen"), "unit": r.get("unit"), "conf": conf,
                     "is_drill": "drill" in str(st).lower() or "core" in str(st).lower(),
                     "below_dl": str(r.get("qualifier") or "").strip() in ("<", "less than")})
    grades = S.grades_by_occ(rows, lambda t: E.value_parts(t)[0])
    occ["_key"] = occ["minfile"].astype(str)
    occ["grade_str"] = occ["_key"].map(lambda k: grades.get(k, ("", 1.0, False))[0])
    occ["grade_conf"] = occ["_key"].map(lambda k: grades.get(k, ("", 1.0, False))[1])
    occ["drill_highlights"] = occ.apply(
        lambda r: "drill-tested" if grades.get(r["_key"], ("", 1.0, False))[2]
        else r.get("drill_highlights", ""), axis=1)
    occ = occ.drop(columns=["_key"])
    occ.to_parquet(f"{out_dir}/occurrences.parquet")
    ng = int((occ["grade_str"].astype(str).str.len() > 0).sum())
    print(f"[ns enrich] grades on {ng}/{len(occ)} occurrences (from {len(ana)} analyses)")
    return ng


# ---------------------------------------------------------------- New Brunswick
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
import requests
from config import grades_from_text

_UA = {"User-Agent": "Mozilla/5.0 (compatible; closeology/1.0)"}


def _nb_status(cls):
    c = cls.lower()
    if any(k in c for k in ("producer", "past-produc", "mine ", "production")):
        return "Past Producer"
    if "tonnage estimate" in c and "no tonnage" not in c and "poorly constrained" not in c:
        return "Deposit"
    if any(k in c for k in ("drilled", "significant assays", "assays over", "three dimensions", "developed")):
        return "Developed Prospect"
    if "prospect" in c:
        return "Prospect"
    if "deposit" in c:
        return "Deposit"
    return "Occurrence"


def _nb_page(url, cache_dir):
    cid = re.sub(r"[^0-9A-Za-z]", "_", url.split("componentID=")[-1])[:60]
    cp = os.path.join(cache_dir, cid + ".html")
    if os.path.exists(cp):
        return open(cp, encoding="utf-8", errors="replace").read()
    for a in range(3):
        try:
            h = requests.get(url, headers=_UA, timeout=30).text
            open(cp, "w", encoding="utf-8").write(h)
            return h
        except Exception:
            if a == 2:
                return ""
            time.sleep(1.5)


def _nb_extract(h):
    txt = re.sub(r"<[^>]+>", " ", h)
    txt = re.sub(r"&nbsp;", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    m = re.search(r"Status:\s*(.*?)\s*(?:Discovery:|Deposit Type|Commodit|Reference)", txt)
    cls = m.group(1).strip() if m else ""
    status = _nb_status(cls)
    # NB assay values live in an HTML table that flattens ambiguously (unit/element/
    # value columns run together -> spurious 'Au 403 g/t', 'Sn 50%'). We DON'T trust
    # scraped NB grades — the status classification is the reliable merit signal; grade
    # stays implied. (A structured table parse could add real grades later.)
    grade, conf = "", 1.0
    mt = re.search(r"Tonnes \(x 1000\):\s*([\d,]+)", txt)
    tonnes = None
    if mt:
        try:
            v = float(mt.group(1).replace(",", "")) * 1000
            tonnes = v if v > 0 else None
        except ValueError:
            pass
    drilled = bool(re.search(r"\bDDH\b|\bdrill", txt, re.I))
    return status, grade, conf, tonnes, drilled


_NB_FACTS = "data/keep/nb_facts.parquet"


def new_brunswick(out_dir="data/nb"):
    from build_minfile_facts import _fmt_t
    occ = gpd.read_parquet(f"{out_dir}/occurrences.parquet")
    occ["_key"] = occ["minfile"].astype(str)
    # use the committed distilled facts if present (so CI needn't rescrape ~1600 pages)
    if os.path.exists(_NB_FACTS) and not os.environ.get("FULL"):
        facts = pd.read_parquet(_NB_FACTS).set_index("key")
        def g(k, col, d):
            return facts.at[k, col] if k in facts.index else d
        occ["status"] = occ["_key"].map(lambda k: g(k, "status", "Occurrence"))
        occ["tonnes"] = occ["_key"].map(lambda k: g(k, "tonnes", None))
        occ["drill_highlights"] = occ["_key"].map(lambda k: g(k, "drill", ""))
        src = "cache"
    else:
        cache = os.path.join(out_dir, "pages")
        os.makedirs(cache, exist_ok=True)

        def work(u):
            if not isinstance(u, str) or not u.startswith("http"):
                return ("Occurrence", "", 1.0, None, False)
            h = _nb_page(u, cache)
            return _nb_extract(h) if h else ("Occurrence", "", 1.0, None, False)
        with ThreadPoolExecutor(max_workers=16) as ex:
            res = list(ex.map(work, occ["minfile_url"].tolist()))
        occ["status"] = [r[0] for r in res]
        occ["tonnes"] = [r[3] for r in res]
        occ["drill_highlights"] = [("drill-tested" if r[4] else "") for r in res]
        os.makedirs("data/keep", exist_ok=True)
        pd.DataFrame({"key": occ["_key"], "status": occ["status"], "tonnes": occ["tonnes"],
                      "drill": occ["drill_highlights"]}).to_parquet(_NB_FACTS)
        src = "scrape"
    occ["grade_str"] = ""
    occ["grade_conf"] = 1.0
    occ["tonnes_str"] = occ["tonnes"].map(lambda t: _fmt_t(t) if t else "")
    occ["prod_ind"] = occ["status"].map(lambda s: "Y" if "produc" in str(s).lower() else "N")
    occ = occ.drop(columns=["_key"])
    occ.to_parquet(f"{out_dir}/occurrences.parquet")
    from collections import Counter
    print(f"[nb enrich/{src}] status {dict(Counter(occ['status']).most_common(6))}")


# ---------------------------------------------------------------- Manitoba
_MB_MINES = ("https://rdmaps.gov.mb.ca/arcgis/rest/services/MapGallery/"
             "MG_GEOLOGY_CLIENT/MapServer/6")


def manitoba(out_dir="data/mb"):
    """MB's base occurrence layer is all bare 'Occurrence' with no merit; the
    'Mine Sites' layer (241 abandoned/past-producing mines w/ commodity + closure
    year) is the real signal. Add those as past-producer occurrences."""
    from shapely.geometry import shape, Point
    fs = A.fetch_layer(_MB_MINES,
                       "MINE_NAME,COMMODITY,PRODUCTION,MINE_STATUS,YEAR_CLOSURE,YESR_CLOSED,"
                       "SHAFT,PIT_STATUS,CURRENT_OWNER,PREVIOUS_OWNER", geom=True)
    rows, geoms = [], []
    for f in fs:
        g = f.get("geometry")
        if not g:
            continue
        try:
            pt = shape(g)
            pt = pt if pt.geom_type == "Point" else pt.representative_point()
        except Exception:
            continue
        p = f.get("properties", {})
        yr = None
        for k in ("YEAR_CLOSURE", "YESR_CLOSED"):
            try:
                y = int(float(p.get(k)))
                if 1850 <= y <= 2035:
                    yr = y
            except (TypeError, ValueError):
                pass
        comm = A._commlist(p.get("COMMODITY"))
        rows.append({"minfile": "MBMINE-" + str(p.get("MINE_NAME") or "")[:40],
                     "name": str(p.get("MINE_NAME") or "").strip().title() or "(unnamed mine)",
                     "status": "Past Producer", "commodities": comm, "commodity": ", ".join(comm),
                     "deposit_type": "", "minfile_url": "", "prod_ind": "Y", "township": "",
                     "drill_highlights": "", "last_prod_year": yr})
        geoms.append(pt)
    mines = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    base = gpd.read_parquet(f"{out_dir}/occurrences.parquet")
    if "last_prod_year" not in base.columns:
        base["last_prod_year"] = None
    keep = [c for c in base.columns if c in mines.columns or c == "geometry"]
    both = gpd.GeoDataFrame(pd.concat([base, mines], ignore_index=True), crs="EPSG:4326")
    both.to_parquet(f"{out_dir}/occurrences.parquet")
    print(f"[mb enrich] +{len(mines)} past-producing mine sites (yrs {int(mines.last_prod_year.notna().sum())})")


if __name__ == "__main__":
    nova_scotia()


