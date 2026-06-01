"""리뷰 & 히스토리 라우트 — 백테스트 결과 상세 검토 UI용 API."""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from web import runs_db
from web.auth import get_current_user
from web.db import get_conn

router = APIRouter(prefix="/review")


# ---------------------------------------------------------------------------
# AI 종합 분석 (논문 관점) — 헬퍼 + 라우트
# ---------------------------------------------------------------------------

INITIAL_INSTRUCTION = """
당신은 멀티모달 금융 트레이딩 에이전트 논문 "A Multimodal Foundation Agent for Financial Trading"(arxiv 2402.18485, 이하 FinAgent 논문)의 평가자이자 동시에 실무 투자자입니다.
위 백테스트 결과에 대해 **학술적 관점**과 **투자자 관점**을 분리하여 작성해주세요.

다음 구조를 따라 마크다운으로, 각 섹션은 핵심 5~8줄:

# A. 학술적 관점 (논문 평가자 입장)
## A1. 한줄 요약
## A2. 베이스라인(Buy&Hold) 대비 성과 — α(초과수익)의 의미, 능동매매가 가치를 더했는지
## A3. 위험 조정 수익 해석 — Sharpe / Sortino / Calmar, MDD 관점
## A4. 논문 모듈별 추론 — DataFetcher / MarketIntelligence(+Diversified Retrieval) / Low-Level Reflection(Vision) / High-Level Reflection / Decision Making(+Tool-Augmented Signals)
## A5. 모델 선택의 영향 — 멀티모달(이미지) vs 텍스트 전용. 본 실행 모델이 LLR/HLR의 차트 비전 분석을 활용 가능했는지.
## A6. 주목할 점·특이점
## A7. 한계점 — 본 구현이 논문 대비 단순화한 부분, 데이터·모델·평가의 한계 (Google RSS 의존, 단일 종목, 슬리피지·수수료 미고려, 365일 제한 등)
## A8. 발표 토킹 포인트 — 학문적 관점에서 강조할 핵심 3가지

# B. 투자자 관점 (실무자 입장)
## B1. 실거래 적용 시 예상 수익성 — 수수료·슬리피지(왕복 0.3% 가정) 차감 후 수익은 어떻게 변할지
## B2. 리스크 평가 — MDD 크기, 손실 지속 기간, 자본 보전 측면에서 견딜 만한가
## B3. 포지션 사이징·자본 규모 — 현재 BUY 50% 룰의 한계, 권장 개선
## B4. 실용성·운영 비용 — LLM 호출 비용, 결정 지연(분/일 단위 의사결정 적합성)
## B5. 추천도 및 다음 행동 — 이 전략을 (a) 그대로 실거래에 쓸지, (b) 시뮬레이션만 더 할지, (c) 보완 후 적용할지 명확한 권고

한국어로 작성해주세요. 두 관점이 서로 다른 결론을 내릴 수 있다면 그 차이를 명시하세요.
""".strip()


COMPARE_INSTRUCTION = """
당신은 멀티모달 금융 트레이딩 에이전트 논문 "A Multimodal Foundation Agent for Financial Trading"(arxiv 2402.18485, 이하 FinAgent 논문)의 평가자이자 동시에 실무 투자자입니다.
위에 나열된 여러 백테스트 결과를 **비교 분석**해주세요. 절대값보다 결과 간 **차이**에 집중하세요.

다음 구조를 따라 마크다운으로:

# A. 학술적 비교 관점
## A1. 비교 요약 — 베스트/워스트 한 줄 (run 번호로 지칭)
## A2. α(초과수익) 비교 — 누가 베이스라인을 가장 크게 이겼고, 누가 졌나
## A3. 위험 조정 비교 — Sharpe/Sortino/Calmar 기준 상대 평가, MDD 분포
## A4. 변수별 영향 추론 — 모델 / 종목 / 성향 / 기간 중 결과 차이를 가장 잘 설명하는 변수
## A5. 논문 모듈 관점 — 어떤 모듈이 차이를 만들었을지 (예: 비전 미지원 모델 vs 지원 모델의 LLR 효과)
## A6. 비교 자체의 한계 — 표본 수, 통제 변수 부재, 기간 비교가능성, 시장 국면 차이 등

# B. 투자자 비교 관점
## B1. 어떤 조합이 가장 유망한가 — 종목 × 모델 × 성향 조합 중 실거래 후보
## B2. 수수료·슬리피지 차감 후에도 알파가 살아남을 후보
## B3. 안정성 vs 수익성 트레이드오프 — Sharpe 높은 쪽 vs 절대수익 높은 쪽
## B4. 운영 비용 대비 효용 — 비싼 모델이 그만한 추가 알파를 만들었는가
## B5. 발표/투자 의사결정 토킹 포인트 — 청중을 설득할 핵심 3가지

한국어로 작성, 항목당 5~8줄 이내로 간결하게.
""".strip()


