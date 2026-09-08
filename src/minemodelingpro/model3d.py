"""3D deposit modelling from the MMP drill-hole store.

Turns a deposit's collars + assays (as collected from NI 43-101 reports and the
news drill bank) into a 3D grade model:

  1. desurvey — straight-hole desurvey of each assay interval to a 3D sample
     point (collar E/N/elev + azimuth/dip + downhole depth). Downhole survey
     stations are used when present; otherwise the hole is assumed straight.
  2. block model — inverse-distance-weighted (IDW) grade estimate on a regular
     3D block grid, keeping only blocks with enough nearby samples (no wild
     extrapolation), with a top-cut to tame outlier grades.
  3. export — a compact JSON (holes, samples, blocks, meta) the three.js viewer
     renders, and which is also a portable block-model dataset.

Coordinates are recentred to a local origin (min E/N/elev) so the numbers the
viewer handles are small; `meta.origin` records the shift to recover true UTM.

Usage:
  from minemodelingpro import model3d
  model = model3d.build_model("sedar:freeman-gold-corp_20260813T1659",
                              project="Freeman Gold — Lemhi", element="Au")
  model3d.write_viewer(model, "site/models/freeman.html")
"""
import os
import re
import json
import glob
import math
import sqlite3
import datetime

import numpy as np
import pandas as pd

from minemodelingpro import shards

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
DRILLBANK = os.path.join(_ROOT, "data", "keep", "drillbank.sqlite")


# ----------------------------------------------------------------- data loading
def _load(source_id):
    col = shards.load_table("collars", source_id)
    asy = shards.load_table("assays", source_id)
    sur = shards.load_table("survey", source_id)
    return col, asy, sur


def _clean_collars(col):
    """Keep collars with a usable easting/northing; drop coordinate outliers
    (a dropped digit in extraction puts a hole hundreds of km away)."""
    c = col.dropna(subset=["easting", "northing"]).copy()
    if c.empty:
        return c
    for ax in ("easting", "northing"):
        med = c[ax].median()
        # a real drill grid spans <~50 km; anything >100 km off the median is a
        # parse error, not a hole.
        c = c[(c[ax] - med).abs() < 100_000]
    return c


def _clean_assays(asy, element):
    a = asy[asy["element"] == element].copy()
    # drop sub-intervals ("including …" rows) so the same rock isn't counted
    # twice, and rows with no real hole id
    a = a[a.get("is_subinterval", 0).fillna(0) == 0]
    a = a[~a["native_id"].astype(str).str.strip().str.lower().isin(
        ["including", "incl", "and", "", "nan"])]
    a = a.dropna(subset=["from_m", "to_m", "grade"])
    a = a[a["to_m"] >= a["from_m"]]
    return a


# --------------------------------------------------------------------- desurvey
def _hole_survey(sur, hole_uid):
    if sur is None or sur.empty:
        return None
    s = sur[sur["hole_uid"] == hole_uid].sort_values("depth_m")
    return s if len(s) >= 2 else None


def _num_or(v, default):
    """v as float, or default when None/NaN (note: `NaN or default` returns NaN
    in Python because NaN is truthy — hence this helper)."""
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _desurvey_point(depth, collar, survey):
    """3D position at `depth` down a hole. Uses survey stations (minimum-curvature-
    lite: nearest-station az/dip, straight between) when available, else the
    collar's single az/dip (straight hole)."""
    e0, n0, z0 = collar["easting"], collar["northing"], _num_or(collar.get("elev_m"), 0.0)
    if survey is not None:
        # integrate straight segments between survey stations
        x, y, z = e0, n0, z0
        prev_d = 0.0
        st = survey[["depth_m", "azimuth", "dip"]].values
        for d, az, dip in st:
            seg = min(d, depth) - prev_d
            if seg > 0:
                az_r = math.radians(az if not np.isnan(az) else 0.0)
                dip_r = math.radians(dip)
                x += seg * math.cos(dip_r) * math.sin(az_r)
                y += seg * math.cos(dip_r) * math.cos(az_r)
                z += seg * math.sin(dip_r)
                prev_d = min(d, depth)
            if d >= depth:
                return x, y, z
        # past last station: continue with last az/dip
        az, dip = st[-1][1], st[-1][2]
        seg = depth - prev_d
        az_r = math.radians(az if not np.isnan(az) else 0.0)
        dip_r = math.radians(dip)
        return (x + seg * math.cos(dip_r) * math.sin(az_r),
                y + seg * math.cos(dip_r) * math.cos(az_r),
                z + seg * math.sin(dip_r))
    az_r = math.radians(_num_or(collar.get("azimuth"), 0.0))
    dip_r = math.radians(_num_or(collar.get("dip"), -90.0))
    return (e0 + depth * math.cos(dip_r) * math.sin(az_r),
            n0 + depth * math.cos(dip_r) * math.cos(az_r),
            z0 + depth * math.sin(dip_r))


