"""순차 실행 배치 백테스트 큐.

여러 백테스트를 하나씩 순서대로 실행한다(병렬 아님). 단일 워커가
큐에서 항목을 꺼내 run_backtest를 완료할 때까지 await 후 다음으로 넘어가므로
Ollama/LLM에 동시 부하가 걸리지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from web import runs_db

logger = logging.getLogger(__name__)

_queue: "asyncio.Queue[dict]" = asyncio.Queue()
_worker_task: Optional[asyncio.Task] = None


def _get_run_backtest():
    from finagent.main import run_backtest  # noqa: PLC0415
    return run_backtest


async def _run_item(item: dict, loop: asyncio.AbstractEventLoop) -> None:
    run_id = item["run_id"]
    llm = item["llm"]
    runs_db.update_run_running(run_id)
    run_backtest = _get_run_backtest()
    result = await loop.run_in_executor(
        None,
        lambda: run_backtest(
            symbol=item["symbol"],
            stock_name=item["stock_name"],
            start=item["start"],
            end=item["end"],
            run_id=run_id,
            initial_cash=item["initial_cash"],
            trader_preference=item["trader_preference"],
            llm_provider=llm.get("provider"),
            llm_model=llm.get("model"),
            llm_api_key=llm.get("api_key"),
            llm_base_url=llm.get("base_url"),
            chart_dir=os.path.join("job_data", run_id, "charts"),
            trace_dir=os.path.join("job_data", run_id, "traces"),
        ),
    )
    runs_db.update_run_done(run_id, result)


async def _worker() -> None:
    loop = asyncio.get_event_loop()
    while True:
        item = await _queue.get()
        try:
            await _run_item(item, loop)
        except Exception as exc:
            logger.exception("Batch backtest failed: %s", item.get("run_id"))
            try:
                runs_db.update_run_error(item["run_id"], str(exc))
            except Exception:
                logger.exception("Failed to mark run as error: %s", item.get("run_id"))
        finally:
            _queue.task_done()


def ensure_worker() -> None:
    """워커 태스크가 없으면 현재 이벤트 루프에서 시작한다."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())


def enqueue(item: dict) -> None:
    _queue.put_nowait(item)


def pending_count() -> int:
    return _queue.qsize()
