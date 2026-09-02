"""Pull grade + tonnage for Ontario leads from the MDI record pages (structured
reserve/resource + production tables), then re-score and re-rank the leads."""
import os
import io
import re
import json
import time
import urllib.request
import pandas as pd
import geopandas as gpd
from shapely.geometry import box
from config import score_lead, METAL_ABBR

REC = "https://www.geologyontario.mndm.gov.on.ca/mndmfiles/mdi/data/records/{mdi}.html"
CACHE = "data/on/mdi_cache"
CATRANK = {"proven": 6, "probable": 5, "measured": 4, "indicated": 3, "inferred": 2, "unclassified": 1}
SYM = {"Gold": "Au", "Silver": "Ag", "Copper": "Cu", "Lead": "Pb", "Zinc": "Zn", "Nickel": "Ni",
       "Cobalt": "Co", "Molybdenum": "Mo", "Platinum": "Pt", "Palladium": "Pd", "Chromium": "Cr",
       "Iron": "Fe", "Tungsten": "W", "Uranium": "U", "Tin": "Sn", "Manganese": "Mn"}


def _fmt_t(t):
    if not t or t != t:
        return ""
    if t >= 1e6:
        return f"{t/1e6:.1f} Mt"
    if t >= 1e3:
        return f"{t/1e3:.0f} kt"
    return f"{t:.0f} t"


def _grade_str(commodities_cell):
    """'Cobalt 201.4 ppm, Copper 1.08 %, Gold 81.7 ppb' -> 'Co 201.4ppm, Cu 1.08%, Au 81.7ppb'."""
    if not isinstance(commodities_cell, str):
        return ""
    out = []
    for part in commodities_cell.split(","):
        m = re.match(r"\s*([A-Za-z ]+?)\s+([\d\.]+)\s*(%|ppm|ppb|g/t|oz/t)", part.strip())
        if m:
            nm = SYM.get(m.group(1).strip().title(), m.group(1).strip()[:3])
            out.append(f"{nm} {m.group(2)}{m.group(3)}")
    return ", ".join(out[:6])


def _fetch(mdi):
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, f"{mdi}.html")
    if os.path.exists(cp):
        return open(cp, encoding="utf-8", errors="replace").read()
    for a in range(3):
        try:
            html = urllib.request.urlopen(REC.format(mdi=mdi), timeout=25).read().decode("utf-8", "replace")
            open(cp, "w", encoding="utf-8").write(html)
            return html
        except Exception:
            if a == 2:
                return ""
            time.sleep(2)


def _parse(html):
    """-> (tonnes, resource_cat, grade_str, produced_tonnes, prod_year)."""
    try:
        tabs = pd.read_html(io.StringIO(html))
    except Exception:
        return None, "", "", None, None
    reserves = production = None
    for tb in tabs:
        cols = [str(c).lower() for c in tb.columns]
        if "category" in cols and "tonnes" in cols and "commodities" in cols:
            reserves = tb
        elif "year" in cols and "tonnes" in cols and "commodities" in cols and "category" not in cols:
            production = tb
    tonnes, cat, grade = None, "", ""
    if reserves is not None and len(reserves):
        r = reserves.copy()
        r["Tonnes"] = pd.to_numeric(r["Tonnes"], errors="coerce")
        r = r.dropna(subset=["Tonnes"])
        if len(r):
            r["rk"] = r["Category"].astype(str).str.lower().map(
                lambda c: max([v for k, v in CATRANK.items() if k in c] + [0]))
            best = r.sort_values(["rk", "Tonnes"]).iloc[-1]
            tonnes = float(best["Tonnes"])
            cat = re.sub(r"\s*mineral resource| reserve", "", str(best["Category"]), flags=re.I).strip()
            grade = _grade_str(best["Commodities"])
    produced = prod_year = None
    if production is not None and len(production):
        p = production.copy()
        p["Tonnes"] = pd.to_numeric(p["Tonnes"], errors="coerce")
        s = p["Tonnes"].sum()
        produced = float(s) if s == s and s > 0 else None
        if "Year" in p.columns:
            yy = pd.to_numeric(p["Year"], errors="coerce").dropna()
            prod_year = int(yy.max()) if len(yy) else None
    return tonnes, cat, grade, produced, prod_year


_GRADE_SENT = re.compile(r'\d[\d,]*\.?\d*\s?(?:%|g/t|gpt|oz/t|opt|ppm|ppb|per cent)', re.I)
_DRILLW = re.compile(r'\b(?:hole|ddh|drill|intersect|intercept|assay|grading|averag|over\s+\d)', re.I)


