// Lviv Transit Control — Live Operations Monitor

const LVIV_CENTER = [24.0315, 49.8397];
const MAX_AGE_SECONDS = 20 * 60; // Strictly 20 minutes (1200s) from channel message post time
let CARTO_API_KEY = "";
let map = null;
let currentEventSource = null;

function buildDualThemeStyle(initialTheme, apiKey = CARTO_API_KEY) {
  const qs = apiKey ? `?key=${encodeURIComponent(apiKey)}` : "";
  const isDark = initialTheme === "dark";
  return {
    version: 8,
    sources: {
      "carto-dark": {
        type: "raster",
        tiles: [
          `https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png${qs}`,
          `https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png${qs}`,
          `https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png${qs}`,
          `https://d.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png${qs}`
        ],
        tileSize: 256,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>'
      },
      "carto-light": {
        type: "raster",
        tiles: [
          `https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png${qs}`,
          `https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png${qs}`,
          `https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png${qs}`,
          `https://d.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png${qs}`
        ],
        tileSize: 256,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>'
      }
    },
    layers: [
      {
        id: "carto-dark-layer",
        type: "raster",
        source: "carto-dark",
        layout: { visibility: isDark ? "visible" : "none" },
        minzoom: 0,
        maxzoom: 20
      },
      {
        id: "carto-light-layer",
        type: "raster",
        source: "carto-light",
        layout: { visibility: isDark ? "none" : "visible" },
        minzoom: 0,
        maxzoom: 20
      }
    ]
  };
}

const state = {
  sightings: [],
  vehicles: [],
  vehicleAnimMap: new Map(), // vehicle_id -> { fromLon, fromLat, toLon, toLat, startTime, duration, props }
  routes: new Map(),
  markers: new Map(),        // sighting_id -> { marker, el }
  registeredBadges: new Set(),
  selectedSightingId: null,
  selectedRouteId: null,
  routeFilterText: localStorage.getItem("lviv_route_filter") || "",
  transportTab: "all",
  showAllVehicles: true,
  activePopup: null,
  activePopupSightingId: null,
  isAdmin: false,            // Strict read-only mode by default; only true when authenticated as admin
  mapLoaded: false
};

const colorSchemeMeta = document.querySelector('meta[name="color-scheme"]');
const darkMediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

function getRenderedScheme() {
  const stored = localStorage.getItem("color-scheme");
  return stored || (darkMediaQuery.matches ? "dark" : "light");
}

function updateAdminUI() {
  const reportBtn = document.getElementById("report-btn");
  const adminBtn = document.getElementById("admin-btn");
  if (reportBtn) {
    reportBtn.classList.toggle("hidden", !state.isAdmin);
  }
  if (adminBtn) {
    adminBtn.classList.toggle("active", state.isAdmin);
    adminBtn.title = state.isAdmin
      ? "Режим адміністратора активний (натисніть для виходу)"
      : "Вхід адміністратора";
  }
  renderSightings();
  if (state.activePopup && state.activePopupSightingId) {
    const s = state.sightings.find((item) => item.id === state.activePopupSightingId);
    if (s) focusSighting(s);
  }
}

// Format vehicle label with А / Т / ТР prefix depending on transport type
function formatVehicleLabel(routeShortName, transportType) {
  const rawNum = (routeShortName || "")
    .replace(/^(А|A|Тр|ТР|TR|Т|T)0*/i, "")
    .trim();
  const num = rawNum || "?";
  if (transportType === "tram") return `Т${num}`;
  if (transportType === "trolleybus") return `ТР${num}`;
  if (transportType === "bus") return `А${num}`;
  if ((routeShortName || "").startsWith("Тр")) return `ТР${num}`;
  if ((routeShortName || "").startsWith("Т")) return `Т${num}`;
  return `А${num}`;
}

function getDangerColorHex(ageClass) {
  if (ageClass === "age-fresh") return "#dc2626";  // 0-5m Red
  if (ageClass === "age-recent") return "#ea580c"; // 5-12m Orange
  if (ageClass === "age-aging") return "#eab308";  // 12-20m Yellow
  return null;
}

// Generate sharp technical rectangular badge icon for each transport dot
// When a vehicle has an active warning, its badge border & glow match the danger level color
function ensureVehicleBadgeImage(label, transportType, dangerAgeClass = "") {
  if (!map) return "";
  const badgeId = `v3_${transportType}_${label}_${dangerAgeClass || "norm"}`;
  if (state.registeredBadges.has(badgeId) && map.hasImage(badgeId)) {
    return badgeId;
  }

  const dangerHex = getDangerColorHex(dangerAgeClass);
  const bgColor = dangerHex
    ? dangerHex
    : transportType === "tram"
    ? "#b91c1c"
    : transportType === "trolleybus"
    ? "#0369a1"
    : "#15803d";

  const fontSize = 16;
  const padX = 8;
  const height = 28;
  const textWidth = Math.max(18, label.length * 10);
  const width = textWidth + padX * 2;

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");

  ctx.beginPath();
  ctx.roundRect(1.5, 1.5, width - 3, height - 3, 5);
  ctx.fillStyle = bgColor;
  ctx.fill();

  ctx.lineWidth = dangerHex ? 3 : 1.5;
  ctx.strokeStyle = dangerHex ? "#ffffff" : "rgba(255, 255, 255, 0.85)";
  ctx.stroke();

  ctx.font = `700 ${fontSize}px "JetBrains Mono", Consolas, monospace`;
  ctx.fillStyle = dangerAgeClass === "age-aging" ? "#09090b" : "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, width / 2, height / 2 + 0.5);

  const imgData = ctx.getImageData(0, 0, width, height);
  try {
    if (!map.hasImage(badgeId)) {
      map.addImage(badgeId, imgData, { pixelRatio: 2 });
    }
    state.registeredBadges.add(badgeId);
  } catch (e) {
    // Ignore duplicate image error
  }
  return badgeId;
}

