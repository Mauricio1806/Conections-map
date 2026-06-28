# -*- coding: utf-8 -*-
"""
confidence_adjusted_kpis.py  (V3 — Separate Score Families)
============================================================
PHILOSOPHY:
  UNKNOWN market does NOT mean the connection has no value.
  UNKNOWN means the market could not be inferred from exported fields.
  A recruiter with UNKNOWN market is still a recruiter.

  Score families:
    1. strategic_network_score   — strength of the network (persona-based, market-blind)
    2. usd_readiness_score       — USD remote job readiness (confirmed + partial UNKNOWN credit)
    3. spain_eu_readiness_score  — Spain/EU readiness (same approach)
    4. market_confidence_score   — quality of market inference only
    5. unknown_resolution_score  — how much UNKNOWN can be reduced
    6. actionable_contacts_score — high-priority + reachable (market-independent)
    7. global_opportunity_score  — GLOBAL_STAFFING/TECH/CONSULTING contacts
    8. data_quality_risk_score   — inverse: how risky is the data gap

  No score is capped below 20 just because UNKNOWN% is high.
  Instead: warnings shown in dashboard about low market confidence.
"""

import logging
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ── Persona groups ─────────────────────────────────────────────────────────────
RECRUITER_PERSONAS   = {"Recruiter", "Sourcer"}
TA_PERSONAS          = {"Talent Acquisition"}
HIRING_MGR_PERSONAS  = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS = {"Data Engineering Manager", "Head of Data",
                        "Director", "Executive"}
DATA_PEER_PERSONAS   = {"Data Engineer", "Analytics Engineer", "Data Analyst",
                        "BI Analyst", "Data Scientist", "Machine Learning / AI"}
HR_PERSONAS          = {"HR"}
FOUNDER_PERSONAS     = {"Founder", "Partner"}
STRATEGIC_PERSONAS   = (RECRUITER_PERSONAS | TA_PERSONAS | HIRING_MGR_PERSONAS
                        | DATA_LEADER_PERSONAS)

# ── V2 market groups ───────────────────────────────────────────────────────────
USD_MARKETS          = {"LATAM_USD", "US_CANADA_NEARSHORE"}
SPAIN_MARKETS        = {"SPAIN_EU", "EUROPE"}
GLOBAL_MARKETS       = {"GLOBAL_STAFFING", "GLOBAL_TECH", "GLOBAL_CONSULTING"}
ALL_KNOWN_MARKETS    = USD_MARKETS | SPAIN_MARKETS | GLOBAL_MARKETS | {"BRAZIL"}

# ── Scoring weights ────────────────────────────────────────────────────────────
# Partial credit for GLOBAL_* contacts (they can place/hire anywhere)
GLOBAL_PARTIAL       = 0.55
# Small partial credit for UNKNOWN contacts with high strategic value
UNKNOWN_PARTIAL      = 0.20

def _pct(n, total):
    return round(n / total * 100, 1) if total else 0.0

def _capped(raw, target, max_pts):
    return min(max_pts, round(raw / target * max_pts, 2)) if target else 0.0

def _pc(df, persona_set):
    return int(df["persona"].isin(persona_set).sum())

def _pc_mkt(df, persona_set, market_set, mkt_col):
    return int((df["persona"].isin(persona_set) & df[mkt_col].isin(market_set)).sum())

