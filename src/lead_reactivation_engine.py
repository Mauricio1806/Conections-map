# -*- coding: utf-8 -*-
"""
lead_reactivation_engine.py  (V2 — corrective patch)
======================================================
Runs message_intelligence, generates segmented CSV outputs,
and returns a summary dict for the public dashboard JSON.

Key fixes vs V1:
  - When messages.csv is missing: returns {"messages_csv_available": False}
    WITHOUT overwriting existing data (export layer preserves it)
  - Weekly action limits: max 20 hot/warm, max 10 career site, max 10 dormant
  - New outputs: this_week, hot, warm, career_site, ignore
  - lead_category field in all outputs
  - Safe dashboard columns include lead_category and profile_url

Outputs (all local/private — never committed):
  outputs/message_threads_summary.csv
  outputs/lead_reactivation_backlog.csv
  outputs/lead_reactivation_this_week.csv
  outputs/lead_reactivation_hot.csv
  outputs/lead_reactivation_warm.csv
  outputs/lead_reactivation_career_site.csv
  outputs/lead_reactivation_ignore.csv
  outputs/recruiter_conversation_history.csv
  outputs/follow_up_due.csv
  outputs/warm_leads.csv
  outputs/dormant_leads.csv
  outputs/rejected_or_closed_leads.csv
  outputs/no_response_leads.csv
"""

import logging
from pathlib import Path

import pandas as pd

from src.message_intelligence import MESSAGES_CSV, RECRUITER_PERSONAS, run_message_intelligence

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"

WEEKLY_LIMITS = {
    "hot_warm":    20,   # hot + warm reactivation leads
    "career_site": 10,   # career site follow-ups
    "dormant":     10,   # dormant warm leads
    "needs_reply": 15,   # needs my response (no real limit but cap to 15)
}

SAFE_DASHBOARD_COLS = [
    "other_person_name",
    "other_person_profile_url",
    "company_clean",
    "position_clean",
    "persona",
    "strategic_market",
    "conversation_status",
    "lead_category",
    "lead_temperature",
    "last_message_date",
    "days_since_last_message",
    "total_messages",
    "reactivation_priority_score",
    "recommended_next_action",
    "message_angle",
    "has_positive_signal",
    "has_interview_signal",
    "has_cv_signal",
    "is_auto_reply",
]


def _save(df: pd.DataFrame, path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"  Saved {label}: {path.name} ({len(df)} rows)")


def _safe_records(df: pd.DataFrame) -> list:
    cols = [c for c in SAFE_DASHBOARD_COLS if c in df.columns]
    return df[cols].to_dict(orient="records")


