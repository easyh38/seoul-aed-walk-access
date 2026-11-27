"""AED 좌표를 보정하고, 위치가 의심스러운 AED에 표시를 단다.

    python pipeline/02_refine_location.py           # 미리보기 (파일 변경 없음)
    python pipeline/02_refine_location.py --apply   # data/clean/aed.csv 갱신 + 검토 목록 저장

API 좌표는 도로명주소를 건물(단지) 대표점 하나로 바꾼 값이라
같은 단지·건물의 AED가 한 점에 겹친다. 이 단계는 두 가지를 한다.

1. 동 단위 재배치: 설치 장소가 "101동 앞"처럼 동 번호를 가진 아파트 AED를,
   원래 점이 속한 OSM 단지 경계(landuse=residential 등) 안의
   같은 이름("101동") OSM 건물로 옮긴다. 단지 안에 그 동이 딱 하나일 때만 옮긴다.
2. 위치 의심 표시 (location_flags):
   - gu_mismatch: 주소의 자치구와 좌표가 놓인 자치구가 다르다 (경계 근처는 제외)
   - mixed_address: 같은 점에 도로명·번지가 다른 AED가 함께 찍혀 있다

원본 API 좌표는 api_wgs84Lat/api_wgs84Lon에 남긴다.
다시 실행하면 원본 좌표에서 다시 계산하므로 몇 번을 돌려도 결과가 같다.
02_clean을 다시 실행하면 이 칼럼들이 사라지므로 이 단계도 다시 실행한다.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.geometry import Point
from shapely.strtree import STRtree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data/clean/aed.csv"
PBF_PATH = PROJECT_ROOT / "valhalla/custom_files/seoul.osm.pbf"
SHAPE_PATH = (
    PROJECT_ROOT
    / "data/raw/boundaries/original/bnd_sigungu_11_2025_2Q.shp"
)
REVIEW_PATH = PROJECT_ROOT / "results/location_review.csv"

# 원래 점에서 이 거리 안에 있는 단지 경계만 같은 단지로 본다
AREA_SEARCH_M = 30
# 재배치 거리 상한. 대단지도 이 안에 들어온다 (실측 p90 ≈ 180m, 최대 ≈ 480m)
MAX_MOVE_M = 600
# 구 경계에서 이 거리 안이면 경계 오차로 보고 gu_mismatch를 달지 않는다
GU_TOLERANCE_M = 100
# 같은 도로의 번지 차이가 이 이하면 붙어 있는 건물로 보고 mixed_address를 달지 않는다
SAME_ROAD_NUMBER_GAP = 20

# 단지 경계로 쓸 OSM 다각형 (아파트 단지, 학교·병원 캠퍼스)
AREA_WHERE = (
    "landuse = 'residential' "
    "OR amenity IN ('school', 'university', 'college', 'hospital')"
)

# "101동앞", "제2관리소(111동)" → 101 / 111.
# "역삼1동 주민센터"(행정동)처럼 앞에 한글이 붙으면 제외, "3동쪽"처럼 방향을 뜻하면 제외
DONG_RE = re.compile(r"(?<![가-힣\d])(\d{1,4})\s*동(?!쪽|측|편|네)")
# "서울특별시 강남구 테헤란로 231", "… 왕십리로 지하 77" → (구, 도로명, 본번)
ROAD_RE = re.compile(
    r"^서울특별시\s+(\S+구)\s+(\S+(?:로|길))\s*(?:지하\s*)?(\d+)"
)

API_LAT = "api_wgs84Lat"
API_LON = "api_wgs84Lon"
ADDED_FIELDS = [API_LAT, API_LON, "location_source", "location_flags"]

# 서울 위도(37.5°)에서 1도당 거리
M_PER_DEG_LAT = 111_000
M_PER_DEG_LON = 88_200


def meters(a: Point, b: Point) -> float:
    return math.hypot(
        (a.x - b.x) * M_PER_DEG_LON,
        (a.y - b.y) * M_PER_DEG_LAT,
    )


def deg(m: float) -> float:
    """거리(m)를 넉넉하게 위경도 차이로 바꾼다 (검색 범위용)."""
    return m / M_PER_DEG_LON


def dong_number(text: str | None) -> str | None:
    match = DONG_RE.search(text or "")
    return match.group(1) if match else None


def road_key(address: str) -> tuple[str, str, int] | None:
    match = ROAD_RE.match(address.replace(",", " "))
    if not match:
        return None
    gu, road, number = match.groups()
    return gu, road, int(number)


def read_rows() -> tuple[list[dict], list[str]]:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"정제 CSV가 없습니다: {CSV_PATH}")

    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    for row in rows:
        # 이미 보정한 CSV면 원본 좌표에서 다시 시작한다
        if row.get(API_LAT) and row.get(API_LON):
            row["wgs84Lat"] = row[API_LAT]
            row["wgs84Lon"] = row[API_LON]
        row["_point"] = Point(float(row["wgs84Lon"]), float(row["wgs84Lat"]))

    base_fields = [f for f in fieldnames if f not in ADDED_FIELDS]
    return rows, base_fields + ADDED_FIELDS


# ---------------------------------------------------------------- 1. 동 단위 재배치

def load_osm():
    if not PBF_PATH.exists():
        raise FileNotFoundError(
            f"OSM 파일이 없습니다: {PBF_PATH} (Valhalla 준비 단계에서 받는다)"
        )

    buildings = pyogrio.read_dataframe(
        PBF_PATH,
        layer="multipolygons",
        columns=["name"],
        where="building IS NOT NULL AND name IS NOT NULL",
    )
    areas = pyogrio.read_dataframe(
        PBF_PATH,
        layer="multipolygons",
        columns=["name"],
        where=AREA_WHERE,
    )
    return buildings, areas


def relocate_dongs(rows: list[dict], stats: Counter) -> None:
    buildings, areas = load_osm()
    print(f"OSM 이름 있는 건물: {len(buildings):,} / 단지 경계: {len(areas):,}")

    b_geoms = list(buildings.geometry)
    b_dongs = [dong_number(n) for n in buildings["name"]]
    a_geoms = list(areas.geometry)
    b_tree = STRtree(b_geoms)
    a_tree = STRtree(a_geoms)

    for row in rows:
        if "gu_mismatch" in row["_flags"]:
            continue  # 원래 점부터 의심스러우면 그 근처 단지도 믿을 수 없다

        dong = dong_number(row.get("buildPlace"))
        if not dong:
            continue
        stats["dong_candidates"] += 1

        p = row["_point"]
        near = [
            a_geoms[i] for i in a_tree.query(p.buffer(deg(AREA_SEARCH_M)))
            if a_geoms[i].distance(p) * M_PER_DEG_LON <= AREA_SEARCH_M
        ]
        if not near:
            stats["dong_no_area"] += 1
            continue

        # 점을 감싸는 단지가 여럿이면(구역 안의 단지) 가장 작은 것
        area = min(near, key=lambda g: g.area)
        hits = [
            b_geoms[i] for i in b_tree.query(area)
            if b_dongs[i] == dong
            and area.contains(b_geoms[i].representative_point())
        ]
        if len(hits) != 1:
            stats["dong_ambiguous" if hits else "dong_no_building"] += 1
            continue

        target = hits[0].representative_point()
        moved = meters(p, target)
        if moved > MAX_MOVE_M:
            stats["dong_too_far"] += 1
            continue

        row["_point"] = target
        row["location_source"] = "osm_building"
        row["_moved_m"] = round(moved)
        stats["dong_relocated"] += 1


# ---------------------------------------------------------------- 2. 위치 의심 표시

def flag_gu_mismatch(rows: list[dict], stats: Counter) -> None:
    gdf = gpd.read_file(SHAPE_PATH).to_crs(4326)
    districts = dict(zip(gdf["SIGUNGU_NM"].str.strip(), gdf.geometry))

    for row in rows:
        address = row.get("buildAddress") or ""
        named = [gu for gu in districts if gu in address]
        if len(named) != 1:
            continue  # 03_load_aed와 같은 기준: 주소로 구를 확정할 수 없으면 판단하지 않는다

        geom = districts[named[0]]
        p = row["_point"]
        if geom.covers(p):
            continue
        if geom.distance(p) * M_PER_DEG_LON <= GU_TOLERANCE_M:
            continue

        row["_flags"].append("gu_mismatch")
        actual = [gu for gu, g in districts.items() if g.covers(p)]
        row["_note"] = f"주소는 {named[0]}, 좌표는 {actual[0] if actual else '서울 밖'}"
        stats["gu_mismatch"] += 1


def flag_mixed_address(rows: list[dict], stats: Counter) -> None:
    by_point = defaultdict(list)
    for row in rows:
        by_point[(row["_point"].x, row["_point"].y)].append(row)

    for group in by_point.values():
        if len(group) < 2:
            continue

        keys = {road_key(r.get("buildAddress") or "") for r in group}
        keys.discard(None)
        roads = {(gu, road) for gu, road, _ in keys}

        if len(roads) == 1:
            numbers = [n for _, _, n in keys]
            if max(numbers) - min(numbers) <= SAME_ROAD_NUMBER_GAP:
                continue
        elif len(roads) == 0:
            continue

        stats["mixed_address_points"] += 1
        for row in group:
            row["_flags"].append("mixed_address")
            row["_note"] = row.get("_note") or (
                "같은 점에 주소가 다른 AED " + str(len(group)) + "대"
            )
            stats["mixed_address"] += 1


# ---------------------------------------------------------------- 저장

def write_csv(rows: list[dict], fieldnames: list[str]) -> None:
    tmp = CSV_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(CSV_PATH)


def write_review(rows: list[dict]) -> int:
    review = [r for r in rows if r["location_flags"]]
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    columns = ["serialSeq", "org", "buildAddress", "buildPlace",
               "wgs84Lat", "wgs84Lon", "location_flags", "note"]
    with REVIEW_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in sorted(review, key=lambda r: (r["location_flags"], r["wgs84Lat"], r["wgs84Lon"])):
            writer.writerow({**r, "note": r.get("_note", "")})
    return len(review)


def main() -> None:
    parser = argparse.ArgumentParser(description="AED 좌표 보정 및 위치 의심 표시")
    parser.add_argument("--apply", action="store_true",
                        help="data/clean/aed.csv를 갱신하고 검토 목록을 저장합니다.")
    args = parser.parse_args()

    rows, fieldnames = read_rows()
    for row in rows:
        row[API_LAT] = row["wgs84Lat"]
        row[API_LON] = row["wgs84Lon"]
        row["location_source"] = "api"
        row["_flags"] = []

    stats = Counter()
    # 구 판정은 원본 좌표로 먼저 한다 (재배치 대상에서 의심 좌표를 빼기 위해)
    flag_gu_mismatch(rows, stats)
    relocate_dongs(rows, stats)
    # 겹침 판정은 재배치 뒤 좌표로 한다 (동별로 흩어진 AED는 더 이상 겹치지 않는다)
    flag_mixed_address(rows, stats)

    for row in rows:
        if row["location_source"] != "api":
            row["wgs84Lat"] = f"{row['_point'].y:.10f}"
            row["wgs84Lon"] = f"{row['_point'].x:.10f}"
        row["location_flags"] = ";".join(dict.fromkeys(row["_flags"]))

    shared_before = Counter((r[API_LAT], r[API_LON]) for r in rows)
    shared_after = Counter((r["wgs84Lat"], r["wgs84Lon"]) for r in rows)
    moved = sorted(r["_moved_m"] for r in rows if "_moved_m" in r)

    print("=" * 50)
    print("AED 위치 보정 결과")
    print("=" * 50)
    print(f"AED: {len(rows):,}건")
    print(f"동 번호가 있는 AED: {stats['dong_candidates']:,}건")
    print(f"  OSM 동 건물로 재배치: {stats['dong_relocated']:,}건")
    if moved:
        print(f"    이동 거리 중앙값 {moved[len(moved) // 2]}m, 최대 {moved[-1]}m")
    print(f"  단지 경계 없음: {stats['dong_no_area']:,}건")
    print(f"  단지 안에 그 동 건물 없음(OSM 미기재): {stats['dong_no_building']:,}건")
    print(f"  같은 동 이름 건물이 여럿: {stats['dong_ambiguous']:,}건")
    print(f"  이동 거리 {MAX_MOVE_M}m 초과로 보류: {stats['dong_too_far']:,}건")
    print(f"다른 AED와 좌표가 겹치는 AED: "
          f"{sum(n for n in shared_before.values() if n > 1):,}건 → "
          f"{sum(n for n in shared_after.values() if n > 1):,}건")
    print(f"gu_mismatch: {stats['gu_mismatch']:,}건")
    print(f"mixed_address: {stats['mixed_address']:,}건 ({stats['mixed_address_points']:,}개 지점)")

    if not args.apply:
        print("\n미리보기 완료. 파일은 변경하지 않았습니다. 적용하려면 --apply")
        return

    write_csv(rows, fieldnames)
    count = write_review(rows)
    print(f"\n저장: {CSV_PATH}")
    print(f"검토 목록: {REVIEW_PATH} ({count:,}건)")


if __name__ == "__main__":
    main()
