"""
planner.py - the 5-day planner: today + 4 days, each split into four parts:
mañana (07-13), tarde (13-20), noche (20-24) and madrugada (00-07). The
madrugada belongs to the night that starts the evening before, so on each row
it shows the early hours of the NEXT calendar day.

    plan(lat, lon, profile, allergy_types, now, demo)  ->  a dictionary with
        days: for each day its date, the pollen forecast for the selected
              types, a confidence label, and four blocks; each block has a
              level (same three levels as the main card), the air forecast
              and the weather.

Level of a block = the worse of
    - the pollen level of that DAY (only the pollen types the person selected;
      from our own model where it proved reliable, else no pollen level), and
    - the air quality forecast for that BLOCK (our own model, made from the
      official station measurements; only as far ahead as it proved better
      than the baseline), with the same official ICA bands and the same
      profile rules as the main card.

DATA RULE: pollen and air come only from the Comunidad de Madrid and the
Ayuntamiento de Madrid (measurements, and our forecasts made from them).
Only the weather comes from Open-Meteo.

Everything here is a FORECAST. All thresholds come from config.py.
"""

import pandas as pd

import config
import data
import risk
from air_model import predict as air_predict

WEEKDAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _confidence(lead, pollen_entries, days_since_reading=None):
    """The confidence label of one day (rules explained in config.py). Returns (label, reason)."""
    label, reason = _confidence_from_test(lead, pollen_entries)
    used = [p for p in pollen_entries if p["kind"] == "model"]
    if used and days_since_reading is not None and days_since_reading > config.CONFIDENCE_GAP_DAYS:
        lower = config.CONFIDENCE_LABELS[max(config.CONFIDENCE_LABELS.index(label) - 1, 0)]
        if lower != label:
            reason += (f" Baja un nivel porque han pasado {days_since_reading} días desde la última lectura de polen "
                       "de la estación: cuantos más días sin lectura, menos segura es la estimación.")
        label = lower
    return label, reason


