"""
clean_data.py – Standardize and clean raw LinkedIn connection data.
"""

import re
import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

# Common unicode/special chars that appear in names from LinkedIn exports
_CLEAN_RE = re.compile(r"[\u0300-\u036f\u200b-\u200d\ufeff]")


def _strip(val) -> str:
    """Strip whitespace and return empty string for NaN/None."""
    if pd.isna(val):
        return ""
    return str(val).strip()


def _clean_name(first: str, last: str) -> str:
    """Combine first/last names and remove invisible characters."""
    name = f"{first} {last}".strip()
    # Remove zero-width characters and similar
    name = re.sub(r"[\u200b-\u200d\ufeff\u0000]", "", name)
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)
    return name


def _parse_date(raw: str) -> str:
    """
    Try multiple date formats used by LinkedIn exports.
    Returns ISO date string (YYYY-MM-DD) or the raw value if parsing fails.
    """
    if not raw:
        return ""
    formats = [
        "%d %b %Y",   # 27 Jun 2026
        "%Y-%m-%d",   # 2026-06-27
        "%m/%d/%Y",   # 06/27/2026
        "%m/%d/%y",   # 6/27/26
        "%B %d, %Y",  # June 27, 2026
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw.strip()  # Return as-is if no format matches


def _clean_company(company: str) -> str:
    """Basic normalization of company name."""
    if not company:
        return ""
    # Remove trailing punctuation / extra spaces
    company = re.sub(r"\s+", " ", company)
    company = company.strip(" .,;")
    return company


def _clean_position(position: str) -> str:
    """Basic normalization of job title / position."""
    if not position:
        return ""
    position = re.sub(r"\s+", " ", position)
    return position.strip()


def clean_connections(df: pd.DataFrame) -> pd.DataFrame:
    """
    Take the raw connections DataFrame and produce a cleaned version with:
      - full_name
      - company_clean
      - position_clean
      - connected_on_clean (ISO date or original)
      - text_combined  (searchable blob of all text fields, lowercased)

    All original columns are preserved.
    """
    df = df.copy()

    logger.info(f"Cleaning {len(df)} connection rows …")

    df["full_name"] = df.apply(
        lambda r: _clean_name(_strip(r.get("first_name", "")), _strip(r.get("last_name", ""))),
        axis=1,
    )
    df["company_clean"]    = df["company"].apply(lambda v: _clean_company(_strip(v)))
    df["position_clean"]   = df["position"].apply(lambda v: _clean_position(_strip(v)))
    df["connected_on_clean"] = df["connected_on"].apply(lambda v: _parse_date(_strip(v)))

    # Build a combined searchable text blob (lowercased)
    df["text_combined"] = (
        df["position_clean"].str.lower().fillna("")
        + " "
        + df["company_clean"].str.lower().fillna("")
        + " "
        + df["full_name"].str.lower().fillna("")
        + " "
        + df.get("email", pd.Series([""] * len(df))).str.lower().fillna("")
    )

    # Drop fully empty rows (no name, no company, no position)
    before = len(df)
    df = df[
        (df["full_name"].str.strip() != "")
        | (df["company_clean"].str.strip() != "")
        | (df["position_clean"].str.strip() != "")
    ].reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        logger.info(f"Dropped {dropped} empty rows.")

    logger.info(f"Cleaned data: {len(df)} rows ready for classification.")
    return df
