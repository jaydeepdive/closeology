"""Explore map = ONE unified all-Canada map of every opportunity. Every
jurisdiction's leads on a single filterable layer (colour by dominant metal,
sized by score); clicking a lead opens its FULL detail in the sidebar (the same
data as the priority page) and draws its actual open, stakeable ground on the
map automatically."""
import os
import re
import json
import shutil
import geopandas as gpd
from shapely.geometry import mapping
import site_theme as T
from build_priority import _load as load_leads, GROUPS
from config import METAL_COLOR, METAL_ORDER

VEN = os.path.join(os.path.dirname(__file__), "vendor")


def _r(p):
    return open(os.path.join(VEN, p)).read()


def _borders(regions_cfg):
    feats, labels = [], []
    for rc in regions_cfg:
        bp = os.path.join("data", "keep", f"{rc['slug']}_boundary.parquet")
        if not os.path.exists(bp):
            continue
        try:
            g = gpd.read_parquet(bp).to_crs(4326)
            geom = g.geometry.union_all() if hasattr(g.geometry, "union_all") else g.geometry.unary_union
            simp = geom.simplify(0.03)
            feats.append({"type": "Feature", "properties": {"n": rc["name"]}, "geometry": mapping(simp)})
            c = simp.representative_point()
            labels.append({"n": rc["slug"].upper(), "lat": round(c.y, 4), "lon": round(c.x, 4)})
        except Exception:
            continue
    return {"type": "FeatureCollection", "features": feats}, labels


def build(regions_cfg, html_path):
    site_dir = os.path.dirname(html_path) or "site"
    leads = []
    jname = {}
    for rc in regions_cfg:
        csv = os.path.join(rc["dir"], "out", "leads.csv")
        if not (rc.get("live") and os.path.exists(csv)):
            continue
        juris = rc["slug"].upper()
        jname[juris] = rc["name"]
        for p in load_leads(csv, juris):
            if not p.get("lat") or not p.get("lon"):
                continue
            p["id"] = juris + "_" + str(p.get("lead_id"))
            p["region"] = rc["slug"].lower()
            leads.append(p)
        oc = os.path.join(rc["dir"], "out", "opencells.geojson")
        if os.path.exists(oc):
            shutil.copy(oc, os.path.join(site_dir, f"{rc['slug'].lower()}_opencells.geojson"))
        cn = os.path.join(rc["dir"], "out", "claims_near.geojson")
        if os.path.exists(cn):
            shutil.copy(cn, os.path.join(site_dir, f"{rc['slug'].lower()}_claims_near.geojson"))
    leads.sort(key=lambda x: -x["score"])

    counts_j, counts_m = {}, {}
    for l in leads:
        counts_j[l["juris"]] = counts_j.get(l["juris"], 0) + 1
        counts_m[l["dmetal"]] = counts_m.get(l["dmetal"], 0) + 1
    jopts = ['<option value="all">All jurisdictions ({0})</option>'.format(len(leads))]
    for j in sorted(counts_j, key=lambda x: -counts_j[x]):
        jopts.append('<option value="{0}">{1} ({2})</option>'.format(j, jname.get(j, j), counts_j[j]))
    order = {m: i for i, m in enumerate(METAL_ORDER)}

    def _gc(g):
        return sum(counts_m.get(m, 0) for m in g)
    mopts = ['<option value="all">All metals ({0})</option>'.format(len(leads)),
             '<optgroup label="Groups">',
             '<option value="prec">Precious — Au, Ag, PGE ({0})</option>'.format(_gc(GROUPS["prec"])),
             '<option value="base">Base — Cu, Pb, Zn, Ni, Sn ({0})</option>'.format(_gc(GROUPS["base"])),
             '<option value="crit">Critical / specialty ({0})</option>'.format(_gc(GROUPS["crit"])),
             '</optgroup><optgroup label="Single metal">']
    for m in sorted(counts_m, key=lambda x: (order.get(x, 99), -counts_m[x])):
        mopts.append('<option value="{0}">{0} ({1})</option>'.format(m, counts_m[m]))
    mopts.append("</optgroup>")

    borders, blabels = _borders(regions_cfg)
    html = TEMPLATE.format(
        fonts=T.FONTS, theme_css=T.THEME_CSS, header=T.header("app.html"),
        leaflet_css=_r("leaflet.css"), mc_css=_r("mc.css") + _r("mcd.css"),
        leaflet_js=_r("leaflet.js"), mc_js=_r("mc.js"),
        leads_json=json.dumps(leads, separators=(",", ":")),
        metal_color=json.dumps(METAL_COLOR),
        groups_json=json.dumps({k: sorted(v) for k, v in GROUPS.items()}),
        borders_json=json.dumps(borders, separators=(",", ":")),
        blabels_json=json.dumps(blabels, separators=(",", ":")),
        jopts="".join(jopts), mopts="".join(mopts),
    )
    open(html_path, "w").write(html)
    body = html
    for pat in [r'<!DOCTYPE html>', r'<html[^>]*>', r'</html>', r'<head>', r'</head>',
                r'<body>', r'</body>', r'<meta[^>]*>', r'<title>.*?</title>']:
        body = re.sub(pat, '', body, flags=re.I | re.S)
    open(html_path.replace('.html', '_artifact.html'), "w").write(body.strip())
    print(f"[app] {html_path} ({os.path.getsize(html_path)//1024} KB) — {len(leads)} leads, one unified map")


TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Explore map · Project Closeology</title>
{fonts}
<style>{leaflet_css}</style><style>{mc_css}</style>
<script>{leaflet_js}</script><script>{mc_js}</script>
<style>{theme_css}
html,body{{height:100%;}} body{{display:flex;flex-direction:column;height:100vh;overflow:hidden;}}
#app{{display:flex;flex:1;min-height:0;}}
#map{{flex:1;height:100%;}}
#side{{width:420px;max-width:44vw;background:#fff;border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0;}}
.filters{{padding:11px 14px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:1fr 1fr;gap:8px;}}
.filters .fsel{{display:flex;flex-direction:column;gap:4px;}} .filters .fsel.wide{{grid-column:1/3;}}
.filters label{{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);font-weight:700;}}
.filters input,.filters select{{height:36px;padding:0 10px;border:1px solid var(--line);border-radius:8px;font-size:13.5px;background:#fff;color:var(--ink);}}
.filters input[type=range]{{height:auto;padding:0;border:0;}}
.cnt{{padding:7px 14px;color:var(--mut);font-size:12.5px;border-bottom:1px solid var(--line);}}
#list{{flex:1;overflow:auto;}}
.row{{padding:9px 14px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;gap:10px;align-items:baseline;}}
.row:hover{{background:var(--panel);}} .row.sel{{background:#fdeaea;}}
.row .sc{{font-family:'Bitter',serif;font-weight:800;color:var(--red);min-width:26px;}}
.row .nm{{font-weight:600;font-size:13.5px;}} .row .mt{{color:var(--mut);font-size:11.5px;margin-top:2px;}}
.jp{{font-size:9.5px;font-weight:700;padding:1px 6px;border-radius:10px;background:var(--chip);color:#333;margin-left:5px;}}
/* detail panel */
#detail{{flex:1;overflow:auto;display:none;}}
.dback{{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:9px 14px;font-size:13px;font-weight:600;color:var(--red);cursor:pointer;z-index:2;}}
.dhd{{padding:14px 16px 6px;}} .dhd .dn{{font-family:'Bitter',serif;font-weight:800;font-size:20px;line-height:1.1;}}
.dhd .dsub{{color:var(--mut);font-size:12.5px;margin-top:3px;}}
.pill{{font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;margin-left:6px;vertical-align:middle;}}
.p-open{{background:#e7f6ec;color:#127a3a;}} .p-hard{{background:#fff4e5;color:#9a5b00;}}
.dscore{{display:inline-block;font-family:'Bitter',serif;font-weight:800;font-size:22px;color:var(--red);}}
.chips{{padding:0 16px 6px;}} .chip{{display:inline-block;background:var(--chip);color:#2d3748;font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px;margin:2px 5px 2px 0;}}
.sec{{padding:10px 16px;border-top:1px solid var(--line);}}
.sechd{{font-size:10.5px;letter-spacing:1px;text-transform:uppercase;color:var(--mut);font-weight:700;margin-bottom:7px;}}
.fact{{margin:5px 0;font-size:13px;}} .fact .k{{color:var(--mut);font-weight:600;display:inline-block;min-width:92px;}}
.prow{{display:flex;gap:9px;align-items:baseline;margin:4px 0;font-size:12.5px;}}
.pbadge{{flex:0 0 auto;font-family:'Bitter',serif;font-weight:800;color:var(--red);min-width:30px;}}
.prow .pl{{font-weight:600;}} .prow .pn{{color:var(--mut);}}
.ground{{background:#eafaf0;border:1px solid #b7e4c7;border-radius:8px;padding:9px 11px;font-size:12.5px;color:#155e34;margin:0 16px 10px;}}
.intercept{{font-size:12px;padding:5px 9px;margin-top:5px;background:#f0f9f4;border-left:3px solid #127a3a;border-radius:0 6px 6px 0;}}
.prodbox{{font-size:12px;background:#fbf6ef;border-left:3px solid #9a5b00;padding:6px 9px;border-radius:0 6px 6px 0;margin-top:6px;}}
.dbtn{{display:inline-block;background:var(--red);color:#fff;font-size:12.5px;font-weight:600;padding:7px 13px;border-radius:8px;margin:2px 16px 14px;}} .dbtn:hover{{text-decoration:none;opacity:.92;}}
.legend{{background:rgba(255,255,255,.95);padding:7px 9px;border-radius:8px;font-size:10.5px;line-height:1.5;border:1px solid var(--line);max-width:160px;}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px;}}
.own{{font-weight:600;color:var(--ink);}}
.claimtip{{font-size:11.5px;line-height:1.35;}}
@media(max-width:820px){{#app{{flex-direction:column;}}#map{{height:50%;}}#side{{width:100%;max-width:100%;height:50%;}}}}
</style></head><body>
{header}
<div id="app">
  <div id="map"></div>
  <div id="side">
    <div class="filters">
      <div class="fsel wide"><label>Search</label><input id="q" placeholder="Name, metal, community…"/></div>
      <div class="fsel"><label>Jurisdiction</label><select id="jsel">{jopts}</select></div>
      <div class="fsel"><label>Dominant metal</label><select id="msel">{mopts}</select></div>
      <div class="fsel wide"><label>Minimum score: <span id="sv">0</span></label>
        <input id="sc" type="range" min="0" max="90" value="0" step="5"></div>
    </div>
    <div class="cnt" id="cnt"></div>
    <div id="list"></div>
    <div id="detail"></div>
  </div>
</div>
<script>
const LEADS={leads_json}, MC={metal_color}, GROUPS={groups_json};
const BORDERS={borders_json}, BLABELS={blabels_json};
const BY={{}}; LEADS.forEach(p=>BY[p.id]=p);
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
const col=m=>MC[m]||'#8091a5';
const topo=L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:17,attribution:'&copy; OpenTopoMap'}});
const osm=L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}});
const map=L.map('map',{{layers:[topo],preferCanvas:true}}).setView([58,-96],4);
L.control.layers({{'Topographic':topo,'Street':osm}}).addTo(map);
L.geoJSON(BORDERS,{{interactive:false,style:{{color:'#334155',weight:1.4,opacity:.6,fill:false,dashArray:'4 3'}}}}).addTo(map);
L.layerGroup(BLABELS.map(b=>L.marker([b.lat,b.lon],{{interactive:false,icon:L.divIcon({{className:'',html:`<span style="font:700 11px Bitter,serif;color:#475569;text-shadow:0 0 3px #fff,0 0 3px #fff">${{b.n}}</span>`}})}}))).addTo(map);
const cluster=L.markerClusterGroup({{chunkedLoading:true,maxClusterRadius:48,disableClusteringAtZoom:9}});
map.addLayer(cluster);
let groundLayer=null, claimLayer=null, selMarker=null, selId=null;
const markers={{}};
let jf='all', mf='all', mins=0, q='';
function metalMatch(dm){{ if(mf==='all')return true; if(GROUPS[mf])return GROUPS[mf].includes(dm); return dm===mf; }}
function pass(p){{ return (jf==='all'||p.juris===jf) && metalMatch(p.dmetal) && p.score>=mins &&
  (!q || (p.name+' '+p.dmetal+' '+p.commodity+' '+p.community).toLowerCase().includes(q)); }}
LEADS.forEach(p=>{{
  const r=3.5+Math.max(0,Math.min(p.score,90))/13, op=0.5+Math.max(0,Math.min(p.score,90))/180;
  const m=L.circleMarker([p.lat,p.lon],{{radius:r,color:'#0b1526',weight:.8,fillColor:col(p.dmetal),fillOpacity:op}});
  m.on('click',()=>select(p.id)); markers[p.id]=m;
}});
function refresh(){{
  cluster.clearLayers();
  const vis=LEADS.filter(pass);
  vis.forEach(p=>cluster.addLayer(markers[p.id]));
  document.getElementById('cnt').textContent=vis.length.toLocaleString()+' opportunities'+(mins>0?(' scoring ≥ '+mins):'');
  document.getElementById('list').innerHTML=vis.slice(0,500).map(p=>`<div class=row data-id="${{p.id}}"><span class=sc>${{p.score}}</span>
    <div><span class=nm>${{esc(p.name)}}</span><span class=jp>${{p.juris}}</span>
    <div class=mt><span style="color:${{col(p.dmetal)}}">●</span> ${{esc(p.dmetal)}}${{p.deposit_open?' · deposit open':''}} · ${{esc(p.community||'')}}</div></div></div>`).join('')
    ||'<div class=row>No opportunities match the filters.</div>';
}}
function clearOverlays(){{
  if(groundLayer){{map.removeLayer(groundLayer);groundLayer=null;}}
  if(claimLayer){{map.removeLayer(claimLayer);claimLayer=null;}}
}}
async function showGround(p){{
  clearOverlays();
  let bounds=null;
  try{{
    const r=await fetch(p.region+'_opencells.geojson'); const d=await r.json();
    // the dissolved open-ground block(s) this lead belongs to (shared with any
    // neighbouring occurrence sitting in the same open ground)
    const fs=d.features.filter(f=>String(f.properties.lead_ids||'').split(',').indexOf(String(p.lead_id))>=0);
    if(fs.length){{
      groundLayer=L.geoJSON({{type:'FeatureCollection',features:fs}},
        {{interactive:false,style:{{color:'#0f7a3a',weight:1.5,opacity:.95,fillColor:'#22c55e',fillOpacity:.34}}}}).addTo(map);
      bounds=groundLayer.getBounds();
    }}
  }}catch(e){{}}
  // real neighbouring claims (already-staked ground) so the open ground reads
  // as the genuine gaps in tenure — and so the user can see WHO is nearby and
  // research what they may have found before committing to stake.
  const owners={{}}; let nearCount=0;
  try{{
    const cr=await fetch(p.region+'_claims_near.geojson');
    if(cr.ok){{
      const cd=await cr.json();
      const near=(cd.features||[]).filter(f=>{{
        try{{ const c=L.geoJSON(f).getBounds().getCenter();
          return Math.abs(c.lat-p.lat)<0.12 && Math.abs(c.lng-p.lon)<0.22; }}catch(_){{return false;}}
      }});
      nearCount=near.length;
      if(near.length){{
        claimLayer=L.geoJSON({{type:'FeatureCollection',features:near}},{{
          style:{{color:'#8a6d3b',weight:1,opacity:.8,fillColor:'#c9a227',fillOpacity:.16}},
          onEachFeature:(f,lyr)=>{{
            const pr=f.properties||{{}};
            const own=(pr.owner||'').replace(/\s*-\s*100%$/,'').trim();
            if(own){{ owners[own]=(owners[own]||0)+1; }}
            const tip=`${{own?'<b>'+esc(own)+'</b><br>':''}}${{pr.cname?esc(pr.cname)+' ':''}}${{pr.claim?'#'+esc(pr.claim):''}}${{pr.expiry?'<br><span style=\"color:#666\">good to '+esc(pr.expiry)+'</span>':''}}`;
            if(tip.trim()) lyr.bindTooltip(tip,{{sticky:true,direction:'top',className:'claimtip'}});
          }}
        }}).addTo(map);
        if(!bounds) bounds=claimLayer.getBounds();
      }}
    }}
  }}catch(e){{}}
  // fill the "who's nearby" list in the sidebar detail
  const nb=document.getElementById('nearby');
  if(nb){{
    const ranked=Object.keys(owners).sort((a,b)=>owners[b]-owners[a]);
    if(ranked.length){{
      nb.innerHTML=`<div class=sechd>Who's nearby — ${{ranked.length}} holder(s) within ~5 km</div>`+
        ranked.slice(0,12).map(o=>`<div class=fact><span class=own>${{esc(o)}}</span> <span class=pn>${{owners[o]}} claim${{owners[o]>1?'s':''}}</span></div>`).join('')+
        `<div class=pn style="margin-top:5px">Hover any gold claim on the map for the holder and tenure number.</div>`;
    }} else if(nearCount>0){{
      nb.innerHTML=`<div class=sechd>Who's nearby — ${{nearCount}} claim(s) within ~5 km</div><div class=pn>Neighbouring ground is staked (shown in gold), but this jurisdiction's dataset doesn't publish holder names. Check the provincial registry for the current holders.</div>`;
    }} else {{
      nb.innerHTML=`<div class=sechd>Who's nearby</div><div class=pn>No active claims recorded within ~5 km — this ground looks open with no immediate neighbours.</div>`;
    }}
  }}
  if(bounds && bounds.isValid()){{ map.fitBounds(bounds.pad(0.55)); }}
  else {{ map.setView([p.lat,p.lon],12); }}
}}
function detailHTML(p){{
  const parts=(p.parts||[]).map(x=>`<div class=prow><span class=pbadge>+${{x.pts}}</span><span><span class=pl>${{esc(x.label)}}.</span> <span class=pn>${{esc(x.note)}}</span></span></div>`).join('');
  const facts=[];
  facts.push(`<div class=fact><span class=k>Status</span>${{esc(p.status)||'—'}}</div>`);
  if(p.commodity) facts.push(`<div class=fact><span class=k>Commodities</span>${{esc(p.commodity)}}</div>`);
  if(p.grade) facts.push(`<div class=fact><span class=k>Grade</span>${{esc(p.grade)}}</div>`);
  if(p.value_parts&&p.value_parts.length) facts.push(`<div class=fact><span class=k>Value split</span>${{p.value_parts.map(x=>esc(x[0])+' <b>$'+x[1]+'</b>').join(' · ')}} <span class=pn>/t in-situ</span></div>`);
  if(p.size) facts.push(`<div class=fact><span class=k>Size</span>${{esc(p.size)}}</div>`);
  if(p.spend_str) facts.push(`<div class=fact><span class=k>Expl. spend</span>${{esc(p.spend_str)}}${{p.operators?(' · '+esc(p.operators)):''}}</div>`);
  if(p.community) facts.push(`<div class=fact><span class=k>Nearest town</span>${{esc(p.community)}}${{p.community_km!==''?(' · '+esc(p.community_km)+' km'):''}}</div>`);
  const drill=(p.drill_top&&p.drill_top.length)?p.drill_top.map(x=>`<div class=intercept><b>${{esc(x.text)}}</b> ≈ $${{x.vpt}}/t</div>`).join(''):(p.drill?`<div class=intercept>${{esc(p.drill)}}</div>`:'');
  return `<div class="dback" onclick="deselect()">← All opportunities</div>
    <div class=dhd><span class=dscore>${{p.score}}</span>
      <span class=dn style="margin-left:8px">${{esc(p.name)}}</span>
      <span class="pill" style="background:${{col(p.dmetal)}}22;color:${{col(p.dmetal)}}">${{esc(p.dmetal)}}</span>
      ${{p.deposit_open?'<span class="pill p-open">deposit open</span>':''}}${{p.hard?'<span class="pill p-hard">harder to stake</span>':''}}
      <div class=dsub>${{esc(p.juris)}} · ${{esc(p.minfile||'')}}</div></div>
    ${{(p.n_cells)?`<div class=ground>◎ <b>Stakeable ground</b> — ${{esc(p.n_cells)}} open cell(s), ~${{esc(p.cells_ha)}} ha ${{p.deposit_open?'on and around the deposit':'adjacent to the deposit'}}. The green block on the map is the real open ground carved around any existing claims (shown in gold). Verify exact cells in the official registry before staking.</div>`:''}}
    <div class=chips>${{p.metals?`<span class=chip>${{esc(p.metals)}}</span>`:''}}${{p.grade?`<span class=chip>${{esc(p.grade)}}</span>`:''}}</div>
    <div class=sec><div class=sechd>Qualifying details</div>${{facts.join('')}}
      ${{p.production?`<div class=prodbox><b>Past production.</b> ${{esc(p.production)}}</div>`:''}}
      ${{drill?(`<div class=sechd style="margin-top:10px">⛏ Drill results</div>`+drill):''}}</div>
    <div class=sec id=nearby><div class=sechd>Who's nearby</div><div class=pn>Loading neighbouring claim holders…</div></div>
    <div class=sec><div class=sechd>Why it ranks here</div>${{parts}}</div>
    ${{p.url?`<a class=dbtn href="${{esc(p.url)}}" target=_blank>Full record ↗</a>`:''}}`;
}}
function select(id){{
  const p=BY[id]; if(!p) return; selId=id;
  document.querySelectorAll('.row').forEach(r=>r.classList.toggle('sel',r.dataset.id===id));
  document.getElementById('list').style.display='none';
  document.getElementById('cnt').style.display='none';
  const det=document.getElementById('detail'); det.style.display='block'; det.innerHTML=detailHTML(p); det.scrollTop=0;
  showGround(p);
}}
function deselect(){{
  selId=null;
  clearOverlays();
  document.getElementById('detail').style.display='none';
  document.getElementById('list').style.display='block';
  document.getElementById('cnt').style.display='block';
  document.querySelectorAll('.row.sel').forEach(r=>r.classList.remove('sel'));
}}
document.getElementById('list').addEventListener('click',e=>{{const r=e.target.closest('.row');if(r&&r.dataset.id)select(r.dataset.id);}});
document.getElementById('jsel').addEventListener('change',e=>{{jf=e.target.value;deselect();refresh();}});
document.getElementById('msel').addEventListener('change',e=>{{mf=e.target.value;deselect();refresh();}});
document.getElementById('sc').addEventListener('input',e=>{{mins=+e.target.value;document.getElementById('sv').textContent=mins;deselect();refresh();}});
document.getElementById('q').addEventListener('input',e=>{{q=e.target.value.toLowerCase();deselect();refresh();}});
const legend=L.control({{position:'bottomleft'}});
legend.onAdd=()=>{{const d=L.DomUtil.create('div','legend');
  const ms=['Gold','Silver','Copper','Zinc','Lead','Nickel','Uranium','Lithium','Other metallic'];
  d.innerHTML='<b>Marker = lead</b> (size = score)<br>'+ms.map(m=>`<i style="background:${{col(m)}}"></i>${{m}}`).join('<br>')
    +'<br><i style="background:#22c55e;border:1px solid #0f7a3a;border-radius:2px"></i>Open ground (stakeable)'
    +'<br><i style="background:#c9a227;border:1px solid #8a6d3b;border-radius:2px"></i>Existing claims (staked)';return d;}};
legend.addTo(map);
refresh();
</script></body></html>
"""
