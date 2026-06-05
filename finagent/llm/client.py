"""LLM 클라이언트 추상화 — OpenAI / Anthropic / Gemini / Ollama 통합 인터페이스.

명시적 config 전달 또는 .env 환경변수 fallback을 지원한다.
Ollama는 네이티브 /api/chat 엔드포인트를 사용해 think:false 속도 최적화를 적용한다.
"""
from __future__ import annotations

import json
import os
import logging
import urllib.request
from typing import List, Optional

from finagent.llm.trace import record_chat, record_image_chat

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "gemini":    "gemini-2.0-flash",
    "ollama":    "gemma3:latest",
}

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_OLLAMA_BASE_URL = "http://localhost:11434"


class LLMClient:
    """Provider-agnostic LLM client."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.provider = (provider or os.getenv("LLM_PROVIDER", "openai")).lower()
        if self.provider not in _DEFAULTS:
            raise ValueError(
                f"Unknown LLM provider='{self.provider}'. 지원: {list(_DEFAULTS.keys())}"
            )
        self.model = model or os.getenv("FINAGENT_MODEL", _DEFAULTS[self.provider])
        self.temperature = float(os.getenv("FINAGENT_TEMPERATURE", "0"))
        self._api_key = api_key
        # Ollama: base_url은 /v1 없이 host:port 형태로 저장
        if self.provider == "ollama":
            base = (base_url or _OLLAMA_BASE_URL).rstrip("/")
            self._ollama_base = base.replace("/v1", "")  # /api/chat 경로용
        self._base_url = base_url
        self._client = self._build_client()
        logger.info("LLMClient: provider=%s model=%s", self.provider, self.model)

    def _build_client(self):
        if self.provider == "openai":
            import openai
            return openai.OpenAI(api_key=self._api_key) if self._api_key else openai.OpenAI()

        if self.provider == "anthropic":
            import anthropic
            return anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()

        if self.provider == "gemini":
            import openai as _openai
            key = self._api_key or os.environ["GEMINI_API_KEY"]
            return _openai.OpenAI(api_key=key, base_url=_GEMINI_BASE_URL)

        # Ollama: 네이티브 API 사용이므로 OpenAI 클라이언트 불필요
        return None

    def _ollama_chat(self, messages: List[dict], images: Optional[List[str]] = None) -> str:
        """Ollama 네이티브 /api/chat 엔드포인트 호출 (think:false로 속도 최적화)."""
        ollama_messages = []
        for m in messages:
            msg: dict = {"role": m["role"]}
            content = m.get("content", "")
            if isinstance(content, list):
                # OpenAI 형식 멀티모달 → Ollama 형식 변환
                texts, imgs = [], []
                for part in content:
                    if part.get("type") == "text":
                        texts.append(part["text"])
                    elif part.get("type") == "image_url":
                        url = part["image_url"]["url"]
                        if url.startswith("data:image/"):
                            imgs.append(url.split(",", 1)[1])
                msg["content"] = "\n".join(texts)
                if imgs:
                    msg["images"] = imgs
            else:
                msg["content"] = content
            if images and m["role"] == "user":
                msg["images"] = images
            ollama_messages.append(msg)

        payload = json.dumps({
            "model": self.model,
            "messages": ollama_messages,
            "think": False,
            "stream": False,
            "options": {
                "num_ctx": 4096,
                "temperature": self.temperature,
            },
        }).encode("utf-8")

        url = f"{self._ollama_base}/api/chat"
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())

        return data.get("message", {}).get("content", "") or ""

    def chat(self, messages: List[dict], max_tokens: int = 1024) -> str:
        if self.provider == "ollama":
            result = self._ollama_chat(messages)
            record_chat(messages, result, self.model, self.temperature)
            return result

        if self.provider == "anthropic":
            # Claude 4.x(예: claude-opus-4-8)는 temperature 파라미터를 더 이상 받지 않아 생략
            resp = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, messages=messages,
            )
            result = resp.content[0].text
            record_chat(messages, result, self.model, self.temperature)
            return result

        resp = self._client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            temperature=self.temperature, messages=messages,
        )
        result = resp.choices[0].message.content or ""
        record_chat(messages, result, self.model, self.temperature)
        return result

    def chat_with_image(self, prompt: str, image_b64: str, max_tokens: int = 2048) -> str:
        if self.provider == "ollama":
            messages = [{"role": "user", "content": prompt}]
            result = self._ollama_chat(messages, images=[image_b64])
            record_image_chat(prompt, result, self.model, self.temperature)
            return result

        if self.provider == "anthropic":
            messages = [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }]
            # Claude 4.x는 temperature 미지원 — 생략
            resp = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, messages=messages,
            )
            result = resp.content[0].text
            record_image_chat(prompt, result, self.model, self.temperature)
            return result

        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }]
        resp = self._client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            temperature=self.temperature, messages=messages,
        )
        result = resp.choices[0].message.content or ""
        record_image_chat(prompt, result, self.model, self.temperature)
        return result
