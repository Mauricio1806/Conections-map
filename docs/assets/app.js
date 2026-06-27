/* LinkedIn Network Intelligence Dashboard — app.js */
'use strict';

// ── Globals ──────────────────────────────────────────────────────────────────
let D = null;           // raw data object
let charts = {};        // Chart.js instances
let contactsPage = 1;
let filteredContacts = [];
let filteredGap = [];

const PAGE_SIZE = 25;

const MARKET_COLORS = {
  BRAZIL:             '#22c55e',
  LATAM_USD:          '#f59e0b',
  US_CANADA_NEARSHORE:'#3b82f6',
  SPAIN_EU:           '#ef4444',
  EUROPE:             '#a78bfa',
  GLOBAL_STAFFING:    '#14b8a6',
  GLOBAL_TECH:        '#38bdf8',
  GLOBAL_CONSULTING:  '#fb923c',
  UNKNOWN:            '#4b5563',
};

const URGENCY_COLORS = {
  Critical:  '#ef4444',
  High:      '#f97316',
  Medium:    '#f59e0b',
  Low:       '#22c55e',
  Saturated: '#14b8a6',
};

// ── Boot ─────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  fetch('assets/dashboard_data.json')
    .then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(data => {
      D = data;
      document.getElementById('loading').style.display = 'none';
      document.getElementById('app').style.display = 'flex';
      initNav();
      renderOverview();
      renderHeatmaps();
      renderGap();
      renderPlan();
      renderContacts();
      renderCompanies();
      renderUnknown();
      renderQuality();
    })
    .catch(err => {
      document.getElementById('loading').innerHTML =
        `<p style="color:var(--red)">Failed to load dashboard data: ${err.message}</p>
         <p style="color:var(--text-muted);font-size:0.8rem">Make sure dashboard_data.json is in assets/ and you are serving via HTTP.</p>`;
    });
});

// ── Navigation ────────────────────────────────────────────────────────────────
function initNav() {
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
      const page = el.dataset.page;
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
      el.classList.add('active');
      document.getElementById(`page-${page}`).classList.add('active');
    });
  });

  // Tab buttons
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.dataset.tab;
      const parent = btn.closest('.page') || btn.parentElement.parentElement;
      parent.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      parent.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(tabId).classList.add('active');
      // Lazy-render company charts when tabs switch
      if (tabId.startsWith('co-')) renderCompanyChart(tabId);
    });
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function kpi(key, fallback = 0) {
  return D?.kpis?.[key] ?? fallback;
}

function fmt(n) {
  if (n === null || n === undefined) return '—';
  if (typeof n === 'number') return n.toLocaleString();
  return n;
}

function scoreClass(s) {
  s = parseFloat(s);
  if (s >= 70) return 'score-high';
  if (s >= 40) return 'score-medium';
  return 'score-low';
}

function urgencyClass(u) { return `urgency-${u}`; }

function levelPillClass(level) {
  const map = { Strong: 'level-pill-strong', Developing: 'level-pill-develop',
                'Early Stage': 'level-pill-early', 'Not Started': 'level-pill-notstart' };
  return map[level] || 'level-pill-develop';
}

function scoreColorClass(level) {
  const map = { Strong: 'score-strong', Developing: 'score-develop',
                'Early Stage': 'score-early', 'Not Started': 'score-notstart' };
  return map[level] || 'score-develop';
}

function makeCard(title, value, delta = '', deltaClass = '') {
  return `<div class="card">
    <div class="card-title">${title}</div>
    <div class="card-value">${fmt(value)}</div>
    ${delta ? `<div class="card-delta ${deltaClass}">${delta}</div>` : ''}
  </div>`;
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function makeBarChart(canvasId, labels, values, colors, opts = {}) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  charts[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors || '#3b82f6', borderRadius: 4, borderSkipped: false }]
    },
    options: {
      indexAxis: opts.horizontal ? 'y' : 'x',
      responsive: true,
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        label: ctx => ` ${ctx.parsed[opts.horizontal ? 'x' : 'y'].toLocaleString()}`
      }}},
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: 11 } }, grid: { color: '#30363d' } },
        y: { ticks: { color: '#8b949e', font: { size: 11 } }, grid: { color: '#30363d' } }
      },
      ...opts.extra
    }
  });
}

