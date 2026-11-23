"""서울시 자치구 경계를 PostGIS district 테이블에 적재한다."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
from dotenv import dotenv_values
from shapely.geometry import MultiPolygon
from sqlalchemy import create_engine, text


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SHAPE_PATH = (
    PROJECT_ROOT
    / "data/raw/boundaries/original/bnd_sigungu_11_2025_2Q.shp"
)

INSERT_SQL = text("""
    INSERT INTO public.district (name, geom)
    VALUES (
        :name,
        ST_Multi(ST_SetSRID(ST_GeomFromWKB(:geom_wkb), 4326))
    )
    ON CONFLICT (name)
    DO UPDATE SET geom = EXCLUDED.geom
""")


def load_boundary_data():
    """경계 파일을 읽고 DB 스키마에 맞게 검증·변환한다."""

    if not SHAPE_PATH.exists():
        raise FileNotFoundError(f"경계 파일이 없습니다: {SHAPE_PATH}")

    gdf = gpd.read_file(SHAPE_PATH)

    if gdf.crs is None:
        raise ValueError("경계 데이터에 좌표계(CRS)가 없습니다.")

    if "SIGUNGU_NM" not in gdf.columns:
        raise ValueError("SIGUNGU_NM 컬럼이 없습니다.")

    if len(gdf) != 25:
        raise ValueError(f"자치구 데이터가 25개가 아닙니다: {len(gdf)}")

    if gdf["SIGUNGU_NM"].isna().any():
        raise ValueError("자치구 이름이 누락된 행이 있습니다.")

    gdf["SIGUNGU_NM"] = gdf["SIGUNGU_NM"].str.strip()

    if gdf["SIGUNGU_NM"].duplicated().any():
        raise ValueError("중복된 자치구 이름이 있습니다.")

    if gdf.geometry.isna().any() or gdf.geometry.is_empty.any():
        raise ValueError("비어 있는 경계 도형이 있습니다.")

    if not gdf.geometry.is_valid.all():
        raise ValueError("유효하지 않은 경계 도형이 있습니다.")

    gdf = gdf.to_crs(epsg=4326)

    converted_geometries = []

    for geom in gdf.geometry:
        if geom.geom_type == "Polygon":
            geom = MultiPolygon([geom])
        elif geom.geom_type != "MultiPolygon":
            raise ValueError(
                f"지원하지 않는 도형 유형입니다: {geom.geom_type}"
            )

        converted_geometries.append(geom)

    gdf = gpd.GeoDataFrame(
        {"SIGUNGU_NM": gdf["SIGUNGU_NM"].to_numpy()},
        geometry=gpd.GeoSeries(
            converted_geometries,
            crs="EPSG:4326",
        ),
        crs="EPSG:4326",
    )

    if not gdf.geometry.is_valid.all():
        raise ValueError("좌표 변환 후 유효하지 않은 도형이 있습니다.")

    return gdf


def main():
    parser = argparse.ArgumentParser(
        description="서울시 자치구 경계 PostGIS 적재"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 DB에 적재합니다. 미입력 시 미리보기만 수행합니다.",
    )
    args = parser.parse_args()

    print("===== 자치구 경계 데이터 검증 =====")

    gdf = load_boundary_data()
    names = set(gdf["SIGUNGU_NM"])

    print(f"자치구 수: {len(gdf)}")
    print(f"좌표계: {gdf.crs}")
    print(f"도형 유형: {set(gdf.geometry.geom_type)}")
    print(f"유효하지 않은 도형: {(~gdf.geometry.is_valid).sum()}")

    for name in sorted(names):
        print(name)

    database_url = dotenv_values(PROJECT_ROOT / ".env").get(
        "DATABASE_URL"
    )

    if not database_url:
        raise RuntimeError(".env에 DATABASE_URL이 없습니다.")

    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        with engine.connect() as conn:
            version = conn.execute(
                text("SELECT PostGIS_Full_Version()")
            ).scalar_one()

            existing_count = conn.execute(
                text("SELECT COUNT(*) FROM public.district")
            ).scalar_one()

        print(f"\nPostGIS 연결: 정상 ({version.split()[0]})")
        print(f"적재 전 district 건수: {existing_count}")

        if not args.apply:
            print("\n미리보기 완료. DB는 변경하지 않았습니다.")
            print("실제 적재하려면 --apply 옵션을 사용하세요.")
            return

        records = [
            {
                "name": row["SIGUNGU_NM"],
                "geom_wkb": row.geometry.wkb,
            }
            for _, row in gdf.iterrows()
        ]

        with engine.begin() as conn:
            conn.execute(INSERT_SQL, records)

            db_names = set(
                conn.execute(
                    text("SELECT name FROM public.district")
                ).scalars().all()
            )

            if db_names != names:
                raise RuntimeError(
                    "DB 자치구 이름과 원본 경계 이름이 일치하지 않습니다."
                )

            geometry_check = conn.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE geom IS NULL) AS null_count,
                    COUNT(*) FILTER (
                        WHERE ST_SRID(geom) <> 4326
                    ) AS wrong_srid_count,
                    COUNT(*) FILTER (
                        WHERE GeometryType(geom) <> 'MULTIPOLYGON'
                    ) AS wrong_type_count,
                    COUNT(*) FILTER (
                        WHERE NOT ST_IsValid(geom)
                    ) AS invalid_count
                FROM public.district
            """)).mappings().one()

            if any(geometry_check.values()):
                raise RuntimeError(
                    f"경계 도형 검증 실패: {dict(geometry_check)}"
                )

        print("\n===== 적재 완료 =====")
        print("district 총 건수:", len(names))
        print("좌표계: EPSG:4326")
        print("도형 유형: MULTIPOLYGON")
        print("빈 도형: 0")
        print("잘못된 도형: 0")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
