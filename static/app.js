/* Respira MAD - the page's behaviour.
   It only draws what the server sends: every number, level and text comes
   from /api/... (see api.py). Plain JavaScript, no build step. */

"use strict";

const REFRESH_MS = 5 * 60 * 1000;                 // reload the data every 5 minutes
// Rough box around the Comunidad de Madrid, to warn if "my location" is outside it.
const MADRID_BOX = { south: 39.88, north: 41.17, west: -4.58, east: -3.05 };
const CUSTOM_PLACE = "__custom__";

// One icon from the sprite at the top of index.html (Lucide icons + our own "pollen").
const icon = (name, extra = "") => `<svg class="icon ${extra}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
// The icon of each level: good, be careful, stay in.
const ICONS = [icon("check"), icon("triangle-alert"), icon("house")];

const $ = (id) => document.getElementById(id);
const isNarrow = () => window.innerWidth < 520;     // phones get shorter labels in the header
const isPhoneLayout = () => window.innerWidth < 900;   // map on top and panel below (same limit as style.css)

// What the user has chosen. Everything on screen is drawn from this + the server's answers.
const state = {
  lat: null, lon: null, placeName: "",
  profile: "general",
  allergic: false,
  types: new Set(["Gramíneas"]),
  demo: false,
  hour: 10,                 // only used in demo mode
  selectedHour: null,       // the hour tapped in the strip
};

let config = null;
let places = [];
let map, pin, stationLayer, usedLayer;
let stationMarkers = [];     // every station on the map: {kind, name, source, lat, lon, marker}
let lastRisk = null;         // the latest result, to know which stations it used
let fitNext = true;          // zoom the map to the chosen point and its stations on the next result
let lastHours = null;
let requestNumber = 0;      // to ignore answers that arrive after a newer request
let updatedMinutes = null;  // for the "actualizado hace X min" pill
let updatedAt = 0;

/* ------------------------------------------------------------------ helpers */

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const num = (value, digits = 0) => (value == null ? "–" : Number(value).toLocaleString("es-ES", { maximumFractionDigits: digits, minimumFractionDigits: digits }));
const hhmm = (iso) => iso.slice(11, 16);
const ddmm = (iso) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;

async function getJSON(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== "") query.set(key, value);
  const response = await fetch(`${path}?${query}`);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

let toastTimer;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 4500);
}

// The parameters every request shares.
function commonParams() {
  return {
    lat: state.lat.toFixed(5), lon: state.lon.toFixed(5),
    profile: state.profile,
    allergy_types: state.allergic ? [...state.types].join("|") : "",
    demo: state.demo,
    hour: state.demo ? state.hour : null,
  };
}

// Outline of a station dot. The light-blue "Buena" gets a dark teal ring so it shows on the light map.
const outline = (category) => (category === 0 ? "#0a6f78" : "#26332d");

/* ------------------------------------------------------------------ set-up */

async function start() {
  try {
    [config, places] = await Promise.all([getJSON("/api/config"), getJSON("/api/places")]);
  } catch (error) {
    $("status-text").textContent = "Sin conexión con el servidor";
    toast("No se pudo cargar la aplicación. Recarga la página en unos segundos.");
    return;
  }
  state.hour = config.demo.default_hour;
  state.lat = places[0].lat; state.lon = places[0].lon; state.placeName = places[0].name;

  buildControls();
  buildMap();
  await refresh({ stations: true });

  setInterval(() => { if (!state.demo) refresh({ stations: true, quiet: true }); }, REFRESH_MS);
  setInterval(drawStatus, 30 * 1000);              // keep "hace X min" ticking
}

function buildControls() {
  // places dropdown
  const select = $("place");
  select.innerHTML = places.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("")
    + `<option value="${CUSTOM_PLACE}" hidden>Punto elegido en el mapa</option>`;
  select.addEventListener("change", () => {
    const place = places[Number(select.value)];
    if (place) setPoint(place.lat, place.lon, place.name, { fromSelect: true });
  });

  // profile chips (pick one)
  $("profiles").innerHTML = config.profiles.map((p) =>
    `<button class="chip" type="button" role="radio" data-key="${esc(p.key)}" title="${esc(p.label)}" aria-checked="${p.key === state.profile}">${esc(p.short)}</button>`).join("");
  $("profiles").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    state.profile = chip.dataset.key;
    for (const other of $("profiles").children) other.setAttribute("aria-checked", other === chip);
    refresh();
  });

  // pollen allergy switch + type chips (pick several)
  $("pollen-types").innerHTML = config.pollen_types.map((t) =>
    `<button class="chip" type="button" data-key="${esc(t.key)}" aria-pressed="${state.types.has(t.key)}">${esc(t.label)}</button>`).join("");
  $("allergy").addEventListener("change", (event) => {
    state.allergic = event.target.checked;
    $("pollen-types").hidden = !state.allergic;
    refresh();
  });
  $("pollen-types").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    const key = chip.dataset.key;
    if (state.types.has(key)) state.types.delete(key); else state.types.add(key);
    chip.setAttribute("aria-pressed", state.types.has(key));
    refresh();
  });

  // demo mode
  const slider = $("hour");
  slider.min = config.demo.first_hour; slider.max = config.demo.last_hour; slider.value = state.hour;
  $("demo").addEventListener("click", () => {
    state.demo = !state.demo;
    state.hour = config.demo.default_hour; slider.value = state.hour;
    state.selectedHour = null;
    $("demo").setAttribute("aria-pressed", state.demo);
    drawDemoLabel();
    $("demo-banner").hidden = !state.demo;
    $("demo-slider").hidden = !state.demo;
    document.body.classList.toggle("has-banner", state.demo);
    if (map) map.invalidateSize();
    refresh({ stations: true });
  });
  slider.addEventListener("input", () => { $("hour-value").textContent = `${slider.value}:00`; });
  slider.addEventListener("change", () => {
    state.hour = Number(slider.value); state.selectedHour = null;
    refresh({ stations: true, keepPlanner: true });      // the planner does not depend on the demo hour
  });

  drawDemoLabel();
  window.addEventListener("resize", () => { drawDemoLabel(); drawStatus(); drawChart(); });

  // my location
  $("locate").addEventListener("click", useMyLocation);

  // legend of the hourly strip: colour + label (never colour alone)
  $("strip-legend").innerHTML = config.levels.map((l) => `<span><i style="background:${l.color}"></i>${esc(l.label)}</span>`).join("")
    + `<span>${icon("star", "icon-fill")}Mejor franja</span>`;

  buildSheet();
  buildChart();
  info.chart = chartInfo();
  buildChat();
}

function buildMap() {
  map = L.map("map", { zoomControl: false, attributionControl: true }).setView([state.lat, state.lon], 10);
  L.control.zoom({ position: "topright" }).addTo(map);
  // A muted light basemap, so the official ICA colours stand out.
  const osm = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';
  if (config.carto_basemap_key) {
    // CARTO Positron (needs a free key, set as CARTO_BASEMAP_KEY on the server)
    L.tileLayer(`https://basemaps.cartocdn.com/rastertiles/light_all/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(config.carto_basemap_key)}`, {
      maxZoom: 19, attribution: `${osm}, © <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a>`,
    }).addTo(map);
    $("map-credit").innerHTML = ', © <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a>';
  } else {
    // no key: OpenStreetMap tiles, turned light grey by style.css (.muted-tiles)
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: osm, className: "muted-tiles" }).addTo(map);
  }
  usedLayer = L.layerGroup().addTo(map);            // thin lines from the stations in use to the chosen point
  stationLayer = L.layerGroup().addTo(map);

  const pinIcon = L.divIcon({
    className: "",
    html: '<svg class="pin" viewBox="0 0 34 42"><path d="M17 41C17 41 31 27.500 31 16A14 14 0 0 0 3 16C3 27.500 17 41 17 41z" fill="#0f6b4f" stroke="#fff" stroke-width="2.500"/><circle cx="17" cy="16" r="5.500" fill="#fff"/></svg>',
    iconSize: [34, 42], iconAnchor: [17, 41],
  });
  pin = L.marker([state.lat, state.lon], { icon: pinIcon, zIndexOffset: 1000, keyboard: false, interactive: false }).addTo(map);

  // clicking the map moves the pin and updates everything
  map.on("click", (event) => setPoint(event.latlng.lat, event.latlng.lng, "Punto elegido en el mapa"));

  // Legend: open on laptops. On phones it starts closed, as a small "Leyenda" button at the
  // top-left of the map (the bottom of the map is covered by the panel).
  const legend = L.control({ position: isPhoneLayout() ? "topleft" : "bottomleft" });
  legend.onAdd = () => {
    const box = L.DomUtil.create("details", "legend");
    box.open = !isPhoneLayout();
    box.innerHTML = `<summary>${icon("list")}Leyenda${icon("chevron-down", "legend-arrow")}</summary><ul>`
      + config.ica_categories.map((c, i) => `<li><span class="swatch" style="background:${c.color};border-color:${outline(i)}"></span>${esc(c.name)}</li>`).join("")
      + `<li><span class="pollen-marker is-small">${icon("pollen")}</span>Estación de polen</li>`
      + '<li><span class="legend-used"></span>Estación usada en tu resultado</li>'
      + "</ul>";
    L.DomEvent.disableClickPropagation(box);
    return box;
  };
  legend.addTo(map);
}

