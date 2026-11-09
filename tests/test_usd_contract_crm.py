# -*- coding: utf-8 -*-
"""
tests/test_usd_contract_crm.py
================================
Regression fixtures for the hybrid USD Contract CRM (src/usd_contract_crm.py):
  1. Manual records from data/manual/*.csv.
  2. Auto-suggested USD pipeline records from Lead Reactivation / Untapped
     Network Intelligence / classified connections + outreach-adjusted scores.

All names/companies below are synthetic fixture data — not real contacts.

Run: python -m pytest tests/test_usd_contract_crm.py -q
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import usd_contract_crm as crm
from src.export_public_dashboard_data import build_usd_contract_crm_public, SAFE_USD_CRM_ROW_COLS


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    manual_dir = tmp_path / "manual"
    outputs_dir = tmp_path / "outputs"
    manual_dir.mkdir()
    monkeypatch.setattr(crm, "DATA_MANUAL_DIR", manual_dir)
    monkeypatch.setattr(crm, "USD_PIPELINE_CSV", manual_dir / "usd_pipeline.csv")
    monkeypatch.setattr(crm, "JOB_APPLICATIONS_CSV", manual_dir / "job_applications.csv")
    monkeypatch.setattr(crm, "RECRUITER_OUTREACH_CSV", manual_dir / "recruiter_outreach_log.csv")
    monkeypatch.setattr(crm, "OUTPUTS_DIR", outputs_dir)
    return manual_dir, outputs_dir


# ── Availability / empty-state (Part 6) ─────────────────────────────────────

def test_nothing_at_all_returns_unavailable(isolated_paths):
    result = crm.run_usd_contract_crm()
    assert result == {"available": False}


def test_blank_template_row_alone_does_not_count_as_data(isolated_paths):
    manual_dir, _ = isolated_paths
    (manual_dir / "usd_pipeline.csv").write_text(
        ",".join(crm.PIPELINE_COLUMNS) + "\n" + ("," * (len(crm.PIPELINE_COLUMNS) - 1)) + "\n",
        encoding="utf-8",
    )
    result = crm.run_usd_contract_crm()
    assert result == {"available": False}


def test_auto_suggested_data_alone_makes_crm_available_with_empty_manual(isolated_paths):
    """The core fix: CRM must NOT be empty when manual CSVs are missing, as
    long as Lead Reactivation / Untapped / classified-connections intelligence
    exists."""
    df = pd.DataFrame([{
        "full_name": "Fake Recruiter", "company_clean": "Acme Staffing",
        "position_clean": "Senior Recruiter", "persona": "Recruiter",
        "opportunity_bucket": "LATAM_USD_CONFIRMED", "priority_score": "80",
        "url": "https://linkedin.com/in/fake-recruiter",
    }])
    result = crm.run_usd_contract_crm(classified_df=df)
    assert result["available"] is True
    assert result["summary"]["manual_usd_opportunities"] == 0
    assert result["summary"]["manual_applications_sent"] == 0
    assert result["summary"]["auto_suggested_usd_leads"] >= 1
    assert result["summary"]["recommended_recruiters_to_contact"] >= 1


# ── usd_crm_score (Part 3) ───────────────────────────────────────────────────

def test_usd_crm_score_confirmed_bucket_plus_recruiter():
    rec = {"opportunity_bucket": "LATAM_USD_CONFIRMED", "persona": "Recruiter"}
    # +30 (confirmed bucket) + +25 (recruiting persona) = 55, no other bonuses/penalties
    assert crm.compute_usd_crm_score(rec) == 55


def test_usd_crm_score_no_signal_gets_penalized():
    rec = {"opportunity_bucket": "", "persona": "Software Engineer", "company": "Local Co", "role": "Dev"}
    assert crm.compute_usd_crm_score(rec) == 0  # -15 clamped at floor 0


def test_usd_crm_score_rejected_low_relationship_value_penalized():
    base = {"persona": "Recruiter"}
    rejected = dict(base, lead_category="Rejected / Closed", relationship_value_score=10)
    warm = dict(base, lead_category="Warm reactivation", relationship_value_score=10)
    # Same persona bonus (+25), but rejected+low-value gets -25 while warm gets +15
    assert crm.compute_usd_crm_score(rejected) < crm.compute_usd_crm_score(warm)


def test_usd_crm_score_location_blocked_penalized():
    rec = {"persona": "Recruiter", "lead_category": "Location / Eligibility Blocked"}
    assert crm.compute_usd_crm_score(rec) == 25 - 20  # recruiter bonus minus blocked penalty


def test_usd_crm_score_never_contacted_bonus():
    with_signal = {"persona": "Recruiter", "contact_history_status": "NEVER_CONTACTED_CONFIRMED"}
    without = {"persona": "Recruiter", "contact_history_status": "HAS_CONVERSATION"}
    assert crm.compute_usd_crm_score(with_signal) - crm.compute_usd_crm_score(without) == 10


def test_usd_crm_score_clamped_0_100():
    huge = {
        "opportunity_bucket": "LATAM_USD_CONFIRMED", "persona": "Recruiter",
        "untapped_outreach_score": 90, "outreach_adjusted_score": 90,
        "relationship_value_score": 90, "lead_category": "Active Interview Pipeline",
        "company": "US LATAM Staffing", "contact_history_status": "NEVER_CONTACTED_CONFIRMED",
    }
    assert crm.compute_usd_crm_score(huge) == 100


# ── Section matchers ─────────────────────────────────────────────────────────

def test_keyword_signal_word_boundary_avoids_false_positive():
    assert crm._has_keyword_signal("Focus Corp") is False  # "us" not a standalone word
    assert crm._has_keyword_signal("US Talent Partners") is True
    assert crm._has_keyword_signal("LATAM Recruiting Co") is True


def test_first_outreach_requires_never_contacted():
    rec = {"persona": "Recruiter", "contact_history_status": "HAS_CONVERSATION"}
    assert crm._match_section_d_first_outreach(rec) is False
    rec2 = dict(rec, contact_history_status="NEVER_CONTACTED_CONFIRMED")
    assert crm._match_section_d_first_outreach(rec2) is True


def test_active_process_matches_cv_and_interview_signals():
    assert crm._match_section_e_active_process({"has_cv_signal": True}) is True
    assert crm._match_section_e_active_process({"has_interview_signal": True}) is True
    assert crm._match_section_e_active_process({"lead_category": "Awaiting Recruiter Update"}) is True
    assert crm._match_section_e_active_process({"lead_category": "Rejected / Closed"}) is False


# ── Auto candidate pool merge ────────────────────────────────────────────────

def test_pool_merges_df_outreach_untapped_lead_by_url():
    df = pd.DataFrame([{
        "full_name": "Fake Person", "company_clean": "Acme", "position_clean": "Recruiter",
        "persona": "Recruiter", "opportunity_bucket": "GLOBAL_STAFFING", "priority_score": "50",
        "url": "https://linkedin.com/in/fake-person/",
    }])
    outreach_scores = {"https://linkedin.com/in/fake-person": {
        "outreach_adjusted_score": 88, "relationship_value_score": 60,
    }}
    untapped_data = {"top_untapped_contacts": [{
        "profile_url": "https://linkedin.com/in/fake-person",
        "untapped_outreach_score": 91, "contact_history_status": "NEVER_CONTACTED_CONFIRMED",
    }]}
    lead_data = {"top_reactivation_contacts": [{
        "other_person_profile_url": "https://linkedin.com/in/fake-person/",
        "lead_category": "Warm reactivation",
    }]}
    pool = crm._build_auto_candidate_pool(df, outreach_scores, untapped_data, lead_data)
    assert len(pool) == 1
    rec = list(pool.values())[0]
    assert rec["persona"] == "Recruiter"
    assert rec["outreach_adjusted_score"] == 88
    assert rec["untapped_outreach_score"] == 91
    assert rec["lead_category"] == "Warm reactivation"


# ── Follow-up queue / risk view (hybrid) ────────────────────────────────────

def test_follow_up_queue_excludes_closed_manual_rows():
    pipeline_df = pd.DataFrame([
        {"company_name": "Acme", "role_title": "DE", "status": "CLOSED_LOST",
         "next_action_date": "2020-01-01", "next_action": "x", "priority": "HIGH"},
        {"company_name": "Beta", "role_title": "DE", "status": "SUBMITTED_TO_CLIENT",
         "next_action_date": "2020-01-01", "next_action": "wait", "priority": "HIGH"},
    ])
    empty_apps = pd.DataFrame(columns=crm.APPLICATION_COLUMNS)
    empty_outreach = pd.DataFrame(columns=crm.OUTREACH_COLUMNS)
    queue = crm._build_follow_up_queue(pipeline_df, empty_apps, empty_outreach, [])
    assert len(queue) == 1
    assert queue[0]["company"] == "Beta"


def test_contingency_risk_flags_any_high_risk_dimension():
    manual_opps = [
        {"company": "A", "timezone_risk": "HIGH", "payment_risk": "LOW", "contract_risk": "LOW", "priority": "HIGH"},
        {"company": "B", "timezone_risk": "LOW", "payment_risk": "LOW", "contract_risk": "LOW", "priority": "BACKUP"},
    ]
    risk = crm._build_contingency_risk(manual_opps)
    assert [r["company"] for r in risk["high_risk"]] == ["A"]
    assert [r["company"] for r in risk["backup"]] == ["B"]


# ── Anti-fabrication (Part 2) ───────────────────────────────────────────────

def test_auto_suggested_leads_never_counted_as_applications_or_manual_opportunities(isolated_paths):
    df = pd.DataFrame([{
        "full_name": "Fake Recruiter", "company_clean": "Acme Staffing",
        "position_clean": "Senior Recruiter", "persona": "Recruiter",
        "opportunity_bucket": "LATAM_USD_CONFIRMED", "priority_score": "80",
        "url": "https://linkedin.com/in/fake-recruiter",
    }])
    lead_data = {"top_reactivation_contacts": [{
        "other_person_profile_url": "https://linkedin.com/in/fake-recruiter",
        "lead_category": "Active Interview Pipeline",
        "has_cv_signal": "True",
    }]}
    result = crm.run_usd_contract_crm(classified_df=df, lead_data=lead_data)
    assert result["summary"]["manual_usd_opportunities"] == 0
    assert result["summary"]["manual_applications_sent"] == 0
    # But the CV signal from message intelligence DOES count toward the
    # separate "cv_requested_or_sent_signals" card (explicit signal, not
    # fabricated from a bare recruiter match).
    assert result["summary"]["cv_requested_or_sent_signals"] >= 1
    assert result["summary"]["active_interview_signals"] >= 1


# ── Privacy — notes_private and unified schema enforcement ─────────────────

def test_notes_private_never_in_manual_opportunities(isolated_paths):
    manual_dir, _ = isolated_paths
    df = pd.DataFrame([{
        **{c: "" for c in crm.PIPELINE_COLUMNS},
        "company_name": "Acme", "role_title": "Data Engineer", "currency": "USD",
        "status": "new", "notes_private": "secret salary negotiation details, phone +5511999999999",
    }])
    df.to_csv(manual_dir / "usd_pipeline.csv", index=False)
    result = crm.run_usd_contract_crm()
    assert result["available"] is True
    for record in result["manual_opportunities"]:
        assert "notes_private" not in record
        assert "secret" not in str(record.values())
        assert set(record.keys()) <= set(crm.PUBLIC_ROW_FIELDS)

    public = build_usd_contract_crm_public(result)
    for section in ("manual_opportunities", "auto_suggested_usd_leads", "recruiter_pipeline",
                     "first_outreach_queue", "follow_up_queue", "active_process_pipeline",
                     "manual_applications"):
        for record in public[section]:
            assert "notes_private" not in record
            assert set(record.keys()) <= SAFE_USD_CRM_ROW_COLS


def test_build_usd_contract_crm_public_unavailable_when_no_data():
    assert build_usd_contract_crm_public({"available": False}) == {"available": False}
    assert build_usd_contract_crm_public(None) == {"available": False}


def test_all_auto_suggested_rows_conform_to_unified_schema(isolated_paths):
    df = pd.DataFrame([{
        "full_name": "Fake Recruiter", "company_clean": "Acme Staffing",
        "position_clean": "Senior Recruiter", "persona": "Recruiter",
        "opportunity_bucket": "LATAM_USD_CONFIRMED", "priority_score": "80",
        "url": "https://linkedin.com/in/fake-recruiter",
    }])
    result = crm.run_usd_contract_crm(classified_df=df)
    for section in ("auto_suggested_usd_leads", "recruiter_pipeline", "first_outreach_queue",
                     "active_process_pipeline"):
        for record in result[section]:
            assert set(record.keys()) == set(crm.PUBLIC_ROW_FIELDS)


# ── Conversion funnel — confirmed vs inferred, never merged ─────────────────

def _funnel_events():
    base = {"opportunity_event_type": "No Response", "inbound_recruiter_contact": False,
            "cv_requested": False, "interview_or_call_requested": False,
            "technical_interview_signal": False, "client_submission_signal": False}
    return [
        {**base, "conversation_id_hash": "c1"},
        {**base, "conversation_id_hash": "c2", "opportunity_event_type": "Inbound Opportunity",
         "inbound_recruiter_contact": True},
        # same conversation, later month — must not double count
        {**base, "conversation_id_hash": "c2", "opportunity_event_type": "CV Requested", "cv_requested": True},
        {**base, "conversation_id_hash": "c3", "opportunity_event_type": "CV Requested", "cv_requested": True},
    ]


def test_funnel_confirmed_not_measured_without_manual_rows():
    from src.usd_contract_crm import build_conversion_funnel
    f = build_conversion_funnel(pd.DataFrame(), {"available": True, "events": _funnel_events()})
    stages = {s["stage"]: s for s in f["stages"]}
    assert f["confirmed_measured"] is False
    assert all(s["confirmed"] is None for s in f["stages"])
    assert stages["Contact"]["inferred"] == 3
    assert stages["Response"]["inferred"] == 2
    assert stages["Vacancy"]["inferred"] == 1
    assert stages["CV"]["inferred"] == 2
    # Offers/contracts are never inferred from messages.
    assert stages["Offer"]["inferred"] is None
    assert stages["Contract"]["inferred"] is None
    assert "pct" not in str(f).lower() and "rate" not in str(f["stages"]).lower()


def test_funnel_confirmed_counts_use_manual_stage_ordinals():
    from src.usd_contract_crm import build_conversion_funnel
    df = pd.DataFrame({"status": ["CV_SENT", "TECHNICAL_INTERVIEW_DONE", "REJECTED", "CLOSED_WON"]})
    stages = {s["stage"]: s["confirmed"] for s in build_conversion_funnel(df, None)["stages"]}
    assert stages["Vacancy"] == 4
    assert stages["CV"] == 3            # REJECTED row's furthest stage is unknown
    assert stages["Interview"] == 2
    assert stages["Contract"] == 1


def test_funnel_public_sanitizer_numbers_only():
    from src.export_public_dashboard_data import build_conversion_funnel_public
    pub = build_conversion_funnel_public({"available": True, "confirmed_measured": False,
        "stages": [{"stage": "CV", "confirmed": None, "inferred": 5, "contact_name": "X"}], "note": "n"})
    assert pub["stages"] == [{"stage": "CV", "confirmed": None, "inferred": 5}]
    assert build_conversion_funnel_public(None) == {"available": False}


# ── Manual-review candidates — inferred, never auto-confirmed ───────────────

def _rc_event(conv, date, etype, url="https://linkedin.com/in/rec-a", company="Acme", **flags):
    e = {"conversation_id_hash": conv, "event_date": date, "opportunity_event_type": etype,
         "profile_url": url, "company": company, "contact_name": "Rec A"}
    e.update({k: True for k, v in flags.items() if v})
    return e


def test_review_candidates_dedupe_by_conversation_and_never_set_status():
    from src.usd_contract_crm import build_review_candidates
    oh = {"available": True, "events": [
        _rc_event("c1", "2026-10-01", "Inbound Opportunity", inbound_recruiter_contact=True),
        _rc_event("c1", "2026-10-20", "CV Requested", cv_requested=True),
        _rc_event("c1", "2026-11-03", "Technical Interview", technical_interview_signal=True),
        _rc_event("c9", "2026-11-04", "No Response"),  # no process signal -> excluded
    ]}
    rows = build_review_candidates(oh, pd.DataFrame())
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == ""  # left for the human to confirm
    assert r["review_suggested_status"] == "TECHNICAL_INTERVIEW_SCHEDULED"
    assert r["review_first_signal_date"] == "2026-10-01"
    assert r["review_stage_timeline"].count(";") == 2


def test_review_candidates_keep_distinct_threads_at_same_company():
    from src.usd_contract_crm import build_review_candidates
    oh = {"available": True, "events": [
        _rc_event("c1", "2026-11-01", "CV Requested", url="https://linkedin.com/in/a", cv_requested=True),
        _rc_event("c2", "2026-11-02", "CV Requested", url="https://linkedin.com/in/b", cv_requested=True),
    ]}
    assert len(build_review_candidates(oh, pd.DataFrame())) == 2


def test_review_candidates_latest_rejection_wins_and_logged_rows_skipped():
    from src.usd_contract_crm import build_review_candidates
    oh = {"available": True, "events": [
        _rc_event("c1", "2026-09-01", "CV Requested", cv_requested=True),
        _rc_event("c1", "2026-10-01", "Rejected / Closed", rejected_or_closed=True),
        _rc_event("c2", "2026-10-05", "CV Requested", url="https://linkedin.com/in/logged/", cv_requested=True),
    ]}
    logged = pd.DataFrame({"recruiter_profile_url": ["https://linkedin.com/in/logged"]})
    rows = build_review_candidates(oh, logged)
    assert [r["review_suggested_status"] for r in rows] == ["REJECTED"]
