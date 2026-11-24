"""100m 격자별 직선거리·실보행 AED 접근성을 분석한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import text

from common import get_engine, load_analysis_config


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_DISTRICTS = 25  # 서울 자치구 수 (데이터가 바뀌어도 고정)

# 직선거리 판정 반경은 config.yaml의 modes.*.radius_m (설계 3-2)
_MODES = load_analysis_config()["modes"]
STRAIGHT_2_M = _MODES["roundtrip"]["radius_m"]
STRAIGHT_4_M = _MODES["oneway"]["radius_m"]


# EPSG:5179 좌표를 이용한 직선거리 검색용 공간 인덱스
CREATE_AED_METRIC_INDEX_SQL = text("""
    CREATE INDEX IF NOT EXISTS aed_geom_5179_idx
    ON public.aed
    USING GIST (ST_Transform(geom, 5179))
""")


PRECHECK_SQL = text("""
    SELECT
        (SELECT COUNT(*) FROM public.district) AS district_count,
        (SELECT COUNT(*) FROM public.aed) AS aed_count,
        (SELECT COUNT(*) FROM public.isochrone) AS isochrone_count,
        (SELECT COUNT(DISTINCT aed_id)
         FROM public.isochrone) AS isochrone_aed_count,
        (SELECT COUNT(*) FROM public.grid_point) AS grid_count,

        (SELECT COUNT(*) FILTER (
            WHERE straight_2 IS NULL
         ) FROM public.grid_point) AS straight_2_null,

        (SELECT COUNT(*) FILTER (
            WHERE straight_4 IS NULL
         ) FROM public.grid_point) AS straight_4_null,

        (SELECT COUNT(*) FILTER (
            WHERE walk_2 IS NULL
         ) FROM public.grid_point) AS walk_2_null,

        (SELECT COUNT(*) FILTER (
            WHERE walk_4 IS NULL
         ) FROM public.grid_point) AS walk_4_null
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


# 4개 접근성 지표를 한 번에 갱신한다.
# straight: EPSG:5179에서 미터 단위 거리 계산
# walk: 실제 Valhalla Isochrone 내부에 격자점이 포함되는지 검사
UPDATE_ACCESSIBILITY_SQL = text("""
    WITH projected_grid AS MATERIALIZED (
        SELECT
            id,
            ST_Transform(geom, 5179) AS geom_5179
        FROM public.grid_point
    )
    UPDATE public.grid_point AS g
    SET
        straight_2 = EXISTS (
            SELECT 1
            FROM public.aed AS a
            WHERE ST_DWithin(
                ST_Transform(a.geom, 5179),
                p.geom_5179,
                :straight_2_m
            )
        ),

        straight_4 = EXISTS (
            SELECT 1
            FROM public.aed AS a
            WHERE ST_DWithin(
                ST_Transform(a.geom, 5179),
                p.geom_5179,
                :straight_4_m
            )
        ),

        walk_2 = EXISTS (
            SELECT 1
            FROM public.isochrone AS i
            WHERE i.minutes = 2
              AND ST_Covers(i.geom, g.geom)
        ),

        walk_4 = EXISTS (
            SELECT 1
            FROM public.isochrone AS i
            WHERE i.minutes = 4
              AND ST_Covers(i.geom, g.geom)
        )

    FROM projected_grid AS p
    WHERE p.id = g.id
""")


VERIFY_SQL = text("""
    SELECT
        COUNT(*) AS total,

        COUNT(*) FILTER (
            WHERE straight_2 IS TRUE
        ) AS straight_2_true,

        COUNT(*) FILTER (
            WHERE straight_2 IS FALSE
        ) AS straight_2_false,

        COUNT(*) FILTER (
            WHERE straight_4 IS TRUE
        ) AS straight_4_true,

        COUNT(*) FILTER (
            WHERE straight_4 IS FALSE
        ) AS straight_4_false,

        COUNT(*) FILTER (
            WHERE walk_2 IS TRUE
        ) AS walk_2_true,

        COUNT(*) FILTER (
            WHERE walk_2 IS FALSE
        ) AS walk_2_false,

        COUNT(*) FILTER (
            WHERE walk_4 IS TRUE
        ) AS walk_4_true,

        COUNT(*) FILTER (
            WHERE walk_4 IS FALSE
        ) AS walk_4_false,

        COUNT(*) FILTER (
            WHERE straight_2 IS NULL
        ) AS straight_2_null,

        COUNT(*) FILTER (
            WHERE straight_4 IS NULL
        ) AS straight_4_null,

        COUNT(*) FILTER (
            WHERE walk_2 IS NULL
        ) AS walk_2_null,

        COUNT(*) FILTER (
            WHERE walk_4 IS NULL
        ) AS walk_4_null

    FROM public.grid_point
""")


