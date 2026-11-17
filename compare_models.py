"""
compare_models.py - compares classic time-series models with our gradient
boosting model, on equal terms, and prepares what the app needs from the winner.

Run it with:   .venv/bin/python compare_models.py        (about 15-25 minutes)
Run train_air_model.py and train_pollen_model.py first: this script reuses their
downloads (model_data/) and the 2024 and 2025 rows to score. Needs statsmodels and matplotlib (not needed by the app):
               .venv/bin/pip install statsmodels matplotlib

Every model works on  log(1 + grains/m3)  per station and pollen type, daily,
and obeys the same rule as the app: a forecast made on day D only knows
readings up to day D-3 (the publication delay). Lead 1 = tomorrow ... lead 5,
so the models forecast 4 to 8 days after the last reading.

The split, the same for every model:
    TRAIN up to 2023  ->  CHOOSE on 2024  ->  TEST on 2025 (never used to decide anything)

What it does:
  1. DIAGNOSTICS for three example series: ADF stationarity test, ACF / PACF
     and an STL decomposition. Plots go to pollen_model/plots/.
  2. Fits, on data up to 2023:
       a. Seasonal naive: the same day one year earlier.
       b. Exponential smoothing (simple / Holt / damped Holt, chosen by AIC).
       c. ARIMAX (dynamic harmonic regression): yearly cycle as Fourier terms,
          weather as exogenous variables, ARMA errors with orders chosen by AIC.
       d. Gradient boosting with QUANTILE regression (10%, 50%, 90%).
  3. Forecasts 2024 and 2025 with all of them: same station-days, same lead
     days, same metrics (mean absolute error on the log scale and accuracy
     on the official level).
  4. On 2024: picks the model per pollen type, decides which type + lead
     days are RELIABLE, and calibrates the 80% intervals (split conformal).
  5. Reports everything on 2025, and saves what the app needs: the choice,
     the quantile models, the ARIMAX parameters, the interval margins, the
     demo-day forecast, tables and charts (including feature importance).

ONE SOURCE OF TRUTH: the level the app shows is always the model's median
forecast read against the official thresholds (not a separate classifier).

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
from sklearn.inspection import permutation_importance

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")                    # statsmodels warns a lot about convergence; we check results ourselves

import config  # noqa: E402
from pollen_model import arimax as A  # noqa: E402
from pollen_model import features as F  # noqa: E402
from pollen_model import issued_weather  # noqa: E402

ROOT = Path(__file__).parent
CACHE = ROOT / "model_data"
OUT = ROOT / "pollen_model"
PLOTS = OUT / "plots"
PLOTS.mkdir(exist_ok=True)

TRAIN_UNTIL = pd.Timestamp("2023-12-31")             # every model is fitted on data up to 2023 ...
CHOOSE_YEAR = 2024                                   # ... the model per pollen type is CHOSEN (and intervals calibrated) on 2024 ...
TEST_YEAR = 2025                                     # ... and 2025 is the untouched TEST year
EVAL_YEARS = (CHOOSE_YEAR, TEST_YEAR)
CHECK_DAY = pd.Timestamp(config.PLANNER_DEMO_DATE)   # the demo planner's day: 24 May 2025
DELAY = F.READING_DELAY                              # 3 days
REPORT_LEADS = [1, 2, 3, 4, 5]

# ---- choices for the statistical models (ours; explained in MODEL_REPORT.md)
FIT_FROM = pd.Timestamp("2014-01-01")                # they are fitted on the last 10 training years (2014-2023)
WINDOW_DAYS = A.WINDOW_DAYS                          # readings a forecast looks back over (120 days)
FOURIER_CHOICES = range(1, 9)                        # how many yearly sine/cosine pairs to try
ARMA_CHOICES = [(1, 0, 0), (2, 0, 0), (3, 0, 0), (1, 0, 1), (2, 0, 1), (1, 0, 2), (2, 0, 2)]
REFERENCE_STATION = "ALER"                           # Madrid-Barrio Salamanca: orders are chosen on this station
QUANTILES = {"q10": 0.10, "q50": 0.50, "q90": 0.90}  # an 80% prediction interval and the median
MIN_GAIN, MIN_SEASON_DAYS = 0.05, 200                # the RELIABLE rule of train_pollen_model.py
INTERVAL = 0.80

EXAMPLES = [("Gramíneas", "ALER"), ("Plátano de paseo", "ALER"), ("Olivo", "ALER")]
MODEL_NAMES = {"naive": "Estacional ingenuo", "ets": "Suavizado exponencial", "arimax": "ARIMAX", "gb": "Gradient boosting"}
# APP DECISION: the app can only run ARIMAX and gradient boosting (they use the weather forecast and give
# prediction intervals). Seasonal naive and exponential smoothing are REFERENCES: they are scored and reported,
# but the model per pollen type is chosen between these two.
CANDIDATES = ("arimax", "gb")
IMPORTANCE_LEAD = 3                                  # the feature-importance chart is for forecasts 3 days ahead
FEATURE_NAMES = {                                    # the model's inputs, in Spanish, for the feature-importance chart
    "day_of_year": "Día del año", "day_sin": "Época del año (seno)", "day_cos": "Época del año (coseno)",
    "last_value": "Última lectura", "last_age": "Antigüedad de la última lectura", "mean_7": "Media de 7 días",
    "max_7": "Máximo de 7 días", "mean_14": "Media de 14 días", "trend": "Tendencia reciente",
    "network_mean_3": "Media de la red (3 días)", "same_day_last_year": "Misma semana del año anterior",
    "warmth_since_jan1": "Calor acumulado desde el 1 de enero (base 5 °C)", "warmth_since_jan1_base0": "Calor acumulado (base 0 °C)",
    "rain_7": "Lluvia de 7 días", "rain_14": "Lluvia de 14 días", "rain_30": "Lluvia de 30 días", "rain_90": "Lluvia de 90 días",
    "temp_mean_7": "Temperatura media de 7 días", "target_temp_mean": "Temperatura prevista (media)",
    "target_temp_max": "Temperatura prevista (máxima)", "target_rain": "Lluvia prevista", "target_wind": "Viento previsto",
    "target_humidity": "Humedad prevista", "between_rain": "Lluvia prevista hasta ese día",
    "between_temp_mean": "Temperatura prevista hasta ese día", "station_number": "Estación", "lead": "Días de antelación",
}


# ============================================================================
# 1. DATA (everything comes from model_data/, downloaded by train_pollen_model.py)
# ============================================================================

print("STEP 1: data")
history = pd.read_csv(CACHE / "pollen_history.csv.gz", parse_dates=["date"])
# the rows every model is scored on: made by train_pollen_model.py for 2024 (choose) and 2025 (test)
test_rows = pd.concat([pd.read_csv(CACHE / f"test_rows_{year}.csv.gz", parse_dates=["issue_date", "target_date"]) for year in EVAL_YEARS],
                      ignore_index=True)
test_rows["year"] = test_rows["target_date"].dt.year
meta = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))
station_number = meta["station_number"]
places = pd.read_csv(ROOT / "backup" / "pollen.csv").drop_duplicates("station_code").set_index("station_code", drop=False)
station_names = places["station"].to_dict()

all_days = pd.date_range("1993-01-01", history["date"].max(), freq="D")
wide = {}                                            # pollen type -> grains/m3 (index = every day, columns = stations)
for pollen_type in F.POLLEN_TYPES:
    one = history[history["pollen_type"] == pollen_type]
    wide[pollen_type] = one.pivot(index="date", columns="station_code", values="value").reindex(all_days)

weather, issued = {}, {}                             # per station: weather that happened / forecasts as issued in 2024 and 2025
for code in station_number:
    source = F.WEATHER_FROM.get(code, code)          # nearby stations share one weather series
    weather[code] = F.weather_features(pd.read_csv(CACHE / f"weather_{source}.csv", parse_dates=["date"])).set_index("date")
    by_year = {year: issued_weather.load(places.loc[source], year, weather[code]) for year in EVAL_YEARS}
    issued[code] = {}
    for k in range(1, 6):
        table = pd.concat([by_year[year][k] for year in EVAL_YEARS]).sort_index()
        # where there is no archived forecast (before 19 January 2024) the weather that happened stands in
        issued[code][k] = table.combine_first(weather[code][table.columns].reindex(table.index))

test_stations = sorted(test_rows["station_code"].unique())
print(f"  {len(history):,} readings, {len(test_stations)} stations, {len(test_rows):,} rows to score (2024 and 2025)")


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
        figure.suptitle(f"{name}: autocorrelación, {FIT_FROM.year}–{TRAIN_UNTIL.year}", fontsize=11.5, x=0.01, ha="left")
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
        figure.suptitle(f"{name}: descomposición STL, {FIT_FROM.year}–{TRAIN_UNTIL.year}", fontsize=11.5, x=0.01, ha="left")
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
    Fits exponential smoothing and ARIMAX on one station's series (2014-2023),
    then, for every issue day, forecasts with only the readings up to D-3.
    The parameters stay fixed during 2024 and 2025: nothing from those years is learnt.
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

    # the forecasts for 2024 (choosing and calibrating) and 2025 (test), with the weather forecasts as issued
    every = build(pollen_type, lead, DELAY, use_issued_forecasts=True)
    test = every[every["target_date"].dt.year.isin(EVAL_YEARS) & every["target_value"].notna()].copy()
    out = pd.concat([test[["station_code", "issue_date", "lead"]], predict(test)], axis=1)
    out["pollen_type"] = pollen_type

    # which inputs matter: shuffle one input at a time on the 2024 rows and see how much worse the forecast gets
    importance = None
    if lead == IMPORTANCE_LEAD:
        sample = test[(test["target_date"].dt.year == CHOOSE_YEAR) & (test["target_value"] > 0)]
        if len(sample) > 200:
            sample = sample.sample(min(len(sample), 3000), random_state=0)
            found = permutation_importance(fitted["mean"], sample[F.FEATURES], np.log1p(sample["target_value"]),
                                           scoring="neg_mean_absolute_error", n_repeats=3, random_state=0)
            importance = dict(zip(F.FEATURES, found.importances_mean))

    # the forecast as of the demo day (24 May 2025), with only what was known then
    demo = every[(every["issue_date"] == CHECK_DAY) & every["last_value"].notna()].copy()
    demo = pd.concat([demo[["station_code", "lead"]], predict(demo)], axis=1)
    demo["pollen_type"] = pollen_type
    return (pollen_type, lead), fitted, out, demo, importance


def conformal_margin(low, high, actual):
    """
    Split conformal calibration (conformalized quantile regression): how much
    the 80% interval must be widened (or narrowed, if negative) so that it
    would have covered 80% of the calibration year. The score of each day is
    how far outside the interval the real value fell (negative if inside).
    """
    scores = np.maximum(low - actual, actual - high)
    n = len(scores)
    if n < 30:
        return 0.0
    return float(np.quantile(scores, min(1.0, np.ceil((n + 1) * INTERVAL) / n)))


# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    diagnostics_found = diagnostics()

    print("\nSTEP 3: statistical models (orders chosen by AIC, fitted on 2014-2023, forecasts for 2024 and 2025)")
    stat_cache = CACHE / "stat_backtest.csv.gz"
    fits_cache = CACHE / "stat_fits.json"
    if stat_cache.exists() and fits_cache.exists():
        print("  (using the saved results in model_data/; delete stat_backtest.csv.gz to redo them)")
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

    print("\nSTEP 4: gradient boosting with quantile regression (trained up to 2023)")
    done = Parallel(n_jobs=-1, verbose=5)(delayed(train_quantiles)(t, lead) for t in F.POLLEN_TYPES for lead in F.LEADS)
    quantile_models = {key: fitted for key, fitted, *_ in done}
    gb = pd.concat([d[2] for d in done], ignore_index=True)
    demo_quantiles = pd.concat([d[3] for d in done], ignore_index=True)
    importances = {d[0][0]: d[4] for d in done if d[4] is not None}        # {pollen type: {feature: importance}}
    joblib.dump(quantile_models, OUT / "quantile_models.joblib", compress=3)
    print(f"  saved quantile_models.joblib ({(OUT / 'quantile_models.joblib').stat().st_size / 1e6:.1f} MB)")

    # ------------------------------------------------------------------------
    # 5. THE COMPARISON: same rows, same lead days, same metrics for everyone
    # ------------------------------------------------------------------------
    print("\nSTEP 5: comparison (choose on 2024, test on 2025)")
    keys = ["station_code", "pollen_type", "issue_date", "lead"]
    rows = test_rows.merge(stat, on=keys, how="inner").merge(gb, on=keys, how="inner")
    print(f"  {len(rows):,} of {len(test_rows):,} rows have a forecast from every model")
    rows["actual"] = np.log1p(rows["target_value"])
    rows["gb"] = rows["gb_q50"]                           # gradient boosting's forecast = its median
    # the same rule as the app: an unstable ARIMAX forecast (see A.MAX_LOG_ERROR) is replaced by gradient boosting
    unstable = ~(rows["arimax_se"] <= A.MAX_LOG_ERROR)
    rows.loc[unstable, ["arimax", "arimax_q10", "arimax_q90"]] = rows.loc[unstable, ["gb", "gb_q10", "gb_q90"]].to_numpy()
    print(f"  {int(unstable.sum()):,} unstable ARIMAX forecasts replaced by gradient boosting (as in the app)")
    for pollen_type, index in rows.groupby("pollen_type").groups.items():
        for name in MODEL_NAMES:                          # every model's level = its forecast read against the official thresholds
            rows.loc[index, f"{name}_level"] = F.level_of(pollen_type, np.expm1(rows.loc[index, name]))
    rows["classifier_level"] = rows["predicted"]          # the old classifier, kept only to compare with the median
    rows.to_csv(CACHE / "comparison_rows.csv.gz", index=False)

    def app_level(series):                                # bajo / medio / alto-or-more: the three levels the app acts on
        return series.clip(upper=2)

    results = []
    for (pollen_type, lead, year), group in rows.groupby(["pollen_type", "lead", "year"], sort=False):
        season = group[group["in_season"]]
        entry = {"pollen_type": pollen_type, "lead": lead, "year": year, "season_days": len(season), "all_days": len(group)}
        real = season["target_level"]
        for name in list(MODEL_NAMES) + ["classifier"]:
            entry[f"{name}_acc"] = (season[f"{name}_level"] == real).mean()                         # exact official level
            entry[f"{name}_acc_app"] = (app_level(season[f"{name}_level"]) == app_level(real)).mean()
            entry[f"{name}_err_app"] = (app_level(season[f"{name}_level"]) - app_level(real)).abs().mean()
            if name in MODEL_NAMES:
                entry[f"{name}_mae"] = (season[name] - season["actual"]).abs().mean()               # log scale, in season
        entry["baseline_acc_app"] = (app_level(season["baseline"]) == app_level(real)).mean()      # last reading adjusted for the season
        entry["baseline_err_app"] = (app_level(season["baseline"]) - app_level(real)).abs().mean()
        results.append(entry)
    results = pd.DataFrame(results)

    # ---- CHOOSE on 2024: per pollen type, the highest accuracy on the official level in season (lead days 1-5);
    #      models within one point of each other are separated by the lower log error
    report = results[results["lead"].isin(REPORT_LEADS)]
    metric_columns = [c for c in results.columns if c.endswith(("_mae", "_acc", "_acc_app"))]
    summary = {year: report[report["year"] == year].groupby("pollen_type", sort=False)[metric_columns].mean() for year in EVAL_YEARS}
    choice = {}
    for pollen_type, r in summary[CHOOSE_YEAR].iterrows():
        best_accuracy = max(r[f"{m}_acc"] for m in CANDIDATES)
        close = [m for m in CANDIDATES if r[f"{m}_acc"] >= best_accuracy - 0.01]
        choice[pollen_type] = min(close, key=lambda m: r[f"{m}_mae"])
        t = summary[TEST_YEAR].loc[pollen_type]
        print(f"  {pollen_type:22s} 2024: " + "  ".join(f"{m} {r[f'{m}_acc']:.0%}/{r[f'{m}_mae']:.2f}" for m in MODEL_NAMES)
              + f"  -> {choice[pollen_type]}   | 2025: " + "  ".join(f"{m} {t[f'{m}_acc']:.0%}" for m in MODEL_NAMES))

    # ---- CALIBRATE the 80% intervals on 2024 (in season), then measure their coverage on 2025
    margins, coverage = {}, []
    for (pollen_type, lead), group in rows.groupby(["pollen_type", "lead"], sort=False):
        calibration = group[(group["year"] == CHOOSE_YEAR) & group["in_season"]]
        test = group[(group["year"] == TEST_YEAR) & group["in_season"]]
        for name in ("gb", "arimax"):
            margin = conformal_margin(calibration[f"{name}_q10"], calibration[f"{name}_q90"], calibration["actual"])
            margins[(pollen_type, lead, name)] = margin
            low, high = test[f"{name}_q10"], test[f"{name}_q90"]
            wide_low, wide_high = np.minimum((low - margin).clip(lower=0), test[name]), np.maximum(high + margin, test[name])
            coverage.append({"pollen_type": pollen_type, "lead": lead, "model": name, "margin": margin,
                             "before": ((test["actual"] >= low) & (test["actual"] <= high)).mean(),
                             "after": ((test["actual"] >= wide_low) & (test["actual"] <= wide_high)).mean(),
                             "width_before": (high - low).mean(), "width_after": (wide_high - wide_low).mean()})
    coverage = pd.DataFrame(coverage)

    # ---- RELIABLE (decided on 2024, same rule as before): on in-season days the model gets the level the app acts on
    #      right at least 5 points more often than the baseline, with a smaller mean error, on at least 200 station-days
    flags = []
    for _, r in results[results["year"] == CHOOSE_YEAR].iterrows():
        test = results[(results["year"] == TEST_YEAR) & (results["pollen_type"] == r["pollen_type"]) & (results["lead"] == r["lead"])].iloc[0]
        for name in ("gb", "arimax"):
            reliable = bool(r[f"{name}_acc_app"] - r["baseline_acc_app"] >= MIN_GAIN and r[f"{name}_err_app"] < r["baseline_err_app"]
                            and r["season_days"] >= MIN_SEASON_DAYS)
            flags.append({"pollen_type": r["pollen_type"], "lead": int(r["lead"]), "model": name, "reliable": reliable,
                          "accuracy_2024": round(float(r[f"{name}_acc_app"]), 4), "baseline_2024": round(float(r["baseline_acc_app"]), 4),
                          "accuracy_2025": round(float(test[f"{name}_acc_app"]), 4), "baseline_2025": round(float(test["baseline_acc_app"]), 4),
                          "margin": round(margins[(r["pollen_type"], int(r["lead"]), name)], 4)})
    flags = pd.DataFrame(flags)
    chosen_flags = flags[[choice[t] == m for t, m in zip(flags["pollen_type"], flags["model"])]]
    used = chosen_flags[chosen_flags["lead"].isin(REPORT_LEADS)]
    print(f"  RELIABLE (decided on 2024): {int(used['reliable'].sum())} of {len(used)} type + lead-day combinations of the chosen models")

    results.to_csv(OUT / "comparison.csv", index=False)
    coverage.to_csv(OUT / "interval_coverage.csv", index=False)

    arimax_types = [t for t, m in choice.items() if m == "arimax"]
    by_series = {(f["pollen_type"], f["station_code"]): f for f in fits}
    (OUT / "arimax_params.json").write_text(json.dumps({
        "made": pd.Timestamp.now().strftime("%Y-%m-%d"), "fitted_on": f"{FIT_FROM:%Y-%m-%d} to {TRAIN_UNTIL:%Y-%m-%d}",
        "models": {t: {code: {"order": by_series[(t, code)]["order"], "fourier_pairs": by_series[(t, code)]["fourier_pairs"],
                              "params": by_series[(t, code)]["arimax_params"]}
                       for code in test_stations if (t, code) in by_series} for t in arimax_types},
    }, ensure_ascii=False), encoding="utf-8")
    (OUT / "model_choice.json").write_text(json.dumps({
        "made": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "split": f"trained up to {TRAIN_UNTIL.year}, chosen and calibrated on {CHOOSE_YEAR}, tested on {TEST_YEAR}",
        "rule": f"per pollen type, between ARIMAX and gradient boosting (the models the app can run), the one with the highest accuracy on the official level in season ({CHOOSE_YEAR}, lead days 1-5); "
                "models within one percentage point are separated by the lower mean absolute error on the log scale",
        "reliable_rule": f"decided on {CHOOSE_YEAR}: on in-season days the model gets the app's level (bajo / medio / alto-or-more) right at "
                         f"least {MIN_GAIN * 100:.0f} points more often than the baseline, with a smaller mean level error, on at least "
                         f"{MIN_SEASON_DAYS} station-days",
        "choice": choice, "interval": INTERVAL,
        "results": flags.to_dict("records"),
        "summary": {str(year): {t: {k: round(float(v), 4) for k, v in r.items()} for t, r in table.iterrows()} for year, table in summary.items()},
        "orders": {o["pollen_type"]: {"fourier_pairs": o["fourier_pairs"], "order": o["order"]} for o in orders},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- Markdown tables for MODEL_REPORT.md
    order = list(MODEL_NAMES)
    lines = []
    for year, title in ((CHOOSE_YEAR, "año de elección"), (TEST_YEAR, "año de prueba, no usado para decidir")):
        lines += [f"**{year} ({title})**", "",
                  "| Tipo de polen | " + " | ".join(f"{MODEL_NAMES[m]}: acierto / MAE" for m in order) + " | Elegido |", "|---|" + "---|" * (len(order) + 1)]
        for pollen_type, r in summary[year].iterrows():
            cells = [f"{'**' if m == choice[pollen_type] else ''}{r[f'{m}_acc']:.0%} / {r[f'{m}_mae']:.2f}{'**' if m == choice[pollen_type] else ''}" for m in order]
            lines.append(f"| {config.POLLEN_NAMES[pollen_type]} | " + " | ".join(cells) + f" | {MODEL_NAMES[choice[pollen_type]]} |")
        mean = summary[year].mean()
        picked = np.mean([summary[year].loc[t, f"{choice[t]}_acc"] for t in choice])
        lines += ["| **Media** | " + " | ".join(f"{mean[f'{m}_acc']:.0%} / {mean[f'{m}_mae']:.2f}" for m in order) + f" | {picked:.0%} con el elegido |", ""]
    lines += [f"**{TEST_YEAR}, por día de antelación (en negrita, el mejor acierto de cada fila)**", "",
              "| Tipo de polen | Día | " + " | ".join(f"{MODEL_NAMES[m]}: acierto / MAE" for m in order) + " | Días de temporada |",
              "|---|---|" + "---|" * (len(order) + 1)]
    for _, r in report[report["year"] == TEST_YEAR].iterrows():
        best = max(order, key=lambda m: r[f"{m}_acc"])
        cells = [f"{'**' if m == best else ''}{r[f'{m}_acc']:.0%} / {r[f'{m}_mae']:.2f}{'**' if m == best else ''}" for m in order]
        lines.append(f"| {config.POLLEN_NAMES[r['pollen_type']]} | +{r['lead']} | " + " | ".join(cells) + f" | {r['season_days']} |")
    chosen_coverage = coverage[coverage["lead"].isin(REPORT_LEADS)].groupby(["pollen_type", "model"], sort=False)[["before", "after", "width_before", "width_after"]].mean()
    lines += ["", f"**Intervalos del 80 %: cobertura real en {TEST_YEAR} (temporada), antes y después de calibrarlos con {CHOOSE_YEAR}**", "",
              "| Tipo de polen | Modelo usado | Antes | Después | Anchura media (log): antes → después | Fourier (pares) | Orden ARMA |", "|---|---|---|---|---|---|---|"]
    for o in orders:
        t = o["pollen_type"]
        c = chosen_coverage.loc[(t, choice[t])]
        lines.append(f"| {config.POLLEN_NAMES[t]} | {MODEL_NAMES[choice[t]]} | {c['before']:.0%} | {c['after']:.0%} | "
                     f"{c['width_before']:.2f} → {c['width_after']:.2f} | {o['fourier_pairs']} | {tuple(o['order'])} |")
    both = coverage[coverage["lead"].isin(REPORT_LEADS)].groupby("model")[["before", "after"]].mean()
    used_cover = np.mean([chosen_coverage.loc[(t, choice[t]), "after"] for t in choice]), np.mean([chosen_coverage.loc[(t, choice[t]), "before"] for t in choice])
    lines += [f"| **Media (modelo usado)** | | {used_cover[1]:.0%} | {used_cover[0]:.0%} | | | |",
              f"| Media gradient boosting (10 tipos) | | {both.loc['gb', 'before']:.0%} | {both.loc['gb', 'after']:.0%} | | | |",
              f"| Media ARIMAX (10 tipos) | | {both.loc['arimax', 'before']:.0%} | {both.loc['arimax', 'after']:.0%} | | | |", ""]
    median_vs_classifier = report[report["year"] == TEST_YEAR][["gb_acc", "classifier_acc", "gb_acc_app", "classifier_acc_app"]].mean()
    lines += [f"**Nivel leído de la mediana frente al clasificador antiguo ({TEST_YEAR}, temporada, media de 10 tipos):** nivel exacto "
              f"{median_vs_classifier['gb_acc']:.1%} (mediana) frente a {median_vs_classifier['classifier_acc']:.1%} (clasificador); "
              f"tres niveles de la aplicación {median_vs_classifier['gb_acc_app']:.1%} frente a {median_vs_classifier['classifier_acc_app']:.1%}.", ""]
    lines += ["| Serie | ADF (serie) | p | ADF (sin ciclo anual) | p | Días con cero | Fuerza estacional (STL) |", "|---|---|---|---|---|---|---|"]
    for d in diagnostics_found:
        lines.append(f"| {d['series']} | {d['adf_stat']:.1f} | {d['adf_p']:.4f} | {d['adf_resid_stat']:.1f} | {d['adf_resid_p']:.4f} | {d['share_zero']:.0%} | {d['seasonal_strength']:.2f} |")
    (OUT / "comparison_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- charts
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colours = {"naive": "#9a9a92", "ets": "#e0902b", "arimax": "#7b4fc4", "gb": "#2a78d6"}
    by_lead = report[report["year"] == TEST_YEAR].groupby("lead")[[f"{m}_{k}" for m in MODEL_NAMES for k in ("mae", "acc")]].mean()
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
    figure.suptitle(f"Comparación de modelos en {TEST_YEAR}, año de prueba (media de los 10 tipos de polen)", fontsize=11.5, x=0.01, ha="left")
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    figure.savefig(PLOTS / "comparison_by_lead.png", dpi=120, facecolor="white")

    # forecasts 3 days ahead against reality, grass at Barrio de Salamanca, spring 2025, with the calibrated intervals
    example = rows[(rows["pollen_type"] == "Gramíneas") & (rows["station_code"] == "ALER") & (rows["lead"] == 3)
                   & (rows["target_date"] >= f"{TEST_YEAR}-04-01") & (rows["target_date"] <= f"{TEST_YEAR}-07-15")].sort_values("target_date")
    figure, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, sharey=True)
    for axis, m in zip(axes, ("arimax", "gb")):
        margin = margins[("Gramíneas", 3, m)]
        axis.fill_between(example["target_date"], np.expm1((example[f"{m}_q10"] - margin).clip(lower=0)), np.expm1(example[f"{m}_q90"] + margin),
                          color=colours[m], alpha=0.18, label="Intervalo del 80 % (calibrado)")
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

    # feature importance of gradient boosting (average of the 10 pollen types, forecasts 3 days ahead)
    importance = pd.DataFrame(importances).mean(axis=1).sort_values()
    importance = importance[importance > 0].tail(14)
    figure, axis = plt.subplots(figsize=(8.5, 5))
    axis.barh([FEATURE_NAMES.get(name, name) for name in importance.index], importance, color="#2a78d6", height=0.6)
    axis.set_xlabel("Cuánto empeora el error (escala log) si se desordena esa variable", fontsize=9)
    axis.tick_params(labelsize=9)
    axis.grid(axis="x", color="#e1e0d9", linewidth=0.6)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    figure.suptitle(f"Qué variables pesan más en el gradient boosting (previsión a {IMPORTANCE_LEAD} días, media de los 10 tipos, {CHOOSE_YEAR})",
                    fontsize=10.5, x=0.01, ha="left")
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    figure.savefig(PLOTS / "feature_importance.png", dpi=120, facecolor="white")
    pd.DataFrame(importances).to_csv(OUT / "feature_importance.csv")

    # ------------------------------------------------------------------------
    # 6. The demo-day forecast (24 May 2025): the chosen model's median, its level and its calibrated interval
    # ------------------------------------------------------------------------
    demo_file = ROOT / "backup" / "replay" / f"pollen_forecast_{CHECK_DAY:%Y-%m-%d}.csv"
    demo = pd.read_csv(demo_file).drop(columns=["q10", "q50", "q90", "model", "accuracy"], errors="ignore")
    demo = demo.merge(demo_quantiles, on=["station_code", "pollen_type", "lead"], how="left")
    flag_of = {(r["pollen_type"], r["lead"], r["model"]): r for r in flags.to_dict("records")}
    that_day = stat[stat["issue_date"] == CHECK_DAY].set_index(["station_code", "pollen_type", "lead"])
    out = []
    for _, row in demo.iterrows():
        model = choice[row["pollen_type"]]
        key = (row["station_code"], row["pollen_type"], row["lead"])
        if model == "arimax" and key in that_day.index and that_day.loc[key, "arimax_se"] <= A.MAX_LOG_ERROR:
            low, middle, high = that_day.loc[key, ["arimax_q10", "arimax", "arimax_q90"]]
        else:
            model = "gb"
            low, middle, high = row["gb_q10"], row["gb_q50"], row["gb_q90"]
        flag = flag_of[(row["pollen_type"], row["lead"], model)]
        low, high = min(max(low - flag["margin"], 0), middle), max(high + flag["margin"], middle)
        out.append({"model": model, "q10": round(float(np.expm1(low)), 1), "q50": round(float(np.expm1(middle)), 1),
                    "q90": round(float(np.expm1(high)), 1), "reliable": flag["reliable"], "accuracy": flag["accuracy_2025"],
                    "predicted_level": int(F.level_of(row["pollen_type"], [np.expm1(middle)])[0])})
    demo = pd.concat([demo.drop(columns=["gb_q10", "gb_q50", "gb_q90", "reliable", "predicted_level", "prob_alto_or_more"], errors="ignore"),
                      pd.DataFrame(out)], axis=1)
    demo.to_csv(demo_file, index=False)

    # ---- the readings that had been published by the demo day (up to 3 days before it), for the app's chart
    recent = history[(history["date"] >= CHECK_DAY - pd.Timedelta(days=16)) & (history["date"] <= CHECK_DAY - pd.Timedelta(days=DELAY))]
    recent = recent.rename(columns={"date": "timestamp"})
    recent["station"] = recent["station_code"].map(station_names)
    recent.to_csv(ROOT / "backup" / "replay" / f"pollen_recent_{CHECK_DAY:%Y-%m-%d}.csv", index=False)
    print(f"\nSaved: comparison.csv, interval_coverage.csv, comparison_tables.md, model_choice.json, arimax_params.json, plots/, and {demo_file.name}")
