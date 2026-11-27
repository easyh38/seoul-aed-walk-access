// Leaflet 지도: 레이어 겹침 순서, AED 마커, 도달 구역·사각지대, 직선거리 원, 경로 (PRD 5장 ④, 6-2)
/* global L */

import { icon } from "./icons.js";

const SEOUL_CENTER = [37.5665, 126.978];
const SEOUL_BOUNDS = [[37.38, 126.68], [37.75, 127.25]];
export const CIRCLE_MIN_ZOOM = 15; // 직선거리 원은 동네 수준으로 확대했을 때만 (S-03)
export const AED_MIN_ZOOM = 13; // 서울 전체 화면에서는 점이 지도를 덮어 도달 구역이 안 보인다

// 겹침 순서(아래 → 위): 타일 → 사각지대 → 도달 구역 → 직선거리 원 → 경로 → AED → 목적지 → 사용자 핀
const PANES = { deadzone: 410, coverage: 420, circles: 430, route: 440, aeds: 450, dest: 460, pin: 470 };

// AED 점 클릭 여유(px): 점 반지름은 5~7px라 조금만 빗나가도 지도 클릭(찾기)이 된다.
// 손가락 입력은 오차가 커서 더 넓게 잡는다
const COARSE = window.matchMedia("(pointer: coarse)").matches;
const AED_TOLERANCE = COARSE ? 12 : 6;

// 배경지도: VWorld(국토교통부) 키가 있으면 VWorld, 없으면 OSM으로 대체한다.
// VWorld 키는 등록한 도메인에서만 동작하므로 브라우저에 노출돼도 된다 (index.html의 meta 태그).
// 도보 경로·도달 구역은 지도와 무관하게 OSM 도로망(Valhalla)으로 계산한다.
function baseTileLayer() {
  const key = document.querySelector('meta[name="vworld-key"]')?.content.trim();
  if (key) {
    return L.tileLayer(`https://api.vworld.kr/req/wmts/1.0.0/${encodeURIComponent(key)}/Base/{z}/{y}/{x}.png`, {
      minZoom: 6,
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.vworld.kr" target="_blank" rel="noopener">VWorld</a>(국토교통부)',
    });
  }
  return L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  });
}

/** 대수를 적은 묶음 마커. HTML 요소(divIcon) 대신 캔버스에 그려 수백 개여도 가볍게 움직인다.
 *  Leaflet 1.9 캔버스 렌더러가 원을 그린 직후 같은 자리에 숫자를 쓴다 (web/vendor의 버전 고정) */
