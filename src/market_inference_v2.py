# -*- coding: utf-8 -*-
"""
market_inference_v2.py
======================
Improved market inference that adds new columns alongside existing V1 data.
Does NOT overwrite strategic_market or market_confidence from V1.

New columns added:
  market_v2          – improved market label
  market_type        – inference method used
  market_confidence_v2 – 0.0–0.95 confidence score
  inference_reason_v2  – human-readable explanation
  company_category   – GLOBAL_STAFFING / GLOBAL_TECH / GLOBAL_CONSULTING / OTHER

V2 Markets:
  BRAZIL, LATAM_USD, US_CANADA_NEARSHORE, SPAIN_EU, EUROPE,
  GLOBAL_STAFFING, GLOBAL_TECH, GLOBAL_CONSULTING, UNKNOWN

market_type values:
  MANUAL_COMPANY_OVERRIDE    – config/company_market_overrides.yml match
  MANUAL_FILE_OVERRIDE       – outputs/company_market_mapping_template.csv manual entry
  COMPANY_KEYWORD            – company name keyword match
  TITLE_KEYWORD              – position title keyword match
  GLOBAL_COMPANY             – known global company category
  UNKNOWN                    – no signal found
"""

import logging
import re
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR  = ROOT / "config"
OUTPUTS_DIR = ROOT / "outputs"

OVERRIDES_YML  = CONFIG_DIR / "company_market_overrides.yml"
CATEGORIES_YML = CONFIG_DIR / "company_category_rules.yml"
KEYWORDS_YML   = CONFIG_DIR / "market_keywords.yml"
MANUAL_CSV     = OUTPUTS_DIR / "company_market_mapping_template.csv"

MARKET_TYPE_MANUAL_OVERRIDE = "MANUAL_COMPANY_OVERRIDE"
MARKET_TYPE_MANUAL_FILE     = "MANUAL_FILE_OVERRIDE"
MARKET_TYPE_COMPANY_KW      = "COMPANY_KEYWORD"
MARKET_TYPE_TITLE_KW        = "TITLE_KEYWORD"
MARKET_TYPE_GLOBAL          = "GLOBAL_COMPANY"
MARKET_TYPE_UNKNOWN         = "UNKNOWN"

CONF_MANUAL   = 0.95
CONF_COMPANY  = 0.85
CONF_TITLE    = 0.75
CONF_GLOBAL   = 0.70
CONF_UNKNOWN  = 0.00

MARKET_ORDER = [
    "SPAIN_EU", "EUROPE", "US_CANADA_NEARSHORE",
    "LATAM_USD", "BRAZIL",
    "GLOBAL_STAFFING", "GLOBAL_TECH", "GLOBAL_CONSULTING",
]


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        logger.warning(f"Config not found: {path}")
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_overrides() -> dict[str, dict]:
    """Returns {company_lower: {market, category}}"""
    raw = _load_yaml(OVERRIDES_YML)
    result = {}
    for company, data in raw.get("overrides", {}).items():
        result[company.lower().strip()] = {
            "market":   data.get("market", "UNKNOWN"),
            "category": data.get("category", "OTHER"),
        }
    return result


def _load_categories() -> dict[str, dict]:
    """Returns {category_name: {keywords: [...], known_companies: [...]}}"""
    raw = _load_yaml(CATEGORIES_YML)
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def _load_keywords() -> dict[str, dict]:
    """Returns {market_lower: {company: [...], title: [...]}}"""
    raw = _load_yaml(KEYWORDS_YML)
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def _load_manual_csv() -> dict[str, str]:
    """Returns {company_lower: market} from filled manual_market column."""
    if not MANUAL_CSV.exists():
        return {}
    try:
        df = pd.read_csv(MANUAL_CSV, dtype=str)
        if "manual_market" not in df.columns or "company_clean" not in df.columns:
            return {}
        filled = df[df["manual_market"].notna() & (df["manual_market"].str.strip() != "")]
        return {
            row["company_clean"].lower().strip(): row["manual_market"].strip().upper()
            for _, row in filled.iterrows()
        }
    except Exception as e:
        logger.warning(f"Could not load manual CSV: {e}")
        return {}


# ── Keyword helpers ───────────────────────────────────────────────────────────

