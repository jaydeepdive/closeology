"""Headless SEDAR+ collector — the DOWNLOAD half, without Claude-in-Chrome.

SEDAR+ is Akamai-protected: a plain HTTP request from a datacenter IP is 403'd,
which is why collection previously needed a browser driven live. This module
removes the live driver: it runs its OWN headless Chromium via Playwright on the
machine it's launched from, so on a residential connection (the user's Mac, incl.
inside the Mac-bound scheduled task's VM) it passes Akamai like any real browser —
no Claude, no Chrome extension, fully unattended and schedulable.

What it does each run:
  1. opens the SEDAR+ document search and filters to Technical report (NI 43-101),
     newest first;
  2. walks result pages, reading each row's real resource URL (node/drmKey +
     the page's live session tokens drr/id) and its metadata;
  3. downloads every report NOT already in the ledger (dedup by SEDAR node id),
     using the browser context's own cookies, to a folder;
  4. records each in data/keep/sedar_manifest.json;
  5. (optional) hands the folder to minemodelingpro.sedar.ingest_folder, which
     extracts every report with the v6 extractor and retains the PDF.

Two browser modes:
  * standalone (default): launches its own persistent Chromium profile (Akamai
    trust persists between runs) and performs the search itself.
  * attach (--cdp PORT): connects to a Chrome already running with
    --remote-debugging-port=PORT and reuses whatever SEDAR results tab is open —
    the most reliable path, reusing a human-established session.

Run (on the Mac / its VM):
    pip install playwright && python -m playwright install chromium
    PYTHONPATH=src python -m minemodelingpro.sedar_collect --max-pages 3 --ingest
    PYTHONPATH=src python -m minemodelingpro.sedar_collect --cdp 9222 --ingest
"""
import os
import re
import sys
import json
import time
import argparse
import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = os.path.join(_ROOT, "data", "keep", "sedar_manifest.json")
DOWNLOADS = os.path.join(_ROOT, "data", "keep", "sedar_pdfs")
PROFILE = os.path.join(_ROOT, "data", "keep", "sedar_profile")
SEARCH_URL = "https://www.sedarplus.ca/csa-party/records/search.html"
DOCTYPE = "Technical report (NI 43-101)"


def _load_ledger():
    try:
        return json.load(open(LEDGER))
    except Exception:
        return []


def _save_ledger(rows):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    json.dump(rows, open(LEDGER, "w"), indent=1)


def _digits(s):
    m = re.search(r"\d[\d,]*", str(s or ""))
    return int(m.group().replace(",", "")) if m else None


# ---- page scraping (runs in the browser via page.evaluate) ----------------
# Reads every result row's resource link (node/drmKey/drr/id live in the href)
# plus the row's visible metadata. Resilient to column reordering: it keys off
# the "Open the document …" links SEDAR+ renders per row.
_ROW_JS = r"""
() => {
  const out = [];
  const links = Array.from(document.querySelectorAll('a[href*="resource.html"]'));
  for (const a of links) {
    const href = a.getAttribute('href') || '';
    const u = new URL(href, location.origin);
    const node = u.searchParams.get('node');
    if (!node) continue;
    const row = a.closest('tr');
    const cells = row ? Array.from(row.querySelectorAll('td')).map(td => td.innerText.trim()) : [];
    out.push({
      node,
      drmKey: u.searchParams.get('drmKey'),
      drr: u.searchParams.get('drr'),
      id: u.searchParams.get('id'),
      url: u.href,
      cells
    });
  }
  return out;
}
"""


def _row_meta(cells):
    """Best-effort pull of company / submitted / jurisdiction / size from a row's
    cell texts (SEDAR+ order: Profile(s), Document, Submitted date, Jurisdiction,
    File size, Actions)."""
    company = submitted = jurisdiction = None
    size_kb = None
    for c in cells:
        if company is None and c and "Technical report" not in c and not re.match(r"^\d", c):
            company = c.split("/")[0].strip()[:120]
        if submitted is None:
            m = re.search(r"\d{1,2}\s+\w{3}\s+20\d\d(?:\s+\d\d:\d\d(?:\s+\w+)?)?", c)
            if m:
                submitted = m.group().strip()
        if jurisdiction is None and c in (
                "Ontario", "British Columbia", "Alberta", "Quebec", "Québec", "Saskatchewan",
                "Manitoba", "Yukon", "Nunavut", "Northwest Territories", "Nova Scotia",
                "New Brunswick", "Newfoundland and Labrador", "Prince Edward Island"):
            jurisdiction = c
        if size_kb is None and re.search(r"\bKB\b", c):
            size_kb = _digits(c)
    return company, submitted, jurisdiction, size_kb


