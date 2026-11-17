**2024 (año de elección)**

| Tipo de polen | Estacional ingenuo: acierto / MAE | Suavizado exponencial: acierto / MAE | ARIMAX: acierto / MAE | Gradient boosting: acierto / MAE | Elegido |
|---|---|---|---|---|---|
| Plátano de paseo | 49% / 1.48 | 36% / 2.05 | 48% / 1.36 | **53% / 1.05** | Gradient boosting |
| Cipreses y arizónicas | 43% / 1.84 | 50% / 1.41 | 47% / 1.20 | **59% / 0.96** | Gradient boosting |
| Encinas y robles | 41% / 1.55 | 37% / 1.03 | **55% / 0.73** | 50% / 0.77 | ARIMAX |
| Olivo | 47% / 1.68 | 57% / 1.17 | **65% / 0.82** | 61% / 0.82 | ARIMAX |
| Gramíneas | 39% / 1.64 | 45% / 0.94 | 56% / 0.70 | **56% / 0.69** | Gradient boosting |
| Pinos | 62% / 1.70 | 58% / 1.09 | 65% / 0.86 | **66% / 0.90** | Gradient boosting |
| Chopos | 69% / 1.45 | 74% / 1.26 | 75% / 1.13 | **80% / 0.86** | Gradient boosting |
| Olmos | 36% / 2.15 | 55% / 1.31 | 58% / 1.09 | **66% / 0.80** | Gradient boosting |
| Llantenes | 26% / 1.80 | 50% / 0.72 | **48% / 0.61** | 38% / 0.77 | ARIMAX |
| Ortigas y parietarias | 54% / 1.15 | 68% / 0.93 | 76% / 0.70 | **75% / 0.65** | Gradient boosting |
| **Media** | 47% / 1.64 | 53% / 1.19 | 59% / 0.92 | 60% / 0.83 | 62% con el elegido |

**2025 (año de prueba, no usado para decidir)**

| Tipo de polen | Estacional ingenuo: acierto / MAE | Suavizado exponencial: acierto / MAE | ARIMAX: acierto / MAE | Gradient boosting: acierto / MAE | Elegido |
|---|---|---|---|---|---|
| Plátano de paseo | 46% / 2.49 | 56% / 1.51 | 65% / 1.23 | **65% / 0.92** | Gradient boosting |
| Cipreses y arizónicas | 48% / 1.69 | 57% / 1.39 | 64% / 1.09 | **67% / 1.03** | Gradient boosting |
| Encinas y robles | 44% / 1.54 | 50% / 1.00 | **57% / 0.82** | 54% / 0.90 | ARIMAX |
| Olivo | 43% / 2.00 | 45% / 1.43 | **53% / 1.13** | 65% / 0.80 | ARIMAX |
| Gramíneas | 43% / 1.37 | 47% / 0.93 | 51% / 0.74 | **53% / 0.71** | Gradient boosting |
| Pinos | 54% / 1.45 | 53% / 0.99 | 65% / 0.74 | **65% / 0.79** | Gradient boosting |
| Chopos | 59% / 2.01 | 61% / 1.35 | 61% / 1.18 | **68% / 1.15** | Gradient boosting |
| Olmos | 47% / 1.54 | 48% / 1.46 | 61% / 0.93 | **61% / 0.88** | Gradient boosting |
| Llantenes | 39% / 1.30 | 51% / 0.75 | **54% / 0.62** | 55% / 0.56 | ARIMAX |
| Ortigas y parietarias | 51% / 1.47 | 56% / 0.94 | 62% / 0.85 | **60% / 0.87** | Gradient boosting |
| **Media** | 47% / 1.69 | 52% / 1.18 | 59% / 0.93 | 61% / 0.86 | 60% con el elegido |

**2025, por día de antelación (en negrita, el mejor acierto de cada fila)**

