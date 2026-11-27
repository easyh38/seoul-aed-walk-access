"""서울 전체 및 자치구별 AED 접근성 통계와 사각지대 레이어를 생성한다.

편도(oneway, 4분)와 왕복 기준(roundtrip, 편도 2분) 두 가지를 같은 방식으로 계산해
metrics(26행 × 2), layer(2개 × 2)에 저장하고, 같은 지표를 results/metrics.csv로도 남긴다.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

from sqlalchemy import text

from common import get_engine, load_analysis_config


ROOT = Path(__file__).resolve().parents[1]
METRICS_CSV = ROOT / "results" / "metrics.csv"

EXPECTED_DISTRICTS = 25  # 서울 자치구 수 (데이터가 바뀌어도 고정)

# mode별 등시선 시간과 06_analyze_accessibility.py가 채운 grid_point 판정 칼럼
MODES = {
    "oneway": {"minutes": 4, "straight": "straight_4", "walk": "walk_4"},
    "roundtrip": {"minutes": 2, "straight": "straight_2", "walk": "walk_2"},
}

CSV_COLUMNS = (
    "run_id", "scope", "mode",
    "straight_pct", "walk_pct", "diff_pp", "deadzone_km2", "aed_count",
)


PRECHECK_SQL = text("""
    SELECT
        (SELECT COUNT(*) FROM public.district) AS district_count,
        (SELECT COUNT(*) FROM public.aed) AS aed_count,
        (SELECT COUNT(*) FROM public.isochrone) AS isochrone_count,
        (SELECT COUNT(DISTINCT aed_id)
         FROM public.isochrone) AS isochrone_aed_count,

        (SELECT COUNT(*)
         FROM public.aed a
         WHERE EXISTS (
             SELECT 1 FROM public.isochrone i
             WHERE i.aed_id = a.id AND i.minutes = 2
         )
         AND EXISTS (
             SELECT 1 FROM public.isochrone i
             WHERE i.aed_id = a.id AND i.minutes = 4
         )) AS successful_aed_count,

        (SELECT COUNT(*)
         FROM public.aed a
         WHERE NOT EXISTS (
             SELECT 1 FROM public.isochrone i
             WHERE i.aed_id = a.id
         )) AS failed_aed_count,

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
            COUNT(i.minutes) > 0
            AND (
                COUNT(i.minutes) <> 2
                OR COUNT(i.minutes) FILTER (WHERE i.minutes = 2) <> 1
                OR COUNT(i.minutes) FILTER (WHERE i.minutes = 4) <> 1
            )
    ) AS invalid
""")


# mode와 무관한 임시 테이블 (한 번만 만든다)
CREATE_BASE_TEMP_TABLES = [
    """
    CREATE TEMP TABLE tmp_district_5179 ON COMMIT DROP AS
    SELECT
        name,
        ST_Transform(geom, 5179) AS geom
    FROM public.district
    """,
    """
    CREATE TEMP TABLE tmp_seoul_geom ON COMMIT DROP AS
    SELECT ST_UnaryUnion(ST_Collect(geom)) AS geom
    FROM tmp_district_5179
    """,
]

# mode별 임시 테이블. 이전 mode의 테이블을 지우고 같은 이름으로 다시 만든다.
DROP_MODE_TEMP_TABLES = """
    DROP TABLE IF EXISTS tmp_coverage_union, tmp_district_overlay, tmp_city_overlay
"""

CREATE_MODE_TEMP_TABLES = [
    """
    CREATE TEMP TABLE tmp_coverage_union ON COMMIT DROP AS
    SELECT
        ST_UnaryUnion(
            ST_Collect(ST_Transform(geom, 5179))
        ) AS geom
    FROM public.isochrone
    WHERE minutes = :minutes
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


