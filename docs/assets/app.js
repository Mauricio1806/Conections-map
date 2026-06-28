/* LinkedIn Network Intelligence Dashboard — app.js (V3) */
'use strict';

// ── Globals ──────────────────────────────────────────────────────────────────
let D = null;
let charts = {};
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

const SCORE_COLORS = {
  Strong:       '#22c55e',
  Solid:        '#84cc16',
  Developing:   '#f59e0b',
  Building:     '#f59e0b',
  Ready:        '#22c55e',
  Early:        '#f97316',
  'Early Stage':'#f97316',
  'Not Started':'#ef4444',
};

// ── Boot ─────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  fetch('./assets/dashboard_data.json')
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(data => {
      D = data;
      document.getElementById('loading').style.display = 'none';
      document.getElementById('app').style.display     = 'flex';
      initNav();
      renderOverview();
      renderHeatmaps();
      renderGap();
      renderPlan();
      renderContacts();
      renderCompanies();
      renderUnknownResolution();
      renderLeads();
      renderQuality();
    })
    .catch(err => {
      document.getElementById('loading').innerHTML =
        '<div class="load-error"><h3>Failed to load dashboard data</h3>'
        + '<p>' + err.message + '</p>'
        + '<p style="font-size:0.8rem;opacity:0.6">If viewing as a local file, run: <code>python -m http.server --directory docs</code></p></div>';
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
      const pg = document.getElementById('page-' + page);
      if (pg) pg.classList.add('active');
    });
  });
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.dataset.tab;
      const parent = btn.closest('.tabs-container') || btn.parentElement;
      parent.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      const panel = document.getElementById(tabId);
      if (!panel) return;
      const pageEl = panel.closest('.page');
      if (pageEl) pageEl.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      panel.classList.add('active');
      if (tabId.startsWith('co-')) renderCompanyChart(tabId);
    });
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const kpi = (k, fb = 0) => D?.kpis?.[k] ?? fb;
const fmt = n => (n === null || n === undefined) ? '—' : (typeof n === 'number' ? n.toLocaleString() : n);

function scoreColorStyle(level) {
  return 'color:' + (SCORE_COLORS[level] || '#f59e0b');
}

function makeCard(title, value, sub = '', subClass = '') {
  return '<div class="card"><div class="card-title">' + title + '</div>'
       + '<div class="card-value">' + fmt(value) + '</div>'
       + (sub ? '<div class="card-sub ' + subClass + '">' + sub + '</div>' : '')
       + '</div>';
}

function makeScoreGauge(label, score, level, desc, next) {
  const color = SCORE_COLORS[level] || '#f59e0b';
  const pct   = Math.min(100, parseFloat(score) || 0);
  return '<div class="gauge-card">'
       + '<div class="gauge-label">' + label + '</div>'
       + '<div class="gauge-arc-wrap">'
       + '<svg viewBox="0 0 120 70" class="gauge-svg">'
       + '<path d="M10,65 A55,55 0 0,1 110,65" stroke="#1e2433" stroke-width="12" fill="none"/>'
       + '<path d="M10,65 A55,55 0 0,1 110,65" stroke="' + color + '" stroke-width="12" fill="none"'
       + ' stroke-dasharray="172.8" stroke-dashoffset="' + (172.8 - 172.8 * pct / 100).toFixed(1) + '"'
       + ' stroke-linecap="round"/>'
       + '<text x="60" y="58" text-anchor="middle" fill="' + color + '" font-size="22" font-weight="700">' + Math.round(pct) + '</text>'
       + '</svg>'
       + '</div>'
       + '<div class="gauge-level" style="' + scoreColorStyle(level) + '">' + level + '</div>'
       + '<div class="gauge-desc">' + (desc || '') + '</div>'
       + (next ? '<div class="gauge-next">' + next + '</div>' : '')
       + '</div>';
}

function urgencyBadge(u) {
  return '<span class="urgency-badge urgency-' + (u||'').toLowerCase() + '">' + (u||'—') + '</span>';
}

function marketBadge(m) {
  return '<span class="market-badge mkt-' + (m||'UNKNOWN').replace(/[^A-Z]/g, '') + '">' + (m||'UNKNOWN') + '</span>';
}

function scoreBar(val, max, color) {
  const pct = Math.min(100, (val / max) * 100);
  return '<div class="mini-bar-wrap"><div class="mini-bar-fill" style="width:' + pct.toFixed(1) + '%;background:' + color + '"></div></div>';
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function barChart(canvasId, labels, values, colors, opts) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const isH = opts && opts.horizontal;
  charts[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ data: values, backgroundColor: colors || '#3b82f6', borderRadius: 4, borderSkipped: false }] },
    options: {
      indexAxis: isH ? 'y' : 'x',
      responsive: true,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => ' ' + (isH ? c.parsed.x : c.parsed.y).toLocaleString() } } },
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: 10 } }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', font: { size: 10 } }, grid: { color: '#21262d' } }
      }
    }
  });
}

function doughnutChart(canvasId, labels, values, colors) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  charts[canvasId] = new Chart(ctx, {
    type: 'doughnut',
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderColor: '#0d1117', borderWidth: 2 }] },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'right', labels: { color: '#8b949e', font: { size: 10 }, boxWidth: 12, padding: 10 } },
        tooltip: { callbacks: { label: c => ' ' + c.label + ': ' + c.parsed.toLocaleString() } }
      }
    }
  });
}

