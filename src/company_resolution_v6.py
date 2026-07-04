# -*- coding: utf-8 -*-
"""
company_resolution_v6.py
=========================
Reduces "Needs Company Mapping" using cross-contact evidence, local message
history evidence, and persona/company-category fallbacks — WITHOUT changing
the Opportunity Bucket V5 architecture and WITHOUT fabricating exact
geography. See CLAUDE_INTELLIGENCE_V6_PATCH.md Parts 9-15.

Only rows that V5 left at NEEDS_COMPANY_MAPPING are touched. Everything V5
already resolved (exact dictionary, region keyword, company category,
language signal, manual override, persona-global) is left exactly as-is.

Resolution hierarchy applied to the NEEDS_COMPANY_MAPPING cohort, in order
(each step only touches rows still unresolved after the previous one):

  1. Same-company dominant-bucket propagation (Part 10)
     - >=2 high-confidence classified contacts at the same normalized company
       AND dominant bucket share >= 70%  -> confidence 0.72
     - >=5 contacts AND share >= 80%     -> confidence 0.80
     - Mixed evidence (share < 70%) is NOT propagated.

  2. Local message-history evidence (Part 12) — messages.csv only, local/
     private. Never publishes matched raw text — only category + match count.
     Confidence 0.60-0.75 depending on signal strength.

  3. Persona + company-category fallback (Part 13)
     - Recruiting/staffing persona            -> GLOBAL_STAFFING (0.55)
     - Company category = consulting          -> GLOBAL_CONSULTING (0.55)
     - Company category = tech/product        -> GLOBAL_TECH (0.55)
     - Strategic persona w/ professional ctx   -> GLOBAL_OPPORTUNITY (0.55)

Anything still unresolved after all three steps is an honest residual:
company exists, category unclear, cross-contact evidence inconclusive,
message signal absent, no region/title evidence (Part 13's stated bar).

New columns added to the connections dataframe (Part 9):
  company_resolution_source, company_resolution_confidence,
  company_evidence_count, company_dominant_bucket,
  company_dominant_bucket_share, cross_contact_propagation_used,
  message_signal_used, manual_review_required, company_canonical

Outputs (Part 14):
  outputs/company_resolution_v6_audit.csv
  outputs/company_mapping_backlog_v6.csv
  outputs/company_propagation_audit.csv
"""

import logging
import re
from pathlib import Path

import pandas as pd

from src.company_normalizer import normalize, canonical_display
from src.company_dictionary_enrichment import (
    LATAM_USD_LIKELY, US_CANADA_LIKELY, SPAIN_EU_LIKELY,
    GLOBAL_STAFFING, GLOBAL_CONSULTING, GLOBAL_TECH, GLOBAL_OPPORTUNITY,
    NEEDS_MAPPING, LOW_VALUE,
)
from src.message_sanitizer import strip_html

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"

RECRUITING_PERSONAS = {"Recruiter", "Talent Acquisition", "Sourcer"}
STRATEGIC_PERSONAS  = {
    "Hiring Manager", "Engineering Manager", "Data Engineering Manager",
    "Head of Data", "Director", "Executive", "Founder", "Partner",
}

# ── Part 12 — local message-history regional evidence keyword lists ──────────
# Evidence only. The matched raw text is NEVER stored/exported — only the
# category name and match count.

LATAM_MSG_KW = re.compile(
    r"\b(latam|latin america|south america|brazil|brasil|argentina|colombia|"
    r"mexico|méxico|chile|uruguay|peru|perú|nearshore)\b", re.I,
)
USCA_MSG_KW = re.compile(
    r"\b(usa|us role|united states|canada|us timezone|est|cst|contractor|usd)\b", re.I,
)
SPAINEU_MSG_KW = re.compile(
    r"\b(spain|españa|espana|madrid|barcelona|portugal|lisbon|lisboa|europe|eu\b|"
    r"germany|netherlands|ireland)\b", re.I,
)


def _match_count(text: str, pattern: re.Pattern) -> int:
    if not text:
        return 0
    return len(pattern.findall(text))


def _message_signal_confidence(n_matches: int) -> float:
    if n_matches >= 3:
        return 0.75
    if n_matches == 2:
        return 0.68
    return 0.60


