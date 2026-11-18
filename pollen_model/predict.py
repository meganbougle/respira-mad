"""
pollen_model/predict.py - makes pollen forecasts in the app with OUR model.

    forecast(now, demo)  ->  table with one row per station, pollen type and day:
        station, station_code, lat, lon, pollen_type, issue_date, target_date,
        lead (0 = today ... 5),
        q50 (grains/m3: the median forecast) and q10, q90 (an 80% prediction
        interval, calibrated so that it really covers about 80%),
        predicted_level (0-3): ALWAYS the median read against the official
        thresholds, so the planner, the chart and the assistant agree,
        reliable (did this type + lead day clearly beat the baseline in 2024?),
        accuracy (share of in-season days of 2025, the test year, it got right)

    recent_readings(demo)  ->  the official readings of the last two weeks at
        every station (for the forecast chart).

    Each pollen type uses the model that won the 2025 comparison (see
    MODEL_REPORT.md and model_choice.json): gradient boosting for most, ARIMAX
    for a few. Column `model` says which ("gb" or "arimax").

    best_source(...) gives, for one pollen type and day, our model's level if
    it is RELIABLE, otherwise nothing ("Sin previsión fiable").

The models are made by compare_models.py (choice per type, calibrated
interval margins, RELIABLE flags: model_choice.json; gradient boosting:
quantile_models.joblib; ARIMAX: arimax_params.json). This file only uses them.
(pollen_model/models.joblib, the old classifier from train_pollen_model.py, is
no longer used by the app: it is kept only for the comparison in the report.)
"""

import json
import re
import unicodedata
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
import streamlit as st

import config
import data
import weather
from pollen_model import arimax as A
from pollen_model import features as F

FOLDER = Path(__file__).parent
SIX_HOURS = 6 * 60 * 60
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY = {"temperature_2m_mean": "temp_mean", "temperature_2m_max": "temp_max", "precipitation_sum": "rain",
         "wind_speed_10m_max": "wind", "relative_humidity_2m_mean": "humidity"}

OUR_MODEL = "Modelo propio de Respira MAD"

RECENT_DAYS = 14                    # how many days of readings recent_readings() keeps

_last_readings = None               # the readings downloaded for the latest forecast (reused by recent_readings)

# MEMORY (18 Nov 2026, Render free plan = 512 MB): all 240 quantile models at
# once take about 260 MB in memory, one pollen type's 24 take about 27 MB. So
# compare_models.py saves one file per pollen type, and the forecast loads a
# type's file, uses it and lets it go before the next type.
QUANTILE_FOLDER = FOLDER / "quantile_models"


def type_file(pollen_type):
    """The file with one pollen type's quantile models, e.g. quantile_models/olivo.joblib."""
    slug = unicodedata.normalize("NFKD", pollen_type).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return QUANTILE_FOLDER / f"{slug}.joblib"


def type_models(pollen_type):
    """
    One pollen type's quantile models, made by compare_models.py:
    {lead: {"mean": model, "q10": model, "q50": model, "q90": model}} (see
    features.predict_quantiles). Not kept: the caller drops it when done.
    """
    return joblib.load(type_file(pollen_type))


@st.cache_data(show_spinner=False)
def model_choice():
    """Which model was chosen (on 2024) for each pollen type, and its test results (see MODEL_REPORT.md)."""
    file = FOLDER / "model_choice.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"choice": {}}


@st.cache_data(show_spinner=False)
def arimax_models():
    """
    The fitted ARIMAX models for the pollen types where ARIMAX won:
    {"models": {pollen type: {station: {order, fourier_pairs, params}}}, "results": [...]}.
    """
    file = FOLDER / "arimax_params.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {"models": {}, "results": []}


