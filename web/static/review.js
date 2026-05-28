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
    if (STATE.runs.some(r => r.status === 'running')) {
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

  runList.innerHTML = filtered.map(r => {
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
            <span class="rv-run-item-stock">${esc(r.stock_name)}</span>
            <span class="rv-run-item-symbol">${esc(r.symbol)}</span>
            <span class="rv-run-item-status ${r.status}">${statusLabel(r.status)}</span>
          </div>
          <div class="rv-run-item-period">${r.start_date} ~ ${r.end_date}</div>
          <div class="rv-run-item-kpi">
            <span class="rv-run-item-ret ${retClass}">${retStr}</span>
            ${bhStr ? `<span style="color:var(--text-muted)">${bhStr}</span>` : ''}
            <span class="rv-run-item-llm">${llmStr}</span>
          </div>
        </div>
      </div>`;
  }).join('');
}

function statusLabel(s) {
  return { done: '완료', running: '실행중', error: '오류' }[s] || s;
}

// ── 이벤트 ──────────────────────────────────────────────
function bindEvents() {
  runList.addEventListener('click', e => {
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
    renderDetail(run);
    renderDayTable();
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
  badge.textContent = statusLabel(run.status);
  badge.className = `rv-run-badge ${run.status}`;
  if (run.status === 'running') {
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
  renderKPI(run.result || {});

  const resumeBtn = $('rv-btn-resume');
  if (run.status === 'error' || run.status === 'running') {
    resumeBtn.style.display = '';
    resumeBtn.disabled = false;
    resumeBtn.textContent = '▶ 이어서 실행';
    resumeBtn.onclick = () => resumeRun(run.id);
  } else {
    resumeBtn.style.display = 'none';
  }

  $('rv-btn-delete').onclick = () => deleteRun(run.id, run.stock_name, run.start_date, run.end_date);
}

async function deleteRun(runId, stockName, startDate, endDate) {
  const confirmed = confirm(`삭제하시겠습니까?\n\n${stockName}  ${startDate} ~ ${endDate}\n\n이 작업은 되돌릴 수 없습니다.`);
  if (!confirmed) return;

  try {
    const res = await fetch(`/review/api/runs/${runId}`, { method: 'DELETE' });
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
  const kpis = [
    { label: '총 수익률', key: 'total_return_pct', fmt: pct, sign: true },
    { label: '연간 수익률', key: 'annualized_return_pct', fmt: pct, sign: true },
    { label: 'Sharpe Ratio', key: 'sharpe_ratio', fmt: v => v.toFixed(3) },
    { label: 'Sortino Ratio', key: 'sortino_ratio', fmt: v => v.toFixed(3) },
    { label: 'Calmar Ratio', key: 'calmar_ratio', fmt: v => v.toFixed(3) },
    { label: '최대 낙폭 MDD', key: 'max_drawdown_pct', fmt: pct, sign: true },
    { label: '연간 변동성', key: 'volatility_annual_pct', fmt: v => v.toFixed(2) + '%' },
    { label: 'B&H 초과수익', key: '_excess', fmt: pct, sign: true },
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
    card.innerHTML = `<div class="rv-kpi-label">${k.label}</div><div class="rv-kpi-value ${cls}">${valStr}</div>`;
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
      <td class="rv-reasoning">${esc((d.reasoning || '').slice(0, 120))}${(d.reasoning || '').length > 120 ? '…' : ''}</td>
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
    parts.push(section('단기 분석 (1-5일)', out.short_term_reasoning));
    parts.push(section('중기 분석 (1-4주)', out.medium_term_reasoning));
    parts.push(section('장기 분석 (1-3개월)', out.long_term_reasoning));
  }

  if (key === 'high_level_reflection') {
    const out = data.output || {};
    parts.push(section('과거 결정 평가', out.reasoning));
    parts.push(section('개선 방안', out.improvement));
    parts.push(section('핵심 요약 (메모리 저장)', out.summary));
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
    if (out.analysis) parts.push(section('단계별 분석', out.analysis));
    parts.push(section('결정 근거', out.reasoning));

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

// ── 드로어 / 모달 ─────────────────────────────────────
function openDrawer()  { drawer.classList.add('open');   drawerOverlay.classList.add('open'); }
function closeDrawer() { drawer.classList.remove('open'); drawerOverlay.classList.remove('open'); }
function openModal()   { modal.classList.add('open');    modalOverlay.classList.add('open'); }
function closeModal()  { modal.classList.remove('open'); modalOverlay.classList.remove('open'); }

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
