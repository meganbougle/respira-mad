"""
build_gazetteer.py - one-off script that builds gazetteer.csv, the list of
place names used to describe a point on the map ("Cerca de Tetuán").

Run it with:   .venv/bin/python build_gazetteer.py

Sources:
  * Madrid's 21 districts: the official location of each district office
    ("Juntas Municipales de Distrito", datos.madrid.es dataset 206688).
  * The other 178 municipalities of the Comunidad de Madrid: names from the
    Comunidad's dataset "Red de Calidad del Aire. Municipios por zonas",
    coordinates from Open-Meteo's free geocoding API (GeoNames data).
The city of Madrid itself is left out, so a point inside it gets a district.
"""

import io
import logging
import re
import time
import unicodedata

import pandas as pd
import requests
import streamlit  # noqa: F401

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import data  # noqa: E402

DISTRICTS_URL = "https://datos.madrid.es/egob/catalogo/206688-0-juntas-distrito.csv"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OUTPUT = data.BACKUP_DIR.parent / "gazetteer.csv"


def plain(text):
    """Lower case, no accents, no punctuation: to compare names."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


rows = []

# ---- Madrid districts -------------------------------------------------------
response = requests.get(DISTRICTS_URL, timeout=data.TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
response.raise_for_status()
districts = pd.read_csv(io.StringIO(response.content.decode("latin-1")), sep=";", dtype=str)
for _, row in districts.iterrows():
    name = re.sub(r"^Junta Municipal (del|de) Distrito (de )?", "", row["NOMBRE"].strip())
    rows.append({"name": name, "kind": "Distrito de Madrid", "lat": float(row["LATITUD"]),
                 "lon": float(row["LONGITUD"]), "source": "Ayuntamiento de Madrid"})
print(f"{len(rows)} Madrid districts")

# ---- the other municipalities ----------------------------------------------
zones = data._read_csv(data._ckan_csv_url("calidad_aire_zonas"))
names = sorted(set(zones["zona_calidad_aire_municipio"].str.strip()) - {"MADRID"})
missing = []
for raw_name in names:
    # "ÁLAMO (EL)" -> "EL ÁLAMO"
    match = re.match(r"^(.*) \((EL|LA|LOS|LAS)\)$", raw_name)
    query = f"{match.group(2)} {match.group(1)}" if match else raw_name
    results = requests.get(GEOCODING_URL, timeout=data.TIMEOUT, params={
        "name": query.title(), "count": 10, "language": "es", "countryCode": "ES"}).json().get("results", [])
    in_madrid = [r for r in results if "Madrid" in (r.get("admin1") or "")]
    exact = [r for r in in_madrid if plain(r["name"]) == plain(query)]
    best = (exact or in_madrid or [None])[0]
    if best is None:
        missing.append(raw_name)
        continue
    rows.append({"name": best["name"], "kind": "Municipio", "lat": best["latitude"],
                 "lon": best["longitude"], "source": "Open-Meteo geocoding (GeoNames)"})
    time.sleep(0.05)

table = pd.DataFrame(rows).drop_duplicates("name")
table.to_csv(OUTPUT, index=False)
print(f"{len(table)} places saved to {OUTPUT.name}")
print(f"not found ({len(missing)}): {missing}")
