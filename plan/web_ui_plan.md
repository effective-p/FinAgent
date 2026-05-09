# FinAgent Web UI Implementation Plan

> This file is copied to `plan/web_ui_plan.md` at the start of implementation.

---

## Context

FinAgent is currently a CLI-only backtesting tool. We are adding a web-based UI so users can enter backtest parameters in a web browser, monitor execution progress in real-time, and immediately view results (returns·charts·trade history).

**Key constraint**: Up to 3~4 Claude API calls per day's backtest (`run_day`) → 3-month backtest = 30+ minutes. Without showing real-time progress in the UI, it is unusable.

---

## Technology Stack Decision

**FastAPI + SSE (Server-Sent Events) + Vanilla HTML/CSS/JS**

| Comparison | FastAPI+SSE | Streamlit |
|------|-------------|-----------|
| Long-running background tasks | Isolated with ThreadPoolExecutor, streamed via SSE | Script re-execution model — conflicts with blocking functions |
| Custom UI | Full HTML/CSS control | Limited |
| Concurrent execution isolation | Independent directory per job | Complex session state |

**Decision**: FastAPI + SSE. `workers=1` (using in-process job store).

---

## Final File Structure

```
FinAgent/
├── finagent/
│   └── main.py                    ← Modify: add progress_callback parameter
│
├── web/
│   ├── __init__.py
│   ├── app.py                     ← FastAPI app factory
│   ├── job_store.py               ← In-memory job state management
│   ├── schemas.py                 ← API request/response Pydantic models
│   └── routes/
│       ├── __init__.py
│       ├── backtest.py            ← POST /api/backtest, GET /api/backtest/{id}/stream
│       ├── results.py             ← GET /api/backtest/{id}/result + trades
│       └── charts.py              ← GET /charts/{job_id}/{filename}
│
├── web/static/
│   ├── index.html                 ← Single-page UI (form → progress → results)
│   ├── style.css
│   └── app.js                     ← SSE EventSource client
│
├── job_data/                      ← Runtime-generated (gitignore)
│   └── {job_id}/
│       ├── portfolio.db
│       ├── memory_db/
│       └── charts/
│
├── run_web.py                     ← Server entry point
└── requirements.txt               ← Add fastapi, uvicorn[standard]
```

---

## Files to Modify

### `finagent/main.py`

Add `progress_callback` parameter to `run_backtest()` signature:

```python
from typing import Callable, Optional

def run_backtest(
    symbol: str,
    stock_name: str,
    start: date,
    end: date,
    initial_cash: float = 10_000_000,
    trader_preference: str = "moderate",
    db_path: str = "portfolio.db",
    memory_dir: str = "memory_db",
    chart_dir: str = "charts",
    progress_callback: Optional[Callable] = None,  # ← NEW
) -> dict:
    ...
    total_days = len(trading_days)
    for i, ts in enumerate(trading_days):
        try:
            decision = run_day(...)
        except Exception:
            logger.exception("Error on %s, skipping day", ts.date())
            decision = None

        if progress_callback and decision:
            progress_callback(
                day_index=i + 1,
                total_days=total_days,
                current_date=ts.date(),
                action=decision.action,
                reasoning=decision.reasoning,
            )
    ...
```

`run_day()` and other modules remain unchanged.

### `requirements.txt`

```
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
```

---

## New File Details

### `web/job_store.py`

```python
import asyncio, uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class BacktestJob:
    job_id: str
    status: str = "pending"        # pending | running | done | error
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
```

### `web/schemas.py`

```python
from datetime import date
from pydantic import BaseModel

class BacktestRequest(BaseModel):
    symbol: str
    stock_name: str
    start: date
    end: date
    initial_cash: float = 10_000_000
    trader_preference: str = "moderate"   # aggressive | moderate | conservative
```

### `web/routes/backtest.py` (Core Logic)

**POST `/api/backtest`**:
1. Check `ANTHROPIC_API_KEY` existence → HTTP 400 if missing
2. Date validation (end > start, range ≤ 365 days)
3. `create_job()` → Create isolated directory per job
4. `asyncio.create_task(run_in_thread())` for async backtest execution
5. Immediately return `{job_id, stream_url}`

**GET `/api/backtest/{job_id}/stream`** (SSE):
```python
async def event_generator():
    # 1. Replay accumulated events (handle reconnection)
    for evt in job.events:
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
    # 2. Stream new events
    while True:
        evt = await asyncio.wait_for(job.queue.get(), timeout=30.0)
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        if evt.get("type") in ("done", "error"):
            break
```

**Thread → asyncio Bridge**:
```python
# progress_callback (synchronous, runs in thread)
def progress_callback(day_index, total_days, current_date, action, reasoning):
    event = {"type": "progress", "day": day_index, "total": total_days,
             "date": str(current_date), "action": action,
             "reasoning": reasoning, "pct": round(day_index/total_days*100, 1)}
    job.events.append(event)
    asyncio.get_event_loop().call_soon_threadsafe(job.queue.put_nowait, event)
```

