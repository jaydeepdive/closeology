"""Safe, resumable re-extraction of banked SEDAR 43-101 PDFs under the current
extractor (v8: robust AISC/cash-cost + memory-safe large-PDF text).

WHY A DEDICATED DRIVER (not `python -m minemodelingpro.sedar`):
  sedar.ingest_folder() ends with shards.export_shards(), which RMTREEs and
  rewrites the WHOLE shard store from the working sqlite. Run against anything
  but the full store that would delete the 800k gov collars / 14M gov assays.
  This driver instead:
    * extracts TEXT-derived tables only (deposit_model / model_method /
      metallurgy / economics) with drill_tables=False — AISC/cash cost live in
      text, so camelot (slow, OOM-prone in a 4 GB box) is unnecessary here, and
      the existing appendix collar/assay shards are left untouched;
    * writes into a SEPARATE temp sqlite (resumable across short runs);
    * merges ONLY the re-extracted sources' text shards into the manifest,
      touching nothing else;
    * upserts those sources into the report index (never rebuilding it).

Source-id parity: each disk PDF maps to the SAME `sedar:<stable-key>` id the
first extraction used (issuer slug + submitted datetime), so shards overwrite
in place rather than duplicating.

Phases:
  python scripts/reextract_econ.py ingest [--limit N] [--max-seconds S]   # resumable
  python scripts/reextract_econ.py shard                                  # merge shards + index
  python scripts/reextract_econ.py status                                 # progress
"""
import os
import re
import sys
import glob
import json
import time
import sqlite3
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from minemodelingpro import pdf_reports, shards, store           # noqa: E402
from minemodelingpro import sedar_collect as sc                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = os.path.join(ROOT, "data", "keep")
PDFDIR = os.path.join(KEEP, "sedar_pdfs")
LEDGER = os.path.join(KEEP, "sedar_manifest.json")
INDEX = os.path.join(KEEP, "mmp_reports_index.json")
TEMPDB = os.path.join(KEEP, "mmp_reextract.sqlite")
TEXT_TABLES = ["deposit_model", "model_method", "metallurgy", "economics"]


def _pdf_sources():
    """Yield (pdf_path, source_id, meta) for every banked PDF, assigning the same
    stable-key source id the first extraction used."""
    man = json.load(open(LEDGER)) if os.path.exists(LEDGER) else []
    by_node = {r.get("node"): r for r in man if r.get("node")}
    for p in sorted(glob.glob(os.path.join(PDFDIR, "*.pdf"))):
        stem = os.path.basename(p)[:-4]
        ref = stem[len("sedar_"):] if stem.startswith("sedar_") else stem
        r = by_node.get(ref) if re.fullmatch(r"W\d+", ref) else None
        if not r:                       # stable-key-named file: match a row by key
            r = next((rr for rr in man
                      if sc._stable_key(rr.get("company"), rr.get("submitted")) == ref), None)
            sid = "sedar:" + ref
        else:
            sk = sc._stable_key(r.get("company"), r.get("submitted"))
            sid = "sedar:" + (sk or ref)
        company = (r or {}).get("company") or ref.rsplit("_", 1)[0].replace("-", " ").title()
        node = (r or {}).get("node") or ref
        meta = {"company": company, "project": (r or {}).get("project") or company,
                "commodity": (r or {}).get("commodity"),
                "jurisdiction": (r or {}).get("jurisdiction"),
                "submitted": (r or {}).get("submitted"),
                "url": (r or {}).get("sedar_url") or f"sedarplus.ca/filing/{node}"}
        yield p, sid, meta


def _done(con, sid):
    r = con.execute("SELECT note FROM sources WHERE id=?", (sid,)).fetchone()
    return bool(r) and f"ev{pdf_reports.EXTRACTOR_VERSION}" in (r[0] or "")


def _redirect_store():
    """ingest_report calls store.connect() with no arg, whose default is BOUND at
    def-time to the real mmp.sqlite — so setting store.DB_PATH alone doesn't
    redirect it. Rebind the default too, so all writes land in the temp db."""
    store.DB_PATH = TEMPDB
    store.connect.__defaults__ = (TEMPDB,)


def ingest(limit=None, max_seconds=150):
    _redirect_store()                          # redirect the whole store to the temp db
    con = store.connect(TEMPDB)
    todo = [(p, sid, m) for p, sid, m in _pdf_sources() if not _done(con, sid)]
    con.close()
    print(f"[reextract] {len(todo)} PDF(s) to (re)extract under ev{pdf_reports.EXTRACTOR_VERSION}")
    t0 = time.time(); done = ok = 0
    for p, sid, m in todo:
        if limit and done >= limit:
            break
        if max_seconds and time.time() - t0 > max_seconds:
            print("[reextract] time budget reached — resume with another `ingest` run"); break
        done += 1
        mb = os.path.getsize(p) // (1024 * 1024)
        print(f"[reextract] ({done}) {sid}  [{mb} MB]")
        try:
            pdf_reports.ingest_report(m["url"], project=m["project"], commodity=m["commodity"],
                                      jurisdiction=m["jurisdiction"], report_date=m["submitted"],
                                      source_id=sid, pdf_path=p, drill_tables=False)
            ok += 1
        except Exception as e:
            print(f"[reextract] FAILED {sid}: {str(e)[:160]}")
    print(f"[reextract] ingested {ok}/{done} this run")
    return ok


