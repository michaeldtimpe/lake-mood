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
from .verdict import kayak_verdict, wind_dir_name

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("lake_mood")

HERE = Path(__file__).parent
STALE_MINUTES = 90
VERDICT_FALLBACK_MINUTES = 60
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
    # Always show a speed — "calm" on its own hides the number. A variable
    # METAR reports no speed and no direction but can still carry a gust.
    if wind is None:
        wind_txt = "variable" if obs.get("gust_mph") is not None else "—"
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


def has_wind(o):
    """True if the observation reports a sustained speed or a gust."""
    return bool(o) and (o.get("wind_mph") is not None or o.get("gust_mph") is not None)


def verdict_observation(obs, rows, minutes=VERDICT_FALLBACK_MINUTES, now=None):
    """The observation the verdict should judge, plus whether it is a fallback.

    A variable-wind METAR can land with neither wind_mph nor gust_mph, which
    would blank the banner. When that happens, reach back to the newest
    observation within `minutes` that reports either — the page stamps its
    time into the note so the reading is not mistaken for the current one.
    """
    if has_wind(obs):
        return obs, False
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)
    for r in reversed(list(rows or ())):
        d = _dt(r.get("ts"))
        if d is None or d.astimezone(timezone.utc) < cutoff:
            continue
        if has_wind(r):
            return r, True
    return obs, False


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


def bars(values, scale):
    """Turn hourly values into CSS bar heights: {"pct": 0-100 or None}.

    Height is a share of the shared wind/gust `scale` so both charts read on
    one axis; a missing hour keeps its slot but has no bar.
    """
    out = []
    for v in values:
        if v is None or not scale:
            out.append({"pct": None})
        else:
            out.append({"pct": max(2, min(100, round(v / scale * 100)))})
    return out


# --- forecast text ----------------------------------------------------------

# NWS "short" strings are title-case sentences; the dense forecast column has
# room for about a dozen characters. Anything unmapped falls back to lowercase.
_SHORT_LABELS = {
    "slight chance showers and thunderstorms": "sl. t-storms",
    "chance showers and thunderstorms": "t-storms",
    "showers and thunderstorms likely": "t-storms likely",
    "showers and thunderstorms": "t-storms",
    "slight chance rain showers": "sl. showers",
    "chance rain showers": "showers",
    "rain showers likely": "showers likely",
    "isolated rain showers": "iso. showers",
    "scattered rain showers": "sct. showers",
    "slight chance light rain": "sl. rain",
    "chance light rain": "light rain",
}


def short_label(s):
    """Shorten an NWS forecast 'short' string for the narrow sky column."""
    if not s:
        return "—"
    t = " ".join(str(s).split()).lower()
    return _SHORT_LABELS.get(t, t)


# --- forecast temperature chips ---------------------------------------------

CHIP_MIN = 8
CHIP_MAX = 40


def chip_pct(temp, lo, hi):
    """Accent share (CHIP_MIN..CHIP_MAX) for a forecast row's temp chip.

    The scale spans only the range present in the rows on screen, so a flat
    12 hours still reads as a flat block rather than a fake gradient. A
    degenerate or unknown range pins every chip to the faintest step.
    """
    if temp is None or lo is None or hi is None or hi <= lo:
        return CHIP_MIN
    frac = max(0.0, min(1.0, (float(temp) - float(lo)) / (float(hi) - float(lo))))
    return int(round(CHIP_MIN + frac * (CHIP_MAX - CHIP_MIN)))


# --- daily condition glyph ---------------------------------------------------

def day_glyph(shorts=None, sky=None):
    """One emoji for a day: forecast wording when we have it, else METAR sky."""
    text = " ".join(str(s) for s in (shorts or ()) if s).lower()
    if text:
        if "thunder" in text:
            return "⛈️"
        if "rain" in text or "shower" in text:
            return "🌧️"
        if "cloud" in text or "overcast" in text:
            return "☁️"
        if "sun" in text or "clear" in text or "fair" in text:
            return "☀️"
    s = (sky or "").upper()
    if "OVC" in s or "BKN" in s:
        return "☁️"
    if "SCT" in s or "FEW" in s or "CLR" in s or "SKC" in s or "NCD" in s:
        return "☀️"
    return "·"


# --- hero readouts -----------------------------------------------------------

def wind_hero(obs):
    """Big wind numbers for the left column: sustained word/number and gust."""
    o = obs or {}
    wind = o.get("wind_mph")
    gust = o.get("gust_mph")
    name = wind_dir_name(o.get("wind_dir"))
    if wind is None:
        sustained = "var" if gust is not None else "—"
        label = "sustained"
    elif wind < 1:
        sustained = "calm"
        label = "sustained"
    else:
        sustained = fmt_num(wind)
        label = f"{name} sustained".strip()
    return {
        "sustained": sustained,
        "sustained_label": label,
        "gust": fmt_num(gust) if gust else "—",
    }


