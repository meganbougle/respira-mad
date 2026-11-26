# Respira MAD: el proyecto completo

*Documento de referencia para preparar la presentación. Estado a 26 de noviembre de 2026.
Todas las cifras salen de `MODEL_REPORT.md`, `DATA_AUDIT.md`, `CLAUDE.md` y de las comprobaciones
de la aplicación en directo de ese día.*

**App:** https://respira-mad.onrender.com · **Código:** https://github.com/meganbougle/respira-mad ·
Hackathon **DataMad 2026**

---

## 1. En una frase

**Respira MAD te dice si es seguro salir ahora mismo en la Comunidad de Madrid si tienes asma,
alergia al polen, eres una persona mayor o cuidas de un niño pequeño**, combinando la calidad del
aire en directo, el polen y el tiempo, con previsiones propias hechas solo con datos oficiales y
recomendaciones oficiales citadas palabra por palabra.

---

## 2. El problema

- Las personas más sensibles (asma y enfermedades respiratorias, alergia al polen, mayores de 65,
  niños de 0 a 6 años) tienen que decidir **cada día** si salir, cuándo y cuánto tiempo.
- La información existe, pero está **dispersa y es técnica**:
  - la calidad del aire está en dos redes distintas (Comunidad de Madrid y Ayuntamiento de Madrid),
    en µg/m³ y en archivos horarios;
  - el polen se publica en otro portal, **con 3 a 7 días de retraso** y sin previsión;
  - los niveles oficiales (índice ICA, niveles de polen) y las recomendaciones oficiales están en
    otros documentos (BOE, folletos de la Comunidad).
- Nadie junta las tres cosas, para **tu** punto exacto y para **tu** perfil, ni te dice qué
  **horas** de hoy y de los próximos días son mejores.

---

## 3. La solución: qué ve la persona

| Parte | Qué hace |
|---|---|
| **Resultado de ahora** | Tres niveles: «Puedes salir», «Precaución», «Mejor quédate en casa». El titular dice la causa: «Precaución por polen de gramíneas», «Mejor quédate en casa por el ozono (O3)». |
| **Recomendación oficial** | Debajo, el texto oficial que corresponde, **literal y con su fuente** (BOE para el aire, folleto de la Comunidad para el polen). Nunca escribimos consejos propios. |
| **Perfil** | General, Asma / respiratorio, Persona mayor, Niño/a. Los tres últimos reciben las recomendaciones oficiales para «grupos de riesgo y personas sensibles». |
| **Polen que cuenta** | Botones con los 10 tipos de polen con umbral oficial. Marcados de inicio, los 5 que la Comunidad señala como principales causantes de alergia en la región (cipreses y arizónicas, plátano de paseo, gramíneas, llantenes, olivo). «Ninguno»: el polen se muestra pero no cambia el resultado. |
| **Próximas 24 horas** | Franja hora a hora (día y noche) y las **mejores franjas de 3 horas** para salir, con temperatura y lluvia. |
| **Planificador de 5 días** | Hoy y 4 días más, cada uno en mañana, tarde, noche y madrugada, con su nivel, temperatura y una etiqueta de confianza (alta, media, baja). Al tocar una casilla se explica en qué se basa. |
| **Evolución y previsión** | Gráfico de polen (7 días medidos + 5 de previsión, con intervalo del 80 %) y de aire (hora a hora, sobre las bandas oficiales). |
| **Mapa** | Todas las estaciones oficiales de aire (colores oficiales del ICA) y de polen. Se resaltan las que dan tu resultado, con su distancia. Se puede tocar cualquier punto del mapa. |
| **Frescura de los datos** | De cada fuente: hora del dato, hace cuánto, si es provisional, si es una copia de seguridad. |
| **Asistente con IA** | «Pregúntale a Respira»: preguntas en lenguaje natural («¿puedo salir a correr a las 7 en Getafe?») respondidas solo con los datos de la app y los textos oficiales. |
| **Modo demostración** | Reproduce el **28 de mayo de 2025**, el día con más polen de gramíneas de todo el histórico (desde 1993): **392 granos/m³ de media en la red**. Un control elige la hora del día. |

