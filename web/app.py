"""FastAPI 애플리케이션 팩토리."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.routes import backtest, charts, results


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinAgent Web UI",
        description="멀티모달 AI 트레이딩 에이전트 백테스팅 대시보드",
        version="1.0.0",
    )

    app.include_router(backtest.router)
    app.include_router(results.router)
    app.include_router(charts.router)

    # static/ 디렉토리를 루트로 마운트 (index.html 자동 서빙)
    app.mount("/", StaticFiles(directory="web/static", html=True), name="static")

    return app


app = create_app()
