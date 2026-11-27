"""Valhalla 호출 함수 (PRD S-04, guideline 4-4).

- walk_matrix: 출발점 1개 → 후보 여러 개의 실제 도보 시간·거리 (/sources_to_targets 한 번)
- walk_route: 출발점 → 도착점 경로선 (/route, polyline6을 [경도, 위도] 목록으로 풀어서)

실패는 두 종류로 나눠 라우터가 안내 문구를 고르게 한다.
- ValhallaUnavailable: 서버가 꺼졌거나 시간 초과·5xx (잠시 후 다시 시도)
- NoRouteError: Valhalla가 경로를 못 찾음 (강 한가운데처럼 근처에 보행로가 없는 좌표 등)
"""

from __future__ import annotations

import requests

from api.config import valhalla_base_url, walking_speed_kmh

TIMEOUT_SECONDS = 5  # 화면이 2초 안에 응답해야 하므로 오래 기다리지 않는다


class ValhallaError(Exception):
    """Valhalla 호출 실패의 공통 부모."""


class ValhallaUnavailable(ValhallaError):
    """연결 실패, 시간 초과, 5xx, 응답 형식 오류."""


class NoRouteError(ValhallaError):
    """Valhalla가 경로를 찾지 못했다 (4xx)."""


def _costing() -> dict:
    return {
        "costing": "pedestrian",
        "costing_options": {"pedestrian": {"walking_speed": walking_speed_kmh()}},
    }


def _post(endpoint: str, body: dict) -> dict:
    try:
        res = requests.post(
            f"{valhalla_base_url()}/{endpoint}", json=body, timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:  # 연결 실패, 시간 초과
        raise ValhallaUnavailable(str(exc)) from exc

    if 400 <= res.status_code < 500:
        raise NoRouteError(res.text[:200])
    if res.status_code != 200:
        raise ValhallaUnavailable(f"HTTP {res.status_code}: {res.text[:200]}")

    try:
        return res.json()
    except ValueError as exc:
        raise ValhallaUnavailable("응답이 JSON이 아닙니다.") from exc


def walk_matrix(origin: tuple[float, float], targets: list[tuple[float, float]]) -> list[dict | None]:
    """origin(위도, 경도) → targets 각각의 도보 결과.

    반환 순서는 targets와 같다. 원소는 {"time_s": 초, "distance_m": 미터}이고,
    걸어서 갈 수 없는 후보는 None이다.
    """
    body = {
        "sources": [{"lat": origin[0], "lon": origin[1]}],
        "targets": [{"lat": lat, "lon": lon} for lat, lon in targets],
        **_costing(),
    }
    data = _post("sources_to_targets", body)

    try:
        row = data["sources_to_targets"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValhallaUnavailable("sources_to_targets 응답 형식이 다릅니다.") from exc

    results: list[dict | None] = [None] * len(targets)
    for cell in row:
        index = cell.get("to_index")
        time_s = cell.get("time")
        distance_km = cell.get("distance")
        # 도달할 수 없는 후보는 time/distance가 null로 온다
        if index is None or time_s is None or distance_km is None:
            continue
        if 0 <= index < len(targets):
            results[index] = {"time_s": float(time_s), "distance_m": float(distance_km) * 1000}
    return results


def walk_route(origin: tuple[float, float], dest: tuple[float, float]) -> list[list[float]]:
    """origin → dest 도보 경로의 좌표 목록 [[경도, 위도], ...] (GeoJSON 순서)."""
    body = {
        "locations": [
            {"lat": origin[0], "lon": origin[1]},
            {"lat": dest[0], "lon": dest[1]},
        ],
        "units": "kilometers",
        **_costing(),
    }
    data = _post("route", body)

    try:
        shapes = [leg["shape"] for leg in data["trip"]["legs"]]
    except (KeyError, TypeError) as exc:
        raise ValhallaUnavailable("route 응답 형식이 다릅니다.") from exc

    coords: list[list[float]] = []
    for shape in shapes:
        try:
            points = decode_polyline6(shape)
        except (ValueError, TypeError) as exc:
            raise ValhallaUnavailable("경로 shape를 풀 수 없습니다.") from exc
        # 구간이 여러 개면 이어지는 점이 겹치므로 첫 점을 뺀다
        coords.extend(points[1:] if coords else points)
    if len(coords) < 2:
        raise NoRouteError("경로 좌표가 2개 미만입니다.")
    return coords


def decode_polyline6(encoded: str) -> list[list[float]]:
    """Valhalla shape(Google polyline, 정밀도 6자리)를 [[경도, 위도], ...]로 푼다."""
    coords: list[list[float]] = []
    index = lat = lon = 0
    length = len(encoded)

    while index < length:
        deltas = []
        for _ in range(2):  # 위도, 경도 순서로 하나씩
            result = shift = 0
            while True:
                if index >= length:
                    raise ValueError("polyline 문자열이 중간에 끝났습니다.")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coords.append([lon / 1e6, lat / 1e6])

    return coords
