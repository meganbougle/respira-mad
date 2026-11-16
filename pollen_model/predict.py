"""
pollen_model/predict.py - makes pollen forecasts in the app with OUR model.

    forecast(now, demo)  ->  table with one row per station, pollen type and day:
        station, station_code, lat, lon, pollen_type, issue_date, target_date,
        lead (0 = today ... 5), predicted_level (0-3), prob_alto_or_more,
        reliable (did this type + lead day beat the baseline in the 2025 test?),
        accuracy (share of in-season days it got right in that test),
        q10, q50, q90 (grains/m3: the median forecast and an 80% prediction
        interval, from quantile regression; see compare_models.py)

    recent_readings(demo)  ->  the official readings of the last two weeks at
        every station (for the forecast chart).

    Each pollen type uses the model that won the 2025 comparison (see
    MODEL_REPORT.md and model_choice.json): gradient boosting for most, ARIMAX
    for a few. Column `model` says which ("gb" or "arimax").

    best_source(...) picks, for one pollen type and day, our model if it is
    RELIABLE, otherwise the European forecast (grass and olive only),
    otherwise nothing ("Sin previsión fiable").

The model itself is trained by train_pollen_model.py and stored in
pollen_model/models.joblib. This file only uses it.
"""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
import streamlit as st

import config
import data
from pollen_model import arimax as A
from pollen_model import features as F

FOLDER = Path(__file__).parent
SIX_HOURS = 6 * 60 * 60
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY = {"temperature_2m_mean": "temp_mean", "temperature_2m_max": "temp_max", "precipitation_sum": "rain",
         "wind_speed_10m_max": "wind", "relative_humidity_2m_mean": "humidity"}

OUR_MODEL = "Modelo propio de Respira MAD"
EUROPEAN = "Previsión europea CAMS (Open-Meteo)"

RECENT_DAYS = 14                    # how many days of readings recent_readings() keeps

_models = None
_quantile_models = None
_last_readings = None               # the readings downloaded for the latest forecast (reused by recent_readings)


def models():
    """The trained models, loaded once: {(pollen type, lead): model}."""
    global _models
    if _models is None:
        _models = joblib.load(FOLDER / "models.joblib")
    return _models


def quantile_models():
    """
    The quantile models made by compare_models.py, loaded once:
    {(pollen type, lead): {"q10": model, "q50": model, "q90": model}} (see
    features.predict_quantiles). Returns {} if the file is missing (no intervals then).
    """
    global _quantile_models
    if _quantile_models is None:
        file = FOLDER / "quantile_models.joblib"
        _quantile_models = joblib.load(file) if file.exists() else {}
    return _quantile_models


@st.cache_data(show_spinner=False)
def model_choice():
    """Which model won the 2025 comparison for each pollen type (see MODEL_REPORT.md)."""
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
    """{(pollen type, lead): {"reliable": True/False, "accuracy": share of in-season days right}}"""
    results = {(r["pollen_type"], r["lead"]): {"reliable": bool(r["reliable"]), "accuracy": r["accuracy_season"],
                                               "baseline": r["baseline_season"]}
               for r in meta()["results"]}
    # the pollen types that use ARIMAX have their own test results
    for r in arimax_models()["results"]:
        results[(r["pollen_type"], r["lead"])] = {"reliable": bool(r["reliable"]), "accuracy": r["accuracy_season"],
                                                  "baseline": r["baseline_season"]}
    return results


