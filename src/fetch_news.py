"""Pull recent drill-result news releases and write per-region news_items.json,
which news.py then geolocates into fresh, assay-bearing edge plays.

This runs inside the daily GitHub Action (its runner can reach the open web),
not in the fenced Claude session. It is CONFIG-DRIVEN and safe to run with no
config: with no sources it simply writes/keeps nothing and the pipeline falls
back to the government drill layers.

Sources come from (first found wins):
  • env NEWS_SOURCES        - comma-separated URLs
  • data/keep/news_sources.json  - {"sources":[{"url":"...","type":"auto|json|rss|wp"}]}

MiningNewsTerminal is the intended primary source once its feed/API URL is known
(add it to news_sources.json). Each source may be:
  - "wp"   WordPress REST  (…/wp-json/wp/v2/posts?per_page=50)
  - "json" generic JSON array of posts
  - "rss"  RSS/Atom XML
  - "auto" sniff by content-type / body
"""
import os
import re
import json
import time
import html
import datetime
import xml.etree.ElementTree as ET

try:
    import requests
except Exception:
    requests = None

RECENT_DAYS = 120
MAX_ITEMS = 60
UA = {"User-Agent": "closeology-news/1.0"}

_ASSAY = re.compile(
    r"\d+(?:\.\d+)?\s*m\s*(?:@|of|grading)?\s*\d+(?:\.\d+)?\s*(?:g/?t|gpt|%|ppm|oz/?t|opt)\s*[A-Za-z]{0,3}"
    r"|\d+(?:\.\d+)?\s*(?:g/?t|gpt|%|ppm|oz/?t|opt)\s*[A-Za-z]{0,3}\s*over\s*\d+(?:\.\d+)?\s*m", re.I)
_VERB = re.compile(r"\b(announces?|reports?|intersects?|intercepts?|drills?|hits?|extends?|"
                   r"discovers?|provides?|completes?|expands?|returns?|files?|confirms?)\b", re.I)
_PROJ = re.compile(r"\b(?:at|on|from)\s+(?:its\s+|the\s+)?([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,3})", )


def _sources():
    env = os.environ.get("NEWS_SOURCES", "").strip()
    if env:
        return [{"url": u.strip(), "type": "auto"} for u in env.split(",") if u.strip()]
    p = os.path.join("data", "keep", "news_sources.json")
    if os.path.exists(p):
        try:
            return json.load(open(p)).get("sources", [])
        except Exception:
            return []
    return []


def _get(url):
    if requests is None:
        return None
    for a in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            return r
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


def _strip(t):
    return html.unescape(re.sub(r"<[^>]+>", " ", str(t or ""))).strip()


def _date(s):
    s = str(s or "")[:25]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(s.replace("Z", "").strip()[:len(datetime.datetime.now().strftime(fmt))], fmt).date()
        except Exception:
            continue
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        try:
            return datetime.date.fromisoformat(m.group(0))
        except Exception:
            pass
    return None


def _province(text):
    t = text.lower()
    on = "ontario" in t
    bc = "british columbia" in t or re.search(r"\bb\.?c\.?\b", t) is not None
    if on and not bc:
        return ["on"]
    if bc and not on:
        return ["bc"]
    if on and bc:
        return ["on", "bc"]
    return ["on", "bc"]   # unknown -> try both; geolocation decides


def _company(title):
    m = _VERB.search(title)
    c = title[:m.start()].strip(" -–:") if m else title.split(" at ")[0]
    return c[:80]


def _project(title):
    m = _PROJ.search(title)
    return (m.group(1).strip() if m else "")[:60]


def _normalize(title, summary, date, url):
    title, summary = _strip(title), _strip(summary)
    blob = title + " . " + summary
    am = _ASSAY.search(blob)
    highlight = am.group(0).strip() if am else (summary[:120] if summary else title[:120])
    return {"date": (date.isoformat() if date else ""), "company": _company(title),
            "project": _project(title), "location": "", "highlight": highlight,
            "url": url, "_title": title, "_blob": blob}


