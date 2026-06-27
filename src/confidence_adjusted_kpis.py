# -*- coding: utf-8 -*-
"""
confidence_adjusted_kpis.py
===========================
Compute confidence-adjusted KPIs from V2-enriched DataFrame.

Key principle:
  Raw score    = count all inferred connections (V1 behaviour)
  Adjusted score = count only high-confidence (>= 0.70) connections
                   + cap score when UNKNOWN% > 50%
                   + cap Spain score when Spain high-confidence contacts are low
                   + cap USD score when USD high-confidence contacts are low

Scores are capped and explained with diagnostic text.
"""

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_CONF_FOR_ADJUSTED = 0.70
UNKNOWN_CAP_THRESHOLD = 50.0   # if UNKNOWN% >= this, cap adjusted scores
USD_MIN_HIGH_CONF_FOR_FULL_SCORE = 30   # must have this many high-conf USD contacts
SPAIN_MIN_HIGH_CONF_FOR_FULL_SCORE = 20

# ── Persona groups ─────────────────────────────────────────────────────────────
RECRUITER_PERSONAS   = {"Recruiter", "Sourcer"}
TA_PERSONAS          = {"Talent Acquisition"}
HIRING_MGR_PERSONAS  = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS = {"Data Engineering Manager", "Head of Data", "Director", "Executive"}
DATA_PEER_PERSONAS   = {"Data Engineer", "Analytics Engineer", "Data Analyst",
                        "BI Analyst", "Data Scientist", "Machine Learning / AI"}
HR_PERSONAS          = {"HR"}

# V2 markets
USD_MARKETS_V2   = {"LATAM_USD", "US_CANADA_NEARSHORE"}
SPAIN_MARKETS_V2 = {"SPAIN_EU", "EUROPE"}


def _pct(n: int, total: int) -> float:
    return round(n / total * 100, 1) if total else 0.0


def _weighted_score(val: int, target: int, weight: float) -> float:
    return min(weight, round(val / target * weight, 2)) if target else 0.0


def _score_interpretation(score: float, raw: float, kind: str) -> dict:
    """Return explanation dict for a score."""
    gap = raw - score
    if score >= 70:
        level = "Strong"
        desc  = f"Your {kind} network is well-developed."
    elif score >= 45:
        level = "Developing"
        desc  = f"Your {kind} network has a foundation but significant gaps remain."
    elif score >= 25:
        level = "Early Stage"
        desc  = f"Your {kind} network is nascent — active outreach is critical."
    else:
        level = "Not Started"
        desc  = f"Your {kind} network needs to be built from scratch."

    penalty_note = ""
    if gap > 5:
        penalty_note = (
            f" Adjusted score is {gap:.0f} pts lower than raw due to "
            f"low-confidence inferences being excluded."
        )

    return {
        "score":   score,
        "raw":     raw,
        "level":   level,
        "desc":    desc + penalty_note,
    }


