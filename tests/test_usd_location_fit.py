# -*- coding: utf-8 -*-
"""
tests/test_usd_location_fit.py
=================================
Regression fixtures for the USD Remote / Location Fit layer
(message_intelligence.py Part 22) — the fix for too many low-quality
Mexico-local/onsite-only leads ranking as if they were real USD remote
opportunities.

All message text below is PARAPHRASED SYNTHETIC fixture data written for this
test file — none of it is real private message content from messages.csv.

Run: python -m pytest tests/test_usd_location_fit.py -q
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.message_intelligence import (
    MY_URL,
    build_conversation_intelligence,
    usd_location_fit,
)


def _mk_conversation(
    conv_id: str,
    other_name: str,
    other_url: str,
    persona: str,
    market: str,
    messages: list[tuple[str, str, int]],
    company: str = "Some Recruiting Co",
    position: str = "Recruiter",
    priority_score: float = 60,
) -> dict:
    """messages: list of (sender 'me'|'other', text, days_ago), chronological.
    Runs the FULL pipeline (build_conversation_intelligence) exactly the way
    the real weekly refresh does, with a one-row synthetic classified_df
    standing in for the contact's connection record."""
    rows = []
    for sender, text, days_ago in messages:
        is_me = sender == "me"
        rows.append({
            "conversation_id":       conv_id,
            "content":               text,
            "is_me_sender":          is_me,
            "date_parsed":           datetime.now() - timedelta(days=days_ago),
            "from":                  "Mauricio Behrens" if is_me else other_name,
            "to":                    other_name if is_me else "Mauricio Behrens",
            "sender_profile_url":    MY_URL if is_me else other_url,
            "recipient_profile_urls": other_url if is_me else MY_URL,
        })
    msgs = pd.DataFrame(rows)
    classified_df = pd.DataFrame([{
        "url":            other_url,
        "full_name":      other_name,
        "persona":        persona,
        "company_clean":  company,
        "position_clean": position,
        "market_v2":      market,
        "priority_score":  priority_score,
        "company_category": "",
    }])
    result = build_conversation_intelligence(msgs, classified_df=classified_df)
    assert len(result) == 1, "expected exactly one conversation row"
    return result.iloc[0].to_dict()


# ── Case 1 — Mexico presencial-only Data Engineer role ───────────────────────
def test_mexico_presencial_only_role_is_downgraded():
    row = _mk_conversation(
        "conv-mx-presencial", "Recruiter CDMX", "https://linkedin.com/in/recruiter-cdmx",
        persona="Recruiter", market="UNKNOWN",
        company="Talent CDMX", position="Data Engineer",
        messages=[
            ("other", "Hi, we have a Data Engineer opening, but it's presencial in CDMX, "
                      "Mexico only — modalidad presencial, must already live in Mexico.", 3),
        ],
    )
    assert row["presencial_only_flag"] is True
    assert row["mexico_local_only_flag"] is True
    assert row["useless_for_usd_remote_flag"] is True
    assert row["lead_quality_score"] <= 35
    assert row["usd_remote_priority_score"] <= 25
    assert row["response_priority_score"] <= 25
    assert row["response_queue_segment"] not in (
        "ACTIVE_PROCESS_NEEDS_REPLY", "INBOUND_OPPORTUNITY_NEEDS_REPLY",
        "SALARY_CV_CALL_REQUESTED", "HIGH_VALUE_RECRUITER_REPLY",
    )
    assert row["lead_disqualification_reason"] == (
        "Downgraded: Mexico-local / onsite-only signal; not aligned with remote USD/LATAM target."
    )


# ── Case 2 — Mexico recruiter, but remote LATAM / US client / contractor ────
def test_mexico_recruiter_remote_latam_is_not_penalized():
    row = _mk_conversation(
        "conv-mx-remote-latam", "Recruiter Mexico", "https://linkedin.com/in/recruiter-mexico",
        persona="Recruiter", market="LATAM_USD",
        company="Mexico Global Staffing", position="Data Engineer",
        messages=[
            ("other", "Hi, I'm a recruiter based in Mexico, but this is a fully remote LATAM "
                      "contractor role for a US client, paid in USD, nearshore, B2B contractor.", 3),
        ],
    )
    assert row["useless_for_usd_remote_flag"] is False
    assert row["usd_remote_priority_score"] >= 75
    assert row["response_queue_segment"] not in ("LOW_FIT_LOCATION_BLOCKED", "LOW_PRIORITY_LOCAL_ONLY")


# ── Case 3 — generic recruiter, no role/signal at all ────────────────────────
def test_generic_recruiter_no_signal_is_medium_low():
    row = _mk_conversation(
        "conv-generic", "Generic Recruiter", "https://linkedin.com/in/generic-recruiter",
        persona="Recruiter", market="UNKNOWN",
        company="Some Recruiting Co", position="Recruiter",
        messages=[
            ("other", "Hi, thanks for connecting! Hope you're doing well.", 3),
        ],
    )
    assert row["useless_for_usd_remote_flag"] is False
    assert row["usd_remote_priority_score"] < 75
    assert row["response_queue_segment"] not in (
        "ACTIVE_PROCESS_NEEDS_REPLY", "INBOUND_OPPORTUNITY_NEEDS_REPLY", "SALARY_CV_CALL_REQUESTED",
    )


# ── Case 4 — strong USD remote lead: Data Engineer + remote + LATAM + USD ───
def test_strong_usd_remote_lead_is_top_priority():
    row = _mk_conversation(
        "conv-strong-usd", "US Recruiter", "https://linkedin.com/in/us-recruiter",
        persona="Recruiter", market="LATAM_USD",
        company="Global Staffing Co", position="Data Engineer",
        messages=[
            ("other", "Hi, we have a fully remote Data Engineer contractor role for a US client, "
                      "LATAM timezone overlap, paid in USD. Could you send your updated CV and "
                      "salary expectations, and hop on a call this week?", 2),
        ],
    )
    assert row["useless_for_usd_remote_flag"] is False
    assert row["usd_remote_priority_score"] >= 75
    assert row["lead_quality_score"] >= 70
    assert row["response_queue_segment"] in (
        "ACTIVE_PROCESS_NEEDS_REPLY", "INBOUND_OPPORTUNITY_NEEDS_REPLY", "SALARY_CV_CALL_REQUESTED",
        "HIGH_VALUE_RECRUITER_REPLY",
    )


# ── Unit-level check on the shared scoring function itself ──────────────────
def test_usd_location_fit_function_directly():
    good = usd_location_fit("Fully remote, LATAM contractor, USD paid, US client.", "Acme LLC", "Data Engineer")
    assert good["useless_for_usd_remote_flag"] is False
    assert good["usd_remote_priority_score"] >= 75

    bad = usd_location_fit("Esquema presencial en México, solo México, CDMX only.", "Acme Mexico", "Data Engineer")
    assert bad["useless_for_usd_remote_flag"] is True
    assert bad["mexico_local_only_flag"] is True
    assert bad["usd_remote_priority_score"] <= 25
    assert bad["lead_disqualification_reason"] == (
        "Downgraded: Mexico-local / onsite-only signal; not aligned with remote USD/LATAM target."
    )
