"""정제된 AED CSV를 PostGIS public.aed 테이블에 적재한다."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from common import get_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data/clean/aed.csv"

DAY_TIME_FIELDS = {
    "mon": ("monSttTme", "monEndTme"),
    "tue": ("tueSttTme", "tueEndTme"),
    "wed": ("wedSttTme", "wedEndTme"),
    "thu": ("thuSttTme", "thuEndTme"),
    "fri": ("friSttTme", "friEndTme"),
    "sat": ("satSttTme", "satEndTme"),
    "sun": ("sunSttTme", "sunEndTme"),
    "hol": ("holSttTme", "holEndTme"),
}

SUNDAY_FLAG_FIELDS = (
    "sunFrtYon",
    "sunScdYon",
    "sunThiYon",
    "sunFurYon",
    "sunFifYon",
)

SPATIAL_LOOKUP_SQL = text("""
    SELECT name
    FROM public.district
    WHERE ST_Covers(
        geom,
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
    )
    ORDER BY name
""")

UPSERT_SQL = text("""
    INSERT INTO public.aed (
        source_id,
        place,
        address,
        gu,
        hours,
        geom
    )
    VALUES (
        :source_id,
        :place,
        :address,
        :gu,
        CAST(:hours AS jsonb),
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
    )
    ON CONFLICT (source_id)
    DO UPDATE SET
        place = EXCLUDED.place,
        address = EXCLUDED.address,
        gu = EXCLUDED.gu,
        hours = EXCLUDED.hours,
        geom = EXCLUDED.geom
""")

# 이번 CSV에 없는 AED (seed의 TEST*, 원본에서 사라진 AED 등)
STALE_SQL = text("""
    SELECT
        COUNT(*) AS stale_count,
        COUNT(*) FILTER (
            WHERE EXISTS (SELECT 1 FROM public.report r WHERE r.aed_id = a.id)
        ) AS stale_with_reports,
        COALESCE(
            (ARRAY_AGG(a.source_id ORDER BY a.source_id))[1:5],
            ARRAY[]::text[]
        ) AS examples
    FROM public.aed a
    WHERE NOT (a.source_id = ANY(CAST(:source_ids AS text[])))
""")

# isochrone은 ON DELETE CASCADE로 함께 지워진다
DELETE_STALE_SQL = text("""
    DELETE FROM public.aed
    WHERE NOT (source_id = ANY(CAST(:source_ids AS text[])))
""")

VERIFY_SQL = text("""
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE a.geom IS NULL) AS null_geom,
        COUNT(*) FILTER (WHERE ST_SRID(a.geom) <> 4326) AS wrong_srid,
        COUNT(*) FILTER (
            WHERE GeometryType(a.geom) <> 'POINT'
        ) AS wrong_type,
        COUNT(*) FILTER (
            WHERE NOT ST_IsValid(a.geom)
        ) AS invalid_geom,
        COUNT(*) FILTER (
            WHERE d.name IS NULL
        ) AS invalid_gu
    FROM public.aed a
    LEFT JOIN public.district d ON d.name = a.gu
    WHERE a.source_id = ANY(CAST(:source_ids AS text[]))
