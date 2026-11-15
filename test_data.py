"""
test_data.py - quick check that every data source is live.

Run it with:   python test_data.py

For each source it prints the latest timestamp, how old that is, the number
of stations, and whether the data came from the LIVE feed or from BACKUP.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit  # noqa: F401  (imported first so we can quiet it below)

# Streamlit prints harmless "No runtime found" warnings when its cache is used
# outside "streamlit run"; hide them.
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import data  # noqa: E402  (imported after the logging line on purpose)

# All timestamps in the feeds are Madrid local time.
now = datetime.now(ZoneInfo("Europe/Madrid")).replace(tzinfo=None)
print(f"Time in Madrid now: {now:%Y-%m-%d %H:%M}\n")


def report(title, loader, what_column):
    """Load one source and print a short summary."""
    print(f"=== {title} ===")
    try:
        table = loader()
    except Exception as error:
        print(f"  FAILED: {error}\n")
        return False

    latest = table["timestamp"].max()
    age_hours = (now - latest).total_seconds() / 3600
    status = "LIVE" if table.attrs.get("live") else "BACKUP (live feed failed: " + table.attrs.get("error", "?") + ")"

    print(f"  status:           {status}")
    print(f"  latest timestamp: {latest:%Y-%m-%d %H:%M}  ({age_hours:.1f} hours ago)")
    print(f"  oldest 'latest':  {table['timestamp'].min():%Y-%m-%d %H:%M}")
    print(f"  stations:         {table['station'].nunique()}")
    print(f"  rows:             {len(table)}")
    print(f"  missing lat/lon:  {int(table['lat'].isna().sum())}")
    print(f"  readings per {what_column}:")
    for name, count in table[what_column].value_counts().items():
        print(f"      {name}: {count}")
    print()
    return True


results = [
    report("1. Comunidad de Madrid air quality", data.load_comunidad_air, "pollutant"),
    report("2. Ayuntamiento de Madrid air quality", data.load_city_air, "pollutant"),
    report("   Merged air table (both networks)", data.load_air, "source"),
    report("4. Weather (Comunidad air network)", data.load_weather, "variable"),
]

# Pollen has 27 pollen types, so print a shorter summary for it.
print("=== 3. Pollen (Comunidad de Madrid) ===")
try:
    everything = data.load_pollen_all()          # every station, however old
    pollen = data.load_pollen()                  # only readings from the last 7 days
    latest = pollen["timestamp"].max()
    print(f"  status:           {'LIVE' if pollen.attrs.get('live') else 'BACKUP'}")
    print(f"  latest timestamp: {latest:%Y-%m-%d}  ({(now - latest).days} days ago)")
    print(f"  stations kept:    {pollen['station'].nunique()} of {everything['station'].nunique()}"
          f"  (readings older than {data.config.POLLEN_MAX_AGE_DAYS} days are ignored)")
    print(f"  pollen types:     {pollen['pollen_type'].nunique()}")
    print(f"  rows:             {len(pollen)}")
    print("  latest reading per station:")
    kept = set(pollen["station"])
    for station, day in everything.groupby("station")["timestamp"].max().items():
        print(f"      {station}: {day:%Y-%m-%d}  {'kept' if station in kept else 'IGNORED (too old)'}")
    results.append(True)
except Exception as error:
    print(f"  FAILED: {error}")
    results.append(False)

# Forecasts (Open-Meteo model) for one test point: central Madrid.
print("\n=== 5. FORECAST (model) - Open-Meteo, central Madrid ===")
for title, loader in (("weather", data.load_forecast_weather), ("air + pollen", data.load_forecast_air)):
    try:
        forecast = loader(40.42, -3.70)
        columns = [c for c in forecast.columns if c not in ("timestamp", "lat", "lon", "source", "kind")]
        print(f"  {title}: {'LIVE' if forecast.attrs.get('live') else 'BACKUP'}, {len(forecast)} hours, "
              f"{forecast['timestamp'].min():%d %b %H:%M} to {forecast['timestamp'].max():%d %b %H:%M}, "
              f"label '{forecast['kind'].iloc[0]}'")
        print(f"      columns: {', '.join(columns)}")
        results.append(True)
    except Exception as error:
        print(f"  {title}: FAILED: {error}")
        results.append(False)

print("\nSample of the merged air table:")
print(data.load_air().head(8).to_string(index=False))

print("\nALL SOURCES OK" if all(results) else "\nSOME SOURCES FAILED - see above")
