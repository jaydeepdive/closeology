"""Fetch BC spatial layers into parquet: occurrences, claims, reserves,
parks, national parks, communities."""
import json
import time
import urllib.parse
import urllib.request
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape

BASE = "https://openmaps.gov.bc.ca/geo/pub/{layer}/ows"


def wfs_all(layer, cql=None, props=None, page=10000):
    feats, start = [], 0
    while True:
        q = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
             "typeName": f"pub:{layer}", "outputFormat": "application/json",
             "srsName": "EPSG:4326", "count": page, "startIndex": start, "sortBy": "OBJECTID"}
        if cql:
            q["cql_filter"] = cql
        if props:
            q["propertyName"] = props
        url = BASE.format(layer=layer) + "?" + urllib.parse.urlencode(q)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=180) as r:
                    d = json.loads(r.read().decode())
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(3)
        fs = d.get("features", [])
        feats += fs
        if len(fs) < page:
            break
        start += page
    return feats


def to_gdf(feats):
    geoms = [shape(f["geometry"]) if f.get("geometry") else None for f in feats]
    props = [f["properties"] for f in feats]
    return gpd.GeoDataFrame(props, geometry=geoms, crs="EPSG:4326")


OCC_CSV = ("https://catalogue.data.gov.bc.ca/dataset/92206d94-bc64-4111-a295-cd14eb5a501c/"
           "resource/120d5ee6-bff5-4cbe-b106-e419c790c395/download/minfile_mineral.csv")


def occurrences():
    import os
    p = "data/bc/minfile_mineral.csv"
    if not os.path.exists(p):
        os.makedirs("data/bc", exist_ok=True)
        urllib.request.urlretrieve(OCC_CSV, p)
    df = pd.read_csv(p, dtype=str)

    def comm(r):
        out = []
        for i in range(1, 9):
            v = r.get(f"COMMODITY_DESCRIPTION{i}")
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    df["commodities"] = df.apply(lambda r: comm(r), axis=1)
    df["commodity"] = df["commodities"].map(lambda x: ", ".join(x))
    df["lat"] = pd.to_numeric(df["DECIMAL_LATITUDE"], errors="coerce")
    df["lon"] = pd.to_numeric(df["DECIMAL_LONGITUDE"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"])
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")
    g = g.rename(columns={"MINERAL_FILE_NUMBER": "minfile", "MINFILE_NAME1": "name",
                          "STATUS_DESCRIPTION": "status", "URL": "minfile_url",
                          "PRODUCTION_INDICATOR": "prod_ind", "RESERVES_INDICATOR": "res_ind",
                          "DEPOSIT_TYPE_DESCRIPTION1": "deposit_type"})
    keep = ["minfile", "name", "status", "commodity", "commodities", "deposit_type",
            "minfile_url", "prod_ind", "res_ind", "lat", "lon", "geometry"]
    g = g[[c for c in keep if c in g.columns]]
    g.to_parquet("data/bc/occurrences.parquet")
    print(f"[occ] {len(g)}")


def run():
    occurrences()
    jobs = [
        ("claims", "WHSE_MINERAL_TENURE.MTA_ACQUIRED_TENURE_SVW", None,
         "TENURE_NUMBER_ID,CLAIM_NAME,TENURE_TYPE_DESCRIPTION,OWNER_NAME,ISSUE_DATE,GOOD_TO_DATE,AREA_IN_HECTARES"),
        ("reserves", "WHSE_MINERAL_TENURE.MTA_SITE_SP", "SITE_MINERAL_IND='Y'",
         "SITE_NAME,MTA_SITE_ORDER_RESTR_DESC,MTA_SITE_REASON_DESCRIPTION,RESERVE_TYPE"),
        ("parks", "WHSE_TANTALIS.TA_PARK_ECORES_PA_SVW", None, "PROTECTED_LANDS_NAME"),
        ("national_parks", "WHSE_ADMIN_BOUNDARIES.CLAB_NATIONAL_PARKS", None, "ENGLISH_NAME"),
        ("communities", "WHSE_BASEMAPPING.GNS_GEOGRAPHICAL_NAMES_SP", "FEATURE_CLASS LIKE 'Populated%'",
         "GEOGRAPHICAL_NAME,FEATURE_TYPE"),
    ]
    for name, layer, cql, props in jobs:
        t0 = time.time()
        g = to_gdf(wfs_all(layer, cql))   # no propertyName -> geometry is returned
        if name == "communities":
            g = g.rename(columns={"GEOGRAPHICAL_NAME": "name", "FEATURE_TYPE": "type"})
        g.to_parquet(f"data/bc/{name}.parquet")
        print(f"[{name}] {len(g)}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    run()
