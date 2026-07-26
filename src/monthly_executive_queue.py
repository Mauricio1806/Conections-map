# -*- coding: utf-8 -*-
"""
monthly_executive_queue.py
=============================
Monthly Executive Queue — a curated, top-N execution layer on top of
Opportunity History (src/opportunity_history_engine.py) and the USD Contract
CRM (src/usd_contract_crm.py). Answers "what should I actually DO this
month," as four ranked top-20 lists plus a secondary top-50 backlog:

  1. Top 20 Inbound Opportunities This Month
  2. Top 20 Reactivation Due This Month
  3. Top 20 Soft-Closed Recruiters to Keep Warm
  4. Top 20 USD Recruiter Follow-ups
  5. Monthly Opportunity Backlog Top 50 (secondary — strong leads that
     didn't make a Top 20 list)

Inputs are entirely local/already-computed: the opportunity history events
(and its 3 curated CSVs) and the already-assembled USD Contract CRM dict
(manual + auto-suggested + opportunity history). No LinkedIn access of any
kind.

CRITICAL distinctions (never fabricate/mis-count):
  - These are EXECUTION QUEUES, not confirmed applications.
  - Soft-closed leads are never counted as active opportunities.
  - "Keep you on my radar" is never treated as a hard rejection.
  - Auto-suggested/queue leads are never counted as manual applications.

Sanitization: every output field is a boolean/score/date/short controlled-
vocabulary label, or one of the 4 fixed message-angle templates below with
only the contact's first name interpolated. Raw message content, emails,
phone numbers, and attachments are never read or emitted.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.usd_contract_crm import (
    CRM_RECRUITING_PERSONAS, CRM_HIRING_DATA_LEADER_PERSONAS,
    USD_TARGET_BUCKET_SUBSTRINGS, CONFIRMED_HIGH_VALUE_BUCKETS,
)

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"

RECRUITING_PERSONAS = CRM_RECRUITING_PERSONAS
TARGET_PERSONAS = CRM_RECRUITING_PERSONAS | CRM_HIRING_DATA_LEADER_PERSONAS

ACTIVE_EVENT_TYPES = {
    "Inbound Opportunity", "Recruiter Outreach", "Active Talent Pool",
    "Salary Expectations Requested", "CV Requested", "Application Requested",
    "Recruiter Call Proposed", "Interview Process", "Client Submission",
    "Technical Interview", "Offer / Contract Discussion",
}
NEEDS_RESPONSE_STATUSES = {"Needs my response — Confirmed", "Needs my response — Likely"}
ACTIVE_PROCESS_STATUSES_LEAD = {"Active Interview Pipeline", "Awaiting Recruiter Update"}
FOLLOWUP_DUE_STATUSES = NEEDS_RESPONSE_STATUSES | ACTIVE_PROCESS_STATUSES_LEAD | {
    "Warm reactivation", "Reactivate This Month",
}
TERMINAL_STATUSES = {"Rejected / Closed", "Closed / no action"}

# ── Public output schema (exact allowlist per spec) ─────────────────────────
QUEUE_ROW_FIELDS = [
    "queue_name", "rank", "contact_name", "company", "role", "persona",
    "profile_url", "event_month", "event_date", "last_contact_date",
    "opportunity_event_type", "opportunity_stage", "opportunity_signal_strength",
    "opportunity_bucket", "usd_signal", "latam_signal", "remote_signal",
    "score", "priority", "recommended_action", "next_action_date",
    "reason_short", "message_angle",
]

# ── Recommended message-angle templates (exact spec wording, [Name] filled) ─
MESSAGE_ANGLE_TEMPLATES = {
    "inbound": (
        "Hi {name}, thank you for reaching out. The opportunity sounds aligned with "
        "my Data Engineering background, especially around cloud data platforms, "
        "SQL/Python, Databricks/AWS/Azure and ETL/ELT pipelines. My expected range "
        "is negotiable depending on scope, contract model and timezone overlap. "
        "I'd be happy to schedule a short call to understand the role and next steps."
    ),
    "reactivation": (
        "Hi {name}, hope you're doing well. We spoke previously about Data Engineering "
        "opportunities, and I wanted to reconnect because I'm currently open to remote "
        "LATAM/US-aligned roles involving Azure, AWS, Databricks, SQL, Python and "
        "ETL/ELT pipelines."
    ),
    "soft_closed": (
        "Hi {name}, thanks again for keeping me on your radar. I wanted to stay in "
        "touch in case any Data Engineering opportunities come up, especially remote "
        "LATAM/US-aligned roles involving cloud data platforms, Databricks, SQL, "
        "Python and pipelines."
    ),
    "usd_followup": (
        "Hi {name}, quick follow-up in case this is still relevant. I'm currently "
        "open to remote Data Engineering roles aligned with LATAM/US time zones, "
        "with experience across Azure, AWS, Databricks, SQL, Python and ETL/ELT "
        "pipelines."
    ),
}

RECOMMENDED_ACTION_BY_QUEUE = {
    "inbound": "Reply with interest, salary range, and propose call slots",
    "reactivation": "Reconnect and share updated availability",
    "soft_closed": "Send a brief keep-warm message",
    "usd_followup": "Follow up on the pending request",
}


def _norm(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _norm_url(v) -> str:
    return _norm(v).lower().rstrip("/")


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def _priority_from_score(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


def _bucket_matches_target(bucket: str) -> bool:
    b = (bucket or "").upper()
    return any(t in b for t in USD_TARGET_BUCKET_SUBSTRINGS)


def _first_name(contact_name: str) -> str:
    name = _norm(contact_name)
    if not name:
        return "there"
    return name.split()[0]


def _fill_angle(template_key: str, contact_name: str) -> str:
    return MESSAGE_ANGLE_TEMPLATES[template_key].format(name=_first_name(contact_name))


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def _end_of_current_month() -> date:
    today = date.today()
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    return next_month - timedelta(days=1)


def _parse_date(s) -> date | None:
    s = _norm(s)
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# ── Aggregate opportunity-history events into one profile per contact ──────

def _build_contact_profiles(events: list[dict]) -> dict:
    """Keyed by normalized profile_url. `latest` holds the most recent event
    (current state); the `ever_*` flags aggregate across the contact's full
    history (used for "previous positive signal" style scoring bonuses)."""
    profiles: dict[str, dict] = {}
    for e in events or []:
        url = _norm_url(e.get("profile_url", ""))
        if not url:
            continue
        p = profiles.setdefault(url, {
            "profile_url": e.get("profile_url", ""), "latest": None,
            "event_count": 0, "ever_positive_signal": False,
            "ever_rejected": False, "ever_soft_closed": False,
        })
        p["event_count"] += 1
        if p["latest"] is None or _norm(e.get("event_date")) >= _norm(p["latest"].get("event_date")):
            p["latest"] = e
        p["ever_positive_signal"] = p["ever_positive_signal"] or any(
            _truthy(e.get(k)) for k in (
                "inbound_recruiter_contact", "active_talent_pool_signal",
                "salary_expectation_requested", "cv_requested",
                "interview_or_call_requested", "client_submission_signal",
            )
        )
        p["ever_rejected"] = p["ever_rejected"] or _truthy(e.get("rejected_or_closed"))
        p["ever_soft_closed"] = p["ever_soft_closed"] or _truthy(e.get("soft_closed"))
    return profiles


def _base_row(profile_url: str, latest: dict) -> dict:
    latest = latest or {}
    return {
        "contact_name": _norm(latest.get("contact_name")),
        "company": _norm(latest.get("company")),
        "role": _norm(latest.get("role")),
        "persona": _norm(latest.get("persona")),
        "profile_url": _norm(profile_url) or _norm(latest.get("profile_url")),
        "event_month": _norm(latest.get("event_month")),
        "event_date": _norm(latest.get("event_date")),
        "last_contact_date": _norm(latest.get("event_date")),
        "opportunity_event_type": _norm(latest.get("opportunity_event_type")),
        "opportunity_stage": _norm(latest.get("opportunity_stage")),
        "opportunity_signal_strength": _norm(latest.get("opportunity_signal_strength")),
        "opportunity_bucket": _norm(latest.get("opportunity_bucket")).upper(),
        "usd_signal": _truthy(latest.get("usd_signal")),
        "latam_signal": _truthy(latest.get("latam_signal")),
        "remote_signal": _truthy(latest.get("remote_signal")),
        "next_action_date": _norm(latest.get("reactivation_date")),
        "reason_short": _norm(latest.get("reason_short")),
    }


def _finalize_rows(rows: list[dict], queue_name: str, angle_key: str, limit: int | None) -> list[dict]:
    rows = sorted(rows, key=lambda r: r["_score"], reverse=True)
    if limit is not None:
        rows = rows[:limit]
    out = []
    for i, r in enumerate(rows, start=1):
        score = int(max(0, min(100, r["_score"])))
        row = {k: r.get(k, "") for k in QUEUE_ROW_FIELDS if k not in ("queue_name", "rank", "score", "priority", "recommended_action", "message_angle")}
        row["queue_name"] = queue_name
        row["rank"] = i
        row["score"] = score
        row["priority"] = _priority_from_score(score)
        row["recommended_action"] = RECOMMENDED_ACTION_BY_QUEUE.get(angle_key, "Review and respond")
        row["message_angle"] = _fill_angle(angle_key, r.get("contact_name", ""))
        out.append(row)
    return out


# ── Queue 1 — Top 20 Inbound Opportunities This Month ───────────────────────

def _build_inbound_queue(profiles: dict, current_month: str, limit: int = 20) -> list[dict]:
    candidates = []
    for url, p in profiles.items():
        latest = p["latest"] or {}
        if _norm(latest.get("event_month")) != current_month:
            continue
        if _norm(latest.get("opportunity_event_type")) not in ACTIVE_EVENT_TYPES:
            continue
        row = _base_row(url, latest)
        score = 0.0
        if _truthy(latest.get("inbound_recruiter_contact")):
            score += 30
        if row["opportunity_event_type"] in ("Inbound Opportunity", "Recruiter Outreach", "Active Talent Pool"):
            score += 25
        if _truthy(latest.get("salary_expectation_requested")):
            score += 20
        if _truthy(latest.get("interview_or_call_requested")):
            score += 20
        if _truthy(latest.get("active_talent_pool_signal")):
            score += 15
        if _truthy(latest.get("cv_requested")):
            score += 15
        if row["usd_signal"] or row["latam_signal"] or row["remote_signal"]:
            score += 15
        if _norm(latest.get("tech_stack_signal")) in ("Medium", "High"):
            score += 10
        score += 10  # current-month event (guaranteed true by filter above)
        if row["opportunity_event_type"] == "Career Site / Talent Database Redirect" and not (
            _truthy(latest.get("interview_or_call_requested")) or _truthy(latest.get("cv_requested"))
            or _truthy(latest.get("salary_expectation_requested"))
        ):
            score -= 20
        if _truthy(latest.get("rejected_or_closed")):
            score -= 25
        row["_score"] = score
        candidates.append(row)
    return candidates


# ── Queue 2 — Top 20 Reactivation Due This Month ────────────────────────────

def _build_reactivation_queue(profiles: dict, end_of_month: date, limit: int = 20) -> list[dict]:
    candidates = []
    for url, p in profiles.items():
        latest = p["latest"] or {}
        reactivation_date = _parse_date(latest.get("reactivation_date"))
        if not reactivation_date or reactivation_date > end_of_month:
            continue
        if not _truthy(latest.get("future_reactivation_candidate")):
            continue
        row = _base_row(url, latest)
        score = 30.0  # reactivation due this month or overdue (guaranteed by filter)
        score += 25   # future_reactivation_candidate (guaranteed by filter)
        if row["persona"] in TARGET_PERSONAS:
            score += 20
        if p["ever_positive_signal"]:
            score += 20
        if _truthy(latest.get("soft_closed")):
            score += 15
        if _bucket_matches_target(row["opportunity_bucket"]) or row["opportunity_bucket"] == "GLOBAL_STAFFING":
            score += 15
        if p["event_count"] > 1:
            score += 10
        if _truthy(latest.get("rejected_or_closed")):
            score -= 25
        if _truthy(latest.get("location_or_eligibility_blocked")):
            score -= 20
        row["_score"] = score
        candidates.append(row)
    return candidates


# ── Queue 3 — Top 20 Soft-Closed Recruiters to Keep Warm ────────────────────

def _build_soft_closed_queue(profiles: dict, limit: int = 20) -> list[dict]:
    candidates = []
    today = date.today()
    for url, p in profiles.items():
        latest = p["latest"] or {}
        if not _truthy(latest.get("soft_closed")):
            continue
        row = _base_row(url, latest)
        score = 30.0  # soft_closed (guaranteed by filter)
        if row["opportunity_event_type"] == "No Current Role / Keep on Radar":
            score += 25
        if row["persona"] in TARGET_PERSONAS:
            score += 20
        if p["event_count"] > 1:
            score += 15
        if p["ever_positive_signal"]:
            score += 15
        if row["usd_signal"] or row["latam_signal"] or row["remote_signal"]:
            score += 15
        reactivation_date = _parse_date(latest.get("reactivation_date"))
        if reactivation_date and (reactivation_date - today).days <= 90:
            score += 10
        if _truthy(latest.get("rejected_or_closed")):
            score -= 30
        if not (row["usd_signal"] or row["latam_signal"] or row["remote_signal"]
                or row["persona"] in TARGET_PERSONAS or _bucket_matches_target(row["opportunity_bucket"])):
            score -= 15
        row["_score"] = score
        candidates.append(row)
    return candidates


# ── Queue 4 — Top 20 USD Recruiter Follow-ups ───────────────────────────────

def _build_usd_followup_pool(profiles: dict, usd_crm_data: dict) -> dict:
    """Merges USD Contract CRM's follow_up_queue + active_process_pipeline
    (lead-reactivation-derived status/persona/score) with opportunity-history
    signals (cv/salary/call/technical-interview booleans), keyed by profile_url."""
    pool: dict[str, dict] = {}

    def _entry(url: str) -> dict:
        return pool.setdefault(url, {"profile_url": url})

    usd_crm_data = usd_crm_data or {}
    for row in (usd_crm_data.get("follow_up_queue") or []) + (usd_crm_data.get("active_process_pipeline") or []):
        url = _norm_url(row.get("profile_url", ""))
        if not url:
            continue
        e = _entry(url)
        e["contact_name"] = row.get("name", "") or e.get("contact_name", "")
        e["company"] = row.get("company", "") or e.get("company", "")
        e["role"] = row.get("role", "") or e.get("role", "")
        e["persona"] = row.get("persona", "") or e.get("persona", "")
        e["opportunity_bucket"] = row.get("opportunity_bucket", "") or e.get("opportunity_bucket", "")
        e["status"] = row.get("status", "") or e.get("status", "")
        e["usd_crm_score"] = max(_to_num(row.get("score")), e.get("usd_crm_score", 0))
        e["next_action_date"] = row.get("next_action_date", "") or e.get("next_action_date", "")
        e["reason_short"] = row.get("reason", "") or e.get("reason_short", "")
        e["from_usd_crm"] = True

    for url, p in profiles.items():
        latest = p["latest"] or {}
        has_followup_signal = any(_truthy(latest.get(k)) for k in (
            "cv_requested", "salary_expectation_requested", "interview_or_call_requested",
            "technical_interview_signal",
        ))
        if not has_followup_signal and url not in pool:
            continue
        e = _entry(url)
        e.setdefault("contact_name", latest.get("contact_name", ""))
        e.setdefault("company", latest.get("company", ""))
        e.setdefault("role", latest.get("role", ""))
        e.setdefault("persona", latest.get("persona", ""))
        e.setdefault("opportunity_bucket", latest.get("opportunity_bucket", ""))
        e["cv_requested"] = _truthy(latest.get("cv_requested"))
        e["salary_expectation_requested"] = _truthy(latest.get("salary_expectation_requested"))
        e["interview_or_call_requested"] = _truthy(latest.get("interview_or_call_requested"))
        e["technical_interview_signal"] = _truthy(latest.get("technical_interview_signal"))
        e["rejected_or_closed"] = _truthy(latest.get("rejected_or_closed"))
        e["usd_signal"] = _truthy(latest.get("usd_signal"))
        e["latam_signal"] = _truthy(latest.get("latam_signal"))
        e["remote_signal"] = _truthy(latest.get("remote_signal"))
        e["event_month"] = latest.get("event_month", "")
        e["event_date"] = latest.get("event_date", "")
        e["opportunity_event_type"] = latest.get("opportunity_event_type", "")
        e["opportunity_stage"] = latest.get("opportunity_stage", "")
        e.setdefault("next_action_date", latest.get("reactivation_date", ""))
        e.setdefault("reason_short", latest.get("reason_short", ""))
        e["has_opportunity_history"] = True

    return pool


def _to_num(v, default=0.0) -> float:
    try:
        f = float(v)
        return default if pd.isna(f) else f
    except (TypeError, ValueError):
        return default


def _build_usd_followup_queue(profiles: dict, usd_crm_data: dict, limit: int = 20) -> list[dict]:
    pool = _build_usd_followup_pool(profiles, usd_crm_data)
    candidates = []
    for url, e in pool.items():
        status = _norm(e.get("status"))
        row = {
            "contact_name": _norm(e.get("contact_name")),
            "company": _norm(e.get("company")),
            "role": _norm(e.get("role")),
            "persona": _norm(e.get("persona")),
            "profile_url": _norm(url),
            "event_month": _norm(e.get("event_month")),
            "event_date": _norm(e.get("event_date")),
            "last_contact_date": _norm(e.get("event_date")),
            "opportunity_event_type": _norm(e.get("opportunity_event_type")) or status,
            "opportunity_stage": _norm(e.get("opportunity_stage")) or status,
            "opportunity_signal_strength": "High" if _to_num(e.get("usd_crm_score")) >= 70 else (
                "Medium" if _to_num(e.get("usd_crm_score")) >= 40 else "Low"),
            "opportunity_bucket": _norm(e.get("opportunity_bucket")).upper(),
            "usd_signal": _truthy(e.get("usd_signal")),
            "latam_signal": _truthy(e.get("latam_signal")),
            "remote_signal": _truthy(e.get("remote_signal")),
            "next_action_date": _norm(e.get("next_action_date")),
            "reason_short": _norm(e.get("reason_short")) or "USD recruiter follow-up",
        }
        score = 0.0
        if status in FOLLOWUP_DUE_STATUSES:
            score += 30
        if status == "Needs my response — Confirmed":
            score += 25
        if status == "Active Interview Pipeline" or _truthy(e.get("technical_interview_signal")) or _truthy(e.get("interview_or_call_requested")):
            score += 25
        if _truthy(e.get("salary_expectation_requested")) or _truthy(e.get("cv_requested")) or _truthy(e.get("interview_or_call_requested")):
            score += 20
        if row["persona"] in TARGET_PERSONAS:
            score += 20
        if _bucket_matches_target(row["opportunity_bucket"]):
            score += 20
        if _to_num(e.get("usd_crm_score")) >= 70:
            score += 15
        if not e.get("has_opportunity_history") and _to_num(e.get("usd_crm_score")) >= 70:
            score += 10  # no message-level reply signal after high-score outreach
        if _truthy(e.get("rejected_or_closed")) or status == "Rejected / Closed":
            score -= 25
        if status in TERMINAL_STATUSES and not _truthy(e.get("future_reactivation_candidate")):
            score -= 20
        row["_score"] = score
        candidates.append(row)
    return candidates


# ── Monthly chart + summary ──────────────────────────────────────────────────

def _build_monthly_chart(events: list[dict]) -> list[dict]:
    if not events:
        return []
    df = pd.DataFrame(events)
    if df.empty or "event_month" not in df.columns:
        return []
    df["reactivation_date"] = df.get("reactivation_date", "").fillna("")
    months = sorted(set(df["event_month"].dropna()) | {
        str(d)[:7] for d in df["reactivation_date"] if d
    })
    rows = []
    for month in months:
        m = df[df["event_month"] == month]
        reactivation_this_month = df[df["reactivation_date"].astype(str).str.startswith(month, na=False)]
        rows.append({
            "month": month,
            "inbound_opportunities": int((m["opportunity_event_type"] == "Inbound Opportunity").sum()),
            "reactivation_due": int(len(reactivation_this_month)),
            "soft_closed": int(m.get("soft_closed", pd.Series([False] * len(m))).sum()),
            "usd_followups": int((m.get("interview_or_call_requested", pd.Series([False] * len(m)))
                                   | m.get("salary_expectation_requested", pd.Series([False] * len(m)))
                                   | m.get("cv_requested", pd.Series([False] * len(m)))).sum()),
        })
    return rows


def _save_csv(rows: list[dict], name: str) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=QUEUE_ROW_FIELDS).to_csv(OUTPUTS_DIR / name, index=False, encoding="utf-8-sig")
    logger.info(f"  Saved {name} ({len(rows)} rows)")


def run_monthly_executive_queue(opportunity_history_data: dict | None = None,
                                 usd_crm_data: dict | None = None) -> dict:
    """Main entry point. Builds the 4 top-20 execution queues + the top-50
    secondary backlog, writes the 6 sanitized output CSVs, and returns a dict
    consumed by src/export_public_dashboard_data.py."""
    oh = opportunity_history_data or {}
    if not oh.get("available"):
        logger.info("  Monthly Executive Queue: no opportunity history available — skipping.")
        return {"available": False}

    events = oh.get("events") or []
    profiles = _build_contact_profiles(events)
    current_month = _current_month()
    end_of_month = _end_of_current_month()

    inbound_candidates      = _build_inbound_queue(profiles, current_month)
    reactivation_candidates = _build_reactivation_queue(profiles, end_of_month)
    soft_closed_candidates  = _build_soft_closed_queue(profiles)
    usd_followup_candidates = _build_usd_followup_queue(profiles, usd_crm_data)

    inbound_top20      = _finalize_rows(inbound_candidates,      "inbound",       "inbound",      20)
    reactivation_top20  = _finalize_rows(reactivation_candidates, "reactivation",  "reactivation", 20)
    soft_closed_top20   = _finalize_rows(soft_closed_candidates,  "soft_closed",   "soft_closed",  20)
    usd_followups_top20 = _finalize_rows(usd_followup_candidates, "usd_followup",  "usd_followup", 20)

    # Backlog top 50 — strong leads that did NOT make a Top 20 list, drawn
    # from the union of all 4 candidate pools before truncation.
    top20_urls = {
        r["profile_url"] for r in (inbound_top20 + reactivation_top20 + soft_closed_top20 + usd_followups_top20)
        if r.get("profile_url")
    }
    backlog_pool = []
    for cands, queue_name, angle_key in (
        (inbound_candidates, "inbound", "inbound"),
        (reactivation_candidates, "reactivation", "reactivation"),
        (soft_closed_candidates, "soft_closed", "soft_closed"),
        (usd_followup_candidates, "usd_followup", "usd_followup"),
    ):
        for r in cands:
            if r.get("profile_url") and r["profile_url"] not in top20_urls:
                backlog_pool.append((r, queue_name, angle_key))
    # dedupe by profile_url, keep highest score (and remember which queue/
    # angle template that best-scoring candidate came from)
    best_by_url: dict[str, tuple[dict, str, str]] = {}
    for r, queue_name, angle_key in backlog_pool:
        url = r["profile_url"]
        if url not in best_by_url or r["_score"] > best_by_url[url][0]["_score"]:
            best_by_url[url] = (r, queue_name, angle_key)
    backlog_sorted = sorted(best_by_url.values(), key=lambda t: t[0]["_score"], reverse=True)[:50]
    monthly_backlog_top50 = []
    for i, (r, _queue_name, angle_key) in enumerate(backlog_sorted, start=1):
        score = int(max(0, min(100, r["_score"])))
        row = {k: r.get(k, "") for k in QUEUE_ROW_FIELDS if k not in ("queue_name", "rank", "score", "priority", "recommended_action", "message_angle")}
        row["queue_name"] = "monthly_backlog"
        row["rank"] = i
        row["score"] = score
        row["priority"] = _priority_from_score(score)
        row["recommended_action"] = RECOMMENDED_ACTION_BY_QUEUE.get(angle_key, "Review and respond")
        row["message_angle"] = _fill_angle(angle_key, r.get("contact_name", ""))
        monthly_backlog_top50.append(row)

    all_records = inbound_top20 + reactivation_top20 + soft_closed_top20 + usd_followups_top20 + monthly_backlog_top50

    today = date.today()
    overdue_reactivations = sum(
        1 for r in reactivation_top20 + monthly_backlog_top50
        if r["queue_name"] in ("reactivation",) and _parse_date(r.get("next_action_date")) and _parse_date(r.get("next_action_date")) < today
    )
    high_priority_this_month = sum(1 for r in all_records if r["priority"] == "HIGH")
    active_opportunity_signals = sum(
        1 for r in inbound_top20 + monthly_backlog_top50
        if r["queue_name"] == "inbound" and r["opportunity_event_type"] in ACTIVE_EVENT_TYPES
    )

    summary = {
        "inbound_opportunities_this_month": len(inbound_top20),
        "reactivation_due_this_month":      len(reactivation_top20),
        "soft_closed_keep_warm":            len(soft_closed_top20),
        "usd_recruiter_followups":          len(usd_followups_top20),
        "monthly_backlog":                  len(monthly_backlog_top50),
        "high_priority_this_month":         high_priority_this_month,
        "overdue_reactivations":            overdue_reactivations,
        "active_opportunity_signals":       active_opportunity_signals,
    }

    monthly_chart = _build_monthly_chart(events)

    _save_csv([{**{k: v for k, v in r.items() if k != "_score"}} for r in inbound_top20], "monthly_executive_queue_inbound_top20.csv")
    _save_csv([{**{k: v for k, v in r.items() if k != "_score"}} for r in reactivation_top20], "monthly_executive_queue_reactivation_top20.csv")
    _save_csv([{**{k: v for k, v in r.items() if k != "_score"}} for r in soft_closed_top20], "monthly_executive_queue_soft_closed_top20.csv")
    _save_csv([{**{k: v for k, v in r.items() if k != "_score"}} for r in usd_followups_top20], "monthly_executive_queue_usd_followups_top20.csv")
    _save_csv([{**{k: v for k, v in r.items() if k != "_score"}} for r in monthly_backlog_top50], "monthly_opportunity_backlog_top50.csv")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"metric": k, "value": v} for k, v in summary.items()]).to_csv(
        OUTPUTS_DIR / "monthly_executive_queue_summary.csv", index=False, encoding="utf-8-sig",
    )
    logger.info(f"  Saved monthly_executive_queue_summary.csv (8 metrics)")

    logger.info(
        f"  Monthly Executive Queue: inbound={summary['inbound_opportunities_this_month']} "
        f"reactivation={summary['reactivation_due_this_month']} "
        f"soft_closed={summary['soft_closed_keep_warm']} "
        f"usd_followups={summary['usd_recruiter_followups']} "
        f"backlog={summary['monthly_backlog']} "
        f"high_priority={summary['high_priority_this_month']} "
        f"overdue={summary['overdue_reactivations']}"
    )

    return {
        "available": True,
        "summary": summary,
        "inbound_top20":       [{k: v for k, v in r.items() if k != "_score"} for r in inbound_top20],
        "reactivation_top20":   [{k: v for k, v in r.items() if k != "_score"} for r in reactivation_top20],
        "soft_closed_top20":    [{k: v for k, v in r.items() if k != "_score"} for r in soft_closed_top20],
        "usd_followups_top20":  [{k: v for k, v in r.items() if k != "_score"} for r in usd_followups_top20],
        "monthly_backlog_top50": monthly_backlog_top50,
        "all_monthly_queue_records": [{k: v for k, v in r.items() if k != "_score"} for r in all_records],
        "monthly_chart": monthly_chart,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    print("This module is normally called from src/build_strategy_layer.py with "
          "opportunity_history_data and usd_crm_data already computed.")
