# -*- coding: utf-8 -*-
"""
market_inference_v4.py
======================
Enhanced market inference with better UNKNOWN categorization.

Builds on V2 with:
  - Known global company dictionaries
  - Language-based title inference
  - Persona-based global opportunity fallback
  - UNKNOWN split into 3 actionable categories

New columns added:
  market_v4                  – resolved market or UNKNOWN sub-category
  market_group               – GEOGRAPHIC / GLOBAL / UNKNOWN
  market_confidence_v4       – 0.0–0.95
  market_signal_strength     – HIGH / MEDIUM / LOW / NONE
  market_resolution_status   – RESOLVED_HIGH_CONFIDENCE / RESOLVED_MEDIUM_CONFIDENCE /
                               RESOLVED_LOW_CONFIDENCE / GLOBAL_OPPORTUNITY_UNRESOLVED_REGION /
                               NEEDS_COMPANY_MAPPING / UNKNOWN_LOW_VALUE
  language_detected          – PORTUGUESE / SPANISH / ENGLISH / MIXED / UNKNOWN
  company_category_v4        – GLOBAL_STAFFING / GLOBAL_TECH / GLOBAL_CONSULTING / OTHER
  company_category_confidence– HIGH / MEDIUM / LOW
  unknown_reason             – why this contact is still UNKNOWN
  can_be_actioned_even_if_unknown – True if persona is high-value even without market
"""

import logging
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
CONFIG_DIR  = ROOT / "config"
OUTPUTS_DIR = ROOT / "outputs"

OVERRIDES_YML = CONFIG_DIR / "company_market_overrides.yml"

CONF_MANUAL  = 0.95
CONF_COMPANY = 0.85
CONF_TITLE   = 0.75
CONF_GLOBAL  = 0.70
CONF_LOW     = 0.40
CONF_NONE    = 0.00

HIGH_VALUE_PERSONAS = {
    "Recruiter", "Talent Acquisition", "Sourcer",
    "Hiring Manager", "Engineering Manager",
    "Data Engineering Manager", "Head of Data", "Director", "Executive",
}

# ── Known company dictionaries ─────────────────────────────────────────────────

GLOBAL_STAFFING_COMPANIES = {
    "hays", "michael page", "robert half", "randstad", "korn ferry",
    "manpower", "adecco", "kelly services", "staffmark", "hudson",
    "spherion", "allegis", "experis", "gi group", "grafton recruitment",
    "page group", "pagegroup", "spencer stuart", "egon zehnder",
    "russell reynolds", "kforce", "insight global", "tek systems",
    "teksystems", "aerotek", "apex group", "primus solutions",
    "people connections", "hunter",
}

GLOBAL_CONSULTING_COMPANIES = {
    "tcs", "tata consultancy", "ntt data", "capgemini", "accenture",
    "deloitte", "ey", "ernst & young", "kpmg", "pwc",
    "pricewaterhousecoopers", "infosys", "wipro", "cognizant", "hcl",
    "dxc technology", "unisys", "atos", "fujitsu", "ibm consulting",
    "bain", "mckinsey", "boston consulting", "bcg", "oliver wyman",
    "booz allen", "leidos", "saic", "thoughtworks",
}

GLOBAL_TECH_COMPANIES = {
    "databricks", "snowflake", "microsoft", "google", "aws", "amazon",
    "meta", "facebook", "salesforce", "oracle", "sap", "servicenow",
    "workday", "adobe", "linkedin", "apple", "netflix", "spotify",
    "atlassian", "zendesk", "hubspot", "datadog", "confluent", "dbt labs",
    "fivetran", "airbyte", "palantir", "tableau", "qlik", "looker",
    "grafana", "hashicorp", "mongodb", "elastic", "redis", "cloudera",
}

LATAM_NEARSHORE_COMPANIES = {
    "globant", "bairesdev", "nearsure", "wizeline", "endava",
    "epam latam", "gorilla logic", "unosquare", "3pillar",
    "agileengine", "blue people", "lemon.io", "turing",
    "toptal", "crossover", "andela",
}

BRAZIL_COMPANIES = {
    "itaú", "itau", "bradesco", "nubank", "banco do brasil",
    "ambev", "ifood", "picpay", "stone", "totvs", "xp inc",
    "btg pactual", "c6 bank", "pagbank", "pagseguro", "movile",
    "rdstation", "semantix", "dataside", "indicium", "zup innovation",
    "magazine luiza", "magalu", "rappi brasil",
}

# ── Language signals ───────────────────────────────────────────────────────────