# ── SCORE 1: Strategic Network Score ─────────────────────────────────────────
def _strategic_network_score(df):
    """
    Measures the intrinsic strength of the professional network.
    Completely market-agnostic — persona and seniority based only.
    """
    total = len(df)
    r    = _pc(df, RECRUITER_PERSONAS)
    ta   = _pc(df, TA_PERSONAS)
    hm   = _pc(df, HIRING_MGR_PERSONAS)
    dl   = _pc(df, DATA_LEADER_PERSONAS)
    peer = _pc(df, DATA_PEER_PERSONAS)
    hp   = int((df["priority_score"] >= 70).sum())
    mp   = int(((df["priority_score"] >= 40) & (df["priority_score"] < 70)).sum())

    score = (
        _capped(r,   250, 25) +   # Recruiters: 25 pts if 250+
        _capped(ta,  150, 15) +   # TA:          15 pts if 150+
        _capped(hm,  100, 15) +   # Hiring Mgrs: 15 pts if 100+
        _capped(dl,   80, 20) +   # Data Leaders: 20 pts if 80+
        _capped(peer, 500, 10) +  # Data Peers:  10 pts if 500+
        _capped(hp,  150, 15)     # High priority: 15 pts if 150+
    )
    score = min(100.0, round(score, 1))

    # Level
    if score >= 75:
        level, desc = "Strong",  "Your professional network is genuinely strong. Large pool of recruiters, hiring managers, and data leaders."
    elif score >= 50:
        level, desc = "Solid",   "Your network has good foundations. Some gaps in key decision-maker personas."
    elif score >= 30:
        level, desc = "Building","Your network is developing. Focus on adding strategic personas."
    else:
        level, desc = "Early Stage","Network is nascent. Significant persona gaps need to be filled."

    return score, level, desc, {
        "recruiters": r, "talent_acquisition": ta, "hiring_managers": hm,
        "data_leaders": dl, "data_peers": peer, "high_priority": hp,
    }

# ── SCORE 2: USD Readiness Score ─────────────────────────────────────────────
def _usd_readiness_score(df, mkt_col, conf_col):
    """
    Measures readiness for getting a USD remote job from Brazil.
    Confirmed USD contacts = full weight.
    GLOBAL_* contacts = partial weight (they hire/place anywhere).
    UNKNOWN high-value contacts = small partial weight.
    """
    # 1. Confirmed LATAM_USD
    latam_r  = _pc_mkt(df, RECRUITER_PERSONAS, {"LATAM_USD"}, mkt_col)
    latam_ta = _pc_mkt(df, TA_PERSONAS,         {"LATAM_USD"}, mkt_col)
    latam_hm = _pc_mkt(df, HIRING_MGR_PERSONAS, {"LATAM_USD"}, mkt_col)
    latam_dl = _pc_mkt(df, DATA_LEADER_PERSONAS,{"LATAM_USD"}, mkt_col)

    # 2. Confirmed US/Canada
    us_r  = _pc_mkt(df, RECRUITER_PERSONAS, {"US_CANADA_NEARSHORE"}, mkt_col)
    us_ta = _pc_mkt(df, TA_PERSONAS,         {"US_CANADA_NEARSHORE"}, mkt_col)
    us_hm = _pc_mkt(df, HIRING_MGR_PERSONAS, {"US_CANADA_NEARSHORE"}, mkt_col)

    # 3. GLOBAL_* contacts (partial credit — can place/hire anywhere)
    glob_r  = _pc_mkt(df, RECRUITER_PERSONAS,  GLOBAL_MARKETS, mkt_col)
    glob_ta = _pc_mkt(df, TA_PERSONAS,           GLOBAL_MARKETS, mkt_col)
    glob_hm = _pc_mkt(df, HIRING_MGR_PERSONAS,   GLOBAL_MARKETS, mkt_col)
    glob_dl = _pc_mkt(df, DATA_LEADER_PERSONAS,  GLOBAL_MARKETS, mkt_col)

    # 4. UNKNOWN high-value contacts (small partial credit)
    unk_hp_mask = (
        (df[mkt_col] == "UNKNOWN") &
        (df["priority_score"] >= 70) &
        df["persona"].isin(RECRUITER_PERSONAS | TA_PERSONAS | HIRING_MGR_PERSONAS)
    )
    unk_hp = int(unk_hp_mask.sum())

    # Score formula
    usd_confirmed = (
        _capped(latam_r  + us_r,  60, 30) +   # Recruiters: 30 pts max
        _capped(latam_ta + us_ta, 40, 20) +   # TA: 20 pts max
        _capped(latam_hm + us_hm, 30, 20) +   # Hiring Mgrs: 20 pts max
        _capped(latam_dl,         20, 10)      # Data Leaders: 10 pts max
    )
    usd_global = min(15.0, round(
        (glob_r + glob_ta) * GLOBAL_PARTIAL * 0.4 +
        glob_hm * GLOBAL_PARTIAL * 0.3 +
        glob_dl * GLOBAL_PARTIAL * 0.1, 1
    ))
    usd_unknown_credit = min(5.0, round(unk_hp * UNKNOWN_PARTIAL * 0.3, 1))

    score = min(100.0, round(usd_confirmed + usd_global + usd_unknown_credit, 1))

    if score >= 65:
        level, desc = "Ready",     "Strong USD network. Active outreach should yield job conversations quickly."
    elif score >= 40:
        level, desc = "Developing","USD network is building. Adding LATAM USD recruiters is the key lever."
    elif score >= 20:
        level, desc = "Early",     "USD network is nascent. Significant recruiter and hiring manager gaps."
    else:
        level, desc = "Not Started","USD-targeted network essentially not built yet. Start immediately."

    next_step = (
        f"You have {latam_r + us_r} confirmed USD recruiters. "
        f"Target: 60. Add {max(0, 60 - latam_r - us_r)} more to grow this score."
    )
    if score < 40:
        next_step += " Focus: AgileEngine, Andela, Wizeline, Gorilla Logic, BairesDev."

    return score, level, desc, next_step, {
        "latam_usd_recruiters": latam_r, "latam_usd_ta": latam_ta,
        "latam_usd_hiring_mgrs": latam_hm, "latam_usd_data_leaders": latam_dl,
        "us_ca_recruiters": us_r, "us_ca_ta": us_ta, "us_ca_hiring_mgrs": us_hm,
        "global_recruiters_partial": glob_r, "global_ta_partial": glob_ta,
        "global_hm_partial": glob_hm,
        "unknown_high_value_partial": unk_hp,
    }

