# Auditoría de los datos de Respira MAD

*19 de noviembre de 2026. Todavía no se ha cambiado nada en la aplicación ni en los modelos:
este documento propone reglas de limpieza para que se aprueben antes de aplicarlas.*

**Qué se ha revisado:** los archivos **tal como se publican**, sin limpiar:

| Fuente | Periodo revisado | Tamaño |
|---|---|---|
| Comunidad de Madrid, calidad del aire (horario) | 2019 – 1 nov 2026 | 5,9 millones de horas, 28 estaciones |
| Ayuntamiento de Madrid, calidad del aire (horario) | 2019 – 1 nov 2026 | 4,0 millones de horas, 24 estaciones |
| Red Palinocam (Comunidad), polen (diario) | 1993 – 17 nov 2026 | 3,3 millones de filas, 11 captadores, 27 tipos |
| Open-Meteo, tiempo (lo que descargaron los modelos) | 1993 – 2026 (diario), 2019 – 2026 (horario) | 86.555 días, 549.504 horas |

**Cómo repetirlo:** el código está en `data_audit.py` y los gráficos en
`notebooks/exploracion_datos.ipynb` (abrir con `.venv/bin/jupyter lab notebooks/exploracion_datos.ipynb`).
Las cifras de este documento salen de ese mismo código.

**Para leer las tablas:** "entrenar" = datos hasta 2023, "elegir" = 2024, "prueba" = 2025
(la división que usan los dos modelos).

---

## Resumen: lo más importante

1. **Los archivos horarios de la Comunidad parecen estar en hora UTC; los del Ayuntamiento, en hora
   de Madrid** (problema A1). Una lectura de la Comunidad lleva una hora 1 h antes que la real en
   invierno y 2 h antes en verano. Afecta al 59 % de todas las horas de aire, a la aplicación en
   directo y al modelo. Es el hallazgo con más consecuencias y conviene confirmarlo a mano con la web
   oficial antes de corregirlo.
2. **¿Los días sin dato de polen se tratan como cero? No.** Una casilla vacía se queda siempre como
   "sin dato", en el entrenamiento y en la aplicación (P1). **El problema es el contrario:** hay 49
   días en los que un captador publica *todos* los tipos a 0 mientras el resto de la red ve más de
   100 granos/m³; casi seguro son "no se midió" escrito como cero (P3).
3. **La estación morada (Orusco del Tajuña, 17/11/2026) era un error de la fuente**, y la propia
   Comunidad lo ha corregido: en el archivo mensual validado esas horas aparecen ahora como no válidas
   (A8). Nuestra regla de "dato dudoso" acertó.
4. El año que usamos para **elegir** los modelos de polen (2024) tiene **tres captadores menos**
   (Alcalá, Las Rozas casi sin datos; Leganés cerró en 2022) (P11).
5. Los valores más extremos son **reales**: el polvo sahariano del 15/03/2022 (PM10 hasta 1.109 µg/m³)
   y el plátano de paseo en Alcalá (hasta 22.056 granos/m³, todas las primaveras) (A7, P5).
6. Pequeños problemas de formato que ya se resolvían, pero sin un único sitio que lo garantice:
   codificaciones mezcladas, nombres de polen rotos en 2021–2022, formato de fecha distinto en 2026,
   duplicados que coinciden, banderas desconocidas, valores negativos.

---

## 1. Polen (Red Palinocam)

### P1. Casillas vacías: ¿se tratan como cero?

- **Qué pasa:** el 8,3 % de las filas no tiene número. Por captador y día: 106.781 días completos,
  **8.882 días todo en blanco** (el captador no midió) y 6.176 días parciales (ver P2).
- **Cómo se trata hoy:** como **sin dato**, nunca como cero. En el entrenamiento la fila se descarta y
  el modelo usa la última lectura conocida (como mucho 10 días, `MAX_CARRY_DAYS`) junto con su
  antigüedad. En la aplicación, el día sin lectura no existe: se muestra la última lectura y su fecha,
  y los días entre esa lectura y hoy los estima el modelo (rotulados "estimado").
- **Regla propuesta:** **no cambiar**. Una casilla vacía es "sin dato". Se deja escrito en
  `cleaning.py` para que sea una regla explícita y probada.

### P2. Días parciales: unos tipos sí, otros no

