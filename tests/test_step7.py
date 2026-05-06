"""Step 7 — metrics (equity curve, performance, plot) 테스트."""
from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from finagent.utils.metrics import (
    compute_benchmark,
    compute_equity_curve,
    compute_performance,
    plot_performance,
)
from finagent.utils.schemas import TradeAction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _price_df(n: int = 60, start_price: float = 70_000.0, drift: float = 100.0) -> pd.DataFrame:
    """단조 상승 가격 데이터 (테스트 결과 예측을 쉽게 하기 위해)."""
    rng = np.random.default_rng(42)
    close = start_price + drift * np.arange(n) + rng.normal(0, 200, n)
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": close - 50,
            "High": close + 300,
            "Low": close - 300,
            "Close": close,
            "Volume": np.ones(n) * 1_000_000,
        },
        index=idx,
    )


def _trade(action: str, d: date, price: float = 70_000.0, qty: float = 1.0) -> TradeAction:
    return TradeAction(action=action, quantity=qty, price=price, date=d)


# ---------------------------------------------------------------------------
# compute_equity_curve
# ---------------------------------------------------------------------------

class TestComputeEquityCurve:
    def test_no_trades_returns_initial_cash_throughout(self):
        df = _price_df(20)
        curve = compute_equity_curve([], df, initial_cash=10_000_000)
        assert len(curve) == 20
        assert all(v == pytest.approx(10_000_000) for v in curve.values)

    def test_length_matches_price_df(self):
        df = _price_df(30)
        curve = compute_equity_curve([], df, initial_cash=5_000_000)
        assert len(curve) == 30

    def test_buy_reduces_cash_increases_value_when_price_rises(self):
        df = _price_df(20, start_price=100.0, drift=10.0)
        buy_date = df.index[0].date()
        buy_price = float(df["Close"].iloc[0])
        trades = [_trade("BUY", buy_date, price=buy_price)]

        curve = compute_equity_curve(trades, df, initial_cash=1_000_000, buy_ratio=0.5)
        # 첫날 이후 가격이 오르므로 마지막 날 가치 > 초기 자금
        assert curve.iloc[-1] > 1_000_000

    def test_sell_converts_position_to_cash(self):
        df = _price_df(20, start_price=100.0, drift=0.0)
        buy_date = df.index[0].date()
        sell_date = df.index[5].date()
        buy_price = float(df["Close"].iloc[0])
        sell_price = float(df["Close"].iloc[5])

        trades = [
            _trade("BUY", buy_date, price=buy_price),
            _trade("SELL", sell_date, price=sell_price),
        ]
        curve = compute_equity_curve(trades, df, initial_cash=1_000_000, buy_ratio=0.5)
        # SELL 이후 가격 변동과 무관하게 가치 고정
        post_sell = curve.iloc[6:]
        assert post_sell.std() == pytest.approx(0.0, abs=1e-3)

    def test_hold_action_does_not_affect_state(self):
        df = _price_df(10, start_price=100.0, drift=0.0)
        curve_no_hold = compute_equity_curve([], df, 1_000_000)
        hold_trades = [_trade("HOLD", df.index[3].date(), price=100.0, qty=0.0)]
        curve_with_hold = compute_equity_curve(hold_trades, df, 1_000_000)
        pd.testing.assert_series_equal(curve_no_hold, curve_with_hold)

    def test_index_is_date_type(self):
        df = _price_df(10)
        curve = compute_equity_curve([], df, 1_000_000)
        assert isinstance(curve.index[0], date)


# ---------------------------------------------------------------------------
# compute_benchmark
# ---------------------------------------------------------------------------

class TestComputeBenchmark:
    def test_length_matches_price_df(self):
        df = _price_df(30)
        bm = compute_benchmark(df, 1_000_000)
        assert len(bm) == 30

    def test_first_value_equals_initial_cash(self):
        df = _price_df(20)
        bm = compute_benchmark(df, 1_000_000)
        assert bm.iloc[0] == pytest.approx(1_000_000)

    def test_rising_price_yields_positive_return(self):
        df = _price_df(30, start_price=100.0, drift=10.0)
        bm = compute_benchmark(df, 1_000_000)
        assert bm.iloc[-1] > bm.iloc[0]

    def test_empty_df_returns_empty_series(self):
        bm = compute_benchmark(pd.DataFrame(), 1_000_000)
        assert bm.empty


