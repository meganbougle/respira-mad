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

## DATA RULE (most important, permanent)

All pollen and air-quality data, **measured AND forecast**, come only from the
Comunidad de Madrid or the Ayuntamiento de Madrid (their stations and their
published historical data). Our own models make the forecasts from those data.
Only the **weather** (Open-Meteo: forecast, historical, issued-forecast
archive) and **geocoding** (Open-Meteo geocoding, `gazetteer.csv`) may come
from elsewhere. Never use CAMS / Open-Meteo air-quality or pollen forecasts,
or any other outside air or pollen source, anywhere (app, planner, chart,
assistant, texts).

## How to work on this project

- The author is not an experienced coder: keep code simple and well commented,
  explain each step briefly, and test as you go.
- Build one step at a time; don't build ahead of what was asked.
- **Permanent rule: design and test every change at phone width (390px)
  FIRST, then check laptop (1440x900).**
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
- **Step 4 (done): AI assistant** — `assistant.py`, `POST /api/chat`, chat panel in `static/`.

## Files

| File | What it is |
|---|---|
| `api.py` | FastAPI server: `/api/config, places, stations, risk, hours, planner, chart, freshness, chat, health`, and serves `static/` at `/` |
| `planner.py` | The 5-day planner logic |
| `chart.py` | The numbers behind the forecast line chart (`/api/chart`) |
| `air_model/`, `train_air_model.py` | Our air-quality forecast (NO2, O3, PM10, PM2.5, hourly): history download, features, live prediction, saved models, comparison tables; and the script that trains and tests it |
| `pollen_model/`, `train_pollen_model.py` | Our pollen forecast model: features, live prediction, saved models, backtest report; and the script that trains and tests it |
| `static/index.html`, `app.js`, `style.css` | The web app page (Spanish, mobile first, no build step) |
| `compare_models.py`, `MODEL_REPORT.md` | Pollen: compares seasonal naive, exponential smoothing, ARIMAX and gradient boosting (choose and calibrate on 2024, test on 2025) and saves what the app uses; the report in Spanish (pollen and air) |
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
| `backup/replay/2025-05-28/` | The demo day: pollen, Comunidad hourly air, weather files per place |
| `backup/replay/air_forecast_*.csv`, `pollen_*_2025-05-24.csv` | Our models' forecasts as they would have been made on the demo days |
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
| `air_model.predict.forecast(demo, moment)` | OUR model, from both networks' latest hourly readings + Open-Meteo weather forecast | FORECAST: hourly NO2, O3, PM10, PM2.5 per station, up to the tested horizon (96 h) |
| `load_replay_air(now)`, `load_replay_pollen()` | `backup/replay/2025-05-28/` | The demo day, same columns as the live loaders |

**Measurements vs forecasts.** Station data are official MEASUREMENTS.
Forecasts (our air and pollen models; Open-Meteo weather) are always labelled
as such (`kind = "FORECAST (model)"`, "PREVISIÓN" in the app). The level comes
from measurements; the forecasts feed the hourly strip, best windows, planner
and chart, and never replace a measurement. Open-Meteo weather is CC BY 4.0:
the footer credit is required.

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
  polen" and pollen does not change the level (no outside forecast, data rule).
- Overall level = the worse of air and pollen; the worse one gives the main
  reason and comes first in the recommendations.
- Location: nearest station with data per pollutant (also for the air
  forecast), nearest pollen trap, Open-Meteo weather for the exact point. No maximum distance; the distance is shown.
- Hourly strip and best windows: the next 24 hours starting at the current
  hour, day and night (`HOURS_AHEAD`); 3-hour windows ranked by level, then no
  rain, then lowest pollution, each labelled "hoy" or "mañana". On phones the
  strip scrolls sideways inside its own box. A measured pollen level is a floor for every hour of today.
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
- No emojis anywhere. The fallback uses the same Lucide icons as the web app,
  from `ICON_PATHS` in `config.py` (`icon_img()` for text, `icon_svg()` for
  the map); the star above the best hours is an Altair point shape.
