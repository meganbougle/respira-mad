"""
data.py - loads all the live data for Respira MAD.

One public function per source:
    load_comunidad_air()  -> Comunidad de Madrid air quality (28 stations)
    load_city_air()       -> Ayuntamiento de Madrid air quality (city stations)
    load_air()            -> both networks merged into one table
    load_pollen()         -> Comunidad pollen levels (Red Palinocam)
    load_weather()        -> hourly weather from the Comunidad air network
    load_forecast_weather(lat, lon) -> Open-Meteo weather FORECAST (model)
    load_replay_air(now), load_replay_pollen() -> the demo day (28 May 2025)

MEASUREMENTS (the first five) come from official stations.
The weather forecast comes from a computer model (Open-Meteo): weather is the
only thing taken from outside the Comunidad and the Ayuntamiento de Madrid.
Air-quality and pollen FORECASTS are our own, made from the official
measurements: see air_model/predict.py and pollen_model/predict.py.

Every function:
  * is cached for 15 minutes (so we don't hammer the open-data portals),
  * saves a backup copy in the backup/ folder each time the live feed works,
  * falls back to that backup copy if the live feed fails.

To know whether a table is live or from backup:  df.attrs["live"]  (True/False)
When it was downloaded:                          df.attrs["fetched_at"]
"""

import io
import logging
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

import config

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

CACHE_SECONDS = 15 * 60                          # 15 minutes
BACKUP_DIR = Path(__file__).parent / "backup"    # backup/ next to this file
TIMEOUT = 30                                     # seconds to wait for a download

# Comunidad de Madrid open data (CKAN API)
CKAN_API = "https://datos.comunidad.madrid/api/3/action/"

# Ayuntamiento de Madrid open data
CITY_AIR_URL = "https://datos.madrid.es/egob/catalogo/212531-10515086-calidad-aire-tiempo-real.csv"
CITY_STATIONS_URL = "https://datos.madrid.es/egob/catalogo/212629-1-estaciones-control-aire.csv"

# Both air networks use the same numeric codes ("magnitud") for pollutants.
POLLUTANTS = {8: "NO2", 14: "O3", 10: "PM10", 9: "PM2.5"}

# Weather codes used by the Comunidad air network.
WEATHER_VARIABLES = {
    81: "wind_speed_ms",
    82: "wind_direction_deg",
    83: "temperature_c",
    86: "humidity_pct",
    87: "pressure_mb",
    88: "solar_radiation_wm2",
    89: "rain_mm",
}

# Validity flags in the hourly files: V = validated, T = provisional (not yet
# validated, this is what the Comunidad "today" file uses), N = no data.
GOOD_FLAGS = ["V", "T"]

# value     = the latest hourly reading
# ica_value = the average the official air index (ICA) uses (see config.py)
# ica_hours = how many hours went into that average
# flag      = V (validated) or T (provisional)
AIR_COLUMNS = ["station", "source", "lat", "lon", "pollutant", "value", "timestamp",
               "ica_value", "ica_hours", "flag"]

# Open-Meteo (free, no key): WEATHER ONLY. Data licence: CC BY 4.0 - the app must credit Open-Meteo.
# DATA RULE: air quality and pollen never come from Open-Meteo, only from the Comunidad and the Ayuntamiento.
FORECAST_LABEL = "FORECAST (model)"


# ----------------------------------------------------------------------------
# Small helpers (names start with _ because the app won't call them directly)
# ----------------------------------------------------------------------------

def _read_csv(url, encoding="utf-8-sig", columns=None):
    """
    Download a semicolon-separated CSV and return it as a table of text.
    `columns` (lower-case names): keep only these, to save memory on big files.
    MEMORY: reads straight from the downloaded bytes (no extra text copies).
    """
    response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()                     # stop here if the download failed
    wanted = None if columns is None else (lambda name: name.strip().lower() in columns)
    try:
        table = pd.read_csv(io.BytesIO(response.content), sep=";", dtype=str, encoding=encoding, usecols=wanted)
    except UnicodeDecodeError:
        # Some Comunidad files are Latin-1, not UTF-8 (e.g. the 2025 pollen history,
        # found 18 Nov 2026: read as UTF-8 it lost "Gramíneas", "Plátano de paseo"...).
        table = pd.read_csv(io.BytesIO(response.content), sep=";", dtype=str, encoding="latin-1", usecols=wanted)
    del response                                    # the raw download is no longer needed
    table.columns = table.columns.str.strip().str.lower()   # tidy column names
    return table


