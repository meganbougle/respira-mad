"""
air_model/features.py - builds the inputs ("features") of our air-quality forecast.

The same code is used to TRAIN the model (train_air_model.py) and to make
forecasts in the app (air_model/predict.py), so both see identical inputs.

A forecast is made at the hour of a station's latest reading (the "issue
hour") for an hour `lead` hours later (1 to 120). It only uses:
  - that station's readings up to the issue hour (official measurements),
  - the calendar (hour of the day, day of the week, time of year),
  - what is typical at that station at that hour (learnt from past years),
  - the weather: what happened up to now, and the weather FORECAST for the
    hour being predicted (Open-Meteo; weather is the only non-official input).
"""

import numpy as np
import pandas as pd

MAX_LEAD = 120                      # hours ahead the model can be asked for (5 days)
POLLUTANTS = ["NO2", "O3", "PM10", "PM2.5"]
WEATHER = ["temp", "wind", "rain", "humidity", "radiation"]            # hourly weather columns
WEATHER_PLUS = WEATHER + ["wind6", "rain6"]                            # + wind and rain over the 6 hours before

FEATURES = [
    "lead", "target_hour", "target_dow", "doy_sin", "doy_cos", "issue_hour", "station",
    # the station's own readings up to the issue hour
    "last", "lag1", "lag2", "mean6", "mean24", "max24", "yday_same_hour", "last_minus_yday",
    # what is typical at this station (same month, weekday/weekend, hour)
    "clim_target", "clim_now",
    # weather forecast for the hour being predicted, and the weather now
    "t_temp", "t_wind", "t_rain", "t_humidity", "t_radiation", "t_wind6", "t_rain6", "now_wind", "now_temp",
]
STATION_COLUMN = FEATURES.index("station")       # a category (which station), not a quantity


def add_weather_windows(weather):
    """Add wind6 (mean wind of the last 6 hours) and rain6 (rain of the last 6 hours) to an hourly weather table."""
    weather = weather.copy()
    weather["wind6"] = weather["wind"].rolling(6, min_periods=1).mean()
    weather["rain6"] = weather["rain"].rolling(6, min_periods=1).sum()
    return weather[WEATHER_PLUS]


def climatology(values, hours):
    """
    What is typical at each station: the mean by month, weekday/weekend and
    hour. Returns an array [station, weekend (0/1), hour, month - 1].
    """
    table = np.full((values.shape[1], 2, 24, 12), np.nan, dtype="float32")
    weekend = (hours.dayofweek >= 5).astype(int)
    frame = pd.DataFrame(values)
    grouped = frame.groupby([weekend, hours.hour, hours.month]).mean()
    for (w, h, m), row in grouped.iterrows():
        table[:, w, h, m - 1] = row.to_numpy()
    # a station with no data for some month/hour gets its own overall mean
    overall = np.nanmean(values, axis=0)
    return np.where(np.isnan(table), overall[:, None, None, None], table)


def baseline(values, pos, station, lead):
    """
    THE BASELINE: "the same hour yesterday" - the reading at the same hour of
    the day on the last day we have (two days back if that one is missing,
    else the latest reading).
    """
    back = 24 * np.ceil(lead / 24).astype(int) - lead           # hours back from the issue hour (0-23)
    guess = values[pos - back, station]
    older = values[np.maximum(pos - back - 24, 0), station]
    guess = np.where(np.isnan(guess), older, guess)
    return np.where(np.isnan(guess), values[pos, station], guess)


def rolling(values):
    """Mean of the last 6 and 24 hours and maximum of the last 24, for every hour and station."""
    frame = pd.DataFrame(values)
    return (frame.rolling(6, min_periods=1).mean().to_numpy(), frame.rolling(24, min_periods=1).mean().to_numpy(),
            frame.rolling(24, min_periods=1).max().to_numpy())


def rows(values, hours, stats, pos, station, lead, clim, weather, weather_start, zone, forecast_run=None, cold=None):
    """
    The feature table for a set of forecasts. Every argument that is an array
    of forecasts has one entry per forecast.

    values   readings [hour, station] (NaN = no reading); hours = its hourly index
    stats    rolling(values)
    pos      position of the issue hour in `hours`;  station = station number;  lead = hours ahead
    clim     climatology() of the training years
    weather  array [run, zone, hour, WEATHER_PLUS]: run 0 = what happened (and, in the app,
             today's forecast for the coming hours); runs 1-5 = the forecast as issued 1-5 days
             before (only used to test honestly on past years)
    weather_start  the hour of weather[..., 0, :];  zone = weather zone of each station number
    forecast_run   which run to read the target hour's weather from (None = run 0)
    cold     optional True/False per forecast: pretend the app has just started and only has
             today's readings (everything before 01:00 today is unknown)
    """
    mean6, mean24, max24 = stats
    issue_time = hours[pos]
    target_time = issue_time + pd.to_timedelta(lead, unit="h")
    back = 24 * np.ceil(lead / 24).astype(int) - lead

    table = {}
    table["lead"] = lead
    table["target_hour"] = target_time.hour
    table["target_dow"] = target_time.dayofweek
    table["doy_sin"] = np.sin(2 * np.pi * target_time.dayofyear / 365.25)
    table["doy_cos"] = np.cos(2 * np.pi * target_time.dayofyear / 365.25)
    table["issue_hour"] = issue_time.hour
    table["station"] = station

    last = values[pos, station]
    yesterday = values[np.maximum(pos - 24, 0), station]
    table["last"] = last
    table["lag1"] = values[np.maximum(pos - 1, 0), station]
    table["lag2"] = values[np.maximum(pos - 2, 0), station]
    table["mean6"] = mean6[pos, station]
    table["mean24"] = mean24[pos, station]
    table["max24"] = max24[pos, station]
    table["yday_same_hour"] = values[pos - back, station]
    table["last_minus_yday"] = last - yesterday

    weekend_target = (target_time.dayofweek >= 5).astype(int)
    weekend_now = (issue_time.dayofweek >= 5).astype(int)
    table["clim_target"] = clim[station, weekend_target, target_time.hour, target_time.month - 1]
    table["clim_now"] = clim[station, weekend_now, issue_time.hour, issue_time.month - 1]

    hours_in = weather.shape[2]
    at_target = np.clip(((target_time - weather_start) / pd.Timedelta(hours=1)).astype(int), 0, hours_in - 1)
    at_issue = np.clip(((issue_time - weather_start) / pd.Timedelta(hours=1)).astype(int), 0, hours_in - 1)
    run = np.zeros(len(pos), dtype=int) if forecast_run is None else forecast_run
    target_weather = weather[run, zone[station], at_target]
    for i, name in enumerate(WEATHER_PLUS):
        table[f"t_{name}"] = target_weather[:, i]
    table["now_wind"] = weather[0, zone[station], at_issue, WEATHER_PLUS.index("wind")]
    table["now_temp"] = weather[0, zone[station], at_issue, WEATHER_PLUS.index("temp")]

    table = pd.DataFrame(table, columns=FEATURES).astype("float32")
    if cold is not None and cold.any():
        # only today's hours are known: readings at 01:00, 02:00 ... up to the issue hour
        known = np.where(issue_time.hour == 0, 24, issue_time.hour)
        table.loc[cold & (known < 2), "lag1"] = np.nan
        table.loc[cold & (known < 3), "lag2"] = np.nan
        table.loc[cold & (known < 24), ["mean24", "max24", "last_minus_yday"]] = np.nan
        table.loc[cold & (known < 6), "mean6"] = np.nan
        table.loc[cold & (back >= known), "yday_same_hour"] = np.nan
    return table
