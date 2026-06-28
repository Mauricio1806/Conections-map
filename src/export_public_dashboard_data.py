# -*- coding: utf-8 -*-
"""
export_public_dashboard_data.py  (V3)
======================================
Sanitizes and exports dashboard JSON for static GitHub Pages dashboard.
Uses the new 8-score model from confidence_adjusted_kpis.py.

Privacy rules:
  - No Email Address, phone, or private contact data
  - Only safe fields exposed for top contacts
  - Action coaching fields enriched from persona/market logic
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
    "market_v2", "strategic_market", "market_type",
    "priority_score", "action_type", "message_angle", "why_priority",
    "market_confidence_v2", "company_category", "url",
    # V5 opportunity market fields
    "opportunity_market_v5", "opportunity_bucket",
    "opportunity_confidence", "is_actionable_opportunity",
    # Outreach adjusted scoring (message history intelligence)
    "outreach_adjusted_score", "outreach_status", "outreach_reason",
    "has_message_history", "replied_to_me", "ghosted_me", "auto_reply_only",
    "last_message_date", "days_since_last_message",
    "prior_positive_signal", "prior_rejection",
]

EXCLUDED_PATTERNS = {
    "email address", "email_address", "email",
    "first name", "last name",
    "connected_on", "connected_on_clean",
    "inference_reason", "inference_reason_v2",
    "phone", "mobile",
}


def _is_safe_field(field: str) -> bool:
    return field.lower() not in EXCLUDED_PATTERNS


def _enrich_action_fields(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"

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
            return ("Hi [Name], I'm a Senior Data Engineer open to remote USD roles. "
                    "I specialize in dbt, Spark, Airflow, and cloud platforms. "
                    "Are you working with LATAM or nearshore data profiles?")
        if p == "Talent Acquisition":
            return ("Hi [Name], I'm a Data Engineer available for remote USD opportunities. "
                    "I focus on modern data stacks — would love to be on your radar.")
        if p in ("Hiring Manager", "Engineering Manager"):
            return ("Hi [Name], I saw your team is building out data capabilities. "
                    "I'm a Data Engineer open to remote roles — happy to connect.")
        if p in ("Head of Data", "Director", "Executive"):
            return ("Hi [Name], I follow your work in the data space. "
                    "I'd love to connect and share perspectives on data platform strategy.")
        if m in ("SPAIN_EU", "EUROPE"):
            return ("Hi [Name], I'm planning to relocate to Spain/Europe and building "
                    "my network there. Would be great to connect with data professionals.")
        if m == "GLOBAL_STAFFING":
            return ("Hi [Name], I'm exploring remote/nearshore Data Engineering roles. "
                    "Are you placing data profiles globally?")
        return "Hi [Name], I'd love to connect and exchange experiences in data engineering."

    def _why_priority(row):
        p  = row.get("persona", "")
        m  = row.get(mkt_col, "UNKNOWN")
        cc = row.get("company_category", "OTHER")
        s  = float(row.get("priority_score", 0))
        reasons = []
        if p in ("Recruiter", "Talent Acquisition", "Sourcer"):
            reasons.append("can refer you to open USD roles")
        if p in ("Hiring Manager", "Data Engineering Manager", "Head of Data"):
            reasons.append("has direct hiring authority")
        if m in ("LATAM_USD", "US_CANADA_NEARSHORE"):
            reasons.append("confirmed USD opportunity market")
        if m in ("SPAIN_EU", "EUROPE"):
            reasons.append("confirmed Spain/EU target market")
        if cc == "GLOBAL_STAFFING":
            reasons.append("global staffing — places data engineers remotely")
        if cc == "GLOBAL_TECH":
            reasons.append("global tech company — hires data roles remotely")
        if s >= 70:
            reasons.append(f"top priority score ({s:.0f}/100)")
        if not reasons:
            reasons.append(f"priority score {s:.0f}/100")
        return "; ".join(reasons)

    df["action_type"]   = df.apply(_action_type, axis=1)
    df["message_angle"] = df.apply(_message_angle, axis=1)
    df["why_priority"]  = df.apply(_why_priority, axis=1)
    return df


def build_public_contacts(
    df: pd.DataFrame,
    n: int = 200,
    outreach_scores: dict = None,
) -> list:
    df = _enrich_action_fields(df)

    # Merge outreach scores (by normalized URL)
    if outreach_scores:
        def _norm(url):
            return str(url or "").strip().rstrip("/").lower()

        for col in [
            "outreach_adjusted_score", "outreach_status", "outreach_reason",
            "has_message_history", "replied_to_me", "ghosted_me", "auto_reply_only",
            "last_message_date", "days_since_last_message",
            "prior_positive_signal", "prior_rejection",
        ]:
            df[col] = None

        for idx, row in df.iterrows():
            norm = _norm(row.get("url", ""))
            rec  = outreach_scores.get(norm)
            if rec:
                for col, val in rec.items():
                    df.at[idx, col] = val

        # Fill defaults for contacts with no message history
        df["outreach_adjusted_score"] = (
            df["outreach_adjusted_score"]
            .combine_first(df["priority_score"])
        )
        df["outreach_status"]     = df["outreach_status"].where(df["outreach_status"].notna(), "No History")
        df["has_message_history"] = df["has_message_history"].where(df["has_message_history"].notna(), False)

        sort_col = "outreach_adjusted_score"
    else:
        sort_col = "priority_score"

    safe_cols = [c for c in SAFE_CONTACT_COLS if c in df.columns]
    safe_cols = [c for c in safe_cols if _is_safe_field(c)]

    top = (
        df.sort_values(sort_col, ascending=False)
        .head(n)[safe_cols]
        .reset_index(drop=True)
    )
    return top.to_dict(orient="records")


def build_public_market_distribution(df: pd.DataFrame) -> dict:
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"
    return df[mkt_col].value_counts().to_dict()


def build_public_persona_distribution(df: pd.DataFrame) -> dict:
    return df["persona"].value_counts().head(15).to_dict()


def build_public_heatmap(df: pd.DataFrame) -> dict:
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
                "labels":  list(pivot.index),
                "columns": list(pivot.columns),
                "data":    pivot.values.tolist(),
            }
        except Exception:
            return {"labels": [], "columns": [], "data": []}

    return {
        "persona_market":   _pivot("persona", mkt_col),
        "area_market":      _pivot("area", mkt_col),
        "seniority_market": _pivot("seniority", mkt_col),
        "persona_priority": _build_persona_priority(df),
    }


def _build_persona_priority(df: pd.DataFrame) -> dict:
    try:
        df2 = df.copy()
        df2["priority_band"] = pd.cut(
            df2["priority_score"],
            bins=[-1, 39, 69, 100],
            labels=["Low (<40)", "Medium (40-69)", "High (≥70)"]
        ).astype(str)
        pivot = (
            df2.groupby(["persona", "priority_band"])
            .size()
            .reset_index(name="count")
            .pivot(index="persona", columns="priority_band", values="count")
            .fillna(0).astype(int)
        )
        return {
            "labels":  list(pivot.index),
            "columns": list(pivot.columns),
            "data":    pivot.values.tolist(),
        }
    except Exception:
        return {"labels": [], "columns": [], "data": []}


def build_public_gap_data(gap_df: pd.DataFrame) -> list:
    safe_cols = [
        "market", "persona", "current_count", "target_count",
        "gap_count", "gap_percentage", "urgency_level", "timeframe",
        "strategic_reason", "recommended_action",
    ]
    safe_cols = [c for c in safe_cols if c in gap_df.columns]
    return gap_df[safe_cols].to_dict(orient="records")


def build_public_company_intel(df: pd.DataFrame) -> dict:
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"

    def _top(mask=None, n=25):
        src = df if mask is None else df[mask]
        src = src[src["company_clean"].str.strip() != ""]
        return (
            src["company_clean"].value_counts().head(n)
            .reset_index()
            .rename(columns={"company_clean": "company", "count": "count"})
            .to_dict(orient="records")
        )

    def _by_cat(cat):
        return _top(df["company_category"] == cat, n=20)

    return {
        "all_companies":   _top(n=30),
        "recruiting":      _top(df["persona"].isin(
            {"Recruiter", "Talent Acquisition", "Sourcer"}), n=20),
        "data_companies":  _top(df["area"].isin(
            {"Data Engineering", "Analytics", "BI", "Data Science / AI"}), n=20),
        "latam_usd":       _top(df[mkt_col] == "LATAM_USD", n=20),
        "spain_eu":        _top(df[mkt_col].isin({"SPAIN_EU", "EUROPE"}), n=20),
        "global_staffing": _by_cat("GLOBAL_STAFFING"),
        "global_tech":     _by_cat("GLOBAL_TECH"),
        "global_consulting": _by_cat("GLOBAL_CONSULTING"),
        "unknown_top":     _top(df[mkt_col] == "UNKNOWN", n=30),
    }


def build_v5_distribution_public(df: pd.DataFrame) -> dict:
    """V5 opportunity market distribution for the public dashboard."""
    if "opportunity_market_v5" not in df.columns:
        return {}
    return df["opportunity_market_v5"].value_counts().to_dict()


def build_unknown_companies_public(df: pd.DataFrame) -> list:
    """Top unknown companies for the 'classify unknown' tab."""
    mkt_col  = "market_v2" if "market_v2" in df.columns else "strategic_market"
    unk_mask = (df[mkt_col] == "UNKNOWN") & (df["company_clean"].str.strip() != "")
    unk = df[unk_mask]

    agg = (
        unk.groupby("company_clean")
        .agg(
            connection_count    = ("company_clean", "size"),
            recruiter_count     = ("persona", lambda x: x.isin(
                {"Recruiter", "Talent Acquisition", "Sourcer"}).sum()),
            hiring_mgr_count    = ("persona", lambda x: x.isin(
                {"Hiring Manager", "Engineering Manager"}).sum()),
            data_leader_count   = ("persona", lambda x: x.isin(
                {"Data Engineering Manager", "Head of Data", "Director"}).sum()),
            top_persona         = ("persona", lambda x: x.mode()[0] if len(x) > 0 else ""),
            avg_priority_score  = ("priority_score", "mean"),
        )
        .reset_index()
        .sort_values("connection_count", ascending=False)
        .head(50)
    )
    agg["avg_priority_score"] = agg["avg_priority_score"].round(1)
    return agg.to_dict(orient="records")


SAFE_LEAD_COLS = {
    "other_person_name", "company_clean", "position_clean", "persona",
    "strategic_market", "conversation_status", "lead_category", "lead_temperature",
    "last_message_date", "days_since_last_message", "total_messages",
    "reactivation_priority_score", "recommended_next_action",
    "message_angle", "other_person_profile_url",
    "has_positive_signal", "has_interview_signal", "has_cv_signal", "is_auto_reply",
}

SAFE_LEAD_SUMMARY_KEYS = {
    "messages_csv_available", "total_conversations",
    "hot_reactivation_leads", "warm_reactivation_leads",
    "needs_my_response", "career_site_follow_ups",
    "follow_up_due", "dormant_warm_leads",
    "rejected_closed_reusable", "no_response_leads",
    "this_week_count", "weekly_action_plan",
    # legacy
    "hot_leads", "warm_leads",
    # contact lists
    "top_reactivation_contacts", "this_week_contacts", "needs_reply_contacts",
}


def _sanitize_lead_contacts(contacts: list) -> list:
    return [
        {k: v for k, v in c.items() if k in SAFE_LEAD_COLS}
        for c in (contacts or [])
    ]


def _load_existing_lead_data() -> dict | None:
    """
    Load lead_reactivation section from the committed JSON if it has real data.
    Used to preserve local-generated lead data when messages.csv is absent.
    """
    for path in [PUBLIC_JSON_DOCS, PUBLIC_JSON_OUTPUTS]:
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                lr = data.get("lead_reactivation", {})
                if lr.get("messages_csv_available") and lr.get("total_conversations", 0) > 0:
                    return lr
            except Exception:
                pass
    return None


def build_lead_reactivation_public(lead_data: dict) -> dict:
    """Strip any private fields from lead reactivation data for public JSON."""
    if not lead_data:
        return {"messages_csv_available": False}

    safe_summary = {k: lead_data[k] for k in SAFE_LEAD_SUMMARY_KEYS if k in lead_data}

    safe_summary["top_reactivation_contacts"] = _sanitize_lead_contacts(
        lead_data.get("top_reactivation_contacts", [])
    )
    safe_summary["this_week_contacts"] = _sanitize_lead_contacts(
        lead_data.get("this_week_contacts", [])
    )
    safe_summary["needs_reply_contacts"] = _sanitize_lead_contacts(
        lead_data.get("needs_reply_contacts", [])
    )
    return safe_summary


def export_public_dashboard_data(
    df:      pd.DataFrame,
    kpis:    dict,
    gap_df:  pd.DataFrame,
    plan_30: pd.DataFrame,
    plan_60: pd.DataFrame,
    plan_90: pd.DataFrame,
    resolution_data: dict  = None,
    lead_data: dict        = None,
    v5_data: dict          = None,
    outreach_scores: dict  = None,
) -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # Determine lead_reactivation section:
    # If messages.csv was present this run → build fresh
    # If not (messages_csv_available == False) → preserve existing committed data
    messages_available = lead_data and lead_data.get("messages_csv_available", False)
    if messages_available:
        lead_section = build_lead_reactivation_public(lead_data)
    else:
        existing = _load_existing_lead_data()
        if existing:
            logger.info("  Preserving existing lead_reactivation data (messages.csv not present this run)")
            lead_section = existing
        else:
            lead_section = {"messages_csv_available": False}

    payload = {
        "meta": {
            "report_date":      str(date.today()),
            "total_connections": kpis.get("total_connections", len(df)),
            "production_url":   "https://mauricio1806.github.io/Conections-map/",
            "note": (
                "Market classification is inferred from company/title keywords. "
                "LinkedIn exports do not include location data. "
                "UNKNOWN market does not mean the connection has no strategic value."
            ),
        },
        "kpis":               kpis,
        "market_distribution":build_public_market_distribution(df),
        "persona_distribution":build_public_persona_distribution(df),
        "heatmaps":           build_public_heatmap(df),
        "gap_analysis":       build_public_gap_data(gap_df),
        "action_plan_30":     plan_30.to_dict(orient="records"),
        "action_plan_60":     plan_60.to_dict(orient="records"),
        "action_plan_90":     plan_90.to_dict(orient="records"),
        "top_contacts":                build_public_contacts(df, n=200, outreach_scores=outreach_scores),
        "company_intel":               build_public_company_intel(df),
        "unknown_companies":           build_unknown_companies_public(df),
        "unknown_resolution":          resolution_data or {},
        "lead_reactivation":           lead_section,
        # V5 Opportunity Market (replaces UNKNOWN as primary business view)
        "opportunity_market_v5":       build_v5_distribution_public(df),
        "opportunity_market_v5_summary": v5_data or {},
    }

    for path in [PUBLIC_JSON_DOCS, PUBLIC_JSON_OUTPUTS]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str, indent=2)
        logger.info(f"Public dashboard JSON saved: {path.name} "
                    f"({path.stat().st_size // 1024} KB)")
