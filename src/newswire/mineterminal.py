"""MineTerminalPro drill feed -> Closeology drill bank.

miningnewsterminal.com exposes a public, no-auth JSON API of parsed drill-result
releases. We use it as a high-recall DISCOVERY feed and a body-text source: for
each release we pull the article body and run Closeology's own extractor +
geolocator on it, so hole/interval IDs stay consistent for the 3D models. MTP's
own pre-parsed intervals are used as a FALLBACK when our extractor finds none.

Guards for the quirks the API owner documented:
  * published_at is UTC but serialized without 'Z' -> we key on the `date` field.
  * /api/v1/drills honours `since` (day-granular); /api/v1/news/recent ignores it.
  * no coordinates exist anywhere in MTP -> geolocation is entirely ours.
  * some MTP grades are extraction errors -> plausibility filter on the fallback.
  * key everything on event_id / source_url; de-duplicate against the bank.

Run:
  PYTHONPATH=src python -m newswire.mineterminal incremental
  PYTHONPATH=src python -m newswire.mineterminal backfill --since 2024-09-17
"""
import sys
import json
import time
import datetime
from newswire import store, extract, sources, ingest_json

API = "https://miningnewsterminal.com"


def _get_json(session, url):
    txt = sources._get(session, url)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def _drills_page(session, page, since, limit=200):
    q = f"{API}/api/v1/drills?limit={limit}&page={page}&include=intervals&since={since}"
    return _get_json(session, q) or {}


def _article_body(session, event_id):
    d = _get_json(session, f"{API}/api/v1/news/article/{event_id}")
    if not d or not d.get("ok"):
        return None
    return (d.get("article") or {}).get("body_html")


_BAD_PROJECT = {"wgs84", "nad83", "nad27", "pfs-level", "n/a", "tbd", "none"}


def _clean_project(p):
    if not p:
        return None
    s = str(p).strip()
    if len(s) < 2 or s.lower() in _BAD_PROJECT:
        return None
    return s


def _plausible(iv):
    """Drop obvious extraction errors before they poison a grade model."""
    g = iv.get("grade")
    if g is None:
        return True
    try:
        g = float(g)
    except Exception:
        return False
    u = (iv.get("unit") or "").lower()
    if u.startswith("g/t") and g > 3000:     # e.g. a misread 4270 g/t Au
        return False
    if u.startswith("%") and g > 50:         # e.g. a misread 35% Cu
        return False
    return True


def _map_mtp_intervals(mtp):
    out = []
    for iv in (mtp or []):
        row = {"hole_id": iv.get("hole_id"), "from_m": iv.get("from_m"),
               "to_m": iv.get("to_m"), "length_m": iv.get("length_m"),
               "element": iv.get("metal"), "grade": iv.get("grade"),
               "unit": iv.get("unit"), "is_subinterval": 1 if iv.get("including") else 0,
               "raw": iv.get("summary")}
        if _plausible(row):
            out.append(row)
    return out


def _pub_date(item):
    d = item.get("date")
    try:
        y = int((d or "")[:4])
        if 2000 <= y <= datetime.date.today().year + 1:
            return d
    except Exception:
        pass
    return None


def collect(mode="incremental", since=None, limit=None, max_seconds=None):
    import os
    if max_seconds is None:
        max_seconds = int(os.environ.get("NEWSWIRE_MAX_SECONDS", "240"))
    if limit is None:
        limit = 5000 if mode == "backfill" else 400
    if not since:
        back = 730 if mode == "backfill" else 3   # 3-day window absorbs late/re-approved releases
        since = (datetime.date.today() - datetime.timedelta(days=back)).isoformat()
    session = sources.new_session()
    sources.set_deadline(max_seconds)
    con = store.connect()
    releases, new, skipped = [], 0, 0
    page, pages, t0 = 1, 1, time.time()
    while page <= pages:
        if sources._expired() or (time.time() - t0) > max_seconds:
            print("[mtp] time budget reached — stopping"); break
        d = _drills_page(session, page, since)
        if not d.get("ok"):
            print(f"[mtp] page {page} not ok — stopping"); break
        pages = d.get("pages", page)
        items = d.get("items", [])
        if not items:
            break
        for it in items:
            if new >= limit:
                break
            url = it.get("source_url") or it.get("release_url")
            if not url or store.seen(con, store.rel_id(url)):
                skipped += 1 if url else 0
                continue
            body = _article_body(session, it.get("event_id"))
            time.sleep(1.1)   # stay well under 60 req/min
            if not body:
                continue
            try:
                holes, iv_html, meta = extract.extract(body)
            except Exception:
                holes, iv_html, meta = [], [], {"utm_zone": None, "utm_hemi": None, "datum": None}
            ivs = iv_html if iv_html else _map_mtp_intervals(it.get("intervals"))
            releases.append({
                "url": url, "source": "miningnewsterminal",
                "company": it.get("company"), "ticker": it.get("ticker"),
                "project": _clean_project(it.get("project")),
                "title": it.get("headline"), "published": _pub_date(it),
                "utm_zone": meta.get("utm_zone"), "utm_hemi": meta.get("utm_hemi"),
                "datum": meta.get("datum"), "country": None,
                "holes": holes, "intervals": ivs})
            new += 1
        page += 1
    con.close()
    print(f"[mtp] {mode} since {since}: {new} new releases fetched, {skipped} already banked")
    if releases:
        ingest_json.ingest(releases)
    else:
        print("[mtp] nothing new to ingest")
    return {"new": new, "skipped": skipped}


if __name__ == "__main__":
    a = sys.argv[1:]
    mode = a[0] if a and not a[0].startswith("-") else "incremental"
    kw = {}
    for i, x in enumerate(a):
        if x == "--since" and i + 1 < len(a): kw["since"] = a[i + 1]
        if x == "--limit" and i + 1 < len(a): kw["limit"] = int(a[i + 1])
        if x == "--max-seconds" and i + 1 < len(a): kw["max_seconds"] = int(a[i + 1])
    collect(mode, **kw)
