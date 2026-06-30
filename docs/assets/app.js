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
  // V2 / legacy
  BRAZIL:             '#22c55e',
  LATAM_USD:          '#f59e0b',
  US_CANADA_NEARSHORE:'#3b82f6',
  SPAIN_EU:           '#ef4444',
  EUROPE:             '#a78bfa',
  GLOBAL_STAFFING:    '#14b8a6',
  GLOBAL_TECH:        '#38bdf8',
  GLOBAL_CONSULTING:  '#fb923c',
  UNKNOWN:            '#4b5563',
  // V5 Opportunity Market buckets
  BRAZIL_CONFIRMED:          '#16a34a',
  BRAZIL_LIKELY:             '#4ade80',
  LATAM_USD_CONFIRMED:       '#d97706',
  LATAM_USD_LIKELY:          '#fbbf24',
  US_CANADA_CONFIRMED:       '#2563eb',
  US_CANADA_LIKELY:          '#60a5fa',
  SPAIN_EU_CONFIRMED:        '#dc2626',
  SPAIN_EU_LIKELY:           '#f87171',
  EUROPE_CONFIRMED:          '#7c3aed',
  EUROPE_LIKELY:             '#c4b5fd',
  GLOBAL_OPPORTUNITY:        '#8b5cf6',
  LANGUAGE_PORTUGUESE_MARKET:'#34d399',
  LANGUAGE_SPANISH_MARKET:   '#fcd34d',
  NEEDS_COMPANY_MAPPING:     '#9ca3af',
  LOW_VALUE_UNRESOLVED:      '#374151',
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

// ── Global error handlers ────────────────────────────────────────────────────
window.onerror = function(msg, src, line, col, err) {
  showBootError('Runtime error: ' + msg + ' (line ' + line + ')');
};
window.addEventListener('unhandledrejection', function(ev) {
  showBootError('Unhandled rejection: ' + (ev.reason && ev.reason.message ? ev.reason.message : String(ev.reason)));
});

function showBootError(msg) {
  const loadEl = document.getElementById('loading');
  if (!loadEl) return;
  loadEl.style.display = '';
  loadEl.innerHTML = '<div class="load-error">'
    + '<h3>Dashboard Error</h3>'
    + '<p>' + msg + '</p>'
    + '<p style="font-size:0.8rem;opacity:0.6">Open browser DevTools (F12 &rarr; Console) for details.</p>'
    + '</div>';
}

// ── Defensive helpers ────────────────────────────────────────────────────────
function safeGet(obj, path, fallback) {
  try {
    return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj) ?? fallback;
  } catch(_) { return fallback; }
}
function asArray(v)  { return Array.isArray(v) ? v : []; }
function asObject(v) { return (v && typeof v === 'object' && !Array.isArray(v)) ? v : {}; }
function formatNumber(v) { return (v === null || v === undefined) ? '—' : (typeof v === 'number' ? v.toLocaleString() : v); }

function safeRender(name, fn) {
  try { fn(); }
  catch(e) {
    console.error('[Dashboard] ' + name + ' render failed:', e);
    const errCard = '<div class="card" style="border-color:#ef4444;color:#ef4444;padding:1rem">'
      + '<strong>' + name + '</strong>: render error — ' + e.message + '</div>';
    const page = document.getElementById('page-' + name.toLowerCase().replace(/\s+/g, '-'));
    if (page) {
      const existing = page.querySelector('.section-label, .metrics-grid, .page-header');
      if (existing) existing.insertAdjacentHTML('afterend', errCard);
    }
  }
}

// ── Boot ─────────────────────────────────────────────────────────────────────
const BUILD_TS = '1782687775';
const DATA_PATHS = [
  'assets/dashboard_data.json?v=' + BUILD_TS,
  './assets/dashboard_data.json?v=' + BUILD_TS,
  '/Conections-map/assets/dashboard_data.json?v=' + BUILD_TS,
];

async function tryFetchData() {
  for (const path of DATA_PATHS) {
    try {
      const r = await fetch(path);
      if (r.ok) {
        const data = await r.json();
        console.log('[Dashboard] Loaded from:', path, '| Keys:', Object.keys(data));
        return data;
      }
    } catch(_) { /* try next */ }
  }
  throw new Error('Could not load dashboard_data.json. Tried:\n' + DATA_PATHS.join('\n'));
}

window.addEventListener('DOMContentLoaded', () => {
  tryFetchData()
    .then(data => {
      D = data;
      document.getElementById('loading').style.display = 'none';
      document.getElementById('app').style.display     = 'flex';
      initNav();
      safeRender('Overview',    renderOverview);
      safeRender('Heatmaps',    renderHeatmaps);
      safeRender('Gap',         renderGap);
      safeRender('Plan',        renderPlan);
      safeRender('Contacts',    renderContacts);
      safeRender('Companies',   renderCompanies);
      safeRender('Opportunity', renderUnknownResolution);
      safeRender('Leads',       renderLeads);
      safeRender('Quality',     renderQuality);
    })
    .catch(err => {
      const loadEl = document.getElementById('loading');
      loadEl.innerHTML = '<div class="load-error">'
        + '<h3>Failed to load dashboard data</h3>'
        + '<p>' + err.message.replace(/\n/g, '<br>') + '</p>'
        + '<p style="font-size:0.8rem;opacity:0.6">If viewing as a local file, run: <code>python -m http.server --directory docs</code></p>'
        + '</div>';
    });
});

