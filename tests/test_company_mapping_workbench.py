# -*- coding: utf-8 -*-
"""
tests/test_company_mapping_workbench.py
=========================================
Regression fixtures for the Company Mapping Workbench —
src/company_mapping_workbench.py: _classify_candidate(), _yaml_suggestion(),
run_company_mapping_workbench() — plus the paired fix in
src/opportunity_market_v5.py: _load_manual_overrides() (previously a bug
made every entry in config/company_market_overrides.yml a silent no-op).

All names/companies below are synthetic fixture data — not real contacts.

Run: python -m pytest tests/test_company_mapping_workbench.py -q
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.company_mapping_workbench import (
    _classify_candidate,
    _yaml_suggestion,
    run_company_mapping_workbench,
)
from src.opportunity_market_v5 import _load_manual_overrides, _classify_row


# ── _load_manual_overrides() — the parsing bug fix ───────────────────────────

def test_load_manual_overrides_parses_nested_market_category_shape(tmp_path, monkeypatch):
    yml = tmp_path / "overrides.yml"
    yml.write_text(
        "overrides:\n"
        "  fakestaffingco:  {market: LATAM_USD, category: GLOBAL_STAFFING}\n"
        "  \"fake, inc.\":   {market: US_CANADA_NEARSHORE, category: GLOBAL_TECH}\n",
        encoding="utf-8",
    )
    import src.opportunity_market_v5 as m
    monkeypatch.setattr(m, "OVERRIDES_YML", yml)
    result = m._load_manual_overrides()
    assert result == {"fakestaffingco": "LATAM_USD", "fake, inc.": "US_CANADA_NEARSHORE"}


def test_load_manual_overrides_still_accepts_flat_string_shape(tmp_path, monkeypatch):
    """Backward compatibility: company -> plain market string (no nested dict)."""
    yml = tmp_path / "overrides.yml"
    yml.write_text("fakeflatco: LATAM_USD\n", encoding="utf-8")
    import src.opportunity_market_v5 as m
    monkeypatch.setattr(m, "OVERRIDES_YML", yml)
    result = m._load_manual_overrides()
    assert result == {"fakeflatco": "LATAM_USD"}


def test_manual_override_resolves_end_to_end_via_classify_row():
    overrides = {"fake latam recruiter co": "LATAM_USD"}
    row = pd.Series({
        "company_clean": "Fake LATAM Recruiter Co", "position_clean": "", "persona": "",
        "market_v4": "", "language_detected": "", "priority_score": 50,
    })
    bucket, conf, reason = _classify_row(row, overrides, "market_v4", "language_detected")
    assert bucket == "LATAM_USD_CONFIRMED"
    assert conf == 0.95
    assert reason == "manual override"


# ── _classify_candidate() — never fabricates geography ──────────────────────

def test_classify_candidate_no_signal_stays_needs_mapping():
    result = _classify_candidate(
        "Fake Neutral Holdings", follow_bucket="", follow_category="", follow_confidence=0.0,
        has_recruiter_ta_signal=False, has_opportunity_history_signal=False,
        latam_signal_present=False, usd_signal_present=False, existing_company_category="",
    )
    assert result["suggested_bucket"] == "NEEDS_COMPANY_MAPPING"
    assert result["confidence"] == 0.0


def test_classify_candidate_staffing_keyword():
    result = _classify_candidate(
        "Fake Talent Recruitment Agency", follow_bucket="", follow_category="", follow_confidence=0.0,
        has_recruiter_ta_signal=False, has_opportunity_history_signal=False,
        latam_signal_present=False, usd_signal_present=False, existing_company_category="",
    )
    assert result["suggested_bucket"] == "GLOBAL_STAFFING"
    assert result["global_staffing_signal"] is True


def test_classify_candidate_latam_keyword_confirmed():
    result = _classify_candidate(
        "Fake Nearshore LATAM Partners", follow_bucket="", follow_category="", follow_confidence=0.0,
        has_recruiter_ta_signal=False, has_opportunity_history_signal=False,
        latam_signal_present=False, usd_signal_present=False, existing_company_category="",
    )
    assert result["suggested_bucket"] == "LATAM_USD_CONFIRMED"


def test_classify_candidate_latam_signal_without_keyword_is_likely_not_confirmed():
    """Message/opportunity-history evidence alone (no keyword in the company's
    own name) must land in the LIKELY tier, not CONFIRMED."""
    result = _classify_candidate(
        "Fake Neutral Name Co", follow_bucket="", follow_category="", follow_confidence=0.0,
        has_recruiter_ta_signal=False, has_opportunity_history_signal=True,
        latam_signal_present=True, usd_signal_present=False, existing_company_category="",
    )
    assert result["suggested_bucket"] == "LATAM_USD_LIKELY"
    assert result["confidence"] < 0.85


def test_classify_candidate_recruiter_signal_without_region_is_global_opportunity():
    result = _classify_candidate(
        "Fake Neutral Name Co", follow_bucket="", follow_category="", follow_confidence=0.0,
        has_recruiter_ta_signal=True, has_opportunity_history_signal=False,
        latam_signal_present=False, usd_signal_present=False, existing_company_category="",
    )
    assert result["suggested_bucket"] == "GLOBAL_OPPORTUNITY"


def test_classify_candidate_followed_alone_without_dictionary_signal_does_not_classify():
    """Company Follow Intelligence only ever sets follow_bucket when IT found
    real evidence — but this guards the contract: an EMPTY follow_bucket
    (i.e. "followed with no evidence") must never, by itself, produce a
    suggested bucket here either."""
    result = _classify_candidate(
        "Fake Neutral Name Co", follow_bucket="", follow_category="", follow_confidence=0.0,
        has_recruiter_ta_signal=False, has_opportunity_history_signal=False,
        latam_signal_present=False, usd_signal_present=False, existing_company_category="",
    )
    assert result["suggested_bucket"] == "NEEDS_COMPANY_MAPPING"


# ── _yaml_suggestion() — keys must be raw company_clean, not normalized ─────

def test_yaml_suggestion_uses_raw_lowercase_keys_not_normalized():
    result = _yaml_suggestion(["Elevation", "Elevation Group"], "GLOBAL_OPPORTUNITY", "GLOBAL_OPPORTUNITY")
    assert '"elevation":' in result
    assert '"elevation group":' in result
    assert result.count("\n") == 1  # exactly two lines


def test_yaml_suggestion_empty_when_no_bucket():
    assert _yaml_suggestion(["Fake Co"], "NEEDS_COMPANY_MAPPING", "") == ""


def test_yaml_suggestion_maps_v5_bucket_to_override_market_scheme():
    result = _yaml_suggestion(["Fake Staffing Co"], "GLOBAL_STAFFING", "GLOBAL_STAFFING")
    assert "market: GLOBAL_STAFFING" in result
    result2 = _yaml_suggestion(["Fake Opportunity Co"], "GLOBAL_OPPORTUNITY", "GLOBAL_OPPORTUNITY")
    assert "market: GLOBAL_OPPORTUNITY_UNRESOLVED_REGION" in result2


# ── run_company_mapping_workbench() — end-to-end on synthetic data ──────────

def _synthetic_df():
    return pd.DataFrame({
        "url": ["u1", "u2", "u3", "u4"],
        "company_clean": ["Fake Staffing Recruiters", "Fake Staffing Recruiters",
                           "Fake Neutral Holdings", "Fake Neutral Holdings"],
        "persona": ["Recruiter", "Talent Acquisition", "Software Engineer", "Data Engineer"],
        "company_category": ["", "", "", ""],
        "priority_score": [60, 55, 40, 42],
        "opportunity_market_v5": ["NEEDS_COMPANY_MAPPING"] * 4,
        "opportunity_bucket": ["NEEDS_COMPANY_MAPPING"] * 4,
    })


def test_workbench_ranks_and_never_fabricates(tmp_path, monkeypatch):
    import src.company_mapping_workbench as w
    monkeypatch.setattr(w, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(w, "PRIVATE_DIR", tmp_path / "private")
    monkeypatch.setattr(w, "OVERRIDES_YML", tmp_path / "does_not_exist.yml")

    result = run_company_mapping_workbench(_synthetic_df())
    assert result["available"] is True
    companies = {c["company_name"]: c for c in result["companies"]}

    staffing = companies["Fake Staffing Recruiters"]
    assert staffing["suggested_bucket"] == "GLOBAL_STAFFING"
    assert staffing["impact_if_mapped"] == 2
    assert staffing["manual_review_required"] is True

    neutral = companies["Fake Neutral Holdings"]
    assert neutral["suggested_bucket"] == "NEEDS_COMPANY_MAPPING"
    assert neutral["confidence"] == 0.0

    # Ranked by priority_score descending
    assert result["companies"][0]["priority_score"] >= result["companies"][-1]["priority_score"]


def test_workbench_handles_empty_needs_mapping_backlog(tmp_path, monkeypatch):
    import src.company_mapping_workbench as w
    monkeypatch.setattr(w, "OUTPUTS_DIR", tmp_path)
    df = _synthetic_df()
    df["opportunity_market_v5"] = "GLOBAL_TECH"  # nothing left to map
    result = run_company_mapping_workbench(df)
    assert result["available"] is True
    assert result["summary"]["candidate_count"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
