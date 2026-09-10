"""View-helper tests for app.main. No network, no DB."""

from zoneinfo import ZoneInfo

from datetime import date, datetime, timedelta, timezone

from app.main import (
    CHIP_MAX,
    CHIP_MIN,
    chip_pct,
    daily_summary,
    day_glyph,
    forecast_view,
    has_wind,
    now_row,
    recent_hours,
    short_label,
    verdict_observation,
)

TZ = ZoneInfo("America/Chicago")


def obs(ts, wind=None, gust=None, wind_dir=None):
    return {"ts": ts, "wind_mph": wind, "gust_mph": gust, "wind_dir": wind_dir}


# ----------------------------------------------------------------- last hours

def test_recent_hours_newest_first_and_limited():
    rows = [
        obs("2026-09-10T17:53:00+00:00", 5.0, None, 135),   # 12:53 pm local
        obs("2026-09-10T18:53:00+00:00", 7.0, 14.0, 180),   #  1:53 pm
        obs("2026-09-10T19:53:00+00:00", 9.0, None, 225),   #  2:53 pm
        obs("2026-09-10T20:53:00+00:00", 11.0, 21.0, 270),  #  3:53 pm
    ]
    out = recent_hours(rows, TZ, hours=3)
    assert [r["time"] for r in out] == ["3:53 pm", "2:53 pm", "1:53 pm"]
    assert [r["wind"] for r in out] == ["W 11", "SW 9", "S 7"]
    assert [r["gust"] for r in out] == ["21", "—", "14"]


def test_recent_hours_collapses_to_newest_reading_per_hour():
    rows = [
        obs("2026-09-10T20:15:00+00:00", 4.0, None, 135),
        obs("2026-09-10T20:53:00+00:00", 12.0, 20.0, 135),
    ]
    out = recent_hours(rows, TZ, hours=3)
    assert len(out) == 1
    assert out[0] == {"time": "3:53 pm", "wind": "SE 12", "gust": "20"}


def test_recent_hours_handles_empty_and_bad_timestamps():
    assert recent_hours([], TZ) == []
    assert recent_hours([obs(None, 5.0), obs("not-a-date", 6.0)], TZ) == []


def test_recent_hours_calm_still_shows_a_number():
    out = recent_hours([obs("2026-09-10T20:53:00+00:00", 0.0, None, 90)], TZ)
    assert out == [{"time": "3:53 pm", "wind": "calm 0", "gust": "—"}]


def test_recent_hours_missing_wind_renders_dash():
    out = recent_hours([obs("2026-09-10T20:53:00+00:00")], TZ)
    assert out == [{"time": "3:53 pm", "wind": "—", "gust": "—"}]


# -------------------------------------------------------------------- now row

def _val(rows, key):
    return dict(rows)[key]


def test_now_row_wind_carries_direction_and_speed():
    rows = now_row(obs("2026-09-10T20:53:00+00:00", 5.0, 12.0, 135), TZ)
    assert _val(rows, "wind") == "SE 5 mph"
    assert _val(rows, "gust") == "12 mph"


def test_now_row_calm_shows_zero_and_no_gust_shows_none():
    rows = now_row(obs("2026-09-10T20:53:00+00:00", 0.0, None, 135), TZ)
    assert _val(rows, "wind") == "calm · 0 mph"
    assert _val(rows, "gust") == "none"


def test_now_row_missing_wind_is_a_dash():
    assert _val(now_row(obs("2026-09-10T20:53:00+00:00"), TZ), "wind") == "—"


def test_now_row_empty_observation():
    assert now_row(None, TZ) == []


# ------------------------------------------------------------ verdict source

NOW = datetime(2026, 9, 10, 22, 40, tzinfo=timezone.utc)


def at(minutes_ago, wind=None, gust=None):
    ts = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return obs(ts, wind, gust)


def test_has_wind():
    assert has_wind({"wind_mph": 0.0, "gust_mph": None}) is True
    assert has_wind({"wind_mph": None, "gust_mph": 24.16}) is True
    assert has_wind({"wind_mph": None, "gust_mph": None}) is False
    assert has_wind(None) is False


def test_verdict_observation_prefers_the_latest_when_it_has_wind():
    latest = at(5, 5.0, None)
    got, fallback = verdict_observation(latest, [at(65, 9.0), latest], now=NOW)
    assert got is latest and fallback is False


def test_verdict_observation_falls_back_to_the_newest_with_wind():
    # The live bug: newest METAR has neither number, 20 minutes back does.
    latest = at(5)
    older = at(20, 5.0, None)
    rows = [at(90, 12.0), at(40), older, latest]
    got, fallback = verdict_observation(latest, rows, now=NOW)
    assert got is older and fallback is True


