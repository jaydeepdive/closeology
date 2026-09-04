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


_MON = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _profile_num(cells):
    for c in cells:
        m = re.search(r"\((\d{5,})\)", c)
        if m:
            return m.group(1)
    return None


def _submitted_compact(submitted):
    """'03 Sep 2026 19:01 EDT' -> '20260903T1901' (stable across sessions)."""
    if not submitted:
        return None
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", submitted)
    if not m:
        return re.sub(r"[^0-9A-Za-z]", "", submitted)[:16] or None
    d, mon, y, hh, mm = m.groups()
    return f"{y}{_MON.get(mon.title(), 0):02d}{int(d):02d}T{(hh or '00').zfill(2)}{mm or '00'}"


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")[:44]


def _stable_key(company, submitted):
    """DEDUP KEY: issuer + exact submitted datetime — stable across SEDAR sessions,
    unlike the per-session `node` handle. Two filings never share an issuer AND a
    to-the-minute submission time."""
    sc = _submitted_compact(submitted)
    base = _slug(company)
    if not base and not sc:
        return None
    return f"{base}_{sc}" if sc else base


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


def _click_text(page, label, log):
    """Click the first visible element whose exact text matches `label`."""
    try:
        el = page.get_by_text(re.compile(r"^\s*" + re.escape(label) + r"\s*$", re.I))
        if el.count():
            el.first.click(timeout=6000)
            return True
    except Exception as e:
        log(f"  click '{label}': {str(e)[:60]}")
    return False


def _set_select(page, want_regex, label=""):
    """Select the first <select> option whose text matches want_regex, firing a
    change event so SEDAR+'s cascade updates. Returns the chosen option text."""
    got = page.evaluate("""(rx) => {
      const re = new RegExp(rx, 'i');
      for (const s of Array.from(document.querySelectorAll('select'))) {
        for (let i = 0; i < s.options.length; i++) {
          if (re.test((s.options[i].text || '').trim())) {
            s.selectedIndex = i;
            s.dispatchEvent(new Event('change', {bubbles: true}));
            return (s.options[i].text || '').trim();
          }
        }
      }
      return null;
    }""", want_regex)
    return got


