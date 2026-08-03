# -*- coding: utf-8 -*-
"""
untapped_outreach_score.py
===========================
Untapped Outreach Scoring V9.

Computes `untapped_outreach_score` — an INDEPENDENT 0-100 score used only to
rank never-contacted 1st-degree connections for first outreach. This is a
separate signal from (and does NOT replace) relationship_value_score,
immediate_action_score, outreach_adjusted_score, or the base priority_score.

Design principle: never contacted is the opportunity, not a penalty. This
module never subtracts points solely because a contact has no message
history — every contact scored here already has no message history by
construction (see untapped_network_intelligence.py, which only calls this
for NEVER_CONTACTED_CONFIRMED / LIKELY_NEVER_CONTACTED rows).

Inputs are local only: the already-classified connections dataframe (title,
company, persona, opportunity_bucket — computed upstream by
market_inference_v4.py / opportunity_market_v5.py / company_resolution_v7.py),
config/company_category_rules.yml, config/company_market_overrides.yml, an
optional company-level cross-signal from lead_reactivation_engine.py (has
this company already produced a warm lead?), and an optional, private, manual
enrichment CSV (data/manual/profile_enrichment.csv — gitignored, never
committed, safe to be entirely absent).

No LinkedIn scraping or automation of any kind — everything here is a lookup
or keyword match over data already on disk.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
import yaml

from src.company_normalizer import normalize as normalize_company

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
CONFIG_DIR  = ROOT / "config"
DATA_DIR    = ROOT / "data"

CATEGORY_RULES_YML   = CONFIG_DIR / "company_category_rules.yml"
MARKET_OVERRIDES_YML = CONFIG_DIR / "company_market_overrides.yml"
MANUAL_ENRICHMENT_CSV = DATA_DIR / "manual" / "profile_enrichment.csv"

MANUAL_ENRICHMENT_COLUMNS = [
    "profile_url", "full_name", "company_clean", "observed_headline",
    "observed_location", "observed_active_hiring_post", "observed_market_signal",
    "manual_opportunity_bucket", "manual_priority_boost", "manual_reason",
    "last_manual_review_date",
]

RECRUITING_PERSONAS  = {"Recruiter", "Talent Acquisition", "Sourcer"}
HIRING_PERSONAS       = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS  = {"Data Engineering Manager", "Head of Data", "Director"}
HIGH_VALUE_PERSONAS   = RECRUITING_PERSONAS | HIRING_PERSONAS | DATA_LEADER_PERSONAS | {"Executive"}

STRONG_BUCKETS = {"LATAM_USD_CONFIRMED", "US_CANADA_CONFIRMED", "GLOBAL_STAFFING", "GLOBAL_OPPORTUNITY"}
LIKELY_BUCKETS = {"LATAM_USD_LIKELY", "US_CANADA_LIKELY", "BRAZIL_CONFIRMED", "BRAZIL_LIKELY"}
UNRESOLVED_BUCKETS = {"", "UNKNOWN", "LOW_VALUE_UNRESOLVED", "NONE", "OTHER"}

# ── Title keyword signal groups (Part 2/5) ────────────────────────────────
LATAM_TITLE_KW = [
    "latam", "latin america", "américa latina", "america latina", "south america",
]
US_TITLE_KW = [
    "u.s.", "us", "usa", "united states", "north america", "nearshore",
]
BILINGUAL_GLOBAL_KW = [
    "bilingual", "english", "global", "international", "corporate recruiter",
]
TECH_RECRUITER_KW = [
    "tech recruiter", "it recruiter", "data recruiter", "cloud recruiter", "ai recruiter",
]

# Strong compound title phrases (Part 5) — assign a confident opportunity
# bucket even when the company itself is not yet mapped to any market.
STRONG_TITLE_PATTERNS = [
    ("corporate u.s. recruiter",     "US_CANADA_CONFIRMED"),
    ("u.s. corporate recruiter",     "US_CANADA_CONFIRMED"),
    ("us corporate recruiter",       "US_CANADA_CONFIRMED"),
    ("senior tech recruiter latam",  "LATAM_USD_CONFIRMED"),
    ("tech recruiter latam",         "LATAM_USD_CONFIRMED"),
    ("bilingual tech recruiter",     "LATAM_USD_CONFIRMED"),
    ("latam recruiter",              "LATAM_USD_CONFIRMED"),
    ("it recruiter",                 "LATAM_USD_CONFIRMED"),
]

UBIMINDS_COMPANY_REASON = (
    "LATAM / international recruiting company connecting LATAM talent with U.S. companies."
)

OPPORTUNITY_MARKET_LABELS = {
    "LATAM_USD_CONFIRMED":  "LATAM → USD (confirmed)",
    "LATAM_USD_LIKELY":     "LATAM → USD (likely)",
    "US_CANADA_CONFIRMED":  "US/Canada (confirmed)",
    "US_CANADA_LIKELY":     "US/Canada (likely)",
    "BRAZIL_CONFIRMED":     "Brazil (confirmed)",
    "BRAZIL_LIKELY":        "Brazil (likely)",
    "SPAIN_EU_CONFIRMED":   "Spain/EU (confirmed)",
    "SPAIN_EU_LIKELY":      "Spain/EU (likely)",
    "EUROPE_CONFIRMED":     "Europe (confirmed)",
    "EUROPE_LIKELY":        "Europe (likely)",
    "GLOBAL_STAFFING":      "Global Staffing",
    "GLOBAL_TECH":          "Global Tech",
    "GLOBAL_CONSULTING":    "Global Consulting",
    "GLOBAL_OPPORTUNITY":   "Global Opportunity",
}

# ── First-message templates by persona (Part 9) — literal, sanitized, never
# claims prior contact. Returns None for personas this module does not own
# (Talent Acquisition, EU exploratory, generic) so the caller falls back to
# the broader angle set in untapped_network_intelligence.py. ────────────────
_ANGLE_LATAM_USD_RECRUITER = (
    "Hi [Name], thanks for being connected. I saw that you work with LATAM / U.S. "
    "recruiting. I'm a Data Engineer focused on Azure, AWS, Databricks, SQL and "
    "ETL/ELT, currently open to remote LATAM/US-aligned opportunities. Happy to "
    "stay in touch if you work with data engineering roles."
)
_ANGLE_US_CORPORATE_RECRUITER = (
    "Hi [Name], I noticed your work with U.S. corporate recruiting and LATAM "
    "talent. I'm a Data Engineer with experience in cloud data platforms, "
    "ETL/ELT pipelines, Azure, AWS and Databricks. I'd be happy to connect in "
    "case you support remote Data Engineering roles."
)
_ANGLE_HIRING_MANAGER = (
    "Hi [Name], I follow data engineering and cloud analytics work closely. "
    "I'm a Data Engineer focused on Azure, AWS, Databricks, SQL and scalable "
    "pipelines. Happy to connect and exchange ideas around data platforms."
)


def _kw_hit(text: str, keywords: list[str]) -> str | None:
    """Word-boundary-aware substring search — returns the first matching
    keyword, or None. Mirrors the matching style already used across the
    market-inference modules in this codebase."""
    for kw in keywords:
        if re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", text):
            return kw
    return None


def _strong_title_override(title_l: str) -> tuple[str | None, str | None]:
    for phrase, bucket in STRONG_TITLE_PATTERNS:
        if phrase in title_l:
            return bucket, phrase
    return None, None


def _truthy(v) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s not in ("", "0", "false", "no", "none", "nan")


# ── Company intelligence (Part 4) ──────────────────────────────────────────

_staffing_sets_cache: dict | None = None


def _load_staffing_company_sets() -> tuple[set, set]:
    """Lazy-loaded, cached for the lifetime of the process — this is called
    once per never-contacted contact, so avoid re-reading YAML every time."""
    global _staffing_sets_cache
    if _staffing_sets_cache is not None:
        return _staffing_sets_cache["known"], _staffing_sets_cache["keywords"]

    known: set[str] = set()
    keywords: set[str] = set()
    try:
        with open(CATEGORY_RULES_YML, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        cat = raw.get("global_staffing", {}) or {}
        known |= {str(c).lower().strip() for c in cat.get("known_companies", [])}
        keywords |= {str(k).lower().strip() for k in cat.get("keywords", [])}
    except Exception as exc:
        logger.warning(f"  untapped_outreach_score: failed to read {CATEGORY_RULES_YML.name}: {exc}")

    try:
        with open(MARKET_OVERRIDES_YML, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        for company, data in (raw.get("overrides") or {}).items():
            if isinstance(data, dict) and data.get("category") == "GLOBAL_STAFFING":
                known.add(str(company).lower().strip())
    except Exception as exc:
        logger.warning(f"  untapped_outreach_score: failed to read {MARKET_OVERRIDES_YML.name}: {exc}")

    _staffing_sets_cache = {"known": known, "keywords": keywords}
    return known, keywords


def is_known_staffing_or_nearshore_company(company_clean: str) -> bool:
    """LATAM-to-US / nearshore / staffing / recruiting company, per Part 4 —
    checked against the exact raw name, the normalized name (company alias
    table in company_normalizer.py), and category keywords."""
    if not company_clean:
        return False
    raw_l = str(company_clean).lower().strip()
    norm = normalize_company(company_clean)
    known, keywords = _load_staffing_company_sets()
    if raw_l in known or norm in known:
        return True
    for kw in keywords:
        if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", raw_l):
            return True
    return False


def _company_market_reason(company_clean: str) -> str:
    if normalize_company(company_clean) == "ubiminds":
        return UBIMINDS_COMPANY_REASON
    return f"known LATAM/nearshore staffing or recruiting company ({company_clean})"


# ── Manual enrichment (optional, private, gitignored) ─────────────────────

def load_manual_enrichment(path: Path = MANUAL_ENRICHMENT_CSV) -> dict:
    """
    Returns an index keyed by ("url", normalized_profile_url) and
    ("name_company", (name_lower, company_lower)) -> record dict.

    The file is entirely optional and private (see .gitignore) — a missing
    file simply means no manual signals are applied, never an error.
    """
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception as exc:
        logger.warning(f"  untapped_outreach_score: failed to read manual enrichment CSV: {exc}")
        return {}

    index: dict = {}
    for _, row in df.iterrows():
        rec = {col: str(row.get(col, "")).strip() for col in MANUAL_ENRICHMENT_COLUMNS if col in df.columns}
        url = rec.get("profile_url", "").lower().rstrip("/")
        name_company = (rec.get("full_name", "").lower(), rec.get("company_clean", "").lower())
        if url:
            index[("url", url)] = rec
        if name_company[0] and name_company[1]:
            index[("name_company", name_company)] = rec
    return index


def match_manual_enrichment(full_name: str, company_clean: str, profile_url: str, index: dict) -> dict:
    if not index:
        return {}
    url = str(profile_url or "").strip().lower().rstrip("/")
    if url and ("url", url) in index:
        return index[("url", url)]
    key = (str(full_name or "").strip().lower(), str(company_clean or "").strip().lower())
    return index.get(("name_company", key), {})


# ── Core scoring (Parts 1-3, 5, 10) ────────────────────────────────────────

def score_untapped_contact(
    full_name: str,
    company_clean: str,
    position_clean: str,
    persona: str,
    opportunity_bucket: str,
    opportunity_confidence,
    seniority: str,
    days_connected,
    manual_record: dict | None = None,
    company_has_warm_signal: bool = False,
    company_has_rejection_signal: bool = False,
) -> dict:
    """
    Returns:
      {
        "untapped_outreach_score": int 0-100,
        "untapped_reason":         str (sanitized, human-readable),
        "opportunity_bucket":      str (possibly upgraded from the input),
        "opportunity_market":      str (human label),
        "market_confidence":       float 0-1,
        "market_reason":           str,
        "exact_location_available": bool,
        "observed_location_manual": str,
        "first_message_angle":     str | None,  # None => caller falls back
      }

    Never penalizes a contact solely for having no message history — that is
    the entire premise of this population (Part 3).
    """
    title    = str(position_clean or "")
    title_l  = title.lower()
    persona  = str(persona or "")
    bucket   = str(opportunity_bucket or "").strip()
    manual   = manual_record or {}

    score = 0.0
    positives: list[str] = []

    is_recruiting_persona = persona in RECRUITING_PERSONAS
    is_high_value_persona = persona in HIGH_VALUE_PERSONAS

    if is_recruiting_persona:
        score += 25
        positives.append(f"{persona.lower()} persona")
    elif is_high_value_persona:
        score += 15
        positives.append(f"{persona.lower()} persona")

    latam_kw = _kw_hit(title_l, LATAM_TITLE_KW)
    if latam_kw:
        score += 20
        positives.append(f"title mentions LATAM ('{latam_kw}')")

    us_kw = _kw_hit(title_l, US_TITLE_KW)
    if us_kw:
        score += 20
        positives.append(f"title mentions U.S./nearshore ('{us_kw}')")

    bilingual_kw = _kw_hit(title_l, BILINGUAL_GLOBAL_KW)
    if bilingual_kw:
        score += 15
        positives.append(f"bilingual/global signal ('{bilingual_kw}')")

    tech_recruiter_kw = _kw_hit(title_l, TECH_RECRUITER_KW)
    if tech_recruiter_kw:
        score += 15
        positives.append(f"tech/IT recruiter title ('{tech_recruiter_kw}')")

    is_staffing_company = is_known_staffing_or_nearshore_company(company_clean)
    if is_staffing_company:
        score += 20
        positives.append(f"known LATAM/US staffing company ({company_clean})")

    if bucket in STRONG_BUCKETS:
        score += 15
        positives.append(f"opportunity bucket {bucket}")

    try:
        days = int(days_connected) if days_connected not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    if days is not None and days > 30:
        score += 10
        positives.append(f"connected {days}d, never contacted")

    if _truthy(manual.get("observed_active_hiring_post")):
        score += 10
        positives.append("manually observed active hiring post")

    if _truthy(manual.get("manual_priority_boost")):
        score += 10
        positives.append("manual priority boost")

    if company_has_warm_signal:
        score += 10
        positives.append(f"{company_clean} already produced warm leads/replies")

    # ── Strong title-keyword bucket override (Part 5) — works even when the
    # company itself has not been mapped to any market yet. Company-level
    # signal wins when present (a company's overall placement market is more
    # reliable than a single title phrase); the title override is the
    # fallback for genuinely unmapped companies.
    override_bucket, override_kw = _strong_title_override(title_l)
    final_bucket = bucket if bucket in STRONG_BUCKETS else None
    if not final_bucket and is_staffing_company:
        final_bucket = "LATAM_USD_CONFIRMED"
    if not final_bucket and override_bucket:
        final_bucket = override_bucket
        score += 20
        positives.append(f"strong recruiter-title signal ('{override_kw}')")
    if not final_bucket:
        final_bucket = bucket or "UNKNOWN"

    # ── Negative signals (Part 3) — never fires solely for "never contacted" ──
    negatives: list[str] = []
    has_any_positive_signal = bool(
        is_recruiting_persona or latam_kw or us_kw or tech_recruiter_kw
        or is_staffing_company or final_bucket in STRONG_BUCKETS
    )
    if not is_high_value_persona and not has_any_positive_signal:
        score -= 30
        negatives.append("persona/title unrelated to recruiting, hiring, or data leadership")

    if (
        final_bucket.upper() in UNRESOLVED_BUCKETS
        and not is_staffing_company
        and not is_high_value_persona
    ):
        score -= 20
        negatives.append("company/category not resolved to any known opportunity market")

    if company_has_rejection_signal:
        score -= 25
        negatives.append(f"{company_clean} history shows rejected/closed outcomes")

    only_vague_global = bool(
        bilingual_kw and not (latam_kw or us_kw or tech_recruiter_kw or is_recruiting_persona or is_staffing_company)
    )
    if only_vague_global:
        score -= 15
        negatives.append("only a vague global/bilingual signal, no recruiting/data/market signal")

    final_score = int(max(0, min(100, round(score))))

    # ── Market fields (Part 10) ────────────────────────────────────────────
    base_conf = float(opportunity_confidence or 0)
    if final_bucket in STRONG_BUCKETS and (is_staffing_company or override_bucket):
        market_confidence = max(base_conf, 0.85)
        market_reason = _company_market_reason(company_clean) if is_staffing_company else (
            f"title keyword match: '{override_kw}'" if override_kw else "resolved opportunity bucket"
        )
    elif final_bucket in STRONG_BUCKETS:
        market_confidence = base_conf
        market_reason = "resolved opportunity bucket (upstream market inference)"
    else:
        market_confidence = base_conf
        market_reason = "no strong company or title market signal found"

    manual_bucket = manual.get("manual_opportunity_bucket", "")
    if manual_bucket:
        final_bucket = manual_bucket
        market_confidence = max(market_confidence, 0.90)
        market_reason = f"manual enrichment: {manual.get('manual_reason', 'manually observed opportunity signal')}"

    exact_location_available = bool(manual.get("observed_location", "").strip())
    observed_location_manual = manual.get("observed_location", "") if exact_location_available else ""

    # ── Reason sentence (Part 8) ───────────────────────────────────────────
    reason = _reason_sentence(final_score, positives, negatives)

    # ── First-message angle (Part 9) ───────────────────────────────────────
    angle = _first_message_angle_v9(persona, final_bucket, title_l)

    return {
        "untapped_outreach_score": final_score,
        "untapped_reason": reason,
        "opportunity_bucket": final_bucket,
        "opportunity_market": OPPORTUNITY_MARKET_LABELS.get(final_bucket, final_bucket),
        "market_confidence": round(min(1.0, market_confidence), 2),
        "market_reason": market_reason,
        "exact_location_available": exact_location_available,
        "observed_location_manual": observed_location_manual,
        "first_message_angle": angle,
    }


def _reason_sentence(score: int, positives: list[str], negatives: list[str]) -> str:
    if not positives:
        return "Lower priority: no recruiting/data/market signal found."
    if score >= 85:
        lead = "High-value first outreach"
    elif score >= 70:
        lead = "Strong LATAM/USD recruiter signal" if any(
            "latam" in p.lower() or "staffing" in p.lower() for p in positives
        ) else "Strong outreach candidate"
    elif score >= 40:
        lead = "Good first outreach candidate"
    else:
        lead = "Lower priority"

    detail = " + ".join(positives[:3])
    sentence = f"{lead}: {detail}."
    if negatives and score < 40:
        sentence += " " + negatives[0] + "."
    return sentence


def _first_message_angle_v9(persona: str, final_bucket: str, title_l: str) -> str | None:
    if persona in HIRING_PERSONAS or persona in DATA_LEADER_PERSONAS:
        return _ANGLE_HIRING_MANAGER
    us_corporate_kw = _kw_hit(title_l, [
        "corporate u.s. recruiter", "u.s. corporate recruiter", "us corporate recruiter",
    ])
    if us_corporate_kw or final_bucket == "US_CANADA_CONFIRMED":
        return _ANGLE_US_CORPORATE_RECRUITER
    if persona in RECRUITING_PERSONAS:
        return _ANGLE_LATAM_USD_RECRUITER
    return None


# ══════════════════════════════════════════════════════════════════════════
# Untapped Activation Potential Scoring (V10)
# ══════════════════════════════════════════════════════════════════════════
# A SEPARATE, additive 0-100 score — untapped_activation_potential_score —
# that answers a narrower question than untapped_outreach_score above: "does
# this never-contacted 1st-degree connection look like a recruiter / TA /
# sourcer / talent partner who places LATAM / international / USD talent,
# regardless of whether their company or opportunity bucket has been
# resolved yet?" It exists because company/bucket resolution lags behind
# title signal — a "Global Recruiter & Sr Talent Partner | Connecting LATAM
# Talent with International Teams" profile at a company that hasn't been
# mapped to any market yet should not wait for company resolution to rank
# near the top of the weekly outreach queue.
#
# Does NOT replace untapped_outreach_score (above), outreach_adjusted_score,
# relationship_value_score, immediate_action_score, or the base
# priority_score. See untapped_network_intelligence.py for how this combines
# with those into untapped_execution_score (the page's default ranking).
#
# Same design principle as V9: never-contacted is the opportunity, not a
# penalty — this module never subtracts points solely because a contact has
# no message history.

ACTIVATION_RECRUITING_PERSONAS = RECRUITING_PERSONAS | {"Talent Partner", "HR Recruiter"}
ACTIVATION_NEVER_CONTACTED_STATUSES = {"NEVER_CONTACTED_CONFIRMED", "LIKELY_NEVER_CONTACTED"}

ACTIVATION_LATAM_KW = [
    "latam", "latin america", "américa latina", "america latina", "south america",
]
ACTIVATION_INTL_KW = [
    "international", "global", "remote", "nearshore",
    "u.s.", "us", "usa", "united states",
]
ACTIVATION_STRONG_TITLE_KW = [
    "global recruiter", "talent partner", "talent acquisition", "it recruiter",
    "tech recruiter", "senior recruiter", "headhunter", "sourcer",
]
ACTIVATION_STAFFING_KW = [
    "staffing", "recruiting", "consulting", "talent solutions", "talent partner",
    "international teams",
]
ACTIVATION_ROLE_TECH_KW = [
    "data", "it", "tech", "cloud", "engineering", "software", "analytics",
]

ACTIVATION_STRONG_BUCKETS = {
    "LATAM_USD_CONFIRMED", "US_CANADA_CONFIRMED", "GLOBAL_STAFFING",
    "GLOBAL_OPPORTUNITY", "GLOBAL_CONSULTING",
}
ACTIVATION_LOW_VALUE_BUCKETS = {"LOW_VALUE_UNRESOLVED", "LOW_VALUE"}

# ── New category labels (V10) ──────────────────────────────────────────────
CAT_HOT_RECRUITER          = "HOT_UNTAPPED_RECRUITER"
CAT_HOT_TALENT_PARTNER     = "HOT_UNTAPPED_TALENT_PARTNER"
CAT_HIGH_POTENTIAL_LONG    = "HIGH_POTENTIAL_LONG_CONNECTED"
CAT_LATAM_INTL_RECRUITER   = "LATAM_INTERNATIONAL_RECRUITER"
CAT_GLOBAL_RECRUITER       = "GLOBAL_RECRUITER_UNTAPPED"
CAT_FIRST_MESSAGE_NOW      = "FIRST_MESSAGE_NOW"
CAT_FIRST_MESSAGE_WEEK     = "FIRST_MESSAGE_THIS_WEEK"
CAT_BACKLOG                = "BACKLOG_UNTAPPED"

# Default weekly-queue priority order (most to least urgent) — used by
# untapped_network_intelligence.py's build_weekly_untapped_queue() to rank
# WITHIN each strategic-focus allocation bucket, without changing the
# existing ~90/10 LATAM/EU allocation itself.
ACTIVATION_CATEGORY_QUEUE_PRIORITY = [
    CAT_HOT_RECRUITER, CAT_LATAM_INTL_RECRUITER, CAT_HIGH_POTENTIAL_LONG, CAT_GLOBAL_RECRUITER,
]

_ANGLE_ACTIVATION_LATAM_INTL = (
    "Hi [Name], thanks for being connected. I noticed your work connecting LATAM "
    "talent with international teams. I'm a Data Engineer focused on Azure, AWS, "
    "Databricks, SQL, Python and ETL/ELT pipelines, currently open to remote "
    "LATAM/US-aligned opportunities. Happy to stay in touch if you work with "
    "data engineering roles."
)
_ANGLE_ACTIVATION_GLOBAL_RECRUITER = (
    "Hi [Name], thanks for being connected. I saw your background in global "
    "recruiting / talent partnerships. I'm a Data Engineer with experience in "
    "cloud data platforms, Azure, AWS, Databricks, SQL and Python. I'd be happy "
    "to stay in touch for remote Data Engineering opportunities."
)
_ANGLE_ACTIVATION_IT_TECH_RECRUITER = (
    "Hi [Name], thanks for being connected. I'm a Data Engineer focused on "
    "cloud data platforms, ETL/ELT, Azure, AWS, Databricks, SQL and Python. "
    "Happy to connect in case you work with Data Engineering, Cloud Data or "
    "Analytics Engineering roles."
)


def activation_age_bucket_label(days) -> str:
    """Human-readable connected-age bucket for the Activation Pattern
    Learning UI — same boundaries as _connection_age_bucket() in
    untapped_network_intelligence.py, human-readable labels for direct
    display/filtering as the `connected_age_bucket` public field."""
    try:
        d = int(days) if days not in (None, "") else None
    except (TypeError, ValueError):
        d = None
    if d is None or d < 0:
        return "Unknown"
    if d <= 30:
        return "0-30 days"
    if d <= 90:
        return "31-90 days"
    if d <= 180:
        return "91-180 days"
    if d <= 365:
        return "181-365 days"
    return "365+ days"


def _activation_category(
    score: int, is_recruiting_persona: bool, latam_kw: str | None, intl_kw: str | None,
    strong_title_kw: str | None, days,
) -> str:
    has_recruiter_signal = is_recruiting_persona or bool(strong_title_kw)
    age_long = days is not None and days >= 180

    if score >= 80 and latam_kw and has_recruiter_signal:
        return CAT_LATAM_INTL_RECRUITER
    if score >= 80 and not latam_kw and (intl_kw or strong_title_kw) and has_recruiter_signal:
        return CAT_GLOBAL_RECRUITER
    if score >= 80 and is_recruiting_persona:
        return CAT_HOT_RECRUITER
    if score >= 80 and strong_title_kw and "talent partner" in strong_title_kw:
        return CAT_HOT_TALENT_PARTNER
    if score >= 60 and age_long:
        return CAT_HIGH_POTENTIAL_LONG
    if score >= 70:
        return CAT_FIRST_MESSAGE_NOW
    if score >= 50:
        return CAT_FIRST_MESSAGE_WEEK
    return CAT_BACKLOG


def _activation_reason_sentence(score: int, positives: list[str], negatives: list[str]) -> str:
    if not positives:
        return "Lower activation priority: no recruiting/data/market signal found."
    if score >= 85:
        lead = "Very high activation potential"
    elif score >= 70:
        lead = "High activation potential"
    elif score >= 50:
        lead = "Good activation candidate"
    else:
        lead = "Lower activation priority"
    detail = " + ".join(positives[:5])
    sentence = f"{lead}: {detail}."
    if negatives and score < 50:
        sentence += " " + negatives[0] + "."
    return sentence


def score_activation_potential(
    full_name: str,
    company_clean: str,
    position_clean: str,
    persona: str,
    opportunity_bucket: str,
    history_status: str,
    days_connected,
    company_has_warm_signal: bool = False,
) -> dict:
    """
    Untapped Activation Potential Scoring (V10) — see module section header.

    Returns:
      {
        "untapped_activation_potential_score": int 0-100,
        "activation_category":  str (one of the CAT_* labels above),
        "activation_reason":    str (sanitized, human-readable),
        "first_message_priority": "TODAY" | "THIS_WEEK" | "BACKLOG" | "LOW_PRIORITY",
        "first_message_angle":  str | None,  # None => caller falls back to V9/generic
      }

    Never penalizes a contact solely for having no message history, and
    never penalizes solely for being long-connected — long-connected +
    never-contacted is exactly the opportunity this function exists to find.
    """
    title      = str(position_clean or "")
    title_l    = title.lower()
    company    = str(company_clean or "")
    company_l  = company.lower()
    combined_l = (title_l + " " + company_l).strip()
    persona    = str(persona or "")
    bucket     = str(opportunity_bucket or "").strip().upper()
    status     = str(history_status or "").strip().upper()

    try:
        days = int(days_connected) if days_connected not in (None, "") else None
    except (TypeError, ValueError):
        days = None

    score = 0.0
    positives: list[str] = []

    is_recruiting_persona = persona in ACTIVATION_RECRUITING_PERSONAS

    if status in ACTIVATION_NEVER_CONTACTED_STATUSES:
        score += 30
        positives.append("never contacted — this is the opportunity, not a penalty")

    if is_recruiting_persona:
        score += 25
        positives.append(f"{persona.lower()} persona")

    latam_kw = _kw_hit(combined_l, ACTIVATION_LATAM_KW)
    if latam_kw:
        score += 20
        positives.append(f"LATAM signal ('{latam_kw}')")

    intl_kw = _kw_hit(combined_l, ACTIVATION_INTL_KW)
    if intl_kw:
        score += 20
        positives.append(f"international/global/remote/US signal ('{intl_kw}')")

    strong_title_kw = _kw_hit(title_l, ACTIVATION_STRONG_TITLE_KW)
    if strong_title_kw:
        score += 20
        positives.append(f"strong recruiter/talent-partner title ('{strong_title_kw}')")

    staffing_kw = _kw_hit(combined_l, ACTIVATION_STAFFING_KW)
    if staffing_kw:
        score += 15
        positives.append(f"staffing/recruiting/talent-solutions signal ('{staffing_kw}')")

    age_gt_180 = days is not None and days > 180
    age_gt_365 = days is not None and days > 365
    if age_gt_180:
        score += 15
        positives.append(f"connected {days}d — long-standing 1st-degree connection")
    if age_gt_365:
        score += 10
        positives.append("connected 365+ days")

    if bucket in ACTIVATION_STRONG_BUCKETS:
        score += 15
        positives.append(f"opportunity bucket {bucket}")

    role_tech_kw = _kw_hit(title_l, ACTIVATION_ROLE_TECH_KW)
    if role_tech_kw:
        score += 15
        positives.append(f"role relevance signal ('{role_tech_kw}')")

    if company_has_warm_signal:
        score += 10
        positives.append(f"{company} already produced replies/warm leads/opportunities")

    # Never-contacted, 1st-degree — the premise of this entire population;
    # never a penalty, always a small credit (see module docstring).
    score += 10
    positives.append("existing 1st-degree connection, never contacted")

    age_gt_90 = days is not None and days > 90
    if age_gt_90 and is_recruiting_persona:
        score += 10
        positives.append("connected 90+ days and recruiter/TA persona")

    # ── Negative signals — never fires solely for "never contacted" ──────────
    negatives: list[str] = []
    has_any_positive_signal = bool(
        is_recruiting_persona or latam_kw or intl_kw or strong_title_kw
        or staffing_kw or role_tech_kw or bucket in ACTIVATION_STRONG_BUCKETS
    )
    if not has_any_positive_signal:
        score -= 30
        negatives.append("persona/title unrelated to recruiting, data, or market opportunity")

    has_recruiter_title_signal = bool(is_recruiting_persona or strong_title_kw or staffing_kw)
    if bucket in ACTIVATION_LOW_VALUE_BUCKETS and not has_recruiter_title_signal:
        score -= 20
        negatives.append("low-value/unresolved company with no recruiter/TA/sourcer/title signal")

    if bucket in ACTIVATION_LOW_VALUE_BUCKETS:
        score -= 20
        negatives.append("opportunity bucket is low value")

    age_very_recent = days is not None and days < 30
    if age_very_recent and not has_any_positive_signal:
        score -= 15
        negatives.append("connected very recently with no market/persona signal yet")

    final_score = int(max(0, min(100, round(score))))

    category = _activation_category(
        final_score, is_recruiting_persona, latam_kw, intl_kw, strong_title_kw, days,
    )

    if final_score >= 80 or category in (
        CAT_HOT_RECRUITER, CAT_HOT_TALENT_PARTNER, CAT_LATAM_INTL_RECRUITER, CAT_GLOBAL_RECRUITER,
    ):
        priority = "TODAY"
    elif final_score >= 55:
        priority = "THIS_WEEK"
    elif final_score >= 30:
        priority = "BACKLOG"
    else:
        priority = "LOW_PRIORITY"

    reason = _activation_reason_sentence(final_score, positives, negatives)

    angle = None
    if latam_kw and (is_recruiting_persona or strong_title_kw):
        angle = _ANGLE_ACTIVATION_LATAM_INTL
    elif (intl_kw or strong_title_kw) and is_recruiting_persona:
        angle = _ANGLE_ACTIVATION_GLOBAL_RECRUITER
    elif role_tech_kw and (is_recruiting_persona or strong_title_kw):
        angle = _ANGLE_ACTIVATION_IT_TECH_RECRUITER

    return {
        "untapped_activation_potential_score": final_score,
        "activation_category": category,
        "activation_reason": reason,
        "first_message_priority": priority,
        "first_message_angle": angle,
    }


def compute_untapped_execution_score(
    untapped_outreach_score,
    untapped_activation_potential_score,
    priority_score,
    persona: str,
    history_status: str,
) -> int:
    """
    Untapped Network default-ranking score (V10):

        untapped_execution_score = max(
            untapped_outreach_score,
            untapped_activation_potential_score,
            base priority_score adjusted by never-contacted recruiter potential,
        )

    Purely a ranking convenience — never mutates or replaces
    untapped_outreach_score, outreach_adjusted_score, relationship_value_score,
    immediate_action_score, or priority_score.
    """
    outreach_v   = float(untapped_outreach_score or 0)
    activation_v = float(untapped_activation_potential_score or 0)
    base_v       = float(priority_score or 0)

    status  = str(history_status or "").strip().upper()
    persona = str(persona or "")
    if status in ACTIVATION_NEVER_CONTACTED_STATUSES and persona in ACTIVATION_RECRUITING_PERSONAS:
        base_v = min(100.0, base_v + 15.0)

    return int(max(0, min(100, round(max(outreach_v, activation_v, base_v)))))
