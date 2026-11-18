"""
api.py - a small web server that gives the web app its data as JSON.

Run it with:   .venv/bin/uvicorn api:app --port 8000
Then open:     http://localhost:8000

It adds NO logic of its own: every answer comes from data.py (loading,
15-minute caching, backups), risk.py (decisions) and config.py (thresholds
and official texts). The web page itself is in the static/ folder.

Addresses:
    /                 the web app (static/index.html)
    /api/config       profiles, pollen types, level names and colours
    /api/places       the places for the location dropdown
    /api/stations     air stations with their ICA category + pollen stations
    /api/risk         level, reason, official recommendation, measurements used
    /api/hours        hour-by-hour levels and best windows (forecast)
    /api/planner      today + 4 days in four parts of the day (forecast, with our pollen and air models)
    /api/chart        the forecast line chart: recent readings + pollen forecast with interval, or the air forecast
    /api/freshness    each source, its timestamp, minutes ago, provisional flag
    /api/chat         (POST) the AI assistant "Pregúntale a Respira" (see assistant.py)
    /api/chat/usage   how many tokens the assistant has used since the server started
    /api/health       "ok" (used by the hosting service to check the app is up)
"""

import ctypes
import gc
import logging
import math
import os
import threading
import time
import warnings
from pathlib import Path

import pandas as pd
import streamlit  # noqa: F401  (data.py uses its cache; imported first so we can quiet it)
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Streamlit's cache works fine outside "streamlit run", but it complains
# about it in the log. Hide those messages.
for name in ("streamlit.runtime.caching.cache_data_api",
             "streamlit.runtime.scriptrunner_utils.script_run_context"):
    logging.getLogger(name).setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*ScriptRunContext.*")

import assistant  # noqa: E402
from air_model import predict as air_predict  # noqa: E402
from pollen_model import predict as pollen_predict  # noqa: E402
import chart      # noqa: E402
import config     # noqa: E402
import data       # noqa: E402
import planner    # noqa: E402
import risk       # noqa: E402
import weather    # noqa: E402

# the server's own log (Render shows it in the dashboard). Users never see these details.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("respira.api")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Respira MAD")


# ----------------------------------------------------------------------------
# Errors: visitors only ever get a short Spanish message; details go to the log
# ----------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
def bad_request(request: Request, error: RequestValidationError):
    log.warning("Bad request %s: %s", request.url.path, error.errors())
    return JSONResponse(status_code=400, content={"detail": "La petición no es válida."})


@app.exception_handler(Exception)
def unexpected_error(request: Request, error: Exception):
    log.exception("Unexpected error in %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Algo ha fallado. Inténtalo de nuevo en unos minutos."})


# ----------------------------------------------------------------------------
# Background job, every 10 minutes: refresh the weather of the 29 dropdown
# places and every station when it is over 1 hour old (a few batched requests,
# weather.py), and recompute our air (15-min cache) and pollen (6-h cache)
# forecasts when they expire, so visitors almost never wait for them.
# ----------------------------------------------------------------------------
PREFETCH_EVERY_SECONDS = 10 * 60      # the loop wakes every 10 minutes; the weather itself is only refreshed hourly


def release_memory():
    """
    MEMORY (Render free plan, 512 MB): after a big job, collect Python's
    garbage and, on Linux, hand the freed memory back to the system
    (malloc_trim). Without the second step the process keeps its peak size.
    """
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass                                  # not Linux (e.g. a Mac): nothing more to do


def _prefetch_loop():
    while True:
        try:
            asked, done = weather.prefetch(risk.weather_points())
            log.info("Weather prefetch: %d cells asked, %d refreshed", asked, done)
        except Exception:
            log.exception("Weather prefetch failed (the cached copies are still used)")
        # keep our two forecasts warm too (each is cached; this only recomputes one that has expired),
        # so a visitor never waits for the pollen model (about 20 s) or the air model
        for name, warm in (("air", lambda: air_predict.forecast(False, None)), ("pollen", lambda: pollen_predict.forecast(False))):
            try:
                warm()
            except Exception:
                log.exception("Could not prepare the %s forecast in advance", name)
            release_memory()
        time.sleep(PREFETCH_EVERY_SECONDS)


