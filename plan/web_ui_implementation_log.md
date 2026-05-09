# FinAgent Web UI Implementation Log

Step-by-step record of adding a web-based input/output interface to the CLI-only FinAgent backtesting pipeline.  
For each step, both **intent (why this design)** and **actual implementation (what was built and how)** are documented together.

---

## Background and Problem Definition

### Limitations of the Existing CLI

```bash
python finagent/main.py \
  --symbol 005930 \
  --stock-name 삼성전자 \
  --start 2024-01-02 \
  --end 2024-03-29
```

The CLI was usable only by those familiar with terminals, and provided no way to know the progress status until the backtest completed.  
A 3-month backtest requires Claude API 3~4 calls × approximately 62 trading days = **186~248 API calls**, taking 30+ minutes.  
With only terminal logs, users couldn't tell "which day it was on and what decision was made," resulting in poor user experience.

### Goals

- Be able to enter parameters and execute in the browser
- Be able to see decisions (BUY/SELL/HOLD) and rationale **in real-time for each trading day** while the backtest is running
- After completion, immediately view performance metrics such as returns, Sharpe ratio, MDD, and charts
- **Minimize modifications to existing finagent/ code**

---

## Technology Stack Decision

### Intent

Three options first considered:

| Option | Advantages | Disadvantages |
|------|------|------|
| **Streamlit** | Complete UI with Python only, fast prototyping | Script re-execution model — conflicts with 30-minute blocking functions. Difficult to naturally stream intermediate progress |
| **FastAPI + SSE** | Handles long-running tasks with async/thread separation, real-time streaming via browser built-in `EventSource` API | Must write frontend directly |
| **FastAPI + React** | Richest UX possible | Build pipeline, bundler, state management library — complexity exceeding this project's scope |

**Choice: FastAPI + SSE + Vanilla HTML/CSS/JS**

Core reason: `run_backtest()` is a synchronous blocking function (sequentially calls Claude API internally), so it must run in a separate thread. FastAPI's `run_in_executor` + SSE (`StreamingResponse`) is designed for this pattern. Streamlit's script re-execution model is fundamentally incompatible with long-running blocking functions. React exceeds this project's scope.

Reason for choosing SSE over WebSocket: Server→browser unidirectional is sufficient, browser auto-reconnects via `EventSource`, and no additional library is needed.

---

## Architecture Overview

```
Browser (index.html + app.js)
  │  POST /api/backtest  →  receive job_id
  │  GET  /api/backtest/{job_id}/stream  (SSE)
  │          │
  │    ┌─────▼──────────────────────────────────┐
  │    │  FastAPI (web/app.py)  workers=1        │
  │    │                                         │
  │    │  BacktestJob (job_store.py)             │
  │    │  ├─ status: pending→running→done        │
  │    │  ├─ events: List[dict] (for replay)     │
  │    │  └─ queue: asyncio.Queue (for streaming) │
  │    │                                         │
  │    │  ThreadPoolExecutor                     │
  │    │  └─ run_backtest() ← finagent/main.py  │
  │    │       └─ run_day() × N (Claude API)    │
  │    │            └─ progress_callback()       │
  │    │                 └─ call_soon_threadsafe │
  │    └────────────────────────────────────────┘
  │
  ├── GET /charts/{job_id}/{filename}  → PNG file
  └── GET /  → index.html (static file)
```

**Core flow**: `run_backtest()` runs in a thread → on each trading day completion, `progress_callback` pushes event to asyncio Queue via `call_soon_threadsafe` → SSE generator pulls from Queue and sends to browser.

---

## Step-by-Step Implementation

---

### Step A — Modify `finagent/main.py`

#### Intent

The existing `run_backtest()` only returned results with no way to communicate intermediate state externally. For the Web UI to show real-time progress, the decision content needs to be passed externally each time a trading day ends.

Reason for adding `progress_callback` as an optional parameter:
- CLI users don't need to change anything (default value `None`, preserves existing behavior)
- Only the web server provides a callback to receive events
- `run_day()` itself is not touched — minimize change scope to just 4 lines in the `run_backtest()` loop

#### Implementation