// ── PAGE 1: Executive Overview ────────────────────────────────────────────────
function renderOverview() {
  // Data quality warning banner
  const unknownPct = kpi('unknown_pct');
  const mktConf    = kpi('market_confidence_score');
  const bannerEl   = document.getElementById('confidence-banner');
  if (unknownPct > 70) {
    bannerEl.innerHTML = '<div class="alert alert-warn">'
      + '<span class="alert-icon">&#9888;&#65039;</span>'
      + '<div><strong>Market Confidence is Low (' + mktConf + '/100)</strong> — '
      + unknownPct + '% of connections have unresolved market. '
      + 'Strategic Network Score and readiness scores are based on persona value, not market certainty. '
      + 'Go to the <strong>Unknown Resolution</strong> page to reduce UNKNOWN quickly.</div></div>';
  } else {
    bannerEl.innerHTML = '<div class="alert alert-good">'
      + '<span class="alert-icon">&#9989;</span>'
      + '<strong>Market Confidence: ' + mktConf + '/100</strong> — '
      + (100 - unknownPct).toFixed(1) + '% of connections have inferred market.</div>';
  }

  // 5 Score gauges
  const gauges = [
    { label: 'Strategic Network Score',    score: kpi('strategic_network_score'),    level: kpi('strategic_network_level','—'),    desc: kpi('strategic_network_desc',''),    next: '' },
    { label: 'USD Readiness Score',         score: kpi('usd_readiness_score'),         level: kpi('usd_readiness_level','—'),         desc: kpi('usd_readiness_desc',''),         next: kpi('usd_readiness_next','') },
    { label: 'Spain/EU Readiness Score',    score: kpi('spain_eu_readiness_score'),    level: kpi('spain_eu_readiness_level','—'),    desc: kpi('spain_eu_readiness_desc',''),    next: kpi('spain_eu_readiness_next','') },
    { label: 'Market Confidence Score',     score: kpi('market_confidence_score'),     level: mktConf >= 30 ? 'Solid' : mktConf >= 15 ? 'Early Stage' : 'Low',  desc: 'How much of your network has reliable market inference. Low = normal when UNKNOWN is high.', next: '' },
    { label: 'Global Opportunity Score',    score: kpi('global_opportunity_score'),    level: kpi('global_opportunity_score') >= 50 ? 'Strong' : 'Building', desc: 'GLOBAL_STAFFING, GLOBAL_TECH, GLOBAL_CONSULTING contacts — companies that hire anywhere.', next: '' },
  ];
  document.getElementById('gauges-row').innerHTML = gauges.map(g =>
    makeScoreGauge(g.label, g.score, g.level, g.desc, g.next)
  ).join('');

  // Diagnosis grid
  const sns   = kpi('strategic_network_score');
  const usd   = kpi('usd_readiness_score');
  const spain = kpi('spain_eu_readiness_score');
  const glob  = kpi('global_opportunity_score');
  const act   = kpi('actionable_contacts');
  const hvUnk = kpi('unknown_recruiters_highvalue') + kpi('unknown_hiring_mgrs_highvalue');
  const diagItems = [
    { cls: sns >= 60 ? 'good' : 'warn',  title: 'Network Strength',    text: sns >= 60 ? 'Your professional network is genuinely strong. Large pool of recruiters, hiring managers, and data leaders.' : 'Your network is building. Keep adding strategic personas.' },
    { cls: usd >= 35 ? 'good' : 'warn',  title: 'USD Job Readiness',   text: usd >= 35 ? 'USD network is developing. You have confirmed contacts in LATAM USD and US/Canada markets.' : 'USD readiness needs work. Add LATAM USD + US/Canada nearshore recruiters.' },
    { cls: spain >= 20 ? 'good' : 'info',title: 'Spain/EU Readiness',  text: spain >= 20 ? 'Early Spain/EU foundation exists. On track for 12-month relocation timeline.' : 'Spain/EU network is nascent — normal for early planning phase.' },
    { cls: glob >= 30 ? 'good' : 'warn', title: 'Global Opportunities', text: 'You have ' + kpi('global_opportunity_total') + ' contacts at GLOBAL_STAFFING, GLOBAL_TECH, and GLOBAL_CONSULTING companies — these can hire anywhere.' },
    { cls: 'info', title: 'UNKNOWN (' + unknownPct + '%)', text: 'UNKNOWN means market was not inferred — NOT that contacts are worthless. You have ' + hvUnk + ' high-value UNKNOWN recruiters/hiring managers. Go to Unknown Resolution to classify them.' },
    { cls: act >= 100 ? 'good' : 'warn', title: 'Actionable Contacts', text: act + ' contacts have priority score >= 60. These are your outreach targets. See the Top Contacts page.' },
  ];
  document.getElementById('diagnosis-grid').innerHTML = diagItems.map(d =>
    '<div class="diag-item ' + d.cls + '"><h4>' + d.title + '</h4><p>' + d.text + '</p></div>'
  ).join('');

  // KPI Metrics rows
  document.getElementById('kpi-size').innerHTML = [
    makeCard('Total Connections',   kpi('total_connections')),
    makeCard('High Priority',       kpi('high_priority'),   kpi('high_priority_pct') + '%', 'good'),
    makeCard('Medium Priority',     kpi('medium_priority'), kpi('medium_priority_pct') + '%', 'warn'),
    makeCard('Actionable',          kpi('actionable_contacts'), 'score ≥ 60'),
    makeCard('Global Opps',         kpi('global_opportunity_total'), 'GLOBAL_STAFFING/TECH/CONS'),
  ].join('');

  document.getElementById('kpi-personas').innerHTML = [
    makeCard('Recruiters',        kpi('recruiters_total')),
    makeCard('Talent / HR',       kpi('talent_hr_total')),
    makeCard('Hiring Managers',   kpi('hiring_managers_total')),
    makeCard('Data Leaders',      kpi('data_leaders_total')),
    makeCard('Data Peers',        kpi('data_peers_total')),
  ].join('');

  document.getElementById('kpi-markets').innerHTML = [
    makeCard('Brazil',            kpi('brazil_count')),
    makeCard('LATAM USD',         kpi('latam_usd_count')),
    makeCard('US/CA Nearshore',   kpi('us_nearshore_count')),
    makeCard('Spain/EU',          kpi('spain_eu_count')),
    makeCard('Europe',            kpi('europe_count')),
    makeCard('Global Staffing',   kpi('global_staffing_count')),
    makeCard('Global Tech',       kpi('global_tech_count')),
    makeCard('Global Consulting', kpi('global_consulting_count')),
    makeCard('Unknown',           kpi('unknown_count'), kpi('unknown_pct') + '%', 'warn'),
  ].join('');

  // Lead reactivation row (additive — only shown when message data is available)
  const lr = D.lead_reactivation || {};
  const lrRow = document.getElementById('kpi-lead-reactivation');
  const lrLabel = document.getElementById('kpi-lead-reactivation-label');
  if (lrRow) {
    if (lr.messages_csv_available && lr.total_conversations) {
      lrRow.style.display = '';
      if (lrLabel) lrLabel.style.display = '';
      lrRow.innerHTML = [
        makeCard('This Week Queue',   lr.this_week_count           || 0, 'action target', 'good'),
        makeCard('Needs My Response', lr.needs_my_response         || 0, 'reply now',     'bad'),
        makeCard('Hot Reactivation',  lr.hot_reactivation_leads    || lr.hot_leads  || 0, 'positive signal + recruiter', 'good'),
        makeCard('Warm Reactivation', lr.warm_reactivation_leads   || lr.warm_leads || 0, 'opportunity signals', 'warn'),
        makeCard('Career Site',       lr.career_site_follow_ups    || 0, 'submit CV'),
        makeCard('Follow-ups Due',    lr.follow_up_due             || 0, '7-120d window'),
      ].join('');
    } else {
      lrRow.style.display = 'none';
      if (lrLabel) lrLabel.style.display = 'none';
    }
  }

  // Market doughnut
  const mktDist   = D.market_distribution || {};
  const mktLabels = Object.keys(mktDist);
  const mktValues = Object.values(mktDist);
  const mktColors = mktLabels.map(m => MARKET_COLORS[m] || '#555');
  doughnutChart('chart-market', mktLabels, mktValues, mktColors);

  // Persona bar
  const persDist  = D.persona_distribution || {};
  const persL = Object.keys(persDist);
  const persV = Object.values(persDist);
  barChart('chart-personas', persL, persV, persL.map(() => '#3b82f6'), { horizontal: true });

  // Flags
  const flags = kpi('concentration_flags', []);
  document.getElementById('flags-list').innerHTML = (Array.isArray(flags) ? flags : [flags])
    .map(f => {
      const cls  = f.includes('No critical') ? 'alert-good' : (f.startsWith('HIGH') ? 'alert-bad' : 'alert-warn');
      const icon = f.includes('No critical') ? '&#9989;' : '&#9888;&#65039;';
      return '<div class="alert ' + cls + '"><span class="alert-icon">' + icon + '</span><span>' + f + '</span></div>';
    }).join('');
}

