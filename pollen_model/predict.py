"""
pollen_model/predict.py - makes pollen forecasts in the app with OUR model.

    forecast(now, demo)  ->  table with one row per station, pollen type and day:
        station, station_code, lat, lon, pollen_type, issue_date, target_date,
        lead (0 = today ... 5), predicted_level (0-3), prob_alto_or_more,
        reliable (did this type + lead day beat the baseline in the 2025 test?),
        accuracy (share of in-season days it got right in that test)

    best_source(...) picks, for one pollen type and day, our model if it is
    RELIABLE, otherwise the European forecast (grass and olive only),
    otherwise nothing ("Sin previsión fiable").

The model itself is trained by train_pollen_model.py and stored in
pollen_model/models.joblib. This file only uses it.
"""

import json
from pathlib import Path

import joblib
import pandas as pd
import requests
import streamlit as st

import config
import data
from pollen_model import features as F

FOLDER = Path(__file__).parent
SIX_HOURS = 6 * 60 * 60
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY = {"temperature_2m_mean": "temp_mean", "temperature_2m_max": "temp_max", "precipitation_sum": "rain",
         "wind_speed_10m_max": "wind", "relative_humidity_2m_mean": "humidity"}

OUR_MODEL = "Modelo propio de Respira MAD"
EUROPEAN = "Previsión europea CAMS (Open-Meteo)"

_models = None


def models():
    """The trained models, loaded once: {(pollen type, lead): model}."""
    global _models
    if _models is None:
        _models = joblib.load(FOLDER / "models.joblib")
    return _models


@st.cache_data(show_spinner=False)
def meta():
    """Facts about the saved model, including the 2025 test results."""
    return json.loads((FOLDER / "meta.json").read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def test_results():
    """{(pollen type, lead): {"reliable": True/False, "accuracy": share of in-season days right}}"""
    return {(r["pollen_type"], r["lead"]): {"reliable": bool(r["reliable"]), "accuracy": r["accuracy_season"],
                                            "baseline": r["baseline_season"]}
            for r in meta()["results"]}


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
    return readings[["station_code", "pollen_type", "value", "timestamp"]].reset_index(drop=True)


def _fetch_station_weather(lat, lon):
    """Daily weather at one station from 1 January to 6 days ahead (observed, then forecast)."""
    today = data._madrid_now().normalize()
    recent = requests.get(FORECAST_URL, timeout=data.TIMEOUT, params={
        "latitude": lat, "longitude": lon, "daily": ",".join(DAILY), "timezone": "Europe/Madrid",
        "past_days": 14, "forecast_days": 7})
    recent.raise_for_status()
    table = pd.DataFrame(recent.json()["daily"])
    start_of_year = pd.Timestamp(year=today.year, month=1, day=1)
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
    rows = []
    for pollen_type in F.POLLEN_TYPES:
        one = readings[readings["pollen_type"] == pollen_type]
        wide = one.pivot(index="timestamp", columns="station_code", values="value").reindex(all_days)
        network = wide.mean(axis=1)
        for code in wide.columns:
            if code not in weather_by_station or code not in station_number:
                continue
            for lead in F.LEADS:
                table = F.station_rows(station_number[code], wide[code], network, weather_by_station[code],
                                       pollen_type, lead, F.READING_DELAY)
                row = table.loc[[issue_day]]
                if pd.isna(row["last_value"].iloc[0]):
                    continue                        # no reading in the last ~2 weeks: no forecast for this station
                model = models()[(pollen_type, lead)]
                probabilities = model.predict_proba(row[F.FEATURES])[0]
                result = results.get((pollen_type, lead), {"reliable": False, "accuracy": None})
                rows.append({
                    "station_code": code, "pollen_type": pollen_type, "issue_date": issue_day,
                    "target_date": issue_day + pd.Timedelta(days=lead), "lead": lead,
                    "predicted_level": int(model.predict(row[F.FEATURES])[0]),
                    "prob_alto_or_more": round(float(sum(p for p, c in zip(probabilities, model.classes_) if c >= 2)), 3),
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
