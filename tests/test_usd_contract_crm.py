# -*- coding: utf-8 -*-
"""
tests/test_usd_contract_crm.py
================================
Regression fixtures for the USD Contract CRM (src/usd_contract_crm.py).

All names/companies below are synthetic fixture data — not real contacts.

Run: python -m pytest tests/test_usd_contract_crm.py -q
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import usd_contract_crm as crm
from src.export_public_dashboard_data import build_usd_contract_crm_public


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


# ── Availability / empty-state ──────────────────────────────────────────────

def test_no_manual_csvs_returns_unavailable(isolated_paths):
    result = crm.run_usd_contract_crm()
    assert result == {"available": False}


def test_blank_template_row_does_not_count_as_data(isolated_paths):
    manual_dir, _ = isolated_paths
    (manual_dir / "usd_pipeline.csv").write_text(
        ",".join(crm.PIPELINE_COLUMNS) + "\n" + ("," * (len(crm.PIPELINE_COLUMNS) - 1)) + "\n",
        encoding="utf-8",
    )
    result = crm.run_usd_contract_crm()
    assert result["available"] is True
    assert result["summary"]["usd_opportunities_found"] == 0


# ── USD Pipeline Score ───────────────────────────────────────────────────────

def test_score_full_positive_signals_caps_at_100():
    row = {
        "currency": "USD", "remote_policy": "Remote worldwide, Brazil OK",
        "status": "submitted_to_client", "tech_stack": "Azure, dbt, SQL",
        "contract_type": "12-month contract", "location_restriction": "",
        "timezone_risk": "LOW", "rate_type": "hourly", "rate_min": "40", "rate_max": "55",
    }
    assert crm.compute_usd_pipeline_score(row) == 100


def test_score_full_negative_signals_floors_at_0():
    row = {
        "currency": "BRL", "remote_policy": "Onsite only",
        "status": "rejected", "tech_stack": "",
        "contract_type": "3 months", "location_restriction": "US only, no digital nomad",
        "timezone_risk": "HIGH", "rate_type": "monthly", "rate_min": "1000", "rate_max": "1500",
    }
    assert crm.compute_usd_pipeline_score(row) == 0


def test_score_milestones_are_cumulative():
    base = {
        "currency": "EUR", "remote_policy": "", "tech_stack": "",
        "contract_type": "", "location_restriction": "", "timezone_risk": "",
        "rate_type": "", "rate_min": "", "rate_max": "",
    }
    new_row = dict(base, status="new")
    submitted_row = dict(base, status="submitted_to_client")
    # SUBMITTED_TO_CLIENT implies recruiter replied (+15) + CV requested (+15)
    # + submitted (+20) = +50 over a bare NEW status.
    assert crm.compute_usd_pipeline_score(submitted_row) - crm.compute_usd_pipeline_score(new_row) == 50


def test_below_target_usd_hourly_rate_penalized():
    assert crm._below_target_rate("USD", "hourly", "10", "15") is True
    assert crm._below_target_rate("USD", "hourly", "40", "60") is False
    assert crm._below_target_rate("BRL", "hourly", "10", "15") is False


def test_location_blocks_brazil_keywords():
    assert crm._location_blocks_brazil("US only, no digital nomad") is True
    assert crm._location_blocks_brazil("Worldwide remote, Brazil OK") is False


def test_contract_long_term_detection():
    assert crm._contract_is_long_term("12-month contract") is True
    assert crm._contract_is_long_term("Permanent, full-time") is True
    assert crm._contract_is_long_term("3 month trial") is False


# ── Follow-up queue / risk view ──────────────────────────────────────────────

def test_follow_up_queue_excludes_closed_rows():
    pipeline_df = pd.DataFrame([
        {"company_name": "Acme", "role_title": "DE", "status": "CLOSED_LOST",
         "next_action_date": "2020-01-01", "next_action": "x", "priority": "HIGH"},
        {"company_name": "Beta", "role_title": "DE", "status": "SUBMITTED_TO_CLIENT",
         "next_action_date": "2020-01-01", "next_action": "wait", "priority": "HIGH"},
    ])
    empty_apps = pd.DataFrame(columns=crm.APPLICATION_COLUMNS)
    empty_outreach = pd.DataFrame(columns=crm.OUTREACH_COLUMNS)
    queue = crm._build_follow_up_queue(pipeline_df, empty_apps, empty_outreach)
    assert len(queue) == 1
    assert queue[0]["name"].startswith("Beta")
    assert queue[0]["overdue"] is True


def test_risk_view_flags_any_high_risk_dimension():
    public_pipeline = [
        {"company_name": "A", "timezone_risk": "HIGH", "payment_risk": "LOW", "contract_risk": "LOW", "priority": "HIGH"},
        {"company_name": "B", "timezone_risk": "LOW", "payment_risk": "LOW", "contract_risk": "LOW", "priority": "BACKUP"},
    ]
    risk = crm._build_risk_view(public_pipeline)
    assert [r["company_name"] for r in risk["high_risk"]] == ["A"]
    assert [r["company_name"] for r in risk["backup"]] == ["B"]


# ── Privacy — notes_private must never survive sanitization ────────────────

def test_notes_private_never_in_public_pipeline(isolated_paths):
    manual_dir, _ = isolated_paths
    df = pd.DataFrame([{
        **{c: "" for c in crm.PIPELINE_COLUMNS},
        "company_name": "Acme", "role_title": "Data Engineer", "currency": "USD",
        "status": "new", "notes_private": "secret salary negotiation details, phone +5511999999999",
    }])
    df.to_csv(manual_dir / "usd_pipeline.csv", index=False)
    result = crm.run_usd_contract_crm()
    assert result["available"] is True
    for record in result["pipeline"]:
        assert "notes_private" not in record
        assert "secret" not in str(record.values())

    public = build_usd_contract_crm_public(result)
    for record in public["pipeline"]:
        assert "notes_private" not in record
        assert set(record.keys()) <= set(crm.PUBLIC_PIPELINE_FIELDS)


def test_build_usd_contract_crm_public_unavailable_when_no_data():
    assert build_usd_contract_crm_public({"available": False}) == {"available": False}
    assert build_usd_contract_crm_public(None) == {"available": False}
