# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Project Overview

FinAgent is a multimodal LLM-based financial trading agent based on the paper "A Multimodal Foundation Agent for Financial Trading" (arxiv 2402.18485). It combines market data analysis, visual chart understanding, memory-augmented reflection, and tool-augmented decision making into a backtesting pipeline.

## Setup

```bash
pip install anthropic pykrx pandas_ta mplfinance chromadb sentence-transformers pydantic feedparser
```

Required environment variables:
- `ANTHROPIC_API_KEY`

## Architecture

The pipeline processes one trading day at a time:

```
DataFetcher → MarketIntelligence → LowLevelReflection → HighLevelReflection → DecisionMaking → Portfolio
                     ↕                      ↕                     ↕
                           MemoryStore (ChromaDB, 3 collections)
```

**Planned file structure:**
```
finagent/
├── data/fetcher.py              # pykrx prices (KRX), Google RSS news, mplfinance charts
├── memory/store.py              # ChromaDB wrapper (3 collections below)
├── modules/
│   ├── market_intelligence.py   # Analyzes latest news+prices; diversified retrieval
│   ├── low_level_reflection.py  # Price movement reasoning via Vision API + kline chart
│   ├── high_level_reflection.py # Past decision evaluation via Vision API + trading chart
│   └── decision_making.py       # BUY/HOLD/SELL with technical signal injection
├── tools/technical_indicators.py # MACD, RSI, KDJ, ZMR → text signal strings
├── portfolio/portfolio.py        # Cash/position/trade history tracking (sqlite3)
├── utils/xml_parser.py           # Parse Claude's XML-format responses
└── main.py                       # Backtesting loop over date range
```

## Key Design Decisions

### Claude API Usage
- Primary model: `claude-sonnet-4-6` (text analysis, reflection, decision)
- All Claude responses use XML output format enforced via prompts; `utils/xml_parser.py` parses them
- Vision API calls (LLR, HLR modules) pass base64-encoded chart images alongside text

### Memory (ChromaDB)
Three collections with independent storage/retrieval:
- `market_intelligence` — MI summaries + retrieval queries
- `low_level_reflection` — LLR results
- `high_level_reflection` — HLR results

### Diversified Retrieval
MarketIntelligence generates three separate retrieval queries (`short_term_query`, `medium_term_query`, `long_term_query`) alongside its trading summary. All three are used to independently query ChromaDB, returning up to 6 past MI records with temporal diversity.

### Technical Signals (Tool Augmentation)
Rather than implementing the paper's full expert guidance system, `tools/technical_indicators.py` converts MACD, KDJ+RSI, and ZMR calculations into human-readable signal strings that are injected into the Decision Making prompt.

### News Source
News is fetched from Google News RSS (`feedparser`) using the stock's Korean name. No API key required.
- RSS URL pattern: `https://news.google.com/rss/search?hl=ko&gl=KR&ie=UTF-8&q={종목명+주가}`
- `DataFetcher.get_news()` accepts `stock_name` (Korean name, e.g. "삼성전자") alongside `symbol`
- Client-side date filter: only news within ±7 days of `target_date` is returned

## Running a Backtest

```bash
python finagent/main.py --symbol AAPL --start 2024-01-01 --end 2024-06-30
```

## Implementation Order

Per the plan, implement in this sequence:
1. `DataFetcher` + `Portfolio` + `TechnicalIndicators`
2. `MemoryStore` (ChromaDB)
3. `MarketIntelligenceModule` (with Diversified Retrieval)
4. `LowLevelReflection` (Vision)
5. `HighLevelReflection` (Vision)
6. `DecisionMakingModule` + full pipeline integration
7. Backtesting + performance measurement

## Do
- Whenever the Python package is modified, update @requirements.txt and @environment.yml to keep them up to date.
- Push directly to 'origin/main' without a PR when the user runs 'git push'.

## Don't do