**Diseño:** pensada primero para el móvil (390 px) y después para el portátil; todo en español; los
colores nunca son la única señal (cada nivel tiene icono y texto); sin emojis; se actualiza sola
cada 5 minutos.

---

## 4. Guion sugerido para la demo (5 minutos)

1. **Abrir la app** (en directo): resultado de ahora para Madrid centro, con su causa y el texto
   oficial. Señalar el indicador «En directo · hace X min».
2. **Cambiar de lugar** (Alcalá de Henares) y **tocar el mapa**: el punto pasa a llamarse
   «Cerca de …» y se resaltan las estaciones que dan el resultado.
3. **Cambiar a Asma**: mismo dato, otro umbral y otro texto oficial.
4. **Planificador**: tocar una casilla y enseñar en qué se basa y la confianza.
5. **Gráfico**: Polen y Aire; abrir «¿Cómo predecimos?».
6. **Modo demostración (28 de mayo de 2025)**, con los tipos de polen de inicio marcados:
   «Mejor quédate en casa por polen de gramíneas». Mover la hora del día.
7. **Asistente**: una pregunta real.

Consejos: abrir la web 10 minutos antes; en el modo demostración usar lugares de la lista o puntos
cerca de Madrid o Alcalá (un punto lejano sin tiempo guardado puede tardar 10–20 s); no elegir
«Ninguno» en el modo demostración (el polen dejaría de contar y saldría «Puedes salir»).

---

## 5. Los datos

### La regla de datos (permanente)

**Todos los datos de aire y de polen, medidos y previstos, salen solo de la Comunidad de Madrid y
del Ayuntamiento de Madrid.** Nuestras previsiones se hacen con esos datos. Solo el **tiempo** y la
**localización de lugares** vienen de fuera (Open-Meteo). No usamos previsiones de calidad del aire
o de polen de terceros (por ejemplo, CAMS), en ninguna parte.

### Fuentes

| Qué | Fuente | Notas |
|---|---|---|
| Calidad del aire, horaria (NO2, O3, PM10, PM2.5) | Comunidad de Madrid (portal CKAN): 28 estaciones | Los datos del día son provisionales (sin validar) y la app lo dice |
| Calidad del aire, horaria | Ayuntamiento de Madrid (tiempo real): 23–24 estaciones | Unos 60 minutos de retraso |
| Polen, diario | Red Palinocam, Comunidad de Madrid: 11 captadores en el histórico, 27 tipos, desde 1993 | Recuento manual: se publica con 3 a 7 días de retraso; la app siempre muestra la fecha y la antigüedad de la lectura |
| Niveles de aire y recomendaciones | Índice Nacional de Calidad del Aire, Resolución de 2 de septiembre de 2020 (BOE-A-2020-10426) | 6 categorías, textos para población general y para grupos sensibles, copiados literalmente |
| Niveles de polen | Informe Ayuntamiento de Madrid / UCM (noviembre de 2025, datos 2010–2019) | Bajo, medio, alto y muy alto para 10 tipos |
| Recomendaciones de polen | Folleto de la Comunidad de Madrid «Lo que debes conocer sobre la alergia al polen» | Literal |
| Grupos sensibles | Página de la Comunidad «Calidad del aire y salud» | Incluye «Niños de 0-6 años» y «Personas mayores de 65 años» |
| Tiempo (previsión, histórico y previsiones tal como se emitieron) | Open-Meteo (CC BY 4.0) | Temperatura, lluvia, viento, humedad, radiación |
| Nombres de lugares | 21 distritos de Madrid + 160 municipios (`gazetteer.csv`) | «Cerca de …» para cualquier punto del mapa |

### Volumen revisado en la auditoría (19 de noviembre de 2026)

| Fuente | Periodo | Tamaño |
|---|---|---|
| Comunidad, aire horario | 2019 – 2026 | 5,9 millones de horas, 28 estaciones |
| Ayuntamiento, aire horario | 2019 – 2026 | 4,0 millones de horas, 24 estaciones |
| Red Palinocam, polen | 1993 – 2026 | 3,3 millones de filas, 11 captadores, 27 tipos |
| Open-Meteo, tiempo | 1993 – 2026 (diario), 2019 – 2026 (horario) | 86.555 días, 549.504 horas, sin huecos |