def desurvey(col, asy, sur, element):
    """Return (holes, samples). holes: collar + toe 3D trace per hole. samples:
    one 3D midpoint per assay interval with its grade."""
    col = _clean_collars(col)
    asy = _clean_assays(asy, element)
    cmap = {r["native_id"]: r for _, r in col.iterrows()}
    holes, samples = [], []
    for _, c in col.iterrows():
        depth = c.get("depth_m") or 0.0
        surv = _hole_survey(sur, c["hole_uid"])
        top = _desurvey_point(0.0, c, surv)
        toe = _desurvey_point(depth, c, surv)
        holes.append({"id": c["native_id"], "collar": [round(v, 2) for v in top],
                      "toe": [round(v, 2) for v in toe], "depth": round(depth, 1)})
    for _, a in asy.iterrows():
        c = cmap.get(a["native_id"])
        if c is None:
            continue
        surv = _hole_survey(sur, c["hole_uid"])
        mid = (float(a["from_m"]) + float(a["to_m"])) / 2.0
        x, y, z = _desurvey_point(mid, c, surv)
        if not all(math.isfinite(v) for v in (x, y, z)):
            continue
        samples.append({"hole": a["native_id"], "from": float(a["from_m"]),
                        "to": float(a["to_m"]), "grade": float(a["grade"]),
                        "xyz": [x, y, z]})
    holes = [h for h in holes if all(math.isfinite(v) for v in h["collar"] + h["toe"])]
    return holes, samples


# ------------------------------------------------------------------ block model
def idw_block_model(samples, block=15.0, power=2.0, radius=60.0,
                    min_samples=3, top_cut=None):
    """IDW grade estimate on a regular grid. Only blocks with >= min_samples
    within `radius` are kept, so we never paint grade where there's no data."""
    if not samples:
        return [], {}
    pts = np.array([s["xyz"] for s in samples], dtype=float)
    g = np.array([s["grade"] for s in samples], dtype=float)
    ok = np.isfinite(pts).all(1)
    pts, g = pts[ok], g[ok]
    if len(pts) == 0 or not np.isfinite(pts).all():
        return [], {"block_m": block, "power": power, "radius_m": radius,
                    "min_samples": min_samples, "top_cut": top_cut, "n_blocks": 0}
    if top_cut:
        g = np.minimum(g, top_cut)
    lo = pts.min(0) - block
    hi = pts.max(0) + block
    xs = np.arange(lo[0], hi[0] + block, block)
    ys = np.arange(lo[1], hi[1] + block, block)
    zs = np.arange(lo[2], hi[2] + block, block)
    blocks = []
    r2 = radius * radius
    for x in xs:
        for y in ys:
            # quick horizontal reject: skip columns with no sample within radius
            if np.min((pts[:, 0] - x) ** 2 + (pts[:, 1] - y) ** 2) > r2:
                continue
            for z in zs:
                d2 = (pts[:, 0] - x) ** 2 + (pts[:, 1] - y) ** 2 + (pts[:, 2] - z) ** 2
                near = d2 <= r2
                if int(near.sum()) < min_samples:
                    continue
                w = 1.0 / np.power(np.maximum(d2[near], 1.0), power / 2.0)
                est = float((w * g[near]).sum() / w.sum())
                blocks.append({"xyz": [round(x, 1), round(y, 1), round(z, 1)],
                               "grade": round(est, 3), "n": int(near.sum())})
    stats = {"block_m": block, "power": power, "radius_m": radius,
             "min_samples": min_samples, "top_cut": top_cut, "n_blocks": len(blocks)}
    return blocks, stats


