"""GET /api/aeds — 화면 범위(bbox) 안 AED 목록 (PRD S-01, 7-4)."""

from fastapi import APIRouter, HTTPException, Query, Response

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
    # FeatureCollection을 PostGIS에서 JSON 문자열 하나로 만든다.
    # 행마다 Python dict를 만들고 FastAPI가 다시 직렬화하면 서울 전체(수천 개)에서 1초를 넘는다 (PRD S-01)
    # json_agg는 행이 없으면 NULL → COALESCE로 빈 배열
    rows = query(
        """
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(json_agg(
                json_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(geom)::json,
                    'properties', json_build_object(
                        'id', id, 'place', place, 'address', address,
                        'gu', gu, 'hours', hours
                    )
                ) ORDER BY id
            ), '[]'::json)
        )::text AS fc
        FROM aed
        WHERE geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)
        """,
        w=west, s=south, e=east, n=north,
    )

    # 이미 완성된 JSON이므로 FastAPI의 재직렬화를 건너뛰고 그대로 보낸다
    return Response(content=rows[0]["fc"], media_type="application/geo+json")
