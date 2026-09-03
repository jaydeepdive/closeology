"""Unified, cross-jurisdiction priority list: every BC + Ontario lead ranked on
one scale, each showing WHY it sits where it does (the score breakdown) plus all
qualifying detail. Deep Dive-styled (sister site to thedeepdive.ca)."""
import os
import json
import math
import pandas as pd
import site_theme as T
import enrich_facts as E
from config import score_breakdown, metal_bucket, METAL_ORDER


def _num(v, d=0.0):
    try:
        f = float(v)
        return f if f == f else d
    except (TypeError, ValueError):
        return d


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v)
    return "" if s in ("nan", "None") else s


def _load(csv, juris):
    if not os.path.exists(csv):
        return []
    d = pd.read_csv(csv)
    out = []
    for _, r in d.iterrows():
        status = _s(r.get("status"))
        dopen = bool(r.get("deposit_open"))
        grade = _s(r.get("grade_str"))
        tonnes = _s(r.get("tonnes_str"))
        drill = _s(r.get("drill_highlights"))
        spend = _num(r.get("exploration_spend"))
        conf = _num(r.get("grade_conf"), 1.0)
        lpy = r.get("last_prod_year")
        lpy = None if (lpy is None or str(lpy).strip() in ("", "nan", "None")) else lpy
        pmetal = _s(r.get("primary_metal"))
        bd = score_breakdown(status, dopen, grade, tonnes, bool(drill), spend, conf, lpy, pmetal)
        capsule = _s(r.get("capsule"))
        grade, top_metal = E.sort_grade_by_value(grade)   # highest-$ metal first
        _vt, vparts = E.value_parts(grade)
        drill_top = E.top_intercepts(drill, 3)
        production = _s(r.get("production")) or E.production_summary(capsule, drill, status)
        # dominant metal = the metal contributing the most $/t (else first commodity)
        dom_raw = vparts[0][0] if vparts else (top_metal or _s(r.get("primary_metal")))
        dmetal = metal_bucket(dom_raw) if dom_raw else "Other metallic"
        out.append({
            "value_parts": vparts, "drill_top": drill_top, "production": production,
            "dmetal": dmetal, "last_prod_year": (int(lpy) if lpy not in (None,) and str(lpy).strip().isdigit() else ""),
            "juris": juris, "name": _s(r.get("name")) or "(unnamed)",
            "lead_id": _s(r.get("lead_id")),
            "minfile": _s(r.get("minfile")), "url": _s(r.get("minfile_url")),
            "metal": top_metal or _s(r.get("primary_metal")), "metals": _s(r.get("metals_abbr")),
            "commodity": _s(r.get("commodity")), "status": status, "deposit_open": dopen,
            "hard": _s(r.get("hard_to_stake")).lower() == "true", "grade": grade,
            "size": _s(r.get("deposit_size")) or (tonnes if tonnes else ""),
            "drill": drill[:300], "spend_str": _s(r.get("exploration_spend_str")),
            "operators": _s(r.get("operators")), "last_work": _s(r.get("last_work_year")),
            "community": _s(r.get("nearest_community")), "community_km": _s(r.get("community_km")),
            "encumbrances": _s(r.get("encumbrances")), "cells_ha": _s(r.get("cells_area_ha")),
            "n_cells": _s(r.get("n_cells")), "score": bd["total"], "parts": bd["parts"],
            "lat": _num(r.get("lat")), "lon": _num(r.get("lon")),
        })
    return out


# per-jurisdiction pill colours (fallback grey for any not listed)
PILL = {"BC": "background:#e8f0fe;color:#1a56db;", "ON": "background:#fdeaea;color:#c81e1e;",
        "YK": "background:#eef7ee;color:#2f7d32;", "NL": "background:#e6f4f6;color:#0e7490;",
        "SK": "background:#f3eefe;color:#6d28d9;", "MB": "background:#fef3e2;color:#b45309;",
        "QC": "background:#fce7f3;color:#be185d;", "AB": "background:#e0f2fe;color:#0369a1;",
        "NT": "background:#e9f7f3;color:#0f766e;", "NB": "background:#fdf0e6;color:#9a3412;",
        "NS": "background:#eef2ff;color:#3730a3;"}

