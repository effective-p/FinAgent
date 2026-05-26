# FinAgent 로컬 변경 이력

git push 권한 없음 — 로컬에서만 관리. 커밋 시 이 파일 참고.

---

## 프로젝트 목표

> **논문(arxiv 2402.18485 — "A Multimodal Foundation Agent for Financial Trading")을 한국 시장(KRX)에 맞게 검증하는 것.**
>
> 모든 변경은 이 목표를 기준으로 판단한다:
> - 논문에 명시된 내용은 최대한 그대로 구현한다.
> - 논문이 US 시장을 전제해 한국에 적용 불가한 부분만 최소한으로 대체한다.
> - 편의 기능은 논문 검증 결과에 영향을 주지 않는 범위에서만 허용한다.

---

## 카테고리 기호

| 기호 | 의미 |
|------|------|
| 🐛 BUG | 소스 버그 수정 (기능 오작동) |
| 📄 PAPER | 논문에 명시된 내용 중 누락되어 추가 |
| 🇰🇷 KR-ADAPT | 논문에 없으나 논문 의도를 한국 시장에 충실히 반영하기 위해 추가 |
| 🛠 UX | 프로그램 사용 편의를 위한 추가 (논문 검증 결과에 영향 없음) |

---

## 🐛 BUG — 소스 버그 수정

### 뉴스 미래 누출(look-ahead) 차단
**파일:** `finagent/data/fetcher.py`

**변경 전:** `±7일` 필터 → 미래 뉴스가 백테스트 분석에 유입  
**변경 후:** `과거 7일만` 허용 (`days_diff > 0` 이면 제외)

**논문 근거:** 논문 §5.1 — *"we conduct backtesting experiments"*. 백테스트는 미래 정보 없이 과거 데이터만 사용해야 한다는 기본 전제. 논문 Figure 1의 파이프라인도 해당 거래일 이전 정보만 입력으로 사용.

---

### 에쿼티 커브 재현 수량 불일치
**파일:** `finagent/utils/metrics.py`

**변경 전:** `cash * buy_ratio / price` 로 수량 재계산 → 실제 거래 수량과 불일치  
**변경 후:** DB에 기록된 `t.quantity` 직접 사용

**논문 근거:** 논문 §5.2 — ARR, Sharpe, Calmar, Sortino, MDD, VOL 6개 지표를 정확히 계산하려면 실제 거래 수량 기반의 에쿼티 커브가 전제되어야 함.

---

### 백테스트 날짜 버그 — lookback_days 오계산
**파일:** `finagent/main.py`

**변경 전:** `lookback_days = (end - start).days + 90` → 백테스트 기간 길이만 계산  
**변경 후:** `lookback_days = (date.today() - start).days + 90`

**논문 근거:** 논문 §5.1 — 백테스트 기간 2022.01 ~ 2023.12 (2년). 현재 시점에서 과거 데이터를 올바르게 조회하기 위한 구현 버그. 논문의 실험 재현 가능성에 직접 영향.

---

### 포트폴리오 재실행 리셋 누락
**파일:** `finagent/portfolio/portfolio.py`, `finagent/main.py`

**변경 전:** `INSERT OR IGNORE` → 동일 종목 재실행 시 이전 포지션/현금 유지  
**변경 후:** `reset=True` 파라미터 추가. 재실행 시 해당 종목의 trades/state DELETE 후 초기화

**논문 근거:** 논문 §5.1 — *"initial capital of $1,000,000"*. 각 백테스트는 초기 자본에서 독립적으로 시작해야 함. 이전 상태가 이어지면 논문의 실험 조건과 달라짐.  
※ `memory_db/`(ChromaDB)는 리셋 안 됨 — 논문의 메모리 누적 구조는 그대로 유지.

---

### 성과 차트 한글 폰트 깨짐
**파일:** `finagent/utils/metrics.py` — `plot_performance()`

**변경 전:** `["Pretendard"]` 만 시도 → Windows에 없는 폰트, 한글 깨짐  
**변경 후:** OS별 우선순위로 탐색 (Malgun Gothic → Apple SD Gothic Neo → NanumGothic → DejaVu Sans)

**논문 근거:** 논문과 직접 관련 없음. 한국 시장 검증 시 한글 종목명/라벨 가독성 확보를 위한 수정.

---

## 📄 PAPER — 논문에 명시되었으나 누락되어 추가

### ~~거래 수수료 0.1% (논문 §5.1) — 한국 시장 적용 제외~~
**파일:** `finagent/portfolio/portfolio.py`, `finagent/utils/metrics.py`

**논문 원문 (§5.1 Experiment Settings):**
> *"Following standard practice in quantitative finance, we assume a fixed transaction cost of 0.1% for both buying and selling."*

