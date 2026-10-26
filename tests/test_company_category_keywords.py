# -*- coding: utf-8 -*-
"""
tests/test_company_category_keywords.py
=========================================
Recruitment-agency company names still in Needs Company Mapping after every
evidence pass resolve to GLOBAL_STAFFING (category only) — recruitment-
marketing / recruiting-software vendors and already-resolved rows (e.g. a
message-history LATAM signal) are left untouched.

Run: python -m pytest tests/test_company_category_keywords.py -q
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.company_resolution_v7 import (
    NEEDS_MAPPING,
    _apply_recruitment_name_fallback,
    _is_recruitment_agency_name,
)


def test_recruitment_agency_name_detection():
    for name in ("Progressive Recruitment", "Riverstate Premium Recruiting",
                 "Gravitas Recruitment Group (Global) Ltd"):
        assert _is_recruitment_agency_name(name), name
    for name in ("Left Lane - Recruitment Marketing", "softgarden e-recruiting GmbH",
                 "Acme Recruiting Software", "Natura"):
        assert not _is_recruitment_agency_name(name), name


def test_fallback_only_touches_residual_needs_mapping():
    df = pd.DataFrame({
        "company_clean": ["Progressive Recruitment", "William Alexander Recruitment", "Natura"],
        "opportunity_market_v5": [NEEDS_MAPPING, "LATAM_USD_LIKELY", NEEDS_MAPPING],
    })
    df["opportunity_bucket"] = df["opportunity_market_v5"]
    n = _apply_recruitment_name_fallback(df, df["opportunity_market_v5"] == NEEDS_MAPPING)
    assert n == 1
    assert df.loc[0, "opportunity_market_v5"] == "GLOBAL_STAFFING"
    assert df.loc[1, "opportunity_market_v5"] == "LATAM_USD_LIKELY"   # message evidence kept
    assert df.loc[2, "opportunity_market_v5"] == NEEDS_MAPPING


def test_self_employment_placeholders_are_never_pooled_as_one_company():
    """Unrelated freelancers all listing "Autónomo" / "Self-employed" are not
    colleagues — they must not share company-level message or cohort evidence."""
    from src.company_normalizer import is_placeholder_company, pooling_key

    for name in ("Autónomo", "autonomo", "Autônoma", "Self-employed", "Self Employed",
                 "Freelance", "Confidential", "Stealth Startup", "Independiente"):
        assert is_placeholder_company(name), name
        assert pooling_key(name) == "", name
    for name in ("Stealth IT Consulting", "Freelance Platform Inc", "Progressive Recruitment"):
        assert not is_placeholder_company(name), name
        assert pooling_key(name) != "", name


def test_placeholder_company_gets_no_cohort_evidence():
    from src.company_resolution_v7 import _apply_persona_role_cohort_fallback

    df = pd.DataFrame({
        "company_clean": ["Autónomo"] * 4,
        "persona": ["Recruiter", "Recruiter", "Recruiter", "Data Engineer"],
        "opportunity_market_v5": ["GLOBAL_STAFFING"] * 3 + [NEEDS_MAPPING],
    })
    df["opportunity_bucket"] = df["opportunity_market_v5"]
    df["company_category_v4"] = "OTHER"
    n = _apply_persona_role_cohort_fallback(df, df["opportunity_market_v5"] == NEEDS_MAPPING, 1)
    assert n == 0
    assert df.loc[3, "opportunity_market_v5"] == NEEDS_MAPPING
