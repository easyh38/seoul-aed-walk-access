// Leaflet 지도: 레이어 겹침 순서, AED 마커, 도달 구역·사각지대, 직선거리 원, 경로 (PRD 5장 ④, 6-2)
/* global L */

import { icon } from "./icons.js";

const SEOUL_CENTER = [37.5665, 126.978];
const SEOUL_BOUNDS = [[37.38, 126.68], [37.75, 127.25]];
export const CIRCLE_MIN_ZOOM = 15; // 직선거리 원은 동네 수준으로 확대했을 때만 (S-03)
export const AED_MIN_ZOOM = 13; // 서울 전체 화면에서는 점이 지도를 덮어 도달 구역이 안 보인다

// 겹침 순서(아래 → 위): 타일 → 사각지대 → 도달 구역 → 직선거리 원 → 경로 → AED → 목적지 → 사용자 핀
const PANES = { deadzone: 410, coverage: 420, circles: 430, route: 440, aeds: 450, dest: 460, pin: 470 };

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export class MapView {
  constructor(el, { onMapClick, onAedClick }) {
    this.colors = {
      aed: cssVar("--map-aed"),
      closed: cssVar("--map-aed-closed"),
      coverageFill: cssVar("--map-coverage-fill"),
      coverageLine: cssVar("--map-coverage-line"),
      coverageOpacity: Number(cssVar("--map-coverage-opacity")),
      deadzoneFill: cssVar("--map-deadzone-fill"),
      deadzoneOpacity: Number(cssVar("--map-deadzone-opacity")),
      circle: cssVar("--map-circle"),
      route: cssVar("--map-route"),
      casing: cssVar("--map-route-casing"),
      dest: cssVar("--map-dest"),
    };

    this.map = L.map(el, {
      zoomControl: false,
      minZoom: 10,
      maxZoom: 19,
      maxBounds: L.latLngBounds(SEOUL_BOUNDS).pad(0.6),
      preferCanvas: true,
    }).setView(SEOUL_CENTER, 11);

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(this.map);
    this.map.attributionControl.setPrefix(false);

    this.renderers = {};
    for (const [name, z] of Object.entries(PANES)) {
      const pane = this.map.createPane(name);
      pane.style.zIndex = String(z);
      pane.classList.add(`leaflet-${name}-pane`);
      this.renderers[name] = L.canvas({ pane: name, padding: 0.3 });
    }

    this.groups = {
      deadzone: L.layerGroup().addTo(this.map),
      coverage: L.layerGroup().addTo(this.map),
      circles: L.layerGroup().addTo(this.map),
      route: L.layerGroup().addTo(this.map),
      aeds: L.layerGroup().addTo(this.map),
      dest: L.layerGroup().addTo(this.map),
      pin: L.layerGroup().addTo(this.map),
    };

    this.onAedClick = onAedClick;
    this.markers = new Map(); // aed id → circleMarker
    this.map.on("click", (e) => onMapClick(e.latlng));
    this.map.on("zoomend", () => this._restyleMarkers());
  }

  // ---------------------------------------------------------- 공통

  bbox() {
    const b = this.map.getBounds();
    return { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() };
  }

  zoom() {
    return this.map.getZoom();
  }

  zoomBy(delta) {
    this.map.setZoom(this.map.getZoom() + delta);
  }

  /** padding: { paddingTopLeft, paddingBottomRight } (모바일 하단 시트에 가리는 만큼 비워 둔다) */
  fitBounds(bounds, padding = { padding: [32, 32] }) {
    this.map.flyToBounds(bounds, { ...padding, duration: reducedMotion() ? 0 : 0.6, maxZoom: 16 });
  }

  /** 실시간 반영 ON/OFF 때 레이어를 투명도로 바꿔 변화가 보이게 한다 */
  async fade(names, swap) {
    const panes = names.map((n) => this.map.getPane(n));
    panes.forEach((p) => p.classList.add("is-fading"));
    await new Promise((r) => setTimeout(r, reducedMotion() ? 0 : 250));
    try {
      await swap();
    } finally {
      panes.forEach((p) => p.classList.remove("is-fading"));
    }
  }

  // ---------------------------------------------------------- AED 마커

  _markerStyle(status) {
    const z = this.map.getZoom();
    const closed = status === "unavailable";
    return {
      radius: z < 15 ? 5 : 7,
      weight: 2,
      color: "#FFFFFF",
      fillColor: closed ? this.colors.closed : this.colors.aed,
      fillOpacity: 1,
    };
  }

  /** features: /api/aeds의 GeoJSON features. statuses: Map(id → 상태) 또는 null(실시간 반영 OFF) */
  setAeds(features, statuses) {
    this.groups.aeds.clearLayers();
    this.markers.clear();
    for (const f of features) {
      const [lon, lat] = f.geometry.coordinates;
      const p = f.properties;
      const status = statuses ? statuses.get(p.id) || "unavailable" : null;
      const m = L.circleMarker([lat, lon], {
        ...this._markerStyle(status),
        pane: "aeds",
        renderer: this.renderers.aeds,
        bubblingMouseEvents: false, // AED를 눌렀을 때 지도 클릭(찾기)이 같이 실행되지 않게
      });
      m.feature = f;
      m.status = status;
      m.on("click", () => this.onAedClick(f, m));
      m.addTo(this.groups.aeds);
      this.markers.set(p.id, m);
    }
  }

  clearAeds() {
    this.groups.aeds.clearLayers();
    this.markers.clear();
  }

  _restyleMarkers() {
    for (const m of this.markers.values()) m.setStyle(this._markerStyle(m.status));
  }

  openPopup(marker, html) {
    // Leaflet 기본 닫기 버튼(× 문자) 대신 팝업 안에 SVG 닫기 버튼을 둔다 (PRD 6-5)
    return L.popup({ maxWidth: 320, minWidth: 300, autoPanPadding: [24, 24], closeButton: false })
      .setLatLng(marker.getLatLng())
      .setContent(html)
      .openOn(this.map);
  }

  closePopup() {
    this.map.closePopup();
  }

  // ---------------------------------------------------------- 도달 구역·사각지대

  setPolygon(kind, geojson) {
    const group = this.groups[kind];
    group.clearLayers();
    if (!geojson) return;
    const style = kind === "coverage"
      ? { fillColor: this.colors.coverageFill, fillOpacity: this.colors.coverageOpacity,
          color: this.colors.coverageLine, weight: 1, opacity: 0.6 }
      : { fillColor: this.colors.deadzoneFill, fillOpacity: this.colors.deadzoneOpacity, stroke: false };
    L.geoJSON(geojson, {
      pane: kind,
      renderer: this.renderers[kind],
      interactive: false, // 클릭은 지도로 넘겨 찾기가 동작하게
      style: () => style,
    }).addTo(group);
  }

  // ---------------------------------------------------------- 직선거리 원 (S-03)

  setCircles(features, radiusM, statuses) {
    const group = this.groups.circles;
    group.clearLayers();
    if (!features || this.map.getZoom() < CIRCLE_MIN_ZOOM) return;
    for (const f of features) {
      if (statuses && (statuses.get(f.properties.id) || "unavailable") === "unavailable") continue;
      const [lon, lat] = f.geometry.coordinates;
      L.circle([lat, lon], {
        radius: radiusM,
        pane: "circles",
        renderer: this.renderers.circles,
        interactive: false,
        fill: false,
        color: this.colors.circle,
        weight: 1.5,
        dashArray: "4 4",
        opacity: 0.9,
      }).addTo(group);
    }
  }

  // ---------------------------------------------------------- 찾기 결과: 핀, 경로, 목적지

  setOrigin(latlng) {
    this.groups.pin.clearLayers();
    if (!latlng) return;
    L.marker(latlng, {
      pane: "pin",
      interactive: false,
      keyboard: false,
      icon: L.divIcon({ className: "pin-icon", html: icon("pin", { size: 34 }), iconSize: [34, 34], iconAnchor: [17, 32] }),
    }).addTo(this.groups.pin);
  }

  setRoute(result) {
    this.groups.route.clearLayers();
    this.groups.dest.clearLayers();
    if (!result) return;
    const latlngs = result.route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    // 흰 외곽선 6px 위에 진한 선 4px: 어떤 배경에서도 잘 보이게
    L.polyline(latlngs, { pane: "route", renderer: this.renderers.route, interactive: false,
      color: this.colors.casing, weight: 7, opacity: 1, lineCap: "round", lineJoin: "round" }).addTo(this.groups.route);
    L.polyline(latlngs, { pane: "route", renderer: this.renderers.route, interactive: false,
      color: this.colors.route, weight: 4, opacity: 1, lineCap: "round", lineJoin: "round" }).addTo(this.groups.route);

    const dest = [result.lat, result.lon];
    L.circleMarker(dest, { pane: "dest", renderer: this.renderers.dest, interactive: false,
      radius: 14, color: this.colors.dest, weight: 2, opacity: 0.5, fill: false }).addTo(this.groups.dest);
    L.circleMarker(dest, { pane: "dest", renderer: this.renderers.dest, interactive: false,
      radius: 8, color: "#FFFFFF", weight: 3, fillColor: this.colors.dest, fillOpacity: 1 }).addTo(this.groups.dest);
  }

  clearFind() {
    this.setOrigin(null);
    this.setRoute(null);
  }

  fitRoute(origin, result, padding) {
    const pts = result.route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    pts.push([origin.lat, origin.lng]);
    this.fitBounds(L.latLngBounds(pts), padding);
  }
}

export function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
