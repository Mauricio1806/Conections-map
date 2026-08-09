# -*- coding: utf-8 -*-
"""
company_mapping_workbench.py
=============================
Company Mapping Workbench — a ranked, weekly manual-mapping worklist that
synthesizes every safe signal already computed this pipeline run (Company
Follow Intelligence, Opportunity History, USD Contract CRM, Untapped
Network, Lead Reactivation, Top Contacts) into ONE table per still-unmapped
company, so a human can clear the Needs Company Mapping backlog in bulk on
a Sunday instead of one contact at a time.

Policy (read before touching the classification logic):
  - This module NEVER writes to config/company_market_overrides.yml. It only
    writes outputs/company_mapping_yaml_suggestions.yml — a copy-paste-ready
    suggestions file. A human always reviews before pasting anything into
    the real overrides file (manual_review_required is always True here).
  - A company is never classified into a market bucket "because it is
    followed" — Company Follow Intelligence's own likely_opportunity_bucket
    is only ever non-empty when IT already found a keyword/dictionary
    signal (see company_follow_intelligence.py), never from the follow
    itself. Being followed is used here only as a PRIORITY/ranking signal.
  - No exact geography is fabricated. A company with no keyword/dictionary/
    persona-cohort signal at all stays "NEEDS_COMPANY_MAPPING" with
    confidence 0.0 — it is still ranked (by network signal strength) so a
    human knows what to research, but never auto-classified.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
import yaml

from src.company_normalizer import normalize as normalize_company
from src.company_dictionary_enrichment import NEEDS_MAPPING

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
PRIVATE_DIR = OUTPUTS_DIR / "private"
CONFIG_DIR = ROOT / "config"
OVERRIDES_YML = CONFIG_DIR / "company_market_overrides.yml"

RECRUITING_PERSONAS  = {"Recruiter"}
TA_PERSONAS           = {"Talent Acquisition", "Sourcer"}
HIRING_PERSONAS       = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS  = {"Data Engineering Manager", "Head of Data", "Director"}

GEOGRAPHY_NOTE = (
    "The Workbench ranks companies by mapping impact and suggests a bucket only when a "
    "keyword, dictionary, or persona/message signal supports it. LinkedIn exports carry no "
    "exact location — suggestions are relevance signals, not confirmed geography, and every "
    "suggestion needs human review before being pasted into company_market_overrides.yml."
)

# ── Suggested-bucket keyword rules (Part: Suggested bucket rules) ───────────
STAFFING_KW = re.compile(
    r"recruiting|staffing|\btalent\b|headhunter|recruitment|talent solutions|\brpo\b|"
    r"hiring agency|latam talent|nearshore recruiting",
    re.IGNORECASE,
)
# "consulting"/"transformation"/"advisory" are reasonably distinctive company-name
# words; bare "solutions" is extremely generic (half the tech industry uses it),
# so it's checked separately at lower confidence rather than folded into the
# same high-confidence tier.
CONSULTING_STRONG_KW = re.compile(
    r"technology consulting|digital consulting|it consulting|transformation|advisory|\bconsulting\b",
    re.IGNORECASE,
)
CONSULTING_WEAK_KW = re.compile(r"\bsolutions\b", re.IGNORECASE)

LATAM_KW = re.compile(
    r"\blatam\b|latin america|am[ée]rica latina|south america|nearshore|remote latam",
    re.IGNORECASE,
)
US_CANADA_KW = re.compile(
    r"\bu\.s\.\b|\busa\b|united states|\bcanada\b|north america|"
    r"us corporate recruiter|us staffing|us client",
    re.IGNORECASE,
)
SPAIN_KW = re.compile(r"\bspain\b|\bmadrid\b|\bbarcelona\b", re.IGNORECASE)
EUROPE_KW = re.compile(
    r"\bportugal\b|\bgermany\b|\bnetherlands\b|\bireland\b|\beurope\b|\beu\b|\blisbon\b|\bberlin\b",
    re.IGNORECASE,
)

# suggested_bucket (V5-style, dashboard-facing) -> the coarse market name
# config/company_market_overrides.yml / opportunity_market_v5.V4_TO_V5 expect.
_BUCKET_TO_OVERRIDE_MARKET = {
    "LATAM_USD_CONFIRMED": "LATAM_USD", "LATAM_USD_LIKELY": "LATAM_USD",
    "US_CANADA_CONFIRMED": "US_CANADA_NEARSHORE", "US_CANADA_LIKELY": "US_CANADA_NEARSHORE",
    "SPAIN_EU_CONFIRMED": "SPAIN_EU", "EUROPE_LIKELY": "EUROPE",
    "BRAZIL_CONFIRMED": "BRAZIL", "BRAZIL_LIKELY": "BRAZIL",
    "GLOBAL_STAFFING": "GLOBAL_STAFFING", "GLOBAL_CONSULTING": "GLOBAL_CONSULTING",
    "GLOBAL_TECH": "GLOBAL_TECH",
    "GLOBAL_OPPORTUNITY": "GLOBAL_OPPORTUNITY_UNRESOLVED_REGION",
}


def _safe_read_csv(path: Path, cols: list) -> pd.DataFrame:
    try:
        if path.exists():
            # keep_default_na=False: pandas otherwise turns an EMPTY string
            # cell into a float NaN even under dtype=str. NaN is truthy in
            # Python, so a later `value or ""` guard silently lets it through
            # instead of falling back to "" — and json.dump() (allow_nan=True
            # by default) then writes the literal (invalid) token `NaN` into
            # the public JSON. Reading empty cells as "" avoids the whole class
            # of bug at the source instead of chasing it downstream.
            return pd.read_csv(path, dtype=str, low_memory=False, encoding="utf-8-sig", keep_default_na=False)
    except Exception as exc:
        logger.warning(f"  Company Mapping Workbench: could not read {path.name}: {exc}")
    return pd.DataFrame(columns=cols)


def _norm_key_col(frame: pd.DataFrame, col: str) -> pd.Series:
    if frame.empty or col not in frame.columns:
        return pd.Series(dtype=str)
    return frame[col].fillna("").apply(normalize_company)


def _existing_overrides() -> set:
    """Companies already present in config/company_market_overrides.yml — used
    only to avoid suggesting a company that's already there (defense in depth;
    by construction candidates are already-unresolved so this should rarely fire)."""
    if not OVERRIDES_YML.exists():
        return set()
    try:
        raw = yaml.safe_load(OVERRIDES_YML.read_text(encoding="utf-8")) or {}
        entries = raw.get("overrides", raw) if isinstance(raw, dict) else {}
        return {str(k).strip().lower() for k in (entries or {}).keys()}
    except Exception:
        return set()


def _classify_candidate(
    company_name: str,
    follow_bucket: str, follow_category: str, follow_confidence: float,
    has_recruiter_ta_signal: bool, has_opportunity_history_signal: bool,
    latam_signal_present: bool, usd_signal_present: bool,
    existing_company_category: str,
) -> dict:
    """Never fabricates geography — only returns a non-empty bucket when a
    keyword, dictionary (via the inherited follow classification), or
    persona/message-history signal actually supports it."""
    text = company_name or ""
    signals = {
        "global_staffing_signal": bool(STAFFING_KW.search(text)),
        "global_consulting_signal": bool(CONSULTING_STRONG_KW.search(text) or CONSULTING_WEAK_KW.search(text)),
        "latam_signal": bool(LATAM_KW.search(text)) or latam_signal_present,
        "us_canada_signal": bool(US_CANADA_KW.search(text)) or usd_signal_present,
        "spain_eu_signal": bool(SPAIN_KW.search(text)),
        "europe_signal": bool(EUROPE_KW.search(text)),
    }

    bucket, category, confidence = "", "", 0.0
    reasons: list[str] = []

    # 1. Inherit Company Follow Intelligence's own classification (already
    # keyword/dictionary-backed there — never "because it's followed alone").
    if follow_bucket:
        bucket, category, confidence = follow_bucket, follow_category, follow_confidence
        reasons.append(f"followed-company classification ({follow_category or follow_bucket})")

    # 2. Direct region keyword in the company's own name — CONFIRMED tier.
    if SPAIN_KW.search(text):
        bucket, confidence = "SPAIN_EU_CONFIRMED", max(confidence, 0.85)
        reasons.append("Spain keyword in company name")
    elif EUROPE_KW.search(text):
        bucket, confidence = "EUROPE_LIKELY", max(confidence, 0.80)
        reasons.append("Europe keyword in company name")
    elif LATAM_KW.search(text):
        bucket, confidence = "LATAM_USD_CONFIRMED", max(confidence, 0.85)
        reasons.append("LATAM keyword in company name")
    elif US_CANADA_KW.search(text):
        bucket, confidence = "US_CANADA_CONFIRMED", max(confidence, 0.85)
        reasons.append("US/Canada keyword in company name")
    elif not bucket and latam_signal_present:
        bucket, confidence = "LATAM_USD_LIKELY", max(confidence, 0.70)
        reasons.append("LATAM signal from message/opportunity history")
    elif not bucket and usd_signal_present:
        bucket, confidence = "US_CANADA_LIKELY", max(confidence, 0.70)
        reasons.append("USD/US signal from message/opportunity history")

    # 3. Category keywords (staffing/consulting) — independent axis; only
    # sets the bucket itself if no region was resolved above.
    if STAFFING_KW.search(text):
        category = category or "GLOBAL_STAFFING"
        if not bucket:
            bucket, confidence = "GLOBAL_STAFFING", max(confidence, 0.80)
            reasons.append("staffing/recruiting keyword in company name")
    elif CONSULTING_STRONG_KW.search(text):
        category = category or "GLOBAL_CONSULTING"
        if not bucket:
            bucket, confidence = "GLOBAL_OPPORTUNITY", max(confidence, 0.65)
            reasons.append("consulting/advisory keyword in company name")
    elif CONSULTING_WEAK_KW.search(text):
        category = category or "GLOBAL_CONSULTING"
        if not bucket:
            bucket, confidence = "GLOBAL_OPPORTUNITY", max(confidence, 0.55)
            reasons.append("generic 'solutions' keyword in company name (weak signal)")

    # 4. Existing company_category from earlier pipeline stages (V2/V4) —
    # useful residual signal when nothing else fired.
    if not bucket and existing_company_category and existing_company_category not in ("OTHER", ""):
        bucket = {"GLOBAL_STAFFING": "GLOBAL_STAFFING", "GLOBAL_TECH": "GLOBAL_TECH",
                  "GLOBAL_CONSULTING": "GLOBAL_CONSULTING"}.get(existing_company_category, "GLOBAL_OPPORTUNITY")
        confidence = max(confidence, 0.55)
        reasons.append(f"existing company category ({existing_company_category})")

    # 5. Strong recruiter/TA or opportunity-history signal, region/category
    # still unresolved -> GLOBAL_OPPORTUNITY (real value, unresolved region).
    if not bucket and (has_recruiter_ta_signal or has_opportunity_history_signal):
        bucket, confidence = "GLOBAL_OPPORTUNITY", max(confidence, 0.55)
        reasons.append("recruiter/TA or opportunity-history signal present, region unresolved")

    # 6. Nothing at all — never fabricate, stays unresolved.
    if not bucket:
        bucket = NEEDS_MAPPING
        confidence = 0.0
        reasons.append("no keyword, dictionary, persona, or message signal found — needs manual research")

    return {
        "suggested_bucket": bucket,
        "suggested_category": category,
        "confidence": round(min(confidence, 0.90), 2),  # 0.90 cap: only a human-applied
                                                          # override reaches the 0.95 "manual" tier
        "reason_short": "; ".join(reasons),
        **signals,
    }


def _yaml_suggestion(raw_company_names: list, bucket: str, category: str) -> str:
    """One override line per DISTINCT raw company_clean spelling in the
    group (newline-joined). opportunity_market_v5._classify_row() matches
    overrides against company_clean.strip().lower() EXACTLY — not the
    suffix-stripped normalized form used for grouping/ranking above — so a
    suggestion keyed by the normalized key (e.g. "elevation") would silently
    never match "Elevation Group"'s raw company_clean. Every real spelling
    that fed this candidate must get its own line for the paste to work."""
    if not bucket or bucket == NEEDS_MAPPING:
        return ""
    market = _BUCKET_TO_OVERRIDE_MARKET.get(bucket, "")
    if not market:
        return ""
    cat = category or "GLOBAL_OPPORTUNITY"
    lines = []
    for name in raw_company_names:
        key = str(name).strip().lower().replace('"', "'")
        if key:
            lines.append(f'  "{key}": {{market: {market}, category: {cat}}}')
    return "\n".join(lines)


def run_company_mapping_workbench(df: pd.DataFrame) -> dict:
    """Main entry point. `df` is the fully-enriched connections frame, post
    V5/V6/V7 and Company Follow Intelligence Stage A (so opportunity_market_v5
    already reflects every honest resolution this run made). Reads the
    already-written CSVs from Company Follow Intelligence Stage B,
    Opportunity History, USD Contract CRM, Lead Reactivation, and Untapped
    Network — must run AFTER all of those this pipeline pass."""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    if "opportunity_market_v5" not in df.columns or "company_clean" not in df.columns:
        logger.warning("  Company Mapping Workbench: required columns missing — skipping")
        _write_empty_outputs()
        return {"available": False, "reason": "opportunity_market_v5/company_clean columns missing"}

    needs_df = df[df["opportunity_market_v5"] == NEEDS_MAPPING].copy()
    if needs_df.empty:
        logger.info("  Company Mapping Workbench: Needs Company Mapping backlog is empty — nothing to rank.")
        _write_empty_outputs()
        return {"available": True, "summary": {"candidate_count": 0, "needs_mapping_total": 0}, "companies": [],
                "geography_note": GEOGRAPHY_NOTE}

    needs_df["company_norm"] = needs_df["company_clean"].fillna("").apply(normalize_company)
    needs_df = needs_df[needs_df["company_norm"] != ""]

    persona_all = df.get("persona", pd.Series(index=df.index, dtype=object)).fillna("")
    company_norm_all = df.get("company_clean", pd.Series(index=df.index, dtype=object)).fillna("").apply(normalize_company)
    category_all = df.get("company_category", pd.Series(index=df.index, dtype=object)).fillna("")
    if "priority_score" in df.columns:
        pri = pd.to_numeric(df["priority_score"], errors="coerce").fillna(0)
        top_idx = pri.sort_values(ascending=False).head(200).index
    else:
        top_idx = pd.Index([])

    # ── Load already-computed signal sources (Part: Inputs) ──────────────────
    follow_intel = _safe_read_csv(OUTPUTS_DIR / "company_follow_intelligence.csv",
                                   ["company_name", "company_follow_key", "likely_opportunity_bucket"])
    follow_matches = _safe_read_csv(OUTPUTS_DIR / "company_follow_company_matches.csv",
                                     ["company_name", "matched_connection_company_keys"])
    events = _safe_read_csv(OUTPUTS_DIR / "opportunity_history_events.csv",
                             ["company", "usd_signal", "latam_signal"])
    inbound = _safe_read_csv(OUTPUTS_DIR / "inbound_opportunities.csv", ["company"])
    soft_closed = _safe_read_csv(OUTPUTS_DIR / "soft_closed_future_leads.csv", ["company"])
    usd_leads = _safe_read_csv(OUTPUTS_DIR / "usd_follow_up_queue.csv", ["company"])
    untapped = _safe_read_csv(PRIVATE_DIR / "untapped_outreach_backlog.csv",
                               ["company_clean", "is_high_value_untapped", "is_latam_primary"])
    msgs = _safe_read_csv(OUTPUTS_DIR / "message_threads_summary.csv", ["company_clean"])

    # Invert follow_matches: connection_norm_key -> best followed-company row
    follow_key_to_bucket = {}
    if not follow_intel.empty:
        for _, r in follow_intel.iterrows():
            follow_key_to_bucket[r.get("company_follow_key", "")] = r

    conn_key_to_follow = {}
    if not follow_matches.empty:
        for _, r in follow_matches.iterrows():
            keys = str(r.get("matched_connection_company_keys", "") or "")
            if not keys:
                continue
            follow_row = follow_key_to_bucket.get(r.get("company_follow_key", ""))
            if follow_row is None:
                follow_row = {}
            days_since = pd.to_numeric(r.get("days_since_followed", -1), errors="coerce")
            days_since = int(days_since) if pd.notna(days_since) else -1
            for k in keys.split("; "):
                if not k:
                    continue
                prev = conn_key_to_follow.get(k)
                conf = pd.to_numeric(follow_row.get("follow_signal_confidence", 0), errors="coerce")
                conf = float(conf) if pd.notna(conf) else 0.0
                if prev is None or conf > prev["confidence"]:
                    conn_key_to_follow[k] = {
                        "bucket": follow_row.get("likely_opportunity_bucket", "") or "",
                        "category": follow_row.get("likely_company_category", "") or "",
                        "confidence": conf,
                        "days_since_followed": days_since,
                    }

    events_key = _norm_key_col(events, "company")
    inbound_key = _norm_key_col(inbound, "company")
    soft_closed_key = _norm_key_col(soft_closed, "company")
    usd_key = _norm_key_col(usd_leads, "company")
    untapped_key = _norm_key_col(untapped, "company_clean")
    msgs_key = _norm_key_col(msgs, "company_clean")

    # ── Aggregate per candidate company ───────────────────────────────────────
    grouped = needs_df.groupby("company_norm")
    rows = []
    existing_overrides = _existing_overrides()

    for company_norm, group in grouped:
        if company_norm in existing_overrides:
            # Already has a manual override — the NEXT run's V5 pass will
            # resolve it; don't suggest re-mapping what's already mapped.
            continue

        raw_names = group["company_clean"].dropna().unique().tolist()
        # Most frequent raw spelling as the display name; ALL distinct raw
        # spellings get their own line in yaml_suggestion (see _yaml_suggestion).
        company_name = group["company_clean"].value_counts().idxmax()
        impact_if_mapped = len(group)

        conn_mask = company_norm_all == company_norm
        personas_here = persona_all[conn_mask]
        matched_connection_count = int(conn_mask.sum())
        matched_recruiters = int((personas_here == "Recruiter").sum())
        matched_ta = int(personas_here.isin(TA_PERSONAS).sum())
        matched_hm = int(personas_here.isin(HIRING_PERSONAS).sum())
        matched_dl = int(personas_here.isin(DATA_LEADER_PERSONAS).sum())
        matched_top = int((conn_mask & df.index.isin(top_idx)).sum())
        existing_category = next(iter(category_all[conn_mask].dropna()), "") if conn_mask.any() else ""

        matched_untapped = int(untapped_key.isin([company_norm]).sum()) if not untapped.empty else 0
        matched_lead_react = int(msgs_key.isin([company_norm]).sum()) if not msgs.empty else 0
        matched_inbound = int(inbound_key.isin([company_norm]).sum()) if not inbound.empty else 0
        matched_soft_closed = int(soft_closed_key.isin([company_norm]).sum()) if not soft_closed.empty else 0
        matched_usd_leads = int(usd_key.isin([company_norm]).sum()) if not usd_leads.empty else 0
        events_here_mask = events_key == company_norm if not events.empty else pd.Series(dtype=bool)
        matched_events = int(events_here_mask.sum())
        latam_signal_present = bool(events.loc[events_here_mask, "latam_signal"].astype(str).isin(["True", "1", "true"]).any()) if matched_events and "latam_signal" in events.columns else False
        usd_signal_present = bool(events.loc[events_here_mask, "usd_signal"].astype(str).isin(["True", "1", "true"]).any()) if matched_events and "usd_signal" in events.columns else False

        fw = conn_key_to_follow.get(company_norm, {})
        followed_company_signal = bool(fw)
        recently_followed_signal = bool(fw) and 0 <= fw.get("days_since_followed", -1) <= 30

        has_recruiter_ta_signal = (matched_recruiters + matched_ta) > 0
        has_opportunity_history_signal = (matched_events + matched_inbound + matched_soft_closed) > 0
        message_history_signal = matched_lead_react > 0

        classification = _classify_candidate(
            company_name,
            follow_bucket=fw.get("bucket", ""), follow_category=fw.get("category", ""),
            follow_confidence=fw.get("confidence", 0.0),
            has_recruiter_ta_signal=has_recruiter_ta_signal,
            has_opportunity_history_signal=has_opportunity_history_signal,
            latam_signal_present=latam_signal_present, usd_signal_present=usd_signal_present,
            existing_company_category=existing_category,
        )

        priority_score = (
            impact_if_mapped * 10
            + matched_recruiters * 5
            + matched_ta * 4
            + matched_hm * 4
            + matched_dl * 3
            + matched_inbound * 6
            + matched_usd_leads * 4
            + matched_lead_react * 2
            + matched_top * 3
            + matched_untapped * 1
            + (5 if followed_company_signal else 0)
            + (3 if recently_followed_signal else 0)
        )

        rows.append({
            "company_name": company_name,
            "normalized_company": company_norm,
            "current_bucket": NEEDS_MAPPING,
            "suggested_bucket": classification["suggested_bucket"],
            "suggested_category": classification["suggested_category"],
            "confidence": classification["confidence"],
            "manual_review_required": True,
            "matched_connection_count": matched_connection_count,
            "matched_recruiters": matched_recruiters,
            "matched_talent_acquisition": matched_ta,
            "matched_hiring_managers": matched_hm,
            "matched_data_leaders": matched_dl,
            "matched_top_contacts": matched_top,
            "matched_untapped_contacts": matched_untapped,
            "matched_lead_reactivation_contacts": matched_lead_react,
            "matched_inbound_opportunities": matched_inbound,
            "matched_usd_crm_leads": matched_usd_leads,
            "matched_soft_closed_leads": matched_soft_closed,
            "followed_company_signal": followed_company_signal,
            "recently_followed_signal": recently_followed_signal,
            "message_history_signal": message_history_signal,
            "opportunity_history_signal": has_opportunity_history_signal,
            # usd_signal: from opportunity-history message evidence (currency/rate
            # mentions), distinct from us_canada_signal below (a geography keyword
            # in the company's own name) — two different kinds of evidence.
            "usd_signal": usd_signal_present,
            "latam_signal": classification["latam_signal"],
            "us_canada_signal": classification["us_canada_signal"],
            "spain_eu_signal": classification["spain_eu_signal"],
            "global_staffing_signal": classification["global_staffing_signal"],
            "global_consulting_signal": classification["global_consulting_signal"],
            "reason_short": classification["reason_short"],
            "impact_if_mapped": impact_if_mapped,
            "priority_score": priority_score,
            "yaml_suggestion": _yaml_suggestion(raw_names, classification["suggested_bucket"], classification["suggested_category"]),
        })

    workbench_df = pd.DataFrame(rows)
    if not workbench_df.empty:
        # Defense in depth: a blank pandas cell (from any upstream CSV re-read)
        # surfaces as float NaN, which is truthy in Python and JSON-illegal —
        # fillna per dtype here so nothing NaN ever reaches the CSV/JSON export
        # regardless of which code path it slipped in through.
        for col in workbench_df.select_dtypes(include="object").columns:
            workbench_df[col] = workbench_df[col].fillna("")
        for col in workbench_df.select_dtypes(include="number").columns:
            workbench_df[col] = workbench_df[col].fillna(0)
        workbench_df = workbench_df.sort_values("priority_score", ascending=False).reset_index(drop=True)

    _export_outputs(workbench_df, needs_mapping_total=len(needs_df))

    summary = {
        "candidate_count": len(workbench_df),
        "needs_mapping_total": len(needs_df),
        "workbench_candidates": int((workbench_df["matched_connection_count"] > 0).sum()) if not workbench_df.empty else 0,
        "with_suggested_bucket": int((workbench_df["suggested_bucket"] != NEEDS_MAPPING).sum()) if not workbench_df.empty else 0,
        "followed_needing_review": int(workbench_df["followed_company_signal"].sum()) if not workbench_df.empty else 0,
        "recruiter_ta_candidates": int(((workbench_df["matched_recruiters"] + workbench_df["matched_talent_acquisition"]) > 0).sum()) if not workbench_df.empty else 0,
        "usd_latam_suggested": int(workbench_df["suggested_bucket"].isin(
            ["LATAM_USD_CONFIRMED", "LATAM_USD_LIKELY", "US_CANADA_CONFIRMED", "US_CANADA_LIKELY"]).sum()) if not workbench_df.empty else 0,
        "staffing_suggested": int((workbench_df["suggested_bucket"] == "GLOBAL_STAFFING").sum()) if not workbench_df.empty else 0,
        "global_opportunity_suggested": int((workbench_df["suggested_bucket"] == "GLOBAL_OPPORTUNITY").sum()) if not workbench_df.empty else 0,
        "top50_impact": int(workbench_df.head(50)["impact_if_mapped"].sum()) if not workbench_df.empty else 0,
    }

    logger.info(
        f"  Company Mapping Workbench: {summary['candidate_count']} companies ranked "
        f"({summary['needs_mapping_total']} contacts) | "
        f"with_suggestion={summary['with_suggested_bucket']} "
        f"followed_review={summary['followed_needing_review']} "
        f"top50_impact={summary['top50_impact']}"
    )

    return {
        "available": True,
        "summary": summary,
        "companies": workbench_df.head(500).to_dict(orient="records"),
        "geography_note": GEOGRAPHY_NOTE,
    }


_EMPTY_WORKBENCH_COLS = [
    "company_name", "normalized_company", "current_bucket", "suggested_bucket", "suggested_category",
    "confidence", "manual_review_required", "matched_connection_count", "matched_recruiters",
    "matched_talent_acquisition", "matched_hiring_managers", "matched_data_leaders", "matched_top_contacts",
    "matched_untapped_contacts", "matched_lead_reactivation_contacts", "matched_inbound_opportunities",
    "matched_usd_crm_leads", "matched_soft_closed_leads", "followed_company_signal", "recently_followed_signal",
    "message_history_signal", "opportunity_history_signal", "usd_signal", "latam_signal", "us_canada_signal",
    "spain_eu_signal", "global_staffing_signal", "global_consulting_signal", "reason_short",
    "impact_if_mapped", "priority_score", "yaml_suggestion",
]


def _write_empty_outputs() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=_EMPTY_WORKBENCH_COLS).to_csv(
        OUTPUTS_DIR / "company_mapping_workbench.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(columns=_EMPTY_WORKBENCH_COLS).to_csv(
        OUTPUTS_DIR / "company_mapping_priority_queue.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(columns=["top_n", "cumulative_companies", "cumulative_contacts_resolved",
                           "cumulative_pct_of_needs_mapping"]).to_csv(
        OUTPUTS_DIR / "company_mapping_impact_estimate.csv", index=False, encoding="utf-8-sig")
    _write_yaml_suggestions(pd.DataFrame(columns=_EMPTY_WORKBENCH_COLS))


def _write_yaml_suggestions(workbench_df: pd.DataFrame) -> None:
    lines = [
        "# company_mapping_yaml_suggestions.yml",
        "# Auto-generated by src/company_mapping_workbench.py — SUGGESTIONS ONLY.",
        "# This file is never read by the pipeline and nothing here is applied automatically.",
        "# Review each line, then copy the ones you confirm into config/company_market_overrides.yml",
        "# (under its 'overrides:' key, same {market: ..., category: ...} format).",
        "#",
        "# LinkedIn exports carry no exact location — these are relevance signals",
        "# (company name keywords, recruiter/TA presence, message/opportunity history),",
        "# not confirmed geography. Confirm before pasting.",
        "",
        "overrides:",
    ]
    candidates = workbench_df[workbench_df["yaml_suggestion"] != ""] if not workbench_df.empty else workbench_df
    if candidates.empty:
        lines.append("  # No suggestions this run — every remaining candidate lacks a supporting signal.")
    else:
        for _, r in candidates.head(200).iterrows():
            lines.append(str(r["yaml_suggestion"]))
    (OUTPUTS_DIR / "company_mapping_yaml_suggestions.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _export_outputs(workbench_df: pd.DataFrame, needs_mapping_total: int) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    workbench_df.to_csv(OUTPUTS_DIR / "company_mapping_workbench.csv", index=False, encoding="utf-8-sig")

    priority_queue = workbench_df.head(200)
    priority_queue.to_csv(OUTPUTS_DIR / "company_mapping_priority_queue.csv", index=False, encoding="utf-8-sig")

    # Impact estimate — "if I map the top N this Sunday, how many contacts
    # leave the backlog" (mirrors company_resolution_v7's Pareto coverage).
    impact_rows = []
    cumulative_contacts = 0
    for n in (10, 25, 50, 100, 200, len(workbench_df)):
        n = min(n, len(workbench_df))
        if n <= 0:
            continue
        cumulative_contacts = int(workbench_df.head(n)["impact_if_mapped"].sum())
        impact_rows.append({
            "top_n": n,
            "cumulative_companies": n,
            "cumulative_contacts_resolved": cumulative_contacts,
            "cumulative_pct_of_needs_mapping": round(cumulative_contacts / needs_mapping_total * 100, 1) if needs_mapping_total else 0.0,
        })
    # de-dupe (e.g. top_n=200 == len(df) for a small backlog)
    seen = set()
    dedup_rows = []
    for r in impact_rows:
        if r["top_n"] in seen:
            continue
        seen.add(r["top_n"])
        dedup_rows.append(r)
    pd.DataFrame(dedup_rows, columns=["top_n", "cumulative_companies", "cumulative_contacts_resolved",
                                       "cumulative_pct_of_needs_mapping"]).to_csv(
        OUTPUTS_DIR / "company_mapping_impact_estimate.csv", index=False, encoding="utf-8-sig")

    _write_yaml_suggestions(workbench_df)

    logger.info(f"  Saved company_mapping_workbench.csv ({len(workbench_df)} companies)")