- **Qué pasa:** casi todos son de 1993–1995. En 1993 (de mayo a diciembre) **solo se contaron las
  gramíneas** (1.366 días); el eucalipto no se contó hasta 1996.
- **Cuánto afecta:** solo a datos de entrenamiento muy antiguos. En esos días, para los otros nueve
  tipos, vacío = "no se contó", no cero.
- **Cómo se trata hoy:** como sin dato (correcto).
- **Regla propuesta:** no cambiar (cubierta por P1).

### P3. Ceros que en realidad son "no se midió"

- **Qué pasa:** 49 días de un captador con **todos** los tipos publicados exactamente a 0 mientras la
  mediana del resto de la red ese día supera 100 granos/m³. Ejemplos: Alcobendas el 21/05/2018
  (26 tipos a 0, red 342); el captador ALER el 10/04/2011 (27 tipos a 0, red 587).
  (Los días "todo a cero" en otoño-invierno, 4.275 en total, son creíbles y no se tocan.)
- **Cuánto afecta:** 490 filas de los 10 tipos que usamos: 45 días en entrenar, 3 en elegir y 1 en la
  prueba.
- **Cómo se trata hoy:** como ceros reales, es decir, como nivel "bajo".
- **Regla propuesta:** si en un captador y día **todos** los tipos valen 0 y la mediana del total de
  la red ese día es mayor que 100 granos/m³, **todo el día pasa a "sin dato"**.

### P4. Picos de un solo día sin apoyo de la red

- **Qué pasa:** 250 días en los que un tipo vale al menos 100 granos/m³ y más de 10 veces el día
  anterior y el siguiente en el mismo captador. En **63** de ellos, además, el resto de la red ve
  menos de la décima parte (ejemplo: pinos en Alcalá el 31/03/2004, 742 granos/m³ frente a 3 en la
  red). Por tipo: cipreses 27, chopos 11, pinos 8, encinas 5, gramíneas 3, olmos 3, plátano 3,
  olivo 2, llantenes 1.
- **Cuánto afecta:** 58 en entrenar, 4 en elegir, 1 en la prueba (de más de un millón de filas).
- **Cómo se trata hoy:** se usan.
- **Ojo:** un pico local puede ser real (un seto de cipreses junto al captador). No hay forma de
  comprobarlo con los datos publicados.
- **Regla propuesta:** marcar esos 63 como **dudosos** y no usarlos para entrenar ni para medir el
  acierto (se guardan en una lista para revisarla). Los otros 187 picos, con apoyo de la red, se
  quedan.

### P5. Valores extremos que son reales

- Plátano de paseo en Alcalá: 22.056 granos/m³ (01/04/2011) y valores de 14.000–17.000 en 2009, 2010,
  2011 y 2019, siempre a finales de marzo o en abril. Es el mismo patrón cada año, no un error.
- **Regla propuesta:** no tocar.

### P6. Valores imposibles

- **Qué pasa:** un solo valor negativo (−4, "Polen no identificado", VILL, 10/01/2004). Ningún código
  centinela (−9999 o similar). Todos los valores no vacíos son números.
- **Cuánto afecta:** ninguno de los 10 tipos que usamos.
- **Regla propuesta:** un valor negativo pasa a "sin dato" (regla general, por si aparece).

### P7. Nombres de tipos rotos (2021 y 2022)

- **Qué pasa:** en los archivos de 2021 y 2022, cinco nombres llevan un "?" en lugar de la letra con
  tilde: `Gram?neas`, `Pl?tano de paseo`, `Cupres?ceas/Tax?ceas`, `Casta?o`,
  `Quenopodi?ceas/Amarant?ceas` (7.991 filas cada uno).
- **Cuánto afecta:** tres de nuestros tipos principales, dos años enteros de entrenamiento.
- **Cómo se trata hoy:** el entrenamiento los repara (`BROKEN_NAMES` en `train_pollen_model.py`); la
  aplicación no los necesita (solo lee el año actual y el anterior).
- **Regla propuesta:** la reparación pasa a `cleaning.py`, en un solo sitio para todo el proyecto.

### P8. Codificación de caracteres mezclada

- **Qué pasa:** 27 archivos anuales están en Latin-1 y 5 en UTF-8 (2021, 2022, 2026 y el del año en
  curso).
