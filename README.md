# Respira MAD

**¿Es seguro salir ahora?** Respira MAD responde a esa pregunta para las
personas más sensibles a la contaminación y al polen (asma, alergia al polen,
personas mayores, niños pequeños) en la Comunidad de Madrid, combinando la
calidad del aire en directo, los niveles de polen y el tiempo.

**App:** https://respira-mad.onrender.com · Proyecto para el hackathon **DataMad 2026**.

## Qué hace

- **Resultado para ahora** en tres niveles: «Puedes salir», «Precaución» o
  «Mejor quédate en casa», según tu perfil (general, asma, persona mayor,
  niño/a) y los tipos de polen que te afectan. El titular dice la causa
  (por ejemplo, «Precaución por polen de gramíneas»).
- **Solo textos oficiales:** cada recomendación es un texto oficial citado
  literalmente, con su fuente. No escribimos consejos propios.
- **Próximas 24 horas y mejores franjas** para salir, con temperatura y lluvia.
- **Planificador de 5 días** (mañana, tarde, noche y madrugada) y un
  **gráfico de evolución y previsión** de polen y aire.
- **Mapa** con las estaciones oficiales de aire y de polen.
- **Asistente con IA** («Pregúntale a Respira») que responde solo con los
  datos de la app y los textos oficiales.
- **Modo demostración:** el 28 de mayo de 2025, el día con más polen de
  gramíneas del histórico (392 granos/m³ de media en la red).

## Datos

Regla del proyecto: **todos los datos de aire y de polen, medidos y previstos,
salen solo de la Comunidad de Madrid y del Ayuntamiento de Madrid.**

| Qué | Fuente |
|---|---|
| Calidad del aire (NO2, O3, PM10, PM2.5), cada hora | Red de la Comunidad de Madrid (28 estaciones) y del Ayuntamiento de Madrid, portales de datos abiertos |
| Polen (10 tipos con umbrales oficiales) | Red Palinocam, Comunidad de Madrid. Se publica con 3 a 7 días de retraso; la app siempre muestra la fecha de la lectura |
| Niveles y recomendaciones | Índice Nacional de Calidad del Aire (BOE-A-2020-10426); niveles de polen del informe Ayuntamiento/UCM; textos de la Comunidad de Madrid |
| Tiempo (solo temperatura, lluvia, viento, humedad) | [Open-Meteo](https://open-meteo.com) (CC BY 4.0) |

Las mediciones son oficiales; las previsiones son de nuestros modelos y la app
siempre las marca como «PREVISIÓN».

## Nuestros modelos

- **Aire:** gradient boosting por contaminante, para las 52 estaciones de las
  dos redes, hora a hora. Se muestra hasta **96 horas**, el horizonte en el
  que mejoró claramente a la referencia en las pruebas.
- **Polen:** para cada tipo, el mejor entre ARIMAX y gradient boosting
  (elegido con 2024, probado con 2025 sin tocarlo): acierta el nivel oficial
  el **60 %** de los días de temporada en 2025. Solo se muestra donde superó
  la prueba de fiabilidad: **36 de las 50** combinaciones de tipo y día.

Todos los detalles, comparaciones y límites están en
[MODEL_REPORT.md](MODEL_REPORT.md). La auditoría de los datos, en
[DATA_AUDIT.md](DATA_AUDIT.md).

## Cómo funciona

- **Web:** `api.py` (FastAPI) + `static/` (HTML, CSS y JavaScript, mapa con
  Leaflet), desplegada en Render (`render.yaml`).
- **Previsiones:** GitHub Actions calcula cada hora la del aire y cada día la
  del polen (`precompute.py`) y las guarda en la rama `results`; el servidor
  solo las lee.
- **Copia de seguridad:** si una fuente no responde, se usa la última copia
  buena de `backup/`, y la app lo indica.
- **Versión alternativa:** `app.py` (Streamlit).

## Ejecutarla en local

Con Python 3.13:

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/uvicorn api:app --port 8000
```

Después abre http://localhost:8000. El asistente necesita la variable de
entorno `ANTHROPIC_API_KEY`; sin ella, el resto de la app funciona igual.

Comprobaciones: `test_data.py` (fuentes en directo), `test_risk.py`
(resultado para 4 lugares) y `test_timestamps.py` (ninguna lectura con hora
futura), con `.venv/bin/python`.
