"""서울시 격자 평가 지점(기본 100m, config.yaml grid_m)을 생성해 PostGIS에 적재한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import text

from common import get_engine, load_analysis_config


ROOT = Path(__file__).resolve().parents[1]

# 격자 간격은 config.yaml의 grid_m (설계 3-2)
GRID_SIZE_M = load_analysis_config()["grid_m"]
GRID_HALF_M = GRID_SIZE_M // 2
EXPECTED_DISTRICT_COUNT = 25


GRID_CTES = """
WITH projected AS MATERIALIZED (
    SELECT
        name,
        ST_Transform(geom, 5179) AS geom
    FROM public.district
),
bounds AS (
    SELECT
        floor(MIN(ST_XMin(Box3D(geom))) / :grid_size)::bigint
            * :grid_size AS min_x,
        ceil(MAX(ST_XMax(Box3D(geom))) / :grid_size)::bigint
            * :grid_size AS max_x,
        floor(MIN(ST_YMin(Box3D(geom))) / :grid_size)::bigint
            * :grid_size AS min_y,
        ceil(MAX(ST_YMax(Box3D(geom))) / :grid_size)::bigint
            * :grid_size AS max_y
    FROM projected
),
grid AS (
    SELECT
        xs.x + :half AS gx,
        ys.y + :half AS gy
    FROM bounds b
    CROSS JOIN LATERAL generate_series(
        b.min_x, b.max_x - :grid_size, :grid_size
    ) AS xs(x)
    CROSS JOIN LATERAL generate_series(
        b.min_y, b.max_y - :grid_size, :grid_size
    ) AS ys(y)
)
"""


PREVIEW_SQL = text(GRID_CTES + """
,
hits AS (
    SELECT
        g.gx,
        g.gy,
        COUNT(d.name) AS district_matches
    FROM grid g
    LEFT JOIN projected d
      ON ST_Covers(
          d.geom,
          ST_SetSRID(ST_MakePoint(g.gx, g.gy), 5179)
      )
    GROUP BY g.gx, g.gy
)
SELECT
    COUNT(*) FILTER (
        WHERE district_matches = 1
    ) AS expected_grid_points,
    COUNT(*) FILTER (
        WHERE district_matches > 1
    ) AS boundary_duplicate_points,
    COUNT(*) FILTER (
        WHERE district_matches = 0
    ) AS outside_points
FROM hits
""")


INSERT_SQL = text(GRID_CTES + """
,
assigned AS MATERIALIZED (
    SELECT
        d.name AS gu,
        ST_Transform(
            ST_SetSRID(
                ST_MakePoint(g.gx, g.gy),
                5179
            ),
            4326
        ) AS geom
    FROM grid g
    JOIN projected d
      ON ST_Covers(
          d.geom,
          ST_SetSRID(ST_MakePoint(g.gx, g.gy), 5179)
      )
)
INSERT INTO public.grid_point (
    gu,
    geom
)
SELECT gu, geom
FROM assigned
""")


DISTRICT_CHECK_SQL = text("""
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (
            WHERE geom IS NULL
        ) AS null_geom,
        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(geom)
        ) AS invalid_geom
    FROM public.district
""")


GRID_COUNT_SQL = text("""
    SELECT COUNT(*)
    FROM public.grid_point
""")


VERIFY_SQL = text("""
    SELECT
        COUNT(*) AS total,
        COUNT(DISTINCT ST_AsEWKB(g.geom)) AS unique_points,
        COUNT(*) FILTER (
            WHERE g.gu IS NULL
        ) AS null_gu,
        COUNT(*) FILTER (
            WHERE g.geom IS NULL
        ) AS null_geom,
        COUNT(*) FILTER (
            WHERE ST_SRID(g.geom) <> 4326
        ) AS wrong_srid,
        COUNT(*) FILTER (
            WHERE GeometryType(g.geom) <> 'POINT'
        ) AS wrong_type,
        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(g.geom)
        ) AS invalid_geom,
        COUNT(*) FILTER (
            WHERE d.name IS NULL
        ) AS missing_district,
        COUNT(*) FILTER (
            WHERE d.name IS NOT NULL
              AND NOT ST_Covers(d.geom, g.geom)
        ) AS outside_district
    FROM public.grid_point g
    LEFT JOIN public.district d ON d.name = g.gu
