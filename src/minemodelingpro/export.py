"""MineModelingPro — export the unified drill record into modelling-ready tables.

MMP draws on THREE stores and flattens them to two parquet files a modelling
workflow can pick up directly:

  * the government collar backbone      data/keep/mmp.sqlite   (collars, + assays
                                        from 43-101/assessment PDFs as they land)
  * the timely news drill bank          data/keep/drillbank.sqlite (recent full
                                        assays as juniors report them)

Outputs:
  data/keep/mmp_collars.parquet   one row per drill hole (coords, orientation, source)
  data/keep/mmp_assays.parquet    one row per assay interval per element (full downhole)
  data/keep/mmp_deposit_models.parquet  resource/block-model params from 43-101
  data/keep/mmp_collars_sample.csv      quick-look sample

Run:  python -m minemodelingpro.export
"""
import os
import sqlite3
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEEP = os.path.join(_ROOT, "data", "keep")
NEWS_DB = os.path.join(KEEP, "drillbank.sqlite")
MMP_DB = os.path.join(KEEP, "mmp.sqlite")

_COLLAR_COLS = ["hole_uid", "source", "source_kind", "native_id", "company", "project",
                "jurisdiction", "lat", "lon", "easting", "northing", "utm_zone",
                "utm_hemi", "datum", "elev_m", "azimuth", "dip", "depth_m",
                "year_drilled", "has_assay", "assay_flags", "report_ref", "url"]
_ASSAY_COLS = ["hole_uid", "source", "source_kind", "native_id", "project", "jurisdiction",
               "from_m", "to_m", "length_m", "element", "grade", "unit", "is_subinterval"]


def _gov(collars, assays, models):
    if not os.path.exists(MMP_DB):
        return
    con = sqlite3.connect(MMP_DB)
    con.row_factory = sqlite3.Row
    for r in con.execute("SELECT * FROM collars"):
        d = dict(r)
        d["source"] = d.pop("source_id"); d["source_kind"] = "gov_drillholes"
        collars.append({k: d.get(k) for k in _COLLAR_COLS})
    for r in con.execute("SELECT * FROM assays"):
        d = dict(r)
        d["source"] = d.pop("source_id"); d["source_kind"] = "gov_assays"
        d["project"] = None; d["jurisdiction"] = None
        assays.append({k: d.get(k) for k in _ASSAY_COLS})
    try:
        for r in con.execute("SELECT * FROM deposit_model"):
            models.append(dict(r))
    except sqlite3.OperationalError:
        pass
    con.close()


def _news(collars, assays):
    if not os.path.exists(NEWS_DB):
        return
    con = sqlite3.connect(NEWS_DB)
    con.row_factory = sqlite3.Row
    for r in con.execute("""SELECT h.hole_id, r.company, r.project, r.country, r.source, r.url,
               h.easting, h.northing, h.utm_zone, h.utm_hemi, h.datum, h.lat, h.lon,
               h.elev_m, h.azimuth, h.dip, h.depth_m, h.release_id
        FROM holes h JOIN releases r ON h.release_id=r.id"""):
        d = dict(r)
        collars.append({
            "hole_uid": f"news:{d['release_id']}:{d['hole_id']}", "source": d["source"],
            "source_kind": "news", "native_id": d["hole_id"], "company": d["company"],
            "project": d["project"], "jurisdiction": d["country"], "lat": d["lat"], "lon": d["lon"],
            "easting": d["easting"], "northing": d["northing"], "utm_zone": d["utm_zone"],
            "utm_hemi": d["utm_hemi"], "datum": d["datum"], "elev_m": d["elev_m"],
            "azimuth": d["azimuth"], "dip": d["dip"], "depth_m": d["depth_m"],
            "year_drilled": None, "has_assay": 1, "assay_flags": None,
            "report_ref": d["release_id"], "url": d["url"]})
    for r in con.execute("""SELECT i.release_id, r.company, r.project, r.country, r.source,
               i.hole_id, i.is_subinterval, i.from_m, i.to_m, i.length_m, i.element, i.grade, i.unit
        FROM intervals i JOIN releases r ON i.release_id=r.id"""):
        d = dict(r)
        assays.append({
            "hole_uid": f"news:{d['release_id']}:{d['hole_id']}", "source": d["source"],
            "source_kind": "news", "native_id": d["hole_id"], "project": d["project"],
            "jurisdiction": d["country"], "from_m": d["from_m"], "to_m": d["to_m"],
            "length_m": d["length_m"], "element": d["element"], "grade": d["grade"],
            "unit": d["unit"], "is_subinterval": d["is_subinterval"]})
    con.close()


def export(out=KEEP):
    collars, assays, models = [], [], []
    _gov(collars, assays, models)
    _news(collars, assays)
    os.makedirs(out, exist_ok=True)
    dc = pd.DataFrame(collars, columns=_COLLAR_COLS)
    da = pd.DataFrame(assays, columns=_ASSAY_COLS)
    dc.to_parquet(os.path.join(out, "mmp_collars.parquet"))
    da.to_parquet(os.path.join(out, "mmp_assays.parquet"))
    if models:
        pd.DataFrame(models).to_parquet(os.path.join(out, "mmp_deposit_models.parquet"))
    dc.head(3000).to_csv(os.path.join(out, "mmp_collars_sample.csv"), index=False)
    by = dc.groupby("source_kind").size().to_dict() if len(dc) else {}
    print(f"[mmp] exported {len(dc)} collars ({int(dc['lat'].notna().sum()) if len(dc) else 0} located), "
          f"{len(da)} assays, {len(models)} deposit-model rows -> data/keep/mmp_*.parquet")
    print(f"[mmp] collars by kind: {by}")
    return {"collars": len(dc), "assays": len(da), "models": len(models)}


if __name__ == "__main__":
    export()
