from __future__ import annotations

import logging
from datetime import date
from typing import List

import anthropic
import pandas as pd

from finagent.memory.store import MemoryStore
from finagent.utils.schemas import MIResult, NewsItem
from finagent.utils.xml_parser import parse_output

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

_ANALYZE_PROMPT = """\
당신은 전문 주식 시장 분석가입니다. 아래 데이터를 바탕으로 종목의 현재 시장 상황을 분석하세요.

[종목코드]
{symbol}

[분석 기준일]
{target_date}

[최근 뉴스]
{news_text}

[최근 가격 데이터 (최근 10거래일)]
{price_text}

다음 XML 형식으로만 응답하세요. 설명이나 추가 텍스트는 절대 포함하지 마세요.

<output>
  <summary>트레이딩 의사결정에 활용할 종합 시장 분석 요약 (3-5문장, 한국어)</summary>
  <short_term_query>단기(1-5일) 관점으로 과거 유사 상황을 검색할 쿼리 (한국어, 1-2문장)</short_term_query>
  <medium_term_query>중기(1-4주) 관점으로 과거 유사 상황을 검색할 쿼리 (한국어, 1-2문장)</medium_term_query>
  <long_term_query>장기(1-3개월) 관점으로 과거 유사 상황을 검색할 쿼리 (한국어, 1-2문장)</long_term_query>
</output>"""


class MarketIntelligenceModule:
    """최신 뉴스·가격 분석 + 과거 MI Diversified Retrieval."""

    def __init__(
        self,
        memory: MemoryStore,
        model: str = _MODEL,
    ) -> None:
        self.memory = memory
        self.model = model
        self._client = anthropic.Anthropic()

    def run(
        self,
        symbol: str,
        target_date: date,
        price_df: pd.DataFrame,
        news_list: List[NewsItem],
    ) -> MIResult:
        # 1. 최신 뉴스+가격 분석
        latest_summary, queries = self._analyze_latest(symbol, target_date, price_df, news_list)

        # 2. Diversified Retrieval — 과거 MI 3가지 관점으로 검색
        past_docs = self.memory.diversified_retrieve(
            "market_intelligence",
            list(queries.values()),
            top_k_each=2,
        )

        # 3. 과거 MI 포맷팅
        past_summary = _format_past_docs(past_docs)

        # 4. 최신 MI를 메모리에 저장
        self.memory.add(
            "market_intelligence",
            latest_summary,
            {
                "symbol": symbol,
                "date": target_date.isoformat(),
                "short_term_query": queries["short_term_query"],
                "medium_term_query": queries["medium_term_query"],
                "long_term_query": queries["long_term_query"],
            },
        )

        logger.info(
            "MI run complete for %s on %s | past_docs=%d",
            symbol, target_date, len(past_docs),
        )

        return MIResult(
            latest_summary=latest_summary,
            past_summary=past_summary,
            short_term_query=queries["short_term_query"],
            medium_term_query=queries["medium_term_query"],
            long_term_query=queries["long_term_query"],
        )

    # ------------------------------------------------------------------
    # 내부 메서드
    # ------------------------------------------------------------------

    def _analyze_latest(
        self,
        symbol: str,
        target_date: date,
        price_df: pd.DataFrame,
        news_list: List[NewsItem],
    ) -> tuple[str, dict[str, str]]:
        news_text = _format_news(news_list)
        price_text = _format_price(price_df)

        prompt = _ANALYZE_PROMPT.format(
            symbol=symbol,
            target_date=target_date.isoformat(),
            news_text=news_text,
            price_text=price_text,
        )

        response = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        logger.debug("MI raw response: %s", raw[:200])

        fields = parse_output(raw, "summary", "short_term_query", "medium_term_query", "long_term_query")
        summary = fields["summary"] or raw  # 파싱 실패 시 원문 fallback
        queries = {
            "short_term_query": fields["short_term_query"] or f"{symbol} 단기 가격 변동",
            "medium_term_query": fields["medium_term_query"] or f"{symbol} 중기 추세",
            "long_term_query": fields["long_term_query"] or f"{symbol} 장기 전망",
        }
        return summary, queries


# ------------------------------------------------------------------
# 포맷 헬퍼
# ------------------------------------------------------------------

def _format_news(news_list: List[NewsItem]) -> str:
    if not news_list:
        return "뉴스 없음"
    lines = []
    for i, n in enumerate(news_list, 1):
        date_str = n.published.strftime("%Y-%m-%d")
        lines.append(f"{i}. [{date_str}] {n.title}\n   {n.summary}")
    return "\n".join(lines)


def _format_price(df: pd.DataFrame, n: int = 10) -> str:
    tail = df.tail(n).copy()
    if tail.empty:
        return "가격 데이터 없음"

    first_close = tail["Close"].iloc[0]
    last_close = tail["Close"].iloc[-1]
    change_pct = (last_close - first_close) / first_close * 100

    lines = [f"{'날짜':<12} {'시가':>8} {'고가':>8} {'저가':>8} {'종가':>8} {'거래량':>12}"]
    for idx, row in tail.iterrows():
        date_str = str(idx)[:10]
        lines.append(
            f"{date_str:<12} {row['Open']:>8.0f} {row['High']:>8.0f} "
            f"{row['Low']:>8.0f} {row['Close']:>8.0f} {row['Volume']:>12.0f}"
        )
    lines.append(f"\n최근 {n}거래일 변동: {change_pct:+.2f}%")
    return "\n".join(lines)


def _format_past_docs(docs: List[str]) -> str:
    if not docs:
        return "과거 Market Intelligence 기록 없음"
    lines = ["[과거 Market Intelligence 요약]"]
    for i, doc in enumerate(docs, 1):
        lines.append(f"{i}. {doc}")
    return "\n".join(lines)
