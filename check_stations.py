"""
check_stations.py - prints raw hourly readings for 3 stations so you can
compare them by hand with the official websites.

Run it with:   .venv/bin/python check_stations.py

Flags: V = validated, T = provisional (not yet validated). Readings flagged
N (no data / not valid) are already dropped.
"""

import logging

import streamlit  # noqa: F401

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import data  # noqa: E402

HOURS_TO_SHOW = 4

# (network, 8-digit station code, name, official website)
STATIONS = [
    ("Comunidad", "28065014", "Getafe", "https://gestiona.comunidad.madrid/azul_internet/run/j/AvisosAccion.icm"),
    ("Comunidad", "28005002", "Alcalá de Henares", "https://gestiona.comunidad.madrid/azul_internet/run/j/AvisosAccion.icm"),
    ("Ayuntamiento", "28079008", "Escuelas Aguirre", "https://airedemadrid.madrid.es/portal/site/calidadaire"),
]

comunidad = data._hourly_long(data._read_csv(data._ckan_csv_url("calidad_aire_datos_dia")), data.POLLUTANTS)
city = data._hourly_long(data._read_csv(data.CITY_AIR_URL), data.POLLUTANTS)

for network, code, name, website in STATIONS:
    hourly = comunidad if network == "Comunidad" else city
    station = hourly[hourly["station_code"] == code]
    print(f"\n{name}  (station {code}, {network} de Madrid)")
    print(f"  compare with: {website}")
    # one row per hour, one column per pollutant, value followed by its flag
    station = station.assign(cell=station["value"].map("{:g}".format) + " " + station["flag"])
    grid = station.pivot(index="timestamp", columns="name", values="cell").tail(HOURS_TO_SHOW)
    print("  " + grid.fillna("-").to_string().replace("\n", "\n  "))
    print("  (ug/m3; the time is the END of the hour, Madrid local time)")

print("\nBoth networks also appear on the Ministry's index map: https://ica.miteco.es/")