---

## 6. Cómo se decide el nivel

- **Aire:** para cada contaminante, la estación más cercana con un dato válido; se aplica la banda
  oficial del ICA. Perfil general: Precaución desde «Desfavorable», quedarse en casa desde «Muy
  desfavorable». Grupos sensibles: Precaución desde «Regular», quedarse en casa desde «Desfavorable».
- **Polen:** el captador más cercano con lectura de menos de 10 días. Solo cuentan los tipos
  marcados: «medio» = Precaución; «alto» o «muy alto» = mejor quedarse en casa.
- **Resultado final:** lo peor de aire y polen; lo peor da la causa del titular.
- **Lo que no inventamos:** si no hay texto oficial para un caso (polen «bajo», aire y polen
  combinados, consejos por perfil para el polen), no se muestra ninguno.
- **Decisiones propias, siempre marcadas** como SIMPLIFICACIÓN o DECISIÓN DE LA APP en `config.py`,
  donde también está la fuente de cada umbral y cada texto.

### Protección frente a datos malos

- **Lecturas viejas:** una lectura de más de 3 horas no se usa para el resultado; se usa la
  siguiente estación y se avisa (en el mapa, la estación se ve atenuada).
- **«Dato dudoso»:** un valor «Desfavorable» o peor mientras el resto de la red está en «Buena» o
  «Razonablemente buena» y es más de 4 veces la mediana. Caso real: **Orusco del Tajuña, 17 de
  noviembre de 2026, PM10 de hasta 437 µg/m³**. La app lo apartó; días después, la propia Comunidad
  marcó esas horas como no válidas en su archivo validado.
- **Nunca un dato del futuro:** los archivos traen horas que aún no han pasado (el 15/11 a las 18:05
  ya aparecía un dato de las 00:00 del día siguiente). Se descartan siempre; hay una prueba
  automática para ello.
- **Copias de seguridad:** si una fuente no responde, se usa la última copia buena y la app lo dice.
  Un fallo del tiempo nunca deja nada en blanco: el nivel sale del aire y del polen.

---

## 7. Nuestros modelos de previsión

### Principios comunes

- **Entrenar hasta 2023, elegir con 2024, probar con 2025.** 2025 no se usa para decidir nada: sus
  números son una medida honesta de cómo funciona con datos nuevos. No se cambió ninguna elección
  después de ver la prueba, aunque a veces otro modelo salió mejor.
- **Meteorología honesta:** en las pruebas, los modelos reciben la **previsión del tiempo tal como se
  emitió entonces** (archivo de ejecuciones anteriores de Open-Meteo), no el tiempo que hizo
  después. Así no parecen mejores de lo que son.
- **Solo se muestra lo que demostró ser útil:** cada previsión tiene una regla para decidir hasta
  dónde se enseña; donde no la pasa, la app dice «Sin previsión fiable».

### Polen

- **El reto:** ciclo anual muy marcado, entre el 46 % y el 78 % de los días a cero, picos que se
  multiplican por diez de un día a otro, y la última lectura tiene al menos 3 días: «mañana» está a
  4 días de lo último medido y el día +5, a 8.
- **Cuatro modelos comparados** sobre exactamente las mismas **350.520 filas** (estación, tipo,
  día, antelación) de 2024 y 2025, en temporada:

| Modelo | Acierto del nivel oficial, 2025 | Error medio (escala log), 2025 |
|---|---|---|
| Estacional ingenuo (referencia) | 47 % | 1,69 |
| Suavizado exponencial | 52 % | 1,18 |
| ARIMAX (ciclo anual + tiempo + errores ARMA) | 59 % | 0,93 |
| Gradient boosting por cuantiles | 61 % | 0,86 |
| **Elegido por tipo con 2024 (lo que usa la app)** | **60 %** | |

- **Modelo por tipo:** ARIMAX para encinas y robles, olivo y llantenes; gradient boosting para los
  otros siete.
- **Variables del gradient boosting:** día del año, calor acumulado desde el 1 de enero, lluvia de
  los 7/14/30/90 días anteriores, la previsión del tiempo, las lecturas disponibles (con al menos 3
  días) y la misma semana del año anterior. Las que más pesan: época del año y calor acumulado.
