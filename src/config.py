"""Shared config for Project Closeology (multi-region)."""

# --- BC WFS ---
BC_WFS = "https://openmaps.gov.bc.ca/geo/pub/{layer}/ows"
OCC_CSV = ("https://catalogue.data.gov.bc.ca/dataset/92206d94-bc64-4111-a295-cd14eb5a501c/"
           "resource/120d5ee6-bff5-4cbe-b106-e419c790c395/download/minfile_mineral.csv")

METRIC_CRS = "EPSG:3005"          # BC Albers (metres)
GRID_M = 500                      # synthetic staking-cell size (25 ha)
NEIGHBOR_M = 1000                 # adjacency radius around an occurrence
TOP_N = 700                       # occurrences enumerated for cell detail

METAL_ORDER = ["Gold", "Silver", "Platinum", "Palladium", "Copper", "Lead", "Zinc",
               "Molybdenum", "Nickel", "Cobalt", "Tungsten", "Tin", "Antimony", "Bismuth",
               "Uranium", "Lithium", "Vanadium", "Rare earths", "Niobium", "Tantalum",
               "Beryllium", "Chromium", "Titanium", "Iron", "Manganese", "Graphite",
               "Industrial", "Other metallic"]

METAL_COLOR = {
    "Gold": "#e6b800", "Silver": "#9aa7b4", "Platinum": "#6f7d8c", "Palladium": "#8a94a0",
    "Copper": "#e07a3f", "Lead": "#7d8a99", "Zinc": "#5fb0c9", "Molybdenum": "#a855c7",
    "Nickel": "#3fa66a", "Cobalt": "#3b6fd4", "Tungsten": "#8a6d3b", "Tin": "#b0a99f",
    "Antimony": "#6b7280", "Bismuth": "#c084fc", "Uranium": "#7cc043", "Lithium": "#22c1a6",
    "Vanadium": "#d4a017", "Rare earths": "#d15fa8", "Niobium": "#4b8bbe", "Tantalum": "#5a7d9a",
    "Beryllium": "#7bb37b", "Chromium": "#4a9e8f", "Titanium": "#9aa0a6", "Iron": "#b0553f",
    "Manganese": "#a06a86", "Graphite": "#555b63", "Industrial": "#c9a15f", "Other metallic": "#8091a5",
}

METAL_ABBR = {"Gold": "Au", "Silver": "Ag", "Platinum": "Pt", "Palladium": "Pd", "Copper": "Cu",
              "Lead": "Pb", "Zinc": "Zn", "Molybdenum": "Mo", "Nickel": "Ni", "Cobalt": "Co",
              "Tungsten": "W", "Tin": "Sn", "Antimony": "Sb", "Bismuth": "Bi", "Uranium": "U",
              "Lithium": "Li", "Vanadium": "V", "Rare earths": "REE", "Niobium": "Nb",
              "Tantalum": "Ta", "Beryllium": "Be", "Chromium": "Cr", "Titanium": "Ti",
              "Iron": "Fe", "Manganese": "Mn", "Graphite": "Cg", "Industrial": "Ind",
              "Other metallic": "Met"}

# commodity description (MINFILE / MDI / SIGEOM) -> metal bucket
_SELF = {"Gold", "Silver", "Platinum", "Palladium", "Copper", "Lead", "Zinc", "Molybdenum",
         "Nickel", "Cobalt", "Tungsten", "Tin", "Antimony", "Bismuth", "Uranium", "Lithium",
         "Vanadium", "Niobium", "Tantalum", "Beryllium", "Chromium", "Titanium", "Iron",
         "Manganese", "Graphite"}
# synonyms / element strings -> canonical bucket
_SYN = {"Rare Earths": "Rare earths", "Rare Earth": "Rare earths", "Rare Earth Elements": "Rare earths",
        "Ree": "Rare earths", "Etr": "Rare earths", "Cerium": "Rare earths", "Neodymium": "Rare earths",
        "Lanthanum": "Rare earths", "Yttrium": "Rare earths", "Dysprosium": "Rare earths",
        "Pge": "Platinum", "Egp": "Platinum", "Platinum Group": "Platinum",
        "Platinum Group Elements": "Platinum", "Platinum Group Metals": "Platinum",
        "Lithium Oxide": "Lithium", "Vanadium Pentoxide": "Vanadium", "Chromite": "Chromium",
        "Ilmenite": "Titanium", "Rutile": "Titanium", "Pyrochlore": "Niobium", "Columbium": "Niobium",
        "Wolfram": "Tungsten", "Tungsten Trioxide": "Tungsten", "Wo3": "Tungsten"}
