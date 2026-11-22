"""전체 AED 데이터의 특정 시점 이용 가능 여부 분석."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.availability import get_availability_detail


PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = PROJECT_ROOT / "data" / "clean" / "aed.csv"
OUTPUT_DIR = PROJECT_ROOT / "results" / "availability"

SEOUL_TZ = timezone(timedelta(hours=9), name="Asia/Seoul")

STATUSES = ("available", "unavailable", "unknown")


def parse_datetime(value: str | None) -> datetime:
    """분석 시점을 한국 시간으로 변환한다."""

    if value is None:
        return datetime.now(SEOUL_TZ)

    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "날짜 형식은 YYYY-MM-DDTHH:MM 또는 "
            "YYYY-MM-DDTHH:MM:SS 형식이어야 합니다."
        ) from exc

    if result.tzinfo is None:
        return result.replace(tzinfo=SEOUL_TZ)

    return result.astimezone(SEOUL_TZ)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="전체 AED의 날짜·시각별 이용 가능 여부 분석"
    )

    parser.add_argument(
        "--datetime",
        dest="analysis_datetime",
        default=None,
        help="분석 시점. 미입력 시 현재 한국 시간 사용",
    )

    parser.add_argument(
        "--holiday",
        action="store_true",
        help="분석 날짜가 공휴일인 경우 지정",
    )

    parser.add_argument(
        "--previous-day-holiday",
        action="store_true",
        help="분석 전날이 공휴일인 경우 지정",
    )

    args = parser.parse_args()

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"AED 정제 데이터가 없습니다: {INPUT_PATH}"
        )

    analysis_time = parse_datetime(args.analysis_datetime)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = analysis_time.strftime("%Y%m%d_%H%M%S")

    output_csv = OUTPUT_DIR / f"aed_availability_{timestamp}.csv"
    output_json = OUTPUT_DIR / f"availability_summary_{timestamp}.json"

    counts = Counter()
    unknown_causes = Counter()
    error_count = 0

    with INPUT_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as source:

        reader = csv.DictReader(source)

        if reader.fieldnames is None:
            raise ValueError("AED CSV의 헤더를 읽을 수 없습니다.")

        output_fields = list(reader.fieldnames) + [
            "availability_status",
            "availability_reason",
            "today_schedule_reason",
            "previous_day_schedule_reason",
            "availability_error",
        ]

        with output_csv.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as destination:

            writer = csv.DictWriter(
                destination,
                fieldnames=output_fields,
            )
            writer.writeheader()

            for row in reader:
                error_message = ""

                try:
                    result = get_availability_detail(
                        row,
                        analysis_time,
                        is_holiday=args.holiday,
                        previous_day_is_holiday=args.previous_day_holiday,
                    )

                    status = result.status
                    reason = result.reason
                    today_reason = result.today_schedule_reason
                    previous_reason = result.previous_day_schedule_reason

                except (ValueError, TypeError, KeyError) as exc:
                    status = "unknown"
                    reason = "analysis_error"
                    today_reason = ""
                    previous_reason = ""
                    error_message = f"{type(exc).__name__}: {exc}"
                    error_count += 1

                row["availability_status"] = status
                row["availability_reason"] = reason
                row["today_schedule_reason"] = today_reason
                row["previous_day_schedule_reason"] = previous_reason
                row["availability_error"] = error_message

                writer.writerow(row)
                counts[status] += 1

                if status == "unknown":
                    cause = (
                        f"today={today_reason}; "
                        f"previous_day={previous_reason}"
                    )
                    unknown_causes[cause] += 1

    total = sum(counts.values())

    summary = {
        "analysis_datetime": analysis_time.isoformat(),
        "timezone": "Asia/Seoul",
        "source_file": str(INPUT_PATH.relative_to(PROJECT_ROOT)),
        "output_file": str(output_csv.relative_to(PROJECT_ROOT)),
        "total_records": total,
        "status_counts": {
            status: counts[status]
            for status in STATUSES
        },
        "status_percentages": {
            status: round(counts[status] / total * 100, 2)
            if total else 0
            for status in STATUSES
        },
        "unknown_reason_counts": dict(unknown_causes),
        "error_count": error_count,
        "holiday_assumptions": {
            "analysis_date_is_holiday": args.holiday,
            "previous_date_is_holiday": args.previous_day_holiday,
        },
        "warnings": [
            "일요일 플래그의 첫째~다섯째 일요일 해석은 잠정 규칙입니다.",
            "공휴일 여부는 실행 옵션으로 전달한 값만 적용합니다.",
            "분석 결과는 등록된 운영시간에 따른 참고 정보이며 실제 현장 운영을 보증하지 않습니다.",
        ],
    }

    with output_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=" * 50)
    print("AED 이용 가능 여부 분석 완료")
    print("=" * 50)
    print(f"분석 시점: {analysis_time.isoformat()}")
    print(f"전체 AED: {total:,}건")

    for status in STATUSES:
        percentage = summary["status_percentages"][status]
        print(f"{status}: {counts[status]:,}건 ({percentage}%)")

    print(f"분석 중 예외: {error_count}건")

    print("\n===== unknown 원인 =====")
    for cause, count in unknown_causes.most_common():
        print(f"{cause}: {count:,}건")

    print(f"\nCSV 결과: {output_csv}")
    print(f"JSON 요약: {output_json}")


if __name__ == "__main__":
    main()
