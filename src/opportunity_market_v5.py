# -*- coding: utf-8 -*-
"""
opportunity_market_v5.py
=========================
Opportunity Market V5 — replaces UNKNOWN as the primary business market view.

V5 builds on V4 and produces fine-grained opportunity buckets so the dashboard
no longer shows 81% UNKNOWN as the main business conclusion.

Inference hierarchy (each step only applies to still-unresolved rows):
  1. Manual override from config/company_market_overrides.yml
  2. Exact company dictionary match (company_dictionary_enrichment.COMPANY_TO_V5)
  3. Substring company dictionary match
  4. V4 result re-mapped to V5 buckets
  5. Company/title region keywords (BRAZIL, LATAM, US/CA, Spain/EU, Europe)
  6. Company category (staffing → GLOBAL_STAFFING, tech → GLOBAL_TECH, etc.)
  7. Language inference (Portuguese title → LANGUAGE_PORTUGUESE_MARKET, Spanish → LANGUAGE_SPANISH)
  8. High-value persona fallback → GLOBAL_OPPORTUNITY
  9. Has company name, medium priority → NEEDS_COMPANY_MAPPING
  10. Final fallback → LOW_VALUE_UNRESOLVED

Output columns added to each row:
  opportunity_market_v5     – bucket string
  opportunity_bucket        – same as v5 (alias for JS)
  opportunity_confidence    – 0.0–0.95
  opportunity_reason        – human-readable explanation
  is_actionable_opportunity – True if not LOW_VALUE_UNRESOLVED
  is_market_exact           – True if confidence >= 0.85
  needs_manual_company_mapping – True if NEEDS_COMPANY_MAPPING
"""

import logging
import re
from pathlib import Path

import pandas as pd
import yaml

from src.company_normalizer import normalize, normalize_for_search, tokens as name_tokens
from src.company_dictionary_enrichment import (
    COMPANY_TO_V5, SUBSTRING_TO_V5, CONF,
    BRAZIL_CONFIRMED, BRAZIL_LIKELY,
    LATAM_USD_CONFIRMED, LATAM_USD_LIKELY,
    US_CANADA_CONFIRMED, US_CANADA_LIKELY,
    SPAIN_EU_CONFIRMED, SPAIN_EU_LIKELY,
    EUROPE_CONFIRMED, EUROPE_LIKELY,
    GLOBAL_STAFFING, GLOBAL_CONSULTING, GLOBAL_TECH,
    GLOBAL_OPPORTUNITY, LANG_PT, LANG_ES,
    NEEDS_MAPPING, LOW_VALUE,
)

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
CONFIG_DIR  = ROOT / "config"
OUTPUTS_DIR = ROOT / "outputs"

OVERRIDES_YML = CONFIG_DIR / "company_market_overrides.yml"


# ── V4 → V5 bucket mapping ────────────────────────────────────────────────────

V4_TO_V5 = {
    "BRAZIL":                             BRAZIL_CONFIRMED,
    "LATAM_USD":                          LATAM_USD_CONFIRMED,
    "US_CANADA_NEARSHORE":                US_CANADA_CONFIRMED,
    "SPAIN_EU":                           SPAIN_EU_CONFIRMED,
    "EUROPE":                             EUROPE_CONFIRMED,
    "GLOBAL_STAFFING":                    GLOBAL_STAFFING,
    "GLOBAL_CONSULTING":                  GLOBAL_CONSULTING,
    "GLOBAL_TECH":                        GLOBAL_TECH,
    "GLOBAL_OPPORTUNITY_UNRESOLVED_REGION": GLOBAL_OPPORTUNITY,
    "NEEDS_COMPANY_MAPPING":              None,   # further processing needed
    "UNKNOWN_LOW_VALUE":                  LOW_VALUE,
    "UNKNOWN":                            None,   # further processing needed
}