function syncVehiclesLayerVisibility() {
  if (!map || !state.mapLoaded) return;
  const vis = state.showAllVehicles ? "visible" : "none";
  if (map.getLayer("vehicles-symbol")) {
    map.setLayoutProperty("vehicles-symbol", "visibility", vis);
  }
  if (map.getLayer("vehicles-inspector-ring")) {
    map.setLayoutProperty("vehicles-inspector-ring", "visibility", vis);
  }
}

function setupMapLayers() {
  if (!map) return;

  try {
    if (!map.getSource("selected-route")) {
      map.addSource("selected-route", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "selected-route-casing",
        type: "line",
        source: "selected-route",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: {
          "line-color": "#09090b",
          "line-width": 8,
          "line-opacity": 0.85,
        },
      });

      map.addLayer({
        id: "selected-route-line",
        type: "line",
        source: "selected-route",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: {
          "line-color": ["coalesce", ["get", "color"], "#3b82f6"],
          "line-width": 4.5,
          "line-opacity": 1.0,
        },
      });
    }

    if (!map.getSource("live-vehicles")) {
      map.addSource("live-vehicles", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "vehicles-inspector-ring",
        type: "circle",
        source: "live-vehicles",
        filter: ["==", ["get", "has_inspectors"], true],
        paint: {
          "circle-radius": 20,
          "circle-color": ["coalesce", ["get", "danger_fill"], "rgba(220, 38, 38, 0.28)"],
          "circle-stroke-width": 2.5,
          "circle-stroke-color": ["coalesce", ["get", "danger_color"], "#dc2626"],
        },
      });

      map.addLayer({
        id: "vehicles-symbol",
        type: "symbol",
        source: "live-vehicles",
        layout: {
          "icon-image": ["get", "badge_icon"],
          "icon-size": [
            "interpolate",
            ["linear"],
            ["zoom"],
            10, 0.72,
            12.5, 0.88,
            15, 1.05
          ],
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
      });

      map.on("click", "vehicles-symbol", async (e) => {
        if (!e.features || !e.features.length) return;
        const feat = e.features[0];
        const props = feat.properties;
        const coords = feat.geometry.coordinates.slice();

        const routeIdToDraw =
          props.route_id || findMatchingRouteId(props.route_short_name, props.transport_type);
        await loadAndDrawRoute(routeIdToDraw);

        const typeLabel =
          props.transport_type === "tram" ? "ТРАМВАЙ" :
          props.transport_type === "trolleybus" ? "ТРОЛЕЙБУС" : "АВТОБУС";

        const dColor = escapeHtml(props.danger_color || "#dc2626");
        const inspectorBanner = props.has_inspectors
          ? `<div style="margin-top:8px;padding:5px 7px;border-radius:3px;border:1px solid ${dColor};background:rgba(220,38,38,0.14);color:${dColor};font-family:var(--font-mono);font-weight:700;font-size:0.70rem;">УВАГА: КОНТРОЛЬ НА БОРТУ</div>`
          : "";

        new maplibregl.Popup({ offset: 12 })
          .setLngLat(coords)
          .setHTML(`
            <div>
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-family:var(--font-mono);font-weight:700;font-size:0.82rem;">${typeLabel} // ${escapeHtml(props.short_label || "")}</span>
                <span style="font-family:var(--font-mono);font-size:0.70rem;color:var(--text-muted);">ID #${escapeHtml(props.vehicle_id)}</span>
              </div>
              <div style="font-family:var(--font-mono);color:var(--text-secondary);font-size:0.72rem;margin-top:4px;">ШВИДКІСТЬ: ${Math.round((Number(props.speed) || 0) * 3.6)} КМ/ГОД</div>
              ${inspectorBanner}
            </div>
          `)
          .addTo(map);
      });

      map.on("mouseenter", "vehicles-symbol", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "vehicles-symbol", () => {
        map.getCanvas().style.cursor = "";
      });
    }

    state.mapLoaded = true;
    syncVehiclesLayerVisibility();
    updateVehiclesGeoJSON(performance.now());
    renderSightings();
    if (state.routeFilterText) {
      highlightRouteFromSearch(state.routeFilterText);
    }
  } catch (err) {
    console.warn("setupMapLayers warning:", err);
  }
}

function getVehicleCurrentCoords(vehicleId, now = performance.now()) {
  const anim = state.vehicleAnimMap.get(vehicleId);
  if (!anim) return null;
  const t = Math.min(1, Math.max(0, (now - anim.startTime) / anim.duration));
  const lon = anim.fromLon + (anim.toLon - anim.fromLon) * t;
  const lat = anim.fromLat + (anim.toLat - anim.fromLat) * t;
  return [lon, lat];
}

// Normalize incoming sightings so age_seconds ticks smoothly every second
// and snapped vehicle coordinates never revert to initial stop position
function prepareSightingsList(rawList) {
  const nowMs = Date.now();
  const perfNow = performance.now();
  return (rawList || [])
    .filter((s) => s.status !== "cleared")
    .map((s) => {
      const sentMs = s.posted_at ? new Date(s.posted_at).getTime() : NaN;
      let baseAge = typeof s.age_seconds === "number" ? s.age_seconds : 0;
      if (!isNaN(sentMs)) {
        const calcAge = Math.floor((nowMs - sentMs) / 1000);
        if (calcAge >= 0 && calcAge < 86400) {
          baseAge = calcAge;
        }
      }
      let lon = s.lon;
      let lat = s.lat;
      if (s.matched_vehicle_id) {
        const liveCoords = getVehicleCurrentCoords(s.matched_vehicle_id, perfNow);
        if (liveCoords) {
          lon = liveCoords[0];
          lat = liveCoords[1];
        }
      }
      return {
        ...s,
        lon,
        lat,
        _baseAge: Math.max(0, baseAge),
        _receivedAt: nowMs,
        age_seconds: Math.max(0, baseAge),
      };
    });
}