**제거 결정 (2026-05-26):** 한국 KRX 거래 수수료가 미비하여 제거. 포지션 관리 전략(BUY 후 장기 보유)에서는 수수료 영향이 미미하므로 논문의 US 기준 0.1% 그대로 적용하지 않기로 결정.

**변경 전:**
- `TRANSACTION_COST_RATE = 0.001` 상수
- BUY: `quantity = int(available / (price * 1.001))`, fee 공제
- SELL: `new_cash = cash + position * price - fee`

**변경 후 (수수료 제거):**
- BUY: `quantity = int(available / price)`
- SELL: `new_cash = cash + position * price`

---

### LLR Diversified Retrieval — 단일 쿼리 → 3방향 (논문 §4.1)
**파일:** `finagent/modules/low_level_reflection.py`

**논문 원문 (§4.1 Market Intelligence):**
> *"three queries: short-term query, medium-term query, and long-term query ... independently retrieve from memory to ensure temporal diversity"*

**변경 전:** MI의 `short_term_query` 하나로만 LLR 메모리 검색  
**변경 후:** 3개 쿼리로 독립 검색, 최대 6개 반환 (`diversified_retrieve`)

**추가 근거:** 논문 Ablation Study Table 5 (RQ4) — Diversified Retrieval 제거 시 모든 지표에서 성과 저하 확인.

---

### HLR에 과거 LLR 메모리 추가 (논문 §4.3)
**파일:** `finagent/modules/high_level_reflection.py`

**논문 원문 (§4.3 High-Level Reflection, workflow step 6):**
> *"retrieve past low-level reflections from memory store"*

**논문 근거 (Appendix F.3 HLR prompt):** `$$past_low_level_reflection$$` 필드 명시.

**변경 내용:** HLR 프롬프트에 `[과거 Low-Level Reflection 참고]` 섹션 추가. LLR 쿼리로 과거 LLR 메모리 검색 후 주입.

---

### DM `analysis` 필드 + 제약 조건 추가 (논문 §F.4)
**파일:** `finagent/modules/decision_making.py`, `finagent/utils/schemas.py`

**논문 근거 (Appendix F.4 DM prompt output format):**
> *"`<analysis>`: step-by-step analysis ... `<action>`: BUY/HOLD/SELL ... `<reasoning>`: explanation"*

**논문 근거 (DM prompt rule 9):**
> *"If cash < stock price, cannot BUY. If position = 0, cannot SELL."*

**변경 내용:** `<analysis>` 필드 추가, `Decision.analysis` 스키마 추가, 제약조건 프롬프트 추가.

---

### Calmar Ratio + Sortino Ratio 추가 (논문 §5.2)
**파일:** `finagent/utils/metrics.py`, `finagent/main.py`

**논문 원문 (§5.2 Evaluation Metrics):**
> *"We evaluate the performance of all methods using six financial metrics: ARR, SR (Sharpe), CR (Calmar), SoR (Sortino), MDD, VOL."*

**변경 내용:**
- Calmar Ratio: `annualized_return / |MDD|`
- Sortino Ratio: `annualized_return / downside_std`

---

## 🇰🇷 KR-ADAPT — 논문 의도를 한국 시장에 충실히 반영하기 위해 추가

### 외국인/기관 투자자 동향 → Market Intelligence 주입
**파일:** `finagent/data/fetcher.py`, `finagent/modules/market_intelligence.py`, `finagent/main.py`

**논문 근거 (§4.2 Market Intelligence):**
> *"analyze the latest news and diverse market information to generate a comprehensive market summary"*

**한국 시장 적용 이유:** 논문의 US 시장에서는 기관 투자자 정보가 뉴스/가격에 이미 반영되어 있음. 한국 KRX에서는 외국인/기관 순매수가 가격에 선행하는 독립 시그널로 공개 제공됨(KRX 공식 데이터). MI 모듈이 "diverse market information"을 분석한다는 논문 취지에 부합.

**추가 내용:** `DataFetcher.get_investor_trading()` — pykrx로 외국인·기관·개인 순매수 금액 조회. MI 프롬프트 `[투자자 동향]` 섹션에 주입.

---

### Expert Guidance → PER/PBR/배당 기반 밸류에이션 신호로 대체
**파일:** `finagent/data/fetcher.py`, `finagent/modules/decision_making.py`, `finagent/main.py`

**논문 근거 (§4.4 Expert Guidance):**
> *"incorporate external expert knowledge from Bloomberg Terminal and Seeking Alpha analyst reports to provide additional context for decision making"*