function drawDemoLabel() {
  $("demo-label").textContent = state.demo ? "Volver a hoy" : (isNarrow() ? "Día de mucho polen" : "Ver día de mucho polen");
}

/* ------------------------------------------------------------------ actions */

function setPoint(lat, lon, name, options = {}) {
  state.lat = lat; state.lon = lon; state.placeName = name; state.selectedHour = null;
  pin.setLatLng([lat, lon]);
  if (!options.fromSelect) $("place").value = CUSTOM_PLACE;
  // a place from the list or "my location": the map zooms to the point and the stations it uses
  // (see highlightStations). A tap on the map keeps the zoom the person chose.
  if (options.fromSelect || options.recenter) { fitNext = true; map.setView([lat, lon], map.getZoom()); }
  else map.panInside([lat, lon], { padding: [60, 60] });
  refresh();
}

function useMyLocation() {
  if (!navigator.geolocation) { toast("Tu navegador no permite obtener la ubicación."); return; }
  const button = $("locate");
  button.classList.add("is-busy");
  navigator.geolocation.getCurrentPosition(
    (position) => {
      button.classList.remove("is-busy");
      const { latitude: lat, longitude: lon } = position.coords;
      const inside = lat >= MADRID_BOX.south && lat <= MADRID_BOX.north && lon >= MADRID_BOX.west && lon <= MADRID_BOX.east;
      if (!inside) { toast("Parece que estás fuera de la Comunidad de Madrid. Elige un municipio o toca el mapa."); return; }
      setPoint(lat, lon, "Tu ubicación", { recenter: true });
    },
    () => { button.classList.remove("is-busy"); toast("No se pudo obtener tu ubicación. Revisa los permisos del navegador."); },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 5 * 60 * 1000 },
  );
}

/* Ask the server for everything and redraw. Nothing reloads the page. */
async function refresh(options = {}) {
  const mine = ++requestNumber;
  if (!options.quiet) setLoading(true);
  const params = commonParams();
  const stationParams = { demo: state.demo, hour: state.demo ? state.hour : null };

  const jobs = [
    getJSON("/api/risk", params),
    getJSON("/api/hours", params),
    getJSON("/api/freshness", { ...stationParams, lat: params.lat, lon: params.lon }),
    options.stations ? getJSON("/api/stations", stationParams) : Promise.resolve(null),
  ];
  // the planner loads on its own, so a slow forecast never holds the main card back
  if (!options.keepPlanner) { loadPlanner(params, mine); loadChart(params, mine); }
  const [risk, hours, freshness, stations] = await Promise.allSettled(jobs);
  if (mine !== requestNumber) return;              // a newer request is on its way: ignore this answer

  if (risk.status === "fulfilled") drawRisk(risk.value); else drawRiskError();
  if (hours.status === "fulfilled") drawHours(hours.value);
  if (freshness.status === "fulfilled") drawFreshness(freshness.value);
  if (stations.status === "fulfilled" && stations.value) drawStations(stations.value);
  if (risk.status === "rejected") toast("No se pudieron actualizar los datos. Se volverá a intentar.");
  setLoading(false);
}

function setLoading(on) {
  $("result").classList.toggle("is-loading", on);
  $("tiles").classList.toggle("is-loading", on);
  $("strip").classList.toggle("is-loading", on);
  if (on && !$("tiles").children.length) $("tiles").innerHTML = '<div class="skeleton"></div>'.repeat(4);
}

/* ------------------------------------------------------------------ drawing */

function drawRisk(risk) {
  lastRisk = risk;
  highlightStations();
  const level = risk.level_index == null ? null : config.levels[risk.level_index];
  const card = $("result");
  // changing these two variables is what makes the colour fade smoothly (see style.css)
  card.style.setProperty("--level-bg", level ? level.color : "#dfe5e2");
  card.style.setProperty("--level-ink", level ? level.text_color : "#14201b");
  $("result-icon").innerHTML = level ? ICONS[risk.level_index] : "";
  $("result-label").textContent = risk.level_label;
  // the same name for the point everywhere: card, dropdown and (later) the chat
  state.placeName = risk.place_label;
  const custom = $("place").querySelector(`option[value="${CUSTOM_PLACE}"]`);
  if ($("place").value === CUSTOM_PLACE) custom.textContent = risk.place_label;
  // the note about the profile (older people, children) sits behind a small info icon
  info.profile = risk.profile_note ? { title: `Perfil: ${risk.profile_label}`, html: `<p>${esc(risk.profile_note)}</p>` } : null;
  $("result-sub").innerHTML = `${esc(risk.profile_label)}${risk.profile_note ? infoIcon("profile", "nota sobre el perfil") : ""} · ${esc(risk.place_label)}`;
  $("result-reason").textContent = risk.reason;

  // official recommendation, word for word, inside the card, with its source in small print
  // The first text (the one behind the level) is always visible; a second one folds away to save space.
  // The source of each text sits behind "Texto oficial (i)".
  risk.recommendations.forEach((r, i) => {
    info[`official-${i}`] = { title: "Texto oficial", html: `<p>Este texto es una cita literal de la fuente oficial, sin cambios.</p><p><b>Fuente:</b> ${esc(r.source)}.</p>` };
  });
  const quote = (r) => `<blockquote class="quote"><p>${esc(r.text)}</p>`
    + `<button class="info-link" type="button" data-info="official-${risk.recommendations.indexOf(r)}">Texto oficial <span class="info" aria-hidden="true">${icon("info")}</span></button></blockquote>`;
  const [first, ...others] = risk.recommendations;
  $("advice").innerHTML = (first ? quote(first) : "")
    + (others.length ? `<details class="more-advice"><summary>Ver también la recomendación de calidad del aire</summary>${others.map(quote).join("")}</details>` : "");
  $("warnings").innerHTML = risk.notes.map((n) => `<div class="warning">${esc(n)}</div>`).join("");

  // pollutant tiles (official station measurements)
  $("tiles").innerHTML = risk.air.map((a) => `
    <div class="tile">
      <div class="tile-name"><span>${esc(a.pollutant)}</span>${a.provisional ? `<button class="tag tag-warn tag-button" type="button" data-info="provisional">Provisional <span class="info" aria-hidden="true">${icon("info")}</span></button>` : ""}</div>
      <div class="tile-value">${num(a.ica_value)} <small>µg/m³</small></div>
      <div class="tile-cat"><span class="swatch" style="background:${a.color};border-color:${outline(a.category)}"></span>${esc(a.category_name)}</div>
      <div class="tile-meta">${esc(a.station)} (${esc(a.source)}) · a ${num(a.distance_km, 1)} km · ${ddmm(a.timestamp)} ${hhmm(a.timestamp)}</div>
    </div>`).join("") || '<p class="hint">Sin mediciones de calidad del aire en este momento.</p>';

  // pollen: always shown to everyone, as information (the latest official reading).
  // Only the allergy switch makes pollen count for the person's result.
  const pollen = risk.pollen;
  const box = $("pollen-tile");
  box.hidden = false;
  if (pollen) {
    const age = pollen.age_days === 0 ? "de hoy" : `de hace ${pollen.age_days} días`;
    const head = `<b>Polen</b> · ${esc(pollen.station)} (${num(pollen.distance_km, 1)} km) · lectura del ${ddmm(pollen.reading_date)}, <b>${age}</b>`;
    // the main types: the ones that count for you, any above "bajo", and at least the top 3 (the list comes worst first)
    const shown = pollen.types.filter((t, i) => t.counts_for_you || t.level > 0 || i < 3).slice(0, 6);
    const foot = !state.allergic
      ? "Solo informativo: no cambia tu resultado. Para que cuente, activa «Tengo alergia al polen»."
      : state.types.size
        ? "Solo cuentan para tu resultado los tipos que has elegido («cuenta para ti»)."
        : "Elige arriba tus tipos de polen para que cuenten en tu resultado.";
    box.innerHTML = `<div class="pollen-head">${head}</div>` + shown.map((t) => `
      <div class="pollen-row ${t.counts_for_you ? "counts" : ""}">
        <span>${esc(t.name)}${t.counts_for_you ? '<span class="pollen-you">cuenta para ti</span>' : ""}</span>
        <span class="pollen-value">${num(t.value)} granos/m³ <span class="lvl lvl-${t.level}">${esc(t.level_name)}</span></span>
      </div>`).join("") + `<p class="pollen-foot">${foot}</p>`;
  } else {
    box.innerHTML = '<div class="pollen-head"><b>Polen</b> · sin lectura reciente de polen en ninguna estación (últimos 10 días)</div>';
  }
}

