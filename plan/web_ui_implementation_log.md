# FinAgent Web UI 구현 로그

CLI 전용이었던 FinAgent 백테스팅 파이프라인에 웹 기반 입력·출력 인터페이스를 추가한 작업의 단계별 기록.  
각 단계마다 **의도(왜 이렇게 설계했는가)** 와 **실제 구현(무엇을 어떻게 만들었는가)** 을 함께 정리한다.

---

## 배경 및 문제 정의

### 기존 CLI의 한계

```bash
python finagent/main.py \
  --symbol 005930 \
  --stock-name 삼성전자 \
  --start 2024-01-02 \
  --end 2024-03-29
```

CLI는 터미널에 익숙한 사람만 사용할 수 있고, 백테스트가 완료되기까지 진행 상황을 전혀 알 수 없다는 문제가 있었다.  
3개월 백테스트는 Claude API 3~4회 × 거래일 약 62일 = **186~248번의 API 호출**, 30분 이상 소요된다.  
터미널 로그만으로는 "지금 몇 번째 날이고 어떤 결정을 했는지" 알 수 없어 사용자 경험이 나빴다.

### 목표

- 브라우저에서 파라미터를 입력하고 실행할 수 있을 것
- 백테스트가 진행되는 동안 **거래일마다 실시간으로** 결정(BUY/SELL/HOLD)과 근거를 볼 수 있을 것
- 완료 후 수익률·샤프비율·MDD 등 성과 지표와 차트를 바로 확인할 수 있을 것
- **기존 finagent/ 코드는 최소한만 수정**할 것

---

## 기술 스택 결정

### 의도

가장 먼저 고려한 세 가지 옵션:

| 옵션 | 장점 | 단점 |
|------|------|------|
| **Streamlit** | Python만으로 UI 완성, 빠른 프로토타이핑 | 스크립트 재실행 모델 — 30분짜리 블로킹 함수와 충돌. 중간 진행 상황을 자연스럽게 스트리밍하기 어렵다 |
| **FastAPI + SSE** | 비동기·스레드 분리로 장시간 작업 처리, 브라우저 내장 `EventSource` API로 실시간 스트리밍 | 프론트엔드를 직접 작성해야 함 |
| **FastAPI + React** | 가장 풍부한 UX 가능 | 빌드 파이프라인, 번들러, 상태 관리 라이브러리 — 이 프로젝트의 범위를 초과하는 복잡도 |

**선택: FastAPI + SSE + Vanilla HTML/CSS/JS**

핵심 이유: `run_backtest()`는 동기 블로킹 함수(내부에서 순차적으로 Claude API 호출)이므로, 반드시 별도 스레드에서 실행해야 한다. FastAPI의 `run_in_executor` + SSE(`StreamingResponse`)는 이 패턴에 맞게 설계되어 있다. 반면 Streamlit은 스크립트 재실행 모델이라 장시간 블로킹 함수와 근본적으로 맞지 않는다. React는 이 프로젝트의 범위를 초과한다.

SSE를 WebSocket 대신 선택한 이유: 서버→브라우저 단방향으로 충분하고, 브라우저가 `EventSource`를 자동 재연결해주며, 추가 라이브러리가 불필요하다.

---

## 아키텍처 개요

```
브라우저 (index.html + app.js)
  │  POST /api/backtest  →  job_id 수신
  │  GET  /api/backtest/{job_id}/stream  (SSE)
  │          │
  │    ┌─────▼──────────────────────────────────┐
  │    │  FastAPI (web/app.py)  workers=1        │
  │    │                                         │
  │    │  BacktestJob (job_store.py)             │
  │    │  ├─ status: pending→running→done        │
  │    │  ├─ events: List[dict] (리플레이용)     │
  │    │  └─ queue: asyncio.Queue (스트리밍용)   │
  │    │                                         │
  │    │  ThreadPoolExecutor                     │
  │    │  └─ run_backtest() ← finagent/main.py  │
  │    │       └─ run_day() × N (Claude API)    │
  │    │            └─ progress_callback()       │
  │    │                 └─ call_soon_threadsafe │
  │    └────────────────────────────────────────┘
  │
  ├── GET /charts/{job_id}/{filename}  → PNG 파일
  └── GET /  → index.html (정적 파일)
```

