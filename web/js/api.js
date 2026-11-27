// API 호출. 주소는 상대 경로(/api/...)만 쓴다: 개발(FastAPI)과 배포(Nginx) 모두 화면과 API가 같은 주소다.

export const MESSAGES = {
  outsideSeoul: "서울특별시 안의 위치만 분석할 수 있습니다.",
  noRoute: "도보 경로를 찾을 수 없습니다. 다른 위치를 선택해 주세요.",
  server: "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
  insecure: "내 위치는 보안 연결(HTTPS)에서만 쓸 수 있습니다. 지도를 클릭해 위치를 선택해 주세요.",
  denied: "위치 권한이 꺼져 있습니다. 지도를 클릭해 위치를 선택해 주세요.",
  locateFailed: "현재 위치를 가져오지 못했습니다. 지도를 클릭해 위치를 선택해 주세요.",
};

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : MESSAGES.server);
    this.status = status;
    this.detail = detail;
  }
}

function query(params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== false) qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

async function request(path, { params, body, signal } = {}) {
  let res;
  try {
    res = await fetch(`/api/${path}${query(params)}`, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, MESSAGES.server); // 네트워크 오류
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* 본문 없음 */
  }
  if (!res.ok) throw new ApiError(res.status, data && data.detail);
  return data;
}

export const api = {
  meta: () => request("meta"),
  aeds: (bbox, { openNow = false, signal } = {}) =>
    request("aeds", { params: { ...bbox, open_now: openNow }, signal }),
  layer: (kind, mode, { openNow = false, signal } = {}) =>
    request(`layers/${kind}`, { params: { mode, open_now: openNow }, signal }),
  stats: (mode, { openNow = false, signal } = {}) =>
    request("stats", { params: { mode, open_now: openNow }, signal }),
  nearest: (lat, lon, { openNow = false, aedId, signal } = {}) =>
    request("nearest", { params: { lat, lon, open_now: openNow, aed_id: aedId }, signal }),
  report: (body) => request("reports", { body }),
};

/** 찾기(/api/nearest) 실패를 화면 문구로 바꾼다 (PRD 5장 안내·오류 메시지). */
export function nearestErrorMessage(err) {
  if (!(err instanceof ApiError)) return MESSAGES.server;
  if (err.status === 400) return MESSAGES.outsideSeoul;
  if (err.status === 404 || err.status === 503) {
    // API 안내가 더 구체적이면(예: "반경 1km 안에 ... AED가 없습니다") 그대로 보여 준다
    return typeof err.detail === "string" ? err.detail : MESSAGES.noRoute;
  }
  return MESSAGES.server;
}
