"""
chart.py - the numbers behind the forecast line chart (web app, under the planner).

    pollen_chart(lat, lon, allergy_types, demo)
        For the nearest pollen station and each selected pollen type:
        the official readings of the last 7 days (MEASURED) and the forecast
        for today + 5 days (FORECAST: the median and an 80% prediction
        interval, in grains/m3), plus the official level thresholds.

    air_chart(lat, lon, demo)
        The hourly air-quality forecast of OUR model (made from the official
        measurements of the nearest station) for the pollutant that reaches
        the worst ICA category, with the official ICA bands of that pollutant.

No logic of its own: readings and forecasts come from pollen_model/predict.py
and data.py, thresholds from config.py.
"""

import pandas as pd

import config
import data
import risk
from air_model import predict as air_predict
from pollen_model import predict

OBSERVED_DAYS = 7                    # how many past days of readings the chart shows
MODEL_LABELS = {"gb": "Gradient boosting", "arimax": "ARIMAX", "ets": "Suavizado exponencial",
                "naive": "Estacional ingenuo"}


def model_label(pollen_type):
    """The name of the forecast model used for one pollen type."""
    model = predict.model_choice()["choice"].get(pollen_type, "gb")
    return MODEL_LABELS.get(model, model)


def _today(demo):
    return pd.Timestamp(config.PLANNER_DEMO_DATE) if demo else risk.madrid_now().normalize()


def pollen_chart(lat, lon, allergy_types=(), demo=False):
    today = _today(demo)
    result = {"today": today, "demo": demo, "station": None, "series": [], "notes": [],
              "levels": config.POLLEN_LEVELS, "interval": predict.model_choice().get("interval", 0.8),
              "first_day": today - pd.Timedelta(days=OBSERVED_DAYS), "last_day": today + pd.Timedelta(days=5)}
    if not allergy_types:
        return result

    # ---- the forecast of our model at the nearest pollen station (the same station the planner uses)
    try:
        forecast = predict.forecast(demo)
    except Exception as error:
        result["notes"].append(data.problem("La previsión de polen no está disponible ahora.", error))
        return result
    if forecast.empty:
        result["notes"].append("No hay ninguna estación de polen con lecturas recientes para hacer la previsión.")
        return result
    forecast = risk._add_distance(forecast, lat, lon)
    nearest = forecast.iloc[0]
    forecast = forecast[forecast["station_code"] == nearest["station_code"]]
    result["station"] = {"station": nearest["station"], "distance_km": round(float(nearest["distance_km"]), 1)}

    # ---- the official readings of the last days at that station
    try:
        readings = predict.recent_readings(demo)
        readings = readings[(readings["station_code"] == nearest["station_code"])
                            & (readings["timestamp"] >= result["first_day"]) & (readings["timestamp"] <= today)]
    except Exception as error:
        readings = None
        result["notes"].append(data.problem("No se pudieron cargar las lecturas recientes.", error))

    has_interval = "q50" in forecast.columns
    for pollen_type in allergy_types:
        thresholds = list(config.POLLEN_THRESHOLDS[pollen_type])

        def level_name(value):
            return config.POLLEN_LEVELS[risk.pollen_level(pollen_type, value)]

        observed = []
        if readings is not None:
            for _, row in readings[readings["pollen_type"] == pollen_type].sort_values("timestamp").iterrows():
                observed.append({"date": row["timestamp"], "value": float(row["value"]), "level_name": level_name(row["value"])})

        predicted = []
        rows = forecast[forecast["pollen_type"] == pollen_type].sort_values("lead")
        for _, row in rows.iterrows():
            if not has_interval or pd.isna(row["q50"]):
                continue
            point = {"date": row["target_date"], "lead": int(row["lead"]),
                     "value": float(row["q50"]), "low": float(row["q10"]), "high": float(row["q90"]),
                     "level_name": level_name(row["q50"]),
                     # did this type + lead day pass our 2025 test? (the planner only trusts the ones that did)
                     "reliable": bool(row["reliable"])}
            # demo mode only: what was really measured on that day, published afterwards
            if demo and "target_value" in row and not pd.isna(row["target_value"]):
                point["actual"] = float(row["target_value"])
            predicted.append(point)

        result["series"].append({"key": pollen_type, "name": config.POLLEN_NAMES[pollen_type], "thresholds": thresholds,
                                 "observed": observed, "forecast": predicted,
                                 "model": model_label(pollen_type)})
    if any(not s["forecast"] for s in result["series"]):
        result["notes"].append("Algún tipo de polen no tiene previsión: no hay lecturas recientes en esta estación.")
    return result


def air_chart(lat, lon, demo=False):
    """Hourly forecast of the pollutant with the worst ICA category, from OUR air model, from the current hour on."""
    now = pd.Timestamp(config.PLANNER_DEMO_DATE) + pd.Timedelta(hours=7) if demo else risk.madrid_now()
    result = {"demo": demo, "pollutant": None, "hours": [], "notes": [], "station": None,
              "horizon_hours": air_predict.horizon_hours(),
              "source": "Previsión de nuestro modelo, hecha con las mediciones oficiales de la estación"}
    try:
        forecast = air_predict.at_point(lat, lon, air_predict.forecast(demo, now))
        if forecast is None:
            raise ValueError("ninguna estación cercana tiene lecturas recientes")
    except Exception as error:
        result["notes"].append(data.problem("Sin previsión de calidad del aire para este punto.", error))
        return result
    used = forecast.attrs.get("stations", {})
    forecast = forecast[forecast["timestamp"] >= now.floor("h")]

    # the main pollutant: the one that reaches the worst category (ties: the one deepest into its band)
    worst = None
    for pollutant, bands in config.ICA_BANDS.items():
        values = forecast[pollutant].dropna()
        if values.empty:
            continue
        category = risk.ica_category(pollutant, values.max())
        if category is None:
            continue
        candidate = (category, values.max() / bands[category], pollutant)
        worst = candidate if worst is None or candidate > worst else worst
    if worst is None:
        result["notes"].append("La previsión de calidad del aire no tiene datos para este punto.")
        return result

    pollutant = worst[2]
    result["pollutant"] = pollutant
    if pollutant in used:                                         # the station this forecast is made from
        result["station"] = {"station": used[pollutant]["station"], "source": used[pollutant]["source"],
                             "distance_km": used[pollutant]["distance_km"]}
    result["bands"] = config.ICA_BANDS[pollutant]                 # upper limit of each official category, µg/m3
    result["categories"] = [{"name": name, "color": color} for name, color in zip(config.ICA_CATEGORIES, config.ICA_COLORS)]
    for _, row in forecast.dropna(subset=[pollutant]).iterrows():
        category = risk.ica_category(pollutant, row[pollutant])
        result["hours"].append({"time": row["timestamp"], "value": round(float(row[pollutant]), 1),
                                "category_name": config.ICA_CATEGORIES[category] if category is not None else None})
    return result