### SSE Event Schema

```
# Progress event (once per trading day)
data: {"type":"progress","day":1,"total":62,"date":"2024-01-02","action":"HOLD","reasoning":"...","pct":1.6}

# Completion event (once)
data: {"type":"done","result":{"total_return_pct":12.5,"sharpe_ratio":1.23,...}}

# Error event
data: {"type":"error","message":"Claude API rate limit exceeded"}
```

### `web/routes/charts.py`

```python
@router.get("/charts/{job_id}/{filename}")
async def serve_chart(job_id: str, filename: str):
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    path = f"job_data/{job_id}/charts/{filename}"
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png")
```

### `web/app.py`

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from web.routes import backtest, results, charts

app = FastAPI(title="FinAgent Web UI")
app.include_router(backtest.router)
app.include_router(results.router)
app.include_router(charts.router)
app.mount("/", StaticFiles(directory="web/static", html=True), name="static")
```

### `web/static/index.html` — 3-Panel Single Page

1. **Form panel**: Input symbol, stock_name, start, end, initial_cash, preference
2. **Progress panel** (shown after submit):
   - `<progress>` bar (current day / total days)
   - Scrollable log: `date | BUY/SELL/HOLD badge | reasoning summary`
3. **Results panel** (shown when `done` event received):
   - KPI cards: total return, annual return, Sharpe Ratio, MDD, excess return vs benchmark
   - Trade counts: BUY / SELL / HOLD count
   - Performance chart image (`performance_{symbol}_{start}_{end}.png`)
   - Trade history table (date, action, price, quantity, reasoning)

### `web/static/app.js`

```javascript
function startBacktest(formData) {
    fetch('/api/backtest', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(formData)
    })
    .then(r => r.json())
    .then(({job_id, stream_url}) => {
        showProgressPanel();
        const es = new EventSource(stream_url);
        es.onmessage = (evt) => {
            const data = JSON.parse(evt.data);
            if (data.type === 'progress') {
                updateProgressBar(data.pct, data.day, data.total);
                appendDayLog(data.date, data.action, data.reasoning);
            } else if (data.type === 'done') {
                es.close();
                showResults(job_id, data.result);
            } else if (data.type === 'error') {
                es.close();
                showError(data.message);
            }
        };
    });
}
```

### `run_web.py`

```python
import uvicorn
if __name__ == "__main__":
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, workers=1)
```

---

## Job Isolation Strategy

Each backtest job uses an independent directory under `job_data/{job_id}/`:
- `portfolio.db` — Prevent SQLite concurrent write conflicts
- `memory_db/` — Prevent ChromaDB PersistentClient directory lock conflicts
- `charts/` — Prevent chart file naming conflicts

`workers=1` setting is required (in-process `_jobs` dictionary cannot be shared).

---

## Error Handling

| Situation | Handling | Reason |
|------|-----------|------|
| `ANTHROPIC_API_KEY` not set | `POST /api/backtest` → HTTP 400 immediately | Block before job creation. Failure after 30-minute task starts cannot be recovered |
| Invalid parameters | Pydantic ValidationError → HTTP 422 per-field detail | Automatic handling without route code |
| Claude API error on specific day | Existing `except Exception` skips, stream continues | One day's error should not stop the entire backtest. Maintains existing `main.py` design |
| Full backtest failure (external exception in `run_in_thread`) | Send SSE `error` event → frontend error banner | |
| SSE connection drop | `EventSource` auto-reconnect + `job.events` replay | Can view from the beginning on reconnect |
| Chart file Path Traversal attempt | HTTP 400 if `..`, `/`, `\` present | Block access to files outside `job_data/` |

---

## Implementation Order

1. `finagent/main.py` — Add `progress_callback` parameter and CLI validation
2. `web/job_store.py` + `web/schemas.py` creation
3. `web/routes/backtest.py` — SSE streaming core logic
4. `web/routes/results.py` + `web/routes/charts.py` creation
5. `web/app.py` + `run_web.py` creation
6. `web/static/` — Implement UI in order: index.html → style.css → app.js
7. Update `requirements.txt`
8. Integration test: Run actual backtest with short date range (1~2 weeks)

---

## Verification Method

```bash
# Install dependencies
pip install fastapi "uvicorn[standard]"

# Start server
export ANTHROPIC_API_KEY=sk-ant-...
python run_web.py

# Access http://localhost:8000 in browser
# symbol: 005930, stock_name: 삼성전자, test with short date range
```

Checklist:
- [ ] Form submit → immediately switch to progress panel
- [ ] Log item added per trading day, progress bar updates
- [ ] After completion: KPI cards·performance chart·trade history displayed
- [ ] Close browser tab and reopen → event replay restores current state
- [ ] `end < start` input → HTTP 422 immediately returned
- [ ] Daily chart browser: Kline / Trading tab switching, per-date chart display
