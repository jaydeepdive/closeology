"""Ingest provincial/territorial government GEOCHEMISTRY sample databases (rock,
soil, silt and drill-core samples with multi-element assays) into the MMP store.

These ArcGIS layers hold hundreds of thousands of samples, each with a location
and a wide row of element concentrations (AU_PPB, AG_PPM, CU_PPM, FE_PCT, ...).
Element columns are auto-detected from the ELEMENT_UNIT naming, so adding a
source is a config entry, not new code. Samples are point analyses (surface or
core), stored as located "collars" (no depth) plus one assay row per element —
the same schema the drill data uses, so MineModelingPro sees one unified assay
table. No OCR, and it runs headless (government ArcGIS isn't IP-throttled).

Run:  python -m minemodelingpro.gov_samples            # all sources
      python -m minemodelingpro.gov_samples yk_geochem --limit 5000
"""
import sys
import re
import datetime

import arcgis_common
from minemodelingpro import store

# element_unit column -> (element symbol, unit). Units kept native (ppb/ppm/%).
_ELEM_COL = re.compile(r"^([A-Za-z]{1,3})[_ ]?(PPB|PPM|PCT|PERCENT|GT|G_T|PPT|OZ)$", re.I)
_ELEM_OK = {"au", "ag", "cu", "pb", "zn", "ni", "co", "mo", "as", "sb", "bi", "w", "sn",
            "u", "th", "li", "be", "cr", "v", "mn", "fe", "ti", "al", "mg", "ca", "na",
            "k", "p", "s", "ba", "sr", "rb", "cs", "ga", "ge", "se", "te", "cd", "tl",
            "hg", "re", "pt", "pd", "au", "ce", "la", "nd", "y", "sc", "zr", "nb", "ta",
            "hf", "sn", "in", "b", "cl", "br", "f"}
_UNIT = {"ppb": "ppb", "ppm": "ppm", "pct": "%", "percent": "%", "gt": "g/t", "g_t": "g/t",
         "ppt": "ppt", "oz": "oz/t"}


def _num(v):
    if v in (None, "", " "):
        return None
    s = str(v).strip().lstrip("<>~=")            # detection-limit markers
    try:
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return None


SOURCES = {
    "yk_geochem": {
        "name": "Yukon Assessment Report Geochemistry",
        "jurisdiction": "Yukon",
        "layer": "https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Geological/MapServer/1",
        "id_fields": ["SAMPLE_NAME", "OBJECTID"], "type_field": "SAMPLE_TYPE",
        "report_field": "REPORT_NUMBER", "method_field": "ANALYTICAL_METHOD_FAMILY",
        "lat_field": "LATITUDE_DD", "lon_field": "LONGITUDE_DD",
    },
    "yk_rgs": {
        "name": "Yukon Regional Geochemical Surveys (RGS) - All",
        "jurisdiction": "Yukon",
        "layer": "https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Geological/MapServer/41",
        "id_fields": ["SAMPLE_NUMBER", "SAMPLE_NAME", "OBJECTID"], "type_field": "SAMPLE_TYPE",
        "report_field": None, "method_field": None,
        "lat_field": "LATITUDE_DD", "lon_field": "LONGITUDE_DD",
    },
}


def _element_cols(feats):
    """Detect element columns present (with data) in the returned features."""
    if not feats:
        return []
    props = feats[0].get("properties", {}) or {}
    cols = []
    for name in props:
        m = _ELEM_COL.match(name)
        if not m:
            continue
        el, unit = m.group(1).lower(), m.group(2).lower()
        if el in _ELEM_OK and unit in _UNIT:
            cols.append((name, el.title(), _UNIT[unit]))
    return cols


def ingest_source(key, cfg=None, limit=None):
    cfg = cfg or SOURCES[key]
    sid = f"gov_geo:{key}"
    where = "1=1"
    print(f"[geo:{key}] fetching {cfg['name']} …")
    feats = arcgis_common.fetch_layer(cfg["layer"], out_fields="*", where=where, geom=True)
    if limit:
        feats = feats[:limit]
    ecols = _element_cols(feats)
    print(f"[geo:{key}] {len(feats)} samples, {len(ecols)} element columns "
          f"({', '.join(e for _, e, _ in ecols[:12])}{'…' if len(ecols) > 12 else ''})")
    collars, assays, seen = [], [], set()
    for ft in feats:
        p = ft.get("properties", {}) or {}
        g = ft.get("geometry") or {}
        lon = lat = None
        if g.get("type") == "Point" and g.get("coordinates"):
            lon, lat = g["coordinates"][0], g["coordinates"][1]
        if lat is None:
            lat = _num(p.get(cfg.get("lat_field")))
            lon = _num(p.get(cfg.get("lon_field")))
        native = None
        for f in cfg["id_fields"]:
            if p.get(f) not in (None, "", " "):
                native = str(p[f]); break
        if not native:
            continue
        uid = f"{sid}:{native}"
        if uid in seen:
            continue
        seen.add(uid)
        collars.append({
            "hole_uid": uid, "source_id": sid, "native_id": native, "company": None,
            "project": p.get(cfg.get("report_field")) or None,
            "jurisdiction": cfg["jurisdiction"], "lat": lat, "lon": lon,
            "easting": None, "northing": None, "utm_zone": None, "utm_hemi": None, "datum": None,
            "elev_m": None, "azimuth": None, "dip": None, "depth_m": None, "year_drilled": None,
            "has_assay": 1, "assay_flags": p.get(cfg.get("type_field")) or None,
            "report_ref": p.get(cfg.get("report_field")) or None, "url": cfg["layer"]})
        for col, el, unit in ecols:
            val = _num(p.get(col))
            if val is None:
                continue
            assays.append({"source_id": sid, "hole_uid": uid, "native_id": native,
                           "from_m": None, "to_m": None, "length_m": None,
                           "element": el, "grade": val, "unit": unit, "is_subinterval": 0})
    con = store.connect()
    store.replace_collars(con, sid, collars)
    store.replace_assays(con, sid, assays)
    store.record_source(con, {
        "id": sid, "kind": "gov_geochem", "name": cfg["name"], "url": cfg["layer"],
        "jurisdiction": cfg["jurisdiction"],
        "pulled_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_collars": len(collars), "n_assays": len(assays),
        "note": f"{len(ecols)} elements; point geochem samples"})
    con.commit()
    s = store.stats(con)
    con.close()
    print(f"[geo:{key}] {len(collars)} samples, {len(assays)} element assays | store now "
          f"{s['collars']} collars, {s['assays']} assays")
    return len(assays)


def run(keys=None, limit=None):
    total = 0
    for k in (keys or list(SOURCES)):
        try:
            total += ingest_source(k, limit=limit)
        except Exception as e:
            print(f"[geo:{k}] FAILED: {str(e)[:160]}")
    return total


if __name__ == "__main__":
    a = sys.argv[1:]
    lim = int(a[a.index("--limit") + 1]) if "--limit" in a else None
    keys = [x for x in a if not x.startswith("--") and (a.index(x) == 0 or a[a.index(x) - 1] != "--limit")]
    run(keys or None, limit=lim)