- **Fiabilidad:** una previsión (tipo + día de antelación) se muestra solo si en 2024 acertó el
  nivel al menos 5 puntos más que la referencia, con menos error y con al menos 200 días de
  temporada. **La pasan 36 de las 50 combinaciones.** Los llantenes no la pasan nunca.
- **Intervalos del 80 %**, calibrados con 2024: la cobertura real en 2025 pasó del 66 % al 77 %.
- **Días sin lectura (nowcast):** entre la última lectura publicada y hoy, la app muestra la
  estimación del modelo para cada día, marcada como estimación.
- **Una sola fuente de verdad:** el nivel en el planificador, el gráfico y el asistente es siempre la
  mediana de la previsión leída con los umbrales oficiales.
- **Ejemplo honesto (modo demostración):** con lo que se sabía el 24 de mayo de 2025, el modelo
  preveía gramíneas en nivel «alto» en Madrid-Arganzuela del 24 al 28; el 28 fue en realidad
  «muy alto». Avisó del peligro, pero quedó corto en el pico.

### Calidad del aire

- **Qué:** NO2, O3, PM10 y PM2.5, **hora a hora**, para **52 estaciones** de las dos redes (NO2 en
  48, O3 en 38, PM10 en 32, PM2.5 en 21), desde la última lectura de cada estación. Histórico horario
  oficial desde 2019 + previsión del tiempo de Open-Meteo (8 zonas).
- **Modelos comparados:** «la misma hora de ayer» (referencia), «lo habitual a esa hora», SARIMAX y
  gradient boosting. Con 2024 ganó el **gradient boosting** en los cuatro contaminantes.
- **Frente a «la misma hora de ayer», en 2025 (error medio en µg/m³):**

| Contaminante | Día +1 | Día +5 |
|---|---|---|
| NO2 | 9,0 frente a 10,3 | 10,4 frente a 13,0 |
| O3 | 14,8 frente a 16,5 | 17,3 frente a 20,2 |
| PM10 | 6,6 frente a 7,9 | 8,2 frente a 10,6 |
| PM2.5 | 4,3 frente a 5,0 | 5,1 frente a 6,1 |

- **En todas las horas de 1 a 120**, en los cuatro contaminantes y en 2024 y 2025, el modelo tiene
  menos error que «la misma hora de ayer» (la mejora más pequeña, 6 %: O3 a las 22 horas, en 2025).
- **Hasta dónde se muestra: 96 horas.** Regla: cada día, al menos un 10 % menos de error que «la
  misma hora de ayer» y un 5 % menos que «lo habitual», en los cuatro contaminantes. El O3 falla el
  día 5, así que se corta en 4 días.

---

## 8. Cómo está construida

```
Portales oficiales (Comunidad, Ayuntamiento)  +  Open-Meteo (tiempo)
              │                                        │
              ▼                                        ▼
   GitHub Actions: modelos de aire (cada hora) y de polen (cada día)
              │   guarda los resultados en la rama "results" (~125 KB)
              ▼
   Servidor FastAPI en Render (api.py): mediciones en directo + previsiones ya calculadas
              │   misma lógica para todo: data.py → risk.py / planner.py / chart.py
              ▼
   Página web (HTML, CSS, JavaScript, mapa Leaflet)   ·   Asistente (Claude Haiku)
```

- **Python** en todo el proyecto: pandas, scikit-learn (gradient boosting), statsmodels
  (ARIMAX/SARIMAX), FastAPI, Leaflet en la página, sin librerías de gráficos (el gráfico es SVG
  dibujado a mano).
- **Por qué los modelos corren en GitHub Actions:** con los modelos cargados, el servidor gratuito
  de Render (512 MB) llegaba al límite y se reiniciaba. Tras optimizar (de 507 MB a 286 MB de pico
  con un visitante) seguía sin bastar con varios visitantes, así que el 19 de noviembre el cálculo se
  llevó a GitHub Actions y el servidor solo lee los resultados.
