/* FinAgent Web UI — SSE 클라이언트 + DOM 조작 */
'use strict';

// ── 인증 ─────────────────────────────────────────────────────────────────────
function getToken() { return localStorage.getItem('fa_token'); }
function authHeaders() {
  return { 'Authorization': 'Bearer ' + getToken(), 'Content-Type': 'application/json' };
}
function navLogout() {
  localStorage.removeItem('fa_token');
  localStorage.removeItem('fa_user');
  window.location.href = '/login.html';
}

(function checkAuth() {
  if (!getToken()) { window.location.href = '/login.html'; }
  const u = localStorage.getItem('fa_user');
  const el = document.getElementById('nav-user');
  if (el && u) el.textContent = u;
})();

// ── LLM 드롭다운 로드 ────────────────────────────────────────────────────────
async function loadLLMConfigs() {
  try {
    const res = await fetch('/api/llm-configs', { headers: authHeaders() });
    if (!res.ok) return;
    const configs = await res.json();
    const sel = document.getElementById('llm_config_id');
    if (!sel) return;
    configs.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = `${c.name} (${c.provider}/${c.model})`;
      sel.appendChild(opt);
    });
  } catch { /* ignore */ }
}
loadLLMConfigs();


// ── 파이프라인 노드 관리 ───────────────────────────────────────────────────────
const PIPELINE_STEP_IDS = [
  'ohlcv_fetch', 'news_fetch', 'market_intelligence',
  'low_level_reflection', 'high_level_reflection',
  'decision_making', 'trade_execution',
];

// 루프 내부 단계 (OHLCV 제외)
const LOOP_STEP_IDS = PIPELINE_STEP_IDS.filter(id => id !== 'ohlcv_fetch');

function activatePipelineStep(stepName) {
  const idx = PIPELINE_STEP_IDS.indexOf(stepName);
  if (idx === -1) return;
  PIPELINE_STEP_IDS.forEach((id, i) => {
    const el = document.getElementById('pnode-' + id);
    if (!el) return;
    el.classList.remove('active', 'done', 'completed');
    if (i < idx)      el.classList.add('done');    // 이미 지난 단계: 켜짐
    else if (i === idx) el.classList.add('active'); // 현재 단계: 깜빡임
    // i > idx: inactive
  });
}

function completePipelineDay() {
  // 루프 단계만 ✓ 플래시 후 비활성화 (OHLCV는 done 유지)
  LOOP_STEP_IDS.forEach(id => {
    const el = document.getElementById('pnode-' + id);
    if (!el) return;
    el.classList.remove('active', 'done');
    el.classList.add('completed');
  });
  setTimeout(() => {
    LOOP_STEP_IDS.forEach(id => {
      const el = document.getElementById('pnode-' + id);
      if (el) el.classList.remove('completed'); // inactive로 복귀
    });
  }, 500);
}

function resetPipeline() {
  PIPELINE_STEP_IDS.forEach(id => {
    const el = document.getElementById('pnode-' + id);
    if (!el) return;
    el.classList.remove('active', 'done', 'completed');
  });
}

// ── 상태 ──────────────────────────────────────────────────────────────────────
let currentJobId = null;
let eventSource = null;
let tradeLog = [];   // progress 이벤트 누적 (거래 내역 테이블용)
let klineFiles = [];
let tradingFiles = [];

// ── DOM 참조 ──────────────────────────────────────────────────────────────────
const formPanel    = document.getElementById('panel-form');
const progressPanel = document.getElementById('panel-progress');
const resultsPanel = document.getElementById('panel-results');
const errorBanner  = document.getElementById('error-banner');
const errorMsg     = document.getElementById('error-msg');

const form = document.getElementById('backtest-form');
const submitBtn = document.getElementById('submit-btn');

const progressFill  = document.getElementById('progress-fill');
const progressPct   = document.getElementById('progress-pct');
const progressLabel = document.getElementById('progress-label');
const logList       = document.getElementById('log-list');