def _open_search(page, log):
    """Drive the SEDAR+ document search to NI 43-101 reports, newest first."""
    log("opening search …")
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)
    # reveal advanced fields if collapsed
    for label in ["Show advanced search", "Advanced search"]:
        try:
            el = page.get_by_text(label, exact=False)
            if el.count():
                el.first.click(timeout=4000); page.wait_for_timeout(800); break
        except Exception:
            pass
    # set the document type to NI 43-101 (try a few control shapes)
    set_ok = False
    for how in ("label", "placeholder", "combobox"):
        try:
            if how == "label":
                box = page.get_by_label(re.compile("document type", re.I))
            elif how == "placeholder":
                box = page.get_by_placeholder(re.compile("document type", re.I))
            else:
                box = page.get_by_role("combobox")
            if not box.count():
                continue
            box.first.click(timeout=4000)
            box.first.fill(DOCTYPE) if how != "combobox" else box.first.type(DOCTYPE, delay=25)
            page.wait_for_timeout(900)
            opt = page.get_by_text(DOCTYPE, exact=False)
            if opt.count():
                opt.first.click(timeout=4000)
            set_ok = True
            log(f"document type set via {how}")
            break
        except Exception as e:
            log(f"  doctype via {how} failed: {str(e)[:80]}")
    if not set_ok:
        log("WARN: could not set document type automatically — searching unfiltered; "
            "use --cdp to reuse a search you set up by hand.")
    # submit
    for how in (lambda: page.get_by_role("button", name=re.compile("^search$", re.I)),
                lambda: page.get_by_text(re.compile("^Search$"))):
        try:
            b = how()
            if b.count():
                b.first.click(timeout=5000); break
        except Exception:
            pass
    page.wait_for_selector('a[href*="resource.html"]', timeout=60000)
    log("results loaded")


def _find_results_tab(context, log):
    """CDP-attach mode: find an open tab already showing SEDAR results."""
    for pg in context.pages:
        try:
            if "sedarplus.ca" in (pg.url or "") and pg.query_selector('a[href*="resource.html"]'):
                log(f"attached to open SEDAR tab: {pg.url[:80]}")
                return pg
        except Exception:
            continue
    return None


def _download(context, url, dest, log):
    """Download one PDF via the browser context's request (shares Akamai cookies)."""
    try:
        resp = context.request.get(url, timeout=120000)
        if resp.status != 200:
            log(f"  download {resp.status} for {os.path.basename(dest)}"); return False
        body = resp.body()
        if not body[:5].startswith(b"%PDF"):
            log(f"  not a PDF (Akamai challenge?) for {os.path.basename(dest)}"); return False
        open(dest, "wb").write(body)
        return True
    except Exception as e:
        log(f"  download error: {str(e)[:100]}"); return False


def collect(max_pages=2, headful=False, cdp=None, ingest=False, throttle=2.0, log=print):
    from playwright.sync_api import sync_playwright
    os.makedirs(DOWNLOADS, exist_ok=True)
    ledger = _load_ledger()
    have = {r.get("node") for r in ledger if r.get("node")}
    new_rows, downloaded = [], 0

    with sync_playwright() as pw:
        if cdp:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{cdp}")
            context = browser.contexts[0] if browser.contexts else browser.new_context(accept_downloads=True)
            page = _find_results_tab(context, log)
            if page is None:
                log("no open SEDAR results tab found — open the NI 43-101 search in that Chrome first.")
                return {"downloaded": 0}
        else:
            context = pw.chromium.launch_persistent_context(
                PROFILE, headless=not headful, accept_downloads=True,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"))
            page = context.new_page()
            _open_search(page, log)

        for pageno in range(1, max_pages + 1):
            page.wait_for_selector('a[href*="resource.html"]', timeout=60000)
            rows = page.evaluate(_ROW_JS)
            log(f"page {pageno}: {len(rows)} documents listed")
            for r in rows:
                node = r["node"]
                if node in have:
                    continue
                company, submitted, jurisdiction, size_kb = _row_meta(r.get("cells") or [])
                dest = os.path.join(DOWNLOADS, f"sedar_{node}.pdf")
                if os.path.exists(dest) or _download(context, r["url"], dest, log):
                    downloaded += 1
                    have.add(node)
                    row = {"node": node, "drm": r.get("drmKey"),
                           "file": os.path.basename(dest), "filing_ref": node,
                           "company": company, "submitted": submitted,
                           "jurisdiction": jurisdiction, "size_kb": size_kb,
                           "doctype": DOCTYPE, "sedar_url": r["url"],
                           "collected": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
                    ledger.append(row); new_rows.append(row)
                    log(f"  ✓ {company or node} ({size_kb or '?'} KB)")
                    _save_ledger(ledger)          # checkpoint after every file
                    time.sleep(throttle)          # be gentle; slowness is fine
            # next page
            try:
                nxt = page.get_by_text(re.compile(r"Next\s*»")).first
                if not nxt.count():
                    log("no further pages"); break
                nxt.click(timeout=8000); page.wait_for_timeout(2500)
            except Exception:
                log("could not advance to next page"); break

        if not cdp:
            context.close()

    log(f"collected {downloaded} new report(s); ledger now {len(ledger)} rows")
    if ingest and downloaded:
        from minemodelingpro import sedar
        sedar.ingest_folder(DOWNLOADS, LEDGER)
    return {"downloaded": downloaded, "new": new_rows}


def main():
    ap = argparse.ArgumentParser(description="Headless SEDAR+ 43-101 collector (no Claude-in-Chrome)")
    ap.add_argument("--max-pages", type=int, default=2, help="result pages to walk (30 reports/page)")
    ap.add_argument("--headful", action="store_true", help="show the browser (debugging)")
    ap.add_argument("--cdp", type=int, default=None, help="attach to Chrome on this remote-debugging port")
    ap.add_argument("--ingest", action="store_true", help="extract downloaded PDFs after collecting")
    ap.add_argument("--throttle", type=float, default=2.0, help="seconds between downloads")
    a = ap.parse_args()
    collect(max_pages=a.max_pages, headful=a.headful, cdp=a.cdp, ingest=a.ingest, throttle=a.throttle)


if __name__ == "__main__":
    main()
