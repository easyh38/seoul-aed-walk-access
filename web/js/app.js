// 화면 전체 흐름: 탭, 레이어, 찾기, AED 팝업, 오류 제보, 실시간 반영, 통계, 모바일 하단 시트 (PRD 5장, 7-2)

import { api, ApiError, MESSAGES, nearestErrorMessage } from "./api.js";
import {
  count, dateLabel, duration, esc, hoursRows, km2, meters, num1, pct, pp, ppAbs, seoulTime, statusPill,
} from "./format.js";
import { hydrateIcons, icon } from "./icons.js";
import { AED_MIN_ZOOM, CIRCLE_MIN_ZOOM, MapView } from "./map.js";

const MODES = {
  oneway: { minutes: 4, radius: 340, label: "편도 4분",
    desc: "AED까지 걸어서 4분 안에 닿는지로 판정합니다." },
  roundtrip: { minutes: 2, radius: 170, label: "왕복 기준 (편도 2분)",
    desc: "AED를 가지고 돌아오는 시간까지 생각해, 편도 2분 안에 닿는지로 판정합니다." },
};

const state = {
  mode: "oneway",
  live: false,
  liveAt: null,
  layers: { aeds: true, coverage: true, circles: false, deadzone: false },
  aedFeatures: [],
  statuses: null, // 실시간 반영 ON일 때 Map(id → available | unknown | unavailable)
  origin: null, // 찾기 출발점 L.LatLng
  originLabel: "",
  lastFind: null, // { aedId }
  pendingAed: null, // "여기까지 경로 보기"를 눌렀는데 출발점이 없을 때 목적지
  stats: {},
  districts: [],
  meta: null,
};

const $ = (sel) => document.querySelector(sel);
const mobileQuery = window.matchMedia("(max-width: 767.98px)");

hydrateIcons();

// Leaflet은 지도를 누르면 지도에 포커스를 준다. 이때 키보드용 포커스 테두리가 지도 전체에 생기지 않게
// 마지막 입력 방식을 기록해 CSS에서 마우스·터치일 때만 지도 테두리를 숨긴다
document.addEventListener("pointerdown", () => document.body.classList.add("pointer-input"), true);
document.addEventListener("keydown", () => document.body.classList.remove("pointer-input"), true);

const view = new MapView($("#map"), { onMapClick, onAedClick, onAedGroupClick });
// 브라우저 콘솔·화면 테스트에서 지도를 직접 움직여 볼 수 있게 열어 둔다 (동작에는 영향 없음)
window.aedApp = { map: view.map };

// ================================================================ 안내 배너, 지도 칩

function showBanner(text, tone = "info", { timeout = 6000 } = {}) {
  const stack = $("#banners");
  while (stack.children.length >= 3) stack.firstElementChild.remove();
  const el = document.createElement("div");
  el.className = `banner banner--${tone}`;
  el.setAttribute("role", tone === "bad" ? "alert" : "status");
  const ic = tone === "bad" ? "warning" : tone === "ok" ? "check" : "info";
  el.innerHTML = `<span class="icon">${icon(ic)}</span><span class="banner__text">${esc(text)}</span>` +
    `<button type="button" aria-label="닫기">${icon("close", { size: 16 })}</button>`;
  el.querySelector("button").addEventListener("click", () => el.remove());
  stack.append(el);
  if (timeout) setTimeout(() => el.remove(), timeout);
}

const chipReasons = new Map(); // 여러 안내가 겹치면 가장 최근 것을 보여 준다
function setChip(reason, html) {
  if (html) chipReasons.set(reason, html);
  else chipReasons.delete(reason);
  const chip = $("#map-chip");
  const last = [...chipReasons.values()].pop();
  chip.hidden = !last;
  chip.innerHTML = last || "";
}

// ================================================================ 탭, 모바일 시트

function setTab(name) {
  document.querySelectorAll(".tab").forEach((btn) => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-selected", String(on));
    btn.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("is-active", p.id === `tab-${name}`));
  if (name === "stats") loadStats();
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    setTab(btn.dataset.tab);
    setSheet(true);
  });
  btn.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const tabs = [...document.querySelectorAll(".tab")];
    const i = tabs.indexOf(btn) + (e.key === "ArrowRight" ? 1 : -1);
    const next = tabs[(i + tabs.length) % tabs.length];
    next.focus();
    setTab(next.dataset.tab);
  });
});

