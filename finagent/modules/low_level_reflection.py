from __future__ import annotations

import base64
import logging
from datetime import date
from typing import Dict, List, Optional

import anthropic
import pandas as pd

from finagent.memory.store import MemoryStore
from finagent.utils.schemas import LLRResult, MIResult
from finagent.utils.xml_parser import parse_output

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

_ANALYZE_PROMPT = """\
당신은 전문 주식 차트 분석가입니다.
첨부된 Kline(캔들스틱) 차트와 아래 데이터를 종합해서 가격 변동의 원인을 분석하세요.

[종목코드]
{symbol}

[분석 기준일]
{target_date}

[가격 변동률]
{price_changes_text}

[Market Intelligence 요약]
{mi_latest}

[과거 Low-Level Reflection 참고]
{past_llr_text}

차트를 직접 보고 캔들 패턴, 거래량 변화, 지지/저항 구간 등을 파악하여 아래 XML 형식으로만 응답하세요.
다른 텍스트는 절대 포함하지 마세요.

<output>
  <short_term_reasoning>단기(1-5일) 가격 변동 원인 분석 (캔들 패턴, 단기 수급 중심, 한국어 2-3문장)</short_term_reasoning>
  <medium_term_reasoning>중기(1-4주) 가격 변동 원인 분석 (추세, 지지/저항 중심, 한국어 2-3문장)</medium_term_reasoning>
  <long_term_reasoning>장기(1-3개월) 가격 변동 원인 분석 (구조적 흐름, 매크로 중심, 한국어 2-3문장)</long_term_reasoning>
  <query>이 분석 결과를 미래에 검색할 때 쓸 쿼리 (한국어, 1문장)</query>
</output>"""


class LowLevelReflectionModule:
    """Kline 차트 Vision + 가격 변동 데이터로 단/중/장기 가격 변동 원인을 분석한다."""

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
        kline_image_path: str,
        mi_result: MIResult,
    ) -> LLRResult:
        # 1. 가격 변동률 계산
        price_changes = _calc_price_changes(price_df, target_date)
        price_changes_text = _format_price_changes(price_changes)

        # 2. 과거 LLR 검색 (MI 단기 쿼리 활용)
        past_docs = self.memory.retrieve(
            "low_level_reflection",
            mi_result.short_term_query,
            top_k=3,
        )
        past_llr_text = _format_past_docs(past_docs)

        # 3. Claude Vision 분석
        result = self._analyze(
            symbol=symbol,
            target_date=target_date,
            price_changes_text=price_changes_text,
            mi_result=mi_result,
            past_llr_text=past_llr_text,
            kline_image_path=kline_image_path,
        )

        # 4. 메모리 저장 (세 reasoning을 하나의 문서로)
        doc_text = (
            f"단기: {result.short_term_reasoning}\n"
            f"중기: {result.medium_term_reasoning}\n"
            f"장기: {result.long_term_reasoning}"
        )
        self.memory.add(
            "low_level_reflection",
            doc_text,
            {"symbol": symbol, "date": target_date.isoformat()},
        )

        logger.info(
            "LLR run complete for %s on %s | past_docs=%d",
            symbol, target_date, len(past_docs),
        )
        return result

    # ------------------------------------------------------------------
    # 내부 메서드
    # ------------------------------------------------------------------

    def _analyze(
        self,
        symbol: str,
        target_date: date,
        price_changes_text: str,
        mi_result: MIResult,
        past_llr_text: str,
        kline_image_path: str,
    ) -> LLRResult:
        with open(kline_image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        prompt = _ANALYZE_PROMPT.format(
            symbol=symbol,
            target_date=target_date.isoformat(),
            price_changes_text=price_changes_text,
            mi_latest=mi_result.latest_summary,
            past_llr_text=past_llr_text,
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
        logger.debug("LLR raw response: %s", raw[:200])

        fields = parse_output(
            raw,
            "short_term_reasoning",
            "medium_term_reasoning",
            "long_term_reasoning",
            "query",
        )
        return LLRResult(
            short_term_reasoning=fields["short_term_reasoning"] or raw,
            medium_term_reasoning=fields["medium_term_reasoning"] or raw,
            long_term_reasoning=fields["long_term_reasoning"] or raw,
            query=fields["query"] or f"{symbol} 가격 변동 원인",
        )


# ------------------------------------------------------------------
# 헬퍼
# ------------------------------------------------------------------

def _calc_price_changes(df: pd.DataFrame, target_date: date) -> Dict[str, Optional[float]]:
    """target_date 기준 1d / 5d / 10d / 20d 변동률(%)을 계산한다."""
    target_ts = pd.Timestamp(target_date)
    idx = df.index.searchsorted(target_ts, side="right") - 1
    if idx < 0:
        return {}

    current = float(df["Close"].iloc[idx])
    changes: Dict[str, Optional[float]] = {"current_price": current}
    for label, n in [("1d", 1), ("5d", 5), ("10d", 10), ("20d", 20)]:
        if idx >= n:
            past = float(df["Close"].iloc[idx - n])
            changes[label] = round((current - past) / past * 100, 2)
        else:
            changes[label] = None
    return changes


def _format_price_changes(changes: Dict[str, Optional[float]]) -> str:
    if not changes:
        return "가격 데이터 없음"
    lines = [f"현재가: {changes.get('current_price', 'N/A'):,.0f}원"]
    for label, name in [("1d", "1일"), ("5d", "5일"), ("10d", "10일"), ("20d", "20일")]:
        val = changes.get(label)
        if val is not None:
            lines.append(f"{name} 변동: {val:+.2f}%")
    return "\n".join(lines)


def _format_past_docs(docs: List[str]) -> str:
    if not docs:
        return "과거 Low-Level Reflection 기록 없음"
    lines = ["[과거 분석 참고]"]
    for i, doc in enumerate(docs, 1):
        lines.append(f"{i}. {doc}")
    return "\n".join(lines)
