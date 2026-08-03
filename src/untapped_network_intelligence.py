# -*- coding: utf-8 -*-
"""
untapped_network_intelligence.py
==================================
CLAUDE_UNTAPPED_NETWORK_V9.md — a SEPARATE intelligence layer from Lead
Reactivation. Lead Reactivation answers "who have I talked to before that
I should reconnect with." This answers a different question: "which
existing 1st-degree connections have NO conversation history at all, and
which of those should be activated first."

Inputs:
  - the already-classified/enriched connections dataframe (persona,
    opportunity_bucket, company_clean, seniority, priority_score, url,
    connected_on_clean already computed upstream — this module does not
    re-classify anything, it only adds untapped-specific fields)
  - outputs/message_threads_summary.csv (one row per historical
    conversation — built by lead_reactivation_engine.py earlier in the
    same pipeline run) for identity matching

Matching is conservative and hierarchical (Part 2): exact profile URL >
normalized URL > exact name+company > exact-unique name > name+role >
conservative fuzzy > no match. Ambiguous cases are never forced into a
confident bucket.

Outputs are PRIVATE by default (outputs/private/, gitignored) — this is a
person-level backlog of up to ~10k contacts, not something to publish
wholesale. Only a small, explicitly sanitized set of top-ranked records
goes into the public dashboard JSON (see export_public_dashboard_data.py).
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.company_normalizer import normalize as normalize_company, canonical_display
from src.untapped_outreach_score import (
    score_untapped_contact, load_manual_enrichment, match_manual_enrichment,
    score_activation_potential, compute_untapped_execution_score, activation_age_bucket_label,
    ACTIVATION_CATEGORY_QUEUE_PRIORITY,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
PRIVATE_DIR = OUTPUTS_DIR / "private"
THREADS_CSV = OUTPUTS_DIR / "message_threads_summary.csv"

RECRUITING_PERSONAS  = {"Recruiter", "Talent Acquisition", "Sourcer"}
HIRING_PERSONAS       = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS  = {"Data Engineering Manager", "Head of Data", "Director"}
HIGH_VALUE_PERSONAS   = RECRUITING_PERSONAS | HIRING_PERSONAS | DATA_LEADER_PERSONAS | {"Executive"}
SENIOR_TITLES         = {"Lead", "Manager", "Director", "Executive", "Founder"}

PRIMARY_BUCKETS = {
    "LATAM_USD_CONFIRMED", "LATAM_USD_LIKELY",
    "US_CANADA_CONFIRMED", "US_CANADA_LIKELY",
    "BRAZIL_CONFIRMED", "BRAZIL_LIKELY",
    "GLOBAL_STAFFING",
}
EXPLORATORY_BUCKETS = {
    "SPAIN_EU_CONFIRMED", "SPAIN_EU_LIKELY",
    "EUROPE_CONFIRMED", "EUROPE_LIKELY",
}
COMPANY_VALUE_BUCKETS = {
    "GLOBAL_STAFFING": 15, "GLOBAL_CONSULTING": 12, "GLOBAL_TECH": 12,
    "GLOBAL_OPPORTUNITY": 8,
}

ROLE_RELEVANCE_KW = [
    "data engineer", "data engineering", "cloud data", "analytics engineer",
    "azure", "aws", "databricks", "etl", "elt", "data platform",
    "data infrastructure", "ai engineer", "machine learning", "big data",
    "spark", "airflow", "snowflake", "dbt", "sql",
]

# ── Part 25 fixture-facing normalization helpers ──────────────────────────

def normalize_url(url: str) -> str:
    if not url:
        return ""
    u = str(url).strip().lower()
    u = re.split(r"[?#]", u)[0]          # drop query/fragment
    u = re.sub(r"^https?://", "", u)      # scheme-agnostic
    u = re.sub(r"^www\.", "", u)          # www-agnostic
    u = u.rstrip("/")
    return u


def normalize_name(name: str) -> str:
    if not name:
        return ""
    n = unicodedata.normalize("NFD", str(name))
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")  # strip accents
    n = n.lower().strip()
    n = re.sub(r"[^\w\s\-]", "", n, flags=re.UNICODE)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _connection_age_bucket(days) -> str:
    if days is None:
        return "UNKNOWN_CONNECTION_AGE"
    try:
        d = int(days)
    except (TypeError, ValueError):
        return "UNKNOWN_CONNECTION_AGE"
    if d < 0:
        return "UNKNOWN_CONNECTION_AGE"
    if d < 30:
        return "CONNECTED_LT_30D"
    if d < 90:
        return "CONNECTED_30_89D"
    if d < 180:
        return "CONNECTED_90_179D"
    if d < 365:
        return "CONNECTED_180_364D"
    return "CONNECTED_365D_PLUS"


def _parse_connected_on(s: str):
    if not s or pd.isna(s):
        return None
    s = str(s).strip()
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ── Message-side identity index (Part 2) ──────────────────────────────────

def _build_message_identity_index(msg_df: pd.DataFrame) -> dict:
    raw_urls: set[str] = set()
    norm_urls: set[str] = set()
    name_company: set[tuple[str, str]] = set()
    by_name: dict[str, list[dict]] = {}
    by_bucket: dict[str, list[str]] = {}  # first-3-char bucket -> [normalized names] for fuzzy

    if msg_df is None or msg_df.empty:
        return {
            "raw_urls": raw_urls, "norm_urls": norm_urls, "name_company": name_company,
            "by_name": by_name, "by_bucket": by_bucket,
        }

    for _, row in msg_df.iterrows():
        url_raw = str(row.get("other_person_profile_url", "") or "").strip().lower().rstrip("/")
        name_norm = normalize_name(row.get("other_person_name", ""))
        company_norm = normalize_company(str(row.get("company_clean", "") or ""))

        if url_raw:
            raw_urls.add(url_raw)
            norm_urls.add(normalize_url(url_raw))
        if name_norm and company_norm:
            name_company.add((name_norm, company_norm))
        if name_norm:
            by_name.setdefault(name_norm, []).append({
                "company_norm": company_norm,
                "persona": str(row.get("persona", "") or ""),
            })
            bucket = name_norm[:3]
            by_bucket.setdefault(bucket, []).append(name_norm)

    return {
        "raw_urls": raw_urls, "norm_urls": norm_urls, "name_company": name_company,
        "by_name": by_name, "by_bucket": by_bucket,
    }


def _conservative_fuzzy_match(name_norm: str, company_norm: str, idx: dict) -> bool:
    """Only used when NOTHING else matched. Requires a high name similarity
    AND an exact company match to stay genuinely conservative (Part 2)."""
    if not name_norm or not company_norm:
        return False
    bucket = idx["by_bucket"].get(name_norm[:3], [])
    for candidate_name in set(bucket):
        if candidate_name == name_norm:
            continue
        ratio = difflib.SequenceMatcher(None, name_norm, candidate_name).ratio() * 100
        if ratio >= 92:
            for cand in idx["by_name"].get(candidate_name, []):
                if cand["company_norm"] == company_norm:
                    return True
    return False


def match_identity(full_name: str, company_clean: str, url: str,
                    idx: dict, connections_name_counts: dict) -> tuple[str, float, str]:
    """
    Returns (contact_history_status, conversation_match_confidence, conversation_match_method).
    Implements the Part 2 hierarchy conservatively.
    """
    url_raw = str(url or "").strip().lower().rstrip("/")
    url_norm = normalize_url(url)
    name_norm = normalize_name(full_name)
    company_norm = normalize_company(str(company_clean or ""))

    # 1. Exact profile URL
    if url_raw and url_raw in idx["raw_urls"]:
        return "HAS_CONVERSATION", 1.00, "EXACT_PROFILE_URL"

    # 2. Normalized profile URL (trailing slash / query / scheme / www variants)
    if url_norm and url_norm in idx["norm_urls"]:
        return "HAS_CONVERSATION", 0.95, "NORMALIZED_PROFILE_URL"

    # 3. Exact normalized name + exact normalized company
    if name_norm and company_norm and (name_norm, company_norm) in idx["name_company"]:
        return "HAS_CONVERSATION", 0.90, "EXACT_NAME_COMPANY"

    candidates = idx["by_name"].get(name_norm, []) if name_norm else []

    if candidates:
        distinct_companies = {c["company_norm"] for c in candidates if c["company_norm"]}
        # Part 25 test 8 — a name that is ALSO common among the connections
        # population must never be force-matched, even if the message side
        # only has one such name.
        is_common_on_connections_side = connections_name_counts.get(name_norm, 0) > 1

        if len(distinct_companies) >= 2:
            return "AMBIGUOUS_REVIEW", 0.50, "AMBIGUOUS"

        # 4. Name + compatible role (persona hint present on both sides,
        # company on message side present but different/blank)
        if company_norm and distinct_companies and company_norm not in distinct_companies:
            # Company mismatch but same name — could be a job change or a
            # different person entirely. Only accept if the connections side
            # name is unique (otherwise ambiguous).
            if not is_common_on_connections_side:
                return "HAS_CONVERSATION", 0.75, "NAME_ROLE_MATCH"
            return "AMBIGUOUS_REVIEW", 0.50, "AMBIGUOUS"

        # 5. Exact unique name (unique on both the message side and the
        # connections side — no company signal to disambiguate, but no
        # conflict either)
        if not is_common_on_connections_side:
            return "HAS_CONVERSATION", 0.85, "EXACT_UNIQUE_NAME"

        return "AMBIGUOUS_REVIEW", 0.50, "AMBIGUOUS"

    # 6. Conservative fuzzy name + company (only when there was truly no
    # exact name hit at all)
    if _conservative_fuzzy_match(name_norm, company_norm, idx):
        return "HAS_CONVERSATION", 0.65, "CONSERVATIVE_FUZZY"

    # 7. No match — confirmed vs likely depends on identity completeness
    identity_complete = bool(url_raw) or bool(name_norm and company_norm)
    if identity_complete:
        return "NEVER_CONTACTED_CONFIRMED", 0.0, "NO_MATCH"
    return "LIKELY_NEVER_CONTACTED", 0.0, "NO_MATCH"


# ── Untapped categorization + scoring (Parts 6-7) ─────────────────────────

def _persona_flags(persona: str) -> dict:
    """Mutually exclusive by construction — each persona maps to exactly one
    of these flags (or none), matching the distinct KPI cards in Part 14."""
    return {
        "is_recruiter_untapped":      persona == "Recruiter",
        "is_ta_untapped":             persona == "Talent Acquisition",
        "is_hiring_manager_untapped": persona in HIRING_PERSONAS,
        "is_data_leader_untapped":    persona in DATA_LEADER_PERSONAS,
    }


def _strategic_focus(opportunity_bucket: str) -> str:
    if opportunity_bucket in PRIMARY_BUCKETS:
        return "PRIMARY_LATAM_USD"
    if opportunity_bucket in EXPLORATORY_BUCKETS:
        return "SPAIN_EU_EXPLORATORY"
    if opportunity_bucket in ("GLOBAL_CONSULTING", "GLOBAL_TECH"):
        return "GLOBAL_OPPORTUNITY"
    return "UNCLASSIFIED"


def _untapped_category(persona: str, opportunity_bucket: str, age_bucket: str,
                        untapped_score: int, status: str) -> str:
    if status == "AMBIGUOUS_REVIEW":
        return "AMBIGUOUS_REVIEW"
    if untapped_score >= 70:
        return "HIGH_VALUE_UNTAPPED"
    if persona == "Recruiter":
        return "RECRUITER_UNTAPPED"
    if persona == "Talent Acquisition":
        return "TALENT_ACQUISITION_UNTAPPED"
    if persona in HIRING_PERSONAS:
        return "HIRING_MANAGER_UNTAPPED"
    if persona in DATA_LEADER_PERSONAS:
        return "DATA_LEADER_UNTAPPED"
    if opportunity_bucket in PRIMARY_BUCKETS:
        return "LATAM_USD_UNTAPPED" if "US_CANADA" not in opportunity_bucket else "US_NEARSHORE_UNTAPPED"
    if opportunity_bucket in EXPLORATORY_BUCKETS:
        return "SPAIN_EU_EXPLORATORY_UNTAPPED"
    if opportunity_bucket == "GLOBAL_STAFFING":
        return "GLOBAL_STAFFING_UNTAPPED"
    if opportunity_bucket == "GLOBAL_TECH":
        return "GLOBAL_TECH_UNTAPPED"
    if opportunity_bucket == "GLOBAL_CONSULTING":
        return "GLOBAL_CONSULTING_UNTAPPED"
    if age_bucket == "CONNECTED_LT_30D":
        return "NEW_CONNECTION_NO_MESSAGE"
    if age_bucket == "CONNECTED_30_89D":
        return "CONNECTED_30D_NO_MESSAGE"
    if age_bucket == "CONNECTED_90_179D":
        return "CONNECTED_90D_NO_MESSAGE"
    if age_bucket == "CONNECTED_180_364D":
        return "CONNECTED_180D_NO_MESSAGE"
    if age_bucket == "CONNECTED_365D_PLUS":
        return "LONG_TERM_UNTAPPED"
    return "LOW_VALUE_UNTAPPED"


def _connection_age_score(days) -> int:
    """Part 7 — practical curve, does not simply reward oldest contacts."""
    if days is None:
        return 3
    try:
        d = int(days)
    except (TypeError, ValueError):
        return 3
    if d < 15:
        return 7   # new — activate soon, but not maximum yet
    if d <= 90:
        return 10  # sweet spot
    if d <= 365:
        return 8   # latent opportunity
    return 5        # long dormant — still worth something, capped


def compute_untapped_outreach_score(
    persona: str, opportunity_bucket: str, opportunity_confidence: float,
    seniority: str, position_clean: str, days_connected, match_confidence: float,
) -> int:
    """Part 7 — independent 0-100 score. NOT priority_score/relationship_value_score/
    immediate_action_score/outreach_adjusted_score."""
    score = 0.0

    # Persona relevance 0-25
    if persona in RECRUITING_PERSONAS or persona in HIRING_PERSONAS or persona in DATA_LEADER_PERSONAS:
        score += 25
    elif persona == "Executive":
        score += 15
    elif persona in ("Consultant", "Project / Program Manager"):
        score += 8

    # Strategic opportunity bucket 0-20
    if opportunity_bucket in ("LATAM_USD_CONFIRMED", "US_CANADA_CONFIRMED", "GLOBAL_STAFFING"):
        score += 20
    elif opportunity_bucket in ("LATAM_USD_LIKELY", "US_CANADA_LIKELY", "BRAZIL_CONFIRMED"):
        score += 16
    elif opportunity_bucket == "GLOBAL_OPPORTUNITY" and persona in HIGH_VALUE_PERSONAS:
        score += 14
    elif opportunity_bucket in EXPLORATORY_BUCKETS:
        score += 10  # exploratory — real but lower short-term weight

    # Company opportunity value 0-15
    score += COMPANY_VALUE_BUCKETS.get(opportunity_bucket, 0) * (15 / 15)
    score = min(score, score)  # no-op guard, bucket dict already capped at 15

    # Seniority / decision power 0-15
    sen = str(seniority or "")
    if sen in ("Director", "Executive", "Founder"):
        score += 15
    elif sen in ("Manager", "Lead"):
        score += 10
    elif sen == "Senior":
        score += 5

    # Role relevance to Mauricio 0-10
    pos = str(position_clean or "").lower()
    if any(kw in pos for kw in ROLE_RELEVANCE_KW):
        score += 10
    elif persona in HIGH_VALUE_PERSONAS:
        score += 4  # recruiters/HMs are relevant by function even w/o keyword match

    # Connection age 0-10
    score += _connection_age_score(days_connected)

    # Data confidence 0-5 (penalize ambiguity / low mapping confidence)
    conf = float(opportunity_confidence or 0)
    data_conf = 5 * min(1.0, conf) * min(1.0, match_confidence if match_confidence else 1.0)
    score += data_conf

    return int(max(0, min(100, round(score))))


def _recommended_first_action(persona: str, strategic_focus: str, score: int) -> str:
    if persona == "Recruiter":
        return "FIRST_MESSAGE_RECRUITER"
    if persona == "Talent Acquisition":
        return "FIRST_MESSAGE_TALENT_ACQUISITION"
    if persona in HIRING_PERSONAS:
        return "FIRST_MESSAGE_HIRING_MANAGER"
    if persona in DATA_LEADER_PERSONAS:
        return "FIRST_MESSAGE_DATA_LEADER"
    if strategic_focus == "SPAIN_EU_EXPLORATORY":
        return "FIRST_MESSAGE_EU_EXPLORATORY"
    if score >= 50:
        return "VALUE_FIRST_ENGAGEMENT"
    if score >= 25:
        return "MONITOR_ONLY"
    return "MONITOR_ONLY"


# ── Part 10 — safe, templated first-message angles (never claims prior contact) ──
_ANGLE_LATAM_RECRUITER = (
    "Thanks for being part of my network. I realized we had not spoken yet. "
    "I'm a Data Engineer focused on Azure/AWS, Databricks, SQL, PySpark and "
    "ETL/ELT, currently open to remote LATAM/USD opportunities."
)
_ANGLE_US_NEARSHORE = (
    "I realized we have been connected for some time but had not spoken yet. "
    "I'm a Data Engineer based in Brazil with experience supporting international "
    "environments and US time zones, focused on Azure/AWS data platforms."
)
_ANGLE_TA = (
    "Thanks for being part of my network. I noticed your work in Talent Acquisition "
    "and wanted to introduce myself properly. I work with Data Engineering, cloud "
    "data platforms and ETL/ELT across Azure and AWS."
)
_ANGLE_HM = (
    "I realized we have been connected for some time but had not spoken yet. I work "
    "with scalable Data Engineering solutions across Azure/AWS, Databricks, SQL and "
    "ETL/ELT and follow teams building modern data platforms."
)
_ANGLE_EU = (
    "I realized we are already connected but had not spoken yet. I'm building my "
    "European network ahead of spending time in Spain and wanted to introduce myself. "
    "I'm a Data Engineer focused on Azure/AWS, Databricks and analytics engineering."
)
_ANGLE_GENERIC = (
    "Thanks for being part of my network. I realized we had not spoken yet — wanted "
    "to introduce myself. I'm a Data Engineer focused on Azure/AWS, Databricks and "
    "ETL/ELT data platforms."
)


def _first_message_angle(persona: str, strategic_focus: str) -> str:
    if strategic_focus == "SPAIN_EU_EXPLORATORY":
        return _ANGLE_EU
    if persona == "Talent Acquisition":
        return _ANGLE_TA
    if persona in HIRING_PERSONAS or persona in DATA_LEADER_PERSONAS:
        return _ANGLE_HM
    if persona == "Recruiter" and strategic_focus == "PRIMARY_LATAM_USD":
        return _ANGLE_LATAM_RECRUITER
    if persona == "Recruiter":
        return _ANGLE_US_NEARSHORE
    return _ANGLE_GENERIC


def _operational_category(status: str, score: int, strategic_focus: str, match_confidence: float) -> str:
    """Part 18."""
    if status == "AMBIGUOUS_REVIEW":
        return "REVIEW"
    if status != "NEVER_CONTACTED_CONFIRMED":
        return "MONITOR"
    if score >= 75 and strategic_focus in ("PRIMARY_LATAM_USD",) and match_confidence >= 0.0:
        return "ACT_NOW"
    if score >= 55 and strategic_focus in ("PRIMARY_LATAM_USD", "SPAIN_EU_EXPLORATORY"):
        return "THIS_WEEK"
    if score >= 30:
        return "THIS_MONTH"
    return "MONITOR"


# ── Part 17 — weekly queue with 90/10-ish integer-safe allocation ─────────

WEEKLY_QUEUE_MAX          = 20
WEEKLY_QUEUE_PRIMARY_MIN  = 14
WEEKLY_QUEUE_PRIMARY_MAX  = 16
WEEKLY_QUEUE_STAFFING_MAX = 4
WEEKLY_QUEUE_EU_MIN       = 2
WEEKLY_QUEUE_EU_MAX       = 3

# Untapped Activation Potential Scoring (V10) — priority rank used to order
# WITHIN each strategic-focus allocation bucket below (does not change the
# ~90/10 LATAM/EU allocation itself, only who goes first inside it).
_ACTIVATION_CATEGORY_RANK = {cat: i for i, cat in enumerate(ACTIVATION_CATEGORY_QUEUE_PRIORITY)}


def _queue_priority_rank(row) -> int:
    cat = row.get("activation_category", "")
    if cat in _ACTIVATION_CATEGORY_RANK:
        return _ACTIVATION_CATEGORY_RANK[cat]
    if row.get("untapped_category", "") == "HIGH_VALUE_UNTAPPED":
        return len(_ACTIVATION_CATEGORY_RANK)
    return len(_ACTIVATION_CATEGORY_RANK) + 1


def _effective_queue_score(df: pd.DataFrame) -> pd.Series:
    base = pd.to_numeric(df["untapped_outreach_score"], errors="coerce").fillna(0)
    if "untapped_execution_score" in df.columns:
        exec_s = pd.to_numeric(df["untapped_execution_score"], errors="coerce")
        return exec_s.fillna(base)
    return base


def build_weekly_untapped_queue(df: pd.DataFrame) -> pd.DataFrame:
    """Part 17 — ~85-90% LATAM/USD/nearshore, ~10-15% Spain/EU, capped at 20,
    prioritizing high score + strategic persona + confirmed-never-contacted +
    good match confidence. Excludes ambiguous identity and low-value personas.

    V10: within each strategic-focus bucket, contacts are additionally
    ordered by activation_category priority (HOT_UNTAPPED_RECRUITER >
    LATAM_INTERNATIONAL_RECRUITER > HIGH_POTENTIAL_LONG_CONNECTED >
    GLOBAL_RECRUITER_UNTAPPED > HIGH_VALUE_UNTAPPED > everything else) so a
    long-connected, never-contacted recruiter/TA/talent-partner surfaces
    ahead of a merely-high-score contact with no such signal."""
    eligible = df[
        (df["contact_history_status"] == "NEVER_CONTACTED_CONFIRMED")
        & (df["untapped_outreach_score"] >= 25)
    ].copy()
    if eligible.empty:
        return eligible

    eligible["_queue_rank"]  = eligible.apply(_queue_priority_rank, axis=1)
    eligible["_queue_score"] = _effective_queue_score(eligible)

    def _sorted(subset: pd.DataFrame) -> pd.DataFrame:
        return subset.sort_values(["_queue_rank", "_queue_score"], ascending=[True, False])

    primary  = _sorted(eligible[eligible["strategic_focus"] == "PRIMARY_LATAM_USD"])
    staffing = _sorted(eligible[eligible["strategic_focus"] == "GLOBAL_OPPORTUNITY"])
    eu       = _sorted(eligible[eligible["strategic_focus"] == "SPAIN_EU_EXPLORATORY"])

    n_primary  = min(len(primary),  WEEKLY_QUEUE_PRIMARY_MAX)
    n_eu       = min(len(eu),       WEEKLY_QUEUE_EU_MAX)
    n_staffing = min(len(staffing), WEEKLY_QUEUE_STAFFING_MAX, WEEKLY_QUEUE_MAX - n_primary - n_eu)
    # Ensure primary floor is respected if enough candidates exist
    if n_primary < WEEKLY_QUEUE_PRIMARY_MIN and len(primary) >= WEEKLY_QUEUE_PRIMARY_MIN:
        n_primary = WEEKLY_QUEUE_PRIMARY_MIN
        n_staffing = min(n_staffing, WEEKLY_QUEUE_MAX - n_primary - n_eu)

    parts = [primary.head(n_primary), staffing.head(n_staffing), eu.head(n_eu)]
    queue = pd.concat([p for p in parts if len(p) > 0], ignore_index=True)

    # Top up from remaining primary candidates if under the max and slots remain
    remaining = WEEKLY_QUEUE_MAX - len(queue)
    if remaining > 0:
        used_urls = set(queue["url"]) if "url" in queue.columns else set()
        topup = primary[~primary["url"].isin(used_urls)].head(remaining)
        queue = pd.concat([queue, topup], ignore_index=True)

    queue = queue.sort_values(["_queue_rank", "_queue_score"], ascending=[True, False]) \
        .head(WEEKLY_QUEUE_MAX).reset_index(drop=True)
    return queue.drop(columns=["_queue_rank", "_queue_score"], errors="ignore")


# ── Activation Pattern Learning (V10) — sanitized, AGGREGATE-ONLY evidence ──
# Shows that long-connected, previously-never-contacted 1st-degree
# connections (mostly recruiters/TA/talent partners) convert into useful
# conversations once first-messaged. Built entirely from already-classified/
# sanitized fields (persona, connection date, opportunity bucket) plus the
# existing message_threads_summary.csv aggregate signals (lead_category,
# has_cv_signal, has_interview_signal, messages_from_other_person) — never
# raw message content. "Long-connected before first contact" = the
# conversation's first message happened more than 90 days after the
# connection was made — i.e. the same pattern observed manually with
# long-connected recruiters who converted immediately once messaged.

WARM_LEAD_CATEGORIES = {
    "Active Interview Pipeline", "Warm reactivation",
    "Needs my response — Confirmed", "Needs my response — Likely",
}
LONG_DORMANT_BEFORE_CONTACT_DAYS = 90


def _truthy(v) -> bool:
    s = str(v).strip().lower()
    return s in ("true", "1", "yes")


def _build_connections_lookup(df: pd.DataFrame) -> dict:
    """(name_norm, company_norm) -> {persona, opportunity_bucket, connected_on (date|None)}."""
    lookup: dict[tuple[str, str], dict] = {}
    for _, row in df.iterrows():
        name_norm = normalize_name(row.get("full_name", ""))
        if not name_norm:
            continue
        company_norm = normalize_company(str(row.get("company_clean", "") or ""))
        conn_date = _parse_connected_on(row.get("connected_on", ""))
        lookup[(name_norm, company_norm)] = {
            "persona": str(row.get("persona", "") or ""),
            "opportunity_bucket": str(row.get("opportunity_bucket", "") or ""),
            "connected_on": conn_date,
        }
    return lookup


def build_activation_pattern_learning(df: pd.DataFrame, msg_df: pd.DataFrame | None) -> dict:
    """
    Returns a sanitized, aggregate-only dict (no names, no raw messages):
      {
        "available": bool,
        "long_connected_contacted_all_time": int,
        "long_connected_contacted_this_week": int,
        "long_connected_replied": int,
        "long_connected_became_warm": int,
        "long_connected_cv_or_interview_requested": int,
        "conversion_rate_overall_pct": float,
        "by_persona": [{"persona":..., "contacted":..., "replied":..., "became_warm":..., "conversion_rate_pct":...}, ...],
        "by_connected_age_bucket": [...],
        "by_opportunity_bucket": [...],
      }
    """
    if msg_df is None or msg_df.empty or df is None or df.empty:
        return {"available": False, "reason": "no message history this run"}

    lookup = _build_connections_lookup(df)
    today = date.today()

    matched = 0
    long_connected_all_time = 0
    long_connected_this_week = 0
    replied = 0
    became_warm = 0
    cv_or_interview_requested = 0

    by_persona: dict[str, dict] = {}
    by_age_bucket: dict[str, dict] = {}
    by_bucket: dict[str, dict] = {}

    def _bump(store: dict, key: str, replied_flag: bool, warm_flag: bool) -> None:
        if not key:
            return
        rec = store.setdefault(key, {"contacted": 0, "replied": 0, "warm": 0})
        rec["contacted"] += 1
        if replied_flag:
            rec["replied"] += 1
        if warm_flag:
            rec["warm"] += 1

    for _, row in msg_df.iterrows():
        name_norm = normalize_name(row.get("other_person_name", ""))
        company_norm = normalize_company(str(row.get("company_clean", "") or ""))
        rec = lookup.get((name_norm, company_norm))
        if not rec or not rec.get("connected_on"):
            continue
        first_msg = _parse_connected_on(row.get("first_message_date", ""))
        if not first_msg:
            continue
        matched += 1
        days_to_first_contact = (first_msg - rec["connected_on"]).days
        if days_to_first_contact <= LONG_DORMANT_BEFORE_CONTACT_DAYS:
            continue  # contacted reasonably promptly — not the "untapped activation" pattern

        long_connected_all_time += 1
        if (today - first_msg).days <= 7:
            long_connected_this_week += 1

        replied_flag = False
        try:
            replied_flag = int(float(row.get("messages_from_other_person", 0) or 0)) > 0
        except (TypeError, ValueError):
            pass
        warm_flag = str(row.get("lead_category", "")) in WARM_LEAD_CATEGORIES
        cv_interview_flag = _truthy(row.get("has_cv_signal")) or _truthy(row.get("has_interview_signal"))

        if replied_flag:
            replied += 1
        if warm_flag:
            became_warm += 1
        if cv_interview_flag:
            cv_or_interview_requested += 1

        persona = rec.get("persona") or "Unknown"
        bucket = rec.get("opportunity_bucket") or "UNKNOWN"
        age_label = activation_age_bucket_label(days_to_first_contact)

        _bump(by_persona, persona, replied_flag, warm_flag)
        _bump(by_age_bucket, age_label, replied_flag, warm_flag)
        _bump(by_bucket, bucket, replied_flag, warm_flag)

    def _rate(rec: dict) -> float:
        return round(100.0 * rec["replied"] / rec["contacted"], 1) if rec["contacted"] else 0.0

    def _to_rows(store: dict, key_label: str) -> list:
        return [
            {key_label: k, "contacted": v["contacted"], "replied": v["replied"],
             "became_warm": v["warm"], "conversion_rate_pct": _rate(v)}
            for k, v in sorted(store.items(), key=lambda kv: kv[1]["contacted"], reverse=True)
        ]

    overall_rate = round(100.0 * replied / long_connected_all_time, 1) if long_connected_all_time else 0.0

    result = {
        "available": True,
        "long_connected_contacted_all_time": long_connected_all_time,
        "long_connected_contacted_this_week": long_connected_this_week,
        "long_connected_replied": replied,
        "long_connected_became_warm": became_warm,
        "long_connected_cv_or_interview_requested": cv_or_interview_requested,
        "conversion_rate_overall_pct": overall_rate,
        "by_persona": _to_rows(by_persona, "persona"),
        "by_connected_age_bucket": _to_rows(by_age_bucket, "connected_age_bucket"),
        "by_opportunity_bucket": _to_rows(by_bucket, "opportunity_bucket"),
    }
    pd.DataFrame([{
        "long_connected_contacted_all_time": long_connected_all_time,
        "long_connected_contacted_this_week": long_connected_this_week,
        "long_connected_replied": replied,
        "long_connected_became_warm": became_warm,
        "long_connected_cv_or_interview_requested": cv_or_interview_requested,
        "conversion_rate_overall_pct": overall_rate,
    }]).to_csv(OUTPUTS_DIR / "untapped_activation_pattern_summary.csv", index=False, encoding="utf-8-sig")
    logger.info(
        f"  Activation Pattern Learning: long-connected-contacted={long_connected_all_time} "
        f"(this_week={long_connected_this_week}) replied={replied} became_warm={became_warm} "
        f"cv_or_interview_requested={cv_or_interview_requested} conversion_rate={overall_rate}%"
    )
    return result


PRIVATE_COLS = [
    "full_name", "company_clean", "position_clean", "persona", "seniority",
    "opportunity_bucket", "opportunity_confidence", "url",
    "connected_on", "days_connected", "connection_age_bucket",
    "contact_history_status", "conversation_match_confidence", "conversation_match_method",
    "untapped_category", "untapped_outreach_score", "untapped_reason",
    "untapped_opportunity_market", "untapped_market_confidence", "untapped_market_reason",
    "exact_location_available", "observed_location_manual", "is_manual_enriched",
    "is_high_value_untapped", "is_recruiter_untapped", "is_ta_untapped",
    "is_hiring_manager_untapped", "is_data_leader_untapped",
    "is_latam_primary", "is_europe_exploratory", "strategic_focus",
    "recommended_first_action", "first_message_angle", "operational_category",
    "priority_score",
    # Untapped Activation Potential Scoring (V10)
    "untapped_activation_potential_score", "untapped_execution_score",
    "connected_age_bucket", "activation_category", "activation_reason",
    "first_message_priority",
]

REVIEW_QUEUE_COLS = [
    "full_name", "company_clean", "position_clean",
    "contact_history_status", "possible_match_count",
    "conversation_match_confidence", "review_reason",
]


def _save_private(df: pd.DataFrame, name: str) -> Path:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    path = PRIVATE_DIR / name
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"  Saved (private): {path.relative_to(ROOT)} ({len(df)} rows)")
    return path


def run_untapped_network_intelligence(
    classified_df: pd.DataFrame,
    messages_csv_available: bool,
    company_signal_map: dict | None = None,
) -> dict:
    """
    Main entry point. Returns a summary dict for the pipeline log + public
    JSON export. All full per-contact CSVs are written to outputs/private/
    (gitignored) — only a small sanitized top-N slice is safe for the public
    dashboard (handled separately in export_public_dashboard_data.py).

    company_signal_map (Untapped Outreach Scoring V9): optional per-company
    aggregate from lead_reactivation_engine.py — {normalized_company: {
    "has_warm_signal": bool, "has_rejection_signal": bool}} — used as a
    cross-signal so a never-contacted recruiter at a company that already
    produced a warm lead/interview scores higher.
    """
    if classified_df is None or classified_df.empty:
        return {"available": False, "reason": "no classified connections dataframe"}

    df = classified_df.copy()
    text_cols = ["full_name", "company_clean", "position_clean", "url", "persona",
                 "seniority", "opportunity_bucket", "connected_on_clean"]
    numeric_cols = ["opportunity_confidence", "priority_score"]
    for col in text_cols:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")  # per-cell NaN, not just missing columns —
        # bare NaN survives json.dump (Python allows it) but is NOT valid JSON
        # and breaks JSON.parse() in every real browser.
    for col in numeric_cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0.0)

    msg_df = None
    if THREADS_CSV.exists():
        try:
            msg_df = pd.read_csv(THREADS_CSV, dtype=str, low_memory=False, encoding="utf-8-sig")
        except Exception as exc:
            logger.warning(f"  Untapped Network: failed to read {THREADS_CSV.name}: {exc}")

    idx = _build_message_identity_index(msg_df)

    # Connections-side name collision map (Part 25 test 8 — common duplicate names)
    name_series = df["full_name"].fillna("").apply(normalize_name)
    connections_name_counts = name_series.value_counts().to_dict()

    # Untapped Outreach Scoring V9 — optional private manual enrichment file
    # and the per-company warm/rejection cross-signal from Lead Reactivation.
    manual_index = load_manual_enrichment()
    company_signal_map = company_signal_map or {}

    today = date.today()

    statuses, confidences, methods = [], [], []
    connected_on_list, days_connected_list, age_bucket_list = [], [], []
    connected_age_bucket_human_list = []
    untapped_categories, scores = [], []
    flags_recruiter, flags_ta, flags_hm, flags_dl, flags_hv = [], [], [], [], []
    flags_latam, flags_eu, foci = [], [], []
    actions, angles, op_cats = [], [], []
    reasons, opp_buckets_out, opp_markets = [], [], []
    market_confs, market_reasons = [], []
    exact_locs, observed_locs, manual_enriched = [], [], []
    # Untapped Activation Potential Scoring (V10)
    activation_scores, activation_categories = [], []
    activation_reasons, first_msg_priorities, execution_scores = [], [], []

    for _, row in df.iterrows():
        status, conf, method = match_identity(
            row.get("full_name", ""), row.get("company_clean", ""), row.get("url", ""),
            idx, connections_name_counts,
        )
        statuses.append(status); confidences.append(conf); methods.append(method)

        conn_date = _parse_connected_on(row.get("connected_on_clean", ""))
        days = (today - conn_date).days if conn_date else None
        age_bucket = _connection_age_bucket(days)
        connected_on_list.append(str(conn_date) if conn_date else "")
        days_connected_list.append(days if days is not None else "")
        age_bucket_list.append(age_bucket)
        connected_age_bucket_human_list.append(activation_age_bucket_label(days))

        persona = str(row.get("persona", "") or "")
        opp_bucket = str(row.get("opportunity_bucket", "") or "")
        priority_score_val = row.get("priority_score", 0)

        if status in ("NEVER_CONTACTED_CONFIRMED", "LIKELY_NEVER_CONTACTED"):
            company_norm = normalize_company(str(row.get("company_clean", "") or ""))
            manual_rec = match_manual_enrichment(
                row.get("full_name", ""), row.get("company_clean", ""), row.get("url", ""), manual_index,
            )
            company_sig = company_signal_map.get(company_norm, {})
            v9 = score_untapped_contact(
                full_name=row.get("full_name", ""),
                company_clean=row.get("company_clean", ""),
                position_clean=row.get("position_clean", ""),
                persona=persona,
                opportunity_bucket=opp_bucket,
                opportunity_confidence=row.get("opportunity_confidence", 0),
                seniority=row.get("seniority", ""),
                days_connected=days,
                manual_record=manual_rec,
                company_has_warm_signal=bool(company_sig.get("has_warm_signal")),
                company_has_rejection_signal=bool(company_sig.get("has_rejection_signal")),
            )
            score = v9["untapped_outreach_score"]
            reason = v9["untapped_reason"]
            out_bucket = v9["opportunity_bucket"]
            opp_market = v9["opportunity_market"]
            market_conf = v9["market_confidence"]
            market_reason = v9["market_reason"]
            exact_loc = v9["exact_location_available"]
            observed_loc = v9["observed_location_manual"]
            angle_v9 = v9["first_message_angle"]
            is_enriched = bool(manual_rec)

            v10 = score_activation_potential(
                full_name=row.get("full_name", ""),
                company_clean=row.get("company_clean", ""),
                position_clean=row.get("position_clean", ""),
                persona=persona,
                opportunity_bucket=out_bucket,
                history_status=status,
                days_connected=days,
                company_has_warm_signal=bool(company_sig.get("has_warm_signal")),
            )
            activation_score = v10["untapped_activation_potential_score"]
            activation_cat = v10["activation_category"]
            activation_reason = v10["activation_reason"]
            first_msg_priority = v10["first_message_priority"]
            angle_v10 = v10["first_message_angle"]
            exec_score = compute_untapped_execution_score(
                score, activation_score, priority_score_val, persona, status,
            )
        else:
            score = 0
            reason = ""
            out_bucket = opp_bucket
            opp_market = ""
            market_conf = 0.0
            market_reason = ""
            exact_loc = False
            observed_loc = ""
            angle_v9 = None
            is_enriched = False
            activation_score = 0
            activation_cat = ""
            activation_reason = ""
            first_msg_priority = ""
            angle_v10 = None
            exec_score = 0

        focus = _strategic_focus(out_bucket)
        foci.append(focus)
        scores.append(score)
        reasons.append(reason)
        opp_buckets_out.append(out_bucket)
        opp_markets.append(opp_market)
        market_confs.append(market_conf)
        market_reasons.append(market_reason)
        exact_locs.append(exact_loc)
        observed_locs.append(observed_loc)
        manual_enriched.append(is_enriched)
        activation_scores.append(activation_score)
        activation_categories.append(activation_cat)
        activation_reasons.append(activation_reason)
        first_msg_priorities.append(first_msg_priority)
        execution_scores.append(exec_score)

        cat = _untapped_category(persona, out_bucket, age_bucket, score, status) if status != "HAS_CONVERSATION" else ""
        untapped_categories.append(cat)

        pf = _persona_flags(persona)
        flags_recruiter.append(pf["is_recruiter_untapped"] and status == "NEVER_CONTACTED_CONFIRMED")
        flags_ta.append(pf["is_ta_untapped"] and status == "NEVER_CONTACTED_CONFIRMED")
        flags_hm.append(pf["is_hiring_manager_untapped"] and status == "NEVER_CONTACTED_CONFIRMED")
        flags_dl.append(pf["is_data_leader_untapped"] and status == "NEVER_CONTACTED_CONFIRMED")
        flags_hv.append(cat == "HIGH_VALUE_UNTAPPED")
        flags_latam.append(focus == "PRIMARY_LATAM_USD" and status == "NEVER_CONTACTED_CONFIRMED")
        flags_eu.append(focus == "SPAIN_EU_EXPLORATORY" and status == "NEVER_CONTACTED_CONFIRMED")

        actions.append(_recommended_first_action(persona, focus, score) if status == "NEVER_CONTACTED_CONFIRMED" else "REVIEW_IDENTITY" if status == "AMBIGUOUS_REVIEW" else "")
        angles.append((angle_v10 or angle_v9 or _first_message_angle(persona, focus)) if status == "NEVER_CONTACTED_CONFIRMED" else "")
        op_cats.append(_operational_category(status, score, focus, conf))

    df["contact_history_status"]          = statuses
    df["conversation_match_confidence"]   = confidences
    df["conversation_match_method"]       = methods
    df["connected_on"]                    = connected_on_list
    df["days_connected"]                  = days_connected_list
    df["connection_age_bucket"]           = age_bucket_list
    df["connected_age_bucket"]            = connected_age_bucket_human_list
    df["strategic_focus"]                 = foci
    df["untapped_outreach_score"]         = scores
    df["untapped_reason"]                 = reasons
    df["opportunity_bucket"]              = opp_buckets_out
    df["untapped_opportunity_market"]     = opp_markets
    df["untapped_market_confidence"]      = market_confs
    df["untapped_market_reason"]          = market_reasons
    df["exact_location_available"]        = exact_locs
    df["observed_location_manual"]        = observed_locs
    df["is_manual_enriched"]              = manual_enriched
    df["untapped_category"]               = untapped_categories
    df["is_recruiter_untapped"]           = flags_recruiter
    df["is_ta_untapped"]                  = flags_ta
    df["is_hiring_manager_untapped"]      = flags_hm
    df["is_data_leader_untapped"]         = flags_dl
    df["is_high_value_untapped"]          = flags_hv
    df["is_latam_primary"]                = flags_latam
    df["is_europe_exploratory"]           = flags_eu
    df["recommended_first_action"]        = actions
    df["first_message_angle"]             = angles
    df["operational_category"]            = op_cats
    # Untapped Activation Potential Scoring (V10)
    df["untapped_activation_potential_score"] = activation_scores
    df["activation_category"]             = activation_categories
    df["activation_reason"]               = activation_reasons
    df["first_message_priority"]          = first_msg_priorities
    df["untapped_execution_score"]        = execution_scores

    total = len(df)
    has_conv   = int((df["contact_history_status"] == "HAS_CONVERSATION").sum())
    confirmed  = int((df["contact_history_status"] == "NEVER_CONTACTED_CONFIRMED").sum())
    likely     = int((df["contact_history_status"] == "LIKELY_NEVER_CONTACTED").sum())
    ambiguous  = int((df["contact_history_status"] == "AMBIGUOUS_REVIEW").sum())
    reconciled = has_conv + confirmed + likely + ambiguous

    untapped_mask = df["contact_history_status"].isin(["NEVER_CONTACTED_CONFIRMED", "LIKELY_NEVER_CONTACTED"])
    # Default ranking (V10): untapped_execution_score first — the blended
    # max() of untapped_outreach_score, untapped_activation_potential_score,
    # and never-contacted-recruiter-adjusted priority_score — so a
    # long-connected, never-contacted recruiter/TA/talent-partner with a high
    # activation score ranks correctly even when untapped_outreach_score
    # alone (company/bucket-driven) hasn't caught up yet.
    untapped_df = df[untapped_mask].sort_values(
        ["untapped_execution_score", "untapped_outreach_score", "priority_score"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    # ── Private outputs (Part 11, 24) — gitignored, never published wholesale ──
    _save_private(untapped_df[[c for c in PRIVATE_COLS if c in untapped_df.columns]],
                  "untapped_outreach_backlog.csv")

    high_value_df = untapped_df[untapped_df["is_high_value_untapped"]]
    _save_private(high_value_df[[c for c in PRIVATE_COLS if c in high_value_df.columns]],
                  "untapped_high_value.csv")

    weekly_queue = build_weekly_untapped_queue(untapped_df)
    _save_private(weekly_queue[[c for c in PRIVATE_COLS if c in weekly_queue.columns]] if not weekly_queue.empty
                  else pd.DataFrame(columns=PRIVATE_COLS),
                  "untapped_weekly_queue.csv")

    ambiguous_df = df[df["contact_history_status"] == "AMBIGUOUS_REVIEW"].copy()
    if not ambiguous_df.empty:
        ambiguous_df["possible_match_count"] = ambiguous_df["full_name"].apply(
            lambda n: len(idx["by_name"].get(normalize_name(n), []))
        )
        ambiguous_df["review_reason"] = "multiple conflicting name/company candidates in message history"
    review_out = ambiguous_df[[c for c in REVIEW_QUEUE_COLS if c in ambiguous_df.columns]] if not ambiguous_df.empty \
        else pd.DataFrame(columns=REVIEW_QUEUE_COLS)
    _save_private(review_out, "untapped_ambiguous_review.csv")

    match_method_counts = df["conversation_match_method"].value_counts().to_dict()
    summary_row = {
        "total_connections": total, "has_conversation": has_conv,
        "never_contacted_confirmed": confirmed, "likely_never_contacted": likely,
        "ambiguous_review": ambiguous, "reconciled_total": reconciled,
        "high_value_untapped": int(untapped_df["is_high_value_untapped"].sum()),
        "recruiters_untapped": int(untapped_df["is_recruiter_untapped"].sum()),
        "ta_untapped": int(untapped_df["is_ta_untapped"].sum()),
        "hiring_managers_untapped": int(untapped_df["is_hiring_manager_untapped"].sum()),
        "data_leaders_untapped": int(untapped_df["is_data_leader_untapped"].sum()),
        # LATAM/USD vs US/Nearshore are reported as distinct, non-overlapping
        # KPI cards (Part 14) even though both roll into the same "primary
        # 90%" strategic_focus bucket for allocation purposes.
        "latam_usd_untapped": int((untapped_df["is_latam_primary"]
                                    & ~untapped_df["opportunity_bucket"].isin(["US_CANADA_CONFIRMED", "US_CANADA_LIKELY"])).sum()),
        "us_nearshore_untapped": int((untapped_df["is_latam_primary"]
                                       & untapped_df["opportunity_bucket"].isin(["US_CANADA_CONFIRMED", "US_CANADA_LIKELY"])).sum()),
        "spain_eu_untapped": int(untapped_df["is_europe_exploratory"].sum()),
        "connected_90d_plus_never_contacted": int(untapped_df["connection_age_bucket"].isin(
            ["CONNECTED_90_179D", "CONNECTED_180_364D", "CONNECTED_365D_PLUS"]).sum()),
        "connected_180d_plus_never_contacted": int(untapped_df["connection_age_bucket"].isin(
            ["CONNECTED_180_364D", "CONNECTED_365D_PLUS"]).sum()),
        "this_week_queue_count": len(weekly_queue),
        # Untapped Outreach Scoring V9 — contacts matched against the private,
        # optional data/manual/profile_enrichment.csv file (Part 6/7).
        "manual_enriched_contacts": int(untapped_df["is_manual_enriched"].sum()),
    }
    pd.DataFrame([summary_row]).to_csv(OUTPUTS_DIR / "untapped_network_summary.csv", index=False, encoding="utf-8-sig")
    match_audit_df = pd.DataFrame(sorted(match_method_counts.items()), columns=["match_method", "count"])
    match_audit_df.to_csv(OUTPUTS_DIR / "untapped_match_audit.csv", index=False, encoding="utf-8-sig")

    logger.info(
        f"  Untapped Network: total={total} has_conversation={has_conv} "
        f"confirmed_never={confirmed} likely_never={likely} ambiguous={ambiguous} "
        f"(reconciled={reconciled}/{total}) | high_value={summary_row['high_value_untapped']} "
        f"recruiters={summary_row['recruiters_untapped']} TA={summary_row['ta_untapped']} "
        f"HM={summary_row['hiring_managers_untapped']} DL={summary_row['data_leaders_untapped']} "
        f"LATAM/USD={summary_row['latam_usd_untapped']} SpainEU={summary_row['spain_eu_untapped']} "
        f"this_week_queue={len(weekly_queue)}"
    )

    # Sanitized FULL population for the public dashboard (see
    # export_public_dashboard_data.py). NOT capped — Part 14's acceptance
    # rule requires every KPI card's displayed count to equal what clicking
    # it actually retrieves, including LIKELY_NEVER_CONTACTED and
    # AMBIGUOUS_REVIEW (which untapped_df alone does not include).
    public_cols = [
        "full_name", "company_clean", "position_clean", "persona", "seniority",
        "opportunity_bucket", "connected_on", "days_connected", "connection_age_bucket",
        "contact_history_status", "untapped_category", "untapped_outreach_score",
        "untapped_reason", "untapped_opportunity_market", "untapped_market_confidence",
        "untapped_market_reason", "exact_location_available", "observed_location_manual",
        "is_manual_enriched",
        "recommended_first_action", "first_message_angle", "url",
        "conversation_match_confidence", "strategic_focus", "operational_category",
        # Untapped Activation Potential Scoring (V10)
        "untapped_activation_potential_score", "untapped_execution_score",
        "connected_age_bucket", "activation_category", "activation_reason",
        "first_message_priority", "priority_score",
    ]
    public_pool = pd.concat([untapped_df, ambiguous_df], ignore_index=True) if not ambiguous_df.empty else untapped_df
    top_public = public_pool[[c for c in public_cols if c in public_pool.columns]] \
        .rename(columns={"url": "profile_url"})

    weekly_queue_public = weekly_queue[[c for c in public_cols if c in weekly_queue.columns]].rename(
        columns={"url": "profile_url"}
    ) if not weekly_queue.empty else pd.DataFrame(columns=public_cols)

    activation_pattern_data = build_activation_pattern_learning(df, msg_df)

    return {
        "available": True,
        "summary": summary_row,
        "match_method_breakdown": {str(k): int(v) for k, v in match_method_counts.items()},
        "top_untapped_contacts": top_public.to_dict(orient="records"),
        "this_week_queue": weekly_queue_public.to_dict(orient="records"),
        "activation_pattern_learning": activation_pattern_data,
    }
