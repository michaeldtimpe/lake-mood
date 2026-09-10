"""lake-mood — is Mountain Creek Lake flat enough to kayak?

FastAPI app: one server-rendered page plus a few JSON endpoints. The lifespan
opens the SQLite connection and starts the background poller.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import db, nws
from .poller import Poller
from .verdict import kayak_verdict, sparkline, wind_dir_name

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("lake_mood")

HERE = Path(__file__).parent
STALE_MINUTES = 90
FORECAST_HOURS = 12
HISTORY_DAYS = 7

templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

state: dict = {"conn": None, "poller": None}


def local_tz():
    name = os.environ.get("TZ") or "America/Chicago"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown TZ %r; falling back to UTC", name)
        return timezone.utc


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.init(conn)
    state["conn"] = conn
    poller = Poller(conn)
    state["poller"] = poller
    poller.start()
    log.info("lake-mood up: db=%s obs=%ss fc=%ss ua=%r",
             db.db_path(), poller.obs_interval, poller.fc_interval, nws.user_agent())
    try:
        yield
    finally:
        await poller.stop()
        conn.close()


app = FastAPI(title="lake-mood", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


# ------------------------------------------------------------------- helpers

def _dt(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _local(ts, tz):
    d = _dt(ts)
    return d.astimezone(tz) if d else None


def fmt_clock(ts, tz):
    """'4:15 pm' — lowercase, no leading zero."""
    d = _local(ts, tz)
    return d.strftime("%I:%M %p").lstrip("0").lower() if d else "—"


def fmt_hour(ts, tz):
    """'4 pm'"""
    d = _local(ts, tz)
    return d.strftime("%I %p").lstrip("0").lower() if d else "—"


def fmt_num(v, digits=0, suffix=""):
    if v is None:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def now_row(obs, tz):
    """The 'now' table, already formatted for display."""
    if not obs:
        return []
    wind = obs.get("wind_mph")
    d = wind_dir_name(obs.get("wind_dir"))
    # Always show a speed — "calm" on its own hides the number.
    if wind is None:
        wind_txt = "—"
    elif wind < 1:
        wind_txt = "calm · 0 mph"
    else:
        wind_txt = f"{d} {fmt_num(wind)} mph".strip()
    return [
        ("temp", fmt_num(obs.get("temp_f"), 0, "°F")),
        ("dew point", fmt_num(obs.get("dewpt_f"), 0, "°F")),
        ("humidity", fmt_num(obs.get("rh"), 0, "%")),
        ("wind", wind_txt),
        ("gust", fmt_num(obs.get("gust_mph"), 0, " mph") if obs.get("gust_mph") else "none"),
        ("pressure", fmt_num(obs.get("pressure_mb"), 1, " mb")),
        ("visibility", fmt_num(obs.get("vis_mi"), 1, " mi")),
        ("sky", obs.get("sky") or obs.get("description") or "—"),
        ("observed", fmt_clock(obs.get("ts"), tz)),
    ]


def recent_hours(rows, tz, hours=3):
    """The last few clock hours as (time, wind, gust), newest first.

    `rows` is the ascending observation list; stations that report more than
    once an hour collapse to their newest reading for that hour. Wind carries
    its direction and is always a number ("SE 5", "calm 0"); the table header
    supplies the mph unit.
    """
    seen: dict = {}
    for r in rows:
        d = _local(r.get("ts"), tz)
        if not d:
            continue
        key = d.replace(minute=0, second=0, microsecond=0)
        if key not in seen or d >= seen[key][0]:
            seen[key] = (d, r)
    out = []
    for key in sorted(seen, reverse=True)[:hours]:
        _, r = seen[key]
        wind = r.get("wind_mph")
        gust = r.get("gust_mph")
        if wind is None:
            wind_txt = "—"
        elif wind < 1:
            wind_txt = "calm 0"
        else:
            wind_txt = f"{wind_dir_name(r.get('wind_dir'))} {wind:.0f}".strip()
        out.append({
            "time": fmt_clock(r.get("ts"), tz),
            "wind": wind_txt,
            "gust": fmt_num(gust, 0) if gust else "—",
        })
    return out


def hourly_buckets(rows, hours=24):
    """Bucket observations into the last `hours` clock hours, max per hour.

    Returns (labels, sustained[], gusts[]) with None for hours that reported
    nothing, so the sparkline shows the gap instead of inventing a value.
    """
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    starts = [end - timedelta(hours=h) for h in range(hours - 1, -1, -1)]
    index = {s: i for i, s in enumerate(starts)}
    wind = [None] * hours
    gust = [None] * hours
    for r in rows:
        d = _dt(r.get("ts"))
        if not d:
            continue
        key = d.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        i = index.get(key)
        if i is None:
            continue
        for src, dest in ((r.get("wind_mph"), wind), (r.get("gust_mph"), gust)):
            if src is not None and (dest[i] is None or src > dest[i]):
                dest[i] = src
    return starts, wind, gust


def daily_summary(rows, tz, days=HISTORY_DAYS):
    """Group observations by local calendar date -> high/low/max wind/max gust."""
    buckets: dict = {}
    for r in rows:
        d = _local(r.get("ts"), tz)
        if not d:
            continue
        b = buckets.setdefault(d.date(), {"date": d, "hi": None, "lo": None,
                                          "wind": None, "gust": None})
        t = r.get("temp_f")
        if t is not None:
            b["hi"] = t if b["hi"] is None else max(b["hi"], t)
            b["lo"] = t if b["lo"] is None else min(b["lo"], t)
        for key, val in (("wind", r.get("wind_mph")), ("gust", r.get("gust_mph"))):
            if val is not None and (b[key] is None or val > b[key]):
                b[key] = val
    out = []
    for day in sorted(buckets, reverse=True)[:days]:
        b = buckets[day]
        out.append({
            "date": b["date"].strftime("%a %b %-d"),
            "hi": fmt_num(b["hi"], 0, "°"),
            "lo": fmt_num(b["lo"], 0, "°"),
            "wind": fmt_num(b["wind"], 0, " mph"),
            "gust": fmt_num(b["gust"], 0, " mph") if b["gust"] else "—",
        })
    return out


def forecast_view(rows, tz, limit=FORECAST_HOURS):
    out = []
    for r in rows[:limit]:
        wind = r.get("wind_mph")
        pop = r.get("pop") or 0.0
        out.append({
            "hour": fmt_hour(r.get("start_ts"), tz),
            "temp": fmt_num(r.get("temp_f"), 0, "°"),
            "wind": f"{r.get('wind_dir') or ''} {fmt_num(wind)} mph".strip(),
            # NB: not "pop" — Jinja resolves `f.pop` to dict.pop, not the key.
            "rain": f"{int(pop)}%",
            "short": r.get("short") or "—",
            # A candidate paddle window: light wind and unlikely to rain.
            "good": wind is not None and wind < 8 and pop < 30,
        })
    return out


# -------------------------------------------------------------------- routes

@app.get("/", response_class=HTMLResponse)
def index():
    conn = state["conn"]
    tz = local_tz()
    obs = db.latest_observation(conn)
    recent = db.observations_since(conn, 24)
    week = db.observations_since(conn, 24 * HISTORY_DAYS)
    fc_rows = db.latest_forecast(conn, limit=FORECAST_HOURS)

    next3 = [
        {"pop": r.get("pop"), "wind_mph": r.get("wind_mph"),
         "hour_label": fmt_hour(r.get("start_ts"), tz)}
        for r in fc_rows[:3]
    ]
    verdict = kayak_verdict(
        (obs or {}).get("wind_mph"),
        (obs or {}).get("gust_mph"),
        (obs or {}).get("wind_dir"),
        next3,
    )

    _, wind, gust = hourly_buckets(recent, 24)
    scale = max([v for v in wind + gust if v is not None] or [0]) or None
    present_w = [v for v in wind if v is not None]
    present_g = [v for v in gust if v is not None]

    obs_dt = _dt((obs or {}).get("ts"))
    stale = (
        obs_dt is None
        or (datetime.now(timezone.utc) - obs_dt) > timedelta(minutes=STALE_MINUTES)
    )

    html = templates.get_template("index.html").render(
        verdict=verdict,
        updated=fmt_clock((obs or {}).get("ts"), tz),
        stale=stale,
        stale_minutes=STALE_MINUTES,
        now_rows=now_row(obs, tz),
        recent_rows=recent_hours(recent, tz),
        wind_spark=sparkline(wind, scale),
        gust_spark=sparkline(gust, scale),
        wind_min=fmt_num(min(present_w), 0) if present_w else "—",
        wind_max=fmt_num(max(present_w), 0) if present_w else "—",
        gust_max=fmt_num(max(present_g), 0) if present_g else "—",
        forecast=forecast_view(fc_rows, tz),
        days=daily_summary(week, tz),
        station=nws.STATION,
    )
    return HTMLResponse(html)


@app.get("/api/latest")
def api_latest():
    obs = db.latest_observation(state["conn"])
    if not obs:
        return JSONResponse({"error": "no observations yet"}, status_code=503)
    obs.pop("raw", None)
    return obs


@app.get("/api/history")
def api_history(hours: int = Query(24, ge=1, le=24 * 365)):
    rows = db.observations_since(state["conn"], hours)
    for r in rows:
        r.pop("raw", None)
    return {"hours": hours, "count": len(rows), "observations": rows}


@app.get("/api/forecast")
def api_forecast():
    rows = db.latest_forecast(state["conn"], limit=FORECAST_HOURS * 4)
    return {"count": len(rows), "periods": rows}


@app.get("/health")
def health():
    conn = state["conn"]
    obs = db.latest_observation(conn)
    poller = state["poller"]
    body = {
        "ok": bool(obs) and (poller is None or poller.healthy),
        "last_obs": (obs or {}).get("ts"),
        "rows": db.observation_count(conn),
    }
    if poller is not None and not poller.healthy:
        body["error"] = poller.last_error
    return JSONResponse(body, status_code=200 if body["ok"] else 503)
