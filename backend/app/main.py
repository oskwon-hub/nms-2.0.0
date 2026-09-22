"""NMS Core Server 진입점 (3장 전체 아키텍처의 REST API / Web UI 서빙 계층)."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import config
from app.api import api_router
from app.db import init_db
from app.discovery.scheduler import AutoDiscoveryScheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = None
    if config.AUTO_DISCOVERY_ENABLED:
        scheduler = AutoDiscoveryScheduler(
            config.AUTO_DISCOVERY_INTERVAL_SECONDS,
            config.AUTO_DISCOVERY_INITIAL_DELAY_SECONDS,
        )
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()


app = FastAPI(title="NMS Core Server", version="1.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 배포 스크립트가 프론트엔드 빌드 결과물을 app/static에 복사하면 동일 프로세스가
# API와 Web UI를 함께 서빙한다 (15.1절 '단일 파일/단순 배포' 원칙의 연장).
if config.STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")