function drawRiskError() {
  $("result-label").textContent = "Sin datos";
  $("result-sub").textContent = state.placeName;
  $("result-reason").textContent = "No hay mediciones disponibles en este momento. Inténtalo de nuevo en unos minutos.";
}

function drawHours(data) {
  lastHours = data;
  $("hour-value").textContent = `${state.hour}:00`;

  const strip = $("strip");
  if (!data.hours.length) {
    strip.innerHTML = "";
    $("hour-detail").textContent = "No hay previsión disponible para este punto.";
    $("windows").innerHTML = "";
    return;
  }
  // The strip shows the next 24 hours, starting now. Each hour is identified by its full time
  // ("2026-11-17T01:00"), because the same hour number can belong to today or to tomorrow.
  const stillThere = data.hours.some((h) => h.time === state.selectedHour);
  if (!stillThere) state.selectedHour = data.hours[0].time;
  const firstTomorrow = data.hours.find((h) => h.day === "mañana");

  strip.innerHTML = data.hours.map((h) => `
    <button class="hour ${h.best ? "is-best" : ""} ${h.time === state.selectedHour ? "is-selected" : ""} ${h === firstTomorrow ? "is-newday" : ""}"
            type="button" data-time="${h.time}" style="--c:${config.levels[h.level_index].color}"
            aria-label="${h.day} ${h.hour}:00, ${esc(h.level_label)}${h.best ? ", mejor franja" : ""}">
      ${h.best ? icon("star", "icon-fill hour-star") : ""}<span class="hour-bar"></span><span class="hour-num">${String(h.hour).padStart(2, "0")}</span>
    </button>`).join("");
  strip.onclick = (event) => {
    const button = event.target.closest(".hour");
    if (!button) return;
    state.selectedHour = button.dataset.time;
    for (const other of strip.children) other.classList.toggle("is-selected", other === button);
    drawHourDetail();
  };
  $("strip-scroll").scrollLeft = 0;                 // back to "now" whenever the data changes
  drawHourDetail();

  // best windows, as text
  let html = "";
  if (data.no_good_window) {
    html = `<p class="windows-title">No hay ninguna franja recomendable en las próximas 24 horas: todas las horas previstas están en «${esc(config.levels[2].label)}».</p>`;
  } else if (data.windows.length) {
    html = `<p class="windows-title">${icon("star", "icon-fill")} Mejores franjas de las próximas 24 horas (nivel «${esc(config.levels[data.windows[0].level_index].label)}»)</p><ul class="windows">`
      + data.windows.map((w) => `<li><b>${esc(w.day)} ${hhmm(w.start)}–${hhmm(w.end)}</b><span>${num(w.temperature_c)} °C · ${w.rain_mm === 0 ? "sin lluvia" : `lluvia ${num(w.rain_mm, 1)} mm`}</span></li>`).join("")
      + "</ul>";
  } else {
    html = '<p class="windows-title">No hay horas suficientes en la previsión para recomendar una franja.</p>';
  }
  $("windows").innerHTML = html;
}

function drawHourDetail() {
  const h = lastHours && lastHours.hours.find((x) => x.time === state.selectedHour);
  if (!h) { $("hour-detail").textContent = ""; return; }
  const parts = [
    ...["NO2", "O3", "PM10", "PM2.5"].filter((p) => h[p] != null).map((p) => `${p} ${num(h[p])}`), "µg/m³ (previsión)",
    `${num(h.temperature_c)} °C`, h.rain_mm > 0 ? `lluvia ${num(h.rain_mm, 1)} mm` : "sin lluvia",
  ];
  $("hour-detail").innerHTML = `<b>${esc(h.day)} ${String(h.hour).padStart(2, "0")}:00 · ${esc(h.level_label)}</b>`
    + (h.limited_by ? ` (por ${esc(h.limited_by)})` : "")
    + `<br>${esc(parts.join(" · "))}`;
}