def _ckan_csv_url(dataset_id):
    """Ask the Comunidad CKAN API where the CSV file of a dataset lives."""
    response = requests.get(CKAN_API + "package_show", params={"id": dataset_id}, timeout=TIMEOUT)
    response.raise_for_status()
    for resource in response.json()["result"]["resources"]:
        if resource["format"].upper() == "CSV":
            return resource["url"]
    raise ValueError(f"No CSV file found in dataset {dataset_id}")


def problem(message, error):
    """
    A short Spanish message for the user; the technical details (English text,
    URLs, error type) go only to the server log. Use it for every note shown
    on screen, so no visitor ever sees a technical error.
    """
    logging.getLogger("respira").warning("%s | %s: %s", message, type(error).__name__, error)
    return message


def _madrid_now():
    """Current time in Madrid, without time zone (same clock as the feeds)."""
    return pd.Timestamp.now(tz="Europe/Madrid").tz_localize(None)


def _to_number(text_column):
    """Turn text like '40,45' or '40.45' into a number (empty -> NaN)."""
    return pd.to_numeric(text_column.str.strip().str.replace(",", ".", regex=False), errors="coerce")


def _hourly_long(table, codes, now=None):
    """
    The hourly files have one row per station + measurement + day, with 24
    value columns (h01..h24) and 24 flag columns (v01..v24).
    This returns one row per station + measurement + HOUR, keeping only the
    good readings (flag V or T; readings flagged N are dropped).

    Hours that have not happened yet are ALWAYS dropped, whatever their flag
    says: h24 of today is midnight at the END of today, so it cannot exist
    until tomorrow starts. (On 15 Nov 2026 the city feed showed a station
    with a reading stamped 16/11 00:00 at 18:05.)

    `codes` is a dictionary {numeric code: friendly name}, e.g. POLLUTANTS.
    `now` is the current Madrid time (only passed in by the tests).
    """
    table = table.copy()
    table["name"] = pd.to_numeric(table["magnitud"]).map(codes)
    table = table.dropna(subset=["name"])            # keep only the codes we want

    # 8-digit station code, e.g. province 28 + municipality 079 + station 004
    table["station_code"] = (
        table["provincia"].str.zfill(2)
        + table["municipio"].str.zfill(3)
        + table["estacion"].str.zfill(3)
    )
    day = pd.to_datetime(table["ano"] + "-" + table["mes"] + "-" + table["dia"])

    # Stack the 24 hours on top of each other: one row per hour.
    hours = []
    for hour in range(1, 25):
        one_hour = table[["station_code", "name"]].copy()
        one_hour["value"] = _to_number(table[f"h{hour:02d}"])
        one_hour["flag"] = table[f"v{hour:02d}"]
        # h01 is the hour ending at 01:00 ... h24 ends at 00:00 the next day.
        one_hour["timestamp"] = day + pd.Timedelta(hours=hour)
        hours.append(one_hour)
    long = pd.concat(hours)

    long = long[long["flag"].isin(GOOD_FLAGS)].dropna(subset=["value"])
    long = long[long["timestamp"] <= (now or _madrid_now())]      # nothing from the future
    return long.sort_values("timestamp").reset_index(drop=True)


def _latest_hourly(long, average_hours=None):
    """
    From the hour-by-hour table, keep ONE row per station + measurement: the
    most recent reading. Also adds:
      ica_value = average of the last N hours up to that reading
      ica_hours = how many hours were actually available for that average
    `average_hours` is a dictionary {name: N}; names not in it use N = 1.
    """
    average_hours = average_hours or {}
    rows = []
    for (station_code, name), group in long.groupby(["station_code", "name"]):
        latest = group.iloc[-1]                       # rows are sorted by time
        n_hours = average_hours.get(name, 1)
        window_start = latest["timestamp"] - pd.Timedelta(hours=n_hours)
        window = group[group["timestamp"] > window_start]
        rows.append({
            "station_code": station_code,
            "name": name,
            "value": latest["value"],
            "timestamp": latest["timestamp"],
            "ica_value": round(window["value"].mean(), 1),
            "ica_hours": len(window),
            "flag": latest["flag"],
        })
    return pd.DataFrame(rows)


def _drop_future(table):
    """Safety net for measurements: remove any row stamped later than now."""
    kept = table[table["timestamp"] <= _madrid_now()].reset_index(drop=True)
    kept.attrs.update(table.attrs)
    return kept


