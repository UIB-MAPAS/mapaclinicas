/* ═══════════════════════════════════════════════════════════════════════════
   app.js — Mapa de Clínicas Afiliadas
   Broker de Seguros · Orientación de accidentados
   ═══════════════════════════════════════════════════════════════════════════ */

'use strict';

// ─── Configuración ──────────────────────────────────────────────────────────
const CONFIG = {
  mapCenter:      [-12.0464, -77.0428], // Lima, Perú
  mapZoom:        12,
  maxClinics:     5,
  geocodeDelay:   1200,  // ms entre llamadas a Nominatim (límite: 1 req/seg)
  nominatimUrl:   'https://nominatim.openstreetmap.org/search',
  country:        'Peru',
  cacheKey:       'geocache_mapa_clinicas_v1',
};

// ─── Colores por AASS ────────────────────────────────────────────────────────
const AASS_COLORS = {
  'SARA CORDERO':       '#27ae60', // verde
  'SHEYDA FLORINDEZ':   '#8e44ad', // morado
  'YEISLL DIAZ':        '#e67e22', // naranja
  'KATHERINE GUTIERREZ':'#2980b9', // azul
  'PAOLA MIJA':         '#f39c12', // amarillo
  'MARIAELENA QUIROZ':  '#2c3e50', // negro
  'GABRIELA VARIAS':    '#e91e8c', // rosado
  'ERENI VALDIVIA':     '#16a085', // verde azulado
};

const AASS_LABELS = {
  'SARA CORDERO':       'Sara Cordero',
  'SHEYDA FLORINDEZ':   'Sheyda Florindez',
  'YEISLL DIAZ':        'Yeisll Díaz',
  'KATHERINE GUTIERREZ':'Katherine Gutiérrez',
  'PAOLA MIJA':         'Paola Mija',
  'MARIAELENA QUIROZ':  'Mariaelena Quiroz',
  'GABRIELA VARIAS':    'Gabriela Varias',
  'ERENI VALDIVIA':     'Ereni Valdivia',
};

// ─── Estado global ───────────────────────────────────────────────────────────
let map;
let localesData   = [];
let clinicasData  = [];
let localeMarkers = L.layerGroup();   // group para toggle
let clinicaMarkers = [];              // marcadores temporales de clínicas
let accidentMarker = null;
let geocodeCache   = {};

// ─── Inicialización ──────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  initMap();
  loadCache();
  Promise.all([loadCSV('data/locales.csv'), loadCSV('data/clinicas.csv')])
    .then(([localesRaw, clinicasRaw]) => {
      localesData  = parseLocales(localesRaw);
      clinicasData = parseClinicas(clinicasRaw);
      setupMarkers();
    })
    .catch(err => {
      console.error(err);
      setStatus('Error cargando los archivos CSV: ' + err.message, 'error');
      hideLoading();
    });
});

// ─── Mapa ────────────────────────────────────────────────────────────────────
function initMap() {
  map = L.map('map', { zoomControl: true }).setView(CONFIG.mapCenter, CONFIG.mapZoom);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  localeMarkers.addTo(map);
}

// ─── Carga CSV genérica ──────────────────────────────────────────────────────
function loadCSV(path) {
  return new Promise((resolve, reject) => {
    Papa.parse(path, {
      download: true,
      header: true,
      skipEmptyLines: true,
      complete: results => resolve(results.data),
      error: err => reject(new Error(`No se pudo leer ${path}: ${err.message}`)),
    });
  });
}

