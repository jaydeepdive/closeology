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


# --------------------------------------------------------- appendix drill tables
# Full collar + assay tables live in the report appendices as BORDERLESS tables.
# Camelot's stream flavour reconstructs them from text alignment (pdfplumber's
# text strategy over-fragments columns). We pre-scan text for candidate pages so
# Camelot only parses the table pages (it is slow), then map columns by header.
_ELEM = {"au": ("Au", "g/t"), "gold": ("Au", "g/t"), "ag": ("Ag", "g/t"), "silver": ("Ag", "g/t"),
         "cu": ("Cu", "%"), "copper": ("Cu", "%"), "pb": ("Pb", "%"), "lead": ("Pb", "%"),
         "zn": ("Zn", "%"), "zinc": ("Zn", "%"), "ni": ("Ni", "%"), "nickel": ("Ni", "%"),
         "co": ("Co", "%"), "mo": ("Mo", "%"), "moly": ("Mo", "%"), "sn": ("Sn", "%"),
         "w": ("W", "%"), "wo3": ("WO3", "%"), "u3o8": ("U3O8", "%"), "u": ("U", "%"),
         "li2o": ("Li2O", "%"), "li": ("Li", "%"), "sb": ("Sb", "%"), "v2o5": ("V2O5", "%"),
         "fe": ("Fe", "%"), "mn": ("Mn", "%"), "cr2o3": ("Cr2O3", "%"), "pt": ("Pt", "g/t"),
         "pd": ("Pd", "g/t"), "aueq": ("AuEq", "g/t"), "ageq": ("AgEq", "g/t"),
         "cueq": ("CuEq", "%"), "reo": ("REO", "%"), "treo": ("TREO", "%")}
_COL_C = re.compile(r"easting|northing|utm[_ ]?[en]\b|azimuth|\bdip\b|\bcollar\b", re.I)
_COL_A = re.compile(r"\bfrom\b|\bto\s*\(|\binterval\b|\bassay", re.I)