- The freshness box is plain text lines so it wraps on a phone.

## Web app notes (`api.py`, `static/`)

- `api.py` adds no logic: each endpoint calls `risk.assess()` or the `data`
  loaders, so caching (15 min) and backups are the same as in Streamlit.
  `data.py` still uses Streamlit's cache, so `streamlit` must stay installed.
- `allergy_types` is sent as pollen-type keys separated by `|`; `demo=true`
  with `hour` selects the demo day.
- **No emojis, anywhere.** Every icon is a Lucide line icon (lucide.dev, ISC
  licence; 24×24 grid, 2px stroke) kept as a `<symbol>` in the sprite at the
  top of `static/index.html` and drawn with `icon("name")` in `app.js`
  (CSS class `.icon`). `i-pollen` is our own drawing (a pollen grain) for
  pollen stations. To add an icon, add its `<symbol>` to the sprite.
- The page keeps one `state` object and redraws from the server's answers;
  nothing reloads. It refreshes every 5 minutes (the server cache is 15).
- Map: CARTO Positron if the environment variable `CARTO_BASEMAP_KEY` is set
  (CARTO tiles need a free key since autumn 2026; without one they are
  watermarked). Otherwise OpenStreetMap tiles turned light grey with a CSS
  filter. Attribution on the map and in the footer either way.
- Map markers: air stations are circles in the official ICA colours; pollen
  stations are small diamonds with our pollen icon. The stations behind the
  current result (`highlightStations()` in `app.js`: one per pollutant from
  `/api/risk`, plus the pollen station when allergy is on) are bigger, with a
  thin line to the chosen point, and the rest are faded. Choosing a place from
  the list (or "my location") zooms the map to the point and those stations;
  a tap on the map keeps the zoom.
- Tapping a station opens the shared bottom sheet on phones (below 900px)
  and a normal popup on laptops (`showStation()`); both say which pollutants
  of the result come from it. The legend is a small "Leyenda" button at the
  top-left on phones (closed by default) and open at the bottom-left on laptops.
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

## Our air-quality forecast (`air_model/`, `train_air_model.py`)

- Hourly NO2, O3, PM10, PM2.5 at each of the 52 stations of both networks,
  1–120 hours ahead, from each station's latest reading. Data: the official
  hourly history since 2019 (`air_model/history.py`) + Open-Meteo weather
  (8 KMeans zones; the forecast as issued is used for testing).
- Split: train 2019–2023, choose on 2024, test on 2025. Compared "same hour
  yesterday" (baseline), "what is typical at that station and hour", SARIMAX
  (24 h season, weather exogenous; 4 stations only, it is slow) and gradient
  boosting (one model per pollutant, lagged features). Gradient boosting won
  on 2024 for all four; on 2025 SARIMAX was slightly better for NO2, O3 and
  PM2.5 on its 4 stations (reported, choice not changed).
- Horizon rule (ours): per day ahead on 2024, ≥10% lower error than the
  baseline and ≥5% lower than "typical"; shown up to the last day all four
  pass = **96 hours** (O3 fails day 5). Limit: on 2025, O3 from day 3 is
  slightly worse than "typical".
- In the app: `air_model/predict.py` — `forecast(demo, moment)` (cached
  15 min, backup `backup/air_forecast.csv`; keeps recent hours in
  `backup/air_recent.csv` because the Ayuntamiento file only has today) and
  `at_point(lat, lon, table)` (nearest station with a forecast per pollutant).
  Demo: stored forecasts in `backup/replay/air_forecast_<day>.csv`.
