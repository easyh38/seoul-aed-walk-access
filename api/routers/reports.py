"""POST /api/reports — AED 오류 제보 (PRD S-07, guideline 4-5).

입력은 Pydantic 모델(ReportIn)로 검사한다. 유형이 틀리거나 설명이 500자를 넘으면
FastAPI가 자동으로 422를 돌려준다. DB의 CHECK 제약(report_type_check,
report_description_check)과 이중으로 막는 셈이다.

개인정보는 저장하지 않는다: report 테이블에는 IP·위치·연락처 칼럼이 없고,
모델에 없는 필드(예: lat, lon, email)를 보내면 422로 거부한다.
"""

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError

from api.db import execute

router = APIRouter(prefix="/api", tags=["reports"])

DESCRIPTION_MAX = 500  # DB report_description_check와 같은 값

ReportType = Literal["wrong_location", "missing", "unusable"]


class ReportIn(BaseModel):
    """제보 입력. 정의하지 않은 필드는 받지 않는다 (개인정보가 섞여 들어오지 않게)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"aed_id": 1, "type": "missing", "description": "안내판만 있고 AED가 없습니다"}]
        },
    )

    aed_id: int = Field(gt=0, description="제보할 AED id (/api/aeds의 id)")
    type: ReportType = Field(
        description="wrong_location: 위치가 다름 / missing: AED가 없음 / unusable: 사용할 수 없음"
    )
    description: Optional[str] = Field(
        default=None, max_length=DESCRIPTION_MAX, description="추가 설명 (500자 이하, 선택)"
    )

    @field_validator("description")
    @classmethod
    def blank_to_none(cls, value: Optional[str]) -> Optional[str]:
        """앞뒤 공백을 지우고, 비어 있으면 저장하지 않는다."""
        if value is None:
            return None
        value = value.strip()
        return value or None


class ReportOut(BaseModel):
    id: int
    aed_id: int
    type: ReportType
    status: str
    created_at: datetime


# 있는 AED에만 넣는다. 없는 aed_id면 0행이 들어가고 RETURNING도 비어 있다 → 404.
# 확인과 저장을 한 문장으로 해서, 확인한 직후 AED가 지워지는 경우도 FK가 막는다.
INSERT_SQL = """
    INSERT INTO report (aed_id, type, description)
    SELECT :aed_id, :type, :description
    WHERE EXISTS (SELECT 1 FROM aed WHERE id = :aed_id)
    RETURNING id, aed_id, type, status, created_at
"""

FOREIGN_KEY_VIOLATION = "23503"  # PostgreSQL 오류 코드

NOT_FOUND_DETAIL = "해당 AED를 찾을 수 없습니다. 지도에서 AED를 다시 선택해 주세요."


@router.post("/reports", status_code=201, response_model=ReportOut)
def create_report(report: ReportIn):
    """AED 오류 제보를 저장하고 접수 번호(id)를 돌려준다."""
    try:
        rows = execute(
            INSERT_SQL,
            aed_id=report.aed_id,
            type=report.type,
            description=report.description,
        )
    except IntegrityError as exc:
        # 저장 직전에 AED가 삭제된 경우(FK 위반)만 404로 안내한다.
        # CHECK 위반 등 다른 무결성 오류는 원인이 가려지지 않게 그대로 올린다
        if getattr(exc.orig, "pgcode", None) != FOREIGN_KEY_VIOLATION:
            raise
        rows = []

    if not rows:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)

    return rows[0]
