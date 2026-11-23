"""GET /api/aeds — 화면 범위(bbox) 안 AED 목록 (PRD S-01, 7-4)."""

import json

from fastapi import APIRouter, HTTPException, Query

from api.db import query

router = APIRouter(prefix="/api", tags=["aeds"])


@router.get("/aeds")
def aeds(
    west: float = Query(..., ge=-180, le=180, description="화면 왼쪽 경도"),
    south: float = Query(..., ge=-90, le=90, description="화면 아래쪽 위도"),
    east: float = Query(..., ge=-180, le=180, description="화면 오른쪽 경도"),
    north: float = Query(..., ge=-90, le=90, description="화면 위쪽 위도"),
):
    """현재 화면 범위 안의 AED를 GeoJSON FeatureCollection으로 돌려준다.

    서울 전체를 한 번에 보내지 않고 지도가 보여 주는 범위만 요청한다 (PRD S-01).
    open_now(운영 중만 보기)는 4-2 구현 순서 7번에서 추가한다.
    """
    if west >= east or south >= north:
        # 범위가 뒤집히면 결과가 0개로 나와 원인을 찾기 어렵다: 이유를 알려 준다
        raise HTTPException(
            status_code=400,
            detail="범위가 잘못되었습니다. west < east, south < north 이어야 합니다.",
        )

    # geom && 범위: GIST 인덱스(aed_geom_idx)를 쓰는 사각형 겹침 검사
    rows = query(
        """
        SELECT id, place, address, gu, hours, ST_AsGeoJSON(geom) AS g
        FROM aed
        WHERE geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)
        ORDER BY id
        """,
        w=west, s=south, e=east, n=north,
    )

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(r["g"]),
                "properties": {
                    k: r[k] for k in ("id", "place", "address", "gu", "hours")
                },
            }
            for r in rows
        ],
    }
