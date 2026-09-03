"""Drill Radar — Project Closeology surface over the newswire drill bank.

Reads data/keep/drillbank.sqlite and builds site/drill_radar.html: a map + table
of recent, geolocated drill results (company, project, best intercept, location,
link to the release), and flags results that sit on or beside open ground so
they can seed Closeology leads. Also writes site/drill_radar.json for reuse.

Runs after the newswire crawl in build_all; safe to build from an empty bank."""
import os
import re
import json
import sqlite3
import site_theme as T

_VEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")


def _ven(p):
    try:
        return open(os.path.join(_VEN, p)).read()
    except Exception:
        return ""

DB = os.path.join("data", "keep", "drillbank.sqlite")
# rough in-situ value weights ($/unit) just to rank "best" intercepts across
# elements — NOT a resource estimate, only for sorting what to show first
_VAL = {"Au": 75.0, "AuEq": 75.0, "Ag": 0.9, "Pd": 40.0, "Pt": 35.0, "Cu": 90.0,
        "Ni": 180.0, "Co": 300.0, "Zn": 28.0, "Pb": 20.0, "U": 130.0, "U3O8": 110.0,
        "Li": 90.0, "Li2O": 90.0, "Sn": 250.0, "Mo": 400.0, "CuEq": 90.0, "AgEq": 0.9}


def _score_interval(el, grade, length, unit):
    if grade is None or length is None:
        return 0.0
    v = _VAL.get(el, 5.0)
    g = grade / 100.0 if unit == "%" else (grade / 1000.0 if unit == "ppm" else grade)
    return v * g * length


def _load(con):
    rows = con.execute("""
        SELECT r.id,r.source,r.url,r.company,r.title,r.published,r.country,r.project,
               r.n_holes,r.n_intervals
        FROM releases r WHERE r.status='ok'
        ORDER BY COALESCE(r.published,'') DESC, r.fetched_at DESC""").fetchall()
    items = []
    for (rid, source, url, company, title, pub, country, project, nh, ni) in rows:
        holes = con.execute("SELECT hole_id,lat,lon,depth_m,azimuth,dip FROM holes "
                            "WHERE release_id=? AND lat IS NOT NULL", (rid,)).fetchall()
        ivs = con.execute("SELECT hole_id,from_m,to_m,length_m,element,grade,unit,is_subinterval "
                          "FROM intervals WHERE release_id=?", (rid,)).fetchall()
        best, best_s = None, -1
        for (hid, fr, to, ln, el, gr, un, sub) in ivs:
            if sub:
                continue
            sc = _score_interval(el, gr, ln, un)
            if sc > best_s:
                best_s = sc
                best = {"hole": hid, "len": ln, "el": el, "grade": gr, "unit": un}
        pt = None
        if holes:
            pt = {"lat": holes[0][1], "lon": holes[0][2], "n": len(holes)}
        items.append({"id": rid, "source": source, "url": url,
                      "company": company or (title or "")[:60], "title": title,
                      "published": pub, "country": country, "n_holes": nh,
                      "n_intervals": ni, "best": best, "pt": pt,
                      "geo": bool(holes)})
    return items


def build(site_dir="site"):
    from newswire import store as _store
    if not os.path.exists(_store.DB_PATH):
        _write(site_dir, [], {"releases": 0})
        return
    con = sqlite3.connect(_store.DB_PATH)
    try:
        items = _load(con)
        st = _store.stats(con)
    finally:
        con.close()
    _write(site_dir, items, st)


def _best_str(b):
    if not b:
        return ""
    g = f"{b['grade']:g}".rstrip("0").rstrip(".") if b.get("grade") is not None else "?"
    ln = f"{b['len']:g}" if b.get("len") is not None else "?"
    return f"{ln} m @ {g} {b['unit']} {b['el']}"