// A pollen station: a small diamond with our pollen icon. Bigger when the result uses it, faded when it does not.
function pollenIcon(state) {
  const size = state === "used" ? 26 : 20;
  return L.divIcon({ className: "", html: `<div class="pollen-marker is-${state}">${icon("pollen")}</div>`, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
}

// Tapping a station: a bottom sheet on phones (easier to read and to close than a small popup),
// the usual popup next to the station on laptops.
function showStation(entry) {
  const used = entry.usedFor && entry.usedFor.length
    ? `<p class="station-used">${icon("check")} Se usa en tu resultado: ${esc(entry.usedFor.join(", "))}.</p>` : "";
  if (isPhoneLayout()) {
    openSheet(entry.title, entry.html + used);
  } else {
    L.popup({ offset: [0, -4] }).setLatLng([entry.lat, entry.lon]).setContent(`<b>${esc(entry.title)}</b><br>${entry.html}${used}`).openOn(map);
  }
}

function drawStations(data) {
  stationLayer.clearLayers();
  stationMarkers = [];
  for (const s of data.air) {
    // air stations: circles in the official ICA colour of their category
    const marker = L.circleMarker([s.lat, s.lon], { radius: 8, color: outline(s.category), weight: s.category === 0 ? 2.2 : 1.3, fillColor: s.color, fillOpacity: 0.95, bubblingMouseEvents: false });
    const entry = {
      kind: "air", name: s.station, source: s.source, lat: s.lat, lon: s.lon, marker, category: s.category,
      title: s.station,
      html: `<span class="popup-meta">${esc(s.source)} · ${ddmm(s.timestamp)} ${hhmm(s.timestamp)}</span><br>`
        + `<span class="popup-cat"><span class="swatch" style="background:${s.color};border-color:${outline(s.category)}"></span>ICA: ${esc(s.category_name)}</span><br>`
        + s.details.map(esc).join("<br>"),
    };
    if (!isPhoneLayout()) marker.bindTooltip(`${esc(s.station)}: ${esc(s.category_name)}`);    // hover labels only where there is a mouse
    marker.on("click", () => showStation(entry)).addTo(stationLayer);
    stationMarkers.push(entry);
  }
  for (const p of data.pollen) {
    const age = p.age_days === 0 ? "de hoy" : `de hace ${p.age_days} días`;
    const marker = L.marker([p.lat, p.lon], { icon: pollenIcon("normal"), bubblingMouseEvents: false });
    const entry = {
      kind: "pollen", name: p.station, lat: p.lat, lon: p.lon, marker,
      title: `Polen · ${p.station}`,
      html: `<span class="popup-meta">Lectura del ${ddmm(p.reading_date)}, ${age}</span><br>`
        + p.types.map((t) => `${esc(t.name)}: ${num(t.value)} granos/m³ (${esc(t.level_name)})`).join("<br>"),
    };
    if (!isPhoneLayout()) marker.bindTooltip(`Polen · ${esc(p.station)}`);
    marker.on("click", () => showStation(entry)).addTo(stationLayer);
    stationMarkers.push(entry);
  }
  highlightStations();
}

// Make the stations behind the current result stand out: bigger, with a thin line to the
// chosen point; the other stations are faded. Called whenever the result or the stations change.
function highlightStations() {
  usedLayer.clearLayers();
  if (!lastRisk || !stationMarkers.length) return;
  // which pollutants each air station supplies, and the pollen station (only if pollen counts for this person)
  const airUse = {};
  for (const a of lastRisk.air) (airUse[`${a.station}|${a.source}`] = airUse[`${a.station}|${a.source}`] || []).push(a.pollutant);
  const pollenUsed = state.allergic && lastRisk.pollen && lastRisk.pollen.kind === "measurement" ? lastRisk.pollen.station : null;
  for (const entry of stationMarkers) {
    entry.usedFor = entry.kind === "air" ? airUse[`${entry.name}|${entry.source}`] : (entry.name === pollenUsed ? ["polen"] : null);
    const used = Boolean(entry.usedFor);
    if (entry.kind === "air") {
      entry.marker.setStyle({ radius: used ? 11 : 7, weight: used ? 2.6 : 1, opacity: used ? 1 : 0.45, fillOpacity: used ? 1 : 0.35, color: used ? "#14201b" : outline(entry.category) });
      if (used) entry.marker.bringToFront();
    } else {
      entry.marker.setIcon(pollenIcon(used ? "used" : "faded"));
      entry.marker.setZIndexOffset(used ? 500 : 0);
    }
    if (used) L.polyline([[entry.lat, entry.lon], [state.lat, state.lon]], { color: "#14201b", weight: 1.5, opacity: 0.75, interactive: false }).addTo(usedLayer);
  }
  // show the chosen point together with the stations it uses (never closer than street level)
  const points = stationMarkers.filter((entry) => entry.usedFor).map((entry) => [entry.lat, entry.lon]);
  if (fitNext && points.length) {
    fitNext = false;
    map.fitBounds(L.latLngBounds([...points, [state.lat, state.lon]]).pad(0.6), { maxZoom: 12, animate: true });
  }
}

function drawFreshness(data) {
  updatedMinutes = data.updated_minutes; updatedAt = Date.now();
  $("status-pill").dataset.backup = data.all_live ? "" : "1";
  drawStatus();
  $("freshness").innerHTML = data.sources.map((s) => `
    <li><b>${esc(s.source)}</b>
      <span>${ddmm(s.timestamp)} ${hhmm(s.timestamp)} (${esc(s.ago)}) · ${esc(s.status)} · ${esc(s.origin)}</span></li>`).join("")
    || "<li>Sin datos de las fuentes en este momento.</li>";
}

// The pill in the header: "En directo · actualizado hace X min".
function drawStatus() {
  const pill = $("status-pill");
  const backup = pill.dataset.backup === "1";
  pill.classList.toggle("is-demo", state.demo);
  pill.classList.toggle("is-backup", !state.demo && backup);
  if (state.demo) { $("status-text").textContent = isNarrow() ? `Demo · 28 mayo 2025, ${state.hour}:00` : `Demostración · 28 mayo 2025, ${state.hour}:00`; return; }
  if (updatedMinutes == null) { $("status-text").textContent = "Sin datos en directo"; return; }
  const minutes = updatedMinutes + Math.floor((Date.now() - updatedAt) / 60000);
  const word = backup ? (isNarrow() ? "Copia" : "Copia de seguridad") : "En directo";
  $("status-text").textContent = `${word} · ${isNarrow() ? "" : "actualizado "}hace ${minutes} min`;
}

/* ------------------------------------------------------------------ info icons and the explanation panel */

// Explanations that open when an info icon is tapped. Fixed ones are here;
// the ones that depend on the data (profile note, official sources) are added as they arrive.
const info = {
  demo: { title: "Modo demostración", html:
    "<p>Estás viendo los datos del <b>28 de mayo de 2025</b>, el día con más polen de gramíneas de toda la serie histórica (1993-2026), no los de hoy.</p>"
    + "<p>Ese día solo hay datos de calidad del aire de las estaciones de la Comunidad de Madrid.</p>"
    + "<p>El planificador muestra la vista del <b>24 de mayo de 2025</b>, usando solo lo que se sabía ese día, para ver si el pico del 28 se anticipaba.</p>" },
  planner: { title: "Planificador de 5 días", html:
    "<p>Todo lo que aparece aquí es una <b>previsión</b>, no una medición. Cuanto más lejos está el día, menos segura es.</p>"
    + "<p>Cada día tiene cuatro tramos: mañana (7–13 h), tarde (13–20 h), noche (20–24 h) y madrugada (0–7 h). La madrugada de cada fila es la de la noche que empieza ese día.</p>"
    + "<p>El nivel de cada tramo es lo peor entre:</p><ul><li>el <b>polen</b> previsto para ese día, solo de los tipos que has marcado;</li>"
    + "<li>la <b>calidad del aire</b> prevista para ese tramo, con los mismos umbrales oficiales del ICA y tu perfil.</li></ul>"
    + "<p>Las dos previsiones son de <b>nuestros propios modelos</b>, hechos solo con las mediciones oficiales de la Comunidad de Madrid y del Ayuntamiento de Madrid (y la previsión del tiempo).</p>"
    + "<p>El polen se muestra donde el modelo demostró ser fiable; donde no, se indica «Sin previsión fiable». La previsión del aire solo se muestra hasta donde mejoró claramente a la referencia «misma hora de ayer»; más allá, la casilla usa solo el polen y lo dice.</p>"
    + "<p>La <b>confianza</b> de cada día (alta, media o baja) resume cuánto acertó la previsión en esa prueba. Toca cualquier casilla para ver el porqué.</p>" },
  hours: { title: "Mejores horas", html:
    "<p><b>Previsión</b> de nuestro modelo de calidad del aire, hecho con las mediciones oficiales de las estaciones más cercanas; no son mediciones. La temperatura y la lluvia son de Open-Meteo.</p>"
    + "<p>Cada barra es una hora de las próximas 24, empezando por la actual, de día y de noche. La línea vertical marca la medianoche. La estrella marca las mejores franjas de tres horas. En el móvil, desliza hacia la izquierda para ver más horas.</p>"
    + "<p>Si tienes alergia y la última medición de polen es alta, ninguna hora de hoy aparece mejor que esa medición.</p>" },
  measurements: { title: "Mediciones cerca de ti", html:
    "<p>Son <b>mediciones oficiales</b> de la estación más cercana que mide cada contaminante (redes de la Comunidad de Madrid y del Ayuntamiento de Madrid).</p>"
    + "<p>El valor es el que usa el Índice Nacional de Calidad del Aire: media de la última hora (NO2), de hasta 8 horas (O3) o de hasta 24 horas (PM10 y PM2.5), calculada con las horas del día en curso.</p>"
    + "<p>El polen se cuenta a mano y se publica con varios días de retraso: por eso se indica siempre la fecha de la lectura.</p>" },
  provisional: { title: "Dato provisional", html:
    "<p>«Provisional» significa que la red de medición todavía no ha validado el dato. Es el dato en tiempo real, y puede corregirse más adelante.</p>"
    + "<p>El índice oficial de calidad del aire también se calcula con datos provisionales.</p>" },
  freshness: { title: "Frescura de los datos", html:
    "<p>Para cada fuente se indica la hora de su último dato y cuánto hace.</p>"
    + "<p><b>Provisional</b>: la red aún no ha validado el dato. <b>Copia de seguridad</b>: la fuente no respondió y se muestra la última copia guardada.</p>" },
};

const infoIcon = (key, label) => ` <button class="info" type="button" data-info="${key}" aria-label="Más información: ${esc(label)}">${icon("info")}</button>`;

function openSheet(title, html, anchor) {
  $("sheet-title").textContent = title;
  $("sheet-body").innerHTML = html;
  $("sheet").hidden = false;
  $("sheet-backdrop").hidden = false;
  const sheet = $("sheet");
  sheet.style.left = sheet.style.top = "";
  if (window.innerWidth >= 900 && anchor) {
    // laptop: a popover next to what was clicked (to its left if there is room, otherwise below it)
    const box = anchor.getBoundingClientRect();
    const width = sheet.offsetWidth, height = sheet.offsetHeight;
    let left = box.left - width - 14;
    let top = box.top + box.height / 2 - height / 2;
    if (left < 12) { left = Math.min(box.left, window.innerWidth - width - 12); top = box.bottom + 10; }
    sheet.style.left = `${Math.max(12, left)}px`;
    sheet.style.top = `${Math.max(74, Math.min(top, window.innerHeight - height - 12))}px`;
  }
  $("sheet-close").focus({ preventScroll: true });
}

function closeSheet() {
  $("sheet").hidden = true;
  $("sheet-backdrop").hidden = true;
  document.querySelectorAll(".pcell.is-open").forEach((cell) => cell.classList.remove("is-open"));
}

function buildSheet() {
  $("sheet-close").addEventListener("click", closeSheet);
  $("sheet-backdrop").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("sheet").hidden) closeSheet(); });
  // any element with data-info="key" opens its explanation on tap (phones cannot hover)
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-info]");
    if (!button) return;
    event.preventDefault();                      // e.g. do not also open/close the <details> it sits in
    const entry = info[button.dataset.info];
    if (entry) openSheet(entry.title, entry.html, button);
  });
}