# ------------------------------------------------------------------------ build
_MATURITY = [(0, "Early"), (6, "Emerging"), (20, "Developing"), (60, "Detailed")]


def _maturity(n_holes, n_blocks):
    label = "Early"
    for thr, lab in _MATURITY:
        if n_holes >= thr:
            label = lab
    if n_blocks == 0:
        label += " · traces only"
    return label


def _build(col, asy, sur, source_id, project, element, jurisdiction=None,
           report_url=None, source="report", region=None, updated=None,
           sources=None, density=2.7,
           block=15.0, radius=60.0, min_samples=3, top_cut=None):
    """Core model builder shared by the 43-101 shard store and the news drill
    bank. Degrades gracefully: a sparse project yields desurveyed traces + assay
    points with few/no interpolated blocks; a dense one grows a full grade shell."""
    if col.empty or asy.empty:
        raise ValueError(f"{source_id}: need both collars and assays to model")
    holes, samples = desurvey(col, asy, sur, element)
    if not samples:
        raise ValueError(f"{source_id}: no {element} samples with locations")
    grades = np.array([s["grade"] for s in samples])
    if top_cut is None:
        top_cut = float(round(np.percentile(grades, 98), 1)) or float(grades.max())
    # sparse projects: relax the per-block sample requirement so a shell still forms
    ms = min_samples if len(samples) >= 40 else 2
    blocks, stats = idw_block_model(samples, block=block, radius=radius,
                                    min_samples=ms, top_cut=top_cut)
    allpts = ([h["collar"] for h in holes] + [h["toe"] for h in holes]
              + [s["xyz"] for s in samples] + [b["xyz"] for b in blocks])
    arr = np.array(allpts, dtype=float)
    origin = arr.min(0)

    def sh(p):
        return [round(p[0] - origin[0], 2), round(p[1] - origin[1], 2), round(p[2] - origin[2], 2)]
    for h in holes:
        h["collar"] = sh(h["collar"]); h["toe"] = sh(h["toe"])
    for s in samples:
        s["xyz"] = sh(s["xyz"])
    for b in blocks:
        b["xyz"] = sh(b["xyz"])
    return {
        "source_id": source_id,
        "project": project or source_id,
        "jurisdiction": jurisdiction, "report_url": report_url,
        "source": source, "region": region, "updated": updated,
        "sources": sources or [], "density": density,
        "maturity": _maturity(len(holes), len(blocks)),
        "element": element, "unit": (asy["unit"].dropna().iloc[0] if asy["unit"].notna().any() else "g/t"),
        "generated": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "origin": [round(float(v), 2) for v in origin],
        "extent": [round(float(v), 1) for v in (arr.max(0) - origin)],
        "counts": {"holes": len(holes), "samples": len(samples), "blocks": len(blocks)},
        "grade_stats": {"min": float(grades.min()), "max": float(grades.max()),
                        "mean": round(float(grades.mean()), 2),
                        "p50": round(float(np.percentile(grades, 50)), 2),
                        "p90": round(float(np.percentile(grades, 90)), 2), "top_cut": top_cut},
        "block_stats": stats,
        "holes": holes, "samples": samples, "blocks": blocks,
    }


def build_model(source_id, project=None, element="Au", **kw):
    """Model one 43-101 deposit from the MMP shard store."""
    col, asy, sur = _load(source_id)
    juris = (col["jurisdiction"].dropna().iloc[0] if not col.empty and col["jurisdiction"].notna().any() else None)
    rpt = (col["url"].dropna().iloc[0] if "url" in col and col["url"].notna().any() else None)
    proj = project or (col["project"].dropna().iloc[0] if not col.empty and col["project"].notna().any() else source_id)
    srcs = [{"kind": "NI 43-101 technical report", "title": proj, "url": rpt, "date": None}] if rpt else []
    return _build(col, asy, sur, source_id, proj, element, jurisdiction=juris,
                  report_url=rpt, source="report", region=juris, sources=srcs, **kw)


_VIEWER_TMPL = os.path.join(_HERE, "model3d_viewer.html")


