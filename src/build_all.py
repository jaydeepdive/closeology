"""Full Canada-wide rebuild for the daily job: fetch/refresh data, run every
pipeline, rebuild every map + daily radar + the combined app + priority front page
+ regions hub. Large source data is re-fetched when absent (kept out of git); small
geology facts / boundaries / snapshots are read from data/keep/. Every region is
guarded so one jurisdiction failing can't stop the others or the site build."""
import os
import shutil
import traceback
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
import arcgis_common
import ca_provinces
from ca_provinces import PROVINCES, run_region_cfg, TODAY

# QC is WFS/GPKG, not ArcGIS REST — its own module + a run_region config
QC = {"slug": "qc", "name": "Quebec", "dir": "data/qc", "metric_crs": "EPSG:3978",
      "attribution": ("Contains information from SIGEOM (Géologie Québec) and GESTIM "
                      "(titres miniers) under the Open Government Licence – Québec. "
                      "Verify tenure before staking.")}

# NU is NUMIN (CSV POST) + CIRNAC claims (ArcGIS) — its own ingest + run_region config
NU = {"slug": "nu", "name": "Nunavut", "dir": "data/nu", "metric_crs": "EPSG:3978",
      "attribution": ("Contains NUMIN mineral showings (Nunavut Geoscience) and CIRNAC "
                      "Nunavut mineral claims (Crown-administered federal tenure) under the "
                      "Open Government Licence. Verify tenure before staking.")}

# order = user's "fastest-open-data first", then the rest
REGIONS_SITE = ([
    {"name": "British Columbia", "dir": "data/bc", "slug": "bc", "live": True},
    {"name": "Ontario", "dir": "data/on", "slug": "on", "live": True},
    {"name": "Yukon", "dir": "data/yk", "slug": "yk", "live": True},
] + [{"name": p["name"], "dir": p["dir"], "slug": p["slug"], "live": True} for p in PROVINCES]
  + [{"name": "Quebec", "dir": "data/qc", "slug": "qc", "live": True},
     {"name": "Nunavut", "dir": "data/nu", "slug": "nu", "live": True},
     {"name": "Prince Edward Island", "dir": "data/pe", "slug": "pe", "info": True,
      "note": ("No metallic mineral occurrences on record. PEI is underlain entirely by "
               "flat-lying Permian–Carboniferous redbeds (sandstone, siltstone, mudstone) "
               "of the Maritimes Basin — a sedimentary cover with no economic metallic "
               "mineralization and no mineral-claim staking regime. Tracked here for "
               "completeness; there is no open metallic ground to flag.")}])

APP_REGIONS = [   # combined explorer stays scoped to the flagship regions (size)
    {"dir": "data/bc", "slug": "bc", "name": "British Columbia", "metric": "EPSG:3005", "news": "site/news.json"},
    {"dir": "data/on", "slug": "on", "name": "Ontario", "metric": "EPSG:3161", "news": "site/news_on.json"},
    {"dir": "data/yk", "slug": "yk", "name": "Yukon", "metric": "EPSG:3579", "news": "site/news_yk.json"},
]


def _have(p):
    return os.path.exists(p)


def _bc():
    ingest.run()
    keep = "data/keep/bc_minfile_facts.parquet"
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
    try:
        import on_web_facts
        on_web_facts.enrich("data/on")     # grade/tonnage from MDI pages; re-rank
    except Exception as e:
        print("[build_all] ON web-facts enrich skipped (leads already built):", str(e)[:120])
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


