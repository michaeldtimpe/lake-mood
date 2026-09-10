"""Background polling loops.

Two independent asyncio tasks: observations every OBS_INTERVAL and the hourly
forecast every FC_INTERVAL. Neither loop is allowed to die — every iteration is
wrapped, exceptions are logged and counted, and the loop sleeps and retries.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import db, nws

log = logging.getLogger("lake_mood.poller")

BACKFILL_DAYS = 7
FORECAST_URL_KEY = "forecast_hourly_url"
ERROR_THRESHOLD = 5  # consecutive failures before /health reports unhealthy


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        log.warning("bad %s, using default %s", name, default)
        return default


class Poller:
    """Owns the polling tasks and the health counters the app reports on."""

    def __init__(self, conn):
        self.conn = conn
        self.obs_interval = _int_env("OBS_INTERVAL", 600)
        self.fc_interval = _int_env("FC_INTERVAL", 3600)
        self.obs_errors = 0
        self.fc_errors = 0
        self.last_error = None
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------- lifecycle
    def start(self):
        self._tasks = [
            asyncio.create_task(self._obs_loop(), name="lake-mood-obs"),
            asyncio.create_task(self._fc_loop(), name="lake-mood-forecast"),
        ]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
        self._tasks = []

    @property
    def healthy(self) -> bool:
        return self.obs_errors < ERROR_THRESHOLD

    # ------------------------------------------------------------------ work
    async def _backfill(self, client):
        """On a fresh DB, pull the last week so the 7-day table isn't empty."""
        if db.observation_count(self.conn) > 0:
            return
        log.info("empty database — backfilling %d days of KGPM observations", BACKFILL_DAYS)
        rows = await nws.fetch_observation_history(client, BACKFILL_DAYS)
        n = db.insert_observations(self.conn, rows)
        log.info("backfill inserted %d observations (%d fetched)", n, len(rows))

    async def _poll_obs(self, client):
        row = await nws.fetch_latest_observation(client)
        if row:
            n = db.insert_observations(self.conn, [row])
            log.info("obs %s wind=%s gust=%s (%s new)",
                     row["ts"], row.get("wind_mph"), row.get("gust_mph"), n)
        else:
            log.warning("latest observation had no usable timestamp")

    async def _poll_forecast(self, client):
        url = db.get_meta(self.conn, FORECAST_URL_KEY)
        if not url:
            url = await nws.resolve_hourly_forecast_url(client)
            db.set_meta(self.conn, FORECAST_URL_KEY, url)
            log.info("resolved hourly forecast URL: %s", url)
        rows = await nws.fetch_hourly_forecast(client, url)
        n = db.insert_forecast(self.conn, rows)
        log.info("forecast: %d periods, %d stored", len(rows), n)

    # ----------------------------------------------------------------- loops
    async def _obs_loop(self):
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                await self._backfill(client)
            except Exception:
                log.exception("backfill failed; continuing with live polling")
            while True:
                try:
                    await self._poll_obs(client)
                    self.obs_errors = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.obs_errors += 1
                    self.last_error = str(exc)
                    log.exception("observation poll failed (%d consecutive)", self.obs_errors)
                await asyncio.sleep(self.obs_interval)

    async def _fc_loop(self):
        async with httpx.AsyncClient(follow_redirects=True) as client:
            while True:
                try:
                    await self._poll_forecast(client)
                    self.fc_errors = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.fc_errors += 1
                    self.last_error = str(exc)
                    log.exception("forecast poll failed (%d consecutive)", self.fc_errors)
                await asyncio.sleep(self.fc_interval)
