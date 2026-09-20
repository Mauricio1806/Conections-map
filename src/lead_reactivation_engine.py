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
    # Lead Reactivation trust layer (Part 1) — sanitized explain fields
    "reply_obligation_confidence",
    "reply_reason_short",
    "action_priority_reason",
    "terminal_state_flag",
    "stale_conversation_flag",
    "recruiter_priority_flag",
    # Response Priority & Lead Quality (Part 21) — separates "do I owe a
    # reply" from "is this a good lead" from "does this deserve action now".
    "reply_obligation_flag",
    "lead_quality_score",
    "response_priority_score",
    "response_queue_segment",
    "low_value_reply_flag",
    "courtesy_only_flag",
    "ghost_or_vacuum_flag",
    "terminal_low_action_flag",
    "hard_rejection_flag",
    "talent_pool_only_flag",
    "recommended_response_timing",
    "response_reason_short",
    "active_process_signal_flag",
    "usd_latam_signal_flag",
    # Part 22 — USD Remote / Location Fit — sanitized fields only, no raw content
    "remote_usd_fit_score",
    "location_fit_score",
    "onsite_or_local_only_flag",
    "mexico_local_only_flag",
    "presencial_only_flag",
    "hybrid_local_only_flag",
    "country_restriction_flag",
    "useless_for_usd_remote_flag",
    "usd_remote_priority_score",
    "lead_disqualification_reason",
    "sourcing_quality_segment",
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


# Response Priority & Lead Quality (Part 21) — the This Week Queue is built
# strictly in this order. The three LOW_TIER segments only fill remaining
# slots if the higher tiers didn't already use up WEEKLY_QUEUE_LIMIT — a
# courtesy reply, a ghosted outreach, or a closed/blocked conversation must
# never outrank a real opportunity just to pad the queue.
RESPONSE_SEGMENT_ORDER = [
    "ACTIVE_PROCESS_NEEDS_REPLY",
    "INBOUND_OPPORTUNITY_NEEDS_REPLY",
    "SALARY_CV_CALL_REQUESTED",
    "HIGH_VALUE_RECRUITER_REPLY",
    "REACTIVATION_DUE_HIGH_VALUE",
    "SOFT_CLOSED_KEEP_WARM",
    "TALENT_POOL_LOW_ACTION",
    "LOW_PRIORITY_COURTESY",
    "NO_RESPONSE_BACKLOG",
    "CLOSED_NO_ACTION",
    # Part 22 — USD Remote / Location Fit: local-only/onsite/presencial-only
    # opportunities. Last-resort fill only — never a top action item.
    "LOW_FIT_LOCATION_BLOCKED",
    "LOW_PRIORITY_LOCAL_ONLY",
]
LOW_TIER_SEGMENTS = {
    "LOW_PRIORITY_COURTESY", "NO_RESPONSE_BACKLOG", "CLOSED_NO_ACTION",
    "LOW_FIT_LOCATION_BLOCKED", "LOW_PRIORITY_LOCAL_ONLY",
}
WEEKLY_QUEUE_LIMIT = sum(WEEKLY_LIMITS.values())  # 55 — matches the existing weekly action limit


