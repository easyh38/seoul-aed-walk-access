"""서울 전체 및 자치구별 AED 접근성 통계와 사각지대 레이어를 생성한다."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_AED = 11589
EXPECTED_ISOCHRONES = 23178
EXPECTED_GRID = 60528
EXPECTED_DISTRICTS = 25

MODE = "oneway"
STRAIGHT_DISTANCE_4M = 340.0


PRECHECK_SQL = text("""
    SELECT
        (SELECT COUNT(*) FROM public.district) AS district_count,
        (SELECT COUNT(*) FROM public.aed) AS aed_count,
        (SELECT COUNT(*) FROM public.isochrone) AS isochrone_count,
        (SELECT COUNT(*) FROM public.grid_point) AS grid_count,

        (SELECT COUNT(*) FROM public.aed
         WHERE gu IS NULL OR geom IS NULL) AS invalid_aed,

        (SELECT COUNT(*) FROM public.grid_point
         WHERE gu IS NULL OR geom IS NULL) AS invalid_grid,

        (SELECT COUNT(*) FROM public.grid_point
         WHERE straight_2 IS NULL
            OR straight_4 IS NULL
            OR walk_2 IS NULL
            OR walk_4 IS NULL) AS incomplete_grid,

        (SELECT COUNT(*) FROM public.district
         WHERE geom IS NULL OR NOT ST_IsValid(geom)) AS invalid_districts,

        (SELECT COUNT(*) FROM public.isochrone
         WHERE geom IS NULL
            OR NOT ST_IsValid(geom)
            OR ST_IsEmpty(geom)) AS invalid_isochrones,

        (SELECT COUNT(*) FROM public.pipeline_run) AS previous_runs,
        (SELECT COUNT(*) FROM public.metrics) AS previous_metrics,
        (SELECT COUNT(*) FROM public.layer) AS previous_layers
""")


INVALID_PAIR_SQL = text("""
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


CREATE_TEMP_TABLES = [
    """
    CREATE TEMP TABLE tmp_district_5179 ON COMMIT DROP AS
    SELECT
        name,
        ST_Transform(geom, 5179) AS geom
    FROM public.district
    """,
    """
    CREATE TEMP TABLE tmp_coverage_union ON COMMIT DROP AS
    SELECT
        ST_UnaryUnion(
            ST_Collect(ST_Transform(geom, 5179))
        ) AS geom
    FROM public.isochrone
    WHERE minutes = 4
    """,
    """
    CREATE TEMP TABLE tmp_seoul_geom ON COMMIT DROP AS
    SELECT ST_UnaryUnion(ST_Collect(geom)) AS geom
    FROM tmp_district_5179
    """,
    """
    CREATE TEMP TABLE tmp_district_overlay ON COMMIT DROP AS
    SELECT
        d.name,
        ST_Multi(
            ST_CollectionExtract(
                ST_Intersection(d.geom, c.geom), 3
            )
        ) AS coverage_geom,
        ST_Multi(
            ST_CollectionExtract(
                ST_Difference(d.geom, c.geom), 3
            )
        ) AS deadzone_geom
    FROM tmp_district_5179 d
    CROSS JOIN tmp_coverage_union c
    """,
    """
    CREATE TEMP TABLE tmp_city_overlay ON COMMIT DROP AS
    SELECT
        s.geom AS seoul_geom,
        ST_Multi(
            ST_CollectionExtract(
                ST_Intersection(s.geom, c.geom), 3
            )
        ) AS coverage_geom,
        ST_Multi(
            ST_CollectionExtract(
                ST_Difference(s.geom, c.geom), 3
            )
        ) AS deadzone_geom
    FROM tmp_seoul_geom s
    CROSS JOIN tmp_coverage_union c
    """,
]


OVERLAY_VERIFY_SQL = text("""
    SELECT
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

    FROM tmp_city_overlay
""")


DISTRICT_OVERLAY_VERIFY_SQL = text("""
    SELECT
        COUNT(*) AS district_count,
        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(coverage_geom)
        ) AS invalid_coverage,
        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(deadzone_geom)
        ) AS invalid_deadzone
    FROM tmp_district_overlay
""")


