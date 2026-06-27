# -*- coding: utf-8 -*-
"""
export_public_dashboard_data.py
================================
Sanitize and export dashboard data for the static HTML/GitHub Pages dashboard.
Removes all private/PII fields before publishing.

Safe fields for top contacts:
  full_name, company_clean, position_clean, persona, area, seniority,
  market_v2, strategic_market, priority_score, recommended_action,
  action_type, message_angle, why_priority, market_confidence_v2,
  market_type, url (kept — public LinkedIn URL)

NOT included:
  Email Address, email, connected_on_clean (raw date), inference_reason,
  any raw LinkedIn export fields not needed for the dashboard.
"""

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
DOCS_DIR    = ROOT / "docs"
ASSETS_DIR  = DOCS_DIR / "assets"

PUBLIC_JSON_DOCS    = ASSETS_DIR / "dashboard_data.json"
PUBLIC_JSON_OUTPUTS = OUTPUTS_DIR / "public_dashboard_data.json"

SAFE_CONTACT_COLS = [
    "full_name", "company_clean", "position_clean",
    "persona", "area", "seniority",
    "market_v2", "strategic_market",
    "priority_score", "recommended_action",
    "action_type", "message_angle", "why_priority",
    "market_confidence_v2", "market_type", "url",
    "company_category",
]

EXCLUDED_COLS = {
    "email address", "email", "first name", "last name",
    "connected_on", "connected_on_clean", "inference_reason",
    "inference_reason_v2",
}


