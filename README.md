# Project Closeology

Mineral **opportunity radar**: past-producing / drilled-out deposits that sit on — or right beside —
open, stakeable ground. Screened against live mineral titles, reserves and parks; enriched with grade,
deposit size, drill/assay highlights and the nearest community; refreshed daily with a scan for claims
lapsing or newly staked near each lead.

**Live site:** published via GitHub Pages (see repo *Settings → Pages*).

## What the site has
- `index.html` — landing page + region cards.
- `bc.html` — one unified British Columbia map: live-queryable claims (WMS GetFeatureInfo),
  all MINFILE occurrences (clustered), and fully-detailed leads + a filterable/sortable tracker.
- `daily.html` — daily radar: leads highlighted where nearby ground is lapsing or freshly staked,
  plus drill news; shows *where* everything sits.
- `bc_leads.csv` — the full leads table.

## How it updates
`.github/workflows/pages.yml` runs daily (and on push): it fetches live BC data, re-runs the pipeline,
rebuilds the maps and landing page, and deploys to Pages. No server needed.

## Pipeline (`src/`)
- `ingest.py` — pull occurrences (MINFILE), claims, reserves, parks, communities (BC WFS).
- `build_minfile_facts.py` — grade / tonnage / resource stage / drill highlights from the MINFILE
  Access DB (run occasionally; output committed to `data/keep/` so the daily job stays fast).
- `pipeline.py` — screen a synthetic staking grid vs claims/reserves/parks, find leads, score, enrich.
- `build_map.py`, `daily.py`, `build_site.py` — render the site.
- `build_all.py` — orchestrates the daily rebuild.

## Data sources
BC Mineral Titles Online (MTO) & MINFILE, under the Open Government Licence – British Columbia.
Ontario (LIO claims + Mineral Deposit Inventory) is being added next.

*Screening tool only — always confirm in Mineral Titles Online before staking.*
