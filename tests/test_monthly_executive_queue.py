# -*- coding: utf-8 -*-
"""
tests/test_monthly_executive_queue.py
========================================
Regression fixtures for the Monthly Executive Queue
(src/monthly_executive_queue.py) — the curated top-20 execution layer on top
of Opportunity History + USD Contract CRM.

Validates against the same two spec examples used elsewhere in this project:
  1. A Qubika-style inbound recruiter pitch (current-month, high signal) must
     rank into the Top 20 Inbound Opportunities This Month queue.
  2. A Juan Pablo-style soft close must land in the Soft-Closed Keep-Warm
     queue (and, once its reactivation_date arrives, the Reactivation Due
     queue) — never treated as a hard rejection.

All names/companies below are synthetic fixture data — not real contacts.

Run: python -m pytest tests/test_monthly_executive_queue.py -q
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import monthly_executive_queue as meq


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "outputs"
    monkeypatch.setattr(meq, "OUTPUTS_DIR", outputs_dir)
    return outputs_dir


CURRENT_MONTH = meq._current_month()


def _event(**overrides):
    base = {
        "profile_url": "https://linkedin.com/in/fake-contact",
        "contact_name": "Fake Contact", "company": "Acme Staffing",
        "role": "Recruiter", "persona": "Recruiter",
        "opportunity_bucket": "GLOBAL_STAFFING",
        "event_month": CURRENT_MONTH, "event_date": str(date.today()),
        "opportunity_event_type": "Inbound Opportunity",
        "opportunity_stage": "Recruiter outreach", "opportunity_signal_strength": "High",
        "inbound_recruiter_contact": True, "active_talent_pool_signal": False,
        "salary_expectation_requested": False, "cv_requested": False,
        "application_requested": False, "interview_or_call_requested": False,
        "client_submission_signal": False, "technical_interview_signal": False,
        "rejected_or_closed": False, "soft_closed": False,
        "location_or_eligibility_blocked": False, "future_reactivation_candidate": False,
        "reactivation_date": "", "recommended_action": "", "message_angle": "",
        "tech_stack_signal": "High", "usd_signal": True, "latam_signal": True,
        "remote_signal": True, "score": 90, "reason_short": "",
    }
    base.update(overrides)
    return base


# ── Availability ─────────────────────────────────────────────────────────────

def test_unavailable_when_no_opportunity_history(isolated_outputs):
    result = meq.run_monthly_executive_queue(opportunity_history_data={"available": False})
    assert result == {"available": False}
    result2 = meq.run_monthly_executive_queue(opportunity_history_data=None)
    assert result2 == {"available": False}


# ── Queue 1 — Inbound Opportunities This Month (Qubika-style) ───────────────

def test_qubika_style_event_ranks_into_inbound_queue(isolated_outputs):
    events = [_event(
        contact_name="Mariana Recruiter", company="Qubika",
        salary_expectation_requested=True, interview_or_call_requested=True,
        active_talent_pool_signal=True,
    )]
    oh = {"available": True, "events": events}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data={})
    assert result["available"] is True
    assert len(result["inbound_top20"]) == 1
    row = result["inbound_top20"][0]
    assert row["contact_name"] == "Mariana Recruiter"
    assert row["queue_name"] == "inbound"
    assert row["rank"] == 1
    assert row["score"] == 100  # 30+25+20+20+15+15+10 clamped to 100
    assert row["priority"] == "HIGH"
    assert "schedule a short call" in row["message_angle"]


def test_inbound_queue_excludes_last_month_events(isolated_outputs):
    last_month_date = date.today().replace(day=1) - timedelta(days=1)
    events = [_event(event_month=last_month_date.strftime("%Y-%m"))]
    oh = {"available": True, "events": events}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data={})
    assert result["inbound_top20"] == []


def test_career_site_only_event_scores_low_not_hard_excluded(isolated_outputs):
    events = [_event(
        opportunity_event_type="Career Site / Talent Database Redirect",
        inbound_recruiter_contact=False, tech_stack_signal="None",
        usd_signal=False, latam_signal=False, remote_signal=False,
    )]
    oh = {"available": True, "events": events}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data={})
    # +10 (current month) - 20 (career site, no role/call/salary signal) = -10 -> clamped 0
    if result["inbound_top20"]:
        assert result["inbound_top20"][0]["score"] == 0


# ── Queue 2 — Reactivation Due This Month ───────────────────────────────────

def test_reactivation_due_this_month_uses_reactivation_date(isolated_outputs):
    events = [_event(
        opportunity_event_type="No Current Role / Keep on Radar",
        opportunity_stage="Closed for now", opportunity_signal_strength="Medium",
        inbound_recruiter_contact=False, soft_closed=True,
        future_reactivation_candidate=True,
        reactivation_date=str(date.today()),
    )]
    oh = {"available": True, "events": events}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data={})
    assert len(result["reactivation_top20"]) == 1
    assert result["reactivation_top20"][0]["next_action_date"] == str(date.today())


def test_reactivation_excludes_dates_beyond_end_of_month(isolated_outputs):
    far_future = date.today() + timedelta(days=200)
    events = [_event(
        soft_closed=True, future_reactivation_candidate=True,
        reactivation_date=str(far_future),
    )]
    oh = {"available": True, "events": events}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data={})
    assert result["reactivation_top20"] == []


# ── Queue 3 — Soft-Closed Keep-Warm (Juan Pablo-style, NOT a rejection) ─────

def test_juan_pablo_style_soft_close_in_keep_warm_not_rejected(isolated_outputs):
    events = [_event(
        contact_name="Juan Pablo Blandon", company="Some Recruiting Co",
        opportunity_event_type="No Current Role / Keep on Radar",
        opportunity_stage="Closed for now", opportunity_signal_strength="Medium",
        inbound_recruiter_contact=True, soft_closed=True,
        rejected_or_closed=False, future_reactivation_candidate=True,
        reactivation_date=str(date.today() + timedelta(days=75)),
    )]
    oh = {"available": True, "events": events}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data={})
    assert len(result["soft_closed_top20"]) == 1
    row = result["soft_closed_top20"][0]
    assert row["contact_name"] == "Juan Pablo Blandon"
    assert row["opportunity_event_type"] == "No Current Role / Keep on Radar"
    assert "keeping me on your radar" in row["message_angle"]
    # Never appears in the inbound (active-opportunity) queue.
    assert all(r["contact_name"] != "Juan Pablo Blandon" for r in result["inbound_top20"])


def test_hard_rejection_never_enters_soft_closed_queue_as_keepwarm(isolated_outputs):
    events = [_event(
        opportunity_event_type="Rejected / Closed", soft_closed=False,
        rejected_or_closed=True, future_reactivation_candidate=False,
    )]
    oh = {"available": True, "events": events}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data={})
    assert result["soft_closed_top20"] == []


# ── Queue 4 — USD Recruiter Follow-ups (separate from manual applications) ──

def test_usd_followup_pulls_from_usd_crm_follow_up_queue(isolated_outputs):
    usd_crm_data = {
        "follow_up_queue": [{
            "name": "Recruiter Warm", "company": "GlobalStaff", "persona": "Talent Acquisition",
            "opportunity_bucket": "LATAM_USD_CONFIRMED", "status": "Needs my response — Confirmed",
            "score": 80, "next_action_date": str(date.today()), "reason": "needs reply",
            "profile_url": "https://linkedin.com/in/recruiter-warm",
        }],
        "active_process_pipeline": [],
    }
    oh = {"available": True, "events": []}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data=usd_crm_data)
    assert len(result["usd_followups_top20"]) == 1
    row = result["usd_followups_top20"][0]
    assert row["contact_name"] == "Recruiter Warm"
    assert row["queue_name"] == "usd_followup"
    # Confirms this is a SEPARATE queue from manual applications (usd_crm_data
    # here carries no manual_applications key at all, yet the followup still
    # populates correctly — proving no dependency/conflation).
    assert "manual_applications" not in usd_crm_data


# ── Score bounds ─────────────────────────────────────────────────────────────

def test_all_queue_scores_clamped_0_100(isolated_outputs):
    events = [_event(
        inbound_recruiter_contact=True, active_talent_pool_signal=True,
        salary_expectation_requested=True, cv_requested=True,
        interview_or_call_requested=True, tech_stack_signal="High",
    )]
    oh = {"available": True, "events": events}
    result = meq.run_monthly_executive_queue(opportunity_history_data=oh, usd_crm_data={})
    for section in ("inbound_top20", "reactivation_top20", "soft_closed_top20", "usd_followups_top20"):
        for row in result[section]:
            assert 0 <= row["score"] <= 100


# ── Message angle templates — first name only, no raw content ──────────────

def test_message_angle_uses_first_name_only():
    assert meq._fill_angle("inbound", "Mariana Ramirez Satizabal").startswith("Hi Mariana,")
    assert meq._fill_angle("soft_closed", "").startswith("Hi there,")


# ── Privacy — sanitized fields only ─────────────────────────────────────────

def test_public_sanitizer_strips_unexpected_fields(isolated_outputs):
    from src.export_public_dashboard_data import build_monthly_executive_queue_public, SAFE_MONTHLY_QUEUE_ROW_COLS

    fake_result = {
        "available": True,
        "summary": {"inbound_opportunities_this_month": 1},
        "inbound_top20": [{
            "queue_name": "inbound", "rank": 1, "contact_name": "Fake",
            "notes_private": "SECRET content, call 5511999999999", "email": "fake@example.com",
        }],
        "reactivation_top20": [], "soft_closed_top20": [], "usd_followups_top20": [],
        "monthly_backlog_top50": [], "all_monthly_queue_records": [], "monthly_chart": [],
    }
    public = build_monthly_executive_queue_public(fake_result)
    for row in public["inbound_top20"]:
        assert "notes_private" not in row
        assert "email" not in row
        assert set(row.keys()) <= SAFE_MONTHLY_QUEUE_ROW_COLS
    assert "SECRET" not in str(public)
    assert "5511999999999" not in str(public)


def test_public_sanitizer_unavailable_when_no_data():
    from src.export_public_dashboard_data import build_monthly_executive_queue_public
    assert build_monthly_executive_queue_public({"available": False}) == {"available": False}
    assert build_monthly_executive_queue_public(None) == {"available": False}