def _enrich_action_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Add action_type, message_angle, why_priority based on persona + market."""
    df = df.copy()

    def _action_type(row):
        p = row.get("persona", "")
        m = row.get("market_v2", row.get("strategic_market", ""))
        if p in ("Recruiter", "Talent Acquisition", "Sourcer"):
            return "connect_and_pitch"
        if p in ("Hiring Manager", "Engineering Manager", "Data Engineering Manager"):
            return "warm_introduction"
        if p in ("Head of Data", "Director", "Executive"):
            return "value_first_content"
        return "connect"

    def _message_angle(row):
        p = row.get("persona", "")
        m = row.get("market_v2", row.get("strategic_market", ""))
        if p in ("Recruiter", "Sourcer"):
            return (
                "Hi [Name], I'm a Senior Data Engineer open to remote USD roles. "
                "I specialize in dbt, Spark, Airflow, and cloud platforms. "
                "Are you working with LATAM or nearshore data profiles? Happy to connect."
            )
        if p == "Talent Acquisition":
            return (
                "Hi [Name], I noticed your work at [Company]. "
                "I'm a Data Engineer currently available for remote USD opportunities. "
                "Would love to be on your radar for data engineering roles."
            )
        if p in ("Hiring Manager", "Engineering Manager"):
            return (
                "Hi [Name], I saw your team is building out data capabilities. "
                "I'm a Data Engineer with deep experience in [tools]. "
                "Open to a quick chat about your data engineering direction."
            )
        if p in ("Head of Data", "Director", "Executive"):
            return (
                "Hi [Name], I follow your work in the data space. "
                "I'd love to connect and share perspectives on data platform architecture."
            )
        if "SPAIN" in m or "EUROPE" in m:
            return (
                "Hi [Name], I'm planning to relocate to Spain/Europe and building my network there. "
                "Would be great to connect with data professionals in the region."
            )
        return "Hi [Name], I'd love to connect and exchange experiences in the data space."

    def _why_priority(row):
        p = row.get("persona", "")
        m = row.get("market_v2", row.get("strategic_market", ""))
        s = float(row.get("priority_score", 0))
        reasons = []
        if p in ("Recruiter", "Talent Acquisition", "Sourcer"):
            reasons.append("can directly refer you to USD job opportunities")
        if p in ("Hiring Manager", "Data Engineering Manager", "Head of Data"):
            reasons.append("has hiring authority for data engineering roles")
        if m in ("LATAM_USD", "US_CANADA_NEARSHORE"):
            reasons.append("in your primary USD target market")
        if m in ("SPAIN_EU", "EUROPE"):
            reasons.append("in your Spain/EU target market")
        if s >= 70:
            reasons.append(f"high priority score ({s:.0f}/100)")
        return "; ".join(reasons) if reasons else f"priority score {s:.0f}/100"

    df["action_type"]    = df.apply(_action_type, axis=1)
    df["message_angle"]  = df.apply(_message_angle, axis=1)
    df["why_priority"]   = df.apply(_why_priority, axis=1)
    return df


def build_public_contacts(df: pd.DataFrame, n: int = 150) -> list:
    """Build sanitized top contacts list."""
    df = _enrich_action_fields(df)
    safe_cols = [c for c in SAFE_CONTACT_COLS if c in df.columns]

    top = (
        df.sort_values("priority_score", ascending=False)
        .head(n)[safe_cols]
        .reset_index(drop=True)
    )

    # Ensure no private columns leaked
    for col in list(top.columns):
        if col.lower() in EXCLUDED_COLS:
            top = top.drop(columns=[col])

    return top.to_dict(orient="records")


def build_public_market_distribution(df: pd.DataFrame) -> dict:
    """Build market distribution using V2 markets."""
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"
    return df[mkt_col].value_counts().to_dict()


def build_public_persona_distribution(df: pd.DataFrame) -> dict:
    return df["persona"].value_counts().head(15).to_dict()


def build_public_heatmap(df: pd.DataFrame) -> dict:
    """Build pivot tables for heatmap visualization."""
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"

    def _pivot(index, columns):
        try:
            pivot = (
                df.groupby([index, columns])
                .size()
                .reset_index(name="count")
                .pivot(index=index, columns=columns, values="count")
                .fillna(0)
                .astype(int)
            )
            return {
                "labels": list(pivot.index),
                "columns": list(pivot.columns),
                "data": pivot.values.tolist(),
            }
        except Exception:
            return {"labels": [], "columns": [], "data": []}

    return {
        "persona_market":   _pivot("persona", mkt_col),
        "area_market":      _pivot("area", mkt_col),
        "seniority_market": _pivot("seniority", mkt_col),
    }


def build_public_gap_data(gap_df: pd.DataFrame) -> list:
    """Sanitize gap matrix for public export."""
    safe_cols = [
        "market", "persona", "current_count", "target_count",
        "gap_count", "gap_percentage", "urgency_level", "timeframe",
        "strategic_reason", "recommended_action",
    ]
    safe_cols = [c for c in safe_cols if c in gap_df.columns]
    return gap_df[safe_cols].to_dict(orient="records")


def build_public_company_intel(df: pd.DataFrame) -> dict:
    """Top companies by segment — no PII."""
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"

    def _top_companies(mask=None, col="company_clean", n=20):
        src = df if mask is None else df[mask]
        return (
            src[src[col].str.strip() != ""][col]
            .value_counts()
            .head(n)
            .reset_index()
            .rename(columns={col: "company", "count": "count"})
            .to_dict(orient="records")
        )

    return {
        "all_companies":    _top_companies(n=30),
        "recruiting":       _top_companies(
            df["persona"].isin({"Recruiter", "Talent Acquisition", "Sourcer"}), n=20),
        "data_companies":   _top_companies(
            df["area"].isin({"Data Engineering", "Analytics", "BI", "Data Science / AI"}), n=20),
        "latam_usd":        _top_companies(df[mkt_col] == "LATAM_USD", n=20),
        "spain_eu":         _top_companies(df[mkt_col].isin({"SPAIN_EU", "EUROPE"}), n=20),
        "unknown_top":      _top_companies(df[mkt_col] == "UNKNOWN", n=30),
    }


def build_unknown_companies_to_classify(df: pd.DataFrame) -> list:
    """Top unknown companies with persona breakdowns for the 'classify me' tab."""
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"
    unknown_mask = (df[mkt_col] == "UNKNOWN") & (df["company_clean"].str.strip() != "")
    unk = df[unknown_mask]

    agg = (
        unk.groupby("company_clean")
        .agg(
            connection_count   = ("company_clean", "size"),
            top_persona        = ("persona", lambda x: x.mode()[0] if len(x) > 0 else ""),
            top_area           = ("area",    lambda x: x.mode()[0] if len(x) > 0 else ""),
            avg_priority_score = ("priority_score", "mean"),
        )
        .reset_index()
        .sort_values("connection_count", ascending=False)
        .head(100)
    )
    agg["avg_priority_score"] = agg["avg_priority_score"].round(1)
    return agg.to_dict(orient="records")


def export_public_dashboard_data(
    df: pd.DataFrame,
    kpis: dict,
    gap_df: pd.DataFrame,
    plan_30: pd.DataFrame,
    plan_60: pd.DataFrame,
    plan_90: pd.DataFrame,
) -> None:
    """Build and save public dashboard JSON."""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "meta": {
            "report_date":        str(date.today()),
            "total_connections":  kpis.get("total_connections", len(df)),
            "note":               (
                "This dashboard uses inferred market data only. "
                "LinkedIn exports do not include location. "
                "Market classification is based on company name and job title keywords."
            ),
        },
        "kpis":                   kpis,
        "market_distribution":    build_public_market_distribution(df),
        "persona_distribution":   build_public_persona_distribution(df),
        "heatmaps":               build_public_heatmap(df),
        "gap_analysis":           build_public_gap_data(gap_df),
        "action_plan_30":         gap_df[gap_df.get("timeframe", pd.Series()).str.contains("30", na=False)].to_dict(orient="records")
                                  if "timeframe" in gap_df.columns else plan_30.to_dict(orient="records"),
        "action_plan_60":         plan_60.to_dict(orient="records"),
        "action_plan_90":         plan_90.to_dict(orient="records"),
        "top_contacts":           build_public_contacts(df, n=150),
        "company_intel":          build_public_company_intel(df),
        "unknown_companies":      build_unknown_companies_to_classify(df),
    }

    # Save to both locations
    for path in [PUBLIC_JSON_DOCS, PUBLIC_JSON_OUTPUTS]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str, indent=2)
        logger.info(f"Public dashboard JSON saved: {path}")