function setSheet(open) {
  if (!mobileQuery.matches) return;
  $("#panel").classList.toggle("is-open", open);
  $("#sheet-handle").setAttribute("aria-expanded", String(open));
}
$("#sheet-handle").addEventListener("click", () => setSheet(!$("#panel").classList.contains("is-open")));

// 모바일에서는 실시간 반영 토글을 지도 위로 옮긴다 (PRD 5장 모바일 화면)
function placeLiveDock() {
  const dock = $("#live-dock");
  if (mobileQuery.matches) {
    $("#map-wrap").append(dock);
    dock.classList.add("is-floating");
  } else {
    $("#panel").append(dock);
    dock.classList.remove("is-floating");
    $("#panel").classList.remove("is-open");
  }
}
mobileQuery.addEventListener("change", placeLiveDock);
placeLiveDock();

function fitPadding() {
  if (!mobileQuery.matches) return { paddingTopLeft: [48, 48], paddingBottomRight: [48, 48] };
  const sheet = $("#panel").getBoundingClientRect();
  const mapBox = $("#map").getBoundingClientRect();
  const covered = Math.max(0, mapBox.bottom - sheet.top);
  return { paddingTopLeft: [24, 72], paddingBottomRight: [24, covered + 24] };
}

// ================================================================ 분석 기준, 레이어 켜기/끄기

function radiusFor(mode) {
  const cfg = state.meta?.config?.modes?.[mode];
  return cfg?.straight_radius_m || MODES[mode].radius;
}

function renderModeTexts() {
  const m = MODES[state.mode];
  $("#mode-desc").textContent = m.desc;
  $("[data-minutes-text]").textContent = `걸어서 ${m.minutes}분 안에 AED에 닿는 곳`;
  $("[data-minutes-text-dead]").textContent = `${m.minutes}분 안에 어떤 AED에도 닿지 못하는 곳`;
  $("[data-radius-text]").textContent = `AED마다 반경 ${Math.round(radiusFor(state.mode))}m. 동네 수준으로 확대하면\u00A0보입니다`;
}

document.querySelectorAll(".segmented [data-mode]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (state.mode === btn.dataset.mode) return;
    state.mode = btn.dataset.mode;
    document.querySelectorAll(".segmented [data-mode]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
    renderModeTexts();
    refreshPolygons();
    renderCircles();
    if (state.lastResult) renderFindResult(state.lastResult); // 골든타임 배지 기준이 바뀐다
    if ($("#tab-stats").classList.contains("is-active")) loadStats();
  });
});

document.querySelectorAll(".switch[data-layer]").forEach((sw) => {
  sw.addEventListener("click", () => {
    const name = sw.dataset.layer;
    state.layers[name] = !state.layers[name];
    sw.setAttribute("aria-checked", String(state.layers[name]));
    if (name === "coverage" || name === "deadzone") refreshPolygon(name);
    else loadAeds({ force: true });
  });
});

// ================================================================ 도달 구역·사각지대

const polygonCache = new Map();
const polygonToken = { coverage: 0, deadzone: 0 };

async function refreshPolygon(kind, { animate = false } = {}) {
  const token = ++polygonToken[kind];
  if (!state.layers[kind]) {
    view.setPolygon(kind, null);
    return;
  }
  const key = `${kind}:${state.mode}`;
  try {
    let data;
    if (!state.live && polygonCache.has(key)) {
      data = polygonCache.get(key);
    } else {
      if (state.live) setChip("live-poly", `${icon("clock", { size: 16 })} 지금 운영 중인 AED로 다시 계산하는 중`);
      data = await api.layer(kind, state.mode, { openNow: state.live });
      if (!state.live) polygonCache.set(key, data);
      const at = data.features?.[0]?.properties?.at;
      if (state.live && at) setLiveAt(at);
    }
    if (token !== polygonToken[kind]) return;
    if (animate) await view.fade([kind], () => view.setPolygon(kind, data));
    else view.setPolygon(kind, data);
  } catch (err) {
    if (token === polygonToken[kind]) {
      showBanner(err instanceof ApiError && err.status === 404 ? err.message : MESSAGES.server, "bad");
    }
  } finally {
    if (state.live) setChip("live-poly", null);
  }
}

// 도달 구역을 먼저 받는다: 실시간 반영 첫 요청은 서버가 계산해 캐시에 넣으므로,
// 사각지대는 그 캐시를 그대로 쓴다 (동시에 보내면 같은 계산을 두 번 한다)
async function refreshPolygons(opts) {
  await refreshPolygon("coverage", opts);
  await refreshPolygon("deadzone", opts);
}

// ================================================================ AED 마커, 직선거리 원

