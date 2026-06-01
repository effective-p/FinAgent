/* FinAgent Review UI — review.js */
'use strict';

// ── 상태 ──────────────────────────────────────────────
const STATE = {
  runs: [],
  selectedRunId: null,
  days: [],
  filterAction: 'all',
  compareMode: false,
  selectedCompareIds: new Set(),
  compareChart: null,
  expandedGroups: new Set(),   // 사이드바에서 펼쳐진 종목(symbol) 집합 — 기본은 모두 접힘
};

// 종목별 시장 구분 (KRX) — 현재 운용 5종목
const MARKETS = {
  '005930': 'KOSPI',   // 삼성전자
  '005380': 'KOSPI',   // 현대차
  '034020': 'KOSPI',   // 두산에너빌리티
  '298380': 'KOSDAQ',  // 에이비엘바이오
  '058470': 'KOSDAQ',  // 리노공업
};

// ── DOM refs ───────────────────────────────────────────
const $ = id => document.getElementById(id);
const runList       = $('rv-run-list');
const welcome       = $('rv-welcome');
const detail        = $('rv-run-detail');
const compareView   = $('rv-compare-view');
const searchInput   = $('rv-search');
const drawer        = $('rv-drawer');
const drawerOverlay = $('rv-drawer-overlay');
const drawerBody    = $('rv-drawer-body');
const drawerTitle   = $('rv-drawer-title');
const modal         = $('rv-modal');
const modalOverlay  = $('rv-modal-overlay');
const modalTitle    = $('rv-modal-title');
const modalMeta     = $('rv-modal-meta');
const modalBody     = $('rv-modal-body');

// ── 초기화 ──────────────────────────────────────────────
async function init() {
  await loadRuns();
  bindEvents();
  scheduleAutoRefresh();
}

async function loadRuns() {
  try {
    const res = await fetch('/review/api/runs');
    STATE.runs = await res.json();
    renderRunList();
  } catch (e) {
    runList.innerHTML = '<div class="rv-run-empty">실행 이력을 불러올 수 없습니다.</div>';
  }
}

function scheduleAutoRefresh() {
  setTimeout(async () => {
    await loadRuns();
    if (STATE.runs.some(r => r.status === 'running' || r.status === 'queued')) {
      scheduleAutoRefresh();
    }
  }, 5000);
}

// ── 사이드바 ────────────────────────────────────────────
function renderRunList() {
  const query = searchInput.value.trim().toLowerCase();
  const filtered = STATE.runs.filter(r =>
    !query || r.symbol.toLowerCase().includes(query) || r.stock_name.includes(query)
  );

  if (!filtered.length) {
    runList.innerHTML = '<div class="rv-run-empty">일치하는 결과 없음</div>';
    return;
  }

  // 종목별 그룹핑 (입력 순서 = 최신순 유지)
  const groups = new Map();
  filtered.forEach(r => {
    if (!groups.has(r.symbol)) {
      groups.set(r.symbol, { stock_name: r.stock_name, items: [] });
    }
    groups.get(r.symbol).items.push(r);
  });

  // 검색 중에는 모든 그룹을 펼쳐서 매치 결과를 즉시 보이게
  const searching = !!query;

  runList.innerHTML = [...groups.entries()].map(([symbol, g]) => {
    const collapsed = !searching && !STATE.expandedGroups.has(symbol);
    const itemsHtml = g.items.map(renderRunItem).join('');
    const runningCnt = g.items.filter(r => r.status === 'running').length;
    const queuedCnt  = g.items.filter(r => r.status === 'queued').length;
    const market = MARKETS[symbol] || '';
    const marketCls = market === 'KOSPI' ? 'rv-market-kospi' : market === 'KOSDAQ' ? 'rv-market-kosdaq' : '';
    const activeBadge = runningCnt > 0
      ? `<span class="rv-group-active running" title="실행중 ${runningCnt}건"><span class="rv-spinner-sm"></span>${runningCnt}</span>`
      : queuedCnt > 0
        ? `<span class="rv-group-active queued" title="대기중 ${queuedCnt}건">⏳ ${queuedCnt}</span>`
        : '';
    return `
      <div class="rv-group ${collapsed ? 'collapsed' : ''}">
        <div class="rv-group-header" data-symbol="${esc(symbol)}">
          <span class="rv-group-arrow">▼</span>
          <span class="rv-group-title">${esc(g.stock_name)}</span>
          <span class="rv-group-symbol">${esc(symbol)}</span>
          ${market ? `<span class="rv-group-market ${marketCls}">${market}</span>` : ''}
          ${activeBadge}
          <span class="rv-group-count">${g.items.length}건</span>
        </div>
        <div class="rv-group-items">${itemsHtml}</div>
      </div>`;
  }).join('');
}