def write_viewer(model, out_html):
    """Render the self-contained three.js viewer for a model dict."""
    tmpl = open(_VIEWER_TMPL).read()
    title = model["project"]
    # NOTE: the store's `jurisdiction` is the SEDAR *filing* province of the issuer,
    # not the deposit's physical location, so it is deliberately kept out of the
    # headline subtitle (it would misread as "the deposit is in BC").
    sub = f"{model['element']} grade shell · IDW block model"
    html = (tmpl.replace("__TITLE__", title)
                .replace("__PROJECT__", model["project"])
                .replace("__SUBTITLE__", sub)
                .replace("__MODEL_JSON__", json.dumps(model, separators=(",", ":"))))
    os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
    open(out_html, "w").write(html)
    return out_html


def build_and_render(source_id, out_html, project=None, element="Au", **kw):
    m = build_model(source_id, project=project, element=element, **kw)
    write_viewer(m, out_html)
    return m


# ------------------------------------------------- news drill bank (grows daily)
# The news bank accumulates fresh drill releases per company, so a project's model
# densifies release by release — the whole point: watch a deposit take shape
# before the company publishes an official estimate. Company names are normalised
# (headline verbs + corporate suffixes stripped) so a company's later releases
# merge into the same growing model.
_VERBS = re.compile(
    r"\b(Intersect|Confirm|Release|Report|Announce|Identif|Increase|Discover|Drill|Hit|"
    r"Extend|Provide|Expand|Return|Complete|File|Deliver|Encounter|Cut|Advance|Commence|"
    r"Continue|Update|Define|Assay|Highlight|Step|Close|Grant|Acquire|Option|Stake|Mobiliz|"
    r"Present|Show|Reveal|Outline|Intercept)\w*", re.I)


def _norm_company(name):
    s = str(name or "").strip()
    m = _VERBS.search(s)
    if m:
        s = s[:m.start()].strip()
    s = re.sub(r"[\s,]+(Ltd|Inc|Corp|Limited|Corporation|Co)\.?$", "", s, flags=re.I).strip()
    return s or str(name or "").strip()


def _cluster_latlon(lats, lons, max_km=8.0):
    """Single-linkage clustering of points within max_km (union-find). Groups
    drill holes into DEPOSITS by location, so a company's separate projects become
    separate models and releases about the same ground merge across companies."""
    n = len(lats)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    la = np.radians(np.asarray(lats, float)); lo = np.radians(np.asarray(lons, float))
    for i in range(n):
        if i + 1 >= n:
            break
        dlat = la[i + 1:] - la[i]; dlon = lo[i + 1:] - lo[i]
        h = np.sin(dlat / 2) ** 2 + np.cos(la[i]) * np.cos(la[i + 1:]) * np.sin(dlon / 2) ** 2
        d = 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
        for off in np.where(d <= max_km)[0]:
            ra, rb = find(i), find(i + 1 + int(off))
            if ra != rb:
                parent[ra] = rb
    return [find(i) for i in range(n)]


