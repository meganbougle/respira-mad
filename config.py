"""
config.py - every threshold, text and setting used by the risk logic.

RULE: nothing in here is invented. Each block says where the numbers come
from. Where the app simplifies the official method, or makes a choice of its
own, the comment says so in capitals (SIMPLIFICATION / APP DECISION).
"""

# ============================================================================
# 1. AIR: Spain's national air quality index (ICA)
# ============================================================================
# Official source:
#   Resolución de 2 de septiembre de 2020, de la Dirección General de Calidad
#   y Evaluación Ambiental, por la que se modifica el Anexo de la Orden
#   TEC/351/2019, de 18 de marzo, por la que se aprueba el Índice Nacional de
#   Calidad del Aire.  BOE núm. 242, 10/09/2020, ref. BOE-A-2020-10426.
#   https://www.boe.es/diario_boe/txt.php?id=BOE-A-2020-10426
# The Ministry's ICA page links to this resolution as the one in force
# (checked 15 Nov 2026):
#   https://www.miteco.gob.es/es/calidad-y-evaluacion-ambiental/temas/atmosfera-y-calidad-del-aire/visualizacion-datos-calidad-del-aire/ica.html

# The six official categories, from best (position 0) to worst (position 5).
ICA_CATEGORIES = [
    "Buena",
    "Razonablemente buena",
    "Regular",
    "Desfavorable",
    "Muy desfavorable",
    "Extremadamente desfavorable",
]

# Upper limit of each category, in µg/m3 (official table "Bandas de
# concentraciones y categorías de índice").
# Example: NO2 0-40 is Buena, 41-90 Razonablemente buena, ... 341-1000
# Extremadamente desfavorable.
# Values above the last number are treated as measurement errors and ignored,
# exactly as the resolution says.
ICA_BANDS = {
    "NO2":   [40, 90, 120, 230, 340, 1000],
    "O3":    [50, 100, 130, 240, 380, 800],
    "PM10":  [20, 40, 50, 100, 150, 1200],
    "PM2.5": [10, 20, 25, 50, 75, 800],
}
# (The official index also uses SO2: 100, 200, 350, 500, 750, 1250.
#  APP DECISION: SO2 is left out because only 9 of the 51 stations measure it.)

# Official averaging period: how many hours are averaged before comparing with
# the bands above.
#   NO2:          mean of the last hour
#   O3:           moving average of the last 8 hours
#   PM10, PM2.5:  moving average of the last 24 hours
ICA_AVERAGING_HOURS = {"NO2": 1, "O3": 8, "PM10": 24, "PM2.5": 24}

# SIMPLIFICATION 1 (station measurements):
#   The real-time files only contain the hours of the CURRENT day. So our
#   "8-hour" and "24-hour" averages only use the hours available since
#   midnight (for example at 10:00 the PM "24-hour" average uses 10 hours).
#   The table from data.py says how many hours were used (column ica_hours).
# SIMPLIFICATION 2 (forecast, used for the "best windows"):
#   The Open-Meteo hourly forecast values are compared directly with the
#   bands, without the 8-hour / 24-hour averaging, because averaging would
#   hide the differences between hours that the windows are meant to show.
# SIMPLIFICATION 3:
#   The official index fills gaps with corrected CAMS model data. We do not:
#   a station with no data for a pollutant is skipped and the next nearest
#   station is used.