function renderRunItem(r) {
  const ret = r.total_return_pct;
  const retClass = ret == null ? '' : ret >= 0 ? 'pos' : 'neg';
  const retStr = ret == null ? '—' : (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%';
  const bh = r.benchmark_return_pct;
  const bhStr = bh == null ? '' : `B&H ${bh >= 0 ? '+' : ''}${bh.toFixed(2)}%`;
  const active = STATE.selectedRunId === r.id ? 'active' : '';
  const checked = STATE.selectedCompareIds.has(r.id) ? 'checked' : '';
  const llmStr = r.model ? esc(r.model) : (r.provider ? esc(r.provider) : 'gpt-4o-mini');
  return `
    <div class="rv-run-item ${active}" data-id="${r.id}">
      <div class="rv-run-item-compare ${checked}" data-id="${r.id}">✓</div>
      <div class="rv-run-item-content">
        <div class="rv-run-item-top">
          <span class="rv-run-item-period-top">${r.start_date} ~ ${r.end_date}</span>
          <span class="rv-run-item-status ${r.status}">${statusLabel(r.status)}</span>
        </div>
        <div class="rv-run-item-kpi">
          <span class="rv-run-item-ret ${retClass}">${retStr}</span>
          ${bhStr ? `<span style="color:var(--text-muted)">${bhStr}</span>` : ''}
          <span class="rv-run-item-llm">${llmStr}</span>
        </div>
      </div>
    </div>`;
}

function statusLabel(s) {
  if (s === 'running') return '<span class="rv-spinner-sm"></span>실행중';
  return { done: '완료', error: '오류', queued: '대기중' }[s] || s;
}

// ── 이벤트 ──────────────────────────────────────────────
function bindEvents() {
  runList.addEventListener('click', e => {
    const header = e.target.closest('.rv-group-header');
    if (header) {
      const sym = header.dataset.symbol;
      if (STATE.expandedGroups.has(sym)) STATE.expandedGroups.delete(sym);
      else STATE.expandedGroups.add(sym);
      renderRunList();
      return;
    }
    const item = e.target.closest('.rv-run-item');
    if (!item) return;
    const id = item.dataset.id;
    if (e.target.closest('.rv-run-item-compare')) {
      e.stopPropagation();
      toggleCompareSelect(id);
      return;
    }
    if (STATE.compareMode) {
      toggleCompareSelect(id);
    } else {
      selectRun(id);
    }
  });

  searchInput.addEventListener('input', renderRunList);

  $('rv-btn-toggle-compare').addEventListener('click', () => {
    STATE.compareMode = !STATE.compareMode;
    STATE.selectedCompareIds.clear();
    document.querySelector('.rv-sidebar').classList.toggle('rv-compare-mode', STATE.compareMode);
    $('rv-compare-bar').style.display = STATE.compareMode ? 'flex' : 'none';
    updateCompareBar();
    renderRunList();
  });

  $('rv-btn-compare-cancel').addEventListener('click', () => {
    STATE.compareMode = false;
    STATE.selectedCompareIds.clear();
    document.querySelector('.rv-sidebar').classList.remove('rv-compare-mode');
    $('rv-compare-bar').style.display = 'none';
    renderRunList();
  });

  $('rv-btn-compare').addEventListener('click', runCompare);

  document.querySelector('.rv-day-filter').addEventListener('click', e => {
    const btn = e.target.closest('.rv-filter-btn');
    if (!btn) return;
    document.querySelectorAll('.rv-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    STATE.filterAction = btn.dataset.filter;
    renderDayTable();
  });

  $('rv-drawer-close').addEventListener('click', closeDrawer);
  drawerOverlay.addEventListener('click', closeDrawer);
  $('rv-modal-close').addEventListener('click', closeModal);
  modalOverlay.addEventListener('click', closeModal);

  $('rv-formula-toggle').addEventListener('click', () => {
    const body = $('rv-formula-body');
    const icon = $('rv-formula-toggle').querySelector('.rv-toggle-icon');
    const hidden = body.style.display === 'none';
    body.style.display = hidden ? '' : 'none';
    icon.style.transform = hidden ? '' : 'rotate(-90deg)';
  });

  // 일별 거래 내역 카드 — 접기/펼치기
  $('rv-day-toggle').addEventListener('click', () => {
    const body = $('rv-day-body');
    const icon = $('rv-day-toggle').querySelector('.rv-toggle-icon');
    const hidden = body.style.display === 'none';
    body.style.display = hidden ? '' : 'none';
    icon.style.transform = hidden ? '' : 'rotate(-90deg)';
  });

  // 종합 분석 카드 — 토글 + AI 버튼들
  $('rv-analysis-toggle').addEventListener('click', () => {
    const body = $('rv-analysis-body');
    const icon = $('rv-analysis-toggle').querySelector('.rv-toggle-icon');
    const hidden = body.style.display === 'none';
    body.style.display = hidden ? '' : 'none';
    icon.style.transform = hidden ? '' : 'rotate(-90deg)';
  });
  $('rv-btn-analyze').addEventListener('click', () => requestAnalysis(false));
  $('rv-btn-analyze-regen').addEventListener('click', () => {
    if (confirm('기존 분석과 토론 내역을 삭제하고 다시 생성합니다. 계속할까요?')) requestAnalysis(true);
  });
  $('rv-btn-ask').addEventListener('click', sendQuestion);
  $('rv-analysis-ask-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendQuestion(); }
  });

  // 비교 종합 분석 카드 — 토글 + AI 버튼들
  $('rv-cmp-analysis-toggle').addEventListener('click', () => {
    const body = $('rv-cmp-analysis-body');
    const icon = $('rv-cmp-analysis-toggle').querySelector('.rv-toggle-icon');
    const hidden = body.style.display === 'none';
    body.style.display = hidden ? '' : 'none';
    icon.style.transform = hidden ? '' : 'rotate(-90deg)';
  });
  $('rv-btn-cmp-analyze').addEventListener('click', () => requestCompareAnalysis(false));
  $('rv-btn-cmp-analyze-regen').addEventListener('click', () => {
    if (confirm('기존 비교 분석과 토론 내역을 삭제하고 다시 생성합니다. 계속할까요?')) requestCompareAnalysis(true);
  });
  $('rv-btn-cmp-ask').addEventListener('click', sendCompareQuestion);
  $('rv-cmp-ask-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendCompareQuestion(); }
  });
}

function toggleCompareSelect(id) {
  if (STATE.selectedCompareIds.has(id)) {
    STATE.selectedCompareIds.delete(id);
  } else {
    STATE.selectedCompareIds.add(id);
  }
  updateCompareBar();
  renderRunList();
}

function updateCompareBar() {
  $('rv-compare-count').textContent = `${STATE.selectedCompareIds.size}개 선택`;
  $('rv-btn-compare').disabled = STATE.selectedCompareIds.size < 2;
}

// ── 실행 상세 ────────────────────────────────────────────
async function selectRun(runId) {
  STATE.selectedRunId = runId;
  renderRunList();
  welcome.style.display = 'none';
  compareView.style.display = 'none';
  detail.style.display = 'none';
  $('rv-kpi-grid').innerHTML = '';

  try {
    const [run, days] = await Promise.all([
      fetch(`/review/api/runs/${runId}`).then(r => r.json()),
      fetch(`/review/api/runs/${runId}/days`).then(r => r.json()),
    ]);
    STATE.days = days;
    STATE.selectedRun = run;
    renderDetail(run);
    renderDayTable();
    renderAnalysis(run, days);
    loadAnalysisThread(runId);
    detail.style.display = '';
    loadPerfChart(runId);
  } catch (e) {
    detail.innerHTML = `<div class="rv-run-empty">오류: ${esc(String(e))}</div>`;
    detail.style.display = '';
  }
}

function renderDetail(run) {
  $('rv-d-stock').textContent = run.stock_name;
  $('rv-d-symbol').textContent = run.symbol;
  const badge = $('rv-d-status');
  badge.innerHTML = statusLabel(run.status);
  badge.className = `rv-run-badge ${run.status}`;
  const isOwner = run.username === localStorage.getItem('fa_user');
  if (run.status === 'running' && isOwner) {
    badge.title = '클릭하면 진행 화면으로 이동';
    badge.style.cursor = 'pointer';
    badge.onclick = () => resumeRun(run.id);
  } else {
    badge.style.cursor = '';
    badge.onclick = null;
    badge.title = '';
  }
  $('rv-d-period').textContent = `📅 ${run.start_date} ~ ${run.end_date}`;
  $('rv-d-cash').textContent = `💰 ${fmt0(run.initial_cash)}원`;
  $('rv-d-pref').textContent = `🎯 ${prefLabel(run.trader_preference)}`;
  $('rv-d-author').textContent = run.username ? `👤 ${run.username}` : '';
  $('rv-d-created').textContent = formatRuntime(run.created_at, run.finished_at);
  $('rv-d-model').textContent = run.model
    ? `🤖 ${run.model}`
    : (run.provider ? `🤖 ${run.provider}` : '');
  const runid = $('rv-d-runid');
  if (runid) {
    runid.textContent = '#' + String(run.id).slice(0, 8);
    runid.title = run.id;
  }
  renderKPI(run.result || {});

  const resumeBtn = $('rv-btn-resume');
  if (run.status === 'error' || run.status === 'running') {
    resumeBtn.style.display = '';
    resumeBtn.disabled = !isOwner;
    resumeBtn.title = isOwner ? '' : '본인의 실행만 재실행할 수 있습니다.';
    resumeBtn.textContent = '▶ 이어서 실행';
    resumeBtn.onclick = isOwner ? () => resumeRun(run.id) : null;
  } else {
    resumeBtn.style.display = 'none';
  }

  // 삭제: 본인 여부 무관하게 활성 (요청에 따라)
  const deleteBtn = $('rv-btn-delete');
  deleteBtn.style.display = '';
  deleteBtn.disabled = false;
  const queued = run.status === 'queued';
  deleteBtn.textContent = queued ? '⏏ 실행 대기 취소' : '🗑 삭제';
  deleteBtn.title = queued ? '대기열에서 제거합니다.' : '';
  deleteBtn.onclick = () => deleteRun(run.id, run.stock_name, run.start_date, run.end_date, queued);
}

