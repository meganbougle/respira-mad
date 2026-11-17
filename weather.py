"""
weather.py - the weather FORECAST from Open-Meteo, fetched carefully.

Open-Meteo answers "429 Too Many Requests" when it gets too many calls, so
every weather request of the app goes through this file:

  1. GRID: a point is rounded to a 0.05 degree grid (about 5 km), so nearby
     points share one weather series.
  2. DISK CACHE: each grid cell has one file in backup/weather/ with 7 days
     of hourly weather. A file younger than 1 hour is used without asking
     Open-Meteo again. Older files stay as the backup if a request fails.
  3. RETRY: a failed request (429, server error, no connection) is retried
     with growing waits (backoff).
  4. BATCHES: several grid cells are asked for in ONE request (Open-Meteo
     takes lists of latitudes and longitudes).
  5. PREFETCH: `prefetch()` refreshes the 29 dropdown places and all
     stations at once; api.py runs it every hour in the background.

If the weather cannot be had at all, `hourly()` raises WeatherUnavailable and
the callers carry on without temperature and rain (the levels come from the
air and pollen data, never from the weather).

Weather is the only data the app takes from outside the Comunidad and the
Ayuntamiento de Madrid (see the data rule in CLAUDE.md). Open-Meteo is
CC BY 4.0: the footer credit is required.
"""

