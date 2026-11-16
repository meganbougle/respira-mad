"""
compare_models.py - compares classic time-series models with our gradient
boosting model, on equal terms, and prepares what the app needs from the winner.

Run it with:   .venv/bin/python compare_models.py        (about 15-25 minutes)
Run train_pollen_model.py first: this script reuses its downloads (model_data/)
and its 2025 test rows. Needs statsmodels and matplotlib (not needed by the app):
               .venv/bin/pip install statsmodels matplotlib

Every model works on  log(1 + grains/m3)  per station and pollen type, daily,
and obeys the same rule as the app: a forecast made on day D only knows
readings up to day D-3 (the publication delay). Lead 1 = tomorrow ... lead 5,
so the models forecast 4 to 8 days after the last reading.

What it does:
  1. DIAGNOSTICS for three example series: ADF stationarity test, ACF / PACF
     and an STL decomposition. Plots go to pollen_model/plots/.
  2. Fits, on data up to 2024:
       a. Seasonal naive: the same day one year earlier.
       b. Exponential smoothing (simple / Holt / damped Holt, chosen by AIC).
       c. ARIMAX (dynamic harmonic regression): yearly cycle as Fourier terms,
          weather as exogenous variables, ARMA errors with orders chosen by AIC.
       d. Gradient boosting with QUANTILE regression (10%, 50%, 90%), same
          inputs as the classifier the app already uses.
  3. Backtests all of them on 2025, same station-days, same lead days, same
     metrics: mean absolute error on the log scale and accuracy on the
     official level.
  4. Saves the comparison (CSV + Markdown tables), the choice of model per
     pollen type, the quantile models, and the demo-day forecast with intervals.

The written explanation is MODEL_REPORT.md.
"""

import json
import logging
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import streamlit  # noqa: F401
from joblib import Parallel, delayed
from scipy.stats import norm
from sklearn.ensemble import HistGradientBoostingRegressor

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")                    # statsmodels warns a lot about convergence; we check results ourselves

import config  # noqa: E402
from pollen_model import arimax as A  # noqa: E402
from pollen_model import features as F  # noqa: E402

ROOT = Path(__file__).parent
CACHE = ROOT / "model_data"
OUT = ROOT / "pollen_model"
PLOTS = OUT / "plots"
PLOTS.mkdir(exist_ok=True)

TRAIN_UNTIL = pd.Timestamp("2024-12-31")
TEST_YEAR = 2025
CHECK_DAY = pd.Timestamp(config.PLANNER_DEMO_DATE)   # the demo planner's day: 24 May 2025
DELAY = F.READING_DELAY                              # 3 days
REPORT_LEADS = [1, 2, 3, 4, 5]

# ---- choices for the statistical models (ours; explained in MODEL_REPORT.md)
FIT_FROM = pd.Timestamp("2015-01-01")                # they are fitted on the last 10 training years
WINDOW_DAYS = A.WINDOW_DAYS                          # readings a forecast looks back over (120 days)
FOURIER_CHOICES = range(1, 9)                        # how many yearly sine/cosine pairs to try
ARMA_CHOICES = [(1, 0, 0), (2, 0, 0), (3, 0, 0), (1, 0, 1), (2, 0, 1), (1, 0, 2), (2, 0, 2)]
REFERENCE_STATION = "ALER"                           # Madrid-Barrio Salamanca: orders are chosen on this station
QUANTILES = {"q10": 0.10, "q50": 0.50, "q90": 0.90}  # an 80% prediction interval and the median
MIN_GAIN, MIN_SEASON_DAYS = 0.05, 200                # the RELIABLE rule of train_pollen_model.py
INTERVAL = 0.80

EXAMPLES = [("Gramíneas", "ALER"), ("Plátano de paseo", "ALER"), ("Olivo", "ALER")]
MODEL_NAMES = {"naive": "Estacional ingenuo", "ets": "Suavizado exponencial", "arimax": "ARIMAX", "gb": "Gradient boosting"}


# ============================================================================
# 1. DATA (everything comes from model_data/, downloaded by train_pollen_model.py)
# ============================================================================

print("STEP 1: data")
history = pd.read_csv(CACHE / "pollen_history.csv.gz", parse_dates=["date"])
test_rows = pd.read_csv(CACHE / "test_rows_2025.csv.gz", parse_dates=["issue_date", "target_date"])
meta = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))
station_number = meta["station_number"]
station_names = (pd.read_csv(ROOT / "backup" / "pollen.csv").drop_duplicates("station_code")
                 .set_index("station_code")["station"].to_dict())

