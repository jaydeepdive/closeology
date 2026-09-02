"""Per-lead narrative extras for the priority page:
  • value_parts(grade_str)      -> ($/t total, [[metal, $/t], ...]) so the value
                                    number is broken down by which metals make it.
  • top_intercepts(text, n)     -> the best n drill intercepts (grade x width).
  • production_summary(...)      -> a short past-production summary from capsule text.
All commodity-agnostic and priced off config.PRICE_KG.
"""
import re
import config as C

_ABBR_NAME = {
    "au": "Gold", "ag": "Silver", "pt": "Platinum", "pd": "Palladium", "cu": "Copper",
    "pb": "Lead", "zn": "Zinc", "ni": "Nickel", "co": "Cobalt", "mo": "Molybdenum",
    "w": "Tungsten", "wo3": "Tungsten", "sn": "Tin", "sb": "Antimony", "bi": "Bismuth",
    "u": "Uranium", "u3o8": "Uranium", "li": "Lithium", "li2o": "Lithium", "v": "Vanadium",
    "v2o5": "Vanadium", "ga": "Gallium", "ge": "Germanium", "in": "Indium", "te": "Tellurium",
    "nd": "Neodymium", "pr": "Praseodymium", "dy": "Dysprosium", "tb": "Terbium",
    "fe": "Iron", "s": "Sulphur", "mn": "Manganese", "cd": "Cadmium",
}


def _dollars(ab, v, is_gpt):
    price = C.PRICE_KG.get(ab)
    if not price:
        return 0.0
    if ab in C._PRECIOUS:
        gpt = v * 10000.0 if not is_gpt else v
        if gpt < C.GRADE_FLOOR.get(ab, 0):
            return 0.0
        return (gpt / 1000.0) * price
    pct = v / 10000.0 if is_gpt else v
    if pct < C.GRADE_FLOOR.get(ab, 0):
        return 0.0
    return (pct * 10.0) * price


def sort_grade_by_value(grade_str):
    """Reorder the compact grade string so the highest-$/t metal comes first — the
    primary asset is then obvious at a glance. Priced metals (desc by $/t) lead;
    trace/unpriced tokens keep their text at the end. Returns (sorted_str, top_metal)."""
    s = str(grade_str or "")
    if not s or s == "nan":
        return "", ""
    toks = []
    for m in C._GRADE_RE.finditer(s):
        ab, num, unit = m.group(1), m.group(2), m.group(3)
        try:
            v = float(num)
        except ValueError:
            v = 0.0
        d = _dollars(ab.lower(), v, "g" in unit.lower())
        toks.append((d, m.group(0).strip(), _ABBR_NAME.get(ab.lower(), ab.title())))
    if not toks:
        return s, ""
    toks.sort(key=lambda t: -t[0])
    top = toks[0][2] if toks[0][0] > 0 else ""
    return ", ".join(t[1] for t in toks), top


def value_parts(grade_str):
    """Return (total $/t, [[metal_name, $/t], ...] desc) from a compact grade str."""
    if not grade_str or str(grade_str) == "nan":
        return 0.0, []
    parts = {}
    for ab, num, unit in C._GRADE_RE.findall(str(grade_str)):
        a = ab.lower()
        try:
            v = float(num)
        except ValueError:
            continue
        d = _dollars(a, v, "g" in unit.lower())
        if d > 0:
            parts[a] = parts.get(a, 0.0) + d
    total = sum(parts.values())
    lst = sorted(([_ABBR_NAME.get(a, a.title()), round(x)] for a, x in parts.items()),
                 key=lambda p: -p[1])
    return total, lst


_OVER = re.compile(r"(?:over|across)\s*(\d+\.?\d*)\s*(?:m|metre|meter)s?\b", re.I)


def top_intercepts(text, n=3):
    """Best n drill intercepts by contained value (in-situ $/t x width)."""
    t = str(text or "")
    if not t or t == "nan":
        return []
    found = []
    for m in _OVER.finditer(t):
        try:
            width = float(m.group(1))
        except ValueError:
            continue
        win = t[max(0, m.start() - 115):m.start()]
        cut = max(win.rfind(". "), win.rfind("; "), win.rfind(" including "), win.rfind("Hole "))
        if cut > 0:
            win = win[cut:]
        grades, dollars = [], 0.0
        for gm in C._GRADE_TEXT_RE.finditer(win):
            num, unitword, word = gm.group(1), gm.group(2), gm.group(3).lower()
            ab = C._METAL_WORD.get(word)
            if not ab:
                continue
            try:
                v = float(num)
            except ValueError:
                continue
            cv, cu = C._canon_grade(v, unitword, ab)
            d = _dollars(ab, cv, cu == "gpt")
            if d <= 0:
                continue
            disp = f"{cv:.1f} g/t {_ABBR_NAME.get(ab, ab.title())}" if cu == "gpt" \
                else f"{cv:.2f}% {_ABBR_NAME.get(ab, ab.title())}"
            grades.append(disp)
            dollars += d
        if grades and dollars > 0:
            found.append({"width": width, "grades": grades, "vpt": dollars,
                          "score": dollars * width})
    found.sort(key=lambda x: -x["score"])
    seen, out = set(), []
    for f in found:
        key = (round(f["width"], 1), tuple(f["grades"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": f"{f['width']:g} m: " + ", ".join(f["grades"]),
                    "vpt": round(f["vpt"])})
        if len(out) >= n:
            break
    return out


_PROD_T = re.compile(
    r"([^.]*\b(?:produc\w+|milled|mined|recovered|yielded|production)\b[^.]*?"
    r"\d[\d,]+\s*(?:tonnes?|tons?)[^.]*)\.", re.I)
_YEARS = re.compile(
    r"(?:operat\w+|mined|production|worked)[^.]*?((?:18|19|20)\d\d)\s*"
    r"(?:to|and|until|[-–])\s*((?:18|19|20)\d\d)", re.I)
_BETWEEN = re.compile(r"between\s+((?:18|19|20)\d\d)\s+and\s+((?:18|19|20)\d\d)", re.I)


def production_summary(capsule, drill, status=""):
    if "past" not in str(status).lower() and "produc" not in str(status).lower():
        return ""
    txt = str(capsule or "") + " " + str(drill or "")
    m = _PROD_T.search(txt)
    if m:
        s = re.sub(r"\s+", " ", m.group(1)).strip()
        return (s[:230] + "…") if len(s) > 230 else s + "."
    y = _YEARS.search(txt) or _BETWEEN.search(txt)
    if y:
        return f"Operated {y.group(1)}–{y.group(2)}; production tonnage not in the public capsule."
    return "Past-producing mine; production figures not captured in the public capsule."
