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
import json
import math
import datetime

import numpy as np
import pandas as pd

from minemodelingpro import shards

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))


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


def _desurvey_point(depth, collar, survey):
    """3D position at `depth` down a hole. Uses survey stations (minimum-curvature-
    lite: nearest-station az/dip, straight between) when available, else the
    collar's single az/dip (straight hole)."""
    e0, n0, z0 = collar["easting"], collar["northing"], collar.get("elev_m") or 0.0
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
    az = collar.get("azimuth")
    dip = collar.get("dip")
    az_r = math.radians(az if (az is not None and not np.isnan(az)) else 0.0)
    dip_r = math.radians(dip if (dip is not None and not np.isnan(dip)) else -90.0)
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
        samples.append({"hole": a["native_id"], "from": float(a["from_m"]),
                        "to": float(a["to_m"]), "grade": float(a["grade"]),
                        "xyz": [x, y, z]})
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
def build_model(source_id, project=None, element="Au", block=15.0,
                radius=60.0, min_samples=3, top_cut=None):
    col, asy, sur = _load(source_id)
    if col.empty or asy.empty:
        raise ValueError(f"{source_id}: need both collars and assays to model")
    holes, samples = desurvey(col, asy, sur, element)
    if not samples:
        raise ValueError(f"{source_id}: no {element} samples with locations")
    grades = np.array([s["grade"] for s in samples])
    if top_cut is None:
        top_cut = float(round(np.percentile(grades, 98), 1))
    blocks, stats = idw_block_model(samples, block=block, radius=radius,
                                    min_samples=min_samples, top_cut=top_cut)
    # recentre to a local origin for the viewer
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
    juris = (col["jurisdiction"].dropna().iloc[0] if col["jurisdiction"].notna().any() else None)
    rpt = (col["url"].dropna().iloc[0] if "url" in col and col["url"].notna().any() else None)
    return {
        "source_id": source_id,
        "project": project or (col["project"].dropna().iloc[0] if col["project"].notna().any() else source_id),
        "jurisdiction": juris, "report_url": rpt,
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


def build_all(site_dir="site"):
    """Build a 3D model page for every viable deposit + a gallery. Idempotent;
    safe to run in the daily pipeline. Gallery is the top-level site/models.html
    (so the shared nav's relative links resolve); viewers live in site/models/."""
    out_dir = os.path.join(site_dir, "models")
    os.makedirs(out_dir, exist_ok=True)
    cards = []
    for sid, nc, na in discover_candidates():
        el = _dominant_element(sid) or "Au"
        slug = _slug(sid)
        try:
            m = build_and_render(sid, os.path.join(out_dir, slug + ".html"), element=el)
        except Exception as e:
            print(f"[model3d] skip {sid}: {str(e)[:90]}")
            continue
        cards.append({"slug": slug, "project": m["project"], "element": m["element"],
                      "unit": m["unit"], "counts": m["counts"], "grade": m["grade_stats"],
                      "extent": m["extent"]})
        print(f"[model3d] {m['project']}: {m['counts']['holes']}h {m['counts']['samples']}s "
              f"{m['counts']['blocks']}blk")
    _write_gallery(cards, os.path.join(site_dir, "models.html"))
    print(f"[model3d] built {len(cards)} deposit model(s) -> {site_dir}/models.html")
    return cards


def _write_gallery(cards, out_html):
    """Gallery in the shared Closeology site theme (white ground, Deep Dive red,
    Bitter/Roboto, the standard top nav + footer)."""
    try:
        import site_theme as T
        head, foot, css, fonts = (T.header("models.html"), T.footer(), T.THEME_CSS, T.FONTS)
    except Exception:
        head = foot = ""; fonts = ""; css = ":root{--red:#D71920;--ink:#111418;--mut:#636363;--line:#e6e8eb;--panel:#f5f7fa;--bg:#fff;}body{font-family:Roboto,sans-serif;margin:0;background:#fff;color:#111418}h1,h3{font-family:Bitter,Georgia,serif}.wrap{max-width:1180px;margin:0 auto;padding:26px 22px 60px}a{color:#D71920;text-decoration:none}"
    cards = sorted(cards, key=lambda c: -c["counts"]["samples"])
    rows = []
    for c in cards:
        g = c["grade"]
        rows.append(f"""<a class="mcard" href="models/{c['slug']}.html">
      <div class="mtop"><span class="mel">{c['element']}</span><h3>{c['project']}</h3></div>
      <div class="mstats">
        <div><b>{c['counts']['holes']}</b><span>holes</span></div>
        <div><b>{c['counts']['samples']}</b><span>assays</span></div>
        <div><b>{c['counts']['blocks']:,}</b><span>blocks</span></div>
      </div>
      <div class="mgrade">mean&nbsp;<b>{g['mean']}</b>&nbsp;· max&nbsp;<b>{g['max']}</b>&nbsp;{c['unit']}</div>
      <div class="mopen">Open 3D model →</div>
    </a>""")
    grid = ("".join(rows) if rows else
            '<p style="color:var(--mut)">No deposits with enough located drilling yet — '
            'models appear here as technical reports with drill appendices are collected.</p>')
    extra = """
    .mhero h1{font-size:30px;letter-spacing:-.3px;}
    .mhero p{color:var(--mut);max-width:780px;}
    .mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(288px,1fr));gap:16px;margin-top:26px;}
    .mcard{display:flex;flex-direction:column;gap:14px;background:#fff;border:1px solid var(--line);
      border-radius:12px;padding:18px 18px 16px;color:var(--ink);text-decoration:none;
      transition:border-color .15s,box-shadow .15s,transform .15s;}
    .mcard:hover{border-color:var(--red);box-shadow:0 8px 22px rgba(0,0,0,.07);transform:translateY(-2px);text-decoration:none;}
    .mtop .mel{display:inline-block;font-family:'Roboto';font-size:11px;font-weight:700;letter-spacing:.08em;
      color:var(--red);background:#fdecec;padding:2px 8px;border-radius:5px;}
    .mtop h3{font-size:19px;margin:10px 0 0;}
    .mstats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;}
    .mstats div{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 9px;}
    .mstats b{display:block;font-size:18px;font-weight:700;font-family:'Bitter',serif;
      font-variant-numeric:tabular-nums;line-height:1.1;}
    .mstats span{font-size:9.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--mut);}
    .mgrade{font-size:13px;color:var(--mut);font-variant-numeric:tabular-nums;}
    .mgrade b{color:var(--ink);font-weight:700;}
    .mopen{font-size:13px;font-weight:500;color:var(--red);margin-top:2px;}
    .mnote{margin-top:40px;color:var(--mut);font-size:12.5px;line-height:1.6;border-top:1px solid var(--line);padding-top:18px;}
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
    <p style="margin-top:14px">Interactive block models built automatically from the NI 43-101 drill data Closeology collects. Each report's collars and assays are desurveyed into 3D and interpolated (inverse-distance) into a grade shell you can orbit, slice by cut-off grade, and inspect hole by hole.</p>
  </div>
  <div class="mgrid">{grid}</div>
  <p class="mnote">Grades are estimated by inverse-distance weighting for <b>visualization</b> — this is not a mineral resource estimate. Verify every figure against the source technical report before relying on it. Coordinates are each report's own local/UTM grid.</p>
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
