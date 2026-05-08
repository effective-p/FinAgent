# FinAgent UI 개선 — 파이프라인 Flow 시각화 구현 로그

진행 패널에 파이프라인 단계별 아이콘·애니메이션을 추가하고, 부가적인 UI 품질 개선을 함께 진행한 작업의 단계별 기록.

---

## 배경 및 문제 정의

### 기존 진행 패널의 한계

백테스트 실행 중 기존 UI는 다음만 보여줬다:
- 전체 거래일 대비 진행률 (숫자 + 진행 바)
- 거래일별 완료 로그 (날짜 · BUY/SELL/HOLD 뱃지 · 판단 근거)

하루치 백테스트 내부에서 Claude API를 최대 4회 호출하는데, 그 사이 UI는 아무런 변화가 없어 **"지금 멈춘 건지 실행 중인 건지"** 알 수 없는 상태였다.

### 목표

- 각 단계(뉴스 수집 → MI → LLR → HLR → 결정 → 실행)가 시작될 때마다 해당 노드 활성화
- 아이콘 타입(외부 API / Claude AI / Portfolio DB)에 따라 색상과 애니메이션 차별화
- 기존 진행 바와 자연스럽게 공존 — 레이아웃 깨짐 없이

---

## 단계별 구현

---

### Step 1 — `finagent/main.py` — step_callback 파라미터 추가

#### 의도

`run_day()`는 6개 단계를 순차 실행하는데, 웹 UI가 "지금 어느 단계인지" 알려면 각 단계 **직전**에 알림이 필요하다. `progress_callback`(거래일 완료 시 1회)과 같은 패턴으로 `step_callback`(단계 시작 시 발화)을 추가했다.

`run_day()` 내부에 `_step()` 헬퍼를 정의한 이유: 콜백이 `None`이거나 예외가 발생해도 파이프라인을 중단하지 않아야 한다. 헬퍼 한 곳에서 방어 처리하면 각 호출 지점에서 반복 코드가 사라진다.

#### 구현

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

`run_backtest()`에는 루프 외부의 OHLCV 수집 전에 `step_callback("ohlcv_fetch")`를 추가했다:

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

#### 설계 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 발화 시점 | 단계 **직전** | 단계가 실행되는 동안 UI가 활성 상태를 보여야 함 |
| 콜백 예외 처리 | `try/except pass` | 시각 효과 오류가 백테스트를 중단하면 안 됨 |
| CLI 호환성 | 기본값 `None` | CLI 사용자는 변경 없이 기존 동작 유지 |

---

### Step 2 — `web/routes/backtest.py` — _make_step_callback 추가

#### 의도

`progress_callback`은 `job.events`에 append해 SSE 재연결 시 리플레이에 사용한다.  
`step_callback`은 의도적으로 `job.events`에 저장하지 않는다.

이유: step 이벤트는 "지금 이 단계가 실행 중"이라는 순간적 시각 정보다. 재연결 시 이미 완료된 단계들을 다시 활성화하면 오해를 줄 수 있고, 이벤트 목록이 하루 7개 × N거래일만큼 불필요하게 커진다. 재연결 후에는 `progress` 이벤트로 전체 진행률만 복원하면 충분하다.

#### 구현

```python
# web/routes/backtest.py

def _make_step_callback(job: BacktestJob, loop: asyncio.AbstractEventLoop):
    def callback(step: str):
        event = {"type": "step", "step": step}
        # job.events에 저장하지 않음 — 재연결 리플레이 제외
        loop.call_soon_threadsafe(job.queue.put_nowait, event)
    return callback

# start_backtest()
step_cb = _make_step_callback(job, loop)
...
lambda: run_backtest(..., step_callback=step_cb)
```

---

### Step 3 — `web/static/index.html` — 파이프라인 Flow HTML

#### 의도

파이프라인 flow를 `#panel-progress` 카드 내부, 진행 바 **위에** 배치한다.  
사용자가 "현재 단계"를 보고, 아래 진행 바로 "전체 진행률"을 확인하는 시선 흐름을 만든다.

OHLCV 수집은 루프 외부에서 1회만 실행되므로 시각적으로도 Daily Loop 래퍼 **바깥에** 분리 배치했다.

#### 구조

```html
<div class="pipeline-flow" id="pipeline-flow">

  <!-- 루프 외부: OHLCV -->
  <div class="pipeline-node type-api" id="pnode-ohlcv_fetch">
    <div class="node-icon-wrap">
      <div class="node-pulse"></div>
      <div class="node-icon">📈</div>
    </div>
    <div class="node-label">OHLCV 수집</div>
    <div class="node-sub">KRX API</div>
  </div>

  <div class="pipeline-arrow">→</div>

  <!-- Daily Loop 래퍼 -->
  <div class="pipeline-loop-wrap">
    <div class="loop-badge">Daily Loop</div>
    <div class="loop-inner">
      <!-- 뉴스 → MI → LLR → HLR → 매매결정 → 거래실행 -->
      ...
    </div>
  </div>

</div>
```

각 노드: `node-icon-wrap > node-pulse (절대 위치) + node-icon (이모지)` + `node-label` + `node-sub`

#### 설계 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 이모지 아이콘 | Unicode 이모지 | 외부 아이콘 라이브러리 불필요, macOS에서 선명하게 렌더링 |
| loop 래퍼 | `border: 1px dashed` | Daily Loop 범위를 시각적으로 명확하게 구분 |
| loop-badge 위치 | `position: absolute; top: -9px` | 래퍼 상단 테두리 위에 플로팅 — 흔히 쓰이는 fieldset 패턴 |

---

### Step 4 — `web/static/style.css` — 파이프라인 애니메이션 CSS

#### 의도