INSERT_METRICS_SQL = text("""
    WITH district_grid AS (
        SELECT
            gu,
            COUNT(*) AS total_points,
            COUNT(*) FILTER (
                WHERE straight_4 IS TRUE
            ) AS straight_points,
            COUNT(*) FILTER (
                WHERE walk_4 IS TRUE
            ) AS walk_points
        FROM public.grid_point
        GROUP BY gu
    ),
    district_aed AS (
        SELECT
            gu,
            COUNT(*) AS aed_count
        FROM public.aed
        GROUP BY gu
    ),
    district_metrics AS (
        SELECT
            g.gu AS scope,

            ROUND(
                (100.0 * g.straight_points
                    / NULLIF(g.total_points, 0))::numeric,
                2
            ) AS straight_pct,

            ROUND(
                (100.0 * g.walk_points
                    / NULLIF(g.total_points, 0))::numeric,
                2
            ) AS walk_pct,

            ROUND(
                (100.0 * (g.straight_points - g.walk_points)
                    / NULLIF(g.total_points, 0))::numeric,
                2
            ) AS diff_pp,

            ROUND(
                (ST_Area(o.deadzone_geom) / 1000000.0)::numeric,
                4
            ) AS deadzone_km2,

            COALESCE(a.aed_count, 0)::integer AS aed_count

        FROM district_grid g
        JOIN tmp_district_overlay o ON o.name = g.gu
        LEFT JOIN district_aed a ON a.gu = g.gu
    ),
    city_metrics AS (
        SELECT
            '서울'::text AS scope,

            ROUND(
                (
                    100.0 * COUNT(*) FILTER (
                        WHERE straight_4 IS TRUE
                    ) / NULLIF(COUNT(*), 0)
                )::numeric,
                2
            ) AS straight_pct,

            ROUND(
                (
                    100.0 * COUNT(*) FILTER (
                        WHERE walk_4 IS TRUE
                    ) / NULLIF(COUNT(*), 0)
                )::numeric,
                2
            ) AS walk_pct,

            ROUND(
                (
                    100.0 * (
                        COUNT(*) FILTER (WHERE straight_4 IS TRUE)
                        -
                        COUNT(*) FILTER (WHERE walk_4 IS TRUE)
                    ) / NULLIF(COUNT(*), 0)
                )::numeric,
                2
            ) AS diff_pp,

            (
                SELECT ROUND(
                    (ST_Area(deadzone_geom) / 1000000.0)::numeric,
                    4
                )
                FROM tmp_city_overlay
            ) AS deadzone_km2,

            (SELECT COUNT(*)::integer FROM public.aed) AS aed_count

        FROM public.grid_point
    )
    INSERT INTO public.metrics (
        run_id,
        scope,
        mode,
        straight_pct,
        walk_pct,
        diff_pp,
        deadzone_km2,
        aed_count
    )
    SELECT
        :run_id,
        scope,
        'oneway',
        straight_pct,
        walk_pct,
        diff_pp,
        deadzone_km2,
        aed_count
    FROM (
        SELECT * FROM city_metrics
        UNION ALL
        SELECT * FROM district_metrics
    ) result
""")


UPSERT_LAYER_SQL = text("""
    INSERT INTO public.layer (
        mode,
        kind,
        geom
    )
    SELECT
        'oneway',
        'coverage',
        ST_Transform(coverage_geom, 4326)
    FROM tmp_city_overlay

    UNION ALL

    SELECT
        'oneway',
        'deadzone',
        ST_Transform(deadzone_geom, 4326)
    FROM tmp_city_overlay

    ON CONFLICT (mode, kind)
    DO UPDATE SET
        geom = EXCLUDED.geom
""")


VERIFY_METRICS_SQL = text("""
    SELECT
        COUNT(*) AS metric_count,
        COUNT(*) FILTER (
            WHERE straight_pct IS NULL
               OR walk_pct IS NULL
               OR diff_pp IS NULL
               OR deadzone_km2 IS NULL
               OR aed_count IS NULL
        ) AS null_metrics
    FROM public.metrics
    WHERE run_id = :run_id
      AND mode = 'oneway'
""")


