# Pollen forecast model: backtest report

Made on 2026-11-16 by `train_pollen_model.py`. Trained on 1993-05-01 to 2023-12-31, tested on 2025 (a year the model never saw).

## How to read this

- **Lead day**: how many days ahead (1 = tomorrow). Readings are published about 3 days late, so the forecast for lead day 5 is made about 8 days after the last reading it can see.
- **In season**: only the days of the year when that pollen type is normally around. Out of season everything is `bajo` and any method scores close to 100%, so those days are not counted.
- **Baseline**: the last available reading, adjusted for the usual seasonal change. The model has to beat this.
- **High days caught**: of the days that really were `alto` or `muy alto`, the share the forecast also put at `alto` or more.
- **Model right / Baseline right**: share of in-season days with the right level out of the three the app acts on (bajo, medio, alto-or-more; alto and muy alto give the same advice). **Exact level** is the stricter score out of all four official levels.
- **RELIABLE**: on in-season days, the model gets the app's level (bajo / medio / alto-or-more) right at least 5 percentage points more often than the baseline, with a smaller mean level error, and there are at least 200 in-season station-days in 2025 to judge from.
- The 2025 test used the weather forecasts as they were issued at the time (Open-Meteo previous-runs archive), not the weather that later happened. Training on earlier years used the weather that happened, because old forecasts are not archived.

## Result by pollen type and lead day (in season, 2025)

