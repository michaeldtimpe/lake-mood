"""View-helper tests for app.main. No network, no DB."""

from zoneinfo import ZoneInfo

from app.main import now_row, recent_hours

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
