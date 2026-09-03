"""Shared Deep Dive-style theme for the Closeology site (sister site to
thedeepdive.ca): white ground, black text, Deep Dive red accent (#D71920),
Bitter headings + Roboto body, and a matching top nav. The Deep Dive wordmark
is embedded, with 'PROJECT CLOSEOLOGY' beneath it."""
import os

_here = os.path.dirname(__file__)
try:
    LOGO_URI = open(os.path.join(_here, "..", "assets", "logo_datauri.txt")).read().strip()
except Exception:
    LOGO_URI = ""

RED = "#D71920"

NAV = [("Priority leads", "index.html"), ("Regions & maps", "regions.html"),
       ("Explore map", "app.html"), ("Drill radar", "drill_radar.html")]

# daily radars live in a nav hover-dropdown (one page per jurisdiction), listed
# alphabetically; the dropdown label itself links to the cross-Canada overview.
RADARS = sorted([
    ("Alberta", "daily_ab.html"), ("British Columbia", "daily_bc.html"),
    ("Manitoba", "daily_mb.html"), ("New Brunswick", "daily_nb.html"),
    ("Newfoundland & Labrador", "daily_nl.html"), ("Northwest Territories", "daily_nt.html"),
    ("Nova Scotia", "daily_ns.html"), ("Nunavut", "daily_nu.html"), ("Ontario", "daily_on.html"),
    ("Quebec", "daily_qc.html"), ("Saskatchewan", "daily_sk.html"),
    ("Yukon", "daily_yk.html")], key=lambda x: x[0])

THEME_CSS = """
:root{ --red:#D71920; --ink:#111418; --mut:#636363; --line:#e6e8eb; --panel:#f5f7fa; --bg:#ffffff; --chip:#EDF2F7; }
*{box-sizing:border-box;}
html,body{margin:0;background:var(--bg);color:var(--ink);font-family:'Roboto',-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:15px;line-height:1.55;}
h1,h2,h3,h4{font-family:'Bitter',Georgia,serif;font-weight:700;color:var(--ink);margin:0;}
a{color:var(--red);text-decoration:none;} a:hover{text-decoration:underline;}
.topbar{border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:50;}
.topwrap{max-width:1180px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;padding:12px 22px;gap:20px;flex-wrap:wrap;}
.brand{display:flex;flex-direction:column;gap:3px;text-decoration:none;}
.brand img{height:30px;width:auto;display:block;}
.brand .sub{font-family:'Bitter',serif;font-weight:700;font-size:11px;letter-spacing:2.5px;color:var(--ink);text-transform:uppercase;}
nav.menu{display:flex;align-items:center;gap:22px;flex-wrap:wrap;}
nav.menu a,nav.menu .dd>a.ddlabel{display:inline-flex;align-items:center;height:32px;color:var(--ink);font-size:14px;font-weight:500;line-height:1;border-bottom:2px solid transparent;text-decoration:none;}
nav.menu a:hover{color:var(--red);text-decoration:none;}
nav.menu a.active{color:var(--red);border-bottom-color:var(--red);}
nav.menu .dd{position:relative;display:inline-flex;align-items:center;}
nav.menu .dd>a.ddlabel::after{content:"";display:inline-block;width:0;height:0;margin-left:6px;border-left:4px solid transparent;border-right:4px solid transparent;border-top:5px solid var(--mut);}
nav.menu .dd:hover>a.ddlabel::after{border-top-color:var(--red);}
nav.menu .dd:hover>a.ddlabel,nav.menu .dd>a.ddlabel.active{color:var(--red);}
nav.menu .dd-menu{display:none;position:absolute;top:100%;left:0;padding-top:8px;min-width:230px;z-index:60;}
nav.menu .dd:hover .dd-menu{display:block;}
nav.menu .dd-menu .inner{background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 18px rgba(0,0,0,.10);padding:6px 0;}
nav.menu .dd-menu a{display:block;padding:7px 15px;font-size:13.5px;color:var(--ink);border:0;white-space:nowrap;}
nav.menu .dd-menu a:hover{background:var(--panel);color:var(--red);text-decoration:none;}
.wrap{max-width:1180px;margin:0 auto;padding:26px 22px 60px;}
.hero h1{font-size:30px;letter-spacing:-.3px;} .hero p{color:var(--mut);max-width:760px;}
.rule{height:3px;width:52px;background:var(--red);margin:10px 0 0;border-radius:2px;}
footer.site{border-top:1px solid var(--line);color:var(--mut);font-size:12px;line-height:1.6;padding:22px;max-width:1180px;margin:0 auto;}
footer.site b{color:var(--ink);}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Bitter:wght@500;700;800&'
         'family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">')


def header(active=""):
    items = "".join(
        f'<a href="{href}" class="{"active" if href==active else ""}">{label}</a>'
        for label, href in NAV)
    radars = "".join(f'<a href="{href}">{label}</a>' for label, href in RADARS)
    on = " active" if active in ["radar.html"] + [h for _, h in RADARS] else ""
    dd = (f'<span class="dd"><a class="ddlabel{on}" href="radar.html">Daily radars</a>'
          f'<div class="dd-menu"><div class="inner">{radars}</div></div></span>')
    logo = f'<img src="{LOGO_URI}" alt="the deep dive">' if LOGO_URI else '<span style="font-family:Bitter;font-weight:800;font-size:20px">thedeepdive<span style="color:#D71920">.ca</span></span>'
    return f"""<div class="topbar"><div class="topwrap">
  <a class="brand" href="index.html">{logo}<span class="sub">Project Closeology</span></a>
  <nav class="menu">{items}{dd}</nav>
</div></div>"""


def footer():
    return ('<footer class="site"><b>Project Closeology</b> — a sister project to '
            '<a href="https://thedeepdive.ca" target="_blank">The Deep Dive</a>. '
            'Screening signals over official data (BC MTO/MINFILE, Ontario LIO/MDI); '
            'metal values are approximate and refreshed daily. Always confirm every claim '
            'and boundary in the official title system before staking — this is decision '
            'support, not staking advice.</footer>')
