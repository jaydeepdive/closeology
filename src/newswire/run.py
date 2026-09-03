"""Newswire drill-data orchestrator: crawl -> fetch -> extract -> geolocate ->
bank. Idempotent (skips releases already in the bank) and resumable, so a
5-year backfill accumulates across runs and the daily CI run just adds the day.

Usage:
  python -m newswire.run incremental              # daily: recent mining releases
  python -m newswire.run backfill --limit 400     # deeper history, resumable
  python -m newswire.run stats
"""
import re
import sys
import time
import html as _html

from newswire import store, sources, extract, geolocate

_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|"
                   r"October|November|December)\s+(\d{1,2}),\s+(20\d\d)")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}
_COUNTRY = re.compile(r"\b(Canada|Ontario|Quebec|Qu\xe9bec|British Columbia|Yukon|Nunavut|"
                      r"Newfoundland|Labrador|Saskatchewan|Manitoba|Nova Scotia|New Brunswick|"
                      r"Northwest Territories|Alberta|Nevada|Arizona|Australia|Mexico|Peru|"
                      r"Chile|Brazil|Argentina|Ghana|Finland|Sweden|USA|United States|Idaho|"
                      r"Alaska|Colorado|Montana|Utah|North Carolina)\b")


def _title(html):
    m = re.search(r"<title>(.*?)</title>", html or "", re.S | re.I)
    return re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip()[:300] if m else None


def _pub(html):
    m = _DATE.search(html or "")
    if m:
        return f"{m.group(3)}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    return None


def _company(title):
    if not title:
        return None
    t = re.split(r"\b(Reports|Announces|Intersects|Drills|Provides|Completes|Closes|"
                 r"Identifies|Extends|Discovers|Files|Commences|Intercepts|Expands)\b", title)[0]
    return t.strip(" -:|")[:120] or None


def _country(text):
    ms = _COUNTRY.findall(text or "")
    if not ms:
        return None
    # the jurisdiction mentioned most often (drop the generic "Canada"/country
    # roll-ups if a specific province/state is named as often)
    from collections import Counter
    cnt = Counter(ms)
    generic = {"Canada", "USA", "United States"}
    specific = {k: v for k, v in cnt.items() if k not in generic}
    pool = specific or cnt
    return max(pool, key=lambda k: (pool[k], k not in generic))


def _process(session, con, desc):
    rid = store.rel_id(desc["url"])
    html = sources.fetch_release(session, desc["url"])
    base = {"id": rid, "source": desc["source"], "url": desc["url"],
            "published": desc.get("published"), "company": desc.get("company"),
            "ticker": None, "lang": "en", "project": None, "country": None,
            "utm_zone": None, "utm_hemi": None, "datum": None,
            "n_holes": 0, "n_intervals": 0}
    if not html:
        base.update(status="error", reason="fetch failed")
        store.record_release(con, base)
        return "error"
    title = _title(html) or desc.get("title")
    base["title"] = title
    base["company"] = desc.get("company") or _company(title)
    base["published"] = desc.get("published") or _pub(html)
    try:
        holes, intervals, meta = extract.extract(html)
    except Exception as e:
        base.update(status="error", reason=f"extract:{str(e)[:80]}")
        store.record_release(con, base)
        return "error"
    region = _country(extract._clean(html)[:6000])
    base["country"] = region
    base["utm_zone"], base["utm_hemi"], base["datum"] = meta["utm_zone"], meta["utm_hemi"], meta["datum"]
    if holes:
        geolocate.locate_holes(holes, meta["utm_zone"], meta["utm_hemi"], meta["datum"], region=region)
    # once holes are placed, label the release by where the drilling actually is
    # (coordinates), not the company's HQ address picked up from the boilerplate
    geo_regions = [geolocate.region_from_latlon(h.get("lat"), h.get("lon"))
                   for h in holes if h.get("lat") is not None]
    geo_regions = [g for g in geo_regions if g]
    if geo_regions:
        from collections import Counter
        base["country"] = Counter(geo_regions).most_common(1)[0][0]
    base["n_holes"] = len(holes)
    base["n_intervals"] = len(intervals)
    if holes or intervals:
        base["status"] = "ok"
        base["reason"] = None
        store.record_release(con, base)
        store.replace_holes(con, rid, holes)
        store.replace_intervals(con, rid, intervals)
        return "ok"
    # nothing parsed — this is the failure log the user asked for
    base["status"] = "empty"
    base["reason"] = "no tables/prose parsed" + ("" if meta["has_tables"] else "; no tables in page")
    store.record_release(con, base)
    return "empty"


def run(mode="incremental", limit=400, only=None, max_seconds=None):
    con = store.connect()
    session = sources.new_session()
    counts = {"ok": 0, "empty": 0, "error": 0, "skipped": 0}
    if max_seconds is None:
        import os as _os
        max_seconds = int(_os.environ.get("NEWSWIRE_MAX_SECONDS", "240"))
    sources.set_deadline(max_seconds)   # hard cap on listings AND fetches
    t0 = time.time()
    stop = False
    for name, adapter in sources.ADAPTERS.items():
        if stop or (only and name not in only):
            continue
        fn = adapter.get(mode)
        if not fn:
            continue
        try:
            descs = fn(session)
        except Exception as e:
            print(f"[newswire] {name} {mode} listing failed: {str(e)[:100]}")
            continue
        print(f"[newswire] {name}: {len(descs)} candidate releases")
        for d in descs:
            if counts["ok"] + counts["empty"] + counts["error"] >= limit:
                break
            if time.time() - t0 > max_seconds:
                print(f"[newswire] time budget ({max_seconds}s) reached — stopping cleanly")
                stop = True
                break
            if store.seen(con, store.rel_id(d["url"])):
                counts["skipped"] += 1
                continue
            r = _process(session, con, d)
            counts[r] += 1
            con.commit()
            time.sleep(1.6)   # be polite (avoid the wire's throttle/202)
    con.commit()
    s = store.stats(con)
    print(f"[newswire] {mode} done in {int(time.time()-t0)}s: "
          f"+{counts['ok']} ok, {counts['empty']} empty, {counts['error']} err, "
          f"{counts['skipped']} already-banked")
    print(f"[newswire] bank now: {s['releases']} releases ({s['ok']} ok / {s['empty']} empty / "
          f"{s['error']} err), {s['holes']} holes ({s['holes_geo']} geolocated), {s['intervals']} intervals")
    con.close()
    return counts


if __name__ == "__main__":
    args = sys.argv[1:]
    mode = args[0] if args and not args[0].startswith("-") else "incremental"
    limit = 400
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    if mode == "stats":
        con = store.connect()
        import json
        print(json.dumps(store.stats(con), indent=2))
    else:
        run(mode, limit)
