"""Deterministic drill-collar + assay extraction from press-release HTML.

No LLM: this parses the structured tables and the common prose intercept
patterns that mining news releases use. Anything it can't read is reported so
run.py can log the release as a failure (for a later LLM pass).

Returns (holes, intervals, meta):
  holes     : [{hole_id, easting, northing, elev_m, azimuth, dip, depth_m}]
  intervals : [{hole_id, from_m, to_m, length_m, element, grade, unit,
                is_subinterval, raw}]
  meta      : {utm_zone, utm_hemi, datum, has_tables}
Coordinates are left in easting/northing here; geolocate.py adds lat/lon.
"""
import re
import html as _html

# ---- element name/symbol handling -----------------------------------------
_ELEMENTS = {
    "au": "Au", "gold": "Au", "ag": "Ag", "silver": "Ag", "cu": "Cu", "copper": "Cu",
    "pb": "Pb", "lead": "Pb", "zn": "Zn", "zinc": "Zn", "ni": "Ni", "nickel": "Ni",
    "co": "Co", "cobalt": "Co", "mo": "Mo", "molybdenum": "Mo", "sn": "Sn", "tin": "Sn",
    "w": "W", "wo3": "WO3", "tungsten": "W", "u": "U", "u3o8": "U3O8", "uranium": "U",
    "li": "Li", "li2o": "Li2O", "lithium": "Li", "sb": "Sb", "antimony": "Sb",
    "bi": "Bi", "v": "V", "v2o5": "V2O5", "vanadium": "V", "mn": "Mn", "manganese": "Mn",
    "fe": "Fe", "iron": "Fe", "cr": "Cr", "pt": "Pt", "pd": "Pd", "rh": "Rh",
    "reo": "REO", "treo": "TREO", "nb": "Nb", "ta": "Ta", "graphite": "Cg", "cg": "Cg",
    "aueq": "AuEq", "ageq": "AgEq", "cueq": "CuEq", "nieq": "NiEq",
}
_HOLE_HDR = ("hole", "ddh", "bhid", "drillhole", "borehole")
_FROM_HDR = ("from",)
_TO_HDR = ("to",)
_LEN_HDR = ("length", "width", "interval", "core length", "etw", "true width", "thickness")
_EAST_HDR = ("easting", "utm e", "utm_e", "utme", "east ")
_NORTH_HDR = ("northing", "utm n", "utm_n", "utmn", "north ")
_LAT_HDR = ("latitude", "lat ", "lat(", "lat")
_LON_HDR = ("longitude", "long ", "lon ", "long(", "lon(", "lon", "long")
_AZ_HDR = ("azimuth", "azi", "bearing")
_DIP_HDR = ("dip", "inclination", "incl")
_DEPTH_HDR = ("depth", "eoh", "total depth", "hole length", "final depth", "td (m)", "length (m)")
_ELEV_HDR = ("elev", "elevation", "rl", "collar elev")

_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = _html.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("%", "").strip()
    m = _NUM.match(s.lstrip("~<>= "))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def tables(html):
    out = []
    for t in re.findall(r"<table\b.*?</table>", html, re.S | re.I):
        rows = []
        for tr in re.findall(r"<tr\b.*?</tr>", t, re.S | re.I):
            cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, re.S | re.I)
            rows.append([_clean(c) for c in cells])
        rows = [r for r in rows if any(c for c in r)]
        if len(rows) >= 2:
            out.append(rows)
    return out


def _hdr_index(header, needles):
    for i, h in enumerate(header):
        hl = h.lower()
        if any(n in hl for n in needles):
            return i
    return None


def _element_of(header_cell):
    """'Au (g/t)' -> ('Au','g/t'); 'Pb (%)' -> ('Pb','%'); else (None,None)."""
    h = header_cell.strip()
    m = re.search(r"\(([^)]*)\)", h)
    unit = None
    if m:
        u = m.group(1).strip().lower()
        if u in ("g/t", "gpt", "g/tonne"):
            unit = "g/t"
        elif u in ("%", "pct", "percent"):
            unit = "%"
        elif u in ("ppm",):
            unit = "ppm"
        elif u in ("oz/t", "opt", "oz/ton"):
            unit = "oz/t"
        elif "%" in u:
            unit = "%"
        elif "g/t" in u:
            unit = "g/t"
    base = re.sub(r"\(.*?\)", "", h).strip().lower()
    base = base.replace(" ", "")
    sym = _ELEMENTS.get(base)
    if sym and unit:
        return sym, unit
    # bare 'Au' with no unit but numeric column -> assume g/t later
    if sym and not unit:
        return sym, ("%" if sym in ("Cu", "Pb", "Zn", "Ni", "Co", "Fe", "Mn") else "g/t")
    return None, None