PT_TERMS = [
    "analista", "engenheiro", "desenvolvedor", "coordenador", "gerente",
    "recursos humanos", "recrutador", "dados", "projetos", "operações",
    "operacoes", "estagiário", "estagiario", "tecnico", "técnico",
    "especialista", "analisa", "programador", "seleção", "selecao",
    "gestão", "gestao", "suporte", "auxiliar", "assistente de",
]

ES_TERMS = [
    "ingeniero", "analista de", "reclutador", "talento", "recursos humanos",
    "datos", "gerente de", "jefe", "líder", "lider", "coordinador",
    "desarrollador", "programador", "especialista", "asistente de",
    "director de", "encargado", "operaciones",
]

EN_HIGH_VALUE_TITLES = [
    "data engineer", "analytics engineer", "data architect", "cloud data",
    "data platform", "senior data", "lead data", "staff data",
    "head of data", "director of data", "vp data", "chief data",
    "recruiting manager", "talent partner", "sourcing partner",
]

# ── Market / Geographic keywords ──────────────────────────────────────────────

COMPANY_GEO_MAP = [
    (["brasil", "brazil", "são paulo", "sao paulo", "rio de janeiro",
      "belo horizonte", "curitiba", "porto alegre", "florianopolis"],
     "BRAZIL"),
    (["latam", "latin america", "latinoamerica", "colombia", "mexico",
      "argentina", "chile", "peru", "nearshore latam"],
     "LATAM_USD"),
    (["united states", "usa", "u.s.", "new york", "san francisco", "seattle",
      "chicago", "austin", "toronto", "vancouver", "canada"],
     "US_CANADA_NEARSHORE"),
    (["spain", "españa", "espana", "madrid", "barcelona", "valencia",
      "sevilla", "bilbao", "portugal", "lisbon", "lisboa"],
     "SPAIN_EU"),
    (["germany", "deutschland", "berlin", "amsterdam", "netherlands",
      "ireland", "dublin", "uk", "london", "france", "paris", "italy"],
     "EUROPE"),
]

TITLE_GEO_MAP = [
    (["latam", "latin america", "nearshore latam", "america latina"],  "LATAM_USD"),
    (["nearshore", "nearshoring", "usa", "us market", "north america"],  "US_CANADA_NEARSHORE"),
    (["spain", "españa", "espana", "madrid", "barcelona", "portugal"],  "SPAIN_EU"),
    (["europe", "europa", "dach", "emea"],  "EUROPE"),
    (["brasil", "brazil", "são paulo", "sao paulo"],  "BRAZIL"),
]

STAFFING_KW = [
    "staffing", "staff augmentation", "nearshore", "nearshoring",
    "offshore", "outsourc", "talent pool", "talent solutions",
    "talent marketplace", "it recruitment", "tech recruitment",
    "remote talent", "global talent", "workforce solutions",
    "placement", "headhunting", "executive search",
    "recruitment agency", "hunting", "bench", "freelance platform",
    "contractor", "consulting staffing",
]

CONSULTING_KW = [
    "consulting", "consultoria", "consultoría", "consultancy",
    "advisory", "advisors", "management consulting", "it consulting",
    "technology consulting", "digital consulting", "professional services",
    "transformation", "implementação", "delivery",
]

TECH_KW = [
    "software", "technology", "tech", "sistemas", "digital", "platform",
    "cloud", "data", "analytics", "ai", "saas", "fintech", "edtech",
    "startup", "desenvolvimento", "development",
]

LEGAL_SUFFIX_STAFFING = [
    " ltda", " s.a.", " s.a", " sa ", " ag ", " gmbh", " bv ", " nv ",
    " plc", " inc.", " inc ", " corp.", " corp ", " llc", " llp",
]


