"""
data_audit.py - reads the RAW official files exactly as published and checks them.

Used by notebooks/exploracion_datos.ipynb and to write DATA_AUDIT.md.
It changes nothing in the app: it only reads files and returns tables.

Sources (the same files the models were trained on, kept in model_data/, not committed):
  - Comunidad de Madrid air: model_data/air_raw/comunidad_<year>.csv
    (CKAN calidad_aire_datos_historico, one CSV per year)
  - Ayuntamiento de Madrid air: model_data/air_raw/city_*.zip (one zip of monthly
    CSVs per year) + city_*-csv.csv (the "current" file, last years)
  - Pollen (Red Palinocam): model_data/pollen_raw/hist_<year>.csv + current.csv
    (CKAN mediciones_polen_historico + mediciones_polen); download with
    download_pollen_raw() if missing
  - Weather (Open-Meteo, the only non-official source allowed): the files the
    models downloaded, model_data/weather_<station>.csv and air_weather_zone<k>.csv

Every loader keeps blanks, flags and the file each row came from, so the
checks can say exactly what is wrong and where.
"""

import glob
import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
RAW_AIR = ROOT / "model_data" / "air_raw"
RAW_POLLEN = ROOT / "model_data" / "pollen_raw"
CACHE = ROOT / "model_data" / "audit"

POLLUTANTS = {8: "NO2", 9: "PM2.5", 10: "PM10", 14: "O3"}   # same codes in both networks

# The 10 pollen types the app uses (official thresholds), as written in the files
POLLEN_TYPES = ["Plátano de paseo", "Cupresáceas/Taxáceas", "Quercus", "Olivo", "Gramíneas",
                "Pinos", "Populus", "Olmos", "Plantago", "Urticaceae (Ortigas)"]