""")


def get_grid_parameters() -> dict:
    return {
        "grid_size": GRID_SIZE_M,
        "half": GRID_HALF_M,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"서울시 {GRID_SIZE_M}m 격자 평가 지점 생성"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 DB에 격자 지점을 저장합니다.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="기존 grid_point를 지우고 다시 만듭니다 (--apply와 함께, 재실행용).",
    )
    args = parser.parse_args()

    if args.replace and not args.apply:
        parser.error("--replace는 --apply와 함께 사용합니다.")

    engine = get_engine()
    parameters = get_grid_parameters()

    try:
        with engine.connect() as conn:
            district_check = conn.execute(
                DISTRICT_CHECK_SQL
            ).mappings().one()

            if district_check["total"] != EXPECTED_DISTRICT_COUNT:
                raise RuntimeError(
                    f"자치구 데이터가 25개가 아닙니다: "
                    f"{district_check['total']}"
                )

            if (
                district_check["null_geom"]
                or district_check["invalid_geom"]
            ):
                raise RuntimeError(
                    f"자치구 Geometry 검증 실패: {dict(district_check)}"
                )

            existing_count = conn.execute(
                GRID_COUNT_SQL
            ).scalar_one()

            preview = conn.execute(
                PREVIEW_SQL,
                parameters,
            ).mappings().one()

        expected_count = preview["expected_grid_points"]
        overlap_count = preview["boundary_duplicate_points"]
        outside_count = preview["outside_points"]

        print("=" * 50)
        print(f"{GRID_SIZE_M}m 격자 생성 사전 검사")
        print("=" * 50)
        print(f"자치구 개수: {district_check['total']:,}개")
        print(f"격자 간격: {GRID_SIZE_M}m")
        print(f"예상 평가 지점: {expected_count:,}개")
        print(f"자치구 경계 중복: {overlap_count:,}개")
        print(f"행정구역 외부 격자: {outside_count:,}개")
        print(f"기존 grid_point: {existing_count:,}개")

        if expected_count == 0:
            raise RuntimeError("생성 가능한 격자 지점이 없습니다.")

        if overlap_count != 0:
            raise RuntimeError(
                f"자치구 경계 중복 지점이 있습니다: {overlap_count}"
            )

        if not args.apply:
            print("\n미리보기 완료. DB는 변경하지 않았습니다.")
            print("실제 적재하려면 --apply 옵션을 사용하세요.")
            return

        if existing_count != 0 and not args.replace:
            raise RuntimeError(
                "grid_point 테이블이 비어 있지 않습니다. "
                "기존 데이터를 지우고 다시 만들려면 --replace를 함께 사용하세요."
            )

        # 하나의 트랜잭션으로 격자 생성 및 검증
        with engine.begin() as conn:
            conn.execute(text(
                "LOCK TABLE public.grid_point IN SHARE ROW EXCLUSIVE MODE"
            ))

            if args.replace:
                # 기존 격자와 판정 결과를 지우고 처음부터 만든다 (id도 1부터 다시)
                conn.execute(text("TRUNCATE public.grid_point RESTART IDENTITY"))
                print(f"기존 grid_point {existing_count:,}개 삭제")

            current_count = conn.execute(
                GRID_COUNT_SQL
            ).scalar_one()

            if current_count != 0:
                raise RuntimeError(
                    f"실행 중 grid_point가 변경되었습니다: {current_count}건"
                )

            conn.execute(INSERT_SQL, parameters)

            verification = conn.execute(
                VERIFY_SQL
            ).mappings().one()

            invalid_counts = {
                key: verification[key]
                for key in (
                    "null_gu",
                    "null_geom",
                    "wrong_srid",
                    "wrong_type",
                    "invalid_geom",
                    "missing_district",
                    "outside_district",
                )
            }

            if verification["total"] != expected_count:
                raise RuntimeError(
                    f"격자 생성 건수 불일치: {dict(verification)}"
                )

            if verification["unique_points"] != expected_count:
                raise RuntimeError(
                    f"중복 격자 지점 발견: {dict(verification)}"
                )

            if any(invalid_counts.values()):
                raise RuntimeError(
                    f"격자 공간정보 검증 실패: {invalid_counts}"
                )

            district_counts = conn.execute(text("""
                SELECT gu, COUNT(*) AS point_count
                FROM public.grid_point
                GROUP BY gu
                ORDER BY gu
            """)).mappings().all()

            if len(district_counts) != EXPECTED_DISTRICT_COUNT:
                raise RuntimeError(
                    "25개 자치구에 격자가 모두 생성되지 않았습니다."
                )

        print(f"\n===== {GRID_SIZE_M}m 격자 생성 완료 =====")
        print(f"평가 지점: {verification['total']:,}개")
        print(f"고유 지점: {verification['unique_points']:,}개")
        print("NULL 자치구: 0")
        print("NULL Geometry: 0")
        print("좌표계 오류: 0")
        print("Geometry 타입 오류: 0")
        print("유효하지 않은 Geometry: 0")
        print("자치구 외부 지점: 0")

        print("\n===== 자치구별 격자 수 =====")
        for row in district_counts:
            print(f"{row['gu']}: {row['point_count']:,}개")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
