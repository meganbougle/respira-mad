# Respira MAD

Streamlit app (Python, interface in Spanish) for the **DataMad 2026 hackathon
— deadline 20 Nov 2026**. It tells vulnerable people (asthma, pollen allergy,
older people, young children) whether it is safe to be outdoors **right now**
in the Comunidad de Madrid, by combining live air quality, pollen levels and
weather.

There are two front ends over the same logic:
- **Web app (main):** `api.py` (FastAPI, JSON) + `static/` (plain HTML, CSS,
  JavaScript, Leaflet from a CDN). Deployed on **Render** (free tier) with
  `render.yaml`.
- **Streamlit app (fallback):** `app.py`, for **Streamlit Community Cloud**.

Repo: https://github.com/meganbougle/respira-mad (public, branch `main`).

## How to work on this project

- The author is not an experienced coder: keep code simple and well commented,
  explain each step briefly, and test as you go.
- Build one step at a time; don't build ahead of what was asked.
- Run things with the project's virtual environment:
  - `.venv/bin/uvicorn api:app --port 8000` — the web app, then open http://localhost:8000
  - `.venv/bin/streamlit run app.py` — the Streamlit fallback
  - `.venv/bin/python test_data.py` — checks every data source is live
  - `.venv/bin/python test_risk.py` — risk result for 4 places, live and demo day
  - `.venv/bin/python test_timestamps.py` — checks no reading is ever stamped in the future
  - `.venv/bin/python warm_backup.py` — refreshes every backup (run before the demo, then commit `backup/`)
- Dependencies go in `requirements.txt` with pinned versions (Streamlit Cloud
  installs from it). Local Python is 3.13.

## Status

- **Step 1 (done): data loading** — `data.py`.
- **Step 2 (done): data checks, forecast data, risk logic** — `config.py`, `risk.py`.
- **Step 3 (done): the app** — `app.py`, in Spanish, with demo mode.
- **Step 3b (done): web app UI** — `api.py` + `static/`, replacing only the
  presentation layer. `data.py`, `risk.py`, `config.py` and `backup/` were not
  changed for it and must stay shared by both front ends.
- **Step 4 (done, untested against the real model until a key is added): AI
  assistant** — `assistant.py`, `POST /api/chat`, chat panel in `static/`.

## Files

| File | What it is |
|---|---|
| `api.py` | FastAPI server: `/api/config, places, stations, risk, hours, freshness, health`, and serves `static/` at `/` |
| `static/index.html`, `app.js`, `style.css` | The web app page (Spanish, mobile first, no build step) |
| `assistant.py` | The AI assistant: system prompt, four tools, limits, cost log |
| `test_chat.py`, `test_timestamps.py` | Assistant test questions (needs a key); future-timestamp checks |
| `gazetteer.csv`, `build_gazetteer.py` | Place names for "Cerca de …" and the one-off script that builds them |
| `render.yaml` | Render Blueprint: free web service, `uvicorn api:app`, Python 3.13.1, Frankfurt; keys typed in the dashboard |
| `app.py` | Streamlit fallback: sidebar, result card, chart, map, freshness, footer |
| `data.py` | One loader function per data source, cached 15 min, with backup |
| `config.py` | Every threshold, official text and setting, each with its source |
| `risk.py` | `assess(lat, lon, profile, allergy_types, now, demo)` → level, reason, official recommendations, best windows |
| `test_data.py`, `test_risk.py` | Print-style tests |
| `check_stations.py` | Raw hourly readings of 3 stations, to compare by hand with the official websites |
| `warm_backup.py` | Refreshes all backups, including forecasts for the 29 dropdown places |
| `build_replay.py` | One-off: ranks the pollen history and saves the demo day to `backup/replay/` |
| `backup/*.csv` | Last good copy of each source, used if a live feed fails |
| `backup/replay/2025-05-28/` | The demo day: pollen, Comunidad hourly air, weather, Open-Meteo files per place |
| `.streamlit/config.toml` | Light theme (committed). `.streamlit/secrets.toml` is never committed. |
| `requirements.txt`, `.gitignore` | Libraries; ignores `.venv/` and secrets |

## Data sources (`data.py`)

| Function | Source | Notes |
|---|---|---|
| `load_comunidad_air()` | Comunidad CKAN, `calidad_aire_datos_dia` + `calidad_aire_estaciones` | 28 stations, hourly, usually 1–2 h behind. Station name = town. |
| `load_city_air()` | Ayuntamiento, dataset 212531 (real time) + 212629 (stations) | 23 stations reporting, about 1 h behind |
| `load_air()` | Both merged | Columns: `station, source, lat, lon, pollutant, value, timestamp` plus `ica_value, ica_hours, flag` |
| `load_pollen()` | Comunidad CKAN, `mediciones_polen` + `captadores_polen` | Latest reading day per trap, grains/m³. Drops traps whose reading is older than 10 days; keeps `timestamp` and `age_days`. `load_pollen_all()` keeps everything. |
| `load_weather()` | Comunidad CKAN, `calidad_aire_datos_meteo_mes` | Up to a day old; not used by the app |
| `load_forecast_weather(lat, lon, day=None)` | Open-Meteo forecast API (archive API for a past `day`) | FORECAST: hourly temperature, rain, wind, humidity |
| `load_forecast_air(lat, lon, day=None)` | Open-Meteo air quality API (CAMS model) | FORECAST: hourly NO2, O3, PM10, PM2.5 + 6 pollen types |
| `load_replay_air(now)`, `load_replay_pollen()` | `backup/replay/2025-05-28/` | The demo day, same columns as the live loaders |

