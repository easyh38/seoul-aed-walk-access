"""실시간 운영 반영(open_now) 공통 함수 (PRD S-06, guideline 4-6).

- 운영 여부는 3단계 파이프라인과 같은 함수(pipeline.availability)로 계산한다.
  운영시간 정책(config.yaml hours_missing_policy 등)과 공휴일 판정
  (holidays 라이브러리 "KR")도 pipeline.analyze_availability의 것을 그대로 쓴다.
- 모든 함수는 "시각(when)"을 인자로 받는다. API는 현재 서울 시각을 넣고,
  발표용 비교(평일 14시 vs 22시)는 같은 함수에 시각만 바꿔 넣는다:
      python -m api.open_now --at 2026-11-25T14:00 --at 2026-11-25T22:00

운영 중으로 보는 상태: available, unknown
  PRD S-06 "운영시간 정보가 없으면 '정보 없음'으로 표시하고 운영 중으로 간주".
  응답에는 open_status(available/unknown/unavailable)를 그대로 넣어 화면이 구분해 보여 준다.

커버리지 캐시(coverage_cache)
  키 = (요일, 분 // 30, mode, 공휴일 여부). 30분 구간의 시작 시각 기준으로 계산해 저장한다.
  같은 키라도 운영 중 AED 집합이 다를 수 있어(몇째 일요일인지, 전날이 공휴일인지,
  파이프라인 재실행으로 AED가 바뀐 경우) 집합의 해시(aed_set_hash)를 같이 저장하고,
  해시가 다르면 다시 계산해 덮어쓴다.
"""

from __future__ import annotations

import argparse
import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from api.db import execute, query
from pipeline.analyze_availability import load_operating_hours_config, resolve_holiday_flags
from pipeline.availability import get_availability_detail

SEOUL_TZ = ZoneInfo("Asia/Seoul")
SLOT_MINUTES = 30
OPEN_STATUSES = ("available", "unknown")
MODE_MINUTES = {"oneway": 4, "roundtrip": 2}  # grid_point·isochrone과 묶여 있는 고정값

HOURS_TTL_SECONDS = 600  # AED 운영시간은 파이프라인을 다시 돌릴 때만 바뀐다

# DB aed.hours의 요일 키 → 원본 API 필드 이름 (pipeline/03_load_aed.py DAY_TIME_FIELDS의 역방향)
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun", "hol")


# ---------------------------------------------------------------- 시각·공휴일

def now_seoul() -> datetime:
    """현재 서울 시각. 서버 시계가 UTC여도 틀리지 않는다."""
    return datetime.now(SEOUL_TZ)


def to_seoul(when: datetime) -> datetime:
    """시간대가 없는 시각은 서울 시각으로 보고, 있으면 서울 시각으로 바꾼다."""
    if when.tzinfo is None:
        return when.replace(tzinfo=SEOUL_TZ)
    return when.astimezone(SEOUL_TZ)


def slot_start(when: datetime) -> datetime:
    """30분 구간의 시작 시각 (14:47 → 14:30)."""
    when = to_seoul(when)
    return when.replace(minute=when.minute - when.minute % SLOT_MINUTES, second=0, microsecond=0)


@dataclass(frozen=True)
class Context:
    """한 시각의 판정 조건."""

    when: datetime
    is_holiday: bool
    previous_day_is_holiday: bool
    holiday_name: str | None


def context_at(when: datetime) -> Context:
    when = to_seoul(when)
    flags = resolve_holiday_flags(when.date())
    return Context(
        when=when,
        is_holiday=flags["is_holiday"],
        previous_day_is_holiday=flags["previous_day_is_holiday"],
        holiday_name=flags["assumptions"]["analysis_date"]["holiday_name"],
    )


# ---------------------------------------------------------------- AED별 운영 여부

def hours_to_record(hours: dict | None) -> dict:
    """aed.hours JSONB를 pipeline.availability가 읽는 원본 필드 형식으로 바꾼다.

    {"mon": ["0900", "1800"], ..., "sun_flags": {"sunFrtYon": "Y", ...}}
      → {"monSttTme": "0900", "monEndTme": "1800", ..., "sunFrtYon": "Y", ...}
    """
    hours = hours or {}
    record: dict[str, str] = {}
    for day in DAY_KEYS:
        pair = hours.get(day) or ["", ""]
        start, end = (list(pair) + ["", ""])[:2]
        record[f"{day}SttTme"] = start or ""
        record[f"{day}EndTme"] = end or ""
    record.update(hours.get("sun_flags") or {})
    return record


class _HoursCache:
    """AED 운영시간(약 1.2만 건)을 메모리에 두고 분 단위 판정 결과를 재사용한다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[tuple[int, dict]] | None = None
        self._loaded_at = 0.0
        self._statuses: dict[tuple, dict[int, str]] = {}

    def clear(self) -> None:
        with self._lock:
            self._records = None
            self._statuses.clear()

    def _load(self) -> list[tuple[int, dict]]:
        if self._records is None or time.monotonic() - self._loaded_at > HOURS_TTL_SECONDS:
            rows = query("SELECT id, hours FROM aed")
            self._records = [(row["id"], hours_to_record(row["hours"])) for row in rows]
            self._loaded_at = time.monotonic()
            self._statuses.clear()
        return self._records

    def statuses(self, ctx: Context) -> dict[int, str]:
        key = (
            ctx.when.date(), ctx.when.hour, ctx.when.minute,
            ctx.is_holiday, ctx.previous_day_is_holiday,
        )
        with self._lock:
            records = self._load()
            cached = self._statuses.get(key)
            if cached is not None:
                return cached

            policy = load_operating_hours_config()
            result = {
                aed_id: get_availability_detail(
                    record,
                    ctx.when,
                    is_holiday=ctx.is_holiday,
                    previous_day_is_holiday=ctx.previous_day_is_holiday,
                    **policy,
                ).status
                for aed_id, record in records
            }
            if len(self._statuses) >= 8:  # 최근 몇 분만 남긴다
                self._statuses.clear()
            self._statuses[key] = result
            return result


_hours_cache = _HoursCache()


def statuses_at(when: datetime) -> dict[int, str]:
    """{aed_id: "available" | "unknown" | "unavailable"}"""
    return _hours_cache.statuses(context_at(when))


def open_ids(statuses: dict[int, str]) -> list[int]:
    return sorted(aed_id for aed_id, status in statuses.items() if status in OPEN_STATUSES)


# ---------------------------------------------------------------- 커버리지 캐시

LOOKUP_SQL = """
    SELECT walk_pct, aed_set_hash
    FROM coverage_cache
    WHERE weekday = :weekday AND slot = :slot AND mode = :mode AND is_holiday = :is_holiday
