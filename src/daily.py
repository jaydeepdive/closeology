"""Region-aware daily radar.

Headline signal = EDGE PLAYS: recent drill holes that sit on a held claim with
open, stakeable ground right beside them (see drill_edges.py). That is the only
geometry where drilled mineralization can run onto ground you could still peg —
so it is presented first and separately from the ordinary open-deposit leads.

Secondary signals:
  - BC: claims lapsing soon / newly staked near our leads (ground opening up).
  - Both: 'ground just opened' from the drop tracker; drill news (MiningNewsTerminal).
"""
import os
import json
import datetime
import pandas as pd
import geopandas as gpd
import drill_edges
import news as newsmod
import site_theme as T

VEN = os.path.join(os.path.dirname(__file__), "vendor")
LAPSE_DAYS = 180
NEW_DAYS = 150
NEAR_KM = 4.0


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

    # ---- EDGE PLAYS (recent drilling/work against open ground) ----
    gov = drill_edges.find(region_dir, metric)      # government layer (lags): OGS drill DB / BC ARIS
    nz = newsmod.find(region_dir, metric)           # fresh company news releases (real assays, dated)
    # news plays lead (freshest, actual assays); government plays follow

    def _combine(a, b):
        plays = a["plays"] + b["plays"]
        pts = a["point_feats"] + b["point_feats"]
        off = len(a["plays"])

        def shift(feats):
            return [{"type": "Feature", "geometry": f["geometry"],
                     "properties": {**f["properties"], "pidx": f["properties"]["pidx"] + off}}
                    for f in feats]
        opens = a["open_feats"] + shift(b["open_feats"])
        helds = a.get("held_feats", []) + shift(b.get("held_feats", []))
        return {"plays": plays, "point_feats": pts, "open_feats": opens, "held_feats": helds}
    edges = _combine(nz, gov)
    news_unplaced = nz["unplaced"]

    # ---- secondary activity (BC only): claim date activity near leads ----
    act_feats, a_flags, b_flags = [], [False] * len(leads), [False] * len(leads)
    claims_p = os.path.join(region_dir, "claims.parquet")
    has_claims = os.path.exists(claims_p)
    claims = gpd.read_parquet(claims_p) if has_claims else None
    has_dates = has_claims and "GOOD_TO_DATE" in claims.columns
    if has_dates:
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
    else:
        counts = dict(n_a=0, n_b=0, n_feat_a=0, n_feat_b=0)
        labels = dict(lead_a="", lead_b="", feat_a="", feat_b="", tag_a="", tag_b="", sec="Top deposit-open leads")

    lead_feats = []
    for i, (_, r) in enumerate(leads.to_crs("EPSG:4326").iterrows()):
        g = r.geometry
        lead_feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(g.x, 5), round(g.y, 5)]},
                           "properties": {"name": r["name"], "rank": int(r["rank"]), "metal": r["primary_metal"],
                                          "status": r["status"], "deposit_open": bool(r["deposit_open"]),
                                          "grade": r.get("grade_str", "") or "", "drill": r.get("drill_highlights", "") or "",
                                          "near_a": bool(a_flags[i]), "near_b": bool(b_flags[i]),
                                          "minfile": r.get("minfile", ""), "url": r.get("minfile_url", "") or "",
                                          "score": int(r.get("score", 0)), "size": r.get("deposit_size", "") or "",
                                          "spend": r.get("exploration_spend_str", "") or "", "spend_val": float(r.get("exploration_spend", 0) or 0),
                                          "operators": r.get("operators", "") or "",
                                          "community": r.get("nearest_community", ""), "community_km": r.get("community_km")}})
    news = []
    if news_path and os.path.exists(news_path):
        try:
            news = json.load(open(news_path))
        except Exception:
            news = []
    dropped = []
    dp = os.path.join(region_dir, "out", "dropped.json")
    if os.path.exists(dp):
        try:
            dropped = json.load(open(dp)).get("dropped", [])
        except Exception:
            dropped = []
    n_hot = sum(1 for p in edges["plays"] if p["hot"])
    open_ha = round(sum(p["open_ha"] for p in edges["plays"]), 0)
    # news list = fresh releases we couldn't tie to open ground + any prefetched news.json
    for u in news_unplaced:
        title = u.get("company", "") + (" — " + u["project"] if u.get("project") else "")
        summ = u.get("highlight", "")
        if u.get("note"):
            summ = (summ + " · " + u["note"]).strip(" ·")
        news.insert(0, {"title": title or "Drill news", "date": u.get("date", ""),
                        "summary": summ, "url": u.get("url", "")})
    return {"today": today.date().isoformat(), "lead_feats": lead_feats, "act": act_feats,
            "news": news, "counts": counts, "labels": labels, "dropped": dropped,
            "edges": edges["plays"], "edge_point_feats": edges["point_feats"],
            "edge_open_feats": edges["open_feats"], "edge_held_feats": edges.get("held_feats", []),
            "edge_counts": {"n": len(edges["plays"]), "hot": n_hot, "open_ha": open_ha,
                            "news": len(nz["plays"])}}


