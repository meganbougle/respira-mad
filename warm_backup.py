"""
warm_backup.py - refreshes every backup file, so the app keeps working if a
live feed fails during the demo.

Run it shortly before the demo, then commit the backup/ folder:
    .venv/bin/python warm_backup.py

It downloads every live source, and the Open-Meteo WEATHER forecast for every place
in the app's dropdown and every station (for today, and for the demo days), in batches.
"""

import logging
import sys

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

# our own pollen and air forecasts: computed exactly as GitHub Actions does (precompute.py),
# then copied into backup/ (with backup/results_status.json), the copies committed on main
# that the server uses if it cannot reach the results branch. Needs requirements-precompute.txt.
import subprocess  # noqa: E402
import tempfile    # noqa: E402
with tempfile.TemporaryDirectory() as folder:
    run = subprocess.run([sys.executable, "precompute.py", "all", "--out", folder, "--also-backup"],
                         capture_output=True, text=True)
    for line in run.stdout.splitlines():
        print(f"Modelos: {line}")
    if run.returncode != 0:
        problems += 1
        print("Modelos: FAILED - kept the old backup for the part that failed")

# the Open-Meteo WEATHER (weather only: air and pollen forecasts are our own models), in batched
# requests (weather.py): the dropdown places and every station, for today and for the two demo days
import weather  # noqa: E402
places = risk.list_places(data.load_air())
points = risk.weather_points()
for day, label in ((None, "hoy"), (config.REPLAY_DATE, "día de demostración"),
                   (config.PLANNER_DEMO_DATE, "planificador de demostración")):
    asked, done = weather.prefetch(points, day, force=day is None)
    ok = done == asked
    problems += 0 if ok else 1
    print(f"Tiempo ({label}): {done} de {asked} celdas descargadas" + ("" if ok else " - SOME FAILED"))

print(f"\n{len(places)} places. " + ("All backups refreshed." if problems == 0 else f"{problems} problems - see above."))
