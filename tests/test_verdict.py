"""Verdict, sparkline and unit-conversion tests. No network, no DB."""

import pytest

from app.verdict import (
    c_to_f,
    kayak_verdict,
    kmh_to_mph,
    m_to_mi,
    pa_to_mb,
    sparkline,
    storm_flag,
    wind_dir_name,
)


# ---------------------------------------------------------------- conversions

def test_conversions():
    assert c_to_f(0) == pytest.approx(32.0)
    assert c_to_f(100) == pytest.approx(212.0)
    assert kmh_to_mph(100) == pytest.approx(62.1371, rel=1e-4)
    assert pa_to_mb(101325) == pytest.approx(1013.25)
    assert m_to_mi(1609.344) == pytest.approx(1.0)


def test_conversions_pass_none_through():
    for fn in (c_to_f, kmh_to_mph, pa_to_mb, m_to_mi):
        assert fn(None) is None


# ------------------------------------------------------------------ compass

@pytest.mark.parametrize("deg,name", [
    (0, "N"), (360, "N"), (11, "N"), (12, "NNE"), (45, "NE"),
    (90, "E"), (135, "SE"), (180, "S"), (225, "SW"), (270, "W"),
    (315, "NW"), (349, "N"), (338, "NNW"),
])
def test_wind_dir_name(deg, name):
    assert wind_dir_name(deg) == name


def test_wind_dir_name_handles_missing():
    assert wind_dir_name(None) == ""
    assert wind_dir_name("nope") == ""


# ---------------------------------------------------------------- sparkline

def test_sparkline_scales_to_max():
    assert sparkline([0, 8]) == "▁█"
    assert sparkline([1, 2, 3, 4, 5, 6, 7, 8]) == "▁▂▃▄▅▆▇█"


def test_sparkline_none_is_a_space():
    assert sparkline([4, None, 8], max_value=8) == "▄ █"


def test_sparkline_explicit_max_shares_an_axis():
    # Two series drawn against the same ceiling stay comparable.
    assert sparkline([5], max_value=10) < sparkline([10], max_value=10)


def test_sparkline_edge_cases():
    assert sparkline([]) == ""
    assert sparkline([0, 0, 0]) == "▁▁▁"
    assert sparkline([None, None]) == "  "


# ------------------------------------------------------------------ verdict

@pytest.mark.parametrize("sustained,gust,level", [
    (0, None, "flat"),
    (6, None, "flat"),
    (7.9, 11.9, "flat"),
    (8, None, "ripples"),
    (6, 13, "ripples"),
    (11, 15, "ripples"),
    (12, None, "chop"),
    (14, 21, "chop"),
    (16, None, "whitecaps"),
    (10, 25, "whitecaps"),
])
def test_verdict_levels(sustained, gust, level):
    assert kayak_verdict(sustained, gust)["level"] == level


def test_verdict_gust_none_treated_as_sustained():
    assert kayak_verdict(7, None)["level"] == kayak_verdict(7, 7)["level"]


def test_verdict_gust_below_sustained_is_ignored():
    assert kayak_verdict(14, 2)["level"] == "chop"


def test_verdict_note_flat_example():
    v = kayak_verdict(6, None, 135)
    assert v["label"] == "flat"
    assert v["note"] == "SE 6 mph, no gusts."


def test_verdict_note_chop_example():
    v = kayak_verdict(14, 21, 180)
    assert v["note"] == "S 14 gusting 21."


def test_verdict_without_observation():
    v = kayak_verdict(None, None)
    assert v["level"] == "unknown"
    assert "No recent observation" in v["note"]


# -------------------------------------------------------------------- storms

def test_storm_flag_on_pop():
    assert storm_flag([{"pop": 43, "wind_mph": 5}]) is True
    assert storm_flag([{"pop": 39, "wind_mph": 5}]) is False


def test_storm_flag_on_wind():
    assert storm_flag([{"pop": 0, "wind_mph": 20}]) is True


def test_storm_flag_tolerates_nulls_and_empty():
    assert storm_flag([]) is False
    assert storm_flag(None) is False
    assert storm_flag([{"pop": None, "wind_mph": None}]) is False


def test_storm_note_appended_with_hour_label():
    v = kayak_verdict(6, None, 135, [
        {"pop": 10, "wind_mph": 6, "hour_label": "5 pm"},
        {"pop": 43, "wind_mph": 9, "hour_label": "7 pm"},
        {"pop": 20, "wind_mph": 8, "hour_label": "8 pm"},
    ])
    assert v["level"] == "flat"
    assert v["note"] == (
        "SE 6 mph, no gusts. "
        "Thunderstorm chance 43% by 7 pm; outflow gusts can hit 30 mph fast."
    )


def test_storm_note_wind_only():
    v = kayak_verdict(6, None, 135, [{"pop": 0, "wind_mph": 22, "hour_label": "7 pm"}])
    assert "Wind building to 22 mph by 7 pm" in v["note"]


def test_calm_wording_still_states_the_number():
    assert kayak_verdict(0, None, 0)["note"] == "calm (0 mph), no gusts."
    assert kayak_verdict(0.4, None, 90)["note"] == "calm (0 mph), no gusts."


def test_calm_with_a_gust_keeps_both_numbers():
    assert kayak_verdict(0, 13, 90)["note"] == "calm (0) gusting 13."
