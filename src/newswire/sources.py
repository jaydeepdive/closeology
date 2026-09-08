"""Newswire crawlers. Each adapter yields release descriptors
{source, url, title, published, company} for the drill-data pipeline to fetch
and extract. Adapters expose:

  incremental(session) -> recent releases (cheap; run daily in CI)
  backfill(session)     -> as far back as the source exposes (resumable: the
                           orchestrator skips ids already in the bank)

Reachability: every mining wire (GlobeNewswire, Business Wire, ACCESSWIRE,
Cision/newswire.ca, The Newswire, Junior Mining Network) fronts its site with
Akamai/Cloudflare TLS-fingerprint blocking, so plain requests/urllib get
403/timeout from BOTH datacenter runners AND residential clients. We fetch
through curl_cffi impersonating Chrome's TLS+HTTP2 fingerprint, which clears
every one of them from CI and the Mac alike — so collection no longer depends
on Jordan's Mac. Newsfile is taken from its own RSS + sequential id-walk; the
other wires are discovered via Google News RSS (keyword-filtered, date-window
backfill) whose article links we decode to the real wire URL and then fetch."""
import re
import time
import json
import html as _html
import urllib.parse
import requests

try:
    from curl_cffi import requests as _cr       # Chrome TLS impersonation
except Exception:                               # not yet installed -> bootstrap
    try:
        import subprocess as _sp, sys as _sys
        _sp.run([_sys.executable, "-m", "pip", "install", "--quiet", "--user",
                 "curl_cffi"], timeout=240, check=False)
        from curl_cffi import requests as _cr
    except Exception:                           # pragma: no cover
        _cr = None
_IMPERSONATE = "chrome"

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
    if _cr is not None:
        return _cr.Session()
    s = requests.Session()
    s.headers.update(UA)
    return s


def _do_get(session, url, timeout):
    if _cr is not None:
        return session.get(url, timeout=timeout, impersonate=_IMPERSONATE,
                           allow_redirects=True)
    return session.get(url, timeout=timeout, headers=UA)


def _get(session, url, timeout=25, tries=3):
    for k in range(tries):
        if _expired():
            return None
        try:
            r = _do_get(session, url, timeout)
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


# ------------------------------------------------------------------ date helpers
_RSS_MONTH = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
_RSS_DATE = re.compile(r"(\d{1,2})\s+([A-Z][a-z]{2})[a-z]*\s+(20\d\d)")


