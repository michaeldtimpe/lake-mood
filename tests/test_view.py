"""View-helper tests for app.main. No network, no DB."""

from zoneinfo import ZoneInfo

from app.main import recent_hours

TZ = ZoneInfo("America/Chicago")


def obs(ts, wind=None, gust=None):
    return {"ts": ts, "wind_mph": wind, "gust_mph": gust}


def test_recent_hours_newest_first_and_limited():
    rows = [
        obs("2026-09-10T17:53:00+00:00", 5.0, None),   # 12:53 pm local
        obs("2026-09-10T18:53:00+00:00", 7.0, 14.0),   #  1:53 pm
        obs("2026-09-10T19:53:00+00:00", 9.0, None),   #  2:53 pm
        obs("2026-09-10T20:53:00+00:00", 11.0, 21.0),  #  3:53 pm
    ]
    out = recent_hours(rows, TZ, hours=3)
    assert [r["time"] for r in out] == ["3:53 pm", "2:53 pm", "1:53 pm"]
    assert [r["wind"] for r in out] == ["11", "9", "7"]
    assert [r["gust"] for r in out] == ["21", "—", "14"]


def test_recent_hours_collapses_to_newest_reading_per_hour():
    rows = [
        obs("2026-09-10T20:15:00+00:00", 4.0, None),
        obs("2026-09-10T20:53:00+00:00", 12.0, 20.0),
    ]
    out = recent_hours(rows, TZ, hours=3)
    assert len(out) == 1
    assert out[0] == {"time": "3:53 pm", "wind": "12", "gust": "20"}


def test_recent_hours_handles_empty_and_bad_timestamps():
    assert recent_hours([], TZ) == []
    assert recent_hours([obs(None, 5.0), obs("not-a-date", 6.0)], TZ) == []


def test_recent_hours_missing_wind_renders_dash():
    out = recent_hours([obs("2026-09-10T20:53:00+00:00")], TZ)
    assert out == [{"time": "3:53 pm", "wind": "—", "gust": "—"}]
