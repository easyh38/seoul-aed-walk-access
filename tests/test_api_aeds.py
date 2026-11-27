"""GET /api/aeds 테스트. DB 없이 돌도록 query()를 가짜 함수로 바꿔 끼운다."""

import json
import os

# api.db는 import할 때 DATABASE_URL을 요구한다. 엔진은 실제로 접속하지 않으므로 가짜 주소면 충분하다
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost:5432/test")

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import aeds as aeds_router

client = TestClient(app)

# 시청 주변 범위 (seed_dev.sql의 TEST1~5가 들어가는 범위)
CITY_HALL = {"west": 126.97, "south": 37.56, "east": 126.99, "north": 37.58}

FAKE_FEATURE = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [126.9779, 37.5663]},
    "properties": {
        "id": 1,
        "place": "테스트 AED (서울시청 1층)",
        "address": "서울특별시 중구 세종대로 110",
        "gu": "중구",
        "hours": {"mon": ["0000", "2400"]},
    },
}


def _fc_row(features):
    """PostGIS가 돌려주는 한 행: FeatureCollection JSON 문자열."""
    return [{"fc": json.dumps({"type": "FeatureCollection", "features": features})}]


@pytest.fixture
def fake_query(monkeypatch):
    """query()에 넘어온 인자를 기록하고, 정해 둔 행을 돌려주는 가짜 함수."""
    calls = []
    result = {"rows": _fc_row([FAKE_FEATURE])}

    def _fake(sql, **params):
        calls.append(params)
        return result["rows"]

    monkeypatch.setattr(aeds_router, "query", _fake)
    return calls, result


def test_returns_geojson_feature_collection(fake_query):
    res = client.get("/api/aeds", params=CITY_HALL)

    assert res.status_code == 200
    body = res.json()
    assert body["type"] == "FeatureCollection"
    feature = body["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"] == {"type": "Point", "coordinates": [126.9779, 37.5663]}
    assert feature["properties"] == {
        "id": 1,
        "place": "테스트 AED (서울시청 1층)",
        "address": "서울특별시 중구 세종대로 110",
        "gu": "중구",
        "hours": {"mon": ["0000", "2400"]},
    }


def test_passes_bbox_to_sql(fake_query):
    calls, _ = fake_query
    client.get("/api/aeds", params=CITY_HALL)

    assert calls == [{"w": 126.97, "s": 37.56, "e": 126.99, "n": 37.58}]


def test_empty_area_returns_empty_collection(fake_query):
    _, result = fake_query
    result["rows"] = _fc_row([])  # SQL의 COALESCE가 빈 배열을 만든다

    res = client.get("/api/aeds", params=CITY_HALL)

    assert res.status_code == 200
    assert res.json() == {"type": "FeatureCollection", "features": []}


@pytest.mark.parametrize(
    "params",
    [
        {**CITY_HALL, "west": 127.0, "east": 126.9},  # 경도 뒤집힘
        {**CITY_HALL, "south": 37.6, "north": 37.5},  # 위도 뒤집힘
        {**CITY_HALL, "east": CITY_HALL["west"]},  # 폭 0
    ],
)
def test_inverted_bbox_is_400(fake_query, params):
    calls, _ = fake_query
    res = client.get("/api/aeds", params=params)

    assert res.status_code == 400
    assert calls == []  # DB까지 가지 않는다


@pytest.mark.parametrize(
    "params",
    [
        {k: v for k, v in CITY_HALL.items() if k != "north"},  # 값 빠짐
        {**CITY_HALL, "west": "abc"},  # 숫자 아님
        {**CITY_HALL, "north": 91},  # 위도 범위 밖
        {**CITY_HALL, "east": 181},  # 경도 범위 밖
    ],
)
def test_invalid_input_is_422(fake_query, params):
    res = client.get("/api/aeds", params=params)

    assert res.status_code == 422


# ---- open_now ----

@pytest.fixture
def fake_open(monkeypatch, fake_query):
    """지금 시각 운영 여부를 고정한다: 1번 운영 중, 2번 닫힘, 3번 정보 없음."""
    from api.open_now import Context
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 11, 25, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr(aeds_router, "now_seoul", lambda: now)
    monkeypatch.setattr(aeds_router, "statuses_at", lambda when: {1: "available", 2: "unavailable", 3: "unknown"})
    monkeypatch.setattr(aeds_router, "context_at", lambda when: Context(when, False, False, None))
    return fake_query


def test_open_now_passes_only_open_aeds(fake_open):
    calls, _ = fake_open
    res = client.get("/api/aeds", params={**CITY_HALL, "open_now": "true"})

    assert res.status_code == 200
    params = calls[0]
    assert dict(zip(params["ids"], params["statuses"])) == {1: "available", 3: "unknown"}
    assert params["at"] == "2026-11-25T14:00:00+09:00"
    assert params["is_holiday"] is False


def test_open_now_default_is_off(fake_open):
    calls, _ = fake_open
    client.get("/api/aeds", params=CITY_HALL)
    assert "ids" not in calls[0]
