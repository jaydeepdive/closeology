"""Static claim-polygon tiles for the Explore map's "Staked claims" layer.

The old layer drew claim CENTROIDS as fixed-size squares, so real contiguous
claim blocks looked like scattered dots. This writes the ACTUAL claim polygons,
tiled on a 0.5-degree geographic grid, so the client loads just the boundaries in
the current viewport and they render as true blocks (adjacent cells share edges).

All jurisdictions merge into ONE geographic tile set:
    site/claimtiles/<ix>_<iy>.geojson   (ix=floor(lon/T), iy=floor(lat/T))
    site/claimtiles/index.json          {"t": T, "tiles": ["ix_iy", ...]}
Each feature: {"c": tenure#, "o": owner, "e": good-to date}, EPSG:4326 polygon.
"""
import os
import json
import math

T = 0.5  # tile size in degrees (~35 km); a z>=10 viewport spans a few tiles


def _standardize(g):
    import geopandas as gpd

    def col(*names):
        for n in names:
            if n in g.columns:
                return n
        return None
    idc = col("TENURE_NUMBER_ID", "claim", "tenure", "NO_TITRE")
    oc = col("OWNER_NAME", "owner", "HOLDER", "TITULAIRE")
    ec = col("GOOD_TO_DATE", "EXPIRY_DATE", "expiry")
    n = len(g)
    out = gpd.GeoDataFrame({
        "c": g[idc].astype(str).values if idc else [""] * n,
        "o": g[oc].astype(str).values if oc else [""] * n,
        "e": g[ec].astype(str).values if ec else [""] * n,
    }, geometry=g.geometry.values, crs=g.crs)
    if out.crs is None:
        out = out.set_crs("EPSG:4326")
    elif str(out.crs).upper() not in ("EPSG:4326",):
        out = out.to_crs("EPSG:4326")
    return out


def build(slugs, site_dir="site"):
    import geopandas as gpd
    from shapely.geometry import mapping
    try:
        from shapely import set_precision
    except Exception:
        set_precision = None

    root = os.path.join(site_dir, "claimtiles")
    os.makedirs(root, exist_ok=True)
    buckets = {}          # (ix, iy) -> list of feature dicts
    cov = {}              # coarse staked-AREA cells for the zoomed-out overview
    COVR = 0.05           # ~5 km coverage cell
    total = 0
    for slug in slugs:
        fp = os.path.join("data", slug, "claims.parquet")
        if not os.path.exists(fp):
            continue
        try:
            g = gpd.read_parquet(fp)
        except Exception as e:
            print(f"[claimtiles] {slug}: read failed {str(e)[:60]}")
            continue
        if g is None or not len(g):
            continue
        g = _standardize(g)
        g = g[g.geometry.notna() & ~g.geometry.is_empty]
        clean = {"nan": "", "None": "", "NaT": "", "none": ""}
        for ccol in ("c", "o", "e"):
            g[ccol] = g[ccol].astype(str).map(lambda v: "" if v in clean else v)
        ndone = 0
        for c, o, e, geom in zip(g["c"], g["o"], g["e"], g.geometry):
            if geom is None or geom.is_empty:
                continue
            gg = geom
            if set_precision is not None:
                try:
                    gg = set_precision(geom, 1e-5)      # snap to ~1 m grid -> smaller
                    if gg.is_empty:
                        gg = geom
                except Exception:
                    gg = geom
            try:
                minx, miny, maxx, maxy = gg.bounds
            except Exception:
                continue
            feat = {"type": "Feature", "properties": {"c": c, "o": o, "e": e},
                    "geometry": mapping(gg)}
            # coarse coverage cell (centre of the claim) for the overview layer
            ccx = (minx + maxx) / 2.0
            ccy = (miny + maxy) / 2.0
            ck = (int(math.floor(ccx / COVR)), int(math.floor(ccy / COVR)))
            cov[ck] = cov.get(ck, 0) + 1
            ix0, ix1 = int(math.floor(minx / T)), int(math.floor(maxx / T))
            iy0, iy1 = int(math.floor(miny / T)), int(math.floor(maxy / T))
            # guard against a stray bad geometry spanning the globe
            if (ix1 - ix0) > 8 or (iy1 - iy0) > 8:
                cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
                ix0 = ix1 = int(math.floor(cx / T))
                iy0 = iy1 = int(math.floor(cy / T))
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    buckets.setdefault((ix, iy), []).append(feat)
            ndone += 1
        total += ndone
        print(f"[claimtiles] {slug}: {ndone} claim polygons tiled")

    tiles = []
    for (ix, iy), feats in buckets.items():
        key = f"{ix}_{iy}"
        with open(os.path.join(root, key + ".geojson"), "w") as fh:
            json.dump({"type": "FeatureCollection", "features": feats}, fh,
                      separators=(",", ":"))
        tiles.append(key)
    json.dump({"t": T, "tiles": tiles}, open(os.path.join(root, "index.json"), "w"),
              separators=(",", ":"))
    # staked-area overview: one point per populated ~5 km cell, rendered as
    # fixed-pixel marks so claims stay VISIBLE when zoomed out (polygons shrink to
    # nothing there). count 'n' lets the client fade sparse cells.
    cov_feats = []
    for (cx, cy), n in cov.items():
        lon = round((cx + 0.5) * COVR, 4)
        lat = round((cy + 0.5) * COVR, 4)
        cov_feats.append({"type": "Feature", "properties": {"n": n},
                          "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    json.dump({"type": "FeatureCollection", "features": cov_feats},
              open(os.path.join(site_dir, "claim_coverage.geojson"), "w"),
              separators=(",", ":"))
    print(f"[claimtiles] wrote {len(tiles)} tiles ({total} polygons), "
          f"{len(cov_feats)} coverage cells, from {len(slugs)} regions")
    return len(tiles)


if __name__ == "__main__":
    import sys
    build(sys.argv[1:] or ["bc", "on", "yk", "qc", "sk", "mb", "nl", "nt", "ns", "ab", "nu"])