```python
# finagent/main.py

def run_backtest(
    ...
    progress_callback=None,  # Added parameter
) -> dict:
    ...
    total_days = len(trading_days)
    for i, ts in enumerate(trading_days):
        decision = None
        try:
            decision = run_day(...)
        except Exception:
            logger.exception("Error on %s, skipping day", ts.date())

        if progress_callback and decision:
            try:
                progress_callback(
                    day_index=i + 1,
                    total_days=total_days,
                    current_date=ts.date(),
                    action=decision.action,
                    reasoning=decision.reasoning,
                )
            except Exception:
                logger.exception("progress_callback error on %s", ts.date())
```

#### Design Decisions

| Item | Choice | Reason |
|------|------|------|
| Callback timing | Just after `run_day()` | Right after decision is made and reflected in portfolio — most natural update timing |
| Callback error handling | Wrapped in separate `try/except` | Callback errors must not interrupt the backtest loop itself |
| No callback on `run_day()` failure | `decision is None` check | Better to honestly skip than falsely report "HOLD" for a failed day |

---

### Step B — `web/job_store.py` — Job State Management

#### Intent

One backtest execution = one "Job". Each job needs a unique ID, and its progress state (starting/running/completed/error) and cumulative event list must be held in server memory.

Reason for including `asyncio.Queue` inside the Job: The SSE streaming endpoint runs in an async context, while `run_backtest()` runs in a synchronous thread. A thread-safe Queue is needed to connect these two worlds. Including the Queue inside the Job object eliminates the need for separate dictionary management.

Reason for maintaining `events: List[dict]` separately: When an SSE connection drops, `EventSource` auto-reconnects. At that point, the Queue is already empty, so accumulated events must be replayed from the `events` list.

#### Implementation

```python
# web/job_store.py

@dataclass
class BacktestJob:
    job_id: str
    status: str = "pending"          # pending | running | done | error
    events: List[dict] = field(default_factory=list)
    result: Optional[dict] = None
    error: Optional[str] = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)

_jobs: Dict[str, BacktestJob] = {}  # Single dictionary within process
```

#### Design Decisions

| Item | Choice | Reason |
|------|------|------|
| Storage | In-process `dict` | External stores like Redis add unnecessary dependencies. Single process guaranteed with `workers=1` |
| Job cleanup | Not implemented (currently) | Potential memory leak for long-running operation, but a backtesting tool won't accumulate dozens of jobs per session |
| ID | `uuid.uuid4()` | No collision possibility, unpredictable (URL security) |

---

### Step C — `web/schemas.py` — Request/Response Models

#### Intent

Leverage FastAPI's Pydantic integration to automate validation of JSON coming from the frontend. Invalid input returns HTTP 422 with per-field detailed errors, so no manual validation logic needs to be written in route code.

#### Implementation

```python
# web/schemas.py

class BacktestRequest(BaseModel):
    symbol: str
    stock_name: str
    start: date
    end: date
    initial_cash: float = 10_000_000
    trader_preference: str = "moderate"

    @field_validator("trader_preference")
    def validate_preference(cls, v):
        allowed = {"aggressive", "moderate", "conservative"}
        if v not in allowed:
            raise ValueError(f"trader_preference must be one of {allowed}")
        return v

    @field_validator("end")
    def validate_dates(cls, v, info):
        start = info.data.get("start")
        if start and v <= start:
            raise ValueError("end must be after start")
        if start and (v - start).days > 365:
            raise ValueError("Date range must not exceed 365 days")
        return v
```

#### Validation Cases

| Validation Item | Error Message |
|-----------|-------------|
| `end <= start` | "end must be after start" |
| Date range > 365 days | "Date range must not exceed 365 days" |
| Undefined `trader_preference` value | "trader_preference must be one of ..." |
| Date format error (not YYYY-MM-DD) | Pydantic built-in date parsing error |

Reason for 365-day limit: Claude API 3~4 calls × approximately 250 trading days = 750~1000 API calls, practically taking several hours. A reasonable upper bound was needed.

---

### Step D — `web/routes/backtest.py` — SSE Streaming (Core)

#### Intent

This file is the core of the Web UI. Two problems must be solved simultaneously.

1. **Sync ↔ Async Bridge**: `run_backtest()` is a synchronous function and must not block FastAPI's async event loop. Must run in a separate thread via `run_in_executor`.