@st.cache_data(show_spinner=False)
def meta():
    """Facts about the saved model, including the 2025 test results."""
    return json.loads((FOLDER / "meta.json").read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def test_results():
    """
    What the tests said about each pollen type, lead day and model ("gb" or "arimax"):
    {(pollen type, lead, model): {"reliable", "accuracy", "margin"}}
      reliable  decided on 2024 (did it clearly beat the baseline?)
      accuracy  share of in-season days of 2025 (the untouched test year) with the right level
      margin    how much the 80% interval is widened so that it really covers 80% (calibrated on 2024)
    """
    return {(r["pollen_type"], r["lead"], r["model"]): {"reliable": bool(r["reliable"]), "accuracy": r["accuracy_2025"],
                                                        "margin": r["margin"]}
            for r in model_choice().get("results", [])}


def _arimax_rows(pollen_type, code, readings, weather, issue_day):
    """
    ARIMAX forecast for one station and pollen type, as of `issue_day`:
    {lead: {"q10", "q50", "q90"}} in grains/m3, or {} if this station has no fitted model.
    It only sees readings up to issue_day - 3 days; the weather of the coming days is the weather forecast.
    """
    fitted = arimax_models()["models"].get(pollen_type, {}).get(code)
    if fitted is None:
        return {}
    last = issue_day - pd.Timedelta(days=F.READING_DELAY)
    window_days = pd.date_range(last - pd.Timedelta(days=A.WINDOW_DAYS - 1), last, freq="D")
    future_days = pd.date_range(last + pd.Timedelta(days=1), issue_day + pd.Timedelta(days=max(F.LEADS)), freq="D")
    window = np.log1p(readings.reindex(window_days))
    mean, error = A.forecast(window, weather[A.WEATHER].reindex(window_days), weather[A.WEATHER].reindex(future_days),
                             fitted["order"], fitted["fourier_pairs"], fitted["params"])
    rows = {}
    for lead in F.LEADS:
        i = F.READING_DELAY - 1 + lead                     # position of day D+lead among the forecast days
        if not np.isfinite(error[i]) or error[i] > A.MAX_LOG_ERROR:
            continue                                       # unstable: gradient boosting answers for this day
        margin = test_results().get((pollen_type, lead, "arimax"), {}).get("margin", 0.0)
        low, middle, high = np.expm1(A.quantiles(mean[i], error[i], margin))
        rows[lead] = {"q10": round(float(low), 1), "q50": round(float(middle), 1), "q90": round(float(high), 1)}
    return rows


# ----------------------------------------------------------------------------
# What the model needs every day: recent pollen readings and each station's weather
# ----------------------------------------------------------------------------

def _fetch_recent_pollen():
    """Readings of this year and last year for the 10 pollen types with thresholds."""
    # only the columns used below (MEMORY: these files are large)
    columns = {"captador", "tipo_polinico", "granos_de_polen_x_metro_cubico", "fecha_lectura"}
    tables = [data._read_csv(data._ckan_csv_url("mediciones_polen"), columns=columns)]
    try:                                            # last year, for the "same day last year" input
        listing = requests.get(data.CKAN_API + "package_show", params={"id": "mediciones_polen_historico"},
                               timeout=data.TIMEOUT).json()["result"]["resources"]
        last_year = str(data._madrid_now().year - 1)
        url = next(r["url"] for r in listing if r["name"] == last_year and r["format"].upper() == "CSV")
        tables.append(data._read_csv(url, columns=columns))
    except Exception:
        pass                                        # the model copes without it
    readings = pd.concat(tables, ignore_index=True)
    readings = readings[readings["tipo_polinico"].isin(F.POLLEN_TYPES)]
    readings["value"] = data._to_number(readings["granos_de_polen_x_metro_cubico"])
    readings["timestamp"] = pd.to_datetime(readings["fecha_lectura"].str[:10].str.replace("/", "-"))
    readings = readings.dropna(subset=["value"]).rename(columns={"captador": "station_code", "tipo_polinico": "pollen_type"})
    readings = readings.drop_duplicates(subset=["station_code", "timestamp", "pollen_type"])
    readings = readings[readings["timestamp"] >= data._madrid_now().normalize() - pd.Timedelta(days=420)]
    global _last_readings
    _last_readings = readings[["station_code", "pollen_type", "value", "timestamp"]].reset_index(drop=True)
    return _last_readings


def _fetch_stations_weather(points):
    """
    Daily weather at several stations up to 6 days ahead (observed, then forecast),
    in TWO batched requests for all of them (weather.py retries if Open-Meteo is busy).
    It starts on 1 January (the "warmth since 1 January" input needs it) or
    about 4 months ago if that is earlier (the ARIMAX model looks back 120 days).
    points: list of (lat, lon). Returns one table per point, in the same order.
    """
    today = data._madrid_now().normalize()
    lats = ",".join(str(lat) for lat, _ in points)
    lons = ",".join(str(lon) for _, lon in points)
    recent = weather.get_json(FORECAST_URL, {
        "latitude": lats, "longitude": lons, "daily": ",".join(DAILY), "timezone": "Europe/Madrid",
        "past_days": 14, "forecast_days": 7})
    recent = recent if isinstance(recent, list) else [recent]
    start_of_year = min(pd.Timestamp(year=today.year, month=1, day=1), today - pd.Timedelta(days=A.WINDOW_DAYS + 10))
    older_end = today - pd.Timedelta(days=15)
    older = [None] * len(points)
    if older_end > start_of_year:                   # the months before: historical weather
        older = weather.get_json(ARCHIVE_URL, {
            "latitude": lats, "longitude": lons, "daily": ",".join(DAILY), "timezone": "Europe/Madrid",
            "start_date": start_of_year.strftime("%Y-%m-%d"), "end_date": older_end.strftime("%Y-%m-%d")})
        older = older if isinstance(older, list) else [older]
    tables = []
    for new_part, old_part in zip(recent, older):
        parts = ([pd.DataFrame(old_part["daily"])] if old_part else []) + [pd.DataFrame(new_part["daily"])]
        table = pd.concat(parts, ignore_index=True).rename(columns={"time": "timestamp", **DAILY})
        table["timestamp"] = pd.to_datetime(table["timestamp"])
        tables.append(table.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True))
    return tables