def daily_summary(rows, tz, days=HISTORY_DAYS, shorts_by_date=None):
    """Group observations by local calendar date -> high/low/max wind/max gust.

    Each day also carries a condition glyph: the forecast wording for that
    date when we have any (``shorts_by_date``), otherwise the day's newest
    METAR sky code.
    """
    buckets: dict = {}
    for r in rows:
        d = _local(r.get("ts"), tz)
        if not d:
            continue
        b = buckets.setdefault(d.date(), {"date": d, "hi": None, "lo": None,
                                          "wind": None, "gust": None, "sky": None})
        if r.get("sky"):
            b["sky"] = r["sky"]
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
            "glyph": day_glyph((shorts_by_date or {}).get(day), b["sky"]),
            "hi": fmt_num(b["hi"], 0, "°"),
            "lo": fmt_num(b["lo"], 0, "°"),
            "wind": fmt_num(b["wind"], 0),
            "gust": fmt_num(b["gust"], 0) if b["gust"] else "—",
            "gusty": bool(b["gust"] and b["gust"] >= 20),
        })
    return out


def forecast_view(rows, tz, limit=FORECAST_HOURS):
    used = list(rows[:limit])
    temps = [r.get("temp_f") for r in used if r.get("temp_f") is not None]
    lo = min(temps) if temps else None
    hi = max(temps) if temps else None
    out = []
    for r in used:
        wind = r.get("wind_mph")
        pop = r.get("pop") or 0.0
        out.append({
            "hour": fmt_hour(r.get("start_ts"), tz),
            "temp": fmt_num(r.get("temp_f"), 0, "°"),
            "chip": chip_pct(r.get("temp_f"), lo, hi),
            "dir": r.get("wind_dir") or "",
            "wind_num": fmt_num(wind),
            "wind": f"{r.get('wind_dir') or ''} {fmt_num(wind)} mph".strip(),
            # NB: not "pop" — Jinja resolves `f.pop` to dict.pop, not the key.
            "rain": f"{int(pop)}%",
            "rain_pct": int(pop),
            "short": short_label(r.get("short")),
            # A candidate paddle window: light wind and unlikely to rain.
            "good": wind is not None and wind < 8 and pop < 30,
        })
    return out


def chart_view(starts, wind, gust, tz):
    """Geometry for the 24-hour wind chart, as percentages of a shared scale.

    Sustained values are columns (height), gusts are markers sitting at their
    own height on the same axis, so one glance compares the two. Gridlines
    step every 10 mph up to the scale top.
    """
    scale = max([v for v in wind + gust if v is not None] or [0]) or None
    w = bars(wind, scale)
    g = bars(gust, scale)
    slots = [{"wind": a["pct"], "gust": b["pct"]} for a, b in zip(w, g)]
    ticks = []
    if scale:
        step = 10
        v = 0
        while v <= scale:
            ticks.append({"pct": round(v / scale * 100, 1), "label": str(v)})
            v += step
    mid = starts[len(starts) // 2] if starts else None
    return {
        "slots": slots,
        "ticks": ticks,
        "start": fmt_hour(starts[0].isoformat(), tz) if starts else "",
        "mid": fmt_hour(mid.isoformat(), tz) if mid else "",
    }


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
    v_obs, v_fallback = verdict_observation(obs, recent)
    verdict = kayak_verdict(
        (v_obs or {}).get("wind_mph"),
        (v_obs or {}).get("gust_mph"),
        (v_obs or {}).get("wind_dir"),
        next3,
        at=fmt_clock((v_obs or {}).get("ts"), tz) if v_fallback else None,
    )

    starts, wind, gust = hourly_buckets(recent, 24)
    present_w = [v for v in wind if v is not None]
    present_g = [v for v in gust if v is not None]

    # Forecast wording keyed by local date, so today's row in the 7-day table
    # shows what is coming rather than what the sky did at 3 a.m.
    shorts_by_date: dict = {}
    for r in fc_rows:
        d = _local(r.get("start_ts"), tz)
        if d and r.get("short"):
            shorts_by_date.setdefault(d.date(), []).append(r["short"])

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
        now=dict(now_row(obs, tz)),
        temp_f=fmt_num((obs or {}).get("temp_f"), 0),
        wind_now=wind_hero(v_obs if v_fallback else obs),
        recent_rows=recent_hours(recent, tz),
        chart=chart_view(starts, wind, gust, tz),
        wind_min=fmt_num(min(present_w), 0) if present_w else "—",
        wind_max=fmt_num(max(present_w), 0) if present_w else "—",
        gust_max=fmt_num(max(present_g), 0) if present_g else "—",
        forecast=forecast_view(fc_rows, tz),
        days=daily_summary(week, tz, shorts_by_date=shorts_by_date),
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