const CountMarker = L.CircleMarker.extend({
  _updatePath() {
    L.CircleMarker.prototype._updatePath.call(this);
    const r = this._renderer;
    if (!r._drawing || this._empty()) return;
    const ctx = r._ctx;
    ctx.save();
    ctx.font = this.options.labelFont;
    ctx.fillStyle = this.options.labelColor;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(this.options.label, this._point.x, this._point.y + 0.5);
    ctx.restore();
  },
});

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export class MapView {
  constructor(el, { onMapClick, onAedClick, onAedGroupClick }) {
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
      group: cssVar("--aed-600"), // 흰 숫자 대비를 위해 점(aed-500)보다 한 단계 진하게
      text: cssVar("--text"),
      font: cssVar("--font"),
    };

    this.map = L.map(el, {
      zoomControl: false,
      minZoom: 10,
      maxZoom: 19,
      maxBounds: L.latLngBounds(SEOUL_BOUNDS).pad(0.6),
      preferCanvas: true,
    }).setView(SEOUL_CENTER, 11);

    baseTileLayer().addTo(this.map);
    this.map.attributionControl.setPrefix(false);

    this.renderers = {};
    for (const [name, z] of Object.entries(PANES)) {
      const pane = this.map.createPane(name);
      pane.style.zIndex = String(z);
      pane.classList.add(`leaflet-${name}-pane`);
      this.renderers[name] = L.canvas({ pane: name, padding: 0.3, tolerance: name === "aeds" ? AED_TOLERANCE : 0 });
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
    this.onAedGroupClick = onAedGroupClick;
    this.markers = new Map(); // aed id → circleMarker (겹친 AED는 묶음 마커)
    this.map.on("click", (e) => onMapClick(e.latlng));
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

  /** features: /api/aeds의 GeoJSON features. statuses: Map(id → 상태) 또는 null(실시간 반영 OFF)
   *  원본 좌표가 건물(단지) 대표점이라 같은 건물의 AED는 한 점에 겹치고, 몇 m 떨어진 AED도 화면에서는 겹친다.
   *  화면에서 겹치는 AED는 대수를 적은 묶음 마커 하나로 보여 주고, 누르면 설치 위치 목록을 연다.
   *  (따로 그리면 아래 깔린 점은 어디를 눌러도 열 수 없다) */
  setAeds(features, statuses) {
    this.groups.aeds.clearLayers();
    this.markers.clear();
    const statusOf = (f) => (statuses ? statuses.get(f.properties.id) || "unavailable" : null);
    const style = this._markerStyle(null);
    // 두 점 중심이 지름 + 테두리보다 가까우면 겹친다
    const minGap = 2 * (style.radius + style.weight) + 2;
    for (const group of groupByScreen(this.map, features, minGap)) {
      const [lon, lat] = centerOf(group);
      if (group.length === 1) {
        const f = group[0];
        const m = L.circleMarker([lat, lon], {
          ...this._markerStyle(statusOf(f)),
          pane: "aeds",
          renderer: this.renderers.aeds,
          bubblingMouseEvents: false, // AED를 눌렀을 때 지도 클릭(찾기)이 같이 실행되지 않게
        });
        m.on("click", () => this.onAedClick(f, m));
        m.addTo(this.groups.aeds);
        this.markers.set(f.properties.id, m);
        continue;
      }
      // 하나라도 운영 중이면 운영 중 색으로 (실시간 반영 OFF면 statuses가 없어 기본 색)
      const allClosed = statuses && group.every((f) => statusOf(f) === "unavailable");
      const small = this.map.getZoom() < 15;
      const m = new CountMarker([lat, lon], {
        pane: "aeds",
        renderer: this.renderers.aeds,
        bubblingMouseEvents: false,
        radius: small ? 8 : 11,
        weight: 2,
        color: "#FFFFFF",
        fillColor: allClosed ? this.colors.closed : this.colors.group,
        fillOpacity: 1,
        label: String(group.length),
        labelColor: allClosed ? this.colors.text : "#FFFFFF",
        labelFont: `600 ${small ? 10 : 12}px ${this.colors.font}`,
      });
      const samePoint = group.every((f) => f.geometry.coordinates.join() === group[0].geometry.coordinates.join());
      m.on("click", () => this.onAedGroupClick(group, m, { samePoint }));
      m.addTo(this.groups.aeds);
      for (const f of group) this.markers.set(f.properties.id, m);
    }
  }

  clearAeds() {
    this.groups.aeds.clearLayers();
    this.markers.clear();
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
    // 한 점에 겹친 AED는 원도 겹치므로 지점마다 하나만 그린다
    for (const group of groupByPoint(features)) {
      const open = statuses
        ? group.filter((f) => (statuses.get(f.properties.id) || "unavailable") !== "unavailable")
        : group;
      if (!open.length) continue;
      const [lon, lat] = open[0].geometry.coordinates;
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

/** 화면에서 minGap(px)보다 가까운 AED끼리 묶는다. 묶음의 첫 점을 기준으로, 격자로 이웃만 확인한다 */
function groupByScreen(map, features, minGap) {
  const cells = new Map();
  const groups = [];
  for (const f of features) {
    const [lon, lat] = f.geometry.coordinates;
    const p = map.project([lat, lon], map.getZoom());
    const cx = Math.floor(p.x / minGap);
    const cy = Math.floor(p.y / minGap);
    let hit = null;
    for (let dx = -1; dx <= 1 && !hit; dx++) {
      for (let dy = -1; dy <= 1 && !hit; dy++) {
        for (const g of cells.get(`${cx + dx},${cy + dy}`) || []) {
          if (g.p.distanceTo(p) < minGap) { hit = g; break; }
        }
      }
    }
    if (hit) {
      hit.items.push(f);
      continue;
    }
    const g = { p, items: [f] };
    groups.push(g);
    const key = `${cx},${cy}`;
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(g);
  }
  return groups.map((g) => g.items);
}

function centerOf(group) {
  const lon = group.reduce((s, f) => s + f.geometry.coordinates[0], 0) / group.length;
  const lat = group.reduce((s, f) => s + f.geometry.coordinates[1], 0) / group.length;
  return [lon, lat];
}

/** 좌표가 똑같은 AED끼리 묶는다 (입력 순서 유지) */
export function groupByPoint(features) {
  const groups = new Map();
  for (const f of features) {
    const key = f.geometry.coordinates.join(",");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(f);
  }
  return [...groups.values()];
}

export function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
