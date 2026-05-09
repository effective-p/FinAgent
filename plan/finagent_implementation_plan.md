# FinAgent Implementation Plan

Planning a simplified version of the paper's core structure that actually runs.

---

## Overall Architecture Summary

```
[Data Collection] → [Market Intelligence] → [Reflection] → [Decision]
                        ↕                     ↕
                    [Memory (Vector DB)]
```

---

## Technology Stack Selection

### Language & Runtime
- Python 3.11+

### LLM
- `claude-sonnet-4-20250514` — text, vision analysis, reflection modules, decision making
- `claude-opus-4-20250514` — optionally usable for high-difficulty reasoning

### Vector DB (Memory)
- `ChromaDB` — usable locally right away, simple installation. FAISS is also possible, but ChromaDB has more convenient metadata filtering

### Embeddings
- `sentence-transformers` (all-MiniLM-L6-v2) — free, local execution

### Data Collection
- `krx_price` — OHLCV + change rates via pykrx
- `google_news` — News RSS based on stock name
- `pandas_ta` — Technical indicators (MACD, KDJ, RSI, Bollinger Bands)

### Visualization (Kline/Trading Chart)
- `mplfinance` — Kline chart generation → save as image and pass to Claude Vision

### Other
- `pydantic` — Inter-module data schema definitions
- `sqlite3` — Trade history storage (lightweight)

---

## Module-by-Module Implementation Plan

### 1. Data Layer

```python
# data_layer.py
class DataFetcher:
    def get_price_data(symbol, lookback_days)  # krx_price
    def get_news(symbol, date)                 # google_news
    def get_technical_indicators(df)           # MACD, RSI, KDJ, BB via pandas_ta
    def plot_kline_chart(df) -> image_path     # mplfinance → PNG save
    def plot_trading_chart(df, actions) -> image_path
```

---

### 2. Memory Module

Create 3 collections in ChromaDB so each module stores/queries independently.

```python
# memory.py
class MemoryStore:
    collections:
        - "market_intelligence"   # MI summary + query text
        - "low_level_reflection"  # LLR results
        - "high_level_reflection" # HLR results

    def add(collection, text, metadata)
    def retrieve(collection, query_text, top_k=3) -> List[str]
```

**Diversified Retrieval Implementation:**

The core idea of the paper is to separate the "trading summary" from the "retrieval query". Design the prompt so Claude outputs `short_term_query`, `medium_term_query`, `long_term_query` as separate fields during MI analysis, then search ChromaDB independently with each query and combine results.

```python
# retrieval approach
results = []
for query in [short_term_q, medium_term_q, long_term_q]:
    results += memory.retrieve("market_intelligence", query, top_k=2)
# → Collect up to 6 past MI records from diverse perspectives
```

---

### 3. Market Intelligence Module

```python
# market_intelligence.py
class MarketIntelligenceModule:

    def run(symbol, date, price_df, news_list) -> MIResult:
        # Step 1: Request Claude to analyze latest news + prices
        latest_mi = self._analyze_latest(news_list, price_df)
        # → Returns summary (for trading) + query_texts (for retrieval)

        # Step 2: Search past MI via Diversified Retrieval
        past_mi_docs = self._diversified_retrieve(latest_mi.queries)

        # Step 3: Generate past MI summary
        past_mi_summary = self._summarize_past(past_mi_docs)

        # Step 4: Store latest MI in memory
        memory.add("market_intelligence", latest_mi.summary,
                   metadata={"date": date, "queries": latest_mi.queries})

        return MIResult(latest_summary, past_summary)
```

**Claude Call Example (XML output enforced):**

```python
prompt = f"""
News: {news_text}
Price: {price_data}

Respond only in the following XML format:
<output>
  <string name="summary">trading summary</string>
  <map name="query">
    <string name="short_term_query">short-term search query</string>
    <string name="medium_term_query">medium-term search query</string>
    <string name="long_term_query">long-term search query</string>
  </map>
</output>
"""
```

---

### 4. Low-Level Reflection (LLR)

Analyzes the relationship between price movements and Market Intelligence.

| Item | Content |
|------|------|
| Target | Price Movements |
| Visual Data | Kline Chart |
| Market Understanding | Micro |
| Function | Adaptability |