// AED 목록은 서울 전체를 처음 한 번만 받는다 (gzip 약 0.7MB).
// 화면 범위마다 다시 받으면 지도를 움직일 때마다 수 MB를 받고 마커를 새로 만들어 버벅인다.
const SEOUL_BBOX = { west: 126.6, south: 37.35, east: 127.3, north: 37.8 };
let allAedsPromise = null; // Promise<features>
let liveStatusPromise = null; // Promise<{ at, statuses: Map }>: 실시간 반영을 켤 때마다 새로 받는다
let drawn = null; // 지금 그려 둔 범위·줌·조건. 이 안에서 움직이면 다시 그리지 않는다
let aedSeq = 0;

function allAeds() {
  if (!allAedsPromise) {
    allAedsPromise = api.aeds(SEOUL_BBOX).then((r) => r.features);
    allAedsPromise.catch(() => { allAedsPromise = null; }); // 실패하면 다음에 다시 시도
  }
  return allAedsPromise;
}

function liveStatuses() {
  if (!liveStatusPromise) {
    // open_now=true는 운영 중(available·unknown)만 돌려준다. 목록에 없는 AED는 운영 종료(회색)
    liveStatusPromise = api.aeds(SEOUL_BBOX, { openNow: true }).then((r) => ({
      at: r.open_now?.at,
      statuses: new Map(r.features.map((f) => [f.properties.id, f.properties.open_status])),
    }));
    liveStatusPromise.catch(() => { liveStatusPromise = null; });
  }
  return liveStatusPromise;
}

function needAeds() {
  const z = view.zoom();
  return (state.layers.aeds && z >= AED_MIN_ZOOM) || (state.layers.circles && z >= CIRCLE_MIN_ZOOM);
}

/** force: 레이어 켜기/끄기, 실시간 반영 전환처럼 같은 범위라도 다시 그려야 할 때 */
async function loadAeds({ animate = false, force = false } = {}) {
  const seq = ++aedSeq;
  setChip("aeds", state.layers.aeds && view.zoom() < AED_MIN_ZOOM ? "확대하면 AED 위치가 보입니다" : null);
  if (!needAeds()) {
    drawn = null;
    view.clearAeds();
    state.aedFeatures = [];
    renderCircles();
    return;
  }
  const bounds = view.map.getBounds();
  const zoom = view.zoom();
  const key = `${state.live}|${state.layers.aeds}`;
  if (!force && drawn && drawn.zoom === zoom && drawn.key === key && drawn.bounds.contains(bounds)) return;

  try {
    const [features, live] = await Promise.all([allAeds(), state.live ? liveStatuses() : null]);
    if (seq !== aedSeq) return;
    if (live?.at) setLiveAt(live.at);
    // 화면보다 사방으로 30%씩 넓게 그려 두고, 그 안에서 움직이는 동안은 다시 그리지 않는다
    const padded = bounds.pad(0.3);
    const visible = features.filter((f) => {
      const [lon, lat] = f.geometry.coordinates;
      return padded.contains([lat, lon]);
    });
    const swap = () => {
      state.aedFeatures = visible;
      state.statuses = live ? live.statuses : null;
      if (state.layers.aeds) view.setAeds(visible, state.statuses);
      else view.clearAeds();
      renderCircles();
      drawn = { bounds: padded, zoom, key };
    };
    if (animate) await view.fade(["aeds", "circles"], swap);
    else swap();
  } catch (err) {
    if (seq === aedSeq) showBanner(MESSAGES.server, "bad");
  }
}

function scheduleAeds() {
  clearTimeout(aedTimer);
  aedTimer = setTimeout(loadAeds, 150);
}
let aedTimer = null;

function renderCircles() {
  const wantCircles = state.layers.circles;
  const zoomedOut = view.zoom() < CIRCLE_MIN_ZOOM;
  setChip("circles", wantCircles && zoomedOut ? "확대하면 직선거리 비교 원이 보입니다" : null);
  view.setCircles(wantCircles ? state.aedFeatures : null, radiusFor(state.mode), state.statuses);
}

view.map.on("moveend", scheduleAeds);

$("#zoom-in").addEventListener("click", () => view.zoomBy(1));
$("#zoom-out").addEventListener("click", () => view.zoomBy(-1));

// ================================================================ AED 상세 팝업

let lastPopupClose = 0;
view.map.on("popupclose", () => { lastPopupClose = performance.now(); });