all_days = pd.date_range("1993-01-01", history["date"].max(), freq="D")
wide = {}                                            # pollen type -> grains/m3 (index = every day, columns = stations)
for pollen_type in F.POLLEN_TYPES:
    one = history[history["pollen_type"] == pollen_type]
    wide[pollen_type] = one.pivot(index="date", columns="station_code", values="value").reindex(all_days)

weather, issued = {}, {}                             # per station: weather that happened / forecasts as issued in 2025
for code in station_number:
    source = F.WEATHER_FROM.get(code, code)          # nearby stations share one weather series
    weather[code] = F.weather_features(pd.read_csv(CACHE / f"weather_{source}.csv", parse_dates=["date"])).set_index("date")
    table = pd.read_csv(CACHE / f"issued_{source}_{TEST_YEAR}.csv", parse_dates=["date"])
    issued[code] = {k: g.drop(columns="k").set_index("date").combine_first(weather[code][["temp_mean", "temp_max", "rain", "wind", "humidity"]].reindex(g["date"]))
                    for k, g in table.groupby("k")}

test_stations = sorted(test_rows["station_code"].unique())
print(f"  {len(history):,} readings, {len(test_stations)} stations with 2025 data, {len(test_rows):,} test rows")


exog_table = A.exog_table                           # Fourier terms + the day's weather (shared with the app)


def log_series(pollen_type, code):
    return np.log1p(wide[pollen_type][code])


# ============================================================================
# 2. DIAGNOSTICS: ADF, ACF / PACF, STL for three example series
# ============================================================================

def diagnostics():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import statsmodels.api as sm
    from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
    from statsmodels.tsa.seasonal import STL
    from statsmodels.tsa.stattools import adfuller

    print("\nSTEP 2: diagnostics (ADF, ACF/PACF, STL)")
    found = []
    for pollen_type, code in EXAMPLES:
        name = f"{config.POLLEN_NAMES[pollen_type]} · {station_names.get(code, code)}"
        slug = f"{pollen_type.split()[0].lower().replace('í', 'i').replace('á', 'a')}_{code}"
        days = pd.date_range(FIT_FROM, TRAIN_UNTIL, freq="D")
        series = log_series(pollen_type, code).reindex(days)
        filled = series.interpolate(limit_direction="both")           # STL and ADF need a series without gaps

        # what is left after removing the yearly cycle and the weather (ordinary regression)
        regressors = sm.add_constant(exog_table(days, weather[code].reindex(days), 6))
        has = series.notna()
        residual = (series - sm.OLS(series[has], regressors[has]).fit().predict(regressors)).interpolate(limit_direction="both")

        adf_raw = adfuller(filled, autolag="AIC")
        adf_residual = adfuller(residual, autolag="AIC")
        found.append({"series": name, "adf_stat": adf_raw[0], "adf_p": adf_raw[1], "adf_lags": adf_raw[2],
                      "adf_resid_stat": adf_residual[0], "adf_resid_p": adf_residual[1],
                      "share_zero": float((series.dropna() == 0).mean()), "missing_days": int(series.isna().sum())})
        print(f"  {name}: ADF {adf_raw[0]:.1f} (p={adf_raw[1]:.4f}); without the yearly cycle {adf_residual[0]:.1f} (p={adf_residual[1]:.4f})")

        # ACF and PACF: of the series itself (the yearly wave dominates) and of what is left without it
        figure, axes = plt.subplots(2, 2, figsize=(11, 6.5))
        plot_acf(filled, lags=60, ax=axes[0][0], title="ACF de la serie (log)")
        plot_pacf(filled, lags=60, ax=axes[0][1], title="PACF de la serie (log)", method="ywm")
        plot_acf(residual, lags=30, ax=axes[1][0], title="ACF sin ciclo anual ni meteorología")
        plot_pacf(residual, lags=30, ax=axes[1][1], title="PACF sin ciclo anual ni meteorología", method="ywm")
        for axis in axes.ravel():
            axis.set_xlabel("Retardo (días)", fontsize=8)
            axis.tick_params(labelsize=8)
        figure.suptitle(f"{name}: autocorrelación, 2015–2024", fontsize=11.5, x=0.01, ha="left")
        figure.tight_layout(rect=(0, 0, 1, 0.96))
        figure.savefig(PLOTS / f"acf_pacf_{slug}.png", dpi=120, facecolor="white")
        plt.close(figure)

        # STL: trend + yearly seasonality + remainder
        parts = STL(filled, period=365, seasonal=13).fit()           # seasonal=13: the yearly shape changes slowly
        figure, axes = plt.subplots(4, 1, figsize=(11, 8), sharex=True)
        for axis, values, title in zip(axes, (filled, parts.trend, parts.seasonal, parts.resid),
                                       ("Serie: log(1 + granos/m³)", "Tendencia", "Estacionalidad anual", "Resto")):
            axis.plot(values.index, values, color="#2a78d6", linewidth=0.8)
            axis.set_title(title, fontsize=9.5, loc="left")
            axis.grid(color="#e1e0d9", linewidth=0.5)
            axis.tick_params(labelsize=8)
            for side in ("top", "right"):
                axis.spines[side].set_visible(False)
        figure.suptitle(f"{name}: descomposición STL, 2015–2024", fontsize=11.5, x=0.01, ha="left")
        figure.tight_layout(rect=(0, 0, 1, 0.96))
        figure.savefig(PLOTS / f"stl_{slug}.png", dpi=120, facecolor="white")
        plt.close(figure)
        found[-1]["slug"] = slug
        found[-1]["seasonal_strength"] = float(max(0, 1 - parts.resid.var() / (parts.resid + parts.seasonal).var()))
    return found