2. **Thread → Event Loop Communication**: `progress_callback` is called from a thread, but `asyncio.Queue.put_nowait()` is only safe in the event loop. `loop.call_soon_threadsafe()` connects these two worlds.

#### Lazy Import (`_get_run_backtest`)

```python
def _get_run_backtest():
    from finagent.main import run_backtest
    return run_backtest
```

Importing `from finagent.main import run_backtest` at module top level would import all of finagent's dependencies (feedparser, pykrx, chromadb, etc.) at server startup time. If those dependencies aren't installed, the server itself cannot start. With lazy import, server startup always succeeds, and the import is only attempted when an actual backtest request comes in.

#### progress_callback — Thread-safe Event Push

```python
def _make_progress_callback(job, loop):
    def callback(day_index, total_days, current_date, action, reasoning):
        event = {
            "type": "progress",
            "day": day_index,
            "total": total_days,
            "date": str(current_date),
            "action": action,
            "reasoning": reasoning,
            "pct": round(day_index / total_days * 100, 1),
        }
        job.events.append(event)                          # Accumulate for replay
        loop.call_soon_threadsafe(job.queue.put_nowait, event)  # Push for SSE
    return callback
```

`job.events.append()` is a simple operation within GIL protection scope, so it is thread-safe.  
`loop.call_soon_threadsafe()` safely schedules work to the event loop from another thread, as its name implies.

#### POST `/api/backtest` Flow

```
1. Check ANTHROPIC_API_KEY existence  → HTTP 400 if missing
2. Parameter validation               → Pydantic handles HTTP 422
3. create_job()                       → Issue UUID job_id
4. Create isolated directory per job:
   job_data/{job_id}/charts/
   job_data/{job_id}/portfolio.db
   job_data/{job_id}/memory_db/
5. asyncio.create_task(run_in_thread()) — Return immediately, background execution
6. Return {job_id, stream_url}
```

Step 5 is key: `create_task` only schedules the coroutine in the event loop without waiting, so the client immediately receives `job_id` and can connect to SSE.

#### GET `/api/backtest/{job_id}/stream` — SSE Generator

```python
async def event_generator():
    # 1. Replay accumulated events on reconnect
    for evt in list(job.events):
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    if job.status in ("done", "error"):
        return   # Already finished Job → only replay then end

    # 2. Stream new events
    while True:
        try:
            evt = await asyncio.wait_for(job.queue.get(), timeout=60.0)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"  # Prevent nginx/proxy timeout
            continue
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        if evt.get("type") in ("done", "error"):
            break
```

SSE message format is `data: {JSON}\n\n`. Empty line (`\n\n`) separates event boundaries.  
`ensure_ascii=False` ensures Korean reasoning text is sent as-is without `\uXXXX` escaping.  
Heartbeat line (`: ...`) is an SSE comment, not delivered to browser as an event.

#### SSE Event Schema

```
# Progress event — once per trading day
data: {"type":"progress","day":1,"total":62,"date":"2024-01-02",
       "action":"HOLD","reasoning":"...","pct":1.6}

# Completion event — once
data: {"type":"done","result":{"total_return_pct":12.5,"sharpe_ratio":1.23,...}}

# Error event
data: {"type":"error","message":"Claude API rate limit exceeded"}
```

---

### Step E — Job Isolation Strategy

#### Intent

SQLite and ChromaDB are vulnerable to concurrent writes. SQLite uses per-DB-file locking, and ChromaDB's `PersistentClient` uses per-directory locking. If multiple backtests run simultaneously using the same file/directory, `OperationalError: database is locked` will occur.

Solution: Assign each Job an independent directory.

```
job_data/
└── {uuid}/
    ├── portfolio.db   ← SQLite for this Job only
    ├── memory_db/     ← ChromaDB for this Job only
    └── charts/        ← Chart PNGs for this Job
```

#### Design Decisions

| Item | Choice | Reason |
|------|------|------|
| Root directory | `job_data/` | Located at project root, added to `.gitignore` |
| Directory creation timing | During `POST /api/backtest` handling | Chart directory must exist before `run_backtest()` call |
| Cleanup policy | Not implemented | TTL-based cleanup can be added later. Currently users delete manually |

---

### Step F — `web/routes/results.py` + `web/routes/charts.py`

#### Intent

