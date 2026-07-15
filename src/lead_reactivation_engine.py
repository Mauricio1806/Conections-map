# -*- coding: utf-8 -*-
"""
lead_reactivation_engine.py  (V2 — corrective patch; V6 — response intelligence)
==================================================================================
Runs message_intelligence, generates segmented CSV outputs,
and returns a summary dict for the public dashboard JSON.

Key fixes vs V1:
  - When messages.csv is missing: returns {"messages_csv_available": False}
    WITHOUT overwriting existing data (export layer preserves it)
  - Weekly action limits: max 20 hot/warm, max 10 career site, max 10 dormant
  - New outputs: this_week, hot, warm, career_site, ignore
  - lead_category field in all outputs
  - Safe dashboard columns include lead_category and profile_url

V6 additions (CLAUDE_INTELLIGENCE_V6_PATCH.md Parts 1, 5, 8):
  - lead_category now uses the refined 12-category taxonomy (Part 5):
    "Needs my response — Confirmed/Likely", "Ambiguous — Review",
    "Hot/Warm reactivation", "Dormant warm", "Career site follow-up",
    "Previous process reusable", "Follow-up candidate", "No response",
    "Closed / no action", "Ignore".
  - The old inflated "Needs my response" count is replaced by
    needs_my_response_confirmed + needs_my_response_likely, both requiring a
    substantive actionable signal (not just "other person sent last").
  - New small manual-review queue: outputs/message_review_queue.csv
    (only "Ambiguous — Review" cases — a handful, not the whole backlog).
  - conversation_status (legacy) is UNCHANGED so outreach_adjusted_scoring.py
    and downstream consumers keep working exactly as before.

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
  outputs/message_review_queue.csv
"""

import logging
from pathlib import Path

import pandas as pd

from src.message_intelligence import MESSAGES_CSV, RECRUITER_PERSONAS, run_message_intelligence
from src.company_normalizer import normalize as normalize_company

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
    # V6 response intelligence — sanitized fields only, no raw content
    "needs_my_response",
    "needs_response_confidence",
    "needs_response_reason",
    "response_intent_score",
    "manual_review_required",
    "last_sender_type",
    "conversation_recency_band",
    "sanitized_intent_label",
    # V8 multi-dimensional conversation state — sanitized fields only, no raw content
    "process_state",
    "relationship_state",
    "reply_obligation",
    "action_urgency",
    "closure_reason",
    "next_action_date",
    "reactivation_window_days",
    "relationship_value_score",
    "immediate_action_score",
    "conversation_state_confidence",
    "state_evidence_codes",
    "external_action_type",
    "request_resolved",
    "cooldown_state",
]

# Ambiguous manual-review queue fields (Part 8) — sanitized, no raw content
REVIEW_QUEUE_COLS = [
    "other_person_name", "company_clean", "persona",
    "last_message_date", "days_since_last_message",
    "inferred_status", "needs_response_confidence", "response_intent_score",
    "sanitized_intent_label", "reason", "manual_status", "manual_action",
]

