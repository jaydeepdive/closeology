"""Newswire crawlers. Each adapter yields release descriptors
{source, url, title, published, company} for the drill-data pipeline to fetch
and extract. Adapters expose:

  incremental(session) -> recent releases (cheap; run daily in CI)
  backfill(session, seen) -> as far back as the source exposes (resumable: the
                             orchestrator skips ids already in the bank)

Fully implemented: newsfilecorp (dominant junior-mining wire) and thenewswire.
Cision/newswire.ca, GlobeNewswire, AccessWire, BusinessWire are registered
best-effort adapters (their public listings are JS-heavy / anti-bot; they will
fill in as their access is worked out, and the deterministic extractor + failure
log already handle whatever they return)."""
import re
import time
import requests

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
      "Accept-Language": "en-CA,en;q=0.9"}

# keywords that mark a release worth fetching for drill/assay data
DRILL_KW = re.compile(r"drill|intersect|assay|g-?t|grades?|gold|copper|zinc|nickel|"
                      r"lithium|discover|mineraliz|intercept|hole|core|resource|"
                      r"silver|uranium|cobalt|rare-?earth|deposit|vein|porphyry", re.I)

NFC = "https://www.newsfilecorp.com"
NFC_CATS = ["mining-metals", "precious-metals", "non-ferrous-metals",
            "energy-metals", "rare-earths", "diamonds"]


# hard wall-clock deadline for the WHOLE crawl (listings + fetches). run.run sets
# it; _get refuses to start a request past it, so a throttled wire can never
# stall the CI build no matter how the retries fall.
DEADLINE = None


def set_deadline(seconds_from_now):
    global DEADLINE
    DEADLINE = time.time() + seconds_from_now


def _expired():
    return DEADLINE is not None and time.time() > DEADLINE


def new_session():
    s = requests.Session()
    s.headers.update(UA)
    return s


def _get(session, url, timeout=20, tries=3):
    for k in range(tries):
        if _expired():
            return None
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 200 and r.text:
                return r.text
            if r.status_code in (202, 429, 503):     # throttle/challenge
                if _expired():
                    return None
                time.sleep(min(3.0 * (k + 1), 6.0))
                continue
        except Exception:
            pass
        if _expired():
            return None
        time.sleep(min(1.5 * (k + 1), 4.0))
    return None


# ------------------------------------------------------------------ newsfilecorp
def _nfc_from_html(html):
    out, seen = [], set()
    for rid, slug in re.findall(r"/release/(\d+)/([A-Za-z0-9\-\.]+)", html or ""):
        if rid in seen:
            continue
        seen.add(rid)
        out.append({"source": "newsfilecorp", "id": rid,
                    "url": f"{NFC}/release/{rid}/{slug}",
                    "title": slug.replace("-", " "), "published": None, "company": None})
    return out


def nfc_incremental(session):
    rel = {}
    for cat in NFC_CATS:
        html = _get(session, f"{NFC}/news/{cat}")
        for r in _nfc_from_html(html):
            rel[r["id"]] = r
        time.sleep(0.4)
    return list(rel.values())


def nfc_sitemap(session):
    """Recent release URLs (~5000) from the news sitemap — the backfill pool."""
    html = _get(session, f"{NFC}/sitemap-news.php")
    rows = _nfc_from_html(html)
    # keep only plausibly-mining slugs to avoid fetching every industry
    return [r for r in rows if DRILL_KW.search(r["url"])]


# ------------------------------------------------------------------ thenewswire
TNW = "https://www.thenewswire.com"


def tnw_incremental(session):
    html = _get(session, f"{TNW}/latest-press-releases")
    if not html:
        html = _get(session, TNW)
    out, seen = [], set()
    for m in re.finditer(r'href="(/press-releases?/[^"]+|/articles?/[^"]+)"[^>]*>([^<]{8,140})', html or ""):
        href, title = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if href in seen or not DRILL_KW.search(title):
            continue
        seen.add(href)
        url = href if href.startswith("http") else TNW + href
        out.append({"source": "thenewswire", "id": href.rsplit("/", 1)[-1][:40],
                    "url": url, "title": title, "published": None, "company": None})
    return out


# --------------------------------------------- best-effort adapters (JS/anti-bot)
def _empty(_session):
    return []


# Registered adapters. incremental runs daily; backfill seeds history where
# available. Others are wired so they activate the moment a working listing/feed
# (or the planned LLM-assisted fetch) is dropped in — no orchestrator change.
ADAPTERS = {
    "newsfilecorp": {"incremental": nfc_incremental, "backfill": nfc_sitemap},
    "thenewswire": {"incremental": tnw_incremental, "backfill": _empty},
    "cision": {"incremental": _empty, "backfill": _empty},        # newswire.ca
    "globenewswire": {"incremental": _empty, "backfill": _empty},
    "accesswire": {"incremental": _empty, "backfill": _empty},
    "businesswire": {"incremental": _empty, "backfill": _empty},
}


def fetch_release(session, url):
    return _get(session, url, timeout=60)
