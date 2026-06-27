# -*- coding: utf-8 -*-
"""
generate_static_dashboard.py
=============================
Generates the complete static HTML dashboard for GitHub Pages.

Reads: outputs/public_dashboard_data.json (or generates it fresh)
Writes:
  docs/index.html
  docs/assets/dashboard_data.json  (copy of public JSON)
  docs/assets/app.js               (generated JS with embedded data reference)

Run after build_strategy_layer.py:
    python src/generate_static_dashboard.py
"""

import io
import json
import logging
import shutil
import sys
from datetime import date
from pathlib import Path

# ── UTF-8 stdout on Windows ──────────────────────────────────────────────────
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
DOCS_DIR    = ROOT / "docs"
ASSETS_DIR  = DOCS_DIR / "assets"

PUBLIC_JSON = OUTPUTS_DIR / "public_dashboard_data.json"
DEST_JSON   = ASSETS_DIR / "dashboard_data.json"
INDEX_HTML  = DOCS_DIR / "index.html"
APP_JS      = ASSETS_DIR / "app.js"
STYLE_CSS   = ASSETS_DIR / "style.css"

CLASSIFIED_CSV = OUTPUTS_DIR / "enriched_connections.csv"
FALLBACK_CSV   = OUTPUTS_DIR / "classified_connections.csv"


def _load_or_generate_data() -> dict:
    """Load existing public JSON or generate fresh."""
    if PUBLIC_JSON.exists():
        logger.info(f"Loading existing public dashboard JSON …")
        with open(PUBLIC_JSON, encoding="utf-8") as f:
            return json.load(f)

    logger.info("Public JSON not found — generating fresh …")
    import pandas as pd
    from src.confidence_adjusted_kpis import compute_confidence_adjusted_kpis
    from src.generate_action_plan import build_connection_gap_matrix, build_90_day_plan, build_30_day_plan, build_60_day_plan
    from src.export_public_dashboard_data import export_public_dashboard_data

    csv = CLASSIFIED_CSV if CLASSIFIED_CSV.exists() else FALLBACK_CSV
    df  = pd.read_csv(csv, dtype=str, low_memory=False)
    df["priority_score"]    = pd.to_numeric(df["priority_score"], errors="coerce").fillna(0)
    df["market_confidence"] = pd.to_numeric(df["market_confidence"], errors="coerce").fillna(0)

    kpis = compute_confidence_adjusted_kpis(df)
    gap  = build_connection_gap_matrix(df)
    p30  = build_30_day_plan(df)
    p60  = build_60_day_plan(df)
    p90  = build_90_day_plan(df)
    export_public_dashboard_data(df, kpis, gap, p30, p60, p90)

    with open(PUBLIC_JSON, encoding="utf-8") as f:
        return json.load(f)


def _copy_data_json(data: dict) -> None:
    """Copy / write dashboard_data.json to docs/assets/."""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEST_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str, indent=2)
    logger.info(f"dashboard_data.json copied to docs/assets/")