def _predict(readings, weather_by_station, stations, issue_day, leads=None, only=None):
    """Run the models for every station, type and lead (or only `leads`, only the station codes in `only`), as of `issue_day`."""
    leads = F.LEADS if leads is None else leads
    station_number = meta()["station_number"]
    all_days = pd.date_range(issue_day - pd.Timedelta(days=420), issue_day + pd.Timedelta(days=max(F.LEADS)), freq="D")
    results = test_results()
    rows = []
    for pollen_type in F.POLLEN_TYPES:
        models = None                       # this type's models, loaded only if gradient boosting is needed
        one = readings[readings["pollen_type"] == pollen_type]
        wide = one.pivot(index="timestamp", columns="station_code", values="value").reindex(all_days)
        network = wide.mean(axis=1)
        use_arimax = model_choice()["choice"].get(pollen_type) == "arimax"
        for code in wide.columns:
            if code not in weather_by_station or code not in station_number or (only is not None and code not in only):
                continue
            arimax = {}
            if use_arimax:
                try:
                    arimax = _arimax_rows(pollen_type, code, wide[code], weather_by_station[code], issue_day)
                except Exception:
                    arimax = {}                     # if ARIMAX fails for this station, gradient boosting still answers
            for lead in leads:
                table = F.station_rows(station_number[code], wide[code], network, weather_by_station[code],
                                       pollen_type, lead, F.READING_DELAY)
                row = table.loc[[issue_day]]
                if pd.isna(row["last_value"].iloc[0]):
                    continue                        # no reading in the last ~2 weeks: no forecast for this station
                if lead in arimax:
                    prediction = {**arimax[lead], "model": "arimax"}
                else:                               # gradient boosting: the median and the calibrated 80% interval
                    margin = results.get((pollen_type, lead, "gb"), {}).get("margin", 0.0)
                    if models is None:
                        models = type_models(pollen_type)
                    prediction = {"model": "gb", **F.predict_quantiles(models[lead], row[F.FEATURES],
                                                                        widen=margin).iloc[0].to_dict()}
                # ONE SOURCE OF TRUTH: the level is the median forecast read against the official thresholds
                prediction["predicted_level"] = int(F.level_of(pollen_type, [prediction["q50"]])[0])
                result = results.get((pollen_type, lead, prediction["model"]), {"reliable": False, "accuracy": None})
                rows.append({
                    "station_code": code, "pollen_type": pollen_type, "issue_date": issue_day,
                    "target_date": issue_day + pd.Timedelta(days=lead), "lead": lead, **prediction,
                    "reliable": result["reliable"], "accuracy": result["accuracy"],
                    "last_value": float(row["last_value"].iloc[0]), "last_age": int(row["last_age"].iloc[0]),
                })
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    return table.merge(stations, on="station_code", how="left")


