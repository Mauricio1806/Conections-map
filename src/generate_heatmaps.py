"""
generate_heatmaps.py – Build pivot-table-style heatmap CSVs from classified data.
"""

import logging

import pandas as pd

from src.config import (
    HEATMAP_PERSONA_CSV, HEATMAP_AREA_CSV, HEATMAP_SENIORITY_CSV,
    HEATMAP_MARKET_CSV, HEATMAP_COMPANY_CSV, STRATEGIC_GAP_CSV,
    STRATEGIC_TARGETS,
)

logger = logging.getLogger(__name__)


def _save(df: pd.DataFrame, path, label: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"Saved {label}: {path}")


# ─── Individual Heatmaps ──────────────────────────────────────────────────────

def heatmap_by_persona(df: pd.DataFrame) -> pd.DataFrame:
    """Count connections per persona × market."""
    pivot = (
        df.groupby(["persona", "strategic_market"])
        .size()
        .reset_index(name="count")
        .sort_values(["persona", "count"], ascending=[True, False])
    )
    _save(pivot, HEATMAP_PERSONA_CSV, "Persona Heatmap")
    return pivot


def heatmap_by_area(df: pd.DataFrame) -> pd.DataFrame:
    """Count connections per functional area × market."""
    pivot = (
        df.groupby(["area", "strategic_market"])
        .size()
        .reset_index(name="count")
        .sort_values(["area", "count"], ascending=[True, False])
    )
    _save(pivot, HEATMAP_AREA_CSV, "Area Heatmap")
    return pivot


def heatmap_by_seniority(df: pd.DataFrame) -> pd.DataFrame:
    """Count connections per seniority × market."""
    pivot = (
        df.groupby(["seniority", "strategic_market"])
        .size()
        .reset_index(name="count")
        .sort_values(["seniority", "count"], ascending=[True, False])
    )
    _save(pivot, HEATMAP_SENIORITY_CSV, "Seniority Heatmap")
    return pivot


def heatmap_by_market(df: pd.DataFrame) -> pd.DataFrame:
    """Count connections per market with average priority score."""
    pivot = (
        df.groupby("strategic_market")
        .agg(
            count=("strategic_market", "size"),
            avg_priority=("priority_score", "mean"),
            high_priority=("priority_score", lambda x: (x >= 70).sum()),
        )
        .reset_index()
        .sort_values("count", ascending=False)
    )
    pivot["avg_priority"] = pivot["avg_priority"].round(1)
    _save(pivot, HEATMAP_MARKET_CSV, "Market Heatmap")
    return pivot


def heatmap_by_company(df: pd.DataFrame) -> pd.DataFrame:
    """Top 200 companies by connection count with average priority score."""
    pivot = (
        df[df["company_clean"].str.strip() != ""]
        .groupby("company_clean")
        .agg(
            count=("company_clean", "size"),
            avg_priority=("priority_score", "mean"),
            top_persona=("persona", lambda x: x.mode()[0] if len(x) > 0 else ""),
            top_market=("strategic_market", lambda x: x.mode()[0] if len(x) > 0 else ""),
        )
        .reset_index()
        .sort_values("count", ascending=False)
        .head(200)
    )
    pivot["avg_priority"] = pivot["avg_priority"].round(1)
    _save(pivot, HEATMAP_COMPANY_CSV, "Company Heatmap")
    return pivot


# ─── Strategic Gap Report ─────────────────────────────────────────────────────

def strategic_gap_report(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compare current network counts against strategic targets.
    Produces a DataFrame with columns:
        market, persona, current_count, short_term_target, medium_term_target,
        short_term_gap, medium_term_gap, gap_pct_short, gap_pct_medium, priority
    """
    rows = []

    for market, personas in STRATEGIC_TARGETS.items():
        for persona, targets in personas.items():
            current = len(
                df[
                    (df["strategic_market"] == market)
                    & (df["persona"] == persona)
                ]
            )
            short_target  = targets["short_term"]
            medium_target = targets["medium_term"]
            short_gap     = max(0, short_target - current)
            medium_gap    = max(0, medium_target - current)

            gap_pct_short  = round(short_gap / short_target * 100)  if short_target  else 0
            gap_pct_medium = round(medium_gap / medium_target * 100) if medium_target else 0

            # Priority label
            if gap_pct_short >= 80:
                priority = "CRITICAL"
            elif gap_pct_short >= 50:
                priority = "HIGH"
            elif gap_pct_short >= 20:
                priority = "MEDIUM"
            else:
                priority = "LOW"

            rows.append({
                "market":             market,
                "persona":            persona,
                "current_count":      current,
                "short_term_target":  short_target,
                "medium_term_target": medium_target,
                "short_term_gap":     short_gap,
                "medium_term_gap":    medium_gap,
                "gap_pct_short":      gap_pct_short,
                "gap_pct_medium":     gap_pct_medium,
                "priority":           priority,
            })

    gap_df = pd.DataFrame(rows).sort_values(
        ["gap_pct_short", "short_term_gap"], ascending=[False, False]
    ).reset_index(drop=True)

    _save(gap_df, STRATEGIC_GAP_CSV, "Strategic Gap Report")
    return gap_df


# ─── Entry Point ─────────────────────────────────────────────────────────────

def generate_all_heatmaps(df: pd.DataFrame) -> dict:
    """Generate all heatmaps and the gap report. Returns a dict of DataFrames."""
    logger.info("Generating heatmaps …")
    return {
        "persona":   heatmap_by_persona(df),
        "area":      heatmap_by_area(df),
        "seniority": heatmap_by_seniority(df),
        "market":    heatmap_by_market(df),
        "company":   heatmap_by_company(df),
        "gap":       strategic_gap_report(df),
    }
