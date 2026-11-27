"""운영시간 상태를 반영한 AED 접근성 및 사각지대 분석 (발표용 시각별 비교).

analyze_availability.py가 만든 시각별 운영 여부 CSV와 DB의 4분 등시선·격자로
"그 시각에 운영 중인 AED만 있을 때"의 접근률·사각지대를 계산한다.

결과는 DB에 쓰지 않고 results/availability_metrics_<시각>.csv로만 저장한다.
(실시간 반영은 API의 coverage_cache가 담당한다. DB와 pipeline_run은 건드리지 않는다.)

    python -m pipeline.analyze_availability --datetime 2026-11-24T14:00
    python pipeline/08_analyze_available_accessibility.py --at 2026-11-24T14:00 --apply
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from common import get_engine, load_isochrone_failures


ROOT = Path(__file__).resolve().parents[1]
AVAILABILITY_DIR = ROOT / "results" / "availability"   # 중간 결과 (git 제외)
RESULTS_DIR = ROOT / "results"                          # 발표용 결과 (git 포함)
SEOUL_TZ = timezone(timedelta(hours=9), name="Asia/Seoul")

EXPECTED_DISTRICTS = 25  # 서울 자치구 수 (데이터가 바뀌어도 고정)

SCENARIOS = ("available", "available_or_unknown")

RESULT_COLUMNS = (
    "analysis_datetime", "scenario", "scope",
    "walk_pct", "coverage_km2", "deadzone_km2",
    "aed_count", "unknown_aed_count", "covered_grid_points", "total_grid_points",
)


PRECHECK_SQL = text("""
    SELECT
        (SELECT COUNT(*) FROM public.district) AS district_count,
        (SELECT COUNT(*) FROM public.aed) AS aed_count,
        (SELECT COUNT(*) FROM public.isochrone WHERE minutes = 4)
            AS four_minute_isochrones,
        (SELECT COUNT(*) FROM public.grid_point) AS grid_count,
        (SELECT COUNT(*) FROM public.grid_point
         WHERE straight_2 IS NULL
            OR straight_4 IS NULL
            OR walk_2 IS NULL
            OR walk_4 IS NULL) AS incomplete_grid,
        (SELECT COUNT(*) FROM public.district
         WHERE geom IS NULL OR NOT ST_IsValid(geom))
            AS invalid_districts,
        (SELECT COUNT(*) FROM public.aed
         WHERE source_id IS NULL OR geom IS NULL OR gu IS NULL)
            AS invalid_aed
""")


INVALID_ISOCHRONE_SQL = text("""
    SELECT COUNT(*)
    FROM (
        SELECT a.id
        FROM public.aed a
        LEFT JOIN public.isochrone i ON i.aed_id = a.id
        WHERE a.source_id <> ALL(CAST(:failed_ids AS text[]))
        GROUP BY a.id
        HAVING
            COUNT(i.minutes) <> 2
            OR COUNT(i.minutes) FILTER (WHERE i.minutes = 2) <> 1
            OR COUNT(i.minutes) FILTER (WHERE i.minutes = 4) <> 1
    ) AS invalid
""")


FAILED_AED_STATUS_SQL = text("""
    SELECT
        (
            SELECT COUNT(*)
            FROM public.aed
            WHERE source_id = ANY(CAST(:failed_ids AS text[]))
        ) AS failed_aed_count,
        (
            SELECT COUNT(*)
            FROM public.isochrone i
            JOIN public.aed a ON a.id = i.aed_id
            WHERE a.source_id = ANY(CAST(:failed_ids AS text[]))
        ) AS failed_isochrone_count
""")


CREATE_AVAILABILITY_TEMP_SQL = text("""
    CREATE TEMP TABLE tmp_availability_status (
        source_id TEXT PRIMARY KEY,
        availability_status TEXT NOT NULL
            CHECK (
                availability_status IN (
                    'available', 'unavailable', 'unknown'
                )
            )
    ) ON COMMIT DROP
""")


INSERT_AVAILABILITY_TEMP_SQL = text("""
    INSERT INTO tmp_availability_status (
        source_id,
        availability_status
    )
    VALUES (
        :source_id,
        :availability_status
    )