def _drillbank_groups(min_located=2, min_assays=5, cluster_km=8.0):
    """{key: {col, asy, region, updated, sources, label, ...}} — one entry per
    DEPOSIT (spatial cluster of holes), not per company. Coordinates are projected
    from lat/lon into a local metre grid centred on the cluster, so holes from
    different releases (and UTM zones) share one consistent frame."""
    if not os.path.exists(DRILLBANK):
        return {}
    conn = sqlite3.connect(DRILLBANK)
    try:
        holes = pd.read_sql_query(
            "SELECT h.release_id, h.hole_id, h.project, h.lat, h.lon, h.elev_m, h.azimuth, h.dip, "
            "h.depth_m, r.company AS r_company, r.country AS r_country, r.published AS r_pub, "
            "r.url AS r_url, r.title AS r_title FROM holes h JOIN releases r ON h.release_id=r.id", conn)
        ivs = pd.read_sql_query("SELECT * FROM intervals", conn)
    finally:
        conn.close()
    loc = holes.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    if len(loc) < min_located:
        return {}
    loc["cl"] = _cluster_latlon(loc["lat"].tolist(), loc["lon"].tolist(), cluster_km)
    loc["hk"] = loc["release_id"].astype(str) + ":" + loc["hole_id"].astype(str)
    ivs["hk"] = ivs["release_id"].astype(str) + ":" + ivs["hole_id"].astype(str)
    out = {}
    for cl, cg in loc.groupby("cl"):
        ig = ivs[ivs["hk"].isin(set(cg["hk"]))]
        if len(cg) < min_located or len(ig) < min_assays:
            continue
        lat0, lon0 = float(cg["lat"].mean()), float(cg["lon"].mean())
        mE = 111320.0 * math.cos(math.radians(lat0)); mN = 110540.0
        col = pd.DataFrame({
            "native_id": cg["hk"], "hole_uid": cg["hk"],
            "easting": (cg["lon"] - lon0) * mE, "northing": (cg["lat"] - lat0) * mN,
            "elev_m": cg["elev_m"], "azimuth": cg["azimuth"], "dip": cg["dip"], "depth_m": cg["depth_m"],
            "project": None, "jurisdiction": cg["r_country"], "url": cg["r_url"]})
        asy = pd.DataFrame({
            "native_id": ig["hk"], "hole_uid": None, "from_m": ig["from_m"], "to_m": ig["to_m"],
            "length_m": ig["length_m"], "element": ig["element"], "grade": ig["grade"],
            "unit": ig["unit"], "is_subinterval": ig["is_subinterval"]})
        projname = cg["project"].dropna().mode().iloc[0] if cg["project"].notna().any() else None
        comp = _norm_company(cg["r_company"].dropna().mode().iloc[0]) if cg["r_company"].notna().any() else None
        region = cg["r_country"].dropna().iloc[0] if cg["r_country"].notna().any() else None
        label = projname or comp or "Unnamed project"
        if not projname and comp and region:
            label = f"{comp} — {region}"
        rels = (cg[["r_url", "r_title", "r_pub"]].dropna(subset=["r_url"])
                .drop_duplicates("r_url").sort_values("r_pub", ascending=False))
        sources = [{"kind": "news release", "title": (t or u).strip()[:120], "url": u, "date": d}
                   for u, t, d in zip(rels["r_url"], rels["r_title"].fillna(""), rels["r_pub"])]
        key = (re.sub(r"[^a-z0-9]+", "-", (comp or projname or "project").lower()).strip("-")[:40]
               + f"-{lat0:.1f}_{lon0:.1f}".replace("-", "s"))
        out[key] = {"col": col, "asy": asy, "region": region,
                    "updated": (cg["r_pub"].dropna().max() if cg["r_pub"].notna().any() else None),
                    "sources": sources, "label": label, "area": f"{lat0:.2f}, {lon0:.2f}"}
    return out


def build_drillbank_model(key, g, **kw):
    col, asy = g["col"], g["asy"]
    sid = "news:" + key
    rpt = g["sources"][0]["url"] if g["sources"] else None
    order = list(asy["element"].value_counts().index) or ["Au"]
    last = None
    for el in order[:4]:                       # fall back if dominant element has no located holes
        try:
            m = _build(col, asy, None, sid, g["label"], el, jurisdiction=g["region"], report_url=rpt,
                       source="news", region=g["region"], updated=g["updated"], sources=g["sources"], **kw)
            m["area"] = g.get("area")
            return m
        except ValueError as e:
            last = e
    raise last or ValueError(f"{sid}: no modelable element")


# ---------------------------------------------------------- discover + gallery
def _dominant_element(source_id):
    a = shards.load_table("assays", source_id)
    if a.empty:
        return None
    return a["element"].value_counts().idxmax()


def discover_candidates(min_coords=8, min_assays=30):
    """Deposits with enough located collars + assays to model, from the manifest
    (fast — no data load). Returns [(source_id, collars, assays)]."""
    man = json.load(open(shards.MANIFEST))["tables"]
    def per(t):
        d = {}
        for x in man.get(t, []):
            d[x["source"]] = d.get(x["source"], 0) + x["rows"]
        return d
    C, A = per("collars"), per("assays")
    out = [(s, C.get(s, 0), A.get(s, 0)) for s in set(C) & set(A)
           if s.startswith("sedar:") and C.get(s, 0) >= min_coords and A.get(s, 0) >= min_assays]
    return sorted(out, key=lambda r: -(r[1] + r[2]))


def _slug(source_id):
    return source_id.replace("sedar:", "").replace(":", "_")[:60]


def _card(m, slug):
    return {"slug": slug, "project": m["project"], "element": m["element"], "unit": m["unit"],
            "counts": m["counts"], "grade": m["grade_stats"], "source": m.get("source", "report"),
            "region": m.get("region"), "updated": m.get("updated"), "maturity": m.get("maturity"),
            "area": m.get("area")}