function getCurrentSightingAge(s) {
  if (typeof s._baseAge === "number" && typeof s._receivedAt === "number") {
    return Math.max(0, s._baseAge + Math.floor((Date.now() - s._receivedAt) / 1000));
  }
  if (s.posted_at) {
    const sentMs = new Date(s.posted_at).getTime();
    if (!isNaN(sentMs)) {
      return Math.max(0, Math.floor((Date.now() - sentMs) / 1000));
    }
  }
  return s.age_seconds || 0;
}

function formatSentClockTime(postedAtIso) {
  if (!postedAtIso) return "--:--";
  const d = new Date(postedAtIso);
  if (isNaN(d.getTime())) return "--:--";
  return d.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
}

// Live MM:SS timer string (e.g. 00:18, 04:35)
function formatElapsedTimer(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const mins = String(Math.floor(total / 60)).padStart(2, "0");
  const secs = String(total % 60).padStart(2, "0");
  return `${mins}:${secs}`;
}

function getAgeClass(seconds) {
  const mins = seconds / 60;
  if (mins < 5) return "age-fresh";
  if (mins < 12) return "age-recent";
  return "age-aging";
}

function normalizeRouteFilter(str) {
  return (str || "").trim().toLowerCase().replace(/^(а|a|т|t|тр|tr|тм)0*/i, "");
}

function findMatchingRouteId(routeShortName, transportType = null) {
  if (!routeShortName) return null;
  const targetNum = normalizeRouteFilter(routeShortName);
  if (!targetNum) return null;

  for (const r of state.routes.values()) {
    if (transportType && transportType !== "all" && transportType !== "in_vehicle" && r.transport_type !== transportType) {
      continue;
    }
    if (normalizeRouteFilter(r.short_name) === targetNum) {
      return r.id;
    }
  }
  return null;
}

function highlightRouteFromSearch(rawQuery) {
  const q = (rawQuery || "").trim();
  if (!q) {
    loadAndDrawRoute(null);
    return;
  }

  const lower = q.toLowerCase();
  let explicitType = null;
  if (/^(тр|tr)/i.test(lower)) explicitType = "trolleybus";
  else if (/^(т|t)/i.test(lower)) explicitType = "tram";
  else if (/^(а|a)/i.test(lower)) explicitType = "bus";

  const numPart = normalizeRouteFilter(q);
  if (!numPart) {
    loadAndDrawRoute(null);
    return;
  }

  const preferredType =
    explicitType ||
    (state.transportTab !== "all" && state.transportTab !== "in_vehicle" ? state.transportTab : null);

  let exactMatchId = findMatchingRouteId(numPart, preferredType);
  if (!exactMatchId && preferredType) {
    exactMatchId = findMatchingRouteId(numPart, null);
  }
  loadAndDrawRoute(exactMatchId);
}

function matchesFilters(item, isVehicle = false) {
  if (state.transportTab === "in_vehicle") {
    if (isVehicle) {
      const matchedIds = new Set(state.sightings.map((s) => s.matched_vehicle_id).filter(Boolean));
      if (!matchedIds.has(item.vehicle_id)) return false;
    } else {
      if (item.action !== "in_vehicle" && !item.matched_vehicle_id) return false;
    }
  } else if (state.transportTab !== "all") {
    if (item.transport_type !== state.transportTab) return false;
  }

  if (state.routeFilterText) {
    const q = normalizeRouteFilter(state.routeFilterText);
    const rShort = normalizeRouteFilter(item.route_short_name || "");
    if (!rShort || !rShort.includes(q)) return false;
  }
  return true;
}

function ingestVehiclesBatch(vehicles) {
  state.vehicles = vehicles;
  const countEl = document.getElementById("live-vehicles-count");
  if (countEl) countEl.textContent = vehicles.length;

  const now = performance.now();
  const seenIds = new Set();

  for (const v of vehicles) {
    seenIds.add(v.vehicle_id);
    const existing = state.vehicleAnimMap.get(v.vehicle_id);
    const shortClean = formatVehicleLabel(v.route_short_name, v.transport_type);

    if (existing) {
      const progress = Math.min(1, (now - existing.startTime) / existing.duration);
      const curLon = existing.fromLon + (existing.toLon - existing.fromLon) * progress;
      const curLat = existing.fromLat + (existing.toLat - existing.fromLat) * progress;
      existing.fromLon = curLon;
      existing.fromLat = curLat;
      existing.toLon = v.lon;
      existing.toLat = v.lat;
      existing.startTime = now;
      existing.duration = 6800;
      existing.props = { ...v, short_label: shortClean };
    } else {
      state.vehicleAnimMap.set(v.vehicle_id, {
        fromLon: v.lon,
        fromLat: v.lat,
        toLon: v.lon,
        toLat: v.lat,
        startTime: now,
        duration: 6800,
        props: { ...v, short_label: shortClean },
      });
    }
  }

  for (const id of state.vehicleAnimMap.keys()) {
    if (!seenIds.has(id)) state.vehicleAnimMap.delete(id);
  }

  updateVehiclesGeoJSON(now);
}

