"""Daily radar overview (radar.html): the cross-Canada digest of everything the
daily scan FLAGGED today — fresh drill plays against open ground, ground that just
opened (dropped claims), and leads with claim activity nearby — ranked and linked.
Built from the same per-region payload that feeds the email digest."""
import os
import json
import site_theme as T

PILL = {"BC": "#e8f0fe:#1a56db", "ON": "#fdeaea:#c81e1e", "YK": "#eef7ee:#2f7d32",
        "NL": "#e6f4f6:#0e7490", "SK": "#f3eefe:#6d28d9", "MB": "#fef3e2:#b45309",
        "QC": "#fce7f3:#be185d", "AB": "#e0f2fe:#0369a1", "NT": "#e9f7f3:#0f766e",
        "NB": "#fdf0e6:#9a3412", "NS": "#eef2ff:#3730a3"}


def _pill(slug):
    bg, fg = PILL.get(slug.upper(), "#eef0f2:#444").split(":")
    return f'<span class="jp" style="background:{bg};color:{fg}">{slug.upper()}</span>'


def _s(v):
    return "" if v is None else str(v)


def build(email, site_dir):
    regions = email.get("regions", [])
    edges, dropped, flagged = [], [], []
    for r in regions:
        slug = r["slug"]
        for e in r.get("edges", []):
            p = e.get("properties", e)
            edges.append((slug, p))
        for d in r.get("dropped", []):
            dropped.append((slug, d))
        for l in r.get("leads", []):
            flagged.append((slug, l))
    edges.sort(key=lambda x: (0 if x[1].get("hot") else 1, -_num(x[1].get("open_ha"))))
    dropped.sort(key=lambda x: _num(x[1].get("near_km"), 1e9))
    flagged.sort(key=lambda x: -_num(x[1].get("score")))

    # ---- fresh drill plays ----
    edge_rows = ""
    for slug, p in edges[:60]:
        assay = _s(p.get("assay")) or _s(p.get("commodity"))
        comp = _s(p.get("company")) or _s(p.get("property")) or "Drill play"
        src = _s(p.get("source"))
        surl = _s(p.get("source_url")) or _s(p.get("url"))
        srclink = f'<a href="{surl}" target=_blank>{src or "source"} ↗</a>' if surl else src
        openha = p.get("open_ha")
        hot = "🔥 " if p.get("hot") else ""
        edge_rows += f"""<div class="item{' hot' if p.get('hot') else ''}">
          <div class="ihead">{_pill(slug)}<b>{hot}{comp}</b></div>
          <div class="imeta">{assay}{(' · near '+_s(p.get('near_lead'))) if p.get('near_lead') else ''}
            {(' · '+str(round(_num(openha)))+' ha open beside it') if openha else ''}</div>
          <div class="isrc">{srclink}</div></div>"""
    edge_sec = _section("Fresh drill plays", "Recent drilling on held ground beside open, stakeable cells — the reason to act now.", edge_rows, len(edges))

    # ---- ground just opened ----
    drop_rows = ""
    for slug, d in dropped[:60]:
        drop_rows += f"""<div class="item">
          <div class="ihead">{_pill(slug)}<b>{_s(d.get('near_lead')) or 'Open ground'}</b></div>
          <div class="imeta">Claim {_s(d.get('id'))} lapsed{(' · '+str(d.get('area_ha'))+' ha') if d.get('area_ha') else ''}
            {(' · '+str(d.get('near_km'))+' km from the lead') if d.get('near_km') is not None else ''}</div>
          <div class="isrc">{('Prior holder: '+_s(d.get('owner'))) if d.get('owner') else ''}</div></div>"""
    drop_sec = _section("Ground just opened", "Claims that lapsed next to a lead since the last scan — freshly stakeable.", drop_rows, len(dropped))

    # ---- leads flagged by claim activity ----
    flag_rows = ""
    for slug, l in flagged[:80]:
        why = []
        if l.get("near_a"):
            why.append("beside ground lapsing soon")
        if l.get("near_b"):
            why.append("beside fresh staking")
        flag_rows += f"""<div class="item">
          <div class="ihead">{_pill(slug)}<b>{_s(l.get('name'))}</b>
            <span class="sc">score {int(_num(l.get('score')))}</span></div>
          <div class="imeta">{_s(l.get('metal'))} · {_s(l.get('status'))}{(' · '+_s(l.get('grade'))) if l.get('grade') else ''}</div>
          <div class="isrc">{' · '.join(why)}{(' · '+_s(l.get('community'))+' '+str(l.get('community_km'))+' km') if l.get('community') else ''}
            {(' · <a href="'+_s(l.get('url'))+'" target=_blank>record ↗</a>') if l.get('url') else ''}</div></div>"""
    flag_sec = _section("Leads flagged by claim activity", "Existing leads where a claim is lapsing or fresh staking just landed nearby.", flag_rows, len(flagged))

    total = len(edges) + len(dropped) + len(flagged)
    if total == 0:
        body = ('<div class="empty">Nothing new was flagged in today\'s scan — no fresh drill '
                'plays, newly-open ground or claim activity beside a lead. The full ranked list of '
                'standing opportunities is on the <a href="index.html">Priority leads</a> page.</div>')
    else:
        body = edge_sec + drop_sec + flag_sec

    html = PAGE.format(fonts=T.FONTS, css=T.THEME_CSS, header=T.header("radar.html"),
                       footer=T.footer(), generated=email.get("generated", ""),
                       n_edges=len(edges), n_drop=len(dropped), n_flag=len(flagged),
                       n_regions=len(regions), body=body)
    open(os.path.join(site_dir, "radar.html"), "w").write(html)
    print(f"[radar] radar.html — {len(edges)} plays, {len(dropped)} newly-open, {len(flagged)} flagged leads")


