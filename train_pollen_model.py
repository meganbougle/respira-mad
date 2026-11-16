"""
train_pollen_model.py - builds, tests and saves our own pollen forecast model.

Run it with:   .venv/bin/python train_pollen_model.py      (10-20 minutes the first time)

What it does:
  1. Downloads the full pollen history (1993 to now) and, for each pollen
     station, the daily weather since 1993 (Open-Meteo historical weather).
  2. Builds the training table: one row per station, day and pollen type,
     for the 10 pollen types that have official thresholds (config.py).
  3. Trains one small gradient-boosting model per pollen type and per lead
     day (0 = today ... 5 = five days ahead) on data up to 2024.
  4. Tests it on 2025, using the weather forecasts exactly as they were issued
     at the time, against a simple baseline ("the last available reading,
     adjusted for the season"). A type + lead day is marked RELIABLE only if
     the model clearly beats the baseline.
  5. Saves the models, the backtest report and a chart in pollen_model/, and
     the forecast "as of 24 May 2025" for the app's demo mode.

Downloads are kept in model_data/ (not committed to git) so a second run is fast.
Needs matplotlib for the chart:  .venv/bin/pip install matplotlib
"""

import io
import json
import logging
import time
import zipfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
import sklearn
import streamlit  # noqa: F401
from sklearn.ensemble import HistGradientBoostingClassifier

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import config  # noqa: E402
import data    # noqa: E402
from pollen_model import features as F  # noqa: E402

ROOT = Path(__file__).parent
CACHE = ROOT / "model_data"
OUT = ROOT / "pollen_model"
CACHE.mkdir(exist_ok=True)

TRAIN_UNTIL = pd.Timestamp("2024-12-31")          # train on data up to 2024 ...
TEST_YEAR = 2025                                  # ... and test on 2025
CHECK_DAY = pd.Timestamp("2025-05-24")            # "what did it say on 24 May 2025?"
REPORT_LEADS = [1, 2, 3, 4, 5]

# ---- OUR RULE for calling a type + lead day RELIABLE (also explained in the report)
# Only days inside that pollen type's season count (outside it everything is
# "bajo" and any method looks perfect). What is scored is the level THE APP
# ACTS ON: bajo / medio / alto-or-more (alto and muy alto give the same advice,
# so mixing them up does not change what the app says). The model must:
#   - get at least 5 percentage points more days right than the baseline, and
#   - be closer to the real level on average (smaller mean error in levels), and
#   - have at least 200 station-days in season to judge from.
# The exact four-level score is also in the report.
MIN_GAIN = 0.05
MIN_SEASON_DAYS = 200
SEASON_SHARE = 0.10        # a day of the year is "in season" if 10% of past readings were medio or higher

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
DAILY = {"temperature_2m_mean": "temp_mean", "temperature_2m_max": "temp_max", "precipitation_sum": "rain",
         "wind_speed_10m_max": "wind", "relative_humidity_2m_mean": "humidity"}

BROKEN_NAMES = {"Gram?neas": "Gramíneas", "Pl?tano de paseo": "Plátano de paseo", "Cupres?ceas/Tax?ceas": "Cupresáceas/Taxáceas"}


def get_json(url, params, tries=8):
    """
    Download JSON. Open-Meteo's free service limits how much one computer may
    download per hour; when it refuses or stops answering, wait and try again.
    """
    for attempt in range(tries):
        try:
            response = requests.get(url, params=params, timeout=120)
            if response.status_code == 200:
                return response.json()
            problem = f"answer {response.status_code}: {response.text[:120]}"
        except requests.RequestException as error:
            problem = str(error)[:120]
        wait = 60 * (attempt + 1)
        print(f"    the weather service did not answer ({problem}); waiting {wait} s...", flush=True)
        time.sleep(wait)
    raise RuntimeError("Open-Meteo kept refusing. Its hourly limit was probably reached: run this script again in an hour "
                       "(what was already downloaded is kept in model_data/).")


# ============================================================================
# 1. DATA
# ============================================================================

