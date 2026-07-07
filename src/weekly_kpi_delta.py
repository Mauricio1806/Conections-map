# -*- coding: utf-8 -*-
"""
weekly_kpi_delta.py
====================
Post-pipeline step for the weekly snapshot refresh (run AFTER
build_strategy_layer.py, BEFORE generate_static_dashboard.py).

Compares the previous snapshot's dashboard baseline (stashed by
weekly_snapshot_refresh.py's caller into
data/processed/_previous_snapshot_baseline.json before the pipeline
overwrote outputs/public_dashboard_data.json) against the freshly generated
current snapshot, and:

  1. Enriches outputs/weekly_new_connections.csv with classified fields
     (persona, seniority, opportunity_bucket, priority_score,
     outreach_adjusted_score where available) by joining against the
     just-generated outputs/enriched_connections.csv and top_contacts.
  2. Writes outputs/weekly_kpi_delta.csv (Part 12): NETWORK / PERSONAS /
     STRATEGIC NETWORK / MESSAGE INTELLIGENCE, each with previous_value,
     current_value, absolute_delta, percentage_delta.
  3. Builds a compact "weekly_evolution" block and merges it into
     outputs/public_dashboard_data.json (and docs/assets/dashboard_data.json
     if already copied) so generate_static_dashboard.py's later copy step
     picks it up, or so it's already present if run before that copy.

If no previous baseline stash exists (first-ever run), deltas are reported
as previous_value=0 and this is noted rather than fabricated.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"
PREVIOUS_BASELINE_PATH = ROOT / "data" / "processed" / "_previous_snapshot_baseline.json"
PUBLIC_JSON_OUTPUTS = OUTPUTS_DIR / "public_dashboard_data.json"
PUBLIC_JSON_DOCS = ROOT / "docs" / "assets" / "dashboard_data.json"


def _pct_delta(prev, cur) -> float | None:
    if prev in (None, 0) or prev == "":
        return None
    try:
        return round((cur - prev) / prev * 100, 1)
    except Exception:
        return None


def _kpi_row(category: str, metric: str, prev, cur) -> dict:
    prev = prev or 0
    cur = cur or 0
    return {
        "category": category, "metric": metric,
        "previous_value": prev, "current_value": cur,
        "absolute_delta": cur - prev,
        "percentage_delta": _pct_delta(prev, cur),
    }


def enrich_weekly_new_connections(current: dict) -> pd.DataFrame:
    """Part 6 (enrichment) — join the raw new-connections list against the
    freshly classified enriched_connections.csv + top_contacts (outreach
    score, where available) so 'new this week' contacts show real
    persona/opportunity_bucket/priority_score instead of just raw fields."""
    new_path = OUTPUTS_DIR / "weekly_new_connections.csv"
    if not new_path.exists():
        logger.warning("  outputs/weekly_new_connections.csv not found — skipping enrichment")
        return pd.DataFrame()

    new_df = pd.read_csv(new_path, dtype=str, encoding="utf-8-sig")
    if new_df.empty:
        return new_df

    # Idempotent: drop any enrichment columns from a prior run of this script
    # before re-merging, so re-running it doesn't create _x/_y suffix clashes.
    new_df = new_df.drop(columns=[c for c in [
        "persona", "seniority", "opportunity_bucket", "priority_score",
        "company_canonical", "outreach_adjusted_score",
    ] if c in new_df.columns])

    enriched_path = OUTPUTS_DIR / "enriched_connections.csv"
    if enriched_path.exists():
        enriched = pd.read_csv(enriched_path, low_memory=False)
        enriched["_url_norm"] = enriched["url"].fillna("").str.strip().str.rstrip("/").str.lower()
        new_df["_url_norm"] = new_df["url"].fillna("").str.strip().str.rstrip("/").str.lower()
        keep_cols = ["_url_norm", "persona", "seniority", "opportunity_bucket", "priority_score", "company_canonical"]
        keep_cols = [c for c in keep_cols if c in enriched.columns]
        new_df = new_df.merge(enriched[keep_cols], on="_url_norm", how="left")

    outreach_by_url = {}
    for c in current.get("top_contacts", []):
        u = str(c.get("url", "") or "").strip().rstrip("/").lower()
        if u:
            outreach_by_url[u] = c.get("outreach_adjusted_score")
    new_df["outreach_adjusted_score"] = new_df["_url_norm"].map(outreach_by_url) if "_url_norm" in new_df.columns else None

    new_df = new_df.drop(columns=["_url_norm"], errors="ignore")
    new_df = new_df.sort_values("priority_score", ascending=False, na_position="last")
    new_df.to_csv(new_path, index=False, encoding="utf-8-sig")
    logger.info(f"  Enriched weekly_new_connections.csv ({len(new_df)} rows) with persona/opportunity_bucket/priority_score")
    return new_df


def build_kpi_delta(previous: dict, current: dict) -> pd.DataFrame:
    rows = []
    pk, ck = previous.get("kpis", {}), current.get("kpis", {})
    pv5, cv5 = previous.get("opportunity_market_v5_summary", {}), current.get("opportunity_market_v5_summary", {})
    plr, clr = previous.get("lead_reactivation", {}) or {}, current.get("lead_reactivation", {}) or {}

    # NETWORK
    rows.append(_kpi_row("NETWORK", "total_connections", pk.get("total_connections"), ck.get("total_connections")))
    rows.append(_kpi_row("NETWORK", "high_priority", pk.get("high_priority"), ck.get("high_priority")))
    rows.append(_kpi_row("NETWORK", "medium_priority", pk.get("medium_priority"), ck.get("medium_priority")))
    rows.append(_kpi_row("NETWORK", "actionable_contacts", pv5.get("v5_actionable_total"), cv5.get("v5_actionable_total")))

    # PERSONAS
    rows.append(_kpi_row("PERSONAS", "recruiters", pk.get("recruiters_total"), ck.get("recruiters_total")))
    rows.append(_kpi_row("PERSONAS", "talent_acquisition_hr", pk.get("talent_hr_total"), ck.get("talent_hr_total")))
    rows.append(_kpi_row("PERSONAS", "hiring_managers", pk.get("hiring_managers_total"), ck.get("hiring_managers_total")))
    rows.append(_kpi_row("PERSONAS", "data_leaders", pk.get("data_leaders_total"), ck.get("data_leaders_total")))
    rows.append(_kpi_row("PERSONAS", "data_peers", pk.get("data_peers_total"), ck.get("data_peers_total")))

    # STRATEGIC NETWORK (Opportunity Bucket V5)
    rows.append(_kpi_row("STRATEGIC", "latam_usd", pv5.get("v5_latam_usd_confirmed", 0) + pv5.get("v5_latam_usd_likely", 0),
                          cv5.get("v5_latam_usd_confirmed", 0) + cv5.get("v5_latam_usd_likely", 0)))
    rows.append(_kpi_row("STRATEGIC", "us_canada_nearshore", pv5.get("v5_us_canada_confirmed", 0) + pv5.get("v5_us_canada_likely", 0),
                          cv5.get("v5_us_canada_confirmed", 0) + cv5.get("v5_us_canada_likely", 0)))
    rows.append(_kpi_row("STRATEGIC", "spain_eu", pv5.get("v5_spain_eu_confirmed", 0) + pv5.get("v5_spain_eu_likely", 0),
                          cv5.get("v5_spain_eu_confirmed", 0) + cv5.get("v5_spain_eu_likely", 0)))
    rows.append(_kpi_row("STRATEGIC", "europe", pv5.get("v5_europe_confirmed", 0) + pv5.get("v5_europe_likely", 0),
                          cv5.get("v5_europe_confirmed", 0) + cv5.get("v5_europe_likely", 0)))
    rows.append(_kpi_row("STRATEGIC", "global_staffing", pv5.get("v5_global_staffing"), cv5.get("v5_global_staffing")))
    rows.append(_kpi_row("STRATEGIC", "global_consulting", pv5.get("v5_global_consulting"), cv5.get("v5_global_consulting")))
    rows.append(_kpi_row("STRATEGIC", "global_tech", pv5.get("v5_global_tech"), cv5.get("v5_global_tech")))
    rows.append(_kpi_row("STRATEGIC", "global_opportunity", pv5.get("v5_global_opportunity"), cv5.get("v5_global_opportunity")))
    rows.append(_kpi_row("STRATEGIC", "needs_company_mapping", pv5.get("v5_needs_company_mapping"), cv5.get("v5_needs_company_mapping")))

    # MESSAGE INTELLIGENCE
    rows.append(_kpi_row("MESSAGE_INTELLIGENCE", "conversations_analyzed", plr.get("total_conversations"), clr.get("total_conversations")))
    rows.append(_kpi_row("MESSAGE_INTELLIGENCE", "needs_my_response", plr.get("needs_my_response"), clr.get("needs_my_response")))
    rows.append(_kpi_row("MESSAGE_INTELLIGENCE", "hot_reactivation", plr.get("hot_reactivation_leads"), clr.get("hot_reactivation_leads")))
    rows.append(_kpi_row("MESSAGE_INTELLIGENCE", "warm_reactivation", plr.get("warm_reactivation_leads"), clr.get("warm_reactivation_leads")))
    rows.append(_kpi_row("MESSAGE_INTELLIGENCE", "dormant_warm", plr.get("dormant_warm_leads"), clr.get("dormant_warm_leads")))
    rows.append(_kpi_row("MESSAGE_INTELLIGENCE", "follow_ups_due", plr.get("follow_up_due"), clr.get("follow_up_due")))
    rows.append(_kpi_row("MESSAGE_INTELLIGENCE", "no_response", plr.get("no_response_leads"), clr.get("no_response_leads")))

    # Interview Pipeline — only meaningful once a previous snapshot actually
    # persisted this full-network aggregate (added this patch). Omitted
    # entirely rather than showing a misleading delta against an unknown
    # baseline; will be tracked starting from the NEXT weekly refresh.
    p_outreach, c_outreach = previous.get("outreach_summary"), current.get("outreach_summary")
    if p_outreach is not None and c_outreach is not None:
        rows.append(_kpi_row("MESSAGE_INTELLIGENCE", "interview_pipeline",
                              p_outreach.get("interview_pipeline_count"), c_outreach.get("interview_pipeline_count")))

    return pd.DataFrame(rows, columns=["category", "metric", "previous_value", "current_value", "absolute_delta", "percentage_delta"])


def _top_new_high_value(new_connections_df: pd.DataFrame, n: int = 10) -> list:
    if new_connections_df.empty or "priority_score" not in new_connections_df.columns:
        return []
    df = new_connections_df.copy()
    df["priority_score"] = pd.to_numeric(df["priority_score"], errors="coerce").fillna(0)
    top = df.sort_values("priority_score", ascending=False).head(n)
    cols = [c for c in ["first_name", "last_name", "company", "persona", "opportunity_bucket", "priority_score", "url"] if c in top.columns]
    return top[cols].to_dict(orient="records")


def _weekly_diagnosis(kpi_df: pd.DataFrame, current: dict) -> dict:
    strategic = kpi_df[kpi_df["category"] == "STRATEGIC"].set_index("metric")
    growth_areas = strategic["absolute_delta"].sort_values(ascending=False)
    strongest = growth_areas.index[0] if len(growth_areas) and growth_areas.iloc[0] > 0 else None
    weakest_candidates = growth_areas[growth_areas.index != "needs_company_mapping"]
    weakest = weakest_candidates.index[-1] if len(weakest_candidates) else None

    latam_delta = float(strategic.loc["latam_usd", "absolute_delta"]) if "latam_usd" in strategic.index else 0
    us_delta = float(strategic.loc["us_canada_nearshore", "absolute_delta"]) if "us_canada_nearshore" in strategic.index else 0
    spain_delta = float(strategic.loc["spain_eu", "absolute_delta"]) if "spain_eu" in strategic.index else 0
    latam_us_total = latam_delta + us_delta
    allocation_note = "insufficient movement this week to assess 90/10 allocation"
    if (latam_us_total + spain_delta) > 0:
        latam_us_share = round(latam_us_total / (latam_us_total + spain_delta) * 100, 1) if (latam_us_total + spain_delta) else 0
        on_track = latam_us_share >= 80
        allocation_note = (
            f"this week's new LATAM/USD+US-nearshore growth is {latam_us_share}% of combined "
            f"LATAM/US/Spain-EU growth ({'on track' if on_track else 'below'} the 90/10 target)"
        )

    lr = current.get("lead_reactivation", {}) or {}
    needs_response = lr.get("needs_my_response", 0)
    next_action = (
        f"Reply to the {needs_response} 'Needs my response' contacts first (Lead Reactivation queue)"
        if needs_response else "No confirmed/likely response-needed leads this week — focus on This Week Queue outreach"
    )

    return {
        "strongest_growth_area": strongest,
        "weakest_strategic_area": weakest,
        "allocation_90_10_note": allocation_note,
        "highest_value_next_action": next_action,
    }


def build_weekly_evolution(previous: dict, current: dict, snapshot_meta: dict) -> dict:
    kpi_df = build_kpi_delta(previous, current)
    kpi_df.to_csv(OUTPUTS_DIR / "weekly_kpi_delta.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Saved weekly_kpi_delta.csv ({len(kpi_df)} metrics)")

    new_conn_df = enrich_weekly_new_connections(current)

    def _row(metric, category=None):
        sub = kpi_df[kpi_df["metric"] == metric]
        if category:
            sub = sub[sub["category"] == category]
        if sub.empty:
            return {"previous_value": 0, "current_value": 0, "absolute_delta": 0}
        r = sub.iloc[0]
        return {"previous_value": r["previous_value"], "current_value": r["current_value"], "absolute_delta": r["absolute_delta"]}

    evolution = {
        "current_snapshot_label": snapshot_meta["current_label"],
        "previous_snapshot_label": snapshot_meta["previous_label"],
        "network_growth": {
            "previous_connections": _row("total_connections")["previous_value"],
            "current_connections": _row("total_connections")["current_value"],
            "net_growth": _row("total_connections")["absolute_delta"],
            "new_connections": snapshot_meta.get("new_connections_count", 0),
        },
        "strategic_growth": {
            "new_recruiters": _row("recruiters")["absolute_delta"],
            "new_talent_acquisition": _row("talent_acquisition_hr")["absolute_delta"],
            "new_hiring_managers": _row("hiring_managers")["absolute_delta"],
            "new_data_leaders": _row("data_leaders")["absolute_delta"],
        },
        "market_movement": {
            "latam_usd_delta": _row("latam_usd")["absolute_delta"],
            "us_canada_nearshore_delta": _row("us_canada_nearshore")["absolute_delta"],
            "spain_eu_delta": _row("spain_eu")["absolute_delta"],
            "global_opportunity_delta": _row("global_opportunity")["absolute_delta"],
            "needs_mapping_delta": _row("needs_company_mapping")["absolute_delta"],
        },
        "lead_pipeline_movement": {
            "needs_my_response_delta": _row("needs_my_response")["absolute_delta"],
            "hot_leads_delta": _row("hot_reactivation")["absolute_delta"],
            "warm_leads_delta": _row("warm_reactivation")["absolute_delta"],
            "follow_ups_due_delta": _row("follow_ups_due")["absolute_delta"],
            "interview_pipeline_delta": (
                _row("interview_pipeline", "MESSAGE_INTELLIGENCE")["absolute_delta"]
                if not kpi_df[kpi_df["metric"] == "interview_pipeline"].empty else None
            ),
        },
        "new_high_value_connections": _top_new_high_value(new_conn_df),
        "diagnosis": _weekly_diagnosis(kpi_df, current),
        "untapped_network_movement": _build_untapped_movement(previous, current),
    }
    return evolution


def _build_untapped_movement(previous: dict, current: dict) -> dict:
    """Part 22 — Untapped Network deltas. Only meaningful once a PREVIOUS
    snapshot actually persisted an untapped_network.summary (this feature's
    first run never has one) — gracefully degrades rather than fabricating
    a delta against an unknown baseline, same pattern as interview_pipeline
    in _build_kpi_delta above."""
    p_un = (previous.get("untapped_network") or {}).get("summary")
    c_un = (current.get("untapped_network") or {}).get("summary") or {}
    if not c_un:
        return {"available": False}
    if not p_un:
        return {
            "available": True, "tracked_since_last_snapshot": False,
            "current_never_contacted_confirmed": c_un.get("never_contacted_confirmed", 0),
            "current_high_value_untapped": c_un.get("high_value_untapped", 0),
            "note": "Untapped Network tracking starts this snapshot — week-over-week deltas "
                    "will be available from the next weekly refresh onward.",
        }

    def _d(key):
        return (c_un.get(key, 0) or 0) - (p_un.get(key, 0) or 0)

    # "Activated this week" = was never-contacted-confirmed last snapshot,
    # now has a conversation. We only have aggregate counts (not identity-
    # level snapshot diffing) here, so this is reported as a bounded estimate
    # from the aggregate delta, never asserted as an exact person-level fact.
    activated_estimate = max(0, -(_d("never_contacted_confirmed")))

    return {
        "available": True, "tracked_since_last_snapshot": True,
        "never_contacted_confirmed_delta": _d("never_contacted_confirmed"),
        "high_value_untapped_delta": _d("high_value_untapped"),
        "untapped_recruiters_delta": _d("recruiters_untapped"),
        "untapped_hiring_managers_delta": _d("hiring_managers_untapped"),
        "untapped_contacts_activated_this_week_estimate": activated_estimate,
    }


def main():
    if not PUBLIC_JSON_OUTPUTS.exists():
        logger.error("  outputs/public_dashboard_data.json not found — run build_strategy_layer.py first.")
        return

    current = json.loads(PUBLIC_JSON_OUTPUTS.read_text(encoding="utf-8"))

    if PREVIOUS_BASELINE_PATH.exists():
        previous = json.loads(PREVIOUS_BASELINE_PATH.read_text(encoding="utf-8"))
        logger.info(f"  Previous baseline: {previous.get('meta', {}).get('report_date')} "
                    f"({previous.get('meta', {}).get('total_connections')} connections)")
    else:
        previous = {}
        logger.warning("  No previous snapshot baseline found — deltas will show previous_value=0.")

    manifest_path = ROOT / "data" / "snapshot_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"snapshots": []}
    snaps = manifest.get("snapshots", [])
    current_snap = snaps[-1] if snaps else {}
    previous_snap_id = current_snap.get("previous_snapshot_id", "")
    previous_date = previous_snap_id.replace("snap-", "") if previous_snap_id else previous.get("meta", {}).get("report_date", "unknown")

    new_count = 0
    new_conn_path = OUTPUTS_DIR / "weekly_new_connections.csv"
    if new_conn_path.exists():
        new_count = len(pd.read_csv(new_conn_path, dtype=str, encoding="utf-8-sig"))

    snapshot_meta = {
        "current_label": current_snap.get("snapshot_date", current.get("meta", {}).get("report_date", "")),
        "previous_label": previous_date,
        "new_connections_count": new_count,
    }

    evolution = build_weekly_evolution(previous, current, snapshot_meta)
    current["weekly_evolution"] = evolution

    for path in [PUBLIC_JSON_OUTPUTS]:
        path.write_text(json.dumps(current, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    logger.info("  weekly_evolution merged into outputs/public_dashboard_data.json")
    logger.info(f"  Diagnosis: {evolution['diagnosis']}")


if __name__ == "__main__":
    main()
