# -*- coding: utf-8 -*-
"""
company_follow_intelligence.py
================================
Company Follow Intelligence — uses "Company Follows.csv" (Organization,
Followed On) as an additional company-relevance / opportunity-market signal.

Rationale: the user typically follows a company's LinkedIn page after
connecting with or messaging someone there, so a followed company is a
genuine relevance signal — but LinkedIn's Company Follows export carries NO
location data. This module NEVER fabricates exact geography from a follow
alone; it only uses follows to improve company relevance / opportunity-bucket
inference, always alongside at least one other honest signal (persona,
keyword, existing company category, or message/opportunity history).

Two entry points, called at two different points in build_strategy_layer.py:

  1. apply_company_follow_resolution(df, follows_df) — STAGE A. Runs right
     after Company Resolution V7, while the frame still only carries
     persona / company_category / opportunity_bucket signals (no message or
     opportunity-history data exists yet this pass). Resolves a
     NEEDS_COMPANY_MAPPING contact only when their company matches a followed
     company AND at least one other honest signal is present. Mutates `df`
     and feeds the same Needs-Mapping backlog recompute / enriched CSV save
     every other resolution pass feeds.

  2. build_company_follow_intelligence(df, follows_df, matches_df, ...) —
     STAGE B. Runs near the end of the pipeline, after Lead Reactivation /
     Opportunity History / USD Contract CRM have all written their sanitized
     CSVs, so per-followed-company counts (matched_lead_reactivation_contacts,
     matched_opportunity_history_events, ...) can be computed by joining
     against those outputs. Writes the five company_follow_*.csv outputs and
     returns the sanitized dict consumed by export_public_dashboard_data.py.
     Does NOT re-open Stage A's resolution decisions.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd

from src.company_normalizer import normalize as normalize_company, normalize_for_search
from src.company_dictionary_enrichment import (
    COMPANY_TO_V5, SUBSTRING_TO_V5, CONF,
    GLOBAL_STAFFING, GLOBAL_CONSULTING, GLOBAL_TECH, GLOBAL_OPPORTUNITY,
    LATAM_USD_LIKELY, US_CANADA_LIKELY, NEEDS_MAPPING,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
PRIVATE_DIR = OUTPUTS_DIR / "private"

RECRUITING_PERSONAS  = {"Recruiter", "Talent Acquisition", "Sourcer"}
HIRING_PERSONAS       = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS  = {"Data Engineering Manager", "Head of Data", "Director"}

GEOGRAPHY_NOTE = (
    "Company Follows improves company relevance and opportunity-market inference, "
    "but LinkedIn export still does not provide exact company location. Follow "
    "signals are not exact geography."
)

# Generic words that must never be the sole basis for a fuzzy company-follow
# match — mirrors the guard company_resolution_v7.py uses for its own fuzzy
# clustering, applied independently here so this module has no private
# cross-module dependency.
GENERIC_FOLLOW_WORDS = {
    "tech", "technology", "technologies", "digital", "solutions", "solution",
    "consulting", "consultoria", "consultancy", "group", "grupo", "services",
    "service", "data", "systems", "sistemas", "global", "holdings", "holding",
    "ventures", "partners", "labs", "software", "informatica", "information",
    "capital", "financial", "finance", "business", "world", "worldwide",
    "people", "markets", "market", "studio", "code", "insights", "insight",
    "research", "media", "brands", "network", "networks", "enterprise",
    "enterprises", "invest", "investments", "vagas", "instituto", "institute",
    "empresa", "company", "corp", "corporation", "international", "brasil",
    "brazil", "latam", "europe", "america", "americas", "remote", "partnership",
    "partnerships", "latino", "latina", "gruppe", "spaces", "comercial",
    "commercial", "coworking",
}

_TOKEN_SPLIT_RE = re.compile(r"[\s\-&/|.]+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# ── Keyword classification (Part 5 of the spec) ──────────────────────────────
_STAFFING_KW = re.compile(
    r"staffing|recruiting|recruitment|\btalent\b|headhunt|executive search|"
    r"outsourc|talent solutions|talent marketplace",
    re.IGNORECASE,
)
_CONSULTING_KW = re.compile(
    r"consulting|consultoria|consultancy|advisory|professional services", re.IGNORECASE
)
_LATAM_KW = re.compile(
    r"latam|latin america|south america|nearshore|nearshoring", re.IGNORECASE
)
_US_KW = re.compile(
    r"\busa\b|united states|\bu\.s\.\b|north america|\bcanada\b", re.IGNORECASE
)
_INTL_KW = re.compile(r"international|worldwide|\bglobal\b", re.IGNORECASE)
_REMOTE_KW = re.compile(r"\bremote\b", re.IGNORECASE)


def _remove_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def build_company_follow_key(name: str) -> str:
    """lowercase -> strip legal/region/generic suffixes + aliases (reusing
    company_normalizer) -> strip accents -> strip punctuation -> collapse
    whitespace. Also strips a couple of extra suffixes ("company", "co.")
    that company_normalizer.normalize() does not remove."""
    if not name or not isinstance(name, str):
        return ""
    base = normalize_company(name)
    base = _remove_accents(base)
    base = re.sub(r"\b(company|co\.?)\b", "", base, flags=re.IGNORECASE)
    base = _PUNCT_RE.sub(" ", base)
    base = _WS_RE.sub(" ", base).strip()
    return base


def _tokens(key: str) -> set:
    # min length 5 (not 4) — short generic words survive a length-4 filter
    # far too often ("code", "vaga") and this matcher's false-positive cost
    # (silently linking two unrelated companies) is higher than a missed match.
    return {t for t in _TOKEN_SPLIT_RE.split(key) if len(t) >= 5 and t not in GENERIC_FOLLOW_WORDS}


# ══════════════════════════════════════════════════════════════════════════
# Part 1-2: load + normalize Company Follows.csv
# ══════════════════════════════════════════════════════════════════════════

def load_and_prepare_company_follows() -> pd.DataFrame | None:
    """Loads Company Follows.csv (optional — may be absent this snapshot).
    Returns None (never a fabricated/empty-but-truthy frame) if unavailable,
    so callers can log an honest 'unavailable' state rather than silently
    reusing a prior week's file."""
    from src.load_data import load_company_follows

    raw = load_company_follows()
    if raw is None or raw.empty:
        logger.warning(
            "  Company Follow Intelligence: Company Follows.csv not available this run — "
            "skipping (no fabricated data)."
        )
        return None

    org_col = "Organization" if "Organization" in raw.columns else raw.columns[0]
    date_col = "Followed On" if "Followed On" in raw.columns else (
        raw.columns[1] if len(raw.columns) > 1 else None
    )

    # Build company_name + followed_on_dt together, BEFORE filtering/dedup/
    # reset_index, so the two columns never drift out of row-alignment.
    # LinkedIn's "Followed On" export includes a UTC timezone suffix — parse
    # as UTC then strip tz info so later date-math never mixes naive/aware.
    df = pd.DataFrame({"company_name": raw[org_col].fillna("").astype(str).str.strip()})
    if date_col:
        parsed = pd.to_datetime(raw[date_col], errors="coerce", utc=True)
        df["followed_on_dt"] = parsed.dt.tz_localize(None)
    else:
        df["followed_on_dt"] = pd.NaT

    df = df[df["company_name"] != ""].drop_duplicates(subset=["company_name"]).reset_index(drop=True)
    if df.empty:
        return None

    df["company_follow_key"] = df["company_name"].apply(build_company_follow_key)
    df["followed_on_display"] = df["followed_on_dt"].dt.strftime("%Y-%m-%d").fillna("")

    today = pd.Timestamp.now().normalize()
    days = (today - df["followed_on_dt"].dt.normalize()).dt.days
    # -1 sentinel = follow date unknown/unparsable; never fabricate a real day count.
    df["days_since_followed"] = days.fillna(-1).astype(int)

    logger.info(f"  Company Follow Intelligence: {len(df):,} followed companies loaded")
    return df


