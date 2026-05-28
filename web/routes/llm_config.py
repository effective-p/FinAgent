"""LLM 설정 등록/조회/삭제 API."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.auth import get_current_user
from web.db import get_conn

router = APIRouter(prefix="/api/llm-configs", tags=["llm_configs"])

_PROVIDERS = {"openai", "anthropic", "gemini", "ollama"}
_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-2.0-flash",
    "ollama": "gemma3:latest",
}
_DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
}


class LLMConfigIn(BaseModel):
    name: str
    provider: str
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class LLMConfigOut(BaseModel):
    id: int
    name: str
    provider: str
    model: str
    api_key_hint: str
    base_url: Optional[str] = None


@router.get("", response_model=List[LLMConfigOut])
async def list_configs(current_user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, provider, model, api_key, base_url FROM llm_configs WHERE user_id = %s ORDER BY id",
                (current_user["id"],),
            )
            rows = cur.fetchall()
    return [
        LLMConfigOut(
            id=r[0], name=r[1], provider=r[2], model=r[3],
            api_key_hint=(r[4][:6] + "***") if r[4] else "(없음)",
            base_url=r[5],
        )
        for r in rows
    ]


@router.post("", response_model=LLMConfigOut, status_code=201)
async def create_config(body: LLMConfigIn, current_user: dict = Depends(get_current_user)):
    if body.provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider는 {_PROVIDERS} 중 하나여야 합니다.")
    if body.provider != "ollama" and not body.api_key:
        raise HTTPException(status_code=400, detail="ollama 외 provider는 API Key가 필요합니다.")
    model = body.model or _DEFAULT_MODELS[body.provider]
    api_key = body.api_key or ""
    base_url = body.base_url or _DEFAULT_BASE_URLS.get(body.provider)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO llm_configs (user_id, name, provider, model, api_key, base_url) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (current_user["id"], body.name, body.provider, model, api_key, base_url),
            )
            new_id = cur.fetchone()[0]
    return LLMConfigOut(id=new_id, name=body.name, provider=body.provider, model=model,
                        api_key_hint=(api_key[:6] + "***") if api_key else "(없음)",
                        base_url=base_url)


@router.delete("/{config_id}", status_code=204)
async def delete_config(config_id: int, current_user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM llm_configs WHERE id = %s AND user_id = %s",
                (config_id, current_user["id"]),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="설정을 찾을 수 없습니다.")
