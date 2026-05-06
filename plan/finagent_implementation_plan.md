# FinAgent 구현 계획

논문의 핵심 구조를 단순화해서 실제로 돌아가는 시스템을 만드는 방향으로 계획을 세웁니다.

---

## 전체 아키텍처 요약

```
[데이터 수집] → [Market Intelligence] → [Reflection] → [Decision]
                        ↕                     ↕
                    [Memory (Vector DB)]
```

---

## 기술 스택 선택

### 언어 & 런타임
- Python 3.11+

### LLM
- `claude-sonnet-4-20250514` — 텍스트 분석, 반성 모듈, 의사결정
- `claude-opus-4-20250514` — 선택적으로 고난이도 reasoning에 사용 가능

### Vector DB (Memory)
- `ChromaDB` — 로컬에서 바로 쓸 수 있고, 설치가 단순함. FAISS도 가능하지만 ChromaDB가 메타데이터 필터링이 편함

### 임베딩
- `sentence-transformers` (all-MiniLM-L6-v2) — 무료, 로컬 실행

### 데이터 수집
- `krx_price` — pykrx로 OHLCV + 등락률 반환
- `naver_news` — 종목명 기반 네이버 뉴스 RSS 
- `pandas_ta` — 기술적 지표 (MACD, KDJ, RSI, Bollinger Bands)

### 시각화 (Kline/Trading Chart)
- `mplfinance` — Kline 차트 생성 → 이미지로 저장 후 Claude Vision에 전달

### 기타
- `pydantic` — 모듈 간 데이터 스키마 정의
- `sqlite3` — 거래 히스토리 저장 (가볍게)

---

## 모듈별 구현 계획

### 1. 데이터 레이어

```python
# data_layer.py
class DataFetcher:
    def get_price_data(symbol, lookback_days)  # krx_price
    def get_news(symbol, date)                 # naver_news
    def get_technical_indicators(df)           # pandas_ta로 MACD, RSI, KDJ, BB 계산
    def plot_kline_chart(df) -> image_path     # mplfinance → PNG 저장
    def plot_trading_chart(df, actions) -> image_path
```

---

### 2. Memory 모듈

ChromaDB에 3개의 컬렉션을 만들어서 각 모듈이 독립적으로 저장/조회하게 구성합니다.

```python
# memory.py
class MemoryStore:
    collections:
        - "market_intelligence"   # MI 요약 + query 텍스트
        - "low_level_reflection"  # LLR 결과
        - "high_level_reflection" # HLR 결과

    def add(collection, text, metadata)
    def retrieve(collection, query_text, top_k=3) -> List[str]
```

**Diversified Retrieval 구현 방식:**

논문의 핵심 아이디어는 "trading용 요약"과 "retrieval용 query"를 분리하는 것입니다. Claude에게 MI를 분석할 때 `short_term_query`, `medium_term_query`, `long_term_query`를 별도 필드로 출력하게 프롬프트를 설계하고, 각 query로 ChromaDB를 따로 검색해서 결과를 합칩니다.

```python
# retrieval 방식
results = []
for query in [short_term_q, medium_term_q, long_term_q]:
    results += memory.retrieve("market_intelligence", query, top_k=2)
# → 최대 6개의 과거 MI를 다양한 관점으로 수집
```

---

### 3. Market Intelligence 모듈

```python
# market_intelligence.py
class MarketIntelligenceModule:

    def run(symbol, date, price_df, news_list) -> MIResult:
        # Step 1: Claude에게 최신 뉴스+가격 분석 요청
        latest_mi = self._analyze_latest(news_list, price_df)
        # → summary (trading용) + query_texts (retrieval용) 반환

        # Step 2: Diversified Retrieval로 과거 MI 검색
        past_mi_docs = self._diversified_retrieve(latest_mi.queries)

        # Step 3: 과거 MI 요약 생성
        past_mi_summary = self._summarize_past(past_mi_docs)

        # Step 4: 최신 MI를 memory에 저장
        memory.add("market_intelligence", latest_mi.summary,
                   metadata={"date": date, "queries": latest_mi.queries})

        return MIResult(latest_summary, past_summary)
```

**Claude 호출 예시 (XML 출력 강제):**

```python
prompt = f"""
뉴스: {news_text}
가격: {price_data}

다음 XML 형식으로만 응답하세요:
<output>
  <string name="summary">trading용 요약</string>
  <map name="query">
    <string name="short_term_query">단기 검색어</string>
    <string name="medium_term_query">중기 검색어</string>
    <string name="long_term_query">장기 검색어</string>
  </map>
</output>
"""
```

---

### 4. Low-Level Reflection (LLR)

가격 변동과 Market Intelligence 간의 연결 관계를 분석합니다.

| 항목 | 내용 |
|------|------|
| Target | Price Movements |
| Visual Data | Kline Chart |
| Market Understanding | Micro |
| Function | Adaptability |

```python
# reflection.py
class LowLevelReflection:
    def run(mi_result, kline_image_path, price_changes) -> LLRResult:
        # Claude Vision으로 Kline 차트 이미지 + 가격변동 데이터 + MI 요약 전달
        # 단기/중기/장기 가격 변동 reasoning 생성
        # query 필드도 함께 생성 → memory 저장

        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", ...}},  # kline 차트
                    {"type": "text", "text": prompt}
                ]
            }]
        )
```

**입력:** MI 요약 + Kline 차트 이미지 + 과거 N일 / 미래 N일 가격 변동률

**출력:** `short_term_reasoning`, `medium_term_reasoning`, `long_term_reasoning`, `query`

