# FinAgent Web UI 구현 계획

> 이 파일은 구현 시작 시 `plan/web_ui_plan.md`로 복사됩니다.

---

## Context

FinAgent는 현재 CLI 전용 백테스팅 도구입니다. 사용자가 웹 브라우저에서 백테스트 파라미터를 입력하고, 실행 진행 상황을 실시간으로 확인하며, 결과(수익률·차트·거래내역)를 바로 볼 수 있도록 Web 기반 UI를 추가합니다.

**핵심 제약**: 하루치 백테스트(run_day)마다 Claude API 3~4회 호출 → 3개월 백테스트 = 30분 이상. UI에서 실시간 진행률을 보여주지 않으면 사용 불가.

---

## 기술 스택 결정

**FastAPI + SSE (Server-Sent Events) + Vanilla HTML/CSS/JS**

| 비교 | FastAPI+SSE | Streamlit |
|------|-------------|-----------|
| 장시간 백그라운드 작업 | ThreadPoolExecutor로 격리, SSE로 스트리밍 | 스크립트 재실행 모델 — 블로킹 함수와 충돌 |
| 커스텀 UI | 완전한 HTML/CSS 제어 | 제한적 |
| 동시 실행 격리 | job별 독립 디렉토리 | 세션 상태 복잡 |

**결정**: FastAPI + SSE. `workers=1` (인-프로세스 job store 사용).

---

## 최종 파일 구조

```
FinAgent/
├── finagent/
│   └── main.py                    ← 수정: progress_callback 파라미터 추가
│
├── web/
│   ├── __init__.py
│   ├── app.py                     ← FastAPI 앱 팩토리
│   ├── job_store.py               ← 인메모리 Job 상태 관리
│   ├── schemas.py                 ← API 요청/응답 Pydantic 모델
│   └── routes/
│       ├── __init__.py
│       ├── backtest.py            ← POST /api/backtest, GET /api/backtest/{id}/stream
│       ├── results.py             ← GET /api/backtest/{id}/result + trades
│       └── charts.py              ← GET /charts/{job_id}/{filename}
│
├── web/static/
│   ├── index.html                 ← 단일 페이지 UI (폼 → 진행 → 결과)
│   ├── style.css
│   └── app.js                     ← SSE EventSource 클라이언트
│
├── job_data/                      ← 런타임 생성 (gitignore)
│   └── {job_id}/
│       ├── portfolio.db
│       ├── memory_db/
│       └── charts/
│
├── run_web.py                     ← 서버 진입점
└── requirements.txt               ← fastapi, uvicorn[standard] 추가
```

---

## 수정 파일

### `finagent/main.py`

`run_backtest()` 시그니처에 `progress_callback` 파라미터 추가:

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

`run_day()`와 다른 모듈은 변경 없음.

### `requirements.txt`

```
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
```

---

## 신규 파일 상세

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

### `web/routes/backtest.py` (핵심 로직)

**POST `/api/backtest`**:
1. `ANTHROPIC_API_KEY` 존재 여부 확인 → 없으면 HTTP 400
2. 날짜 유효성 검사 (end > start, 범위 ≤ 365일)
3. `create_job()` → job별 격리 디렉토리 생성
4. `asyncio.create_task(run_in_thread())` 로 백테스트 비동기 실행
5. `{job_id, stream_url}` 즉시 반환

**GET `/api/backtest/{job_id}/stream`** (SSE):
```python
async def event_generator():
    # 1. 이미 쌓인 이벤트 리플레이 (재연결 대응)
    for evt in job.events:
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
    # 2. 새 이벤트 스트리밍
    while True:
        evt = await asyncio.wait_for(job.queue.get(), timeout=30.0)
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        if evt.get("type") in ("done", "error"):
            break
```

