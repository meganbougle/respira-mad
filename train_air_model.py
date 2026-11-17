"""
train_air_model.py - builds, tests and saves OUR OWN air-quality forecast.

Run it with:   .venv/bin/python train_air_model.py        (about 30-40 minutes the first time)

DATA RULE: the forecast is made only from the official hourly measurements of
the Comunidad de Madrid and the Ayuntamiento de Madrid (air_model/history.py).
The only other input is the weather (Open-Meteo), which the rule allows.

What it does, for NO2, O3, PM10 and PM2.5, hour by hour, up to 5 days ahead:
  1. Downloads the hourly history of both networks since 2019 and the hourly
     weather of 8 zones.
  2. Compares three ways of forecasting, with the same split for all:
     train up to 2023, CHOOSE on 2024, TEST on 2025 (never used to decide).
       a. Baseline: "the same hour yesterday".
       b. SARIMAX: seasonal ARIMA with a 24-hour season and the weather as
          exogenous variables (fitted on a few representative stations:
          it is slow, one model per station and pollutant).
       c. Gradient boosting with lagged features (one model per pollutant
          for all stations).
  3. Decides how far ahead the forecast may be shown: only the days ahead on
     which the chosen model clearly beat the baseline in 2024 (rule below).
  4. Saves the model, the numbers, the charts and the demo-day forecasts.

Needs statsmodels and matplotlib. Downloads are kept in model_data/.
"""

import json
import logging
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
import streamlit  # noqa: F401
from joblib import Parallel, delayed
from sklearn.cluster import KMeans
from sklearn.ensemble import HistGradientBoostingRegressor

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

import config  # noqa: E402
from air_model import features as F  # noqa: E402
from air_model import history  # noqa: E402

ROOT = Path(__file__).parent
CACHE = ROOT / "model_data"
OUT = ROOT / "air_model"
PLOTS = OUT / "plots"
PLOTS.mkdir(exist_ok=True)

FIRST_YEAR = 2019
TRAIN_UNTIL = pd.Timestamp("2023-12-31 23:00")       # train on 2019-2023 ...
CHOOSE_YEAR, TEST_YEAR = 2024, 2025                  # ... choose on 2024, test on 2025
ZONES = 8                                            # stations are grouped into 8 weather zones
ISSUE_HOURS = [6, 14, 22]                            # the test makes a forecast at these hours of every day
TRAIN_EVERY, TRAIN_LEADS = 5, 6                      # training uses one hour in five, and 6 random leads for each
COLD_SHARE = 0.15                                    # share of training rows that pretend the app has just started

# ---- OUR RULE for how far ahead the air forecast is shown (also explained in MODEL_REPORT.md).
# Day by day (hours 1-24 ahead = day 1 ... hours 97-120 = day 5), on 2024, the chosen model must
#   - have a mean error at least 10% smaller than the baseline ("the same hour yesterday"), and
#   - at least 5% smaller than simply saying "what is typical at this station at this hour".
# The forecast is shown up to the last day on which every pollutant passes, counting from day 1.
MIN_SKILL_BASELINE, MIN_SKILL_TYPICAL = 0.10, 0.05

# SARIMAX is fitted on these stations only (two of each network): one model per station and pollutant
SARIMAX_STATIONS = ["Escuelas Aguirre", "Casa de Campo", "Alcalá de Henares", "Getafe"]
SARIMAX_ORDERS = [((1, 0, 1), (0, 1, 1, 24)), ((2, 0, 1), (0, 1, 1, 24)), ((1, 0, 1), (1, 0, 1, 24))]
SARIMAX_WINDOW = 14 * 24                             # readings a SARIMAX forecast looks back over (14 days)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
HOURLY = {"temperature_2m": "temp", "wind_speed_10m": "wind", "precipitation": "rain",
          "relative_humidity_2m": "humidity", "shortwave_radiation": "radiation"}
MODEL_NAMES = {"baseline": "Misma hora de ayer", "typical": "Lo habitual a esa hora", "sarimax": "SARIMAX", "gb": "Gradient boosting"}


