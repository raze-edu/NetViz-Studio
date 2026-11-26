/* NetViz Studio v2.1 Dual-Engine Geo Map Controller
 * Supports: Google Maps JS API (4000ms watchdog + gm_authFailure)
 *        -> Local Leaflet 1.9.4 (/tiles/osm & /tiles/esri_satellite)
 *        -> Lightweight HTML5 <canvas> slippy-tile grid fallback
 * Exact Section 4.2A/B WGS84 tangent-plane & CW rotation_deg parity with shared/geo_math.py.
 */
(function () {
  "use strict";

  const DEFAULT_LAT = 52.520000;
  const DEFAULT_LON = 13.405000;
  const DEFAULT_ZOOM = 18;
  const GOOGLE_WATCHDOG_MS = 4000;

  // --- Section 4.2A/B Canonical Geo Math (Identical to shared/geo_math.py) ---

  function metersPerDegree(originLat) {
    const phi = (originLat * Math.PI) / 180.0;
    const mLat = 111132.92 - 559.82 * Math.cos(2.0 * phi) + 1.175 * Math.cos(4.0 * phi);
    const mLon = 111412.84 * Math.cos(phi) - 93.5 * Math.cos(3.0 * phi);
    return { mLat: mLat, mLon: mLon };
  }

  function localMToLatLon(pt, anchor) {
    const scale = metersPerDegree(anchor.origin_lat);
    const theta = ((anchor.rotation_deg || 0.0) * Math.PI) / 180.0;
    const east = pt.x * Math.cos(theta) + pt.y * Math.sin(theta);
    const north = -pt.x * Math.sin(theta) + pt.y * Math.cos(theta);
    const lat = anchor.origin_lat + north / scale.mLat;
    const lon = anchor.origin_lon + east / scale.mLon;
    return [lat, lon];
  }

  function latLonToLocalM(lat, lon, anchor) {
    const scale = metersPerDegree(anchor.origin_lat);
    const theta = ((anchor.rotation_deg || 0.0) * Math.PI) / 180.0;
    const east = (lon - anchor.origin_lon) * scale.mLon;
    const north = (lat - anchor.origin_lat) * scale.mLat;
    const x = east * Math.cos(theta) - north * Math.sin(theta);
    const y = east * Math.sin(theta) + north * Math.cos(theta);
    return { x: x, y: y };
  }

  function buildingLatLonRing(bldg) {
    if (!bldg || !bldg.geo_anchor) {
      return [];
    }
    if (Array.isArray(bldg.floor0_polygon_m) && bldg.floor0_polygon_m.length >= 3) {
      return bldg.floor0_polygon_m.map(function (pt) {
        return localMToLatLon(pt, bldg.geo_anchor);
      });
    }
    if (Array.isArray(bldg.footprint_latlon) && bldg.footprint_latlon.length >= 3) {
      return bldg.footprint_latlon.slice();
    }
    return [];
  }

  function buildingRotateHandleLatLon(bldg) {
    const anchor = bldg.geo_anchor;
    if (!anchor) return [DEFAULT_LAT, DEFAULT_LON];
    let maxExtent = 12.0;
    if (Array.isArray(bldg.floor0_polygon_m)) {
      for (let i = 0; i < bldg.floor0_polygon_m.length; i++) {
        const pt = bldg.floor0_polygon_m[i];
        const r = Math.hypot(pt.x, pt.y);
        if (r + 5.0 > maxExtent) maxExtent = r + 5.0;
      }
    }
    return localMToLatLon({ x: 0.0, y: maxExtent }, anchor);
  }

  function computeAzimuthCwDeg(originLat, originLon, targetLat, targetLon) {
    const scale = metersPerDegree(originLat);
    const east = (targetLon - originLon) * scale.mLon;
    const north = (targetLat - originLat) * scale.mLat;
    let deg = (Math.atan2(east, north) * 180.0) / Math.PI;
    deg = ((deg % 360.0) + 360.0) % 360.0;
    return deg;
  }

  // --- Slippy tile math for Canvas fallback ---

  function latLonToWorldPx(lat, lon, zoom) {
    const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, lat));
    const scale = 256 * Math.pow(2, zoom);
    const x = ((lon + 180.0) / 360.0) * scale;
    const sinLat = Math.sin((clampedLat * Math.PI) / 180.0);
    const y = (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * scale;
    return { x: x, y: y };
  }

  function worldPxToLatLon(wx, wy, zoom) {
    const scale = 256 * Math.pow(2, zoom);
    const lon = (wx / scale) * 360.0 - 180.0;
    const n = Math.PI - (2.0 * Math.PI * wy) / scale;
    const lat = (180.0 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    return [lat, lon];
  }

  // --- State ---

  const state = {
    bridge: null,
    config: {
      preferred_provider: "google",
      google_api_key: "",
      offline_mode: false,
      tile_base_url: window.location.origin,
    },
    activeEngine: "none", // "google" | "leaflet_osm" | "leaflet_satellite" | "canvas"
    centerLat: DEFAULT_LAT,
    centerLon: DEFAULT_LON,
    zoom: DEFAULT_ZOOM,
    buildings: [],
    selectedBuildingId: null,
    mode: "pan", // "pan" | "trace" | "place"
    placeBuildingId: null,
    tracePoints: [],
    hasCenteredInitially: false,

    // Leaflet handles
    leafletMap: null,
    leafletTileLayer: null,
    leafletBuildingLayers: {},
    leafletHandleLayers: [],
    leafletTraceLayer: null,
    leafletTraceMarkers: [],

    // Google Maps handles
    googleMap: null,
    googleLoadedKey: null,
    googleWatchdog: null,
    googlePolygons: {},
    googleHandleMarkers: [],
    googleStemLine: null,
    googleTracePolyline: null,
    googleTraceMarkers: [],

    // Canvas fallback state
    canvasTiles: {},
    canvasDragging: false,
    canvasDragMode: null, // "pan" | "move" | "rotate"
    canvasLastMouse: null,
  };

  // --- DOM refs ---

  const elLeaflet = document.getElementById("leaflet-map");
  const elGoogle = document.getElementById("google-map");
  const elCanvas = document.getElementById("canvas-fallback");
  const hudProvider = document.getElementById("hud-provider");
  const hudMode = document.getElementById("hud-mode");
  const hudCoords = document.getElementById("hud-coords");
  const traceControls = document.getElementById("trace-controls");
  const placeControls = document.getElementById("place-controls");
  const rotateTooltip = document.getElementById("rotate-tooltip");

  function setActiveLayer(layerName) {
    elLeaflet.classList.toggle("active", layerName === "leaflet");
    elGoogle.classList.toggle("active", layerName === "google");
    elCanvas.classList.toggle("active", layerName === "canvas");
  }

  function updateHud() {
    const providerLabels = {
      google: "Engine: Google Maps",
      leaflet_satellite: "Engine: Leaflet Satellite",
      leaflet_osm: "Engine: Leaflet OSM",
      canvas: "Engine: Offline Tile Grid",
    };
    hudProvider.textContent = providerLabels[state.activeEngine] || "Engine: Initializing...";

    if (state.mode === "trace") {
      hudMode.textContent = "Mode: Trace Building (" + state.tracePoints.length + " pts)";
      hudMode.className = "hud-pill warn";
      traceControls.classList.add("visible");
      placeControls.style.display = "none";
    } else if (state.mode === "place") {
      hudMode.textContent = "Mode: Click Map to Place Building";
      hudMode.className = "hud-pill warn";
      traceControls.classList.remove("visible");
      placeControls.style.display = "inline-flex";
    } else {
      hudMode.textContent = "Mode: Pan & Select";
      hudMode.className = "hud-pill";
      traceControls.classList.remove("visible");
      placeControls.style.display = "none";
    }
    hudCoords.textContent = state.centerLat.toFixed(6) + ", " + state.centerLon.toFixed(6);
  }

  function notifyProviderSwitched(providerName) {
    state.activeEngine = providerName;
    updateHud();
    if (state.bridge && state.bridge.on_provider_switched) {
      state.bridge.on_provider_switched(providerName);
    }
  }

  function notifyAuthError(msg) {
    if (state.bridge && state.bridge.on_auth_error) {
      state.bridge.on_auth_error(msg);
    }
  }

  function showRotateTooltip(deg, clientX, clientY) {
    rotateTooltip.textContent = deg.toFixed(1) + "°";
    rotateTooltip.style.left = (clientX + 14) + "px";
    rotateTooltip.style.top = (clientY - 22) + "px";
    rotateTooltip.style.display = "block";
  }

  function hideRotateTooltip() {
    rotateTooltip.style.display = "none";
  }

  // --- Shared interaction callbacks ---

  function finishTrace() {
    if (state.tracePoints.length < 3) {
      return;
    }
    const payload = JSON.stringify(state.tracePoints);
    state.tracePoints = [];
    state.mode = "pan";
    updateHud();
    renderAll();
    if (state.bridge && state.bridge.on_polygon_traced) {
      state.bridge.on_polygon_traced(payload);
    }
  }

  function cancelMode() {
    state.mode = "pan";
    state.placeBuildingId = null;
    state.tracePoints = [];
    updateHud();
    renderAll();
  }

  function handleMapClick(lat, lon) {
    hudCoords.textContent = lat.toFixed(6) + ", " + lon.toFixed(6);
    if (state.mode === "trace") {
      state.tracePoints.push([lat, lon]);
      updateHud();
      renderTraceOverlay();
      return;
    }
    if (state.mode === "place" && state.placeBuildingId) {
      const targetId = state.placeBuildingId;
      state.mode = "pan";
      state.placeBuildingId = null;
      updateHud();
      if (state.bridge && state.bridge.on_building_placed) {
        state.bridge.on_building_placed(targetId, lat, lon);
      }
    }
  }

  function handleBuildingClick(bldgId) {
    if (state.mode === "trace" || state.mode === "place") {
      return;
    }
    state.selectedBuildingId = bldgId;
    renderAll();
    if (state.bridge && state.bridge.on_building_selected) {
      state.bridge.on_building_selected(bldgId);
    }
  }

  function commitBuildingTransform(bldg) {
    if (!bldg || !bldg.geo_anchor) return;
    if (state.bridge && state.bridge.on_building_transformed) {
      state.bridge.on_building_transformed(
        bldg.id,
        bldg.geo_anchor.origin_lat,
        bldg.geo_anchor.origin_lon,
        bldg.geo_anchor.rotation_deg
      );
    }
  }

  // --- Leaflet Engine ---

  function tileUrlForProvider(provider) {
    const base = state.config.tile_base_url || window.location.origin;
    const tileProvider = provider === "leaflet_osm" ? "osm" : "esri_satellite";
    return base.replace(/\/$/, "") + "/tiles/" + tileProvider + "/{z}/{x}/{y}";
  }

  function activateLeaflet(providerName) {
    if (typeof window.L === "undefined") {
      activateCanvasFallback();
      return;
    }
    setActiveLayer("leaflet");
    if (!state.leafletMap) {
      state.leafletMap = window.L.map(elLeaflet, {
        center: [state.centerLat, state.centerLon],
        zoom: state.zoom,
        zoomControl: true,
        doubleClickZoom: false,
      });
      state.leafletMap.on("click", function (ev) {
        handleMapClick(ev.latlng.lat, ev.latlng.lng);
      });
      state.leafletMap.on("dblclick", function () {
        if (state.mode === "trace" && state.tracePoints.length >= 3) {
          finishTrace();
        }
      });
      state.leafletMap.on("mousemove", function (ev) {
        hudCoords.textContent = ev.latlng.lat.toFixed(6) + ", " + ev.latlng.lng.toFixed(6);
      });
      state.leafletMap.on("moveend", function () {
        const c = state.leafletMap.getCenter();
        state.centerLat = c.lat;
        state.centerLon = c.lng;
        state.zoom = state.leafletMap.getZoom();
      });
    } else {
      state.leafletMap.invalidateSize();
      state.leafletMap.setView([state.centerLat, state.centerLon], state.zoom);
    }

    if (state.leafletTileLayer) {
      state.leafletMap.removeLayer(state.leafletTileLayer);
    }
    state.leafletTileLayer = window.L.tileLayer(tileUrlForProvider(providerName), {
      maxZoom: 20,
      minZoom: 2,
      attribution: providerName === "leaflet_osm" ? "OSM (Cached)" : "Esri World Imagery (Cached)",
    }).addTo(state.leafletMap);

    notifyProviderSwitched(providerName);
    renderAll();
  }

  function clearLeafletOverlays() {
    if (!state.leafletMap) return;
    Object.keys(state.leafletBuildingLayers).forEach(function (id) {
      state.leafletMap.removeLayer(state.leafletBuildingLayers[id]);
    });
    state.leafletBuildingLayers = {};
    state.leafletHandleLayers.forEach(function (layer) {
      state.leafletMap.removeLayer(layer);
    });
    state.leafletHandleLayers = [];
  }

  function renderLeafletBuildings() {
    if (!state.leafletMap) return;
    clearLeafletOverlays();

    state.buildings.forEach(function (bldg) {
      const ring = buildingLatLonRing(bldg);
      if (ring.length < 3) return;
      const isSelected = bldg.id === state.selectedBuildingId;

      const poly = window.L.polygon(ring, {
        color: isSelected ? "#F59E0B" : "#38BDF8",
        weight: isSelected ? 3 : 2,
        fillColor: isSelected ? "#F59E0B" : "#38BDF8",
        fillOpacity: isSelected ? 0.30 : 0.22,
      }).addTo(state.leafletMap);

      poly.on("click", function (ev) {
        if (state.mode === "pan") {
          window.L.DomEvent.stopPropagation(ev);
          handleBuildingClick(bldg.id);
        }
      });

      poly.bindTooltip(bldg.name, {
        permanent: false,
        direction: "center",
        className: "nv-bldg-label",
      });
      state.leafletBuildingLayers[bldg.id] = poly;

      if (isSelected && bldg.geo_anchor) {
        const moveIcon = window.L.divIcon({
          className: "nv-move-handle",
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        });
        const moveMarker = window.L.marker(
          [bldg.geo_anchor.origin_lat, bldg.geo_anchor.origin_lon],
          { icon: moveIcon, draggable: true }
        ).addTo(state.leafletMap);

        const rotLatLon = buildingRotateHandleLatLon(bldg);
        const stemLine = window.L.polyline(
          [[bldg.geo_anchor.origin_lat, bldg.geo_anchor.origin_lon], rotLatLon],
          { color: "#F59E0B", weight: 2, dashArray: "5, 5" }
        ).addTo(state.leafletMap);

        const rotIcon = window.L.divIcon({
          className: "nv-rotate-handle",
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        });
        const rotMarker = window.L.marker(rotLatLon, {
          icon: rotIcon,
          draggable: true,
        }).addTo(state.leafletMap);

        moveMarker.on("drag", function () {
          const ll = moveMarker.getLatLng();
          bldg.geo_anchor.origin_lat = ll.lat;
          bldg.geo_anchor.origin_lon = ll.lng;
          const updatedRing = buildingLatLonRing(bldg);
          poly.setLatLngs(updatedRing);
          const updatedRot = buildingRotateHandleLatLon(bldg);
          rotMarker.setLatLng(updatedRot);
          stemLine.setLatLngs([[ll.lat, ll.lng], updatedRot]);
        });
        moveMarker.on("dragend", function () {
          commitBuildingTransform(bldg);
        });

        rotMarker.on("drag", function (ev) {
          const ll = rotMarker.getLatLng();
          const deg = computeAzimuthCwDeg(
            bldg.geo_anchor.origin_lat,
            bldg.geo_anchor.origin_lon,
            ll.lat,
            ll.lng
          );
          bldg.geo_anchor.rotation_deg = deg;
          poly.setLatLngs(buildingLatLonRing(bldg));
          const snappedRot = buildingRotateHandleLatLon(bldg);
          stemLine.setLatLngs([
            [bldg.geo_anchor.origin_lat, bldg.geo_anchor.origin_lon],
            snappedRot,
          ]);
          if (ev.originalEvent) {
            showRotateTooltip(deg, ev.originalEvent.clientX, ev.originalEvent.clientY);
          }
        });
        rotMarker.on("dragend", function () {
          hideRotateTooltip();
          rotMarker.setLatLng(buildingRotateHandleLatLon(bldg));
          commitBuildingTransform(bldg);
        });

        state.leafletHandleLayers.push(moveMarker, stemLine, rotMarker);
      }
    });
  }

  function renderLeafletTrace() {
    if (!state.leafletMap) return;
    if (state.leafletTraceLayer) {
      state.leafletMap.removeLayer(state.leafletTraceLayer);
      state.leafletTraceLayer = null;
    }
    state.leafletTraceMarkers.forEach(function (m) {
      state.leafletMap.removeLayer(m);
    });
    state.leafletTraceMarkers = [];
    if (state.mode !== "trace" || state.tracePoints.length === 0) return;

    if (state.tracePoints.length >= 3) {
      state.leafletTraceLayer = window.L.polygon(state.tracePoints, {
        color: "#22C55E",
        weight: 2,
        dashArray: "6, 4",
        fillColor: "#22C55E",
        fillOpacity: 0.2,
      }).addTo(state.leafletMap);
    } else if (state.tracePoints.length >= 2) {
      state.leafletTraceLayer = window.L.polyline(state.tracePoints, {
        color: "#22C55E",
        weight: 2,
        dashArray: "6, 4",
      }).addTo(state.leafletMap);
    }

    state.tracePoints.forEach(function (pt, idx) {
      const m = window.L.circleMarker(pt, {
        radius: idx === 0 ? 7 : 5,
        color: "#FFFFFF",
        weight: 2,
        fillColor: idx === 0 ? "#22C55E" : "#38BDF8",
        fillOpacity: 1.0,
      }).addTo(state.leafletMap);
      if (idx === 0) {
        m.on("click", function (ev) {
          window.L.DomEvent.stopPropagation(ev);
          if (state.tracePoints.length >= 3) {
            finishTrace();
          }
        });
      }
      state.leafletTraceMarkers.push(m);
    });
  }

  // --- Google Maps Engine ---

  function clearGoogleWatchdog() {
    if (state.googleWatchdog !== null) {
      window.clearTimeout(state.googleWatchdog);
      state.googleWatchdog = null;
    }
  }

  window.gm_authFailure = function () {
    clearGoogleWatchdog();
    notifyAuthError(
      "Google Maps authentication failed (gm_authFailure). Check API key & HTTP referrer (127.0.0.1/*). Switched to Leaflet fallback."
    );
    activateLeaflet("leaflet_satellite");
  };

  window.__nvGoogleReady = function () {
    clearGoogleWatchdog();
    if (!window.google || !window.google.maps) {
      activateLeaflet("leaflet_satellite");
      return;
    }
    if (state.config.preferred_provider !== "google") {
      return;
    }
    setActiveLayer("google");
    if (!state.googleMap) {
      state.googleMap = new window.google.maps.Map(elGoogle, {
        center: { lat: state.centerLat, lng: state.centerLon },
        zoom: state.zoom,
        mapTypeId: "hybrid",
        tilt: 0,
        disableDoubleClickZoom: true,
      });
      state.googleMap.addListener("click", function (ev) {
        if (ev.latLng) {
          handleMapClick(ev.latLng.lat(), ev.latLng.lng());
        }
      });
      state.googleMap.addListener("dblclick", function () {
        if (state.mode === "trace" && state.tracePoints.length >= 3) {
          finishTrace();
        }
      });
      state.googleMap.addListener("idle", function () {
        const c = state.googleMap.getCenter();
        if (c) {
          state.centerLat = c.lat();
          state.centerLon = c.lng();
        }
        const z = state.googleMap.getZoom();
        if (typeof z === "number") {
          state.zoom = z;
        }
      });
    } else {
      state.googleMap.setCenter({ lat: state.centerLat, lng: state.centerLon });
      state.googleMap.setZoom(state.zoom);
    }
    notifyProviderSwitched("google");
    renderAll();
  };

  function activateGoogle(apiKey) {
    clearGoogleWatchdog();
    if (window.google && window.google.maps && state.googleLoadedKey === apiKey) {
      window.__nvGoogleReady();
      return;
    }
    state.googleWatchdog = window.setTimeout(function () {
      state.googleWatchdog = null;
      if (state.activeEngine !== "google") {
        notifyAuthError(
          "Google Maps JS API timed out after 4000 ms (offline or blocked). Switched to local Leaflet."
        );
        activateLeaflet("leaflet_satellite");
      }
    }, GOOGLE_WATCHDOG_MS);

    const existing = document.getElementById("nv-google-maps-script");
    if (existing) {
      existing.remove();
    }
    state.googleLoadedKey = apiKey;
    const script = document.createElement("script");
    script.id = "nv-google-maps-script";
    script.async = true;
    script.defer = true;
    script.src =
      "https://maps.googleapis.com/maps/api/js?key=" +
      encodeURIComponent(apiKey) +
      "&callback=__nvGoogleReady";
    script.onerror = function () {
      clearGoogleWatchdog();
      notifyAuthError("Failed to load Google Maps JS API script. Switched to local Leaflet.");
      activateLeaflet("leaflet_satellite");
    };
    document.head.appendChild(script);
  }

  function clearGoogleOverlays() {
    Object.keys(state.googlePolygons).forEach(function (id) {
      state.googlePolygons[id].setMap(null);
    });
    state.googlePolygons = {};
    state.googleHandleMarkers.forEach(function (m) {
      m.setMap(null);
    });
    state.googleHandleMarkers = [];
    if (state.googleStemLine) {
      state.googleStemLine.setMap(null);
      state.googleStemLine = null;
    }
  }

  function renderGoogleBuildings() {
    if (!state.googleMap || !window.google || !window.google.maps) return;
    clearGoogleOverlays();

    state.buildings.forEach(function (bldg) {
      const ring = buildingLatLonRing(bldg);
      if (ring.length < 3) return;
      const isSelected = bldg.id === state.selectedBuildingId;
      const paths = ring.map(function (pt) {
        return { lat: pt[0], lng: pt[1] };
      });
      const poly = new window.google.maps.Polygon({
        paths: paths,
        strokeColor: isSelected ? "#F59E0B" : "#38BDF8",
        strokeOpacity: 0.95,
        strokeWeight: isSelected ? 3 : 2,
        fillColor: isSelected ? "#F59E0B" : "#38BDF8",
        fillOpacity: isSelected ? 0.30 : 0.22,
        map: state.googleMap,
      });
      poly.addListener("click", function () {
        if (state.mode === "pan") {
          handleBuildingClick(bldg.id);
        }
      });
      state.googlePolygons[bldg.id] = poly;

      if (isSelected && bldg.geo_anchor) {
        const moveMarker = new window.google.maps.Marker({
          position: { lat: bldg.geo_anchor.origin_lat, lng: bldg.geo_anchor.origin_lon },
          map: state.googleMap,
          draggable: true,
          title: "Drag to Move Building",
          icon: {
            path: window.google.maps.SymbolPath.CIRCLE,
            scale: 8,
            fillColor: "#38BDF8",
            fillOpacity: 1.0,
            strokeColor: "#FFFFFF",
            strokeWeight: 2,
          },
        });
        const rotLatLon = buildingRotateHandleLatLon(bldg);
        const stemLine = new window.google.maps.Polyline({
          path: [
            { lat: bldg.geo_anchor.origin_lat, lng: bldg.geo_anchor.origin_lon },
            { lat: rotLatLon[0], lng: rotLatLon[1] },
          ],
          strokeColor: "#F59E0B",
          strokeWeight: 2,
          map: state.googleMap,
        });
        state.googleStemLine = stemLine;

        const rotMarker = new window.google.maps.Marker({
          position: { lat: rotLatLon[0], lng: rotLatLon[1] },
          map: state.googleMap,
          draggable: true,
          title: "Drag to Rotate Building (CW from True North)",
          icon: {
            path: window.google.maps.SymbolPath.CIRCLE,
            scale: 8,
            fillColor: "#F59E0B",
            fillOpacity: 1.0,
            strokeColor: "#FFFFFF",
            strokeWeight: 2,
          },
        });

        moveMarker.addListener("drag", function (ev) {
          if (!ev.latLng) return;
          bldg.geo_anchor.origin_lat = ev.latLng.lat();
          bldg.geo_anchor.origin_lon = ev.latLng.lng();
          poly.setPath(
            buildingLatLonRing(bldg).map(function (pt) {
              return { lat: pt[0], lng: pt[1] };
            })
          );
          const updatedRot = buildingRotateHandleLatLon(bldg);
          rotMarker.setPosition({ lat: updatedRot[0], lng: updatedRot[1] });
          stemLine.setPath([
            { lat: bldg.geo_anchor.origin_lat, lng: bldg.geo_anchor.origin_lon },
            { lat: updatedRot[0], lng: updatedRot[1] },
          ]);
        });
        moveMarker.addListener("dragend", function () {
          commitBuildingTransform(bldg);
        });

        rotMarker.addListener("drag", function (ev) {
          if (!ev.latLng) return;
          const deg = computeAzimuthCwDeg(
            bldg.geo_anchor.origin_lat,
            bldg.geo_anchor.origin_lon,
            ev.latLng.lat(),
            ev.latLng.lng()
          );
          bldg.geo_anchor.rotation_deg = deg;
          poly.setPath(
            buildingLatLonRing(bldg).map(function (pt) {
              return { lat: pt[0], lng: pt[1] };
            })
          );
          const snappedRot = buildingRotateHandleLatLon(bldg);
          stemLine.setPath([
            { lat: bldg.geo_anchor.origin_lat, lng: bldg.geo_anchor.origin_lon },
            { lat: snappedRot[0], lng: snappedRot[1] },
          ]);
          hudMode.textContent = "Rotate: " + deg.toFixed(1) + "°";
        });
        rotMarker.addListener("dragend", function () {
          updateHud();
          const snappedRot = buildingRotateHandleLatLon(bldg);
          rotMarker.setPosition({ lat: snappedRot[0], lng: snappedRot[1] });
          commitBuildingTransform(bldg);
        });

        state.googleHandleMarkers.push(moveMarker, rotMarker);
      }
    });
  }

  function renderGoogleTrace() {
    if (!state.googleMap || !window.google || !window.google.maps) return;
    if (state.googleTracePolyline) {
      state.googleTracePolyline.setMap(null);
      state.googleTracePolyline = null;
    }
    state.googleTraceMarkers.forEach(function (m) {
      m.setMap(null);
    });
    state.googleTraceMarkers = [];
    if (state.mode !== "trace" || state.tracePoints.length === 0) return;

    const path = state.tracePoints.map(function (pt) {
      return { lat: pt[0], lng: pt[1] };
    });
    state.googleTracePolyline = new window.google.maps.Polyline({
      path: path,
      strokeColor: "#22C55E",
      strokeWeight: 2,
      map: state.googleMap,
    });
    state.tracePoints.forEach(function (pt, idx) {
      const m = new window.google.maps.Marker({
        position: { lat: pt[0], lng: pt[1] },
        map: state.googleMap,
        icon: {
          path: window.google.maps.SymbolPath.CIRCLE,
          scale: idx === 0 ? 7 : 5,
          fillColor: idx === 0 ? "#22C55E" : "#38BDF8",
          fillOpacity: 1.0,
          strokeColor: "#FFFFFF",
          strokeWeight: 2,
        },
      });
      if (idx === 0) {
        m.addListener("click", function () {
          if (state.tracePoints.length >= 3) {
            finishTrace();
          }
        });
      }
      state.googleTraceMarkers.push(m);
    });
  }

  // --- Lightweight HTML5 <canvas> Slippy-Tile Grid Fallback (when Leaflet absent) ---

  function canvasLatLonToScreen(lat, lon) {
    const w = elCanvas.width || elCanvas.clientWidth || 800;
    const h = elCanvas.height || elCanvas.clientHeight || 600;
    const centerPx = latLonToWorldPx(state.centerLat, state.centerLon, state.zoom);
    const ptPx = latLonToWorldPx(lat, lon, state.zoom);
    return {
      x: w * 0.5 + (ptPx.x - centerPx.x),
      y: h * 0.5 + (ptPx.y - centerPx.y),
    };
  }

  function canvasScreenToLatLon(sx, sy) {
    const w = elCanvas.width || elCanvas.clientWidth || 800;
    const h = elCanvas.height || elCanvas.clientHeight || 600;
    const centerPx = latLonToWorldPx(state.centerLat, state.centerLon, state.zoom);
    return worldPxToLatLon(centerPx.x + (sx - w * 0.5), centerPx.y + (sy - h * 0.5), state.zoom);
  }

  function renderCanvasFallback() {
    if (state.activeEngine !== "canvas") return;
    const w = elCanvas.clientWidth || 800;
    const h = elCanvas.clientHeight || 600;
    if (elCanvas.width !== w || elCanvas.height !== h) {
      elCanvas.width = w;
      elCanvas.height = h;
    }
    const ctx = elCanvas.getContext("2d");
    if (!ctx) return;

    ctx.fillStyle = "#0F172A";
    ctx.fillRect(0, 0, w, h);

    const z = Math.max(2, Math.min(19, Math.round(state.zoom)));
    const n = Math.pow(2, z);
    const centerPx = latLonToWorldPx(state.centerLat, state.centerLon, z);
    const topLeftWx = centerPx.x - w * 0.5;
    const topLeftWy = centerPx.y - h * 0.5;
    const minTx = Math.floor(topLeftWx / 256);
    const maxTx = Math.floor((topLeftWx + w) / 256);
    const minTy = Math.floor(topLeftWy / 256);
    const maxTy = Math.floor((topLeftWy + h) / 256);

    const provider = state.config.preferred_provider === "leaflet_osm" ? "osm" : "esri_satellite";
    const base = (state.config.tile_base_url || window.location.origin).replace(/\/$/, "");

    for (let tx = minTx; tx <= maxTx; tx++) {
      for (let ty = minTy; ty <= maxTy; ty++) {
        if (tx < 0 || ty < 0 || tx >= n || ty >= n) continue;
        const sx = tx * 256 - topLeftWx;
        const sy = ty * 256 - topLeftWy;
        const key = provider + "/" + z + "/" + tx + "/" + ty;
        let img = state.canvasTiles[key];
        if (!img) {
          img = new Image();
          img.onload = function () {
            renderCanvasFallback();
          };
          img.src = base + "/tiles/" + key;
          state.canvasTiles[key] = img;
        }
        if (img.complete && img.naturalWidth > 0) {
          ctx.drawImage(img, sx, sy, 256, 256);
        } else {
          ctx.strokeStyle = "#1E293B";
          ctx.strokeRect(sx, sy, 256, 256);
        }
      }
    }

    // Draw buildings
    state.buildings.forEach(function (bldg) {
      const ring = buildingLatLonRing(bldg);
      if (ring.length < 3) return;
      const isSelected = bldg.id === state.selectedBuildingId;
      ctx.beginPath();
      ring.forEach(function (pt, idx) {
        const sp = canvasLatLonToScreen(pt[0], pt[1]);
        if (idx === 0) ctx.moveTo(sp.x, sp.y);
        else ctx.lineTo(sp.x, sp.y);
      });
      ctx.closePath();
      ctx.fillStyle = isSelected ? "rgba(245, 158, 11, 0.32)" : "rgba(56, 189, 248, 0.24)";
      ctx.fill();
      ctx.strokeStyle = isSelected ? "#F59E0B" : "#38BDF8";
      ctx.lineWidth = isSelected ? 2.5 : 1.5;
      ctx.stroke();

      if (isSelected && bldg.geo_anchor) {
        const centerSp = canvasLatLonToScreen(
          bldg.geo_anchor.origin_lat,
          bldg.geo_anchor.origin_lon
        );
        const rotLL = buildingRotateHandleLatLon(bldg);
        const rotSp = canvasLatLonToScreen(rotLL[0], rotLL[1]);

        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.moveTo(centerSp.x, centerSp.y);
        ctx.lineTo(rotSp.x, rotSp.y);
        ctx.strokeStyle = "#F59E0B";
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.beginPath();
        ctx.arc(centerSp.x, centerSp.y, 7, 0, Math.PI * 2);
        ctx.fillStyle = "#38BDF8";
        ctx.fill();
        ctx.strokeStyle = "#FFFFFF";
        ctx.stroke();

        ctx.beginPath();
        ctx.arc(rotSp.x, rotSp.y, 7, 0, Math.PI * 2);
        ctx.fillStyle = "#F59E0B";
        ctx.fill();
        ctx.strokeStyle = "#FFFFFF";
        ctx.stroke();
      }
    });

    // Draw trace preview
    if (state.mode === "trace" && state.tracePoints.length > 0) {
      ctx.beginPath();
      state.tracePoints.forEach(function (pt, idx) {
        const sp = canvasLatLonToScreen(pt[0], pt[1]);
        if (idx === 0) ctx.moveTo(sp.x, sp.y);
        else ctx.lineTo(sp.x, sp.y);
      });
      if (state.tracePoints.length >= 3) ctx.closePath();
      ctx.strokeStyle = "#22C55E";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }

  function activateCanvasFallback() {
    setActiveLayer("canvas");
    notifyProviderSwitched("canvas");
    renderCanvasFallback();
  }

  elCanvas.addEventListener("mousedown", function (ev) {
    state.canvasDragging = true;
    state.canvasDragMode = "pan";
    state.canvasLastMouse = { x: ev.clientX, y: ev.clientY };
  });
  window.addEventListener("mousemove", function (ev) {
    if (!state.canvasDragging || state.activeEngine !== "canvas") return;
    const dx = ev.clientX - state.canvasLastMouse.x;
    const dy = ev.clientY - state.canvasLastMouse.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) {
      const centerPx = latLonToWorldPx(state.centerLat, state.centerLon, state.zoom);
      const next = worldPxToLatLon(centerPx.x - dx, centerPx.y - dy, state.zoom);
      state.centerLat = next[0];
      state.centerLon = next[1];
      state.canvasLastMouse = { x: ev.clientX, y: ev.clientY };
      renderCanvasFallback();
    }
  });
  window.addEventListener("mouseup", function () {
    state.canvasDragging = false;
  });
  elCanvas.addEventListener("click", function (ev) {
    if (state.activeEngine !== "canvas") return;
    const rect = elCanvas.getBoundingClientRect();
    const ll = canvasScreenToLatLon(ev.clientX - rect.left, ev.clientY - rect.top);
    handleMapClick(ll[0], ll[1]);
  });
  elCanvas.addEventListener("wheel", function (ev) {
    if (state.activeEngine !== "canvas") return;
    ev.preventDefault();
    state.zoom = Math.max(4, Math.min(19, state.zoom + (ev.deltaY < 0 ? 1 : -1)));
    renderCanvasFallback();
  });

  // --- Unified Renderers & Public API ---

  function renderTraceOverlay() {
    if (state.activeEngine === "google") renderGoogleTrace();
    else if (state.activeEngine === "canvas") renderCanvasFallback();
    else renderLeafletTrace();
  }

  function renderAll() {
    if (state.activeEngine === "google") {
      renderGoogleBuildings();
      renderGoogleTrace();
    } else if (state.activeEngine === "canvas") {
      renderCanvasFallback();
    } else if (state.leafletMap) {
      renderLeafletBuildings();
      renderLeafletTrace();
    }
  }

  function centerOnFirstAnchoredBuilding() {
    if (state.hasCenteredInitially) return;
    for (let i = 0; i < state.buildings.length; i++) {
      const b = state.buildings[i];
      if (b.geo_anchor) {
        state.hasCenteredInitially = true;
        jumpToLatLon(b.geo_anchor.origin_lat, b.geo_anchor.origin_lon, DEFAULT_ZOOM);
        return;
      }
    }
  }

  function configureMap(cfg) {
    state.config = Object.assign({}, state.config, cfg || {});
    const pref = state.config.preferred_provider || "google";
    const key = (state.config.google_api_key || "").trim();
    const offline = Boolean(state.config.offline_mode);

    if (pref === "google" && key && !offline) {
      activateGoogle(key);
    } else if (pref === "leaflet_osm") {
      clearGoogleWatchdog();
      activateLeaflet("leaflet_osm");
    } else {
      clearGoogleWatchdog();
      activateLeaflet("leaflet_satellite");
    }
  }

  function syncBuildings(buildings, selectedBuildingId) {
    state.buildings = Array.isArray(buildings) ? buildings : [];
    state.selectedBuildingId = selectedBuildingId || null;
    centerOnFirstAnchoredBuilding();
    renderAll();
  }

  function selectBuilding(buildingId) {
    state.selectedBuildingId = buildingId || null;
    renderAll();
  }

  function jumpToLatLon(lat, lon, zoom) {
    state.centerLat = Number(lat);
    state.centerLon = Number(lon);
    if (typeof zoom === "number") {
      state.zoom = zoom;
    }
    if (state.activeEngine === "google" && state.googleMap) {
      state.googleMap.panTo({ lat: state.centerLat, lng: state.centerLon });
      state.googleMap.setZoom(state.zoom);
    } else if (state.leafletMap) {
      state.leafletMap.setView([state.centerLat, state.centerLon], state.zoom);
    } else if (state.activeEngine === "canvas") {
      renderCanvasFallback();
    }
    updateHud();
  }

  function setMode(mode, buildingId) {
    state.mode = mode || "pan";
    state.placeBuildingId = mode === "place" ? (buildingId || null) : null;
    if (mode !== "trace") {
      state.tracePoints = [];
    }
    updateHud();
    renderAll();
  }

  // Wire HUD buttons
  document.getElementById("btn-trace-finish").addEventListener("click", finishTrace);
  document.getElementById("btn-trace-undo").addEventListener("click", function () {
    state.tracePoints.pop();
    updateHud();
    renderTraceOverlay();
  });
  document.getElementById("btn-trace-cancel").addEventListener("click", cancelMode);
  document.getElementById("btn-place-cancel").addEventListener("click", cancelMode);

  window.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && state.mode !== "pan") {
      cancelMode();
    } else if (ev.key === "Enter" && state.mode === "trace" && state.tracePoints.length >= 3) {
      finishTrace();
    }
  });

  window.netvizMap = {
    configureMap: configureMap,
    syncBuildings: syncBuildings,
    selectBuilding: selectBuilding,
    jumpToLatLon: jumpToLatLon,
    setMode: setMode,
    localMToLatLon: localMToLatLon,
    latLonToLocalM: latLonToLocalM,
  };

  // Connect QWebChannel and fire on_map_ready()
  function initChannel() {
    if (typeof window.qt !== "undefined" && typeof window.QWebChannel !== "undefined") {
      new window.QWebChannel(window.qt.webChannelTransport, function (channel) {
        state.bridge = channel.objects.bridge;
        activateLeaflet("leaflet_satellite");
        if (state.bridge && state.bridge.on_map_ready) {
          state.bridge.on_map_ready();
        }
      });
    } else {
      activateLeaflet("leaflet_satellite");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChannel);
  } else {
    initChannel();
  }
})();