def _open_search(page, log):
    """From the SEDAR+ home, walk to the Documents search and filter to NI 43-101
    technical reports. The results view is a fresh per-session URL, so we click
    through rather than navigate to a static page. Best-effort throughout: if the
    filter cascade doesn't fully take, collect() still keeps only 43-101 rows and
    the human can finish the search in the open window."""
    log("opening SEDAR+ home …")
    try:
        page.goto("https://www.sedarplus.ca/home/", wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        log(f"  home nav: {str(e)[:80]}")
    page.wait_for_timeout(3500)
    if _click_text(page, "Search SEDAR+", log):
        page.wait_for_timeout(1500)
    if _click_text(page, "Documents", log):
        page.wait_for_timeout(3500)
    log(f"  on: {page.url[:80]}")
    # filter cascade: Continuous disclosure -> Technical Report -> NI 43-101 subtype
    for regex, lbl, wait in [
            (r"^Continuous disclosure$", "category", 2000),
            (r"^Technical [Rr]eport$", "type", 2000),
            (r"Technical report \(NI 43-101\)", "subtype", 1200)]:
        try:
            got = _set_select(page, regex, lbl)
            if got:
                log(f"  filter {lbl}: {got}")
            page.wait_for_timeout(wait)
        except Exception as e:
            log(f"  filter {lbl}: {str(e)[:60]}")
    # submit the search (the form's button, not the nav item)
    for how in (lambda: page.get_by_role("button", name=re.compile(r"^\s*Search\s*$", re.I)),
                lambda: page.locator("button:has-text('Search')")):
        try:
            b = how()
            if b.count():
                b.last.click(timeout=6000); break
        except Exception:
            pass
    page.wait_for_timeout(3000)


def _await_results(context, log, timeout=210):
    """Poll every tab for a rendered results list. In --chrome mode this lets the
    human finish/adjust the search in the open window if the auto-walk fell short."""
    log("waiting for a results list… (if the browser is sitting on the SEDAR home or an "
        "empty search, click Search > Documents, filter to Technical report (NI 43-101), Search)")
    end = time.time() + timeout
    while time.time() < end:
        for pg in list(context.pages):
            try:
                if pg.query_selector('a[href*="resource.html"]'):
                    log(f"results detected: {pg.url[:80]}")
                    return pg
            except Exception:
                continue
        time.sleep(3)
    return None


def _find_results_tab(browser, log):
    """CDP-attach mode: scan every context/tab for one already showing SEDAR
    results. Returns (page, its_context) so downloads reuse that tab's cookies."""
    ctxs = browser.contexts or []
    # a SEDAR tab with results already rendered
    for ctx in ctxs:
        for pg in ctx.pages:
            try:
                if "sedarplus.ca" in (pg.url or "") and pg.query_selector('a[href*="resource.html"]'):
                    log(f"attached to SEDAR results tab: {pg.url[:80]}")
                    return pg, ctx
            except Exception:
                continue
    # any SEDAR tab (results maybe not loaded yet)
    for ctx in ctxs:
        for pg in ctx.pages:
            try:
                if "sedarplus.ca" in (pg.url or ""):
                    log(f"found a SEDAR tab (no results yet): {pg.url[:80]}")
                    return pg, ctx
            except Exception:
                continue
    return None, (ctxs[0] if ctxs else None)


_FETCH_JS = """async (u) => {
  try {
    const r = await fetch(u, {credentials: 'include'});
    const b = new Uint8Array(await r.arrayBuffer());
    let s = ''; const c = 0x8000;
    for (let i = 0; i < b.length; i += c) s += String.fromCharCode.apply(null, b.subarray(i, i + c));
    return {status: r.status, b64: btoa(s)};
  } catch (e) { return {error: String(e)}; }
}"""


def _download(page, url, dest, log):
    """Fetch the PDF from inside the results page (same-origin, trusted session).
    Returns 'ok', 'throttled' (SEDAR system-error page → the per-session download
    limit), or 'fail'. When not throttled this pulls PDFs cleanly; the batch stops
    on throttle so a fresh session next run resumes."""
    import base64
    for attempt in range(2):
        try:
            res = page.evaluate(_FETCH_JS, url)
        except Exception as e:
            log(f"  fetch exception a{attempt+1}: {str(e)[:90]}"); time.sleep(5); continue
        if res.get("error"):
            log(f"  fetch error a{attempt+1}: {str(res['error'])[:80]}"); time.sleep(6); continue
        data = base64.b64decode(res.get("b64") or "")
        if data[:5].startswith(b"%PDF"):
            open(dest, "wb").write(data)
            return "ok"
        try:
            open(os.path.join(os.path.dirname(DOWNLOADS), "sedar_debug_last.html"), "wb").write(data)
        except Exception:
            pass
        m = re.search(rb"error code (\d{8}-\d{6}-\d+)", data)
        throttled = b"unexpected system error" in data or bool(m)
        log(f"  {'THROTTLED' if throttled else 'not a PDF'} (HTTP {res.get('status')}, {len(data)}B)"
            f"{' ' + m.group(1).decode() if m else ''}")
        if throttled:
            return "throttled"
        time.sleep(6)
    return "fail"


def collect(max_pages=40, headful=False, cdp=None, ingest=False, throttle=8.0,
            chrome=False, limit=25, log=print):
    from playwright.sync_api import sync_playwright
    os.makedirs(DOWNLOADS, exist_ok=True)
    ledger = _load_ledger()
    have = set()
    for r in ledger:
        for k in (r.get("key"), r.get("filing_ref")):
            if k:
                have.add(str(k))
        sk = _stable_key(r.get("company"), r.get("submitted"))   # migrate legacy rows
        if sk:
            have.add(sk)
    new_rows, downloaded = [], 0

    with sync_playwright() as pw:
        if cdp:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{cdp}")
            page, context = _find_results_tab(browser, log)
            if page is None:
                log("no SEDAR tab found in that Chrome — open the NI 43-101 search there first "
                    "(sedarplus.ca > Search > Documents > Document type: Technical report (NI 43-101) > Search).")
                return {"downloaded": 0}
        else:
            kw = dict(headless=not headful, accept_downloads=True,
                      args=["--disable-blink-features=AutomationControlled"],
                      user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"))
            if chrome:                       # real installed Chrome — passes Akamai like any Chrome
                kw["channel"] = "chrome"
                kw.pop("user_agent", None)   # let real Chrome send its own UA
                kw["headless"] = False       # headed real Chrome is the reliable fingerprint
            context = pw.chromium.launch_persistent_context(PROFILE, **kw)
            page = context.pages[0] if context.pages else context.new_page()
            _open_search(page, log)
            got = _await_results(context, log, timeout=(210 if chrome else 8))
            if got is None:
                log("no results list appeared — nothing to collect.")
                if not (headful or chrome):
                    context.close()
                return {"downloaded": 0}
            page = got
            page.wait_for_timeout(4000)          # let the filtered results settle

        def _harvest():
            page.wait_for_selector('a[href*="resource.html"]', timeout=60000)
            rs = page.evaluate(_ROW_JS)
            # keep only NI 43-101 technical reports, even if the server-side filter
            # didn't fully take (belt-and-suspenders on the doctype cascade)
            return [r for r in rs if any("43-101" in c for c in (r.get("cells") or []))]

        stopped = None
        for pageno in range(1, max_pages + 1):
            rows = _harvest()
            if not rows:                          # results may still be re-rendering
                page.wait_for_timeout(3500); rows = _harvest()
            log(f"page {pageno}: {len(rows)} NI 43-101 report(s) on this page")
            for r in rows:
                if limit and downloaded >= limit:
                    stopped = "limit"; break
                company, submitted, jurisdiction, size_kb = _row_meta(r.get("cells") or [])
                profile = _profile_num(r.get("cells") or [])
                key = _stable_key(company, submitted) or ("node-" + r["node"])
                if key in have:
                    continue                      # already owned — skipping is free (no download)
                dest = os.path.join(DOWNLOADS, f"sedar_{key}.pdf")
                if os.path.exists(dest):
                    res = "ok"
                else:
                    time.sleep(throttle)          # space every download (be gentle)
                    res = _download(page, r["url"], dest, log)
                if res == "throttled":
                    # SEDAR's per-session download limit — stop cleanly; a fresh
                    # session on the next scheduled batch resumes from here.
                    log(f"SEDAR download limit reached after {downloaded} this batch — stopping; "
                        f"next batch (fresh session) continues.")
                    stopped = "throttled"; break
                if res != "ok":
                    continue                      # transient miss; try more rows
                downloaded += 1
                have.add(key)
                row = {"key": key, "node": r["node"], "drm": r.get("drmKey"),
                       "file": os.path.basename(dest), "filing_ref": key,
                       "company": company, "profile": profile, "submitted": submitted,
                       "jurisdiction": jurisdiction, "size_kb": size_kb,
                       "doctype": DOCTYPE, "sedar_url": r["url"],
                       "collected": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
                ledger.append(row); new_rows.append(row)
                log(f"  ✓ {company or key} ({size_kb or '?'} KB)  [{downloaded}]")
                _save_ledger(ledger)              # checkpoint after every file
                if os.environ.get("GITHUB_TOKEN"):   # retain the PDF durably (additive)
                    try:
                        from minemodelingpro import report_archive
                        url = report_archive.archive_pdf(dest, "sedar_" + key)
                        if url:
                            row["archive_url"] = url; _save_ledger(ledger)
                    except Exception as e:
                        log(f"  archive skip: {str(e)[:60]}")
            if stopped:
                break
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
    ap.add_argument("--limit", type=int, default=25, help="max NEW reports to download this batch (default 25)")
    ap.add_argument("--max-pages", type=int, default=40, help="max result pages to walk looking for new reports")
    ap.add_argument("--headful", action="store_true", help="show the browser (debugging)")
    ap.add_argument("--cdp", type=int, default=None, help="attach to Chrome on this remote-debugging port")
    ap.add_argument("--chrome", action="store_true", help="launch your real installed Chrome (channel=chrome) and search automatically — the hands-off mode")
    ap.add_argument("--ingest", action="store_true", help="extract downloaded PDFs after collecting")
    ap.add_argument("--throttle", type=float, default=8.0, help="seconds between downloads (be gentle)")
    a = ap.parse_args()
    collect(max_pages=a.max_pages, headful=a.headful, cdp=a.cdp, ingest=a.ingest,
            throttle=a.throttle, chrome=a.chrome, limit=a.limit)


if __name__ == "__main__":
    main()
