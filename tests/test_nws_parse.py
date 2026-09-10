"""Parser tests against small inline NWS-shaped fixtures. No network."""

import pytest

from app.nws import (
    iso_utc,
    parse_forecast,
    parse_forecast_period,
    parse_observation,
    parse_observation_collection,
    parse_sky,
    parse_wind_speed,
)

OBS_PROPS = {
    "timestamp": "2026-09-10T21:53:00+00:00",
    "textDescription": "Partly Cloudy",
    "temperature": {"value": 30.0, "unitCode": "wmoUnit:degC"},
    "dewpoint": {"value": 20.0},
    "relativeHumidity": {"value": 55.4},
    "windDirection": {"value": 160},
    "windSpeed": {"value": 18.36},          # km/h -> ~11.4 mph
    "windGust": {"value": None},
    "barometricPressure": {"value": 101320},
    "visibility": {"value": 16090},
    "cloudLayers": [
        {"base": {"value": 1000}, "amount": "FEW"},
        {"base": {"value": 2000}, "amount": "BKN"},
    ],
}

OBS_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {"properties": OBS_PROPS},
        {"properties": dict(OBS_PROPS, timestamp="2026-09-10T20:53:00Z")},
        {"properties": {"timestamp": None}},   # unusable, must be dropped
    ],
}

FORECAST = {
    "properties": {
        "periods": [
            {
                "startTime": "2026-09-10T17:00:00-05:00",
                "temperature": 88,
                "windSpeed": "5 mph",
                "windDirection": "SE",
                "probabilityOfPrecipitation": {"value": 12},
                "shortForecast": "Sunny",
            },
            {
                "startTime": "2026-09-10T18:00:00-05:00",
                "temperature": 86,
                "windSpeed": "5 to 10 mph",
                "windDirection": "S",
                "probabilityOfPrecipitation": {"value": None},
                "shortForecast": "Chance Showers And Thunderstorms",
            },
        ]
    }
}


# -------------------------------------------------------------- observations

def test_parse_observation_converts_units():
    row = parse_observation(OBS_PROPS)
    assert row["ts"] == "2026-09-10T21:53:00+00:00"
    assert row["temp_f"] == pytest.approx(86.0)
    assert row["dewpt_f"] == pytest.approx(68.0)
    assert row["rh"] == pytest.approx(55.4)
    assert row["wind_dir"] == 160
    assert row["wind_mph"] == pytest.approx(11.408, rel=1e-3)
    assert row["pressure_mb"] == pytest.approx(1013.2)
    assert row["vis_mi"] == pytest.approx(10.0, rel=1e-2)
    assert row["description"] == "Partly Cloudy"


def test_parse_observation_keeps_nulls():
    row = parse_observation(OBS_PROPS)
    assert row["gust_mph"] is None

    sparse = parse_observation({"timestamp": "2026-09-10T21:53:00Z"})
    assert sparse["ts"] == "2026-09-10T21:53:00+00:00"
    assert all(sparse[k] is None for k in
               ("temp_f", "dewpt_f", "rh", "wind_dir", "wind_mph",
                "gust_mph", "pressure_mb", "vis_mi", "sky"))


def test_parse_observation_rejects_missing_timestamp():
    assert parse_observation({}) is None
    assert parse_observation({"timestamp": "not-a-time"}) is None
    assert parse_observation(None) is None


def test_parse_observation_ignores_non_numeric_values():
    row = parse_observation(dict(OBS_PROPS, temperature={"value": "warm"}))
    assert row["temp_f"] is None


def test_parse_sky_picks_densest_layer():
    assert parse_sky(OBS_PROPS) == "BKN"
    assert parse_sky({"cloudLayers": []}) is None
    assert parse_sky({}) is None
    assert parse_sky({"cloudLayers": [{"amount": "OVC"}, {"amount": "FEW"}]}) == "OVC"


def test_parse_collection_drops_bad_features_and_normalizes_tz():
    rows = parse_observation_collection(OBS_COLLECTION)
    assert len(rows) == 2
    assert rows[1]["ts"] == "2026-09-10T20:53:00+00:00"
    assert parse_observation_collection({}) == []
    assert parse_observation_collection(None) == []


def test_iso_utc_normalizes_offsets():
    assert iso_utc("2026-09-10T17:00:00-05:00") == "2026-09-10T22:00:00+00:00"
    assert iso_utc(None) is None


# ------------------------------------------------------------------ forecast

@pytest.mark.parametrize("text,expected", [
    ("5 mph", 5.0),
    ("5 to 10 mph", 10.0),          # take the worse end
    ("0 mph", 0.0),
    ("15 to 25 mph", 25.0),
    (None, None),
    ("calm", None),
    (7, 7.0),
])
def test_parse_wind_speed(text, expected):
    assert parse_wind_speed(text) == expected


def test_parse_forecast_period():
    p = parse_forecast_period(FORECAST["properties"]["periods"][0])
    assert p["start_ts"] == "2026-09-10T22:00:00+00:00"   # normalized to UTC
    assert p["temp_f"] == 88
    assert p["wind_mph"] == 5.0
    assert p["wind_dir"] == "SE"
    assert p["pop"] == 12.0
    assert p["short"] == "Sunny"


def test_parse_forecast_handles_null_pop_and_ranges():
    rows = parse_forecast(FORECAST)
    assert len(rows) == 2
    assert rows[1]["pop"] is None
    assert rows[1]["wind_mph"] == 10.0


def test_parse_forecast_empty_payloads():
    assert parse_forecast({}) == []
    assert parse_forecast(None) == []
    assert parse_forecast_period({}) is None