# ============================================================================
# 3. THE STATISTICAL MODELS
# ============================================================================

def choose_orders(pollen_type):
    """
    For one pollen type, on the reference station: how many Fourier pairs
    (lowest AIC of an ordinary regression) and which ARMA orders (lowest AIC
    of the full model). d = 0 because the ADF test says the series, once the
    yearly cycle is removed, has no unit root.
    """
    import statsmodels.api as sm
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    warnings.filterwarnings("ignore")
    code = REFERENCE_STATION
    days = pd.date_range(FIT_FROM, TRAIN_UNTIL, freq="D")
    series = log_series(pollen_type, code).reindex(days)
    has = series.notna()
    fourier_aic = {}
    for pairs in FOURIER_CHOICES:
        regressors = sm.add_constant(exog_table(days, weather[code].reindex(days), pairs))
        fourier_aic[pairs] = sm.OLS(series[has], regressors[has]).fit().aic
    pairs = min(fourier_aic, key=fourier_aic.get)

    regressors = exog_table(days, weather[code].reindex(days), pairs)
    order_aic = {}
    for order in ARMA_CHOICES:
        try:
            order_aic[order] = SARIMAX(series, exog=regressors, order=order, trend="c").fit(disp=False, maxiter=200).aic
        except Exception:
            continue
    order = min(order_aic, key=order_aic.get)
    return {"pollen_type": pollen_type, "fourier_pairs": pairs, "order": order,
            "order_aic": {str(k): round(v, 1) for k, v in order_aic.items()},
            "fourier_aic": {k: round(v, 1) for k, v in fourier_aic.items()}}


def smoothing_forecast(values, steps, alpha, beta, phi):
    """
    Exponential smoothing by hand, with the fitted parameters: walk through the
    readings updating the level (and the trend, if the model has one), skip the
    days without a reading, then project `steps` days ahead.
    alpha = weight of the newest reading; beta = how fast the trend adapts
    (0 = no trend); phi = damping of the trend (1 = not damped).
    """
    level, trend = np.nan, 0.0
    for value in values:
        if np.isnan(level):
            level = value                               # start at the first reading in the window
            continue
        expected = level + phi * trend
        error = 0.0 if np.isnan(value) else value - expected
        level = expected + alpha * error
        trend = phi * trend + beta * error
    ahead = []
    for _ in range(steps):
        level, trend = level + phi * trend, phi * trend
        ahead.append(level)
    return np.array(ahead)                              # all NaN if the window had no reading at all


