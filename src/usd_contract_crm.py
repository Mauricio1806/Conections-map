# -*- coding: utf-8 -*-
"""
usd_contract_crm.py
====================
USD Contract CRM — tracks real USD job opportunities, recruiter outreach,
applications, interviews, follow-ups, and contingency risk.

Inputs are local/manual only, entirely optional, gitignored, and never
committed (see .gitignore):
  - data/manual/usd_pipeline.csv
  - data/manual/job_applications.csv
  - data/manual/recruiter_outreach_log.csv

No LinkedIn scraping, no application automation — this module only reads
CSVs the user fills in by hand and computes sanitized aggregates/scores.

Expected enum-style values (informal, tolerant — unknown/blank values never
crash, they just don't contribute to the relevant KPI):
  status (usd_pipeline.csv):  NEW, RESEARCHING, OUTREACH_SENT,
    RECRUITER_REPLIED, CV_REQUESTED, CV_SENT, SUBMITTED_TO_CLIENT,
    RECRUITER_CALL_SCHEDULED, RECRUITER_CALL_DONE,
    TECHNICAL_INTERVIEW_SCHEDULED, TECHNICAL_INTERVIEW_DONE,
    FINAL_INTERVIEW, OFFER, CLOSED_WON, CLOSED_LOST, REJECTED, ON_HOLD
  priority (usd_pipeline.csv): HIGH, MEDIUM, LOW, BACKUP
  timezone_risk / payment_risk / contract_risk: LOW, MEDIUM, HIGH
  status (job_applications.csv): APPLIED, IN_REVIEW, INTERVIEW, OFFER,
    REJECTED, WITHDRAWN
  status (recruiter_outreach_log.csv): SENT, REPLIED, NO_RESPONSE,
    GHOSTED, SCHEDULED_CALL, CLOSED

Private fields (notes_private, raw message content, emails, phone numbers)
are never emitted in any public/sanitized output — see PRIVATE_FIELDS and
the explicit allowlists below.
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

# ── Pipeline funnel stages (Part: USD Pipeline Score + KPI cards) ───────────
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


def _norm(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _norm_lower(v) -> str:
    return _norm(v).lower()


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


# ── Score/flag helpers ──────────────────────────────────────────────────────

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
    """0-100 USD Pipeline Score — additive rules per the CRM spec. Never
    overwrites/interacts with relationship_value_score, immediate_action_score,
    outreach_adjusted_score, untapped_outreach_score, or base priority_score;
    this is an entirely separate, independent score."""
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


# ── Sanitized public builders ────────────────────────────────────────────────

PUBLIC_PIPELINE_FIELDS = [
    "company_name", "role_title", "role_url", "source_type", "currency",
    "rate_range", "rate_type", "contract_type", "remote_policy",
    "timezone_required", "overlap_required", "tech_stack", "status",
    "next_action", "next_action_date", "priority", "timezone_risk",
    "payment_risk", "contract_risk", "usd_pipeline_score", "recruiter_name",
    "recruiter_profile_url",
]

PUBLIC_APPLICATION_FIELDS = [
    "application_date", "company_name", "role_title", "role_url", "source",
    "currency", "expected_rate", "status", "cv_version", "recruiter_contacted",
    "follow_up_date", "result", "rejection_reason",
]

PUBLIC_OUTREACH_FIELDS = [
    "date", "contact_name", "profile_url", "company", "source",
    "opportunity_bucket", "message_type", "status", "last_reply_date",
    "next_action", "next_action_date", "usd_signal", "latam_signal",
    "timezone_signal",
]


def _build_public_pipeline(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "company_name":          _norm(r.get("company_name")),
            "role_title":            _norm(r.get("role_title")),
            "role_url":              _norm(r.get("role_url")),
            "source_type":           _norm(r.get("source_type")),
            "currency":              _norm(r.get("currency")),
            "rate_range":            _rate_range(r.get("rate_min", ""), r.get("rate_max", "")),
            "rate_type":             _norm(r.get("rate_type")),
            "contract_type":         _norm(r.get("contract_type")),
            "remote_policy":         _norm(r.get("remote_policy")),
            "timezone_required":     _norm(r.get("timezone_required")),
            "overlap_required":      _norm(r.get("overlap_required")),
            "tech_stack":            _norm(r.get("tech_stack")),
            "status":                _norm(r.get("status")).upper(),
            "next_action":           _norm(r.get("next_action")),
            "next_action_date":      _norm(r.get("next_action_date")),
            "priority":              _norm(r.get("priority")).upper(),
            "timezone_risk":         _norm(r.get("timezone_risk")).upper(),
            "payment_risk":          _norm(r.get("payment_risk")).upper(),
            "contract_risk":         _norm(r.get("contract_risk")).upper(),
            "usd_pipeline_score":    int(r.get("usd_pipeline_score", 0) or 0),
            "recruiter_name":        _norm(r.get("recruiter_name")),
            "recruiter_profile_url": _norm(r.get("recruiter_profile_url")),
        })
    return rows


def _build_public_applications(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({k: _norm(r.get(k)) for k in PUBLIC_APPLICATION_FIELDS})
    return rows


def _build_public_outreach_contacts(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({k: _norm(r.get(k)) for k in PUBLIC_OUTREACH_FIELDS})
    return rows


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


# ── Follow-up queue ──────────────────────────────────────────────────────────

def _build_follow_up_queue(pipeline_df: pd.DataFrame, applications_df: pd.DataFrame,
                            outreach_df: pd.DataFrame) -> list[dict]:
    today = date.today()
    rows: list[dict] = []

    for _, r in pipeline_df.iterrows():
        status = _norm(r.get("status")).upper()
        if status in CLOSED_NEGATIVE_STATUSES or status == "CLOSED_WON":
            continue
        d = _parse_date(r.get("next_action_date", ""))
        if not d:
            continue
        rows.append({
            "source_type":      "pipeline",
            "name":             f"{_norm(r.get('company_name'))} — {_norm(r.get('role_title'))}".strip(" —"),
            "next_action":      _norm(r.get("next_action")),
            "next_action_date": str(d),
            "status":           status,
            "priority":         _norm(r.get("priority")).upper(),
            "overdue":          d < today,
        })

    for _, r in applications_df.iterrows():
        status = _norm(r.get("status")).upper()
        if status in ("REJECTED", "WITHDRAWN"):
            continue
        d = _parse_date(r.get("follow_up_date", ""))
        if not d:
            continue
        rows.append({
            "source_type":      "application",
            "name":             f"{_norm(r.get('company_name'))} — {_norm(r.get('role_title'))}".strip(" —"),
            "next_action":      "Follow up on application",
            "next_action_date": str(d),
            "status":           status,
            "priority":         "",
            "overdue":          d < today,
        })

    for _, r in outreach_df.iterrows():
        status = _norm(r.get("status")).upper()
        if status == "CLOSED":
            continue
        d = _parse_date(r.get("next_action_date", ""))
        if not d:
            continue
        rows.append({
            "source_type":      "outreach",
            "name":             f"{_norm(r.get('contact_name'))} — {_norm(r.get('company'))}".strip(" —"),
            "next_action":      _norm(r.get("next_action")),
            "next_action_date": str(d),
            "status":           status,
            "priority":         "",
            "overdue":          d < today,
        })

    rows.sort(key=lambda x: x["next_action_date"])
    return rows


# ── Contingency risk view ────────────────────────────────────────────────────

def _build_risk_view(public_pipeline: list[dict]) -> dict:
    high_risk = [
        r for r in public_pipeline
        if "HIGH" in (r.get("timezone_risk", ""), r.get("payment_risk", ""), r.get("contract_risk", ""))
    ]
    backup = [r for r in public_pipeline if r.get("priority") == "BACKUP"]
    return {"high_risk": high_risk, "backup": backup}


# ── Summary (Executive cards) ────────────────────────────────────────────────

def _build_summary(pipeline_df: pd.DataFrame, applications_df: pd.DataFrame,
                    outreach_df: pd.DataFrame, follow_up_queue: list[dict],
                    risk_view: dict) -> dict:
    statuses = pipeline_df["status"].fillna("").str.upper() if not pipeline_df.empty else pd.Series([], dtype=str)
    stages = statuses.apply(_stage_index)

    outreach_statuses = outreach_df["status"].fillna("").str.upper() if not outreach_df.empty else pd.Series([], dtype=str)
    has_reply_date = (
        outreach_df.get("last_reply_date", pd.Series([""] * len(outreach_df))).fillna("").astype(str).str.strip() != ""
        if not outreach_df.empty else pd.Series([], dtype=bool)
    )
    recruiters_replied = int(((outreach_statuses == "REPLIED") | has_reply_date).sum())

    due_or_overdue = sum(1 for r in follow_up_queue if _parse_date(r["next_action_date"]) and _parse_date(r["next_action_date"]) <= date.today())

    return {
        "usd_opportunities_found":  len(pipeline_df),
        "applications_sent":       len(applications_df),
        "recruiters_contacted":    len(outreach_df),
        "recruiters_replied":      recruiters_replied,
        "cvs_sent":                int((stages >= STATUS_ORDER["CV_SENT"]).sum()),
        "client_submissions":      int((stages >= STATUS_ORDER["SUBMITTED_TO_CLIENT"]).sum()),
        "recruiter_calls_booked":  int((stages >= STATUS_ORDER["RECRUITER_CALL_SCHEDULED"]).sum()),
        "technical_interviews":    int((stages >= STATUS_ORDER["TECHNICAL_INTERVIEW_SCHEDULED"]).sum()),
        "active_usd_processes":    int(statuses.isin(ACTIVE_PROCESS_STATUSES).sum()),
        "follow_ups_due":          due_or_overdue,
        "high_risk_opportunities": len(risk_view["high_risk"]),
        "backup_opportunities":    len(risk_view["backup"]),
    }


# ── Main entry point ─────────────────────────────────────────────────────────

def run_usd_contract_crm() -> dict:
    """Reads the three manual CSVs (if present), computes the USD Pipeline
    Score, sanitized aggregates, and writes the five sanitized output CSVs.
    Returns a dict consumed by export_public_dashboard_data.py to build the
    public `usd_contract_crm` JSON key. Entirely safe to call when none of
    the manual CSVs exist — returns {"available": False} and writes nothing."""
    pipeline_raw     = _read_manual_csv(USD_PIPELINE_CSV, PIPELINE_COLUMNS, "usd_pipeline")
    applications_raw = _read_manual_csv(JOB_APPLICATIONS_CSV, APPLICATION_COLUMNS, "job_applications")
    outreach_raw      = _read_manual_csv(RECRUITER_OUTREACH_CSV, OUTREACH_COLUMNS, "recruiter_outreach_log")

    if pipeline_raw is None and applications_raw is None and outreach_raw is None:
        logger.info("  USD Contract CRM: no manual CSVs found — skipping (this is normal/optional).")
        return {"available": False}

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

    public_pipeline      = _build_public_pipeline(pipeline_df)
    public_applications  = _build_public_applications(applications_df)
    public_outreach      = _build_public_outreach_contacts(outreach_df)
    outreach_summary     = _build_outreach_summary(outreach_df)
    follow_up_queue      = _build_follow_up_queue(pipeline_df, applications_df, outreach_df)
    risk_view            = _build_risk_view(public_pipeline)
    summary              = _build_summary(pipeline_df, applications_df, outreach_df, follow_up_queue, risk_view)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"metric": k, "value": v} for k, v in summary.items()]).to_csv(
        OUTPUTS_DIR / "usd_contract_pipeline_summary.csv", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame(public_pipeline, columns=PUBLIC_PIPELINE_FIELDS).to_csv(
        OUTPUTS_DIR / "usd_contract_pipeline_public.csv", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame([{"metric": k, "value": v} for k, v in outreach_summary.items()]).to_csv(
        OUTPUTS_DIR / "usd_recruiter_outreach_summary.csv", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame(public_applications, columns=PUBLIC_APPLICATION_FIELDS).to_csv(
        OUTPUTS_DIR / "usd_application_tracker_public.csv", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame(follow_up_queue).to_csv(
        OUTPUTS_DIR / "usd_follow_up_queue.csv", index=False, encoding="utf-8-sig",
    )

    logger.info(
        f"  USD Contract CRM: {summary['usd_opportunities_found']} opportunities | "
        f"{summary['applications_sent']} applications | "
        f"{summary['recruiters_contacted']} recruiters contacted "
        f"({summary['recruiters_replied']} replied) | "
        f"{summary['active_usd_processes']} active processes | "
        f"{summary['follow_ups_due']} follow-ups due | "
        f"{summary['high_risk_opportunities']} high-risk"
    )

    return {
        "available":         True,
        "summary":           summary,
        "pipeline":          public_pipeline,
        "applications":      public_applications,
        "outreach_contacts": public_outreach,
        "outreach_summary":  outreach_summary,
        "follow_up_queue":   follow_up_queue,
        "risk_view":         risk_view,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    result = run_usd_contract_crm()
    if not result.get("available"):
        print("No USD CRM data yet. Create data/manual/usd_pipeline.csv, "
              "job_applications.csv, and recruiter_outreach_log.csv to start tracking applications.")
    else:
        print(result["summary"])
