"""Step 3 — MarketIntelligenceModule + xml_parser 테스트."""
from __future__ import annotations

import tempfile
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from finagent.memory.store import MemoryStore
from finagent.modules.market_intelligence import (
    MarketIntelligenceModule,
    _format_news,
    _format_past_docs,
    _format_price,
)
from finagent.utils.schemas import MIResult, NewsItem
from finagent.utils.xml_parser import parse_field, parse_output


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _store() -> MemoryStore:
    return MemoryStore(persist_dir=tempfile.mkdtemp())


def _price_df(n: int = 20) -> pd.DataFrame:
    rng = np.random.default_rng(0)
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


def _news_list() -> list[NewsItem]:
    return [
        NewsItem(
            title="삼성전자 반도체 수출 호조",
            summary="HBM 수요 증가로 2분기 실적 기대감 상승",
            published=datetime(2024, 1, 15),
            url="https://example.com/1",
        ),
        NewsItem(
            title="외국인 순매수 지속",
            summary="코스피 외국인 3일 연속 순매수",
            published=datetime(2024, 1, 14),
            url="https://example.com/2",
        ),
    ]


_MOCK_XML = """\
<output>
  <summary>삼성전자는 반도체 수출 호조와 외국인 순매수 지속으로 긍정적인 흐름을 보이고 있습니다.</summary>
  <short_term_query>삼성전자 단기 반등 외국인 매수</short_term_query>
  <medium_term_query>삼성전자 반도체 HBM 중기 수급</medium_term_query>
  <long_term_query>삼성전자 장기 실적 반도체 업황</long_term_query>
</output>"""


def _mock_client(xml_body: str = _MOCK_XML):
    """anthropic.Anthropic()를 대체하는 mock 클라이언트."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=xml_body)]
    client = MagicMock()
    client.messages.create.return_value = mock_msg
    return client


# ---------------------------------------------------------------------------
# xml_parser 단위 테스트
# ---------------------------------------------------------------------------

class TestXmlParser:
    def test_parse_simple_tag(self):
        xml = "<output><summary>테스트 요약</summary></output>"
        assert parse_field(xml, "summary") == "테스트 요약"

    def test_parse_string_name_tag(self):
        xml = '<output><string name="summary">테스트 요약</string></output>'
        assert parse_field(xml, "summary") == "테스트 요약"

    def test_parse_missing_tag_returns_empty(self):
        assert parse_field("<output></output>", "nonexistent") == ""

    def test_parse_output_multiple_fields(self):
        result = parse_output(
            _MOCK_XML,
            "summary", "short_term_query", "medium_term_query", "long_term_query",
        )
        assert "삼성전자" in result["summary"]
        assert result["short_term_query"] != ""
        assert result["medium_term_query"] != ""
        assert result["long_term_query"] != ""

    def test_parse_strips_whitespace(self):
        xml = "<output><summary>  \n  공백 테스트  \n  </summary></output>"
        assert parse_field(xml, "summary") == "공백 테스트"

    def test_parse_multiline_value(self):
        xml = "<output><summary>첫 번째 줄\n두 번째 줄</summary></output>"
        result = parse_field(xml, "summary")
        assert "첫 번째 줄" in result
        assert "두 번째 줄" in result


# ---------------------------------------------------------------------------
# 포맷 헬퍼 단위 테스트
# ---------------------------------------------------------------------------

class TestFormatHelpers:
    def test_format_news_empty(self):
        assert _format_news([]) == "뉴스 없음"

    def test_format_news_includes_title(self):
        text = _format_news(_news_list())
        assert "삼성전자 반도체 수출 호조" in text
        assert "2024-01-15" in text

    def test_format_price_includes_change(self):
        text = _format_price(_price_df())
        assert "변동" in text

    def test_format_past_docs_empty(self):
        assert "없음" in _format_past_docs([])

    def test_format_past_docs_numbered(self):
        text = _format_past_docs(["문서1", "문서2"])
        assert "1." in text
        assert "2." in text


# ---------------------------------------------------------------------------
# MarketIntelligenceModule 단위 테스트 (Claude API mock)
# ---------------------------------------------------------------------------

class TestMarketIntelligenceModule:
    def _module(self, xml_body: str = _MOCK_XML) -> tuple[MarketIntelligenceModule, MemoryStore]:
        store = _store()
        module = MarketIntelligenceModule(memory=store)
        module._client = _mock_client(xml_body)
        return module, store

    def test_run_returns_mi_result(self):
        module, _ = self._module()
        result = module.run("005930", date(2024, 1, 15), _price_df(), _news_list())
        assert isinstance(result, MIResult)

    def test_run_latest_summary_populated(self):
        module, _ = self._module()
        result = module.run("005930", date(2024, 1, 15), _price_df(), _news_list())
        assert len(result.latest_summary) > 0
        assert "삼성전자" in result.latest_summary

    def test_run_queries_populated(self):
        module, _ = self._module()
        result = module.run("005930", date(2024, 1, 15), _price_df(), _news_list())
        assert len(result.short_term_query) > 0
        assert len(result.medium_term_query) > 0
        assert len(result.long_term_query) > 0

    def test_run_stores_to_memory(self):
        module, store = self._module()
        module.run("005930", date(2024, 1, 15), _price_df(), _news_list())
        assert store.count("market_intelligence") == 1

    def test_run_past_summary_empty_on_first_call(self):
        module, _ = self._module()
        result = module.run("005930", date(2024, 1, 15), _price_df(), _news_list())
        assert "없음" in result.past_summary

    def test_run_past_summary_filled_on_second_call(self):
        module, _ = self._module()
        module.run("005930", date(2024, 1, 15), _price_df(), _news_list())
        result = module.run("005930", date(2024, 1, 16), _price_df(), _news_list())
        assert "없음" not in result.past_summary

    def test_run_calls_claude_once(self):
        module, _ = self._module()
        module.run("005930", date(2024, 1, 15), _price_df(), _news_list())
        module._client.messages.create.assert_called_once()

    def test_run_fallback_on_malformed_xml(self):
        """XML 파싱 실패 시에도 MIResult를 반환해야 한다."""
        module, _ = self._module(xml_body="Claude의 자유 형식 응답입니다. XML 없음.")
        result = module.run("005930", date(2024, 1, 15), _price_df(), _news_list())
        assert isinstance(result, MIResult)
        assert len(result.latest_summary) > 0  # fallback 원문

    def test_run_no_news(self):
        module, _ = self._module()
        result = module.run("005930", date(2024, 1, 15), _price_df(), news_list=[])
        assert isinstance(result, MIResult)


# ---------------------------------------------------------------------------
# Integration test — 실제 Claude API 호출
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMarketIntelligenceIntegration:
    def test_run_real(self):
        store = _store()
        module = MarketIntelligenceModule(memory=store)
        from finagent.data.fetcher import DataFetcher
        fetcher = DataFetcher()
        price_df = fetcher.get_price_data("005930", lookback_days=30)
        result = module.run("005930", date(2024, 6, 28), price_df, [])
        assert isinstance(result, MIResult)
        assert len(result.latest_summary) > 0
        assert store.count("market_intelligence") == 1
