# -*- coding: utf-8 -*-
"""
tests/test_opportunity_history_engine.py
==========================================
Regression fixtures for the monthly opportunity history engine
(src/opportunity_history_engine.py), validated against the two explicit
classification examples from the spec:
  1. A Qubika-style inbound recruiter pitch that ALSO requests salary and
     proposes a call in the SAME message -> "Inbound Opportunity", High
     signal strength, near-term follow-up (not a long reactivation window).
  2. A Juan Pablo-style soft close ("no open positions... but I'll keep you
     on my radar") -> "No Current Role / Keep on Radar", Medium strength,
     NOT a hard rejection, with a 60-90 day reactivation window.

All names/companies below are synthetic fixture data — not real contacts.

Run: python -m pytest tests/test_opportunity_history_engine.py -q
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import opportunity_history_engine as oh


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "outputs"
    monkeypatch.setattr(oh, "OUTPUTS_DIR", outputs_dir)
    return outputs_dir


def _msg(conv_id, sender, content, when, is_me):
    return {
        "conversation_id": conv_id, "from": sender, "to": "Me",
        "sender_profile_url": f"https://linkedin.com/in/{sender.lower().replace(' ', '-')}",
        "content": content, "date_parsed": when, "is_me_sender": is_me,
    }


# ── Example 1 — Qubika-style inbound opportunity ────────────────────────────

def test_qubika_style_inbound_opportunity():
    msgs = pd.DataFrame([_msg(
        "conv-qubika", "Mariana Recruiter",
        "Data Engineer - Opportunity at Qubika. We are currently seeking several "
        "Data Engineer professionals. You are now part of our Active Talent Pool. "
        "We would like to schedule an initial interview — could you also share "
        "your salary expectations? We work with Databricks and PySpark, dbt, "
        "Terraform, Airflow, SQL and Python, AWS or Azure. Fluent English required.",
        datetime(2026, 7, 25), False,
    )])
    events = oh._build_events_for_conversation("conv-qubika", msgs, {}, {})
    assert len(events) == 1
    e = events[0]
    assert e["opportunity_event_type"] == "Inbound Opportunity"
    assert e["opportunity_signal_strength"] == "High"
    assert e["inbound_recruiter_contact"] is True
    assert e["active_talent_pool_signal"] is True
    assert e["salary_expectation_requested"] is True
    assert e["interview_or_call_requested"] is True
    assert e["tech_stack_signal"] == "High"
    assert e["rejected_or_closed"] is False
    assert e["soft_closed"] is False
    assert e["future_reactivation_candidate"] is False
    # Near-term follow-up (2-3 days), not a long reactivation window.
    assert e["reactivation_date"] == str(date(2026, 7, 25) + oh.timedelta(days=oh._NEAR_TERM_FOLLOWUP_DAYS))
    assert "salary range" in e["recommended_action"].lower()


# ── Example 2 — Juan Pablo-style soft close (NOT a hard rejection) ─────────

def test_juan_pablo_style_soft_close_is_not_a_rejection():
    msgs = pd.DataFrame([_msg(
        "conv-juanpablo", "Juan Pablo Blandon",
        "Hi, thanks for your interest. Unfortunately we don't have open positions "
        "right now, but I will keep you on my radar for future opportunities.",
        datetime(2026, 7, 20), False,
    )])
    events = oh._build_events_for_conversation("conv-juanpablo", msgs, {}, {})
    assert len(events) == 1
    e = events[0]
    assert e["opportunity_event_type"] == "No Current Role / Keep on Radar"
    assert e["opportunity_stage"] == "Closed for now"
    assert e["opportunity_signal_strength"] == "Medium"
    assert e["rejected_or_closed"] is False
    assert e["soft_closed"] is True
    assert e["future_reactivation_candidate"] is True
    d = date.fromisoformat(e["reactivation_date"])
    delta_days = (d - date(2026, 7, 20)).days
    assert 60 <= delta_days <= 90


def test_genuine_hard_rejection_is_not_reclassified_as_soft_close():
    msgs = pd.DataFrame([_msg(
        "conv-rejected", "Some Recruiter",
        "Thank you for interviewing. Unfortunately we decided to move forward "
        "with another candidate for this role. We'll keep you in mind for "
        "future opportunities.",
        datetime(2026, 7, 15), False,
    )])
    events = oh._build_events_for_conversation("conv-rejected", msgs, {}, {})
    e = events[0]
    assert e["opportunity_event_type"] == "Rejected / Closed"
    assert e["rejected_or_closed"] is True


def test_bare_unfortunately_alone_does_not_trigger_hard_rejection():
    """The generic word 'unfortunately' alone (no specific candidacy outcome
    named) must never trigger a hard rejection — this is the exact ambiguity
    the spec's Juan Pablo example warns about."""
    assert oh._kw_match("unfortunately, things came up", oh.STRONG_REJECTION_KW) is False