def _rss_field(block, name):
    m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (name, name), block, re.S | re.I)
    if not m:
        return None
    v = re.sub(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", m.group(1).strip(), flags=re.S).strip()
    return _html.unescape(v)


def _rss_date(s):
    m = _RSS_DATE.search(s or "")
    if m:
        return f"{m.group(3)}-{_RSS_MONTH[m.group(2)]:02d}-{int(m.group(1)):02d}"
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


NFC_FEED = "https://feeds.newsfilecorp.com/industry"
NFC_FEEDS = ["mining-metals", "precious-metals", "non-ferrous-metals",
             "energy-metals", "rare-earths", "diamonds", "energy"]
_RSS_ITEM = re.compile(r"<item\b[^>]*>(.*?)</item>", re.S | re.I)


def _nfc_from_rss(xml):
    out = []
    for block in _RSS_ITEM.findall(xml or ""):
        link = _rss_field(block, "link") or ""
        m = re.search(r"/release/(\d+)/([A-Za-z0-9\-\.]+)", link)
        if not m:
            continue
        out.append({"source": "newsfilecorp", "id": m.group(1),
                    "url": f"{NFC}/release/{m.group(1)}/{m.group(2)}",
                    "title": _rss_field(block, "title"),
                    "published": _rss_date(_rss_field(block, "pubDate")), "company": None})
    return out


def nfc_rss(session):
    rel = {}
    for slug in NFC_FEEDS:
        xml = _get(session, f"{NFC_FEED}/{slug}")
        for r in _nfc_from_rss(xml):
            rel[r["id"]] = r
        time.sleep(0.3)
    return list(rel.values())


def nfc_incremental(session):
    rel = {}
    for r in nfc_rss(session):
        rel[r["id"]] = r
    for cat in NFC_CATS:
        html = _get(session, f"{NFC}/news/{cat}")
        for r in _nfc_from_html(html):
            rel.setdefault(r["id"], r)
        time.sleep(0.4)
    return list(rel.values())


def nfc_sitemap(session):
    """Recent release URLs (~5000) from the news sitemap — the backfill pool."""
    html = _get(session, f"{NFC}/sitemap-news.php")
    rows = _nfc_from_html(html)
    return [r for r in rows if DRILL_KW.search(r["url"])]


def nfc_idwalk(session, span=1500):
    """Deterministic backfill: walk the sequential release-id space downward from
    the newest id seen across the RSS feeds. Orchestrator skips banked ids."""
    newest = None
    for r in nfc_rss(session):
        try:
            newest = max(newest or 0, int(r["id"]))
        except Exception:
            pass
    if not newest:
        return []
    return [{"source": "newsfilecorp", "id": str(rid),
             "url": f"{NFC}/release/{rid}", "title": None,
             "published": None, "company": None}
            for rid in range(newest, max(1, newest - span), -1)]


def _nfc_backfill(session):
    rel = {}
    for r in nfc_sitemap(session):
        rel[r["id"]] = r
    for r in nfc_idwalk(session):
        rel.setdefault(r["id"], r)
    return list(rel.values())


# ------------------------------------------------------ Google News decode layer
# Every non-newsfile wire blocks scraping of its own listings (JS/anti-bot), but
# Google News indexes them all. We query per-wire with a mining filter, then
# decode Google's opaque /rss/articles/<id> link back to the real wire URL via
# the DotsSplashUi batchexecute endpoint (curl_cffi carries the Chrome fingerprint
# both hops). Native URL in hand, the extractor fetches the real release.
GN = "https://news.google.com"
_MINE_Q = ('(drill OR intercept OR intersect OR assay OR "g/t" OR mineraliz OR '
           '"grams per tonne" OR "diamond drilling" OR "drill results" OR '
           '"drill program" OR "drill hole" OR "step-out")')


def _gnews_search(session, query, when=None, after=None, before=None):
    q = query
    if when:
        q += f" when:{when}"
    if after:
        q += f" after:{after}"
    if before:
        q += f" before:{before}"
    url = f"{GN}/rss/search?q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en"
    xml = _get(session, url, timeout=25)
    items = []
    for block in re.findall(r"<item>(.*?)</item>", xml or "", re.S):
        link = re.search(r"<link>(.*?)</link>", block)
        if not link:
            continue
        items.append({"gurl": link.group(1),
                      "title": _rss_field(block, "title"),
                      "published": _rss_date(_rss_field(block, "pubDate"))})
    return items


def _gnews_decode(session, gurl):
    """Resolve a news.google.com/rss/articles/<id> link to the real article URL."""
    if _cr is None:
        return None
    try:
        html = _get(session, gurl, timeout=20)
        if not html:
            return None
        sig = re.search(r'data-n-a-sg="([^"]+)"', html)
        ts = re.search(r'data-n-a-ts="([^"]+)"', html)
        if not (sig and ts):
            return None
        aid = gurl.split("/articles/")[1].split("?")[0]
        inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en",
                 None, 1, None, None, None, None, None, 0, 1], "X", "X", 1,
                 [1, 1, 1], 1, 1, None, 0, 0, None, 0], aid, ts.group(1), sig.group(1)]
        payload = [[["Fbv4je", json.dumps(inner), None, "generic"]]]
        body = "f.req=" + urllib.parse.quote(json.dumps(payload))
        r = session.post(f"{GN}/_/DotsSplashUi/data/batchexecute", data=body,
                         impersonate=_IMPERSONATE, timeout=25,
                         headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        tail = r.text.split("garturlres")[-1] if "garturlres" in r.text else r.text
        m = re.search(r'(https?://[^"\\]+)', tail)
        return m.group(1) if m else None
    except Exception:
        return None


def _gnews_wire(session, domain, source, windows, per_window=100):
    """Discover a wire's drill releases via Google News across `windows` (each a
    when:/after:before: spec), decoding to native URLs. Bounded by the crawl
    deadline; the orchestrator dedupes/skips banked releases."""
    out, seen = [], set()
    host = domain.split(".")[0]
    for w in windows:
        if _expired():
            break
        kwargs = {}
        if isinstance(w, tuple):
            kwargs["after"], kwargs["before"] = w
        else:
            kwargs["when"] = w
        for it in _gnews_search(session, f"{_MINE_Q} site:{domain}", **kwargs)[:per_window]:
            if _expired():
                break
            if it["title"] and not DRILL_KW.search(it["title"]):
                continue
            real = _gnews_decode(session, it["gurl"])
            if not real or host not in real:
                continue
            real = real.split("?")[0].split("#")[0]
            if real in seen:
                continue
            seen.add(real)
            rid = source + "-" + re.sub(r"\W+", "", real)[-28:]
            out.append({"source": source, "id": rid, "url": real,
                        "title": it["title"], "published": it["published"], "company": None})
    return out


def _month_windows(months):
    """(after,before) date pairs marching back `months` months from today."""
    import datetime
    wins, today = [], datetime.date.today()
    y, m = today.year, today.month
    end = today
    for _ in range(months):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        start = datetime.date(y, m, 1)
        wins.append((start.isoformat(), end.isoformat()))
        end = start
    return wins


def _wire_adapter(domain, source):
    def incremental(session):
        return _gnews_wire(session, domain, source, windows=["30d"])

    def backfill(session):
        return _gnews_wire(session, domain, source, windows=_month_windows(20), per_window=100)
    return incremental, backfill


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


# ------------------------------------------------------ juniorminingnetwork
# JMN aggregates EVERY major wire and re-hosts full release text with readable
# collar/assay tables. curl_cffi clears its Cloudflare from CI too, so it stays
# on as a source-agnostic safety net over the native per-wire adapters below.
JMN = "https://www.juniorminingnetwork.com"
JMN_TOPICS = ["drill-results"]
_JMN_REL = re.compile(r'href="(/junior-miner-news/press-releases/[^"]+?\.html)"')
_JMN_ANCHOR = re.compile(
    r'href="(/junior-miner-news/press-releases/[^"]+?\.html)"[^>]*>\s*([^<]{8,200})')


def _jmn_from_html(html):
    """(url, title) for every release link on a JMN listing/topic page."""
    out, seen = [], set()
    for href, title in _JMN_ANCHOR.findall(html or ""):
        if href in seen:
            continue
        seen.add(href)
        out.append((href, re.sub(r"\s+", " ", _html.unescape(title)).strip()))
    for href in _JMN_REL.findall(html or ""):
        if href not in seen:
            seen.add(href)
            out.append((href, ""))
    return out


def jmn_incremental(session):
    rel = {}
    for topic in JMN_TOPICS:
        html = _get(session, f"{JMN}/mining-topics/topic/{topic}.html")
        for href, title in _jmn_from_html(html):
            if title and not DRILL_KW.search(title):
                continue
            url = href if href.startswith("http") else JMN + href
            rid = href.rsplit("/", 1)[-1][:60]
            rel[rid] = {"source": "juniorminingnetwork", "id": rid, "url": url,
                        "title": title or None, "published": None, "company": None}
        time.sleep(0.5)
    return list(rel.values())


# --------------------------------------------------------------------- adapters
def _empty(_session):
    return []


# Native per-wire adapters. newsfile from its own RSS + id-walk; the other five
# wires via Google News discovery -> decoded native URLs -> curl_cffi fetch.
_gnw_inc, _gnw_bf = _wire_adapter("globenewswire.com", "globenewswire")
_bw_inc, _bw_bf = _wire_adapter("businesswire.com", "businesswire")
_acw_inc, _acw_bf = _wire_adapter("accessnewswire.com", "accesswire")
_cnw_inc, _cnw_bf = _wire_adapter("newswire.ca", "cision")
_tnw_g_inc, _tnw_g_bf = _wire_adapter("thenewswire.com", "thenewswire")


def tnw_combined(session):
    rel = {}
    for r in tnw_incremental(session):
        rel[r["url"]] = r
    for r in _tnw_g_inc(session):
        rel.setdefault(r["url"], r)
    return list(rel.values())


ADAPTERS = {
    "newsfilecorp": {"incremental": nfc_incremental, "backfill": _nfc_backfill},
    "globenewswire": {"incremental": _gnw_inc, "backfill": _gnw_bf},
    "businesswire": {"incremental": _bw_inc, "backfill": _bw_bf},
    "accesswire": {"incremental": _acw_inc, "backfill": _acw_bf},   # ACCESS Newswire
    "cision": {"incremental": _cnw_inc, "backfill": _cnw_bf},       # newswire.ca / CNW
    "thenewswire": {"incremental": tnw_combined, "backfill": _tnw_g_bf},
    # JMN aggregator kept as a source-agnostic safety net (now curl_cffi-reachable)
    "juniorminingnetwork": {"incremental": jmn_incremental, "backfill": _empty},
}


def fetch_release(session, url):
    return _get(session, url, timeout=60)
