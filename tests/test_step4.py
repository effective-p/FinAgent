"""Step 4 — LowLevelReflectionModule 테스트."""
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
from finagent.modules.low_level_reflection import (
    LowLevelReflectionModule,
    _calc_price_changes,
    _format_price_changes,
)
from finagent.utils.schemas import LLRResult, MIResult

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


def _price_df(n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(1)
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


def _mi_result() -> MIResult:
    return MIResult(
        latest_summary="반도체 업황 개선으로 주가 상승세 지속.",
        past_summary="과거 기록 없음",
        short_term_query="삼성전자 단기 반등",
        medium_term_query="반도체 중기 수급",
        long_term_query="삼성전자 장기 전망",
    )


def _dummy_kline(tmp_dir: str) -> str:
    path = Path(tmp_dir) / "kline_test.png"
    path.write_bytes(_MINIMAL_PNG)
    return str(path)


_MOCK_XML = """\
<output>
  <short_term_reasoning>단기적으로 양봉 연속 출현 및 거래량 증가로 매수세 유입이 확인됩니다.</short_term_reasoning>
  <medium_term_reasoning>20일 이동평균 위에서 지지받으며 중기 상승 추세가 유지되고 있습니다.</medium_term_reasoning>
  <long_term_reasoning>반도체 업황 회복 사이클 진입으로 장기 상승 추세가 지속될 것으로 보입니다.</long_term_reasoning>
  <query>삼성전자 캔들 패턴 매수세 반등</query>
</output>"""


def _mock_client(xml_body: str = _MOCK_XML) -> MagicMock:
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=xml_body)]
    client = MagicMock()
    client.messages.create.return_value = mock_msg
    return client


# ---------------------------------------------------------------------------
# _calc_price_changes 단위 테스트
# ---------------------------------------------------------------------------

class TestCalcPriceChanges:
    def test_returns_current_price(self):
        df = _price_df(30)
        target = df.index[-1].date()
        changes = _calc_price_changes(df, target)
        assert "current_price" in changes
        assert changes["current_price"] == pytest.approx(float(df["Close"].iloc[-1]))

    def test_returns_all_horizons(self):
        df = _price_df(30)
        target = df.index[-1].date()
        changes = _calc_price_changes(df, target)
        assert "1d" in changes
        assert "5d" in changes
        assert "10d" in changes
        assert "20d" in changes

    def test_none_when_insufficient_history(self):
        df = _price_df(3)
        target = df.index[-1].date()
        changes = _calc_price_changes(df, target)
        assert changes["5d"] is None
        assert changes["10d"] is None

    def test_format_price_changes(self):
        df = _price_df(30)
        target = df.index[-1].date()
        changes = _calc_price_changes(df, target)
        text = _format_price_changes(changes)
        assert "현재가" in text
        assert "1일" in text

    def test_format_empty(self):
        assert _format_price_changes({}) == "가격 데이터 없음"


# ---------------------------------------------------------------------------
# LowLevelReflectionModule 단위 테스트
# ---------------------------------------------------------------------------

class TestLowLevelReflectionModule:
    def _module(self, xml_body: str = _MOCK_XML):
        store = _store()
        module = LowLevelReflectionModule(memory=store)
        module._client = _mock_client(xml_body)
        return module, store

    def test_run_returns_llr_result(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        assert isinstance(result, LLRResult)

    def test_run_short_term_reasoning_populated(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        assert len(result.short_term_reasoning) > 0
        assert "양봉" in result.short_term_reasoning

    def test_run_all_fields_populated(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        assert len(result.medium_term_reasoning) > 0
        assert len(result.long_term_reasoning) > 0
        assert len(result.query) > 0

    def test_run_stores_to_memory(self):
        module, store = self._module()
        tmp = tempfile.mkdtemp()
        module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        assert store.count("low_level_reflection") == 1

    def test_run_stores_combined_reasoning(self):
        module, store = self._module()
        tmp = tempfile.mkdtemp()
        module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        docs = store.retrieve("low_level_reflection", "단기 중기 장기", top_k=1)
        assert "단기:" in docs[0]
        assert "중기:" in docs[0]
        assert "장기:" in docs[0]

    def test_run_past_llr_empty_on_first_call(self):
        """첫 실행 시 과거 LLR이 없어도 정상 동작해야 한다."""
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        result = module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        assert isinstance(result, LLRResult)

    def test_run_past_llr_retrieved_on_second_call(self):
        module, store = self._module()
        tmp = tempfile.mkdtemp()
        module.run("005930", date(2024, 2, 14), _price_df(), _dummy_kline(tmp), _mi_result())
        assert store.count("low_level_reflection") == 1
        module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        assert store.count("low_level_reflection") == 2

    def test_run_passes_image_to_claude(self):
        module, _ = self._module()
        tmp = tempfile.mkdtemp()
        module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        call_args = module._client.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]
        types = [c["type"] for c in content]
        assert "image" in types
        assert "text" in types

    def test_run_fallback_on_malformed_xml(self):
        module, _ = self._module(xml_body="XML 형식이 아닌 자유 텍스트 응답입니다.")
        tmp = tempfile.mkdtemp()
        result = module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        assert isinstance(result, LLRResult)
        assert len(result.short_term_reasoning) > 0

    def test_mi_collection_untouched(self):
        """LLR은 low_level_reflection 컬렉션에만 저장해야 한다."""
        module, store = self._module()
        tmp = tempfile.mkdtemp()
        module.run("005930", date(2024, 2, 15), _price_df(), _dummy_kline(tmp), _mi_result())
        assert store.count("market_intelligence") == 0
        assert store.count("high_level_reflection") == 0


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLowLevelReflectionIntegration:
    def test_run_real(self):
        from finagent.data.fetcher import DataFetcher
        fetcher = DataFetcher(chart_dir=tempfile.mkdtemp())
        price_df = fetcher.get_price_data("005930", lookback_days=60)
        target = price_df.index[-1].date()
        kline_path = fetcher.plot_kline_chart(price_df, target_date=target, symbol="005930")

        store = _store()
        module = LowLevelReflectionModule(memory=store)
        mi = _mi_result()
        result = module.run("005930", target, price_df, kline_path, mi)

        assert isinstance(result, LLRResult)
        assert len(result.short_term_reasoning) > 0
        assert store.count("low_level_reflection") == 1