def compute_confidence_adjusted_kpis(df: pd.DataFrame) -> dict:
    """
    Compute all confidence-adjusted KPIs.
    Requires V2 columns: market_v2, market_confidence_v2.
    Falls back to V1 columns if V2 not present.
    """
    total = len(df)
    today = str(date.today())

    # ── Market columns (V2 preferred, V1 fallback) ────────────────────────────
    use_v2 = "market_v2" in df.columns and "market_confidence_v2" in df.columns
    mkt_col  = "market_v2"           if use_v2 else "strategic_market"
    conf_col = "market_confidence_v2" if use_v2 else "market_confidence"

    df = df.copy()
    df[conf_col] = pd.to_numeric(df[conf_col], errors="coerce").fillna(0.0)

    # ── Market counts (all, regardless of confidence) ─────────────────────────
    mkt_counts = df[mkt_col].value_counts().to_dict()

    def _market_count(key):
        return int(mkt_counts.get(key, 0))

    brazil       = _market_count("BRAZIL")
    latam_usd    = _market_count("LATAM_USD")
    us_near      = _market_count("US_CANADA_NEARSHORE")
    spain_eu     = _market_count("SPAIN_EU")
    europe       = _market_count("EUROPE")
    global_staff = _market_count("GLOBAL_STAFFING")
    global_tech  = _market_count("GLOBAL_TECH")
    global_cons  = _market_count("GLOBAL_CONSULTING")
    unknown      = _market_count("UNKNOWN")
    unknown_pct  = _pct(unknown, total)

    # High-confidence mask
    hc_mask = df[conf_col] >= MIN_CONF_FOR_ADJUSTED

    # ── Persona counts (all) ──────────────────────────────────────────────────
    def _pc(group):
        return int(df["persona"].isin(group).sum())

    def _pc_hc(group):
        return int((df["persona"].isin(group) & hc_mask).sum())

    recruiters_all  = _pc(RECRUITER_PERSONAS)
    ta_all          = _pc(TA_PERSONAS)
    hiring_mgrs_all = _pc(HIRING_MGR_PERSONAS)
    data_leaders_all= _pc(DATA_LEADER_PERSONAS)
    data_peers_all  = _pc(DATA_PEER_PERSONAS)
    hr_all          = _pc(HR_PERSONAS)

    # ── USD raw score ──────────────────────────────────────────────────────────
    usd_mask = df[mkt_col].isin(USD_MARKETS_V2)
    usd_r    = int((usd_mask & df["persona"].isin(RECRUITER_PERSONAS)).sum())
    usd_ta   = int((usd_mask & df["persona"].isin(TA_PERSONAS)).sum())
    usd_hm   = int((usd_mask & df["persona"].isin(HIRING_MGR_PERSONAS)).sum())
    usd_dl   = int((usd_mask & df["persona"].isin(DATA_LEADER_PERSONAS)).sum())
    usd_hp   = int((usd_mask & (df["priority_score"] >= 70)).sum())

    usd_raw = min(100.0, round(
        _weighted_score(usd_r,  60, 30) +
        _weighted_score(usd_ta, 40, 20) +
        _weighted_score(usd_hm, 30, 20) +
        _weighted_score(usd_dl, 20, 15) +
        _weighted_score(usd_hp, 30, 15), 1
    ))

    # ── USD adjusted score (high-confidence only) ─────────────────────────────
    usd_hc_mask = usd_mask & hc_mask
    usd_r_hc  = int((usd_hc_mask & df["persona"].isin(RECRUITER_PERSONAS)).sum())
    usd_ta_hc = int((usd_hc_mask & df["persona"].isin(TA_PERSONAS)).sum())
    usd_hm_hc = int((usd_hc_mask & df["persona"].isin(HIRING_MGR_PERSONAS)).sum())
    usd_dl_hc = int((usd_hc_mask & df["persona"].isin(DATA_LEADER_PERSONAS)).sum())
    usd_hp_hc = int((usd_hc_mask & (df["priority_score"] >= 70)).sum())

    usd_adj_raw = min(100.0, round(
        _weighted_score(usd_r_hc,  60, 30) +
        _weighted_score(usd_ta_hc, 40, 20) +
        _weighted_score(usd_hm_hc, 30, 20) +
        _weighted_score(usd_dl_hc, 20, 15) +
        _weighted_score(usd_hp_hc, 30, 15), 1
    ))

    # Cap: if total USD high-conf contacts < minimum, cap at 70
    total_usd_hc = usd_r_hc + usd_ta_hc + usd_hm_hc + usd_dl_hc
    if total_usd_hc < USD_MIN_HIGH_CONF_FOR_FULL_SCORE and usd_adj_raw > 70:
        usd_adj_raw = 70.0

    # ── Spain raw score ────────────────────────────────────────────────────────
    spain_mask = df[mkt_col].isin(SPAIN_MARKETS_V2)
    s_r   = int((spain_mask & df["persona"].isin(RECRUITER_PERSONAS)).sum())
    s_ta  = int((spain_mask & df["persona"].isin(TA_PERSONAS)).sum())
    s_hm  = int((spain_mask & df["persona"].isin(HIRING_MGR_PERSONAS)).sum())
    s_dl  = int((spain_mask & df["persona"].isin(DATA_LEADER_PERSONAS)).sum())
    s_hp  = int((spain_mask & (df["priority_score"] >= 70)).sum())

    spain_raw = min(100.0, round(
        _weighted_score(s_r,  40, 30) +
        _weighted_score(s_ta, 30, 20) +
        _weighted_score(s_hm, 20, 20) +
        _weighted_score(s_dl, 15, 15) +
        _weighted_score(s_hp, 15, 15), 1
    ))

    # ── Spain adjusted score ───────────────────────────────────────────────────
    spain_hc_mask = spain_mask & hc_mask
    s_r_hc  = int((spain_hc_mask & df["persona"].isin(RECRUITER_PERSONAS)).sum())
    s_ta_hc = int((spain_hc_mask & df["persona"].isin(TA_PERSONAS)).sum())
    s_hm_hc = int((spain_hc_mask & df["persona"].isin(HIRING_MGR_PERSONAS)).sum())
    s_dl_hc = int((spain_hc_mask & df["persona"].isin(DATA_LEADER_PERSONAS)).sum())
    s_hp_hc = int((spain_hc_mask & (df["priority_score"] >= 70)).sum())

    spain_adj_raw = min(100.0, round(
        _weighted_score(s_r_hc,  40, 30) +
        _weighted_score(s_ta_hc, 30, 20) +
        _weighted_score(s_hm_hc, 20, 20) +
        _weighted_score(s_dl_hc, 15, 15) +
        _weighted_score(s_hp_hc, 15, 15), 1
    ))

    # Cap: Spain adjusted cannot exceed 60 if high-conf contacts < minimum
    total_spain_hc = s_r_hc + s_ta_hc + s_hm_hc + s_dl_hc
    if total_spain_hc < SPAIN_MIN_HIGH_CONF_FOR_FULL_SCORE and spain_adj_raw > 60:
        spain_adj_raw = 60.0

    # ── Unknown risk score (0=good, 100=very risky) ───────────────────────────
    unknown_risk = min(100.0, round(unknown_pct * 1.2, 1))

    # ── Data confidence score (0-100, higher = more reliable data) ─────────────
    hc_count = int(hc_mask.sum())
    data_confidence = round(hc_count / total * 100, 1)

    # ── Market readiness (composite) ──────────────────────────────────────────
    market_raw = round(usd_raw * 0.6 + spain_raw * 0.4, 1)
    market_adj = round(usd_adj_raw * 0.6 + spain_adj_raw * 0.4, 1)

    # ── Actionable network score (high-conf + high-priority) ──────────────────
    actionable = int((hc_mask & (df["priority_score"] >= 60)).sum())
    actionable_score = min(100.0, round(actionable / 50 * 100, 1))

    # ── Unknown breakdown (still useful by persona) ───────────────────────────
    unk_mask = df[mkt_col] == "UNKNOWN"
    unk_recruiters = int((unk_mask & df["persona"].isin(RECRUITER_PERSONAS)).sum())
    unk_ta         = int((unk_mask & df["persona"].isin(TA_PERSONAS)).sum())
    unk_hm         = int((unk_mask & df["persona"].isin(HIRING_MGR_PERSONAS)).sum())
    unk_dl         = int((unk_mask & df["persona"].isin(DATA_LEADER_PERSONAS)).sum())
    unk_peers      = int((unk_mask & df["persona"].isin(DATA_PEER_PERSONAS)).sum())

    # ── Priority bands ────────────────────────────────────────────────────────
    high   = int((df["priority_score"] >= 70).sum())
    medium = int(((df["priority_score"] >= 40) & (df["priority_score"] < 70)).sum())
    low    = int((df["priority_score"] < 40).sum())

    # ── Score explanations ────────────────────────────────────────────────────
    usd_interp   = _score_interpretation(usd_adj_raw,   usd_raw,   "USD opportunity")
    spain_interp = _score_interpretation(spain_adj_raw, spain_raw, "Spain/EU")

    usd_next_step = (
        f"You have {usd_r_hc} high-confidence USD recruiters (target: 60). "
        f"Add {max(0, 60 - usd_r_hc)} more to significantly improve this score."
    )
    spain_next_step = (
        f"You have {s_r_hc} high-confidence Spain/EU recruiters (target: 40). "
        f"Add {max(0, 40 - s_r_hc)} more to unlock Spain readiness growth."
    )

    # ── Concentration flags ───────────────────────────────────────────────────
    flags = []
    brazil_pct = _pct(brazil, total)
    if unknown_pct > 70:
        flags.append(f"HIGH UNKNOWN: {unknown_pct}% of network has no market signal. "
                     "Use company_market_mapping_template.csv to reduce this.")
    if brazil_pct > 15:
        flags.append(f"Brazil-concentrated: {brazil_pct}% of identified network. "
                     "Good for referrals, but Brazil alone won't yield USD jobs.")
    if hc_count < 500:
        flags.append(f"Low high-confidence data: only {hc_count} connections have "
                     "reliable market inference. Adjusted scores reflect this limitation.")

    if not flags:
        flags.append("No critical concentration flags.")

    # ── Output dict ───────────────────────────────────────────────────────────
    return {
        "report_date":              today,
        "total_connections":        total,
        "high_priority":            high,
        "medium_priority":          medium,
        "low_priority":             low,
        "high_priority_pct":        _pct(high, total),
        "medium_priority_pct":      _pct(medium, total),

        # Market counts (V2)
        "brazil_count":             brazil,
        "latam_usd_count":          latam_usd,
        "us_nearshore_count":       us_near,
        "spain_eu_count":           spain_eu,
        "europe_count":             europe,
        "global_staffing_count":    global_staff,
        "global_tech_count":        global_tech,
        "global_consulting_count":  global_cons,
        "unknown_count":            unknown,
        "unknown_pct":              unknown_pct,

        # Persona counts
        "recruiters_total":         recruiters_all,
        "talent_hr_total":          ta_all + hr_all,
        "hiring_managers_total":    hiring_mgrs_all,
        "data_leaders_total":       data_leaders_all,
        "data_peers_total":         data_peers_all,

        # USD breakdown
        "usd_recruiters_raw":       usd_r,
        "usd_ta_raw":               usd_ta,
        "usd_hiring_mgrs_raw":      usd_hm,
        "usd_data_leaders_raw":     usd_dl,
        "usd_high_priority_raw":    usd_hp,
        "usd_recruiters_hc":        usd_r_hc,
        "usd_ta_hc":                usd_ta_hc,
        "usd_hiring_mgrs_hc":       usd_hm_hc,
        "usd_data_leaders_hc":      usd_dl_hc,

        # Spain breakdown
        "spain_recruiters_raw":     s_r,
        "spain_ta_raw":             s_ta,
        "spain_hiring_mgrs_raw":    s_hm,
        "spain_data_leaders_raw":   s_dl,
        "spain_recruiters_hc":      s_r_hc,
        "spain_ta_hc":              s_ta_hc,
        "spain_hiring_mgrs_hc":     s_hm_hc,
        "spain_data_leaders_hc":    s_dl_hc,

        # Scores
        "usd_network_score_raw":    usd_raw,
        "usd_network_score_adjusted":   usd_adj_raw,
        "spain_network_score_raw":  spain_raw,
        "spain_network_score_adjusted": spain_adj_raw,
        "market_readiness_score_raw":   market_raw,
        "market_readiness_score_adjusted": market_adj,
        "data_confidence_score":    data_confidence,
        "unknown_market_risk_score": unknown_risk,
        "actionable_network_score": actionable_score,
        "actionable_contacts":      actionable,

        # Score interpretations
        "usd_score_level":          usd_interp["level"],
        "usd_score_desc":           usd_interp["desc"],
        "usd_next_step":            usd_next_step,
        "spain_score_level":        spain_interp["level"],
        "spain_score_desc":         spain_interp["desc"],
        "spain_next_step":          spain_next_step,

        # Unknown analysis
        "unknown_recruiters":       unk_recruiters,
        "unknown_ta":               unk_ta,
        "unknown_hiring_mgrs":      unk_hm,
        "unknown_data_leaders":     unk_dl,
        "unknown_peers":            unk_peers,
        "high_confidence_count":    hc_count,
        "high_confidence_pct":      data_confidence,

        # Flags
        "concentration_flags":      flags,

        # Top personas/areas for dashboard
        "top_personas":             df["persona"].value_counts().head(12).to_dict(),
        "top_areas":                df["area"].value_counts().head(8).to_dict(),
        "top_seniority":            df["seniority"].value_counts().to_dict(),
        "market_v2_distribution":   df["market_v2"].value_counts().to_dict()
                                    if "market_v2" in df.columns else {},
        "market_type_distribution": df["market_type"].value_counts().to_dict()
                                    if "market_type" in df.columns else {},
    }


def save_confidence_adjusted_kpis_csv(kpis: dict, outputs_dir) -> None:
    """Save flat KPI rows to outputs/confidence_adjusted_kpis.csv."""
    rows = [
        {"kpi": k, "value": str(v)}
        for k, v in kpis.items()
        if not isinstance(v, (dict, list))
    ]
    import pathlib
    path = pathlib.Path(outputs_dir) / "confidence_adjusted_kpis.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"Confidence-adjusted KPIs saved: {path.name}")
