"""Turn drill-collar UTM easting/northing into lat/lon so holes can be placed on
the Closeology map and matched to open ground. Uses pyproj. When the UTM zone
isn't stated in the release we infer it from the easting/northing magnitude plus
a project-location hint if available; holes we still can't place keep null
lat/lon (their easting/northing is still banked for MineModelingPro)."""
from pyproj import Transformer

_EPSG_DATUM = {"NAD83": 269, "NAD27": 267, "WGS84": 326}  # +zone for N, 327-family for WGS84 S


def _epsg(zone, hemi, datum):
    datum = (datum or "WGS84").upper()
    hemi = (hemi or "N").upper()
    if datum.startswith("NAD83"):
        return 26900 + zone            # NAD83 UTM Nzone (northern only, N. America)
    if datum.startswith("NAD27"):
        return 26700 + zone
    # WGS84
    return (32600 if hemi == "N" else 32700) + zone


def utm_to_ll(easting, northing, zone, hemi="N", datum="WGS84"):
    try:
        tr = Transformer.from_crs(f"EPSG:{_epsg(zone, hemi, datum)}", "EPSG:4326", always_xy=True)
        lon, lat = tr.transform(easting, northing)
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            return round(lat, 6), round(lon, 6)
    except Exception:
        pass
    return None, None


# region -> (candidate UTM zones, bbox lat/lon min/max) so that when a release
# omits the zone we can still place a collar: try each candidate zone and keep
# the one whose lon/lat lands inside the stated jurisdiction.
_REGION = {
    "Ontario": ([15, 16, 17, 18], (41.6, 57.0, -95.2, -74.3)),
    "Quebec": ([17, 18, 19, 20, 21], (44.9, 62.6, -79.8, -57.1)),
    "Qu\xe9bec": ([17, 18, 19, 20, 21], (44.9, 62.6, -79.8, -57.1)),
    "British Columbia": ([8, 9, 10, 11], (48.2, 60.1, -139.1, -114.0)),
    "Yukon": ([7, 8, 9], (60.0, 69.7, -141.1, -123.8)),
    "Nunavut": ([13, 14, 15, 16, 17, 18], (51.0, 83.2, -110.0, -61.0)),
    "Northwest Territories": ([8, 9, 10, 11, 12], (60.0, 78.8, -136.5, -102.0)),
    "Saskatchewan": ([12, 13, 14], (49.0, 60.0, -110.0, -101.4)),
    "Manitoba": ([13, 14, 15], (49.0, 60.0, -102.1, -88.9)),
    "Newfoundland": ([20, 21, 22], (46.6, 60.4, -67.9, -52.6)),
    "Labrador": ([19, 20, 21], (51.3, 60.4, -67.0, -55.4)),
    "Nova Scotia": ([20], (43.3, 47.1, -66.5, -59.7)),
    "New Brunswick": ([19, 20], (44.5, 48.1, -69.1, -63.7)),
    "Alberta": ([11, 12], (48.9, 60.0, -120.0, -110.0)),
    "Nevada": ([11], (35.0, 42.1, -120.0, -114.0)),
    "Arizona": ([12], (31.3, 37.1, -114.9, -109.0)),
    "Nevada,": ([11], (35.0, 42.1, -120.0, -114.0)),
    "North Carolina": ([17], (33.8, 36.6, -84.4, -75.4)),
    "Alaska": ([1, 2, 3, 4, 5, 6, 7, 8, 9], (51.2, 71.5, -179.9, -129.9)),
}


def _infer(e, nth, region, datum, hemi="N"):
    info = _REGION.get(region)
    if not info:
        return None, None, None
    for z in info[0]:
        lat, lon = utm_to_ll(e, nth, z, hemi, datum or "NAD83")
        if lat is None:
            continue
        la0, la1, lo0, lo1 = info[1]
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return lat, lon, z
    return None, None, None


_PROV_CACHE = None


def _prov_polys():
    """Load the province/territory boundary polygons once (from data/keep) for
    accurate point-in-polygon region labelling."""
    global _PROV_CACHE
    if _PROV_CACHE is not None:
        return _PROV_CACHE
    _PROV_CACHE = []
    import glob
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        import geopandas as gpd
        for fp in glob.glob(os.path.join(root, "data", "keep", "*_boundary.parquet")):
            try:
                g = gpd.read_parquet(fp).to_crs(4326)
                name = str(g.iloc[0].get("name", os.path.basename(fp).split("_")[0]))
                geom = g.geometry.union_all() if hasattr(g.geometry, "union_all") else g.geometry.unary_union
                _PROV_CACHE.append((name, geom))
            except Exception:
                continue
    except Exception:
        _PROV_CACHE = []
    return _PROV_CACHE


