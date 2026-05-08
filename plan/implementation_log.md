# FinAgent 구현 로그

논문 [arxiv 2402.18485](https://arxiv.org/abs/2402.18485) 기반 멀티모달 트레이딩 에이전트의 단계별 구현 기록.  
각 단계마다 **의도(왜 이렇게 설계했는가)** 와 **실제 구현(무엇을 어떻게 만들었는가)** 을 함께 정리한다.

---

## 환경 설정

### 의도
- 재현 가능한 환경을 만들고, 나중에도 동일하게 복원할 수 있어야 한다.
- `pandas_ta` 최신 버전이 Python 3.12+를 요구하므로 Python 3.12로 확정.

### 실제 구현
```bash
conda create -n finagent python=3.12 -y
conda activate finagent
pip install anthropic pykrx pandas_ta mplfinance chromadb \
            sentence-transformers pydantic feedparser pytest
```

생성 파일:
- `environment.yml` — conda 환경 재현용 (채널, Python 버전, pip 패키지 목록)
- `requirements.txt` — pip-only 설치용 (직접 의존성만 명시, transitive 제외)
- `pytest.ini` — `integration` 마크 등록 (단위/통합 테스트 분리 실행)

### 결정 사항
| 항목 | 선택 | 이유 |
|------|------|------|
| Python 버전 | 3.12 | pandas_ta 0.4.x 요구사항 |
| 주가 데이터 | pykrx | yfinance 대신 KRX 한국 주식 전용, 무료 |
| 뉴스 데이터 | 네이버 RSS + feedparser | API 키 불필요, 한국 종목 뉴스 품질 우수 |
| LLM | claude-sonnet-4-6 | 프로젝트 기본 모델 |

---

## Step 1 — 데이터 레이어 (LLM 비의존)

**목표**: 가격 수집, 뉴스 수집, 차트 생성, 포트폴리오 관리, 기술적 지표를 구현한다.  
LLM 없이 동작하는 기반 레이어이므로 이 단계만 완료해도 독립적으로 테스트 가능해야 한다.

### 1-1. 공통 스키마 (`finagent/utils/schemas.py`)

#### 의도
모듈 간 데이터를 안전하게 주고받으려면 명시적인 타입 계약이 필요하다.  
Pydantic을 써서 런타임 유효성 검사와 IDE 자동완성을 동시에 확보한다.

#### 구현
```python
class NewsItem(BaseModel):
    title: str; summary: str; published: datetime; url: str

class TechnicalSignals(BaseModel):
    macd_signal: str       # "BUY" | "SELL" | "HOLD"
    kdj_rsi_signal: str
    zmr_signal: str
    signal_text: str       # LLM 프롬프트 주입용 텍스트

class TradeAction(BaseModel):
    action: str; quantity: float; price: float
    date: date; reasoning: str = ""

class PortfolioState(BaseModel):
    symbol: str; position: float; cash: float; total_value: float
```

---

### 1-2. DataFetcher (`finagent/data/fetcher.py`)

#### 의도
- **주가**: pykrx로 KRX 종목 OHLCV를 수집한다. 컬럼명을 `시가→Open` 등 영문으로 rename하여 mplfinance/pandas_ta와 호환한다.
- **뉴스**: 네이버 뉴스 RSS를 feedparser로 파싱한다. 종목명(한글)을 검색어로 쓰며, `target_date ±7일` 필터를 클라이언트에서 적용한다.
- **차트 2종**: Kline(캔들)은 LLR에, Trading(라인+마커)은 HLR에 사용한다.

#### 구현
```python
class DataFetcher:
    def get_price_data(self, symbol: str, lookback_days: int = 60) -> pd.DataFrame:
        # pykrx.stock.get_market_ohlcv_by_date(fromdate, todate, symbol)
        # 컬럼 rename: 시가→Open, 고가→High, 저가→Low, 종가→Close, 거래량→Volume

    def get_news(self, symbol, stock_name, target_date, max_items=10) -> List[NewsItem]:
        # RSS: https://news.google.com/rss/search?hl=ko&gl=KR&ie=UTF-8&q={종목명+주가}
        # ±7일 필터 (클라이언트 사이드)

    def plot_kline_chart(self, df, target_date, symbol, window=30) -> str:
        # mplfinance candle chart → charts/kline_{symbol}_{date}.png

    def plot_trading_chart(self, df, actions, target_date, symbol, window=60) -> str:
        # line chart + BUY(▲ green) / SELL(▽ red) scatter markers
```

#### 설계 결정
- `get_price_data`는 `today - lookback_days` ~ `today` 구간을 수집한다.  
  백테스팅 시 main.py에서 전체 기간을 한 번에 수집 후 `df.loc[:target_date]`로 슬라이싱하여 look-ahead bias를 방지한다.
- chart 파일 경로: `charts/kline_{symbol}_{date}.png` 형식으로 자동 네이밍.

---

### 1-3. TechnicalIndicators (`finagent/tools/technical_indicators.py`)

#### 의도
논문의 expert guidance를 단순화하여, 기술적 지표 계산 결과를 LLM이 이해할 수 있는 텍스트 시그널로 변환한다.  
`pandas_ta`가 내부적으로 소문자 컬럼을 기대하므로 `df.columns = [c.lower() for c in df.columns]` 전처리가 필요하다.

#### 구현된 지표 3종

| 지표 | 파라미터 | BUY 조건 | SELL 조건 |
|------|----------|----------|-----------|
| **MACD** | fast=12, slow=26, signal=9 | Golden cross (이전봉 MACD < Signal, 현재봉 MACD ≥ Signal) | Dead cross |
| **KDJ + RSI** | K=9, D=3, smooth_k=3 / RSI(14) | K < 20 AND RSI < 30 (oversold) | K > 80 AND RSI > 70 (overbought) |
| **ZMR** | window=20 | z-score < −1.5 (MA20 대비 저평가) | z-score > 1.5 (고평가) |

```python
def get_technical_signals(df: pd.DataFrame) -> TechnicalSignals:
    # 반환: TechnicalSignals.signal_text — 3개 시그널을 \n 구분 텍스트로 합침
    # 예: "MACD: BUY signal (golden cross, MACD=0.1234)\n
    #      KDJ+RSI: HOLD (K=55.2, J=58.1, RSI=52.3)\n
    #      ZMR: SELL signal (z-score=1.72, price overvalued vs MA20)"
```

---

### 1-4. Portfolio (`finagent/portfolio/portfolio.py`)

#### 의도
거래 내역과 포지션을 영속적으로 저장해야 한다. SQLite를 선택한 이유는 파일 하나로 관리되고 외부 서버 불필요하기 때문이다.  
같은 `db_path + symbol`로 재초기화해도 기존 잔고가 유지되어야 한다 (`INSERT OR IGNORE`).

#### 구현
```
테이블:
  state  (symbol PK, position REAL, cash REAL)
  trades (id PK, date, symbol, action, quantity, price, reasoning)
```

```python
BUY_RATIO = 0.5  # 매수 시 현금의 50% 사용

class Portfolio:
    def execute(action, price, target_date, reasoning):
        # BUY  → quantity = cash * 0.5 / price, cash 차감
        # SELL → 전량 매도, position = 0
        # HOLD → 상태 변화 없이 거래 기록만 남김

    def get_all_trades() -> List[TradeAction]      # 날짜 오름차순 전체 내역
    def recent_actions(n=14) -> List[TradeAction]  # 최근 N건 (역순 조회 후 정방향 반환)
    def get_state(current_price) -> PortfolioState
    def get_returns(current_price, initial_cash) -> dict
```

#### 설계 결정
- 컨텍스트 매니저 `_conn()`이 자동 commit/rollback을 처리한다.
- `BUY_RATIO = 0.5`는 모듈 상수로 분리하여 전략 변경 시 한 곳만 수정하면 된다.

---

### Step 1 테스트 결과

```
tests/test_step1.py — 21개 테스트 (단위 18 + 통합 3)
```

| 테스트 클래스 | 케이스 수 | 주요 검증 항목 |
|--------------|-----------|---------------|
| `TestTechnicalSignals` | 6 | BUY/SELL/HOLD 시그널 정확도, ZMR 경계값 |
| `TestPortfolio` | 12 | BUY/SELL/HOLD 실행, 잔고 계산, DB 재사용 |
| `TestDataFetcherIntegration` | 3 | 실제 pykrx 수집, kline/trading 차트 생성 |

---

## Step 2 — MemoryStore (ChromaDB)

**목표**: 세 모듈(MI·LLR·HLR)이 각자의 과거 기억을 저장하고 의미 기반으로 검색할 수 있어야 한다.  
특히 Diversified Retrieval — 단기·중기·장기 3가지 관점으로 독립 검색해 시간적으로 다양한 과거 기억을 수집하는 것이 핵심이다.

### 의도
- ChromaDB를 선택한 이유: 로컬 실행, 메타데이터 필터링, Python API 단순함.
- 임베딩: `sentence-transformers all-MiniLM-L6-v2` — 무료, 로컬 실행, 한국어 포함 다국어 지원.
- 3개 컬렉션 독립: 모듈 간 메모리 오염을 방지하고 각 컬렉션의 의미 공간을 순수하게 유지한다.

### 구현 (`finagent/memory/store.py`)

```python
COLLECTIONS = ("market_intelligence", "low_level_reflection", "high_level_reflection")

class MemoryStore:
    def __init__(self, persist_dir="memory_db", embedding_model="all-MiniLM-L6-v2"):
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._ef = SentenceTransformerEmbeddingFunction(model_name=embedding_model)
        # 3개 컬렉션을 get_or_create로 초기화

    def add(collection, text, metadata):
        # upsert — (symbol, date, text_hash)로 결정적 ID 생성
        # 같은 (symbol, date) 조합 재실행 시 덮어쓰기

    def retrieve(collection, query_text, top_k=3) -> List[str]:
        # 의미 유사도 검색, 컬렉션이 비어있으면 [] 반환

    def diversified_retrieve(collection, queries: List[str], top_k_each=2) -> List[str]:
        # 여러 쿼리로 독립 검색 후 중복 제거
        # 최대 len(queries) * top_k_each 개 반환
```

#### ID 생성 전략
```python
def _make_id(metadata, text) -> str:
    text_hash = hashlib.sha1(text.encode()).hexdigest()[:8]
    return f"{symbol}_{date}_{text_hash}"
```
같은 날짜에 같은 내용이 두 번 저장되면 upsert로 1개만 유지.

### Diversified Retrieval 흐름
```
short_term_query  → retrieve(top_k=2) → [doc_A, doc_B]
medium_term_query → retrieve(top_k=2) → [doc_B, doc_C]  ← doc_B 중복
long_term_query   → retrieve(top_k=2) → [doc_D, doc_E]
                                    ↓ 중복 제거
                             [doc_A, doc_B, doc_C, doc_D, doc_E]  최대 6개
```

### Step 2 테스트 결과

```
tests/test_step2.py — 10개 테스트
```

주요 검증: add/retrieve 정확도, upsert 중복 처리, 컬렉션 독립성, 의미 유사도 순위.

---

## Step 3 — MarketIntelligenceModule

**목표**: 당일 뉴스와 가격 데이터를 Claude로 분석하고, 과거 유사 상황을 메모리에서 검색하여 종합 시장 판단을 생성한다.

### 의도
- Claude에게 분석을 맡기되, `summary`(trading용)와 3개 `query`(retrieval용)를 XML 포맷으로 분리 출력하게 강제한다.
- 이 분리가 Diversified Retrieval의 핵심 — 같은 분석에서 단/중/장기 관점의 쿼리를 동시에 얻는다.

### XML 파서 (`finagent/utils/xml_parser.py`)

#### 의도
Claude의 응답이 항상 완벽한 XML이 아닐 수 있다. 두 가지 포맷을 모두 지원하고, 실패 시 빈 문자열을 반환해 상위 코드에서 fallback을 처리한다.

```python
def parse_field(xml_text, tag) -> str:
    # 1순위: <tag>value</tag>
    # 2순위: <string name="tag">value</string>
    # 미발견 시: ""

def parse_output(xml_text, *tags) -> dict[str, str]:
    # 여러 태그 한 번에 추출
```

### MarketIntelligenceModule (`finagent/modules/market_intelligence.py`)

#### 구현 흐름

```
run(symbol, target_date, price_df, news_list)
  │
  ├─ 1. _analyze_latest()
  │     Claude API 호출 (텍스트)
  │     입력: 최근 10거래일 가격표 + 뉴스 목록
  │     출력: summary, short/medium/long_term_query
  │
  ├─ 2. diversified_retrieve("market_intelligence", [short_q, medium_q, long_q], top_k_each=2)
  │     → 과거 MI 최대 6개
  │
  ├─ 3. _format_past_docs(past_docs)
  │     → "[과거 Market Intelligence 요약]\n1. ...\n2. ..."
  │
  └─ 4. memory.add("market_intelligence", summary, {symbol, date, queries})
        return MIResult(latest_summary, past_summary, short/medium/long_term_query)
```

#### 프롬프트 설계
```
[종목코드] / [분석 기준일] / [최근 뉴스] / [최근 가격 데이터]

→ XML 출력:
<output>
  <summary>트레이딩용 종합 분석 (3-5문장)</summary>
  <short_term_query>단기(1-5일) 검색 쿼리</short_term_query>
  <medium_term_query>중기(1-4주) 검색 쿼리</medium_term_query>
  <long_term_query>장기(1-3개월) 검색 쿼리</long_term_query>
</output>
```

#### 결과 스키마
```python
class MIResult(BaseModel):
    latest_summary: str      # 최신 분석 요약 (trading용)
    past_summary: str        # 과거 MI 포맷팅 결과
    short_term_query: str    # 이후 LLR/HLR 검색에 재사용
    medium_term_query: str
    long_term_query: str
```

### Step 3 테스트 결과

```
tests/test_step3.py — 21개 테스트 (단위 20 + 통합 1)
```

Claude API는 `unittest.mock`으로 대체. 사전 정의된 XML 응답을 주입해 파싱·저장·조회 로직만 검증.

---

## Step 4 — LowLevelReflection (Vision)

**목표**: Kline 차트 이미지를 Claude Vision으로 분석하여 단/중/장기 가격 변동의 원인을 추론한다.

### 의도
논문의 LLR은 "가격 변동과 Market Intelligence 간의 연결 관계"를 분석한다.  
캔들 패턴, 거래량, 지지/저항선은 텍스트보다 이미지로 전달했을 때 모델이 더 잘 분석할 수 있다고 판단하여 Vision API를 사용한다.

### 구현 (`finagent/modules/low_level_reflection.py`)

#### 가격 변동률 계산
```python
def _calc_price_changes(df, target_date) -> dict:
    # target_date 기준 1d / 5d / 10d / 20d 변동률(%)
    # 데이터 부족 시 None 반환 (lookback 초반 안전 처리)
```

#### Claude Vision 호출
```python
messages=[{
    "role": "user",
    "content": [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(open(kline_path, "rb").read()).decode(),
            },
        },
        {"type": "text", "text": prompt},
    ],
}]
```

#### 구현 흐름
```
run(symbol, target_date, price_df, kline_image_path, mi_result)
  │
  ├─ 1. _calc_price_changes(price_df, target_date)  → 1d/5d/10d/20d 변동률
  ├─ 2. memory.retrieve("low_level_reflection", mi_result.short_term_query, top_k=3)
  ├─ 3. Claude Vision 호출 (Kline 이미지 + 가격변동 + MI 요약 + 과거 LLR)
  │     XML 출력: short/medium/long_term_reasoning, query
  └─ 4. memory.add("low_level_reflection",
                   "단기: ...\n중기: ...\n장기: ...",  ← 3개 reasoning 합산
                   {symbol, date})
        return LLRResult
```

#### 메모리 저장 전략
세 reasoning을 하나의 문서로 합쳐 저장한다.  
이유: HLR이 검색할 때 세 관점이 함께 있어야 맥락을 파악할 수 있기 때문.

#### 결과 스키마
```python
class LLRResult(BaseModel):
    short_term_reasoning: str   # 단기 원인 분석
    medium_term_reasoning: str  # 중기 원인 분석
    long_term_reasoning: str    # 장기 원인 분석
    query: str                  # HLR retrieval용 쿼리
```

### Step 4 테스트 결과

```
tests/test_step4.py — 16개 테스트 (단위 15 + 통합 1)
```

단위 테스트에서는 1×1 white PNG를 임시 파일로 생성하여 Vision 코드 경로를 실제로 통과시킨다.  
Claude 응답은 mock으로 대체.

---

## Step 5 — HighLevelReflection (Vision)

**목표**: 과거 거래 결정들을 Trading 차트와 함께 재평가하여 개선 방안을 도출한다.

### 의도
LLR이 "왜 가격이 이렇게 움직였나"를 분석한다면,  
HLR은 "그 상황에서 내 결정이 옳았나"를 평가한다.  
BUY▲/SELL▽ 마커가 포함된 Trading 차트를 보면 결정 시점과 이후 가격 흐름을 시각적으로 비교할 수 있다.

### LLR과의 차이점

| 항목 | LLR | HLR |
|------|-----|-----|
| 차트 타입 | Kline (캔들) | Trading (라인 + 매매 마커) |
| 분석 대상 | 가격 패턴 | 과거 거래 결정의 적절성 |
| 추가 입력 | 가격변동률 | past_actions (최근 14건) |
| 메모리 저장 내용 | 3개 reasoning 합산 | summary (1-2문장 핵심 요약) |

### 구현 (`finagent/modules/high_level_reflection.py`)

#### 구현 흐름
```
run(symbol, target_date, trading_chart_path, past_actions, mi_result, llr_result)
  │
  ├─ 1. memory.retrieve("high_level_reflection", llr_result.query, top_k=3)
  ├─ 2. _format_actions(past_actions)  → 날짜/액션/가격/수량/판단 근거 표
  ├─ 3. Claude Vision 호출
  │     입력: Trading 차트 + 거래내역 + MI 요약 + LLR 단/중/장기 + 과거 HLR
  │     XML 출력: reasoning, improvement, summary, query
  └─ 4. memory.add("high_level_reflection", result.summary, {symbol, date})
        return HLRResult
```

#### 결과 스키마
```python
class HLRResult(BaseModel):
    reasoning: str    # 과거 결정들의 종합 평가
    improvement: str  # 구체적 개선 방안
    summary: str      # 메모리 저장 핵심 요약 (1-2문장)
    query: str        # DecisionMaking retrieval용 쿼리
```

### Step 5 테스트 결과

```
tests/test_step5.py — 15개 테스트 (단위 14 + 통합 1)
```

---

## Step 6 — DecisionMaking + 전체 파이프라인

**목표**: MI·LLR·HLR 결과와 기술적 지표를 종합하여 최종 BUY/SELL/HOLD를 결정하고, 하루치 파이프라인을 `run_day()`로 통합한다.

### DecisionMakingModule (`finagent/modules/decision_making.py`)

#### 의도
모든 분석을 하나의 프롬프트에 담아 Claude에게 최종 판단을 맡긴다.  
기술적 지표는 모듈 내부에서 `get_technical_signals(price_df)`로 계산하므로 호출부에서 따로 계산할 필요가 없다.

#### 프롬프트 구성 요소
```
[트레이더 성향] aggressive / moderate / conservative
[포트폴리오 상태] 현금, 보유 수량, 총 자산
[기술적 지표] MACD, KDJ+RSI, ZMR 시그널 텍스트
[Market Intelligence] 최신 분석 + 과거 패턴
[LLR] 단기/중기/장기 가격 변동 분석
[HLR] 과거 결정 평가 + 개선점
```

#### 안전장치
```python
action = fields["action"].strip().upper()
if action not in ("BUY", "SELL", "HOLD"):
    action = "HOLD"  # 예상 외 응답 → 기본값 HOLD
```

#### 결과 스키마
```python
class Decision(BaseModel):
    action: str      # "BUY" | "SELL" | "HOLD"
    reasoning: str   # 결정 근거
```

---

### 전체 파이프라인 (`finagent/main.py`)

#### `run_day()` — 하루치 실행
```python
def run_day(symbol, stock_name, target_date, price_df, fetcher,
            portfolio, mi_module, llr_module, hlr_module, dm_module,
            trader_preference="moderate") -> Decision:

    # Look-ahead bias 방지
    df = price_df.loc[:pd.Timestamp(target_date)]

    # 1. 데이터 수집
    news        = fetcher.get_news(symbol, stock_name, target_date)
    kline_path  = fetcher.plot_kline_chart(df, target_date, symbol)
    trading_path = fetcher.plot_trading_chart(df, portfolio.recent_actions(14), ...)

    # 2. MI → 3. LLR → 4. HLR → 5. DM
    mi_result  = mi_module.run(symbol, target_date, df, news)
    llr_result = llr_module.run(symbol, target_date, df, kline_path, mi_result)
    hlr_result = hlr_module.run(symbol, target_date, trading_path,
                                portfolio.recent_actions(14), mi_result, llr_result)
    decision   = dm_module.run(symbol, target_date, df,
                               mi_result, llr_result, hlr_result,
                               portfolio.get_state(current_price), trader_preference)

    # 6. 거래 실행
    portfolio.execute(decision.action, current_price, target_date, decision.reasoning)
    return decision
```

#### `run_backtest()` — 날짜 범위 루프
```python
def run_backtest(symbol, stock_name, start, end, initial_cash=10_000_000, ...):
    # 전체 기간 데이터를 한 번에 수집 (lookback_days = (end-start).days + 90)
    # 백테스팅 대상 거래일 필터링
    # 각 거래일에 run_day() 호출, 예외 발생 시 해당 일 skip하고 계속 진행
    # 종료 후 성과 분석 + 차트 생성
```

### Step 6 테스트 결과

```
tests/test_step6.py — 14개 테스트 (단위 13 + 통합 1)
```

`TestPipelineIntegration`: 모든 Claude 모듈을 mock으로 교체하고 `run_day()`의 오케스트레이션 로직만 검증.

---

## Step 7 — 성과 측정

**목표**: 백테스팅 결과를 수치 지표와 시각 차트로 정량화한다.

### 의도
단순 수익률만으로는 전략의 품질을 판단하기 어렵다. Sharpe ratio(리스크 대비 수익)와 MDD(최악의 낙폭)를 함께 봐야 전략의 견고성을 알 수 있다. Buy&Hold 벤치마크와 비교하는 것이 핵심.

### 구현 (`finagent/utils/metrics.py`)

#### Equity Curve 계산
```python
def compute_equity_curve(trades, price_df, initial_cash, buy_ratio=0.5) -> pd.Series:
    # 거래 내역을 시간순으로 재현
    # 각 영업일마다 cash + position * close_price 계산
    # index: date, values: 총 포트폴리오 가치
```

#### 성과 지표
```python
def compute_performance(equity_curve, initial_cash, risk_free_rate=0.03) -> dict:
    # total_return_pct       = (final - initial) / initial * 100
    # annualized_return_pct  = ((final/initial)^(1/years) - 1) * 100
    # sharpe_ratio           = mean(excess_daily) / std(excess_daily) * sqrt(252)
    # max_drawdown_pct       = min((curve - rolling_max) / rolling_max) * 100
    # volatility_annual_pct  = std(daily_returns) * sqrt(252) * 100
```

#### 시각화
```
상단 패널: FinAgent 자산곡선 vs Buy&Hold (파란선 vs 주황점선)
          BUY: 초록 수직선 / SELL: 빨간 수직선
하단 패널: Drawdown (빨간 fill)
저장: charts/performance_{symbol}_{start}_{end}.png
```

#### main.py 통합 — 백테스팅 종료 시 자동 출력
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

### Step 7 테스트 결과

```
tests/test_step7.py — 22개 테스트
```

| 테스트 클래스 | 케이스 수 | 주요 검증 |
|-------------|-----------|-----------|
| `TestComputeEquityCurve` | 6 | 무거래 고정가치, BUY 후 상승, SELL 후 고정, HOLD 무영향 |
| `TestComputeBenchmark` | 4 | 첫날 = initial_cash, 상승장 양수 수익 |
| `TestComputePerformance` | 8 | 총수익률 계산, MDD 정확도, Sharpe 부호, 전체 키 존재 |
| `TestPlotPerformance` | 2 | PNG 파일 생성 및 크기 > 0 |
| `TestPortfolioGetAllTrades` | 2 | 순서 보장, 빈 포트폴리오 처리 |

---

## 최종 현황

### 파일 구조
```
finagent/
├── data/fetcher.py               201줄
├── memory/store.py               135줄
├── modules/
│   ├── market_intelligence.py    180줄
│   ├── low_level_reflection.py   211줄
│   ├── high_level_reflection.py  194줄
│   └── decision_making.py        167줄
├── portfolio/portfolio.py        229줄
├── tools/technical_indicators.py 144줄
├── utils/
│   ├── schemas.py                 62줄
│   ├── xml_parser.py              31줄
│   └── metrics.py                191줄
└── main.py                       255줄
                          총 1,907줄
```

### 테스트 현황
```
Step 1:  21 tests  (18 unit + 3 integration)
Step 2:  10 tests
Step 3:  21 tests  (20 unit + 1 integration)
Step 4:  16 tests  (15 unit + 1 integration)
Step 5:  15 tests  (14 unit + 1 integration)
Step 6:  14 tests  (13 unit + 1 integration)
Step 7:  22 tests
─────────────────────────────────────────
합계:   119 tests  (112 unit + 7 integration)
단위 테스트: 112/112 통과
```

### 실행 방법
```bash
conda activate finagent
export ANTHROPIC_API_KEY="sk-ant-..."

python finagent/main.py \
  --symbol 005930 \
  --stock-name 삼성전자 \
  --start 2024-01-02 \
  --end 2024-03-29 \
  --initial-cash 10000000 \
  --preference moderate
```

---

## 주요 설계 결정 요약

| 결정 | 선택 | 대안 | 이유 |
|------|------|------|------|
| 주가 데이터 | pykrx | yfinance | KRX 한국 주식 전용, 무료 |
| 뉴스 소스 | 네이버 RSS | Finnhub, NewsAPI | API 키 불필요, 한국 주식 뉴스 품질 |
| 벡터 DB | ChromaDB | FAISS | 로컬 실행, 메타데이터 필터, 단순한 API |
| 임베딩 | all-MiniLM-L6-v2 | OpenAI ada | 무료, 로컬, 다국어 지원 |
| LLM | claude-sonnet-4-6 | claude-opus | 비용/성능 균형 |
| 포트폴리오 저장 | SQLite | PostgreSQL, Redis | 파일 하나, 외부 서버 불필요 |
| XML 파싱 | 직접 구현 (regex) | lxml, BeautifulSoup | 경량, Claude 응답 특성에 최적화 |
| 기술적 지표 주입 | 텍스트 시그널로 변환 | 수치 직접 전달 | LLM이 의미를 바로 이해할 수 있는 형태 |
| Look-ahead bias | `df.loc[:target_date]` 슬라이싱 | 별도 API 호출 | 성능 오버헤드 없이 미래 데이터 차단 |
