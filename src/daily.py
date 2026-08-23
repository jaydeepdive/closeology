"""Region-aware daily radar. BC: claims lapsing soon / newly staked near leads.
Ontario: recent drilling near leads (active exploration). Both render a map
showing WHERE the flagged opportunities sit, plus drill news."""
import os
import json
import datetime
import pandas as pd
import geopandas as gpd

VEN = os.path.join(os.path.dirname(__file__), "vendor")
LAPSE_DAYS = 180
NEW_DAYS = 150
NEAR_KM = 4.0
RECENT_YRS = 5


def _pdate(s):
    return pd.to_datetime(s.astype(str).str.replace("Z", "", regex=False), format="%Y-%m-%d", errors="coerce")


def _near_flags(leads_m, feats_m):
    if feats_m is None or not len(feats_m):
        return [False] * len(leads_m)
    si = feats_m.sindex
    out = []
    for g in leads_m.geometry.values:
        halo = g.buffer(NEAR_KM * 1000)
        hit = False
        for i in si.query(halo, predicate="intersects"):
            if halo.intersects(feats_m.iloc[int(i)].geometry):
                hit = True
                break
        out.append(hit)
    return out


def payload(region_dir, metric, news_path=None):
    leads = gpd.read_file(os.path.join(region_dir, "out", "leads.geojson"))
    lm = leads.to_crs(metric)
    today = pd.Timestamp(datetime.date.today())
    claims = gpd.read_parquet(os.path.join(region_dir, "claims.parquet"))
    has_dates = "GOOD_TO_DATE" in claims.columns

    act_feats = []
    if has_dates:   # ---- BC: claim-date activity ----
        claims["good"] = _pdate(claims["GOOD_TO_DATE"])
        claims["issued"] = _pdate(claims["ISSUE_DATE"])
        lapsing = claims[(claims.good >= today) & (claims.good <= today + pd.Timedelta(days=LAPSE_DAYS))]
        newc = claims[(claims.issued >= today - pd.Timedelta(days=NEW_DAYS)) & (claims.issued <= today)]

        def near(cl):
            if not len(cl):
                return cl.iloc[0:0]
            j = gpd.sjoin_nearest(cl.to_crs(metric), gpd.GeoDataFrame(geometry=lm.geometry, crs=metric), distance_col="_d")
            return cl.loc[j[j["_d"] <= NEAR_KM * 1000].index.unique()]
        lap_n, new_n = near(lapsing), near(newc)
        a_flags = _near_flags(lm, lap_n.to_crs(metric) if len(lap_n) else None)
        b_flags = _near_flags(lm, new_n.to_crs(metric) if len(new_n) else None)
        for kind, cl in (("lapsing", lap_n), ("new", new_n)):
            for _, r in cl.to_crs("EPSG:4326").iterrows():
                if r.geometry is None:
                    continue
                act_feats.append({"type": "Feature", "geometry": r.geometry.__geo_interface__,
                                  "properties": {"kind": kind, "label": str(r.get("CLAIM_NAME") or ""),
                                                 "sub": ("good until " if kind == "lapsing" else "staked ") +
                                                        str((r["good"] if kind == "lapsing" else r["issued"]).date())}})
        counts = dict(n_a=int(sum(a_flags)), n_b=int(sum(b_flags)), n_feat_a=len(lap_n), n_feat_b=len(new_n))
        labels = dict(lead_a="leads beside ground lapsing <6mo", lead_b="leads beside fresh staking",
                      feat_a="claims lapsing soon", feat_b="claims newly staked",
                      tag_a="lapsing", tag_b="new stake", sec="Leads with claim activity nearby")
    else:            # ---- Ontario: recent-drilling activity ----
        d = gpd.read_parquet(os.path.join(region_dir, "drillholes.parquet"))
        d["YEAR_DRILLED"] = pd.to_numeric(d["YEAR_DRILLED"], errors="coerce")
        recent = d[(d.YEAR_DRILLED >= today.year - RECENT_YRS) & (d.YEAR_DRILLED <= today.year)].to_crs(metric)
        veryrecent = d[(d.YEAR_DRILLED >= today.year - 1) & (d.YEAR_DRILLED <= today.year)].to_crs(metric)
        a_flags = _near_flags(lm, recent)
        b_flags = _near_flags(lm, veryrecent)
        rr = recent.to_crs("EPSG:4326")
        # cap plotted holes for size; keep those near leads
        near_flags_holes = _near_flags  # reuse
        for _, r in rr.iterrows():
            if r.geometry is None:
                continue
            act_feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(r.geometry.x, 5), round(r.geometry.y, 5)]},
                              "properties": {"kind": "drill", "label": str(r.get("COMPANY_NAME") or "drill hole"),
                                             "sub": f"{int(r['YEAR_DRILLED'])} · {str(r.get('ELEMENTS') or '')}"[:80]}})
        counts = dict(n_a=int(sum(a_flags)), n_b=int(sum(b_flags)), n_feat_a=len(recent), n_feat_b=len(veryrecent))
        labels = dict(lead_a=f"leads with drilling < {RECENT_YRS}yr", lead_b="leads drilled in last year",
                      feat_a=f"drill holes < {RECENT_YRS}yr", feat_b="holes last year",
                      tag_a="drilled", tag_b="hot", sec="Leads with recent drilling nearby")

    lead_feats = []
    for i, (_, r) in enumerate(leads.to_crs("EPSG:4326").iterrows()):
        g = r.geometry
        lead_feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(g.x, 5), round(g.y, 5)]},
                           "properties": {"name": r["name"], "rank": int(r["rank"]), "metal": r["primary_metal"],
                                          "status": r["status"], "deposit_open": bool(r["deposit_open"]),
                                          "grade": r.get("grade_str", "") or "", "drill": r.get("drill_highlights", "") or "",
                                          "near_a": bool(a_flags[i]), "near_b": bool(b_flags[i]),
                                          "community": r.get("nearest_community", ""), "community_km": r.get("community_km")}})
    news = []
    if news_path and os.path.exists(news_path):
        try:
            news = json.load(open(news_path))
        except Exception:
            news = []
    return {"today": today.date().isoformat(), "lead_feats": lead_feats, "act": act_feats,
            "news": news, "counts": counts, "labels": labels}