def _num_cell(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("−", "-").strip()
    m = re.match(r"-?\d+\.?\d*", s.lstrip("~<>= "))
    try:
        return float(m.group(0)) if m else None
    except ValueError:
        return None


def _candidate_pages(pages_text):
    """1-indexed pages likely holding collar or assay tables (strict, to skip prose)."""
    coll, assay = [], []
    for i, t in enumerate(pages_text):
        low = (t or "").lower()
        if "easting" in low and "northing" in low and len(re.findall(r"\b\d{5,7}\b", t or "")) >= 6:
            coll.append(i + 1)
        if re.search(r"\bfrom\b", low) and re.search(r"\bto\b", low) and \
           re.search(r"\b(au|ag|cu|pb|zn|g/t|grade)\b", low) and len(re.findall(r"\d+\.\d", t or "")) >= 8:
            assay.append(i + 1)
    return coll, assay


def _merge_header(rows, max_head=4):
    """Combine the leading header rows (until the first mostly-numeric row) into a
    per-column header string. Returns (col_headers, data_start_index)."""
    data_start = 0
    for idx, r in enumerate(rows[:max_head + 1]):
        nums = sum(1 for c in r if _num_cell(c) is not None)
        cells = sum(1 for c in r if str(c).strip())
        if cells and nums >= max(2, cells // 2):
            data_start = idx
            break
    else:
        data_start = min(max_head, len(rows) - 1)
    if data_start == 0:
        data_start = 1
    ncol = max(len(r) for r in rows) if rows else 0
    heads = []
    for c in range(ncol):
        parts = [str(rows[r][c]).strip() for r in range(data_start)
                 if c < len(rows[r]) and str(rows[r][c]).strip()]
        heads.append(" ".join(parts).lower())
    return heads, data_start


def _map_columns(heads):
    m = {"elements": []}
    for i, h in enumerate(heads):
        if not h:
            continue
        if re.search(r"hole.*(id|no|number|name)|^hole$|ddh|bhid|drill ?hole|hole id", h):
            m.setdefault("hole", i)
        elif "easting" in h or re.search(r"utm[_ ]?e\b|^east", h):
            m.setdefault("easting", i)
        elif "northing" in h or re.search(r"utm[_ ]?n\b|^north", h):
            m.setdefault("northing", i)
        elif re.search(r"elev|^rl\b|elevation", h):
            m.setdefault("elev", i)
        elif re.search(r"azimuth|\baz\b", h):
            m.setdefault("azimuth", i)
        elif re.search(r"\bdip\b|inclination|incl", h):
            m.setdefault("dip", i)
        elif re.search(r"^from|\bfrom\b", h):
            m.setdefault("from", i)
        elif re.search(r"^to\b|\bto\b|\bto\(", h):
            m.setdefault("to", i)
        elif re.search(r"length|width|interval|thickness|core len", h):
            m.setdefault("length", i)
        elif re.search(r"depth|eoh|total depth|hole length|final depth", h):
            m.setdefault("depth", i)
        else:
            tok = re.sub(r"[^a-z0-9]", "", re.split(r"[\s(]", h)[0])
            if tok in _ELEM:
                el, unit = _ELEM[tok]
                u = "g/t" if "g/t" in h or "gpt" in h or "g/t" in h else ("%" if "%" in h or "pct" in h else unit)
                m["elements"].append((i, el, u))
    return m


def extract_drill_tables(path, pages_text, max_pages=80):
    import warnings; warnings.filterwarnings("ignore")
    import camelot
    coll_p, assay_p = _candidate_pages(pages_text)
    pages = sorted(set(coll_p + assay_p))[:max_pages]
    collars, assays = [], []
    if not pages:
        return collars, assays
    # camelot in modest chunks to bound memory
    for start in range(0, len(pages), 20):
        chunk = pages[start:start + 20]
        try:
            tabs = camelot.read_pdf(path, pages=",".join(map(str, chunk)), flavor="stream")
        except Exception:
            continue
        for tb in tabs:
            rows = tb.df.values.tolist()
            if len(rows) < 3:
                continue
            heads, ds = _merge_header(rows)
            cm = _map_columns(heads)
            is_collar = "easting" in cm and "northing" in cm and "hole" in cm
            is_assay = "from" in cm and "to" in cm and cm["elements"] and "hole" in cm
            if not (is_collar or is_assay):
                continue
            last_hole = None
            for r in rows[ds:]:
                def cell(k):
                    return r[cm[k]] if k in cm and cm[k] < len(r) else None
                hid = str(cell("hole") or "").strip()
                if hid:
                    last_hole = hid
                hid = hid or last_hole
                if not hid or hid.lower() in ("total", "average", "mean"):
                    continue
                if is_collar:
                    e, n = _num_cell(cell("easting")), _num_cell(cell("northing"))
                    if e is None or n is None:
                        continue
                    collars.append({"native_id": hid, "easting": e, "northing": n,
                                    "elev_m": _num_cell(cell("elev")), "azimuth": _num_cell(cell("azimuth")),
                                    "dip": _num_cell(cell("dip")), "depth_m": _num_cell(cell("depth"))})
                if is_assay:
                    fr, to = _num_cell(cell("from")), _num_cell(cell("to"))
                    if fr is None or to is None:
                        continue
                    ln = _num_cell(cell("length"))
                    ln = ln if ln is not None else (round(to - fr, 2) if to >= fr else None)
                    for ci, el, unit in cm["elements"]:
                        g = _num_cell(r[ci]) if ci < len(r) else None
                        if g is None:
                            continue
                        assays.append({"native_id": hid, "from_m": fr, "to_m": to, "length_m": ln,
                                       "element": el, "grade": g, "unit": unit, "is_subinterval": 0})
    # de-dupe
    cu = {c["native_id"]: c for c in collars}
    seen = set(); ua = []
    for a in assays:
        k = (a["native_id"], a["from_m"], a["to_m"], a["element"])
        if k not in seen:
            seen.add(k); ua.append(a)
    return list(cu.values()), ua


def ingest_report(url, project=None, commodity=None, jurisdiction=None, report_date=None, drill_tables=True):
    import pdfplumber
    sid = _rid(url)
    path = fetch_pdf(url)
    with pdfplumber.open(path) as pdf:
        pages_text = [pg.extract_text() or "" for pg in pdf.pages]
    res = extract_resources(pages_text)
    meth = extract_methodology(pages_text)
    collars, assays = ([], [])
    if drill_tables:
        try:
            collars, assays = extract_drill_tables(path, pages_text)
        except Exception as e:
            print(f"[43-101] drill-table extract skipped: {str(e)[:100]}")

    con = store.connect()
    # collars + assays from appendix drill tables
    con.execute("DELETE FROM collars WHERE source_id=?", (sid,))
    if collars:
        crow = [{"hole_uid": f"{sid}:{c['native_id']}", "source_id": sid, "native_id": c["native_id"],
                 "company": None, "project": project, "jurisdiction": jurisdiction, "lat": None, "lon": None,
                 "easting": c["easting"], "northing": c["northing"], "utm_zone": None, "utm_hemi": "N",
                 "datum": None, "elev_m": c["elev_m"], "azimuth": c["azimuth"], "dip": c["dip"],
                 "depth_m": c["depth_m"], "year_drilled": None, "has_assay": 1, "assay_flags": None,
                 "report_ref": None, "url": url} for c in collars]
        store.replace_collars(con, sid, crow)
    con.execute("DELETE FROM assays WHERE source_id=?", (sid,))
    if assays:
        arow = [{"source_id": sid, "hole_uid": f"{sid}:{a['native_id']}", "native_id": a["native_id"],
                 "from_m": a["from_m"], "to_m": a["to_m"], "length_m": a["length_m"], "element": a["element"],
                 "grade": a["grade"], "unit": a["unit"], "is_subinterval": a["is_subinterval"]} for a in assays]
        store.replace_assays(con, sid, arow)
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
        "n_collars": len(collars), "n_assays": len(assays),
        "note": f"{len(dm)} resource rows; method={'y' if meth else 'n'}; "
                f"{len(collars)} collars; {len(assays)} assays"})
    con.commit(); con.close()
    print(f"[43-101] {project or url}: {len(dm)} resource rows, {len(collars)} collars, "
          f"{len(assays)} assays | method={meth['estimation_method'] if meth else None}")
    return {"resources": len(dm), "collars": len(collars), "assays": len(assays), "method": bool(meth)}


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QUEUE = os.path.join(_ROOT, "data", "keep", "mmp_report_queue.json")