VERIFY_LAYER_SQL = text("""
    SELECT
        COUNT(*) AS layer_count,
        COUNT(*) FILTER (
            WHERE geom IS NULL
               OR ST_SRID(geom) <> 4326
               OR GeometryType(geom) <> 'MULTIPOLYGON'
               OR NOT ST_IsValid(geom)
               OR ST_IsEmpty(geom)
        ) AS invalid_layers
    FROM public.layer
    WHERE mode = 'oneway'
      AND kind IN ('coverage', 'deadzone')
""")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="서울 AED 접근성 통계 및 사각지대 레이어 생성"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 통계 및 공간 레이어를 DB에 저장합니다.",
    )
    parser.add_argument(
        "--osm-date",
        default="2026-11-09",
        help="사용한 OSM 데이터 기준일 (기본: 2026-11-09)",
    )
    parser.add_argument(
        "--aed-date",
        default="2026-11-10",
        help="사용한 AED 원본 데이터 기준일 (기본: 2026-11-10)",
    )
    args = parser.parse_args()

    osm_date = date.fromisoformat(args.osm_date)
    aed_date = date.fromisoformat(args.aed_date)

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
                INVALID_PAIR_SQL
            ).scalar_one()

        print("=" * 55)
        print("서울 AED 통계·사각지대 분석 사전 검사")
        print("=" * 55)
        print(f"자치구: {precheck['district_count']:,}개")
        print(f"AED: {precheck['aed_count']:,}개")
        print(f"Isochrone: {precheck['isochrone_count']:,}개")
        print(f"100m 격자: {precheck['grid_count']:,}개")
        print(f"잘못된 AED별 Isochrone: {invalid_pairs}건")
        print(f"미계산 격자: {precheck['incomplete_grid']:,}개")
        print(f"이전 실행 이력: {precheck['previous_runs']:,}건")
        print(f"이전 통계: {precheck['previous_metrics']:,}건")
        print(f"기존 레이어: {precheck['previous_layers']:,}개")
        print(f"분석 모드: {MODE}")
        print("운영시간 반영: 아니요 (전체 등록 AED 기준)")
        print("면적 계산: EPSG:5179 실제 Geometry 면적")

        expected = {
            "district_count": EXPECTED_DISTRICTS,
            "aed_count": EXPECTED_AED,
            "isochrone_count": EXPECTED_ISOCHRONES,
            "grid_count": EXPECTED_GRID,
        }

        for key, value in expected.items():
            if precheck[key] != value:
                raise RuntimeError(
                    f"{key} 불일치: {precheck[key]} / 예상 {value}"
                )

        invalid_keys = (
            "invalid_aed",
            "invalid_grid",
            "invalid_districts",
            "invalid_isochrones",
        )

        for key in invalid_keys:
            if precheck[key] != 0:
                raise RuntimeError(
                    f"공간 데이터 사전 검사 실패: {key}={precheck[key]}"
                )

        if precheck["incomplete_grid"] != 0:
            raise RuntimeError("아직 접근성 계산이 완료되지 않은 격자가 있습니다.")

        if invalid_pairs != 0:
            raise RuntimeError(
                f"AED별 2분·4분 Isochrone 구성 오류: {invalid_pairs}"
            )

        if not args.apply:
            print("\n미리보기 완료. DB는 변경하지 않았습니다.")
            print("실제 저장하려면 --apply 옵션을 사용하세요.")
            return

        analysis_config = {
            "pipeline": "pipeline/07_build_metrics.py",
            "mode": MODE,
            "grid_spacing_m": 100,
            "straight_distance_4min_m": STRAIGHT_DISTANCE_4M,
            "walking_coverage": (
                "Union of all AED-origin 4-minute pedestrian isochrones"
            ),
            "coverage_area_method": (
                "District intersection with the 4-minute isochrone union"
            ),
            "deadzone_area_method": (
                "District geometry minus the 4-minute isochrone union"
            ),
            "area_srid": 5179,
            "accessibility_percentage_method": (
                "100m grid-center point proportion"
            ),
            "operating_hours_applied": False,
            "osm_date": args.osm_date,
            "aed_date": args.aed_date,
            "direction_note": (
                "One-way AED-origin isochrone coverage; "
                "roundtrip analysis is not included"
            ),
        }

        with engine.begin() as conn:
            # 원본 공간 테이블을 잠가 분석 도중 데이터가 바뀌지 않게 한다.
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
                or current["isochrone_count"] != EXPECTED_ISOCHRONES
                or current["grid_count"] != EXPECTED_GRID
                or current["incomplete_grid"] != 0
            ):
                raise RuntimeError("실행 직전 원본 데이터 상태가 변경됐습니다.")

            # 1. 모든 4분 Isochrone을 EPSG:5179에서 합집합 처리
            for statement in CREATE_TEMP_TABLES:
                conn.execute(text(statement))

            # 2. 공간 연산 결과 검증
            city_check = conn.execute(
                OVERLAY_VERIFY_SQL
            ).mappings().one()

            district_check = conn.execute(
                DISTRICT_OVERLAY_VERIFY_SQL
            ).mappings().one()

            if (
                not city_check["seoul_valid"]
                or not city_check["coverage_valid"]
                or not city_check["deadzone_valid"]
                or city_check["coverage_empty"]
                or city_check["deadzone_empty"]
            ):
                raise RuntimeError(
                    f"서울 Geometry 검증 실패: {dict(city_check)}"
                )

            if (
                city_check["overlap_area_m2"] > 1.0
                or city_check["partition_difference_m2"] > 1.0
            ):
                raise RuntimeError(
                    f"접근 영역과 사각지대의 공간 분할 오류: {dict(city_check)}"
                )

            if (
                district_check["district_count"] != EXPECTED_DISTRICTS
                or district_check["invalid_coverage"] != 0
                or district_check["invalid_deadzone"] != 0
            ):
                raise RuntimeError(
                    f"자치구별 공간 연산 검증 실패: {dict(district_check)}"
                )

            # 3. 분석 실행 이력 생성
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
                        analysis_config,
                        ensure_ascii=False,
                    ),
                    "osm_date": osm_date,
                    "aed_date": aed_date,
                },
            ).scalar_one()

            # 4. 서울 전체 및 25개 자치구 통계 저장
            conn.execute(
                INSERT_METRICS_SQL,
                {"run_id": run_id},
            )

            # 5. 지도용 coverage/deadzone 레이어 저장
            conn.execute(UPSERT_LAYER_SQL)

            metrics_check = conn.execute(
                VERIFY_METRICS_SQL,
                {"run_id": run_id},
            ).mappings().one()

            layer_check = conn.execute(
                VERIFY_LAYER_SQL
            ).mappings().one()

            if (
                metrics_check["metric_count"] != 26
                or metrics_check["null_metrics"] != 0
            ):
                raise RuntimeError(
                    f"통계 저장 검증 실패: {dict(metrics_check)}"
                )

            if (
                layer_check["layer_count"] != 2
                or layer_check["invalid_layers"] != 0
            ):
                raise RuntimeError(
                    f"지도 레이어 저장 검증 실패: {dict(layer_check)}"
                )

            saved_metrics = conn.execute(
                text("""
                    SELECT
                        scope,
                        straight_pct,
                        walk_pct,
                        diff_pp,
                        deadzone_km2,
                        aed_count
                    FROM public.metrics
                    WHERE run_id = :run_id
                      AND mode = 'oneway'
                    ORDER BY
                        CASE WHEN scope = '서울' THEN 0 ELSE 1 END,
                        scope
                """),
                {"run_id": run_id},
            ).mappings().all()

        print("\n===== 분석 결과 저장 완료 =====")
        print(f"Pipeline run ID: {run_id}")
        print(f"저장 통계: {metrics_check['metric_count']}건")
        print(f"지도 레이어: {layer_check['layer_count']}개")
        print(
            f"서울 전체 4분 사각지대: "
            f"{city_check['deadzone_area_m2'] / 1_000_000:.4f} km²"
        )
        print(
            f"서울 전체 4분 접근 가능 영역: "
            f"{city_check['coverage_area_m2'] / 1_000_000:.4f} km²"
        )

        print("\n===== 서울·자치구별 접근성 =====")
        print(
            f"{'구분':<9} {'직선거리':>10} {'실제 보행':>10} "
            f"{'차이(pp)':>10} {'사각지대(km²)':>14} {'AED':>7}"
        )

        for row in saved_metrics:
            print(
                f"{row['scope']:<9} "
                f"{row['straight_pct']:>9.2f}% "
                f"{row['walk_pct']:>9.2f}% "
                f"{row['diff_pp']:>9.2f} "
                f"{row['deadzone_km2']:>14.4f} "
                f"{row['aed_count']:>7,}"
            )

        print("\nNULL 통계: 0")
        print("Geometry 오류: 0")
        print("공간 분할 검증: 통과")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
