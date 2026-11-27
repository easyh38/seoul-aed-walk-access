"""02_refine_location: 동 번호·주소 해석, 동 재배치, 위치 의심 표시, 04 등시선 재생성 판정."""

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = ROOT / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PIPELINE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


refine = _load("pipeline_02_refine_location_test", "02_refine_location.py")
isochrone = _load("pipeline_04_isochrone_test", "04_isochrone.py")


@pytest.mark.parametrize("text, expected", [
    ("101동앞", "101"),
    ("108동 2호 경비실내", "108"),
    ("제2관리소(111동)", "111"),
    ("212동 211동 사이 휘트니스센터", "212"),
    ("역삼1동 주민센터", None),     # 행정동 이름
    ("동네 놀이터", None),
    ("관리사무소", None),
    ("", None),
    (None, None),
])
def test_dong_number(text, expected):
    assert refine.dong_number(text) == expected


@pytest.mark.parametrize("address, expected", [
    ("서울특별시 강남구 테헤란로 231", ("강남구", "테헤란로", 231)),
    ("서울특별시 서초구 신반포로23길 41 (잠원동, 신반포2지구아파트)", ("서초구", "신반포로23길", 41)),
    ("서울특별시 마포구 마포대로 지하92, 공덕역 (도화동)", ("마포구", "마포대로", 92)),
    ("서울특별시 성동구 왕십리로 지하 77", ("성동구", "왕십리로", 77)),
    ("서울특별시 도봉구 도봉산길 86-3", ("도봉구", "도봉산길", 86)),
    ("경기도 가평군 운악리", None),
])
def test_road_key(address, expected):
    assert refine.road_key(address) == expected


def _row(lon, lat, address="서울특별시 중구 세종대로 110", place=""):
    return {
        "buildAddress": address,
        "buildPlace": place,
        "_point": Point(lon, lat),
        "_flags": [],
        "location_source": "api",
    }


def test_mixed_address_flags_different_roads_only():
    rows = [
        _row(127.0, 37.5, "서울특별시 용산구 이태원로 22"),
        _row(127.0, 37.5, "서울특별시 동작구 현충로 210"),
        # 같은 도로의 붙은 번지는 한 건물로 본다
        _row(127.1, 37.5, "서울특별시 구로구 금오로 865"),
        _row(127.1, 37.5, "서울특별시 구로구 금오로 867"),
        # 혼자 있는 점은 비교 대상이 없다
        _row(127.2, 37.5, "서울특별시 중구 세종대로 110"),
    ]
    stats = Counter()
    refine.flag_mixed_address(rows, stats)

    assert [r["_flags"] for r in rows] == [
        ["mixed_address"], ["mixed_address"], [], [], [],
    ]
    assert stats["mixed_address_points"] == 1


def _fake_osm(monkeypatch, buildings, areas):
    def fake():
        return (
            gpd.GeoDataFrame({"name": [n for n, _ in buildings]},
                             geometry=[g for _, g in buildings], crs=4326),
            gpd.GeoDataFrame({"name": ["단지"] * len(areas)}, geometry=areas, crs=4326),
        )
    monkeypatch.setattr(refine, "load_osm", fake)


# 약 400m × 400m 단지. 원래 점은 단지 가운데
COMPLEX = box(127.000, 37.500, 127.0045, 37.5036)
CENTER = (127.00225, 37.5018)


def test_relocates_to_unique_dong_in_same_complex(monkeypatch):
    target = box(127.0040, 37.5030, 127.0042, 37.5032)
    _fake_osm(monkeypatch, [
        ("101동", target),
        ("102동", box(127.0005, 37.5005, 127.0007, 37.5007)),
        # 이웃 단지의 101동은 후보가 아니다
        ("101동", box(127.0100, 37.5100, 127.0102, 37.5102)),
    ], [COMPLEX])

    row = _row(*CENTER, place="101동앞")
    stats = Counter()
    refine.relocate_dongs([row], stats)

    assert row["location_source"] == "osm_building"
    assert target.contains(row["_point"])
    assert stats["dong_relocated"] == 1


def test_does_not_relocate_when_dong_is_ambiguous(monkeypatch):
    _fake_osm(monkeypatch, [
        ("101동", box(127.0040, 37.5030, 127.0042, 37.5032)),
        ("101동", box(127.0005, 37.5005, 127.0007, 37.5007)),
    ], [COMPLEX])

    row = _row(*CENTER, place="101동앞")
    stats = Counter()
    refine.relocate_dongs([row], stats)

    assert row["location_source"] == "api"
    assert row["_point"] == Point(*CENTER)
    assert stats["dong_ambiguous"] == 1


def test_does_not_relocate_outside_any_complex_or_suspicious_point(monkeypatch):
    _fake_osm(monkeypatch, [("101동", box(127.0040, 37.5030, 127.0042, 37.5032))], [COMPLEX])

    outside = _row(127.05, 37.55, place="101동앞")
    suspicious = _row(*CENTER, place="101동앞")
    suspicious["_flags"].append("gu_mismatch")
    stats = Counter()
    refine.relocate_dongs([outside, suspicious], stats)

    assert outside["location_source"] == "api"
    assert suspicious["location_source"] == "api"
    assert stats["dong_no_area"] == 1


# ---------------------------------------------------------------- 04: 좌표가 바뀐 AED 재생성

def _success(serial, lat, lon):
    return json.dumps({"status": "success", "aed": {"serialSeq": serial, "lat": lat, "lon": lon}}) + "\n"


def test_drop_moved_results_removes_only_moved_aeds(tmp_path):
    path = tmp_path / "iso.jsonl"
    path.write_text(
        _success("A", 37.5, 127.0)
        + _success("B", 37.5, 127.0)
        + _success("GONE", 37.5, 127.0)  # CSV에 없는 AED는 건드리지 않는다
        + "{broken\n",
        encoding="utf-8",
    )
    rows = [
        {"serialSeq": "A", "wgs84Lat": "37.5", "wgs84Lon": "127.0"},
        {"serialSeq": "B", "wgs84Lat": "37.5012", "wgs84Lon": "127.0"},  # 재배치됨
    ]

    assert isochrone.drop_moved_results(path, rows) == 1
    assert isochrone.load_processed_ids(path) == {"A", "GONE"}
    assert "{broken" in path.read_text(encoding="utf-8")
    # 다시 돌려도 더 지울 것이 없다
    assert isochrone.drop_moved_results(path, rows) == 0


def test_drop_moved_results_without_file(tmp_path):
    assert isochrone.drop_moved_results(tmp_path / "none.jsonl", []) == 0