def _looks_collar(header):
    has_e = _hdr_index(header, _EAST_HDR) is not None
    has_n = _hdr_index(header, _NORTH_HDR) is not None
    has_lat = _hdr_index(header, _LAT_HDR) is not None
    has_lon = _hdr_index(header, _LON_HDR) is not None
    return (has_e and has_n) or (has_lat and has_lon)


def _parse_collar(rows):
    header = rows[0]
    hi = _hdr_index(header, _HOLE_HDR)
    ei = _hdr_index(header, _EAST_HDR); ni = _hdr_index(header, _NORTH_HDR)
    lai = _hdr_index(header, _LAT_HDR); loi = _hdr_index(header, _LON_HDR)
    azi = _hdr_index(header, _AZ_HDR); di = _hdr_index(header, _DIP_HDR)
    dpi = _hdr_index(header, _DEPTH_HDR); eli = _hdr_index(header, _ELEV_HDR)
    holes = []
    for r in rows[1:]:
        if hi is None or hi >= len(r):
            continue
        hole_id = r[hi].strip()
        if not hole_id or hole_id.lower() in ("hole", "hole id", "including", "incl"):
            continue

        def g(idx):
            return _num(r[idx]) if idx is not None and idx < len(r) else None
        h = {"hole_id": hole_id, "easting": g(ei), "northing": g(ni),
             "lat": g(lai), "lon": g(loi), "azimuth": g(azi), "dip": g(di),
             "depth_m": g(dpi), "elev_m": g(eli)}
        # a collar row must actually carry a position
        if h["easting"] or h["northing"] or h["lat"] or h["lon"]:
            holes.append(h)
    return holes


def _parse_assay(rows):
    header = rows[0]
    hi = _hdr_index(header, _HOLE_HDR)
    fi = _hdr_index(header, _FROM_HDR); ti = _hdr_index(header, _TO_HDR)
    li = _hdr_index(header, _LEN_HDR)
    if fi is None or ti is None:
        return []
    elem_cols = []
    for idx, cell in enumerate(header):
        sym, unit = _element_of(cell)
        if sym:
            elem_cols.append((idx, sym, unit))
    if not elem_cols:
        return []
    ncol = len(header)
    out = []
    last_hole = None
    for r in rows[1:]:
        if not any(r):
            continue
        # messy tables emit extra empty cells (rowspan artifacts) that shift the
        # columns; drop interior/trailing blanks until the row lines up
        if len(r) > ncol:
            keep = list(r)
            i = 1
            while len(keep) > ncol and i < len(keep):
                if keep[i] == "":
                    keep.pop(i)
                else:
                    i += 1
            r = keep[:ncol] if len(keep) >= ncol else keep
        first = (r[hi].strip() if hi is not None and hi < len(r) else "")
        sub = False
        hole = last_hole
        fl = first.lower()
        if fl in ("including", "incl", "incl.", "and", "and including"):
            sub = True
        elif first:
            hole = first
            last_hole = hole
        frm = _num(r[fi]) if fi < len(r) else None
        to = _num(r[ti]) if ti < len(r) else None
        length = _num(r[li]) if (li is not None and li < len(r)) else (
            round(to - frm, 2) if (frm is not None and to is not None) else None)
        if frm is None and to is None:
            continue
        for idx, sym, unit in elem_cols:
            if idx >= len(r):
                continue
            g = _num(r[idx])
            if g is None:
                continue
            out.append({"hole_id": hole, "from_m": frm, "to_m": to, "length_m": length,
                        "element": sym, "grade": g, "unit": unit, "is_subinterval": sub,
                        "raw": " | ".join(r)[:300]})
    return out


