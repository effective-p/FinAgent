"""Web API 요청/응답 Pydantic 모델."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, field_validator


class BacktestRequest(BaseModel):
    symbol: str
    stock_name: str
    start: date
    end: date
    initial_cash: float = 10_000_000
    trader_preference: str = "moderate"

    @field_validator("trader_preference")
    @classmethod
    def validate_preference(cls, v: str) -> str:
        allowed = {"aggressive", "moderate", "conservative"}
        if v not in allowed:
            raise ValueError(f"trader_preference must be one of {allowed}")
        return v

    @field_validator("end")
    @classmethod
    def validate_dates(cls, v: date, info) -> date:
        start = info.data.get("start")
        if start and v <= start:
            raise ValueError("end must be after start")
        if start and (v - start).days > 365:
            raise ValueError("Date range must not exceed 365 days")
        return v


class JobCreatedResponse(BaseModel):
    job_id: str
    stream_url: str


class BacktestResultResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None
