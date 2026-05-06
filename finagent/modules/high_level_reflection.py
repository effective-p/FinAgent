from __future__ import annotations

import base64
import logging
from datetime import date
from typing import List

import anthropic

from finagent.memory.store import MemoryStore
from finagent.utils.schemas import HLRResult, LLRResult, MIResult, TradeAction
from finagent.utils.xml_parser import parse_output

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

_ANALYZE_PROMPT = """\
당신은 전문 퀀트 트레이더입니다.
첨부된 Trading 차트(가격선 + 매매 마커)와 아래 데이터를 바탕으로
과거 거래 결정들의 잘잘못을 평가하고 개선 방안을 제시하세요.

[종목코드]
{symbol}

[분석 기준일]
{target_date}

[과거 거래 내역 (최근 {n_actions}건)]
{actions_text}

[Market Intelligence 요약]
{mi_latest}

[Low-Level Reflection 요약]
단기: {llr_short}
중기: {llr_medium}
장기: {llr_long}

[과거 High-Level Reflection 참고]
{past_hlr_text}

차트에서 매수(▲)/매도(▽) 마커 위치와 이후 가격 흐름을 직접 확인하여
아래 XML 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.

<output>
  <reasoning>각 거래 결정이 시장 흐름에 맞았는지 평가 (한국어, 3-5문장)</reasoning>
  <improvement>다음 거래에서 반영할 구체적인 개선 방안 (한국어, 2-4문장)</improvement>
  <summary>이번 반성의 핵심 요약 — 메모리에 저장될 내용 (한국어, 1-2문장)</summary>
  <query>이 반성 결과를 미래에 검색할 때 쓸 쿼리 (한국어, 1문장)</query>
</output>"""


class HighLevelReflectionModule:
    """Trading 차트 Vision + 과거 액션 로그로 거래 결정을 반성하고 개선점을 도출한다."""

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
        trading_chart_path: str,
        past_actions: List[TradeAction],
        mi_result: MIResult,
        llr_result: LLRResult,
    ) -> HLRResult:
        # 1. 과거 HLR 검색 (LLR 쿼리 활용)
        past_docs = self.memory.retrieve(
            "high_level_reflection",
            llr_result.query,
            top_k=3,
        )
        past_hlr_text = _format_past_docs(past_docs)

        # 2. Claude Vision 분석
        result = self._analyze(
            symbol=symbol,
            target_date=target_date,
            trading_chart_path=trading_chart_path,
            past_actions=past_actions,
            mi_result=mi_result,
            llr_result=llr_result,
            past_hlr_text=past_hlr_text,
        )

        # 3. 메모리 저장 (summary를 문서 본문으로)
        self.memory.add(
            "high_level_reflection",
            result.summary,
            {"symbol": symbol, "date": target_date.isoformat()},
        )

        logger.info(
            "HLR run complete for %s on %s | actions=%d past_docs=%d",
            symbol, target_date, len(past_actions), len(past_docs),
        )
        return result

    # ------------------------------------------------------------------
    # 내부 메서드
    # ------------------------------------------------------------------

    def _analyze(
        self,
        symbol: str,
        target_date: date,
        trading_chart_path: str,
        past_actions: List[TradeAction],
        mi_result: MIResult,
        llr_result: LLRResult,
        past_hlr_text: str,
    ) -> HLRResult:
        with open(trading_chart_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        actions_text = _format_actions(past_actions)

        prompt = _ANALYZE_PROMPT.format(
            symbol=symbol,
            target_date=target_date.isoformat(),
            n_actions=len(past_actions),
            actions_text=actions_text,
            mi_latest=mi_result.latest_summary,
            llr_short=llr_result.short_term_reasoning,
            llr_medium=llr_result.medium_term_reasoning,
            llr_long=llr_result.long_term_reasoning,
            past_hlr_text=past_hlr_text,
        )

        response = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        raw = response.content[0].text
        logger.debug("HLR raw response: %s", raw[:200])

        fields = parse_output(raw, "reasoning", "improvement", "summary", "query")
        return HLRResult(
            reasoning=fields["reasoning"] or raw,
            improvement=fields["improvement"] or raw,
            summary=fields["summary"] or raw[:200],
            query=fields["query"] or f"{symbol} 거래 결정 반성",
        )


# ------------------------------------------------------------------
# 헬퍼
# ------------------------------------------------------------------

def _format_actions(actions: List[TradeAction]) -> str:
    if not actions:
        return "거래 내역 없음"
    lines = [f"{'날짜':<12} {'액션':<5} {'가격':>10} {'수량':>8}  판단 근거"]
    lines.append("-" * 70)
    for a in actions:
        lines.append(
            f"{str(a.date):<12} {a.action:<5} {a.price:>10,.0f} {a.quantity:>8.4f}"
            f"  {a.reasoning[:60] if a.reasoning else '-'}"
        )
    return "\n".join(lines)


def _format_past_docs(docs: List[str]) -> str:
    if not docs:
        return "과거 High-Level Reflection 기록 없음"
    lines = ["[과거 반성 참고]"]
    for i, doc in enumerate(docs, 1):
        lines.append(f"{i}. {doc}")
    return "\n".join(lines)
