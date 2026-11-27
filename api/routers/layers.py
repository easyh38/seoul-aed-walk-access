"""GET /api/layers/coverage, /api/layers/deadzone — 지도용 다각형 (PRD S-02, 7-4)."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from api.db import query

router = APIRouter(prefix="/api/layers", tags=["layers"])

Mode = Literal["oneway", "roundtrip"]
MODE_QUERY = Query(
    "oneway", description="oneway: 편도 4분 / roundtrip: 왕복 기준(편도 2분)"
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


@router.get("/coverage")
def coverage(mode: Mode = MODE_QUERY):
    """실보행 도달 구역 다각형을 GeoJSON FeatureCollection(Feature 1개)으로 돌려준다.

    open_now(운영 중 AED만 반영)는 4-2 구현 순서 7번에서 추가한다.
    """
    return _layer("coverage", mode)


@router.get("/deadzone")
def deadzone(mode: Mode = MODE_QUERY):
    """사각지대(서울 경계 − 도달 구역) 다각형을 GeoJSON FeatureCollection으로 돌려준다.

    open_now(운영 중 AED만 반영)는 4-2 구현 순서 7번에서 추가한다.
    """
    return _layer("deadzone", mode)
