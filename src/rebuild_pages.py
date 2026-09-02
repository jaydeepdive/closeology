"""Regenerate every HTML page from EXISTING data/*/out outputs — no data
re-fetch, no pipeline re-run. Used to propagate theme/nav/map changes across
the whole site quickly. Mirrors build_all's page-generation tail."""
import os
import json
import shutil
import build_all
import build_app
import build_priority
import build_site
import build_map
import daily
import build_radar
from build_map import BC_WMS
from ca_provinces import PROVINCES, TODAY

METRIC = {"bc": "EPSG:3005", "on": "EPSG:3161", "yk": "EPSG:3579", "qc": "EPSG:3978"}
METRIC.update({p["slug"]: p["metric_crs"] for p in PROVINCES})
NAME = {r["slug"]: r["name"] for r in build_all.REGIONS_SITE}
WMS = {"bc": BC_WMS}
INLINE = {"bc": False}  # BC uses WMS; everything else inline_claims=True


def _have(p):
    return os.path.exists(p)


def main():
    os.makedirs("site", exist_ok=True)
    live = [r["slug"] for r in build_all.REGIONS_SITE
            if _have(os.path.join(r["dir"], "out", "leads.geojson"))]
    regions_site = [dict(r, live=True) for r in build_all.REGIONS_SITE if r["slug"] in live]

    # per-region map + daily radar page
    for r in regions_site:
        slug, d = r["slug"], r["dir"]
        try:
            build_map.build(os.path.join(d, "out"), f"site/{slug}.html",
                            wms=WMS.get(slug), inline_claims=INLINE.get(slug, True))
        except Exception as e:
            print(f"[rebuild] map {slug} failed:", str(e)[:120])
        news = "site/news.json" if slug == "bc" else f"site/news_{slug}.json"
        try:
            daily.build(d, "site", NAME.get(slug, slug.upper()), METRIC.get(slug, "EPSG:3978"),
                        news_path=(news if _have(news) else None), out_name=f"daily_{slug}.html")
        except Exception as e:
            print(f"[rebuild] daily {slug} failed:", str(e)[:120])

    build_app.build(regions_site, "site/app.html")
    build_priority.build("site", regions_site)
    build_site.build("site", regions_site)

    # cross-Canada daily overview (radar.html) from the same email payload
    email = {"generated": TODAY, "site": "https://jaydeepdive.github.io/closeology/", "regions": []}
    for slug in live:
        d = f"data/{slug}"
        news = "site/news.json" if slug == "bc" else f"site/news_{slug}.json"
        try:
            dp = daily.payload(d, METRIC.get(slug, "EPSG:3978"), news if _have(news) else None)
        except Exception:
            continue
        flagged = [f["properties"] for f in dp["lead_feats"]
                   if f["properties"]["near_a"] or f["properties"]["near_b"]]
        flagged.sort(key=lambda p: -p.get("score", 0))
        email["regions"].append({"slug": slug, "name": NAME.get(slug, slug.upper()),
                                 "labels": dp["labels"], "counts": dp["counts"],
                                 "edges": dp.get("edges", [])[:20],
                                 "edge_counts": dp.get("edge_counts", {"n": 0, "hot": 0, "open_ha": 0}),
                                 "leads": flagged[:20], "dropped": dp.get("dropped", [])[:25]})
    json.dump(email, open("site/daily_email.json", "w"))
    build_radar.build(email, "site")
    print(f"[rebuild] pages rebuilt for: {', '.join(live)}")


if __name__ == "__main__":
    main()
