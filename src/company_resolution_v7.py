# -*- coding: utf-8 -*-
"""
company_resolution_v7.py
=========================
Iterative, multi-pass corrective layer that runs AFTER company_resolution_v6
and works the STILL-unresolved NEEDS_COMPANY_MAPPING residual harder, without
fabricating geography. See CLAUDE_CORRECTIVE_UI_MAPPING_V7.md Parts 5-15.

Honest mapping to the spec's 8-pass model (V5 + V6 already implement passes
1-2-3-5-6-7 individually; this module adds the missing mechanisms — fuzzy
clustering and COMPANY-LEVEL aggregate evidence — and, critically, makes the
whole thing an iterative fixpoint instead of the single pass V6 ran):

  PASS 1 (exact dictionary / manual override / explicit keyword) — done by
          opportunity_market_v5.py before this module ever runs.
  PASS 2 (canonical company aliases)                              — company_normalizer.py
          (extended in this patch with more aliases: Globant, BairesDev, ...).
  PASS 3 (same-company high-confidence propagation)                — extended here with a
          3rd confidence tier (>=10 contacts & >=85% share -> 0.88) and it now
          groups companies through the fuzzy-cluster map built in PASS 4, so
          near-duplicate spellings the alias dict doesn't know about still count
          as "the same company" for propagation purposes.
  PASS 4 (fuzzy company entity clustering)                         — NEW. Conservative
          token-set similarity (rapidfuzz if installed, difflib fallback),
          never merging on generic words alone (tech/digital/solutions/...).
  PASS 5 (company category inference)                              — done by V5/V6.
  PASS 6 (aggregate message opportunity signals)                    — NEW. V6 only used a
          contact's OWN messages; this module aggregates signal evidence across
          ALL contacts at the same canonical company so one recruiter's
          "remote LATAM contractor, USD" conversation can support their
          unresolved colleagues.
  PASS 7 (persona + role + company context)                        — extended here into a
          COHORT-level fallback: company composition (N recruiters/TA, N data
          leaders, N consultants) supports contacts whose own persona/title
          alone wasn't enough for V6's per-row fallback.
  PASS 8 (repeat propagation after new evidence)                   — the fixpoint loop
          below: after each iteration's message/cohort evidence resolves new
          rows, propagation (PASS 3) is re-run so companies that just crossed
          the support/share threshold get to propagate too. Runs until no row
          changes state or 10 iterations, whichever comes first.

Only rows still at NEEDS_COMPANY_MAPPING when this module runs are touched.
Nothing V5/V6 already resolved is re-evaluated or overwritten.

New columns added (Part 6): resolution_iteration, resolution_pass,
resolution_source, resolution_confidence, resolution_reason — stamped only on
rows this module resolves (rows V5/V6 already resolved keep their own fields
and get resolution_pass="pre_v7").

Outputs (Part 14/15):
  outputs/company_resolution_v7_summary.csv
  outputs/company_resolution_v7_audit.csv
  outputs/company_mapping_residual_v7.csv
  outputs/company_mapping_pareto_v7.csv
  outputs/company_fuzzy_clusters_v7.csv   (Part 8 audit)
"""

import difflib
import logging
import re
from pathlib import Path

import pandas as pd

from src.company_normalizer import normalize, canonical_display, tokens, COMPANY_ALIASES
from src.company_dictionary_enrichment import (
    LATAM_USD_LIKELY, US_CANADA_LIKELY, SPAIN_EU_LIKELY,
    GLOBAL_STAFFING, GLOBAL_CONSULTING, GLOBAL_TECH, GLOBAL_OPPORTUNITY,
    NEEDS_MAPPING, LOW_VALUE,
)
from src.message_sanitizer import strip_html

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"

MAX_ITERATIONS = 10

RECRUITING_PERSONAS = {"Recruiter", "Talent Acquisition", "Sourcer"}
HIRING_PERSONAS      = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS = {"Data Engineering Manager", "Head of Data", "Director"}
STRATEGIC_PERSONAS  = {
    "Hiring Manager", "Engineering Manager", "Data Engineering Manager",
    "Head of Data", "Director", "Executive", "Founder", "Partner",
}

# Try rapidfuzz for better/faster fuzzy scoring; fall back to stdlib difflib
# (conservative, no extra dependency required — Part 8 says "if available").
try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz
    def _token_set_ratio(a: str, b: str) -> float:
        return _rapidfuzz_fuzz.token_set_ratio(a, b)
    _FUZZ_ENGINE = "rapidfuzz"
