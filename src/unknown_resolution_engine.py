# -*- coding: utf-8 -*-
"""
unknown_resolution_engine.py
============================
Groups UNKNOWN connections by company, auto-suggests market classification
using extended keyword + persona heuristics, and outputs prioritized
override candidates for manual review.

Key insight: classifying ONE company can resolve dozens or hundreds of connections.

Outputs:
  outputs/unknown_resolution_backlog.csv       — all UNKNOWN companies ranked
  outputs/unknown_company_auto_suggestions.csv — companies with high-confidence auto-suggestions
  outputs/company_override_candidates.csv      — top 150 for manual override
  outputs/unresolved_high_value_contacts.csv   — individual high-value UNKNOWN contacts
"""

import logging
import re
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
CONFIG_DIR  = ROOT / "config"
OUTPUTS_DIR = ROOT / "outputs"

OVERRIDES_YML  = CONFIG_DIR / "company_market_overrides.yml"
CATEGORIES_YML = CONFIG_DIR / "company_category_rules.yml"
KEYWORDS_YML   = CONFIG_DIR / "market_keywords.yml"

# ── Extended staffing signals ─────────────────────────────────────────────────
STAFFING_SIGNALS = [
    "staffing", "staff augmentation", "nearshore", "nearshoring",
    "offshore", "outsourc", "talent pool", "talent solutions",
    "talent marketplace", "it recruitment", "tech recruitment",
    "technology recruitment", "remote talent", "global talent",
    "workforce solutions", "placement", "headhunting", "executive search",
    "recruitment agency", "agência de recrutamento", "agencia de reclutamiento",
    "hunting", "staffers", "sourcers", "bench", "freelance platform",
    "contractor", "consulting staffing",
]

CONSULTING_SIGNALS = [
    "consulting", "consultoria", "consultoría", "consultancy",
    "advisory", "advisors", "solutions", "management consulting",
    "it consulting", "technology consulting", "digital consulting",
    "professional services", "transformation", "services",
    "implementação", "implementacion", "delivery",
]

TECH_SIGNALS = [
    "software", "technology", "tech", "sistemas", "sistemas de informação",
    "digital", "platform", "cloud", "data", "analytics", "ai", "saas",
    "fintech", "edtech", "healthtech", "proptech", "insurtech",
    "startup", "desenvolvimento", "development",
]

LATAM_SIGNALS = [
    "latam", "latin", "latinoam", "colombia", "colombia", "peru",
    "mexico", "argentina", "chile", "ecuador", "panama", "costa rica",
    "nearshore latam", "nearshore lat",
]

BRAZIL_SIGNALS = [
    "brasil", "brazil", "são paulo", "sao paulo", "rio", "curitiba",
    "belo horizonte", "porto alegre", "floripa", "florianópolis",
    "salvador", "bahia", "brasília", "recife",
]

SPAIN_SIGNALS = [
    "spain", "espana", "españa", "madrid", "barcelona", "valencia",
    "sevilla", "bilbao", "málaga", "malaga",
]

EUROPE_SIGNALS = [
    "europe", "europa", "germany", "deutschland", "berlin",
    "netherlands", "amsterdam", "ireland", "dublin", "uk",
    "london", "paris", "france", "italy", "milan", "portugal",
    "lisbon", "lisboa",
]

US_SIGNALS = [
    "usa", "united states", "u.s.", "north america",
    "san francisco", "new york", "seattle", "austin", "chicago",
    "canada", "toronto", "vancouver",
]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _match(text: str, signals: list) -> tuple[bool, str]:
    tl = text.lower()
    for s in signals:
        if s.lower() in tl:
            return True, s
    return False, ""


