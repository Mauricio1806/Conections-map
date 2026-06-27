# -*- coding: utf-8 -*-
"""
generate_dashboard_data.py
Serialize all dashboard-ready data to JSON and CSV for the Streamlit app.
"""

import json
import logging
import pandas as pd
from pathlib import Path

from src.config import OUTPUTS_DIR

logger = logging.getLogger(__name__)

DASHBOARD_JSON = OUTPUTS_DIR / "dashboard_metrics.json"


def build_company_intelligence(df: pd.DataFrame) -> dict:
    """Build company intelligence tables for the dashboard."""
    # Top companies overall
    top_companies = (
        df[df["company_clean"].str.strip() != ""]
        .groupby("company_clean")
        .agg(
            count=("company_clean", "size"),
            avg_score=("priority_score", "mean"),
            top_persona=("persona", lambda x: x.mode()[0] if len(x) else ""),
            top_market=("strategic_market", lambda x: x.mode()[0] if len(x) else ""),
        )
        .reset_index()
        .sort_values("count", ascending=False)
        .head(50)
    )
    top_companies["avg_score"] = top_companies["avg_score"].round(1)

    # Recruiting / staffing companies (by persona)
    recruiting_mask = df["persona"].isin({"Recruiter", "Talent Acquisition", "Sourcer"})
    top_recruiting_companies = (
        df[recruiting_mask & (df["company_clean"].str.strip() != "")]
        .groupby("company_clean")
        .size()
        .reset_index(name="recruiter_count")
        .sort_values("recruiter_count", ascending=False)
        .head(30)
    )

    # Data companies (by area)
    data_mask = df["area"].isin({"Data Engineering", "Analytics", "BI", "Data Science / AI"})
    top_data_companies = (
        df[data_mask & (df["company_clean"].str.strip() != "")]
        .groupby("company_clean")
        .size()
        .reset_index(name="data_count")
        .sort_values("data_count", ascending=False)
        .head(30)
    )

    # LATAM USD relevant companies
    latam_mask = df["strategic_market"] == "LATAM_USD"
    top_latam_companies = (
        df[latam_mask & (df["company_clean"].str.strip() != "")]
        .groupby("company_clean")
        .agg(count=("company_clean","size"), avg_score=("priority_score","mean"))
        .reset_index()
        .sort_values("count", ascending=False)
        .head(25)
    )
    top_latam_companies["avg_score"] = top_latam_companies["avg_score"].round(1)

    # Spain/EU relevant companies
    spain_mask = df["strategic_market"].isin({"SPAIN_EU", "EUROPE"})
    top_spain_companies = (
        df[spain_mask & (df["company_clean"].str.strip() != "")]
        .groupby("company_clean")
        .agg(count=("company_clean","size"), avg_score=("priority_score","mean"))
        .reset_index()
        .sort_values("count", ascending=False)
        .head(25)
    )
    top_spain_companies["avg_score"] = top_spain_companies["avg_score"].round(1)

    return {
        "top_companies":           top_companies.to_dict(orient="records"),
        "top_recruiting_companies":top_recruiting_companies.to_dict(orient="records"),
        "top_data_companies":      top_data_companies.to_dict(orient="records"),
        "top_latam_companies":     top_latam_companies.to_dict(orient="records"),
        "top_spain_companies":     top_spain_companies.to_dict(orient="records"),
    }


def build_heatmap_data(df: pd.DataFrame) -> dict:
    """Build pivot tables for heatmap visualizations."""

    def _pivot(df, index, columns, values="priority_score", aggfunc="count"):
        return (
            df.pivot_table(index=index, columns=columns,
                           values=values, aggfunc=aggfunc, fill_value=0)
            .reset_index()
            .rename_axis(None, axis=1)
        )

    # Persona x Market
    persona_market = _pivot(df, "persona", "strategic_market")

    # Area x Market
    area_market = _pivot(df, "area", "strategic_market")

    # Seniority x Market
    seniority_market = _pivot(df, "seniority", "strategic_market")

    # Priority band column
    df = df.copy()
    df["priority_band"] = pd.cut(
        df["priority_score"],
        bins=[-1, 39, 69, 100],
        labels=["Low (<40)", "Medium (40-69)", "High (>=70)"]
    )

    # Persona x Priority band
    persona_priority = _pivot(df, "persona", "priority_band")

    return {
        "persona_market":   persona_market.to_dict(orient="records"),
        "area_market":      area_market.to_dict(orient="records"),
        "seniority_market": seniority_market.to_dict(orient="records"),
        "persona_priority": persona_priority.to_dict(orient="records"),
    }


def build_top_contacts(df: pd.DataFrame, n: int = 100) -> list:
    """Return top N connections by priority_score."""
    cols = [
        "full_name", "company_clean", "position_clean",
        "persona", "area", "seniority", "strategic_market",
        "priority_score", "recommended_action",
        "connected_on_clean", "url", "market_confidence",
    ]
    cols = [c for c in cols if c in df.columns]
    top = (
        df.sort_values("priority_score", ascending=False)
        .head(n)[cols]
        .reset_index(drop=True)
    )
    return top.to_dict(orient="records")


def save_dashboard_json(kpis: dict, heatmaps: dict, company_intel: dict,
                        top_contacts: list, plan_30: pd.DataFrame,
                        plan_60: pd.DataFrame, plan_90: pd.DataFrame,
                        gap_matrix: pd.DataFrame) -> None:
    """Serialize everything to a single JSON file for the dashboard."""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "kpis":           kpis,
        "heatmaps":       heatmaps,
        "company_intel":  company_intel,
        "top_contacts":   top_contacts,
        "plan_30":        plan_30.to_dict(orient="records"),
        "plan_60":        plan_60.to_dict(orient="records"),
        "plan_90":        plan_90.to_dict(orient="records"),
        "gap_matrix":     gap_matrix.to_dict(orient="records"),
    }

    with open(DASHBOARD_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str, indent=2)

    logger.info(f"Dashboard JSON saved: {DASHBOARD_JSON}")