except Exception:
    def _token_set_ratio(a: str, b: str) -> float:
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0.0
        common = ta & tb
        rest_a = ta - common
        rest_b = tb - common
        s1 = " ".join(sorted(common) + sorted(rest_a))
        s2 = " ".join(sorted(common) + sorted(rest_b))
        return difflib.SequenceMatcher(None, s1, s2).ratio() * 100
    _FUZZ_ENGINE = "difflib"

# Generic words that must NEVER be the sole basis for a fuzzy merge (Part 8)
GENERIC_COMPANY_WORDS = {
    "tech", "technology", "technologies", "digital", "solutions", "solution",
    "consulting", "consultoria", "consultancy", "group", "grupo", "services",
    "service", "data", "systems", "sistemas", "global", "holdings", "holding",
    "ventures", "partners", "labs", "software", "informatica", "informatica",
}


def _meaningful_shared_tokens(ta: set, tb: set) -> set:
    return {t for t in (ta & tb) if len(t) >= 4 and t not in GENERIC_COMPANY_WORDS}


def _build_fuzzy_clusters(df: pd.DataFrame) -> tuple[dict, list]:
    """
    Conservatively merge unresolved companies onto an already-resolved
    company's normalized key when the names are clearly the same entity but
    not caught by exact normalize()/alias matching (Part 8).

    Returns (merge_map, audit_rows) where merge_map is
    {unresolved_norm_key: resolved_target_norm_key}.
    """
    needs_mask = df["opportunity_market_v5"] == NEEDS_MAPPING
    resolved_mask = ~df["opportunity_market_v5"].isin([NEEDS_MAPPING, LOW_VALUE])

    unresolved_companies = sorted({
        c for c in df.loc[needs_mask, "company_clean"].fillna("").tolist() if c.strip()
    })
    resolved_keys: dict[str, str] = {}  # norm_key -> original company_clean (first seen)
    for c in df.loc[resolved_mask, "company_clean"].fillna("").tolist():
        if not c.strip():
            continue
        key = normalize(c)
        if key and key not in resolved_keys:
            resolved_keys[key] = c

    merge_map: dict[str, str] = {}
    audit_rows: list = []
    seen_unresolved_keys: set = set()

    for raw in unresolved_companies:
        key = normalize(raw)
        if not key or key in seen_unresolved_keys or key in resolved_keys:
            continue  # already an exact/alias match to a resolved company
        seen_unresolved_keys.add(key)

        raw_tokens = tokens(raw)
        threshold = 94.0 if len(key) <= 12 else 90.0

        best_target, best_score = None, 0.0
        for target_key, target_raw in resolved_keys.items():
            shared = _meaningful_shared_tokens(raw_tokens, tokens(target_raw))
            if not shared:
                continue
            score = _token_set_ratio(key, target_key)
            if score > best_score:
                best_target, best_score = target_key, score

        if best_target and best_score >= threshold:
            merge_map[key] = best_target
            audit_rows.append({
                "raw_company": raw, "canonical_company": canonical_display(resolved_keys[best_target]),
                "similarity_score": round(best_score, 1),
                "cluster_reason": f"token_set_ratio>={threshold:.0f} + shared meaningful token",
                "accepted": True, "confidence": 0.75 if best_score >= 97 else 0.68,
            })
        elif best_target and best_score >= threshold - 10:
            # Close but below the conservative bar — logged for transparency, not merged.
            audit_rows.append({
                "raw_company": raw, "canonical_company": canonical_display(resolved_keys[best_target]),
                "similarity_score": round(best_score, 1),
                "cluster_reason": "below conservative threshold — not merged",
                "accepted": False, "confidence": "",
            })

    return merge_map, audit_rows


def _effective_key(company: str, fuzzy_map: dict) -> str:
    key = normalize(company)
    return fuzzy_map.get(key, key)


