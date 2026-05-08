# FinAgent

**A Multimodal Foundation Agent for Financial Trading**

논문 [arxiv 2402.18485](https://arxiv.org/abs/2402.18485) 기반의 멀티모달 LLM 트레이딩 에이전트.  
뉴스·가격 데이터 분석, 캔들차트 Vision 반성, 메모리 기반 학습, 기술적 지표 주입을 결합한 백테스팅 파이프라인입니다.

![The overall architecture of FinAgent. The ordinal numbers in the figure represent the order of execution, where
augmented tools are implemented with the decision-making module.](assets/architecture.png)

[![Stars](https://img.shields.io/github/stars/effective-p/FinAgent?style=flat)](https://github.com/effective-p/FinAgent/stargazers)
[![Forks](https://img.shields.io/github/forks/effective-p/FinAgent?style=flat)](https://github.com/effective-p/FinAgent/network/members)
[![Contributors](https://img.shields.io/github/contributors/effective-p/FinAgent?style=flat)](https://github.com/effective-p/FinAgent/graphs/contributors)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/-Python-3776AB?logo=python&logoColor=white)
![Conda](https://img.shields.io/badge/-Conda-3776AB?logo=anaconda&logoColor=white)
![Markdown](https://img.shields.io/badge/-FastAPI-3776AB?logo=fastapi&logoColor=white)
![Markdown](https://img.shields.io/badge/-Markdown-303030?logo=markdown&logoColor=white)

---

## 아키텍처

```
DataFetcher → MarketIntelligence → LowLevelReflection → HighLevelReflection → DecisionMaking → Portfolio
                      ↕                     ↕                      ↕
                            MemoryStore (ChromaDB, 3 collections)
```

| 모듈 | 역할 |
|------|------|
| `DataFetcher` | pykrx 주가 수집, 네이버 뉴스 RSS, mplfinance 차트 생성 |
| `MemoryStore` | ChromaDB 3-컬렉션 (MI / LLR / HLR), Diversified Retrieval |
| `MarketIntelligenceModule` | 최신 뉴스+가격 Claude 분석, 단/중/장기 쿼리 생성 |
| `LowLevelReflectionModule` | Kline 차트 Vision + 가격변동률 → 단/중/장기 원인 분석 |
| `HighLevelReflectionModule` | Trading 차트 Vision + 과거 액션 → 결정 평가 & 개선안 |
| `DecisionMakingModule` | 전체 분석 + 기술적 지표(MACD/KDJ/ZMR) 종합 → BUY/SELL/HOLD |
| `Portfolio` | SQLite 거래 내역, 포지션/현금 관리 |
| `metrics` | Equity curve, Sharpe ratio, MDD, Buy&Hold 벤치마크 |

---

## 설치

### 환경 생성 (conda 권장)

```bash
conda env create -f environment.yml
conda activate finagent
```

### 또는 pip

```bash
conda create -n finagent python=3.12 -y
conda activate finagent
pip install -r requirements.txt
```

> `pandas_ta` 0.4.x는 Python 3.12 이상을 요구합니다.

### 환경 변수

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## 실행 모드

FinAgent는 두 가지 실행 모드를 지원합니다. 위 환경 생성에 의해서 만들어지 vitual environment 상에서 실행하는 것을 권장 합니다.

---

### 모드 1 — CLI (터미널 직접 실행)

```bash
python finagent/main.py \
  --symbol 005930 \
  --stock-name 삼성전자 \
  --start 2024-01-02 \
  --end 2024-03-29 \
  --initial-cash 10000000 \
  --preference moderate
```

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--symbol` | 필수 | KRX 종목코드 (예: `005930`) |
| `--stock-name` | 필수 | 한글 종목명 (뉴스 검색용, 예: `삼성전자`) |
| `--start` | 필수 | 시작일 `YYYY-MM-DD` |
| `--end` | 필수 | 종료일 `YYYY-MM-DD` |
| `--initial-cash` | `10000000` | 초기 자금 (원) |
| `--preference` | `moderate` | 트레이더 성향 `aggressive` / `moderate` / `conservative` |
| `--db-path` | `portfolio.db` | SQLite 파일 경로 |
| `--memory-dir` | `memory_db` | ChromaDB 저장 디렉토리 |
| `--chart-dir` | `charts` | 차트 이미지 저장 디렉토리 |

#### 결과 예시

```
====================================================
  백테스팅 결과: 005930  (2024-01-02 ~ 2024-03-29)
====================================================
  최종 자산:              11,234,567원
  총 수익률:                    +12.35%
  연간 환산 수익률:             +52.18%
  Sharpe Ratio:                   1.234
  최대 낙폭 (MDD):               -5.67%
  연간 변동성:                   18.45%
----------------------------------------------------
  Buy & Hold 수익률:             +8.90%
  초과 수익률:                   +3.45%
----------------------------------------------------
  매수 횟수:                         5
  매도 횟수:                         4
  홀드 횟수:                        58
  성과 차트: charts/performance_005930_....png
====================================================
```
---

### 모드 2 — Web UI (브라우저 기반)

백테스트 파라미터 입력부터 실시간 진행 상황 확인, 결과 시각화까지 브라우저에서 처리합니다.

```bash
# 서버 실행
export ANTHROPIC_API_KEY=sk-ant-...
python run_web.py
```

브라우저에서 `http://localhost:8000` 접속 후 사용합니다.

#### 기능

| 단계 | 내용 |
|------|------|
| **폼 입력** | 종목코드, 종목명, 기간, 초기자금, 트레이더 성향 설정 |
| **실시간 진행** | 거래일마다 진행 바 업데이트 + BUY/SELL/HOLD 로그 스트리밍 |
| **결과 대시보드** | KPI 카드, 성과 차트, 거래 내역 테이블, 일별 차트 브라우저 |

##### 폼 입력
![Backtest request form](assets/backtest-req.png)

##### 실시간 진행
![Work flow](assets/workflow.png)

##### 결과 대시보드
![Result dashboard](assets/result-dashboard.png)

#### 동작 방식

- 백테스트는 백그라운드 스레드에서 실행되며, 진행 상황은 **SSE(Server-Sent Events)** 로 실시간 전송됩니다.
- 브라우저 탭을 닫았다가 재접속해도 누락된 이벤트를 자동 리플레이합니다.
- 각 백테스트 실행은 `job_data/{job_id}/` 에 독립적으로 저장됩니다 (SQLite·ChromaDB 충돌 방지).

> **주의**: `workers=1` 고정 (인-프로세스 Job 저장소 사용). 동시에 여러 백테스트를 실행하면 순차 처리됩니다.

---

## 전체 파이프라인

### 하루치 실행 흐름

매 거래일마다 아래 6단계가 순서대로 실행된다.

```
┌─────────────────────────────────────────────────────────────────────┐
│  run_day(symbol, target_date, price_df, ...)                        │
│                                                                     │
│  1. 데이터 수집                                                      │
│     ├─ pykrx          → price_df (OHLCV, look-ahead 차단)           │
│     ├─ 네이버 RSS      → news_list (±7일 필터)                       │
│     ├─ mplfinance     → kline_chart.png   (LLR용)                   │
│     └─ mplfinance     → trading_chart.png (HLR용, BUY▲/SELL▽ 마커) │
│                                                                     │
│  2. Market Intelligence (Claude API)                                │
│     ├─ 입력: news_list + price_df                                   │
│     ├─ 출력: summary + short/medium/long_term_query                 │
│     ├─ Diversified Retrieval: 3개 쿼리 → 과거 MI 최대 6개           │
│     └─ 저장: memory["market_intelligence"]                          │
│                                                                     │
│  3. Low-Level Reflection (Claude Vision)                            │
│     ├─ 입력: kline_chart.png + 가격변동률(1d/5d/10d/20d) + MI 요약   │
│     ├─ 출력: 단기/중기/장기 가격변동 원인 분석 + query               │
│     └─ 저장: memory["low_level_reflection"]                         │
│                                                                     │
│  4. High-Level Reflection (Claude Vision)                           │
│     ├─ 입력: trading_chart.png + 최근 14거래 내역 + MI + LLR        │
│     ├─ 출력: 결정 평가 + 개선 방안 + summary + query                │
│     └─ 저장: memory["high_level_reflection"]                        │
│                                                                     │
│  5. Decision Making (Claude API)                                    │
│     ├─ 입력: MI + LLR + HLR + 기술적지표(MACD/KDJ/ZMR) + 포트폴리오 │
│     └─ 출력: BUY / SELL / HOLD + reasoning                         │
│                                                                     │
│  6. 거래 실행                                                        │
│     └─ Portfolio.execute() → SQLite 기록                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

### 모듈 간 데이터 흐름

```
price_df ──┬──────────────────────────────────────────────────────────────────┐
           │                                                                  │
           ▼                                                                  │
     DataFetcher                                                              │
      ├─ news_list ──────────────────────┐                                   │
      ├─ kline_chart.png ────────────────┼──────────┐                        │
      └─ trading_chart.png ─────────────┼───────────┼──────────┐             │
                                        │           │           │             │
                                        ▼           │           │             │
                              MarketIntelligence    │           │             │
                                ├─ latest_summary ──┼───────────┼─────────────┼──┐
                                ├─ past_summary     │           │             │  │
                                └─ queries ─────────┼───────────┼─────────────┼──┤
                                                    │           │             │  │
                                        ┌───────────┘           │             │  │
                                        ▼                       │             │  │
                              LowLevelReflection                │             │  │
                                ├─ short_term_reasoning ────────┼─────────────┼──┤
                                ├─ medium_term_reasoning ───────┼─────────────┼──┤
                                ├─ long_term_reasoning ─────────┼─────────────┼──┤
                                └─ query ───────────────────────┼─────────────┼──┤
                                                                │             │  │
                                               ┌────────────────┘             │  │
                                               ▼                              │  │
                                     HighLevelReflection ◄── past_actions ◄───┘  │
                                       ├─ reasoning ──────────────────────────┐  │
                                       ├─ improvement ────────────────────────┤  │
                                       └─ query ──────────────────────────────┤  │
                                                                              │  │
                                                              ┌───────────────┘  │
                                                              ▼                  │
                                                      DecisionMaking ◄───────────┘
                                                        ├─ TechnicalSignals (내부 계산)
                                                        └─ Decision (BUY/SELL/HOLD)
                                                                    │
                                                                    ▼
                                                              Portfolio.execute()
```

---

### 메모리 시스템 상세

ChromaDB에 3개의 독립 컬렉션을 유지하며, 각 모듈이 자신의 컬렉션에만 읽고 쓴다.

```
┌──────────────────────────────────────────────────────────────────┐
│  MemoryStore (ChromaDB + all-MiniLM-L6-v2 임베딩)                │
│                                                                  │
│  ┌─────────────────────┐  ┌──────────────────────┐  ┌─────────┐ │
│  │ market_intelligence │  │ low_level_reflection │  │  high_  │ │
│  │                     │  │                      │  │  level_ │ │
│  │  저장: MI summary   │  │  저장: 단기+중기+장기  │  │  reflec │ │
│  │  메타: symbol, date,│  │  reasoning 합산 텍스트 │  │  tion   │ │
│  │  short/medium/long  │  │  메타: symbol, date  │  │         │ │
│  │  term_query         │  │                      │  │  저장:  │ │
│  └─────────────────────┘  └──────────────────────┘  │  summary│ │
│           ↑ ↓                      ↑ ↓               └─────────┘ │
│           MI                      LLR                    ↑ ↓     │
│    (Diversified Retrieval)   (short_term_query로 검색)    HLR     │
└──────────────────────────────────────────────────────────────────┘
```

**Diversified Retrieval** — MarketIntelligence의 핵심 메커니즘:

```python
# 단기·중기·장기 쿼리 3개로 독립 검색 → 중복 제거 → 최대 6개 과거 기억 수집
past_docs = memory.diversified_retrieve(
    "market_intelligence",
    queries=[short_term_q, medium_term_q, long_term_q],
    top_k_each=2,
)
```

---

### 기술적 지표 주입 흐름

```
price_df
    │
    ▼
get_technical_signals(df)
    ├─ MACD (12/26/9)   → golden/dead cross 감지 → "BUY signal (golden cross, MACD=0.12)"
    ├─ KDJ + RSI        → 과매수/과매도 감지      → "SELL signal (K=82, RSI=74, overbought)"
    └─ ZMR (z-score)    → MA20 대비 이탈 감지     → "HOLD (z-score=0.31, normal range)"
              │
              └─ signal_text (3줄 합산)
                          │
                          ▼
              DecisionMaking 프롬프트에 직접 주입
```

---

### 백테스팅 루프

```
run_backtest(symbol, start, end)
│
├─ 전체 기간 + lookback(90일) 한 번에 수집
│  price_df = fetcher.get_price_data(lookback_days = (end-start).days + 90)
│
├─ 거래일 필터링
│  trading_days = price_df[(start ≤ index ≤ end)]
│
└─ for target_date in trading_days:
       try:
           run_day(...)          ← 예외 발생 시 해당 일 skip, 다음 날 계속
       except:
           logger.exception(...)
│
└─ 성과 측정
   ├─ compute_equity_curve(trades, price_df, initial_cash)
   ├─ compute_benchmark(price_df, initial_cash)    ← Buy & Hold
   ├─ compute_performance(equity_curve)            ← Sharpe, MDD, 연환산 수익률
   └─ plot_performance(...)                        → charts/performance_{symbol}.png
```

---

### Look-ahead Bias 방지

백테스팅에서 미래 데이터가 현재 결정에 새어들어가는 것을 막기 위해,  
`run_day` 내부에서 `target_date` 이후 데이터를 잘라낸다.

```python
# run_day 내부
df = price_df.loc[:pd.Timestamp(target_date)]  # target_date 이전만 사용
current_price = float(df["Close"].iloc[-1])     # 당일 종가로 거래 실행
```

---

## 프로젝트 구조

```
finagent/
├── data/
│   └── fetcher.py                   # pykrx 주가, 네이버 RSS 뉴스, mplfinance 차트
├── memory/
│   └── store.py                     # ChromaDB 래퍼 (add / retrieve / diversified_retrieve)
├── modules/
│   ├── market_intelligence.py       # Claude API — 뉴스+가격 분석, Diversified Retrieval
│   ├── low_level_reflection.py      # Claude Vision — Kline 차트 + 가격변동 분석
│   ├── high_level_reflection.py     # Claude Vision — Trading 차트 + 과거 결정 평가
│   └── decision_making.py           # Claude API — 기술적 지표 주입, BUY/SELL/HOLD 결정
├── portfolio/
│   └── portfolio.py                 # SQLite 포지션·현금·거래내역 관리
├── tools/
│   └── technical_indicators.py      # MACD (12/26/9), KDJ+RSI, ZMR 시그널
├── utils/
│   ├── schemas.py                   # Pydantic 스키마 (MIResult, LLRResult, HLRResult, Decision …)
│   ├── xml_parser.py                # Claude XML 응답 파싱
│   └── metrics.py                   # Equity curve, Sharpe, MDD, 벤치마크, 성과 차트
└── main.py                          # 백테스팅 루프 (run_day / run_backtest)

web/                                 # Web UI (FastAPI + SSE)
├── app.py                           # FastAPI 앱 팩토리
├── job_store.py                     # 인메모리 Job 상태 관리
├── schemas.py                       # API 요청/응답 Pydantic 모델
├── routes/
│   ├── backtest.py                  # POST /api/backtest, GET /api/backtest/{id}/stream
│   ├── results.py                   # GET /api/backtest/{id}/result, /trades
│   └── charts.py                   # GET /charts/{job_id}/{filename}
└── static/
    ├── index.html                   # 단일 페이지 UI (폼 → 진행 → 결과)
    ├── style.css                    # 다크 테마
    └── app.js                       # SSE EventSource 클라이언트

run_web.py                           # Web UI 서버 진입점

job_data/                            # 런타임 생성 (gitignore)
└── {job_id}/
    ├── portfolio.db
    ├── memory_db/
    └── charts/

tests/
├── test_step1.py   # DataFetcher, Portfolio, TechnicalIndicators
├── test_step2.py   # MemoryStore
├── test_step3.py   # MarketIntelligenceModule + xml_parser
├── test_step4.py   # LowLevelReflectionModule
├── test_step5.py   # HighLevelReflectionModule
├── test_step6.py   # DecisionMakingModule + 파이프라인
└── test_step7.py   # metrics (equity curve, performance, plot)
```

---

## 테스트

```bash
# 단위 테스트 (API 호출 없음)
conda run -n finagent python -m pytest tests/ -m "not integration" -v

# 통합 테스트 (실제 API 호출, ANTHROPIC_API_KEY 필요)
conda run -n finagent python -m pytest tests/ -m integration -v
```

현재 단위 테스트: **112개 통과**

---

## 주요 설계 결정

### Diversified Retrieval
MarketIntelligence가 `short_term_query` / `medium_term_query` / `long_term_query` 3개를 생성하고, 각각으로 ChromaDB를 독립 검색해 최대 6개의 시간적으로 다양한 과거 기억을 수집합니다.

### Vision API 활용
- **LLR**: Kline(캔들) 차트 이미지를 base64로 인코딩해 Claude에 전달 → 캔들 패턴·거래량 기반 분석
- **HLR**: BUY▲/SELL▽ 마커가 포함된 Trading 차트 → 과거 결정의 시각적 평가

### 기술적 지표 주입 (Tool Augmentation)
논문의 expert guidance를 단순화하여 MACD·KDJ+RSI·ZMR 계산 결과를 텍스트 시그널로 변환, DecisionMaking 프롬프트에 직접 주입합니다.

### Look-ahead Bias 방지
백테스팅 루프에서 `price_df.loc[:target_date]`로 슬라이싱하여 미래 데이터가 현재 결정에 영향을 주지 않도록 합니다.

---

## 의존성

| 라이브러리 | 용도 |
|-----------|------|
| `anthropic` | Claude API (텍스트 + Vision) |
| `pykrx` | KRX 주가 OHLCV 수집 |
| `pandas_ta` | MACD, RSI, Stochastic 계산 |
| `mplfinance` | Kline / Trading 차트 생성 |
| `chromadb` | 벡터 DB (메모리 저장·검색) |
| `sentence-transformers` | 로컬 임베딩 (all-MiniLM-L6-v2) |
| `feedparser` | 네이버 뉴스 RSS 수집 |
| `pydantic` | 모듈 간 데이터 스키마 |
| `fastapi` | Web UI REST API 서버 (SSE 포함) |
| `uvicorn` | ASGI 서버 (Web UI 실행) |

---

## 참고

- 논문: [A Multimodal Foundation Agent for Financial Trading (arxiv 2402.18485)](https://arxiv.org/abs/2402.18485)
- LLM: `claude-sonnet-4-6` (Anthropic)
- 데이터: KRX (한국거래소) — `pykrx` 종목코드 사용
