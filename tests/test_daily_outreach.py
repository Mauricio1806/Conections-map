# -*- coding: utf-8 -*-
"""
tests/test_daily_outreach.py
=============================
Regression fixtures for the Daily Outreach Command Center
(src/daily_outreach.py).

Covers the spec's nine Daily Outreach guarantees:
  1. No duplicate contact/action entries in the Top 15.
  2. High-value active opportunities outrank low-value first outreach.
  3. Old courtesy / historical messages are not urgent.
  4. Location-blocked roles do not enter the USD priority queues.
  5. Never-contacted recruiter status is respected.
  6. KPI action counts match the filtered records.
  7. Manual completion is never fabricated.
  8. Private notes never enter public JSON.
  9. Repeated weekly refresh does not duplicate activity.

All names/companies below are synthetic fixture data — not real contacts.

Run: python -m pytest tests/test_daily_outreach.py -q
"""

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import daily_outreach as do  # noqa: E402

AS_OF = date(2026, 11, 16)


def _lead(name, url, **kw):
    base = {
        "other_person_name": name, "other_person_profile_url": url,
        "company_clean": "Acme Nearshore", "position_clean": "Technical Recruiter",
        "persona": "Recruiter", "strategic_market": "LATAM_USD",
        "process_state": "WARM_RELATIONSHIP_NO_ACTIVE_ROLE",
        "response_queue_segment": "HIGH_VALUE_RECRUITER_REPLY",
        "recommended_response_timing": "THIS_WEEK",
        "conversation_recency_band": "AGING", "days_since_last_message": 60,
        "last_message_date": "2026-09-17", "last_sender_type": "me",
        "response_priority_score": 80, "sourcing_quality_segment": "HIGH_FIT_USD_REMOTE",
        "next_action_date": "2026-11-16", "sanitized_intent_label": "Generic acknowledgement",
        "message_angle": "Reply directly based on their last message.",
        "lead_category": "Warm reactivation",
    }
    base.update(kw)
    return base


def _untapped(name, url, **kw):
    base = {
        "full_name": name, "profile_url": url, "company_clean": "Talent Bridge LATAM",
        "position_clean": "IT Recruiter", "persona": "Recruiter",
        "opportunity_bucket": "GLOBAL_STAFFING", "untapped_opportunity_market": "Global Staffing",
        "strategic_focus": "PRIMARY_LATAM_USD", "contact_history_status": "NEVER_CONTACTED_CONFIRMED",
        "untapped_execution_score": 100, "untapped_activation_potential_score": 100,
        "untapped_outreach_score": 95, "first_message_priority": "TODAY",
        "days_connected": 400, "connected_on": "2025-10-12",
        "recommended_first_action": "FIRST_MESSAGE_RECRUITER",
        "activation_reason": "never contacted recruiter",
    }
    base.update(kw)
    return base


def _payload(leads=(), untapped=(), usd=None):
    return {
        "meta": {"report_date": "2026-11-15"},
        "lead_reactivation": {"top_reactivation_contacts": list(leads)},
        "untapped_network": {"top_untapped_contacts": list(untapped), "summary": {}},
        "usd_contract_crm": usd or {},
        "weekly_history": [],
    }


def _row(out, name):
    return next(r for r in out["rows"] if r["name"] == name)


def _kpi_count(out, code):
    pred = dict((c, p) for c, _l, p in do.KPI_DEFS)[code]
    return sum(1 for r in out["rows"] if pred(r, AS_OF))


def _empty_manual():
    return do.load_manual_activity(Path("does/not/exist.csv"))


# 1 ────────────────────────────────────────────────────────────────────────
def test_top15_has_no_duplicate_contacts():
    url = "https://www.linkedin.com/in/ana-dup"
    leads = [_lead("Ana Dup", url, process_state="CV_REQUESTED", conversation_recency_band="CURRENT",
                   days_since_last_message=3, last_sender_type="other",
                   response_queue_segment="HIGH_VALUE_RECRUITER_REPLY")]
    # Same person also present in Untapped (e.g. stale untapped row) + URL variant.
    untapped = [_untapped("Ana Dup", "https://linkedin.com/in/ana-dup/")]
    untapped += [_untapped(f"Recruiter {i}", f"https://www.linkedin.com/in/r-{i}") for i in range(30)]
    out = do.build_daily_outreach(_payload(leads, untapped), _empty_manual(), as_of=AS_OF)
    ids = out["top15_ids"]
    assert len(ids) == len(set(ids)) <= 15
    assert sum(1 for r in out["rows"] if r["name"] == "Ana Dup") == 1
    ana = _row(out, "Ana Dup")
    # Conversation history wins: she is no longer "never contacted".
    assert "FIRST_OUTREACH" not in ana["q"] and "nc_recruiter" not in ana["tags"]