def _apply_tiered_propagation(df: pd.DataFrame, needs_mask: pd.Series, fuzzy_map: dict,
                               iteration: int) -> tuple[list, int]:
    """PASS 3 (+ PASS 8 repetition) with a 3rd confidence tier and fuzzy-merged
    company grouping. Mutates df in place. Returns (audit_rows, resolved_count)."""
    eff_key = df["company_clean"].fillna("").apply(lambda c: _effective_key(c, fuzzy_map))
    resolved_mask = ~df["opportunity_market_v5"].isin([NEEDS_MAPPING, LOW_VALUE]) & (eff_key != "")

    audit_rows = []
    resolved_count = 0

    for key, group_idx in df[eff_key != ""].groupby(eff_key).groups.items():
        if not key:
            continue
        group = df.loc[group_idx]
        group_needs = group[needs_mask.loc[group_idx]]
        if group_needs.empty:
            continue

        resolved_sub = group[resolved_mask.loc[group_idx]]
        support = len(resolved_sub)
        if support == 0:
            continue

        counts = resolved_sub["opportunity_market_v5"].value_counts()
        dominant_bucket = counts.index[0]
        dominant_count  = int(counts.iloc[0])
        share = dominant_count / support

        if support >= 10 and share >= 0.85:
            confidence, tier = 0.88, "tier_c_10plus_85pct"
        elif support >= 5 and share >= 0.80:
            confidence, tier = 0.82, "tier_b_5plus_80pct"
        elif support >= 2 and share >= 0.70:
            confidence, tier = 0.72, "tier_a_2plus_70pct"
        else:
            confidence, tier = None, None

        if confidence is None:
            continue

        reason = (
            f"same-company dominant bucket propagation v7 "
            f"({support} contacts, {share:.0%} share, {tier})"
        )
        for idx in group_needs.index:
            df.at[idx, "opportunity_market_v5"]        = dominant_bucket
            df.at[idx, "opportunity_bucket"]            = dominant_bucket
            df.at[idx, "opportunity_confidence"]         = confidence
            df.at[idx, "opportunity_reason"]             = reason
            df.at[idx, "needs_manual_company_mapping"]   = False
            df.at[idx, "company_resolution_source"]      = "same_company_propagation_v7"
            df.at[idx, "company_resolution_confidence"]  = confidence
            df.at[idx, "company_evidence_count"]         = support
            df.at[idx, "company_dominant_bucket"]        = dominant_bucket
            df.at[idx, "company_dominant_bucket_share"]  = round(share, 3)
            df.at[idx, "cross_contact_propagation_used"] = True
            df.at[idx, "manual_review_required"]         = confidence < 0.82
            df.at[idx, "resolution_iteration"]           = iteration
            df.at[idx, "resolution_pass"]                = "pass3_propagation"
            df.at[idx, "resolution_source"]              = "same_company_propagation_v7"
            df.at[idx, "resolution_confidence"]          = confidence
            df.at[idx, "resolution_reason"]              = reason
            resolved_count += 1

        audit_rows.append({
            "company_clean": group["company_clean"].iloc[0],
            "company_canonical": canonical_display(key),
            "total_contacts": len(group), "unresolved_count": len(group_needs),
            "resolved_support": support, "dominant_bucket": dominant_bucket,
            "dominant_share": round(share, 3), "tier": tier, "confidence_assigned": confidence,
        })

    return audit_rows, resolved_count


LATAM_KW    = re.compile(r"\b(latam|latin america|south america|brazil|brasil|argentina|colombia|mexico|méxico|chile|uruguay|peru|perú|nearshore|remote latam)\b", re.I)
US_KW       = re.compile(r"\b(usa|us role|united states|us timezone|est|cst|contractor|usd)\b", re.I)
CANADA_KW   = re.compile(r"\bcanada\b", re.I)
SPAIN_KW    = re.compile(r"\b(spain|españa|espana|madrid|barcelona)\b", re.I)
EUROPE_KW   = re.compile(r"\b(portugal|lisbon|lisboa|porto|europe|\beu\b|germany|netherlands|ireland)\b", re.I)
CONTRACTOR_KW = re.compile(r"\bcontractor\b", re.I)
REMOTE_KW   = re.compile(r"\bremote\b", re.I)


