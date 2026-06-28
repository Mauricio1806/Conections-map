# -*- coding: utf-8 -*-
"""
generate_action_plan.py
Build 30/60/90-day action plans from KPIs and gap data.
"""

import pandas as pd


def _mkt_col(df: pd.DataFrame) -> str:
    """Use market_v2 (V2 inference) if available, fall back to strategic_market."""
    return "market_v2" if "market_v2" in df.columns else "strategic_market"


# ── target profiles ───────────────────────────────────────────────────────────

SHORT_TERM_TARGETS = [
    # (market, persona, target, timeframe, strategic_reason)
    ("LATAM_USD",          "Recruiter",               80,  "30d",
     "Direct pipeline for USD remote jobs from Brazil via LATAM staffing firms"),
    ("LATAM_USD",          "Talent Acquisition",      60,  "30d",
     "TA specialists at LATAM companies that hire remote contractors"),
    ("US_CANADA_NEARSHORE","Recruiter",               80,  "30d",
     "US/Canada recruiters who place LATAM contractors in USD-paying roles"),
    ("US_CANADA_NEARSHORE","Talent Acquisition",      60,  "30d",
     "TA teams at nearshore-friendly US/Canada tech companies"),
    ("US_CANADA_NEARSHORE","Hiring Manager",          50,  "45d",
     "Decision-makers at companies with active LATAM remote hiring"),
    ("LATAM_USD",          "Hiring Manager",          50,  "45d",
     "Hiring managers at LATAM companies paying USD compensation"),
    ("US_CANADA_NEARSHORE","Data Engineering Manager",30,  "45d",
     "DE managers who manage remote LATAM data teams"),
    ("LATAM_USD",          "Data Engineering Manager",30,  "45d",
     "DE managers at LATAM-USD companies that value remote data engineers"),
    ("US_CANADA_NEARSHORE","Head of Data",            30,  "60d",
     "Heads of Data who sponsor remote data engineering roles"),
    ("LATAM_USD",          "Head of Data",            30,  "60d",
     "Data leadership at LATAM-USD companies with open data engineering roles"),
    ("US_CANADA_NEARSHORE","Director",                25,  "60d",
     "Directors at US/Canada companies with nearshore-friendly culture"),
    ("LATAM_USD",          "Director",                20,  "60d",
     "Directors at LATAM-USD companies with budget for remote data engineers"),
]

MEDIUM_TERM_TARGETS = [
    ("SPAIN_EU",  "Recruiter",               80,  "60d",
     "Spanish/EU recruiters who place international data engineering candidates"),
    ("SPAIN_EU",  "Talent Acquisition",      60,  "60d",
     "TA professionals at Spain/EU tech companies open to international hires"),
    ("SPAIN_EU",  "Hiring Manager",          60,  "75d",
     "Hiring managers at Madrid/Barcelona/Dublin companies with data roles"),
    ("SPAIN_EU",  "Head of Data",            50,  "75d",
     "Heads of Data at Spain/EU companies who hire Data Engineers"),
    ("SPAIN_EU",  "Data Engineering Manager",40,  "75d",
     "DE managers in Spain/EU who manage data infrastructure teams"),
    ("SPAIN_EU",  "Director",               40,  "90d",
     "Directors at Spain/EU tech companies with international hiring culture"),
    ("EUROPE",    "Recruiter",              60,  "75d",
     "European recruiters (Germany, Netherlands, Ireland) for data engineering"),
    ("EUROPE",    "Talent Acquisition",     50,  "75d",
     "TA professionals at European tech companies open to Data Engineers"),
    ("EUROPE",    "Head of Data",           40,  "90d",
     "European data leaders who can refer or hire for data engineering roles"),
    ("EUROPE",    "Data Engineering Manager",30, "90d",
     "DE managers at European companies building data platforms"),
]


def _urgency(gap_pct: float) -> str:
    if gap_pct >= 80:   return "Critical"
    if gap_pct >= 60:   return "High"
    if gap_pct >= 30:   return "Medium"
    if gap_pct > 0:     return "Low"
    return "Saturated"


def _action(urgency: str, market: str, persona: str) -> str:
    if urgency == "Saturated":
        return f"Maintain existing {persona} connections in {market}. No new outreach needed."
    if urgency == "Critical":
        return (f"URGENT: Search LinkedIn for {persona} in {market}. "
                f"Send 5-10 personalized connection requests daily. "
                f"Use keywords: remote data engineering, nearshore, LATAM, data platform.")
    if urgency == "High":
        return (f"HIGH: Prioritize {persona} in {market}. "
                f"Send 3-5 connection requests daily. Engage with their posts before connecting.")
    if urgency == "Medium":
        return (f"MEDIUM: Add {persona} in {market} to your weekly outreach rotation. "
                f"2-3 connections per week is sufficient.")
    return (f"LOW: Continue organic growth for {persona} in {market}. "
            f"Accept inbound connections, no proactive push needed.")