def build_all(site_dir="site"):
    """Build a 3D model page for EVERY project we hold drill-hole assays on —
    the NI 43-101 deposits from the shard store AND the news drill bank (which
    grows release by release) — plus a gallery. Idempotent; safe for the daily
    pipeline. Gallery = top-level site/models.html; viewers live in site/models/."""
    out_dir = os.path.join(site_dir, "models")
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "*.html")):   # prune stale model pages
        os.remove(f)
    cards, seen = [], set()

    # (1) NI 43-101 deposits from the MMP shard store
    for sid, nc, na in discover_candidates():
        el = _dominant_element(sid) or "Au"
        slug = _slug(sid)
        try:
            m = build_and_render(sid, os.path.join(out_dir, slug + ".html"), element=el)
        except Exception as e:
            print(f"[model3d] skip {sid}: {str(e)[:90]}"); continue
        cards.append(_card(m, slug)); seen.add(slug)
        print(f"[model3d] report {m['project']}: {m['counts']['holes']}h "
              f"{m['counts']['samples']}s {m['counts']['blocks']}blk")

    # (2) news drill bank — every actively-drilled project, densifying over time
    for key, g in _drillbank_groups().items():
        try:
            m = build_drillbank_model(key, g)
            slug = _slug(m["source_id"])
            if slug in seen:
                continue
            write_viewer(m, os.path.join(out_dir, slug + ".html"))
        except Exception as e:
            print(f"[model3d] skip news:{key}: {str(e)[:90]}"); continue
        cards.append(_card(m, slug)); seen.add(slug)
        print(f"[model3d] news {m['project']}: {m['counts']['holes']}h "
              f"{m['counts']['samples']}s {m['counts']['blocks']}blk ({m['maturity']})")

    _write_gallery(cards, os.path.join(site_dir, "models.html"))
    print(f"[model3d] built {len(cards)} project model(s) -> {site_dir}/models.html")
    return cards