def _capsule_and_drill(html):
    """From an MDI record page: (capsule text, real drill/assay highlights).
    The public MDI 'Comments'/'Description' narrative sometimes carries actual
    intersections — surface those instead of the generic 'N holes nearby' count."""
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = re.sub(r"&#x?\w+;", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    m = re.search(r"(Comments?.*)", txt)
    cap = (m.group(1) if m else txt)[:1600]
    sents = re.split(r"(?<=[.;])\s+", cap)
    keep = [s.strip() for s in sents if _GRADE_SENT.search(s) and _DRILLW.search(s)]
    keep.sort(key=lambda s: (0 if re.search(r"g/t|%|oz", s, re.I) else 1))
    drill = " ".join(keep[:4])[:600]
    return cap, drill


def enrich(region_dir):
    out = os.path.join(region_dir, "out")
    leads = gpd.read_file(os.path.join(out, "leads.geojson"))
    stats = json.load(open(os.path.join(out, "stats.json")))
    if "grade_conf" not in leads.columns:
        leads["grade_conf"] = 1.0
    if "capsule" not in leads.columns:
        leads["capsule"] = ""
    if "last_prod_year" not in leads.columns:
        leads["last_prod_year"] = None
    n_grade = n_ton = n_drill = 0
    for i in leads.index:
        mdi = str(leads.at[i, "minfile"])
        html = _fetch(mdi)
        if not html:
            continue
        tonnes, cat, grade, produced, prod_year = _parse(html)
        if prod_year:
            leads.at[i, "last_prod_year"] = prod_year
        cap, real_drill = _capsule_and_drill(html)
        if cap:
            leads.at[i, "capsule"] = cap
        # real intersections from the record replace the generic "N holes nearby"
        if real_drill:
            leads.at[i, "drill_highlights"] = real_drill
            n_drill += 1
        if grade and not str(leads.at[i, "grade_str"]):
            leads.at[i, "grade_str"] = grade; n_grade += 1   # table grade = resource, conf 1.0
        # fallback: pull grades from the record's prose (same extractor as BC),
        # preferring resource/intersection grades over grab samples
        if len(str(leads.at[i, "grade_str"])) < 3:
            import re as _re2
            from config import grades_from_text
            page_txt = _re2.sub(r"<[^>]+>", " ", html)
            g2, cf2 = grades_from_text(page_txt)
            if not g2 and "drill_highlights" in leads.columns:
                g2, cf2 = grades_from_text(str(leads.at[i, "drill_highlights"]))
            if g2:
                leads.at[i, "grade_str"] = g2
                leads.at[i, "grade_conf"] = cf2
                n_grade += 1
        headline = tonnes or produced
        if headline:
            leads.at[i, "tonnes"] = headline
            leads.at[i, "tonnes_str"] = _fmt_t(headline)
            if tonnes:
                leads.at[i, "resource_cat"] = cat or "resource"
                leads.at[i, "deposit_size"] = f"{_fmt_t(tonnes)}" + (f" ({cat.lower()})" if cat else " resource")
            else:
                leads.at[i, "deposit_size"] = f"{_fmt_t(produced)} produced"
            n_ton += 1

    # last production year from the record prose where the table lacked it
    from config import last_production_year
    for i in leads.index:
        if not leads.at[i, "last_prod_year"] and "produc" in str(leads.at[i, "status"]).lower():
            y = last_production_year(leads.at[i, "capsule"])
            if y:
                leads.at[i, "last_prod_year"] = y
    # re-score with the new grade/tonnage/recency, then re-rank
    leads["score"] = leads.apply(lambda r: score_lead(
        r["status"], bool(r["deposit_open"]), r.get("grade_str", ""), r.get("tonnes_str", ""),
        bool(r.get("drill_highlights")), r.get("exploration_spend", 0),
        grade_conf=r.get("grade_conf", 1.0), last_prod_year=r.get("last_prod_year"),
        primary_metal=r.get("primary_metal", "")), axis=1)
    sort_keys = ["score", "deposit_open"] + (["exploration_spend"] if "exploration_spend" in leads.columns else [])
    leads = leads.sort_values(sort_keys, ascending=False).reset_index(drop=True)
    leads["rank"] = range(1, len(leads) + 1)
    leads["lead_id"] = ["L%04d" % i for i in leads["rank"]]

    cols = [c for c in leads.columns if c != "geometry"]
    leads[[c for c in cols]].drop(columns=["metal_buckets"], errors="ignore").to_csv(
        os.path.join(out, "leads.csv"), index=False)
    leads.to_file(os.path.join(out, "leads.geojson"), driver="GeoJSON")

    # rebuild open cells on the same lat/long lattice with refreshed ranks
    dlon, dlat = stats.get("grid_dlon"), stats.get("grid_dlat")
    if dlon and dlat and "cell_ids" in leads.columns:
        feats = []
        for _, r in leads.iterrows():
            for cid in str(r["cell_ids"]).split(";"):
                if "_" not in cid:
                    continue
                a, b = cid.split("_")
                i, j = int(a), int(b)
                feats.append({"lead_id": r["lead_id"], "rank": int(r["rank"]), "name": r["name"],
                              "geometry": box(i * dlon, j * dlat, (i + 1) * dlon, (j + 1) * dlat)})
        if feats:
            gpd.GeoDataFrame(feats, geometry="geometry", crs="EPSG:4326").to_file(
                os.path.join(out, "opencells.geojson"), driver="GeoJSON")

    stats["n_with_grade"] = int((leads["grade_str"].astype(str).str.len() > 0).sum())
    stats["n_with_tonnage"] = int((leads["tonnes_str"].astype(str).str.len() > 0).sum())
    json.dump(stats, open(os.path.join(out, "stats.json"), "w"), indent=2)
    print(f"[on web-facts] grade added: {n_grade} | tonnage added: {n_ton} | real drill: {n_drill} | leads {len(leads)}")


if __name__ == "__main__":
    enrich("data/on")
