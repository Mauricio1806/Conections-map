# -*- coding: utf-8 -*-
"""
message_freshness.py
======================
Single shared source of truth for "is messages.csv current this run, and if
not, what's the last snapshot date it WAS available for."

Written by src/weekly_snapshot_refresh.py at the start of a weekly refresh
(the point where it knows whether the new snapshot folder included a
messages.csv). Read by src/export_public_dashboard_data.py so every
message-derived dashboard section (Lead Reactivation, USD Contract CRM,
Opportunity History & Monthly Pipeline, Monthly Executive Queue,
Reactivation Calendar) can be stamped honestly as refreshed vs.
stale/as-of-last-export, without fabricating current-week message data.

Local/private only (lives under data/processed/, gitignored) — never
committed, never contains message content, just booleans and dates.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
FRESHNESS_JSON_PATH = ROOT_DIR / "data" / "processed" / "message_freshness.json"

logger = logging.getLogger(__name__)

# Safe defaults used when no freshness record exists yet (e.g. ad hoc pipeline
# run before weekly_snapshot_refresh.py has ever written one) — callers fall
# back to whatever they can observe directly (e.g. MESSAGES_CSV.exists()).
_DEFAULTS = {
    "messages_available_for_current_snapshot": True,
    "messages_current_snapshot_date": None,
    "messages_last_available_snapshot_date": None,
    "message_dependent_sections_status": "refreshed",
    "current_snapshot_date": None,
}


def write_message_freshness(
    *,
    messages_available_for_current_snapshot: bool,
    current_snapshot_date: str,
    messages_last_available_snapshot_date: str | None,
) -> dict:
    """Persist this week's message-freshness state. Called once per weekly
    refresh, right after discovering whether messages.csv is in the new
    snapshot folder."""
    record = {
        "messages_available_for_current_snapshot": messages_available_for_current_snapshot,
        "messages_current_snapshot_date": current_snapshot_date if messages_available_for_current_snapshot else None,
        "messages_last_available_snapshot_date": (
            current_snapshot_date if messages_available_for_current_snapshot
            else messages_last_available_snapshot_date
        ),
        "message_dependent_sections_status": (
            "refreshed" if messages_available_for_current_snapshot
            else "stale_until_messages_export_arrives"
        ),
        "current_snapshot_date": current_snapshot_date,
    }
    FRESHNESS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    FRESHNESS_JSON_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    logger.info(f"  Message freshness recorded: {record}")
    return record


def read_message_freshness() -> dict:
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
        logger.warning("  message_freshness.json unreadable — falling back to defaults.")
        return dict(_DEFAULTS)