**한국 시장 적용 이유:** Bloomberg Terminal과 Seeking Alpha 한국 주식 애널리스트 리포트는 논문 저자들의 데이터 소스이며 한국에서는 동일한 방식으로 이용 불가. 동일한 역할(DM에 외부 전문가 시각 주입)을 KRX 공식 PER/PBR/배당수익률 데이터로 대체.

| 구분 | 논문 | 우리 구현 |
|------|------|---------|
| 데이터 소스 | Bloomberg, Seeking Alpha | KRX 공식 PER/PBR/배당 |
| 신호 형태 | 애널리스트 텍스트 (정성) | 역사적 평균 대비 수치 (정량) |
| DM에서의 역할 | 외부 전문가 시각 주입 | 동일 |

**추가 내용:** `DataFetcher.get_fundamental_guidance()` — 최근 60거래일 평균 대비 ±15% 기준으로 BULLISH/BEARISH/NEUTRAL 신호 생성.

---

### 볼린저 밴드(BB) 기술지표 추가
**파일:** `finagent/utils/schemas.py`, `finagent/tools/technical_indicators.py`

**논문 근거 (§4.5 Tool Augmentation):**
> *"convert technical indicators (MACD, KDJ, RSI, ZMR) into human-readable signal strings injected into the Decision Making prompt"*

**한국 시장 적용 이유:** 논문 명시 지표(MACD, KDJ, RSI, ZMR)는 모두 추세/모멘텀 계열. 평균회귀(mean-reversion) 관점 지표가 없어 신호가 편향될 수 있음. BB는 KRX 시장에서 표준 지표로 논문이 의도한 "다양한 기술 분석 관점" 보강.

**추가 내용:** `_calc_bb()` (window=20, std=2.0). 하단밴드 이탈 → BUY, 상단밴드 이탈 → SELL. `TechnicalSignals.bb_signal` 필드 추가.

---

### KRX 1주 단위 정수 거래 적용
**파일:** `finagent/portfolio/portfolio.py`

**논문 근거 (§5.1):**
> *"initial capital of $1,000,000 ... position sizing based on available cash"*

**한국 시장 적용 이유:** 논문은 US 시장 기준으로 소수점 거래를 암묵적으로 허용. 한국 KRX는 1주 단위만 거래 가능. 논문의 실험 조건(실제 거래 가능한 포지션 사이징)을 한국 시장에 충실히 반영.

**변경 전:** `quantity = available / price` (소수점 수량)  
**변경 후:** `quantity = int(available / price)` (정수 수량)

---

## 🛠 UX — 프로그램 사용 편의 (논문 검증 결과에 영향 없음)

### `.env` 파일을 통한 API 키 설정 지원
**파일:** `finagent/main.py`, `run_web.py`, `requirements.txt`, `environment.yml`, `.env.example` (신규)

**내용:** `python-dotenv` 패키지 추가. `main.py`/`run_web.py` 상단에 `load_dotenv()` 추가. `.env.example` 템플릿 제공.

---

### 멀티 LLM 추상화 레이어 — OpenAI / Anthropic / Gemini 통합 지원
**파일:** `finagent/llm/__init__.py` (신규), `finagent/llm/client.py` (신규), 4개 모듈

**내용:** `.env`에서 `LLM_PROVIDER` 한 줄만 변경하면 LLM 전환.

