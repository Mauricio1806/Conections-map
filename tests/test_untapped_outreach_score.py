# -*- coding: utf-8 -*-
"""
tests/test_untapped_outreach_score.py
=========================================
Regression fixtures for Untapped Activation Potential Scoring (V10) —
src/untapped_outreach_score.py: score_activation_potential(),
compute_untapped_execution_score(), activation_age_bucket_label().

Real observed problem this fixes: long-connected (~1 year), never-contacted
1st-degree recruiters/TA/talent-partners were under-ranked on the Untapped
Network page even though manually messaging them produced immediate hot
opportunities / recruiter conversations. All names/companies below are
synthetic fixture data modeled on that real pattern — not real contacts.

Run: python -m pytest tests/test_untapped_outreach_score.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.untapped_outreach_score import (
    score_activation_potential,
    compute_untapped_execution_score,
    activation_age_bucket_label,
    score_untapped_contact,
)


# ── Case 1 — Camila-style profile: Global Recruiter & Sr Talent Partner,
# connecting LATAM talent with international teams, connected 300+ days,
# never contacted => should rank at the very top of the page. ────────────────
def test_camila_style_latam_global_recruiter_scores_top_tier():
    result = score_activation_potential(
        full_name="Fake Camila Profile",
        company_clean="Fake Recruiter / Talent Partner Company",
        position_clean="Global Recruiter & Sr Talent Partner | Connecting LATAM Talent with International Teams",
        persona="Recruiter",
        opportunity_bucket="",
        history_status="NEVER_CONTACTED_CONFIRMED",
        days_connected=300,
    )
    assert result["untapped_activation_potential_score"] >= 90
    assert result["activation_category"] in ("HOT_UNTAPPED_RECRUITER", "LATAM_INTERNATIONAL_RECRUITER")
    assert result["first_message_priority"] == "TODAY"
    reason_l = result["activation_reason"].lower()
    assert "latam" in reason_l
    assert "international" in reason_l
    assert "recruiter" in reason_l or "talent partner" in reason_l
    assert result["first_message_angle"] is not None


# ── Case 2 — Claudia-style profile: IT Talent Attraction Specialist,
# recruiter-style TA persona, never contacted => should rank high even
# though the title doesn't literally say "Recruiter" or "Talent Acquisition". ─
def test_claudia_style_it_talent_attraction_scores_high():
    result = score_activation_potential(
        full_name="Fake Claudia Profile",
        company_clean="Fake Tech Company",
        position_clean="IT Talent Attraction Specialist",
        persona="Talent Acquisition",
        opportunity_bucket="",
        history_status="NEVER_CONTACTED_CONFIRMED",
        days_connected=120,
    )
    assert result["untapped_activation_potential_score"] >= 75
    assert result["first_message_priority"] in ("TODAY", "THIS_WEEK")


# ── Case 3 — Elissavet-style profile: Talent Acquisition | People Analytics,
# never contacted => should rank high on persona + strong-title-phrase signal. ─
def test_elissavet_style_ta_people_analytics_scores_high():
    result = score_activation_potential(
        full_name="Fake Elissavet Profile",
        company_clean="Fake Analytics Company",
        position_clean="Talent Acquisition | People Analytics",
        persona="Talent Acquisition",
        opportunity_bucket="",
        history_status="NEVER_CONTACTED_CONFIRMED",
        days_connected=95,
    )
    assert result["untapped_activation_potential_score"] >= 70
    assert result["first_message_priority"] in ("TODAY", "THIS_WEEK")


# ── Case 4 — Unrelated old connection: no recruiting/data/market/LATAM/US/
# international/talent signal at all, long-connected, never contacted =>
# must NOT outrank the recruiter/TA examples above, even though it is also
# long-connected and never-contacted (never-contacted/long-connected alone
# are not penalized, but they are also not enough to rank above real signal). ─
def test_unrelated_old_connection_does_not_outrank_recruiters():
    camila = score_activation_potential(
        full_name="Fake Camila Profile",
        company_clean="Fake Recruiter / Talent Partner Company",
        position_clean="Global Recruiter & Sr Talent Partner | Connecting LATAM Talent with International Teams",
        persona="Recruiter",
        opportunity_bucket="",
        history_status="NEVER_CONTACTED_CONFIRMED",
        days_connected=300,
    )
    claudia = score_activation_potential(
        full_name="Fake Claudia Profile",
        company_clean="Fake Tech Company",
        position_clean="IT Talent Attraction Specialist",
        persona="Talent Acquisition",
        opportunity_bucket="",
        history_status="NEVER_CONTACTED_CONFIRMED",
        days_connected=120,
    )
    elissavet = score_activation_potential(
        full_name="Fake Elissavet Profile",
        company_clean="Fake Analytics Company",
        position_clean="Talent Acquisition | People Analytics",
        persona="Talent Acquisition",
        opportunity_bucket="",
        history_status="NEVER_CONTACTED_CONFIRMED",
        days_connected=95,
    )
    unrelated = score_activation_potential(
        full_name="Fake Unrelated Profile",
        company_clean="Fake Warehouse Logistics Company",
        position_clean="Warehouse Operations Coordinator",
        persona="Other",
        opportunity_bucket="LOW_VALUE_UNRESOLVED",
        history_status="NEVER_CONTACTED_CONFIRMED",
        days_connected=400,
    )
    assert unrelated["untapped_activation_potential_score"] < camila["untapped_activation_potential_score"]
    assert unrelated["untapped_activation_potential_score"] < claudia["untapped_activation_potential_score"]
    assert unrelated["untapped_activation_potential_score"] < elissavet["untapped_activation_potential_score"]


# ── Never-contacted is never a penalty (module design principle) ─────────────
def test_never_contacted_is_never_penalized_directly():
    # A contact with zero title/persona signal but NEVER_CONTACTED_CONFIRMED
    # still gets credit for being an untapped 1st-degree connection — the
    # negative signals fire on irrelevance, not on the never-contacted status
    # itself. This is a floor check, not a ranking check.
    result = score_activation_potential(
        full_name="Fake Neutral Profile",
        company_clean="Fake Neutral Company",
        position_clean="Generalist",
        persona="Other",
        opportunity_bucket="",
        history_status="NEVER_CONTACTED_CONFIRMED",
        days_connected=45,
    )
    assert result["untapped_activation_potential_score"] >= 0  # never goes negative


# ── Ubiminds-style regression (existing V9 scorer) must still pass unchanged ──
def test_v9_ubiminds_regression_untouched():
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


# ── activation_age_bucket_label boundaries ────────────────────────────────────
def test_activation_age_bucket_label_boundaries():
    assert activation_age_bucket_label(0) == "0-30 days"
    assert activation_age_bucket_label(30) == "0-30 days"
    assert activation_age_bucket_label(31) == "31-90 days"
    assert activation_age_bucket_label(90) == "31-90 days"
    assert activation_age_bucket_label(91) == "91-180 days"
    assert activation_age_bucket_label(180) == "91-180 days"
    assert activation_age_bucket_label(181) == "181-365 days"
    assert activation_age_bucket_label(365) == "181-365 days"
    assert activation_age_bucket_label(366) == "365+ days"
    assert activation_age_bucket_label(None) == "Unknown"
    assert activation_age_bucket_label("") == "Unknown"


# ── compute_untapped_execution_score — blended max(), never below any input ──
def test_execution_score_is_max_of_the_three_signals():
    exec_score = compute_untapped_execution_score(
        untapped_outreach_score=40, untapped_activation_potential_score=95,
        priority_score=30, persona="Recruiter", history_status="NEVER_CONTACTED_CONFIRMED",
    )
    assert exec_score >= 95


def test_execution_score_boosts_priority_for_never_contacted_recruiter():
    with_boost = compute_untapped_execution_score(
        untapped_outreach_score=0, untapped_activation_potential_score=0,
        priority_score=50, persona="Recruiter", history_status="NEVER_CONTACTED_CONFIRMED",
    )
    without_boost = compute_untapped_execution_score(
        untapped_outreach_score=0, untapped_activation_potential_score=0,
        priority_score=50, persona="Other", history_status="NEVER_CONTACTED_CONFIRMED",
    )
    assert with_boost > without_boost
    assert with_boost == 65  # 50 + 15 recruiter/never-contacted boost, capped at 100


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