function formatRuntime(startIso, endIso) {
  if (!startIso) return '';
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : null;
  const fmt = d => {
    const mo = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${d.getFullYear()}-${mo}-${day} ${hh}:${mm}`;
  };
  if (!end) return `🗓️ ${fmt(start)} ~ (진행중)`;
  const mins = Math.max(1, Math.round((end - start) / 60000));
  const dur = mins >= 60 ? `${Math.floor(mins / 60)}시간 ${mins % 60}분` : `${mins}분`;
  return `🗓️ ${fmt(start)} ~ ${fmt(end)} (소요 ${dur})`;
}

async function deleteRun(runId, stockName, startDate, endDate, queued) {
  const msg = queued
    ? `실행 대기 중인 백테스트를 취소하시겠습니까?\n\n${stockName}  ${startDate} ~ ${endDate}`
    : `삭제하시겠습니까?\n\n${stockName}  ${startDate} ~ ${endDate}\n\n이 작업은 되돌릴 수 없습니다.`;
  const confirmed = confirm(msg);
  if (!confirmed) return;

  try {
    const token = localStorage.getItem('fa_token');
    const res = await fetch(`/review/api/runs/${runId}`, {
      method: 'DELETE',
      headers: { 'Authorization': 'Bearer ' + token },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '삭제 실패');
    }
    // 목록에서 제거하고 상세 패널 닫기
    STATE.runs = STATE.runs.filter(r => r.id !== runId);
    STATE.selectedRunId = null;
    renderRunList();
    $('rv-run-detail').style.display = 'none';
    $('rv-welcome').style.display = '';
  } catch (e) {
    alert('삭제 오류: ' + e.message);
  }
}

async function resumeRun(runId) {
  const btn = $('rv-btn-resume');
  btn.disabled = true;
  btn.textContent = '실행 중…';
  const run = STATE.runs.find(r => r.id === runId);
  try {
    const token = localStorage.getItem('fa_token');
    const res = await fetch(`/api/backtest/${runId}/resume`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '재실행 실패');
    }
    const { job_id, stream_url } = await res.json();
    const params = new URLSearchParams({
      resume_job: job_id,
      resume_stream: stream_url,
      resume_run_id: runId,
      ...(run ? {
        resume_stock: run.stock_name,
        resume_symbol: run.symbol,
        resume_start: run.start_date,
        resume_end: run.end_date,
      } : {}),
    });
    window.location.href = '/?' + params.toString();
  } catch (e) {
    alert('재실행 오류: ' + e.message);
    btn.disabled = false;
    btn.textContent = '▶ 이어서 실행';
  }
}

function prefLabel(p) {
  return { aggressive: '공격적', moderate: '중립', conservative: '보수적' }[p] || p;
}

function renderKPI(res) {
  const grid = $('rv-kpi-grid');
  grid.innerHTML = '';
  // 상단 4개: 수익·리스크 절대값 (베이스라인 비교 한 줄에)
  // 하단 4개: 위험 조정·통계 지표 한 줄
  const kpis = [
    { label: '총 수익률', key: 'total_return_pct', fmt: pct, sign: true,
      tip: '(final − initial) / initial × 100\n전체 기간 누적 수익률.' },
    { label: '연간 수익률', key: 'annualized_return_pct', fmt: pct, sign: true,
      tip: '(final / initial)^(1/years) − 1\nyears = n_days / 252\n연환산(annualized) 수익률.' },
    { label: 'B&H 초과수익', key: '_excess', fmt: pct, sign: true,
      tip: 'total_return − benchmark_return\n같은 기간 Buy&Hold 대비 만든 알파(α).' },
    { label: '최대 낙폭 MDD', key: 'max_drawdown_pct', fmt: pct, sign: true,
      tip: 'min((equity − cummax(equity)) / cummax(equity))\n자산곡선 최고점 대비 최악의 손실폭.' },
    { label: 'Sharpe Ratio', key: 'sharpe_ratio', fmt: v => v.toFixed(3),
      tip: '(E[r] − rf) / σ × √252\nrf = 3%/year\n위험 1단위당 초과수익. ≥1 양호, ≥2 우수.' },
    { label: 'Sortino Ratio', key: 'sortino_ratio', fmt: v => v.toFixed(3),
      tip: 'annualized_return / downside_std × 100\n하방 변동성만 페널티로 보는 Sharpe 변형.' },
    { label: 'Calmar Ratio', key: 'calmar_ratio', fmt: v => v.toFixed(3),
      tip: 'annualized_return / |MDD|\nMDD 1단위당 연환산 수익.' },
    { label: '연간 변동성', key: 'volatility_annual_pct', fmt: v => v.toFixed(2) + '%',
      tip: 'std(daily_returns) × √252 × 100\n수익률의 연환산 표준편차.' },
  ];

  kpis.forEach(k => {
    let val;
    if (k.key === '_excess') {
      val = (res.total_return_pct != null && res.benchmark_return_pct != null)
        ? res.total_return_pct - res.benchmark_return_pct : null;
    } else {
      val = res[k.key];
    }
    const valStr = val == null ? '—' : k.fmt(val);
    const cls = val == null ? 'neutral' : (k.sign && val > 0 ? 'pos' : k.sign && val < 0 ? 'neg' : 'neutral');
    const card = document.createElement('div');
    card.className = 'rv-kpi-card';
    const help = k.tip ? `<span class="rv-kpi-help" data-tip="${esc(k.tip)}">?</span>` : '';
    card.innerHTML = `<div class="rv-kpi-label">${k.label}${help}</div><div class="rv-kpi-value ${cls}">${valStr}</div>`;
    grid.appendChild(card);
  });

  $('rv-trade-counts').innerHTML = `
    <div class="rv-tc-item"><div class="rv-tc-num buy">${res.buy_count ?? 0}</div><div class="rv-tc-lbl">매수 BUY</div></div>
    <div class="rv-tc-item"><div class="rv-tc-num sell">${res.sell_count ?? 0}</div><div class="rv-tc-lbl">매도 SELL</div></div>
    <div class="rv-tc-item"><div class="rv-tc-num hold">${res.hold_count ?? 0}</div><div class="rv-tc-lbl">보유 HOLD</div></div>
  `;
}

function loadPerfChart(runId) {
  const img = $('rv-perf-img');
  const noChart = $('rv-no-perf-chart');
  img.onload = () => { img.style.display = ''; noChart.style.display = 'none'; };
  img.onerror = () => { img.style.display = 'none'; noChart.style.display = ''; };
  img.src = `/review/api/runs/${runId}/perf-chart?t=${Date.now()}`;
}

// ── 일별 테이블 ──────────────────────────────────────────
function renderDayTable() {
  const tbody = $('rv-day-tbody');
  const days = STATE.filterAction === 'all'
    ? STATE.days
    : STATE.days.filter(d => d.action === STATE.filterAction);

  if (!days.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">해당 거래 없음</td></tr>';
    return;
  }

  tbody.innerHTML = days.map(d => {
    const traceBtn = d.has_trace
      ? `<button class="rv-btn-detail" data-date="${d.date}">🔍 워크플로우</button>`
      : `<button class="rv-btn-detail no-trace" disabled>트레이스 없음</button>`;
    return `<tr>
      <td>${d.date}</td>
      <td><span class="rv-action-badge ${d.action}">${d.action}</span></td>
      <td style="white-space:nowrap">${fmt0(d.price)}원</td>
      <td>${d.quantity > 0 ? Math.round(d.quantity) + '주' : '—'}</td>
      <td class="rv-reasoning">${(() => { const r = cleanReasoning(d.reasoning); return esc(r.slice(0, 120)) + (r.length > 120 ? '…' : ''); })()}</td>
      <td>${traceBtn}</td>
    </tr>`;
  }).join('');

  tbody.querySelectorAll('.rv-btn-detail:not(.no-trace)').forEach(btn => {
    btn.addEventListener('click', () => openWorkflow(STATE.selectedRunId, btn.dataset.date));
  });
}

// ── 워크플로우 드로어 ────────────────────────────────────
async function openWorkflow(runId, dateStr) {
  drawerTitle.textContent = `워크플로우 — ${dateStr}`;
  drawerBody.innerHTML = '<div class="rv-loading">로딩 중…</div>';
  openDrawer();

  try {
    const trace = await fetch(`/review/api/runs/${runId}/days/${dateStr}`).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    renderWorkflow(runId, dateStr, trace);
  } catch (e) {
    drawerBody.innerHTML = `<div class="rv-loading">트레이스를 불러올 수 없습니다: ${esc(String(e))}</div>`;
  }
}

function renderWorkflow(runId, dateStr, trace) {
  const steps = trace.steps || {};
  const exec = steps.trade_execution || {};
  const actionBadge = exec.action
    ? `<span class="rv-action-badge ${exec.action}" style="font-size:12px;padding:3px 10px">${exec.action}</span>`
    : '';
  drawerTitle.innerHTML = `워크플로우 — ${dateStr} &nbsp;${actionBadge}${exec.price ? ` @ ${fmt0(exec.price)}원` : ''}`;

  const STEP_DEFS = [
    { key: 'news_fetch',            num: 1, icon: '📰', title: '데이터 수집',            type: 'api',  badge: 'API / RSS' },
    { key: 'market_intelligence',   num: 2, icon: '🧠', title: 'Market Intelligence',   type: 'ai',   badge: 'Claude' },
    { key: 'low_level_reflection',  num: 3, icon: '🤖', title: 'Low-Level Reflection',  type: 'ai',   badge: 'Claude Vision' },
    { key: 'high_level_reflection', num: 4, icon: '🤖', title: 'High-Level Reflection', type: 'ai',   badge: 'Claude Vision' },
    { key: 'decision_making',       num: 5, icon: '⚡',  title: 'Decision Making',       type: 'ai',   badge: 'Claude + Tools' },
    { key: 'trade_execution',       num: 6, icon: '🛢',  title: '거래 실행',              type: 'exec', badge: 'Portfolio DB' },
  ];

  drawerBody.innerHTML = STEP_DEFS.map((def, idx) => {
    const stepData = steps[def.key];
    const bodyHtml = stepData ? buildStepBody(def.key, stepData, runId, dateStr) : '';
    const noData = !stepData ? '<span style="color:var(--text-muted);font-size:11px;margin-left:8px">데이터 없음</span>' : '';
    // 결정 단계는 기본으로 열기
    const openClass = (idx === 0 || def.key === 'trade_execution') ? 'open' : '';
    return `
      <div class="rv-wf-step type-${def.type} ${openClass}" id="wf-step-${def.key}">
        <div class="rv-wf-step-header" data-key="${def.key}">
          <div class="rv-wf-step-num">${def.num}</div>
          <span class="rv-wf-step-icon">${def.icon}</span>
          <span class="rv-wf-step-title">${def.title}</span>
          <span class="rv-wf-step-badge">${def.badge}</span>
          ${noData}
          ${stepData ? '<span class="rv-wf-step-toggle">▼</span>' : ''}
        </div>
        ${stepData ? `<div class="rv-wf-step-body">${bodyHtml}</div>` : ''}
      </div>`;
  }).join('');

  drawerBody.querySelectorAll('.rv-wf-step-header[data-key]').forEach(h => {
    h.addEventListener('click', () => {
      const el = document.getElementById(`wf-step-${h.dataset.key}`);
      if (el) el.classList.toggle('open');
    });
  });
}

function buildStepBody(key, data, runId, dateStr) {
  const parts = [];
  const calls = data.llm_calls || [];

  if (calls.length > 0) {
    const safeJson = JSON.stringify(calls).replace(/'/g, "\\'");
    parts.push(`<button class="rv-btn-llm" onclick='showLLMCalls(${JSON.stringify(JSON.stringify(calls))}, "${key}")'>
      🔮 LLM 호출 내용 보기 (${calls.length}건)
    </button>`);
  }

  if (key === 'news_fetch') {
    const news = data.news || [];
    parts.push(section('수집 요약', `뉴스 ${data.news_count ?? news.length}건`));
    if (news.length) {
      parts.push(`<div class="rv-step-section"><div class="rv-step-label">뉴스 목록</div>
        <div class="rv-step-value" style="max-height:300px">${
          news.map(n => `<div class="rv-news-item">
            <div class="rv-news-date">${(n.published || '').slice(0,10)}</div>
            <div class="rv-news-title">${esc(n.title || '')}</div>
            <div class="rv-news-summary">${esc((n.summary || '').slice(0,200))}</div>
          </div>`).join('')
        }</div></div>`);
    }
    if (data.investor_data) parts.push(section('투자자 동향', data.investor_data));
    if (data.fundamental_guidance) parts.push(section('펀더멘탈 가이던스', data.fundamental_guidance));
    parts.push(`<div class="rv-step-section"><div class="rv-step-label">Kline 차트 (캔들)</div>
      <div class="rv-chart-thumb"><img src="/review/api/runs/${runId}/days/${dateStr}/chart/kline" loading="lazy" alt="Kline" onerror="this.parentNode.innerHTML='<span style=color:var(--text-muted);font-size:11px>차트 없음</span>'" /></div>
    </div>`);
    parts.push(`<div class="rv-step-section"><div class="rv-step-label">Trading 차트 (매매 마커)</div>
      <div class="rv-chart-thumb"><img src="/review/api/runs/${runId}/days/${dateStr}/chart/trading" loading="lazy" alt="Trading" onerror="this.parentNode.innerHTML='<span style=color:var(--text-muted);font-size:11px>차트 없음</span>'" /></div>
    </div>`);
  }

  if (key === 'market_intelligence') {
    const out = data.output || {};
    parts.push(section('최신 분석', out.latest_summary));
    parts.push(section('과거 패턴 참조', out.past_summary));
    if (out.short_term_query || out.medium_term_query || out.long_term_query) {
      parts.push(`<div class="rv-step-section"><div class="rv-step-label">Retrieval 쿼리 (단기/중기/장기)</div>
        <div class="rv-step-value">${
          [[out.short_term_query,'단기'],[out.medium_term_query,'중기'],[out.long_term_query,'장기']]
            .filter(([q]) => q)
            .map(([q,l]) => `[${l}] ${esc(q)}`).join('\n')
        }</div></div>`);
    }
  }

  if (key === 'low_level_reflection') {
    const out = data.output || {};
    parts.push(section('단기 분석 (1-5일)', cleanReasoning(out.short_term_reasoning)));
    parts.push(section('중기 분석 (1-4주)', cleanReasoning(out.medium_term_reasoning)));
    parts.push(section('장기 분석 (1-3개월)', cleanReasoning(out.long_term_reasoning)));
  }

  if (key === 'high_level_reflection') {
    const out = data.output || {};
    parts.push(section('과거 결정 평가', cleanReasoning(out.reasoning)));
    parts.push(section('개선 방안', cleanReasoning(out.improvement)));
    parts.push(section('핵심 요약 (메모리 저장)', cleanReasoning(out.summary)));
  }

  if (key === 'decision_making') {
    if (data.technical_signals) {
      const lines = data.technical_signals.split('\n').filter(Boolean);
      const linesHtml = lines.map(l => {
        const cls = l.includes('BUY') ? 'buy' : l.includes('SELL') ? 'sell' : 'hold';
        return `<div class="rv-tech-signal-line ${cls}">${esc(l)}</div>`;
      }).join('');
      parts.push(`<div class="rv-step-section"><div class="rv-step-label">기술적 지표 시그널</div>
        <div class="rv-step-value">${linesHtml}</div></div>`);
    }

    const ps = data.portfolio_state || {};
    parts.push(`<div class="rv-step-section"><div class="rv-step-label">포트폴리오 상태 (결정 시점)</div>
      <div class="rv-step-grid">
        ${kv('현금', fmt0(ps.cash) + '원')}
        ${kv('보유 수량', ps.position != null ? Math.round(ps.position) + '주' : '—')}
        ${kv('총 자산', fmt0(ps.total_value) + '원')}
      </div></div>`);

    const out = data.output || {};
    if (out.analysis) parts.push(section('단계별 분석', cleanReasoning(out.analysis)));
    parts.push(section('결정 근거', cleanReasoning(out.reasoning)));

    if (out.action) {
      const ac = out.action;
      const acCls = ac === 'BUY' ? 'green' : ac === 'SELL' ? 'red' : '';
      parts.push(`<div class="rv-step-section"><div class="rv-step-label">최종 결정</div>
        <div class="rv-step-kv">
          <div class="rv-step-kv-key">Action</div>
          <div class="rv-step-kv-val ${acCls}" style="font-size:18px;font-weight:700">
            ${ac}
          </div>
        </div></div>`);
    }
  }

  if (key === 'trade_execution') {
    const acCls = data.action === 'BUY' ? 'green' : data.action === 'SELL' ? 'red' : '';
    parts.push(`<div class="rv-step-section"><div class="rv-step-label">실행 내역</div>
      <div class="rv-step-grid">
        ${kv('액션', `<span class="rv-step-kv-val ${acCls}">${data.action}</span>`)}
        ${kv('체결가', fmt0(data.price) + '원')}
        ${data.position_after !== data.position_before
          ? kv('수량 변화', `${Math.round(data.position_before)}주 → ${Math.round(data.position_after)}주`)
          : ''}
      </div></div>`);
    parts.push(`<div class="rv-step-section"><div class="rv-step-label">포트폴리오 변화</div>
      <div class="rv-step-grid">
        ${kv('현금 (전)', fmt0(data.cash_before) + '원')}
        ${kv('현금 (후)', fmt0(data.cash_after) + '원')}
        ${kv('총자산 (전)', fmt0(data.total_value_before) + '원')}
        ${kv('총자산 (후)', fmt0(data.total_value_after) + '원')}
      </div></div>`);
  }

  return parts.join('');
}

function section(label, text) {
  if (!text) return '';
  return `<div class="rv-step-section">
    <div class="rv-step-label">${label}</div>
    <div class="rv-step-value">${esc(String(text))}</div>
  </div>`;
}

function kv(key, val) {
  return `<div class="rv-step-kv">
    <div class="rv-step-kv-key">${key}</div>
    <div class="rv-step-kv-val">${val}</div>
  </div>`;
}

// ── LLM 팝업 ────────────────────────────────────────────
window.showLLMCalls = function(callsJsonStr, stepName) {
  let calls;
  try {
    calls = JSON.parse(callsJsonStr);
  } catch(e) {
    calls = [];
  }
  const STEP_NAMES = {
    market_intelligence:   'Market Intelligence',
    low_level_reflection:  'Low-Level Reflection',
    high_level_reflection: 'High-Level Reflection',
    decision_making:       'Decision Making',
  };
  modalTitle.textContent = `LLM 호출 — ${STEP_NAMES[stepName] || stepName}`;

  const first = calls[0] || {};
  modalMeta.innerHTML = `
    <span>🤖 모델: <strong>${esc(first.model || '—')}</strong></span>
    <span>🌡 Temperature: <strong>${first.temperature ?? '—'}</strong></span>
    <span>📞 호출 횟수: <strong>${calls.length}건</strong></span>
    <span>🖼 이미지 포함: <strong>${calls.some(c => c.has_image) ? 'Yes (Vision)' : 'No'}</strong></span>
  `;

  modalBody.innerHTML = calls.map((call, idx) => {
    let promptHtml;
    if (call.type === 'chat') {
      const msgs = call.messages || [];
      const userMsg = msgs.find(m => m.role === 'user');
      const promptText = typeof userMsg?.content === 'string'
        ? userMsg.content
        : JSON.stringify(userMsg?.content, null, 2);
      promptHtml = `<div class="rv-llm-section-title rv-llm-prompt-title">PROMPT (텍스트)</div>
        <div class="rv-llm-block">${esc(promptText)}</div>`;
    } else {
      promptHtml = `<div class="rv-llm-section-title rv-llm-prompt-title">PROMPT (텍스트 + 이미지 포함)</div>
        <div class="rv-llm-block">${esc(call.prompt || '')}</div>`;
    }

    const divider = idx > 0 ? '<hr class="rv-llm-call-divider">' : '';
    const callLabel = calls.length > 1 ? `<div style="font-size:11px;color:var(--text-muted);margin-bottom:8px">호출 #${idx + 1}</div>` : '';
    return `${divider}${callLabel}${promptHtml}
      <div class="rv-llm-section-title rv-llm-response-title" style="margin-top:10px">RESPONSE</div>
      <div class="rv-llm-block">${esc(call.response || '')}</div>`;
  }).join('');

  openModal();
};