| Provider | 기본 모델 | 환경변수 |
|----------|----------|---------|
| `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` |
| `anthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| `gemini` | `gemini-2.0-flash` | `GEMINI_API_KEY` |

**핵심 인터페이스:**
- `LLMClient.chat(messages, max_tokens)` — 텍스트 전용 (MI, DM)
- `LLMClient.chat_with_image(prompt, image_b64, max_tokens)` — Vision (LLR, HLR)

---

### 성과 차트 재생성 유틸리티
**파일:** `regenerate_chart.py` (신규)

**내용:** 전체 백테스트 재실행 없이 `portfolio.db` 기존 데이터로 성과 차트만 재생성.

```bash
python regenerate_chart.py
```

---

### FINAGENT_TEMPERATURE 환경변수 추가
**파일:** `finagent/llm/client.py`, `.env`, `.env.example`

**배경:**
기존 코드에 temperature 설정이 없었음. OpenAI/Anthropic/Gemini 모두 미설정 시 기본값 **1.0**으로 동작하며, 이는 금융 판단에 부적합한 높은 창의성 수준. LG에너지솔루션 2026년 4월 백테스트를 CLI와 웹에서 각각 실행했을 때 결과가 아래와 같이 달라지는 문제가 실제로 발생:

| 항목 | 웹 실행 (temperature=1.0) | CLI 실행 (temperature=1.0) |
|------|--------------------------|---------------------------|
| 총 수익률 | +11.51% | +15.53% |
| Buy&Hold | +13.14% | +13.14% |
| 초과 수익 | **-1.63%** (하회) | **+2.39%** (상회) |
| MDD | -4.52% | -4.49% |
| Sharpe | 3.006 | 4.303 |
| 거래 패턴 | BUY 5, SELL 0, HOLD 17 | BUY 6, SELL 1, HOLD 15 |

동일한 입력 데이터임에도 SELL 여부가 달라져 결과가 역전됨. 논문 재현 및 실험 비교를 위해 재현성 확보가 필수.

**변경 내용:**
- `LLMClient.__init__()`에서 `FINAGENT_TEMPERATURE` 환경변수 읽기 (기본값 `0`)
- `chat()`, `chat_with_image()` 호출 시 `temperature=self.temperature` 전달 (3개 provider 모두)
- `.env`에 `FINAGENT_TEMPERATURE=0` 추가
- `.env.example`에 설명 주석 추가

**`.env` 설정 가이드:**
```
FINAGENT_TEMPERATURE=0    # 결정적 출력, 매 실행 동일 결과 (권장)
FINAGENT_TEMPERATURE=0.7  # 적당한 다양성 허용
FINAGENT_TEMPERATURE=1.0  # 각 provider 원래 기본값 (재현성 없음)
```

---

## 전체 변경 요약표

> 💡 **제거 가능 여부**: ✅ 제거 가능 (되돌리기 용이) / ⚠️ 조건부 (다른 코드 의존) / ❌ 필수 (제거 시 기능/결과 손상)

| # | 카테고리 | 항목 | 관련 파일 | 논문 근거 (섹션) | 제거 가능 |
|---|---------|------|----------|----------------|---------|
| 1 | 🐛 BUG | 뉴스 look-ahead 차단 | `fetcher.py` | §5.1 백테스트 전제 조건 | ❌ |
| 2 | 🐛 BUG | 에쿼티 커브 수량 불일치 | `metrics.py` | §5.2 성과 지표 정확성 | ❌ |
| 3 | 🐛 BUG | lookback_days 날짜 버그 | `main.py` | §5.1 실험 재현 가능성 | ❌ |
| 4 | 🐛 BUG | 포트폴리오 리셋 누락 | `portfolio.py`, `main.py` | §5.1 초기 자본 독립성 | ❌ |
| 5 | 🐛 BUG | 성과 차트 한글 폰트 깨짐 | `metrics.py` | — | ✅ |
| 6 | ~~📄 PAPER~~ | ~~거래 수수료 0.1%~~ **→ 제거** (한국 KRX 수수료 미비) | `portfolio.py`, `metrics.py` | §5.1 명시 | 제거됨 |
| 7 | 📄 PAPER | LLR Diversified Retrieval 3방향 | `low_level_reflection.py` | §4.1 명시, Ablation RQ4 | ⚠️ |
| 8 | 📄 PAPER | HLR에 과거 LLR 메모리 | `high_level_reflection.py` | §4.3 step 6, Appendix F.3 | ⚠️ |
| 9 | 📄 PAPER | DM analysis 필드 + 제약조건 | `decision_making.py`, `schemas.py` | Appendix F.4 | ⚠️ |
| 10 | 📄 PAPER | Calmar + Sortino Ratio | `metrics.py`, `main.py` | §5.2 명시 | ✅ |
| 11 | 🇰🇷 KR-ADAPT | 외국인/기관 투자자 동향 → MI | `fetcher.py`, `market_intelligence.py`, `main.py` | §4.2 (diverse market info) | ✅ |
| 12 | 🇰🇷 KR-ADAPT | Expert Guidance → PER/PBR 대체 | `fetcher.py`, `decision_making.py`, `main.py` | §4.4 (expert knowledge) | ✅ |
| 13 | 🇰🇷 KR-ADAPT | 볼린저 밴드(BB) 추가 | `technical_indicators.py`, `schemas.py` | §4.5 (tool augmentation 보강) | ✅ |
| 14 | 🇰🇷 KR-ADAPT | KRX 1주 단위 정수 거래 | `portfolio.py` | §5.1 (position sizing) | ⚠️ |
| 15 | 🛠 UX | `.env` 지원 | `main.py`, `run_web.py`, `.env.example` | — | ✅ |
| 16 | 🛠 UX | 멀티 LLM 추상화 레이어 | `finagent/llm/`, 4개 모듈 | — | ✅ |
| 17 | 🛠 UX | 성과 차트 재생성 유틸리티 | `regenerate_chart.py` | — | ✅ |
| 18 | 🛠 UX | FINAGENT_TEMPERATURE 추가 (기본값 0) | `llm/client.py`, `.env.example` | §5.1 재현성 | ❌ |