_INDUSTRIAL = {"Barite", "Gypsum", "Limestone", "Dolomite", "Coal", "Clay", "Gravel",
               "Sand", "Silica", "Talc", "Magnesite", "Marble", "Granite", "Jade",
               "Wollastonite", "Diatomite", "Zeolite", "Perlite", "Gemstone",
               "Building Stone", "Dimension Stone", "Phosphate", "Potash", "Salt",
               "Sulphur", "Peat", "Feldspar", "Mica", "Graphite", "Fluorite"}


# industrial / dimension-stone / aggregate keywords — matched as substrings so
# qualified names ("Marble (Building Stone)", "Marble (High Purity/Flux)") are caught
_INDUSTRIAL_KW = ("marble", "granite", "gneiss", "sandstone", "limestone", "dolomite",
                  "building stone", "dimension stone", "structural material", "aggregate",
                  "gravel", "clay", "gypsum", "barite", "baryte", "talc", "silica",
                  "quartzite", "flux", "peat", "feldspar", "mica", "wollastonite", "coal",
                  "slate", "soapstone", "ballast", "flagstone", "diatomite", "zeolite",
                  "perlite", "gemstone", "quarry", "sand ", "salt", "potash")


def metal_bucket(commodity):
    c = (commodity or "").strip()
    ct = c.title()
    if ct in _SYN:
        return _SYN[ct]
    if ct in _SELF:
        return ct
    cl = c.lower()
    if any(k in cl for k in _INDUSTRIAL_KW) or ct in _INDUSTRIAL:
        return "Industrial"
    return "Other metallic"


def spend_points(spend):
    """Exploration $ spent nearby -> ranking boost (higher spend = higher rank)."""
    if not spend or spend != spend:
        return 0
    if spend >= 5e6: return 20
    if spend >= 1e6: return 15
    if spend >= 250e3: return 10
    if spend >= 50e3: return 6
    if spend >= 1e3: return 2
    return 0