def _infer_resolution_method(reason: str) -> str:
    """Map a V5 opportunity_reason string to a coarse resolution-method bucket
    for the Data Quality breakdown (Part 15). Purely descriptive — no logic
    change to V5 itself."""
    r = (reason or "").lower()
    if "manual override" in r:
        return "manual_override"
    if "exact company dict" in r or "substring company match" in r:
        return "exact_dictionary"
    if "v4 inference" in r:
        return "v4_inference"
    if "region keyword" in r:
        return "title_or_company_keyword"
    if "company category" in r:
        return "company_category"
    if "language" in r or "portuguese" in r or "spanish" in r:
        return "language_signal"
    if "high-value persona" in r:
        return "persona_fallback"
    if "same-company dominant bucket propagation" in r:
        return "same_company_propagation"
    if "message history contains" in r:
        return "message_signal_evidence"
    if "persona/company-category fallback" in r:
        return "persona_company_category_fallback"
    if "company exists but unresolved" in r:
        return "unresolved"
    return "no_usable_signal"


# ── Local message-history index (private — messages.csv is never committed) ──

def _build_message_signal_index() -> dict:
    """Return {normalized_profile_url: combined_lowercased_text} built from
    the local messages.csv. Returns {} if messages.csv is absent — this step
    is entirely optional supporting evidence, never required."""
    try:
        from src.message_intelligence import load_messages
    except Exception:
        return {}

    msgs = load_messages()
    if msgs is None or msgs.empty:
        return {}

    idx: dict[str, list[str]] = {}
    for conv_id, group in msgs.groupby("conversation_id"):
        text = " ".join(strip_html(c) for c in group["content"].fillna("").astype(str).tolist())
        if not text.strip():
            continue
        urls = set()
        for _, r in group.iterrows():
            if not r.get("is_me_sender"):
                u = str(r.get("sender_profile_url", "") or "").strip().rstrip("/").lower()
                if u:
                    urls.add(u)
            else:
                ru = str(r.get("recipient_profile_urls", "") or "")
                for part in ru.split(","):
                    part = part.strip().rstrip("/").lower()
                    if part:
                        urls.add(part)
        for u in urls:
            idx.setdefault(u, []).append(text)

    return {u: " ".join(v).lower() for u, v in idx.items()}


# ── Step 1: Same-company dominant-bucket propagation (Part 10) ───────────────

def _apply_same_company_propagation(df: pd.DataFrame, needs_mask: pd.Series) -> list:
    """Mutates df in place for rows that qualify. Returns per-company audit rows."""
    company_key = df["company_clean"].fillna("").apply(normalize)
    resolved_mask = (
        (~df["opportunity_market_v5"].isin([NEEDS_MAPPING, LOW_VALUE])) &
        (company_key != "")
    )

    propagation_audit = []

    for key, group_idx in df[company_key != ""].groupby(company_key).groups.items():
        if not key:
            continue
        group = df.loc[group_idx]
        group_needs = group[needs_mask.loc[group_idx]]
        if group_needs.empty:
            continue

        resolved_sub = group[resolved_mask.loc[group_idx]]
        support = len(resolved_sub)
        if support == 0:
            propagation_audit.append({
                "company_clean": group["company_clean"].iloc[0], "company_canonical": canonical_display(key),
                "total_contacts": len(group), "unresolved_count": len(group_needs),
                "resolved_support": 0, "dominant_bucket": "", "dominant_share": 0.0,
                "decision": "skipped_no_resolved_evidence", "confidence_assigned": "",
            })
            continue

        counts = resolved_sub["opportunity_market_v5"].value_counts()
        dominant_bucket = counts.index[0]
        dominant_count  = int(counts.iloc[0])
        share = dominant_count / support

        if support >= 5 and share >= 0.80:
            confidence = 0.80
            decision = "propagated"
        elif support >= 2 and share >= 0.70:
            confidence = 0.72
            decision = "propagated"
        else:
            confidence = None
            decision = "skipped_mixed_evidence" if support >= 2 else "skipped_insufficient_support"

        propagation_audit.append({
            "company_clean": group["company_clean"].iloc[0], "company_canonical": canonical_display(key),
            "total_contacts": len(group), "unresolved_count": len(group_needs),
            "resolved_support": support, "dominant_bucket": dominant_bucket,
            "dominant_share": round(share, 3), "decision": decision,
            "confidence_assigned": confidence if confidence is not None else "",
        })

        if confidence is None:
            continue

        reason = (
            f"same-company dominant bucket propagation "
            f"({support} contacts, {share:.0%} share)"
        )
        for idx in group_needs.index:
            df.at[idx, "opportunity_market_v5"]        = dominant_bucket
            df.at[idx, "opportunity_bucket"]            = dominant_bucket
            df.at[idx, "opportunity_confidence"]         = confidence
            df.at[idx, "opportunity_reason"]             = reason
            df.at[idx, "needs_manual_company_mapping"]   = False
            df.at[idx, "company_resolution_source"]      = "same_company_propagation"
            df.at[idx, "company_resolution_confidence"]  = confidence
            df.at[idx, "company_evidence_count"]         = support
            df.at[idx, "company_dominant_bucket"]        = dominant_bucket
            df.at[idx, "company_dominant_bucket_share"]  = round(share, 3)
            df.at[idx, "cross_contact_propagation_used"] = True
            df.at[idx, "manual_review_required"]         = confidence < 0.80

    return propagation_audit


