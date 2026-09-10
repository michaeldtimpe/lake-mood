"""Thin async client for api.weather.gov plus pure parsers.

The parse_* functions take raw NWS JSON and return plain dicts, so the tests
can exercise them against small fixtures with no network access.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from .verdict import c_to_f, kmh_to_mph, m_to_mi, pa_to_mb

log = logging.getLogger("lake_mood.nws")

BASE = "https://api.weather.gov"
STATION = "KGPM"
LAT, LON = 32.722, -96.951
# Used only if /points fails: Fort Worth office, the grid cell over the lake.
FALLBACK_HOURLY = f"{BASE}/gridpoints/FWD/84,103/forecast/hourly"

DEFAULT_UA = "(lake-mood, set NWS_UA in .env)"


def user_agent() -> str:
    """NWS rejects requests without a contact User-Agent (HTTP 403)."""
    return os.environ.get("NWS_UA") or DEFAULT_UA


def headers() -> dict:
    return {"User-Agent": user_agent(), "Accept": "application/geo+json"}


# ---------------------------------------------------------------- parse layer

def iso_utc(ts):
    """Normalize an NWS timestamp to a sortable UTC ISO-8601 string."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _val(props, key):
    """Pull properties[key].value, tolerating missing keys and nulls."""
    node = props.get(key)
    if not isinstance(node, dict):
        return None
    v = node.get("value")
    return v if isinstance(v, (int, float)) else None


# Rough coverage ordering; we report the densest layer.
_SKY_RANK = {"CLR": 0, "SKC": 0, "NCD": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 5}


def parse_sky(props):
    layers = props.get("cloudLayers") or []
    best, best_rank = None, -1
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        amount = layer.get("amount")
        if not amount:
            continue
        rank = _SKY_RANK.get(amount, 0)
        if rank > best_rank:
            best, best_rank = amount, rank
    return best


def parse_observation(props):
    """NWS observation ``properties`` -> a row dict for the DB.

    Any field may be null upstream; nulls are preserved rather than guessed.
    """
    if not props:
        return None
    ts = iso_utc(props.get("timestamp"))
    if not ts:
        return None
    return {
        "ts": ts,
        "temp_f": c_to_f(_val(props, "temperature")),
        "dewpt_f": c_to_f(_val(props, "dewpoint")),
        "rh": _val(props, "relativeHumidity"),
        "wind_dir": (
            int(round(_val(props, "windDirection")))
            if _val(props, "windDirection") is not None
            else None
        ),
        "wind_mph": kmh_to_mph(_val(props, "windSpeed")),
        "gust_mph": kmh_to_mph(_val(props, "windGust")),
        "pressure_mb": pa_to_mb(_val(props, "barometricPressure")),
        "vis_mi": m_to_mi(_val(props, "visibility")),
        "sky": parse_sky(props),
        "description": props.get("textDescription"),
    }


def parse_observation_collection(payload):
    """A GeoJSON FeatureCollection of observations -> list of row dicts."""
    rows = []
    for feat in (payload or {}).get("features") or []:
        row = parse_observation((feat or {}).get("properties") or {})
        if row:
            rows.append(row)
    return rows


_NUM = re.compile(r"(\d+(?:\.\d+)?)")


def parse_wind_speed(text):
    """'5 mph' -> 5.0; '5 to 10 mph' -> 10.0 (plan for the worst); None -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    nums = [float(n) for n in _NUM.findall(str(text))]
    return max(nums) if nums else None


def parse_forecast_period(period):
    """One hourly forecast period -> a row dict."""
    if not period:
        return None
    start = iso_utc(period.get("startTime"))
    if not start:
        return None
    pop = period.get("probabilityOfPrecipitation")
    pop_val = pop.get("value") if isinstance(pop, dict) else pop
    temp = period.get("temperature")
    return {
        "start_ts": start,
        "temp_f": float(temp) if isinstance(temp, (int, float)) else None,
        "wind_mph": parse_wind_speed(period.get("windSpeed")),
        "wind_dir": period.get("windDirection"),
        "pop": float(pop_val) if isinstance(pop_val, (int, float)) else None,
        "short": period.get("shortForecast"),
    }


def parse_forecast(payload):
    rows = []
    for p in ((payload or {}).get("properties") or {}).get("periods") or []:
        row = parse_forecast_period(p)
        if row:
            rows.append(row)
    return rows


# ----------------------------------------------------------------- http layer

class NWSError(RuntimeError):
    pass


async def _get_json(client: httpx.AsyncClient, url: str, *, params=None, attempts=3):
    """GET with exponential backoff. Raises NWSError once attempts run out."""
    delay = 2.0
    last = None
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.get(url, params=params, headers=headers(), timeout=30.0)
            if resp.status_code == 403:
                raise NWSError(
                    f"403 from NWS for {url} — set NWS_UA to a real contact string"
                )
            resp.raise_for_status()
            return resp.json()
        except NWSError:
            raise
        except Exception as exc:  # network, timeout, 5xx, bad JSON
            last = exc
            log.warning("NWS GET %s failed (attempt %d/%d): %s", url, attempt, attempts, exc)
            if attempt < attempts:
                await asyncio.sleep(delay)
                delay *= 2
    raise NWSError(f"NWS GET {url} failed after {attempts} attempts: {last}")


async def fetch_latest_observation(client):
    payload = await _get_json(client, f"{BASE}/stations/{STATION}/observations/latest")
    return parse_observation((payload or {}).get("properties") or {})


async def fetch_observations(client, start: datetime, end: datetime):
    """One observation window. NWS caps a response at `limit` features."""
    params = {
        "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "limit": 500,
    }
    payload = await _get_json(client, f"{BASE}/stations/{STATION}/observations", params=params)
    return parse_observation_collection(payload)


async def fetch_observation_history(client, days: int = 7):
    """Backfill helper: walk `days` back in one-day windows.

    A window that fails is logged and skipped so one bad day cannot abort the
    whole backfill.
    """
    now = datetime.now(timezone.utc)
    rows = []
    for day in range(days):
        end = now - timedelta(days=day)
        start = end - timedelta(days=1)
        try:
            rows.extend(await fetch_observations(client, start, end))
        except Exception as exc:
            log.warning("backfill window %s..%s failed: %s", start, end, exc)
    return rows


async def resolve_hourly_forecast_url(client):
    """/points/<lat>,<lon> -> properties.forecastHourly, with a static fallback."""
    try:
        payload = await _get_json(client, f"{BASE}/points/{LAT},{LON}", attempts=2)
        url = ((payload or {}).get("properties") or {}).get("forecastHourly")
        if url:
            return url
        log.warning("/points returned no forecastHourly; using fallback grid URL")
    except Exception as exc:
        log.warning("/points lookup failed (%s); using fallback grid URL", exc)
    return FALLBACK_HOURLY


async def fetch_hourly_forecast(client, url):
    payload = await _get_json(client, url)
    return parse_forecast(payload)
