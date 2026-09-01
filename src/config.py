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


def score_lead(status, deposit_open, has_grade, has_tonnes, n_metals, produced_tonnes,
               has_drill=False, spend=0):
    """0-100 prospectivity-of-opportunity score (BC + Ontario vocabularies).

    Balanced so a region isn't penalised for a data field the other province
    happens to publish: grade/tonnage (strong in BC's MINFILE) and drill data
    (strong in Ontario's OGS drill database) each contribute, and any one of
    them signals a de-risked target."""
    s = 0
    st = (status or "").lower()
    if "past" in st and "produc" in st: s += 34       # past producer / past producing mine
    elif "produc" in st: s += 40                        # producer / producing mine
    elif "developed" in st: s += 26                     # developed prospect
    elif "deposit" in st: s += 24
    elif "prospect" in st: s += 20
    elif "discovery" in st: s += 16
    elif "showing" in st: s += 10
    elif "occurrence" in st: s += 8
    else: s += 6
    if deposit_open: s += 24
    if has_drill: s += 12                                # documented drilling (OGS holes / MINFILE capsule)
    if has_tonnes: s += 8
    if has_grade: s += 8
    s += min(n_metals, 4) * 2
    if produced_tonnes and produced_tonnes > 1e6: s += 8
    elif produced_tonnes and produced_tonnes > 1e5: s += 4
    s += spend_points(spend)
    return min(s, 100)
