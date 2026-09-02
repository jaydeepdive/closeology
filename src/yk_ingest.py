"""Yukon ingest -> pipeline schema. Sources: GeoYukon ArcGIS REST
(mapservices.gov.yk.ca) — Mineral Occurrences (Yukon MINFILE), Quartz Claims
(with owner + expiry, so the daily lapse/new-stake signal works like BC), Quartz
Leases, and Areas Withdrawn from Staking (a no-stake layer)."""
import os
import time
import datetime
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape, Point

GYM = "https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Mining/MapServer"
GYG = "https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Geological/MapServer"
OCC_L, CLAIM_L, LEASE_L, WITHDRAWN_L = 4, 36, 37, 54
UA = {"User-Agent": "closeology-yk/1.0"}


def _get(url, params):
    for a in range(4):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception:
            if a == 3:
                raise
            time.sleep(2 * (a + 1))


def _fetch(layer_url, out_fields, geom=True, generalize=None, page=1500):
    feats, off = [], 0
    while True:
        q = {"where": "1=1", "outFields": out_fields, "returnGeometry": str(geom).lower(),
             "outSR": "4326", "orderByFields": "OBJECTID", "resultOffset": off,
             "resultRecordCount": page, "f": "geojson"}
        if generalize:
            q["maxAllowableOffset"] = generalize
        d = _get(layer_url + "/query", q)
        b = d.get("features", [])
        feats += b
        if not b:
            break
        off += len(b)
        if len(b) < page and not d.get("exceededTransferLimit", False):
            break
    return feats


def _epoch(v):
    try:
        return datetime.datetime.utcfromtimestamp(float(v) / 1000.0).strftime("%Y-%m-%d")
    except Exception:
        return ""


def fetch_occurrences():
    fs = _fetch(f"{GYG}/{OCC_L}",
                "MINFILE_NUMBER,MINFILE_NAME,DEPOSIT_STATUS,DEPOSIT_TYPE,MAIN_COMMODITY,"
                "PRODUCER_IND,LINK_TO_DOCUMENT,LATITUDE_DD,LONGITUDE_DD", geom=False)
    rows, geoms = [], []
    for f in fs:
        p = f.get("properties", {})
        lat, lon = p.get("LATITUDE_DD"), p.get("LONGITUDE_DD")
        if lat is None or lon is None:
            continue
        comm = [c.strip() for c in str(p.get("MAIN_COMMODITY") or "").replace(";", ",").split(",") if c.strip()]
        comm = [c.title() for c in comm]
        is_prod = str(p.get("PRODUCER_IND") or "").upper().startswith("Y")
        # Yukon MINFILE flags producers via PRODUCER_IND, not the status text; fold it in
        # so the pipeline treats them as past producers (production credit + status label).
        status = "Past Producer" if is_prod else (p.get("DEPOSIT_STATUS") or "").strip()
        rows.append({"minfile": str(p.get("MINFILE_NUMBER") or "").strip(),
                     "name": (p.get("MINFILE_NAME") or p.get("MINFILE_NUMBER") or "").strip(),
                     "status": status,
                     "commodities": comm, "commodity": ", ".join(comm),
                     "deposit_type": (p.get("DEPOSIT_TYPE") or "").strip(),
                     "minfile_url": (p.get("LINK_TO_DOCUMENT") or "").strip(),
                     "prod_ind": "Y" if is_prod else "N",
                     "township": ""})
        geoms.append(Point(float(lon), float(lat)))
    g = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    g.to_parquet("data/yk/occurrences.parquet")
    print(f"[yk] occurrences {len(g)}")


def _polys(feats, props):
    rows, geoms = [], []
    for f in feats:
        if not f.get("geometry"):
            continue
        try:
            gm = shape(f["geometry"])
        except Exception:
            continue
        pr = f.get("properties", {})
        rows.append({k: pr.get(v) for k, v in props.items()})
        geoms.append(gm)
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")


def fetch_claims():
    fs = _fetch(f"{GYM}/{CLAIM_L}",
                "CLAIM_NUMBER,CLAIM_NAME,OWNER_NAME,STAKING_DATE,EXPIRY_DATE,TENURE_STATUS",
                geom=True, generalize=25)
    g = _polys(fs, {"TENURE_NUMBER_ID": "CLAIM_NUMBER", "CLAIM_NAME": "CLAIM_NAME",
                    "OWNER_NAME": "OWNER_NAME", "_iss": "STAKING_DATE", "_exp": "EXPIRY_DATE"})
    g["ISSUE_DATE"] = g["_iss"].map(_epoch)
    g["GOOD_TO_DATE"] = g["_exp"].map(_epoch)
    g = g.drop(columns=["_iss", "_exp"])
    g.to_parquet("data/yk/claims.parquet")
    print(f"[yk] claims {len(g)}")


def fetch_leases():
    fs = _fetch(f"{GYM}/{LEASE_L}", "LEASE_NUMBER,CLAIM_NAME", geom=True, generalize=25)
    g = _polys(fs, {"claim": "LEASE_NUMBER"})
    g.to_parquet("data/yk/leases.parquet")
    print(f"[yk] leases {len(g)}")


def fetch_withdrawn():
    fs = _fetch(f"{GYM}/{WITHDRAWN_L}", "*", geom=True, generalize=40)
    g = _polys(fs, {"SITE_NAME": "OBJECTID"})
    g.to_parquet("data/yk/reserves.parquet")     # treated as no-stake
    print(f"[yk] withdrawn-from-staking {len(g)}")


def boundary():
    src = "data/keep/yk_boundary.parquet"
    if os.path.exists(src):
        return
    d = requests.get("https://raw.githubusercontent.com/codeforgermany/click_that_hood/"
                     "main/public/data/canada.geojson", headers=UA, timeout=90).json()
    for f in d["features"]:
        if f["properties"].get("name") == "Yukon Territory":
            gpd.GeoDataFrame({"name": ["Yukon"]}, geometry=[shape(f["geometry"])],
                             crs="EPSG:4326").to_parquet(src)
            print("[yk] boundary saved")


def run():
    os.makedirs("data/yk", exist_ok=True)
    boundary()
    fetch_occurrences()
    fetch_leases()
    fetch_withdrawn()
    fetch_claims()      # heaviest last


if __name__ == "__main__":
    run()