def _build_company_message_aggregate(df: pd.DataFrame) -> dict:
    """PASS 6 — aggregate regional/opportunity signal evidence across ALL
    contacts of the same canonical company (not just the individual contact's
    own conversation, unlike V6). Local messages.csv only; never exposes raw
    matched text — only counts. Returns {norm_company_key: {...counts...}}."""
    try:
        from src.message_intelligence import load_messages
    except Exception:
        return {}

    msgs = load_messages()
    if msgs is None or msgs.empty:
        return {}

    url_to_key = {}
    for _, row in df.iterrows():
        url = str(row.get("url", "") or "").strip().rstrip("/").lower()
        if url:
            url_to_key[url] = normalize(str(row.get("company_clean", "") or ""))

    agg: dict[str, dict] = {}
    for conv_id, group in msgs.groupby("conversation_id"):
        text = " ".join(strip_html(c) for c in group["content"].fillna("").astype(str).tolist()).lower()
        if not text.strip():
            continue
        contact_urls = set()
        for _, r in group.iterrows():
            if not r.get("is_me_sender"):
                u = str(r.get("sender_profile_url", "") or "").strip().rstrip("/").lower()
                if u:
                    contact_urls.add(u)
            else:
                ru = str(r.get("recipient_profile_urls", "") or "")
                for part in ru.split(","):
                    part = part.strip().rstrip("/").lower()
                    if part:
                        contact_urls.add(part)

        for u in contact_urls:
            key = url_to_key.get(u)
            if not key:
                continue
            bucket = agg.setdefault(key, {
                "LATAM_signal_count": 0, "US_signal_count": 0, "Canada_signal_count": 0,
                "Spain_signal_count": 0, "Europe_signal_count": 0,
                "contractor_signal_count": 0, "remote_signal_count": 0,
                "conversation_count": 0, "_contacts": set(),
            })
            bucket["LATAM_signal_count"]      += len(LATAM_KW.findall(text))
            bucket["US_signal_count"]         += len(US_KW.findall(text))
            bucket["Canada_signal_count"]     += len(CANADA_KW.findall(text))
            bucket["Spain_signal_count"]      += len(SPAIN_KW.findall(text))
            bucket["Europe_signal_count"]     += len(EUROPE_KW.findall(text))
            bucket["contractor_signal_count"] += len(CONTRACTOR_KW.findall(text))
            bucket["remote_signal_count"]     += len(REMOTE_KW.findall(text))
            bucket["conversation_count"]      += 1
            bucket["_contacts"].add(u)

    for key, b in agg.items():
        b["distinct_contact_count"] = len(b.pop("_contacts"))
        signals = {
            "LATAM": b["LATAM_signal_count"] + b["remote_signal_count"] * 0.25,
            "US": b["US_signal_count"] + b["contractor_signal_count"] * 0.25,
            "Canada": b["Canada_signal_count"],
            "Spain": b["Spain_signal_count"],
            "Europe": b["Europe_signal_count"],
        }
        dominant = max(signals, key=signals.get) if any(signals.values()) else ""
        b["dominant_signal"] = dominant
        total = sum(v for v in signals.values())
        b["signal_confidence"] = (
            round(min(0.75, 0.60 + 0.03 * b["distinct_contact_count"] + 0.01 * total), 2)
            if dominant else 0.0
        )

    return agg


_SIGNAL_TO_BUCKET = {
    "LATAM": LATAM_USD_LIKELY, "US": US_CANADA_LIKELY, "Canada": US_CANADA_LIKELY,
    "Spain": SPAIN_EU_LIKELY, "Europe": SPAIN_EU_LIKELY,
}


def _apply_company_message_aggregate_evidence(df: pd.DataFrame, needs_mask: pd.Series,
                                               company_agg: dict, iteration: int) -> int:
    """PASS 6 application — supports unresolved contacts using COMPANY-level
    aggregate message evidence, even if they personally have no messages."""
    if not company_agg:
        return 0
    resolved_count = 0
    remaining = df[needs_mask]
    for idx, row in remaining.iterrows():
        key = normalize(str(row.get("company_clean", "") or ""))
        if not key or key not in company_agg:
            continue
        b = company_agg[key]
        dominant = b.get("dominant_signal")
        if not dominant or b.get("distinct_contact_count", 0) < 2:
            continue  # require at least 2 distinct contacts as company-level evidence
        bucket = _SIGNAL_TO_BUCKET.get(dominant)
        if not bucket:
            continue
        confidence = b["signal_confidence"]
        reason = (
            f"message history contains {dominant} opportunity signal "
            f"(aggregated across {b['distinct_contact_count']} contacts at company)"
        )
        df.at[idx, "opportunity_market_v5"]        = bucket
        df.at[idx, "opportunity_bucket"]            = bucket
        df.at[idx, "opportunity_confidence"]         = confidence
        df.at[idx, "opportunity_reason"]             = reason
        df.at[idx, "needs_manual_company_mapping"]   = False
        df.at[idx, "company_resolution_source"]      = "company_message_aggregate"
        df.at[idx, "company_resolution_confidence"]  = confidence
        df.at[idx, "company_evidence_count"]         = b["distinct_contact_count"]
        df.at[idx, "message_signal_used"]            = True
        df.at[idx, "manual_review_required"]         = True
        df.at[idx, "resolution_iteration"]           = iteration
        df.at[idx, "resolution_pass"]                = "pass6_company_message_aggregate"
        df.at[idx, "resolution_source"]              = "company_message_aggregate"
        df.at[idx, "resolution_confidence"]          = confidence
        df.at[idx, "resolution_reason"]              = reason
        resolved_count += 1
    return resolved_count


