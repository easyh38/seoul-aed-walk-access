"""검증된 AED Isochrone GeoJSON을 PostGIS에 적재한다."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from common import get_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = PROJECT_ROOT / "results/isochrones/aed_isochrones.jsonl"

EXPECTED_CONTOURS = {2.0, 4.0}


UPSERT_SQL = text("""
    INSERT INTO public.isochrone (
        aed_id,
        minutes,
        geom
    )
    VALUES (
        :aed_id,
        :minutes,
        ST_Multi(
            ST_SetSRID(
                ST_GeomFromGeoJSON(:geojson),
                4326
            )
        )
    )
    ON CONFLICT (aed_id, minutes)
    DO UPDATE SET
        geom = EXCLUDED.geom
""")


VERIFY_SQL = text("""
    SELECT
        COUNT(*) AS total,
        COUNT(DISTINCT i.aed_id) AS unique_aed,
        COUNT(*) FILTER (WHERE i.minutes = 2) AS two_minutes,
        COUNT(*) FILTER (WHERE i.minutes = 4) AS four_minutes,
        COUNT(*) FILTER (WHERE i.geom IS NULL) AS null_geom,
        COUNT(*) FILTER (
            WHERE ST_SRID(i.geom) <> 4326
        ) AS wrong_srid,
        COUNT(*) FILTER (
            WHERE GeometryType(i.geom) <> 'MULTIPOLYGON'
        ) AS wrong_type,
        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(i.geom)
        ) AS invalid_geom,
        COUNT(*) FILTER (
            WHERE ST_IsEmpty(i.geom)
        ) AS empty_geom
    FROM public.isochrone i
    JOIN public.aed a ON a.id = i.aed_id
    WHERE a.source_id = ANY(CAST(:source_ids AS text[]))
""")


PAIR_VERIFY_SQL = text("""
    SELECT COUNT(*)
    FROM (
        SELECT
            a.source_id
        FROM public.aed a
        LEFT JOIN public.isochrone i ON i.aed_id = a.id
        WHERE a.source_id = ANY(CAST(:source_ids AS text[]))
        GROUP BY a.source_id
        HAVING
            COUNT(i.minutes) <> 2
            OR COUNT(i.minutes) FILTER (WHERE i.minutes = 2) <> 1
            OR COUNT(i.minutes) FILTER (WHERE i.minutes = 4) <> 1
    ) AS invalid_pairs