""")


CHECK_AVAILABILITY_IDS_SQL = text("""
    SELECT
        COUNT(*) FILTER (
            WHERE v.source_id IS NULL
        ) AS missing_status,
        (
            SELECT COUNT(*)
            FROM tmp_availability_status v
            LEFT JOIN public.aed a ON a.source_id = v.source_id
            WHERE a.id IS NULL
        ) AS unknown_aed_ids
    FROM public.aed a
    LEFT JOIN tmp_availability_status v
        ON v.source_id = a.source_id
""")


CREATE_SCENARIO_AED_SQL = text("""
    CREATE TEMP TABLE tmp_scenario_aed ON COMMIT DROP AS

    SELECT
        'available'::text AS scenario,
        a.id AS aed_id,
        a.gu,
        v.availability_status,
        i.geom
    FROM public.aed a
    JOIN tmp_availability_status v
        ON v.source_id = a.source_id
    JOIN public.isochrone i
        ON i.aed_id = a.id AND i.minutes = 4
    WHERE v.availability_status = 'available'
        AND a.source_id <> ALL(CAST(:failed_ids AS text[]))

    UNION ALL

    SELECT
        'available_or_unknown'::text AS scenario,
        a.id AS aed_id,
        a.gu,
        v.availability_status,
        i.geom
    FROM public.aed a
    JOIN tmp_availability_status v
        ON v.source_id = a.source_id
    JOIN public.isochrone i
        ON i.aed_id = a.id AND i.minutes = 4
    WHERE v.availability_status IN ('available', 'unknown')
        AND a.source_id <> ALL(CAST(:failed_ids AS text[]))
""")


CREATE_SCENARIO_UNION_SQL = text("""
    CREATE TEMP TABLE tmp_scenario_union ON COMMIT DROP AS
    SELECT
        scenario,
        ST_UnaryUnion(
            ST_Collect(ST_Transform(geom, 5179))
        ) AS geom
    FROM tmp_scenario_aed
    GROUP BY scenario
""")


CREATE_GRID_5179_SQL = text("""
    CREATE TEMP TABLE tmp_grid_5179 ON COMMIT DROP AS
    SELECT
        id,
        gu,
        ST_Transform(geom, 5179) AS geom
    FROM public.grid_point
""")


CREATE_GRID_STATS_SQL = text("""
    CREATE TEMP TABLE tmp_scenario_grid_stats ON COMMIT DROP AS
    SELECT
        s.scenario,
        g.gu,
        COUNT(*) AS total_points,
        COUNT(*) FILTER (
            WHERE ST_Covers(s.geom, g.geom)
        ) AS covered_points
    FROM tmp_scenario_union s
    CROSS JOIN tmp_grid_5179 g
    GROUP BY s.scenario, g.gu
""")


CREATE_DISTRICT_5179_SQL = text("""
    CREATE TEMP TABLE tmp_district_5179 ON COMMIT DROP AS
    SELECT
        name,
        ST_Transform(geom, 5179) AS geom
    FROM public.district
""")


CREATE_SEOUL_GEOM_SQL = text("""
    CREATE TEMP TABLE tmp_seoul_geom ON COMMIT DROP AS
    SELECT ST_UnaryUnion(ST_Collect(geom)) AS geom
    FROM tmp_district_5179
""")


CREATE_DISTRICT_OVERLAY_SQL = text("""
    CREATE TEMP TABLE tmp_scenario_district_overlay ON COMMIT DROP AS
    SELECT
        u.scenario,
        d.name,
        d.geom AS district_geom,

        ST_Multi(
            ST_CollectionExtract(
                ST_Intersection(d.geom, u.geom), 3
            )
        ) AS coverage_geom,

        ST_Multi(
            ST_CollectionExtract(
                ST_Difference(d.geom, u.geom), 3
            )
        ) AS deadzone_geom

    FROM tmp_scenario_union u
    CROSS JOIN tmp_district_5179 d