# V8 conversation-state review queue fields (Part 17) — sanitized, no raw content
STATE_REVIEW_QUEUE_COLS = [
    "other_person_name", "company_clean", "persona",
    "current_state", "proposed_state", "confidence",
    "evidence_codes", "review_reason",
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
    Priority order: Needs my response (Confirmed → Likely) → Hot/Warm → Career site → Dormant.
    """
    frames = []

    # 1. Needs my response — Confirmed + Likely (up to limit)
    needs = df[df["lead_category"].isin(
        ["Needs my response — Confirmed", "Needs my response — Likely"]
    )].sort_values(
        ["lead_category", "reactivation_priority_score"], ascending=[True, False]
    ).head(WEEKLY_LIMITS["needs_reply"])
    frames.append(needs)

    # 2. Active Interview Pipeline + Warm reactivation leads (up to limit, excluding already added)
    added_ids = set(needs["conversation_id"]) if "conversation_id" in needs.columns else set()
    hot_warm_mask = df["lead_category"].isin(["Active Interview Pipeline", "Warm reactivation"])
    if "conversation_id" in df.columns:
        hot_warm_mask = hot_warm_mask & ~df["conversation_id"].isin(added_ids)
    hot_warm = df[hot_warm_mask].sort_values(
        "reactivation_priority_score", ascending=False
    ).head(WEEKLY_LIMITS["hot_warm"])
    frames.append(hot_warm)
    if "conversation_id" in hot_warm.columns:
        added_ids.update(hot_warm["conversation_id"])

    # 3. Talent pool / career site follow-ups (up to limit)
    career_mask = df["lead_category"] == "Talent Pool / Career Site"
    if "conversation_id" in df.columns:
        career_mask = career_mask & ~df["conversation_id"].isin(added_ids)
    career = df[career_mask].sort_values(
        "reactivation_priority_score", ascending=False
    ).head(WEEKLY_LIMITS["career_site"])
    frames.append(career)
    if "conversation_id" in career.columns:
        added_ids.update(career["conversation_id"])

    # 4. Dormant warm leads / Reactivate This Month (up to limit) — Part 16
    # cooldown logic means a freshly-rejected contact never lands here; only
    # contacts whose cooldown has actually cleared do.
    dormant_mask = df["lead_category"].isin(["Dormant warm", "Reactivate This Month"])
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


def build_company_warm_signal_map(df: pd.DataFrame) -> dict:
    """
    Company-level aggregate (Untapped Outreach Scoring V9): has this company
    already produced a warm lead / interview / positive reply, or conversely
    only rejected/closed outcomes? Consumed by untapped_network_intelligence.py
    as a cross-signal for never-contacted people at the SAME company — e.g. if
    Company X already replied warmly to one conversation, a different,
    never-contacted recruiter at Company X is a better bet.

    Internal-only aggregate (company name -> booleans) — never published to
    the public dashboard JSON, no raw message content.
    """
    if df is None or df.empty or "company_clean" not in df.columns:
        return {}

    warm_categories = {
        "Active Interview Pipeline", "Warm reactivation",
        "Needs my response — Confirmed", "Needs my response — Likely",
    }
    warm_mask = df["lead_category"].isin(warm_categories)
    if "has_positive_signal" in df.columns:
        warm_mask = warm_mask | df["has_positive_signal"].astype(bool)
    if "has_interview_signal" in df.columns:
        warm_mask = warm_mask | df["has_interview_signal"].astype(bool)
    rejection_mask = df["conversation_status"] == "Rejected / closed process"

    signal_map: dict[str, dict] = {}
    for company, idx in df.groupby("company_clean").groups.items():
        norm = normalize_company(str(company or ""))
        if not norm:
            continue
        signal_map[norm] = {
            "has_warm_signal": bool(warm_mask.loc[idx].any()),
            "has_rejection_signal": bool(rejection_mask.loc[idx].any() and not warm_mask.loc[idx].any()),
        }
    return signal_map


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

    # ── Segmented by lead_category (V8 conversation-state taxonomy — Part 15) ──
    _save(
        df[df["lead_category"].isin([
            "Needs my response — Confirmed", "Needs my response — Likely", "Active Interview Pipeline",
        ])],
        OUTPUTS_DIR / "lead_reactivation_hot.csv",
        "lead_reactivation_hot",
    )
    _save(
        df[df["lead_category"] == "Warm reactivation"],
        OUTPUTS_DIR / "lead_reactivation_warm.csv",
        "lead_reactivation_warm",
    )
    _save(
        df[df["lead_category"] == "Talent Pool / Career Site"],
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

    # ── Ambiguous manual-review queue (Part 8) — small queue, NOT the whole backlog ──
    review_mask = df["lead_category"] == "Ambiguous — Review"
    review_df = df[review_mask].copy()
    if not review_df.empty:
        review_df["inferred_status"] = review_df["lead_category"]
        review_df["reason"] = review_df["needs_response_reason"]
        review_df["manual_status"] = ""
        review_df["manual_action"] = ""
        review_df = review_df.sort_values("response_intent_score", ascending=False)
        review_out = review_df[[c for c in REVIEW_QUEUE_COLS if c in review_df.columns]]
    else:
        review_out = pd.DataFrame(columns=REVIEW_QUEUE_COLS)
    _save(review_out, OUTPUTS_DIR / "message_review_queue.csv", "message_review_queue")

    # ── V8 conversation-state review queue (Part 17) — same ambiguous cohort,
    # framed as current (legacy) vs proposed (V8) state for manual review ──────
    state_review_df = df[review_mask].copy()
    if not state_review_df.empty:
        state_review_df["current_state"]  = state_review_df["conversation_status"]
        state_review_df["proposed_state"] = state_review_df["process_state"]
        state_review_df["confidence"]     = state_review_df["conversation_state_confidence"]
        state_review_df["evidence_codes"] = state_review_df["state_evidence_codes"]
        state_review_df["review_reason"]  = state_review_df["needs_response_reason"]
        state_review_df = state_review_df.sort_values("immediate_action_score", ascending=False)
        state_review_out = state_review_df[[c for c in STATE_REVIEW_QUEUE_COLS if c in state_review_df.columns]]
    else:
        state_review_out = pd.DataFrame(columns=STATE_REVIEW_QUEUE_COLS)
    _save(state_review_out, OUTPUTS_DIR / "conversation_state_review_queue.csv", "conversation_state_review_queue")

    # ── Counts ────────────────────────────────────────────────────────────────
    cat_counts  = df["lead_category"].value_counts().to_dict()
    stat_counts = df["conversation_status"].value_counts().to_dict()
    temp_counts = df["lead_temperature"].value_counts().to_dict()

    needs_confirmed  = int(cat_counts.get("Needs my response — Confirmed", 0))
    needs_likely     = int(cat_counts.get("Needs my response — Likely", 0))
    needs_reply      = needs_confirmed + needs_likely  # honest replacement for the old inflated count
    ambiguous_review = int(cat_counts.get("Ambiguous — Review", 0))
    active_interview_pipeline = int(cat_counts.get("Active Interview Pipeline", 0))
    awaiting_recruiter_update = int(cat_counts.get("Awaiting Recruiter Update", 0))
    hot_count        = active_interview_pipeline + needs_confirmed  # legacy alias
    warm_count       = int(cat_counts.get("Warm reactivation", 0))
    career_site      = int(cat_counts.get("Talent Pool / Career Site", 0))
    dormant_warm     = int(cat_counts.get("Dormant warm", 0))
    reactivate_this_month = int(cat_counts.get("Reactivate This Month", 0))
    location_eligibility_blocked = int(cat_counts.get("Location / Eligibility Blocked", 0))
    rejected_closed  = int(cat_counts.get("Rejected / Closed", 0))
    follow_up_candidate = int(cat_counts.get("Follow-up candidate", 0))
    previous_process_reusable = int(cat_counts.get("Previous process reusable", 0))
    closed_no_action = int(cat_counts.get("Closed / no action", 0))
    follow_due       = int(stat_counts.get("Follow-up due", 0))
    rejected         = rejected_closed or int(stat_counts.get("Rejected / closed process", 0))
    no_response      = int(cat_counts.get("No response", 0))
    this_week_count  = int(len(this_week))
    review_queue_count = int(len(review_out))

    # False-urgent check (Part 19): terminal/blocking states whose
    # immediate_action_score is still above the "urgent" threshold would be a
    # bug — should always be 0 after the Part 4 terminal-state cap.
    terminal_mask = df["process_state"].isin([
        "REJECTED_CLOSED", "LOCATION_ELIGIBILITY_BLOCKED", "GEOGRAPHIC_HIRING_RESTRICTION",
        "WORK_AUTHORIZATION_BLOCKED", "TALENT_POOL_REDIRECT", "CAREER_SITE_REDIRECT",
        "AUTO_REPLY_ONLY", "GENERIC_ACKNOWLEDGEMENT",
    ])
    false_urgent_count = int(((df["immediate_action_score"] >= 30) & terminal_mask).sum()) if "immediate_action_score" in df.columns else 0

    # ── All actionable reactivation contacts (safe fields only) ───────────────
    # V7 corrective patch: this used to be capped at head(50), which made the
    # Lead Reactivation filter bar and KPI-card click-through unable to
    # actually retrieve the contacts behind categories like "Dormant warm"
    # (197) or "Follow-up candidate" (732) — filtering only ever searched
    # within the top 50 by score. Exporting the FULL non-Ignore backlog (still
    # sanitized/no raw content) is what makes every KPI card and filter
    # combination return a result set that matches its displayed count.
    top50_records = _safe_records(
        df[df["lead_category"] != "Ignore"]
        .sort_values("reactivation_priority_score", ascending=False)
        .reset_index(drop=True)
    )

    # ── This week queue (safe fields) ─────────────────────────────────────────
    this_week_records = _safe_records(this_week)

    # ── Needs reply (top 15, safe fields; Confirmed first, then Likely) ──────
    needs_reply_records = _safe_records(
        df[df["lead_category"].isin(["Needs my response — Confirmed", "Needs my response — Likely"])]
        .sort_values(["lead_category", "reactivation_priority_score"], ascending=[True, False])
        .head(15)
        .reset_index(drop=True)
    )

    weekly_plan = {
        "Monday":    "Reply to 'Needs my response — Confirmed' contacts first (check leads-reply queue)",
        "Tuesday":   "Reply to 'Needs my response — Likely' + follow up with Hot reactivation leads",
        "Wednesday": "Submit CV to career site leads (up to 10)",
        "Thursday":  "Recontact Warm reactivation leads and dormant warm leads",
        "Friday":    "Clear the small Ambiguous — Review queue (outputs/message_review_queue.csv)",
    }

    logger.info(
        f"  Lead intelligence V8: {len(df)} conversations | "
        f"NeedsConfirmed={needs_confirmed} NeedsLikely={needs_likely} Ambiguous={ambiguous_review} "
        f"ActiveInterview={active_interview_pipeline} AwaitingUpdate={awaiting_recruiter_update} "
        f"Warm={warm_count} RejectedClosed={rejected} LocationBlocked={location_eligibility_blocked} "
        f"TalentPool={career_site} ReactivateThisMonth={reactivate_this_month} "
        f"FollowDue={follow_due} ThisWeek={this_week_count} ReviewQueue={review_queue_count} "
        f"FalseUrgent={false_urgent_count}"
    )

    return {
        "messages_csv_available":    True,
        "total_conversations":       int(len(df)),
        "hot_reactivation_leads":    hot_count,
        "warm_reactivation_leads":   warm_count,
        "needs_my_response":         needs_reply,
        "needs_my_response_confirmed": needs_confirmed,
        "needs_my_response_likely":  needs_likely,
        "ambiguous_review_count":    ambiguous_review,
        "message_review_queue_count": review_queue_count,
        "follow_up_candidate":       follow_up_candidate,
        "previous_process_reusable": previous_process_reusable,
        "closed_no_action":          closed_no_action,
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
        # V8 conversation-state KPI cards (Part 15)
        "active_interview_pipeline":      active_interview_pipeline,
        "awaiting_recruiter_update":      awaiting_recruiter_update,
        "rejected_closed":                rejected_closed,
        "location_eligibility_blocked":   location_eligibility_blocked,
        "talent_pool_career_site":        career_site,
        "reactivate_this_month":          reactivate_this_month,
        "false_urgent_terminal_state_count": false_urgent_count,
        "conversation_state_review_queue_count": int(len(state_review_out)),
        # Legacy keys for backward compat with JS
        "hot_leads":   hot_count,
        "warm_leads":  warm_count,
        # Internal-only (Untapped Outreach Scoring V9) — never published to the
        # public dashboard JSON, see export_public_dashboard_data.py.
        "company_signal_map": build_company_warm_signal_map(df),
    }
