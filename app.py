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

MAP_POINT = "Punto elegido en el mapa"

st.set_page_config(page_title="Respira MAD", layout="centered")

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
    st.info("El asistente «Pregúntale a Respira» está disponible en la versión web de Respira MAD.")


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
    # pollen types as filters: the selected ones count for the result (pollen is shown to everyone anyway).
    # Default: the types the Comunidad de Madrid names as the main causes of pollen allergy (config.py).
    allergy_types = st.multiselect(
        "Polen que cuenta para tu resultado", list(config.POLLEN_THRESHOLDS),
        default=config.POLLEN_DEFAULT_TYPES, format_func=lambda key: config.POLLEN_NAMES[key],
        placeholder="Ninguno",
        help="Marcados de inicio: los tipos que más alergias causan en Madrid, según la Comunidad de Madrid "
             "(«Polen y alergias»). Solo aparecen los tipos con umbrales oficiales de la Red Palinocam.")
    if not allergy_types:
        st.caption("Ninguno: el polen se muestra, pero no cambia tu resultado.")

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
        demo_hour = st.slider("Hora del día", 0, 23,
                              config.REPLAY_DEFAULT_HOUR, format="%d:00")

# ----------------------------------------------------------------------------
# The result
# ----------------------------------------------------------------------------

if demo:
    now = pd.Timestamp(config.REPLAY_DATE) + pd.Timedelta(hours=demo_hour)
else:
    now = risk.madrid_now()

try:
    result = risk.assess(lat, lon, profile, tuple(allergy_types), now, demo)
except Exception as error:                     # never show a technical error: a short Spanish message only
    data.problem("Streamlit: risk.assess failed", error)
    st.error("No se pueden mostrar los datos en este momento. Inténtalo de nuevo en unos minutos.")
    st.stop()
real_now = risk.madrid_now()

st.title("Respira MAD")
st.caption("¿Es buen momento para estar al aire libre en la Comunidad de Madrid?")

if demo:
    st.warning(f"**Modo demostración.** Datos del 28 de mayo de 2025 a las {demo_hour}:00, no de hoy. "
               "Ese día solo hay datos de las estaciones de la Comunidad de Madrid.")
elif result["air_table"] is not None:
    st.caption(f"Actualizado {ago(result['air_table'].attrs['fetched_at'], real_now)} · "
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
          <div style="line-height:1.1;">{config.icon_img(config.LEVEL_ICONS[index], config.LEVEL_TEXT_COLORS[index], 44)}</div>
          <div style="font-size:2rem; font-weight:700; line-height:1.2;">{result["headline"]}</div>
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
        st.info(result["profile_note"])

for note in result["notes"]:
    st.warning(note)

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
# ---- pollen: shown to everyone, as information (latest official reading).
# Only the allergy checkbox makes pollen count for the result.
st.subheader("Polen")
pollen = result["pollen"]
if pollen:
    age = "de hoy" if pollen["age_days"] == 0 else f"de hace {pollen['age_days']} días"
    st.markdown(f"Estación {pollen['station']} (a {pollen['distance_km']:.1f} km), "
                f"lectura del {pollen['reading_date']:%d/%m/%Y}, **{age}**.")
    # the main types: the ones that count for this person, any above "bajo", at least the top 3 (worst first)
    shown = [t for i, t in enumerate(pollen["types"])
             if t["pollen_type"] in allergy_types or t["level"] > 0 or i < 3][:6]
    st.dataframe(pd.DataFrame([{
        "Tipo de polen": t["name"],
        "Granos/m³": round(t["value"]),
        "Nivel (Red Palinocam)": t["level_name"],
        "Cuenta para ti": "Sí" if t["pollen_type"] in allergy_types else "No",
    } for t in shown]), hide_index=True, width="stretch")
    st.caption("Los tipos marcados como «Sí» cuentan para tu resultado; el resto es informativo." if allergy_types else
               "Ninguno: el polen se muestra, pero no cambia tu resultado.")
else:
    st.caption(f"Sin lectura reciente de polen en ninguna estación (últimos {config.POLLEN_MAX_AGE_DAYS} días).")

# ----------------------------------------------------------------------------
# Best hours (FORECAST)
# ----------------------------------------------------------------------------

day_word = "en las próximas 24 horas"
st.subheader("Mejores horas · próximas 24 horas")
st.caption("PREVISIÓN de nuestros modelos de aire y de polen, hechos con las mediciones oficiales de la Comunidad "
           "y del Ayuntamiento de Madrid; no son mediciones. La temperatura y la lluvia son de Open-Meteo.")

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
        f'background:{color}; vertical-align:middle;"></span> {config.icon_img(icon, "#333333", 15)} {label}</span>'
        for label, icon, color in zip(config.LEVEL_LABELS, config.LEVEL_ICONS, config.LEVEL_COLORS))
    st.markdown(f'<div style="font-size:0.85rem;">{legend}'
                f'<span style="white-space:nowrap;">{config.icon_img("star", "#333333", 14)} Mejor franja</span> · '
                f'<span style="white-space:nowrap;">tono claro = hora ya pasada</span></div>',
                unsafe_allow_html=True)
    chart_data = pd.DataFrame({
        "Hora": hours["timestamp"].dt.strftime("%H"),
        "Franja": hours["timestamp"].dt.strftime("%H:00"),
        "Nivel": [config.LEVEL_LABELS[i] for i in hours["level"]],
        "Limitado por": hours["limited_by"].replace("", "—"),
        "Mejor franja": ["Sí" if in_window(t) else "No" for t in hours["timestamp"]],
        "NO2": hours["NO2"].round(0), "O3": hours["O3"].round(0),
        "PM10": hours["PM10"].round(0), "PM2.5": hours["PM2.5"].round(0),
        "Temperatura (°C)": hours["temperature_c"].round(0),
        "Lluvia (mm)": hours["rain_mm"].round(1),
    })
    base = alt.Chart(chart_data).encode(
        x=alt.X("Hora:O", sort=None, title="Hora (las próximas 24, empezando ahora)",   # sort=None keeps the order across midnight
                axis=alt.Axis(labelAngle=0, labelFontSize=10)))
    # one tile per hour, coloured by level (hours already gone are faded)
    tiles = base.mark_rect(height=46, cornerRadius=4, stroke="white", strokeWidth=2).encode(
        color=alt.Color("Nivel:N",
                        scale=alt.Scale(domain=config.LEVEL_LABELS, range=config.LEVEL_COLORS),
                        legend=None),
        tooltip=["Franja", "Nivel", "Limitado por", "Mejor franja", "NO2", "O3", "PM10", "PM2.5",
                 "Temperatura (°C)", "Lluvia (mm)"])
    # a dark bar and a star above the hours that belong to a best window
    best = base.transform_filter("datum['Mejor franja'] == 'Sí'")
    bar = best.mark_rect(height=5, yOffset=-31, color="#0b0b0b")
    # a five-pointed star drawn as a shape (not a text character or an emoji)
    star_shape = "M0,-1L0.224,-0.309L0.951,-0.309L0.363,0.118L0.588,0.809L0,0.382L-0.588,0.809L-0.363,0.118L-0.951,-0.309L-0.224,-0.309Z"
    star = best.mark_point(shape=star_shape, size=90, yOffset=-43, filled=True, opacity=1, color="#0b0b0b")
    st.altair_chart((tiles + bar + star).properties(height=150), width="stretch")

    best_level = result["windows"][0]["level_index"] if result["windows"] else None
    if best_level is None:
        st.write("No quedan horas suficientes para recomendar una franja.")
    elif best_level == 2:
        st.write(f"**No hay ninguna franja recomendable {day_word}**: todas las horas previstas están "
                 f"en «{config.LEVEL_LABELS[2]}».")
    else:
        st.markdown(f"{config.icon_img('star', '#333333', 15)} **Mejores franjas {day_word}** "
                    f"(nivel «{config.LEVEL_LABELS[best_level]}»):", unsafe_allow_html=True)
        for w in result["windows"]:
            rain = "sin lluvia" if w["rain_mm"] == 0 else f"lluvia {w['rain_mm']} mm".replace(".", ",")
            st.markdown(f"- **{w['day']} {w['start']:%H:%M}–{w['end']:%H:%M}** · {w['temperature_c']:.0f} °C · {rain}")

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
            f'<span style="white-space:nowrap;">{config.icon_img("pollen", "#6b5200", 16)} Estación de polen</span> · '
            f'<span style="white-space:nowrap;">{config.icon_img("map-pin", "#1f5fbf", 16)} Tu ubicación</span></div>', unsafe_allow_html=True)

