"""Nunavut ingest. Two public open-data sources:

  occurrences : NUMIN showings summary (Nunavut Geoscience) — a POST that returns
                the full showing table as CSV, already carrying lat/long.
  claims      : CIRNAC "Mineral Tenure in Nunavut — Mineral Claim" ArcGIS layer
                (federal Crown administers Nunavut mineral tenure).

Produces the standard columns pipeline.run_region expects:
  occurrences.parquet : minfile,name,status,commodities(list),commodity,deposit_type,
                        minfile_url,prod_ind,township,geometry(Point 4326)
  claims.parquet      : TENURE_NUMBER_ID,CLAIM_NAME,OWNER_NAME,ISSUE_DATE,GOOD_TO_DATE,geometry
"""
import io
import os
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import arcgis_common
from config import METAL_ABBR

NUMIN = "https://nunavutgeoscience.ca/apps/showing/downloadSummary.php"
CLAIMS = ("https://geo.sac-isc.gc.ca/geomatics/rest/services/Donnees_Ouvertes-Open_Data/"
          "Claim_minier_NU_Mineral_Claim/MapServer/0")
UA = {"User-Agent": "Mozilla/5.0 (Closeology data bot; contact jay@thedeepdive.ca)"}

# element symbol -> canonical metal name (metal_bucket only knows full names)
_SYM = {v: k for k, v in METAL_ABBR.items()}
_SYM.update({"PGE": "Platinum", "PGM": "Platinum", "REE": "Rare earths",
             "Th": "Uranium", "Y": "Rare earths", "Ce": "Rare earths", "Nd": "Rare earths"})

# CIRNAC config for the shared arcgis fetch_claims()
_CLAIM_CFG = {"slug": "nu",
              "claims": {"url": CLAIMS,
                         "fields": "CLAIM_NUM,CLAIM_NAME,OWNERS,STAKING_DT,ANNIV_DT,CLAIM_STAT",
                         "id": "CLAIM_NUM", "name": "CLAIM_NAME", "owner": "OWNERS",
                         "issue": "STAKING_DT", "expiry": "ANNIV_DT",
                         "where": "CLAIM_STAT IN ('ACTIVE','LEASED','REINSTATED')"}}


def _commodities(raw):
    """NUMIN commodities look like 'Zn-Cu-Ni-Pt-Pd' — dash-separated symbols."""
    toks = [t.strip() for t in str(raw or "").replace("/", "-").replace(",", "-").split("-") if t.strip()]
    out, seen = [], set()
    for t in toks:
        name = _SYM.get(t, _SYM.get(t.title(), t))
        k = name.lower()
        if k not in seen:
            seen.add(k); out.append(name)
    return out


def fetch_occurrences(out_dir):
    r = requests.post(NUMIN, data="sq=",
                      headers={**UA, "Content-Type": "application/x-www-form-urlencoded"}, timeout=120)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df = df[df["LATLONG_LATITUDE"].notna() & df["LATLONG_LONGITUDE"].notna()].copy()
    rows, geoms = [], []
    for _, p in df.iterrows():
        stage = str(p.get("DEVELOPMENT_STAGE") or "").strip()
        if stage.lower().startswith("xto be") or not stage:
            stage = "Occurrence"
        is_prod = "past producer" in stage.lower()
        comm = _commodities(p.get("COMMODITIES"))
        try:
            lat = float(p["LATLONG_LATITUDE"]); lon = float(p["LATLONG_LONGITUDE"])
        except Exception:
            continue
        if not (-90 <= lat <= 90 and -141 <= lon <= -50):
            continue
        rows.append({"minfile": str(p.get("SHOWING_ID") or "").strip(),
                     "name": str(p.get("NAME") or p.get("ALIAS") or "").strip() or str(p.get("SHOWING_ID")),
                     "status": stage, "commodities": comm, "commodity": ", ".join(comm),
                     "deposit_type": str(p.get("SETTING_CMT") or "").strip()[:120],
                     "minfile_url": str(p.get("WEB_LINK") or "").strip(),
                     "prod_ind": "Y" if is_prod else "N", "township": ""})
        geoms.append(Point(lon, lat))
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    gdf.to_parquet(os.path.join(out_dir, "occurrences.parquet"))
    print(f"[nu] occurrences {len(gdf)} (producers {int((gdf.prod_ind=='Y').sum())}, "
          f"drilled {int(df['DEVELOPMENT_STAGE'].astype(str).str.contains('Drilled').sum())})")
    return gdf


def run():
    out_dir = "data/nu"
    os.makedirs(out_dir, exist_ok=True)
    fetch_occurrences(out_dir)
    arcgis_common.fetch_claims(_CLAIM_CFG, out_dir)          # -> claims.parquet
    # boundary polygon for the map (Statistics Canada / from keep if present)
    try:
        arcgis_common.boundary({"slug": "nu", "name": "Nunavut",
                                "boundary_name": "Nunavut"})
    except Exception as e:
        print("[nu] boundary skipped:", str(e)[:100])


if __name__ == "__main__":
    run()