// ── Mobile sidebar ────────────────────────────────────────────────────────────
function initMobileSidebar() {
  const sidebar  = document.getElementById('sidebar');
  const overlay  = document.getElementById('sidebar-overlay');
  const burger   = document.getElementById('hamburger-btn');
  if (!sidebar || !burger) return;

  function openSidebar()  { sidebar.classList.add('open');  if (overlay) overlay.classList.add('open'); }
  function closeSidebar() { sidebar.classList.remove('open'); if (overlay) overlay.classList.remove('open'); }

  burger.addEventListener('click', () => sidebar.classList.contains('open') ? closeSidebar() : openSidebar());
  if (overlay) overlay.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSidebar(); });

  // Auto-close on nav item tap (mobile UX)
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => { if (window.innerWidth <= 768) closeSidebar(); });
  });
}

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
      // Resize charts after page switch so Chart.js recalculates dimensions
      setTimeout(() => { Object.values(charts).forEach(c => { try { c.resize(); } catch(_){} }); }, 50);
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
      // Resize charts after tab switch
      setTimeout(() => { Object.values(charts).forEach(c => { try { c.resize(); } catch(_){} }); }, 50);
    });
  });

  initMobileSidebar();

  // Resize all charts when window resizes
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      Object.values(charts).forEach(c => { try { c.resize(); } catch(_){} });
    }, 150);
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
  // V5 coverage banner (replaces old UNKNOWN warning)
  const v5S      = D.opportunity_market_v5_summary || {};
  const actPct   = v5S.v5_actionable_pct || (100 - (v5S.v5_low_value_pct || 0));
  const needsMap = v5S.v5_needs_company_mapping || 0;
  const lowVal   = v5S.v5_low_value_unresolved  || 0;
  const mktConf    = kpi('market_confidence_score') || 0;
  const unknownPct = kpi('unknown_pct') || 0;
  const bannerEl = document.getElementById('confidence-banner');
  if (v5S.total_connections) {
    bannerEl.innerHTML = '<div class="alert alert-good">'
      + '<span class="alert-icon">&#9989;</span>'
      + '<strong>Opportunity Bucket Coverage: ' + actPct + '%</strong> — '
      + (v5S.total_connections - lowVal).toLocaleString() + ' of ' + v5S.total_connections.toLocaleString() + ' contacts classified into actionable opportunity buckets. '
      + needsMap.toLocaleString() + ' need company mapping (action backlog). '
      + lowVal.toLocaleString() + ' low-value/no-signal residual.'
      + ' <em style="opacity:.7;font-size:.85em">Exact geographic location is unavailable from LinkedIn exports — the business dashboard uses company/title/persona inference.</em>'
      + '</div>';
  } else {
    bannerEl.innerHTML = '<div class="alert alert-info">'
      + '<span class="alert-icon">&#9432;</span>'
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
  const v5Sm = D.opportunity_market_v5_summary || {};
  const needsMapCount = v5Sm.v5_needs_company_mapping || 0;
  const lowValCount   = v5Sm.v5_low_value_unresolved  || 0;
  const v5CovPct      = v5Sm.v5_actionable_pct || 0;
  const diagItems = [
    { cls: sns >= 60 ? 'good' : 'warn',  title: 'Network Strength',    text: sns >= 60 ? 'Your professional network is genuinely strong. Large pool of recruiters, hiring managers, and data leaders.' : 'Your network is building. Keep adding strategic personas.' },
    { cls: usd >= 35 ? 'good' : 'warn',  title: 'USD / LATAM Readiness (Primary)',   text: usd >= 35 ? 'USD network is developing. Current 60-day focus: 90% LATAM/USD outreach. You have confirmed contacts in LATAM USD and US/Canada markets.' : 'USD readiness needs work. Current focus: add LATAM USD + US/Canada nearshore recruiters (90% of outreach budget).' },
    { cls: spain >= 20 ? 'info' : 'info',title: 'Spain/EU Readiness (Exploratory)',  text: 'Spain/EU is a 10% exploratory layer for the next 60 days. Build slowly as optionality while USD pipeline is the primary income target.' },
    { cls: glob >= 30 ? 'good' : 'warn', title: 'Global Opportunities', text: 'You have ' + kpi('global_opportunity_total') + ' contacts at GLOBAL_STAFFING, GLOBAL_TECH, and GLOBAL_CONSULTING companies — these can hire anywhere. Reactivate warm ones via Lead Reactivation.' },
    { cls: needsMapCount > 0 ? 'warn' : 'good', title: 'Needs Company Mapping (' + needsMapCount.toLocaleString() + ')', text: needsMapCount + ' contacts have a known company but no opportunity bucket yet. This is an action backlog — not a data failure. Open outputs/unresolved_opportunity_buckets.csv to map them.' },
    { cls: act >= 100 ? 'good' : 'warn', title: 'Actionable Contacts',  text: act + ' contacts have base priority score ≥60. Default ranking uses outreach-adjusted score from message history. See Top Contacts page.' },
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

  // V5 Opportunity Market KPI row — replaces raw UNKNOWN
  const v5D = D.opportunity_market_v5 || {};
  if (v5S.total_connections) {
    document.getElementById('kpi-markets').innerHTML = [
      makeCard('Brazil',            (v5D.BRAZIL_CONFIRMED||0) + (v5D.BRAZIL_LIKELY||0),  'confirmed + likely', 'good'),
      makeCard('LATAM USD',         (v5D.LATAM_USD_CONFIRMED||0) + (v5D.LATAM_USD_LIKELY||0), 'confirmed + likely'),
      makeCard('US / Canada',       (v5D.US_CANADA_CONFIRMED||0) + (v5D.US_CANADA_LIKELY||0)),
      makeCard('Spain / EU',        (v5D.SPAIN_EU_CONFIRMED||0) + (v5D.SPAIN_EU_LIKELY||0) + (v5D.EUROPE_CONFIRMED||0) + (v5D.EUROPE_LIKELY||0)),
      makeCard('Global Staffing',   v5D.GLOBAL_STAFFING||0, 'places data engineers'),
      makeCard('Global Consulting', v5D.GLOBAL_CONSULTING||0),
      makeCard('Global Tech',       v5D.GLOBAL_TECH||0),
      makeCard('Language Signal',   (v5D.LANGUAGE_PORTUGUESE_MARKET||0) + (v5D.LANGUAGE_SPANISH_MARKET||0), 'PT + ES title inference'),
      makeCard('Global Opportunity',v5D.GLOBAL_OPPORTUNITY||0, 'unresolved region'),
      makeCard('Needs Mapping',     v5S.v5_needs_company_mapping||0, 'action backlog', 'warn'),
      makeCard('Low Value',         v5S.v5_low_value_unresolved||0, v5S.v5_low_value_pct + '%'),
    ].join('');
  } else {
    // Fallback to V2 if V5 not yet generated
    document.getElementById('kpi-markets').innerHTML = [
      makeCard('Brazil',            kpi('brazil_count')),
      makeCard('LATAM USD',         kpi('latam_usd_count')),
      makeCard('US/CA Nearshore',   kpi('us_nearshore_count')),
      makeCard('Spain/EU',          kpi('spain_eu_count')),
      makeCard('Europe',            kpi('europe_count')),
      makeCard('Global Staffing',   kpi('global_staffing_count')),
      makeCard('Global Tech',       kpi('global_tech_count')),
      makeCard('Global Consulting', kpi('global_consulting_count')),
    ].join('');
  }

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

  // Market doughnut — V5 Opportunity Market (replaces UNKNOWN-dominated V2 view)
  const v5Dist = D.opportunity_market_v5 || D.market_distribution || {};
  const V5_LABEL = {
    BRAZIL_CONFIRMED: 'Brazil', BRAZIL_LIKELY: 'Brazil (likely)',
    LATAM_USD_CONFIRMED: 'LATAM USD', LATAM_USD_LIKELY: 'LATAM USD (likely)',
    US_CANADA_CONFIRMED: 'US / Canada', US_CANADA_LIKELY: 'US/CA (likely)',
    SPAIN_EU_CONFIRMED: 'Spain / EU', SPAIN_EU_LIKELY: 'Spain/EU (likely)',
    EUROPE_CONFIRMED: 'Europe', EUROPE_LIKELY: 'Europe (likely)',
    GLOBAL_STAFFING: 'Global Staffing', GLOBAL_CONSULTING: 'Global Consulting',
    GLOBAL_TECH: 'Global Tech', GLOBAL_OPPORTUNITY: 'Global Opportunity',
    LANGUAGE_PORTUGUESE_MARKET: 'PT Language Signal', LANGUAGE_SPANISH_MARKET: 'ES Language Signal',
    NEEDS_COMPANY_MAPPING: 'Needs Mapping', LOW_VALUE_UNRESOLVED: 'Low Value',
    // V2 fallbacks
    BRAZIL: 'Brazil', LATAM_USD: 'LATAM USD', US_CANADA_NEARSHORE: 'US/CA',
    SPAIN_EU: 'Spain/EU', EUROPE: 'Europe', UNKNOWN: 'Unknown',
  };
  const mktEntries = Object.entries(v5Dist).sort((a, b) => b[1] - a[1]);
  const mktLabels = mktEntries.map(([k]) => V5_LABEL[k] || k);
  const mktValues = mktEntries.map(([, v]) => v);
  const mktColors = mktEntries.map(([k]) => MARKET_COLORS[k] || '#555');
  doughnutChart('chart-market', mktLabels, mktValues, mktColors);

  // V5 summary KPI row under chart
  const v5Sum = D.opportunity_market_v5_summary || {};
  const v5El = document.getElementById('kpi-v5-summary');
  if (v5El && v5Sum.total_connections) {
    v5El.innerHTML = [
      makeCard('Confirmed Region',   v5Sum.v5_confirmed_geographic || 0, v5Sum.v5_confirmed_pct + '% geographic', 'good'),
      makeCard('Global Buckets',     v5Sum.v5_global_buckets       || 0, 'staffing · consulting · tech'),
      makeCard('Language Signal',    v5Sum.v5_language_inferred    || 0, 'PT / ES title keywords'),
      makeCard('Global Opportunity', v5Sum.v5_global_opportunity   || 0, 'unresolved region persona'),
      makeCard('Needs Mapping',      v5Sum.v5_needs_company_mapping|| 0, 'company exists, unknown market', 'warn'),
      makeCard('Low Value',          v5Sum.v5_low_value_unresolved || 0, v5Sum.v5_low_value_pct + '% unresolvable'),
    ].join('');
    v5El.style.display = '';
  }

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
  // ── Precise Boolean search queries ──────────────────────────────────────────
  const Q = {
    LATAM_USD:      '("Data Engineer" OR "Data Engineering" OR "Cloud Data") AND ("LATAM" OR "Latin America" OR "South America" OR "Remote") AND ("Recruiter" OR "Talent Acquisition" OR "Sourcer")',
    SOUTH_AMERICA:  '("Data Engineer" OR "Data Engineering") AND ("Brazil" OR "Brasil" OR "Argentina" OR "Chile" OR "Colombia" OR "Uruguay" OR "Peru" OR "Mexico") AND ("Recruiter" OR "Talent Acquisition")',
    US_NEARSHORE:   '("Data Engineer" OR "Analytics Engineer" OR "Cloud Data") AND ("nearshore" OR "LATAM" OR "Latin America" OR "remote contractor") AND ("USA" OR "United States" OR "Canada") AND ("Recruiter" OR "Talent Acquisition")',
    STAFFING:       '("Data Engineer" OR "Cloud Data" OR "Azure" OR "AWS" OR "Databricks") AND ("staffing" OR "consulting" OR "nearshore" OR "contractor") AND ("LATAM" OR "remote")',
    HIRING_MGR:     '("Data Engineering Manager" OR "Head of Data" OR "Engineering Manager" OR "Director of Data") AND ("LATAM" OR "remote" OR "nearshore" OR "global")',
    SPAIN_EU:       '("Data Engineer" OR "Data Engineering" OR "Cloud Data") AND ("Spain" OR "España" OR "Madrid" OR "Barcelona" OR "Portugal" OR "Europe") AND ("Recruiter" OR "Talent Acquisition")',
    PORTUGAL_EU:    '("Data Engineer" OR "Analytics Engineer" OR "Cloud Data") AND ("Portugal" OR "Lisbon" OR "Porto" OR "Europe" OR "EU remote") AND ("Recruiter" OR "Talent Acquisition")',
    DIGITAL_NOMAD:  '("Data Engineer" OR "Cloud Data") AND ("remote" OR "contractor" OR "freelance") AND ("Europe" OR "Spain" OR "Portugal") AND ("Recruiter" OR "Talent Acquisition")',
  };

  // ── Filters ──────────────────────────────────────────────────────────────────
  const F = {
    LATAM_USD:    'People · 2nd degree · Locations: Brazil, Argentina, Colombia, Mexico, Chile, Uruguay, Peru · Keywords: recruiter, talent acquisition, data engineer, LATAM, remote',
    US_NEARSHORE: 'People · 2nd degree · Locations: United States, Canada · Keywords: LATAM, nearshore, remote contractor, data engineer, recruiter',
    HIRING_MGR:   'People · 2nd degree · Companies: tech, consulting, SaaS, data/platform · Keywords: Head of Data, Data Engineering Manager, Director of Data, Engineering Manager',
    SPAIN_EU:     'People · 2nd degree · Locations: Spain, Portugal, Germany, Netherlands, Ireland · Keywords: data engineer, recruiter, talent acquisition, remote',
  };

  // ── 7-day sprint ─────────────────────────────────────────────────────────────
  const sprint = [
    {
      day: 'Monday', icon: '&#128293;',
      action: 'LATAM/USD Hot Lead Reactivation',
      detail: 'Message top hot/warm recruiters from Lead Reactivation. Prioritize who replied, asked for CV, or had real conversations.',
      targets: { DMs: '5–10', Connects: '0', Comments: '0' },
      query: Q.LATAM_USD,
      filters: F.LATAM_USD,
      angle: 'Hi [Name], I wanted to reconnect — I\'m currently open to remote LATAM/USD Data Engineering roles. My focus is Azure/AWS data pipelines, Databricks, SQL and ETL/ELT. Happy to share my updated profile.',
    },
    {
      day: 'Tuesday', icon: '&#127758;',
      action: 'LATAM/South America Recruiter Search',
      detail: 'Search and connect with recruiters in Brazil, Argentina, Colombia, Mexico, Chile. Focus on staffing and tech companies.',
      targets: { DMs: '0', Connects: '10–15', Comments: '0' },
      query: Q.SOUTH_AMERICA,
      filters: F.LATAM_USD,
      angle: 'Hi [Name], thanks for connecting. I\'m a Data Engineer focused on Azure, AWS, Databricks, SQL and ETL/ELT, currently open to remote LATAM/USD contractor roles. Happy to stay in touch if you work with data engineering positions.',
    },
    {
      day: 'Wednesday', icon: '&#127482;&#127480;',
      action: 'Nearshore / US-Canada Contractor Ecosystem',
      detail: 'Search for US and Canada recruiters hiring LATAM contractors. Target AgileEngine, Andela, Gorilla Logic, Wizeline, Turing, Deel-adjacent companies.',
      targets: { DMs: '0', Connects: '10–15', Comments: '3' },
      query: Q.US_NEARSHORE,
      filters: F.US_NEARSHORE,
      angle: 'Hi [Name], I\'m currently based in Brazil and available for remote Data Engineering roles aligned with US time zones. My focus is Azure/AWS data pipelines, Databricks, SQL and cloud analytics.',
    },
    {
      day: 'Thursday', icon: '&#127968;',
      action: 'Hiring Managers and Data Leaders',
      detail: 'Search Data Engineering Managers, Heads of Data, Directors in LATAM-friendly or globally remote companies. Softer angle — not a recruiter pitch.',
      targets: { DMs: '3', Connects: '5–10', Comments: '3' },
      query: Q.HIRING_MGR,
      filters: F.HIRING_MGR,
      angle: 'Hi [Name], I\'m connecting because I follow data engineering and cloud data teams working with scalable pipelines and analytics platforms. I work with Azure, AWS, Databricks, SQL and ETL/ELT.',
    },
    {
      day: 'Friday', icon: '&#128203;',
      action: 'Content + Visibility',
      detail: 'Post or comment on LinkedIn around Data Engineering / Azure / AWS / Databricks / remote contractor work. Attract inbound recruiter contacts.',
      targets: { DMs: '0', Connects: '0', Comments: '5–10' },
      query: '—',
      filters: '—',
      angle: 'Post angle: "Remote Data Engineering with Azure/Databricks/dbt — what I\'ve built and what I\'m looking for next." Comment on 5–10 recruiter or company posts about data engineering.',
    },
    {
      day: 'Saturday', icon: '&#127466;&#127480;',
      action: 'Spain/EU Exploratory Layer (10% budget)',
      detail: 'Search Spain, Portugal, Germany, Netherlands, Ireland recruiters. Light touch only — do not over-invest here yet.',
      targets: { DMs: '0', Connects: '2–5', Comments: '1' },
      query: Q.SPAIN_EU,
      filters: F.SPAIN_EU,
      angle: 'Hi [Name], I\'m building my European network as I\'ll be spending time in Spain soon. I\'m a Data Engineer focused on cloud data platforms, Azure/AWS, Databricks and analytics engineering.',
    },
    {
      day: 'Sunday', icon: '&#128197;',
      action: 'Review and Pipeline Hygiene',
      detail: 'Review replies from the week. Update company mapping backlog. Refresh dashboard CSV if available. Prepare next week\'s target list.',
      targets: { DMs: '0', Connects: '0', Comments: '0' },
      query: '—',
      filters: '—',
      angle: 'Open outputs/unresolved_opportunity_buckets.csv → add top 10 companies to config/company_market_overrides.yml → run python src/build_strategy_layer.py to refresh dashboard.',
    },
  ];

  const sprintEl = document.getElementById('sprint-grid');
  if (sprintEl) sprintEl.innerHTML = sprint.map(s => {
    const tgt = Object.entries(s.targets).map(([k,v]) =>
      '<div class="plan-t"><div class="plan-n" style="font-size:1rem">' + v + '</div><div class="plan-l">' + k + '</div></div>'
    ).join('');
    return '<div class="sprint-card">'
      + '<div class="sprint-day">' + s.icon + ' ' + s.day + '</div>'
      + '<div class="sprint-action">' + s.action + '</div>'
      + '<div class="plan-targets">' + tgt + '</div>'
      + '<div class="sprint-meta" style="margin-bottom:.4rem">' + s.detail + '</div>'
      + (s.query !== '—' ? '<div class="sprint-query" style="word-break:break-word;white-space:normal">&#128269; ' + s.query + '</div>' : '')
      + (s.filters !== '—' ? '<div class="sprint-meta" style="color:var(--info);margin-top:.3rem">&#127717; ' + s.filters + '</div>' : '')
      + '<div class="sprint-angle" style="white-space:normal">&#128172; ' + s.angle + '</div>'
      + '</div>';
  }).join('');

  // ── Message angles panel ─────────────────────────────────────────────────────
  const angles = [
    { title: 'LATAM Recruiter',           color: '#f59e0b', angle: 'Hi [Name], thanks for connecting. I\'m a Data Engineer focused on Azure, AWS, Databricks, SQL and ETL/ELT, currently open to remote LATAM/USD contractor roles. Happy to stay in touch if you work with data engineering positions.' },
    { title: 'US/Canada Nearshore Rec.',  color: '#3b82f6', angle: 'Hi [Name], I\'m currently based in Brazil and available for remote Data Engineering roles aligned with US time zones. My focus is Azure/AWS data pipelines, Databricks, SQL and cloud analytics.' },
    { title: 'Hiring Manager / Leader',   color: '#8b5cf6', angle: 'Hi [Name], I\'m connecting because I follow data engineering and cloud data teams working with scalable pipelines and analytics platforms. I work with Azure, AWS, Databricks, SQL and ETL/ELT.' },
    { title: 'Spain/EU Exploratory',      color: '#ef4444', angle: 'Hi [Name], I\'m building my European network as I\'ll be spending time in Spain soon. I\'m a Data Engineer focused on cloud data platforms, Azure/AWS, Databricks and analytics engineering.' },
    { title: 'Dormant Warm Recruiter',    color: '#22c55e', angle: 'We spoke previously about data roles. I wanted to reconnect because I\'m currently open to remote Data Engineering opportunities across LATAM/US time zones.' },
    { title: 'Career Site Follow-up',     color: '#14b8a6', angle: 'I reviewed the careers page and submitted my profile where applicable. If any Data Engineering / Cloud Data role opens, I\'d be happy to be considered.' },
  ];

  const anglesEl = document.getElementById('sprint-angles');
  if (anglesEl) anglesEl.innerHTML = angles.map(a =>
    '<div class="plan-card" style="border-left-color:' + a.color + '">'
    + '<div class="plan-card-title" style="color:' + a.color + '">' + a.title + '</div>'
    + '<div class="plan-reason" style="font-style:italic;margin-top:.5rem;line-height:1.6">"' + a.angle + '"</div>'
    + '</div>'
  ).join('');

  // ── Week-by-week 30-day cards ────────────────────────────────────────────────
  function makeWeekCard(w) {
    const tgt = Object.entries(w.targets).map(([k,v]) =>
      '<div class="plan-t"><div class="plan-n" style="font-size:1rem">' + v + '</div><div class="plan-l">' + k + '</div></div>'
    ).join('');
    return '<div class="plan-card ' + (w.urgency||'high') + '">'
      + '<div class="plan-card-header"><div>'
      + '<div class="plan-card-title">' + w.title + '</div>'
      + '<div class="plan-card-meta">' + w.focus + '</div>'
      + '</div>' + urgencyBadge(w.urgency.charAt(0).toUpperCase() + w.urgency.slice(1)) + '</div>'
      + '<div class="plan-targets" style="margin-bottom:.6rem">' + tgt + '</div>'
      + '<div class="plan-reason">' + w.detail + '</div>'
      + (w.query ? '<div class="plan-query" style="word-break:break-word;white-space:normal;margin-top:.5rem">&#128269; ' + w.query + '</div>' : '')
      + (w.filters ? '<div class="sprint-meta" style="color:var(--info);margin-top:.3rem">&#127717; ' + w.filters + '</div>' : '')
      + (w.angle ? '<div class="sprint-angle" style="white-space:normal;margin-top:.4rem">&#128172; ' + w.angle + '</div>' : '')
      + '</div>';
  }

  const week1 = [
    { urgency: 'critical', title: 'Hot/Warm Lead Reactivation', focus: 'Message history intelligence — leads who already know you',
      targets: { 'DMs': '10–15', 'Career Sites': '5', 'EU Connects': '2–3' },
      detail: 'Start with existing warm contacts. Go to Lead Reactivation → Hot + Warm tabs. Message recruiters who replied, requested CV, or shared roles. Personalize every message. Do NOT send bulk identical DMs.',
      query: Q.LATAM_USD, filters: F.LATAM_USD,
      angle: 'We spoke previously about data roles. I wanted to reconnect because I\'m currently open to remote Data Engineering opportunities across LATAM/US time zones.' },
    { urgency: 'high', title: 'LATAM/USD Recruiter Pipeline — New Connects', focus: 'Brazil · Argentina · Colombia · Chile · Uruguay · Mexico',
      targets: { 'Connects': '40–60', 'DMs': '5', 'Comments': '5' },
      detail: 'Search for LATAM and South America recruiters. Prioritize 2nd degree connections at staffing, consulting, and nearshore tech companies. Send personalized connection requests with a brief note.',
      query: Q.LATAM_USD, filters: F.LATAM_USD,
      angle: 'Hi [Name], thanks for connecting. I\'m a Data Engineer focused on Azure, AWS, Databricks, SQL and ETL/ELT, currently open to remote LATAM/USD contractor roles.' },
    { urgency: 'medium', title: 'Spain/EU Exploratory (Week 1 — Light)', focus: 'Optional — only if LATAM pipeline is on track',
      targets: { 'Connects': '2–3', 'DMs': '0', 'Comments': '1' },
      detail: '10% EU budget. Connect with 2–3 Spain or Portugal recruiters only. Do not spend more than 20 minutes here this week.',
      query: Q.SPAIN_EU, filters: F.SPAIN_EU,
      angle: 'Hi [Name], I\'m building my European network as I\'ll be spending time in Spain soon. I\'m a Data Engineer focused on cloud data platforms, Azure/AWS, Databricks and analytics engineering.' },
  ];

  const week2 = [
    { urgency: 'critical', title: 'South America Recruiter Expansion', focus: 'Staffing & consulting firms hiring LATAM contractors',
      targets: { 'Connects': '50–70', 'DMs': '10', 'Comments': '10' },
      detail: 'Expand beyond your existing network. Search South America recruiters and TA professionals. Focus on staffing companies and consulting firms with LATAM contractor pipelines. Comment on recruiter posts to increase visibility.',
      query: Q.SOUTH_AMERICA, filters: F.LATAM_USD,
      angle: 'Hi [Name], thanks for connecting. I\'m a Data Engineer focused on Azure, AWS, Databricks, SQL and ETL/ELT, currently open to remote LATAM/USD contractor roles. Happy to stay in touch.' },
    { urgency: 'high', title: 'US/Canada Nearshore Recruiters', focus: 'AgileEngine · Andela · Gorilla Logic · Wizeline · Turing · Deel',
      targets: { 'Connects': '15–20', 'DMs': '5', 'Comments': '5' },
      detail: 'Search US and Canada recruiters explicitly hiring LATAM contractors for remote nearshore roles. These companies bridge the USD income gap directly. Priority targets: AgileEngine, Gorilla Logic, Wizeline, BairesDev, Andela.',
      query: Q.US_NEARSHORE, filters: F.US_NEARSHORE,
      angle: 'Hi [Name], I\'m currently based in Brazil and available for remote Data Engineering roles aligned with US time zones. My focus is Azure/AWS data pipelines, Databricks, SQL and cloud analytics.' },
    { urgency: 'medium', title: 'Spain/EU Exploratory (Week 2)', focus: 'Slow build — not the main channel',
      targets: { 'Connects': '3–5', 'DMs': '0', 'Comments': '2' },
      detail: '10% EU budget. Add 3–5 Spain/EU recruiters this week. Focus on people in your network\'s 2nd degree. No DMs yet — just connections.',
      query: Q.SPAIN_EU, filters: F.SPAIN_EU,
      angle: 'Hi [Name], I\'m building my European network for future optionality. I\'m a Data Engineer specializing in Azure/AWS, Databricks and cloud analytics.' },
  ];

  const week3 = [
    { urgency: 'high', title: 'Hiring Managers — LATAM/Remote Companies', focus: 'Decision-makers who can create roles, not just fill them',
      targets: { 'Connects': '30–40', 'DMs': '5', 'Comments': '10' },
      detail: 'Search Data Engineering Managers, Heads of Data, Engineering Directors at LATAM-friendly or globally remote companies. Softer angle — connect and comment on posts, not a direct pitch. Build relationships.',
      query: Q.HIRING_MGR, filters: F.HIRING_MGR,
      angle: 'Hi [Name], I\'m connecting because I follow data engineering and cloud data teams working with scalable pipelines. I work with Azure, AWS, Databricks, SQL and ETL/ELT pipelines.' },
    { urgency: 'high', title: 'Recruiters — Keep Warm', focus: 'Do not let Week 1–2 connections go cold',
      targets: { 'Connects': '30–40', 'DMs': '10', 'Comments': '5' },
      detail: 'Keep the recruiter pipeline active. Follow up with Week 1–2 new connections who accepted but haven\'t replied. Send a brief contextual message — mention open LATAM/USD Data Engineering opportunities.',
      query: Q.LATAM_USD, filters: F.LATAM_USD,
      angle: 'Hi [Name], thanks for accepting — I\'m currently open to remote Data Engineering roles. My stack: Azure, AWS, Databricks, dbt, Airflow, SQL. Happy to send my profile if you work with data engineering positions.' },
    { urgency: 'medium', title: 'Staffing & Global Consulting Firms', focus: 'GLOBAL_STAFFING + GLOBAL_CONSULTING buckets',
      targets: { 'Connects': '10–15', 'DMs': '5', 'Comments': '5' },
      detail: 'Target Hays, Michael Page, Robert Half, Manpower, Randstad, NTT DATA, Accenture, Capgemini — these companies place Data Engineers globally and often have LATAM contractor demand.',
      query: Q.STAFFING, filters: F.LATAM_USD,
      angle: 'Hi [Name], I\'m a Data Engineer specializing in cloud data platforms — Azure, AWS, Databricks, dbt, SQL and ETL/ELT. Currently open to LATAM/USD remote contractor opportunities.' },
  ];

  const week4 = [
    { urgency: 'critical', title: 'Follow-up with Accepted Connections', focus: 'Turn connections into conversations',
      targets: { 'DMs': '20–30', 'Comments': '10', 'Career Sites': '10' },
      detail: 'Send a brief follow-up to everyone who accepted in Weeks 1–3 but hasn\'t replied. Apply to open roles discovered through conversations. Submit to career site talent databases at target companies.',
      query: '—', filters: '—',
      angle: 'Hi [Name], thanks for connecting! I\'m actively looking for remote Data Engineering opportunities. My focus is Azure/AWS data pipelines, Databricks, dbt and SQL. If you\'re working with DE roles, I\'d love to stay in touch.' },
    { urgency: 'high', title: 'EU/Spain Expansion (Week 4 — slightly more)', focus: 'Begin building European optionality',
      targets: { 'Connects': '5–10', 'DMs': '2', 'Comments': '3' },
      detail: 'This week allow slightly more EU exploration now that the LATAM pipeline has momentum. Prioritize Spain, Portugal, Netherlands, Germany, Ireland. Still not the main channel.',
      query: Q.SPAIN_EU, filters: F.SPAIN_EU,
      angle: 'Hi [Name], I\'ll be spending time in Spain soon and I\'m building my European network. I\'m a Data Engineer focused on cloud data platforms, Azure/AWS, Databricks and analytics engineering.' },
    { urgency: 'medium', title: 'Pipeline Cleanup + Next Sprint Setup', focus: 'Compound your momentum',
      targets: { 'Mapping': '20+', 'Review': 'all replies', 'Next Sprint': 'planned' },
      detail: 'Map top 20 companies from unresolved_opportunity_buckets.csv. Review all conversation replies — categorize as Hot/Warm/Cold. Prepare Week 5–8 list with updated contacts from Lead Reactivation.',
      query: '—', filters: '—',
      angle: 'Hygiene actions: run python src/build_strategy_layer.py → update company_market_overrides.yml → review Lead Reactivation hot leads → prepare next sprint focus areas.' },
  ];

  const w1El = document.getElementById('plan-week1-grid');
  const w2El = document.getElementById('plan-week2-grid');
  const w3El = document.getElementById('plan-week3-grid');
  const w4El = document.getElementById('plan-week4-grid');
  if (w1El) w1El.innerHTML = week1.map(makeWeekCard).join('');
  if (w2El) w2El.innerHTML = week2.map(makeWeekCard).join('');
  if (w3El) w3El.innerHTML = week3.map(makeWeekCard).join('');
  if (w4El) w4El.innerHTML = week4.map(makeWeekCard).join('');

  // ── 60 / 90 day plans (data-driven from JSON + strategic overlays) ────────────
  function makePlanGrid(plans, gridId, extraCards) {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    const dataCards = (plans || []).slice(0, 12).map(r => {
      const gap    = r.gap_count || 0;
      const weekly = Math.max(1, Math.ceil(Math.min(gap, 80) / 4));
      const mktKey = (r.market || '').replace(/\s/g,'_').toUpperCase();
      const query  = Q[mktKey] || ('"' + (r.persona||'') + '" "' + (r.market||'') + '"');
      return '<div class="plan-card ' + (r.urgency_level||'').toLowerCase() + '">'
        + '<div class="plan-card-header"><div>'
        + '<div class="plan-card-title">' + (r.market||'') + ' — ' + (r.persona||'') + '</div>'
        + '<div class="plan-card-meta">' + (r.timeframe||'') + '</div>'
        + '</div>' + urgencyBadge(r.urgency_level) + '</div>'
        + '<div class="plan-targets">'
        + '<div class="plan-t"><div class="plan-n">' + fmt(r.current_count) + '</div><div class="plan-l">have</div></div>'
        + '<div class="plan-t"><div class="plan-n">' + fmt(r.target_count) + '</div><div class="plan-l">target</div></div>'
        + '<div class="plan-t"><div class="plan-n" style="color:#ef4444">' + fmt(gap) + '</div><div class="plan-l">gap</div></div>'
        + '<div class="plan-t"><div class="plan-n" style="color:#14b8a6">' + weekly + '/wk</div><div class="plan-l">connects</div></div>'
        + '</div>'
        + '<div class="plan-reason">' + (r.strategic_reason||'').substring(0,140) + '</div>'
        + '<div class="plan-query" style="word-break:break-word;white-space:normal;margin-top:.4rem">&#128269; ' + query + '</div>'
        + '</div>';
    });
    const extra = (extraCards || []).map(makeWeekCard);
    grid.innerHTML = [...extra, ...dataCards].join('');
  }

  const plan60extra = [
    { urgency: 'high', title: '60-Day: Maintain USD Pipeline (80–85%)', focus: 'LATAM/USD + US-nearshore remains primary',
      targets: { 'Connects/wk': '30–40', 'DMs/wk': '10–15', 'Comments/wk': '10' },
      detail: 'Keep the LATAM/USD recruiter and hiring manager pipeline active. Reactivate dormant leads from Lead Reactivation. Add Spain/EU slowly only if USD conversations are already progressing.',
      query: Q.LATAM_USD, filters: F.LATAM_USD, angle: '' },
    { urgency: 'medium', title: '60-Day: Spain/EU Positioning (15–20%)', focus: 'Exploratory — not primary income channel',
      targets: { 'Connects/wk': '5–10', 'DMs/wk': '2–5', 'Comments/wk': '5' },
      detail: 'Build a small but real EU recruiter and hiring manager network. Focus on Spain (Madrid/Barcelona), Portugal (Lisbon), Netherlands, Germany, Ireland. Increase investment only after USD pipeline is stable.',
      query: Q.SPAIN_EU, filters: F.SPAIN_EU, angle: '' },
  ];

  const plan90extra = [
    { urgency: 'high', title: '90-Day: USD Remote Income — Still Priority', focus: 'Do not drop LATAM/USD pipeline',
      targets: { 'Active leads': '15–25', 'EU network': 'growing', 'Mapping': 'ongoing' },
      detail: 'By 90 days you should have active USD job conversations. Keep feeding the LATAM/USD recruiter pipeline. Europe becomes a positioning layer — not a replacement. You can increase EU connects to 20–30% if income is secured.',
      query: Q.LATAM_USD, filters: F.LATAM_USD, angle: '' },
    { urgency: 'medium', title: '90-Day: Europe as Positioning Layer', focus: 'Digital nomad optionality — Spain/Portugal base',
      targets: { 'EU Connects': '40–60 total', 'EU DMs': '15–20 total', 'EU HMs': '10–15' },
      detail: 'Europe becomes a positioning layer while USD remote work remains the income priority. Increase hiring manager relationships in Spain, Portugal, Netherlands. Map companies open to contractors and digital nomads.',
      query: Q.DIGITAL_NOMAD, filters: F.SPAIN_EU, angle: '' },
    { urgency: 'medium', title: '90-Day: Company Mapping Backlog', focus: 'Improve opportunity bucket coverage',
      targets: { 'Companies mapped': '50+', 'V5 coverage': '>80%', 'Mapping sessions': '4' },
      detail: 'Map at least 50 more companies from unresolved_opportunity_buckets.csv. Each company resolved improves the entire dashboard accuracy and reveals hidden opportunities.',
      query: '—', filters: '—', angle: 'Run: python src/build_strategy_layer.py → check Data Quality page for updated bucket coverage.' },
  ];

  makePlanGrid(D.action_plan_60 || [], 'plan-60-grid', plan60extra);
  makePlanGrid(D.action_plan_90 || [], 'plan-90-grid', plan90extra);
}

// ── PAGE 5: Top Contacts ──────────────────────────────────────────────────────
let contactSortMode = 'outreach'; // 'outreach' | 'base'

function renderContacts() {
  const contacts = D.top_contacts || [];
  const personas = [...new Set(contacts.map(c => c.persona||''))].sort();
  const markets  = [...new Set(contacts.map(c => c.opportunity_market_v5 || c.market_v2 || c.strategic_market || ''))].sort();
  const pf = document.getElementById('ct-persona-filter');
  const mf = document.getElementById('ct-market-filter');
  personas.forEach(p => { const o = document.createElement('option'); o.value = p; o.textContent = p; pf && pf.appendChild(o); });
  markets.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; mf && mf.appendChild(o); });
  filteredContacts = [...contacts];
  _sortContacts();
  renderContactsTable();
}

