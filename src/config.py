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
