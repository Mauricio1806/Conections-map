# -*- coding: utf-8 -*-
"""
tests/test_untapped_network_intelligence.py
==============================================
Regression fixtures for the Untapped Network Intelligence engine
(CLAUDE_UNTAPPED_NETWORK_V9.md Part 25).

All names/companies below are synthetic fixture data — not real contacts.

Run: python -m pytest tests/test_untapped_network_intelligence.py -q
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.untapped_network_intelligence import (
    _build_message_identity_index,
    match_identity,
    compute_untapped_outreach_score,
    _strategic_focus,
    normalize_name,
    normalize_url,
    build_weekly_untapped_queue,
    _untapped_category,
    _recommended_first_action,
)
from src.untapped_outreach_score import score_untapped_contact


def _mk_msg_df(rows):
    """rows: list of (other_person_name, other_person_profile_url, company_clean)."""
    return pd.DataFrame([
        {"other_person_name": n, "other_person_profile_url": u, "company_clean": c, "persona": ""}
        for n, u, c in rows
    ])


# ── Case 1 — exact same LinkedIn URL => HAS_CONVERSATION ─────────────────────
def test_exact_profile_url_match():
    idx = _build_message_identity_index(_mk_msg_df([
        ("Fake Person One", "https://www.linkedin.com/in/fake-person-one", "Acme Corp"),
    ]))
    status, conf, method = match_identity(
        "Fake Person One", "Acme Corp", "https://www.linkedin.com/in/fake-person-one",
        idx, {},
    )
    assert status == "HAS_CONVERSATION"
    assert method == "EXACT_PROFILE_URL"
    assert conf == 1.00


# ── Case 2 — URL differs only by trailing slash/query params => HAS_CONVERSATION
def test_normalized_url_match():
    idx = _build_message_identity_index(_mk_msg_df([
        ("Fake Person Two", "https://www.linkedin.com/in/fake-person-two/", "Beta Inc"),
    ]))
    status, conf, method = match_identity(
        "Fake Person Two", "Beta Inc", "https://linkedin.com/in/fake-person-two?trk=abc",
        idx, {},
    )
    assert status == "HAS_CONVERSATION"
    assert method == "NORMALIZED_PROFILE_URL"
    assert conf == 0.95


# ── Case 3 — same normalized name + company => HAS_CONVERSATION ──────────────
def test_exact_name_company_match():
    idx = _build_message_identity_index(_mk_msg_df([
        ("Fake Person Three", "", "Gamma Staffing"),
    ]))
    status, conf, method = match_identity(
        "  Fake   Person Three ", "Gamma Staffing", "",
        idx, {"fake person three": 1},
    )
    assert status == "HAS_CONVERSATION"
    assert method == "EXACT_NAME_COMPANY"


# ── Case 4 — same name, different companies, multiple candidates => AMBIGUOUS_REVIEW
def test_same_name_conflicting_companies_is_ambiguous():
    idx = _build_message_identity_index(_mk_msg_df([
        ("Fake Duplicate Name", "", "Company Red"),
        ("Fake Duplicate Name", "", "Company Blue"),
    ]))
    status, conf, method = match_identity(
        "Fake Duplicate Name", "Company Green", "",
        idx, {"fake duplicate name": 1},
    )
    assert status == "AMBIGUOUS_REVIEW"
    assert method == "AMBIGUOUS"


# ── Case 5 — connection exists, no message match => NEVER_CONTACTED_CONFIRMED
def test_no_match_with_complete_identity_is_confirmed():
    idx = _build_message_identity_index(_mk_msg_df([]))
    status, conf, method = match_identity(
        "Fake Person Five", "Delta Tech", "https://www.linkedin.com/in/fake-person-five",
        idx, {"fake person five": 1},
    )
    assert status == "NEVER_CONTACTED_CONFIRMED"
    assert method == "NO_MATCH"
    assert conf == 0.0


# ── Case 6 — missing URL, incomplete identity, no match => LIKELY_NEVER_CONTACTED
def test_incomplete_identity_no_match_is_likely():
    idx = _build_message_identity_index(_mk_msg_df([]))
    status, conf, method = match_identity(
        "Fake Person Six", "", "",
        idx, {"fake person six": 1},
    )
    assert status == "LIKELY_NEVER_CONTACTED"


# ── Case 7 — accented vs unaccented name => correct normalized matching ──────
def test_accented_name_normalizes_for_matching():
    idx = _build_message_identity_index(_mk_msg_df([
        ("José António Müller", "", "Empresa Exemplo"),
    ]))
    status, conf, method = match_identity(
        "Jose Antonio Muller", "Empresa Exemplo", "",
        idx, {normalize_name("José António Müller"): 1},
    )
    assert status == "HAS_CONVERSATION"
    assert method == "EXACT_NAME_COMPANY"
    assert normalize_name("José António Müller") == normalize_name("Jose Antonio Muller")


# ── Case 8 — common duplicate name => do not force match ─────────────────────
def test_common_duplicate_name_not_force_matched():
    idx = _build_message_identity_index(_mk_msg_df([
        ("Fake Common Name", "", "Some Company"),
    ]))
    # This name is common among CONNECTIONS (count=2) — even though the
    # message side has only one candidate, we cannot be sure which of the two
    # connections actually had the conversation.
    status, conf, method = match_identity(
        "Fake Common Name", "Different Company", "",
        idx, {"fake common name": 2},
    )
    assert status == "AMBIGUOUS_REVIEW"


# ── Case 9 — never-contacted recruiter LATAM => high untapped score ──────────
def test_never_contacted_latam_recruiter_scores_high():
    score = compute_untapped_outreach_score(
        persona="Recruiter", opportunity_bucket="LATAM_USD_CONFIRMED",
        opportunity_confidence=0.9, seniority="Senior", position_clean="Technical Recruiter",
        days_connected=45, match_confidence=1.0,
    )
    assert score >= 60


# ── Case 10 — never-contacted irrelevant low-value persona => lower score ────
def test_never_contacted_low_value_persona_scores_lower():
    score = compute_untapped_outreach_score(
        persona="Other", opportunity_bucket="LOW_VALUE_UNRESOLVED",
        opportunity_confidence=0.1, seniority="Junior", position_clean="Intern",
        days_connected=400, match_confidence=1.0,
    )
    high_score = compute_untapped_outreach_score(
        persona="Recruiter", opportunity_bucket="LATAM_USD_CONFIRMED",
        opportunity_confidence=0.9, seniority="Director", position_clean="Head of Talent",
        days_connected=45, match_confidence=1.0,
    )
    assert score < high_score
    assert score < 30


# ── Case 11 — Spain/EU contact => exploratory allocation, not primary 90% ────
def test_spain_eu_is_exploratory_focus():
    assert _strategic_focus("SPAIN_EU_CONFIRMED") == "SPAIN_EU_EXPLORATORY"
    assert _strategic_focus("EUROPE_LIKELY") == "SPAIN_EU_EXPLORATORY"


# ── Case 12 — LATAM recruiter => primary allocation ──────────────────────────
def test_latam_is_primary_focus():
    assert _strategic_focus("LATAM_USD_CONFIRMED") == "PRIMARY_LATAM_USD"
    assert _strategic_focus("US_CANADA_LIKELY") == "PRIMARY_LATAM_USD"


# ── Case 13/14 — existing conversation (Lead Reactivation territory) excluded ─
def test_contact_with_conversation_excluded_from_untapped_population():
    idx = _build_message_identity_index(_mk_msg_df([
        ("Fake Active Contact", "https://www.linkedin.com/in/fake-active-contact", "Recruiting Co"),
    ]))
    status, conf, method = match_identity(
        "Fake Active Contact", "Recruiting Co", "https://www.linkedin.com/in/fake-active-contact",
        idx, {},
    )
    assert status == "HAS_CONVERSATION"
    # The untapped population is explicitly NEVER_CONTACTED_CONFIRMED / LIKELY_NEVER_CONTACTED only.
    untapped_statuses = {"NEVER_CONTACTED_CONFIRMED", "LIKELY_NEVER_CONTACTED"}
    assert status not in untapped_statuses


# ── Weekly queue allocation sanity (Part 17) ──────────────────────────────────
def test_weekly_queue_allocation_favors_primary():
    rows = []
    for i in range(30):
        rows.append({
            "full_name": f"Fake Primary {i}", "company_clean": "X", "url": f"url{i}",
            "contact_history_status": "NEVER_CONTACTED_CONFIRMED",
            "untapped_outreach_score": 80 - i, "strategic_focus": "PRIMARY_LATAM_USD",
        })
    for i in range(10):
        rows.append({
            "full_name": f"Fake EU {i}", "company_clean": "Y", "url": f"eu{i}",
            "contact_history_status": "NEVER_CONTACTED_CONFIRMED",
            "untapped_outreach_score": 70 - i, "strategic_focus": "SPAIN_EU_EXPLORATORY",
        })
    df = pd.DataFrame(rows)
    queue = build_weekly_untapped_queue(df)
    assert len(queue) <= 20
    primary_count = (queue["strategic_focus"] == "PRIMARY_LATAM_USD").sum()
    eu_count = (queue["strategic_focus"] == "SPAIN_EU_EXPLORATORY").sum()
    assert primary_count >= 14
    assert eu_count <= 3


# ── Untapped Outreach Scoring V9 — LATAM/US recruiter regression ─────────────
# Case: a never-contacted recruiter at a LATAM/international staffing company
# (fixture modeled on Ubiminds: You, International) whose title stacks
# multiple strong recruiting signals (LATAM + U.S. corporate + bilingual +
# tech/IT recruiter). This used to under-rank to a mid score (~66) because
# the company wasn't recognized as a staffing company and the scoring model
# didn't credit stacked title keywords — V9 fixes both. All names/companies
# below are synthetic fixture data, not real contacts.
def test_never_contacted_latam_us_recruiter_at_staffing_company_scores_high_value():
    result = score_untapped_contact(
        full_name="Fake LATAM Recruiter",
        company_clean="Ubiminds: You, International",
        position_clean=(
            "Tech Recruiter LATAM | Bilingual Recruiter | Corporate U.S. Recruiter | "
            "IT Recruiter | Fluent English"
        ),
        persona="Recruiter",
        opportunity_bucket="",
        opportunity_confidence=0.0,
        seniority="Senior",
        days_connected=45,
    )
    assert result["untapped_outreach_score"] >= 85
    assert result["opportunity_bucket"] in ("LATAM_USD_CONFIRMED", "GLOBAL_STAFFING")
    assert "latam" in result["untapped_reason"].lower()
    assert "u.s." in result["untapped_reason"].lower() or "us" in result["untapped_reason"].lower()

    recommended_action = _recommended_first_action("Recruiter", _strategic_focus(result["opportunity_bucket"]),
                                                     result["untapped_outreach_score"])
    assert recommended_action == "FIRST_MESSAGE_RECRUITER"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
