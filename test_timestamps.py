"""
test_timestamps.py - checks that no reading is ever stamped in the future.

Run it with:   .venv/bin/python test_timestamps.py

Part 1 uses a made-up file to prove the rules of the hourly columns:
  - h24 is midnight at the END of the day (00:00 of the next day),
  - hours that have not happened yet are ignored even if flagged valid,
  - readings flagged N are never used.
Part 2 checks the real tables the app shows right now.
"""

import logging

import pandas as pd
import streamlit  # noqa: F401

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import config  # noqa: E402
import data    # noqa: E402
import risk    # noqa: E402

failures = []


def check(description, condition):
    print(f"  {'OK  ' if condition else 'FAIL'}  {description}")
    if not condition:
        failures.append(description)


def fake_row(day, values_and_flags):
    """One row of an hourly file: station 28079038 (Cuatro Caminos), NO2."""
    row = {"provincia": "28", "municipio": "079", "estacion": "38", "magnitud": "8",
           "ano": f"{day.year}", "mes": f"{day.month:02d}", "dia": f"{day.day:02d}"}
    for hour in range(1, 25):
        value, flag = values_and_flags.get(hour, ("0", "N"))
        row[f"h{hour:02d}"], row[f"v{hour:02d}"] = value, flag
    return row


print("PART 1: rules of the hourly columns (made-up file)")
day = pd.Timestamp("2026-11-15")
now = pd.Timestamp("2026-11-15 18:05")

# Hours 1-18 are real. Hour 19 and hour 24 are wrongly flagged V (the bug).
hours = {h: (str(10 + h), "V") for h in range(1, 19)}
hours[19] = ("99", "V")
hours[24] = ("77", "V")
table = pd.DataFrame([fake_row(day, hours)])

long = data._hourly_long(table, data.POLLUTANTS, now=now)
latest = data._latest_hourly(long, config.ICA_AVERAGING_HOURS).iloc[0]
check("no reading later than 'now' survives", long["timestamp"].max() <= now)
check("the latest reading is 18:00 with value 28 (not the future 19:00 or 24:00)",
      latest["timestamp"] == pd.Timestamp("2026-11-15 18:00") and latest["value"] == 28)
check("h01 is 01:00 of the same day", long["timestamp"].min() == pd.Timestamp("2026-11-15 01:00"))

after_midnight = data._hourly_long(table, data.POLLUTANTS, now=pd.Timestamp("2026-11-16 00:30"))
check("h24 becomes 00:00 of the NEXT day, once that moment has passed",
      after_midnight["timestamp"].max() == pd.Timestamp("2026-11-16 00:00"))

not_valid = pd.DataFrame([fake_row(day, {5: ("50", "V"), 6: ("999", "N"), 7: ("60", "T")})])
kept = data._hourly_long(not_valid, data.POLLUTANTS, now=now)
check("readings flagged N are dropped; V and T are kept", sorted(kept["value"]) == [50, 60])


print("\nPART 2: the real tables, right now")
now = risk.madrid_now()
print(f"  (Madrid time: {now:%Y-%m-%d %H:%M})")

for name, loader in (("Comunidad air", data.load_comunidad_air), ("Ayuntamiento air", data.load_city_air),
                     ("merged air", data.load_air), ("Comunidad weather", data.load_weather)):
    try:
        table = loader()
        check(f"{name}: latest reading {table['timestamp'].max():%d/%m %H:%M} is not in the future",
              table["timestamp"].max() <= now)
        check(f"{name}: download time is not in the future", table.attrs["fetched_at"] <= now + pd.Timedelta(minutes=1))
    except Exception as error:
        print(f"  SKIP  {name}: no data available ({error})")

pollen = data.load_pollen_all()
check(f"pollen: latest reading day {pollen['timestamp'].max():%d/%m/%Y} is not after today",
      pollen["timestamp"].max() <= now.normalize())

stations = risk.station_categories(data.load_air(), risk.madrid_now())
check("map popups: no station shows a time in the future", stations["timestamp"].max() <= now)

for place, (lat, lon) in config.PLACES.items():
    result = risk.assess(lat, lon, "general", (), now)
    times = [r["timestamp"] for r in result["air"]]
    check(f"tiles and card for {place}: every reading is at or before now", all(t <= now for t in times))

demo_now = pd.Timestamp(config.REPLAY_DATE) + pd.Timedelta(hours=config.REPLAY_DEFAULT_HOUR)
demo_air = data.load_replay_air(demo_now)
check("demo mode: no reading later than the demo hour", demo_air["timestamp"].max() <= demo_now)

print("\nALL CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED")
raise SystemExit(1 if failures else 0)