/* ------------------------------------------------------------------ the 5-day planner */

let plannerData = null;

async function loadPlanner(params, requestId) {
  const box = $("planner");
  if (!plannerData) box.innerHTML = '<div class="skeleton planner-skeleton"></div>';
  box.classList.add("is-loading");
  try {
    const data = await getJSON("/api/planner", { lat: params.lat, lon: params.lon, profile: params.profile, allergy_types: params.allergy_types, demo: params.demo });
    if (requestId !== requestNumber) return;     // a newer request is on its way
    plannerData = data;
    drawPlanner(data);
  } catch (error) {
    if (requestId !== requestNumber) return;
    plannerData = null;
    box.innerHTML = '<p class="hint" style="grid-column:1/-1">No se pudo cargar el planificador. Se volverá a intentar.</p>';
  }
  box.classList.remove("is-loading");
}

const CONFIDENCE_DOTS = { alta: 3, media: 2, baja: 1 };

function drawPlanner(data) {
  $("planner-title").textContent = data.demo ? "Planificador · vista del 24 mayo 2025" : "Planificador de 5 días";
  let html = "<span></span>" + data.blocks.map((b) => `<div class="planner-col">${esc(b.name)}<small>${esc(b.hours)}</small></div>`).join("");
  data.days.forEach((day, d) => {
    const dots = [1, 2, 3].map((n) => `<span class="${n <= CONFIDENCE_DOTS[day.confidence] ? "on" : ""}"></span>`).join("");
    html += `<div class="planner-day"><b>${esc(day.label)}</b><span class="conf" title="Confianza ${esc(day.confidence)}"><i aria-hidden="true">${dots}</i>${esc(day.confidence)}</span></div>`;
    day.blocks.forEach((block, b) => {
      const level = block.level_index == null ? null : config.levels[block.level_index];
      const temperature = block.weather ? `${num(block.weather.temperature_c)}°` : "";
      const rain = block.weather && block.weather.rain_mm >= 0.1 ? icon("droplet", "pcell-rain") : "";
      const label = `${day.label}, ${block.name.toLowerCase()}: ${block.level_label}${block.weather ? `, ${num(block.weather.temperature_c)} grados` : ""}. Confianza ${day.confidence}.`;
      html += level
        ? `<button class="pcell" type="button" data-day="${d}" data-block="${b}" style="--c:${level.color};--ink-c:${level.text_color}" aria-label="${esc(label)}">${ICONS[block.level_index]}<span>${temperature}</span>${rain}</button>`
        : `<button class="pcell pcell-none" type="button" data-day="${d}" data-block="${b}" aria-label="${esc(label)}"><span>– ${temperature}</span>${rain}</button>`;
    });
  });
  $("planner").innerHTML = html;
  $("planner").onclick = (event) => {
    const cell = event.target.closest(".pcell");
    if (!cell) return;
    const day = plannerData.days[Number(cell.dataset.day)];
    const block = day.blocks[Number(cell.dataset.block)];
    openSheet(`${day.label} · ${block.name} (${block.hours})`, plannerDetail(day, block), cell);
    cell.classList.add("is-open");
  };
}

// "Plaza del Carmen y Plaza de España": the stations our air forecast for this place is made from.
function airStationsText(stations) {
  const names = [...new Set(Object.values(stations || {}).map((s) => s.station))];
  return names.length ? names.join(" y ") : "las estaciones más cercanas";
}

// The "why" behind one planner cell.
function plannerDetail(day, block) {
  const level = block.level_index == null ? null : config.levels[block.level_index];
  let html = level
    ? `<p><span class="sheet-level" style="background:${level.color};color:${level.text_color}">${ICONS[block.level_index]}${esc(block.level_label)}</span></p>`
    : '<p><span class="sheet-level" style="background:#edf0ee">Sin previsión</span></p>';
  html += `<p>${esc(block.basis)}</p>`;
  if (block.next_day) html += `<p>Esta madrugada pertenece a la noche ${day.label === "Hoy" ? "de hoy" : `del ${esc(day.label.toLowerCase())}`}: son las primeras horas del día ${ddmm(block.date)}.</p>`;

  html += "<h4>Polen (previsión para ese día)</h4>";
  if (!block.pollen.length) {
    html += "<p>No has marcado alergia al polen, así que el polen no cuenta para este nivel.</p>";
  } else {
    html += "<ul>" + block.pollen.map((p) => {
      const chip = p.kind === "model" ? '<span class="source-chip">Nuestro modelo</span>'
        : p.kind === "measurement" ? '<span class="source-chip is-measured">Medición oficial</span>'
        : '<span class="source-chip is-none">Sin previsión fiable</span>';
      const accuracy = p.kind === "model" && p.accuracy != null ? ` Acertó el ${num(p.accuracy * 100)} % de los días de temporada en la prueba con 2025.` : "";
      const level = p.level_name ? `nivel <b>${esc(p.level_name)}</b>` : "sin nivel";
      const why = p.kind === "none" ? " Nuestro modelo no superó la prueba para este tipo y antelación."
        : p.kind === "measurement" ? ` ${esc(p.source)}.` : "";
      return `<li><b>${esc(p.name)}</b>: ${level} ${chip}${accuracy}${why}</li>`;
    }).join("") + "</ul>";
  }

  html += "<h4>Calidad del aire (previsión para el tramo)</h4>";
  if (block.air) {
    const values = Object.entries(block.air.values).map(([p, v]) => `${esc(p)} ${num(v)}`).join(" · ");
    html += `<p>ICA previsto: <b>${esc(block.air.category_name)}</b> (por ${esc(block.air.pollutant)}, ${num(block.air.value)} µg/m³). `
      + `<span class="source-chip">Nuestro modelo</span></p><p>Valores del tramo: ${values} µg/m³ (hora más alta para NO2, media del tramo para el resto). Modelo propio, hecho con las mediciones oficiales de ${esc(airStationsText(plannerData.air_stations))}.</p>`
      + `<p>Recomendación oficial para esa categoría y tu perfil: «${esc(block.air.official_advice)}»</p>`;
  } else {
    html += `<p>No mostramos previsión de calidad del aire para este tramo: solo se muestra hasta ${num(plannerData.air_horizon_hours)} horas de antelación, que es hasta donde mejoró claramente a la referencia en las pruebas.</p>`;
  }

  html += "<h4>Tiempo</h4>";
  html += block.weather
    ? `<p>${num(block.weather.temperature_c)} °C de media, ${block.weather.rain_mm >= 0.1 ? `lluvia prevista: ${num(block.weather.rain_mm, 1)} mm` : "sin lluvia prevista"}. Previsión de Open-Meteo.</p>`
    : "<p>Sin previsión del tiempo.</p>";

  html += `<h4>Confianza del día: ${esc(day.confidence)}</h4><p>${esc(day.confidence_reason)} Las previsiones son menos seguras cuanto más lejos está el día.</p>`;
  if (plannerData.demo) html += "<p>Vista del 24 de mayo de 2025: el polen y el aire son lo que nuestros modelos preveían con lo que se sabía ese día.</p>";
  return html;
}

/* ------------------------------------------------------------------ the forecast line chart */

// Up to three lines at once. Each has its own colour AND its own marker shape,
// so they can be told apart without relying on colour.
const CHART_MAX_LINES = 3;
const SERIES_COLORS = ["#2a78d6", "#b0358f", "#8a6a00"];
const POLLEN_BAND_COLORS = ["#e3f3e3", "#fff0c2", "#ffdcc4", "#f8c4c4"];   // bajo, medio, alto, muy alto (each band also has its name)
const WEEKDAYS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const DAY_MS = 24 * 60 * 60 * 1000;

let chartData = null;
let chartMode = null;           // "pollen" or "air"; null = choose automatically
let chartSlots = [];            // which pollen type uses each colour: [key or null, key or null, key or null]
let chartFocus = null;          // the pollen type whose prediction interval is shaded
let chartPick = null;           // the day (pollen) or hour (air) that was tapped

