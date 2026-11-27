"""FastAPI 앱. 실행: uvicorn api.main:app --reload --port 8000 → http://localhost:8000/docs"""

from fastapi import FastAPI

from api.routers import aeds, layers, meta, nearest, reports, stats

app = FastAPI(title="AED 접근성 분석 API")

app.include_router(meta.router)
app.include_router(aeds.router)
app.include_router(stats.router)
app.include_router(layers.router)
app.include_router(nearest.router)
app.include_router(reports.router)