# ── SCORE 3: Spain/EU Readiness Score ────────────────────────────────────────
def _spain_eu_readiness_score(df, mkt_col, conf_col):
    """
    Measures Spain/EU network readiness.
    Confirmed Spain/EU contacts = full weight.
    GLOBAL_* = partial weight.
    UNKNOWN high-value = small partial weight.
    """
    spain_r  = _pc_mkt(df, RECRUITER_PERSONAS,  {"SPAIN_EU", "EUROPE"}, mkt_col)
    spain_ta = _pc_mkt(df, TA_PERSONAS,           {"SPAIN_EU", "EUROPE"}, mkt_col)
    spain_hm = _pc_mkt(df, HIRING_MGR_PERSONAS,  {"SPAIN_EU", "EUROPE"}, mkt_col)
    spain_dl = _pc_mkt(df, DATA_LEADER_PERSONAS, {"SPAIN_EU", "EUROPE"}, mkt_col)

    glob_r  = _pc_mkt(df, RECRUITER_PERSONAS,  GLOBAL_MARKETS, mkt_col)
    glob_ta = _pc_mkt(df, TA_PERSONAS,           GLOBAL_MARKETS, mkt_col)
    glob_hm = _pc_mkt(df, HIRING_MGR_PERSONAS,   GLOBAL_MARKETS, mkt_col)

    unk_hp_mask = (
        (df[mkt_col] == "UNKNOWN") &
        (df["priority_score"] >= 70) &
        df["persona"].isin(RECRUITER_PERSONAS | TA_PERSONAS | HIRING_MGR_PERSONAS)
    )
    unk_hp = int(unk_hp_mask.sum())

    spain_confirmed = (
        _capped(spain_r,  40, 30) +
        _capped(spain_ta, 30, 20) +
        _capped(spain_hm, 20, 20) +
        _capped(spain_dl, 15, 10)
    )
    spain_global = min(15.0, round(
        (glob_r + glob_ta) * GLOBAL_PARTIAL * 0.3 +
        glob_hm * GLOBAL_PARTIAL * 0.2, 1
    ))
    spain_unknown = min(5.0, round(unk_hp * UNKNOWN_PARTIAL * 0.2, 1))

    score = min(100.0, round(spain_confirmed + spain_global + spain_unknown, 1))

    if score >= 60:
        level, desc = "Ready",      "Strong Spain/EU foundation. Good positioning for relocation."
    elif score >= 30:
        level, desc = "Building",   "Spain/EU network under construction. On track for 12-month timeline."
    elif score >= 15:
        level, desc = "Early",      "Spain/EU network nascent. Appropriate for 6-18 month planning horizon."
    else:
        level, desc = "Not Started","Spain/EU network needs to be built. Normal for early planning phase."

    next_step = (
        f"You have {spain_r} confirmed Spain/EU recruiters (target: 40). "
        f"Add {max(0, 40 - spain_r)} more via ERNI, Stratesys, Capgemini Spain."
    )

    return score, level, desc, next_step, {
        "spain_eu_recruiters": spain_r, "spain_eu_ta": spain_ta,
        "spain_eu_hiring_mgrs": spain_hm, "spain_eu_data_leaders": spain_dl,
        "global_partial_credit": round((glob_r + glob_ta) * GLOBAL_PARTIAL * 0.3, 1),
    }