def _infer_company_category(company_lower: str, persona_counts: dict) -> tuple[str, str, str]:
    """
    Return (suggested_market, suggested_confidence, suggested_reason).
    Uses company name keywords + persona composition heuristics.
    """
    total = sum(persona_counts.values())
    recruiters = (persona_counts.get("Recruiter", 0) +
                  persona_counts.get("Talent Acquisition", 0) +
                  persona_counts.get("Sourcer", 0) +
                  persona_counts.get("HR", 0))
    data_roles  = (persona_counts.get("Data Engineer", 0) +
                   persona_counts.get("Analytics Engineer", 0) +
                   persona_counts.get("Data Analyst", 0) +
                   persona_counts.get("Data Scientist", 0) +
                   persona_counts.get("Machine Learning / AI", 0))
    consultants = persona_counts.get("Consultant", 0)
    leaders     = (persona_counts.get("Data Engineering Manager", 0) +
                   persona_counts.get("Head of Data", 0) +
                   persona_counts.get("Director", 0))

    # ── Step 1: Geographic keywords (highest confidence) ─────────────────────
    for signals, market in [
        (BRAZIL_SIGNALS, "BRAZIL"),
        (LATAM_SIGNALS,  "LATAM_USD"),
        (SPAIN_SIGNALS,  "SPAIN_EU"),
        (EUROPE_SIGNALS, "EUROPE"),
        (US_SIGNALS,     "US_CANADA_NEARSHORE"),
    ]:
        matched, kw = _match(company_lower, signals)
        if matched:
            return market, "HIGH", f"company name contains geographic keyword: '{kw}'"

    # ── Step 2: Staffing company signals ─────────────────────────────────────
    matched, kw = _match(company_lower, STAFFING_SIGNALS)
    if matched:
        return "GLOBAL_STAFFING", "HIGH", f"company name contains staffing keyword: '{kw}'"

    # ── Step 3: Persona heuristics ────────────────────────────────────────────
    if total > 0:
        rec_ratio  = recruiters  / total
        data_ratio = data_roles  / total
        cons_ratio = consultants / total

        if rec_ratio >= 0.6 and total >= 3:
            return "GLOBAL_STAFFING", "MEDIUM", (
                f"persona composition: {recruiters}/{total} are recruiters/HR "
                f"({rec_ratio:.0%}) — likely staffing or recruitment firm"
            )
        if rec_ratio >= 0.4 and total >= 5:
            return "GLOBAL_STAFFING", "MEDIUM", (
                f"persona composition: {recruiters}/{total} are recruiters/HR "
                f"({rec_ratio:.0%}) — possible staffing firm"
            )
        if data_ratio >= 0.5 and total >= 3:
            matched_tech, kw2 = _match(company_lower, TECH_SIGNALS)
            cat = "GLOBAL_TECH" if matched_tech else "GLOBAL_TECH"
            return cat, "MEDIUM", (
                f"persona composition: {data_roles}/{total} are data roles "
                f"({data_ratio:.0%}) — likely tech or data company"
            )
        if cons_ratio >= 0.4 and total >= 3:
            matched_c, kw3 = _match(company_lower, CONSULTING_SIGNALS)
            return "GLOBAL_CONSULTING", "MEDIUM", (
                f"persona composition: {consultants}/{total} are consultants "
                f"({cons_ratio:.0%}) — likely consulting firm"
            )

    # ── Step 4: Company name signals (tech/consulting) ────────────────────────
    matched_tech, kw_t = _match(company_lower, TECH_SIGNALS)
    matched_cons, kw_c = _match(company_lower, CONSULTING_SIGNALS)

    if matched_tech and not matched_cons:
        return "GLOBAL_TECH", "LOW", f"company name contains tech keyword: '{kw_t}'"
    if matched_cons and not matched_tech:
        return "GLOBAL_CONSULTING", "LOW", f"company name contains consulting keyword: '{kw_c}'"
    if matched_tech and matched_cons:
        return "GLOBAL_CONSULTING", "LOW", f"mixed signals: tech '{kw_t}' + consulting '{kw_c}'"

    return "UNKNOWN", "UNKNOWN", "no usable signal found in company name or persona composition"


