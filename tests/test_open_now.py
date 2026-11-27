"""api/open_now.py 테스트: 공휴일, 자정 넘는 운영시간, 30분 캐시 적중. DB는 가짜 함수로 바꿔 끼운다."""

import os
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost:5432/test")

import pytest

from api import open_now

WEEKDAY_9_TO_18 = {d: ["0900", "1800"] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
ALL_SUNDAYS = {"sun_flags": {f: "Y" for f in ("sunFrtYon", "sunScdYon", "sunThiYon", "sunFurYon", "sunFifYon")}}

AEDS = {
    # 매일 9~18시, 공휴일 운영시간은 빈칸 (config hours_missing_policy를 따른다)
    1: {**WEEKDAY_9_TO_18, "hol": ["", ""], **ALL_SUNDAYS},
    # 공휴일에만 10~16시 (평일 시간은 짧게)
    2: {**{d: ["0900", "1000"] for d in WEEKDAY_9_TO_18}, "hol": ["1000", "1600"], **ALL_SUNDAYS},
    # 매일 22시~다음 날 6시 (자정 넘김)
    3: {**{d: ["2200", "0600"] for d in WEEKDAY_9_TO_18}, "hol": ["2200", "0600"], **ALL_SUNDAYS},
}


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    """aed.hours 조회와 coverage_cache 읽기·쓰기를 흉내 낸다."""
    cache = {}
    calls = {"upsert": 0, "lookup": 0}

    def _query(sql, **params):
        if "FROM aed" in sql:
            return [{"id": i, "hours": h} for i, h in AEDS.items()]
        if "FROM coverage_cache" in sql:
            calls["lookup"] += 1
            key = (params["weekday"], params["slot"], params["mode"], params["is_holiday"])
            return [cache[key]] if key in cache else []
        raise AssertionError(sql)

    def _execute(sql, **params):
        calls["upsert"] += 1
        key = (params["weekday"], params["slot"], params["mode"], params["is_holiday"])
        cache[key] = {"walk_pct": 10.0 * len(params["ids"]), "aed_set_hash": params["hash"]}
        return [{"walk_pct": cache[key]["walk_pct"]}]

    monkeypatch.setattr(open_now, "query", _query)
    monkeypatch.setattr(open_now, "execute", _execute)
    open_now._hours_cache.clear()
    yield cache, calls
    open_now._hours_cache.clear()


def at(text):
    return open_now.to_seoul(datetime.fromisoformat(text))


def test_hours_to_record_maps_db_json_to_api_fields():
    record = open_now.hours_to_record({"mon": ["0900", "1800"], "sun_flags": {"sunFrtYon": "N"},
                                       "_metadata": {"x": 1}})
    assert record["monSttTme"] == "0900" and record["monEndTme"] == "1800"
    assert record["holSttTme"] == "" and record["sunFrtYon"] == "N"
    assert "_metadata" not in record


def test_weekday_daytime():
    statuses = open_now.statuses_at(at("2026-11-25T14:00"))  # 수요일
    assert statuses[1] == "available"
    assert statuses[2] == "unavailable"
    assert statuses[3] == "unavailable"


def test_holiday_uses_holiday_hours():
    # 2026-05-05(화) 어린이날: 공휴일 운영시간(hol)을 쓴다
    ctx = open_now.context_at(at("2026-05-05T14:00"))
    assert ctx.is_holiday is True
    statuses = open_now.statuses_at(at("2026-05-05T14:00"))
    assert statuses[2] == "available"  # 공휴일 10~16시 (평일이면 10시에 닫힌다)
    assert statuses[1] == "available"  # hol 빈칸 → config의 hours_missing_policy: open


def test_overnight_hours_cover_after_midnight():
    # 금요일 22시~토요일 6시: 토요일 02시는 전날 운영시간이 이어진다
    assert open_now.statuses_at(at("2026-11-28T02:00"))[3] == "available"
    assert open_now.statuses_at(at("2026-11-28T07:00"))[3] == "unavailable"


def test_naive_time_is_seoul_and_utc_is_converted():
    assert open_now.to_seoul(datetime(2026, 11, 25, 14, 0)).hour == 14
    utc = datetime.fromisoformat("2026-11-25T05:00+00:00")
    assert open_now.to_seoul(utc).hour == 14


def test_slot_start():
    assert open_now.slot_start(at("2026-11-25T14:47")).strftime("%H:%M") == "14:30"
    assert open_now.slot_start(at("2026-11-25T14:00")).strftime("%H:%M") == "14:00"


def test_second_request_in_same_slot_hits_cache(fake_db):
    _, calls = fake_db
    first = open_now.coverage_at("oneway", at("2026-11-25T14:05"))
    second = open_now.coverage_at("oneway", at("2026-11-25T14:29"))

    assert first.cache_hit is False and second.cache_hit is True
    assert calls["upsert"] == 1
    assert first.key == second.key == {"weekday": 2, "slot": 28, "mode": "oneway", "is_holiday": False}
    assert second.walk_pct == first.walk_pct


def test_next_slot_and_other_mode_are_new_cache_rows(fake_db):
    _, calls = fake_db
    open_now.coverage_at("oneway", at("2026-11-25T14:05"))
    open_now.coverage_at("oneway", at("2026-11-25T14:35"))
    open_now.coverage_at("roundtrip", at("2026-11-25T14:05"))
    assert calls["upsert"] == 3


def test_same_key_with_different_open_set_recomputes(fake_db):
    # 같은 요일·구간·공휴일 키라도 운영 중 AED 집합이 바뀌면(예: 파이프라인 재실행) 다시 계산한다
    cache, calls = fake_db
    first = open_now.coverage_at("oneway", at("2026-11-25T14:05"))
    cache[(2, 28, "oneway", False)]["aed_set_hash"] = "old-data"
    again = open_now.coverage_at("oneway", at("2026-11-25T14:05"))
    assert again.cache_hit is False
    assert calls["upsert"] == 2
    assert again.walk_pct == first.walk_pct


def test_holiday_key_is_separate(fake_db):
    cov = open_now.coverage_at("oneway", at("2026-05-05T14:05"))
    assert cov.key["is_holiday"] is True


def test_invalid_mode():
    with pytest.raises(ValueError):
        open_now.coverage_at("bike", at("2026-11-25T14:05"))
