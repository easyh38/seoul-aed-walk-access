"""AED 날짜·시각별 이용 가능 여부 판정 모듈."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Mapping

from pipeline.hours import classify_hours_pair


Availability = Literal["available", "unavailable", "unknown"]
ScheduleStatus = Literal["valid", "closed", "unknown"]


@dataclass(frozen=True)
class AvailabilityResult:
    """이용 가능 여부와 판정 근거."""

    status: Availability
    reason: str
    today_schedule_reason: str
    previous_day_schedule_reason: str


DAY_FIELDS = {
    0: ("monSttTme", "monEndTme"),
    1: ("tueSttTme", "tueEndTme"),
    2: ("wedSttTme", "wedEndTme"),
    3: ("thuSttTme", "thuEndTme"),
    4: ("friSttTme", "friEndTme"),
    5: ("satSttTme", "satEndTme"),
}

SUNDAY_FLAGS = (
    "sunFrtYon",
    "sunScdYon",
    "sunThiYon",
    "sunFurYon",
    "sunFifYon",
)


def _get_schedule(
    record: Mapping[str, object],
    target_date: date,
    *,
    is_holiday: bool,
) -> tuple[ScheduleStatus, tuple[int, int] | None, str]:
    """특정 날짜의 운영시간과 정보 상태를 반환한다."""

    is_sunday = target_date.weekday() == 6

    if is_holiday:
        start_field, end_field = "holSttTme", "holEndTme"

    elif is_sunday:
        sunday_number = (target_date.day - 1) // 7
        flag_field = SUNDAY_FLAGS[sunday_number]

        flags = [
            str(record.get(field) or "").strip().upper()
            for field in SUNDAY_FLAGS
        ]

        flag = str(record.get(flag_field) or "").strip().upper()
        start_value = record.get("sunSttTme")
        end_value = record.get("sunEndTme")

        has_time = bool(
            str(start_value or "").strip()
            and str(end_value or "").strip()
        )

        # 전체 플래그가 N인데 일요일 운영시간이 입력된 충돌 사례
        if all(value == "N" for value in flags) and has_time:
            return "unknown", None, "sunday_flag_time_conflict"

        if flag == "N":
            return "closed", None, "sunday_flag_closed"

        if flag != "Y":
            return "unknown", None, "sunday_flag_missing_or_invalid"

        start_field, end_field = "sunSttTme", "sunEndTme"

    else:
        start_field, end_field = DAY_FIELDS[target_date.weekday()]

    result = classify_hours_pair(
        record.get(start_field),
        record.get(end_field),
    )

    if result.status == "missing":
        if is_holiday:
            reason = "holiday_hours_missing"
        elif is_sunday:
            reason = "sunday_hours_missing"
        else:
            reason = "hours_missing"

        return "unknown", None, reason

    if result.status == "invalid" or result.interval is None:
        if is_holiday:
            reason = "holiday_hours_invalid"
        elif is_sunday:
            reason = "sunday_hours_invalid"
        else:
            reason = "hours_invalid"

        return "unknown", None, reason

    return "valid", result.interval, "hours_valid"


def get_availability_detail(
    record: Mapping[str, object],
    when: datetime,
    *,
    is_holiday: bool = False,
    previous_day_is_holiday: bool = False,
) -> AvailabilityResult:
    """특정 시각의 이용 가능 여부와 판정 원인을 반환한다."""

    if not isinstance(when, datetime):
        raise TypeError("when은 datetime 객체여야 합니다.")

    today = when.date()
    current_minute = when.hour * 60 + when.minute

    today_status, today_interval, today_reason = _get_schedule(
        record,
        today,
        is_holiday=is_holiday,
    )

    yesterday = today - timedelta(days=1)

    yesterday_status, yesterday_interval, yesterday_reason = _get_schedule(
        record,
        yesterday,
        is_holiday=previous_day_is_holiday,
    )

    # 당일 운영시간에 현재 시각이 포함되는 경우
    if today_status == "valid" and today_interval is not None:
        start, end = today_interval

        if start <= current_minute < end:
            return AvailabilityResult(
                "available",
                "today_schedule_active",
                today_reason,
                yesterday_reason,
            )

    # 전날 운영시간이 자정을 넘어 현재까지 이어지는 경우
    if yesterday_status == "valid" and yesterday_interval is not None:
        start, end = yesterday_interval
        previous_day_minute = current_minute + 1440

        if end > 1440 and start <= previous_day_minute < end:
            return AvailabilityResult(
                "available",
                "previous_day_overnight_active",
                today_reason,
                yesterday_reason,
            )

    # 당일 또는 전날 운영시간을 확인할 수 없는 경우
    if today_status == "unknown" and yesterday_status == "unknown":
        return AvailabilityResult(
            "unknown",
            "today_and_previous_day_schedule_unknown",
            today_reason,
            yesterday_reason,
        )

    if today_status == "unknown":
        return AvailabilityResult(
            "unknown",
            "today_schedule_unknown",
            today_reason,
            yesterday_reason,
        )

    if yesterday_status == "unknown":
        return AvailabilityResult(
            "unknown",
            "previous_day_schedule_unknown",
            today_reason,
            yesterday_reason,
        )

    return AvailabilityResult(
        "unavailable",
        "outside_known_schedules",
        today_reason,
        yesterday_reason,
    )


def get_availability(
    record: Mapping[str, object],
    when: datetime,
    *,
    is_holiday: bool = False,
    previous_day_is_holiday: bool = False,
) -> Availability:
    """기존 코드와 호환되는 이용 가능 여부 판정 함수."""

    return get_availability_detail(
        record,
        when,
        is_holiday=is_holiday,
        previous_day_is_holiday=previous_day_is_holiday,
    ).status
