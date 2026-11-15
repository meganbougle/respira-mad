"""
build_replay.py - one-off script that prepares the "replay a past day" data.

Run it with:   .venv/bin/python build_replay.py      (takes a few minutes)

What it does:
  1. Downloads the whole pollen history of the Comunidad de Madrid (1993-now).
  2. Finds the 3 days with the highest grass pollen (Gramíneas) and the 3 days
     with the highest plane tree pollen (Plátano de paseo). "Highest" = highest
     average over all the pollen stations that reported that day.
  3. For the day used by the app's demo mode (config.REPLAY_DATE, the top
     grass day) saves, in backup/replay/<date>/ :
       pollen.csv   every station and pollen type that day
       air.csv      hourly NO2, O3, PM10, PM2.5 at the Comunidad stations
       weather.csv  hourly weather at the Comunidad stations (2020 onwards only)
     plus backup/replay/replay_days.csv, the list of days and why they were chosen.

Not included: Ayuntamiento de Madrid air data for those days (its history is
published separately, as one big zip file per year).
"""

import io
import logging
import zipfile

import pandas as pd
import requests
import streamlit  # noqa: F401

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import data  # noqa: E402

REPLAY_DIR = data.BACKUP_DIR / "replay"
TOP_DAYS = 3
MIN_STATIONS = 5          # ignore days when fewer stations than this reported
TARGETS = {"grass": "Gramíneas", "plane tree": "Plátano de paseo"}

# In some of the older files the accented letters are damaged ("Gram?neas").
BROKEN_NAMES = {
    "Gram?neas": "Gramíneas",
    "Pl?tano de paseo": "Plátano de paseo",
    "Casta?o": "Castaño",
    "Cupres?ceas/Tax?ceas": "Cupresáceas/Taxáceas",
    "Quenopodi?ceas/Amarant?ceas": "Quenopodiáceas/Amarantáceas",
}


def ckan_resources(dataset_id):
    """List of files (name, format, url) in a Comunidad dataset."""
    response = requests.get(data.CKAN_API + "package_show", params={"id": dataset_id}, timeout=data.TIMEOUT)
    response.raise_for_status()
    return response.json()["result"]["resources"]


