"""Extract per-occurrence facts from the MINFILE Access DB via access-parser:
grade + resource stage (R28/E29), headline tonnage (capsule narrative),
and drill/assay highlights (C08 capsule)."""
import sys
import re
import pandas as pd

sys.path.insert(0, "src")
from access_parser import AccessParser

DB = "data/bc/MinFile-pc.accdb"
RESCAT = {"BA": "Assay", "CB": "Combined", "IF": "Inferred", "IN": "Indicated",
          "MG": "Geological", "MR": "Measured", "PB": "Probable", "PS": "Possible",
          "PV": "Proven", "UN": "Uncategorized", "**": "Production"}
RANK = {"PV": 7, "PB": 6, "MR": 5, "IN": 4, "IF": 3, "CB": 2, "MG": 1, "BA": 0, "**": 5}

GRADE = re.compile(r'\d[\d,]*\.?\d*\s?(?:g/t|gpt|grams?\s+per\s+tonne|%|per\s?cent|oz|opt|ounces?|ppm|ppb)', re.I)
WIDTH = re.compile(r'\d[\d,]*\.?\d*\s?(?:metre|meter|\bm\b|foot|feet|\bft\b)', re.I)
DRILLW = re.compile(r'\b(?:drill|ddh|hole|intersect|intercept|assay)', re.I)
# tonnage in narrative, near a reserve/resource/production/ore keyword
TON = re.compile(r'([\d][\d,]*(?:\.\d+)?)\s*(million tonnes|million tons|mt\b|tonnes|tons)', re.I)
CTX = re.compile(r'(reserve|resource|production|ore|mined|milled|grading|averag)', re.I)


def _df(db, t):
    d = db.parse_table(t)
    return pd.DataFrame({k: v for k, v in d.items()}) if d else pd.DataFrame()


def _fmt_t(t):
    if not t or t != t:
        return ""
    if t >= 1e9:
        return f"{t/1e9:.1f} Bt"
    if t >= 1e6:
        return f"{t/1e6:.1f} Mt"
    if t >= 1e3:
        return f"{t/1e3:.0f} kt"
    return f"{t:.0f} t"


def _mass_kg(kg):
    """Readable metal mass from kilograms."""
    if not kg or kg != kg:
        return ""
    t = kg / 1000.0
    if t >= 1e6:
        return f"{t/1e6:.2f} Mt"
    if t >= 1e3:
        return f"{t/1e3:.1f} kt"
    if t >= 1:
        return f"{t:,.0f} t"
    if kg >= 1:
        return f"{kg:,.0f} kg"
    return f"{kg*1000:,.0f} g"


def _capsule_tonnes(txt):
    if not isinstance(txt, str):
        return None
    best = 0.0
    for m in TON.finditer(txt):
        s, e = m.start(), m.end()
        if not CTX.search(txt[max(0, s-90):min(len(txt), e+40)]):
            continue
        try:
            val = float(m.group(1).replace(",", ""))
        except Exception:
            continue
        unit = m.group(2).lower()
        if unit.startswith("million") or unit == "mt":
            val *= 1e6
        best = max(best, val)
    return best or None


def _highlights(txt):
    if not isinstance(txt, str):
        return ""
    sents = re.split(r'(?<=[.;])\s+', txt)
    keep = [s.strip() for s in sents
            if GRADE.search(s) and (WIDTH.search(s) or DRILLW.search(s) or '%' in s or 'g/t' in s.lower())]
    keep.sort(key=lambda s: (0 if DRILLW.search(s) else 1))
    return " ".join(keep[:5])[:800]