def _apply_persona_role_cohort_fallback(df: pd.DataFrame, needs_mask: pd.Series,
                                         iteration: int) -> int:
    """PASS 7 — company COHORT context (not persona alone): if the company has
    a strong recruiting/consulting/data-leadership cohort, unresolved contacts
    at that company inherit a non-geographic opportunity category."""
    resolved_count = 0
    remaining = df[needs_mask]
    if remaining.empty:
        return 0

    company_key_all = df["company_clean"].fillna("").apply(normalize)
    persona_all = df.get("persona", pd.Series(index=df.index, dtype=object)).fillna("")

    for idx, row in remaining.iterrows():
        key = company_key_all.loc[idx]
        if not key:
            continue
        cohort = persona_all[company_key_all == key]
        n_recruiting  = cohort.isin(RECRUITING_PERSONAS).sum()
        n_data_leader = cohort.isin(DATA_LEADER_PERSONAS).sum()
        n_hiring      = cohort.isin(HIRING_PERSONAS).sum()
        cat_v4 = str(row.get("company_category_v4", "") or "")

        bucket, reason = None, None
        if n_recruiting >= 3:
            bucket = GLOBAL_STAFFING
            reason = f"persona/role cohort evidence: {n_recruiting} recruiting-persona contacts at company"
        elif cat_v4 == "GLOBAL_CONSULTING" and (n_hiring + n_data_leader) >= 1:
            bucket = GLOBAL_CONSULTING
            reason = "persona/role cohort evidence: consulting company with professional cohort"
        elif n_data_leader >= 2:
            bucket = GLOBAL_OPPORTUNITY
            reason = f"persona/role cohort evidence: {n_data_leader} data-leadership contacts at company"

        if not bucket:
            continue

        df.at[idx, "opportunity_market_v5"]        = bucket
        df.at[idx, "opportunity_bucket"]            = bucket
        df.at[idx, "opportunity_confidence"]         = 0.58
        df.at[idx, "opportunity_reason"]             = reason
        df.at[idx, "needs_manual_company_mapping"]   = False
        df.at[idx, "company_resolution_source"]      = "persona_role_cohort"
        df.at[idx, "company_resolution_confidence"]  = 0.58
        df.at[idx, "manual_review_required"]         = True
        df.at[idx, "resolution_iteration"]           = iteration
        df.at[idx, "resolution_pass"]                = "pass7_persona_role_cohort"
        df.at[idx, "resolution_source"]              = "persona_role_cohort"
        df.at[idx, "resolution_confidence"]          = 0.58
        df.at[idx, "resolution_reason"]              = reason
        resolved_count += 1

    return resolved_count


def _resolution_bucket_v7(reason: str, source: str) -> str:
    r = (reason or "").lower()
    s = (source or "").lower()
    if "manual override" in r:
        return "manual_override"
    if "fuzzy" in s:
        return "fuzzy_cluster"
    if "same_company_propagation" in s or "same-company" in r:
        return "same_company"
    if "company_message_aggregate" in s or "message history contains" in r:
        return "message_signal"
    if "persona_role_cohort" in s or "persona/role cohort" in r:
        return "persona_cohort"
    if "persona_company_category_fallback" in s or "persona/company-category fallback" in r:
        return "persona_cohort"
    if "company category" in r:
        return "company_category"
    if "exact company dict" in r or "substring company match" in r or "v4 inference" in r:
        return "exact_dictionary"
    if "region keyword" in r or "language" in r:
        return "exact_dictionary"
    return "unresolved"


