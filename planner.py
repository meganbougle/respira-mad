"""
planner.py - the 5-day planner: today + 4 days, each split into three parts
of the day (mañana, tarde, noche).

    plan(lat, lon, profile, allergy_types, now, demo)  ->  a dictionary with
        days: for each day its date, the pollen forecast for the selected
              types, a confidence label, and three blocks; each block has a
              level (same three levels as the main card), the air forecast
              and the weather.

Level of a block = the worse of
    - the pollen level of that DAY (only the pollen types the person selected;
      from our own model where it proved reliable, else the European forecast
      for grass and olive, else no pollen level), and
    - the air quality forecast for that BLOCK, with the same official ICA
      bands and the same profile rules as the main card.

Everything here is a FORECAST. All thresholds come from config.py.
"""

import pandas as pd

import config
import data
import risk
from pollen_model import predict

WEEKDAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _confidence(lead, pollen_entries):
    """The confidence label of one day (rule explained in config.py). Returns (label, reason)."""
    used = [p for p in pollen_entries if p["kind"] != "none"]
    if used:
        if any(p["kind"] == "european" for p in used):
            return "baja", "El polen de este día viene de la previsión europea, que no hemos comprobado con mediciones."
        if any(p["kind"] == "measurement" for p in used) and all(p["kind"] in ("measurement", "model") for p in used):
            accuracies = [p["accuracy"] for p in used if p["accuracy"] is not None]
            if not accuracies:
                return "alta", "El polen de hoy se basa en la última medición oficial."
        accuracies = [p["accuracy"] for p in used if p["accuracy"] is not None]
        if accuracies:
            worst = min(accuracies)
            label = ("alta" if worst >= config.CONFIDENCE_ALTA_FROM
                     else "media" if worst >= config.CONFIDENCE_MEDIA_FROM else "baja")
            return label, (f"En la prueba con 2025, nuestro modelo acertó el nivel de polen el {worst:.0%} de los días "
                           f"de temporada con {lead} día{'s' if lead != 1 else ''} de antelación.")
    label = config.CONFIDENCE_BY_DAYS_AHEAD[min(lead, len(config.CONFIDENCE_BY_DAYS_AHEAD) - 1)]
    return label, ("Solo depende de la previsión de calidad del aire, que no hemos comprobado con mediciones: "
                   "la etiqueta refleja únicamente los días de antelación.")


