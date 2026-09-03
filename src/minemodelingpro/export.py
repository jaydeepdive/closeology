"""MineModelingPro — export the shared drill bank into modelling-ready tables.

Project Closeology and MineModelingPro share ONE data bank
(data/keep/drillbank.sqlite, written by src/newswire). Closeology uses the
geolocated recent results to drive open-ground leads; MineModelingPro consumes
the full collar + assay record (worldwide, all commodities) to build deposit
models. This module is the clean handoff: it flattens the bank to two files a
modelling workflow can pick up directly.

  data/keep/mmp_collars.parquet   one row per drill hole (coords, orientation)
  data/keep/mmp_assays.parquet    one row per assay interval per element

Run:  python -m minemodelingpro.export
"""
import os
import sqlite3
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(_ROOT, "data", "keep", "drillbank.sqlite")
OUT = os.path.join(_ROOT, "data", "keep")


def export(db=DB, out=OUT):
    if not os.path.exists(db):
        print("[mmp] no drill bank yet — nothing to export")
        return {"collars": 0, "assays": 0}
    con = sqlite3.connect(db)
    collars = pd.read_sql_query("""
        SELECT h.hole_id, r.company, r.project, r.country, r.published AS release_date,
               r.source, r.url, h.easting, h.northing, h.utm_zone, h.utm_hemi, h.datum,
               h.lat, h.lon, h.elev_m, h.azimuth, h.dip, h.depth_m, h.release_id
        FROM holes h JOIN releases r ON h.release_id=r.id""", con)
    assays = pd.read_sql_query("""
        SELECT i.release_id, r.company, r.project, r.country, r.published AS release_date,
               i.hole_id, i.is_subinterval, i.from_m, i.to_m, i.length_m,
               i.element, i.grade, i.unit
        FROM intervals i JOIN releases r ON i.release_id=r.id""", con)
    con.close()
    os.makedirs(out, exist_ok=True)
    collars.to_parquet(os.path.join(out, "mmp_collars.parquet"))
    assays.to_parquet(os.path.join(out, "mmp_assays.parquet"))
    # a small CSV sample too, for quick inspection
    collars.head(2000).to_csv(os.path.join(out, "mmp_collars_sample.csv"), index=False)
    print(f"[mmp] exported {len(collars)} collars, {len(assays)} assays -> data/keep/mmp_*.parquet")
    return {"collars": len(collars), "assays": len(assays)}


if __name__ == "__main__":
    export()