// Updates both snapped sighting pins (even if GPS vehicles layer is hidden) and live vehicles GeoJSON
function updateVehiclesGeoJSON(now) {
  if (!map || !state.mapLoaded) return;

  // Map vehicle_id -> active sighting so we know danger color & can move snapped pins
  const vehicleSightingMap = new Map();
  for (const s of state.sightings) {
    if (s.status === "active" && s.matched_vehicle_id) {
      vehicleSightingMap.set(s.matched_vehicle_id, s);
    }
  }

  // Always update coordinates of sightings snapped to moving vehicles so clicking them never teleports back
  for (const [vid, s] of vehicleSightingMap.entries()) {
    const coords = getVehicleCurrentCoords(vid, now);
    if (coords) {
      s.lon = coords[0];
      s.lat = coords[1];
      if (state.markers.has(s.id)) {
        state.markers.get(s.id).marker.setLngLat(coords);
      }
      if (state.activePopup && state.activePopupSightingId === s.id) {
        state.activePopup.setLngLat(coords);
      }
    }
  }

  const src = map.getSource("live-vehicles");
  if (!src) return;

  // When "GPS Борти" button is toggled off, hide all vehicles immediately
  if (!state.showAllVehicles) {
    return;
  }

  const features = [];
  for (const [vid, anim] of state.vehicleAnimMap.entries()) {
    const v = anim.props;
    if (!matchesFilters(v, true)) continue;

    const t = Math.min(1, Math.max(0, (now - anim.startTime) / anim.duration));
    const lon = anim.fromLon + (anim.toLon - anim.fromLon) * t;
    const lat = anim.fromLat + (anim.toLat - anim.fromLat) * t;

    const matchedSighting = vehicleSightingMap.get(vid);
    const hasInspectors = Boolean(matchedSighting);
    const dangerAgeClass = matchedSighting ? getAgeClass(matchedSighting.age_seconds) : "";
    const dangerColor = matchedSighting ? getDangerColorHex(dangerAgeClass) : "#dc2626";
    const dangerFill =
      dangerAgeClass === "age-fresh"
        ? "rgba(220, 38, 38, 0.30)"
        : dangerAgeClass === "age-recent"
        ? "rgba(234, 88, 12, 0.28)"
        : dangerAgeClass === "age-aging"
        ? "rgba(234, 179, 8, 0.26)"
        : "rgba(220, 38, 38, 0.22)";

    const badgeIcon = ensureVehicleBadgeImage(v.short_label, v.transport_type, dangerAgeClass);

    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [lon, lat] },
      properties: {
        vehicle_id: v.vehicle_id,
        route_id: v.route_id,
        route_short_name: v.route_short_name,
        short_label: v.short_label,
        transport_type: v.transport_type,
        speed: v.speed,
        has_inspectors: hasInspectors,
        danger_color: dangerColor,
        danger_fill: dangerFill,
        badge_icon: badgeIcon,
      },
    });
  }

  src.setData({ type: "FeatureCollection", features });
}

// Throttled 350ms interpolation loop (~3 FPS instead of 60 FPS) so MapLibre's GeoJSON worker never freezes
setInterval(() => {
  if (state.mapLoaded && !document.hidden) {
    updateVehiclesGeoJSON(performance.now());
  }
}, 350);

async function loadAndDrawRoute(routeId) {
  if (!map || !state.mapLoaded) return;
  const src = map.getSource("selected-route");
  if (!src) return;
  state.selectedRouteId = routeId || null;

  if (!routeId) {
    src.setData({ type: "FeatureCollection", features: [] });
    return;
  }

  try {
    const res = await fetch(`/api/routes/${encodeURIComponent(routeId)}/shape`);
    if (!res.ok) return;
    const data = await res.json();
    if (!data.coordinates || !data.coordinates.length) return;

    src.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: data.coordinates,
          },
          properties: {
            color: data.color || "#3b82f6",
          },
        },
      ],
    });
  } catch (e) {
    console.error("Failed to load route shape:", e);
  }
}

function buildPinText(s) {
  const routeLabel = s.route_short_name
    ? formatVehicleLabel(s.route_short_name, s.transport_type)
    : "ПОСТ";
  const sentClock = formatSentClockTime(s.posted_at);
  const timerStr = formatElapsedTimer(s.age_seconds);
  return `${routeLabel} · ${sentClock} · ${timerStr}`;
}

// Fast 1-second tick that updates timers on all pins, cards, and open popups without rebuilding DOM
function tickSightingTimers() {
  let expiredAny = false;
  for (const s of state.sightings) {
    s.age_seconds = getCurrentSightingAge(s);
    if (s.age_seconds > MAX_AGE_SECONDS || s.status === "cleared") {
      expiredAny = true;
    }
  }

  if (expiredAny) {
    renderSightings();
    return;
  }

  for (const s of state.sightings) {
    const ageClass = getAgeClass(s.age_seconds);
    const snapClass = s.matched_vehicle_id ? "snapped" : "";

    // Update map pin text & danger class
    if (state.markers.has(s.id)) {
      const { el } = state.markers.get(s.id);
      el.className = `maplibregl-marker controller-marker ${ageClass} ${snapClass}`;
      const txtEl = el.querySelector(".marker-text");
      if (txtEl) txtEl.textContent = buildPinText(s);
    }

    // Update feed card timer & danger class
    const cardEl = document.querySelector(`.sighting-card[data-id="${CSS.escape(s.id)}"]`);
    if (cardEl) {
      cardEl.className = `sighting-card ${ageClass}`;
      const timeEl = cardEl.querySelector(".time-meta");
      if (timeEl) {
        timeEl.textContent = `${formatSentClockTime(s.posted_at)} · ${formatElapsedTimer(s.age_seconds)}`;
      }
    }

    // Update open popup timer if this sighting is currently focused
    if (state.activePopupSightingId === s.id) {
      const popupTimerEl = document.getElementById("active-popup-timer");
      if (popupTimerEl) {
        popupTimerEl.textContent = `${formatSentClockTime(s.posted_at)} · ${formatElapsedTimer(s.age_seconds)}`;
      }
    }
  }
}

