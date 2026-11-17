"""
risk.py - turns the data into advice for one place and one person.

Main function:
    assess(lat, lon, profile, allergy_types)  ->  a dictionary with
        level            "good" / "be careful" / "stay in"  (+ level_index 0-2)
        reason           the main reason, in one Spanish sentence
        recommendations  official texts, word for word, each with its source
        windows          the best 3-hour windows of the next 24 hours, from the FORECAST
        hours            hour-by-hour table of the next 24 hours (FORECAST)
        air, pollen      the details behind the level (stations, values, dates)
        notes            warnings the app should show

All thresholds and texts live in config.py. This file contains no numbers
of its own. Texts for the user are in Spanish (the app is in Spanish).
"""

import logging
import math

import pandas as pd

import config
import data
from air_model import predict as air_predict
from pollen_model import predict as pollen_predict

log = logging.getLogger("respira.risk")


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


def weather_points():
    """
    Every point whose weather the app should keep ready (weather.prefetch):
    the dropdown places, every air station of both networks and every pollen station.
    """
    air = data.load_air()
    points = list(list_places(air).values())
    points += list(air[["lat", "lon"]].drop_duplicates().itertuples(index=False, name=None))
    try:
        pollen = data.load_pollen_all()
        points += list(pollen[["lat", "lon"]].dropna().drop_duplicates().itertuples(index=False, name=None))
    except Exception:
        pass                                        # the places and air stations are enough
    return points


def place_name(lat, lon, places=None):
    """
    A name for any point, the same everywhere in the app.
      - exactly one of the dropdown places  ->  its name ("Getafe")
      - anything else  ->  "Cerca de <nearest municipality or Madrid district>"
    `places` is the dropdown dictionary from list_places().
    """
    places = places or {}
    for name, (p_lat, p_lon) in places.items():
        if distance_km(lat, lon, p_lat, p_lon) < 0.15:
            return name
    candidates = [(row["name"], row["lat"], row["lon"]) for _, row in data.load_gazetteer().iterrows()]
    known = {name for name, _, _ in candidates}
    # towns that only appear in the dropdown (the city centre is covered by the districts)
    candidates += [(name, p_lat, p_lon) for name, (p_lat, p_lon) in places.items()
                   if name not in known and name not in config.PLACES]
    nearest = min(candidates, key=lambda c: distance_km(lat, lon, c[1], c[2]))
    return f"Cerca de {nearest[0]}"


# ----------------------------------------------------------------------------
# Air: nearest station with data for each pollutant (MEASUREMENTS)
# ----------------------------------------------------------------------------

# How each pollutant is named when it is the cause of the level (in the headline)
POLLUTANT_CAUSE = {"NO2": "el dióxido de nitrógeno (NO2)", "O3": "el ozono (O3)",
                   "PM10": "las partículas (PM10)", "PM2.5": "las partículas finas (PM2,5)"}


def is_stale(timestamp, now):
    """True if a reading is too old to describe "now" (config.STALE_HOURS)."""
    return now is not None and now - timestamp > pd.Timedelta(hours=config.STALE_HOURS)


def air_near(lat, lon, air, now=None):
    """
    For each pollutant, the reading of the nearest station that measures it,
    leaving out doubtful readings ("Dato dudoso") and readings older than
    config.STALE_HOURS. Returns a list of dictionaries, one per pollutant.
    Each one lists in "skipped" the nearer stations that were left out, and why.
    """
    readings = []
    air = _add_distance(air, lat, lon)
    for pollutant in config.ICA_BANDS:
        candidates = air[air["pollutant"] == pollutant]
        skipped = []
        for _, row in candidates.iterrows():            # nearest first
            category = ica_category(pollutant, row["ica_value"])
            if category is None:
                continue                                # no usable value: try the next station
            if bool(row.get("doubtful", False)):
                skipped.append({"station": row["station"], "reason": "dato dudoso"})
                continue
            if is_stale(row["timestamp"], now):
                skipped.append({"station": row["station"],
                                "reason": f"sin datos desde el {row['timestamp']:%d/%m a las %H:%M}"})
                continue
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
                "skipped": skipped,
            })
            break
    return readings