# ══════════════════════════════════════════════════════════════════════════
# Part 5: classify a followed company (category / bucket / confidence)
# ══════════════════════════════════════════════════════════════════════════

def _company_dict_bucket(follow_key: str):
    if not follow_key:
        return None
    if follow_key in COMPANY_TO_V5:
        return COMPANY_TO_V5[follow_key], CONF["exact_dict"], f"exact company dictionary match: {follow_key}"
    search_form = normalize_for_search(follow_key)
    for key, bucket in SUBSTRING_TO_V5:
        if key in search_form:
            return bucket, CONF["exact_dict"] - 0.05, f"substring company dictionary match: {key}"
    return None


def classify_followed_company(raw_name: str, follow_key: str) -> dict:
    """Never fabricates geography: only returns a bucket when a company
    dictionary hit or an explicit keyword justifies it. Otherwise flags the
    company for manual review."""
    text = raw_name or ""
    signals: list[str] = []
    category = ""
    bucket = ""
    confidence = 0.0
    reasons: list[str] = []

    dict_hit = _company_dict_bucket(follow_key)
    if dict_hit:
        bucket, confidence, reason = dict_hit
        reasons.append(reason)
        if bucket == GLOBAL_STAFFING:
            category = GLOBAL_STAFFING
        elif bucket == GLOBAL_CONSULTING:
            category = GLOBAL_CONSULTING
        elif bucket == GLOBAL_TECH:
            category = GLOBAL_TECH

    if _STAFFING_KW.search(text):
        signals.append("staffing_keyword")
        category = category or GLOBAL_STAFFING
        if not bucket:
            bucket = GLOBAL_STAFFING
            confidence = max(confidence, 0.80)
            reasons.append("staffing/recruiting keyword in company name")
    elif _CONSULTING_KW.search(text):
        signals.append("consulting_keyword")
        category = category or GLOBAL_CONSULTING
        if not bucket:
            bucket = GLOBAL_OPPORTUNITY
            confidence = max(confidence, 0.75)
            reasons.append("consulting/advisory keyword in company name")

    if _LATAM_KW.search(text):
        signals.append("latam_keyword")
        bucket = LATAM_USD_LIKELY
        confidence = max(confidence, 0.85)
        reasons.append("LATAM/nearshore keyword in company name")
    elif _US_KW.search(text):
        signals.append("us_keyword")
        bucket = US_CANADA_LIKELY
        confidence = max(confidence, 0.85)
        reasons.append("US/Canada keyword in company name")
    elif _INTL_KW.search(text) or _REMOTE_KW.search(text):
        signals.append("international_remote_keyword")
        bucket = bucket or GLOBAL_OPPORTUNITY
        confidence = max(confidence, 0.70)
        reasons.append("international/remote keyword in company name")

    if not bucket:
        return {
            "likely_company_category": "",
            "likely_opportunity_bucket": "",
            "follow_signal_confidence": 0.0,
            "company_follow_reason": "no company dictionary, keyword, or market signal found — needs manual review",
            "keyword_signals": signals,
        }

    return {
        "likely_company_category": category,
        "likely_opportunity_bucket": bucket,
        "follow_signal_confidence": round(min(confidence, 0.90), 2),
        "company_follow_reason": "; ".join(reasons),
        "keyword_signals": signals,
    }


