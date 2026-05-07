"""생성된 PNG 차트 파일 서빙 라우트."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/charts/{job_id}/{filename}")
async def serve_chart(job_id: str, filename: str):
    """job_data/{job_id}/charts/{filename} 경로의 PNG를 반환한다."""
    # path traversal 방지
    if ".." in job_id or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="잘못된 파일명입니다.")

    path = os.path.join("job_data", job_id, "charts", filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="차트 파일을 찾을 수 없습니다.")

    return FileResponse(path, media_type="image/png")


@router.get("/api/backtest/{job_id}/chart-list")
async def list_charts(job_id: str):
    """job의 차트 파일 목록을 반환한다."""
    if ".." in job_id:
        raise HTTPException(status_code=400, detail="잘못된 job_id입니다.")

    chart_dir = os.path.join("job_data", job_id, "charts")
    if not os.path.isdir(chart_dir):
        return {"job_id": job_id, "charts": []}

    files = sorted(f for f in os.listdir(chart_dir) if f.endswith(".png"))
    return {"job_id": job_id, "charts": files}