def shard():
    """Merge ONLY the re-extracted sources' TEXT shards into the manifest; leave
    every other shard (gov collars/assays, appendix drill tables, untouched
    reports) exactly as-is."""
    import pandas as pd
    if not os.path.exists(TEMPDB):
        print("[reextract] no temp db — run `ingest` first"); return
    meta = json.load(open(shards.MANIFEST))
    tables = meta["tables"]
    con = sqlite3.connect(TEMPDB)
    srcs = [r[0] for r in con.execute("SELECT id FROM sources").fetchall()]
    touched = 0
    for sid in srcs:
        safe = shards._safe(sid)
        for t in TEXT_TABLES:
            tdir = os.path.join(shards.SHARD_DIR, t)
            for f in (glob.glob(os.path.join(tdir, safe + ".parquet"))
                      + glob.glob(os.path.join(tdir, safe + ".[0-9]*.parquet"))):
                os.remove(f)
            tables[t] = [x for x in tables.get(t, []) if x["source"] != sid]
            df = pd.read_sql_query(f"SELECT * FROM {t} WHERE source_id=?", con, params=[sid])
            if not df.empty:
                shards._write_shards(t, sid, df, tables)
        touched += 1
    con.close()
    meta["tables"] = tables
    meta["generated"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    meta["totals"] = {t: sum(x["rows"] for x in tables.get(t, [])) for t in shards.TABLES}
    meta["shard_count"] = sum(len(v) for v in tables.values())
    json.dump(meta, open(shards.MANIFEST, "w"), indent=2)
    print(f"[reextract] merged text shards for {touched} sources; totals now {meta['totals']}")
    _reindex(srcs)


def _reindex(srcs):
    """Upsert the re-extracted sources into the report index (never rebuild)."""
    con = sqlite3.connect(TEMPDB); con.row_factory = sqlite3.Row
    met = {r["source_id"]: r for r in con.execute(
        "SELECT source_id, process_types, refractory, recovery_summary FROM metallurgy")}
    prev = {}
    if os.path.exists(INDEX):
        try:
            prev = {e["id"]: e for e in json.load(open(INDEX)).get("reports", [])}
        except Exception:
            prev = {}
    for r in con.execute("""SELECT id, name, url, jurisdiction, pulled_at, n_collars, n_assays, note
                            FROM sources""").fetchall():
        note = r["note"] or ""
        m = re.search(r"(\d+)\s+resource rows", note)
        am = re.search(r"archive=(\S+)", note)
        mm = met.get(r["id"])
        prev[r["id"]] = {
            "id": r["id"], "company": r["name"],
            "project": (prev.get(r["id"]) or {}).get("project"),
            "commodity": (prev.get(r["id"]) or {}).get("commodity"),
            "jurisdiction": r["jurisdiction"] or (prev.get(r["id"]) or {}).get("jurisdiction"),
            "source_url": r["url"],
            "archive_url": (am.group(1) if am else None) or (prev.get(r["id"]) or {}).get("archive_url"),
            "collected": r["pulled_at"],
            "collars": r["n_collars"], "assays": r["n_assays"],
            "resource_rows": int(m.group(1)) if m else 0,
            "has_method": "method=y" in note,
            "has_economics": "econ=y" in note,
            "metallurgy_process": mm["process_types"] if mm else (prev.get(r["id"]) or {}).get("metallurgy_process"),
            "refractory": mm["refractory"] if mm else (prev.get(r["id"]) or {}).get("refractory"),
            "recovery": ((mm["recovery_summary"] if mm else "") or "")[:160] or (prev.get(r["id"]) or {}).get("recovery")}
    con.close()
    out = sorted(prev.values(), key=lambda e: (e.get("jurisdiction") or "zz", e.get("company") or ""))
    archived = sum(1 for e in out if e.get("archive_url"))
    json.dump({"generated": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
               "count": len(out), "archived": archived, "reports": out},
              open(INDEX, "w"), indent=1)
    print(f"[reextract] report index -> {len(out)} reports ({archived} archived)")


def status():
    n_pdf = len(list(_pdf_sources()))
    if not os.path.exists(TEMPDB):
        print(f"[reextract] {n_pdf} PDFs, temp db not started"); return
    con = store.connect(TEMPDB)
    dn = sum(1 for _, sid, _ in _pdf_sources() if _done(con, sid))
    ne = con.execute("SELECT COUNT(*) FROM economics").fetchone()[0]
    naisc = con.execute("SELECT COUNT(*) FROM economics WHERE aisc IS NOT NULL").fetchone()[0]
    ncash = con.execute("SELECT COUNT(*) FROM economics WHERE cash_cost IS NOT NULL").fetchone()[0]
    con.close()
    print(f"[reextract] {dn}/{n_pdf} PDFs done | economics rows={ne} aisc={naisc} cash_cost={ncash}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "ingest":
        lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
        secs = int(sys.argv[sys.argv.index("--max-seconds") + 1]) if "--max-seconds" in sys.argv else 150
        ingest(limit=lim, max_seconds=secs)
    elif cmd == "shard":
        shard()
    else:
        status()