// ── 비교 오버레이 ────────────────────────────────────────
async function runCompare() {
  const ids = [...STATE.selectedCompareIds].join(',');
  welcome.style.display = 'none';
  detail.style.display = 'none';
  compareView.style.display = '';
  STATE.selectedRunId = null;
  renderRunList();

  const tbody = document.querySelector('#rv-compare-table tbody');
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">로딩 중…</td></tr>';

  try {
    const data = await fetch(`/review/api/compare?ids=${ids}`).then(r => r.json());
    const colors = data.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]);
    renderCompareChart(data, colors);
    renderCompareTable(data, colors);
    // 비교 종합 분석 — ids 보존 후 정적·AI 패널 갱신
    STATE.currentCompareIds = data.map(r => r.run_id);
    renderCompareAnalysis(data);
    loadCompareAnalysisThread(STATE.currentCompareIds);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7">오류: ${esc(String(e))}</td></tr>`;
  }
}

const CHART_COLORS = ['#58a6ff','#ff7b00','#3fb950','#f85149','#d2a8ff','#ffd700','#00d4d4','#ff69b4'];

function renderCompareChart(data, colors) {
  const canvas = $('rv-compare-chart');
  if (STATE.compareChart) {
    STATE.compareChart.destroy();
    STATE.compareChart = null;
  }

  const datasets = [];
  data.forEach((run, i) => {
    const color = colors[i];
    const modelTag = run.llm_model ? ` [${run.llm_model}]` : '';
    datasets.push({
      label: `${run.stock_name} (${run.symbol})${modelTag}`,
      data: (run.equity_curve || []).map(p => ({ x: p.date, y: p.norm })),
      borderColor: color,
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.1,
    });
    if (run.benchmark_curve?.length) {
      datasets.push({
        label: `${run.symbol} B&H${modelTag}`,
        data: run.benchmark_curve.map(p => ({ x: p.date, y: p.norm })),
        borderColor: color,
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        borderDash: [4, 3],
        pointRadius: 0,
        tension: 0.1,
      });
    }
  });

  STATE.compareChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { type: 'category', ticks: { color: '#8b949e', maxTicksLimit: 12 }, grid: { color: '#21262d' } },
        y: {
          ticks: { color: '#8b949e', callback: v => v.toFixed(1) },
          grid: { color: '#21262d' },
          title: { display: true, text: '수익률 지수 (시작=100)', color: '#8b949e', font: { size: 11 } },
        },
      },
      plugins: {
        legend: { labels: { color: '#e6edf3', font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}` } },
      },
    },
  });
}

function renderCompareTable(data, colors) {
  const tbody = document.querySelector('#rv-compare-table tbody');
  tbody.innerHTML = data.map((run, i) => {
    const color = colors[i];
    const ret = run.total_return_pct;
    const bh = run.benchmark_return_pct;
    const exc = (ret != null && bh != null) ? ret - bh : null;
    const s = (v, pos) => v == null ? '—' : `<span style="color:${pos === null ? 'inherit' : v >= 0 ? 'var(--green)' : 'var(--red)'};font-weight:600">${v >= 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
    return `<tr>
      <td><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${color};margin-right:6px"></span>${esc(run.stock_name)}<br><span style="color:var(--text-muted);font-size:11px;margin-left:16px">${esc(run.symbol)}</span></td>
      <td style="font-size:12px">${run.start_date}<br>${run.end_date}</td>
      <td style="font-size:11px;color:${color};font-weight:600">${esc(run.llm_model || '—')}</td>
      <td>${prefLabel(run.trader_preference)}</td>
      <td>${s(ret, true)}</td>
      <td style="color:var(--text-muted)">${s(bh, null)}</td>
      <td>${s(exc, true)}</td>
    </tr>`;
  }).join('');
}