# ── SCORE 4: Market Confidence Score ─────────────────────────────────────────
def _market_confidence_score(df, mkt_col, conf_col):
    """
    Measures ONLY the quality of market inference.
    Low score = many UNKNOWN connections (normal, not alarming).
    This score is separate from strategic value.
    """
    total = len(df)
    known = int((df[mkt_col] != "UNKNOWN").sum())
    high_conf = int((pd.to_numeric(df[conf_col], errors="coerce").fillna(0) >= 0.70).sum())

    pct_known    = _pct(known, total)
    pct_high_conf= _pct(high_conf, total)

    # Score = blend of known% and high_conf%
    score = min(100.0, round(pct_known * 0.4 + pct_high_conf * 0.6, 1))

    return score, known, high_conf, pct_known, pct_high_conf

# ── SCORE 5: Global Opportunity Score ────────────────────────────────────────
def _global_opportunity_score(df, mkt_col):
    """
    Measures value from GLOBAL_STAFFING, GLOBAL_TECH, GLOBAL_CONSULTING contacts.
    These companies can hire anywhere — they're genuine opportunities.
    """
    glob_mask = df[mkt_col].isin(GLOBAL_MARKETS)
    glob_r    = int((glob_mask & df["persona"].isin(RECRUITER_PERSONAS | TA_PERSONAS)).sum())
    glob_hm   = int((glob_mask & df["persona"].isin(HIRING_MGR_PERSONAS)).sum())
    glob_dl   = int((glob_mask & df["persona"].isin(DATA_LEADER_PERSONAS)).sum())
    glob_all  = int(glob_mask.sum())

    by_cat = df[glob_mask][mkt_col].value_counts().to_dict()

    score = min(100.0, round(
        _capped(glob_r,  30, 50) +
        _capped(glob_hm, 20, 30) +
        _capped(glob_dl, 15, 20), 1
    ))
    return score, glob_all, glob_r, glob_hm, glob_dl, by_cat

# ── SCORE 6: Actionable Contacts Score ───────────────────────────────────────
def _actionable_contacts_score(df):
    """
    Contacts that are both high-priority AND reachable (connected, not just known).
    Market-independent.
    """
    actionable = int((df["priority_score"] >= 60).sum())
    very_high  = int((df["priority_score"] >= 70).sum())
    high       = int(((df["priority_score"] >= 50) & (df["priority_score"] < 70)).sum())

    score = min(100.0, round(
        _capped(very_high, 100, 60) +
        _capped(high,      300, 40), 1
    ))
    return score, actionable, very_high, high

