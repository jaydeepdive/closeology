"""Build ONE self-contained two-region app (site/app.html + app_artifact.html):
region switch (BC / Ontario) x tabs (Overview / Map / Daily Radar).
Occurrence coords are trimmed to keep the file within the artifact size limit."""
import os
import re
import json
import daily
from build_map import BC_WMS

VEN = os.path.join(os.path.dirname(__file__), "vendor")


def _r(p):
    return open(os.path.join(VEN, p)).read()


def _round(gj_str, dp=4, keep=None):
    """Parse a geojson string, round coords, optionally keep only some props."""
    d = json.loads(gj_str)

    def rnd(x):
        if isinstance(x, list):
            return [rnd(v) for v in x]
        if isinstance(x, float):
            return round(x, dp)
        return x
    for f in d.get("features", []):
        if f.get("geometry"):
            f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
        if keep is not None:
            f["properties"] = {k: f["properties"].get(k) for k in keep if k in f["properties"]}
    return d


def _region(bc_dir, slug, metric, news):
    out = os.path.join(bc_dir, "out")
    stats = json.load(open(os.path.join(out, "stats.json")))
    leads = json.loads(open(os.path.join(out, "leads.geojson")).read())
    occ = _round(open(os.path.join(out, "occurrences_all.geojson")).read(), 4, keep=["n", "st", "p"])
    cells = _round(open(os.path.join(out, "opencells.geojson")).read(), 4, keep=["rank", "name"]) \
        if os.path.exists(os.path.join(out, "opencells.geojson")) else {"type": "FeatureCollection", "features": []}
    cnp = os.path.join(out, "claims_near.geojson")
    claims = _round(open(cnp).read(), 4, keep=["claim"]) if os.path.exists(cnp) else {"type": "FeatureCollection", "features": []}
    dp = daily.payload(bc_dir, metric, news)
    return {"slug": slug, "name": stats["region"], "stats": stats, "leads": leads, "occ": occ,
            "cells": cells, "claims": claims, "use_wms": slug == "bc", "daily": dp}


def build(regions_cfg, html_path):
    from config import METAL_COLOR, METAL_ORDER
    regions = {rc["slug"]: _region(rc["dir"], rc["slug"], rc["metric"], rc.get("news")) for rc in regions_cfg}
    gen = list(regions.values())[0]["stats"].get("generated", "")
    html = TEMPLATE.format(
        leaflet_css=_r("leaflet.css"), mc_css=_r("mc.css") + _r("mcd.css"),
        leaflet_js=_r("leaflet.js"), mc_js=_r("mc.js"),
        regions_json=json.dumps(regions), metal_color=json.dumps(METAL_COLOR),
        metal_order=json.dumps(METAL_ORDER), generated=gen,
        wms_url=BC_WMS["url"], wms_layer=BC_WMS["layer"], wms_attr=BC_WMS["attr"],
        f_name=BC_WMS["fields"]["name"], f_id=BC_WMS["fields"]["id"], f_type=BC_WMS["fields"]["type"],
        f_area=BC_WMS["fields"]["area"], f_owner=BC_WMS["fields"]["owner"], f_good=BC_WMS["fields"]["good"],
    )
    open(html_path, "w").write(html)
    body = html
    for pat in [r'<!DOCTYPE html>', r'<html[^>]*>', r'</html>', r'<head>', r'</head>',
                r'<body>', r'</body>', r'<meta[^>]*>', r'<title>.*?</title>']:
        body = re.sub(pat, '', body, flags=re.I | re.S)
    open(html_path.replace('.html', '_artifact.html'), "w").write(body.strip())
    print(f"[app] {html_path} ({os.path.getsize(html_path)//1024} KB) + artifact variant")
    return html_path


TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Project Closeology</title>
<style>{leaflet_css}</style><style>{mc_css}</style>
<script>{leaflet_js}</script><script>{mc_js}</script>
<style>
  :root{{--bg:#0b1220;--panel:#111c33;--line:#243352;--ink:#e5edf7;--mut:#94a3b8;--accent:#c026d3;}}
  *{{box-sizing:border-box;}} html,body{{margin:0;height:100%;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink);}}
  #shell{{display:flex;flex-direction:column;height:100vh;overflow:hidden;}}
  nav{{display:flex;align-items:center;gap:6px;padding:8px 14px;background:#0b1526;border-bottom:1px solid var(--line);flex:0 0 auto;flex-wrap:wrap;}}
  nav .brand{{font-weight:700;font-size:14px;margin-right:8px;}} nav .brand span{{color:var(--accent);}}
  nav button{{background:transparent;border:1px solid transparent;color:var(--mut);padding:6px 12px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600;}}
  nav button.on{{background:var(--panel);color:var(--ink);border-color:var(--line);}}
  .regsel{{display:flex;gap:4px;margin-left:6px;border-left:1px solid var(--line);padding-left:10px;}}
  .regsel button.on{{background:var(--accent);color:#fff;border-color:var(--accent);}}
  nav .spacer{{flex:1;}} nav .upd{{color:var(--mut);font-size:11px;}}
  .view{{flex:1;min-height:0;display:none;}} .view.on{{display:flex;}}
  .split{{display:flex;width:100%;height:100%;overflow:hidden;}}
  .lmap{{flex:1;height:100%;}} .side{{width:440px;background:var(--panel);border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0;}}
  .side h2{{font-size:11px;text-transform:uppercase;color:var(--mut);letter-spacing:.4px;margin:0;}}
  .stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;padding:10px 14px;border-bottom:1px solid var(--line);}}
  .stat{{background:#0b1526;border:1px solid var(--line);border-radius:7px;padding:6px;}} .stat b{{display:block;font-size:15px;}} .stat span{{color:var(--mut);font-size:9px;text-transform:uppercase;}}
  .controls{{padding:8px 14px;border-bottom:1px solid var(--line);font-size:11.5px;}}
  .chips{{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;}}
  .chip{{padding:2px 7px;border-radius:20px;border:1px solid var(--line);cursor:pointer;font-size:10.5px;display:flex;align-items:center;gap:4px;}}
  .chip .dot{{width:8px;height:8px;border-radius:50%;}} .chip.off{{opacity:.35;}}
  .row{{display:flex;gap:10px;align-items:center;color:var(--mut);flex-wrap:wrap;}} .row label{{display:flex;gap:4px;align-items:center;cursor:pointer;}}
  .scroll{{flex:1;overflow:auto;min-height:0;}}
  table{{width:100%;border-collapse:collapse;font-size:11.5px;}} th,td{{padding:6px 8px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;}}
  th{{position:sticky;top:0;background:#0b1526;color:var(--mut);font-weight:600;font-size:10px;text-transform:uppercase;cursor:pointer;white-space:nowrap;}} th.sorted::after{{content:" \25BE";}}
  tbody tr{{cursor:pointer;}} tbody tr:hover{{background:#16223c;}}
  .lead-name{{font-weight:600;}} .sub2{{color:var(--mut);font-size:10px;margin-top:2px;line-height:1.4;}} .drill{{color:#a7f3d0;font-size:10px;margin-top:3px;line-height:1.4;}}
  .metal-chip{{font-size:9px;padding:0 4px;border-radius:4px;color:#0b1526;font-weight:700;margin-left:4px;}}
  .item.sel{{background:#1b2b4a;box-shadow:inset 3px 0 0 #f5a300;}}
  .legendbox{{background:rgba(9,14,26,.9);color:#e5edf7;border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:11px;line-height:1.7;max-width:250px;}}
  .legendbox b{{font-size:11px;}} .legendbox div{{display:flex;align-items:center;gap:6px;margin-top:3px;}}
  .k-open,.k-held,.k-pt{{display:inline-block;flex:0 0 auto;width:16px;height:12px;}}
  .k-open{{background:#f5a300;border:2px dashed #7c2d00;}} .k-held{{background:transparent;border:1.5px solid #cbd5e1;}}
  .k-pt{{width:10px;height:10px;border-radius:50%;background:#ef4444;border:2px solid #fff;}}
  .lbl-open{{background:transparent;border:0;box-shadow:none;color:#7c2d00;font-weight:800;font-size:11px;text-shadow:0 1px 2px #fff,0 0 2px #fff;}}
  .lbl-claim{{background:rgba(255,255,255,.85);border:0;box-shadow:none;color:#111827;font-size:10px;padding:0 3px;}}
  .leaflet-tooltip.lbl-open:before,.leaflet-tooltip.lbl-claim:before{{display:none;}}
  .badge{{font-size:8.5px;padding:1px 4px;border-radius:4px;font-weight:700;margin-left:4px;}} .b-dep{{background:#16a34a;color:#04140a;}} .b-hard{{background:#b45309;color:#fde68a;}}
  .score-pill{{display:inline-block;min-width:26px;text-align:center;padding:2px 5px;border-radius:20px;font-weight:700;color:#0b1526;}}
  .tag{{font-size:9px;padding:1px 5px;border-radius:4px;font-weight:700;margin-left:5px;}} .t-a{{background:#ea580c;color:#0b1526;}} .t-b{{background:#dc2626;color:#fff;}} .t-open{{background:#16a34a;color:#04140a;}}
  .item{{padding:8px 14px;border-bottom:1px solid var(--line);font-size:12px;cursor:pointer;}} .item:hover{{background:#16223c;}} .item b{{font-weight:600;}}
  .muted{{color:var(--mut);font-size:10.5px;margin-top:2px;}} a.news{{color:#93c5fd;text-decoration:none;}}
  footer{{padding:7px 14px;font-size:9.5px;color:var(--mut);border-top:1px solid var(--line);line-height:1.4;}}
  .leaflet-popup-content{{font-size:12px;line-height:1.5;max-width:300px;}} .leaflet-popup-content b{{color:#0b1526;}} .pk{{color:#64748b;}}
  .legend{{background:rgba(15,23,42,.92);padding:8px 10px;border-radius:8px;font-size:10.5px;line-height:1.5;border:1px solid var(--line);max-width:170px;}}
  .legend i{{display:inline-block;width:10px;height:10px;margin-right:5px;border-radius:3px;vertical-align:-1px;}}
  .ov{{overflow:auto;width:100%;padding:40px 24px 60px;}} .ov .wrap{{max-width:1000px;margin:0 auto;}}
  .ov h1{{font-size:30px;margin:0 0 6px;}} .ov h1 span{{color:var(--accent);}} .ov .lead{{color:var(--mut);font-size:15px;line-height:1.6;max-width:720px;}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-top:30px;}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;}}
  .card h3{{margin:0 0 10px;font-size:18px;}} .card .pill{{font-size:10px;text-transform:uppercase;padding:2px 8px;border-radius:20px;background:#14532d;color:#86efac;margin-left:8px;vertical-align:2px;}}
  .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:6px 0 14px;}} .kpis b{{display:block;font-size:18px;}} .kpis span{{color:var(--mut);font-size:9.5px;text-transform:uppercase;}}
  .card button{{background:var(--accent);color:#fff;border:none;padding:8px 13px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;margin-right:6px;}}
  .how{{margin-top:36px;color:var(--mut);font-size:13.5px;line-height:1.7;border-top:1px solid var(--line);padding-top:20px;}} .how b{{color:var(--ink);}}
  @media (max-width:900px){{.side{{width:100%;}}.split{{flex-direction:column;}}.lmap{{height:44%;}}.side{{height:56%;}}}}
</style></head><body><div id="shell">
  <nav>
    <div class="brand">Project Closeology <span>·</span></div>
    <button data-t="overview" class="on">Overview</button>
    <button data-t="map">Map</button>
    <button data-t="daily">Daily Radar</button>
    <div class="regsel" id="regsel"></div>
    <div class="spacer"></div><div class="upd">Updated {generated}</div>
  </nav>
  <section id="view-overview" class="view on"><div class="ov"><div class="wrap">
    <h1>Mineral opportunity radar <span>·</span> BC &amp; Ontario</h1>
    <p class="lead">Past-producing and drilled-out deposits sitting on — or beside — <b>open, stakeable ground</b>.
       Screened against live claims, leases, reserves and parks; enriched with grade, deposit size, drill/assay
       highlights and the nearest community; scanned daily for claims lapsing / newly staked (BC) and recent
       drilling (Ontario) near each lead.</p>
    <div class="cards" id="ovcards"></div>
    <div class="how"><b>How to read it.</b> Pick a province (top right). A <b>lead</b> is an occurrence of historical
      merit whose own cell is unclaimed (“deposit open”) or which has open cells within a few hundred metres — shown
      in <b style="color:#22c55e">green</b> on the map. The <b>Daily Radar</b> flags leads where something is moving
      nearby. Always confirm in the official registry (BC MTO / Ontario MLAS) before acting — a screening tool, not
      staking advice.</div>
  </div></div></section>
  <section id="view-map" class="view"><div class="split">
    <div id="lmap" class="lmap"></div>
    <div class="side">
      <div class="stats" id="m-stats"></div>
      <div class="controls"><div class="chips" id="m-chips"></div>
        <div class="row"><label><input type="checkbox" id="m-fDep"> Deposit open</label>
          <label><input type="checkbox" id="m-fDrill"> Has drill data</label>
          <label><input type="checkbox" id="m-tCells" checked> Open cells</label>
          <label><input type="checkbox" id="m-tClaims" checked> Claims</label>
          <label><input type="checkbox" id="m-tOcc" checked> Occurrences</label></div></div>
      <div class="scroll"><table><thead><tr><th data-k="rank">#</th><th data-k="name">Lead · community · basis</th>
        <th data-k="tonnes_str">Size</th><th data-k="score">Score</th></tr></thead><tbody id="m-rows"></tbody></table></div>
      <footer id="m-attr"></footer>
    </div></div></section>
  <section id="view-daily" class="view"><div class="split">
    <div id="dmap" class="lmap"></div>
    <div class="side">
      <div class="stats" id="d-stats"></div>
      <div class="controls"><h2>Drill news</h2><div id="d-news" style="margin-top:6px"></div></div>
      <div class="scroll" id="d-list"></div>
      <footer>Screening signals only — verify each source. Not staking advice.</footer>
    </div></div></section>
</div>
<script>
const REGIONS={regions_json}, METAL_COLOR={metal_color}, METAL_ORDER={metal_order};
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
const mc=m=>METAL_COLOR[m]||'#94a3b8';
let CUR='bc', tab='overview', mapObj=null, dmapObj=null;
const order=['bc','on'];

// region selector
document.getElementById('regsel').innerHTML=order.map(k=>`<button data-r="${{k}}" class="${{k===CUR?'on':''}}">${{REGIONS[k].name.replace('British Columbia','BC')}}</button>`).join('');
document.getElementById('regsel').addEventListener('click',e=>{{const b=e.target.closest('button[data-r]');if(!b)return;CUR=b.dataset.r;
  document.querySelectorAll('#regsel button').forEach(x=>x.classList.toggle('on',x.dataset.r===CUR));
  if(tab==='map'){{buildMap();}} if(tab==='daily'){{buildDaily();}}}});

// overview cards
document.getElementById('ovcards').innerHTML=order.map(k=>{{const s=REGIONS[k].stats;return `
  <div class="card"><h3>${{s.region}}<span class="pill">live</span></h3>
   <div class="kpis"><div><b>${{(s.n_leads||0).toLocaleString()}}</b><span>leads</span></div>
    <div><b>${{(s.n_deposit_open||0).toLocaleString()}}</b><span>deposit open</span></div>
    <div><b>${{(s.n_with_drill_highlights||0).toLocaleString()}}</b><span>drill data</span></div>
    <div><b>${{(s.n_claims_active||0).toLocaleString()}}</b><span>claims</span></div></div>
   <button data-go="map" data-r="${{k}}">Explore map →</button><button data-go="daily" data-r="${{k}}">Daily radar</button></div>`;}}).join('');
document.getElementById('ovcards').addEventListener('click',e=>{{const b=e.target.closest('button[data-go]');if(!b)return;CUR=b.dataset.r;
  document.querySelectorAll('#regsel button').forEach(x=>x.classList.toggle('on',x.dataset.r===CUR));show(b.dataset.go);}});

function show(t){{tab=t;document.querySelectorAll('nav button[data-t]').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('on'));document.getElementById('view-'+t).classList.add('on');
  if(t==='map')buildMap(); if(t==='daily')buildDaily();}}
document.querySelectorAll('nav [data-t]').forEach(b=>b.addEventListener('click',()=>show(b.dataset.t)));

const WMS={{url:"{wms_url}",layer:"{wms_layer}",attr:"{wms_attr}"}};
function baseLayers(){{return {{topo:L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:17,attribution:'&copy; OpenTopoMap'}}),
  osm:L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}})}};}}
const SC={{'Producer':'#7f1d1d','Producing Mine':'#7f1d1d','Past Producer':'#dc2626','Past Producing Mine':'#dc2626','Past Producing Mine (Low Tonnage)':'#e0562c','Past Producing Quarry':'#b45309','Developed Prospect':'#ea580c','Prospect':'#f59e0b','Deposit':'#eab308','Showing':'#38bdf8','Occurrence':'#94a3b8','Discretionary Occurrence':'#64748b'}};

function buildMap(){{
  const R=REGIONS[CUR]; if(mapObj){{mapObj.remove();mapObj=null;}}
  const bl=baseLayers(); const map=L.map('lmap',{{layers:[bl.topo]}}); mapObj=map;
  let claims;
  if(R.use_wms){{ claims=L.tileLayer.wms(WMS.url+"?",{{layers:WMS.layer,format:'image/png',transparent:true,version:'1.3.0',opacity:0.45,attribution:WMS.attr}}); claims.addTo(map);
    map.on('click', async (e)=>{{ if(!map.hasLayer(claims))return; const p=map.latLngToContainerPoint(e.latlng),sz=map.getSize(),b=map.getBounds();
      const url=WMS.url+"?service=WMS&version=1.3.0&request=GetFeatureInfo&layers="+WMS.layer+"&query_layers="+WMS.layer+"&crs=EPSG:4326&bbox="+b.getSouth()+","+b.getWest()+","+b.getNorth()+","+b.getEast()+"&width="+sz.x+"&height="+sz.y+"&i="+Math.round(p.x)+"&j="+Math.round(p.y)+"&info_format=application/json&feature_count=1";
      try{{const r=await fetch(url);const d=await r.json();if(d.features&&d.features.length){{const q=d.features[0].properties;
        L.popup().setLatLng(e.latlng).setContent(`<b>${{esc(q.{f_name})||'(claim)'}}</b> <span class=pk>#${{esc(q.{f_id})}}</span><br>${{esc(q.{f_type})}} · ${{esc(q.{f_area})}} ha<br>Owner: ${{esc(q.{f_owner})||'—'}}<br>Good to: <b>${{esc((''+(q.{f_good}||'')).slice(0,10))}}</b>`).openOn(map);}}}}catch(err){{}}
    }});
  }} else {{ claims=L.geoJSON(R.claims,{{style:{{color:'#64748b',weight:.8,fillColor:'#64748b',fillOpacity:0.18}},onEachFeature:(f,l)=>l.bindPopup(`<b>Claim ${{esc(f.properties.claim)}}</b><br>Active mining claim cell<br><a href="https://www.mlas.mndm.gov.on.ca/mlas/search/searchIndex.html#/search/searchClaimDetails?claimNumber=${{esc(f.properties.claim)}}" target=_blank>View in MLAS ↗</a>`)}}); claims.addTo(map); }}
  const cells=L.geoJSON(R.cells,{{style:{{color:'#22c55e',weight:1,fillColor:'#22c55e',fillOpacity:0.28}},onEachFeature:(f,l)=>l.bindPopup(`<b>Open cell</b> — beside #${{f.properties.rank}} ${{esc(f.properties.name)}}<br>Appears unstaked. Verify before acting.`)}}).addTo(map);
  const occC=L.markerClusterGroup({{chunkedLoading:true,maxClusterRadius:45,disableClusteringAtZoom:11}});
  L.geoJSON(R.occ,{{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:4,color:'#0b1526',weight:.7,fillColor:SC[(f.properties.st||'').trim()]||'#94a3b8',fillOpacity:f.properties.p?0.95:0.6}}),onEachFeature:(f,l)=>l.bindPopup(`<b>${{esc(f.properties.n)}}</b><br>${{esc(f.properties.st)}}${{f.properties.p?' · produced':''}}`)}}).addTo(occC); occC.addTo(map);
  const M={{}};
  const lg=L.geoJSON(R.leads,{{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:7,color:'#0b1526',weight:1.3,fillColor:mc(f.properties.primary_metal),fillOpacity:0.92}}),
    onEachFeature:(f,l)=>{{const p=f.properties;M[p.lead_id]=l;
      l.bindPopup(`<b>#${{p.rank}} · ${{esc(p.name)}}</b> <span class=pk>${{esc(p.minfile)}}</span> <span class=metal-chip style="background:${{mc(p.primary_metal)}}">${{esc(p.primary_metal)}}</span><br>
        <b>${{esc(p.commodity)}}</b> · ${{esc(p.status)}} · score ${{p.score}}<br>
        ${{p.deposit_open?'<b style="color:#15803d">◎ Deposit itself OPEN</b>':'<b style="color:#b45309">Deposit staked</b> — adjacent open ground'}}<br>
        <b>Nearest community:</b> ${{esc(p.nearest_community)}} (${{p.community_km}} km)<br>
        ${{p.deposit_size&&p.deposit_size!=='no tonnage on record'?`<b>Size:</b> ${{esc(p.deposit_size)}}<br>`:''}}${{p.grade_str?`<b>Grade:</b> ${{esc(p.grade_str)}}<br>`:''}}
        ${{p.drill_highlights?`<b>Drill / assay:</b> ${{esc(p.drill_highlights)}}<br>`:''}}
        ${{p.exploration_spend_str?`<b>Exploration spend:</b> ${{esc(p.exploration_spend_str)}}${{p.n_reports?` · ${{p.n_reports}} report(s)`:''}}${{p.last_work_year?` · last ${{p.last_work_year}}`:''}}${{p.operators?`<br><span class=pk>${{esc(p.operators)}}</span>`:''}}<br>`:''}}
        ${{p.encumbrances?`<b style="color:#b45309">⚠ Harder to stake:</b> ${{esc(p.encumbrances)}}<br>`:''}}
        <b>Open cells nearby:</b> ${{p.n_cells}} (~${{p.cells_area_ha}} ha)<br>${{p.minfile_url?`<a href="${{esc(p.minfile_url)}}" target=_blank>Full record ↗</a>`:''}}`);
    }}}}).addTo(map);
  try{{map.fitBounds(lg.getBounds().pad(0.05));}}catch(e){{map.setView(CUR==='on'?[50,-86]:[54,-125],5);}}
  L.control.layers({{'Topographic':bl.topo,'Street':bl.osm}},{{}},{{position:'topleft'}}).addTo(map);
  const s=R.stats;
  const S=[['n_leads','Leads'],['n_deposit_open','Deposit open'],['n_with_drill_highlights','Drill data'],['n_hard_to_stake','Harder stake'],['n_candidate_leads','Candidates'],['n_occurrences','Occurr.'],['n_claims_active','Claims'],['top_n_examined','Examined']];
  document.getElementById('m-stats').innerHTML=S.map(([k,l])=>`<div class=stat><b>${{(s[k]!=null&&s[k].toLocaleString)?s[k].toLocaleString():(s[k]??'—')}}</b><span>${{l}}</span></div>`).join('');
  document.getElementById('m-attr').textContent=s.attribution||'';
  function bk(p){{let m=p.metal_buckets;if(Array.isArray(m))return m;return (m||p.primary_metal||'').split(';').filter(Boolean);}}
  const present=[];R.leads.features.forEach(f=>bk(f.properties).forEach(m=>{{if(!present.includes(m))present.push(m);}}));
  present.sort((a,b)=>METAL_ORDER.indexOf(a)-METAL_ORDER.indexOf(b));
  const sel=new Set(present);const ce=document.getElementById('m-chips');
  ce.innerHTML=present.map(m=>`<span class="chip" data-m="${{m}}"><span class=dot style="background:${{mc(m)}}"></span>${{m}}</span>`).join('');
  ce.onclick=e=>{{const c=e.target.closest('.chip');if(!c)return;const m=c.dataset.m;sel.has(m)?(sel.delete(m),c.classList.add('off')):(sel.add(m),c.classList.remove('off'));render();}};
  const rows=document.getElementById('m-rows');let sk='rank',sa=true;
  function pass(p){{if(document.getElementById('m-fDep').checked&&!p.deposit_open)return false;if(document.getElementById('m-fDrill').checked&&!p.drill_highlights)return false;return bk(p).some(m=>sel.has(m));}}
  function render(){{let list=R.leads.features.map(f=>f.properties).filter(pass);
    list.sort((a,b)=>{{let x=a[sk],y=b[sk];if(sk==='name'||sk==='tonnes_str'){{x=x||'';y=y||'';return sa?String(x).localeCompare(y):String(y).localeCompare(x);}}x=x==null?-1:x;y=y==null?-1:y;return sa?x-y:y-x;}});
    rows.innerHTML=list.map(p=>{{const dh=p.drill_highlights?`<div class=drill>⛏ ${{esc(p.drill_highlights.slice(0,110))}}…</div>`:'';
      return `<tr data-id="${{p.lead_id}}"><td>${{p.rank}}</td><td><span class=lead-name>${{esc(p.name)}}</span><span class=metal-chip style="background:${{mc(p.primary_metal)}}">${{esc(p.metals_abbr||p.primary_metal)}}</span>${{p.deposit_open?'<span class="badge b-dep">◎ open</span>':''}}${{p.hard_to_stake?'<span class="badge b-hard">⚠</span>':''}}<div class=sub2>${{esc(p.nearest_community)}} · ${{p.community_km}} km · ${{esc(p.basis)}}${{p.exploration_spend_str?' · <span style="color:#7cc043">'+esc(p.exploration_spend_str)+' spent</span>':''}}</div>${{dh}}</td><td>${{esc((p.deposit_size||'').replace('no tonnage on record','—').slice(0,20))}}</td><td><span class=score-pill style="background:${{mc(p.primary_metal)}}">${{p.score}}</span></td></tr>`;}}).join('');
    document.querySelectorAll('#view-map th[data-k]').forEach(th=>th.classList.toggle('sorted',th.dataset.k===sk));
    R.leads.features.forEach(f=>{{const p=f.properties,l=M[p.lead_id];if(!l)return;pass(p)?(map.hasLayer(l)||l.addTo(lg)):lg.removeLayer(l);}});}}
  rows.onclick=e=>{{const tr=e.target.closest('tr');if(!tr)return;const l=M[tr.dataset.id];if(l){{map.setView(l.getLatLng(),12);l.openPopup();}}}};
  document.querySelectorAll('#view-map th[data-k]').forEach(th=>th.onclick=()=>{{const k=th.dataset.k;sa=(k===sk)?!sa:(k==='rank'||k==='name');sk=k;render();}});
  ['m-fDep','m-fDrill'].forEach(id=>document.getElementById(id).onchange=render);
  document.getElementById('m-tClaims').onchange=e=>{{e.target.checked?claims.addTo(map):map.removeLayer(claims);}};
  document.getElementById('m-tCells').onchange=e=>{{e.target.checked?cells.addTo(map):map.removeLayer(cells);}};
  document.getElementById('m-tOcc').onchange=e=>{{e.target.checked?occC.addTo(map):map.removeLayer(occC);}};
  const leg=L.control({{position:'bottomleft'}});leg.onAdd=()=>{{const d=L.DomUtil.create('div','legend');d.innerHTML='<b>Leads by metal</b><br>'+present.map(m=>`<i style="background:${{mc(m)}}"></i>${{m}}`).join('<br>')+'<br><i style="background:#22c55e"></i>Open cell<br><i style="background:#64748b"></i>Active claim';return d;}};leg.addTo(map);
  render(); setTimeout(()=>map.invalidateSize(),60);
}}

function edgePopup(p){{return `<b>${{esc(p.company)}}</b>${{p.source==='News release'?' <span style="color:#0369a1">NEWS</span>':''}}${{p.hot?' <span style="color:#b91c1c">HOT</span>':''}}<br><span style=color:#334155>${{esc(p.property)}}</span><br>${{p.source==='News release'?('news release '+esc(p.date)):(p.n_holes+' recent effort(s), latest '+p.year)}}${{p.commodity?' · '+esc(p.commodity):''}}<br><b style=color:#15803d>${{p.open_ha}} ha open ground ${{esc(p.open_dir)}}</b><br>${{p.assay?'<b style=color:#b45309>Assay: '+esc(p.assay)+'</b><br>':''}}Claim(s): ${{esc((p.claims||[]).join(', '))}}${{p.spend?'<br>Program spend: $'+Math.round(p.spend).toLocaleString():''}}<br><a href="${{esc(p.source_url)}}" target=_blank>Source: ${{esc(p.source)}}${{p.afri?' · AFRI '+esc(p.afri):''}} ↗</a>`;}}
function buildDaily(){{
  const R=REGIONS[CUR], D=R.daily, EC=D.edge_counts||{{n:0,hot:0,open_ha:0}}; if(dmapObj){{dmapObj.remove();dmapObj=null;}}
  const bl=baseLayers(); const map=L.map('dmap',{{layers:[bl.topo]}}); dmapObj=map; const M={{}}, EM={{}};
  L.geoJSON({{type:'FeatureCollection',features:D.act}},{{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:3,color:'#f59e0b',weight:1,fillColor:'#f59e0b',fillOpacity:.7}}),
    style:f=>({{color:f.properties.kind==='new'?'#dc2626':'#ea580c',weight:1.3,fillOpacity:.10}}),onEachFeature:(f,l)=>l.bindPopup(`<b>${{esc(f.properties.label)||'activity'}}</b><br>${{esc(f.properties.sub)}}`)}}).addTo(map);
  const EH=D.edge_held_feats||[], EO=D.edge_open_feats||[];
  const heldFG=L.layerGroup().addTo(map), openFG=L.layerGroup().addTo(map);
  function focusEdge(i){{heldFG.clearLayers();openFG.clearLayers();
    const oL=L.geoJSON({{type:'FeatureCollection',features:EO.filter(f=>f.properties.pidx===i)}},{{style:()=>({{color:'#7c2d00',weight:3,dashArray:'7 4',fillColor:'#f5a300',fillOpacity:.62}})}});
    oL.eachLayer(l=>l.bindTooltip('OPEN — stakeable ('+((D.edges||[])[i]?(D.edges||[])[i].open_ha:'')+' ha)',{{permanent:true,direction:'center',className:'lbl-open'}}));
    oL.addTo(openFG);
    const hL=L.geoJSON({{type:'FeatureCollection',features:EH.filter(f=>f.properties.pidx===i)}},{{style:f=>({{color:'#111827',weight:f.properties.drilled?3:1.5,fill:true,fillColor:'#334155',fillOpacity:f.properties.drilled?.28:.10}}),onEachFeature:(f,l)=>{{if(f.properties.claim)l.bindTooltip('claim '+esc(f.properties.claim),{{permanent:!!f.properties.drilled,direction:'center',className:'lbl-claim'}});}}}});
    hL.addTo(heldFG);
    const m=EM[i];try{{const grp=L.featureGroup([oL,hL].concat(m?[m]:[]));map.fitBounds(grp.getBounds().pad(0.35));}}catch(e){{if(m)map.setView(m.getLatLng(),13);}}
    if(m)m.openPopup();
    document.querySelectorAll('#d-list .item[data-e]').forEach(x=>x.classList.toggle('sel',+x.dataset.e===i));}}
  const lg=L.geoJSON({{type:'FeatureCollection',features:D.lead_feats}},{{pointToLayer:(f,ll)=>{{const p=f.properties;
    const m=L.circleMarker(ll,{{radius:4,color:'#0b1526',weight:1,fillColor:mc(p.metal),fillOpacity:.45}});M[p.rank]=m;
    m.bindPopup(`<b>#${{p.rank}} ${{esc(p.name)}}</b> <span style="color:#64748b">${{esc(p.metal)}}</span> <span class=pk>${{esc(p.minfile)}}</span><br>${{esc(p.status)}}${{p.deposit_open?' · <b style=color:#15803d>deposit open</b>':''}}${{p.grade?'<br>Grade: '+esc(p.grade):''}}${{p.drill?'<br><span style=color:#047857>⛏ '+esc(p.drill.slice(0,120))+'…</span>':''}}${{p.url?'<br><a href="'+esc(p.url)+'" target=_blank>Full record ↗</a>':''}}`);return m;}}}}).addTo(map);
  let _ei=0;
  L.geoJSON({{type:'FeatureCollection',features:D.edge_point_feats||[]}},{{pointToLayer:(f,ll)=>{{const p=f.properties;const c=p.source==='News release'?'#38bdf8':(p.hot?'#ef4444':'#f97316');const i=_ei++;
    const m=L.circleMarker(ll,{{radius:p.hot?9:7,color:'#fff7ed',weight:2,fillColor:c,fillOpacity:.95}});EM[i]=m;m.bindPopup(edgePopup(p));m.on('click',()=>focusEdge(i));return m;}}}}).addTo(map);
  try{{const eg=L.featureGroup(Object.values(EM));map.fitBounds(((D.edges||[]).length?eg:lg).getBounds().pad(0.15));}}catch(e){{map.setView(CUR==='on'?[50,-86]:[54,-125],5);}}
  if((D.edges||[]).length)focusEdge(0);
  const lgd=L.control({{position:'bottomleft'}});lgd.onAdd=function(){{const d=L.DomUtil.create('div','legendbox');d.innerHTML='<b>How to read this</b><div><span class=k-open></span> solid amber = <b>OPEN ground you can stake</b></div><div><span class=k-held></span> outlined = claims already staked (number shown)</div><div><span class=k-pt></span> dot = where they drilled — click a play to focus</div>';return d;}};lgd.addTo(map);
  document.getElementById('d-stats').innerHTML=`
    <div class=stat><b style="color:#f97316">${{EC.n}}</b><span>edge plays</span></div>
    <div class=stat><b style="color:#ef4444">${{EC.hot}}</b><span>hot (last yr)</span></div>
    <div class=stat><b style="color:#22c55e">${{Math.round(EC.open_ha).toLocaleString()}}</b><span>ha open beside</span></div>
    <div class=stat><b style="color:#38bdf8">${{EC.news||0}}</b><span>from news</span></div>`;
  document.getElementById('d-news').innerHTML=D.news.length?D.news.map(n=>`<div class=item>${{n.url?`<a class=news href="${{esc(n.url)}}" target=_blank>`:''}}<b>${{esc(n.title)}}</b>${{n.url?'</a>':''}}<div class=muted>${{esc(n.date||'')}} ${{n.summary?'· '+esc(n.summary):''}}</div></div>`).join(''):'<div class=muted>No drill news captured in the last run.</div>';
  const E=D.edges||[];
  const edgeHtml='<div class=controls><h2>⚡ Edge plays — drilling on the boundary of open ground ('+E.length+')</h2></div>'+(E.length?E.map((p,i)=>`<div class=item data-e="${{i}}"><b>${{esc(p.company)}}</b>${{p.source==='News release'?'<span class="tag" style="background:#38bdf8;color:#052338">news</span>':''}}${{p.hot?'<span class="tag t-b">hot</span>':'<span class="tag t-a">edge</span>'}}<div class=muted>${{esc(p.property)}} · ${{p.source==='News release'?('release '+esc(p.date)):(p.n_holes+' effort(s), latest '+p.year)}}${{p.commodity?' · '+esc(p.commodity):''}}${{p.spend?' · $'+Math.round(p.spend).toLocaleString():''}}</div>${{p.assay?`<div class=drill>⛏ Assay: ${{esc(p.assay)}}</div>`:''}}<div class=drill style="color:#fdba74">▸ ${{p.open_ha}} ha open to the ${{esc(p.open_dir)}} — claim ${{esc((p.claims||[])[0]||'')}}</div><div class=muted><a class=news href="${{esc(p.source_url)}}" target=_blank>source: ${{esc(p.source)}} ↗</a></div></div>`).join(''):'<div class=item><div class=muted>No recent drilling/work on an open-ground boundary. Fresh news-release assays appear here once the news source is wired.</div></div>');
  const dr=(D.dropped||[]);
  const drHtml=dr.length?'<div class=controls><h2>⚑ Ground just opened ('+dr.length+')</h2></div>'+dr.slice(0,25).map(x=>`<div class=item><b>${{esc(x.owner)||'Claim '+esc(x.id)}}</b> dropped ${{x.area_ha}} ha${{x.good_to?' (was good to '+esc(x.good_to)+')':''}}<div class=muted>near #${{x.near_rank}} ${{esc(x.near_lead)}} · ${{x.near_km}} km</div></div>`).join(''):'';
  const sig=D.lead_feats.map(f=>f.properties).filter(p=>p.near_a||p.near_b).sort((a,b)=>a.rank-b.rank);
  const actHtml=sig.length?'<div class=controls><h2>'+esc(D.labels.sec)+' ('+sig.length+')</h2></div>'+sig.map(p=>`<div class=item data-r="${{p.rank}}"><b>#${{p.rank}} ${{esc(p.name)}}</b>${{p.deposit_open?'<span class="tag t-open">open</span>':''}}${{p.near_a?'<span class="tag t-a">'+esc(D.labels.tag_a)+'</span>':''}}${{p.near_b?'<span class="tag t-b">'+esc(D.labels.tag_b)+'</span>':''}}<div class=muted>${{esc(p.metal)}} · ${{esc(p.status)}} · ${{esc(p.community)}} ${{p.community_km!=null?p.community_km+' km':''}}</div>${{p.drill?`<div class=drill>⛏ ${{esc(p.drill.slice(0,110))}}…</div>`:''}}</div>`).join(''):'';
  document.getElementById('d-list').innerHTML=edgeHtml+drHtml+actHtml;
  document.getElementById('d-list').onclick=e=>{{const ei=e.target.closest('.item[data-e]');if(ei){{focusEdge(+ei.dataset.e);return;}}const it=e.target.closest('.item[data-r]');if(!it)return;const m=M[it.dataset.r];if(m){{map.setView(m.getLatLng(),12);m.openPopup();}}}};
  if((D.edges||[]).length)focusEdge(0);   // highlight the top play now that the list exists
  setTimeout(()=>map.invalidateSize(),60);
}}
</script></body></html>
"""


if __name__ == "__main__":
    REGIONS = [
        {"dir": "data/bc", "slug": "bc", "metric": "EPSG:3005", "news": "site/news.json"},
        {"dir": "data/on", "slug": "on", "metric": "EPSG:3161", "news": "site/news_on.json"},
    ]
    build(REGIONS, "site/app.html")