function _sortContacts() {
  if (contactSortMode === 'outreach') {
    filteredContacts.sort((a, b) =>
      (parseFloat(b.outreach_adjusted_score ?? b.priority_score) || 0) -
      (parseFloat(a.outreach_adjusted_score ?? a.priority_score) || 0)
    );
  } else {
    filteredContacts.sort((a, b) =>
      (parseFloat(b.priority_score) || 0) - (parseFloat(a.priority_score) || 0)
    );
  }
}

window.setContactSort = function(mode) {
  contactSortMode = mode;
  const b1 = document.getElementById('ct-sort-outreach');
  const b2 = document.getElementById('ct-sort-base');
  if (b1) b1.classList.toggle('active', mode === 'outreach');
  if (b2) b2.classList.toggle('active', mode === 'base');
  _sortContacts();
  contactsPage = 1;
  renderContactsTable();
};

window.applyContactFilters = function() {
  const minS   = parseFloat(document.getElementById('ct-min-score')?.value) || 0;
  const per    = document.getElementById('ct-persona-filter')?.value || '';
  const mkt    = document.getElementById('ct-market-filter')?.value  || '';
  const band   = document.getElementById('ct-band-filter')?.value    || '';
  const outS   = document.getElementById('ct-outreach-filter')?.value || '';
  filteredContacts = (D.top_contacts || []).filter(c => {
    const s  = parseFloat(c.priority_score) || 0;
    const os = parseFloat(c.outreach_adjusted_score ?? s) || 0;
    const m  = c.opportunity_market_v5 || c.market_v2 || c.strategic_market || '';
    if (s < minS) return false;
    if (per && c.persona !== per) return false;
    if (mkt && m !== mkt) return false;
    if (band === 'high'   && s < 70)           return false;
    if (band === 'medium' && (s < 40 || s >= 70)) return false;
    if (band === 'low'    && s >= 40)          return false;
    if (outS === 'replied'   && !c.replied_to_me)     return false;
    if (outS === 'ghosted'   && !c.ghosted_me)        return false;
    if (outS === 'nohistory' && c.has_message_history) return false;
    return true;
  });
  _sortContacts();
  contactsPage = 1;
  renderContactsTable();
};

