# -*- coding: utf-8 -*-
"""
calculate_kpis.py
Compute all KPIs from the classified connections DataFrame.
Returns a structured dict used by the strategy layer and dashboard.
"""

import pandas as pd
import numpy as np
from datetime import date


# ── persona groups ────────────────────────────────────────────────────────────
RECRUITER_PERSONAS    = {"Recruiter", "Sourcer"}
TA_PERSONAS           = {"Talent Acquisition"}
HIRING_MGR_PERSONAS   = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS  = {"Data Engineering Manager", "Head of Data", "Director", "Executive"}
DATA_PEER_PERSONAS    = {"Data Engineer", "Analytics Engineer", "Data Analyst",
                         "BI Analyst", "Data Scientist", "Machine Learning / AI"}
HR_PERSONAS           = {"HR"}

USD_MARKETS           = {"LATAM_USD", "US_CANADA_NEARSHORE"}
SPAIN_MARKETS         = {"SPAIN_EU", "EUROPE"}
ALL_MARKETS           = {"BRAZIL", "LATAM_USD", "US_CANADA_NEARSHORE",
                         "SPAIN_EU", "EUROPE", "UNKNOWN"}


def _pct(n: int, total: int, decimals: int = 1) -> float:
    return round(n / total * 100, decimals) if total else 0.0


