"""
precompute.py - runs our two forecast models and saves the results as small files.

Run by GitHub Actions (.github/workflows/precompute.yml): the air part every
hour, the pollen part once a day. The results go on the "results" branch; the
server only downloads and reads them (results.py), so it never loads a model
(the Render free plan has 512 MB; the models alone took ~260-400 MB).

    python precompute.py air     --out results-folder
    python precompute.py pollen  --out results-folder
    python precompute.py all     --out results-folder [--also-backup]

What it writes in --out (see results.py for how they are read):
    air_forecast.csv.gz, air_recent.csv.gz                  (part "air")
    pollen_model_forecast.csv.gz, pollen_recent.csv.gz      (part "pollen")
    status.json   {part: {computed_at, last_attempt, last_error}}

KEEP THE LAST GOOD RESULTS: a part is written only if it was computed
correctly. If it fails, its old files stay as they are, and status.json
records the failed attempt (computed_at keeps the time of the last good run),
so the app shows the old results with the time they were computed.

--also-backup: also copies the results into backup/ (with
backup/results_status.json), so the copies committed on main are fresh.

Needs the full model libraries: pip install -r requirements-precompute.txt
"""

import argparse
import gzip
import json
import logging
import shutil
import sys
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
logging.getLogger("streamlit").setLevel(logging.ERROR)

import data                                     # noqa: E402
from air_model import predict as air_predict   # noqa: E402
from pollen_model import predict as pollen_predict  # noqa: E402

STATUS = "status.json"


def now_text():
    """Madrid time as ISO text, e.g. 2026-11-19T15:05:00 (same clock as the app)."""
    return data._madrid_now().floor("s").isoformat()


def write_gz(table, path):
    """Save a table as gzip-compressed CSV."""
    with gzip.open(path, "wt", encoding="utf-8", newline="") as file:
        table.to_csv(file, index=False)


def compute_air(out):
    """Our air forecast for every station (+ the measured hours it starts from)."""
    # The Ayuntamiento's live file only has today, so the measured hours are kept
    # from one run to the next: start from the previous run's copy.
    previous = out / "air_recent.csv.gz"
    if previous.exists():
        (data.BACKUP_DIR / "air_recent.csv").write_bytes(gzip.decompress(previous.read_bytes()))
    table = air_predict._fetch_live_forecast()          # also rewrites backup/air_recent.csv
    if table.empty:
        raise RuntimeError("the air forecast came out empty")
    write_gz(table, out / "air_forecast.csv.gz")
    with open(data.BACKUP_DIR / "air_recent.csv", "rb") as source, gzip.open(out / "air_recent.csv.gz", "wb") as target:
        shutil.copyfileobj(source, target)
    return f"{len(table):,} rows, {table['station_code'].nunique()} stations"


def compute_pollen(out):
    """Our pollen forecast for every station, type and day (with the nowcast) + the recent readings."""
    table = pollen_predict._fetch_live_forecast()
    if table.empty:
        raise RuntimeError("the pollen forecast came out empty")
    recent = pollen_predict._fetch_recent_readings()   # reuses the readings downloaded for the forecast
    write_gz(table, out / "pollen_model_forecast.csv.gz")
    write_gz(recent, out / "pollen_recent.csv.gz")
    return f"{len(table):,} rows, {table['station_code'].nunique()} stations; {len(recent):,} recent readings"


PARTS = {"air": compute_air, "pollen": compute_pollen}


def main():
    parser = argparse.ArgumentParser(description="Compute the air and/or pollen forecasts and save them.")
    parser.add_argument("part", choices=["air", "pollen", "all"])
    parser.add_argument("--out", required=True, help="folder for the results (the results branch checkout)")
    parser.add_argument("--also-backup", action="store_true", help="also copy the results into backup/")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    status_file = out / STATUS
    status = json.loads(status_file.read_text()) if status_file.exists() else {}

    failed = []
    for part in (["air", "pollen"] if args.part == "all" else [args.part]):
        record = status.setdefault(part, {})
        started = now_text()
        record["last_attempt"] = started
        # write into a scratch folder first: if anything fails, the old files stay untouched
        scratch = out / f".new_{part}"
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir()
        if part == "air" and (out / "air_recent.csv.gz").exists():
            shutil.copy(out / "air_recent.csv.gz", scratch / "air_recent.csv.gz")
        try:
            summary = PARTS[part](scratch)
            for file in scratch.iterdir():
                file.replace(out / file.name)
            record["computed_at"] = started
            record["last_error"] = None
            print(f"{part}: OK ({summary}), computed at {started}")
        except Exception as error:
            record["last_error"] = f"{type(error).__name__}: {error}"[:300]
            failed.append(part)
            print(f"{part}: FAILED, keeping the last good results ({record.get('computed_at')})")
            traceback.print_exc()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    status_file.write_text(json.dumps(status, ensure_ascii=False, indent=1))

    if args.also_backup:
        copy_to_backup(out, status)

    sys.exit(1 if failed else 0)                       # a failed part turns the GitHub run red


def copy_to_backup(out, status):
    """
    Copy the results into backup/, PART BY PART, keeping the last good results there too.

    A part with good results in --out (it has a computed_at) replaces its backup files and its
    status. A part without (it failed, and --out is a new folder with no older results, as with
    warm_backup.py) leaves its backup files alone AND keeps their computed_at: only the failed
    attempt is recorded. (Before 26 Nov 2026 the whole status file was copied, so a failed part
    lost the date of the backup files that were kept.)
    """
    backup_status_file = data.BACKUP_DIR / "results_status.json"
    backup_status = json.loads(backup_status_file.read_text()) if backup_status_file.exists() else {}
    files = {"air": ("air_forecast", "air_recent"), "pollen": ("pollen_model_forecast", "pollen_recent")}
    for part, record in status.items():
        if record.get("computed_at"):                  # good results for this part: copy them
            for name in files[part]:
                source = out / f"{name}.csv.gz"
                if source.exists():
                    (data.BACKUP_DIR / f"{name}.csv").write_bytes(gzip.decompress(source.read_bytes()))
            backup_status[part] = dict(record)
        else:                                          # no good results: keep the backup's files and date
            kept = backup_status.setdefault(part, {})
            kept["last_attempt"] = record.get("last_attempt")
            kept["last_error"] = record.get("last_error")
    backup_status_file.write_text(json.dumps(backup_status, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