def build(region_dir, site_dir, region_name, metric, news_path=None, out_name="daily.html"):
    from config import METAL_COLOR
    d = payload(region_dir, metric, news_path)
    html = DAILY.format(
        leaflet_css=open(os.path.join(VEN, "leaflet.css")).read(),
        leaflet_js=open(os.path.join(VEN, "leaflet.js")).read(),
        region=region_name, today=d["today"],
        leads_json=json.dumps({"type": "FeatureCollection", "features": d["lead_feats"]}),
        act_json=json.dumps({"type": "FeatureCollection", "features": d["act"]}),
        news_json=json.dumps(d["news"]), metal_color_json=json.dumps(METAL_COLOR),
        labels_json=json.dumps(d["labels"]), counts_json=json.dumps(d["counts"]), near_km=NEAR_KM,
    )
    os.makedirs(site_dir, exist_ok=True)
    outp = os.path.join(site_dir, out_name)
    open(outp, "w").write(html)
    print(f"[daily {region_name}] {outp} | A={d['counts']['n_a']} B={d['counts']['n_b']} news={len(d['news'])}")
    return outp


DAILY = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Closeology Daily · {region}</title>
<style>{leaflet_css}</style><script>{leaflet_js}</script>
<style>
  :root {{ --bg:#0f172a; --panel:#111c33; --line:#243352; --ink:#e5edf7; --mut:#94a3b8; --accent:#c026d3; }}
  *{{box-sizing:border-box;}} html,body{{margin:0;height:100%;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink);}}
  #app{{display:flex;height:100vh;overflow:hidden;}} #map{{flex:1;}}
  #panel{{width:420px;background:var(--panel);border-left:1px solid var(--line);display:flex;flex-direction:column;}}
  header{{padding:12px 15px;border-bottom:1px solid var(--line);}} header h1{{margin:0 0 2px;font-size:15px;}}
  header .sub{{color:var(--mut);font-size:11.5px;}}
  .stats{{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;padding:10px 15px;border-bottom:1px solid var(--line);}}
  .stat{{background:#0b1526;border:1px solid var(--line);border-radius:7px;padding:8px;}} .stat b{{display:block;font-size:17px;}} .stat span{{color:var(--mut);font-size:10px;}}
  .sec{{padding:10px 15px;border-bottom:1px solid var(--line);}} .sec h2{{font-size:11px;text-transform:uppercase;color:var(--mut);margin:0 0 7px;letter-spacing:.4px;}}
  #list{{flex:1;overflow:auto;}} .item{{padding:8px 15px;border-bottom:1px solid var(--line);font-size:12px;cursor:pointer;}} .item:hover{{background:#16223c;}}
  .item b{{font-weight:600;}} .tag{{font-size:9px;padding:1px 5px;border-radius:4px;font-weight:700;margin-left:5px;}}
  .t-a{{background:#ea580c;color:#0b1526;}} .t-b{{background:#dc2626;color:#fff;}} .t-open{{background:#16a34a;color:#04140a;}}
  .muted{{color:var(--mut);font-size:10.5px;margin-top:2px;}} .drill{{color:#a7f3d0;font-size:10px;margin-top:3px;}}
  a.news{{color:#93c5fd;text-decoration:none;}} footer{{padding:8px 15px;font-size:9.5px;color:var(--mut);border-top:1px solid var(--line);}}
  .leaflet-popup-content{{font-size:12px;}} .leaflet-popup-content b{{color:#0b1526;}}
  @media (max-width:900px){{#panel{{width:100%;}}#app{{flex-direction:column;}}#map{{height:44%;}}#panel{{height:56%;}}}}
</style></head><body><div id="app">
<div id="map"></div>
<div id="panel">
  <header><h1>Daily Radar <span style="color:var(--accent)">·</span> {region}</h1>
    <div class="sub">{today} · activity near our leads</div></header>
  <div class="stats" id="stats"></div>
  <div class="sec"><h2>Drill news</h2><div id="news"></div></div>
  <div id="list"></div>
  <footer>Screening signals only — verify each source. Not staking advice.</footer>
</div></div>
<script>
const LEADS={leads_json}, ACT={act_json}, NEWS={news_json}, MC={metal_color_json}, LBL={labels_json}, CN={counts_json};
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
const mc=m=>MC[m]||'#94a3b8';
const topo=L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:17,attribution:'&copy; OpenTopoMap'}});
const map=L.map('map',{{layers:[topo]}}); const markers={{}};
L.geoJSON(ACT,{{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:3,color:'#f59e0b',weight:1,fillColor:'#f59e0b',fillOpacity:.7}}),
  style:f=>({{color:f.properties.kind==='new'?'#dc2626':'#ea580c',weight:1.4,fillOpacity:.12}}),
  onEachFeature:(f,l)=>l.bindPopup(`<b>${{esc(f.properties.label)||'activity'}}</b><br>${{esc(f.properties.sub)}}`)}}).addTo(map);
const lg=L.geoJSON(LEADS,{{pointToLayer:(f,ll)=>{{const p=f.properties;const sig=p.near_a||p.near_b;
  const m=L.circleMarker(ll,{{radius:sig?8:5,color:sig?'#fde68a':'#0b1526',weight:sig?2:1,fillColor:mc(p.metal),fillOpacity:sig?.95:.5}});markers[p.rank]=m;
  m.bindPopup(`<b>#${{p.rank}} ${{esc(p.name)}}</b> <span style="color:#64748b">${{esc(p.metal)}}</span><br>${{esc(p.status)}}${{p.deposit_open?' · <b style=color:#15803d>deposit open</b>':''}}<br>${{p.grade?'Grade: '+esc(p.grade)+'<br>':''}}${{p.near_a?'<b style=color:#c2410c>◔ '+esc(LBL.feat_a)+' within {near_km} km</b><br>':''}}${{p.near_b?'<b style=color:#b91c1c>⚑ '+esc(LBL.feat_b)+' within {near_km} km</b><br>':''}}${{p.drill?'<span style=color:#047857>⛏ '+esc(p.drill.slice(0,120))+'…</span>':''}}`);return m;}}}}).addTo(map);
try{{map.fitBounds(lg.getBounds().pad(0.1));}}catch(e){{map.setView([50,-86],5);}}
document.getElementById('stats').innerHTML=`
  <div class=stat><b style="color:#f59e0b">${{CN.n_a}}</b><span>${{esc(LBL.lead_a)}}</span></div>
  <div class=stat><b style="color:#f87171">${{CN.n_b}}</b><span>${{esc(LBL.lead_b)}}</span></div>
  <div class=stat><b>${{CN.n_feat_a.toLocaleString()}}</b><span>${{esc(LBL.feat_a)}} (near leads)</span></div>
  <div class=stat><b>${{CN.n_feat_b.toLocaleString()}}</b><span>${{esc(LBL.feat_b)}} (near leads)</span></div>`;
const nb=document.getElementById('news');
nb.innerHTML=NEWS.length?NEWS.map(n=>`<div class=item>${{n.url?`<a class=news href="${{esc(n.url)}}" target=_blank>`:''}}<b>${{esc(n.title)}}</b>${{n.url?'</a>':''}}<div class=muted>${{esc(n.date||'')}} ${{n.summary?'· '+esc(n.summary):''}}</div></div>`).join(''):'<div class=muted>No drill news captured in the last run.</div>';
const sig=LEADS.features.map(f=>f.properties).filter(p=>p.near_a||p.near_b).sort((a,b)=>a.rank-b.rank);
document.getElementById('list').innerHTML='<div class=sec><h2>'+esc(LBL.sec)+' ('+sig.length+')</h2></div>'+sig.map(p=>`<div class=item data-r="${{p.rank}}"><b>#${{p.rank}} ${{esc(p.name)}}</b>${{p.deposit_open?'<span class="tag t-open">open</span>':''}}${{p.near_a?'<span class="tag t-a">'+esc(LBL.tag_a)+'</span>':''}}${{p.near_b?'<span class="tag t-b">'+esc(LBL.tag_b)+'</span>':''}}<div class=muted>${{esc(p.metal)}} · ${{esc(p.status)}} · ${{esc(p.community)}} ${{p.community_km!=null?p.community_km+' km':''}}</div>${{p.drill?`<div class=drill>⛏ ${{esc(p.drill.slice(0,110))}}…</div>`:''}}</div>`).join('');
document.getElementById('list').addEventListener('click',e=>{{const it=e.target.closest('.item[data-r]');if(!it)return;const m=markers[it.dataset.r];if(m){{map.setView(m.getLatLng(),12);m.openPopup();}}}});
</script></body></html>
"""


if __name__ == "__main__":
    build("data/bc", "site", "British Columbia", "EPSG:3005", news_path="site/news.json", out_name="daily_bc.html")
    build("data/on", "site", "Ontario", "EPSG:3161", news_path="site/news_on.json", out_name="daily_on.html")
