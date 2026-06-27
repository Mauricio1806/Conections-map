# Conections-map

> **LinkedIn Network Intelligence Dashboard**  
> Classify, score, visualize, and act on your LinkedIn network to accelerate your career strategy.

---

> ⚠️ **Privacy Warning**  
> If this repository is **public**, do **NOT** commit your raw LinkedIn export files.  
> The `.gitignore` in this repo already excludes files in `data/raw/` by default.

---

## What This Project Does

This project reads your exported LinkedIn data and automatically:

1. **Classifies** each connection by persona, functional area, seniority, and strategic market
2. **Scores** each connection 0–100 based on career relevance
3. **Computes KPIs**: USD Opportunity Score, Spain Readiness Score, Market Readiness Score
4. **Generates action plans**: 30/60/90-day prioritized outreach plans
5. **Identifies strategic gaps**: where your network is under vs. over represented
6. **Produces a Streamlit dashboard** with 7 pages of interactive analysis
7. **Exports** Excel, CSV, and Markdown reports

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the core pipeline (classify + heatmaps)
python src/build_network_heatmap.py

# 3. Run the strategy layer (KPIs + action plans + reports)
python src/build_strategy_layer.py

# 4. Launch the dashboard
streamlit run app/dashboard.py
```

---

## Files Expected

Place your LinkedIn export files in the **project root** (or in `data/raw/`):

| File | Required | Description |
|------|----------|-------------|
| `Connections.csv` | ✅ Yes | Your LinkedIn connections export |
| `Company Follows.csv` | Optional | Companies you follow on LinkedIn |
| `Invitations.csv` | Optional | Sent/received invitations |

### How to Export from LinkedIn

1. Go to **LinkedIn → Settings → Data Privacy → Get a copy of your data**
2. Select **Connections**, **Company Follows**, and **Invitations**
3. Download and unzip the archive
4. Place the CSV files in the project root

---

## Pipeline Architecture

```
[Connections.csv] ──► build_network_heatmap.py ──► classified_connections.csv
                                                 ──► heatmap CSVs
                                                 ──► daily_network_report.md
                                                 ──► dashboard_ready.xlsx (base)

[classified_connections.csv] ──► build_strategy_layer.py ──► kpi_summary.csv
                                                          ──► action_plan_*.csv
                                                          ──► connection_gap_matrix.csv
                                                          ──► dashboard_metrics.json
                                                          ──► executive_strategy_report.md
                                                          ──► career_network_roadmap.md
                                                          ──► dashboard_ready.xlsx (updated)

[outputs/] ──► streamlit run app/dashboard.py ──► Interactive 7-page Dashboard
```

---

## Dashboard Pages

| Page | Description |
|------|-------------|
| 1. Executive Overview | KPI gauges, market distribution, persona breakdown, concentration risk |
| 2. Network Heatmap | 5 interactive heatmaps (Persona×Market, Area×Market, Seniority×Market, etc.) |
| 3. Strategic Gap | Gap analysis with urgency levels, filter by market/urgency |
| 4. Action Plan | 30/60/90-day plans with visual urgency breakdown |
| 5. Top Priority Contacts | Filterable table of highest-priority existing connections |
| 6. Company Intelligence | Top companies by segment (all, recruiting, data, LATAM USD, Spain/EU) |
| 7. Data Quality | Missing data rates, confidence levels, LinkedIn limitations explained |

---

## Strategic Scores

| Score | Formula | Interpretation |
|-------|---------|---------------|
| **USD Opportunity Score** | Weighted: recruiters+TA+HM+data leaders in LATAM/US markets | Readiness to land remote USD job from Brazil |
| **Spain Readiness Score** | Weighted: recruiters+TA+HM+data leaders in SPAIN/EU markets | Readiness for Spain/Europe move |
| **Market Readiness Score** | USD×0.6 + Spain×0.4 | Composite strategic score |

---

## Output Files

```
outputs/
  classified_connections.csv     ← All 10,780 connections classified + scored
  network_heatmap_by_*.csv       ← 5 heatmap pivot CSVs
  strategic_gap_report.csv       ← Gap analysis (original)
  kpi_summary.csv                ← All KPIs as flat CSV
  action_plan_30_days.csv        ← 30-day action plan
  action_plan_60_days.csv        ← 60-day action plan
  action_plan_90_days.csv        ← 90-day action plan
  connection_gap_matrix.csv      ← Full gap matrix with urgency levels
  market_strategy_matrix.csv     ← Market × persona pivot
  persona_strategy_matrix.csv    ← Persona × market with gap data
  dashboard_metrics.json         ← All dashboard data as JSON