def _with_backup(name, fetch, date_columns=("timestamp",)):
    """
    Run `fetch()` to get live data. If it works, save a copy to
    backup/<name>.csv. If it fails, load that copy instead.
    """
    backup_file = BACKUP_DIR / f"{name}.csv"
    try:
        table = fetch()
        if table.empty:
            raise ValueError("the live feed returned no usable rows")
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(backup_file, index=False)
        table.attrs["live"] = True
        table.attrs["fetched_at"] = _madrid_now()
    except Exception as error:
        if not backup_file.exists():
            raise RuntimeError(f"Live feed '{name}' failed and there is no backup yet: {error}")
        table = pd.read_csv(backup_file, parse_dates=list(date_columns))
        table.attrs["live"] = False
        table.attrs["error"] = str(error)
        # when the backup copy was saved
        saved = pd.Timestamp(backup_file.stat().st_mtime, unit="s", tz="UTC")
        table.attrs["fetched_at"] = saved.tz_convert("Europe/Madrid").tz_localize(None)
    return table


# ----------------------------------------------------------------------------
# 1. Comunidad de Madrid air quality
# ----------------------------------------------------------------------------

def _fetch_comunidad_air():
    hourly = _hourly_long(_read_csv(_ckan_csv_url("calidad_aire_datos_dia")), POLLUTANTS)
    if hourly.empty:
        # Normal for an hour or two after midnight: the new day's file exists
        # but has no readings yet. Raising here makes the loader use the last
        # good copy in backup/, with a clear message in the server log.
        raise ValueError("Comunidad 'today' file has no readings yet (normal just after midnight)")
    readings = _latest_hourly(hourly, config.ICA_AVERAGING_HOURS)

    stations = _read_csv(_ckan_csv_url("calidad_aire_estaciones"))
    stations = pd.DataFrame({
        "station_code": stations["estacion_codigo"],
        "station": stations["estacion_municipio"],   # this network names stations by town
        "lat": _to_number(stations["estacion_coord_latitud"]),
        "lon": _to_number(stations["estacion_coord_longitud"]),
    })

    table = readings.merge(stations, on="station_code", how="left")
    table["source"] = "Comunidad de Madrid"
    table = table.rename(columns={"name": "pollutant"})
    return table[AIR_COLUMNS]


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_comunidad_air():
    """Latest NO2, O3, PM10, PM2.5 per station of the Comunidad network."""
    return _drop_future(_with_backup("comunidad_air", _fetch_comunidad_air))


# ----------------------------------------------------------------------------
# 2. Ayuntamiento de Madrid air quality (real time)
# ----------------------------------------------------------------------------

def _fetch_city_air():
    hourly = _hourly_long(_read_csv(CITY_AIR_URL), POLLUTANTS)
    readings = _latest_hourly(hourly, config.ICA_AVERAGING_HOURS)

    stations = _read_csv(CITY_STATIONS_URL)
    stations = pd.DataFrame({
        "station_code": stations["codigo"],
        "station": stations["estacion"].str.strip(),
        "lat": _to_number(stations["latitud"]),
        "lon": _to_number(stations["longitud"]),
    })

    table = readings.merge(stations, on="station_code", how="left")
    table["source"] = "Ayuntamiento de Madrid"
    table = table.rename(columns={"name": "pollutant"})
    return table[AIR_COLUMNS]


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_city_air():
    """Latest NO2, O3, PM10, PM2.5 per station of the Madrid city network."""
    return _drop_future(_with_backup("city_air", _fetch_city_air))


# ----------------------------------------------------------------------------
# Both air networks in one table
# ----------------------------------------------------------------------------

def _band(pollutant, value):
    """Position of a value in the official ICA bands (0 = Buena ... 5); None if no value."""
    if pd.isna(value):
        return None
    return next((i for i, top in enumerate(config.ICA_BANDS[pollutant]) if round(value) <= top), None)


_doubtful_logged = set()          # (station, pollutant, time) already written to the log


def mark_doubtful(table):
    """
    Add the column `doubtful`: True for a reading that is far out of line
    with every other station (the "Dato dudoso" rule in config.py).
    """
    table = table.copy()
    table["doubtful"] = False
    for pollutant, group in table.groupby("pollutant"):
        if pollutant not in config.ICA_BANDS or len(group) < 3:
            continue                                  # too few stations to say what is typical
        for index, row in group.iterrows():
            others = group.drop(index)["ica_value"].dropna()
            typical = others.median()
            category, typical_category = _band(pollutant, row["ica_value"]), _band(pollutant, typical)
            if (category is not None and typical_category is not None
                    and category >= config.DOUBTFUL_MIN_CATEGORY
                    and typical_category <= config.DOUBTFUL_TYPICAL_MAX_CATEGORY
                    and row["ica_value"] > config.DOUBTFUL_FACTOR * max(typical, 1)):
                table.loc[index, "doubtful"] = True
                key = (row["station"], pollutant, row["timestamp"])
                if key in _doubtful_logged:
                    continue                          # already in the log
                _doubtful_logged.add(key)
                logging.getLogger("respira").warning(
                    "Dato dudoso: %s (%s) %s %.0f µg/m3, typical station %.0f", row["station"], row["source"],
                    pollutant, row["ica_value"], typical)
    return table


