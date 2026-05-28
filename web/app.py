"""FastAPI 애플리케이션 팩토리."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.db import init_schema
from web.routes import backtest, charts, results, review
from web.routes import auth as auth_router
from web.routes import llm_config as llm_config_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinAgent Web UI",
        description="멀티모달 AI 트레이딩 에이전트 백테스팅 대시보드",
        version="2.0.0",
    )

    init_schema()

    app.include_router(auth_router.router)
    app.include_router(llm_config_router.router)
    app.include_router(backtest.router)
    app.include_router(results.router)
    app.include_router(charts.router)
    app.include_router(review.router)

    app.mount("/", StaticFiles(directory="web/static", html=True), name="static")

    @app.on_event("startup")
    async def _recover_queued_batches() -> None:
        from web import batch_queue  # noqa: PLC0415
        batch_queue.recover_queued()

    return app


app = create_app()