window.resetContactFilters = function() {
  const ms = document.getElementById('ct-min-score');     if (ms) ms.value = '0';
  const pf = document.getElementById('ct-persona-filter');if (pf) pf.value = '';
  const mf = document.getElementById('ct-market-filter'); if (mf) mf.value = '';
  const bf = document.getElementById('ct-band-filter');   if (bf) bf.value = '';
  const of = document.getElementById('ct-outreach-filter');if (of) of.value = '';
  filteredContacts = [...(D.top_contacts || [])];
  _sortContacts();
  contactsPage = 1;
  renderContactsTable();
};

const OUTREACH_STATUS_STYLE = {
  'Needs Reply':     'background:#ef4444;color:#fff',
  'Interview Pipeline': 'background:#22c55e;color:#fff',
  'CV / Follow-up':  'background:#3b82f6;color:#fff',
  'Warm Lead':       'background:#f59e0b;color:#fff',
  'Follow-up Due':   'background:#fb923c;color:#fff',
  'Ghosted':         'background:#6b7280;color:#fff',
  'Auto-reply':      'background:#9ca3af;color:#111',
  'Rejected':        'background:#dc2626;color:#fff',
  'Dormant':         'background:#a78bfa;color:#fff',
  'Replied':         'background:#14b8a6;color:#fff',
  'Pending Reply':   'background:#fbbf24;color:#111',
  'No History':      'background:#374151;color:#aaa',
  'No Contact':      'background:#374151;color:#aaa',
};

