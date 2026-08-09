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
from datetime import date, datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
DOCS_DIR    = ROOT / "docs"
ASSETS_DIR  = DOCS_DIR / "assets"
DATA_DIR    = ASSETS_DIR / "data"

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
    # V8 conversation-state engine — relationship value vs immediate action
    "relationship_value_score", "immediate_action_score",
    "process_state", "reply_obligation", "action_urgency", "next_action_date",
    # V6 company resolution — computed signals, no raw content
    "company_resolution_source", "company_resolution_confidence",
    "company_evidence_count", "company_dominant_bucket",
    "company_dominant_bucket_share", "cross_contact_propagation_used",
    "message_signal_used", "company_canonical",
    # Untapped Outreach Scoring V9 — merged in from untapped_network_intelligence.py
    # so Top Contacts can sort/filter by the same score used on the Untapped
    # Network page (see build_public_contacts()). Sanitized fields only.
    "untapped_outreach_score", "untapped_reason", "untapped_category",
    "contact_history_status", "recommended_first_action", "first_message_angle",
    # Untapped Activation Potential Scoring (V10) — same merge-in path as V9.
    "untapped_activation_potential_score", "untapped_execution_score",
    "activation_category", "activation_reason", "first_message_priority",
    # Needs Mapping backlog (Part 4)
    "mapping_priority_score", "mapping_reason_short",
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


