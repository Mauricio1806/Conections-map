"""
score_connections.py – Compute a strategic priority score (0-100) for each
connection and generate a recommended action based on persona, market, seniority,
and recency.
"""

import logging
from datetime import date, datetime

import pandas as pd

from src.config import HIGH_VALUE_PERSONAS, HIGH_VALUE_MARKETS, HIGH_VALUE_AREA_KEYWORDS

logger = logging.getLogger(__name__)

# ─── Score weights (all add up to 100 in the best case) ─────────────────────

WEIGHT_PERSONA     = 35   # persona relevance
WEIGHT_MARKET      = 30   # strategic market relevance
WEIGHT_SENIORITY   = 15   # seniority level relevance
WEIGHT_RECENCY     = 10   # recently connected bonus
WEIGHT_COMPANY     = 10   # company signal bonus

# ─── Sub-scores ──────────────────────────────────────────────────────────────

_PERSONA_SCORES = {
    "Recruiter":                 35,
    "Talent Acquisition":        33,
    "Sourcer":                   28,
    "Hiring Manager":            30,
    "Data Engineering Manager":  28,
    "Engineering Manager":       25,
    "Head of Data":              27,
    "Director":                  25,
    "Executive":                 22,
    "Founder":                   20,
    "Partner":                   18,
    "HR":                        12,
    "Data Engineer":             18,
    "Analytics Engineer":        15,
    "Data Scientist":            15,
    "Machine Learning / AI":     15,
    "Data Analyst":              12,
    "BI Analyst":                10,
    "Software Engineer":         10,
    "Product":                    8,
    "Project / Program Manager":  8,
    "Consultant":                10,
    "Operations":                 5,
    "Other":                      3,
}

_MARKET_SCORES = {
    "US_CANADA_NEARSHORE": 30,
    "LATAM_USD":           28,
    "SPAIN_EU":            26,
    "EUROPE":              22,
    "BRAZIL":              12,
    "UNKNOWN":              3,
}

_SENIORITY_SCORES = {
    "Executive": 15,
    "Founder":   15,
    "Director":  13,
    "Manager":   12,
    "Lead":      10,
    "Senior":     8,
    "Mid":        5,
    "Junior":     3,
    "Intern":     1,
    "Unknown":    2,
}


def _persona_score(persona: str) -> int:
    return _PERSONA_SCORES.get(persona, 3)


def _market_score(market: str) -> int:
    return _MARKET_SCORES.get(market, 3)


def _seniority_score(seniority: str) -> int:
    return _SENIORITY_SCORES.get(seniority, 2)


def _recency_score(connected_on: str) -> int:
    """Return bonus up to WEIGHT_RECENCY based on how recently connected."""
    if not connected_on:
        return 0
    try:
        connected_date = datetime.strptime(connected_on, "%Y-%m-%d").date()
    except ValueError:
        return 0
    days_ago = (date.today() - connected_date).days
    if days_ago <= 30:
        return WEIGHT_RECENCY        # Full bonus for < 1 month
    elif days_ago <= 90:
        return int(WEIGHT_RECENCY * 0.7)
    elif days_ago <= 365:
        return int(WEIGHT_RECENCY * 0.4)
    else:
        return 0


def _company_signal_score(company: str) -> int:
    """Bonus if company name contains international/tech signals."""
    if not company:
        return 0
    company_lower = company.lower()
    signals = HIGH_VALUE_AREA_KEYWORDS
    for sig in signals:
        if sig in company_lower:
            return WEIGHT_COMPANY
    return 0


def compute_priority_score(row: pd.Series) -> int:
    """Compute the 0-100 priority score for a single connection row."""
    score = (
        _persona_score(row.get("persona", "Other"))
        + _market_score(row.get("strategic_market", "UNKNOWN"))
        + _seniority_score(row.get("seniority", "Unknown"))
        + _recency_score(str(row.get("connected_on_clean", "")))
        + _company_signal_score(str(row.get("company_clean", "")))
    )
    return min(100, max(0, score))


def _recommended_action(row: pd.Series) -> str:
    """Generate a plain-language recommended action based on the connection profile."""
    persona        = row.get("persona", "Other")
    market         = row.get("strategic_market", "UNKNOWN")
    score          = row.get("priority_score", 0)
    seniority      = row.get("seniority", "Unknown")

    if score >= 80:
        if persona in ("Recruiter", "Talent Acquisition", "Sourcer"):
            return "PRIORITY: Send personalized outreach – share your profile & open to work status."
        elif persona in ("Hiring Manager", "Data Engineering Manager", "Head of Data", "Director"):
            return "PRIORITY: Engage content, comment strategically, and consider a warm DM."
        else:
            return "PRIORITY: Nurture this connection – engage with their posts regularly."

    if score >= 60:
        if persona in ("Recruiter", "Talent Acquisition"):
            return "HIGH: Reconnect – share a brief update about your remote data engineering availability."
        elif market in ("US_CANADA_NEARSHORE", "LATAM_USD"):
            return "HIGH: Monitor their activity; reach out when a relevant job post appears."
        elif market in ("SPAIN_EU", "EUROPE"):
            return "MEDIUM-HIGH: Start warming up this contact for your Europe transition."
        else:
            return "HIGH: Engage periodically – like and comment on their posts."

    if score >= 40:
        if market in ("SPAIN_EU", "EUROPE"):
            return "MEDIUM: Keep on radar for medium-term Spain/Europe strategy."
        else:
            return "MEDIUM: No immediate action – keep connection warm with occasional engagement."

    if score >= 20:
        return "LOW: Passive connection – no immediate action needed."

    return "ARCHIVE: Low relevance to current strategy – can be deprioritized."


def score_connections(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add priority_score and recommended_action columns to the classified DataFrame.
    """
    df = df.copy()
    logger.info(f"Scoring {len(df)} connections …")

    df["priority_score"]     = df.apply(compute_priority_score, axis=1)
    df["recommended_action"] = df.apply(_recommended_action, axis=1)

    # Summary
    high  = (df["priority_score"] >= 70).sum()
    med   = ((df["priority_score"] >= 40) & (df["priority_score"] < 70)).sum()
    low   = (df["priority_score"] < 40).sum()
    logger.info(f"Scores: High (>=70): {high} | Medium (40-69): {med} | Low (<40): {low}")

    return df
