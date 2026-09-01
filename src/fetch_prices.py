"""Refresh metal prices used by the value-based scoring.

Writes data/keep/metal_prices.json = {"updated": ISO-date, "prices_per_kg": {...},
"sources": {...}} which config.py reads (falling back to built-in defaults). Runs
in the daily GitHub Action, so scores track the market: a move in gold, silver,
antimony, gallium etc. flows straight into every lead's in-situ value.

Coverage by source (each is best-effort; a metal keeps its last value if its
source is unavailable, so a failed fetch never zeroes a price):
  • Precious (Au, Ag, Pt, Pd)  — gold-api.com, free, no key, daily.
  • Full basket incl. base + minor/critical (Cu, Zn, Pb, Ni, Co, Mo, Sn, Sb, Ga,
    Ge, In, W, Li, V, U, REE…) — metalpriceapi.com, if env METALPRICE_API_KEY is
    set (a free tier exists). Symbols map below.
  • Any custom feed — env PRICES_FEED_URL returning {"abbr": usd_per_kg, ...}.
Anything not refreshed stays at its previous (or default) value.
"""
import os
import json
import datetime

try:
    import requests
except Exception:
    requests = None

PRICE_FILE = os.path.join("data", "keep", "metal_prices.json")
OZT_PER_KG = 32.1507                       # troy ounces per kilogram
UA = {"User-Agent": "closeology-prices/1.0"}

# metalpriceapi symbols (LME/precious) -> our abbrev, with unit of the quote
# ("ozt" = USD per troy oz, "lb" = USD per pound, "mt" = USD per metric tonne)
_MPA = {
    "XAU": ("au", "ozt"), "XAG": ("ag", "ozt"), "XPT": ("pt", "ozt"), "XPD": ("pd", "ozt"),
    "LME-XCU": ("cu", "mt"), "LME-ZNC": ("zn", "mt"), "LME-LEAD": ("pb", "mt"),
    "LME-NI": ("ni", "mt"), "LME-TIN": ("sn", "mt"), "COBALT": ("co", "mt"),
    "MOLYBDENUM": ("mo", "mt"), "URANIUM": ("u", "lb"), "ANTIMONY": ("sb", "mt"),
    "GALLIUM": ("ga", "mt"), "GERMANIUM": ("ge", "mt"),
}


def _load():
    if os.path.exists(PRICE_FILE):
        try:
            return json.load(open(PRICE_FILE))
        except Exception:
            pass
    # seed from config defaults
    try:
        import config
        return {"updated": None, "prices_per_kg": dict(config.DEFAULT_PRICE_KG), "sources": {}}
    except Exception:
        return {"updated": None, "prices_per_kg": {}, "sources": {}}


def _get_json(url):
    if requests is None:
        return None
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _precious(prices, sources):
    for sym, ab in (("XAU", "au"), ("XAG", "ag"), ("XPT", "pt"), ("XPD", "pd")):
        d = _get_json(f"https://api.gold-api.com/price/{sym}")
        if d and isinstance(d.get("price"), (int, float)) and d["price"] > 0:
            prices[ab] = round(float(d["price"]) * OZT_PER_KG, 2)   # $/ozt -> $/kg
            sources[ab] = "gold-api.com"


def _metalpriceapi(prices, sources):
    key = os.environ.get("METALPRICE_API_KEY")
    if not key:
        return
    syms = ",".join(_MPA.keys())
    d = _get_json(f"https://api.metalpriceapi.com/v1/latest?api_key={key}&base=USD&currencies={syms}")
    if not d or not d.get("rates"):
        return
    rates = d["rates"]
    for sym, (ab, unit) in _MPA.items():
        # metalpriceapi returns USDXXX = price of 1 unit in USD under rates, or
        # XXX = units per USD; handle the USD-prefixed convenience keys.
        usd = rates.get("USD" + sym)
        if usd is None and sym in rates and rates[sym]:
            usd = 1.0 / rates[sym]
        if not usd or usd <= 0:
            continue
        if unit == "ozt":
            kg = usd * OZT_PER_KG
        elif unit == "lb":
            kg = usd * 2.2046226
        else:                                   # metric tonne
            kg = usd / 1000.0
        prices[ab] = round(kg, 3)
        sources[ab] = "metalpriceapi.com"


def _custom(prices, sources):
    url = os.environ.get("PRICES_FEED_URL")
    if not url:
        return
    d = _get_json(url)
    if isinstance(d, dict):
        feed = d.get("prices_per_kg", d)
        for k, v in (feed or {}).items():
            if isinstance(v, (int, float)) and v >= 0:
                prices[k.lower()] = float(v)
                sources[k.lower()] = "custom feed"


def run():
    state = _load()
    prices = state.get("prices_per_kg") or {}
    sources = state.get("sources") or {}
    _precious(prices, sources)
    _metalpriceapi(prices, sources)
    _custom(prices, sources)
    out = {"updated": datetime.date.today().isoformat(),
           "prices_per_kg": prices, "sources": sources}
    os.makedirs(os.path.dirname(PRICE_FILE), exist_ok=True)
    json.dump(out, open(PRICE_FILE, "w"), indent=2, sort_keys=True)
    refreshed = sum(1 for s in sources.values() if s)
    print(f"[fetch_prices] wrote {PRICE_FILE} · {len(prices)} metals, {refreshed} live-sourced, updated {out['updated']}")


if __name__ == "__main__":
    run()
