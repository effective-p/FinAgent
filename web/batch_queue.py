"""순차 실행 배치 백테스트 큐.

여러 백테스트를 하나씩 순서대로 실행한다(병렬 아님). 단일 워커가
큐에서 항목을 꺼내 run_backtest를 완료할 때까지 await 후 다음으로 넘어간다.

추가로 모든 실행 진입점(배치 워커 / 단일 실행 / 이어서 실행)은
공유 세마포어(run_slot, 용량 1)를 획득한 뒤에만 run_backtest를 돌리므로
앱 전체에서 동시에 두 개의 백테스트가 LLM 서버를 때리지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from web import runs_db

logger = logging.getLogger(__name__)

_queue: Optional["asyncio.Queue[dict]"] = None
_run_slot: Optional[asyncio.Semaphore] = None
_worker_task: Optional[asyncio.Task] = None


def _get_queue() -> "asyncio.Queue[dict]":
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def get_run_slot() -> asyncio.Semaphore:
    """전역 실행 슬롯(용량 1). 실행 중인 루프에서 지연 생성한다."""
    global _run_slot
    if _run_slot is None:
        _run_slot = asyncio.Semaphore(1)
    return _run_slot


def _get_run_backtest():
    from finagent.main import run_backtest  # noqa: PLC0415
    return run_backtest


async def _run_item(item: dict, loop: asyncio.AbstractEventLoop) -> None:
    run_id = item["run_id"]
    llm = item["llm"]
    # 대기 중 삭제(취소)되었거나 이미 다른 상태면 원자적으로 건너뜀
    if not runs_db.claim_queued_run(run_id):
        logger.info("Skipping run (cancelled or not queued): %s", run_id)
        return
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
    loop = asyncio.get_running_loop()
    q = _get_queue()
    slot = get_run_slot()
    while True:
        item = await q.get()
        try:
            async with slot:
                await _run_item(item, loop)
        except Exception as exc:
            logger.exception("Batch backtest failed: %s", item.get("run_id"))
            try:
                runs_db.update_run_error(item["run_id"], str(exc))
            except Exception:
                logger.exception("Failed to mark run as error: %s", item.get("run_id"))
        finally:
            q.task_done()


def ensure_worker() -> None:
    """워커 태스크가 없거나 죽었으면 현재 이벤트 루프에서 (재)시작한다."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())


def enqueue(item: dict) -> None:
    ensure_worker()
    _get_queue().put_nowait(item)


def recover_queued() -> None:
    """서버 재시작 시 DB 상태를 인메모리 큐와 정합시킨다.

    - 고아 'running'(이전 프로세스에서 중단됨)은 'error'로 표시.
    - 'queued'로 남은 run들은 다시 큐에 적재(인메모리 큐는 재시작 시 소실되므로
      이 복구가 없으면 영원히 실행되지 않음).
    """
    failed = runs_db.fail_stale_running()
    if failed:
        logger.info("Marked %d orphaned running run(s) as error after restart", failed)

    items = runs_db.list_queued_runs()
    if not items:
        return
    ensure_worker()
    q = _get_queue()
    for item in items:
        q.put_nowait(item)
    logger.info("Recovered %d queued run(s) after restart", len(items))