# ══════════════════════════════════════════════════════════════════════════
# Part 3: match followed companies to connection companies
# ══════════════════════════════════════════════════════════════════════════

def _build_connection_company_index(df: pd.DataFrame):
    company_norm = df.get("company_clean", pd.Series(index=df.index, dtype=object)).fillna("").apply(normalize_company)
    index: dict[str, list] = {}
    for key, idx in company_norm.groupby(company_norm).groups.items():
        if not key:
            continue
        index[key] = list(idx)
    return index, company_norm


def match_follow_to_companies(follow_key: str, conn_index: dict):
    """Exact normalized match first; then safe fuzzy (substring containment
    for long names, meaningful token overlap). Never fuzzy-matches short
    ambiguous names (<4 chars normalized). Returns
    (matched_company_keys, match_method, match_confidence)."""
    if not follow_key or len(follow_key) < 3:
        return [], "no_match", 0.0

    if follow_key in conn_index:
        return [follow_key], "exact_normalized", 0.95

    if len(follow_key) < 4:
        # Too short/ambiguous to trust for fuzzy matching (e.g. "ci", "gi").
        return [], "no_match", 0.0

    follow_tokens = _tokens(follow_key)
    candidates = []
    for conn_key in conn_index:
        if not conn_key or len(conn_key) < 4:
            continue
        # Substring containment: only for long names, AND only when the
        # shorter side is a substantial fraction of the longer side — an
        # 8-char company name buried inside an unrelated 30-char name
        # (e.g. "linkedin" inside "linkedin guide to creating leads") is
        # noise, not a real company match.
        if len(follow_key) >= 8 and len(conn_key) >= 8 and (follow_key in conn_key or conn_key in follow_key):
            shorter, longer = sorted((len(follow_key), len(conn_key)))
            if shorter / longer >= 0.4:
                candidates.append((conn_key, "substring_containment", 0.85))
            continue
        conn_tokens = _tokens(conn_key)
        if not follow_tokens or not conn_tokens:
            continue
        shared = follow_tokens & conn_tokens
        if not shared:
            continue
        # A single shared word is only trustworthy if it's clearly
        # distinctive (long) — short single-word overlaps are exactly how
        # unrelated companies ("Klube Capital" / "EP Capital") collide.
        if len(shared) == 1 and len(next(iter(shared))) < 6:
            continue
        smaller = min(len(follow_tokens), len(conn_tokens))
        if smaller and (len(shared) / smaller) >= 0.75:
            candidates.append((conn_key, "token_overlap_fuzzy", 0.78))

    if not candidates:
        return [], "no_match", 0.0

    candidates.sort(key=lambda t: (0 if t[1] == "substring_containment" else 1, -len(t[0])))
    method = candidates[0][1]
    confidence = candidates[0][2]
    return [c[0] for c in candidates], method, confidence


