# -*- coding: utf-8 -*-
"""
build_strategy_layer.py
=======================
Entry point for the Strategic Intelligence Layer (V2).

Runs:
  1. Load classified_connections.csv
  2. Apply Market Inference V2 (enriches with market_v2, market_type, etc.)
  3. Compute confidence-adjusted KPIs
  4. Build 30/60/90-day action plans (V1 still works)
  5. Export unknown companies for manual classification
  6. Generate Markdown reports
  7. Update Excel dashboard
  8. Save public dashboard JSON (for static HTML)

Usage:
    python src/build_strategy_layer.py
"""

import io
import json
import logging
import sys
import time
from pathlib import Path

# ── UTF-8 stdout on Windows ──────────────────────────────────────────────────
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.config import (
    CLASSIFIED_CSV, OUTPUTS_DIR, REPORTS_DIR, DASHBOARD_XLSX,
)
from src.market_inference_v2 import (
    apply_market_inference_v2,
    export_unknown_companies,
    export_inference_audit,
)
from src.confidence_adjusted_kpis import (
    compute_confidence_adjusted_kpis,
    save_confidence_adjusted_kpis_csv,
)
from src.generate_action_plan import (
    build_30_day_plan, build_60_day_plan, build_90_day_plan,
    build_connection_gap_matrix, build_market_strategy_matrix,
    build_persona_strategy_matrix,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Output paths ─────────────────────────────────────────────────────────────
ENRICHED_CSV      = OUTPUTS_DIR / "enriched_connections.csv"
KPI_CSV           = OUTPUTS_DIR / "kpi_summary.csv"
PLAN_30           = OUTPUTS_DIR / "action_plan_30_days.csv"
PLAN_60           = OUTPUTS_DIR / "action_plan_60_days.csv"
PLAN_90           = OUTPUTS_DIR / "action_plan_90_days.csv"
MARKET_MATRIX     = OUTPUTS_DIR / "market_strategy_matrix.csv"
PERSONA_MATRIX    = OUTPUTS_DIR / "persona_strategy_matrix.csv"
GAP_MATRIX        = OUTPUTS_DIR / "connection_gap_matrix.csv"
ACTION_BACKLOG    = OUTPUTS_DIR / "action_backlog.csv"
EXEC_REPORT       = REPORTS_DIR / "executive_strategy_report.md"
CAREER_ROADMAP    = REPORTS_DIR / "career_network_roadmap.md"
KPI_REPORT        = REPORTS_DIR / "kpi_dashboard_report.md"


def _save_csv(df: pd.DataFrame, path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"Saved {label}: {path.name}")


def _save_kpi_csv(kpis: dict) -> None:
    rows = [{"kpi": k, "value": v} for k, v in kpis.items()
            if not isinstance(v, (dict, list))]
    _save_csv(pd.DataFrame(rows), KPI_CSV, "KPI Summary")


# ── Action backlog (per contact, top 200) ─────────────────────────────────────

def _build_action_backlog(df: pd.DataFrame) -> pd.DataFrame:
    """Top 200 contacts with actionable fields for daily outreach."""
    mkt_col  = "market_v2" if "market_v2" in df.columns else "strategic_market"
    conf_col = "market_confidence_v2" if "market_confidence_v2" in df.columns else "market_confidence"

    # High-value personas
    target_personas = {
        "Recruiter", "Talent Acquisition", "Sourcer",
        "Hiring Manager", "Engineering Manager",
        "Data Engineering Manager", "Head of Data", "Director",
    }
    target_markets = {
        "LATAM_USD", "US_CANADA_NEARSHORE", "SPAIN_EU", "EUROPE",
        "GLOBAL_STAFFING",
    }

    mask = (
        df["persona"].isin(target_personas) |
        df[mkt_col].isin(target_markets)
    )
    top = (
        df[mask]
        .sort_values("priority_score", ascending=False)
        .head(200)
    )

    def _action_type(row):
        p = row.get("persona", "")
        if p in ("Recruiter", "Talent Acquisition", "Sourcer"):
            return "connect_and_pitch"
        if p in ("Hiring Manager", "Engineering Manager", "Data Engineering Manager"):
            return "warm_introduction"
        if p in ("Head of Data", "Director", "Executive"):
            return "value_first_content"
        return "connect"

    def _message_angle(row):
        p = row.get("persona", "")
        m = row.get(mkt_col, "UNKNOWN")
        if p in ("Recruiter", "Sourcer"):
            return "Pitch as LATAM/nearshore Data Engineer open to USD remote"
        if p == "Talent Acquisition":
            return "Ask to be on their radar for data engineering roles"
        if p in ("Hiring Manager", "Engineering Manager"):
            return "Express interest in their team's data capabilities"
        if m in ("SPAIN_EU", "EUROPE"):
            return "Connect as someone planning Spain/EU relocation"
        return "General professional connection"

    def _weekly_target(row):
        p  = row.get("persona", "")
        sc = float(row.get("priority_score", 0))
        if sc >= 70:
            return "contact this week"
        if sc >= 50:
            return "contact this month"
        return "contact next month"

    top = top.copy()
    top["action_type"]     = top.apply(_action_type, axis=1)
    top["message_angle"]   = top.apply(_message_angle, axis=1)
    top["weekly_priority"] = top.apply(_weekly_target, axis=1)

    cols = [
        "full_name", "company_clean", "position_clean",
        "persona", "area", "seniority", mkt_col, "priority_score",
        "action_type", "message_angle", "weekly_priority",
        conf_col, "url",
    ]
    cols = [c for c in cols if c in top.columns]
    return top[cols].reset_index(drop=True)


# ── Markdown reports ───────────────────────────────────────────────────────────

def _executive_report(kpis: dict, gap_df: pd.DataFrame) -> str:
    from datetime import date
    today      = date.today().strftime("%B %d, %Y")
    total      = kpis["total_connections"]
    usd_raw    = kpis.get("usd_network_score_raw", 0)
    usd_adj    = kpis.get("usd_network_score_adjusted", 0)
    spain_raw  = kpis.get("spain_network_score_raw", 0)
    spain_adj  = kpis.get("spain_network_score_adjusted", 0)
    conf_score = kpis.get("data_confidence_score", 0)
    unknown_pct= kpis.get("unknown_pct", 0)
    flags      = kpis.get("concentration_flags", [])
    usd_level  = kpis.get("usd_score_level", "N/A")
    spain_level= kpis.get("spain_score_level", "N/A")
    usd_desc   = kpis.get("usd_score_desc", "")
    spain_desc = kpis.get("spain_score_desc", "")
    usd_next   = kpis.get("usd_next_step", "")
    spain_next = kpis.get("spain_next_step", "")

    critical = gap_df[gap_df["urgency_level"] == "Critical"].head(5)

    lines = [
        f"# Executive Strategy Report",
        f"> Generated: **{today}**  ",
        f"> ⚠️ *LinkedIn exports do not include location data. "
        f"Market classification is inferred from company/title keywords only.*",
        "",
        "---",
        "",
        "## 1. Network Summary",
        "",
        f"- **Total connections:** {total:,}",
        f"- **High-confidence inferred:** {kpis.get('high_confidence_count',0):,} ({conf_score}%)",
        f"- **Unknown market:** {kpis.get('unknown_count',0):,} ({unknown_pct}%)",
        "",
        f"| Market (V2) | Count |",
        f"|-------------|-------|",
        f"| Brazil | {kpis.get('brazil_count',0):,} |",
        f"| LATAM USD | {kpis.get('latam_usd_count',0):,} |",
        f"| US/Canada Nearshore | {kpis.get('us_nearshore_count',0):,} |",
        f"| Spain/EU | {kpis.get('spain_eu_count',0):,} |",
        f"| Europe | {kpis.get('europe_count',0):,} |",
        f"| Global Staffing | {kpis.get('global_staffing_count',0):,} |",
        f"| Global Tech | {kpis.get('global_tech_count',0):,} |",
        f"| Global Consulting | {kpis.get('global_consulting_count',0):,} |",
        f"| Unknown | {kpis.get('unknown_count',0):,} ({unknown_pct}%) |",
        "",
        "---",
        "",
        "## 2. Strategic Scores (Raw vs Confidence-Adjusted)",
        "",
        "> **Raw score**: uses all inferred connections.  ",
        "> **Adjusted score**: uses only high-confidence connections (confidence >= 0.70).",
        "",
        f"| Score | Raw | Adjusted | Level |",
        f"|-------|-----|----------|-------|",
        f"| USD Opportunity | {usd_raw}/100 | **{usd_adj}/100** | {usd_level} |",
        f"| Spain/EU Readiness | {spain_raw}/100 | **{spain_adj}/100** | {spain_level} |",
        f"| Data Confidence | — | {conf_score}% | — |",
        "",
        f"**USD:** {usd_desc}",
        "",
        f"**Spain/EU:** {spain_desc}",
        "",
        "---",
        "",
        "## 3. Biggest Gaps (Critical Urgency)",
        "",
        f"| Market | Persona | Current | Target | Gap |",
        f"|--------|---------|---------|--------|-----|",
    ]
    for _, row in critical.iterrows():
        lines.append(
            f"| {row.get('market','')} | {row.get('persona','')} | "
            f"{row.get('current_count',0)} | {row.get('target_count',0)} | "
            f"**{row.get('gap_count',0)}** |"
        )

    lines += [
        "",
        "---",
        "",
        "## 4. Data Quality Warning",
        "",
        f"- **{unknown_pct}% of your network has no market signal.**",
        "- This is normal for LinkedIn exports — location is NOT included.",
        "- Fix this by filling in `outputs/company_market_mapping_template.csv`.",
        "- Each company you map reduces UNKNOWN and improves score accuracy.",
        "",
        "---",
        "",
        "## 5. Top 10 Concrete Actions",
        "",
        f"1. **Fill mapping template**: Open `outputs/company_market_mapping_template.csv`, "
        f"classify the top 50 companies, re-run the pipeline.",
        f"2. **{usd_next}**",
        f"3. **{spain_next}**",
        f"4. Activate top 50 contacts in `outputs/action_backlog.csv` (score >= 70).",
        f"5. Send 5 personalized messages/day to LATAM USD recruiters.",
        f"6. Search: `\"data engineer\" \"remote\" \"nearshore\" \"LATAM\"` on LinkedIn.",
        f"7. Connect with US/Canada hiring managers at: AgileEngine, Andela, Wizeline, Gorilla Logic.",
        f"8. Post weekly Data Engineering content to attract inbound recruiters.",
        f"9. Begin Spain/EU recruiter connections at: ERNI, Stratesys, Capgemini Spain.",
        f"10. Track weekly: USD adjusted score target >= 45 before Day 60.",
        "",
        "---",
        "",
        "## 6. What NOT to Do",
        "",
        "- Do not use the raw USD score ({usd_raw}/100) as a reliable number — it includes low-confidence inferences.".format(usd_raw=usd_raw),
        "- Do not assume UNKNOWN connections are irrelevant (many may be in USD markets).",
        "- Do not focus exclusively on Brazil recruiters — they will not get you a USD role.",
        "- Do not send generic connection requests — personalization triples acceptance rate.",
        "",
        "---",
        "",
        "## 7. Flags",
        "",
    ]
    for flag in flags:
        lines.append(f"- {flag}")

    lines += [
        "",
        "---",
        "_Generated by LinkedIn Connections Heatmap — Market Inference V2._",
    ]
    return "\n".join(lines)


def _career_roadmap(kpis: dict) -> str:
    from datetime import date
    today = date.today().strftime("%B %d, %Y")
    usd_adj   = kpis.get("usd_network_score_adjusted", 0)
    spain_adj = kpis.get("spain_network_score_adjusted", 0)
    total     = kpis.get("total_connections", 0)
    hc        = kpis.get("high_confidence_count", 0)
    usd_r_hc  = kpis.get("usd_recruiters_hc", 0)
    spain_r_hc= kpis.get("spain_recruiters_hc", 0)

    return f"""# Career Network Roadmap
> Generated: **{today}**

---

## Strategic Context

| | |
|--|--|
| Current location | Brazil |
| Short-term goal | Remote Data Engineering job paid in USD |
| Medium-term goal | Relocate to Spain / Europe |
| Network size | {total:,} connections |
| High-confidence inferred | {hc:,} |
| USD Score (adjusted) | {usd_adj}/100 |
| Spain Score (adjusted) | {spain_adj}/100 |

> **Note:** Scores use only high-confidence market inference.
> Market is inferred from company/title keywords — LinkedIn does not export location.

---

## 7-Day Action Sprint

| Day | Action | Target |
|-----|--------|--------|
| Mon | Search LATAM USD recruiters, send 7 requests | +7 LATAM USD recruiters |
| Tue | Search US/Canada nearshore recruiters, send 7 requests | +7 US/CA recruiters |
| Wed | Fill 20 rows in company_market_mapping_template.csv | +20 classified companies |
| Thu | Message top 10 from action_backlog.csv (score >= 70) | 10 personalized DMs |
| Fri | Post LinkedIn content (Data Engineering topic) | 1 post, target recruiter reach |
| Sat | Review Spain/EU recruiters: ERNI, Stratesys, Capgemini Spain | +3 Spain connections |
| Sun | Audit: review new connections, update pipeline outputs | 0 outreach, 1 pipeline run |

---

## 30-Day Plan: USD Remote Job Focus

### Goal
Reach USD Adjusted Score >= 45.

### Weekly Connection Targets
| Market | Persona | Weekly Target | Reason |
|--------|---------|--------------|--------|
| LATAM_USD | Recruiter | 8/week | Direct USD job pipeline |
| US_CANADA_NEARSHORE | Recruiter | 6/week | Critical gap |
| US_CANADA_NEARSHORE | Hiring Manager | 4/week | Decision-makers |
| LATAM_USD | Talent Acquisition | 4/week | Secondary pipeline |
| LATAM_USD | Hiring Manager | 3/week | Active hiring |

### Search Queries
- `"data engineer" "remote" "nearshore" recruiter`
- `"data engineering" "LATAM" hiring manager`
- `"staffing" "data" "remote" site:linkedin.com`

---

## 60-Day Plan: USD Stable + Spain Start

### USD Maintenance (Days 31-60)
- 3 US/Canada connections/week (maintenance)
- 2 LATAM USD connections/week (maintenance)
- Weekly DMs to top 15 existing high-priority contacts

### Spain/EU Launch (Days 31-60)
| Persona | Company Examples | Weekly Target |
|---------|-----------------|--------------|
| Spain Recruiter | ERNI, Stratesys, Capgemini Spain | 4/week |
| Spain TA | Seidor, Minsait | 2/week |
| Spain Hiring Manager | Tech companies in Madrid/Barcelona | 2/week |

### KPI Targets at Day 60
| KPI | Target |
|-----|--------|
| USD Adjusted Score | >= 45 |
| Spain Adjusted Score | >= 20 |
| High-confidence contacts | >= 700 |
| LATAM USD Recruiters | 60 |
| US/CA Nearshore Recruiters | 40 |

---

## 90-Day Plan: Balance USD + EU Readiness

### USD Stability (Days 61-90)
- Maintain weekly cadence at 2-3 connections/week
- Target: 1 active USD job conversation by Day 90
- Activate Data Engineering Manager connections for referrals

### EU Deep Build (Days 61-90)
| Week | Focus |
|------|-------|
| Week 9-10 | Spain Heads of Data + EU Recruiters (Dublin, Lisbon) |
| Week 11-12 | European Data Leaders + Germany/Netherlands |

### KPI Targets at Day 90
| KPI | Target |
|-----|--------|
| USD Adjusted Score | >= 55 |
| Spain Adjusted Score | >= 35 |
| LATAM USD Recruiters | 80 |
| US/CA Nearshore Recruiters | 50 |
| Spain/EU Recruiters | 25 |
| Spain/EU Hiring Managers | 15 |
| High-confidence contacts | >= 900 |

---

_Generated by LinkedIn Connections Heatmap — Market Inference V2._
"""


def _kpi_report(kpis: dict) -> str:
    from datetime import date
    today = date.today().strftime("%B %d, %Y")
    return f"""# KPI Dashboard Report
> Generated: **{today}**

## Key Scores

| KPI | Raw | Adjusted | Notes |
|-----|-----|----------|-------|
| USD Opportunity Score | {kpis.get('usd_network_score_raw',0)}/100 | {kpis.get('usd_network_score_adjusted',0)}/100 | Adjusted uses only conf >= 0.70 |
| Spain Readiness Score | {kpis.get('spain_network_score_raw',0)}/100 | {kpis.get('spain_network_score_adjusted',0)}/100 | Capped at 60 if < 20 high-conf contacts |
| Market Readiness | {kpis.get('market_readiness_score_raw',0)}/100 | {kpis.get('market_readiness_score_adjusted',0)}/100 | USD*0.6 + Spain*0.4 |
| Data Confidence | — | {kpis.get('data_confidence_score',0)}% | % of connections with conf >= 0.70 |
| Unknown Risk | — | {kpis.get('unknown_market_risk_score',0)}/100 | Higher = more unknown = less reliable |
| Actionable Network | — | {kpis.get('actionable_network_score',0)}/100 | High-conf + priority >= 60 |

## USD Breakdown (High-Confidence Only)

| Persona | Count | Target |
|---------|-------|--------|
| Recruiters | {kpis.get('usd_recruiters_hc',0)} | 60 |
| Talent Acquisition | {kpis.get('usd_ta_hc',0)} | 40 |
| Hiring Managers | {kpis.get('usd_hiring_mgrs_hc',0)} | 30 |
| Data Leaders | {kpis.get('usd_data_leaders_hc',0)} | 20 |

## Spain/EU Breakdown (High-Confidence Only)

| Persona | Count | Target |
|---------|-------|--------|
| Recruiters | {kpis.get('spain_recruiters_hc',0)} | 40 |
| Talent Acquisition | {kpis.get('spain_ta_hc',0)} | 30 |
| Hiring Managers | {kpis.get('spain_hiring_mgrs_hc',0)} | 20 |
| Data Leaders | {kpis.get('spain_data_leaders_hc',0)} | 15 |

## Unknown Market Analysis

UNKNOWN connections are not necessarily irrelevant.
They simply have no geographic keyword in company or title.

| Persona in UNKNOWN | Count |
|--------------------|-------|
| Recruiters | {kpis.get('unknown_recruiters',0)} |
| Talent Acquisition | {kpis.get('unknown_ta',0)} |
| Hiring Managers | {kpis.get('unknown_hiring_mgrs',0)} |
| Data Leaders | {kpis.get('unknown_data_leaders',0)} |
| Data Peers | {kpis.get('unknown_peers',0)} |

To reduce UNKNOWN: fill `outputs/company_market_mapping_template.csv`.

---
_Generated by LinkedIn Connections Heatmap — Market Inference V2._
"""


# ── Excel update ──────────────────────────────────────────────────────────────

def _update_excel(kpis, plan_30, plan_60, plan_90, market_mat, persona_mat, gap_mat):
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import PatternFill, Font, Alignment
        from openpyxl.utils import get_column_letter

        if not DASHBOARD_XLSX.exists():
            logger.warning("dashboard_ready.xlsx not found — skipping Excel update.")
            return

        wb = load_workbook(DASHBOARD_XLSX)

        kpi_df = pd.DataFrame([
            {"KPI": k, "Value": str(v)}
            for k, v in kpis.items() if not isinstance(v, (dict, list))
        ])

        sheets = {
            "Confidence KPIs":    kpi_df,
            "Market Matrix V2":   market_mat,
            "Persona Matrix V2":  persona_mat,
            "Gap Matrix V2":      gap_mat,
            "30 Day Plan V2":     plan_30,
            "60 Day Plan V2":     plan_60,
            "90 Day Plan V2":     plan_90,
        }

        header_fill = PatternFill(start_color="1a3a5c", end_color="1a3a5c", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)

        for sheet_name, df in sheets.items():
            if sheet_name in wb.sheetnames:
                del wb[sheet_name]
            ws = wb.create_sheet(title=sheet_name)
            for ci, col_name in enumerate(df.columns, 1):
                cell = ws.cell(row=1, column=ci, value=col_name)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center")
            for ri, row in enumerate(df.itertuples(index=False), 2):
                for ci, val in enumerate(row, 1):
                    ws.cell(row=ri, column=ci, value=val)
            for col_cells in ws.columns:
                max_len = max((len(str(c.value)) for c in col_cells if c.value), default=10)
                ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max_len + 2, 60)

        wb.save(DASHBOARD_XLSX)
        logger.info(f"Excel updated: {DASHBOARD_XLSX.name}")
    except Exception as e:
        logger.warning(f"Excel update failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_strategy_layer() -> None:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("  Strategy Layer V2 — Starting")
    logger.info("=" * 60)

    # 1. Load
    logger.info("Step 1/8: Loading classified_connections.csv …")
    if not CLASSIFIED_CSV.exists():
        logger.error(f"Not found: {CLASSIFIED_CSV}")
        logger.error("Run `python src/build_network_heatmap.py` first.")
        sys.exit(1)

    df = pd.read_csv(CLASSIFIED_CSV, dtype=str, low_memory=False)
    df["priority_score"]    = pd.to_numeric(df["priority_score"],    errors="coerce").fillna(0)
    df["market_confidence"] = pd.to_numeric(df["market_confidence"], errors="coerce").fillna(0)
    logger.info(f"  Loaded {len(df):,} rows")

    # 2. Market Inference V2
    logger.info("Step 2/8: Applying Market Inference V2 …")
    df = apply_market_inference_v2(df)
    _save_csv(df, ENRICHED_CSV, "Enriched Connections")

    # 3. Confidence-adjusted KPIs
    logger.info("Step 3/8: Computing confidence-adjusted KPIs …")
    kpis = compute_confidence_adjusted_kpis(df)
    save_confidence_adjusted_kpis_csv(kpis, OUTPUTS_DIR)
    logger.info(f"  USD Score (raw/adj):   {kpis['usd_network_score_raw']} / {kpis['usd_network_score_adjusted']}")
    logger.info(f"  Spain Score (raw/adj): {kpis['spain_network_score_raw']} / {kpis['spain_network_score_adjusted']}")
    logger.info(f"  Data Confidence:       {kpis['data_confidence_score']}%")

    # 4. Action plans (pass enriched df so market_v2 is used)
    logger.info("Step 4/8: Building action plans …")
    plan_30 = build_30_day_plan(df)
    plan_60 = build_60_day_plan(df)
    plan_90 = build_90_day_plan(df)
    _save_csv(plan_30, PLAN_30, "30-Day Plan")
    _save_csv(plan_60, PLAN_60, "60-Day Plan")
    _save_csv(plan_90, PLAN_90, "90-Day Plan")

    # 5. Strategy matrices + gap matrix
    logger.info("Step 5/8: Building strategy matrices …")
    market_mat  = build_market_strategy_matrix(df)
    persona_mat = build_persona_strategy_matrix(df)
    gap_mat     = build_connection_gap_matrix(df)
    action_backlog = _build_action_backlog(df)
    _save_csv(market_mat,      MARKET_MATRIX, "Market Matrix")
    _save_csv(persona_mat,     PERSONA_MATRIX, "Persona Matrix")
    _save_csv(gap_mat,         GAP_MATRIX, "Gap Matrix")
    _save_csv(action_backlog,  ACTION_BACKLOG, "Action Backlog")

    # 6. Unknown company exports + audit
    logger.info("Step 6/8: Exporting unknown companies + inference audit …")
    export_unknown_companies(df)
    export_inference_audit(df)

    # 7. Markdown reports
    logger.info("Step 7/8: Generating Markdown reports …")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    EXEC_REPORT.write_text(_executive_report(kpis, gap_mat), encoding="utf-8")
    CAREER_ROADMAP.write_text(_career_roadmap(kpis), encoding="utf-8")
    KPI_REPORT.write_text(_kpi_report(kpis), encoding="utf-8")
    logger.info("  Reports saved.")

    # 8. Excel
    logger.info("Step 8/8: Updating Excel …")
    _update_excel(kpis, plan_30, plan_60, plan_90, market_mat, persona_mat, gap_mat)

    elapsed = round(time.time() - t0, 1)
    logger.info("=" * 60)
    logger.info(f"  Strategy Layer V2 complete in {elapsed}s")
    logger.info("=" * 60)
    logger.info("")
    logger.info("  Now run: python src/generate_static_dashboard.py")


if __name__ == "__main__":
    try:
        run_strategy_layer()
    except Exception as e:
        logger.exception(f"Strategy layer failed: {e}")
        sys.exit(1)
