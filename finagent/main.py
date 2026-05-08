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
import logging
import sys
from datetime import date

import pandas as pd

from finagent.data.fetcher import DataFetcher
from finagent.memory.store import MemoryStore
from finagent.modules.decision_making import DecisionMakingModule
from finagent.modules.high_level_reflection import HighLevelReflectionModule
from finagent.modules.low_level_reflection import LowLevelReflectionModule
from finagent.modules.market_intelligence import MarketIntelligenceModule
from finagent.portfolio.portfolio import Portfolio
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

    # 1. 데이터 수집
    _step("news_fetch")
    news = fetcher.get_news(symbol, stock_name, target_date)
    kline_path = fetcher.plot_kline_chart(df, target_date, symbol)
    trading_path = fetcher.plot_trading_chart(
        df, portfolio.recent_actions(14), target_date, symbol,
    )

    # 2. Market Intelligence
    _step("market_intelligence")
    mi_result = mi_module.run(symbol, target_date, df, news)

    # 3. Low-Level Reflection
    _step("low_level_reflection")
    llr_result = llr_module.run(symbol, target_date, df, kline_path, mi_result)

    # 4. High-Level Reflection
    _step("high_level_reflection")
    hlr_result = hlr_module.run(
        symbol, target_date, trading_path,
        portfolio.recent_actions(14), mi_result, llr_result,
    )

    # 5. Decision Making
    _step("decision_making")
    portfolio_state = portfolio.get_state(current_price)
    decision = dm_module.run(
        symbol, target_date, df,
        mi_result, llr_result, hlr_result,
        portfolio_state, trader_preference,
    )

    # 6. 거래 실행
    _step("trade_execution")
    portfolio.execute(decision.action, current_price, target_date, decision.reasoning)

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
    initial_cash: float = 10_000_000,
    trader_preference: str = "moderate",
    db_path: str = "portfolio.db",
    memory_dir: str = "memory_db",
    chart_dir: str = "charts",
    progress_callback=None,
    step_callback=None,
) -> dict:
    """start ~ end 기간 동안 백테스팅을 실행하고 결과를 반환한다.

    Args:
        progress_callback: 각 거래일 완료 시 호출되는 콜백.
            signature: (day_index, total_days, current_date, action, reasoning) -> None
        step_callback: 각 파이프라인 단계 시작 시 호출되는 콜백.
            signature: (step_name: str) -> None
    """
    fetcher = DataFetcher(chart_dir=chart_dir)
    memory = MemoryStore(persist_dir=memory_dir)
    portfolio = Portfolio(symbol=symbol, initial_cash=initial_cash, db_path=db_path)

    mi_module = MarketIntelligenceModule(memory=memory)
    llr_module = LowLevelReflectionModule(memory=memory)
    hlr_module = HighLevelReflectionModule(memory=memory)
    dm_module = DecisionMakingModule(memory=memory)

    # 전체 기간 + 충분한 lookback 한 번에 수집
    lookback_days = (end - start).days + 90
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
            )
        except Exception:
            logger.exception("Error on %s, skipping day", ts.date())

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
    return {**basic, **perf, "benchmark_return_pct": round(bm_return, 2)}


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
    )
    sys.exit(0 if results else 1)