# Names broken in the 2021 and 2022 files (a literal "?" instead of the accented letter)
BROKEN_NAMES = {"Gram?neas": "Gramíneas", "Pl?tano de paseo": "Plátano de paseo",
                "Cupres?ceas/Tax?ceas": "Cupresáceas/Taxáceas", "Casta?o": "Castaño",
                "Quenopodi?ceas/Amarant?ceas": "Quenopodiáceas/Amarantáceas"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _decode(raw):
    """Text of a file: UTF-8 if it is valid UTF-8, otherwise Latin-1. Returns (text, encoding)."""
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _table(raw):
    text, encoding = _decode(raw)
    table = pd.read_csv(io.StringIO(text), sep=";", dtype=str)
    table.columns = table.columns.str.strip().str.lower()
    return table, encoding


def _number(text):
    """'12,5' -> 12.5 (decimal commas); blanks stay NaN."""
    return pd.to_numeric(text.str.replace(",", ".", regex=False), errors="coerce")


# ---------------------------------------------------------------------------
# Air: both networks, every hour WITH its flag (V valid, T provisional, N not valid)
# ---------------------------------------------------------------------------

def _air_long(table, source, file_name):
    """One wide file (row = station + pollutant + day, h01..h24 / v01..v24) -> one row per hour."""
    table = table[pd.to_numeric(table["magnitud"], errors="coerce").isin(POLLUTANTS)].reset_index(drop=True)
    code = table["provincia"].str.zfill(2) + table["municipio"].str.zfill(3) + table["estacion"].str.zfill(3)
    day = pd.to_datetime(table["ano"] + "-" + table["mes"].str.zfill(2) + "-" + table["dia"].str.zfill(2), errors="coerce")
    pollutant = pd.to_numeric(table["magnitud"]).map(POLLUTANTS)
    parts = []
    for hour in range(1, 25):
        parts.append(pd.DataFrame({
            "station_code": code, "pollutant": pollutant,
            "timestamp": day + pd.Timedelta(hours=hour),     # h01 = hour ending 01:00; h24 = 00:00 next day
            "hour_column": hour,
            "raw": table[f"h{hour:02d}"], "flag": table[f"v{hour:02d}"],
        }))
    out = pd.concat(parts, ignore_index=True)
    out["value"] = _number(out["raw"]).astype("float32")
    out["source"] = source
    out["file"] = file_name
    return out


def load_air(first_year=2019):
    """
    Every hour of NO2, O3, PM10 and PM2.5 since `first_year`, both networks,
    blanks and flags included. Columns: station_code, pollutant, timestamp,
    hour_column, raw, flag, value, source, file, file_order.
    `file_order` is the order the training code reads the files (it keeps the
    FIRST copy of a duplicated hour).
    """
    cache = CACHE / f"air_raw_{first_year}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    tables = []
    for path in sorted(RAW_AIR.glob("comunidad_*.csv")):
        if int(path.stem.split("_")[1]) >= first_year:
            table, _ = _table(path.read_bytes())
            tables.append(_air_long(table, "Comunidad de Madrid", path.name))
    # Ayuntamiento: same order as air_model/history.py (links sorted by name)
    for path in sorted(RAW_AIR.glob("city_*")):
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    match = re.search(r"(\d\d)\.csv$", name.lower())
                    if match and int("20" + match.group(1)) >= first_year:
                        table, _ = _table(archive.read(name))
                        tables.append(_air_long(table, "Ayuntamiento de Madrid", f"{path.name}:{name}"))
        elif path.suffix == ".csv":
            table, _ = _table(path.read_bytes())
            tables.append(_air_long(table, "Ayuntamiento de Madrid", path.name))
    for i, table in enumerate(tables):
        table["file_order"] = i
    air = pd.concat(tables, ignore_index=True)
    air = air[air["timestamp"].dt.year >= first_year]
    for column in ("station_code", "pollutant", "flag", "source", "file"):
        air[column] = air[column].astype("category")
    CACHE.mkdir(parents=True, exist_ok=True)
    air.drop(columns="raw").to_parquet(cache)
    return air.drop(columns="raw")


def air_stations():
    """Station list used by the models (code, name, network, lat, lon): model_data/air_stations.csv."""
    return pd.read_csv(ROOT / "model_data" / "air_stations.csv", dtype={"station_code": str})


# ---------------------------------------------------------------------------
# Pollen: every published row, blanks included
# ---------------------------------------------------------------------------

def download_pollen_raw():
    """Downloads the raw pollen files into model_data/pollen_raw/ (about 140 MB). Only if missing."""
    import requests
    import data                                   # the app's CKAN helpers
    RAW_POLLEN.mkdir(parents=True, exist_ok=True)
    listing = requests.get(data.CKAN_API + "package_show", params={"id": "mediciones_polen_historico"},
                           timeout=60).json()["result"]["resources"]
    for resource in listing:
        kind = resource["format"].upper()
        if kind not in ("CSV", "ZIP"):
            continue
        content = requests.get(resource["url"], timeout=600).content
        if kind == "ZIP":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for name in archive.namelist():
                    (RAW_POLLEN / f"hist_{Path(name).name}").write_bytes(archive.read(name))
        else:
            (RAW_POLLEN / f"hist_{resource['name']}.csv").write_bytes(content)
    (RAW_POLLEN / "current.csv").write_bytes(requests.get(data._ckan_csv_url("mediciones_polen"), timeout=300).content)
    (RAW_POLLEN / "traps.csv").write_bytes(requests.get(data._ckan_csv_url("captadores_polen"), timeout=300).content)


def load_pollen():
    """
    Every published pollen row (all types, 1993 to now). Columns: station_code,
    date, type_raw (as written), type (broken names repaired), raw, value,
    file, encoding, date_format.
    """
    cache = CACHE / "pollen_raw.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    if not (RAW_POLLEN / "current.csv").exists():
        download_pollen_raw()
    tables = []
    for path in sorted(RAW_POLLEN.glob("hist_*.csv")) + [RAW_POLLEN / "current.csv"]:
        table, encoding = _table(path.read_bytes())
        table["file"], table["encoding"] = path.name, encoding
        tables.append(table)
    p = pd.concat(tables, ignore_index=True)
    out = pd.DataFrame({
        "station_code": p["captador"],
        "date": pd.to_datetime(p["fecha_lectura"].str[:10].str.replace("/", "-"), errors="coerce"),
        "type_raw": p["tipo_polinico"],
        "type": p["tipo_polinico"].replace(BROKEN_NAMES),
        "raw": p["granos_de_polen_x_metro_cubico"],
        "value": _number(p["granos_de_polen_x_metro_cubico"]),
        "file": p["file"], "encoding": p["encoding"],
        "date_format": np.where(p["fecha_lectura"].str[4] == "/", "AAAA/MM/DD", "AAAA-MM-DD"),
    })
    for column in ("station_code", "type_raw", "type", "file", "encoding", "date_format"):
        out[column] = out[column].astype("category")
    CACHE.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache)
    return out