def _arimax_rows(pollen_type, code, readings, weather, issue_day):
    """
    ARIMAX forecast for one station and pollen type, as of `issue_day`:
    {lead: {"predicted_level", "prob_alto_or_more", "q10", "q50", "q90"}} or {} if this station has no fitted model.
    It only sees readings up to issue_day - 3 days; the weather of the coming days is the weather forecast.
    """
    from scipy.stats import norm

    fitted = arimax_models()["models"].get(pollen_type, {}).get(code)
    if fitted is None:
        return {}
    last = issue_day - pd.Timedelta(days=F.READING_DELAY)
    window_days = pd.date_range(last - pd.Timedelta(days=A.WINDOW_DAYS - 1), last, freq="D")
    future_days = pd.date_range(last + pd.Timedelta(days=1), issue_day + pd.Timedelta(days=max(F.LEADS)), freq="D")
    window = np.log1p(readings.reindex(window_days))
    mean, error = A.forecast(window, weather[A.WEATHER].reindex(window_days), weather[A.WEATHER].reindex(future_days),
                             fitted["order"], fitted["fourier_pairs"], fitted["params"])
    alto = np.log1p(config.POLLEN_THRESHOLDS[pollen_type][1])
    rows = {}
    for lead in F.LEADS:
        i = F.READING_DELAY - 1 + lead                     # position of day D+lead among the forecast days
        low, middle, high = np.expm1(A.quantiles(mean[i], error[i]))
        rows[lead] = {"predicted_level": int(F.level_of(pollen_type, [middle])[0]),
                      "prob_alto_or_more": round(float(1 - norm.cdf((alto - mean[i]) / error[i])), 3),
                      "q10": round(float(low), 1), "q50": round(float(middle), 1), "q90": round(float(high), 1)}
    return rows


# ----------------------------------------------------------------------------
# What the model needs every day: recent pollen readings and each station's weather
# ----------------------------------------------------------------------------

def _fetch_recent_pollen():
    """Readings of this year and last year for the 10 pollen types with thresholds."""
    tables = [data._read_csv(data._ckan_csv_url("mediciones_polen"))]
    try:                                            # last year, for the "same day last year" input
        listing = requests.get(data.CKAN_API + "package_show", params={"id": "mediciones_polen_historico"},
                               timeout=data.TIMEOUT).json()["result"]["resources"]
        last_year = str(data._madrid_now().year - 1)
        url = next(r["url"] for r in listing if r["name"] == last_year and r["format"].upper() == "CSV")
        tables.append(data._read_csv(url))
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