def _fetch_live_forecast():
    readings = _fetch_recent_pollen()
    traps = data._read_csv(data._ckan_csv_url("captadores_polen"), encoding="latin-1")
    stations = pd.DataFrame({"station_code": traps["codigo"], "station": traps["nombre"],
                             "lat": data._to_number(traps["lat_epsg4258"]), "lon": data._to_number(traps["long_epsg4258"])})
    stations = stations[stations["station_code"].isin(readings["station_code"].unique())]
    # the same weather sharing between nearby stations as in training (F.WEATHER_FROM)
    coordinates = pd.DataFrame({"station_code": traps["codigo"], "lat": data._to_number(traps["lat_epsg4258"]),
                                "lon": data._to_number(traps["long_epsg4258"])}).set_index("station_code")
    # every station's weather source (nearby stations share one), all downloaded in one batch
    sources = list(dict.fromkeys(F.WEATHER_FROM.get(code, code) for code in stations["station_code"]))
    daily_tables = _fetch_stations_weather([(coordinates.loc[c, "lat"], coordinates.loc[c, "lon"]) for c in sources])
    downloaded = {source: F.weather_features(daily.rename(columns={"timestamp": "date"})).set_index("date")
                  for source, daily in zip(sources, daily_tables)}
    weather_by_station = {code: downloaded[F.WEATHER_FROM.get(code, code)] for code in stations["station_code"]}
    today = data._madrid_now().normalize()
    table = _predict(readings, weather_by_station, stations, today)
    table["nowcast"] = False
    # NOWCAST: the days between each station's last reading and today have no reading yet (publication
    # delay), so each of them gets the model's same-day estimate as of that day (lead 0, the tested setting).
    # Each day is only computed for the stations that need it (a recent last reading before that day).
    last_reading = readings.groupby("station_code")["timestamp"].max()
    recent = last_reading[last_reading >= today - pd.Timedelta(days=config.POLLEN_MAX_AGE_DAYS)]
    estimates = []
    if not recent.empty:
        for day in pd.date_range(recent.min() + pd.Timedelta(days=1), today - pd.Timedelta(days=1), freq="D"):
            needing = set(recent[recent < day].index)
            if needing:
                estimates.append(_predict(readings, weather_by_station, stations, day, leads=[0], only=needing))
    estimates = [e for e in estimates if not e.empty]
    if estimates:
        past = pd.concat(estimates, ignore_index=True)
        past["nowcast"] = True
        table = pd.concat([table, past], ignore_index=True)
    table["last_reading"] = table["station_code"].map(last_reading)     # the station's real latest reading
    table["timestamp"] = table["issue_date"]         # the backup helper expects this column
    return table


@st.cache_data(ttl=SIX_HOURS, show_spinner=False)
def _live_forecast(day_text):
    """Cached for 6 hours (and per day), with a backup copy like every other source."""
    return data._with_backup("pollen_model_forecast", _fetch_live_forecast,
                             date_columns=("timestamp", "issue_date", "target_date", "last_reading"))


@st.cache_data(show_spinner=False)
def _demo_forecast():
    """The forecast as of 24 May 2025, made by compare_models.py with only what was known that day."""
    table = pd.read_csv(data.BACKUP_DIR / "replay" / f"pollen_forecast_{config.PLANNER_DEMO_DATE}.csv",
                        parse_dates=["issue_date", "target_date", "last_reading"])
    table.attrs["live"] = False
    table.attrs["fetched_at"] = pd.Timestamp(config.PLANNER_DEMO_DATE)
    return table


def _fetch_recent_readings():
    """The readings of the last RECENT_DAYS days at every station, with the station names."""
    readings = _last_readings if _last_readings is not None else _fetch_recent_pollen()
    readings = readings[readings["timestamp"] >= data._madrid_now().normalize() - pd.Timedelta(days=RECENT_DAYS)]
    traps = data._read_csv(data._ckan_csv_url("captadores_polen"), encoding="latin-1")
    names = pd.DataFrame({"station_code": traps["codigo"], "station": traps["nombre"]})
    return readings.merge(names, on="station_code", how="left").reset_index(drop=True)


@st.cache_data(ttl=SIX_HOURS, show_spinner=False)
def _live_recent_readings(day_text):
    return data._with_backup("pollen_recent", _fetch_recent_readings)


def recent_readings(demo=False):
    """
    Official pollen readings of the last two weeks: station, station_code,
    pollen_type, value (grains/m3), timestamp (the reading day). In demo mode:
    the readings that had been published by 24 May 2025.
    """
    if demo:
        table = pd.read_csv(data.BACKUP_DIR / "replay" / f"pollen_recent_{config.PLANNER_DEMO_DATE}.csv", parse_dates=["timestamp"])
        table.attrs["live"] = False
        return table
    return _live_recent_readings(data._madrid_now().strftime("%Y-%m-%d"))


def forecast(demo=False):
    """Our model's forecast for every station (see the top of this file)."""
    if demo:
        return _demo_forecast()
    return _live_forecast(data._madrid_now().strftime("%Y-%m-%d"))


def best_source(pollen_type, model_row):
    """
    The forecast to show for one pollen type and day: our model, if that type
    and lead day was RELIABLE in the test; otherwise nothing ("Sin previsión
    fiable"). No other forecast is used: the data rule only allows forecasts
    made from the official measurements.
    Returns {"level": 0-3 or None, "source": text, "kind": "model" / "none", "accuracy": share or None}.
    """
    if model_row is not None and model_row["reliable"]:
        return {"level": int(model_row["predicted_level"]), "source": OUR_MODEL, "kind": "model",
                "accuracy": model_row["accuracy"]}
    return {"level": None, "source": "Sin previsión fiable", "kind": "none", "accuracy": None}