function renderSightings() {
  state.sightings = state.sightings
    .map((s) => ({
      ...s,
      age_seconds: getCurrentSightingAge(s),
    }))
    .filter((s) => s.status !== "cleared" && s.age_seconds <= MAX_AGE_SECONDS);

  const activeCount = state.sightings.length;
  const countEl = document.getElementById("active-sightings-count");
  if (countEl) countEl.textContent = activeCount;

  const filtered = state.sightings.filter((s) => matchesFilters(s, false));
  const visibleIds = new Set(filtered.map((s) => s.id));

  for (const [id, obj] of state.markers.entries()) {
    if (!visibleIds.has(id)) {
      obj.marker.remove();
      state.markers.delete(id);
    }
  }

  if (map) {
    const perfNow = performance.now();
    for (const s of filtered) {
      if (s.matched_vehicle_id) {
        const liveCoords = getVehicleCurrentCoords(s.matched_vehicle_id, perfNow);
        if (liveCoords) {
          s.lon = liveCoords[0];
          s.lat = liveCoords[1];
        }
      }

      const ageClass = getAgeClass(s.age_seconds);
      const snapClass = s.matched_vehicle_id ? "snapped" : "";
      const pinLabel = buildPinText(s);

      if (state.markers.has(s.id)) {
        const { marker, el } = state.markers.get(s.id);
        el.className = `maplibregl-marker controller-marker ${ageClass} ${snapClass}`;
        const txtEl = el.querySelector(".marker-text");
        if (txtEl) txtEl.textContent = pinLabel;
        marker.setLngLat([s.lon, s.lat]);
      } else {
        const el = document.createElement("div");
        el.className = `controller-marker ${ageClass} ${snapClass}`;
        el.innerHTML = `
          <div class="marker-inner">
            <div class="marker-pulse"></div>
            <div class="marker-tag">
              <span class="marker-text">${pinLabel}</span>
            </div>
          </div>
        `;
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          const latest = state.sightings.find((item) => item.id === s.id) || s;
          focusSighting(latest);
        });

        // Offset snapped warning pin slightly above the vehicle icon so both danger colors are visible
        const markerOffset = s.matched_vehicle_id ? [0, -24] : [0, 0];
        const marker = new maplibregl.Marker({
          element: el,
          anchor: "center",
          offset: markerOffset,
        })
          .setLngLat([s.lon, s.lat])
          .addTo(map);

        state.markers.set(s.id, { marker, el });
      }
    }
  }

  const listEl = document.getElementById("sightings-list");
  if (!listEl) return;
  if (!filtered.length) {
    listEl.innerHTML = `<div class="empty-state">НЕМАЄ АКТИВНИХ ЗАПИСІВ ЗА ОСТАННІ 20 ХВИЛИН</div>`;
    return;
  }

  listEl.innerHTML = filtered
    .map((s) => {
      const ageClass = getAgeClass(s.age_seconds);
      const tType = s.transport_type || "unknown";
      const routeText = s.route_short_name
        ? formatVehicleLabel(s.route_short_name, s.transport_type)
        : "ЗУПИНКА";

      const snapBadge = s.matched_vehicle_id
        ? `<span class="gps-tag" title="Прив'язано до GPS борту">GPS #${escapeHtml(s.matched_vehicle_id)}</span>`
        : "";

      const dirHtml = s.direction_hint
        ? `<div class="card-direction">НАПРЯМОК: ${escapeHtml(s.direction_hint)}</div>`
        : "";

      const sentClock = formatSentClockTime(s.posted_at);

      // Only render modification/deletion buttons when logged in as Admin
      const adminVoteControls = state.isAdmin
        ? `
            <div class="vote-btns">
              <button type="button" class="vote-btn up" data-vote="up" data-id="${escapeHtml(s.id)}">
                АКТУАЛЬНО ${s.votes_up ? `[${s.votes_up}]` : ""}
              </button>
              <button type="button" class="vote-btn down" data-vote="down" data-id="${escapeHtml(s.id)}">
                ВІДБІЙ
              </button>
            </div>
          `
        : "";

      return `
        <article class="sighting-card ${ageClass}" data-id="${escapeHtml(s.id)}">
          <div class="card-top">
            <div class="badges">
              <span class="route-tag ${escapeHtml(tType)}">${escapeHtml(routeText)}</span>
              ${snapBadge}
            </div>
            <span class="time-meta" title="Час публікації в каналі та таймер активності">${sentClock} · ${formatElapsedTimer(s.age_seconds)}</span>
          </div>
          <div class="card-location">${escapeHtml(s.stop_name || "Львів")}</div>
          ${dirHtml}
          <div class="card-raw">${escapeHtml(s.raw_text)}</div>
          <div class="card-footer">
            <span class="conf-label">ДОВІРА ${Math.round(s.confidence * 100)}%</span>
            ${adminVoteControls}
          </div>
        </article>
      `;
    })
    .join("");

  listEl.querySelectorAll(".sighting-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".vote-btn")) return;
      const s = state.sightings.find((item) => item.id === card.dataset.id);
      if (s) focusSighting(s);
    });
  });

  if (state.isAdmin) {
    listEl.querySelectorAll(".vote-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        await sendVote(btn.dataset.id, btn.dataset.vote);
      });
    });
  }
}

