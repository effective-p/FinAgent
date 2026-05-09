# FinAgent Implementation Log

Step-by-step implementation record of a multimodal trading agent based on the paper [arxiv 2402.18485](https://arxiv.org/abs/2402.18485).  
For each step, both **intent (why this design)** and **actual implementation (what was built and how)** are documented together.

---

## Environment Setup

### Intent
- Create a reproducible environment that can be restored identically later.
- Python 3.12 was chosen because the latest `pandas_ta` version requires Python 3.12+.

### Actual Implementation
```bash
conda create -n finagent python=3.12 -y
conda activate finagent
pip install anthropic pykrx pandas_ta mplfinance chromadb \
            sentence-transformers pydantic feedparser pytest
```

Files created:
- `environment.yml` — For conda environment reproduction (channels, Python version, pip package list)
- `requirements.txt` — For pip-only installation (direct dependencies only, no transitive)
- `pytest.ini` — Register `integration` mark (separate unit/integration test execution)

### Decisions
| Item | Choice | Reason |
|------|------|------|
| Python version | 3.12 | pandas_ta 0.4.x requirement |
| Stock price data | pykrx | Dedicated to KRX Korean stocks instead of yfinance, free |
| News data | Google RSS + feedparser | No API key required, good quality Korean stock news |
| LLM | claude-sonnet-4-6 | Default model for the project |

---

## Step 1 — Data Layer (LLM-independent)

**Goal**: Implement price collection, news collection, chart generation, portfolio management, and technical indicators.  
Since this is a foundation layer that works without LLM, it should be independently testable after this step alone.

### 1-1. Common Schemas (`finagent/utils/schemas.py`)

#### Intent
Explicit type contracts are needed to safely pass data between modules.  
Using Pydantic to achieve both runtime validation and IDE autocomplete simultaneously.

#### Implementation
```python
class NewsItem(BaseModel):
    title: str; summary: str; published: datetime; url: str

class TechnicalSignals(BaseModel):
    macd_signal: str       # "BUY" | "SELL" | "HOLD"
    kdj_rsi_signal: str
    zmr_signal: str
    signal_text: str       # Text for LLM prompt injection

class TradeAction(BaseModel):
    action: str; quantity: float; price: float
    date: date; reasoning: str = ""

class PortfolioState(BaseModel):
    symbol: str; position: float; cash: float; total_value: float
```

---

### 1-2. DataFetcher (`finagent/data/fetcher.py`)

#### Intent
- **Stock prices**: Collect KRX stock OHLCV via pykrx. Rename columns to English (`시가→Open`, etc.) for compatibility with mplfinance/pandas_ta.
- **News**: Parse Google News RSS via feedparser. Uses stock name (Korean) as search term, applies `target_date ±7 day` filter client-side.
- **2 chart types**: Kline (candlestick) for LLR, Trading (line+markers) for HLR.

#### Implementation
```python
class DataFetcher:
    def get_price_data(self, symbol: str, lookback_days: int = 60) -> pd.DataFrame:
        # pykrx.stock.get_market_ohlcv_by_date(fromdate, todate, symbol)
        # Column rename: 시가→Open, 고가→High, 저가→Low, 종가→Close, 거래량→Volume

    def get_news(self, symbol, stock_name, target_date, max_items=10) -> List[NewsItem]:
        # RSS: https://news.google.com/rss/search?hl=ko&gl=KR&ie=UTF-8&q={stock_name+stock_price}
        # ±7 day filter (client-side)

    def plot_kline_chart(self, df, target_date, symbol, window=30) -> str:
        # mplfinance candle chart → charts/kline_{symbol}_{date}.png

    def plot_trading_chart(self, df, actions, target_date, symbol, window=60) -> str:
        # line chart + BUY(▲ green) / SELL(▽ red) scatter markers
```

#### Design Decisions
- `get_price_data` collects the range from `today - lookback_days` to `today`.  
  In backtesting, main.py collects the full period at once and slices with `df.loc[:target_date]` to prevent look-ahead bias.
- Chart file path: Auto-named in the format `charts/kline_{symbol}_{date}.png`.

---

### 1-3. TechnicalIndicators (`finagent/tools/technical_indicators.py`)

#### Intent
Simplifying the paper's expert guidance, converts technical indicator calculation results into text signals that the LLM can understand.  
`pandas_ta` internally expects lowercase columns, so `df.columns = [c.lower() for c in df.columns]` preprocessing is required.

#### 3 Implemented Indicators

| Indicator | Parameters | BUY Condition | SELL Condition |
|------|----------|----------|-----------|
| **MACD** | fast=12, slow=26, signal=9 | Golden cross (previous bar MACD < Signal, current bar MACD ≥ Signal) | Dead cross |
| **KDJ + RSI** | K=9, D=3, smooth_k=3 / RSI(14) | K < 20 AND RSI < 30 (oversold) | K > 80 AND RSI > 70 (overbought) |
| **ZMR** | window=20 | z-score < −1.5 (undervalued vs MA20) | z-score > 1.5 (overvalued) |

```python
def get_technical_signals(df: pd.DataFrame) -> TechnicalSignals:
    # Returns: TechnicalSignals.signal_text — 3 signals combined with \n separator
    # Example: "MACD: BUY signal (golden cross, MACD=0.1234)\n
    #           KDJ+RSI: HOLD (K=55.2, J=58.1, RSI=52.3)\n
    #           ZMR: SELL signal (z-score=1.72, price overvalued vs MA20)"
```

---

### 1-4. Portfolio (`finagent/portfolio/portfolio.py`)

#### Intent
Trade history and positions must be stored persistently. SQLite was chosen because it is managed as a single file and requires no external server.  
Reinitializing with the same `db_path + symbol` should retain existing balances (`INSERT OR IGNORE`).

#### Implementation
```
Tables:
  state  (symbol PK, position REAL, cash REAL)
  trades (id PK, date, symbol, action, quantity, price, reasoning)
```

```python
BUY_RATIO = 0.5  # Use 50% of cash for each buy

class Portfolio:
    def execute(action, price, target_date, reasoning):
        # BUY  → quantity = cash * 0.5 / price, deduct from cash
        # SELL → sell entire position, position = 0
        # HOLD → no state change, only record trade

    def get_all_trades() -> List[TradeAction]      # Full history in ascending date order
    def recent_actions(n=14) -> List[TradeAction]  # Recent N records (query in reverse, return forward)
    def get_state(current_price) -> PortfolioState
    def get_returns(current_price, initial_cash) -> dict
```

#### Design Decisions
- The context manager `_conn()` handles automatic commit/rollback.
- `BUY_RATIO = 0.5` is separated as a module constant so only one place needs to change when modifying strategy.

---

### Step 1 Test Results

```
tests/test_step1.py — 21 tests (18 unit + 3 integration)
```

| Test Class | Case Count | Key Validations |
|--------------|-----------|---------------|
| `TestTechnicalSignals` | 6 | BUY/SELL/HOLD signal accuracy, ZMR boundary values |
| `TestPortfolio` | 12 | BUY/SELL/HOLD execution, balance calculation, DB reuse |
| `TestDataFetcherIntegration` | 3 | Actual pykrx fetch, kline/trading chart generation |

---

## Step 2 — MemoryStore (ChromaDB)

**Goal**: The three modules (MI·LLR·HLR) must be able to store their own past memories and search them semantically.  
In particular, Diversified Retrieval — collecting temporally diverse past memories through independent searches from short/medium/long-term perspectives — is the core.

### Intent
- Why ChromaDB: local execution, metadata filtering, simple Python API.
- Embeddings: `sentence-transformers all-MiniLM-L6-v2` — free, local execution, multilingual support including Korean.
- 3 independent collections: Prevents memory contamination between modules and keeps the semantic space of each collection pure.

### Implementation (`finagent/memory/store.py`)

```python
COLLECTIONS = ("market_intelligence", "low_level_reflection", "high_level_reflection")

class MemoryStore:
    def __init__(self, persist_dir="memory_db", embedding_model="all-MiniLM-L6-v2"):
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._ef = SentenceTransformerEmbeddingFunction(model_name=embedding_model)
        # Initialize 3 collections with get_or_create

    def add(collection, text, metadata):
        # upsert — generate deterministic ID with (symbol, date, text_hash)
        # Overwrite on re-run with same (symbol, date) combination

    def retrieve(collection, query_text, top_k=3) -> List[str]:
        # Semantic similarity search, returns [] if collection is empty

    def diversified_retrieve(collection, queries: List[str], top_k_each=2) -> List[str]:
        # Independent search with multiple queries, then deduplicate
        # Returns up to len(queries) * top_k_each results
```

#### ID Generation Strategy
```python
def _make_id(metadata, text) -> str:
    text_hash = hashlib.sha1(text.encode()).hexdigest()[:8]
    return f"{symbol}_{date}_{text_hash}"
```
If the same content is stored twice on the same date, upsert keeps only 1.

### Diversified Retrieval Flow
```
short_term_query  → retrieve(top_k=2) → [doc_A, doc_B]
medium_term_query → retrieve(top_k=2) → [doc_B, doc_C]  ← doc_B duplicate
long_term_query   → retrieve(top_k=2) → [doc_D, doc_E]
                                    ↓ deduplicate
                             [doc_A, doc_B, doc_C, doc_D, doc_E]  up to 6
```

### Step 2 Test Results

```
tests/test_step2.py — 10 tests
```

Key validations: add/retrieve accuracy, upsert duplicate handling, collection independence, semantic similarity ranking.

---

## Step 3 — MarketIntelligenceModule

**Goal**: Analyze the day's news and price data with Claude, search past similar situations from memory, and generate a comprehensive market judgment.

### Intent
- Delegate analysis to Claude, but enforce separate XML format output for `summary` (for trading) and 3 `queries` (for retrieval).
- This separation is key to Diversified Retrieval — simultaneously obtaining short/medium/long-term perspective queries from the same analysis.

### XML Parser (`finagent/utils/xml_parser.py`)

#### Intent
Claude's responses may not always be perfect XML. Support two formats and return empty string on failure, letting higher-level code handle fallback.

```python
def parse_field(xml_text, tag) -> str:
    # Priority 1: <tag>value</tag>
    # Priority 2: <string name="tag">value</string>
    # Not found: ""

def parse_output(xml_text, *tags) -> dict[str, str]:
    # Extract multiple tags at once
```

### MarketIntelligenceModule (`finagent/modules/market_intelligence.py`)

#### Implementation Flow

```
run(symbol, target_date, price_df, news_list)
  │
  ├─ 1. _analyze_latest()
  │     Claude API call (text)
  │     Input: recent 10 trading day price table + news list
  │     Output: summary, short/medium/long_term_query
  │
  ├─ 2. diversified_retrieve("market_intelligence", [short_q, medium_q, long_q], top_k_each=2)
  │     → up to 6 past MI records
  │
  ├─ 3. _format_past_docs(past_docs)
  │     → "[Past Market Intelligence Summary]\n1. ...\n2. ..."
  │
  └─ 4. memory.add("market_intelligence", summary, {symbol, date, queries})
        return MIResult(latest_summary, past_summary, short/medium/long_term_query)
```

#### Prompt Design
```
[Stock code] / [Analysis reference date] / [Recent news] / [Recent price data]

→ XML output:
<output>
  <summary>Comprehensive analysis for trading (3-5 sentences)</summary>
  <short_term_query>Short-term (1-5 days) search query</short_term_query>
  <medium_term_query>Medium-term (1-4 weeks) search query</medium_term_query>
  <long_term_query>Long-term (1-3 months) search query</long_term_query>
</output>
```

#### Result Schema
```python
class MIResult(BaseModel):
    latest_summary: str      # Latest analysis summary (for trading)
    past_summary: str        # Past MI formatting result
    short_term_query: str    # Reused for subsequent LLR/HLR search
    medium_term_query: str
    long_term_query: str
```

### Step 3 Test Results

```
tests/test_step3.py — 21 tests (20 unit + 1 integration)
```

Claude API replaced with `unittest.mock`. Only parsing·storing·retrieval logic validated by injecting pre-defined XML responses.

---

## Step 4 — LowLevelReflection (Vision)

**Goal**: Analyze Kline chart images with Claude Vision to infer the causes of short/medium/long-term price movements.

### Intent
The paper's LLR analyzes "the connection between price movements and Market Intelligence."  
Candlestick patterns, trading volume, and support/resistance levels were judged to be better analyzed by the model as images rather than text, hence Vision API is used.

### Implementation (`finagent/modules/low_level_reflection.py`)

#### Price Change Calculation
```python
def _calc_price_changes(df, target_date) -> dict:
    # 1d / 5d / 10d / 20d change rates (%) relative to target_date
    # Return None if insufficient data (safe handling at start of lookback)
```

#### Claude Vision Call
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

#### Implementation Flow
```
run(symbol, target_date, price_df, kline_image_path, mi_result)
  │
  ├─ 1. _calc_price_changes(price_df, target_date)  → 1d/5d/10d/20d change rates
  ├─ 2. memory.retrieve("low_level_reflection", mi_result.short_term_query, top_k=3)
  ├─ 3. Claude Vision call (Kline image + price changes + MI summary + past LLR)
  │     XML output: short/medium/long_term_reasoning, query
  └─ 4. memory.add("low_level_reflection",
                   "short-term: ...\nmedium-term: ...\nlong-term: ...",  ← 3 reasonings combined
                   {symbol, date})
        return LLRResult
```

#### Memory Storage Strategy
Store the three reasonings as a single combined document.  
Reason: When HLR searches, all three perspectives need to be together to understand context.

#### Result Schema
```python
class LLRResult(BaseModel):
    short_term_reasoning: str   # Short-term cause analysis
    medium_term_reasoning: str  # Medium-term cause analysis
    long_term_reasoning: str    # Long-term cause analysis
    query: str                  # Query for HLR retrieval
```

### Step 4 Test Results

```
tests/test_step4.py — 16 tests (15 unit + 1 integration)
```

Unit tests create a 1×1 white PNG as a temporary file to actually pass through the Vision code path.  
Claude responses replaced with mock.

---

## Step 5 — HighLevelReflection (Vision)

**Goal**: Re-evaluate past trading decisions along with the Trading chart to derive improvement plans.

### Intent
If LLR analyzes "why the price moved this way,"  
HLR evaluates "whether my decision in that situation was correct."  
Viewing a Trading chart with BUY▲/SELL▽ markers allows visual comparison of decision timing and subsequent price movement.

### Differences from LLR

| Item | LLR | HLR |
|------|-----|-----|
| Chart type | Kline (candlestick) | Trading (line + trade markers) |
| Analysis target | Price patterns | Appropriateness of past trading decisions |
| Additional input | Price change rates | past_actions (recent 14 records) |
| Memory storage content | 3 reasonings combined | summary (1-2 sentence key summary) |

### Implementation (`finagent/modules/high_level_reflection.py`)

#### Implementation Flow
```
run(symbol, target_date, trading_chart_path, past_actions, mi_result, llr_result)
  │
  ├─ 1. memory.retrieve("high_level_reflection", llr_result.query, top_k=3)
  ├─ 2. _format_actions(past_actions)  → date/action/price/quantity/reasoning table
  ├─ 3. Claude Vision call
  │     Input: Trading chart + trade history + MI summary + LLR short/medium/long-term + past HLR
  │     XML output: reasoning, improvement, summary, query
  └─ 4. memory.add("high_level_reflection", result.summary, {symbol, date})
        return HLRResult
```

#### Result Schema
```python
class HLRResult(BaseModel):
    reasoning: str    # Comprehensive evaluation of past decisions
    improvement: str  # Specific improvement plans
    summary: str      # Key summary for memory storage (1-2 sentences)
    query: str        # Query for DecisionMaking retrieval
```

### Step 5 Test Results

```
tests/test_step5.py — 15 tests (14 unit + 1 integration)
```

---

## Step 6 — DecisionMaking + Full Pipeline

**Goal**: Synthesize MI·LLR·HLR results with technical indicators to make the final BUY/SELL/HOLD decision, and integrate the one-day pipeline into `run_day()`.

### DecisionMakingModule (`finagent/modules/decision_making.py`)

#### Intent
Pass all analyses to Claude in a single prompt for the final judgment.  
Technical indicators are computed internally with `get_technical_signals(price_df)`, so the caller doesn't need to compute them separately.

#### Prompt Components
```
[Trader preference] aggressive / moderate / conservative
[Portfolio state] cash, held quantity, total assets
[Technical indicators] MACD, KDJ+RSI, ZMR signal text
[Market Intelligence] latest analysis + past patterns
[LLR] short/medium/long-term price movement analysis
[HLR] past decision evaluation + improvement points
```

#### Safeguard
```python
action = fields["action"].strip().upper()
if action not in ("BUY", "SELL", "HOLD"):
    action = "HOLD"  # Unexpected response → default HOLD
```

#### Result Schema
```python
class Decision(BaseModel):
    action: str      # "BUY" | "SELL" | "HOLD"
    reasoning: str   # Decision rationale
```

---

### Full Pipeline (`finagent/main.py`)

#### `run_day()` — Daily Execution
```python
def run_day(symbol, stock_name, target_date, price_df, fetcher,
            portfolio, mi_module, llr_module, hlr_module, dm_module,
            trader_preference="moderate") -> Decision:

    # Prevent look-ahead bias
    df = price_df.loc[:pd.Timestamp(target_date)]

    # 1. Data collection
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

    # 6. Execute trade
    portfolio.execute(decision.action, current_price, target_date, decision.reasoning)
    return decision
```

#### `run_backtest()` — Date Range Loop
```python
def run_backtest(symbol, stock_name, start, end, initial_cash=10_000_000, ...):
    # Collect full period data at once (lookback_days = (end-start).days + 90)
    # Filter target trading days
    # Call run_day() for each trading day, skip that day on exception and continue
    # After completion: performance analysis + chart generation
```

### Step 6 Test Results

```
tests/test_step6.py — 14 tests (13 unit + 1 integration)
```

`TestPipelineIntegration`: Replaces all Claude modules with mock and validates only the orchestration logic of `run_day()`.

---

## Step 7 — Performance Measurement

**Goal**: Quantify backtesting results with numerical metrics and visual charts.

### Intent
Simple returns alone are insufficient to judge strategy quality. Sharpe ratio (return vs. risk) and MDD (worst drawdown) must be viewed together to understand strategy robustness. Comparison against the Buy&Hold benchmark is the key.

### Implementation (`finagent/utils/metrics.py`)

#### Equity Curve Calculation
```python
def compute_equity_curve(trades, price_df, initial_cash, buy_ratio=0.5) -> pd.Series:
    # Replay trade history in chronological order
    # Calculate cash + position * close_price for each business day
    # index: date, values: total portfolio value
```

#### Performance Metrics
```python
def compute_performance(equity_curve, initial_cash, risk_free_rate=0.03) -> dict:
    # total_return_pct       = (final - initial) / initial * 100
    # annualized_return_pct  = ((final/initial)^(1/years) - 1) * 100
    # sharpe_ratio           = mean(excess_daily) / std(excess_daily) * sqrt(252)
    # max_drawdown_pct       = min((curve - rolling_max) / rolling_max) * 100
    # volatility_annual_pct  = std(daily_returns) * sqrt(252) * 100
```

#### Visualization
```
Upper panel: FinAgent equity curve vs Buy&Hold (blue line vs orange dashed)
             BUY: green vertical line / SELL: red vertical line
Lower panel: Drawdown (red fill)
Save: charts/performance_{symbol}_{start}_{end}.png
```

#### main.py Integration — Auto output at end of backtesting
```
====================================================
  Backtesting Result: 005930  (2024-01-02 ~ 2024-03-29)
====================================================
  Final Portfolio Value:      11,234,567 KRW
  Total Return:                   +12.35%
  Annualized Return:              +52.18%
  Sharpe Ratio:                    1.234
  Max Drawdown (MDD):              -5.67%
  Annual Volatility:              18.45%
----------------------------------------------------
  Buy & Hold Return:               +8.90%
  Excess Return:                   +3.45%
----------------------------------------------------
  Buy count:                           5
  Sell count:                          4
  Hold count:                         58
  Performance chart: charts/performance_005930_....png
====================================================
```

### Step 7 Test Results

```
tests/test_step7.py — 22 tests
```

| Test Class | Case Count | Key Validations |
|-------------|-----------|-----------|
| `TestComputeEquityCurve` | 6 | No-trade fixed value, post-BUY rise, post-SELL fixed, HOLD no effect |
| `TestComputeBenchmark` | 4 | Day 1 = initial_cash, positive return in bull market |
| `TestComputePerformance` | 8 | Total return calculation, MDD accuracy, Sharpe sign, all key existence |
| `TestPlotPerformance` | 2 | PNG file creation and size > 0 |
| `TestPortfolioGetAllTrades` | 2 | Order guarantee, empty portfolio handling |

---

## Final Status

### File Structure
```
finagent/
├── data/fetcher.py               201 lines
├── memory/store.py               135 lines
├── modules/
│   ├── market_intelligence.py    180 lines
│   ├── low_level_reflection.py   211 lines
│   ├── high_level_reflection.py  194 lines
│   └── decision_making.py        167 lines
├── portfolio/portfolio.py        229 lines
├── tools/technical_indicators.py 144 lines
├── utils/
│   ├── schemas.py                 62 lines
│   ├── xml_parser.py              31 lines
│   └── metrics.py                191 lines
└── main.py                       255 lines
                          Total: 1,907 lines
```

### Test Status
```
Step 1:  21 tests  (18 unit + 3 integration)
Step 2:  10 tests
Step 3:  21 tests  (20 unit + 1 integration)
Step 4:  16 tests  (15 unit + 1 integration)
Step 5:  15 tests  (14 unit + 1 integration)
Step 6:  14 tests  (13 unit + 1 integration)
Step 7:  22 tests
─────────────────────────────────────────
Total:  119 tests  (112 unit + 7 integration)
Unit tests: 112/112 passing
```

### How to Run
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

## Key Design Decision Summary

| Decision | Choice | Alternative | Reason |
|------|------|------|------|
| Stock price data | pykrx | yfinance | Dedicated to KRX Korean stocks, free |
| News source | Google RSS | Finnhub, NewsAPI | No API key required, quality Korean stock news |
| Vector DB | ChromaDB | FAISS | Local execution, metadata filtering, simple API |
| Embeddings | all-MiniLM-L6-v2 | OpenAI ada | Free, local, multilingual support |
| LLM | claude-sonnet-4-6 | claude-opus | Cost/performance balance |
| Portfolio storage | SQLite | PostgreSQL, Redis | Single file, no external server required |
| XML parsing | Custom implementation (regex) | lxml, BeautifulSoup | Lightweight, optimized for Claude response characteristics |
| Technical indicator injection | Convert to text signals | Pass raw numbers directly | Form that the LLM can immediately understand the meaning of |
| Look-ahead bias | `df.loc[:target_date]` slicing | Separate API calls | Block future data without performance overhead |