# 2 ────────────────────────────────────────────────────────────────────────
def test_active_opportunity_outranks_first_outreach():
    leads = [_lead("Bea Interview", "https://www.linkedin.com/in/bea", process_state="INTERVIEW_PIPELINE",
                   conversation_recency_band="CURRENT", days_since_last_message=2,
                   last_sender_type="other", sanitized_intent_label="Interview scheduling")]
    untapped = [_untapped("Top Cold Recruiter", "https://www.linkedin.com/in/cold", untapped_outreach_score=100)]
    out = do.build_daily_outreach(_payload(leads, untapped), _empty_manual(), as_of=AS_OF)
    bea, cold = _row(out, "Bea Interview"), _row(out, "Top Cold Recruiter")
    assert bea["cat"] == "ACTIVE_PROCESS" and bea["score"] > cold["score"]
    assert out["top15_ids"].index(bea["id"]) < out["top15_ids"].index(cold["id"])


# 3 ────────────────────────────────────────────────────────────────────────
def test_old_courtesy_and_historical_messages_are_not_urgent():
    leads = [
        _lead("Old Courtesy", "https://www.linkedin.com/in/oc", response_queue_segment="LOW_PRIORITY_COURTESY",
              recommended_response_timing="NO_ACTION_NEEDED", courtesy_only_flag=True,
              conversation_recency_band="HISTORICAL", days_since_last_message=700, last_sender_type="other"),
        _lead("Ancient CV Ask", "https://www.linkedin.com/in/acv", process_state="CV_REQUESTED",
              conversation_recency_band="HISTORICAL", days_since_last_message=400, last_sender_type="other",
              response_queue_segment="REACTIVATION_DUE_HIGH_VALUE", recommended_response_timing="THIS_WEEK"),
    ]
    out = do.build_daily_outreach(_payload(leads), _empty_manual(), as_of=AS_OF)
    names = {r["name"] for r in out["rows"]}
    assert "Old Courtesy" not in names
    acv = _row(out, "Ancient CV Ask")
    assert "REPLY_NOW" not in acv["q"] and "ACTIVE_PROCESS" not in acv["q"]
    assert "urgent" not in acv["tags"] and "cv_salary_call" not in acv["tags"]
    assert _kpi_count(out, "urgent") == 0


# 4 ────────────────────────────────────────────────────────────────────────
def test_location_blocked_never_enters_usd_queues():
    leads = [
        _lead("Onsite Only", "https://www.linkedin.com/in/onsite", onsite_or_local_only_flag=True,
              process_state="CV_REQUESTED", conversation_recency_band="CURRENT", days_since_last_message=2,
              recommended_response_timing="TODAY"),
        _lead("Mexico Local", "https://www.linkedin.com/in/mxlocal", mexico_local_only_flag=True),
        _lead("Blocked State", "https://www.linkedin.com/in/blocked", process_state="LOCATION_ELIGIBILITY_BLOCKED"),
        # A Mexican recruiter WITHOUT a demonstrated restriction stays eligible.
        _lead("Mexico Remote OK", "https://www.linkedin.com/in/mxok", company_clean="Talento México Remote"),
    ]
    usd = {"disqualified_local_only": [{"profile_url": "https://www.linkedin.com/in/crm-blocked"}]}
    untapped = [_untapped("CRM Blocked", "https://www.linkedin.com/in/crm-blocked")]
    out = do.build_daily_outreach(_payload(leads, untapped, usd), _empty_manual(), as_of=AS_OF)
    names = {r["name"] for r in out["rows"]}
    assert not names & {"Onsite Only", "Mexico Local", "Blocked State", "CRM Blocked"}
    mx = _row(out, "Mexico Remote OK")
    assert "USD_FOLLOWUP" in mx["q"] and mx["lang"] == "es"
    assert all(r["name"] not in {"Onsite Only", "Mexico Local"} for r in out["rows"] if r.get("top_rank"))


