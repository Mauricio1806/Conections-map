# Conections-map

> **LinkedIn Network Intelligence Dashboard — V3**  
> Strategic career analysis powered by your LinkedIn exports.

---

## Production Dashboard

**Live URL:** https://mauricio1806.github.io/Conections-map/

> GitHub Pages is configured from the **main branch / docs folder**.  
> Push to main and the dashboard updates automatically.

---

## Weekly Update Workflow

1. **Export fresh LinkedIn data** — download `Connections.csv`, replace the file in the project folder.

2. **Run the three-step pipeline:**
   ```
   python src/build_network_heatmap.py
   python src/build_strategy_layer.py
   python src/generate_static_dashboard.py
   ```

3. **Validate locally** (optional):
   ```
   python src/privacy_check.py
   python -m http.server --directory docs
   ```
   Then open `http://localhost:8000`

4. **Commit and push:**
   ```
   git add .
   git commit -m "update weekly LinkedIn network dashboard"
   git push
   ```

5. **GitHub Pages** updates `https://mauricio1806.github.io/Conections-map/` automatically (usually within 1-2 minutes).

---

## What This Project Does

Reads your exported LinkedIn data and automatically:

1. **Classifies** each connection: persona, functional area, seniority, strategic market
2. **Market Inference V2**: Hierarchical keyword + manual override inference
3. **V3 Scoring Model**: 8 separate score families — UNKNOWN market does NOT drag scores down
4. **Unknown Resolution Engine**: Groups UNKNOWN connections by company, auto-suggests markets
5. **Action Plans**: 7 / 30 / 60 / 90-day prioritized outreach plans
6. **Strategic Gap Analysis**: Where your network falls short, with honest urgency levels
7. **Static HTML Dashboard**: 8-page interactive dashboard served via GitHub Pages
8. **Privacy Check**: Validates no PII in public dashboard data before publishing

---

## Score Model (V3)

UNKNOWN market does NOT mean the connection has no strategic value.
UNKNOWN means no geographic signal was found in the exported fields.

| Score | Measures | UNKNOWN impact |
|-------|---------|----------------|
| `strategic_network_score` | Persona strength — recruiters, HMs, data leaders | None |
| `usd_readiness_score` | USD remote job readiness | GLOBAL_* = partial; high-value UNKNOWN = small partial |
| `spain_eu_readiness_score` | Spain/EU relocation readiness | Same |
| `market_confidence_score` | Quality of market inference | Pure data quality |
| `global_opportunity_score` | GLOBAL_STAFFING/TECH/CONSULTING contacts | Positive |
| `actionable_contacts_score` | Contacts with priority >= 60 | None |
| `unknown_resolution_score` | How much UNKNOWN can be reduced | Diagnostic |
| `data_quality_risk_score` | Risk of score inaccuracy due to data gaps | Warning only |

---

## How to Reduce UNKNOWN

LinkedIn exports do NOT include location. Market is inferred from company/title keywords.

**Fastest path to reduce UNKNOWN:**

1. Open `outputs/company_override_candidates.csv` (top 150 companies to classify)
2. Fill `manual_market` column for companies you recognize
3. Re-run `python src/build_strategy_layer.py && python src/generate_static_dashboard.py`
4. Each company you classify resolves all its connections at once

The Unknown Resolution page in the dashboard shows the top 25 companies to classify first.

---

## Privacy

The static dashboard (`docs/assets/dashboard_data.json`) is safe to publish publicly:
- No email addresses
- No phone numbers
- No raw LinkedIn export data
- Top contacts show: name, company, role, persona, score, LinkedIn URL only

Run `python src/privacy_check.py` to validate before pushing.

---

## Quick Start (First Time)

### 1. Setup
```bash
pip install -r requirements.txt
```

### 2. Place LinkedIn Export Files
Download your LinkedIn export and place these files in the `data/raw/` folder (or project root):
- `Connections.csv`

### 3. Run Pipeline
```bash
python src/build_network_heatmap.py
python src/build_strategy_layer.py
python src/generate_static_dashboard.py
python src/privacy_check.py
```

### 4. View Dashboard
Open `docs/index.html` via a local HTTP server:
```bash
python -m http.server --directory docs
```
Then open: `http://localhost:8000`

Or push to GitHub — the production dashboard updates at:
**https://mauricio1806.github.io/Conections-map/**

---

## Project Structure

```
Conections-map/
  src/
    build_network_heatmap.py       # Step 1: Classify connections
    build_strategy_layer.py        # Step 2: V3 strategy + KPIs + export JSON
    generate_static_dashboard.py   # Step 3: Verify static files
    privacy_check.py               # Step 4: Validate public JSON
    market_inference_v2.py         # Hierarchical market inference
    confidence_adjusted_kpis.py    # V3 8-score model
    unknown_resolution_engine.py   # Classify UNKNOWN companies automatically
    export_public_dashboard_data.py# Build sanitized public JSON
    generate_action_plan.py        # 7/30/60/90-day plans
  config/
    market_keywords.yml            # Keyword rules by market
    company_market_overrides.yml   # Manual company overrides (confidence 0.95)
    company_category_rules.yml     # GLOBAL_STAFFING/TECH/CONSULTING rules
  docs/
    index.html                     # Static dashboard (GitHub Pages)
    assets/
      dashboard_data.json          # Public-safe analytics JSON
      style.css                    # Dashboard styles
      app.js                       # Dashboard logic (Chart.js)
  outputs/
    enriched_connections.csv
    company_override_candidates.csv # Fill manual_market to reduce UNKNOWN
    unknown_resolution_backlog.csv
    unresolved_high_value_contacts.csv
    action_backlog.csv
  reports/
    executive_strategy_report.md
    career_network_roadmap.md
    kpi_dashboard_report.md
  app/
    dashboard.py                   # Streamlit (local development only)
```

---

## Local Streamlit Dashboard (Optional)

For deeper analysis with interactive filters:
```bash
streamlit run app/dashboard.py
```

Note: Streamlit is optional. The production dashboard is the static GitHub Pages version.

---

## Market Inference V2

LinkedIn exports do NOT include location data. Market is inferred from company/title keywords:

| Confidence | Method | Example |
|-----------|--------|---------|
| 0.95 | Manual override (YAML or CSV) | agileengine → LATAM_USD |
| 0.85 | Company keyword match | "Wizeline" → LATAM_USD |
| 0.75 | Title/position keyword | "nearshore Canada" → US_CA |
| 0.70 | Global company category | Accenture → GLOBAL_CONSULTING |
| 0.00 | No signal → UNKNOWN | "Digital Solutions" → UNKNOWN |

See `docs/market_inference_methodology.md` for full details.

---

## GitHub Actions

Pushes to `main` automatically:
1. Run full 4-step pipeline
2. Privacy check (fails if email/phone found in JSON)
3. Verify all 4 required docs/ files exist
4. Commit updated outputs and dashboard

---

MIT License — use freely, no warranty.