def load_pollen_history():
    """Every reading of the 10 pollen types, 1993 to now: columns station_code, date, pollen_type, value."""
    cache = CACHE / "pollen_history.csv.gz"
    if cache.exists():
        return pd.read_csv(cache, parse_dates=["date"])
    print("Downloading the pollen history (about 80 MB)...")

    def read(raw):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        table = pd.read_csv(io.StringIO(text), sep=";", dtype=str)
        table.columns = table.columns.str.strip().str.lower()
        return table

    tables = []
    listing = requests.get(data.CKAN_API + "package_show", params={"id": "mediciones_polen_historico"}, timeout=60).json()
    for resource in listing["result"]["resources"]:
        if resource["format"].upper() == "CSV":
            tables.append(read(requests.get(resource["url"], timeout=300).content))
        elif resource["format"].upper() == "ZIP":
            with zipfile.ZipFile(io.BytesIO(requests.get(resource["url"], timeout=300).content)) as archive:
                tables += [read(archive.read(name)) for name in archive.namelist()]
    tables.append(read(requests.get(data._ckan_csv_url("mediciones_polen"), timeout=300).content))   # current year

    history = pd.concat(tables, ignore_index=True)
    history["pollen_type"] = history["tipo_polinico"].replace(BROKEN_NAMES)
    history = history[history["pollen_type"].isin(F.POLLEN_TYPES)]
    history["date"] = pd.to_datetime(history["fecha_lectura"].str[:10].str.replace("/", "-"), errors="coerce")
    history["value"] = data._to_number(history["granos_de_polen_x_metro_cubico"])
    history = history.dropna(subset=["value", "date"]).rename(columns={"captador": "station_code"})
    history = history.drop_duplicates(subset=["station_code", "date", "pollen_type"])
    history = history[["station_code", "date", "pollen_type", "value"]].sort_values(["station_code", "date"])
    history.to_csv(cache, index=False)
    return history


def load_stations():
    traps = data._read_csv(data._ckan_csv_url("captadores_polen"), encoding="latin-1")
    return pd.DataFrame({"station_code": traps["codigo"], "station": traps["nombre"],
                         "lat": data._to_number(traps["lat_epsg4258"]), "lon": data._to_number(traps["long_epsg4258"])})