async function loadChart(params, requestId) {
  $("chart").classList.add("is-loading");
  try {
    const data = await getJSON("/api/chart", { lat: params.lat, lon: params.lon, allergy_types: params.allergy_types, demo: params.demo });
    if (requestId !== requestNumber) return;      // a newer request is on its way
    chartData = data;
    chartPick = null;
    drawChart();
  } catch (error) {
    if (requestId !== requestNumber) return;
    chartData = null;
    $("chart").innerHTML = '<p class="chart-empty">No se pudo cargar el gráfico. Se volverá a intentar.</p>';
    $("chart-detail").hidden = true;
  }
  $("chart").classList.remove("is-loading");
}

function buildChart() {
  $("chart-mode").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    chartMode = button.dataset.mode; chartPick = null;
    drawChart();
  });
  // pollen-type chips: tap to show a line and shade its interval; tap the shaded one again to hide it
  $("chart-series").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    const key = chip.dataset.key;
    const slot = chartSlots.indexOf(key);
    if (slot >= 0 && chartFocus === key && chartSlots.filter(Boolean).length > 1) {
      chartSlots[slot] = null;                                  // hide it
      chartFocus = chartSlots.find(Boolean);
    } else {
      if (slot < 0) {
        let free = chartSlots.indexOf(null);
        if (free < 0) free = chartSlots.findIndex((k) => k !== chartFocus);   // full: replace one that is not in focus
        chartSlots[free] = key;
      }
      chartFocus = key;
    }
    drawChart();
  });
  // tap (or move the mouse over) the chart to read the values of a day or an hour
  const pick = (event) => {
    const svg = $("chart").querySelector("svg");
    if (!svg || !svg.dataset.count) return;
    const box = svg.getBoundingClientRect();
    const first = Number(svg.dataset.first), last = Number(svg.dataset.last), count = Number(svg.dataset.count);
    const position = (event.clientX - box.left - first) / (last - first);
    const index = Math.max(0, Math.min(count - 1, Math.round(position * (count - 1))));
    if (index !== chartPick) { chartPick = index; drawChart(); }
  };
  $("chart").addEventListener("click", pick);
  $("chart").addEventListener("pointermove", (event) => { if (event.pointerType === "mouse") pick(event); });
}

// A small marker: circle, square or triangle (one shape per line).
function marker(shape, x, y, color, hollow = false, size = 3.6) {
  const paint = `fill="${hollow ? "#fff" : color}" stroke="${hollow ? color : "#fff"}" stroke-width="1.5"`;
  if (shape === 1) return `<rect x="${x - size}" y="${y - size}" width="${size * 2}" height="${size * 2}" rx="1" ${paint}/>`;
  if (shape === 2) return `<path d="M${x} ${y - size - 1}L${x + size + 0.8} ${y + size}H${x - size - 0.8}Z" ${paint}/>`;
  return `<circle cx="${x}" cy="${y}" r="${size}" ${paint}/>`;
}
// A tiny line sample for the chips and the legend.
const sample = (color, shape, dashed = false) =>
  `<svg viewBox="0 0 22 12" aria-hidden="true"><path d="M1 6H21" stroke="${color}" stroke-width="2" ${dashed ? 'stroke-dasharray="4 3"' : ""}/>${shape == null ? "" : marker(shape, 11, 6, color, false, 3)}</svg>`;

function drawChart() {
  if (!chartData) return;
  const pollen = chartData.pollen, air = chartData.air;
  const mode = chartMode || (pollen.series.length ? "pollen" : "air");
  for (const button of $("chart-mode").children) button.setAttribute("aria-pressed", button.dataset.mode === mode);
  $("chart-detail").hidden = false;
  if (mode === "pollen") drawPollenChart(pollen); else drawAirChart(air);
}

function chartEmpty(message) {
  $("chart").innerHTML = `<p class="chart-empty">${esc(message)}</p>`;
  $("chart-detail").hidden = true;
  $("chart-legend").innerHTML = "";
}

function chartFrame() {
  const width = Math.max($("chart").clientWidth, 280), height = 220;
  return { width, height, left: 8, right: width - 8, top: 20, bottom: height - 24 };
}

