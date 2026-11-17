"""FastAPI 앱. 실행: uvicorn api.main:app --reload --port 8000 → http://localhost:8000/docs"""

from fastapi import FastAPI

from api.routers import meta  # 다음: aeds, stats, layers, nearest, reports

app = FastAPI(title="AED 접근성 분석 API")

app.include_router(meta.router)