```python
# reflection.py
class LowLevelReflection:
    def run(mi_result, kline_image_path, price_changes) -> LLRResult:
        # Pass Kline chart image + price change data + MI summary to Claude Vision
        # Generate short/medium/long-term price movement reasoning
        # Also generate query field → store in memory

        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", ...}},  # kline chart
                    {"type": "text", "text": prompt}
                ]
            }]
        )
```

**Input:** MI summary + Kline chart image + price change rates for past N days / future N days

**Output:** `short_term_reasoning`, `medium_term_reasoning`, `long_term_reasoning`, `query`

---

### 5. High-Level Reflection (HLR)

Reflects on the correctness of past trading decisions.

| Item | Content |
|------|------|
| Target | Trading Decisions |
| Visual Data | Trading Chart |
| Market Understanding | Macro |
| Function | Amendability |

```python
class HighLevelReflection:
    def run(mi_result, llr_result, trading_chart_path,
            past_actions_and_reasoning) -> HLRResult:
        # Pass trading chart image + past 14 days of actions + reasoning
        # Evaluate whether each decision was correct
        # Suggest improvement plans
        # Generate summary + query → store in memory
```

**Input:** MI summary + LLR result + Trading chart image + past action log

**Output:** `reasoning`, `improvement`, `summary`, `query`

---

### 6. Tool-Augmented Decision Making

```python
class DecisionMakingModule:
    def run(mi_result, llr_result, hlr_result,
            technical_signals, trader_preference) -> Decision:

        # technical_signals: MACD/KDJ&RSI/ZMR results converted to text
        # trader_preference: "aggressive" or "conservative"
        # Also pass current position and cash balance

        # Claude returns BUY/HOLD/SELL + reasoning
```

**Augmented Tools (simplified):**

Since the paper's expert guidance is complex to implement, only the signals from technical indicators (MACD, KDJ+RSI, ZMR) are computed and injected as text into the LLM prompt.

```python
def get_technical_signals(df) -> str:
    # MACD crossover → "BUY signal (MACD crossed above signal line)"
    # KDJ+RSI → "SELL signal (KDJ overbought, RSI > 70)"
    # ZMR → "HOLD (z-score: 0.3, within normal range)"
    return signal_text
```

---

## Full Execution Flow

```python
# main.py - daily execution
def run_step(symbol, date, portfolio):

    # 1. Data collection
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

    # 6. Execute trade & save results
    portfolio.execute(decision.action, price_df.iloc[-1]['close'])
    return decision
```

---

## Project Structure

```
finagent/
├── data/
│   └── fetcher.py                   # pykrx + news + chart generation
├── memory/
│   └── store.py                     # ChromaDB wrapper
├── modules/
│   ├── market_intelligence.py
│   ├── low_level_reflection.py
│   ├── high_level_reflection.py
│   └── decision_making.py
├── tools/
│   └── technical_indicators.py      # MACD, RSI, KDJ, ZMR signals
├── portfolio/
│   └── portfolio.py                 # Position/cash/trade history management
├── utils/
│   └── xml_parser.py                # Claude response XML parsing
└── main.py                          # Backtesting loop
```

---

## Implementation Order

| Step | Content | Key Tools |
|------|------|-----------|
| Step 1 | Implement & test `DataFetcher` + `Portfolio` + `TechnicalIndicators` | pykrx, pandas_ta, mplfinance |
| Step 2 | Implement `MemoryStore` (ChromaDB), test store/retrieve | ChromaDB, sentence-transformers |
| Step 3 | Implement `MarketIntelligenceModule` (including Diversified Retrieval) | Anthropic API, ChromaDB |
| Step 4 | Implement `LowLevelReflection` (using Vision API) | Anthropic API (Vision) |
| Step 5 | Implement `HighLevelReflection` | Anthropic API (Vision) |
| Step 6 | Implement `DecisionMakingModule` + integrate full pipeline | Anthropic API |
| Step 7 | Run single-stock backtesting and measure performance | Full system |

---

## Reference APIs and Libraries

| Purpose | Library / API | Cost |
|------|-----------------|------|
| Stock price data | `pykrx` | Free |
| News data | `google_news` | Free |
| Technical indicators | `pandas_ta` | Free |
| Kline charts | `mplfinance` | Free |
| LLM (text+vision) | Anthropic Claude API | Paid |
| Vector DB | `ChromaDB` | Free (local) |
| Embeddings | `sentence-transformers` | Free (local) |
| Data schemas | `pydantic` | Free |
| Trade history | `sqlite3` | Free (built-in) |