def get_json(url, params, tries=8):
    """Download JSON; if Open-Meteo's free service refuses (hourly limit), wait and try again."""
    for attempt in range(tries):
        try:
            response = requests.get(url, params=params, timeout=600)
            if response.status_code == 200:
                return response.json()
            problem = f"answer {response.status_code}: {response.text[:120]}"
        except requests.RequestException as error:
            problem = str(error)[:120]
        wait = 60 * (attempt + 1)
        print(f"    the weather service did not answer ({problem}); waiting {wait} s...", flush=True)
        time.sleep(wait)
    raise RuntimeError("Open-Meteo kept refusing: run this script again in an hour (downloads are kept in model_data/).")


def zone_weather(zone, lat, lon, end):
    """Hourly weather that happened in one zone since FIRST_YEAR (Open-Meteo historical weather)."""
    cache = CACHE / f"air_weather_zone{zone}.csv"
    if not cache.exists():
        print(f"  downloading hourly weather for zone {zone}...", flush=True)
        parts = []
        for year in range(FIRST_YEAR, end.year + 1):          # one year per request
            result = get_json(ARCHIVE_URL, {"latitude": lat, "longitude": lon, "start_date": f"{year}-01-01",
                                            "end_date": min(pd.Timestamp(f"{year}-12-31"), end).strftime("%Y-%m-%d"),
                                            "hourly": ",".join(HOURLY), "timezone": "Europe/Madrid"})
            parts.append(pd.DataFrame(result["hourly"]))
        pd.concat(parts).rename(columns={"time": "timestamp", **HOURLY}).to_csv(cache, index=False)
    return pd.read_csv(cache, parse_dates=["timestamp"]).drop_duplicates("timestamp").set_index("timestamp")


def issued_run(zone, lat, lon, year, k):
    """The weather forecast AS ISSUED k days before each hour of `year`, for one zone (Open-Meteo previous runs)."""
    cache = CACHE / f"air_issued_zone{zone}_{year}_k{k}.csv"
    if not cache.exists():
        names = {f"{their}_previous_day{k}": ours for their, ours in HOURLY.items()}
        parts = []
        for start, end in ((f"{year}-01-01", f"{year}-06-30"), (f"{year}-07-01", f"{year}-12-31")):     # the archive is slow: half a year per request
            result = get_json(PREVIOUS_RUNS_URL, {"latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
                                                  "hourly": ",".join(names), "timezone": "Europe/Madrid"})
            parts.append(pd.DataFrame(result["hourly"]).rename(columns={"time": "timestamp", **names}))
        pd.concat(parts).to_csv(cache, index=False)
    return pd.read_csv(cache, parse_dates=["timestamp"]).drop_duplicates("timestamp").set_index("timestamp")


# ============================================================================
# 1. DATA
# ============================================================================

print("STEP 1: data")
wide = history.load(FIRST_YEAR)
stations = history.stations()
measured = sorted(set().union(*[set(t.columns) for t in wide.values()]) & set(stations["station_code"]))
stations = stations[stations["station_code"].isin(measured)].sort_values("station_code").reset_index(drop=True)
station_number = {code: i for i, code in enumerate(stations["station_code"])}
hours = wide["NO2"].index
print(f"  {len(stations)} stations, {hours[0]:%Y-%m-%d} to {hours[-1]:%Y-%m-%d}")

# weather zones: nearby stations share one weather series (keeps downloads inside Open-Meteo's free limits)
grouping = KMeans(n_clusters=ZONES, random_state=0, n_init=10).fit(stations[["lat", "lon"]])
stations["zone"] = grouping.labels_
zone = stations["zone"].to_numpy()
centres = grouping.cluster_centers_

# weather[run, zone, hour, variable]: run 0 = what happened; runs 1-5 = the forecast issued 1-5 days before.
# SIMPLIFICATION: the archive of issued forecasts is very slow to download, so they are downloaded for the
# three zones with most stations (Madrid city, south, east: 41 of 52 stations). Each other zone uses its own
# weather shifted by the forecast ERROR of the nearest of those three (rain: that zone's forecast rain).
weather = np.full((6, ZONES, len(hours), len(F.WEATHER_PLUS)), np.nan, dtype="float32")
actual = {}
for z in range(ZONES):
    actual[z] = zone_weather(z, centres[z][0], centres[z][1], hours[-1].normalize()).reindex(hours).ffill(limit=3)
    weather[0, z] = F.add_weather_windows(actual[z]).to_numpy()
