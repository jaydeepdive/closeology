"""Ingest LLM/WebFetch-extracted drill releases into the shared bank.

The wires rate-limit datacenter crawlers, so collection runs through WebFetch in a
Claude session (not throttled, and it reads collar + assay tables and prose
equally well). That session hands the extracted releases to this helper, which
geolocates the holes and writes them to data/keep/drillbank.sqlite — same schema,
same idempotency as the crawler path.

Input JSON (a list, or {"releases":[...]}), each release:
  {url, source?, company?, project?, published?(YYYY-MM-DD), country?,
   utm_zone?, utm_hemi?, datum?,
   holes:[{hole_id, easting?, northing?, elev_m?, azimuth?, dip?, depth_m?, lat?, lon?}],
   intervals:[{hole_id?, from_m?, to_m?, length_m?, element, grade, unit, is_subinterval?}]}
"""
import sys
import json
from newswire import store, geolocate


def _source_of(url):
    import re
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    host = (m.group(1).lower() if m else "")
    for key, name in (("newsfilecorp", "newsfilecorp"), ("thenewswire", "thenewswire"),
                      ("newswire.ca", "cision"), ("prnewswire", "prnewswire"),
                      ("globenewswire", "globenewswire"), ("accesswire", "accesswire"),
                      ("businesswire", "businesswire"), ("juniorminingnetwork", "juniorminingnetwork"),
                      ("miningnewsterminal", "miningnewsterminal"), ("stockwatch", "stockwatch"),
                      ("theglobeandmail", "globeandmail")):
        if key in host:
            return name
    return host or "other"


def ingest(releases):
    con = store.connect()
    n_ok = n_holes = n_iv = 0
    for r in releases:
        url = r.get("url")
        if not url:
            continue
        rid = store.rel_id(url)
        holes = r.get("holes") or []
        intervals = r.get("intervals") or []
        region = r.get("country")
        if holes:
            geolocate.locate_holes(holes, r.get("utm_zone"), r.get("utm_hemi"),
                                   r.get("datum"), region=region)
            geo = [geolocate.region_from_latlon(h.get("lat"), h.get("lon"))
                   for h in holes if h.get("lat") is not None]
            geo = [g for g in geo if g]
            if geo:
                from collections import Counter
                region = Counter(geo).most_common(1)[0][0]
        # normalise interval keys
        ivs = []
        for iv in intervals:
            ivs.append({"hole_id": iv.get("hole_id"), "from_m": iv.get("from_m"),
                        "to_m": iv.get("to_m"), "length_m": iv.get("length_m"),
                        "element": iv.get("element"), "grade": iv.get("grade"),
                        "unit": iv.get("unit") or "g/t", "is_subinterval": iv.get("is_subinterval"),
                        "raw": iv.get("raw", "")})
        status = "ok" if (holes or ivs) else "empty"
        store.record_release(con, {
            "id": rid, "source": r.get("source") or _source_of(url), "url": url,
            "title": r.get("title"), "company": r.get("company"), "ticker": r.get("ticker"),
            "published": r.get("published"), "lang": "en", "status": status,
            "reason": None if status == "ok" else "no data extracted",
            "n_holes": len(holes), "n_intervals": len(ivs),
            "utm_zone": r.get("utm_zone"), "utm_hemi": r.get("utm_hemi"),
            "datum": r.get("datum"), "project": r.get("project"), "country": region})
        if status == "ok":
            store.replace_holes(con, rid, holes)
            store.replace_intervals(con, rid, ivs)
            n_ok += 1; n_holes += len(holes); n_iv += len(ivs)
    con.commit()
    s = store.stats(con)
    con.close()
    print(f"[ingest] +{n_ok} releases, +{n_holes} holes, +{n_iv} intervals | bank now "
          f"{s['ok']} ok, {s['holes']} holes ({s['holes_geo']} geo), {s['intervals']} intervals")
    return s


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    ingest(data.get("releases", data) if isinstance(data, dict) else data)