// ── PAGE 2: Heatmap ───────────────────────────────────────────────────────────
function renderHeatmaps() {
  const hm = D.heatmaps || {};
  function buildHeatmap(tableId, data) {
    const tbl = document.getElementById(tableId);
    if (!tbl || !data || !data.labels) { if (tbl) tbl.innerHTML = '<tr><td>No data</td></tr>'; return; }
    const excludeUnk = document.getElementById('hm-excl-unk')?.checked;
    let cols   = data.columns || [];
    let colIdx = cols.map((c, i) => i);
    if (excludeUnk) colIdx = colIdx.filter(i => cols[i] !== 'UNKNOWN');
    const filtCols = colIdx.map(i => cols[i]);
    let maxVal = 0;
    data.data.forEach(row => colIdx.forEach(i => { if (row[i] > maxVal) maxVal = row[i]; }));
    let html = '<thead><tr><th></th>';
    filtCols.forEach(c => { html += '<th style="white-space:nowrap;font-size:0.75rem">' + c + '</th>'; });
    html += '</tr></thead><tbody>';
    data.labels.forEach((lbl, ri) => {
      const row = data.data[ri] || [];
      html += '<tr><td style="white-space:nowrap;font-weight:500;font-size:0.8rem">' + lbl + '</td>';
      colIdx.forEach(ci => {
        const v   = row[ci] || 0;
        const pct = maxVal > 0 ? v / maxVal : 0;
        const bg  = v > 0 ? 'rgba(59,130,246,' + (0.1 + pct * 0.75).toFixed(2) + ')' : 'transparent';
        html += '<td><span class="hm-cell" style="background:' + bg + '">' + (v > 0 ? v.toLocaleString() : '—') + '</span></td>';
      });
      html += '</tr>';
    });
    html += '</tbody>';
    tbl.innerHTML = html;
  }

  function rebuildAll() {
    buildHeatmap('hm-persona-market',   hm.persona_market);
    buildHeatmap('hm-area-market',      hm.area_market);
    buildHeatmap('hm-seniority-market', hm.seniority_market);
    buildHeatmap('hm-persona-priority', hm.persona_priority);
  }
  rebuildAll();
  document.getElementById('hm-excl-unk')?.addEventListener('change', rebuildAll);
}

