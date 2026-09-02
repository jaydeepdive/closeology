"""Full multi-region rebuild for the daily job: fetch/refresh data, run every
pipeline, rebuild every map + daily radar + the combined app + landing page.
Large source data is re-fetched when absent (kept out of git); small geology
facts are read from data/keep/. Each region is guarded so one province failing
can't stop the others or the site build."""
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
from run_yk import YK

REGIONS_SITE = [
    {"name": "British Columbia", "dir": "data/bc", "slug": "bc", "live": True},
    {"name": "Ontario", "dir": "data/on", "slug": "on", "live": True},
    {"name": "Yukon", "dir": "data/yk", "slug": "yk", "live": True},
]
APP_REGIONS = [
    {"dir": "data/bc", "slug": "bc", "name": "British Columbia", "metric": "EPSG:3005", "news": "site/news.json"},
    {"dir": "data/on", "slug": "on", "name": "Ontario", "metric": "EPSG:3161", "news": "site/news_on.json"},
    {"dir": "data/yk", "slug": "yk", "name": "Yukon", "metric": "EPSG:3579", "news": "site/news_yk.json"},
]


def _have(p):
    return os.path.exists(p)


def _bc():
    ingest.run()                                    # fast WFS layers + occurrences CSV
    keep = "data/keep/bc_minfile_facts.parquet"     # committed geology facts (grade/tonnage/drill)
    if _have(keep):
        shutil.copy(keep, "data/bc/minfile_facts.parquet")
    pipeline.run_region(BC)
    build_map.build("data/bc/out", "site/bc.html", wms=BC_WMS)
    daily.build("data/bc", "site", "British Columbia", "EPSG:3005",
                news_path="site/news.json", out_name="daily_bc.html")


def _on():
    if os.environ.get("FULL") or not _have("data/on/claims.parquet"):
        on_ingest.run()
    else:
        for fn, fx in [("mdi", on_ingest.fetch_mdi), ("drillholes", on_ingest.fetch_drill),
                       ("parks", on_ingest.fetch_parks), ("communities", on_ingest.fetch_communities)]:
            if not _have(f"data/on/{fn}.parquet"):
                fx()
    on_prep.prep()
    pipeline.run_region(ON)
    import on_web_facts
    on_web_facts.enrich("data/on")     # grade + tonnage from MDI record pages, then re-rank
    build_map.build("data/on/out", "site/on.html", inline_claims=True)
    daily.build("data/on", "site", "Ontario", "EPSG:3161",
                news_path="site/news_on.json", out_name="daily_on.html")


def _yk():
    import yk_ingest
    if not _have("data/yk/claims.parquet") or not _have("data/yk/occurrences.parquet"):
        yk_ingest.run()
    pipeline.run_region(YK)
    build_map.build("data/yk/out", "site/yk.html", inline_claims=True)
    daily.build("data/yk", "site", "Yukon", "EPSG:3579",
                news_path="site/news_yk.json", out_name="daily_yk.html")


def main():
    os.makedirs("site", exist_ok=True)

    # ---- refresh metal prices (value scoring tracks the market) ----
    try:
        import fetch_prices
        fetch_prices.run()
    except Exception as e:
        print("[build_all] price refresh skipped:", str(e)[:80])

    # ---- fresh drill news -> per-region news_items.json (safe no-op if no sources) ----
    try:
        import fetch_news
        fetch_news.run(["data/bc", "data/on", "data/yk"])
    except Exception as e:
        print("[build_all] news fetch skipped:", str(e)[:80])

    # ---- per-region builds; a province failing must not stop the others ----
    live = []
    for slug, fn in [("bc", _bc), ("on", _on), ("yk", _yk)]:
        try:
            fn()
            live.append(slug)
        except Exception as e:
            import traceback
            print(f"[build_all] region {slug} FAILED, skipping:", str(e)[:120])
            traceback.print_exc()

    # only surface regions that actually produced outputs
    regions_site = [r for r in REGIONS_SITE if r["slug"] in live and _have(os.path.join(r["dir"], "out", "leads.geojson"))]
    app_regions = [r for r in APP_REGIONS if r["slug"] in live and _have(os.path.join(r["dir"], "out", "leads.geojson"))]

    # ---- combined app, landing, unified priority ----
    build_app.build(app_regions, "site/app.html")
    import build_priority
    build_priority.build("site", regions_site)         # unified cross-jurisdiction ranking
    build_site.build("site", regions_site)             # Deep Dive-styled landing = index.html

    # digest for the daily email. Order of priority per region:
    #   1) EDGE PLAYS  - fresh drilling against open ground (the reason to act now)
    #   2) ground just opened (drop tracker)
    #   3) leads with claim activity nearby
    import json as _json
    email = {"generated": BC["today"], "site": "https://jaydeepdive.github.io/closeology/", "regions": []}
    for rc in app_regions:
        dp = daily.payload(rc["dir"], rc["metric"], rc.get("news"))
        flagged = [f["properties"] for f in dp["lead_feats"]
                   if f["properties"]["near_a"] or f["properties"]["near_b"]]
        flagged.sort(key=lambda p: -p.get("score", 0))
        email["regions"].append({"slug": rc["slug"], "name": rc["name"], "labels": dp["labels"],
                                 "counts": dp["counts"],
                                 "edges": dp.get("edges", [])[:20],
                                 "edge_counts": dp.get("edge_counts", {"n": 0, "hot": 0, "open_ha": 0}),
                                 "leads": flagged[:20],
                                 "dropped": dp.get("dropped", [])[:25]})
    _json.dump(email, open("site/daily_email.json", "w"))
    tot_edges = sum(len(r["edges"]) for r in email["regions"])
    print(f"[build_all] done ({', '.join(live)}) + daily_email.json ({tot_edges} edge plays across regions)")


if __name__ == "__main__":
    main()