# --- grade QUALITY = in-situ contained-metal VALUE, commodity-agnostic ------
# Grade is scored by the dollar value of contained metal per tonne at current
# market prices. This ranks by real economics without preferring any commodity:
# a 10 g/t Au or 15% Zn body scores high, a marginal 4.75% Zn showing or a barren
# iron/sulphur pyrite mass scores near zero. Two guards keep a pricey metal from
# distorting things: bulk commodities that aren't staking targets (Fe, S, Mn) are
# priced at 0, and a per-metal GRADE FLOOR means a *trace* of a valuable metal
# (a few ppm of gallium, say) contributes nothing no matter how high its price —
# only a genuinely meaningful grade counts.
#
# Prices are $/kg of metal and are refreshed on each daily run (fetch_prices.py
# writes data/keep/metal_prices.json); the values below are the fallback if that
# file is absent or stale. Contained value: $/t = kg-metal-per-tonne * $/kg,
# where kg/t = grade% * 10  (base)  or  grade_gpt / 1000  (precious/minor).
import os as _os
import json as _json
DEFAULT_PRICE_KG = {              # USD per kg of contained metal — real quotes, ~1 Sep 2026
    # sourced from Kitco (precious), metalcharts.org/LME (base), Fastmarkets /
    # SMM / strategic-metals trackers (minor+critical). Realizable spot/FOB used
    # for thin markets, NOT small-lot investor-retail quotes. fetch_prices.py
    # refreshes these on each daily build.
    "au": 139340.0, "ag": 2070.0, "pt": 56650.0, "pd": 41900.0, "rh": 150000.0,
    "cu": 14.6, "pb": 1.91, "zn": 3.93, "ni": 16.7, "co": 56.3, "mo": 33.0,
    "sn": 50.2, "w": 380.0, "wo3": 300.0, "sb": 50.0, "bi": 67.0,
    "u": 208.0, "u3o8": 176.0, "li": 120.0, "li2o": 56.0, "v": 25.0, "v2o5": 12.0,
    "ga": 350.0, "ge": 6000.0, "in": 970.0, "te": 240.0,
    "reo": 30.0, "treo": 30.0, "nd": 245.0, "pr": 245.0, "dy": 930.0, "tb": 4030.0,
    "la": 3.0, "ce": 3.0, "cd": 3.0,
    "fe": 0.0, "iro": 0.0, "iron": 0.0, "mn": 0.0, "s": 0.0, "as": 0.0, "al": 0.0,
}
# minimum meaningful grade for a metal to contribute at all (in its canonical
# reporting unit: g/t for precious/minor, % for base metals). Below this the
# metal is a trace and adds no value regardless of price.
GRADE_FLOOR = {
    "au": 0.3, "ag": 5.0, "pt": 0.1, "pd": 0.1, "rh": 0.05,       # g/t
    "cu": 0.1, "pb": 0.2, "zn": 0.3, "ni": 0.05, "co": 0.02,      # %
    "mo": 0.01, "sn": 0.05, "w": 0.03, "wo3": 0.03, "sb": 0.05,
    "bi": 0.02, "u": 0.005, "u3o8": 0.005, "li": 0.1, "li2o": 0.1,
    "v": 0.05, "v2o5": 0.05, "reo": 0.1, "treo": 0.1,
    "nd": 0.005, "pr": 0.005, "dy": 0.001, "tb": 0.0005,
    "ga": 0.003, "ge": 0.001, "in": 0.0005, "te": 0.0005, "cd": 0.05,
}
VALUE_CAP = 400.0                                             # $/t that earns full grade credit
PRICES_UPDATED = "built-in defaults"
PRICE_KG = dict(DEFAULT_PRICE_KG)
try:
    _pf = _os.path.join("data", "keep", "metal_prices.json")
    if _os.path.exists(_pf):
        _d = _json.load(open(_pf))
        for _k, _v in (_d.get("prices_per_kg") or {}).items():
            if isinstance(_v, (int, float)) and _v >= 0:
                PRICE_KG[_k.lower()] = float(_v)
        PRICES_UPDATED = _d.get("updated", PRICES_UPDATED)
except Exception:
    pass

import re as _re
_GRADE_RE = _re.compile(r"([A-Za-z][A-Za-z0-9]{0,4})\s*([\d.]+)\s*(g/t|%)", _re.I)
_TONNES_RE = _re.compile(r"([\d.]+)\s*(kt|mt|gt|t)\b", _re.I)
_TMULT = {"t": 1.0, "kt": 1e3, "mt": 1e6, "gt": 1e9}
_PRECIOUS = {"au", "ag", "pt", "pd", "rh"}


def grade_value(grade_str):
    """In-situ contained-metal value in $/tonne at current prices, with a trace
    grade floor so a pricey metal at negligible grade contributes nothing."""
    if not grade_str or str(grade_str) == "nan":
        return 0.0
    val = 0.0
    for ab, num, unit in _GRADE_RE.findall(str(grade_str)):
        a = ab.lower()
        price = PRICE_KG.get(a)
        if not price:
            continue
        try:
            v = float(num)
        except ValueError:
            continue
        is_gpt = "g" in unit.lower()
        # normalise to the metal's canonical unit (precious -> g/t, base -> %)
        if a in _PRECIOUS:
            gpt = v * 10000.0 if not is_gpt else v          # % -> g/t if needed
            if gpt < GRADE_FLOOR.get(a, 0):
                continue
            val += (gpt / 1000.0) * price                    # kg/t * $/kg
        else:
            pct = v / 10000.0 if is_gpt else v               # g/t -> % if needed
            if pct < GRADE_FLOOR.get(a, 0):
                continue
            val += (pct * 10.0) * price
    return val


def grade_quality(grade_str):
    """0..1 — in-situ value normalised against VALUE_CAP ($/t for full credit)."""
    return min(grade_value(grade_str) / VALUE_CAP, 1.0)


