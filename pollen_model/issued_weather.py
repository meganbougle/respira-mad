"""
pollen_model/issued_weather.py - the weather forecasts AS THEY WERE ISSUED, per
pollen station and day, for testing the pollen models honestly on past years.

    load(station, year, actual)  ->  {k: daily table}  with the forecast made
        k days earlier (k = 1..5) for each day of `year`: columns temp_mean,
        temp_max, rain, wind, humidity (same as the weather that happened).

2025: downloaded per station from Open-Meteo's previous-runs archive by
train_pollen_model.py (files model_data/issued_<station>_2025.csv).

2024: SIMPLIFICATION. That archive became very slow, so 2024 is not downloaded
again per pollen station. Instead it reuses the hourly forecasts already
downloaded for the air model's weather zones (train_air_model.py must have
run first): the station's own weather is shifted by the forecast ERROR of the
nearest zone (rain: the zone's forecast rain). The archive starts on 19
January 2024; before that there is no issued forecast and the scripts fall
back to the weather that happened.
"""

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
CACHE = ROOT / "model_data"
COLUMNS = ["temp_mean", "temp_max", "rain", "wind", "humidity"]


def _daily(hourly):
    """Hourly weather (temp, wind, rain, humidity) -> the daily values the pollen model uses."""
    days = hourly.groupby(hourly.index.normalize())
    return pd.DataFrame({"temp_mean": days["temp"].mean(), "temp_max": days["temp"].max(),
                         "rain": days["rain"].sum(min_count=1), "wind": days["wind"].max(), "humidity": days["humidity"].mean()})


def _from_air_zones(station, year, actual):
    centres = json.loads((ROOT / "air_model" / "meta.json").read_text(encoding="utf-8"))["zone_centres"]
    zones = [z for z in range(len(centres)) if (CACHE / f"air_issued_zone{z}_{year}_k1.csv").exists()]
    if not zones:
        raise RuntimeError("Run train_air_model.py first: it downloads the issued weather forecasts this needs.")
    zone = min(zones, key=lambda z: (centres[z][0] - station["lat"]) ** 2 + (centres[z][1] - station["lon"]) ** 2)
    happened = _daily(pd.read_csv(CACHE / f"air_weather_zone{zone}.csv", parse_dates=["timestamp"])
                      .drop_duplicates("timestamp").set_index("timestamp"))
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    own = actual[COLUMNS].reindex(days)
    parts = []
    for k in range(1, 6):
        issued = _daily(pd.read_csv(CACHE / f"air_issued_zone{zone}_{year}_k{k}.csv", parse_dates=["timestamp"])
                        .drop_duplicates("timestamp").set_index("timestamp")).reindex(days)
        error = issued - happened.reindex(days)
        table = own + error
        table["rain"] = issued["rain"]
        table["wind"] = table["wind"].clip(lower=0)
        table["humidity"] = table["humidity"].clip(0, 100)
        table.insert(0, "k", k)
        parts.append(table.rename_axis("date").reset_index())
    return pd.concat(parts, ignore_index=True)


def load(station, year, actual):
    """
    station: its station_code, lat and lon;  actual: the station's daily weather that happened (index = date).
    Returns {k: table indexed by date}.
    """
    cache = CACHE / f"issued_{station['station_code']}_{year}.csv"
    if not cache.exists():
        _from_air_zones(station, year, actual).to_csv(cache, index=False)
    table = pd.read_csv(cache, parse_dates=["date"])
    return {k: group.drop(columns="k").set_index("date") for k, group in table.groupby("k")}
