
import csv
import json
import math
import re

from pathlib import Path
from collections import Counter
from datetime import datetime


# ==========================================
# 1. 프로젝트 기본 경로
# ==========================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = PROJECT_ROOT / "data" / "raw"
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"
RESULTS_DIR = PROJECT_ROOT / "results"

CLEAN_FILE = CLEAN_DIR / "aed.csv"
REPORT_FILE = RESULTS_DIR / "cleaning_report.csv"


# ==========================================
# 2. AED 운영시간 필드
# ==========================================

TIME_FIELDS = [
    "monSttTme", "monEndTme",
    "tueSttTme", "tueEndTme",
    "wedSttTme", "wedEndTme",
    "thuSttTme", "thuEndTme",
    "friSttTme", "friEndTme",
    "satSttTme", "satEndTme",
    "sunSttTme", "sunEndTme",
    "holSttTme", "holEndTme"
]


# ==========================================
# 3. 공통 함수
# ==========================================

def get_text(record, field):
    """데이터의 값을 문자열로 가져오고 공백을 제거합니다."""

    value = record.get(field)

    if value is None:
        return ""

    return str(value).strip()


def get_latest_raw_file():
    """가장 최근에 수집한 AED JSON 파일을 찾습니다."""

    files = list(RAW_DIR.glob("aed_seoul_*.json"))

    if not files:
        raise FileNotFoundError(
            "data/raw 폴더에서 AED 원본 JSON을 찾을 수 없습니다."
        )

    return max(files, key=lambda path: path.stat().st_mtime)


# ==========================================
# 4. 좌표 검사
# ==========================================

def check_coordinates(record):
    """
    위도와 경도를 숫자로 변환하고
    WGS84의 유효 범위인지 확인합니다.
    """

    lat_text = get_text(record, "wgs84Lat")
    lon_text = get_text(record, "wgs84Lon")

    if not lat_text or not lon_text:
        return None, None, "missing_coordinate"

    try:
        lat = float(lat_text)
        lon = float(lon_text)

    except (ValueError, TypeError):
        return None, None, "invalid_coordinate"

    if not math.isfinite(lat) or not math.isfinite(lon):
        return None, None, "invalid_coordinate"

    if not (-90 <= lat <= 90):
        return None, None, "invalid_coordinate"

    if not (-180 <= lon <= 180):
        return None, None, "invalid_coordinate"

    return lat, lon, None


# ==========================================
# 5. 운영시간 검사
# ==========================================

def check_operating_hours(record):
    """
    운영시간 원본을 변경하지 않고
    특이한 시간 형식만 확인합니다.

    2400 초과 값은 임의로 변환하거나 삭제하지 않습니다.
    """

    has_missing_time = False
    over_2400_count = 0
    malformed_count = 0

    for field in TIME_FIELDS:

        value = get_text(record, field)

        if not value:
            has_missing_time = True
            continue

        # 4자리 숫자 형식인지 확인
        if not re.fullmatch(r"\d{4}", value):
            malformed_count += 1
            continue

        # 24시 초과 값 확인
        if int(value) > 2400:
            over_2400_count += 1

    review_required = (
        over_2400_count > 0 or malformed_count > 0
    )

    return {
        "has_missing_time": has_missing_time,
        "over_2400_count": over_2400_count,
        "malformed_count": malformed_count,
        "review_required": review_required
    }


# ==========================================
# 6. AED 데이터 정제
# ==========================================