DISTRICT_SUMMARY_SQL = text("""
    SELECT
        gu,
        COUNT(*) AS total_points,

        COUNT(*) FILTER (
            WHERE straight_4 IS TRUE
        ) AS straight_4_points,

        COUNT(*) FILTER (
            WHERE walk_4 IS TRUE
        ) AS walk_4_points,

        COUNT(*) FILTER (
            WHERE walk_4 IS FALSE
        ) AS walk_4_blind_points,

        ROUND(
            100.0 * COUNT(*) FILTER (
                WHERE straight_4 IS TRUE
            ) / NULLIF(COUNT(*), 0),
            2
        ) AS straight_4_pct,

        ROUND(
            100.0 * COUNT(*) FILTER (
                WHERE walk_4 IS TRUE
            ) / NULLIF(COUNT(*), 0),
            2
        ) AS walk_4_pct,

        ROUND(
            100.0 * COUNT(*) FILTER (
                WHERE walk_4 IS FALSE
            ) / NULLIF(COUNT(*), 0),
            2
        ) AS walk_4_blind_pct

    FROM public.grid_point
    GROUP BY gu
    ORDER BY gu
""")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="100m 격자별 AED 접근성 분석"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 접근성 지표를 계산하고 DB에 저장합니다.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="이미 계산된 판정 결과를 다시 계산합니다 (--apply와 함께, 재실행용).",
    )
    args = parser.parse_args()

    if args.replace and not args.apply:
        parser.error("--replace는 --apply와 함께 사용합니다.")

    engine = get_engine()

    try:
        with engine.connect() as conn:
            precheck = conn.execute(
                PRECHECK_SQL
            ).mappings().one()

            invalid_isochrones = conn.execute(
                INVALID_ISOCHRONE_SQL
            ).scalar_one()

        print("=" * 55)
        print("AED 접근성 분석 사전 검사")
        print("=" * 55)
        print(f"자치구: {precheck['district_count']:,}개")
        print(f"AED: {precheck['aed_count']:,}개")
        print(f"Isochrone: {precheck['isochrone_count']:,}개")
        print(f"Isochrone 고유 AED: {precheck['isochrone_aed_count']:,}개")
        print(f"100m 격자: {precheck['grid_count']:,}개")
        print(f"잘못된 AED별 Isochrone 구성: {invalid_isochrones}건")

        print("\n===== 접근성 기준 =====")
        print(f"직선거리 2분: {STRAIGHT_2_M:.0f}m")
        print(f"직선거리 4분: {STRAIGHT_4_M:.0f}m")
        print("실제 보행 2분: Valhalla Isochrone")
        print("실제 보행 4분: Valhalla Isochrone")

        null_counts = {
            "straight_2": precheck["straight_2_null"],
            "straight_4": precheck["straight_4_null"],
            "walk_2": precheck["walk_2_null"],
            "walk_4": precheck["walk_4_null"],
        }

        if precheck["district_count"] != EXPECTED_DISTRICTS:
            raise RuntimeError("자치구 데이터가 25개가 아닙니다.")

        # 고정 건수 대신 테이블 사이의 관계로 검사한다
        aed_count = precheck["aed_count"]
        grid_count = precheck["grid_count"]

        if aed_count == 0:
            raise RuntimeError("AED가 0건입니다. 03_load_aed.py를 먼저 실행하세요.")

        if grid_count == 0:
            raise RuntimeError("격자가 0건입니다. 05_generate_grid.py를 먼저 실행하세요.")

        if precheck["isochrone_count"] != aed_count * 2:
            raise RuntimeError(
                f"Isochrone 건수 불일치: {precheck['isochrone_count']} / "
                f"예상 {aed_count * 2} (AED {aed_count} × 2)"
            )

        if precheck["isochrone_aed_count"] != aed_count:
            raise RuntimeError(
                f"Isochrone 고유 AED 수 불일치: {precheck['isochrone_aed_count']} / AED {aed_count}"
            )

        if invalid_isochrones != 0:
            raise RuntimeError(
                f"AED별 2분·4분 Isochrone 구성 오류: {invalid_isochrones}"
            )

        # 모든 지표가 NULL이면 아직 계산하지 않은 상태
        if all(value == grid_count for value in null_counts.values()):
            already_analyzed = False

        # 모든 지표에 NULL이 없으면 이미 계산된 상태
        elif all(value == 0 for value in null_counts.values()):
            already_analyzed = True

        elif args.replace:
            already_analyzed = True  # 일부만 계산된 상태도 --replace면 전부 다시 계산

        else:
            raise RuntimeError(
                f"접근성 지표가 부분적으로 계산된 상태입니다: {null_counts} "
                "(전부 다시 계산하려면 --apply --replace)"
            )

        print("\n===== 기존 계산 상태 =====")
        for key, value in null_counts.items():
            print(f"{key} 미계산: {value:,}개")

        if not args.apply:
            print("\n미리보기 완료. DB는 변경하지 않았습니다.")
            print("실제 계산하려면 --apply 옵션을 사용하세요.")
            return

        if already_analyzed and not args.replace:
            raise RuntimeError(
                "접근성 지표가 이미 계산되어 있습니다. "
                "다시 계산하려면 --replace를 함께 사용하세요."
            )

        # 하나의 트랜잭션으로 인덱스 생성, 분석, 검증을 수행한다.
        with engine.begin() as conn:
            conn.execute(text(
                "LOCK TABLE public.grid_point IN SHARE ROW EXCLUSIVE MODE"
            ))

            # 재검사: 실행 직전 데이터 변경 방지
            current = conn.execute(
                PRECHECK_SQL
            ).mappings().one()

            null_keys = (
                "straight_2_null",
                "straight_4_null",
                "walk_2_null",
                "walk_4_null",
            )
            if (
                current["grid_count"] != grid_count
                or current["aed_count"] != aed_count
                or current["isochrone_count"] != precheck["isochrone_count"]
                or any(current[key] != precheck[key] for key in null_keys)
            ):
                raise RuntimeError(
                    "실행 직전 DB 상태가 변경되었습니다. 분석을 중단합니다."
                )

            # 미터 단위 직선거리 검색 인덱스
            conn.execute(CREATE_AED_METRIC_INDEX_SQL)

            conn.execute(text("ANALYZE public.aed"))
            conn.execute(text("ANALYZE public.isochrone"))
            conn.execute(text("ANALYZE public.grid_point"))

            result = conn.execute(
                UPDATE_ACCESSIBILITY_SQL,
                {
                    "straight_2_m": STRAIGHT_2_M,
                    "straight_4_m": STRAIGHT_4_M,
                },
            )

            verification = conn.execute(
                VERIFY_SQL
            ).mappings().one()

            if verification["total"] != grid_count:
                raise RuntimeError("접근성 분석 대상 건수가 변경됐습니다.")

            null_keys = (
                "straight_2_null",
                "straight_4_null",
                "walk_2_null",
                "walk_4_null",
            )

            if any(verification[key] != 0 for key in null_keys):
                raise RuntimeError(
                    f"계산되지 않은 지점이 있습니다: {dict(verification)}"
                )

            district_rows = conn.execute(
                DISTRICT_SUMMARY_SQL
            ).mappings().all()

            if len(district_rows) != EXPECTED_DISTRICTS:
                raise RuntimeError(
                    "자치구별 접근성 통계가 25개 구로 계산되지 않았습니다."
                )

        print("\n===== 접근성 분석 완료 =====")
        print(f"갱신 행: {result.rowcount:,}개")

        for key in (
            "straight_2",
            "straight_4",
            "walk_2",
            "walk_4",
        ):
            true_key = f"{key}_true"
            false_key = f"{key}_false"

            print(
                f"{key}: 접근 가능 {verification[true_key]:,}개 / "
                f"접근 불가 {verification[false_key]:,}개"
            )

        print("\n===== 자치구별 4분 접근성 =====")
        print(
            f"{'자치구':<8} {'평가 지점':>8} "
            f"{'직선거리':>10} {'실제 보행':>10} {'보행 사각지대':>12}"
        )

        for row in district_rows:
            print(
                f"{row['gu']:<8} "
                f"{row['total_points']:>8,} "
                f"{row['straight_4_pct']:>9.2f}% "
                f"{row['walk_4_pct']:>9.2f}% "
                f"{row['walk_4_blind_points']:>12,}"
            )

        print("\nNULL 지표: 0개")
        print("접근성 지표 검증: 통과")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
