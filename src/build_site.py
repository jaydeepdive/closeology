"""Deep Dive-styled landing page + CSV/XLSX downloads for Project Closeology."""
import os
import json
import shutil
import site_theme as T


def _xlsx(csv, out, region):
    try:
        import pandas as pd
        from openpyxl.utils import get_column_letter
        cols = ["rank", "name", "minfile", "nearest_community", "community_km", "primary_metal",
                "commodity", "status", "deposit_open", "hard_to_stake", "deposit_size", "grade_str",
                "drill_highlights", "exploration_spend_str", "n_reports", "last_work_year", "operators",
                "encumbrances", "n_cells", "cells_area_ha", "score", "lat", "lon", "minfile_url"]
        d = pd.read_csv(csv)
        c = [x for x in cols if x in d.columns]
        dd = d[c].copy(); dd.columns = [x.replace("_", " ").title() for x in c]
        with pd.ExcelWriter(out, engine="openpyxl") as w:
            dd.to_excel(w, index=False, sheet_name=region[:31]); ws = w.sheets[region[:31]]; ws.freeze_panes = "A2"
            wid = {"Name": 24, "Nearest Community": 18, "Commodity": 22, "Deposit Size": 26, "Grade Str": 20,
                   "Drill Highlights": 60, "Operators": 34, "Encumbrances": 28, "Minfile Url": 40}
            for i, cc in enumerate(dd.columns, 1):
                ws.column_dimensions[get_column_letter(i)].width = wid.get(cc, 12)
    except Exception as e:
        print("  [xlsx] skipped:", str(e)[:60])


def build(site_dir, regions):
    cards = []
    tot_leads = 0
    for r in regions:
        sp = os.path.join(r["dir"], "out", "stats.json")
        if r.get("live") and os.path.exists(sp):
            s = json.load(open(sp))
            tot_leads += int(s.get("n_leads", 0))
            cards.append(f"""
      <div class="card">
        <div class="card-h"><h3>{s['region']}</h3><span class="pill on">live</span></div>
        <div class="kpis">
          <div><b>{s['n_leads']:,}</b><span>leads</span></div>
          <div><b>{s['n_deposit_open']:,}</b><span>deposit open</span></div>
          <div><b>{s.get('n_with_drill_highlights',0):,}</b><span>drill data</span></div>
          <div><b>{s.get('n_claims_active',0):,}</b><span>active claims</span></div>
        </div>
        <div class="links">
          <a class="btn" href="{r['slug']}.html">Explore map →</a>
          <a class="btn ghost" href="daily_{r['slug']}.html">Daily radar</a>
          <a class="btn ghost" href="{r['slug']}_leads.csv">CSV</a>
          <a class="btn ghost" href="{r['slug']}_leads.xlsx">XLSX</a>
        </div>
        <div class="upd">Updated {s.get('generated','')}</div>
      </div>""")
        else:
            cards.append(f"""
      <div class="card"><div class="card-h"><h3>{r['name']}</h3><span class="pill">soon</span></div>
        <p class="soon">{r.get('note','Pipeline in progress.')}</p></div>""")
    # NB: index.html is the priority list (build_priority). build_site only ships
    # the per-region CSV/XLSX downloads consumed by the priority page's hero links.
    os.makedirs(site_dir, exist_ok=True)
    for r in regions:
        csv = os.path.join(r["dir"], "out", "leads.csv")
        if r.get("live") and os.path.exists(csv):
            shutil.copy(csv, os.path.join(site_dir, f"{r['slug']}_leads.csv"))
            _xlsx(csv, os.path.join(site_dir, f"{r['slug']}_leads.xlsx"), r["name"])
    open(os.path.join(site_dir, ".nojekyll"), "w").write("")
    print(f"[site] region CSV/XLSX downloads + .nojekyll")


INDEX = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Project Closeology · mineral opportunity radar</title>
{fonts}
<style>{css}
.big{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:26px;margin:22px 0;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap;align-items:center;}}
.big .lead{{max-width:640px;}} .big h2{{font-size:22px;margin-bottom:6px;}}
.big .cta{{background:var(--red);color:#fff;font-weight:700;padding:12px 20px;border-radius:9px;font-size:15px;white-space:nowrap;}}
.big .cta:hover{{text-decoration:none;opacity:.92;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;margin-top:8px;}}
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px;}}
.card-h{{display:flex;align-items:center;justify-content:space-between;}} .card-h h3{{font-size:18px;}}
.pill{{font-size:10px;text-transform:uppercase;letter-spacing:.5px;padding:3px 9px;border-radius:20px;background:#eee;color:var(--mut);}}
.pill.on{{background:#e7f6ec;color:#127a3a;}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:16px 0;}}
.kpis b{{display:block;font-size:19px;font-family:'Bitter',serif;}} .kpis span{{color:var(--mut);font-size:10px;text-transform:uppercase;}}
.links{{display:flex;gap:8px;flex-wrap:wrap;}}
.btn{{background:var(--red);color:#fff;padding:8px 13px;border-radius:8px;font-size:13px;font-weight:600;}}
.btn:hover{{text-decoration:none;opacity:.92;}} .btn.ghost{{background:#fff;border:1px solid var(--line);color:var(--ink);}}
.upd{{color:var(--mut);font-size:11px;margin-top:12px;}} .soon{{color:var(--mut);font-size:13px;}}
.how{{margin-top:30px;color:var(--mut);font-size:14px;line-height:1.75;}} .how b{{color:var(--ink);}}
</style></head><body>
{header}
<div class="wrap">
  <div class="hero">
    <h1>Mineral opportunity radar</h1><div class="rule"></div>
    <p>Past-producing and drilled-out deposits sitting on — or right beside — <b>open, stakeable ground</b>
       across British Columbia and Ontario. Screened against live mineral titles, leases, reserves and parks;
       valued on real in-situ metal worth and refreshed by a daily scan.</p>
  </div>
  <a class="big" href="priorities.html">
    <div class="lead"><h2>Priority leads — one ranked list</h2>
      <div style="color:var(--mut)">Every BC and Ontario lead on a single common scale ({tot} in all), each showing
      exactly why it ranks where it does and all its qualifying detail.</div></div>
    <span class="cta">Open the priority list →</span>
  </a>
  <div class="grid">{cards}</div>
  <div class="how">
    <b>How to read it.</b> A <b>lead</b> is a mineral occurrence with historical merit whose own ground is
    unclaimed (“deposit open”) or which has open cells within a few hundred metres. The <b>priority list</b>
    ranks everything on one scale so a BC score and an Ontario score mean the same thing. The <b>daily radar</b>
    flags fresh drilling on the boundary of open ground and claims that just lapsed. Always confirm in the
    official title system before acting — this is screening, not staking advice.
  </div>
</div>
{footer}
</body></html>
"""