def _generate_index_html(report_date: str) -> str:
    """Return the full HTML string for docs/index.html."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LinkedIn Network Intelligence Dashboard</title>
  <meta name="description" content="Strategic LinkedIn network analysis dashboard — career intelligence for data engineers.">
  <meta name="robots" content="noindex">
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>

<!-- Loading screen -->
<div id="loading">
  <div class="spinner"></div>
  <p>Loading network intelligence data&hellip;</p>
</div>

<!-- Main layout (hidden until data loads) -->
<div class="layout" id="app" style="display:none">

  <!-- Sidebar -->
  <nav class="sidebar">
    <div class="sidebar-logo">
      <div style="font-size:2rem">&#127757;</div>
      <h1>Network Intel</h1>
      <p>LinkedIn Connections Heatmap</p>
    </div>
    <div class="nav-section">
      <div class="nav-label">Analysis</div>
      <div class="nav-item active" data-page="overview">
        <span class="icon">&#128200;</span> Executive Overview
      </div>
      <div class="nav-item" data-page="heatmap">
        <span class="icon">&#128293;</span> Network Heatmap
      </div>
      <div class="nav-item" data-page="gap">
        <span class="icon">&#128269;</span> Strategic Gap
      </div>
    </div>
    <div class="nav-section">
      <div class="nav-label">Action</div>
      <div class="nav-item" data-page="plan">
        <span class="icon">&#128203;</span> Action Plan
      </div>
      <div class="nav-item" data-page="contacts">
        <span class="icon">&#128100;</span> Top Contacts
      </div>
    </div>
    <div class="nav-section">
      <div class="nav-label">Intelligence</div>
      <div class="nav-item" data-page="companies">
        <span class="icon">&#127970;</span> Company Intel
      </div>
      <div class="nav-item" data-page="unknown">
        <span class="icon">&#10067;</span> Classify Unknown
      </div>
      <div class="nav-item" data-page="quality">
        <span class="icon">&#128202;</span> Data Quality
      </div>
    </div>
    <div style="padding: 1rem 1.2rem; border-top: 1px solid var(--border); margin-top: auto;">
      <div style="font-size:0.7rem; color:var(--text-dim)">Report date</div>
      <div style="font-size:0.8rem; color:var(--text-muted)">{report_date}</div>
    </div>
  </nav>

  <!-- Main content -->
  <main class="main">

    <!-- PAGE: Executive Overview -->
    <div class="page active" id="page-overview">
      <div class="page-header">
        <h2>Executive Network Overview</h2>
        <p>Confidence-adjusted strategic scores. Market is inferred from company/title keywords &mdash; LinkedIn does not export location.</p>
      </div>

      <!-- Data confidence warning -->
      <div id="data-confidence-alert"></div>

      <!-- Score gauges -->
      <div class="gauges-row" id="gauges-row"></div>

      <!-- Executive Diagnosis -->
      <div class="section-header">Executive Diagnosis</div>
      <div class="diagnosis-grid" id="diagnosis-grid"></div>

      <!-- KPI metrics -->
      <div class="section-header">Network Size &amp; Priority</div>
      <div class="metrics-grid metrics-grid-5" id="priority-metrics"></div>

      <div class="section-header">Persona Groups</div>
      <div class="metrics-grid metrics-grid-5" id="persona-metrics"></div>

      <div class="section-header">Market Distribution (V2 Inference)</div>
      <div class="metrics-grid" id="market-metrics"></div>

      <!-- Charts -->
      <div class="section-header">Visual Distribution</div>
      <div class="chart-grid-2">
        <div class="chart-box">
          <h4>Market Distribution (V2)</h4>
          <canvas id="chart-market-pie"></canvas>
        </div>
        <div class="chart-box">
          <h4>Top Personas</h4>
          <canvas id="chart-persona-bar"></canvas>
        </div>
      </div>

      <!-- Concentration flags -->
      <div class="section-header">Network Health Flags</div>
      <div id="concentration-flags"></div>
    </div>

    <!-- PAGE: Heatmap -->
    <div class="page" id="page-heatmap">
      <div class="page-header">
        <h2>Network Heatmap</h2>
        <p>Distribution of connections across market and persona dimensions. Toggle UNKNOWN to focus on classified connections.</p>
      </div>
      <div class="alert alert-info">
        <span class="alert-icon">&#8505;&#65039;</span>
        <span><strong>Market is inferred, not exact location.</strong>
        These heatmaps show where your connections likely operate based on company and title keyword signals.
        UNKNOWN connections have no geographic signal in their exported data.</span>
      </div>
      <div class="filters-bar" style="margin-bottom:1rem">
        <label class="filter-toggle">
          <input type="checkbox" id="hm-exclude-unknown" checked>
          <span>Exclude UNKNOWN market</span>
        </label>
        <label class="filter-toggle">
          <input type="checkbox" id="hm-high-conf-only">
          <span>High-confidence only (>= 0.70)</span>
        </label>
      </div>
      <div class="tabs">
        <button class="tab-btn active" data-tab="hm-persona">Persona &times; Market</button>
        <button class="tab-btn" data-tab="hm-area">Area &times; Market</button>
        <button class="tab-btn" data-tab="hm-seniority">Seniority &times; Market</button>
      </div>
      <div class="tab-panel active" id="hm-persona">
        <div class="table-wrap"><table id="heatmap-persona-market" class="heatmap-table"></table></div>
      </div>
      <div class="tab-panel" id="hm-area">
        <div class="table-wrap"><table id="heatmap-area-market" class="heatmap-table"></table></div>
      </div>
      <div class="tab-panel" id="hm-seniority">
        <div class="table-wrap"><table id="heatmap-seniority-market" class="heatmap-table"></table></div>
      </div>
    </div>

    <!-- PAGE: Strategic Gap -->
    <div class="page" id="page-gap">
      <div class="page-header">
        <h2>Strategic Gap Analysis</h2>
        <p>Where your network is underrepresented vs. your career targets.</p>
      </div>
      <div class="filters-bar">
        <div class="filter-group">
          <label>Urgency</label>
          <select id="gap-urgency-filter">
            <option value="">All</option>
            <option value="Critical">Critical</option>
            <option value="High">High</option>
            <option value="Medium">Medium</option>
            <option value="Low">Low</option>
            <option value="Saturated">Saturated</option>
          </select>
        </div>
        <div class="filter-group">
          <label>Market</label>
          <select id="gap-market-filter"><option value="">All Markets</option></select>
        </div>
        <button class="btn-apply" onclick="applyGapFilters()">Apply</button>
        <button class="btn-reset" onclick="resetGapFilters()">Reset</button>
      </div>
      <div class="chart-box" style="margin-bottom:1.5rem">
        <h4>Connections Needed (Top 15)</h4>
        <canvas id="chart-gap-bar" style="max-height:380px"></canvas>
      </div>
      <div class="table-stats" id="gap-stats"></div>
      <div class="table-wrap">
        <table id="gap-table">
          <thead><tr>
            <th>Market</th><th>Persona</th><th>Current</th><th>Target</th>
            <th>Gap</th><th>Gap %</th><th>Urgency</th><th>Timeframe</th><th>Action</th>
          </tr></thead>
          <tbody id="gap-tbody"></tbody>
        </table>
      </div>
    </div>

    <!-- PAGE: Action Plan -->
    <div class="page" id="page-plan">
      <div class="page-header">
        <h2>30 / 60 / 90 Day Action Plan</h2>
        <p>Prioritized outreach plan with weekly targets, search queries, and message angles.</p>
      </div>
      <div class="tabs">
        <button class="tab-btn active" data-tab="plan-30">30 Days</button>
        <button class="tab-btn" data-tab="plan-60">60 Days</button>
        <button class="tab-btn" data-tab="plan-90">90 Days</button>
      </div>
      <div class="tab-panel active" id="plan-30">
        <div class="alert alert-info">
          <span class="alert-icon">&#127919;</span>
          <span><strong>30-Day Focus:</strong> Build LATAM USD and US/Canada recruiter pipeline. Priority = landing active USD job conversations.</span>
        </div>
        <div class="plan-grid" id="plan-30-grid"></div>
      </div>
      <div class="tab-panel" id="plan-60">
        <div class="alert alert-info">
          <span class="alert-icon">&#127919;</span>
          <span><strong>60-Day Focus:</strong> Maintain USD pipeline, begin Spain/EU network positioning.</span>
        </div>
        <div class="plan-grid" id="plan-60-grid"></div>
      </div>
      <div class="tab-panel" id="plan-90">
        <div class="alert alert-info">
          <span class="alert-icon">&#127919;</span>
          <span><strong>90-Day Focus:</strong> Balance USD income stability with Spain/Europe readiness.</span>
        </div>
        <div class="plan-grid" id="plan-90-grid"></div>
      </div>
    </div>

    <!-- PAGE: Top Contacts -->
    <div class="page" id="page-contacts">
      <div class="page-header">
        <h2>Top Priority Contacts</h2>
        <p>Your highest-priority existing connections. Focus on these first for reactivation.</p>
      </div>
      <div class="filters-bar">
        <div class="filter-group">
          <label>Min Score</label>
          <input type="number" id="ct-min-score" value="60" min="0" max="100">
        </div>
        <div class="filter-group">
          <label>Persona</label>
          <select id="ct-persona-filter"><option value="">All Personas</option></select>
        </div>
        <div class="filter-group">
          <label>Market (V2)</label>
          <select id="ct-market-filter"><option value="">All Markets</option></select>
        </div>
        <div class="filter-group">
          <label>Priority Band</label>
          <select id="ct-band-filter">
            <option value="">All</option>
            <option value="high">High (&ge;70)</option>
            <option value="medium">Medium (40-69)</option>
            <option value="low">Low (&lt;40)</option>
          </select>
        </div>
        <label class="filter-toggle">
          <input type="checkbox" id="ct-hc-only">
          <span>High-confidence only</span>
        </label>
        <button class="btn-apply" onclick="applyContactFilters()">Apply</button>
        <button class="btn-reset" onclick="resetContactFilters()">Reset</button>
      </div>
      <div class="table-stats" id="ct-stats"></div>
      <div class="table-wrap">
        <table id="contacts-table">
          <thead><tr>
            <th>Name</th><th>Company</th><th>Role</th><th>Persona</th>
            <th>Market (V2)</th><th>Score</th><th>Action Type</th>
            <th>Why Priority</th><th>Conf.</th><th>LinkedIn</th>
          </tr></thead>
          <tbody id="contacts-tbody"></tbody>
        </table>
      </div>
      <div class="pagination" id="ct-pagination"></div>
    </div>

    <!-- PAGE: Company Intelligence -->
    <div class="page" id="page-companies">
      <div class="page-header">
        <h2>Company Intelligence</h2>
        <p>Top companies in your network by segment.</p>
      </div>
      <div class="tabs">
        <button class="tab-btn active" data-tab="co-all">All Companies</button>
        <button class="tab-btn" data-tab="co-recruiting">Recruiting</button>
        <button class="tab-btn" data-tab="co-data">Data Companies</button>
        <button class="tab-btn" data-tab="co-latam">LATAM USD</button>
        <button class="tab-btn" data-tab="co-spain">Spain/EU</button>
      </div>
      <div class="tab-panel active" id="co-all">
        <div class="chart-box">
          <h4>Top 25 Companies by Connection Count</h4>
          <canvas id="chart-co-all" style="max-height:450px"></canvas>
        </div>
      </div>
      <div class="tab-panel" id="co-recruiting">
        <div class="chart-box">
          <h4>Top Recruiting &amp; Staffing Companies</h4>
          <canvas id="chart-co-rec" style="max-height:400px"></canvas>
        </div>
      </div>
      <div class="tab-panel" id="co-data">
        <div class="chart-box">
          <h4>Top Data-Focused Companies</h4>
          <canvas id="chart-co-data" style="max-height:400px"></canvas>
        </div>
      </div>
      <div class="tab-panel" id="co-latam">
        <div class="chart-box">
          <h4>Top LATAM USD Companies</h4>
          <canvas id="chart-co-latam" style="max-height:400px"></canvas>
        </div>
      </div>
      <div class="tab-panel" id="co-spain">
        <div class="chart-box">
          <h4>Top Spain/EU Companies</h4>
          <canvas id="chart-co-spain" style="max-height:400px"></canvas>
        </div>
      </div>
    </div>

    <!-- PAGE: Classify Unknown -->
    <div class="page" id="page-unknown">
      <div class="page-header">
        <h2>Classify Unknown Companies</h2>
        <p>These companies have the most connections but no market signal. Classifying them reduces UNKNOWN % and improves score accuracy.</p>
      </div>
      <div class="alert alert-info">
        <span class="alert-icon">&#128161;</span>
        <span>
          <strong>How to classify:</strong> Download <code>outputs/company_market_mapping_template.csv</code>,
          fill in the <code>manual_market</code> column, then re-run the pipeline.
          Each company you classify improves the accuracy of all KPI scores.
        </span>
      </div>
      <div class="section-header">Top Unknown Companies by Connection Count</div>
      <div class="table-wrap">
        <table id="unknown-table">
          <thead><tr>
            <th>Company</th><th>Connections</th><th>Top Persona</th><th>Top Area</th><th>Avg Score</th><th>Suggested Market</th>
          </tr></thead>
          <tbody id="unknown-tbody"></tbody>
        </table>
      </div>
      <div class="section-header">Unknown Connections Still Useful by Persona</div>
      <div class="metrics-grid metrics-grid-5" id="unknown-persona-metrics"></div>
    </div>

    <!-- PAGE: Data Quality -->
    <div class="page" id="page-quality">
      <div class="page-header">
        <h2>Data Quality &amp; Limitations</h2>
        <p>Understanding what LinkedIn exports &mdash; and what it doesn&apos;t.</p>
      </div>
      <div class="alert alert-warn">
        <span class="alert-icon">&#9888;&#65039;</span>
        <span>
          <strong>UNKNOWN does not mean irrelevant.</strong>
          LinkedIn&apos;s connection export does not include location data.
          UNKNOWN means there was no geographic keyword in the company name or job title.
          Many UNKNOWN connections may be in your target markets.
        </span>
      </div>
      <div class="section-header">Completeness Metrics</div>
      <div class="metrics-grid metrics-grid-4" id="quality-metrics"></div>
      <div class="section-header">Inference Confidence Distribution</div>
      <div class="chart-box" style="max-width:500px;margin-bottom:1.5rem">
        <h4>Market Type Distribution</h4>
        <canvas id="chart-market-type"></canvas>
      </div>
      <div class="section-header">How Market Inference Works</div>
      <div class="card">
        <div style="font-size:0.88rem;line-height:1.7;color:var(--text)">
          <p><strong>Inference hierarchy (highest to lowest confidence):</strong></p>
          <ol style="margin:0.8rem 0 0 1.2rem;display:flex;flex-direction:column;gap:0.4rem">
            <li><strong>Manual override</strong> (0.95) &mdash; company manually mapped in overrides YAML</li>
            <li><strong>Manual CSV file</strong> (0.95) &mdash; company filled in company_market_mapping_template.csv</li>
            <li><strong>Company keyword</strong> (0.85) &mdash; company name contains geographic keyword (e.g. &ldquo;LATAM&rdquo;, &ldquo;Madrid&rdquo;)</li>
            <li><strong>Title keyword</strong> (0.75) &mdash; job title contains geographic keyword (e.g. &ldquo;Canada&rdquo;, &ldquo;Spain&rdquo;)</li>
            <li><strong>Global company category</strong> (0.70) &mdash; known global staffing/tech/consulting firm</li>
            <li><strong>UNKNOWN</strong> (0.00) &mdash; no signal found</li>
          </ol>
          <br>
          <p><strong>Why is UNKNOWN so high?</strong></p>
          <p>LinkedIn&apos;s bulk CSV export only includes: Name, URL, Company, Position, Connected On.
          Location is never included in the export.
          A company named &ldquo;Digital Ventures&rdquo; gives zero geographic signal even if it&apos;s based in Austin, TX.
          The inference engine can only work with what&apos;s available.</p>
          <br>
          <p><strong>How to improve:</strong></p>
          <p>1. Download <code>outputs/company_market_mapping_template.csv</code><br>
          2. Fill in <code>manual_market</code> for the top 50 unknown companies<br>
          3. Re-run the pipeline &mdash; scores will update automatically</p>
        </div>
      </div>
    </div>

  </main>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="assets/app.js"></script>
</body>
</html>
"""