def load_weather(station):
    """Daily weather since 1993 at one pollen station (Open-Meteo historical weather, ERA5)."""
    cache = CACHE / f"weather_{station['station_code']}.csv"
    if cache.exists():
        return pd.read_csv(cache, parse_dates=["date"])
    print(f"  downloading weather history for {station['station']}...")
    end = (pd.Timestamp.now().normalize() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    result = get_json(ARCHIVE_URL, {"latitude": station["lat"], "longitude": station["lon"], "start_date": "1993-01-01",
                                    "end_date": end, "daily": ",".join(DAILY), "timezone": "Europe/Madrid"})
    table = pd.DataFrame(result["daily"]).rename(columns={"time": "date", **DAILY})
    table["date"] = pd.to_datetime(table["date"])
    table.to_csv(cache, index=False)
    return table


def load_issued_forecasts(station, year):
    """
    The weather forecasts as they were ISSUED: for each day of `year`, what the
    forecast made k days earlier said (k = 1..5). From Open-Meteo's archive of
    previous model runs. Returns {k: daily table indexed by date}.
    """
    cache = CACHE / f"issued_{station['station_code']}_{year}.csv"
    if not cache.exists():
        print(f"  downloading the forecasts issued in {year} for {station['station']}...")
        variables = [f"{name}_previous_day{k}" for k in range(1, 6)
                     for name in ("temperature_2m", "precipitation", "wind_speed_10m", "relative_humidity_2m")]
        result = get_json(PREVIOUS_RUNS_URL, {"latitude": station["lat"], "longitude": station["lon"],
                                              "start_date": f"{year}-01-01", "end_date": f"{year}-12-31",
                                              "hourly": ",".join(variables), "timezone": "Europe/Madrid"})
        hourly = pd.DataFrame(result["hourly"])
        hourly["date"] = pd.to_datetime(hourly["time"]).dt.normalize()
        days = []
        for k in range(1, 6):
            grouped = hourly.groupby("date")
            days.append(pd.DataFrame({
                "k": k,
                "temp_mean": grouped[f"temperature_2m_previous_day{k}"].mean(),
                "temp_max": grouped[f"temperature_2m_previous_day{k}"].max(),
                "rain": grouped[f"precipitation_previous_day{k}"].sum(min_count=1),
                "wind": grouped[f"wind_speed_10m_previous_day{k}"].max(),
                "humidity": grouped[f"relative_humidity_2m_previous_day{k}"].mean(),
            }).reset_index())
        pd.concat(days).to_csv(cache, index=False)
    table = pd.read_csv(cache, parse_dates=["date"])
    return {k: group.drop(columns="k").set_index("date") for k, group in table.groupby("k")}


# ============================================================================
# 2. THE TABLES OF ROWS
# ============================================================================

print("STEP 1: data")
history = load_pollen_history()
stations = load_stations()
stations = stations[stations["station_code"].isin(history["station_code"].unique())].sort_values("station_code").reset_index(drop=True)
station_number = {code: i for i, code in enumerate(stations["station_code"])}
print(f"  {len(history):,} readings, {history['date'].min():%Y-%m-%d} to {history['date'].max():%Y-%m-%d}, "
      f"{len(stations)} stations, {history['pollen_type'].nunique()} pollen types")

weather, issued = {}, {}
by_code = stations.set_index("station_code", drop=False)
for _, station in stations.iterrows():
    code = station["station_code"]
    # stations very close to another one use that one's weather (see F.WEATHER_FROM)
    source = by_code.loc[F.WEATHER_FROM.get(code, code)]
    weather[code] = F.weather_features(load_weather(source)).set_index("date")
    forecasts = load_issued_forecasts(source, TEST_YEAR)
    # where an archived forecast is missing, fall back to what happened
    issued[code] = {k: table.combine_first(weather[code][table.columns].reindex(table.index)) for k, table in forecasts.items()}

all_days = pd.date_range("1993-01-01", history["date"].max(), freq="D")
wide = {}                                         # pollen type -> table (index = every day, columns = stations)
for pollen_type in F.POLLEN_TYPES:
    one = history[history["pollen_type"] == pollen_type]
    wide[pollen_type] = one.pivot(index="date", columns="station_code", values="value").reindex(all_days)


def build(pollen_type, lead, delay, use_issued_forecasts):
    """All rows (every station, every day) for one pollen type and lead."""
    table = wide[pollen_type]
    network = table.mean(axis=1)
    parts = []
    for code in table.columns:
        rows = F.station_rows(station_number[code], table[code], network, weather[code], pollen_type, lead, delay,
                              forecast_weather=issued[code] if use_issued_forecasts else None)
        rows["station_code"] = code
        rows["issue_date"] = rows.index
        rows["target_date"] = rows.index + pd.Timedelta(days=lead)
        parts.append(rows)
    rows = pd.concat(parts, ignore_index=True)
    rows["target_level"] = F.level_of(pollen_type, rows["target_value"])
    return rows.dropna(subset=["target_level"])


# ============================================================================
# 3. TRAIN AND TEST
# ============================================================================

def smooth_by_day(series_by_doy):
    """A 15-day moving average over the days of the year (wrapping round New Year)."""
    full = series_by_doy.reindex(range(1, 367)).interpolate(limit_direction="both")
    padded = pd.concat([full.iloc[-15:], full, full.iloc[:15]])
    return padded.rolling(15, center=True, min_periods=1).mean().iloc[15:-15].set_axis(range(1, 367))


print("\nSTEP 2: training (data up to 2024) and testing (2025)")
models, results, test_rows, climate, season = {}, [], [], {}, {}
for pollen_type in F.POLLEN_TYPES:
    # seasonal pattern from the training years only: used by the baseline and to define the season
    train_readings = history[(history["pollen_type"] == pollen_type) & (history["date"] <= TRAIN_UNTIL)]
    doy = train_readings["date"].dt.day_of_year
    climate[pollen_type] = smooth_by_day(train_readings.groupby(doy)["value"].mean())
    share_medio = (F.level_of(pollen_type, train_readings["value"]) >= 1).astype(float)
    season[pollen_type] = smooth_by_day(pd.Series(share_medio).groupby(doy.to_numpy()).mean()) >= SEASON_SHARE

    for lead in F.LEADS:
        # TRAIN: the weather that happened stands in for the forecast; readings 3 and 6 days old
        train = pd.concat([build(pollen_type, lead, delay, use_issued_forecasts=False) for delay in (3, 6)])
        train = train[train["target_date"] <= TRAIN_UNTIL]
        # small trees on purpose: 60 models must fit in a few MB so the free server can load them
        model = HistGradientBoostingClassifier(max_iter=60, learning_rate=0.1, max_leaf_nodes=15, random_state=0)
        model.fit(train[F.FEATURES], train["target_level"].astype(int))
        models[(pollen_type, lead)] = model

        # TEST on 2025: readings 3 days old, weather forecasts as they were issued
        for delay in (3, 6):
            test = build(pollen_type, lead, delay, use_issued_forecasts=True)
            test = test[test["target_date"].dt.year == TEST_YEAR].copy()
            test["predicted"] = model.predict(test[F.FEATURES])
            probabilities = model.predict_proba(test[F.FEATURES])
            test["prob_alto_or_more"] = probabilities[:, [i for i, c in enumerate(model.classes_) if c >= 2]].sum(axis=1)

            # BASELINE: the last available reading, adjusted for the season
            target_doy = test["target_date"].dt.day_of_year
            reading_doy = (test["issue_date"] - pd.to_timedelta(test["last_age"].fillna(delay), unit="D")).dt.day_of_year
            pattern = climate[pollen_type]
            ratio = (pattern.reindex(target_doy).to_numpy() + 0.5) / (pattern.reindex(reading_doy).to_numpy() + 0.5)
            guess = np.where(test["last_value"].notna(), test["last_value"] * ratio, pattern.reindex(target_doy).to_numpy())
            test["baseline"] = F.level_of(pollen_type, guess)
            test["in_season"] = season[pollen_type].reindex(target_doy).to_numpy()
            test["pollen_type"], test["delay"] = pollen_type, delay

            in_season = test[test["in_season"]]
            high = in_season[in_season["target_level"] >= 2]
            entry = {
                "pollen_type": pollen_type, "lead": lead, "delay": delay,
                "days": len(test), "season_days": len(in_season),
                "accuracy_all": (test["predicted"] == test["target_level"]).mean(),
                "baseline_all": (test["baseline"] == test["target_level"]).mean(),
                # the level the app acts on: 0 bajo, 1 medio, 2 alto or more
                "accuracy_season": (in_season["predicted"].clip(upper=2) == in_season["target_level"].clip(upper=2)).mean(),
                "baseline_season": (in_season["baseline"].clip(upper=2) == in_season["target_level"].clip(upper=2)).mean(),
                # the exact official level (four levels)
                "exact_season": (in_season["predicted"] == in_season["target_level"]).mean(),
                "baseline_exact_season": (in_season["baseline"] == in_season["target_level"]).mean(),
                "error_season": (in_season["predicted"].clip(upper=2) - in_season["target_level"].clip(upper=2)).abs().mean(),
                "baseline_error_season": (in_season["baseline"].clip(upper=2) - in_season["target_level"].clip(upper=2)).abs().mean(),
                "high_days": len(high),
                "high_caught": (high["predicted"] >= 2).mean() if len(high) else np.nan,
                "baseline_high_caught": (high["baseline"] >= 2).mean() if len(high) else np.nan,
            }
            entry["gain"] = entry["accuracy_season"] - entry["baseline_season"]
            entry["reliable"] = bool(entry["gain"] >= MIN_GAIN and entry["error_season"] < entry["baseline_error_season"]
                                     and entry["season_days"] >= MIN_SEASON_DAYS)
            results.append(entry)
            if delay == 3:
                test_rows.append(test[["station_code", "pollen_type", "issue_date", "target_date", "lead", "target_value",
                                       "target_level", "predicted", "baseline", "prob_alto_or_more", "in_season"]])
    done = [r for r in results if r["pollen_type"] == pollen_type and r["delay"] == 3]
    print(f"  {pollen_type:22s} app level, in season: " + "  ".join(
        f"d{r['lead']} {r['accuracy_season']:.0%} vs {r['baseline_season']:.0%}{'*' if r['reliable'] else ' '}" for r in done))

results = pd.DataFrame(results)
test_rows = pd.concat(test_rows, ignore_index=True)
main = results[results["delay"] == 3]


# ============================================================================
# 4. THE 24 MAY 2025 CHECK and the forecast file for demo mode
# ============================================================================

def forecast_as_of(day):
    """Every station, type and lead, using only what was known on `day` (readings at least 3 days old)."""
    rows = []
    for pollen_type in F.POLLEN_TYPES:
        for lead in F.LEADS:
            table = build_for_day(pollen_type, lead, day)
            if table.empty:
                continue
            model = models[(pollen_type, lead)]
            probabilities = model.predict_proba(table[F.FEATURES])
            table["predicted_level"] = model.predict(table[F.FEATURES])
            table["prob_alto_or_more"] = probabilities[:, [i for i, c in enumerate(model.classes_) if c >= 2]].sum(axis=1)
            table["pollen_type"] = pollen_type
            rows.append(table)
    return pd.concat(rows, ignore_index=True)


def build_for_day(pollen_type, lead, day):
    table = wide[pollen_type]
    network = table.mean(axis=1)
    parts = []
    for code in table.columns:
        rows = F.station_rows(station_number[code], table[code], network, weather[code], pollen_type, lead, 3,
                              forecast_weather=issued[code])
        row = rows.loc[[day]].copy()
        if pd.isna(row["last_value"].iloc[0]):
            continue                                  # no reading in the 10 days before: no forecast for this station
        row["station_code"], row["issue_date"], row["target_date"] = code, day, day + pd.Timedelta(days=lead)
        row["actual_level"] = F.level_of(pollen_type, row["target_value"])
        parts.append(row)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


print(f"\nSTEP 3: what the model said on {CHECK_DAY:%d %b %Y} (last reading used: {CHECK_DAY - pd.Timedelta(days=3):%d %b})")
check = forecast_as_of(CHECK_DAY).merge(stations, on="station_code")
reliable_now = main.set_index(["pollen_type", "lead"])["reliable"]
check["reliable"] = [bool(reliable_now.get((t, l), False)) for t, l in zip(check["pollen_type"], check["lead"])]
levels = config.POLLEN_LEVELS
grass = check[(check["pollen_type"] == "Gramíneas") & (check["lead"] >= 1)]
for target_date, group in grass.groupby("target_date"):
    predicted = group["predicted_level"].map(lambda x: levels[int(x)]).value_counts().to_dict()
    actual = group["actual_level"].dropna().map(lambda x: levels[int(x)]).value_counts().to_dict()
    print(f"  {target_date:%d %b}: predicted {predicted} | real {actual} | real average {group['target_value'].mean():.0f} grains/m3")

demo_file = data.BACKUP_DIR / "replay" / f"pollen_forecast_{CHECK_DAY:%Y-%m-%d}.csv"
check[["station", "station_code", "lat", "lon", "pollen_type", "issue_date", "target_date", "lead", "predicted_level",
       "prob_alto_or_more", "reliable", "last_value", "last_age", "target_value", "actual_level"]].to_csv(demo_file, index=False)


# ============================================================================
# 5. SAVE: models, numbers, report, chart
# ============================================================================

print("\nSTEP 4: saving")
joblib.dump(models, OUT / "models.joblib", compress=3)
meta = {
    "trained_on": f"{history['date'].min():%Y-%m-%d} to {TRAIN_UNTIL:%Y-%m-%d}",
    "tested_on": str(TEST_YEAR),
    "made": pd.Timestamp.now().strftime("%Y-%m-%d"),
    "scikit_learn": sklearn.__version__,
    "features": F.FEATURES,
    "station_number": station_number,
    "reliable_rule": f"on in-season days, the model gets the app's level (bajo / medio / alto-or-more) right at least "
                     f"{MIN_GAIN * 100:.0f} percentage points more often than the baseline, with a smaller mean level error, "
                     f"and there are at least {MIN_SEASON_DAYS} in-season station-days in {TEST_YEAR} to judge from",
    "results": [{k: (None if isinstance(v, float) and np.isnan(v) else (round(v, 4) if isinstance(v, float) else v))
                 for k, v in row.items()} for row in main.to_dict("records")],
}
(OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
results.to_csv(OUT / "backtest_2025.csv", index=False)
test_rows.to_csv(CACHE / "test_rows_2025.csv.gz", index=False)
check.to_csv(OUT / f"check_{CHECK_DAY:%Y-%m-%d}.csv", index=False)

# ---- chart: predicted vs real level, spring 2025, at the station with most readings in central Madrid
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

chart_types = ["Gramíneas", "Olivo", "Plátano de paseo", "Cupresáceas/Taxáceas"]
chart_station = "AYTM" if "AYTM" in station_number else stations["station_code"].iloc[0]
station_name = stations.set_index("station_code").loc[chart_station, "station"]
spring = test_rows[(test_rows["station_code"] == chart_station)
                   & (test_rows["target_date"] >= f"{TEST_YEAR}-02-15") & (test_rows["target_date"] <= f"{TEST_YEAR}-06-30")]
figure, axes = plt.subplots(len(chart_types), 2, figsize=(11, 8.5), sharex=True, sharey=True)
for row, pollen_type in enumerate(chart_types):
    for column, lead in enumerate((1, 5)):
        axis = axes[row][column]
        part = spring[(spring["pollen_type"] == pollen_type) & (spring["lead"] == lead)].sort_values("target_date")
        axis.fill_between(part["target_date"], part["target_level"], step="mid", color="#c3c2b7", alpha=0.75, label="Real level")
        axis.step(part["target_date"], part["predicted"], where="mid", color="#2a78d6", linewidth=1.6, label="Our model")
        axis.set_yticks(range(4))
        axis.set_yticklabels(config.POLLEN_LEVELS, fontsize=8)
        axis.set_ylim(-0.15, 3.3)
        axis.grid(axis="y", color="#e1e0d9", linewidth=0.6)
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)
        axis.set_title(f"{config.POLLEN_NAMES[pollen_type]} · forecast {lead} day{'s' if lead > 1 else ''} ahead", fontsize=9.5, loc="left")
        axis.xaxis.set_major_locator(mdates.MonthLocator())          # one tick per month: "Mar", "Apr"...
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
        axis.tick_params(axis="x", labelsize=8)
axes[0][1].legend(loc="upper right", fontsize=8, frameon=False)
figure.suptitle(f"Predicted vs real pollen level, spring {TEST_YEAR} · {station_name} (year not used in training)", fontsize=11.5, x=0.01, ha="left")
figure.tight_layout(rect=(0, 0, 1, 0.97))
figure.savefig(OUT / f"spring_{TEST_YEAR}_predicted_vs_real.png", dpi=130, facecolor="#fcfcfb")

# ---- report
lines = [
    "# Pollen forecast model: backtest report", "",
    f"Made on {meta['made']} by `train_pollen_model.py`. Trained on {meta['trained_on']}, tested on {TEST_YEAR} "
    "(a year the model never saw).", "",
    "## How to read this", "",
    "- **Lead day**: how many days ahead (1 = tomorrow). Readings are published about 3 days late, so the forecast "
    "for lead day 5 is made about 8 days after the last reading it can see.",
    "- **In season**: only the days of the year when that pollen type is normally around. Out of season everything "
    "is `bajo` and any method scores close to 100%, so those days are not counted.",
    "- **Baseline**: the last available reading, adjusted for the usual seasonal change. The model has to beat this.",
    "- **High days caught**: of the days that really were `alto` or `muy alto`, the share the forecast also put at `alto` or more.",
    "- **Model right / Baseline right**: share of in-season days with the right level out of the three the app acts on "
    "(bajo, medio, alto-or-more; alto and muy alto give the same advice). **Exact level** is the stricter score out of "
    "all four official levels.",
    f"- **RELIABLE**: {meta['reliable_rule']}.",
    f"- The {TEST_YEAR} test used the weather forecasts as they were issued at the time (Open-Meteo previous-runs "
    "archive), not the weather that later happened. Training on earlier years used the weather that happened, "
    "because old forecasts are not archived.", "",
    "## Result by pollen type and lead day (in season, 2025)", "",
    "| Pollen type | Lead | Model right | Baseline right | Gain | Exact level (model / baseline) | High days caught (model / baseline) | In-season days | Verdict |",
    "|---|---|---|---|---|---|---|---|---|",
]
for _, r in main.iterrows():                       # lead 0 (the same day) is included: the planner uses it for "today"
    caught = "–" if pd.isna(r["high_caught"]) else f"{r['high_caught']:.0%} / {r['baseline_high_caught']:.0%}"
    lines.append(f"| {config.POLLEN_NAMES[r['pollen_type']]} | {r['lead']} | {r['accuracy_season']:.0%} | {r['baseline_season']:.0%} | "
                 f"{r['gain']:+.0%} | {r['exact_season']:.0%} / {r['baseline_exact_season']:.0%} | {caught} | {r['season_days']} | "
                 f"{'**RELIABLE**' if r['reliable'] else 'not reliable'} |")
late = results[(results["delay"] == 6) & (results["lead"].isin(REPORT_LEADS))]
lines += ["", "## If readings arrive later than 3 days", "",
          "The same test with readings 6 days old (it happens): average in-season accuracy "
          f"{late['accuracy_season'].mean():.0%} for the model and {late['baseline_season'].mean():.0%} for the baseline, "
          f"against {main[main['lead'].isin(REPORT_LEADS)]['accuracy_season'].mean():.0%} and "
          f"{main[main['lead'].isin(REPORT_LEADS)]['baseline_season'].mean():.0%} with 3-day-old readings.", "",
          f"## The {CHECK_DAY:%d %B %Y} check (grass)", "",
          f"Forecast made with only what was known on {CHECK_DAY:%d %B}: readings up to {CHECK_DAY - pd.Timedelta(days=3):%d %B} "
          "and the weather forecast issued that day.", "",
          "| Day | Lead | Stations predicted at each level | Real level at those stations | Real network average (grains/m³) |", "|---|---|---|---|---|"]
for target_date, group in grass.groupby("target_date"):
    predicted = ", ".join(f"{n} {k}" for k, n in group["predicted_level"].map(lambda x: levels[int(x)]).value_counts().items())
    actual = ", ".join(f"{n} {k}" for k, n in group["actual_level"].dropna().map(lambda x: levels[int(x)]).value_counts().items())
    lines.append(f"| {target_date:%d %b} | {group['lead'].iloc[0]} | {predicted} | {actual} | {group['target_value'].mean():.0f} |")
lines += ["", f"![Predicted vs real, spring {TEST_YEAR}](spring_{TEST_YEAR}_predicted_vs_real.png)", ""]
(OUT / "backtest_report.md").write_text("\n".join(lines), encoding="utf-8")
print(f"  saved models.joblib ({(OUT / 'models.joblib').stat().st_size / 1e6:.1f} MB), meta.json, backtest_report.md, the chart, and {demo_file.name}")
print(f"  RELIABLE: {int(main[main['lead'].isin(REPORT_LEADS)]['reliable'].sum())} of {len(main[main['lead'].isin(REPORT_LEADS)])} type + lead-day combinations")