// ── 종합 분석 (정적 + LLM 스레드) ──────────────────────
function renderAnalysis(run, days) {
  const target = $('rv-analysis-static');
  const res = run.result || {};
  const tr = res.total_return_pct, bh = res.benchmark_return_pct;
  const alpha = (tr != null && bh != null) ? (tr - bh) : null;
  const cls = v => v == null ? '' : v >= 0 ? 'pos' : 'neg';

  const counts = { BUY: 0, SELL: 0, HOLD: 0 };
  (days || []).forEach(d => { counts[d.action] = (counts[d.action] || 0) + 1; });
  const total = counts.BUY + counts.SELL + counts.HOLD;
  const holdPct = total ? (counts.HOLD / total * 100) : 0;

  const provider = (run.provider || '').toLowerCase();
  const model = (run.model || '').toLowerCase();
  const visionCapable = /claude|gpt-4o|gemini|gemma|llava|vision/.test(model + provider);
  const visionNote = visionCapable
    ? '선택한 모델은 비전 입력을 지원하므로 LLR/HLR의 차트 이미지 분석이 활성화됩니다.'
    : '⚠️ 선택한 모델은 텍스트 전용으로 추정됩니다 — LLR/HLR의 차트 이미지 분석이 제한될 수 있어 논문의 멀티모달 가정 일부가 약화됩니다.';

  let baselineVerdict;
  if (alpha == null) baselineVerdict = '베이스라인과의 비교를 위한 데이터가 부족합니다.';
  else if (alpha > 1) baselineVerdict = `능동 매매가 베이스라인 대비 +${alpha.toFixed(2)}%p의 알파를 만들었습니다.`;
  else if (alpha < -1) baselineVerdict = `능동 매매는 베이스라인에 ${alpha.toFixed(2)}%p 미달 — Buy&Hold가 우세했습니다.`;
  else baselineVerdict = '베이스라인과 거의 동일 — 능동 매매가 의미 있는 가치를 더하지 못했습니다.';

  target.innerHTML = `
    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">📊 베이스라인(Buy&amp;Hold) 비교</div>
      <ul class="rv-analysis-list">
        <li>전략 수익률 <strong class="${cls(tr)}">${fmtPct(tr)}</strong> · B&amp;H 수익률 <strong>${fmtPct(bh)}</strong></li>
        <li>초과수익(α): <strong class="${cls(alpha)}">${fmtPct(alpha, true)}</strong></li>
        <li>${baselineVerdict}</li>
      </ul>
    </div>

    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">⚖️ 위험 조정 수익</div>
      <ul class="rv-analysis-list">
        <li>Sharpe: <strong>${fmtNum(res.sharpe_ratio)}</strong> · Sortino: <strong>${fmtNum(res.sortino_ratio)}</strong> · Calmar: <strong>${fmtNum(res.calmar_ratio)}</strong></li>
        <li>최대 낙폭(MDD): <strong class="${cls(res.max_drawdown_pct)}">${fmtPct(res.max_drawdown_pct)}</strong> · 연 변동성: <strong>${fmtPct(res.volatility_annual_pct)}</strong></li>
        <li>${ratioVerdict(res.sharpe_ratio)}</li>
      </ul>
    </div>

    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">🎯 거래 행동 분포</div>
      <ul class="rv-analysis-list">
        <li>BUY <strong>${counts.BUY}</strong> · SELL <strong>${counts.SELL}</strong> · HOLD <strong>${counts.HOLD}</strong> (총 ${total}일)</li>
        <li>HOLD 비율 <strong>${holdPct.toFixed(1)}%</strong> — ${holdPct > 80 ? '매우 보수적' : holdPct > 60 ? '보수적' : holdPct > 40 ? '중립' : '적극적'} 매매 패턴</li>
        <li>설정 성향: ${esc(prefLabel(run.trader_preference))}</li>
      </ul>
    </div>

    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">🧩 논문 모듈 매핑 (arxiv 2402.18485)</div>
      <ul class="rv-analysis-list">
        <li><strong>DataFetcher</strong>: pykrx OHLCV + Google RSS 뉴스(±7일 윈도)</li>
        <li><strong>Market Intelligence + Diversified Retrieval</strong>: 단/중/장기 3가지 질의로 ChromaDB 회상</li>
        <li><strong>Low-Level Reflection (Vision)</strong>: kline 차트 이미지 분석 (모델 비전 지원 필요)</li>
        <li><strong>High-Level Reflection</strong>: 과거 결정+결과 평가 → 트레이딩 차트 이미지로 검증</li>
        <li><strong>Decision Making + Tool-Augmented Signals</strong>: MACD / KDJ+RSI / ZMR / Bollinger 텍스트 시그널 주입</li>
      </ul>
    </div>

    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">⚠️ 본 구현의 한계점</div>
      <ul class="rv-analysis-list">
        <li>${visionNote}</li>
        <li>뉴스 소스가 Google RSS 단일 — 노이즈/중복 가능성, 영문 원문 누락</li>
        <li>단일 종목 백테스트 — 포트폴리오 분산 효과 미검증</li>
        <li>슬리피지·거래수수료·시장충격(Market Impact) 미반영</li>
        <li>기간 365일 이내 — 장기 사이클(경기/금리) 효과 검증 어려움</li>
        <li>BUY는 가용 현금의 50%, SELL은 전량 매도로 단순화 — 논문의 포지션 사이징은 더 정교</li>
        <li>LLM 호출의 비결정성 (temperature&gt;0일 경우 재현성 저하)</li>
      </ul>
    </div>
  `;
}