# Official health recommendations ("Recomendaciones para la salud"), copied
# word for word from the resolution. One text per category, in the same order
# as ICA_CATEGORIES.
#   "general"   = column "Población general"
#   "sensitive" = column "Grupos de riesgo y personas sensibles"
# The resolution defines the sensitive population as "adultos y niños con
# problemas respiratorios y adultos con afecciones cardiacas".
ICA_ADVICE_ES = {
    "general": [
        "Disfruta de tus actividades al aire libre de manera normal.",
        "Disfruta de tus actividades al aire libre de manera normal.",
        "Disfruta de tus actividades al aire libre de manera normal. Sin embargo, vigila la aparición de síntomas como tos, irritación de garganta, falta de aire, fatiga excesiva o palpitaciones.",
        "Considera reducir las actividades prolongadas y enérgicas al aire libre, especialmente si experimentas tos, falta de aire o irritación de garganta.",
        "Considera reducir las actividades al aire libre, y realizarlas en el interior o posponerlas para cuando la calidad del aire sea buena o razonablemente buena.",
        "Reduce toda actividad al aire libre y considera realizar las actividades en el interior o posponerlas para cuando la calidad del aire sea buena o razonablemente buena. Utiliza la protección adecuada para los trabajos que deban ser realizados al aire libre.",
    ],
    "sensitive": [
        "Disfruta de tus actividades al aire libre de manera normal.",
        "Disfruta de tus actividades al aire libre de manera normal.",
        "Considera reducir las actividades prolongadas y enérgicas al aire libre. Las personas con asma o enfermedades respiratorias deben seguir cuidadosamente su plan de medicación. Las personas con problemas del corazón pueden experimentar palpitaciones, dificultad en la respiración o fatiga inusual.",
        "Considera reducir las actividades al aire libre, y realizarlas en el interior o posponerlas para cuando la calidad del aire sea buena o razonablemente buena. Sigue el plan de tratamiento médico meticulosamente.",
        "Reduce toda actividad al aire libre, y considera realizar las actividades en el interior o posponerlas para cuando la calidad del aire sea buena o razonablemente buena. Sigue el plan de tratamiento médico meticulosamente.",
        "Evita la estancia prolongada al aire libre. Sigue el plan de tratamiento médico, en su caso, meticulosamente, y acude a un servicio de urgencias si tu estado de salud empeora.",
    ],
}

# ----------------------------------------------------------------------------
# The app's three levels
# ----------------------------------------------------------------------------
LEVELS = ["good", "be careful", "stay in"]      # position 0, 1, 2 (names used in the code)
# What the app shows for each level (verde / ámbar / rojo). Never colour
# alone: the label and the icon always go with it.
LEVEL_LABELS = ["Puedes salir", "Precaución", "Mejor quédate en casa"]
LEVEL_ICONS = ["check", "triangle-alert", "house"]        # names of the icons below (no emojis anywhere in the app)

# The icons of the Streamlit fallback: Lucide line icons (lucide.dev, ISC
# licence; 24x24 grid, 2px stroke), plus "pollen", our own drawing of a pollen
# grain for pollen stations. The web app has the same icons in static/index.html.
ICON_PATHS = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "triangle-alert": '<path d="m21.730 18-8-14a2 2 0 0 0-3.480 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.730-3"/><path d="M12 9v4"/><path d="M12 17h.010"/>',
    "house": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "map-pin": '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
    "star": '<path d="M11.525 2.295a.530.530 0 0 1 .950 0l2.310 4.679a2.123 2.123 0 0 0 1.595 1.160l5.166.756a.530.530 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.140a.530.530 0 0 1-.771.560l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.010a.530.530 0 0 1-.770-.560l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.160 9.795a.530.530 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.160z"/>',
    "pollen": '<circle cx="12" cy="12" r="5"/><path d="M12 7V3.900M16.330 9.500l2.680-1.550M16.330 14.500l2.680 1.550M12 17v3.100M7.670 14.500l-2.680 1.550M7.670 9.500 4.990 7.950"/>'
              '<g fill="{color}" stroke="none"><circle cx="12" cy="2.800" r="1.300"/><circle cx="19.970" cy="7.400" r="1.300"/><circle cx="19.970" cy="16.600" r="1.300"/>'
              '<circle cx="12" cy="21.200" r="1.300"/><circle cx="4.030" cy="16.600" r="1.300"/><circle cx="4.030" cy="7.400" r="1.300"/></g>',
}


