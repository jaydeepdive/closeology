"""Shared config for Project Closeology (multi-region)."""

# --- BC WFS ---
BC_WFS = "https://openmaps.gov.bc.ca/geo/pub/{layer}/ows"
OCC_CSV = ("https://catalogue.data.gov.bc.ca/dataset/92206d94-bc64-4111-a295-cd14eb5a501c/"
           "resource/120d5ee6-bff5-4cbe-b106-e419c790c395/download/minfile_mineral.csv")

METRIC_CRS = "EPSG:3005"          # BC Albers (metres)
GRID_M = 500                      # synthetic staking-cell size (25 ha)
NEIGHBOR_M = 1000                 # adjacency radius around an occurrence
TOP_N = 700                       # occurrences enumerated for cell detail

METAL_ORDER = ["Gold", "Silver", "Copper", "Lead", "Zinc", "Molybdenum", "Nickel",
               "Cobalt", "Tungsten", "Tin", "Uranium", "Iron", "Industrial", "Other metallic"]

METAL_COLOR = {
    "Gold": "#e6b800", "Silver": "#9aa7b4", "Copper": "#e07a3f", "Lead": "#7d8a99",
    "Zinc": "#5fb0c9", "Molybdenum": "#a855c7", "Nickel": "#3fa66a", "Cobalt": "#3b6fd4",
    "Tungsten": "#8a6d3b", "Tin": "#b0a99f", "Uranium": "#7cc043", "Iron": "#b0553f",
    "Industrial": "#c9a15f", "Other metallic": "#8091a5",
}

METAL_ABBR = {"Gold": "Au", "Silver": "Ag", "Copper": "Cu", "Lead": "Pb", "Zinc": "Zn",
              "Molybdenum": "Mo", "Nickel": "Ni", "Cobalt": "Co", "Tungsten": "W",
              "Tin": "Sn", "Uranium": "U", "Iron": "Fe", "Industrial": "Ind",
              "Other metallic": "Met"}

# commodity description (MINFILE / MDI) -> metal bucket
_SELF = {"Gold", "Silver", "Copper", "Lead", "Zinc", "Molybdenum", "Nickel",
         "Cobalt", "Tungsten", "Tin", "Uranium", "Iron"}
_INDUSTRIAL = {"Barite", "Gypsum", "Limestone", "Dolomite", "Coal", "Clay", "Gravel",
               "Sand", "Silica", "Talc", "Magnesite", "Marble", "Granite", "Jade",
               "Wollastonite", "Diatomite", "Zeolite", "Perlite", "Gemstone",
               "Building Stone", "Dimension Stone", "Phosphate", "Potash", "Salt",
               "Sulphur", "Peat", "Feldspar", "Mica", "Graphite", "Fluorite"}


def metal_bucket(commodity):
    c = (commodity or "").strip().title()
    if c in _SELF:
        return c
    if c in _INDUSTRIAL:
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
               grade_conf=1.0):
    """0-100 opportunity score, driven by GRADE QUALITY + DEPOSIT SIZE.

    Quality-first and commodity-agnostic: a marginal, tiny past-producer no
    longer tops the list just for ticking boxes. Grade (0-26) and size (0-16)
    are the dominant levers; status/open-ground/drilling/spend de-risk on top.
    grade_conf (0-1) discounts the grade by how it was established — an actual
    resource counts fully, a drill intersection ~0.8, a grab sample ~0.5."""
    s = 0
    st = (status or "").lower()
    if "past" in st and "produc" in st: s += 18       # a real deposit existed, but historic
    elif "produc" in st: s += 22                        # active/near producer
    elif "developed" in st: s += 15
    elif "deposit" in st: s += 13
    elif "prospect" in st: s += 9
    elif "discovery" in st: s += 7
    elif "showing" in st: s += 5
    elif "occurrence" in st: s += 3
    else: s += 2
    if deposit_open: s += 14                             # its own ground is stakeable
    if has_drill: s += 8
    gv = grade_value(grade_str) * max(0.0, min(grade_conf, 1.0))   # confidence-weighted $/t
    s += round(min(gv / VALUE_CAP, 1.0) * 26)            # 0..26  — the main lever
    # size only counts when the rock is actually worth something: a huge barren
    # (iron/sulphur) or unknown-grade tonnage earns no size credit.
    size_factor = 0.0 if gv < 25 else min((gv - 25) / 50.0, 1.0)
    s += round(size_quality(parse_tonnes(tonnes_str)) * size_factor)   # 0..16, grade-gated
    s += spend_points(spend)                             # 0..20
    return min(s, 100)