def _load_overrides() -> dict:
    if not OVERRIDES_YML.exists():
        return {}
    with open(OVERRIDES_YML, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    result = {}
    for company, data in raw.get("overrides", {}).items():
        result[company.lower().strip()] = data
    return result


def _detect_language(position: str) -> str:
    if not position:
        return "UNKNOWN"
    p = position.lower()
    pt_hits = sum(1 for t in PT_TERMS if t in p)
    es_hits = sum(1 for t in ES_TERMS if t in p)
    en_hits = sum(1 for t in EN_HIGH_VALUE_TITLES if t in p)
    if pt_hits > 0 and es_hits == 0:
        return "PORTUGUESE"
    if es_hits > 0 and pt_hits == 0:
        return "SPANISH"
    if pt_hits > 0 and es_hits > 0:
        return "MIXED"
    if en_hits > 0:
        return "ENGLISH"
    return "UNKNOWN"


def _known_company_match(company_lower: str) -> tuple:
    for name in BRAZIL_COMPANIES:
        if name in company_lower:
            return "BRAZIL", "GLOBAL_TECH", "HIGH", f"known Brazil company: {name}"
    for name in GLOBAL_STAFFING_COMPANIES:
        if name in company_lower:
            return "GLOBAL_STAFFING", "GLOBAL_STAFFING", "HIGH", f"known global staffing: {name}"
    for name in GLOBAL_CONSULTING_COMPANIES:
        if name in company_lower:
            return "GLOBAL_CONSULTING", "GLOBAL_CONSULTING", "HIGH", f"known global consulting: {name}"
    for name in GLOBAL_TECH_COMPANIES:
        if name in company_lower:
            return "GLOBAL_TECH", "GLOBAL_TECH", "HIGH", f"known global tech: {name}"
    for name in LATAM_NEARSHORE_COMPANIES:
        if name in company_lower:
            return "LATAM_USD", "GLOBAL_TECH", "HIGH", f"known LATAM/nearshore company: {name}"
    return None, None, None, None


def _geo_keyword_match(text: str, geo_map: list) -> tuple:
    tl = text.lower() if text else ""
    for keywords, market in geo_map:
        for kw in keywords:
            if kw in tl:
                return market, kw
    return None, None


def _company_category_from_keywords(company_lower: str) -> tuple:
    for kw in STAFFING_KW:
        if kw in company_lower:
            return "GLOBAL_STAFFING", "MEDIUM"
    for kw in CONSULTING_KW:
        if kw in company_lower:
            return "GLOBAL_CONSULTING", "LOW"
    for kw in TECH_KW:
        if kw in company_lower:
            return "GLOBAL_TECH", "LOW"
    return "OTHER", "LOW"


def _infer_v4(
    company: str,
    position: str,
    persona: str,
    existing_market_v2: str,
    manual_overrides: dict,
) -> dict:
    company_lower  = (company or "").lower().strip()
    position_lower = (position or "").lower().strip()

    result = {
        "market_v4":                    "UNKNOWN_LOW_VALUE",
        "market_group":                 "UNKNOWN",
        "market_confidence_v4":         CONF_NONE,
        "market_signal_strength":       "NONE",
        "market_resolution_status":     "UNKNOWN_LOW_VALUE",
        "language_detected":            _detect_language(position),
        "company_category_v4":          "OTHER",
        "company_category_confidence":  "LOW",
        "unknown_reason":               "no usable signal in company or title",
        "can_be_actioned_even_if_unknown": persona in HIGH_VALUE_PERSONAS,
    }

    # 1. Manual override
    if company_lower in manual_overrides:
        ov = manual_overrides[company_lower]
        mkt = ov.get("market", ov.get("strategic_market", ""))
        cat = ov.get("category", "OTHER")
        if mkt:
            result.update({
                "market_v4":                "RESOLVED_MANUAL",
                "market_group":             "GEOGRAPHIC" if mkt in {"BRAZIL","LATAM_USD","US_CANADA_NEARSHORE","SPAIN_EU","EUROPE"} else "GLOBAL",
                "market_confidence_v4":     CONF_MANUAL,
                "market_signal_strength":   "HIGH",
                "market_resolution_status": "RESOLVED_HIGH_CONFIDENCE",
                "company_category_v4":      cat,
                "unknown_reason":           "",
            })
            result["market_v4"] = mkt
            return result

    # 2. V2 result if already resolved high-confidence
    if existing_market_v2 and existing_market_v2 not in ("UNKNOWN", ""):
        conf = CONF_COMPANY
        result.update({
            "market_v4":                existing_market_v2,
            "market_group":             "GEOGRAPHIC" if existing_market_v2 in {"BRAZIL","LATAM_USD","US_CANADA_NEARSHORE","SPAIN_EU","EUROPE"} else "GLOBAL",
            "market_confidence_v4":     conf,
            "market_signal_strength":   "HIGH",
            "market_resolution_status": "RESOLVED_HIGH_CONFIDENCE",
            "company_category_v4":      _company_category_from_keywords(company_lower)[0],
            "unknown_reason":           "",
        })
        return result

    # 3. Known company dictionaries
    mkt, cat, conf_str, reason = _known_company_match(company_lower)
    if mkt:
        result.update({
            "market_v4":                mkt,
            "market_group":             "GEOGRAPHIC" if mkt in {"BRAZIL"} else "GLOBAL",
            "market_confidence_v4":     CONF_COMPANY,
            "market_signal_strength":   "HIGH",
            "market_resolution_status": "RESOLVED_HIGH_CONFIDENCE",
            "company_category_v4":      cat or "OTHER",
            "company_category_confidence": "HIGH",
            "unknown_reason":           "",
        })
        return result

    # 4. Company geo keyword
    geo_mkt, geo_kw = _geo_keyword_match(company, COMPANY_GEO_MAP)
    if geo_mkt:
        result.update({
            "market_v4":                geo_mkt,
            "market_group":             "GEOGRAPHIC",
            "market_confidence_v4":     CONF_COMPANY,
            "market_signal_strength":   "HIGH",
            "market_resolution_status": "RESOLVED_HIGH_CONFIDENCE",
            "unknown_reason":           "",
        })
        return result

    # 5. Title geo keyword
    title_mkt, title_kw = _geo_keyword_match(position, TITLE_GEO_MAP)
    if title_mkt:
        result.update({
            "market_v4":                title_mkt,
            "market_group":             "GEOGRAPHIC",
            "market_confidence_v4":     CONF_TITLE,
            "market_signal_strength":   "MEDIUM",
            "market_resolution_status": "RESOLVED_MEDIUM_CONFIDENCE",
            "unknown_reason":           "",
        })
        return result

    # 6. Company suffix / category keywords
    cat_kw, cat_conf = _company_category_from_keywords(company_lower)
    result["company_category_v4"]         = cat_kw
    result["company_category_confidence"] = cat_conf

    if cat_kw == "GLOBAL_STAFFING":
        result.update({
            "market_v4":                "GLOBAL_STAFFING",
            "market_group":             "GLOBAL",
            "market_confidence_v4":     CONF_LOW,
            "market_signal_strength":   "MEDIUM",
            "market_resolution_status": "RESOLVED_LOW_CONFIDENCE",
            "unknown_reason":           "",
        })
        return result

    # 7. Language-based inference
    lang = result["language_detected"]
    if lang == "PORTUGUESE":
        result.update({
            "market_v4":                "BRAZIL",
            "market_group":             "GEOGRAPHIC",
            "market_confidence_v4":     CONF_LOW,
            "market_signal_strength":   "LOW",
            "market_resolution_status": "RESOLVED_LOW_CONFIDENCE",
            "unknown_reason":           "inferred from Portuguese title",
        })
        return result
    if lang == "SPANISH":
        result.update({
            "market_v4":                "LATAM_USD",
            "market_group":             "GEOGRAPHIC",
            "market_confidence_v4":     CONF_LOW,
            "market_signal_strength":   "LOW",
            "market_resolution_status": "RESOLVED_LOW_CONFIDENCE",
            "unknown_reason":           "inferred from Spanish title",
        })
        return result

    # 8. High-value persona = GLOBAL_OPPORTUNITY if no region found
    if persona in HIGH_VALUE_PERSONAS:
        result.update({
            "market_v4":                "GLOBAL_OPPORTUNITY_UNRESOLVED_REGION",
            "market_group":             "GLOBAL",
            "market_confidence_v4":     CONF_NONE,
            "market_signal_strength":   "LOW",
            "market_resolution_status": "GLOBAL_OPPORTUNITY_UNRESOLVED_REGION",
            "unknown_reason":           "high-value persona but no region signal in company or title",
        })
        return result

    # 9. Needs company mapping if company exists but has no signal
    if company_lower and len(company_lower) > 2:
        result.update({
            "market_v4":                "NEEDS_COMPANY_MAPPING",
            "market_resolution_status": "NEEDS_COMPANY_MAPPING",
            "unknown_reason":           "company exists but no geographic or category signal",
        })
        return result

    # Final fallback
    result.update({
        "market_v4":                "UNKNOWN_LOW_VALUE",
        "market_resolution_status": "UNKNOWN_LOW_VALUE",
        "unknown_reason":           "no company, no title signal, no high-value persona",
    })
    return result


def apply_market_inference_v4(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enriches df with V4 market columns.
    Requires: company_clean, position_clean, persona, market_v2 columns.
    """
    df = df.copy()
    manual_overrides = _load_overrides()

    mkt_v2_col = "market_v2" if "market_v2" in df.columns else "strategic_market"

    records = []
    for _, row in df.iterrows():
        r = _infer_v4(
            company            = str(row.get("company_clean", "") or ""),
            position           = str(row.get("position_clean", "") or ""),
            persona            = str(row.get("persona", "") or ""),
            existing_market_v2 = str(row.get(mkt_v2_col, "") or ""),
            manual_overrides   = manual_overrides,
        )
        records.append(r)

    v4_df = pd.DataFrame(records)
    for col in v4_df.columns:
        df[col] = v4_df[col].values

    total    = len(df)
    resolved = (df["market_resolution_status"] == "RESOLVED_HIGH_CONFIDENCE").sum()
    glob_opp = (df["market_resolution_status"] == "GLOBAL_OPPORTUNITY_UNRESOLVED_REGION").sum()
    needs_map= (df["market_resolution_status"] == "NEEDS_COMPANY_MAPPING").sum()
    low_val  = (df["market_resolution_status"] == "UNKNOWN_LOW_VALUE").sum()

    logger.info(f"  V4 inference: {resolved:,} resolved-high ({100*resolved/total:.0f}%) | "
                f"{glob_opp:,} global-opp | {needs_map:,} needs-mapping | {low_val:,} low-value")
    return df


def export_market_v4_audit(df: pd.DataFrame) -> pd.DataFrame:
    """Export audit CSV with V4 resolution details."""
    cols = [
        "full_name", "company_clean", "position_clean", "persona",
        "market_v2", "market_v4", "market_group",
        "market_confidence_v4", "market_signal_strength",
        "market_resolution_status", "language_detected",
        "company_category_v4", "unknown_reason",
        "can_be_actioned_even_if_unknown", "priority_score",
    ]
    cols = [c for c in cols if c in df.columns]
    audit = df[cols].sort_values("market_v4")
    audit_path = OUTPUTS_DIR / "market_v4_audit.csv"
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    audit.to_csv(audit_path, index=False, encoding="utf-8-sig")
    logger.info(f"  V4 audit saved: {audit_path.name} ({len(audit)} rows)")
    return audit


def build_unknown_by_reason(df: pd.DataFrame) -> pd.DataFrame:
    """Group unknown contacts by resolution_status and reason."""
    if "market_resolution_status" not in df.columns:
        return pd.DataFrame()
    mask = df["market_resolution_status"].isin([
        "GLOBAL_OPPORTUNITY_UNRESOLVED_REGION",
        "NEEDS_COMPANY_MAPPING",
        "UNKNOWN_LOW_VALUE",
    ])
    unk = df[mask]
    if unk.empty:
        return pd.DataFrame()
    agg = (
        unk.groupby(["market_resolution_status", "unknown_reason"])
        .agg(
            count        = ("company_clean", "size"),
            recruiters   = ("persona", lambda x: x.isin({"Recruiter","Talent Acquisition","Sourcer"}).sum()),
            hiring_mgrs  = ("persona", lambda x: x.isin({"Hiring Manager","Engineering Manager"}).sum()),
            avg_score    = ("priority_score", "mean"),
        )
        .reset_index()
        .sort_values("count", ascending=False)
    )
    agg["avg_score"] = agg["avg_score"].round(1)
    path = OUTPUTS_DIR / "unknown_by_reason.csv"
    agg.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"  Unknown-by-reason saved: {path.name}")
    return agg


def build_unknown_reduction_simulation(df: pd.DataFrame) -> pd.DataFrame:
    """Simulate how many contacts would be resolved by category improvement."""
    if "market_resolution_status" not in df.columns:
        return pd.DataFrame()
    rows = []
    for status, label in [
        ("GLOBAL_OPPORTUNITY_UNRESOLVED_REGION", "Global Opportunity (needs region tag)"),
        ("NEEDS_COMPANY_MAPPING",                "Needs manual company mapping"),
        ("UNKNOWN_LOW_VALUE",                    "Low value — no action needed"),
    ]:
        mask = df["market_resolution_status"] == status
        sub  = df[mask]
        rows.append({
            "resolution_status": status,
            "label":             label,
            "count":             int(len(sub)),
            "recruiter_count":   int(sub["persona"].isin({"Recruiter","Talent Acquisition","Sourcer"}).sum()),
            "hiring_mgr_count":  int(sub["persona"].isin({"Hiring Manager","Engineering Manager"}).sum()),
            "avg_score":         round(sub["priority_score"].mean(), 1) if len(sub) > 0 else 0,
            "pct_of_total":      round(100 * len(sub) / max(len(df), 1), 1),
        })
    sim = pd.DataFrame(rows)
    path = OUTPUTS_DIR / "unknown_reduction_simulation.csv"
    sim.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"  Unknown reduction simulation saved: {path.name}")
    return sim
