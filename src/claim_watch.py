"""Claim watch — two staking signals derived from the daily claim fetch:

  1. EXPIRING SOON : active claims whose good-to date falls within the next 7 days
                     (plus a 'past good-to / may lapse' bucket). Computed straight
                     from today's claims — works on day one.
  2. RECENTLY DROPPED : claims that were active in a recent fetch but have now
                     disappeared from their jurisdiction's registry (i.e. lapsed /
                     forfeited / cancelled). Detected by diffing today's fetch
                     against a committed registry of last-known claim state, so it
                     populates going forward and is complete after ~30 days.

A single committed registry (data/keep/claim_registry.parquet) holds the
last-known state of every claim (id, jurisdiction, owner, name, expiry, centroid,
area, first/last seen). Each build upserts the claims fetched today, detects drops,
and prunes anything gone longer than the drop window. Outputs: expiring/dropped
GeoJSON + CSV/XLSX + a watch.json summary for the page and the daily email.
"""
import os
import json
import datetime

import pandas as pd
import geopandas as gpd

REG_PATH = "data/keep/claim_registry.parquet"
EXPIRE_SOON_DAYS = 7      # "expiring this week"
LAPSE_LOOKBACK_DAYS = 14  # past good-to but still active -> "may lapse"
DROP_WINDOW_DAYS = 30     # "dropped in the last ~30 days"
PRUNE_DAYS = 40           # forget claims gone longer than this
MIN_COUNT_FRACTION = 0.5  # skip drop detection if a province's count collapses (bad fetch)

# jurisdictions whose good-to date is an ANNIVERSARY (work-renewal) rather than a
# hard drop date — their "expiring" entries are softer (a claim usually survives a
# passed anniversary through a grace/work-filing window).
ANNIVERSARY_JURIS = {"nu", "nt", "yk"}


def _today():
    return datetime.date.today()


def _d2s(d):
    return d.strftime("%Y-%m-%d")