def run_unknown_resolution_engine(df: pd.DataFrame) -> dict:
    """
    Main entry point. Analyze all UNKNOWN connections and output resolution files.
    """
    mkt_col = "market_v2" if "market_v2" in df.columns else "strategic_market"

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Filter UNKNOWN contacts ────────────────────────────────────────────
    unk_mask = df[mkt_col] == "UNKNOWN"
    unk_df   = df[unk_mask & (df["company_clean"].str.strip() != "")].copy()
    logger.info(f"  UNKNOWN contacts: {len(unk_df):,} from "
                f"{unk_df['company_clean'].nunique():,} unique companies")

    # ── 2. Group by company ───────────────────────────────────────────────────
    persona_sets = unk_df.groupby("company_clean")["persona"].apply(
        lambda x: x.value_counts().to_dict()
    ).to_dict()

    agg = (
        unk_df.groupby("company_clean")
        .agg(
            connection_count    = ("company_clean", "size"),
            recruiter_count     = ("persona", lambda x: x.isin(
                {"Recruiter", "Sourcer"}).sum()),
            talent_count        = ("persona", lambda x: x.isin(
                {"Talent Acquisition"}).sum()),
            hiring_manager_count= ("persona", lambda x: x.isin(
                {"Hiring Manager", "Engineering Manager"}).sum()),
            data_leader_count   = ("persona", lambda x: x.isin(
                {"Data Engineering Manager", "Head of Data",
                 "Director", "Executive"}).sum()),
            data_peer_count     = ("persona", lambda x: x.isin(
                {"Data Engineer", "Analytics Engineer", "Data Analyst",
                 "BI Analyst", "Data Scientist", "Machine Learning / AI"}).sum()),
            avg_priority_score  = ("priority_score", "mean"),
            top_persona         = ("persona", lambda x: x.mode()[0] if len(x) > 0 else ""),
            top_area            = ("area",    lambda x: x.mode()[0] if len(x) > 0 else ""),
        )
        .reset_index()
    )

    agg["avg_priority_score"] = agg["avg_priority_score"].round(1)

    # ── 3. Strategic potential score (for ranking) ────────────────────────────
    agg["strategic_potential"] = (
        agg["recruiter_count"]      * 3.0 +
        agg["talent_count"]         * 2.5 +
        agg["hiring_manager_count"] * 4.0 +
        agg["data_leader_count"]    * 2.0 +
        agg["connection_count"]     * 0.1 +
        agg["avg_priority_score"]   * 0.5
    ).round(1)

    agg = agg.sort_values(
        ["strategic_potential", "connection_count"],
        ascending=False
    ).reset_index(drop=True)

    # ── 4. Auto-suggest classification ───────────────────────────────────────
    suggestions, confidences, reasons = [], [], []

    for _, row in agg.iterrows():
        company_lower = row["company_clean"].lower().strip()
        persona_counts = persona_sets.get(row["company_clean"], {})
        mkt, conf, reason = _infer_company_category(company_lower, persona_counts)
        suggestions.append(mkt)
        confidences.append(conf)
        reasons.append(reason)

    agg["suggested_market"]     = suggestions
    agg["suggested_confidence"] = confidences
    agg["suggested_reason"]     = reasons
    agg["manual_market"]        = ""
    agg["manual_category"]      = ""
    agg["manual_notes"]         = ""

    # ── 5. Save outputs ───────────────────────────────────────────────────────

    override_cols = [
        "company_clean", "connection_count",
        "recruiter_count", "talent_count", "hiring_manager_count",
        "data_leader_count", "avg_priority_score",
        "suggested_market", "suggested_confidence", "suggested_reason",
        "manual_market", "manual_category", "manual_notes",
    ]

    # 5a. Full backlog
    backlog_path = OUTPUTS_DIR / "unknown_resolution_backlog.csv"
    agg.to_csv(backlog_path, index=False, encoding="utf-8-sig")
    logger.info(f"  Unknown backlog saved: {backlog_path.name} ({len(agg)} companies)")

    # 5b. High-confidence auto-suggestions
    auto_mask = agg["suggested_confidence"].isin(["HIGH", "MEDIUM"])
    auto_sugg = agg[auto_mask].copy()
    auto_path = OUTPUTS_DIR / "unknown_company_auto_suggestions.csv"
    auto_sugg.to_csv(auto_path, index=False, encoding="utf-8-sig")
    auto_count = int(auto_sugg["connection_count"].sum())
    logger.info(f"  Auto-suggestions: {len(auto_sugg)} companies / {auto_count:,} connections resolvable")

    # 5c. Top 150 for manual override with proper company name rows
    top150 = agg[override_cols].head(150).copy()
    top150["company_clean"] = top150["company_clean"].astype(str)
    top150["connection_count"] = pd.to_numeric(top150["connection_count"], errors="coerce").fillna(0).astype(int)
    top150["avg_priority_score"] = pd.to_numeric(top150["avg_priority_score"], errors="coerce").fillna(0).round(1)
    override_path = OUTPUTS_DIR / "company_override_candidates.csv"
    top150.to_csv(override_path, index=False, encoding="utf-8-sig")
    logger.info(f"  Override candidates saved: {override_path.name} ({len(top150)} rows)")

    # 5d. High-value UNKNOWN individuals (alias + legacy path)
    hv_mask = (
        unk_mask &
        (df["priority_score"] >= 60) &
        df["persona"].isin({
            "Recruiter", "Talent Acquisition", "Sourcer",
            "Hiring Manager", "Engineering Manager",
            "Data Engineering Manager", "Head of Data", "Director",
        })
    )
    hv_base_cols = [
        "full_name", "company_clean", "position_clean",
        "persona", "area", "seniority", "priority_score",
        mkt_col, "url",
    ]
    hv_cols = [c for c in hv_base_cols if c in df.columns]
    hv_df   = df[hv_mask][hv_cols].sort_values("priority_score", ascending=False)
    for hv_path in [
        OUTPUTS_DIR / "unknown_high_value_contacts.csv",
        OUTPUTS_DIR / "unresolved_high_value_contacts.csv",
    ]:
        hv_df.to_csv(hv_path, index=False, encoding="utf-8-sig")
    logger.info(f"  High-value UNKNOWN contacts: {len(hv_df):,}")

    # 5e. V4 outputs (if V4 columns are available)
    v4_available = "market_v4" in df.columns
    if v4_available:
        try:
            from src.market_inference_v4 import (
                build_unknown_by_reason, build_unknown_reduction_simulation,
                export_market_v4_audit,
            )
            export_market_v4_audit(df)
            build_unknown_by_reason(df)
            build_unknown_reduction_simulation(df)
        except Exception as exc:
            logger.warning(f"  V4 audit export failed (non-fatal): {exc}")

    # ── 6. Summary stats for dashboard ───────────────────────────────────────
    top25_coverage = int(agg.head(25)["connection_count"].sum())
    top50_coverage = int(agg.head(50)["connection_count"].sum())

    auto_by_market = auto_sugg.groupby("suggested_market")["connection_count"].sum().to_dict()

    # Build top25_companies as clean list of dicts (no concatenation bugs)
    top25_rows = []
    for _, r in agg.head(25).iterrows():
        top25_rows.append({
            "company_clean":       str(r["company_clean"]),
            "connection_count":    int(r["connection_count"]),
            "recruiter_count":     int(r.get("recruiter_count", 0)),
            "talent_count":        int(r.get("talent_count", 0)),
            "hiring_manager_count":int(r.get("hiring_manager_count", 0)),
            "data_leader_count":   int(r.get("data_leader_count", 0)),
            "avg_priority_score":  round(float(r.get("avg_priority_score", 0)), 1),
            "suggested_market":    str(r.get("suggested_market", "UNKNOWN")),
            "suggested_confidence":str(r.get("suggested_confidence", "")),
            "suggested_reason":    str(r.get("suggested_reason", "")),
        })

    return {
        "total_unknown_companies":   int(len(agg)),
        "total_unknown_contacts":    int(len(unk_df)),
        "auto_resolvable_companies": int(len(auto_sugg)),
        "auto_resolvable_contacts":  auto_count,
        "top25_coverage":            top25_coverage,
        "top50_coverage":            top50_coverage,
        "top25_pct_of_unknown":      round(top25_coverage / len(unk_df) * 100, 1) if len(unk_df) > 0 else 0,
        "high_value_unknown_contacts": int(len(hv_df)),
        "auto_by_market":            auto_by_market,
        "top25_companies":           top25_rows,
    }
