"""Static claim-polygon tiles for the Explore map's "Staked claims" layer.

The old layer drew claim CENTROIDS as fixed-size squares, so real contiguous
claim blocks looked like scattered dots. This writes the ACTUAL claim polygons,
tiled on a 0.5-degree geographic grid, so the client loads just the boundaries in
the current viewport and they render as true blocks (adjacent cells share edges).

All jurisdictions merge into ONE geographic tile set:
    site/claimtiles/<ix>_<iy>.geojson   (ix=floor(lon/T), iy=floor(lat/T))
    site/claimtiles/index.json          {"t": T, "tiles": ["ix_iy", ...]}
Each feature (EPSG:4326 polygon) carries:
    c  claim / tenure number          (always)
    j  jurisdiction code, e.g. "ON"   (always)
    o  holder                         ("" when the source does not publish it)
    e  good-to / anniversary date     (YYYY-MM-DD, "" when not published)
    n  claim / property name          (only when present)
    i  issue / staking date           (only when present, YYYY-MM-DD)
    a  area in hectares               (only when known)

PUBLIC API CONTRACT: these tiles are the GeoJSON layer of the public claims API
(/api/v1, see build_api.py and api_static/). Keys may be ADDED but existing keys
must not be renamed or change meaning without bumping the API version.
"""
import os, glob
import json
import math

T = 0.5  # tile size in degrees (~35 km); a z>=10 viewport spans a few tiles

# internal region slug -> standard Canadian province/territory code
JURIS_CODE = {"ab": "AB", "bc": "BC", "mb": "MB", "nb": "NB", "nl": "NL", "ns": "NS",
              "nt": "NT", "nu": "NU", "on": "ON", "qc": "QC", "sk": "SK", "yk": "YT"}

_JUNK = {"nan", "nat", "none", "null", "<na>"}


def _txt(v):
    """'' for None/NaN/junk, otherwise the stripped string."""
    if v is None:
        return ""
    try:
        if v != v:            # NaN
            return ""
    except Exception:
        pass
    s = str(v).strip()
    return "" if s.lower() in _JUNK else s


def _date(v):
    """Normalise a source date to YYYY-MM-DD; keep the raw text if unparseable."""
    s = _txt(v)
    if not s:
        return ""
    try:
        from claim_watch import _parse_date
        d = _parse_date(s)
        if d:
            return d.strftime("%Y-%m-%d")
    except Exception:
        pass
    return s[:10]


def _standardize(g, slug=""):
    import geopandas as gpd

    def col(*names):
        for n in names:
            if n in g.columns:
                return n
        return None
    # column candidates mirror claim_watch._current_claims so the map, the watch
    # page and the API all read the same fields from each provincial source
    idc = col("TENURE_NUMBER_ID", "claim", "tenure", "NO_TITRE")
    oc = col("OWNER_NAME", "owner", "HOLDER", "TITULAIRE")
    ec = col("GOOD_TO_DATE", "EXPIRY_DATE", "expiry", "ANNIV_DT", "GOODSTANDI")
    nc = col("CLAIM_NAME", "name", "CNAME")
    ic = col("ISSUE_DATE", "issue", "STAKING_DT")
    ac = col("AREA_IN_HECTARES", "area_ha", "AREA_HA")
    n = len(g)
    if g.crs is None:
        g = g.set_crs("EPSG:4326")
    elif str(g.crs).upper() not in ("EPSG:4326",):
        g = g.to_crs("EPSG:4326")
    area = [None] * n
    if ac:
        area = list(g[ac].values)
    else:
        try:
            area = list((g.to_crs("EPSG:3978").area / 1e4).values)   # Canada Atlas Lambert
        except Exception:
            pass

    def _area(v):
        try:
            f = float(v)
            return round(f, 2) if math.isfinite(f) and f > 0 else ""
        except Exception:
            return ""
    out = gpd.GeoDataFrame({
        "c": [_txt(v) for v in g[idc].values] if idc else [""] * n,
        "o": [_txt(v) for v in g[oc].values] if oc else [""] * n,
        "e": [_date(v) for v in g[ec].values] if ec else [""] * n,
        "n": [_txt(v) for v in g[nc].values] if nc else [""] * n,
        "i": [_date(v) for v in g[ic].values] if ic else [""] * n,
        "a": [_area(v) for v in area],
    }, geometry=g.geometry.values, crs="EPSG:4326")
    out["j"] = JURIS_CODE.get(slug, slug.upper())
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
    for _old in glob.glob(os.path.join(root, "*.geojson")):   # drop stale tiles so a
        try: os.remove(_old)                                  # removed/failed region
        except OSError: pass                                  # can't leave orphans
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
        g = g[g.geometry.notna() & ~g.geometry.is_empty]
        if not len(g):
            continue
        g = _standardize(g, slug)
        ndone = 0
        for c, o, e, nm, iss, ar, j, geom in zip(g["c"], g["o"], g["e"], g["n"], g["i"],
                                                 g["a"], g["j"], g.geometry):
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
            props = {"c": c, "o": o, "e": e, "j": j}
            if nm:
                props["n"] = nm
            if iss:
                props["i"] = iss
            if ar != "":
                props["a"] = ar
            feat = {"type": "Feature", "properties": props, "geometry": mapping(gg)}
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
    build(sys.argv[1:] or ["bc", "on", "yk", "qc", "sk", "mb", "nl", "nt", "ns", "ab", "nu", "nb"])
