#!/usr/bin/env python3
"""Generate 2-minute and 4-minute pedestrian isochrones for Seoul AEDs."""

import argparse
import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def resolve_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_config():
    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    required_sections = {"paths", "valhalla", "analysis"}
    missing = required_sections - set(config or {})
    if missing:
        raise ValueError(f"config.yaml 필수 항목 누락: {sorted(missing)}")

    return config


def read_aed_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"AED CSV가 없습니다: {path}")

    required_columns = {"serialSeq", "wgs84Lat", "wgs84Lon"}

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        missing = required_columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"AED CSV 필수 컬럼 누락: {sorted(missing)}")

        rows = list(reader)

    # serialSeq 기준으로 중복 제거
    unique_rows = []
    seen = set()
    duplicate_count = 0

    for row in rows:
        serial = (row.get("serialSeq") or "").strip()

        if not serial:
            continue

        if serial in seen:
            duplicate_count += 1
            continue

        seen.add(serial)
        unique_rows.append(row)

    return rows, unique_rows, duplicate_count


def load_processed_ids(path):
    """이전에 성공한 AED는 재실행 시 건너뛴다."""
    processed = set()

    if not path.exists():
        return processed

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            try:
                record = json.loads(line)
                if record.get("status") == "success":
                    serial = record.get("aed", {}).get("serialSeq")
                    if serial:
                        processed.add(str(serial))
            except (json.JSONDecodeError, AttributeError):
                logging.warning(
                    "기존 결과의 JSON 오류: %s행", line_number
                )

    return processed


def parse_bool(value):
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def validate_geojson(data, contours):
    if data.get("type") != "FeatureCollection":
        raise ValueError("응답이 GeoJSON FeatureCollection 형식이 아닙니다.")

    features = data.get("features", [])
    if not features:
        raise ValueError("Isochrone 영역이 생성되지 않았습니다.")

    expected = {round(float(value), 4) for value in contours}
    actual = set()

    for feature in features:
        geometry = feature.get("geometry") or {}
        geometry_type = geometry.get("type")

        if geometry_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"예상하지 못한 도형 유형: {geometry_type}")

        properties = feature.get("properties") or {}
        contour = properties.get("contour")

        if contour is not None:
            actual.add(round(float(contour), 4))

    missing = expected - actual
    if missing:
        raise ValueError(f"접근 영역 누락: {sorted(missing)}분 등고선")

    return features


def request_isochrone(session, url, row, config):
    lat = float(row["wgs84Lat"])
    lon = float(row["wgs84Lon"])

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"유효하지 않은 좌표: lat={lat}, lon={lon}")

    valhalla = config["valhalla"]
    analysis = config["analysis"]
    contours = analysis["contours_minutes"]

    payload = {
        "locations": [{"lat": lat, "lon": lon}],
        "costing": valhalla.get("costing", "pedestrian"),
        "costing_options": {
            "pedestrian": {
                "walking_speed": float(
                    valhalla["walking_speed_kmh"]
                )
            }
        },
        "contours": [
            {"time": float(minutes)}
            for minutes in contours
        ],
        "polygons": bool(valhalla.get("polygons", True))
    }

    response = session.post(
        url,
        json=payload,
        timeout=int(valhalla.get("timeout_seconds", 120))
    )
    response.raise_for_status()

    data = response.json()
    validate_geojson(data, contours)

    return data, lat, lon