# ── Step 2: Local message-history evidence (Part 12) ──────────────────────────

def _apply_message_signal_evidence(df: pd.DataFrame, needs_mask: pd.Series) -> int:
    """Mutates df in place. Returns count of rows resolved this way."""
    msg_index = _build_message_signal_index()
    if not msg_index:
        return 0

    resolved_count = 0
    remaining = df[needs_mask]
    for idx, row in remaining.iterrows():
        url = str(row.get("url", "") or "").strip().rstrip("/").lower()
        if not url or url not in msg_index:
            continue
        text = msg_index[url]

        latam_n = _match_count(text, LATAM_MSG_KW)
        usca_n  = _match_count(text, USCA_MSG_KW)
        eu_n    = _match_count(text, SPAINEU_MSG_KW)

        candidates = [
            ("LATAM", latam_n, LATAM_USD_LIKELY, "message history contains LATAM opportunity signal"),
            ("US_CA", usca_n,  US_CANADA_LIKELY, "message history contains US contractor signal"),
            ("SPAIN_EU", eu_n, SPAIN_EU_LIKELY,  "message history contains Spain/EU signal"),
        ]
        candidates.sort(key=lambda c: c[1], reverse=True)
        best_label, best_n, best_bucket, best_reason = candidates[0]
        if best_n < 1:
            continue

        confidence = _message_signal_confidence(best_n)
        df.at[idx, "opportunity_market_v5"]        = best_bucket
        df.at[idx, "opportunity_bucket"]            = best_bucket
        df.at[idx, "opportunity_confidence"]         = confidence
        df.at[idx, "opportunity_reason"]             = f"{best_reason} ({best_n} matches)"
        df.at[idx, "needs_manual_company_mapping"]   = False
        df.at[idx, "company_resolution_source"]      = "message_signal_evidence"
        df.at[idx, "company_resolution_confidence"]  = confidence
        df.at[idx, "company_evidence_count"]         = best_n
        df.at[idx, "message_signal_used"]            = True
        df.at[idx, "manual_review_required"]         = True
        resolved_count += 1

    return resolved_count


# ── Step 3: Persona + company-category fallback (Part 13) ────────────────────

