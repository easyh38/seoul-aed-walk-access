"""파이프라인 스크립트 공통 코드: 프로젝트 경로, DB 연결, 설정 읽기.

스크립트는 `python pipeline/<스크립트>.py`로 실행하므로 `from common import ...`로 가져온다.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# 환경변수가 이미 있으면 그 값을 쓰고, 없으면 .env에서 읽는다 (api/db.py와 같은 방식).
# 6단계 Docker Compose에서는 .env 파일 대신 환경변수로 값이 들어온다.
load_dotenv(PROJECT_ROOT / ".env")


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL이 없습니다. .env.example을 복사해 .env를 만들고 값을 채우세요."
        )
    return url


def get_engine() -> Engine:
    """DB 엔진. pool_pre_ping: 오래 쉬던 연결이 끊겼으면 쓰기 전에 다시 연결한다."""
    return create_engine(get_database_url(), pool_pre_ping=True)


def load_config() -> dict:
    """config.yaml 전체를 dict로 읽는다. 항목별 검증은 각 스크립트가 한다."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {CONFIG_PATH}")
    with CONFIG_PATH.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError("config.yaml 형식이 올바르지 않습니다.")
    return config


# mode별 등시선 시간은 grid_point 칼럼(straight_2/4, walk_2/4)과 묶여 있어 고정이다
MODE_MINUTES = {"oneway": 4, "roundtrip": 2}


def load_analysis_config() -> dict:
    """config.yaml의 접근성 분석 조건(grid_m, modes)을 읽고 검증한다.

    반환: {"grid_m": 100, "modes": {"oneway": {"minutes": 4, "radius_m": 340.0}, ...}}
    """
    config = load_config()

    grid_m = config.get("grid_m")
    if not isinstance(grid_m, int) or grid_m <= 0:
        raise ValueError(f"config.yaml grid_m은 양의 정수여야 합니다: {grid_m!r}")

    modes = config.get("modes")
    if not isinstance(modes, dict) or set(modes) != set(MODE_MINUTES):
        raise ValueError(
            f"config.yaml modes에는 {sorted(MODE_MINUTES)}가 있어야 합니다: {modes!r}"
        )

    checked = {}
    for mode, minutes in MODE_MINUTES.items():
        spec = modes[mode] or {}
        if spec.get("minutes") != minutes:
            raise ValueError(
                f"config.yaml modes.{mode}.minutes는 {minutes}이어야 합니다 "
                f"(grid_point 칼럼과 연결됨): {spec.get('minutes')!r}"
            )
        radius = spec.get("radius_m")
        if not isinstance(radius, (int, float)) or radius <= 0:
            raise ValueError(
                f"config.yaml modes.{mode}.radius_m은 양수여야 합니다: {radius!r}"
            )
        checked[mode] = {"minutes": minutes, "radius_m": float(radius)}

    return {"grid_m": grid_m, "modes": checked}