// ── PAGE 3: Strategic Gap ─────────────────────────────────────────────────────
function renderGap() {
  const gap = D.gap_analysis || [];
  filteredGap = [...gap];

  const markets = [...new Set(gap.map(r => r.market || ''))].sort();
  const mf = document.getElementById('gap-market-filter');
  if (mf) markets.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; mf.appendChild(o); });

  renderGapTable();
  renderGapChart();
}

window.applyGapFilters = function() {
  const urg = document.getElementById('gap-urgency-filter')?.value || '';
  const mkt = document.getElementById('gap-market-filter')?.value  || '';
  filteredGap = (D.gap_analysis || []).filter(r =>
    (!urg || r.urgency_level === urg) && (!mkt || r.market === mkt)
  );
  renderGapTable(); renderGapChart();
};

window.resetGapFilters = function() {
  const u = document.getElementById('gap-urgency-filter');
  const m = document.getElementById('gap-market-filter');
  if (u) u.value = ''; if (m) m.value = '';
  filteredGap = [...(D.gap_analysis || [])];
  renderGapTable(); renderGapChart();
};

function renderGapTable() {
  const st = document.getElementById('gap-stats');
  if (st) st.textContent = 'Showing ' + filteredGap.length + ' rows';
  const tbody = document.getElementById('gap-tbody');
  if (!tbody) return;
  tbody.innerHTML = filteredGap.map(r =>
    '<tr>'
    + '<td>' + marketBadge(r.market) + '</td>'
    + '<td style="white-space:nowrap">' + (r.persona||'') + '</td>'
    + '<td>' + fmt(r.current_count) + '</td>'
    + '<td>' + fmt(r.target_count) + '</td>'
    + '<td><strong style="color:#ef4444">' + fmt(r.gap_count) + '</strong></td>'
    + '<td>' + fmt(r.gap_percentage) + '%</td>'
    + '<td>' + urgencyBadge(r.urgency_level) + '</td>'
    + '<td>' + (r.timeframe||'') + '</td>'
    + '<td style="white-space:normal;font-size:0.74rem;max-width:200px">' + ((r.strategic_reason||'').substring(0,100)) + '</td>'
    + '</tr>'
  ).join('');
}

function renderGapChart() {
  const sorted = [...filteredGap].sort((a,b)=>(b.gap_count||0)-(a.gap_count||0)).slice(0,15);
  const labels = sorted.map(r => (r.market||'').replace('_',' ') + ' — ' + (r.persona||''));
  const values = sorted.map(r => r.gap_count || 0);
  const colors = sorted.map(r => URGENCY_COLORS[r.urgency_level] || '#555');
  barChart('chart-gap', labels, values, colors, { horizontal: true });
}

// ── PAGE 4: Action Plan ───────────────────────────────────────────────────────
function renderPlan() {
  const queries = {
    LATAM_USD:           '"data engineer" "LATAM" OR "latin america" recruiter',
    US_CANADA_NEARSHORE: '"data engineer" "nearshore" OR "remote" USA OR Canada recruiter',
    SPAIN_EU:            '"data engineer" Spain OR Madrid OR Barcelona recruiter',
    EUROPE:              '"data engineer" Europe OR Germany OR Netherlands recruiter',
    GLOBAL_STAFFING:     '"data engineer" staffing OR nearshore recruiter',
  };

  // 7-day sprint hardcoded (based on strategic priorities)
  const sprint = [
    { day: 'Mon', action: 'Search LATAM USD recruiters, send 7 connection requests', target: '+7 LATAM USD recruiters', market: 'LATAM_USD', dms: 0, connects: 7, comments: 0, query: queries.LATAM_USD, angle: 'Pitch as nearshore Data Engineer open to remote USD roles' },
    { day: 'Tue', action: 'Search US/Canada nearshore recruiters, send 7 requests', target: '+7 US/CA recruiters', market: 'US_CANADA_NEARSHORE', dms: 0, connects: 7, comments: 0, query: queries.US_CANADA_NEARSHORE, angle: 'Mention LATAM/nearshore experience' },
    { day: 'Wed', action: 'Classify 20 companies in company_override_candidates.csv', target: '+20 resolved companies', market: 'ALL', dms: 0, connects: 0, comments: 0, query: '—', angle: 'Open outputs/company_override_candidates.csv, fill manual_market' },
    { day: 'Thu', action: 'Message top 10 from action_backlog.csv (score >= 70)', target: '10 personalized DMs', market: 'ALL', dms: 10, connects: 0, comments: 0, query: '—', angle: 'Use message_angle column from backlog' },
    { day: 'Fri', action: 'Post LinkedIn content on Data Engineering topic', target: '1 post, recruiter reach', market: 'ALL', dms: 0, connects: 0, comments: 5, query: '—', angle: 'Position as LATAM nearshore Data Engineer expert' },
    { day: 'Sat', action: 'Search Spain/EU recruiters: ERNI, Stratesys, Capgemini Spain', target: '+3 Spain connections', market: 'SPAIN_EU', dms: 0, connects: 3, comments: 2, query: queries.SPAIN_EU, angle: 'Planning Spain relocation — connect early' },
    { day: 'Sun', action: 'Run full pipeline, review new connections, update CSV', target: 'Pipeline refreshed', market: 'ALL', dms: 0, connects: 0, comments: 0, query: '—', angle: 'Weekly hygiene — keep data fresh' },
  ];

  document.getElementById('sprint-grid').innerHTML = sprint.map(s =>
    '<div class="sprint-card">'
    + '<div class="sprint-day">' + s.day + '</div>'
    + '<div class="sprint-action">' + s.action + '</div>'
    + '<div class="sprint-target">' + s.target + '</div>'
    + (s.connects ? '<div class="sprint-meta">Connects: ' + s.connects + '</div>' : '')
    + (s.dms      ? '<div class="sprint-meta">DMs: ' + s.dms + '</div>' : '')
    + (s.query !== '—' ? '<div class="sprint-query">Search: ' + s.query + '</div>' : '')
    + '<div class="sprint-angle">' + s.angle + '</div>'
    + '</div>'
  ).join('');

  function makePlanGrid(plans, gridId) {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    grid.innerHTML = plans.slice(0, 20).map(r => {
      const gap     = r.gap_count || 0;
      const weekly  = Math.max(1, Math.ceil(Math.min(gap, 80) / 4));
      const mktKey  = (r.market || '').replace(/\s/g,'_');
      const query   = queries[mktKey] || ('"' + (r.persona||'') + '" "' + (r.market||'') + '"');
      return '<div class="plan-card ' + (r.urgency_level||'').toLowerCase() + '">'
        + '<div class="plan-card-header">'
        + '<div>'
        + '<div class="plan-card-title">' + (r.market||'') + ' — ' + (r.persona||'') + '</div>'
        + '<div class="plan-card-meta">' + (r.timeframe||'') + '</div>'
        + '</div>'
        + urgencyBadge(r.urgency_level)
        + '</div>'
        + '<div class="plan-targets">'
        + '<div class="plan-t"><div class="plan-n">' + fmt(r.current_count) + '</div><div class="plan-l">have</div></div>'
        + '<div class="plan-t"><div class="plan-n">' + fmt(r.target_count) + '</div><div class="plan-l">target</div></div>'
        + '<div class="plan-t"><div class="plan-n" style="color:#ef4444">' + fmt(gap) + '</div><div class="plan-l">gap</div></div>'
        + '<div class="plan-t"><div class="plan-n" style="color:#14b8a6">' + weekly + '/wk</div><div class="plan-l">connects</div></div>'
        + '</div>'
        + '<div class="plan-reason">' + (r.strategic_reason||'').substring(0,140) + '</div>'
        + '<div class="plan-query">Search: ' + query + '</div>'
        + '</div>';
    }).join('');
  }

  makePlanGrid(D.action_plan_30 || [], 'plan-30-grid');
  makePlanGrid(D.action_plan_60 || [], 'plan-60-grid');
  makePlanGrid(D.action_plan_90 || [], 'plan-90-grid');
}

