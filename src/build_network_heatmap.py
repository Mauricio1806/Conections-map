# -*- coding: utf-8 -*-
"""
build_network_heatmap.py – Main entry point for the LinkedIn Connections Heatmap project.

Usage:
    python src/build_network_heatmap.py

This script orchestrates the full pipeline:
  1. Load raw CSV files
  2. Clean data
  3. Classify connections (persona, area, seniority, market)
  4. Score connections (priority score 0-100)
  5. Generate heatmap CSVs
  6. Generate Excel dashboard
  7. Generate Markdown reports
"""

import io
import logging
import sys
import time
from pathlib import Path

# ─── Ensure src/ is importable when run from project root ────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import CLASSIFIED_CSV, OUTPUTS_DIR, REPORTS_DIR
from src.load_data import load_connections, load_company_follows, load_invitations
from src.clean_data import clean_connections
from src.classify_connections import classify_connections
from src.score_connections import score_connections
from src.generate_heatmaps import generate_all_heatmaps
from src.generate_reports import (
    generate_excel_dashboard,
    generate_daily_report,
    generate_strategic_gap_markdown,
)

# ─── Logging Setup ────────────────────────────────────────────────────────────

# Re-wrap stdout with UTF-8 so emoji/arrow chars don't crash on Windows cp1252
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ─── Pipeline ─────────────────────────────────────────────────────────────────

def run_pipeline() -> None:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("  LinkedIn Connections Heatmap — Starting Pipeline")
    logger.info("=" * 60)

    # 1. Load
    logger.info("Step 1/7: Loading data …")
    connections  = load_connections()
    company_follows = load_company_follows()   # optional – may be None
    invitations  = load_invitations()          # optional – may be None

    logger.info(f"  Connections loaded:    {len(connections):,} rows")
    if company_follows is not None:
        logger.info(f"  Company Follows:       {len(company_follows):,} rows")
    if invitations is not None:
        logger.info(f"  Invitations loaded:    {len(invitations):,} rows")

    # 2. Clean
    logger.info("Step 2/7: Cleaning connections …")
    cleaned = clean_connections(connections)

    # 3. Classify
    logger.info("Step 3/7: Classifying connections …")
    classified = classify_connections(cleaned)

    # 4. Score
    logger.info("Step 4/7: Scoring connections …")
    scored = score_connections(classified)

    # 5. Save classified CSV
    logger.info("Step 5/7: Saving classified_connections.csv …")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    output_cols = [
        "full_name", "company_clean", "position_clean", "connected_on_clean",
        "persona", "area", "seniority", "strategic_market",
        "market_confidence", "inference_reason",
        "priority_score", "recommended_action",
        "url", "email",
    ]
    # Only include cols that exist
    output_cols = [c for c in output_cols if c in scored.columns]
    scored[output_cols].to_csv(CLASSIFIED_CSV, index=False, encoding="utf-8-sig")
    logger.info(f"  Saved: {CLASSIFIED_CSV}")

    # 6. Generate heatmaps
    logger.info("Step 6/7: Generating heatmap CSVs …")
    heatmaps = generate_all_heatmaps(scored)

    # 7. Generate reports
    logger.info("Step 7/7: Generating Excel dashboard and Markdown reports …")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    generate_excel_dashboard(scored, heatmaps)
    generate_daily_report(scored, heatmaps)
    generate_strategic_gap_markdown(heatmaps)

    elapsed = round(time.time() - t0, 1)
    logger.info("=" * 60)
    logger.info(f"  Pipeline complete in {elapsed}s")
    logger.info("=" * 60)
    logger.info("")
    logger.info("[Outputs]")
    logger.info("  CSV heatmaps    -> outputs/")
    logger.info("  Excel dashboard -> reports/dashboard_ready.xlsx")
    logger.info("  Daily report    -> reports/daily_network_report.md")
    logger.info("  Gap report      -> reports/strategic_gap_report.md")
    logger.info("")

    # Print quick stats to console
    total = len(scored)
    high  = (scored["priority_score"] >= 70).sum()
    top3_personas = scored["persona"].value_counts().head(3)
    top3_markets  = scored["strategic_market"].value_counts().head(3)

    logger.info("[Quick Stats]")
    logger.info(f"  Total connections:      {total:,}")
    logger.info(f"  High priority (>=70):   {high:,}")
    logger.info(f"  Top personas: {dict(top3_personas)}")
    logger.info(f"  Top markets:  {dict(top3_markets)}")


# ─── Entry ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        run_pipeline()
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        sys.exit(1)
