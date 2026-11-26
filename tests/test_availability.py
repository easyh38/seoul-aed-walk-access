from datetime import datetime

from pipeline.availability import get_availability


def make_record(**overrides):
    record = {
        "monSttTme": "0900", "monEndTme": "1800",
        "tueSttTme": "0900", "tueEndTme": "1800",
        "wedSttTme": "0900", "wedEndTme": "1800",
        "thuSttTme": "0900", "thuEndTme": "1800",
        "friSttTme": "0900", "friEndTme": "1800",
        "satSttTme": "0900", "satEndTme": "1800",
        "sunSttTme": "0900", "sunEndTme": "1800",
        "holSttTme": "0900", "holEndTme": "1800",
        "sunFrtYon": "Y", "sunScdYon": "Y",
        "sunThiYon": "Y", "sunFurYon": "Y",
        "sunFifYon": "Y",
    }
    record.update(overrides)
    return record


def test_weekday_open():
    record = make_record()
    assert get_availability(
        record, datetime(2026, 11, 23, 10, 0)
    ) == "available"


def test_weekday_closed_after_hours():
    record = make_record()
    assert get_availability(
        record, datetime(2026, 11, 23, 20, 0)
    ) == "unavailable"


def test_sunday_open():
    record = make_record()
    # 2026-11-22는 넷째 일요일
    assert get_availability(
        record, datetime(2026, 11, 22, 10, 0)
    ) == "available"


def test_sunday_flag_n():
    record = make_record(sunFurYon="N")
    assert get_availability(
        record, datetime(2026, 11, 22, 10, 0)
    ) == "unavailable"


def test_sunday_flag_missing():
    record = make_record(sunFurYon="")
    assert get_availability(
        record, datetime(2026, 11, 22, 10, 0)
    ) == "unknown"


def test_sunday_all_n_with_time_is_unknown():
    record = make_record(
        sunFrtYon="N",
        sunScdYon="N",
        sunThiYon="N",
        sunFurYon="N",
        sunFifYon="N",
        sunSttTme="0000",
        sunEndTme="2400",
    )
    assert get_availability(
        record, datetime(2026, 11, 22, 10, 0)
    ) == "unknown"


def test_missing_weekday_hours():
    record = make_record(monSttTme="", monEndTme="")
    assert get_availability(
        record, datetime(2026, 11, 23, 10, 0)
    ) == "unknown"


def test_holiday_schedule():
    record = make_record(
        holSttTme="1000",
        holEndTme="1200",
    )
    assert get_availability(
        record,
        datetime(2026, 11, 23, 11, 0),
        is_holiday=True,
    ) == "available"


def test_overnight_from_previous_day():
    record = make_record(
        monSttTme="2200",
        monEndTme="0600",
    )
    # 월요일 22시 운영이 화요일 02시까지 이어지는 경우
    assert get_availability(
        record, datetime(2026, 11, 24, 2, 0)
    ) == "available"

def test_unknown_reason_when_today_hours_missing():
    from pipeline.availability import get_availability_detail

    record = make_record(
        satSttTme="",
        satEndTme="",
    )

    result = get_availability_detail(
        record,
        datetime(2026, 11, 21, 21, 51),
    )

    assert result.status == "unknown"
    assert result.reason == "today_schedule_unknown"
    assert result.today_schedule_reason == "hours_missing"
    assert result.previous_day_schedule_reason == "hours_valid"


def test_unknown_reason_when_both_days_hours_missing():
    from pipeline.availability import get_availability_detail

    record = make_record(
        satSttTme="",
        satEndTme="",
        friSttTme="",
        friEndTme="",
    )

    result = get_availability_detail(
        record,
        datetime(2026, 11, 21, 21, 51),
    )

    assert result.status == "unknown"
    assert result.reason == "today_and_previous_day_schedule_unknown"
    assert result.today_schedule_reason == "hours_missing"
    assert result.previous_day_schedule_reason == "hours_missing"


def test_unknown_reason_when_previous_day_hours_missing():
    from pipeline.availability import get_availability_detail

    record = make_record(
        friSttTme="",
        friEndTme="",
        satSttTme="0900",
        satEndTme="1800",
    )

    result = get_availability_detail(
        record,
        datetime(2026, 11, 21, 21, 51),
    )

    assert result.status == "unknown"
    assert result.reason == "previous_day_schedule_unknown"
    assert result.today_schedule_reason == "hours_valid"
    assert result.previous_day_schedule_reason == "hours_missing"


def test_unknown_reason_when_today_hours_missing():
    from pipeline.availability import get_availability_detail

    record = make_record(
        satSttTme="",
        satEndTme="",
    )

    result = get_availability_detail(
        record,
        datetime(2026, 11, 21, 21, 51),
    )

    assert result.status == "unknown"
    assert result.reason == "today_schedule_unknown"
    assert result.today_schedule_reason == "hours_missing"
    assert result.previous_day_schedule_reason == "hours_valid"


def test_unknown_reason_when_both_days_hours_missing():
    from pipeline.availability import get_availability_detail

    record = make_record(
        satSttTme="",
        satEndTme="",
        friSttTme="",
        friEndTme="",
    )

    result = get_availability_detail(
        record,
        datetime(2026, 11, 21, 21, 51),
    )

    assert result.status == "unknown"
    assert result.reason == "today_and_previous_day_schedule_unknown"
    assert result.today_schedule_reason == "hours_missing"
    assert result.previous_day_schedule_reason == "hours_missing"


def test_unknown_reason_when_previous_day_hours_missing():
    from pipeline.availability import get_availability_detail

    record = make_record(
        friSttTme="",
        friEndTme="",
        satSttTme="0900",
        satEndTme="1800",
    )

    result = get_availability_detail(
        record,
        datetime(2026, 11, 21, 21, 51),
    )

    assert result.status == "unknown"
    assert result.reason == "previous_day_schedule_unknown"
    assert result.today_schedule_reason == "hours_valid"
    assert result.previous_day_schedule_reason == "hours_missing"


def test_missing_hours_policy_open():
    from pipeline.availability import get_availability_detail

    record = make_record(monSttTme="", monEndTme="")
    result = get_availability_detail(
        record,
        datetime(2026, 11, 23, 10, 0),
        missing_policy="open",
    )

    assert result.status == "available"
    assert result.reason == "today_schedule_assumed_open_by_policy"
    assert result.today_schedule_reason == "hours_missing_policy_open"


def test_missing_hours_policy_closed():
    from pipeline.availability import get_availability_detail

    record = make_record(monSttTme="", monEndTme="")
    result = get_availability_detail(
        record,
        datetime(2026, 11, 23, 10, 0),
        missing_policy="closed",
    )

    assert result.status == "unavailable"
    assert result.today_schedule_reason == "hours_missing_policy_closed"


def test_invalid_hours_policy_closed():
    from pipeline.availability import get_availability_detail

    record = make_record(monSttTme="0900", monEndTme="0900")
    result = get_availability_detail(
        record,
        datetime(2026, 11, 23, 10, 0),
        invalid_policy="closed",
    )

    assert result.status == "unavailable"
    assert result.today_schedule_reason == "hours_invalid_policy_closed"


def test_overnight_disabled_uses_invalid_policy():
    from pipeline.availability import get_availability_detail

    record = make_record(monSttTme="2200", monEndTme="0600")
    result = get_availability_detail(
        record,
        datetime(2026, 11, 23, 23, 0),
        allow_overnight=False,
        invalid_policy="closed",
    )

    assert result.status == "unavailable"
    assert result.today_schedule_reason == "hours_invalid_policy_closed"