def _build_data_context(run: dict, trades: list[tuple]) -> str:
    res = run.get("result") or {}
    tr = res.get("total_return_pct")
    bh = res.get("benchmark_return_pct")
    alpha = (tr - bh) if (isinstance(tr, (int, float)) and isinstance(bh, (int, float))) else None

    actions: dict[str, int] = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for _d, a in trades:
        actions[a] = actions.get(a, 0) + 1

    def fmt(v, suffix="%"):
        return f"{v:.2f}{suffix}" if isinstance(v, (int, float)) else "—"

    lines = [
        "# 백테스트 정보",
        f"- 종목: {run.get('stock_name')} ({run.get('symbol')})",
        f"- 기간: {run.get('start_date')} ~ {run.get('end_date')}",
        f"- 초기자본: {float(run.get('initial_cash') or 0):,.0f}원",
        f"- 트레이더 성향: {run.get('trader_preference')}",
        f"- LLM 모델: {run.get('model') or run.get('provider') or '기본'}",
        "",
        "# 성과 지표",
        f"- 총 수익률: {fmt(tr)}",
        f"- 베이스라인(Buy&Hold): {fmt(bh)}",
        f"- 초과수익(α): {fmt(alpha)}",
        f"- 연간 환산 수익률: {fmt(res.get('annualized_return_pct'))}",
        f"- Sharpe Ratio: {fmt(res.get('sharpe_ratio'), '')}",
        f"- Sortino Ratio: {fmt(res.get('sortino_ratio'), '')}",
        f"- Calmar Ratio: {fmt(res.get('calmar_ratio'), '')}",
        f"- 최대 낙폭(MDD): {fmt(res.get('max_drawdown_pct'))}",
        f"- 연간 변동성: {fmt(res.get('volatility_annual_pct'))}",
        "",
        "# 거래 통계",
        f"- BUY: {actions.get('BUY',0)}회 / SELL: {actions.get('SELL',0)}회 / HOLD: {actions.get('HOLD',0)}회",
        f"- 총 거래일: {sum(actions.values())}일",
    ]
    return "\n".join(lines)


def _fetch_trades_simple(run_id: str) -> list[tuple]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT date, action FROM trades WHERE run_id=%s ORDER BY id", (run_id,))
            return cur.fetchall()


async def _call_llm(messages: list[dict], max_tokens: int) -> str:
    from finagent.llm.client import LLMClient  # noqa: PLC0415
    loop = asyncio.get_running_loop()
    client = LLMClient()
    return await loop.run_in_executor(None, lambda: client.chat(messages, max_tokens=max_tokens))


@router.get("/api/runs/{run_id}/analysis")
async def get_analysis(run_id: str):
    thread = runs_db.get_analysis_thread(run_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.")
    return {"thread": thread}


@router.post("/api/runs/{run_id}/analyze")
async def analyze_run(
    run_id: str,
    current_user: dict = Depends(get_current_user),
    force: bool = False,
):
    run = runs_db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.")
    if run.get("status") != "done":
        raise HTTPException(status_code=400, detail="완료된 백테스트만 분석할 수 있습니다.")

    thread = runs_db.get_analysis_thread(run_id) or []
    if thread and not force:
        return {"thread": thread, "cached": True}
    if force:
        runs_db.clear_analysis_thread(run_id)

    trades = _fetch_trades_simple(run_id)
    user_prompt = _build_data_context(run, trades) + "\n\n" + INITIAL_INSTRUCTION
    reply = await _call_llm([{"role": "user", "content": user_prompt}], max_tokens=2500)
    runs_db.append_analysis_message(run_id, "assistant", reply)
    return {"thread": runs_db.get_analysis_thread(run_id), "cached": False}


@router.post("/api/runs/{run_id}/analyze/ask")
async def ask_followup(
    run_id: str,
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    run = runs_db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.")

    thread = runs_db.get_analysis_thread(run_id) or []
    if not thread:
        raise HTTPException(status_code=400, detail="먼저 AI 종합 분석을 요청해주세요.")

    trades = _fetch_trades_simple(run_id)
    initial_user = _build_data_context(run, trades) + "\n\n" + INITIAL_INSTRUCTION

    messages: list[dict] = [{"role": "user", "content": initial_user}]
    for m in thread:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})

    reply = await _call_llm(messages, max_tokens=2000)
    runs_db.append_analysis_message(run_id, "user", question)
    runs_db.append_analysis_message(run_id, "assistant", reply)
    return {"thread": runs_db.get_analysis_thread(run_id)}


