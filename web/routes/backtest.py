"""백테스트 시작 및 SSE 스트리밍 라우트."""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from web import runs_db
from web.auth import get_current_user, get_current_user_from_token
from web.db import get_conn
from web.job_store import BacktestJob, create_job, get_job
from web.schemas import BacktestRequest, BatchBacktestRequest, JobCreatedResponse


def _get_run_backtest():
    from finagent.main import run_backtest  # noqa: PLC0415
    return run_backtest


router = APIRouter()


def _make_step_callback(job: BacktestJob, loop: asyncio.AbstractEventLoop):
    def callback(step: str):
        event = {"type": "step", "step": step}
        loop.call_soon_threadsafe(job.queue.put_nowait, event)
    return callback


def _make_progress_callback(job: BacktestJob, loop: asyncio.AbstractEventLoop):
    def callback(day_index, total_days, current_date, action, reasoning):
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


def _fetch_llm_config(llm_config_id: int, user_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider, model, api_key, base_url FROM llm_configs WHERE id=%s AND user_id=%s",
                (llm_config_id, user_id),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="LLM 설정을 찾을 수 없습니다.")
    return {"provider": row[0], "model": row[1], "api_key": row[2] or None, "base_url": row[3]}


@router.post("/api/backtest", response_model=JobCreatedResponse)
async def start_backtest(req: BacktestRequest, current_user: dict = Depends(get_current_user)):
    llm_cfg = {}
    if req.llm_config_id:
        llm_cfg = _fetch_llm_config(req.llm_config_id, current_user["id"])

    job = create_job()

    runs_db.create_run(
        run_id=job.job_id,
        symbol=req.symbol,
        stock_name=req.stock_name,
        start_date=str(req.start),
        end_date=str(req.end),
        initial_cash=req.initial_cash,
        trader_preference=req.trader_preference,
        user_id=current_user["id"],
        llm_config_id=req.llm_config_id,
    )

    loop = asyncio.get_event_loop()
    progress_cb = _make_progress_callback(job, loop)
    step_cb = _make_step_callback(job, loop)

    async def run_in_thread():
        from web import batch_queue  # noqa: PLC0415
        slot = batch_queue.get_run_slot()
        if slot.locked():
            wait_event = {"type": "step", "step": "waiting"}
            job.events.append(wait_event)
            job.queue.put_nowait(wait_event)
        async with slot:
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
                        run_id=job.job_id,
                        initial_cash=req.initial_cash,
                        trader_preference=req.trader_preference,
                        llm_provider=llm_cfg.get("provider"),
                        llm_model=llm_cfg.get("model"),
                        llm_api_key=llm_cfg.get("api_key"),
                        llm_base_url=llm_cfg.get("base_url"),
                        chart_dir=os.path.join("job_data", job.job_id, "charts"),
                        trace_dir=os.path.join("job_data", job.job_id, "traces"),
                        progress_callback=progress_cb,
                        step_callback=step_cb,
                    ),
                )
                job.result = result
                job.status = "done"
                runs_db.update_run_done(job.job_id, result)
                done_event = {"type": "done", "result": result}
                job.events.append(done_event)
                job.queue.put_nowait(done_event)
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)
                runs_db.update_run_error(job.job_id, str(exc))
                err_event = {"type": "error", "message": str(exc)}
                job.events.append(err_event)
                job.queue.put_nowait(err_event)

    asyncio.create_task(run_in_thread())
    return JobCreatedResponse(job_id=job.job_id, stream_url=f"/api/backtest/{job.job_id}/stream")