function drawPollenChart(data) {
  // keep the colour of the types that are still selected; fill the free colours with the new ones
  const keys = data.series.map((s) => s.key);
  chartSlots = Array.from({ length: CHART_MAX_LINES }, (_, i) => (keys.includes(chartSlots[i]) ? chartSlots[i] : null));
  for (const key of keys) {
    if (chartSlots.includes(key)) continue;
    const free = chartSlots.indexOf(null);
    if (free < 0) break;
    chartSlots[free] = key;
  }
  if (!chartSlots.includes(chartFocus)) chartFocus = chartSlots.find(Boolean) || null;

  $("chart-series").innerHTML = data.series.map((s) => {
    const slot = chartSlots.indexOf(s.key);
    return `<button class="chip ${s.key === chartFocus ? "is-focus" : ""}" type="button" data-key="${esc(s.key)}" aria-pressed="${slot >= 0}">`
      + `${slot >= 0 ? sample(SERIES_COLORS[slot], slot) : ""}${esc(s.name)}</button>`;
  }).join("");
  $("chart-note").textContent = [
    data.station ? `Estación de polen: ${data.station.station} (a ${num(data.station.distance_km, 1)} km).` : "",
    data.series.length > CHART_MAX_LINES ? `Se muestran hasta ${CHART_MAX_LINES} tipos a la vez: toca otro para cambiarlo.` : "",
    data.demo && data.series.length ? "Vista del 24 de mayo de 2025, con lo que se sabía ese día. Las cruces son lo que se midió después (del tipo resaltado)." : "",
    ...data.notes,
  ].filter(Boolean).join(" ");

  if (!data.series.length) {
    chartEmpty("Activa «Tengo alergia al polen» y elige uno o más tipos para ver sus lecturas y su previsión. Mientras tanto puedes ver la previsión del aire.");
    return;
  }

  const f = chartFrame();
  const firstDay = Date.parse(data.first_day.slice(0, 10)), today = Date.parse(data.today.slice(0, 10));
  const count = Math.round((Date.parse(data.last_day.slice(0, 10)) - firstDay) / DAY_MS) + 1;
  const todayIndex = Math.round((today - firstDay) / DAY_MS);
  const dayIndex = (iso) => Math.round((Date.parse(iso.slice(0, 10)) - firstDay) / DAY_MS);
  const x = (i) => f.left + 14 + i * (f.right - f.left - 28) / (count - 1);
  if (chartPick == null || chartPick >= count) chartPick = todayIndex;

  // The vertical scale is the official LEVEL: each of the four levels gets the same height, and a
  // value sits inside its level's band in proportion to that pollen type's own thresholds.
  // That way several pollen types (with different thresholds) can share one chart.
  const bandHeight = (f.bottom - f.top) / 4;
  const scale = (series) => {
    const values = [...series.observed.map((p) => p.value), ...series.forecast.flatMap((p) => [p.high, p.actual || 0])];
    const bounds = [0, ...series.thresholds, Math.max(series.thresholds[2] * 2, ...values) * 1.05];
    return (value) => {
      let k = 0;
      while (k < 3 && value >= bounds[k + 1]) k += 1;
      return f.bottom - bandHeight * (k + Math.min((value - bounds[k]) / (bounds[k + 1] - bounds[k]), 1));
    };
  };

  let svg = "";
  data.levels.forEach((name, k) => {
    const top = f.bottom - bandHeight * (k + 1);
    svg += `<rect x="${f.left}" y="${top}" width="${f.right - f.left}" height="${bandHeight - 1}" rx="3" fill="${POLLEN_BAND_COLORS[k]}"/>`
      + `<text class="band-name" x="${f.left + 5}" y="${top + 12}">${esc(name)}</text>`;
  });
  // today: on its left what was measured, on its right the forecast
  svg += `<path d="M${x(todayIndex)} ${f.top - 4}V${f.bottom}" stroke="#14201b" stroke-width="1" stroke-dasharray="2 3"/>`
    + `<text x="${x(todayIndex) - 6}" y="${f.top - 8}" text-anchor="end">Medido</text>`
    + `<text x="${x(todayIndex) + 6}" y="${f.top - 8}">Previsión</text>`;
  for (let i = 0; i < count; i += 1) {
    const day = new Date(firstDay + i * DAY_MS);
    svg += `<text class="${i === todayIndex ? "axis-strong" : ""}" x="${x(i)}" y="${f.height - 8}" text-anchor="middle">${i === todayIndex && !data.demo ? "hoy" : day.getUTCDate()}</text>`;
  }
  svg += `<path d="M${x(chartPick)} ${f.top}V${f.bottom}" stroke="#14201b" stroke-width="1.2"/>`;

  // the lines: the one in focus is drawn last (on top) and is the only one with its interval shaded
  const shown = chartSlots.map((key, slot) => ({ slot, series: data.series.find((s) => s.key === key) })).filter((e) => e.series);
  shown.sort((a, b) => (a.series.key === chartFocus) - (b.series.key === chartFocus));
  let anyUnreliable = false, anyActual = false;
  for (const { slot, series } of shown) {
    const y = scale(series), color = SERIES_COLORS[slot];
    const observed = series.observed.map((p) => [x(dayIndex(p.date)), y(p.value)]);
    const forecast = series.forecast.map((p) => [x(dayIndex(p.date)), y(p.value)]);
    const line = (points) => points.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("");
    if (series.key === chartFocus && series.forecast.length) {
      const upper = series.forecast.map((p) => [x(dayIndex(p.date)), y(p.high)]);
      const lower = series.forecast.map((p) => [x(dayIndex(p.date)), y(p.low)]).reverse();
      svg += `<path d="${line([...upper, ...lower])}Z" fill="${color}" fill-opacity=".2"/>`;
    }
    // the days between the last reading and today have no data yet (readings are published about 3 days late)
    if (observed.length && forecast.length) svg += `<path d="${line([observed[observed.length - 1], forecast[0]])}" stroke="${color}" stroke-width="1.5" stroke-dasharray="1 4" stroke-linecap="round" fill="none" opacity=".7"/>`;
    if (observed.length) svg += `<path d="${line(observed)}" stroke="${color}" stroke-width="2" fill="none" stroke-linejoin="round"/>`;
    if (forecast.length) svg += `<path d="${line(forecast)}" stroke="${color}" stroke-width="2" stroke-dasharray="5 4" fill="none" stroke-linejoin="round"/>`;
    observed.forEach((p) => { svg += marker(slot, p[0], p[1], color); });
    series.forecast.forEach((p, i) => {
      svg += marker(slot, forecast[i][0], forecast[i][1], color, !p.reliable);
      anyUnreliable = anyUnreliable || !p.reliable;
      if (p.actual != null && series.key === chartFocus) {   // demo mode: what was really measured afterwards (only for the line in focus)
        const ax = forecast[i][0], ay = y(p.actual);
        const cross = `d="M${ax - 3.5} ${ay - 3.5}l7 7M${ax + 3.5} ${ay - 3.5}l-7 7" stroke-linecap="round"`;
        svg += `<path ${cross} stroke="#fff" stroke-width="4"/><path ${cross} stroke="#14201b" stroke-width="1.8"/>`;
        anyActual = true;
      }
    });
  }
  const names = shown.map((e) => e.series.name).join(", ");
  $("chart").innerHTML = `<svg viewBox="0 0 ${f.width} ${f.height}" role="img" data-count="${count}" data-first="${x(0)}" data-last="${x(count - 1)}"`
    + ` aria-label="Polen medido en los últimos 7 días y previsto para los próximos 5 días: ${esc(names)}. Toca un día para ver sus valores.">${svg}</svg>`;

  // the values of the chosen day, in words
  const pickedDay = new Date(firstDay + chartPick * DAY_MS);
  const dayText = `${WEEKDAYS[pickedDay.getUTCDay()]} ${pickedDay.getUTCDate()}/${String(pickedDay.getUTCMonth() + 1).padStart(2, "0")}`;
  const lines = shown.slice().sort((a, b) => a.slot - b.slot).map(({ series }) => {
    const seen = series.observed.find((p) => dayIndex(p.date) === chartPick);
    const expected = series.forecast.find((p) => dayIndex(p.date) === chartPick);
    let text = "sin lectura todavía (se publican con unos 3 días de retraso)";
    if (seen) text = `medido ${num(seen.value)} granos/m³ (${esc(seen.level_name)})`;
    else if (expected) {
      text = `previsión ${num(expected.value)} granos/m³ (${esc(expected.level_name)}), intervalo del ${num(data.interval * 100)} %: ${num(expected.low)}–${num(expected.high)}`
        + (expected.reliable ? "" : " · poco fiable para este día")
        + (expected.actual != null ? ` · medido después: ${num(expected.actual)}` : "");
    }
    return `${esc(series.name)}: ${text}`;
  });
  $("chart-detail").innerHTML = `<b>${dayText}${chartPick === todayIndex && !data.demo ? " (hoy)" : ""}</b><br>${lines.join("<br>")}`;

  $("chart-legend").innerHTML = `<span>${sample("#4d5c55", null)}Medido</span><span>${sample("#4d5c55", null, true)}Previsión</span>`
    + `<span><i style="background:#4d5c55;opacity:.3"></i>Intervalo del ${num(data.interval * 100)} %</span>`
    + (anyUnreliable ? `<span><svg viewBox="0 0 22 12" aria-hidden="true">${marker(0, 11, 6, "#4d5c55", true, 3.4)}</svg>Poco fiable</span>` : "")
    + (anyActual ? '<span><svg viewBox="0 0 22 12" aria-hidden="true"><path d="M8 3l6 6M14 3l-6 6" stroke="#14201b" stroke-width="1.8" stroke-linecap="round"/></svg>Medido después</span>' : "");
}

function drawAirChart(data) {
  $("chart-series").innerHTML = "";
  $("chart-note").textContent = [
    data.pollutant ? `Contaminante con la categoría más alta prevista: ${data.pollutant}.` : "",
    data.station ? `${data.source} ${data.station.station} (${data.station.source}, a ${num(data.station.distance_km, 1)} km).` : "",
    data.pollutant ? `Se muestra hasta ${num(data.horizon_hours)} horas de antelación.` : "",
    ...data.notes].filter(Boolean).join(" ");
  if (!data.pollutant || data.hours.length < 2) { chartEmpty("No hay previsión de calidad del aire para este punto en este momento."); return; }

  const f = chartFrame();
  const hours = data.hours, count = hours.length;
  if (chartPick == null || chartPick >= count) chartPick = 0;
  const x = (i) => f.left + 4 + i * (f.right - f.left - 8) / (count - 1);
  // show the official bands up to the one the highest forecast value reaches (at least the first two)
  const highest = Math.max(...hours.map((h) => h.value));
  let lastBand = data.bands.findIndex((limit) => Math.round(highest) <= limit);
  lastBand = Math.max(lastBand < 0 ? data.bands.length - 1 : lastBand, 1);
  const top = data.bands[lastBand];
  const y = (value) => f.bottom - (f.bottom - f.top) * Math.min(value / top, 1);

  let svg = "";
  for (let k = 0; k <= lastBand; k += 1) {
    const from = k ? data.bands[k - 1] : 0;
    svg += `<rect x="${f.left}" y="${y(data.bands[k])}" width="${f.right - f.left}" height="${Math.max(y(from) - y(data.bands[k]) - 1, 1)}" rx="3" fill="${data.categories[k].color}" fill-opacity=".3"/>`
      + `<text class="band-name" x="${f.left + 5}" y="${y(data.bands[k]) + 12}">${esc(data.categories[k].name)} (hasta ${data.bands[k]})</text>`;
  }
  hours.forEach((h, i) => {                      // a tick and the weekday at every midnight
    if (h.time.slice(11, 13) !== "00" || i === 0) return;
    const day = new Date(Date.parse(h.time.slice(0, 10)));
    svg += `<path d="M${x(i)} ${f.top}V${f.bottom + 4}" stroke="#14201b" stroke-opacity=".25" stroke-width="1"/>`
      + `<text x="${x(i) + 3}" y="${f.height - 8}">${WEEKDAYS[day.getUTCDay()]} ${day.getUTCDate()}</text>`;
  });
  svg += `<text x="${f.left}" y="${f.top - 8}">${esc(data.pollutant)}, µg/m³ · previsión por horas</text>`;
  svg += `<path d="${hours.map((h, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(h.value).toFixed(1)}`).join("")}" stroke="${SERIES_COLORS[0]}" stroke-width="2" stroke-dasharray="5 3" fill="none" stroke-linejoin="round"/>`;
  const picked = hours[chartPick];
  svg += `<path d="M${x(chartPick)} ${f.top}V${f.bottom}" stroke="#14201b" stroke-width="1.2"/>` + marker(0, x(chartPick), y(picked.value), SERIES_COLORS[0], false, 4.5);
  $("chart").innerHTML = `<svg viewBox="0 0 ${f.width} ${f.height}" role="img" data-count="${count}" data-first="${x(0)}" data-last="${x(count - 1)}"`
    + ` aria-label="Previsión por horas de ${esc(data.pollutant)} con las bandas del índice de calidad del aire. Toca una hora para ver su valor.">${svg}</svg>`;

  const day = new Date(Date.parse(picked.time.slice(0, 10)));
  $("chart-detail").innerHTML = `<b>${WEEKDAYS[day.getUTCDay()]} ${ddmm(picked.time)}, ${hhmm(picked.time)}</b><br>`
    + `${esc(data.pollutant)} previsto: ${num(picked.value)} µg/m³ · ${esc(picked.category_name || "sin categoría")}`;
  $("chart-legend").innerHTML = `<span>${sample(SERIES_COLORS[0], null, true)}Previsión (modelo propio)</span>`;
}

