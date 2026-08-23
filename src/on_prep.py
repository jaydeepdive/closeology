"""Normalize Ontario MDI into the pipeline's occurrence schema, and build
per-occurrence drill 'facts' from nearby drill holes (Ontario's drill highlights)."""
import numpy as np
import pandas as pd
import geopandas as gpd

METRIC = "EPSG:3161"   # Ontario MNR Lambert (metres)
DRILL_R = 600          # m: drill holes within this of an occurrence count as "its" drilling


def _commlist(row):
    out = []
    for col in ("PRIMARY_COMMODITIES", "SECONDARY_COMMODITIES"):
        v = row.get(col)
        if isinstance(v, str) and v.strip():
            out += [c.strip() for c in v.split(",") if c.strip()]
    # dedupe, keep order
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c); res.append(c)
    return res


def prep():
    m = gpd.read_parquet("data/on/mdi.parquet")
    m["commodities"] = m.apply(_commlist, axis=1)
    m["commodity"] = m["commodities"].map(lambda x: ", ".join(x))
    occ = gpd.GeoDataFrame({
        "minfile": m["MDI_IDENT"], "name": m["NAME"].fillna(m["MDI_IDENT"]),
        "status": m["STATUS"].fillna(""), "commodities": m["commodities"],
        "commodity": m["commodity"], "deposit_type": m["CLASS"].fillna(""),
        "minfile_url": m["INFO_LINK"].fillna(""),
        "prod_ind": m["STATUS"].fillna("").str.lower().str.contains("produc").map(lambda b: "Y" if b else "N"),
        "township": m.get("TOWNSHIP", ""),
    }, geometry=m.geometry, crs="EPSG:4326")
    occ.to_parquet("data/on/occurrences.parquet")
    print(f"[on prep] occurrences {len(occ)}")

    # drill facts: holes within DRILL_R of each occurrence
    d = gpd.read_parquet("data/on/drillholes.parquet").to_crs(METRIC)
    d["YEAR_DRILLED"] = pd.to_numeric(d["YEAR_DRILLED"], errors="coerce")
    d = d[(d.YEAR_DRILLED.isna()) | ((d.YEAR_DRILLED >= 1900) & (d.YEAR_DRILLED <= 2026))]
    om = occ.to_crs(METRIC)
    buf = gpd.GeoDataFrame({"key": occ["minfile"].astype(str).str.upper()},
                           geometry=om.geometry.buffer(DRILL_R), crs=METRIC)
    j = gpd.sjoin(d, buf, predicate="within", how="inner")
    facts = {}
    for key, grp in j.groupby("key"):
        yrs = grp["YEAR_DRILLED"].dropna()
        comps = [c for c in grp["COMPANY_NAME"].dropna().unique() if str(c).strip()][:2]
        elems = []
        for e in grp["ELEMENTS"].dropna():
            for tok in str(e).split(","):
                t = tok.strip()
                if t and t not in elems:
                    elems.append(t)
        yr = ""
        if len(yrs):
            lo, hi = int(yrs.min()), int(yrs.max())
            yr = f"{lo}–{hi}" if lo != hi else f"{lo}"
        parts = [f"{len(grp)} drill hole(s) nearby"]
        if yr:
            parts.append(yr)
        if comps:
            parts.append(", ".join(map(str, comps)))
        if elems:
            parts.append("tested " + ", ".join(elems[:6]))
        facts[key] = {"drill_highlights": " · ".join(parts), "latest_drill": int(yrs.max()) if len(yrs) else None,
                      "n_drill": int(len(grp))}
    rows = []
    for key in occ["minfile"].astype(str).str.upper():
        f = facts.get(key, {})
        rows.append({"key": key, "grade_str": "", "resource_cat": "", "has_resource": False,
                     "tonnes": np.nan, "tonnes_str": "", "capsule": "",
                     "drill_highlights": f.get("drill_highlights", ""),
                     "latest_drill": f.get("latest_drill"), "n_drill": f.get("n_drill", 0)})
    pd.DataFrame(rows).drop_duplicates("key").to_parquet("data/on/minfile_facts.parquet")
    nnz = sum(1 for r in rows if r["drill_highlights"])
    print(f"[on prep] drill facts: {nnz}/{len(rows)} occurrences have nearby drilling")


if __name__ == "__main__":
    prep()