import logging
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger("respira.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"      # past days (demo mode)

GRID = 0.05                       # degrees; about 5 km
FRESH_SECONDS = 60 * 60           # a cached forecast younger than 1 hour is used as it is
FORECAST_DAYS = 7                 # every file holds 7 days, so the 2-day and 6-day users share it
DEMO_DAYS = 7                     # the demo day and the 6 days after it
BATCH_SIZE = 50                   # grid cells per request
RETRY_WAITS = [2, 5, 12]          # seconds to wait before each new try (backoff)
MAX_RETRY_AFTER = 20              # never wait longer than this, even if Open-Meteo asks for more
TIMEOUT = 30                      # seconds; background requests
VISITOR_TIMEOUT = 8               # seconds; when a visitor is waiting, give up sooner and use the cached copy
NEAREST_CELL_MAX = 0.3            # if a cell has no data at all, borrow a cached cell up to ~30 km away

FOLDER = Path(__file__).parent / "backup" / "weather"
HOURLY = {                        # Open-Meteo name: our name
    "temperature_2m": "temperature_c",
    "precipitation": "rain_mm",
    "wind_speed_10m": "wind_kmh",
    "relative_humidity_2m": "humidity_pct",
}
FORECAST_LABEL = "FORECAST (model)"


class WeatherUnavailable(Exception):
    """No weather for this point: neither from Open-Meteo nor from the cache."""


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def cell(lat, lon):
    """The grid cell of a point: (lat, lon) rounded to the 0.05 degree grid."""
    return (round(round(float(lat) / GRID) * GRID, 2), round(round(float(lon) / GRID) * GRID, 2))


def _path(grid_cell, day=None):
    """The cache file of one grid cell (demo days have their own folder: past weather never changes)."""
    lat, lon = grid_cell
    folder = FOLDER / day if day else FOLDER
    return folder / f"{lat:.2f}_{lon:.2f}.csv"


def _age_seconds(path):
    return time.time() - path.stat().st_mtime


def get_json(url, params, retries=len(RETRY_WAITS), timeout=TIMEOUT):
    """
    One request to Open-Meteo, retried with backoff when it fails (`retries` times at most).
    Raises the last error if every try fails (the caller then uses the cache).
    """
    error = None
    for attempt in range(retries + 1):
        wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                # too many requests / server trouble: wait (as long as Open-Meteo asks, within limits) and try again
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = min(int(retry_after), MAX_RETRY_AFTER)
                raise requests.HTTPError(f"Open-Meteo answered {response.status_code}", response=response)
            response.raise_for_status()                 # any other error (400...) is not retried, see below
            return response.json()
        except (requests.RequestException, ValueError) as problem:
            error = problem
            status = getattr(getattr(problem, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                break                                   # a bad request will not get better by waiting
            if attempt < retries:
                log.warning("Open-Meteo request failed (%s); retry %d in %d s", problem, attempt + 1, wait)
                time.sleep(wait)
    raise error


def _to_table(answer, grid_cell):
    """One location of an Open-Meteo answer as our hourly table."""
    table = pd.DataFrame(answer["hourly"]).rename(columns={"time": "timestamp", **HOURLY})
    table["timestamp"] = pd.to_datetime(table["timestamp"])
    table["lat"], table["lon"] = grid_cell
    table["source"] = "Open-Meteo"
    table["kind"] = FORECAST_LABEL
    return table


# ----------------------------------------------------------------------------
# Fetching (several grid cells per request)
# ----------------------------------------------------------------------------

def fetch_cells(cells, day=None, retries=len(RETRY_WAITS), timeout=TIMEOUT):
    """
    Download the hourly weather of several grid cells, in batches, and save
    each one to its cache file. Returns {cell: table} for the cells that worked.
    day=None: the forecast from today (7 days); day="2025-05-28": that day and the 6 after (archive).
    """
    cells = list(dict.fromkeys(cells))                  # no duplicates, same order
    done = {}
    for start in range(0, len(cells), BATCH_SIZE):
        batch = cells[start:start + BATCH_SIZE]
        params = {
            "latitude": ",".join(f"{lat:.2f}" for lat, _ in batch),
            "longitude": ",".join(f"{lon:.2f}" for _, lon in batch),
            "hourly": ",".join(HOURLY),
            "timezone": "Europe/Madrid",                # so the times match the station data
        }
        if day:
            params["start_date"] = day
            params["end_date"] = str((pd.Timestamp(day) + pd.Timedelta(days=DEMO_DAYS - 1)).date())
            url = ARCHIVE_URL
        else:
            params["forecast_days"] = FORECAST_DAYS
            url = FORECAST_URL
        try:
            answer = get_json(url, params, retries, timeout)
        except Exception as error:
            log.warning("Open-Meteo batch of %d cells failed: %s", len(batch), error)
            continue                                    # the other batches may still work
        answers = answer if isinstance(answer, list) else [answer]
        for grid_cell, one in zip(batch, answers):
            table = _to_table(one, grid_cell)
            path = _path(grid_cell, day)
            path.parent.mkdir(parents=True, exist_ok=True)
            table.to_csv(path, index=False)
            done[grid_cell] = table
    return done


def _read(path):
    return pd.read_csv(path, parse_dates=["timestamp"])


def _nearest_cached(grid_cell, day=None):
    """The closest grid cell that has a cache file (to borrow when this one has none)."""
    folder = FOLDER / day if day else FOLDER
    best, best_distance = None, NEAREST_CELL_MAX
    for path in folder.glob("*.csv"):
        try:
            lat, lon = (float(x) for x in path.stem.split("_"))
        except ValueError:
            continue
        distance = max(abs(lat - grid_cell[0]), abs(lon - grid_cell[1]))
        if distance <= best_distance:
            best, best_distance = path, distance
    return best


# ----------------------------------------------------------------------------
# What the rest of the app calls
# ----------------------------------------------------------------------------

def hourly(lat, lon, day=None, days=2):
    """
    Hourly weather FORECAST at a point: `days` days from today (or from `day`
    in demo mode). Columns: timestamp, temperature_c, rain_mm, wind_kmh,
    humidity_pct, lat, lon, source, kind. attrs["live"] says whether it is a
    fresh copy (False = an older copy used because Open-Meteo failed).
    Raises WeatherUnavailable if there is no weather at all for this point.
    """
    grid_cell = cell(lat, lon)
    path = _path(grid_cell, day)
    live, used = True, path
    # 1. a fresh cached copy (demo days never expire: past weather does not change)
    if path.exists() and (day or _age_seconds(path) < FRESH_SECONDS):
        table = _read(path)
    else:
        # 2. ask Open-Meteo. A visitor is waiting, so: one quick try if an older copy exists, else one retry.
        #    (The hourly background prefetch is the one that retries patiently.)
        table = fetch_cells([grid_cell], day, retries=0 if path.exists() else 1, timeout=VISITOR_TIMEOUT).get(grid_cell)
        if table is None:
            # 3. Open-Meteo failed: an older copy of this cell, or of the nearest cell that has one
            live = False
            fallback = path if path.exists() else _nearest_cached(grid_cell, day)
            if fallback is None:
                raise WeatherUnavailable(f"no weather for cell {grid_cell}")
            log.warning("Weather for %s from the cached copy %s", grid_cell, fallback.name)
            table, used = _read(fallback), fallback
    start = pd.Timestamp(day) if day else pd.Timestamp.now(tz="Europe/Madrid").tz_localize(None).normalize()
    table = table[(table["timestamp"] >= start) & (table["timestamp"] < start + pd.Timedelta(days=days))]
    if table.empty:
        raise WeatherUnavailable(f"the cached weather for cell {grid_cell} does not cover these days")
    table = table.reset_index(drop=True)
    table.attrs["live"] = live
    # when this copy was downloaded (Madrid time)
    table.attrs["fetched_at"] = (pd.Timestamp(used.stat().st_mtime, unit="s", tz="UTC")
                                 .tz_convert("Europe/Madrid").tz_localize(None))
    return table


def prefetch(points, day=None, force=False):
    """
    Refresh the weather of many points in as few requests as possible.
    Only cells whose cache is missing or older than 1 hour are asked for
    (all of them with force=True). Returns (cells asked for, cells that worked).
    """
    cells = list(dict.fromkeys(cell(lat, lon) for lat, lon in points))
    if not force:
        cells = [c for c in cells if not _path(c, day).exists()
                 or (not day and _age_seconds(_path(c, day)) >= FRESH_SECONDS)]
    if not cells:
        return 0, 0
    done = fetch_cells(cells, day)
    log.info("Weather prefetch: %d of %d cells refreshed", len(done), len(cells))
    return len(cells), len(done)