def build_action_plan(df: pd.DataFrame, targets: list, label: str) -> pd.DataFrame:
    """
    Build an action plan DataFrame from a list of target tuples.
    Uses market_v2 (V2 inference) when available.
    """
    mkt = _mkt_col(df)
    rows = []
    for market, persona, target, timeframe, reason in targets:
        current = int(len(df[(df[mkt] == market) & (df["persona"] == persona)]))
        gap     = max(0, target - current)
        gap_pct = round(gap / target * 100) if target else 0
        urgency = _urgency(gap_pct)
        action  = _action(urgency, market, persona)

        rows.append({
            "plan":                label,
            "market":              market,
            "persona":             persona,
            "current_count":       current,
            "target_count":        target,
            "gap_count":           gap,
            "gap_percentage":      gap_pct,
            "urgency_level":       urgency,
            "recommended_action":  action,
            "timeframe":           timeframe,
            "strategic_reason":    reason,
        })

    return (
        pd.DataFrame(rows)
        .sort_values(["urgency_level", "gap_percentage"], ascending=[True, False])
        .reset_index(drop=True)
    )


def build_30_day_plan(df: pd.DataFrame) -> pd.DataFrame:
    """Focus on immediate USD opportunity: LATAM + US/Canada recruiters & hiring managers."""
    targets_30 = [t for t in SHORT_TERM_TARGETS if t[3] == "30d"]
    return build_action_plan(df, targets_30, "30-Day Plan")


def build_60_day_plan(df: pd.DataFrame) -> pd.DataFrame:
    """Extend USD pipeline + begin Spain/EU positioning."""
    targets_60 = (
        [t for t in SHORT_TERM_TARGETS if t[3] in ("30d", "45d")] +
        [t for t in MEDIUM_TERM_TARGETS if t[3] == "60d"]
    )
    return build_action_plan(df, targets_60, "60-Day Plan")


def build_90_day_plan(df: pd.DataFrame) -> pd.DataFrame:
    """Full strategic picture: USD stability + Spain/Europe readiness."""
    all_targets = SHORT_TERM_TARGETS + MEDIUM_TERM_TARGETS
    return build_action_plan(df, all_targets, "90-Day Plan")


def build_market_strategy_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Market x persona count pivot for the strategy matrix.
    """
    mkt = _mkt_col(df)
    pivot = (
        df.groupby([mkt, "persona"])
        .agg(
            count=("persona", "size"),
            avg_score=("priority_score", "mean"),
            high_priority=("priority_score", lambda x: (x >= 70).sum()),
        )
        .reset_index()
        .rename(columns={mkt: "market"})
    )
    pivot["avg_score"] = pivot["avg_score"].round(1)
    return pivot


def build_persona_strategy_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Persona x market pivot showing coverage and urgency.
    """
    mkt = _mkt_col(df)
    all_targets = SHORT_TERM_TARGETS + MEDIUM_TERM_TARGETS
    rows = []
    for market, persona, target, timeframe, reason in all_targets:
        current = int(len(df[(df[mkt] == market) & (df["persona"] == persona)]))
        gap     = max(0, target - current)
        gap_pct = round(gap / target * 100) if target else 0
        rows.append({
            "persona":        persona,
            "market":         market,
            "current_count":  current,
            "target_count":   target,
            "gap_count":      gap,
            "gap_percentage": gap_pct,
            "urgency_level":  _urgency(gap_pct),
            "timeframe":      timeframe,
            "strategic_reason": reason,
        })
    return pd.DataFrame(rows).sort_values("gap_percentage", ascending=False).reset_index(drop=True)


def build_connection_gap_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Combined gap matrix covering all target market/persona pairs.
    Uses market_v2 (V2 inference) when available.
    """
    mkt = _mkt_col(df)
    all_targets = SHORT_TERM_TARGETS + MEDIUM_TERM_TARGETS
    rows = []
    for market, persona, target, timeframe, reason in all_targets:
        current = int(len(df[(df[mkt] == market) & (df["persona"] == persona)]))
        gap     = max(0, target - current)
        gap_pct = round(gap / target * 100) if target else 0
        urgency = _urgency(gap_pct)
        rows.append({
            "market":              market,
            "persona":             persona,
            "current_count":       current,
            "target_count":        target,
            "gap_count":           gap,
            "gap_percentage":      gap_pct,
            "urgency_level":       urgency,
            "timeframe":           timeframe,
            "recommended_action":  _action(urgency, market, persona),
            "strategic_reason":    reason,
        })
    return pd.DataFrame(rows).sort_values("gap_percentage", ascending=False).reset_index(drop=True)