SSE is unidirectional push, so events may be missed if connection drops. `GET /result` serves as a polling fallback to check final results without SSE. `GET /trades` is an endpoint for the client to separately fetch trade history.

Path Traversal defense in `charts.py`:

```python
if ".." in job_id or ".." in filename or "/" in filename or "\\" in filename:
    raise HTTPException(400)
```

Both `job_id` and `filename` are validated. `..` can escape the directory, and `/` or `\` allows subdirectory access.

The `chart-list` endpoint is needed for the frontend's daily chart browser to dynamically determine "which dates' charts exist." Directly calculating the date range in the frontend could cause mismatches with actually generated files, so the server returns the list directly.

---

### Step G — `web/app.py` — Application Factory

#### Intent

The order of router registration is important. FastAPI matches routes in registration order. Mounting `StaticFiles` at the root (`/`) means API routes registered afterward will never match. Therefore **API routers must be registered first, then `StaticFiles` mounted last**.

```python
def create_app():
    app = FastAPI(...)
    app.include_router(backtest.router)   # /api/backtest
    app.include_router(results.router)    # /api/backtest/{id}/result
    app.include_router(charts.router)     # /charts/{id}/{file}
    # Last: handle all remaining paths as static
    app.mount("/", StaticFiles(directory="web/static", html=True), name="static")
    return app
```

The `html=True` option makes `GET /` return `index.html`.

Reason for mandatory `workers=1` setting: The `_jobs` dictionary is in process memory. With `workers=2` or more, each process has an independent `_jobs`, so the process that created a Job and the process that handles SSE may differ. External stores like Redis are needed for cross-process sharing.

---

### Step H — Frontend (`web/static/`)

#### Intent

Implemented with Vanilla HTML/CSS/JS without frameworks like React or Vue. Reasons:
- No build pipeline (webpack, npm) needed. `web/static/` can be served directly
- Browser built-in `EventSource` API completes SSE handling
- UI is not complex — just 3-panel (form·progress·results) state switching

#### `index.html` — 3-Panel Single Page

```
#panel-form (initially shown)
  │ submit
  ▼
#panel-progress (backtest in progress)
  │ done event
  ▼
#panel-results (results)
  │ "New backtest" click
  └─ → #panel-form (return to initial state)
```

Reason for not separating three panels into separate pages, instead using `display: none / block`: Can maintain SSE connection without page navigation. Page navigation disconnects `EventSource`.

#### `app.js` — SSE Client State Machine

Key functions:

```javascript
// Form submit → POST → SSE connection
form.addEventListener('submit', async (e) => { ... });

// SSE event branching
eventSource.onmessage = (evt) => {
    if (data.type === 'progress') handleProgress(data);
    else if (data.type === 'done')  handleDone(data.result, formData);
    else if (data.type === 'error') showError(data.message);
};

// Progress event: update progress bar + add log item
function handleProgress(data) {
    progressFill.style.width = data.pct + '%';
    logList.appendChild(item);           // with animation
    logList.scrollTop = logList.scrollHeight;  // auto-scroll
}

