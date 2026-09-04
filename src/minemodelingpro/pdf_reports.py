"""NI 43-101 technical-report extractor for MineModelingPro.

A 43-101 carries what deposit modelling needs and government DBs lack: full
downhole assays, the resource/reserve estimate, and — crucially for MMP learning
to model — the METHODOLOGY (how the deposit was actually estimated: method,
block size, capping, density, cut-off, search, software). This module pulls all
of it from the PDF into the sharded MMP store under a per-report source id.

Reports use borderless tables, so extraction is text/line based (pdfplumber's
line-table detection misses them). Three outputs:
  * deposit_model  — resource/reserve rows (category, tonnes, grade, cut-off)
  * model_method   — estimation methodology + the Section-14 narrative (training)
  * assays/collars — from drilling appendices (staged; see extract_intervals)

Run:  python -m minemodelingpro.pdf_reports <pdf_url_or_path> [project] [commodity] [jurisdiction]
"""
import os
import re
import sys
import hashlib
import datetime
import urllib.request

from minemodelingpro import store

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"}
_NUM = re.compile(r"\d[\d,]*\.?\d*")
_CAT = re.compile(r"\b(measured\s*(?:\+|and|&)\s*indicated|measured|indicated|inferred|"
                  r"proven\s*(?:\+|and|&)\s*probable|proven|probable|total\s+mineral\s+resource|"
                  r"total\s+resource|total\s+reserve)\b", re.I)

# methodology signals
_METHOD = re.compile(r"\b(ordinary kriging|simple kriging|multiple indicator kriging|"
                     r"indicator kriging|inverse distance (?:squared|cubed|weighting|to the \w+ power)|"
                     r"nearest neighbou?r|ID2|ID3|ID\^?2|MIK|kriging)\b", re.I)
_SOFTWARE = re.compile(r"\b(Leapfrog|Seequent|Vulcan|Datamine|GEMS|GEOVIA|Surpac|Micromine|"
                       r"Isatis|MineSight|Hexagon|Deswik|Snowden Supervisor|Supervisor)\b", re.I)
_BLOCK = re.compile(r"(?:block (?:model )?(?:size|dimensions?)[^.]{0,60}?|parent block[^.]{0,40}?)"
                    r"(\d+(?:\.\d+)?\s*m?\s*(?:x|×|by)\s*\d+(?:\.\d+)?\s*m?\s*(?:x|×|by)\s*\d+(?:\.\d+)?\s*m?)", re.I)
_BLOCK2 = re.compile(r"(\d+(?:\.\d+)?\s*m\s*(?:x|×|by)\s*\d+(?:\.\d+)?\s*m\s*(?:x|×|by)\s*\d+(?:\.\d+)?\s*m)", re.I)
_DENSITY = re.compile(r"(?:bulk )?(?:density|specific gravity|SG)[^.]{0,50}?(\d\.\d{1,3})\s*(?:t/m3|t/m³|tonnes?/m3|g/cm3|g/cc)?", re.I)
_CAP = re.compile(r"(?:capp(?:ed|ing)|top[- ]?cut|grade cut)[^.]{0,110}", re.I)
_CUTOFF = re.compile(r"cut[- ]?off[^.]{0,90}", re.I)
_COMPOSITE = re.compile(r"composit(?:e|ed|ing)[^.]{0,80}", re.I)
_SEARCH = re.compile(r"search (?:ellipse|radius|distance|neighbou?rhood)[^.]{0,120}", re.I)
_CLASS = re.compile(r"(?:classif(?:ied|ication))[^.]{0,140}", re.I)
_METH_PAGE = re.compile(r"kriging|inverse distance|block model|specific gravity|bulk density|"
                        r"search ellipse|composit|capp|cut-?off|variogram|estimation domain|wireframe", re.I)


def _rid(url):
    return "ni43101:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def fetch_pdf(url, cache_dir="/tmp/mmp_reports"):
    if os.path.exists(url):
        return url
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers=_UA)
        data = urllib.request.urlopen(req, timeout=180).read()
        open(path, "wb").write(data)
    return path


