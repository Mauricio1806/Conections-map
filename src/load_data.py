"""
load_data.py – Detect and load LinkedIn export CSV files from the project root
and from data/raw, with flexible filename and encoding support.
"""

import os
import logging
from pathlib import Path

import pandas as pd

from src.config import (
    ROOT_DIR, DATA_RAW_DIR,
    CONNECTIONS_FILENAMES, COMPANY_FILENAMES, INVITATIONS_FILENAMES,
)

logger = logging.getLogger(__name__)


# ─── Helper ────────────────────────────────────────────────────────────────────

def _find_file(candidates: list[str], search_dirs: list[Path]) -> Path | None:
    """Return the first match of any candidate filename in any search directory."""
    for d in search_dirs:
        for name in candidates:
            p = d / name
            if p.exists():
                return p
    return None


def _read_csv_flexible(path: Path) -> pd.DataFrame:
    """
    Read a CSV file with multiple encoding fallbacks.
    LinkedIn exports sometimes use UTF-8 BOM or latin-1.
    Also handles the LinkedIn 'Notes:' header block at the top of Connections.csv
    by skipping preamble rows until the real header is found.
    """
    encodings = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            # First pass: detect preamble rows
            raw_lines = path.read_text(encoding=enc, errors="replace").splitlines()
            header_row = 0
            for i, line in enumerate(raw_lines):
                # The real CSV header starts where we see a comma-separated field list
                # LinkedIn's Connections.csv has a "Notes:" block at the top.
                if line.startswith("First Name,") or line.startswith('"First Name"'):
                    header_row = i
                    break
                # For other files try to detect the header row generically
                if i == 0 and "," in line and not line.startswith("Notes"):
                    header_row = 0
                    break

            df = pd.read_csv(
                path,
                skiprows=header_row,
                encoding=enc,
                on_bad_lines="skip",
                dtype=str,
            )
            df.columns = df.columns.str.strip()
            logger.info(f"Loaded {len(df)} rows from {path.name} (encoding={enc}, skip={header_row})")
            return df
        except Exception as e:
            logger.debug(f"Failed with encoding {enc}: {e}")

    raise RuntimeError(f"Could not read {path} with any supported encoding.")


# ─── Public Loaders ────────────────────────────────────────────────────────────

def load_connections() -> pd.DataFrame:
    """
    Load the LinkedIn Connections export.
    Searches: project root, then data/raw.
    Returns a DataFrame with at least these columns:
        First Name, Last Name, URL, Email Address, Company, Position, Connected On
    """
    search_dirs = [ROOT_DIR, DATA_RAW_DIR]
    path = _find_file(CONNECTIONS_FILENAMES, search_dirs)

    if path is None:
        raise FileNotFoundError(
            f"Connections file not found. Searched for {CONNECTIONS_FILENAMES} in "
            f"{[str(d) for d in search_dirs]}. "
            "Please place your LinkedIn Connections.csv export in the project root or in data/raw/."
        )

    df = _read_csv_flexible(path)
    logger.info(f"Connections file: {path}")

    # Normalize column names
    rename_map = {
        "First Name": "first_name",
        "Last Name": "last_name",
        "URL": "url",
        "Email Address": "email",
        "Company": "company",
        "Position": "position",
        "Connected On": "connected_on",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # Ensure all expected columns exist (fill with empty string if missing)
    for col in rename_map.values():
        if col not in df.columns:
            df[col] = ""

    return df


def load_company_follows() -> pd.DataFrame | None:
    """
    Load the Company Follows export (optional).
    Returns None if the file is not found.
    """
    search_dirs = [ROOT_DIR, DATA_RAW_DIR]
    path = _find_file(COMPANY_FILENAMES, search_dirs)
    if path is None:
        logger.warning("Company Follows file not found – skipping.")
        return None
    df = _read_csv_flexible(path)
    logger.info(f"Company Follows file: {path}")
    return df


def load_invitations() -> pd.DataFrame | None:
    """
    Load the Invitations export (optional).
    Returns None if the file is not found.
    """
    search_dirs = [ROOT_DIR, DATA_RAW_DIR]
    path = _find_file(INVITATIONS_FILENAMES, search_dirs)
    if path is None:
        logger.warning("Invitations file not found – skipping.")
        return None
    df = _read_csv_flexible(path)
    logger.info(f"Invitations file: {path}")
    return df