# dominant-metal preset groups for the filter dropdown
GROUPS = {
    "prec": {"Gold", "Silver", "Platinum", "Palladium"},
    "base": {"Copper", "Lead", "Zinc", "Nickel", "Tin"},
    "crit": {"Lithium", "Cobalt", "Uranium", "Rare earths", "Vanadium", "Niobium", "Tantalum",
             "Beryllium", "Antimony", "Bismuth", "Molybdenum", "Tungsten", "Graphite",
             "Chromium", "Titanium", "Manganese"},
}


def build(site_dir, regions):
    leads = []
    juris = []      # (code, display name) in region order, only those with leads
    for r in regions:
        if not r.get("live"):
            continue
        code = r["slug"].upper()
        rows = _load(os.path.join(r["dir"], "out", "leads.csv"), code)
        if rows:
            leads += rows
            juris.append((code, r["name"]))
    leads.sort(key=lambda x: (-x["score"], not x["deposit_open"]))
    for i, l in enumerate(leads, 1):
        l["rank"] = i
    counts = {c: sum(1 for l in leads if l["juris"] == c) for c, _ in juris}

    pill_css = "".join(".p-{0}{{{1}}}".format(c.lower(), PILL.get(c, "background:#eef0f2;color:#444;"))
                       for c, _ in juris)
    names = ", ".join(nm for _, nm in juris[:-1]) + (" and " + juris[-1][1] if len(juris) > 1 else
                                                     (juris[0][1] if juris else ""))

    # jurisdiction dropdown options
    jopts = ['<option value="all">All jurisdictions ({0})</option>'.format(len(leads))]
    for c, nm in juris:
        jopts.append('<option value="{0}">{1} ({2})</option>'.format(c, nm, counts[c]))

    # dominant-metal dropdown: preset groups + individual metals (by $/t taxonomy order)
    mcounts = {}
    for l in leads:
        mcounts[l["dmetal"]] = mcounts.get(l["dmetal"], 0) + 1
    order = {m: i for i, m in enumerate(METAL_ORDER)}
    metals_sorted = sorted(mcounts, key=lambda m: (order.get(m, 99), -mcounts[m]))

    def _gc(group):
        return sum(mcounts.get(m, 0) for m in group)
    mopts = ['<option value="all">All metals ({0})</option>'.format(len(leads))]
    mopts.append('<optgroup label="Groups">')
    mopts.append('<option value="prec">Precious — Au, Ag, PGE ({0})</option>'.format(_gc(GROUPS["prec"])))
    mopts.append('<option value="base">Base — Cu, Pb, Zn, Ni, Sn ({0})</option>'.format(_gc(GROUPS["base"])))
    mopts.append('<option value="crit">Critical / specialty ({0})</option>'.format(_gc(GROUPS["crit"])))
    mopts.append('</optgroup><optgroup label="Single metal">')
    for m in metals_sorted:
        mopts.append('<option value="{0}">{0} ({1})</option>'.format(m, mcounts[m]))
    mopts.append('</optgroup>')

    html = PAGE.format(
        fonts=T.FONTS, css=T.THEME_CSS + "\n" + pill_css,
        header=T.header("index.html"), footer=T.footer(),
        leads_json=json.dumps(leads, separators=(",", ":")),
        jopts="".join(jopts), mopts="".join(mopts), region_names=names,
        groups_json=json.dumps({k: sorted(v) for k, v in GROUPS.items()}),
    )
    os.makedirs(site_dir, exist_ok=True)
    open(os.path.join(site_dir, "index.html"), "w").write(html)          # front page
    open(os.path.join(site_dir, "priorities.html"), "w").write(html)      # stable alias
    summ = ", ".join("{0} {1}".format(counts[c], c) for c, _ in juris)
    print(f"[priority] index.html (front page) — {len(leads)} leads ({summ})")


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Priority Leads · Project Closeology</title>
{fonts}
<style>{css}
.controls{{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:18px 0 8px;}}
.fsel{{display:flex;flex-direction:column;gap:5px;}}
.fsel.grow{{flex:1;min-width:220px;}}
.fsel label{{font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);font-weight:700;}}
.fsel input,.fsel select{{height:40px;padding:0 12px;border:1px solid var(--line);border-radius:8px;font-size:14px;background:#fff;color:var(--ink);}}
.fsel select{{font-weight:600;font-size:13.5px;cursor:pointer;min-width:190px;}}
.fsel input[type=text],.fsel input#q{{width:100%;}}
.fsel input[type=range]{{height:auto;min-width:150px;padding:0;border:0;}}
.count{{color:var(--mut);font-size:13px;}}
.lead{{border:1px solid var(--line);border-radius:12px;padding:0;margin:14px 0;overflow:hidden;background:#fff;}}
.lead:hover{{box-shadow:0 3px 14px rgba(0,0,0,.06);}}
.lhead{{display:flex;gap:16px;align-items:flex-start;padding:16px 18px;border-bottom:1px solid var(--line);}}
.rankbox{{flex:0 0 auto;text-align:center;min-width:56px;}}
.rankbox .r{{font-family:'Bitter',serif;font-weight:800;font-size:26px;line-height:1;}}
.rankbox .rl{{font-size:9.5px;letter-spacing:1px;color:var(--mut);text-transform:uppercase;}}
.scorebox{{flex:0 0 auto;text-align:center;min-width:62px;padding:6px 8px;border:1px solid var(--line);border-radius:10px;}}
.scorebox .s{{font-family:'Bitter',serif;font-weight:800;font-size:24px;line-height:1;color:var(--red);}}
.scorebox .sl{{font-size:9px;letter-spacing:.5px;color:var(--mut);text-transform:uppercase;}}
.lmain{{flex:1;min-width:0;}}
.lname{{font-family:'Bitter',serif;font-weight:700;font-size:18px;}}
.pill{{font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;margin-left:8px;vertical-align:middle;}}
.p-open{{background:#e7f6ec;color:#127a3a;}} .p-hard{{background:#fff4e5;color:#9a5b00;}}
.sub{{color:var(--mut);font-size:13px;margin-top:3px;}}
.chips{{margin-top:7px;}} .chip{{display:inline-block;background:var(--chip);color:#2d3748;font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px;margin:2px 5px 2px 0;}}
.lbody{{display:grid;grid-template-columns:1.05fr 1fr;gap:0;}}
@media (max-width:760px){{.lbody{{grid-template-columns:1fr;}}}}
.why,.facts{{padding:14px 18px;}} .why{{border-right:1px solid var(--line);background:#fcfcfd;}}
@media (max-width:760px){{.why{{border-right:0;border-bottom:1px solid var(--line);}}}}
.sechd{{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--mut);font-weight:700;margin-bottom:8px;}}
.prow{{display:flex;gap:10px;align-items:baseline;margin:5px 0;font-size:13px;}}
.pbadge{{flex:0 0 auto;font-family:'Bitter',serif;font-weight:800;color:var(--red);min-width:34px;}}
.prow .pl{{font-weight:600;}} .prow .pn{{color:var(--mut);}}
.fact{{margin:6px 0;font-size:13px;}} .fact .k{{color:var(--mut);font-weight:600;display:inline-block;min-width:96px;}}
.drill{{font-size:12.5px;color:#374151;background:#fcfcfd;border-left:3px solid var(--red);padding:7px 10px;margin-top:8px;border-radius:0 6px 6px 0;}}
.maplink{{display:inline-block;background:var(--red);color:#fff;font-size:12px;font-weight:600;padding:5px 12px;border-radius:7px;}} .maplink:hover{{text-decoration:none;opacity:.92;}}
.intercept{{font-size:12.5px;padding:5px 10px;margin-top:5px;background:#f0f9f4;border-left:3px solid #127a3a;border-radius:0 6px 6px 0;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;}}
.intercept .vpt{{color:#127a3a;font-weight:700;white-space:nowrap;}}
.prodbox{{font-size:12.5px;color:#374151;background:#fbf6ef;border-left:3px solid #9a5b00;padding:7px 10px;margin-top:10px;border-radius:0 6px 6px 0;}}
.herolinks{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px;}}
.hbtn{{background:var(--red);color:#fff;font-size:13px;font-weight:600;padding:8px 14px;border-radius:8px;}} .hbtn:hover{{text-decoration:none;opacity:.92;}}
.hbtn.ghost{{background:#fff;border:1px solid var(--line);color:var(--ink);}}
.empty{{color:var(--mut);padding:30px;text-align:center;}}
</style></head><body>
{header}
<div class="wrap">
  <div class="hero">
    <h1>Priority leads</h1><div class="rule"></div>
    <p>Every {region_names} lead ranked on <b>one common scale</b> — driven by in-situ
       metal value, deposit size, development status, open ground, drilling on record and exploration
       spend. A score of 80 means the same thing in every jurisdiction. Each lead shows exactly why it
       sits where it does.</p>
  </div>
  <div class="controls">
    <div class="fsel grow"><label for="q">Search</label>
      <input id="q" placeholder="Name, metal, commodity, community…"/></div>
    <div class="fsel"><label for="jsel">Jurisdiction</label>
      <select id="jsel">{jopts}</select></div>
    <div class="fsel"><label for="msel">Dominant metal</label>
      <select id="msel">{mopts}</select></div>
    <div class="fsel"><label for="sc">Min score: <span id="sv">0</span></label>
      <input id="sc" type="range" min="0" max="90" value="0" step="5"></div>
  </div>
  <div class="count" id="count"></div>
  <div id="list"></div>
</div>
{footer}
<script>
const LEADS={leads_json};
const GROUPS={groups_json};
const esc=s=>(s==null?'':String(s)).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
let jf='all', mf='all', q='', mins=0;
function metalMatch(dm){{
  if(mf==='all') return true;
  if(GROUPS[mf]) return GROUPS[mf].includes(dm);
  return dm===mf;
}}
function card(p){{
  const parts=(p.parts||[]).map(x=>`<div class=prow><span class=pbadge>+${{x.pts}}</span><span><span class=pl>${{esc(x.label)}}.</span> <span class=pn>${{esc(x.note)}}</span></span></div>`).join('');
  const chips=[];
  if(p.metals) chips.push(`<span class=chip>${{esc(p.metals)}}</span>`);
  else if(p.metal) chips.push(`<span class=chip>${{esc(p.metal)}}</span>`);
  if(p.grade) chips.push(`<span class=chip>${{esc(p.grade)}}</span>`);
  const facts=[];
  facts.push(`<div class=fact><span class=k>Status</span>${{esc(p.status)||'—'}}</div>`);
  if(p.commodity) facts.push(`<div class=fact><span class=k>Commodities</span>${{esc(p.commodity)}}</div>`);
  if(p.grade) facts.push(`<div class=fact><span class=k>Grade</span>${{esc(p.grade)}}</div>`);
  if(p.value_parts&&p.value_parts.length) facts.push(`<div class=fact><span class=k>Value split</span>${{p.value_parts.map(x=>esc(x[0])+' <b>$'+x[1]+'</b>').join(' · ')}} <span class=pn>per tonne in-situ</span></div>`);
  if(p.size) facts.push(`<div class=fact><span class=k>Size</span>${{esc(p.size)}}</div>`);
  if(p.spend_str) facts.push(`<div class=fact><span class=k>Expl. spend</span>${{esc(p.spend_str)}}${{p.last_work?(' · last '+esc(p.last_work)):''}}${{p.operators?(' · '+esc(p.operators)):''}}</div>`);
  if(p.community) facts.push(`<div class=fact><span class=k>Nearest town</span>${{esc(p.community)}}${{p.community_km!==''?(' · '+esc(p.community_km)+' km'):''}}</div>`);
  if(p.cells_ha) facts.push(`<div class=fact><span class=k>Open ground</span>${{esc(p.n_cells)}} cell(s) · ${{esc(p.cells_ha)}} ha adjacent</div>`);
  if(p.encumbrances && !p.hard) facts.push(`<div class=fact><span class=k>Nearby</span>${{esc(p.encumbrances)}}</div>`);
  if(p.url) facts.push(`<div class=fact><span class=k>Record</span><a href="${{esc(p.url)}}" target=_blank>${{esc(p.minfile)||'official record'}} ↗</a></div>`);
  const mapurl=`app.html?lat=${{p.lat}}&lon=${{p.lon}}&z=12&region=${{p.juris.toLowerCase()}}&kind=lead&label=${{encodeURIComponent(p.name||'')}}`;
  return `<div class=lead>
    <div class=lhead>
      <div class=rankbox><div class=r>${{p.rank}}</div><div class=rl>rank</div></div>
      <div class=scorebox><div class=s>${{p.score}}</div><div class=sl>score</div></div>
      <div class=lmain>
        <div><span class=lname>${{esc(p.name)}}</span>
          <span class="pill p-${{p.juris.toLowerCase()}}">${{p.juris}}</span>
          ${{p.deposit_open?'<span class="pill p-open">deposit open</span>':''}}
          ${{p.hard?'<span class="pill p-hard">harder to stake</span>':''}}</div>
        <div class=sub>${{esc(p.metal)}}${{p.minfile?(' · '+esc(p.minfile)):''}}</div>
        <div class=chips>${{chips.join('')}}</div>
        <div style="margin-top:8px"><a class=maplink href="${{mapurl}}">📍 View on the map</a></div>
      </div>
    </div>
    <div class=lbody>
      <div class=why><div class=sechd>Why it ranks here</div>${{parts}}</div>
      <div class=facts><div class=sechd>Qualifying details</div>${{facts.join('')}}
        ${{p.production?`<div class=prodbox><b>Past production.</b> ${{esc(p.production)}}</div>`:''}}
        ${{(p.drill_top&&p.drill_top.length)?(`<div class=sechd style="margin-top:12px">⛏ Top drill results</div>`+p.drill_top.map(x=>`<div class=intercept><b>${{esc(x.text)}}</b> <span class=vpt>≈ $${{x.vpt}}/t</span></div>`).join('')):(p.drill?`<div class=drill><b>Drill / assay.</b> ${{esc(p.drill)}}${{p.drill.length>=300?'…':''}}</div>`:'')}}
        ${{p.hard?`<div class=drill style="border-left-color:#9a5b00"><b>Staking note:</b> ${{esc(p.encumbrances)||'A conditional / registration reserve applies here — a special staking process is required.'}}</div>`:''}}
      </div>
    </div>
  </div>`;
}}
function render(){{
  const ql=q.toLowerCase();
  const rows=LEADS.filter(p=>(jf==='all'||p.juris===jf) && metalMatch(p.dmetal) && p.score>=mins &&
    (!ql || (p.name+' '+p.metal+' '+p.commodity+' '+p.community+' '+p.metals).toLowerCase().includes(ql)));
  document.getElementById('count').textContent=rows.length+' lead'+(rows.length===1?'':'s')+' shown, ranked by priority';
  document.getElementById('list').innerHTML=rows.length?rows.map(card).join(''):'<div class=empty>No leads match — widen the metal or jurisdiction filter.</div>';
}}
document.getElementById('jsel').addEventListener('change',e=>{{jf=e.target.value;render();}});
document.getElementById('msel').addEventListener('change',e=>{{mf=e.target.value;render();}});
document.getElementById('sc').addEventListener('input',e=>{{mins=+e.target.value;document.getElementById('sv').textContent=mins;render();}});
document.getElementById('q').addEventListener('input',e=>{{q=e.target.value;render();}});
render();
</script></body></html>
"""
