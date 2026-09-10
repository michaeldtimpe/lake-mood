"""SQLite storage. Append-only: nothing is ever pruned.

All timestamps are stored as UTC ISO-8601 strings, which sort lexically, so
range queries are plain string comparisons.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("lake_mood.db")

DEFAULT_DB_PATH = "/data/lake.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  ts           TEXT PRIMARY KEY,
  temp_f       REAL,
  dewpt_f      REAL,
  rh           REAL,
  wind_dir     INTEGER,
  wind_mph     REAL,
  gust_mph     REAL,
  pressure_mb  REAL,
  vis_mi       REAL,
  sky          TEXT,
  description  TEXT,
  raw          TEXT
);

CREATE TABLE IF NOT EXISTS forecasts (
  fetched_at   TEXT,
  start_ts     TEXT,
  temp_f       REAL,
  wind_mph     REAL,
  wind_dir     TEXT,
  pop          REAL,
  short        TEXT,
  PRIMARY KEY (fetched_at, start_ts)
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_forecasts_start ON forecasts(start_ts);
"""

OBS_COLUMNS = (
    "ts", "temp_f", "dewpt_f", "rh", "wind_dir", "wind_mph",
    "gust_mph", "pressure_mb", "vis_mi", "sky", "description",
)


def db_path() -> str:
    return os.environ.get("DB_PATH") or DEFAULT_DB_PATH


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open the DB, creating the parent directory if the NAS volume is fresh."""
    p = Path(path or db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------- writes

def insert_observations(conn, rows) -> int:
    """INSERT OR IGNORE so re-polls and overlapping backfills dedupe on ts."""
    payload = []
    for r in rows:
        if not r or not r.get("ts"):
            continue
        payload.append(
            tuple(r.get(c) for c in OBS_COLUMNS) + (json.dumps(r, default=str),)
        )
    if not payload:
        return 0
    cur = conn.executemany(
        "INSERT OR IGNORE INTO observations "
        "(ts, temp_f, dewpt_f, rh, wind_dir, wind_mph, gust_mph, pressure_mb,"
        " vis_mi, sky, description, raw) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        payload,
    )
    conn.commit()
    return cur.rowcount or 0


def insert_forecast(conn, rows, fetched_at: str | None = None) -> int:
    """Store a whole forecast snapshot; every snapshot is kept for history."""
    fetched_at = fetched_at or utc_now_iso()
    payload = [
        (fetched_at, r["start_ts"], r.get("temp_f"), r.get("wind_mph"),
         r.get("wind_dir"), r.get("pop"), r.get("short"))
        for r in rows if r and r.get("start_ts")
    ]
    if not payload:
        return 0
    cur = conn.executemany(
        "INSERT OR IGNORE INTO forecasts "
        "(fetched_at, start_ts, temp_f, wind_mph, wind_dir, pop, short) "
        "VALUES (?,?,?,?,?,?,?)",
        payload,
    )
    conn.commit()
    return cur.rowcount or 0


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


# ---------------------------------------------------------------------- reads

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def latest_observation(conn):
    row = conn.execute(
        "SELECT * FROM observations ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def observations_since(conn, hours: int):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return _rows(conn.execute(
        "SELECT * FROM observations WHERE ts >= ? ORDER BY ts ASC", (since,)
    ))


def observation_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM observations").fetchone()["n"]


def latest_forecast(conn, limit: int = 12):
    """The most recent snapshot, trimmed to periods that have not started yet."""
    row = conn.execute("SELECT MAX(fetched_at) AS f FROM forecasts").fetchone()
    if not row or not row["f"]:
        return []
    # Include the hour we are currently inside, not just future hours.
    hour_start = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ).isoformat()
    return _rows(conn.execute(
        "SELECT * FROM forecasts WHERE fetched_at = ? AND start_ts >= ? "
        "ORDER BY start_ts ASC LIMIT ?",
        (row["f"], hour_start, limit),
    ))
