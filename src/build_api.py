"""Public, static claims API for Closeology  ->  site/api/v1/

Built every day from the claim tiles (site/claimtiles, see claim_tiles.py) and served
by GitHub Pages with open CORS (Access-Control-Allow-Origin: *) and HTTP range
support, so any site — e.g. mineterminalpro.com — can read it straight from the
browser or a server. No keys, no servers.

  api/v1/index.json                        manifest: endpoints, schema, counts, version
  api/v1/jurisdictions.json                per-jurisdiction counts, bbox, source, field coverage
  api/v1/claims.pmtiles                    vector tiles: layer "claims" (z8+, full polygons +
                                           details, nothing dropped) + layer "coverage" (z0-7)
  api/v1/claims.style.json                 drop-in MapLibre style (sources + layers)
  api/v1/claims/<jur>.csv.gz               bulk: every claim in a jurisdiction (details + centroid + bbox)
  api/v1/claim/<jur>/<shard>.json          single-claim lookup; shard = fnv1a32(id) % lookup_shards
  claimtiles/index.json, <ix>_<iy>.geojson GeoJSON tiles on a 0.5-degree grid (existing map layer)

The heavy files are rebuilt daily and published on the orphan `api-data` branch
(force-pushed, one commit) rather than main, so they never bloat git history;
both deploy workflows splice that branch into site/api before uploading Pages.
Docs + examples live in api_static/ and are copied to site/api/.
"""
import os
import csv
import glob
import gzip
import json
import math
import shutil
import datetime
import tempfile
import subprocess

API_VERSION = "v1"
BASE_URL = "https://jaydeepdive.github.io/closeology/"
API_URL = BASE_URL + "api/" + API_VERSION + "/"
TILE_DEG = 0.5
CLAIMS_MINZOOM, CLAIMS_MAXZOOM = 9, 11      # polygons (~3 m precision at z11; clients overzoom past 11)
COVERAGE_MAXZOOM = 8                         # coverage dots below the polygon zooms
SHARD_TARGET = 2500                          # ~claims per lookup shard
MAX_PMTILES_BYTES = 95 * 1024 * 1024         # GitHub's hard per-file limit is 100 MB

JURIS = {
    "AB": {"name": "Alberta", "expiry_kind": "term_end",
           "source": "Alberta Energy — metallic & industrial mineral agreements",
           "source_url": "https://gis.energy.gov.ab.ca/arcgis/rest/services/MapServices/Agreements/MapServer/4"},
    "BC": {"name": "British Columbia", "expiry_kind": "good_to",
           "source": "BC Mineral Titles Online — acquired tenures (DataBC)",
           "source_url": "https://openmaps.gov.bc.ca/geo/pub/WHSE_MINERAL_TENURE.MTA_ACQUIRED_TENURE_SVW/ows"},
    "MB": {"name": "Manitoba", "expiry_kind": "good_to",
           "source": "Manitoba mineral dispositions",
           "source_url": "https://rdmaps.gov.mb.ca/arcgis/rest/services/MapGallery/Mineral_Dispositions/MapServer/1"},
    "NB": {"name": "New Brunswick", "expiry_kind": "good_to",
           "source": "New Brunswick mineral claims (open data)",
           "source_url": "https://gis-erd-der.gnb.ca/server/rest/services/OpenData/Mineral_Claims/MapServer/0"},
    "NL": {"name": "Newfoundland and Labrador", "expiry_kind": "good_to",
           "source": "Newfoundland and Labrador GeoAtlas — mineral lands",
           "source_url": "https://dnrmaps.gov.nl.ca/arcgis/rest/services/GeoAtlas/Mineral_Lands/MapServer/0"},
    "NS": {"name": "Nova Scotia", "expiry_kind": "good_to",
           "source": "Nova Scotia NovaROC mineral rights",
           "source_url": "https://novarocmaps.novascotia.ca/arcgis/rest/services/NovaRoc/MapServer/1"},
    "NT": {"name": "Northwest Territories", "expiry_kind": "anniversary",
           "source": "GNWT Mining Recorder — mineral claims",
           "source_url": "https://www.apps.geomatics.gov.nt.ca/ArcGIS/rest/services/GNWT/Economy_LCC/MapServer/1"},
    "NU": {"name": "Nunavut", "expiry_kind": "anniversary",
           "source": "CIRNAC Nunavut mineral claims (open data)",
           "source_url": "https://geo.sac-isc.gc.ca/geomatics/rest/services/Donnees_Ouvertes-Open_Data/Claim_minier_NU_Mineral_Claim/MapServer/0"},
    "ON": {"name": "Ontario", "expiry_kind": "anniversary",
           "source": "Ontario MLAS claim map (Ministry of Mines)",
           "source_url": "https://www.geologyontario.mndm.gov.on.ca/mines/data/google/claims2/claimmap",
           "record_url": "https://www.mlas.mndm.gov.on.ca/mlas/search/searchIndex.html#/search/searchClaimDetails?claimNumber={id}",
           "note": "Ontario's public claim map publishes claim number and boundary only; holder and "
                   "anniversary date are on each claim's official MLAS abstract (record_url)."},
    "QC": {"name": "Quebec", "expiry_kind": "good_to",
           "source": "Quebec GESTIM active mining titles (SIGEOM WFS, SGM:Actifs)",
           "source_url": "https://servicesvectoriels.atlas.gouv.qc.ca/IDS_SGM_WFS/service.svc/get",
           "note": "Quebec's public active-titles feed does not publish expiry dates."},
    "SK": {"name": "Saskatchewan", "expiry_kind": "good_to",
           "source": "Saskatchewan mineral tenure — Crown dispositions",
           "source_url": "https://gis.saskatchewan.ca/arcgis/rest/services/Economy/Mineral_Tenure_Crown_Dispositions/MapServer/0"},
    "YT": {"name": "Yukon", "expiry_kind": "anniversary",
           "source": "Yukon Mining Recorder — quartz & placer claims (GeoYukon)",
           "source_url": "https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Mining/MapServer"},
}

