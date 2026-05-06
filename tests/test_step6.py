"""Step 6 — DecisionMakingModule + 파이프라인 통합 테스트."""
from __future__ import annotations

import base64
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from finagent.memory.store import MemoryStore
from finagent.modules.decision_making import DecisionMakingModule
from finagent.utils.schemas import (
    Decision,
    HLRResult,
    LLRResult,
    MIResult,
    PortfolioState,
    TradeAction,
)

_MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQAABjE+ibYAAAAASUVORK5CYII="
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _store() -> MemoryStore:
    return MemoryStore(persist_dir=tempfile.mkdtemp())


def _price_df(n: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 70_000 + np.cumsum(rng.normal(0, 300, n))
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": close - 100,
            "High": close + 500,
            "Low": close - 500,
            "Close": close,
            "Volume": rng.integers(5_000_000, 20_000_000, n).astype(float),
        },
        index=idx,
    )


def _mi() -> MIResult:
    return MIResult(
        latest_summary="반도체 업황 개선으로 긍정적 흐름.",
        past_summary="과거 기록 없음",
        short_term_query="삼성전자 단기 반등",
        medium_term_query="반도체 중기 수급",
        long_term_query="삼성전자 장기 전망",
    )


def _llr() -> LLRResult:
    return LLRResult(
        short_term_reasoning="단기 양봉 패턴으로 매수세 확인.",
        medium_term_reasoning="20일선 위 유지.",
        long_term_reasoning="반도체 업황 회복 사이클.",
        query="삼성전자 반등 패턴",
    )


def _hlr() -> HLRResult:
    return HLRResult(
        reasoning="이전 매수 결정은 적절했음.",
        improvement="분할 매수 전략 고려.",
        summary="목표가 달성 매도는 적절.",
        query="삼성전자 거래 결정 평가",
    )


def _portfolio_state() -> PortfolioState:
    return PortfolioState(
        symbol="005930",
        position=0.0,
        cash=10_000_000,
        total_value=10_000_000,
    )


def _mock_client(action: str = "BUY", reasoning: str = "기술적 지표 종합 매수 신호.") -> MagicMock:
    xml = f"<output><action>{action}</action><reasoning>{reasoning}</reasoning></output>"
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=xml)]
    client = MagicMock()
    client.messages.create.return_value = mock_msg
    return client


# ---------------------------------------------------------------------------
# DecisionMakingModule 단위 테스트
# ---------------------------------------------------------------------------