def _arcgis_province(cfg):
    def _run():
        fresh = os.environ.get("FULL") or not _have(os.path.join(cfg["dir"], "claims.parquet")) \
            or not _have(os.path.join(cfg["dir"], "occurrences.parquet"))
        if fresh:
            arcgis_common.run_fetch(cfg)
        else:
            arcgis_common.boundary(cfg)
        # per-province enrichment (assay tables / detail pages) so leads qualify
        enrich = cfg.get("enrich")
        if enrich and fresh:
            try:
                import region_enrich
                getattr(region_enrich, enrich)(cfg["dir"])
            except Exception as e:
                print(f"[build_all] {cfg['slug']} enrich skipped:", str(e)[:100])
        pipeline.run_region(run_region_cfg(cfg))
        build_map.build(os.path.join(cfg["dir"], "out"), f"site/{cfg['slug']}.html", inline_claims=True)
        daily.build(cfg["dir"], "site", cfg["name"], cfg["metric_crs"],
                    news_path=f"site/news_{cfg['slug']}.json", out_name=f"daily_{cfg['slug']}.html")
    return _run


def _nu():
    import nu_ingest
    if os.environ.get("FULL") or not _have("data/nu/claims.parquet") \
            or not _have("data/nu/occurrences.parquet"):
        nu_ingest.run()
    else:
        arcgis_common.boundary({"slug": "nu", "name": "Nunavut", "boundary_name": "Nunavut"})
    pipeline.run_region({"name": "Nunavut", "dir": "data/nu", "metric_crs": "EPSG:3978",
                         "today": TODAY, "inline_claims": True, "attribution": NU["attribution"]})
    build_map.build("data/nu/out", "site/nu.html", inline_claims=True)
    daily.build("data/nu", "site", "Nunavut", "EPSG:3978",
                news_path="site/news_nu.json", out_name="daily_nu.html")


def _qc():
    import qc_ingest
    if os.environ.get("FULL") or not _have("data/qc/claims.parquet") \
            or not _have("data/qc/occurrences.parquet"):
        qc_ingest.run()
    pipeline.run_region({"name": "Quebec", "dir": "data/qc", "metric_crs": "EPSG:3978",
                         "today": TODAY, "inline_claims": True, "attribution": QC["attribution"]})
    build_map.build("data/qc/out", "site/qc.html", inline_claims=True)
    daily.build("data/qc", "site", "Quebec", "EPSG:3978",
                news_path="site/news_qc.json", out_name="daily_qc.html")


