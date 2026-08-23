"""Ontario ingest: MDI occurrences + drill holes (ArcGIS REST), active claim
cells (OGSEarth daily KMZ tiles), parks and communities."""
import io
import os
import re
import json
import time
import zipfile
import urllib.parse
import urllib.request
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon

GO = "https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/GeologyOntario/GeologyOntario_Map/MapServer"
CLAIM_BASE = "https://www.geologyontario.mndm.gov.on.ca/mines/data/google/claims2/claimmap"


def _get(url, timeout=120):
    for a in range(4):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except Exception:
            if a == 3:
                raise
            time.sleep(3)


def _json(url, timeout=120):
    for a in range(4):
        try:
            return json.loads(_get(url, timeout))
        except json.JSONDecodeError:
            if a == 3:
                raise
            time.sleep(3)


def arcgis_points(layer_id, out_fields, page=2000):
    """Fetch a point layer via lat/long attribute fields (no geometry payload)."""
    base = f"{GO}/{layer_id}/query"
    rows, off = [], 0
    while True:
        q = {"where": "1=1", "outFields": out_fields, "returnGeometry": "false",
             "orderByFields": "OBJECTID", "resultOffset": off, "resultRecordCount": page, "f": "json"}
        d = _json(base + "?" + urllib.parse.urlencode(q))
        fs = d.get("features", [])
        rows += [f["attributes"] for f in fs]
        if len(fs) < page:
            break
        off += page
    return pd.DataFrame(rows)


def fetch_mdi():
    df = arcgis_points(46, "MDI_IDENT,STATUS,NAME,ALL_NAMES,CLASS,PRIMARY_COMMODITIES,SECONDARY_COMMODITIES,TOWNSHIP,LATITUDE_DD,LONGITUDE_DD,INFO_LINK")
    df = df.dropna(subset=["LATITUDE_DD", "LONGITUDE_DD"])
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.LONGITUDE_DD, df.LATITUDE_DD), crs="EPSG:4326")
    g.to_parquet("data/on/mdi.parquet")
    print(f"[on mdi] {len(g)}")


def fetch_drill():
    df = arcgis_points(47, "HOLE_IDENT,COMPANY_NAME,PROPERTY_NAME,YEAR_DRILLED,LENGTH,LENGTH_UNIT,ELEMENTS,COMMENTS,LATITUDE_DD,LONGITUDE_DD")
    df = df.dropna(subset=["LATITUDE_DD", "LONGITUDE_DD"])
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.LONGITUDE_DD, df.LATITUDE_DD), crs="EPSG:4326")
    g.to_parquet("data/on/drillholes.parquet")
    print(f"[on drill] {len(g)}")


