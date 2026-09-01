"""Build the site landing page (index.html) from region stats."""
import os
import json
import shutil


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
    for r in regions:
        sp = os.path.join(r["dir"], "out", "stats.json")
        if r.get("live") and os.path.exists(sp):
            s = json.load(open(sp))
            cards.append(f"""
      <div class="card live">
        <div class="card-h"><h2>{s['region']}</h2><span class="pill on">live</span></div>
        <div class="kpis">
          <div><b>{s['n_leads']:,}</b><span>leads</span></div>
          <div><b>{s['n_deposit_open']:,}</b><span>deposit open</span></div>
          <div><b>{s['n_with_drill_highlights']:,}</b><span>drill data</span></div>
          <div><b>{s['n_claims_active']:,}</b><span>active claims</span></div>
        </div>
        <div class="links">
          <a class="btn" href="{r['slug']}.html">Explore map →</a>
          <a class="btn ghost" href="daily_{r['slug']}.html">Daily radar</a>
          <a class="btn ghost" href="{r['slug']}_leads.csv">Leads CSV</a>
        </div>
        <div class="upd">Updated {s.get('generated','')}</div>
      </div>""")
        else:
            cards.append(f"""
      <div class="card">
        <div class="card-h"><h2>{r['name']}</h2><span class="pill">coming soon</span></div>
        <p class="soon">{r.get('note','Pipeline in progress.')}</p>
      </div>""")
    html = INDEX.replace("{CARDS}", "\n".join(cards))
    os.makedirs(site_dir, exist_ok=True)
    open(os.path.join(site_dir, "index.html"), "w").write(html)
    # ship CSV downloads
    for r in regions:
        csv = os.path.join(r["dir"], "out", "leads.csv")
        if r.get("live") and os.path.exists(csv):
            shutil.copy(csv, os.path.join(site_dir, f"{r['slug']}_leads.csv"))
            _xlsx(csv, os.path.join(site_dir, f"{r['slug']}_leads.xlsx"), r["name"])
    open(os.path.join(site_dir, ".nojekyll"), "w").write("")
    print(f"[site] index.html + {sum(1 for r in regions if r.get('live'))} region CSV(s)")


INDEX = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Project Closeology</title>
<style>
  :root{ --bg:#0b1220; --panel:#111c33; --line:#243352; --ink:#e5edf7; --mut:#94a3b8; --accent:#c026d3; }
  *{box-sizing:border-box;} body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink);}
  .wrap{max-width:1000px;margin:0 auto;padding:48px 22px 60px;}
  .hero h1{font-size:34px;margin:0 0 6px;} .hero h1 span{color:var(--accent);}
  .hero p{color:var(--mut);font-size:15px;max-width:680px;line-height:1.6;}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;margin-top:34px;}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;}
  .card.live{border-color:#3b2a55;}
  .card-h{display:flex;align-items:center;justify-content:space-between;} .card-h h2{margin:0;font-size:19px;}
  .pill{font-size:10px;text-transform:uppercase;letter-spacing:.5px;padding:3px 9px;border-radius:20px;background:#1e293b;color:var(--mut);}
  .pill.on{background:#14532d;color:#86efac;}
  .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:16px 0;}
  .kpis b{display:block;font-size:19px;} .kpis span{color:var(--mut);font-size:10px;text-transform:uppercase;}
  .links{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;}
  .btn{background:var(--accent);color:#fff;text-decoration:none;padding:8px 13px;border-radius:8px;font-size:13px;font-weight:600;}
  .btn.ghost{background:transparent;border:1px solid var(--line);color:var(--ink);font-weight:500;}
  .upd{color:var(--mut);font-size:11px;margin-top:12px;} .soon{color:var(--mut);font-size:13px;line-height:1.5;}
  .how{margin-top:44px;color:var(--mut);font-size:13.5px;line-height:1.7;border-top:1px solid var(--line);padding-top:22px;}
  .how b{color:var(--ink);} footer{margin-top:30px;color:#5b6b82;font-size:11px;line-height:1.5;}
</style></head><body><div class="wrap">
  <div class="hero">
    <h1>Project Closeology <span>·</span> mineral opportunity radar</h1>
    <p>Past-producing and drilled-out deposits that sit on — or right beside — <b>open, stakeable ground</b>.
       Screened against live mineral titles, reserves and parks; enriched with grade, deposit size, drill/assay
       highlights and the nearest community; refreshed by a daily scan for claims lapsing or newly staked near each lead.</p>
  </div>
  <div class="grid">{CARDS}</div>
  <div class="how">
    <b>How to read it.</b> Each <b>lead</b> is a mineral occurrence with historical merit whose own cell is unclaimed
    (“deposit open”) or which has open cells within a few hundred metres. The map shows every claim (click to query the
    live title), all occurrences, and the leads with full detail. The <b>daily radar</b> highlights leads where nearby
    ground is about to lapse or where someone just staked. Always confirm in Mineral Titles Online before acting — this is a
    screening tool, not staking advice.
  </div>
  <footer>Sources: BC Mineral Titles Online (MTO) &amp; MINFILE under the Open Government Licence – British Columbia;
    Ontario LIO &amp; Mineral Deposit Inventory (in progress). Generated by an automated pipeline.</footer>
</div></body></html>
"""


if __name__ == "__main__":
    REGIONS = [
        {"name": "British Columbia", "dir": "data/bc", "slug": "bc", "live": True},
        {"name": "Ontario", "dir": "data/on", "slug": "on", "live": False,
         "note": "LIO claims + Mineral Deposit Inventory — pipeline being wired up next."},
    ]
    build("site", REGIONS)