def station_categories(air, now=None):
    """
    One row per air station with its ICA category (the worst of its
    pollutants, as the official index does). Used to colour the map.
    Doubtful readings and old readings do not count for the colour:
      doubtful = the station has a "Dato dudoso" (drawn grey on the map)
      stale    = all its readings are older than config.STALE_HOURS (drawn faded)
    """
    rows = []
    for (station, source), group in air.groupby(["station", "source"]):
        details, worst, doubtful = [], None, False
        stale = all(is_stale(t, now) for t in group["timestamp"])
        for _, row in group.iterrows():
            category = ica_category(row["pollutant"], row["ica_value"])
            if category is None:
                continue
            if bool(row.get("doubtful", False)):
                doubtful = True
                details.append(f"{row['pollutant']}: {row['ica_value']:.0f} µg/m³ (dato dudoso, no se usa)")
                continue
            details.append(f"{row['pollutant']}: {row['ica_value']:.0f} µg/m³ ({config.ICA_CATEGORIES[category]})")
            worst = category if worst is None else max(worst, category)
        if worst is None and not doubtful:
            continue
        rows.append({
            "station": station, "source": source,
            "lat": group["lat"].iloc[0], "lon": group["lon"].iloc[0],
            "category": worst, "category_name": config.ICA_CATEGORIES[worst] if worst is not None else "Dato dudoso",
            "details": details, "timestamp": group["timestamp"].max(),
            "doubtful": doubtful, "stale": stale,
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


def pollen_forecast_rows(lat, lon, demo=False):
    """
    OUR pollen model's forecast at the nearest pollen station WITH RECENT DATA
    (a reading in the last config.POLLEN_MAX_AGE_DAYS days; others are skipped).
    Includes the nowcast rows (estimates for the days since its last reading).
    Returns (rows of that station, {"station", "distance_km", "last_reading", "skipped"}),
    or (None, None). "skipped" names the nearer stations left out for having no recent reading.
    """
    table = pollen_predict.forecast(demo)
    if table.empty:
        return None, None
    table = _add_distance(table, lat, lon)
    nowcast = table["nowcast"].fillna(False).astype(bool) if "nowcast" in table else pd.Series(False, index=table.index)
    issued_today = table[(table["lead"] == 0) & ~nowcast]
    issue_day = issued_today["issue_date"].max()
    if "last_reading" in issued_today and issued_today["last_reading"].notna().any():
        last = issued_today.groupby("station", sort=False)["last_reading"].max()      # the station's real latest reading
    else:                                                                            # (older tables: from the model's input age)
        last = issue_day - pd.to_timedelta(issued_today.groupby("station", sort=False)["last_age"].min(), unit="D")
    usable = last[(issue_day - last).dt.days <= config.POLLEN_MAX_AGE_DAYS]
    if usable.empty:
        return None, None
    order = list(issued_today.drop_duplicates("station")["station"])          # nearest first
    chosen = next(name for name in order if name in usable.index)
    skipped = [name for name in order[:order.index(chosen)]]
    rows = table[table["station"] == chosen]
    return rows, {"station": chosen, "distance_km": round(float(rows["distance_km"].iloc[0]), 1),
                  "last_reading": usable[chosen], "skipped": skipped}


def pollen_forecast_entry(pollen_type, date, rows):
    """
    The forecast of one pollen type for one day, from our model:
    {"pollen_type", "name", "level" (0-3 or None), "level_name", "kind": "model" or "none", "source", "accuracy"}.
    Where our model is not reliable for that type and day there is NO forecast
    ("Sin previsión fiable"): no other forecast is used (data rule).
    """
    row = None
    if rows is not None:
        match = rows[(rows["pollen_type"] == pollen_type) & (rows["target_date"] == date)]
        row = match.iloc[0] if len(match) else None
    chosen = pollen_predict.best_source(pollen_type, row)
    entry = {"pollen_type": pollen_type, "name": config.POLLEN_NAMES[pollen_type], **chosen,
             "level_name": config.POLLEN_LEVELS[chosen["level"]] if chosen["level"] is not None else None}
    if row is not None:
        entry["model_level_name"] = config.POLLEN_LEVELS[int(row["predicted_level"])]
        entry["model_reliable"] = bool(row["reliable"])
    return entry


# ----------------------------------------------------------------------------
# Hour-by-hour levels and best windows (FORECAST)
# ----------------------------------------------------------------------------

def hourly_levels(forecast_air, forecast_weather, profile, pollen_by_day=None):
    """
    The forecast table with, for every hour, the app level (0, 1, 2), a
    pollution score (to rank hours of the same level) and what limits it.

    forecast_air   our own air forecast for this place (air_model.predict.at_point)
    pollen_by_day  {date: (app level, pollen name)}: the forecast pollen level of each
                   day for the pollen types the person selected (pollen is a daily
                   value, so every hour of a day gets the same one)
    """
    audience = config.PROFILES[profile]["air"]
    pollen_by_day = pollen_by_day or {}
    if forecast_weather is not None:
        table = forecast_air.merge(
            forecast_weather[["timestamp", "temperature_c", "rain_mm"]], on="timestamp", how="left")
    else:                                               # no weather: the levels do not need it, only the display does
        table = forecast_air.assign(temperature_c=float("nan"), rain_mm=float("nan"))
    levels, scores, reasons, categories = [], [], [], []
    for _, row in table.iterrows():
        level, score, reason, worst_category = 0, 0.0, "", None

        # air: the worst ICA category among the four pollutants
        for pollutant, bands in config.ICA_BANDS.items():
            category = ica_category(pollutant, row[pollutant])
            if category is None:
                continue
            score += row[pollutant] / bands[0]          # 1.0 = top of "Buena"
            worst_category = category if worst_category is None else max(worst_category, category)
            air_level = config.ICA_TO_LEVEL[audience][category]
            if air_level > level:
                level, reason = air_level, pollutant

        # pollen: the forecast level of that day, only for the types the person is allergic to
        day_pollen = pollen_by_day.get(row["timestamp"].normalize())
        if day_pollen is not None:
            score += day_pollen[0]
            if day_pollen[0] > level:
                level, reason = day_pollen[0], f"polen de {day_pollen[1].lower()} (previsión)"

        levels.append(level)
        scores.append(score)
        reasons.append(reason)
        categories.append(worst_category)
    table["level"], table["score"], table["limited_by"] = levels, scores, reasons
    table["air_category"] = categories             # worst ICA category of the hour (for the official advice text)
    return table


def next_hours(all_hours, now):
    """The next 24 hours of the forecast table, starting at the current hour (day and night)."""
    start = now.floor("h")
    end = start + pd.Timedelta(hours=config.HOURS_AHEAD)
    return all_hours[(all_hours["timestamp"] >= start) & (all_hours["timestamp"] < end)].reset_index(drop=True)


def best_windows(hours, now):
    """
    The best windows for being outdoors in the next 24 hours, from the
    hour-by-hour FORECAST table (already cut to those hours by next_hours()).
    Ranked by: lowest level, then no rain, then lowest pollution.
    Only windows at the best level found are returned (up to WINDOW_COUNT).
    Each window says whether it is "hoy" or "mañana".
    """
    length = config.WINDOW_HOURS
    if len(hours) < length:
        return []
    today = now.normalize()

    # every possible run of `length` consecutive hours
    candidates = []
    for start in range(len(hours) - length + 1):
        chunk = hours.iloc[start:start + length]
        rain = chunk["rain_mm"].fillna(0).sum()
        level = int(chunk["level"].max())
        candidates.append({
            "day": "hoy" if chunk["timestamp"].iloc[0].normalize() == today else "mañana",
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
    return sorted(chosen, key=lambda c: c["start"])


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

    # ---- FORECAST for this place: OUR air model (nearest stations), OUR pollen model (nearest
    # pollen station) and the weather forecast (Open-Meteo). Used for the hours and the best windows.
    all_hours, forecast_air = None, None
    try:
        forecast_air = air_predict.at_point(lat, lon, air_predict.forecast(demo, now))
        if forecast_air is None:
            raise ValueError("ninguna estación cercana tiene lecturas recientes")
        try:                                            # the weather only adds temperature and rain
            forecast_weather = data.load_forecast_weather(lat, lon, day_text)
        except Exception as error:
            log.warning("No weather for (%s, %s): %s", lat, lon, error)
            forecast_weather = None
            notes.append("Sin previsión del tiempo ahora mismo: se muestran los niveles sin temperatura ni lluvia.")
        pollen_by_day = {}
        if allergy_types:
            try:
                rows, _ = pollen_forecast_rows(lat, lon, demo)
                for date in (today, today + pd.Timedelta(days=1)):
                    entries = [pollen_forecast_entry(t, date, rows) for t in allergy_types]
                    entries = [e for e in entries if e["level"] is not None]
                    if entries:
                        worst = max(entries, key=lambda e: e["level"])
                        pollen_by_day[date] = (config.POLLEN_TO_LEVEL[worst["level"]], worst["name"])
            except Exception as error:
                notes.append(data.problem("Sin previsión de polen para las próximas horas.", error))
        all_hours = hourly_levels(forecast_air, forecast_weather, profile, pollen_by_day)
    except Exception as error:
        notes.append(data.problem("Sin previsión de calidad del aire, así que no hay mejores horas.", error))

    # ---- AIR (official station measurements)
    air_level, air_worst, air_readings, air_table = None, None, [], None
    try:
        air_table = data.load_replay_air(now) if demo else data.load_air()
        air_readings = air_near(lat, lon, air_table, now)
    except Exception as error:
        notes.append(data.problem("Sin mediciones de calidad del aire.", error))
    if air_readings:
        # worst category; if several tie, the one closest to its next band
        air_worst = max(air_readings, key=lambda r: (
            r["category"], r["ica_value"] / config.ICA_BANDS[r["pollutant"]][r["category"]]))
        air_level = config.ICA_TO_LEVEL[audience][air_worst["category"]]
    # name every nearer station that was left out, and why (once per station)
    left_out = {}
    for reading in air_readings:
        for skip in reading["skipped"]:
            left_out.setdefault((skip["station"], skip["reason"]), []).append(reading["pollutant"])
    for (station, reason), pollutants in left_out.items():
        if reason == "dato dudoso":
            notes.append(f"{station}: {', '.join(pollutants)} con un dato dudoso (muy distinto de todas las demás "
                         "estaciones); no se usa y se toma la siguiente estación más cercana.")
        else:
            notes.append(f"{station}: {', '.join(pollutants)} {reason}; se usa la siguiente estación más cercana.")

    # ---- POLLEN (official station measurement; if no station has a recent reading there is no pollen level)
    pollen, pollen_table = None, None
    try:
        pollen_table = data.load_replay_pollen() if demo else data.load_pollen(today=today)
        pollen = pollen_near(lat, lon, pollen_table)
    except Exception as error:
        notes.append(data.problem("Sin mediciones de polen.", error))
    if pollen is None:
        notes.append("Sin lectura reciente de polen "
                     f"(ninguna estación tiene datos de los últimos {config.POLLEN_MAX_AGE_DAYS} días).")
    elif not demo:
        # name the nearer pollen stations that were skipped for having no recent reading
        try:
            every = _add_distance(data.load_pollen_all(), lat, lon)
            last_reading = every.groupby("station")["timestamp"].max()          # latest reading of each station
            distance = every.groupby("station")["distance_km"].min()
            for station in distance[distance < pollen["distance_km"] - 0.05].sort_values().index:
                notes.append(f"La estación de polen de {station} no tiene lecturas desde el "
                             f"{last_reading[station]:%d/%m}; se usa {pollen['station']} (a {_km(pollen['distance_km'])}).")
        except Exception as error:
            data.problem("No se pudo comprobar qué estaciones de polen están sin datos.", error)

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
    hours, windows = None, []
    if all_hours is not None:
        if pollen_app_level:
            is_today = all_hours["timestamp"].dt.normalize() == today
            worse = is_today & (all_hours["level"] < pollen_app_level)
            all_hours.loc[worse, "level"] = pollen_app_level
            all_hours.loc[worse, "limited_by"] = f"polen de {pollen_worst['name'].lower()} (medición)"
        hours = next_hours(all_hours, now)             # the next 24 hours, starting now
        windows = best_windows(hours, now)
        if hours.empty:
            hours = None
            notes.append("La previsión guardada no cubre las próximas horas, así que no hay mejores horas.")

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
        age = "de hoy" if pollen["age_days"] == 0 else f"de hace {pollen['age_days']} días"
        where = (f"{pollen_worst['value']:.0f} granos/m³ en {pollen['station']}, "
                 f"lectura del {pollen['reading_date']:%d/%m}, {age}")
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

    # ---- the headline names its cause: "Precaución por polen de gramíneas", "Mejor quédate en casa por el ozono (O3)"
    cause = None
    if level:                                           # "Puedes salir" needs no cause
        if pollen_first:
            cause = f"polen de {pollen_worst['name'].lower()}"
        elif air_worst:
            cause = POLLUTANT_CAUSE.get(air_worst["pollutant"], air_worst["pollutant"])
    headline = config.LEVEL_LABELS[level] + (f" por {cause}" if cause else "") if level is not None else None

    return {
        "profile": profile,
        "level_index": level,
        "headline": headline,                           # the level with its cause
        "cause": cause,
        "level": config.LEVELS[level] if level is not None else "unknown",
        "reason": reason,
        "recommendations": recommendations,             # list of (source, official text)
        "profile_note": settings["note"],
        "windows": windows,
        "hours": hours,                                 # the next 24 hours
        "all_hours": all_hours,                         # everything the forecast covers (today and tomorrow)
        "air": air_readings,
        "air_table": air_table,
        "pollen": pollen,
        "pollen_table": pollen_table,
        "forecast": forecast_air,                       # our air forecast for this place (None if there is none)
        "notes": notes,
        "time": now,
    }