def _first(rx, text):
    m = rx.search(text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip()[:200] if m else None


def extract_resources(pages_text):
    """Resource/reserve rows from category-bearing numeric lines. Column order
    varies per report, so we keep the full line verbatim (note) and pull the
    category + tonnage; downstream parsing can refine per-report."""
    rows, seen = [], set()
    for i, t in enumerate(pages_text):
        for ln in (t or "").split("\n"):
            low = ln.lower()
            if not _CAT.search(ln):
                continue
            nums = _NUM.findall(ln)
            if len(nums) < 3:
                continue
            if any(w in low for w in ("figure", "table of", "see section", "...", "…")):
                continue
            cat = re.sub(r"\s+", " ", _CAT.search(ln).group(0)).strip().title()
            key = (cat, ln.strip()[:60])
            if key in seen:
                continue
            seen.add(key)
            def _f(x):
                try:
                    return float(x.replace(",", ""))
                except ValueError:
                    return None
            tonnes = _f(nums[0])
            rows.append({"category": cat, "tonnes": tonnes, "note": re.sub(r"\s+", " ", ln).strip()[:300],
                         "page": i + 1})
    return rows


def extract_methodology(pages_text):
    """Gather the resource-estimation methodology: the narrative pages + the
    high-signal parameters (method, software, block size, density, capping,
    cut-off, compositing, search, classification)."""
    meth_pages = [t for t in pages_text if t and len(_METH_PAGE.findall(t)) >= 3]
    blob = "\n".join(meth_pages)
    if not blob:
        return None
    block = _first(_BLOCK, blob) or _first(_BLOCK2, blob)
    return {
        "estimation_method": _first(_METHOD, blob),
        "software": _first(_SOFTWARE, blob),
        "block_size": block,
        "density": _first(_DENSITY, blob),
        "capping": _first(_CAP, blob),
        "cutoff": _first(_CUTOFF, blob),
        "compositing": _first(_COMPOSITE, blob),
        "search_params": _first(_SEARCH, blob),
        "classification": _first(_CLASS, blob),
        # keep a bounded narrative excerpt for training / RAG
        "method_text": re.sub(r"\s+", " ", blob)[:12000],
        "n_method_pages": len(meth_pages),
    }


def ingest_report(url, project=None, commodity=None, jurisdiction=None, report_date=None):
    import pdfplumber
    sid = _rid(url)
    path = fetch_pdf(url)
    with pdfplumber.open(path) as pdf:
        pages_text = [pg.extract_text() or "" for pg in pdf.pages]
    res = extract_resources(pages_text)
    meth = extract_methodology(pages_text)

    con = store.connect()
    # deposit_model rows
    con.execute("DELETE FROM deposit_model WHERE source_id=?", (sid,))
    dm = []
    for k, r in enumerate(res):
        dm.append({"id": f"{sid}:{k}", "source_id": sid, "project": project,
                   "jurisdiction": jurisdiction, "category": r["category"],
                   "tonnes": r["tonnes"], "grade": None, "grade_unit": None,
                   "contained": None, "contained_unit": None, "cutoff": None,
                   "cutoff_unit": None, "commodity": commodity, "report_url": url,
                   "report_date": report_date, "note": r["note"]})
    if dm:
        store.add_deposit_model(con, dm)
    # model_method row
    con.execute("DELETE FROM model_method WHERE source_id=?", (sid,))
    if meth:
        con.execute("""INSERT INTO model_method
            (id,source_id,project,jurisdiction,commodity,estimation_method,software,block_size,
             compositing_m,capping,density,cutoff,cutoff_basis,search_params,compositing,domaining,
             classification,qaqc,section_ref,method_text,report_url,report_date)
            VALUES (:id,:source_id,:project,:jurisdiction,:commodity,:estimation_method,:software,:block_size,
             NULL,:capping,:density,:cutoff,NULL,:search_params,:compositing,NULL,
             :classification,NULL,:section_ref,:method_text,:report_url,:report_date)""", {
            "id": f"{sid}:method", "source_id": sid, "project": project, "jurisdiction": jurisdiction,
            "commodity": commodity, "estimation_method": meth["estimation_method"],
            "software": meth["software"], "block_size": meth["block_size"], "capping": meth["capping"],
            "density": meth["density"], "cutoff": meth["cutoff"], "search_params": meth["search_params"],
            "compositing": meth["compositing"], "classification": meth["classification"],
            "section_ref": f"{meth['n_method_pages']} methodology pages", "method_text": meth["method_text"],
            "report_url": url, "report_date": report_date})
    store.record_source(con, {
        "id": sid, "kind": "ni43101", "name": project or url.rsplit("/", 1)[-1],
        "url": url, "jurisdiction": jurisdiction,
        "pulled_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_collars": 0, "n_assays": 0,
        "note": f"{len(dm)} resource rows; method={'y' if meth else 'n'}"})
    con.commit(); con.close()
    print(f"[43-101] {project or url}: {len(dm)} resource rows | "
          f"method: { {k: v for k, v in (meth or {}).items() if k not in ('method_text','n_method_pages') and v} }")
    return {"resources": len(dm), "method": bool(meth)}


if __name__ == "__main__":
    a = sys.argv[1:]
    ingest_report(a[0], project=a[1] if len(a) > 1 else None,
                  commodity=a[2] if len(a) > 2 else None,
                  jurisdiction=a[3] if len(a) > 3 else None)