async function focusSighting(s) {
  if (!map) return;

  // On mobile, if the bottom sheet is expanded, return it to default height so the map is visible
  const feedPanel = document.getElementById("feed-panel");
  if (window.innerWidth <= 860 && feedPanel && feedPanel.classList.contains("expanded")) {
    feedPanel.classList.remove("expanded");
    setTimeout(() => map?.resize(), 240);
  }

  // Always ensure we use the live vehicle coordinates if snapped to a moving vehicle
  if (s.matched_vehicle_id) {
    const liveCoords = getVehicleCurrentCoords(s.matched_vehicle_id, performance.now());
    if (liveCoords) {
      s.lon = liveCoords[0];
      s.lat = liveCoords[1];
    }
  }

  state.selectedSightingId = s.id;
  state.activePopupSightingId = s.id;
  if (state.activePopup) {
    state.activePopup.remove();
  }

  const routeIdToDraw = s.route_id || findMatchingRouteId(s.route_short_name, s.transport_type);
  await loadAndDrawRoute(routeIdToDraw);

  map.flyTo({
    center: [s.lon, s.lat],
    zoom: Math.max(map.getZoom(), 14.8),
    speed: 1.4,
  });

  const tgUrl = s.raw_message_id.startsWith("user/")
    ? null
    : `https://t.me/${encodeURI(s.raw_message_id)}`;

  const ageClass = getAgeClass(s.age_seconds);
  const dangerHex = getDangerColorHex(ageClass) || "#dc2626";

  const snapInfo = s.matched_vehicle_id
    ? `<div style="margin:6px 0;padding:4px 7px;border-radius:3px;border:1px solid ${dangerHex};background:rgba(220,38,38,0.12);color:${dangerHex};font-family:var(--font-mono);font-size:0.70rem;font-weight:700;">GPS ПРИВ'ЯЗКА // БОРТ #${escapeHtml(s.matched_vehicle_id)}</div>`
    : `<div style="margin:6px 0;font-family:var(--font-mono);font-size:0.68rem;color:var(--text-muted);">СТАТИЧНА ТОЧКА ЗУПИНКИ</div>`;

  const sentClock = formatSentClockTime(s.posted_at);
  const routeBadgeLabel = s.route_short_name
    ? formatVehicleLabel(s.route_short_name, s.transport_type)
    : "ПОСТ КОНТРОЛЮ";

  const adminPopupClearBtn = state.isAdmin
    ? `<button type="button" id="popup-clear-btn" class="vote-btn down" style="padding:3px 8px;">ВІДБІЙ</button>`
    : `<span style="font-family:var(--font-mono);font-size:0.68rem;color:var(--text-muted);">ДОВІРА ${Math.round(s.confidence * 100)}%</span>`;

  state.activePopup = new maplibregl.Popup({ offset: s.matched_vehicle_id ? 28 : 16 })
    .setLngLat([s.lon, s.lat])
    .setHTML(`
      <div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
          <strong style="font-family:var(--font-mono);font-size:0.84rem;color:${dangerHex};">${escapeHtml(routeBadgeLabel)}</strong>
          <span id="active-popup-timer" style="font-family:var(--font-mono);font-size:0.70rem;color:var(--text-muted);">${sentClock} · ${formatElapsedTimer(s.age_seconds)}</span>
        </div>
        <div style="font-weight:600;font-size:0.82rem;margin-bottom:2px;">${escapeHtml(s.stop_name || "")}</div>
        ${s.direction_hint ? `<div style="font-size:0.74rem;color:var(--text-secondary);">НАПРЯМОК: ${escapeHtml(s.direction_hint)}</div>` : ""}
        ${snapInfo}
        <div style="font-size:0.75rem;padding:6px 8px;background:var(--bg-elevated);border:1px solid var(--border);border-radius:3px;margin:6px 0;">
          ${escapeHtml(s.raw_text)}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;gap:8px;">
          ${tgUrl ? `<a href="${tgUrl}" target="_blank" rel="noopener" style="font-family:var(--font-mono);font-size:0.70rem;color:#60a5fa;text-decoration:none;font-weight:600;">ДЖЕРЕЛО TG ↗</a>` : `<span></span>`}
          ${adminPopupClearBtn}
        </div>
      </div>
    `)
    .addTo(map);

  if (state.isAdmin) {
    const popupClearBtn = document.getElementById("popup-clear-btn");
    if (popupClearBtn) {
      popupClearBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        await sendVote(s.id, "down");
      });
    }
  }
}