// ── 폼 제출 ───────────────────────────────────────────────────────────────────
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideError();
  tradeLog = [];

  const llmSel = document.getElementById('llm_config_id');
  const llmConfigId = llmSel && llmSel.value ? parseInt(llmSel.value) : null;
  const llmLabel = llmSel && llmSel.value
    ? llmSel.options[llmSel.selectedIndex].textContent.split('(')[0].trim()
    : 'Claude';

  const stockSel = document.getElementById('stock_select');
  const [symbol, stockName] = stockSel.value ? stockSel.value.split('|') : ['', ''];
  if (!symbol) { showError('종목을 선택하세요.'); submitBtn.disabled = false; submitBtn.textContent = '백테스트 실행'; return; }

  const data = {
    symbol:           symbol,
    stock_name:       stockName,
    start:            form.start.value,
    end:              form.end.value,
    initial_cash:     parseFloat(form.initial_cash.value),
    trader_preference: form.trader_preference.value,
    llm_config_id:    llmConfigId,
  };

  submitBtn.disabled = true;
  submitBtn.textContent = '백테스트 시작 중…';

  // 파이프라인 노드에 모델명 반영
  document.querySelectorAll('.llm-label').forEach(el => {
    el.textContent = el.textContent.replace('LLM', llmLabel);
  });

  // 즉시 진행 패널 표시 (POST 응답 전에)
  showProgressPanel(data);

  try {
    const res = await fetch('/api/backtest', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(data),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = Array.isArray(err.detail)
        ? err.detail.map(e => e.msg || JSON.stringify(e)).join(', ')
        : (err.detail || '알 수 없는 오류가 발생했습니다.');
      throw new Error(detail);
    }

    const { job_id, stream_url } = await res.json();
    currentJobId = job_id;
    connectSSE(stream_url, data);
  } catch (err) {
    showError(err.message);
    submitBtn.disabled = false;
    submitBtn.textContent = '백테스트 실행';
  }
});

// ── 일괄 실행 목록 (장바구니) ────────────────────────────────────────────────
let batchItems = [];

function readFormConfig() {
  const llmSel = document.getElementById('llm_config_id');
  const llmConfigId = llmSel && llmSel.value ? parseInt(llmSel.value) : null;
  const llmLabel = llmSel && llmSel.value
    ? llmSel.options[llmSel.selectedIndex].textContent.split('(')[0].trim()
    : '.env 기본값';
  const stockSel = document.getElementById('stock_select');
  const [symbol, stockName] = stockSel.value ? stockSel.value.split('|') : ['', ''];
  return {
    symbol,
    stock_name: stockName,
    start: form.start.value,
    end: form.end.value,
    initial_cash: parseFloat(form.initial_cash.value),
    trader_preference: form.trader_preference.value,
    llm_config_id: llmConfigId,
    _llmLabel: llmLabel,
  };
}

function renderBatchList() {
  const list = document.getElementById('batch-list');
  const cnt = document.getElementById('batch-count');
  const runBtn = document.getElementById('batch-run-btn');
  cnt.textContent = batchItems.length + '개';
  runBtn.disabled = batchItems.length === 0;
  runBtn.textContent = batchItems.length ? `일괄 실행 (${batchItems.length})` : '일괄 실행';
  if (!batchItems.length) {
    list.innerHTML = '<div class="batch-empty">목록이 비어 있습니다. 설정 후 "+ 목록에 추가"를 누르세요.</div>';
    return;
  }
  list.innerHTML = batchItems.map((it, i) => `
    <div class="batch-item">
      <span class="batch-item-stock">${escHtml(it.stock_name)} <span class="batch-item-sym">${escHtml(it.symbol)}</span></span>
      <span class="batch-item-period">${it.start} ~ ${it.end}</span>
      <span class="batch-item-llm">${escHtml(it._llmLabel)}</span>
      <button type="button" class="batch-remove" data-i="${i}" title="제거">✕</button>
    </div>`).join('');
}

document.getElementById('add-batch-btn').addEventListener('click', () => {
  const cfg = readFormConfig();
  if (!cfg.symbol) { showError('종목을 선택하세요.'); return; }
  if (!cfg.start || !cfg.end) { showError('기간을 입력하세요.'); return; }
  hideError();
  batchItems.push(cfg);
  renderBatchList();
});

document.getElementById('batch-list').addEventListener('click', (e) => {
  const btn = e.target.closest('.batch-remove');
  if (!btn) return;
  batchItems.splice(parseInt(btn.dataset.i), 1);
  renderBatchList();
});

document.getElementById('batch-run-btn').addEventListener('click', async () => {
  if (!batchItems.length) return;
  const runBtn = document.getElementById('batch-run-btn');
  runBtn.disabled = true;
  runBtn.textContent = '대기열 등록 중…';
  try {
    const items = batchItems.map(({ _llmLabel, ...rest }) => rest);
    const res = await fetch('/api/backtest/batch', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ items }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = Array.isArray(err.detail)
        ? err.detail.map(e => e.msg || JSON.stringify(e)).join(', ')
        : (err.detail || '알 수 없는 오류');
      throw new Error(detail);
    }
    const json = await res.json();
    batchItems = [];
    renderBatchList();
    alert(`${json.queued}개 백테스트가 대기열에 등록되었습니다. 순차적으로 실행됩니다.`);
    window.location.href = '/review.html';
  } catch (e) {
    showError('일괄 실행 오류: ' + e.message);
    runBtn.disabled = false;
    runBtn.textContent = `일괄 실행 (${batchItems.length})`;
  }
});

