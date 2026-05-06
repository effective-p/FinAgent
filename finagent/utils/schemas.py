from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from pydantic import BaseModel


class NewsItem(BaseModel):
    title: str
    summary: str
    published: datetime
    url: str


class TechnicalSignals(BaseModel):
    macd_signal: str      # "BUY" | "SELL" | "HOLD"
    kdj_rsi_signal: str
    zmr_signal: str
    signal_text: str      # LLM 프롬프트에 주입할 최종 텍스트


class TradeAction(BaseModel):
    action: str           # "BUY" | "SELL" | "HOLD"
    quantity: float
    price: float
    date: date
    reasoning: str = ""


class PortfolioState(BaseModel):
    symbol: str
    position: float
    cash: float
    total_value: float


class MIResult(BaseModel):
    latest_summary: str       # 최신 뉴스+가격 분석 요약 (trading용)
    past_summary: str         # 과거 MI 요약 (메모리에서 검색)
    short_term_query: str     # 단기(1-5일) retrieval 쿼리
    medium_term_query: str    # 중기(1-4주) retrieval 쿼리
    long_term_query: str      # 장기(1-3개월) retrieval 쿼리


class LLRResult(BaseModel):
    short_term_reasoning: str   # 단기(1-5일) 가격 변동 이유 분석
    medium_term_reasoning: str  # 중기(1-4주) 가격 변동 이유 분석
    long_term_reasoning: str    # 장기(1-3개월) 가격 변동 이유 분석
    query: str                  # HLR retrieval용 쿼리


class HLRResult(BaseModel):
    reasoning: str    # 과거 거래 결정들의 종합 평가
    improvement: str  # 개선 방안 제시
    summary: str      # 메모리 저장용 요약
    query: str        # DecisionMaking retrieval용 쿼리


class Decision(BaseModel):
    action: str      # "BUY" | "SELL" | "HOLD"
    reasoning: str   # 결정 근거