def csv_from_bytes(raw):
    """Read a semicolon CSV whose encoding we are not sure about."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    table = pd.read_csv(io.StringIO(text), sep=";", dtype=str)
    table.columns = table.columns.str.strip().str.lower()
    return table


def download(url):
    response = requests.get(url, timeout=300, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    return response.content


# ---- 1. the whole pollen history ------------------------------------------
print("Downloading the pollen history...")
tables = []
for resource in ckan_resources("mediciones_polen_historico"):
    if resource["format"].upper() == "CSV":
        tables.append(csv_from_bytes(download(resource["url"])))
    elif resource["format"].upper() == "ZIP":                 # 1993-2009, one CSV per year
        with zipfile.ZipFile(io.BytesIO(download(resource["url"]))) as archive:
            for name in archive.namelist():
                tables.append(csv_from_bytes(archive.read(name)))
# the current year's file has the most recent months
tables.append(csv_from_bytes(download(data._ckan_csv_url("mediciones_polen"))))

history = pd.concat(tables, ignore_index=True)
history["pollen_type"] = history["tipo_polinico"].replace(BROKEN_NAMES)
history["timestamp"] = pd.to_datetime(history["fecha_lectura"].str[:10].str.replace("/", "-"), errors="coerce")
history["value"] = data._to_number(history["granos_de_polen_x_metro_cubico"])
history = history.dropna(subset=["value", "timestamp"])
history = history.drop_duplicates(subset=["captador", "timestamp", "pollen_type"])
print(f"  {len(history):,} readings from {history['timestamp'].min():%Y-%m-%d} "
      f"to {history['timestamp'].max():%Y-%m-%d}, {history['captador'].nunique()} stations")

# ---- 2. the highest days ---------------------------------------------------
chosen = []
for label, pollen_type in TARGETS.items():
    daily = history[history["pollen_type"] == pollen_type].groupby("timestamp")["value"].agg(["mean", "max", "count"])
    daily = daily[daily["count"] >= MIN_STATIONS].sort_values("mean", ascending=False).head(TOP_DAYS)
    for day, row in daily.iterrows():
        chosen.append({"date": day, "chosen_for": label, "pollen_type": pollen_type,
                       "network_mean": round(row["mean"]), "highest_station": round(row["max"]),
                       "stations": int(row["count"])})
        print(f"  top {label} day: {day:%Y-%m-%d}  network mean {row['mean']:.0f}, "
              f"highest station {row['max']:.0f} grains/m3 ({int(row['count'])} stations)")

# ---- 3. save the demo day --------------------------------------------------
# The ranking above is printed for information; only the day chosen for the
# demo mode (config.REPLAY_DATE) is saved.
chosen = [item for item in chosen if f"{item['date']:%Y-%m-%d}" == data.config.REPLAY_DATE]

traps = data._read_csv(data._ckan_csv_url("captadores_polen"), encoding="latin-1")
traps = pd.DataFrame({"captador": traps["codigo"], "station": traps["nombre"],
                      "lat": data._to_number(traps["lat_epsg4258"]),
                      "lon": data._to_number(traps["long_epsg4258"])})
stations = data._read_csv(data._ckan_csv_url("calidad_aire_estaciones"))
stations = pd.DataFrame({"station_code": stations["estacion_codigo"], "station": stations["estacion_municipio"],
                         "lat": data._to_number(stations["estacion_coord_latitud"]),
                         "lon": data._to_number(stations["estacion_coord_longitud"])})

yearly_files = {}       # so each big yearly file is only downloaded once


def hourly_for_day(dataset_id, day, codes):
    """Hour-by-hour station values for one past day (None if that year is not published)."""
    key = (dataset_id, day.year)
    if key not in yearly_files:
        urls = {r["name"]: r["url"] for r in ckan_resources(dataset_id) if r["format"].upper() == "CSV"}
        if str(day.year) not in urls:
            yearly_files[key] = None
        else:
            print(f"  downloading {dataset_id} {day.year}...")
            yearly_files[key] = csv_from_bytes(download(urls[str(day.year)]))
    year_table = yearly_files[key]
    if year_table is None:
        return None
    same_day = year_table[(year_table["mes"].astype(int) == day.month) & (year_table["dia"].astype(int) == day.day)]
    long = data._hourly_long(same_day, codes).merge(stations, on="station_code", how="left")
    long["source"] = "Comunidad de Madrid"
    return long


for item in chosen:
    day = item["date"]
    folder = REPLAY_DIR / f"{day:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)

    pollen = history[history["timestamp"] == day].merge(traps, on="captador", how="left")
    pollen = pollen.rename(columns={"captador": "station_code"})
    pollen[["station", "station_code", "lat", "lon", "pollen_type", "value", "timestamp"]].to_csv(
        folder / "pollen.csv", index=False)

    air = hourly_for_day("calidad_aire_datos_historico", day, data.POLLUTANTS)
    air = air.rename(columns={"name": "pollutant"})
    air[["station", "source", "lat", "lon", "pollutant", "value", "timestamp", "flag"]].to_csv(
        folder / "air.csv", index=False)
    item["air_rows"] = len(air)

    weather = hourly_for_day("calidad_aire_datos_meteo_historico", day, data.WEATHER_VARIABLES)
    if weather is None or weather.empty:
        item["weather_rows"] = 0
    else:
        weather = weather.rename(columns={"name": "variable"})
        weather[["station", "source", "lat", "lon", "variable", "value", "timestamp"]].to_csv(
            folder / "weather.csv", index=False)
        item["weather_rows"] = len(weather)
    item["pollen_rows"] = len(pollen)
    print(f"  saved {folder.name}: pollen {item['pollen_rows']} rows, air {item['air_rows']}, weather {item['weather_rows']}")

pd.DataFrame(chosen).to_csv(REPLAY_DIR / "replay_days.csv", index=False)
print(f"\nDone. Replay data is in {REPLAY_DIR}")