# {straight}, {walk}에는 MODES의 칼럼 이름(코드에 정해 둔 상수)만 들어간다
INSERT_METRICS_TEMPLATE = """
    WITH district_grid AS (
        SELECT
            gu,
            COUNT(*) AS total_points,
            COUNT(*) FILTER (
                WHERE {straight} IS TRUE
            ) AS straight_points,
            COUNT(*) FILTER (
                WHERE {walk} IS TRUE
            ) AS walk_points
        FROM public.grid_point
        GROUP BY gu
    ),
    district_aed AS (
        SELECT
            a.gu,
            COUNT(*) AS aed_count
        FROM public.aed a
        WHERE EXISTS (
            SELECT 1
            FROM public.isochrone i
            WHERE i.aed_id = a.id
              AND i.minutes = 2
        )
        AND EXISTS (
            SELECT 1
            FROM public.isochrone i
            WHERE i.aed_id = a.id
              AND i.minutes = 4
        )
        GROUP BY a.gu
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
                        WHERE {straight} IS TRUE
                    ) / NULLIF(COUNT(*), 0)
                )::numeric,
                2
            ) AS straight_pct,

            ROUND(
                (
                    100.0 * COUNT(*) FILTER (
                        WHERE {walk} IS TRUE
                    ) / NULLIF(COUNT(*), 0)
                )::numeric,
                2
            ) AS walk_pct,

            ROUND(
                (
                    100.0 * (
                        COUNT(*) FILTER (WHERE {straight} IS TRUE)
                        -
                        COUNT(*) FILTER (WHERE {walk} IS TRUE)
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

            (
                SELECT COUNT(*)::integer
                FROM public.aed a
                WHERE EXISTS (
                    SELECT 1
                    FROM public.isochrone i
                    WHERE i.aed_id = a.id
                      AND i.minutes = 2
                )
                AND EXISTS (
                    SELECT 1
                    FROM public.isochrone i
                    WHERE i.aed_id = a.id
                      AND i.minutes = 4
                )
            ) AS aed_count

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
        :mode,
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
"""


UPSERT_LAYER_SQL = text("""
    INSERT INTO public.layer (
        mode,
        kind,
        geom
    )
    SELECT
        :mode,
        'coverage',
        ST_Transform(coverage_geom, 4326)
    FROM tmp_city_overlay

    UNION ALL

    SELECT
        :mode,
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
      AND mode = :mode
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
    WHERE mode = :mode
      AND kind IN ('coverage', 'deadzone')
""")


SAVED_METRICS_SQL = text("""
    SELECT
        run_id,
        scope,
        mode,
        straight_pct,
        walk_pct,
        diff_pp,
        deadzone_km2,
        aed_count
    FROM public.metrics
    WHERE run_id = :run_id
    ORDER BY
        CASE mode WHEN 'oneway' THEN 0 ELSE 1 END,
        CASE WHEN scope = '서울' THEN 0 ELSE 1 END,
        scope
""")


def check_source_data(precheck, invalid_pairs) -> None:
    """원본 테이블끼리 맞는지 검사한다 (고정 건수 대신 데이터 사이의 관계로 확인)."""

    if precheck["district_count"] != EXPECTED_DISTRICTS:
        raise RuntimeError(
            f"자치구 수 불일치: {precheck['district_count']} / 예상 {EXPECTED_DISTRICTS}"
        )

    for key in ("aed_count", "grid_count"):
        if precheck[key] == 0:
            raise RuntimeError(f"{key}가 0건입니다. 이전 단계를 먼저 실행하세요.")

    # 등시선 성공 AED와 완전 실패 AED가 전체 AED를 구성해야 한다.
    successful_aed_count = precheck["successful_aed_count"]
    failed_aed_count = precheck["failed_aed_count"]

    if successful_aed_count == 0:
        raise RuntimeError("2분·4분 등시선 생성에 성공한 AED가 없습니다.")

    if successful_aed_count + failed_aed_count != precheck["aed_count"]:
        raise RuntimeError(
            "AED가 성공·실패 집합으로 완전히 분류되지 않았습니다: "
            f"전체 {precheck['aed_count']}, 성공 {successful_aed_count}, "
            f"실패 {failed_aed_count}"
        )

    if precheck["isochrone_count"] != successful_aed_count * len(MODES):
        raise RuntimeError(
            f"등시선 수 불일치: {precheck['isochrone_count']} / "
            f"예상 {successful_aed_count * len(MODES)} "
            f"(성공 AED {successful_aed_count} × {len(MODES)})"
        )

    if precheck["isochrone_aed_count"] != successful_aed_count:
        raise RuntimeError(
            "등시선 고유 AED 수 불일치: "
            f"{precheck['isochrone_aed_count']} / "
            f"성공 AED {successful_aed_count}"
        )

    for key in ("invalid_aed", "invalid_grid", "invalid_districts", "invalid_isochrones"):
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