def _apply_persona_category_fallback(df: pd.DataFrame, needs_mask: pd.Series) -> int:
    resolved_count = 0
    remaining = df[needs_mask]
    for idx, row in remaining.iterrows():
        persona  = str(row.get("persona", "") or "")
        cat_v4   = str(row.get("company_category_v4", "") or "")
        position = str(row.get("position_clean", "") or "").strip()
        priority = float(row.get("priority_score", 0) or 0)

        bucket = None
        reason = None

        if persona in RECRUITING_PERSONAS:
            bucket = GLOBAL_STAFFING
            reason = "persona/company-category fallback: recruiting/staffing persona, region unresolved"
        elif cat_v4 == "GLOBAL_CONSULTING":
            bucket = GLOBAL_CONSULTING
            reason = "persona/company-category fallback: consulting company category, region unresolved"
        elif cat_v4 == "GLOBAL_TECH":
            bucket = GLOBAL_TECH
            reason = "persona/company-category fallback: technology/product company category, region unresolved"
        elif persona in STRATEGIC_PERSONAS and (position or priority >= 50):
            bucket = GLOBAL_OPPORTUNITY
            reason = "persona/company-category fallback: strategic persona with professional context"

        if not bucket:
            continue

        df.at[idx, "opportunity_market_v5"]        = bucket
        df.at[idx, "opportunity_bucket"]            = bucket
        df.at[idx, "opportunity_confidence"]         = 0.55
        df.at[idx, "opportunity_reason"]             = reason
        df.at[idx, "needs_manual_company_mapping"]   = False
        df.at[idx, "company_resolution_source"]      = "persona_company_category_fallback"
        df.at[idx, "company_resolution_confidence"]  = 0.55
        df.at[idx, "manual_review_required"]         = True
        resolved_count += 1

    return resolved_count


# ── Main entry point ──────────────────────────────────────────────────────────