# ---------------------------------------------------------------------------
# 비교 분석 (여러 run을 한꺼번에 평가) — 학술 + 투자자 두 관점
# ---------------------------------------------------------------------------

def _build_compare_context(runs: list[dict]) -> str:
    lines = ["# 비교 대상 백테스트"]
    for i, r in enumerate(runs, 1):
        res = r.get("result") or {}
        tr, bh = res.get("total_return_pct"), res.get("benchmark_return_pct")
        alpha = (tr - bh) if (isinstance(tr, (int, float)) and isinstance(bh, (int, float))) else None

        def fmt(v, suffix="%"):
            return f"{v:.2f}{suffix}" if isinstance(v, (int, float)) else "—"

        lines.append(
            f"\n## Run {i}: {r.get('stock_name')} ({r.get('symbol')})\n"
            f"- 기간: {r.get('start_date')} ~ {r.get('end_date')} | "
            f"성향: {r.get('trader_preference')} | "
            f"모델: {r.get('model') or r.get('provider') or '기본'}\n"
            f"- 총수익: {fmt(tr)} | B&H: {fmt(bh)} | α: {fmt(alpha)}\n"
            f"- Sharpe: {fmt(res.get('sharpe_ratio'),'')} | "
            f"Sortino: {fmt(res.get('sortino_ratio'),'')} | "
            f"Calmar: {fmt(res.get('calmar_ratio'),'')} | "
            f"MDD: {fmt(res.get('max_drawdown_pct'))} | "
            f"연변동성: {fmt(res.get('volatility_annual_pct'))}\n"
            f"- 거래: BUY {res.get('buy_count', 0)} / SELL {res.get('sell_count', 0)} / HOLD {res.get('hold_count', 0)}"
        )
    return "\n".join(lines)


def _resolve_compare_runs(ids_csv: str) -> tuple[list[str], list[dict]]:
    ids = [s.strip() for s in (ids_csv or "").split(",") if s.strip()]
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="비교 분석은 2개 이상의 run이 필요합니다.")
    runs = []
    for rid in ids:
        run = runs_db.get_run(rid)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run을 찾을 수 없습니다: {rid}")
        if run.get("status") != "done":
            raise HTTPException(status_code=400, detail="완료된 백테스트만 비교할 수 있습니다.")
        runs.append(run)
    return ids, runs


@router.get("/api/compare/analysis")
async def get_compare_analysis(ids: str):
    run_ids, _ = _resolve_compare_runs(ids)
    return {"thread": runs_db.get_comparison_thread(run_ids)}


@router.post("/api/compare/analyze")
async def analyze_compare(
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    ids_csv = ",".join(body.get("ids") or [])
    force = bool(body.get("force"))
    run_ids, runs = _resolve_compare_runs(ids_csv)

    thread = runs_db.get_comparison_thread(run_ids)
    if thread and not force:
        return {"thread": thread, "cached": True}
    if force:
        runs_db.clear_comparison_thread(run_ids)

    user_prompt = _build_compare_context(runs) + "\n\n" + COMPARE_INSTRUCTION
    reply = await _call_llm([{"role": "user", "content": user_prompt}], max_tokens=3000)
    runs_db.append_comparison_message(run_ids, "assistant", reply)
    return {"thread": runs_db.get_comparison_thread(run_ids), "cached": False}


@router.post("/api/compare/analyze/ask")
async def ask_compare_followup(
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    ids_csv = ",".join(body.get("ids") or [])
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")
    run_ids, runs = _resolve_compare_runs(ids_csv)

    thread = runs_db.get_comparison_thread(run_ids)
    if not thread:
        raise HTTPException(status_code=400, detail="먼저 비교 종합 분석을 요청해주세요.")

    initial_user = _build_compare_context(runs) + "\n\n" + COMPARE_INSTRUCTION
    messages: list[dict] = [{"role": "user", "content": initial_user}]
    for m in thread:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})

    reply = await _call_llm(messages, max_tokens=2000)
    runs_db.append_comparison_message(run_ids, "user", question)
    runs_db.append_comparison_message(run_ids, "assistant", reply)
    return {"thread": runs_db.get_comparison_thread(run_ids)}


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