- **Puntualidad:** la programación de GitHub resultó poco fiable (unas 6 ejecuciones al día en lugar
  de 24). Desde el 26 de noviembre un servicio externo (cron-job.org) lanza el cálculo cada hora; si
  un cálculo llega tarde, la app usa la última previsión buena en lugar de quedarse en blanco.
- **Servidor:** Render, plan de pago de 1 CPU y 2 GB desde el 26 de noviembre (con 0,1 CPU la
  página tardaba unos 30 s en llenarse; ahora unos 6 s).
- **Versión alternativa** completa en Streamlit (`app.py`) con la misma lógica.

---

## 9. El asistente con IA

- Modelo **Claude Haiku** con cinco herramientas que llaman a **nuestras propias funciones**
  (buscar lugar, resultado de ahora o de cualquier hora, mejores horas, planificador, previsión de
  polen). Dice exactamente lo mismo que la app.
- Reglas: responder solo con lo que devuelven las herramientas; citar estación, fuente y hora del
  dato; avisar de lo provisional y de lo previsto; sin diagnósticos ni consejos de medicación; el
  112 para emergencias; rechazar preguntas fuera de tema; sin consejos propios.
- Entiende el perfil por la conversación («soy asmática», «tengo 80 años»).
- Límites: 20 mensajes por persona y hora, respuestas cortas, etiqueta «Generado por IA con datos
  oficiales».
- **Coste:** unos 0,0016 $ por pregunta en las pruebas (14 preguntas de prueba, todas con las
  herramientas correctas). El 26 de noviembre, en directo: respuesta en 4–7 s.

---

## 10. Auditoría de los datos (lo más interesante)

Revisamos los archivos **tal como se publican**, sin limpiar (`DATA_AUDIT.md`, `data_audit.py` y el
cuaderno `notebooks/exploracion_datos.ipynb`):

1. **Los archivos horarios de la Comunidad parecen estar en hora UTC y los del Ayuntamiento en hora
   de Madrid.** Tres pruebas independientes apuntan a lo mismo: el pico de NO2 de la mañana, el pico
   de ozono de la tarde y 12 parejas de estaciones vecinas de las dos redes, que coinciden mejor
   desplazando la Comunidad 1 hora en invierno y 2 en verano. Pendiente de confirmar a mano con la
   web oficial antes de corregirlo (ver límites).
2. **El polen sin dato nunca se trata como cero.** El problema real es el contrario: 49 días en que
   un captador publica todos los tipos a 0 mientras la red ve más de 100 granos/m³.
3. **El «dato dudoso» de Orusco del Tajuña era un error de la fuente**, corregido después por la
   propia Comunidad: la regla de la app acertó.
4. **Los extremos son reales:** el polvo sahariano del 15 de marzo de 2022 (PM10 de hasta
   1.109 µg/m³) y el plátano de paseo en Alcalá (hasta 22.056 granos/m³).
5. Problemas de formato resueltos en la carga: codificaciones mezcladas, nombres de polen rotos en
   2021–2022, formato de fecha distinto en 2026, duplicados, decimales con coma.

---

## 11. Límites (dichos con claridad)

- **Polen con retraso:** la última lectura tiene de 3 a 7 días. Todo lo de después es estimación o
  previsión, y la app lo marca.
- **Un año para elegir y otro para probar.** Algunos intervalos siguen siendo estrechos (olivo,
  chopos, ortigas) y los picos se suavizan (el 28 de mayo de 2025 se previó «alto» y fue «muy alto»).
- **Olivo:** con el modelo elegido en 2024 (ARIMAX) acertó el 53 % en 2025, frente al 65 % del
  gradient boosting. Se informa, no se cambia: cambiarlo sería elegir con el año de prueba.
- **Ozono a partir del día 3:** en 2025 queda un poco peor que «lo habitual a esa hora».
- **Medias del ICA simplificadas:** los archivos en tiempo real solo tienen el día en curso, así que
  las medias oficiales (8 y 24 horas) se calculan con las horas desde medianoche.
- **Hora de la Comunidad (auditoría, punto 1):** las reglas de limpieza propuestas en la auditoría,
  incluida la conversión de UTC a hora de Madrid, **todavía no se han aplicado**.
- **Demo con solo la red de la Comunidad:** el día de demostración usa sus 28 estaciones. La
  auditoría descubrió que el Ayuntamiento sí publicó ya 2025; se podría añadir.

