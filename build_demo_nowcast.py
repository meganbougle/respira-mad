"""
build_demo_nowcast.py - one-off: adds the NOWCAST to the demo pollen forecast.

In demo mode the app shows the view "as of 24 May 2025". The last readings
published by then are from 21 May (they arrive about 3 days late), so the days
22 and 23 May have no reading. Like the live app, those days get the model's
same-day estimate (lead 0) as it would have been made on each of those days.

The estimates come from model_data/comparison_rows_2025.csv.gz (written by
compare_models.py: every model's forecast for every day of 2025), with the
same rules as the app: the model chosen on 2024 for each pollen type (ARIMAX
falls back to gradient boosting if unstable), the calibrated 80% interval
margin, the RELIABLE flag and the 2025 accuracy.

Run once (after compare_models.py):  .venv/bin/python build_demo_nowcast.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

import config
from pollen_model import arimax as A
from pollen_model import features as F
from pollen_model import predict

ROOT = Path(__file__).parent
DEMO_DAY = pd.Timestamp(config.PLANNER_DEMO_DATE)
DEMO_FILE = ROOT / "backup" / "replay" / f"pollen_forecast_{config.PLANNER_DEMO_DATE}.csv"
RECENT_FILE = ROOT / "backup" / "replay" / f"pollen_recent_{config.PLANNER_DEMO_DATE}.csv"

demo = pd.read_csv(DEMO_FILE, parse_dates=["issue_date", "target_date"])
demo = demo[~demo.get("nowcast", pd.Series(False, index=demo.index)).fillna(False).astype(bool)]   # rerunnable
recent = pd.read_csv(RECENT_FILE, parse_dates=["timestamp"])
last_reading = recent.groupby("station_code")["timestamp"].max()
demo["nowcast"] = False
demo["last_reading"] = demo["station_code"].map(last_reading)

rows = pd.read_csv(ROOT / "model_data" / "comparison_rows_2025.csv.gz", parse_dates=["issue_date", "target_date"])
first = last_reading.min() + pd.Timedelta(days=1)
rows = rows[(rows["lead"] == 0) & (rows["issue_date"] >= first) & (rows["issue_date"] < DEMO_DAY)
            & rows["station_code"].isin(demo["station_code"].unique())]
rows = rows[rows["target_date"] > rows["station_code"].map(last_reading)]

choice = predict.model_choice()["choice"]
flags = predict.test_results()
stations = demo.drop_duplicates("station_code").set_index("station_code")[["station", "lat", "lon"]]
out = []
for _, row in rows.iterrows():
    model = choice.get(row["pollen_type"], "gb")
    if model == "arimax" and not pd.isna(row["arimax"]) and row["arimax_se"] <= A.MAX_LOG_ERROR:
        low, middle, high = row["arimax_q10"], row["arimax"], row["arimax_q90"]
    else:
        model = "gb"
        low, middle, high = row["gb_q10"], row["gb_q50"], row["gb_q90"]
    if pd.isna(middle):
        continue
    flag = flags.get((row["pollen_type"], 0, model), {"reliable": False, "accuracy": None, "margin": 0.0})
    low, high = min(max(low - flag["margin"], 0), middle), max(high + flag["margin"], middle)
    q50 = float(np.expm1(middle))
    out.append({
        **stations.loc[row["station_code"]].to_dict(), "station_code": row["station_code"],
        "pollen_type": row["pollen_type"], "issue_date": row["issue_date"], "target_date": row["target_date"],
        "lead": 0, "target_value": row["target_value"], "model": model,
        "q10": round(float(np.expm1(low)), 1), "q50": round(q50, 1), "q90": round(float(np.expm1(high)), 1),
        "reliable": flag["reliable"], "accuracy": flag["accuracy"],
        "predicted_level": int(F.level_of(row["pollen_type"], [q50])[0]),
        "nowcast": True, "last_reading": last_reading[row["station_code"]],
    })
added = pd.DataFrame(out)
result = pd.concat([demo, added], ignore_index=True)
result.to_csv(DEMO_FILE, index=False)
print(f"Added {len(added)} nowcast rows ({added['target_date'].min():%d/%m} to {added['target_date'].max():%d/%m}) "
      f"to {DEMO_FILE.name}")
