"""Ingest provincial/territorial government drill-hole databases (the collar
backbone) into the MMP store via their public ArcGIS REST services.

These government layers give the authoritative COLLAR record — location,
orientation, depth, year, and (for most) a reference to the assessment report
that holds the full assays. They rarely carry the assay VALUES themselves; those
are chased separately from assessment files / NI 43-101 reports
(minemodelingpro.pdf_assays). Together: this places every historical hole on the
map and tells us which ones have assays worth extracting.

Each SOURCE is a config mapping the layer's native fields to the common collar
schema, so adding a province is a dict entry, not new code. Re-running is
idempotent (replace-by-source).

Run:  python -m minemodelingpro.gov_drillholes            # all sources
      python -m minemodelingpro.gov_drillholes nb sk      # selected
"""
import sys
import datetime

import arcgis_common
from minemodelingpro import store


def _f(props, *names):
    """First present, non-empty attribute among candidate field names."""
    for n in names:
        if n in props and props[n] not in (None, "", " "):
            return props[n]
    return None


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _year(v):
    n = _num(v)
    if n is None:
        s = str(v or "")
        for tok in s.replace("-", " ").replace("/", " ").split():
            if tok.isdigit() and len(tok) == 4:
                return int(tok)
        return None
    return int(n) if 1800 < n < 2100 else None


# --- source registry -------------------------------------------------------
# assay_flag_fields: {ELEMENT: field} where a truthy/"Y" value flags the element
# was assayed (presence only). id_fields/az_fields/etc: candidate native columns.
SOURCES = {
    "nb": {
        "name": "New Brunswick NBGS Exploration Drillholes",
        "jurisdiction": "New Brunswick",
        "layer": "https://gis-erd-der.gnb.ca/server/rest/services/OpenData/NBGS_Exploration_Drillholes/MapServer/0",
        "id_fields": ["LABEL", "GRIDSTATION", "OBJECTID"],
        "az_fields": ["AZIMUTHTRUE"], "dip_fields": ["DIP"],
        "depth_fields": ["LENGTH_M"], "year_fields": ["YEARDRILLED"],
        "company_fields": [], "project_fields": ["GRIDNAME", "LOCATIONMAP"],
        "report_fields": ["REPT_NO"], "assay_flag_fields": {},
    },
    "sk": {
        "name": "Saskatchewan Mineral Exploration — Drillholes",
        "jurisdiction": "Saskatchewan",
        "layer": "https://gis.saskatchewan.ca/arcgis/rest/services/Economy/Mineral_Exploration/MapServer/3",
        "id_fields": ["GOS_UNIQUE_DRILLHOLE_ID", "DRILLHOLE_NAME", "OBJECTID"],
        "az_fields": ["DH_AZIMUTH"], "dip_fields": ["DH_INCLINATION"],
        "depth_fields": ["TOTAL_DH_LENGTH_M"], "year_fields": ["DATE_DRILLED"],
        "elev_fields": ["ORIGINAL_COLLAR_ELEVATION_M", "ELEV_CORRECTED_1ARCSEC_DEM_M"],
        "company_fields": ["COMPANY"], "project_fields": ["PROJECT_OR_PROPERTY_NAME"],
        "report_fields": ["SOURCE"], "commodity_fields": ["COMMODITY_OF_INTEREST"],
        "assay_flag_fields": {},
    },
}


def ingest_source(key, cfg=None):
    cfg = cfg or SOURCES[key]
    sid = f"gov:{key}"
    print(f"[gov:{key}] fetching {cfg['name']} …")
    feats = arcgis_common.fetch_layer(cfg["layer"], out_fields="*", geom=True)
    rows, seen = [], set()
    for ft in feats:
        p = ft.get("properties", {}) or {}
        g = ft.get("geometry") or {}
        lon = lat = None
        if g.get("type") == "Point" and g.get("coordinates"):
            lon, lat = g["coordinates"][0], g["coordinates"][1]
        native = _f(p, *cfg["id_fields"])
        native = str(native) if native is not None else None
        if not native:
            continue
        uid = f"{sid}:{native}"
        if uid in seen:                       # keep first; some layers repeat ids per sample
            continue
        seen.add(uid)
        flags = [el for el, fld in cfg.get("assay_flag_fields", {}).items()
                 if str(p.get(fld, "")).strip().upper() in ("Y", "YES", "1", "TRUE")]
        rows.append({
            "hole_uid": uid, "source_id": sid, "native_id": native,
            "company": _f(p, *cfg.get("company_fields", [])),
            "project": _f(p, *cfg.get("project_fields", [])),
            "jurisdiction": cfg["jurisdiction"],
            "lat": lat, "lon": lon,
            "easting": None, "northing": None, "utm_zone": None, "utm_hemi": None,
            "datum": "NAD83",
            "elev_m": _num(_f(p, *(cfg.get("elev_fields") or ["ELEVATION", "ELEV", "ELEV_M", "RL"]))),
            "azimuth": _num(_f(p, *cfg.get("az_fields", []))),
            "dip": _num(_f(p, *cfg.get("dip_fields", []))),
            "depth_m": _num(_f(p, *cfg.get("depth_fields", []))),
            "year_drilled": _year(_f(p, *cfg.get("year_fields", []))),
            "has_assay": 1 if flags else 0,
            "assay_flags": ",".join(flags) if flags else None,
            "report_ref": _f(p, *cfg.get("report_fields", [])),
            "url": cfg.get("record_url"),
        })
    con = store.connect()
    store.replace_collars(con, sid, rows)
    store.record_source(con, {
        "id": sid, "kind": "gov_drillholes", "name": cfg["name"], "url": cfg["layer"],
        "jurisdiction": cfg["jurisdiction"],
        "pulled_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_collars": len(rows), "n_assays": 0,
        "note": f"{sum(1 for r in rows if r['lat'] is not None)} located"})
    con.commit()
    s = store.stats(con)
    con.close()
    print(f"[gov:{key}] {len(rows)} collars ingested | store now {s['collars']} collars "
          f"({s['collars_located']} located) across {s['sources']} sources")
    return len(rows)


def run(keys=None):
    keys = keys or list(SOURCES)
    total = 0
    for k in keys:
        try:
            total += ingest_source(k)
        except Exception as e:
            print(f"[gov:{k}] FAILED: {str(e)[:160]}")
    return total


if __name__ == "__main__":
    run(sys.argv[1:] or None)