| Tipo de polen | Día | Estacional ingenuo: acierto / MAE | Suavizado exponencial: acierto / MAE | ARIMAX: acierto / MAE | Gradient boosting: acierto / MAE | Días de temporada |
|---|---|---|---|---|---|---|
| Plátano de paseo | +1 | 46% / 2.49 | 60% / 1.23 | 65% / 1.08 | **66% / 0.79** | 390 |
| Plátano de paseo | +2 | 46% / 2.49 | 57% / 1.37 | 64% / 1.17 | **69% / 0.84** | 390 |
| Plátano de paseo | +3 | 46% / 2.49 | 57% / 1.50 | 65% / 1.25 | **67% / 0.92** | 390 |
| Plátano de paseo | +4 | 46% / 2.49 | 55% / 1.64 | **64% / 1.30** | 64% / 0.97 | 390 |
| Plátano de paseo | +5 | 46% / 2.49 | 52% / 1.79 | **65% / 1.36** | 62% / 1.05 | 390 |
| Cipreses y arizónicas | +1 | 48% / 1.69 | 59% / 1.35 | 65% / 1.09 | **67% / 1.03** | 680 |
| Cipreses y arizónicas | +2 | 48% / 1.69 | 58% / 1.38 | 65% / 1.10 | **68% / 1.02** | 680 |
| Cipreses y arizónicas | +3 | 48% / 1.69 | 58% / 1.37 | 63% / 1.09 | **66% / 1.02** | 680 |
| Cipreses y arizónicas | +4 | 48% / 1.69 | 56% / 1.39 | 64% / 1.09 | **68% / 1.04** | 680 |
| Cipreses y arizónicas | +5 | 48% / 1.69 | 55% / 1.46 | 63% / 1.11 | **65% / 1.06** | 680 |
| Encinas y robles | +1 | 44% / 1.54 | 55% / 0.88 | **59% / 0.76** | 55% / 0.82 | 710 |
| Encinas y robles | +2 | 44% / 1.54 | 51% / 0.96 | **58% / 0.80** | 54% / 0.89 | 710 |
| Encinas y robles | +3 | 44% / 1.54 | 50% / 0.99 | **58% / 0.82** | 53% / 0.92 | 710 |
| Encinas y robles | +4 | 44% / 1.54 | 47% / 1.05 | **57% / 0.84** | 53% / 0.91 | 710 |
| Encinas y robles | +5 | 44% / 1.54 | 45% / 1.12 | **55% / 0.87** | 55% / 0.95 | 710 |
| Olivo | +1 | 43% / 2.00 | 47% / 1.37 | 53% / 1.13 | **64% / 0.83** | 513 |
| Olivo | +2 | 43% / 2.00 | 45% / 1.43 | 53% / 1.12 | **65% / 0.82** | 513 |
| Olivo | +3 | 43% / 2.00 | 44% / 1.42 | 54% / 1.11 | **65% / 0.77** | 513 |
| Olivo | +4 | 43% / 2.00 | 44% / 1.43 | 52% / 1.12 | **69% / 0.77** | 513 |
| Olivo | +5 | 43% / 2.00 | 42% / 1.50 | 50% / 1.17 | **63% / 0.82** | 513 |
| Gramíneas | +1 | 43% / 1.37 | 54% / 0.80 | 53% / 0.70 | **55% / 0.67** | 729 |
| Gramíneas | +2 | 43% / 1.37 | 49% / 0.88 | 53% / 0.72 | **53% / 0.67** | 729 |
| Gramíneas | +3 | 43% / 1.37 | 47% / 0.94 | 50% / 0.74 | **52% / 0.69** | 729 |
| Gramíneas | +4 | 43% / 1.37 | 44% / 0.99 | 49% / 0.76 | **52% / 0.72** | 729 |
| Gramíneas | +5 | 43% / 1.37 | 42% / 1.05 | 50% / 0.78 | **51% / 0.77** | 729 |
| Pinos | +1 | 54% / 1.45 | 56% / 0.92 | **65% / 0.72** | 65% / 0.77 | 484 |
| Pinos | +2 | 54% / 1.45 | 54% / 0.97 | 64% / 0.74 | **65% / 0.80** | 484 |
| Pinos | +3 | 54% / 1.45 | 54% / 1.00 | 64% / 0.74 | **66% / 0.78** | 484 |
| Pinos | +4 | 54% / 1.45 | 51% / 1.02 | **64% / 0.74** | 64% / 0.79 | 484 |
| Pinos | +5 | 54% / 1.45 | 50% / 1.05 | **65% / 0.75** | 65% / 0.82 | 484 |
| Chopos | +1 | 59% / 2.01 | 63% / 1.24 | 66% / 1.08 | **68% / 1.05** | 417 |
| Chopos | +2 | 59% / 2.01 | 62% / 1.30 | 63% / 1.15 | **68% / 1.12** | 417 |
| Chopos | +3 | 59% / 2.01 | 62% / 1.35 | 61% / 1.19 | **68% / 1.14** | 417 |
| Chopos | +4 | 59% / 2.01 | 62% / 1.41 | 59% / 1.23 | **67% / 1.19** | 417 |
| Chopos | +5 | 59% / 2.01 | 59% / 1.47 | 58% / 1.25 | **68% / 1.24** | 417 |
| Olmos | +1 | 47% / 1.54 | 55% / 1.12 | 65% / 0.82 | **66% / 0.82** | 360 |
| Olmos | +2 | 47% / 1.54 | 53% / 1.28 | **64% / 0.87** | 62% / 0.84 | 360 |
| Olmos | +3 | 47% / 1.54 | 49% / 1.47 | 61% / 0.94 | **62% / 0.89** | 360 |
| Olmos | +4 | 47% / 1.54 | 44% / 1.64 | 59% / 0.98 | **61% / 0.89** | 360 |
| Olmos | +5 | 47% / 1.54 | 42% / 1.80 | **57% / 1.03** | 56% / 0.98 | 360 |
| Llantenes | +1 | 39% / 1.30 | 53% / 0.66 | **58% / 0.57** | 57% / 0.54 | 611 |
| Llantenes | +2 | 39% / 1.30 | 51% / 0.71 | 56% / 0.60 | **57% / 0.54** | 611 |
| Llantenes | +3 | 39% / 1.30 | 51% / 0.74 | 54% / 0.62 | **56% / 0.54** | 611 |
| Llantenes | +4 | 39% / 1.30 | 50% / 0.78 | 52% / 0.64 | **54% / 0.57** | 611 |
| Llantenes | +5 | 39% / 1.30 | 48% / 0.83 | **52% / 0.66** | 51% / 0.60 | 611 |
| Ortigas y parietarias | +1 | 51% / 1.47 | 59% / 0.89 | **63% / 0.80** | 60% / 0.85 | 652 |
| Ortigas y parietarias | +2 | 51% / 1.47 | 56% / 0.91 | **62% / 0.89** | 61% / 0.86 | 652 |
| Ortigas y parietarias | +3 | 51% / 1.47 | 57% / 0.93 | **62% / 0.81** | 60% / 0.86 | 652 |
| Ortigas y parietarias | +4 | 51% / 1.47 | 54% / 0.96 | **61% / 0.91** | 59% / 0.89 | 652 |
| Ortigas y parietarias | +5 | 51% / 1.47 | 52% / 0.99 | **62% / 0.86** | 60% / 0.89 | 652 |