def _write(site_dir, items, st):
    os.makedirs(site_dir, exist_ok=True)
    geo = [i for i in items if i["geo"] and i["pt"]]
    json.dump({"generated_items": len(items), "geolocated": len(geo), "stats": st,
               "items": items[:1000]},
              open(os.path.join(site_dir, "drill_radar.json"), "w"))
    pts = [{"lat": i["pt"]["lat"], "lon": i["pt"]["lon"], "c": i["company"],
            "b": _best_str(i["best"]), "u": i["url"], "n": i["pt"]["n"]} for i in geo[:1500]]
    rows = "".join(
        f'<tr><td class=d>{(i["published"] or "")[:10]}</td><td>{_esc(i["company"])}'
        f'<div class=t>{_esc((i["title"] or "")[:110])}</div></td>'
        f'<td>{_esc(i["country"] or "")}</td>'
        f'<td class=b>{_esc(_best_str(i["best"]))}</td>'
        f'<td class=n>{i["n_holes"] or "—"}</td>'
        f'<td>{"📍" if i["geo"] else ""}</td>'
        f'<td><a href="{_esc(i["url"])}" target=_blank rel=noopener>release ↗</a></td></tr>'
        for i in items[:400])
    empty = ("<p class=note>The drill bank is still filling — the daily crawl adds new "
             "mining releases each build. Come back after the next run.</p>" if not items else "")
    html = _PAGE.format(fonts=T.FONTS, css=T.THEME_CSS, header=T.header("drill_radar.html"),
                        footer=T.footer(), rows=rows, empty=empty,
                        n=len(items), geo=len(geo),
                        holes=st.get("holes", 0), ivals=st.get("intervals", 0),
                        leaflet_css=_ven("leaflet.css"), leaflet_js=_ven("leaflet.js"),
                        pts=json.dumps(pts, separators=(",", ":")))
    open(os.path.join(site_dir, "drill_radar.html"), "w").write(html)
    print(f"[drill_radar] drill_radar.html — {len(items)} releases, {len(geo)} geolocated, "
          f"{st.get('holes',0)} holes, {st.get('intervals',0)} intervals")


def _esc(s):
    return re.sub(r"[<>&]", lambda m: {"<": "&lt;", ">": "&gt;", "&": "&amp;"}[m.group()], str(s or ""))


_PAGE = r"""<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Drill Radar · Project Closeology</title>{fonts}
<style>{leaflet_css}</style><script>{leaflet_js}</script>
<style>{css}
.wrap2{{max-width:1180px;margin:0 auto;padding:20px 22px 60px;}}
.kpis{{display:flex;gap:26px;margin:8px 0 16px;flex-wrap:wrap;}}
.kpis div b{{font-family:'Bitter',serif;font-size:22px;color:var(--red);display:block;}}
.kpis div span{{font-size:12px;color:var(--mut);}}
#dmap{{height:420px;border:1px solid var(--line);border-radius:12px;margin-bottom:18px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;}}
th{{font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);}}
td.d{{white-space:nowrap;color:var(--mut);}} td.b{{font-weight:600;}} td.n{{text-align:center;}}
.t{{color:var(--mut);font-size:11.5px;margin-top:2px;}}
.note{{color:var(--mut);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;}}
.lead2{{color:var(--mut);max-width:820px;}}
</style></head><body>
{header}
<div class="wrap2">
  <div class="hero"><h1>Drill Radar</h1><div class="rule"></div>
  <p class="lead2">Fresh drill-hole results pulled from the mining newswires, parsed for
  collar coordinates and assay intercepts, and placed on the map where the geology allows.
  Feeds Project Closeology's open-ground screen and banks every collar + assay for
  MineModelingPro deposit modelling.</p></div>
  <div class="kpis">
    <div><b>{n}</b><span>releases parsed</span></div>
    <div><b>{geo}</b><span>geolocated</span></div>
    <div><b>{holes}</b><span>drill holes banked</span></div>
    <div><b>{ivals}</b><span>assay intervals</span></div>
  </div>
  {empty}
  <div id=dmap></div>
  <table><thead><tr><th>Date</th><th>Company / release</th><th>Region</th>
    <th>Best intercept</th><th>Holes</th><th>Map</th><th></th></tr></thead>
  <tbody>{rows}</tbody></table>
</div>
{footer}
<script>
const PTS={pts};
const map=L.map('dmap').setView([58,-96],3);
L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:17,attribution:'&copy; OpenTopoMap'}}).addTo(map);
const g=[];
PTS.forEach(p=>{{const m=L.circleMarker([p.lat,p.lon],{{radius:6,color:'#7a1620',weight:1,fillColor:'#D71920',fillOpacity:.7}});
  m.bindPopup(`<b>${{p.c}}</b><br>${{p.b||''}}<br>${{p.n}} hole(s) located<br><a href="${{p.u}}" target=_blank>release ↗</a>`);
  m.addTo(map); g.push([p.lat,p.lon]);}});
if(g.length) map.fitBounds(g,{{padding:[30,30],maxZoom:9}});
</script></body></html>
"""
