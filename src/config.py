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
# Grade is scored by the rough dollar value of contained metal per tonne, using
# approximate market prices. This ranks by real economics without preferring any
# commodity: a 10 g/t Au or 15% Zn body scores high, while a marginal 4.75% Zn
# showing or a barren iron/sulphur (pyrite) mass scores near zero. Bulk/industrial
# commodities that aren't exploration-staking targets (Fe, S, Mn) are valued at 0,
# and byproducts with no price (Ga, Ge, Cd…) simply don't contribute.
# Value contribution per reported grade unit:  %-metal -> $/t = pct * 10 * $/kg ;
# g/t-metal -> $/t = gpt * $/g.
_PRICE_PCT = {  # $ per tonne of rock, per 1% grade  (= 10 kg * $/kg)
    "cu": 95.0, "pb": 21.0, "zn": 29.0, "ni": 160.0, "co": 330.0, "mo": 400.0,
    "sn": 300.0, "w": 450.0, "wo3": 450.0, "sb": 150.0, "bi": 100.0,
    "u": 1800.0, "u3o8": 1800.0, "li": 80.0, "li2o": 80.0, "reo": 150.0,
    "treo": 150.0, "v": 80.0, "v2o5": 80.0,
    "fe": 0.0, "iro": 0.0, "iron": 0.0, "mn": 0.0, "s": 0.0,   # not staking targets
}
_PRICE_GPT = {"au": 75.0, "ag": 0.95, "pt": 33.0, "pd": 34.0}  # $ per tonne, per 1 g/t
VALUE_CAP = 400.0                                             # $/t that earns full grade credit
import re as _re
_GRADE_RE = _re.compile(r"([A-Za-z][A-Za-z0-9]{0,4})\s*([\d.]+)\s*(g/t|%)", _re.I)
_TONNES_RE = _re.compile(r"([\d.]+)\s*(kt|mt|gt|t)\b", _re.I)
_TMULT = {"t": 1.0, "kt": 1e3, "mt": 1e6, "gt": 1e9}


def grade_value(grade_str):
    """Approximate in-situ contained-metal value in $/tonne from a grade string."""
    if not grade_str or str(grade_str) == "nan":
        return 0.0
    val = 0.0
    for ab, num, unit in _GRADE_RE.findall(str(grade_str)):
        a = ab.lower()
        try:
            v = float(num)
        except ValueError:
            continue
        is_gpt = "g" in unit.lower()
        if is_gpt and a in _PRICE_GPT:
            val += v * _PRICE_GPT[a]
        elif (not is_gpt) and a in _PRICE_PCT:
            val += v * _PRICE_PCT[a]
        elif is_gpt and a in _PRICE_PCT:        # base metal reported in g/t -> to %
            val += (v / 10000.0) * _PRICE_PCT[a]
        elif (not is_gpt) and a in _PRICE_GPT:  # precious reported in % (rare) -> to g/t
            val += (v * 10000.0) * _PRICE_GPT[a]
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


def grades_from_text(text):
    """Compact grade string from capsule/assay prose, preferring deposit-scale
    (resource, then drill-intersection) grades over isolated grab samples — so a
    barren resource isn't flattered by a rich grab, and vice-versa. Returns e.g.
    'Zn 6.7%, Pb 1.55%, Ag 19.2g/t' or '' when nothing usable is found."""
    t = str(text or "")
    if not t or t == "nan":
        return ""
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
    chosen = tiers[1] or tiers[2] or tiers[3]
    if not chosen:
        return ""
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
    return ", ".join(out)


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


def score_lead(status, deposit_open, grade_str="", tonnes_str="", has_drill=False, spend=0):
    """0-100 opportunity score, driven by GRADE QUALITY + DEPOSIT SIZE.

    Quality-first and commodity-agnostic: a marginal, tiny past-producer no
    longer tops the list just for ticking boxes. Grade (0-26) and size (0-16)
    are the dominant levers; status/open-ground/drilling/spend de-risk on top."""
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
    gv = grade_value(grade_str)                          # in-situ $/t of contained metal
    s += round(min(gv / VALUE_CAP, 1.0) * 26)            # 0..26  — the main lever
    # size only counts when the rock is actually worth something: a huge barren
    # (iron/sulphur) or unknown-grade tonnage earns no size credit.
    size_factor = 0.0 if gv < 25 else min((gv - 25) / 50.0, 1.0)
    s += round(size_quality(parse_tonnes(tonnes_str)) * size_factor)   # 0..16, grade-gated
    s += spend_points(spend)                             # 0..20
    return min(s, 100)