def fit_and_backtest(pollen_type, code, pairs, order, issue_days):
    """
    Fits exponential smoothing and ARIMAX on one station's series (2015-2024),
    then, for every issue day, forecasts with only the readings up to D-3.
    The parameters stay fixed during 2025: nothing from the test year is learnt.
    Returns (table of forecasts, the fitted parameters).
    """
    from statsmodels.tsa.statespace.exponential_smoothing import ExponentialSmoothing
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    warnings.filterwarnings("ignore")

    series = log_series(pollen_type, code)
    fit_days = pd.date_range(FIT_FROM, TRAIN_UNTIL, freq="D")
    train = series.reindex(fit_days)

    # ---- (b) exponential smoothing: simple, Holt or damped Holt, the lowest AIC wins
    ets_fits = {}
    for label, options in (("simple", {}), ("Holt", {"trend": True}), ("Holt amortiguado", {"trend": True, "damped_trend": True})):
        try:
            ets_fits[label] = ExponentialSmoothing(train, initialization_method="concentrated", **options).fit(disp=False)
        except Exception:
            continue
    ets_label = min(ets_fits, key=lambda k: ets_fits[k].aic)
    ets = ets_fits[ets_label]
    ets_alpha = float(ets.params["smoothing_level"])
    ets_beta = float(ets.params.get("smoothing_trend", 0.0))           # 0 = no trend (simple smoothing)
    ets_phi = float(ets.params.get("damping_trend", 1.0))              # 1 = the trend is not damped

    # ---- (c) ARIMAX: regression on Fourier terms + weather, with ARMA errors
    regressors = exog_table(fit_days, weather[code].reindex(fit_days), pairs)
    try:
        arimax = SARIMAX(train, exog=regressors, order=order, trend="c").fit(disp=False, maxiter=200)
    except Exception:
        order = (1, 0, 0)
        arimax = SARIMAX(train, exog=regressors, order=order, trend="c").fit(disp=False, maxiter=200)

    arimax_params = arimax.params.to_numpy()
    rows = []
    for day in issue_days:
        last = day - pd.Timedelta(days=DELAY)           # the newest reading that has been published
        window_days = pd.date_range(last - pd.Timedelta(days=WINDOW_DAYS - 1), last, freq="D")
        window = series.reindex(window_days)
        future_days = pd.date_range(last + pd.Timedelta(days=1), day + pd.Timedelta(days=max(F.LEADS)), freq="D")
        steps = len(future_days)                        # 8: D-2 ... D+5

        # weather for the days to forecast: what happened up to day D, the forecast issued on D after that
        future_weather = weather[code][["temp_mean", "rain", "wind", "humidity"]].reindex(future_days).astype(float)
        for k in range(1, max(F.LEADS) + 1):
            target = day + pd.Timedelta(days=k)
            if k in issued[code] and target in issued[code][k].index:
                future_weather.loc[target] = issued[code][k].loc[target, ["temp_mean", "rain", "wind", "humidity"]]

        ets_mean = smoothing_forecast(window.to_numpy(), steps, ets_alpha, ets_beta, ets_phi)
        arimax_mean, arimax_se = A.forecast(window, weather[code].reindex(window_days), future_weather, order, pairs, arimax_params)

        for lead in F.LEADS:
            i = DELAY - 1 + lead                        # position of day D+lead among the forecast days
            target = day + pd.Timedelta(days=lead)
            # (a) seasonal naive: the same day a year earlier (or the days around it, or two years earlier)
            naive = series.get(target - pd.Timedelta(days=365), np.nan)
            if np.isnan(naive):
                naive = series.reindex(pd.date_range(target - pd.Timedelta(days=368), target - pd.Timedelta(days=362))).mean()
            if np.isnan(naive):
                naive = series.reindex(pd.date_range(target - pd.Timedelta(days=733), target - pd.Timedelta(days=727))).mean()
            naive = 0.0 if np.isnan(naive) else naive
            rows.append({"station_code": code, "pollen_type": pollen_type, "issue_date": day, "lead": lead,
                         "naive": naive,
                         "ets": max(ets_mean[i], 0.0) if not np.isnan(ets_mean[i]) else naive,
                         "arimax": max(arimax_mean[i], 0.0), "arimax_se": arimax_se[i],
                         "arimax_q10": A.quantiles(arimax_mean[i], arimax_se[i])[0],
                         "arimax_q90": A.quantiles(arimax_mean[i], arimax_se[i])[2]})
    fitted = {"station_code": code, "pollen_type": pollen_type, "ets_kind": ets_label, "order": list(order),
              "fourier_pairs": pairs, "ets_params": {k: float(v) for k, v in ets.params.items()},
              "arimax_params": [float(v) for v in arimax_params], "arimax_param_names": list(arimax.params.index),
              "arimax_aic": float(arimax.aic), "ets_aic": float(ets.aic)}
    return pd.DataFrame(rows), fitted


# ============================================================================
# 4. GRADIENT BOOSTING WITH QUANTILES (same inputs as the app's classifier)
# ============================================================================

def build(pollen_type, lead, delay, use_issued_forecasts):
    """All rows (every station, every day) for one pollen type and lead: same code as train_pollen_model.py."""
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
    return pd.concat(parts, ignore_index=True)