# ══════════════════════════════════════════════════════════════════════════
# STAGE A — resolve NEEDS_COMPANY_MAPPING rows using company-follow + an
# honest secondary signal already available on df at this point in the
# pipeline (persona, company_category, or a dictionary/keyword hit on the
# followed company's own name). Never resolves on a bare name match alone.
# ══════════════════════════════════════════════════════════════════════════

def apply_company_follow_resolution(df: pd.DataFrame, follows_df: pd.DataFrame | None):
    df = df.copy()

    if "opportunity_market_v5" not in df.columns:
        logger.warning("  Company Follow Intelligence: opportunity_market_v5 missing — skipping resolution pass")
        return df, {"available": False, "reason": "opportunity_market_v5 column missing"}, pd.DataFrame()

    if follows_df is None or follows_df.empty:
        return df, {"available": False, "reason": "Company Follows.csv not available this run"}, pd.DataFrame()

    for col, default in [("company_resolution_source", ""), ("company_resolution_confidence", 0.0)]:
        if col not in df.columns:
            df[col] = default

    needs_before = int((df["opportunity_market_v5"] == NEEDS_MAPPING).sum())
    conn_index, _ = _build_connection_company_index(df)
    persona_series = df.get("persona", pd.Series(index=df.index, dtype=object)).fillna("")
    category_series = df.get("company_category", pd.Series(index=df.index, dtype=object)).fillna("")

    match_rows = []
    resolved_count = 0
    matched_no_signal = 0

    for _, frow in follows_df.iterrows():
        follow_key = frow["company_follow_key"]
        raw_name = frow["company_name"]
        matched_keys, method, match_conf = match_follow_to_companies(follow_key, conn_index)
        classification = classify_followed_company(raw_name, follow_key)

        matched_idx = []
        for k in matched_keys:
            matched_idx.extend(conn_index.get(k, []))

        match_rows.append({
            "company_name": raw_name,
            "company_follow_key": follow_key,
            "followed_on": frow.get("followed_on_display", ""),
            "days_since_followed": int(frow.get("days_since_followed", -1)),
            "match_method": method,
            "match_confidence": match_conf,
            "matched_connection_company_keys": "; ".join(matched_keys),
            "matched_connection_count": len(matched_idx),
            "likely_company_category": classification["likely_company_category"],
            "likely_opportunity_bucket": classification["likely_opportunity_bucket"],
            "follow_signal_confidence": classification["follow_signal_confidence"],
            "company_follow_reason": classification["company_follow_reason"],
        })

        if not matched_idx:
            continue

        for idx in matched_idx:
            if df.at[idx, "opportunity_market_v5"] != NEEDS_MAPPING:
                continue

            row_persona = persona_series.get(idx, "")
            row_category = category_series.get(idx, "")
            has_persona_signal = row_persona in (RECRUITING_PERSONAS | HIRING_PERSONAS | DATA_LEADER_PERSONAS)
            has_keyword_signal = bool(classification["keyword_signals"])
            has_category_signal = bool(row_category) and row_category not in ("OTHER", "")
            has_dict_or_keyword_bucket = bool(classification["likely_opportunity_bucket"])

            if not (has_persona_signal or has_keyword_signal or has_category_signal or has_dict_or_keyword_bucket):
                matched_no_signal += 1
                continue

            if classification["likely_opportunity_bucket"]:
                bucket = classification["likely_opportunity_bucket"]
                conf = classification["follow_signal_confidence"]
                reason = (f"Company Follow Signal: followed company '{raw_name}' matches contact's company "
                          f"— {classification['company_follow_reason']}")
            elif has_persona_signal:
                bucket = GLOBAL_STAFFING if row_persona in RECRUITING_PERSONAS else GLOBAL_OPPORTUNITY
                conf = 0.65
                reason = (f"Company Follow Signal: followed company '{raw_name}' matches contact's company; "
                          f"contact persona ({row_persona}) is a recruiting/hiring/data-leadership signal")
            else:  # has_category_signal
                bucket = {"GLOBAL_STAFFING": GLOBAL_STAFFING, "GLOBAL_TECH": GLOBAL_TECH,
                          "GLOBAL_CONSULTING": GLOBAL_CONSULTING}.get(row_category, GLOBAL_OPPORTUNITY)
                conf = 0.60
                reason = (f"Company Follow Signal: followed company '{raw_name}' matches contact's company; "
                          f"existing company category ({row_category})")

            df.at[idx, "opportunity_market_v5"] = bucket
            df.at[idx, "opportunity_bucket"] = bucket
            df.at[idx, "opportunity_confidence"] = conf
            df.at[idx, "opportunity_reason"] = reason
            df.at[idx, "needs_manual_company_mapping"] = False
            df.at[idx, "company_resolution_source"] = "company_follow_signal"
            df.at[idx, "company_resolution_confidence"] = conf
            if "resolution_source" in df.columns:
                df.at[idx, "resolution_source"] = "COMPANY_FOLLOW_SIGNAL"
                df.at[idx, "resolution_confidence"] = conf
                df.at[idx, "resolution_reason"] = reason
            if "manual_review_required" in df.columns:
                df.at[idx, "manual_review_required"] = True
            resolved_count += 1

    needs_after = int((df["opportunity_market_v5"] == NEEDS_MAPPING).sum())
    matches_df = pd.DataFrame(match_rows)

    summary = {
        "available": True,
        "total_followed_companies": len(follows_df),
        "matched_followed_companies": int((matches_df["matched_connection_count"] > 0).sum()) if not matches_df.empty else 0,
        "needs_mapping_before": needs_before,
        "needs_mapping_after": needs_after,
        "resolved_by_company_follow_signal": resolved_count,
        "needs_mapping_reduction_count": needs_before - needs_after,
        "needs_mapping_reduction_pct": round((needs_before - needs_after) / needs_before * 100, 1) if needs_before else 0.0,
        "matched_needs_mapping_contacts_without_useful_signal": matched_no_signal,
        "geography_note": GEOGRAPHY_NOTE,
    }

    logger.info(
        f"  Company Follow Signal: Needs Mapping {needs_before:,} -> {needs_after:,} "
        f"(resolved={resolved_count}, matched-but-no-useful-signal={matched_no_signal})"
    )

    return df, summary, matches_df