V4_TO_CONF = {
    "BRAZIL":                             CONF["keyword_region"],
    "LATAM_USD":                          CONF["keyword_region"],
    "US_CANADA_NEARSHORE":                CONF["keyword_region"],
    "SPAIN_EU":                           CONF["keyword_region"],
    "EUROPE":                             CONF["keyword_region"],
    "GLOBAL_STAFFING":                    CONF["exact_dict"],
    "GLOBAL_CONSULTING":                  CONF["exact_dict"],
    "GLOBAL_TECH":                        CONF["exact_dict"],
    "GLOBAL_OPPORTUNITY_UNRESOLVED_REGION": CONF["persona_global"],
    "NEEDS_COMPANY_MAPPING":              CONF["needs_mapping"],
    "UNKNOWN_LOW_VALUE":                  CONF["low_value"],
    "UNKNOWN":                            CONF["low_value"],
}

# ── Region keyword patterns ───────────────────────────────────────────────────

BRAZIL_KW   = re.compile(r"\b(brasil|brazil|são paulo|sao paulo|rio de janeiro|curitiba|belo horizonte|porto alegre|floripa|florianopolis|salvador|bahia|brasilia|recife|fortaleza|manaus|belém|belem|goiânia|goiania|campinas|guarulhos|maceio|maceió|natal|teresina)\b", re.I)
LATAM_KW    = re.compile(r"\b(latam|latin|latinoam|colombia|peru|mexico|argentina|chile|ecuador|panama|costa rica|nearshore|remote latam|remoto latam)\b", re.I)
US_KW       = re.compile(r"\b(usa|united states|u\.s\.|north america|san francisco|new york|seattle|austin|chicago|boston|miami|dallas|toronto|vancouver|canada)\b", re.I)
SPAIN_KW    = re.compile(r"\b(spain|espana|españa|madrid|barcelona|valencia|sevilla|bilbao|malaga|málaga|andalucia|catalonia)\b", re.I)
EUROPE_KW   = re.compile(r"\b(europe|europa|germany|deutschland|berlin|netherlands|amsterdam|ireland|dublin|uk|united kingdom|london|paris|france|italy|milan|portugal|lisbon|lisboa|netherlands|sweden|stockholm|norway|oslo|denmark|copenhagen|finland|helsinki|belgium|brussels|switzerland|zurich)\b", re.I)

# Portuguese job title keywords
PT_TERMS    = re.compile(r"\b(analista|engenheiro|engenheira|desenvolvedor|desenvolvedora|coordenador|coordenadora|gerente|recursos humanos|recrutador|recrutadora|recrutamento|dados|projetos|operações|operacoes|gestor|gestora|diretor|diretora|assistente|técnico|técnica|especialista)\b", re.I)

# Spanish job title keywords
ES_TERMS    = re.compile(r"\b(ingeniero|ingenieria|analista|reclutador|reclutadora|talento|recursos humanos|datos|gerente|jefe|jefa|líder|lider|coordinador|coordinadora|desarrollador|desarrolladora|especialista)\b", re.I)

HIGH_VALUE_PERSONAS = {
    "Recruiter", "Talent Acquisition", "Sourcer",
    "Hiring Manager", "Engineering Manager",
    "Data Engineering Manager", "Head of Data",
    "Director", "Executive", "Founder", "Partner",
}


