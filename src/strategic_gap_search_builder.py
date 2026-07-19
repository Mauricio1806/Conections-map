# -*- coding: utf-8 -*-
"""
strategic_gap_search_builder.py
================================
Builds realistic, LinkedIn-search-safe query packs for Strategic Gap cards.

Problem this fixes: the Strategic Gap page's action cards used to build a
LinkedIn People Search query by literally quoting the internal V2 market
label, e.g. '"Hiring Manager" AND "LATAM USD"' or '"Recruiter" AND "SPAIN EU"'.
LATAM_USD / SPAIN_EU / US_CANADA_NEARSHORE / GLOBAL_* / NEEDS_COMPANY_MAPPING
are dashboard-internal opportunity-bucket labels — they are not real-world
titles or place names LinkedIn indexes, so those searches returned nothing.

This module translates (market, persona) into short, low-Boolean, real-world
query terms LinkedIn's People Search actually understands, plus filter
guidance to use instead of stuffing everything into the keyword string.

Internal bucket labels must NEVER appear inside a generated query or URL —
BAD_TERMS lists every label this module guards against; validated by
tests/test_strategic_gap_search_builder.py.
"""

from __future__ import annotations

from urllib.parse import quote

# ── Internal bucket / inference labels that must never reach a LinkedIn
# query or URL — display-label use elsewhere in the dashboard is fine, but
# none of these are real-world search terms. Note "EUROPE" itself is NOT
# in this list — unlike the underscore-joined bucket tokens, "Europe" is a
# genuine real-world region name and is used correctly in queries below. ───
BAD_TERMS = [
    "LATAM_USD", "SPAIN_EU", "US_CANADA_NEARSHORE",
    "GLOBAL_OPPORTUNITY", "GLOBAL_STAFFING", "GLOBAL_CONSULTING",
    "NEEDS_MAPPING", "NEEDS_COMPANY_MAPPING", "LOW_VALUE_UNRESOLVED",
    "BRAZIL_CONFIRMED", "BRAZIL_LIKELY",
    "LATAM_USD_CONFIRMED", "LATAM_USD_LIKELY",
    "US_CANADA_CONFIRMED", "US_CANADA_LIKELY",
    "SPAIN_EU_CONFIRMED", "SPAIN_EU_LIKELY",
    "EUROPE_CONFIRMED", "EUROPE_LIKELY",
]

RECRUITER_PERSONAS = {"Recruiter", "Talent Acquisition", "Sourcer"}
HIRING_MANAGER_PERSONAS = {"Hiring Manager", "Engineering Manager", "Data Engineering Manager"}
SENIOR_LEADER_PERSONAS = {"Head of Data", "Director", "Executive"}

# Persona-specific title synonyms (Part 5 of the spec) — used for the
# "recommended_filters" title-keyword guidance, not glued into the query.
PERSONA_TITLE_SYNONYMS = {
    "Recruiter": ["recruiter", "technical recruiter", "tech recruiter", "IT recruiter", "senior recruiter", "recruiting partner"],
    "Talent Acquisition": ["talent acquisition", "talent partner", "talent acquisition specialist", "talent acquisition manager", "sourcing specialist", "sourcer"],
    "Sourcer": ["sourcer", "sourcing specialist", "talent sourcer"],
    "Hiring Manager": ["hiring manager", "engineering manager", "data engineering manager", "analytics engineering manager", "software engineering manager data", "data platform manager"],
    "Engineering Manager": ["engineering manager", "data engineering manager", "software engineering manager data", "data platform manager"],
    "Data Engineering Manager": ["data engineering manager", "analytics engineering manager", "data platform manager", "engineering manager data", "data infrastructure manager"],
    "Head of Data": ["head of data", "head of analytics", "head of data engineering", "data platform lead", "data lead"],
    "Director": ["director data", "director data engineering", "director analytics", "director engineering data", "data platform director"],
    "Executive": ["VP data", "VP engineering", "chief data officer", "head of data"],
}

# Readable labels for internal V2 market strings — for display text only,
# never fed into a query.
MARKET_DISPLAY_LABEL = {
    "LATAM_USD": "LATAM/USD",
    "US_CANADA_NEARSHORE": "US/Canada Nearshore",
    "SPAIN_EU": "Spain/EU",
    "EUROPE": "Europe",
}