// The explanation behind the chart's info icon: "¿Cómo predecimos?"
function chartInfo() {
  const byModel = {};
  for (const t of config.pollen_types) (byModel[t.model] = byModel[t.model] || []).push(t.label.toLowerCase());
  const models = Object.entries(byModel).map(([model, names]) => `<li><b>${esc(model)}</b>: ${esc(names.join(", "))}.</li>`).join("");
  return { title: "¿Cómo predecimos?", html:
    "<h4>Polen: nuestros modelos</h4>"
    + "<p>Probamos cuatro métodos con todo 2025 (un año que no habían visto) y para cada tipo de polen usamos el que más acertó:</p>"
    + `<ul>${models}</ul>`
    + "<p><b>Gradient boosting</b> son muchos árboles de decisión pequeños que aprenden de todas las estaciones a la vez. "
    + "<b>ARIMAX</b> es un modelo estadístico clásico: ciclo anual + meteorología + las últimas lecturas.</p>"
    + "<h4>Qué datos usan</h4>"
    + "<ul><li>El historial de polen de la Red Palinocam desde 1993.</li>"
    + "<li>La meteorología (Open-Meteo): calor acumulado en el año, lluvia de las últimas semanas y la previsión del tiempo. Es lo único que no viene de la Comunidad o del Ayuntamiento.</li>"
    + "<li>Las lecturas se publican con unos <b>3 días de retraso</b>: la previsión de «mañana» se hace 4 días después de la última lectura conocida. Por eso hay un hueco entre lo medido y hoy.</li></ul>"
    + "<h4>Cómo leer el gráfico</h4>"
    + "<p>Línea continua: lo medido. Línea discontinua: la previsión (su valor central). La zona sombreada es el <b>intervalo de predicción del 80 %</b>; "
    + "en la prueba con 2025 el valor real cayó dentro algo menos de lo previsto (en torno al 70 % con gradient boosting y al 55–70 % con ARIMAX). "
    + "Las bandas de fondo son los niveles oficiales (bajo, medio, alto, muy alto) de cada tipo.</p>"
    + "<h4>Aire</h4>"
    + "<p>La previsión de calidad del aire también es <b>nuestra</b>: un modelo de gradient boosting por contaminante (NO2, O3, PM10, PM2.5), entrenado con las mediciones horarias oficiales de las estaciones de la Comunidad y del Ayuntamiento de Madrid desde 2019 y con la meteorología. "
    + "Lo comparamos con la referencia «misma hora de ayer» y con un modelo SARIMAX, y solo la mostramos hasta donde mejoró claramente a la referencia.</p>"
    + "<h4>De dónde salen los datos</h4>"
    + "<p>Todos los datos de polen y de calidad del aire, medidos y previstos, salen únicamente de la Comunidad de Madrid y del Ayuntamiento de Madrid. Solo la meteorología es de Open-Meteo.</p>"
    + `<p><a href="${esc(config.model_report_url)}" target="_blank" rel="noopener">Leer el informe completo de los modelos (MODEL_REPORT.md)</a></p>` };
}

/* ------------------------------------------------------------------ the AI assistant */

const CHAT_KEEP = 6;            // only the last 6 messages are sent (the server enforces it too)
const chatHistory = [];         // {role, content}: questions and the answers that worked
let chatBusy = false;

function buildChat() {
  if (!config.ai_assistant_enabled) return;                 // switched off in config.py: no button at all
  $("chat-fab").hidden = false;
  $("chat-fab").addEventListener("click", openChat);
  $("chat-close").addEventListener("click", closeChat);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("chat").hidden) closeChat(); });
  $("chat-suggestions").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (chip) sendChat(chip.textContent);
  });
  $("chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("chat-input").value.trim();
    if (text) sendChat(text);
  });
  if (!config.ai_assistant_available) chatUnavailable();
}

function openChat() {
  $("chat").hidden = false;
  $("chat-fab").hidden = true;
  drawChatContext();
  if (window.innerWidth >= 600) $("chat-input").focus();    // on phones, don't pop the keyboard up straight away
}

function closeChat() {
  $("chat").hidden = true;
  $("chat-fab").hidden = false;
}

// The line under the title: what the assistant already knows from the panel.
function drawChatContext() {
  const profile = config.profiles.find((p) => p.key === state.profile);
  $("chat-context").textContent = `${profile ? profile.label : ""} · ${state.placeName}${state.demo ? " · Demostración 28/05/2025" : ""}`;
}

function chatUnavailable() {
  $("chat-suggestions").hidden = true;
  $("chat-form").classList.add("is-off");
  $("chat-input").disabled = true;
  $("chat-input").placeholder = "Asistente no disponible";
  addBubble("error", "El asistente no está disponible en este momento. El resto de la aplicación funciona con normalidad.");
}

function addBubble(kind, text, withLabel = false) {
  const bubble = document.createElement("div");
  bubble.className = `msg msg-${kind}`;
  const p = document.createElement("p");
  p.textContent = text;                                     // textContent: nothing the model writes can run as code
  bubble.appendChild(p);
  if (withLabel) {
    const label = document.createElement("div");
    label.className = "msg-label";
    label.textContent = "Generado por IA con datos oficiales";
    bubble.appendChild(label);
  }
  $("chat-body").appendChild(bubble);
  $("chat-body").scrollTop = $("chat-body").scrollHeight;
  return bubble;
}

async function sendChat(text) {
  if (chatBusy) return;
  chatBusy = true;
  $("chat-form").classList.add("is-busy");
  $("chat-suggestions").hidden = true;
  $("chat-input").value = "";
  drawChatContext();
  addBubble("user", text);
  chatHistory.push({ role: "user", content: text });

  // typing indicator while we wait
  const typing = document.createElement("div");
  typing.className = "msg msg-ai";
  typing.innerHTML = '<span class="typing" aria-label="Respira está escribiendo"><i></i><i></i><i></i></span>';
  $("chat-body").appendChild(typing);
  $("chat-body").scrollTop = $("chat-body").scrollHeight;

  let answer;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: chatHistory.slice(-CHAT_KEEP),
        // what the panel already knows, so the assistant can use it
        context: {
          lat: state.lat, lon: state.lon, place: state.placeName, profile: state.profile,
          allergy_types: state.allergic ? [...state.types] : [],
          demo: state.demo, hour: state.demo ? state.hour : null,
        },
      }),
    });
    if (!response.ok) throw new Error(`chat: ${response.status}`);
    answer = await response.json();
  } catch (error) {
    // the chat failed, but nothing else in the app depends on it
    answer = { ok: false, available: true, reply: "No he podido conectar con el asistente. Inténtalo de nuevo en un momento; el mapa y el panel siguen funcionando con normalidad." };
  }

  typing.remove();
  if (answer.ok) {
    addBubble("ai", answer.reply, true);
    chatHistory.push({ role: "assistant", content: answer.reply });
  } else {
    chatHistory.pop();                                      // forget the question that got no answer
    if (answer.available === false) chatUnavailable();      // shows the "no disponible" message and locks the box
    else addBubble("error", answer.reply);
  }
  chatBusy = false;
  $("chat-form").classList.remove("is-busy");
}

start();