FIELDS = [
    ("id", "string", "Claim / tenure number as issued by the jurisdiction (unique within a jurisdiction)."),
    ("jurisdiction", "string", "Province/territory code: AB BC MB NB NL NS NT NU ON QC SK YT."),
    ("holder", "string", "Registered holder(s) as published by the source, incl. % interest where given. Empty if not published."),
    ("name", "string", "Claim or property name. Empty if not published."),
    ("issued", "date", "Issue / staking / recording date, YYYY-MM-DD. Empty if not published."),
    ("expiry", "date", "Good-to, term-end or anniversary date (see jurisdictions.json expiry_kind), YYYY-MM-DD. Empty if not published."),
    ("area_ha", "number", "Area in hectares (source value, else computed in EPSG:3978)."),
    ("lon", "number", "Representative point inside the claim (WGS84)."),
    ("lat", "number", "Representative point inside the claim (WGS84)."),
    ("bbox", "array", "[min_lon, min_lat, max_lon, max_lat] of the claim polygon (WGS84)."),
    ("tile", "string", "GeoJSON tile key ix_iy holding the claim's representative point (0.5-degree grid)."),
    ("record_url", "string", "Link to the official registry record, where the jurisdiction has one."),
]

LOOKUP_KEYS = ("holder", "name", "issued", "expiry", "area_ha", "lon", "lat", "bbox")
MAX_SITE_BYTES = 960 * 1000 * 1000           # GitHub Pages hard cap is 1 GB per site

# terse GeoJSON-tile keys -> API field names (claimtiles keep short keys for size)
TILE_KEYS = {"c": "id", "j": "jurisdiction", "o": "holder", "n": "name",
             "i": "issued", "e": "expiry", "a": "area_ha"}


def fnv1a32(s):
    """32-bit FNV-1a over the UTF-8 bytes — tiny, identical in Python and JS."""
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _record_url(jur, cid):
    t = JURIS.get(jur, {}).get("record_url")
    return t.replace("{id}", cid) if t else ""


def _iter_features(tile_dir):
    for fp in glob.glob(os.path.join(tile_dir, "*.geojson")):
        try:
            d = json.load(open(fp))
        except Exception as e:
            print(f"[api] skip unreadable tile {os.path.basename(fp)}: {str(e)[:60]}")
            continue
        for ft in d.get("features", []):
            yield ft


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _tile_bounds(z, x, y):
    n = 2 ** z
    lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon0, lat0, lon1, lat1