// 좌표 출처·위치 의심 안내 (02_refine_location). 팝업과 찾기 결과 카드가 같이 쓴다
function locationNoteHtml(p, className) {
  const flags = p.location_flags || [];
  if (flags.length) {
    return `<div class="${className} ${className}--warn">${icon("warning", { size: 16 })}
      <span>지도 위치가 실제 설치 위치와 다를 수 있습니다. 설치 장소 안내를 함께 확인하세요.</span></div>`;
  }
  if (p.location_source === "osm_building") {
    return `<div class="${className}">${icon("info", { size: 16 })}
      <span>설치 장소의 동 이름으로 지도 위치를 보정했습니다.</span></div>`;
  }
  return "";
}

function liveStatusOf(feature) {
  return state.live ? state.statuses?.get(feature.properties.id) || "unavailable" : undefined;
}

function popupHtml(feature, status, { back = 0 } = {}) {
  const p = feature.properties;
  const hours = hoursRows(p.hours)
    .map(([d, t]) => `<li><b>${esc(d)}</b><span>${esc(t)}</span></li>`).join("");
  const statusHtml = status === undefined
    ? '<span class="pill pill--muted">확인 중</span>'
    : statusPill(status) || '<span class="pill pill--muted">확인 불가</span>';
  return `
    <div class="aed-pop">
      <button class="icon-btn aed-pop__close" type="button" data-act="close" aria-label="닫기">${icon("close")}</button>
      ${back ? `<button class="btn btn--ghost aed-pop__back" type="button" data-act="back">${icon("back", { size: 16 })}AED ${back}대 목록으로</button>` : ""}
      <h3 class="title">${esc(p.place || "설치 장소 정보 없음")}</h3>
      <p class="small muted">${esc(p.address || "")}</p>
      ${locationNoteHtml(p, "aed-pop__note")}
      <ul class="aed-pop__hours" aria-label="운영시간">${hours}</ul>
      <div class="aed-pop__status"><span class="muted">현재 상태</span><span data-status>${statusHtml}</span></div>
      <div class="aed-pop__actions">
        <button class="btn btn--primary" type="button" data-act="route">여기까지 경로 보기</button>
        <button class="btn btn--ghost" type="button" data-act="report">오류 제보</button>
      </div>
    </div>`;
}

/** 화면에서 겹친 AED 목록 (같은 건물·단지이거나 몇 m 거리). 하나를 고르면 그 AED의 상세 팝업으로 바뀐다 */
function onAedGroupClick(features, marker, { samePoint = true } = {}) {
  const sorted = [...features].sort((a, b) =>
    (a.properties.place || "").localeCompare(b.properties.place || "", "ko", { numeric: true }));
  const addresses = new Set(sorted.map((f) => f.properties.address || ""));
  const items = sorted.map((f, i) => {
    const pill = state.live ? statusPill(liveStatusOf(f)) || "" : "";
    return `<li><button type="button" data-idx="${i}">
      <span class="aed-stack-list__place">${esc(f.properties.place || "설치 장소 정보 없음")}</span>${pill}</button></li>`;
  }).join("");
  const flagged = sorted.find((f) => (f.properties.location_flags || []).length);
  const html = `
    <div class="aed-pop">
      <button class="icon-btn aed-pop__close" type="button" data-act="close" aria-label="닫기">${icon("close")}</button>
      <h3 class="title">${samePoint ? "이 지점에" : "이 근처에"} AED ${sorted.length}대</h3>
      <p class="small muted">${esc(addresses.size === 1 ? sorted[0].properties.address || "" : "주소가 서로 다른 AED가 함께 있습니다")}</p>
      ${flagged ? locationNoteHtml(flagged.properties, "aed-pop__note") : `
      <div class="aed-pop__note">${icon("info", { size: 16 })}
        <span>${samePoint
          ? "지도 좌표가 건물 단위라 한 지점에 모여 보입니다. 층·동 등 설치 장소를 확인하세요."
          : "가까이 붙어 있어 하나로 묶었습니다. 지도를 더 확대하면 따로 보입니다."}</span></div>`}
      <ul class="aed-stack-list" aria-label="설치 장소 목록">${items}</ul>
    </div>`;
  const popup = view.openPopup(marker, html);
  const root = popup.getElement();
  root.querySelector('[data-act="close"]').addEventListener("click", () => view.closePopup());
  root.querySelectorAll("[data-idx]").forEach((btn) => btn.addEventListener("click", () => {
    onAedClick(sorted[Number(btn.dataset.idx)], marker, { group: features, samePoint });
  }));
}

