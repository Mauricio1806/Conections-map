# -*- coding: utf-8 -*-
"""
tests/test_company_follow_intelligence.py
==========================================
Regression fixtures for Company Follow Intelligence —
src/company_follow_intelligence.py: build_company_follow_key(),
match_follow_to_companies(), classify_followed_company(),
apply_company_follow_resolution().

Real problem this guards against: an early version of the fuzzy matcher
linked unrelated companies that merely shared one generic business word
(e.g. "Klube Capital" matched "EP Capital" / "Faz Capital" via "capital";
"World Business Lenders" matched 14 unrelated companies via "business"/
"world"). These tests pin down that generic single-word overlaps must
never match, while genuine same-entity variants still do.

All names/companies below are synthetic fixture data — not real contacts.

Run: python -m pytest tests/test_company_follow_intelligence.py -q
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.company_follow_intelligence import (
    build_company_follow_key,
    match_follow_to_companies,
    classify_followed_company,
    apply_company_follow_resolution,
    _build_connection_company_index,
)


# ── build_company_follow_key ─────────────────────────────────────────────────

def test_build_company_follow_key_strips_punctuation_and_suffixes():
    assert build_company_follow_key("Acme Staffing, Inc.") == "acme staffing"


def test_build_company_follow_key_strips_company_word():
    # "Company" is not stripped by company_normalizer.normalize() itself —
    # this module adds it explicitly per the Company Follows spec.
    assert build_company_follow_key("Fake Tech Company") == "fake"


def test_build_company_follow_key_removes_accents_and_collapses_spaces():
    assert build_company_follow_key("Associação   Fictícia") == "associacao ficticia"


def test_build_company_follow_key_empty_input():
    assert build_company_follow_key("") == ""
    assert build_company_follow_key(None) == ""


# ── match_follow_to_companies — false-positive regression ───────────────────

def _index(company_names):
    df = pd.DataFrame({"company_clean": company_names})
    index, _ = _build_connection_company_index(df)
    return index


def test_generic_single_word_overlap_does_not_match():
    """'Klube Capital' must NOT match 'EP Capital' / 'Faz Capital' / bare
    'Capital' — the shared word is a generic business noun, not a real
    signal that these are the same company."""
    index = _index(["EP Capital", "Faz Capital", "Capital", "Unrelated Co"])
    matched, method, conf = match_follow_to_companies(build_company_follow_key("Klube Capital"), index)
    assert matched == []
    assert method == "no_match"
    assert conf == 0.0


def test_generic_business_word_business_and_world_do_not_match():
    index = _index(["NTT Data Business", "PM Business", "DP World"])
    matched, method, conf = match_follow_to_companies(
        build_company_follow_key("World Business Lenders LLC"), index
    )
    assert matched == []


def test_short_ambiguous_key_never_fuzzy_matches():
    """A 3-char normalized key (e.g. 'abc') must not fuzzy-match anything —
    only an exact match is trusted for names this short/ambiguous. Here
    "ABC Two" normalizes to a *different* key ("abc two"), so a fuzzy engine
    tempted by the shared "abc" substring must still refuse to match."""
    index = _index(["ABC Two", "Some Other Company"])
    matched, method, conf = match_follow_to_companies("abc", index)
    assert matched == []
    assert method == "no_match"


def test_short_ambiguous_key_does_match_when_exact():
    index = _index(["ABC"])
    matched, method, conf = match_follow_to_companies("abc", index)
    assert matched == ["abc"]
    assert method == "exact_normalized"


# ── match_follow_to_companies — legitimate matches still work ───────────────

def test_exact_normalized_match():
    index = _index(["Globant"])
    matched, method, conf = match_follow_to_companies(build_company_follow_key("Globant S.A."), index)
    assert matched == ["globant"]
    assert method == "exact_normalized"
    assert conf > 0.9


def test_substring_containment_for_long_related_names():
    index = _index(["George Bernard International Recruitment"])
    matched, method, conf = match_follow_to_companies(
        build_company_follow_key("George Bernard Consulting Jobs"), index
    )
    # Both sides share the distinctive "george bernard" prefix and pass the
    # length-ratio floor — this is exactly the kind of same-entity variant
    # substring containment should catch.
    assert method in ("substring_containment", "token_overlap_fuzzy")
    assert matched


def test_substring_containment_rejects_short_name_buried_in_long_title():
    """An 8-char company name incidentally appearing inside an unrelated
    30-char title is noise, not a match — length-ratio floor should reject it."""
    index = _index(["LinkedIn"])
    matched, method, conf = match_follow_to_companies(
        build_company_follow_key("LinkedIn Guide to Creating Leads Fast"), index
    )
    assert matched == []


# ── classify_followed_company — never fabricates geography ──────────────────

def test_classify_staffing_keyword():
    result = classify_followed_company("Fake Talent Staffing Solutions", "fake talent staffing")
    assert result["likely_company_category"] == "GLOBAL_STAFFING"
    assert result["likely_opportunity_bucket"] == "GLOBAL_STAFFING"
    assert 0.70 <= result["follow_signal_confidence"] <= 0.90


def test_classify_latam_keyword():
    result = classify_followed_company("Fake Nearshore LATAM Partners", "fake nearshore latam partners")
    assert result["likely_opportunity_bucket"] == "LATAM_USD_LIKELY"


def test_classify_us_keyword():
    result = classify_followed_company("Fake Company United States Division", "fake united states division")
    assert result["likely_opportunity_bucket"] == "US_CANADA_LIKELY"


def test_classify_no_signal_does_not_fabricate_bucket():
    result = classify_followed_company("Fake Neutral Name Holdings", "fake neutral name")
    assert result["likely_opportunity_bucket"] == ""
    assert result["likely_company_category"] == ""
    assert result["follow_signal_confidence"] == 0.0
    assert "needs manual review" in result["company_follow_reason"]


# ── apply_company_follow_resolution — honest Needs Mapping resolution ───────

def _enriched_df():
    return pd.DataFrame({
        "url": ["u1", "u2", "u3"],
        "company_clean": ["Fake Recruiter Co", "Fake Neutral Co", "Fake Recruiter Co"],
        "persona": ["Recruiter", "Software Engineer", "Other"],
        "company_category": ["", "", ""],
        "opportunity_market_v5": ["NEEDS_COMPANY_MAPPING", "NEEDS_COMPANY_MAPPING", "NEEDS_COMPANY_MAPPING"],
        "opportunity_bucket": ["NEEDS_COMPANY_MAPPING", "NEEDS_COMPANY_MAPPING", "NEEDS_COMPANY_MAPPING"],
        "opportunity_confidence": [0.4, 0.4, 0.4],
        "opportunity_reason": ["", "", ""],
        "needs_manual_company_mapping": [True, True, True],
    })


def _follows_df():
    return pd.DataFrame({
        "company_name": ["Fake Recruiter Co", "Fake Neutral Co"],
        "company_follow_key": ["fake recruiter co", "fake neutral co"],
        "followed_on_display": ["2026-08-01", "2026-08-01"],
        "days_since_followed": [5, 5],
    })


def test_resolution_moves_contact_with_persona_signal_out_of_needs_mapping():
    df, summary, matches = apply_company_follow_resolution(_enriched_df(), _follows_df())
    row1 = df[df["url"] == "u1"].iloc[0]
    assert row1["opportunity_market_v5"] != "NEEDS_COMPANY_MAPPING"
    assert row1["company_resolution_source"] == "company_follow_signal"
    assert summary["resolved_by_company_follow_signal"] >= 1


def test_resolution_leaves_contact_without_signal_in_needs_mapping():
    """u2's company follow matched, but the contact has no persona/keyword/
    category signal at all — must stay NEEDS_COMPANY_MAPPING, never resolved
    on a bare name match alone."""
    df, summary, matches = apply_company_follow_resolution(_enriched_df(), _follows_df())
    row2 = df[df["url"] == "u2"].iloc[0]
    assert row2["opportunity_market_v5"] == "NEEDS_COMPANY_MAPPING"


def test_resolution_handles_missing_company_follows_gracefully():
    df, summary, matches = apply_company_follow_resolution(_enriched_df(), None)
    assert summary["available"] is False
    assert (df["opportunity_market_v5"] == "NEEDS_COMPANY_MAPPING").all()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