// Completion event: fetch chart list → render KPI
async function handleDone(result, formData) {
    const { charts } = await fetch(`/api/backtest/${currentJobId}/chart-list`).then(r=>r.json());
    renderResults(result, formData);
    showResultsPanel();
}
```

Reconnection handling: `EventSource.onerror` tries to reconnect automatically on connection failure. If `readyState !== CLOSED`, even when an `error` event comes, it's reconnecting so errors are not shown in the UI.

#### `style.css` — Dark Theme Design

Color system defined with CSS variables:

```css
:root {
  --bg: #0f1117;          /* Outermost background */
  --surface: #1a1d27;     /* Card background */
  --buy: #10b981;         /* Green — BUY */
  --sell: #ef4444;        /* Red — SELL */
  --hold: #f59e0b;        /* Yellow — HOLD */
}
```

BUY/SELL/HOLD badge colors follow financial data visualization conventions (green/red/neutral).

`animation: fadeIn 0.3s ease` applied to log items: New items appear smoothly, visually emphasizing that progress is being made.

---

### Step I — `run_web.py` — Server Entry Point

#### Intent

Reason for putting `uvicorn.run()` only in the `if __name__ == "__main__"` block: Pointing to `web.app:app` allows startup with both `uvicorn web.app:app` and `python run_web.py`. In development mode with `--reload`, the `if __name__` block should not execute repeatedly.

```python
# run_web.py
if __name__ == "__main__":
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, workers=1)
```

Reason for explicitly noting `workers=1` comment in code: Anyone reading the code should immediately know that changing this breaks the job store.

---

## Error Handling Design

| Error Situation | Handling Method | Reason |
|-----------|-----------|------|
| `ANTHROPIC_API_KEY` not set | `POST /api/backtest` → HTTP 400 immediately | Block before job creation. Failure after 30-minute task starts cannot be recovered |
| Invalid parameters (date reversal, range exceeded, etc.) | Pydantic ValidationError → HTTP 422 per-field detail | Automatic handling without route code |
| Claude API error on specific trading day | Existing `except` skips, stream continues | One day's error should not stop the entire backtest. Preserves existing `main.py` design |
| Full backtest failure (exception outside `run_in_thread`) | Send SSE `error` event → frontend error banner | |
| SSE connection drop | `EventSource` auto-reconnect + `job.events` replay | Can view from beginning on reconnect |
| Chart file Path Traversal attempt | HTTP 400 if `..`, `/`, `\` present | Block access to files outside `job_data/` |

---

## Final File Structure

```
web/
├── __init__.py
├── app.py                  40 lines   FastAPI app factory + router registration
├── job_store.py            31 lines   BacktestJob dataclass + _jobs dictionary
├── schemas.py              44 lines   BacktestRequest (with validation) + response models
└── routes/
    ├── __init__.py
    ├── backtest.py        134 lines   POST /api/backtest + GET .../stream (SSE core)
    ├── results.py          44 lines   GET .../result + .../trades (fallback)
    └── charts.py           38 lines   GET /charts/{job_id}/{filename} + chart-list

web/static/
├── index.html             175 lines   3-panel single-page UI
├── style.css              220 lines   Dark theme + animations
└── app.js                 230 lines   SSE client + DOM manipulation + result rendering

run_web.py                  14 lines   uvicorn entry point
```

Modified existing files:
- `finagent/main.py` — Add `progress_callback` parameter (+13 lines)
- `requirements.txt` — Add `fastapi`, `uvicorn[standard]`, `python-multipart`
- `.gitignore` — Add `job_data/`

---

## Key Design Decision Summary

| Decision | Choice | Alternative | Reason |
|------|------|------|------|
| UI framework | FastAPI + SSE | Streamlit, React | Optimal for long-running blocking tasks + real-time streaming |
| Real-time communication | SSE (EventSource) | WebSocket | Unidirectional sufficient, auto-reconnect, no library needed |
| Background execution | ThreadPoolExecutor (`run_in_executor`) | asyncio, Celery | `run_backtest()` is a sync function — thread is the only option |
| Thread→asyncio bridge | `call_soon_threadsafe` | Shared variable polling | Official method to safely schedule work to the event loop |
| Job storage | In-process dict | Redis | No external dependencies, sufficient with `workers=1` |
| Job isolation | Independent directories under `job_data/{uuid}/` | Shared DB + symbol key | Prevent SQLite lock + ChromaDB directory lock conflicts |
| finagent import | Lazy import (`_get_run_backtest`) | Module-level import | Server startup remains independent of finagent dependencies |
| Frontend | Vanilla JS | React, Vue | No build needed, sufficient with 3-panel switching logic |
| Route registration order | API → StaticFiles last | — | FastAPI matches in registration order — StaticFiles first hides API |

---

## How to Run

```bash
# Install dependencies
conda activate finagent
pip install "fastapi>=0.111.0" "uvicorn[standard]>=0.30.0"

# Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# Start server
python run_web.py
# → Access http://localhost:8000

# Development mode (auto-restart on code changes)
uvicorn web.app:app --reload --port 8000
```

Checklist:
- [ ] Form submit → immediately switch to progress panel, job_id issued
- [ ] Log item added per trading day, progress bar updated
- [ ] After completion: KPI cards·performance chart·trade history displayed
- [ ] Close browser tab and reopen → event replay restores current state
- [ ] `ANTHROPIC_API_KEY` not set → error banner displayed (HTTP 400)
- [ ] `end < start` input → HTTP 422 immediately returned
- [ ] Daily chart browser: Kline / Trading tab switching, per-date chart display
