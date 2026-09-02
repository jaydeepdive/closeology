"""Explore map = ONE unified all-Canada map of every opportunity (not a
province switcher). Every jurisdiction's leads are merged into a single
filterable layer (colour by dominant metal, filter by metal / jurisdiction /
minimum score, search). A lead's stakeable open ground is fetched on demand
when it's opened, so the page stays light while covering all of Canada."""
import os
import re
import json
import shutil
import geopandas as gpd
from shapely.geometry import mapping
import site_theme as T
import enrich_facts as E
from config import METAL_COLOR, METAL_ORDER, metal_bucket


def _borders(regions_cfg):
    """Simplified province/territory outlines + a centroid label for each, so a
    lead sitting on a border line is easy to place."""
    feats, labels = [], []
    for rc in regions_cfg:
        bp = os.path.join("data", "keep", f"{rc['slug']}_boundary.parquet")
        if not os.path.exists(bp):
            continue
        try:
            g = gpd.read_parquet(bp).to_crs(4326)
            geom = g.geometry.union_all() if hasattr(g.geometry, "union_all") else g.geometry.unary_union
            simp = geom.simplify(0.03)
            feats.append({"type": "Feature", "properties": {"n": rc["name"]},
                          "geometry": mapping(simp)})
            c = simp.representative_point()
            labels.append({"n": rc["slug"].upper(), "lat": round(c.y, 4), "lon": round(c.x, 4)})
        except Exception:
            continue
    return {"type": "FeatureCollection", "features": feats}, labels

VEN = os.path.join(os.path.dirname(__file__), "vendor")

GROUPS = {
    "prec": ["Gold", "Silver", "Platinum", "Palladium"],
    "base": ["Copper", "Lead", "Zinc", "Nickel", "Tin"],
    "crit": ["Lithium", "Cobalt", "Uranium", "Rare earths", "Vanadium", "Niobium", "Tantalum",
             "Beryllium", "Antimony", "Bismuth", "Molybdenum", "Tungsten", "Graphite",
             "Chromium", "Titanium", "Manganese"],
}


def _r(p):
    return open(os.path.join(VEN, p)).read()


def _slim(props, juris):
    grade = str(props.get("grade_str") or "")
    g_sorted, top_metal = E.sort_grade_by_value(grade)
    _v, vparts = E.value_parts(g_sorted)
    dom = vparts[0][0] if vparts else (top_metal or props.get("primary_metal") or "")
    dmetal = metal_bucket(dom) if dom else "Other metallic"
    return {
        "id": juris + "_" + str(props.get("lead_id")),   # globally unique
        "lid": props.get("lead_id"),                       # region-local id (for cell fetch)
        "n": props.get("name"), "j": juris,
        "s": int(props.get("score") or 0), "dm": dmetal,
        "st": props.get("status"), "o": 1 if props.get("deposit_open") else 0,
        "c": props.get("commodity"), "g": g_sorted, "mf": props.get("minfile"),
        "u": props.get("minfile_url") or "", "nc": props.get("n_cells"),
        "ha": props.get("cells_area_ha"), "tn": props.get("nearest_community"),
        "km": props.get("community_km"),
        "lat": round(float(props.get("lat") or 0), 5), "lon": round(float(props.get("lon") or 0), 5),
    }