**핵심 흐름**: `run_backtest()`는 스레드에서 실행 → 각 거래일 완료 시 `progress_callback`이 `call_soon_threadsafe`로 asyncio Queue에 이벤트 push → SSE 제너레이터가 Queue에서 꺼내 브라우저로 전송.

---

## 단계별 구현

---

### Step A — `finagent/main.py` 수정

#### 의도

기존 `run_backtest()`는 결과만 반환하고 중간 상태를 외부로 전달하는 방법이 없었다. 웹 UI가 실시간 진행 상황을 보여주려면 각 거래일이 끝날 때마다 결정 내용을 외부로 넘겨야 한다.

`progress_callback`을 선택적 파라미터로 추가한 이유:
- CLI 사용자는 아무것도 바꿀 필요가 없다 (기본값 `None`, 기존 동작 유지)
- 웹 서버만 콜백을 제공해 이벤트를 수신한다
- `run_day()` 자체는 건드리지 않는다 — 변경 범위를 `run_backtest()` 루프 내 단 4줄로 최소화

#### 구현

```python
# finagent/main.py

def run_backtest(
    ...
    progress_callback=None,  # 추가된 파라미터
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

#### 설계 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 콜백 시점 | `run_day()` 직후 | 결정이 내려지고 포트폴리오에 반영된 직후 — 가장 자연스러운 업데이트 시점 |
| 콜백 오류 처리 | 별도 `try/except`로 감쌈 | 콜백 오류가 백테스트 루프 자체를 중단시키면 안 된다 |
| `run_day()` 실패 시 콜백 미호출 | `decision is None` 체크 | 실패한 날의 "HOLD"를 가짜로 보고하는 것보다 정직하게 건너뛰는 것이 낫다 |

---

### Step B — `web/job_store.py` — Job 상태 관리

#### 의도

백테스트 한 번 실행 = 하나의 "Job". Job마다 고유 ID가 필요하고, 진행 상태(시작/실행 중/완료/오류)와 누적 이벤트 목록을 서버 메모리에 보관해야 한다.

`asyncio.Queue`를 Job 내부에 포함시킨 이유: SSE 스트리밍 엔드포인트는 비동기 컨텍스트에서 실행되고, `run_backtest()`는 동기 스레드에서 실행된다. 이 두 세계를 연결하려면 스레드 안전한 Queue가 필요하다. Job 객체 안에 Queue를 포함시키면 별도 딕셔너리 관리가 필요 없다.

`events: List[dict]`를 별도로 유지하는 이유: SSE 연결이 끊기면 `EventSource`가 자동 재연결한다. 이때 Queue는 이미 비어있으므로 누적 이벤트를 `events` 리스트에서 리플레이해야 한다.

#### 구현

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

_jobs: Dict[str, BacktestJob] = {}  # 프로세스 내 단일 딕셔너리
```

#### 설계 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 저장소 | 프로세스 내 `dict` | Redis 같은 외부 저장소는 불필요한 의존성 추가. `workers=1`로 단일 프로세스 보장 |
| Job 정리 | 미구현 (현재) | 장시간 운영 시 메모리 누수 가능성 있으나, 백테스팅 도구 특성상 한 세션에 수십 개 이상의 Job이 쌓이는 일은 없음 |
| ID | `uuid.uuid4()` | 충돌 가능성 없고, 추측 불가 (URL 보안) |

---

### Step C — `web/schemas.py` — 요청/응답 모델

#### 의도

FastAPI의 Pydantic 통합을 활용해 프론트엔드에서 넘어오는 JSON의 유효성 검사를 자동화한다. 잘못된 입력은 HTTP 422로 필드별 상세 오류를 반환하므로 라우트 코드에 수동 검증 로직을 쓸 필요가 없다.

#### 구현

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

#### 검증 케이스

| 검증 항목 | 오류 메시지 |
|-----------|-------------|
| `end <= start` | "end must be after start" |
| 날짜 범위 > 365일 | "Date range must not exceed 365 days" |
| `trader_preference` 미정의 값 | "trader_preference must be one of ..." |
| 날짜 형식 오류 (YYYY-MM-DD 아님) | Pydantic 내장 date 파싱 오류 |

