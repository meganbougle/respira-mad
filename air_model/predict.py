"""
air_model/predict.py - makes the air-quality forecast in the app with OUR model.

DATA RULE: it only uses the official hourly measurements of the Comunidad de
Madrid and the Ayuntamiento de Madrid, plus the weather (Open-Meteo).

    forecast(demo, moment)  ->  one row per station, pollutant and hour:
        station_code, station, source, lat, lon, pollutant, issued (the
        station's latest reading), timestamp (the hour forecast), lead
        (hours ahead), value (ug/m3). Only as far ahead as the model clearly
        beat the baseline when tested (horizon_hours(), see MODEL_REPORT.md).

    at_point(lat, lon, table)  ->  the hourly forecast for one place: for
        each pollutant, the nearest station that has a forecast (the same
        rule the app uses for measurements).

The model is trained by train_air_model.py and stored in air_model/.
"""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import config
import data
import results
import weather
from air_model import features as F

FOLDER = Path(__file__).parent
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = {"temperature_2m": "temp", "wind_speed_10m": "wind", "precipitation": "rain",
          "relative_humidity_2m": "humidity", "shortwave_radiation": "radiation"}
RECENT_HOURS = 72                   # how many hours of readings the app keeps to make forecasts
MAX_READING_AGE = 6                 # a station whose latest reading is older than this (hours) gets no forecast
SOURCE = "Modelo propio de Respira MAD, con mediciones oficiales de la Comunidad y del Ayuntamiento de Madrid"

_models = None


def models():
    """The trained models, loaded once: {pollutant: model}."""
    global _models
    if _models is None:
        import joblib          # only where the forecast is computed (precompute.py), never on the server
        _models = joblib.load(FOLDER / "models.joblib")
    return _models