- **Cuánto afecta:** el entrenamiento ya lo resolvía. **La aplicación no:** hasta el 18/11/2026 leía
  el archivo de 2025 (Latin-1) como UTF-8 y perdía las filas de gramíneas, plátano y cipreses del año
  anterior (corregido ese día en `data._read_csv`).
- **Regla propuesta:** leer siempre probando UTF-8 y, si falla, Latin-1 (ya es así desde el 18/11).

### P9. Formato de fecha distinto en 2026

- **Qué pasa:** hasta 2025 la fecha es `1993/05/01 00:00:00.000`; desde 2026, `2026-01-01 00:00:00.0`.
- **Cómo se trata hoy:** las dos lecturas (entrenamiento y aplicación) toman los 10 primeros
  caracteres y cambian "/" por "-", así que funcionan.
- **Regla propuesta:** aceptar solo esos dos formatos y **avisar** si aparece otro, en vez de
  descartar filas sin decir nada.

### P10. Filas duplicadas

- **Qué pasa:** 84.455 combinaciones de captador, día y tipo aparecen dos veces. Casi todas vienen
  del solapamiento entre el archivo del año en curso y el de 2026; otras 6.285 filas están repetidas
  dentro de los archivos históricos (2012–2020 y 2025).
- **Cuánto afecta:** nada en los valores: **en los 82.370 casos con dos números, las copias
  coinciden**.
- **Regla propuesta:** quedarse con una copia (como ahora) y avisar si alguna vez no coinciden.

### P11. Huecos por captador, cierres y cambios

| Captador | Qué pasa |
|---|---|
| Leganés (LEGA) | Deja de medir el 11/04/2022 (cerrado) |
| Alcalá (ALCA) | 17 % de los días con dato en 2023, 0 % en 2024 |
| Las Rozas (ROZA) | Empieza en 2009; 0 % en 2024 |
| Villaverde (VILL) | Empieza en 1999; 18 % en 2024 |
| AYTM | Muy irregular entre 1996 y 2005 |
| Todos | Bajan en 2020 (confinamiento) |

- **Cuánto afecta:** el año que se usa para **elegir** modelo y calibrar intervalos (2024) tiene tres
  captadores menos, así que la elección se apoya en menos estaciones.
- **Cambios de ubicación:** el archivo oficial de captadores solo da la ubicación actual y la fecha de
  inicio; no hay historial de traslados, así que **no se puede comprobar si alguno se movió**.
- **Regla propuesta:** no se limpia nada aquí (no hay nada que corregir), pero el informe de los
  modelos dirá con cuántos captadores se eligió y se probó.

### P12. Tipos que aparecen más tarde

- "Arce" y "Otros" solo existen desde el 19/04/2004. No son de los 10 tipos que usamos.
- **Regla propuesta:** ninguna.

### P13. Decimales

- En 1993 el 96 % de los valores tiene decimales; después, entre el 2 % y el 25 % según el año. No es un
  error: los granos/m³ se calculan a partir del recuento con un factor de corrección.
- **Regla propuesta:** ninguna.

---

## 2. Calidad del aire (Comunidad y Ayuntamiento)

### A1. Horario: la Comunidad parece estar en UTC y el Ayuntamiento en hora local

**Este es el hallazgo más importante.** Tres comprobaciones independientes apuntan a lo mismo:

1. **El pico de NO2 de la mañana** (sigue al reloj: la gente va al trabajo a la misma hora todo el
   año). En los archivos del Ayuntamiento está siempre en la hora 9. En los de la Comunidad pasa de
   la hora 8 a la 7 con el cambio de marzo y vuelve a la 8 en octubre, **en 7 de los 8 años**.
2. **El pico de ozono de la tarde** (sigue al sol, no al reloj): en los archivos del Ayuntamiento se
   mueve +1,0 h en marzo y −1,2 h en octubre (lo esperado en hora local); en los de la Comunidad,
   apenas ±0,2 h (lo esperado en una hora fija).
3. **Estaciones vecinas de las dos redes** (12 parejas a menos de 12 km, por ejemplo Getafe y
   Villaverde a 3,6 km): las series coinciden mejor desplazando la de la Comunidad **1 hora en
   invierno y 2 en verano** (en 10 de 12 parejas). Esa es exactamente la diferencia entre la hora UTC
   y la de Madrid.

