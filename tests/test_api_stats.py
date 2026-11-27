"""GET /api/stats 테스트. DB 없이 돌도록 query()를 가짜 함수로 바꿔 끼운다."""

import os
from datetime import datetime, timezone
from decimal import Decimal

# api.db는 import할 때 DATABASE_URL을 요구한다. 엔진은 실제로 접속하지 않으므로 가짜 주소면 충분하다
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost:5432/test")

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import stats as stats_router

client = TestClient(app)

RUN_AT = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def row(scope, walk, straight="80.00", deadzone="3.5", aed=300, run_id=7):
    """DB가 돌려주는 형태(NUMERIC → Decimal)를 흉내 낸 한 행."""
    return {
        "run_id": run_id,
        "run_at": RUN_AT,
        "scope": scope,
        "straight_pct": Decimal(straight),
        "walk_pct": Decimal(walk),
        "diff_pp": Decimal(straight) - Decimal(walk),
        "deadzone_km2": Decimal(deadzone),
        "aed_count": aed,
    }


@pytest.fixture
def fake_query(monkeypatch):
    calls = []
    result = {
        "rows": [
            row("중구", "70.00"),
            row("서울", "65.50", straight="75.00", deadzone="150.0", aed=9000),
            row("강남구", "72.00"),
            row("종로구", "70.00"),  # 중구와 동점 → 이름순
        ]
    }

    def _fake(sql, **params):
        calls.append(params)
        return result["rows"]

    monkeypatch.setattr(stats_router, "query", _fake)
    return calls, result


def test_separates_seoul_summary(fake_query):
    res = client.get("/api/stats")

    assert res.status_code == 200
    body = res.json()
    assert body["run_id"] == 7
    assert body["mode"] == "oneway"
    assert body["seoul"] == {
        "straight_pct": 75.0,
        "walk_pct": 65.5,
        "diff_pp": 9.5,
        "deadzone_km2": 150.0,
        "aed_count": 9000,
    }
    assert all(d["gu"] != "서울" for d in body["districts"])


def test_districts_ranked_by_walk_pct(fake_query):
    districts = client.get("/api/stats").json()["districts"]

    assert [(d["rank"], d["gu"]) for d in districts] == [
        (1, "강남구"),
        (2, "종로구"),  # 중구와 동점: 가나다순으로 종 < 중
        (3, "중구"),
    ]


def test_numbers_are_json_numbers(fake_query):
    d = client.get("/api/stats").json()["districts"][0]

    assert isinstance(d["walk_pct"], float)
    assert isinstance(d["aed_count"], int)


@pytest.mark.parametrize("mode", ["oneway", "roundtrip"])
def test_passes_mode_to_sql(fake_query, mode):
    calls, _ = fake_query
    res = client.get("/api/stats", params={"mode": mode})

    assert res.json()["mode"] == mode
    assert calls == [{"mode": mode}]


def test_no_metrics_is_404(fake_query):
    _, result = fake_query
    result["rows"] = []

    res = client.get("/api/stats", params={"mode": "roundtrip"})

    assert res.status_code == 404
    assert "roundtrip" in res.json()["detail"]


def test_invalid_mode_is_422(fake_query):
    calls, _ = fake_query
    res = client.get("/api/stats", params={"mode": "walk"})

    assert res.status_code == 422
    assert calls == []


# ---- open_now ----

def test_open_now_adds_current_walk_pct(fake_query, monkeypatch):
    from zoneinfo import ZoneInfo

    from api.open_now import Coverage

    start = datetime(2026, 11, 25, 22, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr(
        stats_router, "coverage_at",
        lambda mode, when: Coverage({}, 48.5, 6512, start, False, cache_hit=True),
    )
    body = client.get("/api/stats", params={"open_now": "true"}).json()

    assert body["open_now"] == {
        "at": "2026-11-25T22:00:00+09:00",
        "is_holiday": False,
        "open_aed_count": 6512,
        "walk_pct": 48.5,
    }
    assert body["seoul"] is not None  # 전체 AED 기준 값은 그대로


def test_open_now_off_has_no_block(fake_query, monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("open_now=false면 계산하지 않는다")

    monkeypatch.setattr(stats_router, "coverage_at", _fail)
    assert "open_now" not in client.get("/api/stats").json()