# ── SCORE 7: Unknown Resolution Score ────────────────────────────────────────
def _unknown_resolution_score(df, mkt_col):
    """
    Estimates what % of UNKNOWN could be resolved with company mapping.
    """
    total  = len(df)
    unk    = int((df[mkt_col] == "UNKNOWN").sum())
    unk_pct= _pct(unk, total)

    # Count unique unknown companies (each company = one mapping action)
    unk_companies = df[df[mkt_col] == "UNKNOWN"]["company_clean"].value_counts()
    top25_coverage = int(unk_companies.head(25).sum())
    top50_coverage = int(unk_companies.head(50).sum())
    top100_coverage= int(unk_companies.head(100).sum())

    # Resolution potential = how many connections covered by classifying top 25
    resolution_potential = _pct(top25_coverage, unk)

    # Score = how easy it would be to resolve UNKNOWN
    # (high resolution potential = you CAN reduce it quickly)
    score = min(100.0, round(resolution_potential, 1))

    return score, unk, unk_pct, top25_coverage, top50_coverage, top100_coverage, resolution_potential

# ── SCORE 8: Data Quality Risk Score ─────────────────────────────────────────
def _data_quality_risk_score(unknown_pct):
    """
    High risk = high unknown% = scores may be underestimates.
    This is a WARNING metric, not a performance metric.
    """
    risk = min(100.0, round(unknown_pct * 1.1, 1))
    if risk >= 75:
        level = "High Risk"
        desc  = "Most market inferences are unresolved. Readiness scores are conservative estimates."
    elif risk >= 50:
        level = "Medium Risk"
        desc  = "Many connections have unresolved market. Use company mapping to improve accuracy."
    else:
        level = "Low Risk"
        desc  = "Market inference coverage is adequate. Scores are reasonably reliable."
    return risk, level, desc