365일 제한을 둔 이유: Claude API 3~4회 × 약 250 거래일 = 750~1000번의 API 호출, 실질적으로 몇 시간이 걸린다. 합리적인 상한선이 필요했다.

---

### Step D — `web/routes/backtest.py` — SSE 스트리밍 (핵심)

#### 의도

이 파일이 Web UI의 핵심이다. 두 가지 문제를 동시에 해결해야 한다.

1. **동기 ↔ 비동기 브리지**: `run_backtest()`는 동기 함수이므로 FastAPI의 비동기 이벤트 루프를 블로킹하면 안 된다. `run_in_executor`로 별도 스레드에서 실행해야 한다.

2. **스레드 → 이벤트 루프 통신**: `progress_callback`은 스레드에서 호출되는데, `asyncio.Queue.put_nowait()`는 이벤트 루프에서만 안전하다. `loop.call_soon_threadsafe()`가 이 두 세계를 연결한다.

#### 지연 임포트 (`_get_run_backtest`)

```python
def _get_run_backtest():
    from finagent.main import run_backtest
    return run_backtest
```

모듈 최상단에서 `from finagent.main import run_backtest`를 하면, 서버 기동 시점에 finagent의 모든 의존성(feedparser, pykrx, chromadb 등)이 임포트된다. 이 의존성들이 설치되지 않은 환경에서 서버 자체를 띄울 수 없게 된다. 지연 임포트를 사용하면 서버 기동은 항상 성공하고, 실제 백테스트 요청이 들어왔을 때만 임포트를 시도한다.

#### progress_callback — 스레드 안전 이벤트 push

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
        job.events.append(event)                          # 리플레이용 누적
        loop.call_soon_threadsafe(job.queue.put_nowait, event)  # SSE용 push
    return callback
```

`job.events.append()`는 GIL이 보호하는 범위 내의 단순 연산이라 스레드 안전하다.  
`loop.call_soon_threadsafe()`는 이름 그대로 다른 스레드에서 이벤트 루프에 작업을 안전하게 예약한다.

#### POST `/api/backtest` 흐름

```
1. ANTHROPIC_API_KEY 존재 확인  → 없으면 HTTP 400
2. 파라미터 유효성 검사          → Pydantic이 HTTP 422 처리
3. create_job()                 → UUID job_id 발급
4. job별 격리 디렉토리 생성:
   job_data/{job_id}/charts/
   job_data/{job_id}/portfolio.db
   job_data/{job_id}/memory_db/
5. asyncio.create_task(run_in_thread()) — 즉시 반환, 백그라운드 실행
6. {job_id, stream_url} 반환
```

5번이 핵심: `create_task`는 코루틴을 이벤트 루프에 예약만 하고 기다리지 않으므로, 클라이언트는 즉시 `job_id`를 받아 SSE에 연결할 수 있다.

#### GET `/api/backtest/{job_id}/stream` — SSE 제너레이터

```python
async def event_generator():
    # 1. 재연결 시 누적 이벤트 리플레이
    for evt in list(job.events):
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    if job.status in ("done", "error"):
        return   # 이미 끝난 Job → 리플레이만 하고 종료

    # 2. 새 이벤트 스트리밍
    while True:
        try:
            evt = await asyncio.wait_for(job.queue.get(), timeout=60.0)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"  # nginx/프록시 타임아웃 방지
            continue
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        if evt.get("type") in ("done", "error"):
            break
```

SSE 메시지 형식은 `data: {JSON}\n\n`이다. 빈 줄(`\n\n`)이 이벤트 경계를 구분한다.  
`ensure_ascii=False`는 한글 추론 텍스트가 `\uXXXX` 이스케이프 없이 그대로 전송되도록 한다.  
heartbeat 줄(`: ...`)은 SSE 주석으로, 브라우저에는 이벤트로 전달되지 않는다.

#### SSE 이벤트 스키마

```
# 진행 이벤트 — 거래일마다 1회
data: {"type":"progress","day":1,"total":62,"date":"2024-01-02",
       "action":"HOLD","reasoning":"...","pct":1.6}

# 완료 이벤트 — 1회
data: {"type":"done","result":{"total_return_pct":12.5,"sharpe_ratio":1.23,...}}