def _persona_group(persona: str) -> str:
    if persona in RECRUITER_PERSONAS:
        return "recruiter_ta"
    if persona in HIRING_MANAGER_PERSONAS:
        return "hiring_manager"
    if persona in SENIOR_LEADER_PERSONAS:
        return "senior_leader"
    return "recruiter_ta"  # safest, broadest default


# ── Query tables ──────────────────────────────────────────────────────────
# Every query below is a short, real-world phrase (title + region), never a
# nested Boolean string and never an internal bucket label. Matches the
# exact examples given in the spec for each (market, persona group).
_QUERY_TABLE = {
    "LATAM_USD": {
        "recruiter_ta": {
            "primary": "data engineer recruiter LATAM",
            "secondary": "talent acquisition LATAM data",
            "fallback": ["tech recruiter LATAM", "IT recruiter LATAM", "recruiter Brazil remote data engineer", "recruiter Latin America data engineer"],
            "quality": "Broad discovery",
        },
        "hiring_manager": {
            "primary": "data engineering manager LATAM",
            "secondary": "head of data LATAM",
            "fallback": ["data platform manager LATAM", "analytics engineering manager Brazil", "data engineering manager remote"],
            "quality": "Medium precision",
        },
        "senior_leader": {
            "primary": "head of data LATAM",
            "secondary": "director data engineering LATAM",
            "fallback": ["data platform director Latin America", "analytics director LATAM"],
            "quality": "Medium precision",
        },
    },
    "US_CANADA_NEARSHORE": {
        "recruiter_ta": {
            "primary": "nearshore recruiter data engineer",
            "secondary": "LATAM recruiter US data engineer",
            "fallback": ["remote recruiter Latin America data", "technical recruiter nearshore", "talent acquisition nearshore LATAM"],
            "quality": "Broad discovery",
        },
        "hiring_manager": {
            "primary": "data engineering manager remote",
            "secondary": "head of data remote",
            "fallback": ["data platform manager nearshore", "engineering manager data platform remote", "director data engineering remote"],
            "quality": "Broad discovery",
        },
        "senior_leader": {
            "primary": "head of data remote",
            "secondary": "director data engineering remote",
            "fallback": ["data platform manager nearshore", "engineering manager data platform remote"],
            "quality": "Broad discovery",
        },
    },
    "SPAIN_EU": {
        "recruiter_ta": {
            "primary": "data engineer recruiter Spain",
            "secondary": "technical recruiter Spain",
            "fallback": ["talent acquisition Spain data", "IT recruiter Portugal", "recruiter Europe data engineer"],
            "quality": "Medium precision",
        },
        "hiring_manager": {
            "primary": "data engineering manager Spain",
            "secondary": "head of data Spain",
            "fallback": ["data platform manager Spain", "director data engineering Europe", "analytics engineering manager Portugal"],
            "quality": "High precision",
        },
        "senior_leader": {
            "primary": "head of data Spain",
            "secondary": "director data engineering Europe",
            "fallback": ["data platform director Spain", "analytics director Europe"],
            "quality": "Medium precision",
        },
    },
    "EUROPE": {
        "recruiter_ta": {
            "primary": "data engineer recruiter Europe",
            "secondary": "technical recruiter Europe",
            "fallback": ["talent acquisition Europe data", "recruiter Germany data engineer", "recruiter Netherlands data engineer"],
            "quality": "Broad discovery",
        },
        "hiring_manager": {
            "primary": "data engineering manager Europe",
            "secondary": "head of data Europe",
            "fallback": ["data platform manager Germany", "analytics engineering manager Netherlands", "director data engineering Europe"],
            "quality": "Medium precision",
        },
        "senior_leader": {
            "primary": "head of data Europe",
            "secondary": "director data engineering Europe",
            "fallback": ["data platform director Europe", "analytics director Germany"],
            "quality": "Medium precision",
        },
    },
}

