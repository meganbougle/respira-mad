"""
risk.py - turns the data into advice for one place and one person.

Main function:
    assess(lat, lon, profile, allergy_types)  ->  a dictionary with
        level            "good" / "be careful" / "stay in"  (+ level_index 0-2)
        reason           the main reason, in one Spanish sentence
        recommendations  official texts, word for word, each with its source
        windows          the best 2-3 hour windows, from the FORECAST
        hours            hour-by-hour table for the chart (FORECAST)
        air, pollen      the details behind the level (stations, values, dates)
        notes            warnings the app should show

All thresholds and texts live in config.py. This file contains no numbers
of its own. Texts for the user are in Spanish (the app is in Spanish).
"""

import math

import pandas as pd

import config
import data


# ----------------------------------------------------------------------------
# Small building blocks
# ----------------------------------------------------------------------------

def distance_km(lat1, lon1, lat2, lon2):
    """Distance between two points on Earth, in km (haversine formula)."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def _add_distance(table, lat, lon):
    """Copy of a station table with a distance_km column, nearest first."""
    table = table.copy()
    table["distance_km"] = [distance_km(lat, lon, la, lo) for la, lo in zip(table["lat"], table["lon"])]
    return table.sort_values("distance_km")


def ica_category(pollutant, value):
    """
    Position of a concentration in the official ICA table:
    0 = Buena ... 5 = Extremadamente desfavorable.
    Returns None if there is no value, or if it is above the table's maximum
    (the official method treats those as measurement errors).
    """
    if value is None or pd.isna(value):
        return None
    value = round(value)                 # the official bands are whole numbers
    for position, upper_limit in enumerate(config.ICA_BANDS[pollutant]):
        if value <= upper_limit:
            return position
    return None


def pollen_level(pollen_type, value):
    """
    Position of a pollen count in the Palinocam levels:
    0 = bajo, 1 = medio, 2 = alto, 3 = muy alto.
    Returns None for pollen types that have no official threshold.
    """
    if pollen_type not in config.POLLEN_THRESHOLDS or value is None or pd.isna(value):
        return None
    level = 0
    for threshold in config.POLLEN_THRESHOLDS[pollen_type]:
        if value >= threshold:
            level += 1
    return level


def madrid_now():
    """Current time in Madrid (the same clock the data feeds use)."""
    return pd.Timestamp.now(tz="Europe/Madrid").tz_localize(None)


def _km(distance):
    """Distance the Spanish way: 9,4 km."""
    return f"{distance:.1f}".replace(".", ",") + " km"


def list_places(air):
    """
    Places for the location dropdown: {name: (lat, lon)}.
    The ones in config.PLACES, plus every municipality that has a Comunidad
    air station (placed at the station).
    """
    places = dict(config.PLACES)
    known = {name.lower().replace("-", " ") for name in places}
    stations = air[air["source"] == "Comunidad de Madrid"].drop_duplicates("station").sort_values("station")
    for _, row in stations.iterrows():
        if row["station"].lower().replace("-", " ") not in known:
            places[row["station"]] = (round(row["lat"], 4), round(row["lon"], 4))
    return places


# ----------------------------------------------------------------------------
# Air: nearest station with data for each pollutant (MEASUREMENTS)
# ----------------------------------------------------------------------------

def air_near(lat, lon, air):
    """
    For each pollutant, the reading of the nearest station that measures it.
    Returns a list of dictionaries, one per pollutant.
    """
    readings = []
    air = _add_distance(air, lat, lon)
    for pollutant in config.ICA_BANDS:
        candidates = air[air["pollutant"] == pollutant]
        for _, row in candidates.iterrows():            # nearest first
            category = ica_category(pollutant, row["ica_value"])
            if category is None:
                continue                                # no usable value: try the next station
            readings.append({
                "pollutant": pollutant,
                "station": row["station"],
                "source": row["source"],
                "distance_km": round(row["distance_km"], 1),
                "value": row["value"],                  # latest hourly reading
                "ica_value": row["ica_value"],          # the average the index uses
                "ica_hours": int(row["ica_hours"]),
                "timestamp": row["timestamp"],
                "provisional": row["flag"] == "T",
                "category": category,
                "category_name": config.ICA_CATEGORIES[category],
            })
            break
    return readings


def station_categories(air):
    """
    One row per air station with its ICA category (the worst of its
    pollutants, as the official index does). Used to colour the map.
    """
    rows = []
    for (station, source), group in air.groupby(["station", "source"]):
        details, worst = [], None
        for _, row in group.iterrows():
            category = ica_category(row["pollutant"], row["ica_value"])
            if category is None:
                continue
            details.append(f"{row['pollutant']}: {row['ica_value']:.0f} µg/m³ ({config.ICA_CATEGORIES[category]})")
            worst = category if worst is None else max(worst, category)
        if worst is None:
            continue
        rows.append({
            "station": station, "source": source,
            "lat": group["lat"].iloc[0], "lon": group["lon"].iloc[0],
            "category": worst, "category_name": config.ICA_CATEGORIES[worst],
            "details": details, "timestamp": group["timestamp"].max(),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Pollen: nearest station with a recent reading (MEASUREMENTS)
# ----------------------------------------------------------------------------

def _pollen_entry(pollen_type, value):
    level = pollen_level(pollen_type, value)
    return {
        "pollen_type": pollen_type,
        "name": config.POLLEN_NAMES[pollen_type],
        "value": value,
        "level": level,
        "level_name": config.POLLEN_LEVELS[level],
        # how close the value is to the "medio" threshold (to pick the main type)
        "ratio": value / config.POLLEN_THRESHOLDS[pollen_type][0],
    }


def pollen_near(lat, lon, pollen):
    """
    The nearest pollen station (data.load_pollen has already dropped the
    stations whose reading is too old). Returns None if there is none.
    """
    if pollen.empty:
        return None
    pollen = _add_distance(pollen, lat, lon)
    nearest = pollen.iloc[0]
    station_rows = pollen[pollen["station"] == nearest["station"]]

    types = [_pollen_entry(row["pollen_type"], row["value"])
             for _, row in station_rows.iterrows()
             if pollen_level(row["pollen_type"], row["value"]) is not None]
    types.sort(key=lambda t: (t["level"], t["ratio"]), reverse=True)

    return {
        "kind": "measurement",
        "station": nearest["station"],
        "distance_km": round(nearest["distance_km"], 1),
        "reading_date": nearest["timestamp"],
        "age_days": int(nearest["age_days"]),
        "types": types,                                 # worst first
    }


def pollen_from_forecast(forecast_air, day):
    """
    Used only when no pollen station has a recent reading: the average of the
    day's hourly FORECAST for the two types that have an official threshold
    (grass and olive). Returns None if the forecast has no data for that day.
    """
    hours = forecast_air[forecast_air["timestamp"].dt.normalize() == day]
    types = []
    for column, official_type in config.FORECAST_POLLEN_TO_OFFICIAL.items():
        value = hours[column].mean()
        if pd.notna(value):
            types.append(_pollen_entry(official_type, round(float(value), 1)))
    if not types:
        return None
    types.sort(key=lambda t: (t["level"], t["ratio"]), reverse=True)
    return {"kind": "forecast", "station": "Open-Meteo", "distance_km": 0.0,
            "reading_date": day, "age_days": 0, "types": types}


# ----------------------------------------------------------------------------
# Hour-by-hour levels and best windows (FORECAST)
# ----------------------------------------------------------------------------

def hourly_levels(forecast_air, forecast_weather, profile, allergy_types=()):
    """
    The forecast table with, for every hour, the app level (0, 1, 2), a
    pollution score (to rank hours of the same level) and what limits it.
    """
    audience = config.PROFILES[profile]["air"]
    table = forecast_air.merge(
        forecast_weather[["timestamp", "temperature_c", "rain_mm"]], on="timestamp", how="left")
    levels, scores, reasons = [], [], []
    for _, row in table.iterrows():
        level, score, reason = 0, 0.0, ""

        # air: the worst ICA category among the four pollutants
        for pollutant, bands in config.ICA_BANDS.items():
            category = ica_category(pollutant, row[pollutant])
            if category is None:
                continue
            score += row[pollutant] / bands[0]          # 1.0 = top of "Buena"
            air_level = config.ICA_TO_LEVEL[audience][category]
            if air_level > level:
                level, reason = air_level, pollutant

        # pollen: only the types the person says they are allergic to
        for column, official_type in config.FORECAST_POLLEN_TO_OFFICIAL.items():
            if official_type not in allergy_types:
                continue
            p_level = pollen_level(official_type, row[column])
            if p_level is None:
                continue
            score += row[column] / config.POLLEN_THRESHOLDS[official_type][0]
            app_level = config.POLLEN_TO_LEVEL[p_level]
            if app_level > level:
                level, reason = app_level, f"polen de {config.POLLEN_NAMES[official_type].lower()}"

        levels.append(level)
        scores.append(score)
        reasons.append(reason)
    table["level"], table["score"], table["limited_by"] = levels, scores, reasons
    return table


def best_windows(hours, now):
    """
    The best windows for being outdoors, from the hour-by-hour FORECAST table.
    Ranked by: lowest level, then no rain, then lowest pollution.
    Only windows at the best level found are returned (up to WINDOW_COUNT).
    If today has too few hours left, the windows are for tomorrow.
    Returns (windows, day): a list of dictionaries and the day they are for.
    """
    hours = hours[hours["timestamp"].dt.hour.between(config.WINDOW_FIRST_HOUR, config.WINDOW_LAST_HOUR - 1)]

    for day_name, day in (("hoy", now.normalize()), ("mañana", now.normalize() + pd.Timedelta(days=1))):
        day_hours = hours[(hours["timestamp"].dt.normalize() == day) & (hours["timestamp"] >= now.floor("h"))]
        day_hours = day_hours.reset_index(drop=True)
        length = config.WINDOW_HOURS if len(day_hours) >= config.WINDOW_HOURS else 2
        if len(day_hours) < length:
            continue                                    # too late today: try tomorrow

        # every possible run of `length` consecutive hours
        candidates = []
        for start in range(len(day_hours) - length + 1):
            chunk = day_hours.iloc[start:start + length]
            rain = chunk["rain_mm"].fillna(0).sum()
            level = int(chunk["level"].max())
            candidates.append({
                "day": day_name,
                "start": chunk["timestamp"].iloc[0],
                "end": chunk["timestamp"].iloc[-1] + pd.Timedelta(hours=1),
                "level_index": level,
                "level": config.LEVELS[level],
                "limited_by": chunk.loc[chunk["level"].idxmax(), "limited_by"],
                "rain_mm": round(float(rain), 1),
                "temperature_c": round(float(chunk["temperature_c"].mean()), 1),
                "kind": data.FORECAST_LABEL,
                "_sort": (level, rain > 0, chunk["score"].mean()),
            })
        candidates.sort(key=lambda c: c["_sort"])

        # take the best ones that do not overlap each other
        chosen = []
        for candidate in candidates:
            overlaps = any(candidate["start"] < c["end"] and c["start"] < candidate["end"] for c in chosen)
            if not overlaps:
                chosen.append(candidate)
            if len(chosen) == config.WINDOW_COUNT:
                break
        # only keep windows as good as the best one (never suggest a worse level)
        best_level = chosen[0]["_sort"][0]
        chosen = [c for c in chosen if c["_sort"][0] == best_level]
        for c in chosen:
            del c["_sort"]
        return sorted(chosen, key=lambda c: c["start"]), day
    return [], now.normalize()


# ----------------------------------------------------------------------------
# The main function
# ----------------------------------------------------------------------------

def assess(lat, lon, profile="general", allergy_types=(), now=None, demo=False):
    """
    Risk level, reason, official recommendations and best windows for one
    place and one person.
      profile        a key of config.PROFILES
      allergy_types  pollen types the person is allergic to (keys of
                     config.POLLEN_THRESHOLDS); empty = no pollen allergy
      demo           True = use the stored demo day instead of live data
    """
    settings = config.PROFILES[profile]
    audience = settings["air"]
    now = now or madrid_now()
    today = now.normalize()
    day_text = config.REPLAY_DATE if demo else None
    notes = []                                          # warnings for the app

    # ---- FORECAST (Open-Meteo, for this exact point): chart, windows, pollen fallback
    all_hours, forecast_air = None, None
    try:
        forecast_air = data.load_forecast_air(round(lat, 2), round(lon, 2), day_text)
        forecast_weather = data.load_forecast_weather(round(lat, 2), round(lon, 2), day_text)
        all_hours = hourly_levels(forecast_air, forecast_weather, profile, allergy_types)
    except Exception as error:
        notes.append(f"Sin previsión disponible, así que no hay mejores horas ({error}).")

    # ---- AIR (official station measurements)
    air_level, air_worst, air_readings, air_table = None, None, [], None
    try:
        air_table = data.load_replay_air(now) if demo else data.load_air()
        air_readings = air_near(lat, lon, air_table)
    except Exception as error:
        notes.append(f"Sin mediciones de calidad del aire ({error}).")
    if air_readings:
        # worst category; if several tie, the one closest to its next band
        air_worst = max(air_readings, key=lambda r: (
            r["category"], r["ica_value"] / config.ICA_BANDS[r["pollutant"]][r["category"]]))
        air_level = config.ICA_TO_LEVEL[audience][air_worst["category"]]
        oldest = min(r["timestamp"] for r in air_readings)
        if now - oldest > pd.Timedelta(hours=6):
            notes.append(f"Algunas mediciones de aire son antiguas (del {oldest:%d/%m a las %H:%M}).")

    # ---- POLLEN (station measurement; forecast only if there is no recent reading)
    pollen, pollen_table = None, None
    try:
        pollen_table = data.load_replay_pollen() if demo else data.load_pollen(today=today)
        pollen = pollen_near(lat, lon, pollen_table)
    except Exception as error:
        notes.append(f"Sin mediciones de polen ({error}).")
    if pollen is None:
        notes.append("Sin lectura reciente de polen "
                     f"(ninguna estación tiene datos de los últimos {config.POLLEN_MAX_AGE_DAYS} días).")
        if forecast_air is not None:
            pollen = pollen_from_forecast(forecast_air, today)
            if pollen:
                notes.append("Se usa la PREVISIÓN de Open-Meteo para gramíneas y olivo (modelo, no medición).")

    # the allergy rule: only for the pollen types the person selected
    pollen_app_level, pollen_worst = None, None
    if allergy_types and pollen:
        selected = [t for t in pollen["types"] if t["pollen_type"] in allergy_types]
        found = {t["pollen_type"] for t in selected}
        missing = [config.POLLEN_NAMES[t] for t in allergy_types if t not in found]
        if missing:
            notes.append("Sin datos de polen para: " + ", ".join(missing) + ".")
        if selected:
            pollen_worst = selected[0]                  # the list is sorted worst first
            pollen_app_level = config.POLLEN_TO_LEVEL[pollen_worst["level"]]

    # ---- BEST WINDOWS. The pollen station reading counts for the whole day:
    # no hour of today can be better than the measured pollen level, even if
    # the model forecasts less pollen (see config.py, section 4).
    hours, windows, windows_day = None, [], today
    if all_hours is not None:
        if pollen_app_level and pollen["kind"] == "measurement":
            is_today = all_hours["timestamp"].dt.normalize() == today
            worse = is_today & (all_hours["level"] < pollen_app_level)
            all_hours.loc[worse, "level"] = pollen_app_level
            all_hours.loc[worse, "limited_by"] = f"polen de {pollen_worst['name'].lower()} (medición)"
        windows, windows_day = best_windows(all_hours, now)
        hours = all_hours[all_hours["timestamp"].dt.normalize() == windows_day]
        hours = hours[hours["timestamp"].dt.hour.between(config.WINDOW_FIRST_HOUR, config.WINDOW_LAST_HOUR - 1)]
        if hours.empty:
            hours = None
            notes.append("La previsión guardada no cubre el día de hoy, así que no hay mejores horas.")

    # ---- the two sentences and the two official texts
    air_text, air_advice, pollen_text, pollen_advice = None, None, None, None
    if air_worst:
        air_text = (f"Calidad del aire «{air_worst['category_name']}» (ICA): {air_worst['pollutant']} "
                    f"{air_worst['ica_value']:.0f} µg/m³ en {air_worst['station']}, "
                    f"a {_km(air_worst['distance_km'])}.")
        group = "población general" if audience == "general" else "grupos de riesgo y personas sensibles"
        air_advice = (
            f"Índice Nacional de Calidad del Aire (BOE-A-2020-10426), {group}, "
            f"categoría «{air_worst['category_name']}»",
            config.ICA_ADVICE_ES[audience][air_worst["category"]])
    if pollen_worst:
        if pollen["kind"] == "measurement":
            age = "de hoy" if pollen["age_days"] == 0 else f"de hace {pollen['age_days']} días"
            where = (f"{pollen_worst['value']:.0f} granos/m³ en {pollen['station']}, "
                     f"lectura del {pollen['reading_date']:%d/%m}, {age}")
        else:
            where = f"{pollen_worst['value']:.0f} granos/m³, PREVISIÓN de Open-Meteo para hoy"
        pollen_text = f"Polen de {pollen_worst['name'].lower()} en nivel {pollen_worst['level_name']} ({where})."
        if pollen_app_level >= 1:                       # the leaflet's advice is for high-pollen days
            pollen_advice = (
                "Comunidad de Madrid, «Lo que debes conocer sobre la alergia al polen» "
                "(medidas preventivas para los días de máxima concentración de polen)",
                " ".join(config.POLLEN_ADVICE_ES))

    # ---- OVERALL LEVEL = the worse of air and pollen; the worse one goes first
    known = [lv for lv in (air_level, pollen_app_level) if lv is not None]
    level = max(known) if known else None
    pollen_first = pollen_app_level is not None and pollen_app_level >= 1 and pollen_app_level >= (air_level or 0)
    ordered = [(pollen_text, pollen_advice), (air_text, air_advice)]
    if not pollen_first:
        ordered.reverse()
    reason = " ".join(text for text, _ in ordered if text) or "No hay mediciones disponibles en este momento."
    recommendations = [advice for _, advice in ordered if advice]

    return {
        "profile": profile,
        "level_index": level,
        "level": config.LEVELS[level] if level is not None else "unknown",
        "reason": reason,
        "recommendations": recommendations,             # list of (source, official text)
        "profile_note": settings["note"],
        "windows": windows,
        "windows_day": windows_day,
        "hours": hours,
        "air": air_readings,
        "air_table": air_table,
        "pollen": pollen,
        "pollen_table": pollen_table,
        "forecast": forecast_air,                       # None if there is no forecast
        "notes": notes,
        "time": now,
    }
