# -*- coding: utf-8 -*-
"""
tests/test_message_state_machine.py
=====================================
Regression fixtures for the V8 conversation-state engine
(CLAUDE_MESSAGE_STATE_V8.md Part 18).

All message text below is PARAPHRASED SYNTHETIC fixture data written for this
test file — none of it is real private message content from messages.csv.

Run: python -m pytest tests/test_message_state_machine.py -q
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.message_intelligence import (
    _analyze_conversation_state,
    _build_meaningful_turns,
    _cooldown_state,
    _lead_category_v8,
)

TODAY = datetime(2026, 7, 6)


def _mk_group(messages: list[tuple[str, str, int]]) -> pd.DataFrame:
    """messages: list of (sender 'me'|'other', text, days_ago) in chronological order."""
    rows = []
    for sender, text, days_ago in messages:
        rows.append({
            "content": text,
            "is_me_sender": sender == "me",
            "date_parsed": TODAY - timedelta(days=days_ago),
        })
    return pd.DataFrame(rows)


def _run(messages, is_valuable=True, has_interview=False, has_cv=False,
          priority_score=60, market_value=True, days_since_last=None):
    group = _mk_group(messages)
    turns = _build_meaningful_turns(group)
    messages_from_other = sum(1 for m in messages if m[0] == "other")
    if days_since_last is None:
        days_since_last = messages[-1][2]
    return _analyze_conversation_state(
        turns=turns, is_valuable_persona=is_valuable, has_interview_hist=has_interview,
        has_cv_hist=has_cv, priority_score=priority_score, market_value=market_value,
        messages_from_other=messages_from_other, days_since_last=days_since_last,
    )


# ── Case 1 — recruiter asks availability, Mauricio answers => NONE ───────────
def test_availability_question_answered_resolves():
    state = _run([
        ("other", "Are you available for a call on Monday?", 5),
        ("me", "Yes, I am available on Monday, happy to chat.", 5),
    ])
    assert state["reply_obligation"] == "NONE"
    assert state["request_resolved"] is True


# ── Case 2 — rejection, Mauricio acknowledges => REJECTED_CLOSED + NONE ──────
def test_rejection_then_acknowledgement():
    state = _run([
        ("other", "Unfortunately we decided to move forward with another candidate.", 10),
        ("me", "Thank you for the update, I appreciate it.", 10),
    ])
    assert state["process_state"] == "REJECTED_CLOSED"
    assert state["reply_obligation"] == "NONE"
    assert state["closure_reason"] == "OTHER_CANDIDATE_SELECTED"


# ── Case 3 — India-only hiring restriction => GEOGRAPHIC_HIRING_RESTRICTION + NONE
def test_india_only_hiring_restriction():
    state = _run([
        ("other", "We are only hiring in India right now, but may keep you in mind for future international roles.", 3),
    ])
    assert state["process_state"] == "GEOGRAPHIC_HIRING_RESTRICTION"
    assert state["reply_obligation"] == "NONE"


# ── Case 4 — Portugal residency requirement => LOCATION_ELIGIBILITY_BLOCKED + NONE
def test_portugal_residency_constraint():
    state = _run([
        ("other", "Do you currently reside in Portugal? Most of our roles are hybrid and require fiscal residency here.", 2),
    ])
    assert state["process_state"] == "LOCATION_ELIGIBILITY_BLOCKED"
    assert state["reply_obligation"] == "NONE"


# ── Case 5 — talent database redirect => TALENT_POOL_REDIRECT + NONE ─────────
def test_talent_pool_redirect():
    state = _run([
        ("other", "Please register your profile in our talent database via this Linktree — mentoring is also available through the same link.", 4),
    ])
    assert state["process_state"] == "TALENT_POOL_REDIRECT"
    assert state["reply_obligation"] == "NONE"
    assert state["external_action_type"] == "JOIN_TALENT_POOL"


# ── Case 6 — direct unanswered CV request => CONFIRMED ───────────────────────
def test_unanswered_cv_request_is_confirmed():
    state = _run([
        ("other", "Could you please send your updated CV so I can share it with the client?", 1),
    ], has_cv=False)
    assert state["reply_obligation"] == "CONFIRMED"
    assert state["process_state"] == "CV_REQUESTED"


# ── Case 7 — direct unanswered scheduling question => CONFIRMED ─────────────
def test_unanswered_scheduling_question_is_confirmed():
    state = _run([
        ("other", "When are you available for a technical interview this week?", 1),
    ], has_interview=True)
    assert state["reply_obligation"] == "CONFIRMED"


# ── Case 8 — generic "thanks" => NONE ────────────────────────────────────────
def test_generic_thanks_is_none():
    state = _run([
        ("other", "Can you share your availability for next week?", 20),
        ("me", "Sure, I am available Tuesday and Wednesday afternoon.", 20),
        ("other", "Thanks!", 19),
    ])
    assert state["reply_obligation"] == "NONE"


# ── Case 9 — recruiter promises update next week => AWAITING_RECRUITER_UPDATE
def test_recruiter_promises_update():
    state = _run([
        ("other", "I'll get back to you next week with feedback from the client.", 3),
    ])
    assert state["process_state"] == "AWAITING_RECRUITER_UPDATE"
    assert state["reply_obligation"] == "NONE"


# ── Case 10/11 — second interview + availability ask, then Mauricio answers ──
def test_second_interview_request_then_answered():
    unanswered = _run([
        ("other", "The client wants to schedule a second interview — what is your availability next week?", 1),
    ], has_interview=True)
    assert unanswered["reply_obligation"] == "CONFIRMED"
    assert unanswered["process_state"] == "INTERVIEW_PIPELINE"

    answered = _run([
        ("other", "The client wants to schedule a second interview — what is your availability next week?", 2),
        ("me", "I'm available Monday or Wednesday morning, whichever works best.", 1),
    ], has_interview=True)
    assert answered["reply_obligation"] == "NONE"
    assert answered["request_resolved"] is True


# ── Case 12 — rejection without Mauricio response => REJECTED_CLOSED, not Needs Reply
def test_rejection_without_response_not_needs_reply():
    state = _run([
        ("other", "Unfortunately, the position has been filled by another candidate.", 5),
    ])
    assert state["process_state"] == "REJECTED_CLOSED"
    assert state["reply_obligation"] == "NONE"


# ── Case 8 (Part 8 spec) — full interview process then rejection, polite close ─
def test_full_interview_process_then_rejected_relationship_stays_warm():
    state = _run([
        ("other", "Let's do a quick technical screening call this week.", 60),
        ("me", "Sounds good, I'm available.", 60),
        ("other", "Great, let's schedule the interview for Thursday.", 55),
        ("me", "Works for me.", 55),
        ("other", "Could you send your updated CV before the call?", 50),
        ("me", "Sure, attaching it now.", 50),
        ("other", "Your profile has been submitted to the client.", 45),
        ("other", "The client wants to move forward with a second interview.", 30),
        ("me", "Happy to continue, let me know the schedule.", 30),
        ("other", "Unfortunately the client decided to move forward with another candidate this time.", 10),
        ("me", "Thank you for letting me know, I appreciate the opportunity.", 10),
    ], has_interview=True, has_cv=True, priority_score=80)
    assert state["process_state"] == "REJECTED_CLOSED"
    assert state["reply_obligation"] == "NONE"
    assert state["closure_reason"] == "OTHER_CANDIDATE_SELECTED"
    assert state["relationship_value_score"] >= 60  # HIGH relationship value
    assert state["immediate_action_score"] <= 20    # LOW immediate urgency
    assert state["action_urgency"] in ("LOW", "NONE")


def test_cooldown_state_bands():
    assert _cooldown_state("REJECTED_CLOSED", 10) == "NO_ACTION_COOLDOWN"
    assert _cooldown_state("REJECTED_CLOSED", 45) == "MONITOR"
    assert _cooldown_state("REJECTED_CLOSED", 75) == "REACTIVATION_ELIGIBLE"
    assert _cooldown_state("REJECTED_CLOSED", 120) == "REACTIVATE_IF_STRATEGIC"
    assert _cooldown_state("NO_RESPONSE", 120) == ""


def test_lead_category_mapping_never_shows_needs_reply_for_terminal_states():
    for state, cooldown in [
        ("REJECTED_CLOSED", "NO_ACTION_COOLDOWN"),
        ("LOCATION_ELIGIBILITY_BLOCKED", ""),
        ("GEOGRAPHIC_HIRING_RESTRICTION", ""),
        ("WORK_AUTHORIZATION_BLOCKED", ""),
        ("TALENT_POOL_REDIRECT", ""),
        ("CAREER_SITE_REDIRECT", ""),
        ("AUTO_REPLY_ONLY", ""),
    ]:
        cat = _lead_category_v8(state, "NONE", cooldown)
        assert "Needs my response" not in cat, f"{state} incorrectly mapped to {cat}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
