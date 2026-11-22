"""운영시간 상태를 반영한 AED 접근성 및 사각지대 분석."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]

AVAILABILITY_CSV = (
    ROOT / "results/availability/aed_availability_20261122_162400.csv"
)
AVAILABILITY_SUMMARY = (
    ROOT / "results/availability/availability_summary_20261122_162400.json"
)

EXPECTED_AED = 11589
EXPECTED_GRID = 60528
EXPECTED_DISTRICTS = 25

SCENARIOS = ("available", "available_or_unknown")


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
        GROUP BY a.id
        HAVING
            COUNT(i.minutes) <> 2
            OR COUNT(i.minutes) FILTER (WHERE i.minutes = 2) <> 1
            OR COUNT(i.minutes) FILTER (WHERE i.minutes = 4) <> 1
    ) AS invalid
""")


CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS public.availability_metrics (
        run_id INTEGER NOT NULL
            REFERENCES public.pipeline_run(id) ON DELETE CASCADE,
        scope TEXT NOT NULL,
        scenario TEXT NOT NULL
            CHECK (scenario IN ('available', 'available_or_unknown')),
        analysis_datetime TIMESTAMPTZ NOT NULL,
        walk_pct NUMERIC NOT NULL,
        coverage_km2 NUMERIC NOT NULL,
        deadzone_km2 NUMERIC NOT NULL,
        aed_count INTEGER NOT NULL,
        unknown_aed_count INTEGER NOT NULL,
        total_grid_points INTEGER NOT NULL,
        covered_grid_points INTEGER NOT NULL,
        PRIMARY KEY (run_id, scope, scenario)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS public.availability_layer (
        run_id INTEGER NOT NULL
            REFERENCES public.pipeline_run(id) ON DELETE CASCADE,
        scenario TEXT NOT NULL
            CHECK (scenario IN ('available', 'available_or_unknown')),
        kind TEXT NOT NULL
            CHECK (kind IN ('coverage', 'deadzone')),
        analysis_datetime TIMESTAMPTZ NOT NULL,
        geom geometry(MultiPolygon, 4326) NOT NULL,
        PRIMARY KEY (run_id, scenario, kind)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS availability_layer_geom_gix
    ON public.availability_layer USING GIST (geom)
    """,
]


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


INSERT_METRICS_SQL = text("""
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

    INSERT INTO public.availability_metrics (
        run_id,
        scope,
        scenario,
        analysis_datetime,
        walk_pct,
        coverage_km2,
        deadzone_km2,
        aed_count,
        unknown_aed_count,
        total_grid_points,
        covered_grid_points
    )
    SELECT
        :run_id,
        scope,
        scenario,
        :analysis_datetime,
        walk_pct,
        coverage_km2,
        deadzone_km2,
        aed_count,
        unknown_aed_count,
        total_grid_points,
        covered_grid_points
    FROM (
        SELECT * FROM city_rows
        UNION ALL
        SELECT * FROM district_rows
    ) result
""")


INSERT_LAYERS_SQL = text("""
    INSERT INTO public.availability_layer (
        run_id,
        scenario,
        kind,
        analysis_datetime,
        geom
    )
    SELECT
        :run_id,
        scenario,
        'coverage',
        :analysis_datetime,
        ST_Transform(coverage_geom, 4326)
    FROM tmp_scenario_city_overlay

    UNION ALL

    SELECT
        :run_id,
        scenario,
        'deadzone',
        :analysis_datetime,
        ST_Transform(deadzone_geom, 4326)
    FROM tmp_scenario_city_overlay
""")


VERIFY_METRICS_SQL = text("""
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (
            WHERE walk_pct IS NULL
               OR coverage_km2 IS NULL
               OR deadzone_km2 IS NULL
               OR aed_count IS NULL
               OR unknown_aed_count IS NULL
        ) AS null_rows
    FROM public.availability_metrics
    WHERE run_id = :run_id
""")


VERIFY_LAYERS_SQL = text("""
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (
            WHERE geom IS NULL
               OR ST_SRID(geom) <> 4326
               OR GeometryType(geom) <> 'MULTIPOLYGON'
               OR NOT ST_IsValid(geom)
        ) AS invalid_rows
    FROM public.availability_layer
    WHERE run_id = :run_id
""")


def load_availability() -> tuple[list[dict], Counter, datetime, dict]:
    """운영시간 CSV와 요약 JSON을 검증한다."""

    if not AVAILABILITY_CSV.exists():
        raise FileNotFoundError(AVAILABILITY_CSV)

    if not AVAILABILITY_SUMMARY.exists():
        raise FileNotFoundError(AVAILABILITY_SUMMARY)

    rows = []
    source_ids = set()
    status_counts = Counter()

    with AVAILABILITY_CSV.open(
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

    with AVAILABILITY_SUMMARY.open(encoding="utf-8") as f:
        summary = json.load(f)

    try:
        analysis_datetime = datetime.fromisoformat(
            summary["analysis_datetime"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("분석 기준 시각이 올바르지 않습니다.") from exc

    if analysis_datetime.tzinfo is None:
        raise ValueError("분석 기준 시각에 시간대 정보가 없습니다.")

    if len(rows) != EXPECTED_AED:
        raise ValueError(f"운영시간 AED 건수 오류: {len(rows)}")

    expected_counts = summary.get("status_counts", {})

    for status in ("available", "unavailable", "unknown"):
        if status_counts[status] != expected_counts.get(status):
            raise ValueError(
                f"{status} 건수가 CSV와 요약 JSON에서 일치하지 않습니다."
            )

    if summary.get("total_records") != EXPECTED_AED:
        raise ValueError("운영시간 분석 요약의 전체 건수가 일치하지 않습니다.")

    if summary.get("error_count") != 0:
        raise ValueError("운영시간 분석 예외가 0건이 아닙니다.")

    return rows, status_counts, analysis_datetime, summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="운영시간을 반영한 AED 접근성 분석"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="결과를 PostGIS에 저장합니다. 미입력 시 미리보기만 수행합니다.",
    )
    parser.add_argument(
        "--osm-date",
        default="2026-11-09",
    )
    parser.add_argument(
        "--aed-date",
        default="2026-11-10",
    )
    args = parser.parse_args()

    osm_date = date.fromisoformat(args.osm_date)
    aed_date = date.fromisoformat(args.aed_date)

    availability_rows, status_counts, analysis_datetime, summary = (
        load_availability()
    )

    source_ids = sorted(
        row["source_id"] for row in availability_rows
    )

    database_url = dotenv_values(ROOT / ".env").get("DATABASE_URL")

    if not database_url:
        raise RuntimeError(".env에 DATABASE_URL이 없습니다.")

    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        with engine.connect() as conn:
            precheck = conn.execute(
                PRECHECK_SQL
            ).mappings().one()

            invalid_pairs = conn.execute(
                INVALID_ISOCHRONE_SQL
            ).scalar_one()

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

        if precheck["district_count"] != EXPECTED_DISTRICTS:
            raise RuntimeError("자치구 수가 25개가 아닙니다.")

        if precheck["aed_count"] != EXPECTED_AED:
            raise RuntimeError("DB AED 수가 예상과 다릅니다.")

        if precheck["four_minute_isochrones"] != EXPECTED_AED:
            raise RuntimeError("4분 Isochrone 수가 예상과 다릅니다.")

        if precheck["grid_count"] != EXPECTED_GRID:
            raise RuntimeError("100m 격자 수가 예상과 다릅니다.")

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

        scenario_counts = {
            "available": available_count,
            "available_or_unknown": available_count + unknown_count,
        }

        print("=" * 55)
        print("운영시간 반영 접근성 분석 사전 검사")
        print("=" * 55)
        print(f"분석 기준 시각: {analysis_datetime.isoformat()}")
        print(f"DB AED: {precheck['aed_count']:,}개")
        print(f"운영시간 결과: {len(availability_rows):,}개")
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
            print("\n미리보기 완료. DB는 변경하지 않았습니다.")
            print("실제 저장하려면 --apply 옵션을 사용하세요.")
            return

        run_config = {
            "pipeline": "pipeline/08_analyze_available_accessibility.py",
            "analysis_type": "operating_hours_snapshot",
            "analysis_datetime": analysis_datetime.isoformat(),
            "timezone": summary.get("timezone", "Asia/Seoul"),
            "availability_csv": str(
                AVAILABILITY_CSV.relative_to(ROOT)
            ),
            "availability_summary": str(
                AVAILABILITY_SUMMARY.relative_to(ROOT)
            ),
            "status_counts": dict(status_counts),
            "scenarios": {
                "available": {
                    "included_statuses": ["available"],
                    "description": "운영 중으로 확인된 AED만 포함",
                },
                "available_or_unknown": {
                    "included_statuses": ["available", "unknown"],
                    "description": (
                        "unknown을 잠재적 이용 가능으로 가정한 참고 시나리오"
                    ),
                },
            },
            "walking_minutes": 4,
            "grid_spacing_m": 100,
            "coverage_method": (
                "Union of AED-origin 4-minute pedestrian isochrones"
            ),
            "area_srid": 5179,
            "percentage_method": "100m grid-center point proportion",
            "arrival_time_dynamic_check": False,
            "osm_date": args.osm_date,
            "aed_date": args.aed_date,
            "warnings": [
                "운영 상태는 지정된 분석 시점의 스냅샷입니다.",
                "실제 도착 예상 시각별 운영 여부를 동적으로 판정하지 않습니다.",
                "available_or_unknown은 unknown AED가 이용 가능하다고 보장하지 않습니다.",
                "Isochrone은 AED를 출발점으로 계산한 4분 보행 도달 영역입니다.",
            ],
        }

        with engine.begin() as conn:
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

            if (
                current["district_count"] != EXPECTED_DISTRICTS
                or current["aed_count"] != EXPECTED_AED
                or current["four_minute_isochrones"] != EXPECTED_AED
                or current["grid_count"] != EXPECTED_GRID
                or current["incomplete_grid"] != 0
            ):
                raise RuntimeError("실행 직전 DB 상태가 변경됐습니다.")

            # 기존 metrics/layer와 분리된 운영시간 전용 테이블
            for statement in CREATE_TABLES:
                conn.execute(text(statement))

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

            conn.execute(CREATE_SCENARIO_AED_SQL)
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
                "available": (available_count, 0),
                "available_or_unknown": (
                    available_count + unknown_count,
                    unknown_count,
                ),
            }

            if actual_scenarios != expected_scenarios:
                raise RuntimeError(
                    f"시나리오 AED 수 불일치: {actual_scenarios}"
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

            run_id = conn.execute(
                text("""
                    INSERT INTO public.pipeline_run (
                        config,
                        osm_date,
                        aed_date
                    )
                    VALUES (
                        CAST(:config AS jsonb),
                        :osm_date,
                        :aed_date
                    )
                    RETURNING id
                """),
                {
                    "config": json.dumps(
                        run_config,
                        ensure_ascii=False,
                    ),
                    "osm_date": osm_date,
                    "aed_date": aed_date,
                },
            ).scalar_one()

            conn.execute(
                INSERT_METRICS_SQL,
                {
                    "run_id": run_id,
                    "analysis_datetime": analysis_datetime,
                },
            )

            conn.execute(
                INSERT_LAYERS_SQL,
                {
                    "run_id": run_id,
                    "analysis_datetime": analysis_datetime,
                },
            )

            metrics_check = conn.execute(
                VERIFY_METRICS_SQL,
                {"run_id": run_id},
            ).mappings().one()

            layer_check = conn.execute(
                VERIFY_LAYERS_SQL,
                {"run_id": run_id},
            ).mappings().one()

            if (
                metrics_check["total"] != 52
                or metrics_check["null_rows"] != 0
            ):
                raise RuntimeError(
                    f"운영시간 통계 검증 실패: {dict(metrics_check)}"
                )

            if (
                layer_check["total"] != 4
                or layer_check["invalid_rows"] != 0
            ):
                raise RuntimeError(
                    f"운영시간 지도 레이어 검증 실패: {dict(layer_check)}"
                )

            saved_metrics = conn.execute(
                text("""
                    SELECT
                        scenario,
                        scope,
                        walk_pct,
                        coverage_km2,
                        deadzone_km2,
                        aed_count,
                        unknown_aed_count,
                        covered_grid_points,
                        total_grid_points
                    FROM public.availability_metrics
                    WHERE run_id = :run_id
                    ORDER BY
                        CASE scenario
                            WHEN 'available' THEN 1
                            ELSE 2
                        END,
                        CASE WHEN scope = '서울' THEN 0 ELSE 1 END,
                        scope
                """),
                {"run_id": run_id},
            ).mappings().all()

        print("\n===== 운영시간 반영 분석 저장 완료 =====")
        print(f"Pipeline run ID: {run_id}")
        print(f"저장 통계: {metrics_check['total']}건")
        print(f"저장 지도 레이어: {layer_check['total']}개")

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
