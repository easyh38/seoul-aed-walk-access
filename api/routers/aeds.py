"""GET /api/aeds — 화면 범위(bbox) 안 AED 목록 (PRD S-01, 7-4)."""

from fastapi import APIRouter, HTTPException, Query, Response

from api.db import query
from api.open_now import OPEN_STATUSES, context_at, now_seoul, statuses_at

router = APIRouter(prefix="/api", tags=["aeds"])


@router.get("/aeds")
def aeds(
    west: float = Query(..., ge=-180, le=180, description="화면 왼쪽 경도"),
    south: float = Query(..., ge=-90, le=90, description="화면 아래쪽 위도"),
    east: float = Query(..., ge=-180, le=180, description="화면 오른쪽 경도"),
    north: float = Query(..., ge=-90, le=90, description="화면 위쪽 위도"),
    open_now: bool = Query(False, description="true면 지금(서울 시각) 운영 중인 AED만"),
):
    """현재 화면 범위 안의 AED를 GeoJSON FeatureCollection으로 돌려준다.

    서울 전체를 한 번에 보내지 않고 지도가 보여 주는 범위만 요청한다 (PRD S-01).
    open_now=true면 운영 중 AED만 남기고, 각 AED에 open_status(available / unknown)를 붙인다.
    unknown은 운영시간 정보를 확인할 수 없어 운영 중으로 간주한 AED다 (PRD S-06).
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
    if open_now:
        return _open_aeds(west, south, east, north)

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
                        'gu', gu, 'hours', hours,
                        'location_source', location_source,
                        'location_flags', location_flags
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


# 운영 중 AED만: 파이썬에서 계산한 (id, 상태) 목록을 unnest로 표처럼 만들어 JOIN한다.
# id = ANY(배열)보다 해시 조인이 되어 1만 건 넘는 목록에도 빠르다.
OPEN_AEDS_SQL = """
    SELECT json_build_object(
        'type', 'FeatureCollection',
        'open_now', json_build_object('at', :at, 'is_holiday', :is_holiday),
        'features', COALESCE(json_agg(
            json_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(a.geom)::json,
                'properties', json_build_object(
                    'id', a.id, 'place', a.place, 'address', a.address,
                    'gu', a.gu, 'hours', a.hours, 'open_status', o.status,
                    'location_source', a.location_source,
                    'location_flags', a.location_flags
                )
            ) ORDER BY a.id
        ), '[]'::json)
    )::text AS fc
    FROM aed a
    JOIN unnest(CAST(:ids AS int[]), CAST(:statuses AS text[])) AS o(id, status)
        ON o.id = a.id
    WHERE a.geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)
"""


def _open_aeds(west, south, east, north) -> Response:
    now = now_seoul()
    ctx = context_at(now)
    open_pairs = [(i, st) for i, st in statuses_at(now).items() if st in OPEN_STATUSES]
    rows = query(
        OPEN_AEDS_SQL,
        w=west, s=south, e=east, n=north,
        ids=[i for i, _ in open_pairs],
        statuses=[st for _, st in open_pairs],
        at=now.isoformat(timespec="seconds"),
        is_holiday=ctx.is_holiday,
    )
    return Response(content=rows[0]["fc"], media_type="application/geo+json")