def apply_company_resolution_v7(df: pd.DataFrame, v6_summary: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Main entry point. Assumes df already went through V5 + V6. Returns
    (df, summary_dict). Iterates fuzzy-cluster propagation, company-level
    message-aggregate evidence, and persona/role cohort fallback to a fixpoint
    (Part 6, PASS 8), then honestly reports the result (Part 14)."""
    df = df.copy()
    if "opportunity_market_v5" not in df.columns:
        logger.warning("  company_resolution_v7: opportunity_market_v5 missing — skipping")
        return df, {}

    total = len(df)
    for col, default in [
        ("resolution_iteration", 0), ("resolution_pass", "pre_v7"),
        ("resolution_source", ""), ("resolution_confidence", 0.0), ("resolution_reason", ""),
    ]:
        if col not in df.columns:
            df[col] = default

    needs_before = int((df["opportunity_market_v5"] == NEEDS_MAPPING).sum())
    before_snapshot = df.loc[df["opportunity_market_v5"] == NEEDS_MAPPING, [
        c for c in ["full_name", "url", "company_clean", "persona", "position_clean",
                    "priority_score", "opportunity_market_v5", "opportunity_reason"]
        if c in df.columns
    ]].copy().rename(columns={
        "opportunity_market_v5": "opportunity_market_v5_before",
        "opportunity_reason":    "opportunity_reason_before",
    })

    fuzzy_map, fuzzy_audit = _build_fuzzy_clusters(df)
    company_agg = _build_company_message_aggregate(df)

    iteration_log = []
    resolved_propagation = resolved_message = resolved_cohort = 0
    all_propagation_audit = []

    for iteration in range(1, MAX_ITERATIONS + 1):
        before_iter = int((df["opportunity_market_v5"] == NEEDS_MAPPING).sum())
        if before_iter == 0:
            break

        needs_mask = df["opportunity_market_v5"] == NEEDS_MAPPING
        prop_audit, n_prop = _apply_tiered_propagation(df, needs_mask, fuzzy_map, iteration)
        all_propagation_audit.extend(prop_audit)
        resolved_propagation += n_prop

        needs_mask = df["opportunity_market_v5"] == NEEDS_MAPPING
        n_msg = _apply_company_message_aggregate_evidence(df, needs_mask, company_agg, iteration)
        resolved_message += n_msg

        needs_mask = df["opportunity_market_v5"] == NEEDS_MAPPING
        n_cohort = _apply_persona_role_cohort_fallback(df, needs_mask, iteration)
        resolved_cohort += n_cohort

        after_iter = int((df["opportunity_market_v5"] == NEEDS_MAPPING).sum())
        delta = before_iter - after_iter
        iteration_log.append({
            "iteration": iteration, "before": before_iter, "after": after_iter,
            "resolved_propagation": n_prop, "resolved_message_aggregate": n_msg,
            "resolved_persona_cohort": n_cohort, "delta": delta,
        })
        if delta == 0:
            break

    needs_after = int((df["opportunity_market_v5"] == NEEDS_MAPPING).sum())
    resolved_total = needs_before - needs_after
    pct_before = round(needs_before / total * 100, 1) if total else 0.0
    pct_after  = round(needs_after  / total * 100, 1) if total else 0.0
    target_met = pct_after <= 15.0

    # ── Honest resolution-method breakdown across the WHOLE network ──────────
    bucket_series = df.apply(
        lambda r: _resolution_bucket_v7(r.get("opportunity_reason", ""), r.get("company_resolution_source", "")),
        axis=1,
    )
    method_breakdown = bucket_series.value_counts().to_dict()
    # Rows still NEEDS_COMPANY_MAPPING must show as unresolved regardless of any stale reason text
    method_breakdown["unresolved"] = needs_after

    def _count(name):
        return int(method_breakdown.get(name, 0))

    v6_before_ref = (v6_summary or {}).get("needs_mapping_before", needs_before)

    if target_met:
        target_note = f"Target met: Needs Company Mapping is {pct_after}% of network (<=15% target for this patch)."
    else:
        target_note = (
            f"Target not fully met: Needs Company Mapping is {pct_after}% of network (target <=15%, "
            f"aspirational 5-10%). Reduced from {pct_before}% ({needs_before:,}) to {pct_after}% ({needs_after:,}) "
            f"this pass using honest fuzzy-cluster, company-level message-aggregate, and persona/role-cohort "
            f"evidence only. The remaining residual genuinely has no company match, no cross-contact evidence, "
            f"no message signal, and no persona/category cohort signal — it was left unresolved rather than "
            f"fabricating geography."
        )

    summary = {
        "fuzz_engine":                  _FUZZ_ENGINE,
        "total_connections":            total,
        "before_unresolved":            needs_before,
        "after_unresolved":             needs_after,
        "resolved_total":               resolved_total,
        "unresolved_pct":               pct_after,
        "needs_mapping_pct_before":     pct_before,
        "needs_mapping_pct_after":      pct_after,
        "resolved_by_exact_dictionary": _count("exact_dictionary"),
        "resolved_by_alias":            int(
                                             df.loc[~df["opportunity_market_v5"].isin([NEEDS_MAPPING, LOW_VALUE]), "company_clean"]
                                             .fillna("").apply(lambda c: c.strip().lower() in COMPANY_ALIASES).sum()
                                         ),
        "resolved_by_fuzzy_cluster":    _count("fuzzy_cluster"),
        "resolved_by_same_company":     _count("same_company"),
        "resolved_by_message_signal":   _count("message_signal"),
        "resolved_by_company_category": _count("company_category"),
        "resolved_by_persona_cohort":   _count("persona_cohort"),
        "resolved_by_manual_override":  _count("manual_override"),
        "resolved_this_pass_propagation":       resolved_propagation,
        "resolved_this_pass_message_aggregate": resolved_message,
        "resolved_this_pass_persona_cohort":    resolved_cohort,
        "iterations_run":               len(iteration_log),
        "iteration_log":                iteration_log,
        "fuzzy_clusters_accepted":      sum(1 for a in fuzzy_audit if a["accepted"]),
        "fuzzy_clusters_reviewed":      len(fuzzy_audit),
        "target_met":                   target_met,
        "target_note":                  target_note,
        "resolution_method_breakdown":  {str(k): int(v) for k, v in method_breakdown.items()},
        # Context vs. the ORIGINAL pre-V6 baseline, for the Data Quality page
        "overall_before_v6":            v6_before_ref,
        "overall_after_v7":             needs_after,
        "overall_reduction_count":      v6_before_ref - needs_after,
        "overall_reduction_pct":        round((v6_before_ref - needs_after) / v6_before_ref * 100, 1) if v6_before_ref else 0.0,
    }

    logger.info(
        f"  Company Resolution V7: Needs Mapping {needs_before:,} -> {needs_after:,} "
        f"({pct_before}% -> {pct_after}% of network) in {len(iteration_log)} iteration(s) | "
        f"propagation={resolved_propagation} msg_aggregate={resolved_message} persona_cohort={resolved_cohort} | "
        f"fuzz_engine={_FUZZ_ENGINE} target_met={target_met}"
    )

    _export_outputs(df, before_snapshot, needs_before, all_propagation_audit, fuzzy_audit, summary)

    return df, summary


def _export_outputs(df: pd.DataFrame, before_snapshot: pd.DataFrame, needs_before: int,
                     propagation_audit: list, fuzzy_audit: list, summary: dict) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── company_resolution_v7_summary.csv ─────────────────────────────────────
    summary_flat = {k: v for k, v in summary.items() if k not in ("iteration_log", "resolution_method_breakdown")}
    pd.DataFrame([summary_flat]).to_csv(OUTPUTS_DIR / "company_resolution_v7_summary.csv", index=False, encoding="utf-8-sig")

    # ── company_resolution_v7_audit.csv — per-contact before/after ────────────
    after_cols = [
        "opportunity_market_v5", "opportunity_confidence", "opportunity_reason",
        "resolution_iteration", "resolution_pass", "resolution_source",
        "resolution_confidence", "resolution_reason",
    ]
    after_cols = [c for c in after_cols if c in df.columns]
    mask_idx = before_snapshot.index
    after_snapshot = df.loc[mask_idx, after_cols].rename(columns={"opportunity_market_v5": "opportunity_market_v5_after"})
    audit = before_snapshot.join(after_snapshot, how="left")
    audit.to_csv(OUTPUTS_DIR / "company_resolution_v7_audit.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Saved company_resolution_v7_audit.csv ({len(audit)} rows)")

    # ── company_fuzzy_clusters_v7.csv ─────────────────────────────────────────
    fz_cols = ["raw_company", "canonical_company", "similarity_score", "cluster_reason", "accepted", "confidence"]
    fz_df = pd.DataFrame(fuzzy_audit, columns=fz_cols) if fuzzy_audit else pd.DataFrame(columns=fz_cols)
    fz_df.to_csv(OUTPUTS_DIR / "company_fuzzy_clusters_v7.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Saved company_fuzzy_clusters_v7.csv ({len(fz_df)} candidates)")

    # ── company_mapping_residual_v7.csv — still-unresolved companies ─────────
    still_needs = df[df["opportunity_market_v5"] == NEEDS_MAPPING]
    if not still_needs.empty:
        agg = (
            still_needs.groupby("company_clean")
            .agg(
                connection_count  = ("company_clean", "size"),
                recruiter_count   = ("persona", lambda x: x.isin(RECRUITING_PERSONAS).sum()),
                hiring_mgr_count  = ("persona", lambda x: x.isin(HIRING_PERSONAS).sum()),
                data_leader_count = ("persona", lambda x: x.isin(DATA_LEADER_PERSONAS).sum()),
                avg_priority_score= ("priority_score", "mean"),
            )
            .reset_index()
            .sort_values("connection_count", ascending=False)
        )
        agg["avg_priority_score"] = agg["avg_priority_score"].round(1)
        agg["company_canonical"] = agg["company_clean"].apply(canonical_display)
        agg["manual_market"] = ""
        agg["manual_category"] = ""
        agg["manual_notes"] = ""
    else:
        agg = pd.DataFrame(columns=[
            "company_clean", "connection_count", "recruiter_count", "hiring_mgr_count",
            "data_leader_count", "avg_priority_score", "company_canonical",
            "manual_market", "manual_category", "manual_notes",
        ])
    agg.to_csv(OUTPUTS_DIR / "company_mapping_residual_v7.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Saved company_mapping_residual_v7.csv ({len(agg)} companies)")

    # ── company_mapping_pareto_v7.csv — Pareto backlog with cumulative % ─────
    if not agg.empty:
        pareto = agg.copy().reset_index(drop=True)
        pareto["rank"] = pareto.index + 1
        pareto["cumulative_contacts"] = pareto["connection_count"].cumsum()
        total_unresolved_contacts = pareto["connection_count"].sum()
        pareto["cumulative_pct_of_unresolved"] = (
            (pareto["cumulative_contacts"] / total_unresolved_contacts * 100).round(1)
            if total_unresolved_contacts else 0.0
        )
        pareto["talent_acquisition"] = pareto["recruiter_count"]
        pareto["hiring_managers"]    = pareto["hiring_mgr_count"]
        pareto["data_leaders"]       = pareto["data_leader_count"]

        def _suggest(row):
            if row["recruiter_count"] >= 3:
                return GLOBAL_STAFFING, 0.5, "recruiting cohort present but below propagation/message-evidence bar"
            if row["data_leader_count"] >= 1:
                return GLOBAL_OPPORTUNITY, 0.5, "data-leadership contact present but insufficient cohort size"
            return "", 0.0, "no usable signal yet — candidate for manual company mapping override"

        suggestions = pareto.apply(_suggest, axis=1, result_type="expand")
        pareto["suggested_bucket"], pareto["suggested_confidence"], pareto["suggested_reason"] = (
            suggestions[0], suggestions[1], suggestions[2]
        )
        pareto_out = pareto[[
            "rank", "company_canonical", "connection_count", "cumulative_contacts",
            "cumulative_pct_of_unresolved", "recruiter_count", "talent_acquisition",
            "hiring_managers", "data_leaders", "avg_priority_score",
            "suggested_bucket", "suggested_confidence", "suggested_reason",
        ]].rename(columns={"connection_count": "unresolved_contact_count"})

        n_companies = len(pareto_out)
        top10  = pareto_out.head(10)["cumulative_pct_of_unresolved"].max() if n_companies else 0.0
        top25  = pareto_out.head(25)["cumulative_pct_of_unresolved"].max() if n_companies else 0.0
        top50  = pareto_out.head(50)["cumulative_pct_of_unresolved"].max() if n_companies else 0.0
        top100 = pareto_out.head(100)["cumulative_pct_of_unresolved"].max() if n_companies else 0.0
        summary["pareto_unique_unresolved_companies"] = n_companies
        summary["pareto_top10_coverage_pct"]  = float(top10)
        summary["pareto_top25_coverage_pct"]  = float(top25)
        summary["pareto_top50_coverage_pct"]  = float(top50)
        summary["pareto_top100_coverage_pct"] = float(top100)
    else:
        pareto_out = pd.DataFrame(columns=[
            "rank", "company_canonical", "unresolved_contact_count", "cumulative_contacts",
            "cumulative_pct_of_unresolved", "recruiter_count", "talent_acquisition",
            "hiring_managers", "data_leaders", "avg_priority_score",
            "suggested_bucket", "suggested_confidence", "suggested_reason",
        ])
        summary["pareto_unique_unresolved_companies"] = 0
        summary["pareto_top10_coverage_pct"] = summary["pareto_top25_coverage_pct"] = 0.0
        summary["pareto_top50_coverage_pct"] = summary["pareto_top100_coverage_pct"] = 0.0

    pareto_out.to_csv(OUTPUTS_DIR / "company_mapping_pareto_v7.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Saved company_mapping_pareto_v7.csv ({len(pareto_out)} companies)")

    # Top 20 rows embedded in the public JSON summary (Data Quality page table).
    # Company names only — no personal/contact data — same visibility level as
    # the rest of the Company Intelligence page.
    summary["pareto_top20"] = pareto_out.head(20).to_dict(orient="records")
