from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from finagent.llm.client import LLMClient
from finagent.memory.store import MemoryStore
from finagent.tools.technical_indicators import get_technical_signals
from finagent.utils.schemas import (
    Decision,
    HLRResult,
    LLRResult,
    MIResult,
    PortfolioState,
)
from finagent.utils.xml_parser import parse_output

logger = logging.getLogger(__name__)

_PREFERENCE_TEXT = {
    "aggressive":    "공격적 (수익 극대화 우선, 높은 리스크 허용)",
    "moderate":      "중립적 (수익과 리스크의 균형)",
    "conservative":  "보수적 (리스크 최소화 우선, 안정성 중시)",
}

_DECIDE_PROMPT = """\
당신은 전문 주식 트레이더입니다.
아래 종합 분석을 바탕으로 오늘의 거래 결정을 내리세요.

[종목코드]
{symbol}

[분석 기준일]
{target_date}

[트레이더 성향]
{preference_text}

[현재 포트폴리오 상태]
현금: {cash:,.0f}원 | 보유 수량: {position:.4f} | 총 자산: {total_value:,.0f}원

[기술적 지표 시그널]
{tech_signals}

[Market Intelligence]
최신 분석: {mi_latest}
과거 패턴: {mi_past}

[Low-Level Reflection — 가격 변동 분석]
단기: {llr_short}
중기: {llr_medium}
장기: {llr_long}

[High-Level Reflection — 과거 결정 평가]
평가: {hlr_reasoning}
개선점: {hlr_improvement}

[전문가 가이던스 — PER/PBR/배당 기반 투자 신호]
{fundamental_guidance}

위 분석을 종합하여 오늘의 거래 결정을 내리세요.

[중요 제약 조건]
- 현금이 현재 주가보다 적으면 BUY 불가
- 보유 수량이 0이면 SELL 불가

action은 반드시 BUY, SELL, HOLD 중 하나여야 합니다.
다른 텍스트 없이 아래 XML 형식으로만 응답하세요.

<output>
  <analysis>각 데이터(MI/LLR/HLR/기술지표)가 결정에 미치는 영향 단계별 분석 (한국어, 3-5문장)</analysis>
  <action>BUY 또는 SELL 또는 HOLD</action>
  <reasoning>최종 결정 근거 요약 (한국어, 2-3문장)</reasoning>
</output>"""


class DecisionMakingModule:
    """MI + LLR + HLR + 기술적 지표를 종합해 BUY / SELL / HOLD를 결정한다."""

    def __init__(
        self,
        memory: MemoryStore,
    ) -> None:
        self.memory = memory
        self._llm = LLMClient()

    def run(
        self,
        symbol: str,
        target_date: date,
        price_df: pd.DataFrame,
        mi_result: MIResult,
        llr_result: LLRResult,
        hlr_result: HLRResult,
        portfolio_state: PortfolioState,
        trader_preference: str = "moderate",
        fundamental_guidance: str = "",
    ) -> Decision:
        tech_signals = get_technical_signals(price_df)
        decision = self._decide(
            symbol=symbol,
            target_date=target_date,
            tech_signals=tech_signals.signal_text,
            mi_result=mi_result,
            llr_result=llr_result,
            hlr_result=hlr_result,
            portfolio_state=portfolio_state,
            trader_preference=trader_preference,
            fundamental_guidance=fundamental_guidance,
        )

        logger.info(
            "Decision for %s on %s: %s",
            symbol, target_date, decision.action,
        )
        return decision

    # ------------------------------------------------------------------
    # 내부 메서드
    # ------------------------------------------------------------------

    def _decide(
        self,
        symbol: str,
        target_date: date,
        tech_signals: str,
        mi_result: MIResult,
        llr_result: LLRResult,
        hlr_result: HLRResult,
        portfolio_state: PortfolioState,
        trader_preference: str,
        fundamental_guidance: str = "",
    ) -> Decision:
        preference_text = _PREFERENCE_TEXT.get(trader_preference, _PREFERENCE_TEXT["moderate"])

        prompt = _DECIDE_PROMPT.format(
            symbol=symbol,
            target_date=target_date.isoformat(),
            preference_text=preference_text,
            cash=portfolio_state.cash,
            position=portfolio_state.position,
            total_value=portfolio_state.total_value,
            tech_signals=tech_signals,
            mi_latest=mi_result.latest_summary,
            mi_past=mi_result.past_summary,
            llr_short=llr_result.short_term_reasoning,
            llr_medium=llr_result.medium_term_reasoning,
            llr_long=llr_result.long_term_reasoning,
            hlr_reasoning=hlr_result.reasoning,
            hlr_improvement=hlr_result.improvement,
            fundamental_guidance=fundamental_guidance or "기본 투자지표 데이터 없음",
        )

        raw = self._llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=512,
        )
        logger.debug("DM raw response: %s", raw[:200])

        fields = parse_output(raw, "analysis", "action", "reasoning")
        action = fields["action"].strip().upper()
        if action not in ("BUY", "SELL", "HOLD"):
            logger.warning("Unexpected action '%s', defaulting to HOLD", action)
            action = "HOLD"

        return Decision(
            action=action,
            reasoning=fields["reasoning"] or raw,
            analysis=fields["analysis"] or "",
        )