def pollen_traps():
    """The official trap list (code, name, start date, coordinates)."""
    table, _ = _table((RAW_POLLEN / "traps.csv").read_bytes())
    return pd.DataFrame({
        "station_code": table["codigo"], "name": table["nombre"],
        "start": pd.to_datetime(table["fecha_inicio_mediciones"].str[:10].str.replace("/", "-"), errors="coerce"),
        "lat": _number(table["lat_epsg4258"]), "lon": _number(table["long_epsg4258"]),
    })


# ---------------------------------------------------------------------------
# Weather (Open-Meteo, as downloaded by the training scripts)
# ---------------------------------------------------------------------------

def load_weather():
    """
    Daily weather per pollen station (weather_<code>.csv) and hourly weather per
    air zone (air_weather_zone<k>.csv). Returns (daily, hourly), each with a
    `place` column.
    """
    daily, hourly = [], []
    for path in sorted(glob.glob(str(ROOT / "model_data" / "weather_*.csv"))):
        t = pd.read_csv(path)
        t["place"] = Path(path).stem.replace("weather_", "")
        daily.append(t)
    for path in sorted(glob.glob(str(ROOT / "model_data" / "air_weather_zone*.csv"))):
        t = pd.read_csv(path)
        t["place"] = Path(path).stem.replace("air_weather_", "")
        hourly.append(t)
    return pd.concat(daily, ignore_index=True), pd.concat(hourly, ignore_index=True)


# ---------------------------------------------------------------------------
# Checks used by the report (each returns a small table)
# ---------------------------------------------------------------------------

def pollen_day_kinds(pollen):
    """Per trap and day: how many types were published, blank, zero; and the kind of day."""
    d = pollen.groupby(["station_code", "date"], observed=True)["value"].agg(
        published="size", numbers="count", total="sum", zeros=lambda s: int((s == 0).sum()))
    d["blank"] = d["published"] - d["numbers"]
    d["kind"] = np.select([d["numbers"] == 0, d["blank"] == 0], ["todo en blanco", "completo"], "parcial")
    return d.reset_index()


def pollen_false_zeros(pollen, network_min=100):
    """
    Trap-days where EVERY published type is exactly 0 while the median of the
    other traps that day is above `network_min` grains/m3 (sum of all types):
    almost certainly "no sample" written as 0.
    """
    d = pollen_day_kinds(pollen)
    network = d[d["numbers"] > 0].groupby("date")["total"].median()
    allzero = d[(d["numbers"] > 0) & (d["total"] == 0)].copy()
    allzero["network_median"] = allzero["date"].map(network)
    return allzero[allzero["network_median"] > network_min]


def pollen_coverage(pollen):
    """Share of days (%) with at least one number, per trap and year."""
    d = pollen[pollen["value"].notna()].groupby(["station_code", "date"], observed=True).size().reset_index()
    d["year"] = d["date"].dt.year
    days_in_year = d["year"].map(lambda y: 366 if pd.Timestamp(f"{y}-12-31").dayofyear == 366 else 365)
    d["share"] = 1 / days_in_year
    return (d.groupby(["station_code", "year"], observed=True)["share"].sum() * 100).round().unstack(fill_value=0)


def air_coverage(air):
    """Share of hours (%) with a valid value (flag V or T), per station, pollutant and year."""
    valid = air[air["flag"].isin(["V", "T"]) & air["value"].notna()]
    valid = valid.drop_duplicates(["station_code", "pollutant", "timestamp"])
    valid = valid.assign(year=valid["timestamp"].dt.year)
    n = valid.groupby(["station_code", "pollutant", "year"], observed=True).size()
    hours = n.index.get_level_values("year").map(lambda y: 8784 if pd.Timestamp(f"{y}-12-31").dayofyear == 366 else 8760)
    return (n / hours * 100).round(1)


def air_gaps(air, min_hours=24 * 7):
    """Runs of at least `min_hours` consecutive hours without a valid value, per station and pollutant."""
    valid = air[air["flag"].isin(["V", "T"]) & air["value"].notna()]
    valid = valid.drop_duplicates(["station_code", "pollutant", "timestamp"]).sort_values("timestamp")
    out = []
    for (code, pollutant), group in valid.groupby(["station_code", "pollutant"], observed=True):
        step = group["timestamp"].diff()
        big = step > pd.Timedelta(hours=min_hours)
        for end, length in zip(group["timestamp"][big], step[big]):
            out.append({"station_code": code, "pollutant": pollutant, "from": end - length, "to": end,
                        "days": round(length / pd.Timedelta(days=1), 1)})
    return pd.DataFrame(out)


