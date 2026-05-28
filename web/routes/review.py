"""리뷰 & 히스토리 라우트 — 백테스트 결과 상세 검토 UI용 API."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from web import runs_db
from web.auth import get_current_user
from web.db import get_conn

router = APIRouter(prefix="/review")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def review_page():
    path = os.path.join("web", "static", "review.html")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="review.html not found")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ---------------------------------------------------------------------------
# 실행 목록 / 상세
# ---------------------------------------------------------------------------

@router.get("/api/runs")
async def list_runs():
    return runs_db.list_runs()


@router.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = runs_db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.")
    return run


@router.delete("/api/runs/{run_id}")
async def delete_run(run_id: str, current_user: dict = Depends(get_current_user)):
    import shutil  # noqa: PLC0415
    run = runs_db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.")
    if run.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="본인의 실행만 삭제할 수 있습니다.")
    runs_db.delete_run(run_id)
    job_dir = os.path.join("job_data", run_id)
    if os.path.isdir(job_dir):
        shutil.rmtree(job_dir, ignore_errors=True)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 일별 거래 내역 (portfolio.db에서 직접 조회)
# ---------------------------------------------------------------------------

@router.get("/api/runs/{run_id}/days")
async def list_days(run_id: str):
    import traceback as _tb
    try:
        run = runs_db.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT date, action, quantity, price, reasoning FROM trades WHERE run_id=%s ORDER BY id ASC",
                    (run_id,),
                )
                rows = cur.fetchall()

        trace_dir = os.path.join("job_data", run_id, "traces")
        return [
            {
                "date": r[0], "action": r[1], "quantity": r[2], "price": r[3],
                "reasoning": r[4] or "",
                "has_trace": os.path.isfile(os.path.join(trace_dir, f"{r[0]}.json")),
            }
            for r in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        print("=== list_days ERROR ===")
        _tb.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 하루치 워크플로우 트레이스
# ---------------------------------------------------------------------------

@router.get("/api/runs/{run_id}/days/{date_str}")
async def get_day_trace(run_id: str, date_str: str):
    if ".." in date_str or "/" in date_str or "\\" in date_str:
        raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다.")

    trace_dir = os.path.join("job_data", run_id, "traces")
    trace_path = os.path.join(trace_dir, f"{date_str}.json")
    if not os.path.isfile(trace_path):
        raise HTTPException(status_code=404, detail=f"{date_str} 트레이스가 없습니다.")
    with open(trace_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 성과 차트 이미지
# ---------------------------------------------------------------------------

@router.get("/api/runs/{run_id}/perf-chart")
async def get_perf_chart(run_id: str):
    if ".." in run_id:
        raise HTTPException(status_code=400, detail="잘못된 run_id입니다.")
    run = runs_db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.")

    chart_dir = os.path.join("job_data", run_id, "charts")
    if not os.path.isdir(chart_dir):
        raise HTTPException(status_code=404, detail="차트 디렉토리가 없습니다.")

    prefix = f"performance_{run['symbol']}_"
    candidates = [f for f in os.listdir(chart_dir) if f.startswith(prefix) and f.endswith(".png")]
    if not candidates:
        raise HTTPException(status_code=404, detail="성과 차트가 없습니다.")

    return FileResponse(os.path.join(chart_dir, candidates[0]), media_type="image/png")


# ---------------------------------------------------------------------------
# 일별 차트 이미지 (kline / trading) — 트레이스에서 경로 참조
# ---------------------------------------------------------------------------

@router.get("/api/runs/{run_id}/days/{date_str}/chart/{chart_type}")
async def get_day_chart(run_id: str, date_str: str, chart_type: str):
    if chart_type not in ("kline", "trading"):
        raise HTTPException(status_code=400, detail="chart_type은 kline 또는 trading이어야 합니다.")
    if ".." in date_str or ".." in run_id:
        raise HTTPException(status_code=400, detail="잘못된 파라미터입니다.")

    run = runs_db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.")

    symbol = run["symbol"]
    chart_dir = os.path.join("job_data", run_id, "charts")
    filename = f"{chart_type}_{symbol}_{date_str}.png"
    path = os.path.join(chart_dir, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="차트 파일이 없습니다.")

    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# 멀티 종목 비교 — 정규화된 equity curve
# ---------------------------------------------------------------------------

@router.get("/api/compare")
async def compare_runs(ids: str):
    """ids=a,b,c 형태로 run_id 목록을 받아 정규화 equity curve를 반환한다."""
    run_ids = [r.strip() for r in ids.split(",") if r.strip()]
    if not run_ids:
        raise HTTPException(status_code=400, detail="ids 파라미터가 필요합니다.")

    result = []
    for rid in run_ids:
        run = runs_db.get_run(rid)
        if not run or run["status"] != "done":
            continue
        res = run.get("result") or {}
        equity = res.get("equity_curve", [])
        benchmark = res.get("benchmark_curve", [])

        def normalize(curve: list) -> list:
            if not curve:
                return []
            base = curve[0]["value"]
            if base == 0:
                return curve
            return [{"date": p["date"], "norm": round(p["value"] / base * 100, 4)} for p in curve]

        result.append({
            "run_id": rid,
            "symbol": run["symbol"],
            "stock_name": run["stock_name"],
            "start_date": run["start_date"],
            "end_date": run["end_date"],
            "trader_preference": run["trader_preference"],
            "status": run["status"],
            "llm_model": run.get("model") or os.getenv("FINAGENT_MODEL", "gpt-4o-mini"),
            "total_return_pct": res.get("total_return_pct"),
            "benchmark_return_pct": res.get("benchmark_return_pct"),
            "equity_curve": normalize(equity),
            "benchmark_curve": normalize(benchmark),
        })
    return result