def build_mode(conn, run_id: int, mode: str) -> dict:
    """한 mode의 도달 구역·사각지대를 계산해 metrics, layer에 저장한다."""

    spec = MODES[mode]

    conn.execute(text(DROP_MODE_TEMP_TABLES))
    for statement in CREATE_MODE_TEMP_TABLES:
        conn.execute(text(statement), {"minutes": spec["minutes"]})

    city_check = conn.execute(OVERLAY_VERIFY_SQL).mappings().one()
    district_check = conn.execute(DISTRICT_OVERLAY_VERIFY_SQL).mappings().one()

    if (
        not city_check["seoul_valid"]
        or not city_check["coverage_valid"]
        or not city_check["deadzone_valid"]
        or city_check["coverage_empty"]
        or city_check["deadzone_empty"]
    ):
        raise RuntimeError(
            f"[{mode}] 서울 Geometry 검증 실패: {dict(city_check)}"
        )

    if (
        city_check["overlap_area_m2"] > 1.0
        or city_check["partition_difference_m2"] > 1.0
    ):
        raise RuntimeError(
            f"[{mode}] 접근 영역과 사각지대의 공간 분할 오류: {dict(city_check)}"
        )

    if (
        district_check["district_count"] != EXPECTED_DISTRICTS
        or district_check["invalid_coverage"] != 0
        or district_check["invalid_deadzone"] != 0
    ):
        raise RuntimeError(
            f"[{mode}] 자치구별 공간 연산 검증 실패: {dict(district_check)}"
        )

    insert_metrics = text(INSERT_METRICS_TEMPLATE.format(
        straight=spec["straight"], walk=spec["walk"],
    ))
    conn.execute(insert_metrics, {"run_id": run_id, "mode": mode})
    conn.execute(UPSERT_LAYER_SQL, {"mode": mode})

    metrics_check = conn.execute(
        VERIFY_METRICS_SQL, {"run_id": run_id, "mode": mode}
    ).mappings().one()
    layer_check = conn.execute(
        VERIFY_LAYER_SQL, {"mode": mode}
    ).mappings().one()

    if (
        metrics_check["metric_count"] != EXPECTED_DISTRICTS + 1
        or metrics_check["null_metrics"] != 0
    ):
        raise RuntimeError(
            f"[{mode}] 통계 저장 검증 실패: {dict(metrics_check)}"
        )

    if (
        layer_check["layer_count"] != 2
        or layer_check["invalid_layers"] != 0
    ):
        raise RuntimeError(
            f"[{mode}] 지도 레이어 저장 검증 실패: {dict(layer_check)}"
        )

    return dict(city_check)


