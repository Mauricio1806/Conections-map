# -*- coding: utf-8 -*-
"""
daily_outreach.py — Daily Outreach Command Center
==================================================
One operational view answering, every morning:

    Who should I contact today, why, what should I say, and when should I
    follow up?

Design rules (see the 2026-11-15 refresh spec):

* NO new CRM and NO new scoring engine. Every row is derived from data the
  pipeline has ALREADY produced and sanitized into the public payload:
    - Lead Reactivation V8 (+ recency-aware triage)   -> reply / active / follow-up / reactivation
    - Untapped Network V9/V10 (outreach + activation) -> first outreach / long-connected / Spain-EU
    - USD Contract CRM (USD-fit, location blockers)   -> USD-fit enrichment by profile URL
    - Monthly Executive Queue + Opportunity History   -> monthly-queue / November inbound tags
  Only short derived fields go into the public page — the large source arrays
  stay in their own page files.

* Manual activity comes ONLY from the private, gitignored
  data/manual/recruiter_outreach_log.csv (existing schema, message_type
  vocabulary documented in MANUAL_ACTION_TYPES). `notes_private` is never
  read into any output. Nothing is ever marked completed unless it is in
  that log — a recommended action is never a completed action.

* Business priority, not network score: an active CV/interview step outranks
  any first outreach; stale/historical conversations never become urgent;
  rejected / location-blocked / local-only / courtesy-only contacts never
  enter the action queues.

Called from generate_static_dashboard.py (adds payload["daily_outreach"]
before the page split). Can also be run standalone for a console summary:

    python src/daily_outreach.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

MANUAL_OUTREACH_LOG = ROOT / "data" / "manual" / "recruiter_outreach_log.csv"
MANUAL_USD_PIPELINE = ROOT / "data" / "manual" / "usd_pipeline.csv"
PUBLIC_JSON = ROOT / "outputs" / "public_dashboard_data.json"

# Existing recruiter_outreach_log.csv schema (src/usd_contract_crm.py
# OUTREACH_COLUMNS) — reused unchanged, so the file stays backward compatible.
OUTREACH_LOG_COLUMNS = [
    "date", "contact_name", "profile_url", "company", "source",
    "opportunity_bucket", "message_type", "status", "last_reply_date",
    "next_action", "next_action_date", "usd_signal", "latam_signal",
    "timezone_signal", "notes_private",
]

# message_type vocabulary for Daily Outreach tracking. Older free-text values
# are mapped by keyword in _normalize_action_type(); unknown values are kept
# as OTHER (counted as "logged activity", never as a specific milestone).
MANUAL_ACTION_TYPES = {
    "FIRST_MESSAGE":         "First message sent",
    "FOLLOW_UP":             "Follow-up sent",
    "RECRUITER_REPLIED":     "Recruiter replied",
    "MEANINGFUL_REPLY":      "Meaningful reply",
    "CV_REQUESTED":          "CV requested",
    "CV_SUBMITTED":          "CV submitted",
    "INTERVIEW_SCHEDULED":   "Interview scheduled",
    "INTERVIEW_COMPLETED":   "Interview completed",
    "OPPORTUNITY_CONFIRMED": "Opportunity confirmed",
    "OFFER":                 "Offer",
    "CONTRACT":              "Contract signed",
    "CLOSED_REJECTED":       "Closed / rejected",
    "CONNECTION_SENT":       "Connection request sent",
    "OTHER":                 "Other logged activity",
}

_ACTION_KEYWORDS = [
    ("CONTRACT", ("contract", "closed_won", "signed")),
    ("OFFER", ("offer",)),
    ("OPPORTUNITY_CONFIRMED", ("confirmed", "vacancy", "opening")),
    ("INTERVIEW_COMPLETED", ("interview_done", "interview_completed", "interview completed")),
    ("INTERVIEW_SCHEDULED", ("interview", "call_scheduled", "scheduled_call")),
    ("CV_SUBMITTED", ("cv_sent", "cv_submitted", "cv sent", "cv submitted", "resume sent")),
    ("CV_REQUESTED", ("cv_requested", "cv requested")),
    ("CLOSED_REJECTED", ("reject", "closed", "lost")),
    ("MEANINGFUL_REPLY", ("meaningful",)),
    ("RECRUITER_REPLIED", ("replied", "reply")),
    ("FOLLOW_UP", ("follow",)),
    ("FIRST_MESSAGE", ("first", "initial", "outreach", "dm", "message")),
    ("CONNECTION_SENT", ("connection", "invite")),
]

# ── Queues ─────────────────────────────────────────────────────────────────
# Order = priority when one contact qualifies for several queues (the contact
# appears ONCE, under its highest queue; secondary memberships are kept in
# row["q"] so every queue filter still finds it).
QUEUES = [
    ("REPLY_NOW",       "Reply Now"),
    ("ACTIVE_PROCESS",  "Active Interview / CV / Client Process"),
    ("USD_FOLLOWUP",    "USD Recruiter Follow-ups"),
    ("FIRST_OUTREACH",  "First Outreach — Never Contacted"),
    ("LONG_CONNECTED",  "Long-Connected Recruiters"),
    ("REACTIVATION",    "Reactivation Due"),
    ("SPAIN_EU",        "Spain/EU Future Pipeline"),
]
QUEUE_ORDER = {q: i for i, (q, _) in enumerate(QUEUES)}
QUEUE_LABEL = dict(QUEUES)

# Category caps for TODAY'S TOP 15 — keeps the list a realistic day of work
# (no 15 cold messages while a CV request waits; no exploratory EU at the top).
TOP_N = 15
TOP_CAPS = {
    "REPLY_NOW": 15, "ACTIVE_PROCESS": 6, "USD_FOLLOWUP": 3,
    "FIRST_OUTREACH": 4, "LONG_CONNECTED": 2, "REACTIVATION": 1, "SPAIN_EU": 0,
}

RECRUITING_PERSONAS = {"Recruiter", "Talent Acquisition", "Sourcer"}
HIRING_PERSONAS = {"Hiring Manager", "Engineering Manager"}
DATA_LEADER_PERSONAS = {"Head of Data", "Data Engineering Manager", "Director"}
FIRST_OUTREACH_PERSONAS = RECRUITING_PERSONAS | HIRING_PERSONAS | DATA_LEADER_PERSONAS | {"HR"}

ACTIVE_STATES = {"INTERVIEW_PIPELINE", "CV_REQUESTED", "ACTIVE_OPPORTUNITY", "AWAITING_RECRUITER_UPDATE"}
ACTIVE_BASE = {"INTERVIEW_PIPELINE": 78, "CV_REQUESTED": 75, "ACTIVE_OPPORTUNITY": 70, "AWAITING_RECRUITER_UPDATE": 62}
FRESH_BANDS = {"CURRENT", "RECENT"}
ACTIVE_MAX_DAYS = 45

BLOCKED_PROCESS_STATES = {
    "WORK_AUTHORIZATION_BLOCKED", "LOCATION_ELIGIBILITY_BLOCKED",
    "GEOGRAPHIC_HIRING_RESTRICTION", "REJECTED_CLOSED", "LOW_VALUE",
    "TALENT_POOL_REDIRECT", "CAREER_SITE_REDIRECT",
}
BLOCKING_FLAGS = (
    "hard_rejection_flag", "terminal_state_flag", "terminal_low_action_flag",
    "useless_for_usd_remote_flag", "onsite_or_local_only_flag",
    "presencial_only_flag", "hybrid_local_only_flag", "country_restriction_flag",
    "mexico_local_only_flag", "talent_pool_only_flag", "courtesy_only_flag",
    "low_value_reply_flag", "ghost_or_vacuum_flag",
)
BLOCKED_SEGMENTS = {
    "LOW_PRIORITY_COURTESY", "LOW_PRIORITY_LOCAL_ONLY", "LOW_FIT_LOCATION_BLOCKED",
    "CLOSED_NO_ACTION", "TALENT_POOL_LOW_ACTION", "NO_RESPONSE_BACKLOG",
    "SOFT_CLOSED_KEEP_WARM",
}

USD_MARKET_HINTS = ("LATAM", "USD", "US_CANADA", "US/CANADA", "GLOBAL_STAFFING", "GLOBAL STAFFING",
                    "NEARSHORE", "GLOBAL_OPPORTUNITY", "GLOBAL OPPORTUNITY", "GLOBAL TECH", "GLOBAL_TECH")
SPAIN_EU_HINTS = ("SPAIN", "EUROPE", "EU_", "SPAIN/EU")
LOW_VALUE_MARKETS = {"LOW_VALUE_UNRESOLVED"}

SPANISH_HINT_RE = re.compile(
    r"\b(reclutador|reclutadora|reclutamiento|selecci[oó]n|talento|m[eé]xico|colombia|"
    r"argentina|chile|per[uú]|uruguay|espa[nñ]a|madrid|barcelona|bogot[aá]|monterrey|"
    r"guadalajara|recursos humanos)\b",
    re.IGNORECASE,
)


# ── Small helpers ──────────────────────────────────────────────────────────

def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    return str(v).strip()


def _b(v) -> bool:
    if isinstance(v, bool):
        return v
    return _s(v).lower() in {"true", "1", "yes", "y"}


def _i(v, default: int = 0) -> int:
    try:
        if v is None or _s(v) == "" or _s(v).lower() == "none":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _d(v) -> date | None:
    s = _s(v)[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_url(url) -> str:
    u = _s(url).lower()
    if not u:
        return ""
    u = u.split("?")[0].split("#")[0].rstrip("/")
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www\.|[a-z]{2}\.)linkedin\.com", "linkedin.com", u)
    return u


def _norm_text(s) -> str:
    return re.sub(r"\s+", " ", _s(s).lower())


def identity_key(url, name, company) -> str:
    """Reliable contact identity: profile URL first; name|company only as a
    fallback (generic employer placeholders never merge different people
    because the name is always part of the fallback key)."""
    nu = normalize_url(url)
    if nu:
        return "u:" + nu
    n, c = _norm_text(name), _norm_text(company)
    if not n:
        return ""
    return "n:" + n + "|" + c


def _short_id(key: str) -> str:
    # Letter prefix: a bare 10-hex-char id can be all digits, which the
    # public privacy scan (correctly) treats as a possible phone number.
    return "c" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def _first_name(name: str) -> str:
    parts = _s(name).split()
    return parts[0].title() if parts else "there"


def _is_usd_market(*vals) -> bool:
    txt = " ".join(_s(v).upper() for v in vals)
    return any(h in txt for h in USD_MARKET_HINTS)


def _is_spain_eu_market(*vals) -> bool:
    txt = " ".join(_s(v).upper() for v in vals)
    return any(h in txt for h in SPAIN_EU_HINTS)


def _language(market: str, company: str, role: str) -> str:
    m = _s(market).upper()
    if "SPANISH" in m or "SPAIN" in m:
        return "es"
    if SPANISH_HINT_RE.search(_s(company) + " " + _s(role)):
        return "es"
    return "en"


# ── Message templates (concise, persona-specific; never auto-sent) ─────────

_STACK = "Azure/AWS, Databricks, PySpark, SQL, dbt and Airflow"


def first_message(persona: str, name: str, company: str, lang: str, staffing: bool = False) -> str:
    fn = _first_name(name)
    co = _s(company)
    if lang == "es":
        if persona in RECRUITING_PERSONAS or persona == "HR" or staffing:
            return (f"Hola {fn}, gracias por la conexión. Soy Senior Data Engineer ({_STACK}) "
                    f"y busco proyectos remotos con equipos de EE. UU./LATAM en modalidad contractor (USD). "
                    f"Si en {co or 'tu equipo'} tienen búsquedas de Data Engineering, con gusto te comparto mi CV.")
        return (f"Hola {fn}, gracias por la conexión. Soy Senior Data Engineer ({_STACK}). "
                f"Me interesa mucho cómo trabaja el equipo de datos en {co or 'tu empresa'}; "
                f"si en algún momento suman perfiles remotos senior, me encantaría conversar.")
    if persona in RECRUITING_PERSONAS or persona == "HR" or staffing:
        return (f"Hi {fn}, thanks for connecting. I'm a Senior Data Engineer ({_STACK}) based in Brazil, "
                f"open to remote USD contract roles with U.S./LATAM teams. If {co or 'your team'} is hiring "
                f"for data engineering, I'd be glad to share my CV.")
    return (f"Hi {fn}, thanks for connecting. I'm a Senior Data Engineer ({_STACK}) working remotely "
            f"with international teams. I'd love to learn how the data team at {co or 'your company'} is "
            f"growing — and if you ever add senior remote engineers, I'd be happy to talk.")


def follow_up_message(name: str, lang: str, active: bool = False) -> str:
    fn = _first_name(name)
    if lang == "es":
        if active:
            return (f"Hola {fn}, ¿cómo va el proceso? Sigo muy interesado y disponible para los próximos pasos. "
                    f"Si necesitas algo más de mi parte (CV, disponibilidad, expectativa en USD), avísame.")
        return (f"Hola {fn}, retomo nuestra conversación: estoy disponible para proyectos remotos de "
                f"Senior Data Engineer en USD. ¿Tienes alguna búsqueda abierta donde mi perfil encaje?")
    if active:
        return (f"Hi {fn}, checking in on the process — I'm still very interested and available for next steps. "
                f"Let me know if you need anything else from me (CV, availability, USD rate).")
    return (f"Hi {fn}, following up on our last conversation — I'm currently available for remote Senior "
            f"Data Engineer contracts paid in USD. Do you have any open roles where my profile could fit?")


def reactivation_message(name: str, lang: str) -> str:
    fn = _first_name(name)
    if lang == "es":
        return (f"Hola {fn}, ¡tanto tiempo! Te escribo porque vuelvo a estar disponible para proyectos remotos "
                f"de Data Engineering ({_STACK}). Si surge algo en tu radar, me encantaría saberlo.")
    return (f"Hi {fn}, it's been a while! I'm available again for remote Data Engineering work "
            f"({_STACK}). If anything comes up on your radar, I'd love to hear about it.")


# ── Manual activity log (private) ──────────────────────────────────────────

def _normalize_action_type(raw: str) -> str:
    t = _s(raw).upper().replace(" ", "_").replace("-", "_")
    if t in MANUAL_ACTION_TYPES:
        return t
    low = _s(raw).lower()
    if not low:
        return "OTHER"
    for code, kws in _ACTION_KEYWORDS:
        if any(k in low for k in kws):
            return code
    return "OTHER"


def load_manual_activity(path: Path = MANUAL_OUTREACH_LOG) -> pd.DataFrame:
    """Reads the private outreach log. Returns only the columns Daily Outreach
    needs — notes_private is deliberately NOT selected. Blank template rows
    (no contact_name AND no profile_url) are dropped. Exact duplicate events
    (same contact identity + date + action type) are collapsed, so re-running
    or appending the same export twice never double-counts activity."""
    cols = ["date", "contact_name", "profile_url", "company", "message_type",
            "status", "last_reply_date", "next_action_date"]
    if not path.exists():
        return pd.DataFrame(columns=cols + ["action_type", "identity", "action_date"])
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(f"  daily_outreach: could not read manual log: {exc}")
        return pd.DataFrame(columns=cols + ["action_type", "identity", "action_date"])
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols].copy()
    has_id = (df["contact_name"].str.strip() != "") | (df["profile_url"].str.strip() != "")
    df = df[has_id].copy()
    if df.empty:
        return df.assign(action_type=[], identity=[], action_date=[])
    # message_type is the action; fall back to status for older rows.
    df["action_type"] = [
        _normalize_action_type(mt if _s(mt) else st)
        for mt, st in zip(df["message_type"], df["status"])
    ]
    df["identity"] = [identity_key(u, n, c) for u, n, c in zip(df["profile_url"], df["contact_name"], df["company"])]
    df["action_date"] = [(_d(x).isoformat() if _d(x) else "") for x in df["date"]]
    df = df.drop_duplicates(subset=["identity", "action_date", "action_type"]).reset_index(drop=True)
    return df


def _manual_by_identity(manual: pd.DataFrame) -> dict:
    """Latest logged action per identity (public-safe: type/date/next only)."""
    out: dict[str, dict] = {}
    if manual is None or manual.empty:
        return out
    m = manual.sort_values("action_date")
    for _, r in m.iterrows():
        key = r["identity"]
        if not key:
            continue
        prev = out.get(key, {"types": []})
        types = prev["types"] + ([r["action_type"]] if r["action_type"] not in prev["types"] else [])
        out[key] = {
            "type": r["action_type"],
            "label": MANUAL_ACTION_TYPES.get(r["action_type"], "Other logged activity"),
            "date": r["action_date"],
            "next": (_d(r["next_action_date"]).isoformat() if _d(r["next_action_date"]) else ""),
            "types": types,
        }
    return out


# ── Row builders ───────────────────────────────────────────────────────────

def _lead_blocked(r: dict) -> str:
    """Returns a short blocker reason, or '' when the lead is actionable."""
    # A soft close ("no role now, keep you on radar") is terminal only during
    # its cooldown; once Lead Reactivation marks it REACTIVATION_ELIGIBLE it
    # is exactly a reactivation candidate — not a blocked contact.
    soft_close_eligible = (_s(r.get("process_state")) == "SOFT_CLOSED_KEEP_WARM"
                           and _s(r.get("cooldown_state")) == "REACTIVATION_ELIGIBLE")
    for f in BLOCKING_FLAGS:
        if soft_close_eligible and f in ("terminal_state_flag", "terminal_low_action_flag"):
            continue
        if _b(r.get(f)):
            return f.replace("_flag", "").replace("_", " ")
    if _s(r.get("process_state")) in BLOCKED_PROCESS_STATES:
        return _s(r.get("process_state")).replace("_", " ").lower()
    if _s(r.get("sourcing_quality_segment")) == "LOW_QUALITY_LOCAL_ONLY":
        return "local-only role"
    if _s(r.get("lead_disqualification_reason")):
        return _s(r.get("lead_disqualification_reason"))
    return ""


def _crm_index(usd_crm: dict) -> dict:
    """profile-URL -> USD CRM enrichment (bucket, USD fit segment, score)."""
    idx: dict[str, dict] = {}
    for key in ("auto_suggested_usd_leads", "recruiter_pipeline", "follow_up_queue",
                "first_outreach_queue", "active_process_pipeline"):
        for r in (usd_crm.get(key) or []):
            u = normalize_url(r.get("profile_url"))
            if not u:
                continue
            cur = idx.setdefault(u, {})
            if r.get("opportunity_bucket") and not cur.get("bucket"):
                cur["bucket"] = _s(r.get("opportunity_bucket"))
            seg = _s(r.get("sourcing_quality_segment"))
            if seg and not cur.get("seg"):
                cur["seg"] = seg
            cur["crm_score"] = max(cur.get("crm_score", 0), _i(r.get("score")))
            if _b(r.get("useless_for_usd_remote_flag")) or _b(r.get("mexico_local_only_flag")):
                cur["blocked"] = True
            cur.setdefault("sources", set()).add(key)
    disq = usd_crm.get("disqualified_local_only") or []
    for r in disq:
        u = normalize_url(r.get("profile_url"))
        if u:
            idx.setdefault(u, {})["blocked"] = True
    return idx


def _monthly_index(usd_crm: dict, as_of: date) -> tuple[dict, dict]:
    mq = (usd_crm.get("monthly_executive_queue") or {})
    by_url: dict[str, list] = defaultdict(list)
    for r in (mq.get("all_monthly_queue_records") or []):
        u = normalize_url(r.get("profile_url"))
        if u:
            by_url[u].append(_s(r.get("queue_name")))
    inbound: dict[str, dict] = {}
    month = as_of.strftime("%Y-%m")
    oh = (usd_crm.get("opportunity_history") or {})
    for r in (oh.get("inbound_opportunities") or []):
        if _s(r.get("event_month")) != month:
            continue
        if _b(r.get("location_or_eligibility_blocked")) or _b(r.get("rejected_or_closed")):
            continue
        u = normalize_url(r.get("profile_url"))
        if u:
            inbound[u] = r
    return dict(by_url), inbound


def _base_row(key, name, company, role, persona, market, url) -> dict:
    return {
        "id": _short_id(key), "_key": key,
        "name": _s(name), "company": _s(company), "role": _s(role)[:90],
        "persona": _s(persona) or "Other", "market": _s(market).replace("_", " ") or "Unclassified",
        "url": _s(url),
        "q": [], "tags": [], "src": [],
        "score": 0, "cat": "", "status": "", "why": "", "last": "", "last_label": "",
        "next": "", "fu": "", "msg": "", "lang": "en", "usd": False, "ev": "inferred",
        "days_conn": None,
    }


def _lead_rows(lr: dict, crm_idx: dict, monthly: dict, inbound: dict, as_of: date) -> tuple[list, Counter]:
    rows = []
    excluded = Counter()
    for r in (lr.get("top_reactivation_contacts") or []):
        name = r.get("other_person_name")
        url = r.get("other_person_profile_url")
        key = identity_key(url, name, r.get("company_clean"))
        if not key:
            excluded["no_identity"] += 1
            continue
        nu = normalize_url(url)
        crm = crm_idx.get(nu, {})
        state = _s(r.get("process_state"))
        seg = _s(r.get("response_queue_segment"))
        band = _s(r.get("conversation_recency_band"))
        days = _i(r.get("days_since_last_message"), 9999)
        timing = _s(r.get("recommended_response_timing"))
        persona = _s(r.get("persona"))
        bucket = crm.get("bucket") or _s(r.get("strategic_market"))
        usd_fit = (_s(r.get("sourcing_quality_segment")) == "HIGH_FIT_USD_REMOTE"
                   or crm.get("seg") == "HIGH_FIT_USD_REMOTE" or _b(r.get("usd_latam_signal_flag")))
        blocker = _lead_blocked(r) or ("local-only / not eligible (CRM)" if crm.get("blocked") else "")
        intent = _s(r.get("sanitized_intent_label"))
        last_sender = _s(r.get("last_sender_type"))
        lang = _language(bucket, r.get("company_clean"), r.get("position_clean"))

        queues: list[str] = []
        if blocker:
            excluded["blocked:" + blocker] += 1
            continue

        fresh_active = state in ACTIVE_STATES and (band in FRESH_BANDS or days <= ACTIVE_MAX_DAYS)
        if timing == "TODAY":
            queues.append("REPLY_NOW")
        if fresh_active:
            queues.append("ACTIVE_PROCESS")
        if seg == "HIGH_VALUE_RECRUITER_REPLY" and (usd_fit or _is_usd_market(bucket)) and not fresh_active:
            queues.append("USD_FOLLOWUP")
        if seg == "REACTIVATION_DUE_HIGH_VALUE":
            queues.append("REACTIVATION")
        if _is_spain_eu_market(bucket) and (seg in ("HIGH_VALUE_RECRUITER_REPLY", "REACTIVATION_DUE_HIGH_VALUE")):
            queues.append("SPAIN_EU")
        if not queues:
            excluded["no_action_queue:" + (seg or "none")] += 1
            continue
        # Spain/EU leads are exploratory (90/10 intent): unless a live reply or
        # process step exists, they stay ONLY in the small Spain/EU queue and
        # never take a USD follow-up / reactivation slot.
        if "SPAIN_EU" in queues and not ({"REPLY_NOW", "ACTIVE_PROCESS"} & set(queues)):
            queues = ["SPAIN_EU"]

        row = _base_row(key, name, r.get("company_clean"), r.get("position_clean"), persona, bucket, url)
        row["q"] = sorted(set(queues), key=QUEUE_ORDER.get)
        row["cat"] = row["q"][0]
        row["src"] = ["Lead Reactivation"] + (["USD CRM"] if crm else [])
        row["usd"] = bool(usd_fit)
        row["lang"] = lang
        row["last"] = _s(r.get("last_message_date"))
        row["last_label"] = "Last message"
        row["state"] = state
        row["intent"] = intent
        nad = _d(r.get("next_action_date"))
        rps = _i(r.get("response_priority_score"))
        angle = _s(r.get("message_angle"))

        cat = row["cat"]
        if cat == "REPLY_NOW":
            row["score"] = min(100, 92 + max(0, (rps - 60) // 5))
            row["status"] = "Awaiting MY reply — " + (intent or "open question")
            row["why"] = _s(r.get("response_reason_short")) or _s(r.get("reply_reason_short")) or "They were the last sender."
            row["next"] = _s(r.get("recommended_next_action")) or "Reply today"
            row["fu"] = as_of.isoformat()
            row["msg"] = angle
            row["tags"].append("urgent")
        elif cat == "ACTIVE_PROCESS":
            s = ACTIVE_BASE.get(state, 62)
            if last_sender == "other":
                s += 8   # the recruiter is waiting on ME — real action today
            if usd_fit:
                s += 5
            if band == "CURRENT":
                s += 2
            if days > 21:
                s -= 4
            if intent in ("Generic acknowledgement", "General message"):
                s -= 12
            row["_tb"] = (rps, -days)
            who = "they wrote last — reply" if last_sender == "other" else "I wrote last — awaiting their update"
            row["status"] = state.replace("_", " ").title() + f" ({who})"
            row["why"] = (f"{intent or 'Active process'} {days}d ago"
                          + ("; high USD remote fit" if usd_fit else "")
                          + ("; recruiter/TA" if persona in RECRUITING_PERSONAS else ""))
            if last_sender == "other":
                row["next"] = "Reply and move the process forward (CV / availability / call slot)"
                row["fu"] = as_of.isoformat()
                row["msg"] = angle or follow_up_message(name, lang, active=True)
            else:
                row["next"] = _s(r.get("recommended_next_action")) or "Polite check-in on next steps"
                last_d = _d(r.get("last_message_date"))
                due = (last_d + timedelta(days=5)) if last_d else as_of
                if nad and nad > due:
                    due = nad
                row["fu"] = due.isoformat()
                row["msg"] = follow_up_message(name, lang, active=True)
                if due > as_of:
                    s -= 10  # check-in not due yet — keep visible, not top-of-day
            row["score"] = int(max(50, min(89, s)))
        elif cat == "USD_FOLLOWUP":
            s = 50 + max(0, rps - 60) * 0.4 + (5 if usd_fit else 0) + (3 if persona in RECRUITING_PERSONAS else 0)
            if band in ("HISTORICAL",):
                s -= 6
            row["score"] = int(max(40, min(72, s)))
            row["_tb"] = (rps, -days)
            lc = _s(r.get("lead_category")).replace("—", "-")
            if state in ACTIVE_STATES or lc in ("Active Interview Pipeline", "Awaiting Recruiter Update"):
                row["status"] = f"Earlier process went quiet ({days}d ago) - follow up"
            else:
                row["status"] = (lc or "Warm recruiter") + (f" ({days}d since last message)" if days < 9999 else "")
            row["why"] = ("Valuable recruiter relationship with USD/remote fit"
                          + (f"; last signal: {intent.lower()}" if intent else "")
                          + (f"; last message {days}d ago" if days < 9999 else ""))
            row["next"] = "Send a short availability follow-up"
            row["fu"] = (nad or as_of).isoformat()
            row["msg"] = follow_up_message(name, lang)
        elif cat == "REACTIVATION":
            s = 45 + max(0, rps - 60) * 0.3 + (5 if usd_fit else 0)
            row["score"] = int(max(35, min(65, s)))
            row["status"] = "Reactivation due"
            row["why"] = "Past high-value conversation; reactivation window reached"
            row["next"] = "Reactivate with updated availability"
            row["fu"] = (nad or as_of).isoformat()
            row["msg"] = reactivation_message(name, lang)
        else:  # SPAIN_EU
            row["score"] = int(max(20, min(40, 25 + max(0, rps - 60) * 0.2)))
            row["status"] = "Spain/EU relationship"
            row["why"] = "Exploratory Spain/EU 2027 positioning"
            row["next"] = "Light-touch keep-warm message"
            row["fu"] = (nad or as_of).isoformat()
            row["msg"] = reactivation_message(name, lang)

        # Monthly queue / November inbound context
        mq = monthly.get(nu, [])
        if mq:
            row["src"].append("Monthly Queue: " + ", ".join(sorted(set(mq))))
            row["score"] = min(100 if cat == "REPLY_NOW" else 89, row["score"] + 2)
        if nu in inbound:
            row["src"].append("Nov inbound opportunity")
            row["tags"].append("inbound_month")

        # KPI tags (only on fresh / actionable rows)
        if "ACTIVE_PROCESS" in row["q"] or "REPLY_NOW" in row["q"]:
            if state == "INTERVIEW_PIPELINE" or intent == "Interview scheduling":
                row["tags"].append("interview")
            if state == "CV_REQUESTED" or intent in ("Asked for CV", "Asked for availability"):
                row["tags"].append("cv_salary_call")
            ih = inbound.get(nu)
            if ih and (_b(ih.get("salary_expectation_requested")) or _b(ih.get("interview_or_call_requested"))
                       or _b(ih.get("cv_requested"))) and "cv_salary_call" not in row["tags"]:
                row["tags"].append("cv_salary_call")
        if row["usd"]:
            row["tags"].append("high_fit_usd")
        rows.append(row)
    return rows, excluded


def _untapped_rows(un: dict, crm_idx: dict, as_of: date) -> tuple[list, Counter]:
    rows = []
    excluded = Counter()
    for r in (un.get("top_untapped_contacts") or []):
        if _s(r.get("contact_history_status")) != "NEVER_CONTACTED_CONFIRMED":
            excluded["not_confirmed_never_contacted"] += 1
            continue
        name, company, url = r.get("full_name"), r.get("company_clean"), r.get("profile_url")
        key = identity_key(url, name, company)
        if not key:
            excluded["no_identity"] += 1
            continue
        persona = _s(r.get("persona"))
        market = _s(r.get("untapped_opportunity_market"))
        bucket = _s(r.get("opportunity_bucket"))
        focus = _s(r.get("strategic_focus"))
        exe = _i(r.get("untapped_execution_score"))
        act = _i(r.get("untapped_activation_potential_score"))
        out = _i(r.get("untapped_outreach_score"))
        fmp = _s(r.get("first_message_priority"))
        days_conn = _i(r.get("days_connected"), 0)
        crm = crm_idx.get(normalize_url(url), {})
        if crm.get("blocked"):
            excluded["blocked_local_only"] += 1
            continue
        staffing = "STAFFING" in bucket.upper() or "staffing" in market.lower()
        usd_market = _is_usd_market(market, bucket, focus) and focus != "SPAIN_EU_EXPLORATORY"
        spain = focus == "SPAIN_EU_EXPLORATORY" or _is_spain_eu_market(market, bucket)
        low_value = market in LOW_VALUE_MARKETS or bucket in LOW_VALUE_MARKETS

        target_persona = persona in FIRST_OUTREACH_PERSONAS
        strong_signal = exe >= 70 and fmp in ("TODAY", "THIS_WEEK") and (usd_market or staffing)
        if not (target_persona or strong_signal):
            excluded["not_target_persona_or_signal"] += 1
            continue
        if persona == "HR" and not (usd_market or staffing) and exe < 70:
            excluded["hr_without_usd_signal"] += 1
            continue

        queues = []
        if spain:
            queues.append("SPAIN_EU")
        else:
            queues.append("FIRST_OUTREACH")
            if persona in RECRUITING_PERSONAS and days_conn >= 180:
                queues.append("LONG_CONNECTED")

        row = _base_row(key, name, company, r.get("position_clean"), persona, market or bucket, url)
        row["q"] = sorted(set(queues), key=QUEUE_ORDER.get)
        row["cat"] = "LONG_CONNECTED" if "LONG_CONNECTED" in row["q"] and exe >= 70 else row["q"][0]
        row["src"] = ["Untapped V10"] + (["USD CRM"] if crm else [])
        row["days_conn"] = days_conn
        row["last"] = _s(r.get("connected_on"))
        row["last_label"] = "Connected on (never messaged)"
        row["state"] = "NEVER_CONTACTED"
        lang = _language(market, company, r.get("position_clean"))
        row["lang"] = lang
        usd_fit = crm.get("seg") == "HIGH_FIT_USD_REMOTE"
        row["usd"] = usd_fit

        if spain:
            row["score"] = int(max(15, min(40, 15 + exe * 0.25)))
            row["status"] = "Never contacted — Spain/EU exploratory"
            row["next"] = "Optional exploratory first message (2027 positioning)"
        else:
            s = 30 + exe * 0.33
            if persona in RECRUITING_PERSONAS:
                s += 3
            if usd_market or staffing:
                s += 3
            if usd_fit:
                s += 2
            if days_conn >= 180 and persona in RECRUITING_PERSONAS:
                s += 1
            if low_value:
                s -= 15
            row["score"] = int(max(10, min(68, s)))
            row["_tb"] = (out, act, 1 if usd_fit else 0)
            row["status"] = "Never contacted" + (f" — connected {days_conn}d" if days_conn else "")
            row["next"] = (_s(r.get("recommended_first_action")) or "FIRST_MESSAGE").replace("_", " ").title()
        reason = _s(r.get("untapped_reason")) or _s(r.get("activation_reason"))
        reason = re.sub(r"^(High-value first outreach|Very high activation potential)\s*:\s*", "", reason)
        if len(reason) > 110:
            reason = reason[:107].rsplit(" ", 1)[0] + "…"
        extra = []
        if days_conn >= 180 and persona in RECRUITING_PERSONAS:
            extra.append(f"connected {days_conn}d, never messaged")
        if usd_fit:
            extra.append("high USD remote fit")
        row["why"] = "; ".join([reason] + extra) + f" · V10 exec {exe}/act {act}/outreach {out}"
        row["fu"] = ""  # set to send-date + 7 when a first message is logged
        row["fu_rule"] = "7 days after sending"
        row["msg"] = first_message(persona, name, company, lang, staffing=staffing)

        if persona == "Recruiter":
            row["tags"].append("nc_recruiter")
        if persona == "Talent Acquisition":
            row["tags"].append("nc_ta")
        if persona in RECRUITING_PERSONAS and days_conn >= 180:
            row["tags"].append("long_connected")
        if usd_fit:
            row["tags"].append("high_fit_usd")
        rows.append(row)
    return rows, excluded


def _merge_rows(rows: list[dict]) -> list[dict]:
    """One row per contact identity. The row from the highest-priority queue
    wins; queue memberships, tags and sources are unioned so every filter
    still finds the contact (deduplicated by identity + action context)."""
    merged: dict[str, dict] = {}
    for r in rows:
        k = r["_key"]
        cur = merged.get(k)
        if cur is None:
            merged[k] = r
            continue
        a, b = (cur, r) if QUEUE_ORDER[cur["cat"]] <= QUEUE_ORDER[r["cat"]] else (r, cur)
        a["q"] = sorted(set(a["q"]) | set(b["q"]), key=QUEUE_ORDER.get)
        # A contact with conversation history is no longer "never contacted":
        # drop untapped-only tags when merged with a lead row.
        if b.get("state") == "NEVER_CONTACTED" and a.get("state") != "NEVER_CONTACTED":
            a["q"] = [q for q in a["q"] if q not in ("FIRST_OUTREACH", "LONG_CONNECTED")]
            b_tags = [t for t in b["tags"] if t not in ("nc_recruiter", "nc_ta", "long_connected")]
        else:
            b_tags = b["tags"]
        a["tags"] = sorted(set(a["tags"]) | set(b_tags))
        a["src"] = list(dict.fromkeys(a["src"] + b["src"]))
        a["usd"] = a["usd"] or b["usd"]
        merged[k] = a
    return list(merged.values())


def _apply_manual(rows: list[dict], manual_idx: dict, as_of: date) -> None:
    """Overlay manually-logged activity. Only the private log can mark an
    action as done; a never-contacted contact with a logged FIRST_MESSAGE
    leaves First Outreach and becomes a follow-up (not a fabricated reply)."""
    for r in rows:
        m = manual_idx.get(r["_key"])
        if not m:
            continue
        r["ev"] = "manual"
        r["manual"] = {"type": m["type"], "label": m["label"], "date": m["date"], "next": m["next"]}
        types = set(m["types"])
        if "CLOSED_REJECTED" in types and m["type"] == "CLOSED_REJECTED":
            r["q"] = []
            r["closed"] = True
            continue
        if r.get("state") == "NEVER_CONTACTED" and types & {"FIRST_MESSAGE", "FOLLOW_UP"}:
            r["q"] = [q for q in r["q"] if q not in ("FIRST_OUTREACH", "LONG_CONNECTED")] + ["USD_FOLLOWUP"]
            r["q"] = sorted(set(r["q"]), key=QUEUE_ORDER.get)
            r["cat"] = r["q"][0]
            r["tags"] = [t for t in r["tags"] if t not in ("nc_recruiter", "nc_ta", "long_connected")]
            sent = _d(m["date"]) or as_of
            r["status"] = f"First message sent {m['date']} (manual log) — awaiting reply"
            r["next"] = "Follow up if no reply"
            r["msg"] = follow_up_message(r["name"], r["lang"])
            r["fu"] = (m["next"] or (sent + timedelta(days=7)).isoformat())
            r["last"] = m["date"]
            r["last_label"] = "Manual log"
        elif m["next"]:
            r["fu"] = m["next"]
        if types & {"OPPORTUNITY_CONFIRMED", "OFFER", "CONTRACT"}:
            r["tags"] = sorted(set(r["tags"]) | {"confirmed"})


def _rank_key(r: dict):
    tb = r.get("_tb") or ()
    return (-r["score"], QUEUE_ORDER[r["cat"]], tuple(-x for x in tb), r["name"].lower())


def _select_top(rows: list[dict], n: int = TOP_N) -> list[dict]:
    caps = dict(TOP_CAPS)
    ranked = sorted(
        [r for r in rows if r["q"] and r["cat"] in caps],
        key=_rank_key,
    )
    top, seen = [], set()
    counts = Counter()
    for r in ranked:
        if len(top) >= n:
            break
        if r["_key"] in seen or counts[r["cat"]] >= caps.get(r["cat"], 0):
            continue
        top.append(r)
        seen.add(r["_key"])
        counts[r["cat"]] += 1
    return top


def _daily_plan(rows: list[dict], as_of: date) -> dict:
    """Sustainable daily workload — PROPOSED targets, adjusted to the actual
    urgency of the pipeline. Never reported as completed activity."""
    urgent = sum(1 for r in rows if "REPLY_NOW" in r["q"])
    active_reply = sum(1 for r in rows if r["cat"] == "ACTIVE_PROCESS" and "they wrote last" in r["status"])
    active_wait = sum(1 for r in rows if r["cat"] == "ACTIVE_PROCESS" and "they wrote last" not in r["status"])
    due_fu = sum(1 for r in rows if r["cat"] == "USD_FOLLOWUP" and r["fu"] and (_d(r["fu"]) or as_of) <= as_of)
    first_pool = sum(1 for r in rows if r["cat"] in ("FIRST_OUTREACH", "LONG_CONNECTED") and r["score"] >= 50)

    active_target = max(2, min(5, active_reply + (1 if active_wait else 0)))
    heavy = urgent + active_reply > 5
    first_target = 3 if heavy else 5
    fu_target = 3 if due_fu >= 3 else due_fu
    conn_target = 0 if heavy else 3
    items = [
        {"label": "Reply to every urgent message", "target": urgent,
         "why": "Confirmed reply obligations from recent conversations (recency-aware triage)."},
        {"label": "Active opportunity actions", "target": active_target,
         "why": f"{active_reply} active processes where they wrote last; {active_wait} awaiting their update (check in after ~5 days)."},
        {"label": "High-value USD follow-ups", "target": fu_target,
         "why": f"{due_fu} USD-fit recruiter follow-ups are due."},
        {"label": "First outreach to qualified recruiters/TA", "target": first_target,
         "why": (f"{first_pool} never-contacted contacts score ≥50. "
                 + ("Reduced to 3 because open replies/process steps need attention first." if heavy else "Default daily mix."))},
        {"label": "New targeted connection requests", "target": conn_target,
         "why": ("Paused today — clear replies and process steps first." if heavy
                 else "Only U.S./LATAM technical recruiters or nearshore staffing companies.")},
    ]
    return {"items": items, "total_target": sum(i["target"] for i in items),
            "heavy_day": heavy, "note": "Proposed targets — not completed activity."}


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _weekly_performance(manual: pd.DataFrame, oh: dict, weekly_history: list, as_of: date, weeks: int = 8) -> dict:
    """Aggregate weekly outreach performance. Manual metrics come only from
    the private log; a week before the first logged entry is 'Not measured'
    (None), never zero. Inferred message signals are labelled as inferred."""
    starts = [_week_start(as_of) - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    labels = [s.isoformat() for s in starts]
    manual_metrics = [
        ("FIRST_MESSAGE", "First outreach messages"),
        ("RECRUITER_REPLIED", "Recruiter replies"),
        ("MEANINGFUL_REPLY", "Meaningful replies"),
        ("FOLLOW_UP", "Follow-ups completed"),
        ("CV_REQUESTED", "CV requests"),
        ("CV_SUBMITTED", "CV submissions"),
        ("INTERVIEW_SCHEDULED", "Interviews"),
        ("OPPORTUNITY_CONFIRMED", "Confirmed vacancies"),
        ("OFFER", "Offers"),
        ("CONTRACT", "Contracts"),
    ]
    first_logged = None
    if manual is not None and not manual.empty:
        ds = [d for d in (_d(x) for x in manual["action_date"]) if d]
        first_logged = min(ds) if ds else None
    measured_from = _week_start(first_logged) if first_logged else None

    def _bucket(d):
        ws = _week_start(d)
        return ws.isoformat() if ws in starts else None

    manual_counts = {code: Counter() for code, _ in manual_metrics}
    if manual is not None and not manual.empty:
        for _, r in manual.iterrows():
            d = _d(r["action_date"])
            if not d:
                continue
            b = _bucket(d)
            if b and r["action_type"] in manual_counts:
                manual_counts[r["action_type"]][b] += 1
            # A meaningful reply is also a reply.
            if b and r["action_type"] == "MEANINGFUL_REPLY":
                manual_counts["RECRUITER_REPLIED"][b] += 1

    manual_rows = []
    for code, label in manual_metrics:
        vals = []
        for s, lab in zip(starts, labels):
            vals.append(manual_counts[code][lab] if (measured_from and s >= measured_from) else None)
        manual_rows.append({"metric": label, "code": code, "values": vals, "source": "manual"})

    inferred_map = {
        "Inbound Opportunity": "Inbound opportunities",
        "CV Requested": "CV requests",
        "Salary Expectations Requested": "Salary requests",
        "Recruiter Call Proposed": "Call requests",
        "Interview Process": "Interviews",
        "Technical Interview": "Interviews",
        "Client Submission": "Client submissions",
        "Rejected / Closed": "Rejections",
    }
    inferred_counts: dict[str, Counter] = defaultdict(Counter)
    for e in (oh.get("events") or []):
        lab = inferred_map.get(_s(e.get("opportunity_event_type")))
        d = _d(e.get("event_date"))
        if lab and d:
            b = _bucket(d)
            if b:
                inferred_counts[lab][b] += 1
    inferred_rows = [
        {"metric": lab, "values": [inferred_counts[lab][l] for l in labels], "source": "inferred"}
        for lab in dict.fromkeys(inferred_map.values())
    ]
    # New conversations per snapshot week (from Weekly History).
    conv = Counter()
    for w in (weekly_history or []):
        d = _d(w.get("current_snapshot_date"))
        if d:
            # Snapshot on Sunday covers the Mon-Sun week ending that day.
            b = _bucket(d)
            if b and w.get("new_conversations_started") is not None:
                conv[b] = _i(w.get("new_conversations_started"))
    inferred_rows.insert(0, {"metric": "New conversations (export)",
                             "values": [conv.get(l) if l in conv else None for l in labels],
                             "source": "inferred"})

    # Conversion rates only when a reliable denominator exists (manual log).
    cur = labels[-1]
    def _total(code):
        return sum(manual_counts[code].values())
    rates = []
    for num, den, label in [
        ("RECRUITER_REPLIED", "FIRST_MESSAGE", "Reply rate (replies / first messages)"),
        ("CV_SUBMITTED", "RECRUITER_REPLIED", "CV rate (CV submitted / replies)"),
        ("INTERVIEW_SCHEDULED", "CV_SUBMITTED", "Interview rate (interviews / CVs)"),
        ("OFFER", "INTERVIEW_SCHEDULED", "Offer rate (offers / interviews)"),
    ]:
        d_, n_ = _total(den), _total(num)
        rates.append({"metric": label,
                      "value": (round(100 * n_ / d_, 1) if d_ >= 5 else None),
                      "numerator": n_, "denominator": d_,
                      "note": "" if d_ >= 5 else "Not measured — fewer than 5 logged events in the denominator"})
    return {
        "weeks": labels, "manual": manual_rows, "inferred": inferred_rows, "rates": rates,
        "manual_measured": bool(measured_from), "measured_from": measured_from.isoformat() if measured_from else "",
        "current_week": cur,
    }


# ── KPI definitions (server-side mirror of the frontend predicates) ────────

def _is_due(r: dict, as_of: date) -> bool:
    d = _d(r.get("fu"))
    return bool(d and d <= as_of)


KPI_DEFS = [
    ("urgent",          "Urgent Replies",                    lambda r, t: "urgent" in r["tags"] and "REPLY_NOW" in r["q"]),
    ("interview",       "Active Interview Actions",          lambda r, t: "interview" in r["tags"]),
    ("cv_salary_call",  "CV / Salary / Call Requests",       lambda r, t: "cv_salary_call" in r["tags"]),
    ("followup_due",    "High-Value Follow-ups Due",         lambda r, t: "USD_FOLLOWUP" in r["q"] and _is_due(r, t)),
    ("nc_recruiter",    "Never-Contacted Recruiters",        lambda r, t: "nc_recruiter" in r["tags"]),
    ("nc_ta",           "Never-Contacted Talent Acquisition", lambda r, t: "nc_ta" in r["tags"]),
    ("high_fit_usd",    "High-Fit USD Leads (actionable)",   lambda r, t: "high_fit_usd" in r["tags"] and bool(r["q"])),
    ("long_connected",  "Long-Connected Untapped Recruiters", lambda r, t: "long_connected" in r["tags"]),
    ("reactivation_due", "Reactivation Due",                 lambda r, t: "REACTIVATION" in r["q"] and _is_due(r, t)),
    ("confirmed",       "Confirmed Active Opportunities",    lambda r, t: "confirmed" in r["tags"]),
]


def build_daily_outreach(payload: dict, manual: pd.DataFrame | None = None,
                         as_of: date | None = None, manual_confirmed_count: int = 0) -> dict:
    as_of = as_of or date.today()
    lr = payload.get("lead_reactivation") or {}
    un = payload.get("untapped_network") or {}
    usd = payload.get("usd_contract_crm") or {}
    if not (lr.get("top_reactivation_contacts") or un.get("top_untapped_contacts")):
        return {"available": False, "reason": "No Lead Reactivation or Untapped Network data in this build."}

    crm_idx = _crm_index(usd)
    monthly, inbound = _monthly_index(usd, as_of)
    lead_rows, lead_excl = _lead_rows(lr, crm_idx, monthly, inbound, as_of)
    un_rows, un_excl = _untapped_rows(un, crm_idx, as_of)
    rows = _merge_rows(lead_rows + un_rows)

    manual = manual if manual is not None else pd.DataFrame()
    manual_idx = _manual_by_identity(manual)
    _apply_manual(rows, manual_idx, as_of)
    rows = [r for r in rows if r["q"]]

    rows.sort(key=_rank_key)
    top = _select_top(rows)
    top_ids = {r["id"] for r in top}
    for i, r in enumerate(top, 1):
        r["top_rank"] = i

    kpis = {}
    for code, label, pred in KPI_DEFS:
        kpis[code] = {"label": label, "count": sum(1 for r in rows if pred(r, as_of))}
    if manual_confirmed_count and kpis["confirmed"]["count"] == 0:
        kpis["confirmed"]["note"] = f"{manual_confirmed_count} confirmed in usd_pipeline.csv (role-level)"

    queues = {}
    for q, label in QUEUES:
        members = [r for r in rows if q in r["q"]]
        queues[q] = {"label": label, "count": len(members),
                     "primary": sum(1 for r in members if r["cat"] == q)}

    completed_today = completed_week = 0
    if not manual.empty:
        ws = _week_start(as_of)
        for d in (_d(x) for x in manual["action_date"]):
            if d == as_of:
                completed_today += 1
            if d and ws <= d <= as_of:
                completed_week += 1

    summary = {
        "total_actionable": len(rows),
        "top_ready": len(top),
        "high_priority": sum(1 for r in rows if r["score"] >= 70),
        "overdue": sum(1 for r in rows if (_d(r["fu"]) or date.max) < as_of),
        "due_today_or_overdue": sum(1 for r in rows if _is_due(r, as_of)),
        "manual_log_rows": int(len(manual)),
        "manual_tracking_available": True,
        "completed_today": completed_today if not manual.empty else None,
        "completed_this_week": completed_week if not manual.empty else None,
        "usd_share_of_queue_pct": round(100 * sum(1 for r in rows if r["cat"] != "SPAIN_EU") / max(1, len(rows)), 1),
    }

    public_cols = ["id", "name", "company", "role", "persona", "market", "url", "q", "cat", "tags",
                   "src", "score", "status", "why", "last", "last_label", "next", "fu", "msg",
                   "lang", "usd", "ev", "days_conn", "fu_rule", "manual", "top_rank"]

    def _pub(r):
        return {k: r[k] for k in public_cols if k in r and r[k] not in (None, "", [])} | {"q": r["q"], "score": r["score"], "tags": r["tags"]}

    oh = usd.get("opportunity_history") or {}
    return {
        "available": True,
        "as_of_date": as_of.isoformat(),
        "report_date": (payload.get("meta") or {}).get("report_date", ""),
        "summary": summary,
        "kpis": kpis,
        "queues": queues,
        "queue_order": [q for q, _ in QUEUES],
        "top15_ids": [r["id"] for r in top],
        "rows": [_pub(r) for r in rows],
        "daily_plan": _daily_plan(rows, as_of),
        "performance": _weekly_performance(manual, oh, payload.get("weekly_history") or [], as_of),
        "context": {
            "crm_high_fit_usd_remote_leads": (usd.get("summary") or {}).get("high_fit_usd_remote_leads"),
            "untapped_recruiters": (un.get("summary") or {}).get("recruiters_untapped"),
            "untapped_ta": (un.get("summary") or {}).get("ta_untapped"),
            "lead_needs_response_high_priority": lr.get("needs_response_high_priority_count"),
            "confirmed_funnel_measured": bool((usd.get("conversion_funnel") or {}).get("confirmed_measured")),
        },
        "exclusions": {
            "lead_reactivation": dict(sorted(_sum_by(lead_excl, lambda k: k.split(":")[0]).items())),
            "lead_blockers": dict(_sum_by({k: v for k, v in lead_excl.items() if k.startswith("blocked:")},
                                          lambda k: k.split(":", 1)[1]).most_common(12)),
            "lead_no_action_segments": dict(_sum_by({k: v for k, v in lead_excl.items() if k.startswith("no_action_queue:")},
                                                    lambda k: k.split(":", 1)[1]).most_common(12)),
            "untapped": dict(un_excl),
        },
        "manual_log": {
            "file": "data/manual/recruiter_outreach_log.csv (private, gitignored)",
            "action_types": MANUAL_ACTION_TYPES,
            "columns": [c for c in OUTREACH_LOG_COLUMNS if c != "notes_private"] + ["notes (private, never published)"],
        },
        "note": ("Recommended actions are suggestions from existing engines. Only rows in the private "
                 "manual log are shown as completed; nothing here is sent automatically."),
    }


def _sum_by(counter: dict, keyfn) -> Counter:
    out = Counter()
    for k, v in counter.items():
        out[keyfn(k)] += v
    return out


def count_manual_confirmed(path: Path = MANUAL_USD_PIPELINE) -> int:
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except Exception:
        return 0
    if "company_name" not in df.columns or "status" not in df.columns:
        return 0
    df = df[df["company_name"].str.strip() != ""]
    active = {"CV_SENT", "SUBMITTED_TO_CLIENT", "RECRUITER_CALL_SCHEDULED", "RECRUITER_CALL_DONE",
              "TECHNICAL_INTERVIEW_SCHEDULED", "TECHNICAL_INTERVIEW_DONE", "FINAL_INTERVIEW", "OFFER"}
    return int(df["status"].str.upper().isin(active).sum())


def add_daily_outreach_to_payload(payload: dict, as_of: date | None = None) -> dict:
    manual = load_manual_activity()
    payload["daily_outreach"] = build_daily_outreach(
        payload, manual, as_of=as_of, manual_confirmed_count=count_manual_confirmed())
    return payload["daily_outreach"]


def main():  # pragma: no cover — console summary
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
    with open(PUBLIC_JSON, encoding="utf-8") as f:
        payload = json.load(f)
    do = add_daily_outreach_to_payload(payload)
    print(json.dumps({k: do[k] for k in ("summary", "kpis", "queues", "daily_plan")}, indent=2, ensure_ascii=False))
    for r in do["rows"]:
        if r.get("top_rank"):
            print(r["top_rank"], r["score"], r["cat"], r["name"], "|", r.get("company", ""), "|", r["status"])


if __name__ == "__main__":
    main()