@st.cache_data(show_spinner=False)
def meta():
    """Facts about the saved model: stations, weather zones, how far ahead it may be shown."""
    return json.loads((FOLDER / "meta.json").read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _climatology():
    saved = np.load(FOLDER / "climatology.npz")
    return {pollutant: saved[pollutant.replace(".", "_")] for pollutant in F.POLLUTANTS}


def horizon_hours():
    """How many hours ahead the air forecast is shown (0 = not shown at all)."""
    return int(meta()["horizon_hours"])


# ----------------------------------------------------------------------------
# What the model needs: the last three days of readings and the weather
# ----------------------------------------------------------------------------

def _fetch_recent():
    """
    Hourly readings of the last RECENT_HOURS hours, every station of both networks.
    The Comunidad publishes the whole current month; the Ayuntamiento's
    real-time file only has today, so the app also keeps what it has already
    seen (backup/air_recent.csv) and adds the new hours to it.
    """
    now = data._madrid_now()
    parts = []

    def last_days(table):
        """MEMORY: the month file grows all month; keep only the days within RECENT_HOURS."""
        day = pd.to_datetime(table["ano"] + "-" + table["mes"] + "-" + table["dia"], errors="coerce")
        return table[day >= (now - pd.Timedelta(hours=RECENT_HOURS)).normalize()]

    for fetch in (lambda: last_days(data._read_csv(data._ckan_csv_url("calidad_aire_datos_mes"))),  # Comunidad: month so far
                  lambda: data._read_csv(data._ckan_csv_url("calidad_aire_datos_dia")),      # Comunidad: today (newest hours)
                  lambda: data._read_csv(data.CITY_AIR_URL)):                                # Ayuntamiento: today
        try:
            parts.append(data._hourly_long(fetch(), data.POLLUTANTS, now)[["station_code", "name", "value", "timestamp"]])
        except Exception:
            continue
    if not parts:
        raise RuntimeError("neither air network answered")
    stored = data.BACKUP_DIR / "air_recent.csv"
    if stored.exists():
        parts.append(pd.read_csv(stored, parse_dates=["timestamp"], dtype={"station_code": str})
                     .rename(columns={"pollutant": "name"})[["station_code", "name", "value", "timestamp"]])
    table = pd.concat(parts, ignore_index=True).rename(columns={"name": "pollutant"})
    table = table.drop_duplicates(["station_code", "pollutant", "timestamp"])
    table = table[(table["timestamp"] > now - pd.Timedelta(hours=RECENT_HOURS)) & (table["timestamp"] <= now)]
    return table.sort_values(["station_code", "pollutant", "timestamp"]).reset_index(drop=True)


def _fetch_weather():
    """Hourly weather of the 8 zones: the last 3 days and the forecast for the coming days (one request)."""
    centres = meta()["zone_centres"]
    days_ahead = min(horizon_hours() // 24 + 2, 16)
    # one request for the 8 zones, retried with backoff if Open-Meteo is busy (weather.py)
    answer = weather.get_json(FORECAST_URL, retries=1, timeout=weather.VISITOR_TIMEOUT, params={
        "latitude": ",".join(str(lat) for lat, _ in centres), "longitude": ",".join(str(lon) for _, lon in centres),
        "hourly": ",".join(HOURLY), "timezone": "Europe/Madrid", "past_days": 3, "forecast_days": days_ahead})
    answer = answer if isinstance(answer, list) else [answer]
    tables = []
    for zone, one in enumerate(answer):
        table = pd.DataFrame(one["hourly"]).rename(columns={"time": "timestamp", **HOURLY})
        table["zone"] = zone
        tables.append(table)
    table = pd.concat(tables, ignore_index=True)
    table["timestamp"] = pd.to_datetime(table["timestamp"])
    return table


def _predict(recent, weather_table, now):
    """Run the models for every station and pollutant that has a recent reading."""
    info = meta()
    horizon = horizon_hours()
    stations = pd.DataFrame(info["stations"])
    stations["station_code"] = stations["station_code"].astype(str)
    number = {code: i for i, code in enumerate(stations["station_code"])}
    zone = stations["zone"].to_numpy()
    hours = pd.date_range(now.floor("h") - pd.Timedelta(hours=RECENT_HOURS), now.floor("h"), freq="h")

    # weather[0, zone, hour, variable] on its own hourly clock
    weather_hours = pd.date_range(weather_table["timestamp"].min(), weather_table["timestamp"].max(), freq="h")
    weather = np.full((1, len(info["zone_centres"]), len(weather_hours), len(F.WEATHER_PLUS)), np.nan, dtype="float32")
    for z, group in weather_table.groupby("zone"):
        one = group.drop_duplicates("timestamp").set_index("timestamp")[F.WEATHER].reindex(weather_hours).ffill().bfill()
        weather[0, z] = F.add_weather_windows(one).to_numpy()

    out = []
    leads = np.arange(1, horizon + 1)
    for pollutant in F.POLLUTANTS:
        one = recent[(recent["pollutant"] == pollutant) & recent["station_code"].isin(info["measuring"][pollutant])]
        if one.empty:
            continue
        wide = one.pivot(index="timestamp", columns="station_code", values="value").reindex(hours)
        values = np.full((len(hours), len(stations)), np.nan, dtype="float32")
        for code in wide.columns:
            values[:, number[code]] = wide[code].to_numpy()
        stats = F.rolling(values)
        pos, station = [], []
        for code in wide.columns:                      # each station forecasts from its own latest reading
            valid = np.where(np.isfinite(values[:, number[code]]))[0]
            if len(valid) and len(hours) - 1 - valid[-1] <= MAX_READING_AGE:
                pos.append(valid[-1])
                station.append(number[code])
        if not pos:
            continue
        pos = np.repeat(np.array(pos), len(leads))
        station = np.repeat(np.array(station), len(leads))
        lead = np.tile(leads, len(pos) // len(leads))
        # "cold": the station has no reading from before today, so yesterday's values are unknown
        midnight = np.searchsorted(hours, hours[pos].normalize() + pd.Timedelta(hours=1))
        cold = np.array([not np.isfinite(values[:m, s]).any() for m, s in zip(midnight, station)])
        table = F.rows(values, hours, stats, pos, station, lead, _climatology()[pollutant], weather, weather_hours[0], zone, cold=cold)
        out.append(pd.DataFrame({
            "station_code": stations["station_code"].to_numpy()[station], "pollutant": pollutant,
            "issued": hours[pos], "timestamp": hours[pos] + pd.to_timedelta(lead, unit="h"), "lead": lead,
            "value": np.clip(np.expm1(models()[pollutant].predict(table.to_numpy())), 0, None).round(1)}))
    if not out:
        return pd.DataFrame()
    table = pd.concat(out, ignore_index=True)
    return table.merge(stations[["station_code", "station", "source", "lat", "lon"]], on="station_code", how="left")


def _zone_weather():
    """The zones' weather: the saved copy if it is less than 1 hour old, otherwise a new download (with backup)."""
    saved = data.BACKUP_DIR / "air_weather.csv"
    if saved.exists() and time.time() - saved.stat().st_mtime < weather.FRESH_SECONDS:
        return pd.read_csv(saved, parse_dates=["timestamp"])
    return data._with_backup("air_weather", _fetch_weather)


def _fetch_live_forecast():
    if horizon_hours() == 0:
        raise RuntimeError("the air forecast did not beat the baseline, so it is not shown")
    recent = _fetch_recent()
    recent.to_csv(data.BACKUP_DIR / "air_recent.csv", index=False)          # remember these hours for next time
    weather_table = _zone_weather()
    return _predict(recent, weather_table, data._madrid_now())


def _live_forecast():
    """
    The latest forecast computed by precompute.py (GitHub Actions, every hour),
    read by results.py. The server never runs the model (memory, see config.py).
    """
    return results.read("air_forecast")


@st.cache_data(show_spinner=False)
def _demo_forecast(day_text):
    """The forecasts stored for a demo day, made by train_air_model.py with only what was known then."""
    table = pd.read_csv(data.BACKUP_DIR / "replay" / f"air_forecast_{day_text}.csv",
                        parse_dates=["issued", "timestamp"], dtype={"station_code": str})
    stations = pd.DataFrame(meta()["stations"])
    stations["station_code"] = stations["station_code"].astype(str)
    return table.merge(stations[["station_code", "station", "source", "lat", "lon"]], on="station_code", how="left")


def forecast(demo=False, moment=None):
    """
    Our air forecast for every station (see the top of this file).
    demo=True: the forecast as it would have been made at `moment` on the demo
    day (28 May 2025, hour by hour) or on the planner's demo day (24 May 2025, 07:00).
    """
    if not demo:
        table = _live_forecast()
        now = data._madrid_now()
        table = table[table["timestamp"] > now.floor("h") - pd.Timedelta(hours=1)]
        return _drop_unusable(table, now)
    table = _demo_forecast(moment.strftime("%Y-%m-%d"))
    issued = table[table["issued"] == moment.floor("h")]
    issued.attrs["live"] = False
    issued.attrs["fetched_at"] = moment
    return issued


def _drop_unusable(table, now):
    """
    Leave out the forecasts that start from a reading the app does not trust
    (same rules as the measurements, config.py): a station + pollutant whose
    latest reading is a "Dato dudoso", or more than config.STALE_HOURS older
    than the newest reading the forecast run had (a station that was already
    lagging when the forecast was made). at_point() then takes the next
    nearest station.

    The comparison is with the run's newest reading, NOT with now: GitHub
    Actions often runs hours late (26 Nov 2026: last run at 05:56, nothing
    until after 09:30), and comparing with now threw away the WHOLE forecast.
    A forecast issued at 04:00 is still our tested forecast for 12:00 (8 hours
    ahead, inside the 96-hour horizon); the freshness box says when it was computed.
    """
    if table.empty:
        return table
    newest = table["issued"].max()
    keep = table["issued"] >= newest - pd.Timedelta(hours=config.STALE_HOURS)
    try:
        air = data.load_air()
        doubtful = air[air["doubtful"]]
        for _, row in doubtful.iterrows():
            keep &= ~((table["station"] == row["station"]) & (table["source"] == row["source"])
                      & (table["pollutant"] == row["pollutant"]))
    except Exception:
        pass                                         # without the measurements, keep the forecast as it is
    kept = table[keep]
    kept.attrs.update(table.attrs)
    return kept


def measured_at_point(lat, lon, now):
    """
    The MEASURED hourly values of today (midnight to now) for one place: one
    row per hour, one column per pollutant, each pollutant from the nearest
    station that measures it and whose reading is usable (not a "Dato dudoso",
    not older than config.STALE_HOURS). Used for the planner's past and
    current parts of today. The hours come from backup/air_recent.csv, which
    the forecast keeps up to date (GitHub Actions) plus today's live hourly
    files (data.load_air_today_hourly), so they are current even when Actions
    runs late. `.attrs["stations"]` names the station per pollutant.
    Returns None if there is nothing.
    """
    import risk                                      # (imported here because risk.py also uses this file)
    parts = []
    stored = data.BACKUP_DIR / "air_recent.csv"
    if stored.exists():
        parts.append(pd.read_csv(stored, parse_dates=["timestamp"], dtype={"station_code": str}))
    try:
        parts.append(data.load_air_today_hourly().astype({"station_code": str}))
    except Exception as error:
        data.problem("Sin mediciones horarias de hoy.", error)
    if not parts:
        return None
    hours = pd.concat([p[["station_code", "pollutant", "value", "timestamp"]] for p in parts], ignore_index=True)
    # the live file comes last, so keep="last" prefers it if the same hour is in both
    hours = hours.drop_duplicates(["station_code", "pollutant", "timestamp"], keep="last")
    hours = hours[(hours["timestamp"] > now.normalize()) & (hours["timestamp"] <= now)]
    if hours.empty:
        return None
    stations = pd.DataFrame(meta()["stations"])
    stations["station_code"] = stations["station_code"].astype(str)
    hours = hours.merge(stations[["station_code", "station", "source", "lat", "lon"]], on="station_code")
    doubtful = set()
    try:
        air = data.load_air()
        doubtful = set(zip(air.loc[air["doubtful"], "station"], air.loc[air["doubtful"], "source"],
                           air.loc[air["doubtful"], "pollutant"]))
    except Exception:
        pass
    columns, used = {}, {}
    for pollutant, group in hours.groupby("pollutant"):
        if pollutant not in config.ICA_BANDS:
            continue
        best = None
        for (code, station, source, la, lo), one in group.groupby(["station_code", "station", "source", "lat", "lon"]):
            if (station, source, pollutant) in doubtful or risk.is_stale(one["timestamp"].max(), now):
                continue
            distance = risk.distance_km(lat, lon, la, lo)
            if best is None or distance < best[0]:
                best = (distance, station, source, one)
        if best is not None:
            columns[pollutant] = best[3].set_index("timestamp")["value"]
            used[pollutant] = {"station": best[1], "source": best[2], "distance_km": round(best[0], 1)}
    if not columns:
        return None
    table = pd.DataFrame(columns).sort_index()
    table = table.reset_index().rename(columns={"index": "timestamp"})
    table.attrs["stations"] = used
    return table


def at_point(lat, lon, table):
    """
    The hourly forecast for one place: a table with one row per hour and one
    column per pollutant (NO2, O3, PM10, PM2.5), taking each pollutant from
    the nearest station that has a forecast. `.attrs["stations"]` says which
    station was used for each pollutant and how far it is.
    Returns None if there is no forecast at all.
    """
    if table is None or table.empty:
        return None
    import risk                                      # (imported here because risk.py also uses this file)
    columns, used = {}, {}
    for pollutant, group in table.groupby("pollutant"):
        places = group.drop_duplicates("station_code")
        distance = [risk.distance_km(lat, lon, la, lo) for la, lo in zip(places["lat"], places["lon"])]
        nearest = places.iloc[int(np.argmin(distance))]
        one = group[group["station_code"] == nearest["station_code"]]
        columns[pollutant] = one.set_index("timestamp")["value"]
        used[pollutant] = {"station": nearest["station"], "source": nearest["source"],
                           "distance_km": round(float(min(distance)), 1), "issued": one["issued"].iloc[0]}
    hourly = pd.DataFrame(columns).sort_index()
    for pollutant in config.ICA_BANDS:
        if pollutant not in hourly:
            hourly[pollutant] = np.nan
    hourly = hourly.reset_index().rename(columns={"index": "timestamp"})
    hourly["kind"] = data.FORECAST_LABEL
    hourly.attrs["stations"] = used
    hourly.attrs["live"] = table.attrs.get("live", True)
    hourly.attrs["fetched_at"] = table.attrs.get("fetched_at")
    return hourly