def _write_gallery(cards, out_html):
    """Gallery in the shared Closeology site theme (white ground, Deep Dive red,
    Bitter/Roboto, the standard top nav + footer)."""
    try:
        import site_theme as T
        head, foot, css, fonts = (T.header("models.html"), T.footer(), T.THEME_CSS, T.FONTS)
    except Exception:
        head = foot = ""; fonts = ""; css = ":root{--red:#D71920;--ink:#111418;--mut:#636363;--line:#e6e8eb;--panel:#f5f7fa;--bg:#fff;}body{font-family:Roboto,sans-serif;margin:0;background:#fff;color:#111418}h1,h3{font-family:Bitter,Georgia,serif}.wrap{max-width:1180px;margin:0 auto;padding:26px 22px 60px}a{color:#D71920;text-decoration:none}"
    def card_html(c):
        g = c["grade"]
        mat = c.get("maturity") or ""
        matcls = "mm-detail" if "Detailed" in mat else "mm-dev" if ("Developing" in mat or "Emerging" in mat) else "mm-early"
        meta = " · ".join(x for x in (c.get("region"), c.get("area"),
                          (f"updated {c['updated']}" if c.get("updated") else None)) if x)
        return f"""<a class="mcard" href="models/{c['slug']}.html">
      <div class="mtop"><div class="mchips"><span class="mel">{c['element']}</span><span class="mmat {matcls}">{mat}</span></div><h3>{c['project']}</h3>
        <div class="mmeta">{meta}</div></div>
      <div class="mstats">
        <div><b>{c['counts']['holes']}</b><span>holes</span></div>
        <div><b>{c['counts']['samples']}</b><span>assays</span></div>
        <div><b>{c['counts']['blocks']:,}</b><span>blocks</span></div>
      </div>
      <div class="mgrade">mean&nbsp;<b>{g['mean']}</b>&nbsp;· max&nbsp;<b>{g['max']}</b>&nbsp;{c['unit']}</div>
      <div class="mopen">Open 3D model →</div>
    </a>"""

    news = sorted([c for c in cards if c["source"] == "news"],
                  key=lambda c: (c.get("updated") or "", c["counts"]["samples"]), reverse=True)
    reports = sorted([c for c in cards if c["source"] != "news"],
                     key=lambda c: -c["counts"]["samples"])

    def section(title, blurb, items):
        if not items:
            return ""
        return (f'<h2 class="msec">{title}</h2><p class="msecp">{blurb}</p>'
                f'<div class="mgrid">{"".join(card_html(c) for c in items)}</div>')

    body = (section("Actively drilling", "Live models from the news drill bank — these densify release by release as companies report new holes, so you can watch a deposit take shape before an official estimate exists.", news)
            + section("From technical reports", "Models built from the collars and assays in collected NI 43-101 technical reports.", reports))
    if not (news or reports):
        body = ('<p style="color:var(--mut)">No projects with enough located drilling yet — '
                'models appear here as drill results are collected.</p>')
    extra = """
    .mhero h1{font-size:30px;letter-spacing:-.3px;}
    .mhero p{color:var(--mut);max-width:820px;}
    .msec{font-size:19px;margin:38px 0 2px;} .msecp{color:var(--mut);font-size:13px;max-width:760px;margin:0 0 4px;}
    .mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(288px,1fr));gap:16px;margin-top:16px;}
    .mcard{display:flex;flex-direction:column;gap:13px;background:#fff;border:1px solid var(--line);
      border-radius:12px;padding:18px 18px 16px;color:var(--ink);text-decoration:none;
      transition:border-color .15s,box-shadow .15s,transform .15s;}
    .mcard:hover{border-color:var(--red);box-shadow:0 8px 22px rgba(0,0,0,.07);transform:translateY(-2px);text-decoration:none;}
    .mchips{display:flex;gap:6px;align-items:center;}
    .mel{display:inline-block;font-family:'Roboto';font-size:11px;font-weight:700;letter-spacing:.08em;
      color:var(--red);background:#fdecec;padding:2px 8px;border-radius:5px;}
    .mmat{font-family:'Roboto';font-size:10.5px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:5px;}
    .mm-early{background:#eef1f6;color:#636363;} .mm-dev{background:#fff4e0;color:#8a5a00;} .mm-detail{background:#e7f4ec;color:#1c6b3f;}
    .mtop h3{font-size:19px;margin:10px 0 0;} .mmeta{font-size:11.5px;color:var(--mut);margin-top:3px;}
    .mstats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:auto;}
    .mstats div{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 9px;}
    .mstats b{display:block;font-size:18px;font-weight:700;font-family:'Bitter',serif;font-variant-numeric:tabular-nums;line-height:1.1;}
    .mstats span{font-size:9.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);}
    .mgrade{font-size:13px;color:var(--mut);font-variant-numeric:tabular-nums;}
    .mgrade b{color:var(--ink);font-weight:700;}
    .mopen{font-size:13px;font-weight:500;color:var(--red);}
    .mnote{margin-top:44px;color:var(--mut);font-size:12.5px;line-height:1.6;border-top:1px solid var(--line);padding-top:18px;}
    """
    html = f"""<title>3D deposit models</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
{fonts}
<style>{css}{extra}</style>
{head}
<div class="wrap">
  <div class="mhero">
    <h1>3D deposit models</h1>
    <div class="rule"></div>
    <p style="margin-top:14px">Every project Closeology collects drill-hole assays on, built into an interactive 3D grade model — collars and assays desurveyed into 3D and interpolated (inverse-distance) into a grade shell you can orbit, slice by cut-off grade, and inspect hole by hole. The news-bank models grow with each new release, so a deposit's shape emerges here before the company publishes an official estimate.</p>
  </div>
  {body}
  <p class="mnote">Grades are estimated by inverse-distance weighting for <b>visualization</b> — this is not a mineral resource estimate, and early-stage models are sparse by nature. Verify every figure against the source before relying on it. Coordinates are each source's own local/UTM grid; a company drilling more than one project may show separate clusters.</p>
</div>
{foot}"""
    open(out_html, "w").write(html)


if __name__ == "__main__":
    import sys
    sid = sys.argv[1] if len(sys.argv) > 1 else "sedar:freeman-gold-corp_20260813T1659"
    out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/model.html"
    m = build_and_render(sid, out, project=sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps({k: v for k, v in m.items() if k not in ("holes", "samples", "blocks")}, indent=2))
    print("viewer ->", out)
