"""GET /api/nearest 테스트. DB(query)와 Valhalla(walk_matrix, walk_route)를 가짜 함수로 바꿔 끼운다."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost:5432/test")

import pytest
import requests
from fastapi.testclient import TestClient

from api import valhalla
from api.main import app
from api.routers import nearest as nearest_router

client = TestClient(app)

CITY_HALL = {"lat": 37.5665, "lon": 126.978}


def _cand(aed_id, straight_m, lat=37.566, lon=126.977):
    return {
        "id": aed_id, "place": f"장소{aed_id}", "address": f"주소{aed_id}", "gu": "중구",
        "lat": lat, "lon": lon, "straight_m": straight_m,
    }


ROUTE = [[126.978, 37.5665], [126.9775, 37.5662], [126.977, 37.566]]


@pytest.fixture
def fake(monkeypatch):
    """inside, candidates, walks, route를 테스트마다 바꿔 쓰는 가짜 DB·Valhalla."""
    state = {
        "inside": True,
        "candidates": [_cand(1, 100.0), _cand(2, 200.0), _cand(3, 300.0)],
        "walks": [{"time_s": 300, "distance_m": 400}, {"time_s": 150, "distance_m": 220},
                  {"time_s": 200, "distance_m": 310}],
        "route": ROUTE,
        "matrix_error": None,
        "route_error": None,
    }
    calls = {"sql": [], "route": []}

    def _query(sql, **params):
        calls["sql"].append(params)
        if "EXISTS" in sql:
            return [{"inside": state["inside"]}]
        return state["candidates"]

    def _matrix(origin, targets):
        if state["matrix_error"]:
            raise state["matrix_error"]
        return state["walks"][: len(targets)]

    def _route(origin, dest):
        calls["route"].append(dest)
        if state["route_error"]:
            raise state["route_error"]
        return state["route"]

    monkeypatch.setattr(nearest_router, "query", _query)
    monkeypatch.setattr(nearest_router, "walk_matrix", _matrix)
    monkeypatch.setattr(nearest_router, "walk_route", _route)
    return state, calls


def test_picks_fastest_walk_not_straight_first(fake):
    state, calls = fake
    res = client.get("/api/nearest", params=CITY_HALL)

    assert res.status_code == 200
    body = res.json()
    # 도보 시간 150초인 2번이 1등, 직선 1등(1번)과 다르다
    assert body["nearest"]["id"] == 2
    assert body["nearest"]["walk_time_s"] == 150
    assert body["straight_first_differs"] is True
    assert body["straight_first"]["id"] == 1
    assert body["straight_first"]["walk_time_s"] == 300
    # 후보 2개는 도보 시간 순서
    assert [a["id"] for a in body["alternatives"]] == [3, 1]
    # 경로는 1등까지만 한 번 호출, LineString으로 돌려준다
    assert len(calls["route"]) == 1
    route = body["nearest"]["route"]
    assert route["geometry"]["type"] == "LineString"
    assert route["geometry"]["coordinates"] == ROUTE


def test_same_as_straight_first(fake):
    state, _ = fake
    state["walks"] = [{"time_s": 100, "distance_m": 120}, {"time_s": 250, "distance_m": 300},
                      {"time_s": 400, "distance_m": 500}]
    body = client.get("/api/nearest", params=CITY_HALL).json()
    assert body["nearest"]["id"] == 1
    assert body["straight_first_differs"] is False


def test_outside_seoul_is_400(fake):
    state, _ = fake
    state["inside"] = False
    res = client.get("/api/nearest", params={"lat": 37.45, "lon": 126.70})
    assert res.status_code == 400
    assert "서울" in res.json()["detail"]


def test_no_candidates_is_404(fake):
    state, _ = fake
    state["candidates"] = []
    res = client.get("/api/nearest", params=CITY_HALL)
    assert res.status_code == 404
    assert "AED가 없습니다" in res.json()["detail"]


def test_unreachable_candidates_are_skipped(fake):
    state, _ = fake
    state["walks"] = [None, {"time_s": 500, "distance_m": 600}, None]
    body = client.get("/api/nearest", params=CITY_HALL).json()
    assert body["nearest"]["id"] == 2
    assert body["alternatives"] == []
    assert body["straight_first"]["walk_time_s"] is None


def test_all_unreachable_is_404(fake):
    state, _ = fake
    state["walks"] = [None, None, None]
    res = client.get("/api/nearest", params=CITY_HALL)
    assert res.status_code == 404
    assert "도보 경로를 찾을 수 없습니다" in res.json()["detail"]


@pytest.mark.parametrize(
    "error, where, status",
    [
        (valhalla.ValhallaUnavailable("timeout"), "matrix_error", 503),
        (valhalla.NoRouteError("no edges"), "matrix_error", 404),
        (valhalla.ValhallaUnavailable("down"), "route_error", 503),
        (valhalla.NoRouteError("no path"), "route_error", 404),
    ],
)
def test_valhalla_failure_is_guided_not_500(fake, error, where, status):
    state, _ = fake
    state[where] = error
    res = client.get("/api/nearest", params=CITY_HALL)
    assert res.status_code == status
    assert "도보 경로를 찾을 수 없습니다" in res.json()["detail"]


def test_missing_or_bad_coords_is_422(fake):
    assert client.get("/api/nearest", params={"lat": 37.5}).status_code == 422
    assert client.get("/api/nearest", params={"lat": 100, "lon": 126.9}).status_code == 422


# ---- api/valhalla.py 단위 테스트 (requests.post를 가짜로) ----

class _Res:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_decode_polyline6_known_value():
    # Valhalla 문서 예시와 같은 방식으로 인코딩된 두 점
    coords = valhalla.decode_polyline6("_izlhA~rlgdF_{geC~ywl@")
    assert coords == [[-120.2, 38.5], [-120.95, 40.7]]


def test_walk_matrix_parses_and_marks_unreachable(monkeypatch):
    payload = {"sources_to_targets": [[
        {"from_index": 0, "to_index": 0, "time": 823, "distance": 1.161},
        {"from_index": 0, "to_index": 1, "time": None, "distance": None},
    ]]}
    monkeypatch.setattr(valhalla.requests, "post", lambda *a, **k: _Res(200, payload))
    result = valhalla.walk_matrix((37.5, 126.9), [(37.51, 126.91), (37.52, 126.92)])
    assert result == [{"time_s": 823.0, "distance_m": 1161.0}, None]


def test_walk_matrix_timeout_is_unavailable(monkeypatch):
    def _raise(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(valhalla.requests, "post", _raise)
    with pytest.raises(valhalla.ValhallaUnavailable):
        valhalla.walk_matrix((37.5, 126.9), [(37.51, 126.91)])


def test_walk_route_400_is_no_route(monkeypatch):
    monkeypatch.setattr(valhalla.requests, "post", lambda *a, **k: _Res(400, {"error": "No suitable edges"}))
    with pytest.raises(valhalla.NoRouteError):
        valhalla.walk_route((37.5, 126.9), (37.51, 126.91))
