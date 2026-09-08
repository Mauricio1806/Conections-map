# -*- coding: utf-8 -*-
"""
tests/test_response_priority_ranking.py
=========================================
Regression fixtures for the Response Priority & Lead Quality layer
(message_intelligence.py Part 21) — the fix for the Lead Reactivation queue
ranking poor leads (generic replies, ghosts, talent-pool redirects) above
real USD/LATAM opportunities.

All message text below is PARAPHRASED SYNTHETIC fixture data written for this
test file — none of it is real private message content from messages.csv.

Run: python -m pytest tests/test_response_priority_ranking.py -q
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.message_intelligence import (
    MY_URL,
    build_conversation_intelligence,
    _RESPONSE_SEGMENT_RANK,
)


def _mk_conversation(
    conv_id: str,
    other_name: str,
    other_url: str,
    persona: str,
    market: str,
    messages: list[tuple[str, str, int]],
    company: str = "Some Recruiting Co",
    company_category: str = "",
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
        "position_clean": "Recruiter",
        "market_v2":      market,
        "priority_score":  priority_score,
        "company_category": company_category,
    }])
    result = build_conversation_intelligence(msgs, classified_df=classified_df)
    assert len(result) == 1, "expected exactly one conversation row"
    return result.iloc[0].to_dict()


# ── Case 1 — bad low-value reply: courtesy-only, no real ask ─────────────────
def test_bad_low_value_reply_not_top_priority():
    row = _mk_conversation(
        "conv-low-value", "Random Recruiter", "https://linkedin.com/in/random-recruiter",
        persona="Recruiter", market="UNKNOWN",
        messages=[
            ("me", "Hi, thanks for connecting.", 6),
            ("other", "Thanks, we'll keep your profile in our database.", 4),
        ],
    )
    assert row["lead_quality_score"] <= 40
    assert row["response_priority_score"] <= 35
    assert row["response_queue_segment"] in ("LOW_PRIORITY_COURTESY", "SOFT_CLOSED_KEEP_WARM")
    assert row["response_queue_segment"] not in (
        "ACTIVE_PROCESS_NEEDS_REPLY", "INBOUND_OPPORTUNITY_NEEDS_REPLY", "SALARY_CV_CALL_REQUESTED",
    )


# ── Case 2 — ghost / vacuum: I asked, they never replied ─────────────────────
def test_ghost_vacuum_not_needs_response():
    row = _mk_conversation(
        "conv-ghost", "Silent Recruiter", "https://linkedin.com/in/silent-recruiter",
        persona="Recruiter", market="LATAM_USD",
        messages=[
            ("me", "Hi! Just wanted to reconnect and see how things are going on your end.", 20),
        ],
    )
    assert bool(row["ghost_or_vacuum_flag"]) is True
    assert bool(row["reply_obligation_flag"]) is False
    assert row["response_priority_score"] <= 20
    assert row["response_queue_segment"] == "NO_RESPONSE_BACKLOG"


# ── Case 3 — strong inbound opportunity: role + salary + availability ask ────
def test_strong_inbound_opportunity_ranks_top():
    row = _mk_conversation(
        "conv-strong", "Great Recruiter", "https://linkedin.com/in/great-recruiter",
        persona="Recruiter", market="LATAM_USD", company_category="GLOBAL_STAFFING",
        messages=[
            ("other",
             "We have a great Data Engineer role that looks like a strong fit for your "
             "background. What's your salary expectation, and are you available for a "
             "call this week?", 1),
        ],
    )
    assert row["lead_quality_score"] >= 85
    assert row["response_priority_score"] >= 85
    assert row["response_queue_segment"] in ("INBOUND_OPPORTUNITY_NEEDS_REPLY", "SALARY_CV_CALL_REQUESTED")


# ── Case 4 — talent pool only: register/apply, no role/call/salary/CV ────────
def test_talent_pool_only_not_top_queue():
    row = _mk_conversation(
        "conv-talentpool", "Database Recruiter", "https://linkedin.com/in/database-recruiter",
        persona="Recruiter", market="UNKNOWN",
        messages=[
            ("other",
             "Please register your profile in our talent database via this link — "
             "we'll reach out if a matching role opens up.", 3),
        ],
    )
    assert row["response_priority_score"] <= 35
    assert row["response_queue_segment"] in ("TALENT_POOL_LOW_ACTION", "LOW_PRIORITY_COURTESY")
    assert row["response_queue_segment"] not in (
        "ACTIVE_PROCESS_NEEDS_REPLY", "INBOUND_OPPORTUNITY_NEEDS_REPLY", "SALARY_CV_CALL_REQUESTED",
    )


# ── Case 5 — soft close: no open roles now, but keep on radar ────────────────
def test_soft_close_is_not_hard_rejection():
    row = _mk_conversation(
        "conv-softclose", "Honest Recruiter", "https://linkedin.com/in/honest-recruiter",
        persona="Recruiter", market="UNKNOWN",
        messages=[
            ("other",
             "Unfortunately we don't have open positions right now, but I'll keep you "
             "on my radar for future opportunities.", 2),
        ],
    )
    assert bool(row["hard_rejection_flag"]) is False
    assert row["response_priority_score"] <= 55
    assert row["response_queue_segment"] == "SOFT_CLOSED_KEEP_WARM"


# ── Case 6 — hard rejection: process closed / not selected ───────────────────
def test_hard_rejection_is_closed_no_action():
    row = _mk_conversation(
        "conv-rejected", "Closing Recruiter", "https://linkedin.com/in/closing-recruiter",
        persona="Recruiter", market="UNKNOWN",
        messages=[
            ("other",
             "Thank you for your time. Unfortunately, we've decided to move forward "
             "with another candidate for this role.", 5),
        ],
    )
    assert bool(row["hard_rejection_flag"]) is True
    assert row["response_priority_score"] <= 10
    assert row["response_queue_segment"] == "CLOSED_NO_ACTION"


# ── Ranking sanity — the segment priority order matches the requested 1-9 ────
def test_segment_rank_matches_requested_priority_order():
    expected_order = [
        "ACTIVE_PROCESS_NEEDS_REPLY",
        "INBOUND_OPPORTUNITY_NEEDS_REPLY",
        "SALARY_CV_CALL_REQUESTED",
        "HIGH_VALUE_RECRUITER_REPLY",
        "REACTIVATION_DUE_HIGH_VALUE",
        "SOFT_CLOSED_KEEP_WARM",
    ]
    ranks = [_RESPONSE_SEGMENT_RANK[s] for s in expected_order]
    assert ranks == sorted(ranks), "segment rank must strictly follow the requested 1-9 order"
    assert _RESPONSE_SEGMENT_RANK["LOW_PRIORITY_COURTESY"] < _RESPONSE_SEGMENT_RANK["NO_RESPONSE_BACKLOG"]
    assert _RESPONSE_SEGMENT_RANK["NO_RESPONSE_BACKLOG"] < _RESPONSE_SEGMENT_RANK["CLOSED_NO_ACTION"]
    assert _RESPONSE_SEGMENT_RANK["SOFT_CLOSED_KEEP_WARM"] < _RESPONSE_SEGMENT_RANK["LOW_PRIORITY_COURTESY"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
