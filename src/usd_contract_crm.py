# -*- coding: utf-8 -*-
"""
usd_contract_crm.py
====================
USD Contract CRM — a HYBRID CRM combining:

  1. Manual records from data/manual/*.csv (real, confirmed opportunities/
     applications the user logs by hand — optional, gitignored, can be
     entirely empty/absent).
  2. Auto-suggested USD pipeline records derived from intelligence the
     pipeline ALREADY computed and sanitized this run: Lead Reactivation
     (src/lead_reactivation_engine.py), Untapped Network Intelligence
     (src/untapped_network_intelligence.py), and the classified connections
     dataframe + outreach-adjusted scores (src/outreach_adjusted_scoring.py)
     — i.e. the same data that backs Top Contacts / Opportunity Market.
  3. Monthly opportunity history (src/opportunity_history_engine.py) —
     inbound opportunities, active talent pool invites, salary/CV/interview
     requests, client submissions, soft-closed "keep on radar" leads, and a
     reactivation calendar, classified from messages.csv. Passed through as
     its own section — never merged into (1)/(2)'s counts.

No LinkedIn scraping, browsing, or automation of any kind — every input here
is either a hand-filled local CSV or an in-memory dataframe/dict this
pipeline already produced from local exports.

CRITICAL distinction (never fabricate real applications):
  - Auto-suggested contacts/leads are RECOMMENDED ACTIONS, not confirmed
    applications. They must never be counted as "applications sent", "CVs
    sent", "client submissions", or "technical interviews" — those numbers
    can ONLY come from data/manual/*.csv or explicit message-intelligence
    signals (has_cv_signal / has_interview_signal from Lead Reactivation).

Private fields (notes_private, raw message content, emails, phone numbers)
are never emitted in any public/sanitized output.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

ROOT             = Path(__file__).resolve().parent.parent
DATA_MANUAL_DIR  = ROOT / "data" / "manual"
OUTPUTS_DIR      = ROOT / "outputs"

USD_PIPELINE_CSV       = DATA_MANUAL_DIR / "usd_pipeline.csv"
JOB_APPLICATIONS_CSV   = DATA_MANUAL_DIR / "job_applications.csv"
RECRUITER_OUTREACH_CSV = DATA_MANUAL_DIR / "recruiter_outreach_log.csv"

PIPELINE_COLUMNS = [
    "date_added", "company_name", "role_title", "role_url", "source",
    "source_type", "currency", "rate_min", "rate_max", "rate_type",
    "contract_type", "remote_policy", "timezone_required", "overlap_required",
    "location_restriction", "tech_stack", "status", "recruiter_name",
    "recruiter_profile_url", "last_action_date", "next_action",
    "next_action_date", "priority", "timezone_risk", "payment_risk",
    "contract_risk", "notes_private",
]

APPLICATION_COLUMNS = [
    "application_date", "company_name", "role_title", "role_url", "source",
    "currency", "expected_rate", "status", "cv_version", "recruiter_contacted",
    "follow_up_date", "result", "rejection_reason", "notes_private",
]

OUTREACH_COLUMNS = [
    "date", "contact_name", "profile_url", "company", "source",
    "opportunity_bucket", "message_type", "status", "last_reply_date",
    "next_action", "next_action_date", "usd_signal", "latam_signal",
    "timezone_signal", "notes_private",
]

PRIVATE_FIELDS = {"notes_private"}

# ── Manual pipeline funnel stages (score for MANUAL usd_pipeline.csv rows) ──
# A row's `status` is its CURRENT stage — since the funnel is monotonic
# (you cannot be SUBMITTED_TO_CLIENT without having been RECRUITER_REPLIED
# and CV_REQUESTED first), ordinal >= comparisons let a handful of milestone
# bonuses (+15/+15/+20) apply cumulatively from a single current-stage value,
# without requiring the CSV to store full stage history.
STATUS_STAGES = [
    "NEW", "RESEARCHING", "OUTREACH_SENT", "RECRUITER_REPLIED",
    "CV_REQUESTED", "CV_SENT", "SUBMITTED_TO_CLIENT",
    "RECRUITER_CALL_SCHEDULED", "RECRUITER_CALL_DONE",
    "TECHNICAL_INTERVIEW_SCHEDULED", "TECHNICAL_INTERVIEW_DONE",
    "FINAL_INTERVIEW", "OFFER", "CLOSED_WON",
    "CLOSED_LOST", "REJECTED", "ON_HOLD",
]
STATUS_ORDER = {s: i for i, s in enumerate(STATUS_STAGES)}
CLOSED_NEGATIVE_STATUSES = {"CLOSED_LOST", "REJECTED"}
ACTIVE_PROCESS_STATUSES = {
    "SUBMITTED_TO_CLIENT", "RECRUITER_CALL_SCHEDULED", "RECRUITER_CALL_DONE",
    "TECHNICAL_INTERVIEW_SCHEDULED", "TECHNICAL_INTERVIEW_DONE",
    "FINAL_INTERVIEW", "OFFER",
}

STACK_KEYWORDS = ("azure", "aws", "databricks", "snowflake", "dbt", "python", "sql")

BRAZIL_LATAM_REMOTE_KEYWORDS = (
    "brazil", "brasil", "latam", "latin america", "worldwide", "global",
    "anywhere", "remote-first", "remote first", "remote global",
    "remote worldwide", "remote anywhere",
)

LOCATION_BLOCK_KEYWORDS = (
    "no brazil", "not brazil", "excludes brazil", "no latam",
    "us only", "usa only", "u.s. only", "eu only", "onsite only",
    "must relocate", "citizens only", "green card", "work visa required",
    "no digital nomad", "not eligible", "us residents only", "eu residents only",
)

# Conservative default target-rate floors — adjust to your own bar.
TARGET_MIN_RATE = {
    "hourly":  25,     # USD/hour
    "monthly": 3000,   # USD/month
    "annual":  60000,  # USD/year
}

# ── Auto-suggested candidate scoring vocabulary (Part 3) ────────────────────
CRM_RECRUITING_PERSONAS = {"Recruiter", "Talent Acquisition", "Sourcer"}
# "Data Leader" umbrella (matches the DATA_LEADER_PERSONAS convention already
# used by src/untapped_network_intelligence.py) + Hiring Manager per spec.
CRM_HIRING_DATA_LEADER_PERSONAS = {"Hiring Manager", "Head of Data", "Data Engineering Manager", "Director"}
CRM_TARGET_PERSONAS = CRM_RECRUITING_PERSONAS | CRM_HIRING_DATA_LEADER_PERSONAS

USD_TARGET_BUCKET_SUBSTRINGS = ("LATAM_USD", "US_CANADA", "GLOBAL_STAFFING", "GLOBAL_OPPORTUNITY")
CONFIRMED_HIGH_VALUE_BUCKETS = {"LATAM_USD_CONFIRMED", "US_CANADA_CONFIRMED"}
USD_STRATEGIC_MARKETS = {"LATAM_USD", "US_CANADA_NEARSHORE", "GLOBAL_STAFFING"}

NEVER_CONTACTED_STATUSES = {"NEVER_CONTACTED_CONFIRMED", "LIKELY_NEVER_CONTACTED"}

# Company/title keyword signal (Part 1B / Part 3) — word-boundary matched so
# "us" doesn't false-positive inside unrelated words.
KEYWORD_SIGNAL_RE = re.compile(
    r"\b(latam|usa|united states|nearshore|contractor|staffing|recruiting|talent acquisition|us)\b",
    re.IGNORECASE,
)

# lead_category vocabulary produced by src/message_intelligence.py's
# _lead_category_v8() — see that module for the authoritative list.
FOLLOWUP_LEAD_CATEGORIES = {
    "Needs my response — Confirmed", "Needs my response — Likely",
    "Warm reactivation", "Dormant warm", "Active Interview Pipeline",
    "Awaiting Recruiter Update", "Reactivate This Month",
}
ACTIVE_PROCESS_LEAD_CATEGORIES = {"Active Interview Pipeline", "Awaiting Recruiter Update"}
BLOCKED_LEAD_CATEGORIES = {"Location / Eligibility Blocked"}
TERMINAL_LEAD_CATEGORIES = {"Rejected / Closed", "Closed / no action"}


def _norm(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _norm_lower(v) -> str:
    return _norm(v).lower()


def _norm_url(v) -> str:
    return _norm(v).lower().rstrip("/")


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def _to_num(v, default=0.0) -> float:
    if v is None or v == "":
        return default
    try:
        f = float(v)
        return default if pd.isna(f) else f
    except (TypeError, ValueError):
        return default


def _parse_date(s) -> date | None:
    s = _norm(s)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(v) -> float | None:
    s = _norm(v)
    if not s:
        return None
    try:
        return float(re.sub(r"[^0-9.\-]", "", s))
    except ValueError:
        return None


# ── Manual CSV loading ───────────────────────────────────────────────────────

def _read_manual_csv(path: Path, expected_columns: list[str], label: str) -> pd.DataFrame | None:
    """Returns None if the file does not exist (never an error — these files
    are entirely optional/private). Missing expected columns are added as
    empty strings so downstream code never KeyErrors; unexpected extra
    columns are simply ignored by every sanitized allowlist below."""
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except Exception as exc:
        logger.warning(f"  usd_contract_crm: failed to read {label} CSV: {exc}")
        return None

    missing = [c for c in expected_columns if c not in df.columns]
    if missing:
        logger.warning(f"  usd_contract_crm: {label} CSV missing columns {missing} — treated as blank")
        for c in missing:
            df[c] = ""
    return df


# Columns whose combined blankness marks a row as an unfilled template
# placeholder (e.g. the instructional example row shipped in each CSV) rather
# than a real logged opportunity/application/outreach contact.
_IDENTITY_COLUMNS = {
    "usd_pipeline":          ("company_name", "role_title"),
    "job_applications":      ("company_name", "role_title"),
    "recruiter_outreach_log": ("contact_name", "company"),
}


def _drop_blank_template_rows(df: pd.DataFrame, label: str) -> pd.DataFrame:
    cols = _IDENTITY_COLUMNS.get(label)
    if not cols or df.empty:
        return df
    has_identity = pd.Series(False, index=df.index)
    for c in cols:
        if c in df.columns:
            has_identity |= df[c].fillna("").astype(str).str.strip() != ""
    return df[has_identity].reset_index(drop=True)


# ── Manual pipeline score helpers (unchanged from V1) ───────────────────────

def _accepts_brazil_latam(remote_policy: str) -> bool:
    text = _norm_lower(remote_policy)
    return any(kw in text for kw in BRAZIL_LATAM_REMOTE_KEYWORDS)


def _location_blocks_brazil(location_restriction: str) -> bool:
    text = _norm_lower(location_restriction)
    return any(kw in text for kw in LOCATION_BLOCK_KEYWORDS)


def _stack_matches(tech_stack: str) -> bool:
    text = _norm_lower(tech_stack)
    return any(kw in text for kw in STACK_KEYWORDS)


def _contract_is_long_term(contract_type: str) -> bool:
    text = _norm_lower(contract_type)
    if not text:
        return False
    m = re.search(r"(\d+)\s*\+?\s*month", text)
    if m:
        try:
            return int(m.group(1)) >= 6
        except ValueError:
            pass
    long_term_keywords = (
        "permanent", "full-time", "full time", "indefinite", "long-term",
        "long term", "1 year", "annual", "ongoing", "open-ended", "12 month",
        "12-month",
    )
    return any(kw in text for kw in long_term_keywords)


def _below_target_rate(currency: str, rate_type: str, rate_min: str, rate_max: str) -> bool:
    if _norm_lower(currency) != "usd":
        return False
    rt = _norm_lower(rate_type)
    key = None
    if "hour" in rt or rt in ("hr", "h"):
        key = "hourly"
    elif "month" in rt:
        key = "monthly"
    elif "year" in rt or "annual" in rt:
        key = "annual"
    if key is None:
        return False
    target = TARGET_MIN_RATE[key]
    best = _to_float(rate_max)
    if best is None:
        best = _to_float(rate_min)
    if best is None:
        return False
    return best < target


def _stage_index(status: str) -> int:
    return STATUS_ORDER.get(_norm(status).upper(), -1)


def compute_usd_pipeline_score(row: dict) -> int:
    """0-100 score for MANUAL usd_pipeline.csv rows. Never overwrites/interacts
    with relationship_value_score, immediate_action_score, outreach_adjusted_score,
    untapped_outreach_score, or base priority_score — entirely separate score."""
    score = 0
    currency = _norm(row.get("currency", "")).upper()
    status   = _norm(row.get("status", "")).upper()
    stage    = _stage_index(status)

    if currency == "USD":
        score += 25
    if _accepts_brazil_latam(row.get("remote_policy", "")):
        score += 20
    if stage >= STATUS_ORDER["RECRUITER_REPLIED"]:
        score += 15
    if stage >= STATUS_ORDER["CV_REQUESTED"]:
        score += 15
    if stage >= STATUS_ORDER["SUBMITTED_TO_CLIENT"]:
        score += 20
    if _stack_matches(row.get("tech_stack", "")):
        score += 10
    if _contract_is_long_term(row.get("contract_type", "")):
        score += 10
    if _location_blocks_brazil(row.get("location_restriction", "")):
        score -= 30
    if _norm(row.get("timezone_risk", "")).upper() == "HIGH":
        score -= 25
    if currency == "BRL" or _below_target_rate(
        currency, row.get("rate_type", ""), row.get("rate_min", ""), row.get("rate_max", "")
    ):
        score -= 20
    if status in CLOSED_NEGATIVE_STATUSES:
        score -= 15

    return int(max(0, min(100, score)))


def _rate_range(rate_min: str, rate_max: str) -> str:
    lo, hi = _norm(rate_min), _norm(rate_max)
    if lo and hi and lo != hi:
        return f"{lo}-{hi}"
    return hi or lo or ""


# ── Auto-suggested candidate scoring (Part 3 — usd_crm_score) ───────────────

def _company_text(rec: dict) -> str:
    return f"{rec.get('company','') or ''} {rec.get('role','') or ''}"


def _has_keyword_signal(text: str) -> bool:
    return bool(KEYWORD_SIGNAL_RE.search(text or ""))


def _bucket_matches_target(bucket: str) -> bool:
    b = (bucket or "").upper()
    return any(t in b for t in USD_TARGET_BUCKET_SUBSTRINGS)


def _is_never_contacted(rec: dict) -> bool:
    return rec.get("contact_history_status") in NEVER_CONTACTED_STATUSES


def compute_usd_crm_score(rec: dict) -> int:
    """0-100 usd_crm_score for AUTO-SUGGESTED contacts (lead_reactivation /
    untapped_network / classified connections + outreach-adjusted scores).
    Never overwrites relationship_value_score, immediate_action_score,
    outreach_adjusted_score, untapped_outreach_score, or base priority_score
    — those are read-only inputs to this independent score."""
    score = 0
    bucket        = str(rec.get("opportunity_bucket", "") or "").upper()
    persona       = str(rec.get("persona", "") or "")
    lead_category = str(rec.get("lead_category", "") or "")

    if bucket in CONFIRMED_HIGH_VALUE_BUCKETS:
        score += 30
    if bucket == "GLOBAL_STAFFING":
        score += 25
    if bucket == "GLOBAL_OPPORTUNITY":
        score += 20
    if persona in CRM_RECRUITING_PERSONAS:
        score += 25
    if persona in CRM_HIRING_DATA_LEADER_PERSONAS:
        score += 20
    if _to_num(rec.get("untapped_outreach_score")) >= 85:
        score += 20
    if _to_num(rec.get("outreach_adjusted_score")) >= 85:
        score += 15
    if _to_num(rec.get("relationship_value_score")) >= 80:
        score += 15
    if lead_category == "Active Interview Pipeline":
        score += 20
    if lead_category == "Needs my response — Confirmed":
        score += 20
    if lead_category == "Reactivate This Month":  # "Follow-up due" proxy
        score += 15
    if lead_category == "Warm reactivation":
        score += 15
    if _has_keyword_signal(_company_text(rec)):
        score += 10
    if _is_never_contacted(rec):
        score += 10

    rel_val = _to_num(rec.get("relationship_value_score"))
    if lead_category in TERMINAL_LEAD_CATEGORIES and rel_val < 40:
        score -= 25
    if lead_category in BLOCKED_LEAD_CATEGORIES:
        score -= 20

    has_any_usd_signal = (
        _bucket_matches_target(bucket)
        or persona in CRM_TARGET_PERSONAS
        or _has_keyword_signal(_company_text(rec))
        or lead_category in FOLLOWUP_LEAD_CATEGORIES
    )
    if not has_any_usd_signal:
        score -= 15

    return int(max(0, min(100, score)))


def _priority_from_score(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


def _build_usd_crm_reason(rec: dict) -> str:
    reasons = []
    bucket        = str(rec.get("opportunity_bucket", "") or "").upper()
    persona       = str(rec.get("persona", "") or "")
    lead_category = str(rec.get("lead_category", "") or "")

    if bucket in CONFIRMED_HIGH_VALUE_BUCKETS:
        reasons.append(f"confirmed {bucket.replace('_', ' ').title()} opportunity")
    elif bucket == "GLOBAL_STAFFING":
        reasons.append("global staffing company")
    elif bucket == "GLOBAL_OPPORTUNITY":
        reasons.append("global opportunity signal")
    if persona in CRM_RECRUITING_PERSONAS:
        reasons.append(f"{persona} persona")
    elif persona in CRM_HIRING_DATA_LEADER_PERSONAS:
        reasons.append(f"{persona} persona (hiring authority)")
    us = _to_num(rec.get("untapped_outreach_score"))
    if us:
        reasons.append(f"untapped outreach score {int(us)}")
    oa = _to_num(rec.get("outreach_adjusted_score"))
    if oa:
        reasons.append(f"outreach adjusted score {int(oa)}")
    rv = _to_num(rec.get("relationship_value_score"))
    if rv:
        reasons.append(f"relationship value score {int(rv)}")
    if lead_category:
        reasons.append(f"lead status: {lead_category}")
    if _has_keyword_signal(_company_text(rec)):
        reasons.append("company/title USD or LATAM keyword match")
    if _is_never_contacted(rec):
        reasons.append("connected but never contacted")
    if not reasons:
        reasons.append("matched USD pipeline criteria")
    return "; ".join(reasons)


def _build_usd_crm_recommended_action(rec: dict, record_type: str) -> str:
    if rec.get("recommended_first_action"):
        return rec["recommended_first_action"]
    if rec.get("recommended_next_action"):
        return rec["recommended_next_action"]
    return {
        "recruiter_pipeline": "CONTACT_RECRUITER",
        "auto_suggested_lead": "REVIEW_AND_CONTACT",
        "first_outreach": "SEND_FIRST_MESSAGE",
        "auto_followup": "SEND_FOLLOW_UP",
        "active_process": "CHECK_PROCESS_STATUS",
    }.get(record_type, "REVIEW")


def _resolve_next_action_date(rec: dict) -> str:
    return _norm(rec.get("lead_next_action_date")) or _norm(rec.get("outreach_next_action_date")) or ""


# ── Unified public row schema (Part 4) ──────────────────────────────────────
# Every row across every array (manual or auto-suggested) is normalized into
# this exact, explicit field set — nothing else is ever emitted.
PUBLIC_ROW_FIELDS = [
    "name", "company", "role", "persona", "opportunity_bucket", "source",
    "record_type", "status", "score", "priority", "recommended_action",
    "reason", "next_action", "next_action_date", "profile_url", "role_url",
    "currency", "rate_range", "remote_policy", "timezone_required",
    "timezone_risk", "payment_risk", "contract_risk",
]


def _empty_public_row() -> dict:
    row = {k: "" for k in PUBLIC_ROW_FIELDS}
    row["score"] = 0
    return row


def _auto_row_to_public(rec: dict, record_type: str, source: str) -> dict:
    score = compute_usd_crm_score(rec)
    row = _empty_public_row()
    row.update({
        "name":               rec.get("full_name", "") or "",
        "company":            rec.get("company", "") or "",
        "role":               rec.get("role", "") or "",
        "persona":            rec.get("persona", "") or "",
        "opportunity_bucket": rec.get("opportunity_bucket", "") or "",
        "source":             source,
        "record_type":        record_type,
        "status":             rec.get("lead_category") or rec.get("contact_history_status") or "",
        "score":              score,
        "priority":           _priority_from_score(score),
        "recommended_action": _build_usd_crm_recommended_action(rec, record_type),
        "reason":             _build_usd_crm_reason(rec),
        "next_action":        rec.get("recommended_next_action") or rec.get("recommended_first_action") or "",
        "next_action_date":   _resolve_next_action_date(rec),
        "profile_url":        rec.get("profile_url", "") or "",
    })
    return row


def _manual_pipeline_row_to_public(r: dict) -> dict:
    row = _empty_public_row()
    score = int(r.get("usd_pipeline_score", 0) or 0)
    row.update({
        "name":               _norm(r.get("recruiter_name")),
        "company":            _norm(r.get("company_name")),
        "role":               _norm(r.get("role_title")),
        "persona":            "",
        "opportunity_bucket": _norm(r.get("source_type")).upper(),
        "source":             "manual",
        "record_type":        "manual_opportunity",
        "status":             _norm(r.get("status")).upper(),
        "score":              score,
        "priority":           _norm(r.get("priority")).upper() or _priority_from_score(score),
        "recommended_action": "",
        "reason":             "",
        "next_action":        _norm(r.get("next_action")),
        "next_action_date":   _norm(r.get("next_action_date")),
        "profile_url":        _norm(r.get("recruiter_profile_url")),
        "role_url":           _norm(r.get("role_url")),
        "currency":           _norm(r.get("currency")),
        "rate_range":         _rate_range(r.get("rate_min", ""), r.get("rate_max", "")),
        "remote_policy":      _norm(r.get("remote_policy")),
        "timezone_required":  _norm(r.get("timezone_required")),
        "timezone_risk":      _norm(r.get("timezone_risk")).upper(),
        "payment_risk":       _norm(r.get("payment_risk")).upper(),
        "contract_risk":      _norm(r.get("contract_risk")).upper(),
    })
    return row


def _manual_application_row_to_public(r: dict) -> dict:
    row = _empty_public_row()
    follow_up = _norm(r.get("follow_up_date"))
    row.update({
        "company":          _norm(r.get("company_name")),
        "role":              _norm(r.get("role_title")),
        "source":            "manual",
        "record_type":       "manual_application",
        "status":            _norm(r.get("status")).upper(),
        "reason":            _norm(r.get("rejection_reason")),
        "next_action":       "Follow up on application" if follow_up else "",
        "next_action_date":  follow_up,
        "role_url":          _norm(r.get("role_url")),
        "currency":          _norm(r.get("currency")),
        "rate_range":        _norm(r.get("expected_rate")),
    })
    return row


def _manual_outreach_row_to_public(r: dict) -> dict:
    row = _empty_public_row()
    row.update({
        "name":             _norm(r.get("contact_name")),
        "company":          _norm(r.get("company")),
        "opportunity_bucket": _norm(r.get("opportunity_bucket")).upper(),
        "source":           "manual",
        "record_type":      "manual_outreach",
        "status":           _norm(r.get("status")).upper(),
        "next_action":      _norm(r.get("next_action")),
        "next_action_date": _norm(r.get("next_action_date")),
        "profile_url":      _norm(r.get("profile_url")),
    })
    return row


# ── Auto-suggested candidate pool (Part 1B-E) ───────────────────────────────
# Merges already-sanitized, already-computed signals from the classified
# connections dataframe, outreach-adjusted scores, Untapped Network
# Intelligence, and Lead Reactivation into ONE per-profile-URL index, so each
# of the four auto-suggested sections is a simple filter over the same pool
# instead of four separate re-derivations.

def _build_auto_candidate_pool(
    classified_df: pd.DataFrame | None,
    outreach_scores: dict | None,
    untapped_data: dict | None,
    lead_data: dict | None,
) -> dict:
    pool: dict[str, dict] = {}

    def _entry(url: str) -> dict:
        return pool.setdefault(url, {"profile_url": url})

    if classified_df is not None and not classified_df.empty:
        cols = ["full_name", "company_clean", "position_clean", "persona",
                "opportunity_bucket", "priority_score", "url"]
        for col in cols:
            if col not in classified_df.columns:
                classified_df = classified_df.assign(**{col: ""})
        for _, r in classified_df[cols].iterrows():
            url = _norm_url(r.get("url", ""))
            if not url:
                continue
            e = _entry(url)
            e["full_name"]          = _norm(r.get("full_name"))
            e["company"]            = _norm(r.get("company_clean"))
            e["role"]               = _norm(r.get("position_clean"))
            e["persona"]            = _norm(r.get("persona"))
            e["opportunity_bucket"] = _norm(r.get("opportunity_bucket")).upper()
            e["priority_score"]     = _to_num(r.get("priority_score"))

    for url_raw, rec in (outreach_scores or {}).items():
        u = _norm_url(url_raw)
        if not u:
            continue
        e = _entry(u)
        e["outreach_adjusted_score"]  = rec.get("outreach_adjusted_score")
        e["relationship_value_score"] = rec.get("relationship_value_score")
        e["immediate_action_score"]   = rec.get("immediate_action_score")
        e["process_state"]            = rec.get("process_state")
        e["reply_obligation"]         = rec.get("reply_obligation")
        e["outreach_next_action_date"] = rec.get("next_action_date")

    for c in (untapped_data or {}).get("top_untapped_contacts", []) or []:
        u = _norm_url(c.get("profile_url", ""))
        if not u:
            continue
        e = _entry(u)
        e["untapped_outreach_score"]  = _to_float(c.get("untapped_outreach_score"))
        e["contact_history_status"]   = c.get("contact_history_status")
        e["untapped_category"]        = c.get("untapped_category")
        e["strategic_focus"]          = c.get("strategic_focus")
        e["recommended_first_action"] = c.get("recommended_first_action")
        e["first_message_angle"]      = c.get("first_message_angle")
        e.setdefault("full_name", _norm(c.get("full_name")))
        e.setdefault("company", _norm(c.get("company_clean")))
        e.setdefault("role", _norm(c.get("position_clean")))
        e.setdefault("persona", _norm(c.get("persona")))
        e.setdefault("opportunity_bucket", _norm(c.get("opportunity_bucket")).upper())

    for c in (lead_data or {}).get("top_reactivation_contacts", []) or []:
        u = _norm_url(c.get("other_person_profile_url", ""))
        if not u:
            continue
        e = _entry(u)
        e["lead_category"]               = c.get("lead_category")
        e["reactivation_priority_score"] = _to_float(c.get("reactivation_priority_score"))
        e["recommended_next_action"]     = c.get("recommended_next_action")
        e["lead_next_action_date"]       = c.get("next_action_date")
        e["has_cv_signal"]               = _truthy(c.get("has_cv_signal"))
        e["has_interview_signal"]        = _truthy(c.get("has_interview_signal"))
        e["has_positive_signal"]         = _truthy(c.get("has_positive_signal"))
        e["strategic_market"]            = c.get("strategic_market")
        e.setdefault("full_name", _norm(c.get("other_person_name")))
        e.setdefault("company", _norm(c.get("company_clean")))
        e.setdefault("role", _norm(c.get("position_clean")))
        e.setdefault("persona", _norm(c.get("persona")))

    return pool


def _match_section_b_lead(rec: dict) -> bool:
    """Auto-Suggested USD Recruiter Pipeline (Part 1B) — inclusion criteria."""
    if _bucket_matches_target(rec.get("opportunity_bucket", "")):
        return True
    if rec.get("persona") in CRM_TARGET_PERSONAS:
        return True
    if _to_num(rec.get("untapped_outreach_score")) >= 70:
        return True
    if _to_num(rec.get("outreach_adjusted_score")) >= 70:
        return True
    if _to_num(rec.get("relationship_value_score")) >= 70:
        return True
    if rec.get("lead_category") in {
        "Warm reactivation", "Active Interview Pipeline",
        "Needs my response — Confirmed", "Needs my response — Likely",
        "Reactivate This Month",
    }:
        return True
    if _has_keyword_signal(_company_text(rec)):
        return True
    return False


def _match_section_c_followup(rec: dict) -> bool:
    """Auto-Suggested Follow-up Queue (Part 1C) — inclusion criteria."""
    if rec.get("lead_category") in FOLLOWUP_LEAD_CATEGORIES:
        return True
    if rec.get("has_cv_signal"):
        return True
    if rec.get("lead_category") == "No response" and (
        _to_num(rec.get("relationship_value_score")) >= 50
        or _bucket_matches_target(rec.get("opportunity_bucket", ""))
        or str(rec.get("strategic_market", "")) in USD_STRATEGIC_MARKETS
    ):
        return True
    return False


def _match_section_d_first_outreach(rec: dict) -> bool:
    """Auto-Suggested First Outreach Queue (Part 1D) — Untapped Network only."""
    if not _is_never_contacted(rec):
        return False
    if rec.get("persona") in CRM_TARGET_PERSONAS:
        return True
    if rec.get("strategic_focus") == "PRIMARY_LATAM_USD":
        return True
    if _to_num(rec.get("untapped_outreach_score")) >= 70:
        return True
    if rec.get("untapped_category") == "HIGH_VALUE_UNTAPPED":
        return True
    return False


def _match_section_e_active_process(rec: dict) -> bool:
    """Auto-Suggested Interview / Active Process Pipeline (Part 1E)."""
    if rec.get("lead_category") in ACTIVE_PROCESS_LEAD_CATEGORIES:
        return True
    if rec.get("has_cv_signal") or rec.get("has_interview_signal"):
        return True
    return False


def _auto_source_label(rec: dict) -> str:
    if _bucket_matches_target(rec.get("opportunity_bucket", "")):
        return "opportunity_market"
    if rec.get("lead_category"):
        return "lead_reactivation"
    if rec.get("untapped_outreach_score") is not None:
        return "untapped_network"
    return "top_contacts"


def _build_auto_suggested_sections(pool: dict) -> dict:
    auto_leads, recruiter_pipeline = [], []
    follow_up_auto, first_outreach, active_process_auto = [], [], []

    for rec in pool.values():
        if _match_section_b_lead(rec):
            source = _auto_source_label(rec)
            auto_leads.append(_auto_row_to_public(rec, "auto_suggested_lead", source))
            if rec.get("persona") in CRM_TARGET_PERSONAS:
                recruiter_pipeline.append(_auto_row_to_public(rec, "recruiter_pipeline", source))
        if _match_section_c_followup(rec):
            source = "lead_reactivation" if rec.get("lead_category") else "top_contacts"
            follow_up_auto.append(_auto_row_to_public(rec, "auto_followup", source))
        if _match_section_d_first_outreach(rec):
            first_outreach.append(_auto_row_to_public(rec, "first_outreach", "untapped_network"))
        if _match_section_e_active_process(rec):
            active_process_auto.append(_auto_row_to_public(rec, "active_process", "lead_reactivation"))

    for lst in (auto_leads, recruiter_pipeline, follow_up_auto, first_outreach, active_process_auto):
        lst.sort(key=lambda r: r["score"], reverse=True)

    cv_signal_count = sum(1 for rec in pool.values() if rec.get("has_cv_signal"))

    return {
        "auto_suggested_usd_leads": auto_leads,
        "recruiter_pipeline":       recruiter_pipeline,
        "follow_up_auto":           follow_up_auto,
        "first_outreach_queue":     first_outreach,
        "active_process_auto":      active_process_auto,
        "auto_cv_signal_count":     cv_signal_count,
    }


# ── Manual sanitized builders ────────────────────────────────────────────────

def _build_manual_opportunities(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return [_manual_pipeline_row_to_public(r) for _, r in df.iterrows()]


def _build_manual_applications(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return [_manual_application_row_to_public(r) for _, r in df.iterrows()]


def _build_outreach_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "total_contacted": 0, "total_replied": 0, "reply_rate_pct": 0.0,
            "scheduled_calls": 0, "ghosted": 0, "no_response": 0,
        }
    statuses = df["status"].fillna("").str.upper()
    total = len(df)
    has_reply_date = df.get("last_reply_date", pd.Series([""] * total)).fillna("").astype(str).str.strip() != ""
    replied = int(((statuses == "REPLIED") | has_reply_date).sum())
    return {
        "total_contacted":  total,
        "total_replied":    replied,
        "reply_rate_pct":   round(100 * replied / total, 1) if total else 0.0,
        "scheduled_calls":  int((statuses == "SCHEDULED_CALL").sum()),
        "ghosted":          int((statuses == "GHOSTED").sum()),
        "no_response":      int((statuses == "NO_RESPONSE").sum()),
    }


# ── Follow-up queue (hybrid: manual + auto) ─────────────────────────────────

def _build_follow_up_queue(pipeline_df: pd.DataFrame, applications_df: pd.DataFrame,
                            outreach_df: pd.DataFrame, auto_followups: list[dict]) -> list[dict]:
    rows: list[dict] = []

    for _, r in pipeline_df.iterrows():
        status = _norm(r.get("status")).upper()
        if status in CLOSED_NEGATIVE_STATUSES or status == "CLOSED_WON":
            continue
        if not _norm(r.get("next_action_date")):
            continue
        row = _manual_pipeline_row_to_public(r.to_dict())
        row["record_type"] = "manual_followup"
        rows.append(row)

    for _, r in applications_df.iterrows():
        status = _norm(r.get("status")).upper()
        if status in ("REJECTED", "WITHDRAWN") or not _norm(r.get("follow_up_date")):
            continue
        row = _manual_application_row_to_public(r.to_dict())
        row["record_type"] = "manual_followup"
        rows.append(row)

    for _, r in outreach_df.iterrows():
        status = _norm(r.get("status")).upper()
        if status == "CLOSED" or not _norm(r.get("next_action_date")):
            continue
        row = _manual_outreach_row_to_public(r.to_dict())
        row["record_type"] = "manual_followup"
        rows.append(row)

    rows.extend(auto_followups)
    rows.sort(key=lambda x: (x["next_action_date"] == "", x["next_action_date"]))
    return rows


def _count_due_or_overdue(follow_up_queue: list[dict]) -> int:
    today = date.today()
    count = 0
    for r in follow_up_queue:
        d = _parse_date(r.get("next_action_date", ""))
        if d and d <= today:
            count += 1
    return count


# ── Contingency risk view (manual pipeline only, per spec) ──────────────────

def _build_contingency_risk(manual_opportunities: list[dict]) -> dict:
    high_risk = [
        r for r in manual_opportunities
        if "HIGH" in (r.get("timezone_risk", ""), r.get("payment_risk", ""), r.get("contract_risk", ""))
    ]
    backup = [r for r in manual_opportunities if r.get("priority") == "BACKUP"]
    return {"high_risk": high_risk, "backup": backup}


# ── Summary (Part 4) ─────────────────────────────────────────────────────────

def _build_summary(manual_opportunities: list[dict], manual_applications: list[dict],
                    outreach_summary: dict, follow_up_queue: list[dict],
                    contingency_risk: dict, auto_sections: dict,
                    manual_pipeline_df: pd.DataFrame, active_process_pipeline: list[dict]) -> dict:
    manual_cv_signals = int((
        manual_pipeline_df["status"].fillna("").str.upper().apply(_stage_index) >= STATUS_ORDER["CV_REQUESTED"]
    ).sum()) if not manual_pipeline_df.empty else 0

    return {
        "manual_usd_opportunities":       len(manual_opportunities),
        "auto_suggested_usd_leads":       len(auto_sections["auto_suggested_usd_leads"]),
        "recommended_recruiters_to_contact": len(auto_sections["recruiter_pipeline"]),
        "recommended_first_outreach":     len(auto_sections["first_outreach_queue"]),
        "recommended_followups":          len(follow_up_queue),
        "active_interview_signals":       len(active_process_pipeline),
        "manual_applications_sent":       len(manual_applications),
        "cv_requested_or_sent_signals":   manual_cv_signals + auto_sections["auto_cv_signal_count"],
        "recruiters_replied":             outreach_summary.get("total_replied", 0),
        "followups_due":                  _count_due_or_overdue(follow_up_queue),
        "high_risk_manual_opportunities": len(contingency_risk["high_risk"]),
        "backup_manual_opportunities":    len(contingency_risk["backup"]),
    }


# ── Main entry point ─────────────────────────────────────────────────────────

def run_usd_contract_crm(
    classified_df: pd.DataFrame | None = None,
    lead_data: dict | None = None,
    untapped_data: dict | None = None,
    outreach_scores: dict | None = None,
    opportunity_history_data: dict | None = None,
) -> dict:
    """Hybrid USD Contract CRM — five distinct, never-conflated sections:
      A. Manual records from data/manual/*.csv (optional, can be empty).
      B. Auto-suggested USD pipeline records derived from Lead Reactivation,
         Untapped Network Intelligence, and classified connections + outreach
         scores — so the CRM is never empty just because manual CSVs are
         empty/absent, as long as the weekly pipeline has produced any of
         that intelligence.
      C. Inbound opportunity history (src/opportunity_history_engine.py) —
         monthly message-intelligence-derived events. Passed through
         unmodified (already sanitized/classified upstream); never merged
         into A/B's counts, so soft-closed or auto-suggested items can never
         be counted as manual applications or confirmed opportunities.
      D. Reactivation calendar (from the same opportunity history engine).
      E. Soft-closed future leads (from the same engine) — never counted as
         active applications or as a rejection.

    Returns {"available": False} only when manual CSVs, auto-suggested
    intelligence, AND opportunity history are all entirely absent (e.g. no
    LinkedIn export has ever been processed at all).
    """
    pipeline_raw     = _read_manual_csv(USD_PIPELINE_CSV, PIPELINE_COLUMNS, "usd_pipeline")
    applications_raw = _read_manual_csv(JOB_APPLICATIONS_CSV, APPLICATION_COLUMNS, "job_applications")
    outreach_raw      = _read_manual_csv(RECRUITER_OUTREACH_CSV, OUTREACH_COLUMNS, "recruiter_outreach_log")

    pipeline_df     = pipeline_raw if pipeline_raw is not None else pd.DataFrame(columns=PIPELINE_COLUMNS)
    applications_df = applications_raw if applications_raw is not None else pd.DataFrame(columns=APPLICATION_COLUMNS)
    outreach_df      = outreach_raw if outreach_raw is not None else pd.DataFrame(columns=OUTREACH_COLUMNS)

    pipeline_df     = _drop_blank_template_rows(pipeline_df, "usd_pipeline")
    applications_df = _drop_blank_template_rows(applications_df, "job_applications")
    outreach_df      = _drop_blank_template_rows(outreach_df, "recruiter_outreach_log")

    if not pipeline_df.empty:
        pipeline_df = pipeline_df.copy()
        pipeline_df["usd_pipeline_score"] = pipeline_df.apply(
            lambda r: compute_usd_pipeline_score(r.to_dict()), axis=1
        )
    else:
        pipeline_df["usd_pipeline_score"] = pd.Series(dtype=int)

    pool = _build_auto_candidate_pool(classified_df, outreach_scores, untapped_data, lead_data)
    auto_sections = _build_auto_suggested_sections(pool)

    manual_available = not (pipeline_df.empty and applications_df.empty and outreach_df.empty)
    auto_available = any(auto_sections[k] for k in (
        "auto_suggested_usd_leads", "recruiter_pipeline", "follow_up_auto",
        "first_outreach_queue", "active_process_auto",
    ))
    opportunity_history_available = bool(opportunity_history_data and opportunity_history_data.get("available"))

    if not manual_available and not auto_available and not opportunity_history_available:
        logger.info("  USD Contract CRM: no manual CSVs, no auto-suggested intelligence, and no "
                    "opportunity history — skipping.")
        return {"available": False}

    manual_opportunities = _build_manual_opportunities(pipeline_df)
    manual_applications  = _build_manual_applications(applications_df)
    outreach_summary      = _build_outreach_summary(outreach_df)
    follow_up_queue       = _build_follow_up_queue(pipeline_df, applications_df, outreach_df, auto_sections["follow_up_auto"])
    contingency_risk      = _build_contingency_risk(manual_opportunities)
    active_process_pipeline = []
    if not pipeline_df.empty:
        active_mask = pipeline_df["status"].fillna("").str.upper().isin(ACTIVE_PROCESS_STATUSES)
        for r in pipeline_df[active_mask].to_dict(orient="records"):
            row = _manual_pipeline_row_to_public(r)
            row["record_type"] = "manual_active_process"
            active_process_pipeline.append(row)
    active_process_pipeline.extend(auto_sections["active_process_auto"])

    summary = _build_summary(
        manual_opportunities, manual_applications, outreach_summary,
        follow_up_queue, contingency_risk, auto_sections, pipeline_df,
        active_process_pipeline,
    )

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"metric": k, "value": v} for k, v in summary.items()]).to_csv(
        OUTPUTS_DIR / "usd_contract_pipeline_summary.csv", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame(manual_opportunities, columns=PUBLIC_ROW_FIELDS).to_csv(
        OUTPUTS_DIR / "usd_contract_pipeline_public.csv", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame([{"metric": k, "value": v} for k, v in outreach_summary.items()]).to_csv(
        OUTPUTS_DIR / "usd_recruiter_outreach_summary.csv", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame(manual_applications, columns=PUBLIC_ROW_FIELDS).to_csv(
        OUTPUTS_DIR / "usd_application_tracker_public.csv", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame(follow_up_queue, columns=PUBLIC_ROW_FIELDS if follow_up_queue else None).to_csv(
        OUTPUTS_DIR / "usd_follow_up_queue.csv", index=False, encoding="utf-8-sig",
    )

    oh_summary = (opportunity_history_data or {}).get("summary", {}) if opportunity_history_available else {}
    logger.info(
        f"  USD Contract CRM: manual_opportunities={summary['manual_usd_opportunities']} "
        f"auto_suggested_leads={summary['auto_suggested_usd_leads']} "
        f"recommended_recruiters={summary['recommended_recruiters_to_contact']} "
        f"first_outreach={summary['recommended_first_outreach']} "
        f"followups={summary['recommended_followups']} (due={summary['followups_due']}) "
        f"active_interview_signals={summary['active_interview_signals']} "
        f"manual_applications={summary['manual_applications_sent']} "
        f"high_risk={summary['high_risk_manual_opportunities']} | "
        f"opportunity_history: inbound={oh_summary.get('inbound_opportunities_total', 0)} "
        f"soft_closed={oh_summary.get('soft_closed_total', 0)} "
        f"reactivation_due_now={oh_summary.get('reactivation_due_now', 0)}"
    )

    return {
        "available":              True,
        "summary":                summary,
        "manual_opportunities":   manual_opportunities,
        "auto_suggested_usd_leads": auto_sections["auto_suggested_usd_leads"],
        "recruiter_pipeline":     auto_sections["recruiter_pipeline"],
        "first_outreach_queue":   auto_sections["first_outreach_queue"],
        "follow_up_queue":        follow_up_queue,
        "active_process_pipeline": active_process_pipeline,
        "manual_applications":    manual_applications,
        "contingency_risk":       contingency_risk,
        "outreach_summary":       outreach_summary,
        # C/D/E — Opportunity History (src/opportunity_history_engine.py).
        # Passed through as its own section: never merged into manual/auto
        # counts above, so soft-closed or inbound-only events can never be
        # miscounted as confirmed applications or active processes.
        "opportunity_history":    opportunity_history_data or {"available": False},
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    result = run_usd_contract_crm()
    if not result.get("available"):
        print("No USD CRM data yet. Add manual CSVs or run the weekly pipeline to "
              "generate Lead Reactivation / Untapped / Top Contacts intelligence.")
    else:
        print(result["summary"])