""")


def validate_geometry(geometry: dict, serial: str) -> None:
    """Polygon/MultiPolygon 좌표 배열의 기본 구조를 검사한다."""

    if not isinstance(geometry, dict):
        raise ValueError(f"{serial}: Geometry가 객체가 아닙니다.")

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Polygon":
        polygons = [coordinates]
    elif geometry_type == "MultiPolygon":
        polygons = coordinates
    else:
        raise ValueError(
            f"{serial}: 지원하지 않는 Geometry 유형: {geometry_type}"
        )

    if not isinstance(polygons, list) or not polygons:
        raise ValueError(f"{serial}: Geometry 좌표가 비어 있습니다.")

    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError(f"{serial}: Polygon 구조가 잘못되었습니다.")

        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4:
                raise ValueError(f"{serial}: 유효하지 않은 LinearRing입니다.")

            points = []

            for position in ring:
                if not isinstance(position, list) or len(position) < 2:
                    raise ValueError(f"{serial}: 잘못된 좌표 형식입니다.")

                lon, lat = position[:2]

                if (
                    isinstance(lon, bool)
                    or isinstance(lat, bool)
                    or not isinstance(lon, (int, float))
                    or not isinstance(lat, (int, float))
                    or not math.isfinite(lon)
                    or not math.isfinite(lat)
                    or not -180 <= lon <= 180
                    or not -90 <= lat <= 90
                ):
                    raise ValueError(
                        f"{serial}: 유효하지 않은 좌표: {position}"
                    )

                points.append(position[:2])

            if points[0] != points[-1]:
                raise ValueError(f"{serial}: LinearRing이 닫혀 있지 않습니다.")


def load_isochrones() -> tuple[list[dict], set[str]]:
    """JSONL을 읽고 AED별 2분·4분 영역을 검증한다."""

    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Isochrone 파일이 없습니다: {INPUT_PATH}")

    rows = []
    source_ids = set()

    with INPUT_PATH.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{line_number}행: JSON 형식 오류"
                ) from exc

            if record.get("status") != "success":
                raise ValueError(f"{line_number}행: 성공하지 않은 레코드입니다.")

            aed = record.get("aed") or {}
            serial = str(aed.get("serialSeq") or "").strip()

            if not serial:
                raise ValueError(f"{line_number}행: AED 식별자가 없습니다.")

            if serial in source_ids:
                raise ValueError(f"중복 AED 식별자: {serial}")

            source_ids.add(serial)

            geojson = record.get("geojson") or {}

            if geojson.get("type") != "FeatureCollection":
                raise ValueError(f"{serial}: FeatureCollection 형식이 아닙니다.")

            features = geojson.get("features")

            if not isinstance(features, list) or len(features) != 2:
                raise ValueError(f"{serial}: Feature 개수가 2개가 아닙니다.")

            contour_counts = Counter()

            for feature in features:
                properties = feature.get("properties") or {}

                if properties.get("aed_serial_seq") != serial:
                    raise ValueError(f"{serial}: Feature 식별자가 일치하지 않습니다.")

                try:
                    contour = float(properties["contour"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"{serial}: Contour 값 오류") from exc

                if contour not in EXPECTED_CONTOURS:
                    raise ValueError(f"{serial}: 허용되지 않은 Contour: {contour}")

                contour_counts[contour] += 1

                geometry = feature.get("geometry")
                validate_geometry(geometry, serial)

                rows.append({
                    "source_id": serial,
                    "minutes": int(contour),
                    "geojson": json.dumps(
                        geometry,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                })

            if contour_counts != Counter({2.0: 1, 4.0: 1}):
                raise ValueError(f"{serial}: 2분·4분 영역 구성이 잘못되었습니다.")

    if not source_ids:
        raise ValueError("JSONL에 AED 등시선이 없습니다.")

    # AED마다 2분·4분 두 개
    if len(rows) != len(source_ids) * 2:
        raise ValueError(
            f"Isochrone 개수 불일치: {len(rows)} / 예상 {len(source_ids) * 2}"
        )

    return rows, source_ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AED Isochrone을 PostGIS에 적재"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 DB에 적재합니다. 미입력 시 미리보기만 수행합니다.",
    )
    args = parser.parse_args()

    engine = get_engine()

    try:
        rows, source_ids = load_isochrones()
        sorted_ids = sorted(source_ids)

        with engine.connect() as conn:
            db_aed_count = conn.execute(
                text("SELECT COUNT(*) FROM public.aed")
            ).scalar_one()

            db_rows = conn.execute(
                text("""
                    SELECT id, source_id
                    FROM public.aed
                    WHERE source_id = ANY(CAST(:source_ids AS text[]))
                """),
                {"source_ids": sorted_ids},
            ).mappings().all()

            id_map = {
                row["source_id"]: row["id"]
                for row in db_rows
            }

            db_ids = set(id_map)

            # 등시선 JSONL과 DB의 AED 수가 같아야 한다 (등시선 실패 AED가 있으면 여기서 멈춘다)
            if db_aed_count != len(source_ids):
                raise RuntimeError(
                    f"DB AED 건수 불일치: DB {db_aed_count} / JSONL {len(source_ids)}"
                )

            if db_ids != source_ids:
                raise RuntimeError(
                    "JSONL과 DB의 AED 식별자가 일치하지 않습니다. "
                    f"DB 누락: {len(source_ids - db_ids)}, "
                    f"JSONL 누락: {len(db_ids - source_ids)}"
                )

            existing_count = conn.execute(
                text("""
                    SELECT COUNT(*)
                    FROM public.isochrone i
                    JOIN public.aed a ON a.id = i.aed_id
                    WHERE a.source_id = ANY(CAST(:source_ids AS text[]))
                """),
                {"source_ids": sorted_ids},
            ).scalar_one()

        print("=" * 50)
        print("Isochrone DB 적재 사전 검사")
        print("=" * 50)
        print(f"JSONL 고유 AED: {len(source_ids):,}건")
        print(f"DB AED: {db_aed_count:,}건")
        print(f"식별자 일치: {len(db_ids):,}건")
        print(f"적재 대상 영역: {len(rows):,}개")
        print(f"기존 Isochrone: {existing_count:,}개")

        if not args.apply:
            print("\n미리보기 완료. DB는 변경하지 않았습니다.")
            print("실제 적재하려면 --apply 옵션을 사용하세요.")
            return

        insert_rows = [
            {
                "aed_id": id_map[row["source_id"]],
                "minutes": row["minutes"],
                "geojson": row["geojson"],
            }
            for row in rows
        ]

        # 모든 영역을 하나의 트랜잭션으로 적재한다.
        with engine.begin() as conn:
            conn.execute(UPSERT_SQL, insert_rows)

            verification = conn.execute(
                VERIFY_SQL,
                {"source_ids": sorted_ids},
            ).mappings().one()

            invalid_pairs = conn.execute(
                PAIR_VERIFY_SQL,
                {"source_ids": sorted_ids},
            ).scalar_one()

            if verification["total"] != len(source_ids) * 2:
                raise RuntimeError(
                    f"Isochrone 전체 건수 오류: {dict(verification)}"
                )

            if verification["unique_aed"] != len(source_ids):
                raise RuntimeError("Isochrone AED 식별자 개수가 일치하지 않습니다.")

            if (
                verification["two_minutes"] != len(source_ids)
                or verification["four_minutes"] != len(source_ids)
            ):
                raise RuntimeError("2분·4분 영역 개수가 일치하지 않습니다.")

            invalid_counts = {
                key: verification[key]
                for key in (
                    "null_geom",
                    "wrong_srid",
                    "wrong_type",
                    "invalid_geom",
                    "empty_geom",
                )
            }

            if any(invalid_counts.values()) or invalid_pairs:
                raise RuntimeError(
                    "Isochrone 공간정보 검증 실패: "
                    f"{invalid_counts}, 잘못된 AED별 영역 구성={invalid_pairs}"
                )

        print("\n===== Isochrone 적재 완료 =====")
        print(f"적재·갱신 영역: {len(rows):,}개")
        print(f"대상 AED: {verification['unique_aed']:,}개")
        print(f"2분 영역: {verification['two_minutes']:,}개")
        print(f"4분 영역: {verification['four_minutes']:,}개")
        print("NULL Geometry: 0")
        print("좌표계 오류: 0")
        print("Geometry 타입 오류: 0")
        print("유효하지 않은 Geometry: 0")
        print("빈 Geometry: 0")
        print("AED별 영역 구성 오류: 0")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