**En directo también:** el 19/11/2026 a las 14:47, la última lectura de la Comunidad era de las 13:00 y
la del Ayuntamiento de las 14:00; con las lecturas de hoy, las parejas vecinas con más correlación
(Leganés y Móstoles frente a Villaverde) coinciden con 1 hora de desfase. Solo hay 12 horas para
comparar, así que en directo la prueba es más débil. Lo que en `CLAUDE.md` se describe como "la
Comunidad va 1–2 horas por detrás" probablemente es esto y no un retraso.

- **Cuánto afecta:** **todas las horas de la Comunidad** (el 59 % de las horas válidas de aire), en
  entrenar, elegir y probar, y en la aplicación en directo.
- **Qué provoca hoy:**
  - En la aplicación, una lectura de la Comunidad parece 1 h más vieja en invierno y 2 h en verano.
    La regla de "más de 3 horas = no se usa" puede descartar en verano estaciones que en realidad
    tienen datos de hace 1 hora.
  - Se comparan y mezclan lecturas de las dos redes que no son de la misma hora real.
  - En el modelo de aire, la previsión del tiempo (Open-Meteo, en hora local) queda desalineada 1–2 h
    con las lecturas de la Comunidad, y "la misma hora ayer" o "la hora del día" significan cosas
    distintas en cada red.
  - El día de demostración (28 de mayo de 2025, verano) tiene las horas de la Comunidad 2 h corridas.
- **Regla propuesta:** convertir las horas de la Comunidad **de UTC a la hora de Madrid** (con
  `zoneinfo`, que aplica el cambio de hora solo), tanto en la historia como en directo.
  **Antes de aplicarla, comprobarlo a mano:** comparar un par de horas de una estación de la
  Comunidad en su web oficial con nuestros datos (`check_stations.py` imprime las lecturas). Si la web
  confirma el desfase, se aplica; si no, se investiga más.

### A2. Una estación con dos analizadores a la vez

- **Qué pasa:** en 2019, la estación 28123002 (ozono) publica **dos series completas** (puntos de
  muestreo `_14_6` y `_14_8`), 8.760 horas cada una. Difieren en 3,6 µg/m³ de media (máximo 25).
- **Cómo se trata hoy:** el entrenamiento se queda con la primera copia que lee, por casualidad.
- **Regla propuesta:** usar la **media de las dos** cuando ambas son válidas (y la que haya, si solo
  una lo es), y avisar si aparece otro caso igual.

### A3. Banderas

| Bandera | Significado | Cuántas (2019–2026) | Cómo se trata hoy |
|---|---|---|---|
| V | válido | 9,65 millones | se usa |
| T | provisional (solo en los archivos del día) | — en la historia | se usa |
| N | no válido | 208.101 | se descarta |
| M | **no documentada** | 3 (Ayuntamiento, 28079040, PM10, 13/01/2025) | se descarta |

- **Ojo:** en el Ayuntamiento, **74.409 horas marcadas N sí traen un número**, a veces absurdo (de −60
  a 89.337). Hoy se descartan bien porque solo se usan V y T.
- **Regla propuesta:** solo V y T son válidas (como ahora); cualquier otra bandera, incluida M, es
  "sin dato". Queda escrito y probado en `cleaning.py`.

### A4. Valores negativos

- **Qué pasa:** 239 negativos; solo **2 con bandera V** (PM2.5 del Ayuntamiento: −12 y −1). El resto
  llevan N.
- **Cómo se trata hoy:** el **entrenamiento** los descarta; la **aplicación en directo no** (solo mira
  la bandera). Son reglas distintas.
- **Regla propuesta:** un valor negativo es "sin dato", en los dos sitios. No hay códigos centinela
  (−9999 o similares).

### A5. Ceros

- 7.753 horas válidas a 0 (NO2 1.254, O3 369, PM10 1.776, PM2.5 4.354). Como mucho el 1,5 % de las horas
  de una estación (PM2.5 en algunas del Ayuntamiento). Son valores por debajo del límite de detección.
- **Regla propuesta:** se quedan.

### A6. Series planas (el mismo valor muchas horas seguidas)

- 714 tramos de 24 h o más con exactamente el mismo valor (28.703 horas). **Todos son de 1 o 2 µg/m³**
  (casi siempre NO2 en estaciones rurales como Rascafría u Orusco): es el valor mínimo que da el
  aparato. Ya se comprobó el 15/11/2026 que esos valores bajos son reales. Ningún tramo plano supera
  5 µg/m³.
