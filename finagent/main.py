"""FinAgent 백테스팅 진입점.

Usage:
    python finagent/main.py \
        --symbol 005930 \
        --stock-name 삼성전자 \
        --start 2024-01-01 \
        --end 2024-03-31
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from finagent.data.fetcher import DataFetcher
from finagent.llm.trace import begin_trace, end_trace
from finagent.memory.store import MemoryStore
from finagent.modules.decision_making import DecisionMakingModule
from finagent.modules.high_level_reflection import HighLevelReflectionModule
from finagent.modules.low_level_reflection import LowLevelReflectionModule
from finagent.modules.market_intelligence import MarketIntelligenceModule
from finagent.portfolio.portfolio import Portfolio
from finagent.tools.technical_indicators import get_technical_signals
from finagent.utils.metrics import (
    compute_benchmark,
    compute_equity_curve,
    compute_performance,
    plot_performance,
)
from finagent.utils.schemas import Decision

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 하루치 파이프라인
# ---------------------------------------------------------------------------

def run_day(
    symbol: str,
    stock_name: str,
    target_date: date,
    price_df: pd.DataFrame,
    fetcher: DataFetcher,
    portfolio: Portfolio,
    mi_module: MarketIntelligenceModule,
    llr_module: LowLevelReflectionModule,
    hlr_module: HighLevelReflectionModule,
    dm_module: DecisionMakingModule,
    trader_preference: str = "moderate",
    step_callback=None,
    trace_dir: str | None = None,
) -> Decision:
    """하루치 전체 파이프라인을 실행하고 Decision을 반환한다."""

    def _step(name: str) -> None:
        if step_callback:
            try:
                step_callback(name)
            except Exception:
                pass

    logger.info("=== %s | %s ===", symbol, target_date)

    # look-ahead bias 방지: target_date 이전 데이터만 사용
    df = price_df.loc[:pd.Timestamp(target_date)]
    current_price = float(df["Close"].iloc[-1])

    trace: dict | None = {"date": str(target_date), "symbol": symbol, "stock_name": stock_name,
                          "current_price": current_price, "steps": {}} if trace_dir else None

    # 1. 데이터 수집
    _step("news_fetch")
    begin_trace("news_fetch")
    news = fetcher.get_news(symbol, stock_name, target_date)
    investor_data = fetcher.get_investor_trading(symbol, target_date)
    fundamental_guidance = fetcher.get_fundamental_guidance(symbol, target_date)
    kline_path = fetcher.plot_kline_chart(df, target_date, symbol)
    trading_path = fetcher.plot_trading_chart(
        df, portfolio.recent_actions(14), target_date, symbol,
    )
    if trace is not None:
        trace["steps"]["news_fetch"] = {
            "llm_calls": end_trace(),
            "news_count": len(news),
            "news": [{"title": n.title, "summary": n.summary[:300], "published": str(n.published)} for n in news],
            "investor_data": investor_data or "",
            "fundamental_guidance": fundamental_guidance or "",
            "kline_image": kline_path,
            "trading_image": trading_path,
        }
    else:
        end_trace()

    # 2. Market Intelligence
    _step("market_intelligence")
    begin_trace("market_intelligence")
    mi_result = mi_module.run(symbol, target_date, df, news, investor_data=investor_data)
    if trace is not None:
        trace["steps"]["market_intelligence"] = {
            "llm_calls": end_trace(),
            "output": {
                "latest_summary": mi_result.latest_summary,
                "past_summary": mi_result.past_summary,
                "short_term_query": mi_result.short_term_query,
                "medium_term_query": mi_result.medium_term_query,
                "long_term_query": mi_result.long_term_query,
            },
        }
    else:
        end_trace()

    # 3. Low-Level Reflection
    _step("low_level_reflection")
    begin_trace("low_level_reflection")
    llr_result = llr_module.run(symbol, target_date, df, kline_path, mi_result)
    if trace is not None:
        trace["steps"]["low_level_reflection"] = {
            "llm_calls": end_trace(),
            "kline_image": kline_path,
            "output": {
                "short_term_reasoning": llr_result.short_term_reasoning,
                "medium_term_reasoning": llr_result.medium_term_reasoning,
                "long_term_reasoning": llr_result.long_term_reasoning,
                "query": llr_result.query,
            },
        }
    else:
        end_trace()

    # 4. High-Level Reflection
    _step("high_level_reflection")
    begin_trace("high_level_reflection")
    hlr_result = hlr_module.run(
        symbol, target_date, trading_path,
        portfolio.recent_actions(14), mi_result, llr_result,
    )
    if trace is not None:
        trace["steps"]["high_level_reflection"] = {
            "llm_calls": end_trace(),
            "trading_image": trading_path,
            "output": {
                "reasoning": hlr_result.reasoning,
                "improvement": hlr_result.improvement,
                "summary": hlr_result.summary,
                "query": hlr_result.query,
            },
        }
    else:
        end_trace()

    # 5. Decision Making — portfolio_state는 execute() 전에 캡처
    _step("decision_making")
    portfolio_state = portfolio.get_state(current_price)
    begin_trace("decision_making")
    decision = dm_module.run(
        symbol, target_date, df,
        mi_result, llr_result, hlr_result,
        portfolio_state, trader_preference,
        fundamental_guidance=fundamental_guidance,
    )
    if trace is not None:
        tech = get_technical_signals(df)
        trace["steps"]["decision_making"] = {
            "llm_calls": end_trace(),
            "technical_signals": tech.signal_text,
            "portfolio_state": {
                "cash": portfolio_state.cash,
                "position": portfolio_state.position,
                "total_value": portfolio_state.total_value,
            },
            "output": {
                "action": decision.action,
                "reasoning": decision.reasoning,
                "analysis": decision.analysis,
            },
        }
    else:
        end_trace()

    # 6. 거래 실행
    _step("trade_execution")
    portfolio.execute(decision.action, current_price, target_date, decision.reasoning)
    if trace is not None:
        state_after = portfolio.get_state(current_price)
        trace["steps"]["trade_execution"] = {
            "action": decision.action,
            "price": current_price,
            "cash_before": portfolio_state.cash,
            "cash_after": state_after.cash,
            "position_before": portfolio_state.position,
            "position_after": state_after.position,
            "total_value_before": portfolio_state.total_value,
            "total_value_after": state_after.total_value,
        }
        os.makedirs(trace_dir, exist_ok=True)
        trace_path = os.path.join(trace_dir, f"{target_date}.json")
        try:
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(trace, f, ensure_ascii=False, default=str, indent=2)
        except Exception:
            logger.exception("Failed to write trace for %s", target_date)

    logger.info(
        "%s @ %.0f | cash=%.0f pos=%.4f total=%.0f",
        decision.action, current_price,
        portfolio.get_cash(), portfolio.get_position(),
        portfolio.get_portfolio_value(current_price),
    )
    return decision


# ---------------------------------------------------------------------------
# 백테스팅 루프
# ---------------------------------------------------------------------------

def run_backtest(
    symbol: str,
    stock_name: str,
    start: date,
    end: date,
    run_id: str | None = None,
    initial_cash: float = 10_000_000,
    trader_preference: str = "moderate",
    chart_dir: str = "charts",
    trace_dir: str | None = None,
    progress_callback=None,
    step_callback=None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    resume_from: date | None = None,
    # 하위 호환: 더 이상 사용 안 하지만 기존 호출자가 넘기는 경우 무시
    db_path: str | None = None,
    memory_dir: str | None = None,
) -> dict:
    """start ~ end 기간 동안 백테스팅을 실행하고 결과를 반환한다."""
    import uuid as _uuid  # noqa: PLC0415
    from finagent.llm.client import LLMClient  # noqa: PLC0415

    if run_id is None:
        run_id = str(_uuid.uuid4())

    fetcher = DataFetcher(chart_dir=chart_dir)
    memory = MemoryStore(run_id=run_id)
    portfolio = Portfolio(run_id=run_id, symbol=symbol, initial_cash=initial_cash)
    llm_client = LLMClient(provider=llm_provider, model=llm_model, api_key=llm_api_key, base_url=llm_base_url)

    mi_module = MarketIntelligenceModule(memory=memory, llm_client=llm_client)
    llr_module = LowLevelReflectionModule(memory=memory, llm_client=llm_client)
    hlr_module = HighLevelReflectionModule(memory=memory, llm_client=llm_client)
    dm_module = DecisionMakingModule(memory=memory, llm_client=llm_client)

    # 전체 기간 + 충분한 lookback 한 번에 수집
    lookback_days = (date.today() - start).days + 90
    logger.info("Fetching price data for %s (lookback=%d days)…", symbol, lookback_days)
    if step_callback:
        try:
            step_callback("ohlcv_fetch")
        except Exception:
            pass
    price_df = fetcher.get_price_data(symbol, lookback_days=lookback_days)

    # 백테스팅 대상 거래일 필터
    trading_days = price_df.index[
        (price_df.index >= pd.Timestamp(start)) &
        (price_df.index <= pd.Timestamp(end))
    ]

    # 이어서 실행: 이미 처리된 날은 건너뜀
    if resume_from is not None:
        trading_days = trading_days[trading_days > pd.Timestamp(resume_from)]
        logger.info("Resuming from %s (skipping already-processed days)", resume_from)

    if trading_days.empty:
        logger.warning("No trading days found between %s and %s", start, end)
        return {}

    logger.info("Running backtest: %s → %s (%d days)", start, end, len(trading_days))

    total_days = len(trading_days)
    for i, ts in enumerate(trading_days):
        decision = None
        try:
            decision = run_day(
                symbol=symbol,
                stock_name=stock_name,
                target_date=ts.date(),
                price_df=price_df,
                fetcher=fetcher,
                portfolio=portfolio,
                mi_module=mi_module,
                llr_module=llr_module,
                hlr_module=hlr_module,
                dm_module=dm_module,
                trader_preference=trader_preference,
                step_callback=step_callback,
                trace_dir=trace_dir,
            )
        except Exception as exc:
            logger.exception("Error on %s, skipping day", ts.date())
            # LLM 실패 시에도 HOLD로 기록해 거래일 추적 유지
            try:
                portfolio.execute("HOLD", float(price_df.loc[:pd.Timestamp(ts.date()), "Close"].iloc[-1]),
                                  ts.date(), f"LLM 오류로 인한 강제 HOLD: {exc}")
                decision = Decision(action="HOLD", reasoning=f"LLM 오류: {exc}", analysis="")
            except Exception:
                logger.exception("Fallback HOLD also failed on %s", ts.date())

        if progress_callback and decision:
            try:
                progress_callback(
                    day_index=i + 1,
                    total_days=total_days,
                    current_date=ts.date(),
                    action=decision.action,
                    reasoning=decision.reasoning,
                )
            except Exception:
                logger.exception("progress_callback error on %s", ts.date())

    # 최종 결과
    backtest_df = price_df.loc[
        (price_df.index >= pd.Timestamp(start)) &
        (price_df.index <= pd.Timestamp(end))
    ]
    all_trades = portfolio.get_all_trades()
    equity_curve = compute_equity_curve(all_trades, backtest_df, initial_cash)
    benchmark = compute_benchmark(backtest_df, initial_cash)
    perf = compute_performance(equity_curve, initial_cash)

    chart_path = f"{chart_dir}/performance_{symbol}_{start}_{end}.png"
    try:
        plot_performance(equity_curve, benchmark, all_trades, chart_path)
    except Exception:
        logger.exception("Failed to generate performance chart")
        chart_path = None

    bm_return = (
        (float(benchmark.iloc[-1]) - initial_cash) / initial_cash * 100
        if not benchmark.empty else 0.0
    )
    basic = portfolio.get_returns(float(backtest_df["Close"].iloc[-1]), initial_cash)
    _print_summary(symbol, stock_name, start, end, basic, perf, bm_return, chart_path)

    # equity curve / benchmark curve — 비교 차트용 직렬화
    equity_curve_data = [{"date": str(d), "value": float(v)} for d, v in equity_curve.items()]
    benchmark_curve_data = [{"date": str(d), "value": float(v)} for d, v in benchmark.items()]

    return {
        **basic,
        **perf,
        "benchmark_return_pct": round(bm_return, 2),
        "equity_curve": equity_curve_data,
        "benchmark_curve": benchmark_curve_data,
    }


def _print_summary(
    symbol: str,
    stock_name: str,
    start: date,
    end: date,
    basic: dict,
    perf: dict,
    bm_return: float,
    chart_path: str | None,
) -> None:
    W = 52
    print("\n" + "=" * W)
    print(f"  백테스팅 결과: {symbol} [{stock_name}] ({start} ~ {end})")
    print("=" * W)
    print(f"  최종 자산:          {basic.get('total_value', 0):>16,.0f}원")
    print(f"  총 수익률:          {perf.get('total_return_pct', 0):>+15.2f}%")
    print(f"  연간 환산 수익률:   {perf.get('annualized_return_pct', 0):>+15.2f}%")
    print(f"  Sharpe Ratio:       {perf.get('sharpe_ratio', 0):>16.3f}")
    print(f"  Calmar Ratio:       {perf.get('calmar_ratio', 0):>16.3f}")
    print(f"  Sortino Ratio:      {perf.get('sortino_ratio', 0):>16.3f}")
    print(f"  최대 낙폭 (MDD):   {perf.get('max_drawdown_pct', 0):>+15.2f}%")
    print(f"  연간 변동성:        {perf.get('volatility_annual_pct', 0):>15.2f}%")
    print("-" * W)
    print(f"  Buy & Hold 수익률: {bm_return:>+15.2f}%")
    print(f"  초과 수익률:        {perf.get('total_return_pct', 0) - bm_return:>+15.2f}%")
    print("-" * W)
    print(f"  매수 횟수:          {basic.get('buy_count', 0):>16}")
    print(f"  매도 횟수:          {basic.get('sell_count', 0):>16}")
    print(f"  홀드 횟수:          {basic.get('hold_count', 0):>16}")
    if chart_path:
        print(f"  성과 차트:          {chart_path}")
    print("=" * W)


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinAgent 백테스팅")
    parser.add_argument("--symbol",      required=True, help="KRX 종목코드 (예: 005930)")
    parser.add_argument("--stock-name",  required=True, help="한글 종목명 (예: 삼성전자)")
    parser.add_argument("--start",       required=True, help="시작일 YYYY-MM-DD")
    parser.add_argument("--end",         required=True, help="종료일 YYYY-MM-DD")
    parser.add_argument("--initial-cash", type=float, default=10_000_000, help="초기 자금 (기본: 10,000,000)")
    parser.add_argument("--preference",  default="moderate",
                        choices=["aggressive", "moderate", "conservative"],
                        help="트레이더 성향")
    parser.add_argument("--db-path",     default="portfolio.db")
    parser.add_argument("--memory-dir",  default="memory_db")
    parser.add_argument("--chart-dir",   default="charts")
    parser.add_argument("--trace-dir",   default=None, help="워크플로우 트레이스 저장 디렉토리")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    results = run_backtest(
        symbol=args.symbol,
        stock_name=args.stock_name,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        initial_cash=args.initial_cash,
        trader_preference=args.preference,
        db_path=args.db_path,
        memory_dir=args.memory_dir,
        chart_dir=args.chart_dir,
        trace_dir=args.trace_dir,
    )
    sys.exit(0 if results else 1)