def region_from_latlon(lat, lon):
    """Label a coordinate by the province/territory whose boundary actually
    contains it (real polygons, not bbox), so a geolocated hole is tagged by
    where it IS — not the company's head-office address in the boilerplate.
    Falls back to a coarse country label outside Canada."""
    if lat is None or lon is None:
        return None
    try:
        from shapely.geometry import Point
        p = Point(lon, lat)
        for name, geom in _prov_polys():
            if geom is not None and geom.contains(p):
                return name
    except Exception:
        pass
    if 41 <= lat <= 84 and -141 <= lon <= -52:
        return "Canada"
    if 24 <= lat <= 50 and -125 <= lon <= -66:
        return "USA"
    return "International"


_PROV_NAMES_CACHE = None
_CA_HINT = {"canada", "ontario", "quebec", "qu\xe9bec", "british columbia", "alberta",
            "saskatchewan", "manitoba", "yukon", "nunavut", "northwest territories",
            "nova scotia", "new brunswick", "prince edward island", "newfoundland",
            "newfoundland & labrador", "labrador"}


def _prov_names():
    global _PROV_NAMES_CACHE
    if _PROV_NAMES_CACHE is None:
        _PROV_NAMES_CACHE = {n for n, _ in _prov_polys()}
    return _PROV_NAMES_CACHE


def _in_province(lat, lon):
    return lat is not None and region_from_latlon(lat, lon) in _prov_names()


def _canada_hint(region):
    r = (region or "").strip().lower()
    return (not r) or r in _CA_HINT or "canada" in r or any(p in r for p in _CA_HINT if len(p) > 6)


def _snap_canada(e, nth, hemi, datum, region):
    """A guessed UTM zone can land a Canadian collar in a lake/ocean (no province
    matches). Try the plausible zones and keep the one that lands inside a real
    province — preferring the zones of the hinted region. Corrects wrong-zone
    guesses so the hole plots where it actually is."""
    if not _canada_hint(region):
        return None
    order = []
    info = _REGION.get(region)
    if info:
        order += info[0]
    order += list(range(7, 23))            # full Canadian UTM span
    seen, hits = set(), []
    for z in order:
        if z in seen:
            continue
        seen.add(z)
        lat, lon = utm_to_ll(e, nth, z, hemi or "N", datum or "NAD83")
        prov = region_from_latlon(lat, lon) if lat is not None else None
        if prov in _prov_names():
            hits.append((lat, lon, z, prov))
    if not hits:
        return None
    # prefer a hit whose province matches the text hint, else the first (zones
    # were tried hint-first, so the first hit is the most plausible)
    rlow = (region or "").lower()
    for lat, lon, z, prov in hits:
        if prov.lower() in rlow or prov.lower() in _CA_HINT and prov.lower() in rlow:
            return lat, lon, z
    return hits[0][:3]


def locate_holes(holes, zone, hemi, datum, region=None):
    """Fill lat/lon on each hole dict in place; returns count located. If the
    release stated no zone, infer it from `region`; and if a Canadian collar
    lands outside every province (wrong-zone guess), snap it to the zone that
    places it inside a real province."""
    n = 0
    for h in holes:
        if h.get("lat") is not None and h.get("lon") is not None:
            n += 1
            continue
        e, nth = h.get("easting"), h.get("northing")
        if e is None or nth is None:
            continue
        if not (1e5 <= e <= 9e5 and 0 <= nth <= 1e7):
            continue
        z = h.get("utm_zone") or zone
        lat = lon = None
        if z:
            lat, lon = utm_to_ll(e, nth, z, h.get("utm_hemi") or hemi, h.get("datum") or datum)
        if lat is None and region:
            lat, lon, z = _infer(e, nth, region, datum, hemi or "N")
        # correct a wrong-zone guess that fell outside every Canadian province
        if lat is not None and not _in_province(lat, lon):
            snap = _snap_canada(e, nth, h.get("utm_hemi") or hemi, h.get("datum") or datum, region)
            if snap:
                lat, lon, z = snap
        if lat is not None:
            h["lat"], h["lon"], h["utm_zone"] = lat, lon, z
            h["utm_hemi"] = h.get("utm_hemi") or hemi or "N"
            h["datum"] = h.get("datum") or datum
            n += 1
    return n
