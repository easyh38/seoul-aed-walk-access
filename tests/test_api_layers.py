"""GET /api/layers/coverage, /api/layers/deadzone 테스트. DB 없이 돌도록 query()를 가짜 함수로 바꿔 끼운다."""

import json
import os

# api.db는 import할 때 DATABASE_URL을 요구한다. 엔진은 실제로 접속하지 않으므로 가짜 주소면 충분하다
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost:5432/test")

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import layers as layers_router

client = TestClient(app)

KINDS = ["coverage", "deadzone"]
MODES = ["oneway", "roundtrip"]

SQUARE = {
    "type": "MultiPolygon",
    "coordinates": [[[[126.97, 37.56], [126.98, 37.56], [126.98, 37.57], [126.97, 37.56]]]],
}


def _fc_row(mode, kind):
    """PostGIS가 돌려주는 한 행: FeatureCollection JSON 문자열."""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": SQUARE, "properties": {"mode": mode, "kind": kind}}
        ],
    }
    return [{"fc": json.dumps(fc)}]


@pytest.fixture
def fake_query(monkeypatch):
    """query()에 넘어온 인자를 기록하고, 넘어온 mode·kind에 맞는 행을 돌려주는 가짜 함수."""
    calls = []
    state = {"empty": False}

    def _fake(sql, **params):
        calls.append(params)
        return [] if state["empty"] else _fc_row(params["mode"], params.get("kind", "coverage"))

    monkeypatch.setattr(layers_router, "query", _fake)
    return calls, state


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", MODES)
def test_returns_layer_as_geojson(fake_query, kind, mode):
    calls, _ = fake_query
    res = client.get(f"/api/layers/{kind}", params={"mode": mode})

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/geo+json")
    body = res.json()
    assert body["type"] == "FeatureCollection"
    assert body["features"][0]["geometry"]["type"] == "MultiPolygon"
    assert body["features"][0]["properties"] == {"mode": mode, "kind": kind}
    assert calls == [{"mode": mode, "kind": kind}]


@pytest.mark.parametrize("kind", KINDS)
def test_default_mode_is_oneway(fake_query, kind):
    calls, _ = fake_query
    res = client.get(f"/api/layers/{kind}")

    assert res.status_code == 200
    assert calls == [{"mode": "oneway", "kind": kind}]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["", "both", "ONEWAY", "4min"])
def test_invalid_mode_is_422(fake_query, kind, mode):
    calls, _ = fake_query
    res = client.get(f"/api/layers/{kind}", params={"mode": mode})

    assert res.status_code == 422
    assert calls == []  # DB까지 가지 않는다


@pytest.mark.parametrize("kind", KINDS)
def test_missing_layer_is_404(fake_query, kind):
    _, state = fake_query
    state["empty"] = True

    res = client.get(f"/api/layers/{kind}", params={"mode": "roundtrip"})

    assert res.status_code == 404
    assert "roundtrip" in res.json()["detail"]


def test_unknown_layer_kind_is_404(fake_query):
    calls, _ = fake_query
    res = client.get("/api/layers/straight")

    assert res.status_code == 404
    assert calls == []


# ---- open_now ----

@pytest.fixture
def fake_open(monkeypatch, fake_query):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from api.open_now import Coverage

    start = datetime(2026, 11, 25, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    seen = []

    def _coverage(mode, when):
        seen.append(mode)
        key = {"weekday": 2, "slot": 28, "mode": mode, "is_holiday": False}
        return Coverage(key, 60.0, 11000, start, False, cache_hit=True)

    monkeypatch.setattr(layers_router, "coverage_at", _coverage)
    return fake_query, seen


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", MODES)
def test_open_now_reads_coverage_cache(fake_open, kind, mode):
    (calls, _), seen = fake_open
    res = client.get(f"/api/layers/{kind}", params={"mode": mode, "open_now": "true"})

    assert res.status_code == 200
    assert seen == [mode]
    assert calls == [{"weekday": 2, "slot": 28, "mode": mode, "is_holiday": False,
                      "at": "2026-11-25T14:00+09:00"}]


def test_open_now_off_uses_precomputed_layer(fake_open):
    (calls, _), seen = fake_open
    client.get("/api/layers/coverage", params={"mode": "oneway"})
    assert seen == []
    assert calls == [{"mode": "oneway", "kind": "coverage"}]
