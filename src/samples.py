"""Turn a table of individual assay samples (occurrence, element, concentration,
unit, sample-type) into a per-occurrence grade string + confidence, exactly like
the Quebec SIGEOM sample table. Shared by any jurisdiction that publishes assay
records (Nova Scotia ANALYSES table, etc.)."""
import re

_PRECIOUS = {"Au", "Ag", "Pt", "Pd", "Rh", "Ir", "Os", "Ru"}
# elements worth surfacing as a grade (targets); everything else (major-element
# oxides, pathfinders like As/Hg, rock-formers) is ignored.
TARGET = {"Au", "Ag", "Pt", "Pd", "Cu", "Pb", "Zn", "Ni", "Co", "Mo", "Sn", "W",
          "Sb", "Bi", "U", "Li", "Ta", "Nb", "Be", "V", "Cr", "Mn", "Re", "Te",
          "In", "Ga", "Ge", "Cd", "Sc", "Cs", "Rb"}

# sample-type keyword -> grade confidence (drill/ore high, grab/float low)
_TYPE_CONF = [
    ("drill", 0.8), ("core", 0.8), ("ddh", 0.8),
    ("channel", 0.7), ("chip", 0.7), ("trench", 0.7),
    ("ore", 0.9), ("bulk", 0.85), ("composite", 0.75),
    ("grab", 0.5), ("float", 0.45), ("boulder", 0.45), ("specimen", 0.4),
]


def type_conf(sam_type):
    s = str(sam_type or "").lower()
    for k, v in _TYPE_CONF:
        if k in s:
            return v
    return 0.5


def _clean_elem(e):
    """'Au' -> 'Au'; 'Cu (ppm)' -> 'Cu'; oxide/compound -> None."""
    e = str(e or "").strip()
    m = re.match(r"^([A-Z][a-z]?)\b", e)
    if not m:
        return None
    sym = m.group(1)
    # drop oxide/compound reports (Al2O3, SiO2, Fe2O3, ...): those have digits/O after
    if re.match(r"^[A-Z][a-z]?\d", e) or "O3" in e or "O2" in e or "O5" in e:
        return None
    return sym


def grade_token(sym, val, unit):
    """(symbol, value, unit) -> 'Au 5.20 g/t' | 'Cu 1.08%'  (None if unusable)."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None, 0.0
    if v <= 0:
        return None, 0.0
    u = str(unit or "").lower().replace("wt.%", "%").replace("wt%", "%")
    if sym in _PRECIOUS:
        if "ppb" in u:
            gpt = v / 1000.0
        elif "%" in u:
            gpt = v * 10000.0
        else:                       # ppm, g/t, g/tonne ~ 1:1
            gpt = v
        return f"{sym} {gpt:.2f} g/t", gpt
    if "%" in u:
        pct = v
    elif "ppm" in u or "g/t" in u:
        pct = v / 1e4
    elif "ppb" in u:
        pct = v / 1e7
    else:
        pct = v
    return f"{sym} {pct:.3g}%", pct


def grades_by_occ(rows, value_fn):
    """rows: iterable of dicts with keys occ, elem, val, unit, conf, is_drill,
    below_dl. Returns {occ: (grade_str, grade_conf, has_drill)} keeping, per
    element, the best (value-ranked) real assay, preferring higher-confidence
    sample types. `value_fn(token)` -> $/t used only to order elements."""
    by = {}
    for r in rows:
        if r.get("below_dl"):
            continue
        sym = _clean_elem(r["elem"])
        if not sym or sym not in TARGET:
            continue
        tok, mag = grade_token(sym, r["val"], r["unit"])
        if not tok:
            continue
        occ = r["occ"]
        d = by.setdefault(occ, {"drill": False, "el": {}})
        if r.get("is_drill"):
            d["drill"] = True
        prev = d["el"].get(sym)
        # keep the higher-confidence sample; tie-break on higher grade magnitude
        cand = (r.get("conf", 0.5), mag, tok)
        if prev is None or cand[:2] > prev[:2]:
            d["el"][sym] = cand
    out = {}
    for occ, d in by.items():
        toks = []
        for sym, (conf, mag, tok) in d["el"].items():
            toks.append((value_fn(tok), conf, tok))
        if not toks:
            continue
        toks.sort(key=lambda x: -x[0])
        grade_str = ", ".join(t[2] for t in toks[:5])
        gconf = toks[0][1] if toks[0][0] > 0 else max(c for _, c, _ in toks)
        out[occ] = (grade_str, gconf, d["drill"])
    return out
