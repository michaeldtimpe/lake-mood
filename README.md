# lake-mood

A text-only weather dashboard for **Mountain Creek Lake**, Grand Prairie TX
(32.722, -96.951). It answers one question: is the lake flat enough to kayak
right now, and if not, when might it be?

Observations come from NWS station **KGPM** (Grand Prairie Municipal Airport,
on the lake's east shore) via the [National Weather Service API][nws], polled
every 10 minutes, plus the hourly forecast for the same point every hour.
Everything is kept forever in a small SQLite file; nothing is pruned.

The page shows a wind verdict (`flat` / `ripples` / `chop` / `whitecaps`), the
current conditions, a 24-hour wind sparkline, the next 12 forecast hours with
candidate paddle windows highlighted, and a 7-day summary. Server-rendered,
no client JavaScript beyond the theme toggle, auto-refreshes every 5 minutes.

## Endpoints

| Path | Returns |
| --- | --- |
| `/` | The dashboard |
| `/api/latest` | Most recent observation |
| `/api/history?hours=24` | Observations for the last N hours (max 8760) |
| `/api/forecast` | Latest hourly forecast snapshot |
| `/health` | `{"ok", "last_obs", "rows"}`; 503 when there is no data |

## Configuration

| Env | Default | Notes |
| --- | --- | --- |
| `NWS_UA` | placeholder | **Required.** NWS returns 403 without a contact string. |
| `TZ` | `America/Chicago` | Display timezone; storage is always UTC. |
| `DB_PATH` | `/data/lake.db` | Parent directory is created on start. |
| `OBS_INTERVAL` | `600` | Observation poll, seconds. |
| `FC_INTERVAL` | `3600` | Forecast poll, seconds. |

Styling follows the [zoleb.com style guide](../zoleb/style-guide/README.md):
Monokai tokens, dark by default, orange accent.

See [DEPLOY.md](DEPLOY.md) for local dev and the Synology deploy recipe.

[nws]: https://www.weather.gov/documentation/services-web-api
