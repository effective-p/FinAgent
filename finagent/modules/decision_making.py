from __future__ import annotations

import logging
from datetime import date

import anthropic
import pandas as pd

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

_MODEL = "claude-sonnet-4-6"

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

위 분석을 종합하여 오늘의 거래 결정을 내리세요.
action은 반드시 BUY, SELL, HOLD 중 하나여야 합니다.
다른 텍스트 없이 아래 XML 형식으로만 응답하세요.

<output>
  <action>BUY 또는 SELL 또는 HOLD</action>
  <reasoning>결정 근거 (한국어, 2-4문장)</reasoning>
</output>"""


class DecisionMakingModule:
    """MI + LLR + HLR + 기술적 지표를 종합해 BUY / SELL / HOLD를 결정한다."""

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
        mi_result: MIResult,
        llr_result: LLRResult,
        hlr_result: HLRResult,
        portfolio_state: PortfolioState,
        trader_preference: str = "moderate",
    ) -> Decision:
        # 기술적 지표 계산
        tech_signals = get_technical_signals(price_df)

        # 프롬프트 구성 및 Claude 호출
        decision = self._decide(
            symbol=symbol,
            target_date=target_date,
            tech_signals=tech_signals.signal_text,
            mi_result=mi_result,
            llr_result=llr_result,
            hlr_result=hlr_result,
            portfolio_state=portfolio_state,
            trader_preference=trader_preference,
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
        )

        response = self._client.messages.create(
            model=self.model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        logger.debug("DM raw response: %s", raw[:200])

        fields = parse_output(raw, "action", "reasoning")
        action = fields["action"].strip().upper()
        if action not in ("BUY", "SELL", "HOLD"):
            logger.warning("Unexpected action '%s', defaulting to HOLD", action)
            action = "HOLD"

        return Decision(
            action=action,
            reasoning=fields["reasoning"] or raw,
        )
