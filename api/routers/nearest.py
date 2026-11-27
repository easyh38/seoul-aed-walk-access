"""GET /api/nearest — 클릭한 지점에서 걸어서 가장 빨리 닿는 AED와 경로 (PRD S-04, guideline 4-4).

처리 순서
1. 서울 안인지 확인 (district와 ST_Contains) → 밖이면 400
2. 직선 1km 안 후보 5개 (EPSG:5179 거리 순) → 없으면 404
3. Valhalla /sources_to_targets 한 번으로 후보 5개의 실제 도보 시간을 받아 정렬
4. 1등 AED까지만 /route를 호출해 경로선(GeoJSON LineString)을 만든다
5. 1등 + 후보 2개 + straight_first_differs(직선 1등과 도보 1등이 다른지)

aed_id를 주면 후보 검색(2단계) 대신 그 AED 하나까지의 도보 시간과 경로를 돌려준다
(지도 팝업의 "여기까지 경로 보기"). 이때는 반경 제한과 open_now 필터를 쓰지 않는다.

모든 AED에 지금 시각의 open_status(available / unknown / unavailable)를 붙인다.
open_now=true면 2단계 후보를 운영 중(available, unknown) AED로만 고른다.

클릭 좌표는 계산에만 쓰고 DB나 로그에 저장하지 않는다 (PRD 8-6).
"""

from fastapi import APIRouter, HTTPException, Query

from api.db import query
from api.open_now import OPEN_STATUSES, now_seoul, statuses_at
from api.valhalla import NoRouteError, ValhallaUnavailable, walk_matrix, walk_route

router = APIRouter(prefix="/api", tags=["nearest"])

SEARCH_RADIUS_M = 1000  # 직선 1km 안에서만 후보를 찾는다
CANDIDATE_LIMIT = 5
ALTERNATIVE_COUNT = 2

NO_ROUTE_DETAIL = "도보 경로를 찾을 수 없습니다. 주변 도로 위의 지점을 다시 눌러 보세요."
UNAVAILABLE_DETAIL = "도보 경로를 찾을 수 없습니다. 경로 서버에 연결하지 못했으니 잠시 후 다시 시도하세요."

INSIDE_SEOUL_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM district
        WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
    ) AS inside
"""

# 직선거리는 미터 좌표계(EPSG:5179)로 잰다. 4326의 <->는 도(degree) 단위라
# 서울 위도에서 경도 1도(약 88km)와 위도 1도(약 111km)가 달라 순서가 틀어질 수 있다.
# ST_Transform(geom, 5179) 식 인덱스(aed_geom_5179_idx)를 그대로 쓴다.
CANDIDATES_SQL = """
    WITH p AS (
        SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 5179) AS g
    )
    SELECT a.id, a.place, a.address, a.gu,
           ST_Y(a.geom) AS lat, ST_X(a.geom) AS lon,
           ST_Distance(ST_Transform(a.geom, 5179), p.g) AS straight_m
    FROM aed a, p
    WHERE ST_DWithin(ST_Transform(a.geom, 5179), p.g, :radius)
      AND (NOT :open_only OR a.id = ANY(CAST(:ids AS int[])))
    ORDER BY ST_Transform(a.geom, 5179) <-> p.g
    LIMIT :limit
"""


# aed_id로 고른 AED 하나. 직선거리 계산은 CANDIDATES_SQL과 같다
TARGET_SQL = """
    WITH p AS (
        SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 5179) AS g
    )
    SELECT a.id, a.place, a.address, a.gu,
           ST_Y(a.geom) AS lat, ST_X(a.geom) AS lon,
           ST_Distance(ST_Transform(a.geom, 5179), p.g) AS straight_m
    FROM aed a, p
    WHERE a.id = :aed_id
