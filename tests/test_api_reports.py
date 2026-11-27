"""POST /api/reports 테스트. DB 없이 돌도록 execute()를 가짜 함수로 바꿔 끼운다."""

import os
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost:5432/test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from api.main import app
from api.routers import reports as reports_router

client = TestClient(app)

EXISTING_AED = 1
CREATED_AT = datetime(2026, 11, 28, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_execute(monkeypatch):
    """있는 AED(id=1)에만 저장되는 가짜 execute. 넘어온 인자를 기록한다."""
    calls = []
    state = {"error": None}

    def _fake(sql, **params):
        calls.append(params)
        if state["error"]:
            raise state["error"]
        if params["aed_id"] != EXISTING_AED:
            return []  # WHERE EXISTS가 거짓 → 0행
        return [{"id": 10, "aed_id": params["aed_id"], "type": params["type"],
                 "status": "접수", "created_at": CREATED_AT}]

    monkeypatch.setattr(reports_router, "execute", _fake)
    return calls, state


def test_valid_report_is_201(fake_execute):
    calls, _ = fake_execute
    res = client.post("/api/reports", json={"aed_id": 1, "type": "missing", "description": "AED가 없어요"})

    assert res.status_code == 201
    body = res.json()
    assert body["id"] == 10
    assert body["status"] == "접수"
    assert body["created_at"].startswith("2026-11-28T14:00:00")
    assert calls == [{"aed_id": 1, "type": "missing", "description": "AED가 없어요"}]


def test_description_is_optional_and_blank_becomes_null(fake_execute):
    calls, _ = fake_execute
    assert client.post("/api/reports", json={"aed_id": 1, "type": "unusable"}).status_code == 201
    assert client.post("/api/reports", json={"aed_id": 1, "type": "unusable", "description": "   "}).status_code == 201
    assert [c["description"] for c in calls] == [None, None]


def test_500_chars_is_ok_501_is_422(fake_execute):
    calls, _ = fake_execute
    ok = client.post("/api/reports", json={"aed_id": 1, "type": "missing", "description": "가" * 500})
    too_long = client.post("/api/reports", json={"aed_id": 1, "type": "missing", "description": "가" * 501})

    assert ok.status_code == 201
    assert too_long.status_code == 422
    assert len(calls) == 1  # 422는 DB까지 가지 않는다


@pytest.mark.parametrize(
    "payload",
    [
        {"aed_id": 1, "type": "broken"},          # 정해지지 않은 유형
        {"aed_id": 1},                            # 유형 없음
        {"type": "missing"},                      # aed_id 없음
        {"aed_id": 0, "type": "missing"},         # 0 이하 id
        {"aed_id": "abc", "type": "missing"},     # 숫자가 아님
        {"aed_id": 1, "type": "missing", "lat": 37.5, "lon": 126.9},  # 위치(개인정보) 거부
    ],
)
def test_invalid_input_is_422(fake_execute, payload):
    calls, _ = fake_execute
    assert client.post("/api/reports", json=payload).status_code == 422
    assert calls == []


def test_unknown_aed_is_404(fake_execute):
    res = client.post("/api/reports", json={"aed_id": 999999, "type": "missing"})
    assert res.status_code == 404
    assert "AED를 찾을 수 없습니다" in res.json()["detail"]


class _PgError(Exception):
    def __init__(self, pgcode):
        super().__init__(pgcode)
        self.pgcode = pgcode


def test_fk_race_is_404_not_500(fake_execute):
    _, state = fake_execute
    state["error"] = IntegrityError("INSERT", {}, _PgError("23503"))
    res = client.post("/api/reports", json={"aed_id": 1, "type": "missing"})
    assert res.status_code == 404


def test_other_integrity_error_is_not_hidden(fake_execute):
    _, state = fake_execute
    state["error"] = IntegrityError("INSERT", {}, _PgError("23514"))  # check_violation
    with pytest.raises(IntegrityError):
        client.post("/api/reports", json={"aed_id": 1, "type": "missing"})