- **Regla propuesta:** se quedan; avisar si apareciera un tramo plano de 24 h por encima de 5 µg/m³.

### A7. Valores extremos que son reales

- **PM10 hasta 1.109 µg/m³ y PM2.5 hasta 432**, el 15/03/2022: el episodio de polvo sahariano (calima).
  Todas las estaciones suben a la vez.
- **NO2 hasta 328 µg/m³** en Plaza Elíptica (28079056), tardes de invierno de 2019: una estación de
  tráfico conocida.
- **Regla propuesta:** no tocar.

### A8. "Dato dudoso" (la estación morada)

- **Qué pasa:** aplicando la regla de la aplicación a 2019–2026 (un valor Desfavorable o peor
  mientras la mediana de las demás estaciones está en Buena o Razonablemente buena, y más de 4 veces
  esa mediana) salen **8.341 horas** (el 0,09 % de las válidas): PM2.5 5.031, PM10 3.033, NO2 258,
  O3 19. Más frecuentes en Collado Villalba, Plaza Castilla, Urb. Embajada, Escuelas Aguirre y Plaza
  Elíptica (PM2.5 sobre todo). Por división: 5.507 en entrenar, 1.015 en elegir, 650 en la prueba.
- **Orusco del Tajuña, 17/11/2026:** en directo publicó PM10 de hasta 437 µg/m³ como provisional (T).
  **En el archivo mensual validado, esas horas (del 16/11 a las 10:00 al 18/11 a las 10:00) aparecen
  ahora como N (no válidas).** La red las descartó al validarlas. En la historia, su PM10 máximo
  (889 µg/m³) es el día de la calima.
- **Cómo se trata hoy:** la aplicación excluye el dato dudoso en directo; el entrenamiento lo usa.
- **Ojo:** las 8.341 horas históricas **ya pasaron la validación oficial** (bandera V). Pueden ser
  episodios locales reales (obras, tráfico, fuegos artificiales).
- **Regla propuesta:** **para los datos en directo (provisionales), mantener la regla** (lo de
  Orusco demuestra que funciona). **Para la historia validada, no excluirlas** del entrenamiento: la
  red ya las revisó. Se marcan, y el informe del modelo dirá si cambian los resultados.

### A9. Huecos, estaciones nuevas y cierres

- **Cobertura:** de 1.161 combinaciones de estación, contaminante y año, 22 tienen menos del 75 % de
  horas válidas. **62 huecos de 7 días o más** (1.872 días en total). El más largo: 28014002, ozono,
  todo 2022.
- **Estaciones nuevas:** cuatro de la Comunidad en julio–agosto de 2023 (28106001, 28115003, 28127004,
  28134002); partículas nuevas en 28079004, 28079054 y 28079060 desde enero de 2025.
- **Cierres:** 28079055 (Urb. Embajada) deja de medir a finales de 2024; Plaza Elíptica deja de medir
  ozono en mayo de 2021.
- **Ubicaciones:** todas las estaciones con datos tienen coordenadas. Como en el polen, solo se publica
  la ubicación actual: **no se puede comprobar si alguna se ha movido**.
- **Cómo se trata hoy:** el modelo de aire solo pronostica con una lectura de hace 6 horas como mucho y
  no rellena huecos.
- **Regla propuesta:** no rellenar huecos. Solo se documentan.

### A10. ¿Coinciden las dos redes?

| | Comunidad | Ayuntamiento | ¿Problema? |
|---|---|---|---|
| Código de estación | 8 cifras: provincia + municipio + estación | igual | No. No comparten estaciones (la Comunidad no publica las del municipio de Madrid, 079) |
| Contaminantes | 8 NO2, 9 PM2.5, 10 PM10, 14 O3 | igual | No |
| Unidades | µg/m³ | µg/m³ | No |
| Método de medida (punto de muestreo) | partículas `_49` | partículas `_47` | Puede haber pequeñas diferencias sistemáticas; el significado del código no viene en los archivos |
| Decimales | coma (`0,4`) | punto (`0.4`) | No, el código acepta los dos |
| Banderas | V, N (y T en directo) | V, N y una M sin documentar | Ver A3 |
| Hora | **UTC (aparentemente)** | hora local | **Sí, ver A1** |
| h01 … h24 | hora que termina a la 01:00 … a las 24:00 | igual | No |

### A11. Días de cambio de hora