async function onAedClick(feature, marker, { group, samePoint } = {}) {
  const known = liveStatusOf(feature);
  const popup = view.openPopup(marker, popupHtml(feature, known, { back: group ? group.length : 0 }));
  const root = popup.getElement();
  root.querySelector('[data-act="route"]').addEventListener("click", () => routeToAed(feature));
  root.querySelector('[data-act="report"]').addEventListener("click", () => openReport(feature));
  root.querySelector('[data-act="close"]').addEventListener("click", () => view.closePopup());
  root.querySelector('[data-act="back"]')?.addEventListener("click", () => onAedGroupClick(group, marker, { samePoint }));

  if (known !== undefined) return;
  // 실시간 반영 OFF: 이 AED 하나만 지금 운영 중인지 물어본다 (작은 범위 + open_now)
  const [lon, lat] = feature.geometry.coordinates;
  const d = 0.00001;
  try {
    const res = await api.aeds({ west: lon - d, south: lat - d, east: lon + d, north: lat + d }, { openNow: true });
    const hit = res.features.find((f) => f.properties.id === feature.properties.id);
    const el = root.querySelector("[data-status]");
    if (el) el.innerHTML = statusPill(hit ? hit.properties.open_status : "unavailable");
  } catch {
    const el = root.querySelector("[data-status]");
    if (el) el.innerHTML = '<span class="pill pill--muted">확인 불가</span>';
  }
}

function routeToAed(feature) {
  view.closePopup();
  if (state.origin) {
    findFrom(state.origin, { aedId: feature.properties.id, originLabel: state.originLabel });
    return;
  }
  state.pendingAed = feature;
  setChip("pending", `${icon("pin", { size: 16 })} 출발점을 지도에서 눌러 주세요 ` +
    `<button class="chip-btn" type="button" data-cancel>취소</button>`);
  $("#map-chip [data-cancel]").addEventListener("click", () => {
    state.pendingAed = null;
    setChip("pending", null);
  });
}

// ================================================================ 찾기 (S-04)

function onMapClick(latlng) {
  // 열려 있던 팝업을 닫으려고 누른 클릭은 찾기로 쓰지 않는다
  if (performance.now() - lastPopupClose < 120) return;
  if (state.pendingAed) {
    const target = state.pendingAed;
    state.pendingAed = null;
    setChip("pending", null);
    findFrom(latlng, { aedId: target.properties.id, originLabel: "지도에서 선택한 위치" });
    return;
  }
  findFrom(latlng, { originLabel: "지도에서 선택한 위치" });
}

let findSeq = 0;

async function findFrom(latlng, { aedId, originLabel = "지도에서 선택한 위치" } = {}) {
  const seq = ++findSeq;
  state.origin = latlng;
  state.originLabel = originLabel;
  state.lastFind = { aedId };
  view.setOrigin(latlng);
  view.setRoute(null);
  setTab("find");
  setSheet(true);
  $("#find-result").innerHTML = `
    <div class="card" aria-busy="true">
      <p class="eyebrow">도보 경로 계산 중</p>
      <div class="skeleton" style="width:70%"></div>
      <div class="skeleton" style="width:40%;height:32px"></div>
      <div class="skeleton" style="width:55%"></div>
    </div>`;

  try {
    const res = await api.nearest(latlng.lat, latlng.lng, { openNow: state.live, aedId });
    if (seq !== findSeq) return;
    state.lastResult = { ...res, aedId, originLabel };
    renderFindResult(state.lastResult);
    view.setRoute(res.nearest);
    view.fitRoute(latlng, res.nearest, fitPadding());
  } catch (err) {
    if (seq !== findSeq) return;
    if (!(err instanceof ApiError)) console.error(err);
    state.lastResult = null;
    const msg = nearestErrorMessage(err);
    $("#find-result").innerHTML = `
      <div class="card">
        <div class="notice notice--bad" role="alert">${icon("warning")}<span>${esc(msg)}</span></div>
        <p class="small muted">출발: ${esc(originLabel)}</p>
      </div>`;
    showBanner(msg, "bad");
  }
}

function goldenPill(seconds) {
  const limit = MODES[state.mode].minutes;
  if (seconds == null) return "";
  return seconds <= limit * 60
    ? `<span class="pill pill--ok">${limit}분 안에 도착</span>`
    : `<span class="pill pill--warn">${limit}분 넘음</span>`;
}

/** 결과 카드 안에 넣는 안내 줄: 위치 의심, 운영 종료, 직선거리 1위와 다름.
 *  따로 상자를 쌓지 않고 카드 안에 구분선 아래 줄로 넣는다 */
