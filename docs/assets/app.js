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

// ── Browser extension / wallet error filter ───────────────────────────────────
function isExtensionError(msg, src) {
  const m = String(msg || '').toLowerCase();
  const s = String(src || '').toLowerCase();
  return (
    s.includes('chrome-extension://') ||
    s.includes('moz-extension://') ||
    s.includes('safari-extension://') ||
    s.includes('inpage.js') ||
    s.includes('contentscript') ||
    s.includes('content-script') ||
    m.includes('metamask') ||
    m.includes('failed to connect to metamask') ||
    m.includes('metamask extension not found') ||
    m.includes('message channel closed before a response was received') ||
    m.includes('a listener indicated an asynchronous response') ||
    m.includes('extension context invalidated') ||
    m.includes('could not establish connection') ||
    m.includes('receiving end does not exist') ||
    m.includes('chrome-extension') ||
    m.includes('moz-extension') ||
    m.includes('inpage.js') ||
    m.includes('contentscript')
  );
}

// ── Global error handlers — COMPLETELY NON-FATAL ──────────────────────────────
// These handlers NEVER render the fatal UI. Only fatalAppError() does that.
window.onerror = function(message, source, lineno, colno, error) {
  const msg = String(message || (error && error.message) || '');
  const src = String(source || (error && error.stack) || '');
  if (isExtensionError(msg, src)) {
    console.warn('[Ignored extension error]', msg, src);
    return true;
  }
  // Non-fatal: log to console only, dashboard keeps running
  console.error('[Non-fatal global error]', { message: msg, source, lineno, colno, error });
  return true; // always suppress default browser error reporting
};

window.addEventListener('unhandledrejection', function(event) {
  const reason = event.reason;
  const msg = String((reason && reason.message) ? reason.message : (reason || ''));
  const src = String((reason && reason.stack) ? reason.stack : '');
  if (isExtensionError(msg, src)) {
    console.warn('[Ignored extension rejection]', msg);
    event.preventDefault();
    return;
  }
  // Non-fatal: log only, never show UI error
  console.error('[Non-fatal unhandled rejection]', reason);
  event.preventDefault();
});

// ── Fatal app error — ONLY call for real boot failures ────────────────────────
function fatalAppError(title, detail) {
  // Guard: never show fatal UI for extension errors
  const combined = String(title || '') + ' ' + String(detail || '');
  if (isExtensionError(combined, '')) {
    console.warn('[Suppressed fatal UI from extension error]', combined);
    return;
  }
  const loadEl = document.getElementById('loading');
  if (!loadEl) return;
  loadEl.style.display = '';
  loadEl.innerHTML = '<div class="load-error">'
    + '<h3>' + (title || 'Dashboard Error') + '</h3>'
    + '<p>' + (detail || '') + '</p>'
    + '<p style="font-size:0.8rem;opacity:0.6">Open browser DevTools (F12 &rarr; Console) for details.</p>'
    + '</div>';
}

