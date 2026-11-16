/* Respira MAD - the page's behaviour.
   It only draws what the server sends: every number, level and text comes
   from /api/... (see api.py). Plain JavaScript, no build step. */

"use strict";

const REFRESH_MS = 5 * 60 * 1000;                 // reload the data every 5 minutes
// Rough box around the Comunidad de Madrid, to warn if "my location" is outside it.
const MADRID_BOX = { south: 39.88, north: 41.17, west: -4.58, east: -3.05 };
const CUSTOM_PLACE = "__custom__";

const ICONS = [
  '<svg viewBox="0 0 24 24"><path d="M5 12.500l4.500 4.500L19 7.500"/></svg>',                                   // good
  '<svg viewBox="0 0 24 24"><path d="M12 4l9 15.500H3z"/><path d="M12 10v4.500M12 17.200v.100"/></svg>',           // be careful
  '<svg viewBox="0 0 24 24"><path d="M4 11.500L12 4l8 7.500"/><path d="M6.500 10v9.500h11V10"/><path d="M10 19.500v-5h4v5"/></svg>', // stay in
];

const $ = (id) => document.getElementById(id);
const isNarrow = () => window.innerWidth < 520;     // phones get shorter labels in the header

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
let map, pin, stationLayer;
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
  window.addEventListener("resize", () => { drawDemoLabel(); drawStatus(); });

  // my location
  $("locate").addEventListener("click", useMyLocation);

  // legend of the hourly strip: colour + label (never colour alone)
  $("strip-legend").innerHTML = config.levels.map((l) => `<span><i style="background:${l.color}"></i>${esc(l.label)}</span>`).join("")
    + "<span>★ Mejor franja</span>";

  buildSheet();
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
  stationLayer = L.layerGroup().addTo(map);

  const pinIcon = L.divIcon({
    className: "",
    html: '<svg class="pin" viewBox="0 0 34 42"><path d="M17 41C17 41 31 27.500 31 16A14 14 0 0 0 3 16C3 27.500 17 41 17 41z" fill="#0f6b4f" stroke="#fff" stroke-width="2.500"/><circle cx="17" cy="16" r="5.500" fill="#fff"/></svg>',
    iconSize: [34, 42], iconAnchor: [17, 41],
  });
  pin = L.marker([state.lat, state.lon], { icon: pinIcon, zIndexOffset: 1000, keyboard: false, interactive: false }).addTo(map);

  // clicking the map moves the pin and updates everything
  map.on("click", (event) => setPoint(event.latlng.lat, event.latlng.lng, "Punto elegido en el mapa"));

  // legend (opens by default on wide screens)
  const legend = L.control({ position: "bottomleft" });
  legend.onAdd = () => {
    const box = L.DomUtil.create("details", "legend");
    box.open = window.innerWidth >= 900;
    box.innerHTML = "<summary>Leyenda</summary><ul>"
      + config.ica_categories.map((c, i) => `<li><span class="swatch" style="background:${c.color};border-color:${outline(i)}"></span>${esc(c.name)}</li>`).join("")
      + '<li><span class="pollen-marker" style="width:16px;height:16px;font-size:10px;border-radius:5px">🌼</span>Estación de polen</li>'
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
  if (options.fromSelect || options.recenter) map.setView([lat, lon], Math.max(map.getZoom(), 10));
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
  if (!options.keepPlanner) loadPlanner(params, mine);
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
    + `<button class="info-link" type="button" data-info="official-${risk.recommendations.indexOf(r)}">Texto oficial <span class="info" aria-hidden="true">i</span></button></blockquote>`;
  const [first, ...others] = risk.recommendations;
  $("advice").innerHTML = (first ? quote(first) : "")
    + (others.length ? `<details class="more-advice"><summary>Ver también la recomendación de calidad del aire</summary>${others.map(quote).join("")}</details>` : "");
  $("warnings").innerHTML = risk.notes.map((n) => `<div class="warning">${esc(n)}</div>`).join("");

  // pollutant tiles (official station measurements)
  $("tiles").innerHTML = risk.air.map((a) => `
    <div class="tile">
      <div class="tile-name"><span>${esc(a.pollutant)}</span>${a.provisional ? '<button class="tag tag-warn tag-button" type="button" data-info="provisional">Provisional <span class="info" aria-hidden="true">i</span></button>' : ""}</div>
      <div class="tile-value">${num(a.ica_value)} <small>µg/m³</small></div>
      <div class="tile-cat"><span class="swatch" style="background:${a.color};border-color:${outline(a.category)}"></span>${esc(a.category_name)}</div>
      <div class="tile-meta">${esc(a.station)} (${esc(a.source)}) · a ${num(a.distance_km, 1)} km · ${ddmm(a.timestamp)} ${hhmm(a.timestamp)}</div>
    </div>`).join("") || '<p class="hint">Sin mediciones de calidad del aire en este momento.</p>';

  // pollen
  const pollen = risk.pollen;
  const box = $("pollen-tile");
  box.hidden = !pollen;
  if (pollen) {
    const age = pollen.age_days === 0 ? "de hoy" : `de hace ${pollen.age_days} días`;
    const head = pollen.kind === "measurement"
      ? `<b>Polen</b> · ${esc(pollen.station)} (${num(pollen.distance_km, 1)} km) · lectura del ${ddmm(pollen.reading_date)}, <b>${age}</b>`
      : '<b>Polen</b> · sin lectura reciente de polen <span class="tag tag-forecast">Previsión Open-Meteo</span>';
    const shown = pollen.types.filter((t, i) => t.counts_for_you || t.level > 0 || i < 3).slice(0, 6);
    box.innerHTML = `<div class="pollen-head">${head}</div>` + shown.map((t) => `
      <div class="pollen-row ${t.counts_for_you ? "counts" : ""}">
        <span>${esc(t.name)}${t.counts_for_you ? " · cuenta para ti" : ""}</span>
        <span>${num(t.value)} granos/m³ <span class="lvl lvl-${t.level}">${esc(t.level_name)}</span></span>
      </div>`).join("");
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
      <span class="hour-bar"></span><span class="hour-num">${String(h.hour).padStart(2, "0")}</span>
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
    html = `<p class="windows-title">★ Mejores franjas de las próximas 24 horas (nivel «${esc(config.levels[data.windows[0].level_index].label)}»)</p><ul class="windows">`
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
    `NO2 ${num(h.NO2)}`, `O3 ${num(h.O3)}`, `PM10 ${num(h.PM10)}`, `PM2.5 ${num(h["PM2.5"])} µg/m³`,
    `${num(h.temperature_c)} °C`, h.rain_mm > 0 ? `lluvia ${num(h.rain_mm, 1)} mm` : "sin lluvia",
  ];
  if (state.allergic) parts.push(`gramíneas ${num(h.grass_pollen)} · olivo ${num(h.olive_pollen)} granos/m³`);
  $("hour-detail").innerHTML = `<b>${esc(h.day)} ${String(h.hour).padStart(2, "0")}:00 · ${esc(h.level_label)}</b>`
    + (h.limited_by ? ` (por ${esc(h.limited_by)})` : "")
    + `<br>${esc(parts.join(" · "))}`;
}