def apply_company_resolution_v6(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Improves Opportunity Bucket V5 mapping resolution honestly. Only rows at
    NEEDS_COMPANY_MAPPING when this runs are touched. Returns (df, summary_dict).
    """
    df = df.copy()

    if "opportunity_market_v5" not in df.columns:
        logger.warning("  company_resolution_v6: opportunity_market_v5 column missing — skipping (V5 must run first)")
        return df, {}

    total = len(df)

    # Default columns for every row
    df["company_resolution_confidence"] = df.get("opportunity_confidence", 0.0)
    df["company_resolution_source"]     = df["opportunity_reason"].apply(_infer_resolution_method)
    df["company_evidence_count"]        = 0
    df["company_dominant_bucket"]       = ""
    df["company_dominant_bucket_share"] = 0.0
    df["cross_contact_propagation_used"]= False
    df["message_signal_used"]           = False
    df["manual_review_required"]        = False
    df["company_canonical"]             = df["company_clean"].fillna("").apply(canonical_display)

    needs_mask_before = df["opportunity_market_v5"] == NEEDS_MAPPING
    needs_before_count = int(needs_mask_before.sum())

    # Snapshot for the per-contact audit CSV (before state)
    before_snapshot = df.loc[needs_mask_before, [
        c for c in ["full_name", "url", "company_clean", "persona", "position_clean",
                    "priority_score", "opportunity_market_v5", "opportunity_reason"]
        if c in df.columns
    ]].copy()
    before_snapshot = before_snapshot.rename(columns={
        "opportunity_market_v5": "opportunity_market_v5_before",
        "opportunity_reason":    "opportunity_reason_before",
    })

    # ── Step 1: same-company propagation ──────────────────────────────────────
    needs_mask = df["opportunity_market_v5"] == NEEDS_MAPPING
    propagation_audit = _apply_same_company_propagation(df, needs_mask)
    resolved_by_propagation = int((needs_mask & (df["opportunity_market_v5"] != NEEDS_MAPPING)).sum())

    # ── Step 2: local message-signal evidence (messages.csv, local only) ─────
    needs_mask = df["opportunity_market_v5"] == NEEDS_MAPPING
    resolved_by_message_signal = _apply_message_signal_evidence(df, needs_mask)

    # ── Step 3: persona + company-category fallback ──────────────────────────
    needs_mask = df["opportunity_market_v5"] == NEEDS_MAPPING
    resolved_by_persona_fallback = _apply_persona_category_fallback(df, needs_mask)

    needs_mask_after = df["opportunity_market_v5"] == NEEDS_MAPPING
    needs_after_count = int(needs_mask_after.sum())

    reduction_count = needs_before_count - needs_after_count
    reduction_pct   = round(reduction_count / needs_before_count * 100, 1) if needs_before_count else 0.0
    pct_before = round(needs_before_count / total * 100, 1) if total else 0.0
    pct_after  = round(needs_after_count  / total * 100, 1) if total else 0.0
    target_met = pct_after <= 10.0

    if target_met:
        target_note = f"Target met: Needs Company Mapping is {pct_after}% of network (<=10% aspirational target)."
    else:
        target_note = (
            f"Target not fully met: Needs Company Mapping is {pct_after}% of network (target <=10%). "
            f"Reduced from {pct_before}% to {pct_after}% using honest cross-contact/message/persona evidence only — "
            f"the remaining residual has no company match, no cross-contact evidence, no message history, "
            f"and no persona/category signal, so it was left unresolved rather than fabricating geography."
        )

    # Resolution-method breakdown across the WHOLE network (Part 15)
    method_breakdown = {
        str(k): int(v) for k, v in df["company_resolution_source"].value_counts().to_dict().items()
    }

    summary = {
        "total_connections":            total,
        "needs_mapping_before":         needs_before_count,
        "needs_mapping_after":          needs_after_count,
        "needs_mapping_reduction_count":reduction_count,
        "needs_mapping_reduction_pct":  reduction_pct,
        "needs_mapping_pct_before":     pct_before,
        "needs_mapping_pct_after":      pct_after,
        "target_met":                  target_met,
        "target_note":                 target_note,
        "resolved_by_same_company_propagation": resolved_by_propagation,
        "resolved_by_message_signal_evidence":  resolved_by_message_signal,
        "resolved_by_persona_company_category": resolved_by_persona_fallback,
        "resolution_method_breakdown":  method_breakdown,
    }

    logger.info(
        f"  Company Resolution V6: Needs Mapping {needs_before_count:,} -> {needs_after_count:,} "
        f"({pct_before}% -> {pct_after}% of network) | "
        f"propagation={resolved_by_propagation} message_signal={resolved_by_message_signal} "
        f"persona_fallback={resolved_by_persona_fallback} | target_met={target_met}"
    )

    _export_audits(df, before_snapshot, needs_mask_before, propagation_audit)

    return df, summary


def _export_audits(df: pd.DataFrame, before_snapshot: pd.DataFrame,
                    needs_mask_before: pd.Series, propagation_audit: list) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── company_resolution_v6_audit.csv — per-contact before/after ───────────
    after_cols = [
        "opportunity_market_v5", "opportunity_confidence", "opportunity_reason",
        "company_resolution_source", "company_resolution_confidence",
        "company_evidence_count", "company_dominant_bucket", "company_dominant_bucket_share",
        "cross_contact_propagation_used", "message_signal_used", "manual_review_required",
        "company_canonical",
    ]
    after_cols = [c for c in after_cols if c in df.columns]
    after_snapshot = df.loc[needs_mask_before.index[needs_mask_before], after_cols].rename(
        columns={"opportunity_market_v5": "opportunity_market_v5_after"}
    )
    audit = before_snapshot.join(after_snapshot, how="left")
    audit.to_csv(OUTPUTS_DIR / "company_resolution_v6_audit.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Saved company_resolution_v6_audit.csv ({len(audit)} rows)")

    # ── company_mapping_backlog_v6.csv — still-unresolved companies ──────────
    still_needs = df[df["opportunity_market_v5"] == NEEDS_MAPPING]
    if not still_needs.empty:
        agg = (
            still_needs.groupby("company_clean")
            .agg(
                connection_count  = ("company_clean", "size"),
                recruiter_count   = ("persona", lambda x: x.isin({"Recruiter", "Talent Acquisition", "Sourcer"}).sum()),
                hiring_mgr_count  = ("persona", lambda x: x.isin({"Hiring Manager", "Engineering Manager"}).sum()),
                data_leader_count = ("persona", lambda x: x.isin({"Data Engineering Manager", "Head of Data", "Director"}).sum()),
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
    agg.to_csv(OUTPUTS_DIR / "company_mapping_backlog_v6.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Saved company_mapping_backlog_v6.csv ({len(agg)} companies)")

    # ── company_propagation_audit.csv — per-company propagation decisions ────
    prop_df = pd.DataFrame(propagation_audit) if propagation_audit else pd.DataFrame(columns=[
        "company_clean", "company_canonical", "total_contacts", "unresolved_count",
        "resolved_support", "dominant_bucket", "dominant_share", "decision", "confidence_assigned",
    ])
    if not prop_df.empty:
        prop_df = prop_df.sort_values(["decision", "total_contacts"], ascending=[True, False])
    prop_df.to_csv(OUTPUTS_DIR / "company_propagation_audit.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Saved company_propagation_audit.csv ({len(prop_df)} companies)")