def _contains(text: str, keywords: list[str]) -> tuple[bool, str]:
    """Return (matched, keyword) if any keyword found in text."""
    tl = text.lower()
    for kw in keywords:
        # Word-boundary-aware match
        if re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", tl):
            return True, kw
    return False, ""


# ── Category detection ────────────────────────────────────────────────────────

def _detect_category(company_lower: str, categories: dict) -> str:
    """Classify company into GLOBAL_STAFFING / GLOBAL_TECH / GLOBAL_CONSULTING / OTHER."""
    for cat_name, cat_data in categories.items():
        known = [c.lower() for c in cat_data.get("known_companies", [])]
        if company_lower in known:
            return cat_name.upper()
        kws = cat_data.get("keywords", [])
        matched, _ = _contains(company_lower, kws)
        if matched:
            return cat_name.upper()
    return "OTHER"


# ── Single-row inference ──────────────────────────────────────────────────────

def _infer_row(
    company: str,
    position: str,
    overrides: dict,
    manual_csv: dict,
    keywords: dict,
    categories: dict,
) -> tuple[str, str, float, str, str]:
    """
    Returns (market_v2, market_type, confidence, reason, company_category).
    """
    company_l  = company.lower().strip() if company else ""
    position_l = position.lower().strip() if position else ""

    # ── Step 1: Manual CSV override ───────────────────────────────────────────
    if company_l and company_l in manual_csv:
        market = manual_csv[company_l]
        return (
            market,
            MARKET_TYPE_MANUAL_FILE,
            CONF_MANUAL,
            f"manual CSV override: {company} → {market}",
            _detect_category(company_l, categories),
        )

    # ── Step 2: YAML company override ─────────────────────────────────────────
    if company_l and company_l in overrides:
        o = overrides[company_l]
        return (
            o["market"],
            MARKET_TYPE_MANUAL_OVERRIDE,
            CONF_MANUAL,
            f"company YAML override: {company} → {o['market']}",
            o.get("category", "OTHER"),
        )

    # ── Step 3: Company keyword matching ─────────────────────────────────────
    market_map = {
        "spain_eu":            "SPAIN_EU",
        "europe":              "EUROPE",
        "us_canada_nearshore": "US_CANADA_NEARSHORE",
        "latam_usd":           "LATAM_USD",
        "brazil":              "BRAZIL",
    }
    for yml_key, market in market_map.items():
        kws = keywords.get(yml_key, {}).get("company", [])
        matched, kw = _contains(company_l, kws)
        if matched:
            cat = _detect_category(company_l, categories)
            return (
                market,
                MARKET_TYPE_COMPANY_KW,
                CONF_COMPANY,
                f"company keyword match: '{kw}' in '{company}'",
                cat,
            )

    # ── Step 4: Title keyword matching ────────────────────────────────────────
    for yml_key, market in market_map.items():
        kws = keywords.get(yml_key, {}).get("title", [])
        matched, kw = _contains(position_l, kws)
        if matched:
            cat = _detect_category(company_l, categories)
            return (
                market,
                MARKET_TYPE_TITLE_KW,
                CONF_TITLE,
                f"title keyword match: '{kw}' in '{position}'",
                cat,
            )

    # ── Step 5: Global company category ───────────────────────────────────────
    cat = _detect_category(company_l, categories) if company_l else "OTHER"
    if cat in ("GLOBAL_STAFFING", "GLOBAL_TECH", "GLOBAL_CONSULTING"):
        return (
            cat,          # market_v2 = category name for global companies
            MARKET_TYPE_GLOBAL,
            CONF_GLOBAL,
            f"global company category: {cat.lower().replace('_', ' ')}",
            cat,
        )

    # ── Step 6: UNKNOWN ───────────────────────────────────────────────────────
    return (
        "UNKNOWN",
        MARKET_TYPE_UNKNOWN,
        CONF_UNKNOWN,
        "no market signal found in company or title",
        "OTHER",
    )


# ── Bulk inference ────────────────────────────────────────────────────────────