def _parse_kml_polys(kml_bytes):
    s = kml_bytes.decode("utf-8", "replace")
    out = []
    for pm in re.findall(r"<Placemark>.*?</Placemark>", s, re.S):
        nm = re.search(r"<name>(.*?)</name>", pm, re.S)
        name = nm.group(1).strip() if nm else ""
        polys = []
        for coordblock in re.findall(r"<Polygon>.*?</Polygon>", pm, re.S):
            oc = re.search(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>", coordblock, re.S)
            if not oc:
                continue
            pts = []
            for tok in oc.group(1).split():
                parts = tok.split(",")
                if len(parts) >= 2:
                    pts.append((float(parts[0]), float(parts[1])))
            if len(pts) >= 3:
                polys.append(Polygon(pts))
        if polys:
            out.append({"claim": name, "geometry": polys[0] if len(polys) == 1 else MultiPolygon(polys)})
    return out


def fetch_claims():
    doc = _get(CLAIM_BASE + "/doc.kml").decode("utf-8", "replace")
    tiles = re.findall(r"<href>files/([^<]+)</href>", doc)
    print(f"[on claims] {len(tiles)} tiles")
    feats, t0 = [], time.time()
    for i, t in enumerate(tiles):
        try:
            kmz = _get(f"{CLAIM_BASE}/files/{t}", timeout=60)
            z = zipfile.ZipFile(io.BytesIO(kmz))
            kmlname = [n for n in z.namelist() if n.endswith(".kml")][0]
            feats += _parse_kml_polys(z.read(kmlname))
        except Exception as e:
            print("  tile fail", t, str(e)[:60])
        if (i + 1) % 50 == 0:
            print(f"   {i+1}/{len(tiles)} tiles, {len(feats)} claims, {time.time()-t0:.0f}s")
    g = gpd.GeoDataFrame(feats, geometry="geometry", crs="EPSG:4326")
    g = g.drop_duplicates("claim")
    g.to_parquet("data/on/claims.parquet")
    print(f"[on claims] {len(g)} unique claim cells")


ALIEN_BASE = "https://www.geologyontario.mndm.gov.on.ca/mines/data/google/claims2/alienations"


def fetch_alienations():
    """Held mining lands (leases, patents, licences of occupation, withdrawals)
    — not available for staking. Same tiled KMZ structure as claims."""
    doc = _get(ALIEN_BASE + "/doc.kml").decode("utf-8", "replace")
    tiles = re.findall(r"<href>files/([^<]+)</href>", doc)
    print(f"[on leases] {len(tiles)} tiles")
    feats, t0 = [], time.time()
    for i, t in enumerate(tiles):
        try:
            kmz = _get(f"{ALIEN_BASE}/files/{t}", timeout=60)
            z = zipfile.ZipFile(io.BytesIO(kmz))
            kmlname = [n for n in z.namelist() if n.endswith(".kml")][0]
            feats += _parse_kml_polys(z.read(kmlname))
        except Exception as e:
            print("  tile fail", t, str(e)[:50])
        if (i + 1) % 100 == 0:
            print(f"   {i+1}/{len(tiles)}, {len(feats)} parcels, {time.time()-t0:.0f}s")
    g = gpd.GeoDataFrame(feats, geometry="geometry", crs="EPSG:4326")
    g.to_parquet("data/on/leases.parquet")
    print(f"[on leases] {len(g)} held parcels")


OPEN = "https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/LIO_OPEN_DATA"


def arcgis_geojson(layer_url, where="1=1", page=1000, generalize=None, fields="*"):
    from shapely.geometry import shape
    feats, off = [], 0
    while True:
        q = {"where": where, "outFields": fields, "returnGeometry": "true",
             "outSR": "4326", "orderByFields": "OBJECTID", "resultOffset": off,
             "resultRecordCount": page, "f": "geojson"}
        if generalize:
            q["maxAllowableOffset"] = generalize
        try:
            d = _json(layer_url + "/query?" + urllib.parse.urlencode(q))
        except json.JSONDecodeError:
            break                     # LIO returns a non-JSON body past the last page
        if d.get("error"):
            break
        fs = d.get("features", [])
        feats += fs
        if len(fs) < page:
            break
        off += page
    geoms = [shape(f["geometry"]) if f.get("geometry") else None for f in feats]
    props = [f.get("properties", {}) for f in feats]
    return gpd.GeoDataFrame(props, geometry=geoms, crs="EPSG:4326")


def fetch_parks():
    layers = [("LIO_Open03", 4), ("LIO_Open03", 2), ("LIO_Open03", 10)]
    parts = []
    for svc, lid in layers:
        g = arcgis_geojson(f"{OPEN}/{svc}/MapServer/{lid}", page=500, generalize=0.0015)
        namecol = next((c for c in g.columns if "NAME" in c.upper()), None)
        g["PARK_NAME"] = g[namecol] if namecol else ""
        parts.append(g[["PARK_NAME", "geometry"]])
    g = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
    g = g[g.geometry.notna()]
    g.to_parquet("data/on/parks.parquet")
    print(f"[on parks] {len(g)}")


def fetch_communities():
    L = f"{OPEN}/LIO_Open09/MapServer/36"
    where = ("ENTITY_TYPE LIKE '%Community%' OR ENTITY_TYPE LIKE '%Urban%' "
             "OR ENTITY_TYPE = 'Locality' OR ENTITY_TYPE LIKE '%Municipality%'")
    q = {"where": where, "outFields": "OFFICIAL_NAME,ENTITY_TYPE,LATITUDE_DECIMAL_DEGREES,LONGITUDE_DECIMAL_DEGREES",
         "returnGeometry": "false", "f": "json"}
    rows, off = [], 0
    while True:
        q["resultOffset"] = off; q["resultRecordCount"] = 2000
        d = _json(L + "/query?" + urllib.parse.urlencode(q))
        fs = d.get("features", [])
        rows += [f["attributes"] for f in fs]
        if len(fs) < 2000:
            break
        off += 2000
    df = pd.DataFrame(rows).dropna(subset=["LATITUDE_DECIMAL_DEGREES", "LONGITUDE_DECIMAL_DEGREES"])
    df = df.rename(columns={"OFFICIAL_NAME": "name", "ENTITY_TYPE": "type"})
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.LONGITUDE_DECIMAL_DEGREES, df.LATITUDE_DECIMAL_DEGREES), crs="EPSG:4326")
    g.to_parquet("data/on/communities.parquet")
    print(f"[on communities] {len(g)}")


def run():
    os.makedirs("data/on", exist_ok=True)
    fetch_mdi()
    fetch_drill()
    fetch_claims()
    fetch_alienations()
    fetch_parks()
    fetch_communities()


if __name__ == "__main__":
    run()
