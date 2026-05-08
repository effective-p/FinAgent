"""백테스트 시작 및 SSE 스트리밍 라우트."""
from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from web.job_store import BacktestJob, create_job, get_job
from web.schemas import BacktestRequest, JobCreatedResponse


def _get_run_backtest():
    """지연 임포트 — finagent 의존성이 준비된 환경에서만 사용된다."""
    from finagent.main import run_backtest  # noqa: PLC0415
    return run_backtest

router = APIRouter()


def _make_step_callback(job: BacktestJob, loop: asyncio.AbstractEventLoop):
    """동기 step_callback 생성 — 파이프라인 각 단계 시작 시 SSE로 push (재연결 리플레이 제외)."""

    def callback(step: str):
        event = {"type": "step", "step": step}
        # 일시적 시각 정보이므로 job.events에 누적하지 않음 (재연결 시 리플레이 불필요)
        loop.call_soon_threadsafe(job.queue.put_nowait, event)

    return callback


def _make_progress_callback(job: BacktestJob, loop: asyncio.AbstractEventLoop):
    """동기 progress_callback 생성 — 스레드에서 안전하게 asyncio queue에 push."""

    def callback(day_index: int, total_days: int, current_date, action: str, reasoning: str):
        event = {
            "type": "progress",
            "day": day_index,
            "total": total_days,
            "date": str(current_date),
            "action": action,
            "reasoning": reasoning,
            "pct": round(day_index / total_days * 100, 1),
        }
        job.events.append(event)
        loop.call_soon_threadsafe(job.queue.put_nowait, event)

    return callback


@router.post("/api/backtest", response_model=JobCreatedResponse)
async def start_backtest(req: BacktestRequest):
    """백테스트 Job을 생성하고 즉시 job_id를 반환한다. 실제 실행은 백그라운드."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")

    job = create_job()
    job_dir = f"job_data/{job.job_id}"
    chart_dir = f"{job_dir}/charts"
    db_path = f"{job_dir}/portfolio.db"
    memory_dir = f"{job_dir}/memory_db"
    os.makedirs(chart_dir, exist_ok=True)

    loop = asyncio.get_event_loop()
    progress_cb = _make_progress_callback(job, loop)
    step_cb = _make_step_callback(job, loop)

    async def run_in_thread():
        job.status = "running"
        try:
            run_backtest = _get_run_backtest()
            result = await loop.run_in_executor(
                None,
                lambda: run_backtest(
                    symbol=req.symbol,
                    stock_name=req.stock_name,
                    start=req.start,
                    end=req.end,
                    initial_cash=req.initial_cash,
                    trader_preference=req.trader_preference,
                    db_path=db_path,
                    memory_dir=memory_dir,
                    chart_dir=chart_dir,
                    progress_callback=progress_cb,
                    step_callback=step_cb,
                ),
            )
            job.result = result
            job.status = "done"
            done_event = {"type": "done", "result": result}
            job.events.append(done_event)
            job.queue.put_nowait(done_event)
        except Exception as exc:
            job.status = "error"
            job.error = str(exc)
            err_event = {"type": "error", "message": str(exc)}
            job.events.append(err_event)
            job.queue.put_nowait(err_event)

    asyncio.create_task(run_in_thread())

    return JobCreatedResponse(
        job_id=job.job_id,
        stream_url=f"/api/backtest/{job.job_id}/stream",
    )


@router.get("/api/backtest/{job_id}/stream")
async def stream_backtest(job_id: str):
    """SSE 스트림으로 백테스트 진행 상황을 실시간 전송한다."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    async def event_generator() -> AsyncGenerator[str, None]:
        # 재연결 시 이미 쌓인 이벤트를 리플레이
        for evt in list(job.events):
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

        if job.status in ("done", "error"):
            return

        while True:
            try:
                evt = await asyncio.wait_for(job.queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                # heartbeat — 연결 유지
                yield ": heartbeat\n\n"
                continue

            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") in ("done", "error"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
