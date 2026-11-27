// 표시용 형식 함수: 운영시간, 시간·거리, 퍼센트, 서울 시각. 판정(운영 중 여부)은 하지 않는다 (서버 몫).

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

const nf1 = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

export const pct = (v) => (v == null ? "-" : `${nf1.format(v)}%`);
export const pp = (v) => (v == null ? "-" : `${v > 0 ? "+" : ""}${nf1.format(v)}%p`);
/** 부호 없이 "13.9%p" (직선 − 실보행 차이처럼 방향이 정해진 값) */
export const ppAbs = (v) => (v == null ? "-" : `${nf1.format(v)}%p`);
export const km2 = (v) => (v == null ? "-" : `${nf1.format(v)}km²`);
/** 단위 없이 소수 1자리 (단위는 표 머리에 적는다) */
export const num1 = (v) => (v == null ? "-" : nf1.format(v));
export const count = (v) => (v == null ? "-" : nf0.format(v));

export function meters(m) {
  if (m == null) return "-";
  return m >= 1000 ? `${nf1.format(m / 1000)}km` : `${nf0.format(Math.round(m))}m`;
}

/** 초 → "4분 12초" / "45초" */
export function duration(seconds) {
  if (seconds == null) return "-";
  const s = Math.round(seconds);
  const min = Math.floor(s / 60);
  const sec = s % 60;
  if (min === 0) return `${sec}초`;
  return sec === 0 ? `${min}분` : `${min}분 ${sec}초`;
}

const seoulLabel = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  weekday: "long",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** ISO 시각 → "금요일 18:17" (서울 시각) */
export function seoulTime(iso) {
  if (!iso) return "";
  const parts = Object.fromEntries(
    seoulLabel.formatToParts(new Date(iso)).map((p) => [p.type, p.value]),
  );
  return `${parts.weekday} ${parts.hour}:${parts.minute}`;
}

export function dateLabel(iso) {
  return iso ? String(iso).slice(0, 10) : "-";
}

// ------------------------------------------------------------ 운영시간

const DAYS = [
  ["mon", "월"], ["tue", "화"], ["wed", "수"], ["thu", "목"], ["fri", "금"], ["sat", "토"],
];
const SUNDAY_WEEKS = [
  ["sunFrtYon", "첫째"], ["sunScdYon", "둘째"], ["sunThiYon", "셋째"], ["sunFurYon", "넷째"], ["sunFifYon", "다섯째"],
];

function hhmm(value) {
  const v = String(value).padStart(4, "0");
  let h = Number(v.slice(0, 2));
  const m = v.slice(2);
  const nextDay = h >= 24 && !(h === 24 && m === "00");
  if (nextDay) h -= 24;
  return `${nextDay ? "다음날 " : ""}${String(h).padStart(2, "0")}:${m}`;
}

/** ["0900", "1800"] → "09:00-18:00" */
export function hoursRange(pair) {
  const [start, end] = Array.isArray(pair) ? pair : ["", ""];
  if (!start && !end) return "정보 없음";
  if (!start || !end) return "정보 일부 없음";
  if ((start === "0000" && end === "2400") || (start === "0000" && end === "0000")) return "24시간";
  const overnight = Number(end) <= Number(start) && Number(end) < 2400;
  return `${hhmm(start)}-${overnight ? "다음날 " : ""}${hhmm(end)}`;
}

/** aed.hours → [["월~금", "09:00-18:00"], ["토", ...], ["일", ...], ["공휴일", ...]] */
export function hoursRows(hours) {
  hours = hours || {};
  const rows = [];
  // 같은 시간이 이어지는 평일·토요일은 묶는다 (월~금)
  let i = 0;
  while (i < DAYS.length) {
    const text = hoursRange(hours[DAYS[i][0]]);
    let j = i;
    while (j + 1 < DAYS.length && hoursRange(hours[DAYS[j + 1][0]]) === text) j += 1;
    rows.push([i === j ? DAYS[i][1] : `${DAYS[i][1]}~${DAYS[j][1]}`, text]);
    i = j + 1;
  }

  const flags = hours.sun_flags || {};
  const open = SUNDAY_WEEKS.filter(([key]) => String(flags[key] || "").toUpperCase() === "Y");
  const sunTime = hoursRange(hours.sun);
  if (Object.keys(flags).length && open.length === 0) {
    rows.push(["일", "휴무"]);
  } else if (open.length && open.length < SUNDAY_WEEKS.length) {
    rows.push(["일", `${open.map(([, n]) => n).join("·")} 주 ${sunTime}`]);
  } else {
    rows.push(["일", sunTime]);
  }
  rows.push(["공휴일", hoursRange(hours.hol)]);

  // 모든 요일·공휴일이 같으면 한 줄로 (예: 매일 24시간)
  if (rows.every(([, t]) => t === rows[0][1])) return [["매일", rows[0][1]]];
  return rows;
}

// ------------------------------------------------------------ 운영 상태 배지

export const STATUS = {
  available: { cls: "pill--ok", text: "운영 중" },
  unknown: { cls: "pill--info", text: "정보 없음 (운영 중으로 봄)" },
  unavailable: { cls: "pill--bad", text: "운영 종료" },
};

export function statusPill(status) {
  const s = STATUS[status];
  return s ? `<span class="pill ${s.cls}">${s.text}</span>` : "";
}