def plan(lat, lon, profile="general", allergy_types=(), now=None, demo=False):
    settings = config.PROFILES[profile]
    audience = settings["air"]
    if demo:
        today = pd.Timestamp(config.PLANNER_DEMO_DATE)
        now = today + pd.Timedelta(hours=7)                 # the demo planner is "the morning of 24 May 2025"
    else:
        now = now or risk.madrid_now()
        today = now.normalize()
    day_text = config.PLANNER_DEMO_DATE if demo else None
    notes = []

    # ---- forecasts for this exact point (Open-Meteo): air + pollen, and weather
    air, weather = None, None
    try:
        air = data.load_forecast_air(round(lat, 2), round(lon, 2), day_text, config.PLANNER_DAYS)
        weather = data.load_forecast_weather(round(lat, 2), round(lon, 2), day_text, config.PLANNER_DAYS)
    except Exception as error:
        notes.append(f"Sin previsión de Open-Meteo para este punto ({error}).")

    # ---- our pollen model: the nearest pollen station that has a forecast
    model_rows, model_station = None, None
    if allergy_types:
        try:
            table = predict.forecast(demo)
            if not table.empty:
                table = risk._add_distance(table, lat, lon)
                model_station = {"station": table.iloc[0]["station"], "distance_km": round(table.iloc[0]["distance_km"], 1)}
                model_rows = table[table["station"] == model_station["station"]]
        except Exception as error:
            notes.append(f"Nuestro modelo de polen no está disponible ahora ({error}).")

    # ---- today's latest official reading (live mode only): today can never look better than what was measured
    measured = None
    if allergy_types and not demo:
        try:
            measured = risk.pollen_near(lat, lon, data.load_pollen(today=today))
        except Exception:
            pass

    days = []
    for lead in range(config.PLANNER_DAYS):
        date = today + pd.Timedelta(days=lead)

        # ---------------- pollen for this day, one entry per selected type
        pollen_entries, pollen_app_level = [], None
        for pollen_type in allergy_types:
            row = None
            if model_rows is not None:
                match = model_rows[(model_rows["pollen_type"] == pollen_type) & (model_rows["lead"] == lead)]
                row = match.iloc[0] if len(match) else None
            european = None
            column = {v: k for k, v in config.FORECAST_POLLEN_TO_OFFICIAL.items()}.get(pollen_type)
            if air is not None and column:
                values = air[air["timestamp"].dt.normalize() == date][column]
                if values.notna().sum() >= 12:              # at least half the day forecast
                    european = risk.pollen_level(pollen_type, values.mean())
            chosen = predict.best_source(pollen_type, lead, row, european)
            entry = {"pollen_type": pollen_type, "name": config.POLLEN_NAMES[pollen_type], **chosen,
                     "level_name": config.POLLEN_LEVELS[chosen["level"]] if chosen["level"] is not None else None}
            if row is not None:
                entry["model_level_name"] = config.POLLEN_LEVELS[int(row["predicted_level"])]
                entry["model_reliable"] = bool(row["reliable"])
            # today: the official reading is a floor (same rule as the main card)
            if lead == 0 and measured:
                reading = next((t for t in measured["types"] if t["pollen_type"] == pollen_type), None)
                if reading and (entry["level"] is None or reading["level"] > entry["level"]):
                    entry.update(level=reading["level"], level_name=reading["level_name"], kind="measurement", accuracy=None,
                                 source=f"Medición de {measured['station']} del {measured['reading_date']:%d/%m} "
                                        f"(hace {measured['age_days']} días)")
            if entry["level"] is not None:
                app_level = config.POLLEN_TO_LEVEL[entry["level"]]
                pollen_app_level = app_level if pollen_app_level is None else max(pollen_app_level, app_level)
            pollen_entries.append(entry)
        worst_pollen = max((p for p in pollen_entries if p["level"] is not None), key=lambda p: p["level"], default=None)

        # ---------------- the three parts of the day
        blocks = []
        for name, first_hour, last_hour in config.PLANNER_BLOCKS:
            block = {"name": name, "hours": f"{first_hour}–{last_hour} h", "air": None, "weather": None}
            air_level = None
            if air is not None:
                hours = air[(air["timestamp"].dt.normalize() == date)
                            & air["timestamp"].dt.hour.between(first_hour, last_hour - 1)]
                # One value per pollutant for the block (see config.py, PLANNER_BLOCK_VALUE):
                # the highest hour for NO2, the average of the block for O3 and particles.
                values = {}
                for pollutant in config.ICA_BANDS:
                    series = hours[pollutant].dropna()
                    if not series.empty:
                        values[pollutant] = float(series.max() if config.PLANNER_BLOCK_VALUE[pollutant] == "max" else series.mean())
                worst = None                                # (category, how far into the band, pollutant)
                for pollutant, value in values.items():
                    category = risk.ica_category(pollutant, value)
                    if category is not None:
                        candidate = (category, value / config.ICA_BANDS[pollutant][category], pollutant)
                        worst = candidate if worst is None or candidate > worst else worst
                if worst is not None:
                    air_level = config.ICA_TO_LEVEL[audience][worst[0]]
                    block["air"] = {"category": worst[0], "category_name": config.ICA_CATEGORIES[worst[0]],
                                    "pollutant": worst[2], "value": round(values[worst[2]]),
                                    "values": {p: round(v) for p, v in values.items()}}
            if weather is not None:
                hours = weather[(weather["timestamp"].dt.normalize() == date)
                                & weather["timestamp"].dt.hour.between(first_hour, last_hour - 1)]
                if hours["temperature_c"].notna().any():
                    block["weather"] = {"temperature_c": round(float(hours["temperature_c"].mean())),
                                        "rain_mm": round(float(hours["rain_mm"].fillna(0).sum()), 1)}

            known = [lv for lv in (air_level, pollen_app_level) if lv is not None]
            level = max(known) if known else None
            block["level_index"] = level
            block["level_label"] = config.LEVEL_LABELS[level] if level is not None else "Sin previsión"
            # what the level is based on, in words
            if level is None:
                block["basis"] = "Sin previsión de aire ni de polen para este tramo."
            elif air_level is None:
                block["basis"] = "Solo polen: la previsión de calidad del aire no llega hasta este tramo."
            elif pollen_app_level is None:
                block["basis"] = "Solo calidad del aire" + (": no hay previsión fiable de polen para tus tipos." if allergy_types else ".")
            else:
                block["basis"] = "Lo peor entre el polen del día y la calidad del aire del tramo."
            block["driver"] = ("polen" if pollen_app_level is not None and pollen_app_level >= (air_level or 0) and pollen_app_level > 0
                               else "aire" if air_level else None)
            blocks.append(block)

        confidence, confidence_reason = _confidence(lead, pollen_entries)
        days.append({
            "date": date, "lead": lead,
            "label": "Hoy" if lead == 0 and not demo else f"{WEEKDAYS[date.dayofweek]} {date.day}",
            "pollen": pollen_entries,
            "worst_pollen": worst_pollen["name"] if worst_pollen else None,
            "confidence": confidence, "confidence_reason": confidence_reason,
            "blocks": blocks,
        })

    # ---- honest notes about what is missing
    missing_air = [f"{d['label']} {b['name'].lower()}" for d in days for b in d["blocks"] if b["air"] is None]
    if missing_air and air is not None:
        notes.append("La previsión de calidad del aire de Open-Meteo no llega a: " + ", ".join(missing_air)
                     + ". En esos tramos solo se usan el polen y el tiempo.")
    unreliable = sorted({p["name"] for d in days for p in d["pollen"] if p["kind"] == "none"})
    if unreliable:
        notes.append("Sin previsión fiable de polen para: " + ", ".join(unreliable) + " (en alguno de los días).")
    if demo:
        notes.append("Modo demostración: vista del 24 de mayo de 2025. El polen es la previsión de nuestro modelo con lo "
                     "que se sabía ese día; el aire y el tiempo son datos del modelo europeo para esas fechas, no la "
                     "previsión que se emitió entonces.")

    return {
        "today": today, "demo": demo, "profile": profile,
        "kind": "PREVISIÓN: nada de esto son mediciones",
        "pollen_station": model_station,
        "days": days, "notes": notes,
    }
