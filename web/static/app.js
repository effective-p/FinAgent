/* FinAgent Web UI — SSE 클라이언트 + DOM 조작 */
'use strict';

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

  const data = {
    symbol:           form.symbol.value.trim(),
    stock_name:       form.stock_name.value.trim(),
    start:            form.start.value,
    end:              form.end.value,
    initial_cash:     parseFloat(form.initial_cash.value),
    trader_preference: form.trader_preference.value,
  };

  submitBtn.disabled = true;
  submitBtn.textContent = '백테스트 시작 중…';

  try {
    const res = await fetch('/api/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '알 수 없는 오류가 발생했습니다.');
    }

    const { job_id, stream_url } = await res.json();
    currentJobId = job_id;

    showProgressPanel();
    connectSSE(stream_url, data);
  } catch (err) {
    showError(err.message);
    submitBtn.disabled = false;
    submitBtn.textContent = '백테스트 실행';
  }
});

// ── SSE 연결 ──────────────────────────────────────────────────────────────────
function connectSSE(streamUrl, formData) {
  if (eventSource) eventSource.close();

  eventSource = new EventSource(streamUrl);

  eventSource.onmessage = (evt) => {
    let data;
    try { data = JSON.parse(evt.data); } catch { return; }

    if (data.type === 'progress') {
      handleProgress(data);
    } else if (data.type === 'done') {
      eventSource.close();
      handleDone(data.result, formData);
    } else if (data.type === 'error') {
      eventSource.close();
      showError(data.message);
      submitBtn.disabled = false;
      submitBtn.textContent = '백테스트 실행';
    }
  };

  eventSource.onerror = () => {
    // EventSource 자동 재연결 — 명시적 종료 아니면 재연결 허용
    if (eventSource.readyState === EventSource.CLOSED) {
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
function showProgressPanel() {
  formPanel.style.display = 'none';
  progressPanel.style.display = 'block';
  resultsPanel.style.display = 'none';
  logList.innerHTML = '';
  progressFill.style.width = '0%';
  progressPct.textContent = '0%';
  progressLabel.textContent = '준비 중…';
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