"""


def _aed(row: dict, walk: dict | None, statuses: dict[int, str]) -> dict:
    """응답용 AED 정보. walk가 None이면 걸어서 갈 수 없는 후보다."""
    return {
        "id": row["id"],
        "place": row["place"],
        "address": row["address"],
        "gu": row["gu"],
        "lat": row["lat"],
        "lon": row["lon"],
        "straight_distance_m": round(float(row["straight_m"]), 1),
        "walk_time_s": None if walk is None else round(walk["time_s"]),
        "walk_distance_m": None if walk is None else round(walk["distance_m"], 1),
        "open_status": statuses.get(row["id"], "unknown"),
    }


@router.get("/nearest")
def nearest(
    lat: float = Query(..., ge=-90, le=90, description="클릭한 지점 위도"),
    lon: float = Query(..., ge=-180, le=180, description="클릭한 지점 경도"),
    open_now: bool = Query(False, description="true면 지금(서울 시각) 운영 중인 AED만 후보로"),
    aed_id: int | None = Query(
        None, gt=0, description="지정하면 후보 검색 없이 이 AED까지의 경로 (팝업의 '여기까지 경로 보기')"
    ),
):
    """클릭한 지점에서 걸어서 가장 빨리 닿는 AED, 경로, 다음 후보 2개를 돌려준다.

    aed_id를 주면 그 AED 하나까지의 경로만 돌려준다 (alternatives는 빈 목록).
    """

    # 1. 서울 안인지
    if not query(INSIDE_SEOUL_SQL, lat=lat, lon=lon)[0]["inside"]:
        raise HTTPException(
            status_code=400,
            detail="서울시 밖의 지점입니다. 서울시 안을 눌러 주세요.",
        )

    # 2. 직선 1km 안 후보 (직선거리 순). aed_id가 있으면 그 AED 하나
    statuses = statuses_at(now_seoul())
    if aed_id is not None:
        candidates = query(TARGET_SQL, lat=lat, lon=lon, aed_id=aed_id)
        if not candidates:
            raise HTTPException(status_code=404, detail="해당 AED를 찾을 수 없습니다.")
    else:
        ids = [i for i, st in statuses.items() if st in OPEN_STATUSES] if open_now else []
        candidates = query(
            CANDIDATES_SQL, lat=lat, lon=lon, radius=SEARCH_RADIUS_M, limit=CANDIDATE_LIMIT,
            open_only=open_now, ids=ids,
        )
    if not candidates:
        which = "지금 운영 중인" if open_now else "등록된"
        raise HTTPException(
            status_code=404,
            detail=f"반경 {SEARCH_RADIUS_M / 1000:g}km 안에 {which} AED가 없습니다.",
        )

    # 3. 실제 도보 시간
    origin = (lat, lon)
    try:
        walks = walk_matrix(origin, [(c["lat"], c["lon"]) for c in candidates])
    except NoRouteError:
        raise HTTPException(status_code=404, detail=NO_ROUTE_DETAIL)
    except ValhallaUnavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL)

    reachable = [(c, w) for c, w in zip(candidates, walks) if w is not None]
    if not reachable:
        raise HTTPException(status_code=404, detail=NO_ROUTE_DETAIL)

    # 도보 시간이 같으면 직선거리가 가까운 쪽을 앞에 둔다
    reachable.sort(key=lambda cw: (cw[1]["time_s"], cw[0]["straight_m"]))
    first_row, first_walk = reachable[0]

    # 4. 1등까지만 경로
    try:
        coords = walk_route(origin, (first_row["lat"], first_row["lon"]))
    except NoRouteError:
        raise HTTPException(status_code=404, detail=NO_ROUTE_DETAIL)
    except ValhallaUnavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL)

    # 5. 응답. candidates[0]이 직선거리 1등이다
    straight_first = candidates[0]
    return {
        "nearest": {
            **_aed(first_row, first_walk, statuses),
            "route": {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"aed_id": first_row["id"]},
            },
        },
        "alternatives": [_aed(c, w, statuses) for c, w in reachable[1 : 1 + ALTERNATIVE_COUNT]],
        "straight_first_differs": straight_first["id"] != first_row["id"],
        # 화면의 "직선거리 1위는 실제로는 도보 N분" 안내용. 걸어서 못 가면 walk_*가 null
        "straight_first": _aed(straight_first, walks[0], statuses),
    }