def apply_market_inference_v2(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply V2 market inference to classified connections DataFrame.
    Adds columns: market_v2, market_type, market_confidence_v2,
                  inference_reason_v2, company_category.
    Original columns are NOT modified.
    """
    logger.info("Loading V2 config files …")
    overrides   = _load_overrides()
    manual_csv  = _load_manual_csv()
    keywords    = _load_keywords()
    categories  = _load_categories()

    logger.info(
        f"  Overrides: {len(overrides)} | Manual CSV: {len(manual_csv)} | "
        f"Keyword sets: {len(keywords)} | Categories: {len(categories)}"
    )

    df = df.copy()

    markets_v2, types, confidences, reasons, company_cats = [], [], [], [], []

    for _, row in df.iterrows():
        company  = str(row.get("company_clean", "") or "")
        position = str(row.get("position_clean", "") or "")

        m, t, c, r, cc = _infer_row(
            company, position, overrides, manual_csv, keywords, categories
        )
        markets_v2.append(m)
        types.append(t)
        confidences.append(c)
        reasons.append(r)
        company_cats.append(cc)

    df["market_v2"]           = markets_v2
    df["market_type"]         = types
    df["market_confidence_v2"]= confidences
    df["inference_reason_v2"] = reasons
    df["company_category"]    = company_cats

    # Summary
    v2_dist = df["market_v2"].value_counts()
    unknown_pct = round(v2_dist.get("UNKNOWN", 0) / len(df) * 100, 1)
    logger.info(f"V2 Market distribution: {v2_dist.to_dict()}")
    logger.info(f"V2 UNKNOWN: {v2_dist.get('UNKNOWN', 0):,} ({unknown_pct}%)")
    logger.info(f"V2 Improvement vs V1: "
                f"V1 UNKNOWN={df['strategic_market'].eq('UNKNOWN').sum():,} | "
                f"V2 UNKNOWN={v2_dist.get('UNKNOWN',0):,}")

    return df


# ── Unknown company export ────────────────────────────────────────────────────

def export_unknown_companies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Export top unknown companies (V2 UNKNOWN) for manual classification.
    Returns the DataFrame and saves to outputs/.
    """
    unknown_mask = df["market_v2"] == "UNKNOWN"
    unknown_df   = df[unknown_mask & (df["company_clean"].str.strip() != "")]

    agg = (
        unknown_df
        .groupby("company_clean")
        .agg(
            connection_count  = ("company_clean", "size"),
            top_persona       = ("persona", lambda x: x.mode()[0] if len(x) > 0 else ""),
            top_area          = ("area",    lambda x: x.mode()[0] if len(x) > 0 else ""),
            avg_priority_score= ("priority_score", "mean"),
        )
        .reset_index()
        .sort_values("connection_count", ascending=False)
    )
    agg["avg_priority_score"] = agg["avg_priority_score"].round(1)

    # Suggested market based on persona heuristics
    def _suggest_market(row):
        p = row["top_persona"]
        if p in ("Recruiter", "Talent Acquisition", "Sourcer"):
            return "LATAM_USD"      # conservative default for unknown recruiters
        return ""

    agg["suggested_market"]   = agg.apply(_suggest_market, axis=1)
    agg["suggested_category"] = ""
    agg["manual_market"]      = ""
    agg["manual_category"]    = ""
    agg["reason"]             = ""

    out_path = OUTPUTS_DIR / "top_unknown_companies_to_classify.csv"
    agg.head(300).to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info(f"Top unknown companies saved: {out_path.name}")

    # Also save the full template for manual mapping
    template_path = OUTPUTS_DIR / "company_market_mapping_template.csv"
    agg.head(300).to_csv(template_path, index=False, encoding="utf-8-sig")
    logger.info(f"Company mapping template saved: {template_path.name}")

    return agg


def export_inference_audit(df: pd.DataFrame) -> pd.DataFrame:
    """
    Export a sample audit of inference decisions for review.
    """
    audit_cols = [
        "full_name", "company_clean", "position_clean",
        "strategic_market", "market_confidence",
        "market_v2", "market_type", "market_confidence_v2",
        "inference_reason_v2", "company_category",
    ]
    audit_cols = [c for c in audit_cols if c in df.columns]
    audit = df[audit_cols].copy()

    out_path = OUTPUTS_DIR / "market_inference_audit.csv"
    audit.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info(f"Inference audit saved: {out_path.name}")
    return audit