// ─── Mapeo tipo accidente → tipos de clínica compatibles ─────────────────────
const ACCIDENTE_TIPOS = {
  'quemadura':     { label: 'Quemadura',                  emoji: '🔥', prioridad: ['CLINICA_QUEMADURA'],  compatibles: ['CLINICA_QUEMADURA', 'CLINICA_GENERAL', 'AMD'] },
  'corte':         { label: 'Corte / Herida',             emoji: '🩹', prioridad: ['CLINICA_GENERAL'],    compatibles: ['CLINICA_GENERAL', 'CLINICA_QUEMADURA', 'AMD'] },
  'fractura':      { label: 'Fractura / Golpe',           emoji: '🦴', prioridad: ['CLINICA_GENERAL'],    compatibles: ['CLINICA_GENERAL', 'MED_FISIOTERAPIA', 'AMD'] },
  'traumatismo':   { label: 'Traumatismo',                emoji: '⚠️', prioridad: ['CLINICA_GENERAL'],    compatibles: ['CLINICA_GENERAL', 'AMD'] },
  'rehabilitacion':{ label: 'Rehabilitación / Fisioterapia', emoji: '💪', prioridad: ['MED_FISIOTERAPIA'],compatibles: ['MED_FISIOTERAPIA', 'CLINICA_GENERAL'] },
  'diagnostico':   { label: 'Diagnóstico / Imágenes',     emoji: '🔬', prioridad: ['APOYO_DX'],           compatibles: ['APOYO_DX', 'CLINICA_GENERAL'] },
  'dental':        { label: 'Dental',                     emoji: '🦷', prioridad: ['ODONTOLOGIA'],         compatibles: ['ODONTOLOGIA', 'CLINICA_GENERAL'] },
  'ocular':        { label: 'Ocular / Oftalmología',      emoji: '👁',  prioridad: ['OFTALMOLOGIA'],        compatibles: ['OFTALMOLOGIA', 'CLINICA_GENERAL'] },
  'otro':          { label: 'Otro / General',             emoji: '🏥', prioridad: ['CLINICA_GENERAL'],    compatibles: ['CLINICA_GENERAL', 'CLINICA_QUEMADURA', 'MED_FISIOTERAPIA', 'APOYO_DX', 'ODONTOLOGIA', 'AMD', 'OFTALMOLOGIA'] },
};

const TIPO_BADGE = {
  'CLINICA_QUEMADURA': { css: 'badge-quemadura', label: 'Quemaduras' },
  'CLINICA_GENERAL':   { css: 'badge-general',   label: 'Clínica general' },
  'MED_FISIOTERAPIA':  { css: 'badge-rehab',      label: 'Rehabilitación' },
  'APOYO_DX':          { css: 'badge-dx',         label: 'Diagnóstico' },
  'ODONTOLOGIA':       { css: 'badge-odonto',     label: 'Odontología' },
  'AMD':               { css: 'badge-amd',         label: 'Atención médica domiciliaria' },
  'OFTALMOLOGIA':      { css: 'badge-oftalmo',    label: 'Oftalmología' },
};

// ─── Parseo de locales ───────────────────────────────────────────────────────
function parseLocales(rows) {
  return rows.map(r => ({
    referencia: trim(r['Referencia']),
    provincia:  trim(r['Provincia']),
    distrito:   trim(r['Distrito']),
    tipoVia:    trim(r['TipoVia']),
    nombreVia:  trim(r['NombreVia']),
    aass:       trim(r['AASS']).toUpperCase(),
    // Coordenadas opcionales pre-geocodificadas
    lat: r['Lat'] ? parseFloat(r['Lat']) : null,
    lng: r['Lng'] ? parseFloat(r['Lng']) : null,
  }));
}

function parseClinicas(rows) {
  return rows.map(r => ({
    nombre:    trim(r['Nombre']),
    tipo:      trim(r['Tipo']) || 'CLINICA_GENERAL',
    direccion: trim(r['Direccion']),
    distrito:  trim(r['Distrito']),
    telefono:  trim(r['Telefono']),
    lat: r['Lat'] ? parseFloat(r['Lat']) : null,
    lng: r['Lng'] ? parseFloat(r['Lng']) : null,
  }));
}

function trim(v) { return (v || '').trim(); }

// ─── Geocodificación con Nominatim ───────────────────────────────────────────
function loadCache() {
  try {
    geocodeCache = JSON.parse(localStorage.getItem(CONFIG.cacheKey) || '{}');
  } catch { geocodeCache = {}; }
}

function saveCache() {
  try { localStorage.setItem(CONFIG.cacheKey, JSON.stringify(geocodeCache)); } catch {}
}

