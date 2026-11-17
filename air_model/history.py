"""
air_model/history.py - downloads the official HOURLY HISTORY of air quality.

Only two sources, both official:
  - Comunidad de Madrid, "Red de Calidad del Aire. Datos horarios desde 2005"
    (CKAN dataset calidad_aire_datos_historico, one CSV per year)
  - Ayuntamiento de Madrid, "Calidad del aire. Datos horarios desde 2001"
    (dataset 201200: one zip of monthly CSVs per year, plus one CSV with the
    last two years, updated monthly)

    load(first_year)  ->  {pollutant: table}  with one row per hour and one
                          column per station code (ug/m3; NaN = no valid reading)
    stations()        ->  station_code, station, source, lat, lon

Used by train_air_model.py. Downloads are kept in model_data/ (not committed).
"""

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import data

CACHE = Path(__file__).parent.parent / "model_data"
RAW = CACHE / "air_raw"
CITY_PAGE = "https://datos.madrid.es/dataset/201200-0-calidad-aire-horario/downloads"
CITY_BASE = "https://datos.madrid.es"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _download(url, path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(requests.get(url, headers=HEADERS, timeout=600).content)
    return path


def _long(table):
    """One wide table (a row per station + pollutant + day, columns h01..h24, v01..v24) -> one row per hour."""
    table.columns = table.columns.str.strip().str.lower()
    table["pollutant"] = pd.to_numeric(table["magnitud"], errors="coerce").map(data.POLLUTANTS)
    table = table.dropna(subset=["pollutant"]).reset_index(drop=True)
    code = table["provincia"].str.zfill(2) + table["municipio"].str.zfill(3) + table["estacion"].str.zfill(3)
    day = pd.to_datetime(table["ano"] + "-" + table["mes"].str.zfill(2) + "-" + table["dia"].str.zfill(2), errors="coerce")
    parts = []
    for hour in range(1, 25):
        value = pd.to_numeric(table[f"h{hour:02d}"].str.replace(",", ".", regex=False), errors="coerce")
        good = table[f"v{hour:02d}"].isin(data.GOOD_FLAGS) & value.notna() & day.notna()
        # h01 is the hour ending at 01:00 ... h24 ends at 00:00 of the next day
        parts.append(pd.DataFrame({"station_code": code[good], "pollutant": table["pollutant"][good],
                                   "timestamp": day[good] + pd.Timedelta(hours=hour), "value": value[good]}))
    return pd.concat(parts, ignore_index=True)


def _read(raw):
    return pd.read_csv(io.StringIO(raw.decode("utf-8-sig", errors="replace")), sep=";", dtype=str)


def load(first_year=2019):
    """Every valid hourly reading of NO2, O3, PM10 and PM2.5 since `first_year`, both networks."""
    cache = CACHE / f"air_hourly_{first_year}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    tables = []

    print("  Comunidad de Madrid: hourly history, one file per year...")
    listing = requests.get(data.CKAN_API + "package_show", params={"id": "calidad_aire_datos_historico"}, timeout=60).json()
    for resource in listing["result"]["resources"]:
        if resource["format"].upper() == "CSV" and resource["name"].isdigit() and int(resource["name"]) >= first_year:
            tables.append(_long(_read(_download(resource["url"], RAW / f"comunidad_{resource['name']}.csv").read_bytes())))

    print("  Ayuntamiento de Madrid: hourly history, one zip per year + the current file...")
    page = requests.get(CITY_PAGE, headers=HEADERS, timeout=90).text
    links = sorted(set(re.findall(r'/dataset/201200-0-calidad-aire-horario/resource/[^"\'<> ]+/download/[^"\'<> ]+', page)))
    for link in links:
        name = link.split("/")[-1]
        if name.endswith(".zip"):
            with zipfile.ZipFile(_download(CITY_BASE + link, RAW / f"city_{name}")) as archive:
                # the monthly files are named like "ene_mo23.csv": keep the years we want
                months = [n for n in archive.namelist() if n.lower().endswith(".csv") and int("20" + re.search(r"(\d\d)\.csv$", n.lower()).group(1)) >= first_year]
                tables += [_long(_read(archive.read(n))) for n in months]
        elif name.endswith(".csv"):
            tables.append(_long(_read(_download(CITY_BASE + link, RAW / f"city_{name}").read_bytes())))

    readings = pd.concat(tables, ignore_index=True).drop_duplicates(["station_code", "pollutant", "timestamp"])
    readings = readings[readings["value"] >= 0]
    hours = pd.date_range(f"{first_year}-01-01 01:00", readings["timestamp"].max(), freq="h")
    wide = {pollutant: group.pivot(index="timestamp", columns="station_code", values="value").reindex(hours).astype("float32")
            for pollutant, group in readings.groupby("pollutant")}
    pd.to_pickle(wide, cache)
    return wide


def stations():
    """Where every air station is (both networks): station_code, station, source, lat, lon."""
    cache = CACHE / "air_stations.csv"
    if cache.exists():
        return pd.read_csv(cache, dtype={"station_code": str})
    comunidad = data._read_csv(data._ckan_csv_url("calidad_aire_estaciones"))
    city = data._read_csv(data.CITY_STATIONS_URL)
    table = pd.concat([
        pd.DataFrame({"station_code": comunidad["estacion_codigo"], "station": comunidad["estacion_municipio"],
                      "source": "Comunidad de Madrid", "lat": data._to_number(comunidad["estacion_coord_latitud"]),
                      "lon": data._to_number(comunidad["estacion_coord_longitud"])}),
        pd.DataFrame({"station_code": city["codigo"], "station": city["estacion"].str.strip(),
                      "source": "Ayuntamiento de Madrid", "lat": data._to_number(city["latitud"]),
                      "lon": data._to_number(city["longitud"])}),
    ], ignore_index=True).dropna(subset=["lat", "lon"])
    table.to_csv(cache, index=False)
    return table