station_map = folium.Map(location=[lat, lon], zoom_start=10)      # OpenStreetMap background
if air_table is not None:
    for _, s in risk.station_categories(air_table, now).iterrows():
        # a station whose readings are ALL older than 3 hours ("stale") is not drawn: it is never
        # used in a result, and the result notice already names it when a nearer station is skipped
        if s["stale"]:
            continue
        has_category = s["category"] is not None and not pd.isna(s["category"])
        fill = config.DOUBTFUL_COLOR if s["doubtful"] or not has_category else config.ICA_COLORS[int(s["category"])]
        label = "Dato dudoso" if s["doubtful"] else s["category_name"]
        folium.CircleMarker(
            [s["lat"], s["lon"]], radius=9, color="#333333", weight=1,
            # a doubtful reading is grey
            fill=True, fill_color=fill, fill_opacity=0.95, opacity=1,
            tooltip=f"{s['station']}: {label}",
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
            icon=folium.DivIcon(html='<div style="width:22px; height:22px; background:#fff; border:1.5px solid #8a6d00; '
                                     'border-radius:7px; display:flex; align-items:center; justify-content:center;">'
                                     + config.icon_svg("pollen", "#6b5200", 15) + "</div>",
                                icon_size=(22, 22), icon_anchor=(11, 11)),
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
        "Fuente": "Previsión de aire · modelo propio (con mediciones oficiales)",
        "Último dato": f"{(forecast.attrs.get('fetched_at') or real_now):%d/%m %H:%M}",
        "Antigüedad": "día de demostración" if demo else ago(forecast.attrs.get("fetched_at") or real_now, real_now),
        "Estado": "PREVISIÓN (modelo), no medición",
        "Origen": "Día de demostración" if demo else ("En directo" if forecast.attrs.get("live") else "Copia de seguridad"),
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

# The assistant itself lives in the web app (api.py + static/). This Streamlit
# fallback only points to it.
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
    "Previsiones de calidad del aire y de polen: modelos propios, hechos únicamente con esas mediciones oficiales. "
    "Previsión meteorológica (lo único que no es de la Comunidad ni del Ayuntamiento): "
    "[Open-Meteo.com](https://open-meteo.com/) (licencia CC BY 4.0). "
    "Categorías y recomendaciones de calidad del aire: Índice Nacional de Calidad del Aire, Resolución de "
    "2 de septiembre de 2020 ([BOE-A-2020-10426](https://www.boe.es/diario_boe/txt.php?id=BOE-A-2020-10426)). "
    "Niveles de polen: umbrales de la Red Palinocam.")
st.caption("Respira MAD es un proyecto del hackathon DataMad 2026. Es una herramienta informativa y no "
           "sustituye el consejo de un profesional sanitario.")
