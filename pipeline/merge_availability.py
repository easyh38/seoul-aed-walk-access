"""AED 등시선 데이터와 운영 가능 여부 데이터를 통합한다."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_AVAILABILITY_CSV = (
    PROJECT_ROOT
    / "results/availability/aed_availability_20261121_215106.csv"
)

DEFAULT_AVAILABILITY_SUMMARY = (
    PROJECT_ROOT
    / "results/availability/availability_summary_20261121_215106.json"
)

DEFAULT_ISOCHRONE_JSONL = (
    PROJECT_ROOT
    / "results/isochrones/aed_isochrones.jsonl"
)

DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "results/isochrones/availability_merged"
)

VALID_STATUSES = {"available", "unavailable", "unknown"}
EXPECTED_CONTOURS = {2.0, 4.0}


def load_availability(path: Path) -> dict[str, dict]:
    """운영 가능 여부 CSV를 AED 식별자 기준으로 읽는다."""

    if not path.exists():
        raise FileNotFoundError(f"이용 가능 여부 CSV가 없습니다: {path}")

    availability = {}

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        required_fields = {
            "serialSeq",
            "availability_status",
            "availability_reason",
        }

        if not required_fields.issubset(set(reader.fieldnames or [])):
            raise ValueError("이용 가능 여부 CSV의 필수 컬럼이 부족합니다.")

        for row in reader:
            serial = (row.get("serialSeq") or "").strip()
            status = (row.get("availability_status") or "").strip()

            if not serial:
                raise ValueError("CSV에 AED 식별자가 없는 행이 있습니다.")

            if serial in availability:
                raise ValueError(f"CSV에서 중복 AED 식별자 발견: {serial}")

            if status not in VALID_STATUSES:
                raise ValueError(
                    f"잘못된 이용 가능 상태: {serial} / {status}"
                )

            availability[serial] = row

    return availability


def merge_files(
    availability_path: Path,
    summary_path: Path,
    isochrone_path: Path,
    output_dir: Path,
) -> None:
    """두 원본 파일을 검증하고 통합 결과를 생성한다."""

    if not isochrone_path.exists():
        raise FileNotFoundError(f"등시선 JSONL 파일이 없습니다: {isochrone_path}")

    if not summary_path.exists():
        raise FileNotFoundError(f"분석 요약 JSON이 없습니다: {summary_path}")

    availability = load_availability(availability_path)

    with summary_path.open(encoding="utf-8") as f:
        availability_summary = json.load(f)

    analysis_datetime = availability_summary.get("analysis_datetime")

    if not analysis_datetime:
        raise ValueError("분석 요약 파일에 analysis_datetime이 없습니다.")

    summary_output = availability_summary.get("output_file")
    if summary_output and Path(summary_output).name != availability_path.name:
        raise ValueError("CSV와 분석 요약 JSON의 분석 시점이 일치하지 않습니다.")

    output_dir.mkdir(parents=True, exist_ok=True)

    output_jsonl = output_dir / "aed_isochrones_with_availability.jsonl"
    output_geojson = output_dir / "aed_isochrones_with_availability.geojson"
    output_summary = output_dir / "availability_merge_summary.json"

    temp_jsonl = output_jsonl.with_suffix(".jsonl.tmp")
    temp_geojson = output_geojson.with_suffix(".geojson.tmp")

    seen_ids = set()
    status_counts = Counter()
    contour_counts = Counter()
    feature_count = 0
    record_count = 0

    metadata = {
        "availability_analysis_datetime": analysis_datetime,
        "timezone": availability_summary.get("timezone", "Asia/Seoul"),
        "availability_source": str(
            availability_path.relative_to(PROJECT_ROOT)
        ),
        "isochrone_source": str(
            isochrone_path.relative_to(PROJECT_ROOT)
        ),
        "warnings": [
            "운영 가능 여부는 지정된 분석 시점의 등록 운영시간을 기준으로 합니다.",
            "실제 현장 운영 상태를 보증하지 않습니다.",
            "일요일 플래그의 첫째~다섯째 일요일 해석은 잠정 규칙입니다.",
        ],
    }

    try:
        with (
            isochrone_path.open(encoding="utf-8") as source,
            temp_jsonl.open("w", encoding="utf-8") as jsonl_out,
            temp_geojson.open("w", encoding="utf-8") as geojson_out,
        ):
            # GeoJSON FeatureCollection 시작
            geojson_out.write('{"type":"FeatureCollection",')
            geojson_out.write('"name":"aed_isochrones_with_availability",')
            geojson_out.write('"metadata":')
            json.dump(metadata, geojson_out, ensure_ascii=False)
            geojson_out.write(',"features":[')

            first_feature = True

            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue

                record = json.loads(line)

                if record.get("status") != "success":
                    raise ValueError(
                        f"등시선 생성 실패 레코드 발견: {line_number}"
                    )

                aed = record.get("aed") or {}
                serial = str(aed.get("serialSeq") or "").strip()

                if not serial:
                    raise ValueError(
                        f"등시선 AED 식별자 누락: {line_number}"
                    )

                if serial in seen_ids:
                    raise ValueError(
                        f"등시선 JSONL 중복 AED 식별자: {serial}"
                    )

                if serial not in availability:
                    raise ValueError(
                        f"운영 가능 여부 데이터에 없는 AED: {serial}"
                    )

                seen_ids.add(serial)

                availability_row = availability[serial]
                status = availability_row["availability_status"]
                reason = availability_row.get("availability_reason", "")

                geojson = record.get("geojson") or {}

                if geojson.get("type") != "FeatureCollection":
                    raise ValueError(
                        f"GeoJSON 형식 오류: {serial}"
                    )

                features = geojson.get("features") or []

                if len(features) != 2:
                    raise ValueError(
                        f"AED의 등시선 Feature 개수가 2개가 아닙니다: {serial}"
                    )

                current_contours = set()

                for feature in features:
                    properties = feature.get("properties")

                    if not isinstance(properties, dict):
                        raise ValueError(
                            f"Feature properties 누락: {serial}"
                        )

                    if properties.get("aed_serial_seq") != serial:
                        raise ValueError(
                            f"Feature AED 식별자 불일치: {serial}"
                        )

                    try:
                        contour = float(properties["contour"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(
                            f"Contour 값 오류: {serial}"
                        ) from exc

                    current_contours.add(contour)
                    contour_counts[str(contour)] += 1

                    # Feature에 운영 가능 여부 추가
                    properties["availability_status"] = status
                    properties["availability_reason"] = reason
                    properties["availability_analysis_datetime"] = (
                        analysis_datetime
                    )

                    if not first_feature:
                        geojson_out.write(",")

                    json.dump(
                        feature,
                        geojson_out,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )

                    first_feature = False
                    feature_count += 1

                if current_contours != EXPECTED_CONTOURS:
                    raise ValueError(
                        f"2분·4분 등시선 구성이 올바르지 않습니다: "
                        f"{serial} / {current_contours}"
                    )

                # JSONL의 AED 정보에도 운영 가능 여부 추가
                aed["availability_status"] = status
                aed["availability_reason"] = reason
                aed["availability_analysis_datetime"] = analysis_datetime

                record["aed"] = aed

                json.dump(
                    record,
                    jsonl_out,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                jsonl_out.write("\n")

                status_counts[status] += 1
                record_count += 1

            # 양방향 식별자 일치 검사
            availability_ids = set(availability)
            missing_ids = availability_ids - seen_ids

            if missing_ids:
                raise ValueError(
                    f"등시선 데이터에 없는 AED가 {len(missing_ids)}건 있습니다. "
                    f"예시: {list(missing_ids)[:5]}"
                )

            if seen_ids != availability_ids:
                raise ValueError("CSV와 등시선의 AED 식별자 집합이 다릅니다.")

            geojson_out.write("]}")

        # 검증이 모두 끝난 경우에만 최종 파일 이름으로 변경
        temp_jsonl.replace(output_jsonl)
        temp_geojson.replace(output_geojson)

    except Exception:
        temp_jsonl.unlink(missing_ok=True)
        temp_geojson.unlink(missing_ok=True)
        raise

    total = sum(status_counts.values())

    result_summary = {
        "availability_analysis_datetime": analysis_datetime,
        "timezone": availability_summary.get("timezone", "Asia/Seoul"),
        "total_aed_records": total,
        "total_geojson_features": feature_count,
        "aed_record_status_counts": {
            status: status_counts[status]
            for status in ("available", "unavailable", "unknown")
        },
        "contour_feature_counts": dict(contour_counts),
        "identifier_validation": {
            "availability_csv_unique_ids": len(availability),
            "isochrone_unique_ids": len(seen_ids),
            "missing_ids": 0,
            "duplicate_ids": 0,
            "feature_identifier_mismatches": 0,
        },
        "outputs": {
            "jsonl": str(output_jsonl.relative_to(PROJECT_ROOT)),
            "geojson": str(output_geojson.relative_to(PROJECT_ROOT)),
        },
        "warnings": metadata["warnings"],
    }

    with output_summary.open("w", encoding="utf-8") as f:
        json.dump(result_summary, f, ensure_ascii=False, indent=2)

    print("=" * 50)
    print("AED 등시선 + 이용 가능 여부 통합 완료")
    print("=" * 50)
    print(f"분석 시점: {analysis_datetime}")
    print(f"AED 레코드: {total:,}건")
    print(f"GeoJSON Feature: {feature_count:,}개")

    print("\n===== 이용 가능 여부 =====")
    for status in ("available", "unavailable", "unknown"):
        print(f"{status}: {status_counts[status]:,}건")

    print("\n===== 등시선 =====")
    for contour, count in sorted(contour_counts.items()):
        print(f"{contour}분: {count:,}개")

    print(f"\nJSONL: {output_jsonl}")
    print(f"GeoJSON: {output_geojson}")
    print(f"요약: {output_summary}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AED 등시선과 운영 가능 여부 데이터 통합"
    )

    parser.add_argument(
        "--availability-csv",
        type=Path,
        default=DEFAULT_AVAILABILITY_CSV,
    )

    parser.add_argument(
        "--availability-summary",
        type=Path,
        default=DEFAULT_AVAILABILITY_SUMMARY,
    )

    parser.add_argument(
        "--isochrone-jsonl",
        type=Path,
        default=DEFAULT_ISOCHRONE_JSONL,
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )

    args = parser.parse_args()

    merge_files(
        args.availability_csv,
        args.availability_summary,
        args.isochrone_jsonl,
        args.output_dir,
    )


if __name__ == "__main__":
    main()