""")


def parse_bool(value: str | None) -> bool:
    """CSV의 Boolean 문자열을 bool로 변환한다."""

    raw = (value or "").strip().lower()

    if raw in ("true", "1", "yes", "y"):
        return True

    if raw in ("false", "0", "no", "n", ""):
        return False

    raise ValueError(f"잘못된 Boolean 값: {value!r}")


def build_hours(row: dict[str, str]) -> dict:
    """요일별 운영시간과 일요일 예외 플래그를 JSON 구조로 만든다."""

    hours = {}

    for day, (start_field, end_field) in DAY_TIME_FIELDS.items():
        start = (row.get(start_field) or "").strip()
        end = (row.get(end_field) or "").strip()

        # 기존 DB 설계에 맞춰 [시작시간, 종료시간] 배열 유지
        hours[day] = [start, end]

    # 첫째~다섯째 일요일이라는 필드 해석을 확정하지 않고
    # 원본 필드명과 값을 그대로 보존한다.
    hours["sun_flags"] = {
        field: (row.get(field) or "").strip().upper()
        for field in SUNDAY_FLAG_FIELDS
    }

    hours["_metadata"] = {
        "operating_hours_review_required": parse_bool(
            row.get("operating_hours_review_required")
        ),
        "quality_flags": (row.get("quality_flags") or "").strip(),
    }

    return hours


def resolve_district(
    address: str,
    district_names: set[str],
    conn,
    *,
    lat: float,
    lon: float,
) -> tuple[str, str]:
    """주소에서 자치구를 찾고, 실패하면 좌표로 판정한다."""

    matches = [
        name for name in district_names
        if name in address
    ]

    if len(matches) == 1:
        return matches[0], "address"

    if len(matches) > 1:
        raise ValueError(
            f"주소에서 자치구가 여러 개 검출되었습니다: {address!r} / {matches}"
        )

    spatial_matches = conn.execute(
        SPATIAL_LOOKUP_SQL,
        {"lat": lat, "lon": lon},
    ).scalars().all()

    if len(spatial_matches) != 1:
        raise ValueError(
            "주소에서 자치구를 찾지 못했고 좌표 판정도 확정할 수 없습니다: "
            f"{address!r} / 좌표=({lat}, {lon}) / "
            f"공간 판정={spatial_matches}"
        )

    return spatial_matches[0], "spatial"


def load_records(conn) -> tuple[list[dict], Counter, Counter, set[str]]:
    """CSV를 읽고 검증된 DB 적재 레코드를 만든다."""

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"AED CSV가 없습니다: {CSV_PATH}")

    district_names = set(
        conn.execute(
            text("SELECT name FROM public.district")
        ).scalars().all()
    )

    if len(district_names) != 25:
        raise RuntimeError(
            f"district 테이블에 자치구 25개가 필요합니다: {len(district_names)}"
        )

    records = []
    gu_counts = Counter()
    gu_resolution_counts = Counter()
    seen_ids = set()

    with CSV_PATH.open(
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        required_fields = {
            "serialSeq",
            "buildAddress",
            "buildPlace",
            "wgs84Lat",
            "wgs84Lon",
        }

        if not required_fields.issubset(set(reader.fieldnames or [])):
            raise ValueError("AED CSV의 필수 컬럼이 부족합니다.")

        for line_number, row in enumerate(reader, start=2):
            serial = (row.get("serialSeq") or "").strip()

            if not serial:
                raise ValueError(f"{line_number}행: serialSeq 누락")

            if serial in seen_ids:
                raise ValueError(f"중복 serialSeq: {serial}")

            seen_ids.add(serial)

            try:
                lat = float(row["wgs84Lat"])
                lon = float(row["wgs84Lon"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{serial}: 좌표가 숫자가 아닙니다."
                ) from exc

            if not math.isfinite(lat) or not math.isfinite(lon):
                raise ValueError(f"{serial}: 좌표가 유한한 숫자가 아닙니다.")

            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"{serial}: 좌표 범위를 벗어났습니다.")

            address = (row.get("buildAddress") or "").strip()
            place = (row.get("buildPlace") or "").strip()

            gu, resolution_method = resolve_district(
                address,
                district_names,
                conn,
                lat=lat,
                lon=lon,
            )

            hours = build_hours(row)

            records.append({
                "source_id": serial,
                "place": place or None,
                "address": address or None,
                "gu": gu,
                "hours": json.dumps(
                    hours,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "lat": lat,
                "lon": lon,
            })

            gu_counts[gu] += 1
            gu_resolution_counts[resolution_method] += 1

    if len(records) != len(seen_ids):
        raise RuntimeError("AED 레코드 수와 고유 식별자 수가 다릅니다.")

    return records, gu_counts, gu_resolution_counts, seen_ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="정제된 AED CSV를 PostGIS에 적재"
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 DB에 적재합니다. 미입력 시 미리보기만 수행합니다.",
    )

    parser.add_argument(
        "--prune",
        action="store_true",
        help="이번 CSV에 없는 AED를 삭제합니다 (--apply와 함께). "
             "등시선도 함께 지워지고, 오류 제보가 연결된 AED가 있으면 멈춥니다.",
    )

    args = parser.parse_args()

    if args.prune and not args.apply:
        parser.error("--prune은 --apply와 함께 사용합니다.")

    engine = get_engine()

    try:
        with engine.connect() as conn:
            records, gu_counts, resolution_counts, source_ids = (
                load_records(conn)
            )

            existing_count = conn.execute(
                text("SELECT COUNT(*) FROM public.aed")
            ).scalar_one()

            stale = conn.execute(
                STALE_SQL,
                {"source_ids": sorted(source_ids)},
            ).mappings().one()

            print("=" * 50)
            print("AED 데이터 적재 사전 검사")
            print("=" * 50)
            print(f"CSV AED 건수: {len(records):,}")
            print(f"고유 source_id: {len(source_ids):,}")
            print(f"기존 DB AED 건수: {existing_count:,}")
            print(f"주소 기반 자치구 판정: {resolution_counts['address']:,}")
            print(f"좌표 기반 자치구 판정: {resolution_counts['spatial']:,}")
            print(
                f"CSV에 없는 기존 AED: {stale['stale_count']:,}"
                + (f" (예: {', '.join(stale['examples'])})" if stale["stale_count"] else "")
            )

            print("\n===== 자치구별 AED 건수 =====")
            for gu, count in sorted(gu_counts.items()):
                print(f"{gu}: {count:,}건")

            if not records:
                raise RuntimeError("CSV에 적재할 AED가 없습니다.")

            if stale["stale_count"] and not args.prune:
                print(
                    "\n⚠ CSV에 없는 AED가 DB에 남습니다. "
                    "이후 단계의 건수 검사에서 멈출 수 있으니 --prune으로 정리하세요."
                )

            if not args.apply:
                print("\n미리보기 완료. DB는 변경하지 않았습니다.")
                print("실제 적재하려면 --apply 옵션을 사용하세요.")
                return

        # 모든 AED를 한 트랜잭션으로 적재한다.
        with engine.begin() as conn:
            deleted = 0
            if args.prune:
                current_stale = conn.execute(
                    STALE_SQL,
                    {"source_ids": sorted(source_ids)},
                ).mappings().one()

                # 오류 제보(report)가 달린 AED를 지우면 제보 기록이 깨지므로 멈춘다
                if current_stale["stale_with_reports"]:
                    raise RuntimeError(
                        f"삭제 대상 AED 중 {current_stale['stale_with_reports']}건에 "
                        "오류 제보가 연결돼 있어 정리하지 않습니다. report를 먼저 확인하세요."
                    )

                deleted = conn.execute(
                    DELETE_STALE_SQL,
                    {"source_ids": sorted(source_ids)},
                ).rowcount

            conn.execute(UPSERT_SQL, records)

            verification = conn.execute(
                VERIFY_SQL,
                {"source_ids": sorted(source_ids)},
            ).mappings().one()

            db_ids = set(
                conn.execute(
                    text("""
                        SELECT source_id
                        FROM public.aed
                        WHERE source_id = ANY(CAST(:source_ids AS text[]))
                    """),
                    {"source_ids": sorted(source_ids)},
                ).scalars().all()
            )

            if db_ids != source_ids:
                raise RuntimeError("DB source_id와 원본 CSV 식별자가 일치하지 않습니다.")

            if verification["total"] != len(records):
                raise RuntimeError(
                    f"적재 건수 불일치: {dict(verification)}"
                )

            if args.prune:
                total_aed = conn.execute(
                    text("SELECT COUNT(*) FROM public.aed")
                ).scalar_one()
                if total_aed != len(records):
                    raise RuntimeError(
                        f"정리 후 AED 수({total_aed})가 CSV({len(records)})와 다릅니다."
                    )

            invalid_counts = {
                key: verification[key]
                for key in (
                    "null_geom",
                    "wrong_srid",
                    "wrong_type",
                    "invalid_geom",
                    "invalid_gu",
                )
            }

            if any(invalid_counts.values()):
                raise RuntimeError(
                    f"공간 데이터 검증 실패: {invalid_counts}"
                )

        print("\n===== AED 적재 완료 =====")
        print(f"적재·갱신 AED: {len(records):,}건")
        if args.prune:
            print(f"CSV에 없어 삭제한 AED: {deleted:,}건 (등시선 함께 삭제)")
        print("source_id 중복: 0")
        print("좌표계 오류: 0")
        print("잘못된 Geometry: 0")
        print("자치구 외래키 오류: 0")

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