def _build_this_week_queue(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the weekly action queue ranked by response_queue_segment (Part 21),
    highest-value segment first, response_priority_score breaking ties within
    a segment. LOW_PRIORITY_COURTESY / NO_RESPONSE_BACKLOG / CLOSED_NO_ACTION
    only fill remaining slots when no better candidates are left.
    """
    if "response_queue_segment" not in df.columns or "response_priority_score" not in df.columns:
        return pd.DataFrame()

    id_col = "conversation_id" if "conversation_id" in df.columns else None
    added_ids: set = set()
    frames = []

    def _take(seg: str, remaining: int):
        mask = df["response_queue_segment"] == seg
        if id_col:
            mask = mask & ~df[id_col].isin(added_ids)
        chunk = df[mask].sort_values("response_priority_score", ascending=False).head(remaining)
        if len(chunk):
            frames.append(chunk)
            if id_col:
                added_ids.update(chunk[id_col])
        return len(chunk)

    for seg in RESPONSE_SEGMENT_ORDER:
        if seg in LOW_TIER_SEGMENTS:
            continue
        remaining = WEEKLY_QUEUE_LIMIT - sum(len(f) for f in frames)
        if remaining <= 0:
            break
        _take(seg, remaining)

    # Only dip into the low-value tiers if better candidates ran out.
    remaining = WEEKLY_QUEUE_LIMIT - sum(len(f) for f in frames)
    for seg in RESPONSE_SEGMENT_ORDER:
        if seg not in LOW_TIER_SEGMENTS or remaining <= 0:
            continue
        taken = _take(seg, remaining)
        remaining -= taken

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    if id_col:
        result = result.drop_duplicates(subset=[id_col])
    rank_map = {s: i for i, s in enumerate(RESPONSE_SEGMENT_ORDER)}
    result["_segment_rank"] = result["response_queue_segment"].map(rank_map).fillna(99)
    result = result.sort_values(
        ["_segment_rank", "response_priority_score"], ascending=[True, False]
    ).drop(columns=["_segment_rank"])
    return result.reset_index(drop=True)


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

    # ── Lead Reactivation trust layer (Part 1) — operational summary counts ──
    # Four working-queue buckets that cut across the raw lead_category taxonomy,
    # built from the sanitized explain fields added to message_intelligence.py.
    most_urgent_confirmed_mask   = (df["reply_obligation"] == "CONFIRMED") & (~df["terminal_state_flag"])
    warm_recruiter_followup_mask = df["recruiter_priority_flag"] & (df["reply_obligation"] != "CONFIRMED")
    stale_but_valuable_mask      = df["stale_conversation_flag"] & (df["relationship_value_score"] >= 40)
    closed_low_action_mask       = df["terminal_state_flag"]

    most_urgent_confirmed_count    = int(most_urgent_confirmed_mask.sum())
    warm_recruiter_followups_count = int(warm_recruiter_followup_mask.sum())
    stale_but_valuable_count       = int(stale_but_valuable_mask.sum())
    closed_low_action_count        = int(closed_low_action_mask.sum())
    recruiter_priority_count       = int(df["recruiter_priority_flag"].sum())
    high_confidence_reply_count    = int((df["reply_obligation_confidence"] >= 70).sum())

    # ── Response Priority & Lead Quality (Part 21) — segment-based KPI cards ──
    # Separates "needs my response" into a real high-priority tier (active
    # process / inbound opportunity / salary-CV-call ask) vs. a medium tier
    # (valuable recruiter relationship, no urgent ask), and gives courtesy
    # replies, ghosted outreach, soft closes, and closed/blocked conversations
    # their own honest counts instead of hiding inside the raw taxonomy.
    seg_counts = df["response_queue_segment"].value_counts().to_dict() if "response_queue_segment" in df.columns else {}

    # ── Part 22 — USD Remote / Location Fit — honest sourcing-quality KPIs ────
    useless_for_usd_remote_count = int(df["useless_for_usd_remote_flag"].sum()) if "useless_for_usd_remote_flag" in df.columns else 0
    mexico_local_only_count      = int(df["mexico_local_only_flag"].sum()) if "mexico_local_only_flag" in df.columns else 0
    presencial_only_count        = int(df["presencial_only_flag"].sum()) if "presencial_only_flag" in df.columns else 0
    high_fit_usd_remote_count    = int((df["sourcing_quality_segment"] == "HIGH_FIT_USD_REMOTE").sum()) if "sourcing_quality_segment" in df.columns else 0
    low_fit_location_blocked_count = sum(seg_counts.get(s, 0) for s in (
        "LOW_FIT_LOCATION_BLOCKED", "LOW_PRIORITY_LOCAL_ONLY",
    ))
    needs_response_high_priority = sum(seg_counts.get(s, 0) for s in (
        "ACTIVE_PROCESS_NEEDS_REPLY", "INBOUND_OPPORTUNITY_NEEDS_REPLY", "SALARY_CV_CALL_REQUESTED",
    ))
    needs_response_medium = sum(seg_counts.get(s, 0) for s in (
        "HIGH_VALUE_RECRUITER_REPLY", "REACTIVATION_DUE_HIGH_VALUE",
    ))
    courtesy_low_priority_count      = sum(seg_counts.get(s, 0) for s in ("LOW_PRIORITY_COURTESY", "TALENT_POOL_LOW_ACTION"))
    no_response_ghost_backlog_count  = int(seg_counts.get("NO_RESPONSE_BACKLOG", 0))
    soft_closed_keep_warm_count      = int(seg_counts.get("SOFT_CLOSED_KEEP_WARM", 0))
    closed_no_action_v2_count        = int(seg_counts.get("CLOSED_NO_ACTION", 0))

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
    # Default sort is now response_queue_segment rank (Part 21) then
    # response_priority_score, so the backlog's own default order also puts
    # real actionable opportunities first instead of raw "they sent last".
    backlog_df = df[df["lead_category"] != "Ignore"].copy()
    if "response_queue_segment" in backlog_df.columns:
        rank_map = {s: i for i, s in enumerate(RESPONSE_SEGMENT_ORDER)}
        backlog_df["_segment_rank"] = backlog_df["response_queue_segment"].map(rank_map).fillna(99)
        backlog_df = backlog_df.sort_values(
            ["_segment_rank", "response_priority_score"], ascending=[True, False]
        ).drop(columns=["_segment_rank"])
    else:
        backlog_df = backlog_df.sort_values("reactivation_priority_score", ascending=False)
    top50_records = _safe_records(backlog_df.reset_index(drop=True))

    # ── This week queue (safe fields) ─────────────────────────────────────────
    this_week_records = _safe_records(this_week)

    # ── Needs reply (top 15, safe fields) — Part 21: ranked by
    # response_priority_score within the real high-priority segments (active
    # process / inbound opportunity / salary-CV-call ask), NOT by raw
    # "they sent last" reactivation_priority_score, so a generic recruiter
    # reply can no longer outrank an actual actionable opportunity. ─────────
    if "response_queue_segment" in df.columns:
        needs_reply_pool = df[df["response_queue_segment"].isin([
            "ACTIVE_PROCESS_NEEDS_REPLY", "INBOUND_OPPORTUNITY_NEEDS_REPLY", "SALARY_CV_CALL_REQUESTED",
        ])].sort_values("response_priority_score", ascending=False)
    else:
        needs_reply_pool = df[df["lead_category"].isin(
            ["Needs my response — Confirmed", "Needs my response — Likely"]
        )].sort_values(["lead_category", "reactivation_priority_score"], ascending=[True, False])
    needs_reply_records = _safe_records(needs_reply_pool.head(15).reset_index(drop=True))

    weekly_plan = {
        "Monday":    "Reply to 'Needs My Response — High Priority' first (active process / inbound opportunity / salary-CV-call ask)",
        "Tuesday":   "Reply to 'Needs My Response — Medium' (valuable recruiter relationship, no urgent ask) + Hot reactivation leads",
        "Wednesday": "Submit CV to career site / talent-pool leads (up to 10) — low action, not urgent",
        "Thursday":  "Recontact Soft Closed — Keep Warm and Dormant warm leads whose cooldown cleared",
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
    logger.info(
        f"  Response Priority (Part 21): HighPriority={needs_response_high_priority} "
        f"Medium={needs_response_medium} CourtesyLowPriority={courtesy_low_priority_count} "
        f"NoResponseGhost={no_response_ghost_backlog_count} SoftClosedKeepWarm={soft_closed_keep_warm_count} "
        f"ClosedNoAction={closed_no_action_v2_count}"
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
        # Lead Reactivation trust layer (Part 1) — operational summary counts
        "most_urgent_confirmed_count":    most_urgent_confirmed_count,
        "warm_recruiter_followups_count": warm_recruiter_followups_count,
        "stale_but_valuable_count":       stale_but_valuable_count,
        "closed_low_action_count":        closed_low_action_count,
        "recruiter_priority_count":       recruiter_priority_count,
        "high_confidence_reply_count":    high_confidence_reply_count,
        # Response Priority & Lead Quality (Part 21) — honest KPI cards that
        # separate reply obligation from lead quality from final priority.
        "needs_response_high_priority_count": int(needs_response_high_priority),
        "needs_response_medium_count":         int(needs_response_medium),
        "courtesy_low_priority_count":         int(courtesy_low_priority_count),
        "no_response_ghost_backlog_count":     no_response_ghost_backlog_count,
        "soft_closed_keep_warm_count":         soft_closed_keep_warm_count,
        "closed_no_action_v2_count":           closed_no_action_v2_count,
        # Part 22 — USD Remote / Location Fit — sourcing-quality KPIs (business
        # driver: too many useless Mexico-local/onsite-only recruiter leads).
        "useless_for_usd_remote_count":        useless_for_usd_remote_count,
        "mexico_local_only_count":             mexico_local_only_count,
        "presencial_only_count":               presencial_only_count,
        "high_fit_usd_remote_count":           high_fit_usd_remote_count,
        "low_fit_location_blocked_count":      low_fit_location_blocked_count,
        # Internal-only (Untapped Outreach Scoring V9) — never published to the
        # public dashboard JSON, see export_public_dashboard_data.py.
        "company_signal_map": build_company_warm_signal_map(df),
    }
