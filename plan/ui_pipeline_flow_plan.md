# FinAgent UI 개선 — 파이프라인 Flow 시각화 계획

---

## Context

Web UI의 백테스트 진행 패널은 전체 날짜 대비 진행률(%)과 거래일별 결정 로그만 표시했다.  
하루치 백테스트가 완료되기까지 Claude API를 3~4회 호출하는 긴 대기 시간 동안,  
현재 파이프라인의 **어느 단계가 실행 중인지** 사용자가 전혀 알 수 없어 답답함이 있었다.

**목표**: 실행 중인 파이프라인 단계를 아이콘 + 애니메이션으로 실시간 표시하고,  
기존 날짜 진행률 바와 함께 파이프라인 전체 흐름을 한눈에 볼 수 있게 한다.

---

## 파이프라인 구조

`run_day()` 내 단계 실행 순서:

```
OHLCV 수집 (루프 외)  →  ┌──────────────── Daily Loop ────────────────────────┐
  KRX API 1회            │  뉴스수집 → MI → LLR → HLR → 매매결정 → 거래실행  │
                         └────────────────────────────────────────────────────┘
```

| 단계 ID              | 표시명    | 아이콘 | 타입   | 설명                       |
|----------------------|-----------|--------|--------|----------------------------|
| `ohlcv_fetch`        | OHLCV 수집 | 📈    | API    | KRX pykrx 가격 데이터 수집 |
| `news_fetch`         | 뉴스 수집  | 📰    | API    | Naver RSS 뉴스 피드        |
| `market_intelligence`| 시장 분석  | 🧠    | AI     | MI 모듈 Claude 호출        |
| `low_level_reflection`| 기술 분석 | 📊    | AI     | LLR 모듈 Claude + 차트     |
| `high_level_reflection`| 전략 반성| 🔍   | AI     | HLR 모듈 Claude + 차트     |
| `decision_making`    | 매매 결정  | ⚡    | AI     | DM 모듈 Claude 최종 결정   |
| `trade_execution`    | 거래 실행  | 💼    | DB     | Portfolio SQLite 기록      |

---

## 기술 설계

### SSE 이벤트 추가

기존 `progress` (거래일 완료), `done`, `error` 외에 **`step` 이벤트** 추가:

```json
{"type": "step", "step": "market_intelligence"}
```

- 각 단계 **시작 직전**에 발화 (단계가 실행되는 동안 아이콘이 활성화)
- `job.events`에 저장하지 않음 (재연결 리플레이 불필요 — 시각 전용)

### 이벤트 흐름

```
[스레드] run_backtest()
    step_callback("ohlcv_fetch")   →  SSE: {"type":"step","step":"ohlcv_fetch"}
    fetcher.get_price_data()
    loop:
        step_callback("news_fetch")          →  SSE step 이벤트
        get_news() + plot_charts()
        step_callback("market_intelligence")  →  SSE step 이벤트
        mi_module.run()
        ...
        step_callback("trade_execution")      →  SSE step 이벤트
        portfolio.execute()
        progress_callback(...)               →  SSE: {"type":"progress",...}
```

### 스레드 안전

`step_callback`은 `progress_callback`과 동일한 패턴으로 `loop.call_soon_threadsafe(queue.put_nowait, event)`를 사용한다.  
단, `job.events`에 append하지 않아 이벤트 누적량을 최소화한다.

---

## UI 컴포넌트 설계

### 레이아웃 (진행 패널 내부)

```
┌─────────────────────────────────────────────────────────────────────┐
│  카드: 백테스트 진행 중…                                             │
│                                                                      │
│  [📈]  →  ┌─────────── Daily Loop ──────────────────────────────┐  │
│  OHLCV    │ [📰]→[🧠]→[📊]→[🔍]→[⚡]→[💼]                      │  │
│  KRX API  │  뉴스  MI  LLR  HLR  결정  실행                      │  │
│           └────────────────────────────────────────────────────┘  │
│                                                                      │
│  62 / 62 거래일  ████████████████  100%                            │
│                                                                      │
│  [로그 리스트]                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 노드 상태

| 상태       | 시각 효과                                            |
|------------|------------------------------------------------------|
| inactive   | 불투명도 28%, 흐린 테두리                            |
| active     | 불투명도 100%, 색상 glow + 확산 pulse ring 애니메이션|
| completed  | 불투명도 55%, 초록 ✓ 뱃지, 0.6초 후 inactive 복귀   |

### 타입별 색상

| 타입 | 색상       | 적용 단계                        |
|------|------------|----------------------------------|
| API  | `#38bdf8`  | OHLCV 수집, 뉴스 수집            |
| AI   | `#a78bfa`  | MI, LLR, HLR, 매매 결정 (Claude) |
| DB   | `#fb923c`  | 거래 실행 (Portfolio DB)          |

AI 타입 노드는 active 시 `scale(1.07)` 호흡 애니메이션 추가 (처리 중임을 강조).

---

## 수정 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `finagent/main.py` | `run_day()`에 `step_callback` 파라미터 추가, 각 단계 직전 호출. `run_backtest()`에 `step_callback` 파라미터 추가, OHLCV 수집 전 발화 |
| `web/routes/backtest.py` | `_make_step_callback()` 추가, `run_backtest` 호출에 `step_callback=step_cb` 전달 |
| `web/static/index.html` | `#panel-progress` 내 파이프라인 flow HTML 추가 |
| `web/static/style.css` | 파이프라인 노드·화살표·루프 래퍼·애니메이션 CSS 추가 |
| `web/static/app.js` | `activatePipelineStep()`, `completePipelineDay()`, `resetPipeline()` 추가, SSE `step` 이벤트 핸들러 연결 |

---

## 기타 UI 수정

| 항목 | 변경 내용 | 커밋 |
|------|-----------|------|
| 종료일 기본값 | 하드코딩 `2026-05-07` → JS로 `new Date() - 1일` 동적 계산 | `04aa33e` |
| 시작일 기본값 | `2024-01-02` → `2026-01-02` | `04aa33e` |
| 성과 차트 폰트 | 모듈 최상단 폰트 설정 → `plot_performance()` 내부로 이동, `Pretendard` 지정 | `917d487` |

---

## 검증 방법

```bash
python run_web.py
# → http://localhost:8000

# 짧은 날짜 범위 (1~2주)로 실행
# symbol: 005930, stock_name: 삼성전자
```

확인 항목:
- [ ] 폼 제출 직후 OHLCV 수집 노드 활성화 (pulse ring + glow)
- [ ] 각 거래일마다 뉴스 → MI → LLR → HLR → 결정 → 실행 순서로 노드 전환
- [ ] 거래일 완료 시 전체 노드 completed 플래시(✓) 후 0.6초 뒤 inactive 리셋
- [ ] 전체 진행 바와 파이프라인 flow가 동시에 업데이트됨
- [ ] AI 노드(MI·LLR·HLR·결정) active 시 호흡 애니메이션 동작
- [ ] 모바일/좁은 화면에서 파이프라인 가로 스크롤 동작