- To retrain: `.venv/bin/python train_air_model.py` (about an hour; downloads
  kept in `model_data/`), then `train_pollen_model.py` and `compare_models.py`
  (the pollen 2024 test reuses the air model's issued-weather files).

## Our pollen forecast (`pollen_model/`, `train_pollen_model.py`, `compare_models.py`)

- **Split (all models):** train up to 2023, CHOOSE (model per type, RELIABLE
  flags, interval calibration) on 2024, TEST on 2025, never used to decide.
- **Golden rule (keep it):** a forecast made on day D uses only what is known
  on day D. Pollen readings are at least 3 days old (publication delay), so
  lead day 5 is about 8 days after the last reading. Gradient-boosting inputs:
  day of year, warmth accumulated since 1 January, rain over the previous
  7/14/30/90 days, the weather forecast for the target day and the days in
  between, readings ≥3 days old (last value and its age, 7- and 14-day means,
  trend, network mean) and the same week one year earlier.
  `pollen_model/features.py` is shared by training and live prediction.
- **Weather for testing:** the forecast as issued. 2025: per station
  (Open-Meteo previous-runs archive). 2024: SIMPLIFICATION, the air model's
  zone forecasts shifted to each station (`pollen_model/issued_weather.py`),
  so `train_air_model.py` must have run first. Before 19 Jan 2024 no issued
  forecast exists and the weather that happened stands in.
- **`train_pollen_model.py`:** downloads, builds the training table, trains the
  old gradient-boosting CLASSIFIER (`models.joblib`, no longer used by the
  app, kept to compare with the median) and writes the 2024 and 2025 test
  rows to `model_data/`. Then run `compare_models.py`.
- **`compare_models.py` (16 Nov 2026 run):** on `log(1 + grains/m³)`, same
  rows, lead days 1–5, in season: seasonal naive, exponential smoothing,
  ARIMAX (Fourier year + weather + ARMA errors, orders by AIC, d = 0 by ADF,
  fitted per station on 2014–2023) and quantile gradient boosting. 2025
  accuracy on the official level / log MAE: naive 47% / 1.69, smoothing
  52% / 1.18, ARIMAX 59% / 0.93, gradient boosting 61% / 0.86.
- **Model per type** (`pollen_model/model_choice.json`; APP DECISION: chosen on
  2024 between ARIMAX and gradient boosting only, the models the app can run;
  best level accuracy, within one point the lower MAE): ARIMAX for Encinas y
  robles (Quercus), Olivo and Llantenes; gradient boosting for the other
  seven. Smoothing was best for Llantenes on 2024 but is a reference only.
  With the 2024 choice, 2025 accuracy is 60% (61% with gradient boosting
  everywhere; Olivo is the big miss: ARIMAX 53% vs 65%). Reported, not changed.
- **ONE SOURCE OF TRUTH:** the level everywhere (planner, chart, assistant) is
  the MEDIAN `q50` read against the official thresholds. Checked on 2025: the
  median is within ~1 point of the old classifier (61.3% vs 61.7% exact,
  68.1% vs 69.3% on the app's three levels).
- **RELIABLE (ours, decided on 2024):** per type and lead day, the chosen
  model gets the app's level (bajo / medio / alto-or-more) right ≥5 points
  more often than the baseline (last reading adjusted for the season), with a
  smaller mean error and ≥200 in-season station-days. 36 of 50 (leads 1–5).
  Never reliable: Llantenes; Olmos only at lead 5. Where not RELIABLE:
  "Sin previsión fiable" (no other forecast, data rule).
- **80% intervals (`q10`, `q90`), calibrated** by split conformal on 2024
  in-season days (margin per type, model and lead, `margin` in
  `model_choice.json`). Coverage in 2025 with the models used: 66% before,
  77% after (gradient boosting 72% → 80%, ARIMAX 54% → 79%); still short for
  olivo, chopos and ortigas.
- **Unstable ARIMAX:** a forecast with standard error > 3 on the log scale
  (`A.MAX_LOG_ERROR`, e.g. Quercus at Las Rozas, a station with gaps) counts
  as failed and gradient boosting answers, in the app and in the comparison.
- **Feature importance** (permutation, lead 3, 2024): time of year and
  accumulated warmth first, then recent readings, then the weather forecast
  (`pollen_model/plots/feature_importance.png`).
- **24 May 2025 check:** the demo forecast says grass "alto" at
  Madrid-Arganzuela for 24–28 May (median ~105–128 grains/m³); the 28th was
  really "muy alto". Planner, chart and assistant all show "alto".
- **In the app:** `pollen_model/predict.py` — `forecast(demo)` (cached 6 h,
  backup `backup/pollen_model_forecast.csv`; demo = the stored forecast as of
  24 May 2025, `backup/replay/pollen_forecast_2025-05-24.csv`, written by
  `compare_models.py`), `best_source()` and `recent_readings()` (backup
  `backup/pollen_recent.csv`; demo `backup/replay/pollen_recent_2025-05-24.csv`).
  `scikit-learn`, `joblib`, `numpy`, `statsmodels` and `scipy` are pinned in
  `requirements.txt` because the saved models only load with those versions.
- Weather downloads: stations within ~10 km share one series
  (`WEATHER_FROM` in `features.py`). A full retrain needs about 10,000
  Open-Meteo "calls": if it refuses, wait an hour and rerun (downloads are in
  `model_data/`, not committed).
- To retrain: `train_air_model.py` → `train_pollen_model.py` →
  `compare_models.py` (needs `matplotlib`; not in `requirements.txt`).
- Honest limits (also in the report): one year to choose and one to test;
  some intervals still too narrow; peaks are smoothed.

## 5-day planner (`planner.py`, `/api/planner`)

- Today + 4 days, each split into Mañana (7–13), Tarde (13–20), Noche (20–24)
  and Madrugada (0–7) (`PLANNER_BLOCKS` in `config.py`). The madrugada belongs
  to the night that starts the evening before, so on each row it uses the
  early hours (and the pollen forecast) of the next calendar day; the planner
  therefore loads one calendar day more than it shows. Everything is a forecast.
- Level of a block = the worse of the day's pollen level (only the selected
  allergy types, via `predict.best_source()`) and the block's air forecast
  (our air model at the nearest stations, same ICA bands and profile rules as
  the main card).
  Block value: highest hour for NO2, block average for O3 and particles
  (`PLANNER_BLOCK_VALUE`, a documented simplification).
- Horizon: our air forecast is shown only 96 hours ahead (where it clearly
  beat "same hour yesterday" in 2024, `air_model/meta.json`), so the last
  day has no air level; those cells use pollen and weather only and the
  planner says so.
- Today's pollen is never better than the latest official reading (same floor
  as the main card).
