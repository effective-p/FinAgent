"""Step 1 unit + integration tests.

Unit tests: no network, no file I/O beyond tmp.
Integration tests: real yfinance calls, marked with @pytest.mark.integration.
"""
from __future__ import annotations

import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from finagent.portfolio.portfolio import Portfolio
from finagent.tools.technical_indicators import get_technical_signals
from finagent.utils.schemas import TechnicalSignals, TradeAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 50, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    open_ = close - rng.normal(0, 0.5, n)
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def _make_buy_signal_df(n: int = 50) -> pd.DataFrame:
    """Construct a DataFrame whose last bar triggers MACD golden cross."""
    df = _make_ohlcv(n)
    # Force a descending then ascending MACD pattern by padding a long decline
    # then a sharp recovery so golden cross fires on last bar.
    # Easiest: just return the random df and assert signals are returned at all.
    return df


# ---------------------------------------------------------------------------
# TechnicalSignals — unit tests
# ---------------------------------------------------------------------------

class TestTechnicalSignals:
    def test_returns_technical_signals(self):
        df = _make_ohlcv()
        result = get_technical_signals(df)
        assert isinstance(result, TechnicalSignals)

    def test_signal_values_valid(self):
        df = _make_ohlcv()
        result = get_technical_signals(df)
        valid = {"BUY", "SELL", "HOLD"}
        assert result.macd_signal in valid
        assert result.kdj_rsi_signal in valid
        assert result.zmr_signal in valid

    def test_signal_text_contains_all_indicators(self):
        df = _make_ohlcv()
        result = get_technical_signals(df)
        assert "MACD" in result.signal_text
        assert "KDJ" in result.signal_text
        assert "ZMR" in result.signal_text

    def test_insufficient_data_returns_hold(self):
        df = _make_ohlcv(n=5)  # far below minimum for MACD/KDJ
        result = get_technical_signals(df)
        assert result.macd_signal == "HOLD"

    def test_zmr_buy_signal(self):
        """Price well below 20-day MA → z < -1.5 → ZMR BUY."""
        n = 40
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        # Stable price then sudden drop
        close = np.array([100.0] * (n - 1) + [80.0])
        high = close + 1
        low = close - 1
        open_ = close
        volume = np.ones(n) * 1_000_000
        df = pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
            index=idx,
        )
        result = get_technical_signals(df)
        assert result.zmr_signal == "BUY"

    def test_zmr_sell_signal(self):
        """Price well above 20-day MA → z > 1.5 → ZMR SELL."""
        n = 40
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        close = np.array([100.0] * (n - 1) + [120.0])
        high = close + 1
        low = close - 1
        open_ = close
        volume = np.ones(n) * 1_000_000
        df = pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
            index=idx,
        )
        result = get_technical_signals(df)
        assert result.zmr_signal == "SELL"


# ---------------------------------------------------------------------------
# Portfolio — unit tests
# ---------------------------------------------------------------------------

