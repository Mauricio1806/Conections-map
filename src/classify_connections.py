"""
classify_connections.py – Classify each connection by persona, area, seniority,
and strategic market using keyword matching on position + company text.
"""

import logging
import re

import pandas as pd

from src.config import (
    PERSONA_KEYWORDS, AREA_KEYWORDS, SENIORITY_KEYWORDS, MARKET_KEYWORDS,
    PERSONAS, AREAS, SENIORITY_LEVELS, STRATEGIC_MARKETS,
)

logger = logging.getLogger(__name__)


def _match_keywords(text: str, keyword_dict: dict) -> tuple[str, str]:
    """
    Match `text` against a keyword dictionary {category: [kw1, kw2, ...]}.
    Returns (matched_category, matched_keyword) or ("Other"/"Unknown", "").
    Earlier categories in the dict have higher priority.
    """
    text = text.lower()
    for category, keywords in keyword_dict.items():
        for kw in keywords:
            # Use word-boundary-aware match to avoid partial word hits
            pattern = re.compile(r"(?<![a-z])" + re.escape(kw.lower()) + r"(?![a-z])")
            if pattern.search(text):
                return category, kw
    return None, ""


def classify_persona(text: str) -> tuple[str, str]:
    """Classify job persona from combined text. Returns (persona, matched_keyword)."""
    persona, kw = _match_keywords(text, PERSONA_KEYWORDS)
    if persona is None:
        persona = "Other"
    return persona, kw


def classify_area(text: str, persona: str) -> str:
    """Classify functional area from combined text, with persona as a fallback hint."""
    area, _ = _match_keywords(text, AREA_KEYWORDS)
    if area is not None:
        return area

    # Persona → area fallback mapping
    persona_to_area = {
        "Recruiter": "Recruiting",
        "Talent Acquisition": "Recruiting",
        "Sourcer": "Recruiting",
        "HR": "HR",
        "Hiring Manager": "Management",
        "Engineering Manager": "Management",
        "Data Engineering Manager": "Data Engineering",
        "Head of Data": "Data Engineering",
        "Director": "Management",
        "Executive": "Management",
        "Founder": "Management",
        "Partner": "Management",
        "Data Engineer": "Data Engineering",
        "Analytics Engineer": "Data Engineering",
        "Data Analyst": "Analytics",
        "BI Analyst": "BI",
        "Data Scientist": "Data Science / AI",
        "Machine Learning / AI": "Data Science / AI",
        "Software Engineer": "Software Engineering",
        "Product": "Product",
        "Project / Program Manager": "Management",
        "Operations": "Operations",
        "Consultant": "Consulting",
    }
    return persona_to_area.get(persona, "Other")


def classify_seniority(text: str, persona: str) -> str:
    """Classify seniority level from combined text."""
    seniority, _ = _match_keywords(text, SENIORITY_KEYWORDS)
    if seniority is not None:
        return seniority

    # Persona-based fallback
    if persona in ("Executive", "Founder", "Partner"):
        return persona if persona in SENIORITY_LEVELS else "Executive"
    if persona in ("Director", "Head of Data"):
        return "Director"
    if persona in ("Hiring Manager", "Engineering Manager", "Data Engineering Manager"):
        return "Manager"
    return "Unknown"


def classify_market(text: str) -> tuple[str, float, str]:
    """
    Infer strategic market from combined text.
    Returns (market, confidence, reason).
    Confidence: 0.0 – 1.0
    """
    text_lower = text.lower()
    scores: dict[str, tuple[int, list[str]]] = {}

    for market, keywords in MARKET_KEYWORDS.items():
        hits = []
        for kw in keywords:
            if kw.lower() in text_lower:
                hits.append(kw)
        if hits:
            scores[market] = (len(hits), hits)

    if not scores:
        return "UNKNOWN", 0.0, "No market signals detected in position or company."

    # Pick the market with the most keyword hits
    best_market = max(scores, key=lambda m: scores[m][0])
    hit_count, hit_words = scores[best_market]

    # Confidence heuristic: 1+ hit = low, 2+ = medium, 3+ = high
    if hit_count >= 3:
        confidence = 0.9
    elif hit_count == 2:
        confidence = 0.7
    else:
        confidence = 0.5

    reason = f"Matched keywords: {', '.join(hit_words[:3])}"
    return best_market, confidence, reason


def classify_connections(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all classification functions to the cleaned connections DataFrame.
    Adds columns: persona, area, seniority, strategic_market,
                  market_confidence, inference_reason.
    """
    df = df.copy()
    logger.info(f"Classifying {len(df)} connections …")

    personas, areas, seniorities = [], [], []
    markets, confidences, reasons = [], [], []

    for _, row in df.iterrows():
        text = str(row.get("text_combined", ""))

        persona, kw = classify_persona(text)
        area = classify_area(text, persona)
        seniority = classify_seniority(text, persona)
        market, confidence, reason = classify_market(text)

        personas.append(persona)
        areas.append(area)
        seniorities.append(seniority)
        markets.append(market)
        confidences.append(round(confidence, 2))
        reasons.append(reason)

    df["persona"]          = personas
    df["area"]             = areas
    df["seniority"]        = seniorities
    df["strategic_market"] = markets
    df["market_confidence"] = confidences
    df["inference_reason"] = reasons

    # Summary log
    logger.info("Classification complete. Distribution:")
    logger.info(f"  Top personas: {df['persona'].value_counts().head(5).to_dict()}")
    logger.info(f"  Top markets:  {df['strategic_market'].value_counts().to_dict()}")

    return df
