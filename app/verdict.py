"""Pure helpers: unit conversion, wind naming, text sparklines, kayak verdict.

Nothing in here touches the network, the database, or the clock, so it is all
directly unit-testable.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------- conversions

def c_to_f(c):
    """Celsius -> Fahrenheit. None passes through."""
    return None if c is None else c * 9.0 / 5.0 + 32.0


def kmh_to_mph(kmh):
    """km/h -> mph. None passes through."""
    return None if kmh is None else kmh * 0.621371


def pa_to_mb(pa):
    """Pascals -> millibars (hPa). None passes through."""
    return None if pa is None else pa / 100.0


def m_to_mi(m):
    """Meters -> statute miles. None passes through."""
    return None if m is None else m / 1609.344


# ------------------------------------------------------------------ direction

_COMPASS_16 = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def wind_dir_name(deg):
    """Degrees -> 16-point compass abbreviation. None/invalid -> ''."""
    if deg is None:
        return ""
    try:
        d = float(deg)
    except (TypeError, ValueError):
        return ""
    idx = int((d % 360.0) / 22.5 + 0.5) % 16
    return _COMPASS_16[idx]


# ------------------------------------------------------------------ sparkline

_BLOCKS = "▁▂▃▄▅▆▇█"  # ▁▂▃▄▅▆▇█


def sparkline(values, max_value=None):
    """Render an iterable of numbers as block characters.

    ``None`` entries render as a space so gaps stay visible. ``max_value``
    pins the top of the scale (useful to draw two series, e.g. wind and
    gusts, against a shared axis); it defaults to the max present value.
    """
    vals = list(values)
    if not vals:
        return ""
    present = [v for v in vals if v is not None]
    top = max_value if max_value is not None else (max(present) if present else None)
    if top is None or top <= 0:
        return "".join(" " if v is None else _BLOCKS[0] for v in vals)
    n = len(_BLOCKS)
    out = []
    for v in vals:
        if v is None:
            out.append(" ")
            continue
        # Split 0..top into n equal bins so evenly spaced values get distinct
        # blocks and only the true maximum reaches the full block.
        frac = max(0.0, min(1.0, float(v) / float(top)))
        idx = 0 if frac <= 0 else min(n - 1, math.ceil(frac * n) - 1)
        out.append(_BLOCKS[idx])
    return "".join(out)


# -------------------------------------------------------------------- verdict

# (level, label, max sustained, max gust). The label is the judgment — the
# banner carries no advice phrase on top of it.
_LEVELS = (
    ("flat", "flat", 8.0, 12.0),
    ("ripples", "ripples", 12.0, 16.0),
    ("chop", "chop", 16.0, 22.0),
)
_WHITECAPS = ("whitecaps", "whitecaps")

# The two top levels are held states, not single quiet readings: every
# observation for at least HELD_MINUTES must qualify, and no storm may be
# coming. "optimal" is glass, every reading 0 mph (KGPM reports anything
# under 3 kt as calm); "calm" is every reading under these ceilings.
DEAD_FLAT_SUSTAINED = 5.0
DEAD_FLAT_GUST = 8.0
HELD_MINUTES = 30

STORM_POP = 40.0
STORM_WIND_MPH = 20.0


def storm_flag(forecast_hours):
    """True if any of the given forecast hours looks stormy.

    ``forecast_hours`` is a list of dicts with optional ``pop`` (percent) and
    ``wind_mph`` keys — normally the next three hourly periods.
    """
    for h in forecast_hours or ():
        pop = h.get("pop")
        wind = h.get("wind_mph")
        if pop is not None and pop >= STORM_POP:
            return True
        if wind is not None and wind >= STORM_WIND_MPH:
            return True
    return False


def _storm_note(forecast_hours):
    """One sentence describing why the next three hours look unfriendly."""
    hours = list(forecast_hours or ())
    pop_hours = [h for h in hours if (h.get("pop") or 0) >= STORM_POP]
    wind_hours = [h for h in hours if (h.get("wind_mph") or 0) >= STORM_WIND_MPH]
    tail = "outflow gusts can hit 30 mph fast."
    if pop_hours:
        worst = max(pop_hours, key=lambda h: h.get("pop") or 0)
        when = worst.get("hour_label")
        by = f" by {when}" if when else " in the next 3 hours"
        return f"Thunderstorm chance {int(round(worst['pop']))}%{by}; {tail}"
    if wind_hours:
        worst = max(wind_hours, key=lambda h: h.get("wind_mph") or 0)
        when = worst.get("hour_label")
        by = f" by {when}" if when else " within 3 hours"
        return (
            f"Wind building to {int(round(worst['wind_mph']))} mph{by}; {tail}"
        )
    return ""


def dead_still(sustained_mph, gust_mph=None):
    """True if one reading is 0 mph with no gust — counts toward ``optimal``.

    A gust-only reading has wind by definition, so it is never still.
    """
    if sustained_mph is None:
        return False
    return float(sustained_mph) < 0.1 and (gust_mph is None or float(gust_mph) < 0.1)


def dead_flat(sustained_mph, gust_mph=None):
    """True if one reading is under 5 mph — counts toward a ``calm`` streak.

    Same reading of the numbers as ``kayak_verdict``: a missing gust means the
    sustained speed, a gust-only reading is held against both ceilings. A
    reading with neither number is not evidence either way, so it is False.
    """
    if sustained_mph is None and gust_mph is None:
        return False
    if sustained_mph is None:
        sustained = gust = float(gust_mph)
    else:
        sustained = float(sustained_mph)
        gust = sustained if gust_mph is None else max(float(gust_mph), sustained)
    return sustained < DEAD_FLAT_SUSTAINED and gust < DEAD_FLAT_GUST


def kayak_verdict(sustained_mph, gust_mph=None, wind_dir=None, forecast_hours=None,
                  at=None, calm_minutes=None, calm_since=None,
                  still_minutes=None, still_since=None):
    """Judge whether Mountain Creek Lake is worth putting a boat on.

    ``wind_dir`` may be degrees or an already-named compass point.
    ``forecast_hours`` is the next ~3 hourly periods (see ``storm_flag``).
    ``at`` stamps the note with the observation clock time, for when the page
    had to reach back past a wind-less observation (e.g. ``(5:15 pm obs)``).
    Returns ``{"level", "label", "note"}``; the page renders the label in the
    accent color followed by the note, e.g. ``flat — SE 6 mph, no gusts.``
    The label is the judgment, so the note adds no advice phrase; it always
    carries a number, so a dead-calm lake reads ``calm (0 mph), no gusts.``
    rather than just "calm". A storm sentence is appended when one applies.

    A METAR with variable wind can report a gust and nothing else. That gust
    is exactly what a kayaker needs, so it drives both thresholds on its own
    and the note reads ``wind variable, gusting 24.`` Only an observation
    with neither number is "no data".

    ``still_minutes`` / ``calm_minutes`` are how long the station has read
    0 mph (``dead_still``) / under 5 mph (``dead_flat``) without a break, and
    ``still_since`` / ``calm_since`` the clock time each streak began. At
    ``HELD_MINUTES`` or more, with this reading itself qualifying and no
    storm flag, the level becomes ``optimal`` or ``calm`` and the note says
    since when, e.g. ``calm (0 mph), no gusts · 0 mph since 5:35 am.``
    """
    if sustained_mph is None and gust_mph is None:
        return {
            "level": "unknown",
            "label": "no data",
            "note": "No recent observation from KGPM.",
            "base_note": "No recent observation from KGPM.",
            "storm_note": "",
        }

    # Gust-only: judge it as if the gust were also the sustained wind, which
    # is the conservative reading of a variable-direction outflow.
    gust_only = sustained_mph is None
    if gust_only:
        sustained = gust = float(gust_mph)
    else:
        sustained = float(sustained_mph)
        gust = sustained if gust_mph is None else max(float(gust_mph), sustained)

    level, label = _WHITECAPS
    for lvl, lbl, max_sus, max_gust in _LEVELS:
        if sustained < max_sus and gust < max_gust:
            level, label = lvl, lbl
            break

    if isinstance(wind_dir, str):
        name = wind_dir
    else:
        name = wind_dir_name(wind_dir)

    if gust_only:
        # No sustained speed and no direction to report — just the gust.
        core = f"wind variable, gusting {gust:.0f}"
    else:
        # Always state a speed, so "calm" never hides the actual number.
        calm = sustained < 1
        if calm:
            wind_txt = "calm (0 mph)"
        else:
            wind_txt = f"{name} {sustained:.0f} mph".strip()

        if gust_mph is None or gust - sustained < 2:
            gust_txt = ", no gusts"
        else:
            wind_txt = "calm (0)" if calm else f"{name} {sustained:.0f}".strip()
            gust_txt = f" gusting {gust:.0f}"
        core = f"{wind_txt}{gust_txt}"

    base = f"{core} ({at} obs)." if at else f"{core}."

    storm = _storm_note(forecast_hours) if storm_flag(forecast_hours) else ""

    for lvl, held_for, since, qualifies, what in (
        ("optimal", still_minutes, still_since, dead_still, "0 mph"),
        ("calm", calm_minutes, calm_since, dead_flat, "light wind"),
    ):
        if (not storm and held_for is not None and held_for >= HELD_MINUTES
                and qualifies(sustained_mph, gust_mph)):
            level = label = lvl
            held = f"since {since}" if since else f"for {held_for:.0f} min"
            base = f"{base[:-1]} · {what} {held}."
            break

    note = f"{base} {storm}" if storm else base

    # `note` stays the whole sentence (JSON, tests, anything that wants one
    # string); `base_note` and `storm_note` let the page colour the storm
    # clause on its own without re-parsing the text.
    return {
        "level": level,
        "label": label,
        "note": note,
        "base_note": base,
        "storm_note": storm,
    }