def icon_svg(name, color="#14201b", size=18):
    """One icon as an SVG drawing (for maps and other places that accept raw HTML)."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            + ICON_PATHS[name].replace("{color}", color) + "</svg>")


def icon_img(name, color="#14201b", size=18):
    """The same icon as an <img> tag (Streamlit's text does not draw raw SVG, but it does draw images)."""
    from urllib.parse import quote
    return (f'<img src="data:image/svg+xml;utf8,{quote(icon_svg(name, color, size))}" width="{size}" height="{size}" '
            f'alt="" style="vertical-align:-0.2em;">')
LEVEL_COLORS = ["#0ca30c", "#fab219", "#d03b3b"]          # fill
LEVEL_TEXT_COLORS = ["#ffffff", "#0b0b0b", "#ffffff"]     # text on top of the fill

# Official colours of the six ICA categories (same order as ICA_CATEGORIES),
# taken from the table in the resolution. Used on the map.
ICA_COLORS = ["#72f3f5", "#5fb35f", "#f0cb30", "#dc3a14", "#800000", "#8000ff"]

# APP DECISION: how the six official categories become the app's three levels.
# The official index has no three-level summary, so this mapping is ours. It
# follows the wording of the official advice for each audience:
#   0 "good"       = the advice says to enjoy outdoor activities as normal
#   1 "be careful" = the advice says to consider reducing prolonged and
#                    vigorous outdoor activities
#   2 "stay in"    = the advice says to do activities indoors, postpone them,
#                    or avoid being outdoors
# One number per official category, in the same order as ICA_CATEGORIES.
ICA_TO_LEVEL = {
    "general":   [0, 0, 0, 1, 2, 2],
    "sensitive": [0, 0, 1, 2, 2, 2],
}


# ============================================================================
# 2. POLLEN: Red Palinocam (Comunidad de Madrid) risk levels
# ============================================================================
# Official source of the numbers:
#   The thresholds "actualmente vigentes en la Red PALINOCAM", calculated from
#   the network's 2010-2019 pollen data, as published in:
#   "Informe técnico sobre los resultados de los análisis polínicos en el área
#   urbana de Madrid durante el periodo 2023-2024" (Ayuntamiento de Madrid /
#   Grupo de Aerobiología, Universidad Complutense, November 2025), tables 5-14.
#   https://airedemadrid.madrid.es/UnidadesDescentralizadas/Sostenibilidad/CalidadAire/Portal/02_Actuaciones/02%20Ficheros/Informe_Analisis_Polen_Noviembre_2025.pdf
#   The Comunidad's own weekly bulletins draw the same "Nivel Medio" and
#   "Nivel Alto" lines but only as a chart: https://www.comunidad.madrid/salud/polen
#
# The network has FOUR levels (not three): bajo, medio, alto, muy alto.
# The numbers are daily averages in grains of pollen per m3 of air, the same
# unit as the open-data file. Each entry is: (medio, alto, muy alto).
# Example: Gramíneas below 26 is "bajo", from 26 "medio", from 53 "alto",
# from 150 "muy alto".
# Note: the report says the levels are based on percentiles of the historical
# series ("criterios aerobiológicos"), not on clinical studies.
#
# Only these 10 pollen types have published thresholds. The other 17 types in
# the data (Artemisia, Abedul, Aliso...) have none, so they are NOT used in
# the risk level. We did not fall back to the Spanish Aerobiology Network
# (REA) numbers because we could not check them against an official REA page.
POLLEN_LEVELS = ["bajo", "medio", "alto", "muy alto"]     # position 0..3

POLLEN_THRESHOLDS = {
    # name exactly as it appears in the open-data file: (medio, alto, muy alto)
    "Plátano de paseo":     (81, 248, 953),
    "Cupresáceas/Taxáceas": (59, 171, 659),
    "Quercus":              (55, 164, 728),
    "Olivo":                (34, 74, 194),
    "Gramíneas":            (26, 53, 150),
    "Pinos":                (54, 88, 206),
    "Populus":              (24, 46, 128),
    "Olmos":                (18, 38, 124),
    "Plantago":             (21, 32, 60),
    "Urticaceae (Ortigas)": (10, 13, 22),
}

# Ignore a pollen station if its latest reading is older than this.
# (Readings are published several days late; the app always shows their age.)
POLLEN_MAX_AGE_DAYS = 10

# Open-Meteo forecasts 6 pollen types. Only two of them match a pollen type
# that has an official threshold above.
# The forecast is used in two places:
#   - the "best hours" chart (hour by hour), and
#   - INSTEAD of a station reading when no pollen station has a reading from
#     the last POLLEN_MAX_AGE_DAYS days. The app then says "Sin lectura
#     reciente de polen" and labels the value as a forecast. In that case the
#     average of today's forecast hours is compared with the thresholds
#     (they are thresholds for daily averages).
# SIMPLIFICATION: in the hour-by-hour chart the HOURLY forecast is compared
# with thresholds that were defined for DAILY averages.
FORECAST_POLLEN_TO_OFFICIAL = {
    "grass_pollen": "Gramíneas",
    "olive_pollen": "Olivo",
}

# APP DECISION: how the four pollen levels become the app's three levels.
# One number per pollen level (bajo, medio, alto, muy alto):
#   bajo = good, medio = be careful, alto and muy alto = stay in.
# It is only applied to people who tick "Tengo alergia al polen", and only
# for the pollen types they select.
POLLEN_TO_LEVEL = [0, 1, 2, 2]

# Friendly Spanish names for the pollen types that have official thresholds
# (shown in the app's multi-select). The keys must match POLLEN_THRESHOLDS.
POLLEN_NAMES = {
    "Gramíneas":            "Gramíneas",
    "Olivo":                "Olivo",
    "Plátano de paseo":     "Plátano de paseo",
    "Cupresáceas/Taxáceas": "Cipreses y arizónicas",
    "Quercus":              "Encinas y robles",
    "Pinos":                "Pinos",
    "Populus":              "Chopos",
    "Olmos":                "Olmos",
    "Plantago":             "Llantenes",
    "Urticaceae (Ortigas)": "Ortigas y parietarias",
}

# Official advice for days of high pollen, copied word for word from the
# Comunidad de Madrid leaflet "Lo que debes conocer sobre la alergia al polen"
# (section "Medidas preventivas para los días de máxima concentración
# atmosférica de polen"):
#   https://www.comunidad.madrid/docs/assets/2017/11/23/lo_que_debes_conocer_sobre_la_alergia_al_polen.pdf
POLLEN_ADVICE_ES = [
    "Evitar salidas al campo, cruzar parques y zonas verdes.",
    "Viajar en coche con las ventanillas cerradas (evitando desplazamientos en moto o bicicleta).",
    "Ventanas cerradas en el domicilio, aireando a primera hora de la mañana (si es posible aire acondicionado con filtros).",
    "Utilizar gafas de sol en el exterior.",
    "Realizar el tratamiento prescrito por su médico.",
]

# ============================================================================
# 3. PROFILES
# ============================================================================
# "air": which column of the official ICA advice the profile gets.
# "note": shown in the app next to the recommendation (None = no note).
#
# APP DECISION: the official ICA text defines sensitive people as "adultos y
# niños con problemas respiratorios y adultos con afecciones cardiacas". It
# does not name older people or young children as such. We give both the
# sensitive-group advice as a precaution, and the app says so (EXTENSION_NOTE).
#
# Pollen allergy is NOT a profile: it is a separate checkbox, with a list of
# the pollen types the person is allergic to. Asthma without the allergy
# checkbox uses air quality only.
EXTENSION_NOTE = (
    "Aplicamos a este perfil las recomendaciones oficiales para grupos de riesgo "
    "y personas sensibles por precaución. Es una extensión nuestra: el texto "
    "oficial del ICA define como población sensible a las personas con problemas "
    "respiratorios y a los adultos con afecciones cardiacas."
)
PROFILES = {
    # "short" is the label on the small buttons of the web app
    "general":  {"label": "General",             "short": "General",       "air": "general",   "note": None},
    "asthma":   {"label": "Asma / respiratorio", "short": "Asma",          "air": "sensitive", "note": None},
    "older":    {"label": "Persona mayor",       "short": "Persona mayor", "air": "sensitive", "note": EXTENSION_NOTE},
    "children": {"label": "Niño/a",              "short": "Niño/a",        "air": "sensitive", "note": EXTENSION_NOTE},
}


# ============================================================================
# 4. BEST WINDOWS (APP DECISIONS - there is no official method for this)
# ============================================================================
WINDOW_HOURS = 3            # length of each suggested window (2 if few hours are left)
WINDOW_COUNT = 3            # how many windows to suggest at most
HOURS_AHEAD = 24            # the hourly strip and the windows cover the next 24 hours, day and night
# APP DECISION: if the person is allergic and the pollen STATION reading puts
# them at "be careful" or "stay in", every hour of today is at least that
# level, even when the model forecasts less pollen. A measurement beats the
# model, and the chart must never look better than the main result.
# Windows are ranked by: lowest risk level, then no rain, then lowest
# forecast pollution. Weather has NO thresholds of its own in this app
# (no official ones were adopted); rain is only used to break ties.


# ============================================================================
# 5. DEMO MODE, PLACES AND SETTINGS
# ============================================================================
# "Modo demostración": the day with the highest grass pollen in the whole
# history (1993-2026). Its data is stored in backup/replay/2025-05-28/.
# That day only has the 28 Comunidad air stations (the Ayuntamiento has not
# published its 2025 hourly history).
REPLAY_DATE = "2025-05-28"
REPLAY_DEFAULT_HOUR = 10          # the demo starts at 10:00 on that day

# ---- AI assistant "Pregúntale a Respira" (web app only) --------------------
# Set to False to switch the assistant off completely (the chat button hides).
# It also needs the environment variable ANTHROPIC_API_KEY on the server.
# The key is NEVER written in the code or committed to git.
AI_ASSISTANT_ENABLED = True
AI_MODEL = "claude-haiku-5-5"
# Longest answer the model may write. It was 400, but the official pollen
# recommendation alone is about 130 tokens, and answers that quote it were
# being cut off mid-sentence, so it is 600. Answers are still limited to 5
# sentences by the instructions in assistant.py.
AI_MAX_TOKENS = 600
AI_KEEP_MESSAGES = 6              # only the last 6 chat messages are sent
AI_MAX_MESSAGES_PER_HOUR = 20     # per visitor
AI_MAX_TOOL_ROUNDS = 5            # how many times it may fetch data for one answer
# The model's extra "thinking" step is off: thinking uses up the same 400
# tokens, which could cut answers short. Set to True to turn it back on
# (then raise AI_MAX_TOKENS to about 1500).
AI_THINKING = False
# List prices used ONLY to estimate cost in the log (US dollars per million
# tokens, for prompts under 100,000 tokens). Check them against Anthropic's
# pricing page before relying on the estimate.
AI_PRICE_INPUT_PER_MTOK = 0.10
AI_PRICE_OUTPUT_PER_MTOK = 0.50

# ---- Our pollen forecast model and the 5-day planner -----------------------
# The model is trained and tested by train_pollen_model.py; its test results
# (which pollen types and lead days are RELIABLE) are in pollen_model/meta.json
# and pollen_model/backtest_report.md. The app uses the model only where it is
# RELIABLE; otherwise the European forecast (grass and olive only), otherwise
# "Sin previsión fiable".
PLANNER_DAYS = 5                                   # today + 4 days
# The parts of the day shown in the planner: (name, first hour, last hour, day offset).
# The fourth number says which calendar day the block falls on: the
# "madrugada" (00-07) belongs to the night that starts the evening before, so
# it uses the hours of the NEXT calendar day.
PLANNER_BLOCKS = [("Mañana", 7, 13, 0), ("Tarde", 13, 20, 0), ("Noche", 20, 24, 0), ("Madrugada", 0, 7, 1)]
# SIMPLIFICATION: which forecast value stands for a whole block. The official
# index uses the last hour for NO2, an 8-hour average for O3 and a 24-hour
# average for particles. A block lasts 3 to 7 hours, so we take the highest
# hour for NO2 and the AVERAGE of the block for O3, PM10 and PM2.5 (one high
# hour of particles should not turn a whole morning red).
PLANNER_BLOCK_VALUE = {"NO2": "max", "O3": "mean", "PM10": "mean", "PM2.5": "mean"}
# In demo mode the planner shows the view "as of" this day, using only what
# was known then, so the 28 May 2025 peak appears four days ahead.
PLANNER_DEMO_DATE = "2025-05-24"

# APP DECISION: the confidence label of each planner day.
#  - If the person selected pollen types: from the share of in-season days the
#    pollen forecast got right in the 2025 test, for that lead day (the lowest
#    of the selected types). "Alta" from 75%, "media" from 60%, else "baja".
#    The European forecast was not tested by us, so it always counts as "baja".
#  - If no pollen type is selected the level only depends on the air forecast,
#    which we have NOT tested against measurements. The label then simply
#    follows how far ahead the day is (the list below: today, +1, +2, +3, +4).
CONFIDENCE_LABELS = ["baja", "media", "alta"]
CONFIDENCE_ALTA_FROM = 0.75
CONFIDENCE_MEDIA_FROM = 0.60
CONFIDENCE_BY_DAYS_AHEAD = ["alta", "alta", "media", "media", "baja"]

# Extra places for the location dropdown and for test_risk.py. The other
# municipalities in the dropdown come from the Comunidad air stations.
PLACES = {
    "Madrid (Centro)":   (40.4168, -3.7038),    # Puerta del Sol
    "Getafe":            (40.3083, -3.7327),
    "Alcalá de Henares": (40.4818, -3.3643),
    "Collado Villalba":  (40.6350, -4.0049),
}


# ----------------------------------------------------------------------------
# Forecast chart
# ----------------------------------------------------------------------------
# Where the page links to the model report (the info icon "¿Cómo predecimos?").
MODEL_REPORT_URL = "https://github.com/meganbougle/respira-mad/blob/main/MODEL_REPORT.md"