def _confidence_from_test(lead, pollen_entries):
    """The confidence label from the 2025 test accuracy (or the days ahead, without pollen)."""
    used = [p for p in pollen_entries if p["kind"] != "none"]
    if used:
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
    return label, ("Solo depende de nuestra previsión de calidad del aire: la etiqueta refleja los días de antelación "
                   "(cuanto más lejos, menos acierta; véase el informe de modelos).")


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
    # one more calendar day than the planner shows, because the last night's
    # "madrugada" (00-07) falls on the following calendar day
    calendar_days = config.PLANNER_DAYS + 1

    # ---- forecasts for this place: OUR air model (nearest stations) and the weather (Open-Meteo)
    air, weather = None, None
    air_until = None                                        # the last hour our air forecast reaches
    try:
        air = air_predict.at_point(lat, lon, air_predict.forecast(demo, now))
        if air is not None:
            air_until = air["timestamp"].max()
    except Exception as error:
        data.problem("Nuestra previsión de calidad del aire no está disponible ahora.", error)   # the note is added below
    # today's MEASURED hours at the nearest usable stations (live mode; the demo view starts at 07:00)
    measured_air = None
    if not demo:
        try:
            measured_air = air_predict.measured_at_point(lat, lon, now)
        except Exception as error:
            data.problem("Sin mediciones de hoy para el planificador.", error)
    try:
        weather = data.load_forecast_weather(lat, lon, day_text, calendar_days)
    except Exception as error:
        notes.append(data.problem("Sin previsión del tiempo para este punto.", error))

    # ---- our pollen model: the nearest pollen station that has a forecast
    model_rows, model_station = None, None
    if allergy_types:
        try:
            model_rows, model_station = risk.pollen_forecast_rows(lat, lon, demo)
        except Exception as error:
            notes.append(data.problem("Nuestro modelo de polen no está disponible ahora.", error))

    # ---- today's latest official reading (live mode only): today can never look better than what was measured
    measured = None
    if allergy_types and not demo:
        try:
            measured = risk.pollen_near(lat, lon, data.load_pollen(today=today))
        except Exception:
            pass

    # ---------------- pollen for each CALENDAR day (lead 0 = today), one entry per selected type
    def pollen_of(lead):
        date = today + pd.Timedelta(days=lead)
        entries, app_level = [], None
        for pollen_type in allergy_types:
            entry = risk.pollen_forecast_entry(pollen_type, date, model_rows)
            # today: the official reading is a floor (same rule as the main card)
            if lead == 0 and measured:
                reading = next((t for t in measured["types"] if t["pollen_type"] == pollen_type), None)
                if reading and (entry["level"] is None or reading["level"] > entry["level"]):
                    entry.update(level=reading["level"], level_name=reading["level_name"], kind="measurement", accuracy=None,
                                 source=f"Medición de {measured['station']} del {measured['reading_date']:%d/%m} "
                                        f"(hace {measured['age_days']} días)")
            if entry["level"] is not None:
                level = config.POLLEN_TO_LEVEL[entry["level"]]
                app_level = level if app_level is None else max(app_level, level)
            entries.append(entry)
        return entries, app_level

    pollen_by_lead = [pollen_of(lead) for lead in range(calendar_days)]

    days = []
    for lead in range(config.PLANNER_DAYS):
        date = today + pd.Timedelta(days=lead)
        pollen_entries = pollen_by_lead[lead][0]
        worst_pollen = max((p for p in pollen_entries if p["level"] is not None), key=lambda p: p["level"], default=None)

        # ---------------- the four parts of the day
        blocks = []
        for name, first_hour, last_hour, day_offset in config.PLANNER_BLOCKS:
            block_date = date + pd.Timedelta(days=day_offset)      # the madrugada falls on the next calendar day
            block_pollen, pollen_app_level = pollen_by_lead[lead + day_offset]
            block = {"name": name, "hours": f"{first_hour}–{last_hour} h", "date": block_date,
                     "next_day": bool(day_offset), "air": None, "weather": None, "pollen": block_pollen}
            # ---- AIR for this part of the day, only from data that exists:
            #   past hours of today   -> what the stations MEASURED
            #   hours still to come   -> OUR FORECAST (up to its tested horizon)
            #   the current part      -> measured hours so far + forecast for the rest; if neither
            #                            covers it yet, the latest measurement (it is never empty)
            block_start = block_date + pd.Timedelta(hours=first_hour)
            block_end = block_date + pd.Timedelta(hours=last_hour)
            block["status"] = "past" if block_end <= now else ("now" if block_start <= now else "future")
            in_block = lambda table: table[(table["timestamp"].dt.normalize() == block_date)
                                           & table["timestamp"].dt.hour.between(first_hour, last_hour - 1)]
            measured_hours = in_block(measured_air) if measured_air is not None else None
            last_measured = measured_air["timestamp"].max() if measured_air is not None else None
            parts, kinds = [], []
            if measured_hours is not None and not measured_hours.empty:
                parts.append(measured_hours)
                kinds.append("medida")
            if air is not None and block["status"] != "past":
                future = in_block(air)
                if last_measured is not None:
                    future = future[future["timestamp"] > last_measured]
                if not future.empty:
                    parts.append(future)
                    kinds.append("prevista")
            hours = pd.concat(parts, ignore_index=True) if parts else None
            enough = hours is not None and (block["status"] == "now" or len(hours) >= (last_hour - first_hour) / 2)
            if not enough and block["status"] == "now" and measured_air is not None:
                hours, kinds, enough = measured_air.tail(1), ["última medición"], True   # never leave the current part empty
            if not enough:
                hours, kinds = None, []

            air_level = None
            if hours is not None:
                # One value per pollutant for the block (see config.py, PLANNER_BLOCK_VALUE):
                # the highest hour for NO2, the average of the block for O3 and particles.
                values = {}
                for pollutant in config.ICA_BANDS:
                    if pollutant not in hours:
                        continue
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
                                    "values": {p: round(v) for p, v in values.items()},
                                    "kind": " y ".join(kinds),     # "medida", "prevista", "medida y prevista", "última medición"
                                    # the official advice for that category and this profile
                                    "official_advice": config.ICA_ADVICE_ES[audience][worst[0]]}
            if weather is not None:
                hours = weather[(weather["timestamp"].dt.normalize() == block_date)
                                & weather["timestamp"].dt.hour.between(first_hour, last_hour - 1)]
                if hours["temperature_c"].notna().any():
                    block["weather"] = {"temperature_c": round(float(hours["temperature_c"].mean())),
                                        "rain_mm": round(float(hours["rain_mm"].fillna(0).sum()), 1)}

            known = [lv for lv in (air_level, pollen_app_level) if lv is not None]
            level = max(known) if known else None
            block["level_index"] = level
            block["level_label"] = config.LEVEL_LABELS[level] if level is not None else "Sin previsión"
            # what the level is based on, in words (only what was really used)
            air_words = f"la calidad del aire ({block['air']['kind']})" if block["air"] else None
            if block["air"] is None:
                if block["status"] == "past":
                    why_no_air = "no hay mediciones de aire para este tramo"
                elif air is None:
                    why_no_air = "la previsión de calidad del aire no está disponible ahora"
                else:
                    why_no_air = ("no mostramos previsión de calidad del aire tan lejos, porque a esa distancia ya no "
                                  "mejora claramente a la referencia")
            if level is None:
                block["basis"] = f"Sin nivel: {why_no_air}" + (", y no hay previsión fiable de polen para tus tipos."
                                                              if allergy_types else ".")
            elif air_level is None:
                block["basis"] = f"Solo polen: {why_no_air}."
            elif pollen_app_level is None:
                block["basis"] = (f"Solo {air_words}" + (": no hay previsión fiable de polen para tus tipos." if allergy_types else "."))
            else:
                block["basis"] = f"Lo peor entre el polen del día y {air_words} del tramo."
            block["driver"] = ("polen" if pollen_app_level is not None and pollen_app_level >= (air_level or 0) and pollen_app_level > 0
                               else "aire" if air_level else None)
            blocks.append(block)

        gap = int((date - model_station["last_reading"]).days) if model_station else None
        confidence, confidence_reason = _confidence(lead, pollen_entries, gap)
        days.append({
            "date": date, "lead": lead,
            "label": "Hoy" if lead == 0 and not demo else f"{WEEKDAYS[date.dayofweek]} {date.day}",
            "pollen": pollen_entries,
            "worst_pollen": worst_pollen["name"] if worst_pollen else None,
            "confidence": confidence, "confidence_reason": confidence_reason,
            "blocks": blocks,
        })

    # ---- honest notes about what is missing
    missing_air = [f"{d['label']} {b['name'].lower()}" for d in days for b in d["blocks"]
                   if b["air"] is None and b["status"] == "future"]
    if air is None:
        notes.append("Nuestra previsión de calidad del aire no está disponible ahora: los tramos que aún no han "
                     "llegado no tienen nivel de aire.")
    elif missing_air:
        notes.append(f"Nuestra previsión de calidad del aire solo se muestra hasta {air_predict.horizon_hours()} horas de "
                     "antelación (hasta donde mejoró claramente a la referencia en las pruebas). No llega a: "
                     + ", ".join(missing_air) + ". En esos tramos solo se usan el polen y el tiempo.")
    if model_station and model_station["skipped"]:
        notes.append(f"Sin lecturas de polen recientes en {', '.join(model_station['skipped'])}: "
                     f"se usa la estación de {model_station['station']}.")
    unreliable = sorted({p["name"] for d in days for p in d["pollen"] if p["kind"] == "none"})
    if unreliable:
        notes.append("Sin previsión fiable de polen para: " + ", ".join(unreliable) + " (en alguno de los días).")
    if demo:
        notes.append("Modo demostración: vista del 24 de mayo de 2025. El polen y el aire son las previsiones de nuestros "
                     "modelos con lo que se sabía ese día; el tiempo es el que hizo en esas fechas (archivo de Open-Meteo).")

    return {
        "today": today, "demo": demo, "profile": profile,
        "kind": "PREVISIÓN: nada de esto son mediciones",
        "pollen_station": model_station,
        # which stations the air forecast comes from, and how far ahead it is shown
        "air_stations": air.attrs.get("stations") if air is not None else None,
        "measured_stations": measured_air.attrs.get("stations") if measured_air is not None else None,
        "air_horizon_hours": air_predict.horizon_hours(), "air_until": air_until,
        "days": days, "notes": notes,
    }