# 오류 이벤트
data: {"type":"error","message":"Claude API rate limit exceeded"}
```

---

### Step E — Job 격리 전략

#### 의도

SQLite와 ChromaDB는 동시 쓰기에 취약하다. SQLite는 DB 파일 단위 잠금이고, ChromaDB의 `PersistentClient`는 디렉토리 단위 잠금을 사용한다. 여러 백테스트가 동시에 실행될 경우 같은 파일/디렉토리를 사용하면 `OperationalError: database is locked`가 발생한다.

해결책: 각 Job에게 독립된 디렉토리를 부여한다.

```
job_data/
└── {uuid}/
    ├── portfolio.db   ← 이 Job만의 SQLite
    ├── memory_db/     ← 이 Job만의 ChromaDB
    └── charts/        ← 이 Job의 차트 PNG
```

#### 설계 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 루트 디렉토리 | `job_data/` | 프로젝트 루트에 위치, `.gitignore`에 추가 |
| 디렉토리 생성 시점 | `POST /api/backtest` 처리 시 | `run_backtest()` 호출 전에 차트 디렉토리가 존재해야 함 |
| 정리 정책 | 미구현 | 향후 TTL 기반 정리 추가 가능. 현재는 사용자가 수동으로 삭제 |

---

### Step F — `web/routes/results.py` + `web/routes/charts.py`

#### 의도

SSE는 단방향 푸시이므로 연결이 끊기면 이벤트를 놓칠 수 있다. `GET /result`는 폴링 대안으로, SSE 없이 최종 결과를 확인할 수 있게 한다. `GET /trades`는 클라이언트가 거래 내역을 별도로 가져올 수 있는 엔드포인트다.

`charts.py`의 경로 순회(Path Traversal) 방어:

```python
if ".." in job_id or ".." in filename or "/" in filename or "\\" in filename:
    raise HTTPException(400)
```

`job_id`와 `filename` 모두 검증한다. `..`이 있으면 디렉토리를 벗어날 수 있고, `/`나 `\`가 있으면 하위 디렉토리 접근이 가능하다.

`chart-list` 엔드포인트는 프론트엔드의 일별 차트 브라우저가 "어떤 날짜의 차트가 존재하는지"를 동적으로 파악하기 위해 필요하다. 프론트엔드에서 날짜 범위를 직접 계산하면 실제 생성된 파일과 불일치가 생길 수 있어 서버가 직접 목록을 반환한다.

---

### Step G — `web/app.py` — 애플리케이션 팩토리

#### 의도

라우터를 등록하는 순서가 중요하다. FastAPI는 라우트를 등록 순서대로 매칭한다. `StaticFiles`를 루트(`/`)에 마운트하면 이후에 등록된 API 라우트는 절대 매칭되지 않는다. 따라서 **API 라우터를 먼저 등록하고, `StaticFiles`를 마지막에** 마운트해야 한다.

```python
def create_app():
    app = FastAPI(...)
    app.include_router(backtest.router)   # /api/backtest
    app.include_router(results.router)    # /api/backtest/{id}/result
    app.include_router(charts.router)     # /charts/{id}/{file}
    # 마지막: 나머지 모든 경로를 static으로 처리
    app.mount("/", StaticFiles(directory="web/static", html=True), name="static")
    return app
```

`html=True` 옵션은 `GET /`가 `index.html`을 반환하도록 한다.

`workers=1` 필수 설정 이유: `_jobs` 딕셔너리는 프로세스 메모리에 있다. `workers=2` 이상이면 각 프로세스가 독립된 `_jobs`를 가지므로, Job을 만든 프로세스와 SSE를 처리하는 프로세스가 달라질 수 있다. 프로세스 간 공유가 필요하면 Redis 같은 외부 저장소로 교체해야 한다.

---

### Step H — 프론트엔드 (`web/static/`)

#### 의도

React나 Vue 같은 프레임워크 없이 Vanilla HTML/CSS/JS로 구현했다. 이유:
- 빌드 파이프라인(webpack, npm)이 불필요하다. `web/static/`을 그대로 서빙하면 된다.
- 브라우저 내장 `EventSource` API로 SSE 처리가 완결된다
- UI가 복잡하지 않다 — 3개 패널(폼·진행·결과)의 상태 전환이 전부

#### `index.html` — 3패널 단일 페이지

```
#panel-form (초기 표시)
  │ 제출
  ▼
