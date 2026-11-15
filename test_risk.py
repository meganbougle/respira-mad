"""
test_risk.py - prints the risk result for 4 places and every profile,
live and for the demo day.

Run it with:   .venv/bin/python test_risk.py
"""

import logging

import pandas as pd
import streamlit  # noqa: F401  (imported first so we can quiet it below)

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import config  # noqa: E402
import risk    # noqa: E402

ALLERGY_EXAMPLE = ["Gramíneas", "Olivo"]


def show(result, title):
    label = config.LEVEL_LABELS[result["level_index"]] if result["level_index"] is not None else "SIN DATOS"
    print(f"\n  [{title}]  ->  {label.upper()}")
    print(f"    motivo:        {result['reason']}")
    for source, text in result["recommendations"]:
        print(f"    recomendación: {text}")
        print(f"                   (fuente: {source})")
    if result["profile_note"]:
        print(f"    nota perfil:   {result['profile_note'][:90]}...")
    for w in result["windows"]:
        print(f"    mejor franja:  {w['day']} {w['start']:%H:%M}-{w['end']:%H:%M}  {w['level']}"
              f"  ({w['temperature_c']} C, lluvia {w['rain_mm']} mm)  {w['kind']}")
    for note in result["notes"]:
        print(f"    aviso:         {note}")


def run(now, demo):
    for place, (lat, lon) in config.PLACES.items():
        print("\n" + "=" * 78)
        print(f"{place}  ({lat}, {lon})" + ("   [MODO DEMOSTRACIÓN]" if demo else ""))
        print("=" * 78)

        # The measurements are the same for every profile, so print them once.
        result = risk.assess(lat, lon, "general", (), now, demo)
        print("MEDICIONES usadas (estaciones oficiales):")
        for r in result["air"]:
            print(f"  {r['pollutant']:5s} {r['ica_value']:5.0f} ug/m3 (media de {r['ica_hours']:2d} h, última hora {r['value']:.0f})"
                  f" -> {r['category_name']:21s} {r['station']} ({r['source']}), {r['distance_km']} km,"
                  f" {r['timestamp']:%d/%m %H:%M}{' provisional' if r['provisional'] else ''}")
        pollen = result["pollen"]
        if pollen:
            top = pollen["types"][0]
            print(f"  Polen ({pollen['kind']}): {pollen['station']}, {pollen['distance_km']} km, lectura del"
                  f" {pollen['reading_date']:%d/%m} (hace {pollen['age_days']} días); más alto: {top['name']}"
                  f" {top['value']:.0f} granos/m3 -> {top['level_name']}")

        for profile, settings in config.PROFILES.items():
            show(risk.assess(lat, lon, profile, (), now, demo), settings["label"])
        show(risk.assess(lat, lon, "general", ALLERGY_EXAMPLE, now, demo), "General + alergia a gramíneas y olivo")
        show(risk.assess(lat, lon, "asthma", ALLERGY_EXAMPLE, now, demo), "Asma + alergia a gramíneas y olivo")


now = risk.madrid_now()
print(f"Hora en Madrid: {now:%Y-%m-%d %H:%M}")
run(now, demo=False)

print("\n\n" + "#" * 78)
print(f"# MODO DEMOSTRACIÓN: {config.REPLAY_DATE} a las {config.REPLAY_DEFAULT_HOUR}:00")
print("#" * 78)
run(pd.Timestamp(config.REPLAY_DATE) + pd.Timedelta(hours=config.REPLAY_DEFAULT_HOUR), demo=True)
