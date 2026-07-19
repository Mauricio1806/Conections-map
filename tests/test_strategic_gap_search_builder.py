# -*- coding: utf-8 -*-
"""
Tests for src/strategic_gap_search_builder.py.

Core regression this guards against: the Strategic Gap page used to build a
LinkedIn People Search query by quoting the internal V2 market label
directly (e.g. '"Hiring Manager" AND "LATAM USD"'), which returns nothing
because LATAM_USD/SPAIN_EU/US_CANADA_NEARSHORE are dashboard-internal bucket
labels, not real LinkedIn keywords.
"""

import pytest

from src.strategic_gap_search_builder import (
    BAD_TERMS,
    RECRUITER_PERSONAS,
    HIRING_MANAGER_PERSONAS,
    SENIOR_LEADER_PERSONAS,
    build_strategic_gap_search_pack,
)

ALL_MARKETS = ["LATAM_USD", "US_CANADA_NEARSHORE", "SPAIN_EU", "EUROPE"]
ALL_PERSONAS = list(RECRUITER_PERSONAS | HIRING_MANAGER_PERSONAS | SENIOR_LEADER_PERSONAS)


def _all_query_and_url_text(pack: dict) -> str:
    """Only the fields that actually reach a LinkedIn query or URL.
    search_rationale is deliberately excluded — it's allowed (and expected)
    to *name* the excluded internal bucket label as documentation, e.g.
    "instead of the internal bucket label 'LATAM_USD'" — that's a display
    label, not a query sent to LinkedIn."""
    return " ".join([
        pack["primary_query"], pack["secondary_query"], pack["company_query"],
        pack["people_search_url"], *pack["fallback_queries"],
    ])


@pytest.mark.parametrize("market", ALL_MARKETS)
@pytest.mark.parametrize("persona", ALL_PERSONAS)
def test_no_bad_bucket_term_in_any_generated_field(market, persona):
    pack = build_strategic_gap_search_pack(market, persona, "Critical", 50)
    text = _all_query_and_url_text(pack)
    for bad in BAD_TERMS:
        assert bad not in text, f"internal bucket label '{bad}' leaked into search pack for ({market}, {persona}): {text}"


@pytest.mark.parametrize("market", ALL_MARKETS)
@pytest.mark.parametrize("persona", ALL_PERSONAS)
def test_no_bad_bucket_term_in_search_url(market, persona):
    pack = build_strategic_gap_search_pack(market, persona)
    for bad in ["LATAM_USD", "SPAIN_EU", "US_CANADA_NEARSHORE"]:
        assert bad not in pack["people_search_url"]


@pytest.mark.parametrize("market", ALL_MARKETS)
@pytest.mark.parametrize("persona", ALL_PERSONAS)
def test_query_has_no_nested_boolean(market, persona):
    """Spec explicitly discourages complex nested Boolean strings — queries
    should be short real-world phrases, not '(" OR ") AND (" OR ")'."""
    pack = build_strategic_gap_search_pack(market, persona)
    for q in [pack["primary_query"], pack["secondary_query"]] + pack["fallback_queries"]:
        assert " OR " not in q
        assert q.count(" AND ") == 0


def test_latam_usd_hiring_manager_matches_spec_example():
    pack = build_strategic_gap_search_pack("LATAM_USD", "Hiring Manager", "Critical", 50)
    assert pack["primary_query"] in ("data engineering manager LATAM", "head of data Latin America")
    assert "LATAM_USD" not in pack["primary_query"]


def test_spain_eu_data_engineering_manager_matches_spec_example():
    pack = build_strategic_gap_search_pack("SPAIN_EU", "Data Engineering Manager", "Critical", 40)
    assert pack["primary_query"] in ("data engineering manager Spain", "data platform manager Europe")
    assert "SPAIN_EU" not in pack["primary_query"]


def test_us_canada_nearshore_recruiter_matches_spec_example():
    pack = build_strategic_gap_search_pack("US_CANADA_NEARSHORE", "Recruiter", "High", 30)
    assert pack["primary_query"] in ("nearshore recruiter data engineer", "LATAM recruiter US data engineer")
    assert "US_CANADA_NEARSHORE" not in pack["primary_query"]


def test_search_quality_is_one_of_three_tiers():
    for market in ALL_MARKETS:
        for persona in ALL_PERSONAS:
            pack = build_strategic_gap_search_pack(market, persona)
            assert pack["search_quality"] in ("High precision", "Medium precision", "Broad discovery")


def test_bad_terms_excluded_echoes_the_input_bucket_when_applicable():
    pack = build_strategic_gap_search_pack("LATAM_USD", "Recruiter")
    assert pack["bad_terms_excluded"] == ["LATAM_USD"]
    # EUROPE is a genuine real-world term, not an internal bucket label —
    # nothing should be reported as "excluded" for it.
    pack_eu = build_strategic_gap_search_pack("EUROPE", "Recruiter")
    assert pack_eu["bad_terms_excluded"] == []


def test_unknown_market_falls_back_safely_without_bad_terms():
    pack = build_strategic_gap_search_pack("SOME_UNKNOWN_BUCKET", "Recruiter")
    for bad in BAD_TERMS:
        assert bad not in _all_query_and_url_text(pack)


def test_people_search_url_is_well_formed():
    pack = build_strategic_gap_search_pack("SPAIN_EU", "Head of Data")
    assert pack["people_search_url"].startswith("https://www.linkedin.com/search/results/people/?keywords=")
    assert " " not in pack["people_search_url"]
