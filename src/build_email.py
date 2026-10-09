"""Deterministic daily-email renderer. Produces (subject, html) from the digest
dict so the scheduled email task fetches a ready-to-send body instead of composing
HTML itself (which was fragile and leaked raw markup)."""
import html as _html
import os


def _esc(v):
    return _html.escape(str(v if v is not None else ""))


def _chip(j):
    return (f'<span style="display:inline-block;font:600 11px/1 -apple-system,BlinkMacSystemFont,\'Segoe UI\','
            f'Roboto,Helvetica,Arial,sans-serif;color:#1e293b;background:#eef2f7;border:1px solid #dce3ec;'
            f'border-radius:4px;padding:3px 6px;letter-spacing:.04em;vertical-align:middle;margin-right:8px;">'
            f'{_esc(j)}</span>')


def _maplink(u):
    if not u:
        return ""
    return (f'<a href="{_esc(u)}" style="color:#2563eb;text-decoration:none;white-space:nowrap;'
            f'margin-left:6px;">\U0001f5fa map</a>')


def _row(item, hot_ok=True):
    j = item.get("juris", "")
    hot = " \U0001f525" if (hot_ok and item.get("hot")) else ""
    txt = _esc(item.get("text", ""))
    return (f'<tr><td style="padding:7px 0;border-bottom:1px solid #f1f5f9;font:400 14px/1.45 '
            f'-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;color:#334155;">'
            f'{_chip(j)}{txt}{hot}{_maplink(item.get("map_url"))}</td></tr>')


def _section(title, items, empty_msg=None, hot_ok=True):
    rows = "".join(_row(it, hot_ok) for it in (items or []))
    if not rows:
        if not empty_msg:
            return ""
        rows = (f'<tr><td style="padding:7px 0;font:400 13px/1.45 -apple-system,sans-serif;color:#94a3b8;">'
                f'{_esc(empty_msg)}</td></tr>')
    return (f'<tr><td style="padding:22px 0 4px 0;font:700 13px/1.3 -apple-system,BlinkMacSystemFont,'
            f'\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;letter-spacing:.01em;">{title}</td></tr>'
            f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>')


def render(d):
    gen = d.get("generated", "")
    site = d.get("site", "https://jaydeepdive.github.io/closeology/")
    top = d.get("top", {}) or {}
    watch = d.get("watch", {}) or {}
    edges = top.get("edges", []) or []
    tdrop = top.get("dropped", []) or []
    leads = top.get("leads", []) or []
    wexp = watch.get("expiring", {}) or {}
    wdrp = watch.get("dropped", {}) or {}
    n_edges = top.get("counts", {}).get("edges", len(edges))
    n_wdrop = wdrp.get("n", 0)
    n_wexp = wexp.get("n", 0)
    watch_url = watch.get("watch_url") or (site + "watch.html")
    radar = site + "radar.html"

    if watch:
        subject = f"Closeology — {n_edges} plays · {n_wdrop} just dropped · {n_wexp} expiring ({gen})"
    else:
        subject = (f"Closeology — {n_edges} plays · {top.get('counts',{}).get('dropped',0)} dropped "
                   f"· {top.get('counts',{}).get('leads',0)} leads ({gen})")

    secs = []
    secs.append(_section("\U0001f525 Act now — fresh drilling by open ground", edges,
                         empty_msg="No fresh drilling on an open boundary today."))
    if watch:
        dmsg = None if wdrp.get("items") else (
            f"No drops recorded yet — tracking since {wdrp.get('tracking_since', gen)}, complete after ~30 days.")
        secs.append(_section(f"\U0001f513 Just dropped — claims that lapsed (last 30 days, {n_wdrop})",
                             wdrp.get("items", []), empty_msg=dmsg, hot_ok=False))
        if wexp.get("items"):
            secs.append(_section(f"⏳ Expiring this week ({n_wexp})", wexp.get("items", [])))
    if tdrop:
        secs.append(_section("⚑ Properties just opened", tdrop, hot_ok=False))
    if leads:
        secs.append(_section("⭐ Top leads with activity nearby", leads))

    body_rows = "".join(secs)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8fafc;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:100%;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;">
<tr><td style="padding:28px 28px 24px 28px;">
  <div style="font:700 17px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">Closeology <span style="color:#94a3b8;font-weight:600;">· {_esc(gen)}</span></div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="margin:18px 0 10px 0;"><tr><td style="border-radius:8px;background:#0f172a;">
    <a href="{_esc(radar)}" style="display:inline-block;padding:12px 22px;font:700 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#ffffff;text-decoration:none;border-radius:8px;">\U0001f6f0 Open the full radar →</a>
  </td></tr></table>
  <div style="font:400 13px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#64748b;">The movements worth a look today — full detail and maps on the radar.</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{body_rows}</table>
  <div style="margin-top:22px;padding-top:14px;border-top:1px solid #eef2f7;font:400 12px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#94a3b8;">
    Full detail on the <a href="{_esc(radar)}" style="color:#64748b;">radar</a> and the <a href="{_esc(watch_url)}" style="color:#64748b;">claim watch</a>. Verify every claim in the official title system before staking.<br>
    <a href="{_esc(site)}" style="color:#94a3b8;">closeology</a> · <a href="{_esc(site)}drill_radar.html" style="color:#94a3b8;">drill bank</a>
  </div>
</td></tr></table>
</td></tr></table>
</body></html>"""
    return subject, html


def build(email_dict, site_dir="site"):
    subject, html = render(email_dict)
    email_dict["email_subject"] = subject
    email_dict["email_ready"] = True
    try:
        os.makedirs(site_dir, exist_ok=True)
        open(os.path.join(site_dir, "daily_email.html"), "w").write(html)
    except Exception as e:
        print("[build_email] write failed:", str(e)[:100])
    return subject, html
