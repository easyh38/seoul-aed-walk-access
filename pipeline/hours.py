"""AED 운영시간 파싱 및 검증 공통 모듈."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class OperatingHoursError(ValueError):
    """운영시간 형식 또는 구간이 유효하지 않을 때 발생."""


@dataclass(frozen=True)
class HoursPairResult:
    """요일별 운영시간 검증 결과."""

    status: Literal["valid", "missing", "invalid"]
    interval: tuple[int, int] | None = None
    error: str | None = None


def parse_hhmm(
    value: str | int | None,
    *,
    is_end: bool = False,
) -> int | None:
    """
    HHMM 형식의 시간을 자정 기준 분 단위로 변환한다.

    시작시간: 0000~2359
    종료시간: 0000~4759 (지속시간 검사는 별도 수행)
    빈 값: None
    """
    if value is None or str(value).strip() == "":
        return None

    raw = str(value).strip()

    if not raw.isdigit() or len(raw) > 4:
        raise OperatingHoursError(f"잘못된 시간 형식: {raw!r}")

    raw = raw.zfill(4)
    hour = int(raw[:2])
    minute = int(raw[2:])

    if not 0 <= minute < 60:
        raise OperatingHoursError(f"잘못된 분 값: {raw!r}")

    if is_end:
        if not 0 <= hour < 48:
            raise OperatingHoursError(f"종료시간 범위 초과: {raw!r}")
    elif not 0 <= hour < 24:
        raise OperatingHoursError(f"시작시간 범위 초과: {raw!r}")

    return hour * 60 + minute


def parse_hours_pair(
    start_value: str | int | None,
    end_value: str | int | None,
    *,
    allow_overnight: bool = True,
    max_duration_minutes: int = 1440,
) -> tuple[int, int] | None:
    """
    시작시간과 종료시간을 파싱한다.

    정상 예:
        0900~1800 -> (540, 1080)
        2200~0600 -> (1320, 1800)
        2200~2500 -> (1320, 1500)
        0000~2400 -> (0, 1440)
        0000~0000 -> (0, 1440)

    0000~0000 이외의 동일 시작·종료시간은 비정상으로 처리한다.
    양쪽 모두 빈 값이면 None을 반환한다.
    한쪽만 빈 값이면 OperatingHoursError를 발생시킨다.
    """
    raw_start = "" if start_value is None else str(start_value).strip()
    raw_end = "" if end_value is None else str(end_value).strip()

    if not raw_start and not raw_end:
        return None

    if not raw_start or not raw_end:
        raise OperatingHoursError("시작시간 또는 종료시간이 누락되었습니다.")

    start = parse_hhmm(raw_start)
    end = parse_hhmm(raw_end, is_end=True)

    if start is None or end is None:
        raise OperatingHoursError("시작시간 또는 종료시간이 누락되었습니다.")

    if start == end:
        # 0000~0000만 24시간 운영으로 인정
        if start == 0 and raw_end.zfill(4) == "0000":
            end = 1440
        else:
            raise OperatingHoursError(
                "동일한 시작·종료시간은 0000~0000만 허용됩니다."
            )

    elif end < start:
        if not allow_overnight:
            raise OperatingHoursError("익일 종료가 허용되지 않습니다.")
        end += 1440

    duration = end - start

    if duration <= 0:
        raise OperatingHoursError("운영시간 지속시간이 유효하지 않습니다.")

    if duration > max_duration_minutes:
        raise OperatingHoursError(
            f"운영시간이 최대 허용시간을 초과합니다: {duration}분"
        )

    if end > 1440 and not allow_overnight:
        raise OperatingHoursError("익일 종료가 허용되지 않습니다.")

    return start, end


def classify_hours_pair(
    start_value: str | int | None,
    end_value: str | int | None,
    *,
    allow_overnight: bool = True,
    max_duration_minutes: int = 1440,
) -> HoursPairResult:
    """
    한 요일의 운영시간을 valid, missing, invalid로 분류한다.

    양쪽 모두 빈 값 또는 한쪽만 빈 값:
        missing

    정상적인 시간:
        valid

    형식 오류, 24시간 초과 등:
        invalid
    """
    raw_start = "" if start_value is None else str(start_value).strip()
    raw_end = "" if end_value is None else str(end_value).strip()

    if not raw_start and not raw_end:
        return HoursPairResult(status="missing")

    if not raw_start or not raw_end:
        return HoursPairResult(
            status="missing",
            error="시작시간 또는 종료시간 누락",
        )

    try:
        interval = parse_hours_pair(
            raw_start,
            raw_end,
            allow_overnight=allow_overnight,
            max_duration_minutes=max_duration_minutes,
        )
    except OperatingHoursError as exc:
        return HoursPairResult(
            status="invalid",
            error=str(exc),
        )

    if interval is None:
        return HoursPairResult(status="missing")

    return HoursPairResult(
        status="valid",
        interval=interval,
    )


def is_open(
    start: int | None,
    end: int | None,
    now: int,
) -> bool | None:
    """
    파싱된 운영시간에 현재 시각이 포함되는지 판정한다.

    now는 운영 시작일 자정부터 경과한 분이다.
    예: 다음 날 00:30은 1470분이다.

    반환:
        True  = 운영 중
        False = 운영 종료
        None  = 운영시간 미상
    """
    if start is None or end is None:
        return None

    if not isinstance(now, int) or now < 0:
        raise ValueError("현재 시각은 0 이상의 정수(분)여야 합니다.")

    if end < start:
        raise ValueError("종료시간은 시작시간보다 빠릅니다.")

    return start <= now < end


def is_open_with_policy(
    result: HoursPairResult,
    now: int,
    *,
    missing_policy: Literal["open", "closed"] = "open",
    invalid_policy: Literal["open", "closed"] = "closed",
) -> bool:
    """
    운영시간 검증 결과와 설정 정책을 적용해 운영 여부를 반환한다.

    valid   -> 실제 시간 구간으로 판정
    missing -> missing_policy 적용
    invalid -> invalid_policy 적용
    """
    if missing_policy not in ("open", "closed"):
        raise ValueError("missing_policy는 open 또는 closed여야 합니다.")

    if invalid_policy not in ("open", "closed"):
        raise ValueError("invalid_policy는 open 또는 closed여야 합니다.")

    if result.status == "missing":
        return missing_policy == "open"

    if result.status == "invalid":
        return invalid_policy == "open"

    if result.interval is None:
        raise ValueError("valid 결과에 운영시간 구간이 없습니다.")

    return is_open(*result.interval, now) is True