@router.post("/api/backtest/batch")
async def start_batch(req: BatchBacktestRequest, current_user: dict = Depends(get_current_user)):
    """여러 백테스트를 큐에 등록해 순차 실행한다. 각 항목은 개별 run_id로 격리된다."""
    import uuid as _uuid  # noqa: PLC0415
    from web import batch_queue  # noqa: PLC0415

    if not req.items:
        raise HTTPException(status_code=400, detail="실행할 백테스트가 없습니다.")

    # 1) 모든 LLM 설정을 먼저 검증 — 하나라도 실패하면 아무것도 등록하지 않는다(all-or-nothing)
    resolved = []
    for item in req.items:
        llm_cfg = {}
        if item.llm_config_id:
            llm_cfg = _fetch_llm_config(item.llm_config_id, current_user["id"])
        resolved.append((item, llm_cfg))

    # 2) 전부 통과하면 DB 등록 + 큐 적재
    run_ids = []
    for item, llm_cfg in resolved:
        run_id = str(_uuid.uuid4())
        runs_db.create_run(
            run_id=run_id,
            symbol=item.symbol,
            stock_name=item.stock_name,
            start_date=str(item.start),
            end_date=str(item.end),
            initial_cash=item.initial_cash,
            trader_preference=item.trader_preference,
            user_id=current_user["id"],
            llm_config_id=item.llm_config_id,
            status="queued",
        )
        batch_queue.enqueue({
            "run_id": run_id,
            "symbol": item.symbol,
            "stock_name": item.stock_name,
            "start": item.start,
            "end": item.end,
            "initial_cash": item.initial_cash,
            "trader_preference": item.trader_preference,
            "llm": llm_cfg,
        })
        run_ids.append(run_id)

    return {"queued": len(run_ids), "run_ids": run_ids}


@router.post("/api/backtest/{run_id}/resume", response_model=JobCreatedResponse)
async def resume_backtest(run_id: str, current_user: dict = Depends(get_current_user)):
    info = runs_db.get_resume_info(run_id)
    if not info:
        raise HTTPException(status_code=404, detail="실행 정보를 찾을 수 없습니다.")
    if info.get("user_id") is not None and info["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="본인의 실행만 재실행할 수 있습니다.")

    if info["last_trade_date"] is None:
        raise HTTPException(status_code=400, detail="이어서 실행할 거래 내역이 없습니다.")

    llm_cfg = {}
    if info["llm_config_id"]:
        with get_conn() as _conn:
            with _conn.cursor() as _cur:
                _cur.execute(
                    "SELECT provider, model, api_key, base_url FROM llm_configs WHERE id=%s",
                    (info["llm_config_id"],),
                )
                _row = _cur.fetchone()
        if _row:
            llm_cfg = {"provider": _row[0], "model": _row[1], "api_key": _row[2] or None, "base_url": _row[3]}

    job = create_job()
    runs_db.update_run_running(run_id)

    loop = asyncio.get_event_loop()
    progress_cb = _make_progress_callback(job, loop)
    step_cb = _make_step_callback(job, loop)

    async def run_in_thread():
        from web import batch_queue  # noqa: PLC0415
        slot = batch_queue.get_run_slot()
        if slot.locked():
            wait_event = {"type": "step", "step": "waiting"}
            job.events.append(wait_event)
            job.queue.put_nowait(wait_event)
        async with slot:
            job.status = "running"
            try:
                run_backtest = _get_run_backtest()
                result = await loop.run_in_executor(
                    None,
                    lambda: run_backtest(
                        symbol=info["symbol"],
                        stock_name=info["stock_name"],
                        start=info["start_date"],
                        end=info["end_date"],
                        run_id=run_id,
                        initial_cash=info["initial_cash"],
                        trader_preference=info["trader_preference"],
                        resume_from=info["last_trade_date"],
                        llm_provider=llm_cfg.get("provider"),
                        llm_model=llm_cfg.get("model"),
                        llm_api_key=llm_cfg.get("api_key"),
                        llm_base_url=llm_cfg.get("base_url"),
                        chart_dir=os.path.join("job_data", run_id, "charts"),
                        trace_dir=os.path.join("job_data", run_id, "traces"),
                        progress_callback=progress_cb,
                        step_callback=step_cb,
                    ),
                )
                job.result = result
                job.status = "done"
                runs_db.update_run_done(run_id, result)
                done_event = {"type": "done", "result": result}
                job.events.append(done_event)
                job.queue.put_nowait(done_event)
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)
                runs_db.update_run_error(run_id, str(exc))
                err_event = {"type": "error", "message": str(exc)}
                job.events.append(err_event)
                job.queue.put_nowait(err_event)

    asyncio.create_task(run_in_thread())
    return JobCreatedResponse(job_id=job.job_id, stream_url=f"/api/backtest/{job.job_id}/stream")


@router.get("/api/backtest/{job_id}/stream")
async def stream_backtest(job_id: str, token: str | None = None):
    # EventSource는 Authorization 헤더를 지원하지 않으므로 쿼리 파라미터로 수신
    if not token:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    await get_current_user_from_token(token)
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    async def event_generator():
        for evt in list(job.events):
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        if job.status in ("done", "error"):
            return
        while True:
            try:
                evt = await asyncio.wait_for(job.queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") in ("done", "error"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