class TestDecisionMakingModule:
    def _module(self, action: str = "BUY") -> tuple[DecisionMakingModule, MemoryStore]:
        store = _store()
        module = DecisionMakingModule(memory=store)
        module._client = _mock_client(action)
        return module, store

    def test_run_returns_decision(self):
        module, _ = self._module()
        result = module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        assert isinstance(result, Decision)

    def test_run_action_buy(self):
        module, _ = self._module("BUY")
        result = module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        assert result.action == "BUY"

    def test_run_action_sell(self):
        module, _ = self._module("SELL")
        result = module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        assert result.action == "SELL"

    def test_run_action_hold(self):
        module, _ = self._module("HOLD")
        result = module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        assert result.action == "HOLD"

    def test_run_reasoning_populated(self):
        module, _ = self._module()
        result = module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        assert len(result.reasoning) > 0

    def test_run_invalid_action_defaults_to_hold(self):
        store = _store()
        module = DecisionMakingModule(memory=store)
        module._client = _mock_client(action="INVALID_ACTION")
        result = module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        assert result.action == "HOLD"

    def test_run_all_preferences(self):
        for pref in ["aggressive", "moderate", "conservative"]:
            module, _ = self._module()
            result = module.run(
                "005930", date(2024, 2, 15), _price_df(),
                _mi(), _llr(), _hlr(), _portfolio_state(), pref,
            )
            assert result.action in ("BUY", "SELL", "HOLD")

    def test_run_calls_claude_once(self):
        module, _ = self._module()
        module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        module._client.messages.create.assert_called_once()

    def test_run_prompt_contains_tech_signals(self):
        module, _ = self._module()
        module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        prompt = module._client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "MACD" in prompt or "ZMR" in prompt

    def test_run_prompt_contains_portfolio_state(self):
        module, _ = self._module()
        module.run(
            "005930", date(2024, 2, 15), _price_df(),
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        prompt = module._client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "10,000,000" in prompt


# ---------------------------------------------------------------------------
# 파이프라인 통합 단위 테스트 (모든 Claude 모듈 mock)
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    """run_day를 모든 Claude 모듈을 mock으로 교체해 테스트한다."""

    def _make_modules(self, decision_action: str = "BUY"):
        store = _store()
        tmp = tempfile.mkdtemp()

        mi = MagicMock()
        mi.run.return_value = _mi()

        llr = MagicMock()
        llr.run.return_value = _llr()

        hlr = MagicMock()
        hlr.run.return_value = _hlr()

        dm = DecisionMakingModule(memory=store)
        dm._client = _mock_client(decision_action)

        fetcher = MagicMock()
        fetcher.get_news.return_value = []
        fetcher.plot_kline_chart.return_value = _make_dummy_png(tmp, "kline.png")
        fetcher.plot_trading_chart.return_value = _make_dummy_png(tmp, "trading.png")

        return mi, llr, hlr, dm, fetcher, store

    def test_run_day_returns_decision(self):
        from finagent.main import run_day
        from finagent.portfolio.portfolio import Portfolio

        mi, llr, hlr, dm, fetcher, _ = self._make_modules("BUY")
        portfolio = Portfolio("005930", 10_000_000, db_path=tempfile.mktemp(suffix=".db"))

        result = run_day(
            symbol="005930",
            stock_name="삼성전자",
            target_date=date(2024, 2, 15),
            price_df=_price_df(),
            fetcher=fetcher,
            portfolio=portfolio,
            mi_module=mi,
            llr_module=llr,
            hlr_module=hlr,
            dm_module=dm,
        )
        assert isinstance(result, Decision)
        assert result.action in ("BUY", "SELL", "HOLD")

    def test_run_day_buy_changes_portfolio(self):
        from finagent.main import run_day
        from finagent.portfolio.portfolio import Portfolio

        mi, llr, hlr, dm, fetcher, _ = self._make_modules("BUY")
        portfolio = Portfolio("005930", 10_000_000, db_path=tempfile.mktemp(suffix=".db"))
        initial_cash = portfolio.get_cash()

        run_day(
            symbol="005930",
            stock_name="삼성전자",
            target_date=date(2024, 2, 15),
            price_df=_price_df(),
            fetcher=fetcher,
            portfolio=portfolio,
            mi_module=mi,
            llr_module=llr,
            hlr_module=hlr,
            dm_module=dm,
        )
        assert portfolio.get_cash() < initial_cash
        assert portfolio.get_position() > 0

    def test_run_day_calls_all_modules(self):
        from finagent.main import run_day
        from finagent.portfolio.portfolio import Portfolio

        mi, llr, hlr, dm, fetcher, _ = self._make_modules()
        portfolio = Portfolio("005930", 10_000_000, db_path=tempfile.mktemp(suffix=".db"))

        run_day(
            symbol="005930",
            stock_name="삼성전자",
            target_date=date(2024, 2, 15),
            price_df=_price_df(),
            fetcher=fetcher,
            portfolio=portfolio,
            mi_module=mi,
            llr_module=llr,
            hlr_module=hlr,
            dm_module=dm,
        )
        mi.run.assert_called_once()
        llr.run.assert_called_once()
        hlr.run.assert_called_once()


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _make_dummy_png(tmp_dir: str, filename: str) -> str:
    path = Path(tmp_dir) / filename
    path.write_bytes(_MINIMAL_PNG)
    return str(path)


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDecisionMakingIntegration:
    def test_run_real(self):
        from finagent.data.fetcher import DataFetcher
        store = _store()
        module = DecisionMakingModule(memory=store)
        fetcher = DataFetcher()
        price_df = fetcher.get_price_data("005930", lookback_days=60)
        result = module.run(
            "005930", price_df.index[-1].date(), price_df,
            _mi(), _llr(), _hlr(), _portfolio_state(),
        )
        assert result.action in ("BUY", "SELL", "HOLD")
        assert len(result.reasoning) > 0
