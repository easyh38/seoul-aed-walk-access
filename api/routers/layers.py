"""GET /api/layers/coverage, /api/layers/deadzone — 지도용 다각형 (PRD S-02, 7-4)."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from api.db import query
from api.open_now import coverage_at, now_seoul

router = APIRouter(prefix="/api/layers", tags=["layers"])

Mode = Literal["oneway", "roundtrip"]
MODE_QUERY = Query(
    "oneway", description="oneway: 편도 4분 / roundtrip: 왕복 기준(편도 2분)"
)
OPEN_NOW_QUERY = Query(
    False, description="true면 지금(서울 시각) 운영 중인 AED만으로 계산한 다각형 (30분 단위 캐시)"
)

KIND_LABEL = {"coverage": "도달 구역", "deadzone": "사각지대"}

# 07_build_metrics.py가 미리 합쳐 둔 다각형을 그대로 꺼낸다.
# aeds와 같이 GeoJSON을 PostGIS에서 문자열 하나로 만들어 Python 재직렬화를 건너뛴다.
# 좌표는 소수 6자리(약 0.1m)로 줄여 응답 크기를 줄인다 (기본 9자리는 화면에 의미 없음)
LAYER_SQL = """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'features', json_build_array(
            json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(geom, 6)::json,
                'properties', json_build_object('mode', mode, 'kind', kind)
            )
        )
    )::text AS fc
    FROM layer
    WHERE mode = :mode AND kind = :kind AND geom IS NOT NULL
"""


def _layer(kind: str, mode: str) -> Response:
    rows = query(LAYER_SQL, mode=mode, kind=kind)
    if not rows:
        # 지표 계산(07)을 안 돌린 DB: 500 대신 이유를 알려 준다
        raise HTTPException(
            status_code=404,
            detail=(
                f"아직 {mode} {KIND_LABEL[kind]} 레이어가 없습니다. "
                "파이프라인(지표 계산)을 먼저 실행하세요."
            ),
        )
    return Response(content=rows[0]["fc"], media_type="application/geo+json")


# open_now: coverage_cache에 저장해 둔 운영 중 AED 도달 구역을 꺼낸다.
# 사각지대는 저장하지 않고 서울 경계 − 도달 구역으로 그때 만든다 (실측 약 0.1초).
# 운영 중 AED가 0개면 coverage가 NULL → 도달 구역은 빈 목록, 사각지대는 서울 전체.
OPEN_LAYER_SQL = {
    "coverage": """
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', CASE WHEN c.coverage IS NULL THEN '[]'::json ELSE json_build_array(
                json_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(c.coverage, 6)::json,
                    'properties', json_build_object(
                        'mode', c.mode, 'kind', 'coverage', 'open_now', true,
                        'at', :at, 'walk_pct', c.walk_pct
                    )
                )
            ) END
        )::text AS fc
        FROM coverage_cache c
        WHERE weekday = :weekday AND slot = :slot AND mode = :mode AND is_holiday = :is_holiday
    """,
    "deadzone": """
        WITH seoul AS (SELECT ST_UnaryUnion(ST_Collect(geom)) AS g FROM district)
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', json_build_array(
                json_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(
                        CASE WHEN c.coverage IS NULL THEN ST_Multi(seoul.g)
                             ELSE ST_Multi(ST_CollectionExtract(ST_Difference(seoul.g, c.coverage), 3))
                        END, 6)::json,
                    'properties', json_build_object(
                        'mode', c.mode, 'kind', 'deadzone', 'open_now', true,
                        'at', :at, 'walk_pct', c.walk_pct
                    )
                )
            )
        )::text AS fc
        FROM coverage_cache c, seoul
        WHERE weekday = :weekday AND slot = :slot AND mode = :mode AND is_holiday = :is_holiday
    """,
}


def _open_layer(kind: str, mode: str) -> Response:
    cov = coverage_at(mode, now_seoul())  # 캐시에 없으면 여기서 계산·저장
    rows = query(
        OPEN_LAYER_SQL[kind], **cov.key, at=cov.slot_start.isoformat(timespec="minutes")
    )
    if not rows:
        raise HTTPException(status_code=503, detail="운영 중 커버리지를 계산하지 못했습니다. 다시 시도하세요.")
    return Response(content=rows[0]["fc"], media_type="application/geo+json")


@router.get("/coverage")
def coverage(mode: Mode = MODE_QUERY, open_now: bool = OPEN_NOW_QUERY):
    """실보행 도달 구역 다각형을 GeoJSON FeatureCollection(Feature 1개)으로 돌려준다.

    open_now=true면 지금 운영 중인 AED만으로 계산한 도달 구역이다.
    같은 30분 구간의 두 번째 요청부터는 coverage_cache에서 바로 꺼낸다.
    """
    return _open_layer("coverage", mode) if open_now else _layer("coverage", mode)


@router.get("/deadzone")
def deadzone(mode: Mode = MODE_QUERY, open_now: bool = OPEN_NOW_QUERY):
    """사각지대(서울 경계 − 도달 구역) 다각형을 GeoJSON FeatureCollection으로 돌려준다.

    open_now=true면 지금 운영 중인 AED 기준 사각지대다.
    """
    return _open_layer("deadzone", mode) if open_now else _layer("deadzone", mode)