function resultNotes(res) {
  const n = res.nearest;
  const sf = res.straight_first;
  const notes = [];
  const loc = locationNoteHtml(n, "result-note");
  if (loc) notes.push(loc);
  if (!state.live && n.open_status === "unavailable") {
    notes.push(`<div class="result-note result-note--info">${icon("clock", { size: 16 })}
      <span>지금 운영 종료입니다. 실시간 반영을 켜면 운영 중인 AED만 찾습니다.</span></div>`);
  }
  if (!res.aedId && res.straight_first_differs && sf) {
    const walkText = sf.walk_time_s == null
      ? "걸어서는 갈 수 없습니다."
      : `실제로 걸으면 <b class="nowrap">${duration(sf.walk_time_s)}</b> 걸립니다.`;
    notes.push(`<div class="result-note result-note--warn">${icon("walk", { size: 16 })}
      <span>직선거리로 가장 가까운 AED는 <b>${esc(sf.place)}</b> <span class="nowrap">(${meters(sf.straight_distance_m)})입니다.</span>
      ${walkText}</span></div>`);
  }
  return notes.length ? `<div class="result-notes">${notes.join("")}</div>` : "";
}

function renderFindResult(res) {
  const n = res.nearest;
  const chosen = Boolean(res.aedId);
  let html = `
    <div class="card">
      <div class="card__head">
        <span class="eyebrow">${chosen ? "선택한 AED까지" : "걸어서 가장 빨리 닿는 AED"}</span>
        ${statusPill(n.open_status)}
      </div>
      <h2 class="title result__place">${esc(n.place || "설치 장소 정보 없음")}</h2>
      <p class="small muted">${esc(n.address || "")}</p>
      <div class="result__time">
        <span class="big-num">${duration(n.walk_time_s)}</span>
        <span class="muted">도보</span>
        ${goldenPill(n.walk_time_s)}
      </div>
      <div class="stat-row small">
        <span><span class="muted">도보 거리</span> <b class="num">${meters(n.walk_distance_m)}</b></span>
        <span><span class="muted">직선거리</span> <b class="num">${meters(n.straight_distance_m)}</b></span>
      </div>
      ${resultNotes(res)}
      <p class="small muted">출발: ${esc(res.originLabel)}${state.live ? " · 지금 운영 중인 AED만" : ""}</p>
    </div>`;

  if (res.alternatives?.length) {
    html += `
      <div class="card">
        <h3 class="title">다른 후보</h3>
        <ul class="alt-list">
          ${res.alternatives.map((a) => `
            <li><button class="alt-item" type="button" data-aed="${a.id}">
              <span class="alt-item__text">
                <span class="alt-item__place">${esc(a.place)}</span>
                <span class="small muted">도보 ${meters(a.walk_distance_m)} · 직선 ${meters(a.straight_distance_m)}</span>
              </span>
              <span class="alt-item__time">${duration(a.walk_time_s)}</span>
              ${statusPill(a.open_status)}
            </button></li>`).join("")}
        </ul>
        <p class="small muted">후보를 누르면 그 AED까지 경로를 보여 줍니다.</p>
      </div>`;
  }

  if (chosen) {
    html += `<button class="btn btn--ghost btn--block" type="button" data-fastest>${icon("search")}가장 빨리 닿는 AED 다시 찾기</button>`;
  }

  const box = $("#find-result");
  box.innerHTML = `<div class="stack result-actions">${html}</div>`;
  box.querySelectorAll("[data-aed]").forEach((b) =>
    b.addEventListener("click", () => findFrom(state.origin, { aedId: Number(b.dataset.aed), originLabel: res.originLabel })));
  box.querySelector("[data-fastest]")?.addEventListener("click", () =>
    findFrom(state.origin, { originLabel: res.originLabel }));
}

// 내 위치 (S-08): HTTPS 또는 localhost에서만 브라우저가 위치를 준다
$("#locate-btn").addEventListener("click", () => {
  if (!window.isSecureContext || !navigator.geolocation) {
    showBanner(MESSAGES.insecure, "info", { timeout: 8000 });
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => findFrom(L.latLng(pos.coords.latitude, pos.coords.longitude), { originLabel: "내 위치" }),
    (err) => showBanner(err.code === err.PERMISSION_DENIED ? MESSAGES.denied : MESSAGES.locateFailed, "bad"),
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
  );
});

// ================================================================ 오류 제보 (S-07)

const dialog = $("#report-dialog");
const form = $("#report-form");
let reportTarget = null;

