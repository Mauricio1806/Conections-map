# Conections-map

> **LinkedIn Network Heatmap & Strategic Gap Analyzer**  
> Classify, score, and visualize your LinkedIn network to guide your job search strategy.

---

> ⚠️ **Privacy Warning**  
> If this repository is **public**, do **NOT** commit your raw LinkedIn export files (`Connections.csv`, `Invitations.csv`, `Company Follows.csv`).  
> These files may contain personal data, email addresses, and profile URLs belonging to your connections.  
> The `.gitignore` in this repo already excludes files in `data/raw/` by default.  
> Move your raw exports to `data/raw/` and they will be excluded from version control automatically.

---

## What This Project Does

This project reads your exported LinkedIn data and automatically:

1. **Classifies** each connection by:
   - Persona (Recruiter, Data Engineer, Head of Data, etc.)
   - Functional area (Recruiting, Data Engineering, Analytics, etc.)
   - Seniority level (Junior, Senior, Manager, Director, Executive, etc.)
   - Strategic market (BRAZIL, LATAM_USD, US_CANADA_NEARSHORE, SPAIN_EU, EUROPE)

2. **Scores** each connection with a 0–100 priority score based on:
   - How relevant their role is to your goals
   - Which strategic market they are in
   - Their seniority level
   - How recently you connected
   - Company signals (nearshore, global, tech, data, consulting, etc.)

3. **Generates heatmaps** showing your network distribution across:
   - Persona × Market
   - Area × Market
   - Seniority × Market
   - Top Companies
   - Market overview

4. **Identifies strategic gaps** by comparing your current network against target counts for your short-term (Brazil → USD remote job) and medium-term (Spain/Europe move) goals.

5. **Exports**:
   - `outputs/classified_connections.csv` — full classified dataset
   - `outputs/network_heatmap_by_*.csv` — heatmap CSVs
   - `outputs/strategic_gap_report.csv` — gap analysis
   - `reports/dashboard_ready.xlsx` — multi-sheet Excel workbook
   - `reports/daily_network_report.md` — Markdown report with recommendations
   - `reports/strategic_gap_report.md` — Markdown gap report

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

Expected columns in `Connections.csv`:
```
First Name, Last Name, URL, Email Address, Company, Position, Connected On
```
Note: LinkedIn sometimes adds a 3-line "Notes:" block at the top — the loader handles this automatically.

---

## How to Run Locally

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the pipeline

```bash
python src/build_network_heatmap.py
```

### 3. View outputs

```
reports/
  dashboard_ready.xlsx         ← Open in Excel / LibreOffice
  daily_network_report.md      ← Open in any Markdown viewer
  strategic_gap_report.md      ← Gap analysis with priorities

outputs/
  classified_connections.csv   ← Full classified dataset
  network_heatmap_by_persona.csv
  network_heatmap_by_area.csv
  network_heatmap_by_seniority.csv
  network_heatmap_by_market.csv
  network_heatmap_by_company.csv
  strategic_gap_report.csv
```

---

## Project Structure

```
Conections-map/
├── src/
│   ├── __init__.py
│   ├── config.py                 # All configuration, keywords, and targets
│   ├── load_data.py              # CSV file detection and loading
│   ├── clean_data.py             # Data normalization and cleaning
│   ├── classify_connections.py   # Persona / area / seniority / market classification
│   ├── score_connections.py      # Priority scoring 0-100
│   ├── generate_heatmaps.py      # Heatmap CSV generation + gap analysis
│   ├── generate_reports.py       # Excel + Markdown report generation
│   └── build_network_heatmap.py  # Main pipeline entry point
├── data/
│   ├── raw/                      # (Place LinkedIn exports here — gitignored)
│   └── processed/
├── outputs/                      # Generated CSV heatmaps
├── reports/                      # Generated Excel + Markdown reports
├── config/
│   ├── classification_rules.yml  # Human-readable rule documentation
│   └── strategic_targets.yml     # Strategic target counts
├── .github/
│   └── workflows/
│       └── build-report.yml      # GitHub Actions CI/CD
├── requirements.txt
├── .gitignore
└── README.md
```

---

## How GitHub Actions Works

Every time you **push to `main`** (or trigger manually via **workflow_dispatch**):

1. GitHub spins up an Ubuntu runner
2. Installs Python 3.11 and all dependencies
3. Runs `python src/build_network_heatmap.py`
4. Uploads the generated `reports/` and `outputs/` folders as downloadable **workflow artifacts** (retained for 30 days)

> **Note:** For the GitHub Actions pipeline to work, your `Connections.csv` file must be committed to the repository.  
> If you want to keep raw data private, run the pipeline **locally only** and only commit the generated reports.

To trigger manually:
1. Go to your repo → **Actions** tab
2. Select **Build LinkedIn Network Report**
3. Click **Run workflow**

---

## How to Interpret the Strategic Gap Report

The `strategic_gap_report.csv` and `reports/strategic_gap_report.md` show:

| Column | Meaning |
|--------|---------|
| `market` | Strategic market (e.g., US_CANADA_NEARSHORE) |
| `persona` | Connection type (e.g., Recruiter) |
| `current_count` | How many you already have |
| `short_term_target` | Target for your Brazil → USD goal |
| `short_term_gap` | How many more you need (short term) |
| `medium_term_target` | Target for your Spain/Europe move |
| `medium_term_gap` | How many more you need (medium term) |
| `priority` | CRITICAL / HIGH / MEDIUM / LOW |

**Focus on CRITICAL rows first** — these are the biggest gaps blocking your strategic goals.

### Strategic Context

| Goal | Timeline | Focus Markets |
|------|----------|---------------|
| Remote USD job from Brazil | 3–6 months | US_CANADA_NEARSHORE, LATAM_USD |
| Spain / Europe move | 6–18 months | SPAIN_EU, EUROPE |

---

## Customization

All classification keywords, scoring weights, and strategic targets live in:
- **`src/config.py`** — edit this file to tune classification rules and scoring
- **`config/strategic_targets.yml`** — human-readable documentation of targets

---

## License

MIT — use freely, no warranty.