def _verify_no_drops(pm, recs, z=10, samples=3):
    """Decode the z10 vector tiles over the densest claim areas and confirm they hold
    every claim whose bbox falls inside the tile (plus edge buffer). Raises if not —
    a vector layer that silently loses claims must never be published."""
    pts = [r for rows in recs.values() for r in rows]
    if not pts or not shutil.which("tippecanoe-decode"):
        return
    n = 2 ** z
    dens = {}
    for r in pts:
        x = int((r["lon"] + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(r["lat"]))) / math.pi) / 2 * n)
        dens[(x, y)] = dens.get((x, y), 0) + 1
    for (x, y), _ in sorted(dens.items(), key=lambda kv: -kv[1])[:samples]:
        b0, b1, b2, b3 = _tile_bounds(z, x, y)
        # claims wholly inside the tile must all be present
        expect = sum(1 for r in pts if r["bbox"][0] >= b0 and r["bbox"][2] <= b2
                     and r["bbox"][1] >= b1 and r["bbox"][3] <= b3)
        res = subprocess.run(["tippecanoe-decode", pm, str(z), str(x), str(y)],
                             capture_output=True, text=True, timeout=600)
        d = json.loads(res.stdout or "{}")
        got = sum(len(l.get("features", [])) for l in d.get("features", [])
                  if l.get("properties", {}).get("layer") == "claims")
        if got < expect:
            raise RuntimeError(f"tile {z}/{x}/{y} has {got} claims but {expect} lie inside it")
        print(f"[api] verify z{z}/{x}/{y}: {got} features in tile, {expect} claims wholly inside — ok")


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, separators=(",", ":"), ensure_ascii=False)