// ── PAGE 5: Top Contacts ──────────────────────────────────────────────────────
function renderContacts() {
  const contacts = D.top_contacts || [];
  const personas = [...new Set(contacts.map(c => c.persona||''))].sort();
  const markets  = [...new Set(contacts.map(c => c.market_v2 || c.strategic_market || ''))].sort();
  const pf = document.getElementById('ct-persona-filter');
  const mf = document.getElementById('ct-market-filter');
  personas.forEach(p => { const o = document.createElement('option'); o.value = p; o.textContent = p; pf && pf.appendChild(o); });
  markets.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; mf && mf.appendChild(o); });
  filteredContacts = contacts;
  renderContactsTable();
}

window.applyContactFilters = function() {
  const minS   = parseFloat(document.getElementById('ct-min-score')?.value) || 0;
  const per    = document.getElementById('ct-persona-filter')?.value || '';
  const mkt    = document.getElementById('ct-market-filter')?.value  || '';
  const band   = document.getElementById('ct-band-filter')?.value    || '';
  filteredContacts = (D.top_contacts || []).filter(c => {
    const s = parseFloat(c.priority_score) || 0;
    const m = c.market_v2 || c.strategic_market || '';
    if (s < minS) return false;
    if (per && c.persona !== per) return false;
    if (mkt && m !== mkt) return false;
    if (band === 'high'   && s < 70)           return false;
    if (band === 'medium' && (s < 40 || s >= 70)) return false;
    if (band === 'low'    && s >= 40)          return false;
    return true;
  });
  contactsPage = 1;
  renderContactsTable();
};

window.resetContactFilters = function() {
  const ms = document.getElementById('ct-min-score');    if (ms) ms.value = '60';
  const pf = document.getElementById('ct-persona-filter');if (pf) pf.value = '';
  const mf = document.getElementById('ct-market-filter');if (mf) mf.value = '';
  const bf = document.getElementById('ct-band-filter');   if (bf) bf.value = '';
  filteredContacts = D.top_contacts || [];
  contactsPage = 1;
  renderContactsTable();
};