def _num(v, d=0.0):
    try:
        f = float(v)
        return f if f == f else d
    except (TypeError, ValueError):
        return d


def _section(title, sub, rows, n):
    if not rows:
        return ""
    return f'<div class="sec"><h2>{title} <span class="n">{n}</span></h2><p class="ss">{sub}</p>{rows}</div>'


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Daily radar · Project Closeology</title>
{fonts}
<style>{css}
.summary{{display:flex;gap:26px;flex-wrap:wrap;margin:16px 0 6px;}}
.summary .kv{{}} .summary b{{font-family:'Bitter',serif;font-size:24px;display:block;}}
.summary span{{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.5px;}}
.sec{{margin:26px 0;}} .sec h2{{font-size:19px;}} .sec .n{{color:var(--mut);font-size:13px;font-weight:500;}}
.sec .ss{{color:var(--mut);font-size:13px;margin:2px 0 12px;}}
.item{{border:1px solid var(--line);border-left:3px solid var(--line);border-radius:8px;padding:10px 14px;margin:8px 0;background:#fff;}}
.item.hot{{border-left-color:var(--red);background:#fffafa;}}
.ihead{{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;font-size:15px;}}
.ihead b{{font-weight:700;}}
.imeta{{color:#374151;font-size:13px;margin-top:3px;}} .isrc{{color:var(--mut);font-size:12px;margin-top:3px;}}
.jp{{font-size:9.5px;font-weight:700;padding:1px 7px;border-radius:10px;}}
.sc{{color:var(--red);font-weight:700;font-size:12px;}}
.empty{{border:1px solid var(--line);border-radius:10px;padding:26px;color:var(--mut);text-align:center;margin-top:20px;}}
</style></head><body>
{header}
<div class="wrap">
  <div class="hero"><h1>Daily radar</h1><div class="rule"></div>
    <p>What the scan flagged across every jurisdiction today — the movements worth acting on now.
       Updated {generated}.</p></div>
  <div class="summary">
    <div class="kv"><b>{n_edges}</b><span>Fresh drill plays</span></div>
    <div class="kv"><b>{n_drop}</b><span>Newly-open ground</span></div>
    <div class="kv"><b>{n_flag}</b><span>Flagged leads</span></div>
    <div class="kv"><b>{n_regions}</b><span>Jurisdictions scanned</span></div>
  </div>
  {body}
</div>
{footer}
</body></html>
"""