/** Construye la cadena de búsqueda para un local */
function buildLocaleQuery(locale) {
  const parts = [locale.tipoVia, locale.nombreVia, locale.distrito, locale.provincia, CONFIG.country]
    .filter(Boolean);
  return parts.join(', ');
}

/** Construye la cadena de búsqueda para una clínica */
function buildClinicaQuery(clinica) {
  return [clinica.direccion, clinica.distrito, CONFIG.country].filter(Boolean).join(', ');
}

/**
 * Geocodifica una dirección usando Nominatim.
 * Respeta el límite de 1 petición/segundo mediante cola.
 */
async function geocodeAddress(query) {
  if (geocodeCache[query]) return geocodeCache[query];

  const url = `${CONFIG.nominatimUrl}?q=${encodeURIComponent(query)}&format=json&limit=1&countrycodes=pe`;
  const controller = new AbortController();
  const timeoutId  = setTimeout(() => controller.abort(), 10000); // 10 s máx

  try {
    const resp = await fetch(url, {
      signal:  controller.signal,
      headers: {
        'Accept-Language': 'es',
        'User-Agent':      'MapaClinicasBroker/1.0 (uibmapa@gmail.com)',
      },
    });
    clearTimeout(timeoutId);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    if (data && data.length > 0) {
      const result = { lat: parseFloat(data[0].lat), lng: parseFloat(data[0].lon) };
      geocodeCache[query] = result;
      saveCache();
      return result;
    }
    return null;
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      console.warn('Geocoding timeout (>10 s) for:', query);
    } else {
      console.warn('Geocoding failed for:', query, err);
    }
    return null;
  }
}

/** Retraso para respetar el rate-limit de Nominatim */
function delay(ms) { return new Promise(res => setTimeout(res, ms)); }

/**
 * Agrega todos los marcadores al mapa desde los datos ya pre-geocodificados en el CSV.
 * No hace llamadas a Nominatim — todo viene del CSV.
 * Locales sin Lat/Lng (imprecisos) simplemente no se muestran.
 */
function setupMarkers() {
  const localesCon    = localesData.filter(l => l.lat && l.lng).length;
  const localesSin    = localesData.length - localesCon;
  const clinicasCon   = clinicasData.filter(c => c.lat && c.lng).length;

  localesData.forEach(addLocaleMarker);
  buildLegend();
  hideLoading();

  const msgLocales = localesSin > 0
    ? `${localesCon} locales visibles (${localesSin} sin ubicación precisa omitidos)`
    : `${localesCon} locales`;

  setStatus(`${msgLocales} · ${clinicasCon} clínicas listas.`, 'success');
  setTimeout(clearStatus, 5000);
}

// ─── Marcadores de locales ───────────────────────────────────────────────────
function getAassColor(aass) {
  return AASS_COLORS[aass] || '#607d8b';
}

/** Icono SVG personalizado tipo pin */
function createPinIcon(color) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="36" viewBox="0 0 28 36">
    <path d="M14 0C6.27 0 0 6.27 0 14c0 9.33 14 22 14 22S28 23.33 28 14C28 6.27 21.73 0 14 0z"
      fill="${color}" stroke="#fff" stroke-width="2"/>
    <circle cx="14" cy="14" r="5" fill="#fff" opacity=".85"/>
  </svg>`;
  return L.divIcon({
    className: '',
    html: svg,
    iconSize:   [28, 36],
    iconAnchor: [14, 36],
    popupAnchor:[0, -36],
  });
}

/** Icono para clínicas cercanas (cuadrado numerado, color configurable) */
function createClinicIcon(rank, color = '#1e40af') {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="30" height="36" viewBox="0 0 30 36">
    <rect x="1" y="1" width="28" height="28" rx="6" fill="${color}" stroke="#fff" stroke-width="2"/>
    <text x="15" y="21" text-anchor="middle" fill="#fff" font-size="15" font-weight="bold"
      font-family="system-ui,sans-serif">${rank}</text>
    <polygon points="15,35 9,28 21,28" fill="${color}"/>
  </svg>`;
  return L.divIcon({
    className: '',
    html: svg,
    iconSize:   [30, 36],
    iconAnchor: [15, 36],
    popupAnchor:[0, -36],
  });
}