def _already(con, url):
    return con.execute("SELECT 1 FROM sources WHERE id=?", (_rid(url),)).fetchone() is not None


def run_queue(path=QUEUE, limit=None, max_seconds=None, refresh=False):
    """Ingest every report in the queue not already in the store. Idempotent and
    resumable (skips ingested reports), time-budgeted for CI. Shards at the end.
    refresh=True re-ingests every report (e.g. after an extractor upgrade)."""
    import json
    import time
    from minemodelingpro import shards
    q = json.load(open(path))
    con = store.connect()
    todo = q if refresh else [r for r in q if not _already(con, r["url"])]
    con.close()
    print(f"[43-101] queue: {len(q)} reports, {len(todo)} new to ingest")
    t0 = time.time()
    done = ok = 0
    for r in todo:
        if limit and done >= limit:
            break
        if max_seconds and time.time() - t0 > max_seconds:
            print(f"[43-101] time budget reached — {done} done this run, rest resume next run")
            break
        done += 1
        try:
            res = ingest_report(r["url"], project=r.get("project"),
                                commodity=r.get("commodity"), jurisdiction=r.get("jurisdiction"))
            ok += 1
        except Exception as e:
            print(f"[43-101] FAILED {r.get('project') or r['url']}: {str(e)[:120]}")
    shards.export_shards()
    print(f"[43-101] run complete: {ok}/{done} ingested this run")
    return {"new": len(todo), "ingested_this_run": ok}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "queue":
        limit = int(a[a.index("--limit") + 1]) if "--limit" in a else None
        secs = int(a[a.index("--max-seconds") + 1]) if "--max-seconds" in a else None
        run_queue(limit=limit, max_seconds=secs, refresh="--refresh" in a)
    elif a:
        ingest_report(a[0], project=a[1] if len(a) > 1 else None,
                      commodity=a[2] if len(a) > 2 else None,
                      jurisdiction=a[3] if len(a) > 3 else None)