async function loadAnalysisThread(runId) {
  try {
    const res = await fetch(`/review/api/runs/${runId}/analysis`);
    if (!res.ok) return;
    const { thread } = await res.json();
    renderThread(thread || []);
  } catch {}
}

function renderThread(thread) {
  const container = $('rv-analysis-thread');
  const askArea   = $('rv-analysis-ask');
  const btnInit   = $('rv-btn-analyze');
  const btnRegen  = $('rv-btn-analyze-regen');

  if (!thread.length) {
    container.innerHTML = '';
    askArea.style.display = 'none';
    btnInit.style.display = '';
    btnRegen.style.display = 'none';
    return;
  }

  btnInit.style.display = 'none';
  btnRegen.style.display = '';
  askArea.style.display = '';

  container.innerHTML = thread.map(m => {
    const cls = m.role === 'assistant' ? 'rv-msg-ai' : 'rv-msg-user';
    const label = m.role === 'assistant' ? '🤖 AI' : '🗣️ 의견·질문';
    const ts = m.ts ? `<span class="rv-msg-ts">${esc(m.ts.slice(0,16).replace('T',' '))}</span>` : '';
    return `
      <div class="rv-msg ${cls}">
        <div class="rv-msg-head"><span class="rv-msg-label">${label}</span>${ts}</div>
        <div class="rv-msg-body">${mdToHtml(m.content)}</div>
      </div>`;
  }).join('');
}