# --- extract grades from free capsule / assay text (BC + Ontario, identical) --
_METAL_WORD = {
    "gold": "au", "silver": "ag", "copper": "cu", "lead": "pb", "zinc": "zn",
    "molybdenum": "mo", "molybdenite": "mo", "nickel": "ni", "cobalt": "co",
    "tungsten": "w", "tin": "sn", "uranium": "u", "iron": "fe", "sulphur": "s",
    "sulfur": "s", "cadmium": "cd", "antimony": "sb", "manganese": "mn",
    "vanadium": "v", "lithium": "li", "platinum": "pt", "palladium": "pd",
    "gallium": "ga", "germanium": "ge", "bismuth": "bi", "arsenic": "as",
    "indium": "in", "tellurium": "te", "rhodium": "rh",
    "neodymium": "nd", "praseodymium": "pr", "dysprosium": "dy", "terbium": "tb",
    "lanthanum": "la", "cerium": "ce",
}
_UNIT_WORD = (r"per\s*cent|percent|%|grams?\s*per\s*tonne|g/t|gpt|"
              r"ounces?\s*per\s*ton|oz/t|parts\s*per\s*million|ppm|"
              r"parts\s*per\s*billion|ppb")
_GRADE_TEXT_RE = _re.compile(
    r"(\d+\.?\d*)\s*(" + _UNIT_WORD + r")\s*(?:\([^)]{0,12}\)\s*)?([a-zA-Z]+)", _re.I)
_TONNES_CTX = _re.compile(r"\d[\d,]*\s*tonnes|\d+\.?\d*\s*(?:mt|million\s*tonnes)", _re.I)
_RESOURCE_KW = _re.compile(r"averag|grad|indicat|inferred|measured|contain|reserve|resourc", _re.I)
_INTERSECT_KW = _re.compile(r"averag|grad|over\s*\d+\.?\d*\s*(?:m|metre|meter)", _re.I)


def _canon_grade(value, unitword, metal_abbr):
    """-> (value, 'pct'|'gpt') in the metal's natural reporting unit."""
    u = unitword.lower().replace(" ", "")
    if "percent" in u or u == "%" or "cent" in u:
        v, unit = value, "pct"
    elif "ounce" in u or "oz" in u:
        v, unit = value * 34.2857, "gpt"        # troy oz/short ton -> g/t
    elif "billion" in u or u == "ppb":
        v, unit = value / 1000.0, "gpt"          # ppb -> g/t
    elif "million" in u or u == "ppm":
        v, unit = value, "gpt"                   # ppm == g/t
    else:                                         # grams per tonne / g/t
        v, unit = value, "gpt"
    precious = metal_abbr in ("au", "ag", "pt", "pd")
    if precious and unit == "pct":
        v, unit = v * 10000.0, "gpt"
    elif (not precious) and unit == "gpt":
        v, unit = v / 10000.0, "pct"
    return v, unit


# confidence weight by how the grade was established: an actual resource is
# trusted fully, a drill-intersection average less, an isolated grab sample least.
TIER_CONF = {1: 1.0, 2: 0.8, 3: 0.5}


def grades_from_text(text):
    """(grade_str, confidence) from capsule/assay prose. Prefers deposit-scale
    (resource, then drill-intersection) grades over isolated grab samples, and
    returns a confidence reflecting which tier the grade came from. Returns
    ('', 1.0) when nothing usable is found."""
    t = str(text or "")
    if not t or t == "nan":
        return "", 1.0
    import statistics
    tiers = {1: {}, 2: {}, 3: {}}      # tier -> {abbr -> [values in canonical unit]}
    units = {}
    for m in _GRADE_TEXT_RE.finditer(t):
        num, unitword, word = m.group(1), m.group(2), m.group(3).lower()
        ab = _METAL_WORD.get(word)
        if not ab:
            continue
        try:
            v = float(num)
        except ValueError:
            continue
        cv, cu = _canon_grade(v, unitword, ab)
        win = t[max(0, m.start() - 130):m.start()]
        if _TONNES_CTX.search(win) and _RESOURCE_KW.search(win):
            tier = 1
        elif _INTERSECT_KW.search(win):
            tier = 2
        else:
            tier = 3
        tiers[tier].setdefault(ab, []).append(cv)
        units[ab] = cu
    chosen_tier = 1 if tiers[1] else (2 if tiers[2] else (3 if tiers[3] else 0))
    chosen = tiers.get(chosen_tier) or {}
    if not chosen:
        return "", 1.0
    conf = TIER_CONF[chosen_tier]
    parts = []
    for ab, vals in chosen.items():
        v = statistics.median(vals)
        if units[ab] == "gpt":
            parts.append((ab, f"{ab.title()} {v:.1f}g/t" if v < 100 else f"{ab.title()} {v:.0f}g/t"))
        else:
            parts.append((ab, f"{ab.title()} {v:.2f}%".rstrip("0").rstrip(".") + ("%" if False else "")))
    # keep a stable, readable order (priced/high-value first)
    order = {k: i for i, k in enumerate(
        ["au", "ag", "pt", "pd", "cu", "pb", "zn", "ni", "co", "mo", "w", "sn",
         "u", "sb", "bi", "li", "v", "fe", "s", "mn", "cd", "as", "ga", "ge"])}
    parts.sort(key=lambda p: order.get(p[0], 99))
    # fix percent formatting (ensure exactly one % sign)
    out = []
    for ab, s in parts:
        if units[ab] == "pct":
            num = s.split(" ", 1)[1].rstrip("%")
            out.append(f"{ab.title()} {num}%")
        else:
            out.append(s)
    return ", ".join(out), conf