def test_verdict_observation_accepts_a_gust_only_row():
    latest = at(5)
    gusty = at(15, None, 24.16)
    got, fallback = verdict_observation(latest, [gusty, latest], now=NOW)
    assert got is gusty and fallback is True


def test_verdict_observation_ignores_rows_older_than_the_window():
    latest = at(5)
    rows = [at(75, 9.0), at(61, 8.0), latest]
    got, fallback = verdict_observation(latest, rows, now=NOW)
    assert got is latest and fallback is False


def test_verdict_observation_survives_empty_history_and_bad_timestamps():
    latest = at(5)
    assert verdict_observation(latest, [], now=NOW) == (latest, False)
    assert verdict_observation(latest, [obs("nope", 5.0)], now=NOW) == (latest, False)
    assert verdict_observation(None, [], now=NOW) == (None, False)


# ------------------------------------------------- now table, variable wind

def test_now_row_variable_wind_with_a_gust():
    rows = now_row(obs("2026-09-10T22:35:00+00:00", None, 24.16, None), TZ)
    assert _val(rows, "wind") == "variable"
    assert _val(rows, "gust") == "24 mph"


# ------------------------------------------------- forecast chips and glyphs

def test_chip_pct_spans_the_row_range():
    assert chip_pct(80.0, 80.0, 100.0) == CHIP_MIN
    assert chip_pct(100.0, 80.0, 100.0) == CHIP_MAX
    assert chip_pct(90.0, 80.0, 100.0) == 24  # midpoint of 8..40


def test_chip_pct_clamps_and_survives_a_flat_or_unknown_range():
    assert chip_pct(120.0, 80.0, 100.0) == CHIP_MAX
    assert chip_pct(60.0, 80.0, 100.0) == CHIP_MIN
    assert chip_pct(90.0, 90.0, 90.0) == CHIP_MIN   # every hour the same temp
    assert chip_pct(None, 80.0, 100.0) == CHIP_MIN
    assert chip_pct(90.0, None, None) == CHIP_MIN


def test_forecast_view_chips_scale_across_the_rows_on_screen():
    rows = [
        {"start_ts": "2026-09-10T23:00:00+00:00", "temp_f": 97.0, "wind_mph": 5.0,
         "wind_dir": "SE", "pop": 28.0, "short": "Chance Showers And Thunderstorms"},
        {"start_ts": "2026-09-11T00:00:00+00:00", "temp_f": 81.0, "wind_mph": 12.0,
         "wind_dir": "S", "pop": 60.0, "short": "Slight Chance Rain Showers"},
    ]
    out = forecast_view(rows, TZ)
    assert [f["chip"] for f in out] == [CHIP_MAX, CHIP_MIN]
    assert [f["short"] for f in out] == ["t-storms", "sl. showers"]
    assert [f["rain_pct"] for f in out] == [28, 60]
    assert out[0]["good"] is True and out[1]["good"] is False


def test_short_label_maps_known_phrases_and_lowercases_the_rest():
    assert short_label("Slight Chance Showers And Thunderstorms") == "sl. t-storms"
    assert short_label("Mostly Cloudy") == "mostly cloudy"
    assert short_label("Patchy Fog") == "patchy fog"
    assert short_label(None) == "—"


def test_day_glyph_prefers_forecast_wording():
    assert day_glyph(["Chance Showers And Thunderstorms"], "CLR") == "⛈️"
    assert day_glyph(["Slight Chance Rain Showers"], "CLR") == "🌧️"
    assert day_glyph(["Mostly Cloudy"], "CLR") == "☁️"
    assert day_glyph(["Sunny"], "OVC") == "☀️"


def test_day_glyph_falls_back_to_the_sky_code():
    assert day_glyph(None, "OVC") == "☁️"
    assert day_glyph([], "BKN045") == "☁️"
    assert day_glyph([], "SCT050") == "☀️"
    assert day_glyph([], "CLR") == "☀️"
    assert day_glyph([], None) == "·"


def test_daily_summary_carries_a_glyph_and_flags_a_deciding_gust():
    rows = [
        {"ts": "2026-09-10T20:53:00+00:00", "temp_f": 100.0, "wind_mph": 10.0,
         "gust_mph": 24.0, "sky": "OVC008"},
        {"ts": "2026-09-09T20:53:00+00:00", "temp_f": 90.0, "wind_mph": 8.0,
         "gust_mph": 12.0, "sky": "CLR"},
    ]
    out = daily_summary(rows, TZ)
    assert out[0]["glyph"] == "☁️" and out[0]["gusty"] is True
    assert out[1]["glyph"] == "☀️" and out[1]["gusty"] is False
    # a forecast for today wins over the METAR sky
    out = daily_summary(rows, TZ, shorts_by_date={date(2026, 9, 10): ["Sunny"]})
    assert out[0]["glyph"] == "☀️"