// ── SSE 연결 ──────────────────────────────────────────────────────────────────
function connectSSE(streamUrl, formData) {
  if (eventSource) eventSource.close();

  const url = streamUrl + '?token=' + encodeURIComponent(getToken());
  const es = new EventSource(url);
  eventSource = es;

  es.onmessage = (evt) => {
    let data;
    try { data = JSON.parse(evt.data); } catch { return; }

    if (data.type === 'step') {
      activatePipelineStep(data.step);
    } else if (data.type === 'progress') {
      completePipelineDay();
      handleProgress(data);
    } else if (data.type === 'done') {
      es.close();
      handleDone(data.result, formData);
    } else if (data.type === 'error') {
      es.close();
      showError(data.message);
      submitBtn.disabled = false;
      submitBtn.textContent = '백테스트 실행';
    }
  };

  es.onerror = () => {
    // 이 인스턴스(es)가 실제로 닫혔을 때만 오류 표시
    if (es.readyState === EventSource.CLOSED) {
      showError('서버 연결이 끊겼습니다. 페이지를 새로고침하여 결과를 확인하세요.');
    }
  };
}

// ── 진행 이벤트 처리 ───────────────────────────────────────────────────────────
function handleProgress(data) {
  const pct = data.pct;
  progressFill.style.width = pct + '%';
  progressPct.textContent = pct + '%';
  progressLabel.textContent = `${data.day} / ${data.total} 거래일`;

  tradeLog.push(data);

  const item = document.createElement('div');
  item.className = 'log-item';
  item.innerHTML = `
    <span class="log-date">${data.date}</span>
    <span class="badge badge-${data.action}">${data.action}</span>
    <span class="log-reason">${escHtml(data.reasoning)}</span>
  `;
  logList.appendChild(item);
  logList.scrollTop = logList.scrollHeight;
}

// ── 완료 처리 ─────────────────────────────────────────────────────────────────
async function handleDone(result, formData) {
  // 차트 파일 목록 가져오기
  try {
    const res = await fetch(`/api/backtest/${currentJobId}/chart-list`);
    const json = await res.json();
    const files = json.charts || [];
    klineFiles   = files.filter(f => f.startsWith('kline_'));
    tradingFiles = files.filter(f => f.startsWith('trading_'));
  } catch { /* 무시 */ }

  renderResults(result, formData);
  showResultsPanel();
}

// ── 결과 렌더링 ───────────────────────────────────────────────────────────────
function renderResults(r, fd) {
  // 백테스트 대상 정보
  document.getElementById('info-stock-name').textContent = fd.stock_name;
  document.getElementById('info-symbol').textContent = fd.symbol;
  document.getElementById('info-period').textContent = `${fd.start} ~ ${fd.end}`;

  // KPI 카드
  setKpi('kpi-total-return',  formatPct(r.total_return_pct),   r.total_return_pct);
  setKpi('kpi-annual-return', formatPct(r.annualized_return_pct), r.annualized_return_pct);
  setKpi('kpi-sharpe',        (r.sharpe_ratio ?? 0).toFixed(3), r.sharpe_ratio);
  setKpi('kpi-mdd',           formatPct(r.max_drawdown_pct),   r.max_drawdown_pct);
  setKpi('kpi-vol',           formatPct(r.volatility_annual_pct, false), null);
  setKpi('kpi-excess',        formatPct((r.total_return_pct ?? 0) - (r.benchmark_return_pct ?? 0)),
                               (r.total_return_pct ?? 0) - (r.benchmark_return_pct ?? 0));

  // 거래 카운트
  document.getElementById('cnt-buy').textContent  = r.buy_count  ?? 0;
  document.getElementById('cnt-sell').textContent = r.sell_count ?? 0;
  document.getElementById('cnt-hold').textContent = r.hold_count ?? 0;

  // 성과 차트
  const perfFilename = `performance_${fd.symbol}_${fd.start}_${fd.end}.png`;
  const perfImg = document.getElementById('perf-img');
  perfImg.src = `/charts/${currentJobId}/${perfFilename}`;
  perfImg.onerror = () => { perfImg.alt = '성과 차트 생성 실패'; };

  // 거래 내역 테이블
  renderTradeTable();

  // 일별 차트 브라우저
  renderChartBrowser();
}

function setKpi(id, text, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'kpi-value';
  if (value === null || value === undefined) {
    el.classList.add('neutral');
  } else if (value > 0) {
    el.classList.add('pos');
  } else if (value < 0) {
    el.classList.add('neg');
  } else {
    el.classList.add('neutral');
  }
}

function formatPct(v, withSign = true) {
  if (v === null || v === undefined) return '—';
  const sign = withSign && v > 0 ? '+' : '';
  return sign + v.toFixed(2) + '%';
}

