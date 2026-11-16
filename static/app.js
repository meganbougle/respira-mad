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
    refresh({ stations: true });
  });

  drawDemoLabel();
  window.addEventListener("resize", () => { drawDemoLabel(); drawStatus(); });

  // my location
  $("locate").addEventListener("click", useMyLocation);

  // legend of the hourly strip: colour + label (never colour alone)
  $("strip-legend").innerHTML = config.levels.map((l) => `<span><i style="background:${l.color}"></i>${esc(l.label)}</span>`).join("")
    + "<span>★ Mejor franja</span>";

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
  $("result-sub").textContent = `${risk.profile_label} · ${risk.place_label}`;
  $("result-reason").textContent = risk.reason;

  // official recommendation, word for word, inside the card, with its source in small print
  // The first text (the one behind the level) is always visible; a second one folds away to save space.
  const quote = (r) => `<blockquote class="quote"><p>${esc(r.text)}</p><cite>Texto oficial, literal. Fuente: ${esc(r.source)}.</cite></blockquote>`;
  const [first, ...others] = risk.recommendations;
  $("advice").innerHTML = (first ? quote(first) : "")
    + (others.length ? `<details class="more-advice"><summary>Ver también la recomendación de calidad del aire</summary>${others.map(quote).join("")}</details>` : "");
  $("profile-note").hidden = !risk.profile_note;
  $("profile-note").textContent = risk.profile_note || "";
  $("warnings").innerHTML = risk.notes.map((n) => `<div class="warning">${esc(n)}</div>`).join("");

  // pollutant tiles (official station measurements)
  $("tiles").innerHTML = risk.air.map((a) => `
    <div class="tile">
      <div class="tile-name"><span>${esc(a.pollutant)}</span>${a.provisional ? '<span class="tag tag-warn">Provisional</span>' : ""}</div>
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
  $("hours-title").textContent = `Mejores horas ${data.day}`;
  $("hours-kind").textContent = `${data.kind}.`;
  $("hour-value").textContent = `${state.hour}:00`;

  const strip = $("strip");
  if (!data.hours.length) {
    strip.innerHTML = "";
    $("hour-detail").textContent = "No hay previsión disponible para este punto.";
    $("windows").innerHTML = "";
    return;
  }
  // which hour to show the details of: the tapped one, or the first hour not yet gone
  const stillThere = data.hours.some((h) => h.hour === state.selectedHour);
  if (!stillThere) state.selectedHour = (data.hours.find((h) => !h.past) || data.hours[0]).hour;

  strip.innerHTML = data.hours.map((h) => `
    <button class="hour ${h.past ? "is-past" : ""} ${h.best ? "is-best" : ""} ${h.hour === state.selectedHour ? "is-selected" : ""}"
            type="button" data-hour="${h.hour}" style="--c:${config.levels[h.level_index].color}"
            aria-label="${h.hour}:00, ${esc(h.level_label)}${h.best ? ", mejor franja" : ""}">
      <span class="hour-bar"></span><span class="hour-num">${String(h.hour).padStart(2, "0")}</span>
    </button>`).join("");
  strip.onclick = (event) => {
    const button = event.target.closest(".hour");
    if (!button) return;
    state.selectedHour = Number(button.dataset.hour);
    for (const other of strip.children) other.classList.toggle("is-selected", other === button);
    drawHourDetail();
  };
  drawHourDetail();

  // best windows, as text
  let html = "";
  if (data.no_good_window) {
    html = `<p class="windows-title">No hay ninguna franja recomendable ${data.day}: todas las horas previstas están en «${esc(config.levels[2].label)}».</p>`;
  } else if (data.windows.length) {
    html = `<p class="windows-title">★ Mejores franjas ${data.day} (nivel «${esc(config.levels[data.windows[0].level_index].label)}»)</p><ul class="windows">`
      + data.windows.map((w) => `<li><b>${hhmm(w.start)}–${hhmm(w.end)}</b><span>${num(w.temperature_c)} °C · ${w.rain_mm === 0 ? "sin lluvia" : `lluvia ${num(w.rain_mm, 1)} mm`}</span></li>`).join("")
      + "</ul>";
  } else {
    html = '<p class="windows-title">No quedan horas suficientes para recomendar una franja.</p>';
  }
  $("windows").innerHTML = html;
}

function drawHourDetail() {
  const h = lastHours && lastHours.hours.find((x) => x.hour === state.selectedHour);
  if (!h) { $("hour-detail").textContent = ""; return; }
  const parts = [
    `NO2 ${num(h.NO2)}`, `O3 ${num(h.O3)}`, `PM10 ${num(h.PM10)}`, `PM2.5 ${num(h["PM2.5"])} µg/m³`,
    `${num(h.temperature_c)} °C`, h.rain_mm > 0 ? `lluvia ${num(h.rain_mm, 1)} mm` : "sin lluvia",
  ];
  if (state.allergic) parts.push(`gramíneas ${num(h.grass_pollen)} · olivo ${num(h.olive_pollen)} granos/m³`);
  $("hour-detail").innerHTML = `<b>${String(h.hour).padStart(2, "0")}:00 · ${esc(h.level_label)}</b>`
    + (h.limited_by ? ` (por ${esc(h.limited_by)})` : "") + (h.past ? " · hora ya pasada" : "")
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