def build(regions_cfg, html_path):
    site_dir = os.path.dirname(html_path) or "site"
    feats = []
    for rc in regions_cfg:
        gj = os.path.join(rc["dir"], "out", "leads.geojson")
        if not (rc.get("live") and os.path.exists(gj)):
            continue
        juris = rc["slug"].upper()
        d = json.loads(open(gj).read())
        for f in d.get("features", []):
            p = f.get("properties", {})
            if p.get("lat") is None or p.get("lon") is None:
                continue
            feats.append(_slim(p, juris))
        # per-region open cells for on-demand fetch (deployed with the site artifact)
        oc = os.path.join(rc["dir"], "out", "opencells.geojson")
        if os.path.exists(oc):
            shutil.copy(oc, os.path.join(site_dir, f"{juris.lower()}_opencells.geojson"))
    feats.sort(key=lambda x: -x["s"])

    # filter option lists
    counts_j = {}
    counts_m = {}
    for f in feats:
        counts_j[f["j"]] = counts_j.get(f["j"], 0) + 1
        counts_m[f["dm"]] = counts_m.get(f["dm"], 0) + 1
    jname = {rc["slug"].upper(): rc["name"] for rc in regions_cfg}
    jopts = ['<option value="all">All jurisdictions ({0})</option>'.format(len(feats))]
    for j in sorted(counts_j, key=lambda x: -counts_j[x]):
        jopts.append('<option value="{0}">{1} ({2})</option>'.format(j, jname.get(j, j), counts_j[j]))
    order = {m: i for i, m in enumerate(METAL_ORDER)}

    def _gc(g):
        return sum(counts_m.get(m, 0) for m in g)
    mopts = ['<option value="all">All metals ({0})</option>'.format(len(feats)),
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
        leads_json=json.dumps(feats, separators=(",", ":")),
        metal_color=json.dumps(METAL_COLOR), groups_json=json.dumps(GROUPS),
        borders_json=json.dumps(borders, separators=(",", ":")),
        blabels_json=json.dumps(blabels, separators=(",", ":")),
        jopts="".join(jopts), mopts="".join(mopts), n=len(feats),
    )
    open(html_path, "w").write(html)
    body = html
    for pat in [r'<!DOCTYPE html>', r'<html[^>]*>', r'</html>', r'<head>', r'</head>',
                r'<body>', r'</body>', r'<meta[^>]*>', r'<title>.*?</title>']:
        body = re.sub(pat, '', body, flags=re.I | re.S)
    open(html_path.replace('.html', '_artifact.html'), "w").write(body.strip())
    print(f"[app] {html_path} ({os.path.getsize(html_path)//1024} KB) — {len(feats)} leads, one unified map")


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
#side{{width:390px;max-width:42vw;background:#fff;border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0;}}
.filters{{padding:12px 14px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:8px;}}
.filters .fsel{{display:flex;flex-direction:column;gap:4px;}}
.filters label{{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);font-weight:700;}}
.filters input,.filters select{{height:38px;padding:0 10px;border:1px solid var(--line);border-radius:8px;font-size:13.5px;background:#fff;color:var(--ink);}}
.filters .rangewrap{{display:flex;align-items:center;gap:8px;}} .filters input[type=range]{{height:auto;padding:0;flex:1;}}
.cnt{{padding:8px 14px;color:var(--mut);font-size:12.5px;border-bottom:1px solid var(--line);}}
#list{{flex:1;overflow:auto;}}
.row{{padding:9px 14px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;gap:10px;align-items:baseline;}}
.row:hover{{background:var(--panel);}}
.row .sc{{font-family:'Bitter',serif;font-weight:800;color:var(--red);min-width:26px;}}
.row .nm{{font-weight:600;font-size:13.5px;}} .row .mt{{color:var(--mut);font-size:11.5px;margin-top:2px;}}
.jp{{font-size:9.5px;font-weight:700;padding:1px 6px;border-radius:10px;background:var(--chip);color:#333;margin-left:5px;}}
.legend{{background:rgba(255,255,255,.95);padding:7px 9px;border-radius:8px;font-size:10.5px;line-height:1.5;border:1px solid var(--line);max-width:150px;}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px;}}
.leaflet-popup-content{{font-size:12.5px;line-height:1.5;}} .leaflet-popup-content b{{color:#111;}}
@media(max-width:820px){{#app{{flex-direction:column;}}#map{{height:52%;}}#side{{width:100%;max-width:100%;height:48%;}}}}
</style></head><body>
{header}
<div id="app">
  <div id="map"></div>
  <div id="side">
    <div class="filters">
      <div class="fsel"><label>Search</label><input id="q" placeholder="Name, metal, community…"/></div>
      <div class="fsel"><label>Jurisdiction</label><select id="jsel">{jopts}</select></div>
      <div class="fsel"><label>Dominant metal</label><select id="msel">{mopts}</select></div>
      <div class="fsel"><label>Minimum score: <span id="sv">0</span></label>
        <div class="rangewrap"><input id="sc" type="range" min="0" max="100" value="0" step="5"></div></div>
    </div>
    <div class="cnt" id="cnt"></div>
    <div id="list"></div>
  </div>
</div>
<script>
const LEADS={leads_json}, MC={metal_color}, GROUPS={groups_json};
const BORDERS={borders_json}, BLABELS={blabels_json};
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
const col=m=>MC[m]||'#8091a5';
const topo=L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:17,attribution:'&copy; OpenTopoMap'}});
const osm=L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}});
const map=L.map('map',{{layers:[topo],preferCanvas:true}}).setView([58,-96],4);
L.control.layers({{'Topographic':topo,'Street':osm}}).addTo(map);
// province / territory borders + labels
L.geoJSON(BORDERS,{{interactive:false,style:{{color:'#334155',weight:1.4,opacity:.6,fill:false,dashArray:'4 3'}}}}).addTo(map);
const blabelLayer=L.layerGroup(BLABELS.map(b=>L.marker([b.lat,b.lon],{{interactive:false,
  icon:L.divIcon({{className:'',html:`<span style="font:700 11px Bitter,serif;color:#475569;text-shadow:0 0 3px #fff,0 0 3px #fff">${{b.n}}</span>`}})}}))).addTo(map);
const cluster=L.markerClusterGroup({{chunkedLoading:true,maxClusterRadius:48,disableClusteringAtZoom:9}});
map.addLayer(cluster);
let cellLayer=null;
const markers={{}};
let jf='all', mf='all', mins=0, q='';
function metalMatch(dm){{ if(mf==='all')return true; if(GROUPS[mf])return GROUPS[mf].includes(dm); return dm===mf; }}
function pass(p){{ return (jf==='all'||p.j===jf) && metalMatch(p.dm) && p.s>=mins &&
  (!q || (p.n+' '+p.dm+' '+p.c+' '+p.tn).toLowerCase().includes(q)); }}
function popup(p){{
  return `<b>${{esc(p.n)}}</b> <span style="color:#64748b">${{esc(p.mf||'')}}</span><br>
    <span style="color:${{col(p.dm)}};font-weight:700">${{esc(p.dm)}}</span> · ${{esc(p.st||'')}} · score <b>${{p.s}}</b><br>
    ${{esc(p.c||'')}}<br>${{p.g?('<b>Grade:</b> '+esc(p.g)+'<br>'):''}}
    ${{p.o?'<b style="color:#15803d">◎ deposit ground open</b><br>':''}}
    <b>Open ground:</b> ${{esc(p.nc||'0')}} cell(s) · ${{esc(p.ha||'0')}} ha<br>
    ${{p.tn?('Nearest: '+esc(p.tn)+' ('+esc(p.km)+' km)<br>'):''}}
    <a href="#" onclick="showCells('${{p.j}}','${{p.lid}}');return false;">Show stakeable ground →</a>
    ${{p.u?(' · <a href="'+esc(p.u)+'" target=_blank>record ↗</a>'):''}}`;
}}
LEADS.forEach(p=>{{
  // size + opacity scale with score so quality leads visually dominate the tail
  const r=3.5+Math.max(0,Math.min(p.s,90))/13, op=0.45+Math.max(0,Math.min(p.s,90))/180;
  const m=L.circleMarker([p.lat,p.lon],{{radius:r,color:'#0b1526',weight:.8,fillColor:col(p.dm),fillOpacity:op}});
  m.bindPopup(()=>popup(p)); m._p=p; markers[p.id]=m;
}});
function refresh(){{
  cluster.clearLayers();
  const vis=LEADS.filter(pass);
  vis.forEach(p=>cluster.addLayer(markers[p.id]));
  document.getElementById('cnt').textContent=vis.length.toLocaleString()+' opportunities shown';
  const L2=vis.slice(0,400).map(p=>`<div class=row data-id="${{p.id}}"><span class=sc>${{p.s}}</span>
    <div><span class=nm>${{esc(p.n)}}</span><span class=jp>${{p.j}}</span>
    <div class=mt><span style="color:${{col(p.dm)}}">●</span> ${{esc(p.dm)}}${{p.o?' · open':''}} · ${{esc(p.tn||'')}}</div></div></div>`).join('');
  document.getElementById('list').innerHTML=L2||'<div class=row>No opportunities match the filters.</div>';
}}
window.showCells=async function(j,id){{
  try{{
    const r=await fetch(j.toLowerCase()+'_opencells.geojson'); const d=await r.json();
    if(cellLayer) map.removeLayer(cellLayer);
    const fs=d.features.filter(f=>f.properties.lead_id===id);
    cellLayer=L.geoJSON({{type:'FeatureCollection',features:fs}},{{style:{{color:'#127a3a',weight:1,fillColor:'#22c55e',fillOpacity:.35}}}}).addTo(map);
    if(fs.length) map.fitBounds(cellLayer.getBounds().pad(0.4));
  }}catch(e){{}}
}};
document.getElementById('list').addEventListener('click',e=>{{const r=e.target.closest('.row');if(!r)return;
  const m=markers[r.dataset.id]; if(m){{map.setView(m.getLatLng(),11); m.openPopup();}}}});
document.getElementById('jsel').addEventListener('change',e=>{{jf=e.target.value;refresh();}});
document.getElementById('msel').addEventListener('change',e=>{{mf=e.target.value;refresh();}});
document.getElementById('sc').addEventListener('input',e=>{{mins=+e.target.value;document.getElementById('sv').textContent=mins;refresh();}});
document.getElementById('q').addEventListener('input',e=>{{q=e.target.value.toLowerCase();refresh();}});
const legend=L.control({{position:'bottomleft'}});
legend.onAdd=()=>{{const d=L.DomUtil.create('div','legend');
  const ms=['Gold','Silver','Copper','Zinc','Lead','Nickel','Uranium','Lithium','Other metallic'];
  d.innerHTML='<b>Dominant metal</b><br>'+ms.map(m=>`<i style="background:${{col(m)}}"></i>${{m}}`).join('<br>')
    +'<br><i style="background:#22c55e;border-radius:2px"></i>Open ground';return d;}};
legend.addTo(map);
refresh();
</script></body></html>
"""
