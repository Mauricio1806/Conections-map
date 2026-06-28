# -*- coding: utf-8 -*-
"""
lead_reactivation_engine.py
============================
Runs message_intelligence, generates all output CSVs, and returns
a summary dict for the public dashboard JSON.

Outputs (local/private — not committed):
  outputs/message_threads_summary.csv
  outputs/lead_reactivation_backlog.csv
  outputs/recruiter_conversation_history.csv
  outputs/follow_up_due.csv
  outputs/auto_reply_leads.csv
  outputs/warm_leads.csv
  outputs/dormant_leads.csv
  outputs/rejected_or_closed_leads.csv
  outputs/no_response_leads.csv
"""

import logging
from pathlib import Path

import pandas as pd

from src.message_intelligence import MESSAGES_CSV, run_message_intelligence

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"

SAFE_DASHBOARD_COLS = [
    "other_person_name",
    "company_clean",
    "position_clean",
    "persona",
    "strategic_market",
    "conversation_status",
    "lead_temperature",
    "last_message_date",
    "days_since_last_message",
    "total_messages",
    "reactivation_priority_score",
    "recommended_next_action",
    "message_angle",
    "other_person_profile_url",
    "has_positive_signal",
    "has_interview_signal",
    "has_cv_signal",
    "is_auto_reply",
]

RECRUITER_PERSONAS = {
    "Recruiter", "Talent Acquisition", "Sourcer",
    "Hiring Manager", "Engineering Manager",
    "Data Engineering Manager", "Head of Data", "Director",
}


def _save(df: pd.DataFrame, path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"  Saved {label}: {path.name} ({len(df)} rows)")


def run_lead_reactivation_engine(classified_df: pd.DataFrame | None = None) -> dict:
    """
    Main entry point. Returns summary dict for dashboard JSON.
    All outputs are local CSV files — none exposed to public dashboard.
    """
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    messages_available = MESSAGES_CSV.exists()

    if not messages_available:
        logger.warning("  messages.csv not found — generating empty lead reactivation outputs.")
        empty = pd.DataFrame()
        for name in [
            "message_threads_summary", "lead_reactivation_backlog",
            "recruiter_conversation_history", "follow_up_due",
            "auto_reply_leads", "warm_leads", "dormant_leads",
            "rejected_or_closed_leads", "no_response_leads",
        ]:
            _save(empty, OUTPUTS_DIR / f"{name}.csv", name)
        return {"messages_csv_available": False, "total_conversations": 0}

    df = run_message_intelligence(classified_df=classified_df)

    if df.empty:
        logger.warning("  No conversations found in messages.csv.")
        return {"messages_csv_available": True, "total_conversations": 0}

    # Full backlog
    _save(df, OUTPUTS_DIR / "message_threads_summary.csv", "message_threads_summary")

    # Lead reactivation backlog (actionable only)
    actionable_mask = df["lead_temperature"].isin(["Hot", "Warm", "Neutral"]) & \
                      (df["reactivation_priority_score"] > 10)
    backlog = df[actionable_mask].sort_values("reactivation_priority_score", ascending=False)
    _save(backlog, OUTPUTS_DIR / "lead_reactivation_backlog.csv", "lead_reactivation_backlog")

    # Recruiter/TA/HM conversations
    rec_mask = df["persona"].isin(RECRUITER_PERSONAS)
    _save(df[rec_mask], OUTPUTS_DIR / "recruiter_conversation_history.csv", "recruiter_conversation_history")

    # Segmented CSVs
    status_map = {
        "follow_up_due":         df["conversation_status"] == "Follow-up due",
        "auto_reply_leads":      df["conversation_status"] == "Auto-reply / career site redirect",
        "warm_leads":            df["conversation_status"] == "Warm lead",
        "dormant_leads":         df["conversation_status"] == "Dormant warm lead",
        "rejected_or_closed_leads": df["conversation_status"] == "Rejected / closed process",
        "no_response_leads":     df["conversation_status"] == "No response",
    }
    for fname, mask in status_map.items():
        _save(df[mask], OUTPUTS_DIR / f"{fname}.csv", fname)

    # Build summary counts
    counts = df["conversation_status"].value_counts().to_dict()
    temp_counts = df["lead_temperature"].value_counts().to_dict()

    hot_leads     = temp_counts.get("Hot", 0)
    warm_leads    = temp_counts.get("Warm", 0)
    follow_due    = counts.get("Follow-up due", 0)
    needs_reply   = counts.get("Needs my response", 0)
    auto_reply    = counts.get("Auto-reply / career site redirect", 0)
    dormant_warm  = counts.get("Dormant warm lead", 0)
    rejected      = counts.get("Rejected / closed process", 0)
    no_response   = counts.get("No response", 0)

    # Top 50 reactivation contacts (safe columns only for dashboard)
    safe_cols = [c for c in SAFE_DASHBOARD_COLS if c in df.columns]
    top50 = (
        df[df["reactivation_priority_score"] > 0]
        .sort_values("reactivation_priority_score", ascending=False)
        .head(50)[safe_cols]
        .reset_index(drop=True)
    )
    top50_records = top50.to_dict(orient="records")

    # Weekly action plan
    weekly_plan = {
        "Monday":    "Follow up with Hot/Warm recruiters and TA contacts who sent a message",
        "Tuesday":   "Recontact dormant warm leads — reconnect after inactivity",
        "Wednesday": "Submit profiles to talent databases from auto-reply conversations",
        "Thursday":  "Message recruiters with previous job opportunity conversations",
        "Friday":    "Review rejected/closed leads and schedule future follow-ups for 60-day window",
    }

    # Needs reply contacts (top 10)
    needs_reply_contacts = df[
        df["conversation_status"] == "Needs my response"
    ].sort_values("reactivation_priority_score", ascending=False).head(10)
    needs_reply_records = needs_reply_contacts[safe_cols].to_dict(orient="records")

    logger.info(f"  Lead intelligence: {len(df)} conversations | "
                f"Hot={hot_leads} Warm={warm_leads} FollowDue={follow_due} "
                f"NeedsReply={needs_reply}")

    return {
        "messages_csv_available":    True,
        "total_conversations":       int(len(df)),
        "hot_leads":                 int(hot_leads),
        "warm_leads":                int(warm_leads),
        "follow_up_due":             int(follow_due),
        "needs_my_response":         int(needs_reply),
        "auto_reply_leads":          int(auto_reply),
        "dormant_warm_leads":        int(dormant_warm),
        "rejected_closed_reusable":  int(rejected),
        "no_response_leads":         int(no_response),
        "top_reactivation_contacts": top50_records,
        "needs_reply_contacts":      needs_reply_records,
        "weekly_action_plan":        weekly_plan,
    }