def main():
    os.makedirs("site", exist_ok=True)

    try:
        import fetch_prices
        fetch_prices.run()
    except Exception as e:
        print("[build_all] price refresh skipped:", str(e)[:80])

    try:
        import fetch_news
        fetch_news.run(["data/bc", "data/on"])
    except Exception as e:
        print("[build_all] news fetch skipped:", str(e)[:80])

    # region builders in the chosen order
    builders = [("bc", _bc), ("on", _on), ("yk", _yk)]
    builders += [(p["slug"], _arcgis_province(p)) for p in PROVINCES]
    builders += [("qc", _qc), ("nu", _nu)]

    live = []
    for slug, fn in builders:
        try:
            fn()
            live.append(slug)
        except Exception as e:
            print(f"[build_all] region {slug} FAILED, skipping:", str(e)[:140])
            traceback.print_exc()

    regions_site = [r for r in REGIONS_SITE if r["slug"] in live
                    and _have(os.path.join(r["dir"], "out", "leads.geojson"))]
    app_regions = [r for r in APP_REGIONS if r["slug"] in live
                   and _have(os.path.join(r["dir"], "out", "leads.geojson"))]

    # info-only regions (e.g. PEI — no metallic ground) carry no leads but are
    # shown on the hub for completeness
    info_regions = [r for r in REGIONS_SITE if r.get("info")]

    build_app.build(regions_site, "site/app.html")   # ONE unified all-Canada map
    import build_priority
    build_priority.build("site", regions_site)         # index.html (front page)
    build_site.build("site", regions_site + info_regions)   # regions.html hub + CSV/XLSX

    # daily email digest across every live region
    import json as _json
    metric = {r["slug"]: r["metric_crs"] for r in [
        {"slug": "bc", "metric_crs": "EPSG:3005"}, {"slug": "on", "metric_crs": "EPSG:3161"},
        {"slug": "yk", "metric_crs": "EPSG:3579"}] + [
        {"slug": p["slug"], "metric_crs": p["metric_crs"]} for p in PROVINCES] + [
        {"slug": "qc", "metric_crs": "EPSG:3978"}, {"slug": "nu", "metric_crs": "EPSG:3978"}]}
    name = {r["slug"]: r["name"] for r in REGIONS_SITE}
    email = {"generated": TODAY, "site": "https://jaydeepdive.github.io/closeology/", "regions": []}
    for slug in live:
        d = f"data/{slug}"
        news = f"site/news_{slug}.json" if slug != "bc" else "site/news.json"
        try:
            dp = daily.payload(d, metric.get(slug, "EPSG:3978"), news)
        except Exception:
            continue
        import digest
        flagged = [f["properties"] for f in dp["lead_feats"]
                   if f["properties"]["near_a"] or f["properties"]["near_b"]]
        flagged.sort(key=lambda p: -p.get("score", 0))
        # coalesce the per-cell drop rows into the PROPERTIES that were dropped,
        # and give every lead a map deep-link so it can actually be looked at
        dropped_props = digest.group_dropped(dp.get("dropped", []), slug)
        for lf in dp["lead_feats"]:
            p = lf["properties"]
            c = lf["geometry"]["coordinates"]
            p["lat"], p["lon"] = c[1], c[0]
            p["map_url"] = digest.map_url(slug, c[1], c[0], zoom=12,
                                          label=p.get("name"), kind="lead")
        flagged = [f["properties"] for f in dp["lead_feats"]
                   if f["properties"]["near_a"] or f["properties"]["near_b"]]
        flagged.sort(key=lambda p: -p.get("score", 0))
        for e in dp.get("edges", []):
            ll = e.get("lat"), e.get("lon")
            if ll[0] is not None and ll[1] is not None:
                e["map_url"] = digest.map_url(slug, ll[0], ll[1], zoom=12,
                                              label=e.get("company") or e.get("property"), kind="edge")
        email["regions"].append({"slug": slug, "name": name.get(slug, slug.upper()),
                                 "labels": dp["labels"], "counts": dp["counts"],
                                 "map": f"https://jaydeepdive.github.io/closeology/{slug}.html",
                                 "radar": "https://jaydeepdive.github.io/closeology/radar.html",
                                 "edges": dp.get("edges", [])[:20],
                                 "edge_counts": dp.get("edge_counts", {"n": 0, "hot": 0, "open_ha": 0}),
                                 "leads": flagged[:20],
                                 "dropped_properties": dropped_props[:25],
                                 "dropped": dp.get("dropped", [])[:25]})
    # short, ranked teaser for the email (a handful of one-liners across all
    # regions + a link to the radar for the rest) so the email can't be a wall
    import digest as _digest
    email["top"] = _digest.build_top(email["regions"], site=email["site"])
    _json.dump(email, open("site/daily_email.json", "w"))
    import build_radar
    build_radar.build(email, "site")                   # radar.html cross-Canada overview

    # newswire drill-data pipeline: crawl recent mining releases, extract collars
    # + assays into the shared bank, then build the Drill Radar page. Guarded so a
    # throttle / network hiccup never breaks the site build.
    try:
        from newswire import run as nw_run, radar as nw_radar
        limit = int(os.environ.get("NEWSWIRE_LIMIT", "120"))
        mode = "backfill" if os.environ.get("NEWSWIRE_BACKFILL") else "incremental"
        nw_run.run(mode, limit=limit)
        nw_radar.build("site")
        from minemodelingpro import export as mmp_export
        mmp_export.export()                             # refresh modelling tables
    except Exception as e:
        print("[build_all] newswire step skipped:", str(e)[:160])
        try:
            from newswire import radar as nw_radar
            nw_radar.build("site")     # still (re)build the page from whatever's banked
        except Exception:
            pass
    tot_edges = sum(len(r["edges"]) for r in email["regions"])
    print(f"[build_all] done ({', '.join(live)}) + daily_email.json ({tot_edges} edge plays)")


if __name__ == "__main__":
    main()