**Measurements vs forecasts.** Station data are official MEASUREMENTS.
Open-Meteo data are a model FORECAST and are always labelled as such
(`kind = "FORECAST (model)"`, "PREVISIÓN" in the app). The level comes from
measurements; the forecast feeds the hourly chart and best windows, and
replaces pollen only when no station has a recent reading. Open-Meteo data are
CC BY 4.0: the footer credit is required.

Things worth knowing about the feeds:

- CKAN API base: `https://datos.comunidad.madrid/api/3/action/`. `data.py`
  calls `package_show` to find each dataset's CSV URL.
- The hourly files are "wide": one row per station + magnitude + day, columns
  `h01..h24` (values) and `v01..v24` (flags: `V` valid, `T` provisional,
  `N` not valid). `h01` is the hour ending 01:00. Times are Madrid local time.
- Hours that have not happened yet are always dropped, whatever their flag
  (`_hourly_long` and `_drop_future` in `data.py`). `h24` is 00:00 of the next
  day. A reading stamped 16/11 00:00 was seen in the app at 18:05 on 15/11.
- All timestamps are Madrid local time with no time zone attached. The web page
  shows them as text and never converts them, and "hace X min" is computed on
  the server.
- Pollutant codes (same in both networks): 8 = NO2, 9 = PM2.5, 10 = PM10, 14 = O3.
- Not every station measures every pollutant (e.g. PM2.5 at 15 of 28).
- Very low values (PM10 = 1, NO2 = 1) were checked on 15 Nov 2026 and are real.
- The Comunidad "today" file is flagged `T` (provisional) for every hour; it is
  validated later. We keep `T` readings (dropping them would leave no live
  Comunidad data) and the app labels them "Provisional".
- Comunidad files use decimal commas (mostly); the pollen-trap file is Latin-1.
- **Pollen is not real time**: readings arrive 3–7 days late. The app always
  shows the reading date and its age.
- Each loaded table has `df.attrs["live"]` (False = from `backup/`) and
  `df.attrs["fetched_at"]`.
- Forecast backups are per location, rounded to 2 decimals. `warm_backup.py`
  saves them for every dropdown place; a clicked map point has none.
- Streamlit Cloud's disk is temporary, so `backup/` must be **committed to
  git** for the fallback (and the demo mode) to work in the deployed app.
- The Ayuntamiento has not published 2025 hourly history, so the demo day has
  only the 28 Comunidad stations.

## Risk logic decisions (`config.py`, `risk.py`)

`config.py` is the single place for thresholds and texts. Nothing in it is
invented: each block links its official source, and every simplification or
choice of ours is marked SIMPLIFICATION or APP DECISION. Keep it that way.

**Air — Spain's national index (ICA).** Source: Resolución de 2 de septiembre
de 2020 (BOE-A-2020-10426). Six categories, bands for NO2, O3, PM10, PM2.5,
and the official advice for "población general" and "grupos de riesgo y
personas sensibles", copied word for word in Spanish and shown literally.

- Official averaging: NO2 last hour, O3 8-hour, PM 24-hour moving averages.
  Simplification: the real-time files only hold the current day, so our
  averages use the hours since midnight (`ica_hours`).
- SO2 is left out. No CAMS gap-filling: the next nearest station is used.

**Pollen — Red Palinocam levels.** Four levels (bajo, medio, alto, muy alto),
thresholds for 10 pollen types from the Ayuntamiento/UCM report of November
2025 (2010–2019 data). The other 17 types have no threshold and are not used.
Pollen advice is the Comunidad leaflet "Lo que debes conocer sobre la alergia
al polen", word for word.

**App decisions (ours, not official):**

- Three levels: "Puedes salir" / "Precaución" / "Mejor quédate en casa", mapped
  from the wording of the official advice. General: Precaución at Desfavorable,
  stay in from Muy desfavorable. Sensitive: Precaución at Regular, stay in from
  Desfavorable.
- Profiles: General, Asma / respiratorio, Persona mayor, Niño/a. The last three
  get the sensitive-group advice. For older people and children the app shows a
  note that this is our extension, not the official text.
- Pollen allergy is a checkbox plus a multi-select of the 10 pollen types with
  thresholds, not a profile. Rule: medio = Precaución, alto or muy alto = stay
  in, only for the selected types. Without the checkbox pollen never changes
  the level (asthma without allergy uses air only).
- Pollen cutoff 10 days. If no station qualifies: "Sin lectura reciente de
  polen", and the Open-Meteo grass/olive forecast (average of today's hours) is
  used, labelled as forecast.