function renderTradeTable() {
  const tbody = document.getElementById('trade-tbody');
  tbody.innerHTML = '';

  // BUY/SELL만 표시 (HOLD는 reason 없으면 의미 없음), 최신 순
  const trades = [...tradeLog].reverse().filter(t => t.action !== 'HOLD');

  if (trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--text-muted)">거래 내역 없음</td></tr>';
    return;
  }

  trades.forEach(t => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${t.date}</td>
      <td><span class="badge badge-${t.action}">${t.action}</span></td>
      <td class="td-reason">${escHtml(t.reasoning)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ── 일별 차트 브라우저 ────────────────────────────────────────────────────────
let chartType = 'kline';

function renderChartBrowser() {
  const browser = document.getElementById('chart-browser');
  browser.style.display = 'block';

  const dateSelect = document.getElementById('chart-date-select');
  dateSelect.innerHTML = '';

  const updateOptions = () => {
    const files = chartType === 'kline' ? klineFiles : tradingFiles;
    dateSelect.innerHTML = '';
    files.forEach(f => {
      // kline_005930_2024-01-02.png → 2024-01-02
      const parts = f.replace('.png', '').split('_');
      const dateStr = parts.slice(2).join('-');
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = dateStr;
      dateSelect.appendChild(opt);
    });
    showChart();
  };

  document.getElementById('tab-kline').addEventListener('click', () => {
    chartType = 'kline';
    document.getElementById('tab-kline').classList.add('active');
    document.getElementById('tab-trading').classList.remove('active');
    updateOptions();
  });
  document.getElementById('tab-trading').addEventListener('click', () => {
    chartType = 'trading';
    document.getElementById('tab-trading').classList.add('active');
    document.getElementById('tab-kline').classList.remove('active');
    updateOptions();
  });
  dateSelect.addEventListener('change', showChart);

  updateOptions();
}

function showChart() {
  const dateSelect = document.getElementById('chart-date-select');
  const chartImg = document.getElementById('daily-chart-img');
  const noChart  = document.getElementById('no-chart-msg');

  const filename = dateSelect.value;
  if (!filename) {
    chartImg.style.display = 'none';
    noChart.style.display = 'block';
    return;
  }
  chartImg.src = `/charts/${currentJobId}/${filename}`;
  chartImg.style.display = 'block';
  noChart.style.display = 'none';
}

// ── 패널 전환 ─────────────────────────────────────────────────────────────────
function showProgressPanel(fd) {
  formPanel.style.display = 'none';
  progressPanel.style.display = 'block';
  resultsPanel.style.display = 'none';
  logList.innerHTML = '';
  progressFill.style.width = '0%';
  progressPct.textContent = '0%';
  progressLabel.textContent = '준비 중…';
  resetPipeline();
  const info = document.getElementById('progress-run-info');
  if (info) {
    if (fd && fd.stock_name) {
      info.textContent = `${fd.stock_name} (${fd.symbol})  ${fd.start} ~ ${fd.end}`;
    } else {
      info.textContent = '';
    }
  }
}

function showResultsPanel() {
  progressPanel.style.display = 'none';
  resultsPanel.style.display = 'block';
  submitBtn.disabled = false;
  submitBtn.textContent = '백테스트 실행';
}

function showError(msg) {
  errorBanner.style.display = 'block';
  errorMsg.textContent = msg;
  formPanel.style.display = 'block';
  progressPanel.style.display = 'none';
}

function hideError() {
  errorBanner.style.display = 'none';
}

// ── 재실행 resume 처리 (review 페이지에서 넘어온 경우) ───────────────────────
(function checkResumeParam() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get('resume_job');
  const streamUrl = params.get('resume_stream');
  if (!jobId || !streamUrl) return;
  history.replaceState({}, '', '/');
  currentJobId = jobId;
  tradeLog = [];
  const formData = {
    stock_name: params.get('resume_stock') || '',
    symbol:     params.get('resume_symbol') || '',
    start:      params.get('resume_start') || '',
    end:        params.get('resume_end') || '',
  };
  const resumeRunId = params.get('resume_run_id');
  showProgressPanel(formData);
  if (resumeRunId) {
    fetch(`/review/api/runs/${resumeRunId}/days`)
      .then(r => r.json())
      .then(days => {
        days.forEach(d => tradeLog.push({ date: d.date, action: d.action, reasoning: d.reasoning || '' }));
        renderTradeTable();
      })
      .catch(() => {});
  }
  connectSSE(streamUrl, formData);
})();

// ── 새 백테스트 버튼 ──────────────────────────────────────────────────────────
document.getElementById('btn-new').addEventListener('click', () => {
  if (eventSource) eventSource.close();
  currentJobId = null;
  tradeLog = [];
  klineFiles = [];
  tradingFiles = [];
  resultsPanel.style.display = 'none';
  progressPanel.style.display = 'none';
  formPanel.style.display = 'block';
  document.getElementById('chart-browser').style.display = 'none';
  hideError();
});

// ── 유틸 ─────────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