#panel-progress (백테스트 진행 중)
  │ done 이벤트
  ▼
#panel-results (결과)
  │ "새 백테스트" 클릭
  └─ → #panel-form (초기 상태 복귀)
```

세 패널을 별도 페이지로 분리하지 않고 `display: none / block`으로 전환하는 이유: 페이지 이동 없이 SSE 연결을 유지할 수 있다. 페이지 이동이 발생하면 `EventSource`가 끊긴다.

#### `app.js` — SSE 클라이언트 상태 머신

주요 함수:

```javascript
// 폼 제출 → POST → SSE 연결
form.addEventListener('submit', async (e) => { ... });

// SSE 이벤트 분기
eventSource.onmessage = (evt) => {
    if (data.type === 'progress') handleProgress(data);
    else if (data.type === 'done')  handleDone(data.result, formData);
    else if (data.type === 'error') showError(data.message);
};

// 진행 이벤트: 진행 바 + 로그 항목 추가
function handleProgress(data) {
    progressFill.style.width = data.pct + '%';
    logList.appendChild(item);           // 애니메이션 포함
    logList.scrollTop = logList.scrollHeight;  // 자동 스크롤
}

// 완료 이벤트: 차트 목록 가져오기 → KPI 렌더링
async function handleDone(result, formData) {
    const { charts } = await fetch(`/api/backtest/${currentJobId}/chart-list`).then(r=>r.json());
    renderResults(result, formData);
    showResultsPanel();
}
```

재연결 처리: `EventSource.onerror`는 연결 실패 시 자동 재연결을 시도한다. `readyState === CLOSED`가 아니면 `error` 이벤트가 와도 재연결 중인 것이므로 UI에 오류를 표시하지 않는다.

#### `style.css` — 다크 테마 디자인

CSS 변수로 색상 시스템 정의:

```css
:root {
  --bg: #0f1117;          /* 최외곽 배경 */
  --surface: #1a1d27;     /* 카드 배경 */
  --buy: #10b981;         /* 초록 — BUY */
  --sell: #ef4444;        /* 빨강 — SELL */
  --hold: #f59e0b;        /* 노랑 — HOLD */
}
```

BUY/SELL/HOLD 뱃지 색상은 금융 데이터 시각화의 관례(초록/빨강/중립)를 따른다.

로그 항목에 `animation: fadeIn 0.3s ease` 적용: 새 항목이 부드럽게 나타나 진행 중임을 시각적으로 강조한다.

---

### Step I — `run_web.py` — 서버 진입점

#### 의도

`uvicorn.run()`을 `if __name__ == "__main__"` 블록에만 넣는 이유: `web.app:app`을 가리키면 `uvicorn web.app:app`으로도, `python run_web.py`로도 기동할 수 있다. `--reload` 옵션을 사용하는 개발 모드에서는 `if __name__` 블록이 반복 실행되면 안 된다.

```python
# run_web.py
if __name__ == "__main__":
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, workers=1)
```

`workers=1` 주석을 코드에 직접 명시한 이유: 이것을 바꾸면 job store가 깨진다는 사실을 코드를 읽는 사람이 즉시 알 수 있어야 한다.

---

## 에러 처리 설계

| 오류 상황 | 처리 방식 | 이유 |
|-----------|-----------|------|
| `ANTHROPIC_API_KEY` 미설정 | `POST /api/backtest` → HTTP 400 즉시 반환 | Job 생성 전에 차단. 30분짜리 작업이 시작된 후에 실패하면 복구 불가 |
| 잘못된 파라미터 (날짜 역전, 범위 초과 등) | Pydantic ValidationError → HTTP 422 필드별 상세 | 라우트 코드 없이 자동 처리 |
| 특정 거래일 Claude API 오류 | 기존 `except` 로 skip, 스트림 계속 | 한 날의 오류가 전체 백테스트를 중단하면 안 됨. 기존 `main.py`의 설계 유지 |
| 전체 백테스트 실패 (`run_in_thread` 외부 예외) | `error` SSE 이벤트 전송 → 프론트엔드 오류 배너 표시 | |
| SSE 연결 끊김 | `EventSource` 자동 재연결 + `job.events` 리플레이 | 재연결 시 처음부터 다시 볼 수 있음 |
| 차트 파일 Path Traversal 시도 | `..`, `/`, `\` 포함 시 HTTP 400 | `job_data/` 밖의 파일에 접근 차단 |

---

## 파일 구조 최종

```
web/
├── __init__.py
├── app.py                  40줄   FastAPI 앱 팩토리 + 라우터 등록
├── job_store.py            31줄   BacktestJob 데이터클래스 + _jobs 딕셔너리
├── schemas.py              44줄   BacktestRequest (검증 포함) + 응답 모델
└── routes/
    ├── __init__.py
    ├── backtest.py        134줄   POST /api/backtest + GET .../stream (SSE 핵심)
    ├── results.py          44줄   GET .../result + .../trades (폴백)
    └── charts.py           38줄   GET /charts/{job_id}/{filename} + chart-list