@app.on_event("startup")
def start_prefetch():
    threading.Thread(target=_prefetch_loop, name="weather-prefetch", daemon=True).start()


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def clean(value):
    """Turn pandas/numpy values into plain ones that can be sent as JSON."""
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat(timespec="minutes")
    if hasattr(value, "item"):                 # numpy number -> Python number
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def the_moment(demo, hour):
    """The time the app is 'looking at': now, or an hour of the demo day."""
    if demo:
        hour = config.REPLAY_DEFAULT_HOUR if hour is None else hour
        return pd.Timestamp(config.REPLAY_DATE) + pd.Timedelta(hours=hour)
    return risk.madrid_now()


def ago_text(minutes):
    """'hace 35 min', 'hace 2 h 10 min' or 'hace 6 días'."""
    if minutes < 60:
        return f"hace {minutes} min"
    if minutes < 48 * 60:
        return f"hace {minutes // 60} h {minutes % 60} min"
    return f"hace {minutes // (24 * 60)} días"


def minutes_between(earlier, later):
    return max(int((later - earlier).total_seconds() // 60), 0)


def assess(lat, lon, profile, allergy_types, demo, hour):
    """Check the inputs and call risk.assess()."""
    if profile not in config.PROFILES:
        raise HTTPException(400, f"Perfil desconocido: {profile}")
    types = tuple(t for t in allergy_types.split("|") if t) if allergy_types else ()
    unknown = [t for t in types if t not in config.POLLEN_THRESHOLDS]
    if unknown:
        raise HTTPException(400, f"Tipo de polen desconocido: {unknown[0]}")
    return risk.assess(lat, lon, profile, types, the_moment(demo, hour), demo)


def air_table(demo, moment):
    return data.load_replay_air(moment) if demo else data.load_air()


def pollen_table(demo, moment):
    return data.load_replay_pollen() if demo else data.load_pollen(today=moment.normalize())


# Reusable descriptions of the address parameters.
LAT = Query(..., ge=-90, le=90)
LON = Query(..., ge=-180, le=180)
HOUR = Query(None, ge=0, le=23, description="Only used in demo mode")
TYPES = Query("", description="Pollen types separated by | , e.g. Gramíneas|Olivo")


# ----------------------------------------------------------------------------
# The addresses
# ----------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


STARTED = time.time()                 # when this server process started (for /api/memory)


def _read_number(path):
    """A number from a Linux system file, or None (e.g. on a Mac)."""
    try:
        text = Path(path).read_text().strip()
        return None if text == "max" else int(text)
    except (OSError, ValueError):
        return None


@app.get("/api/memory")
def memory():
    """
    Memory in MB, to watch the Render free plan's 512 MB limit (Linux only):
    this process now and at its peak (/proc/self/status), and the whole
    container now, at its peak and its limit (cgroup files). Read-only.
    """
    status = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                status[line.split(":")[0]] = round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    to_mb = lambda value: None if value is None else round(value / 1048576)
    return {
        "process_now_mb": status.get("VmRSS"),
        "process_peak_mb": status.get("VmHWM"),
        "container_now_mb": to_mb(_read_number("/sys/fs/cgroup/memory.current") or _read_number("/sys/fs/cgroup/memory/memory.usage_in_bytes")),
        "container_peak_mb": to_mb(_read_number("/sys/fs/cgroup/memory.peak") or _read_number("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")),
        "container_limit_mb": to_mb(_read_number("/sys/fs/cgroup/memory.max") or _read_number("/sys/fs/cgroup/memory/memory.limit_in_bytes")),
        "hours_since_start": round((time.time() - STARTED) / 3600, 2),
    }


@app.get("/api/config")
def get_config():
    """Everything the page needs to draw its buttons and colours."""
    return {
        "profiles": [{"key": key, "label": p["label"], "short": p["short"]} for key, p in config.PROFILES.items()],
        "doubtful_color": config.DOUBTFUL_COLOR,
        # the pollen types selected by default (official source in config.py)
        "pollen_default": config.POLLEN_DEFAULT_TYPES,
        "pollen_default_source": config.POLLEN_DEFAULT_SOURCE, "pollen_default_url": config.POLLEN_DEFAULT_URL,
        # each pollen type with the forecast model that won the 2025 comparison (MODEL_REPORT.md)
        "pollen_types": [{"key": key, "label": label, "model": chart.model_label(key)}
                         for key, label in config.POLLEN_NAMES.items()],
        "model_report_url": config.MODEL_REPORT_URL,
        "levels": [{"label": label, "color": color, "text_color": text}
                   for label, color, text in zip(config.LEVEL_LABELS, config.LEVEL_COLORS, config.LEVEL_TEXT_COLORS)],
        "ica_categories": [{"name": name, "color": color}
                           for name, color in zip(config.ICA_CATEGORIES, config.ICA_COLORS)],
        "demo": {"date": config.REPLAY_DATE, "default_hour": config.REPLAY_DEFAULT_HOUR,
                 "first_hour": 0, "last_hour": 23},
        "ai_assistant_enabled": config.AI_ASSISTANT_ENABLED,           # show the chat button?
        "ai_assistant_available": assistant.is_available(),            # ...and is there a key?
        # Optional key for the CARTO Positron basemap (free, from carto.com/basemaps/apikey).
        # It is used by the visitor's browser, so it is not a secret. Without it the map
        # uses OpenStreetMap tiles in muted grey.
        "carto_basemap_key": os.environ.get("CARTO_BASEMAP_KEY", ""),
    }


def dropdown_places():
    """The places of the location dropdown: {name: (lat, lon)}."""
    try:
        air = data.load_air()
    except Exception:            # live feed and backup both failed: use the demo day's stations
        air = data.load_replay_air(the_moment(True, 12))
    return risk.list_places(air)


@app.get("/api/places")
def get_places():
    """The places of the location dropdown."""
    return [{"name": name, "lat": lat, "lon": lon} for name, (lat, lon) in dropdown_places().items()]


@app.get("/api/stations")
def get_stations(demo: bool = False, hour: int = HOUR):
    """All air stations with their ICA category, and the pollen stations."""
    moment = the_moment(demo, hour)
    notes = []

    air_stations = []
    try:
        for _, s in risk.station_categories(air_table(demo, moment), moment).iterrows():
            has_category = s["category"] is not None and not pd.isna(s["category"])
            air_stations.append({
                "station": s["station"], "source": s["source"], "lat": s["lat"], "lon": s["lon"],
                "category": int(s["category"]) if has_category else None,
                "category_name": "Dato dudoso" if s["doubtful"] else s["category_name"],
                # a station with a doubtful reading is grey on the map (its other pollutants are still listed and used)
                "color": config.DOUBTFUL_COLOR if s["doubtful"] or not has_category else config.ICA_COLORS[int(s["category"])],
                "details": s["details"], "timestamp": s["timestamp"],
                "doubtful": bool(s["doubtful"]), "stale": bool(s["stale"]),
            })
    except Exception as error:
        notes.append(data.problem("Sin mediciones de calidad del aire.", error))

    pollen_stations = []
    try:
        pollen = pollen_table(demo, moment)
        for station, group in pollen.groupby("station"):
            info = risk.pollen_near(group["lat"].iloc[0], group["lon"].iloc[0], group)
            pollen_stations.append({
                "station": station, "lat": group["lat"].iloc[0], "lon": group["lon"].iloc[0],
                "reading_date": info["reading_date"], "age_days": info["age_days"],
                "types": [{"name": t["name"], "value": t["value"], "level_name": t["level_name"]}
                          for t in info["types"][:4]],
            })
    except Exception as error:
        notes.append(data.problem("Sin mediciones de polen.", error))

    return clean({"air": air_stations, "pollen": pollen_stations, "notes": notes, "time": moment})


@app.get("/api/risk")
def get_risk(lat: float = LAT, lon: float = LON, profile: str = "general",
             allergy_types: str = TYPES, demo: bool = False, hour: int = HOUR):
    """Level, reason, official recommendation text and the measurements used."""
    result = assess(lat, lon, profile, allergy_types, demo, hour)
    index = result["level_index"]
    pollen = result["pollen"]
    selected = set(allergy_types.split("|")) if allergy_types else set()
    return clean({
        "level_index": index,
        "level_label": config.LEVEL_LABELS[index] if index is not None else "Sin datos",
        "headline": result["headline"] or (config.LEVEL_LABELS[index] if index is not None else "Sin datos"),
        "cause": result["cause"],
        "profile_label": config.PROFILES[profile]["label"],
        "place_label": risk.place_name(lat, lon, dropdown_places()),   # the same name everywhere
        "reason": result["reason"],
        "recommendations": [{"source": source, "text": text} for source, text in result["recommendations"]],
        "profile_note": result["profile_note"],
        "notes": result["notes"],
        "air": [{**reading, "color": config.ICA_COLORS[reading["category"]]} for reading in result["air"]],
        "pollen": None if pollen is None else {
            "kind": pollen["kind"],                       # "measurement" or "forecast"
            "station": pollen["station"], "distance_km": pollen["distance_km"],
            "reading_date": pollen["reading_date"], "age_days": pollen["age_days"],
            "types": [{"key": t["pollen_type"], "name": t["name"], "value": t["value"],
                       "level": t["level"], "level_name": t["level_name"],
                       "counts_for_you": t["pollen_type"] in selected} for t in pollen["types"]],
        },
        "demo": demo,
        "time": result["time"],
    })


@app.get("/api/hours")
def get_hours(lat: float = LAT, lon: float = LON, profile: str = "general",
              allergy_types: str = TYPES, demo: bool = False, hour: int = HOUR):
    """Hour-by-hour levels and the best windows. This is a FORECAST (model)."""
    result = assess(lat, lon, profile, allergy_types, demo, hour)
    now = result["time"]
    # windows worth recommending (a "stay in" window is never a best window)
    windows = [w for w in result["windows"] if w["level_index"] < 2]

    hours = []
    if result["hours"] is not None:
        for _, row in result["hours"].iterrows():
            moment = row["timestamp"]
            hours.append({
                "time": moment, "hour": moment.hour,
                "level_index": row["level"], "level_label": config.LEVEL_LABELS[row["level"]],
                "limited_by": row["limited_by"],
                "day": "hoy" if moment.normalize() == now.normalize() else "mañana",
                "best": any(w["start"] <= moment < w["end"] for w in windows),
                "NO2": row["NO2"], "O3": row["O3"], "PM10": row["PM10"], "PM2.5": row["PM2.5"],
                "temperature_c": row["temperature_c"], "rain_mm": row["rain_mm"],
            })
    return clean({
        "kind": "PREVISIÓN de nuestros modelos (hecha con las mediciones oficiales), no mediciones",
        # which station each pollutant's forecast comes from
        "air_stations": result["forecast"].attrs.get("stations") if result["forecast"] is not None else None,
        "hours": hours,                              # the next 24 hours, starting at the current hour
        "windows": [{"day": w["day"], "start": w["start"], "end": w["end"], "level_index": w["level_index"],
                     "temperature_c": w["temperature_c"], "rain_mm": w["rain_mm"]} for w in windows],
        "no_good_window": bool(result["windows"]) and not windows,
        "notes": [n for n in result["notes"] if "previsión" in n.lower()],
        "time": now,
    })


@app.get("/api/planner")
def get_planner(lat: float = LAT, lon: float = LON, profile: str = "general",
                allergy_types: str = TYPES, demo: bool = False):
    """
    The 5-day planner: today + 4 days, each split into mañana, tarde and noche.
    In demo mode it is the view as of 24 May 2025 (config.PLANNER_DEMO_DATE).
    """
    if profile not in config.PROFILES:
        raise HTTPException(400, f"Perfil desconocido: {profile}")
    types = tuple(t for t in allergy_types.split("|") if t) if allergy_types else ()
    unknown = [t for t in types if t not in config.POLLEN_THRESHOLDS]
    if unknown:
        raise HTTPException(400, f"Tipo de polen desconocido: {unknown[0]}")
    result = planner.plan(lat, lon, profile, types, None, demo)
    result["blocks"] = [{"name": name, "hours": f"{first}–{last} h"} for name, first, last, _ in config.PLANNER_BLOCKS]
    result["place_label"] = risk.place_name(lat, lon, dropdown_places())
    result["profile_label"] = config.PROFILES[profile]["label"]
    return clean(result)


@app.get("/api/chart")
def get_chart(lat: float = LAT, lon: float = LON, allergy_types: str = TYPES, demo: bool = False):
    """
    The forecast line chart under the planner (see chart.py): for the selected
    pollen types, the readings of the last 7 days and the forecast for the
    next 5 with its prediction interval; and the hourly air forecast of the
    main pollutant. In demo mode it is the view as of 24 May 2025.
    """
    types = tuple(t for t in allergy_types.split("|") if t) if allergy_types else ()
    unknown = [t for t in types if t not in config.POLLEN_THRESHOLDS]
    if unknown:
        raise HTTPException(400, f"Tipo de polen desconocido: {unknown[0]}")
    return clean({"pollen": chart.pollen_chart(lat, lon, types, demo), "air": chart.air_chart(lat, lon, demo)})


@app.get("/api/freshness")
def get_freshness(demo: bool = False, hour: int = HOUR,
                  lat: float = Query(None, ge=-90, le=90), lon: float = Query(None, ge=-180, le=180)):
    """Each source, its latest timestamp, how long ago, and whether it is provisional."""
    moment = the_moment(demo, hour)
    real_now = risk.madrid_now()
    sources, updated_minutes = [], None

    def origin(table):
        if demo:
            return "Día de demostración"
        return "En directo" if table.attrs.get("live") else "Copia de seguridad"

    try:
        air = air_table(demo, moment)
        updated_minutes = minutes_between(air.attrs["fetched_at"], real_now)
        for source, group in air.groupby("source"):
            latest = group["timestamp"].max()
            provisional = bool((group["flag"] == "T").any())
            minutes = minutes_between(latest, moment)
            sources.append({
                "source": f"Aire · {source}", "timestamp": latest,
                "minutes_ago": minutes, "ago": ago_text(minutes), "provisional": provisional,
                "status": ("Provisional (pendiente de validar)" if provisional
                           else "Validado" if demo else "Tiempo real"),
                "origin": origin(air), "live": bool(air.attrs.get("live")),
            })
    except Exception:
        pass

    try:
        pollen = pollen_table(demo, moment)
        if not pollen.empty:
            latest = pollen["timestamp"].max()
            minutes = minutes_between(latest, moment)
            sources.append({
                "source": "Polen · Red Palinocam (Comunidad de Madrid)", "timestamp": latest,
                "minutes_ago": minutes, "ago": "de hoy" if minutes < 24 * 60 else ago_text(minutes),
                "provisional": False, "status": "Recuento manual, se publica con días de retraso",
                "origin": origin(pollen), "live": bool(pollen.attrs.get("live")),
            })
    except Exception:
        pass

    # our own forecasts (made from the official measurements) and the weather forecast
    try:
        forecast = air_predict.forecast(demo, moment)
        made = forecast.attrs.get("fetched_at") or real_now
        minutes = minutes_between(made, moment if demo else real_now)
        sources.append({
            "source": "Previsión de aire · modelo propio (con mediciones oficiales)", "timestamp": made,
            "minutes_ago": minutes, "ago": ago_text(minutes), "provisional": False,
            "status": f"PREVISIÓN (modelo), hasta {air_predict.horizon_hours()} h",
            "origin": origin(forecast), "live": bool(forecast.attrs.get("live", True)) or demo,
        })
    except Exception:
        pass
    if lat is not None and lon is not None:
        try:
            forecast_weather = data.load_forecast_weather(lat, lon, config.REPLAY_DATE if demo else None)
            minutes = minutes_between(forecast_weather.attrs["fetched_at"], real_now)
            sources.append({
                "source": "Previsión del tiempo · Open-Meteo", "timestamp": forecast_weather.attrs["fetched_at"],
                "minutes_ago": minutes, "ago": ago_text(minutes), "provisional": False,
                "status": "PREVISIÓN (modelo), no medición",
                "origin": "Día de demostración" if demo else ("En directo" if forecast_weather.attrs.get("live") else "Copia de seguridad"),
                "live": bool(forecast_weather.attrs.get("live")) or demo,
            })
        except Exception:
            pass

    return clean({
        "demo": demo, "time": moment,
        "updated_minutes": updated_minutes,          # how long ago the app downloaded the air data
        "all_live": bool(sources) and all(s["live"] for s in sources),
        "sources": sources,
    })


# ----------------------------------------------------------------------------
# The AI assistant
# ----------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str                       # "user" or "assistant"
    content: str


class ChatContext(BaseModel):
    """What the panel already knows, so the assistant can use it."""
    lat: float | None = None
    lon: float | None = None
    place: str | None = None
    profile: str = "general"
    allergy_types: list[str] = []
    demo: bool = False
    hour: int | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    context: ChatContext = ChatContext()


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request):
    """
    One reply from the assistant. It always answers with a normal page code
    and a friendly `reply`, even when the assistant is off, the key is
    missing or Anthropic's service fails: the rest of the app is unaffected.
    """
    # the visitor's address (behind Render's proxy it is in X-Forwarded-For)
    forwarded = request.headers.get("x-forwarded-for", "")
    visitor = forwarded.split(",")[0].strip() or (request.client.host if request.client else "anon")
    return assistant.answer([m.model_dump() for m in body.messages], body.context.model_dump(), visitor)


@app.get("/api/chat/usage")
def chat_usage():
    """Token use and estimated cost since the server started."""
    return assistant.usage_totals()


# ----------------------------------------------------------------------------
# The web page
# ----------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
