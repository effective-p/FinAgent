"""백테스트 결과 조회 라우트."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from web.job_store import get_job
from web.schemas import BacktestResultResponse

router = APIRouter()


@router.get("/api/backtest/{job_id}/result", response_model=BacktestResultResponse)
async def get_result(job_id: str):
    """백테스트 최종 결과를 반환한다 (SSE 실패 시 폴링 대안)."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    return BacktestResultResponse(
        job_id=job.job_id,
        status=job.status,
        result=job.result,
        error=job.error,
    )


@router.get("/api/backtest/{job_id}/trades")
async def get_trades(job_id: str):
    """거래 내역을 progress 이벤트에서 추출하여 반환한다."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    trades = [
        {
            "date": evt["date"],
            "action": evt["action"],
            "reasoning": evt["reasoning"],
        }
        for evt in job.events
        if evt.get("type") == "progress"
    ]
    return {"job_id": job_id, "trades": trades}
