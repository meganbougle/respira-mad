"""
warm_backup.py - refreshes every backup file, so the app keeps working if a
live feed fails during the demo.

Run it shortly before the demo, then commit the backup/ folder:
    .venv/bin/python warm_backup.py

It downloads every live source, and the Open-Meteo forecast for every place
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

places = risk.list_places(data.load_air())
for name, (lat, lon) in places.items():
    status = []
    for day in (None, config.REPLAY_DATE):
        for loader in (data.load_forecast_air, data.load_forecast_weather):
            try:
                status.append(loader(round(lat, 2), round(lon, 2), day).attrs.get("live"))
            except Exception:
                status.append(False)
    problems += 0 if all(status) else 1
    print(f"Forecast {name}: {'OK' if all(status) else 'SOME FAILED'}")

print(f"\n{len(places)} places. " + ("All backups refreshed." if problems == 0 else f"{problems} problems - see above."))