def write_metrics_csv(rows) -> None:
    """metrics 테이블에 저장한 이번 실행의 지표를 results/metrics.csv로 저장한다."""

    METRICS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with METRICS_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in CSV_COLUMNS})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="서울 AED 접근성 통계 및 사각지대 레이어 생성 (편도·왕복)"
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

    engine = get_engine()

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
        print(f"전체 AED: {precheck['aed_count']:,}개")
        print(f"등시선 생성 성공 AED: {precheck['successful_aed_count']:,}개")
        print(f"등시선 생성 실패 AED(통계 제외): {precheck['failed_aed_count']:,}개")
        print(f"Isochrone: {precheck['isochrone_count']:,}개")
        print(f"Isochrone 고유 AED: {precheck['isochrone_aed_count']:,}개")
        print(f"100m 격자: {precheck['grid_count']:,}개")
        print(f"불완전한 AED별 Isochrone: {invalid_pairs}건")
        print(f"미계산 격자: {precheck['incomplete_grid']:,}개")
        print(f"이전 실행 이력: {precheck['previous_runs']:,}건")
        print(f"이전 통계: {precheck['previous_metrics']:,}건")
        print(f"기존 레이어: {precheck['previous_layers']:,}개")
        mode_labels = ", ".join(
            f"{mode}({spec['minutes']}분)" for mode, spec in MODES.items()
        )
        print(f"분석 모드: {mode_labels}")
        print("운영시간 반영: 아니요 (전체 등록 AED 기준)")
        print("면적 계산: EPSG:5179 실제 Geometry 면적")

        check_source_data(precheck, invalid_pairs)

        if not args.apply:
            print("\n미리보기 완료. DB는 변경하지 않았습니다.")
            print("실제 저장하려면 --apply 옵션을 사용하세요.")
            return

        # 05·06이 사용한 분석 조건(config.yaml)을 실행 기록에 남긴다
        settings = load_analysis_config()
        analysis_config = {
            "pipeline": "pipeline/07_build_metrics.py",
            "modes": {
                mode: {
                    "isochrone_minutes": spec["minutes"],
                    "straight_radius_m": settings["modes"][mode]["radius_m"],
                }
                for mode, spec in MODES.items()
            },
            "grid_spacing_m": settings["grid_m"],
            "walking_coverage": (
                "Union of all AED-origin pedestrian isochrones per mode "
                "(oneway: 4 min, roundtrip: 2 min)"
            ),
            "coverage_area_method": (
                "District intersection with the isochrone union"
            ),
            "deadzone_area_method": (
                "District geometry minus the isochrone union"
            ),
            "area_srid": 5179,
            "accessibility_percentage_method": (
                "100m grid-center point proportion"
            ),
            "operating_hours_applied": False,
            "osm_date": args.osm_date,
            "aed_date": args.aed_date,
        }

        city_results = {}

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

            # 사전 검사 이후 원본 데이터가 바뀌었는지 확인
            source_count_keys = (
                "district_count",
                "aed_count",
                "isochrone_count",
                "isochrone_aed_count",
                "successful_aed_count",
                "failed_aed_count",
                "grid_count",
            )
            for key in source_count_keys:
                if current[key] != precheck[key]:
                    raise RuntimeError(
                        f"실행 직전 원본 데이터 상태가 변경됐습니다: "
                        f"{key} {precheck[key]} → {current[key]}"
                    )

            current_invalid_pairs = conn.execute(
                INVALID_PAIR_SQL
            ).scalar_one()
            if current_invalid_pairs != invalid_pairs:
                raise RuntimeError(
                    "실행 직전 AED별 Isochrone 구성이 변경됐습니다."
                )
            if current["incomplete_grid"] != 0:
                raise RuntimeError("실행 직전 미계산 격자가 생겼습니다.")

            for statement in CREATE_BASE_TEMP_TABLES:
                conn.execute(text(statement))

            # 분석 실행 이력 생성
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

            # mode별 통계·레이어 저장 (하나라도 실패하면 전체 취소)
            for mode in MODES:
                city_results[mode] = build_mode(conn, run_id, mode)

            saved_metrics = conn.execute(
                SAVED_METRICS_SQL,
                {"run_id": run_id},
            ).mappings().all()

        # DB 저장이 확정된 뒤 같은 값을 CSV로 남긴다
        write_metrics_csv(saved_metrics)

        print("\n===== 분석 결과 저장 완료 =====")
        print(f"Pipeline run ID: {run_id}")
        print(f"저장 통계: {len(saved_metrics)}건 ({len(MODES)}개 mode × {EXPECTED_DISTRICTS + 1})")
        print(f"지도 레이어: {len(MODES) * 2}개")
        print(f"CSV: {METRICS_CSV.relative_to(ROOT)}")

        for mode, city in city_results.items():
            print(
                f"[{mode}] 서울 사각지대 {city['deadzone_area_m2'] / 1_000_000:.4f} km², "
                f"접근 가능 영역 {city['coverage_area_m2'] / 1_000_000:.4f} km²"
            )

        for mode in MODES:
            print(f"\n===== 서울·자치구별 접근성 ({mode}) =====")
            print(
                f"{'구분':<9} {'직선거리':>10} {'실제 보행':>10} "
                f"{'차이(pp)':>10} {'사각지대(km²)':>14} {'AED':>7}"
            )
            for row in saved_metrics:
                if row["mode"] != mode:
                    continue
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