def build():
    db = AccessParser(DB)
    e01 = _df(db, "E01_Minfile_Occurrences")[["MINFILE_ID", "MINFILNO"]]
    e19 = _df(db, "E19_Commodity_Types")
    gr = _df(db, "R28_Minfile_Inventory_Commodities")
    resc = _df(db, "E29_Inventory_Category_Types")
    cap = _df(db, "C08_Capsule_Geology_Comments")

    e19m = dict(zip(e19["COMMOD_ID"], e19["COMMOD_D"]))
    prec = dict(zip(e19["COMMOD_ID"], [str(x).strip().upper() for x in e19["PRECIOUS_IND"]]))
    rescc = dict(zip(resc["RESCAT_ID"], [str(x).strip() for x in resc["RESCAT_C"]]))
    gr["GRADE"] = pd.to_numeric(gr["GRADE"], errors="coerce")
    gr = gr.dropna(subset=["GRADE"])
    gr["code"] = gr["RESCAT_ID"].map(rescc)

    abbr = {"Gold": "Au", "Silver": "Ag", "Copper": "Cu", "Lead": "Pb", "Zinc": "Zn",
            "Molybdenum": "Mo", "Nickel": "Ni", "Cobalt": "Co", "Tungsten": "WO3",
            "Platinum": "Pt", "Palladium": "Pd"}

    # headline grade group per occurrence: most advanced rescat, then most commodities
    facts = {}
    for mid, g in gr.groupby("MINFILE_ID"):
        g = g.copy()
        g["rank"] = g["code"].map(lambda c: RANK.get(c, 0))
        key = g.groupby(["OREZON_ID", "RESCAT_ID"]).agg(rank=("rank", "max"), n=("GRADE", "size")).reset_index()
        best = key.sort_values(["rank", "n"]).iloc[-1]
        sub = g[(g.OREZON_ID == best.OREZON_ID) & (g.RESCAT_ID == best.RESCAT_ID)]
        parts = []
        for _, gg in sub.iterrows():
            nm = str(e19m.get(gg["COMMOD_ID"], "")).strip()
            if not nm:
                continue
            unit = "g/t" if prec.get(gg["COMMOD_ID"]) == "Y" else "%"
            parts.append(f"{abbr.get(nm, nm[:3])} {gg['GRADE']:g}{unit}")
        code = str(best.get("RESCAT_ID"))
        facts[mid] = {"grade_str": ", ".join(parts[:5]), "has_resource": True,
                      "resource_cat": RESCAT.get(rescc.get(best.RESCAT_ID, ""), "")}

    # ---- structured PRODUCTION: ore mined/milled (R18A) + commodity yield (R18B) ----
    a = _df(db, "R18A_Minfile_Materials_Mined")
    b = _df(db, "R18B_Minfile_Commodities_Yield")
    for c in ("YEAR", "MINED", "MILLED"):
        a[c] = pd.to_numeric(a[c], errors="coerce")
    for c in ("YEAR", "QUANTITY"):
        b[c] = pd.to_numeric(b[c], errors="coerce")
    prod = {}
    for mid, g in a.groupby("MINFILE_ID"):
        yrs = g["YEAR"].dropna()
        prod[mid] = {"y0": int(yrs.min()) if len(yrs) else None,
                     "y1": int(yrs.max()) if len(yrs) else None,
                     "ore": float(g["MINED"].sum() or 0), "mil": float(g["MILLED"].sum() or 0),
                     "comm": {}}
    for mid, g in b.groupby("MINFILE_ID"):
        d = prod.setdefault(mid, {"y0": None, "y1": None, "ore": 0.0, "mil": 0.0, "comm": {}})
        yrs = g["YEAR"].dropna()
        if len(yrs):
            d["y0"] = min(d["y0"] or 9999, int(yrs.min()))
            d["y1"] = max(d["y1"] or 0, int(yrs.max()))
        for cid, gg in g.groupby("COMMOD_ID"):
            q = float(gg["QUANTITY"].sum() or 0)
            if q:
                d["comm"][cid] = d["comm"].get(cid, 0.0) + q

    def _fmt_prod(d):
        if not d:
            return ""
        cparts = []
        for cid, q in sorted(d["comm"].items(), key=lambda x: -x[1]):
            nm = str(e19m.get(cid, "")).strip()
            if not nm or q <= 0:
                continue
            kg = q / 1000.0 if prec.get(cid) == "Y" else q   # precious grams->kg; base already kg
            m = _mass_kg(kg)
            if m:
                cparts.append(f"{m} {abbr.get(nm, nm)}")
        mil, mined = d.get("mil") or 0, d.get("ore") or 0
        ore = max(mil, mined)
        seg = []
        if ore:
            seg.append(_fmt_t(ore) + (" milled" if mil >= mined else " mined"))
        if cparts:
            seg.append(", ".join(cparts[:6]))
        if not seg:
            return ""
        yr = f"{d['y0']}–{d['y1']}" if d.get("y0") and d.get("y1") else ""
        return (f"Produced {yr}: " if yr else "Produced: ") + " — ".join(seg)

    prod_str = {mid: _fmt_prod(d) for mid, d in prod.items()}

    caps = {}
    for _, r in cap.iterrows():
        t = r["CAPSUL_T"]
        caps[r["MINFILE_ID"]] = (_highlights(t), t[:1200] if isinstance(t, str) else "", _capsule_tonnes(t))

    rows = []
    for _, r in e01.iterrows():
        mid = r["MINFILE_ID"]
        mfno = str(r["MINFILNO"]).replace(" ", "").upper()
        f = facts.get(mid, {})
        dh, capsule, tonnes = caps.get(mid, ("", "", None))
        rows.append({"key": mfno, "grade_str": f.get("grade_str", ""),
                     "resource_cat": f.get("resource_cat", ""), "has_resource": bool(f.get("has_resource")),
                     "tonnes": tonnes, "tonnes_str": _fmt_t(tonnes),
                     "drill_highlights": dh, "capsule": capsule,
                     "production": prod_str.get(mid, "")})
    out = pd.DataFrame(rows).drop_duplicates("key")
    out.to_parquet("data/bc/minfile_facts.parquet")
    print(f"[facts] {len(out)} occ | grade: {int((out.grade_str.str.len()>0).sum())} | "
          f"tonnage: {int(out.tonnes.notna().sum())} | drill highlights: {int((out.drill_highlights.str.len()>0).sum())} | "
          f"production: {int((out.production.str.len()>0).sum())}")


if __name__ == "__main__":
    build()