def _generate_app_js() -> str:
    """Return the full JavaScript for app.js."""
    return r"""/* LinkedIn Network Intelligence Dashboard — app.js */
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
"""


def generate_static_dashboard() -> None:
    """Main entry point — generate all static HTML dashboard files."""
    logger.info("=" * 60)
    logger.info("  Generating Static HTML Dashboard")
    logger.info("=" * 60)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load or generate data
    logger.info("Step 1/4: Loading dashboard data …")
    try:
        data = _load_or_generate_data()
    except Exception as e:
        logger.error(f"Failed to load/generate data: {e}")
        # Try to generate fresh
        import pandas as pd
        from src.market_inference_v2 import apply_market_inference_v2
        from src.confidence_adjusted_kpis import compute_confidence_adjusted_kpis
        from src.generate_action_plan import build_connection_gap_matrix, build_30_day_plan, build_60_day_plan, build_90_day_plan
        from src.export_public_dashboard_data import export_public_dashboard_data

        csv = CLASSIFIED_CSV if CLASSIFIED_CSV.exists() else FALLBACK_CSV
        if not csv.exists():
            logger.error("No classified_connections.csv found. Run build_network_heatmap.py first.")
            sys.exit(1)

        df = pd.read_csv(csv, dtype=str, low_memory=False)
        df["priority_score"] = pd.to_numeric(df["priority_score"], errors="coerce").fillna(0)
        df["market_confidence"] = pd.to_numeric(df["market_confidence"], errors="coerce").fillna(0)

        if "market_v2" not in df.columns:
            df = apply_market_inference_v2(df)
        kpis = compute_confidence_adjusted_kpis(df)
        gap  = build_connection_gap_matrix(df)
        p30  = build_30_day_plan(df)
        p60  = build_60_day_plan(df)
        p90  = build_90_day_plan(df)
        export_public_dashboard_data(df, kpis, gap, p30, p60, p90)

        with open(PUBLIC_JSON, encoding="utf-8") as f:
            data = json.load(f)

    # 2. Copy data JSON
    logger.info("Step 2/4: Copying dashboard_data.json to docs/assets/ …")
    _copy_data_json(data)

    # 3. Write app.js
    logger.info("Step 3/4: Writing app.js …")
    APP_JS.write_text(_generate_app_js(), encoding="utf-8")
    logger.info(f"  app.js written ({APP_JS.stat().st_size // 1024}KB)")

    # 4. Write index.html
    logger.info("Step 4/4: Writing index.html …")
    report_date = data.get("meta", {}).get("report_date", str(date.today()))
    INDEX_HTML.write_text(_generate_index_html(report_date), encoding="utf-8")
    logger.info(f"  index.html written ({INDEX_HTML.stat().st_size // 1024}KB)")

    # Verify all required files exist
    required = [INDEX_HTML, DEST_JSON, APP_JS, STYLE_CSS]
    all_ok = True
    for p in required:
        if p.exists():
            logger.info(f"  [OK] {p.relative_to(ROOT)}")
        else:
            logger.error(f"  [MISSING] {p.relative_to(ROOT)}")
            all_ok = False

    logger.info("=" * 60)
    if all_ok:
        logger.info("  Static dashboard generation COMPLETE")
        logger.info("  Open: docs/index.html in browser")
        logger.info("  Or serve: python -m http.server --directory docs")
    else:
        logger.warning("  Some files are missing — check logs above")
    logger.info("=" * 60)


if __name__ == "__main__":
    try:
        generate_static_dashboard()
    except Exception as e:
        logger.exception(f"Static dashboard generation failed: {e}")
        sys.exit(1)
