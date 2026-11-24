
import os
import json
import math
import time
import requests
import xml.etree.ElementTree as ET

from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv


# ==========================================
# 1. 프로젝트 기본 설정
# ==========================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data" / "raw"
ENV_FILE = PROJECT_ROOT / ".env"

API_URL = (
    "https://apis.data.go.kr/B552657/"
    "AEDInfoInqireService/getEgytAedManageInfoInqire"
)

REGION = "서울특별시"
NUM_OF_ROWS = 1000
TIMEOUT = 30


# ==========================================
# 2. API 인증키 불러오기
# ==========================================

load_dotenv(dotenv_path=ENV_FILE)

SERVICE_KEY = os.getenv("AED_API_KEY")

if not SERVICE_KEY:
    raise RuntimeError(
        ".env 파일에서 AED_API_KEY를 찾을 수 없습니다. (공공데이터포털 Decoding 키)"
    )


# ==========================================
# 3. XML 태그 이름 처리
# ==========================================

def get_tag_name(tag):
    """XML 태그에서 네임스페이스를 제거합니다."""
    return tag.split("}")[-1]


def get_value(root, target):
    """XML에서 특정 태그의 값을 가져옵니다."""
    for node in root.iter():
        if get_tag_name(node.tag) == target:
            return (node.text or "").strip()

    return None


# ==========================================
# 4. XML에서 AED 데이터 추출
# ==========================================

def extract_items(root):
    """XML의 item 데이터를 Python 딕셔너리로 변환합니다."""

    records = []

    for node in root.iter():

        if get_tag_name(node.tag) != "item":
            continue

        record = {}

        for child in node:
            field_name = get_tag_name(child.tag)
            field_value = (child.text or "").strip()

            record[field_name] = field_value

        records.append(record)

    return records


# ==========================================
# 5. API 페이지 요청
# ==========================================

def fetch_page(page_no):
    """서울시 AED 데이터의 지정된 페이지를 요청합니다."""

    params = {
        "serviceKey": SERVICE_KEY,
        "Q0": REGION,
        "pageNo": page_no,
        "numOfRows": NUM_OF_ROWS
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            timeout=TIMEOUT
        )

        response.raise_for_status()

    except requests.exceptions.RequestException as error:
        raise RuntimeError(
            f"{page_no}페이지 API 요청 실패: "
            f"{type(error).__name__}"
        ) from error

    try:
        root = ET.fromstring(response.content)

    except ET.ParseError as error:
        raise RuntimeError(
            f"{page_no}페이지 XML 파싱 실패"
        ) from error

    result_code = get_value(root, "resultCode")
    result_message = get_value(root, "resultMsg")

    if result_code != "00":
        raise RuntimeError(
            f"API 오류: {result_code} / {result_message}"
        )

    total_count_text = get_value(root, "totalCount")

    if not total_count_text:
        raise RuntimeError("API 응답에 totalCount가 없습니다.")

    total_count = int(total_count_text)

    records = extract_items(root)

    return total_count, records


# ==========================================
# 6. 전체 서울 AED 데이터 수집
# ==========================================

def collect_aed():

    print("=" * 50)
    print("서울시 AED 데이터 수집 시작")
    print("=" * 50)

    all_records = []

    total_count = None
    total_pages = None

    page_no = 1

    while True:

        print(f"\n[{page_no}/{total_pages or '?'}] 페이지 요청 중...")

        current_total, records = fetch_page(page_no)

        if total_count is None:

            total_count = current_total

            total_pages = math.ceil(
                total_count / NUM_OF_ROWS
            )

            print(f"서울시 AED 전체 데이터: {total_count:,}건")
            print(f"예상 페이지 수: {total_pages}페이지")

        elif current_total != total_count:

            raise RuntimeError(
                "수집 도중 API의 전체 데이터 건수가 변경됐습니다."
            )

        if not records:
            raise RuntimeError(
                f"{page_no}페이지에서 데이터가 반환되지 않았습니다."
            )

        all_records.extend(records)

        print(f"현재 페이지 수집: {len(records):,}건")
        print(f"누적 수집: {len(all_records):,}건")

        if page_no >= total_pages:
            break

        page_no += 1

        # API에 연속 요청을 너무 빠르게 보내지 않도록 대기
        time.sleep(0.2)

    if len(all_records) != total_count:
        raise RuntimeError(
            f"수집 건수 불일치: "
            f"예상 {total_count}, 실제 {len(all_records)}"
        )

    return all_records, total_count


# ==========================================
# 7. 원본 JSON 저장
# ==========================================

def save_json(records, total_count):

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    collected_at = datetime.now().astimezone()

    timestamp = collected_at.strftime(
        "%Y%m%d_%H%M%S"
    )

    output_path = DATA_DIR / f"aed_seoul_{timestamp}.json"

    result = {
        "metadata": {
            "source": "국립중앙의료원 전국 AED 정보 조회 서비스",
            "region": REGION,
            "collected_at": collected_at.isoformat(),
            "total_count": total_count,
            "collected_count": len(records),
            "num_of_rows": NUM_OF_ROWS
        },
        "items": records
    }

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            result,
            file,
            ensure_ascii=False,
            indent=2
        )

    print("\n" + "=" * 50)
    print("AED 데이터 수집 완료")
    print("=" * 50)

    print(f"전체 데이터: {total_count:,}건")
    print(f"수집 데이터: {len(records):,}건")
    print(f"저장 위치: {output_path}")


# ==========================================
# 8. 프로그램 실행
# ==========================================

def main():

    records, total_count = collect_aed()

    save_json(
        records,
        total_count
    )


if __name__ == "__main__":
    main()
