from datetime import date

from pipeline.analyze_availability import resolve_holiday_flags


def test_detects_korean_public_holiday():
    result = resolve_holiday_flags(date(2026, 5, 5))

    assert result["is_holiday"] is True
    assert result["assumptions"]["analysis_date"]["holiday_name"] == "어린이날"


def test_detects_previous_day_public_holiday():
    result = resolve_holiday_flags(date(2026, 5, 6))

    assert result["is_holiday"] is False
    assert result["previous_day_is_holiday"] is True
    assert result["assumptions"]["previous_date"]["holiday_name"] == "어린이날"


def test_regular_dates_are_not_holidays():
    result = resolve_holiday_flags(date(2026, 11, 26))

    assert result["is_holiday"] is False
    assert result["previous_day_is_holiday"] is False


def test_manual_holiday_override():
    result = resolve_holiday_flags(
        date(2026, 11, 26),
        force_today=True,
    )

    assert result["is_holiday"] is True
    assert result["assumptions"]["analysis_date"]["holiday_name"] is None
    assert result["assumptions"]["analysis_date"]["manual_override"] is True


def test_manual_previous_day_holiday_override():
    result = resolve_holiday_flags(
        date(2026, 11, 26),
        force_previous_day=True,
    )

    assert result["previous_day_is_holiday"] is True
    assert result["assumptions"]["previous_date"]["manual_override"] is True