""")


CREATE_CITY_OVERLAY_SQL = text("""
    CREATE TEMP TABLE tmp_scenario_city_overlay ON COMMIT DROP AS
    SELECT
        u.scenario,
        s.geom AS seoul_geom,

        ST_Multi(
            ST_CollectionExtract(
                ST_Intersection(s.geom, u.geom), 3
            )
        ) AS coverage_geom,

        ST_Multi(
            ST_CollectionExtract(
                ST_Difference(s.geom, u.geom), 3
            )
        ) AS deadzone_geom

    FROM tmp_scenario_union u
    CROSS JOIN tmp_seoul_geom s
""")


CREATE_SCENARIO_AED_COUNTS_SQL = text("""
    CREATE TEMP TABLE tmp_scenario_aed_counts ON COMMIT DROP AS
    SELECT
        scenario,
        gu,
        COUNT(*) AS aed_count,
        COUNT(*) FILTER (
            WHERE availability_status = 'unknown'
        ) AS unknown_aed_count
    FROM tmp_scenario_aed
    GROUP BY scenario, gu
""")


CREATE_CITY_GRID_STATS_SQL = text("""
    CREATE TEMP TABLE tmp_city_grid_stats ON COMMIT DROP AS
    SELECT
        scenario,
        SUM(total_points) AS total_points,
        SUM(covered_points) AS covered_points
    FROM tmp_scenario_grid_stats
    GROUP BY scenario
""")


VERIFY_SCENARIO_SQL = text("""
    SELECT
        scenario,
        COUNT(*) AS aed_count,
        COUNT(*) FILTER (
            WHERE availability_status = 'unknown'
        ) AS unknown_aed_count
    FROM tmp_scenario_aed
    GROUP BY scenario
""")


VERIFY_CITY_OVERLAY_SQL = text("""
    SELECT
        scenario,
        ST_IsValid(seoul_geom) AS seoul_valid,
        ST_IsValid(coverage_geom) AS coverage_valid,
        ST_IsValid(deadzone_geom) AS deadzone_valid,
        ST_IsEmpty(coverage_geom) AS coverage_empty,
        ST_IsEmpty(deadzone_geom) AS deadzone_empty,

        ST_Area(seoul_geom) AS seoul_area_m2,
        ST_Area(coverage_geom) AS coverage_area_m2,
        ST_Area(deadzone_geom) AS deadzone_area_m2,

        ST_Area(
            ST_Intersection(coverage_geom, deadzone_geom)
        ) AS overlap_area_m2,

        ST_Area(
            ST_SymDifference(
                ST_UnaryUnion(
                    ST_Collect(coverage_geom, deadzone_geom)
                ),
                seoul_geom
            )
        ) AS partition_difference_m2

    FROM tmp_scenario_city_overlay
""")


VERIFY_DISTRICT_OVERLAY_SQL = text("""
    SELECT
        COUNT(*) AS overlay_count,

        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(coverage_geom)
        ) AS invalid_coverage,

        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(deadzone_geom)
        ) AS invalid_deadzone,

        MAX(
            ST_Area(
                ST_Intersection(coverage_geom, deadzone_geom)
            )
        ) AS max_overlap_m2,

        MAX(
            ST_Area(
                ST_SymDifference(
                    ST_UnaryUnion(
                        ST_Collect(coverage_geom, deadzone_geom)
                    ),
                    district_geom
                )
            )
        ) AS max_partition_difference_m2

    FROM tmp_scenario_district_overlay