# ══════════════════════════════════════════════════════════════════════════
# STAGE B — full per-company intelligence table + 5 CSV outputs, run after
# Lead Reactivation / Opportunity History / USD Contract CRM have written
# their sanitized CSVs this pass.
# ══════════════════════════════════════════════════════════════════════════

_EMPTY_INTEL_COLS = [
    "company_name", "company_follow_key", "followed_on", "days_since_followed",
    "matched_connection_count", "matched_recruiters", "matched_talent_acquisition",
    "matched_hiring_managers", "matched_data_leaders", "matched_top_contacts",
    "matched_untapped_contacts", "matched_lead_reactivation_contacts",
    "matched_opportunity_history_events", "matched_inbound_opportunities",
    "matched_soft_closed_leads", "matched_usd_crm_leads",
    "likely_company_category", "likely_opportunity_bucket",
    "follow_signal_confidence", "company_follow_reason", "signals",
]
_EMPTY_MATCH_COLS = [
    "company_name", "company_follow_key", "followed_on", "days_since_followed",
    "match_method", "match_confidence", "matched_connection_company_keys",
    "matched_connection_count", "likely_company_category", "likely_opportunity_bucket",
    "follow_signal_confidence", "company_follow_reason",
]


def _safe_read_csv(path: Path, cols: list) -> pd.DataFrame:
    try:
        if path.exists():
            return pd.read_csv(path, dtype=str, low_memory=False, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning(f"  Company Follow Intelligence: could not read {path.name}: {exc}")
    return pd.DataFrame(columns=cols)


def _norm_key_col(frame: pd.DataFrame, col: str) -> pd.Series:
    if frame.empty or col not in frame.columns:
        return pd.Series(dtype=str)
    return frame[col].fillna("").apply(normalize_company)


def _write_empty_outputs() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=_EMPTY_INTEL_COLS).to_csv(
        OUTPUTS_DIR / "company_follow_intelligence.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(columns=_EMPTY_MATCH_COLS).to_csv(
        OUTPUTS_DIR / "company_follow_company_matches.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(columns=_EMPTY_INTEL_COLS).to_csv(
        OUTPUTS_DIR / "company_follow_mapping_candidates.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(columns=_EMPTY_INTEL_COLS).to_csv(
        OUTPUTS_DIR / "followed_companies_needing_review.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{
        "total_followed_companies": 0, "matched_followed_companies": 0,
        "companies_with_recruiters_or_ta": 0, "companies_with_opportunity_history": 0,
        "recently_followed_companies": 0, "contacts_resolved_via_company_follow": 0,
        "contacts_still_needing_review": 0, "companies_still_needing_review": 0,
        "needs_mapping_before_company_follow_pass": 0,
        "needs_mapping_after_company_follow_pass": 0,
    }]).to_csv(OUTPUTS_DIR / "company_follow_resolution_summary.csv", index=False, encoding="utf-8-sig")