# ---------------------------------------------------------------------------
# compute_performance
# ---------------------------------------------------------------------------

class TestComputePerformance:
    def test_empty_returns_empty_dict(self):
        assert compute_performance(pd.Series(dtype=float), 1_000_000) == {}

    def test_positive_return(self):
        curve = pd.Series([1_000_000.0 * (1.01 ** i) for i in range(252)])
        perf = compute_performance(curve, 1_000_000)
        assert perf["total_return_pct"] > 0
        assert perf["annualized_return_pct"] > 0

    def test_total_return_calculation(self):
        curve = pd.Series([1_000_000.0, 1_100_000.0])
        perf = compute_performance(curve, 1_000_000)
        assert perf["total_return_pct"] == pytest.approx(10.0)

    def test_negative_return(self):
        curve = pd.Series([1_000_000.0 * (0.995 ** i) for i in range(100)])
        perf = compute_performance(curve, 1_000_000)
        assert perf["total_return_pct"] < 0

    def test_max_drawdown_negative(self):
        # 절반으로 떨어졌다가 회복
        vals = [100.0] * 20 + [50.0] * 20 + [100.0] * 20
        curve = pd.Series([v * 10_000 for v in vals])
        perf = compute_performance(curve, 1_000_000)
        assert perf["max_drawdown_pct"] < -40  # 약 -50%

    def test_sharpe_positive_for_consistent_gains(self):
        curve = pd.Series([1_000_000.0 * (1.001 ** i) for i in range(252)])
        perf = compute_performance(curve, 1_000_000)
        assert perf["sharpe_ratio"] > 0

    def test_all_keys_present(self):
        curve = pd.Series([1_000_000.0] * 50)
        perf = compute_performance(curve, 1_000_000)
        for key in ["total_return_pct", "annualized_return_pct", "sharpe_ratio",
                    "max_drawdown_pct", "volatility_annual_pct", "final_value", "n_trading_days"]:
            assert key in perf

    def test_flat_curve_zero_return(self):
        curve = pd.Series([1_000_000.0] * 100)
        perf = compute_performance(curve, 1_000_000)
        assert perf["total_return_pct"] == pytest.approx(0.0)
        assert perf["max_drawdown_pct"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# plot_performance
# ---------------------------------------------------------------------------

class TestPlotPerformance:
    def test_creates_png_file(self):
        df = _price_df(60)
        curve = compute_equity_curve([], df, 1_000_000)
        benchmark = compute_benchmark(df, 1_000_000)
        tmp = tempfile.mkdtemp()
        out = str(Path(tmp) / "perf.png")
        result = plot_performance(curve, benchmark, [], out)
        assert result == out
        assert Path(out).exists()
        assert Path(out).stat().st_size > 0

    def test_creates_png_with_trades(self):
        df = _price_df(60)
        trades = [
            _trade("BUY",  df.index[5].date(),  float(df["Close"].iloc[5])),
            _trade("SELL", df.index[20].date(), float(df["Close"].iloc[20])),
        ]
        curve = compute_equity_curve(trades, df, 1_000_000)
        benchmark = compute_benchmark(df, 1_000_000)
        tmp = tempfile.mkdtemp()
        out = str(Path(tmp) / "perf_trades.png")
        plot_performance(curve, benchmark, trades, out)
        assert Path(out).exists()


# ---------------------------------------------------------------------------
# Portfolio.get_all_trades 단위 테스트
# ---------------------------------------------------------------------------

class TestPortfolioGetAllTrades:
    def test_returns_trades_in_order(self):
        import tempfile
        from finagent.portfolio.portfolio import Portfolio

        db = tempfile.mktemp(suffix=".db")
        p = Portfolio("005930", 10_000_000, db_path=db)
        p.execute("BUY",  70_000, date(2024, 1, 2))
        p.execute("HOLD", 71_000, date(2024, 1, 3))
        p.execute("SELL", 75_000, date(2024, 1, 10))

        trades = p.get_all_trades()
        assert [t.action for t in trades] == ["BUY", "HOLD", "SELL"]

    def test_empty_portfolio_returns_empty(self):
        import tempfile
        from finagent.portfolio.portfolio import Portfolio

        db = tempfile.mktemp(suffix=".db")
        p = Portfolio("005930", 10_000_000, db_path=db)
        assert p.get_all_trades() == []
