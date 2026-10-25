#!/usr/bin/env python3
"""Closeology daily email sender — runs inside GitHub Actions.

Reads the already-built digest from the checked-out repo (no network fetch) and
sends it via SMTP. Sends NOTHING on any gate failure (missing/stale/malformed) —
silence is the correct outcome when the build didn't produce fresh data.

Requires env var SMTP_PASS (a Gmail App Password), provided by the workflow from
an encrypted repo secret. The password never appears in this file or the repo.
"""
import os, sys, json, ssl, smtplib, html as _html
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYLOAD = os.path.join(ROOT, "site", "daily_email_payload.json")
HTMLFILE = os.path.join(ROOT, "site", "daily_email.html")

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
SMTP_USER = "jordan.douglas.lutz@gmail.com"
FROM_NAME, FROM_EMAIL = "Closeology", "jordan.douglas.lutz@gmail.com"
TO = ["jay@thedeepdive.ca"]
FRESH_DAYS = 2
PAGES = "https://jaydeepdive.github.io/closeology/"

LEAK = ["<" + "parameter", "function" + "_calls", "</" + "invoke", "tool" + "_use", "ant" + "ml:"]


def _esc(v):
    return _html.escape(str(v if v is not None else ""))


def _build(d):
    gen = d.get("generated", ""); site = d.get("site", PAGES)
    radar = d.get("radar_url", site + "radar.html"); watch = d.get("watch_url", site + "watch.html")
    secs = []
    for s in d.get("sections", []):
        lines = ""
        if s.get("items"):
            for it in s["items"]:
                hot = " \U0001f525" if it.get("hot") else ""
                txt = _esc(it.get("text", ""))
                if it.get("url"):
                    txt = f'<a href="{_esc(it["url"])}">{txt}</a>'
                m = (f' <a class="m" href="{_esc(it.get("map_url",""))}">\U0001f5fa map</a>' if it.get("map_url") else "")
                lines += f'<div class="it"><span class="c">{_esc(it.get("juris",""))}</span>{txt}{hot}{m}</div>'
        elif s.get("empty"):
            lines = f'<div class="em">{_esc(s["empty"])}</div>'
        else:
            continue
        secs.append(f'<div class="h">{_esc(s.get("title",""))}</div>{lines}')
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;background:#f8fafc;font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:#334155;}}
.wrap{{max-width:600px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:28px;}}
.top{{font-weight:700;font-size:17px;color:#0f172a;}}.top span{{color:#94a3b8;font-weight:600;}}
.btn{{display:inline-block;margin:18px 0 10px;padding:12px 22px;background:#0f172a;color:#fff !important;text-decoration:none;border-radius:8px;font-weight:700;font-size:14px;}}
.intro{{font-size:13px;color:#64748b;}}.h{{margin:22px 0 4px;font-weight:700;font-size:13px;color:#0f172a;}}
.it{{padding:7px 0;border-bottom:1px solid #f1f5f9;font-size:14px;line-height:1.45;}}
.c{{display:inline-block;font-weight:600;font-size:11px;color:#1e293b;background:#eef2f7;border:1px solid #dce3ec;border-radius:4px;padding:3px 6px;margin-right:8px;}}
.em{{padding:7px 0;font-size:13px;color:#94a3b8;}}.m{{color:#2563eb;text-decoration:none;white-space:nowrap;}}a{{color:#0f172a;}}
.ft{{margin-top:22px;padding-top:14px;border-top:1px solid #eef2f7;font-size:12px;color:#94a3b8;}}.ft a{{color:#64748b;}}
</style></head><body><div style="padding:24px 16px;"><div class="wrap">
<div class="top">Closeology <span>· {_esc(gen)}</span></div>
<a class="btn" href="{_esc(radar)}">\U0001f6f0 Open the full radar →</a>
<div class="intro">{_esc(d.get("intro",""))}</div>
{''.join(secs)}
<div class="ft">Full detail on the <a href="{_esc(radar)}">radar</a> and the <a href="{_esc(watch)}">claim watch</a>. Verify every claim in the official title system before staking.<br><a href="{_esc(site)}">closeology</a></div>
</div></div></body></html>"""


def main():
    if not os.path.exists(PAYLOAD):
        print("NOSEND: payload file missing"); return 0
    try:
        d = json.load(open(PAYLOAD))
    except Exception as e:
        print("NOSEND: payload unreadable:", repr(e)[:100]); return 0
    if not (d.get("email_ready") is True and d.get("sections") and d.get("email_subject")):
        print("NOSEND: payload not ready/complete"); return 0
    gen = d.get("generated", "")
    try:
        gd = datetime.strptime(gen, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        print("NOSEND: unparseable generated date", repr(gen)); return 0
    age = (datetime.now(timezone.utc) - gd).days
    if age > FRESH_DAYS or age < 0:
        print(f"NOSEND: stale data (generated {gen}, {age}d old)"); return 0
    subject = d["email_subject"]
    body = None
    if os.path.exists(HTMLFILE):
        h = open(HTMLFILE, encoding="utf-8").read()
        if "<" in h and "html" in h.lower() and gen in h:
            body = h
    if not body:
        body = _build(d)
    hit = [t for t in LEAK if t in body]
    if hit:
        print("NOSEND: leak token(s) in body:", hit); return 0
    pw = os.environ.get("SMTP_PASS", "")
    if not pw:
        print("NOSEND: SMTP_PASS secret not set"); return 0
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = ", ".join(TO)
    msg.attach(MIMEText("Open the Closeology radar: " + PAGES + "radar.html", "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=60) as s:
        s.login(SMTP_USER, pw)
        s.sendmail(FROM_EMAIL, TO, msg.as_string())
    print(f"SENT to {', '.join(TO)} | subject: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
