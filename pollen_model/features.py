"""
pollen_model/features.py - builds the inputs ("features") of our pollen model.

The same code is used to TRAIN the model (train_pollen_model.py) and to make
forecasts in the app (pollen_model/predict.py), so both see identical inputs.

THE GOLDEN RULE: a forecast made on day D may only use what is known on day D.
  - Pollen readings: only readings at least `delay` days old (3 by default),
    because the network publishes them several days late.
  - Weather up to day D: observed.
  - Weather of the days being forecast: a weather FORECAST. (When training on
    old years we do not have the old forecasts, so the weather that actually
    happened stands in for them. The 2025 test uses the real archived
    forecasts, so the test is not flattered by hindsight.)
"""

import numpy as np
import pandas as pd

import config

LEADS = [0, 1, 2, 3, 4, 5]          # 0 = the day the forecast is made, 5 = five days later
READING_DELAY = 3                   # pollen readings are at least 3 days old
MAX_CARRY_DAYS = 10                 # how long an old reading may still be used
WARMTH_BASE = 5.0                   # degrees C: warmth counts above this (growing degree days)
POLLEN_TYPES = list(config.POLLEN_THRESHOLDS)

# SIMPLIFICATION: pollen stations that are very close to another one share that
# one's weather series (the weather archive's grid is 9-25 km wide, so nearby
# stations get almost the same numbers anyway, and it keeps us inside
# Open-Meteo's free download limits). Distances: Arganzuela and Ciudad
# Universitaria are 4 km from Barrio de Salamanca, Coslada 10 km, and Leganés
# 3 km from Getafe.
WEATHER_FROM = {"AYTM": "ALER", "FACF": "ALER", "COSL": "ALER", "LEGA": "GETA"}

FEATURES = [
    "day_of_year", "day_sin", "day_cos",
    # pollen (at least `delay` days old)
    "last_value", "last_age", "mean_7", "max_7", "mean_14", "trend", "network_mean_3", "same_day_last_year",
    # weather known on day D
    "warmth_since_jan1", "warmth_since_jan1_base0", "rain_7", "rain_14", "rain_30", "rain_90", "temp_mean_7",
    # weather forecast for the target day and the days in between
    "target_temp_mean", "target_temp_max", "target_rain", "target_wind", "target_humidity",
    "between_rain", "between_temp_mean",
    # which station and how far ahead
    "station_number", "lead",
]


def level_of(pollen_type, values):
    """Official level 0-3 (bajo, medio, alto, muy alto) for an array of grains/m3."""
    thresholds = config.POLLEN_THRESHOLDS[pollen_type]
    values = np.asarray(values, dtype=float)
    level = np.zeros(len(values))
    for threshold in thresholds:
        level += values >= threshold
    return np.where(np.isnan(values), np.nan, level)


def weather_features(weather):
    """
    Add the "known on day D" weather columns to one station's daily weather
    (columns: date, temp_mean, temp_max, rain, wind, humidity; one row per day).
    """
    weather = weather.sort_values("date").reset_index(drop=True).copy()
    year = weather["date"].dt.year
    warm5 = (weather["temp_mean"] - WARMTH_BASE).clip(lower=0).fillna(0)
    warm0 = weather["temp_mean"].clip(lower=0).fillna(0)
    weather["warmth_since_jan1"] = warm5.groupby(year).cumsum()          # restarts every 1 January
    weather["warmth_since_jan1_base0"] = warm0.groupby(year).cumsum()
    for days in (7, 14, 30, 90):
        weather[f"rain_{days}"] = weather["rain"].rolling(days, min_periods=1).sum()
    weather["temp_mean_7"] = weather["temp_mean"].rolling(7, min_periods=1).mean()
    return weather