function renderContactsTable() {
  const start = (contactsPage - 1) * PAGE_SIZE;
  const slice = filteredContacts.slice(start, start + PAGE_SIZE);
  const st = document.getElementById('ct-stats');
  if (st) st.textContent = 'Showing ' + (start+1) + '–' + Math.min(start + PAGE_SIZE, filteredContacts.length) + ' of ' + filteredContacts.length;
  const tbody = document.getElementById('ct-tbody');
  if (!tbody) return;
  tbody.innerHTML = slice.map(c => {
    const s   = parseFloat(c.priority_score) || 0;
    const mkt = c.market_v2 || c.strategic_market || 'UNKNOWN';
    const cf  = parseFloat(c.market_confidence_v2) || 0;
    const url = c.url || '';
    const sClass = s >= 70 ? 'score-high' : s >= 40 ? 'score-med' : 'score-low';
    return '<tr>'
      + '<td style="white-space:nowrap">' + (c.full_name||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (c.company_clean||'—') + '</td>'
      + '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (c.position_clean||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (c.persona||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (c.seniority||'—') + '</td>'
      + '<td>' + marketBadge(mkt) + '</td>'
      + '<td><span class="score-badge ' + sClass + '">' + s.toFixed(0) + '</span></td>'
      + '<td style="font-size:0.73rem">' + (c.action_type||'—') + '</td>'
      + '<td style="white-space:normal;font-size:0.72rem;max-width:200px">' + ((c.why_priority||'').substring(0,100)) + '</td>'
      + '<td>' + (url ? '<a href="' + url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
      + '</tr>';
  }).join('');
  renderContactPagination();
}

function renderContactPagination() {
  const total = Math.ceil(filteredContacts.length / PAGE_SIZE);
  const pg    = document.getElementById('ct-pagination');
  if (!pg) return;
  let html = '';
  for (let i = 1; i <= Math.min(total, 8); i++) {
    html += '<button class="pg-btn' + (i === contactsPage ? ' active' : '') + '" onclick="goPage(' + i + ')">' + i + '</button>';
  }
  if (total > 8) html += '<span style="color:var(--text-muted);font-size:0.8rem"> … ' + total + ' pages</span>';
  pg.innerHTML = html;
}

window.goPage = function(n) { contactsPage = n; renderContactsTable(); };

// ── PAGE 6: Company Intelligence ──────────────────────────────────────────────
function renderCompanies() { renderCompanyChart('co-all'); }

function renderCompanyChart(tabId) {
  const intel = D.company_intel || {};
  const map = {
    'co-all':       { data: intel.all_companies     || [], id: 'chart-co-all' },
    'co-recruiting':{ data: intel.recruiting        || [], id: 'chart-co-rec' },
    'co-data':      { data: intel.data_companies    || [], id: 'chart-co-data' },
    'co-staffing':  { data: intel.global_staffing   || [], id: 'chart-co-staff' },
    'co-tech':      { data: intel.global_tech       || [], id: 'chart-co-tech' },
    'co-consulting':{ data: intel.global_consulting || [], id: 'chart-co-cons' },
    'co-latam':     { data: intel.latam_usd         || [], id: 'chart-co-latam' },
    'co-spain':     { data: intel.spain_eu          || [], id: 'chart-co-spain' },
  };
  const cfg = map[tabId];
  if (!cfg) return;
  const sorted = [...cfg.data].sort((a,b) => (b.count||0)-(a.count||0)).slice(0,20);
  barChart(cfg.id,
    sorted.map(d => d.company||''),
    sorted.map(d => d.count||0),
    sorted.map(() => '#3b82f6'),
    { horizontal: true }
  );
}

// ── PAGE 7: Unknown Resolution ────────────────────────────────────────────────
function renderUnknownResolution() {
  const res   = D.unknown_resolution || {};
  const total = res.total_unknown_contacts || kpi('unknown_count');
  const hvUnk = res.high_value_unknown_contacts || 0;
  const top25 = res.top25_coverage || kpi('top25_company_coverage');
  const top25pct = res.top25_pct_of_unknown || kpi('unknown_resolution_potential');
  const autoRes  = res.auto_resolvable_contacts || 0;

  document.getElementById('unk-metrics').innerHTML = [
    makeCard('Total UNKNOWN',         total,   kpi('unknown_pct') + '% of network'),
    makeCard('High-Value UNKNOWN',    hvUnk,   'recruiters + hiring mgrs with score ≥60', 'warn'),
    makeCard('UNKNOWN Recruiters',    kpi('unknown_recruiters_highvalue'), 'score ≥60'),
    makeCard('UNKNOWN Hiring Mgrs',   kpi('unknown_hiring_mgrs_highvalue'), 'score ≥50'),
    makeCard('UNKNOWN Data Leaders',  kpi('unknown_data_leaders_highvalue'), 'score ≥50'),
  ].join('');

  document.getElementById('unk-resolution-metrics').innerHTML = [
    makeCard('Top 25 Companies Cover',top25,    top25pct + '% of UNKNOWN', 'good'),
    makeCard('Auto-Resolvable',       autoRes,  'via keyword + heuristics', 'good'),
    makeCard('Unknown Resolution Score', kpi('unknown_resolution_score') + '/100', ''),
  ].join('');

  // Top 25 companies table
  const top25Companies = (res.top25_companies && Array.isArray(res.top25_companies))
    ? res.top25_companies
    : (D.unknown_companies || []).slice(0, 25);
  const tbody = document.getElementById('unk-companies-tbody');
  if (tbody) {
    if (!top25Companies.length) {
      tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-muted)">No UNKNOWN companies found.</td></tr>';
    } else {
    tbody.innerHTML = top25Companies.map((r, i) =>
      '<tr>'
      + '<td><strong>#' + (i+1) + '</strong></td>'
      + '<td style="font-weight:500">' + (r.company_clean||'') + '</td>'
      + '<td><strong>' + fmt(r.connection_count) + '</strong></td>'
      + '<td>' + fmt(r.recruiter_count||0) + '</td>'
      + '<td>' + fmt(r.talent_count||0) + '</td>'
      + '<td>' + fmt(r.hiring_manager_count||0) + '</td>'
      + '<td>' + fmt(r.data_leader_count||0) + '</td>'
      + '<td>' + (Number(r.avg_priority_score||0).toFixed(0)) + '</td>'
      + '<td>' + marketBadge(r.suggested_market||'UNKNOWN') + '</td>'
      + '<td style="font-size:0.72rem;max-width:200px">' + String(r.suggested_reason||'').substring(0,80) + '</td>'
      + '</tr>'
    ).join('');
    } // end if top25Companies.length
  }

  // Unknown persona metrics
  document.getElementById('unk-persona-metrics').innerHTML = [
    makeCard('Unknown Recruiters (any)', kpi('sns_recruiters') > 0 ? kpi('unknown_count') + ' total' : '—'),
    makeCard('UNKNOWN Rec. (score≥60)',  kpi('unknown_recruiters_highvalue'), 'potential USD pipeline'),
    makeCard('UNKNOWN Hiring Mgr',       kpi('unknown_hiring_mgrs_highvalue'), 'potential direct hire'),
    makeCard('UNKNOWN Data Leaders',     kpi('unknown_data_leaders_highvalue'), 'referral network'),
    makeCard('UNKNOWN Data Peers',       kpi('unknown_peers'), 'lowest priority'),
  ].join('');
}

// ── PAGE 8: Lead Reactivation ─────────────────────────────────────────────────
let filteredLeads = [];
const LEAD_PAGE_SIZE = 50;

const TEMP_COLORS = {
  Hot:     '#ef4444',
  Warm:    '#f97316',
  Neutral: '#f59e0b',
  Cold:    '#3b82f6',
  Ignore:  '#4b5563',
};

const STATUS_ICONS = {
  'Needs my response':              '&#128233;',
  'Follow-up due':                  '&#9203;',
  'Warm lead':                      '&#128293;',
  'Dormant warm lead':              '&#128564;',
  'Auto-reply / career site redirect': '&#129302;',
  'Rejected / closed process':      '&#10060;',
  'No response':                    '&#128260;',
  'Low value / ignore':             '&#128374;',
};

function tempBadge(t) {
  const c = TEMP_COLORS[t] || '#555';
  return '<span class="urgency-badge" style="background:' + c + '20;color:' + c + ';border:1px solid ' + c + '">' + (t||'—') + '</span>';
}

function renderLeads() {
  const lr = D.lead_reactivation || {};
  const noData = document.getElementById('leads-no-data');
  const mainContent = document.getElementById('leads-main-content');

  // Only show no-data banner if there truly is no data (check contacts too,
  // to avoid hiding data that was preserved from a previous local build)
  const hasContacts = (lr.top_reactivation_contacts || []).length > 0
                   || (lr.this_week_contacts || []).length > 0;
  const genuinelyEmpty = !lr.total_conversations && !hasContacts;

  if (genuinelyEmpty) {
    if (noData) noData.style.display = '';
    if (mainContent) mainContent.style.display = 'none';
    return;
  }
  if (noData) noData.style.display = 'none';
  if (mainContent) mainContent.style.display = '';

  // ── Summary cards ─────────────────────────────────────────────────────────
  const sumEl = document.getElementById('leads-summary');
  if (sumEl) sumEl.innerHTML = [
    makeCard('Conversations Analyzed', lr.total_conversations || 0),
    makeCard('Needs My Response', lr.needs_my_response || 0, 'reply first', 'bad'),
    makeCard('This Week Queue',   lr.this_week_count   || 0, 'weekly action limit', 'warn'),
    makeCard('Follow-ups Due',    lr.follow_up_due     || 0, '7-120d, positive signal'),
  ].join('');

  const pipeEl = document.getElementById('leads-pipeline');
  if (pipeEl) pipeEl.innerHTML = [
    makeCard('Hot Reactivation',  lr.hot_reactivation_leads  || lr.hot_leads  || 0, 'needs response + positive signal', 'good'),
    makeCard('Warm Reactivation', lr.warm_reactivation_leads || lr.warm_leads || 0, 'opportunity signals found', 'good'),
    makeCard('Career Site',       lr.career_site_follow_ups  || 0, 'auto-reply → submit CV'),
    makeCard('Dormant Warm',      lr.dormant_warm_leads      || 0, 'positive but >30d ago', 'warn'),
    makeCard('Rejected / Closed', lr.rejected_closed_reusable || 0, 'reusable for future'),
    makeCard('No Response',       lr.no_response_leads        || 0, 'sent, no reply'),
  ].join('');

  // ── This Week queue (shown first) ─────────────────────────────────────────
  const thisWeekSection = document.getElementById('leads-this-week');
  const thisWeekTbody   = document.getElementById('leads-this-week-tbody');
  if (thisWeekTbody) {
    const tw = lr.this_week_contacts || [];
    if (!tw.length) {
      if (thisWeekSection) thisWeekSection.style.display = 'none';
    } else {
      if (thisWeekSection) thisWeekSection.style.display = '';
      thisWeekTbody.innerHTML = tw.map((r, i) => {
        const url   = r.other_person_profile_url || '';
        const score = parseInt(r.reactivation_priority_score) || 0;
        const sCls  = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
        const icon  = STATUS_ICONS[r.conversation_status] || '';
        return '<tr>'
          + '<td><strong>#' + (i+1) + '</strong></td>'
          + '<td style="white-space:nowrap">' + (r.other_person_name||'—') + '</td>'
          + '<td>' + (r.company_clean||'—') + '</td>'
          + '<td style="white-space:nowrap">' + (r.persona||'—') + '</td>'
          + '<td style="font-size:0.78rem">' + (r.lead_category||'—') + '</td>'
          + '<td>' + tempBadge(r.lead_temperature||'—') + '</td>'
          + '<td style="font-size:0.75rem">' + icon + ' ' + (r.conversation_status||'—') + '</td>'
          + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
          + '<td style="font-size:0.72rem;max-width:200px">' + String(r.recommended_next_action||'').substring(0,80) + '</td>'
          + '<td>' + (url ? '<a href="' + url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
          + '</tr>';
      }).join('');
    }
  }

  // ── Needs reply table ─────────────────────────────────────────────────────
  const replyTbody = document.getElementById('leads-reply-tbody');
  if (replyTbody) {
    const replies = lr.needs_reply_contacts || [];
    if (!replies.length) {
      replyTbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">No contacts waiting for your reply.</td></tr>';
    } else {
      replyTbody.innerHTML = replies.map((r, i) => {
        const url = r.other_person_profile_url || '';
        return '<tr>'
          + '<td><strong>#' + (i+1) + '</strong></td>'
          + '<td style="white-space:nowrap">' + (r.other_person_name||'—') + '</td>'
          + '<td>' + (r.company_clean||'—') + '</td>'
          + '<td style="white-space:nowrap">' + (r.persona||'—') + '</td>'
          + '<td><span class="score-badge score-high">' + (r.reactivation_priority_score||0) + '</span></td>'
          + '<td style="font-size:0.72rem;max-width:260px">' + String(r.message_angle||'').substring(0,120) + '</td>'
          + '<td>' + (url ? '<a href="' + url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
          + '</tr>';
      }).join('');
    }
  }

  // ── Full backlog contacts table ───────────────────────────────────────────
  filteredLeads = lr.top_reactivation_contacts || [];
  renderLeadsTable();

  // ── Weekly plan ───────────────────────────────────────────────────────────
  const planEl = document.getElementById('leads-weekly-plan');
  if (planEl && lr.weekly_action_plan) {
    planEl.innerHTML = Object.entries(lr.weekly_action_plan).map(([day, action]) =>
      '<div class="sprint-card">'
      + '<div class="sprint-day">' + day + '</div>'
      + '<div class="sprint-action">' + action + '</div>'
      + '</div>'
    ).join('');
  }
}

window.applyLeadFilters = function() {
  const temp    = document.getElementById('lead-temp-filter')?.value   || '';
  const status  = document.getElementById('lead-status-filter')?.value || '';
  const recOnly = document.getElementById('lead-recruiter-only')?.checked || false;
  const contacts = (D.lead_reactivation || {}).top_reactivation_contacts || [];
  filteredLeads = contacts.filter(c => {
    if (temp   && c.lead_temperature    !== temp)   return false;
    if (status && c.conversation_status !== status) return false;
    if (recOnly && !['Recruiter','Talent Acquisition','Sourcer','Hiring Manager','Engineering Manager'].includes(c.persona)) return false;
    return true;
  });
  renderLeadsTable();
};

window.resetLeadFilters = function() {
  const t = document.getElementById('lead-temp-filter');    if (t) t.value = '';
  const s = document.getElementById('lead-status-filter'); if (s) s.value = '';
  const r = document.getElementById('lead-recruiter-only'); if (r) r.checked = false;
  filteredLeads = (D.lead_reactivation || {}).top_reactivation_contacts || [];
  renderLeadsTable();
};

function renderLeadsTable() {
  const st = document.getElementById('leads-stats');
  if (st) st.textContent = 'Showing ' + filteredLeads.length + ' contacts';
  const tbody = document.getElementById('leads-tbody');
  if (!tbody) return;
  if (!filteredLeads.length) {
    tbody.innerHTML = '<tr><td colspan="14" style="text-align:center;color:var(--text-muted)">No contacts match the current filters.</td></tr>';
    return;
  }
  tbody.innerHTML = filteredLeads.map((r, i) => {
    const url    = r.other_person_profile_url || '';
    const score  = parseInt(r.reactivation_priority_score) || 0;
    const sCls   = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
    const icon   = STATUS_ICONS[r.conversation_status] || '';
    return '<tr>'
      + '<td><strong>#' + (i+1) + '</strong></td>'
      + '<td style="white-space:nowrap">' + (r.other_person_name||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (r.company_clean||'—') + '</td>'
      + '<td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (r.position_clean||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (r.persona||'—') + '</td>'
      + '<td>' + marketBadge(r.strategic_market||'UNKNOWN') + '</td>'
      + '<td style="font-size:0.75rem">' + (r.lead_category||'—') + '</td>'
      + '<td style="white-space:nowrap;font-size:0.78rem">' + icon + ' ' + (r.conversation_status||'—') + '</td>'
      + '<td>' + tempBadge(r.lead_temperature||'—') + '</td>'
      + '<td style="white-space:nowrap;font-size:0.78rem">' + (r.last_message_date||'—') + '</td>'
      + '<td style="text-align:center">' + (r.days_since_last_message||'—') + '</td>'
      + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
      + '<td style="font-size:0.72rem;max-width:180px">' + String(r.recommended_next_action||'').substring(0,80) + '</td>'
      + '<td>' + (url ? '<a href="' + url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
      + '</tr>';
  }).join('');
}

// ── PAGE 9: Data Quality ──────────────────────────────────────────────────────
function renderQuality() {
  const mktConf = kpi('market_confidence_score');
  const risk    = kpi('data_quality_risk_score');
  const unkPct  = kpi('unknown_pct');

  document.getElementById('quality-metrics').innerHTML = [
    makeCard('Market Confidence', mktConf + '/100', mktConf < 30 ? 'Low — normal for LinkedIn exports' : 'Adequate'),
    makeCard('Data Quality Risk',  risk + '/100',   risk > 70 ? 'High — classify companies' : 'Moderate', risk > 70 ? 'bad' : 'warn'),
    makeCard('Unknown Market',     kpi('unknown_count'), unkPct + '% of network'),
    makeCard('Market Known %',     kpi('market_known_pct') + '%', kpi('market_known_count') + ' connections'),
  ].join('');

  // Market type distribution chart
  const mtDist = kpi('market_type_distribution', {});
  if (typeof mtDist === 'object' && Object.keys(mtDist).length > 0) {
    const ls = Object.keys(mtDist);
    const vs = Object.values(mtDist);
    const cs = ['#3b82f6','#22c55e','#f59e0b','#a78bfa','#14b8a6','#4b5563'];
    doughnutChart('chart-mkt-type', ls, vs, cs.slice(0, ls.length));
  }
}