web/static/
├── index.html             175줄   3패널 단일 페이지 UI
├── style.css              220줄   다크 테마 + 애니메이션
└── app.js                 230줄   SSE 클라이언트 + DOM 조작 + 결과 렌더링

run_web.py                  14줄   uvicorn 진입점
```

수정된 기존 파일:
- `finagent/main.py` — `progress_callback` 파라미터 추가 (+13줄)
- `requirements.txt` — `fastapi`, `uvicorn[standard]`, `python-multipart` 추가
- `.gitignore` — `job_data/` 추가

---

## 주요 설계 결정 요약

| 결정 | 선택 | 대안 | 이유 |
|------|------|------|------|
| UI 프레임워크 | FastAPI + SSE | Streamlit, React | 장시간 블로킹 작업 + 실시간 스트리밍에 최적 |
| 실시간 통신 | SSE (EventSource) | WebSocket | 단방향으로 충분, 자동 재연결, 라이브러리 불필요 |
| 백그라운드 실행 | ThreadPoolExecutor (`run_in_executor`) | asyncio, Celery | `run_backtest()`가 동기 함수 — 스레드가 유일한 선택 |
| 스레드→asyncio 브리지 | `call_soon_threadsafe` | 공유 변수 폴링 | 이벤트 루프에 안전하게 작업 예약하는 공식 방법 |
| Job 저장소 | 프로세스 내 dict | Redis | 외부 의존성 없이 `workers=1`로 충분 |
| Job 격리 | `job_data/{uuid}/` 하위 디렉토리 | 공유 DB + 심볼 키 | SQLite 잠금 + ChromaDB 디렉토리 잠금 충돌 방지 |
| finagent 임포트 | 지연 임포트 (`_get_run_backtest`) | 모듈 최상단 임포트 | 서버 기동이 finagent 의존성에 독립적으로 유지 |
| 프론트엔드 | Vanilla JS | React, Vue | 빌드 불필요, 3패널 전환 로직으로 충분 |
| 라우트 등록 순서 | API → StaticFiles 마지막 | — | FastAPI는 등록 순서대로 매칭 — StaticFiles가 먼저면 API가 가려짐 |

---

## 실행 방법

```bash
# 의존성 설치
conda activate finagent
pip install "fastapi>=0.111.0" "uvicorn[standard]>=0.30.0"

# API 키 설정
export ANTHROPIC_API_KEY=sk-ant-...

# 서버 실행
python run_web.py
# → http://localhost:8000 접속

# 개발 모드 (코드 변경 시 자동 재시작)
uvicorn web.app:app --reload --port 8000
```

검증 항목:
- [ ] 폼 제출 → 즉시 진행 패널 전환, job_id 발급
- [ ] 거래일마다 로그 항목 추가, 진행 바 업데이트
- [ ] 완료 후 KPI 카드·성과 차트·거래 내역 표시
- [ ] 브라우저 탭 닫고 재접속 → 이벤트 리플레이로 현재 상태 복원
- [ ] `ANTHROPIC_API_KEY` 미설정 → 에러 배너 표시 (HTTP 400)
- [ ] `end < start` 입력 → HTTP 422 즉시 반환
- [ ] 일별 차트 브라우저: Kline / Trading 탭 전환, 날짜별 차트 표시
