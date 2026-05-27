"""Thread-local LLM call trace capture.

Usage in run_day():
    begin_trace("market_intelligence")
    result = module.run(...)
    calls = end_trace()   # returns list of captured call dicts
"""
from __future__ import annotations

import threading
import time

_local = threading.local()


def begin_trace(step: str) -> None:
    _local.active = True
    _local.step = step
    _local.calls = []


def end_trace() -> list[dict]:
    calls = list(getattr(_local, "calls", []))
    _local.active = False
    _local.calls = []
    return calls


def record_chat(messages: list[dict], response: str, model: str, temperature: float) -> None:
    if not getattr(_local, "active", False):
        return
    _local.calls.append({
        "type": "chat",
        "messages": messages,
        "response": response,
        "model": model,
        "temperature": temperature,
        "ts": time.time(),
    })


def record_image_chat(prompt: str, response: str, model: str, temperature: float) -> None:
    """chat_with_image 호출을 기록한다 (base64 이미지 데이터는 저장하지 않음)."""
    if not getattr(_local, "active", False):
        return
    _local.calls.append({
        "type": "chat_with_image",
        "prompt": prompt,
        "has_image": True,
        "response": response,
        "model": model,
        "temperature": temperature,
        "ts": time.time(),
    })
