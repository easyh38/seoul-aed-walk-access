"""GET /api/stats — 서울 전체·자치구별 지표 (PRD S-05, 7-4)."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from api.db import query
from api.open_now import coverage_at, now_seoul

router = APIRouter(prefix="/api", tags=["stats"])

METRIC_FIELDS = ("straight_pct", "walk_pct", "diff_pp", "deadzone_km2", "aed_count")


def _num(value):
    """NUMERIC(Decimal)을 JSON 숫자로. 값이 없으면 None."""
    return None if value is None else float(value)


def _metrics(row):
    out = {k: _num(row[k]) for k in METRIC_FIELDS}
    out["aed_count"] = None if row["aed_count"] is None else int(row["aed_count"])
    return out


@router.get("/stats")
def stats(
    mode: Literal["oneway", "roundtrip"] = Query(
        "oneway", description="oneway: 편도 4분 / roundtrip: 왕복 기준(편도 2분)"
    ),
    open_now: bool = Query(
        False, description="true면 지금 운영 중인 AED 기준 서울 전체 walk_pct를 open_now에 추가"
    ),
):
    """가장 최근 실행의 서울 전체 요약과 25개 자치구 순위를 돌려준다.

    최근 실행은 pipeline_run이 아니라 metrics에서 고른다.
    지표를 만들지 않는 스크립트(08 등)도 pipeline_run에 기록을 남기기 때문이다.
    open_now=true면 지금(30분 구간) 운영 중인 AED만으로 계산한 서울 전체 walk_pct를
    "open_now"에 함께 준다. 자치구별 값은 coverage_cache에 없어서 전체 AED 기준만 있다.
    """
    rows = query(
        """
        WITH latest AS (
            SELECT MAX(run_id) AS run_id FROM metrics WHERE mode = :mode
        )
        SELECT m.run_id, r.run_at, m.scope,
               m.straight_pct, m.walk_pct, m.diff_pp, m.deadzone_km2, m.aed_count
        FROM metrics m
        JOIN latest l ON m.run_id = l.run_id
        LEFT JOIN pipeline_run r ON r.id = m.run_id
        WHERE m.mode = :mode
        """,
        mode=mode,
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"아직 {mode} 지표가 없습니다. 파이프라인(지표 계산)을 먼저 실행하세요.",
        )

    seoul = next((r for r in rows if r["scope"] == "서울"), None)

    # 순위: 실보행 커버리지가 높은 구가 1위. 같으면 구 이름순
    districts = sorted(
        (r for r in rows if r["scope"] != "서울"),
        key=lambda r: (-(r["walk_pct"] or 0), r["scope"]),
    )

    result = {
        "run_id": rows[0]["run_id"],
        "run_at": rows[0]["run_at"],
        "mode": mode,
        "seoul": _metrics(seoul) if seoul else None,
        "districts": [
            {"rank": i, "gu": r["scope"], **_metrics(r)}
            for i, r in enumerate(districts, start=1)
        ],
    }

    if open_now:
        cov = coverage_at(mode, now_seoul())
        result["open_now"] = {
            "at": cov.slot_start,  # 이 30분 구간의 시작 시각 기준으로 계산했다
            "is_holiday": cov.is_holiday,
            "open_aed_count": cov.open_aed_count,
            "walk_pct": cov.walk_pct,
        }

    return result