def load_air():
    """
    Both air networks merged, with columns:
    station, source, lat, lon, pollutant, value, timestamp
    (plus ica_value, ica_hours, flag - see AIR_COLUMNS at the top).
    If one network fails completely (no live data AND no backup), the other
    one is still returned.
    """
    tables = []
    for loader in (load_comunidad_air, load_city_air):
        try:
            tables.append(loader())
        except Exception as error:
            print(f"Warning: {loader.__name__} failed: {error}")
    if not tables:
        raise RuntimeError("No air quality data available (live or backup).")
    merged = mark_doubtful(pd.concat(tables, ignore_index=True))
    merged.attrs["live"] = all(t.attrs.get("live", False) for t in tables) and len(tables) == 2
    merged.attrs["fetched_at"] = min(t.attrs["fetched_at"] for t in tables)
    return merged


# columns of the hourly files that _hourly_long needs (MEMORY: the rest are not read)
HOURLY_COLUMNS = ({"provincia", "municipio", "estacion", "magnitud", "ano", "mes", "dia"}
                  | {f"h{h:02d}" for h in range(1, 25)} | {f"v{h:02d}" for h in range(1, 25)})


def _fetch_air_today_hourly():
    """Today's hour-by-hour NO2, O3, PM10, PM2.5 of both networks (station_code, pollutant, value, timestamp)."""
    parts = []
    for fetch in (lambda: _read_csv(_ckan_csv_url("calidad_aire_datos_dia"), columns=HOURLY_COLUMNS),   # Comunidad
                  lambda: _read_csv(CITY_AIR_URL, columns=HOURLY_COLUMNS)):                              # Ayuntamiento
        try:
            parts.append(_hourly_long(fetch(), POLLUTANTS)[["station_code", "name", "value", "timestamp"]])
        except Exception as error:
            logging.getLogger("respira").warning("Hourly air file failed: %s", error)
    if not parts:
        raise RuntimeError("neither air network answered")
    return pd.concat(parts, ignore_index=True).rename(columns={"name": "pollutant"})


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_air_today_hourly():
    """
    Today's MEASURED hours of both networks, straight from the live files
    (cached 15 min, with backup). The planner's past and current parts of
    today use it together with backup/air_recent.csv, so they do not depend
    on when GitHub Actions last ran (it is often hours late).
    """
    return _drop_future(_with_backup("air_today_hourly", _fetch_air_today_hourly))


# ----------------------------------------------------------------------------
# 3. Pollen (Comunidad de Madrid, Red Palinocam)
# ----------------------------------------------------------------------------

def _fetch_pollen():
    readings = _read_csv(_ckan_csv_url("mediciones_polen"))
    readings["value"] = _to_number(readings["granos_de_polen_x_metro_cubico"])
    readings["timestamp"] = pd.to_datetime(readings["fecha_lectura"])
    readings = readings.dropna(subset=["value"])     # days not yet counted are empty

    # For each pollen trap, keep only the rows of its most recent reading day.
    last_day = readings.groupby("captador")["timestamp"].transform("max")
    latest = readings[readings["timestamp"] == last_day]

    # where each pollen trap ("captador") is  (this file is not UTF-8)
    traps = _read_csv(_ckan_csv_url("captadores_polen"), encoding="latin-1")
    traps = pd.DataFrame({
        "captador": traps["codigo"],
        "station": traps["nombre"],
        "lat": _to_number(traps["lat_epsg4258"]),
        "lon": _to_number(traps["long_epsg4258"]),
    })

    table = latest.merge(traps, on="captador", how="left")
    table = table.rename(columns={"captador": "station_code", "tipo_polinico": "pollen_type"})
    table = table[["station", "station_code", "lat", "lon", "pollen_type", "value", "timestamp"]]
    return table.reset_index(drop=True)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_pollen_all():
    """Latest pollen reading per trap and pollen type, however old it is."""
    return _with_backup("pollen", _fetch_pollen)


