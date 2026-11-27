FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY api/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api/ /app/api/
# 보행 속도 등 파이프라인과 같은 설정 (api/config.py가 읽는다)
COPY config.yaml /app/config.yaml
# open_now: 운영 여부·공휴일 판정을 3단계 파이프라인과 같은 코드로 한다 (api/open_now.py)
COPY pipeline/hours.py pipeline/availability.py pipeline/analyze_availability.py pipeline/common.py /app/pipeline/

# root가 아닌 사용자로 실행한다 (앱은 파일을 쓰지 않으므로 읽기 권한이면 충분하다)
RUN useradd --system --no-create-home --uid 10001 app
USER app

EXPOSE 8000

# 요청 기록은 Nginx가 쿼리·IP 없이 남긴다. uvicorn 기록에는 /api/nearest?lat=..&lon=..가
# 그대로 찍히므로 끈다 (PRD 8-6 클릭 좌표 미저장)
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