// Kept for backwards-compat with any internal call sites — delegates to fatalAppError.
function showBootError(msg) {
  const m = String(msg || '');
  if (isExtensionError(m, '')) {
    console.warn('[Suppressed showBootError from extension]', m);
    return;
  }
  fatalAppError('Dashboard Error', m);
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
// Data Payload Optimization V1: dashboard_data.json is now a lightweight
// manifest (meta, kpis, page_manifest) loaded once at boot. Each nav page's
// full data lives in its own docs/assets/data/<file>.json, fetched on first
// visit to that page (see PAGE_REGISTRY / ensurePageData below) and merged
// into the same global D object every render function already reads from —
// render functions themselves are unchanged, they just see D populated
// incrementally instead of all at once.
const BUILD_TS = '1785796188';

function _dataPathVariants(relPath) {
  return [
    'assets/' + relPath + (relPath.includes('?') ? '&' : '?') + 'v=' + BUILD_TS,
    './assets/' + relPath + (relPath.includes('?') ? '&' : '?') + 'v=' + BUILD_TS,
    '/Conections-map/assets/' + relPath + (relPath.includes('?') ? '&' : '?') + 'v=' + BUILD_TS,
  ];
}

async function _fetchJsonWithFallback(relPath) {
  let lastErr = null;
  for (const path of _dataPathVariants(relPath)) {
    try {
      const r = await fetch(path);
      if (r.ok) {
        const data = await r.json();
        console.log('[Dashboard] Loaded from:', path, '| Keys:', Object.keys(data));
        return data;
      }
      lastErr = new Error('HTTP ' + r.status + ' loading ' + path);
    } catch (e) { lastErr = e; /* try next path variant */ }
  }
  throw lastErr || new Error('Could not load ' + relPath);
}

async function tryFetchData() {
  try {
    return await _fetchJsonWithFallback('dashboard_data.json');
  } catch (e) {
    throw new Error('Could not load dashboard_data.json. Tried:\n' + _dataPathVariants('dashboard_data.json').join('\n'));
  }
}

// ── Lazy page-data registry ───────────────────────────────────────────────────
// pageId (matches .nav-item[data-page] / #page-<id> in index.html) -> the
// render function(s) to call once that page's data file has been merged into D.
const PAGE_REGISTRY = {
  overview:  { renderFns: [renderOverview] },
  heatmap:   { renderFns: [renderHeatmaps] },
  gap:       { renderFns: [renderGap] },
  plan:      { renderFns: [renderPlan, renderPlanProgress, renderPlanExecSummary, renderWeekHistoryPanels] },
  contacts:  { renderFns: [renderContacts] },
  weekly:    { renderFns: [renderWeekly] },
  companies: { renderFns: [renderCompanies] },
  unknown:   { renderFns: [renderUnknownResolution] },
  leads:     { renderFns: [renderLeads] },
  untapped:  { renderFns: [renderUntapped] },
  usdcrm:    { renderFns: [renderUsdCrm, renderOpportunityHistory, renderMonthlyExecutiveQueue] },
  quality:   { renderFns: [renderQuality] },
};

const loadedPages    = new Set();  // pageId -> data already merged into D
const renderedPages  = new Set();  // pageId -> render function(s) already ran once
const pageLoadPromises = {};       // pageId -> in-flight fetch promise (de-dupes concurrent clicks)

// Fetches (and merges into D) a page's data file, first recursively loading
// any pages it depends on (e.g. 'gap' needs 'untapped' for its "Recommended
// People to Activate Next" tab) — never fetches the same file twice.
async function ensurePageData(pageId) {
  if (loadedPages.has(pageId)) return;
  if (pageLoadPromises[pageId]) return pageLoadPromises[pageId];

  const info = (D && D.page_manifest) ? D.page_manifest[pageId] : null;
  if (!info) { loadedPages.add(pageId); return; }

  const p = (async () => {
    for (const dep of (info.dependencies || [])) {
      await ensurePageData(dep);
    }
    const json = await _fetchJsonWithFallback(info.file);
    Object.assign(D, json);
    loadedPages.add(pageId);
  })();
  pageLoadPromises[pageId] = p;
  try { await p; }
  finally { delete pageLoadPromises[pageId]; }
}

function _pageHeaderEl(pageId) {
  const page = document.getElementById('page-' + pageId);
  return page ? page.querySelector('.page-header') : null;
}

function _clearPageBanner(pageId) {
  const loadEl = document.getElementById('page-load-banner-' + pageId);
  if (loadEl) loadEl.remove();
  const errEl = document.getElementById('page-load-error-' + pageId);
  if (errEl) errEl.remove();
}

function _showPageLoading(pageId) {
  _clearPageBanner(pageId);
  const header = _pageHeaderEl(pageId);
  if (!header) return;
  header.insertAdjacentHTML('afterend',
    '<div class="alert alert-info" id="page-load-banner-' + pageId + '">'
    + '<span class="alert-icon">&#8987;</span><span>Loading data&hellip;</span></div>');
}

function _showPageLoadError(pageId, err) {
  _clearPageBanner(pageId);
  const header = _pageHeaderEl(pageId);
  if (!header) return;
  const msg = (err && err.message) ? err.message : 'Unknown error';
  header.insertAdjacentHTML('afterend',
    '<div class="alert alert-bad" id="page-load-error-' + pageId + '">'
    + '<span class="alert-icon">&#9888;&#65039;</span>'
    + '<span><strong>Could not load this page’s data.</strong> ' + msg
    + ' <button class="btn-ghost" style="padding:2px 10px;font-size:0.75rem" '
    + 'onclick="retryPageLoad(\'' + pageId + '\')">Retry</button></span></div>');
}

// Loads (if needed) and renders a page exactly once — safe to call on every
// nav click; a page already rendered is left as-is (matches the dashboard's
// original render-once-then-just-show/hide behavior). Never throws — a data
// load failure shows a page-level error banner, never a fatal dashboard crash.
async function ensurePageRendered(pageId) {
  const reg = PAGE_REGISTRY[pageId];
  if (!reg || renderedPages.has(pageId)) return;

  _showPageLoading(pageId);
  try {
    await ensurePageData(pageId);
  } catch (err) {
    console.error('[Dashboard] Failed to load page data for "' + pageId + '":', err);
    _showPageLoadError(pageId, err);
    return;
  }
  _clearPageBanner(pageId);
  reg.renderFns.forEach(fn => safeRender(pageId, fn));
  renderedPages.add(pageId);
  setTimeout(() => { Object.values(charts).forEach(c => { try { c.resize(); } catch(_){} }); }, 50);
}

window.retryPageLoad = function(pageId) {
  _clearPageBanner(pageId);
  ensurePageRendered(pageId);
};

window.addEventListener('DOMContentLoaded', () => {
  let booted = false;

  // Watchdog: if loading screen still visible after 12 s, show a soft warning (never fatal)
  const watchdog = setTimeout(() => {
    if (booted) return;
    const loadEl = document.getElementById('loading');
    if (!loadEl || loadEl.style.display === 'none') return;
    loadEl.innerHTML = '<div class="load-error" style="border-color:#f59e0b;color:#f59e0b">'
      + '<h3 style="color:#f59e0b">Taking longer than expected</h3>'
      + '<p>Dashboard is still loading. Try a hard refresh (Ctrl+Shift+R / Cmd+Shift+R).</p>'
      + '<p style="font-size:0.8rem;opacity:0.6">If this persists, open DevTools (F12) Console for details.</p>'
      + '</div>';
  }, 12000);

  tryFetchData()
    .then(manifest => {
      booted = true;
      clearTimeout(watchdog);
      D = manifest; // lightweight: meta, kpis, page_manifest, build — full
                     // page data is merged in on demand (see ensurePageData)
      document.getElementById('loading').style.display = 'none';
      document.getElementById('app').style.display     = 'flex';
      initNav();
      // Render only the initially-active nav page (Executive Overview by
      // default) — every other page loads+renders on first visit/click.
      const initialNav = document.querySelector('.nav-item.active');
      const initialPageId = (initialNav && initialNav.dataset.page) || 'overview';
      ensurePageRendered(initialPageId);
    })
    .catch(err => {
      booted = true;
      clearTimeout(watchdog);
      // This is a real app boot failure — the only place fatalAppError is called
      fatalAppError(
        'Failed to load dashboard data',
        err.message.replace(/\n/g, '<br>')
          + '<br><span style="font-size:.8rem;opacity:.6">If viewing as a local file, run: <code>python -m http.server --directory docs</code></span>'
      );
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
// Pure DOM page switch — decoupled from data loading so setRoute() can
// activate the destination page immediately (showing its loading state)
// without waiting on the network.
function activatePageUI(pageId) {
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const navEl = document.querySelector('.nav-item[data-page="' + pageId + '"]');
  if (navEl) navEl.classList.add('active');
  const pg = document.getElementById('page-' + pageId);
  if (pg) pg.classList.add('active');
  // Resize charts after page switch so Chart.js recalculates dimensions
  setTimeout(() => { Object.values(charts).forEach(c => { try { c.resize(); } catch(_){} }); }, 50);
}

function initNav() {
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
      const page = el.dataset.page;
      activatePageUI(page);
      ensurePageRendered(page); // fetches on first visit only; no-op if already rendered
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

// ── Cross-page routing (Executive Overview clickthroughs, Part 1) ────────────
// setRoute(pageId, filterPayload) switches to another page (reusing initNav's
// own click-delegation, not a duplicate routing system) then applies a filter
// on the destination page and shows an active-filter banner: "Showing X of Y
// — <label>". filterPayload = { applyFn, applyArgs, label, resetFn }.
let dashboardState = { activePageId: null, activeLabel: null, resetFn: null };

// Maps a destination pageId to its primary .table-stats element and the
// filtered/total array getters needed to render "Showing X of Y — <label>"
// in the SAME element the page's own filters already use — no new DOM.
function _routeStatsConfig(pageId) {
  const cfg = {
    contacts: { el: 'ct-stats',                filtered: () => filteredContacts.length,   total: () => (D.top_contacts || []).length },
    leads:    { el: 'leads-stats',             filtered: () => filteredLeads.length,      total: () => ((D.lead_reactivation || {}).top_reactivation_contacts || []).length },
    untapped: { el: 'untapped-stats',          filtered: () => filteredUntapped.length,   total: () => ((D.untapped_network || {}).top_untapped_contacts || []).length },
    unknown:  { el: 'mapping-drilldown-stats', filtered: () => filteredMappingPeople.length, total: () => mappingPeopleBase.length },
  };
  return cfg[pageId];
}

function renderActiveFilterBanner(pageId, label) {
  const cfg = _routeStatsConfig(pageId);
  if (!cfg) return;
  const el = document.getElementById(cfg.el);
  if (!el) return;
  el.textContent = 'Showing ' + cfg.filtered() + ' of ' + cfg.total() + (label ? ' — ' + label : '');
}

async function setRoute(pageId, filterPayload) {
  activatePageUI(pageId);
  try {
    await ensurePageRendered(pageId); // loads the destination page's data (+ dependencies) before filtering
  } catch (e) {
    console.error('[setRoute] page render failed for', pageId, e);
    return;
  }
  if (!filterPayload) return;
  const fn = window[filterPayload.applyFn];
  if (typeof fn !== 'function') { console.error('[setRoute] unknown applyFn:', filterPayload.applyFn); return; }
  try { fn.apply(null, filterPayload.applyArgs || []); }
  catch (e) { console.error('[setRoute] filter apply failed:', e); return; }
  dashboardState = { activePageId: pageId, activeLabel: filterPayload.label, resetFn: filterPayload.resetFn };
  renderActiveFilterBanner(pageId, filterPayload.label);
  const table = document.querySelector('#page-' + pageId + ' .table-wrap table');
  if (table) table.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

window.clearRouteFilter = function() {
  const st = dashboardState;
  dashboardState = { activePageId: null, activeLabel: null, resetFn: null };
  if (st.resetFn && typeof window[st.resetFn] === 'function') window[st.resetFn]();
};

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

// Clickable KPI card (Lead Reactivation Part 2) — same look as makeCard but
// wired to applyLeadKpiFilter(key) on click/Enter/Space, keyboard accessible.
function makeKpiCard(key, title, value, sub = '', subClass = '', handler = 'applyLeadKpiFilter') {
  return '<div class="card kpi-card" data-kpi="' + key + '" tabindex="0" role="button" '
       + 'aria-pressed="false" aria-label="Filter by ' + title + '" '
       + 'onclick="' + handler + '(\'' + key + '\')" '
       + 'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();' + handler + '(\'' + key + '\')}">'
       + '<div class="card-title">' + title + '</div>'
       + '<div class="card-value">' + fmt(value) + '</div>'
       + (sub ? '<div class="card-sub ' + subClass + '">' + sub + '</div>' : '')
       + '</div>';
}

// Cross-page routing card (Part 1) — same look as makeKpiCard, but the click
// calls setRoute(route.pageId, route) instead of a same-page handler, so it
// switches pages AND applies the filter there. `route` is registered on
// window._ROUTE_CARDS[idx] rather than inlined into the onclick attribute,
// so card titles/labels never need HTML-attribute escaping.
window._routeCardRegistry = [];
function makeRouteCard(title, value, sub, subClass, route) {
  const idx = window._routeCardRegistry.push(route) - 1;
  return '<div class="card kpi-card" tabindex="0" role="button" '
       + 'aria-label="Filter by ' + title + '" '
       + 'onclick="setRoute(\'' + route.pageId + '\', window._routeCardRegistry[' + idx + '])" '
       + 'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();setRoute(\'' + route.pageId + '\', window._routeCardRegistry[' + idx + '])}">'
       + '<div class="card-title">' + title + '</div>'
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
  const periodActuals = opts && opts.periodActuals;
  charts[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ data: values, backgroundColor: colors || '#3b82f6', borderRadius: 4, borderSkipped: false }] },
    options: {
      indexAxis: isH ? 'y' : 'x',
      responsive: true,
      onClick: (opts && opts.onBarClick) ? (evt, els) => { if (els.length) opts.onBarClick(els[0].index); } : undefined,
      onHover: (opts && opts.onBarClick) ? (evt) => { evt.native.target.style.cursor = 'pointer'; } : undefined,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: c => ' ' + (isH ? c.parsed.x : c.parsed.y).toLocaleString(),
            afterLabel: c => (periodActuals && periodActuals[c.dataIndex] !== null && periodActuals[c.dataIndex] !== undefined)
              ? ' (period total: ' + Number(periodActuals[c.dataIndex]).toLocaleString() + ')' : ''
          }
        }
      },
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

// Grouped bar chart — multiple named datasets sharing one label axis.
// datasets: [{ label, data, color, periodActuals? }, ...] — periodActuals is
// an optional array (parallel to data/labels) shown as a secondary tooltip
// line, e.g. the raw multi-week period total behind a weekly-pace bar.
function groupedBarChart(canvasId, labels, datasets, opts) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const isH = opts && opts.horizontal;
  charts[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: datasets.map(d => ({
        label: d.label, data: d.data, backgroundColor: d.color, borderRadius: 4, borderSkipped: false,
        periodActuals: d.periodActuals
      }))
    },
    options: {
      indexAxis: isH ? 'y' : 'x',
      responsive: true,
      plugins: {
        legend: { display: true, position: 'bottom', labels: { color: '#8b949e', font: { size: 10 }, boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: c => ' ' + c.dataset.label + ': ' + (isH ? c.parsed.x : c.parsed.y).toLocaleString(),
            afterLabel: c => {
              const pa = c.dataset.periodActuals;
              if (!pa || pa[c.dataIndex] === null || pa[c.dataIndex] === undefined) return '';
              return ' (period total: ' + Number(pa[c.dataIndex]).toLocaleString() + ')';
            }
          }
        }
      },
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: 10 } }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', font: { size: 10 } }, grid: { color: '#21262d' } }
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
  // Each diag item's `route` (Part 1) is a self-contained setRoute() call —
  // page + filter + label — so clicking the card shows the exact people/
  // companies behind the number instead of leaving it as a static bullet.
  const diagItems = [
    { cls: sns >= 60 ? 'good' : 'warn',  title: 'Network Strength',    text: sns >= 60 ? 'Your professional network is genuinely strong. Large pool of recruiters, hiring managers, and data leaders.' : 'Your network is building. Keep adding strategic personas.',
      route: { pageId: 'contacts', applyFn: 'applyExternalContactFilter', applyArgs: [{ minScoreField: 'priority_score', minScore: 60 }], label: 'Executive card: Network Strength', resetFn: 'resetContactFilters' } },
    { cls: usd >= 35 ? 'good' : 'warn',  title: 'USD / LATAM Readiness (Primary)',   text: usd >= 35 ? 'USD network is developing. Current 60-day focus: 90% LATAM/USD outreach. You have confirmed contacts in LATAM USD and US/Canada markets.' : 'USD readiness needs work. Current focus: add LATAM USD + US/Canada nearshore recruiters (90% of outreach budget).',
      route: { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['usd_readiness'], label: 'Executive card: USD/LATAM Readiness', resetFn: 'resetUntappedFilters' } },
    { cls: spain >= 20 ? 'info' : 'info',title: 'Spain/EU Readiness (Exploratory)',  text: 'Spain/EU is a 10% exploratory layer for the next 60 days. Build slowly as optionality while USD pipeline is the primary income target.',
      route: { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['spain_eu_readiness'], label: 'Executive card: Spain/EU Exploratory', resetFn: 'resetUntappedFilters' } },
    { cls: glob >= 30 ? 'good' : 'warn', title: 'Global Opportunities', text: 'You have ' + kpi('global_opportunity_total') + ' contacts at GLOBAL_STAFFING, GLOBAL_TECH, and GLOBAL_CONSULTING companies — these can hire anywhere. Reactivate warm ones via Lead Reactivation.',
      route: { pageId: 'contacts', applyFn: 'applyExternalContactFilter', applyArgs: [{ opportunityBuckets: ['GLOBAL_STAFFING', 'GLOBAL_TECH', 'GLOBAL_CONSULTING', 'GLOBAL_OPPORTUNITY'] }], label: 'Executive card: Global Opportunities', resetFn: 'resetContactFilters' } },
    { cls: needsMapCount > 0 ? 'warn' : 'good', title: 'Needs Company Mapping (' + needsMapCount.toLocaleString() + ')', text: needsMapCount + ' contacts have a known company but no opportunity bucket yet. This is an action backlog — not a data failure.',
      route: { pageId: 'unknown', applyFn: 'applyUnknownKpiFilter', applyArgs: ['needs_mapping_total'], label: 'Executive card: Needs Company Mapping', resetFn: 'resetMappingDrilldown' } },
    { cls: act >= 100 ? 'good' : 'warn', title: 'Actionable Contacts',  text: act + ' contacts have base priority score ≥60. Default ranking uses outreach-adjusted score from message history. See Top Contacts page.',
      route: { pageId: 'contacts', applyFn: 'applyExternalContactFilter', applyArgs: [{ minScoreAnyFields: ['priority_score', 'outreach_adjusted_score', 'untapped_outreach_score'], minScoreAny: 60 }], label: 'Executive card: Actionable Contacts', resetFn: 'resetContactFilters' } },
  ];
  window._EXEC_DIAG_ROUTES = diagItems.map(d => d.route);
  document.getElementById('diagnosis-grid').innerHTML = diagItems.map((d, i) =>
    '<div class="diag-item ' + d.cls + (d.route ? ' kpi-card' : '') + '"'
    + (d.route ? ' tabindex="0" role="button" aria-label="Filter by ' + d.title + '" onclick="setRoute(\'' + d.route.pageId + '\', window._EXEC_DIAG_ROUTES[' + i + '])" '
      + 'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();setRoute(\'' + d.route.pageId + '\', window._EXEC_DIAG_ROUTES[' + i + '])}"' : '')
    + '><h4>' + d.title + '</h4><p>' + d.text + '</p></div>'
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
  // D.lead_reactivation_summary is the lightweight scalar-only extract that
  // ships with executive_overview.json; D.lead_reactivation (the full,
  // multi-MB object) is preferred if the Leads page has already been visited.
  const lr = D.lead_reactivation || D.lead_reactivation_summary || {};
  const lrRow = document.getElementById('kpi-lead-reactivation');
  const lrLabel = document.getElementById('kpi-lead-reactivation-label');
  if (lrRow) {
    if (lr.messages_csv_available && lr.total_conversations) {
      lrRow.style.display = '';
      if (lrLabel) lrLabel.style.display = '';
      lrRow.innerHTML = [
        makeRouteCard('This Week Queue',   lr.this_week_count           || 0, 'action target', 'good',
          { pageId: 'leads', applyFn: 'applyLeadKpiFilter', applyArgs: ['this_week'], label: 'Executive card: This Week Queue', resetFn: 'resetLeadFilters' }),
        makeRouteCard('Needs My Response', lr.needs_my_response         || 0, 'reply now',     'bad',
          { pageId: 'leads', applyFn: 'applyLeadKpiFilter', applyArgs: ['needs_response_all'], label: 'Executive card: Needs Reply', resetFn: 'resetLeadFilters' }),
        makeRouteCard('Hot Reactivation',  lr.hot_reactivation_leads    || lr.hot_leads  || 0, 'positive signal + recruiter', 'good',
          { pageId: 'leads', applyFn: 'applyLeadKpiFilter', applyArgs: ['hot_reactivation'], label: 'Executive card: Hot Reactivation', resetFn: 'resetLeadFilters' }),
        makeRouteCard('Warm Reactivation', lr.warm_reactivation_leads   || lr.warm_leads || 0, 'opportunity signals', 'warn',
          { pageId: 'leads', applyFn: 'applyLeadKpiFilter', applyArgs: ['warm_reactivation'], label: 'Executive card: Warm Reactivation', resetFn: 'resetLeadFilters' }),
        makeRouteCard('Career Site',       lr.career_site_follow_ups    || 0, 'submit CV', '',
          { pageId: 'leads', applyFn: 'applyLeadKpiFilter', applyArgs: ['talent_pool'], label: 'Executive card: Career Site', resetFn: 'resetLeadFilters' }),
        makeRouteCard('Follow-ups Due',    lr.follow_up_due             || 0, '7-120d window', '',
          { pageId: 'leads', applyFn: 'applyLeadKpiFilter', applyArgs: ['follow_up_due_status'], label: 'Executive card: Follow-ups Due', resetFn: 'resetLeadFilters' }),
      ].join('');
    } else {
      lrRow.style.display = 'none';
      if (lrLabel) lrLabel.style.display = 'none';
    }
  }

  // Untapped Network Opportunity row (Part 19 — additive, own section)
  // D.untapped_network_summary is the lightweight extract (summary counts +
  // match_method_breakdown, no per-contact arrays) shipped with
  // executive_overview.json; D.untapped_network (the full object) is
  // preferred if the Untapped Network page has already been visited.
  const un = D.untapped_network || D.untapped_network_summary || {};
  const unSum = un.summary || {};
  const unRow = document.getElementById('kpi-untapped');
  const unLabel = document.getElementById('kpi-untapped-label');
  const unDiag = document.getElementById('untapped-diagnosis-text');
  if (unRow) {
    if (un.available) {
      unRow.style.display = '';
      if (unLabel) unLabel.style.display = '';
      unRow.innerHTML = [
        makeRouteCard('Never Contacted — Confirmed', unSum.never_contacted_confirmed || 0, 'existing connections, no conversation', 'warn',
          { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['never_confirmed'], label: 'Executive card: Never Contacted', resetFn: 'resetUntappedFilters' }),
        makeRouteCard('High-Value Untapped',         unSum.high_value_untapped       || 0, 'strong persona + market fit', 'good',
          { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['high_value'], label: 'Executive card: High-Value Untapped', resetFn: 'resetUntappedFilters' }),
        makeRouteCard('Recruiters/TA Untapped',      (unSum.recruiters_untapped||0) + (unSum.ta_untapped||0), 'never messaged', '',
          { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['recruiters_ta'], label: 'Executive card: Recruiters/TA Untapped', resetFn: 'resetUntappedFilters' }),
        makeRouteCard('Hiring Managers Untapped',    unSum.hiring_managers_untapped  || 0, 'never messaged', '',
          { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['hm'], label: 'Executive card: Hiring Managers Untapped', resetFn: 'resetUntappedFilters' }),
        makeRouteCard('Primary LATAM/USD Untapped',  unSum.latam_usd_untapped        || 0, '90% short-term focus', 'good',
          { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['latam'], label: 'Executive card: Primary LATAM/USD Untapped', resetFn: 'resetUntappedFilters' }),
        makeRouteCard('Europe Exploratory Untapped', unSum.spain_eu_untapped         || 0, '10% exploratory', '',
          { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['spain_eu'], label: 'Executive card: Europe Exploratory Untapped', resetFn: 'resetUntappedFilters' }),
        makeRouteCard('This Week Untapped Queue',    unSum.this_week_queue_count     || 0, 'ready to activate', 'good',
          { pageId: 'untapped', applyFn: 'applyUntappedKpiFilter', applyArgs: ['this_week'], label: 'Executive card: This Week Untapped Queue', resetFn: 'resetUntappedFilters' }),
      ].join('');
      if (unDiag) {
        unDiag.style.display = '';
        const hv = unSum.high_value_untapped || 0;
        unDiag.textContent = 'You already have ' + hv.toLocaleString() + ' high-value first-degree connections with no known '
          + 'conversation history. Activating existing connections may be more efficient than relying only on new connection '
          + 'requests — this does not claim any guaranteed outcome, just that the outreach barrier (connection accepted) is already cleared.';
      }
    } else {
      unRow.style.display = 'none';
      if (unLabel) unLabel.style.display = 'none';
      if (unDiag) unDiag.style.display = 'none';
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
      // Defense-in-depth (Part 2): raw V2 UNKNOWN is a technical limitation,
      // never a red "dashboard is failing" alert — even if an older cached
      // JSON still carries a HIGH UNKNOWN flag, it can never render red here.
      const isUnknownNote = /UNKNOWN/i.test(f);
      const cls  = f.includes('No critical') ? 'alert-good' : (isUnknownNote ? 'alert-info' : (f.startsWith('HIGH') ? 'alert-bad' : 'alert-warn'));
      const icon = f.includes('No critical') ? '&#9989;' : (isUnknownNote ? '&#8505;&#65039;' : '&#9888;&#65039;');
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
  renderGapActionPlan(gap);
}

// ── Connection Gap Action Plan (Objective 5) — built entirely from the
// existing gap_analysis rows (market/persona/gap/urgency/timeframe/
// recommended_action), no new backend data. ─────────────────────────────────
function _gapVolumeForUrgency(urgency) {
  return ({ Critical: '8-10/wk', High: '5-7/wk', Medium: '2-4/wk', Low: '1-2/wk', Saturated: 'maintain only' })[urgency] || '2-4/wk';
}

window.applyGapPersonaMarketFilter = function(market, urgency) {
  const mf = document.getElementById('gap-market-filter');
  const uf = document.getElementById('gap-urgency-filter');
  if (mf) mf.value = market || '';
  if (uf) uf.value = urgency || '';
  window.applyGapFilters();
  const table = document.getElementById('gap-table');
  if (table) table.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

// Readable market labels — display-only, never fed into a LinkedIn query.
// Mirrors src/strategic_gap_search_builder.py's MARKET_DISPLAY_LABEL.
const GAP_MARKET_DISPLAY_LABEL = {
  LATAM_USD: 'LATAM/USD', US_CANADA_NEARSHORE: 'US/Canada Nearshore',
  SPAIN_EU: 'Spain/EU', EUROPE: 'Europe',
};
function gapMarketLabel(market) { return GAP_MARKET_DISPLAY_LABEL[market] || String(market || '').replace(/_/g, ' '); }

const SEARCH_QUALITY_COLOR = { 'High precision': '#22c55e', 'Medium precision': '#f59e0b', 'Broad discovery': '#8b949e' };
function searchQualityBadge(q) {
  const color = SEARCH_QUALITY_COLOR[q] || '#8b949e';
  return '<span class="urgency-badge" style="background:' + color + '20;color:' + color + ';border:1px solid ' + color + '">' + (q || 'Broad discovery') + '</span>';
}

// Builds one query row: label + query text + Open Search + Copy Query.
// query is always a plain real-world phrase from the backend search_pack
// (src/strategic_gap_search_builder.py) — never the raw market/persona
// bucket labels, and never a nested Boolean expression.
function _gapQueryRow(label, query, color) {
  const qAttr = escapeAttr(query);
  return '<div class="search-pack-row">'
    + '<span style="font-size:.7rem;font-weight:700;color:' + (color || '#8b949e') + ';min-width:56px;flex-shrink:0">' + label + '</span>'
    + '<code class="search-query-code" style="flex:1 1 160px">' + query + '</code>'
    + '<a href="https://www.linkedin.com/search/results/people/?keywords=' + encodeURIComponent(query) + '" target="_blank" rel="noopener" '
    + 'class="search-pack-btn" style="background:var(--accent);color:#fff;text-decoration:none" title="Open People Search on LinkedIn">Open Search</a>'
    + '<button type="button" class="btn-ghost search-pack-btn" data-query="' + qAttr + '" onclick="copyQueryBtn(this)" title="Copy this query to clipboard">Copy Query</button>'
    + '</div>';
}

function _gapActionCard(r) {
  const market = r.market || '';
  const persona = r.persona || '';
  const marketLabel = gapMarketLabel(market);
  const volume = _gapVolumeForUrgency(r.urgency_level);
  // search_pack is precomputed server-side by build_strategic_gap_search_pack
  // (src/strategic_gap_search_builder.py) — real-world title+region terms,
  // never the raw LATAM_USD/SPAIN_EU/US_CANADA_NEARSHORE bucket label.
  const sp = r.search_pack || {};
  const primary = sp.primary_query || ('data engineer ' + persona.toLowerCase());
  const fallback = sp.secondary_query || (sp.fallback_queries && sp.fallback_queries[0]) || '';
  const mAttr = escapeAttr(market);
  const uAttr = escapeAttr(r.urgency_level || '');
  return '<div class="plan-card gap-plan-card ' + (r.urgency_level||'').toLowerCase() + '">'
    + '<div class="plan-card-header"><div>'
    + '<div class="plan-card-title">Add ' + fmt(r.gap_count) + ' ' + persona + ' — ' + marketLabel + '</div>'
    + '<div class="plan-card-meta">Current ' + fmt(r.current_count) + ' · Target ' + fmt(r.target_count) + ' · Gap ' + fmt(r.gap_count)
    + ' · ' + (r.timeframe||'') + ' · pace: ' + volume + '</div>'
    + '</div><div style="display:flex;flex-direction:column;gap:.25rem;align-items:flex-end">' + urgencyBadge(r.urgency_level) + searchQualityBadge(sp.search_quality) + '</div></div>'
    + _gapQueryRow('Primary', primary, '#3b82f6')
    + (fallback ? _gapQueryRow('Fallback', fallback, '#8b5cf6') : '')
    + '<div style="font-size:.7rem;color:var(--info);margin-top:.3rem;overflow-wrap:break-word">&#127717; ' + (sp.recommended_filters || 'People · 2nd degree') + '</div>'
    + '<div class="plan-reason" style="overflow-wrap:break-word">' + (sp.search_rationale || r.strategic_reason || '').substring(0, 220) + '</div>'
    + '<div class="search-pack-row" style="border-bottom:none">'
    + '<button type="button" class="btn-ghost search-pack-btn" onclick="applyGapDashboardFilter(\'' + mAttr + '\',\'' + escapeAttr(persona) + '\',\'' + uAttr + '\')" title="Filter the gap table and show the people behind this gap">Apply Dashboard Filter</button>'
    + '</div>'
    + '</div>';
}

// "Apply Dashboard Filter" (Part 10) — filters the gap table to this exact
// market+persona AND opens the people drilldown (Tab A) for the same
// segment, so clicking one button both narrows the table above and shows
// "Showing X people for MARKET — PERSONA" (or an honest empty state) below.
window.applyGapDashboardFilter = function(market, persona, urgency) {
  applyGapPersonaMarketFilter(market, urgency);
  openGapDrilldown(market, persona);
};

function renderGapActionPlan(gap) {
  const closeGrid = document.getElementById('gap-plan-close-grid');
  const onTrackGrid = document.getElementById('gap-plan-ontrack-grid');
  const saturatedGrid = document.getElementById('gap-plan-saturated-grid');
  if (!closeGrid && !onTrackGrid && !saturatedGrid) return;

  const critHigh = gap.filter(r => r.urgency_level === 'Critical' || r.urgency_level === 'High')
    .sort((a, b) => (b.gap_percentage||0) - (a.gap_percentage||0)).slice(0, 10);
  const onTrack = gap.filter(r => r.urgency_level === 'Low' || r.urgency_level === 'Medium')
    .sort((a, b) => (a.gap_percentage||0) - (b.gap_percentage||0)).slice(0, 10);
  const saturated = gap.filter(r => r.urgency_level === 'Saturated').slice(0, 10);

  if (closeGrid) closeGrid.innerHTML = critHigh.length
    ? critHigh.map(_gapActionCard).join('')
    : '<div class="alert alert-good" style="grid-column:1/-1"><span class="alert-icon">&#9989;</span><span>No Critical/High urgency gaps right now.</span></div>';
  if (onTrackGrid) onTrackGrid.innerHTML = onTrack.length
    ? onTrack.map(_gapActionCard).join('')
    : '<div class="alert alert-info" style="grid-column:1/-1"><span>No Low/Medium urgency gaps to show.</span></div>';
  if (saturatedGrid) saturatedGrid.innerHTML = saturated.length
    ? saturated.map(_gapActionCard).join('')
    : '<div class="alert alert-info" style="grid-column:1/-1"><span>No saturated markets/personas yet.</span></div>';
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
  tbody.innerHTML = filteredGap.map(r => {
    const mAttr = escapeAttr(r.market || '');
    const pAttr = escapeAttr(r.persona || '');
    return '<tr style="cursor:pointer" tabindex="0" role="button" '
    + 'onclick="openGapDrilldown(\'' + mAttr + '\',\'' + pAttr + '\')" '
    + 'onkeydown="if(event.key===\'Enter\'){openGapDrilldown(\'' + mAttr + '\',\'' + pAttr + '\')}">'
    + '<td>' + marketBadge(r.market) + '</td>'
    + '<td style="white-space:nowrap">' + (r.persona||'') + '</td>'
    + '<td>' + fmt(r.current_count) + '</td>'
    + '<td>' + fmt(r.target_count) + '</td>'
    + '<td><strong style="color:#ef4444">' + fmt(r.gap_count) + '</strong></td>'
    + '<td>' + fmt(r.gap_percentage) + '%</td>'
    + '<td>' + urgencyBadge(r.urgency_level) + '</td>'
    + '<td>' + (r.timeframe||'') + '</td>'
    + '<td style="white-space:normal;font-size:0.74rem;max-width:200px">' + ((r.strategic_reason||'').substring(0,100)) + '</td>'
    + '</tr>';
  }).join('');
}

function renderGapChart() {
  const sorted = [...filteredGap].sort((a,b)=>(b.gap_count||0)-(a.gap_count||0)).slice(0,15);
  const labels = sorted.map(r => (r.market||'').replace('_',' ') + ' — ' + (r.persona||''));
  const values = sorted.map(r => r.gap_count || 0);
  const colors = sorted.map(r => URGENCY_COLORS[r.urgency_level] || '#555');
  barChart('chart-gap', labels, values, colors, {
    horizontal: true,
    onBarClick: i => { const r = sorted[i]; if (r) openGapDrilldown(r.market, r.persona); },
  });
}

// ── Strategic Gap people drill-down (Part 3) ─────────────────────────────────
// Mirrors src/export_public_dashboard_data.py's MARKET_V2_TO_V5_BUCKETS —
// keep both in sync if either changes.
const GAP_MARKET_TO_V5_BUCKETS = {
  LATAM_USD:            ['LATAM_USD_CONFIRMED', 'LATAM_USD_LIKELY'],
  US_CANADA_NEARSHORE:  ['US_CANADA_CONFIRMED', 'US_CANADA_LIKELY'],
  SPAIN_EU:             ['SPAIN_EU_CONFIRMED', 'SPAIN_EU_LIKELY'],
  EUROPE:               ['EUROPE_CONFIRMED', 'EUROPE_LIKELY'],
};
const GAP_PERSONA_PRIORITY = ['Recruiter', 'Talent Acquisition', 'Hiring Manager', 'Engineering Manager', 'Head of Data', 'Data Engineering Manager', 'Director'];

function _gapTabStats(tabId, shown, total, extra) {
  const el = document.getElementById(tabId + '-stats');
  if (el) el.textContent = 'Showing ' + shown + ' of ' + total + (extra ? ' — ' + extra : '');
}

window.openGapDrilldown = function(market, persona) {
  const panel = document.getElementById('gap-drilldown');
  const titleEl = document.getElementById('gap-drilldown-title');
  if (!panel) return;
  const marketLabel = gapMarketLabel(market);
  if (titleEl) titleEl.textContent = (persona || '') + ' — ' + marketLabel;

  // Tab A — Current Contacts Counted (segment='current'; count always equals
  // this gap row's own current_count since both are built from the same
  // predicate — see build_strategic_gap_people_drilldown in export_public_dashboard_data.py)
  const allGapPeople = D.strategic_gap_people_drilldown || [];
  const current = allGapPeople.filter(p => p.market === market && p.persona === persona && p.segment === 'current');
  const currentTbody = document.getElementById('gap-tab-current-tbody');
  if (currentTbody) {
    currentTbody.innerHTML = current.length ? current.map(p => '<tr>'
      + '<td style="white-space:nowrap">' + (p.full_name||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (p.company_clean||'—') + '</td>'
      + '<td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (p.position_clean||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (p.persona||'—') + '</td>'
      + '<td>' + marketBadge(p.opportunity_bucket||'—') + '</td>'
      + '<td>' + fmt(p.priority_score||0) + '</td>'
      + '<td style="font-size:0.72rem">' + (p.outreach_status || p.contact_history_status || '—') + '</td>'
      + '<td style="font-size:0.75rem;white-space:nowrap">' + (p.connected_on||'—') + '</td>'
      + '<td>' + (p.url ? '<a href="' + p.url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
      + '</tr>').join('')
      : '<tr><td colspan="9" style="text-align:center;color:var(--text-muted)">No existing people found for this gap. Use the search query to create new contacts.</td></tr>';
  }
  const currentStatsEl = document.getElementById('gap-tab-current-stats');
  if (currentStatsEl) currentStatsEl.textContent = 'Showing ' + current.length + ' people for ' + marketLabel + ' — ' + persona;

  // Tab B — New This Week Matching This Gap (segment='new_this_week',
  // appended by src/weekly_kpi_delta.py — empty until a weekly refresh runs
  // with a previous snapshot baseline present)
  const newThisWeek = allGapPeople.filter(p => p.market === market && p.persona === persona && p.segment === 'new_this_week');
  const newTbody = document.getElementById('gap-tab-new-tbody');
  if (newTbody) {
    newTbody.innerHTML = newThisWeek.length ? newThisWeek.map(p => '<tr>'
      + '<td style="white-space:nowrap">' + (p.full_name||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (p.company_clean||'—') + '</td>'
      + '<td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (p.position_clean||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (p.persona||'—') + '</td>'
      + '<td>' + marketBadge(p.opportunity_bucket||'—') + '</td>'
      + '<td style="font-size:0.75rem;white-space:nowrap">' + (p.connected_on||'—') + '</td>'
      + '<td>' + fmt(p.priority_score||0) + '</td>'
      + '<td style="font-size:0.72rem;max-width:200px">' + (p.reason||'—') + '</td>'
      + '<td>' + (p.url ? '<a href="' + p.url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
      + '</tr>').join('')
      : '<tr><td colspan="9" style="text-align:center;color:var(--text-muted)">No new connections matched this gap in the latest weekly snapshot.</td></tr>';
  }
  _gapTabStats('gap-tab-new', newThisWeek.length, newThisWeek.length);

  // Tab C — Recommended People to Activate Next: never-contacted Untapped
  // Network contacts whose opportunity_bucket matches this gap's market,
  // ranked by untapped_outreach_score, persona priority as tiebreak.
  const buckets = GAP_MARKET_TO_V5_BUCKETS[market] || [];
  const untappedSource = (D.untapped_network || {}).top_untapped_contacts || [];
  let recommended = untappedSource.filter(c =>
    c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' && buckets.includes(c.opportunity_bucket));
  if (persona && GAP_PERSONA_PRIORITY.includes(persona)) {
    // Prefer exact persona match first, then fall back to the same
    // persona-priority ladder used elsewhere, so a Recruiter gap surfaces
    // recruiters first even if very few exact matches exist.
    const exact = recommended.filter(c => c.persona === persona);
    const rest = recommended.filter(c => c.persona !== persona);
    recommended = exact.concat(rest);
  }
  recommended = recommended
    .sort((a, b) => (parseFloat(b.untapped_outreach_score) || 0) - (parseFloat(a.untapped_outreach_score) || 0))
    .slice(0, 50);
  const recTbody = document.getElementById('gap-tab-recommended-tbody');
  if (recTbody) {
    recTbody.innerHTML = recommended.length ? recommended.map(p => '<tr>'
      + '<td style="white-space:nowrap">' + (p.full_name||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (p.company_clean||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (p.persona||'—') + '</td>'
      + '<td>' + marketBadge(p.opportunity_bucket||'—') + '</td>'
      + '<td>' + fmt(p.untapped_outreach_score||0) + '</td>'
      + '<td style="font-size:0.72rem;max-width:220px">' + (p.first_message_angle||'—') + '</td>'
      + '<td>' + (p.profile_url ? '<a href="' + p.profile_url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
      + '</tr>').join('')
      : '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">No never-contacted candidates match this gap right now — check Untapped Network directly.</td></tr>';
  }
  _gapTabStats('gap-tab-recommended', recommended.length, untappedSource.length, 'never-contacted, matching bucket');

  panel.style.display = '';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

// ── PAGE 4: Action Plan ───────────────────────────────────────────────────────

// Part 17 — per-tier "Recommended LinkedIn Filters" metadata. Each of the 4
// search tiers gets ITS OWN distinct filter recipe (not shared boilerplate).
const TIER_COLORS = { Broad: '#f59e0b', Precision: '#3b82f6', Persona: '#8b5cf6', Company: '#22c55e' };

const REGION_FILTER_META = {
  LATAM_USD: {
    locations: 'Brazil, Argentina, Colombia, Mexico, Chile, Uruguay, Peru',
    companies: ['Hays', 'Michael Page', 'Randstad', 'NTT DATA', 'BairesDev', 'Nearsure'],
    industry: 'Staffing & Recruiting, IT Services & IT Consulting',
    personaFocus: 'Talent Acquisition, Technical Recruiter, IT Recruiter',
  },
  SOUTH_AMERICA: {
    locations: 'Brazil, Argentina, Colombia, Chile, Peru',
    companies: ['Michael Page', 'Robert Half', 'Globant', 'CI&T'],
    industry: 'Staffing & Recruiting, IT Services & IT Consulting',
    personaFocus: 'Talent Acquisition, Recruiter, HR Business Partner',
  },
  US_NEARSHORE: {
    locations: 'United States, Canada',
    companies: ['Nearsure', 'AgileEngine', 'Wizeline', 'Andela', 'BairesDev'],
    industry: 'IT Services & IT Consulting, Staffing & Recruiting',
    personaFocus: 'Technical Recruiter, Talent Acquisition Partner, Delivery Manager',
  },
  STAFFING: {
    locations: 'Brazil, LATAM, United States (remote-first)',
    companies: ['Hays', 'NTT DATA', 'Randstad', 'Capgemini', 'Accenture', 'TCS'],
    industry: 'Staffing & Recruiting, IT Services & IT Consulting',
    personaFocus: 'Recruiter, Talent Acquisition, Account Manager',
  },
  HIRING_MGR: {
    locations: 'Brazil, United States, Canada, Remote',
    companies: ['Databricks', 'Snowflake', 'Microsoft', 'Nubank', 'iFood'],
    industry: 'Software Development, IT Services & IT Consulting, Data Infrastructure',
    personaFocus: 'Head of Data, Data Engineering Manager, Director of Data',
  },
  SPAIN_EU: {
    locations: 'Spain, Portugal, Germany, Netherlands, Ireland',
    companies: ['Stratesys', 'ERNI', 'Minsait', 'Indra', 'Capgemini'],
    industry: 'IT Services & IT Consulting, Staffing & Recruiting',
    personaFocus: 'Talent Acquisition, Technical Recruiter, HR Business Partner',
  },
  PORTUGAL_EU: {
    locations: 'Portugal, Spain, Ireland',
    companies: ['ERNI', 'Critical TechWorks', 'Farfetch', 'Talkdesk'],
    industry: 'IT Services & IT Consulting, Software Development',
    personaFocus: 'Talent Acquisition, Technical Recruiter',
  },
  DIGITAL_NOMAD: {
    locations: 'Europe (remote-first companies)',
    companies: ['GitLab', 'Automattic', 'Toptal', 'Remote.com'],
    industry: 'Software Development, IT Services & IT Consulting',
    personaFocus: 'Remote Talent Acquisition, People Ops, Recruiter',
  },
};

// Builds the 4 distinct per-tier filter recipes (Parts 16-17) for one region/profile.
function buildTierFilters(regionKey, pack) {
  const meta = REGION_FILTER_META[regionKey] || REGION_FILTER_META.LATAM_USD;
  const primaryCompany = (meta.companies && meta.companies[0]) || 'target company';
  return [
    {
      tier: 'Broad', query: pack.broad,
      purpose: 'Market discovery — see the shape of the market before narrowing.',
      peopleJobs: 'People', degree: '2nd degree', locations: meta.locations,
      currentCompany: 'Do not restrict company', industry: '—',
      activelyHiring: 'Not required', expectedPersona: 'Mixed / broad', expectedPrecision: 'Low',
      whenToUse: 'Use for discovery only — first pass on a new market.',
    },
    {
      tier: 'Precision', query: pack.precision,
      purpose: 'Default daily outreach search.',
      peopleJobs: 'People', degree: '2nd degree', locations: meta.locations,
      currentCompany: 'Staffing, consulting, nearshore firms', industry: meta.industry,
      activelyHiring: 'Yes, when available', expectedPersona: 'Recruiter / Talent Acquisition', expectedPrecision: 'Medium-High',
      whenToUse: 'Default daily search — best mix of volume and relevance.',
    },
    {
      tier: 'Persona', query: pack.persona,
      purpose: 'High-quality recruiter/persona targeting.',
      peopleJobs: 'People', degree: '2nd degree', locations: meta.locations,
      currentCompany: 'Do not restrict company', industry: meta.industry,
      activelyHiring: 'Optional', expectedPersona: meta.personaFocus, expectedPrecision: 'High',
      whenToUse: 'Use when Broad/Precision results contain irrelevant profiles.',
    },
    {
      tier: 'Company', query: pack.company,
      purpose: 'Account-based networking at a known target firm.',
      peopleJobs: 'People', degree: '2nd degree', locations: 'Optional: ' + meta.locations,
      currentCompany: primaryCompany + ' (exact target company)', industry: meta.industry,
      activelyHiring: 'Yes, when available', expectedPersona: 'Recruiter / account contact at target firm', expectedPrecision: 'Very High',
      whenToUse: 'Use for account-based networking and known target companies.',
    },
  ];
}

function escapeAttr(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Clipboard copy for "Copy Query" — never throws uncaught (fail-safe rule).
window.copyQueryBtn = function(btn) {
  try {
    const q = btn.getAttribute('data-query') || '';
    const restore = () => { const old = btn.dataset.label || btn.textContent; btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = old; }, 1200); };
    if (!btn.dataset.label) btn.dataset.label = btn.textContent;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(q).then(restore).catch(() => { try { _fallbackCopyText(q); restore(); } catch(_) {} });
    } else {
      _fallbackCopyText(q); restore();
    }
  } catch(_) { /* never let a copy-button click become an uncaught error */ }
};

function _fallbackCopyText(text) {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.focus(); ta.select();
  try { document.execCommand('copy'); } finally { document.body.removeChild(ta); }
}

function renderPlan() {
  // ── Search pack helper ───────────────────────────────────────────────────────
  function liUrl(q) {
    return 'https://www.linkedin.com/search/results/people/?keywords=' + encodeURIComponent(q);
  }

  // Build a compact stacked search-pack block — 4 tiers, EACH with its own
  // distinct filter recipe (Part 16-17), an expandable "Recommended LinkedIn
  // Filters" panel, an Open Search link, and a Copy Query button.
  function searchPack(pack, filters, noise) {
    const tiers = buildTierFilters(pack.key, pack);
    const rows = tiers.map(t => {
      const color = TIER_COLORS[t.tier] || '#8b949e';
      const qAttr = escapeAttr(t.query);
      return '<div class="search-pack-row">'
        + '<span style="font-size:.7rem;font-weight:700;color:' + color + ';min-width:64px;flex-shrink:0">' + t.tier + '</span>'
        + '<div style="flex:1 1 180px;min-width:0">'
        + '<code class="search-query-code">' + t.query + '</code>'
        + '<span style="font-size:.65rem;color:var(--text-muted)">' + t.purpose + ' — precision: ' + t.expectedPrecision + '</span>'
        + '</div>'
        + '<a href="' + liUrl(t.query) + '" target="_blank" rel="noopener" class="search-pack-btn" '
        + 'style="background:var(--accent);color:#fff;text-decoration:none" '
        + 'title="Open People Search on LinkedIn">Open Search</a>'
        + '<button type="button" class="btn-ghost search-pack-btn" data-query="' + qAttr + '" onclick="copyQueryBtn(this)" '
        + 'title="Copy this query to clipboard">Copy Query</button>'
        + '<details style="flex:1 0 100%;margin-top:.3rem">'
        + '<summary style="cursor:pointer;font-size:.68rem;color:var(--text-muted);font-weight:600">&#9881; Recommended LinkedIn Filters</summary>'
        + '<div style="font-size:.7rem;color:var(--text-secondary);padding:.4rem .3rem;line-height:1.7;background:var(--bg-surface);border-radius:4px;margin-top:.25rem">'
        + '<div><strong>Search type:</strong> ' + t.peopleJobs + '</div>'
        + '<div><strong>Connection degree:</strong> ' + t.degree + '</div>'
        + '<div><strong>Location filters:</strong> ' + t.locations + '</div>'
        + '<div><strong>Current company filters:</strong> ' + t.currentCompany + '</div>'
        + '<div><strong>Industry suggestions:</strong> ' + t.industry + '</div>'
        + '<div><strong>Actively hiring:</strong> ' + t.activelyHiring + '</div>'
        + '<div><strong>Expected persona:</strong> ' + t.expectedPersona + '</div>'
        + '<div><strong>Expected precision:</strong> ' + t.expectedPrecision + '</div>'
        + '<div><strong>When to use:</strong> ' + t.whenToUse + '</div>'
        + '</div></details>'
        + '</div>';
    }).join('');
    const noiseHtml = noise
      ? '<div style="font-size:.7rem;color:var(--text-muted);margin-top:.35rem">&#9888; Noise tip: if too many irrelevant results, try adding <code style="font-size:.7rem">NOT Student</code> or <code style="font-size:.7rem">NOT Course</code></div>'
      : '';
    return '<div style="background:var(--bg-elevated);border:1px solid var(--border);border-radius:6px;padding:.6rem .75rem;margin:.5rem 0;max-width:100%;overflow:hidden">'
      + '<div style="font-size:.7rem;font-weight:700;color:var(--text-secondary);margin-bottom:.25rem">&#128269; SEARCH PACK — Broad / Precision / Persona / Company (each with its own filters)</div>'
      + rows
      + (filters ? '<div style="font-size:.7rem;color:var(--info);margin-top:.4rem;overflow-wrap:anywhere">&#127717; Market context: ' + filters + '</div>' : '')
      + '<div style="font-size:.7rem;color:var(--text-muted);margin-top:.3rem;font-style:italic">Do not encode every criterion into the keyword query — use the query for intent and LinkedIn filters (degree, geography, company, persona) for refinement.</div>'
      + noiseHtml
      + '</div>';
  }

  // Compact, always-visible single-line version — just the Precision tier
  // (the "default daily search" tier per the guidance above) — used on the
  // collapsed day/week card face. The full 4-tier searchPack() above still
  // lives inside that card's "Expandir detalhes" accordion.
  function primaryTierSnippet(pack) {
    const tiers = buildTierFilters(pack.key, pack);
    const t = tiers.find(x => x.tier === 'Precision') || tiers[0];
    const color = TIER_COLORS[t.tier] || '#8b949e';
    const qAttr = escapeAttr(t.query);
    return '<div class="search-pack-row" style="border-bottom:none;padding:.3rem 0">'
      + '<span style="font-size:.7rem;font-weight:700;color:' + color + ';min-width:64px;flex-shrink:0">' + t.tier + '</span>'
      + '<code class="search-query-code" style="flex:1 1 140px">' + t.query + '</code>'
      + '<a href="' + liUrl(t.query) + '" target="_blank" rel="noopener" class="search-pack-btn" '
      + 'style="background:var(--accent);color:#fff;text-decoration:none" title="Open People Search on LinkedIn">Open Search</a>'
      + '<button type="button" class="btn-ghost search-pack-btn" data-query="' + qAttr + '" onclick="copyQueryBtn(this)" '
      + 'title="Copy this query to clipboard">Copy Query</button>'
      + '</div>';
  }

  // ── Search packs ─────────────────────────────────────────────────────────────
  const SP = {
    LATAM_USD: {
      broad:     'data engineer recruiter LATAM',
      precision: '"Data Engineer" AND Recruiter AND LATAM',
      persona:   '"Talent Acquisition" AND "Data Engineer" AND LATAM',
      company:   'Recruiter AND "Data Engineer" AND Hays',
    },
    SOUTH_AMERICA: {
      broad:     'data engineer recruiter South America',
      precision: '"Data Engineer" AND Recruiter AND Brazil',
      persona:   '"Talent Acquisition" AND "Data Engineer" AND Brazil',
      company:   'Recruiter AND "Data Engineer" AND "Michael Page"',
    },
    US_NEARSHORE: {
      broad:     'nearshore recruiter data engineer',
      precision: '"Data Engineer" AND Recruiter AND nearshore',
      persona:   '"Talent Acquisition" AND "Data Engineer" AND LATAM',
      company:   'Recruiter AND "Data Engineer" AND Nearsure',
    },
    STAFFING: {
      broad:     'data engineer recruiter staffing',
      precision: '"Data Engineer" AND Recruiter AND contractor',
      persona:   '"Talent Acquisition" AND "Cloud Data" AND contractor',
      company:   'Recruiter AND "Data Engineer" AND "NTT DATA"',
    },
    HIRING_MGR: {
      broad:     'data engineering manager remote',
      precision: '"Data Engineering Manager" AND Remote',
      persona:   '"Head of Data" AND "Data Engineer"',
      company:   '"Data Engineering Manager" AND Databricks',
    },
    SPAIN_EU: {
      broad:     'data engineer recruiter Spain',
      precision: '"Data Engineer" AND Recruiter AND Spain',
      persona:   '"Talent Acquisition" AND "Data Engineer" AND Spain',
      company:   'Recruiter AND "Data Engineer" AND Stratesys',
    },
    PORTUGAL_EU: {
      broad:     'data engineer recruiter Portugal',
      precision: '"Data Engineer" AND Recruiter AND Portugal',
      persona:   '"Talent Acquisition" AND "Data Engineer" AND Portugal',
      company:   'Recruiter AND "Data Engineer" AND ERNI',
    },
    DIGITAL_NOMAD: {
      broad:     'remote data engineer recruiter Europe',
      precision: '"Data Engineer" AND Recruiter AND remote',
      persona:   '"Talent Acquisition" AND "Data Engineer" AND Europe',
      company:   'Recruiter AND "Data Engineer" AND remote',
    },
  };
  // Tag each search pack with its region key so searchPack() can look up
  // its distinct per-tier filter recipe (Part 16-17).
  Object.keys(SP).forEach(k => { SP[k].key = k; });

  // ── Filters ──────────────────────────────────────────────────────────────────
  const F = {
    LATAM_USD:    'People · 2nd degree · Locations: Brazil, Argentina, Colombia, Mexico, Chile, Uruguay, Peru · Keywords: recruiter, talent acquisition, data engineer, LATAM, remote',
    US_NEARSHORE: 'People · 2nd degree · Locations: United States, Canada · Keywords: LATAM, nearshore, remote contractor, data engineer, recruiter',
    HIRING_MGR:   'People · 2nd degree · Seniority: Manager/Director/Head · Keywords: Head of Data, Data Engineering Manager, Director of Data',
    SPAIN_EU:     'People · 2nd degree · Locations: Spain, Portugal, Germany, Netherlands, Ireland · Keywords: data engineer, recruiter, talent acquisition, remote',
    STAFFING:     'People · 2nd degree · Companies: Hays, Michael Page, Randstad, Robert Half, NTT DATA, Capgemini, Accenture, TCS, Globant, BairesDev, Nearsure',
  };

  // Legacy single-query fallback for data-driven 60/90 cards
  const Q = {
    LATAM_USD:      SP.LATAM_USD.precision,
    SOUTH_AMERICA:  SP.SOUTH_AMERICA.precision,
    US_NEARSHORE:   SP.US_NEARSHORE.precision,
    STAFFING:       SP.STAFFING.precision,
    HIRING_MGR:     SP.HIRING_MGR.precision,
    SPAIN_EU:       SP.SPAIN_EU.precision,
    PORTUGAL_EU:    SP.PORTUGAL_EU.precision,
    DIGITAL_NOMAD:  SP.DIGITAL_NOMAD.precision,
  };

  // ── 7-day sprint ─────────────────────────────────────────────────────────────
  const sprint = [
    {
      day: 'Monday', icon: '&#128293;',
      action: 'LATAM/USD Hot Lead Reactivation',
      detail: 'Message top hot/warm recruiters from Lead Reactivation. Prioritize who replied, asked for CV, or had real conversations.',
      targets: { DMs: '5–10', Connects: '0', Comments: '0' },
      sp: SP.LATAM_USD, filters: F.LATAM_USD, noise: true,
      angle: 'Hi [Name], I wanted to reconnect — I\'m currently open to remote LATAM/USD Data Engineering roles. My focus is Azure/AWS data pipelines, Databricks, SQL and ETL/ELT. Happy to share my updated profile.',
    },
    {
      day: 'Tuesday', icon: '&#127758;',
      action: 'LATAM/South America Recruiter Search',
      detail: 'Search and connect with recruiters in Brazil, Argentina, Colombia, Mexico, Chile. Focus on staffing and tech companies.',
      targets: { DMs: '0', Connects: '10–15', Comments: '0' },
      sp: SP.SOUTH_AMERICA, filters: F.LATAM_USD, noise: true,
      angle: 'Hi [Name], thanks for connecting. I\'m a Data Engineer focused on Azure, AWS, Databricks, SQL and ETL/ELT, currently open to remote LATAM/USD contractor roles. Happy to stay in touch if you work with data engineering positions.',
    },
    {
      day: 'Wednesday', icon: '&#127482;&#127480;',
      action: 'Nearshore / US-Canada Contractor Ecosystem',
      detail: 'Search for US and Canada recruiters hiring LATAM contractors. Target AgileEngine, Andela, Gorilla Logic, Wizeline, Turing, Deel-adjacent companies.',
      targets: { DMs: '0', Connects: '10–15', Comments: '3' },
      sp: SP.US_NEARSHORE, filters: F.US_NEARSHORE, noise: false,
      angle: 'Hi [Name], I\'m currently based in Brazil and available for remote Data Engineering roles aligned with US time zones. My focus is Azure/AWS data pipelines, Databricks, SQL and cloud analytics.',
    },
    {
      day: 'Thursday', icon: '&#127968;',
      action: 'Hiring Managers and Data Leaders',
      detail: 'Search Data Engineering Managers, Heads of Data, Directors in LATAM-friendly or globally remote companies. Softer angle — not a recruiter pitch.',
      targets: { DMs: '3', Connects: '5–10', Comments: '3' },
      sp: SP.HIRING_MGR, filters: F.HIRING_MGR, noise: false,
      angle: 'Hi [Name], I\'m connecting because I follow data engineering and cloud data teams working with scalable pipelines and analytics platforms. I work with Azure, AWS, Databricks, SQL and ETL/ELT.',
    },
    {
      day: 'Friday', icon: '&#128203;',
      action: 'Content + Visibility',
      detail: 'Post or comment on LinkedIn around Data Engineering / Azure / AWS / Databricks / remote contractor work. Attract inbound recruiter contacts.',
      targets: { DMs: '0', Connects: '0', Comments: '5–10' },
      sp: null, filters: null, noise: false,
      angle: 'Post angle: "Remote Data Engineering with Azure/Databricks/dbt — what I\'ve built and what I\'m looking for next." Comment on 5–10 recruiter or company posts about data engineering.',
    },
    {
      day: 'Saturday', icon: '&#127466;&#127480;',
      action: 'Spain/EU Exploratory Layer (10% budget)',
      detail: 'Search Spain, Portugal, Germany, Netherlands, Ireland recruiters. Light touch only — do not over-invest here yet.',
      targets: { DMs: '0', Connects: '2–5', Comments: '1' },
      sp: SP.SPAIN_EU, filters: F.SPAIN_EU, noise: false,
      angle: 'Hi [Name], I\'m building my European network as I\'ll be spending time in Spain soon. I\'m a Data Engineer focused on cloud data platforms, Azure/AWS, Databricks and analytics engineering.',
    },
    {
      day: 'Sunday', icon: '&#128197;',
      action: 'Review and Pipeline Hygiene',
      detail: 'Review replies from the week. Update company mapping backlog. Refresh dashboard CSV if available. Prepare next week\'s target list.',
      targets: { DMs: '0', Connects: '0', Comments: '0' },
      sp: null, filters: null, noise: false,
      angle: 'Open outputs/unresolved_opportunity_buckets.csv → add top 10 companies to config/company_market_overrides.yml → run python src/build_strategy_layer.py to refresh dashboard.',
    },
  ];

  const sprintEl = document.getElementById('sprint-grid');
  if (sprintEl) sprintEl.innerHTML = sprint.map(s => {
    const tgt = Object.entries(s.targets).map(([k,v]) =>
      '<div class="plan-t"><div class="plan-n" style="font-size:1rem">' + v + '</div><div class="plan-l">' + k + '</div></div>'
    ).join('');
    // Compact face: day, objective, targets, ONE primary search tier, ONE
    // message angle. Everything else (full description, all 4 search
    // tiers) is inside "Expandir detalhes" so cards don't grow indefinitely.
    return '<div class="sprint-card">'
      + '<div class="sprint-day">' + s.icon + ' ' + s.day + '</div>'
      + '<div class="sprint-action">' + s.action + '</div>'
      + '<div class="plan-targets">' + tgt + '</div>'
      + (s.sp ? primaryTierSnippet(s.sp) : '')
      + '<div class="sprint-angle" style="white-space:normal;margin-top:.4rem">&#128172; ' + s.angle + '</div>'
      + '<details style="margin-top:.5rem">'
      + '<summary style="cursor:pointer;font-size:.72rem;font-weight:600;color:var(--accent-2)">Expandir detalhes &#9660;</summary>'
      + '<div class="sprint-meta" style="margin:.5rem 0">' + s.detail + '</div>'
      + (s.sp ? searchPack(s.sp, s.filters, s.noise) : '')
      + '</details>'
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
  // Compact face (title, focus, urgency, targets, one primary search tier,
  // one angle) + "Expandir detalhes" accordion for the full description and
  // all 4 search-pack tiers — same pattern as the 7-Day Sprint cards.
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
      + (w.sp ? primaryTierSnippet(w.sp) : '')
      + (w.angle ? '<div class="sprint-angle" style="white-space:normal;margin-top:.4rem">&#128172; ' + w.angle + '</div>' : '')
      + '<details style="margin-top:.5rem">'
      + '<summary style="cursor:pointer;font-size:.72rem;font-weight:600;color:var(--accent-2)">Expandir detalhes &#9660;</summary>'
      + '<div class="plan-reason" style="margin-top:.5rem">' + w.detail + '</div>'
      + (w.sp ? searchPack(w.sp, w.filters || null, w.noise || false) : '')
      + '</details>'
      + '</div>';
  }

  const week1 = [
    { urgency: 'critical', title: 'Hot/Warm Lead Reactivation', focus: 'Message history intelligence — leads who already know you',
      targets: { 'DMs': '10–15', 'Career Sites': '5', 'EU Connects': '2–3' },
      detail: 'Start with existing warm contacts. Go to Lead Reactivation → Hot + Warm tabs. Message recruiters who replied, requested CV, or shared roles. Personalize every message. Do NOT send bulk identical DMs.',
      sp: SP.LATAM_USD, filters: F.LATAM_USD, noise: true,
      angle: 'We spoke previously about data roles. I wanted to reconnect because I\'m currently open to remote Data Engineering opportunities across LATAM/US time zones.' },
    { urgency: 'high', title: 'LATAM/USD Recruiter Pipeline — New Connects', focus: 'Brazil · Argentina · Colombia · Chile · Uruguay · Mexico',
      targets: { 'Connects': '40–60', 'DMs': '5', 'Comments': '5' },
      detail: 'Search for LATAM and South America recruiters. Prioritize 2nd degree connections at staffing, consulting, and nearshore tech companies. Send personalized connection requests with a brief note.',
      sp: SP.SOUTH_AMERICA, filters: F.LATAM_USD, noise: true,
      angle: 'Hi [Name], thanks for connecting. I\'m a Data Engineer focused on Azure, AWS, Databricks, SQL and ETL/ELT, currently open to remote LATAM/USD contractor roles.' },
    { urgency: 'medium', title: 'Spain/EU Exploratory (Week 1 — Light)', focus: 'Optional — only if LATAM pipeline is on track',
      targets: { 'Connects': '2–3', 'DMs': '0', 'Comments': '1' },
      detail: '10% EU budget. Connect with 2–3 Spain or Portugal recruiters only. Do not spend more than 20 minutes here this week.',
      sp: SP.SPAIN_EU, filters: F.SPAIN_EU, noise: false,
      angle: 'Hi [Name], I\'m building my European network as I\'ll be spending time in Spain soon. I\'m a Data Engineer focused on cloud data platforms, Azure/AWS, Databricks and analytics engineering.' },
  ];

  const week2 = [
    { urgency: 'critical', title: 'South America Recruiter Expansion', focus: 'Staffing & consulting firms hiring LATAM contractors',
      targets: { 'Connects': '50–70', 'DMs': '10', 'Comments': '10' },
      detail: 'Expand beyond your existing network. Search South America recruiters and TA professionals. Focus on staffing companies and consulting firms with LATAM contractor pipelines. Comment on recruiter posts to increase visibility.',
      sp: SP.SOUTH_AMERICA, filters: F.LATAM_USD, noise: true,
      angle: 'Hi [Name], thanks for connecting. I\'m a Data Engineer focused on Azure, AWS, Databricks, SQL and ETL/ELT, currently open to remote LATAM/USD contractor roles. Happy to stay in touch.' },
    { urgency: 'high', title: 'US/Canada Nearshore Recruiters', focus: 'AgileEngine · Andela · Gorilla Logic · Wizeline · Turing · Deel',
      targets: { 'Connects': '15–20', 'DMs': '5', 'Comments': '5' },
      detail: 'Search US and Canada recruiters explicitly hiring LATAM contractors for remote nearshore roles. These companies bridge the USD income gap directly. Priority targets: AgileEngine, Gorilla Logic, Wizeline, BairesDev, Andela.',
      sp: SP.US_NEARSHORE, filters: F.US_NEARSHORE, noise: false,
      angle: 'Hi [Name], I\'m currently based in Brazil and available for remote Data Engineering roles aligned with US time zones. My focus is Azure/AWS data pipelines, Databricks, SQL and cloud analytics.' },
    { urgency: 'medium', title: 'Spain/EU Exploratory (Week 2)', focus: 'Slow build — not the main channel',
      targets: { 'Connects': '3–5', 'DMs': '0', 'Comments': '2' },
      detail: '10% EU budget. Add 3–5 Spain/EU recruiters this week. Focus on people in your network\'s 2nd degree. No DMs yet — just connections.',
      sp: SP.SPAIN_EU, filters: F.SPAIN_EU, noise: false,
      angle: 'Hi [Name], I\'m building my European network for future optionality. I\'m a Data Engineer specializing in Azure/AWS, Databricks and cloud analytics.' },
  ];

  const week3 = [
    { urgency: 'high', title: 'Hiring Managers — LATAM/Remote Companies', focus: 'Decision-makers who can create roles, not just fill them',
      targets: { 'Connects': '30–40', 'DMs': '5', 'Comments': '10' },
      detail: 'Search Data Engineering Managers, Heads of Data, Engineering Directors at LATAM-friendly or globally remote companies. Softer angle — connect and comment on posts, not a direct pitch. Build relationships.',
      sp: SP.HIRING_MGR, filters: F.HIRING_MGR, noise: false,
      angle: 'Hi [Name], I\'m connecting because I follow data engineering and cloud data teams working with scalable pipelines. I work with Azure, AWS, Databricks, SQL and ETL/ELT pipelines.' },
    { urgency: 'high', title: 'Recruiters — Keep Warm', focus: 'Do not let Week 1–2 connections go cold',
      targets: { 'Connects': '30–40', 'DMs': '10', 'Comments': '5' },
      detail: 'Keep the recruiter pipeline active. Follow up with Week 1–2 new connections who accepted but haven\'t replied. Send a brief contextual message — mention open LATAM/USD Data Engineering opportunities.',
      sp: SP.LATAM_USD, filters: F.LATAM_USD, noise: true,
      angle: 'Hi [Name], thanks for accepting — I\'m currently open to remote Data Engineering roles. My stack: Azure, AWS, Databricks, dbt, Airflow, SQL. Happy to send my profile if you work with data engineering positions.' },
    { urgency: 'medium', title: 'Staffing & Global Consulting Firms', focus: 'GLOBAL_STAFFING + GLOBAL_CONSULTING buckets',
      targets: { 'Connects': '10–15', 'DMs': '5', 'Comments': '5' },
      detail: 'Target Hays, Michael Page, Robert Half, Manpower, Randstad, NTT DATA, Accenture, Capgemini — these companies place Data Engineers globally and often have LATAM contractor demand.',
      sp: SP.STAFFING, filters: F.STAFFING, noise: false,
      angle: 'Hi [Name], I\'m a Data Engineer specializing in cloud data platforms — Azure, AWS, Databricks, dbt, SQL and ETL/ELT. Currently open to LATAM/USD remote contractor opportunities.' },
  ];

  const week4 = [
    { urgency: 'critical', title: 'Follow-up with Accepted Connections', focus: 'Turn connections into conversations',
      targets: { 'DMs': '20–30', 'Comments': '10', 'Career Sites': '10' },
      detail: 'Send a brief follow-up to everyone who accepted in Weeks 1–3 but hasn\'t replied. Apply to open roles discovered through conversations. Submit to career site talent databases at target companies.',
      sp: null, filters: null, noise: false,
      angle: 'Hi [Name], thanks for connecting! I\'m actively looking for remote Data Engineering opportunities. My focus is Azure/AWS data pipelines, Databricks, dbt and SQL. If you\'re working with DE roles, I\'d love to stay in touch.' },
    { urgency: 'high', title: 'EU/Spain Expansion (Week 4 — slightly more)', focus: 'Begin building European optionality',
      targets: { 'Connects': '5–10', 'DMs': '2', 'Comments': '3' },
      detail: 'This week allow slightly more EU exploration now that the LATAM pipeline has momentum. Prioritize Spain, Portugal, Netherlands, Germany, Ireland. Still not the main channel.',
      sp: SP.SPAIN_EU, filters: F.SPAIN_EU, noise: false,
      angle: 'Hi [Name], I\'ll be spending time in Spain soon and I\'m building my European network. I\'m a Data Engineer focused on cloud data platforms, Azure/AWS, Databricks and analytics engineering.' },
    { urgency: 'medium', title: 'Pipeline Cleanup + Next Sprint Setup', focus: 'Compound your momentum',
      targets: { 'Mapping': '20+', 'Review': 'all replies', 'Next Sprint': 'planned' },
      detail: 'Map top 20 companies from unresolved_opportunity_buckets.csv. Review all conversation replies — categorize as Hot/Warm/Cold. Prepare Week 5–8 list with updated contacts from Lead Reactivation.',
      sp: null, filters: null, noise: false,
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
      // Prefer the backend search_pack (src/strategic_gap_search_builder.py —
      // real title+region terms). Q[mktKey] is a legacy fallback for older
      // cached JSON without a search_pack. NEVER fall back to quoting the
      // raw market label itself (e.g. '"Recruiter" "US_CANADA_NEARSHORE"') —
      // that string is not something LinkedIn indexes and returns nothing.
      const query  = (r.search_pack && r.search_pack.primary_query) || Q[mktKey] || ('data engineer ' + (r.persona||'').toLowerCase());
      const marketLabel = gapMarketLabel(r.market || '');
      return '<div class="plan-card ' + (r.urgency_level||'').toLowerCase() + '">'
        + '<div class="plan-card-header"><div>'
        + '<div class="plan-card-title">' + marketLabel + ' — ' + (r.persona||'') + '</div>'
        + '<div class="plan-card-meta">' + (r.timeframe||'') + '</div>'
        + '</div>' + urgencyBadge(r.urgency_level) + '</div>'
        + '<div class="plan-targets">'
        + '<div class="plan-t"><div class="plan-n">' + fmt(r.current_count) + '</div><div class="plan-l">have</div></div>'
        + '<div class="plan-t"><div class="plan-n">' + fmt(r.target_count) + '</div><div class="plan-l">target</div></div>'
        + '<div class="plan-t"><div class="plan-n" style="color:#ef4444">' + fmt(gap) + '</div><div class="plan-l">gap</div></div>'
        + '<div class="plan-t"><div class="plan-n" style="color:#14b8a6">' + weekly + '/wk</div><div class="plan-l">connects</div></div>'
        + '</div>'
        + '<div class="plan-reason">' + (r.strategic_reason||'').substring(0,140) + '</div>'
        + '<div style="margin-top:.5rem;display:flex;flex-wrap:wrap;align-items:center;gap:.4rem">'
        + '<a href="' + liUrl(query) + '" target="_blank" rel="noopener" '
        + 'style="font-size:.72rem;background:var(--accent);color:#fff;padding:2px 8px;border-radius:4px;text-decoration:none;flex-shrink:0">&#128269; Precision Search</a>'
        + '<button type="button" class="btn-ghost search-pack-btn" data-query="' + escapeAttr(query) + '" onclick="copyQueryBtn(this)">Copy Query</button>'
        + '<code class="search-query-code" style="color:var(--text-muted);flex:1 1 140px">' + query + '</code></div>'
        + '</div>';
    });
    const extra = (extraCards || []).map(makeWeekCard);
    grid.innerHTML = [...extra, ...dataCards].join('');
  }

  const plan60extra = [
    { urgency: 'high', title: '60-Day: Maintain USD Pipeline (80–85%)', focus: 'LATAM/USD + US-nearshore remains primary',
      targets: { 'Connects/wk': '30–40', 'DMs/wk': '10–15', 'Comments/wk': '10' },
      detail: 'Keep the LATAM/USD recruiter and hiring manager pipeline active. Reactivate dormant leads from Lead Reactivation. Add Spain/EU slowly only if USD conversations are already progressing.',
      sp: SP.LATAM_USD, filters: F.LATAM_USD, noise: true, angle: '' },
    { urgency: 'medium', title: '60-Day: Spain/EU Positioning (15–20%)', focus: 'Exploratory — not primary income channel',
      targets: { 'Connects/wk': '5–10', 'DMs/wk': '2–5', 'Comments/wk': '5' },
      detail: 'Build a small but real EU recruiter and hiring manager network. Focus on Spain (Madrid/Barcelona), Portugal (Lisbon), Netherlands, Germany, Ireland. Increase investment only after USD pipeline is stable.',
      sp: SP.SPAIN_EU, filters: F.SPAIN_EU, noise: false, angle: '' },
  ];

  const plan90extra = [
    { urgency: 'high', title: '90-Day: USD Remote Income — Still Priority', focus: 'Do not drop LATAM/USD pipeline',
      targets: { 'Active leads': '15–25', 'EU network': 'growing', 'Mapping': 'ongoing' },
      detail: 'By 90 days you should have active USD job conversations. Keep feeding the LATAM/USD recruiter pipeline. Europe becomes a positioning layer — not a replacement. You can increase EU connects to 20–30% if income is secured.',
      sp: SP.LATAM_USD, filters: F.LATAM_USD, noise: true, angle: '' },
    { urgency: 'medium', title: '90-Day: Europe as Positioning Layer', focus: 'Digital nomad optionality — Spain/Portugal base',
      targets: { 'EU Connects': '40–60 total', 'EU DMs': '15–20 total', 'EU HMs': '10–15' },
      detail: 'Europe becomes a positioning layer while USD remote work remains the income priority. Increase hiring manager relationships in Spain, Portugal, Netherlands. Map companies open to contractors and digital nomads.',
      sp: SP.DIGITAL_NOMAD, filters: F.SPAIN_EU, noise: false, angle: '' },
    { urgency: 'medium', title: '90-Day: Company Mapping Backlog', focus: 'Improve opportunity bucket coverage',
      targets: { 'Companies mapped': '50+', 'V5 coverage': '>80%', 'Mapping sessions': '4' },
      detail: 'Map at least 50 more companies from unresolved_opportunity_buckets.csv. Each company resolved improves the entire dashboard accuracy and reveals hidden opportunities.',
      sp: null, filters: null, noise: false, angle: 'Run: python src/build_strategy_layer.py → check Data Quality page for updated bucket coverage.' },
  ];

  makePlanGrid(D.action_plan_60 || [], 'plan-60-grid', plan60extra);
  makePlanGrid(D.action_plan_90 || [], 'plan-90-grid', plan90extra);
}

// ── PAGE 5: Top Contacts ──────────────────────────────────────────────────────
let contactSortMode = 'outreach'; // 'outreach' | 'relvalue' | 'base' | 'untapped' | 'activation'

// Preferred display order for Outreach Status — anything not listed here
// (e.g. a future new status) is appended alphabetically at the end.
const OUTREACH_STATUS_ORDER = [
  'Needs Reply', 'Replied', 'Interview Pipeline', 'CV / Follow-up', 'Warm Lead',
  'Follow-up Due', 'Pending Reply', 'Dormant', 'Ghosted', 'Auto-reply',
  'Rejected', 'No Contact', 'No History',
];

function renderContacts() {
  const contacts = D.top_contacts || [];
  const personas = [...new Set(contacts.map(c => c.persona||''))].sort();
  const markets  = [...new Set(contacts.map(c => c.opportunity_market_v5 || c.market_v2 || c.strategic_market || ''))].sort();
  const pf = document.getElementById('ct-persona-filter');
  const mf = document.getElementById('ct-market-filter');
  if (pf && pf.options.length <= 1) {
    personas.forEach(p => { const o = document.createElement('option'); o.value = p; o.textContent = p; pf.appendChild(o); });
  }
  if (mf && mf.options.length <= 1) {
    markets.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; mf.appendChild(o); });
  }

  // Part 17 — Outreach Status filter, populated from ACTUAL outreach_status
  // values present in the data (not a hardcoded/duplicate filter).
  const of = document.getElementById('ct-outreach-filter');
  if (of && of.options.length <= 1) {
    const present = new Set(
      contacts.map(c => c.outreach_status || (c.has_message_history ? 'Replied' : 'No History')).filter(Boolean)
    );
    const ordered = OUTREACH_STATUS_ORDER.filter(s => present.has(s));
    const extra = [...present].filter(s => !OUTREACH_STATUS_ORDER.includes(s)).sort();
    [...ordered, ...extra].forEach(s => {
      const o = document.createElement('option'); o.value = s; o.textContent = s; of.appendChild(o);
    });
  }

  // V8 (Part 14) — Process State / Reply Obligation filters, populated from
  // actual values present in the data.
  const psf = document.getElementById('ct-process-state-filter');
  if (psf && psf.options.length <= 1) {
    [...new Set(contacts.map(c => c.process_state).filter(Boolean))].sort().forEach(s => {
      const o = document.createElement('option'); o.value = s; o.textContent = s.replace(/_/g, ' '); psf.appendChild(o);
    });
  }
  const robf = document.getElementById('ct-reply-obligation-filter');
  if (robf && robf.options.length <= 1) {
    ['CONFIRMED', 'LIKELY', 'AMBIGUOUS', 'NONE'].forEach(s => {
      if (contacts.some(c => c.reply_obligation === s)) {
        const o = document.createElement('option'); o.value = s; o.textContent = s; robf.appendChild(o);
      }
    });
  }

  filteredContacts = [...contacts];
  _sortContacts();
  renderContactsTable();
}

function _sortContacts() {
  if (contactSortMode === 'relvalue') {
    filteredContacts.sort((a, b) => (parseFloat(b.relationship_value_score) || 0) - (parseFloat(a.relationship_value_score) || 0));
  } else if (contactSortMode === 'base') {
    filteredContacts.sort((a, b) => (parseFloat(b.priority_score) || 0) - (parseFloat(a.priority_score) || 0));
  } else if (contactSortMode === 'untapped') {
    filteredContacts.sort((a, b) => (parseFloat(b.untapped_outreach_score) || 0) - (parseFloat(a.untapped_outreach_score) || 0));
  } else if (contactSortMode === 'activation') {
    filteredContacts.sort((a, b) => (parseFloat(b.untapped_activation_potential_score) || 0) - (parseFloat(a.untapped_activation_potential_score) || 0));
  } else {
    // Default (Part 14): Immediate Action descending, then Relationship Value
    // descending — this is what prevents a recently-rejected high-value
    // recruiter from outranking someone with a genuinely unresolved request.
    filteredContacts.sort((a, b) => {
      const aAction = parseFloat(a.immediate_action_score ?? a.outreach_adjusted_score ?? a.priority_score) || 0;
      const bAction = parseFloat(b.immediate_action_score ?? b.outreach_adjusted_score ?? b.priority_score) || 0;
      if (bAction !== aAction) return bAction - aAction;
      return (parseFloat(b.relationship_value_score) || 0) - (parseFloat(a.relationship_value_score) || 0);
    });
  }
}

window.setContactSort = function(mode) {
  contactSortMode = mode;
  const b1 = document.getElementById('ct-sort-outreach');
  const b2 = document.getElementById('ct-sort-relvalue');
  const b3 = document.getElementById('ct-sort-base');
  const b4 = document.getElementById('ct-sort-untapped');
  const b5 = document.getElementById('ct-sort-activation');
  if (b1) b1.classList.toggle('active', mode === 'outreach');
  if (b2) b2.classList.toggle('active', mode === 'relvalue');
  if (b3) b3.classList.toggle('active', mode === 'base');
  if (b4) b4.classList.toggle('active', mode === 'untapped');
  if (b5) b5.classList.toggle('active', mode === 'activation');
  _sortContacts();
  contactsPage = 1;
  renderContactsTable();
};

// History Status (Untapped Outreach Scoring V9) — a simplified, fixed
// taxonomy layered on top of the raw contact_history_status / outreach_status
// fields, so users don't need to know the underlying enum values.
function _historyStatusOf(c) {
  const hist = c.contact_history_status || '';
  if (hist === 'NEVER_CONTACTED_CONFIRMED' || hist === 'LIKELY_NEVER_CONTACTED') return 'never_contacted';
  const outS = c.outreach_status || (c.has_message_history ? 'Replied' : 'No History');
  if (outS === 'No History') return 'never_contacted';
  if (outS === 'Needs Reply' || outS === 'Pending Reply') return 'needs_reply';
  if (outS === 'Warm Lead') return 'warm_lead';
  if (outS === 'Dormant') return 'dormant';
  if (outS === 'Rejected') return 'rejected_closed';
  if (outS === 'Ghosted' || outS === 'No Contact') return 'no_response';
  return '';
}

window.applyContactFilters = function() {
  const minS   = parseFloat(document.getElementById('ct-min-score')?.value) || 0;
  const per    = document.getElementById('ct-persona-filter')?.value || '';
  const mkt    = document.getElementById('ct-market-filter')?.value  || '';
  const band   = document.getElementById('ct-band-filter')?.value    || '';
  const outS   = document.getElementById('ct-outreach-filter')?.value || '';
  const histS  = document.getElementById('ct-history-filter')?.value || '';
  const untapCat = document.getElementById('ct-untapped-category-filter')?.value || '';
  const procS  = document.getElementById('ct-process-state-filter')?.value || '';
  const replyO = document.getElementById('ct-reply-obligation-filter')?.value || '';
  const relBand = document.getElementById('ct-relvalue-band-filter')?.value || '';
  const actBand = document.getElementById('ct-actionband-filter')?.value || '';
  filteredContacts = (D.top_contacts || []).filter(c => {
    const s  = parseFloat(c.priority_score) || 0;
    const m  = c.opportunity_market_v5 || c.market_v2 || c.strategic_market || '';
    const relV = parseFloat(c.relationship_value_score) || 0;
    const actV = parseFloat(c.immediate_action_score) || 0;
    if (s < minS) return false;
    if (per && c.persona !== per) return false;
    if (mkt && m !== mkt) return false;
    if (band === 'high'   && s < 70)           return false;
    if (band === 'medium' && (s < 40 || s >= 70)) return false;
    if (band === 'low'    && s >= 40)          return false;
    if (outS) {
      const status = c.outreach_status || (c.has_message_history ? 'Replied' : 'No History');
      if (status !== outS) return false;
    }
    if (histS && _historyStatusOf(c) !== histS) return false;
    if (untapCat && c.untapped_category !== untapCat) return false;
    if (procS && c.process_state !== procS) return false;
    if (replyO && c.reply_obligation !== replyO) return false;
    if (relBand === 'high'   && relV < 70)            return false;
    if (relBand === 'medium' && (relV < 40 || relV >= 70)) return false;
    if (relBand === 'low'    && relV >= 40)           return false;
    if (actBand === 'high'   && actV < 60)            return false;
    if (actBand === 'medium' && (actV < 30 || actV >= 60)) return false;
    if (actBand === 'low'    && actV >= 30)           return false;
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
  const hf = document.getElementById('ct-history-filter'); if (hf) hf.value = '';
  const ucf = document.getElementById('ct-untapped-category-filter'); if (ucf) ucf.value = '';
  const psf = document.getElementById('ct-process-state-filter'); if (psf) psf.value = '';
  const robf = document.getElementById('ct-reply-obligation-filter'); if (robf) robf.value = '';
  const rbf = document.getElementById('ct-relvalue-band-filter'); if (rbf) rbf.value = '';
  const abf = document.getElementById('ct-actionband-filter'); if (abf) abf.value = '';
  filteredContacts = [...(D.top_contacts || [])];
  _sortContacts();
  contactsPage = 1;
  renderContactsTable();
};

// Cross-page routing entry point (Part 1) — applyContactFilters() only reads
// the page's own <select> elements and can't express an OR-across-scores
// filter or an arbitrary opportunity-bucket set, so Executive Overview cards
// that need those (Actionable Contacts, Global Opportunities) call this
// instead. Does not touch the filter <select>s — the active-filter banner
// (setRoute) communicates what's shown instead.
// spec: { minScoreField, minScore, minScoreAnyFields, minScoreAny, opportunityBuckets }
window.applyExternalContactFilter = function(spec) {
  spec = spec || {};
  const source = D.top_contacts || [];
  filteredContacts = source.filter(c => {
    if (spec.opportunityBuckets && !spec.opportunityBuckets.includes(c.opportunity_bucket)) return false;
    if (spec.minScoreField) {
      if ((parseFloat(c[spec.minScoreField]) || 0) < (spec.minScore || 0)) return false;
    }
    if (spec.minScoreAnyFields) {
      const best = Math.max(...spec.minScoreAnyFields.map(f => parseFloat(c[f]) || 0));
      if (best < (spec.minScoreAny || 0)) return false;
    }
    return true;
  });
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
    const mkt      = c.opportunity_market_v5 || c.market_v2 || c.strategic_market || 'UNKNOWN';
    const url      = c.url || '';
    const daysAgo  = c.days_since_last_message != null ? c.days_since_last_message + 'd' : '—';
    const relV     = c.relationship_value_score != null ? parseFloat(c.relationship_value_score) : null;
    const actV     = c.immediate_action_score != null ? parseFloat(c.immediate_action_score) : null;
    const relClass = relV == null ? '' : relV >= 70 ? 'score-high' : relV >= 40 ? 'score-med' : 'score-low';
    const actClass  = actV == null ? '' : actV >= 60 ? 'score-high' : actV >= 30 ? 'score-med' : 'score-low';
    const processState = c.process_state ? c.process_state.replace(/_/g, ' ') : '—';
    const untapS = c.untapped_outreach_score != null ? parseFloat(c.untapped_outreach_score) : null;
    const untapClass = untapS == null ? '' : untapS >= 70 ? 'score-high' : untapS >= 40 ? 'score-med' : 'score-low';
    return '<tr>'
      + '<td style="white-space:nowrap">' + (c.full_name||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (c.company_clean||'—') + '</td>'
      + '<td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (c.position_clean||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (c.persona||'—') + '</td>'
      + '<td>' + marketBadge(mkt) + '</td>'
      + '<td title="Relationship Value Score">' + (relV != null ? '<span class="score-badge ' + relClass + '">' + relV.toFixed(0) + '</span>' : '—') + '</td>'
      + '<td title="Immediate Action Score">' + (actV != null ? '<span class="score-badge ' + actClass + '">' + actV.toFixed(0) + '</span>' : '—') + '</td>'
      + '<td style="font-size:0.7rem;white-space:nowrap">' + processState + '</td>'
      + '<td style="font-size:0.7rem;white-space:nowrap">' + (c.reply_obligation||'—') + '</td>'
      + '<td>' + outreachBadge(c.outreach_status || (c.has_message_history ? 'Replied' : 'No History')) + '</td>'
      + '<td style="font-size:0.7rem;color:var(--text-muted)">' + daysAgo + '</td>'
      + '<td style="font-size:0.7rem;white-space:nowrap">' + (c.next_action_date||'—') + '</td>'
      + '<td style="white-space:normal;font-size:0.7rem;max-width:180px">' + ((c.outreach_reason || c.why_priority||'').substring(0,80)) + '</td>'
      + '<td title="Untapped Outreach Score">' + (untapS != null ? '<span class="score-badge ' + untapClass + '">' + untapS.toFixed(0) + '</span>' : '—') + '</td>'
      + '<td style="white-space:normal;font-size:0.7rem;max-width:200px">' + (c.untapped_reason || '—') + '</td>'
      + '<td style="white-space:normal;font-size:0.7rem;max-width:200px">' + (c.first_message_angle || '—') + '</td>'
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
const RESOLUTION_METHOD_LABEL = {
  manual_override:                   'Manual override (YAML)',
  exact_dictionary:                  'Exact company dictionary',
  v4_inference:                      'V4 keyword inference',
  title_or_company_keyword:          'Title/company region keyword',
  company_category:                  'Company category',
  language_signal:                   'Language signal (PT/ES)',
  persona_fallback:                  'High-value persona fallback',
  same_company_propagation:          'Same-company propagation (V6)',
  message_signal_evidence:           'Message-history signal (V6, local)',
  persona_company_category_fallback: 'Persona/company-category fallback (V6)',
  unresolved:                        'Still unresolved (honest residual)',
  no_usable_signal:                  'Low value — no usable signal',
};

// ── V5 Opportunity Market segment drill-down (Part 5) ────────────────────────
// Backed by D.opportunity_market_people_segments (full population, built by
// build_opportunity_market_people_segments in export_public_dashboard_data.py
// using the SAME bucket groupings as opportunity_market_v5.build_v5_summary,
// so every card's count matches this array exactly).
let filteredV5Segment = [];
let selectedV5SegmentCompany = null;

const V5_SEGMENT_KPI_FILTERS = {
  actionable:           { label: 'Actionable Connections',       match: s => s.is_actionable === true || s.is_actionable === 'True' },
  confirmed_geographic: { label: 'Confirmed Geographic Signals', match: s => s.market_segment === 'confirmed_geographic' },
  global_buckets:       { label: 'Global Company Buckets',       match: s => s.market_segment === 'global_buckets' },
  language_signal:      { label: 'Language Signal (PT/ES)',      match: s => s.market_segment === 'language_signal' },
  global_opportunity:   { label: 'Global Opportunity',           match: s => s.market_segment === 'global_opportunity' },
  needs_mapping:        { label: 'Needs Company Mapping',        match: s => s.market_segment === 'needs_mapping' },
  low_value:            { label: 'Low Value Unresolved',         match: s => s.market_segment === 'low_value' },
};

function _renderV5SegmentCompanies() {
  const tbody = document.getElementById('v5-segment-companies-tbody');
  if (!tbody) return;
  const byCompany = {};
  filteredV5Segment.forEach(s => {
    const c = s.company_clean || '(no company)';
    byCompany[c] = (byCompany[c] || 0) + 1;
  });
  const rows = Object.entries(byCompany).sort((a, b) => b[1] - a[1]).slice(0, 100);
  tbody.innerHTML = rows.length ? rows.map(([company, count]) => {
    const cAttr = escapeAttr(company);
    const isSelected = selectedV5SegmentCompany === company;
    return '<tr class="' + (isSelected ? 'active' : '') + '" style="cursor:pointer" tabindex="0" role="button" '
      + 'onclick="selectV5SegmentCompany(\'' + cAttr + '\')" onkeydown="if(event.key===\'Enter\'){selectV5SegmentCompany(\'' + cAttr + '\')}">'
      + '<td>' + company + '</td><td><strong>' + count + '</strong></td></tr>';
  }).join('') : '<tr><td colspan="2" style="text-align:center;color:var(--text-muted)">No companies in this segment.</td></tr>';
}

function _renderV5SegmentPeople() {
  const tbody = document.getElementById('v5-segment-tbody');
  if (!tbody) return;
  const rows = selectedV5SegmentCompany
    ? filteredV5Segment.filter(s => s.company_clean === selectedV5SegmentCompany)
    : filteredV5Segment.slice(0, 200);
  tbody.innerHTML = rows.length ? rows.map(p => '<tr>'
    + '<td style="white-space:nowrap">' + (p.full_name||'—') + '</td>'
    + '<td style="white-space:nowrap">' + (p.company_clean||'—') + '</td>'
    + '<td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (p.position_clean||'—') + '</td>'
    + '<td style="white-space:nowrap">' + (p.persona||'—') + '</td>'
    + '<td>' + marketBadge(p.opportunity_bucket||'—') + '</td>'
    + '<td>' + fmt(p.priority_score||0) + '</td>'
    + '<td>' + (p.url ? '<a href="' + p.url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
    + '</tr>').join('') : '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">No contacts match this selection.</td></tr>';
  const nCompanies = new Set(filteredV5Segment.map(p => p.company_clean)).size;
  const statsEl = document.getElementById('v5-segment-stats');
  if (statsEl) {
    statsEl.textContent = selectedV5SegmentCompany
      ? 'Showing ' + rows.length + ' contacts from ' + selectedV5SegmentCompany + ' — selected company'
      : 'Showing ' + filteredV5Segment.length + ' contacts across ' + nCompanies + ' companies — selected segment';
  }
}

window.applyV5SegmentFilter = function(key) {
  const def = V5_SEGMENT_KPI_FILTERS[key];
  if (!def) return;
  const source = D.opportunity_market_people_segments || [];
  filteredV5Segment = source.filter(def.match);
  selectedV5SegmentCompany = null;
  document.querySelectorAll('#page-unknown .kpi-card[data-kpi]').forEach(el => {
    if (Object.prototype.hasOwnProperty.call(V5_SEGMENT_KPI_FILTERS, el.dataset.kpi)) {
      const isActive = el.dataset.kpi === key;
      el.classList.toggle('active', isActive);
      el.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    }
  });
  _renderV5SegmentCompanies();
  _renderV5SegmentPeople();
  const panel = document.getElementById('v5-segment-drilldown');
  if (panel) { panel.style.display = ''; panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
};

window.selectV5SegmentCompany = function(company) {
  selectedV5SegmentCompany = company;
  _renderV5SegmentCompanies();
  _renderV5SegmentPeople();
  const table = document.getElementById('v5-segment-table');
  if (table) table.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

function renderCompanyResolutionV6() {
  const v6 = D.company_resolution_v6 || {};
  const el = document.getElementById('v6-resolution-summary');
  if (el && v6.total_connections) {
    el.innerHTML = [
      makeKpiCard('needs_mapping_before', 'Needs Mapping — Before V6', v6.needs_mapping_before || 0, v6.needs_mapping_pct_before + '% of network — click to view current backlog', '', 'applyUnknownKpiFilter'),
      makeKpiCard('needs_mapping_after',  'Needs Mapping — After V6',  v6.needs_mapping_after  || 0, v6.needs_mapping_pct_after + '% of network — click to filter', v6.target_met ? 'good' : 'warn', 'applyUnknownKpiFilter'),
      makeKpiCard('reduced_by',           'Reduced By',                v6.needs_mapping_reduction_count || 0, v6.needs_mapping_reduction_pct + '% reduction — click to view current backlog', 'good', 'applyUnknownKpiFilter'),
      makeKpiCard('same_company_propagation', 'Same-Company Propagation', v6.resolved_by_same_company_propagation || 0, '>=2 contacts, >=70% share — click to view current backlog', '', 'applyUnknownKpiFilter'),
      makeKpiCard('message_signal_evidence',  'Message-Signal Evidence',  v6.resolved_by_message_signal_evidence  || 0, 'local messages.csv only — click to view current backlog', '', 'applyUnknownKpiFilter'),
      makeKpiCard('persona_fallback',         'Persona/Category Fallback',v6.resolved_by_persona_company_category || 0, 'staffing/consulting/tech/strategic — click to view current backlog', '', 'applyUnknownKpiFilter'),
    ].join('');
  }
  const noteEl = document.getElementById('v6-target-note');
  if (noteEl) {
    if (v6.target_note) {
      noteEl.innerHTML = '<span class="alert-icon">' + (v6.target_met ? '&#9989;' : '&#8505;&#65039;') + '</span><span>' + v6.target_note
        + ' <em style="opacity:.75">These evidence types describe HOW past contacts were resolved — they already left the '
        + 'current backlog, so there is no live per-contact list for them. Clicking shows the current actionable backlog below instead.</em></span>';
      noteEl.className = 'alert ' + (v6.target_met ? 'alert-good' : 'alert-info');
    } else {
      noteEl.innerHTML = '';
    }
  }
}

// ── Needs Mapping drill-down (consolidated UX + analytics patch, Parts 2-4) ──
// Every KPI card on this page maps to a REAL backing list: company-level in
// unknownCompaniesBase/filteredUnknownCompanies, person-level in
// mappingPeopleBase/filteredMappingPeople. Cards describing evidence types
// whose contacts already left the current backlog (they were RESOLVED by
// that mechanism) honestly show the current actionable backlog instead of
// fabricating a per-row breakdown that doesn't exist — labeled clearly.
let unknownCompaniesBase = [];
let filteredUnknownCompanies = [];
let mappingPeopleBase = [];
let filteredMappingPeople = [];
let activeUnknownKpi = null;
let selectedMappingCompany = null;
let mappingPersonPage = 1;
const MAPPING_PERSON_PAGE_SIZE = 25;

const MAPPING_RECRUITER_PERSONAS    = new Set(['Recruiter', 'Sourcer']);
const MAPPING_HIRING_PERSONAS       = new Set(['Hiring Manager', 'Engineering Manager']);
const MAPPING_DATA_LEADER_PERSONAS  = new Set(['Data Engineering Manager', 'Head of Data', 'Director', 'Executive']);
const MAPPING_HIGH_VALUE_PERSONAS   = new Set([
  ...MAPPING_RECRUITER_PERSONAS, 'Talent Acquisition', ...MAPPING_HIRING_PERSONAS, ...MAPPING_DATA_LEADER_PERSONAS,
]);

function _isAutoResolvablePerson(p) { return (p.resolution_source || '').indexOf('auto-resolvable') === 0; }

const MAPPING_KPI_FILTERS = {
  needs_mapping_total: { label: 'All Needs Mapping',                match: () => true },
  high_value:          { label: 'High-Value Needs Mapping',         match: p => MAPPING_HIGH_VALUE_PERSONAS.has(p.persona) },
  recruiters:          { label: 'Recruiters Needing Mapping',       match: p => MAPPING_RECRUITER_PERSONAS.has(p.persona) },
  talent_acquisition:  { label: 'Talent Acquisition Needing Mapping', match: p => p.persona === 'Talent Acquisition' },
  hiring_mgrs:         { label: 'Hiring Managers Needing Mapping',  match: p => MAPPING_HIRING_PERSONAS.has(p.persona) },
  data_leaders:        { label: 'Data Leaders Needing Mapping',     match: p => MAPPING_DATA_LEADER_PERSONAS.has(p.persona) },
  auto_resolvable:     { label: 'Auto-Resolvable',                  match: p => _isAutoResolvablePerson(p) },
  top25:               { label: 'Top 25 Companies Impact',          match: () => true, top25: true },
  same_company_propagation: { label: 'Same-Company Propagation — historical; showing current backlog', match: () => true },
  message_signal_evidence:  { label: 'Message-Signal Evidence — historical; showing current backlog',  match: () => true },
  persona_fallback:         { label: 'Persona/Category Fallback — historical; showing current backlog', match: () => true },
  reduced_by:                { label: 'Reduced By — historical reduction; showing current backlog',     match: () => true },
  needs_mapping_before:      { label: 'Needs Mapping — Before V6 (historical snapshot; showing current backlog)', match: () => true },
  needs_mapping_after:       { label: 'Needs Mapping — After V6 (current backlog)', match: () => true },
  companies_3plus:           { label: 'Companies with 3+ Unresolved', match: () => true, filterCompanies: c => (c.contacts || 0) >= 3 },
};

function _updateActiveUnknownKpiCards() {
  document.querySelectorAll('#page-unknown .kpi-card').forEach(el => {
    const isActive = el.dataset.kpi === activeUnknownKpi;
    el.classList.toggle('active', isActive);
    el.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
}

window.applyUnknownKpiFilter = function(key) {
  const def = MAPPING_KPI_FILTERS[key];
  if (!def) return;
  activeUnknownKpi = key;
  selectedMappingCompany = null;

  filteredUnknownCompanies = def.top25 ? unknownCompaniesBase.slice(0, 25) : unknownCompaniesBase.slice();
  if (def.filterCompanies) filteredUnknownCompanies = filteredUnknownCompanies.filter(def.filterCompanies);
  const companySet = (def.top25 || def.filterCompanies) ? new Set(filteredUnknownCompanies.map(c => c.company_clean)) : null;
  filteredMappingPeople = mappingPeopleBase.filter(p => def.match(p) && (!companySet || companySet.has(p.company_clean)));

  mappingPersonPage = 1;
  _updateActiveUnknownKpiCards();
  renderUnknownCompaniesTable(def.label);
  renderMappingPersonTable(def.label);
  const table = document.getElementById('unk-companies-table');
  if (table) table.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

window.selectMappingCompany = function(company) {
  selectedMappingCompany = company;
  activeUnknownKpi = null;
  _updateActiveUnknownKpiCards();
  filteredMappingPeople = mappingPeopleBase.filter(p => p.company_clean === company);
  mappingPersonPage = 1;
  renderMappingPersonTable(null);
  const table = document.getElementById('mapping-person-table');
  if (table) table.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

window.resetMappingDrilldown = function() {
  activeUnknownKpi = null;
  selectedMappingCompany = null;
  filteredUnknownCompanies = unknownCompaniesBase.slice();
  filteredMappingPeople = mappingPeopleBase.slice();
  mappingPersonPage = 1;
  _updateActiveUnknownKpiCards();
  renderUnknownCompaniesTable(null);
  renderMappingPersonTable(null);
};

function _mappingDrilldownStats(label) {
  const el = document.getElementById('mapping-drilldown-stats');
  if (!el) return;
  if (selectedMappingCompany) {
    el.textContent = 'Showing ' + filteredMappingPeople.length + ' contacts from ' + selectedMappingCompany + ' — selected company';
  } else {
    const nCompanies = new Set(filteredMappingPeople.map(p => p.company_clean)).size;
    el.textContent = 'Showing ' + filteredMappingPeople.length + ' contacts across ' + nCompanies + ' companies'
      + (label ? ' — ' + label : '');
  }
}

function renderUnknownCompaniesTable(label) {
  const tbody = document.getElementById('unk-companies-tbody');
  const rows = filteredUnknownCompanies;
  _mappingDrilldownStats(label);
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;color:var(--text-muted)">No companies match this filter.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r, i) => {
    const badgeVal = r.suggested_opportunity_bucket || 'NEEDS_COMPANY_MAPPING';
    const isSelected = selectedMappingCompany === r.company_clean;
    return '<tr class="' + (isSelected ? 'active' : '') + '" style="cursor:pointer" '
      + 'onclick="selectMappingCompany(' + JSON.stringify(r.company_clean) + ')" tabindex="0" role="button" '
      + 'onkeydown="if(event.key===\'Enter\'){selectMappingCompany(' + JSON.stringify(r.company_clean) + ')}">'
    + '<td><strong>#' + (i+1) + '</strong></td>'
    + '<td style="font-weight:500">' + (r.company_clean||'') + '</td>'
    + '<td><strong>' + fmt(r.contacts) + '</strong></td>'
    + '<td>' + fmt(r.recruiters||0) + '</td>'
    + '<td>' + fmt(r.talent_acquisition||0) + '</td>'
    + '<td>' + fmt(r.hiring_managers||0) + '</td>'
    + '<td>' + fmt(r.data_leaders||0) + '</td>'
    + '<td>' + fmt(r.high_value_contacts||0) + '</td>'
    + '<td>' + fmt(r.auto_resolvable_count||0) + '</td>'
    + '<td>' + fmt(r.mapping_impact_score||0) + '</td>'
    + '<td>' + marketBadge(badgeVal) + '</td>'
    + '</tr>';
  }).join('');
}

window.renderMappingPersonTable = function(label) {
  _mappingDrilldownStats(label);
  const sortMode = document.getElementById('mapping-person-sort')?.value || 'score';
  const rows = filteredMappingPeople.slice();
  if (sortMode === 'persona')      rows.sort((a, b) => (a.persona || '').localeCompare(b.persona || ''));
  else if (sortMode === 'bucket')  rows.sort((a, b) => (a.suggested_opportunity_bucket || '').localeCompare(b.suggested_opportunity_bucket || ''));
  else                              rows.sort((a, b) => (parseFloat(b.mapping_priority_score) || 0) - (parseFloat(a.mapping_priority_score) || 0));

  const start = (mappingPersonPage - 1) * MAPPING_PERSON_PAGE_SIZE;
  const slice = rows.slice(start, start + MAPPING_PERSON_PAGE_SIZE);
  const tbody = document.getElementById('mapping-person-tbody');
  if (!tbody) return;
  if (!slice.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-muted)">No contacts match the current filter.</td></tr>';
  } else {
    tbody.innerHTML = slice.map(p => {
      const url = p.profile_url || '';
      const score = parseInt(p.mapping_priority_score) || 0;
      const sCls = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
      return '<tr>'
        + '<td style="white-space:nowrap">' + (p.full_name||'—') + '</td>'
        + '<td style="white-space:nowrap">' + (p.company_clean||'—') + '</td>'
        + '<td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (p.position_clean||'—') + '</td>'
        + '<td style="white-space:nowrap">' + (p.persona||'—') + '</td>'
        + '<td>' + marketBadge(p.suggested_opportunity_bucket||'UNKNOWN') + '</td>'
        + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
        + '<td style="font-size:0.72rem;max-width:200px">' + String(p.resolution_source||'').substring(0,90) + '</td>'
        + '<td style="font-size:0.72rem;max-width:200px">' + String(p.mapping_reason_short||'').substring(0,90) + '</td>'
        + '<td>' + (url ? '<a href="' + url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
        + '<td><button class="btn-ghost" style="font-size:0.7rem;padding:2px 8px" onclick="goToNavPage(\'contacts\')">Open in Top Contacts</button></td>'
        + '</tr>';
    }).join('');
  }
  renderMappingPersonPagination(rows.length);
};

function renderMappingPersonPagination(total) {
  const pages = Math.ceil(total / MAPPING_PERSON_PAGE_SIZE);
  const pg = document.getElementById('mapping-person-pagination');
  if (!pg) return;
  let html = '';
  for (let i = 1; i <= Math.min(pages, 8); i++) {
    html += '<button class="pg-btn' + (i === mappingPersonPage ? ' active' : '') + '" onclick="goMappingPersonPage(' + i + ')">' + i + '</button>';
  }
  if (pages > 8) html += '<span style="color:var(--text-muted);font-size:0.8rem"> … ' + pages + ' pages</span>';
  pg.innerHTML = html;
}
window.goMappingPersonPage = function(n) { mappingPersonPage = n; renderMappingPersonTable(); };

function renderNeedsMappingActionPlan() {
  const plan = D.needs_mapping_action_plan || {};
  const summaryEl = document.getElementById('mapping-plan-summary');
  if (summaryEl) {
    if (!plan.available) { summaryEl.innerHTML = ''; }
    else {
      const s = plan.executive_summary || {};
      summaryEl.innerHTML = [
        makeCard('Backlog Size',              s.backlog_size || 0, 'contacts still needing company mapping', 'warn'),
        makeCard('High-Value Unresolved',     s.high_value_unresolved || 0, 'recruiters/hiring/data-leader personas'),
        makeCard('Recruiters Unresolved',     s.recruiters_unresolved || 0),
        makeCard('Biggest-Impact Companies',  s.biggest_impact_companies || 0, 'top-10 by mapping impact score', 'good'),
        makeCard('Auto-Resolvable Share',     (s.auto_resolvable_share_pct || 0) + '%', 'quick override, low review effort', 'good'),
        makeCard('Est. Weekly Reduction Potential', s.estimated_weekly_reduction_potential || 0, 'if this week\'s queue is worked'),
      ].join('');
    }
  }
  const recEl = document.getElementById('mapping-plan-recommendation');
  if (recEl) {
    recEl.innerHTML = plan.available && plan.recommendation
      ? '<span class="alert-icon">&#128161;</span><span>' + plan.recommendation + '</span>'
      : '';
  }
  const actionsEl = document.getElementById('mapping-plan-actions');
  if (actionsEl) {
    actionsEl.innerHTML = (plan.available ? (plan.next_actions || []) : [])
      .map(a => '<li>' + a + '</li>').join('');
  }
  const queueTbody = document.getElementById('mapping-plan-queue-tbody');
  if (queueTbody) {
    const q = plan.available ? (plan.weekly_queue || []) : [];
    queueTbody.innerHTML = q.length
      ? q.map(r => '<tr>'
          + '<td><strong>P' + (r.priority ?? '—') + '</strong></td>'
          + '<td style="font-size:0.8rem">' + (r.segment||'—') + '</td>'
          + '<td style="white-space:nowrap">' + (r.target||'—') + '</td>'
          + '<td style="white-space:nowrap">' + (r.company||'—') + '</td>'
          + '<td style="font-size:0.78rem">' + (r.action||'—') + '</td>'
          + '</tr>').join('')
      : '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No action plan data yet — run the pipeline with a Needs Mapping backlog present.</td></tr>';
  }
}

function renderUnknownResolution() {
  const v5Sum = D.opportunity_market_v5_summary || {};
  renderCompanyResolutionV6();

  // Primary V5 summary cards — all clickable (Part 5), backed by the
  // full-population D.opportunity_market_people_segments array so every
  // card's count exactly matches what its click reveals.
  const v5TopEl = document.getElementById('v5-resolution-summary');
  if (v5TopEl && v5Sum.total_connections) {
    const needsMapping = v5Sum.v5_needs_company_mapping || 0;
    const lowValue     = v5Sum.v5_low_value_unresolved || 0;
    const actionable   = v5Sum.v5_actionable_total || 0;
    v5TopEl.innerHTML = [
      makeKpiCard('actionable',          'Actionable Connections',       actionable,                        v5Sum.v5_actionable_pct + '% classified — click to filter', 'good', 'applyV5SegmentFilter'),
      makeKpiCard('confirmed_geographic','Confirmed Geographic Signals', v5Sum.v5_confirmed_geographic||0,  'Brazil · LATAM · US · EU · Spain — click to filter', 'good', 'applyV5SegmentFilter'),
      makeKpiCard('global_buckets',      'Global Company Buckets',       v5Sum.v5_global_buckets||0,        'Staffing · Consulting · Tech — click to filter', '', 'applyV5SegmentFilter'),
      makeKpiCard('language_signal',     'Language Signal (PT/ES)',      v5Sum.v5_language_inferred||0,     'inferred from title keywords — click to filter', '', 'applyV5SegmentFilter'),
      makeKpiCard('global_opportunity',  'Global Opportunity',           v5Sum.v5_global_opportunity||0,    'valuable persona, region unresolved — click to filter', '', 'applyV5SegmentFilter'),
      makeKpiCard('needs_mapping',       'Needs Company Mapping',        needsMapping,                      'action backlog — click to filter', 'warn', 'applyV5SegmentFilter'),
      makeKpiCard('low_value',           'Low Value Unresolved',         lowValue,                          v5Sum.v5_low_value_pct + '% — click to filter', '', 'applyV5SegmentFilter'),
    ].join('');
  }

  // Needs-mapping contacts — sourced from the current, post-V6/V7 residual
  // backlog (D.needs_mapping_backlog), NOT the older/looser market_v2==UNKNOWN
  // population. Every card here is clickable and filters both the company
  // table and the person drill-down table below (Parts 2-4).
  const backlog = D.needs_mapping_backlog || {};
  const bs = backlog.summary || {};
  mappingPeopleBase = backlog.people || [];
  unknownCompaniesBase = backlog.companies || [];

  const unkMetEl = document.getElementById('unk-metrics');
  if (unkMetEl) unkMetEl.innerHTML = [
    makeKpiCard('needs_mapping_total', 'Needs Mapping Total',           bs.backlog_size||0, 'company known, market unresolved — click to filter', '', 'applyUnknownKpiFilter'),
    makeKpiCard('high_value',          'High-Value Needs Mapping',      bs.high_value_unresolved||0, 'recruiter/hiring/data-leader personas — click to filter', 'warn', 'applyUnknownKpiFilter'),
    makeKpiCard('recruiters',          'Recruiters Needing Mapping',    bs.recruiters_unresolved||0, 'click to filter', '', 'applyUnknownKpiFilter'),
    makeKpiCard('hiring_mgrs',         'Hiring Mgrs Needing Mapping',   bs.hiring_managers_unresolved||0, 'click to filter', '', 'applyUnknownKpiFilter'),
    makeKpiCard('data_leaders',        'Data Leaders Needing Mapping',  bs.data_leaders_unresolved||0, 'click to filter', '', 'applyUnknownKpiFilter'),
  ].join('');

  // Resolution potential
  const unkResEl = document.getElementById('unk-resolution-metrics');
  if (unkResEl) unkResEl.innerHTML = [
    makeKpiCard('top25', 'Top 25 Companies Impact', Math.min(25, unknownCompaniesBase.length), 'ranked by mapping impact score — click to view, then click a company for its people', 'good', 'applyUnknownKpiFilter'),
    makeKpiCard('auto_resolvable', 'Auto-Resolvable', bs.auto_resolvable_contacts||0, bs.auto_resolvable_companies + ' companies — company name alone suggests a bucket — click to filter', 'good', 'applyUnknownKpiFilter'),
    makeCard('Opportunity Bucket Score', kpi('unknown_resolution_score') + '/100', 'higher = better mapped'),
  ].join('');

  if (!activeUnknownKpi && !selectedMappingCompany) {
    filteredUnknownCompanies = unknownCompaniesBase.slice();
    filteredMappingPeople = mappingPeopleBase.slice();
  }
  renderUnknownCompaniesTable(activeUnknownKpi ? MAPPING_KPI_FILTERS[activeUnknownKpi].label : null);
  renderMappingPersonTable(activeUnknownKpi ? MAPPING_KPI_FILTERS[activeUnknownKpi].label : null);
  renderNeedsMappingActionPlan();

  // Persona breakdown for needs-mapping contacts — reuses the existing
  // MAPPING_KPI_FILTERS keys (recruiters/talent_acquisition/hiring_mgrs/
  // data_leaders were already defined but unused by any card until now).
  const unkPersonaEl = document.getElementById('unk-persona-metrics');
  if (!unkPersonaEl) return;
  unkPersonaEl.innerHTML = [
    makeKpiCard('recruiters',         'Recruiters Needing Mapping',      bs.recruiters_unresolved||0,          'map their companies first — click to filter', 'warn', 'applyUnknownKpiFilter'),
    makeKpiCard('talent_acquisition', 'Talent Acquisition — No Bucket',  bs.talent_acquisition_unresolved||0,  'click to filter', '', 'applyUnknownKpiFilter'),
    makeKpiCard('hiring_mgrs',        'Hiring Mgrs — No Bucket',         bs.hiring_managers_unresolved||0,     'potential direct hire — click to filter', '', 'applyUnknownKpiFilter'),
    makeKpiCard('data_leaders',       'Data Leaders — No Bucket',        bs.data_leaders_unresolved||0,        'referral network value — click to filter', '', 'applyUnknownKpiFilter'),
    makeKpiCard('companies_3plus',    'Companies with 3+ Unresolved',    bs.companies_with_3plus_unresolved||0,'highest mapping impact per company — click to filter', '', 'applyUnknownKpiFilter'),
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

  // ── Summary cards (Part 15 — V8 conversation-state KPI cards) ──────────────
  const sumEl = document.getElementById('leads-summary');
  if (sumEl) sumEl.innerHTML = [
    makeCard('Conversations Analyzed', lr.total_conversations || 0),
    makeKpiCard('needs_confirmed', 'Needs Reply — Confirmed', lr.needs_my_response_confirmed || 0, 'unresolved actionable request — click to filter', 'bad'),
    makeKpiCard('needs_likely',    'Needs Reply — Likely',    lr.needs_my_response_likely    || 0, 'probable request — click to filter', 'warn'),
    makeKpiCard('this_week',       'This Week Queue',         lr.this_week_count             || 0, 'weekly action limit — click to filter', 'warn'),
  ].join('');

  const pipeEl = document.getElementById('leads-pipeline');
  if (pipeEl) pipeEl.innerHTML = [
    makeKpiCard('active_interview',   'Active Interview Pipeline', lr.active_interview_pipeline || 0, 'CV requested / interview step', 'good'),
    makeKpiCard('awaiting_update',    'Awaiting Recruiter Update', lr.awaiting_recruiter_update  || 0, 'they promised an update'),
    makeKpiCard('rejected',           'Rejected / Closed',         lr.rejected_closed            || 0, 'process closed — not urgent'),
    makeKpiCard('location_blocked',   'Location / Eligibility Blocked', lr.location_eligibility_blocked || 0, 'geography/residency/work-auth constraint'),
    makeKpiCard('talent_pool',        'Talent Pool / Career Site',  lr.talent_pool_career_site    || 0, 'external action, not a reply'),
    makeKpiCard('dormant',            'Dormant Warm',               lr.dormant_warm_leads         || 0, 'warm but inactive', 'warn'),
    makeKpiCard('no_response',        'No Response',                lr.no_response_leads          || 0, 'sent, no reply'),
    makeKpiCard('reactivate_month',   'Reactivate This Month',      lr.reactivate_this_month      || 0, 'cooldown cleared — safe to reach out', 'good'),
  ].join('');

  // ── Operational summary — a real working queue, not just a raw taxonomy ────
  const opEl = document.getElementById('leads-operational-summary');
  if (opEl) opEl.innerHTML = [
    makeKpiCard('urgent_confirmed', 'Most Urgent Confirmed Replies', lr.most_urgent_confirmed_count    || 0, 'confirmed, non-terminal — click to filter', 'bad'),
    makeKpiCard('warm_recruiter',   'Warm Recruiter Follow-ups',     lr.warm_recruiter_followups_count || 0, 'recruiter conversation still live — click to filter', 'warn'),
    makeKpiCard('stale_valuable',   'Stale But Valuable Leads',      lr.stale_but_valuable_count       || 0, 'gone quiet, still worth revisiting — click to filter', 'warn'),
    makeKpiCard('closed_low_action','Closed / Low-Action Backlog',   lr.closed_low_action_count        || 0, 'terminal state — not urgent — click to filter'),
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
  const allContacts = lr.top_reactivation_contacts || [];
  filteredLeads = allContacts;
  _populateLeadFilterOptions(allContacts);
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

function _populateLeadFilterOptions(contacts) {
  const personaSel = document.getElementById('lead-persona-filter');
  const marketSel  = document.getElementById('lead-market-filter');
  if (personaSel && personaSel.options.length <= 1) {
    [...new Set(contacts.map(c => c.persona || '').filter(Boolean))].sort().forEach(p => {
      const o = document.createElement('option'); o.value = p; o.textContent = p; personaSel.appendChild(o);
    });
  }
  if (marketSel && marketSel.options.length <= 1) {
    [...new Set(contacts.map(c => c.strategic_market || '').filter(Boolean))].sort().forEach(m => {
      const o = document.createElement('option'); o.value = m; o.textContent = m; marketSel.appendChild(o);
    });
  }
}

// Part 6 — full client-side filter bar for the Lead Reactivation page
window.applyLeadFilters = function() {
  const search    = (document.getElementById('lead-search')?.value || '').trim().toLowerCase();
  const status    = document.getElementById('lead-status-filter')?.value || '';
  const category  = document.getElementById('lead-category-filter')?.value || '';
  const temp      = document.getElementById('lead-temp-filter')?.value || '';
  const persona   = document.getElementById('lead-persona-filter')?.value || '';
  const market    = document.getElementById('lead-market-filter')?.value || '';
  const sender    = document.getElementById('lead-sender-filter')?.value || '';
  const needsResp = document.getElementById('lead-needs-response-filter')?.value || '';
  const replied   = document.getElementById('lead-replied-filter')?.value || '';
  const ghosted   = document.getElementById('lead-ghosted-filter')?.value || '';
  const autoReply = document.getElementById('lead-autoreply-filter')?.value || '';
  const positive  = document.getElementById('lead-positive-filter')?.value || '';
  const interview = document.getElementById('lead-interview-filter')?.value || '';
  const recency   = document.getElementById('lead-recency-filter')?.value || '';
  const minScore  = parseFloat(document.getElementById('lead-min-score')?.value) || 0;
  const thisWeekOnly = document.getElementById('lead-this-week-only')?.checked || false;
  const recOnly   = document.getElementById('lead-recruiter-only')?.checked || false;
  const highConfOnly   = document.getElementById('lead-high-confidence-only')?.checked || false;
  const staleOnly       = document.getElementById('lead-stale-only')?.checked || false;
  const terminalOnly    = document.getElementById('lead-terminal-only')?.checked || false;
  const warmOnly        = document.getElementById('lead-warm-only')?.checked || false;
  const interviewRelatedOnly = document.getElementById('lead-interview-related-only')?.checked || false;

  const lr = D.lead_reactivation || {};
  const thisWeekIds = new Set((lr.this_week_contacts || []).map(c => c.other_person_profile_url || c.other_person_name));
  const source = lr.top_reactivation_contacts || [];

  filteredLeads = source.filter(c => {
    if (search) {
      const hay = ((c.other_person_name||'') + ' ' + (c.company_clean||'')).toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (status   && c.conversation_status !== status) return false;
    if (category && c.lead_category       !== category) return false;
    if (temp     && c.lead_temperature    !== temp) return false;
    if (persona  && c.persona             !== persona) return false;
    if (market   && c.strategic_market    !== market) return false;
    if (sender   && (c.last_sender_type || '') !== sender) return false;
    if (needsResp === 'confirmed' && c.lead_category !== 'Needs my response — Confirmed') return false;
    if (needsResp === 'likely'    && c.lead_category !== 'Needs my response — Likely')    return false;
    if (needsResp === 'no'        && c.needs_my_response) return false;
    if (replied === 'yes' && !(c.messages_from_other_person > 0 || c.conversation_status === 'Warm lead' || c.needs_my_response)) return false;
    if (replied === 'no'  && (c.messages_from_other_person > 0 || c.needs_my_response)) return false;
    if (ghosted === 'yes' && !(c.last_sender_type === 'me' && c.conversation_status === 'No response')) return false;
    if (ghosted === 'no'  && (c.last_sender_type === 'me' && c.conversation_status === 'No response')) return false;
    if (autoReply === 'yes' && !c.is_auto_reply) return false;
    if (autoReply === 'no'  && c.is_auto_reply) return false;
    if (positive === 'yes' && !c.has_positive_signal) return false;
    if (positive === 'no'  && c.has_positive_signal) return false;
    if (interview === 'yes' && !c.has_interview_signal) return false;
    if (interview === 'no'  && c.has_interview_signal) return false;
    if (recency) {
      const d = c.days_since_last_message;
      const dn = (d === null || d === undefined || d === '') ? Infinity : parseInt(d);
      if (recency === '0-7'    && !(dn <= 7))            return false;
      if (recency === '8-30'   && !(dn >= 8 && dn <= 30)) return false;
      if (recency === '31-90'  && !(dn >= 31 && dn <= 90)) return false;
      if (recency === '91-180' && !(dn >= 91 && dn <= 180)) return false;
      if (recency === '180+'   && !(dn > 180))            return false;
    }
    if ((parseFloat(c.reactivation_priority_score) || 0) < minScore) return false;
    if (thisWeekOnly && !thisWeekIds.has(c.other_person_profile_url || c.other_person_name)) return false;
    if (recOnly && !['Recruiter','Talent Acquisition','Sourcer','Hiring Manager','Engineering Manager'].includes(c.persona)) return false;
    if (highConfOnly && !((parseFloat(c.reply_obligation_confidence) || 0) >= 0.7)) return false;
    if (staleOnly && !c.stale_conversation_flag) return false;
    if (terminalOnly && !c.terminal_state_flag) return false;
    if (warmOnly && c.lead_category !== 'Warm reactivation') return false;
    if (interviewRelatedOnly && !(c.has_interview_signal || c.process_state === 'INTERVIEW_PIPELINE')) return false;
    return true;
  });
  activeLeadKpi = null;
  _updateActiveKpiCards();
  renderLeadsTable();
};

window.resetLeadFilters = function() {
  ['lead-search'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  ['lead-status-filter','lead-category-filter','lead-temp-filter','lead-persona-filter',
   'lead-market-filter','lead-sender-filter','lead-needs-response-filter','lead-replied-filter',
   'lead-ghosted-filter','lead-autoreply-filter','lead-positive-filter','lead-interview-filter',
   'lead-recency-filter'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  const ms = document.getElementById('lead-min-score'); if (ms) ms.value = '0';
  const tw = document.getElementById('lead-this-week-only'); if (tw) tw.checked = false;
  const r  = document.getElementById('lead-recruiter-only'); if (r) r.checked = false;
  ['lead-high-confidence-only', 'lead-stale-only', 'lead-terminal-only', 'lead-warm-only',
   'lead-interview-related-only'].forEach(id => { const el = document.getElementById(id); if (el) el.checked = false; });
  filteredLeads = (D.lead_reactivation || {}).top_reactivation_contacts || [];
  activeLeadKpi = null;
  _updateActiveKpiCards();
  renderLeadsTable();
};

// Part 2 — clickable Lead Reactivation KPI cards. Each key's predicate mirrors
// EXACTLY the same definition the backend used to compute the number shown on
// the card (see src/lead_reactivation_engine.py), so "Showing N" always
// matches the card count it was clicked from.
let activeLeadKpi = null;

const LEAD_KPI_FILTERS = {
  needs_confirmed: { label: 'Needs Reply — Confirmed', match: c => c.lead_category === 'Needs my response — Confirmed' },
  needs_likely:    { label: 'Needs Reply — Likely',    match: c => c.lead_category === 'Needs my response — Likely' },
  active_interview:{ label: 'Active Interview Pipeline', match: c => c.lead_category === 'Active Interview Pipeline' },
  awaiting_update: { label: 'Awaiting Recruiter Update', match: c => c.lead_category === 'Awaiting Recruiter Update' },
  rejected:        { label: 'Rejected / Closed',       match: c => c.lead_category === 'Rejected / Closed' },
  location_blocked:{ label: 'Location / Eligibility Blocked', match: c => c.lead_category === 'Location / Eligibility Blocked' },
  talent_pool:     { label: 'Talent Pool / Career Site', match: c => c.lead_category === 'Talent Pool / Career Site' },
  dormant:         { label: 'Dormant Warm',            match: c => c.lead_category === 'Dormant warm' },
  no_response:     { label: 'No Response',             match: c => c.lead_category === 'No response' },
  reactivate_month:{ label: 'Reactivate This Month',   match: c => c.lead_category === 'Reactivate This Month' },
  this_week:       { label: 'This Week Queue',         match: null }, // special-cased below
  // Operational summary (Part 1) — cuts across lead_category, mirrors the
  // exact predicates used to compute the card counts in lead_reactivation_engine.py.
  urgent_confirmed: { label: 'Most Urgent Confirmed Replies', match: c => c.reply_obligation === 'CONFIRMED' && !c.terminal_state_flag },
  warm_recruiter:    { label: 'Warm Recruiter Follow-ups',    match: c => !!c.recruiter_priority_flag && c.reply_obligation !== 'CONFIRMED' },
  stale_valuable:     { label: 'Stale But Valuable Leads',     match: c => !!c.stale_conversation_flag && (parseFloat(c.relationship_value_score) || 0) >= 40 },
  closed_low_action:  { label: 'Closed / Low-Action Backlog',  match: c => !!c.terminal_state_flag },
  // Executive Overview cross-page routing (Part 1) — combine categories the
  // way the backend's own summary counts do (see lead_reactivation_engine.py
  // hot_count/warm_count), so these mirror the Executive card numbers exactly.
  needs_response_all: { label: 'Needs My Response (Confirmed + Likely)', match: c => c.lead_category === 'Needs my response — Confirmed' || c.lead_category === 'Needs my response — Likely' },
  hot_reactivation:    { label: 'Hot Reactivation',            match: c => c.lead_category === 'Active Interview Pipeline' || c.lead_category === 'Needs my response — Confirmed' },
  warm_reactivation:   { label: 'Warm Reactivation',           match: c => c.lead_category === 'Warm reactivation' },
  follow_up_due_status:{ label: 'Follow-ups Due',              match: c => c.conversation_status === 'Follow-up due' },
};

function _updateActiveKpiCards() {
  document.querySelectorAll('#page-leads .kpi-card').forEach(el => {
    const isActive = activeLeadKpi && el.getAttribute('data-kpi') === activeLeadKpi;
    el.classList.toggle('active', !!isActive);
    el.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
}

window.applyLeadKpiFilter = function(key) {
  const def = LEAD_KPI_FILTERS[key];
  if (!def) return;
  const lr = D.lead_reactivation || {};
  const source = lr.top_reactivation_contacts || [];

  if (key === 'this_week') {
    const ids = new Set((lr.this_week_contacts || []).map(c => c.other_person_profile_url || c.other_person_name));
    filteredLeads = source.filter(c => ids.has(c.other_person_profile_url || c.other_person_name));
  } else {
    filteredLeads = source.filter(def.match);
  }

  activeLeadKpi = key;
  _updateActiveKpiCards();
  renderLeadsTable(def.label);

  const table = document.getElementById('leads-table');
  if (table) table.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

const NEEDS_RESPONSE_CONF_STYLE = {
  HIGH:   'background:#ef4444;color:#fff',
  MEDIUM: 'background:#f59e0b;color:#111',
  LOW:    'background:#9ca3af;color:#111',
  NONE:   'background:#374151;color:#aaa',
};

function needsResponseBadge(conf, numericConf) {
  const style = NEEDS_RESPONSE_CONF_STYLE[conf] || NEEDS_RESPONSE_CONF_STYLE.NONE;
  const title = numericConf != null ? ' title="Reply obligation confidence: ' + numericConf + '"' : '';
  return '<span' + title + ' style="' + style + ';padding:2px 6px;border-radius:4px;font-size:0.68rem;white-space:nowrap">' + (conf||'NONE') + '</span>';
}

function renderLeadsTable(kpiLabel) {
  const st = document.getElementById('leads-stats');
  if (st) {
    const total = ((D.lead_reactivation || {}).top_reactivation_contacts || []).length;
    const label = kpiLabel || (activeLeadKpi && LEAD_KPI_FILTERS[activeLeadKpi] ? LEAD_KPI_FILTERS[activeLeadKpi].label : '');
    st.textContent = 'Showing ' + filteredLeads.length + ' of ' + total + ' matching contacts'
      + (label ? ' — ' + label : '');
  }
  const tbody = document.getElementById('leads-tbody');
  if (!tbody) return;
  if (!filteredLeads.length) {
    tbody.innerHTML = '<tr><td colspan="17" style="text-align:center;color:var(--text-muted)">No contacts match the current filters.</td></tr>';
    return;
  }
  tbody.innerHTML = filteredLeads.map((r, i) => {
    const url    = r.other_person_profile_url || '';
    const score  = parseInt(r.reactivation_priority_score) || 0;
    const sCls   = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
    const icon   = STATUS_ICONS[r.conversation_status] || '';
    const lastSender = r.last_sender_type === 'me' ? 'Me' : (r.last_sender_type === 'other' ? 'Them' : '—');
    return '<tr>'
      + '<td><strong>#' + (i+1) + '</strong></td>'
      + '<td style="white-space:nowrap">' + (r.other_person_name||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (r.company_clean||'—') + '</td>'
      + '<td style="white-space:nowrap">' + (r.persona||'—') + '</td>'
      + '<td>' + marketBadge(r.strategic_market||'UNKNOWN') + '</td>'
      + '<td style="font-size:0.75rem">' + (r.lead_category||'—') + '</td>'
      + '<td>' + tempBadge(r.lead_temperature||'—') + '</td>'
      + '<td style="white-space:nowrap;font-size:0.78rem">' + icon + ' ' + (r.conversation_status||'—') + '</td>'
      + '<td style="font-size:0.75rem">' + lastSender + '</td>'
      + '<td style="white-space:nowrap;font-size:0.78rem">' + (r.last_message_date||'—') + '</td>'
      + '<td style="text-align:center">' + (r.days_since_last_message ?? '—') + '</td>'
      + '<td>' + needsResponseBadge(r.needs_response_confidence, r.reply_obligation_confidence) + '</td>'
      + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
      + '<td style="font-size:0.72rem;max-width:170px">' + String(r.recommended_next_action||'').substring(0,80) + '</td>'
      + '<td style="font-size:0.7rem;max-width:170px;color:var(--text-muted);cursor:help" '
        + 'title="Intent: ' + (r.sanitized_intent_label||'—') + ' | Confidence: ' + (r.reply_obligation_confidence ?? '—')
        + ' | Priority: ' + (r.action_priority_reason||'—') + '">'
        + String(r.reply_reason_short || r.needs_response_reason || '').substring(0,80) + '</td>'
      + '<td style="font-size:0.72rem;white-space:nowrap">' + (r.sanitized_intent_label||'—') + '</td>'
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

  // Resolution method breakdown (Part 15/16) — how every contact got its bucket.
  // V7 runs after V6 and its breakdown reflects the final state; fall back to
  // V6 only if V7 hasn't run yet (e.g. an older cached JSON).
  const v7 = D.company_resolution_v7 || {};
  const methodTbody = document.getElementById('quality-method-tbody');
  if (methodTbody) {
    const breakdown = v7.resolution_method_breakdown || (D.company_resolution_v6 || {}).resolution_method_breakdown || {};
    const entries = Object.entries(breakdown).sort((a, b) => b[1] - a[1]);
    const grand = entries.reduce((s, [, v]) => s + v, 0) || total || 1;
    if (!entries.length) {
      methodTbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--text-muted)">Run the pipeline to populate resolution-method breakdown.</td></tr>';
    } else {
      methodTbody.innerHTML = entries.map(([method, count]) => {
        const pct = (count / grand * 100).toFixed(1);
        return '<tr>'
          + '<td>' + (RESOLUTION_METHOD_LABEL[method] || method) + '</td>'
          + '<td><strong>' + count.toLocaleString() + '</strong></td>'
          + '<td>' + pct + '%' + scoreBar(count, grand, '#3b82f6') + '</td>'
          + '</tr>';
      }).join('');
    }
  }

  // Residual Mapping Pareto (Part 15/16) — where manual mapping has the highest ROI
  const paretoSumEl = document.getElementById('quality-pareto-summary');
  if (paretoSumEl) paretoSumEl.innerHTML = [
    makeCard('Unique Unresolved Companies', v7.pareto_unique_unresolved_companies || 0),
    makeCard('Top 10 Coverage',  (v7.pareto_top10_coverage_pct  || 0) + '%', 'of unresolved contacts'),
    makeCard('Top 25 Coverage',  (v7.pareto_top25_coverage_pct  || 0) + '%', 'of unresolved contacts'),
    makeCard('Top 50 Coverage',  (v7.pareto_top50_coverage_pct  || 0) + '%', 'of unresolved contacts'),
    makeCard('Top 100 Coverage', (v7.pareto_top100_coverage_pct || 0) + '%', 'of unresolved contacts'),
  ].join('');
  const paretoTbody = document.getElementById('quality-pareto-tbody');
  if (paretoTbody) {
    const rows = v7.pareto_top20 || [];
    if (!rows.length) {
      paretoTbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">See outputs/company_mapping_pareto_v7.csv for the full ranked backlog.</td></tr>';
    } else {
      paretoTbody.innerHTML = rows.map(r => '<tr>'
        + '<td>' + (r.rank||'—') + '</td>'
        + '<td>' + (r.company_canonical||'—') + '</td>'
        + '<td>' + (r.unresolved_contact_count||0) + '</td>'
        + '<td>' + (r.cumulative_pct_of_unresolved||0) + '%</td>'
        + '<td>' + (r.talent_acquisition||0) + '</td>'
        + '<td>' + (r.data_leaders||0) + '</td>'
        + '<td>' + (r.avg_priority_score||0) + '</td>'
        + '<td style="font-size:0.72rem">' + (r.suggested_bucket||'—') + '</td>'
        + '</tr>').join('');
    }
  }

  // Untapped Matching Quality (Part 23)
  // D.untapped_network_summary ships with data_quality.json (summary +
  // match_method_breakdown only); D.untapped_network (full object) wins if
  // the Untapped Network page has already been visited.
  const unMatchTbody = document.getElementById('quality-untapped-match-tbody');
  if (unMatchTbody) {
    const un = D.untapped_network || D.untapped_network_summary || {};
    const breakdown = un.match_method_breakdown || {};
    const entries = Object.entries(breakdown).sort((a, b) => b[1] - a[1]);
    const grand = (un.summary && un.summary.total_connections) || entries.reduce((s, [, v]) => s + v, 0) || 1;
    const METHOD_LABEL = {
      EXACT_PROFILE_URL: 'Exact profile URL', NORMALIZED_PROFILE_URL: 'Normalized profile URL',
      EXACT_NAME_COMPANY: 'Exact name + company', EXACT_UNIQUE_NAME: 'Unique name match',
      NAME_ROLE_MATCH: 'Name + compatible role', CONSERVATIVE_FUZZY: 'Conservative fuzzy match',
      AMBIGUOUS: 'Ambiguous — review', NO_MATCH: 'Confirmed no-match (untapped)',
    };
    if (!entries.length) {
      unMatchTbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--text-muted)">Run the pipeline to populate untapped matching quality.</td></tr>';
    } else {
      unMatchTbody.innerHTML = entries.map(([method, count]) => {
        const pct = (count / grand * 100).toFixed(1);
        return '<tr>'
          + '<td>' + (METHOD_LABEL[method] || method) + '</td>'
          + '<td><strong>' + count.toLocaleString() + '</strong></td>'
          + '<td>' + pct + '%' + scoreBar(count, grand, '#22c55e') + '</td>'
          + '</tr>';
      }).join('');
    }
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
  const geoNoteEl = document.getElementById('quality-geo-note');
  if (geoNoteEl) geoNoteEl.textContent = kpi('technical_geo_limitation_note',
    'Exact geography is unavailable from LinkedIn export; opportunity buckets are inferred from company, ' +
    'title, persona, language, message history, and manual enrichment. This is an action backlog, not a data failure.');

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

// ── PAGE 10: Weekly Evolution ─────────────────────────────────────────────────
function _deltaSub(n, positiveIsGood = true) {
  if (n === null || n === undefined) return { text: 'not tracked yet', cls: '' };
  const v = parseFloat(n) || 0;
  const sign = v > 0 ? '+' : '';
  const cls = v === 0 ? '' : (v > 0) === positiveIsGood ? 'good' : 'bad';
  return { text: sign + v.toLocaleString() + ' vs previous', cls };
}

// ── Weekly Evolution card drill-down (Part 4) ────────────────────────────────
// Every card here filters D.weekly_people_delta_segments (built by
// src/weekly_kpi_delta.py from a real previous-vs-current snapshot diff, not
// a fabricated estimate). Signed-delta cards (LATAM/USD Δ, etc.) never force
// a single list to equal the signed number — they show two separately
// countable sub-lists ("moved in" / "moved out") whose difference is the
// card's delta, via `splitInOut`.
let activeWeeklyKpi = null;
// 'latest' | 'cumulative' | a snapshot_number (as string) from D.weekly_history.
// Person-level drilldown (D.weekly_people_delta_segments) only ever reflects
// the LATEST comparison — selecting any other week must never silently show
// the latest week's people under a mislabeled historical selection.
let selectedWeeklyWeek = 'latest';

const WEEKLY_KPI_FILTERS = {
  gross_new:         { label: 'Gross New Connections',        match: r => r.segment === 'new_this_week' },
  net_growth:        { label: 'Net Growth',                   splitInOut: true, inMatch: r => r.segment === 'new_this_week', outMatch: r => r.segment === 'removed_this_week' },
  new_recruiters:    { label: 'New Recruiters',                match: r => r.segment === 'new_this_week' && r.persona === 'Recruiter' },
  new_ta:            { label: 'New Talent Acquisition',       match: r => r.segment === 'new_this_week' && r.persona === 'Talent Acquisition' },
  new_hm:            { label: 'New Hiring Managers',           match: r => r.segment === 'new_this_week' && ['Hiring Manager', 'Engineering Manager'].includes(r.persona) },
  new_data_leaders:  { label: 'New Data Leaders',              match: r => r.segment === 'new_this_week' && ['Data Engineering Manager', 'Head of Data', 'Director'].includes(r.persona) },
  latam_usd_delta:   { label: 'LATAM/USD Δ',                   splitInOut: true, inMatch: r => ['new_this_week', 'bucket_change_in'].includes(r.segment) && r.bucket_group === 'latam_usd', outMatch: r => ['removed_this_week', 'bucket_change_out'].includes(r.segment) && r.bucket_group === 'latam_usd' },
  us_canada_delta:   { label: 'US/Canada Nearshore Δ',         splitInOut: true, inMatch: r => ['new_this_week', 'bucket_change_in'].includes(r.segment) && r.bucket_group === 'us_canada', outMatch: r => ['removed_this_week', 'bucket_change_out'].includes(r.segment) && r.bucket_group === 'us_canada' },
  spain_eu_delta:    { label: 'Spain/EU Δ',                    splitInOut: true, inMatch: r => ['new_this_week', 'bucket_change_in'].includes(r.segment) && r.bucket_group === 'spain_eu', outMatch: r => ['removed_this_week', 'bucket_change_out'].includes(r.segment) && r.bucket_group === 'spain_eu' },
  global_opp_delta:  { label: 'Global Opportunity Δ',          splitInOut: true, inMatch: r => ['new_this_week', 'bucket_change_in'].includes(r.segment) && r.bucket_group === 'global_opportunity', outMatch: r => ['removed_this_week', 'bucket_change_out'].includes(r.segment) && r.bucket_group === 'global_opportunity' },
  needs_mapping_delta:{ label: 'Needs Mapping Δ',              splitInOut: true, inMatch: r => ['new_this_week', 'bucket_change_in'].includes(r.segment) && r.bucket_group === 'needs_mapping', outMatch: r => ['removed_this_week', 'bucket_change_out'].includes(r.segment) && r.bucket_group === 'needs_mapping' },
  needs_response_delta:{ label: 'Needs My Response Δ',         match: r => r.segment === 'lead_category_change' && (r.current_value === 'Needs my response — Confirmed' || r.current_value === 'Needs my response — Likely') },
  hot_leads_delta:   { label: 'Hot Leads Δ',                   match: r => r.segment === 'lead_category_change' && (r.current_value === 'Active Interview Pipeline' || r.current_value === 'Needs my response — Confirmed') },
  warm_leads_delta:  { label: 'Warm Leads Δ',                  match: r => r.segment === 'lead_category_change' && r.current_value === 'Warm reactivation' },
  follow_ups_due_delta:{ label: 'Follow-ups Due Δ',            match: r => r.segment === 'lead_category_change' && r.current_value === 'Follow-up due' },
  interview_pipeline_delta:{ label: 'Interview Pipeline Δ',    match: r => r.segment === 'interview_pipeline_change' },
  never_contacted_delta:{ label: 'Never Contacted — Confirmed Δ', match: r => r.segment === 'untapped_category_change' && r.current_value === 'HIGH_VALUE_UNTAPPED' },
  high_value_untapped_delta:{ label: 'High-Value Untapped Δ', match: r => r.segment === 'untapped_category_change' && r.current_value === 'HIGH_VALUE_UNTAPPED' },
  untapped_recruiters_delta:{ label: 'Untapped Recruiters Δ', match: r => r.segment === 'untapped_category_change' && r.persona === 'Recruiter' },
  untapped_hm_delta: { label: 'Untapped Hiring Managers Δ',    match: r => r.segment === 'untapped_category_change' && ['Hiring Manager', 'Engineering Manager'].includes(r.persona) },
  activated_this_week:{ label: 'Activated This Week',          match: r => r.segment === 'activated_this_week' },
};

window.applyWeeklyKpiFilter = function(key) {
  const def = WEEKLY_KPI_FILTERS[key];
  if (!def) return;
  const panel = document.getElementById('weekly-drilldown');
  const titleEl = document.getElementById('weekly-drilldown-title');
  const tbody = document.getElementById('weekly-drilldown-tbody');
  const statsEl = document.getElementById('weekly-drilldown-stats');
  if (!panel || !tbody) return;
  activeWeeklyKpi = key;
  if (titleEl) titleEl.textContent = def.label;

  // Part 4 — honesty guard: identity-level segments only ever cover the
  // LATEST snapshot comparison. Never show them under a historical/
  // cumulative week selection — that would misattribute the latest week's
  // people to a different period.
  if (selectedWeeklyWeek !== 'latest') {
    if (statsEl) statsEl.textContent = 'Selected week — ' + def.label + ' (aggregate only)';
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">'
      + 'No identity-level list is available for this metric. This value is aggregate-only.</td></tr>';
    panel.style.display = '';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return;
  }
  const source = D.weekly_people_delta_segments || [];

  let rows;
  if (def.splitInOut) {
    const inRows = source.filter(def.inMatch).map(r => ({ ...r, _dir: 'Moved in' }));
    const outRows = source.filter(def.outMatch).map(r => ({ ...r, _dir: 'Moved out' }));
    rows = inRows.concat(outRows);
    if (statsEl) statsEl.textContent = 'Moved in: ' + inRows.length + '  ·  Moved out: ' + outRows.length
      + '  (net ' + (inRows.length - outRows.length >= 0 ? '+' : '') + (inRows.length - outRows.length) + ') — ' + def.label;
  } else {
    rows = source.filter(def.match).map(r => ({ ...r, _dir: r.current_value || r.segment }));
    if (statsEl) statsEl.textContent = 'Showing ' + rows.length + ' — ' + def.label;
  }

  tbody.innerHTML = rows.length ? rows.map(r => '<tr>'
    + '<td style="white-space:nowrap">' + (r.full_name||'—') + '</td>'
    + '<td style="white-space:nowrap">' + (r.company_clean||'—') + '</td>'
    + '<td style="white-space:nowrap">' + (r.persona||'—') + '</td>'
    + '<td>' + marketBadge(r.opportunity_bucket||'—') + '</td>'
    + '<td style="font-size:0.72rem">' + (r._dir||'—') + '</td>'
    + '<td>' + fmt(r.priority_score||0) + '</td>'
    + '<td style="font-size:0.7rem;color:var(--text-muted)">' + (r.match_method||'—') + '</td>'
    + '<td>' + (r.url ? '<a href="' + r.url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
    + '</tr>').join('')
    : '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">No people matched this delta this week. Matched by profile URL/name/company from weekly snapshot comparison.</td></tr>';

  panel.style.display = '';
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

// ── Part 2/3 — multi-week support ────────────────────────────────────────────
// Maps a flat outputs/action_plan_weekly_history.csv row (D.weekly_history
// entry) onto the same nested shape build_weekly_evolution() produces, so
// the EXACT SAME card-rendering function (_renderWeeklyCardsFromEvolution)
// works for both the live "latest" comparison and any recorded past week.
// Fields the flat history row does not carry (follow-ups due, interview
// pipeline, most untapped sub-deltas) are left null — _deltaSub(null)
// already renders those as "not tracked yet", never a fabricated 0.
function _historyRowToEvolutionShape(row) {
  if (!row) return null;
  return {
    current_snapshot_label: row.current_snapshot_date,
    previous_snapshot_label: row.previous_snapshot_date,
    network_growth: {
      previous_connections: row.total_connections_previous,
      current_connections: row.total_connections_current,
      new_connections: row.gross_new_connections,
      net_growth: row.net_connection_growth,
    },
    strategic_growth: {
      new_recruiters: row.new_recruiters,
      new_talent_acquisition: row.new_talent_acquisition,
      new_hiring_managers: row.new_hiring_managers,
      new_data_leaders: row.new_data_leaders,
    },
    market_movement: {
      latam_usd_delta: row.latam_usd_delta,
      us_canada_nearshore_delta: row.us_canada_delta,
      spain_eu_delta: row.spain_eu_delta,
      global_opportunity_delta: row.global_opportunity_delta,
      needs_mapping_delta: row.needs_company_mapping_delta,
    },
    lead_pipeline_movement: {
      needs_my_response_delta: row.needs_my_response_delta,
      hot_leads_delta: row.hot_reactivation_delta,
      warm_leads_delta: row.warm_reactivation_delta,
      follow_ups_due_delta: null,
      interview_pipeline_delta: null,
    },
    untapped_network_movement: {
      available: row.untapped_activated_this_week !== null && row.untapped_activated_this_week !== undefined,
      tracked_since_last_snapshot: true,
      never_contacted_confirmed_delta: null,
      high_value_untapped_delta: null,
      untapped_recruiters_delta: null,
      untapped_hiring_managers_delta: null,
      untapped_contacts_activated_this_week_estimate: row.untapped_activated_this_week,
    },
    new_high_value_connections: [], // never fabricated for a past week — no per-person list exists here
    diagnosis: {
      strongest_growth_area: null,
      weakest_strategic_area: null,
      allocation_90_10_note: row.strategy_mix_status ? ('Strategy mix status: ' + row.strategy_mix_status) : null,
      highest_value_next_action: row.recommendation || null,
    },
  };
}

function _weeklyHistoryLabel(row) {
  return (row.previous_snapshot_date || '—') + ' → ' + (row.current_snapshot_date || '—');
}

// "All available weeks / cumulative view" — sums additive fields across
// every recorded week, keeps the earliest previous_connections / latest
// current_connections (summing connection counts across weeks would be
// meaningless), and is always clearly labeled as cumulative, never
// presented as if it were a single week.
function _buildCumulativeHistoryRow(history) {
  if (!history || !history.length) return null;
  const sorted = [...history].sort((a, b) => (a.snapshot_number || 0) - (b.snapshot_number || 0));
  const first = sorted[0], last = sorted[sorted.length - 1];
  const sum = key => sorted.reduce((s, r) => s + (parseFloat(r[key]) || 0), 0);
  return {
    snapshot_number: 'cumulative',
    previous_snapshot_date: first.previous_snapshot_date,
    current_snapshot_date: last.current_snapshot_date,
    total_connections_previous: first.total_connections_previous,
    total_connections_current: last.total_connections_current,
    gross_new_connections: sum('gross_new_connections'),
    net_connection_growth: sum('net_connection_growth'),
    connection_churn_or_removed_estimate: sum('connection_churn_or_removed_estimate'),
    new_recruiters: sum('new_recruiters'),
    new_talent_acquisition: sum('new_talent_acquisition'),
    new_hiring_managers: sum('new_hiring_managers'),
    new_data_leaders: sum('new_data_leaders'),
    latam_usd_delta: sum('latam_usd_delta'),
    us_canada_delta: sum('us_canada_delta'),
    spain_eu_delta: sum('spain_eu_delta'),
    global_opportunity_delta: sum('global_opportunity_delta'),
    needs_company_mapping_delta: sum('needs_company_mapping_delta'),
    needs_my_response_delta: sum('needs_my_response_delta'),
    hot_reactivation_delta: sum('hot_reactivation_delta'),
    warm_reactivation_delta: sum('warm_reactivation_delta'),
    untapped_activated_this_week: sum('untapped_activated_this_week'),
    actual_primary_share: last.actual_primary_share,
    actual_europe_share: last.actual_europe_share,
    strategy_mix_status: last.strategy_mix_status,
    diagnosis_summary: 'Cumulative across ' + sorted.length + ' recorded week(s).',
    recommendation: last.recommendation,
    new_connections_target_min: null, new_connections_target_max: null,
  };
}

window.applyWeeklyWeekFilter = function(value) {
  selectedWeeklyWeek = value;
  const history = D.weekly_history || [];
  const noteEl = document.getElementById('weekly-selection-note');
  const labelEl = document.getElementById('weekly-snapshot-label');

  let evolutionShape, historyRow = null, prevRow = null;
  if (value === 'latest') {
    evolutionShape = D.weekly_evolution || {};
    const sorted = [...history].sort((a, b) => (a.snapshot_number || 0) - (b.snapshot_number || 0));
    historyRow = sorted[sorted.length - 1] || null;
    prevRow = sorted[sorted.length - 2] || null;
    if (noteEl) noteEl.textContent = '';
  } else if (value === 'cumulative') {
    historyRow = _buildCumulativeHistoryRow(history);
    evolutionShape = _historyRowToEvolutionShape(historyRow) || {};
    if (noteEl) noteEl.textContent = 'Cumulative view across ' + history.length + ' recorded week(s) — person-level drilldown is not available for this view.';
  } else {
    historyRow = history.find(r => String(r.snapshot_number) === String(value)) || null;
    const sorted = [...history].sort((a, b) => (a.snapshot_number || 0) - (b.snapshot_number || 0));
    const idx = sorted.findIndex(r => String(r.snapshot_number) === String(value));
    prevRow = idx > 0 ? sorted[idx - 1] : null;
    evolutionShape = _historyRowToEvolutionShape(historyRow) || {};
    if (noteEl) noteEl.textContent = historyRow ? 'Historical week — person-level drilldown is not available for past weeks (aggregate numbers only).' : 'No recorded data for this week.';
  }

  if (labelEl) labelEl.textContent =
    'Current Snapshot: ' + (evolutionShape.current_snapshot_label || '—') +
    '   |   Previous Snapshot: ' + (evolutionShape.previous_snapshot_label || '—');

  _renderWeeklyCardsFromEvolution(evolutionShape);
  renderSundayStrategyReview(historyRow, prevRow);

  // Closing any open drilldown panel on a week switch avoids showing a
  // stale identity-level list (from the previously selected week) under
  // the newly selected week's label.
  const panel = document.getElementById('weekly-drilldown');
  if (panel) panel.style.display = 'none';
};

function _populateWeeklyWeekSelector() {
  const sel = document.getElementById('weekly-week-selector');
  if (!sel) return;
  const history = D.weekly_history || [];
  const sorted = [...history].sort((a, b) => (a.snapshot_number || 0) - (b.snapshot_number || 0));
  const existing = new Set(Array.from(sel.options).map(o => o.value));
  sorted.forEach(row => {
    const val = String(row.snapshot_number);
    if (existing.has(val)) return;
    const o = document.createElement('option');
    o.value = val;
    o.textContent = _weeklyHistoryLabel(row);
    sel.appendChild(o);
  });
  if (sorted.length && !existing.has('cumulative')) {
    const o = document.createElement('option');
    o.value = 'cumulative';
    o.textContent = 'All available weeks / cumulative view';
    sel.appendChild(o);
  }
}

function renderWeeklyHistoryTable() {
  const tbody = document.getElementById('weekly-history-tbody');
  const statsEl = document.getElementById('weekly-history-stats');
  if (!tbody) return;
  const history = [...(D.weekly_history || [])].sort((a, b) => (b.snapshot_number || 0) - (a.snapshot_number || 0));
  if (statsEl) statsEl.textContent = history.length
    ? 'Showing ' + history.length + ' recorded week(s)'
    : 'No recorded weeks yet.';
  tbody.innerHTML = history.length ? history.map(r => '<tr>'
    + '<td style="white-space:nowrap">Week ' + (r.snapshot_number ?? '—') + '</td>'
    + '<td style="white-space:nowrap">' + (r.previous_snapshot_date || '—') + '</td>'
    + '<td style="white-space:nowrap">' + (r.current_snapshot_date || '—') + '</td>'
    + '<td>' + fmt(r.gross_new_connections) + '</td>'
    + '<td>' + fmt(r.net_connection_growth) + '</td>'
    + '<td>' + fmt(r.connection_churn_or_removed_estimate) + '</td>'
    + '<td>' + fmt(r.new_recruiters) + '</td>'
    + '<td>' + fmt(r.new_talent_acquisition) + '</td>'
    + '<td>' + fmt(r.new_hiring_managers) + '</td>'
    + '<td>' + (r.latam_usd_delta ?? '—') + '</td>'
    + '<td>' + (r.us_canada_delta ?? '—') + '</td>'
    + '<td>' + (r.spain_eu_delta ?? '—') + '</td>'
    + '<td>' + (r.needs_company_mapping_delta ?? '—') + '</td>'
    + '<td>' + (r.hot_reactivation_delta ?? '—') + '</td>'
    + '<td>' + (r.warm_reactivation_delta ?? '—') + '</td>'
    + '<td>' + (r.needs_my_response_delta ?? '—') + '</td>'
    + '<td style="font-size:0.72rem;max-width:220px;white-space:normal">' + (r.diagnosis_summary || '—').substring(0, 160) + '</td>'
    + '</tr>').join('')
    : '<tr><td colspan="17" style="text-align:center;color:var(--text-muted)">No recorded weeks yet. Keep adding weekly snapshots every Sunday.</td></tr>';
}

function renderWeeklyTrendChart() {
  const history = [...(D.weekly_history || [])].sort((a, b) => (a.snapshot_number || 0) - (b.snapshot_number || 0));
  const msgEl = document.getElementById('weekly-trend-msg');
  const msgTextEl = document.getElementById('weekly-trend-msg-text');
  if (history.length < 2) {
    destroyChart('chart-weekly-trend');
    if (msgEl) msgEl.style.display = '';
    if (msgTextEl) msgTextEl.textContent = 'Not enough weekly history yet. Keep adding weekly snapshots every Sunday.';
    return;
  }
  if (msgEl) msgEl.style.display = 'none';
  const labels = history.map(r => 'Week ' + (r.snapshot_number ?? '—'));
  groupedBarChart('chart-weekly-trend', labels, [
    { label: 'Gross New Connections', data: history.map(r => r.gross_new_connections || 0), color: '#3b82f6' },
    { label: 'Net Growth', data: history.map(r => r.net_connection_growth || 0), color: '#22c55e' },
    { label: 'Recruiter + TA Growth', data: history.map(r => (parseFloat(r.new_recruiters) || 0) + (parseFloat(r.new_talent_acquisition) || 0)), color: '#f59e0b' },
    { label: 'LATAM/USD Growth', data: history.map(r => r.latam_usd_delta || 0), color: '#d97706' },
    { label: 'Spain/EU Growth', data: history.map(r => r.spain_eu_delta || 0), color: '#dc2626' },
    { label: 'Needs Mapping Movement', data: history.map(r => r.needs_company_mapping_delta || 0), color: '#8b5cf6' },
  ]);
}

// ── Part 6 — Sunday Strategy Review: deterministic rules only, no
// free-text/AI generation. Operates on one weekly_history row (+ the prior
// row for trend comparisons, when available) — same field shape whether
// the selected week is the latest or a historical one.
function renderSundayStrategyReview(row, prevRow) {
  const el = document.getElementById('weekly-strategy-review');
  if (!el) return;
  if (!row) {
    el.innerHTML = '<div class="alert alert-info"><span class="alert-icon">&#8505;&#65039;</span>'
      + '<span>Not enough weekly history yet. Keep adding weekly snapshots every Sunday.</span></div>';
    return;
  }

  const items = [];
  const num = v => (v === null || v === undefined || v === '') ? null : parseFloat(v);

  // 1. Weekly connection target
  const gross = num(row.gross_new_connections);
  const tmin = num(row.new_connections_target_min), tmax = num(row.new_connections_target_max);
  if (gross !== null && tmin !== null && tmax !== null) {
    const hit = gross >= tmin && gross <= tmax;
    const above = gross > tmax;
    items.push({ cls: hit ? 'good' : (above ? 'info' : 'warn'),
      text: 'Weekly connection target: ' + gross + ' new connections vs target ' + tmin + '-' + tmax + '/wk — '
        + (hit ? 'on target.' : above ? 'above target.' : 'below target.') });
  } else {
    items.push({ cls: '', text: 'Weekly connection target: no target configured for this week — cannot assess.' });
  }

  // 2. 90% LATAM/USD + 10% Spain/EU exploratory mix
  if (row.strategy_mix_status) {
    const onStrategy = row.strategy_mix_status === 'ON_STRATEGY' || row.strategy_mix_status === 'ON_TRACK';
    items.push({ cls: onStrategy ? 'good' : 'warn',
      text: '90/10 LATAM/USD vs Spain/EU mix: ' + row.strategy_mix_status.replace(/_/g, ' ')
        + (row.actual_primary_share != null ? (' (primary ' + Math.round(row.actual_primary_share * 100) + '%, Europe ' + Math.round((row.actual_europe_share || 0) * 100) + '%)') : '') + '.' });
  }

  // 3. Recruiter/TA pipeline trend
  const recTa = (v => v === null ? null : (parseFloat(v.new_recruiters) || 0) + (parseFloat(v.new_talent_acquisition) || 0));
  const recTaCur = recTa(row), recTaPrev = prevRow ? recTa(prevRow) : null;
  if (recTaPrev !== null) {
    items.push({ cls: recTaCur >= recTaPrev ? 'good' : 'warn',
      text: 'Recruiter/TA pipeline: ' + recTaCur + ' this week vs ' + recTaPrev + ' last week — '
        + (recTaCur > recTaPrev ? 'improved.' : recTaCur === recTaPrev ? 'flat.' : 'declined.') });
  } else {
    items.push({ cls: '', text: 'Recruiter/TA pipeline: not enough history to assess trend yet.' });
  }

  // 4. Hiring manager pipeline trend
  const hmCur = num(row.new_hiring_managers), hmPrev = prevRow ? num(prevRow.new_hiring_managers) : null;
  if (hmCur !== null && hmPrev !== null) {
    items.push({ cls: hmCur >= hmPrev ? 'good' : 'warn',
      text: 'Hiring manager pipeline: ' + hmCur + ' this week vs ' + hmPrev + ' last week — '
        + (hmCur > hmPrev ? 'improved.' : hmCur === hmPrev ? 'flat.' : 'declined.') });
  } else {
    items.push({ cls: '', text: 'Hiring manager pipeline: not enough history to assess trend yet.' });
  }

  // 5. Needs Mapping
  const needsMapDelta = num(row.needs_company_mapping_delta);
  if (needsMapDelta !== null) {
    items.push({ cls: needsMapDelta > 0 ? 'bad' : (needsMapDelta < 0 ? 'good' : ''),
      text: 'Needs Company Mapping: ' + (needsMapDelta > 0 ? '+' : '') + needsMapDelta + ' this week — '
        + (needsMapDelta > 0 ? 'got worse.' : needsMapDelta < 0 ? 'improved.' : 'no change.') });
  }

  // 6. Lead reactivation
  const hotD = num(row.hot_reactivation_delta), warmD = num(row.warm_reactivation_delta), needsRespD = num(row.needs_my_response_delta);
  if (hotD !== null || warmD !== null) {
    const net = (hotD || 0) + (warmD || 0);
    items.push({ cls: net > 0 ? 'good' : (net < 0 ? 'warn' : ''),
      text: 'Lead reactivation: hot ' + (hotD ?? '—') + ', warm ' + (warmD ?? '—')
        + (needsRespD != null ? (', needs-my-response ' + (needsRespD > 0 ? '+' : '') + needsRespD) : '') + ' — '
        + (net > 0 ? 'improved.' : net < 0 ? 'declined.' : 'flat.') });
  }

  // 7. Next-week focus — deterministic combination rules
  const latamDelta = num(row.latam_usd_delta);
  const focusRules = [];
  if (gross !== null && tmax !== null && gross > tmax && latamDelta !== null && latamDelta < Math.max(2, Math.round(gross * 0.2))) {
    focusRules.push('Gross New Connections is above target but LATAM/USD confirmed growth is low — recommend mapping new companies and prioritizing LATAM recruiter outreach.');
  }
  if (needsMapDelta !== null && needsMapDelta > 0) {
    focusRules.push('Needs Mapping increased — recommend mapping top unresolved companies before adding more broad connections.');
  }
  if (row.actual_europe_share != null && row.actual_europe_share > 0.10) {
    focusRules.push('Spain/EU growth is above 10% — do not over-invest in Europe yet; the next 60 days are still LATAM/USD-first.');
  }
  if (!focusRules.length) {
    focusRules.push('No specific rule triggered this week — continue the current 90% LATAM/USD / 10% Spain/EU outreach plan.');
  }
  focusRules.forEach(text => items.push({ cls: 'info', text: 'Next week focus: ' + text }));

  el.innerHTML = items.map(i => '<div class="alert alert-' + (i.cls === 'good' ? 'good' : i.cls === 'bad' ? 'bad' : i.cls === 'warn' ? 'warn' : 'info') + '">'
    + '<span class="alert-icon">' + (i.cls === 'good' ? '&#9989;' : i.cls === 'bad' ? '&#10060;' : i.cls === 'warn' ? '&#9888;&#65039;' : '&#8505;&#65039;') + '</span>'
    + '<span>' + i.text + '</span></div>').join('');
}

function _renderWeeklyCardsFromEvolution(we) {
  we = we || {};
  // A. Network Growth — gross new connections vs net growth are DIFFERENT
  // numbers on purpose: gross can exceed net when some prior connections
  // disappear, are removed, or exports change between snapshots.
  const ng = we.network_growth || {};
  const grossNew = parseFloat(ng.new_connections) || 0;
  const netGrowth = parseFloat(ng.net_growth) || 0;
  const churnEstimate = Math.max(0, grossNew - netGrowth);
  const ngEl = document.getElementById('weekly-network-growth');
  if (ngEl) ngEl.innerHTML = [
    makeCard('Previous Connections', ng.previous_connections || 0),
    makeCard('Current Connections',  ng.current_connections  || 0),
    makeKpiCard('net_growth', 'Net Growth', (netGrowth >= 0 ? '+' : '') + netGrowth, 'total connections, previous vs current — click for movers', netGrowth >= 0 ? 'good' : 'bad', 'applyWeeklyKpiFilter'),
    makeKpiCard('gross_new', 'Gross New Connections', grossNew, 'newly identity-matched this period — click to view', 'good', 'applyWeeklyKpiFilter'),
    makeCard('Removed/Lost/Changed Estimate', churnEstimate, 'gross new minus net growth'),
  ].join('');
  const ngNoteEl = document.getElementById('weekly-network-growth-note');
  if (ngNoteEl) ngNoteEl.textContent = grossNew > netGrowth
    ? 'Gross new connections can be higher than net growth when some prior connections disappear, are removed, or exports change.'
    : '';

  // B. Strategic Growth
  const sg = we.strategic_growth || {};
  const sgEl = document.getElementById('weekly-strategic-growth');
  if (sgEl) sgEl.innerHTML = [
    makeKpiCard('new_recruiters', 'New Recruiters', sg.new_recruiters || 0, _deltaSub(sg.new_recruiters).text, '', 'applyWeeklyKpiFilter'),
    makeKpiCard('new_ta', 'New Talent Acquisition', sg.new_talent_acquisition || 0, _deltaSub(sg.new_talent_acquisition).text, '', 'applyWeeklyKpiFilter'),
    makeKpiCard('new_hm', 'New Hiring Managers', sg.new_hiring_managers || 0, _deltaSub(sg.new_hiring_managers).text, '', 'applyWeeklyKpiFilter'),
    makeKpiCard('new_data_leaders', 'New Data Leaders', sg.new_data_leaders || 0, _deltaSub(sg.new_data_leaders).text, '', 'applyWeeklyKpiFilter'),
  ].join('');

  // C. Market / Opportunity Movement
  const mm = we.market_movement || {};
  const mmEl = document.getElementById('weekly-market-movement');
  if (mmEl) mmEl.innerHTML = [
    makeKpiCard('latam_usd_delta', 'LATAM/USD Δ', mm.latam_usd_delta || 0, _deltaSub(mm.latam_usd_delta).text, _deltaSub(mm.latam_usd_delta).cls, 'applyWeeklyKpiFilter'),
    makeKpiCard('us_canada_delta', 'US/Canada Nearshore Δ', mm.us_canada_nearshore_delta || 0, _deltaSub(mm.us_canada_nearshore_delta).text, _deltaSub(mm.us_canada_nearshore_delta).cls, 'applyWeeklyKpiFilter'),
    makeKpiCard('spain_eu_delta', 'Spain/EU Δ', mm.spain_eu_delta || 0, _deltaSub(mm.spain_eu_delta).text, _deltaSub(mm.spain_eu_delta).cls, 'applyWeeklyKpiFilter'),
    makeKpiCard('global_opp_delta', 'Global Opportunity Δ', mm.global_opportunity_delta || 0, _deltaSub(mm.global_opportunity_delta).text, _deltaSub(mm.global_opportunity_delta).cls, 'applyWeeklyKpiFilter'),
    makeKpiCard('needs_mapping_delta', 'Needs Mapping Δ', mm.needs_mapping_delta || 0, _deltaSub(mm.needs_mapping_delta, false).text, _deltaSub(mm.needs_mapping_delta, false).cls, 'applyWeeklyKpiFilter'),
  ].join('');

  // D. Lead Pipeline Movement
  const lp = we.lead_pipeline_movement || {};
  const lpEl = document.getElementById('weekly-lead-pipeline');
  if (lpEl) lpEl.innerHTML = [
    makeKpiCard('needs_response_delta', 'Needs My Response Δ', lp.needs_my_response_delta ?? 0, _deltaSub(lp.needs_my_response_delta, false).text, _deltaSub(lp.needs_my_response_delta, false).cls, 'applyWeeklyKpiFilter'),
    makeKpiCard('hot_leads_delta', 'Hot Leads Δ', lp.hot_leads_delta ?? 0, _deltaSub(lp.hot_leads_delta).text, _deltaSub(lp.hot_leads_delta).cls, 'applyWeeklyKpiFilter'),
    makeKpiCard('warm_leads_delta', 'Warm Leads Δ', lp.warm_leads_delta ?? 0, _deltaSub(lp.warm_leads_delta).text, _deltaSub(lp.warm_leads_delta).cls, 'applyWeeklyKpiFilter'),
    makeKpiCard('follow_ups_due_delta', 'Follow-ups Due Δ', lp.follow_ups_due_delta ?? 0, _deltaSub(lp.follow_ups_due_delta, false).text, _deltaSub(lp.follow_ups_due_delta, false).cls, 'applyWeeklyKpiFilter'),
    makeKpiCard('interview_pipeline_delta', 'Interview Pipeline Δ', lp.interview_pipeline_delta ?? '—', _deltaSub(lp.interview_pipeline_delta).text, _deltaSub(lp.interview_pipeline_delta).cls, 'applyWeeklyKpiFilter'),
  ].join('');

  // Untapped Network Movement (Part 22) — gracefully degrades on first run
  const um = we.untapped_network_movement || {};
  const umEl = document.getElementById('weekly-untapped-movement');
  const umNote = document.getElementById('weekly-untapped-note');
  if (umEl) {
    if (!um.available) {
      umEl.innerHTML = '';
      if (umNote) { umNote.style.display = 'none'; }
    } else if (!um.tracked_since_last_snapshot) {
      umEl.innerHTML = [
        makeCard('Never Contacted — Confirmed', um.current_never_contacted_confirmed || 0, 'baseline established this snapshot'),
        makeCard('High-Value Untapped', um.current_high_value_untapped || 0, 'baseline established this snapshot', 'good'),
      ].join('');
      if (umNote) { umNote.style.display = ''; umNote.textContent = um.note || ''; }
    } else {
      umEl.innerHTML = [
        makeKpiCard('never_contacted_delta', 'Never Contacted — Confirmed Δ', um.never_contacted_confirmed_delta ?? 0, _deltaSub(um.never_contacted_confirmed_delta, false).text, _deltaSub(um.never_contacted_confirmed_delta, false).cls, 'applyWeeklyKpiFilter'),
        makeKpiCard('high_value_untapped_delta', 'High-Value Untapped Δ', um.high_value_untapped_delta ?? 0, _deltaSub(um.high_value_untapped_delta).text, _deltaSub(um.high_value_untapped_delta).cls, 'applyWeeklyKpiFilter'),
        makeKpiCard('untapped_recruiters_delta', 'Untapped Recruiters Δ', um.untapped_recruiters_delta ?? 0, _deltaSub(um.untapped_recruiters_delta).text, _deltaSub(um.untapped_recruiters_delta).cls, 'applyWeeklyKpiFilter'),
        makeKpiCard('untapped_hm_delta', 'Untapped Hiring Managers Δ', um.untapped_hiring_managers_delta ?? 0, _deltaSub(um.untapped_hiring_managers_delta).text, _deltaSub(um.untapped_hiring_managers_delta).cls, 'applyWeeklyKpiFilter'),
        makeKpiCard('activated_this_week', 'Activated This Week', um.untapped_contacts_activated_this_week_estimate ?? 0, 'click to see who — identity-level match', 'good', 'applyWeeklyKpiFilter'),
      ].join('');
      if (umNote) { umNote.style.display = ''; umNote.textContent = 'Click a card to see the exact people behind it — matched by profile URL/name/company from weekly snapshot comparison.'; }
    }
  }

  // E. New High-Value Connections (sanitized — name/company/persona/bucket/score/public URL only)
  const tbody = document.getElementById('weekly-new-contacts-tbody');
  if (tbody) {
    const rows = we.new_high_value_connections || [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">No new connections this week.</td></tr>';
    } else {
      tbody.innerHTML = rows.map(r => {
        const url = r.url || '';
        const score = parseInt(r.priority_score) || 0;
        const sCls = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
        return '<tr>'
          + '<td style="white-space:nowrap">' + ((r.first_name||'') + ' ' + (r.last_name||'')).trim() + '</td>'
          + '<td style="white-space:nowrap">' + (r.company||'—') + '</td>'
          + '<td style="white-space:nowrap">' + (r.persona||'—') + '</td>'
          + '<td>' + marketBadge(r.opportunity_bucket||'UNKNOWN') + '</td>'
          + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
          + '<td>' + (url ? '<a href="' + url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
          + '</tr>';
      }).join('');
    }
  }

  // F. Weekly Diagnosis
  const diagEl = document.getElementById('weekly-diagnosis-card');
  const diag = we.diagnosis || {};
  if (diagEl) diagEl.innerHTML = [
    '<p><strong>Strongest growth area:</strong> ' + (diag.strongest_growth_area || '—') + '</p>',
    '<p><strong>Weakest strategic area:</strong> ' + (diag.weakest_strategic_area || '—') + '</p>',
    '<p><strong>90/10 allocation check:</strong> ' + (diag.allocation_90_10_note || '—') + '</p>',
    '<p><strong>Highest-value next action:</strong> ' + (diag.highest_value_next_action || '—') + '</p>',
  ].join('');
}

function renderWeekly() {
  const we = D.weekly_evolution || {};
  const noData = document.getElementById('weekly-no-data');
  const mainContent = document.getElementById('weekly-main-content');

  if (!we || !we.network_growth) {
    if (noData) noData.style.display = '';
    if (mainContent) mainContent.style.display = 'none';
    return;
  }
  if (noData) noData.style.display = 'none';
  if (mainContent) mainContent.style.display = '';

  selectedWeeklyWeek = 'latest';
  _populateWeeklyWeekSelector();
  const sel = document.getElementById('weekly-week-selector');
  if (sel) sel.value = 'latest';
  window.applyWeeklyWeekFilter('latest');

  // History table + trend chart always show every recorded week, regardless
  // of the selector above — that is their purpose (Part 3).
  renderWeeklyHistoryTable();
  renderWeeklyTrendChart();
}

// ── PAGE: Action Plan — Plan Progress tab ─────────────────────────────────────
const PROGRESS_STATUS_COLOR = {
  ON_TRACK: '#22c55e', AHEAD: '#22c55e', ON_STRATEGY: '#22c55e', NORMAL: '#22c55e',
  BELOW_TARGET: '#f59e0b', REBALANCE_NEEDED: '#f59e0b',
  TOO_MUCH_EU: '#f59e0b', TOO_LITTLE_EU: '#f59e0b', TOO_MUCH_UNCLASSIFIED: '#f59e0b',
  CLASSIFICATION_BACKLOG_INCREASED: '#f59e0b',
  BASELINE_ONLY: '#8b949e', NO_BASELINE: '#8b949e',
};

function _progressStatusClass(status) {
  const c = PROGRESS_STATUS_COLOR[status] || '#8b949e';
  return 'color:' + c + ';font-weight:700';
}

function _statusLabel(status) {
  return '<span style="' + _progressStatusClass(status) + '">' + status.replace(/_/g, ' ') + '</span>';
}

// Cards with weekly-pace data get a full breakdown (period actual / weekly
// pace / weekly target / status) — never collapse this to "168 vs 40-60",
// since that raw comparison is what caused the original AHEAD/BELOW_TARGET
// misreporting when snapshots are more than a week apart.
function _paceCard(card) {
  const lines = [];
  if (card.period_actual !== null && card.period_actual !== undefined) {
    lines.push('Period actual: <strong>' + card.period_actual + '</strong>');
  }
  if (card.weekly_pace_actual !== null && card.weekly_pace_actual !== undefined) {
    lines.push('Weekly pace: <strong>' + card.weekly_pace_actual + '/wk</strong>');
  } else {
    lines.push('Weekly pace: —');
  }
  if (card.weekly_target_min !== null && card.weekly_target_min !== undefined) {
    lines.push('Weekly target: ' + card.weekly_target_min + '-' + card.weekly_target_max + '/wk');
  }
  if (card.status) lines.push('Status: ' + _statusLabel(card.status));
  return '<div class="card"><div class="card-title">' + card.title + '</div>'
    + '<div class="card-sub" style="line-height:1.7;margin-top:.4rem">' + lines.join('<br>') + '</div>'
    + '</div>';
}

function _progressCard(card) {
  if (card.weekly_pace_actual !== undefined || card.period_actual !== undefined) {
    return _paceCard(card);
  }
  // Custom sub text and the colored status badge are independent pieces of
  // information — show both when both are present instead of one silently
  // replacing the other.
  const subParts = [];
  if (card.sub) subParts.push(card.sub);
  if (card.status) subParts.push(_statusLabel(card.status));
  if (!subParts.length && card.target) subParts.push('target: ' + card.target);
  const value = (card.value === null || card.value === undefined) ? '—' : card.value;
  return makeCard(card.title, value, subParts.join('<br>'));
}

// ── Action Plan — Executive Summary + Mini Panel (top of page) ──────────────
// Consolidates what used to be 3 always-open alert blocks into a compact,
// data-driven summary built from the same action_plan_progress numbers the
// Plan Progress tab uses — nothing here is fabricated, it's a different view
// of fields already computed by src/action_plan_progress.py.
function goToNavPage(page) {
  const el = document.querySelector('.nav-item[data-page="' + page + '"]');
  if (el) el.click();
}
function goToPlanTab(tab) {
  const btn = document.querySelector('#page-plan .tab-btn[data-tab="' + tab + '"]');
  if (btn) btn.click();
}

function _mainBottleneck(ap) {
  const rules = ap.diagnosis || [];
  const byRule = {};
  rules.forEach(r => { byRule[r.rule] = r.message; });
  const priority = [
    'needs_my_response_high', 'primary_share_below_80', 'europe_share_above_20',
    'classification_backlog_increased', 'europe_share_below_5',
    'new_connections_below_target', 'high_value_untapped_high',
  ];
  for (const key of priority) {
    if (byRule[key]) return byRule[key];
  }
  return (rules[0] && rules[0].message) || 'No major bottleneck — plan execution is on track.';
}

function renderPlanExecSummary() {
  const ap = (D && D.action_plan_progress) || {};
  const noData = document.getElementById('plan-exec-no-data');
  const mainEl = document.getElementById('plan-exec-main');
  if (!ap.available) {
    if (noData) noData.style.display = '';
    if (mainEl) mainEl.style.display = 'none';
    return;
  }
  if (noData) noData.style.display = 'none';
  if (mainEl) mainEl.style.display = '';

  const summary = ap.summary || {};
  const wm = ap.weekly_metrics || {};
  const network = wm.network_growth || {};
  const strategic = wm.strategic_growth || {};
  const leads = wm.lead_reactivation || {};
  const untapped = wm.untapped_network || {};
  const quality = wm.data_quality || {};

  // Block 1: Focus This Week
  const focusEl = document.getElementById('plan-exec-summary');

  const priorities = [];
  if ((leads.needs_my_response_current || 0) > 0) {
    priorities.push('Reply to ' + leads.needs_my_response_current + ' pending Needs My Response contacts');
  }
  if ((untapped.high_value_untapped_current || 0) > 0) {
    priorities.push('Activate ' + untapped.high_value_untapped_current + ' high-value Untapped contacts');
  }
  priorities.push('Add ' + (network.new_connections_target_min || 0) + '-' + (network.new_connections_target_max || 0)
    + ' new recruiter/TA connections');
  if (strategic.classification_risk_status === 'CLASSIFICATION_BACKLOG_INCREASED') {
    priorities.push('Map top unresolved companies (backlog grew by ' + (quality.needs_company_mapping_delta || 0) + ')');
  }

  const cards = [
    {
      title: 'Focus This Week',
      value: summary.primary_focus || '—',
      sub: '80-90% LATAM/USD · 10-20% Spain/EU exploratory',
    },
    {
      title: 'Main Bottleneck',
      value: _mainBottleneck(ap),
    },
    {
      title: 'Recommended Priorities',
      value: priorities.map((p, i) => (i + 1) + ') ' + p).join('<br>'),
    },
    {
      title: 'Weekly Targets Snapshot',
      value: [
        'New connections: ' + (network.new_connections_target_min || 0) + '-' + (network.new_connections_target_max || 0) + '/wk',
        'Reactivation: ' + (leads.reactivation_weekly_target_min ?? '—') + '-' + (leads.reactivation_weekly_target_max ?? '—') + '/wk',
        'Untapped outreach: ' + (untapped.untapped_outreach_weekly_target_min ?? '—') + '-' + (untapped.untapped_outreach_weekly_target_max ?? '—') + '/wk',
        'Europe exploratory: ' + (strategic.europe_exploratory_weekly_target_min ?? '—') + '-' + (strategic.europe_exploratory_weekly_target_max ?? '—') + '/wk',
      ].join('<br>'),
    },
  ];
  if (focusEl) focusEl.innerHTML = cards.map(c => {
    return '<div class="card"><div class="card-title">' + c.title + '</div>'
      + '<div class="card-sub" style="line-height:1.6;margin-top:.3rem;font-size:0.82rem">' + c.value + '</div>'
      + (c.sub ? '<div class="card-sub" style="margin-top:.3rem;color:var(--text-muted)">' + c.sub + '</div>' : '')
      + '</div>';
  }).join('');

  // Mini panel: This Week Progress / Targets / Diagnosis + cross-links
  const panelEl = document.getElementById('plan-mini-panel');
  if (panelEl) {
    const diagList = (ap.diagnosis || []).map(r => '<li>' + r.message + '</li>').join('');
    panelEl.innerHTML =
      '<div class="section-label" style="margin-top:0">This Week</div>'
      + '<p><strong>Progress:</strong> ' + (summary.plan_status || '—')
      + ' — gross ' + (network.gross_new_connections ?? '—') + ' new / net ' + (network.net_connection_growth ?? '—') + ' growth'
      + ' (week ' + (summary.week_index || '?') + ' of the plan cycle, snapshot #' + (summary.snapshot_count || '?') + ')</p>'
      + '<p><strong>Diagnosis:</strong></p>'
      + '<ul style="margin:.2rem 0 .8rem 1.2rem;line-height:1.8">' + diagList + '</ul>'
      + '<div style="display:flex;gap:.5rem;flex-wrap:wrap">'
      + '<button type="button" class="btn-ghost" onclick="goToNavPage(\'weekly\')">&#128197; Weekly Evolution</button>'
      + '<button type="button" class="btn-ghost" onclick="goToPlanTab(\'plan-progress\')">&#127919; Plan Progress</button>'
      + '</div>';
  }
}

function renderPlanProgress() {
  const ap = (D && D.action_plan_progress) || {};
  const noData = document.getElementById('progress-no-data');
  const mainContent = document.getElementById('progress-main-content');

  if (!ap.available) {
    if (noData) noData.style.display = '';
    if (mainContent) mainContent.style.display = 'none';
    return;
  }
  if (noData) noData.style.display = 'none';
  if (mainContent) mainContent.style.display = '';

  const summary = ap.summary || {};
  const charts = ap.charts || {};
  const baselineAvailable = !!summary.baseline_available;

  const period = summary.period || {};

  // Status banner
  const banner = document.getElementById('progress-status-banner');
  if (banner) {
    banner.className = 'alert ' + (baselineAvailable
      ? (summary.plan_status === 'ON_TRACK' ? 'alert-good' : 'alert-warn')
      : 'alert-info');
    const periodText = (period.period_days !== null && period.period_days !== undefined)
      ? ' Comparison period: ' + period.period_days + ' days (' + period.period_weeks + ' weeks).'
      : '';
    banner.innerHTML = '<span class="alert-icon">&#127919;</span><span><strong>Plan Status: '
      + (summary.plan_status || '—') + '</strong> — Week ' + (summary.week_index || '?')
      + ' of the plan cycle (snapshot #' + (summary.snapshot_count || 1) + '). Primary focus: '
      + (summary.primary_focus || '—') + '.' + periodText + '</span>';
  }
  const baselineNote = document.getElementById('progress-baseline-note');
  if (baselineNote) {
    if (!baselineAvailable) {
      baselineNote.style.display = '';
      baselineNote.innerHTML = '<div class="alert alert-info"><span class="alert-icon">&#9432;</span>'
        + '<span><strong>Baseline mode:</strong> this is the first tracked snapshot — week-over-week '
        + 'progress will be available starting next Sunday\'s refresh.</span></div>';
    } else if (period.is_multi_week) {
      baselineNote.style.display = '';
      baselineNote.innerHTML = '<div class="alert alert-warn"><span class="alert-icon">&#9888;&#65039;</span>'
        + '<span><strong>Multi-week baseline period:</strong> ' + (summary.period_warning || period.note || '')
        + '</span></div>';
    } else {
      baselineNote.style.display = 'none';
    }
  }

  // 1. Executive Progress Summary
  const cardsEl = document.getElementById('progress-summary-cards');
  if (cardsEl) cardsEl.innerHTML = (ap.progress_cards || []).map(_progressCard).join('');

  // 2. Weekly Target vs Actual — bars show WEEKLY PACE (normalized by
  // period_weeks), never the raw multi-week period total. The period total
  // is still surfaced as a tooltip line so nothing is hidden.
  const targetData = charts.weekly_target_vs_actual || [];
  const targetMsgEl = document.getElementById('progress-target-baseline-msg');
  if (!baselineAvailable || !targetData.length) {
    destroyChart('chart-progress-target');
    if (targetMsgEl) { targetMsgEl.style.display = ''; targetMsgEl.textContent = 'No baseline yet — targets will compare against actuals starting next week.'; }
  } else {
    if (targetMsgEl) targetMsgEl.style.display = 'none';
    groupedBarChart('chart-progress-target',
      targetData.map(r => r.label),
      [
        { label: 'Weekly Target Min', data: targetData.map(r => r.target_min || 0), color: '#3b82f640' },
        { label: 'Weekly Target Max', data: targetData.map(r => r.target_max || 0), color: '#3b82f680' },
        { label: 'Weekly Pace (Actual)', data: targetData.map(r => r.actual || 0), color: '#22c55e',
          periodActuals: targetData.map(r => r.period_actual) },
      ]
    );
  }

  // 3. 90/10 Strategy Mix
  const mixData = charts.strategy_mix || [];
  const mixMsgEl = document.getElementById('progress-mix-baseline-msg');
  const mixValues = mixData.map(r => r.value);
  if (!baselineAvailable || mixValues.some(v => v === null || v === undefined)) {
    destroyChart('chart-progress-mix');
    if (mixMsgEl) { mixMsgEl.style.display = ''; mixMsgEl.textContent = 'No new connections classified yet this snapshot to compute the mix.'; }
  } else {
    if (mixMsgEl) mixMsgEl.style.display = 'none';
    barChart('chart-progress-mix',
      mixData.map(r => r.label),
      mixData.map(r => Math.round((r.value || 0) * 100)),
      ['#f59e0b', '#f59e0b80', '#a78bfa', '#a78bfa80'],
      { horizontal: true }
    );
  }

  // Persona growth — bars show weekly pace, tooltip shows period total
  const personaData = charts.persona_growth || [];
  barChart('chart-progress-persona', personaData.map(r => r.label), personaData.map(r => r.value || 0), '#3b82f6',
    { horizontal: true, periodActuals: personaData.map(r => r.period_actual) });

  // Lead pipeline movement
  const leadData = charts.lead_pipeline_movement || [];
  barChart('chart-progress-leads', leadData.map(r => r.label), leadData.map(r => r.value || 0),
    leadData.map(r => r.label === 'Needs My Response' ? '#ef4444' : '#3b82f6'));

  // Untapped movement
  const untappedData = charts.untapped_movement || [];
  barChart('chart-progress-untapped', untappedData.map(r => r.label), untappedData.map(r => r.value || 0), '#14b8a6');

  // Diagnosis
  const diagEl = document.getElementById('progress-diagnosis-card');
  if (diagEl) {
    const rules = ap.diagnosis || [];
    diagEl.innerHTML = rules.length
      ? '<ul style="margin:0;padding-left:1.2rem;line-height:1.9">' + rules.map(r => '<li>' + r.message + '</li>').join('') + '</ul>'
      : '<p>No diagnosis available yet.</p>';
  }

  // Next Week Recommendation
  const recEl = document.getElementById('progress-recommendation-card');
  if (recEl) {
    const recs = ap.next_week_recommendations || [];
    recEl.innerHTML = recs.length ? recs.map(r => '<p>' + r + '</p>').join('') : '<p>—</p>';
  }

  // 8. Manual Activity This Week — measured where available, zero shown as
  // zero (not hidden/missing) when a log row exists; a distinct message
  // when no row exists for this week at all (no fabricated activity).
  const manual = (ap.weekly_metrics && ap.weekly_metrics.manual_activity) || {};
  const manualNoteEl = document.getElementById('progress-manual-note');
  const manualCardsEl = document.getElementById('progress-manual-cards');
  if (manual.manual_activity_available) {
    if (manualNoteEl) manualNoteEl.textContent = 'From data/manual/weekly_action_log.csv (week ending ' + (manual.week_end || '—') + ').';
    if (manualCardsEl) manualCardsEl.innerHTML = [
      makeCard('Comments Done', manual.comments_done ?? 0),
      makeCard('Posts Done', manual.posts_done ?? 0),
      makeCard('Career Sites Submitted', manual.career_sites_submitted ?? 0),
      makeCard('Manual DMs Sent', manual.manual_dms_sent ?? 0),
      makeCard('Manual Follow-ups Done', manual.manual_followups_done ?? 0),
      makeCard('Companies Mapped', manual.companies_mapped ?? 'Not logged', manual.companies_mapped == null ? 'column not in log yet' : ''),
    ].join('');
  } else {
    if (manualNoteEl) manualNoteEl.textContent = 'No manual activity log entry for this week — measured where available, nothing fabricated.';
    if (manualCardsEl) manualCardsEl.innerHTML = '';
  }

  // 9. Week-over-Week Comparison — only ever plots weeks that actually have
  // a recorded snapshot (ap.charts.weekly_comparison is built server-side
  // from the persistent history log, never fabricated for missing weeks).
  const wcData = charts.weekly_comparison || [];
  const wcNoteEl = document.getElementById('progress-weekly-comparison-note');
  const wcMsgEl = document.getElementById('progress-weekly-comparison-msg');
  if (wcData.length < 2) {
    destroyChart('chart-progress-weekly-comparison');
    if (wcMsgEl) {
      wcMsgEl.style.display = '';
      wcMsgEl.textContent = wcData.length === 1
        ? 'Only one recorded week so far (' + wcData[0].week_label + ') — the comparison chart needs at least two weekly snapshots.'
        : 'No recorded weeks yet.';
    }
    if (wcNoteEl) wcNoteEl.textContent = '';
  } else {
    if (wcMsgEl) wcMsgEl.style.display = 'none';
    if (wcNoteEl) wcNoteEl.textContent = 'Showing ' + wcData.length + ' recorded week(s) of ' + MAX_WEEK_TABS_JS + ' tracked.';
    groupedBarChart('chart-progress-weekly-comparison',
      wcData.map(r => r.week_label),
      [
        { label: 'Gross New', data: wcData.map(r => r.gross_new_connections || 0), color: '#3b82f6' },
        { label: 'Net Growth', data: wcData.map(r => r.net_connection_growth || 0), color: '#22c55e' },
        { label: 'Recruiters', data: wcData.map(r => r.new_recruiters || 0), color: '#f59e0b' },
        { label: 'Needs Response', data: wcData.map(r => r.needs_my_response_current || 0), color: '#ef4444' },
        { label: 'Untapped Activated', data: wcData.map(r => r.untapped_activated_this_week || 0), color: '#14b8a6' },
        { label: 'Needs Mapping Δ', data: wcData.map(r => r.needs_company_mapping_delta || 0), color: '#8b5cf6' },
      ]
    );
  }
}
const MAX_WEEK_TABS_JS = 4;

// ── Action Plan — Week 1-4 tabs: real weekly-history panel ──────────────────
// Injects a "resumo / targets / realizado / diagnóstico / próximas ações"
// panel at the top of each existing plan-week1..plan-week4 tab, sourced from
// action_plan_progress's by_week history. Weeks without a recorded snapshot
// yet show an honest "baseline not available" message — never fabricated.
function _weekTargetLine(label, min, max, unit) {
  if (min === null || min === undefined) return null;
  return label + ': ' + min + '-' + max + (unit || '/wk');
}

function renderWeekHistoryPanels() {
  const byWeek = ((D && D.action_plan_progress) || {}).by_week || {};
  for (let n = 1; n <= MAX_WEEK_TABS_JS; n++) {
    const panel = document.getElementById('week-' + n + '-history-panel');
    if (!panel) continue;
    const w = byWeek['week_' + n];
    if (!w || !w.available) {
      panel.innerHTML = '<div class="alert alert-info" style="margin:0"><span class="alert-icon">&#8505;&#65039;</span>'
        + '<span><strong>Baseline not available for Week ' + n + '.</strong> No weekly snapshot has reached this '
        + 'point in the plan cycle yet — run the weekly refresh to populate real progress here. Nothing is fabricated.</span></div>';
      continue;
    }

    const targets = [
      _weekTargetLine('New connections', w.new_connections_target_min, w.new_connections_target_max),
      _weekTargetLine('Reactivation', w.reactivation_target_min, w.reactivation_target_max),
      _weekTargetLine('Untapped outreach', w.untapped_outreach_target_min, w.untapped_outreach_target_max),
      _weekTargetLine('Europe exploratory', w.europe_exploratory_target_min, w.europe_exploratory_target_max),
    ].filter(Boolean).join(' &middot; ');

    const diagItems = String(w.diagnosis_summary || '').split('|').map(s => s.trim()).filter(Boolean);
    const statusColor = PROGRESS_STATUS_COLOR[w.overall_status] || '#8b949e';

    panel.innerHTML =
      '<div class="section-label" style="margin-top:0">1. Resumo da Semana</div>'
      + '<p style="font-size:0.85rem">' + (w.previous_snapshot_date || '—') + ' &rarr; ' + (w.current_snapshot_date || '—')
      + ' (' + (w.period_days ?? '—') + ' days) — focus: <strong>' + (w.primary_focus || '—') + '</strong></p>'
      + '<div class="section-label">2. Targets da Semana</div>'
      + '<p style="font-size:0.85rem">' + (targets || '—') + '</p>'
      + '<div class="section-label">3. Realizado da Semana</div>'
      + '<div class="metrics-grid">'
      + makeCard('Gross New Connections', w.gross_new_connections ?? '—')
      + makeCard('Net Growth', w.net_connection_growth ?? '—')
      + makeCard('Recruiters Added', w.new_recruiters ?? '—')
      + makeCard('TA Added', w.new_talent_acquisition ?? '—')
      + makeCard('Hiring Managers Added', w.new_hiring_managers ?? '—')
      + makeCard('New Conversations', w.new_conversations_started ?? '—')
      + makeCard('Needs My Response Δ', w.needs_my_response_delta ?? '—')
      + makeCard('Hot Reactivation Δ', w.hot_reactivation_delta ?? '—')
      + makeCard('Warm Reactivation Δ', w.warm_reactivation_delta ?? '—')
      + makeCard('Untapped Activated', w.untapped_activated_this_week ?? '—')
      + makeCard('Needs Mapping Δ', w.needs_company_mapping_delta ?? '—')
      + makeCard('90/10 Mix', w.strategy_mix_status || '—')
      + '</div>'
      + '<div class="section-label">4. Diagnóstico da Semana</div>'
      + '<p style="font-size:0.85rem"><span style="color:' + statusColor + ';font-weight:700">' + (w.overall_status || '—') + '</span></p>'
      + (diagItems.length ? '<ul style="margin:.2rem 0 .6rem 1.2rem;font-size:0.85rem;line-height:1.8">'
          + diagItems.map(d => '<li>' + d + '</li>').join('') + '</ul>' : '')
      + '<div class="section-label">5. Próximas Ações</div>'
      + '<p style="font-size:0.85rem">' + (w.recommendation || '—') + '</p>';
  }
}

// ── PAGE 11: Untapped Network ──────────────────────────────────────────────
let filteredUntapped = [];
let untappedPage = 1;
const UNTAPPED_PAGE_SIZE = 25;
let activeUntappedKpi = null;

const HISTORY_STATUS_LABEL = {
  NEVER_CONTACTED_CONFIRMED: 'Never Contacted', LIKELY_NEVER_CONTACTED: 'Likely Never Contacted',
  AMBIGUOUS_REVIEW: 'Ambiguous — Review', HAS_CONVERSATION: 'Has Conversation',
};

function untappedBadge(status) {
  const colors = { NEVER_CONTACTED_CONFIRMED: '#f59e0b', LIKELY_NEVER_CONTACTED: '#fbbf24', AMBIGUOUS_REVIEW: '#9ca3af' };
  const c = colors[status] || '#6b7280';
  return '<span class="urgency-badge" style="background:' + c + '20;color:' + c + ';border:1px solid ' + c + '">'
    + (HISTORY_STATUS_LABEL[status] || status || '—') + '</span>';
}

function renderUntapped() {
  const un = D.untapped_network || {};
  const noData = document.getElementById('untapped-no-data');
  const mainContent = document.getElementById('untapped-main-content');

  if (!un.available) {
    if (noData) noData.style.display = '';
    if (mainContent) mainContent.style.display = 'none';
    return;
  }
  if (noData) noData.style.display = 'none';
  if (mainContent) mainContent.style.display = '';

  const s = un.summary || {};

  // Contact History Matching summary
  const sumEl = document.getElementById('untapped-summary');
  if (sumEl) sumEl.innerHTML = [
    makeCard('Total Connections', s.total_connections || 0),
    makeCard('Has Conversation', s.has_conversation || 0, 'Lead Reactivation territory'),
    makeKpiCard('never_confirmed', 'Never Contacted — Confirmed', s.never_contacted_confirmed || 0, 'click to filter', 'warn', 'applyUntappedKpiFilter'),
    makeKpiCard('likely_never',    'Likely Never Contacted',      s.likely_never_contacted     || 0, 'incomplete identity — click to filter', '', 'applyUntappedKpiFilter'),
    makeKpiCard('ambiguous',       'Ambiguous — Review',          s.ambiguous_review            || 0, 'conflicting candidates — click to filter', '', 'applyUntappedKpiFilter'),
  ].join('');

  // Untapped Opportunity KPI cards — all clickable
  const oppEl = document.getElementById('untapped-opportunity');
  if (oppEl) oppEl.innerHTML = [
    makeKpiCard('high_value',   'High-Value Untapped',       s.high_value_untapped       || 0, 'click to filter', 'good', 'applyUntappedKpiFilter'),
    makeKpiCard('recruiters',   'Recruiters Untapped',       s.recruiters_untapped       || 0, 'click to filter', '', 'applyUntappedKpiFilter'),
    makeKpiCard('ta',           'Talent Acquisition Untapped', s.ta_untapped              || 0, 'click to filter', '', 'applyUntappedKpiFilter'),
    makeKpiCard('hm',           'Hiring Managers Untapped',  s.hiring_managers_untapped   || 0, 'click to filter', '', 'applyUntappedKpiFilter'),
    makeKpiCard('data_leaders', 'Data Leaders Untapped',     s.data_leaders_untapped      || 0, 'click to filter', '', 'applyUntappedKpiFilter'),
    makeKpiCard('latam',        'LATAM/USD Untapped',        s.latam_usd_untapped         || 0, '90% primary focus — click to filter', 'good', 'applyUntappedKpiFilter'),
    makeKpiCard('nearshore',    'US/Nearshore Untapped',     s.us_nearshore_untapped      || 0, 'part of primary 90% — click to filter', '', 'applyUntappedKpiFilter'),
    makeKpiCard('spain_eu',     'Spain/EU Exploratory Untapped', s.spain_eu_untapped      || 0, '10% exploratory — click to filter', '', 'applyUntappedKpiFilter'),
    makeKpiCard('conn_90d',     'Connected >90 Days, Never Contacted', s.connected_90d_plus_never_contacted || 0, 'click to filter', 'warn', 'applyUntappedKpiFilter'),
    makeKpiCard('conn_180d',    'Connected >180 Days, Never Contacted', s.connected_180d_plus_never_contacted || 0, 'click to filter', 'warn', 'applyUntappedKpiFilter'),
    makeKpiCard('manual_enriched', 'Active/Manual Enriched Contacts', s.manual_enriched_contacts || 0, 'matched to data/manual/profile_enrichment.csv — click to filter', '', 'applyUntappedKpiFilter'),
  ].join('');

  // Activation Potential KPI cards (V10) — all clickable, computed client-side
  // from top_untapped_contacts since these are cross-cutting (persona +
  // connected-age + activation_category), not part of the base summary dict.
  const source0 = un.top_untapped_contacts || [];
  const activationCounts = {
    long_connected_recruiters: source0.filter(UNTAPPED_KPI_FILTERS.long_connected_recruiters.match).length,
    days_365_plus: source0.filter(UNTAPPED_KPI_FILTERS.days_365_plus.match).length,
    latam_intl_recruiters: source0.filter(UNTAPPED_KPI_FILTERS.latam_intl_recruiters.match).length,
    global_talent_partners: source0.filter(UNTAPPED_KPI_FILTERS.global_talent_partners.match).length,
    highest_activation: source0.filter(UNTAPPED_KPI_FILTERS.highest_activation.match).length,
    first_message_now: source0.filter(UNTAPPED_KPI_FILTERS.first_message_now.match).length,
  };
  const actEl = document.getElementById('untapped-activation-cards');
  if (actEl) actEl.innerHTML = [
    makeKpiCard('long_connected_recruiters', 'Long-Connected Recruiters Untapped', activationCounts.long_connected_recruiters,
      'connected 180+ days, recruiter/TA/sourcer/talent partner — click to filter', 'warn', 'applyUntappedKpiFilter'),
    makeKpiCard('days_365_plus', '365+ Days Never Contacted', activationCounts.days_365_plus,
      'click to filter', 'warn', 'applyUntappedKpiFilter'),
    makeKpiCard('latam_intl_recruiters', 'LATAM/International Recruiters Untapped', activationCounts.latam_intl_recruiters,
      'click to filter', 'good', 'applyUntappedKpiFilter'),
    makeKpiCard('global_talent_partners', 'Global Talent Partners Untapped', activationCounts.global_talent_partners,
      'click to filter', 'good', 'applyUntappedKpiFilter'),
    makeKpiCard('highest_activation', 'Highest Activation Potential', activationCounts.highest_activation,
      'activation score 80+ — click to filter', 'good', 'applyUntappedKpiFilter'),
    makeKpiCard('first_message_now', 'First Message Now', activationCounts.first_message_now,
      'first_message_priority = TODAY — click to filter', 'warn', 'applyUntappedKpiFilter'),
  ].join('');

  // Activation Pattern Learning (V10) — sanitized aggregate evidence
  const apl = un.activation_pattern_learning || {};
  const aplNoData = document.getElementById('untapped-activation-pattern-no-data');
  const aplContent = document.getElementById('untapped-activation-pattern-content');
  if (!apl.available) {
    if (aplNoData) aplNoData.style.display = '';
    if (aplContent) aplContent.style.display = 'none';
  } else {
    if (aplNoData) aplNoData.style.display = 'none';
    if (aplContent) aplContent.style.display = '';
    const aplSumEl = document.getElementById('untapped-activation-pattern-summary');
    if (aplSumEl) aplSumEl.innerHTML = [
      makeCard('Long-Connected Contacts Activated', apl.long_connected_contacted_all_time || 0, 'first messaged 90+ days after connecting'),
      makeCard('Activated This Week', apl.long_connected_contacted_this_week || 0, 'first messaged in the last 7 days', 'good'),
      makeCard('Replied', apl.long_connected_replied || 0),
      makeCard('Became Warm Lead', apl.long_connected_became_warm || 0, '', 'good'),
      makeCard('CV / Interview Requested', apl.long_connected_cv_or_interview_requested || 0),
      makeCard('Overall Conversion Rate', (apl.conversion_rate_overall_pct || 0) + '%', 'replied ÷ activated', 'good'),
    ].join('');

    const _aplRow = (r, keyLabel) => '<tr>'
      + '<td>' + (r[keyLabel] || '—').toString().replace(/_/g, ' ') + '</td>'
      + '<td style="text-align:center">' + (r.contacted || 0) + '</td>'
      + '<td style="text-align:center">' + (r.replied || 0) + '</td>'
      + '<td style="text-align:center">' + (r.became_warm || 0) + '</td>'
      + '<td style="text-align:center">' + (r.conversion_rate_pct || 0) + '%</td>'
      + '</tr>';

    const personaTbody = document.getElementById('activation-pattern-persona-tbody');
    if (personaTbody) {
      const rows = apl.by_persona || [];
      personaTbody.innerHTML = rows.length
        ? rows.map(r => _aplRow(r, 'persona')).join('')
        : '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No data yet.</td></tr>';
    }
    const ageTbody = document.getElementById('activation-pattern-age-tbody');
    if (ageTbody) {
      const rows = apl.by_connected_age_bucket || [];
      ageTbody.innerHTML = rows.length
        ? rows.map(r => _aplRow(r, 'connected_age_bucket')).join('')
        : '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No data yet.</td></tr>';
    }
    const bucketTbody = document.getElementById('activation-pattern-bucket-tbody');
    if (bucketTbody) {
      const rows = apl.by_opportunity_bucket || [];
      bucketTbody.innerHTML = rows.length
        ? rows.map(r => _aplRow(r, 'opportunity_bucket')).join('')
        : '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No data yet.</td></tr>';
    }
  }

  const noteEl = document.getElementById('untapped-location-note');
  if (noteEl) noteEl.textContent = (D.meta && D.meta.untapped_scoring_note) ||
    'LinkedIn export does not include location, but opportunity market can still be inferred from company, title, persona, language, manual enrichment, and message history. These are opportunity signals, not exact geography.';

  // This Week Queue
  const queueTbody = document.getElementById('untapped-queue-tbody');
  if (queueTbody) {
    const q = un.this_week_queue || [];
    if (!q.length) {
      queueTbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">No queue this week.</td></tr>';
    } else {
      queueTbody.innerHTML = q.map((r, i) => {
        const url = r.profile_url || '';
        const score = parseInt(r.untapped_outreach_score) || 0;
        const sCls = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
        return '<tr>'
          + '<td><strong>#' + (i + 1) + '</strong></td>'
          + '<td style="white-space:nowrap">' + (r.full_name || '—') + '</td>'
          + '<td style="white-space:nowrap">' + (r.company_clean || '—') + '</td>'
          + '<td style="white-space:nowrap">' + (r.persona || '—') + '</td>'
          + '<td style="font-size:0.75rem">' + (r.strategic_focus || '—').replace(/_/g, ' ') + '</td>'
          + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
          + '<td style="font-size:0.72rem">' + (r.recommended_first_action || '—').replace(/_/g, ' ') + '</td>'
          + '<td>' + (url ? '<a href="' + url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
          + '</tr>';
      }).join('');
    }
  }

  // Populate dynamic filter dropdowns
  const source = un.top_untapped_contacts || [];
  const catSel = document.getElementById('untapped-category-filter');
  if (catSel && catSel.options.length <= 1) {
    [...new Set(source.map(c => c.untapped_category).filter(Boolean))].sort().forEach(v => {
      const o = document.createElement('option'); o.value = v; o.textContent = v.replace(/_/g, ' '); catSel.appendChild(o);
    });
  }
  const perSel = document.getElementById('untapped-persona-filter');
  if (perSel && perSel.options.length <= 1) {
    [...new Set(source.map(c => c.persona).filter(Boolean))].sort().forEach(v => {
      const o = document.createElement('option'); o.value = v; o.textContent = v; perSel.appendChild(o);
    });
  }
  const bucketSel = document.getElementById('untapped-bucket-filter');
  if (bucketSel && bucketSel.options.length <= 1) {
    [...new Set(source.map(c => c.opportunity_bucket).filter(Boolean))].sort().forEach(v => {
      const o = document.createElement('option'); o.value = v; o.textContent = v; bucketSel.appendChild(o);
    });
  }
  const senSel = document.getElementById('untapped-seniority-filter');
  if (senSel && senSel.options.length <= 1) {
    [...new Set(source.map(c => c.seniority).filter(Boolean))].sort().forEach(v => {
      const o = document.createElement('option'); o.value = v; o.textContent = v; senSel.appendChild(o);
    });
  }
  const actCatSel = document.getElementById('untapped-activation-category-filter');
  if (actCatSel && actCatSel.options.length <= 1) {
    [...new Set(source.map(c => c.activation_category).filter(Boolean))].sort().forEach(v => {
      const o = document.createElement('option'); o.value = v; o.textContent = v.replace(/_/g, ' '); actCatSel.appendChild(o);
    });
  }

  filteredUntapped = source;
  renderUntappedTable();
}

const UNTAPPED_KPI_FILTERS = {
  never_confirmed: { label: 'Never Contacted — Confirmed', match: c => c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  likely_never:     { label: 'Likely Never Contacted',      match: c => c.contact_history_status === 'LIKELY_NEVER_CONTACTED' },
  ambiguous:        { label: 'Ambiguous — Review',          match: c => c.contact_history_status === 'AMBIGUOUS_REVIEW' },
  high_value:       { label: 'High-Value Untapped',         match: c => c.untapped_category === 'HIGH_VALUE_UNTAPPED' },
  recruiters:       { label: 'Recruiters Untapped',         match: c => c.persona === 'Recruiter' && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  ta:               { label: 'Talent Acquisition Untapped', match: c => c.persona === 'Talent Acquisition' && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  hm:               { label: 'Hiring Managers Untapped',    match: c => ['Hiring Manager', 'Engineering Manager'].includes(c.persona) && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  data_leaders:     { label: 'Data Leaders Untapped',       match: c => ['Data Engineering Manager', 'Head of Data', 'Director'].includes(c.persona) && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  latam:            { label: 'LATAM/USD Untapped',
    match: c => c.strategic_focus === 'PRIMARY_LATAM_USD' && !['US_CANADA_CONFIRMED', 'US_CANADA_LIKELY'].includes(c.opportunity_bucket) && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  nearshore:        { label: 'US/Nearshore Untapped',
    match: c => ['US_CANADA_CONFIRMED', 'US_CANADA_LIKELY'].includes(c.opportunity_bucket) && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  spain_eu:         { label: 'Spain/EU Exploratory Untapped', match: c => c.strategic_focus === 'SPAIN_EU_EXPLORATORY' && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  conn_90d:         { label: 'Connected >90 Days, Never Contacted',
    match: c => ['CONNECTED_90_179D', 'CONNECTED_180_364D', 'CONNECTED_365D_PLUS'].includes(c.connection_age_bucket) && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  conn_180d:        { label: 'Connected >180 Days, Never Contacted',
    match: c => ['CONNECTED_180_364D', 'CONNECTED_365D_PLUS'].includes(c.connection_age_bucket) && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  manual_enriched:  { label: 'Active/Manual Enriched Contacts', match: c => c.is_manual_enriched === true || c.is_manual_enriched === 'True' },
  // Activation Potential (V10) — clickable cards for long-connected /
  // LATAM-international / global-recruiter / highest-potential / today
  // never-contacted recruiters, TA, sourcers and talent partners.
  long_connected_recruiters: { label: 'Long-Connected Recruiters Untapped', match: c =>
    c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED'
    && ['Recruiter', 'Talent Acquisition', 'Sourcer'].includes(c.persona)
    && ['181-365 days', '365+ days'].includes(c.connected_age_bucket) },
  days_365_plus: { label: '365+ Days Never Contacted', match: c =>
    c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' && c.connected_age_bucket === '365+ days' },
  latam_intl_recruiters: { label: 'LATAM/International Recruiters Untapped', match: c =>
    c.activation_category === 'LATAM_INTERNATIONAL_RECRUITER' },
  global_talent_partners: { label: 'Global Talent Partners Untapped', match: c =>
    ['GLOBAL_RECRUITER_UNTAPPED', 'HOT_UNTAPPED_TALENT_PARTNER'].includes(c.activation_category) },
  highest_activation: { label: 'Highest Activation Potential', match: c =>
    (parseFloat(c.untapped_activation_potential_score) || 0) >= 80 },
  first_message_now: { label: 'First Message Now', match: c => c.first_message_priority === 'TODAY' },
  // Executive Overview cross-page routing (Part 1) — combined/derived keys
  // not otherwise exposed as a single Untapped Network card.
  usd_readiness: { label: 'USD/LATAM Readiness — Untapped', match: c =>
    ['LATAM_USD_CONFIRMED', 'LATAM_USD_LIKELY', 'US_CANADA_CONFIRMED', 'US_CANADA_LIKELY', 'GLOBAL_STAFFING', 'GLOBAL_OPPORTUNITY'].includes(c.opportunity_bucket)
    && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  spain_eu_readiness: { label: 'Spain/EU Readiness — Untapped', match: c =>
    ['SPAIN_EU_CONFIRMED', 'SPAIN_EU_LIKELY', 'EUROPE_CONFIRMED', 'EUROPE_LIKELY'].includes(c.opportunity_bucket)
    && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  recruiters_ta: { label: 'Recruiters/TA Untapped', match: c =>
    ['Recruiter', 'Talent Acquisition'].includes(c.persona) && c.contact_history_status === 'NEVER_CONTACTED_CONFIRMED' },
  this_week: { label: 'This Week Untapped Queue', match: null }, // special-cased below
};

// Persona priority used to sort the two Executive-routed "readiness" keys
// above so Recruiter/TA/Hiring-Manager/Data-Leader candidates surface first.
const _EXEC_PERSONA_PRIORITY = ['Recruiter', 'Talent Acquisition', 'Hiring Manager', 'Engineering Manager', 'Head of Data', 'Data Engineering Manager', 'Director'];
function _sortByExecPersonaPriority(rows) {
  return [...rows].sort((a, b) => {
    const pa = _EXEC_PERSONA_PRIORITY.indexOf(a.persona); const pb = _EXEC_PERSONA_PRIORITY.indexOf(b.persona);
    const ra = pa === -1 ? 999 : pa; const rb = pb === -1 ? 999 : pb;
    if (ra !== rb) return ra - rb;
    return (parseFloat(b.untapped_outreach_score) || 0) - (parseFloat(a.untapped_outreach_score) || 0);
  });
}

function _updateActiveUntappedKpiCards() {
  document.querySelectorAll('#page-untapped .kpi-card').forEach(el => {
    const isActive = activeUntappedKpi && el.getAttribute('data-kpi') === activeUntappedKpi;
    el.classList.toggle('active', !!isActive);
    el.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
}

window.applyUntappedKpiFilter = function(key) {
  const def = UNTAPPED_KPI_FILTERS[key];
  if (!def) return;
  const source = (D.untapped_network || {}).top_untapped_contacts || [];
  if (key === 'this_week') {
    const ids = new Set(((D.untapped_network || {}).this_week_queue || []).map(c => c.profile_url));
    filteredUntapped = source.filter(c => ids.has(c.profile_url));
  } else {
    filteredUntapped = source.filter(def.match);
  }
  if (key === 'usd_readiness' || key === 'spain_eu_readiness') {
    filteredUntapped = _sortByExecPersonaPriority(filteredUntapped);
  }
  activeUntappedKpi = key;
  untappedPage = 1;
  _updateActiveUntappedKpiCards();
  renderUntappedTable(def.label);
  const table = document.getElementById('untapped-table');
  if (table) table.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

window.applyUntappedFilters = function() {
  const search   = (document.getElementById('untapped-search')?.value || '').trim().toLowerCase();
  const status   = document.getElementById('untapped-status-filter')?.value || '';
  const cat      = document.getElementById('untapped-category-filter')?.value || '';
  const persona  = document.getElementById('untapped-persona-filter')?.value || '';
  const bucket   = document.getElementById('untapped-bucket-filter')?.value || '';
  const focus    = document.getElementById('untapped-focus-filter')?.value || '';
  const age      = document.getElementById('untapped-age-filter')?.value || '';
  const seniority= document.getElementById('untapped-seniority-filter')?.value || '';
  const minScore = parseFloat(document.getElementById('untapped-min-score')?.value) || 0;
  const minConf  = parseFloat(document.getElementById('untapped-min-confidence')?.value) || 0;
  const primaryOnly = document.getElementById('untapped-primary-only')?.checked || false;
  const europeOnly  = document.getElementById('untapped-europe-only')?.checked || false;
  const activationCat = document.getElementById('untapped-activation-category-filter')?.value || '';
  const connectedAgeBucket = document.getElementById('untapped-connected-age-filter')?.value || '';
  const firstMsgPriority = document.getElementById('untapped-first-message-priority-filter')?.value || '';
  const minActivationScore = parseFloat(document.getElementById('untapped-min-activation-score')?.value) || 0;
  const latamIntlOnly = document.getElementById('untapped-latam-intl-only')?.checked || false;
  const recruiterTaOnly = document.getElementById('untapped-recruiter-ta-only')?.checked || false;
  const connected180Only = document.getElementById('untapped-connected-180-only')?.checked || false;
  const connected365Only = document.getElementById('untapped-connected-365-only')?.checked || false;

  const source = (D.untapped_network || {}).top_untapped_contacts || [];
  filteredUntapped = source.filter(c => {
    if (search) {
      const hay = ((c.full_name||'') + ' ' + (c.company_clean||'')).toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (status && c.contact_history_status !== status) return false;
    if (cat && c.untapped_category !== cat) return false;
    if (persona && c.persona !== persona) return false;
    if (bucket && c.opportunity_bucket !== bucket) return false;
    if (focus && c.strategic_focus !== focus) return false;
    if (age && c.connection_age_bucket !== age) return false;
    if (seniority && c.seniority !== seniority) return false;
    if ((parseFloat(c.untapped_outreach_score) || 0) < minScore) return false;
    if ((parseFloat(c.conversation_match_confidence) || 0) < minConf) return false;
    if (primaryOnly && c.strategic_focus !== 'PRIMARY_LATAM_USD') return false;
    if (europeOnly && c.strategic_focus !== 'SPAIN_EU_EXPLORATORY') return false;
    if (activationCat && c.activation_category !== activationCat) return false;
    if (connectedAgeBucket && c.connected_age_bucket !== connectedAgeBucket) return false;
    if (firstMsgPriority && c.first_message_priority !== firstMsgPriority) return false;
    if ((parseFloat(c.untapped_activation_potential_score) || 0) < minActivationScore) return false;
    if (latamIntlOnly && !['LATAM_INTERNATIONAL_RECRUITER', 'GLOBAL_RECRUITER_UNTAPPED'].includes(c.activation_category)) return false;
    if (recruiterTaOnly && !['Recruiter', 'Talent Acquisition', 'Sourcer'].includes(c.persona)) return false;
    if (connected180Only && !['181-365 days', '365+ days'].includes(c.connected_age_bucket)) return false;
    if (connected365Only && c.connected_age_bucket !== '365+ days') return false;
    return true;
  });
  activeUntappedKpi = null;
  untappedPage = 1;
  _updateActiveUntappedKpiCards();
  renderUntappedTable();
};

window.resetUntappedFilters = function() {
  ['untapped-search'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  ['untapped-status-filter', 'untapped-category-filter', 'untapped-persona-filter', 'untapped-bucket-filter',
   'untapped-focus-filter', 'untapped-age-filter', 'untapped-seniority-filter',
   'untapped-activation-category-filter', 'untapped-connected-age-filter',
   'untapped-first-message-priority-filter'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  const ms = document.getElementById('untapped-min-score'); if (ms) ms.value = '0';
  const mc = document.getElementById('untapped-min-confidence'); if (mc) mc.value = '0';
  const mas = document.getElementById('untapped-min-activation-score'); if (mas) mas.value = '0';
  const po = document.getElementById('untapped-primary-only'); if (po) po.checked = false;
  const eo = document.getElementById('untapped-europe-only'); if (eo) eo.checked = false;
  const lio = document.getElementById('untapped-latam-intl-only'); if (lio) lio.checked = false;
  const rto = document.getElementById('untapped-recruiter-ta-only'); if (rto) rto.checked = false;
  const c180 = document.getElementById('untapped-connected-180-only'); if (c180) c180.checked = false;
  const c365 = document.getElementById('untapped-connected-365-only'); if (c365) c365.checked = false;
  filteredUntapped = (D.untapped_network || {}).top_untapped_contacts || [];
  activeUntappedKpi = null;
  untappedPage = 1;
  _updateActiveUntappedKpiCards();
  renderUntappedTable();
};

function renderUntappedTable(kpiLabel) {
  const st = document.getElementById('untapped-stats');
  if (st) {
    const total = ((D.untapped_network || {}).top_untapped_contacts || []).length;
    const label = kpiLabel || (activeUntappedKpi && UNTAPPED_KPI_FILTERS[activeUntappedKpi] ? UNTAPPED_KPI_FILTERS[activeUntappedKpi].label : '');
    st.textContent = 'Showing ' + filteredUntapped.length + ' of ' + total + ' matching contacts' + (label ? ' — ' + label : '');
  }
  filteredUntapped = [...filteredUntapped].sort((a, b) => {
    // Default ranking (V10): untapped_execution_score first — the blended
    // max() of untapped_outreach_score, untapped_activation_potential_score,
    // and never-contacted-recruiter-adjusted priority_score.
    const e = (parseFloat(b.untapped_execution_score) || 0) - (parseFloat(a.untapped_execution_score) || 0);
    if (e !== 0) return e;
    const s = (parseFloat(b.untapped_outreach_score) || 0) - (parseFloat(a.untapped_outreach_score) || 0);
    if (s !== 0) return s;
    return (parseFloat(b.priority_score) || 0) - (parseFloat(a.priority_score) || 0);
  });
  const start = (untappedPage - 1) * UNTAPPED_PAGE_SIZE;
  const slice = filteredUntapped.slice(start, start + UNTAPPED_PAGE_SIZE);
  const tbody = document.getElementById('untapped-tbody');
  if (!tbody) return;
  if (!slice.length) {
    tbody.innerHTML = '<tr><td colspan="21" style="text-align:center;color:var(--text-muted)">No contacts match the current filters.</td></tr>';
  } else {
    tbody.innerHTML = slice.map(c => {
      const url = c.profile_url || '';
      const score = parseInt(c.untapped_outreach_score) || 0;
      const sCls = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
      const actScore = parseInt(c.untapped_activation_potential_score) || 0;
      const actCls = actScore >= 70 ? 'score-high' : actScore >= 40 ? 'score-med' : 'score-low';
      const execScore = parseInt(c.untapped_execution_score) || 0;
      const execCls = execScore >= 70 ? 'score-high' : execScore >= 40 ? 'score-med' : 'score-low';
      return '<tr>'
        + '<td style="white-space:nowrap">' + (c.full_name||'—') + '</td>'
        + '<td style="white-space:nowrap">' + (c.company_clean||'—') + '</td>'
        + '<td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (c.position_clean||'—') + '</td>'
        + '<td style="white-space:nowrap">' + (c.persona||'—') + '</td>'
        + '<td style="font-size:0.75rem">' + (c.seniority||'—') + '</td>'
        + '<td>' + marketBadge(c.opportunity_bucket||'UNKNOWN') + '</td>'
        + '<td style="font-size:0.75rem;white-space:nowrap">' + (c.connected_on||'—') + '</td>'
        + '<td style="text-align:center">' + (c.days_connected ?? '—') + '</td>'
        + '<td style="font-size:0.75rem;white-space:nowrap">' + (c.connected_age_bucket||'—') + '</td>'
        + '<td>' + untappedBadge(c.contact_history_status) + '</td>'
        + '<td style="font-size:0.7rem;white-space:nowrap">' + (c.untapped_category||'—').replace(/_/g, ' ') + '</td>'
        + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
        + '<td><span class="score-badge ' + actCls + '">' + actScore + '</span></td>'
        + '<td><span class="score-badge ' + execCls + '">' + execScore + '</span></td>'
        + '<td style="font-size:0.7rem;white-space:nowrap">' + (c.activation_category||'—').replace(/_/g, ' ') + '</td>'
        + '<td style="white-space:normal;font-size:0.7rem;max-width:220px">' + (c.untapped_reason||'—') + '</td>'
        + '<td style="white-space:normal;font-size:0.7rem;max-width:220px">' + (c.activation_reason||'—') + '</td>'
        + '<td style="font-size:0.7rem;white-space:nowrap">' + (c.first_message_priority||'—').replace(/_/g, ' ') + '</td>'
        + '<td style="font-size:0.7rem;white-space:nowrap">' + (c.recommended_first_action||'—').replace(/_/g, ' ') + '</td>'
        + '<td style="white-space:normal;font-size:0.7rem;max-width:220px">' + (c.first_message_angle||'—') + '</td>'
        + '<td>' + (url ? '<a href="' + url + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
        + '</tr>';
    }).join('');
  }
  renderUntappedPagination();
}

function renderUntappedPagination() {
  const total = Math.ceil(filteredUntapped.length / UNTAPPED_PAGE_SIZE);
  const pg = document.getElementById('untapped-pagination');
  if (!pg) return;
  let html = '';
  for (let i = 1; i <= Math.min(total, 8); i++) {
    html += '<button class="pg-btn' + (i === untappedPage ? ' active' : '') + '" onclick="goUntappedPage(' + i + ')">' + i + '</button>';
  }
  if (total > 8) html += '<span style="color:var(--text-muted);font-size:0.8rem"> … ' + total + ' pages</span>';
  pg.innerHTML = html;
}

window.goUntappedPage = function(n) { untappedPage = n; renderUntappedTable(); };


// ── USD Contract CRM (hybrid: manual + auto-suggested) ──────────────────────
// Every row across every array — manual or auto-suggested — shares ONE
// unified schema (name, company, role, persona, opportunity_bucket, source,
// record_type, status, score, priority, recommended_action, reason,
// next_action, next_action_date, profile_url, role_url, currency, rate_range,
// remote_policy, timezone_required, timezone_risk, payment_risk,
// contract_risk) — see src/usd_contract_crm.py PUBLIC_ROW_FIELDS. This lets
// one generic table renderer and one shared filter bar drive every section.

const USDCRM_MAX_RENDERED_ROWS = 500; // DOM row cap per section (perf only —
// "Showing X of Y" always reflects the full filtered count, not just what's
// rendered; rows are sorted by score first so the cap never hides the best
// matches).

const USDCRM_COLUMNS = [
  'Name', 'Company', 'Role', 'Persona', 'Bucket', 'Source', 'Status', 'Score',
  'Priority', 'Recommended Action', 'Reason', 'Next Action', 'Next Action Date', 'Link',
];

function usdCrmTableHeaderHtml() {
  return '<tr>' + USDCRM_COLUMNS.map(c => '<th>' + c + '</th>').join('') + '</tr>';
}

function usdCrmRowHtml(r) {
  const score = parseInt(r.score) || 0;
  const sCls = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
  const link = r.profile_url || r.role_url || '';
  return '<tr>'
    + '<td style="white-space:nowrap">' + (r.name || '—') + '</td>'
    + '<td style="white-space:nowrap">' + (r.company || '—') + '</td>'
    + '<td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (r.role || '—') + '</td>'
    + '<td style="font-size:0.75rem">' + (r.persona || '—') + '</td>'
    + '<td>' + (r.opportunity_bucket ? marketBadge(r.opportunity_bucket) : '—') + '</td>'
    + '<td style="font-size:0.7rem;white-space:nowrap">' + (r.source || '—').replace(/_/g, ' ') + '</td>'
    + '<td style="font-size:0.72rem;max-width:140px;white-space:normal">' + (r.status || '—') + '</td>'
    + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
    + '<td>' + (r.priority ? priorityBadge(r.priority) : '—') + '</td>'
    + '<td style="font-size:0.72rem;max-width:160px;white-space:normal">' + (r.recommended_action || '—').replace(/_/g, ' ') + '</td>'
    + '<td style="font-size:0.7rem;max-width:220px;white-space:normal">' + (r.reason || '—') + '</td>'
    + '<td style="font-size:0.72rem;max-width:150px;white-space:normal">' + (r.next_action || '—') + '</td>'
    + '<td style="font-size:0.72rem;white-space:nowrap">' + (r.next_action_date || '—') + '</td>'
    + '<td>' + (link ? '<a href="' + link + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
    + '</tr>';
}

function riskBadge(level) {
  return '<span class="urgency-badge urgency-' + (level || '').toLowerCase() + '">' + (level || '—') + '</span>';
}
function priorityBadge(p) {
  return '<span class="urgency-badge urgency-' + (p || '').toLowerCase() + '">' + (p || '—') + '</span>';
}

function _usdCrmVal(id) { return document.getElementById(id)?.value || ''; }
function _usdCrmChecked(id) { return document.getElementById(id)?.checked || false; }

function _usdCrmPopulateSelect(id, values) {
  const sel = document.getElementById(id);
  if (!sel || sel.options.length > 1) return;
  [...new Set(values.filter(Boolean))].sort().forEach(v => {
    const o = document.createElement('option'); o.value = v; o.textContent = String(v).replace(/_/g, ' '); sel.appendChild(o);
  });
}

function usdCrmAllRows() {
  const crm = D.usd_contract_crm || {};
  return [].concat(
    crm.manual_opportunities || [], crm.auto_suggested_usd_leads || [],
    crm.recruiter_pipeline || [], crm.first_outreach_queue || [],
    crm.follow_up_queue || [], crm.active_process_pipeline || [],
    crm.manual_applications || [],
  );
}

function renderUsdCrm() {
  const crm = D.usd_contract_crm || {};
  const noData = document.getElementById('usdcrm-no-data');
  const mainContent = document.getElementById('usdcrm-main-content');

  if (!crm.available) {
    if (noData) noData.style.display = '';
    if (mainContent) mainContent.style.display = 'none';
    return;
  }
  if (noData) noData.style.display = 'none';
  if (mainContent) mainContent.style.display = '';

  document.querySelectorAll('#page-usdcrm .usdcrm-table thead').forEach(t => {
    if (!t.innerHTML.trim()) t.innerHTML = usdCrmTableHeaderHtml();
  });

  const s = crm.summary || {};
  const sumEl = document.getElementById('usdcrm-summary');
  if (sumEl) sumEl.innerHTML = [
    makeKpiCard('manual_opps',    'Manual USD Opportunities',          s.manual_usd_opportunities || 0,        'click to view', '',     'applyUsdCrmKpiFilter'),
    makeKpiCard('auto_leads',     'Auto-Suggested USD Leads',          s.auto_suggested_usd_leads || 0,        'click to view', 'good', 'applyUsdCrmKpiFilter'),
    makeKpiCard('recruiters',     'Recommended Recruiters to Contact', s.recommended_recruiters_to_contact||0, 'click to view', '',     'applyUsdCrmKpiFilter'),
    makeKpiCard('first_outreach', 'Recommended First Outreach',        s.recommended_first_outreach || 0,      'click to view', '',     'applyUsdCrmKpiFilter'),
    makeKpiCard('followups',      'Recommended Follow-ups',            s.recommended_followups || 0,           'click to view', '',     'applyUsdCrmKpiFilter'),
    makeKpiCard('active_signals', 'Active Interview Signals',          s.active_interview_signals || 0,        'click to view', 'good', 'applyUsdCrmKpiFilter'),
    makeKpiCard('manual_apps',    'Manual Applications Sent',          s.manual_applications_sent || 0,        'click to view', '',     'applyUsdCrmKpiFilter'),
    makeKpiCard('cv_signals',     'CV Requested / Sent Signals',       s.cv_requested_or_sent_signals || 0,    'click to view', '',     'applyUsdCrmKpiFilter'),
    makeKpiCard('replied',        'Recruiters Replied',                s.recruiters_replied || 0,              'click to view', '',     'applyUsdCrmKpiFilter'),
    makeKpiCard('due',            'Follow-ups Due',                    s.followups_due || 0, s.followups_due ? 'action needed' : '',   'warn', 'applyUsdCrmKpiFilter'),
    makeKpiCard('high_risk',      'High-Risk Manual Opportunities',    s.high_risk_manual_opportunities||0, s.high_risk_manual_opportunities?'click to view':'', 'warn', 'applyUsdCrmKpiFilter'),
    makeKpiCard('backup',         'Backup Manual Opportunities',       s.backup_manual_opportunities || 0,     'click to view', '',     'applyUsdCrmKpiFilter'),
  ].join('');

  const allRows = usdCrmAllRows();
  _usdCrmPopulateSelect('usdcrm-record-type-filter', allRows.map(r => r.record_type));
  _usdCrmPopulateSelect('usdcrm-status-filter', allRows.map(r => r.status));
  _usdCrmPopulateSelect('usdcrm-persona-filter', allRows.map(r => r.persona));
  _usdCrmPopulateSelect('usdcrm-bucket-filter', allRows.map(r => r.opportunity_bucket));
  _usdCrmPopulateSelect('usdcrm-currency-filter', allRows.map(r => r.currency));

  renderUsdCrmRiskTables();
  renderUsdCrmWeeklyActions();
  applyUsdCrmFilters();
}

function renderUsdCrmRiskTables() {
  const crm = D.usd_contract_crm || {};
  const risk = crm.contingency_risk || {};
  const hi = document.getElementById('usdcrm-risk-high-tbody');
  if (hi) {
    const rows = risk.high_risk || [];
    hi.innerHTML = rows.length
      ? rows.map(usdCrmRowHtml).join('')
      : '<tr><td colspan="14" style="text-align:center;color:var(--text-muted)">No high-risk manual opportunities.</td></tr>';
  }
  const bk = document.getElementById('usdcrm-risk-backup-tbody');
  if (bk) {
    const rows = risk.backup || [];
    bk.innerHTML = rows.length
      ? rows.map(usdCrmRowHtml).join('')
      : '<tr><td colspan="14" style="text-align:center;color:var(--text-muted)">No backup manual opportunities.</td></tr>';
  }
}

function renderUsdCrmWeeklyActions() {
  const crm = D.usd_contract_crm || {};
  const in7 = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString().slice(0, 10);

  const overdueFollowups = (crm.follow_up_queue || [])
    .filter(r => r.next_action_date && r.next_action_date <= in7)
    .sort((a, b) => (a.next_action_date || '9999').localeCompare(b.next_action_date || '9999'));
  const activeProcess  = [...(crm.active_process_pipeline || [])].sort((a, b) => (parseFloat(b.score) || 0) - (parseFloat(a.score) || 0));
  const topRecruiters  = [...(crm.recruiter_pipeline || [])].sort((a, b) => (parseFloat(b.score) || 0) - (parseFloat(a.score) || 0));
  const topOutreach    = [...(crm.first_outreach_queue || [])].sort((a, b) => (parseFloat(b.score) || 0) - (parseFloat(a.score) || 0));

  const seen = new Set();
  const picks = [];
  function addSome(arr, max) {
    let taken = 0;
    for (const r of arr) {
      if (picks.length >= 15 || taken >= max) break;
      const key = r.profile_url || (r.company + '|' + r.role + '|' + r.name);
      if (seen.has(key)) continue;
      seen.add(key);
      picks.push(r);
      taken++;
    }
  }
  addSome(overdueFollowups, 6);
  addSome(activeProcess, 4);
  addSome(topRecruiters, 3);
  addSome(topOutreach, 3);

  const tbody = document.getElementById('usdcrm-weekly-actions-tbody');
  if (!tbody) return;
  if (!picks.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">No recommended actions this week.</td></tr>';
    return;
  }
  tbody.innerHTML = picks.map((r, i) => {
    const link = r.profile_url || r.role_url || '';
    const score = parseInt(r.score) || 0;
    const sCls = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
    return '<tr>'
      + '<td><strong>#' + (i + 1) + '</strong></td>'
      + '<td style="white-space:nowrap">' + (r.name || '—') + '</td>'
      + '<td style="white-space:nowrap">' + (r.company || '—') + '</td>'
      + '<td style="font-size:0.72rem">' + (r.status || '—') + '</td>'
      + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
      + '<td style="font-size:0.72rem;max-width:200px;white-space:normal">' + (r.recommended_action || r.next_action || '—').replace(/_/g, ' ') + '</td>'
      + '<td style="font-size:0.72rem;white-space:nowrap">' + (r.next_action_date || '—') + '</td>'
      + '<td>' + (link ? '<a href="' + link + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
      + '</tr>';
  }).join('');
}

function renderUsdCrmSection(tbodyId, statsId, sourceArr, pred) {
  const filtered = sourceArr.filter(pred).sort((a, b) => (parseFloat(b.score) || 0) - (parseFloat(a.score) || 0));
  const statsEl = document.getElementById(statsId);
  if (statsEl) statsEl.textContent = 'Showing ' + filtered.length + ' of ' + sourceArr.length;
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="14" style="text-align:center;color:var(--text-muted)">No records match the current filters.</td></tr>';
    return;
  }
  const shown = filtered.slice(0, USDCRM_MAX_RENDERED_ROWS);
  let html = shown.map(usdCrmRowHtml).join('');
  if (filtered.length > USDCRM_MAX_RENDERED_ROWS) {
    html += '<tr><td colspan="14" style="text-align:center;color:var(--text-muted);font-size:0.75rem">'
      + '… ' + (filtered.length - USDCRM_MAX_RENDERED_ROWS) + ' more rows — narrow the filters above to see them (sorted by score, highest first)</td></tr>';
  }
  tbody.innerHTML = html;
}

window.applyUsdCrmFilters = function() {
  const search     = _usdCrmVal('usdcrm-search').toLowerCase();
  const source     = _usdCrmVal('usdcrm-source-filter');
  const recordType = _usdCrmVal('usdcrm-record-type-filter');
  const status     = _usdCrmVal('usdcrm-status-filter');
  const persona    = _usdCrmVal('usdcrm-persona-filter');
  const bucket     = _usdCrmVal('usdcrm-bucket-filter');
  const priority   = _usdCrmVal('usdcrm-priority-filter');
  const scoreMin   = parseFloat(_usdCrmVal('usdcrm-score-min')) || 0;
  const tzRisk     = _usdCrmVal('usdcrm-tzrisk-filter');
  const currency   = _usdCrmVal('usdcrm-currency-filter');
  const dueOnly    = _usdCrmChecked('usdcrm-due-only');
  const manualOnly = _usdCrmChecked('usdcrm-manual-only');
  const autoOnly   = _usdCrmChecked('usdcrm-auto-only');
  const today      = new Date().toISOString().slice(0, 10);

  function pred(r) {
    if (search) {
      const hay = ((r.name || '') + ' ' + (r.company || '') + ' ' + (r.role || '')).toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (source && r.source !== source) return false;
    if (recordType && r.record_type !== recordType) return false;
    if (status && r.status !== status) return false;
    if (persona && r.persona !== persona) return false;
    if (bucket && r.opportunity_bucket !== bucket) return false;
    if (priority && r.priority !== priority) return false;
    if ((parseFloat(r.score) || 0) < scoreMin) return false;
    if (tzRisk && r.timezone_risk !== tzRisk) return false;
    if (currency && r.currency !== currency) return false;
    if (dueOnly && !(r.next_action_date && r.next_action_date <= today)) return false;
    if (manualOnly && r.source !== 'manual') return false;
    if (autoOnly && r.source === 'manual') return false;
    return true;
  }

  const crm = D.usd_contract_crm || {};
  renderUsdCrmSection('usdcrm-leads-tbody',       'usdcrm-leads-stats',       crm.auto_suggested_usd_leads || [], pred);
  renderUsdCrmSection('usdcrm-recruiters-tbody',  'usdcrm-recruiters-stats',  crm.recruiter_pipeline || [],       pred);
  renderUsdCrmSection('usdcrm-outreach-tbody',    'usdcrm-outreach-stats',    crm.first_outreach_queue || [],     pred);
  renderUsdCrmSection('usdcrm-followup-tbody',    'usdcrm-followup-stats',    crm.follow_up_queue || [],          pred);
  renderUsdCrmSection('usdcrm-active-tbody',      'usdcrm-active-stats',      crm.active_process_pipeline || [],  pred);
  renderUsdCrmSection('usdcrm-manual-tbody',      'usdcrm-manual-stats',      crm.manual_opportunities || [],     pred);
  renderUsdCrmSection('usdcrm-applications-tbody','usdcrm-applications-stats',crm.manual_applications || [],     pred);
};

window.resetUsdCrmFilters = function() {
  const el = document.getElementById('usdcrm-search'); if (el) el.value = '';
  ['usdcrm-source-filter', 'usdcrm-record-type-filter', 'usdcrm-status-filter', 'usdcrm-persona-filter',
   'usdcrm-bucket-filter', 'usdcrm-priority-filter', 'usdcrm-tzrisk-filter', 'usdcrm-currency-filter'].forEach(id => {
    const s = document.getElementById(id); if (s) s.value = '';
  });
  const sm = document.getElementById('usdcrm-score-min'); if (sm) sm.value = '0';
  ['usdcrm-due-only', 'usdcrm-manual-only', 'usdcrm-auto-only'].forEach(id => {
    const c = document.getElementById(id); if (c) c.checked = false;
  });
  applyUsdCrmFilters();
};

// Clickable summary cards (Part 5) — scroll to the section the card
// describes; "Follow-ups Due" additionally pre-applies the due/overdue filter.
const USDCRM_KPI_SCROLL = {
  manual_opps:    'usdcrm-section-manual',
  auto_leads:     'usdcrm-section-leads',
  recruiters:     'usdcrm-section-recruiters',
  first_outreach: 'usdcrm-section-outreach',
  followups:      'usdcrm-section-followup',
  active_signals: 'usdcrm-section-active',
  manual_apps:    'usdcrm-section-applications',
  cv_signals:     'usdcrm-section-active',
  replied:        'usdcrm-section-outreach',
  due:            'usdcrm-section-followup',
  high_risk:      'usdcrm-section-risk',
  backup:         'usdcrm-section-risk',
};

window.applyUsdCrmKpiFilter = function(key) {
  if (key === 'due') {
    const el = document.getElementById('usdcrm-due-only');
    if (el) { el.checked = true; applyUsdCrmFilters(); }
  }
  const targetId = USDCRM_KPI_SCROLL[key];
  const target = targetId && document.getElementById(targetId);
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

// ── Opportunity History & Monthly Pipeline (message-intelligence-derived) ──
// Lives inside the USD Contract CRM page. Every row uses the unified event
// schema from src/opportunity_history_engine.py (see EVENT_COLUMNS) —
// contact_name, company, role, persona, event_month/date, opportunity_event_type,
// opportunity_stage, opportunity_signal_strength, score, reactivation_date,
// reason_short, message_angle, profile_url — no raw message content ever.

const OPPHIST_MAX_RENDERED_ROWS = 500;

const OPPHIST_COLUMNS = [
  'Month', 'Contact', 'Company', 'Role', 'Persona', 'Event Type', 'Stage',
  'Strength', 'Score', 'Reactivation Date', 'Reason', 'Message Angle', 'Link',
];

function opphistTableHeaderHtml() {
  return '<tr>' + OPPHIST_COLUMNS.map(c => '<th>' + c + '</th>').join('') + '</tr>';
}

function opphistStrengthBadge(s) {
  const cls = s === 'High' ? 'urgency-critical' : s === 'Medium' ? 'urgency-medium' : 'urgency-low';
  return '<span class="urgency-badge ' + cls + '">' + (s || '—') + '</span>';
}

function opphistRowHtml(e) {
  const score = parseInt(e.score) || 0;
  const sCls = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
  const link = e.profile_url || '';
  return '<tr>'
    + '<td style="font-size:0.72rem;white-space:nowrap">' + (e.event_month || '—') + '</td>'
    + '<td style="white-space:nowrap">' + (e.contact_name || '—') + '</td>'
    + '<td style="white-space:nowrap">' + (e.company || '—') + '</td>'
    + '<td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (e.role || '—') + '</td>'
    + '<td style="font-size:0.75rem">' + (e.persona || '—') + '</td>'
    + '<td style="font-size:0.72rem;max-width:150px;white-space:normal">' + (e.opportunity_event_type || '—') + '</td>'
    + '<td style="font-size:0.72rem;max-width:150px;white-space:normal">' + (e.opportunity_stage || '—') + '</td>'
    + '<td>' + opphistStrengthBadge(e.opportunity_signal_strength) + '</td>'
    + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
    + '<td style="font-size:0.72rem;white-space:nowrap">' + (e.reactivation_date || '—') + '</td>'
    + '<td style="font-size:0.7rem;max-width:220px;white-space:normal">' + (e.reason_short || '—') + '</td>'
    + '<td style="font-size:0.7rem;max-width:220px;white-space:normal">' + (e.message_angle || '—') + '</td>'
    + '<td>' + (link ? '<a href="' + link + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
    + '</tr>';
}

function _opphistCurrentMonth() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
}

function _opphistData() {
  return ((D.usd_contract_crm || {}).opportunity_history) || { available: false };
}

function renderOpportunityHistory() {
  const oh = _opphistData();
  const noData = document.getElementById('opphist-no-data');
  const mainContent = document.getElementById('opphist-main-content');

  if (!oh.available) {
    if (noData) noData.style.display = '';
    if (mainContent) mainContent.style.display = 'none';
    return;
  }
  if (noData) noData.style.display = 'none';
  if (mainContent) mainContent.style.display = '';

  document.querySelectorAll('#page-usdcrm .opphist-table thead').forEach(t => {
    if (!t.innerHTML.trim()) t.innerHTML = opphistTableHeaderHtml();
  });

  const s = oh.summary || {};
  const currentMonth = _opphistCurrentMonth();
  const inboundThisMonth = (oh.inbound_opportunities || []).filter(e => e.event_month === currentMonth).length;

  const sumEl = document.getElementById('opphist-summary');
  if (sumEl) sumEl.innerHTML = [
    makeKpiCard('inbound_this_month', 'Inbound Opportunities This Month', inboundThisMonth, 'click to view', 'good', 'applyOpphistKpiFilter'),
    makeKpiCard('salary_requested',   'Salary Requested',                 s.salary_requested_total || 0,      'click to view', '',     'applyOpphistKpiFilter'),
    makeKpiCard('calls_requested',    'Calls Requested',                  s.calls_requested_total || 0,       'click to view', '',     'applyOpphistKpiFilter'),
    makeKpiCard('active_talent_pool', 'Active Talent Pool',               s.active_talent_pool_total || 0,    'click to view', 'good', 'applyOpphistKpiFilter'),
    makeKpiCard('cv_requested',       'CV Requested',                     s.cv_requested_total || 0,          'click to view', '',     'applyOpphistKpiFilter'),
    makeKpiCard('soft_closed',        'Soft-Closed / Keep on Radar',      s.soft_closed_total || 0,           'click to view', '',     'applyOpphistKpiFilter'),
    makeKpiCard('reactivation_due',   'Reactivation Due',                 s.reactivation_due_now || 0, s.reactivation_due_now ? 'action needed' : '', 'warn', 'applyOpphistKpiFilter'),
    makeKpiCard('rejected',           'Rejected / Closed',                s.hard_rejections_total || 0,       'click to view', '',     'applyOpphistKpiFilter'),
    makeKpiCard('location_blockers',  'Location Blockers',                s.location_blockers_total || 0,     'click to view', '',     'applyOpphistKpiFilter'),
  ].join('');

  renderOpphistMonthlyTable(oh.monthly_pipeline || []);
  renderOpphistMonthlyChart(oh.monthly_pipeline || []);

  const allEvents = oh.events || [];
  _usdCrmPopulateSelect('opphist-month-filter', (oh.monthly_pipeline || []).map(m => m.month));
  _usdCrmPopulateSelect('opphist-event-type-filter', allEvents.map(e => e.opportunity_event_type));
  _usdCrmPopulateSelect('opphist-stage-filter', allEvents.map(e => e.opportunity_stage));
  _usdCrmPopulateSelect('opphist-persona-filter', allEvents.map(e => e.persona));
  _usdCrmPopulateSelect('opphist-bucket-filter', allEvents.map(e => e.opportunity_bucket));

  applyOpphistFilters();
}

function renderOpphistMonthlyTable(rows) {
  const tbody = document.getElementById('opphist-monthly-tbody');
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="14" style="text-align:center;color:var(--text-muted)">No monthly data yet.</td></tr>';
    return;
  }
  const sorted = [...rows].sort((a, b) => (a.month || '').localeCompare(b.month || ''));
  tbody.innerHTML = sorted.map(m => '<tr>'
    + '<td style="white-space:nowrap"><strong>' + (m.month || '—') + '</strong></td>'
    + '<td>' + (m.inbound_opportunities || 0) + '</td>'
    + '<td>' + (m.active_talent_pool || 0) + '</td>'
    + '<td>' + (m.salary_requested || 0) + '</td>'
    + '<td>' + (m.cv_requested || 0) + '</td>'
    + '<td>' + (m.calls_requested || 0) + '</td>'
    + '<td>' + (m.interviews || 0) + '</td>'
    + '<td>' + (m.client_submissions || 0) + '</td>'
    + '<td>' + (m.soft_closed_keep_radar || 0) + '</td>'
    + '<td>' + (m.hard_rejections || 0) + '</td>'
    + '<td>' + (m.location_blockers || 0) + '</td>'
    + '<td>' + (m.reactivation_due || 0) + '</td>'
    + '<td>' + (m.hot_opportunities || 0) + '</td>'
    + '<td>' + (m.warm_opportunities || 0) + '</td>'
    + '</tr>').join('');
}

function renderOpphistMonthlyChart(rows) {
  const canvas = document.getElementById('chart-opphist-monthly');
  if (!canvas) return;
  const sorted = [...rows].sort((a, b) => (a.month || '').localeCompare(b.month || ''));
  const labels = sorted.map(m => m.month);
  groupedBarChart('chart-opphist-monthly', labels, [
    { label: 'Inbound Opportunities', data: sorted.map(m => m.inbound_opportunities || 0), color: '#3fb950' },
    { label: 'Calls / Interviews',    data: sorted.map(m => (m.calls_requested || 0) + (m.interviews || 0)), color: '#3b82f6' },
    { label: 'Soft Closed',           data: sorted.map(m => m.soft_closed_keep_radar || 0), color: '#d29922' },
    { label: 'Reactivation Due',      data: sorted.map(m => m.reactivation_due || 0), color: '#f85149' },
  ]);
}

function renderOpphistSection(tbodyId, statsId, sourceArr, pred) {
  const filtered = sourceArr.filter(pred).sort((a, b) => (parseFloat(b.score) || 0) - (parseFloat(a.score) || 0));
  const statsEl = document.getElementById(statsId);
  if (statsEl) statsEl.textContent = 'Showing ' + filtered.length + ' of ' + sourceArr.length;
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="13" style="text-align:center;color:var(--text-muted)">No events match the current filters.</td></tr>';
    return;
  }
  const shown = filtered.slice(0, OPPHIST_MAX_RENDERED_ROWS);
  let html = shown.map(opphistRowHtml).join('');
  if (filtered.length > OPPHIST_MAX_RENDERED_ROWS) {
    html += '<tr><td colspan="13" style="text-align:center;color:var(--text-muted);font-size:0.75rem">'
      + '… ' + (filtered.length - OPPHIST_MAX_RENDERED_ROWS) + ' more rows — narrow the filters above to see them</td></tr>';
  }
  tbody.innerHTML = html;
}

window.applyOpphistFilters = function() {
  const search        = _usdCrmVal('opphist-search').toLowerCase();
  const month         = _usdCrmVal('opphist-month-filter');
  const eventType     = _usdCrmVal('opphist-event-type-filter');
  const stage         = _usdCrmVal('opphist-stage-filter');
  const strength      = _usdCrmVal('opphist-strength-filter');
  const persona       = _usdCrmVal('opphist-persona-filter');
  const company       = _usdCrmVal('opphist-company-filter').toLowerCase();
  const bucket        = _usdCrmVal('opphist-bucket-filter');
  const inboundOnly   = _usdCrmChecked('opphist-inbound-only');
  const reactivationDueOnly = _usdCrmChecked('opphist-reactivation-due-only');
  const softClosedOnly = _usdCrmChecked('opphist-soft-closed-only');
  const activeOnly    = _usdCrmChecked('opphist-active-only');
  const currentMonth  = _opphistCurrentMonth();

  const ACTIVE_TYPES = ['Inbound Opportunity', 'Recruiter Outreach', 'Active Talent Pool',
    'Salary Expectations Requested', 'CV Requested', 'Application Requested',
    'Recruiter Call Proposed', 'Interview Process', 'Client Submission',
    'Technical Interview', 'Offer / Contract Discussion'];

  function pred(e) {
    if (search) {
      const hay = ((e.contact_name || '') + ' ' + (e.company || '')).toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (month && e.event_month !== month) return false;
    if (eventType && e.opportunity_event_type !== eventType) return false;
    if (stage && e.opportunity_stage !== stage) return false;
    if (strength && e.opportunity_signal_strength !== strength) return false;
    if (persona && e.persona !== persona) return false;
    if (company && !(e.company || '').toLowerCase().includes(company)) return false;
    if (bucket && e.opportunity_bucket !== bucket) return false;
    if (inboundOnly && !(e.opportunity_event_type === 'Inbound Opportunity' || e.inbound_recruiter_contact)) return false;
    if (reactivationDueOnly && !(e.reactivation_date && e.reactivation_date.slice(0, 7) === currentMonth)) return false;
    if (softClosedOnly && !e.soft_closed) return false;
    if (activeOnly && !ACTIVE_TYPES.includes(e.opportunity_event_type)) return false;
    return true;
  }

  const oh = _opphistData();
  const allEvents = oh.events || [];
  renderOpphistSection('opphist-inbound-tbody', 'opphist-inbound-stats', oh.inbound_opportunities || [], pred);
  renderOpphistSection('opphist-active-tbody', 'opphist-active-stats',
    allEvents.filter(e => e.active_talent_pool_signal || e.salary_expectation_requested), pred);
  renderOpphistSection('opphist-softclosed-tbody', 'opphist-softclosed-stats', oh.soft_closed_future_leads || [], pred);
  renderOpphistSection('opphist-rejected-tbody', 'opphist-rejected-stats',
    allEvents.filter(e => e.rejected_or_closed), pred);
  renderOpphistSection('opphist-calendar-tbody', 'opphist-calendar-stats', oh.reactivation_calendar || [], pred);
};

window.resetOpphistFilters = function() {
  const el = document.getElementById('opphist-search'); if (el) el.value = '';
  const co = document.getElementById('opphist-company-filter'); if (co) co.value = '';
  ['opphist-month-filter', 'opphist-event-type-filter', 'opphist-stage-filter',
   'opphist-strength-filter', 'opphist-persona-filter', 'opphist-bucket-filter'].forEach(id => {
    const s = document.getElementById(id); if (s) s.value = '';
  });
  ['opphist-inbound-only', 'opphist-reactivation-due-only', 'opphist-soft-closed-only', 'opphist-active-only'].forEach(id => {
    const c = document.getElementById(id); if (c) c.checked = false;
  });
  applyOpphistFilters();
};

const OPPHIST_KPI_SCROLL = {
  inbound_this_month: 'opphist-section-inbound',
  salary_requested:   'opphist-section-active',
  calls_requested:    'opphist-section-active',
  active_talent_pool: 'opphist-section-active',
  cv_requested:        'opphist-section-active',
  soft_closed:         'opphist-section-softclosed',
  reactivation_due:    'opphist-section-calendar',
  rejected:            'opphist-section-rejected',
  location_blockers:   'opphist-section-rejected',
};

window.applyOpphistKpiFilter = function(key) {
  if (key === 'reactivation_due') {
    const el = document.getElementById('opphist-reactivation-due-only');
    if (el) { el.checked = true; applyOpphistFilters(); }
  } else if (key === 'soft_closed') {
    const el = document.getElementById('opphist-soft-closed-only');
    if (el) { el.checked = true; applyOpphistFilters(); }
  } else if (key === 'inbound_this_month') {
    const monthSel = document.getElementById('opphist-month-filter');
    if (monthSel) { monthSel.value = _opphistCurrentMonth(); applyOpphistFilters(); }
  }
  const targetId = OPPHIST_KPI_SCROLL[key];
  const target = targetId && document.getElementById(targetId);
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

// ── Monthly Executive Queue (curated top-20 execution lists) ───────────────
// Lives inside the USD Contract CRM page, near the top. Every row uses the
// unified queue schema from src/monthly_executive_queue.py (QUEUE_ROW_FIELDS)
// — queue_name, rank, contact_name, company, role, persona, event_month/date,
// last_contact_date, opportunity_event_type/stage/strength, opportunity_bucket,
// usd/latam/remote_signal, score, priority, recommended_action,
// next_action_date, reason_short, message_angle — no raw message content ever.

const MEQ_MAX_RENDERED_ROWS = 200;

const MEQ_COLUMNS = [
  'Rank', 'Contact', 'Company', 'Role', 'Persona', 'Month', 'Event Type',
  'Stage', 'Strength', 'Bucket', 'Score', 'Priority', 'Next Action Date',
  'Reason', 'Message Angle', 'Link',
];

function meqTableHeaderHtml() {
  return '<tr>' + MEQ_COLUMNS.map(c => '<th>' + c + '</th>').join('') + '</tr>';
}

function meqRowHtml(r) {
  const score = parseInt(r.score) || 0;
  const sCls = score >= 70 ? 'score-high' : score >= 40 ? 'score-med' : 'score-low';
  const link = r.profile_url || '';
  return '<tr>'
    + '<td><strong>#' + (r.rank || '—') + '</strong></td>'
    + '<td style="white-space:nowrap">' + (r.contact_name || '—') + '</td>'
    + '<td style="white-space:nowrap">' + (r.company || '—') + '</td>'
    + '<td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (r.role || '—') + '</td>'
    + '<td style="font-size:0.75rem">' + (r.persona || '—') + '</td>'
    + '<td style="font-size:0.72rem;white-space:nowrap">' + (r.event_month || '—') + '</td>'
    + '<td style="font-size:0.72rem;max-width:140px;white-space:normal">' + (r.opportunity_event_type || '—') + '</td>'
    + '<td style="font-size:0.72rem;max-width:130px;white-space:normal">' + (r.opportunity_stage || '—') + '</td>'
    + '<td>' + opphistStrengthBadge(r.opportunity_signal_strength) + '</td>'
    + '<td>' + (r.opportunity_bucket ? marketBadge(r.opportunity_bucket) : '—') + '</td>'
    + '<td><span class="score-badge ' + sCls + '">' + score + '</span></td>'
    + '<td>' + (r.priority ? priorityBadge(r.priority) : '—') + '</td>'
    + '<td style="font-size:0.72rem;white-space:nowrap">' + (r.next_action_date || '—') + '</td>'
    + '<td style="font-size:0.7rem;max-width:200px;white-space:normal">' + (r.reason_short || '—') + '</td>'
    + '<td style="font-size:0.7rem;max-width:220px;white-space:normal">' + (r.message_angle || '—') + '</td>'
    + '<td>' + (link ? '<a href="' + link + '" target="_blank" rel="noopener">View</a>' : '—') + '</td>'
    + '</tr>';
}

function _meqData() {
  return ((D.usd_contract_crm || {}).monthly_executive_queue) || { available: false };
}

function renderMonthlyExecutiveQueue() {
  const meq = _meqData();
  const noData = document.getElementById('meq-no-data');
  const mainContent = document.getElementById('meq-main-content');

  if (!meq.available) {
    if (noData) noData.style.display = '';
    if (mainContent) mainContent.style.display = 'none';
    return;
  }
  if (noData) noData.style.display = 'none';
  if (mainContent) mainContent.style.display = '';

  document.querySelectorAll('#page-usdcrm .meq-table thead').forEach(t => {
    if (!t.innerHTML.trim()) t.innerHTML = meqTableHeaderHtml();
  });

  const s = meq.summary || {};
  const sumEl = document.getElementById('meq-summary');
  if (sumEl) sumEl.innerHTML = [
    makeKpiCard('meq_inbound',    'Inbound Opportunities This Month', s.inbound_opportunities_this_month || 0, 'click to view', 'good', 'applyMeqKpiFilter'),
    makeKpiCard('meq_reactivation','Reactivation Due This Month',      s.reactivation_due_this_month || 0,      'click to view', '',     'applyMeqKpiFilter'),
    makeKpiCard('meq_softclosed', 'Soft-Closed Keep-Warm',             s.soft_closed_keep_warm || 0,            'click to view', '',     'applyMeqKpiFilter'),
    makeKpiCard('meq_usdfollowup','USD Recruiter Follow-ups',          s.usd_recruiter_followups || 0,          'click to view', '',     'applyMeqKpiFilter'),
    makeKpiCard('meq_backlog',    'Monthly Backlog',                   s.monthly_backlog || 0,                  'click to view', '',     'applyMeqKpiFilter'),
    makeKpiCard('meq_highprio',   'High Priority This Month',          s.high_priority_this_month || 0,         'click to view', 'good', 'applyMeqKpiFilter'),
    makeKpiCard('meq_overdue',    'Overdue Reactivations',             s.overdue_reactivations || 0, s.overdue_reactivations ? 'action needed' : '', 'warn', 'applyMeqKpiFilter'),
    makeKpiCard('meq_active',     'Active Opportunity Signals',        s.active_opportunity_signals || 0,       'click to view', 'good', 'applyMeqKpiFilter'),
  ].join('');

  renderMeqMonthlyChart(meq.monthly_chart || []);

  const allRecords = meq.all_monthly_queue_records || [];
  _usdCrmPopulateSelect('meq-month-filter', allRecords.map(r => r.event_month));
  _usdCrmPopulateSelect('meq-persona-filter', allRecords.map(r => r.persona));
  _usdCrmPopulateSelect('meq-bucket-filter', allRecords.map(r => r.opportunity_bucket));

  applyMeqFilters();
}

function renderMeqMonthlyChart(rows) {
  const canvas = document.getElementById('chart-meq-monthly');
  if (!canvas) return;
  const sorted = [...rows].sort((a, b) => (a.month || '').localeCompare(b.month || ''));
  const labels = sorted.map(m => m.month);
  groupedBarChart('chart-meq-monthly', labels, [
    { label: 'Inbound Opportunities', data: sorted.map(m => m.inbound_opportunities || 0), color: '#3fb950' },
    { label: 'Reactivation Due',      data: sorted.map(m => m.reactivation_due || 0), color: '#f85149' },
    { label: 'Soft Closed',           data: sorted.map(m => m.soft_closed || 0), color: '#d29922' },
    { label: 'USD Follow-ups',        data: sorted.map(m => m.usd_followups || 0), color: '#3b82f6' },
  ]);
}

function renderMeqSection(tbodyId, statsId, sourceArr, pred) {
  const filtered = sourceArr.filter(pred).sort((a, b) => (a.rank || 999) - (b.rank || 999));
  const statsEl = document.getElementById(statsId);
  if (statsEl) statsEl.textContent = 'Showing ' + filtered.length + ' of ' + sourceArr.length;
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="16" style="text-align:center;color:var(--text-muted)">No records match the current filters.</td></tr>';
    return;
  }
  const shown = filtered.slice(0, MEQ_MAX_RENDERED_ROWS);
  let html = shown.map(meqRowHtml).join('');
  if (filtered.length > MEQ_MAX_RENDERED_ROWS) {
    html += '<tr><td colspan="16" style="text-align:center;color:var(--text-muted);font-size:0.75rem">'
      + '… ' + (filtered.length - MEQ_MAX_RENDERED_ROWS) + ' more rows — narrow the filters above to see them</td></tr>';
  }
  tbody.innerHTML = html;
}

window.applyMeqFilters = function() {
  const search      = _usdCrmVal('meq-search').toLowerCase();
  const month       = _usdCrmVal('meq-month-filter');
  const queueType   = _usdCrmVal('meq-queue-filter');
  const strength    = _usdCrmVal('meq-strength-filter');
  const persona     = _usdCrmVal('meq-persona-filter');
  const bucket      = _usdCrmVal('meq-bucket-filter');
  const priority    = _usdCrmVal('meq-priority-filter');
  const scoreMin    = parseFloat(_usdCrmVal('meq-score-min')) || 0;
  const overdueOnly = _usdCrmChecked('meq-overdue-only');
  const inboundOnly = _usdCrmChecked('meq-inbound-only');
  const softClosedOnly = _usdCrmChecked('meq-soft-closed-only');
  const activeOnly  = _usdCrmChecked('meq-active-only');
  const today       = new Date().toISOString().slice(0, 10);
  const ACTIVE_TYPES = ['Inbound Opportunity', 'Recruiter Outreach', 'Active Talent Pool',
    'Salary Expectations Requested', 'CV Requested', 'Application Requested',
    'Recruiter Call Proposed', 'Interview Process', 'Client Submission',
    'Technical Interview', 'Offer / Contract Discussion'];

  function pred(r) {
    if (search) {
      const hay = ((r.contact_name || '') + ' ' + (r.company || '')).toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (month && r.event_month !== month) return false;
    if (queueType && r.queue_name !== queueType) return false;
    if (strength && r.opportunity_signal_strength !== strength) return false;
    if (persona && r.persona !== persona) return false;
    if (bucket && r.opportunity_bucket !== bucket) return false;
    if (priority && r.priority !== priority) return false;
    if ((parseFloat(r.score) || 0) < scoreMin) return false;
    if (overdueOnly && !(r.next_action_date && r.next_action_date < today)) return false;
    if (inboundOnly && !(r.opportunity_event_type === 'Inbound Opportunity')) return false;
    if (softClosedOnly && !(r.opportunity_event_type === 'No Current Role / Keep on Radar')) return false;
    if (activeOnly && !ACTIVE_TYPES.includes(r.opportunity_event_type)) return false;
    return true;
  }

  const meq = _meqData();
  renderMeqSection('meq-inbound-tbody',      'meq-inbound-stats',      meq.inbound_top20 || [], pred);
  renderMeqSection('meq-reactivation-tbody', 'meq-reactivation-stats', meq.reactivation_top20 || [], pred);
  renderMeqSection('meq-softclosed-tbody',   'meq-softclosed-stats',   meq.soft_closed_top20 || [], pred);
  renderMeqSection('meq-usdfollowup-tbody',  'meq-usdfollowup-stats',  meq.usd_followups_top20 || [], pred);
  renderMeqSection('meq-backlog-tbody',      'meq-backlog-stats',      meq.monthly_backlog_top50 || [], pred);
};

window.resetMeqFilters = function() {
  const el = document.getElementById('meq-search'); if (el) el.value = '';
  ['meq-month-filter', 'meq-queue-filter', 'meq-strength-filter', 'meq-persona-filter',
   'meq-bucket-filter', 'meq-priority-filter'].forEach(id => {
    const s = document.getElementById(id); if (s) s.value = '';
  });
  const sm = document.getElementById('meq-score-min'); if (sm) sm.value = '0';
  ['meq-overdue-only', 'meq-inbound-only', 'meq-soft-closed-only', 'meq-active-only'].forEach(id => {
    const c = document.getElementById(id); if (c) c.checked = false;
  });
  applyMeqFilters();
};

const MEQ_KPI_SCROLL = {
  meq_inbound:     'meq-section-inbound',
  meq_reactivation:'meq-section-reactivation',
  meq_softclosed:  'meq-section-softclosed',
  meq_usdfollowup: 'meq-section-usdfollowup',
  meq_backlog:     'meq-section-backlog',
  meq_highprio:    'meq-section-inbound',
  meq_overdue:     'meq-section-reactivation',
  meq_active:      'meq-section-inbound',
};

window.applyMeqKpiFilter = function(key) {
  if (key === 'meq_overdue') {
    const el = document.getElementById('meq-overdue-only');
    if (el) { el.checked = true; applyMeqFilters(); }
  } else if (key === 'meq_active') {
    const el = document.getElementById('meq-active-only');
    if (el) { el.checked = true; applyMeqFilters(); }
  } else if (key === 'meq_highprio') {
    const el = document.getElementById('meq-priority-filter');
    if (el) { el.value = 'HIGH'; applyMeqFilters(); }
  }
  const targetId = MEQ_KPI_SCROLL[key];
  const target = targetId && document.getElementById(targetId);
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
};