/** Icono rojo para el punto del accidente */
function createAccidentIcon() {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="34" height="42" viewBox="0 0 34 42">
    <path d="M17 0C7.61 0 0 7.61 0 17c0 11.2 17 25 17 25S34 28.2 34 17C34 7.61 26.39 0 17 0z"
      fill="#e74c3c" stroke="#fff" stroke-width="2.5"/>
    <text x="17" y="23" text-anchor="middle" fill="#fff" font-size="18" font-weight="bold"
      font-family="system-ui,sans-serif">!</text>
  </svg>`;
  return L.divIcon({
    className: '',
    html: svg,
    iconSize:   [34, 42],
    iconAnchor: [17, 42],
    popupAnchor:[0, -42],
  });
}

function addLocaleMarker(locale) {
  if (!locale.lat || !locale.lng) return;

  const color    = getAassColor(locale.aass);
  const aassName = AASS_LABELS[locale.aass] || locale.aass;
  const address  = `${locale.tipoVia} ${locale.nombreVia}, ${locale.distrito}`;

  const marker = L.marker([locale.lat, locale.lng], { icon: createPinIcon(color) });

  marker.bindPopup(`
    <div class="popup-title">${locale.referencia}</div>
    <div class="popup-row">📍 ${address}</div>
    <div class="popup-row">🏙 ${locale.distrito}, ${locale.provincia}</div>
    <span class="popup-aass" style="background:${color}">${aassName}</span>
  `);

  localeMarkers.addLayer(marker);
}

// ─── Leyenda ─────────────────────────────────────────────────────────────────
function buildLegend() {
  const container = document.getElementById('legend-items');
  const seen = new Set();

  // Solo AASS que tienen al menos un local geocodificado
  localesData.forEach(l => { if (l.lat) seen.add(l.aass); });

  if (!seen.size) {
    container.innerHTML = '<div style="font-size:11px;color:#9ca3af">Sin locales geocodificados</div>';
    return;
  }

  container.innerHTML = [...seen].map(aass => `
    <div class="legend-item">
      <span class="legend-dot" style="background:${getAassColor(aass)}"></span>
      <span>${AASS_LABELS[aass] || aass}</span>
    </div>
  `).join('');
}

// ─── Toggle locales ───────────────────────────────────────────────────────────
function toggleLocales(visible) {
  if (visible) {
    localeMarkers.addTo(map);
  } else {
    localeMarkers.remove();
  }
}

// ─── Búsqueda de accidente ────────────────────────────────────────────────────
async function searchAccident() {
  const input        = document.getElementById('search-input').value.trim();
  const accidenteTipo = document.getElementById('accident-type').value;

  if (!input) {
    setStatus('Por favor ingresa el lugar del accidente.', 'error');
    return;
  }

  const btn = document.getElementById('btn-search');
  btn.disabled = true;
  setStatus('Buscando ubicación…');
  clearClinicMarkers();
  hideResults();

  // Intento 1: query tal como la ingresó el usuario + Perú (cubre provincias)
  let coords = await geocodeAddress(`${input}, ${CONFIG.country}`);

  // Intento 2: agregando Lima (útil para búsquedas cortas sin distrito)
  if (!coords) {
    coords = await geocodeAddress(`${input}, Lima, ${CONFIG.country}`);
  }

  if (!coords) {
    setStatus('No se encontró el lugar. Intentá ser más específico: "KFC Miraflores", "av larco miraflores" o el nombre completo.', 'error');
    btn.disabled = false;
    return;
  }

  // Marcador del accidente
  if (accidentMarker) map.removeLayer(accidentMarker);
  const tipoLabel = accidenteTipo && ACCIDENTE_TIPOS[accidenteTipo]
    ? ` · ${ACCIDENTE_TIPOS[accidenteTipo].emoji} ${ACCIDENTE_TIPOS[accidenteTipo].label}`
    : '';
  accidentMarker = L.marker([coords.lat, coords.lng], { icon: createAccidentIcon(), zIndexOffset: 1000 })
    .addTo(map)
    .bindPopup(`<b>⚠ Accidente</b><br><span style="font-size:12px">${input}${tipoLabel}</span>`)
    .openPopup();

  map.setView([coords.lat, coords.lng], 14, { animate: true });

  // Calcular clínicas
  if (!clinicasData.some(c => c.lat)) {
    setStatus('No hay clínicas geocodificadas. Recarga la página.', 'error');
    btn.disabled = false;
    return;
  }

  const { recomendadas, cercanas } = findNearestClinics(coords.lat, coords.lng, accidenteTipo);
  displayNearestClinics(recomendadas, cercanas, input, accidenteTipo);
  addClinicMarkers(recomendadas, cercanas);

  btn.disabled = false;
  clearStatus();
}

// ─── Fórmula de Haversine ─────────────────────────────────────────────────────
function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371; // km
  const toRad = deg => deg * Math.PI / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Retorna { recomendadas, cercanas }
 * - recomendadas: top 3 clínicas que coinciden con el tipo prioritario del accidente
 * - cercanas:     top 5 clínicas compatibles (excluye las recomendadas)
 * Si no hay tipo de accidente, recomendadas=[], cercanas=top 5 todas.
 */
function findNearestClinics(lat, lng, accidenteTipo) {
  const withDist = clinicasData
    .filter(c => c.lat && c.lng)
    .map(c => ({ ...c, distancia: haversine(lat, lng, c.lat, c.lng) }))
    .sort((a, b) => a.distancia - b.distancia);

  if (!accidenteTipo || !ACCIDENTE_TIPOS[accidenteTipo]) {
    return { recomendadas: [], cercanas: withDist.slice(0, CONFIG.maxClinics) };
  }

  const { prioridad, compatibles } = ACCIDENTE_TIPOS[accidenteTipo];

  // Recomendadas: tipo prioritario (max 3)
  const recomendadas = withDist
    .filter(c => prioridad.includes(c.tipo))
    .slice(0, 3);

  const recomIds = new Set(recomendadas.map(c => c.nombre + c.direccion));

  // Cercanas: tipos compatibles, sin duplicar recomendadas (max 5)
  const cercanas = withDist
    .filter(c => compatibles.includes(c.tipo) && !recomIds.has(c.nombre + c.direccion))
    .slice(0, CONFIG.maxClinics);

  return { recomendadas, cercanas };
}

// ─── Marcadores de clínicas ───────────────────────────────────────────────────
function addClinicMarkers(recomendadas, cercanas) {
  // Cada sección numera desde 1, igual que la lista en el sidebar
  const addGroup = (list, color, zOffset) => {
    (list || []).forEach((c, i) => {
      const badgeInfo = TIPO_BADGE[c.tipo] || TIPO_BADGE['CLINICA_GENERAL'];
      const m = L.marker([c.lat, c.lng], {
        icon: createClinicIcon(i + 1, color),
        zIndexOffset: zOffset,
      })
        .addTo(map)
        .bindPopup(`
          <div class="popup-title">🏥 ${c.nombre}</div>
          <div class="popup-row"><span class="clinic-badge ${badgeInfo.css}">${badgeInfo.label}</span></div>
          <div class="popup-row">📍 ${c.direccion}, ${c.distrito}</div>
          <div class="popup-row">📞 ${c.telefono}</div>
          <div class="popup-row" style="font-weight:700;color:#059669">${c.distancia.toFixed(2)} km del accidente</div>
        `);
      clinicaMarkers.push(m);
    });
  };

  addGroup(recomendadas, '#d97706', 950); // dorado, encima
  addGroup(cercanas,     '#1e40af', 900); // azul
}

function clearClinicMarkers() {
  clinicaMarkers.forEach(m => map.removeLayer(m));
  clinicaMarkers = [];
}

// ─── Mostrar resultados ───────────────────────────────────────────────────────
function clinicHTML(c, rank, isRecomendada) {
  const badgeInfo = TIPO_BADGE[c.tipo] || TIPO_BADGE['CLINICA_GENERAL'];
  return `
    <div class="clinic-item${isRecomendada ? ' recommended-item' : ''}" onclick="flyToClinic(${c.lat}, ${c.lng})">
      <div class="clinic-header">
        <span class="clinic-rank">${rank}</span>
        <span class="clinic-name">${c.nombre}</span>
        <span class="clinic-distance">${c.distancia.toFixed(2)} km</span>
      </div>
      <div style="margin-bottom:4px">
        <span class="clinic-badge ${badgeInfo.css}">${badgeInfo.label}</span>
      </div>
      <div class="clinic-detail">📍 ${c.direccion}, ${c.distrito}</div>
      <div class="clinic-phone">📞 ${c.telefono}</div>
      <a class="btn-route"
        href="https://www.google.com/maps/dir/?api=1&destination=${c.lat},${c.lng}"
        target="_blank" rel="noopener">
        🗺 Abrir en Google Maps
      </a>
    </div>
  `;
}

function displayNearestClinics(recomendadas, cercanas, addressLabel, accidenteTipo) {
  const container = document.getElementById('results-container');
  const info      = document.getElementById('accident-info');

  const tipoInfo  = accidenteTipo && ACCIDENTE_TIPOS[accidenteTipo];
  const tipoLabel = tipoInfo ? ` · ${tipoInfo.emoji} ${tipoInfo.label}` : '';

  info.innerHTML = `<strong>⚠ Accidente reportado en:</strong>${addressLabel}${tipoLabel}`;

  // Sección recomendadas
  const recSection = document.getElementById('recommended-section');
  const recTitle   = document.getElementById('recommended-title');
  const recList    = document.getElementById('recommended-list');

  if (tipoInfo && recomendadas.length > 0) {
    recTitle.textContent = `Recomendadas para ${tipoInfo.emoji} ${tipoInfo.label}`;
    recList.innerHTML    = recomendadas.map((c, i) => clinicHTML(c, i + 1, true)).join('');
    recSection.style.display = 'block';
  } else {
    recSection.style.display = 'none';
  }

  // Sección cercanas
  const nearbyTitle = document.getElementById('nearby-title');
  const clinicsList = document.getElementById('clinics-list');
  nearbyTitle.textContent = tipoInfo && recomendadas.length > 0
    ? 'Otras clínicas compatibles'
    : 'Clínicas más cercanas';
  clinicsList.innerHTML   = cercanas.map((c, i) => clinicHTML(c, i + 1, false)).join('');

  container.classList.add('show');
  container.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function hideResults() {
  document.getElementById('results-container').classList.remove('show');
}

/** Vuela al marcador de la clínica al hacer clic en el resultado */
function flyToClinic(lat, lng) {
  map.flyTo([lat, lng], 16, { animate: true, duration: 1 });
  // Busca el marcador por lat Y lng para no abrir el popup equivocado
  const match = clinicaMarkers.find(m =>
    Math.abs(m.getLatLng().lat - lat) < 0.0001 &&
    Math.abs(m.getLatLng().lng - lng) < 0.0001
  );
  if (match) match.openPopup();
}

// ─── UI helpers ───────────────────────────────────────────────────────────────
function setLoading(text, progress) {
  document.getElementById('loading-overlay').style.display = 'flex';
  document.getElementById('loading-text').textContent     = text;
  document.getElementById('loading-progress').textContent = progress;
}

function hideLoading() {
  document.getElementById('loading-overlay').style.display = 'none';
}

function setStatus(msg, type = 'info') {
  const bar = document.getElementById('status-bar');
  bar.textContent  = msg;
  bar.className    = `show ${type === 'error' ? 'error' : type === 'success' ? 'success' : ''}`;
}

function clearStatus() {
  const bar = document.getElementById('status-bar');
  bar.className = '';
}

// Permite buscar presionando Enter
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('search-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') searchAccident();
  });
});
