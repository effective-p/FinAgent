# FinAgent UI Improvement — Pipeline Flow Visualization Implementation Log

Step-by-step record of adding stage-by-stage icons and animations to the progress panel, along with additional UI quality improvements.

---

## Background and Problem Definition

### Limitations of the Existing Progress Panel

During backtest execution, the existing UI only showed:
- Overall trading day progress (number + progress bar)
- Per-trading-day completion log (date · BUY/SELL/HOLD badge · decision rationale)

With up to 4 Claude API calls per day of backtesting, the UI showed **no change in between**, leaving users unable to tell **"whether it's stopped or still running."**

### Goals

- Activate the corresponding node each time a stage (news fetch → MI → LLR → HLR → decision → execution) starts
- Differentiate colors and animations by icon type (external API / Claude AI / Portfolio DB)
- Coexist naturally with the existing progress bar — no layout breakage

---

## Step-by-Step Implementation

---

### Step 1 — `finagent/main.py` — Add step_callback Parameter

#### Intent

`run_day()` executes 6 stages sequentially, and for the Web UI to know "which stage is currently active," a notification is needed **just before** each stage. Added `step_callback` (fires when stage starts) following the same pattern as `progress_callback` (fires once when trading day completes).

Reason for defining `_step()` helper inside `run_day()`: The pipeline should not be interrupted if the callback is `None` or an exception occurs. Defensive handling in one place eliminates repeated code at each call site.

#### Implementation

```python
# finagent/main.py — run_day()

def run_day(..., step_callback=None) -> Decision:

    def _step(name: str) -> None:
        if step_callback:
            try:
                step_callback(name)
            except Exception:
                pass

    _step("news_fetch")
    news = fetcher.get_news(...)
    kline_path = ...
    trading_path = ...

    _step("market_intelligence")
    mi_result = mi_module.run(...)

    _step("low_level_reflection")
    llr_result = llr_module.run(...)

    _step("high_level_reflection")
    hlr_result = hlr_module.run(...)

    _step("decision_making")
    decision = dm_module.run(...)

    _step("trade_execution")
    portfolio.execute(...)
```

Added `step_callback("ohlcv_fetch")` before OHLCV collection outside the loop in `run_backtest()`:

```python
# finagent/main.py — run_backtest()

def run_backtest(..., step_callback=None) -> dict:
    ...
    if step_callback:
        try:
            step_callback("ohlcv_fetch")
        except Exception:
            pass
    price_df = fetcher.get_price_data(symbol, lookback_days=lookback_days)
    ...
    for i, ts in enumerate(trading_days):
        decision = run_day(..., step_callback=step_callback)
```

#### Design Decisions

| Item | Choice | Reason |
|------|------|------|
| Fire timing | **Just before** stage | UI should show active state while stage is executing |
| Callback exception handling | `try/except pass` | Visual effect errors should not stop the backtest |
| CLI compatibility | Default value `None` | CLI users maintain existing behavior with no changes |

---

### Step 2 — `web/routes/backtest.py` — Add _make_step_callback

#### Intent

`progress_callback` appends to `job.events` for use in SSE reconnect replay.  
`step_callback` is intentionally **not** stored in `job.events`.

Reason: Step events are momentary visual information indicating "this stage is currently executing." Reactivating already-completed stages on reconnect could cause confusion, and the event list would grow unnecessarily by 7 events × N trading days. On reconnect, restoring only the overall progress with `progress` events is sufficient.

#### Implementation

```python
# web/routes/backtest.py

def _make_step_callback(job: BacktestJob, loop: asyncio.AbstractEventLoop):
    def callback(step: str):
        event = {"type": "step", "step": step}
        # Not stored in job.events — excluded from reconnect replay
        loop.call_soon_threadsafe(job.queue.put_nowait, event)
    return callback

# start_backtest()
step_cb = _make_step_callback(job, loop)
...
lambda: run_backtest(..., step_callback=step_cb)
```

---

### Step 3 — `web/static/index.html` — Pipeline Flow HTML

#### Intent

Place the pipeline flow **above** the progress bar, inside the `#panel-progress` card.  
Creates a visual flow where users see "the current stage" and then confirm "overall progress" with the progress bar below.

Since OHLCV collection runs only once outside the loop, it is also visually placed **outside** the Daily Loop wrapper.

#### Structure

```html
<div class="pipeline-flow" id="pipeline-flow">

  <!-- Outside loop: OHLCV -->
  <div class="pipeline-node type-api" id="pnode-ohlcv_fetch">
    <div class="node-icon-wrap">
      <div class="node-pulse"></div>
      <div class="node-icon">📈</div>
    </div>
    <div class="node-label">OHLCV Fetch</div>
    <div class="node-sub">KRX API</div>
  </div>

  <div class="pipeline-arrow">→</div>

  <!-- Daily Loop wrapper -->
  <div class="pipeline-loop-wrap">
    <div class="loop-badge">Daily Loop</div>
    <div class="loop-inner">
      <!-- News → MI → LLR → HLR → Decision → Execution -->
      ...
    </div>
  </div>

</div>
```

Each node: `node-icon-wrap > node-pulse (absolute position) + node-icon (emoji)` + `node-label` + `node-sub`

#### Design Decisions

| Item | Choice | Reason |
|------|------|------|
| Emoji icons | Unicode emoji | No external icon library needed, renders sharply on macOS |
| Loop wrapper | `border: 1px dashed` | Visually clearly delineates the Daily Loop scope |
| loop-badge position | `position: absolute; top: -9px` | Floating above the wrapper's top border — common fieldset pattern |

---

### Step 4 — `web/static/style.css` — Pipeline Animation CSS

#### Intent

Express three states with CSS classes: `(none) / active / completed`

