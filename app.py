"""
app.py - the Respira MAD app (in Spanish).

Run it with:   .venv/bin/streamlit run app.py

This file only draws the screen. The data comes from data.py, the decisions
from risk.py, and every threshold and official text from config.py.
"""

import altair as alt
import folium
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from streamlit_folium import st_folium

import config
import data
import risk

MAP_POINT = "📍 Punto elegido en el mapa"

st.set_page_config(page_title="Respira MAD", page_icon="🌿", layout="centered")

# Reload the page every 15 minutes (the same time the data is cached for).
st_autorefresh(interval=data.CACHE_SECONDS * 1000, key="auto_refresh")


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def ago(moment, now):
    """'hace 35 min', 'hace 2 h 10 min' or 'hace 6 días'."""
    minutes = max(int((now - moment).total_seconds() // 60), 0)
    if minutes < 60:
        return f"hace {minutes} min"
    if minutes < 48 * 60:
        return f"hace {minutes // 60} h {minutes % 60} min"
    return f"hace {minutes // (24 * 60)} días"


def assistant_placeholder():
    """
    STEP 4 (not built yet): the optional AI assistant goes here.
    It is only shown when config.AI_ASSISTANT_ENABLED is True.
    When it is built, the Anthropic API key must be read from
    st.secrets["ANTHROPIC_API_KEY"] - never written in the code.
    """
    st.subheader("Asistente")
    st.info("Próximamente: un asistente para hacer preguntas sobre las condiciones de hoy.")


# ----------------------------------------------------------------------------
# Sidebar: who you are and where you are
# ----------------------------------------------------------------------------

# The list of places needs the air stations. If the live feed AND its backup
# both fail, fall back to the stations of the demo day.
try:
    places = risk.list_places(data.load_air())
except Exception:
    places = risk.list_places(data.load_replay_air(pd.Timestamp(config.REPLAY_DATE) + pd.Timedelta(hours=12)))

if "map_point" not in st.session_state:
    st.session_state.map_point = None          # (lat, lon) once the user clicks on the map
    st.session_state.last_click = None


def use_dropdown_place():
    """Called when the user picks a municipality: forget the map point."""
    st.session_state.map_point = None


with st.sidebar:
    st.header("Tu perfil")
    profile = st.selectbox("Perfil", list(config.PROFILES),
                           format_func=lambda key: config.PROFILES[key]["label"])
    allergic = st.checkbox("Tengo alergia al polen")
    allergy_types = []
    if allergic:
        allergy_types = st.multiselect(
            "¿A qué tipos de polen?", list(config.POLLEN_THRESHOLDS),
            default=["Gramíneas"], format_func=lambda key: config.POLLEN_NAMES[key],
            help="Solo aparecen los tipos de polen que tienen umbrales oficiales de la Red Palinocam.")
        if not allergy_types:
            st.caption("Elige al menos un tipo de polen para tenerlo en cuenta.")

    st.header("Ubicación")
    place = st.selectbox("Municipio", list(places), on_change=use_dropdown_place)
    if st.session_state.map_point:
        lat, lon = st.session_state.map_point
        place_label = risk.place_name(lat, lon, places)        # e.g. "Cerca de Tetuán"
        st.caption(f"Usando el punto elegido en el mapa: {place_label}. "
                   "Elige un municipio para volver a la lista.")
    else:
        lat, lon = places[place]
        place_label = place
        st.caption("También puedes tocar el mapa para elegir un punto.")

    st.header("Demostración")
    demo = st.toggle("Modo demostración: 28 mayo 2025",
                     help="Reproduce el día con más polen de gramíneas de toda la serie histórica (1993-2026).")
    demo_hour = config.REPLAY_DEFAULT_HOUR
    if demo:
        demo_hour = st.slider("Hora del día", config.WINDOW_FIRST_HOUR, config.WINDOW_LAST_HOUR - 1,
                              config.REPLAY_DEFAULT_HOUR, format="%d:00")

# ----------------------------------------------------------------------------
# The result
# ----------------------------------------------------------------------------

if demo:
    now = pd.Timestamp(config.REPLAY_DATE) + pd.Timedelta(hours=demo_hour)
else:
    now = risk.madrid_now()

result = risk.assess(lat, lon, profile, tuple(allergy_types), now, demo)
real_now = risk.madrid_now()

st.title("🌿 Respira MAD")
st.caption("¿Es buen momento para estar al aire libre en la Comunidad de Madrid?")

if demo:
    st.warning(f"**Modo demostración.** Datos del 28 de mayo de 2025 a las {demo_hour}:00, no de hoy. "
               "Ese día solo hay datos de las estaciones de la Comunidad de Madrid.")
elif result["air_table"] is not None:
    st.caption(f"🔄 Actualizado {ago(result['air_table'].attrs['fetched_at'], real_now)} · "
               "se actualiza solo cada 15 minutos")

# ---- the big card
index = result["level_index"]
if index is None:
    st.error("No hay mediciones disponibles en este momento. Inténtalo de nuevo en unos minutos.")
else:
    st.markdown(
        f"""
        <div style="background:{config.LEVEL_COLORS[index]}; color:{config.LEVEL_TEXT_COLORS[index]};
                    border-radius:18px; padding:24px 18px; text-align:center; margin-bottom:12px;">
          <div style="font-size:2.6rem; line-height:1.1;">{config.LEVEL_ICONS[index]}</div>
          <div style="font-size:2rem; font-weight:700; line-height:1.2;">{config.LEVEL_LABELS[index]}</div>
          <div style="font-size:1rem; margin-top:8px;">{config.PROFILES[profile]['label']} · {place_label}</div>
        </div>
        """,
        unsafe_allow_html=True)

    st.markdown(f"**Motivo principal:** {result['reason']}")

    st.markdown("**Recomendación oficial:**")
    for source, text in result["recommendations"]:
        st.markdown(f"> {text}")
        st.caption(f"Texto oficial, literal. Fuente: {source}.")
    if result["profile_note"]:
        st.info(result["profile_note"], icon="ℹ️")

for note in result["notes"]:
    st.warning(note, icon="⚠️")

# ---- the measurements behind the result
with st.expander("Mediciones usadas para este resultado"):
    if result["air"]:
        st.dataframe(pd.DataFrame([{
            "Contaminante": r["pollutant"],
            "Valor ICA (µg/m³)": round(r["ica_value"]),
            "Categoría ICA": r["category_name"],
            "Estación": f"{r['station']} ({r['source']})",
            "Distancia": f"{r['distance_km']:.1f} km".replace(".", ","),
            "Hora": f"{r['timestamp']:%d/%m %H:%M}",
            "Estado": "Provisional" if r["provisional"] else "Validado",
        } for r in result["air"]]), hide_index=True, width="stretch")
        st.caption("Valor ICA: media de la última hora (NO2), de hasta 8 horas (O3) o de hasta 24 horas "
                   "(PM10 y PM2.5), calculada solo con las horas del día en curso.")
    pollen = result["pollen"]
    if pollen:
        if pollen["kind"] == "measurement":
            age = "de hoy" if pollen["age_days"] == 0 else f"de hace {pollen['age_days']} días"
            st.markdown(f"**Polen:** estación {pollen['station']} (a {pollen['distance_km']:.1f} km), "
                        f"lectura del {pollen['reading_date']:%d/%m/%Y}, **{age}**.")
        else:
            st.markdown("**Polen:** sin lectura reciente de polen. Valores de la **PREVISIÓN** de Open-Meteo "
                        "(modelo, no medición), media del día.")
        st.dataframe(pd.DataFrame([{
            "Tipo de polen": t["name"],
            "Granos/m³": round(t["value"]),
            "Nivel (Red Palinocam)": t["level_name"],
            "Cuenta para ti": "Sí" if t["pollen_type"] in allergy_types else "No",
        } for t in pollen["types"]]), hide_index=True, width="stretch")

# ----------------------------------------------------------------------------
# Best hours (FORECAST)
# ----------------------------------------------------------------------------

day_word = "hoy" if result["windows_day"] == now.normalize() else "mañana"
st.subheader(f"Mejores horas {day_word}")
st.caption("PREVISIÓN (modelo de Open-Meteo), no mediciones oficiales.")

hours = result["hours"]
if hours is None:
    st.write("No hay previsión disponible para este punto.")
else:
    # windows worth recommending (never mark a "stay in" window as a best window)
    good_windows = [w for w in result["windows"] if w["level_index"] < 2]
    in_window = lambda t: any(w["start"] <= t < w["end"] for w in good_windows)   # noqa: E731

    # legend: colour + icon + label, so it never depends on colour alone
    legend = "".join(
        f'<span style="display:inline-block; margin:0 12px 4px 0; white-space:nowrap;">'
        f'<span style="display:inline-block; width:12px; height:12px; border-radius:3px; '
        f'background:{color}; vertical-align:middle;"></span> {icon} {label}</span>'
        for label, icon, color in zip(config.LEVEL_LABELS, config.LEVEL_ICONS, config.LEVEL_COLORS))
    st.markdown(f'<div style="font-size:0.85rem;">{legend}'
                f'<span style="white-space:nowrap;">★ Mejor franja</span> · '
                f'<span style="white-space:nowrap;">tono claro = hora ya pasada</span></div>',
                unsafe_allow_html=True)
    chart_data = pd.DataFrame({
        "Hora": hours["timestamp"].dt.strftime("%H"),
        "Franja": hours["timestamp"].dt.strftime("%H:00"),
        "Nivel": [config.LEVEL_LABELS[i] for i in hours["level"]],
        "Limitado por": hours["limited_by"].replace("", "—"),
        "Mejor franja": ["Sí" if in_window(t) else "No" for t in hours["timestamp"]],
        "Pasada": hours["timestamp"] < now.floor("h"),
        "NO2": hours["NO2"].round(0), "O3": hours["O3"].round(0),
        "PM10": hours["PM10"].round(0), "PM2.5": hours["PM2.5"].round(0),
        "Polen gramíneas": hours["grass_pollen"].round(0),
        "Polen olivo": hours["olive_pollen"].round(0),
        "Temperatura (°C)": hours["temperature_c"].round(0),
        "Lluvia (mm)": hours["rain_mm"].round(1),
    })
    base = alt.Chart(chart_data).encode(
        x=alt.X("Hora:O", title="Hora del día", axis=alt.Axis(labelAngle=0, labelFontSize=11)))
    # one tile per hour, coloured by level (hours already gone are faded)
    tiles = base.mark_rect(height=46, cornerRadius=4, stroke="white", strokeWidth=2).encode(
        color=alt.Color("Nivel:N",
                        scale=alt.Scale(domain=config.LEVEL_LABELS, range=config.LEVEL_COLORS),
                        legend=None),
        opacity=alt.condition("datum.Pasada", alt.value(0.3), alt.value(1.0)),
        tooltip=["Franja", "Nivel", "Limitado por", "Mejor franja", "NO2", "O3", "PM10", "PM2.5",
                 "Polen gramíneas", "Polen olivo", "Temperatura (°C)", "Lluvia (mm)"])
    # a dark bar and a star above the hours that belong to a best window
    best = base.transform_filter("datum['Mejor franja'] == 'Sí'")
    bar = best.mark_rect(height=5, yOffset=-31, color="#0b0b0b")
    star = best.mark_text(text="★", yOffset=-43, fontSize=13, color="#0b0b0b")
    st.altair_chart((tiles + bar + star).properties(height=150), width="stretch")

    best_level = result["windows"][0]["level_index"] if result["windows"] else None
    if best_level is None:
        st.write("No quedan horas suficientes para recomendar una franja.")
    elif best_level == 2:
        st.write(f"**No hay ninguna franja recomendable {day_word}**: todas las horas previstas están "
                 f"en «{config.LEVEL_LABELS[2]}».")
    else:
        st.markdown(f"★ **Mejores franjas {day_word}** (nivel «{config.LEVEL_LABELS[best_level]}»):")
        for w in result["windows"]:
            rain = "sin lluvia" if w["rain_mm"] == 0 else f"lluvia {w['rain_mm']} mm".replace(".", ",")
            st.markdown(f"- **{w['start']:%H:%M}–{w['end']:%H:%M}** · {w['temperature_c']:.0f} °C · {rain}")

# ----------------------------------------------------------------------------
# Map
# ----------------------------------------------------------------------------

st.subheader("Mapa de estaciones")
air_table, pollen_table = result["air_table"], result["pollen_table"]

chips = "".join(
    f'<span style="display:inline-block; margin:0 10px 4px 0; white-space:nowrap;">'
    f'<span style="display:inline-block; width:12px; height:12px; border-radius:50%; '
    f'background:{color}; border:1px solid #555; vertical-align:middle;"></span> {name}</span>'
    for name, color in zip(config.ICA_CATEGORIES, config.ICA_COLORS))
st.markdown(f'<div style="font-size:0.85rem;">Calidad del aire (ICA): {chips}'
            f'<span style="white-space:nowrap;">🌼 Estación de polen</span> · '
            f'<span style="white-space:nowrap;">📍 Tu ubicación</span></div>', unsafe_allow_html=True)

station_map = folium.Map(location=[lat, lon], zoom_start=10)      # OpenStreetMap background
if air_table is not None:
    for _, s in risk.station_categories(air_table).iterrows():
        folium.CircleMarker(
            [s["lat"], s["lon"]], radius=9, color="#333333", weight=1,
            fill=True, fill_color=config.ICA_COLORS[s["category"]], fill_opacity=0.95,
            tooltip=f"{s['station']}: {s['category_name']}",
            popup=folium.Popup(
                f"<b>{s['station']}</b><br>{s['source']}<br>ICA: <b>{s['category_name']}</b><br>"
                + "<br>".join(s["details"]) + f"<br>{s['timestamp']:%d/%m %H:%M}", max_width=260),
        ).add_to(station_map)
if pollen_table is not None and not pollen_table.empty:
    for station, group in pollen_table.groupby("station"):
        info = risk.pollen_near(group["lat"].iloc[0], group["lon"].iloc[0], group)
        top = "<br>".join(f"{t['name']}: {t['value']:.0f} granos/m³ ({t['level_name']})" for t in info["types"][:4])
        age = "de hoy" if info["age_days"] == 0 else f"de hace {info['age_days']} días"
        folium.Marker(
            [group["lat"].iloc[0], group["lon"].iloc[0]],
            icon=folium.DivIcon(html='<div style="font-size:20px; line-height:20px;">🌼</div>',
                                icon_size=(20, 20), icon_anchor=(10, 10)),
            tooltip=f"Polen · {station}",
            popup=folium.Popup(f"<b>Polen · {station}</b><br>Lectura del {info['reading_date']:%d/%m/%Y}, "
                               f"{age}<br>{top}", max_width=260),
        ).add_to(station_map)
folium.Marker([lat, lon], tooltip="Tu ubicación", icon=folium.Icon(color="blue", icon="user")).add_to(station_map)

clicked = st_folium(station_map, height=420, width="stretch", returned_objects=["last_clicked"], key="map")
st.caption("Toca cualquier punto del mapa para usarlo como ubicación. Toca una estación para ver sus datos.")

# A new click on the map becomes the chosen location.
click = (clicked or {}).get("last_clicked")
if click and click != st.session_state.last_click:
    st.session_state.last_click = click
    st.session_state.map_point = (click["lat"], click["lng"])
    st.rerun()

# ----------------------------------------------------------------------------
# Data freshness
# ----------------------------------------------------------------------------

st.subheader("Frescura de los datos")
rows = []
if air_table is not None:
    origin = "Día de demostración" if demo else ("En directo" if air_table.attrs.get("live") else "Copia de seguridad")
    for source, group in air_table.groupby("source"):
        provisional = (group["flag"] == "T").any()
        rows.append({
            "Fuente": f"Aire · {source}",
            "Último dato": f"{group['timestamp'].max():%d/%m %H:%M}",
            "Antigüedad": ago(group["timestamp"].max(), now),
            "Estado": "Provisional (pendiente de validar)" if provisional else "Tiempo real" if not demo else "Validado",
            "Origen": origin,
        })
if pollen_table is not None and not pollen_table.empty:
    latest = pollen_table["timestamp"].max()
    rows.append({
        "Fuente": "Polen · Red Palinocam (Comunidad de Madrid)",
        "Último dato": f"{latest:%d/%m/%Y}",
        "Antigüedad": "de hoy" if demo else ago(latest, now),
        "Estado": "Recuento manual, se publica con días de retraso",
        "Origen": "Día de demostración" if demo else ("En directo" if pollen_table.attrs.get("live") else "Copia de seguridad"),
    })
if result["forecast"] is not None:
    forecast = result["forecast"]
    rows.append({
        "Fuente": "Previsión · Open-Meteo (aire, polen y tiempo)",
        "Último dato": f"{forecast.attrs['fetched_at']:%d/%m %H:%M}",
        "Antigüedad": ago(forecast.attrs["fetched_at"], real_now),
        "Estado": "PREVISIÓN (modelo), no medición",
        "Origen": "En directo" if forecast.attrs.get("live") else "Copia de seguridad",
    })
# One line per source (plain text wraps well on a phone, a table does not).
for row in rows:
    st.markdown(f"- **{row['Fuente']}** — {row['Último dato']} ({row['Antigüedad']}) · "
                f"{row['Estado']} · {row['Origen']}")
st.caption("«Provisional» significa que la red todavía no ha validado el dato. «Copia de seguridad» significa "
           "que la fuente no respondió y se muestra la última copia guardada.")

# ----------------------------------------------------------------------------
# AI assistant (Step 4) - switched off by config.AI_ASSISTANT_ENABLED
# ----------------------------------------------------------------------------

if config.AI_ASSISTANT_ENABLED:
    assistant_placeholder()

# ----------------------------------------------------------------------------
# Footer: credits
# ----------------------------------------------------------------------------

st.divider()
st.caption(
    "**Fuentes de datos.** Calidad del aire y polen (Red Palinocam): "
    "[Datos abiertos de la Comunidad de Madrid](https://datos.comunidad.madrid/). "
    "Calidad del aire de la ciudad de Madrid: "
    "[Portal de datos abiertos del Ayuntamiento de Madrid](https://datos.madrid.es/). "
    "Previsión meteorológica, de calidad del aire y de polen: "
    "[Open-Meteo.com](https://open-meteo.com/) (licencia CC BY 4.0), con datos del servicio europeo CAMS. "
    "Categorías y recomendaciones de calidad del aire: Índice Nacional de Calidad del Aire, Resolución de "
    "2 de septiembre de 2020 ([BOE-A-2020-10426](https://www.boe.es/diario_boe/txt.php?id=BOE-A-2020-10426)). "
    "Niveles de polen: umbrales de la Red Palinocam.")
st.caption("Respira MAD es un proyecto del hackathon DataMad 2026. Es una herramienta informativa y no "
           "sustituye el consejo de un profesional sanitario.")