def load_pollen(today=None):
    """
    Latest pollen count (grains per m3) per pollen trap and pollen type,
    WITHOUT the traps whose latest reading is older than 10 days
    (config.POLLEN_MAX_AGE_DAYS). The reading date is in column `timestamp`,
    and `age_days` says how old it is.
    """
    table = load_pollen_all()
    attrs = dict(table.attrs)
    if today is None:
        today = _madrid_now().normalize()
    table = table.copy()
    table["age_days"] = (pd.Timestamp(today) - table["timestamp"]).dt.days
    table = table[table["age_days"] <= config.POLLEN_MAX_AGE_DAYS].reset_index(drop=True)
    table.attrs.update(attrs)
    return table


# ----------------------------------------------------------------------------
# 4. Weather (Comunidad air quality network, hourly)
# ----------------------------------------------------------------------------

def _fetch_weather():
    hourly = _hourly_long(_read_csv(_ckan_csv_url("calidad_aire_datos_meteo_mes")), WEATHER_VARIABLES)
    readings = _latest_hourly(hourly)

    stations = _read_csv(_ckan_csv_url("calidad_aire_estaciones"))
    stations = pd.DataFrame({
        "station_code": stations["estacion_codigo"],
        "station": stations["estacion_municipio"],
        "lat": _to_number(stations["estacion_coord_latitud"]),
        "lon": _to_number(stations["estacion_coord_longitud"]),
    })

    table = readings.merge(stations, on="station_code", how="left")
    table["source"] = "Comunidad de Madrid"
    table = table.rename(columns={"name": "variable"})
    return table[["station", "source", "lat", "lon", "variable", "value", "timestamp"]]


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_weather():
    """Latest temperature, humidity, wind, pressure, sun and rain per station."""
    return _drop_future(_with_backup("weather", _fetch_weather))


# ----------------------------------------------------------------------------
# 5. WEATHER FORECAST from Open-Meteo (a computer model; weather is the only thing we take from it)
# ----------------------------------------------------------------------------

@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_forecast_weather(lat, lon, day=None, days=2):
    """
    Hourly weather FORECAST for today and tomorrow (or `days` days) at any lat/lon.
    Everything (0.05 degree grid, 1-hour disk cache, retries, batches,
    backup copies) is in weather.py. Raises weather.WeatherUnavailable if
    there is no weather at all for this point.
    """
    import weather                                   # here, so data.py does not depend on it when unused
    return weather.hourly(lat, lon, day, days)


# ----------------------------------------------------------------------------
# Place names (to describe a point on the map: "Cerca de Tetuán")
# ----------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_gazetteer():
    """
    Madrid's 21 districts and the other municipalities of the region, with
    coordinates: columns name, kind, lat, lon, source.
    The file is made by build_gazetteer.py and stored in the project.
    """
    return pd.read_csv(Path(__file__).parent / "gazetteer.csv")


# ----------------------------------------------------------------------------
# 6. DEMO MODE: a past day stored in backup/replay/ (see config.REPLAY_DATE)
# ----------------------------------------------------------------------------

REPLAY_DIR = BACKUP_DIR / "replay" / config.REPLAY_DATE


@st.cache_data(show_spinner=False)
def load_replay_air(now):
    """
    The air table as it would have looked at time `now` on the demo day:
    same columns as load_air(). Only the 28 Comunidad stations.
    """
    hourly = pd.read_csv(REPLAY_DIR / "air.csv", parse_dates=["timestamp"])
    hourly = hourly[hourly["timestamp"] <= pd.Timestamp(now)].sort_values("timestamp")
    hourly = hourly.rename(columns={"pollutant": "name", "station": "station_code"})
    table = _latest_hourly(hourly, config.ICA_AVERAGING_HOURS)
    table = table.rename(columns={"name": "pollutant", "station_code": "station"})
    places = hourly[["station_code", "lat", "lon"]].drop_duplicates().rename(columns={"station_code": "station"})
    table = table.merge(places, on="station", how="left")
    table["source"] = "Comunidad de Madrid"
    table = table[AIR_COLUMNS]
    table = mark_doubtful(table)
    table.attrs["live"] = False
    table.attrs["fetched_at"] = pd.Timestamp(now)
    return table


@st.cache_data(show_spinner=False)
def load_replay_pollen():
    """The pollen readings of the demo day, same columns as load_pollen()."""
    table = pd.read_csv(REPLAY_DIR / "pollen.csv", parse_dates=["timestamp"])
    table["age_days"] = 0
    table.attrs["live"] = False
    table.attrs["fetched_at"] = pd.Timestamp(config.REPLAY_DATE)
    return table