async function sendVote(sightingId, direction) {
  if (!state.isAdmin) return;

  // Optimistically remove immediately on "down" (ВІДБІЙ) so pins disappear right away
  if (direction === "down") {
    state.sightings = state.sightings.filter((s) => s.id !== sightingId);
    if (state.markers.has(sightingId)) {
      state.markers.get(sightingId).marker.remove();
      state.markers.delete(sightingId);
    }
    if (state.activePopup && state.activePopupSightingId === sightingId) {
      state.activePopup.remove();
      state.activePopup = null;
      state.activePopupSightingId = null;
    }
    renderSightings();
    updateVehiclesGeoJSON(performance.now());
  }

  try {
    const res = await fetch(`/api/sightings/${encodeURIComponent(sightingId)}/vote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction }),
    });
    if (res.status === 403) {
      state.isAdmin = false;
      updateAdminUI();
      await syncSightingsFromServer();
      return;
    }
    if (res.ok && direction !== "down") {
      const { sighting } = await res.json();
      if (sighting.status === "cleared") {
        state.sightings = state.sightings.filter((s) => s.id !== sighting.id);
      } else {
        const [prepared] = prepareSightingsList([sighting]);
        if (prepared) {
          const idx = state.sightings.findIndex((s) => s.id === prepared.id);
          if (idx !== -1) {
            state.sightings[idx] = prepared;
          }
        }
      }
      renderSightings();
      updateVehiclesGeoJSON(performance.now());
    }
  } catch (e) {
    console.error("Vote error:", e);
  }
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function syncSightingsFromServer() {
  try {
    const res = await fetch("/api/sightings");
    if (res.ok) {
      const data = await res.json();
      state.sightings = prepareSightingsList(data.sightings || []);
      renderSightings();
    }
  } catch (e) {
    // Silent fallback
  }
}

async function initApp() {
  const routeInput = document.getElementById("route-filter-input");
  const clearRouteBtn = document.getElementById("clear-route-btn");
  if (state.routeFilterText && routeInput) {
    routeInput.value = state.routeFilterText;
    if (clearRouteBtn) clearRouteBtn.classList.remove("hidden");
  }

  let initialData = null;
  try {
    const res = await fetch("/api/init");
    initialData = await res.json();
    if (initialData.carto_api_key) {
      CARTO_API_KEY = initialData.carto_api_key;
    }
    state.isAdmin = Boolean(initialData.is_admin);
  } catch (e) {
    console.error("Failed to fetch initial state:", e);
  }

  updateAdminUI();

  // Initialize MapLibre once with dual-theme CARTO layers so setStyle() is never needed
  map = new maplibregl.Map({
    container: "map",
    style: buildDualThemeStyle(getRenderedScheme(), CARTO_API_KEY),
    center: LVIV_CENTER,
    zoom: 12.6,
    minZoom: 10,
    maxZoom: 18,
    attributionControl: false,
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "bottom-right");

  map.on("load", () => {
    setupMapLayers();
  });

  if (initialData) {
    for (const r of initialData.routes || []) {
      state.routes.set(r.id, r);
    }
    ingestVehiclesBatch(initialData.vehicles || []);
    state.sightings = prepareSightingsList(initialData.sightings || []);
    renderSightings();
  }

  connectSSE();

  // Live 1-second timer tick for all map pins, feed cards, and open popups
  setInterval(() => {
    tickSightingTimers();
  }, 1000);

  // Sync sightings with server every 15 seconds
  setInterval(() => {
    syncSightingsFromServer();
  }, 15000);
}

function connectSSE() {
  if (currentEventSource) {
    try {
      currentEventSource.close();
    } catch (_) {}
  }

  const indicator = document.getElementById("sse-indicator");
  const es = new EventSource("/api/stream");
  currentEventSource = es;

  es.onopen = () => {
    if (indicator) indicator.classList.remove("offline");
  };

  es.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "live_tick") {
        if (msg.data.vehicles) ingestVehiclesBatch(msg.data.vehicles);
        if (msg.data.sightings) {
          state.sightings = prepareSightingsList(msg.data.sightings);
          renderSightings();
        }
      } else if (msg.type === "sightings_update") {
        state.sightings = prepareSightingsList(msg.data.sightings || []);
        renderSightings();
        updateVehiclesGeoJSON(performance.now());
      }
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  };

  es.onerror = () => {
    if (indicator) indicator.classList.add("offline");
    try {
      es.close();
    } catch (_) {}
    setTimeout(connectSSE, 4000);
  };
}

// UI Event Listeners
document.getElementById("route-filter-input")?.addEventListener("input", (e) => {
  state.routeFilterText = e.target.value.trim();
  localStorage.setItem("lviv_route_filter", state.routeFilterText);
  document.getElementById("clear-route-btn")?.classList.toggle("hidden", !state.routeFilterText);
  highlightRouteFromSearch(state.routeFilterText);
  renderSightings();
  updateVehiclesGeoJSON(performance.now());
});

document.getElementById("clear-route-btn")?.addEventListener("click", () => {
  state.routeFilterText = "";
  localStorage.removeItem("lviv_route_filter");
  const input = document.getElementById("route-filter-input");
  if (input) input.value = "";
  document.getElementById("clear-route-btn")?.classList.add("hidden");
  loadAndDrawRoute(null);
  renderSightings();
  updateVehiclesGeoJSON(performance.now());
});

document.querySelectorAll(".segmented-filter .seg-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".segmented-filter .seg-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.transportTab = btn.dataset.type;
    if (state.routeFilterText) {
      highlightRouteFromSearch(state.routeFilterText);
    }
    renderSightings();
    updateVehiclesGeoJSON(performance.now());
  });
});

document.getElementById("toggle-vehicles-btn")?.addEventListener("click", (e) => {
  state.showAllVehicles = !state.showAllVehicles;
  e.currentTarget.classList.toggle("active", state.showAllVehicles);
  syncVehiclesLayerVisibility();
  if (state.showAllVehicles) {
    updateVehiclesGeoJSON(performance.now());
  }
});

document.getElementById("locate-me-btn")?.addEventListener("click", () => {
  if (!navigator.geolocation || !map) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { longitude, latitude } = pos.coords;
      map.flyTo({ center: [longitude, latitude], zoom: 15 });
      new maplibregl.Marker({ color: "#2563eb" })
        .setLngLat([longitude, latitude])
        .addTo(map);
    },
    () => {
      map.flyTo({ center: LVIV_CENTER, zoom: 13.5 });
    }
  );
});

// Instant Theme Toggle without calling map.setStyle() (preserves all vehicle layers & badges!)
document.getElementById("theme-toggle-btn")?.addEventListener("click", () => {
  const current = getRenderedScheme();
  const target = current === "dark" ? "light" : "dark";
  const systemScheme = darkMediaQuery.matches ? "dark" : "light";

  if (target !== systemScheme) {
    localStorage.setItem("color-scheme", target);
  } else {
    localStorage.removeItem("color-scheme");
  }
  if (colorSchemeMeta) {
    colorSchemeMeta.content = localStorage.getItem("color-scheme") || "light dark";
  }

  if (map) {
    const isDark = target === "dark";
    if (map.getLayer("carto-dark-layer")) {
      map.setLayoutProperty("carto-dark-layer", "visibility", isDark ? "visible" : "none");
    }
    if (map.getLayer("carto-light-layer")) {
      map.setLayoutProperty("carto-light-layer", "visibility", isDark ? "none" : "visible");
    }
  }
});

// Mobile Bottom Sheet Controls (Tap + Touch Swipe Up/Down + Auto Map Resize)
const feedPanelEl = document.getElementById("feed-panel");
const panelHandleEl = document.getElementById("panel-handle");
const sheetToggleBtn = document.getElementById("sheet-toggle-btn");

function setMobileSheetState(mode) {
  if (!feedPanelEl) return;
  feedPanelEl.classList.remove("expanded", "collapsed");
  if (mode === "expanded") feedPanelEl.classList.add("expanded");
  if (mode === "collapsed") feedPanelEl.classList.add("collapsed");
  setTimeout(() => map?.resize(), 240);
}

function cycleMobileSheetState() {
  if (!feedPanelEl) return;
  if (feedPanelEl.classList.contains("expanded")) {
    setMobileSheetState("collapsed");
  } else if (feedPanelEl.classList.contains("collapsed")) {
    setMobileSheetState("default");
  } else {
    setMobileSheetState("expanded");
  }
}

panelHandleEl?.addEventListener("click", cycleMobileSheetState);
sheetToggleBtn?.addEventListener("click", (e) => {
  e.stopPropagation();
  cycleMobileSheetState();
});

let touchStartY = null;
panelHandleEl?.addEventListener(
  "touchstart",
  (e) => {
    if (e.touches.length === 1) {
      touchStartY = e.touches[0].clientY;
    }
  },
  { passive: true }
);

panelHandleEl?.addEventListener(
  "touchend",
  (e) => {
    if (touchStartY === null || !e.changedTouches.length || !feedPanelEl) return;
    const dy = e.changedTouches[0].clientY - touchStartY;
    touchStartY = null;
    if (Math.abs(dy) < 24) return;
    if (dy < 0) {
      // Swiped up
      if (feedPanelEl.classList.contains("collapsed")) {
        setMobileSheetState("default");
      } else {
        setMobileSheetState("expanded");
      }
    } else {
      // Swiped down
      if (feedPanelEl.classList.contains("expanded")) {
        setMobileSheetState("default");
      } else {
        setMobileSheetState("collapsed");
      }
    }
  },
  { passive: true }
);

window.addEventListener("resize", () => {
  map?.resize();
});

// Admin Login & Logout Controls
const adminDialog = document.getElementById("admin-dialog");
const adminForm = document.getElementById("admin-form");
const adminPasswordInput = document.getElementById("admin-password");
const adminError = document.getElementById("admin-error");

document.getElementById("admin-btn")?.addEventListener("click", async () => {
  if (state.isAdmin) {
    await fetch("/api/admin/logout", { method: "POST" });
    state.isAdmin = false;
    updateAdminUI();
    return;
  }
  adminError?.classList.add("hidden");
  if (adminPasswordInput) adminPasswordInput.value = "";
  adminDialog?.showModal();
});

document.getElementById("close-admin-btn")?.addEventListener("click", () => adminDialog?.close());
document.getElementById("cancel-admin-btn")?.addEventListener("click", () => adminDialog?.close());

adminForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  adminError?.classList.add("hidden");
  const password = adminPasswordInput?.value || "";
  if (!password) return;

  try {
    const res = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (adminError) {
        adminError.textContent = data.detail || "Помилка авторизації";
        adminError.classList.remove("hidden");
      }
      return;
    }
    state.isAdmin = Boolean(data.is_admin);
    if (adminPasswordInput) adminPasswordInput.value = "";
    adminDialog?.close();
    updateAdminUI();
  } catch (err) {
    if (adminError) {
      adminError.textContent = "Помилка мережі";
      adminError.classList.remove("hidden");
    }
  }
});

// Admin-Only Report Dialog
const reportDialog = document.getElementById("report-dialog");
const reportForm = document.getElementById("report-form");
const reportText = document.getElementById("report-text");
const reportError = document.getElementById("report-error");

document.getElementById("report-btn")?.addEventListener("click", () => {
  if (!state.isAdmin) return;
  reportError?.classList.add("hidden");
  reportDialog?.showModal();
});

document.getElementById("close-dialog-btn")?.addEventListener("click", () => reportDialog?.close());
document.getElementById("cancel-report-btn")?.addEventListener("click", () => reportDialog?.close());

document.querySelectorAll(".preset-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    if (reportText) reportText.value = chip.dataset.text;
  });
});

reportForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.isAdmin) return;
  reportError?.classList.add("hidden");
  const text = reportText?.value.trim();
  if (!text) return;

  try {
    const res = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (res.status === 403) {
        state.isAdmin = false;
        updateAdminUI();
        reportDialog?.close();
        return;
      }
      if (reportError) {
        reportError.textContent = data.detail || "Помилка відправки";
        reportError.classList.remove("hidden");
      }
      return;
    }
    if (reportText) reportText.value = "";
    reportDialog?.close();
    if (data.sighting) {
      const [prepared] = prepareSightingsList([data.sighting]);
      const existingIdx = state.sightings.findIndex((item) => item.id === prepared.id);
      if (existingIdx !== -1) {
        state.sightings[existingIdx] = prepared;
      } else {
        state.sightings.unshift(prepared);
      }
      renderSightings();
      updateVehiclesGeoJSON(performance.now());
      focusSighting(prepared);
    }
  } catch (err) {
    if (reportError) {
      reportError.textContent = "Помилка мережі";
      reportError.classList.remove("hidden");
    }
  }
});

initApp();