---

## 12. Próximos pasos

1. Confirmar con la web oficial el desfase horario de la Comunidad y aplicar las reglas de limpieza
   de la auditoría; volver a entrenar y comparar antes y después.
2. Añadir las 24 estaciones del Ayuntamiento al día de demostración.
3. Avisos: notificar cuando el nivel de un lugar guardado empeore.
4. Más tipos de polen cuando haya umbrales oficiales para ellos.
5. Medir con usuarios reales (asociaciones de pacientes con asma o alergia).

---

## 13. Cifras clave (para las diapositivas)

| Cifra | Qué es |
|---|---|
| **52** | estaciones de calidad del aire con previsión propia (Comunidad + Ayuntamiento) |
| **4** | contaminantes: NO2, O3, PM10, PM2.5 |
| **96 h** | horizonte de la previsión del aire |
| **120 de 120** | horas de antelación en las que el modelo de aire mejora «la misma hora de ayer» |
| **10** | tipos de polen con umbral oficial |
| **1993** | año desde el que hay histórico de polen |
| **350.520** | filas con las que se compararon los modelos de polen |
| **60 %** | acierto del nivel oficial de polen en 2025 (frente al 47 % de la referencia) |
| **36 de 50** | previsiones de polen que superan la prueba de fiabilidad |
| **77 %** | cobertura de los intervalos del 80 % tras calibrarlos (antes 66 %) |
| **392** | granos/m³ de gramíneas de media en la red el 28 de mayo de 2025 (día de demostración) |
| **0** | consejos inventados: todo texto de recomendación es oficial y literal |
| **~6 s** | en llenarse la página en directo |
| **~0,0016 $** | coste por pregunta al asistente |

---

## 14. Cronología

| Fecha (2026) | Hito |
|---|---|
| 15 nov | Carga de datos, lógica del riesgo y app en Streamlit; web en FastAPI y despliegue en Render; asistente con IA; primer modelo de polen; planificador de 5 días |
| 16 nov | Comparación de cuatro modelos de polen; gráfico de previsión; iconos en lugar de emojis; regla de datos y modelo propio de calidad del aire |
| 17 nov | Estabilidad («dato dudoso», lecturas viejas, caché del tiempo), comprobación de fuga de información, nowcast de polen, tipos de polen como filtros, solo recomendaciones oficiales |
| 18 nov | Optimización de memoria para el servidor gratuito |
| 19 nov | Previsiones calculadas en GitHub Actions; auditoría de datos |
| 25 nov | Copias de seguridad más robustas; el mapa oculta estaciones sin lecturas recientes |
| 26 nov | Planificador robusto ante cálculos tardíos, lanzador externo cada hora, servidor de pago, README |

41 cambios guardados en el repositorio entre el 15 y el 26 de noviembre.

---

## 15. Estructura sugerida para la presentación

1. **Portada:** Respira MAD, la frase del punto 1.
2. **El problema:** quién lo sufre y por qué la información actual no basta (punto 2).
3. **La solución en una pantalla:** captura del resultado de ahora en el móvil (punto 3).
4. **Demo en directo** (punto 4).
5. **Los datos y la regla de datos** (punto 5): solo fuentes oficiales.
6. **Cómo predecimos:** polen y aire, con la tabla de modelos y el «120 de 120» (punto 7).
7. **Honestidad:** entrenar / elegir / probar, meteorología tal como se emitió, «Sin previsión
   fiable», textos oficiales literales (puntos 6 y 7).
8. **Lo que encontramos en los datos** (punto 10).
9. **Arquitectura** (punto 8, el esquema).
10. **Límites y próximos pasos** (puntos 11 y 12).
11. **Cierre:** las cifras clave (punto 13) y el enlace a la app.

Imágenes ya disponibles en el repositorio: `air_model/plots/air_error_by_lead.png`,
`pollen_model/plots/comparison_by_lead.png`, `pollen_model/plots/feature_importance.png`,
`pollen_model/plots/forecast_vs_real_gramineas_ALER.png` y los gráficos STL y ACF/PACF de
`pollen_model/plots/`.
