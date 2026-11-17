"""
air_model/lead_report.py - the air forecast's error for EVERY lead time (1 to 120 hours).

Reads air_model/comparison_by_lead.csv (written by train_air_model.py: mean
error per pollutant, year and lead hour, for "same hour yesterday",
"typical at that hour" and gradient boosting, with the weather forecast as it
was issued) and writes:
  - air_model/comparison_by_hour.md : one table per pollutant, hours 1-120
  - a short summary printed on screen (selected hours), pasted into MODEL_REPORT.md

Run:  .venv/bin/python air_model/lead_report.py
"""

from pathlib import Path

import pandas as pd

FOLDER = Path(__file__).parent
SELECTED = [1, 3, 6, 12, 24, 36, 48, 72, 96, 120]


def gain(model, baseline):
    """How much lower the model's error is than the baseline's, in percent."""
    return 100 * (1 - model / baseline)


def main():
    table = pd.read_csv(FOLDER / "comparison_by_lead.csv")
    lines = ["# Previsión del aire: error por hora de antelación (1 a 120 h)", "",
             "Error medio absoluto (µg/m³) en todas las estaciones. «Mejora» = cuánto menor es el error del "
             "gradient boosting que el de «la misma hora de ayer». 2024 = año de elección; 2025 = prueba. "
             "En las dos, la meteorología es la previsión tal como se emitió (ver MODEL_REPORT.md, sección 8).", ""]
    summary = ["| Contaminante | Año | " + " | ".join(f"{h} h" for h in SELECTED) + " |",
               "|---|---|" + "---|" * len(SELECTED)]
    for pollutant, group in table.groupby("pollutant", sort=False):
        wide = group.pivot(index="lead", columns="year", values=["baseline_mae", "gb_mae", "typical_mae"])
        lines += [f"## {pollutant}", "",
                  "| Horas | 2024: ayer | 2024: habitual | 2024: modelo | 2024: mejora | 2025: ayer | 2025: habitual | 2025: modelo | 2025: mejora |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for lead, row in wide.iterrows():
            cells = []
            for year in (2024, 2025):
                base, typ, gb = row[("baseline_mae", year)], row[("typical_mae", year)], row[("gb_mae", year)]
                cells += [f"{base:.1f}", f"{typ:.1f}", f"{gb:.1f}", f"{gain(gb, base):.0f} %"]
            lines.append(f"| {lead} | " + " | ".join(cells) + " |")
        lines.append("")
        for year in (2024, 2025):
            picked = wide.loc[SELECTED]
            gains = [gain(picked.loc[h, ("gb_mae", year)], picked.loc[h, ("baseline_mae", year)]) for h in SELECTED]
            summary.append(f"| {pollutant} | {year} | " + " | ".join(f"{g:.0f} %" for g in gains) + " |")
    (FOLDER / "comparison_by_hour.md").write_text("\n".join(lines), encoding="utf-8")
    print("Mejora del modelo frente a «la misma hora de ayer» (error medio):")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
