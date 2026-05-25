"""LLM 클라이언트 추상화 — OpenAI / Anthropic / Gemini 통합 인터페이스.

.env 설정:
    LLM_PROVIDER=openai      # openai | anthropic | gemini
    OPENAI_API_KEY=sk-proj-...
    ANTHROPIC_API_KEY=sk-ant-...
    GEMINI_API_KEY=AIza...
    FINAGENT_MODEL=gpt-4o-mini   # 선택: 기본값은 provider별 기본 모델
"""
from __future__ import annotations

import os
import logging
from typing import List

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "gemini":    "gemini-2.0-flash",
}

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class LLMClient:
    """Provider-agnostic LLM client.

    Vision(이미지 분석)은 chat_with_image()로 호출.
    3개 provider 모두 Vision 지원:
      - openai(gpt-4o-mini): image_url 포맷
      - anthropic(claude-sonnet-4-6): base64 source 포맷
      - gemini(gemini-2.0-flash): OpenAI 호환 API → image_url 포맷
    """

    def __init__(self) -> None:
        self.provider = os.getenv("LLM_PROVIDER", "openai").lower()
        if self.provider not in _DEFAULTS:
            raise ValueError(
                f"Unknown LLM_PROVIDER='{self.provider}'. "
                f"지원: {list(_DEFAULTS.keys())}"
            )
        self.model = os.getenv("FINAGENT_MODEL", _DEFAULTS[self.provider])
        self._client = self._build_client()
        logger.info("LLMClient: provider=%s model=%s", self.provider, self.model)

    def _build_client(self):
        if self.provider == "openai":
            import openai
            return openai.OpenAI()

        if self.provider == "anthropic":
            import anthropic
            return anthropic.Anthropic()

        if self.provider == "gemini":
            import openai as _openai
            return _openai.OpenAI(
                api_key=os.environ["GEMINI_API_KEY"],
                base_url=_GEMINI_BASE_URL,
            )

    def chat(self, messages: List[dict], max_tokens: int = 1024) -> str:
        """텍스트 전용 호출."""
        if self.provider == "anthropic":
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=messages,
            )
            return resp.content[0].text

        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return resp.choices[0].message.content

    def chat_with_image(
        self,
        prompt: str,
        image_b64: str,
        max_tokens: int = 2048,
    ) -> str:
        """텍스트 + base64 PNG 이미지 호출 (Vision)."""
        if self.provider == "anthropic":
            messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }]
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=messages,
            )
            return resp.content[0].text

        # openai / gemini — 동일한 image_url 포맷
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
                {"type": "text", "text": prompt},
            ],
        }]
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return resp.choices[0].message.content