# ---- prose intercept patterns (releases with no tables) --------------------
# a length phrase ("N.N metres [ETW/core/grading] ...") that opens an intercept,
# optionally introduced by "including"/"incl" (a sub-interval of the prior one)
_LEN_LEAD = re.compile(
    r"(including|incl\.?|and)?\s*(\d+\.?\d*)\s*(?:m|metre?s?|meters?)\b"
    r"[A-Za-z ()/.\-]{0,20}?(?:of|grading|averaging|returning|@|at|assay\w*)\s+", re.I)
# a single "grade unit element" token, e.g. "13.49 g/t Au" / "6.5 % Zn"
_GRADE_TOK = re.compile(r"(\d[\d,]*\.?\d*)\s*(g/t|%|ppm|gpt|oz/t)\s*([A-Za-z][A-Za-z0-9]{0,4})", re.I)
# reversed form: "48.04 g/t Au over 12.62 m"
_REV = re.compile(r"(\d[\d,]*\.?\d*)\s*(g/t|%|ppm|gpt|oz/t)\s*([A-Za-z][A-Za-z0-9]{0,4})\s+over\s+(\d+\.?\d*)\s*(?:m|metre?s?|meters?)", re.I)


def _norm_unit(u):
    return {"gpt": "g/t"}.get(u.lower(), u.lower())


def _parse_prose(text):
    out = []
    # forward: length lead, then every grade token in the ~90 chars that follow
    for m in _LEN_LEAD.finditer(text):
        sub = bool(m.group(1))
        length = _num(m.group(2))
        tail = text[m.end():m.end() + 90]
        # stop the tail at the next length-lead so we don't bleed into the next hole
        nxt = _LEN_LEAD.search(tail)
        if nxt:
            tail = tail[:nxt.start()]
        for g in _GRADE_TOK.finditer(tail):
            sym = _ELEMENTS.get(g.group(3).lower())
            if not sym:
                continue
            out.append({"hole_id": None, "from_m": None, "to_m": None, "length_m": length,
                        "element": sym, "grade": _num(g.group(1)), "unit": _norm_unit(g.group(2)),
                        "is_subinterval": sub, "raw": _clean(m.group(0) + tail)[:200]})
    for m in _REV.finditer(text):
        sym = _ELEMENTS.get(m.group(3).lower())
        if not sym:
            continue
        out.append({"hole_id": None, "from_m": None, "to_m": None, "length_m": _num(m.group(4)),
                    "element": sym, "grade": _num(m.group(1)), "unit": _norm_unit(m.group(2)),
                    "is_subinterval": False, "raw": _clean(m.group(0))[:200]})
    seen, uniq = set(), []
    for o in out:
        k = (o["length_m"], o["element"], o["grade"], o["unit"], o["is_subinterval"])
        if k not in seen:
            seen.add(k); uniq.append(o)
    return uniq


def _detect_zone(text):
    """UTM zone/hemisphere + datum from the release text."""
    zone = hemi = datum = None
    m = re.search(r"UTM[^.]{0,40}?Zone\s*(\d{1,2})\s*([NnSs])?", text)
    if not m:
        m = re.search(r"\bZone\s*(\d{1,2})\s*([NnSs])\b", text)
    if m:
        zone = int(m.group(1))
        if m.group(2):
            hemi = m.group(2).upper()
    dm = re.search(r"(NAD\s?83|NAD\s?27|WGS\s?84)", text, re.I)
    if dm:
        datum = dm.group(1).upper().replace(" ", "")
    return zone, hemi, datum


def extract(html):
    text = _clean(html)
    holes, intervals = [], []
    tbls = tables(html)
    for rows in tbls:
        if _looks_collar(rows[0]):
            holes.extend(_parse_collar(rows))
        else:
            intervals.extend(_parse_assay(rows))
    if not intervals and not holes:
        intervals = _parse_prose(text)
    zone, hemi, datum = _detect_zone(text)
    # default northern hemisphere for Canadian coords when unstated
    if zone and not hemi:
        hemi = "N"
    meta = {"utm_zone": zone, "utm_hemi": hemi, "datum": datum, "has_tables": bool(tbls)}
    return holes, intervals, meta
