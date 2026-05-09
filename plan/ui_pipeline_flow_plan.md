# FinAgent UI Improvement — Pipeline Flow Visualization Plan

---

## Context

The backtest progress panel in the Web UI only displayed overall progress (%) relative to total days and a per-trading-day decision log.  
During the long wait while a single day's backtest makes 3~4 Claude API calls,  
users had no way to know **which pipeline stage was currently executing**, which was frustrating.

**Goal**: Display the currently executing pipeline stage in real-time with icons + animations,  
and allow viewing the full pipeline flow at a glance alongside the existing daily progress bar.

---

## Pipeline Structure

Stage execution order within `run_day()`:

```
OHLCV Fetch (outside loop)  →  ┌──────────────── Daily Loop ────────────────────────┐
  KRX API once                 │  News → MI → LLR → HLR → Decision → Trade Execution │
                               └────────────────────────────────────────────────────┘
```

| Step ID              | Display Name    | Icon | Type   | Description                       |
|----------------------|-----------|--------|--------|----------------------------|
| `ohlcv_fetch`        | OHLCV Fetch | 📈    | API    | KRX pykrx price data collection |
| `news_fetch`         | News Fetch  | 📰    | API    | Google RSS news feed        |
| `market_intelligence`| Market Analysis | 🧠    | AI     | MI module Claude call        |
| `low_level_reflection`| Technical Analysis | 📊    | AI     | LLR module Claude + chart     |
| `high_level_reflection`| Strategy Reflection| 🔍   | AI     | HLR module Claude + chart     |
| `decision_making`    | Trade Decision  | ⚡    | AI     | DM module Claude final decision   |
| `trade_execution`    | Trade Execution  | 💼    | DB     | Portfolio SQLite record      |

---

## Technical Design

### Adding SSE Events

Beyond the existing `progress` (trading day complete), `done`, `error`, add a **`step` event**:

```json
{"type": "step", "step": "market_intelligence"}
```

- Fired **immediately before** each stage starts (icon stays active while stage is executing)
- Not stored in `job.events` (no replay needed on reconnect — visual only)

### Event Flow

```
[Thread] run_backtest()
    step_callback("ohlcv_fetch")   →  SSE: {"type":"step","step":"ohlcv_fetch"}
    fetcher.get_price_data()
    loop:
        step_callback("news_fetch")          →  SSE step event
        get_news() + plot_charts()
        step_callback("market_intelligence")  →  SSE step event
        mi_module.run()
        ...
        step_callback("trade_execution")      →  SSE step event
        portfolio.execute()
        progress_callback(...)               →  SSE: {"type":"progress",...}
```

### Thread Safety

`step_callback` uses the same pattern as `progress_callback`: `loop.call_soon_threadsafe(queue.put_nowait, event)`.  
However, it does not append to `job.events`, minimizing event accumulation.

---

## UI Component Design

### Layout (inside progress panel)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Card: Backtesting in progress…                                      │
│                                                                      │
│  [📈]  →  ┌─────────── Daily Loop ──────────────────────────────┐  │
│  OHLCV    │ [📰]→[🧠]→[📊]→[🔍]→[⚡]→[💼]                      │  │
│  KRX API  │  News  MI  LLR  HLR  Decision  Execution             │  │
│           └────────────────────────────────────────────────────┘  │
│                                                                      │
│  62 / 62 trading days  ████████████████  100%                      │
│                                                                      │
│  [Log list]                                                          │
└─────────────────────────────────────────────────────────────────────┘
```

### Node States

| State       | Visual Effect                                            |
|------------|------------------------------------------------------|
| inactive   | 28% opacity, faint border                            |
| active     | 100% opacity, color glow + expanding pulse ring animation |
| completed  | 55% opacity, green ✓ badge, reverts to inactive after 0.6s |

### Colors by Type

| Type | Color       | Applied Stages                        |
|------|------------|----------------------------------|
| API  | `#38bdf8`  | OHLCV Fetch, News Fetch            |
| AI   | `#a78bfa`  | MI, LLR, HLR, Trade Decision (Claude) |
| DB   | `#fb923c`  | Trade Execution (Portfolio DB)          |

AI type nodes get an additional `scale(1.07)` breathing animation when active (emphasizing that processing is in progress).

---

## Files to Modify

| File | Change Content |
|------|-----------|
| `finagent/main.py` | Add `step_callback` parameter to `run_day()`, call before each stage. Add `step_callback` parameter to `run_backtest()`, fire before OHLCV fetch |
| `web/routes/backtest.py` | Add `_make_step_callback()`, pass `step_callback=step_cb` to `run_backtest` call |
| `web/static/index.html` | Add pipeline flow HTML inside `#panel-progress` |
| `web/static/style.css` | Add pipeline node·arrow·loop wrapper·animation CSS |
| `web/static/app.js` | Add `activatePipelineStep()`, `completePipelineDay()`, `resetPipeline()`, connect SSE `step` event handler |

---

## Other UI Changes

| Item | Change Content | Commit |
|------|-----------|------|
| End date default | Hardcoded `2026-05-07` → JS dynamic calculation `new Date() - 1 day` | `04aa33e` |
| Start date default | `2024-01-02` → `2026-01-02` | `04aa33e` |
| Performance chart font | Module-level font setting → moved inside `plot_performance()`, specified `Pretendard` | `917d487` |

---

## Verification Method

```bash
python run_web.py
# → http://localhost:8000

# Run with a short date range (1~2 weeks)
# symbol: 005930, stock_name: 삼성전자
```

Checklist:
- [ ] OHLCV fetch node activates immediately after form submit (pulse ring + glow)
- [ ] Each trading day: nodes switch in order News → MI → LLR → HLR → Decision → Execution
- [ ] On trading day completion: all nodes flash completed (✓), then reset to inactive after 0.6s
- [ ] Overall progress bar and pipeline flow update simultaneously
- [ ] AI nodes (MI·LLR·HLR·Decision) show breathing animation when active
- [ ] Pipeline horizontal scroll works on mobile/narrow screens