def _parse_date(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    s = s.replace("Z", "")[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def _col(df, *names):
    for n in names:
        if n in df.columns:
            return n
    return None


def _current_claims(slug):
    """Return a DataFrame of today's claims for one jurisdiction: tid, owner, name,
    expiry (date|None), issue, lon, lat, area_ha. None if no usable data."""
    fp = os.path.join("data", slug, "claims.parquet")
    if not os.path.exists(fp):
        return None
    try:
        g = gpd.read_parquet(fp)
    except Exception:
        return None
    if g is None or not len(g):
        return None
    if g.crs is None:
        g = g.set_crs("EPSG:4326")
    elif str(g.crs).upper() != "EPSG:4326":
        g = g.to_crs("EPSG:4326")
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    if not len(g):
        return None
    idc = _col(g, "TENURE_NUMBER_ID", "claim", "tenure", "NO_TITRE")
    oc = _col(g, "OWNER_NAME", "owner", "HOLDER", "TITULAIRE")
    nc = _col(g, "CLAIM_NAME", "name", "CNAME")
    ec = _col(g, "GOOD_TO_DATE", "EXPIRY_DATE", "expiry", "ANNIV_DT", "GOODSTANDI")
    ic = _col(g, "ISSUE_DATE", "issue", "STAKING_DT")
    ac = _col(g, "AREA_IN_HECTARES", "area_ha", "AREA_HA")
    n = len(g)
    rep = g.geometry.representative_point()
    try:
        area_m = g.to_crs("EPSG:3978").area
        area_ha = (area_m / 1e4).round(1).values
    except Exception:
        area_ha = [None] * n
    df = pd.DataFrame({
        "prov": slug,
        "tid": g[idc].astype(str).values if idc else [""] * n,
        "owner": (g[oc].astype(str).values if oc else [""] * n),
        "name": (g[nc].astype(str).values if nc else [""] * n),
        "expiry": [(_d2s(d) if d else "") for d in (g[ec].map(_parse_date).values if ec else [None] * n)],
        "issue": (g[ic].astype(str).str.slice(0, 10).values if ic else [""] * n),
        "lon": rep.x.round(5).values,
        "lat": rep.y.round(5).values,
        "area_ha": (g[ac].round(1).values if ac else area_ha),
    })
    # clean junk strings
    for c in ("owner", "name", "issue", "expiry"):
        df[c] = df[c].map(lambda v: "" if str(v).lower() in ("nan", "nat", "none") else str(v))
    df["tid"] = df["tid"].str.strip()
    df = df[df["tid"] != ""]
    # one row per tenure (largest area wins if duplicated across multipart)
    df = df.sort_values("area_ha", ascending=False).drop_duplicates(["prov", "tid"])
    return df.reset_index(drop=True)


def update_registry(slugs, today=None):
    """Upsert today's claims, detect drops. Returns (registry_df, meta)."""
    today = today or _today()
    tstr = _d2s(today)
    # load existing registry
    if os.path.exists(REG_PATH):
        try:
            reg = pd.read_parquet(REG_PATH)
        except Exception:
            reg = pd.DataFrame()
    else:
        reg = pd.DataFrame()
    if len(reg):
        prev_counts = reg[reg["last_seen"] != ""].groupby("prov")["tid"].count().to_dict()
    else:
        prev_counts = {}
        reg = pd.DataFrame(columns=["prov", "tid", "owner", "name", "expiry", "issue",
                                    "lon", "lat", "area_ha", "first_seen", "last_seen"])

    updated_ok, meta_counts = [], {}
    cur_frames = []
    for slug in slugs:
        cur = _current_claims(slug)
        if cur is None or not len(cur):
            meta_counts[slug] = {"today": 0, "updated": False, "reason": "no data"}
            continue
        # sanity gate: a collapsed count vs last known => likely a bad/partial fetch
        prev = prev_counts.get(slug, 0)
        if prev and len(cur) < prev * MIN_COUNT_FRACTION:
            meta_counts[slug] = {"today": int(len(cur)), "prev": int(prev),
                                 "updated": False, "reason": "count collapse — skipped"}
            continue
        updated_ok.append(slug)
        cur_frames.append(cur)
        meta_counts[slug] = {"today": int(len(cur)), "prev": int(prev), "updated": True}

    cur_all = pd.concat(cur_frames, ignore_index=True) if cur_frames else \
        pd.DataFrame(columns=["prov", "tid", "owner", "name", "expiry", "issue",
                              "lon", "lat", "area_ha"])
    cur_all["key"] = cur_all["prov"] + "|" + cur_all["tid"]

    reg = reg.copy()
    if len(reg):
        reg["key"] = reg["prov"] + "|" + reg["tid"]
    else:
        reg["key"] = pd.Series(dtype=str)
    first_seen_map = dict(zip(reg["key"], reg["first_seen"])) if len(reg) else {}

    # rows for provinces we did NOT update today: keep as-is (don't touch last_seen)
    keep_other = reg[~reg["prov"].isin(updated_ok)].copy() if len(reg) else reg
    # rows for updated provinces that are STILL present today -> refresh to today
    cur_all["first_seen"] = cur_all["key"].map(lambda k: first_seen_map.get(k, tstr))
    cur_all["last_seen"] = tstr
    present = cur_all.drop(columns=["key"])
    # rows for updated provinces that DISAPPEARED today -> keep last-known (a drop)
    if len(reg):
        gone = reg[reg["prov"].isin(updated_ok) & ~reg["key"].isin(set(cur_all["key"]))].copy()
        gone = gone.drop(columns=["key"], errors="ignore")
    else:
        gone = reg.iloc[0:0]

    _parts = [p for p in (keep_other.drop(columns=["key"], errors="ignore"), present, gone)
              if p is not None and len(p)]
    new_reg = pd.concat(_parts, ignore_index=True) if _parts else present.iloc[0:0]
    # prune claims gone longer than PRUNE_DAYS
    cutoff = _d2s(today - datetime.timedelta(days=PRUNE_DAYS))
    new_reg = new_reg[new_reg["last_seen"] >= cutoff].reset_index(drop=True)
    new_reg = new_reg.drop_duplicates(["prov", "tid"], keep="last").reset_index(drop=True)

    os.makedirs(os.path.dirname(REG_PATH), exist_ok=True)
    new_reg.to_parquet(REG_PATH, index=False)

    n_drop = int(((new_reg["last_seen"] != tstr) &
                  (new_reg["prov"].isin(updated_ok))).sum()) if len(new_reg) else 0
    meta = {"generated": tstr, "provinces": meta_counts,
            "registry_rows": int(len(new_reg)), "updated_provinces": updated_ok,
            "recent_drops_in_registry": n_drop}
    return new_reg, meta


def _feat(r, kind, today):
    exp = _parse_date(r.get("expiry"))
    days = (exp - today).days if exp else None
    p = {"tid": r["tid"], "prov": str(r["prov"]).upper(),
         "owner": _clean_owner(r.get("owner", "")), "name": r.get("name", ""),
         "expiry": r.get("expiry", ""), "area_ha": _num(r.get("area_ha")),
         "anniversary": str(r["prov"]).lower() in ANNIVERSARY_JURIS}
    if kind == "expiring":
        p["days_to_expiry"] = days
        p["bucket"] = "lapsing" if (days is not None and days < 0) else "expiring"
    else:
        p["drop_after"] = r.get("last_seen", "")   # last day it was seen active
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
            "properties": p}


def _clean_owner(o):
    return str(o or "").replace("\u0000", "").strip().rstrip(";").strip()


def _num(v):
    try:
        f = float(v)
        return round(f, 1) if f == f else None
    except Exception:
        return None


def build_outputs(reg, site_dir="site", today=None, meta=None):
    today = today or _today()
    tstr = _d2s(today)
    os.makedirs(site_dir, exist_ok=True)
    if reg is None or not len(reg):
        reg = pd.DataFrame(columns=["prov", "tid", "owner", "name", "expiry", "issue",
                                    "lon", "lat", "area_ha", "first_seen", "last_seen"])
    reg = reg.copy()
    reg["_exp"] = reg["expiry"].map(_parse_date)
    soon = today + datetime.timedelta(days=EXPIRE_SOON_DAYS)
    lapse_floor = today - datetime.timedelta(days=LAPSE_LOOKBACK_DAYS)

    is_current = reg["last_seen"] == tstr
    # expiring: active today, good-to within [today-14, today+7]
    exp_mask = is_current & reg["_exp"].map(
        lambda d: bool(d) and (lapse_floor <= d <= soon))
    expiring = reg[exp_mask].copy()
    expiring["_sort"] = expiring["_exp"].map(lambda d: (d - today).days if d else 9999)
    expiring = expiring.sort_values("_sort")

    # dropped: not seen today but seen within DROP_WINDOW, in a province updated today
    updated = set((meta or {}).get("updated_provinces", []))
    drop_floor = _d2s(today - datetime.timedelta(days=DROP_WINDOW_DAYS))
    drop_mask = (reg["last_seen"] != tstr) & (reg["last_seen"] >= drop_floor) & \
                (reg["prov"].isin(updated) if updated else False)
    dropped = reg[drop_mask].copy().sort_values("last_seen", ascending=False)

    ef = [_feat(r, "expiring", today) for _, r in expiring.iterrows()]
    df_ = [_feat(r, "dropped", today) for _, r in dropped.iterrows()]
    json.dump({"type": "FeatureCollection", "generated": tstr, "features": ef},
              open(os.path.join(site_dir, "expiring.geojson"), "w"), separators=(",", ":"))
    json.dump({"type": "FeatureCollection", "generated": tstr, "features": df_},
              open(os.path.join(site_dir, "dropped.geojson"), "w"), separators=(",", ":"))

    def _rows(feats):
        out = []
        for f in feats:
            p = dict(f["properties"]); c = f["geometry"]["coordinates"]
            p["lon"], p["lat"] = c[0], c[1]
            out.append(p)
        return pd.DataFrame(out)

    edf, ddf = _rows(ef), _rows(df_)
    if len(edf):
        edf.to_csv(os.path.join(site_dir, "expiring.csv"), index=False)
    if len(ddf):
        ddf.to_csv(os.path.join(site_dir, "dropped.csv"), index=False)
    try:
        with pd.ExcelWriter(os.path.join(site_dir, "claim_watch.xlsx")) as xw:
            (edf if len(edf) else pd.DataFrame({"note": ["none"]})).to_excel(xw, sheet_name="Expiring 7d", index=False)
            (ddf if len(ddf) else pd.DataFrame({"note": ["none"]})).to_excel(xw, sheet_name="Dropped 30d", index=False)
    except Exception as e:
        print("[claim_watch] xlsx skipped:", str(e)[:80])

    def _by_prov(df):
        return (df.groupby("prov")["tid"].count().sort_values(ascending=False).to_dict()
                if len(df) else {})

    def _top(feats, n=400):
        return [f["properties"] | {"lon": f["geometry"]["coordinates"][0],
                                   "lat": f["geometry"]["coordinates"][1]}
                for f in feats[:n]]

    watch = {"generated": tstr,
             "expiring": {"n": len(ef), "by_prov": _by_prov(edf), "items": _top(ef)},
             "dropped": {"n": len(df_), "by_prov": _by_prov(ddf), "items": _top(df_),
                         "window_days": DROP_WINDOW_DAYS,
                         "tracking_since": (meta or {}).get("tracking_since", tstr)},
             "meta": meta or {}}
    json.dump(watch, open(os.path.join(site_dir, "watch.json"), "w"), separators=(",", ":"))
    print(f"[claim_watch] expiring<=7d: {len(ef)}  dropped(30d): {len(df_)}  "
          f"registry: {len(reg)} rows")
    return watch


SINCE_PATH = "data/keep/claim_watch_since.txt"


def _tracking_since(today):
    """First date we began snapshotting (so the page can say when the 30d window
    becomes complete). Persisted in data/keep so it survives across builds."""
    try:
        if os.path.exists(SINCE_PATH):
            v = open(SINCE_PATH).read().strip()
            if v:
                return v
    except Exception:
        pass
    os.makedirs(os.path.dirname(SINCE_PATH), exist_ok=True)
    try:
        open(SINCE_PATH, "w").write(_d2s(today))
    except Exception:
        pass
    return _d2s(today)


def run(slugs, site_dir="site", today=None):
    today = today or _today()
    reg, meta = update_registry(slugs, today=today)
    meta["tracking_since"] = _tracking_since(today)
    watch = build_outputs(reg, site_dir=site_dir, today=today, meta=meta)
    try:
        build_page(site_dir=site_dir, today=today)
    except Exception as e:
        import traceback
        print("[claim_watch] page skipped:", str(e)[:120]); traceback.print_exc()
    return watch


def _esc(v):
    import html
    return html.escape(str(v if v is not None else ""))


def _maplink(p):
    return f'app.html?lat={p.get("lat")}&lon={p.get("lon")}&z=14'


def build_page(site_dir="site", today=None):
    """Render the standalone 'Claim watch' page: Expiring and Recently-dropped on
    separate tabs, each a sortable table."""
    import site_theme as T
    today = today or _today()
    tstr = _d2s(today)

    def _load(fn):
        try:
            return json.load(open(os.path.join(site_dir, fn))).get("features", [])
        except Exception:
            return []
    exp = _load("expiring.geojson")
    drp = _load("dropped.geojson")
    try:
        meta = json.load(open(os.path.join(site_dir, "watch.json"))).get("meta", {})
    except Exception:
        meta = {}
    tracking_since = meta.get("tracking_since", tstr)
    CAP = 5000

    def _prop(fe):
        p = dict(fe["properties"]); c = fe["geometry"]["coordinates"]
        p["lon"], p["lat"] = c[0], c[1]
        return p

    exp_rows = sorted((_prop(fe) for fe in exp),
                      key=lambda p: (999 if p.get("days_to_expiry") is None else p["days_to_expiry"]))
    drp_rows = sorted((_prop(fe) for fe in drp), key=lambda p: p.get("drop_after", ""), reverse=True)

    def _exp_tr(p):
        d = p.get("days_to_expiry")
        if d is None:
            when, cls, sort = "unknown", "", 999
        elif d < 0:
            when, cls, sort = f"{-d}d past · may lapse", "bad", d
        elif d <= 2:
            when, cls, sort = f"{d}d", "bad", d
        else:
            when, cls, sort = f"{d}d", "warn", d
        anni = ' <span class="muted">(anniv.)</span>' if p.get("anniversary") else ""
        own = _esc((p.get("owner") or "").rstrip("%").rstrip(" -"))
        ar = p.get("area_ha")
        return (f'<tr><td data-s="{_esc(p.get("prov"))}">{_esc(p.get("prov"))}</td>'
                f'<td data-s="{_esc(p.get("tid"))}">{_esc(p.get("tid"))}</td>'
                f'<td data-s="{own.lower()}">{own}</td>'
                f'<td data-s="{_esc(p.get("expiry"))}">{_esc(p.get("expiry"))}{anni}</td>'
                f'<td class="{cls}" data-s="{sort}">{when}</td>'
                f'<td class="r" data-s="{ar if ar is not None else -1}">{_esc(ar if ar is not None else "")}</td>'
                f'<td><a href="{_maplink(p)}">map ↗</a></td></tr>')

    def _drp_tr(p):
        own = _esc((p.get("owner") or "").rstrip("%").rstrip(" -"))
        ar = p.get("area_ha")
        return (f'<tr><td data-s="{_esc(p.get("prov"))}">{_esc(p.get("prov"))}</td>'
                f'<td data-s="{_esc(p.get("tid"))}">{_esc(p.get("tid"))}</td>'
                f'<td data-s="{own.lower()}">{own or "<span class=muted>unknown</span>"}</td>'
                f'<td data-s="{_esc(p.get("drop_after"))}">active until ~{_esc(p.get("drop_after"))}</td>'
                f'<td class="r" data-s="{ar if ar is not None else -1}">{_esc(ar if ar is not None else "")}</td>'
                f'<td><a href="{_maplink(p)}">map ↗</a></td></tr>')

    exp_body = "".join(_exp_tr(p) for p in exp_rows[:CAP]) or '<tr><td colspan=7 class=muted>No claims expiring in the next 7 days.</td></tr>'
    drp_body = "".join(_drp_tr(p) for p in drp_rows[:CAP]) or (
        '<tr><td colspan=6 class=muted>No drops recorded yet — this list fills in as '
        'claims lapse from ' + _esc(tracking_since) + ' onward (complete after ~30 days).</td></tr>')

    def byprov(rows):
        d = {}
        for r in rows:
            d[r["prov"]] = d.get(r["prov"], 0) + 1
        return ", ".join(f'{k} {v}' for k, v in sorted(d.items(), key=lambda kv: -kv[1]))

    css = T.THEME_CSS + """
.wrap{max-width:1180px;margin:0 auto;padding:22px;}
.stat{display:flex;gap:26px;flex-wrap:wrap;margin:14px 0 6px;}
.stat .k{font-family:'Bitter',serif;font-weight:800;font-size:30px;}
.stat .k.exp{color:#d97706;} .stat .k.drop{color:#0a7a3d;}
.stat .lab{color:var(--mut);font-size:12.5px;text-transform:uppercase;letter-spacing:.04em;}
.dl{font-size:13px;color:var(--mut);margin:4px 0 16px;}
.tabs{display:flex;gap:6px;border-bottom:2px solid var(--line);margin:10px 0 0;}
.tabs button{font-family:'Bitter',serif;font-weight:700;font-size:15px;padding:9px 16px;border:0;background:none;color:var(--mut);cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;}
.tabs button.on{color:var(--ink);border-bottom-color:var(--red);}
.panel{display:none;} .panel.on{display:block;}
.subhd{color:var(--mut);font-size:12.5px;margin:12px 0 2px;}
table.cw{border-collapse:collapse;width:100%;font-size:13.5px;margin:6px 0 10px;}
table.cw th,table.cw td{border-bottom:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top;}
table.cw th{font-family:'Bitter',serif;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:var(--mut);cursor:pointer;user-select:none;white-space:nowrap;position:relative;}
table.cw th:hover{color:var(--ink);}
table.cw th .ar{opacity:.35;font-size:10px;margin-left:3px;}
table.cw th.sorted .ar{opacity:1;color:var(--red);}
table.cw td.r,table.cw th.r{text-align:right;}
td.bad{color:#b00020;font-weight:700;} td.warn{color:#d97706;font-weight:700;}
.muted{color:var(--mut);}
.note{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;font-size:13px;color:var(--mut);margin:10px 0 16px;}
"""

    def _th(label, idx, num=False, r=False):
        return (f'<th data-c="{idx}"{" data-num=1" if num else ""}'
                f'{" class=r" if r else ""}>{label}<span class="ar">▴▾</span></th>')

    exp_head = ("<tr>" + _th("Jurisdiction", 0) + _th("Claim #", 1) + _th("Holder", 2)
                + _th("Good-to", 3) + _th("Expires in", 4, num=True)
                + _th("Area (ha)", 5, num=True, r=True) + "<th>Map</th></tr>")
    drp_head = ("<tr>" + _th("Jurisdiction", 0) + _th("Claim #", 1) + _th("Was held by", 2)
                + _th("Status", 3) + _th("Area (ha)", 4, num=True, r=True) + "<th>Map</th></tr>")

    html_doc = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Claim watch · Project Closeology</title>{T.FONTS}<style>{css}</style></head><body>
{T.header("watch.html")}
<div class="wrap">
  <h1>Claim watch</h1><div class="rule"></div>
  <p class="muted" style="margin-top:12px">Time-sensitive staking signals, refreshed daily ({_esc(tstr)}).
  Claims whose good-to date is almost up, and ground that has just opened because a claim lapsed.
  Always confirm in the official title system before staking.</p>
  <div class="stat">
    <div><div class="k exp">{len(exp_rows)}</div><div class="lab">Expiring &le; 7 days</div></div>
    <div><div class="k drop">{len(drp_rows)}</div><div class="lab">Dropped (last 30 days)</div></div>
  </div>
  <div class="dl">Download: <a href="expiring.csv">expiring.csv</a> · <a href="dropped.csv">dropped.csv</a> · <a href="claim_watch.xlsx">claim_watch.xlsx</a></div>

  <div class="tabs">
    <button id="tab-exp" class="on" onclick="showTab('exp')">⏳ Expiring this week ({len(exp_rows)})</button>
    <button id="tab-drop" onclick="showTab('drop')">\U0001f513 Recently dropped ({len(drp_rows)})</button>
  </div>

  <div id="panel-exp" class="panel on">
    <div class="subhd">{byprov(exp_rows) or "—"}</div>
    <table class="cw" id="exptab"><thead>{exp_head}</thead><tbody>{exp_body}</tbody></table>
  </div>

  <div id="panel-drop" class="panel">
    <div class="note">A claim counts as "dropped" once it disappears from its jurisdiction's registry (lapsed, forfeited or cancelled).
    Detection works by comparing daily snapshots, so this window fills in going forward from <b>{_esc(tracking_since)}</b> and is complete after ~30 days. There is no public feed to backfill older drops.</div>
    <div class="subhd">{byprov(drp_rows) or "—"}</div>
    <table class="cw" id="droptab"><thead>{drp_head}</thead><tbody>{drp_body}</tbody></table>
  </div>
</div>
{T.footer()}
<script>
function showTab(k){{
  document.getElementById('panel-exp').classList.toggle('on',k==='exp');
  document.getElementById('panel-drop').classList.toggle('on',k==='drop');
  document.getElementById('tab-exp').classList.toggle('on',k==='exp');
  document.getElementById('tab-drop').classList.toggle('on',k==='drop');
}}
document.querySelectorAll('table.cw th[data-c]').forEach(function(th){{
  th.onclick=function(){{
    var tb=th.closest('table').tBodies[0], ci=+th.dataset.c, num=th.dataset.num==='1';
    var dir=th._d=-(th._d||-1);
    th.closest('thead').querySelectorAll('th').forEach(function(h){{h.classList.remove('sorted');}});
    th.classList.add('sorted');
    var rows=[].slice.call(tb.rows).filter(function(r){{return r.cells.length>2;}});
    rows.sort(function(a,b){{
      var ca=a.cells[ci], cb=b.cells[ci];
      var x=ca?(ca.dataset.s!==undefined?ca.dataset.s:ca.innerText.trim()):'';
      var y=cb?(cb.dataset.s!==undefined?cb.dataset.s:cb.innerText.trim()):'';
      if(num){{x=parseFloat(x)||0;y=parseFloat(y)||0;return (x-y)*dir;}}
      return x<y?-dir:x>y?dir:0;
    }});
    rows.forEach(function(r){{tb.appendChild(r);}});
  }};
}});
</script>
</body></html>"""
    open(os.path.join(site_dir, "watch.html"), "w").write(html_doc)
    print(f"[claim_watch] watch.html — {len(exp_rows)} expiring, {len(drp_rows)} dropped")

def email_summary(site_dir="site", site_url="", n=6):
    """Compact watch block for the daily email, read from watch.json. Each item is
    a finished one-liner + an absolute map link; the email prints them verbatim."""
    try:
        w = json.load(open(os.path.join(site_dir, "watch.json")))
    except Exception:
        return {"expiring": {"n": 0, "items": []}, "dropped": {"n": 0, "items": []},
                "watch_url": (site_url or "") + "watch.html"}

    def _m(p):
        return f'{(site_url or "")}app.html?lat={p.get("lat")}&lon={p.get("lon")}&z=14'

    def _own(p):
        return (p.get("owner") or "").rstrip("%").rstrip(" -").strip()

    ex = sorted(w.get("expiring", {}).get("items", []),
                key=lambda p: (999 if p.get("days_to_expiry") is None else p["days_to_expiry"]))
    exi = []
    for p in ex[:n]:
        d = p.get("days_to_expiry")
        when = "unknown" if d is None else (f"{-d}d past good-to · may lapse" if d < 0
                                            else f"expires in {d}d")
        o = _own(p)
        exi.append({"juris": p.get("prov"), "hot": (d is not None and d <= 2),
                    "text": (o + " · " if o else "") + f'claim #{p.get("tid")} · {when}',
                    "map_url": _m(p)})
    dr = sorted(w.get("dropped", {}).get("items", []),
                key=lambda p: p.get("drop_after", ""), reverse=True)
    dri = []
    for p in dr[:n]:
        o = _own(p)
        dri.append({"juris": p.get("prov"),
                    "text": (f"was {o} · " if o else "") + f'claim #{p.get("tid")} · active until ~{p.get("drop_after")}',
                    "map_url": _m(p)})
    return {"generated": w.get("generated"),
            "watch_url": (site_url or "") + "watch.html",
            "expiring": {"n": w.get("expiring", {}).get("n", 0), "items": exi},
            "dropped": {"n": w.get("dropped", {}).get("n", 0), "items": dri,
                        "tracking_since": w.get("dropped", {}).get("tracking_since")}}
