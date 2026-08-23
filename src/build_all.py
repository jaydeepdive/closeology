"""Full two-region rebuild for the daily job: fetch/refresh data, run both
pipelines, rebuild every map + daily radar + the combined app + landing page.
Large source data is re-fetched when absent (kept out of git); small geology
facts are read from data/keep/."""
import os
import shutil
import ingest
import on_ingest
import on_prep
import pipeline
import build_map
import build_app
import daily
import build_site
from build_map import BC_WMS
from run_bc import BC
from run_on import ON

REGIONS_SITE = [
    {"name": "British Columbia", "dir": "data/bc", "slug": "bc", "live": True},
    {"name": "Ontario", "dir": "data/on", "slug": "on", "live": True},
]
APP_REGIONS = [
    {"dir": "data/bc", "slug": "bc", "metric": "EPSG:3005", "news": "site/news.json"},
    {"dir": "data/on", "slug": "on", "metric": "EPSG:3161", "news": "site/news_on.json"},
]


def _have(p):
    return os.path.exists(p)


def main():
    os.makedirs("site", exist_ok=True)

    # ---- British Columbia ----
    ingest.run()                                    # fast WFS layers + occurrences CSV
    keep = "data/keep/bc_minfile_facts.parquet"     # committed geology facts (grade/tonnage/drill)
    if _have(keep):
        shutil.copy(keep, "data/bc/minfile_facts.parquet")
    pipeline.run_region(BC)
    build_map.build("data/bc/out", "site/bc.html", wms=BC_WMS)

    # ---- Ontario ----  (heavy claim/lease crawls only if absent or FULL=1)
    if os.environ.get("FULL") or not _have("data/on/claims.parquet"):
        on_ingest.run()
    else:
        for fn, fx in [("mdi", on_ingest.fetch_mdi), ("drillholes", on_ingest.fetch_drill),
                       ("parks", on_ingest.fetch_parks), ("communities", on_ingest.fetch_communities)]:
            if not _have(f"data/on/{fn}.parquet"):
                fx()
    on_prep.prep()
    pipeline.run_region(ON)
    build_map.build("data/on/out", "site/on.html", inline_claims=True)

    # ---- daily radars, combined app, landing ----
    daily.build("data/bc", "site", "British Columbia", "EPSG:3005", news_path="site/news.json", out_name="daily_bc.html")
    daily.build("data/on", "site", "Ontario", "EPSG:3161", news_path="site/news_on.json", out_name="daily_on.html")
    build_app.build(APP_REGIONS, "site/app.html")
    build_site.build("site", REGIONS_SITE)
    print("[build_all] done")


if __name__ == "__main__":
    main()