**스레드 → asyncio 브리지**:
```python
# progress_callback (동기, 스레드에서 실행)
def progress_callback(day_index, total_days, current_date, action, reasoning):
    event = {"type": "progress", "day": day_index, "total": total_days,
             "date": str(current_date), "action": action,
             "reasoning": reasoning, "pct": round(day_index/total_days*100, 1)}
    job.events.append(event)
    asyncio.get_event_loop().call_soon_threadsafe(job.queue.put_nowait, event)
```

### SSE 이벤트 스키마

```
# 진행 이벤트 (거래일마다 1회)
data: {"type":"progress","day":1,"total":62,"date":"2024-01-02","action":"HOLD","reasoning":"...","pct":1.6}

# 완료 이벤트 (1회)
data: {"type":"done","result":{"total_return_pct":12.5,"sharpe_ratio":1.23,...}}

# 에러 이벤트
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

### `web/static/index.html` — 3단계 단일 페이지

1. **폼 패널**: symbol, stock_name, start, end, initial_cash, preference 입력
2. **진행 패널** (제출 후 표시):
   - `<progress>` 진행 바 (현재일 / 전체일)
   - 스크롤 로그: `날짜 | BUY/SELL/HOLD 뱃지 | reasoning 요약`
3. **결과 패널** (`done` 이벤트 수신 시 표시):
   - KPI 카드: 총 수익률, 연간 수익률, Sharpe Ratio, MDD, 벤치마크 대비 초과수익률
   - 거래 카운트: BUY / SELL / HOLD 횟수
   - 성과 차트 이미지 (`performance_{symbol}_{start}_{end}.png`)
   - 거래 내역 테이블 (date, action, price, quantity, reasoning)

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

## Job 격리 전략

각 백테스트 job은 `job_data/{job_id}/` 하위에 독립 디렉토리를 사용합니다:
- `portfolio.db` — SQLite 동시 쓰기 충돌 방지
- `memory_db/` — ChromaDB PersistentClient 디렉토리 잠금 충돌 방지
- `charts/` — 차트 파일 네이밍 충돌 방지

`workers=1` 설정 필수 (인-프로세스 `_jobs` 딕셔너리 공유 불가).

---

## 에러 처리

| 상황 | 처리 |
|------|------|
| ANTHROPIC_API_KEY 미설정 | `POST /api/backtest` → HTTP 400 즉시 반환 |
| 잘못된 파라미터 | Pydantic 검증 → HTTP 422 필드별 상세 |
| 특정 일자 Claude 오류 | 기존 `except Exception` 로 skip, 스트림 계속 |
| 전체 backtest 실패 | SSE `error` 이벤트 전송 |
| SSE 연결 끊김 | EventSource 자동 재연결 + `job.events` 리플레이 |

---

## 구현 순서

1. `finagent/main.py` — `progress_callback` 파라미터 추가 및 CLI 검증
2. `web/job_store.py` + `web/schemas.py` 생성
3. `web/routes/backtest.py` — SSE 스트리밍 핵심 로직
4. `web/routes/results.py` + `web/routes/charts.py` 생성
5. `web/app.py` + `run_web.py` 생성
6. `web/static/` — index.html → style.css → app.js 순서로 UI 구현
7. `requirements.txt` 업데이트
8. 통합 테스트: 짧은 날짜 범위(1~2주)로 실제 백테스트 실행

---

## 검증 방법

```bash
# 의존성 설치
pip install fastapi "uvicorn[standard]"

# 서버 실행
export ANTHROPIC_API_KEY=sk-ant-...
python run_web.py

# 브라우저에서 http://localhost:8000 접속
# symbol: 005930, stock_name: 삼성전자, 짧은 날짜 범위로 테스트
```

확인 항목:
- [ ] 폼 제출 → 즉시 진행 패널 전환
- [ ] 거래일마다 로그 항목 추가, 진행 바 업진
- [ ] 완료 후 KPI 카드·차트·거래 테이블 표시
- [ ] 브라우저 탭 닫고 재접속 → SSE 이벤트 리플레이로 진행 상태 복원
- [ ] ANTHROPIC_API_KEY 미설정 상태에서 에러 메시지 표시
