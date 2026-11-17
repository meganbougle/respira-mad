"""
warm_backup.py - refreshes every backup file, so the app keeps working if a
live feed fails during the demo.

Run it shortly before the demo, then commit the backup/ folder:
    .venv/bin/python warm_backup.py

It downloads every live source, and the Open-Meteo WEATHER forecast for every place
in the app's dropdown (for today, and for the demo day).
"""

import logging

import streamlit  # noqa: F401

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import config  # noqa: E402
import data    # noqa: E402
import risk    # noqa: E402

problems = 0
for name, loader in (("Aire (ambas redes)", data.load_air), ("Polen", data.load_pollen_all), ("Meteorología", data.load_weather)):
    table = loader()
    live = table.attrs.get("live")
    problems += 0 if live else 1
    print(f"{name}: {'LIVE, backup saved' if live else 'FAILED - kept the old backup'}")

# our own pollen model's forecast (needs recent readings and each station's weather)
try:
    from pollen_model import predict
    live = predict.forecast().attrs.get("live")
    live = live and predict.recent_readings().attrs.get("live")      # the last two weeks of readings, for the chart
    problems += 0 if live else 1
    print(f"Modelo de polen: {'LIVE, backup saved' if live else 'FAILED - kept the old backup'}")
except Exception as error:
    problems += 1
    print(f"Modelo de polen: FAILED ({error})")

# our own air-quality forecast (needs the last days of readings of both networks and the weather)
try:
    from air_model import predict as air_predict
    live = air_predict.forecast().attrs.get("live")
    problems += 0 if live else 1
    print(f"Modelo de aire: {'LIVE, backup saved' if live else 'FAILED - kept the old backup'}")
except Exception as error:
    problems += 1
    print(f"Modelo de aire: FAILED ({error})")

places = risk.list_places(data.load_air())
# (day, number of days): the main view (today, demo day) and the 5-day planner (today, demo planner day)
planner_days = config.PLANNER_DAYS + 1          # one extra day for the last night's madrugada
requests_needed = [(None, 2), (config.REPLAY_DATE, 2), (None, planner_days), (config.PLANNER_DEMO_DATE, planner_days)]
for name, (lat, lon) in places.items():
    status = []
    for day, days in requests_needed:
        try:                                         # weather only: air and pollen forecasts are our own models
            status.append(data.load_forecast_weather(round(lat, 2), round(lon, 2), day, days).attrs.get("live"))
        except Exception:
            status.append(False)
    problems += 0 if all(status) else 1
    print(f"Forecast {name}: {'OK' if all(status) else 'SOME FAILED'}")

print(f"\n{len(places)} places. " + ("All backups refreshed." if problems == 0 else f"{problems} problems - see above."))