| Pollen type | Lead | Model right | Baseline right | Gain | Exact level (model / baseline) | High days caught (model / baseline) | In-season days | Verdict |
|---|---|---|---|---|---|---|---|---|
| Plátano de paseo | 0 | 82% | 72% | +11% | 69% / 62% | 85% / 65% | 390 | **RELIABLE** |
| Plátano de paseo | 1 | 78% | 71% | +7% | 68% / 62% | 68% / 62% | 390 | **RELIABLE** |
| Plátano de paseo | 2 | 69% | 70% | -1% | 66% / 61% | 47% / 59% | 390 | not reliable |
| Plátano de paseo | 3 | 78% | 68% | +9% | 67% / 61% | 83% / 54% | 390 | **RELIABLE** |
| Plátano de paseo | 4 | 74% | 65% | +9% | 60% / 59% | 82% / 47% | 390 | **RELIABLE** |
| Plátano de paseo | 5 | 78% | 64% | +13% | 65% / 59% | 74% / 44% | 390 | **RELIABLE** |
| Cipreses y arizónicas | 0 | 72% | 59% | +13% | 70% / 58% | 43% / 37% | 680 | **RELIABLE** |
| Cipreses y arizónicas | 1 | 69% | 59% | +11% | 68% / 57% | 50% / 36% | 680 | **RELIABLE** |
| Cipreses y arizónicas | 2 | 69% | 56% | +13% | 68% / 56% | 42% / 27% | 680 | **RELIABLE** |
| Cipreses y arizónicas | 3 | 68% | 58% | +10% | 66% / 57% | 37% / 28% | 680 | **RELIABLE** |
| Cipreses y arizónicas | 4 | 71% | 56% | +15% | 68% / 55% | 44% / 31% | 680 | **RELIABLE** |
| Cipreses y arizónicas | 5 | 68% | 54% | +13% | 66% / 54% | 28% / 27% | 680 | **RELIABLE** |
| Encinas y robles | 0 | 54% | 60% | -6% | 51% / 56% | 49% / 64% | 710 | not reliable |
| Encinas y robles | 1 | 58% | 59% | -0% | 56% / 56% | 51% / 62% | 710 | not reliable |
| Encinas y robles | 2 | 60% | 55% | +5% | 56% / 52% | 50% / 56% | 710 | not reliable |
| Encinas y robles | 3 | 57% | 54% | +3% | 50% / 51% | 60% / 54% | 710 | not reliable |
| Encinas y robles | 4 | 55% | 53% | +3% | 52% / 49% | 44% / 52% | 710 | not reliable |
| Encinas y robles | 5 | 55% | 50% | +6% | 49% / 46% | 46% / 50% | 710 | **RELIABLE** |
| Olivo | 0 | 72% | 57% | +15% | 58% / 49% | 68% / 65% | 513 | **RELIABLE** |
| Olivo | 1 | 67% | 55% | +12% | 61% / 46% | 51% / 61% | 513 | **RELIABLE** |
| Olivo | 2 | 75% | 52% | +23% | 68% / 42% | 70% / 52% | 513 | **RELIABLE** |
| Olivo | 3 | 77% | 54% | +22% | 68% / 44% | 78% / 51% | 513 | **RELIABLE** |
| Olivo | 4 | 74% | 56% | +18% | 66% / 45% | 66% / 52% | 513 | **RELIABLE** |
| Olivo | 5 | 74% | 58% | +17% | 66% / 47% | 72% / 53% | 513 | **RELIABLE** |
| Gramíneas | 0 | 72% | 67% | +5% | 59% / 57% | 75% / 73% | 729 | not reliable |
| Gramíneas | 1 | 69% | 68% | +1% | 54% / 57% | 73% / 76% | 729 | not reliable |
| Gramíneas | 2 | 75% | 64% | +11% | 59% / 51% | 83% / 74% | 729 | **RELIABLE** |
| Gramíneas | 3 | 75% | 63% | +12% | 55% / 50% | 86% / 72% | 729 | **RELIABLE** |
| Gramíneas | 4 | 73% | 60% | +13% | 53% / 50% | 84% / 71% | 729 | **RELIABLE** |
| Gramíneas | 5 | 73% | 60% | +14% | 56% / 48% | 86% / 70% | 729 | **RELIABLE** |
| Pinos | 0 | 69% | 60% | +9% | 64% / 56% | 43% / 38% | 484 | **RELIABLE** |
| Pinos | 1 | 67% | 58% | +10% | 60% / 55% | 69% / 32% | 484 | **RELIABLE** |
| Pinos | 2 | 70% | 56% | +14% | 66% / 53% | 45% / 29% | 484 | **RELIABLE** |
| Pinos | 3 | 70% | 55% | +15% | 65% / 53% | 54% / 31% | 484 | **RELIABLE** |
| Pinos | 4 | 69% | 55% | +14% | 64% / 50% | 45% / 35% | 484 | **RELIABLE** |
| Pinos | 5 | 66% | 55% | +11% | 61% / 50% | 45% / 38% | 484 | **RELIABLE** |
| Chopos | 0 | 72% | 68% | +4% | 69% / 65% | 29% / 48% | 417 | not reliable |
| Chopos | 1 | 73% | 66% | +7% | 71% / 63% | 23% / 37% | 417 | **RELIABLE** |
| Chopos | 2 | 72% | 62% | +10% | 71% / 59% | 14% / 29% | 417 | **RELIABLE** |
| Chopos | 3 | 70% | 60% | +9% | 69% / 58% | 12% / 22% | 417 | **RELIABLE** |
| Chopos | 4 | 68% | 58% | +11% | 67% / 56% | 11% / 16% | 417 | **RELIABLE** |
| Chopos | 5 | 69% | 56% | +12% | 68% / 54% | 7% / 13% | 417 | **RELIABLE** |
| Olmos | 0 | 81% | 76% | +5% | 71% / 66% | 83% / 76% | 360 | **RELIABLE** |
| Olmos | 1 | 78% | 72% | +5% | 64% / 59% | 69% / 73% | 360 | **RELIABLE** |
| Olmos | 2 | 75% | 69% | +6% | 63% / 60% | 70% / 67% | 360 | **RELIABLE** |
| Olmos | 3 | 66% | 66% | +0% | 59% / 55% | 33% / 62% | 360 | not reliable |
| Olmos | 4 | 72% | 61% | +11% | 63% / 52% | 54% / 58% | 360 | **RELIABLE** |
| Olmos | 5 | 71% | 59% | +12% | 61% / 49% | 50% / 53% | 360 | **RELIABLE** |
| Llantenes | 0 | 67% | 64% | +3% | 59% / 57% | 79% / 63% | 611 | not reliable |
| Llantenes | 1 | 65% | 62% | +2% | 57% / 56% | 72% / 58% | 611 | not reliable |
| Llantenes | 2 | 68% | 56% | +13% | 60% / 49% | 84% / 53% | 611 | **RELIABLE** |
| Llantenes | 3 | 65% | 58% | +7% | 56% / 51% | 78% / 51% | 611 | **RELIABLE** |
| Llantenes | 4 | 64% | 53% | +11% | 55% / 47% | 80% / 47% | 611 | **RELIABLE** |
| Llantenes | 5 | 61% | 53% | +9% | 52% / 48% | 76% / 44% | 611 | **RELIABLE** |
| Ortigas y parietarias | 0 | 70% | 64% | +6% | 62% / 59% | 50% / 56% | 652 | **RELIABLE** |
| Ortigas y parietarias | 1 | 74% | 61% | +13% | 66% / 56% | 56% / 53% | 652 | **RELIABLE** |
| Ortigas y parietarias | 2 | 72% | 58% | +13% | 66% / 53% | 45% / 51% | 652 | **RELIABLE** |
| Ortigas y parietarias | 3 | 67% | 57% | +10% | 60% / 50% | 42% / 52% | 652 | **RELIABLE** |
| Ortigas y parietarias | 4 | 63% | 59% | +4% | 54% / 52% | 39% / 52% | 652 | not reliable |
| Ortigas y parietarias | 5 | 69% | 54% | +15% | 60% / 49% | 44% / 49% | 652 | **RELIABLE** |

## If readings arrive later than 3 days

The same test with readings 6 days old (it happens): average in-season accuracy 68% for the model and 55% for the baseline, against 69% and 59% with 3-day-old readings.

## The 24 May 2025 check (grass)

Forecast made with only what was known on 24 May: readings up to 21 May and the weather forecast issued that day.

| Day | Lead | Stations predicted at each level | Real level at those stations | Real network average (grains/m³) |
|---|---|---|---|---|
| 25 May | 1 | 9 muy alto, 1 alto | 8 muy alto, 2 alto | 203 |
| 26 May | 2 | 6 alto, 4 muy alto | 10 muy alto | 302 |
| 27 May | 3 | 9 alto, 1 muy alto | 10 muy alto | 264 |
| 28 May | 4 | 8 alto, 2 muy alto | 10 muy alto | 392 |
| 29 May | 5 | 6 alto, 4 muy alto | 10 muy alto | 308 |

![Predicted vs real, spring 2025](spring_2025_predicted_vs_real.png)