def air_duplicates(air):
    """Hours that appear more than once (same station, pollutant, time) and whether the copies agree."""
    key = ["station_code", "pollutant", "timestamp"]
    dup = air[air.duplicated(key, keep=False)]
    if dup.empty:
        return dup, pd.DataFrame()
    summary = dup.groupby(key, observed=True).agg(copies=("value", "size"), lo=("value", "min"), hi=("value", "max"),
                                                  flags=("flag", lambda s: "".join(sorted(map(str, s)))))
    summary["agree"] = (summary["hi"] - summary["lo"]).fillna(0) < 0.05
    return dup, summary.reset_index()


def air_doubtful(air, factor=4, min_value=None):
    """
    The app's "dato dudoso" rule applied to the history: a value in the ICA's
    Desfavorable band or worse while the median of all OTHER stations that hour
    is in the two best bands, and more than `factor` times that median.
    Bands from config.py (BOE-A-2020-10426).
    """
    import config
    valid = air[air["flag"].isin(["V", "T"]) & air["value"].notna()]
    valid = valid.drop_duplicates(["station_code", "pollutant", "timestamp"])
    valid = valid[["station_code", "pollutant", "timestamp", "value", "flag"]].astype({"station_code": str, "pollutant": str})
    out = []
    for pollutant, group in valid.groupby("pollutant"):
        bands = config.ICA_BANDS[pollutant]                    # upper limits of each category
        bad_from = bands[2]                                    # above "Regular" = Desfavorable or worse
        good_to = bands[1]                                     # up to "Razonablemente buena"
        candidates = group[group["value"] > bad_from]
        if candidates.empty:
            continue
        # the other stations at the same hour (the station itself left out)
        pairs = candidates.merge(group[["timestamp", "station_code", "value"]], on="timestamp", suffixes=("", "_other"))
        pairs = pairs[pairs["station_code"] != pairs["station_code_other"]]
        others = pairs.groupby(["station_code", "timestamp"])["value_other"].agg(["median", "count"]).reset_index()
        result = candidates.merge(others, on=["station_code", "timestamp"])
        result = result[(result["count"] >= 3) & (result["median"] <= good_to)
                        & (result["value"] > factor * result["median"].clip(lower=1))]
        out.append(result.rename(columns={"median": "median_others", "count": "other_stations"}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def rush_hour_peak(air, station_codes=None):
    """
    Daylight saving check: the hour of the morning NO2 peak (people follow the
    clock) in the week before and after each change. If the files were in fixed
    winter time, the peak would move by one hour in the file's clock.
    """
    no2 = air[(air["pollutant"] == "NO2") & air["flag"].isin(["V", "T"]) & air["value"].notna()]
    no2 = no2.drop_duplicates(["station_code", "pollutant", "timestamp"])
    if station_codes is not None:
        no2 = no2[no2["station_code"].isin(station_codes)]
    no2 = no2.assign(day=no2["timestamp"].dt.normalize() - pd.to_timedelta((no2["timestamp"].dt.hour == 0).astype(int), unit="D"),
                     hour=no2["timestamp"].dt.hour.replace(0, 24))
    rows = []
    for year in sorted(no2["timestamp"].dt.year.unique()):
        for name, change in (("marzo", _last_sunday(year, 3)), ("octubre", _last_sunday(year, 10))):
            for side, start, end in (("antes", change - pd.Timedelta(days=14), change - pd.Timedelta(days=1)),
                                     ("después", change + pd.Timedelta(days=1), change + pd.Timedelta(days=14))):
                week = no2[(no2["day"] >= start) & (no2["day"] <= end) & (no2["day"].dt.dayofweek < 5)]   # working days
                morning = week[(week["hour"] >= 4) & (week["hour"] <= 12)]
                if morning.empty:
                    continue
                profile = morning.groupby("hour")["value"].mean()
                rows.append({"year": year, "change": name, "side": side, "peak_hour": int(profile.idxmax()),
                             "profile": profile.round(1).to_dict()})
    return pd.DataFrame(rows)


def _last_sunday(year, month):
    last = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    return last - pd.Timedelta(days=(last.dayofweek + 1) % 7)
