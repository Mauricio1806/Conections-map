# -*- coding: utf-8 -*-
"""
connection_freshness.py
=========================
Single shared source of truth for "is Connections.csv current this run, and
if not, what's the last snapshot date it WAS available for."

Mirrors src/message_freshness.py exactly, for the same reason: a weekly
LinkedIn export can legitimately arrive without Connections.csv (e.g. it's
exported in a second batch, or skipped for a week). Written by
src/weekly_snapshot_refresh.py at the start of a weekly refresh. Read by
src/export_public_dashboard_data.py so every connection-derived dashboard
section (network growth, new connections, Weekly Evolution, Action Plan
connection growth, Strategic Gap current network counts) can be stamped
honestly as refreshed vs. stale/as-of-last-export, without fabricating
current-week connection data.

Local/private only (lives under data/processed/, gitignored) — never
committed, never contains connection content, just booleans and dates.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
FRESHNESS_JSON_PATH = ROOT_DIR / "data" / "processed" / "connection_freshness.json"

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "connections_available_for_current_snapshot": True,
    "connections_current_snapshot_date": None,
    "connections_last_available_snapshot_date": None,
    "connection_dependent_sections_status": "refreshed",
    "current_snapshot_date": None,
}


def write_connection_freshness(
    *,
    connections_available_for_current_snapshot: bool,
    current_snapshot_date: str,
    connections_last_available_snapshot_date: str | None,
) -> dict:
    """Persist this week's connection-freshness state. Called once per weekly
    refresh, right after discovering whether Connections.csv is in the new
    snapshot folder."""
    record = {
        "connections_available_for_current_snapshot": connections_available_for_current_snapshot,
        "connections_current_snapshot_date": (
            current_snapshot_date if connections_available_for_current_snapshot else None
        ),
        "connections_last_available_snapshot_date": (
            current_snapshot_date if connections_available_for_current_snapshot
            else connections_last_available_snapshot_date
        ),
        "connection_dependent_sections_status": (
            "refreshed" if connections_available_for_current_snapshot
            else "stale_until_connections_export_arrives"
        ),
        "current_snapshot_date": current_snapshot_date,
    }
    FRESHNESS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    FRESHNESS_JSON_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    logger.info(f"  Connection freshness recorded: {record}")
    return record


def read_connection_freshness() -> dict:
    """Read the last-written freshness record, falling back to safe defaults
    (assume current/refreshed) if none exists yet."""
    if not FRESHNESS_JSON_PATH.exists():
        return dict(_DEFAULTS)
    try:
        record = json.loads(FRESHNESS_JSON_PATH.read_text(encoding="utf-8"))
        merged = dict(_DEFAULTS)
        merged.update(record)
        return merged
    except Exception:
        logger.warning("  connection_freshness.json unreadable — falling back to defaults.")
        return dict(_DEFAULTS)