def build(site_dir="site", static_dir="api_static"):
    from shapely.geometry import shape

    tile_dir = os.path.join(site_dir, "claimtiles")
    out = os.path.join(site_dir, "api", API_VERSION)
    if not os.path.exists(os.path.join(tile_dir, "index.json")):
        print("[api] no claimtiles — skipping API build")
        return None
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="closeology_api_")   # scratch outside site/: never published
    tmp_nd = os.path.join(work, "claims.ndjson")
    cov_nd = os.path.join(work, "coverage.ndjson")

    seen = set()
    recs = {}                 # jur -> list of records (no geometry)
    stats = {}                # jur -> counters / bbox
    with open(tmp_nd, "w") as nd:
        for ft in _iter_features(tile_dir):
            p = ft.get("properties") or {}
            cid = str(p.get("c", "")).strip()
            jur = str(p.get("j", "")).strip().upper()
            if not cid or not jur:
                continue
            key = (jur, cid)
            if key in seen:                      # claims crossing tile edges repeat
                continue
            seen.add(key)
            try:
                geom = shape(ft["geometry"])
                rp = geom.representative_point()
                minx, miny, maxx, maxy = geom.bounds
            except Exception:
                continue
            lon, lat = round(rp.x, 5), round(rp.y, 5)
            rec = {"id": cid, "jurisdiction": jur,
                   "holder": p.get("o", "") or "", "name": p.get("n", "") or "",
                   "issued": p.get("i", "") or "", "expiry": p.get("e", "") or "",
                   "area_ha": _num(p.get("a")),
                   "lon": lon, "lat": lat,
                   "bbox": [round(minx, 5), round(miny, 5), round(maxx, 5), round(maxy, 5)],
                   "tile": f"{int(math.floor(lon / TILE_DEG))}_{int(math.floor(lat / TILE_DEG))}",
                   "record_url": _record_url(jur, cid)}
            recs.setdefault(jur, []).append(rec)
            s = stats.setdefault(jur, {"n": 0, "holder": 0, "name": 0, "issued": 0, "expiry": 0,
                                       "area_ha": 0, "bbox": [180, 90, -180, -90]})
            s["n"] += 1
            for f in ("holder", "name", "issued", "expiry"):
                s[f] += bool(rec[f])
            s["area_ha"] += rec["area_ha"] is not None
            b = s["bbox"]
            b[0], b[1], b[2], b[3] = min(b[0], minx), min(b[1], miny), max(b[2], maxx), max(b[3], maxy)
            # vector-tile feature: descriptive keys, omit empties (smaller tiles).
            # NB: no per-feature "tippecanoe" {minzoom,maxzoom} block — tippecanoe 2.49
            # silently drops most polygons when that is present. Layers are built as
            # separate tilesets with -Z/-z and merged with tile-join instead.
            # record_url is left out of the tiles (it is derivable from the jurisdiction
            # template and would add ~40 MB); closeology-claims.js fills it in.
            vp = {k: v for k, v in rec.items() if k in ("id", "jurisdiction", "holder", "name",
                                                         "issued", "expiry", "area_ha")
                  and v not in ("", None)}
            nd.write(json.dumps({"type": "Feature", "properties": vp, "geometry": ft["geometry"]},
                                separators=(",", ":"), ensure_ascii=False) + "\n")
    # zoomed-out overview: one point per populated ~5 km cell (count n)
    have_cov = False
    try:
        cov = json.load(open(os.path.join(site_dir, "claim_coverage.geojson")))
        with open(cov_nd, "w") as cf:
            for ft in cov.get("features", []):
                cf.write(json.dumps(ft, separators=(",", ":")) + "\n")
        have_cov = bool(cov.get("features"))
    except Exception as e:
        print(f"[api] coverage layer skipped: {str(e)[:80]}")

    total = sum(len(v) for v in recs.values())
    print(f"[api] {total} unique claims across {len(recs)} jurisdictions")

    # ---- vector tiles ---------------------------------------------------------
    pm = os.path.join(out, "claims.pmtiles")
    pmtiles_ok = False
    if shutil.which("tippecanoe") and shutil.which("tile-join"):
        part_c = os.path.join(work, "claims_part.pmtiles")
        part_v = os.path.join(work, "coverage_part.pmtiles")
        # NOTHING may be dropped or thinned: no feature/tile-size limits, no tiny-polygon
        # reduction, and -r1 so coverage points are not thinned at low zooms.
        steps = [["tippecanoe", "-q", "-o", part_c, "--force", "-P", "-l", "claims",
                  f"-Z{CLAIMS_MINZOOM}", f"-z{CLAIMS_MAXZOOM}",
                  "--no-feature-limit", "--no-tile-size-limit", "--no-tiny-polygon-reduction",
                  "--detect-shared-borders", "--simplification=2", tmp_nd]]
        if have_cov:
            steps.append(["tippecanoe", "-q", "-o", part_v, "--force", "-l", "coverage",
                          "-Z0", f"-z{COVERAGE_MAXZOOM}", "-r1",
                          "--no-feature-limit", "--no-tile-size-limit", cov_nd])
        steps.append(["tile-join", "-q", "-o", pm, "--force", "--no-tile-size-limit",
                      "-n", "Closeology claims",
                      "-N", "Active mineral claims across Canada (Closeology). "
                            "Layers: claims (z9-11, every claim), coverage (z0-8).",
                      "-A", "Claims: Canadian mineral tenure registries via Closeology",
                      part_c] + ([part_v] if have_cov else []))
        try:
            for cmd in steps:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
                if r.returncode != 0:
                    raise RuntimeError(f"{cmd[0]} rc={r.returncode}: {(r.stderr or '')[-300:]}")
            sz = os.path.getsize(pm)
            if sz > MAX_PMTILES_BYTES:
                raise RuntimeError(f"claims.pmtiles is {sz/1e6:.0f} MB (> GitHub 100 MB file limit)")
            _verify_no_drops(pm, recs)          # raises if the densest tile lost claims
            pmtiles_ok = True
            print(f"[api] claims.pmtiles {sz/1e6:.1f} MB (verified: no claims dropped)")
        except Exception as e:
            print(f"[api] WARNING vector tiles omitted — {str(e)[:300]} (GeoJSON tiles still served)")
            if os.path.exists(pm):
                os.remove(pm)
    else:
        print("[api] tippecanoe/tile-join not installed — vector tiles skipped (GeoJSON tiles still served)")
    shutil.rmtree(work, ignore_errors=True)

    # ---- bulk CSV + lookup shards per jurisdiction ----------------------------
    cols = ["id", "jurisdiction", "holder", "name", "issued", "expiry", "area_ha",
            "lon", "lat", "min_lon", "min_lat", "max_lon", "max_lat", "tile", "record_url"]
    juris_out = []
    os.makedirs(os.path.join(out, "claims"), exist_ok=True)
    for jur in sorted(recs):
        rows = sorted(recs[jur], key=lambda r: r["id"])
        jl = jur.lower()
        with gzip.open(os.path.join(out, "claims", f"{jl}.csv.gz"), "wt",
                       newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                b = r["bbox"]
                w.writerow([r["id"], r["jurisdiction"], r["holder"], r["name"], r["issued"],
                            r["expiry"], "" if r["area_ha"] is None else r["area_ha"],
                            r["lon"], r["lat"], b[0], b[1], b[2], b[3], r["tile"], r["record_url"]])
        nshards = max(1, int(math.ceil(len(rows) / SHARD_TARGET)))
        shards = {}
        for r in rows:
            # lean record: id is the key, jurisdiction is in the path, and tile /
            # record_url are derivable (docs + closeology-claims.js rebuild them)
            lean = {k: r[k] for k in LOOKUP_KEYS if r.get(k) not in ("", None)}
            shards.setdefault(fnv1a32(r["id"]) % nshards, {})[r["id"]] = lean
        for k in range(nshards):
            _write_json(os.path.join(out, "claim", jl, f"{k}.json"), shards.get(k, {}))
        s = stats[jur]
        n = s["n"]
        meta = JURIS.get(jur, {"name": jur, "expiry_kind": "good_to", "source": "", "source_url": ""})
        juris_out.append({
            "code": jur, "name": meta["name"], "claims": n,
            "bbox": [round(v, 4) for v in s["bbox"]],
            "expiry_kind": meta.get("expiry_kind", "good_to"),
            "field_coverage": {f: round(s[f] / n, 3) for f in ("holder", "name", "issued", "expiry", "area_ha")},
            "lookup_shards": nshards,
            "bulk_csv": f"{API_URL}claims/{jl}.csv.gz",
            "lookup_url": f"{API_URL}claim/{jl}/{{shard}}.json",
            "record_url_template": meta.get("record_url", ""),
            "source": meta.get("source", ""), "source_url": meta.get("source_url", ""),
            "note": meta.get("note", ""),
        })

    generated = datetime.date.today().isoformat()
    _write_json(os.path.join(out, "jurisdictions.json"),
                {"generated": generated, "jurisdictions": juris_out})

    # ---- MapLibre drop-in style -----------------------------------------------
    style = {
        "version": 8, "name": "Closeology claims overlay",
        "sources": {"closeology-claims": {"type": "vector", "url": "pmtiles://" + API_URL + "claims.pmtiles",
                                          "attribution": "Claims: Canadian mineral tenure registries via Closeology"}},
        "layers": [
            {"id": "closeology-coverage", "type": "circle", "source": "closeology-claims",
             "source-layer": "coverage", "maxzoom": CLAIMS_MINZOOM,
             "paint": {"circle-color": "#d4380d", "circle-radius": 2.2,
                       "circle-opacity": ["interpolate", ["linear"], ["get", "n"], 1, 0.35, 20, 0.85]}},
            {"id": "closeology-claims-fill", "type": "fill", "source": "closeology-claims",
             "source-layer": "claims", "minzoom": CLAIMS_MINZOOM,
             "paint": {"fill-color": "#d4380d", "fill-opacity": 0.18}},
            {"id": "closeology-claims-line", "type": "line", "source": "closeology-claims",
             "source-layer": "claims", "minzoom": CLAIMS_MINZOOM,
             "paint": {"line-color": "#a8071a", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.3, 14, 1.2]}},
        ],
    }
    _write_json(os.path.join(out, "claims.style.json"), style)

    # ---- manifest -------------------------------------------------------------
    index = {
        "name": "Closeology claims API", "version": API_VERSION, "generated": generated,
        "docs": BASE_URL + "api/",
        "claims": total,
        "jurisdictions": {j["code"]: j["claims"] for j in juris_out},
        "cors": "Access-Control-Allow-Origin: * (usable directly from any website)",
        "update": "Rebuilt daily from each jurisdiction's public registry.",
        "attribution": "Claims data: Canadian provincial/territorial mineral tenure registries "
                       "(see jurisdictions.json for each source), compiled by Closeology. "
                       "Always verify a claim in the official registry before staking or transacting.",
        "fields": [{"name": n, "type": t, "description": d} for n, t, d in FIELDS],
        "endpoints": {
            "manifest": API_URL + "index.json",
            "jurisdictions": API_URL + "jurisdictions.json",
            "vector_tiles": {
                "url": API_URL + "claims.pmtiles" if pmtiles_ok else None,
                "format": "PMTiles v3 (Mapbox Vector Tiles), EPSG:3857, served with HTTP range requests",
                "layers": {"claims": f"claim polygons, z{CLAIMS_MINZOOM}-{CLAIMS_MAXZOOM} (clients overzoom "
                                     f"beyond); every claim kept at every zoom. Properties: id, jurisdiction, "
                                     f"holder, name, issued, expiry, area_ha (empty ones omitted). "
                                     f"record_url = jurisdictions.json record_url_template",
                           "coverage": f"one point per ~5 km cell with staked ground, z0-{COVERAGE_MAXZOOM}; "
                                       f"property n = number of claims"},
                "maplibre_style": API_URL + "claims.style.json",
            },
            "geojson_tiles": {
                "index": BASE_URL + "claimtiles/index.json",
                "url": BASE_URL + "claimtiles/{ix}_{iy}.geojson",
                "scheme": f"ix = floor(lon / {TILE_DEG}), iy = floor(lat / {TILE_DEG}); WGS84 polygons. "
                          "Only keys listed in index.json 'tiles' exist. A claim crossing a tile edge "
                          "appears in each tile it touches.",
                "property_keys": TILE_KEYS,
            },
            "claim_lookup": {
                "url": API_URL + "claim/{jurisdiction_lower}/{shard}.json",
                "shard": "fnv1a32(utf8(id)) % lookup_shards  (lookup_shards per jurisdiction in jurisdictions.json)",
                "returns": "object keyed by claim id -> {holder, name, issued, expiry, area_ha, lon, lat, bbox}; "
                           "empty fields omitted. jurisdiction = the path; tile = floor(lon/0.5)_floor(lat/0.5); "
                           "record_url = jurisdictions.json record_url_template with {id}. "
                           "closeology-claims.js getClaim() returns the full record.",
            },
            "bulk_csv": API_URL + "claims/{jurisdiction_lower}.csv.gz",
            "coverage_geojson": BASE_URL + "claim_coverage.geojson",
            "js_helper": BASE_URL + "api/closeology-claims.js",
        },
    }
    _write_json(os.path.join(out, "index.json"), index)

    # ---- docs + examples (versioned in the repo, copied in) ------------------
    if os.path.isdir(static_dir):
        for src in glob.glob(os.path.join(static_dir, "**", "*"), recursive=True):
            if os.path.isfile(src):
                dst = os.path.join(site_dir, "api", os.path.relpath(src, static_dir))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)

    def _du(d):
        return sum(os.path.getsize(f) for f in glob.glob(os.path.join(d, "**", "*"), recursive=True)
                   if os.path.isfile(f))
    # GitHub Pages refuses sites over 1 GB. If the whole site is near the cap, shed the
    # (non-essential) per-claim lookup shards rather than let the deploy fail.
    site_bytes = _du(site_dir)
    if site_bytes > MAX_SITE_BYTES and os.path.isdir(os.path.join(out, "claim")):
        freed = _du(os.path.join(out, "claim"))
        shutil.rmtree(os.path.join(out, "claim"))
        index["endpoints"]["claim_lookup"] = None
        index["warnings"] = ["claim_lookup omitted this build: site near GitHub Pages 1 GB limit; "
                             "use bulk_csv or the tiles for claim details"]
        _write_json(os.path.join(out, "index.json"), index)
        print(f"[api] WARNING site {site_bytes/1e6:.0f} MB near Pages 1 GB cap — dropped lookup "
              f"shards ({freed/1e6:.0f} MB)")
        site_bytes -= freed
    size = _du(os.path.join(site_dir, "api"))
    print(f"[api] wrote {API_URL} — {total} claims, {len(juris_out)} jurisdictions, "
          f"vector tiles {'yes' if pmtiles_ok else 'NO'}, api {size/1e6:.0f} MB, "
          f"whole site {site_bytes/1e6:.0f} MB")
    return index


if __name__ == "__main__":
    build()