async function requestAnalysis(force) {
  const runId = STATE.selectedRunId;
  if (!runId) return;
  const status = $('rv-analysis-ai-status');
  const btnInit = $('rv-btn-analyze');
  const btnRegen = $('rv-btn-analyze-regen');
  btnInit.disabled = true; btnRegen.disabled = true;
  status.textContent = 'LLM이 분석 중입니다…';
  try {
    const token = localStorage.getItem('fa_token');
    const url = `/review/api/runs/${runId}/analyze${force ? '?force=true' : ''}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '분석 실패');
    }
    const { thread, cached } = await res.json();
    renderThread(thread || []);
    status.textContent = cached ? '저장된 분석을 불러왔습니다.' : '분석을 새로 생성했습니다.';
  } catch (e) {
    status.textContent = '오류: ' + e.message;
  } finally {
    btnInit.disabled = false; btnRegen.disabled = false;
  }
}

async function sendQuestion() {
  const runId = STATE.selectedRunId;
  if (!runId) return;
  const input = $('rv-analysis-ask-input');
  const q = (input.value || '').trim();
  if (!q) return;
  const status = $('rv-analysis-ai-status');
  const btn = $('rv-btn-ask');
  btn.disabled = true; input.disabled = true;
  status.textContent = 'LLM이 답변 중입니다…';
  try {
    const token = localStorage.getItem('fa_token');
    const res = await fetch(`/review/api/runs/${runId}/analyze/ask`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '질문 전송 실패');
    }
    const { thread } = await res.json();
    renderThread(thread || []);
    input.value = '';
    status.textContent = '';
  } catch (e) {
    status.textContent = '오류: ' + e.message;
  } finally {
    btn.disabled = false; input.disabled = false;
  }
}

function fmtPct(v, withSign = false) {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = withSign && v > 0 ? '+' : '';
  return sign + Number(v).toFixed(2) + '%';
}
function fmtNum(v) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toFixed(3);
}
function ratioVerdict(sharpe) {
  if (sharpe == null) return '데이터 부족으로 위험 조정 평가 보류.';
  if (sharpe >= 2) return 'Sharpe ≥ 2 — 위험 대비 매우 우수한 수익 구조.';
  if (sharpe >= 1) return 'Sharpe ≥ 1 — 위험 대비 양호한 수익.';
  if (sharpe >= 0) return 'Sharpe < 1 — 위험 대비 수익이 미흡.';
  return 'Sharpe 음수 — 위험을 감수하고도 손실 발생.';
}

// 간단한 마크다운 → HTML (## 헤더, **bold**, - 리스트, 단락)
function mdToHtml(md) {
  let s = esc(md);
  s = s.replace(/^### (.+)$/gm, '<h5>$1</h5>');
  s = s.replace(/^## (.+)$/gm, '<h4>$1</h4>');
  s = s.replace(/^# (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // 리스트
  s = s.replace(/(?:^- .+(?:\n|$))+?/gm, block => {
    const items = block.trim().split(/\n/).map(l => l.replace(/^- /, '').trim());
    return '<ul>' + items.map(i => `<li>${i}</li>`).join('') + '</ul>';
  });
  // 단락 (빈 줄 기준)
  s = s.split(/\n{2,}/).map(p => /^<(h\d|ul|li|p)/.test(p.trim()) ? p : `<p>${p.replace(/\n/g,'<br>')}</p>`).join('');
  return s;
}

// ── 비교 종합 분석 (정적 + LLM 스레드) ─────────────────
function renderCompareAnalysis(data) {
  const target = $('rv-cmp-analysis-static');
  if (!data || data.length === 0) { target.innerHTML = ''; return; }

  // 베스트/워스트
  const rows = data.map((r, i) => {
    const tr = r.total_return_pct;
    const bh = r.benchmark_return_pct;
    const alpha = (tr != null && bh != null) ? (tr - bh) : null;
    return { i: i + 1, run: r, tr, bh, alpha };
  });
  const valid = rows.filter(x => x.tr != null);
  const bestRet = valid.length ? valid.reduce((a, b) => a.tr > b.tr ? a : b) : null;
  const worstRet = valid.length ? valid.reduce((a, b) => a.tr < b.tr ? a : b) : null;
  const validA = rows.filter(x => x.alpha != null);
  const bestAlpha = validA.length ? validA.reduce((a, b) => a.alpha > b.alpha ? a : b) : null;
  const winners = validA.filter(x => x.alpha > 0).length;
  const losers = validA.filter(x => x.alpha < 0).length;

  // 변수 다양성
  const models = new Set(data.map(r => r.llm_model || '기본'));
  const stocks = new Set(data.map(r => r.symbol));
  const prefs  = new Set(data.map(r => r.trader_preference));

  const fmtA = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  const cls = v => v == null ? '' : v >= 0 ? 'pos' : 'neg';

  let varHint;
  if (stocks.size === 1 && models.size > 1) {
    varHint = '동일 종목·다른 모델 — 결과 차이는 주로 <strong>LLM 모델 선택</strong>에 기인합니다.';
  } else if (models.size === 1 && stocks.size > 1) {
    varHint = '동일 모델·다른 종목 — 결과 차이는 주로 <strong>종목 특성</strong>에 기인합니다.';
  } else if (prefs.size > 1 && stocks.size === 1 && models.size === 1) {
    varHint = '동일 종목·모델·다른 성향 — 결과 차이는 주로 <strong>트레이더 성향</strong>에 기인합니다.';
  } else {
    varHint = '여러 변수가 동시에 다릅니다 — 단일 원인을 단정하기 어렵습니다(통제 변수 부재).';
  }

  target.innerHTML = `
    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">🏆 베스트 / 워스트</div>
      <ul class="rv-analysis-list">
        ${bestRet ? `<li>최고 수익: <strong>Run ${bestRet.i} — ${esc(bestRet.run.stock_name)}</strong> <span class="${cls(bestRet.tr)}">${fmtA(bestRet.tr)}</span></li>` : ''}
        ${worstRet ? `<li>최저 수익: <strong>Run ${worstRet.i} — ${esc(worstRet.run.stock_name)}</strong> <span class="${cls(worstRet.tr)}">${fmtA(worstRet.tr)}</span></li>` : ''}
        ${bestAlpha ? `<li>최대 알파(α): <strong>Run ${bestAlpha.i}</strong> <span class="${cls(bestAlpha.alpha)}">${fmtA(bestAlpha.alpha)}</span></li>` : ''}
      </ul>
    </div>

    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">📊 베이스라인(Buy&amp;Hold) 비교</div>
      <ul class="rv-analysis-list">
        <li>α &gt; 0 (베이스라인 이김): <strong>${winners}</strong>건 / α &lt; 0 (짐): <strong>${losers}</strong>건 / 전체 <strong>${data.length}</strong>건</li>
        <li>${winners > losers ? '능동 매매 전략이 평균적으로 베이스라인을 이긴 표본 우세' : winners < losers ? '베이스라인이 평균적으로 능동 매매보다 우세' : '베이스라인과 능동 매매가 팽팽함'}</li>
      </ul>
    </div>

    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">🔬 변수별 영향 가설</div>
      <ul class="rv-analysis-list">
        <li>비교된 모델: ${models.size}종 · 종목: ${stocks.size}종 · 성향: ${prefs.size}종</li>
        <li>${varHint}</li>
      </ul>
    </div>

    <div class="rv-analysis-section">
      <div class="rv-analysis-section-title">⚠️ 비교 자체의 한계</div>
      <ul class="rv-analysis-list">
        <li>표본 ${data.length}건은 통계적 유의성을 주장하기엔 작음</li>
        <li>기간이 다르면 시장 국면(상승/하락/횡보)도 다름 — 동일 조건 비교 아님</li>
        <li>모델·종목·성향이 동시 변수일 경우 단일 변수의 효과를 분리하기 어려움</li>
        <li>LLM 비결정성 (temperature&gt;0) 시 동일 입력에도 결과가 변동 가능</li>
      </ul>
    </div>
  `;
}

async function loadCompareAnalysisThread(ids) {
  if (!ids || ids.length < 2) return;
  try {
    const res = await fetch(`/review/api/compare/analysis?ids=${ids.join(',')}`);
    if (!res.ok) return;
    const { thread } = await res.json();
    renderCompareThread(thread || []);
  } catch {}
}

function renderCompareThread(thread) {
  const container = $('rv-cmp-analysis-thread');
  const askArea   = $('rv-cmp-analysis-ask');
  const btnInit   = $('rv-btn-cmp-analyze');
  const btnRegen  = $('rv-btn-cmp-analyze-regen');

  if (!thread.length) {
    container.innerHTML = '';
    askArea.style.display = 'none';
    btnInit.style.display = '';
    btnRegen.style.display = 'none';
    return;
  }
  btnInit.style.display = 'none';
  btnRegen.style.display = '';
  askArea.style.display = '';

  STATE.cmpThread = thread;
  container.innerHTML = thread.map((m, i) => {
    const cls = m.role === 'assistant' ? 'rv-msg-ai' : 'rv-msg-user';
    const label = m.role === 'assistant' ? '🤖 AI' : '🗣️ 의견·질문';
    const ts = m.ts ? `<span class="rv-msg-ts">${esc(m.ts.slice(0,16).replace('T',' '))}</span>` : '';
    const inspectBtn = m.role === 'assistant' && m.prompt
      ? `<button class="rv-msg-inspect" data-idx="${i}" title="LLM 호출 프롬프트와 응답을 봅니다">🔍 LLM 호출 보기</button>`
      : '';
    return `
      <div class="rv-msg ${cls}">
        <div class="rv-msg-head"><span class="rv-msg-label">${label}</span>${ts}${inspectBtn}</div>
        <div class="rv-msg-body">${mdToHtml(m.content)}</div>
      </div>`;
  }).join('');

  container.querySelectorAll('.rv-msg-inspect').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx, 10);
      const m = STATE.cmpThread[idx];
      if (!m) return;
      modalTitle.textContent = '🤖 AI 비교 분석 — LLM 호출 상세';
      modalMeta.innerHTML = `<span>📨 메시지 ${idx + 1}</span>` +
        (m.ts ? `<span>🕒 ${esc(m.ts.replace('T',' ').slice(0,19))}</span>` : '');
      modalBody.innerHTML = `
        <div class="rv-llm-section-title rv-llm-prompt-title">PROMPT</div>
        <div class="rv-llm-block">${esc(m.prompt || '')}</div>
        <div class="rv-llm-section-title rv-llm-response-title">RESPONSE</div>
        <div class="rv-llm-block">${esc(m.content || '')}</div>
      `;
      openModal();
    });
  });
}

async function requestCompareAnalysis(force) {
  const ids = STATE.currentCompareIds;
  if (!ids || ids.length < 2) return;
  const status = $('rv-cmp-analysis-status');
  const btnInit = $('rv-btn-cmp-analyze');
  const btnRegen = $('rv-btn-cmp-analyze-regen');
  btnInit.disabled = true; btnRegen.disabled = true;
  status.textContent = 'LLM이 비교 분석 중입니다…';
  try {
    const token = localStorage.getItem('fa_token');
    const res = await fetch(`/review/api/compare/analyze`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, force: !!force }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '분석 실패');
    }
    const { thread, cached } = await res.json();
    renderCompareThread(thread || []);
    status.textContent = cached ? '저장된 비교 분석을 불러왔습니다.' : '비교 분석을 새로 생성했습니다.';
  } catch (e) {
    status.textContent = '오류: ' + e.message;
  } finally {
    btnInit.disabled = false; btnRegen.disabled = false;
  }
}

async function sendCompareQuestion() {
  const ids = STATE.currentCompareIds;
  if (!ids || ids.length < 2) return;
  const input = $('rv-cmp-ask-input');
  const q = (input.value || '').trim();
  if (!q) return;
  const status = $('rv-cmp-analysis-status');
  const btn = $('rv-btn-cmp-ask');
  btn.disabled = true; input.disabled = true;
  status.textContent = 'LLM이 답변 중입니다…';
  try {
    const token = localStorage.getItem('fa_token');
    const res = await fetch(`/review/api/compare/analyze/ask`, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, question: q }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || '질문 전송 실패');
    }
    const { thread } = await res.json();
    renderCompareThread(thread || []);
    input.value = '';
    status.textContent = '';
  } catch (e) {
    status.textContent = '오류: ' + e.message;
  } finally {
    btn.disabled = false; input.disabled = false;
  }
}

// ── 드로어 / 모달 ─────────────────────────────────────
function openDrawer()  { drawer.classList.add('open');   drawerOverlay.classList.add('open'); }
function closeDrawer() { drawer.classList.remove('open'); drawerOverlay.classList.remove('open'); }
function openModal()   { modal.classList.add('open');    modalOverlay.classList.add('open'); }
function closeModal()  { modal.classList.remove('open'); modalOverlay.classList.remove('open'); }

// LLM이 XML 형식으로 출력한 reasoning에서 태그를 벗겨 깔끔히 표시
function cleanReasoning(text) {
  if (!text) return '';
  let s = String(text);
  // <analysis> 블록이 있으면 그 내용만 우선 추출
  const m = s.match(/<analysis>([\s\S]*?)<\/analysis>/i);
  if (m) s = m[1];
  // 남은 우리 도메인 태그들 제거
  s = s.replace(/<\/?(?:output|analysis|reasoning|action|decision|summary|short_term[^>]*|medium_term[^>]*|long_term[^>]*)[^>]*>/gi, '');
  return s.trim();
}

// ── 포맷 유틸 ──────────────────────────────────────────
function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function fmt0(n) {
  return n == null ? '—' : Number(n).toLocaleString('ko-KR', { maximumFractionDigits: 0 });
}
function pct(v) { return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'; }

document.addEventListener('DOMContentLoaded', init);
