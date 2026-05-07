"""인메모리 백테스트 Job 상태 관리."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BacktestJob:
    job_id: str
    status: str = "pending"          # pending | running | done | error
    events: List[dict] = field(default_factory=list)
    result: Optional[dict] = None
    error: Optional[str] = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


_jobs: Dict[str, BacktestJob] = {}


def create_job() -> BacktestJob:
    job = BacktestJob(job_id=str(uuid.uuid4()))
    _jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> Optional[BacktestJob]:
    return _jobs.get(job_id)