"""

# 운영 중 AED의 등시선만 합쳐 서울 경계로 자르고, 격자 점 비율을 계산해 저장한다.
# 비율은 합친 다각형 하나에 점이 들어가는지로 센다 (07의 walk_pct와 같은 결과, 실측 약 0.7초).
# 운영 중 AED가 0개면 coverage는 NULL, walk_pct는 0이다.
UPSERT_SQL = """
    WITH cov AS (
        SELECT ST_UnaryUnion(ST_Collect(i.geom)) AS g
        FROM isochrone i
        WHERE i.minutes = :minutes
          AND i.aed_id = ANY(CAST(:ids AS int[]))
    ),
    seoul AS (
        SELECT ST_UnaryUnion(ST_Collect(geom)) AS g FROM district
    ),
    pct AS (
        SELECT COALESCE(round(
            100.0 * count(*) FILTER (WHERE cov.g IS NOT NULL AND ST_Intersects(cov.g, gp.geom))
            / NULLIF(count(*), 0), 2), 0) AS v
        FROM grid_point gp CROSS JOIN cov
    )
    INSERT INTO coverage_cache (weekday, slot, mode, is_holiday, coverage, walk_pct, aed_set_hash)
    SELECT :weekday, :slot, :mode, :is_holiday,
           ST_Multi(ST_CollectionExtract(ST_Intersection(seoul.g, cov.g), 3)),
           pct.v, :hash
    FROM seoul, cov, pct
    ON CONFLICT (weekday, slot, mode, is_holiday) DO UPDATE SET
        coverage = EXCLUDED.coverage,
        walk_pct = EXCLUDED.walk_pct,
        aed_set_hash = EXCLUDED.aed_set_hash
    RETURNING walk_pct
"""


@dataclass(frozen=True)
class Coverage:
    """open_now 커버리지 한 건. key는 coverage_cache에서 이 행을 찾는 값."""

    key: dict
    walk_pct: float
    open_aed_count: int
    slot_start: datetime
    is_holiday: bool
    cache_hit: bool


def _hash_ids(ids: list[int]) -> str:
    return hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()


def coverage_at(mode: str, when: datetime) -> Coverage:
    """when이 속한 30분 구간의 운영 중 AED 커버리지. 캐시에 있으면 그대로, 없으면 계산·저장."""
    if mode not in MODE_MINUTES:
        raise ValueError(f"mode는 {sorted(MODE_MINUTES)} 중 하나여야 합니다: {mode!r}")

    start = slot_start(when)
    ctx = context_at(start)
    ids = open_ids(_hours_cache.statuses(ctx))
    digest = _hash_ids(ids)

    key = {
        "weekday": start.weekday(),
        "slot": (start.hour * 60 + start.minute) // SLOT_MINUTES,
        "mode": mode,
        "is_holiday": ctx.is_holiday,
    }

    rows = query(LOOKUP_SQL, **key)
    if rows and rows[0]["aed_set_hash"] == digest:
        walk_pct, hit = rows[0]["walk_pct"], True
    else:
        walk_pct = execute(
            UPSERT_SQL, **key, minutes=MODE_MINUTES[mode], ids=ids, hash=digest
        )[0]["walk_pct"]
        hit = False

    return Coverage(
        key=key,
        walk_pct=float(walk_pct),
        open_aed_count=len(ids),
        slot_start=start,
        is_holiday=ctx.is_holiday,
        cache_hit=hit,
    )


# ---------------------------------------------------------------- 발표용 비교

def main() -> None:
    parser = argparse.ArgumentParser(
        description="시각별 운영 중 AED 기준 실보행 커버리지(walk_pct). 예: 평일 14시 vs 22시"
    )
    parser.add_argument(
        "--at", action="append", required=True, metavar="DATETIME",
        help="서울 시각 (예: 2026-11-25T14:00). 여러 번 지정 가능",
    )
    args = parser.parse_args()

    print(f"{'시각':<18}{'공휴일':<6}{'운영 AED':>9}{'편도 4분':>10}{'왕복 기준':>10}")
    for value in args.at:
        when = to_seoul(datetime.fromisoformat(value))
        oneway = coverage_at("oneway", when)
        roundtrip = coverage_at("roundtrip", when)
        print(
            f"{when:%Y-%m-%d %H:%M}  {'예' if oneway.is_holiday else '아니오':<6}"
            f"{oneway.open_aed_count:>9,}{oneway.walk_pct:>9.2f}%{roundtrip.walk_pct:>9.2f}%"
        )


if __name__ == "__main__":
    main()
