"""Shared helpers for the daily digest (radar page + email JSON).

The drop tracker emits ONE row per lapsed claim/cell. But an explorer dropping a
property lets go of many contiguous cells at once, so the raw rows read as dozens
of meaningless "small parcels". group_dropped() coalesces them back into the
PROPERTY that was actually dropped — same holder, same cluster — and every item
carries a map deep-link so it can be looked at, which is the whole point."""
import math


def _num(v, d=0.0):
    try:
        f = float(v)
        return f if f == f else d
    except (TypeError, ValueError):
        return d


def map_url(slug, lat, lon, zoom=12, label=None, kind=None):
    """Deep-link into the unified Explore map, centred on a point."""
    if lat is None or lon is None:
        return ""
    u = f"app.html?lat={round(_num(lat),5)}&lon={round(_num(lon),5)}&z={zoom}&region={slug}"
    if label:
        from urllib.parse import quote
        u += f"&label={quote(str(label)[:60])}"
    if kind:
        u += f"&kind={kind}"
    return u


def _dkm(a_lat, a_lon, b_lat, b_lon):
    dlat = (a_lat - b_lat) * 111.0
    dlon = (a_lon - b_lon) * 111.0 * math.cos(math.radians((a_lat + b_lat) / 2))
    return math.hypot(dlat, dlon)


def group_dropped(rows, slug, cluster_km=3.0):
    """Coalesce per-claim drop rows into property-level events.

    Rows share a property when they have the SAME prior holder and sit in the same
    cluster (within cluster_km of the group so far). Claims with no holder on
    record (e.g. Ontario cells) are clustered on the nearest-lead they hug plus
    proximity, so a block dropped around one showing becomes one event."""
    out = []
    # stable order so clustering is deterministic: by owner, then lead, then space
    rows = [r for r in rows if r.get("lat") is not None and r.get("lon") is not None]
    rows = sorted(rows, key=lambda r: (str(r.get("owner", "")), int(r.get("near_rank", 0) or 0),
                                       _num(r.get("lat")), _num(r.get("lon"))))
    groups = []
    for r in rows:
        owner = (r.get("owner") or "").strip()
        lat, lon = _num(r.get("lat")), _num(r.get("lon"))
        placed = False
        for g in groups:
            same_holder = (owner == g["owner"])
            # unowned cells (no holder) group by shared nearest-lead instead
            same_anchor = same_holder if owner else (int(r.get("near_rank", 0) or 0) == g["near_rank"])
            if same_anchor and _dkm(lat, lon, g["clat"], g["clon"]) <= cluster_km:
                g["rows"].append(r)
                n = len(g["rows"])
                g["clat"] += (lat - g["clat"]) / n
                g["clon"] += (lon - g["clon"]) / n
                placed = True
                break
        if not placed:
            groups.append({"owner": owner, "near_rank": int(r.get("near_rank", 0) or 0),
                           "clat": lat, "clon": lon, "rows": [r]})
    for g in groups:
        rs = g["rows"]
        area = round(sum(_num(x.get("area_ha")) for x in rs), 1)
        near_km = round(min(_num(x.get("near_km"), 1e9) for x in rs), 1)
        near_lead = next((x.get("near_lead") for x in rs if x.get("near_lead")), "")
        near_rank = g["near_rank"]
        goods = [x.get("good_to") for x in rs if x.get("good_to")]
        ids = [x.get("id") for x in rs if x.get("id")]
        out.append({
            "owner": g["owner"], "n_claims": len(rs), "area_ha": area,
            "near_lead": near_lead, "near_rank": near_rank, "near_km": near_km,
            "lat": round(g["clat"], 5), "lon": round(g["clon"], 5),
            "good_to": (min(goods) if goods else ""),
            "ids": ids[:12], "more_ids": max(0, len(ids) - 12),
            "map_url": map_url(slug, g["clat"], g["clon"], zoom=12,
                               label=(g["owner"] or near_lead or "Dropped ground"), kind="drop"),
        })
    # biggest, closest properties first
    out.sort(key=lambda x: (x["near_km"], -x["area_ha"]))
    return out


# ---------------------------------------------------------------------------
# build_top(): the SHORT ranked digest for the email. The email must be a
# skimmable teaser (a handful of one-line movements + a link to the radar for
# the rest), never a per-region dump of every lead. We pre-compose the one-line
# strings HERE so the email step can't balloon the format again.

def _abbr(name):
    m = {"British Columbia": "BC", "Ontario": "ON", "Quebec": "QC", "Yukon": "YK",
         "Newfoundland & Labrador": "NL", "Saskatchewan": "SK", "Manitoba": "MB",
         "Northwest Territories": "NT", "Nova Scotia": "NS", "New Brunswick": "NB",
         "Alberta": "AB", "Nunavut": "NU", "Prince Edward Island": "PE"}
    return m.get(name, (name or "")[:2].upper())


def build_top(regions, n_edges=6, n_drop=8, n_leads=8, site=""):
    """Collapse every region's signals into one short, ranked set for the email."""
    edges, drops, leads = [], [], []
    for r in regions:
        juris = _abbr(r.get("name", r.get("slug", "")))
        for e in r.get("edges", []):
            edges.append((juris, e))
        for d in r.get("dropped_properties", []):
            drops.append((juris, d))
        for l in r.get("leads", []):
            leads.append((juris, l))
    edges.sort(key=lambda x: (0 if x[1].get("hot") else 1, -_num(x[1].get("open_ha"))))
    drops.sort(key=lambda x: (-_num(x[1].get("area_ha")), _num(x[1].get("near_km"), 1e9)))
    leads.sort(key=lambda x: -_num(x[1].get("score")))

    def _abs(u):
        return (site + u) if (u and site and not u.startswith("http")) else u

    def edge_line(j, e):
        comp = e.get("company") or e.get("property") or "Drill play"
        prop = e.get("property")
        oh = e.get("open_ha")
        bit = f" · {round(_num(oh))} ha open{(' to the '+e.get('open_dir')) if e.get('open_dir') else ''}" if oh else ""
        return {"juris": j, "hot": bool(e.get("hot")),
                "text": f"{comp}{(' — '+prop) if prop else ''}{bit}",
                "map_url": _abs(e.get("map_url"))}

    def drop_line(j, d):
        owner = (d.get("owner") or "A holder").replace(" 100%", "").strip()
        lead = d.get("near_lead") or "open ground"
        n = int(d.get("n_claims", 1) or 1)
        area = _num(d.get("area_ha"))
        km = d.get("near_km")
        size = f"{n} claim{'s' if n!=1 else ''}" + (f", ~{round(area)} ha" if area >= 1 else "")
        prox = f" · {km} km from #{d.get('near_rank')}" if km is not None else ""
        return {"juris": j,
                "text": f"{owner} dropped its {lead} block · {size}{prox}",
                "map_url": _abs(d.get("map_url"))}

    def lead_line(j, l):
        return {"juris": j,
                "text": f"#{l.get('rank')} {l.get('name')} · {l.get('metal')} · score {int(_num(l.get('score')))}",
                "url": l.get("url") or "", "map_url": _abs(l.get("map_url"))}

    return {
        "counts": {"edges": len(edges), "dropped": len(drops), "leads": len(leads)},
        "edges": [edge_line(j, e) for j, e in edges[:n_edges]],
        "dropped": [drop_line(j, d) for j, d in drops[:n_drop]],
        "leads": [lead_line(j, l) for j, l in leads[:n_leads]],
    }
