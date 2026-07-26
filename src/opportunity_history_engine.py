# -*- coding: utf-8 -*-
"""
opportunity_history_engine.py
===============================
Monthly opportunity history & reactivation intelligence, built ONLY from
messages.csv (already used by src/message_intelligence.py) and the already-
classified connections dataframe. No LinkedIn scraping, browsing, or
automation of any kind.

For each conversation, walks chronologically through the OTHER person's
meaningful messages, buckets them by calendar month, and classifies each
conversation-month bucket into a controlled-vocabulary `opportunity_event_type`
plus a set of independent boolean signals (inbound contact, active talent
pool, salary/CV/interview/call requests, client submission, rejection,
soft-close/keep-on-radar, location/eligibility blocks, career-site
redirects). Multiple signals can be true on the same event — e.g. a single
inbound message can simultaneously be an "Inbound Opportunity" that also
requests salary expectations and proposes a call (see Qubika-style example
in the module docstring for run_opportunity_history_engine()).

Sanitization (hard requirement): every output field is a boolean, a score, a
date, or a SHORT controlled-vocabulary / templated label. Raw message
content, emails, phone numbers, and attachments are never read into any
output field — classification is done via keyword-match booleans only, and
`reason_short` / `message_angle` are built from static templates keyed off
those booleans, never from extracted substrings of the real message.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.message_sanitizer import strip_html
from src.message_intelligence import (
    load_messages,
    POSITIVE_KW, CV_KW, INTERVIEW_ACTIVE_KW,
    LOCATION_ELIGIBILITY_KW, GEOGRAPHIC_RESTRICTION_KW, WORK_AUTH_KW,
    CAREER_SITE_KW, AUTO_REPLY_KW, GENERIC_ACK_KW,
)

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = ROOT / "outputs"

MY_URL = "https://www.linkedin.com/in/mauricio-behrens"
MY_NAMES = {
    "mauricio esquivel de siqueira behrens",
    "mauricio behrens",
    "mauricio esquivel",
    "mauricio",
}

RECRUITER_PERSONAS = {
    "Recruiter", "Talent Acquisition", "Sourcer",
    "Hiring Manager", "Engineering Manager",
    "Data Engineering Manager", "Head of Data", "Director", "Executive",
}

# ── New keyword vocabularies specific to Opportunity History (EN/PT/ES) ─────

INBOUND_OPENING_KW = [
    "came across your profile", "your profile looks like a strong match",
    "your profile caught my attention", "we are currently seeking",
    "currently seeking several", "i am reaching out because",
    "reaching out because your background", "opportunity at", "role at",
    "position at", "vaga em", "oportunidade na", "oportunidade em",
    "encontrei seu perfil", "seu perfil parece um bom encaixe",
    "estamos buscando", "buscamos atualmente", "encontré tu perfil",
    "encontre tu perfil", "tu perfil parece un buen match",
    "estamos buscando actualmente", "oportunidad en",
]

ACTIVE_TALENT_POOL_KW = [
    "active talent pool", "you are now part of our active talent pool",
    "pool de talentos ativo", "grupo de talentos ativo",
    "grupo de talento activo", "pool de talento activo",
]

SALARY_KW = [
    "salary expectation", "salary expectations", "compensation expectation",
    "rate expectation", "your expected salary", "expected rate",
    "your rate expectations", "share your salary",
    "pretensão salarial", "pretensao salarial", "expectativa salarial",
    "expectativas salariales", "pretensión salarial", "pretension salarial",
    "expectativa de remuneração", "expectativa de remuneracao",
]

CLIENT_SUBMISSION_KW = [
    "submitted your profile to the client", "shared your profile with the client",
    "presented your profile to the client", "moving your profile forward to the client",
    "client will review your profile", "forwarded your profile to the client",
    "submeti seu perfil ao cliente", "enviamos seu perfil ao cliente",
    "apresentamos seu perfil ao cliente", "envié tu perfil al cliente",
    "presentamos tu perfil al cliente",
]

TECHNICAL_INTERVIEW_KW = [
    "technical interview", "technical assessment", "technical round",
    "coding challenge", "live coding", "system design interview",
    "take-home test", "take home test", "technical test",
    "entrevista técnica", "entrevista tecnica", "prova técnica", "prova tecnica",
    "desafio técnico", "desafio tecnico", "teste técnico", "teste tecnico",
    "prueba técnica", "prueba tecnica", "reto técnico", "reto tecnico",
]

CALL_PROPOSED_KW = [
    "schedule a call", "book a call", "hop on a call", "quick call",
    "short call", "call this week", "brief call", "15-minute call",
    "30-minute call", "agendar uma ligação", "agendar uma ligacao",
    "agendar uma call", "agendar una llamada", "una breve llamada",
]

INTERVIEW_PROCESS_KW = INTERVIEW_ACTIVE_KW + [
    "initial interview", "first interview", "interview process",
    "first round", "screening call", "screening interview",
    "entrevista inicial", "primeira entrevista", "processo de entrevista",
    "primera entrevista", "proceso de entrevista",
]

APPLICATION_REQUESTED_KW = [
    "please apply", "apply here", "apply now", "apply through", "apply via",
    "submit your application", "complete your application",
    "candidate-se através", "candidate-se atraves",
    "por favor se candidate", "aplica aquí", "aplica aqui",
    "por favor postula", "postula a través", "postula a traves",
]

# Deliberately STRICTER than message_intelligence.py's TERMINAL_REJECTION_KW,
# which also includes generic weak words like bare "unfortunately" /
# "infelizmente" — those false-positive on soft-close messages that use the
# same polite negative framing without ever having entered a real process
# (see the Juan Pablo-style example this module is validated against: "we
# don't have open positions... but I'll keep you on my radar" — this must
# classify as a soft close, never a hard rejection, even though it uses
# "unfortunately"-style wording). Only phrases naming a SPECIFIC concluded
# process/candidacy outcome count as a hard rejection here.
STRONG_REJECTION_KW = [
    "not selected", "decided to move forward", "move forward with another candidate",
    "moving forward with another candidate", "another candidate", "rejected",
    "role has been filled", "position has been filled", "process closed",
    "role closed", "position closed", "client chose another candidate",
    "client selected another candidate", "decided not to proceed",
    "moved forward with other",
    "não avançamos", "nao avancamos", "não seguiremos", "nao seguiremos",
    "encerramos o processo", "outra pessoa foi selecionada", "outro candidato",
    "posição fechada", "posicao fechada", "seguimos com outro candidato",
    "avançamos com outro candidato", "avancamos com outro candidato",
    "optamos por outro candidato", "não avançaremos", "nao avancaremos",
    "posição foi preenchida", "posicao foi preenchida", "vaga foi encerrada",
    "processo encerrado", "posición cerrada", "posicion cerrada", "otra persona",
    "otro candidato", "avanzamos con otro candidato",
]

SOFT_CLOSE_KW = [
    "don't have open positions", "do not have open positions",
    "no open positions", "no current openings", "no current opening",
    "no role right now", "no roles right now", "keep you on my radar",
    "keep your profile on file", "for future opportunities",
    "will keep your profile", "let's stay in touch", "lets stay in touch",
    "stay in touch", "keep in touch",
    "não temos vagas abertas no momento", "nao temos vagas abertas no momento",
    "não temos posições abertas", "nao temos posicoes abertas",
    "vou te manter no radar", "manter seu perfil", "manter seu perfil em nosso radar",
    "para futuras oportunidades", "para futuras oportunidades",
    "vamos ficar em contato", "fico no aguardo de futuras oportunidades",
    "no tenemos vacantes abiertas", "no tenemos posiciones abiertas",
    "te mantendré en mi radar", "te mantendre en mi radar",
    "mantengamos el contacto", "sigamos en contacto",
]

# Controlled-vocabulary tech stack terms — only a strength LABEL is ever
# emitted (see _tech_stack_signal), never the matched words themselves.
TECH_STACK_TERMS = (
    "databricks", "pyspark", "spark", "dbt", "terraform", "airflow", "sql",
    "python", "aws", "azure", "gcp", "snowflake", "kafka", "etl", "elt",
)
USD_TERMS = ("usd", "us dollar", "us dollars", "dólar", "dolar", "dollars")
LATAM_TERMS = ("latam", "latin america", "brazil", "brasil", "nearshore")
REMOTE_TERMS = ("remote", "remoto", "fully remote", "work from home", " wfh")

# ── Allowed opportunity_event_type values (fixed vocabulary) ────────────────
EVENT_TYPES = [
    "Inbound Opportunity", "Recruiter Outreach", "Active Talent Pool",
    "Salary Expectations Requested", "CV Requested", "Application Requested",
    "Recruiter Call Proposed", "Interview Process", "Client Submission",
    "Technical Interview", "Offer / Contract Discussion",
    "No Current Role / Keep on Radar", "Rejected / Closed",
    "Location / Eligibility Blocked", "Career Site / Talent Database Redirect",
    "No Response", "Warm Relationship", "Unknown / Review Needed",
]

STAGE_BY_EVENT_TYPE = {
    "Inbound Opportunity": "Recruiter outreach",
    "Recruiter Outreach": "Recruiter outreach",
    "Active Talent Pool": "Active Talent Pool",
    "Salary Expectations Requested": "Salary discussion",
    "CV Requested": "CV / Screening",
    "Application Requested": "Application",
    "Recruiter Call Proposed": "Call scheduled",
    "Interview Process": "Interview / Screening",
    "Client Submission": "Submitted to client",
    "Technical Interview": "Technical interview",
    "Offer / Contract Discussion": "Offer stage",
    "No Current Role / Keep on Radar": "Closed for now",
    "Rejected / Closed": "Closed",
    "Location / Eligibility Blocked": "Blocked",
    "Career Site / Talent Database Redirect": "Redirected",
    "No Response": "Awaiting reply",
    "Warm Relationship": "Warm / dormant",
    "Unknown / Review Needed": "Unknown",
}

HIGH_SIGNAL_TYPES = {
    "Inbound Opportunity", "Client Submission", "Technical Interview",
    "Interview Process", "Recruiter Call Proposed", "Offer / Contract Discussion",
}
MEDIUM_SIGNAL_TYPES = {
    "Active Talent Pool", "Salary Expectations Requested", "CV Requested",
    "Application Requested", "No Current Role / Keep on Radar", "Recruiter Outreach",
}

RECOMMENDED_ACTION_BY_TYPE = {
    "Inbound Opportunity": "Reply with interest, salary range, availability, and ask for call slots",
    "Recruiter Outreach": "Reply with interest and ask for role details",
    "Active Talent Pool": "Confirm interest and share availability",
    "Salary Expectations Requested": "Share salary/rate range promptly",
    "CV Requested": "Send updated CV/resume",
    "Application Requested": "Submit application as requested",
    "Recruiter Call Proposed": "Propose call slots",
    "Interview Process": "Prepare for interview and confirm slot",
    "Client Submission": "Wait for client feedback; follow up if no update in 5-7 days",
    "Technical Interview": "Prepare for technical round",
    "Offer / Contract Discussion": "Review offer terms and respond promptly",
    "No Current Role / Keep on Radar": "Do not reply immediately if already responded; reactivate later with updated availability",
    "Rejected / Closed": "No action needed now; consider reactivating in a few months",
    "Location / Eligibility Blocked": "No action — not eligible for this specific role",
    "Career Site / Talent Database Redirect": "Apply via career site only if the role is a strong match",
    "No Response": "Consider a polite follow-up",
    "Warm Relationship": "Stay in touch periodically",
    "Unknown / Review Needed": "Review conversation manually",
}

MESSAGE_ANGLE_BY_TYPE = {
    "Inbound Opportunity": "Thank them for reaching out, confirm interest, share salary expectations and availability, and propose call slots.",
    "Recruiter Outreach": "Thank them for reaching out and ask for more details on the role.",
    "Active Talent Pool": "Confirm continued interest and share current availability.",
    "Salary Expectations Requested": "Share a clear salary/rate range aligned to the role and location.",
    "CV Requested": "Send an updated CV tailored to the role.",
    "Application Requested": "Complete the application with an updated CV.",
    "Recruiter Call Proposed": "Propose 2-3 concrete call time slots.",
    "Interview Process": "Confirm the interview slot and ask about format/structure.",
    "Client Submission": "Acknowledge and ask for an expected timeline.",
    "Technical Interview": "Confirm the technical interview slot and ask about topics/format.",
    "Offer / Contract Discussion": "Review terms and respond with any questions promptly.",
    "No Current Role / Keep on Radar": "Acknowledge warmly, keep the relationship open, and note availability for future roles.",
    "Rejected / Closed": "No reply needed unless reactivating later.",
    "Location / Eligibility Blocked": "No reply needed — role requires different eligibility.",
    "Career Site / Talent Database Redirect": "Apply directly via the career site if genuinely interested.",
    "No Response": "Send a brief, polite follow-up.",
    "Warm Relationship": "Send an occasional friendly check-in.",
    "Unknown / Review Needed": "Review manually before deciding the next step.",
}

# Near-term follow-up / longer-term reactivation windows (days), keyed by
# event_type. Mirrors the day-window convention already used by
# src/message_intelligence.py's _REACTIVATION_WINDOW_DAYS for closed/blocked
# states (60-90 days), and uses a short 2-3 day nudge for active leads.
_NEAR_TERM_FOLLOWUP_DAYS = 3
_SOFT_CLOSE_REACTIVATION_DAYS = 75   # midpoint of the requested 60-90 day range
_REJECTED_REACTIVATION_DAYS = 0      # no reactivation date for hard rejections
_BLOCKED_REACTIVATION_DAYS = 0


def _is_me(name: str, url: str) -> bool:
    if url and MY_URL.lower() in (url or "").lower():
        return True
    if name:
        n = name.lower().strip()
        for my_n in MY_NAMES:
            if n == my_n or n.startswith(my_n):
                return True
    return False


def _kw_match(text: str, keywords: list) -> bool:
    if not text:
        return False
    tl = text.lower()
    return any(kw in tl for kw in keywords)


def _norm(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _norm_url(v) -> str:
    return _norm(v).lower().rstrip("/")


def _tech_stack_signal(text: str) -> str:
    tl = (text or "").lower()
    hits = sum(1 for kw in TECH_STACK_TERMS if kw in tl)
    if hits >= 3:
        return "High"
    if hits >= 1:
        return "Medium"
    return "None"


def _has_any(text: str, terms: tuple) -> bool:
    tl = (text or "").lower()
    return any(t in tl for t in terms)


# ── Identity join (mirrors message_intelligence._build_url_name_lookup) ────

def _build_lookup(classified_df: pd.DataFrame | None) -> tuple:
    url_lookup, name_lookup = {}, {}
    if classified_df is None or classified_df.empty:
        return url_lookup, name_lookup
    for _, row in classified_df.iterrows():
        url = _norm_url(row.get("url", ""))
        name = str(row.get("full_name", "") or "").lower().strip()
        if url:
            url_lookup[url] = row
        if name and name not in name_lookup:
            name_lookup[name] = row
    return url_lookup, name_lookup


# ── Per-bucket classification ───────────────────────────────────────────────

def _classify_bucket(texts: list[str], is_first_bucket: bool, opened_by_other: bool,
                      has_prior_opportunity: bool) -> dict:
    """Classifies ONE conversation-month bucket of the other person's
    meaningful messages. Returns all required boolean signals + the single
    primary opportunity_event_type, chosen by priority so that a message
    matching several categories at once (e.g. the Qubika-style example: an
    opening pitch that ALSO asks for salary and proposes a call) still gets
    one coherent primary label while every matched boolean stays true."""
    joined = " \n ".join(t for t in texts if t)

    rejected            = _kw_match(joined, STRONG_REJECTION_KW)
    location_blocked    = (_kw_match(joined, LOCATION_ELIGIBILITY_KW)
                            or _kw_match(joined, GEOGRAPHIC_RESTRICTION_KW)
                            or _kw_match(joined, WORK_AUTH_KW))
    client_submission   = _kw_match(joined, CLIENT_SUBMISSION_KW)
    technical_interview = _kw_match(joined, TECHNICAL_INTERVIEW_KW)
    interview_process    = _kw_match(joined, INTERVIEW_PROCESS_KW)
    call_proposed        = _kw_match(joined, CALL_PROPOSED_KW)
    cv_requested         = _kw_match(joined, CV_KW)
    salary_requested     = _kw_match(joined, SALARY_KW)
    active_talent_pool   = _kw_match(joined, ACTIVE_TALENT_POOL_KW)
    application_requested = _kw_match(joined, APPLICATION_REQUESTED_KW)
    career_site          = _kw_match(joined, CAREER_SITE_KW)
    soft_closed          = _kw_match(joined, SOFT_CLOSE_KW)
    auto_reply           = _kw_match(joined, AUTO_REPLY_KW)
    inbound_opening      = _kw_match(joined, INBOUND_OPENING_KW) or (
        opened_by_other and _kw_match(joined, POSITIVE_KW)
    )
    interview_or_call_requested = interview_process or call_proposed or technical_interview

    # ── Priority order for the single primary event_type ────────────────────
    if rejected:
        event_type = "Rejected / Closed"
    elif location_blocked:
        event_type = "Location / Eligibility Blocked"
    elif soft_closed:
        # Checked BEFORE the generic inbound/positive-signal checks below:
        # a soft-close message ("no open positions... but I'll keep you on
        # my radar") often also contains an incidental positive keyword like
        # "opportunities" — that must never promote it to "Inbound
        # Opportunity" or any active-signal type. Soft-close framing wins.
        event_type = "No Current Role / Keep on Radar"
    elif is_first_bucket and opened_by_other and inbound_opening:
        # The seminal event of the relationship wins over any specific
        # sub-signal (CV/salary/call) mentioned in that same opening message.
        event_type = "Inbound Opportunity"
    elif client_submission:
        event_type = "Client Submission"
    elif technical_interview:
        event_type = "Technical Interview"
    elif interview_process:
        event_type = "Interview Process"
    elif call_proposed:
        event_type = "Recruiter Call Proposed"
    elif cv_requested:
        event_type = "CV Requested"
    elif salary_requested:
        event_type = "Salary Expectations Requested"
    elif active_talent_pool:
        event_type = "Active Talent Pool"
    elif application_requested:
        event_type = "Application Requested"
    elif inbound_opening:
        event_type = "Recruiter Outreach"
    elif career_site:
        event_type = "Career Site / Talent Database Redirect"
    elif auto_reply:
        event_type = "Unknown / Review Needed"
    elif _kw_match(joined, GENERIC_ACK_KW):
        event_type = "Warm Relationship" if has_prior_opportunity else "Unknown / Review Needed"
    else:
        event_type = "Unknown / Review Needed"

    signal_strength = (
        "High" if event_type in HIGH_SIGNAL_TYPES else
        "Medium" if event_type in MEDIUM_SIGNAL_TYPES else
        "Low"
    )

    opportunity_stage = STAGE_BY_EVENT_TYPE.get(event_type, "Unknown")
    if event_type in ("Inbound Opportunity", "Recruiter Outreach") and interview_or_call_requested:
        opportunity_stage += " / Initial call proposed"

    tech_stack_signal = _tech_stack_signal(joined)
    usd_signal    = _has_any(joined, USD_TERMS)
    latam_signal  = _has_any(joined, LATAM_TERMS)
    remote_signal = _has_any(joined, REMOTE_TERMS)

    return {
        "opportunity_event_type": event_type,
        "opportunity_stage": opportunity_stage,
        "opportunity_signal_strength": signal_strength,
        "inbound_recruiter_contact": bool(is_first_bucket and opened_by_other and inbound_opening),
        "active_talent_pool_signal": active_talent_pool,
        "salary_expectation_requested": salary_requested,
        "cv_requested": cv_requested,
        "application_requested": application_requested,
        "interview_or_call_requested": interview_or_call_requested,
        "client_submission_signal": client_submission,
        "technical_interview_signal": technical_interview,
        "rejected_or_closed": rejected,
        "soft_closed": soft_closed,
        "location_or_eligibility_blocked": location_blocked,
        "tech_stack_signal": tech_stack_signal,
        "usd_signal": usd_signal,
        "latam_signal": latam_signal,
        "remote_signal": remote_signal,
    }


def _event_score(flags: dict) -> int:
    score = 0
    if flags["inbound_recruiter_contact"]:        score += 25
    if flags["active_talent_pool_signal"]:        score += 15
    if flags["salary_expectation_requested"]:     score += 10
    if flags["cv_requested"]:                     score += 10
    if flags["application_requested"]:            score += 10
    if flags["interview_or_call_requested"]:      score += 15
    if flags["client_submission_signal"]:         score += 20
    if flags["technical_interview_signal"]:       score += 20
    if flags["tech_stack_signal"] == "High":      score += 10
    elif flags["tech_stack_signal"] == "Medium":  score += 5
    if flags["rejected_or_closed"]:                score -= 30
    if flags["soft_closed"]:                       score -= 10
    if flags["location_or_eligibility_blocked"]:   score -= 25
    return int(max(0, min(100, score)))


def _reason_short(flags: dict, event_type: str) -> str:
    tags = []
    if flags["inbound_recruiter_contact"]:       tags.append("inbound recruiter contact")
    if flags["active_talent_pool_signal"]:       tags.append("active talent pool")
    if flags["salary_expectation_requested"]:    tags.append("salary requested")
    if flags["cv_requested"]:                    tags.append("CV requested")
    if flags["application_requested"]:           tags.append("application requested")
    if flags["interview_or_call_requested"]:     tags.append("call/interview requested")
    if flags["client_submission_signal"]:        tags.append("client submission")
    if flags["technical_interview_signal"]:      tags.append("technical interview")
    if flags["tech_stack_signal"] != "None":      tags.append(f"tech stack match: {flags['tech_stack_signal']}")
    if flags["rejected_or_closed"]:                tags.append("rejected/closed")
    if flags["soft_closed"]:                       tags.append("soft-closed, keep on radar")
    if flags["location_or_eligibility_blocked"]:   tags.append("location/eligibility blocker")
    if not tags:
        tags.append(event_type.lower())
    return "; ".join(tags)


def _reactivation_fields(event_type: str, event_date: date) -> tuple[str, bool]:
    """Returns (reactivation_date_str, future_reactivation_candidate).
    reactivation_date doubles as "next date to revisit this event" — a
    near-term nudge for active leads, a longer 60-90d window for soft-closed
    ones. future_reactivation_candidate is only true for genuinely closed-
    for-now leads worth revisiting later (soft-close / career-site redirect),
    never for hard rejections or location/eligibility blocks."""
    if event_type in ("No Current Role / Keep on Radar", "Career Site / Talent Database Redirect"):
        return str(event_date + timedelta(days=_SOFT_CLOSE_REACTIVATION_DAYS)), True
    if event_type in ("Rejected / Closed", "Location / Eligibility Blocked"):
        return "", False
    if event_type == "No Response":
        return "", False
    if event_type == "Warm Relationship":
        return str(event_date + timedelta(days=30)), True
    if event_type == "Unknown / Review Needed":
        return "", False
    # Active/live event types get a short near-term follow-up nudge.
    return str(event_date + timedelta(days=_NEAR_TERM_FOLLOWUP_DAYS)), False


def _event_id(conversation_id: str, event_month: str) -> str:
    # 8 hex chars — deliberately SHORT: a 10+ char hex substring can, by pure
    # chance, land entirely on digits (0-9) with no interceding a-f letter,
    # which would false-positive privacy_check.py's "possible phone number"
    # regex (\d{10,15}) once this ID is embedded in the public JSON. An
    # 8-char token can never contain a 10-digit run, so this is safe by
    # construction; collision risk at this data scale is negligible for a
    # display-only identifier.
    return hashlib.sha1(f"{conversation_id}|{event_month}".encode("utf-8", "ignore")).hexdigest()[:8]


def _conversation_id_hash(conversation_id: str) -> str:
    return hashlib.sha1(str(conversation_id).encode("utf-8", "ignore")).hexdigest()[:8]


# ── Main per-conversation walk ───────────────────────────────────────────────

def _build_events_for_conversation(conv_id: str, group: pd.DataFrame, url_lookup: dict,
                                    name_lookup: dict) -> list[dict]:
    group = group.sort_values("date_parsed").reset_index(drop=True)
    if group.empty:
        return []

    them_msgs = group[~group["is_me_sender"]]
    my_msgs   = group[group["is_me_sender"]]

    if not them_msgs.empty:
        other_name = them_msgs.iloc[0]["from"]
        other_url  = them_msgs.iloc[0].get("sender_profile_url", "")
    elif not my_msgs.empty:
        other_name = my_msgs.iloc[0]["to"]
        other_url  = ""
    else:
        return []
    if _is_me(other_name, other_url):
        other_name = group.iloc[0]["to"] if not group.empty else ""

    url_clean = _norm_url(other_url)
    match_row = url_lookup.get(url_clean) if url_clean else None
    if match_row is None and other_name:
        match_row = name_lookup.get(other_name.lower().strip())

    contact_name = _norm(other_name)
    company      = ""
    role         = ""
    persona      = ""
    opportunity_bucket = ""
    profile_url  = _norm(other_url)
    if match_row is not None:
        company            = str(match_row.get("company_clean", "") or "")
        role               = str(match_row.get("position_clean", "") or "")
        persona            = str(match_row.get("persona", "") or "")
        opportunity_bucket = str(match_row.get("opportunity_bucket", "") or "").upper()
        if not profile_url:
            profile_url = str(match_row.get("url", "") or "")

    # Whether the conversation OPENED (first message overall) with the other
    # person, not me — used only for the FIRST bucket's inbound classification.
    opened_by_other_overall = bool(len(group) and not group.iloc[0]["is_me_sender"])

    all_content = " ".join(strip_html(c or "") for c in group["content"].fillna("").tolist())
    has_prior_opportunity = _kw_match(all_content, POSITIVE_KW) or _kw_match(all_content, CV_KW) \
        or _kw_match(all_content, INTERVIEW_ACTIVE_KW)

    conv_id_hash = _conversation_id_hash(conv_id)

    if them_msgs.empty:
        # Pure outbound, never replied — one "No Response" event on my last message's month.
        last = my_msgs.iloc[-1]
        event_month = last["date_parsed"].strftime("%Y-%m") if last["date_parsed"] else ""
        event_date_val = last["date_parsed"].date() if last["date_parsed"] else date.today()
        flags = {
            "opportunity_event_type": "No Response", "opportunity_stage": "Awaiting reply",
            "opportunity_signal_strength": "Low", "inbound_recruiter_contact": False,
            "active_talent_pool_signal": False, "salary_expectation_requested": False,
            "cv_requested": False, "application_requested": False,
            "interview_or_call_requested": False, "client_submission_signal": False,
            "technical_interview_signal": False, "rejected_or_closed": False,
            "soft_closed": False, "location_or_eligibility_blocked": False,
            "tech_stack_signal": "None", "usd_signal": False, "latam_signal": False,
            "remote_signal": False,
        }
        reactivation_date, future_candidate = "", False
        return [{
            "event_id": _event_id(conv_id, event_month or "unknown"),
            "conversation_id_hash": conv_id_hash,
            "contact_name": contact_name, "company": company, "role": role,
            "persona": persona, "profile_url": profile_url,
            "event_month": event_month, "event_date": str(event_date_val),
            **flags,
            "future_reactivation_candidate": future_candidate,
            "reactivation_date": reactivation_date,
            "recommended_action": RECOMMENDED_ACTION_BY_TYPE["No Response"],
            "message_angle": MESSAGE_ANGLE_BY_TYPE["No Response"],
            "opportunity_bucket": opportunity_bucket,
            "score": 0,
            "reason_short": "no response received",
        }]

    # ── Bucket the OTHER person's messages by calendar month ────────────────
    them_msgs = them_msgs.copy()
    them_msgs = them_msgs[them_msgs["date_parsed"].notna()]
    if them_msgs.empty:
        return []
    them_msgs["month"] = them_msgs["date_parsed"].apply(lambda d: d.strftime("%Y-%m"))

    events = []
    months_sorted = sorted(them_msgs["month"].unique())
    for i, month in enumerate(months_sorted):
        bucket = them_msgs[them_msgs["month"] == month]
        texts = [strip_html(c or "") for c in bucket["content"].fillna("").tolist()]
        is_first_bucket = (i == 0)
        flags = _classify_bucket(texts, is_first_bucket, opened_by_other_overall, has_prior_opportunity)
        event_type = flags["opportunity_event_type"]
        event_date_val = bucket["date_parsed"].max().date()
        reactivation_date, future_candidate = _reactivation_fields(event_type, event_date_val)
        events.append({
            "event_id": _event_id(conv_id, month),
            "conversation_id_hash": conv_id_hash,
            "contact_name": contact_name, "company": company, "role": role,
            "persona": persona, "profile_url": profile_url,
            "event_month": month, "event_date": str(event_date_val),
            **flags,
            "future_reactivation_candidate": future_candidate,
            "reactivation_date": reactivation_date,
            "recommended_action": RECOMMENDED_ACTION_BY_TYPE.get(event_type, "Review manually"),
            "message_angle": MESSAGE_ANGLE_BY_TYPE.get(event_type, "Review manually before replying."),
            "opportunity_bucket": opportunity_bucket,
            "score": _event_score(flags),
            "reason_short": _reason_short(flags, event_type),
        })
    return events


EVENT_COLUMNS = [
    "event_id", "conversation_id_hash", "contact_name", "company", "role",
    "persona", "profile_url", "event_month", "event_date",
    "opportunity_event_type", "opportunity_stage", "opportunity_signal_strength",
    "inbound_recruiter_contact", "active_talent_pool_signal",
    "salary_expectation_requested", "cv_requested", "application_requested",
    "interview_or_call_requested", "client_submission_signal",
    "technical_interview_signal", "rejected_or_closed", "soft_closed",
    "location_or_eligibility_blocked", "future_reactivation_candidate",
    "reactivation_date", "recommended_action", "message_angle",
    "tech_stack_signal", "opportunity_bucket", "usd_signal", "latam_signal",
    "remote_signal", "score", "reason_short",
]

MONTHLY_PIPELINE_COLUMNS = [
    "month", "inbound_opportunities", "active_talent_pool", "salary_requested",
    "cv_requested", "calls_requested", "interviews", "client_submissions",
    "soft_closed_keep_radar", "hard_rejections", "location_blockers",
    "reactivation_due", "hot_opportunities", "warm_opportunities",
]


def _build_monthly_pipeline(events_df: pd.DataFrame) -> pd.DataFrame:
    if events_df.empty:
        return pd.DataFrame(columns=MONTHLY_PIPELINE_COLUMNS)

    months = sorted(set(events_df["event_month"]) | {
        d[:7] for d in events_df["reactivation_date"] if d
    })
    rows = []
    for month in months:
        m = events_df[events_df["event_month"] == month]
        reactivation_due_month = events_df[events_df["reactivation_date"].str.startswith(month, na=False)]
        rows.append({
            "month": month,
            "inbound_opportunities": int((m["opportunity_event_type"] == "Inbound Opportunity").sum()),
            "active_talent_pool":    int(m["active_talent_pool_signal"].sum()),
            "salary_requested":      int(m["salary_expectation_requested"].sum()),
            "cv_requested":          int(m["cv_requested"].sum()),
            "calls_requested":       int(m["interview_or_call_requested"].sum()),
            "interviews":            int(m["opportunity_event_type"].isin(["Interview Process", "Technical Interview"]).sum()),
            "client_submissions":    int(m["client_submission_signal"].sum()),
            "soft_closed_keep_radar": int(m["soft_closed"].sum()),
            "hard_rejections":       int(m["rejected_or_closed"].sum()),
            "location_blockers":     int(m["location_or_eligibility_blocked"].sum()),
            "reactivation_due":      int(len(reactivation_due_month)),
            "hot_opportunities":     int((m["opportunity_signal_strength"] == "High").sum()),
            "warm_opportunities":    int((m["opportunity_signal_strength"] == "Medium").sum()),
        })
    return pd.DataFrame(rows, columns=MONTHLY_PIPELINE_COLUMNS)


def _build_summary(events_df: pd.DataFrame) -> dict:
    if events_df.empty:
        return {
            "total_events": 0, "total_conversations": 0,
            "inbound_opportunities_total": 0, "active_talent_pool_total": 0,
            "salary_requested_total": 0, "cv_requested_total": 0,
            "calls_requested_total": 0, "interviews_total": 0,
            "client_submissions_total": 0, "soft_closed_total": 0,
            "hard_rejections_total": 0, "location_blockers_total": 0,
            "reactivation_due_now": 0, "hot_opportunities_total": 0,
            "warm_opportunities_total": 0,
        }
    today = date.today()
    reactivation_due_now = 0
    for d in events_df["reactivation_date"]:
        if not d:
            continue
        try:
            if date.fromisoformat(d) <= today:
                reactivation_due_now += 1
        except ValueError:
            continue
    return {
        "total_events":                  int(len(events_df)),
        "total_conversations":            int(events_df["conversation_id_hash"].nunique()),
        "inbound_opportunities_total":    int((events_df["opportunity_event_type"] == "Inbound Opportunity").sum()),
        "active_talent_pool_total":       int(events_df["active_talent_pool_signal"].sum()),
        "salary_requested_total":         int(events_df["salary_expectation_requested"].sum()),
        "cv_requested_total":             int(events_df["cv_requested"].sum()),
        "calls_requested_total":          int(events_df["interview_or_call_requested"].sum()),
        "interviews_total":               int(events_df["opportunity_event_type"].isin(["Interview Process", "Technical Interview"]).sum()),
        "client_submissions_total":       int(events_df["client_submission_signal"].sum()),
        "soft_closed_total":              int(events_df["soft_closed"].sum()),
        "hard_rejections_total":          int(events_df["rejected_or_closed"].sum()),
        "location_blockers_total":        int(events_df["location_or_eligibility_blocked"].sum()),
        "reactivation_due_now":           int(reactivation_due_now),
        "hot_opportunities_total":        int((events_df["opportunity_signal_strength"] == "High").sum()),
        "warm_opportunities_total":       int((events_df["opportunity_signal_strength"] == "Medium").sum()),
    }


def _save_csv(df: pd.DataFrame, name: str) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUTS_DIR / name, index=False, encoding="utf-8-sig")
    logger.info(f"  Saved {name} ({len(df)} rows)")


def run_opportunity_history_engine(classified_df: pd.DataFrame | None = None) -> dict:
    """Main entry point. Reads messages.csv (optional — safe no-op if
    absent), builds the monthly opportunity event history, writes the 6
    sanitized output CSVs, and returns a dict consumed by
    src/usd_contract_crm.py and src/export_public_dashboard_data.py.
    """
    msgs = load_messages()
    if msgs is None or msgs.empty:
        logger.info("  Opportunity History: messages.csv not available — skipping (optional).")
        return {"available": False}

    url_lookup, name_lookup = _build_lookup(classified_df)

    all_events: list[dict] = []
    for conv_id, group in msgs.groupby("conversation_id"):
        all_events.extend(_build_events_for_conversation(conv_id, group, url_lookup, name_lookup))

    events_df = pd.DataFrame(all_events, columns=EVENT_COLUMNS)
    monthly_df = _build_monthly_pipeline(events_df)
    summary = _build_summary(events_df)

    inbound_df = events_df[
        (events_df["opportunity_event_type"] == "Inbound Opportunity")
        | events_df["inbound_recruiter_contact"]
    ]
    # Strictly soft_closed==True only (not the broader future_reactivation_candidate,
    # which also includes dormant "Warm Relationship" events) — keeps this
    # section's row count exactly matching summary["soft_closed_total"], per
    # the "every KPI card count matches its filtered table" convention used
    # throughout this dashboard. Warm/dormant contacts still appear in the
    # broader reactivation_calendar section below.
    soft_closed_df = events_df[events_df["soft_closed"]]
    reactivation_df = events_df[events_df["reactivation_date"] != ""].sort_values("reactivation_date")

    _save_csv(events_df, "opportunity_history_events.csv")
    _save_csv(monthly_df, "opportunity_monthly_pipeline.csv")
    _save_csv(inbound_df, "inbound_opportunities.csv")
    _save_csv(soft_closed_df, "soft_closed_future_leads.csv")
    _save_csv(reactivation_df, "opportunity_reactivation_calendar.csv")
    _save_csv(
        pd.DataFrame([{"metric": k, "value": v} for k, v in summary.items()]),
        "opportunity_history_summary.csv",
    )

    logger.info(
        f"  Opportunity History: {summary['total_events']} events / {summary['total_conversations']} conversations | "
        f"inbound={summary['inbound_opportunities_total']} active_talent_pool={summary['active_talent_pool_total']} "
        f"salary_requested={summary['salary_requested_total']} cv_requested={summary['cv_requested_total']} "
        f"calls={summary['calls_requested_total']} interviews={summary['interviews_total']} "
        f"client_submissions={summary['client_submissions_total']} soft_closed={summary['soft_closed_total']} "
        f"hard_rejections={summary['hard_rejections_total']} location_blockers={summary['location_blockers_total']} "
        f"reactivation_due_now={summary['reactivation_due_now']}"
    )

    return {
        "available": True,
        "summary": summary,
        "monthly_pipeline": monthly_df.to_dict(orient="records"),
        "events": events_df.to_dict(orient="records"),
        "inbound_opportunities": inbound_df.to_dict(orient="records"),
        "soft_closed_future_leads": soft_closed_df.to_dict(orient="records"),
        "reactivation_calendar": reactivation_df.to_dict(orient="records"),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    result = run_opportunity_history_engine()
    if not result.get("available"):
        print("No opportunity history yet — messages.csv not found.")
    else:
        print(result["summary"])