def build(region_dir, site_dir, region_name, metric, news_path=None, out_name="daily.html"):
    from config import METAL_COLOR
    d = payload(region_dir, metric, news_path)
    html = DAILY.format(
        fonts=T.FONTS, theme_css=T.THEME_CSS, header=T.header(out_name),
        leaflet_css=open(os.path.join(VEN, "leaflet.css")).read(),
        leaflet_js=open(os.path.join(VEN, "leaflet.js")).read(),
        region=region_name, today=d["today"],
        leads_json=json.dumps({"type": "FeatureCollection", "features": d["lead_feats"]}),
        act_json=json.dumps({"type": "FeatureCollection", "features": d["act"]}),
        edge_pts_json=json.dumps({"type": "FeatureCollection", "features": d["edge_point_feats"]}),
        edge_open_json=json.dumps({"type": "FeatureCollection", "features": d["edge_open_feats"]}),
        edge_held_json=json.dumps({"type": "FeatureCollection", "features": d["edge_held_feats"]}),
        edges_json=json.dumps(d["edges"]), edge_counts_json=json.dumps(d["edge_counts"]),
        news_json=json.dumps(d["news"]), metal_color_json=json.dumps(METAL_COLOR),
        labels_json=json.dumps(d["labels"]), counts_json=json.dumps(d["counts"]), near_km=NEAR_KM,
        dropped_json=json.dumps(d.get("dropped", [])),
    )
    os.makedirs(site_dir, exist_ok=True)
    outp = os.path.join(site_dir, out_name)
    open(outp, "w").write(html)
    print(f"[daily {region_name}] {outp} | edge_plays={d['edge_counts']['n']} (hot={d['edge_counts']['hot']}) "
          f"activity A={d['counts']['n_a']} B={d['counts']['n_b']} news={len(d['news'])}")
    return outp