function drawStations(data) {
  stationLayer.clearLayers();
  for (const s of data.air) {
    L.circleMarker([s.lat, s.lon], { radius: 8, color: outline(s.category), weight: s.category === 0 ? 2.2 : 1.3, fillColor: s.color, fillOpacity: 0.95, bubblingMouseEvents: false })
      .bindTooltip(`${esc(s.station)}: ${esc(s.category_name)}`)
      .bindPopup(`<b>${esc(s.station)}</b><br><span class="popup-meta">${esc(s.source)} · ${ddmm(s.timestamp)} ${hhmm(s.timestamp)}</span><br>`
        + `<span class="popup-cat"><span class="swatch" style="background:${s.color};border-color:${outline(s.category)}"></span>ICA: ${esc(s.category_name)}</span><br>`
        + s.details.map(esc).join("<br>"))
      .addTo(stationLayer);
  }
  const pollenIcon = L.divIcon({ className: "", html: '<div class="pollen-marker">🌼</div>', iconSize: [22, 22], iconAnchor: [11, 11] });
  for (const p of data.pollen) {
    const age = p.age_days === 0 ? "de hoy" : `de hace ${p.age_days} días`;
    L.marker([p.lat, p.lon], { icon: pollenIcon, bubblingMouseEvents: false })
      .bindTooltip(`Polen · ${esc(p.station)}`)
      .bindPopup(`<b>Polen · ${esc(p.station)}</b><br><span class="popup-meta">Lectura del ${ddmm(p.reading_date)}, ${age}</span><br>`
        + p.types.map((t) => `${esc(t.name)}: ${num(t.value)} granos/m³ (${esc(t.level_name)})`).join("<br>"))
      .addTo(stationLayer);
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
    + "<li>la <b>calidad del aire</b> prevista para ese tramo (modelo europeo CAMS, vía Open-Meteo), con los mismos umbrales oficiales del ICA y tu perfil.</li></ul>"
    + "<p>El polen sale de nuestro propio modelo allí donde demostró ser fiable al probarlo con 2025. Donde no lo fue, se usa la previsión europea (solo gramíneas y olivo) o se indica «Sin previsión fiable».</p>"
    + "<p>La <b>confianza</b> de cada día (alta, media o baja) resume cuánto acertó la previsión en esa prueba. Toca cualquier casilla para ver el porqué.</p>" },
  hours: { title: "Mejores horas", html:
    "<p><b>Previsión</b> del modelo europeo (Open-Meteo), no mediciones oficiales.</p>"
    + "<p>Cada barra es una hora de las próximas 24, empezando por la actual, de día y de noche. La línea vertical marca la medianoche. La estrella ★ marca las mejores franjas de tres horas. En el móvil, desliza hacia la izquierda para ver más horas.</p>"
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

const infoIcon = (key, label) => ` <button class="info" type="button" data-info="${key}" aria-label="Más información: ${esc(label)}">i</button>`;

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
      const rain = block.weather && block.weather.rain_mm >= 0.1 ? "<small>💧</small>" : "";
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
        : p.kind === "european" ? '<span class="source-chip is-european">Previsión europea</span>'
        : p.kind === "measurement" ? '<span class="source-chip is-measured">Medición oficial</span>'
        : '<span class="source-chip is-none">Sin previsión fiable</span>';
      const accuracy = p.kind === "model" && p.accuracy != null ? ` Acertó el ${num(p.accuracy * 100)} % de los días de temporada en la prueba con 2025.` : "";
      const level = p.level_name ? `nivel <b>${esc(p.level_name)}</b>` : "sin nivel";
      const why = p.kind === "none" ? " Nuestro modelo no superó la prueba para este tipo y antelación, y la previsión europea no lo cubre."
        : p.kind === "european" ? " Nuestro modelo no superó la prueba para este tipo y antelación; se usa el modelo europeo CAMS (Open-Meteo)."
        : p.kind === "measurement" ? ` ${esc(p.source)}.` : "";
      return `<li><b>${esc(p.name)}</b>: ${level} ${chip}${accuracy}${why}</li>`;
    }).join("") + "</ul>";
  }

  html += "<h4>Calidad del aire (previsión para el tramo)</h4>";
  if (block.air) {
    const values = Object.entries(block.air.values).map(([p, v]) => `${esc(p)} ${num(v)}`).join(" · ");
    html += `<p>ICA previsto: <b>${esc(block.air.category_name)}</b> (por ${esc(block.air.pollutant)}, ${num(block.air.value)} µg/m³). `
      + `<span class="source-chip is-european">Previsión europea</span></p><p>Valores del tramo: ${values} µg/m³ (hora más alta para NO2, media del tramo para el resto). Modelo CAMS, vía Open-Meteo.</p>`
      + `<p>Recomendación oficial para esa categoría y tu perfil: «${esc(block.air.official_advice)}»</p>`;
  } else {
    html += "<p>La previsión de calidad del aire no llega hasta este tramo.</p>";
  }

  html += "<h4>Tiempo</h4>";
  html += block.weather
    ? `<p>${num(block.weather.temperature_c)} °C de media, ${block.weather.rain_mm >= 0.1 ? `lluvia prevista: ${num(block.weather.rain_mm, 1)} mm` : "sin lluvia prevista"}. Previsión de Open-Meteo.</p>`
    : "<p>Sin previsión del tiempo.</p>";

  html += `<h4>Confianza del día: ${esc(day.confidence)}</h4><p>${esc(day.confidence_reason)} Las previsiones son menos seguras cuanto más lejos está el día.</p>`;
  if (plannerData.demo) html += "<p>Vista del 24 de mayo de 2025: el polen es lo que nuestro modelo preveía con lo que se sabía ese día.</p>";
  return html;
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