reference_zones = list(stations["zone"].value_counts().index[:3])
tasks = [(z, year, k) for z in reference_zones for year in (CHOOSE_YEAR, TEST_YEAR) for k in range(1, 6)]
print(f"  weather forecasts as issued in {CHOOSE_YEAR} and {TEST_YEAR}: {len(tasks)} files (slow the first time)...", flush=True)
with ThreadPoolExecutor(max_workers=8) as pool:
    downloaded = list(pool.map(lambda t: issued_run(t[0], centres[t[0]][0], centres[t[0]][1], t[1], t[2]), tasks))
issued_by = dict(zip(tasks, downloaded))
for z in range(ZONES):
    nearest = min(reference_zones, key=lambda r: (centres[r][0] - centres[z][0]) ** 2 + (centres[r][1] - centres[z][1]) ** 2)
    for year in (CHOOSE_YEAR, TEST_YEAR):
        in_year = hours.year == year
        for k in range(1, 6):
            issued = issued_by[(nearest, year, k)].reindex(hours)
            if nearest != z:
                error = issued - actual[nearest][F.WEATHER]
                shifted = (actual[z][F.WEATHER] + error).clip(lower=0)
                shifted["temp"] = actual[z]["temp"] + error["temp"]            # temperature may be below zero
                shifted["rain"] = issued["rain"]
                issued = shifted
            # where no archived forecast exists (before 19 January 2024) the weather that happened stands in
            issued = issued.combine_first(actual[z][F.WEATHER])
            weather[k, z, in_year] = F.add_weather_windows(issued).to_numpy()[in_year]
weather_start = hours[0]

train_hours = np.where(hours <= TRAIN_UNTIL)[0]
rng = np.random.default_rng(0)


def year_origins(year):
    """Positions of the test forecasts of one year: every day at 06:00, 14:00 and 22:00."""
    return np.where((hours.year == year) & np.isin(hours.hour, ISSUE_HOURS) & (np.arange(len(hours)) + F.MAX_LEAD < len(hours)))[0]


def ica_category(pollutant, value):
    """Official ICA category 0-5 of an array of concentrations."""
    return np.searchsorted(np.array(config.ICA_BANDS[pollutant]), np.round(value), side="left").clip(max=5)


# ============================================================================
# 2. GRADIENT BOOSTING + BASELINE, every station
# ============================================================================

def train_and_test(pollutant):
    values = wide[pollutant].reindex(columns=stations["station_code"]).to_numpy(dtype="float32")
    has_data = np.where(np.isfinite(values[train_hours]).mean(axis=0) > 0.2)[0]      # stations that measure it
    clim = F.climatology(values[train_hours], hours[train_hours])
    stats = F.rolling(values)
    local = np.random.default_rng(F.POLLUTANTS.index(pollutant))

    # ---- training rows: one hour in five, every station that measures it, 6 random leads each
    grid = train_hours[local.integers(0, TRAIN_EVERY)::TRAIN_EVERY]
    grid = grid[grid >= 48]
    pos = np.repeat(np.repeat(grid, len(has_data)), TRAIN_LEADS)
    station = np.repeat(np.tile(has_data, len(grid)), TRAIN_LEADS)
    lead = local.integers(1, F.MAX_LEAD + 1, size=len(pos))
    keep = (pos + lead <= train_hours[-1]) & np.isfinite(values[pos, station])
    keep &= np.isfinite(values[np.minimum(pos + lead, len(hours) - 1), station])
    pos, station, lead = pos[keep], station[keep], lead[keep]
    cold = local.random(len(pos)) < COLD_SHARE
    table = F.rows(values, hours, stats, pos, station, lead, clim, weather, weather_start, zone, cold=cold)
    model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, max_leaf_nodes=63,
                                          categorical_features=[F.STATION_COLUMN], random_state=0)
    model.fit(table.to_numpy(), np.log1p(values[pos + lead, station]))
    n_train = len(pos)
    del table

    # ---- test rows of 2024 and 2025: every station, three forecasts a day, every lead from 1 to 120 hours
    summary, kept = [], []
    subset = [station_number[c] for c in sarimax_codes]
    for year in (CHOOSE_YEAR, TEST_YEAR):
        origins = year_origins(year)
        for one_lead in range(1, F.MAX_LEAD + 1):
            pos = np.repeat(origins, len(has_data))
            station = np.tile(has_data, len(origins))
            lead = np.full(len(pos), one_lead)
            actual = values[pos + lead, station]
            keep = np.isfinite(values[pos, station]) & np.isfinite(actual)
            pos, station, lead, actual = pos[keep], station[keep], lead[keep], actual[keep]
            run = np.ceil(lead / 24).astype(int)                      # the weather forecast issued that many days before
            table = F.rows(values, hours, stats, pos, station, lead, clim, weather, weather_start, zone, forecast_run=run)
            guess = {"baseline": F.baseline(values, pos, station, lead), "typical": table["clim_target"].to_numpy(),
                     "gb": np.clip(np.expm1(model.predict(table.to_numpy())), 0, None)}
            real_category = ica_category(pollutant, actual)
            entry = {"pollutant": pollutant, "year": year, "lead": one_lead, "rows": len(pos)}
            for name, values_guess in guess.items():
                entry[f"{name}_mae"] = float(np.abs(values_guess - actual).mean())
                entry[f"{name}_cat"] = float((ica_category(pollutant, values_guess) == real_category).mean())
            summary.append(entry)
            in_subset = np.isin(station, subset)
            kept.append(pd.DataFrame({"pollutant": pollutant, "year": year, "pos": pos[in_subset], "station": station[in_subset],
                                      "lead": one_lead, "actual": actual[in_subset], **{k: v[in_subset] for k, v in guess.items()}}))
    print(f"  {pollutant}: trained on {n_train:,} rows, {len(has_data)} stations", flush=True)
    return pollutant, model, clim, pd.DataFrame(summary), pd.concat(kept, ignore_index=True), has_data