def train_quantiles(pollen_type, lead):
    """Four small models per type and lead day: the average of log(1 + grains) and the 10%, 50% and 90% points around it."""
    train = pd.concat([build(pollen_type, lead, delay, use_issued_forecasts=False) for delay in (3, 6)])
    train = train[(train["target_date"] <= TRAIN_UNTIL) & train["target_value"].notna()]
    options = dict(max_iter=60, learning_rate=0.1, max_leaf_nodes=15, random_state=0)
    target = np.log1p(train["target_value"])
    # First a model for the AVERAGE of log(1 + grains). Then three models for how far the real value
    # usually falls from that average: its 10%, 50% (median) and 90% points. (Learnt directly, the
    # quantiles never move from zero for pollen types that are at zero most days of the year.)
    fitted = {"mean": HistGradientBoostingRegressor(**options).fit(train[F.FEATURES], target)}
    distance = target - fitted["mean"].predict(train[F.FEATURES])
    for name, quantile in QUANTILES.items():
        fitted[name] = HistGradientBoostingRegressor(loss="quantile", quantile=quantile, **options).fit(train[F.FEATURES], distance)

    def predict(rows):
        return F.predict_quantiles(fitted, rows[F.FEATURES], grains=False).set_axis(rows.index).add_prefix("gb_")

    test = build(pollen_type, lead, DELAY, use_issued_forecasts=True)
    test = test[(test["target_date"].dt.year == TEST_YEAR) & test["target_value"].notna()].copy()
    test = pd.concat([test[["station_code", "issue_date", "lead"]], predict(test)], axis=1)
    test["pollen_type"] = pollen_type

    # the forecast as of the demo day (24 May 2025), with only what was known then
    demo = build(pollen_type, lead, DELAY, use_issued_forecasts=True)
    demo = demo[(demo["issue_date"] == CHECK_DAY) & demo["last_value"].notna()].copy()
    demo = pd.concat([demo[["station_code", "lead"]], predict(demo)], axis=1)
    demo["pollen_type"] = pollen_type
    return (pollen_type, lead), fitted, test, demo


# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    diagnostics_found = diagnostics()

    print("\nSTEP 3: statistical models (orders chosen by AIC, fitted on 2015-2024, tested on 2025)")
    stat_cache = CACHE / "stat_backtest_2025.csv.gz"
    fits_cache = CACHE / "stat_fits.json"
    if stat_cache.exists() and fits_cache.exists():
        print("  (using the saved results in model_data/; delete stat_backtest_2025.csv.gz to redo them)")
        stat = pd.read_csv(stat_cache, parse_dates=["issue_date"])
        saved = json.loads(fits_cache.read_text(encoding="utf-8"))
        orders, fits = saved["orders"], saved["fits"]
    else:
        orders = Parallel(n_jobs=-1)(delayed(choose_orders)(t) for t in F.POLLEN_TYPES)
        for o in orders:
            o["order"] = list(o["order"])
            print(f"  {o['pollen_type']:22s} Fourier pairs {o['fourier_pairs']}, ARMA order {tuple(o['order'])}")
        chosen = {o["pollen_type"]: o for o in orders}
        issue_days = sorted(test_rows["issue_date"].unique())
        jobs = [(t, code) for t in F.POLLEN_TYPES for code in test_stations]
        done = Parallel(n_jobs=-1, verbose=5)(
            delayed(fit_and_backtest)(t, code, chosen[t]["fourier_pairs"], tuple(chosen[t]["order"]), issue_days) for t, code in jobs)
        stat = pd.concat([d[0] for d in done], ignore_index=True)
        fits = [d[1] for d in done]
        stat.to_csv(stat_cache, index=False)
        fits_cache.write_text(json.dumps({"orders": orders, "fits": fits}, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\nSTEP 4: gradient boosting with quantile regression (trained up to 2024)")
    done = Parallel(n_jobs=-1, verbose=5)(delayed(train_quantiles)(t, lead) for t in F.POLLEN_TYPES for lead in F.LEADS)
    quantile_models = {key: fitted for key, fitted, _, _ in done}
    gb = pd.concat([d[2] for d in done], ignore_index=True)
    demo_quantiles = pd.concat([d[3] for d in done], ignore_index=True)
    joblib.dump(quantile_models, OUT / "quantile_models.joblib", compress=3)
    print(f"  saved quantile_models.joblib ({(OUT / 'quantile_models.joblib').stat().st_size / 1e6:.1f} MB)")

    # ------------------------------------------------------------------------
    # 5. THE COMPARISON: same rows, same lead days, same metrics for everyone
    # ------------------------------------------------------------------------
    print("\nSTEP 5: comparison on 2025")
    keys = ["station_code", "pollen_type", "issue_date", "lead"]
    rows = test_rows.merge(stat, on=keys, how="inner").merge(gb, on=keys, how="inner")
    print(f"  {len(rows):,} of {len(test_rows):,} test rows have a forecast from every model")
    rows["actual"] = np.log1p(rows["target_value"])
    rows["gb"] = rows["gb_q50"]                           # gradient boosting's point forecast = its median
    for pollen_type, index in rows.groupby("pollen_type").groups.items():
        for name in ("naive", "ets", "arimax", "gb_q50"):
            rows.loc[index, f"{name}_level"] = F.level_of(pollen_type, np.expm1(rows.loc[index, name]))
    rows["gb_level"] = rows["predicted"]                  # the level of the classifier the app already uses
    rows.to_csv(CACHE / "comparison_rows_2025.csv.gz", index=False)

    results = []
    for (pollen_type, lead), group in rows.groupby(["pollen_type", "lead"], sort=False):
        season = group[group["in_season"]]
        entry = {"pollen_type": pollen_type, "lead": lead, "season_days": len(season), "all_days": len(group)}
        for name in MODEL_NAMES:
            entry[f"{name}_mae"] = (season[name] - season["actual"]).abs().mean()                   # log scale, in season
            entry[f"{name}_acc"] = (season[f"{name}_level"] == season["target_level"]).mean()      # exact official level
            entry[f"{name}_acc_app"] = (season[f"{name}_level"].clip(upper=2) == season["target_level"].clip(upper=2)).mean()
            entry[f"{name}_mae_all"] = (group[name] - group["actual"]).abs().mean()                 # whole year
        entry["gb_q50_acc"] = (season["gb_q50_level"] == season["target_level"]).mean()            # level read off the median
        for name in ("arimax", "gb"):                     # how often the real value fell inside the 80% interval
            entry[f"{name}_coverage"] = ((season["actual"] >= season[f"{name}_q10"]) & (season["actual"] <= season[f"{name}_q90"])).mean()
            entry[f"{name}_width"] = (season[f"{name}_q90"] - season[f"{name}_q10"]).mean()
        results.append(entry)
    results = pd.DataFrame(results)
    results.to_csv(OUT / "comparison_2025.csv", index=False)

    # ---- the best model per pollen type: highest accuracy on the official level (leads 1-5, in season);
    #      if two models are within one point of each other, the lower log error decides
    report = results[results["lead"].isin(REPORT_LEADS)]
    summary = report.groupby("pollen_type", sort=False)[[f"{m}_{k}" for m in MODEL_NAMES for k in ("mae", "acc", "acc_app")]
                                                         + ["arimax_coverage", "gb_coverage", "arimax_width", "gb_width"]].mean()
    choice = {}
    for pollen_type, r in summary.iterrows():
        best_accuracy = max(r[f"{m}_acc"] for m in MODEL_NAMES)
        close = [m for m in MODEL_NAMES if r[f"{m}_acc"] >= best_accuracy - 0.01]
        choice[pollen_type] = min(close, key=lambda m: r[f"{m}_mae"])
        print(f"  {pollen_type:22s} " + "  ".join(f"{m} {r[f'{m}_acc']:.0%}/{r[f'{m}_mae']:.2f}" for m in MODEL_NAMES)
              + f"   -> {choice[pollen_type]}")
    # ---- for the pollen types where ARIMAX won, the app needs the same facts it has for gradient boosting:
    #      per lead day, is it RELIABLE (same rule as train_pollen_model.py: at least 5 points better than the
    #      baseline on the level the app acts on, with a smaller mean error) and how often it was right
    arimax_types = [t for t, m in choice.items() if m == "arimax"]
    arimax_results = []
    for (pollen_type, lead), group in rows[rows["pollen_type"].isin(arimax_types)].groupby(["pollen_type", "lead"], sort=False):
        season = group[group["in_season"]]
        real, baseline, model = season["target_level"].clip(upper=2), season["baseline"].clip(upper=2), season["arimax_level"].clip(upper=2)
        accuracy, baseline_accuracy = (model == real).mean(), (baseline == real).mean()
        reliable = bool(accuracy - baseline_accuracy >= MIN_GAIN and (model - real).abs().mean() < (baseline - real).abs().mean()
                        and len(season) >= MIN_SEASON_DAYS)
        arimax_results.append({"pollen_type": pollen_type, "lead": int(lead), "reliable": reliable,
                               "accuracy_season": round(float(accuracy), 4), "baseline_season": round(float(baseline_accuracy), 4)})
    by_series = {(f["pollen_type"], f["station_code"]): f for f in fits}
    (OUT / "arimax_params.json").write_text(json.dumps({
        "made": pd.Timestamp.now().strftime("%Y-%m-%d"), "fitted_on": f"{FIT_FROM:%Y-%m-%d} to {TRAIN_UNTIL:%Y-%m-%d}",
        "results": arimax_results,
        "models": {t: {code: {"order": by_series[(t, code)]["order"], "fourier_pairs": by_series[(t, code)]["fourier_pairs"],
                              "params": by_series[(t, code)]["arimax_params"]}
                       for code in test_stations if (t, code) in by_series} for t in arimax_types},
    }, ensure_ascii=False), encoding="utf-8")

    (OUT / "model_choice.json").write_text(json.dumps({
        "made": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "rule": "per pollen type, the model with the highest accuracy on the official level in season (2025, lead days 1-5); "
                "models within one percentage point are separated by the lower mean absolute error on the log scale",
        "choice": choice, "interval": INTERVAL,
        "summary": {t: {k: round(float(v), 4) for k, v in r.items()} for t, r in summary.iterrows()},
        "orders": {o["pollen_type"]: {"fourier_pairs": o["fourier_pairs"], "order": o["order"]} for o in orders},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- Markdown tables for MODEL_REPORT.md
    order = list(MODEL_NAMES)
    lines = ["| Tipo de polen | " + " | ".join(f"{MODEL_NAMES[m]}: acierto / MAE" for m in order) + " | Mejor |", "|---|" + "---|" * (len(order) + 1)]
    for pollen_type, r in summary.iterrows():
        cells = [f"{'**' if m == choice[pollen_type] else ''}{r[f'{m}_acc']:.0%} / {r[f'{m}_mae']:.2f}{'**' if m == choice[pollen_type] else ''}" for m in order]
        lines.append(f"| {config.POLLEN_NAMES[pollen_type]} | " + " | ".join(cells) + f" | {MODEL_NAMES[choice[pollen_type]]} |")
    mean = summary.mean()
    lines.append("| **Media** | " + " | ".join(f"{mean[f'{m}_acc']:.0%} / {mean[f'{m}_mae']:.2f}" for m in order) + " | |")
    lines += ["", "| Tipo de polen | Día | " + " | ".join(f"{MODEL_NAMES[m]}: acierto / MAE" for m in order) + " | Días de temporada |",
              "|---|---|" + "---|" * (len(order) + 1)]
    for _, r in report.iterrows():
        best = max(order, key=lambda m: r[f"{m}_acc"])
        cells = [f"{'**' if m == best else ''}{r[f'{m}_acc']:.0%} / {r[f'{m}_mae']:.2f}{'**' if m == best else ''}" for m in order]
        lines.append(f"| {config.POLLEN_NAMES[r['pollen_type']]} | +{r['lead']} | " + " | ".join(cells) + f" | {r['season_days']} |")
    lines += ["", "| Tipo de polen | Fourier (pares) | Orden ARMA (p, d, q) | Cobertura intervalo 80%: ARIMAX | Cobertura intervalo 80%: gradient boosting |", "|---|---|---|---|---|"]
    for o in orders:
        r = summary.loc[o["pollen_type"]]
        lines.append(f"| {config.POLLEN_NAMES[o['pollen_type']]} | {o['fourier_pairs']} | {tuple(o['order'])} | {r['arimax_coverage']:.0%} | {r['gb_coverage']:.0%} |")
    lines += ["", "| Serie | ADF (serie) | p | ADF (sin ciclo anual) | p | Días con cero | Fuerza estacional (STL) |", "|---|---|---|---|---|---|---|"]
    for d in diagnostics_found:
        lines.append(f"| {d['series']} | {d['adf_stat']:.1f} | {d['adf_p']:.4f} | {d['adf_resid_stat']:.1f} | {d['adf_resid_p']:.4f} | {d['share_zero']:.0%} | {d['seasonal_strength']:.2f} |")
    (OUT / "comparison_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- chart: accuracy and error by lead day, averaged over the ten pollen types
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colours = {"naive": "#9a9a92", "ets": "#e0902b", "arimax": "#7b4fc4", "gb": "#2a78d6"}
    by_lead = report.groupby("lead")[[f"{m}_{k}" for m in MODEL_NAMES for k in ("mae", "acc")]].mean()
    figure, axes = plt.subplots(1, 2, figsize=(11, 4))
    for m in MODEL_NAMES:
        axes[0].plot(by_lead.index, by_lead[f"{m}_acc"] * 100, marker="o", color=colours[m], label=MODEL_NAMES[m])
        axes[1].plot(by_lead.index, by_lead[f"{m}_mae"], marker="o", color=colours[m], label=MODEL_NAMES[m])
    axes[0].set_title("Acierto del nivel oficial (%), en temporada", fontsize=10, loc="left")
    axes[1].set_title("Error absoluto medio en escala log, en temporada", fontsize=10, loc="left")
    for axis in axes:
        axis.set_xlabel("Días de antelación", fontsize=9)
        axis.set_xticks(REPORT_LEADS)
        axis.grid(color="#e1e0d9", linewidth=0.6)
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)
    axes[0].legend(fontsize=8.5, frameon=False)
    figure.suptitle("Comparación de modelos en 2025 (media de los 10 tipos de polen)", fontsize=11.5, x=0.01, ha="left")
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    figure.savefig(PLOTS / "comparison_by_lead.png", dpi=120, facecolor="white")

    # ---- chart: forecasts 3 days ahead against reality, grass at Barrio de Salamanca, spring 2025
    example = rows[(rows["pollen_type"] == "Gramíneas") & (rows["station_code"] == "ALER") & (rows["lead"] == 3)
                   & (rows["target_date"] >= f"{TEST_YEAR}-04-01") & (rows["target_date"] <= f"{TEST_YEAR}-07-15")].sort_values("target_date")
    figure, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, sharey=True)
    for axis, m in zip(axes, ("arimax", "gb")):
        axis.fill_between(example["target_date"], np.expm1(example[f"{m}_q10"]), np.expm1(example[f"{m}_q90"]), color=colours[m], alpha=0.18, label="Intervalo del 80%")
        axis.plot(example["target_date"], np.expm1(example[m]), color=colours[m], linewidth=1.6, linestyle="--", label=f"{MODEL_NAMES[m]} (3 días antes)")
        axis.plot(example["target_date"], example["target_value"], color="#222", linewidth=1.2, label="Medido")
        for threshold in config.POLLEN_THRESHOLDS["Gramíneas"]:
            axis.axhline(threshold, color="#b8b7ad", linewidth=0.7, linestyle=":")
        axis.set_yscale("symlog", linthresh=10)
        axis.set_ylabel("granos/m³", fontsize=9)
        axis.legend(fontsize=8.5, frameon=False, loc="upper right")
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)
    figure.suptitle(f"Gramíneas · {station_names.get('ALER', 'ALER')}: previsión a 3 días y medición, primavera {TEST_YEAR} (líneas de puntos: umbrales oficiales)",
                    fontsize=11, x=0.01, ha="left")
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    figure.savefig(PLOTS / "forecast_vs_real_gramineas_ALER.png", dpi=120, facecolor="white")

    # ---- the demo-day forecast (24 May 2025) gets its interval columns
    demo_file = ROOT / "backup" / "replay" / f"pollen_forecast_{CHECK_DAY:%Y-%m-%d}.csv"
    demo = pd.read_csv(demo_file).drop(columns=["q10", "q50", "q90", "model"], errors="ignore")
    for name in QUANTILES:
        demo_quantiles[name] = np.expm1(demo_quantiles[f"gb_{name}"]).round(1)       # back to grains/m3
    demo = demo.merge(demo_quantiles[["station_code", "pollen_type", "lead", "q10", "q50", "q90"]],
                      on=["station_code", "pollen_type", "lead"], how="left")
    demo["model"] = "gb"
    # where ARIMAX won, its forecast of that day replaces gradient boosting's
    flags = {(r["pollen_type"], r["lead"]): r["reliable"] for r in arimax_results}
    that_day = stat[(stat["issue_date"] == CHECK_DAY) & stat["pollen_type"].isin(arimax_types)].set_index(["station_code", "pollen_type", "lead"])
    for i, row in demo.iterrows():
        key = (row["station_code"], row["pollen_type"], row["lead"])
        if key in that_day.index:
            f = that_day.loc[key]
            alto = np.log1p(config.POLLEN_THRESHOLDS[row["pollen_type"]][1])
            demo.loc[i, ["q10", "q50", "q90"]] = np.expm1([f["arimax_q10"], f["arimax"], f["arimax_q90"]]).round(1)
            demo.loc[i, "predicted_level"] = int(F.level_of(row["pollen_type"], [np.expm1(f["arimax"])])[0])
            demo.loc[i, "prob_alto_or_more"] = float(1 - norm.cdf((alto - f["arimax"]) / f["arimax_se"]))
            demo.loc[i, "reliable"] = flags[(row["pollen_type"], row["lead"])]
            demo.loc[i, "model"] = "arimax"
    demo.to_csv(demo_file, index=False)

    # ---- the readings that had been published by the demo day (up to 3 days before it), for the app's chart
    recent = history[(history["date"] >= CHECK_DAY - pd.Timedelta(days=16)) & (history["date"] <= CHECK_DAY - pd.Timedelta(days=DELAY))]
    recent = recent.rename(columns={"date": "timestamp"})
    recent["station"] = recent["station_code"].map(station_names)
    recent.to_csv(ROOT / "backup" / "replay" / f"pollen_recent_{CHECK_DAY:%Y-%m-%d}.csv", index=False)
    print(f"\nSaved: comparison_2025.csv, comparison_tables.md, model_choice.json, arimax_params.json, plots/, and the intervals in {demo_file.name}")
