"""config.yaml 읽기 (파이프라인과 같은 설정 파일을 API도 쓴다).

보행 속도처럼 등시선 분석과 반드시 같아야 하는 값을 한 곳에서 가져온다.
Docker 이미지에는 api/Dockerfile이 config.yaml을 /app/config.yaml로 복사해 둔다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

DEFAULT_VALHALLA_URL = "http://localhost:8002"


@lru_cache(maxsize=1)
def load_config() -> dict:
    """config.yaml 전체를 dict로 읽는다. 서버가 켜져 있는 동안 한 번만 읽는다."""
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"설정 파일이 없습니다: {CONFIG_PATH}")
    with CONFIG_PATH.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise RuntimeError("config.yaml 형식이 올바르지 않습니다.")
    return config


def valhalla_base_url() -> str:
    """Valhalla 주소. Docker에서는 환경변수(VALHALLA_BASE_URL)가 우선한다."""
    url = os.getenv("VALHALLA_BASE_URL") or load_config().get("valhalla", {}).get(
        "base_url", DEFAULT_VALHALLA_URL
    )
    return url.rstrip("/")


def walking_speed_kmh() -> float:
    """등시선(04_isochrone.py)과 같은 보행 속도. 다르면 지도 색과 경로 시간이 어긋난다."""
    speed = load_config().get("valhalla", {}).get("walking_speed_kmh")
    if not isinstance(speed, (int, float)) or speed <= 0:
        raise RuntimeError(f"config.yaml valhalla.walking_speed_kmh가 올바르지 않습니다: {speed!r}")
    return float(speed)