def build_company_follow_intelligence(
    df: pd.DataFrame,
    follows_df: pd.DataFrame | None,
    matches_df: pd.DataFrame | None,
    resolution_summary: dict | None = None,
) -> dict:
    resolution_summary = resolution_summary or {}

    if follows_df is None or follows_df.empty:
        _write_empty_outputs()
        return {
            "available": False,
            "reason": "Company Follows.csv not available this run",
            "summary": {
                "total_followed_companies": 0, "matched_followed_companies": 0,
                "companies_with_recruiters_or_ta": 0, "companies_with_opportunity_history": 0,
                "recently_followed_companies": 0,
                "contacts_resolved_via_company_follow": resolution_summary.get("resolved_by_company_follow_signal", 0),
                "contacts_still_needing_review": resolution_summary.get("matched_needs_mapping_contacts_without_useful_signal", 0),
                "companies_still_needing_review": 0,
            },
            "geography_note": GEOGRAPHY_NOTE,
        }

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    msgs        = _safe_read_csv(OUTPUTS_DIR / "message_threads_summary.csv", ["company_clean"])
    events      = _safe_read_csv(OUTPUTS_DIR / "opportunity_history_events.csv", ["company"])
    inbound     = _safe_read_csv(OUTPUTS_DIR / "inbound_opportunities.csv", ["company"])
    soft_closed = _safe_read_csv(OUTPUTS_DIR / "soft_closed_future_leads.csv", ["company"])
    usd_leads   = _safe_read_csv(OUTPUTS_DIR / "usd_follow_up_queue.csv", ["company"])
    untapped    = _safe_read_csv(PRIVATE_DIR / "untapped_outreach_backlog.csv", ["company_clean"])

    msgs_key        = _norm_key_col(msgs, "company_clean")
    events_key      = _norm_key_col(events, "company")
    inbound_key     = _norm_key_col(inbound, "company")
    soft_closed_key = _norm_key_col(soft_closed, "company")
    usd_key         = _norm_key_col(usd_leads, "company")
    untapped_key    = _norm_key_col(untapped, "company_clean")

    persona_series = df.get("persona", pd.Series(index=df.index, dtype=object)).fillna("")
    company_key_all = df.get("company_clean", pd.Series(index=df.index, dtype=object)).fillna("").apply(normalize_company)

    if "priority_score" in df.columns:
        pri = pd.to_numeric(df["priority_score"], errors="coerce").fillna(0)
        top_idx = pri.sort_values(ascending=False).head(200).index
    else:
        top_idx = pd.Index([])

    matches_lookup = {}
    if matches_df is not None and not matches_df.empty:
        for _, r in matches_df.iterrows():
            matches_lookup[r["company_follow_key"]] = r

    intelligence_rows, review_rows, mapping_candidate_rows = [], [], []

    for _, frow in follows_df.iterrows():
        follow_key = frow["company_follow_key"]
        raw_name = frow["company_name"]
        days_since = int(frow.get("days_since_followed", -1))

        mrow = matches_lookup.get(follow_key)
        matched_keys = []
        if mrow is not None and mrow.get("matched_connection_company_keys"):
            matched_keys = [k for k in str(mrow["matched_connection_company_keys"]).split("; ") if k]

        matched_mask = company_key_all.isin(matched_keys) if matched_keys else pd.Series(False, index=df.index)
        matched_count = int(matched_mask.sum())
        matched_personas = persona_series[matched_mask]

        matched_recruiters = int((matched_personas == "Recruiter").sum())
        matched_ta = int(matched_personas.isin({"Talent Acquisition", "Sourcer"}).sum())
        matched_hm = int(matched_personas.isin(HIRING_PERSONAS).sum())
        matched_dl = int(matched_personas.isin(DATA_LEADER_PERSONAS).sum())
        matched_top = int((matched_mask & df.index.isin(top_idx)).sum())

        matched_leads_ct   = int(msgs_key.isin(matched_keys).sum()) if matched_keys else 0
        matched_events_ct  = int(events_key.isin(matched_keys).sum()) if matched_keys else 0
        matched_inbound_ct = int(inbound_key.isin(matched_keys).sum()) if matched_keys else 0
        matched_soft_ct    = int(soft_closed_key.isin(matched_keys).sum()) if matched_keys else 0
        matched_usd_ct     = int(usd_key.isin(matched_keys).sum()) if matched_keys else 0
        matched_untapped_ct = int(untapped_key.isin(matched_keys).sum()) if matched_keys else 0

        likely_category = str(mrow.get("likely_company_category", "") or "") if mrow is not None else ""
        likely_bucket = str(mrow.get("likely_opportunity_bucket", "") or "") if mrow is not None else ""
        confidence = float(mrow.get("follow_signal_confidence", 0.0) or 0.0) if mrow is not None else 0.0
        reason = str(mrow.get("company_follow_reason", "") or "") if mrow is not None else ""

        signals = []
        if (matched_recruiters + matched_ta) > 0:
            signals.append("FOLLOWED_COMPANY_RECRUITER_SIGNAL")
        if 0 <= days_since <= 30:
            signals.append("RECENTLY_FOLLOWED_COMPANY")
        if matched_leads_ct > 0:
            signals.append("FOLLOWED_COMPANY_WITH_MESSAGE_HISTORY")
        if (matched_events_ct + matched_inbound_ct) > 0:
            signals.append("FOLLOWED_COMPANY_WITH_OPPORTUNITY_SIGNAL")
        if not likely_bucket:
            signals.append("FOLLOWED_COMPANY_NEEDS_REVIEW")

        row = {
            "company_name": raw_name,
            "company_follow_key": follow_key,
            "followed_on": frow.get("followed_on_display", ""),
            "days_since_followed": days_since,
            "matched_connection_count": matched_count,
            "matched_recruiters": matched_recruiters,
            "matched_talent_acquisition": matched_ta,
            "matched_hiring_managers": matched_hm,
            "matched_data_leaders": matched_dl,
            "matched_top_contacts": matched_top,
            "matched_untapped_contacts": matched_untapped_ct,
            "matched_lead_reactivation_contacts": matched_leads_ct,
            "matched_opportunity_history_events": matched_events_ct,
            "matched_inbound_opportunities": matched_inbound_ct,
            "matched_soft_closed_leads": matched_soft_ct,
            "matched_usd_crm_leads": matched_usd_ct,
            "likely_company_category": likely_category,
            "likely_opportunity_bucket": likely_bucket,
            "follow_signal_confidence": confidence,
            "company_follow_reason": reason,
            "signals": "; ".join(signals),
        }
        intelligence_rows.append(row)

        if "FOLLOWED_COMPANY_NEEDS_REVIEW" in signals or matched_count == 0:
            review_rows.append(row)
        if matched_count > 0:
            mapping_candidate_rows.append(row)

    intel_df = pd.DataFrame(intelligence_rows, columns=_EMPTY_INTEL_COLS)
    review_df = pd.DataFrame(review_rows, columns=_EMPTY_INTEL_COLS)
    mapping_df = pd.DataFrame(mapping_candidate_rows, columns=_EMPTY_INTEL_COLS)

    # Part 10 — Sunday manual-mapping ranked list.
    if not review_df.empty:
        review_df = review_df.sort_values(
            by=["matched_recruiters", "matched_talent_acquisition",
                "matched_opportunity_history_events", "matched_connection_count",
                "days_since_followed"],
            ascending=[False, False, False, False, True],
        )

    matches_out = matches_df if (matches_df is not None and not matches_df.empty) else pd.DataFrame(columns=_EMPTY_MATCH_COLS)

    intel_df.to_csv(OUTPUTS_DIR / "company_follow_intelligence.csv", index=False, encoding="utf-8-sig")
    matches_out.to_csv(OUTPUTS_DIR / "company_follow_company_matches.csv", index=False, encoding="utf-8-sig")
    mapping_df.to_csv(OUTPUTS_DIR / "company_follow_mapping_candidates.csv", index=False, encoding="utf-8-sig")
    review_df.to_csv(OUTPUTS_DIR / "followed_companies_needing_review.csv", index=False, encoding="utf-8-sig")

    summary = {
        "total_followed_companies": len(follows_df),
        "matched_followed_companies": int((intel_df["matched_connection_count"] > 0).sum()) if not intel_df.empty else 0,
        "companies_with_recruiters_or_ta": int(
            ((intel_df["matched_recruiters"] + intel_df["matched_talent_acquisition"]) > 0).sum()
        ) if not intel_df.empty else 0,
        "companies_with_opportunity_history": int(
            ((intel_df["matched_opportunity_history_events"] + intel_df["matched_inbound_opportunities"]) > 0).sum()
        ) if not intel_df.empty else 0,
        "recently_followed_companies": int(
            intel_df["days_since_followed"].between(0, 30).sum()
        ) if not intel_df.empty else 0,
        "contacts_resolved_via_company_follow": resolution_summary.get("resolved_by_company_follow_signal", 0),
        # Genuine contact-level count (from Stage A): NEEDS_COMPANY_MAPPING
        # contacts whose company matched a followed company but had no other
        # useful signal, so they stayed unresolved. NOT the same as the
        # company-level count below — a company row in the review queue can
        # represent many contacts, or none at all if it never matched.
        "contacts_still_needing_review": resolution_summary.get("matched_needs_mapping_contacts_without_useful_signal", 0),
        "companies_still_needing_review": int(len(review_df)),
        "needs_mapping_before_company_follow_pass": resolution_summary.get("needs_mapping_before", 0),
        "needs_mapping_after_company_follow_pass": resolution_summary.get("needs_mapping_after", 0),
    }
    pd.DataFrame([summary]).to_csv(OUTPUTS_DIR / "company_follow_resolution_summary.csv", index=False, encoding="utf-8-sig")

    logger.info(
        f"  Company Follow Intelligence: {summary['total_followed_companies']} followed | "
        f"matched={summary['matched_followed_companies']} "
        f"recruiter/TA={summary['companies_with_recruiters_or_ta']} "
        f"opportunity_history={summary['companies_with_opportunity_history']} "
        f"recent={summary['recently_followed_companies']} "
        f"resolved_contacts={summary['contacts_resolved_via_company_follow']} "
        f"contacts_still_review={summary['contacts_still_needing_review']} "
        f"companies_still_review={summary['companies_still_needing_review']}"
    )

    return {
        "available": True,
        "summary": summary,
        "companies": intel_df.to_dict(orient="records"),
        "needs_review": review_df.to_dict(orient="records"),
        "mapping_candidates": mapping_df.sort_values(
            by=["matched_recruiters", "matched_connection_count"], ascending=[False, False]
        ).head(50).to_dict(orient="records") if not mapping_df.empty else [],
        "geography_note": GEOGRAPHY_NOTE,
    }