reports/
  dashboard_ready.xlsx           ← 17-sheet Excel workbook
  daily_network_report.md        ← Original daily report
  strategic_gap_report.md        ← Original gap report
  executive_strategy_report.md   ← NEW: Strategic analysis and recommendations
  career_network_roadmap.md      ← NEW: 30/60/90 day roadmap
  kpi_dashboard_report.md        ← NEW: KPI definitions and benchmarks

docs/
  dashboard_kpi_dictionary.md    ← Full KPI reference
  strategy_playbook.md           ← Weekly cadences, target companies, message templates
  data_limitations.md            ← LinkedIn export limitations explained
```

---

## Project Structure

```
Conections-map/
├── src/
│   ├── config.py                   # Keywords, targets, paths
│   ├── load_data.py                # CSV loader with preamble skip
│   ├── clean_data.py               # Normalization and cleaning
│   ├── classify_connections.py     # Persona / area / seniority / market
│   ├── score_connections.py        # Priority score 0-100
│   ├── generate_heatmaps.py        # Heatmap CSVs + gap report
│   ├── generate_reports.py         # Excel + Markdown reports
│   ├── build_network_heatmap.py    # Pipeline entry point (step 1)
│   ├── calculate_kpis.py           # KPI computations
│   ├── generate_action_plan.py     # 30/60/90-day plans
│   ├── generate_dashboard_data.py  # JSON data for dashboard
│   └── build_strategy_layer.py     # Strategy layer entry point (step 2)
├── app/
│   └── dashboard.py               # Streamlit 7-page dashboard
├── docs/
│   ├── dashboard_kpi_dictionary.md
│   ├── strategy_playbook.md
│   └── data_limitations.md
├── config/
│   ├── classification_rules.yml
│   └── strategic_targets.yml
├── outputs/                        # Generated CSV + JSON files
├── reports/                        # Generated Excel + Markdown reports
├── data/raw/                       # LinkedIn exports (gitignored)
├── .github/workflows/build-report.yml
├── requirements.txt
└── README.md
```

---

## How GitHub Actions Works

Every push to `main` (or manual trigger) runs:

1. `pip install -r requirements.txt`
2. `python src/build_network_heatmap.py` — classify + heatmaps
3. `python src/build_strategy_layer.py` — KPIs + action plans + reports
4. Uploads `reports/` and `outputs/` as downloadable artifacts (30-day retention)
5. Uploads `dashboard_ready.xlsx` separately (90-day retention)

To trigger manually:
1. Go to your repo → **Actions** tab
2. Select **Build LinkedIn Network Report**
3. Click **Run workflow**

> **Note:** For GitHub Actions to work, `Connections.csv` must be committed.
> To keep raw data private, run the pipeline locally and commit only the reports.

---

## Customization

| File | What to Edit |
|------|-------------|
| `src/config.py` | Classification keywords, scoring weights, strategic targets |
| `src/generate_action_plan.py` | Target counts for 30/60/90-day plans |
| `config/strategic_targets.yml` | Human-readable target documentation |

---

## How to Interpret Results

- **High Priority (score ≥ 70)**: Reach out immediately with a personalized message
- **Medium Priority (40-69)**: Engage with content regularly; reconnect when relevant
- **Low Priority (< 40)**: No immediate action needed
- **UNKNOWN market**: Not irrelevant — just unclassifiable from title/company keywords alone

See [docs/data_limitations.md](docs/data_limitations.md) for a full explanation of
LinkedIn's export limitations and how market inference works.

---

## License

MIT — use freely, no warranty.