""")


SELECT_METRICS_SQL = text("""
    WITH district_rows AS (
        SELECT
            g.scenario,
            g.gu AS scope,

            ROUND(
                (
                    100.0 * g.covered_points
                    / NULLIF(g.total_points, 0)
                )::numeric,
                2
            ) AS walk_pct,

            ROUND(
                (ST_Area(o.coverage_geom) / 1000000.0)::numeric,
                4
            ) AS coverage_km2,

            ROUND(
                (ST_Area(o.deadzone_geom) / 1000000.0)::numeric,
                4
            ) AS deadzone_km2,

            COALESCE(a.aed_count, 0)::integer AS aed_count,
            COALESCE(a.unknown_aed_count, 0)::integer
                AS unknown_aed_count,

            g.total_points::integer AS total_grid_points,
            g.covered_points::integer AS covered_grid_points

        FROM tmp_scenario_grid_stats g
        JOIN tmp_scenario_district_overlay o
            ON o.scenario = g.scenario
           AND o.name = g.gu
        LEFT JOIN tmp_scenario_aed_counts a
            ON a.scenario = g.scenario
           AND a.gu = g.gu
    ),

    city_rows AS (
        SELECT
            g.scenario,
            '서울'::text AS scope,

            ROUND(
                (
                    100.0 * g.covered_points
                    / NULLIF(g.total_points, 0)
                )::numeric,
                2
            ) AS walk_pct,

            ROUND(
                (ST_Area(o.coverage_geom) / 1000000.0)::numeric,
                4
            ) AS coverage_km2,

            ROUND(
                (ST_Area(o.deadzone_geom) / 1000000.0)::numeric,
                4
            ) AS deadzone_km2,

            a.aed_count::integer AS aed_count,
            a.unknown_aed_count::integer AS unknown_aed_count,

            g.total_points::integer AS total_grid_points,
            g.covered_points::integer AS covered_grid_points

        FROM tmp_city_grid_stats g
        JOIN tmp_scenario_city_overlay o
            ON o.scenario = g.scenario
        JOIN (
            SELECT
                scenario,
                SUM(aed_count)::integer AS aed_count,
                SUM(unknown_aed_count)::integer AS unknown_aed_count
            FROM tmp_scenario_aed_counts
            GROUP BY scenario
        ) a ON a.scenario = g.scenario
    )

    SELECT *
    FROM (
        SELECT * FROM city_rows
        UNION ALL
        SELECT * FROM district_rows
    ) result
    ORDER BY
        CASE scenario WHEN 'available' THEN 1 ELSE 2 END,
        CASE WHEN scope = '서울' THEN 0 ELSE 1 END,
        scope
