"""
pollen_model/arimax.py - the ARIMAX model (dynamic harmonic regression).

    log(1 + grains)  =  yearly cycle (Fourier terms)  +  weather of the day
                        +  ARMA errors (what the last readings add)

The same code is used by the comparison (compare_models.py) and by the app
(pollen_model/predict.py), so both make forecasts in exactly the same way.
The parameters are fitted once, on 2015-2024, by compare_models.py and stored
in pollen_model/arimax_params.json. Needs statsmodels.
"""

import numpy as np
import pandas as pd

WINDOW_DAYS = 120                   # how many days of readings a forecast looks back over
WEATHER = ["temp_mean", "rain", "wind", "humidity"]
Z80 = 1.2816                        # 10% and 90% points of a normal distribution (80% interval)


def exog_table(days, weather_table, fourier_pairs):
    """
    The regressors for some days: `fourier_pairs` sine/cosine pairs for the
    yearly cycle, and that day's weather (temperature, rain, wind, humidity).
    `weather_table` must have one row per day of `days`, in the same order.
    """
    day_of_year = days.day_of_year.to_numpy()
    columns = {}
    for k in range(1, fourier_pairs + 1):
        columns[f"sin{k}"] = np.sin(2 * np.pi * k * day_of_year / 365.25)
        columns[f"cos{k}"] = np.cos(2 * np.pi * k * day_of_year / 365.25)
    table = pd.DataFrame(columns, index=days)
    table["temp_mean"] = weather_table["temp_mean"].to_numpy(dtype=float)
    table["rain_log"] = np.log1p(weather_table["rain"].to_numpy(dtype=float))
    table["wind"] = weather_table["wind"].to_numpy(dtype=float)
    table["humidity"] = weather_table["humidity"].to_numpy(dtype=float)
    return table.ffill().bfill()


def forecast(window, window_weather, future_weather, order, fourier_pairs, params):
    """
    Forecast the days after the last day of `window`.

    window          log(1 + grains) for the last WINDOW_DAYS days (index = days, NaN = no reading)
    window_weather  the weather of those days
    future_weather  the weather (observed or forecast) of the days to predict (index = days)
    order, fourier_pairs, params   the fitted model (see arimax_params.json)
    Returns (mean, standard error) on the log scale, one value per future day.
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    model = SARIMAX(window, exog=exog_table(window.index, window_weather, fourier_pairs), order=tuple(order), trend="c")
    result = model.smooth(params).get_forecast(len(future_weather), exog=exog_table(future_weather.index, future_weather, fourier_pairs))
    return result.predicted_mean.to_numpy(), result.se_mean.to_numpy()


def quantiles(mean, error):
    """The 10%, 50% and 90% points on the log scale (never below zero)."""
    return max(mean - Z80 * error, 0.0), max(mean, 0.0), max(mean + Z80 * error, 0.0)
