"""FastAPI 앱. 실행: uvicorn api.main:app --reload --port 8000 → http://localhost:8000/docs"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import aeds, layers, meta, nearest, reports, stats

app = FastAPI(title="AED 접근성 분석 API")

# 지도 데이터(GeoJSON)는 1MB를 넘기도 해서 압축해 보낸다 (서울 전체 AED, 도달 구역 다각형)
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(meta.router)
app.include_router(aeds.router)
app.include_router(stats.router)
app.include_router(layers.router)
app.include_router(nearest.router)
app.include_router(reports.router)

# 개발용: 화면 파일(web/)도 같은 주소에서 제공한다 → http://localhost:8000/
# API 라우터를 모두 등록한 뒤에 붙여야 /api/... 가 화면 파일에 가려지지 않는다.
# Docker에서는 Nginx가 web/을 제공하고 API 이미지에는 web/이 없으므로 건너뛴다.
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