function makePieChart(canvasId, labels, values, colors) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  charts[canvasId] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors, borderColor: '#0f1117', borderWidth: 2 }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'right', labels: { color: '#8b949e', font: { size: 11 }, boxWidth: 14, padding: 12 } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${ctx.parsed.toLocaleString()}` } }
      }
    }
  });
}

// ── PAGE: Executive Overview ──────────────────────────────────────────────────
function renderOverview() {
  // Data confidence alert
  const confScore  = kpi('data_confidence_score');
  const unknownPct = kpi('unknown_pct');
  const alertEl = document.getElementById('data-confidence-alert');
  if (unknownPct > 70) {
    alertEl.innerHTML = `<div class="alert alert-warn">
      <span class="alert-icon">&#9888;&#65039;</span>
      <span><strong>Data Confidence Warning:</strong> ${unknownPct}% of your network has no market signal.
      Adjusted scores are penalized accordingly. Use <code>outputs/company_market_mapping_template.csv</code>
      to classify the top unknown companies and improve score accuracy.</span>
    </div>`;
  } else {
    alertEl.innerHTML = `<div class="alert alert-good">
      <span class="alert-icon">&#9989;</span>
      <span><strong>Data Confidence: ${confScore}%</strong> of connections have reliable market inference.
      Adjusted scores reflect this level of confidence.</span>
    </div>`;
  }

  // Gauges
  const gauges = [
    { label: 'USD Opportunity Score', raw: kpi('usd_network_score_raw'), adj: kpi('usd_network_score_adjusted'), level: kpi('usd_score_level','—'), desc: kpi('usd_score_desc',''), next: kpi('usd_next_step','') },
    { label: 'Spain/EU Readiness Score', raw: kpi('spain_network_score_raw'), adj: kpi('spain_network_score_adjusted'), level: kpi('spain_score_level','—'), desc: kpi('spain_score_desc',''), next: kpi('spain_next_step','') },
    { label: 'Market Readiness Score', raw: kpi('market_readiness_score_raw'), adj: kpi('market_readiness_score_adjusted'), level: confScore >= 15 ? 'Developing' : 'Early Stage', desc: 'Composite: USD×0.6 + Spain×0.4. Based on confidence-adjusted sub-scores.', next: '' },
  ];

  document.getElementById('gauges-row').innerHTML = gauges.map(g => {
    const cc = scoreColorClass(g.level);
    const lc = levelPillClass(g.level);
    return `<div class="gauge-card">
      <div class="gauge-label">${g.label}</div>
      <div class="gauge-scores">
        <div class="gauge-raw">
          <div class="val">${g.raw}</div>
          <div class="lbl">raw</div>
        </div>
        <div style="font-size:1.5rem;color:var(--border);align-self:center">&#8594;</div>
        <div class="gauge-adj">
          <div class="val ${cc}">${g.adj}</div>
          <div class="lbl">adjusted</div>
        </div>
      </div>
      <div><span class="gauge-level ${lc}">${g.level}</span></div>
      <div class="gauge-desc">${g.desc}</div>
      ${g.next ? `<div class="gauge-desc" style="margin-top:0.5rem;color:var(--accent2)">${g.next}</div>` : ''}
    </div>`;
  }).join('');

  // Executive diagnosis
  const usdAdj = kpi('usd_network_score_adjusted');
  const spainAdj = kpi('spain_network_score_adjusted');
  const diagItems = [
    { cls: usdAdj >= 45 ? 'good' : 'bad', title: 'USD Job Readiness', text: usdAdj >= 45 ? `Score ${usdAdj}/100 — network is developing. Active opportunities possible.` : `Score ${usdAdj}/100 — network needs significant growth. Focus on LATAM USD and US/CA recruiters.` },
    { cls: spainAdj >= 25 ? 'good' : 'warn', title: 'Spain/EU Readiness', text: spainAdj >= 25 ? `Score ${spainAdj}/100 — early foundation exists. Sustain 4+ Spain connections/week.` : `Score ${spainAdj}/100 — Spain/EU network is nascent. Acceptable given your 6-18 month timeline.` },
    { cls: confScore >= 20 ? 'good' : 'warn', title: 'Data Confidence', text: confScore >= 20 ? `${confScore}% high-confidence. Adjusted scores are moderately reliable.` : `${confScore}% high-confidence. Scores are estimates. Classify top unknown companies to improve.` },
    { cls: 'info', title: 'Next 7-Day Focus', text: `Add ${Math.max(0, 60 - kpi('usd_recruiters_hc'))} LATAM USD recruiters + classify 20 companies in mapping template.` },
    { cls: 'info', title: 'Main Weakness', text: `${unknownPct}% market UNKNOWN limits score accuracy. Top unknown companies: see "Classify Unknown" page.` },
    { cls: usdAdj >= 30 ? 'good' : 'warn', title: 'Network Strength', text: `${kpi('total_connections').toLocaleString()} total connections, ${kpi('high_priority').toLocaleString()} high-priority (score ≥70), ${kpi('actionable_contacts').toLocaleString()} actionable (high-conf + score ≥60).` },
  ];
  document.getElementById('diagnosis-grid').innerHTML = diagItems.map(d =>
    `<div class="diagnosis-item ${d.cls}"><h4>${d.title}</h4><p>${d.text}</p></div>`
  ).join('');

  // Priority metrics
  document.getElementById('priority-metrics').innerHTML = [
    makeCard('Total Connections', kpi('total_connections')),
    makeCard('High Priority', kpi('high_priority'), kpi('high_priority_pct') + '%', 'good'),
    makeCard('Medium Priority', kpi('medium_priority'), kpi('medium_priority_pct') + '%', 'warn'),
    makeCard('Actionable Contacts', kpi('actionable_contacts')),
    makeCard('Data Confidence', kpi('data_confidence_score') + '%'),
  ].join('');

  // Persona metrics
  document.getElementById('persona-metrics').innerHTML = [
    makeCard('Recruiters', kpi('recruiters_total')),
    makeCard('Talent / HR', kpi('talent_hr_total')),
    makeCard('Hiring Managers', kpi('hiring_managers_total')),
    makeCard('Data Leaders', kpi('data_leaders_total')),
    makeCard('Data Peers', kpi('data_peers_total')),
  ].join('');

  // Market metrics (V2)
  const mktItems = [
    ['Brazil', 'brazil_count'],
    ['LATAM USD', 'latam_usd_count'],
    ['US/CA Nearshore', 'us_nearshore_count'],
    ['Spain/EU', 'spain_eu_count'],
    ['Europe', 'europe_count'],
    ['Global Staffing', 'global_staffing_count'],
    ['Global Tech', 'global_tech_count'],
    ['Global Consulting', 'global_consulting_count'],
    ['Unknown', 'unknown_count'],
  ];
  document.getElementById('market-metrics').innerHTML = mktItems.map(([l, k]) =>
    makeCard(l, kpi(k))
  ).join('');

  // Market pie chart
  const mktDist = D.market_distribution || {};
  const mktLabels  = Object.keys(mktDist);
  const mktValues  = Object.values(mktDist);
  const mktColors  = mktLabels.map(m => MARKET_COLORS[m] || '#555');
  makePieChart('chart-market-pie', mktLabels, mktValues, mktColors);

  // Persona bar chart
  const persDist = D.persona_distribution || {};
  const persLabels = Object.keys(persDist);
  const persValues = Object.values(persDist);
  makeBarChart('chart-persona-bar', persLabels, persValues, persLabels.map(() => '#3b82f6'), { horizontal: true });

  // Concentration flags
  const flags = kpi('concentration_flags', []);
  document.getElementById('concentration-flags').innerHTML = (Array.isArray(flags) ? flags : [flags])
    .map(f => {
      const cls = f.includes('No critical') ? 'alert-good' : (f.includes('HIGH') ? 'alert-bad' : 'alert-warn');
      const icon = f.includes('No critical') ? '&#9989;' : '&#9888;&#65039;';
      return `<div class="alert ${cls}"><span class="alert-icon">${icon}</span><span>${f}</span></div>`;
    }).join('');
}

// ── PAGE: Heatmap ─────────────────────────────────────────────────────────────
function renderHeatmaps() {
  const hm = D.heatmaps || {};

  function renderHeatmapTable(tableId, hmData) {
    const tbl = document.getElementById(tableId);
    if (!tbl || !hmData || !hmData.labels) { if(tbl) tbl.innerHTML='<tr><td>No data</td></tr>'; return; }

    const excludeUnknown = document.getElementById('hm-exclude-unknown')?.checked;
    let cols = hmData.columns || [];
    if (excludeUnknown) cols = cols.filter(c => c !== 'UNKNOWN');

    const colIdxMap = (hmData.columns || []).map((c, i) => excludeUnknown && c === 'UNKNOWN' ? -1 : i);
    const filteredColIdxs = colIdxMap.filter(i => i >= 0);
    const filteredCols    = filteredColIdxs.map(i => hmData.columns[i]);

    // Get max value for heatmap coloring
    let maxVal = 0;
    hmData.data.forEach(row => row.forEach((v, i) => {
      if (filteredColIdxs.includes(i)) maxVal = Math.max(maxVal, v);
    }));

    let html = '<thead><tr><th>Persona/Category</th>';
    filteredCols.forEach(c => { html += `<th>${c}</th>`; });
    html += '</tr></thead><tbody>';

    hmData.labels.forEach((label, ri) => {
      const row = hmData.data[ri] || [];
      html += `<tr><td style="white-space:nowrap;font-weight:500">${label}</td>`;
      filteredColIdxs.forEach(ci => {
        const v   = row[ci] || 0;
        const pct = maxVal > 0 ? v / maxVal : 0;
        const r   = Math.round(59 + pct * 30);
        const g   = Math.round(130 + pct * 60);
        const b   = Math.round(246);
        const bg  = v > 0 ? `rgba(${r},${g},${b},${0.1 + pct * 0.7})` : 'transparent';
        html += `<td><span class="heatmap-cell" style="background:${bg};color:var(--text)">${v > 0 ? v.toLocaleString() : '—'}</span></td>`;
      });
      html += '</tr>';
    });

    html += '</tbody>';
    tbl.innerHTML = html;
  }

  renderHeatmapTable('heatmap-persona-market', hm.persona_market);
  renderHeatmapTable('heatmap-area-market',    hm.area_market);
  renderHeatmapTable('heatmap-seniority-market', hm.seniority_market);

  // Re-render on toggle change
  document.getElementById('hm-exclude-unknown')?.addEventListener('change', () => {
    renderHeatmapTable('heatmap-persona-market', hm.persona_market);
    renderHeatmapTable('heatmap-area-market',    hm.area_market);
    renderHeatmapTable('heatmap-seniority-market', hm.seniority_market);
  });
}

// ── PAGE: Gap ─────────────────────────────────────────────────────────────────
function renderGap() {
  const gap = D.gap_analysis || [];
  filteredGap = [...gap];

  // Populate market filter
  const markets = [...new Set(gap.map(r => r.market || r.strategic_market || ''))].sort();
  const mf = document.getElementById('gap-market-filter');
  markets.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; mf.appendChild(o); });

  renderGapTable();
  renderGapChart();
}

function applyGapFilters() {
  const urg = document.getElementById('gap-urgency-filter').value;
  const mkt = document.getElementById('gap-market-filter').value;
  filteredGap = (D.gap_analysis || []).filter(r => {
    const u = r.urgency_level || '';
    const m = r.market || r.strategic_market || '';
    return (!urg || u === urg) && (!mkt || m === mkt);
  });
  renderGapTable();
  renderGapChart();
}

function resetGapFilters() {
  document.getElementById('gap-urgency-filter').value = '';
  document.getElementById('gap-market-filter').value = '';
  filteredGap = [...(D.gap_analysis || [])];
  renderGapTable();
  renderGapChart();
}

function renderGapTable() {
  document.getElementById('gap-stats').textContent = `Showing ${filteredGap.length} rows`;
  const tbody = document.getElementById('gap-tbody');
  tbody.innerHTML = filteredGap.map(r => {
    const urg = r.urgency_level || '';
    const gap = r.gap_count ?? 0;
    return `<tr>
      <td><span class="market-badge">${r.market || ''}</span></td>
      <td>${r.persona || ''}</td>
      <td>${fmt(r.current_count)}</td>
      <td>${fmt(r.target_count)}</td>
      <td><strong>${fmt(gap)}</strong></td>
      <td>${fmt(r.gap_percentage)}%</td>
      <td><span class="urgency-badge ${urgencyClass(urg)}">${urg}</span></td>
      <td>${r.timeframe || ''}</td>
      <td style="white-space:normal;font-size:0.75rem;max-width:250px">${(r.recommended_action||'').substring(0,120)}${r.recommended_action?.length > 120 ? '…' : ''}</td>
    </tr>`;
  }).join('');
}

function renderGapChart() {
  const sorted = [...filteredGap].sort((a, b) => (b.gap_count || 0) - (a.gap_count || 0)).slice(0, 15);
  const labels = sorted.map(r => `${r.market||''} – ${r.persona||''}`);
  const values = sorted.map(r => r.gap_count || 0);
  const colors = sorted.map(r => URGENCY_COLORS[r.urgency_level] || '#555');
  makeBarChart('chart-gap-bar', labels, values, colors, { horizontal: true, extra: { indexAxis: 'y' } });
}

// ── PAGE: Action Plan ─────────────────────────────────────────────────────────
function renderPlan() {
  const searchQueries = {
    LATAM_USD:           '"data engineer" "remote" "LATAM" OR "latin america" recruiter',
    US_CANADA_NEARSHORE: '"data engineer" "nearshore" OR "remote" "USA" OR "Canada" recruiter',
    SPAIN_EU:            '"data engineer" "Spain" OR "Madrid" OR "Barcelona" recruiter',
    EUROPE:              '"data engineer" "Europe" OR "Germany" OR "Netherlands" recruiter',
    GLOBAL_STAFFING:     '"data engineer" "staffing" OR "nearshore" recruiter',
  };

  function renderPlanGrid(plans, gridId) {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    grid.innerHTML = plans.slice(0, 20).map(r => {
      const urg     = (r.urgency_level || '').toLowerCase();
      const gap     = r.gap_count || 0;
      const persona = r.persona || '';
      const market  = r.market || '';
      const query   = searchQueries[market] || `"${persona}" "${market.replace(/_/g,' ')}"`;
      const weeklyConn = Math.ceil(Math.min(gap, 80) / 4) || 1;

      return `<div class="plan-card ${urg}">
        <div class="plan-card-header">
          <div>
            <div class="plan-card-title">${market} &mdash; ${persona}</div>
            <div class="plan-card-meta">${r.timeframe || ''}</div>
          </div>
          <span class="urgency-badge ${urgencyClass(r.urgency_level)}">${r.urgency_level||''}</span>
        </div>
        <div class="plan-card-targets">
          <div class="plan-target-item"><div class="num">${fmt(r.current_count)}</div><div class="lbl">current</div></div>
          <div class="plan-target-item"><div class="num">${fmt(r.target_count)}</div><div class="lbl">target</div></div>
          <div class="plan-target-item"><div class="num" style="color:var(--red)">${fmt(gap)}</div><div class="lbl">gap</div></div>
          <div class="plan-target-item"><div class="num" style="color:var(--accent2)">${weeklyConn}/wk</div><div class="lbl">connections</div></div>
        </div>
        <div class="plan-card-action">${(r.strategic_reason||'').substring(0,140)}</div>
        <div class="plan-card-query">Search: ${query}</div>
      </div>`;
    }).join('');
  }

  renderPlanGrid(D.action_plan_30 || [], 'plan-30-grid');
  renderPlanGrid(D.action_plan_60 || [], 'plan-60-grid');
  renderPlanGrid(D.action_plan_90 || [], 'plan-90-grid');
}

// ── PAGE: Top Contacts ────────────────────────────────────────────────────────
function renderContacts() {
  const contacts = D.top_contacts || [];

  // Populate filters
  const personas = [...new Set(contacts.map(c => c.persona || ''))].sort();
  const markets  = [...new Set(contacts.map(c => c.market_v2 || c.strategic_market || ''))].sort();
  const pf = document.getElementById('ct-persona-filter');
  const mf = document.getElementById('ct-market-filter');
  personas.forEach(p => { const o = document.createElement('option'); o.value = p; o.textContent = p; pf.appendChild(o); });
  markets.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; mf.appendChild(o); });

  filteredContacts = contacts;
  renderContactsTable();
}

function applyContactFilters() {
  const minScore = parseFloat(document.getElementById('ct-min-score').value) || 0;
  const persona  = document.getElementById('ct-persona-filter').value;
  const market   = document.getElementById('ct-market-filter').value;
  const band     = document.getElementById('ct-band-filter').value;
  const hcOnly   = document.getElementById('ct-hc-only').checked;

  filteredContacts = (D.top_contacts || []).filter(c => {
    const s  = parseFloat(c.priority_score) || 0;
    const cf = parseFloat(c.market_confidence_v2 || 0);
    const m  = c.market_v2 || c.strategic_market || '';
    if (s < minScore) return false;
    if (persona && c.persona !== persona) return false;
    if (market && m !== market) return false;
    if (band === 'high'   && s < 70) return false;
    if (band === 'medium' && (s < 40 || s >= 70)) return false;
    if (band === 'low'    && s >= 40) return false;
    if (hcOnly && cf < 0.70) return false;
    return true;
  });

  contactsPage = 1;
  renderContactsTable();
}

function resetContactFilters() {
  document.getElementById('ct-min-score').value = '60';
  document.getElementById('ct-persona-filter').value = '';
  document.getElementById('ct-market-filter').value = '';
  document.getElementById('ct-band-filter').value = '';
  document.getElementById('ct-hc-only').checked = false;
  filteredContacts = D.top_contacts || [];
  contactsPage = 1;
  renderContactsTable();
}

function renderContactsTable() {
  const start = (contactsPage - 1) * PAGE_SIZE;
  const slice = filteredContacts.slice(start, start + PAGE_SIZE);

  document.getElementById('ct-stats').textContent =
    `Showing ${start + 1}–${Math.min(start + PAGE_SIZE, filteredContacts.length)} of ${filteredContacts.length}`;

  const tbody = document.getElementById('contacts-tbody');
  tbody.innerHTML = slice.map(c => {
    const s   = parseFloat(c.priority_score) || 0;
    const cf  = parseFloat(c.market_confidence_v2) || 0;
    const mkt = c.market_v2 || c.strategic_market || 'UNKNOWN';
    const url = c.url || '';
    const confPct = Math.round(cf * 100);
    return `<tr>
      <td style="white-space:nowrap">${c.full_name || '—'}</td>
      <td style="white-space:nowrap">${c.company_clean || '—'}</td>
      <td style="white-space:nowrap;max-width:200px;overflow:hidden;text-overflow:ellipsis">${c.position_clean || '—'}</td>
      <td style="white-space:nowrap">${c.persona || '—'}</td>
      <td><span class="market-badge">${mkt}</span></td>
      <td><span class="score-badge ${scoreClass(s)}">${s.toFixed(0)}</span></td>
      <td style="font-size:0.75rem">${c.action_type || '—'}</td>
      <td style="white-space:normal;font-size:0.74rem;max-width:180px">${(c.why_priority||'').substring(0,100)}</td>
      <td>
        <div class="conf-bar-wrap"><div class="conf-bar-fill" style="width:${confPct}%"></div></div>
        <span style="font-size:0.72rem;color:var(--text-muted)">${confPct}%</span>
      </td>
      <td>${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">View</a>` : '—'}</td>
    </tr>`;
  }).join('');

  renderPagination();
}

function renderPagination() {
  const totalPages = Math.ceil(filteredContacts.length / PAGE_SIZE);
  const pg = document.getElementById('ct-pagination');
  let html = '';
  for (let i = 1; i <= Math.min(totalPages, 10); i++) {
    html += `<button class="page-btn ${i === contactsPage ? 'active' : ''}" onclick="goPage(${i})">${i}</button>`;
  }
  if (totalPages > 10) html += `<span style="color:var(--text-muted);font-size:0.8rem"> … ${totalPages} total</span>`;
  pg.innerHTML = html;
}

function goPage(n) {
  contactsPage = n;
  renderContactsTable();
}

// ── PAGE: Companies ───────────────────────────────────────────────────────────
function renderCompanies() {
  // Render first tab immediately
  renderCompanyChart('co-all');
}

function renderCompanyChart(tabId) {
  const intel = D.company_intel || {};
  const map = {
    'co-all':       { data: intel.all_companies || [],    canvasId: 'chart-co-all',   key: 'count' },
    'co-recruiting':{ data: intel.recruiting || [],       canvasId: 'chart-co-rec',   key: 'count' },
    'co-data':      { data: intel.data_companies || [],   canvasId: 'chart-co-data',  key: 'count' },
    'co-latam':     { data: intel.latam_usd || [],        canvasId: 'chart-co-latam', key: 'count' },
    'co-spain':     { data: intel.spain_eu || [],         canvasId: 'chart-co-spain', key: 'count' },
  };
  const cfg = map[tabId];
  if (!cfg) return;
  const sorted = [...cfg.data].sort((a, b) => (b[cfg.key]||0) - (a[cfg.key]||0)).slice(0, 20);
  const labels = sorted.map(d => d.company || d.company_clean || '');
  const values = sorted.map(d => d[cfg.key] || 0);
  makeBarChart(cfg.canvasId, labels, values, labels.map(() => '#3b82f6'), { horizontal: true });
}

// ── PAGE: Classify Unknown ────────────────────────────────────────────────────
function renderUnknown() {
  const rows = D.unknown_companies || [];
  const tbody = document.getElementById('unknown-tbody');
  tbody.innerHTML = rows.slice(0, 100).map(r =>
    `<tr>
      <td style="white-space:nowrap;font-weight:500">${r.company_clean || '—'}</td>
      <td><strong>${fmt(r.connection_count)}</strong></td>
      <td>${r.top_persona || '—'}</td>
      <td>${r.top_area || '—'}</td>
      <td><span class="score-badge ${scoreClass(r.avg_priority_score)}">${r.avg_priority_score || 0}</span></td>
      <td style="color:var(--text-muted);font-size:0.8rem">${r.suggested_market || '—'}</td>
    </tr>`
  ).join('');

  // Unknown by persona metrics
  const kpis = D.kpis || {};
  document.getElementById('unknown-persona-metrics').innerHTML = [
    makeCard('Unknown Recruiters',    kpis.unknown_recruiters    || 0, 'could be USD targets'),
    makeCard('Unknown TA',            kpis.unknown_ta            || 0, 'could be USD targets'),
    makeCard('Unknown Hiring Mgrs',   kpis.unknown_hiring_mgrs   || 0, 'could be USD targets'),
    makeCard('Unknown Data Leaders',  kpis.unknown_data_leaders  || 0, 'valuable referrers'),
    makeCard('Unknown Data Peers',    kpis.unknown_peers         || 0, 'peers, lower priority'),
  ].join('');
}

// ── PAGE: Data Quality ────────────────────────────────────────────────────────
function renderQuality() {
  const kpis = D.kpis || {};

  document.getElementById('quality-metrics').innerHTML = [
    makeCard('Unknown Market',       kpis.unknown_count || 0, (kpis.unknown_pct||0) + '%', kpis.unknown_pct > 70 ? 'bad' : 'warn'),
    makeCard('High Confidence',      kpis.high_confidence_count || 0, (kpis.data_confidence_score||0) + '%', 'good'),
    makeCard('Unknown Risk Score',   (kpis.unknown_market_risk_score||0) + '/100', '', kpis.unknown_market_risk_score > 60 ? 'bad' : 'warn'),
    makeCard('Actionable Contacts',  kpis.actionable_contacts || 0, 'high-conf + score ≥60'),
  ].join('');

  // Market type distribution chart
  const mtDist = kpis.market_type_distribution || {};
  if (Object.keys(mtDist).length > 0) {
    const labels = Object.keys(mtDist);
    const values = Object.values(mtDist);
    const colors = ['#3b82f6','#22c55e','#f59e0b','#a78bfa','#14b8a6','#4b5563'];
    makePieChart('chart-market-type', labels, values, colors.slice(0, labels.length));
  }
}