def clean_records(raw_records):

    cleaned_records = []

    seen_serials = set()

    exclusion_counts = Counter()

    excluded_count = 0

    missing_org_count = 0
    missing_time_count = 0
    blank_time_values = 0

    review_record_count = 0
    over_2400_count = 0
    malformed_time_count = 0

    for record in raw_records:

        reasons = []

        # ------------------------------
        # 6-1. 필수 정보 검사
        # ------------------------------

        serial = get_text(record, "serialSeq")
        address = get_text(record, "buildAddress")
        organization = get_text(record, "org")

        if not serial:
            reasons.append("missing_serial_seq")

        if not address:
            reasons.append("missing_address")

        elif not address.startswith("서울특별시"):
            reasons.append("outside_seoul_address")

        # ------------------------------
        # 6-2. 좌표 검사
        # ------------------------------

        lat, lon, coordinate_error = check_coordinates(record)

        if coordinate_error:
            reasons.append(coordinate_error)

        # ------------------------------
        # 6-3. 데이터 제외
        # ------------------------------

        if reasons:

            excluded_count += 1

            for reason in reasons:
                exclusion_counts[reason] += 1

            continue

        # ------------------------------
        # 6-4. 중복 검사
        # ------------------------------

        if serial in seen_serials:

            excluded_count += 1
            exclusion_counts["duplicate_serial_seq"] += 1

            continue

        seen_serials.add(serial)

        # ------------------------------
        # 6-5. 운영시간 검사
        # ------------------------------

        time_result = check_operating_hours(record)

        if time_result["has_missing_time"]:
            missing_time_count += 1

        for field in TIME_FIELDS:
            if not get_text(record, field):
                blank_time_values += 1

        if time_result["review_required"]:
            review_record_count += 1

        over_2400_count += time_result["over_2400_count"]
        malformed_time_count += time_result["malformed_count"]

        # ------------------------------
        # 6-6. 기관명 검사
        # ------------------------------

        quality_flags = []

        if not organization:
            missing_org_count += 1
            quality_flags.append("missing_organization")

        if time_result["over_2400_count"] > 0:
            quality_flags.append("time_over_2400")

        if time_result["malformed_count"] > 0:
            quality_flags.append("unrecognized_time_format")

        if time_result["has_missing_time"]:
            quality_flags.append("some_operating_hours_missing")

        # ------------------------------
        # 6-7. 정제 데이터 생성
        # ------------------------------

        clean_record = dict(record)

        # 좌표는 숫자형으로 변환
        clean_record["wgs84Lat"] = lat
        clean_record["wgs84Lon"] = lon

        # 운영시간 원본은 그대로 보존
        clean_record["operating_hours_review_required"] = (
            time_result["review_required"]
        )

        clean_record["quality_flags"] = (
            ";".join(quality_flags)
        )

        cleaned_records.append(clean_record)

    statistics = {
        "cleaned_count": len(cleaned_records),
        "excluded_count": excluded_count,
        "exclusion_counts": exclusion_counts,
        "missing_org_count": missing_org_count,
        "missing_time_count": missing_time_count,
        "blank_time_values": blank_time_values,
        "review_record_count": review_record_count,
        "over_2400_count": over_2400_count,
        "malformed_time_count": malformed_time_count
    }

    return cleaned_records, statistics


# ==========================================
# 7. 정제 데이터 CSV 저장
# ==========================================