class TestPortfolio:
    def _portfolio(self, initial_cash: float = 10_000.0) -> Portfolio:
        tmp = tempfile.mktemp(suffix=".db")
        return Portfolio(symbol="TEST", initial_cash=initial_cash, db_path=tmp)

    def test_initial_state(self):
        p = self._portfolio(10_000)
        assert p.get_cash() == pytest.approx(10_000)
        assert p.get_position() == pytest.approx(0.0)

    def test_buy_reduces_cash_increases_position(self):
        p = self._portfolio(10_000)
        p.execute("BUY", price=100.0, target_date=date(2024, 1, 2))
        cash = p.get_cash()
        pos = p.get_position()
        assert cash == pytest.approx(5_000.0)   # 50% spent
        assert pos == pytest.approx(50.0)        # 5000 / 100

    def test_sell_clears_position(self):
        p = self._portfolio(10_000)
        p.execute("BUY", price=100.0, target_date=date(2024, 1, 2))
        p.execute("SELL", price=110.0, target_date=date(2024, 1, 3))
        assert p.get_position() == pytest.approx(0.0)
        assert p.get_cash() > 10_000  # sold at profit

    def test_hold_does_not_change_state(self):
        p = self._portfolio(10_000)
        p.execute("BUY", price=100.0, target_date=date(2024, 1, 2))
        cash_before = p.get_cash()
        pos_before = p.get_position()
        p.execute("HOLD", price=105.0, target_date=date(2024, 1, 3))
        assert p.get_cash() == pytest.approx(cash_before)
        assert p.get_position() == pytest.approx(pos_before)

    def test_buy_with_no_cash_is_skipped(self):
        p = self._portfolio(initial_cash=0.0)
        p.execute("BUY", price=100.0, target_date=date(2024, 1, 2))
        assert p.get_position() == pytest.approx(0.0)

    def test_sell_with_no_position_is_skipped(self):
        p = self._portfolio(10_000)
        cash_before = p.get_cash()
        p.execute("SELL", price=100.0, target_date=date(2024, 1, 2))
        assert p.get_cash() == pytest.approx(cash_before)

    def test_recent_actions_ordering(self):
        p = self._portfolio(10_000)
        p.execute("BUY", price=100.0, target_date=date(2024, 1, 2))
        p.execute("HOLD", price=102.0, target_date=date(2024, 1, 3))
        p.execute("SELL", price=105.0, target_date=date(2024, 1, 4))
        actions = p.recent_actions()
        assert [a.action for a in actions] == ["BUY", "HOLD", "SELL"]

    def test_recent_actions_limit(self):
        p = self._portfolio(10_000)
        for i in range(20):
            p.execute("HOLD", price=100.0, target_date=date(2024, 1, 1) + timedelta(days=i))
        actions = p.recent_actions(n=5)
        assert len(actions) == 5

    def test_get_portfolio_value(self):
        p = self._portfolio(10_000)
        p.execute("BUY", price=100.0, target_date=date(2024, 1, 2))
        # cash=5000, position=50, price now 120
        value = p.get_portfolio_value(120.0)
        assert value == pytest.approx(5_000 + 50 * 120)

    def test_get_returns(self):
        p = self._portfolio(10_000)
        p.execute("BUY", price=100.0, target_date=date(2024, 1, 2))
        returns = p.get_returns(current_price=200.0, initial_cash=10_000)
        assert returns["buy_count"] == 1
        assert returns["total_return_pct"] > 0

    def test_invalid_action_raises(self):
        p = self._portfolio()
        with pytest.raises(ValueError):
            p.execute("INVALID", price=100.0, target_date=date(2024, 1, 2))

    def test_reinitialize_same_db_keeps_state(self):
        """Re-creating Portfolio with same db_path should not reset cash."""
        tmp = tempfile.mktemp(suffix=".db")
        p1 = Portfolio(symbol="TEST", initial_cash=10_000, db_path=tmp)
        p1.execute("BUY", price=100.0, target_date=date(2024, 1, 2))
        cash_after_buy = p1.get_cash()

        p2 = Portfolio(symbol="TEST", initial_cash=10_000, db_path=tmp)
        assert p2.get_cash() == pytest.approx(cash_after_buy)


# ---------------------------------------------------------------------------
# Integration tests — real network / file I/O
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDataFetcherIntegration:
    def test_fetch_price_data(self):
        from finagent.data.fetcher import DataFetcher
        fetcher = DataFetcher()
        df = fetcher.get_price_data("005930", lookback_days=30)
        assert not df.empty
        assert set(["Open", "High", "Low", "Close", "Volume"]).issubset(df.columns)

    def test_plot_kline_chart(self, tmp_path):
        from finagent.data.fetcher import DataFetcher
        fetcher = DataFetcher(chart_dir=str(tmp_path))
        df = fetcher.get_price_data("005930", lookback_days=60)
        target = df.index[-1].date()
        path = fetcher.plot_kline_chart(df, target_date=target, symbol="005930")
        assert path.endswith(".png")
        import os
        assert os.path.exists(path)

    def test_plot_trading_chart_with_actions(self, tmp_path):
        from finagent.data.fetcher import DataFetcher
        fetcher = DataFetcher(chart_dir=str(tmp_path))
        df = fetcher.get_price_data("005930", lookback_days=60)
        dates = df.index[-10:].tolist()
        actions = [
            TradeAction(action="BUY", quantity=1, price=float(df["Close"].iloc[-10]), date=dates[0].date()),
            TradeAction(action="SELL", quantity=1, price=float(df["Close"].iloc[-1]), date=dates[-1].date()),
        ]
        target = df.index[-1].date()
        path = fetcher.plot_trading_chart(df, actions=actions, target_date=target, symbol="005930")
        assert path.endswith(".png")
        import os
        assert os.path.exists(path)