# ── No-response conversations ────────────────────────────────────────────────

def test_no_reply_from_other_produces_no_response_event():
    msgs = pd.DataFrame([_msg("conv-noreply", "Me", "Hi, interested in connecting!",
                               datetime(2026, 6, 1), True)])
    events = oh._build_events_for_conversation("conv-noreply", msgs, {}, {})
    assert len(events) == 1
    assert events[0]["opportunity_event_type"] == "No Response"
    assert events[0]["reactivation_date"] == ""


# ── Monthly bucketing ────────────────────────────────────────────────────────

def test_events_bucketed_by_month_not_by_message():
    msgs = pd.DataFrame([
        _msg("conv-multi", "Recruiter X", "Send your updated CV please.", datetime(2026, 5, 3), False),
        _msg("conv-multi", "Recruiter X", "Following up — send your updated resume too.", datetime(2026, 5, 20), False),
        _msg("conv-multi", "Recruiter X", "Let's schedule a technical interview next week.", datetime(2026, 6, 2), False),
    ])
    events = oh._build_events_for_conversation("conv-multi", msgs, {}, {})
    # Two calendar months of other-sent messages -> two events, not three.
    assert len(events) == 2
    months = sorted(e["event_month"] for e in events)
    assert months == ["2026-05", "2026-06"]
    may_event = next(e for e in events if e["event_month"] == "2026-05")
    june_event = next(e for e in events if e["event_month"] == "2026-06")
    assert may_event["cv_requested"] is True
    assert june_event["opportunity_event_type"] == "Technical Interview"


# ── Location / eligibility blocks ────────────────────────────────────────────

def test_location_block_detected():
    msgs = pd.DataFrame([_msg(
        "conv-blocked", "EU Recruiter",
        "This role requires fiscal residency and you must reside in Portugal.",
        datetime(2026, 4, 10), False,
    )])
    events = oh._build_events_for_conversation("conv-blocked", msgs, {}, {})
    e = events[0]
    assert e["opportunity_event_type"] == "Location / Eligibility Blocked"
    assert e["location_or_eligibility_blocked"] is True
    assert e["future_reactivation_candidate"] is False
    assert e["reactivation_date"] == ""


# ── Score bounds ─────────────────────────────────────────────────────────────

def test_event_score_clamped_0_100():
    all_true = {k: True for k in [
        "inbound_recruiter_contact", "active_talent_pool_signal",
        "salary_expectation_requested", "cv_requested", "application_requested",
        "interview_or_call_requested", "client_submission_signal",
        "technical_interview_signal", "rejected_or_closed", "soft_closed",
        "location_or_eligibility_blocked",
    ]}
    all_true["tech_stack_signal"] = "High"
    assert 0 <= oh._event_score(all_true) <= 100


# ── Privacy — sanitized output only ──────────────────────────────────────────

def test_no_raw_content_or_pii_in_public_sanitizer(isolated_outputs):
    from src.export_public_dashboard_data import build_opportunity_history_public, SAFE_OPPORTUNITY_EVENT_COLS

    result = {
        "available": True,
        "summary": {"total_events": 1, "inbound_opportunities_total": 1},
        "monthly_pipeline": [{"month": "2026-07", "inbound_opportunities": 1}],
        "events": [{
            "event_id": "abc12345", "conversation_id_hash": "def67890",
            "contact_name": "Fake Person", "company": "Acme",
            "opportunity_event_type": "Inbound Opportunity",
            "reason_short": "inbound recruiter contact",
            "notes_private": "SECRET raw message content, call me at 5511999999999",
            "email": "fake@example.com",
        }],
        "inbound_opportunities": [], "soft_closed_future_leads": [], "reactivation_calendar": [],
    }
    public = build_opportunity_history_public(result)
    for event in public["events"]:
        assert "notes_private" not in event
        assert "email" not in event
        assert set(event.keys()) <= SAFE_OPPORTUNITY_EVENT_COLS
    assert "SECRET" not in str(public)
    assert "5511999999999" not in str(public)


def test_unavailable_when_no_messages(isolated_outputs, monkeypatch):
    monkeypatch.setattr(oh, "load_messages", lambda: None)
    result = oh.run_opportunity_history_engine()
    assert result == {"available": False}