---

### 5. High-Level Reflection (HLR)

과거 거래 결정의 잘잘못을 반성합니다.

| 항목 | 내용 |
|------|------|
| Target | Trading Decisions |
| Visual Data | Trading Chart |
| Market Understanding | Macro |
| Function | Amendability |

```python
class HighLevelReflection:
    def run(mi_result, llr_result, trading_chart_path,
            past_actions_and_reasoning) -> HLRResult:
        # trading 차트 이미지 + 과거 14일 액션 + reasoning 전달
        # 각 결정이 옳았는지 평가
        # 개선 방안 제시
        # summary + query 생성 → memory 저장
```

**입력:** MI 요약 + LLR 결과 + Trading 차트 이미지 + 과거 액션 로그

**출력:** `reasoning`, `improvement`, `summary`, `query`

---

### 6. Tool-Augmented Decision Making

```python
class DecisionMakingModule:
    def run(mi_result, llr_result, hlr_result,
            technical_signals, trader_preference) -> Decision:

        # technical_signals: MACD/KDJ&RSI/ZMR 계산 결과를 텍스트로 변환
        # trader_preference: "aggressive" or "conservative"
        # 현재 포지션, 현금 잔고도 함께 전달

        # Claude가 BUY/HOLD/SELL + reasoning 반환
```

**Augmented Tools (단순화):**

논문의 expert guidance는 구현이 복잡하므로, 기술적 지표(MACD, KDJ+RSI, ZMR)의 시그널만 텍스트로 계산해서 LLM 프롬프트에 주입합니다.

```python
def get_technical_signals(df) -> str:
    # MACD crossover → "BUY signal (MACD crossed above signal line)"
    # KDJ+RSI → "SELL signal (KDJ overbought, RSI > 70)"
    # ZMR → "HOLD (z-score: 0.3, within normal range)"
    return signal_text
```

---

## 전체 실행 흐름

```python
# main.py - 하루치 실행
def run_step(symbol, date, portfolio):

    # 1. 데이터 수집
    price_df = fetcher.get_price_data(symbol)
    news = fetcher.get_news(symbol, date)
    kline_path = fetcher.plot_kline_chart(price_df)
    trading_path = fetcher.plot_trading_chart(price_df, portfolio.history)

    # 2. Market Intelligence
    mi_result = mi_module.run(symbol, date, price_df, news)

    # 3. Low-Level Reflection
    price_changes = calculate_price_changes(price_df, date)
    past_llr = memory.retrieve("low_level_reflection", mi_result.query)
    llr_result = llr_module.run(mi_result, kline_path, price_changes, past_llr)

    # 4. High-Level Reflection
    past_hlr = memory.retrieve("high_level_reflection", llr_result.query)
    hlr_result = hlr_module.run(mi_result, llr_result, trading_path,
                                portfolio.recent_actions(14), past_hlr)

    # 5. Decision Making
    tech_signals = fetcher.get_technical_indicators(price_df)
    decision = dm_module.run(mi_result, llr_result, hlr_result,
                             tech_signals, portfolio)

    # 6. 거래 실행 & 결과 저장
    portfolio.execute(decision.action, price_df.iloc[-1]['close'])
    return decision
```

---

## 프로젝트 구조

```
finagent/
├── data/
│   └── fetcher.py                   # pykrx + 네이버뉴스 + 차트 생성
├── memory/
│   └── store.py                     # ChromaDB 래퍼
├── modules/
│   ├── market_intelligence.py
│   ├── low_level_reflection.py
│   ├── high_level_reflection.py
│   └── decision_making.py
├── tools/
│   └── technical_indicators.py      # MACD, RSI, KDJ, ZMR 시그널
├── portfolio/
│   └── portfolio.py                 # 포지션/현금/거래내역 관리
├── utils/
│   └── xml_parser.py                # Claude 응답 XML 파싱
└── main.py                          # 백테스팅 루프
```

---

## 단계별 구현 순서

| 단계 | 내용 | 핵심 도구 |
|------|------|-----------|
| 1단계 | `DataFetcher` + `Portfolio` + `TechnicalIndicators` 구현 및 테스트 | pykrx, pandas_ta, mplfinance |
| 2단계 | `MemoryStore` (ChromaDB) 구현, 저장/조회 테스트 | ChromaDB, sentence-transformers |
| 3단계 | `MarketIntelligenceModule` 구현 (Diversified Retrieval 포함) | Anthropic API, ChromaDB |
| 4단계 | `LowLevelReflection` 구현 (Vision API 활용) | Anthropic API (Vision) |
| 5단계 | `HighLevelReflection` 구현 | Anthropic API (Vision) |
| 6단계 | `DecisionMakingModule` 구현 + 전체 파이프라인 연결 | Anthropic API |
| 7단계 | 단일 종목 백테스팅 실행 및 성능 측정 | 전체 시스템 |

---

## 참고 API 및 라이브러리

| 용도 | 라이브러리 / API | 비용 |
|------|-----------------|------|
| 주가 데이터 | `pykrx` | 무료 |
| 뉴스 데이터 | `naver_news` | 무료 |
| 기술적 지표 | `pandas_ta` | 무료 |
| Kline 차트 | `mplfinance` | 무료 |
| LLM (텍스트+비전) | Anthropic Claude API | 유료 |
| Vector DB | `ChromaDB` | 무료 (로컬) |
| 임베딩 | `sentence-transformers` | 무료 (로컬) |
| 데이터 스키마 | `pydantic` | 무료 |
| 거래 히스토리 | `sqlite3` | 무료 (내장) |