import re as _re_year
_YEAR_RE = _re_year.compile(r"\b(18\d\d|19\d\d|20[0-2]\d)\b")


def last_production_year(*texts):
    """Latest 4-digit year mentioned in production text (e.g. 'Produced 1928–2011: …').
    Returns int or None. Used to weight past production by recency."""
    best = None
    for t in texts:
        for m in _YEAR_RE.finditer(str(t or "")):
            y = int(m.group(1))
            if 1850 <= y <= 2035 and (best is None or y > best):
                best = y
    return best


def parse_tonnes(tonnes_str):
    m = _TONNES_RE.search(str(tonnes_str or ""))
    if not m:
        return 0.0
    try:
        return float(m.group(1)) * _TMULT[m.group(2).lower()]
    except (ValueError, KeyError):
        return 0.0


def size_quality(tonnes):
    t = tonnes or 0
    if t >= 50e6: return 16
    if t >= 10e6: return 13
    if t >= 1e6:  return 10
    if t >= 100e3: return 6
    if t >= 10e3:  return 3
    return 0                                            # < 10 kt is not a target on its own


def score_lead(status, deposit_open, grade_str="", tonnes_str="", has_drill=False, spend=0,
               grade_conf=1.0, last_prod_year=None, primary_metal=""):
    """0-100 opportunity score. Jurisdiction-agnostic and recency-aware."""
    return score_breakdown(status, deposit_open, grade_str, tonnes_str,
                           has_drill, spend, grade_conf, last_prod_year, primary_metal)["total"]


# commodities we price at ~$0 (bulk/industrial) — a body known to be one of these
# is not a staking target even without an assay number, so it earns no implied grade.
_LOW_VALUE_METAL = {"Iron", "Industrial", "Manganese"}


_CONF_LABEL = {1.0: "resource-grade", 0.8: "drill-intersection", 0.5: "grab-sample"}

# non-producer development status -> (points, implied-grade credit, label)
_STATUS_PTS = {
    "developed": (15, 13, "Developed prospect"),
    "deposit": (13, 11, "Classified deposit"),
    "prospect": (9, 5, "Prospect"),
    "discovery": (7, 4, "Discovery"),
    "showing": (5, 0, "Showing"),
    "occurrence": (3, 0, "Occurrence"),
}


def _prod_recency(year):
    """Past-production value by WHEN it last produced. Pre-1980 workings are
    treated as spent — a century-old mine almost never has modern-economic
    material left, so it earns little. Recent producers (still-modern mines that
    closed on price/permitting, not exhaustion) are the prime targets.
    Returns (status_pts, implied_grade_factor 0..1, label)."""
    try:
        y = int(year) if year is not None and str(year).strip() not in ("", "nan", "None") else None
    except (ValueError, TypeError):
        y = None
    if y is None:
        return (10, 0.5, "past producer — production date unknown")
    if y >= 2010:
        return (24, 1.0, f"modern past producer (to {y}) — likely revivable")
    if y >= 2000:
        return (22, 1.0, f"recent past producer ({y})")
    if y >= 1990:
        return (18, 0.85, f"past producer ({y})")
    if y >= 1980:
        return (13, 0.6, f"past producer ({y})")
    return (3, 0.1, f"historic pre-1980 workings ({y}) — little modern-economic material likely remains")


