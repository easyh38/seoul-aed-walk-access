"""GET /api/meta — 데이터 기준일과 분석 설정값 (PRD 7-4)."""

from fastapi import APIRouter, HTTPException

from api.db import query

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
def meta():
    """가장 최근 파이프라인 실행 기록을 돌려준다.

    화면 하단의 "AED 데이터: 공공데이터포털 (기준일)" 표시에 쓴다.
    """
    rows = query(
        """
        SELECT id, run_at, config, osm_date, aed_date
        FROM pipeline_run
        ORDER BY run_at DESC, id DESC
        LIMIT 1
        """
    )
    if not rows:
        # 파이프라인을 한 번도 안 돌린 DB: 500 대신 이유를 알려 준다
        raise HTTPException(
            status_code=404,
            detail="아직 파이프라인 실행 기록이 없습니다. run_all.py를 먼저 실행하세요.",
        )

    run = rows[0]
    return {
        "run_id": run["id"],
        "run_at": run["run_at"],
        "osm_date": run["osm_date"],
        "aed_date": run["aed_date"],
        "config": run["config"],
    }
