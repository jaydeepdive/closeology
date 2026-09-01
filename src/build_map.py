"""Unified region map (self-contained HTML). Handles BC (live claims WMS +
GetFeatureInfo) and Ontario (inline claim cells), plus an open-cell overlay
showing the actual stakeable ground, clustered occurrences and detailed leads."""
import os
import json

VEN = os.path.join(os.path.dirname(__file__), "vendor")

BC_WMS = {
    "url": "https://openmaps.gov.bc.ca/geo/pub/WHSE_MINERAL_TENURE.MTA_ACQUIRED_TENURE_SVW/ows",
    "layer": "pub:WHSE_MINERAL_TENURE.MTA_ACQUIRED_TENURE_SVW",
    "attr": "BC Mineral Titles (MTO)",
    "fields": {"name": "CLAIM_NAME", "id": "TENURE_NUMBER_ID", "type": "TENURE_TYPE_DESCRIPTION",
               "area": "AREA_IN_HECTARES", "owner": "OWNER_NAME", "good": "GOOD_TO_DATE"},
}


def _read(p):
    with open(p) as f:
        return f.read()


def _load(out_dir, name):
    p = os.path.join(out_dir, name)
    return _read(p) if os.path.exists(p) else '{"type":"FeatureCollection","features":[]}'


def build(out_dir, html_path, wms=None, inline_claims=False):
    from config import METAL_COLOR, METAL_ORDER
    stats = json.load(open(os.path.join(out_dir, "stats.json")))
    w = wms or {"url": "", "layer": "", "attr": "", "fields": {k: "OBJECTID" for k in
                ("name", "id", "type", "area", "owner", "good")}}
    html = TEMPLATE.format(
        leaflet_css=_read(os.path.join(VEN, "leaflet.css")),
        leaflet_js=_read(os.path.join(VEN, "leaflet.js")),
        mc_css=_read(os.path.join(VEN, "mc.css")) + _read(os.path.join(VEN, "mcd.css")),
        mc_js=_read(os.path.join(VEN, "mc.js")),
        stats_json=json.dumps(stats),
        metal_color_json=json.dumps(METAL_COLOR), metal_order_json=json.dumps(METAL_ORDER),
        leads_json=_load(out_dir, "leads.geojson"),
        occ_json=_load(out_dir, "occurrences_all.geojson"),
        opencells_json=_load(out_dir, "opencells.geojson"),
        claims_json=_load(out_dir, "claims_near.geojson"),
        use_wms="true" if wms else "false",
        wms_url=w["url"], wms_layer=w["layer"], wms_attr=w["attr"],
        f_name=w["fields"]["name"], f_id=w["fields"]["id"], f_type=w["fields"]["type"],
        f_area=w["fields"]["area"], f_owner=w["fields"]["owner"], f_good=w["fields"]["good"],
        region=stats.get("region", ""),
    )
    with open(html_path, "w") as f:
        f.write(html)
    print(f"[map] {html_path} ({os.path.getsize(html_path)//1024} KB)")
    return html_path


TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Closeology · {region}</title>
<style>{leaflet_css}</style><style>{mc_css}</style>
<script>{leaflet_js}</script><script>{mc_js}</script>
<style>
  :root {{ --bg:#0f172a; --panel:#111c33; --line:#243352; --ink:#e5edf7; --mut:#94a3b8; --accent:#c026d3; }}
  * {{ box-sizing:border-box; }}
  html,body {{ margin:0; height:100%; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--ink); }}
  #app {{ display:flex; height:100vh; overflow:hidden; }}
  #map {{ flex:1; height:100%; }}
  #panel {{ width:460px; background:var(--panel); border-left:1px solid var(--line); display:flex; flex-direction:column; }}
  header {{ padding:12px 15px; border-bottom:1px solid var(--line); }}
  header h1 {{ margin:0 0 2px; font-size:15px; }} header .sub {{ color:var(--mut); font-size:11.5px; }}
  .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:6px; padding:10px 15px; border-bottom:1px solid var(--line); }}
  .stat {{ background:#0b1526; border:1px solid var(--line); border-radius:7px; padding:6px; }}
  .stat b {{ display:block; font-size:15px; }} .stat span {{ color:var(--mut); font-size:9px; text-transform:uppercase; letter-spacing:.3px; }}
  .controls {{ padding:8px 15px; border-bottom:1px solid var(--line); font-size:11.5px; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:4px; margin-bottom:6px; }}
  .chip {{ padding:2px 7px; border-radius:20px; border:1px solid var(--line); cursor:pointer; font-size:10.5px; display:flex; align-items:center; gap:4px; }}
  .chip .dot {{ width:8px; height:8px; border-radius:50%; }} .chip.off {{ opacity:.35; }}
  .controls .row {{ display:flex; gap:10px; align-items:center; color:var(--mut); flex-wrap:wrap; }}
  .controls label {{ display:flex; gap:4px; align-items:center; cursor:pointer; }}
  #leadlist {{ flex:1; overflow:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:11.5px; }}
  th,td {{ padding:6px 8px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ position:sticky; top:0; background:#0b1526; color:var(--mut); font-weight:600; font-size:10px; text-transform:uppercase; cursor:pointer; white-space:nowrap; }}
  th.sorted::after {{ content:" \25BE"; }}
  tbody tr {{ cursor:pointer; }} tbody tr:hover {{ background:#16223c; }}
  .lead-name {{ font-weight:600; }}
  .sub2 {{ color:var(--mut); font-size:10px; margin-top:2px; line-height:1.4; }}
  .drill {{ color:#a7f3d0; font-size:10px; margin-top:3px; line-height:1.4; }}
  .metal-chip {{ font-size:9px; padding:0 4px; border-radius:4px; color:#0b1526; font-weight:700; margin-left:4px; }}
  .badge {{ font-size:8.5px; padding:1px 4px; border-radius:4px; font-weight:700; margin-left:4px; }}
  .b-dep {{ background:#16a34a; color:#04140a; }} .b-hard {{ background:#b45309; color:#fde68a; }}
  .score-pill {{ display:inline-block; min-width:26px; text-align:center; padding:2px 5px; border-radius:20px; font-weight:700; color:#0b1526; }}
  footer {{ padding:8px 15px; font-size:9.5px; color:var(--mut); border-top:1px solid var(--line); line-height:1.4; }}
  .leaflet-popup-content {{ font-size:12px; line-height:1.5; max-width:300px; }}
  .leaflet-popup-content b {{ color:#0b1526; }} .pk {{ color:#64748b; }}
  .cells {{ max-height:70px; overflow:auto; background:#f1f5f9; border:1px solid #cbd5e1; border-radius:4px; padding:3px 5px; margin-top:3px; font-family:ui-monospace,monospace; font-size:10.5px; color:#0b1526; }}
  .legend {{ background:rgba(15,23,42,.92); padding:8px 10px; border-radius:8px; font-size:10.5px; line-height:1.55; border:1px solid var(--line); max-width:175px; }}
  .legend i {{ display:inline-block; width:10px; height:10px; margin-right:5px; border-radius:3px; vertical-align:-1px; }}
  @media (max-width:900px) {{ #panel {{ width:100%; }} #app{{flex-direction:column;}} #map{{height:44%;}} #panel{{height:56%;}} }}
</style></head>
<body><div id="app">
  <div id="map"></div>
  <div id="panel">
    <header><h1>Project Closeology <span style="color:var(--accent)">·</span> {region}</h1>
      <div class="sub" id="subline"></div></header>
    <div class="stats" id="stats"></div>
    <div class="controls">
      <div class="chips" id="metalChips"></div>
      <div class="row">
        <label><input type="checkbox" id="fDep"> Deposit open</label>
        <label><input type="checkbox" id="fDrill"> Has drill data</label>
        <label><input type="checkbox" id="tCells" checked> Open cells</label>
        <label><input type="checkbox" id="tClaims" checked> Claims</label>
        <label><input type="checkbox" id="tOcc" checked> Occurrences</label>
      </div>
    </div>
    <div id="leadlist"><table>
      <thead><tr><th data-k="rank">#</th><th data-k="name">Lead · community · basis</th>
        <th data-k="tonnes_str">Size</th><th data-k="score">Score</th></tr></thead>
      <tbody id="leadrows"></tbody></table></div>
    <footer id="attribution"></footer>
  </div></div>
<script>
const STATS={stats_json}, LEADS={leads_json}, OCC={occ_json}, CELLS={opencells_json}, CLAIMS={claims_json};
const METAL_COLOR={metal_color_json}, METAL_ORDER={metal_order_json};
const USE_WMS={use_wms};
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
const mc=m=>METAL_COLOR[m]||'#94a3b8';
const topo=L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:17,attribution:'&copy; OpenTopoMap'}});
const osm=L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}});
const map=L.map('map',{{layers:[topo]}});

// --- claims: BC live WMS + GFI, or Ontario inline cells ---
let claimsLayer;
if(USE_WMS){{
  claimsLayer=L.tileLayer.wms("{wms_url}?",{{layers:'{wms_layer}',format:'image/png',transparent:true,version:'1.3.0',opacity:0.45,attribution:'{wms_attr}'}});
  claimsLayer.addTo(map);
  map.on('click', async (e)=>{{ if(!map.hasLayer(claimsLayer))return;
    const p=map.latLngToContainerPoint(e.latlng),sz=map.getSize(),b=map.getBounds();
    const url="{wms_url}?service=WMS&version=1.3.0&request=GetFeatureInfo&layers={wms_layer}&query_layers={wms_layer}&crs=EPSG:4326&bbox="+b.getSouth()+","+b.getWest()+","+b.getNorth()+","+b.getEast()+"&width="+sz.x+"&height="+sz.y+"&i="+Math.round(p.x)+"&j="+Math.round(p.y)+"&info_format=application/json&feature_count=1";
    try{{const r=await fetch(url);const d=await r.json();if(d.features&&d.features.length){{const q=d.features[0].properties;
      L.popup().setLatLng(e.latlng).setContent(`<b>${{esc(q.{f_name})||'(unnamed claim)'}}</b> <span class=pk>#${{esc(q.{f_id})}}</span><br>${{esc(q.{f_type})}} · ${{esc(q.{f_area})}} ha<br>Owner: ${{esc(q.{f_owner})||'—'}}<br>Good to: <b>${{esc((''+(q.{f_good}||'')).slice(0,10))}}</b><br><span class=pk>{wms_attr}</span>`).openOn(map);}}}}catch(err){{}}
  }});
}} else {{
  claimsLayer=L.geoJSON(CLAIMS,{{style:{{color:'#64748b',weight:.8,fillColor:'#64748b',fillOpacity:0.18}},
    onEachFeature:(f,l)=>l.bindPopup(`<b>Claim ${{esc(f.properties.claim)}}</b><br>Active mining claim cell<br><a href="https://www.mlas.mndm.gov.on.ca/mlas/search/searchIndex.html#/search/searchClaimDetails?claimNumber=${{esc(f.properties.claim)}}" target=_blank>View in MLAS ↗</a>`)}});
  claimsLayer.addTo(map);
}}

// --- open stakeable cells (the ground to stake) ---
const cellLayer=L.geoJSON(CELLS,{{style:{{color:'#22c55e',weight:1,fillColor:'#22c55e',fillOpacity:0.28}},
  onEachFeature:(f,l)=>l.bindPopup(`<b>Open cell</b> — beside #${{f.properties.rank}} ${{esc(f.properties.name)}}<br>Appears unstaked &amp; stakeable. Verify before acting.`)}}).addTo(map);

// --- occurrences: clustered ---
const SC={{'Producer':'#7f1d1d','Producing Mine':'#7f1d1d','Past Producer':'#dc2626','Past Producing Mine':'#dc2626','Past Producing Mine (Low Tonnage)':'#e0562c','Developed Prospect':'#ea580c','Prospect':'#f59e0b','Deposit':'#eab308','Showing':'#38bdf8','Occurrence':'#94a3b8'}};
const occCluster=L.markerClusterGroup({{chunkedLoading:true,maxClusterRadius:45,disableClusteringAtZoom:11}});
L.geoJSON(OCC,{{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:4,color:'#0b1526',weight:.7,fillColor:SC[(f.properties.st||'').trim()]||'#94a3b8',fillOpacity:f.properties.p?0.95:0.6}}),
  onEachFeature:(f,l)=>{{const p=f.properties; l.bindPopup(`<b>${{esc(p.n)}}</b> <span class=pk>${{esc(p.mf)}}</span><br><b>${{esc(p.c)}}</b><br>${{esc(p.st)}}${{p.p?' · produced':''}}<br>${{p.u?`<a href="${{esc(p.u)}}" target=_blank>record ↗</a>`:''}}`);}}
}}).addTo(occCluster);
occCluster.addTo(map);

// --- leads ---
const leadMarkers={{}};
const leadsLayer=L.geoJSON(LEADS,{{pointToLayer:(f,ll)=>L.circleMarker(ll,{{radius:7,color:'#0b1526',weight:1.3,fillColor:mc(f.properties.primary_metal),fillOpacity:0.92}}),
  onEachFeature:(f,l)=>{{const p=f.properties; leadMarkers[p.lead_id]=l;
    l.bindPopup(`<b>#${{p.rank}} · ${{esc(p.name)}}</b> <span class=pk>${{esc(p.minfile)}}</span>
      <span class=metal-chip style="background:${{mc(p.primary_metal)}}">${{esc(p.primary_metal)}}</span><br>
      <b>${{esc(p.commodity)}}</b> · ${{esc(p.status)}} · score ${{p.score}}<br>
      ${{p.deposit_open?'<b style="color:#15803d">◎ Deposit itself OPEN</b>':'<b style="color:#b45309">Deposit staked</b> — adjacent open ground'}}<br>
      <b>Nearest community:</b> ${{esc(p.nearest_community)}} (${{p.community_km}} km)<br>
      ${{p.deposit_size&&p.deposit_size!=='no tonnage on record'?`<b>Size:</b> ${{esc(p.deposit_size)}}<br>`:''}}${{p.grade_str?`<b>Grade:</b> ${{esc(p.grade_str)}}<br>`:''}}
      ${{p.drill_highlights?`<b>Drill / assay:</b> ${{esc(p.drill_highlights)}}<br>`:''}}
      ${{p.encumbrances?`<b style="color:#b45309">⚠ Harder to stake:</b> ${{esc(p.encumbrances)}}<br>`:''}}
      <b>Open cells nearby:</b> ${{p.n_cells}} (~${{p.cells_area_ha}} ha)<br>
      ${{p.minfile_url?`<a href="${{esc(p.minfile_url)}}" target=_blank>Full record ↗</a>`:''}}`);
  }}}}).addTo(map);
try{{map.fitBounds(leadsLayer.getBounds().pad(0.05));}}catch(e){{map.setView([50,-86],5);}}
L.control.layers({{'Topographic':topo,'Street':osm}},{{}},{{position:'topleft'}}).addTo(map);

document.getElementById('subline').textContent=`Updated ${{STATS.generated}} · green = open stakeable ground · click any lead`;
document.getElementById('attribution').textContent=STATS.attribution;
const S=[['n_leads','Leads'],['n_deposit_open','Deposit open'],['n_with_drill_highlights','Drill data'],['n_hard_to_stake','Harder stake'],['n_candidate_leads','Candidates'],['n_occurrences','Occurrences'],['n_claims_active','Claims'],['top_n_examined','Examined']];
document.getElementById('stats').innerHTML=S.map(([k,l])=>`<div class=stat><b>${{(STATS[k]!=null&&STATS[k].toLocaleString)?STATS[k].toLocaleString():(STATS[k]??'—')}}</b><span>${{l}}</span></div>`).join('');

function buckets(p){{let m=p.metal_buckets;if(Array.isArray(m))return m;return (m||p.primary_metal||'').split(';').filter(Boolean);}}
const present=[]; LEADS.features.forEach(f=>buckets(f.properties).forEach(m=>{{if(!present.includes(m))present.push(m);}}));
present.sort((a,b)=>METAL_ORDER.indexOf(a)-METAL_ORDER.indexOf(b));
const selected=new Set(present);
const chipsEl=document.getElementById('metalChips');
chipsEl.innerHTML=present.map(m=>`<span class="chip" data-m="${{m}}"><span class=dot style="background:${{mc(m)}}"></span>${{m}}</span>`).join('');
chipsEl.addEventListener('click',e=>{{const c=e.target.closest('.chip');if(!c)return;const m=c.dataset.m;selected.has(m)?(selected.delete(m),c.classList.add('off')):(selected.add(m),c.classList.remove('off'));render();}});

const rowsEl=document.getElementById('leadrows'); let sortK='rank',sortAsc=true;
function passes(p){{
  if(document.getElementById('fDep').checked && !p.deposit_open) return false;
  if(document.getElementById('fDrill').checked && !(p.drill_highlights)) return false;
  return buckets(p).some(m=>selected.has(m));
}}
function render(){{
  let list=LEADS.features.map(f=>f.properties).filter(passes);
  list.sort((a,b)=>{{let x=a[sortK],y=b[sortK];if(sortK==='name'||sortK==='tonnes_str'){{x=x||'';y=y||'';return sortAsc?String(x).localeCompare(y):String(y).localeCompare(x);}}x=x==null?-1:x;y=y==null?-1:y;return sortAsc?x-y:y-x;}});
  rowsEl.innerHTML=list.map(p=>{{
    const dh=p.drill_highlights?`<div class=drill>⛏ ${{esc(p.drill_highlights.slice(0,110))}}${{p.drill_highlights.length>110?'…':''}}</div>`:'';
    return `<tr data-id="${{p.lead_id}}"><td>${{p.rank}}</td>
      <td><span class=lead-name>${{esc(p.name)}}</span><span class=metal-chip style="background:${{mc(p.primary_metal)}}">${{esc(p.metals_abbr||p.primary_metal)}}</span>
        ${{p.deposit_open?'<span class="badge b-dep">◎ open</span>':''}}${{p.hard_to_stake?'<span class="badge b-hard">⚠ process</span>':''}}
        <div class=sub2>${{esc(p.nearest_community)}} · ${{p.community_km}} km · ${{esc(p.basis)}}</div>${{dh}}</td>
      <td>${{esc((p.deposit_size||'').replace('no tonnage on record','—').slice(0,22))}}</td>
      <td><span class=score-pill style="background:${{mc(p.primary_metal)}}">${{p.score}}</span></td></tr>`;}}).join('');
  document.querySelectorAll('th[data-k]').forEach(th=>th.classList.toggle('sorted',th.dataset.k===sortK));
  LEADS.features.forEach(f=>{{const p=f.properties,l=leadMarkers[p.lead_id];if(!l)return;passes(p)?(map.hasLayer(l)||l.addTo(leadsLayer)):leadsLayer.removeLayer(l);}});
}}
rowsEl.addEventListener('click',e=>{{const tr=e.target.closest('tr');if(!tr)return;const l=leadMarkers[tr.dataset.id];if(l){{map.setView(l.getLatLng(),12);l.openPopup();}}}});
document.querySelectorAll('th[data-k]').forEach(th=>th.addEventListener('click',()=>{{const k=th.dataset.k;sortAsc=(k===sortK)?!sortAsc:(k==='rank'||k==='name');sortK=k;render();}}));
['fDep','fDrill'].forEach(id=>document.getElementById(id).addEventListener('change',render));
document.getElementById('tClaims').addEventListener('change',e=>{{e.target.checked?claimsLayer.addTo(map):map.removeLayer(claimsLayer);}});
document.getElementById('tCells').addEventListener('change',e=>{{e.target.checked?cellLayer.addTo(map):map.removeLayer(cellLayer);}});
document.getElementById('tOcc').addEventListener('change',e=>{{e.target.checked?occCluster.addTo(map):map.removeLayer(occCluster);}});

const legend=L.control({{position:'bottomleft'}});
legend.onAdd=()=>{{const d=L.DomUtil.create('div','legend');d.innerHTML='<b>Leads by metal</b><br>'+present.map(m=>`<i style="background:${{mc(m)}}"></i>${{m}}`).join('<br>')+'<br><i style="background:#22c55e"></i>Open stakeable cell<br><i style="background:#64748b"></i>Active claim';return d;}};
legend.addTo(map);
render();
</script></body></html>
"""


if __name__ == "__main__":
    build("data/bc/out", "site/bc.html", wms=BC_WMS)
    build("data/on/out", "site/on.html", inline_claims=True)