def station_rows(station_number, pollen, network, weather, pollen_type, lead, delay=READING_DELAY,
                 forecast_weather=None):
    """
    One row per day D for one station, one pollen type and one lead.

    pollen   daily readings of this type at this station (index = every date, NaN = no reading)
    network  the average of this type over all stations (same index)
    weather  the station's daily weather with weather_features() already applied (index = date)
    forecast_weather  optional: {k: daily table} with the weather forecast issued k days
             before each date (used for the honest 2025 test and for live forecasts).
             If None, the weather that happened is used.
    Returns a table indexed by D with the FEATURES columns plus `target_value`.
    """
    dates = pollen.index
    rows = pd.DataFrame(index=dates)
    target_dates = dates + pd.Timedelta(days=lead)

    doy = target_dates.dayofyear.to_numpy()
    rows["day_of_year"] = doy
    rows["day_sin"] = np.sin(2 * np.pi * doy / 365.25)
    rows["day_cos"] = np.cos(2 * np.pi * doy / 365.25)

    # ---- pollen: only what was published by day D (readings up to D - delay)
    known = pollen.shift(delay)                                   # the value of day D-delay, seen on day D
    has_reading = known.notna()
    days_since = (~has_reading).groupby(has_reading.cumsum()).cumsum()   # days since the last reading
    carried = known.ffill(limit=MAX_CARRY_DAYS)
    rows["last_value"] = carried
    rows["last_age"] = np.where(carried.notna(), delay + days_since, np.nan)
    rows["mean_7"] = known.rolling(7, min_periods=1).mean()
    rows["max_7"] = known.rolling(7, min_periods=1).max()
    rows["mean_14"] = known.rolling(14, min_periods=1).mean()
    rows["trend"] = known.rolling(3, min_periods=1).mean() - known.shift(3).rolling(3, min_periods=1).mean()
    rows["network_mean_3"] = network.shift(delay).rolling(3, min_periods=1).mean()
    rows["same_day_last_year"] = pollen.rolling(7, min_periods=1, center=True).mean().shift(365 + lead * -1)

    # ---- weather known on day D
    known_weather = weather.reindex(dates)
    for column in ("warmth_since_jan1", "warmth_since_jan1_base0", "rain_7", "rain_14", "rain_30", "rain_90", "temp_mean_7"):
        rows[column] = known_weather[column].to_numpy()

    # ---- weather forecast for the target day, and for the days between D and the target
    def issued(k):
        """Daily weather for each date as forecast k days earlier (k=0: what happened)."""
        if forecast_weather is not None and k in forecast_weather:
            return forecast_weather[k]
        return weather

    target = issued(lead).reindex(target_dates)
    rows["target_temp_mean"] = target["temp_mean"].to_numpy()
    rows["target_temp_max"] = target["temp_max"].to_numpy()
    rows["target_rain"] = target["rain"].to_numpy()
    rows["target_wind"] = target["wind"].to_numpy()
    rows["target_humidity"] = target["humidity"].to_numpy()
    between_rain = np.zeros(len(dates))
    between_temp = np.zeros(len(dates))
    for k in range(1, lead + 1):
        day_k = issued(k).reindex(dates + pd.Timedelta(days=k))
        between_rain += day_k["rain"].to_numpy()
        between_temp += day_k["temp_mean"].to_numpy()
    rows["between_rain"] = between_rain
    rows["between_temp_mean"] = between_temp / lead if lead else rows["target_temp_mean"]

    rows["station_number"] = station_number
    rows["lead"] = lead
    # what we are trying to predict: the reading of day D + lead
    rows["target_value"] = pollen.shift(-lead).to_numpy()
    return rows


def predict_quantiles(models, rows, grains=True, widen=0.0):
    """
    The median forecast and the 80% prediction interval for some feature rows.
    `models` is {"mean": ..., "q10": ..., "q50": ..., "q90": ...} for one
    pollen type and lead day (made by compare_models.py): "mean" predicts the
    average of log(1 + grains) and the other three predict how far from it
    the 10%, 50% and 90% points are.
    `widen` is the calibration margin (log scale): the interval is widened by
    that much on each side so that it really covers 80% (see compare_models.py).
    Returns a table with columns q10, q50, q90: grains/m3, or the log scale if grains=False.
    """
    mean = models["mean"].predict(rows)
    median = mean + models["q50"].predict(rows)
    low = np.minimum(mean + models["q10"].predict(rows) - widen, median)        # never above the median
    high = np.maximum(mean + models["q90"].predict(rows) + widen, median)       # never below it
    table = pd.DataFrame({"q10": low, "q50": median, "q90": high}).clip(lower=0)
    return np.expm1(table).round(1) if grains else table