def save_clean_csv(records):

    CLEAN_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    if not records:
        raise RuntimeError(
            "정제 후 남은 AED 데이터가 없습니다. "
            "원본 파일과 정제 기준을 확인하세요."
        )

    # 원본 API 필드명을 유지하면서
    # 품질 관리용 필드를 추가합니다.
    fieldnames = list(
        dict.fromkeys(
            key
            for record in records
            for key in record
        )
    )

    with open(
        CLEAN_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(records)

    print("정제 데이터 저장:", CLEAN_FILE)


# ==========================================
# 8. 정제 보고서 저장
# ==========================================

def save_report(
    source_file,
    raw_count,
    metadata_count,
    statistics
):

    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    excluded = statistics["exclusion_counts"]

    report_rows = [
        ("source_file", source_file.name,
         "사용한 원본 파일"),

        ("metadata_total_count", metadata_count,
         "원본 메타데이터 전체 건수"),

        ("raw_record_count", raw_count,
         "원본 JSON 실제 데이터 건수"),

        ("clean_record_count", statistics["cleaned_count"],
         "정제 후 저장된 AED 건수"),

        ("excluded_record_count", statistics["excluded_count"],
         "정제 과정에서 제외된 데이터 건수"),

        ("missing_serial_seq", excluded["missing_serial_seq"],
         "AED 일련번호 누락"),

        ("missing_address", excluded["missing_address"],
         "주소 누락"),

        ("missing_coordinate", excluded["missing_coordinate"],
         "위도 또는 경도 누락"),

        ("invalid_coordinate", excluded["invalid_coordinate"],
         "좌표 형식 또는 범위 오류"),

        ("outside_seoul_address",
         excluded["outside_seoul_address"],
         "서울 외 주소"),

        ("duplicate_serial_seq",
         excluded["duplicate_serial_seq"],
         "중복 AED 일련번호"),

        ("missing_organization",
         statistics["missing_org_count"],
         "기관명 누락, 데이터는 유지"),

        ("records_with_missing_operating_hours",
         statistics["missing_time_count"],
         "운영시간 필드 중 하나 이상 빈값"),

        ("blank_operating_time_values",
         statistics["blank_time_values"],
         "운영시간 빈값 전체 개수"),

        ("records_requiring_time_review",
         statistics["review_record_count"],
         "특이한 운영시간 형식이 포함된 AED"),

        ("time_values_over_2400",
         statistics["over_2400_count"],
         "2400을 초과하는 운영시간 값"),

        ("unrecognized_time_formats",
         statistics["malformed_time_count"],
         "4자리 숫자 형식이 아닌 운영시간 값"),

        ("generated_at",
         datetime.now().astimezone().isoformat(),
         "정제 보고서 생성 시각")
    ]

    with open(
        REPORT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            "항목",
            "건수 또는 값",
            "설명"
        ])

        writer.writerows(report_rows)

    print("정제 보고서 저장:", REPORT_FILE)


# ==========================================
# 9. 전체 정제 작업 실행
# ==========================================

def main():

    print("=" * 50)
    print("AED 데이터 정제 시작")
    print("=" * 50)

    # 가장 최근 원본 파일 선택
    source_file = get_latest_raw_file()

    print("원본 파일:", source_file.name)

    # JSON 읽기
    with open(
        source_file,
        "r",
        encoding="utf-8"
    ) as file:

        source_data = json.load(file)

    raw_records = source_data.get("items", [])

    metadata_count = source_data.get(
        "metadata", {}
    ).get("total_count")

    if not isinstance(raw_records, list):
        raise RuntimeError(
            "원본 JSON의 items 형식이 올바르지 않습니다."
        )

    print("원본 AED 데이터:", f"{len(raw_records):,}건")

    # 정제 실행
    cleaned_records, statistics = clean_records(raw_records)

    # 결과 저장
    save_clean_csv(cleaned_records)

    save_report(
        source_file,
        len(raw_records),
        metadata_count,
        statistics
    )

    # 최종 결과 출력
    print("\n" + "=" * 50)
    print("AED 데이터 정제 완료")
    print("=" * 50)

    print("원본 데이터:", f"{len(raw_records):,}건")
    print("정제 데이터:", f"{statistics['cleaned_count']:,}건")
    print("제외 데이터:", f"{statistics['excluded_count']:,}건")

    print("\n===== 제외 사유 =====")

    for reason, count in statistics["exclusion_counts"].items():
        print(f"{reason}: {count}건")

    print("\n===== 운영시간 검사 =====")

    print(
        "운영시간 누락 데이터:",
        f"{statistics['missing_time_count']:,}건"
    )

    print(
        "운영시간 검토 대상:",
        f"{statistics['review_record_count']:,}건"
    )

    print(
        "2400 초과 시간값:",
        f"{statistics['over_2400_count']:,}개"
    )

    print(
        "알 수 없는 시간 형식:",
        f"{statistics['malformed_time_count']:,}개"
    )

    print("\n===== 저장 경로 =====")

    print(CLEAN_FILE)
    print(REPORT_FILE)


if __name__ == "__main__":
    main()