Two types of animations:

1. **`nodePulse`** (all types): Border ring expands and fades repeatedly → network signal, processing feel
2. **`aiIconPulse`** (AI type only): Icon slowly breathes at 1.07x → emphasizes that Claude model is "thinking"

Colors separated by type:
- **API** (`#38bdf8` sky blue): External API calls — network feel
- **AI** (`#a78bfa` purple): Claude models — "intelligence" feel
- **DB** (`#fb923c` orange): Portfolio record — storage/execution feel

#### Key CSS

```css
/* Pulse ring — expands then fades */
@keyframes nodePulse {
  0%   { transform: scale(0.85); opacity: 0.9; }
  60%  { transform: scale(1.5);  opacity: 0; }
  100% { transform: scale(1.5);  opacity: 0; }
}

/* AI icon breathing */
@keyframes aiIconPulse {
  0%, 100% { transform: scale(1); }
  50%       { transform: scale(1.07); }
}

/* Active state example (AI type) */
.type-ai.active .node-icon {
  border-color: #a78bfa;
  background: rgba(167,139,250,0.08);
  box-shadow: 0 0 18px rgba(167,139,250,0.45);
  animation: aiIconPulse 1.2s ease-in-out infinite;
}
.type-ai.active .node-pulse {
  border-color: #a78bfa;
  animation: nodePulse 1.6s ease-out infinite;
}
```

The completed state shows a green ✓ badge as a pseudo-element via `node-icon-wrap::after` — implemented without additional DOM.

---

### Step 5 — `web/static/app.js` — Pipeline State Management

#### Intent

Encapsulate pipeline node state into three functions:

| Function | When Called | Action |
|------|-----------|------|
| `activatePipelineStep(step)` | On receiving `step` SSE event | Only the target node gets `active`, others lose `active` |
| `completePipelineDay()` | On receiving `progress` SSE event | All nodes get `completed`, all reset after 600ms |
| `resetPipeline()` | When `showProgressPanel()` is called | Remove all classes, return to initial state |

#### Implementation

```javascript
function activatePipelineStep(stepName) {
  PIPELINE_STEP_IDS.forEach(id => {
    document.getElementById('pnode-' + id)?.classList.remove('active');
  });
  document.getElementById('pnode-' + stepName)?.classList.add('active');
}

function completePipelineDay() {
  PIPELINE_STEP_IDS.forEach(id => {
    const el = document.getElementById('pnode-' + id);
    if (el) { el.classList.remove('active'); el.classList.add('completed'); }
  });
  setTimeout(() => {
    PIPELINE_STEP_IDS.forEach(id => {
      document.getElementById('pnode-' + id)?.classList.remove('completed');
    });
  }, 600);
}
```

Added to SSE event branching:

```javascript
if (data.type === 'step') {
  activatePipelineStep(data.step);
} else if (data.type === 'progress') {
  completePipelineDay();   // Flash completed then reset
  handleProgress(data);
}
```

#### Design Decisions

| Item | Choice | Reason |
|------|------|------|
| Only one active at a time | Remove previous active then add new | Two nodes active simultaneously would be confusing. Stages execute sequentially so always one |
| completed resets after 600ms | `setTimeout 600` | Too short: can't see ✓ badge; too long: completed remains before next day's node activates |
| Step events not stored | Skip `job.events` append | Visual state restoration on reconnect unnecessary — restoring only progress is sufficient |

---

## Additional UI Changes

### End Date Default Dynamic Calculation

**Before**: `value="2024-03-29"` (hardcoded)  
**After**: JS IIFE calculates `new Date() - 1 day` and sets value

```javascript
(function setDefaultDates() {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const ymd = yesterday.toISOString().slice(0, 10);
  const endInput = document.getElementById('end');
  if (endInput && !endInput.value) endInput.value = ymd;
})();
```

Reason for using `toISOString().slice(0, 10)`: `toLocaleDateString()` format varies by locale, but `toISOString()` always returns `YYYY-MM-DDTHH:mm:ss.sssZ` format, guaranteeing `YYYY-MM-DD` by slicing the first 10 characters.

### Performance Chart Font Configuration Move

**Before**: `for _font in ["AppleGothic", "NanumGothic", ...]: matplotlib.rcParams[...] = _font` at module top  
**After**: Moved inside `plot_performance()` function, specifying `Pretendard` as single font

Reason: Font searching at module import is a side effect. Setting it inside the function makes the intent clear: "only configure fonts when this function is called." `Pretendard` is fixed as the installed Korean font on the system.

---

## Final Changed Files Summary

| File | Change Type | Key Content |
|------|-----------|-----------|
| `finagent/main.py` | Feature addition | `step_callback` parameter in `run_day` + `run_backtest` |
| `web/routes/backtest.py` | Feature addition | `_make_step_callback()`, pass `step_cb` |
| `web/static/index.html` | UI addition | Pipeline flow HTML (8 nodes + arrows + loop wrapper) |
| `web/static/style.css` | Style addition | Node state·type·animation CSS (~160 lines) |
| `web/static/app.js` | Feature addition | 3 pipeline state management functions, SSE step handler |
| `web/static/index.html` + `app.js` | Bug fix | End date default dynamic calculation |
| `finagent/utils/metrics.py` | Refactoring | Move font config inside `plot_performance()`, specify Pretendard |

---

## Commit History

| Commit | Message | Content |
|------|--------|------|
| `917d487` | fix: move font config into plot_performance and use Pretendard | metrics.py font refactoring |
| `04aa33e` | feat: set end date default to yesterday dynamically via JS | End date dynamic calculation |
| (uncommitted) | feat: add pipeline flow step visualization to progress panel | Full pipeline flow visualization |
