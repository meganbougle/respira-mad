"""
results.py - the server's side of the precomputed forecasts.

GitHub Actions runs precompute.py (air every hour, pollen once a day) and puts
the results on the "results" branch of the repository:

    status.json                 when each part was last computed correctly (and the last attempt)
    air_forecast.csv.gz         our air forecast, every station, every hour up to the horizon
    air_recent.csv.gz           the measured hours of the last 3 days (the planner's past slots)
    pollen_model_forecast.csv.gz  our pollen forecast, every station, type and day (with the nowcast)
    pollen_recent.csv.gz        the official pollen readings of the last two weeks

This file downloads them (at most every RESULTS_REFRESH_SECONDS) into backup/
as plain CSV files with the same names the app always used, and reads them.
No model is ever loaded on the server (that is what saves the memory).

If a download fails, or a scheduled run failed, the last good copy stays in
backup/ and its computed time is shown. The copies committed on main are the
fallback when the server starts and GitHub cannot be reached.
"""

import gzip
import json
import logging
import os
import threading
import time
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

import config
import data

log = logging.getLogger("respira.results")

# file name -> (part of the run, the columns that hold dates)
FILES = {
    "air_forecast": ("air", ("timestamp", "issued")),
    "air_recent": ("air", ("timestamp",)),
    "pollen_model_forecast": ("pollen", ("timestamp", "issue_date", "target_date", "last_reading")),
    "pollen_recent": ("pollen", ("timestamp",)),
}
STATUS_FILE = data.BACKUP_DIR / "results_status.json"
TIMEOUT = 30

_lock = threading.Lock()
_last_check = 0.0


# ----------------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------------

def _get(name):
    response = requests.get(config.RESULTS_URL + name, timeout=TIMEOUT, headers={"Cache-Control": "no-cache"})
    response.raise_for_status()
    return response.content


def _save(path, content):
    """Write a file in one step (a reader never sees half a file)."""
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def refresh(force=False):
    """
    Download the results that are newer than our copies. Returns the parts
    updated. Never raises: on any problem the old copies stay.
    """
    global _last_check
    with _lock:
        if not force and time.time() - _last_check < config.RESULTS_REFRESH_SECONDS:
            return []
        _last_check = time.time()
        try:
            remote = json.loads(_get("status.json"))
        except Exception as error:
            log.warning("Could not download the results status (keeping our copies): %s", error)
            return []
        local = status()
        updated = []
        for part in ("air", "pollen"):
            new = (remote.get(part) or {}).get("computed_at")
            mine = (local.get(part) or {}).get("computed_at")
            if not new or (mine and new <= mine):                 # ISO times: text order = time order
                continue                                          # nothing newer for this part
            try:
                for name, (file_part, _) in FILES.items():
                    if file_part == part:
                        content = gzip.decompress(_get(f"{name}.csv.gz"))
                        _save(data.BACKUP_DIR / f"{name}.csv", content)
                local[part] = remote[part]
                updated.append(part)
            except Exception as error:
                log.warning("Could not download the %s results (keeping our copies): %s", part, error)
        # always keep the remote's record of the last attempt (to show a failed run)
        for part in ("air", "pollen"):
            if part in remote and part in local:
                local[part]["last_attempt"] = remote[part].get("last_attempt")
                local[part]["last_error"] = remote[part].get("last_error")
        _save(STATUS_FILE, json.dumps(local, ensure_ascii=False, indent=1).encode())
        if updated:
            log.info("Downloaded new results: %s", ", ".join(updated))
        return updated


# ----------------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------------

def status():
    """{part: {"computed_at": ..., "last_attempt": ..., "last_error": ...}} of our copies ({} if unknown)."""
    try:
        return json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def computed_at(part):
    """When our copy of `part` ("air" or "pollen") was computed (Madrid time), or None."""
    text = (status().get(part) or {}).get("computed_at")
    return pd.Timestamp(text) if text else None


def is_stale(part, made=None):
    """True if the last good result (computed at `made`) is older than RESULTS_STALE_HOURS: the runs are failing."""
    made = made if made is not None else computed_at(part)
    if made is None:
        return True
    return data._madrid_now() - made > pd.Timedelta(hours=config.RESULTS_STALE_HOURS[part])


@st.cache_data(show_spinner=False, max_entries=8)
def _read_file(name, modified):
    """Read one results file (cached until the file changes: `modified` is its time stamp)."""
    _, dates = FILES[name]
    path = data.BACKUP_DIR / f"{name}.csv"
    header = pd.read_csv(path, nrows=0).columns
    return pd.read_csv(path, parse_dates=[c for c in dates if c in header],
                       dtype={"station_code": str} if "station_code" in header else None)


def read(name):
    """
    One precomputed table, with attrs: live (False if the runs are failing),
    fetched_at = computed_at (when it was computed), stale.
    """
    refresh()                                            # at most every RESULTS_REFRESH_SECONDS
    path = data.BACKUP_DIR / f"{name}.csv"
    if not path.exists():
        raise RuntimeError(f"no precomputed results for {name} yet")
    table = _read_file(name, path.stat().st_mtime).copy()
    part = FILES[name][0]
    made = computed_at(part)
    if made is None:                                     # an old copy without a status: use the file's time
        made = pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC").tz_convert("Europe/Madrid").tz_localize(None)
    stale = is_stale(part, made)
    table.attrs.update({"live": not stale, "stale": stale, "fetched_at": made, "computed_at": made})
    return table


def path_of(name):
    """Where the local copy of a results file is (for code that reads it directly)."""
    return Path(data.BACKUP_DIR / f"{name}.csv")