**Intervalos del 80 %: cobertura real en 2025 (temporada), antes y después de calibrarlos con 2024**

| Tipo de polen | Modelo usado | Antes | Después | Anchura media (log): antes → después | Fourier (pares) | Orden ARMA |
|---|---|---|---|---|---|---|
| Plátano de paseo | Gradient boosting | 77% | 89% | 2.43 → 3.18 | 8 | (3, 0, 0) |
| Cipreses y arizónicas | Gradient boosting | 74% | 77% | 2.94 → 3.14 | 8 | (2, 0, 1) |
| Encinas y robles | ARIMAX | 63% | 76% | 1.94 → 2.49 | 6 | (2, 0, 1) |
| Olivo | ARIMAX | 43% | 66% | 1.57 → 2.68 | 8 | (2, 0, 1) |
| Gramíneas | Gradient boosting | 71% | 80% | 1.95 → 2.26 | 8 | (2, 0, 1) |
| Pinos | Gradient boosting | 78% | 86% | 2.47 → 3.00 | 8 | (2, 0, 1) |
| Chopos | Gradient boosting | 60% | 69% | 2.37 → 2.88 | 8 | (2, 0, 1) |
| Olmos | Gradient boosting | 66% | 78% | 2.07 → 2.54 | 8 | (1, 0, 2) |
| Llantenes | ARIMAX | 60% | 79% | 1.33 → 1.88 | 8 | (2, 0, 1) |
| Ortigas y parietarias | Gradient boosting | 63% | 68% | 1.94 → 2.13 | 7 | (2, 0, 1) |
| **Media (modelo usado)** | | 66% | 77% | | | |
| Media gradient boosting (10 tipos) | | 72% | 80% | | | |
| Media ARIMAX (10 tipos) | | 54% | 79% | | | |

**Nivel leído de la mediana frente al clasificador antiguo (2025, temporada, media de 10 tipos):** nivel exacto 61.3% (mediana) frente a 61.7% (clasificador); tres niveles de la aplicación 68.1% frente a 69.3%.

| Serie | ADF (serie) | p | ADF (sin ciclo anual) | p | Días con cero | Fuerza estacional (STL) |
|---|---|---|---|---|---|---|
| Gramíneas · Madrid-Barrio Salamanca | -6.0 | 0.0000 | -8.4 | 0.0000 | 44% | 0.78 |
| Plátano de paseo · Madrid-Barrio Salamanca | -8.4 | 0.0000 | -11.0 | 0.0000 | 78% | 0.75 |
| Olivo · Madrid-Barrio Salamanca | -7.8 | 0.0000 | -10.1 | 0.0000 | 69% | 0.74 |