- Confidence label per day (`config.py`): with pollen types selected, from the
  2025 test accuracy of the chosen model for that lead day (alta ≥75%,
  media ≥60%, else baja).
  With no pollen type selected it only reflects days ahead.
- Demo mode shows the view as of 24 May 2025 (`PLANNER_DEMO_DATE`): pollen is
  the model's forecast with what was known that day; air is our air model's
  forecast made at 07:00 that day (`backup/replay/air_forecast_2025-05-24.csv`).
- Page: a 5-column grid (day + four blocks) under the main card, which fits
  390px without sideways scrolling; tapping a
  cell opens the shared explanation panel (bottom sheet on phones, popover on
  laptops). Explanatory notes across the page sit behind small "i" icons that
  open the same panel on tap (`info` object in `static/app.js`).
- `warm_backup.py` also saves the planner's 5-day forecasts for the 29
  dropdown places (live and demo) and the model's forecast.

## Forecast line chart (`chart.py`, `/api/chart`, "Evolución y previsión")

- Under the planner. A "Polen / Aire" switch (`chartMode` in `static/app.js`);
  it starts on Polen when pollen types are selected, otherwise on Aire.
- **Polen:** nearest pollen station with a forecast (the planner's station).
  Last 7 days of official readings (solid line) + today and the next 5 days
  (dashed line = median `q50`, shaded = 80% interval `q10`–`q90`). The days
  between the last reading and today have no data (publication delay) and are
  joined by a dotted line. Hollow markers = that type + lead day is not
  RELIABLE. Demo mode: view as of 24 May 2025, with crosses for what was
  measured afterwards.
- **Vertical scale = the official level** (APP DECISION): the four levels get
  equal height and a value sits inside its band in proportion to that pollen
  type's own thresholds, so types with different thresholds share one chart.
  Up to 3 lines at once (`CHART_MAX_LINES`), each with its own colour and
  marker shape (colours checked for colour-blind separation); tapping a type
  chip shows it and shades its interval.
- **Aire:** our hourly air forecast (same as the planner, up to 96 h) of the
  pollutant that reaches the worst ICA category, over the
  official bands of that pollutant in the official colours.
- Hand-drawn SVG, no chart library. Tap (or hover) a day or hour to read its
  values in the box below the chart.
- The info icon opens "¿Cómo predecimos?" (`chartInfo()`): model per pollen
  type (from `/api/config`), the data used, the delay, our air model, and a
  link to `MODEL_REPORT.md` on GitHub (`MODEL_REPORT_URL` in `config.py`).

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
  `get_risk`, `get_best_hours`, `get_planner`, `get_pollen_forecast`. In demo
  mode `get_risk` and `get_best_hours` use the 28 May 2025 replay data;
  `get_planner` and `get_pollen_forecast` use the planner's view as of 24 May
  2025.
- `get_planner` and `get_pollen_forecast` both call `planner.plan()`, so the
  assistant says exactly what the planner shows: our pollen model where it is
  RELIABLE, otherwise "sin previsión fiable", each with the day's confidence label.
- The system prompt is in Spanish: answer only from tool results, cite station
  or source and data time, flag provisional and forecast data, at most ~5
  sentences, no diagnoses or medication advice, 112 for emergencies, decline
  off-topic questions. For multi-day forecasts it must say they are less
  certain further ahead and mention the day's confidence level and the source
  of the pollen forecast.
- Any hour: `get_risk` takes `hour` (and optionally `day`: hoy/mañana) and
  returns the forecast for that hour, day or night, with the official text for
  that hour's category; without `day` it means the next time it is that hour.
- Profile from the chat: if the person says something about themselves ("soy
  asmática", "tengo 80 años", "mi hijo es alérgico al polen") that overrides
  the panel, and the answer says which profile it used. Pollen allergy without
  a named type uses the panel's types, or all ten if the panel has none.
- No emojis: the prompt forbids them and `_no_emojis()` strips any that slip
  through before the reply is sent.
- No advice of its own: the prompt limits an answer to the level, the reason
  from the data, the official text for the time asked about, and "consulta con
  tu médico" / 112. The model still occasionally adds a stray remark.
- Limits: `max_tokens` 600 (400 cut off answers that quote the long official
  pollen text), last 6 messages only, 20 messages per visitor per
  hour (in memory), at most 5 tool rounds. Thinking is off so the 400 tokens go
  to the answer (`AI_THINKING` in `config.py`).
- Costs: every chat logs its tokens and a running total (lines starting
  `CHAT` in the server log), and `/api/chat/usage` shows totals since the
  server started. The dollar figure is an estimate from list prices in
  `config.py`.
- The page sends the panel's profile, place, allergy types and demo state as
  context. Answers are labelled "Generado por IA con datos oficiales".
- `test_chat.py` asks the 3 suggested questions live and in demo mode, a
  planner question live and in demo mode, plus one off-topic and one medical
  question, and night-time / stated-profile questions (needs a real key). Runs
  on 16 Nov 2026: all 14 answered with the right tools, about 14,000 input
  tokens and $0.0016 per question (list-price estimate).
- The Streamlit fallback (`app.py`) has no assistant; its placeholder never
  shows a chat.