def compute_kpis(df: pd.DataFrame) -> dict:
    """
    Compute the full KPI dictionary from classified_connections DataFrame.
    """
    total = len(df)

    # ── priority bands ────────────────────────────────────────────────────────
    high   = int((df["priority_score"] >= 70).sum())
    medium = int(((df["priority_score"] >= 40) & (df["priority_score"] < 70)).sum())
    low    = int((df["priority_score"] < 40).sum())

    # ── persona counts ────────────────────────────────────────────────────────
    recruiters     = int(df["persona"].isin(RECRUITER_PERSONAS).sum())
    talent_hr      = int(df["persona"].isin(TA_PERSONAS | HR_PERSONAS).sum())
    hiring_mgrs    = int(df["persona"].isin(HIRING_MGR_PERSONAS).sum())
    data_leaders   = int(df["persona"].isin(DATA_LEADER_PERSONAS).sum())
    data_peers     = int(df["persona"].isin(DATA_PEER_PERSONAS).sum())

    # ── market counts ─────────────────────────────────────────────────────────
    market_counts = df["strategic_market"].value_counts().to_dict()
    brazil        = int(market_counts.get("BRAZIL", 0))
    latam_usd     = int(market_counts.get("LATAM_USD", 0))
    us_nearshore  = int(market_counts.get("US_CANADA_NEARSHORE", 0))
    spain_eu      = int(market_counts.get("SPAIN_EU", 0))
    europe        = int(market_counts.get("EUROPE", 0))
    unknown       = int(market_counts.get("UNKNOWN", 0))
    unknown_pct   = _pct(unknown, total)

    # ── data quality ──────────────────────────────────────────────────────────
    missing_company  = int((df["company_clean"].str.strip() == "").sum())
    missing_position = int((df["position_clean"].str.strip() == "").sum())
    missing_company_pct  = _pct(missing_company,  total)
    missing_position_pct = _pct(missing_position, total)

    # ── USD opportunity score (0-100) ─────────────────────────────────────────
    # Weighted: recruiters in USD markets (30) + TA in USD markets (20)
    # + hiring mgrs in USD markets (20) + data leaders in USD markets (15)
    # + high-priority contacts in USD markets (15)
    usd_mask = df["strategic_market"].isin(USD_MARKETS)
    usd_recruiters   = int((usd_mask & df["persona"].isin(RECRUITER_PERSONAS)).sum())
    usd_ta           = int((usd_mask & df["persona"].isin(TA_PERSONAS)).sum())
    usd_hiring_mgrs  = int((usd_mask & df["persona"].isin(HIRING_MGR_PERSONAS)).sum())
    usd_data_leaders = int((usd_mask & df["persona"].isin(DATA_LEADER_PERSONAS)).sum())
    usd_high         = int((usd_mask & (df["priority_score"] >= 70)).sum())

    def _score(val, target, weight):
        return min(weight, round(val / target * weight, 1)) if target else 0

    usd_opportunity_score = min(100, round(
        _score(usd_recruiters,   60, 30) +
        _score(usd_ta,           40, 20) +
        _score(usd_hiring_mgrs,  30, 20) +
        _score(usd_data_leaders, 20, 15) +
        _score(usd_high,         30, 15),
        1
    ))

    # ── Spain readiness score (0-100) ─────────────────────────────────────────
    spain_mask = df["strategic_market"].isin(SPAIN_MARKETS)
    spain_recruiters   = int((spain_mask & df["persona"].isin(RECRUITER_PERSONAS)).sum())
    spain_ta           = int((spain_mask & df["persona"].isin(TA_PERSONAS)).sum())
    spain_hiring_mgrs  = int((spain_mask & df["persona"].isin(HIRING_MGR_PERSONAS)).sum())
    spain_data_leaders = int((spain_mask & df["persona"].isin(DATA_LEADER_PERSONAS)).sum())
    spain_high         = int((spain_mask & (df["priority_score"] >= 70)).sum())

    spain_readiness_score = min(100, round(
        _score(spain_recruiters,  40, 30) +
        _score(spain_ta,          30, 20) +
        _score(spain_hiring_mgrs, 20, 20) +
        _score(spain_data_leaders,15, 15) +
        _score(spain_high,        15, 15),
        1
    ))

    # ── market readiness score (composite) ────────────────────────────────────
    market_readiness_score = round((usd_opportunity_score * 0.6 + spain_readiness_score * 0.4), 1)

    # ── network concentration risk ────────────────────────────────────────────
    brazil_pct        = _pct(brazil, total)
    recruiter_pct     = _pct(recruiters, total)
    data_peer_pct     = _pct(data_peers, total)
    hr_pct            = _pct(talent_hr, total)

    concentration_flags = []
    if brazil_pct > 20:
        concentration_flags.append(f"Brazil-heavy ({brazil_pct}% of identified network)")
    if unknown_pct > 70:
        concentration_flags.append(f"High unknown market ({unknown_pct}% — market inference limited)")
    if hr_pct > 20:
        concentration_flags.append(f"Generic HR/Recruiting ({hr_pct}%) — broad but not strategic")
    if data_peer_pct > 25:
        concentration_flags.append(f"Data peer heavy ({data_peer_pct}%) — peers cannot hire you")
    if not concentration_flags:
        concentration_flags.append("No critical concentration risk detected")

    # ── avg priority by market ────────────────────────────────────────────────
    avg_priority_by_market = (
        df.groupby("strategic_market")["priority_score"]
        .mean()
        .round(1)
        .to_dict()
    )

    # ── top personas ──────────────────────────────────────────────────────────
    top_personas = df["persona"].value_counts().head(10).to_dict()
    top_areas    = df["area"].value_counts().head(8).to_dict()
    top_seniority = df["seniority"].value_counts().to_dict()

    # ── recency ───────────────────────────────────────────────────────────────
    try:
        df["_date"] = pd.to_datetime(df["connected_on_clean"], errors="coerce")
        last_30  = int((df["_date"] >= pd.Timestamp(date.today()) - pd.Timedelta(days=30)).sum())
        last_90  = int((df["_date"] >= pd.Timestamp(date.today()) - pd.Timedelta(days=90)).sum())
        last_365 = int((df["_date"] >= pd.Timestamp(date.today()) - pd.Timedelta(days=365)).sum())
    except Exception:
        last_30 = last_90 = last_365 = 0

    # ── strategic summary signals ─────────────────────────────────────────────
    network_is_usd_ready    = usd_opportunity_score >= 40
    network_is_spain_ready  = spain_readiness_score >= 30
    top_priority_market     = max(
        {"LATAM_USD": latam_usd, "US_CANADA_NEARSHORE": us_nearshore},
        key=lambda m: {"LATAM_USD": latam_usd, "US_CANADA_NEARSHORE": us_nearshore}[m]
    )

    return {
        # ── core counts ──────────────────────────────────────────────────────
        "total_connections":       total,
        "high_priority":           high,
        "medium_priority":         medium,
        "low_priority":            low,
        "high_priority_pct":       _pct(high, total),
        "medium_priority_pct":     _pct(medium, total),
        # ── persona counts ────────────────────────────────────────────────────
        "recruiters_total":        recruiters,
        "talent_hr_total":         talent_hr,
        "hiring_managers_total":   hiring_mgrs,
        "data_leaders_total":      data_leaders,
        "data_peers_total":        data_peers,
        # ── market counts ─────────────────────────────────────────────────────
        "brazil_count":            brazil,
        "latam_usd_count":         latam_usd,
        "us_nearshore_count":      us_nearshore,
        "spain_eu_count":          spain_eu,
        "europe_count":            europe,
        "unknown_count":           unknown,
        "unknown_pct":             unknown_pct,
        # ── scores ────────────────────────────────────────────────────────────
        "usd_opportunity_score":   usd_opportunity_score,
        "spain_readiness_score":   spain_readiness_score,
        "market_readiness_score":  market_readiness_score,
        # ── USD breakdown ─────────────────────────────────────────────────────
        "usd_recruiters":          usd_recruiters,
        "usd_ta":                  usd_ta,
        "usd_hiring_mgrs":         usd_hiring_mgrs,
        "usd_data_leaders":        usd_data_leaders,
        "usd_high_priority":       usd_high,
        # ── Spain breakdown ───────────────────────────────────────────────────
        "spain_recruiters":        spain_recruiters,
        "spain_ta":                spain_ta,
        "spain_hiring_mgrs":       spain_hiring_mgrs,
        "spain_data_leaders":      spain_data_leaders,
        "spain_high_priority":     spain_high,
        # ── data quality ──────────────────────────────────────────────────────
        "missing_company_count":   missing_company,
        "missing_company_pct":     missing_company_pct,
        "missing_position_count":  missing_position,
        "missing_position_pct":    missing_position_pct,
        # ── concentration risk ────────────────────────────────────────────────
        "concentration_flags":     concentration_flags,
        "brazil_pct":              brazil_pct,
        "recruiter_pct":           recruiter_pct,
        "data_peer_pct":           data_peer_pct,
        "hr_pct":                  hr_pct,
        # ── analytics ─────────────────────────────────────────────────────────
        "avg_priority_by_market":  avg_priority_by_market,
        "top_personas":            top_personas,
        "top_areas":               top_areas,
        "top_seniority":           top_seniority,
        # ── recency ───────────────────────────────────────────────────────────
        "connected_last_30_days":  last_30,
        "connected_last_90_days":  last_90,
        "connected_last_365_days": last_365,
        # ── strategic flags ───────────────────────────────────────────────────
        "network_is_usd_ready":    network_is_usd_ready,
        "network_is_spain_ready":  network_is_spain_ready,
        "top_priority_market":     top_priority_market,
        # ── metadata ──────────────────────────────────────────────────────────
        "report_date":             str(date.today()),
    }