function openReport(feature) {
  reportTarget = feature;
  view.closePopup();
  form.reset();
  $("#report-count").textContent = "0";
  $("#report-error").hidden = true;
  $("#report-target").textContent = feature.properties.place || `AED ${feature.properties.id}`;
  dialog.showModal();
}

function reportError(text) {
  const el = $("#report-error");
  el.innerHTML = `${icon("warning")}<span>${esc(text)}</span>`;
  el.hidden = false;
}

$("#report-desc").addEventListener("input", (e) => {
  $("#report-count").textContent = String(e.target.value.length);
});
dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const type = form.elements.type.value;
  const description = $("#report-desc").value.trim();
  if (!type) return reportError("오류 유형을 선택해 주세요.");
  if (description.length > 500) return reportError("설명은 500자 이하로 입력해 주세요.");

  const submit = $("#report-submit");
  submit.disabled = true;
  try {
    await api.report({ aed_id: reportTarget.properties.id, type, description: description || null });
    dialog.close();
    showBanner("제보가 접수되었습니다. 확인 후 반영하겠습니다.", "ok");
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) reportError(err.message);
    else if (err instanceof ApiError && err.status === 422) reportError("입력값을 확인해 주세요. 설명은 500자 이하입니다.");
    else reportError(MESSAGES.server);
  } finally {
    submit.disabled = false;
  }
});

// ================================================================ 실시간 반영 (S-06)

function setLiveAt(iso) {
  state.liveAt = iso;
  $("#live-time").textContent = state.live && iso ? `기준: ${seoulTime(iso)}` : "";
}

function renderLiveButton() {
  const btn = $("#live-toggle");
  btn.classList.toggle("btn--dark", state.live);
  btn.classList.toggle("btn--ghost", !state.live);
  btn.setAttribute("aria-pressed", String(state.live));
  // 아이콘 칸(18px)은 그대로 두고 안만 바꾼다: 켤 때 글자가 옆으로 밀리지 않게
  $("#live-icon").innerHTML = state.live ? '<span class="live-dot"></span>' : icon("clock");
  $("#live-label").textContent = state.live ? "실시간 반영 켜짐" : "실시간 반영 꺼짐";
  setLiveAt(state.live ? state.liveAt : null);
}

$("#live-toggle").addEventListener("click", async () => {
  state.live = !state.live;
  state.liveAt = null;
  renderLiveButton();
  if (state.live) {
    showBanner("지금 운영 중인 AED만으로 다시 계산합니다. 처음 한\u00A0번은 몇\u00A0초 걸릴 수 있습니다.", "info");
  }
  // 지금 보이는 레이어는 그대로 두고 계산하다가, 새 결과가 오면 투명도 전환으로 바꾼다
  liveStatusPromise = null; // 켤 때마다 지금 시각 기준으로 새로 받는다
  loadAeds({ animate: true, force: true });
  if (state.origin && state.lastResult) {
    findFrom(state.origin, { aedId: state.lastFind?.aedId, originLabel: state.originLabel });
  }
  await refreshPolygons({ animate: true });
  if ($("#tab-stats").classList.contains("is-active")) loadStats();
});

// ================================================================ 통계 (S-05)

let statsSeq = 0;

async function loadStats() {
  const seq = ++statsSeq;
  const mode = state.mode;
  const live = state.live;
  try {
    if (!state.stats[mode]) {
      $("#stats-summary").innerHTML = '<div class="card"><div class="skeleton"></div><div class="skeleton" style="width:60%"></div></div>';
      state.stats[mode] = await api.stats(mode);
    }
    if (seq !== statsSeq) return;
    renderStats(state.stats[mode]);

    if (live) {
      $("#stats-live").innerHTML = `
        <div class="card" aria-busy="true">
          <p class="eyebrow">평상시 vs 지금</p>
          <p class="small muted">지금 운영 중인 AED 기준 커버리지를 계산하는 중입니다. 처음 한&nbsp;번은 몇&nbsp;초 걸릴 수 있습니다.</p>
        </div>`;
      const now = await api.stats(mode, { openNow: true });
      if (seq !== statsSeq) return;
      renderLiveStats(state.stats[mode], now.open_now);
    } else {
      $("#stats-live").innerHTML = "";
    }
  } catch (err) {
    if (seq !== statsSeq) return;
    const msg = err instanceof ApiError && err.status === 404 ? err.message : MESSAGES.server;
    $("#stats-summary").innerHTML = `<div class="notice notice--bad" role="alert">${icon("warning")}<span>${esc(msg)}</span></div>`;
  }
}