- Overall level = the worse of air and pollen; the worse one gives the main
  reason and comes first in the recommendations.
- Location: nearest station with data per pollutant, nearest pollen trap,
  Open-Meteo for the exact point. No maximum distance; the distance is shown.
- Best windows: forecast only, 3-hour windows between 07:00 and 22:00
  (tomorrow's if today is nearly over), ranked by level, then no rain, then
  lowest pollution. A measured pollen level is a floor for every hour of today.
  Windows at "stay in" are never presented as recommended.
- Weather has no thresholds of its own; temperature and rain are shown with
  each window.

**Demo mode.** "Modo demostración: 28 mayo 2025" replays the day with the
highest grass pollen in the history (May 1993 to now): network average 392
grains/m³. An hour slider (default 10:00) sets the time of day.

## App notes (`app.py`)

- `streamlit-folium` for the map (click = choose a point; the dropdown resets
  it), `streamlit-autorefresh` for the 15-minute reload, Altair for the chart.
- Map: OpenStreetMap background, air stations coloured with the official ICA
  colours, 🌼 pollen stations, blue marker for the chosen point.
- Colours never carry meaning alone: every level has an icon and a label.
- The freshness box is plain text lines so it wraps on a phone.

## Web app notes (`api.py`, `static/`)

- `api.py` adds no logic: each endpoint calls `risk.assess()` or the `data`
  loaders, so caching (15 min) and backups are the same as in Streamlit.
  `data.py` still uses Streamlit's cache, so `streamlit` must stay installed.
- `allergy_types` is sent as pollen-type keys separated by `|`; `demo=true`
  with `hour` selects the demo day.
- The page keeps one `state` object and redraws from the server's answers;
  nothing reloads. It refreshes every 5 minutes (the server cache is 15).
- Map: CARTO Positron if the environment variable `CARTO_BASEMAP_KEY` is set
  (CARTO tiles need a free key since autumn 2026; without one they are
  watermarked). Otherwise OpenStreetMap tiles turned light grey with a CSS
  filter. Attribution on the map and in the footer either way.
- A point is named the same everywhere by `risk.place_name()`: a dropdown
  place keeps its name; anything else is "Cerca de <nearest municipality or
  Madrid district>", from `gazetteer.csv` (built by `build_gazetteer.py`: 21
  districts from the city's district offices, 160 municipalities geocoded with
  Open-Meteo; 17 small villages were not found and fall to their neighbour).
- Panel order: profile, result card (reason + official recommendation + source
  inside it), hourly strip, measurements. On a 1440×900 screen the card and
  the strip are visible without scrolling, including demo mode with allergy on.
- The level colour fades via two CSS variables set on the result card.
- "Usar mi ubicación" needs HTTPS (or localhost) and warns if the position is
  outside a rough box around the Comunidad de Madrid.
- Render free tier sleeps after 15 minutes without visits; the first visit
  after that takes about a minute. Open the site before a demo.

## AI assistant "Pregúntale a Respira" (`assistant.py`, web app only)

- `POST /api/chat` in `api.py` calls `assistant.answer()`. Model
  `claude-haiku-5-5` through the `anthropic` Python SDK, with a hand-written
  tool loop. Settings are in `config.py` (section "AI assistant").
- The key comes from the environment variable `ANTHROPIC_API_KEY` (on Render:
  set in the dashboard; locally: a `.env` file, which git ignores). **Never
  commit a key.** If `AI_ASSISTANT_ENABLED` is False the chat button is hidden;
  if the key is missing or the API fails the chat says so and the rest of the
  app keeps working (`answer()` never raises).
- Tools, all calling our own functions: `find_place` (29 dropdown places, then
  `gazetteer.csv`, then Open-Meteo geocoding limited to the region),
  `get_risk`, `get_best_hours`, `get_pollen_forecast`. In demo mode every tool
  uses the 28 May 2025 replay data.
- The pollen forecast source is one swappable function (`POLLEN_FORECAST` in
  `assistant.py`), currently Open-Meteo/CAMS grass and olive, today and
  tomorrow only. Our own pollen model can replace it later.
- The system prompt is in Spanish: answer only from tool results, cite station
  or source and data time, flag provisional and forecast data, at most ~5
  sentences, no diagnoses or medication advice, 112 for emergencies, decline
  off-topic questions.
- Limits: `max_tokens` 400, last 6 messages only, 20 messages per visitor per
  hour (in memory), at most 5 tool rounds. Thinking is off so the 400 tokens go
  to the answer (`AI_THINKING` in `config.py`).
- Costs: every chat logs its tokens and a running total (lines starting
  `CHAT` in the server log), and `/api/chat/usage` shows totals since the
  server started. The dollar figure is an estimate from list prices in
  `config.py`.
- The page sends the panel's profile, place, allergy types and demo state as
  context. Answers are labelled "Generado por IA con datos oficiales".
- `test_chat.py` asks the 3 suggested questions live and in demo mode, plus one
  off-topic and one medical question (needs a real key).
- The Streamlit fallback (`app.py`) has no assistant; its placeholder never
  shows a chat.