function outreachBadge(status) {
  const style = OUTREACH_STATUS_STYLE[status] || 'background:#374151;color:#aaa';
  return '<span style="' + style + ';padding:2px 6px;border-radius:4px;font-size:0.7rem;white-space:nowrap">' + (status||'—') + '</span>';
}

function renderContactsTable() {
  const start = (contactsPage - 1) * PAGE_SIZE;
  const slice = filteredContacts.slice(start, start + PAGE_SIZE);
  const st = document.getElementById('ct-stats');
  if (st) st.textContent = 'Showing ' + (start+1) + '–' + Math.min(start + PAGE_SIZE, filteredContacts.length) + ' of ' + filteredContacts.length;
  const tbody = document.getElementById('ct-tbody');
  if (!tbody) return;
  tbody.innerHTML = slice.map(c => {
    const baseS    = parseFloat(c.priority_score) || 0;
    const adjS     = parseFloat(c.outreach_adjusted_score ?? baseS) || 0;
    const mkt      = c.opportunity_market_v5 || c.market_v2 || c.strategic_market || 'UNKNOWN';
    const url      = c.url || '';
    const adjClass = adjS >= 70 ? 'score-high' : adjS >= 40 ? 'score-med' : 'score-low';
    const baseClass= baseS >= 70 ? 'score-high' : baseS >= 40 ? 'score-med' : 'score-low';
    const daysAgo  = c.days_since_last_message != null ? c.days_since_last_message + 'd' : '—';
    return '<tr>'
      + '<td style="white-space:nowrap">' + (c.full_name||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (c.company_clean||'—') + '</td>'
      + '<td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (c.position_clean||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (c.persona||'—') + '</td>'
      + '<td>' + marketBadge(mkt) + '</td>'
      + '<td title="Outreach Adjusted Score"><span class="score-badge ' + adjClass + '">' + adjS.toFixed(0) + '</span></td>'
      + '<td title="Base Network Score"><span class="score-badge ' + baseClass + '" style="opacity:.65">' + baseS.toFixed(0) + '</span></td>'
      + '<td>' + outreachBadge(c.outreach_status || (c.has_message_history ? 'Replied' : 'No History')) + '</td>'
      + '<td style="font-size:0.7rem;color:var(--text-muted)">' + daysAgo + '</td>'
      + '<td style="white-space:normal;font-size:0.7rem;max-width:180px">' + ((c.outreach_reason || c.why_priority||'').substring(0,80)) + '</td>'
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

// ── PAGE 7: Opportunity Market V5 ────────────────────────────────────────────
function renderUnknownResolution() {
  const res   = D.unknown_resolution || {};
  const v5Sum = D.opportunity_market_v5_summary || {};
  const v5Dist= D.opportunity_market_v5 || {};

  // Primary V5 summary cards
  const v5TopEl = document.getElementById('v5-resolution-summary');
  if (v5TopEl && v5Sum.total_connections) {
    const needsMapping = v5Sum.v5_needs_company_mapping || 0;
    const lowValue     = v5Sum.v5_low_value_unresolved || 0;
    const actionable   = v5Sum.v5_actionable_total || 0;
    v5TopEl.innerHTML = [
      makeCard('Actionable Connections',       actionable,                        v5Sum.v5_actionable_pct + '% classified', 'good'),
      makeCard('Confirmed Geographic Signals', v5Sum.v5_confirmed_geographic||0,  'Brazil · LATAM · US · EU · Spain', 'good'),
      makeCard('Global Company Buckets',       v5Sum.v5_global_buckets||0,        'Staffing · Consulting · Tech'),
      makeCard('Language Signal (PT/ES)',      v5Sum.v5_language_inferred||0,     'inferred from title keywords'),
      makeCard('Global Opportunity',           v5Sum.v5_global_opportunity||0,    'valuable persona, region unresolved'),
      makeCard('Needs Company Mapping',        needsMapping,                      'action backlog — map in overrides.yml', 'warn'),
      makeCard('Low Value Unresolved',         lowValue,                          v5Sum.v5_low_value_pct + '% — no usable signal at all'),
    ].join('');
  }

  // Needs-mapping contacts (renamed from "UNKNOWN")
  const hvUnk = res.high_value_unknown_contacts || 0;
  const unkMetEl = document.getElementById('unk-metrics');
  if (unkMetEl) unkMetEl.innerHTML = [
    makeCard('Needs Mapping Total',           v5Sum.v5_needs_company_mapping||0, 'company known, market unresolved'),
    makeCard('High-Value Needs Mapping',      hvUnk,   'recruiters + hiring mgrs score ≥60', 'warn'),
    makeCard('Recruiters Needing Mapping',    kpi('unknown_recruiters_highvalue'), 'score ≥60 — map their companies first'),
    makeCard('Hiring Mgrs Needing Mapping',   kpi('unknown_hiring_mgrs_highvalue'), 'score ≥50'),
    makeCard('Data Leaders Needing Mapping',  kpi('unknown_data_leaders_highvalue'), 'score ≥50'),
  ].join('');

  // Resolution potential
  const autoRes = res.auto_resolvable_contacts || 0;
  const top25   = res.top25_coverage || kpi('top25_company_coverage');
  const top25pct= res.top25_pct_of_unknown || kpi('unknown_resolution_potential');
  const unkResEl = document.getElementById('unk-resolution-metrics');
  if (unkResEl) unkResEl.innerHTML = [
    makeCard('Top 25 Companies Impact', top25,   top25pct + '% of needs-mapping contacts', 'good'),
    makeCard('Auto-Resolvable',         autoRes, 'via keyword + heuristics', 'good'),
    makeCard('Opportunity Bucket Score',kpi('unknown_resolution_score') + '/100', 'higher = better mapped'),
  ].join('');

  // Top companies needing mapping — prefer V5 backlog, fall back to V2 unknown
  const backlogData = D.unknown_companies || [];
  const top25Companies = (res.top25_companies && Array.isArray(res.top25_companies))
    ? res.top25_companies
    : backlogData.slice(0, 25);
  const tbody = document.getElementById('unk-companies-tbody');
  if (tbody) {
    if (!top25Companies.length) {
      tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-muted)">All companies have been mapped.</td></tr>';
    } else {
    tbody.innerHTML = top25Companies.map((r, i) => {
      const sugMarket = r.suggested_market || r.opportunity_bucket || '';
      const badgeVal  = sugMarket && sugMarket !== 'UNKNOWN' ? sugMarket : 'NEEDS_COMPANY_MAPPING';
      return '<tr>'
      + '<td><strong>#' + (i+1) + '</strong></td>'
      + '<td style="font-weight:500">' + (r.company_clean||r.company||'') + '</td>'
      + '<td><strong>' + fmt(r.connection_count) + '</strong></td>'
      + '<td>' + fmt(r.recruiter_count||0) + '</td>'
      + '<td>' + fmt(r.talent_count||0) + '</td>'
      + '<td>' + fmt(r.hiring_manager_count||0) + '</td>'
      + '<td>' + fmt(r.data_leader_count||0) + '</td>'
      + '<td>' + (Number(r.avg_priority_score||0).toFixed(0)) + '</td>'
      + '<td>' + marketBadge(badgeVal) + '</td>'
      + '<td style="font-size:0.72rem;max-width:200px">' + String(r.suggested_reason||'').substring(0,80) + '</td>'
      + '</tr>';
    }).join('');
    }
  }

  // Persona breakdown for needs-mapping contacts
  const unkPersonaEl = document.getElementById('unk-persona-metrics');
  if (!unkPersonaEl) return;
  unkPersonaEl.innerHTML = [
    makeCard('Recruiters Needing Mapping',      kpi('unknown_recruiters_highvalue'),   'score ≥60 — map their companies first', 'warn'),
    makeCard('Talent Acquisition — No Bucket',  kpi('unknown_ta_highvalue') || kpi('unknown_ta') || 0, 'score ≥50'),
    makeCard('Hiring Mgrs — No Bucket',         kpi('unknown_hiring_mgrs_highvalue'),  'score ≥50 — potential direct hire'),
    makeCard('Data Leaders — No Bucket',        kpi('unknown_data_leaders_highvalue'), 'referral network value'),
    makeCard('Data Peers — No Bucket',          kpi('unknown_peers'),                 'lowest priority to map'),
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
  const v5S  = D.opportunity_market_v5_summary || {};
  const v5D  = D.opportunity_market_v5 || {};
  const total = v5S.total_connections || kpi('total_connections');

  // Section A — Business Classification Quality (V5)
  const qaEl = document.getElementById('quality-metrics-a');
  if (qaEl) {
    const actionable  = v5S.v5_actionable_total  || (total - (v5S.v5_low_value_unresolved||0));
    const actPct      = v5S.v5_actionable_pct    || 0;
    const needsMap    = v5S.v5_needs_company_mapping || 0;
    const lowVal      = v5S.v5_low_value_unresolved  || 0;
    const lowPct      = v5S.v5_low_value_pct     || 0;
    const geoConf     = v5S.v5_confirmed_geographic  || 0;
    const globalBuck  = v5S.v5_global_buckets    || 0;
    const langInf     = v5S.v5_language_inferred || 0;
    const globalOpp   = v5S.v5_global_opportunity|| 0;
    qaEl.innerHTML = [
      makeCard('Opportunity Bucket Coverage', actPct + '%',     actionable.toLocaleString() + ' contacts classified', 'good'),
      makeCard('Confirmed Geographic Signals', geoConf.toLocaleString(), 'Brazil · LATAM · US · EU · Spain', 'good'),
      makeCard('Global Company Buckets',      globalBuck.toLocaleString(), 'Staffing · Consulting · Tech'),
      makeCard('Language Signal (PT/ES)',     langInf.toLocaleString(), 'inferred from title keywords'),
      makeCard('Global Opportunity',          globalOpp.toLocaleString(), 'valuable persona, unresolved region'),
      makeCard('Needs Company Mapping',       needsMap.toLocaleString(), 'action backlog — map in overrides YAML', 'warn'),
      makeCard('Low Value Unresolved',        lowVal.toLocaleString(), lowPct + '% — no usable signal found'),
    ].join('');
  }

  // Section B — Geographic data limitation (technical)
  const qbEl = document.getElementById('quality-metrics-b');
  if (qbEl) {
    const unkPct  = kpi('unknown_pct');
    const mktConf = kpi('market_confidence_score');
    qbEl.innerHTML = [
      makeCard('Exact Location Available', '0%',             'LinkedIn export has no location field'),
      makeCard('Geographic Confidence',    mktConf + '/100', 'Low = normal for LinkedIn exports'),
      makeCard('Raw V2 Unknown (technical)',kpi('unknown_count'), unkPct + '% — before V5 reclassification'),
      makeCard('V5 Reclassified',          (kpi('unknown_count') - (v5S.v5_needs_company_mapping||0) - (v5S.v5_low_value_unresolved||0)) + '', 'contacts rescued from raw UNKNOWN', 'good'),
    ].join('');
  }

  // V5 doughnut (replaces old market type distribution)
  const chartEl = document.getElementById('chart-mkt-type');
  if (chartEl && Object.keys(v5D).length > 0) {
    const V5_SHORT = {
      BRAZIL_CONFIRMED:'Brazil', BRAZIL_LIKELY:'Brazil (likely)',
      LATAM_USD_CONFIRMED:'LATAM USD', LATAM_USD_LIKELY:'LATAM (likely)',
      US_CANADA_CONFIRMED:'US/Canada', US_CANADA_LIKELY:'US/CA (likely)',
      SPAIN_EU_CONFIRMED:'Spain/EU', SPAIN_EU_LIKELY:'Spain (likely)',
      EUROPE_CONFIRMED:'Europe', EUROPE_LIKELY:'Europe (likely)',
      GLOBAL_STAFFING:'Staffing', GLOBAL_CONSULTING:'Consulting',
      GLOBAL_TECH:'Tech', GLOBAL_OPPORTUNITY:'Global Opp.',
      LANGUAGE_PORTUGUESE_MARKET:'PT Signal', LANGUAGE_SPANISH_MARKET:'ES Signal',
      NEEDS_COMPANY_MAPPING:'Needs Mapping', LOW_VALUE_UNRESOLVED:'Low Value',
    };
    const entries = Object.entries(v5D).sort((a,b) => b[1]-a[1]);
    doughnutChart('chart-mkt-type',
      entries.map(([k]) => V5_SHORT[k] || k),
      entries.map(([,v]) => v),
      entries.map(([k]) => MARKET_COLORS[k] || '#555')
    );
  } else if (chartEl) {
    const mtDist = kpi('market_type_distribution', {});
    if (typeof mtDist === 'object' && Object.keys(mtDist).length > 0) {
      const ls = Object.keys(mtDist);
      const vs = Object.values(mtDist);
      const cs = ['#3b82f6','#22c55e','#f59e0b','#a78bfa','#14b8a6','#4b5563'];
      doughnutChart('chart-mkt-type', ls, vs, cs.slice(0, ls.length));
    }
  }
}