# 5 ────────────────────────────────────────────────────────────────────────
def test_never_contacted_status_is_respected():
    untapped = [
        _untapped("Never Rec", "https://www.linkedin.com/in/nr"),
        _untapped("Ambiguous Rec", "https://www.linkedin.com/in/ar", contact_history_status="AMBIGUOUS_REVIEW"),
        _untapped("Never TA", "https://www.linkedin.com/in/nta", persona="Talent Acquisition"),
    ]
    out = do.build_daily_outreach(_payload(untapped=untapped), _empty_manual(), as_of=AS_OF)
    names = {r["name"] for r in out["rows"]}
    assert "Ambiguous Rec" not in names
    assert _kpi_count(out, "nc_recruiter") == 1 and _kpi_count(out, "nc_ta") == 1
    nr = _row(out, "Never Rec")
    assert "LONG_CONNECTED" in nr["q"] and "long_connected" in nr["tags"]
    assert nr["msg"].startswith("Hi Never") and "Senior Data Engineer" in nr["msg"]


# 6 ────────────────────────────────────────────────────────────────────────
def test_kpi_counts_match_filtered_records():
    leads = [
        _lead("R1", "https://www.linkedin.com/in/r1", recommended_response_timing="TODAY",
              process_state="CV_REQUESTED", conversation_recency_band="RECENT", days_since_last_message=10,
              last_sender_type="other", sanitized_intent_label="Asked for CV",
              response_queue_segment="ACTIVE_PROCESS_NEEDS_REPLY"),
        _lead("F1", "https://www.linkedin.com/in/f1", next_action_date="2026-11-10"),
        _lead("F2", "https://www.linkedin.com/in/f2", next_action_date="2026-12-10"),
        _lead("RE1", "https://www.linkedin.com/in/re1", response_queue_segment="REACTIVATION_DUE_HIGH_VALUE",
              next_action_date="2026-11-01"),
    ]
    untapped = [_untapped(f"U{i}", f"https://www.linkedin.com/in/u{i}",
                          persona="Recruiter" if i % 2 else "Talent Acquisition") for i in range(6)]
    out = do.build_daily_outreach(_payload(leads, untapped), _empty_manual(), as_of=AS_OF)
    for code, k in out["kpis"].items():
        assert k["count"] == _kpi_count(out, code), code
    assert out["kpis"]["urgent"]["count"] == 1
    assert out["kpis"]["followup_due"]["count"] == 1      # F1 due, F2 future
    assert out["kpis"]["reactivation_due"]["count"] == 1
    assert out["kpis"]["nc_recruiter"]["count"] == 3 and out["kpis"]["nc_ta"]["count"] == 3
    for q, info in out["queues"].items():
        assert info["count"] == sum(1 for r in out["rows"] if q in r["q"])
    assert out["summary"]["total_actionable"] == len(out["rows"])


# 7 ────────────────────────────────────────────────────────────────────────
def test_manual_completion_is_never_fabricated(tmp_path):
    leads = [_lead("Hot Lead", "https://www.linkedin.com/in/hot", recommended_response_timing="TODAY",
                   process_state="ACTIVE_OPPORTUNITY", conversation_recency_band="CURRENT",
                   days_since_last_message=1, last_sender_type="other")]
    untapped = [_untapped("Cold", "https://www.linkedin.com/in/cold2")]
    out = do.build_daily_outreach(_payload(leads, untapped), _empty_manual(), as_of=AS_OF)
    assert all(r["ev"] == "inferred" and "manual" not in r for r in out["rows"])
    assert out["summary"]["completed_today"] is None and out["summary"]["completed_this_week"] is None
    assert out["kpis"]["confirmed"]["count"] == 0
    assert all(m["values"] == [None] * len(m["values"]) for m in out["performance"]["manual"])
    assert all(r["value"] is None for r in out["performance"]["rates"])
    # With a logged first message the contact moves to follow-up — it does NOT become "replied".
    log = tmp_path / "log.csv"
    pd.DataFrame([{c: "" for c in do.OUTREACH_LOG_COLUMNS} | {
        "date": "2026-11-16", "contact_name": "Cold", "profile_url": "https://www.linkedin.com/in/cold2",
        "company": "Talent Bridge LATAM", "message_type": "FIRST_MESSAGE"}]).to_csv(log, index=False)
    out2 = do.build_daily_outreach(_payload(leads, untapped), do.load_manual_activity(log), as_of=AS_OF)
    cold = _row(out2, "Cold")
    assert cold["ev"] == "manual" and cold["manual"]["type"] == "FIRST_MESSAGE"
    assert "FIRST_OUTREACH" not in cold["q"] and "USD_FOLLOWUP" in cold["q"]
    assert cold["fu"] == "2026-11-23" and "nc_recruiter" not in cold["tags"]
    assert _row(out2, "Hot Lead")["ev"] == "inferred"
    assert out2["summary"]["completed_today"] == 1
    replies = next(m for m in out2["performance"]["manual"] if m["code"] == "RECRUITER_REPLIED")
    assert replies["values"][-1] == 0