세 가지 상태를 CSS 클래스로 표현한다: `(없음) / active / completed`

애니메이션은 두 종류:

1. **`nodePulse`** (모든 타입): 테두리 링이 확산·소멸을 반복 → 네트워크 신호, 처리 중 느낌
2. **`aiIconPulse`** (AI 타입만): 아이콘이 1.07배로 천천히 호흡 → Claude 모델이 "생각하는 중" 강조

타입별 색상 분리:
- **API** (`#38bdf8` 하늘색): 외부 API 호출 — 네트워크 느낌
- **AI** (`#a78bfa` 보라): Claude 모델 — "지능" 느낌
- **DB** (`#fb923c` 주황): Portfolio 기록 — 저장/실행 느낌

#### 핵심 CSS

```css
/* Pulse ring — 확산 후 소멸 */
@keyframes nodePulse {
  0%   { transform: scale(0.85); opacity: 0.9; }
  60%  { transform: scale(1.5);  opacity: 0; }
  100% { transform: scale(1.5);  opacity: 0; }
}

/* AI 아이콘 호흡 */
@keyframes aiIconPulse {
  0%, 100% { transform: scale(1); }
  50%       { transform: scale(1.07); }
}

/* Active 상태 예시 (AI 타입) */
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

completed 상태는 `node-icon-wrap::after`로 초록 ✓ 뱃지를 의사 요소로 표시 — 별도 DOM 없이 구현.

---

### Step 5 — `web/static/app.js` — 파이프라인 상태 관리

#### 의도

파이프라인 노드 상태를 세 함수로 캡슐화한다:

| 함수 | 호출 시점 | 동작 |
|------|-----------|------|
| `activatePipelineStep(step)` | `step` SSE 이벤트 수신 | 해당 노드만 `active`, 나머지 `active` 제거 |
| `completePipelineDay()` | `progress` SSE 이벤트 수신 | 전체 `completed`, 600ms 후 전체 초기화 |
| `resetPipeline()` | `showProgressPanel()` 호출 시 | 모든 클래스 제거, 초기 상태 복귀 |

#### 구현

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

SSE 이벤트 분기에 추가:

```javascript
if (data.type === 'step') {
  activatePipelineStep(data.step);
} else if (data.type === 'progress') {
  completePipelineDay();   // 완료 플래시 후 리셋
  handleProgress(data);
}
```

#### 설계 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 한 번에 하나만 active | 이전 active 제거 후 신규 추가 | 동시에 두 노드가 활성화되면 혼란. 단계는 순차 실행이므로 항상 하나 |
| completed 600ms 후 리셋 | `setTimeout 600` | 너무 짧으면 ✓ 뱃지를 못 보고, 너무 길면 다음 날 노드 활성화 전에 completed가 남아있음 |
| step 이벤트 미저장 | `job.events` append 생략 | 재연결 시 시각 상태 복원 불필요 — 진행률만 복원하면 충분 |

---

## 부가 UI 수정 사항

### 종료일 기본값 동적 계산

**변경 전**: `value="2024-03-29"` (하드코딩)  
**변경 후**: JS IIFE로 `new Date() - 1일` 계산 후 세팅

```javascript
(function setDefaultDates() {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const ymd = yesterday.toISOString().slice(0, 10);
  const endInput = document.getElementById('end');
  if (endInput && !endInput.value) endInput.value = ymd;
})();
```

`toISOString().slice(0, 10)`을 쓴 이유: `toLocaleDateString()`은 로케일에 따라 형식이 달라지지만, `toISOString()`은 항상 `YYYY-MM-DDTHH:mm:ss.sssZ` 형식을 반환하므로 앞 10자를 자르면 항상 `YYYY-MM-DD` 형식이 보장된다.

### 성과 차트 폰트 설정 이동

**변경 전**: 모듈 최상단 `for _font in ["AppleGothic", "NanumGothic", ...]: matplotlib.rcParams[...] = _font`  
**변경 후**: `plot_performance()` 함수 내부로 이동, `Pretendard` 단일 폰트 지정

이유: 모듈 임포트 시 폰트 탐색이 실행되는 것은 부수효과(side effect)다. 함수 내부에서 설정해야 "이 함수를 호출할 때만 폰트를 설정한다"는 의도가 명확해진다. `Pretendard`는 시스템에 설치된 한글 폰트로 고정.

---

## 최종 변경 파일 요약

| 파일 | 변경 종류 | 주요 내용 |
|------|-----------|-----------|
| `finagent/main.py` | 기능 추가 | `run_day` + `run_backtest`에 `step_callback` 파라미터 |
| `web/routes/backtest.py` | 기능 추가 | `_make_step_callback()`, `step_cb` 전달 |
| `web/static/index.html` | UI 추가 | 파이프라인 flow HTML (8개 노드 + 화살표 + 루프 래퍼) |
| `web/static/style.css` | 스타일 추가 | 노드 상태·타입·애니메이션 CSS (~160줄) |
| `web/static/app.js` | 기능 추가 | 파이프라인 상태 관리 함수 3개, SSE step 핸들러 |
| `web/static/index.html` + `app.js` | 버그 수정 | 종료일 기본값 동적 계산 |
| `finagent/utils/metrics.py` | 리팩터링 | 폰트 설정 `plot_performance()` 내부로 이동, Pretendard 지정 |

---

## 커밋 이력

| 커밋 | 메시지 | 내용 |
|------|--------|------|
| `917d487` | fix: move font config into plot_performance and use Pretendard | metrics.py 폰트 리팩터링 |
| `04aa33e` | feat: set end date default to yesterday dynamically via JS | 종료일 동적 계산 |
| (미커밋) | feat: add pipeline flow step visualization to progress panel | 파이프라인 flow 시각화 전체 |