def _fetch_station_weather(lat, lon):
    """
    Daily weather at one station up to 6 days ahead (observed, then forecast).
    It starts on 1 January (the "warmth since 1 January" input needs it) or
    about 4 months ago if that is earlier (the ARIMAX model looks back 120 days).
    """
    today = data._madrid_now().normalize()
    recent = requests.get(FORECAST_URL, timeout=data.TIMEOUT, params={
        "latitude": lat, "longitude": lon, "daily": ",".join(DAILY), "timezone": "Europe/Madrid",
        "past_days": 14, "forecast_days": 7})
    recent.raise_for_status()
    table = pd.DataFrame(recent.json()["daily"])
    start_of_year = min(pd.Timestamp(year=today.year, month=1, day=1), today - pd.Timedelta(days=A.WINDOW_DAYS + 10))
    older_end = today - pd.Timedelta(days=15)
    if older_end > start_of_year:                   # the months before: historical weather
        older = requests.get(ARCHIVE_URL, timeout=data.TIMEOUT, params={
            "latitude": lat, "longitude": lon, "daily": ",".join(DAILY), "timezone": "Europe/Madrid",
            "start_date": start_of_year.strftime("%Y-%m-%d"), "end_date": older_end.strftime("%Y-%m-%d")})
        older.raise_for_status()
        table = pd.concat([pd.DataFrame(older.json()["daily"]), table], ignore_index=True)
    table = table.rename(columns={"time": "timestamp", **DAILY})
    table["timestamp"] = pd.to_datetime(table["timestamp"])
    return table.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _predict(readings, weather_by_station, stations, issue_day):
    """Run the models for every station, type and lead, as of `issue_day`."""
    station_number = meta()["station_number"]
    all_days = pd.date_range(issue_day - pd.Timedelta(days=420), issue_day + pd.Timedelta(days=max(F.LEADS)), freq="D")
    results = test_results()
    gb_results = {(r["pollen_type"], r["lead"]): {"reliable": bool(r["reliable"]), "accuracy": r["accuracy_season"]}
                  for r in meta()["results"]}
    rows = []
    for pollen_type in F.POLLEN_TYPES:
        one = readings[readings["pollen_type"] == pollen_type]
        wide = one.pivot(index="timestamp", columns="station_code", values="value").reindex(all_days)
        network = wide.mean(axis=1)
        use_arimax = model_choice()["choice"].get(pollen_type) == "arimax"
        for code in wide.columns:
            if code not in weather_by_station or code not in station_number:
                continue
            arimax = {}
            if use_arimax:
                try:
                    arimax = _arimax_rows(pollen_type, code, wide[code], weather_by_station[code], issue_day)
                except Exception:
                    arimax = {}                     # if ARIMAX fails for this station, gradient boosting still answers
            for lead in F.LEADS:
                table = F.station_rows(station_number[code], wide[code], network, weather_by_station[code],
                                       pollen_type, lead, F.READING_DELAY)
                row = table.loc[[issue_day]]
                if pd.isna(row["last_value"].iloc[0]):
                    continue                        # no reading in the last ~2 weeks: no forecast for this station
                if lead in arimax:
                    prediction = {**arimax[lead], "model": "arimax"}
                    result = results.get((pollen_type, lead), {"reliable": False, "accuracy": None})
                else:
                    model = models()[(pollen_type, lead)]
                    probabilities = model.predict_proba(row[F.FEATURES])[0]
                    prediction = {"predicted_level": int(model.predict(row[F.FEATURES])[0]), "model": "gb",
                                  "prob_alto_or_more": round(float(sum(p for p, c in zip(probabilities, model.classes_) if c >= 2)), 3)}
                    # the median and the 80% interval, in grains/m3
                    if (pollen_type, lead) in quantile_models():
                        prediction.update(F.predict_quantiles(quantile_models()[(pollen_type, lead)], row[F.FEATURES]).iloc[0].to_dict())
                    # the test results of gradient boosting (not ARIMAX's, even for a type that normally uses ARIMAX)
                    result = gb_results.get((pollen_type, lead), {"reliable": False, "accuracy": None})
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
    weather, downloaded = {}, {}
    for code in stations["station_code"]:
        source = F.WEATHER_FROM.get(code, code)
        if source not in downloaded:
            daily = _fetch_station_weather(coordinates.loc[source, "lat"], coordinates.loc[source, "lon"])
            downloaded[source] = F.weather_features(daily.rename(columns={"timestamp": "date"})).set_index("date")
        weather[code] = downloaded[source]
    table = _predict(readings, weather, stations, data._madrid_now().normalize())
    table["timestamp"] = table["issue_date"]         # the backup helper expects this column
    return table


@st.cache_data(ttl=SIX_HOURS, show_spinner=False)
def _live_forecast(day_text):
    """Cached for 6 hours (and per day), with a backup copy like every other source."""
    return data._with_backup("pollen_model_forecast", _fetch_live_forecast,
                             date_columns=("timestamp", "issue_date", "target_date"))


@st.cache_data(show_spinner=False)
def _demo_forecast():
    """The forecast as of 24 May 2025, made by train_pollen_model.py with only what was known that day."""
    table = pd.read_csv(data.BACKUP_DIR / "replay" / f"pollen_forecast_{config.PLANNER_DEMO_DATE}.csv",
                        parse_dates=["issue_date", "target_date"])
    results = test_results()
    table["accuracy"] = [results.get((t, l), {}).get("accuracy") for t, l in zip(table["pollen_type"], table["lead"])]
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


def best_source(pollen_type, lead, model_row, european_level):
    """
    Which forecast to trust for one pollen type and day:
      1. our model, if that type + lead day was RELIABLE in the 2025 test;
      2. otherwise the European forecast, if it has that type (grass, olive) and that day;
      3. otherwise nothing.
    Returns {"level": 0-3 or None, "source": text, "kind": "model" / "european" / "none", "accuracy": share or None}.
    """
    if model_row is not None and model_row["reliable"]:
        return {"level": int(model_row["predicted_level"]), "source": OUR_MODEL, "kind": "model",
                "accuracy": model_row["accuracy"]}
    if european_level is not None:
        return {"level": int(european_level), "source": EUROPEAN, "kind": "european", "accuracy": None}
    return {"level": None, "source": "Sin previsión fiable", "kind": "none", "accuracy": None}