def _parse_wp(r):
    out = []
    for p in r.json():
        out.append(_normalize(p.get("title", {}).get("rendered", ""),
                               p.get("excerpt", {}).get("rendered", "") or p.get("content", {}).get("rendered", ""),
                               _date(p.get("date")), p.get("link", "")))
    return out


def _parse_json(r):
    d = r.json()
    posts = d if isinstance(d, list) else (d.get("items") or d.get("posts") or d.get("data") or [])
    out = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        title = p.get("title") or p.get("headline") or ""
        if isinstance(title, dict):
            title = title.get("rendered", "")
        out.append(_normalize(title, p.get("summary") or p.get("excerpt") or p.get("description") or "",
                               _date(p.get("date") or p.get("published") or p.get("pubDate")),
                               p.get("url") or p.get("link") or ""))
    return out


def _parse_rss(r):
    out = []
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return out
    for it in root.iter():
        tag = it.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        g = {c.tag.split("}")[-1].lower(): c for c in it}
        title = g.get("title").text if g.get("title") is not None else ""
        summ = (g.get("description") or g.get("summary"))
        summ = summ.text if summ is not None else ""
        link = ""
        if g.get("link") is not None:
            link = g["link"].get("href") or g["link"].text or ""
        date = _date((g.get("pubdate") or g.get("published") or g.get("updated")).text) if (g.get("pubdate") or g.get("published") or g.get("updated")) is not None else None
        out.append(_normalize(title, summ, date, link))
    return out


def _fetch_source(src):
    r = _get(src["url"])
    if r is None:
        return []
    t = (src.get("type") or "auto").lower()
    ct = r.headers.get("content-type", "").lower()
    try:
        if t == "wp" or ("wp-json" in src["url"] and t == "auto"):
            return _parse_wp(r)
        if t == "json" or ("json" in ct and t == "auto"):
            return _parse_json(r)
        if t == "rss" or ("xml" in ct or r.text.lstrip()[:5].lower() in ("<?xml", "<rss")):
            return _parse_rss(r)
        # last resort: try json then rss
        try:
            return _parse_json(r)
        except Exception:
            return _parse_rss(r)
    except Exception as e:
        print("  [news] parse failed for", src["url"], "->", str(e)[:60])
        return []


def run(region_dirs=("data/bc", "data/on")):
    srcs = _sources()
    if not srcs:
        print("[fetch_news] no sources configured — skipping (set data/keep/news_sources.json or NEWS_SOURCES)")
        return
    today = datetime.date.today()
    all_items = []
    for s in srcs:
        got = _fetch_source(s)
        print(f"[fetch_news] {s['url']} -> {len(got)} items")
        all_items += got
    # de-dupe + recency filter
    seen, items = set(), []
    for it in sorted(all_items, key=lambda x: x["date"], reverse=True):
        key = (it["company"].lower(), it["_title"].lower()[:60])
        if key in seen:
            continue
        seen.add(key)
        d = _date(it["date"])
        if d and (today - d).days > RECENT_DAYS:
            continue
        items.append(it)
    # route by province
    slug = {"data/bc": "bc", "data/on": "on"}
    buckets = {rd: [] for rd in region_dirs}
    for it in items:
        provs = _province(it["_blob"])
        for rd in region_dirs:
            if slug.get(rd) in provs:
                buckets[rd].append({k: it[k] for k in ("date", "company", "project", "location", "highlight", "url")})
    for rd in region_dirs:
        os.makedirs(rd, exist_ok=True)
        b = buckets[rd][:MAX_ITEMS]
        json.dump({"items": b}, open(os.path.join(rd, "news_items.json"), "w"))
        print(f"[fetch_news] {rd}/news_items.json <- {len(b)} items")


if __name__ == "__main__":
    run()
