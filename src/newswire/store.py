"""Shared drill-and-assay data bank (SQLite) for two projects:

  * Project Closeology drill radar — geolocated recent drill results drive
    open-ground plays.
  * MineModelingPro — the full banked collar + assay record (worldwide, all
    commodities) for deposit modelling.

One file, committed under data/keep/ so it survives CI rebuilds and accumulates.
Everything keyed by a stable release id (hash of the canonical URL) so re-runs
are idempotent (INSERT OR REPLACE).

Tables
  releases   one row per press release seen (status ok|empty|error + reason —
             'empty'/'error' rows ARE the failure log the user asked for)
  holes      one row per drill hole (collar): coords, azimuth, dip, depth
  intervals  one row per assay interval per element (hole, from/to/len, grade)
"""
import os
import re
import time
import sqlite3
import hashlib

# repo-root-absolute (…/src/newswire/store.py -> repo root) so the bank lands in
# the same data/keep whether run from the repo root (CI) or from src/
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(_ROOT, "data", "keep", "drillbank.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
  id TEXT PRIMARY KEY,            -- sha1 of canonical url
  source TEXT,                   -- newsfilecorp | cision | thenewswire | globenewswire | accesswire | businesswire
  url TEXT,
  title TEXT,
  company TEXT,
  ticker TEXT,
  published TEXT,                -- ISO date
  lang TEXT,
  status TEXT,                   -- ok | empty | error
  reason TEXT,                   -- why empty/error (for the failure log)
  n_holes INTEGER DEFAULT 0,
  n_intervals INTEGER DEFAULT 0,
  utm_zone INTEGER, utm_hemi TEXT, datum TEXT,
  project TEXT,
  country TEXT,
  fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS holes (
  id TEXT PRIMARY KEY,           -- release_id + ':' + hole_id
  release_id TEXT,
  hole_id TEXT,
  project TEXT,
  easting REAL, northing REAL, utm_zone INTEGER, utm_hemi TEXT, datum TEXT,
  lat REAL, lon REAL,
  elev_m REAL, azimuth REAL, dip REAL, depth_m REAL,
  FOREIGN KEY(release_id) REFERENCES releases(id)
);
CREATE TABLE IF NOT EXISTS intervals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_id TEXT,
  hole_id TEXT,
  is_subinterval INTEGER DEFAULT 0,  -- 1 for "including" rows
  from_m REAL, to_m REAL, length_m REAL,
  element TEXT, grade REAL, unit TEXT,
  raw TEXT,
  FOREIGN KEY(release_id) REFERENCES releases(id)
);
CREATE INDEX IF NOT EXISTS ix_holes_rel ON holes(release_id);
CREATE INDEX IF NOT EXISTS ix_int_rel ON intervals(release_id);
CREATE INDEX IF NOT EXISTS ix_int_hole ON intervals(hole_id);
CREATE INDEX IF NOT EXISTS ix_rel_status ON releases(status);
CREATE INDEX IF NOT EXISTS ix_rel_pub ON releases(published);
CREATE INDEX IF NOT EXISTS ix_holes_ll ON holes(lat, lon);
"""


def rel_id(url):
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:16]


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def seen(con, rid):
    """True only if this release was already SUCCESSFULLY processed (ok) or
    definitively parsed-but-empty. 'error' rows (transient fetch/throttle) are
    NOT considered seen, so they get retried on the next run."""
    row = con.execute("SELECT status FROM releases WHERE id=?", (rid,)).fetchone()
    return bool(row) and row[0] in ("ok", "empty")


def record_release(con, rec):
    """rec: dict with keys matching release columns (id required)."""
    cols = ["id", "source", "url", "title", "company", "ticker", "published", "lang",
            "status", "reason", "n_holes", "n_intervals", "utm_zone", "utm_hemi",
            "datum", "project", "country", "fetched_at"]
    rec.setdefault("fetched_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    vals = [rec.get(c) for c in cols]
    con.execute(f"INSERT OR REPLACE INTO releases ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", vals)


def replace_holes(con, release_id, holes):
    con.execute("DELETE FROM holes WHERE release_id=?", (release_id,))
    for h in holes:
        hid = f"{release_id}:{h.get('hole_id')}"
        con.execute("""INSERT OR REPLACE INTO holes
            (id,release_id,hole_id,project,easting,northing,utm_zone,utm_hemi,datum,lat,lon,elev_m,azimuth,dip,depth_m)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (hid, release_id, h.get("hole_id"), h.get("project"), h.get("easting"),
                     h.get("northing"), h.get("utm_zone"), h.get("utm_hemi"), h.get("datum"),
                     h.get("lat"), h.get("lon"), h.get("elev_m"), h.get("azimuth"),
                     h.get("dip"), h.get("depth_m")))


def replace_intervals(con, release_id, intervals):
    con.execute("DELETE FROM intervals WHERE release_id=?", (release_id,))
    for iv in intervals:
        con.execute("""INSERT INTO intervals
            (release_id,hole_id,is_subinterval,from_m,to_m,length_m,element,grade,unit,raw)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (release_id, iv.get("hole_id"), 1 if iv.get("is_subinterval") else 0,
                     iv.get("from_m"), iv.get("to_m"), iv.get("length_m"),
                     iv.get("element"), iv.get("grade"), iv.get("unit"), iv.get("raw")))


def stats(con):
    q = con.execute
    return {
        "releases": q("SELECT COUNT(*) FROM releases").fetchone()[0],
        "ok": q("SELECT COUNT(*) FROM releases WHERE status='ok'").fetchone()[0],
        "empty": q("SELECT COUNT(*) FROM releases WHERE status='empty'").fetchone()[0],
        "error": q("SELECT COUNT(*) FROM releases WHERE status='error'").fetchone()[0],
        "holes": q("SELECT COUNT(*) FROM holes").fetchone()[0],
        "holes_geo": q("SELECT COUNT(*) FROM holes WHERE lat IS NOT NULL").fetchone()[0],
        "intervals": q("SELECT COUNT(*) FROM intervals").fetchone()[0],
        "by_source": dict(q("SELECT source, COUNT(*) FROM releases GROUP BY source").fetchall()),
    }
