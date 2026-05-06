"""Step 5 — HighLevelReflectionModule 테스트."""
from __future__ import annotations

import base64
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from finagent.memory.store import MemoryStore
from finagent.modules.high_level_reflection import (
    HighLevelReflectionModule,
    _format_actions,
)
from finagent.utils.schemas import HLRResult, LLRResult, MIResult, TradeAction

# ---------------------------------------------------------------------------
# 최소 유효 PNG (1×1 white)
# ---------------------------------------------------------------------------
_MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQAABjE+ibYAAAAASUVORK5CYII="
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _store() -> MemoryStore:
    return MemoryStore(persist_dir=tempfile.mkdtemp())


def _mi_result() -> MIResult:
    return MIResult(
        latest_summary="반도체 업황 개선으로 긍정적 흐름 지속.",
        past_summary="과거 기록 없음",
        short_term_query="삼성전자 단기 반등",
        medium_term_query="반도체 중기 수급",
        long_term_query="삼성전자 장기 전망",
    )


def _llr_result() -> LLRResult:
    return LLRResult(
        short_term_reasoning="양봉 연속 출현으로 단기 매수세 확인.",
        medium_term_reasoning="20일선 위 유지로 중기 상승 추세 유효.",
        long_term_reasoning="반도체 업황 회복 사이클 진입.",
        query="삼성전자 캔들 패턴 반등",
    )


def _past_actions() -> list[TradeAction]:
    return [
        TradeAction(action="BUY",  quantity=1.43, price=70_000, date=date(2024, 1, 2),  reasoning="업황 개선 기대"),
        TradeAction(action="HOLD", quantity=0.0,  price=71_000, date=date(2024, 1, 3),  reasoning="추세 확인"),
        TradeAction(action="SELL", quantity=1.43, price=75_000, date=date(2024, 1, 10), reasoning="목표가 도달"),
    ]


def _dummy_chart(tmp_dir: str) -> str:
    path = Path(tmp_dir) / "trading_test.png"
    path.write_bytes(_MINIMAL_PNG)
    return str(path)


_MOCK_XML = """\
<output>
  <reasoning>매수 진입 후 목표가 도달로 매도한 결정은 적절했습니다. 다만 홀드 구간에서 추가 매수 기회를 놓쳤습니다.</reasoning>
  <improvement>다음 거래에서는 분할 매수 전략을 통해 평균 단가를 낮추는 방식을 고려해야 합니다.</improvement>
  <summary>목표가 달성 매도는 적절했으나 홀드 구간 추가 매수 기회 미활용.</summary>
  <query>삼성전자 목표가 달성 매도 전략 평가</query>
</output>"""


def _mock_client(xml_body: str = _MOCK_XML) -> MagicMock:
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=xml_body)]
    client = MagicMock()
    client.messages.create.return_value = mock_msg
    return client


# ---------------------------------------------------------------------------
# _format_actions 단위 테스트
# ---------------------------------------------------------------------------

class TestFormatActions:
    def test_empty_returns_no_history(self):
        assert _format_actions([]) == "거래 내역 없음"

    def test_includes_action_and_price(self):
        text = _format_actions(_past_actions())
        assert "BUY" in text
        assert "SELL" in text
        assert "70,000" in text

    def test_includes_reasoning(self):
        text = _format_actions(_past_actions())
        assert "업황 개선 기대" in text

    def test_row_count(self):
        text = _format_actions(_past_actions())
        data_rows = [l for l in text.splitlines() if "BUY" in l or "SELL" in l or "HOLD" in l]
        assert len(data_rows) == 3


# ---------------------------------------------------------------------------
# HighLevelReflectionModule 단위 테스트
# ---------------------------------------------------------------------------

class TestHighLevelReflectionModule:
    def _module(self, xml_body: str = _MOCK_XML):
        store = _store()
        module = HighLevelReflectionModule(memory=store)
        module._client = _mock_client(xml_body)
        return module, store

    def test_run_returns_hlr_result(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        assert isinstance(result, HLRResult)

    def test_run_all_fields_populated(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        assert len(result.reasoning) > 0
        assert len(result.improvement) > 0
        assert len(result.summary) > 0
        assert len(result.query) > 0

    def test_run_reasoning_content(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        assert "매수" in result.reasoning or "매도" in result.reasoning

    def test_run_stores_summary_to_memory(self):
        module, store = self._module()
        tmp = tempfile.mkdtemp()
        module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        assert store.count("high_level_reflection") == 1

    def test_run_stores_only_hlr_collection(self):
        module, store = self._module()
        tmp = tempfile.mkdtemp()
        module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        assert store.count("market_intelligence") == 0
        assert store.count("low_level_reflection") == 0

    def test_run_past_hlr_empty_on_first_call(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        assert isinstance(result, HLRResult)

    def test_run_accumulates_memory_across_calls(self):
        module, store = self._module()
        tmp = tempfile.mkdtemp()
        module.run(
            "005930", date(2024, 1, 14), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        assert store.count("high_level_reflection") == 2

    def test_run_passes_image_to_claude(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        call_args = module._client.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]
        types = [c["type"] for c in content]
        assert "image" in types
        assert "text" in types

    def test_run_with_no_past_actions(self):
        """거래 내역이 없어도 정상 동작해야 한다."""
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            [], _mi_result(), _llr_result(),
        )
        assert isinstance(result, HLRResult)

    def test_run_fallback_on_malformed_xml(self):
        module, _ = self._module(xml_body="XML 없는 자유 형식 응답.")
        tmp = tempfile.mkdtemp()
        result = module.run(
            "005930", date(2024, 1, 15), _dummy_chart(tmp),
            _past_actions(), _mi_result(), _llr_result(),
        )
        assert isinstance(result, HLRResult)
        assert len(result.reasoning) > 0


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestHighLevelReflectionIntegration:
    def test_run_real(self):
        from finagent.data.fetcher import DataFetcher
        from finagent.portfolio.portfolio import Portfolio

        tmp_charts = tempfile.mkdtemp()
        fetcher = DataFetcher(chart_dir=tmp_charts)
        price_df = fetcher.get_price_data("005930", lookback_days=60)
        target = price_df.index[-1].date()

        actions = [
            TradeAction(action="BUY",  quantity=1.0, price=70_000, date=price_df.index[-10].date(), reasoning="테스트 매수"),
            TradeAction(action="HOLD", quantity=0.0, price=71_000, date=price_df.index[-5].date(),  reasoning="테스트 홀드"),
        ]
        trading_path = fetcher.plot_trading_chart(price_df, actions=actions, target_date=target, symbol="005930")

        store = _store()
        module = HighLevelReflectionModule(memory=store)
        result = module.run(
            "005930", target, trading_path,
            actions, _mi_result(), _llr_result(),
        )
        assert isinstance(result, HLRResult)
        assert len(result.summary) > 0
        assert store.count("high_level_reflection") == 1