function renderStats(data) {
  const s = data.seoul;
  const m = MODES[data.mode];
  $("#stats-summary").innerHTML = s ? `
    <div class="card surface-deep">
      <p class="eyebrow">서울 전체 · ${esc(m.label)}</p>
      <p class="lead">걸어서 ${m.minutes}분 안에 AED에 닿는 곳은 <span class="accent num">${pct(s.walk_pct)}</span></p>
      <div class="stat-pair">
        <div><p class="small muted">직선거리 기준</p><p class="big-num">${pct(s.straight_pct)}</p></div>
        <div><p class="small muted">실보행 기준</p><p class="big-num">${pct(s.walk_pct)}</p></div>
      </div>
      <p class="small muted">
        직선거리로 보면 <span class="num nowrap">${ppAbs(s.diff_pp)}</span> 더 넓게 잡힙니다 ·
        <span class="nowrap">사각지대 <span class="num">${km2(s.deadzone_km2)}</span></span> ·
        <span class="nowrap">AED <span class="num">${count(s.aed_count)}</span>개</span>
      </p>
    </div>` : "";

  const n = data.districts.length;
  $("#stats-rows").innerHTML = data.districts.map((d) => {
    // 상위·하위 3개 구는 순위를 배지로 표시한다 (색 + 글자로 구분)
    const rank = d.rank <= 3 ? `<span class="pill pill--ok" title="상위 3개 구">${d.rank}위</span>`
      : d.rank > n - 3 ? `<span class="pill pill--bad" title="하위 3개 구">${d.rank}위</span>` : `${d.rank}`;
    return `
      <tr tabindex="0" data-gu="${esc(d.gu)}" aria-describedby="stats-row-hint">
        <td>${rank}</td>
        <td class="col-gu">${esc(d.gu)}</td>
        <td>${num1(d.walk_pct)}</td>
        <td>${num1(d.diff_pp)}</td>
        <td>${num1(d.deadzone_km2)}</td>
        <td>${count(d.aed_count)}</td>
      </tr>`;
  }).join("");

  $("#stats-rows").querySelectorAll("tr").forEach((tr) => {
    const go = () => flyToDistrict(tr.dataset.gu);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
    });
  });

  $("#stats-source").textContent =
    `지표 계산: ${dateLabel(data.run_at)} · AED 데이터 기준일 ${dateLabel(state.meta?.aed_date)} · ` +
    "차이 = 직선거리 기준 − 실보행 기준 (%p)";
}

function renderLiveStats(base, now) {
  const s = base.seoul;
  if (!now || !s) return;
  const diff = now.walk_pct - s.walk_pct;
  $("#stats-live").innerHTML = `
    <div class="card">
      <div class="card__head">
        <p class="eyebrow">평상시 vs 지금</p>
        <span class="small muted num">기준: ${esc(seoulTime(now.at))}${now.is_holiday ? " · 공휴일" : ""}</span>
      </div>
      <div class="stat-pair">
        <div><p class="small muted">평상시 (전체 AED)</p><p class="big-num">${pct(s.walk_pct)}</p></div>
        <div><p class="small muted">지금 (운영 중 AED)</p><p class="big-num">${pct(now.walk_pct)}</p></div>
      </div>
      <p class="small">
        <span class="pill ${diff < 0 ? "pill--bad" : "pill--ok"} num">${pp(diff)}</span>
        <span class="muted">운영 중 AED <span class="num">${count(now.open_aed_count)}</span>개 / 전체 <span class="num">${count(s.aed_count)}</span>개</span>
      </p>
    </div>`;
  setLiveAt(now.at);
}

function flyToDistrict(gu) {
  const d = state.districts.find((x) => x.gu === gu);
  if (!d) return;
  setSheet(false);
  view.fitBounds(d.bounds, fitPadding());
}

// ================================================================ 시작

async function init() {
  renderModeTexts();
  renderLiveButton();

  const [meta, districts] = await Promise.allSettled([
    api.meta(),
    fetch("data/districts.json").then((r) => r.json()),
  ]);
  if (meta.status === "fulfilled") {
    state.meta = meta.value;
    $("#data-date").textContent =
      `AED 데이터: 공공데이터포털 (기준일 ${dateLabel(meta.value.aed_date)}) · 지도 기준일 ${dateLabel(meta.value.osm_date)}`;
    renderModeTexts();
  }
  if (districts.status === "fulfilled") state.districts = districts.value;

  refreshPolygons();
  loadAeds();
}

init();