def _safe_str(v, default: str = "") -> str:
    """NaN/None-safe string coercion — a blank pandas cell reads back as
    float('nan'), which is JSON-illegal (json.dump emits a literal NaN
    token that fails JSON.parse() in every browser) and also truthy in
    Python, so a plain `v or default` does not catch it."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    return str(v)


def _safe_num(v, default=0):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    try:
        n = float(v)
        return default if pd.isna(n) else n
    except (TypeError, ValueError):
        return default


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
    untapped_scores: dict = None,
    needs_mapping_scores: dict = None,
) -> list:
    df = _enrich_action_fields(df)

    # Merge Untapped Outreach Scoring V9 fields (by normalized URL) — lets Top
    # Contacts sort/filter by untapped_outreach_score alongside the existing
    # scores, and makes sure a high-scoring never-contacted recruiter isn't
    # dropped from the top-N pool just because their base priority_score
    # doesn't reflect it.
    untapped_cols = [
        "untapped_outreach_score", "untapped_reason", "untapped_category",
        "contact_history_status", "recommended_first_action", "first_message_angle",
        # Untapped Activation Potential Scoring (V10)
        "untapped_activation_potential_score", "untapped_execution_score",
        "activation_category", "activation_reason", "first_message_priority",
    ]
    if untapped_scores:
        def _norm_u(url):
            return str(url or "").strip().rstrip("/").lower()

        for col in untapped_cols:
            df[col] = None
        for idx, row in df.iterrows():
            norm = _norm_u(row.get("url", ""))
            rec = untapped_scores.get(norm)
            if rec:
                for col in untapped_cols:
                    if col in rec:
                        df.at[idx, col] = rec[col]
        # NaN (from unmatched rows / coercion) is not valid JSON — 0 is the
        # correct semantic default for "no untapped score applies here"
        # (a contacted person, or one outside the top_untapped_contacts pool).
        df["untapped_outreach_score"] = pd.to_numeric(df["untapped_outreach_score"], errors="coerce").fillna(0)
        df["contact_history_status"] = df["contact_history_status"].where(df["contact_history_status"].notna(), "")
        # Untapped Activation Potential Scoring (V10) — same NaN-safe defaults.
        df["untapped_activation_potential_score"] = pd.to_numeric(
            df["untapped_activation_potential_score"], errors="coerce"
        ).fillna(0)
        df["untapped_execution_score"] = pd.to_numeric(df["untapped_execution_score"], errors="coerce").fillna(0)
        for col in ("activation_category", "activation_reason", "first_message_priority"):
            df[col] = df[col].where(df[col].notna(), "")

    # Merge Needs Mapping backlog fields (Part 4) — lets Top Contacts sort by
    # mapping_priority_score and cross-navigate from the Opportunity Market
    # person drill-down without a separate page-routing mechanism.
    mapping_cols = ["mapping_priority_score", "mapping_reason_short"]
    if needs_mapping_scores:
        def _norm_m(url):
            return str(url or "").strip().rstrip("/").lower()

        for col in mapping_cols:
            df[col] = None
        for idx, row in df.iterrows():
            rec = needs_mapping_scores.get(_norm_m(row.get("url", "")))
            if rec:
                for col in mapping_cols:
                    if col in rec:
                        df.at[idx, col] = rec[col]
        df["mapping_priority_score"] = pd.to_numeric(df["mapping_priority_score"], errors="coerce").fillna(0)
        df["mapping_reason_short"] = df["mapping_reason_short"].where(df["mapping_reason_short"].notna(), "")

    # Merge outreach scores (by normalized URL)
    if outreach_scores:
        def _norm(url):
            return str(url or "").strip().rstrip("/").lower()

        for col in [
            "outreach_adjusted_score", "outreach_status", "outreach_reason",
            "has_message_history", "replied_to_me", "ghosted_me", "auto_reply_only",
            "last_message_date", "days_since_last_message",
            "prior_positive_signal", "prior_rejection",
            "relationship_value_score", "immediate_action_score",
            "process_state", "reply_obligation", "action_urgency", "next_action_date",
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

        # V8: outreach_adjusted_score is now weighted primarily toward
        # immediate_action_score (Part 12), which is intentionally LOW for
        # anyone not in an urgent state (e.g. a warm-but-not-urgent recruiter
        # relationship). Using it alone to decide WHICH 200 contacts make the
        # public export would silently drop every valuable-but-not-urgent
        # message-history contact from Top Contacts entirely. Select on
        # whichever dimension is highest — base network priority, relationship
        # value, or immediate action — so nobody is dropped just for being
        # merely "not urgent right now"; the client-side default sort (Part 14)
        # still ranks by immediate action first within this pool.
        rel_v = pd.to_numeric(df.get("relationship_value_score"), errors="coerce").fillna(0)
        act_v = pd.to_numeric(df.get("immediate_action_score"), errors="coerce").fillna(0)
        base_v = pd.to_numeric(df["priority_score"], errors="coerce").fillna(0)
        untap_v = pd.to_numeric(df.get("untapped_outreach_score"), errors="coerce").fillna(0)
        exec_v = pd.to_numeric(df.get("untapped_execution_score"), errors="coerce").fillna(0)
        df["_selection_score"] = pd.concat([base_v, rel_v, act_v, untap_v, exec_v], axis=1).max(axis=1)
        sort_col = "_selection_score"
    elif untapped_scores:
        # No message-history scores this run, but untapped scores are
        # available — a high-scoring never-contacted recruiter should still
        # make the top-N pool even if their base priority_score is modest.
        base_v = pd.to_numeric(df["priority_score"], errors="coerce").fillna(0)
        untap_v = pd.to_numeric(df.get("untapped_outreach_score"), errors="coerce").fillna(0)
        exec_v = pd.to_numeric(df.get("untapped_execution_score"), errors="coerce").fillna(0)
        df["_selection_score"] = pd.concat([base_v, untap_v, exec_v], axis=1).max(axis=1)
        sort_col = "_selection_score"
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
    records = gap_df[safe_cols].to_dict(orient="records")

    # Attach a realistic LinkedIn search pack per row — never the internal
    # bucket label itself (see src/strategic_gap_search_builder.py). This
    # replaces the old frontend behavior of quoting `market` directly into
    # a LinkedIn keyword query (e.g. '"Hiring Manager" AND "LATAM USD"',
    # which returns nothing because LATAM_USD is not a real search term).
    return _enrich_plan_with_search_pack(records)


def _enrich_plan_with_search_pack(records: list) -> list:
    """Same search-pack enrichment as build_public_gap_data, applied to the
    30/60/90-day Action Plan rows (they share the same market/persona/
    urgency_level/gap_count shape). Fixes the same bug on the Action Plan
    page: without this, its client-side fallback query builder could fall
    through to quoting the raw V2 market label (e.g. "US_CANADA_NEARSHORE",
    "EUROPE") directly into a LinkedIn search query."""
    try:
        from src.strategic_gap_search_builder import build_strategic_gap_search_pack
        for rec in records:
            rec["search_pack"] = build_strategic_gap_search_pack(
                rec.get("market", ""), rec.get("persona", ""),
                rec.get("urgency_level", "Medium"), rec.get("gap_count", 0),
            )
    except Exception as exc:
        logger.warning(f"  Action Plan search pack build failed (non-fatal): {exc}")
    return records


# ── V2 market label ↔ V5 opportunity-bucket set mapping ─────────────────────
# Strategic Gap targets (generate_action_plan.py) are defined against the
# older V2 market labels (market_v2/strategic_market column), while Untapped
# Network / Top Contacts carry the newer V5 opportunity_bucket. This mapping
# lets a gap segment ("LATAM_USD", "Recruiter") pull the right V5-bucketed
# never-contacted candidates for its "Recommended to Activate Next" tab.
# Mirrored client-side in app.js as GAP_MARKET_TO_V5_BUCKETS — keep both in
# sync if either changes.
MARKET_V2_TO_V5_BUCKETS = {
    "LATAM_USD":           ["LATAM_USD_CONFIRMED", "LATAM_USD_LIKELY"],
    "US_CANADA_NEARSHORE": ["US_CANADA_CONFIRMED", "US_CANADA_LIKELY"],
    "SPAIN_EU":            ["SPAIN_EU_CONFIRMED", "SPAIN_EU_LIKELY"],
    "EUROPE":              ["EUROPE_CONFIRMED", "EUROPE_LIKELY"],
}

SAFE_GAP_DRILLDOWN_COLS = [
    "full_name", "company_clean", "position_clean", "persona",
    "market", "opportunity_bucket", "priority_score",
    "outreach_status", "contact_history_status",
    "connected_on", "url", "segment", "reason",
]


def build_strategic_gap_people_drilldown(
    df: pd.DataFrame,
    gap_df: pd.DataFrame,
    outreach_scores: dict = None,
    untapped_scores: dict = None,
) -> list:
    """
    Tab A ("Current Contacts Counted") of the Strategic Gap drill-down.
    Reuses the EXACT predicate generate_action_plan.py uses for each gap row's
    current_count, so the drill-down's row count can never drift from the gap
    table's own number. Tab B ("New This Week") rows are appended later by
    weekly_kpi_delta.py with segment="new_this_week"; this function only ever
    emits segment="current" rows.
    """
    if gap_df is None or gap_df.empty:
        return []
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"
    if mkt_col not in df.columns or "persona" not in df.columns:
        return []

    outreach_scores = outreach_scores or {}
    untapped_scores = untapped_scores or {}

    def _norm(url):
        return str(url or "").strip().rstrip("/").lower()

    rows = []
    for _, gap_row in gap_df.iterrows():
        market  = gap_row.get("market")
        persona = gap_row.get("persona")
        if not market or not persona:
            continue
        subset = df[(df[mkt_col] == market) & (df["persona"] == persona)]
        if subset.empty:
            continue
        for _, r in subset.iterrows():
            u = _norm(r.get("url", ""))
            outreach = outreach_scores.get(u, {})
            untapped = untapped_scores.get(u, {})
            rows.append({
                "full_name":              _safe_str(r.get("full_name")),
                "company_clean":          _safe_str(r.get("company_clean")),
                "position_clean":         _safe_str(r.get("position_clean")),
                "persona":                _safe_str(r.get("persona")),
                "market":                 market,
                "opportunity_bucket":     _safe_str(r.get("opportunity_bucket")),
                "priority_score":         _safe_num(r.get("priority_score")),
                "outreach_status":        _safe_str(outreach.get("outreach_status")),
                "contact_history_status": _safe_str(untapped.get("contact_history_status")),
                "connected_on":           _safe_str(r.get("connected_on_clean")),
                "url":                    _safe_str(r.get("url")),
                "segment":                "current",
            })
    return rows


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


# ── Opportunity Market — full-population person segments (Part 5/6) ─────────
# Mirrors opportunity_market_v5.build_v5_summary()'s own bucket groupings
# exactly, so every Opportunity Market KPI card's displayed count equals what
# this array shows when filtered by market_segment — no separate matching
# logic to keep in sync. Full population (not top-N) — safe because every
# field here is already in SAFE_CONTACT_COLS/SAFE_NEEDS_MAPPING_PERSON_COLS.
_CONFIRMED_GEOGRAPHIC_BUCKETS = {
    "BRAZIL_CONFIRMED", "BRAZIL_LIKELY",
    "LATAM_USD_CONFIRMED", "LATAM_USD_LIKELY",
    "US_CANADA_CONFIRMED", "US_CANADA_LIKELY",
    "SPAIN_EU_CONFIRMED", "SPAIN_EU_LIKELY",
    "EUROPE_CONFIRMED", "EUROPE_LIKELY",
}
_GLOBAL_BUCKETS = {"GLOBAL_STAFFING", "GLOBAL_CONSULTING", "GLOBAL_TECH"}
_LANGUAGE_BUCKETS = {"LANGUAGE_PORTUGUESE_MARKET", "LANGUAGE_SPANISH_MARKET"}

SAFE_OPPORTUNITY_SEGMENT_COLS = [
    "full_name", "company_clean", "position_clean", "persona",
    "opportunity_bucket", "market_segment", "priority_score",
    "is_actionable", "url",
]


def _opportunity_market_segment(bucket: str) -> str:
    if bucket in _CONFIRMED_GEOGRAPHIC_BUCKETS:
        return "confirmed_geographic"
    if bucket in _GLOBAL_BUCKETS:
        return "global_buckets"
    if bucket in _LANGUAGE_BUCKETS:
        return "language_signal"
    if bucket == "GLOBAL_OPPORTUNITY":
        return "global_opportunity"
    if bucket == "NEEDS_COMPANY_MAPPING":
        return "needs_mapping"
    if bucket == "LOW_VALUE_UNRESOLVED":
        return "low_value"
    return "other"


def build_opportunity_market_people_segments(df: pd.DataFrame) -> list:
    if "opportunity_bucket" not in df.columns:
        return []
    rows = []
    for _, r in df.iterrows():
        bucket = _safe_str(r.get("opportunity_bucket"))
        if not bucket:
            continue
        rows.append({
            "full_name":          _safe_str(r.get("full_name")),
            "company_clean":      _safe_str(r.get("company_clean")),
            "position_clean":     _safe_str(r.get("position_clean")),
            "persona":            _safe_str(r.get("persona")),
            "opportunity_bucket": bucket,
            "market_segment":     _opportunity_market_segment(bucket),
            "priority_score":     _safe_num(r.get("priority_score")),
            "is_actionable":      bucket != "LOW_VALUE_UNRESOLVED",
            "url":                _safe_str(r.get("url")),
        })
    return rows


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
    # V6 response intelligence — sanitized fields only, no raw content
    "needs_my_response", "needs_response_confidence", "needs_response_reason",
    "response_intent_score", "manual_review_required", "last_sender_type",
    "conversation_recency_band", "sanitized_intent_label",
    # V8 multi-dimensional conversation state — sanitized fields only, no raw content
    "process_state", "relationship_state", "reply_obligation", "action_urgency",
    "closure_reason", "next_action_date", "reactivation_window_days",
    "relationship_value_score", "immediate_action_score",
    "conversation_state_confidence", "state_evidence_codes",
    "external_action_type", "request_resolved", "cooldown_state",
    # Lead Reactivation trust layer (Part 1) — sanitized explain fields
    "reply_obligation_confidence", "reply_reason_short", "action_priority_reason",
    "terminal_state_flag", "stale_conversation_flag", "recruiter_priority_flag",
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
    # V6 refined taxonomy summary counts
    "needs_my_response_confirmed", "needs_my_response_likely",
    "ambiguous_review_count", "message_review_queue_count",
    "follow_up_candidate", "previous_process_reusable", "closed_no_action",
    # V8 conversation-state KPI cards (Part 15)
    "active_interview_pipeline", "awaiting_recruiter_update",
    "rejected_closed", "location_eligibility_blocked",
    "talent_pool_career_site", "reactivate_this_month",
    "false_urgent_terminal_state_count", "conversation_state_review_queue_count",
    # contact lists
    "top_reactivation_contacts", "this_week_contacts", "needs_reply_contacts",
    # Lead Reactivation trust layer (Part 1) — operational summary counts
    "most_urgent_confirmed_count", "warm_recruiter_followups_count",
    "stale_but_valuable_count", "closed_low_action_count",
    "recruiter_priority_count", "high_confidence_reply_count",
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


# ── Untapped Network Intelligence (Part 12) — explicit allowlist, defense in
# depth on top of what untapped_network_intelligence.py already sanitizes.
# Never emits raw messages, email, phone, or the internal ambiguous-candidate
# lists — those stay in outputs/private/ only.
SAFE_UNTAPPED_SUMMARY_KEYS = {
    "total_connections", "has_conversation", "never_contacted_confirmed",
    "likely_never_contacted", "ambiguous_review", "reconciled_total",
    "high_value_untapped", "recruiters_untapped", "ta_untapped",
    "hiring_managers_untapped", "data_leaders_untapped",
    "latam_usd_untapped", "us_nearshore_untapped", "spain_eu_untapped",
    "connected_90d_plus_never_contacted", "connected_180d_plus_never_contacted",
    "this_week_queue_count", "manual_enriched_contacts",
}
SAFE_UNTAPPED_CONTACT_COLS = {
    "full_name", "company_clean", "position_clean", "persona", "seniority",
    "opportunity_bucket", "connected_on", "days_connected", "connection_age_bucket",
    "contact_history_status", "untapped_category", "untapped_outreach_score",
    "recommended_first_action", "first_message_angle", "profile_url",
    "conversation_match_confidence", "strategic_focus", "operational_category",
    # Untapped Outreach Scoring V9
    "untapped_reason", "untapped_opportunity_market", "untapped_market_confidence",
    "untapped_market_reason", "exact_location_available", "observed_location_manual",
    "is_manual_enriched",
    # Untapped Activation Potential Scoring (V10)
    "untapped_activation_potential_score", "untapped_execution_score",
    "connected_age_bucket", "activation_category", "activation_reason",
    "first_message_priority", "priority_score",
}

# Activation Pattern Learning (V10) — already aggregate-only (counts/rates
# and persona/bucket/age-bucket labels, no names, no raw messages) as built
# by build_activation_pattern_learning() — explicit allowlist here is
# defense in depth, same pattern as every other safe-* builder in this file.
SAFE_ACTIVATION_PATTERN_KEYS = {
    "available", "long_connected_contacted_all_time", "long_connected_contacted_this_week",
    "long_connected_replied", "long_connected_became_warm",
    "long_connected_cv_or_interview_requested", "conversion_rate_overall_pct",
}
SAFE_ACTIVATION_PATTERN_ROW_COLS = {
    "persona", "connected_age_bucket", "opportunity_bucket",
    "contacted", "replied", "became_warm", "conversion_rate_pct",
}


def _sanitize_untapped_contacts(contacts: list) -> list:
    return [{k: v for k, v in c.items() if k in SAFE_UNTAPPED_CONTACT_COLS} for c in (contacts or [])]


def build_activation_pattern_learning_public(data: dict) -> dict:
    if not data or not data.get("available"):
        return {"available": False}
    safe = {k: v for k, v in data.items() if k in SAFE_ACTIVATION_PATTERN_KEYS}
    for list_key in ("by_persona", "by_connected_age_bucket", "by_opportunity_bucket"):
        safe[list_key] = [
            {k: v for k, v in row.items() if k in SAFE_ACTIVATION_PATTERN_ROW_COLS}
            for row in (data.get(list_key) or [])
        ]
    return safe


def build_untapped_network_public(untapped_data: dict) -> dict:
    if not untapped_data or not untapped_data.get("available"):
        return {"available": False}
    summary = untapped_data.get("summary", {}) or {}
    return {
        "available": True,
        "summary": {k: v for k, v in summary.items() if k in SAFE_UNTAPPED_SUMMARY_KEYS},
        "match_method_breakdown": untapped_data.get("match_method_breakdown", {}),
        "top_untapped_contacts": _sanitize_untapped_contacts(untapped_data.get("top_untapped_contacts", [])),
        "this_week_queue": _sanitize_untapped_contacts(untapped_data.get("this_week_queue", [])),
        "activation_pattern_learning": build_activation_pattern_learning_public(
            untapped_data.get("activation_pattern_learning", {})
        ),
    }


# ── Opportunity Market — Needs Mapping person/company drill-down (Parts 2-4) ─
# Explicit allowlist, defense in depth on top of what opportunity_market_v5.py
# already builds from already-classified/sanitized fields — no raw content.
SAFE_NEEDS_MAPPING_SUMMARY_KEYS = {
    "backlog_size", "high_value_unresolved", "recruiters_unresolved",
    "talent_acquisition_unresolved", "hiring_managers_unresolved",
    "data_leaders_unresolved", "auto_resolvable_contacts", "auto_resolvable_companies",
    "companies_with_3plus_unresolved", "total_companies",
}
SAFE_NEEDS_MAPPING_COMPANY_COLS = {
    "company_clean", "contacts", "recruiters", "talent_acquisition",
    "hiring_managers", "data_leaders", "high_value_contacts",
    "auto_resolvable_count", "mapping_impact_score", "suggested_opportunity_bucket",
}
SAFE_NEEDS_MAPPING_PERSON_COLS = {
    "full_name", "company_clean", "position_clean", "persona",
    "suggested_opportunity_bucket", "mapping_priority_score",
    "resolution_source", "mapping_reason_short", "profile_url",
}


def build_needs_mapping_public(backlog: dict) -> dict:
    if not backlog or not backlog.get("available"):
        return {"available": False}
    summary = backlog.get("summary", {}) or {}
    companies = [
        {k: v for k, v in c.items() if k in SAFE_NEEDS_MAPPING_COMPANY_COLS}
        for c in (backlog.get("companies") or [])
    ]
    people = [
        {k: v for k, v in p.items() if k in SAFE_NEEDS_MAPPING_PERSON_COLS}
        for p in (backlog.get("people") or [])
    ]
    return {
        "available": True,
        "summary": {k: v for k, v in summary.items() if k in SAFE_NEEDS_MAPPING_SUMMARY_KEYS},
        "companies": companies,
        "people": people,
    }


# ── Company Follow Intelligence — explicit allowlist, defense in depth on
# top of what src/company_follow_intelligence.py already produces (company-
# level aggregates only — organization names the user follows plus counts
# already sanitized upstream, no raw messages/email/phone/notes ever touch
# this dict).
SAFE_COMPANY_FOLLOW_SUMMARY_KEYS = {
    "total_followed_companies", "matched_followed_companies",
    "companies_with_recruiters_or_ta", "companies_with_opportunity_history",
    "recently_followed_companies", "contacts_resolved_via_company_follow",
    "contacts_still_needing_review", "companies_still_needing_review",
    "needs_mapping_before_company_follow_pass",
    "needs_mapping_after_company_follow_pass",
}
SAFE_COMPANY_FOLLOW_ROW_COLS = {
    "company_name", "company_follow_key", "followed_on", "days_since_followed",
    "matched_connection_count", "matched_recruiters", "matched_talent_acquisition",
    "matched_hiring_managers", "matched_data_leaders", "matched_top_contacts",
    "matched_untapped_contacts", "matched_lead_reactivation_contacts",
    "matched_opportunity_history_events", "matched_inbound_opportunities",
    "matched_soft_closed_leads", "matched_usd_crm_leads",
    "likely_company_category", "likely_opportunity_bucket",
    "follow_signal_confidence", "company_follow_reason", "signals",
}


def _sanitize_company_follow_rows(rows: list) -> list:
    return [{k: v for k, v in r.items() if k in SAFE_COMPANY_FOLLOW_ROW_COLS} for r in (rows or [])]


def build_company_follow_public(data: dict) -> dict:
    if not data or not data.get("available"):
        return {
            "available": False,
            "reason": (data or {}).get("reason", "Company Follows.csv not available"),
            "geography_note": (data or {}).get("geography_note", ""),
        }
    summary = data.get("summary", {}) or {}
    return {
        "available": True,
        "summary": {k: v for k, v in summary.items() if k in SAFE_COMPANY_FOLLOW_SUMMARY_KEYS},
        "companies": _sanitize_company_follow_rows(data.get("companies", [])),
        "needs_review": _sanitize_company_follow_rows(data.get("needs_review", [])),
        "mapping_candidates": _sanitize_company_follow_rows(data.get("mapping_candidates", [])),
        "geography_note": data.get("geography_note", ""),
    }


# ── USD Contract CRM (hybrid: manual + auto-suggested) — explicit allowlist,
# defense in depth on top of what src/usd_contract_crm.py already sanitizes
# (drops notes_private/raw content before it ever reaches this dict). Never
# emits notes_private, raw message content, email, or phone. Every row across
# every array — manual or auto-suggested — is normalized to this ONE field
# set (mirrors src/usd_contract_crm.py's PUBLIC_ROW_FIELDS).
SAFE_USD_CRM_SUMMARY_KEYS = {
    "manual_usd_opportunities", "auto_suggested_usd_leads",
    "recommended_recruiters_to_contact", "recommended_first_outreach",
    "recommended_followups", "active_interview_signals",
    "manual_applications_sent", "cv_requested_or_sent_signals",
    "recruiters_replied", "followups_due",
    "high_risk_manual_opportunities", "backup_manual_opportunities",
}
SAFE_USD_CRM_ROW_COLS = {
    "name", "company", "role", "persona", "opportunity_bucket", "source",
    "record_type", "status", "score", "priority", "recommended_action",
    "reason", "next_action", "next_action_date", "profile_url", "role_url",
    "currency", "rate_range", "remote_policy", "timezone_required",
    "timezone_risk", "payment_risk", "contract_risk",
}


def _sanitize_records(records: list, allowed: set) -> list:
    return [{k: v for k, v in r.items() if k in allowed} for r in (records or [])]


# ── Opportunity History (src/opportunity_history_engine.py) — explicit
# allowlist, defense in depth on top of what that module already sanitizes
# (it emits only booleans/scores/dates/short controlled-vocabulary labels —
# never raw message content, email, phone, or attachments). Mirrors
# EVENT_COLUMNS / MONTHLY_PIPELINE_COLUMNS there field-for-field.
SAFE_OPPORTUNITY_HISTORY_SUMMARY_KEYS = {
    "total_events", "total_conversations", "inbound_opportunities_total",
    "active_talent_pool_total", "salary_requested_total", "cv_requested_total",
    "calls_requested_total", "interviews_total", "client_submissions_total",
    "soft_closed_total", "hard_rejections_total", "location_blockers_total",
    "reactivation_due_now", "hot_opportunities_total", "warm_opportunities_total",
}
SAFE_OPPORTUNITY_EVENT_COLS = {
    "event_id", "conversation_id_hash", "contact_name", "company", "role",
    "persona", "profile_url", "event_month", "event_date",
    "opportunity_event_type", "opportunity_stage", "opportunity_signal_strength",
    "inbound_recruiter_contact", "active_talent_pool_signal",
    "salary_expectation_requested", "cv_requested", "application_requested",
    "interview_or_call_requested", "client_submission_signal",
    "technical_interview_signal", "rejected_or_closed", "soft_closed",
    "location_or_eligibility_blocked", "future_reactivation_candidate",
    "reactivation_date", "recommended_action", "message_angle",
    "tech_stack_signal", "opportunity_bucket", "usd_signal", "latam_signal",
    "remote_signal", "score", "reason_short",
}
SAFE_MONTHLY_PIPELINE_COLS = {
    "month", "inbound_opportunities", "active_talent_pool", "salary_requested",
    "cv_requested", "calls_requested", "interviews", "client_submissions",
    "soft_closed_keep_radar", "hard_rejections", "location_blockers",
    "reactivation_due", "hot_opportunities", "warm_opportunities",
}


def build_opportunity_history_public(oh_data: dict) -> dict:
    if not oh_data or not oh_data.get("available"):
        return {"available": False}
    summary = oh_data.get("summary", {}) or {}
    return {
        "available": True,
        "summary": {k: v for k, v in summary.items() if k in SAFE_OPPORTUNITY_HISTORY_SUMMARY_KEYS},
        "monthly_pipeline":         _sanitize_records(oh_data.get("monthly_pipeline"), SAFE_MONTHLY_PIPELINE_COLS),
        "events":                   _sanitize_records(oh_data.get("events"), SAFE_OPPORTUNITY_EVENT_COLS),
        "inbound_opportunities":    _sanitize_records(oh_data.get("inbound_opportunities"), SAFE_OPPORTUNITY_EVENT_COLS),
        "soft_closed_future_leads": _sanitize_records(oh_data.get("soft_closed_future_leads"), SAFE_OPPORTUNITY_EVENT_COLS),
        "reactivation_calendar":    _sanitize_records(oh_data.get("reactivation_calendar"), SAFE_OPPORTUNITY_EVENT_COLS),
    }


# ── Monthly Executive Queue (src/monthly_executive_queue.py) — explicit
# allowlist, defense in depth on top of what that module already sanitizes
# (booleans/scores/dates/short controlled-vocabulary labels + the 4 fixed
# message-angle templates with only a first name interpolated — never raw
# message content). Mirrors QUEUE_ROW_FIELDS there field-for-field.
SAFE_MONTHLY_QUEUE_SUMMARY_KEYS = {
    "inbound_opportunities_this_month", "reactivation_due_this_month",
    "soft_closed_keep_warm", "usd_recruiter_followups", "monthly_backlog",
    "high_priority_this_month", "overdue_reactivations",
    "active_opportunity_signals",
}
SAFE_MONTHLY_QUEUE_ROW_COLS = {
    "queue_name", "rank", "contact_name", "company", "role", "persona",
    "profile_url", "event_month", "event_date", "last_contact_date",
    "opportunity_event_type", "opportunity_stage", "opportunity_signal_strength",
    "opportunity_bucket", "usd_signal", "latam_signal", "remote_signal",
    "score", "priority", "recommended_action", "next_action_date",
    "reason_short", "message_angle",
}
SAFE_MONTHLY_CHART_COLS = {
    "month", "inbound_opportunities", "reactivation_due", "soft_closed", "usd_followups",
}


def build_monthly_executive_queue_public(meq_data: dict) -> dict:
    if not meq_data or not meq_data.get("available"):
        return {"available": False}
    summary = meq_data.get("summary", {}) or {}
    return {
        "available": True,
        "summary": {k: v for k, v in summary.items() if k in SAFE_MONTHLY_QUEUE_SUMMARY_KEYS},
        "inbound_top20":            _sanitize_records(meq_data.get("inbound_top20"), SAFE_MONTHLY_QUEUE_ROW_COLS),
        "reactivation_top20":        _sanitize_records(meq_data.get("reactivation_top20"), SAFE_MONTHLY_QUEUE_ROW_COLS),
        "soft_closed_top20":         _sanitize_records(meq_data.get("soft_closed_top20"), SAFE_MONTHLY_QUEUE_ROW_COLS),
        "usd_followups_top20":       _sanitize_records(meq_data.get("usd_followups_top20"), SAFE_MONTHLY_QUEUE_ROW_COLS),
        "monthly_backlog_top50":     _sanitize_records(meq_data.get("monthly_backlog_top50"), SAFE_MONTHLY_QUEUE_ROW_COLS),
        "all_monthly_queue_records": _sanitize_records(meq_data.get("all_monthly_queue_records"), SAFE_MONTHLY_QUEUE_ROW_COLS),
        "monthly_chart":             _sanitize_records(meq_data.get("monthly_chart"), SAFE_MONTHLY_CHART_COLS),
    }


def build_usd_contract_crm_public(usd_crm_data: dict) -> dict:
    if not usd_crm_data or not usd_crm_data.get("available"):
        return {"available": False}
    summary = usd_crm_data.get("summary", {}) or {}
    risk = usd_crm_data.get("contingency_risk", {}) or {}
    return {
        "available": True,
        "summary": {k: v for k, v in summary.items() if k in SAFE_USD_CRM_SUMMARY_KEYS},
        "manual_opportunities":     _sanitize_records(usd_crm_data.get("manual_opportunities"), SAFE_USD_CRM_ROW_COLS),
        "auto_suggested_usd_leads": _sanitize_records(usd_crm_data.get("auto_suggested_usd_leads"), SAFE_USD_CRM_ROW_COLS),
        "recruiter_pipeline":       _sanitize_records(usd_crm_data.get("recruiter_pipeline"), SAFE_USD_CRM_ROW_COLS),
        "first_outreach_queue":     _sanitize_records(usd_crm_data.get("first_outreach_queue"), SAFE_USD_CRM_ROW_COLS),
        "follow_up_queue":          _sanitize_records(usd_crm_data.get("follow_up_queue"), SAFE_USD_CRM_ROW_COLS),
        "active_process_pipeline":  _sanitize_records(usd_crm_data.get("active_process_pipeline"), SAFE_USD_CRM_ROW_COLS),
        "manual_applications":      _sanitize_records(usd_crm_data.get("manual_applications"), SAFE_USD_CRM_ROW_COLS),
        "contingency_risk": {
            "high_risk": _sanitize_records(risk.get("high_risk"), SAFE_USD_CRM_ROW_COLS),
            "backup":    _sanitize_records(risk.get("backup"), SAFE_USD_CRM_ROW_COLS),
        },
        # New section (this update): Opportunity History & Monthly Pipeline —
        # see src/opportunity_history_engine.py. Distinct from A/B above;
        # never merged into their counts.
        "opportunity_history": build_opportunity_history_public(usd_crm_data.get("opportunity_history")),
        # New section (this update): Monthly Executive Queue — curated top-20
        # execution lists, see src/monthly_executive_queue.py. Execution
        # queues, not confirmed applications; never merged into A/B's counts.
        "monthly_executive_queue": build_monthly_executive_queue_public(usd_crm_data.get("monthly_executive_queue")),
    }


SAFE_ACTION_PLAN_SUMMARY_KEYS = {
    "backlog_size", "high_value_unresolved", "recruiters_unresolved",
    "biggest_impact_companies", "auto_resolvable_share_pct",
    "estimated_weekly_reduction_potential",
}
SAFE_ACTION_PLAN_QUEUE_COLS = {"priority", "segment", "target", "company", "action"}


def build_needs_mapping_action_plan_public(plan: dict) -> dict:
    if not plan or not plan.get("available"):
        return {"available": False}
    exec_summary = plan.get("executive_summary", {}) or {}
    queue = [
        {k: v for k, v in q.items() if k in SAFE_ACTION_PLAN_QUEUE_COLS}
        for q in (plan.get("weekly_queue") or [])
    ]
    return {
        "available": True,
        "executive_summary": {k: v for k, v in exec_summary.items() if k in SAFE_ACTION_PLAN_SUMMARY_KEYS},
        "weekly_queue": queue,
        "next_actions": [str(a) for a in (plan.get("next_actions") or [])],
        "recommendation": str(plan.get("recommendation", "")),
    }


# ══════════════════════════════════════════════════════════════════════════
# Data Payload Optimization V1 — splits the full sanitized payload into a
# lightweight manifest (docs/assets/dashboard_data.json) plus per-page JSON
# files (docs/assets/data/<page>.json), loaded on demand by app.js.
#
# This is called from generate_static_dashboard.py, NOT from
# export_public_dashboard_data() below — weekly_kpi_delta.py and
# action_plan_progress.py both enrich outputs/public_dashboard_data.json
# in place AFTER this module runs (adding weekly_evolution,
# weekly_people_delta_segments, action_plan_progress, weekly_history), so the
# split must happen only once that full enrichment pipeline has finished,
# reading the final outputs/public_dashboard_data.json. export_public_dashboard_data()
# itself still writes the full (unsplit) payload to both
# outputs/public_dashboard_data.json (kept as a compatibility artifact) and
# docs/assets/dashboard_data.json (a placeholder, immediately overwritten by
# the split manifest at the end of the pipeline).
# ══════════════════════════════════════════════════════════════════════════

# pageId -> nav data-page attribute in docs/index.html; also the key used in
# PAGE_DATA_MAP / PAGE_FILE_NAME / PAGE_DEPENDENCIES below.
PAGE_DATA_MAP = {
    "overview": [
        "opportunity_market_v5", "opportunity_market_v5_summary",
        "persona_distribution", "market_distribution",
        "lead_reactivation_summary", "untapped_network_summary",
    ],
    "heatmap": ["heatmaps"],
    "gap": ["gap_analysis", "strategic_gap_people_drilldown"],
    "plan": ["action_plan_30", "action_plan_60", "action_plan_90", "action_plan_progress"],
    "contacts": ["top_contacts"],
    "weekly": ["weekly_evolution", "weekly_history", "weekly_people_delta_segments"],
    "companies": ["company_intel", "company_follow_intelligence"],
    "unknown": [
        "opportunity_market_v5", "opportunity_market_v5_summary",
        "opportunity_market_people_segments", "needs_mapping_backlog",
        "needs_mapping_action_plan", "company_resolution_v6", "company_resolution_v7",
        "unknown_companies", "unknown_resolution",
    ],
    "leads": ["lead_reactivation"],
    "untapped": ["untapped_network"],
    "usdcrm": ["usd_contract_crm"],
    "quality": [
        "opportunity_market_v5", "opportunity_market_v5_summary",
        "company_resolution_v6", "company_resolution_v7",
        "untapped_network_summary", "outreach_summary",
        "company_follow_intelligence_summary",
    ],
}

PAGE_FILE_NAME = {
    "overview":  "executive_overview.json",
    "heatmap":   "network_heatmap.json",
    "gap":       "strategic_gap.json",
    "plan":      "action_plan.json",
    "contacts":  "top_contacts.json",
    "weekly":    "weekly_evolution.json",
    "companies": "company_intel.json",
    "unknown":   "opportunity_market.json",
    "leads":     "lead_reactivation.json",
    "untapped":  "untapped_network.json",
    "usdcrm":    "usd_contract_crm.json",
    "quality":   "data_quality.json",
}

# Pages whose full functionality needs ANOTHER page's data too (kept
# separate rather than duplicating a multi-MB array into both files) — e.g.
# Strategic Gap's "Recommended People to Activate Next" tab reads the same
# never-contacted population as the Untapped Network page. The frontend
# loader fetches these dependency files before rendering the page.
PAGE_DEPENDENCIES = {
    "gap": ["untapped"],
}

# Keys that ALWAYS stay in the lightweight manifest — needed cross-page
# (the kpi() helper, meta/untapped_scoring_note) and tiny (a few KB).
MANIFEST_KEYS = ["meta", "kpis"]


def _build_lead_reactivation_summary(lead_reactivation: dict) -> dict:
    """Tiny, scalar-only extract for Executive Overview's Lead Reactivation
    KPI row — avoids Overview needing the full multi-MB lead_reactivation.json
    just to show six counts."""
    lr = lead_reactivation or {}
    fields = [
        "messages_csv_available", "total_conversations", "this_week_count",
        "needs_my_response", "hot_reactivation_leads", "hot_leads",
        "warm_reactivation_leads", "warm_leads", "career_site_follow_ups",
        "follow_up_due",
    ]
    return {k: lr[k] for k in fields if k in lr}


def _build_untapped_network_summary(untapped_network: dict) -> dict:
    """Tiny extract (summary counts + match-method breakdown, no per-contact
    arrays) for Executive Overview and Data Quality — avoids either needing
    the full multi-MB untapped_network.json just for a handful of numbers."""
    un = untapped_network or {}
    if not un.get("available"):
        return {"available": False}
    return {
        "available": True,
        "summary": un.get("summary", {}),
        "match_method_breakdown": un.get("match_method_breakdown", {}),
    }


def _build_company_follow_summary(company_follow_intelligence: dict) -> dict:
    """Tiny extract (summary counts only, no per-company arrays) for the Data
    Quality page — avoids Quality needing the full company_follow_intelligence
    array just to show the company-follow contribution numbers."""
    cf = company_follow_intelligence or {}
    if not cf.get("available"):
        return {"available": False, "reason": cf.get("reason", "")}
    return {
        "available": True,
        "summary": cf.get("summary", {}),
        "geography_note": cf.get("geography_note", ""),
    }


def split_payload_into_pages(payload: dict) -> tuple[dict, dict]:
    """
    Splits a full sanitized dashboard payload into:
      - manifest: the lightweight dict written to docs/assets/dashboard_data.json
      - pages:    {pageId: page_dict} written to docs/assets/data/<file>.json

    Every top-level payload key referenced anywhere in PAGE_DATA_MAP /
    MANIFEST_KEYS ends up in the manifest and/or at least one page; a key
    that belongs to no page (should not happen — every payload key above is
    accounted for) is simply never emitted, never silently duplicated.
    """
    payload = dict(payload)  # shallow copy — never mutate the caller's dict
    payload["lead_reactivation_summary"] = _build_lead_reactivation_summary(payload.get("lead_reactivation"))
    payload["untapped_network_summary"]  = _build_untapped_network_summary(payload.get("untapped_network"))
    payload["company_follow_intelligence_summary"] = _build_company_follow_summary(payload.get("company_follow_intelligence"))

    pages = {
        page_id: {k: payload[k] for k in keys if k in payload}
        for page_id, keys in PAGE_DATA_MAP.items()
    }
    manifest = {k: payload[k] for k in MANIFEST_KEYS if k in payload}
    return manifest, pages


def write_split_dashboard_data(payload: dict) -> dict:
    """
    Writes the lightweight manifest to docs/assets/dashboard_data.json and
    each page's data to docs/assets/data/<file>.json.

    Returns a size report dict (Data Payload Optimization V1, Part 5):
      {"manifest_kb": int, "pages": {pageId: {"file", "kb"}},
       "total_data_kb": int, "largest_page": (pageId, kb) | (None, 0)}
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest, pages = split_payload_into_pages(payload)

    page_manifest = {}
    page_sizes = {}
    for page_id, page_payload in pages.items():
        filename = PAGE_FILE_NAME[page_id]
        path = DATA_DIR / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(page_payload, f, ensure_ascii=False, default=str, indent=2)
        size_kb = path.stat().st_size // 1024
        page_sizes[page_id] = size_kb
        page_manifest[page_id] = {
            "file": "data/" + filename,
            "size_kb": size_kb,
            "available": bool(page_payload),
            "dependencies": PAGE_DEPENDENCIES.get(page_id, []),
        }

    manifest["page_manifest"] = page_manifest
    manifest["build"] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema": "split-v1",
    }

    with open(PUBLIC_JSON_DOCS, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, default=str, indent=2)
    manifest_kb = PUBLIC_JSON_DOCS.stat().st_size // 1024

    total_data_kb = sum(page_sizes.values())
    largest = max(page_sizes.items(), key=lambda kv: kv[1]) if page_sizes else (None, 0)

    return {
        "manifest_kb": manifest_kb,
        "pages": {pid: {"file": PAGE_FILE_NAME[pid], "kb": kb} for pid, kb in page_sizes.items()},
        "total_data_kb": total_data_kb,
        "largest_page": largest,
    }


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
    company_resolution_v6_data: dict = None,
    company_resolution_v7_data: dict = None,
    untapped_network_data: dict = None,
    needs_mapping_backlog_data: dict = None,
    needs_mapping_action_plan_data: dict = None,
    usd_crm_data: dict = None,
    company_follow_data: dict = None,
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

    # Untapped Outreach Scoring V9 — index by normalized profile URL so Top
    # Contacts can merge in untapped_outreach_score/untapped_reason/etc.
    untapped_scores = {}
    if untapped_network_data and untapped_network_data.get("available"):
        for c in untapped_network_data.get("top_untapped_contacts", []):
            u = str(c.get("profile_url", "") or "").strip().rstrip("/").lower()
            if u:
                untapped_scores[u] = c

    # Needs Mapping backlog (Part 4) — index by normalized profile URL so Top
    # Contacts can merge in mapping_priority_score/mapping_reason_short for
    # cross-navigation from Opportunity Market's person drill-down.
    needs_mapping_scores = {}
    if needs_mapping_backlog_data and needs_mapping_backlog_data.get("available"):
        for p in needs_mapping_backlog_data.get("people", []):
            u = str(p.get("profile_url", "") or "").strip().rstrip("/").lower()
            if u:
                needs_mapping_scores[u] = p

    # Strategic Gap people drill-down (Tab A: "Current Contacts Counted") —
    # Tab B ("New This Week") rows are appended in-place by weekly_kpi_delta.py.
    gap_drilldown = build_strategic_gap_people_drilldown(
        df, gap_df, outreach_scores=outreach_scores, untapped_scores=untapped_scores,
    )
    if gap_drilldown:
        pd.DataFrame(gap_drilldown).to_csv(
            OUTPUTS_DIR / "strategic_gap_people_drilldown.csv", index=False,
        )

    # Opportunity Market full-population person segments (Part 5/6)
    opportunity_market_segments = build_opportunity_market_people_segments(df)
    if opportunity_market_segments:
        pd.DataFrame(opportunity_market_segments).to_csv(
            OUTPUTS_DIR / "opportunity_market_people_segments.csv", index=False,
        )

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
            "untapped_scoring_note": (
                "LinkedIn export does not include location, but opportunity market can "
                "still be inferred from company, title, persona, language, manual "
                "enrichment, and message history. These are opportunity signals, not "
                "exact geography."
            ),
        },
        "kpis":               kpis,
        "market_distribution":build_public_market_distribution(df),
        "persona_distribution":build_public_persona_distribution(df),
        "heatmaps":           build_public_heatmap(df),
        "gap_analysis":       build_public_gap_data(gap_df),
        # Strategic Gap people drill-down (Part 3) — segment="current" rows
        # here; weekly_kpi_delta.py appends segment="new_this_week" rows.
        "strategic_gap_people_drilldown": gap_drilldown,
        "action_plan_30":     _enrich_plan_with_search_pack(plan_30.to_dict(orient="records")),
        "action_plan_60":     _enrich_plan_with_search_pack(plan_60.to_dict(orient="records")),
        "action_plan_90":     _enrich_plan_with_search_pack(plan_90.to_dict(orient="records")),
        "top_contacts":                build_public_contacts(df, n=200, outreach_scores=outreach_scores, untapped_scores=untapped_scores, needs_mapping_scores=needs_mapping_scores),
        # Full-network outreach status aggregate (sanitized counts only) — persisted
        # so next week's weekly_kpi_delta.py can compute a real Interview Pipeline /
        # ghosted / replied delta instead of only ever seeing the top-200 slice.
        "outreach_summary": {
            "total_scored":            len(outreach_scores or {}),
            "ghosted_count":           sum(1 for v in (outreach_scores or {}).values() if v.get("ghosted_me")),
            "replied_count":           sum(1 for v in (outreach_scores or {}).values() if v.get("replied_to_me")),
            "interview_pipeline_count":sum(1 for v in (outreach_scores or {}).values() if v.get("outreach_status") == "Interview Pipeline"),
        },
        "company_intel":               build_public_company_intel(df),
        "unknown_companies":           build_unknown_companies_public(df),
        "unknown_resolution":          resolution_data or {},
        "lead_reactivation":           lead_section,
        # V5 Opportunity Market (replaces UNKNOWN as primary business view)
        "opportunity_market_v5":       build_v5_distribution_public(df),
        "opportunity_market_v5_summary": v5_data or {},
        # Opportunity Market full-population person segments (Part 5/6) —
        # backs every clickable card on the Opportunity Market page, grouped
        # client-side by market_segment/company_clean as needed.
        "opportunity_market_people_segments": opportunity_market_segments,
        # V6 Company Resolution — cross-contact/message/persona mapping improvements
        "company_resolution_v6":       company_resolution_v6_data or {},
        # V7 Company Resolution — iterative fuzzy-cluster/company-message-aggregate/
        # persona-cohort mapping improvements (sanitized aggregate counts only)
        "company_resolution_v7":       company_resolution_v7_data or {},
        # Untapped Network Intelligence — 1st-degree connections with no
        # conversation history (separate from Lead Reactivation). Sanitized:
        # no raw messages, no email, no phone.
        "untapped_network":            build_untapped_network_public(untapped_network_data),
        # Opportunity Market — Needs Mapping person/company drill-down (Parts 2-4)
        "needs_mapping_backlog":       build_needs_mapping_public(needs_mapping_backlog_data),
        "needs_mapping_action_plan":   build_needs_mapping_action_plan_public(needs_mapping_action_plan_data),
        # Company Follow Intelligence — Company Follows.csv used as an
        # additional company-relevance/opportunity-market signal. Never
        # fabricates exact geography (see geography_note in the payload).
        "company_follow_intelligence": build_company_follow_public(company_follow_data),
        # USD Contract CRM — real USD job opportunities, recruiter outreach,
        # applications, interviews, follow-ups, contingency risk. Local/manual
        # CSV inputs only (data/manual/*.csv, gitignored). Sanitized: no
        # notes_private, no raw message content, no email, no phone.
        "usd_contract_crm":            build_usd_contract_crm_public(usd_crm_data),
        # Action Plan Progress — measurement layer for the Action Plan page.
        # Placeholder here (this function runs before the weekly delta layer
        # exists); src/action_plan_progress.py merges the real sanitized
        # block in afterward, same pattern as weekly_evolution below.
        "action_plan_progress":        {"available": False},
        # Weekly Evolution multi-week history (Part 3) — placeholder here;
        # src/action_plan_progress.py merges the real sanitized array in
        # afterward (same pattern as action_plan_progress above).
        "weekly_history":              [],
    }

    for path in [PUBLIC_JSON_DOCS, PUBLIC_JSON_OUTPUTS]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str, indent=2)
        logger.info(f"Public dashboard JSON saved: {path.name} "
                    f"({path.stat().st_size // 1024} KB)")