def _load_manual_overrides() -> dict:
    if not OVERRIDES_YML.exists():
        return {}
    try:
        with open(OVERRIDES_YML, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return {k.strip().lower(): str(v).strip().upper() for k, v in raw.items() if v}
    except Exception:
        return {}


def _match_region_keywords(text: str) -> tuple[str, float, str] | None:
    """Return (bucket, confidence, reason) if any region keyword matches."""
    if not text:
        return None
    if BRAZIL_KW.search(text):
        return BRAZIL_CONFIRMED, CONF["keyword_region"], "Brazil region keyword"
    if LATAM_KW.search(text):
        return LATAM_USD_CONFIRMED, CONF["keyword_region"], "LATAM region keyword"
    if US_KW.search(text):
        return US_CANADA_CONFIRMED, CONF["keyword_region"], "US/Canada region keyword"
    if SPAIN_KW.search(text):
        return SPAIN_EU_CONFIRMED, CONF["keyword_region"], "Spain/EU region keyword"
    if EUROPE_KW.search(text):
        return EUROPE_CONFIRMED, CONF["keyword_region"], "Europe region keyword"
    return None


def _company_dict_lookup(company_norm: str) -> tuple[str, float, str] | None:
    """Exact then substring lookup in known company dictionary."""
    if not company_norm:
        return None

    # Exact match
    if company_norm in COMPANY_TO_V5:
        bucket = COMPANY_TO_V5[company_norm]
        return bucket, CONF["exact_dict"], f"exact company dict: {company_norm}"

    # Substring match (longest first)
    search_form = normalize_for_search(company_norm)
    for key, bucket in SUBSTRING_TO_V5:
        if key in search_form:
            return bucket, CONF["exact_dict"] - 0.05, f"substring company match: {key}"

    return None


def _classify_row(
    row,
    overrides: dict,
    mkt_v4_col: str,
    lang_col: str,
) -> tuple[str, float, str]:
    """
    Return (opportunity_market_v5, confidence, reason) for one row.
    10-step hierarchy.
    """
    company = str(row.get("company_clean", "") or "")
    position = str(row.get("position_clean", "") or "")
    persona = str(row.get("persona", "") or "")
    v4 = str(row.get(mkt_v4_col, "") or "")
    language = str(row.get(lang_col, "") or "")
    priority_score = float(row.get("priority_score", 0) or 0)

    combined_text = f"{company} {position}"

    # ── Step 1: Manual override ──────────────────────────────────────────────
    co_lower = company.strip().lower()
    if co_lower in overrides:
        mapped = overrides[co_lower]
        # Map V2-style market names to V5 if needed
        v5 = V4_TO_V5.get(mapped, mapped)
        if v5:
            return v5, CONF["manual"], "manual override"

    # ── Step 2: V4 result already resolved → map to V5 ──────────────────────
    v5_from_v4 = V4_TO_V5.get(v4)
    v4_conf = V4_TO_CONF.get(v4, CONF["low_value"])

    if v5_from_v4 and v5_from_v4 != NEEDS_MAPPING and v5_from_v4 != LOW_VALUE:
        # V4 already resolved this — trust it
        # But still check if company dict gives a better (more specific) answer
        company_norm = normalize(company)
        dict_result = _company_dict_lookup(company_norm)
        if dict_result and dict_result[1] >= v4_conf:
            return dict_result
        return v5_from_v4, v4_conf, f"v4 inference: {v4}"

    # ── Step 3: Company dictionary lookup ────────────────────────────────────
    company_norm = normalize(company)
    dict_result = _company_dict_lookup(company_norm)
    if dict_result:
        return dict_result

    # ── Step 4: Company region keywords ──────────────────────────────────────
    region_co = _match_region_keywords(company)
    if region_co:
        return region_co

    # ── Step 5: Position/title region keywords ────────────────────────────────
    region_pos = _match_region_keywords(position)
    if region_pos:
        return region_pos

    # ── Step 6: Company category (from V4 columns) ────────────────────────────
    cat_v4 = str(row.get("company_category_v4", "") or "")
    if cat_v4 == "GLOBAL_STAFFING":
        return GLOBAL_STAFFING, CONF["company_cat"], "company category: GLOBAL_STAFFING"
    if cat_v4 == "GLOBAL_TECH":
        return GLOBAL_TECH, CONF["company_cat"], "company category: GLOBAL_TECH"
    if cat_v4 == "GLOBAL_CONSULTING":
        return GLOBAL_CONSULTING, CONF["company_cat"], "company category: GLOBAL_CONSULTING"

    # ── Step 7: Language inference ────────────────────────────────────────────
    if language == "PORTUGUESE" or PT_TERMS.search(position):
        return LANG_PT, CONF["language"], "Portuguese title/language signal"
    if language == "SPANISH" or ES_TERMS.search(position):
        return LANG_ES, CONF["language"], "Spanish title/language signal"

    # ── Step 8: High-value persona fallback → GLOBAL_OPPORTUNITY ─────────────
    if persona in HIGH_VALUE_PERSONAS:
        return GLOBAL_OPPORTUNITY, CONF["persona_global"], f"high-value persona: {persona}"

    # ── Step 9: Has company and medium priority → NEEDS_COMPANY_MAPPING ───────
    if company.strip() and (priority_score >= 40 or len(company.strip()) >= 4):
        return NEEDS_MAPPING, CONF["needs_mapping"], "company exists but unresolved"

    # ── Step 10: Final fallback ───────────────────────────────────────────────
    return LOW_VALUE, CONF["low_value"], "no usable signal"


def apply_opportunity_market_v5(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply V5 inference to all rows. Adds opportunity_market_v5 and related columns.
    Preserves ALL existing columns — no changes to market_v2, priority_score, etc.
    """
    df = df.copy()

    # Determine which V4 market column to use
    mkt_v4_col = "market_v4" if "market_v4" in df.columns else "market_v2"
    lang_col   = "language_detected" if "language_detected" in df.columns else "_none_"

    overrides = _load_manual_overrides()
    logger.info(f"  V5: loaded {len(overrides)} manual overrides")

    buckets   = []
    confs     = []
    reasons   = []

    for _, row in df.iterrows():
        b, c, r = _classify_row(row, overrides, mkt_v4_col, lang_col)
        buckets.append(b)
        confs.append(c)
        reasons.append(r)

    df["opportunity_market_v5"]      = buckets
    df["opportunity_bucket"]          = buckets   # alias
    df["opportunity_confidence"]      = confs
    df["opportunity_reason"]          = reasons
    df["is_actionable_opportunity"]   = df["opportunity_market_v5"] != LOW_VALUE
    df["is_market_exact"]             = df["opportunity_confidence"] >= 0.85
    df["needs_manual_company_mapping"]= df["opportunity_market_v5"] == NEEDS_MAPPING

    resolved   = (df["opportunity_market_v5"] != NEEDS_MAPPING) & (df["opportunity_market_v5"] != LOW_VALUE)
    logger.info(
        f"  V5 inference complete: "
        f"{resolved.sum():,}/{len(df):,} resolved "
        f"({resolved.sum()/len(df)*100:.1f}%) | "
        f"needs_mapping={df['needs_manual_company_mapping'].sum():,} | "
        f"low_value={(df['opportunity_market_v5']==LOW_VALUE).sum():,}"
    )

    return df


def build_v5_distribution(df: pd.DataFrame) -> dict:
    """Return distribution dict for public JSON."""
    if "opportunity_market_v5" not in df.columns:
        return {}
    return df["opportunity_market_v5"].value_counts().to_dict()


def build_v5_summary(df: pd.DataFrame) -> dict:
    """Return high-level summary for KPI cards."""
    if "opportunity_market_v5" not in df.columns:
        return {}

    dist = df["opportunity_market_v5"].value_counts().to_dict()
    total = len(df)

    confirmed_geographic = sum(
        dist.get(b, 0) for b in [
            BRAZIL_CONFIRMED, BRAZIL_LIKELY,
            LATAM_USD_CONFIRMED, LATAM_USD_LIKELY,
            US_CANADA_CONFIRMED, US_CANADA_LIKELY,
            SPAIN_EU_CONFIRMED, SPAIN_EU_LIKELY,
            EUROPE_CONFIRMED, EUROPE_LIKELY,
        ]
    )
    global_buckets = sum(
        dist.get(b, 0) for b in [GLOBAL_STAFFING, GLOBAL_CONSULTING, GLOBAL_TECH]
    )
    language_buckets = dist.get(LANG_PT, 0) + dist.get(LANG_ES, 0)
    global_opp = dist.get(GLOBAL_OPPORTUNITY, 0)
    needs_mapping = dist.get(NEEDS_MAPPING, 0)
    low_value = dist.get(LOW_VALUE, 0)
    actionable = total - low_value

    return {
        "total_connections":            total,
        "v5_confirmed_geographic":      confirmed_geographic,
        "v5_confirmed_pct":             round(confirmed_geographic / total * 100, 1) if total else 0,
        "v5_global_buckets":            global_buckets,
        "v5_language_inferred":         language_buckets,
        "v5_global_opportunity":        global_opp,
        "v5_needs_company_mapping":     needs_mapping,
        "v5_low_value_unresolved":      low_value,
        "v5_actionable_total":          actionable,
        "v5_actionable_pct":            round(actionable / total * 100, 1) if total else 0,
        "v5_low_value_pct":             round(low_value / total * 100, 1) if total else 0,
        "v5_distribution":              dist,
        # Breakdown by bucket
        "v5_brazil_confirmed":          dist.get(BRAZIL_CONFIRMED, 0),
        "v5_brazil_likely":             dist.get(BRAZIL_LIKELY, 0),
        "v5_latam_usd_confirmed":       dist.get(LATAM_USD_CONFIRMED, 0),
        "v5_latam_usd_likely":          dist.get(LATAM_USD_LIKELY, 0),
        "v5_us_canada_confirmed":       dist.get(US_CANADA_CONFIRMED, 0),
        "v5_us_canada_likely":          dist.get(US_CANADA_LIKELY, 0),
        "v5_spain_eu_confirmed":        dist.get(SPAIN_EU_CONFIRMED, 0),
        "v5_spain_eu_likely":           dist.get(SPAIN_EU_LIKELY, 0),
        "v5_europe_confirmed":          dist.get(EUROPE_CONFIRMED, 0),
        "v5_europe_likely":             dist.get(EUROPE_LIKELY, 0),
        "v5_global_staffing":           dist.get(GLOBAL_STAFFING, 0),
        "v5_global_consulting":         dist.get(GLOBAL_CONSULTING, 0),
        "v5_global_tech":               dist.get(GLOBAL_TECH, 0),
        "v5_lang_portuguese":           dist.get(LANG_PT, 0),
        "v5_lang_spanish":              dist.get(LANG_ES, 0),
    }


def export_v5_audit(df: pd.DataFrame) -> None:
    """Save detailed V5 audit CSV for review."""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    v5_cols = [
        "full_name", "company_clean", "position_clean", "persona",
        "market_v2", "market_v4",
        "opportunity_market_v5", "opportunity_confidence", "opportunity_reason",
        "is_actionable_opportunity", "is_market_exact", "needs_manual_company_mapping",
        "priority_score",
    ]
    avail = [c for c in v5_cols if c in df.columns]
    df[avail].to_csv(OUTPUTS_DIR / "opportunity_market_v5_audit.csv",
                     index=False, encoding="utf-8-sig")

    # Top companies still needing mapping
    needs = df[df["needs_manual_company_mapping"] == True].copy()
    if not needs.empty:
        agg = (
            needs.groupby("company_clean")
            .agg(
                connection_count   = ("company_clean", "size"),
                avg_priority_score = ("priority_score", "mean"),
                top_persona        = ("persona", lambda x: x.mode()[0] if len(x) > 0 else ""),
            )
            .reset_index()
            .sort_values("connection_count", ascending=False)
        )
        agg["avg_priority_score"] = agg["avg_priority_score"].round(1)
        agg.to_csv(OUTPUTS_DIR / "unresolved_opportunity_buckets.csv",
                   index=False, encoding="utf-8-sig")

    # Top reclassified companies (V2=UNKNOWN → V5 resolved)
    if "market_v2" in df.columns:
        reclassified = df[
            (df["market_v2"] == "UNKNOWN") &
            (df["opportunity_market_v5"] != NEEDS_MAPPING) &
            (df["opportunity_market_v5"] != LOW_VALUE)
        ].copy()
        if not reclassified.empty:
            top_rc = (
                reclassified.groupby(["company_clean", "opportunity_market_v5"])
                .size()
                .reset_index(name="count")
                .sort_values("count", ascending=False)
                .head(100)
            )
            top_rc.to_csv(OUTPUTS_DIR / "top_companies_reclassified_v5.csv",
                          index=False, encoding="utf-8-sig")

    # Company normalization audit
    if "company_clean" in df.columns:
        from src.company_normalizer import normalize
        sample = df[["company_clean", "opportunity_market_v5", "opportunity_confidence"]].copy()
        sample["company_normalized"] = sample["company_clean"].apply(normalize)
        sample.drop_duplicates("company_clean").sort_values("company_clean").head(500).to_csv(
            OUTPUTS_DIR / "company_normalization_audit.csv", index=False, encoding="utf-8-sig"
        )

    logger.info(f"  V5 audit files saved to outputs/")