""")


def parse_analysis_time(value: str) -> datetime:
    """'2026-11-24T14:00' → 한국 시간. analyze_availability.py와 같은 규칙."""
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "날짜 형식은 YYYY-MM-DDTHH:MM 또는 YYYY-MM-DDTHH:MM:SS 형식이어야 합니다."
        ) from exc
    if result.tzinfo is None:
        return result.replace(tzinfo=SEOUL_TZ)
    return result.astimezone(SEOUL_TZ)


def resolve_availability_files(at: str | None, csv_path: str | None) -> tuple[Path, Path]:
    """운영 여부 CSV와 요약 JSON 경로를 정한다.

    --at이 있으면 그 시각의 파일, --availability-csv가 있으면 그 파일,
    둘 다 없으면 results/availability/에서 가장 최근 파일을 쓴다.
    """
    if at is not None:
        stamp = parse_analysis_time(at).strftime("%Y%m%d_%H%M%S")
        availability_csv = AVAILABILITY_DIR / f"aed_availability_{stamp}.csv"
    elif csv_path is not None:
        availability_csv = Path(csv_path).resolve()
    else:
        candidates = sorted(AVAILABILITY_DIR.glob("aed_availability_*.csv"))
        if not candidates:
            raise FileNotFoundError(
                f"{AVAILABILITY_DIR.relative_to(ROOT)}에 운영 여부 CSV가 없습니다. "
                "먼저 실행: python -m pipeline.analyze_availability --datetime <시각>"
            )
        availability_csv = candidates[-1]

    if not availability_csv.exists():
        raise FileNotFoundError(
            f"운영 여부 CSV가 없습니다: {availability_csv} "
            "(python -m pipeline.analyze_availability --datetime <시각> 으로 먼저 만드세요)"
        )

    summary_name = availability_csv.name.replace(
        "aed_availability_", "availability_summary_"
    ).replace(".csv", ".json")
    availability_summary = availability_csv.with_name(summary_name)
    if not availability_summary.exists():
        raise FileNotFoundError(f"운영 여부 요약 JSON이 없습니다: {availability_summary}")

    return availability_csv, availability_summary


def load_availability(
    availability_csv: Path, availability_summary: Path
) -> tuple[list[dict], Counter, datetime, dict]:
    """운영시간 CSV와 요약 JSON을 검증한다."""

    rows = []
    source_ids = set()
    status_counts = Counter()

    with availability_csv.open(
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        required = {
            "serialSeq",
            "availability_status",
            "availability_error",
        }

        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("운영시간 CSV 필수 컬럼이 부족합니다.")

        for line_number, row in enumerate(reader, start=2):
            serial = (row.get("serialSeq") or "").strip()
            status = (row.get("availability_status") or "").strip()

            if not serial:
                raise ValueError(f"{line_number}행: AED 식별자가 없습니다.")

            if serial in source_ids:
                raise ValueError(f"중복 AED 식별자: {serial}")

            if status not in {"available", "unavailable", "unknown"}:
                raise ValueError(f"{serial}: 잘못된 운영 상태: {status}")

            if (row.get("availability_error") or "").strip():
                raise ValueError(f"{serial}: 운영시간 분석 예외가 있습니다.")

            source_ids.add(serial)
            status_counts[status] += 1

            rows.append({
                "source_id": serial,
                "availability_status": status,
            })

    with availability_summary.open(encoding="utf-8") as f:
        summary = json.load(f)

    try:
        analysis_datetime = datetime.fromisoformat(
            summary["analysis_datetime"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("분석 기준 시각이 올바르지 않습니다.") from exc

    if analysis_datetime.tzinfo is None:
        raise ValueError("분석 기준 시각에 시간대 정보가 없습니다.")

    if not rows:
        raise ValueError("운영시간 CSV에 AED가 없습니다.")

    # 고정 건수 대신 CSV와 요약 JSON이 서로 맞는지 검사한다
    if summary.get("total_records") != len(rows):
        raise ValueError(
            f"운영시간 요약의 전체 건수({summary.get('total_records')})가 "
            f"CSV({len(rows)})와 일치하지 않습니다."
        )

    expected_counts = summary.get("status_counts", {})

    for status in ("available", "unavailable", "unknown"):
        if status_counts[status] != expected_counts.get(status):
            raise ValueError(
                f"{status} 건수가 CSV와 요약 JSON에서 일치하지 않습니다."
            )

    if summary.get("error_count") != 0:
        raise ValueError("운영시간 분석 예외가 0건이 아닙니다.")

    return rows, status_counts, analysis_datetime, summary


def write_result_csv(path: Path, analysis_datetime: datetime, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "analysis_datetime": analysis_datetime.isoformat(),
                **{k: row[k] for k in RESULT_COLUMNS if k != "analysis_datetime"},
            })


def main() -> None:
    parser = argparse.ArgumentParser(
        description="운영시간을 반영한 AED 접근성 분석 (결과는 CSV로만 저장)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="분석을 실행해 results/availability_metrics_<시각>.csv로 저장합니다. "
             "미입력 시 사전 검사만 합니다. DB는 어느 경우에도 바꾸지 않습니다.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--at",
        metavar="DATETIME",
        help="분석 시각 (예: 2026-11-24T14:00). 같은 시각으로 만든 운영 여부 CSV를 사용합니다.",
    )
    source.add_argument(
        "--availability-csv",
        metavar="PATH",
        help="운영 여부 CSV 경로를 직접 지정합니다.",
    )
    args = parser.parse_args()

    availability_csv, availability_summary = resolve_availability_files(
        args.at, args.availability_csv
    )

    availability_rows, status_counts, analysis_datetime, summary = (
        load_availability(availability_csv, availability_summary)
    )

    failed_ids = set(load_isochrone_failures())
    failed_id_list = sorted(failed_ids)

    failed_status_counts = Counter(
        row['availability_status']
        for row in availability_rows
        if row['source_id'] in failed_ids
    )

    source_ids = sorted(
        row["source_id"] for row in availability_rows
    )

    engine = get_engine()

    try:
        with engine.connect() as conn:
            precheck = conn.execute(
                PRECHECK_SQL
            ).mappings().one()

            invalid_pairs = conn.execute(
                INVALID_ISOCHRONE_SQL,
                {"failed_ids": failed_id_list},
            ).scalar_one()

            failed_status = conn.execute(
                FAILED_AED_STATUS_SQL,
                {"failed_ids": failed_id_list},
            ).mappings().one()

            db_ids = set(
                conn.execute(
                    text("""
                        SELECT source_id
                        FROM public.aed
                        WHERE source_id = ANY(CAST(:source_ids AS text[]))
                    """),
                    {"source_ids": source_ids},
                ).scalars().all()
            )

        # 고정 건수 대신 DB 테이블과 운영 여부 CSV 사이의 관계로 검사한다
        if failed_status['failed_aed_count'] != len(failed_ids):
            raise RuntimeError(
                "실패 CSV와 DB의 AED 식별자가 일치하지 않습니다. "
                f"CSV={len(failed_ids)}, DB={failed_status['failed_aed_count']}"
            )

        if failed_status['failed_isochrone_count'] != 0:
            raise RuntimeError(
                "등시선 생성 실패 AED에 등시선 데이터가 남아 있습니다: "
                f"{failed_status['failed_isochrone_count']}건"
            )

        expected_success_isochrones = (
            precheck['aed_count'] - len(failed_ids)
        )
        if expected_success_isochrones <= 0:
            raise RuntimeError("등시선 생성 성공 AED가 0건입니다.")

        if precheck["district_count"] != EXPECTED_DISTRICTS:
            raise RuntimeError("자치구 수가 25개가 아닙니다.")

        if precheck["aed_count"] != len(availability_rows):
            raise RuntimeError(
                f"DB AED 수({precheck['aed_count']})와 운영 여부 CSV({len(availability_rows)})가 다릅니다."
            )

        if precheck["four_minute_isochrones"] != expected_success_isochrones:
            raise RuntimeError(
                f"4분 Isochrone 수({precheck['four_minute_isochrones']})가 "
                f"등시선 성공 AED 수({expected_success_isochrones})와 다릅니다."
            )

        if precheck["grid_count"] == 0:
            raise RuntimeError("격자가 0건입니다. 05_generate_grid.py를 먼저 실행하세요.")

        if precheck["incomplete_grid"] != 0:
            raise RuntimeError("기존 전체 AED 접근성 분석이 완료되지 않았습니다.")

        if precheck["invalid_districts"] or precheck["invalid_aed"]:
            raise RuntimeError("기본 공간 데이터에 오류가 있습니다.")

        if invalid_pairs != 0:
            raise RuntimeError(f"AED별 2분·4분 영역 구성 오류: {invalid_pairs}")

        if db_ids != set(source_ids):
            raise RuntimeError("운영시간 CSV와 DB의 AED 식별자가 일치하지 않습니다.")

        available_count = status_counts["available"]
        unknown_count = status_counts["unknown"]
        unavailable_count = status_counts["unavailable"]

        available_success_count = (
            available_count - failed_status_counts['available']
        )
        unknown_success_count = (
            unknown_count - failed_status_counts['unknown']
        )

        scenario_counts = {
            "available": available_success_count,
            "available_or_unknown": (
                available_success_count + unknown_success_count
            ),
        }

        print("=" * 55)
        print("운영시간 반영 접근성 분석 사전 검사")
        print("=" * 55)
        print(f"운영 여부 CSV: {availability_csv.relative_to(ROOT)}")
        print(f"분석 기준 시각: {analysis_datetime.isoformat()}")
        print(f"DB AED: {precheck['aed_count']:,}개")
        print(f"운영시간 결과: {len(availability_rows):,}개")
        print(f"등시선 성공 AED: {expected_success_isochrones:,}개")
        print(f"등시선 실패 AED (공간 분석 제외): {len(failed_ids):,}개")
        print(f"DB 식별자 일치: {len(db_ids):,}개")
        print(f"전체 AED 기준 격자: {precheck['grid_count']:,}개")
        print(f"잘못된 Isochrone 구성: {invalid_pairs}건")

        print("\n===== 운영 상태 =====")
        print(f"available: {available_count:,}개")
        print(f"unavailable: {unavailable_count:,}개")
        print(f"unknown: {unknown_count:,}개")

        print("\n===== 분석 시나리오 =====")
        print(f"확인된 운영 AED만: {scenario_counts['available']:,}개")
        print(
            "운영 불명 포함: "
            f"{scenario_counts['available_or_unknown']:,}개"
        )

        if not args.apply:
            print("\n사전 검사 완료. 결과 파일은 만들지 않았습니다.")
            print("분석을 실행하려면 --apply 옵션을 사용하세요.")
            return

        stamp = analysis_datetime.astimezone(SEOUL_TZ).strftime("%Y%m%d_%H%M%S")
        result_csv = RESULTS_DIR / f"availability_metrics_{stamp}.csv"

        # 임시 테이블만 쓰고 끝나면 되돌린다 (DB의 실제 테이블은 바꾸지 않음)
        with engine.connect() as conn:
            conn.execute(text("""
                LOCK TABLE
                    public.district,
                    public.aed,
                    public.isochrone,
                    public.grid_point
                IN SHARE MODE
            """))

            current = conn.execute(
                PRECHECK_SQL
            ).mappings().one()

            for key in ("district_count", "aed_count", "four_minute_isochrones", "grid_count"):
                if current[key] != precheck[key]:
                    raise RuntimeError(
                        f"실행 직전 DB 상태가 변경됐습니다: {key} {precheck[key]} → {current[key]}"
                    )
            if current["incomplete_grid"] != 0:
                raise RuntimeError("실행 직전 미계산 격자가 생겼습니다.")

            current_failed_status = conn.execute(
                FAILED_AED_STATUS_SQL,
                {"failed_ids": failed_id_list},
            ).mappings().one()

            if (
                current_failed_status["failed_aed_count"] != len(failed_ids)
                or current_failed_status["failed_isochrone_count"] != 0
            ):
                raise RuntimeError(
                    "실행 직전 실패 AED의 DB 상태가 변경됐습니다: "
                    f"{dict(current_failed_status)}"
                )

            current_invalid_pairs = conn.execute(
                INVALID_ISOCHRONE_SQL,
                {"failed_ids": failed_id_list},
            ).scalar_one()
            if current_invalid_pairs != 0:
                raise RuntimeError(
                    f"실행 직전 성공 AED의 등시선 구성 오류: {current_invalid_pairs}건"
                )

            conn.execute(CREATE_AVAILABILITY_TEMP_SQL)
            conn.execute(
                INSERT_AVAILABILITY_TEMP_SQL,
                availability_rows,
            )

            id_check = conn.execute(
                CHECK_AVAILABILITY_IDS_SQL
            ).mappings().one()

            if id_check["missing_status"] or id_check["unknown_aed_ids"]:
                raise RuntimeError(
                    f"운영시간 식별자 검증 실패: {dict(id_check)}"
                )

            conn.execute(
                CREATE_SCENARIO_AED_SQL,
                {"failed_ids": failed_id_list},
            )
            conn.execute(CREATE_SCENARIO_AED_COUNTS_SQL)
            conn.execute(CREATE_SCENARIO_UNION_SQL)
            conn.execute(CREATE_GRID_5179_SQL)
            conn.execute(CREATE_GRID_STATS_SQL)
            conn.execute(CREATE_DISTRICT_5179_SQL)
            conn.execute(CREATE_SEOUL_GEOM_SQL)
            conn.execute(CREATE_DISTRICT_OVERLAY_SQL)
            conn.execute(CREATE_CITY_OVERLAY_SQL)
            conn.execute(CREATE_CITY_GRID_STATS_SQL)

            conn.execute(text("""
                CREATE INDEX tmp_scenario_union_gix
                ON tmp_scenario_union USING GIST (geom)
            """))
            conn.execute(text("""
                CREATE INDEX tmp_grid_5179_gix
                ON tmp_grid_5179 USING GIST (geom)
            """))

            conn.execute(text("ANALYZE tmp_scenario_union"))
            conn.execute(text("ANALYZE tmp_grid_5179"))

            scenario_rows = conn.execute(
                VERIFY_SCENARIO_SQL
            ).mappings().all()

            actual_scenarios = {
                row["scenario"]: (
                    row["aed_count"],
                    row["unknown_aed_count"],
                )
                for row in scenario_rows
            }

            expected_scenarios = {
                "available": (available_success_count, 0),
                "available_or_unknown": (
                    available_success_count + unknown_success_count,
                    unknown_success_count,
                ),
            }

            if actual_scenarios != expected_scenarios:
                raise RuntimeError(
                    f"시나리오 AED 수 불일치: {actual_scenarios} "
                    "(운영 중 AED가 0개인 시나리오가 있으면 계산할 수 없습니다)"
                )

            city_checks = conn.execute(
                VERIFY_CITY_OVERLAY_SQL
            ).mappings().all()

            if len(city_checks) != 2:
                raise RuntimeError("시나리오별 서울 공간 레이어가 2개가 아닙니다.")

            for check in city_checks:
                if (
                    not check["seoul_valid"]
                    or not check["coverage_valid"]
                    or not check["deadzone_valid"]
                    or check["coverage_empty"]
                    or check["deadzone_empty"]
                    or check["overlap_area_m2"] > 1.0
                    or check["partition_difference_m2"] > 1.0
                ):
                    raise RuntimeError(
                        f"서울 Geometry 분할 검증 실패: {dict(check)}"
                    )

            district_check = conn.execute(
                VERIFY_DISTRICT_OVERLAY_SQL
            ).mappings().one()

            if (
                district_check["overlay_count"] != EXPECTED_DISTRICTS * 2
                or district_check["invalid_coverage"] != 0
                or district_check["invalid_deadzone"] != 0
                or district_check["max_overlap_m2"] > 1.0
                or district_check["max_partition_difference_m2"] > 1.0
            ):
                raise RuntimeError(
                    f"자치구별 Geometry 검증 실패: {dict(district_check)}"
                )

            saved_metrics = conn.execute(
                SELECT_METRICS_SQL
            ).mappings().all()

            conn.rollback()  # 임시 테이블 정리, 실제 테이블 변경 없음

        expected_rows = len(SCENARIOS) * (EXPECTED_DISTRICTS + 1)
        null_rows = sum(
            1 for row in saved_metrics
            if any(row[k] is None for k in RESULT_COLUMNS if k != "analysis_datetime")
        )
        if len(saved_metrics) != expected_rows or null_rows:
            raise RuntimeError(
                f"운영시간 통계 검증 실패: {len(saved_metrics)}행 / 예상 {expected_rows}, NULL {null_rows}행"
            )

        write_result_csv(result_csv, analysis_datetime, saved_metrics)

        print("\n===== 운영시간 반영 분석 완료 =====")
        print(f"결과 CSV: {result_csv.relative_to(ROOT)} ({len(saved_metrics)}행)")
        print("DB 변경: 없음")

        print("\n===== 서울 전체 비교 =====")
        for row in saved_metrics:
            if row["scope"] != "서울":
                continue

            print(
                f"{row['scenario']}: "
                f"접근률 {row['walk_pct']}%, "
                f"사각지대 {row['deadzone_km2']} km², "
                f"접근 가능 영역 {row['coverage_km2']} km², "
                f"포함 AED {row['aed_count']:,}개, "
                f"unknown 포함 {row['unknown_aed_count']:,}개"
            )

        print("\n===== 운영시간 기준 자치구별 접근성 =====")
        print(
            f"{'시나리오':<21} {'자치구':<8} "
            f"{'접근률':>9} {'사각지대(km²)':>14} {'AED':>7}"
        )

        for row in saved_metrics:
            if row["scope"] == "서울":
                continue

            print(
                f"{row['scenario']:<21} "
                f"{row['scope']:<8} "
                f"{row['walk_pct']:>8.2f}% "
                f"{row['deadzone_km2']:>14.4f} "
                f"{row['aed_count']:>7,}"
            )

        print("\nNULL 통계: 0")
        print("Geometry 오류: 0")
        print("운영시간 시나리오 검증: 통과")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