# ============================================================================
# 3. SARIMAX on a few stations (seasonal period 24 hours, weather as exogenous variables)
# ============================================================================

def exog_of(run, z, positions):
    """The weather regressors of SARIMAX for some hours (scaled so the numbers have similar sizes)."""
    block = weather[run, z, positions][:, :len(F.WEATHER)].astype(float)
    block = np.where(np.isnan(block), weather[0, z, positions][:, :len(F.WEATHER)], block)
    return np.nan_to_num(block / np.array([10.0, 10.0, 1.0, 100.0, 100.0]))


def sarimax_series(pollutant, code):
    """Fit SARIMAX on one station's 2023 readings, then forecast from every test origin of 2024 and 2025."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    warnings.filterwarnings("ignore")
    s = station_number[code]
    series = np.log1p(wide[pollutant][code].to_numpy(dtype=float))
    fit_positions = np.where(hours.year == 2023)[0]
    fits = {}
    for order, seasonal in SARIMAX_ORDERS:                 # the lowest AIC wins
        try:
            fits[(order, seasonal)] = SARIMAX(series[fit_positions], exog=exog_of(0, zone[s], fit_positions), order=order,
                                              seasonal_order=seasonal, trend=None if seasonal[1] else "c").fit(disp=False, maxiter=50)
        except Exception:
            continue
    best = min(fits, key=lambda k: fits[k].aic)
    fitted = fits[best]

    out = []
    leads = np.arange(1, F.MAX_LEAD + 1)
    run = np.ceil(leads / 24).astype(int)
    for year in (CHOOSE_YEAR, TEST_YEAR):
        for origin in year_origins(year):
            if np.isnan(series[origin]):
                continue
            window = np.arange(origin - SARIMAX_WINDOW + 1, origin + 1)
            future = origin + leads
            future_exog = np.vstack([exog_of(k, zone[s], future[run == k]) for k in range(1, 6)])
            try:
                mean = fitted.apply(series[window], exog=exog_of(0, zone[s], window)).forecast(F.MAX_LEAD, exog=future_exog)
            except Exception:
                continue
            out.append(pd.DataFrame({"pollutant": pollutant, "pos": origin, "station": s, "lead": leads,
                                     "sarimax": np.clip(np.expm1(mean), 0, None)}))
    return pd.concat(out, ignore_index=True), {"pollutant": pollutant, "station_code": code, "order": list(best[0]),
                                               "seasonal_order": list(best[1]), "aic": float(fitted.aic)}


# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    by_name = stations.set_index("station")["station_code"]
    sarimax_codes = [by_name[name] for name in SARIMAX_STATIONS if name in by_name]

    print("\nSTEP 2: gradient boosting and the baseline, every station (train 2019-2023, test rows 2024 and 2025)")
    done = [train_and_test(p) for p in F.POLLUTANTS]
    models = {p: model for p, model, *_ in done}
    clims = {p: clim for p, _, clim, *_ in done}
    by_lead = pd.concat([d[3] for d in done], ignore_index=True)
    subset_rows = pd.concat([d[4] for d in done], ignore_index=True)
    measuring = {d[0]: [stations["station_code"][i] for i in d[5]] for d in done}       # which stations measure each pollutant

    print("\nSTEP 3: SARIMAX on", ", ".join(SARIMAX_STATIONS))
    sarimax_cache = CACHE / "air_sarimax.pkl"
    if sarimax_cache.exists():
        print("  (using the saved SARIMAX forecasts in model_data/; delete air_sarimax.pkl to redo them)")
        sarimax_rows, sarimax_fits = pd.read_pickle(sarimax_cache)
    else:
        jobs = [(p, code) for p in F.POLLUTANTS for code in sarimax_codes if code in measuring[p]]
        results = Parallel(n_jobs=-1, verbose=5)(delayed(sarimax_series)(p, code) for p, code in jobs)
        sarimax_rows = pd.concat([r[0] for r in results], ignore_index=True)
        sarimax_fits = [r[1] for r in results]
        pd.to_pickle((sarimax_rows, sarimax_fits), sarimax_cache)

    # ------------------------------------------------------------------------
    # 4. COMPARE, CHOOSE (2024), TEST (2025)
    # ------------------------------------------------------------------------
    print("\nSTEP 4: comparison")
    same = subset_rows.merge(sarimax_rows, on=["pollutant", "pos", "station", "lead"], how="inner")
    same["day"] = np.ceil(same["lead"] / 24).astype(int)
    by_lead["day"] = np.ceil(by_lead["lead"] / 24).astype(int)
    names = ["baseline", "typical", "sarimax", "gb"]
    for name in names:
        same[f"{name}_err"] = (same[name] - same["actual"]).abs()
    # (a) the three-way comparison, on exactly the same rows (the SARIMAX stations)
    three_way = same.groupby(["pollutant", "year", "day"], sort=False)[[f"{n}_err" for n in names]].mean().reset_index()
    three_way["rows"] = same.groupby(["pollutant", "year", "day"], sort=False).size().to_numpy()
    # (b) gradient boosting against the baseline on every station
    weighted = by_lead.assign(**{c: by_lead[c] * by_lead["rows"] for c in by_lead.columns if c.endswith(("_mae", "_cat"))})
    by_day = weighted.groupby(["pollutant", "year", "day"], sort=False).sum(numeric_only=True).reset_index()
    for c in [c for c in by_lead.columns if c.endswith(("_mae", "_cat"))]:
        by_day[c] = by_day[c] / by_day["rows"]
    by_day = by_day.drop(columns="lead")

    # CHOOSE on 2024: the model with the lowest mean error on the shared rows, per pollutant
    choice, horizon_days = {}, {}
    for pollutant in F.POLLUTANTS:
        chosen_year = three_way[(three_way["pollutant"] == pollutant) & (three_way["year"] == CHOOSE_YEAR)]
        means = {n: chosen_year[f"{n}_err"].mean() for n in ("sarimax", "gb")}
        choice[pollutant] = min(means, key=means.get)
        # how far ahead: day by day on 2024, the chosen model against the baseline and against "what is typical"
        table = (by_day if choice[pollutant] == "gb" else three_way.rename(columns=lambda c: c.replace("_err", "_mae")))
        table = table[(table["pollutant"] == pollutant) & (table["year"] == CHOOSE_YEAR)].sort_values("day")
        days = 0
        for _, r in table.iterrows():
            model_error = r[f"{choice[pollutant]}_mae"]
            if model_error <= (1 - MIN_SKILL_BASELINE) * r["baseline_mae"] and model_error <= (1 - MIN_SKILL_TYPICAL) * r["typical_mae"]:
                days = int(r["day"])
            else:
                break
        horizon_days[pollutant] = days
        print(f"  {pollutant}: chosen on {CHOOSE_YEAR}: {choice[pollutant]} (SARIMAX {means['sarimax']:.2f}, gradient boosting {means['gb']:.2f} µg/m3); "
              f"beats the baseline clearly up to day {days}")
    horizon = min(horizon_days.values())
    print(f"  => the air forecast is shown up to {horizon} day(s) ahead ({horizon * 24} hours)")

    by_lead.to_csv(OUT / "comparison_by_lead.csv", index=False)
    by_day.to_csv(OUT / "comparison_by_day.csv", index=False)
    three_way.to_csv(OUT / "comparison_three_way.csv", index=False)

    # ------------------------------------------------------------------------
    # 5. SAVE the model and what the app needs
    # ------------------------------------------------------------------------
    joblib.dump(models, OUT / "models.joblib", compress=3)
    np.savez_compressed(OUT / "climatology.npz", **{p.replace(".", "_"): clims[p] for p in F.POLLUTANTS})
    meta = {
        "made": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "trained_on": f"{FIRST_YEAR}-01-01 to {TRAIN_UNTIL:%Y-%m-%d}", "chosen_on": str(CHOOSE_YEAR), "tested_on": str(TEST_YEAR),
        "features": F.FEATURES,
        "stations": stations.assign(zone=stations["zone"].astype(int)).to_dict("records"),
        "zone_centres": [[round(float(a), 4), round(float(b), 4)] for a, b in centres],
        "measuring": measuring,
        "choice": choice, "horizon_days_by_pollutant": horizon_days,
        "horizon_days": horizon, "horizon_hours": horizon * 24,
        "rule": f"day by day on {CHOOSE_YEAR}, the chosen model's mean error must be at least {MIN_SKILL_BASELINE:.0%} smaller than "
                f"'the same hour yesterday' and at least {MIN_SKILL_TYPICAL:.0%} smaller than 'what is typical at that station and hour'; "
                "the forecast is shown up to the last day on which all four pollutants pass",
        "sarimax": sarimax_fits,
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  saved models.joblib ({(OUT / 'models.joblib').stat().st_size / 1e6:.1f} MB), climatology.npz, meta.json")

    # ---- Markdown tables for MODEL_REPORT.md
    lines = []
    for year, title in ((CHOOSE_YEAR, "elección"), (TEST_YEAR, "prueba")):
        lines += [f"**{year} ({title}), mismas filas para los tres modelos (estaciones {', '.join(SARIMAX_STATIONS)}). Error medio en µg/m³.**", "",
                  "| Contaminante | Día | " + " | ".join(MODEL_NAMES[n] for n in names) + " | Filas |", "|---|---|" + "---|" * (len(names) + 1)]
        for _, r in three_way[three_way["year"] == year].iterrows():
            best = min(("sarimax", "gb"), key=lambda n: r[f"{n}_err"])
            cells = [f"{'**' if n == best else ''}{r[f'{n}_err']:.1f}{'**' if n == best else ''}" for n in names]
            lines.append(f"| {r['pollutant']} | +{int(r['day'])} | " + " | ".join(cells) + f" | {int(r['rows']):,} |")
        lines.append("")
    for year, title in ((CHOOSE_YEAR, "elección"), (TEST_YEAR, "prueba")):
        lines += [f"**{year} ({title}), todas las estaciones. Error medio en µg/m³ y acierto de la categoría oficial del ICA.**", "",
                  "| Contaminante | Día | Misma hora de ayer | Lo habitual | Gradient boosting | Mejora frente a ayer | Categoría: ayer / gradient boosting | Filas |",
                  "|---|---|---|---|---|---|---|---|"]
        for _, r in by_day[by_day["year"] == year].iterrows():
            lines.append(f"| {r['pollutant']} | +{int(r['day'])} | {r['baseline_mae']:.1f} | {r['typical_mae']:.1f} | {r['gb_mae']:.1f} | "
                         f"{1 - r['gb_mae'] / r['baseline_mae']:.0%} | {r['baseline_cat']:.0%} / {r['gb_cat']:.0%} | {int(r['rows']):,} |")
        lines.append("")
    lines += ["| Contaminante | Estación | Orden SARIMAX (p,d,q)(P,D,Q,24) |", "|---|---|---|"]
    names_by_code = stations.set_index("station_code")["station"]
    for f in sarimax_fits:
        lines.append(f"| {f['pollutant']} | {names_by_code[f['station_code']]} | {tuple(f['order'])}{tuple(f['seasonal_order'])} |")
    (OUT / "comparison_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- chart: error by hours ahead, 2025, every station
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colours = {"baseline": "#9a9a92", "typical": "#e0902b", "gb": "#2a78d6", "sarimax": "#7b4fc4"}
    figure, axes = plt.subplots(1, 4, figsize=(13, 3.6))
    for axis, pollutant in zip(axes, F.POLLUTANTS):
        part = by_lead[(by_lead["pollutant"] == pollutant) & (by_lead["year"] == TEST_YEAR)]
        for name in ("baseline", "typical", "gb"):
            axis.plot(part["lead"], part[f"{name}_mae"], color=colours[name], linewidth=1.6, label=MODEL_NAMES[name])
        subset = same[(same["pollutant"] == pollutant) & (same["year"] == TEST_YEAR)].groupby("lead")["sarimax_err"].mean()
        axis.plot(subset.index, subset, color=colours["sarimax"], linewidth=1.2, linestyle="--", label="SARIMAX (4 estaciones)")
        axis.axvline(horizon * 24, color="#222", linewidth=0.8, linestyle=":")
        axis.set_title(pollutant, fontsize=10, loc="left")
        axis.set_xlabel("Horas de antelación", fontsize=8.5)
        axis.set_xticks(range(0, 121, 24))
        axis.grid(color="#e1e0d9", linewidth=0.6)
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)
    axes[0].set_ylabel("Error medio (µg/m³)", fontsize=8.5)
    axes[-1].legend(fontsize=7.5, frameon=False)
    figure.suptitle(f"Previsión del aire: error según las horas de antelación, {TEST_YEAR} (línea de puntos: hasta dónde se muestra)", fontsize=11, x=0.01, ha="left")
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    figure.savefig(PLOTS / "air_error_by_lead.png", dpi=120, facecolor="white")

    # ------------------------------------------------------------------------
    # 6. The demo-day forecasts (28 May 2025 hour by hour, and 24 May 2025 at 07:00 for the planner)
    # ------------------------------------------------------------------------
    def forecast_at(moment, max_lead):
        """Every station and pollutant, as of `moment`, with the weather forecast issued then."""
        origin = hours.get_loc(moment)
        out = []
        for pollutant in F.POLLUTANTS:
            values = wide[pollutant].reindex(columns=stations["station_code"]).to_numpy(dtype="float32")
            stats = F.rolling(values[:origin + 1])
            usable = np.array([station_number[c] for c in measuring[pollutant] if np.isfinite(values[origin, station_number[c]])])
            if not len(usable):
                continue
            leads = np.arange(1, max_lead + 1)
            pos = np.full(len(usable) * len(leads), origin)
            station = np.repeat(usable, len(leads))
            lead = np.tile(leads, len(usable))
            table = F.rows(values[:origin + 1], hours, stats, pos, station, lead, clims[pollutant], weather, weather_start, zone,
                           forecast_run=np.ceil(lead / 24).astype(int))
            out.append(pd.DataFrame({"station_code": stations["station_code"].to_numpy()[station], "pollutant": pollutant,
                                     "issued": moment, "timestamp": moment + pd.to_timedelta(lead, unit="h"), "lead": lead,
                                     "value": np.clip(np.expm1(models[pollutant].predict(table.to_numpy())), 0, None).round(1)}))
        return pd.concat(out, ignore_index=True)

    if horizon:
        replay = ROOT / "backup" / "replay"
        comunidad = set(stations.loc[stations["source"] == "Comunidad de Madrid", "station_code"])     # the demo day only shows this network
        day = pd.Timestamp(config.REPLAY_DATE)
        main = pd.concat([forecast_at(day + pd.Timedelta(hours=h), min(horizon * 24, 30)) for h in range(24)], ignore_index=True)
        main[main["station_code"].isin(comunidad)].to_csv(replay / f"air_forecast_{config.REPLAY_DATE}.csv", index=False)
        planner_moment = pd.Timestamp(config.PLANNER_DEMO_DATE) + pd.Timedelta(hours=7)
        planner = forecast_at(planner_moment, horizon * 24)
        planner[planner["station_code"].isin(comunidad)].to_csv(replay / f"air_forecast_{config.PLANNER_DEMO_DATE}.csv", index=False)
        print("  saved the demo-day forecasts in backup/replay/")
    print("Done.")