DAILY = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Closeology Daily · {region}</title>
{fonts}
<style>{leaflet_css}</style><script>{leaflet_js}</script>
<style>{theme_css}</style>
<style>
  :root {{ --bg:#ffffff; --panel:#f5f7fa; --line:#e6e8eb; --ink:#111418; --mut:#636363; --accent:#D71920; --edge:#f97316; --hot:#ef4444; --open:#22c55e; }}
  *{{box-sizing:border-box;}} html,body{{margin:0;height:100%;font-family:'Roboto',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--ink);}}
  body{{display:flex;flex-direction:column;height:100vh;overflow:hidden;}}
  #app{{display:flex;flex:1;min-height:0;overflow:hidden;}} #map{{flex:1;}}
  #panel{{width:440px;background:#fff;border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0;}}
  #panel > header{{padding:12px 15px;border-bottom:1px solid var(--line);}} #panel header h1{{margin:0 0 2px;font-size:15px;font-family:'Bitter',serif;}}
  #panel header .sub{{color:var(--mut);font-size:11.5px;}}
  .stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:10px 15px;border-bottom:1px solid var(--line);}}
  .stat{{background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:8px;}} .stat b{{display:block;font-size:17px;font-family:'Bitter',serif;}} .stat span{{color:var(--mut);font-size:10px;}}
  .sec{{padding:9px 15px 4px;}} .sec h2{{font-size:11px;text-transform:uppercase;color:var(--mut);margin:0 0 3px;letter-spacing:.4px;font-family:'Roboto',sans-serif;font-weight:700;}}
  .sec .lead{{color:var(--mut);font-size:10.5px;margin:0 0 4px;line-height:1.45;}}
  #scroll{{flex:1;overflow:auto;}}
  .item{{padding:8px 15px;border-bottom:1px solid var(--line);font-size:12px;cursor:pointer;}} .item:hover{{background:var(--panel);}}
  .item b{{font-weight:600;}} .tag{{font-size:9px;padding:1px 5px;border-radius:4px;font-weight:700;margin-left:5px;vertical-align:middle;}}
  .t-hot{{background:var(--hot);color:#fff;}} .t-edge{{background:var(--edge);color:#fff;}} .t-news{{background:#0ea5e9;color:#fff;}} .t-a{{background:#ea580c;color:#fff;}}
  .t-b{{background:#dc2626;color:#fff;}} .t-open{{background:#e7f6ec;color:#127a3a;}}
  .edge-item{{border-left:3px solid var(--edge);}} .edge-item.hot{{border-left-color:var(--hot);}}
  .muted{{color:var(--mut);font-size:10.5px;margin-top:2px;}} .why{{color:#9a5b00;font-size:10.5px;margin-top:3px;line-height:1.4;}}
  .drill{{color:#047857;font-size:10px;margin-top:3px;}}
  a.news{{color:var(--accent);text-decoration:none;}} footer{{padding:8px 15px;font-size:9.5px;color:var(--mut);border-top:1px solid var(--line);}}
  .leaflet-popup-content{{font-size:12px;}} .leaflet-popup-content b{{color:#111;}}
  .empty{{color:var(--mut);font-size:11px;padding:2px 15px 10px;}}
  .item.sel{{background:#fdeaea;box-shadow:inset 3px 0 0 var(--accent);}}
  .legendbox{{background:rgba(255,255,255,.95);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:11px;line-height:1.7;max-width:250px;box-shadow:0 1px 6px rgba(0,0,0,.08);}}
  .legendbox b{{font-size:11px;}} .legendbox div{{display:flex;align-items:center;gap:6px;margin-top:3px;}}
  .k-open,.k-held,.k-pt{{display:inline-block;flex:0 0 auto;width:16px;height:12px;}}
  .k-open{{background:#f5a300;border:2px dashed #7c2d00;}}
  .k-held{{background:transparent;border:1.5px solid #64748b;}}
  .k-pt{{width:10px;height:10px;border-radius:50%;background:var(--accent);border:2px solid #fff;}}
  .lbl-open{{background:transparent;border:0;box-shadow:none;color:#7c2d00;font-weight:800;font-size:11px;text-shadow:0 1px 2px #fff,0 0 2px #fff;}}
  .lbl-claim{{background:rgba(255,255,255,.85);border:0;box-shadow:none;color:#111827;font-size:10px;padding:0 3px;}}
  .leaflet-tooltip.lbl-open:before,.leaflet-tooltip.lbl-claim:before{{display:none;}}
  @media (max-width:900px){{#panel{{width:100%;}}#app{{flex-direction:column;}}#map{{height:52%;}}#panel{{height:48%;}}}}
</style></head><body>
{header}
<div id="app">
<div id="map"></div>
<div id="panel">
  <header><h1>Daily Radar <span style="color:var(--accent)">·</span> {region}</h1>
    <div class="sub">{today} · fresh drilling against open ground</div></header>
  <div class="stats" id="stats"></div>
  <div id="scroll">
    <div class="sec"><h2>⚡ Edge plays — drilling on the boundary of open ground</h2>
      <p class="lead">Recent holes drilled on a held claim that <b>abut open, stakeable ground</b>. If the intersected geology continues across the line, that open cell is the play — verify structure and trend before pegging.</p>
    </div>
    <div id="edges"></div>
    <div id="dropped"></div>
    <div class="sec" id="actsec" style="display:none"></div>
    <div id="list"></div>
    <div class="sec"><h2>Drill news · MiningNewsTerminal</h2></div>
    <div id="news"></div>
  </div>
  <footer>Screening signals only — confirm every claim and boundary in the official title system before acting. Not staking advice.</footer>
</div></div>
<script>
const LEADS={leads_json}, ACT={act_json}, NEWS={news_json}, MC={metal_color_json}, LBL={labels_json}, CN={counts_json};
const DROPPED={dropped_json}, EDGES={edges_json}, EPTS={edge_pts_json}, EOPEN={edge_open_json}, EHELD={edge_held_json}, EC={edge_counts_json};
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
const mc=m=>MC[m]||'#94a3b8';
const topo=L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:17,attribution:'&copy; OpenTopoMap'}});
const map=L.map('map',{{layers:[topo]}}); const emk={{}};

// secondary BC activity (lapsing / new stake)
L.geoJSON(ACT,{{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:3,color:'#f59e0b',weight:1,fillColor:'#f59e0b',fillOpacity:.7}}),
  style:f=>({{color:f.properties.kind==='new'?'#dc2626':'#ea580c',weight:1.3,fillOpacity:.10}}),
  onEachFeature:(f,l)=>l.bindPopup(`<b>${{esc(f.properties.label)||'activity'}}</b><br>${{esc(f.properties.sub)}}`)}}).addTo(map);

// per-play detail layers (drawn only for the selected play). Colour is NOT relied
// on: STAKED = hollow outlined boxes carrying the claim number; OPEN = the one
// solid filled block, thick dashed border, labelled "OPEN — stakeable".
const heldFG=L.layerGroup().addTo(map), openFG=L.layerGroup().addTo(map);
function focusEdge(i){{
  heldFG.clearLayers(); openFG.clearLayers();
  const held={{type:'FeatureCollection',features:EHELD.features.filter(f=>f.properties.pidx===i)}};
  const open={{type:'FeatureCollection',features:EOPEN.features.filter(f=>f.properties.pidx===i)}};
  const oL=L.geoJSON(open,{{style:()=>({{color:'#7c2d00',weight:3,dashArray:'7 4',fillColor:'#f5a300',fillOpacity:.62}})}});
  oL.eachLayer(l=>l.bindTooltip('OPEN — stakeable ('+(EDGES[i]?EDGES[i].open_ha:'')+' ha)',{{permanent:true,direction:'center',className:'lbl-open'}}));
  oL.addTo(openFG);
  const hL=L.geoJSON(held,{{style:f=>({{color:'#111827',weight:f.properties.drilled?3:1.5,fill:true,fillColor:'#334155',fillOpacity:f.properties.drilled?.28:.10}}),
    onEachFeature:(f,l)=>{{if(f.properties.claim){{l.bindTooltip('claim '+esc(f.properties.claim),{{permanent:f.properties.drilled,direction:'center',className:'lbl-claim'}});}}}}}});
  hL.addTo(heldFG);
  const m=emk[i];
  try{{const grp=L.featureGroup([oL,hL].concat(m?[m]:[]));map.fitBounds(grp.getBounds().pad(0.35));}}catch(e){{if(m)map.setView(m.getLatLng(),13);}}
  if(m)m.openPopup();
  document.querySelectorAll('#edges .item').forEach(x=>x.classList.toggle('sel',+x.dataset.e===i));
}}

// leads for context (dim)
const lg=L.geoJSON(LEADS,{{pointToLayer:(f,ll)=>{{const p=f.properties;
  return L.circleMarker(ll,{{radius:4,color:'#0b1526',weight:1,fillColor:mc(p.metal),fillOpacity:.45}})
    .bindPopup(`<b>#${{p.rank}} ${{esc(p.name)}}</b> <span style=color:#64748b>${{esc(p.metal)}}</span><br>${{esc(p.status)}}${{p.deposit_open?' · <b style=color:#15803d>deposit open</b>':''}}${{p.spend?'<br>Spend: '+esc(p.spend):''}}`);}}}}).addTo(map);

// edge-play markers (the stars) — EPTS is in the same order as EDGES
let _ei=0;
L.geoJSON(EPTS,{{pointToLayer:(f,ll)=>{{const p=f.properties;const c=p.source==='News release'?'#38bdf8':(p.hot?'#ef4444':'#f97316');const idx=_ei++;
  const m=L.circleMarker(ll,{{radius:p.hot?9:7,color:'#fff7ed',weight:2,fillColor:c,fillOpacity:.95}});
  emk[idx]=m;
  m.bindPopup(`<b>${{esc(p.company)}}</b>${{p.hot?' <span style="color:#b91c1c">HOT</span>':''}}<br><span style=color:#334155>${{esc(p.property)}}</span><br>${{p.source==='News release'?('news release '+esc(p.date)):(p.n_holes+' recent effort(s), latest '+p.year)}}${{p.commodity?' · '+esc(p.commodity):''}}<br><b>${{p.open_ha}} ha open ground to the ${{esc(p.open_dir)}}</b><br>${{p.assay?'<b style=color:#b45309>Assay: '+esc(p.assay)+'</b><br>':''}}Staked claim(s): ${{esc((p.claims||[]).join(', '))}}${{p.spend?'<br>Program spend: $'+Math.round(p.spend).toLocaleString():''}}<br><a href="${{esc(p.source_url)}}" target=_blank>Source: ${{esc(p.source)}}${{p.afri?' · AFRI '+esc(p.afri):''}} ↗</a>`);
  m.on('click',()=>focusEdge(idx));
  return m;}}}}).addTo(map);

try{{const grp=L.featureGroup(Object.values(emk));map.fitBounds((EDGES.length?grp:lg).getBounds().pad(0.15));}}catch(e){{map.setView([50,-86],5);}}
if(EDGES.length)focusEdge(0);   // show the top play's staked/open detail on load

// plain-words legend (no colour reliance)
const lgd=L.control({{position:'bottomleft'}});
lgd.onAdd=function(){{const d=L.DomUtil.create('div','legendbox');
  d.innerHTML='<b>How to read this</b><div><span class=k-open></span> solid amber block = <b>OPEN ground you can stake</b></div><div><span class=k-held></span> outlined boxes = claims already staked (number shown)</div><div><span class=k-pt></span> dot = where they drilled — click any play to focus</div>';
  return d;}};
lgd.addTo(map);

// ---- stats ----
document.getElementById('stats').innerHTML=`
  <div class=stat><b style="color:#f97316">${{EC.n}}</b><span>edge plays</span></div>
  <div class=stat><b style="color:#ef4444">${{EC.hot}}</b><span>hot (last yr)</span></div>
  <div class=stat><b style="color:#22c55e">${{Math.round(EC.open_ha).toLocaleString()}}</b><span>ha open beside</span></div>`;

// ---- edge-play list ----
const eb=document.getElementById('edges');
eb.innerHTML=EDGES.length?EDGES.map((p,i)=>`<div class="item edge-item${{p.hot?' hot':''}}" data-e="${{i}}">
  <b>${{esc(p.company)}}</b>${{p.source==='News release'?'<span class="tag t-news">news</span>':''}}${{p.hot?'<span class="tag t-hot">hot</span>':'<span class="tag t-edge">edge</span>'}}
  <div class=muted>${{esc(p.property)}} · ${{p.source==='News release'?('release '+esc(p.date)):(p.n_holes+' recent effort(s), latest '+p.year)}}${{p.commodity?' · '+esc(p.commodity):''}}${{p.spend?' · $'+Math.round(p.spend).toLocaleString()+' program':''}}</div>
  ${{p.assay?`<div class=why>⛏ Assay: ${{esc(p.assay)}}</div>`:''}}
  <div class=why>▸ ${{p.open_ha}} ha open, stakeable ground to the ${{esc(p.open_dir)}} — abutting claim ${{esc((p.claims||[])[0]||'')}}</div>
  <div class=muted><a class=news href="${{esc(p.source_url)}}" target=_blank>source: ${{esc(p.source)}}${{p.afri?' · AFRI '+esc(p.afri):''}} ↗</a> · nearest lead ${{p.near_rank?('#'+p.near_rank+' '+p.near_km+'km'):'none'}}</div>
</div>`).join(''):'<div class=empty>No recent drilling/work on an open-ground boundary in this region right now. Fresh news-release assays feed in here once the news source is wired.</div>';
if(EDGES.length)focusEdge(0);   // highlight the top play now that the list exists

// ---- ground just opened (drop tracker) ----
const db=document.getElementById('dropped');
if(DROPPED.length){{db.innerHTML='<div class=sec><h2>⚑ Ground just opened ('+DROPPED.length+')</h2></div>'+DROPPED.slice(0,25).map(x=>`<div class=item><b>${{esc(x.owner)||'Claim '+esc(x.id)}}</b> dropped ${{x.area_ha}} ha${{x.good_to?' (was good to '+esc(x.good_to)+')':''}}<div class=muted>near #${{x.near_rank}} ${{esc(x.near_lead)}} · ${{x.near_km}} km</div></div>`).join('');}}

// ---- BC secondary activity list ----
const sig=LEADS.features.map(f=>f.properties).filter(p=>p.near_a||p.near_b).sort((a,b)=>a.rank-b.rank);
if(sig.length){{document.getElementById('actsec').style.display='block';
  document.getElementById('actsec').innerHTML='<h2>'+esc(LBL.sec)+' ('+sig.length+')</h2>';
  document.getElementById('list').innerHTML=sig.map(p=>`<div class=item data-r="${{p.rank}}"><b>#${{p.rank}} ${{esc(p.name)}}</b>${{p.deposit_open?'<span class="tag t-open">open</span>':''}}${{p.near_a?'<span class="tag t-a">'+esc(LBL.tag_a)+'</span>':''}}${{p.near_b?'<span class="tag t-b">'+esc(LBL.tag_b)+'</span>':''}}<div class=muted>${{esc(p.metal)}} · ${{esc(p.status)}} · ${{esc(p.community)}} ${{p.community_km!=null?p.community_km+' km':''}}</div>${{p.spend?`<div class=drill>$ ${{esc(p.spend)}} prior spend</div>`:''}}</div>`).join('');}}

// ---- drill news ----
const nb=document.getElementById('news');
nb.innerHTML=NEWS.length?NEWS.map(n=>`<div class=item>${{n.url?`<a class=news href="${{esc(n.url)}}" target=_blank>`:''}}<b>${{esc(n.title)}}</b>${{n.url?'</a>':''}}<div class=muted>${{esc(n.date||'')}} ${{n.summary?'· '+esc(n.summary):''}}</div></div>`).join(''):'<div class=empty>No drill news captured in the last run.</div>';

// ---- clicks ----
document.getElementById('edges').addEventListener('click',e=>{{const it=e.target.closest('.item[data-e]');if(!it)return;focusEdge(+it.dataset.e);}});
document.getElementById('list').addEventListener('click',e=>{{const it=e.target.closest('.item[data-r]');if(!it)return;const p=LEADS.features.find(f=>f.properties.rank==it.dataset.r);if(p){{map.setView([p.geometry.coordinates[1],p.geometry.coordinates[0]],12);}}}});
</script></body></html>
"""


if __name__ == "__main__":
    build("data/bc", "site", "British Columbia", "EPSG:3005", news_path="site/news.json", out_name="daily_bc.html")
    build("data/on", "site", "Ontario", "EPSG:3161", news_path="site/news_on.json", out_name="daily_on.html")