def save_failures(path, failures):
    columns = [
        "serialSeq",
        "buildAddress",
        "lat",
        "lon",
        "attempts",
        "error"
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(failures)


def main():
    parser = argparse.ArgumentParser(
        description="AED pedestrian isochrone generator"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="설정의 테스트 개수 제한 없이 전체 AED 처리"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="이번 실행에서 처리할 AED 개수 지정"
    )
    args = parser.parse_args()

    if args.all and args.limit is not None:
        parser.error("--all과 --limit은 함께 사용할 수 없습니다.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    config = load_config()

    paths = config["paths"]
    valhalla = config["valhalla"]
    analysis = config["analysis"]

    input_path = resolve_path(paths["aed_csv"])
    output_path = resolve_path(paths["output_jsonl"])
    failure_path = resolve_path(paths["failures_csv"])
    summary_path = resolve_path(paths["summary_json"])

    output_path.parent.mkdir(parents=True, exist_ok=True)

    contours = analysis["contours_minutes"]
    if not contours or any(float(value) <= 0 for value in contours):
        raise ValueError("contours_minutes는 양수 목록이어야 합니다.")

    if args.all:
        limit = None
        run_mode = "all"
    elif args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit은 1 이상의 정수여야 합니다.")
        limit = args.limit
        run_mode = "custom"
    else:
        limit = analysis.get("test_limit")
        run_mode = "test"

    raw_rows, unique_rows, duplicate_count = read_aed_csv(input_path)

    resume = bool(analysis.get("resume", True))

    if not resume and output_path.exists():
        output_path.unlink()

    processed = load_processed_ids(output_path) if resume else set()

    selected_rows = (
        unique_rows if limit is None else unique_rows[:limit]
    )

    base_url = valhalla["base_url"].rstrip("/")
    endpoint = valhalla.get("isochrone_endpoint", "/isochrone")
    request_url = base_url + "/" + endpoint.lstrip("/")

    max_retries = int(analysis.get("max_retries", 3))
    retry_delay = float(analysis.get("retry_delay_seconds", 2))
    request_interval = float(
        analysis.get("request_interval_seconds", 0.1)
    )

    failures = []
    attempted = 0
    succeeded = 0
    skipped = 0
    run_started = now_iso()

    logging.info("========== AED Isochrone 분석 시작 ==========")
    logging.info("실행 모드: %s", run_mode)
    logging.info("원본 데이터: %d건", len(raw_rows))
    logging.info("중복 제거 후: %d건", len(unique_rows))
    logging.info("중복 제거 건수: %d건", duplicate_count)
    logging.info("이번 실행 대상: %d건", len(selected_rows))
    logging.info("기존 성공 데이터: %d건", len(processed))
    logging.info("Valhalla 주소: %s", request_url)
    logging.info("접근 시간: %s분", contours)
    logging.info("보행 속도: %s km/h", valhalla["walking_speed_kmh"])

    with requests.Session() as session:
        session.headers.update({"Content-Type": "application/json"})

        with output_path.open("a", encoding="utf-8") as output_file:
            for index, row in enumerate(selected_rows, start=1):
                serial = (row.get("serialSeq") or "").strip()

                if not serial:
                    continue

                if serial in processed:
                    skipped += 1
                    continue

                attempted += 1
                last_error = None
                geojson = None
                lat = None
                lon = None
                attempts_used = 0

                for attempt in range(1, max_retries + 2):
                    attempts_used = attempt

                    try:
                        geojson, lat, lon = request_isochrone(
                            session, request_url, row, config
                        )
                        last_error = None
                        break

                    except (requests.RequestException, ValueError) as error:
                        last_error = str(error)

                        if attempt <= max_retries:
                            logging.warning(
                                "[%d/%d] AED %s 요청 실패 (%d/%d): %s",
                                index,
                                len(selected_rows),
                                serial,
                                attempt,
                                max_retries + 1,
                                last_error
                            )
                            time.sleep(retry_delay)

                if geojson is None:
                    failures.append({
                        "serialSeq": serial,
                        "buildAddress": row.get("buildAddress", ""),
                        "lat": row.get("wgs84Lat", ""),
                        "lon": row.get("wgs84Lon", ""),
                        "attempts": attempts_used,
                        "error": last_error or "알 수 없는 오류"
                    })

                    logging.error(
                        "[%d/%d] 실패: AED %s",
                        index, len(selected_rows), serial
                    )

                else:
                    # 결과 GeoJSON에 AED 식별 정보를 추가한다.
                    for feature in geojson["features"]:
                        properties = feature.setdefault("properties", {})
                        properties["aed_serial_seq"] = serial
                        properties["aed_lat"] = lat
                        properties["aed_lon"] = lon
                        properties["aed_build_address"] = (
                            row.get("buildAddress", "")
                        )
                        properties["aed_build_place"] = (
                            row.get("buildPlace", "")
                        )
                        properties["aed_operating_hours_review_required"] = (
                            parse_bool(
                                row.get("operating_hours_review_required", "")
                            )
                        )

                    record = {
                        "status": "success",
                        "aed": {
                            "serialSeq": serial,
                            "buildAddress": row.get("buildAddress", ""),
                            "buildPlace": row.get("buildPlace", ""),
                            "lat": lat,
                            "lon": lon,
                            "operating_hours_review_required": parse_bool(
                                row.get("operating_hours_review_required", "")
                            )
                        },
                        "geojson": geojson,
                        "generated_at": now_iso()
                    }

                    output_file.write(
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            allow_nan=False
                        ) + "\n"
                    )
                    output_file.flush()

                    processed.add(serial)
                    succeeded += 1

                    logging.info(
                        "[%d/%d] 성공: AED %s / 영역 %d개",
                        index,
                        len(selected_rows),
                        serial,
                        len(geojson["features"])
                    )

                if request_interval > 0:
                    time.sleep(request_interval)

    save_failures(failure_path, failures)

    summary = {
        "run_started_at": run_started,
        "run_finished_at": now_iso(),
        "run_mode": run_mode,
        "input_file": str(input_path),
        "input_rows": len(raw_rows),
        "unique_aed_count": len(unique_rows),
        "duplicate_count": duplicate_count,
        "selected_count": len(selected_rows),
        "attempted_count": attempted,
        "success_count": succeeded,
        "failure_count": len(failures),
        "skipped_existing_success_count": skipped,
        "contours_minutes": contours,
        "walking_speed_kmh": valhalla["walking_speed_kmh"],
        "output_jsonl": str(output_path),
        "failures_csv": str(failure_path)
    }

    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)

    logging.info("========== 분석 종료 ==========")
    logging.info("이번 실행 성공: %d건", succeeded)
    logging.info("이번 실행 실패: %d건", len(failures))
    logging.info("기존 결과 건너뜀: %d건", skipped)
    logging.info("결과 파일: %s", output_path)
    logging.info("실패 파일: %s", failure_path)
    logging.info("요약 파일: %s", summary_path)


if __name__ == "__main__":
    main()