def score_breakdown(status, deposit_open, grade_str="", tonnes_str="", has_drill=False,
                    spend=0, grade_conf=1.0, last_prod_year=None, primary_metal=""):
    """Same scoring as score_lead, but returns the component parts so a lead can
    explain WHY it ranks where it does: {total, raw, parts:[{label,pts,note}]}.

    Jurisdiction-agnostic: the score is built from signals available everywhere
    (development status, open ground, drilling, deposit size) plus a grade term
    that uses the real in-situ value where a grade is on record and an implied
    value from past-production/development status where it is not — so a proven
    past producer on open ground ranks the same whether or not its province
    publishes assay numbers."""
    parts = []
    st = (status or "").lower()
    is_prod = "produc" in st
    if is_prod:
        sp, imp_factor, sl = _prod_recency(last_prod_year)
        implied_base = 18
    else:
        sp, imp_base, sl = _STATUS_PTS.get(
            next((k for k in _STATUS_PTS if k in st), "occurrence"), (2, 0, "Mineral occurrence"))
        imp_factor, implied_base = 1.0, imp_base
    parts.append({"label": "Development status", "pts": sp, "note": sl})

    if deposit_open:
        parts.append({"label": "Open ground", "pts": 16, "note": "The deposit's own ground is unstaked and stakeable"})
    if has_drill:
        parts.append({"label": "Drilling on record", "pts": 8, "note": "Documented drill/assay history de-risks the target"})

    gv_raw = grade_value(grade_str)
    conf = max(0.0, min(grade_conf, 1.0))
    gv = gv_raw * conf
    has_grade_data = len(str(grade_str or "").strip()) >= 2
    known_economic = has_grade_data and gv_raw > 0
    low_value = metal_bucket(primary_metal) in _LOW_VALUE_METAL if primary_metal else False
    if known_economic:
        gpts = round(min(gv / VALUE_CAP, 1.0) * 22)
        if gpts:
            cl = _CONF_LABEL.get(round(conf, 1), f"{int(conf*100)}%-confidence")
            note = f"In-situ metal value ≈ ${gv_raw:,.0f}/t ({cl}"
            note += f", discounted to ${gv:,.0f}/t)" if conf < 1.0 else ")"
            parts.append({"label": "Grade value", "pts": gpts, "note": note})
    elif has_grade_data or low_value:
        # grade is on record but the metals are worth ~$0 (iron/sulphur/industrial),
        # or the commodity itself is a bulk non-target — this is KNOWN low value, not
        # unknown, so it earns no implied credit. Keeps barren iron/pyrite bodies down.
        pass
    else:
        # unknown grade -> credit from status. If the commodity itself is unknown
        # ("Other metallic"), we can't be sure it's valuable, so credit cautiously.
        uncertain = metal_bucket(primary_metal) == "Other metallic" if primary_metal else False
        imp = round(implied_base * imp_factor * (0.5 if uncertain else 1.0))
        if imp:
            parts.append({"label": "Grade (implied)", "pts": imp,
                          "note": ("No assay and commodity not well defined — partial credit"
                                   if uncertain else
                                   "No assay in the public data — grade credited from "
                                   + ("past production" if is_prod else "development status"))})

    # deposit size counts only for an economically-graded body or a proven, still-
    # relevant deposit — never for a known-barren (iron/sulphur) mass or spent
    # pre-1980 workings.
    size_ok = known_economic and gv >= 25
    size_ok = size_ok or (not has_grade_data and not low_value
                          and (any(k in st for k in ("developed", "deposit")) or (is_prod and imp_factor >= 0.6)))
    spts = round(size_quality(parse_tonnes(tonnes_str))) if size_ok else 0
    if spts:
        parts.append({"label": "Deposit size", "pts": spts, "note": f"Tonnage {tonnes_str} on record"})

    spp = spend_points(spend)
    if spp:
        parts.append({"label": "Exploration spend", "pts": spp, "note": f"≈ ${spend:,.0f} already spent proving the ground nearby"})

    raw = sum(p["pts"] for p in parts)
    return {"total": min(raw, 100), "raw": raw, "parts": parts}