# 8 ────────────────────────────────────────────────────────────────────────
def test_private_notes_never_enter_public_json(tmp_path):
    secret = "PRIVATE-NOTE-SALARY-9876 call me"
    log = tmp_path / "log.csv"
    pd.DataFrame([{c: "" for c in do.OUTREACH_LOG_COLUMNS} | {
        "date": "2026-11-15", "contact_name": "Noted Person", "profile_url": "https://www.linkedin.com/in/noted",
        "company": "Acme", "message_type": "follow up", "notes_private": secret}]).to_csv(log, index=False)
    manual = do.load_manual_activity(log)
    assert "notes_private" not in manual.columns
    leads = [_lead("Noted Person", "https://www.linkedin.com/in/noted")]
    out = do.build_daily_outreach(_payload(leads), manual, as_of=AS_OF)
    text = json.dumps(out, ensure_ascii=False)
    assert secret not in text and "PRIVATE-NOTE" not in text
    assert '"notes_private"' not in text
    assert _row(out, "Noted Person")["manual"]["type"] == "FOLLOW_UP"


# 9 ────────────────────────────────────────────────────────────────────────
def test_repeated_refresh_does_not_duplicate_activity(tmp_path):
    row = {c: "" for c in do.OUTREACH_LOG_COLUMNS} | {
        "date": "2026-11-16", "contact_name": "Rep Person", "profile_url": "https://www.linkedin.com/in/rep",
        "company": "Acme", "message_type": "FIRST_MESSAGE"}
    log = tmp_path / "log.csv"
    # Same event appended twice (e.g. draft CSV pasted twice) + URL variant.
    pd.DataFrame([row, row, row | {"profile_url": "https://linkedin.com/in/rep/"}]).to_csv(log, index=False)
    manual = do.load_manual_activity(log)
    assert len(manual) == 1
    payload = _payload(untapped=[_untapped("Rep Person", "https://www.linkedin.com/in/rep")])
    out1 = do.build_daily_outreach(payload, manual, as_of=AS_OF)
    out2 = do.build_daily_outreach(payload, manual, as_of=AS_OF)
    assert json.dumps(out1, sort_keys=True) == json.dumps(out2, sort_keys=True)
    first = next(m for m in out1["performance"]["manual"] if m["code"] == "FIRST_MESSAGE")
    assert first["values"][-1] == 1


def test_spain_eu_stays_exploratory_and_out_of_top15():
    untapped = [_untapped("Madrid Rec", "https://www.linkedin.com/in/mad", strategic_focus="SPAIN_EU_EXPLORATORY",
                          untapped_opportunity_market="Spain/EU (confirmed)", opportunity_bucket="SPAIN_EU_CONFIRMED")]
    out = do.build_daily_outreach(_payload(untapped=untapped), _empty_manual(), as_of=AS_OF)
    mad = _row(out, "Madrid Rec")
    assert mad["q"] == ["SPAIN_EU"] and mad["score"] <= 40
    assert mad["id"] not in out["top15_ids"]


def test_public_ids_never_look_like_phone_numbers():
    import re
    untapped = [_untapped(f"Person {i}", f"https://www.linkedin.com/in/p-{i}") for i in range(300)]
    out = do.build_daily_outreach(_payload(untapped=untapped), _empty_manual(), as_of=AS_OF)
    assert not re.search(r"(?<!-)\b\d{10,15}\b", json.dumps([r["id"] for r in out["rows"]]))


def test_privacy_check_rejects_private_fields_in_daily_outreach(tmp_path):
    from src import privacy_check as pc
    bad = tmp_path / "daily_outreach.json"
    bad.write_text(json.dumps({"daily_outreach": {"rows": [
        {"id": "cabc", "name": "X", "notes": "private"},
        {"id": "cdef", "name": "Y", "manual": {"type": "FOLLOW_UP", "notes_private": "secret"}},
    ]}}), encoding="utf-8")
    violations = pc.check_json(bad)
    assert any("'notes'" in v for v in violations)
    assert any("notes_private" in v for v in violations)
    good = tmp_path / "ok.json"
    good.write_text(json.dumps({"daily_outreach": {"rows": [{"id": "cabc", "name": "X", "tags": []}]}}), encoding="utf-8")
    assert pc.check_json(good) == []
