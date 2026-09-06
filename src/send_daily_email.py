"""Send the daily Closeology digest email straight from the build.

Runs inside GitHub Actions right after build_all writes site/daily_email.json, so
there is no browser fetch and no Cowork approval prompt to get stuck on. Reads the
pre-computed `top` object and emails a short, skimmable HTML digest via Gmail SMTP.

Secrets (GitHub -> Settings -> Secrets and variables -> Actions):
  GMAIL_USER          the sending Gmail address
  GMAIL_APP_PASSWORD  a Google App Password (needs 2FA on that account)
  EMAIL_TO            optional; defaults to jay@thedeepdive.ca

If the secrets are absent the script exits 0 without sending, so the build never
fails just because email isn't configured yet.
"""
import os
import re
import sys
import json
import smtplib
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGEST = os.path.join(ROOT, "site", "daily_email.json")
TO = os.environ.get("EMAIL_TO", "jay@thedeepdive.ca")
USER = os.environ.get("GMAIL_USER")
PW = os.environ.get("GMAIL_APP_PASSWORD")


def esc(s):
    return re.sub(r"[&<>]", lambda m: {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[m.group()], str(s or ""))


def chip(j):
    return (f'<span style="font:700 10px Arial;background:#eef1f4;color:#333;'
            f'padding:1px 6px;border-radius:10px;margin-right:6px">{esc(j)}</span>')


def line(it, link_text=False):
    j = chip(it.get("juris", ""))
    hot = " 🔥" if it.get("hot") else ""
    txt = esc(it.get("text", ""))
    if link_text and it.get("url"):
        txt = f'<a href="{esc(it["url"])}" style="color:#111;text-decoration:none">{txt}</a>'
    mp = (f' · <a href="{esc(it["map_url"])}" style="color:#D71920;text-decoration:none">🗺 map</a>'
          if it.get("map_url") else "")
    return f'<div style="margin:6px 0;font:14px Arial;color:#111">{j}{txt}{hot}{mp}</div>'


def section(title, items, link_text=False, empty=None):
    if not items and empty is None:
        return ""
    body = "".join(line(x, link_text) for x in items) if items else \
        f'<div style="margin:6px 0;font:14px Arial;color:#888">{empty}</div>'
    return (f'<div style="margin:18px 0 4px;font:700 13px Arial;color:#111">{title}</div>{body}')


def main():
    if not os.path.exists(DIGEST):
        print("[email] no daily_email.json — skipping"); return 0
    d = json.load(open(DIGEST))
    top = d.get("top")
    site = d.get("site", "https://jaydeepdive.github.io/closeology/")
    gen = d.get("generated", "")
    if not USER or not PW:
        print("[email] GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping send (build stays green)")
        return 0
    if not top:
        html = (f'<div style="font:14px Arial">Today\'s scan is published but the summary field '
                f'is missing — open the radar: <a href="{site}radar.html">{site}radar.html</a></div>')
        subj = f"Closeology — scan published ({gen})"
    else:
        c = top.get("counts", {})
        e, dr, le = c.get("edges", 0), c.get("dropped", 0), c.get("leads", 0)
        subj = f"Closeology — {e} plays · {dr} dropped · {le} leads ({gen})"
        html = (
            f'<div style="max-width:600px;margin:0 auto">'
            f'<div style="font:700 16px Arial;color:#111">Closeology · {esc(gen)}</div>'
            f'<div style="margin:12px 0"><a href="{site}radar.html" '
            f'style="display:inline-block;background:#D71920;color:#fff;font:700 14px Arial;'
            f'padding:10px 16px;border-radius:8px;text-decoration:none">🛰 Open the full radar →</a></div>'
            f'<div style="font:14px Arial;color:#444">The movements worth a look today — full detail and maps on the radar.</div>'
            + section("🔥 Act now — fresh drilling by open ground", top.get("edges", []),
                      empty="No fresh drilling on an open boundary today.")
            + section("⚑ Just opened — properties dropped", top.get("dropped", []))
            + section("⭐ Top leads with activity nearby", top.get("leads", []), link_text=True)
            + f'<div style="margin:20px 0 6px;font:12px Arial;color:#888">Full list — {e} edge plays · '
              f'{dr} dropped · {le} leads on the <a href="{site}radar.html" style="color:#888">radar</a>. '
              f'Verify every claim in the official title system before staking. '
              f'<a href="{site}" style="color:#888">site</a> · '
              f'<a href="{site}drill_radar.html" style="color:#888">drill radar</a></div></div>')

    msg = MIMEText(html, "html")
    msg["Subject"] = subj
    msg["From"] = USER
    msg["To"] = TO
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(USER, PW)
        s.sendmail(USER, [a.strip() for a in TO.split(",")], msg.as_string())
    print(f"[email] sent: {subj}  -> {TO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