# Recommended-filter metadata per market — locations/keywords/companies are
# real-world values, used for filter guidance text only (never the query).
_FILTER_META = {
    "LATAM_USD": {
        "locations": "Brazil, Argentina, Colombia, Chile, Mexico, Uruguay, Peru",
        "keywords": "LATAM, Latin America, Remote, Data Engineer, Azure, AWS, Databricks, Snowflake",
        "companies_recruiter": ["Hays", "Michael Page", "Randstad", "NTT DATA", "BairesDev", "Nearsure"],
        "companies_leader": ["Databricks", "Snowflake", "Nubank", "iFood", "Microsoft"],
    },
    "US_CANADA_NEARSHORE": {
        "locations": "United States, Canada",
        "keywords": "nearshore, LATAM, remote, contractor, Latin America",
        "companies_recruiter": ["Nearsure", "AgileEngine", "Wizeline", "Andela", "BairesDev"],
        "companies_leader": ["Andela", "AgileEngine", "Wizeline", "Toptal"],
    },
    "SPAIN_EU": {
        "locations": "Spain, Portugal, Germany, Netherlands, Ireland (Madrid, Barcelona for Spain specifically)",
        "keywords": "Spain, España, Portugal, Europe, remote, data platform",
        "companies_recruiter": ["Stratesys", "ERNI", "Minsait", "Indra", "Capgemini"],
        "companies_leader": ["Capgemini", "Indra", "Minsait", "ERNI"],
    },
    "EUROPE": {
        "locations": "Germany, Netherlands, Ireland, Portugal, Spain",
        "keywords": "Europe, remote, data platform, data engineer",
        "companies_recruiter": ["Capgemini", "ERNI", "GitLab", "Automattic", "Toptal"],
        "companies_leader": ["GitLab", "Automattic", "Toptal", "Capgemini"],
    },
}

_DEFAULT_MARKET = "LATAM_USD"


def _company_query(role_term: str, company: str) -> str:
    """Short 'role + company' query for account-based networking — never
    a Boolean expression, never an internal bucket label."""
    return f"{role_term} {company}"


def build_strategic_gap_search_pack(market: str, persona: str, urgency: str = "Medium", target_count: int = 0) -> dict:
    """
    Build a realistic LinkedIn People Search pack for one Strategic Gap
    (market, persona) row. Never puts the internal V2 market label
    (LATAM_USD, SPAIN_EU, US_CANADA_NEARSHORE, EUROPE, or any GLOBAL_*/
    NEEDS_*/*_CONFIRMED/*_LIKELY opportunity bucket) into a query or URL.
    """
    market_key = market if market in _QUERY_TABLE else _DEFAULT_MARKET
    group = _persona_group(persona)
    table = _QUERY_TABLE[market_key].get(group, _QUERY_TABLE[market_key]["recruiter_ta"])
    meta = _FILTER_META[market_key]

    primary_query = table["primary"]
    secondary_query = table["secondary"]
    fallback_queries = list(table["fallback"])

    companies = meta["companies_leader"] if group != "recruiter_ta" else meta["companies_recruiter"]
    role_term = PERSONA_TITLE_SYNONYMS.get(persona, [persona.lower()])[0]
    company_query = _company_query(role_term, companies[0]) if companies else primary_query

    market_label = MARKET_DISPLAY_LABEL.get(market_key, market_key)
    recommended_filters = (
        f"People · 2nd degree first · Locations: {meta['locations']} · "
        f"Keywords to try manually: {meta['keywords']}"
    )

    if market_key in BAD_TERMS:
        origin_note = (
            f"instead of the internal bucket label '{market_key}', which LinkedIn does not "
            f"index as a keyword"
        )
    else:
        origin_note = "using a region LinkedIn actually indexes"
    search_rationale = (
        f"Uses real title and region terms for {persona} in {market_label} {origin_note}. "
        f"{table['quality']} query — refine further with the Location/Company filters above, "
        f"not by adding more words to the search box."
    )
    if target_count:
        search_rationale += f" Target: roughly {target_count} contacts in this segment."

    people_search_url = "https://www.linkedin.com/search/results/people/?keywords=" + quote(primary_query, safe="")

    return {
        "primary_query": primary_query,
        "secondary_query": secondary_query,
        "company_query": company_query,
        "people_search_url": people_search_url,
        "recommended_filters": recommended_filters,
        "search_rationale": search_rationale,
        "fallback_queries": fallback_queries,
        "bad_terms_excluded": [market_key] if market_key in BAD_TERMS else [],
        "search_quality": table["quality"],
    }