def _build_this_week_queue(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the weekly action queue with limits.
    Priority order: Needs my response → Hot/Warm → Career site → Dormant.
    """
    frames = []

    # 1. Needs my response (up to limit)
    needs = df[df["conversation_status"] == "Needs my response"].sort_values(
        "reactivation_priority_score", ascending=False
    ).head(WEEKLY_LIMITS["needs_reply"])
    frames.append(needs)

    # 2. Hot + Warm reactivation leads (up to limit, excluding already added)
    added_ids = set(needs["conversation_id"]) if "conversation_id" in needs.columns else set()
    hot_warm_mask = (
        df["lead_category"].isin(["Hot reactivation lead", "Warm reactivation lead"]) &
        df["conversation_status"].isin(["Follow-up due", "Warm lead"])
    )
    if "conversation_id" in df.columns:
        hot_warm_mask = hot_warm_mask & ~df["conversation_id"].isin(added_ids)
    hot_warm = df[hot_warm_mask].sort_values(
        "reactivation_priority_score", ascending=False
    ).head(WEEKLY_LIMITS["hot_warm"])
    frames.append(hot_warm)
    if "conversation_id" in hot_warm.columns:
        added_ids.update(hot_warm["conversation_id"])

    # 3. Career site follow-ups (up to limit)
    career_mask = df["lead_category"] == "Career site follow-up"
    if "conversation_id" in df.columns:
        career_mask = career_mask & ~df["conversation_id"].isin(added_ids)
    career = df[career_mask].sort_values(
        "reactivation_priority_score", ascending=False
    ).head(WEEKLY_LIMITS["career_site"])
    frames.append(career)
    if "conversation_id" in career.columns:
        added_ids.update(career["conversation_id"])

    # 4. Dormant warm leads (up to limit)
    dormant_mask = df["conversation_status"] == "Dormant warm lead"
    if "conversation_id" in df.columns:
        dormant_mask = dormant_mask & ~df["conversation_id"].isin(added_ids)
    dormant = df[dormant_mask].sort_values(
        "reactivation_priority_score", ascending=False
    ).head(WEEKLY_LIMITS["dormant"])
    frames.append(dormant)

    if not any(len(f) > 0 for f in frames):
        return pd.DataFrame()

    result = pd.concat([f for f in frames if len(f) > 0], ignore_index=True)
    result = result.drop_duplicates(
        subset=["conversation_id"] if "conversation_id" in result.columns else None
    )
    return result.sort_values("reactivation_priority_score", ascending=False).reset_index(drop=True)


def run_lead_reactivation_engine(classified_df: pd.DataFrame | None = None) -> dict:
    """
    Main entry point. Returns summary dict for dashboard JSON.
    If messages.csv is not present, returns sentinel that tells
    export layer to PRESERVE existing lead data.
    """
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    if not MESSAGES_CSV.exists():
        logger.info("  messages.csv not found — lead data will be preserved from existing JSON.")
        return {"messages_csv_available": False}

    df = run_message_intelligence(classified_df=classified_df)

    if df.empty:
        logger.warning("  No conversations parsed from messages.csv.")
        return {"messages_csv_available": True, "total_conversations": 0}

    # ── Save full summary ─────────────────────────────────────────────────────
    _save(df, OUTPUTS_DIR / "message_threads_summary.csv", "message_threads_summary")

    # ── Recruiter conversations ───────────────────────────────────────────────
    rec_mask = df["persona"].isin(RECRUITER_PERSONAS)
    _save(df[rec_mask], OUTPUTS_DIR / "recruiter_conversation_history.csv", "recruiter_conversations")

    # ── Segmented by status ───────────────────────────────────────────────────
    seg_map = {
        "follow_up_due":           df["conversation_status"] == "Follow-up due",
        "warm_leads":              df["conversation_status"] == "Warm lead",
        "dormant_leads":           df["conversation_status"] == "Dormant warm lead",
        "rejected_or_closed_leads":df["conversation_status"] == "Rejected / closed process",
        "no_response_leads":       df["conversation_status"] == "No response",
    }
    for fname, mask in seg_map.items():
        _save(df[mask], OUTPUTS_DIR / f"{fname}.csv", fname)

    # ── Segmented by lead_category ────────────────────────────────────────────
    _save(
        df[df["lead_category"].isin(["Hot reactivation lead", "Needs my response"])],
        OUTPUTS_DIR / "lead_reactivation_hot.csv",
        "lead_reactivation_hot",
    )
    _save(
        df[df["lead_category"] == "Warm reactivation lead"],
        OUTPUTS_DIR / "lead_reactivation_warm.csv",
        "lead_reactivation_warm",
    )
    _save(
        df[df["lead_category"] == "Career site follow-up"],
        OUTPUTS_DIR / "lead_reactivation_career_site.csv",
        "lead_reactivation_career_site",
    )
    _save(
        df[df["lead_category"] == "Ignore"],
        OUTPUTS_DIR / "lead_reactivation_ignore.csv",
        "lead_reactivation_ignore",
    )

    # ── This week queue (with limits) ─────────────────────────────────────────
    this_week = _build_this_week_queue(df)
    _save(this_week, OUTPUTS_DIR / "lead_reactivation_this_week.csv", "lead_reactivation_this_week")

    # ── Full backlog (all actionable) ─────────────────────────────────────────
    backlog_mask = df["lead_category"] != "Ignore"
    backlog = df[backlog_mask].sort_values("reactivation_priority_score", ascending=False)
    _save(backlog, OUTPUTS_DIR / "lead_reactivation_backlog.csv", "lead_reactivation_backlog")

    # ── Counts ────────────────────────────────────────────────────────────────
    cat_counts  = df["lead_category"].value_counts().to_dict()
    stat_counts = df["conversation_status"].value_counts().to_dict()
    temp_counts = df["lead_temperature"].value_counts().to_dict()

    hot_count        = int(cat_counts.get("Hot reactivation lead", 0))
    warm_count       = int(cat_counts.get("Warm reactivation lead", 0))
    needs_reply      = int(cat_counts.get("Needs my response", 0))
    # "Needs my response" contacts are the hottest leads — include them in hot count
    # so the dashboard pipeline card reflects urgency correctly
    hot_count = hot_count + needs_reply
    career_site      = int(cat_counts.get("Career site follow-up", 0))
    dormant_warm     = int(stat_counts.get("Dormant warm lead", 0))
    follow_due       = int(stat_counts.get("Follow-up due", 0))
    rejected         = int(stat_counts.get("Rejected / closed process", 0))
    no_response      = int(stat_counts.get("No response", 0))
    this_week_count  = int(len(this_week))

    # ── Top 50 reactivation contacts (safe fields only) ───────────────────────
    top50_records = _safe_records(
        df[df["lead_category"] != "Ignore"]
        .sort_values("reactivation_priority_score", ascending=False)
        .head(50)
        .reset_index(drop=True)
    )

    # ── This week queue (safe fields) ─────────────────────────────────────────
    this_week_records = _safe_records(this_week)

    # ── Needs reply (top 15, safe fields) ─────────────────────────────────────
    needs_reply_records = _safe_records(
        df[df["conversation_status"] == "Needs my response"]
        .sort_values("reactivation_priority_score", ascending=False)
        .head(15)
        .reset_index(drop=True)
    )

    weekly_plan = {
        "Monday":    "Reply to 'Needs my response' contacts (check leads-reply queue)",
        "Tuesday":   "Follow up with Hot reactivation leads from this week's queue",
        "Wednesday": "Submit CV to career site leads (up to 10)",
        "Thursday":  "Recontact Warm reactivation leads and dormant warm leads",
        "Friday":    "Review Warm leads backlog and tag any previous process reusable",
    }

    logger.info(
        f"  Lead intelligence V2: {len(df)} conversations | "
        f"Hot={hot_count} Warm={warm_count} NeedsReply={needs_reply} "
        f"FollowDue={follow_due} CareerSite={career_site} ThisWeek={this_week_count}"
    )

    return {
        "messages_csv_available":    True,
        "total_conversations":       int(len(df)),
        "hot_reactivation_leads":    hot_count,
        "warm_reactivation_leads":   warm_count,
        "needs_my_response":         needs_reply,
        "career_site_follow_ups":    career_site,
        "follow_up_due":             follow_due,
        "dormant_warm_leads":        dormant_warm,
        "rejected_closed_reusable":  rejected,
        "no_response_leads":         no_response,
        "this_week_count":           this_week_count,
        "top_reactivation_contacts": top50_records,
        "this_week_contacts":        this_week_records,
        "needs_reply_contacts":      needs_reply_records,
        "weekly_action_plan":        weekly_plan,
        # Legacy keys for backward compat with JS
        "hot_leads":   hot_count,
        "warm_leads":  warm_count,
    }
