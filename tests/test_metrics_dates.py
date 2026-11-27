"""07_build_metrics: 데이터 기준일을 입력 파일에서 읽는다."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = ROOT / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost:5432/test")
spec = importlib.util.spec_from_file_location("pipeline_07_build_metrics_test", PIPELINE_DIR / "07_build_metrics.py")
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def test_aed_date_from_cleaning_report(tmp_path):
    report = tmp_path / "cleaning_report.csv"
    report.write_text(
        "﻿항목,건수 또는 값,설명\nsource_file,aed_seoul_20261125_140246.json,사용한 원본 파일\n",
        encoding="utf-8",
    )
    assert metrics.detect_aed_date(report) == "2026-11-25"


def test_aed_date_requires_source_file(tmp_path):
    report = tmp_path / "cleaning_report.csv"
    report.write_text("항목,건수 또는 값,설명\n", encoding="utf-8")
    with pytest.raises(ValueError):
        metrics.detect_aed_date(report)


def test_osm_date_falls_back_to_file_mtime(tmp_path, monkeypatch):
    pbf = tmp_path / "seoul.osm.pbf"
    pbf.write_bytes(b"")
    os.utime(pbf, (1_795_000_000, 1_795_000_000))  # 2026-11-18 (UTC)
    monkeypatch.setattr(metrics.shutil, "which", lambda _: None)
    assert metrics.detect_osm_date(pbf) == "2026-11-18"