# ── Main entry point ──────────────────────────────────────────────────────────
def compute_confidence_adjusted_kpis(df: pd.DataFrame) -> dict:
    """Compute all 8 score families from enriched connections DataFrame."""
    total = len(df)
    today = str(date.today())

    # Column detection
    mkt_col  = "market_v2"           if "market_v2"           in df.columns else "strategic_market"
    conf_col = "market_confidence_v2" if "market_confidence_v2" in df.columns else "market_confidence"

    df = df.copy()
    df["priority_score"] = pd.to_numeric(df["priority_score"], errors="coerce").fillna(0)

    # Market distribution
    mkt_counts = df[mkt_col].value_counts().to_dict()
    def _mc(k): return int(mkt_counts.get(k, 0))

    brazil       = _mc("BRAZIL")
    latam_usd    = _mc("LATAM_USD")
    us_near      = _mc("US_CANADA_NEARSHORE")
    spain_eu     = _mc("SPAIN_EU")
    europe       = _mc("EUROPE")
    global_staff = _mc("GLOBAL_STAFFING")
    global_tech  = _mc("GLOBAL_TECH")
    global_cons  = _mc("GLOBAL_CONSULTING")
    unknown      = _mc("UNKNOWN")
    unknown_pct  = _pct(unknown, total)

    # Persona counts
    recruiters_all  = _pc(df, RECRUITER_PERSONAS)
    ta_all          = _pc(df, TA_PERSONAS)
    hiring_mgrs_all = _pc(df, HIRING_MGR_PERSONAS)
    data_leaders_all= _pc(df, DATA_LEADER_PERSONAS)
    data_peers_all  = _pc(df, DATA_PEER_PERSONAS)
    hr_all          = _pc(df, HR_PERSONAS)

    # Priority bands
    high   = int((df["priority_score"] >= 70).sum())
    medium = int(((df["priority_score"] >= 40) & (df["priority_score"] < 70)).sum())
    low    = int((df["priority_score"] < 40).sum())

    # Recent connections (needs connected_on_clean or connected_on)
    recent_30 = recent_90 = 0
    for col in ["connected_on_clean", "connected_on"]:
        if col in df.columns:
            try:
                dt = pd.to_datetime(df[col], errors="coerce")
                now = pd.Timestamp.now()
                recent_30 = int((now - dt).dt.days.le(30).sum())
                recent_90 = int((now - dt).dt.days.le(90).sum())
            except Exception:
                pass
            break

    # ── Compute all 8 scores ──────────────────────────────────────────────────
    sns, sns_level, sns_desc, sns_breakdown = _strategic_network_score(df)

    usd_s, usd_level, usd_desc, usd_next, usd_breakdown = _usd_readiness_score(df, mkt_col, conf_col)

    esp_s, esp_level, esp_desc, esp_next, esp_breakdown = _spain_eu_readiness_score(df, mkt_col, conf_col)

    mkt_conf_s, known_count, hc_count, pct_known, pct_hc = _market_confidence_score(df, mkt_col, conf_col)

    glob_s, glob_all, glob_r, glob_hm, glob_dl, glob_by_cat = _global_opportunity_score(df, mkt_col)

    act_s, actionable, very_high, high_act = _actionable_contacts_score(df)

    unk_res_s, unk_count, unk_pct, top25, top50, top100, res_potential = _unknown_resolution_score(df, mkt_col)

    risk_s, risk_level, risk_desc = _data_quality_risk_score(unknown_pct)

    # ── Composite market readiness ──────────────────────────────────────────
    market_readiness = round(usd_s * 0.6 + esp_s * 0.4, 1)

    # ── Concentration flags ─────────────────────────────────────────────────
    flags = []
    brazil_pct = _pct(brazil, total)
    if unknown_pct > 70:
        flags.append(
            f"HIGH UNKNOWN ({unknown_pct}%): Market confidence is low. "
            f"Classify top 25 companies in company_market_mapping_template.csv to improve."
        )
    if brazil_pct > 20:
        flags.append(
            f"Brazil-concentrated ({brazil_pct}% of identified): "
            "Brazil contacts are great for referrals but won't directly yield USD remote roles."
        )
    if recruiters_all < 200:
        flags.append("Low recruiter count: target 200+ across all markets.")
    if glob_all < 50:
        flags.append("Few GLOBAL_STAFFING/TECH/CONSULTING contacts identified yet.")
    if not flags:
        flags.append("No critical concentration flags.")

    # ── Unknown high-value contacts (for dashboard) ─────────────────────────
    unk_recruiters_hp = int(
        ((df[mkt_col] == "UNKNOWN") &
         df["persona"].isin(RECRUITER_PERSONAS | TA_PERSONAS) &
         (df["priority_score"] >= 60)).sum()
    )
    unk_hm_hp = int(
        ((df[mkt_col] == "UNKNOWN") &
         df["persona"].isin(HIRING_MGR_PERSONAS) &
         (df["priority_score"] >= 50)).sum()
    )
    unk_dl_hp = int(
        ((df[mkt_col] == "UNKNOWN") &
         df["persona"].isin(DATA_LEADER_PERSONAS) &
         (df["priority_score"] >= 50)).sum()
    )
    unk_peers = int(
        ((df[mkt_col] == "UNKNOWN") &
         df["persona"].isin(DATA_PEER_PERSONAS)).sum()
    )

    return {
        "report_date":            today,
        "total_connections":      total,

        # ── Priority bands ─────────────────────────────────────────────────
        "high_priority":          high,
        "medium_priority":        medium,
        "low_priority":           low,
        "high_priority_pct":      _pct(high, total),
        "medium_priority_pct":    _pct(medium, total),

        # ── Market distribution (V2) ───────────────────────────────────────
        "brazil_count":           brazil,
        "latam_usd_count":        latam_usd,
        "us_nearshore_count":     us_near,
        "spain_eu_count":         spain_eu,
        "europe_count":           europe,
        "global_staffing_count":  global_staff,
        "global_tech_count":      global_tech,
        "global_consulting_count":global_cons,
        "unknown_count":          unknown,
        "unknown_pct":            unknown_pct,

        # ── Persona counts ─────────────────────────────────────────────────
        "recruiters_total":       recruiters_all,
        "talent_hr_total":        ta_all + hr_all,
        "hiring_managers_total":  hiring_mgrs_all,
        "data_leaders_total":     data_leaders_all,
        "data_peers_total":       data_peers_all,
        "connected_last_30_days": recent_30,
        "connected_last_90_days": recent_90,

        # ── SCORE 1: Strategic Network ──────────────────────────────────────
        "strategic_network_score":     sns,
        "strategic_network_level":     sns_level,
        "strategic_network_desc":      sns_desc,
        **{f"sns_{k}": v for k, v in sns_breakdown.items()},

        # ── SCORE 2: USD Readiness ──────────────────────────────────────────
        "usd_readiness_score":         usd_s,
        "usd_readiness_level":         usd_level,
        "usd_readiness_desc":          usd_desc,
        "usd_readiness_next":          usd_next,
        **{f"usd_{k}": v for k, v in usd_breakdown.items()},

        # ── SCORE 3: Spain/EU Readiness ─────────────────────────────────────
        "spain_eu_readiness_score":    esp_s,
        "spain_eu_readiness_level":    esp_level,
        "spain_eu_readiness_desc":     esp_desc,
        "spain_eu_readiness_next":     esp_next,
        **{f"spain_{k}": v for k, v in esp_breakdown.items()},

        # ── SCORE 4: Market Confidence ──────────────────────────────────────
        "market_confidence_score":     mkt_conf_s,
        "market_known_count":          known_count,
        "market_high_conf_count":      hc_count,
        "market_known_pct":            pct_known,
        "market_high_conf_pct":        pct_hc,

        # ── SCORE 5: Global Opportunity ─────────────────────────────────────
        "global_opportunity_score":    glob_s,
        "global_opportunity_total":    glob_all,
        "global_recruiters":           glob_r,
        "global_hiring_mgrs":          glob_hm,
        "global_data_leaders":         glob_dl,
        "global_by_category":          glob_by_cat,

        # ── SCORE 6: Actionable Contacts ────────────────────────────────────
        "actionable_contacts_score":   act_s,
        "actionable_contacts":         actionable,
        "very_high_priority":          very_high,

        # ── SCORE 7: Unknown Resolution ─────────────────────────────────────
        "unknown_resolution_score":    unk_res_s,
        "unknown_resolution_potential": res_potential,
        "top25_company_coverage":      top25,
        "top50_company_coverage":      top50,
        "top100_company_coverage":     top100,

        # ── SCORE 8: Data Quality Risk ──────────────────────────────────────
        "data_quality_risk_score":     risk_s,
        "data_quality_risk_level":     risk_level,
        "data_quality_risk_desc":      risk_desc,

        # ── Market Readiness Composite ──────────────────────────────────────
        "market_readiness_composite":  market_readiness,

        # ── Unknown breakdown ────────────────────────────────────────────────
        "unknown_recruiters_highvalue": unk_recruiters_hp,
        "unknown_hiring_mgrs_highvalue":unk_hm_hp,
        "unknown_data_leaders_highvalue":unk_dl_hp,
        "unknown_peers":               unk_peers,

        # ── Flags ────────────────────────────────────────────────────────────
        "concentration_flags":         flags,

        # ── Top distributions ────────────────────────────────────────────────
        "top_personas":                df["persona"].value_counts().head(12).to_dict(),
        "top_areas":                   df["area"].value_counts().head(8).to_dict(),
        "top_seniority":               df["seniority"].value_counts().to_dict(),
        "market_v2_distribution":      df[mkt_col].value_counts().to_dict() if mkt_col in df.columns else {},
        "market_type_distribution":    df["market_type"].value_counts().to_dict() if "market_type" in df.columns else {},
    }


def save_confidence_adjusted_kpis_csv(kpis: dict, outputs_dir) -> None:
    import pathlib
    path = pathlib.Path(outputs_dir) / "confidence_adjusted_kpis.csv"
    rows = [{"kpi": k, "value": str(v)}
            for k, v in kpis.items() if not isinstance(v, (dict, list))]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"KPIs saved: {path.name}")