- Los dos archivos tienen siempre 24 columnas (h01 … h24), también el último domingo de marzo (que en
  Madrid tuvo 23 horas) y el de octubre (25 horas). En una hora fija como UTC, eso es correcto; en hora
  local, un día al año sobra una hora y otro falta.
- **Cuánto afecta:** 2 días al año.
- **Regla propuesta:** con la conversión de A1, la Comunidad queda bien. Para el Ayuntamiento no se
  puede saber qué hora se repite o falta; se deja como está y se documenta.

### A12. Novedad: el Ayuntamiento sí ha publicado 2025

- El archivo "actual" del Ayuntamiento ya contiene **todo 2025 y 2026** (hasta el 1 de noviembre).
  `CLAUDE.md` dice que el Ayuntamiento no había publicado 2025, así que el día de demostración
  (28/05/2025) podría incluir también sus 24 estaciones.
- **Regla propuesta:** no es limpieza; se apunta como mejora posible.

---

## 3. Tiempo (Open-Meteo)

- **Huecos:** ninguno (ni días ni horas que falten). **Duplicados:** ninguno. **Vacíos:** ninguno.
- **Rangos:** temperatura de −11,9 a 43,4 °C, humedad del 5 al 100 %, lluvia hasta 95 mm en un día,
  viento hasta 52 km/h: todo creíble para Madrid.
- **Días de cambio de hora:** el archivo tiene 24 horas también el día de marzo (incluida una "02:00"
  que no existió en Madrid). Afecta a 2 días al año. **Regla propuesta:** ninguna.
- **Diseño, no error:** el tiempo diario de los 11 captadores sale de 7 puntos (los captadores a menos
  de ~10 km comparten serie, `WEATHER_FROM`).
- **Relación con A1:** el tiempo está en hora local; si las lecturas de la Comunidad están en UTC, el
  modelo de aire las cruza con 1–2 h de desfase. La corrección de A1 lo resuelve.

---

## 4. Reglas de limpieza propuestas (resumen para aprobar)

Todas irían en un solo módulo, `cleaning.py`, usado por el entrenamiento **y** por la aplicación,
para que las dos usen exactamente las mismas reglas.

| # | Regla | Datos afectados | Cambia resultados |
|---|---|---|---|
| A1 | Horas de la Comunidad: de UTC a hora de Madrid (**tras comprobarlo con la web oficial**) | 59 % de las horas de aire | Sí, bastante (modelo de aire y aplicación) |
| A2 | Dos analizadores a la vez: media de las dos copias válidas | 8.760 horas | Muy poco |
| A3 | Solo banderas V y T; cualquier otra (N, M…) = sin dato | igual que ahora (+3 horas M) | No |
| A4 | Valor negativo = sin dato, también en directo | 2 horas con V | No |
| A8 | "Dato dudoso": se mantiene en directo; en la historia validada se marca pero no se excluye | 8.341 horas marcadas | No (se informa) |
| P1 | Casilla vacía de polen = sin dato, nunca cero (ya es así; queda escrito y probado) | 8,3 % de las filas | No |
| P3 | Día con todos los tipos a 0 y la red > 100 granos/m³ = sin dato | 49 días, 490 filas | Poco |
| P4 | Pico de un día sin apoyo de la red (> 10× días vecinos y > 10× la red) = dudoso, fuera del entrenamiento y de la medida del acierto | 63 filas | Poco |
| P6 | Valor de polen negativo = sin dato | 1 fila (no usada) | No |
| P7 | Reparar nombres rotos (`Gram?neas` → `Gramíneas`, etc.) en un solo sitio | 7.991 filas × 5 tipos | No (ya se hacía) |
| P8–P9 | Codificación UTF-8 o Latin-1; solo los dos formatos de fecha conocidos, avisar si hay otro | todos los archivos | No |
| P10 | Duplicados: una copia; avisar si no coinciden | 84.455 grupos | No |

**Lo que no se toca** (y por qué): los valores extremos reales (calima, plátano en Alcalá), los ceros y
las series planas de valores bajos, los huecos (no se inventan datos) y los días de cambio de hora del
Ayuntamiento.

**Después de aprobarlo:** se crea `cleaning.py` con estas reglas y sus pruebas, se vuelven a entrenar
los dos modelos con los datos limpios y se compara el acierto antes y después (por contaminante, por
tipo de polen y por día de antelación), en un apartado nuevo de `MODEL_REPORT.md`.
