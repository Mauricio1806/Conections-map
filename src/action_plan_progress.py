# -*- coding: utf-8 -*-
"""
action_plan_progress.py
========================
Plan Progress measurement layer for the Action Plan page.

Run AFTER weekly_kpi_delta.py, BEFORE generate_static_dashboard.py:

    python src/weekly_snapshot_refresh.py --snapshot-folder "DD-MM" --snapshot-date "YYYY-MM-DD"
    python src/build_network_heatmap.py
    python src/build_strategy_layer.py
    python src/weekly_kpi_delta.py
    python src/action_plan_progress.py
    python src/generate_static_dashboard.py
    python src/privacy_check.py
    python src/validate_static_dashboard_runtime.py

This does NOT invent a second snapshot system. Every number below is derived
from outputs that already exist:

  - data/snapshot_manifest.json           (snapshot history / baseline availability)
  - outputs/weekly_kpi_delta.csv           (previous/current NETWORK, PERSONAS,
                                             STRATEGIC, MESSAGE_INTELLIGENCE values —
                                             built by weekly_kpi_delta.py)
  - outputs/weekly_new_connections.csv     (this week's new contacts, enriched with
                                             persona / opportunity_bucket / priority_score
                                             / outreach_adjusted_score by weekly_kpi_delta.py)
  - outputs/public_dashboard_data.json     (current snapshot's lead_reactivation,
                                             untapped_network, opportunity_market_v5_summary,
                                             weekly_evolution blocks)
  - data/processed/_previous_snapshot_baseline.json  (previous snapshot's full payload,
                                             stashed by the weekly refresh caller)
  - config/action_plan_targets.yml         (editable weekly targets — see that file)
  - data/manual/weekly_action_log.csv      (optional, gitignored, manual activity —
                                             never fabricated if absent)

If only one snapshot exists (no previous baseline), every metric that needs a
comparison is reported in BASELINE mode instead of a fabricated delta.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

ROOT_DIR       = Path(__file__).resolve().parent.parent
OUTPUTS_DIR    = ROOT_DIR / "outputs"
CONFIG_DIR     = ROOT_DIR / "config"
DATA_DIR       = ROOT_DIR / "data"

MANIFEST_PATH        = DATA_DIR / "snapshot_manifest.json"
TARGETS_PATH         = CONFIG_DIR / "action_plan_targets.yml"
KPI_DELTA_CSV        = OUTPUTS_DIR / "weekly_kpi_delta.csv"
NEW_CONNECTIONS_CSV  = OUTPUTS_DIR / "weekly_new_connections.csv"
PUBLIC_JSON          = OUTPUTS_DIR / "public_dashboard_data.json"
PREVIOUS_BASELINE_JSON = DATA_DIR / "processed" / "_previous_snapshot_baseline.json"
MANUAL_LOG_CSV       = DATA_DIR / "manual" / "weekly_action_log.csv"

SUMMARY_CSV    = OUTPUTS_DIR / "action_plan_progress_summary.csv"
WEEKLY_CSV     = OUTPUTS_DIR / "action_plan_progress_weekly.csv"
DELTAS_CSV     = OUTPUTS_DIR / "action_plan_progress_deltas.csv"
DIAGNOSIS_CSV  = OUTPUTS_DIR / "action_plan_progress_diagnosis.csv"

# ── Opportunity bucket groupings for the 90/10 strategy mix ─────────────────
# Mirrors the 90/10 framing weekly_kpi_delta.py already uses (latam_usd +
# us_canada_nearshore vs spain_eu), extended with Brazil/language-inferred
# buckets since the owner is Brazil-based and LATAM/USD is the primary
# short-term focus (see config/strategic_targets.yml context block).
PRIMARY_BUCKETS = {
    "LATAM_USD_CONFIRMED", "LATAM_USD_LIKELY",
    "BRAZIL_CONFIRMED", "BRAZIL_LIKELY",
    "US_CANADA_CONFIRMED", "US_CANADA_LIKELY",
    "LANGUAGE_PORTUGUESE_MARKET", "LANGUAGE_SPANISH_MARKET",
}
EUROPE_BUCKETS = {
    "SPAIN_EU_CONFIRMED", "SPAIN_EU_LIKELY",
    "EUROPE_CONFIRMED", "EUROPE_LIKELY",
}
UNCLASSIFIED_BUCKETS = {
    "GLOBAL_OPPORTUNITY", "GLOBAL_STAFFING", "GLOBAL_CONSULTING", "GLOBAL_TECH",
    "NEEDS_COMPANY_MAPPING", "LOW_VALUE_UNRESOLVED",
}

PERSONA_GROUPS = {
    "new_recruiters":         {"Recruiter", "Sourcer"},
    "new_talent_acquisition": {"Talent Acquisition", "HR"},
    "new_hiring_managers":    {"Hiring Manager", "Engineering Manager"},
    "new_data_leaders":       {"Head of Data", "Data Engineering Manager", "Director"},
    "new_data_peers":         {"Data Engineer", "Analytics Engineer", "Data Analyst",
                                "Data Scientist", "BI Analyst"},
    "new_executives":         {"Executive", "Founder", "Partner"},
}

# Tunable diagnosis thresholds (not user-editable via YAML — these are
# structural rule cutoffs, not weekly targets).
NEEDS_RESPONSE_HIGH_THRESHOLD = 20
HIGH_VALUE_UNTAPPED_HIGH_THRESHOLD = 50


# ══════════════════════════════════════════════════════════════════════════
# Loaders
# ══════════════════════════════════════════════════════════════════════════

def load_targets() -> dict:
    if not TARGETS_PATH.exists():
        raise FileNotFoundError(f"Missing {TARGETS_PATH} — cannot compute plan progress targets.")
    with open(TARGETS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_manifest() -> list:
    if not MANIFEST_PATH.exists():
        return []
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("snapshots", [])
    except Exception:
        return []


def current_plan_week(snapshots: list) -> tuple[str, int, int]:
    """The Nth tracked weekly snapshot uses week_N targets (capped at week_4,
    which is then reused every week as the steady-state cadence)."""
    n = max(1, len(snapshots))
    week_index = min(n, 4)
    return f"week_{week_index}", week_index, n


# ══════════════════════════════════════════════════════════════════════════
# Period normalization — weekly targets must be compared against a WEEKLY
# PACE, not a raw period total. Two snapshots taken 38 days apart are not
# "one week" of progress; treating the raw delta as a single week's number
# makes slow weeks look AHEAD and fast weeks look BELOW_TARGET.
# ══════════════════════════════════════════════════════════════════════════

from datetime import date as _date


def compute_period(current_json: dict) -> dict:
    we = current_json.get("weekly_evolution", {}) or {}
    cur_label = we.get("current_snapshot_label")
    prev_label = we.get("previous_snapshot_label")

    def _parse(label):
        if not label or str(label).lower() == "unknown":
            return None
        try:
            return _date.fromisoformat(str(label)[:10])
        except ValueError:
            return None

    cur_date, prev_date = _parse(cur_label), _parse(prev_label)
    if cur_date is None or prev_date is None:
        return {
            "baseline_available": False,
            "current_snapshot_date": str(cur_label or ""),
            "previous_snapshot_date": str(prev_label or ""),
            "period_days": None,
            "period_weeks": None,
            "is_multi_week": False,
            "note": "First tracked snapshot — no previous snapshot date to measure a period against.",
        }

    period_days = (cur_date - prev_date).days
    period_weeks = max(period_days / 7, 1)
    is_multi_week = period_days > 10

    note = (
        f"Current comparison covers {period_days} days / {round(period_weeks, 1)} weeks. "
        f"Weekly progress is normalized by pace."
        if is_multi_week else
        f"Current comparison covers {period_days} days — treated as a single weekly snapshot."
    )

    return {
        "baseline_available": True,
        "current_snapshot_date": str(cur_label),
        "previous_snapshot_date": str(prev_label),
        "period_days": period_days,
        "period_weeks": round(period_weeks, 2),
        "is_multi_week": is_multi_week,
        "note": note,
    }


def _pace_metrics(period_actual: float, weekly_target_min: float, weekly_target_max: float,
                   period: dict) -> dict:
    """Given a raw period total and weekly min/max targets, compute the
    weekly-pace-normalized figures and a status driven by PACE, not the
    raw period total."""
    period_weeks = period.get("period_weeks")
    baseline_available = period.get("baseline_available", False)

    if not baseline_available or not period_weeks:
        return {
            "period_actual": period_actual,
            "weekly_pace_actual": None,
            "weekly_target_min": weekly_target_min,
            "weekly_target_max": weekly_target_max,
            "period_target_min": None,
            "period_target_max": None,
            "status": "NO_BASELINE",
        }

    weekly_pace = round(period_actual / period_weeks, 1)
    period_target_min = round(weekly_target_min * period_weeks, 1)
    period_target_max = round(weekly_target_max * period_weeks, 1)

    if weekly_pace > weekly_target_max:
        status = "AHEAD"
    elif weekly_pace >= weekly_target_min:
        status = "ON_TRACK"
    else:
        status = "BELOW_TARGET"

    return {
        "period_actual": period_actual,
        "weekly_pace_actual": weekly_pace,
        "weekly_target_min": weekly_target_min,
        "weekly_target_max": weekly_target_max,
        "period_target_min": period_target_min,
        "period_target_max": period_target_max,
        "status": status,
    }


def load_kpi_delta_lookup() -> dict:
    if not KPI_DELTA_CSV.exists():
        return {}
    df = pd.read_csv(KPI_DELTA_CSV, encoding="utf-8-sig")
    return {(r["category"], r["metric"]): r for _, r in df.iterrows()}


def load_new_connections() -> pd.DataFrame:
    if not NEW_CONNECTIONS_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(NEW_CONNECTIONS_CSV, encoding="utf-8-sig")


def load_current_json() -> dict:
    if not PUBLIC_JSON.exists():
        raise FileNotFoundError(f"{PUBLIC_JSON} not found — run build_strategy_layer.py first.")
    return json.loads(PUBLIC_JSON.read_text(encoding="utf-8"))


def load_previous_baseline_json() -> dict:
    if not PREVIOUS_BASELINE_JSON.exists():
        return {}
    try:
        return json.loads(PREVIOUS_BASELINE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_manual_log(week_end: str | None) -> dict:
    """Optional local file — never committed, never fabricated if missing.

    Only the row whose week_end exactly matches the CURRENT snapshot date is
    used (not just the last row — an unrelated stale row must not be
    reported as this week's activity). The free-text 'notes' column is
    intentionally NEVER included in the returned dict: this dict flows into
    outputs/action_plan_progress_weekly.csv (committed to git) and the public
    dashboard JSON (served on GitHub Pages), and notes may contain names or
    other private context that has no place in either.
    """
    if not MANUAL_LOG_CSV.exists() or not week_end:
        return {"manual_activity_available": False}
    try:
        df = pd.read_csv(MANUAL_LOG_CSV, encoding="utf-8-sig", dtype=str)
    except Exception:
        return {"manual_activity_available": False}
    if df.empty or "week_end" not in df.columns:
        return {"manual_activity_available": False}

    match = df[df["week_end"].fillna("").str.strip() == str(week_end).strip()]
    if match.empty:
        return {"manual_activity_available": False}

    row = match.iloc[-1]
    return {
        "manual_activity_available": True,
        "week_start": str(row.get("week_start", "") or ""),
        "week_end": str(row.get("week_end", "") or ""),
        "comments_done": int(pd.to_numeric(row.get("comments_done"), errors="coerce") or 0),
        "posts_done": int(pd.to_numeric(row.get("posts_done"), errors="coerce") or 0),
        "career_sites_submitted": int(pd.to_numeric(row.get("career_sites_submitted"), errors="coerce") or 0),
        "manual_dms_sent": int(pd.to_numeric(row.get("manual_dms_sent"), errors="coerce") or 0),
        "manual_followups_done": int(pd.to_numeric(row.get("manual_followups_done"), errors="coerce") or 0),
    }


# ══════════════════════════════════════════════════════════════════════════
# Metric builders (Part 3)
# ══════════════════════════════════════════════════════════════════════════

def _kpi(lookup: dict, category: str, metric: str, field: str, default=0):
    row = lookup.get((category, metric))
    if row is None:
        return default
    val = row.get(field, default)
    try:
        if pd.isna(val):
            return default
    except Exception:
        pass
    return val


def _bucket_set(df: pd.DataFrame, buckets: set) -> int:
    if df.empty or "opportunity_bucket" not in df.columns:
        return 0
    return int(df["opportunity_bucket"].isin(buckets).sum())


def _persona_set(df: pd.DataFrame, personas: set) -> int:
    if df.empty or "persona" not in df.columns:
        return 0
    return int(df["persona"].isin(personas).sum())


def build_network_growth(kpi_lookup: dict, week_cfg: dict, new_conn_df: pd.DataFrame,
                          period: dict) -> dict:
    prev_total = _kpi(kpi_lookup, "NETWORK", "total_connections", "previous_value", 0)
    cur_total  = _kpi(kpi_lookup, "NETWORK", "total_connections", "current_value", 0)
    delta      = _kpi(kpi_lookup, "NETWORK", "total_connections", "absolute_delta", 0)
    new_count  = len(new_conn_df)

    tmin = week_cfg.get("new_connections_target_min", 0)
    tmax = week_cfg.get("new_connections_target_max", 0)

    pace = _pace_metrics(new_count, tmin, tmax, period)
    status = pace["status"]
    progress_pct = (round(pace["weekly_pace_actual"] / tmax * 100, 1)
                    if tmax and pace["weekly_pace_actual"] is not None else None)

    net_growth = int(delta) if delta == delta else 0
    gross_new = new_count
    # Gross new connections (identity-matched additions this period) can be
    # higher than net growth when some prior connections disappear, are
    # removed, or an export simply doesn't re-list them — this is an
    # estimate of that gap, not evidence of a data error.
    churn_estimate = max(0, gross_new - net_growth)

    return {
        "total_connections_current": int(cur_total) if cur_total else 0,
        "total_connections_previous": int(prev_total) if prev_total else 0,
        "new_connections_count": new_count,
        "new_connections_delta": net_growth,
        "new_connections_target_min": tmin,
        "new_connections_target_max": tmax,
        "new_connections_progress_pct": progress_pct,
        "new_connections_status": status,
        # Weekly-pace normalization (period may span more than one week)
        "new_connections_period_actual": pace["period_actual"],
        "new_connections_weekly_pace": pace["weekly_pace_actual"],
        "new_connections_period_target_min": pace["period_target_min"],
        "new_connections_period_target_max": pace["period_target_max"],
        # Gross vs net (Problem 1) — explicit, separate labels so the
        # dashboard never conflates "205 new connections" with "+187 net
        # growth" as if one were wrong.
        "gross_new_connections": gross_new,
        "net_connection_growth": net_growth,
        "connection_churn_or_removed_estimate": churn_estimate,
        "gross_vs_net_note": (
            "Gross new connections can be higher than net growth when some prior "
            "connections disappear, are removed, or exports change."
        ),
    }


def build_strategic_growth(new_conn_df: pd.DataFrame, targets: dict, week_cfg: dict, period: dict) -> dict:
    new_latam_usd    = _bucket_set(new_conn_df, {"LATAM_USD_CONFIRMED", "LATAM_USD_LIKELY"})
    new_us_nearshore = _bucket_set(new_conn_df, {"US_CANADA_CONFIRMED", "US_CANADA_LIKELY"})
    new_spain_eu      = _bucket_set(new_conn_df, {"SPAIN_EU_CONFIRMED", "SPAIN_EU_LIKELY"})
    new_global_staffing    = _bucket_set(new_conn_df, {"GLOBAL_STAFFING"})
    new_global_opportunity = _bucket_set(new_conn_df, {"GLOBAL_OPPORTUNITY"})

    primary_new = _bucket_set(new_conn_df, PRIMARY_BUCKETS)
    europe_new  = _bucket_set(new_conn_df, EUROPE_BUCKETS)
    unclassified_new = _bucket_set(new_conn_df, UNCLASSIFIED_BUCKETS)
    total_new = len(new_conn_df) if not new_conn_df.empty else 0

    # Share ratios are period-length independent (numerator and denominator
    # cover the same period) — no pace normalization needed for these.
    classified_denominator = primary_new + europe_new
    actual_primary_share = round(primary_new / classified_denominator, 4) if classified_denominator else None
    actual_europe_share  = round(europe_new / classified_denominator, 4) if classified_denominator else None
    unclassified_share   = round(unclassified_new / total_new, 4) if total_new else None

    monthly = targets.get("monthly_strategy", {})
    target_primary = monthly.get("primary_latam_usd_share_target", 0.90)
    target_europe  = monthly.get("europe_exploratory_share_target", 0.10)

    # Strategy Mix Status evaluates ONLY the 90/10 LATAM/USD vs Spain/EU
    # effort split among new connections that ARE classified into one of the
    # two strategic buckets. A large unclassified/GLOBAL/NEEDS_MAPPING share
    # is a data-quality problem, not a strategy problem — mixing the two
    # into one status previously made a genuinely on-strategy week (92%
    # primary / 8% Europe) get flagged as TOO_MUCH_UNCLASSIFIED, which is
    # misleading. See classification_risk_status below for that signal.
    if classified_denominator == 0:
        status = "NO_BASELINE"
    elif actual_europe_share is not None and actual_europe_share > 0.20:
        status = "TOO_MUCH_EU"
    elif actual_europe_share is not None and actual_europe_share < 0.05:
        status = "TOO_LITTLE_EU"
    else:
        status = "ON_STRATEGY"

    # Classification Risk — independent signal: a large share of this week's
    # new connections landed in GLOBAL_*/NEEDS_COMPANY_MAPPING/LOW_VALUE
    # buckets rather than a confirmed market, or the company-mapping backlog
    # grew. Surfaced separately so it never overwrites an on-strategy mix.
    classification_risk_status = (
        "CLASSIFICATION_BACKLOG_INCREASED"
        if (unclassified_share is not None and unclassified_share > 0.6)
        else "NORMAL"
    )

    # Europe exploratory has an explicit weekly count target — normalize by pace.
    europe_pace = _pace_metrics(
        europe_new, week_cfg.get("europe_exploratory_target_min", 0),
        week_cfg.get("europe_exploratory_target_max", 0), period,
    )

    # Primary/LATAM/US/Spain raw counts have no dedicated weekly count target
    # (only the share targets above) — still expose weekly pace so the UI can
    # show "X/wk" instead of a raw multi-week total.
    period_weeks = period.get("period_weeks")

    def _pace_only(count):
        return round(count / period_weeks, 1) if period_weeks else None

    return {
        "new_latam_usd_connections": new_latam_usd,
        "new_us_nearshore_connections": new_us_nearshore,
        "new_spain_eu_connections": new_spain_eu,
        "new_global_staffing_connections": new_global_staffing,
        "new_global_opportunity_connections": new_global_opportunity,
        "primary_focus_new_connections": primary_new,
        "primary_focus_weekly_pace": _pace_only(primary_new),
        "europe_exploratory_new_connections": europe_new,
        "europe_exploratory_period_actual": europe_pace["period_actual"],
        "europe_exploratory_weekly_pace": europe_pace["weekly_pace_actual"],
        "europe_exploratory_weekly_target_min": europe_pace["weekly_target_min"],
        "europe_exploratory_weekly_target_max": europe_pace["weekly_target_max"],
        "europe_exploratory_period_target_min": europe_pace["period_target_min"],
        "europe_exploratory_period_target_max": europe_pace["period_target_max"],
        "europe_exploratory_pace_status": europe_pace["status"],
        "unclassified_new_connections": unclassified_new,
        "unclassified_share": unclassified_share,
        "actual_primary_share": actual_primary_share,
        "actual_europe_share": actual_europe_share,
        "target_primary_share": target_primary,
        "target_europe_share": target_europe,
        "strategy_mix_status": status,
        "classification_risk_status": classification_risk_status,
    }


def build_persona_growth(new_conn_df: pd.DataFrame, period: dict) -> dict:
    period_weeks = period.get("period_weeks")
    result = {}
    for key, personas in PERSONA_GROUPS.items():
        count = _persona_set(new_conn_df, personas)
        result[key] = count
        result[f"{key}_weekly_pace"] = round(count / period_weeks, 1) if period_weeks else None
    return result


def build_lead_reactivation(current_json: dict, kpi_lookup: dict, week_cfg: dict, period: dict) -> dict:
    lr = current_json.get("lead_reactivation", {}) or {}
    new_conversations_started = lr.get("total_conversations", 0) - _kpi(
        kpi_lookup, "MESSAGE_INTELLIGENCE", "conversations_analyzed", "previous_value", 0)

    # "Reactivation movement" measured as a period delta (new conversations
    # started since the previous snapshot) — normalized against the weekly
    # reactivation target, same pace treatment as new_connections.
    reactivation_pace = _pace_metrics(
        max(new_conversations_started, 0), week_cfg.get("reactivation_target_min", 0),
        week_cfg.get("reactivation_target_max", 0), period,
    )

    return {
        "conversations_current": lr.get("total_conversations", 0),
        "conversations_previous": _kpi(kpi_lookup, "MESSAGE_INTELLIGENCE", "conversations_analyzed", "previous_value", 0),
        "new_conversations_started": new_conversations_started,
        "new_conversations_weekly_pace": reactivation_pace["weekly_pace_actual"],
        "reactivation_weekly_target_min": reactivation_pace["weekly_target_min"],
        "reactivation_weekly_target_max": reactivation_pace["weekly_target_max"],
        "reactivation_period_target_min": reactivation_pace["period_target_min"],
        "reactivation_period_target_max": reactivation_pace["period_target_max"],
        "reactivation_pace_status": reactivation_pace["status"],
        "followups_due_current": lr.get("follow_up_due", 0),
        "needs_my_response_current": lr.get("needs_my_response", 0),
        "hot_reactivation_current": lr.get("hot_reactivation_leads", 0),
        "warm_reactivation_current": lr.get("warm_reactivation_leads", 0),
        "dormant_warm_current": lr.get("dormant_warm_leads", 0),
        "rejected_closed_current": lr.get("rejected_closed", 0),
    }


def build_untapped_movement(current_json: dict, week_cfg: dict, period: dict) -> dict:
    un = current_json.get("untapped_network", {}) or {}
    s  = un.get("summary", {}) or {}
    we = current_json.get("weekly_evolution", {}) or {}
    um = we.get("untapped_network_movement", {}) or {}

    new_conn_df = load_new_connections()
    # Estimate: new connections that don't yet appear among contacts with any
    # conversation history (top_reactivation_contacts covers ~all contacts
    # with message history). A genuine per-contact "not contacted yet" flag
    # would require identity-level matching this pipeline doesn't persist —
    # this is a bounded aggregate estimate, not an exact fact.
    contacted_urls = set()
    for c in (current_json.get("lead_reactivation", {}) or {}).get("top_reactivation_contacts", []):
        u = str(c.get("other_person_profile_url", "") or "").strip().rstrip("/").lower()
        if u:
            contacted_urls.add(u)
    if not new_conn_df.empty and "url" in new_conn_df.columns:
        new_urls = new_conn_df["url"].fillna("").str.strip().str.rstrip("/").str.lower()
        not_contacted_yet = int((~new_urls.isin(contacted_urls) & (new_urls != "")).sum())
    else:
        not_contacted_yet = 0

    activated_period = um.get("untapped_contacts_activated_this_week_estimate", 0) if um.get("available") else 0
    activated_pace = _pace_metrics(
        activated_period, week_cfg.get("untapped_outreach_target_min", 0),
        week_cfg.get("untapped_outreach_target_max", 0), period,
    )

    return {
        "never_contacted_current": s.get("never_contacted_confirmed", 0),
        "never_contacted_previous": None if not um.get("tracked_since_last_snapshot") else (
            s.get("never_contacted_confirmed", 0) - um.get("never_contacted_confirmed_delta", 0)),
        "high_value_untapped_current": s.get("high_value_untapped", 0),
        "high_value_untapped_previous": None if not um.get("tracked_since_last_snapshot") else (
            s.get("high_value_untapped", 0) - um.get("high_value_untapped_delta", 0)),
        "untapped_activated_this_week": activated_period,
        "untapped_activated_weekly_pace": activated_pace["weekly_pace_actual"],
        "untapped_outreach_weekly_target_min": activated_pace["weekly_target_min"],
        "untapped_outreach_weekly_target_max": activated_pace["weekly_target_max"],
        "untapped_activated_pace_status": activated_pace["status"],
        "new_connections_not_contacted_yet": not_contacted_yet,
        "untapped_queue_completed_estimate": s.get("this_week_queue_count", 0),
        "untapped_backlog_change": um.get("high_value_untapped_delta") if um.get("tracked_since_last_snapshot") else None,
        "available": bool(um.get("available", False)) or bool(s),
    }


def build_top_contacts(current_json: dict, previous_json: dict, kpi_lookup: dict) -> dict:
    cur_top = current_json.get("top_contacts", []) or []
    prev_top = (previous_json or {}).get("top_contacts", []) or []

    def _avg_outreach(contacts):
        scores = [c.get("outreach_adjusted_score") for c in contacts if c.get("outreach_adjusted_score") is not None]
        return round(sum(scores) / len(scores), 1) if scores else None

    return {
        "top_priority_contacts_current": _kpi(kpi_lookup, "NETWORK", "high_priority", "current_value", 0),
        "top_priority_contacts_previous": _kpi(kpi_lookup, "NETWORK", "high_priority", "previous_value", 0),
        "outreach_adjusted_score_avg_current": _avg_outreach(cur_top),
        "outreach_adjusted_score_avg_previous": _avg_outreach(prev_top) if previous_json else None,
    }


def build_data_quality(current_json: dict, kpi_lookup: dict) -> dict:
    v5 = current_json.get("opportunity_market_v5_summary", {}) or {}
    return {
        "needs_company_mapping_current": v5.get("v5_needs_company_mapping", 0),
        "needs_company_mapping_previous": _kpi(kpi_lookup, "STRATEGIC", "needs_company_mapping", "previous_value", 0),
        "needs_company_mapping_delta": _kpi(kpi_lookup, "STRATEGIC", "needs_company_mapping", "absolute_delta", 0),
        "business_classification_coverage_current": v5.get("v5_actionable_pct", 0),
        "confirmed_geographic_signals_current": v5.get("v5_confirmed_geographic", 0),
    }


# ══════════════════════════════════════════════════════════════════════════
# Diagnosis (Part 7.7) + Next Week Recommendation (Part 7.8)
# ══════════════════════════════════════════════════════════════════════════

def build_diagnosis(network: dict, strategic: dict, leads: dict, untapped: dict, quality: dict,
                     period: dict) -> list:
    baseline_available = period.get("baseline_available", False)
    if not baseline_available:
        return [{"rule": "baseline_only",
                  "message": "First tracked snapshot — no previous week to compare against yet. "
                             "Run the next Sunday refresh to start seeing week-over-week progress."}]

    rules = []
    if network["new_connections_status"] == "BELOW_TARGET":
        rules.append({"rule": "new_connections_below_target",
                       "message": "New connection growth is below weekly pace. "
                                  "Increase new connection requests next week."})
    elif network["new_connections_status"] == "AHEAD":
        rules.append({"rule": "new_connections_above_target",
                       "message": "Connection growth is above weekly pace. Keep quality control."})

    if period.get("is_multi_week"):
        rules.append({"rule": "multi_week_baseline_period",
                       "message": "Because this is a baseline catch-up period, use weekly pace instead of raw total."})

    primary_share = strategic.get("actual_primary_share")
    if primary_share is not None and primary_share < 0.80:
        rules.append({"rule": "primary_share_below_80",
                       "message": "Your outreach is drifting away from LATAM/USD. "
                                  "Rebalance toward recruiters and TA in LATAM/South America."})

    europe_share = strategic.get("actual_europe_share")
    if europe_share is not None and europe_share > 0.20:
        rules.append({"rule": "europe_share_above_20",
                       "message": "Europe exploration is above the intended 10% allocation. "
                                  "Keep Europe as positioning, not main income funnel."})
    if europe_share is not None and europe_share < 0.05:
        rules.append({"rule": "europe_share_below_5",
                       "message": "Add a small Spain/EU exploratory block, but do not reduce LATAM/USD focus."})

    if leads["needs_my_response_current"] > NEEDS_RESPONSE_HIGH_THRESHOLD:
        rules.append({"rule": "needs_my_response_high",
                       "message": "Reply queue is becoming a bottleneck. "
                                  "Clear Needs My Response before adding more new contacts."})

    if untapped["high_value_untapped_current"] > HIGH_VALUE_UNTAPPED_HIGH_THRESHOLD:
        rules.append({"rule": "high_value_untapped_high",
                       "message": "Use existing 1st-degree network before adding more cold connections."})

    # Classification Risk (Problem 2) — a separate, non-overriding signal.
    # Fires on either a growing company-mapping backlog or a high unclassified
    # share among this week's new connections; never changes strategy_mix_status.
    if strategic.get("classification_risk_status") == "CLASSIFICATION_BACKLOG_INCREASED":
        rules.append({"rule": "classification_backlog_increased",
                       "message": "Keep the 90/10 outreach strategy. Spend Sunday mapping the top new "
                                  "unresolved companies before planning next week."})

    if not rules:
        rules.append({"rule": "on_track", "message": "Plan execution is on track. Keep the current weekly rhythm."})
    return rules


def overall_diagnosis_status(diagnosis_rules: list, network: dict, baseline_available: bool) -> str:
    if not baseline_available:
        return "BASELINE_ONLY"
    rebalance_rules = {"primary_share_below_80", "europe_share_above_20", "europe_share_below_5"}
    if any(r["rule"] in rebalance_rules for r in diagnosis_rules):
        return "REBALANCE_NEEDED"
    if network["new_connections_status"] == "BELOW_TARGET":
        return "BELOW_TARGET"
    return "ON_TRACK"


def build_next_week_recommendation(week_index: int, targets: dict, leads: dict, untapped: dict) -> str:
    next_index = min(week_index + 1, 4)
    next_cfg = targets.get(f"week_{next_index}", {})
    return (
        f"Next week: keep 80-90% effort on LATAM/USD. "
        f"Prioritize {next_cfg.get('reactivation_target_min', 10)}-{next_cfg.get('reactivation_target_max', 15)} "
        f"Lead Reactivation contacts (currently {leads['needs_my_response_current']} awaiting your response), "
        f"{next_cfg.get('untapped_outreach_target_min', 15)}-{next_cfg.get('untapped_outreach_target_max', 20)} "
        f"Untapped LATAM recruiters/TA (currently {untapped['high_value_untapped_current']} high-value untapped), "
        f"and {next_cfg.get('new_connections_target_min', 30)}-{next_cfg.get('new_connections_target_max', 50)} "
        f"new recruiter/TA connections. Keep Spain/EU to "
        f"{next_cfg.get('europe_exploratory_target_min', 2)}-{next_cfg.get('europe_exploratory_target_max', 5)} "
        f"exploratory contacts."
    )


# ══════════════════════════════════════════════════════════════════════════
# Outputs (Part 5) + public JSON block (Part 6)
# ══════════════════════════════════════════════════════════════════════════

def write_output_csvs(week_key: str, week_index: int, snapshot_count: int, period: dict,
                       network: dict, strategic: dict, persona: dict, leads: dict, untapped: dict,
                       top_contacts: dict, quality: dict, manual: dict, diagnosis: list,
                       overall_status: str, recommendation: str) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    pd.DataFrame([{
        "week_key": week_key,
        "week_index": week_index,
        "snapshot_count": snapshot_count,
        "baseline_available": period.get("baseline_available", False),
        "period_days": period.get("period_days"),
        "period_weeks": period.get("period_weeks"),
        "is_multi_week": period.get("is_multi_week"),
        "plan_status": overall_status,
        "new_connections_status": network["new_connections_status"],
        "strategy_mix_status": strategic["strategy_mix_status"],
        "classification_risk_status": strategic["classification_risk_status"],
        "new_connections_period_actual": network["new_connections_period_actual"],
        "new_connections_weekly_pace": network["new_connections_weekly_pace"],
        "gross_new_connections": network["gross_new_connections"],
        "net_connection_growth": network["net_connection_growth"],
        "connection_churn_or_removed_estimate": network["connection_churn_or_removed_estimate"],
        "actual_primary_share": strategic["actual_primary_share"],
        "actual_europe_share": strategic["actual_europe_share"],
        "next_week_recommendation": recommendation,
    }]).to_csv(SUMMARY_CSV, index=False, encoding="utf-8-sig")

    weekly_rows = [{"category": "PERIOD", "metric": k, "value": v} for k, v in period.items()]
    for category, block in [("NETWORK_GROWTH", network), ("STRATEGIC_GROWTH", strategic),
                             ("PERSONA_GROWTH", persona), ("LEAD_REACTIVATION", leads),
                             ("UNTAPPED_NETWORK", untapped), ("TOP_CONTACTS", top_contacts),
                             ("DATA_QUALITY", quality), ("MANUAL_ACTIVITY", manual)]:
        for metric, value in block.items():
            weekly_rows.append({"category": category, "metric": metric, "value": value})
    pd.DataFrame(weekly_rows).to_csv(WEEKLY_CSV, index=False, encoding="utf-8-sig")

    deltas = [
        {"metric": "total_connections", "previous_value": network["total_connections_previous"],
         "current_value": network["total_connections_current"], "absolute_delta": network["new_connections_delta"]},
        {"metric": "needs_my_response", "previous_value": None, "current_value": leads["needs_my_response_current"],
         "absolute_delta": None},
        {"metric": "needs_company_mapping", "previous_value": quality["needs_company_mapping_previous"],
         "current_value": quality["needs_company_mapping_current"], "absolute_delta": quality["needs_company_mapping_delta"]},
        {"metric": "top_priority_contacts", "previous_value": top_contacts["top_priority_contacts_previous"],
         "current_value": top_contacts["top_priority_contacts_current"],
         "absolute_delta": top_contacts["top_priority_contacts_current"] - top_contacts["top_priority_contacts_previous"]},
    ]
    pd.DataFrame(deltas).to_csv(DELTAS_CSV, index=False, encoding="utf-8-sig")

    pd.DataFrame(diagnosis).to_csv(DIAGNOSIS_CSV, index=False, encoding="utf-8-sig")

    logger.info(f"  Wrote {SUMMARY_CSV.name}, {WEEKLY_CSV.name}, {DELTAS_CSV.name}, {DIAGNOSIS_CSV.name}")


def build_public_block(week_key: str, week_index: int, snapshot_count: int, baseline_available: bool,
                        week_cfg: dict, network: dict, strategic: dict, persona: dict, leads: dict,
                        untapped: dict, top_contacts: dict, quality: dict, manual: dict,
                        diagnosis: list, overall_status: str, recommendation: str, period: dict) -> dict:
    baseline_available = period.get("baseline_available", False)
    summary = {
        "week_key": week_key,
        "week_index": week_index,
        "snapshot_count": snapshot_count,
        "baseline_available": baseline_available,
        "primary_focus": week_cfg.get("primary_focus", ""),
        "plan_status": overall_status,
        "period": period,
        "period_warning": period.get("note") if period.get("is_multi_week") else None,
    }

    def _pace_card(title, pace_key_prefix, block, sub_label):
        period_actual = block.get(f"{pace_key_prefix}_period_actual", block.get(f"{pace_key_prefix}_count"))
        weekly_pace = block.get(f"{pace_key_prefix}_weekly_pace")
        tmin = block.get(f"{pace_key_prefix}_target_min")
        tmax = block.get(f"{pace_key_prefix}_target_max")
        status = block.get(f"{pace_key_prefix}_status")
        return {
            "title": title,
            "period_actual": period_actual,
            "weekly_pace_actual": weekly_pace,
            "weekly_target_min": tmin,
            "weekly_target_max": tmax,
            "value": (f"{weekly_pace}/wk (period total {period_actual})" if weekly_pace is not None
                      else f"{period_actual} (no baseline yet)"),
            "sub": sub_label,
            "status": status,
        }

    progress_cards = [
        {"title": "Plan Status", "value": overall_status},
        _pace_card("New Connections", "new_connections", network,
                   f"target {network['new_connections_target_min']}-{network['new_connections_target_max']}/wk"),
        {
            "title": "Gross New vs Net Growth",
            "value": f"gross {network['gross_new_connections']} / net {network['net_connection_growth']:+d}",
            "sub": (f"Removed/lost/changed estimate: {network['connection_churn_or_removed_estimate']}. "
                    + network["gross_vs_net_note"]),
        },
        {"title": "Strategy Mix Status",
         "value": strategic["strategy_mix_status"],
         "sub": (f"Primary LATAM/USD {strategic['actual_primary_share']*100:.0f}% / "
                 f"Europe {strategic['actual_europe_share']*100:.0f}% (target "
                 f"{strategic['target_primary_share']*100:.0f}/{strategic['target_europe_share']*100:.0f})"
                 if strategic["actual_primary_share"] is not None else None),
         "status": strategic["strategy_mix_status"]},
        {"title": "Classification Risk",
         "value": strategic["classification_risk_status"].replace("CLASSIFICATION_", ""),
         "sub": "unclassified/GLOBAL share or company-mapping backlog — independent of strategy mix",
         "status": strategic["classification_risk_status"]},
        {"title": "Reactivation Movement", "value": leads["new_conversations_started"],
         "sub": "new conversations this period", "status": leads.get("reactivation_pace_status")},
        {"title": "Untapped Activated", "value": untapped["untapped_activated_this_week"],
         "status": untapped.get("untapped_activated_pace_status")},
        {"title": "Needs My Response", "value": leads["needs_my_response_current"]},
        {"title": "Needs Company Mapping", "value": quality["needs_company_mapping_current"],
         "delta": quality["needs_company_mapping_delta"]},
    ]

    charts = {
        "weekly_target_vs_actual": [
            {"label": "New Connections", "actual": network["new_connections_weekly_pace"],
             "period_actual": network["new_connections_period_actual"],
             "target_min": network["new_connections_target_min"], "target_max": network["new_connections_target_max"]},
            {"label": "Reactivation", "actual": leads["new_conversations_weekly_pace"],
             "period_actual": leads["new_conversations_started"],
             "target_min": week_cfg.get("reactivation_target_min", 0), "target_max": week_cfg.get("reactivation_target_max", 0)},
            {"label": "Europe Exploratory", "actual": strategic["europe_exploratory_weekly_pace"],
             "period_actual": strategic["europe_exploratory_period_actual"],
             "target_min": week_cfg.get("europe_exploratory_target_min", 0), "target_max": week_cfg.get("europe_exploratory_target_max", 0)},
        ],
        "strategy_mix": [
            {"label": "Primary LATAM/USD (actual)", "value": strategic["actual_primary_share"]},
            {"label": "Primary LATAM/USD (target)", "value": strategic["target_primary_share"]},
            {"label": "Europe Exploratory (actual)", "value": strategic["actual_europe_share"]},
            {"label": "Europe Exploratory (target)", "value": strategic["target_europe_share"]},
        ],
        "persona_growth": [
            {"label": k.replace("new_", "").replace("_", " ").title(),
             "value": persona.get(f"{k}_weekly_pace"), "period_actual": v}
            for k, v in persona.items() if not k.endswith("_weekly_pace")
        ],
        "lead_pipeline_movement": [
            {"label": "Needs My Response", "value": leads["needs_my_response_current"]},
            {"label": "Hot Reactivation", "value": leads["hot_reactivation_current"]},
            {"label": "Warm Reactivation", "value": leads["warm_reactivation_current"]},
            {"label": "Follow-ups Due", "value": leads["followups_due_current"]},
            {"label": "Dormant Warm", "value": leads["dormant_warm_current"]},
        ],
        "untapped_movement": [
            {"label": "High-Value Untapped", "value": untapped["high_value_untapped_current"]},
            {"label": "Activated This Week", "value": untapped["untapped_activated_this_week"]},
            {"label": "New Not Contacted Yet", "value": untapped["new_connections_not_contacted_yet"]},
            {"label": "Never Contacted Backlog", "value": untapped["never_contacted_current"]},
        ],
    }

    return {
        "available": True,
        "summary": summary,
        "weekly_metrics": {
            "network_growth": network, "strategic_growth": strategic, "persona_growth": persona,
            "lead_reactivation": leads, "untapped_network": untapped, "top_contacts": top_contacts,
            "data_quality": quality, "manual_activity": manual,
        },
        "strategy_mix": strategic,
        "progress_cards": progress_cards,
        "charts": charts,
        "diagnosis": diagnosis,
        "next_week_recommendations": [recommendation],
    }


def merge_into_public_json(block: dict) -> None:
    current = load_current_json()
    current["action_plan_progress"] = block
    PUBLIC_JSON.write_text(json.dumps(current, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    logger.info("  action_plan_progress merged into outputs/public_dashboard_data.json")


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 70)
    logger.info("  ACTION PLAN PROGRESS")
    logger.info("=" * 70)

    targets = load_targets()
    snapshots = load_manifest()
    week_key, week_index, snapshot_count = current_plan_week(snapshots)
    week_cfg = targets.get(week_key, {})

    current_json = load_current_json()
    previous_json = load_previous_baseline_json()
    period = compute_period(current_json)
    baseline_available = period["baseline_available"]

    kpi_lookup = load_kpi_delta_lookup()
    new_conn_df = load_new_connections()

    network = build_network_growth(kpi_lookup, week_cfg, new_conn_df, period)
    strategic = build_strategic_growth(new_conn_df, targets, week_cfg, period)
    persona = build_persona_growth(new_conn_df, period)
    leads = build_lead_reactivation(current_json, kpi_lookup, week_cfg, period)
    untapped = build_untapped_movement(current_json, week_cfg, period)
    top_contacts = build_top_contacts(current_json, previous_json, kpi_lookup)
    quality = build_data_quality(current_json, kpi_lookup)
    manual = load_manual_log(period.get("current_snapshot_date"))

    # Classification Risk also fires when the company-mapping backlog grew
    # (quality dict isn't available inside build_strategic_growth) — folded
    # in here rather than changing strategy_mix_status, which must stay
    # scoped to the 90/10 effort mix only.
    if (quality.get("needs_company_mapping_delta") or 0) > 0:
        strategic["classification_risk_status"] = "CLASSIFICATION_BACKLOG_INCREASED"

    diagnosis = build_diagnosis(network, strategic, leads, untapped, quality, period)
    overall_status = overall_diagnosis_status(diagnosis, network, baseline_available)
    recommendation = build_next_week_recommendation(week_index, targets, leads, untapped)

    write_output_csvs(week_key, week_index, snapshot_count, period, network, strategic,
                       persona, leads, untapped, top_contacts, quality, manual, diagnosis,
                       overall_status, recommendation)

    block = build_public_block(week_key, week_index, snapshot_count, baseline_available, week_cfg,
                                network, strategic, persona, leads, untapped, top_contacts, quality,
                                manual, diagnosis, overall_status, recommendation, period)
    merge_into_public_json(block)

    logger.info("=" * 70)
    logger.info(f"  Plan week: {week_key} (snapshot #{snapshot_count})  baseline_available={baseline_available}")
    logger.info(f"  Period: {period.get('period_days')} days / {period.get('period_weeks')} weeks "
                f"(multi_week={period.get('is_multi_week')})")
    logger.info(f"  New connections: period_actual={network['new_connections_period_actual']} "
                f"weekly_pace={network['new_connections_weekly_pace']} "
                f"(target {network['new_connections_target_min']}-{network['new_connections_target_max']}/wk) "
                f"-> {network['new_connections_status']}")
    logger.info(f"  Gross new={network['gross_new_connections']}  Net growth={network['net_connection_growth']}  "
                f"Removed/lost/changed estimate={network['connection_churn_or_removed_estimate']}")
    logger.info(f"  Primary LATAM/USD share: {strategic['actual_primary_share']} "
                f"(target {strategic['target_primary_share']}) -> strategy_mix={strategic['strategy_mix_status']}  "
                f"classification_risk={strategic['classification_risk_status']}")
    logger.info(f"  Overall plan status: {overall_status}")
    logger.info(f"  Next week: {recommendation}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
