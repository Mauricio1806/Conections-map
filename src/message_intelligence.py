# -*- coding: utf-8 -*-
"""
message_intelligence.py  (V2 — corrective patch; V6 — response intelligence)
==============================================================================
Reads messages.csv and builds per-conversation intelligence.

Key fixes vs V1:
  - Join with classified_df BEFORE status determination (persona matters)
  - "Follow-up due" requires positive signal + recruiter/TA persona + 7-120 days
  - "Needs my response" requires substantive non-auto-reply last message
  - Old low-value conversations → "Low value / ignore" (not "Follow-up due")
  - Adds lead_category for cleaner segmentation
  - Uses O(1) lookup dict for classified_df join

V6 additions (response intelligence — see CLAUDE_INTELLIGENCE_V6_PATCH.md Parts 1-5):
  - Explicit response-intelligence fields computed on the TRUE last message of the
    conversation (whoever sent it): needs_my_response, needs_response_confidence,
    needs_response_reason, response_intent_score, last_message_is_substantive/
    question/request/auto_reply/generic_ack/process_closure/opportunity_signal,
    manual_review_required, last_sender_type, conversation_recency_band.
  - EN/PT/ES substantive-signal keyword rules (Part 2) and do-not-flag rules for
    generic acknowledgements / auto-replies / process closure (Part 3).
  - Recency-band decay logic (Part 4): conversations older than 90 days are not
    flagged "Needs my response" by default; older than 180 days only remain
    actionable if there was a prior interview/CV request/role-share/recruiter
    interaction.
  - Refined lead_category taxonomy (Part 5) with a 0-100 confidence-aligned
    response_intent_score. conversation_status (legacy) is left UNCHANGED so
    outreach_adjusted_scoring.py and the this-week-queue masks keep working.
  - No raw message content is ever exposed — only booleans/labels/short static
    reason strings are produced.
"""

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.message_sanitizer import strip_html

logger = logging.getLogger(__name__)

ROOT         = Path(__file__).resolve().parent.parent
MESSAGES_CSV = ROOT / "messages.csv"

MY_NAMES = {
    "mauricio esquivel de siqueira behrens",
    "mauricio behrens",
    "mauricio esquivel",
    "mauricio",
}
MY_URL = "https://www.linkedin.com/in/mauricio-behrens"

RECRUITER_PERSONAS = {
    "Recruiter", "Talent Acquisition", "Sourcer",
    "Hiring Manager", "Engineering Manager",
    "Data Engineering Manager", "Head of Data", "Director", "Executive",
}

# ── Keyword lists ─────────────────────────────────────────────────────────────

AUTO_REPLY_KW = [
    "automatic reply", "auto reply", "auto-reply", "high volume",
    "profiles can't be reviewed here", "profiles cannot be reviewed",
    "apply via our career site", "career site", "talent database",
    "submit your cv", "thanks for reaching out", "due to the high volume",
    "no role fits now", "we will keep your profile", "automated message",
    "apply through our", "apply on our", "submit your resume",
    "volume of messages", "cannot respond to everyone", "please apply",
    "canned response", "not able to respond individually",
]

POSITIVE_KW = [
    "opportunity", "vaga", "role", "job", "hiring",
    "data engineer", "engenheiro de dados",
    "remote", "contractor", "usd", "salary", "salário",
    "interview", "entrevista", "technical interview",
    "cv", "resume", "curriculo", "currículo",
    "apply", "candidatura", "processo seletivo",
    "recruiter", "talent acquisition",
    "screening", "open position", "open role", "availab",
]

REJECTION_KW = [
    "not selected", "unfortunately", "decided to move forward",
    "rejected", "não avançamos", "nao avancamos", "não seguiremos",
    "nao seguiremos", "encerramos", "outra pessoa",
    "posição fechada", "posicao fechada",
    "process closed", "role closed", "position closed",
    "moved forward with other", "decided not to proceed",
    "infelizmente", "não prosseguiremos",
    # Part 3 additions
    "posición cerrada", "posicion cerrada", "otra persona",
]

CV_KW = [
    "send your cv", "send me your resume", "send your updated cv",
    "send your updated resume", "share your updated cv", "share your updated resume",
    "updated cv", "updated resume", "curriculum", "currículo",
    "curriculo", "i applied", "realizei minha candidatura",
    "application submitted", "candidatura realizada",
    # NOTE: "talent database" / "career site" / "submit your profile" were
    # removed from this list (V8) — they are talent-pool/career-site REDIRECTS
    # (see TALENT_POOL_KW / CAREER_SITE_KW below), not CV requests, and lumping
    # them in here caused false CV-request/escalation signals.
]

INTERVIEW_KW = [
    "interview", "entrevista", "screening", "technical interview",
    "call", "agenda", "calendário", "calendario", "schedule",
    "meeting", "recruiter call", "video call", "video interview",
    "bate-papo", "conversa",
]

# ── V6 Response Intelligence keyword lists (CLAUDE_INTELLIGENCE_V6_PATCH.md Parts 2-3) ──

# Part 2 — substantive actionable request/question patterns (EN/PT/ES combined)
REQUEST_SUBSTANTIVE_KW = [
    # English
    "can you", "could you", "would you", "please send", "please share",
    "let me know", "confirm", "available", "availability",
    "when are you available", "what time", "schedule", "calendar",
    "meeting", "call", "interview", "screening", "technical interview",
    "send your cv", "send your resume", "share your cv", "share your resume",
    "updated cv", "updated resume", "compensation", "salary expectation",
    "notice period", "start date", "interested", "are you interested",
    "location", "relocate", "contractor", "citizenship", "work authorization",
    # Portuguese
    "você pode", "voce pode", "poderia", "me envie", "compartilhe",
    "confirma", "disponibilidade", "quando você pode", "quando voce pode",
    "agenda", "reunião", "reuniao", "entrevista", "currículo", "curriculo",
    "pretensão", "pretensao", "aviso prévio", "aviso previo", "início",
    "inicio", "tem interesse",
    # Spanish
    "puedes", "podrías", "podrias", "envíame", "enviame", "comparte",
    "confirma", "disponibilidad", "cuándo puedes", "cuando puedes",
    "agenda", "reunión", "reunion", "entrevista", "cv", "salario",
    "interesado",
]

# Explicit question lead-ins (subset used for last_message_is_question, beyond a bare "?")
QUESTION_LEADIN_KW = [
    "can you", "could you", "would you", "when are you available",
    "what time", "are you interested",
    "você pode", "voce pode", "poderia", "quando você pode", "quando voce pode",
    "puedes", "podrías", "podrias", "cuándo puedes", "cuando puedes",
]

# Explicit request/instruction lead-ins (subset used for last_message_is_request)
REQUEST_LEADIN_KW = [
    "please send", "please share", "let me know", "confirm",
    "send your cv", "send your resume", "share your cv", "share your resume",
    "updated cv", "updated resume", "schedule", "calendar",
    "me envie", "compartilhe", "confirma",
    "envíame", "enviame", "comparte",
]

# Part 3 — generic acknowledgements / reaction-only (do NOT flag as needing response)
GENERIC_ACK_KW = [
    # English
    "thanks", "thank you", "great", "perfect", "sounds good", "noted",
    "okay", "ok", "welcome", "my pleasure", "keep in touch",
    "best wishes", "good luck",
    # Portuguese
    "obrigado", "obrigada", "perfeito", "combinado", "beleza",
    "sucesso", "boa sorte",
    # Spanish
    "gracias", "perfecto", "entendido", "suerte",
]

# Emoji / reaction-only unicode ranges — used to detect emoji-only messages
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "❤️❤"
    "]+",
    flags=re.UNICODE,
)

RECENCY_BANDS = [
    (7,   "CURRENT"),
    (30,  "RECENT"),
    (90,  "AGING"),
    (180, "STALE"),
]


def _recency_band(days_since) -> str:
    """0-7 CURRENT · 8-30 RECENT · 31-90 AGING · 91-180 STALE · >180 HISTORICAL."""
    try:
        d = int(days_since)
    except (TypeError, ValueError):
        return "HISTORICAL"
    for limit, band in RECENCY_BANDS:
        if d <= limit:
            return band
    return "HISTORICAL"


def _is_emoji_or_reaction_only(text: str) -> bool:
    """True if, after stripping emoji/whitespace/punctuation, nothing substantive remains."""
    plain = (text or "").strip()
    if not plain:
        return False
    stripped = _EMOJI_RE.sub("", plain)
    stripped = re.sub(r"[\s\.,!?;:\-–—'\"()]+", "", stripped)
    return len(stripped) == 0 and len(plain) > 0


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {
        "CONVERSATION ID":        "conversation_id",
        "CONVERSATION TITLE":     "conversation_title",
        "FROM":                   "from",
        "SENDER PROFILE URL":     "sender_profile_url",
        "TO":                     "to",
        "RECIPIENT PROFILE URLS": "recipient_profile_urls",
        "DATE":                   "date",
        "SUBJECT":                "subject",
        "CONTENT":                "content",
        "FOLDER":                 "folder",
        "ATTACHMENTS":            "attachments",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    return df


def _is_me(name: str, url: str) -> bool:
    if url and MY_URL.lower() in url.lower():
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


def _kw_match_wb(text: str, keywords: list) -> bool:
    """Word-boundary keyword match — avoids short tokens (e.g. 'ok') matching
    inside unrelated words ('look', 'broken', 'token')."""
    if not text:
        return False
    tl = text.lower()
    for kw in keywords:
        if re.search(r"(?<![a-zà-ÿ0-9])" + re.escape(kw) + r"(?![a-zà-ÿ0-9])", tl):
            return True
    return False


def _score_content(text: str) -> dict:
    plain = strip_html(text or "")
    return {
        "auto_reply":  _kw_match(plain, AUTO_REPLY_KW),
        "positive":    _kw_match(plain, POSITIVE_KW),
        "rejection":   _kw_match(plain, REJECTION_KW),
        "cv_request":  _kw_match(plain, CV_KW),
        "interview":   _kw_match(plain, INTERVIEW_KW),
    }


def _parse_date(s) -> datetime | None:
    if pd.isna(s) or not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt[:len(s[:19])])
        except ValueError:
            pass
    return None


def _first_url(urls_str: str) -> str:
    if not urls_str:
        return ""
    parts = [u.strip() for u in str(urls_str).split(",") if u.strip()]
    return parts[0] if parts else ""


def _is_substantive(text: str, min_chars: int = 40) -> bool:
    """True if message has real substance (not auto-reply, not just a few chars)."""
    plain = strip_html(text or "").strip()
    if len(plain) < min_chars:
        return False
    if _kw_match(plain, AUTO_REPLY_KW):
        return False
    return True


# ── V6 Response Intelligence (CLAUDE_INTELLIGENCE_V6_PATCH.md Parts 1-4) ─────────

_CONFIDENCE_TIER_BONUS = {"HIGH": 30, "MEDIUM": 15, "LOW": 5, "NONE": 0}
_RECENCY_BAND_BONUS    = {"CURRENT": 12, "RECENT": 9, "AGING": 5, "STALE": 2, "HISTORICAL": 0}


def _response_intelligence(
    last_message_raw: str,
    last_sender_is_me: bool,
    days_since: int,
    is_valuable_persona: bool,
    has_interview_signal: bool,
    has_cv_signal: bool,
) -> dict:
    """
    Classify the TRUE last message of a conversation (whoever sent it) into
    explicit response-intelligence fields. Never returns/uses raw message text —
    only booleans, short static reason strings, and numeric scores.
    """
    plain = strip_html(last_message_raw or "").strip()
    band  = _recency_band(days_since)

    is_auto        = _kw_match(plain, AUTO_REPLY_KW)
    is_ack         = _kw_match_wb(plain, GENERIC_ACK_KW)
    is_closure     = _kw_match(plain, REJECTION_KW)
    is_emoji_only  = _is_emoji_or_reaction_only(plain)
    is_question    = ("?" in plain) or _kw_match(plain, QUESTION_LEADIN_KW)
    is_request     = _kw_match(plain, REQUEST_LEADIN_KW)
    is_opportunity = _kw_match(plain, POSITIVE_KW)

    is_substantive = (
        len(plain) >= 8
        and not is_emoji_only
        and (is_question or is_request or _kw_match(plain, REQUEST_SUBSTANTIVE_KW))
        and not (is_ack and not (is_question or is_request))
    )

    last_sender_type = "me" if last_sender_is_me else "other"
    eligible = (not last_sender_is_me) and is_substantive and not is_auto
    meaningful_history = bool(has_interview_signal or has_cv_signal or is_valuable_persona)

    manual_review = False
    needs_my_response = False

    if not eligible:
        needs_my_response = False
        if last_sender_is_me:
            confidence, reason = "NONE", "I sent the last message — no reply owed"
        elif is_auto:
            confidence, reason = "NONE", "last message is an automatic reply / career-site redirect"
            if is_substantive:
                manual_review = True
                reason = "auto-reply pattern but message also contains a possible request — verify manually"
        elif is_emoji_only:
            confidence, reason = "NONE", "emoji/reaction-only message"
        elif is_ack:
            confidence, reason = "NONE", "generic acknowledgement only, no actionable request"
        elif is_closure:
            confidence, reason = "NONE", "process closure / rejection message, no separate actionable request"
        else:
            confidence, reason = "NONE", "no substantive actionable signal in last message"
    else:
        if band in ("CURRENT", "RECENT", "AGING"):
            needs_my_response = True
            confidence = "HIGH"
            reason = f"substantive question/request from them ({band.lower()}, within 90 days)"
        elif band == "STALE":
            if meaningful_history:
                needs_my_response = True
                confidence = "MEDIUM"
                manual_review = True
                reason = "substantive signal 91-180 days old with prior meaningful interaction — verify still relevant"
            else:
                needs_my_response = False
                confidence = "LOW"
                manual_review = True
                reason = "stale unanswered (91-180 days) — not treated as urgent by default"
        else:  # HISTORICAL (> 180 days)
            if meaningful_history:
                needs_my_response = True
                confidence = "LOW"
                manual_review = True
                reason = "historical reactivation candidate (>180 days) — prior interview/CV/recruiter interaction"
            else:
                needs_my_response = False
                confidence = "NONE"
                reason = "historical (>180 days), no prior meaningful process — not flagged as needing reply"

    # Mixed-signal messages (substantive wording alongside an ack/closure phrase)
    # that did not already trigger manual review get flagged for a human look.
    # Only applies when THEY sent the last message — if I sent it, there is no
    # open response question regardless of ack/closure/substantive wording.
    if (not manual_review and not needs_my_response and not last_sender_is_me
            and is_substantive and (is_ack or is_closure) and not is_auto):
        manual_review = True
        reason = reason + "; mixed signals — flagged for manual review"

    base = 0
    if is_substantive:  base += 25
    if is_question:     base += 8
    if is_request:      base += 8
    if is_opportunity:  base += 7
    base += _RECENCY_BAND_BONUS.get(band, 0)
    if is_auto:         base -= 15
    if is_ack:          base -= 10
    if is_closure:       base -= 10
    if is_emoji_only:   base -= 15
    base = max(0, base)

    response_intent_score = max(0, min(100, base + _CONFIDENCE_TIER_BONUS.get(confidence, 0)))

    return {
        "needs_my_response":               needs_my_response,
        "needs_response_confidence":       confidence,
        "needs_response_reason":           reason,
        "response_intent_score":           int(response_intent_score),
        "last_message_is_substantive":     is_substantive,
        "last_message_is_question":        is_question,
        "last_message_is_request":         is_request,
        "last_message_is_auto_reply":      is_auto,
        "last_message_is_generic_ack":     is_ack,
        "last_message_is_process_closure": is_closure,
        "last_message_is_opportunity_signal": is_opportunity,
        "manual_review_required":          manual_review,
        "last_sender_type":                last_sender_type,
        "conversation_recency_band":       band,
    }


# ══════════════════════════════════════════════════════════════════════════
# V8 — Multi-dimensional conversation state engine
# (CLAUDE_MESSAGE_STATE_V8.md Parts 1-13)
#
# The V6 _response_intelligence() above only ever looked at the single TRUE
# last message. That conflates five independent things: relationship value,
# immediate action urgency, reply obligation, process status, and
# reactivation timing. A valuable recruiter relationship that ended in
# REJECTED_CLOSED, or a message that contains "?" only because the OTHER
# person is explaining a geographic/residency restriction, was getting
# flagged as an urgent "Needs my response" — a false positive.
#
# This engine scans the conversation's MEANINGFUL turns (excluding emoji/
# generic-ack-only messages) in reverse chronological order, so the most
# recent substantive signal wins, and terminal/blocking states (rejection,
# location/geography/work-auth blocks, talent-pool/career-site redirects,
# auto-replies) take precedence over any question mark or keyword in the
# same message. It also detects "request resolved" — if Mauricio sent a
# substantive reply after the other person's last actionable message, there
# is no outstanding reply_obligation regardless of who technically sent the
# very last message in the thread.
# ══════════════════════════════════════════════════════════════════════════

# ── Part 9 — strengthened multilingual rejection/closure detection ───────────
TERMINAL_REJECTION_KW = REJECTION_KW + [
    "move forward with another candidate", "moving forward with another candidate",
    "another candidate", "experience aligned more closely", "tough decision",
    "role has been filled", "position has been filled",
    "client chose another candidate", "client selected another candidate",
    "seguimos com outro candidato", "avançamos com outro candidato",
    "avancamos com outro candidato", "optamos por outro candidato",
    "não avançaremos", "nao avancaremos", "posição foi preenchida",
    "posicao foi preenchida", "vaga foi encerrada", "processo encerrado",
    "avanzamos con otro candidato", "hemos decidido continuar con otro candidato",
    "posición cubierta", "posicion cubierta", "proceso cerrado",
    "no continuaremos",
]
_OTHER_CANDIDATE_KW = [
    "another candidate", "outro candidato", "otro candidato",
    "client chose another candidate", "client selected another candidate",
    "seguimos com outro candidato", "avançamos com outro candidato",
    "avanzamos con otro candidato",
]

# ── Part 10 — location / eligibility / geographic / work-authorization blocks ─
LOCATION_ELIGIBILITY_KW = [
    "reside in portugal", "residency in portugal", "fiscal residency",
    "residência fiscal", "residencia fiscal", "must reside in", "must be located in",
    "hybrid role requires", "requires local residence", "local residence",
    "morar em portugal", "residir em portugal", "morar na região",
    "based in portugal", "based locally", "residir localmente",
]
GEOGRAPHIC_RESTRICTION_KW = [
    "only hiring in india", "not hiring outside india", "hiring only in india",
    "não contratamos fora", "nao contratamos fora", "solo contratamos en",
    "local candidates only", "not hiring outside", "hiring locally only",
    "candidatos locales solamente", "somente candidatos locais",
]
WORK_AUTH_KW = [
    "work authorization", "visa sponsorship", "sponsorship unavailable",
    "sponsorship is not available", "autorização de trabalho",
    "autorizacao de trabalho", "necesita autorización", "necesita autorizacion",
    "requer visto", "no visa sponsorship",
]

# ── Part 11 — talent pool / career site redirects (broader than AUTO_REPLY_KW) ─
TALENT_POOL_KW = [
    "talent database", "talent pool", "register your profile",
    "join our talent", "linktree", "register in our database",
    "mentoring is available", "cadastre seu perfil", "banco de talentos",
    "base de talentos", "regístrate en nuestra base", "registrate en nuestra base",
]
CAREER_SITE_KW = [
    "career site", "apply via our career site", "apply through our",
    "apply on our", "check our job board", "job board",
    "site de carreiras", "portal de vagas", "portal de carreras",
]

# ── Part 13 — awaiting-update / active-interview detection ───────────────────
AWAITING_UPDATE_KW = [
    "will get back to you", "get back to you", "let you know next week",
    "update you soon", "keep you posted", "will follow up", "will circle back",
    "aguarde retorno", "te aviso", "os mantendo informado", "mantendo voce informado",
    "te mantendo informado", "en breve te contactamos", "te contactaremos pronto",
]
INTERVIEW_ACTIVE_KW = [
    "second interview", "next round", "move forward with the interview",
    "schedule a call", "schedule the interview", "technical interview",
    "interview process", "próxima etapa", "proxima etapa", "segunda entrevista",
    "siguiente etapa", "segunda entrevista",
]

# Terminal/blocking states — Part 4: these OVERRIDE reply_obligation to NONE
# unless a separate unresolved actionable request follows.
TERMINAL_STATES = {
    "REJECTED_CLOSED", "LOCATION_ELIGIBILITY_BLOCKED",
    "GEOGRAPHIC_HIRING_RESTRICTION", "WORK_AUTHORIZATION_BLOCKED",
    "TALENT_POOL_REDIRECT", "CAREER_SITE_REDIRECT", "AUTO_REPLY_ONLY",
    "GENERIC_ACKNOWLEDGEMENT",
}

# process_state -> external_action_type (Part 11)
_EXTERNAL_ACTION_BY_STATE = {
    "TALENT_POOL_REDIRECT": "JOIN_TALENT_POOL",
    "CAREER_SITE_REDIRECT": "APPLY_CAREER_SITE",
}

# process_state -> reactivation_window_days, next_action_date offset (Part 13)
_REACTIVATION_WINDOW_DAYS = {
    "REJECTED_CLOSED":                 75,   # 60-90
    "LOCATION_ELIGIBILITY_BLOCKED":     90,
    "GEOGRAPHIC_HIRING_RESTRICTION":   150,   # 120-180
    "WORK_AUTHORIZATION_BLOCKED":      120,
    "TALENT_POOL_REDIRECT":             75,   # 60-90
    "CAREER_SITE_REDIRECT":             75,
    "DORMANT_WARM":                      0,   # eligible now if cooldown passed
    "WARM_RELATIONSHIP_NO_ACTIVE_ROLE": 30,
}


def _is_meaningful_message(text: str) -> bool:
    """Part 6 — excludes emoji/reaction-only and short generic-acknowledgement
    messages from conversation-state determination (still counted for
    total_messages etc., just not used to drive state)."""
    plain = (text or "").strip()
    if not plain:
        return False
    if _is_emoji_or_reaction_only(plain):
        return False
    if len(plain) <= 60 and _kw_match_wb(plain, GENERIC_ACK_KW):
        return False
    return True


def _build_meaningful_turns(group: pd.DataFrame) -> list[dict]:
    """Part 6 — ordered list of {sender, text, date, meaningful} for every
    message in the conversation (chronological)."""
    turns = []
    for _, row in group.iterrows():
        text = strip_html(row.get("content", "") or "")
        turns.append({
            "sender": "me" if row["is_me_sender"] else "other",
            "text": text,
            "date": row.get("date_parsed"),
            "meaningful": _is_meaningful_message(text),
        })
    return turns


def _is_strong_ask(text: str) -> bool:
    """A clearly actionable, specific request — CV/scheduling/interview/explicit
    availability confirmation. Deliberately NARROWER than REQUEST_SUBSTANTIVE_KW
    (which contains generic words like "available" that can appear inside an
    unrelated sentence, e.g. "mentoring is also available") so it's safe to use
    for escalating a terminal/blocking state (Part 4)."""
    return bool(
        _kw_match(text, CV_KW)
        or _kw_match(text, INTERVIEW_ACTIVE_KW)
        or _kw_match_wb(text, ["schedule", "confirm interview", "disponibilidade", "disponibilidad"])
        or (("?" in text) and _kw_match_wb(text, [
            "available", "availability", "when are you available", "what time",
            "interested", "are you interested",
            "disponibilidade", "disponibilidad", "tem interesse", "interesado",
        ]))
    )


def _analyze_conversation_state(
    turns: list[dict],
    is_valuable_persona: bool,
    has_interview_hist: bool,
    has_cv_hist: bool,
    priority_score: float,
    market_value: bool,
    messages_from_other: int,
    days_since_last: int,
) -> dict:
    """
    Parts 1-13 — the authoritative conversation-state classifier. Scans
    meaningful turns in reverse (most recent first) so the latest relevant
    signal wins, detects resolved requests (Part 5), and gives terminal/
    blocking states precedence over reply_obligation (Part 4).
    """
    meaningful = [t for t in turns if t["meaningful"]]
    evidence: list[str] = []

    latest_overall  = meaningful[-1] if meaningful else (turns[-1] if turns else None)
    latest_other    = next((t for t in reversed(meaningful) if t["sender"] == "other"), None)
    latest_mine     = next((t for t in reversed(meaningful) if t["sender"] == "me"), None)

    # Part 5 — resolved-request detection: if the latest meaningful turn is
    # mine, or my latest meaningful reply is not older than their latest
    # meaningful message, the request is resolved.
    request_resolved = False
    if latest_overall is not None and latest_overall["sender"] == "me":
        request_resolved = True
    elif latest_mine and latest_other and latest_mine["date"] and latest_other["date"]:
        request_resolved = latest_mine["date"] >= latest_other["date"]

    # ── Scan meaningful turns in reverse for the most recent classifying signal ──
    process_state = None
    closure_reason = None
    triggering_text = ""
    for t in reversed(meaningful):
        text = t["text"]
        if _kw_match(text, TERMINAL_REJECTION_KW):
            process_state = "REJECTED_CLOSED"
            closure_reason = "OTHER_CANDIDATE_SELECTED" if _kw_match(text, _OTHER_CANDIDATE_KW) else "PROCESS_CLOSED"
            evidence.append("REJECTION_KW"); triggering_text = text; break
        if _kw_match(text, LOCATION_ELIGIBILITY_KW):
            process_state = "LOCATION_ELIGIBILITY_BLOCKED"
            evidence.append("LOCATION_BLOCK_KW"); triggering_text = text; break
        if _kw_match(text, GEOGRAPHIC_RESTRICTION_KW):
            process_state = "GEOGRAPHIC_HIRING_RESTRICTION"
            evidence.append("GEO_RESTRICTION_KW"); triggering_text = text; break
        if _kw_match(text, WORK_AUTH_KW):
            process_state = "WORK_AUTHORIZATION_BLOCKED"
            evidence.append("WORK_AUTH_KW"); triggering_text = text; break
        if _kw_match(text, TALENT_POOL_KW):
            process_state = "TALENT_POOL_REDIRECT"
            evidence.append("TALENT_POOL_KW"); triggering_text = text; break
        if _kw_match(text, CAREER_SITE_KW):
            process_state = "CAREER_SITE_REDIRECT"
            evidence.append("CAREER_SITE_KW"); triggering_text = text; break
        if _kw_match(text, AUTO_REPLY_KW):
            process_state = "AUTO_REPLY_ONLY"
            evidence.append("AUTO_REPLY_KW"); triggering_text = text; break
        if _kw_match(text, INTERVIEW_ACTIVE_KW):
            process_state = "INTERVIEW_PIPELINE"
            evidence.append("INTERVIEW_ACTIVE_KW"); triggering_text = text; break
        if _kw_match(text, CV_KW):
            process_state = "CV_REQUESTED"
            evidence.append("CV_REQUEST_KW"); triggering_text = text; break
        if _kw_match(text, AWAITING_UPDATE_KW):
            process_state = "AWAITING_RECRUITER_UPDATE"
            evidence.append("AWAITING_UPDATE_KW"); triggering_text = text; break
        if t["sender"] == "other" and _kw_match_wb(t["text"], GENERIC_ACK_KW):
            process_state = "GENERIC_ACKNOWLEDGEMENT"
            evidence.append("GENERIC_ACK_KW"); triggering_text = text; break

    has_opportunity_overall = has_interview_hist or has_cv_hist or any(
        _kw_match(t["text"], POSITIVE_KW) for t in meaningful
    )

    if process_state is None:
        if not meaningful:
            process_state = "NO_RESPONSE" if messages_from_other == 0 else "LOW_VALUE"
            evidence.append("NO_MEANINGFUL_TURNS")
        elif has_opportunity_overall and days_since_last <= 30:
            process_state = "ACTIVE_OPPORTUNITY"; evidence.append("OPPORTUNITY_RECENT")
        elif has_opportunity_overall and days_since_last <= 365:
            process_state = "WARM_RELATIONSHIP_NO_ACTIVE_ROLE"; evidence.append("OPPORTUNITY_AGING")
        elif has_opportunity_overall:
            process_state = "DORMANT_WARM"; evidence.append("OPPORTUNITY_OLD")
        elif messages_from_other == 0:
            process_state = "NO_RESPONSE"; evidence.append("NO_REPLY_FROM_OTHER")
        else:
            process_state = "LOW_VALUE"; evidence.append("NO_SIGNAL")

    # ── Part 3/4 — reply_obligation ───────────────────────────────────────────
    is_terminal = process_state in TERMINAL_STATES
    triggered_by_latest_other = bool(latest_other and triggering_text and triggering_text == latest_other["text"])

    if is_terminal:
        # Terminal/blocking states default to NONE. Only escalate to AMBIGUOUS
        # (never straight to CONFIRMED) when the SAME latest-other message also
        # carries a strong, SPECIFIC separate actionable request (not just a
        # loose generic word like "available" appearing incidentally) and it
        # isn't resolved yet.
        if (triggered_by_latest_other and not request_resolved
                and _is_strong_ask(triggering_text)
                and process_state not in ("AUTO_REPLY_ONLY", "GENERIC_ACKNOWLEDGEMENT")):
            reply_obligation = "AMBIGUOUS"
            evidence.append("TERMINAL_WITH_POSSIBLE_SEPARATE_ASK")
        else:
            reply_obligation = "NONE"
    elif request_resolved:
        reply_obligation = "NONE"
        evidence.append("REQUEST_RESOLVED")
    elif latest_other is not None:
        text = latest_other["text"]
        strong_ask = _is_strong_ask(text)
        weak_ask = _kw_match(text, REQUEST_SUBSTANTIVE_KW) or "?" in text
        if strong_ask:
            reply_obligation = "CONFIRMED"; evidence.append("UNRESOLVED_STRONG_ASK")
        elif weak_ask:
            reply_obligation = "LIKELY"; evidence.append("UNRESOLVED_WEAK_ASK")
        else:
            reply_obligation = "NONE"; evidence.append("NO_ACTIONABLE_ASK")
    else:
        reply_obligation = "NONE"

    # Recency decay (mirrors V6 Part 4 band logic) — old unresolved asks are
    # downgraded rather than left urgent forever, matching V6's existing behavior.
    if reply_obligation in ("CONFIRMED", "LIKELY") and days_since_last > 90:
        meaningful_history = has_interview_hist or has_cv_hist or is_valuable_persona
        if not meaningful_history:
            reply_obligation = "AMBIGUOUS" if reply_obligation == "CONFIRMED" else "NONE"
            evidence.append("RECENCY_DECAY_90D")

    # ── Part 12 — relationship_value_score (independent of urgency) ──────────
    rel_score = 0.0
    if is_valuable_persona:               rel_score += 25
    if has_interview_hist:                rel_score += 20
    if has_cv_hist:                        rel_score += 15
    if messages_from_other >= 2:          rel_score += 10
    if market_value:                      rel_score += 10
    rel_score += min(20, (priority_score or 0) * 0.20)
    if process_state in ("REJECTED_CLOSED", "CV_REQUESTED", "INTERVIEW_PIPELINE") and has_opportunity_overall:
        rel_score += 10  # reached a real process — reusable relationship
    relationship_value_score = int(max(0, min(100, rel_score)))

    # ── Part 12 — immediate_action_score (independent of relationship value) ──
    if reply_obligation == "CONFIRMED":     act_score = 60.0
    elif reply_obligation == "LIKELY":      act_score = 35.0
    elif reply_obligation == "AMBIGUOUS":   act_score = 15.0
    else:                                    act_score = 0.0
    if process_state == "INTERVIEW_PIPELINE":            act_score += 15
    if process_state == "AWAITING_RECRUITER_UPDATE":     act_score += 5
    if days_since_last <= 7:    act_score += 10
    elif days_since_last <= 30: act_score += 5
    # Terminal/blocking states are hard-capped low regardless of any bonus above.
    if is_terminal:
        act_score = min(act_score, 20.0)
    immediate_action_score = int(max(0, min(100, act_score)))

    action_urgency = (
        "HIGH" if immediate_action_score >= 60 else
        "MEDIUM" if immediate_action_score >= 30 else
        "LOW" if immediate_action_score >= 10 else
        "NONE"
    )

    # ── Part 13 — next_action_date / reactivation_window_days ────────────────
    today = date.today()
    if reply_obligation in ("CONFIRMED",) or process_state in ("INTERVIEW_PIPELINE", "CV_REQUESTED"):
        next_action_date = today
        reactivation_window_days = 0
    elif process_state == "ACTIVE_OPPORTUNITY":
        # Recent positive-keyword mention but no specific active step detected —
        # warm, not "today"-urgent.
        next_action_date = today + timedelta(days=7)
        reactivation_window_days = 7
    elif process_state == "AWAITING_RECRUITER_UPDATE":
        next_action_date = today + timedelta(days=6)
        reactivation_window_days = 6
    elif process_state in _REACTIVATION_WINDOW_DAYS:
        win = _REACTIVATION_WINDOW_DAYS[process_state]
        next_action_date = today + timedelta(days=win)
        reactivation_window_days = win
    elif process_state == "NO_RESPONSE":
        reactivation_window_days = 14
        next_action_date = today + timedelta(days=14)
    else:
        reactivation_window_days = 0
        next_action_date = today

    external_action_type = _EXTERNAL_ACTION_BY_STATE.get(process_state, "NONE")

    # ── relationship_state (simple, descriptive) ──────────────────────────────
    if is_valuable_persona and (has_interview_hist or has_cv_hist or messages_from_other >= 2):
        relationship_state = "WARM_RECRUITER_RELATIONSHIP" if is_valuable_persona else "WARM_CONTACT"
    elif is_valuable_persona:
        relationship_state = "PROFESSIONAL_CONTACT"
    else:
        relationship_state = "GENERAL_CONTACT"

    _weak_evidence = {"NO_SIGNAL", "NO_MEANINGFUL_TURNS", "NO_REPLY_FROM_OTHER", "OPPORTUNITY_AGING", "OPPORTUNITY_OLD"}
    if evidence and evidence[0] not in _weak_evidence:
        confidence = "HIGH"
    elif process_state != "LOW_VALUE":
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    if reply_obligation == "AMBIGUOUS":
        confidence = "LOW"

    return {
        "process_state":               process_state,
        "relationship_state":           relationship_state,
        "reply_obligation":             reply_obligation,
        "action_urgency":               action_urgency,
        "closure_reason":               closure_reason or "",
        "next_action_date":             str(next_action_date),
        "reactivation_window_days":     reactivation_window_days,
        "relationship_value_score":     relationship_value_score,
        "immediate_action_score":       immediate_action_score,
        "conversation_state_confidence": confidence,
        "state_evidence_codes":         ",".join(evidence),
        "external_action_type":         external_action_type,
        "request_resolved":             request_resolved,
        "last_actionable_response_date": str(latest_mine["date"].date()) if (latest_mine and latest_mine["date"]) else "",
        "last_actionable_request_date":  str(latest_other["date"].date()) if (latest_other and latest_other["date"]) else "",
    }


def _cooldown_state(process_state: str, days_since_last: int) -> str:
    """Part 16 — process-closed cooldown. Only applies to REJECTED_CLOSED."""
    if process_state != "REJECTED_CLOSED":
        return ""
    d = days_since_last if isinstance(days_since_last, int) else 9999
    if d <= 30:
        return "NO_ACTION_COOLDOWN"
    if d <= 59:
        return "MONITOR"
    if d <= 90:
        return "REACTIVATION_ELIGIBLE"
    return "REACTIVATE_IF_STRATEGIC"


def _lead_category_v8(process_state: str, reply_obligation: str, cooldown: str) -> str:
    """Part 15 — Lead Reactivation KPI-card categories, driven by the new
    conversation-state engine instead of last-message-only heuristics."""
    if reply_obligation == "CONFIRMED":
        return "Needs my response — Confirmed"
    if reply_obligation == "LIKELY":
        return "Needs my response — Likely"
    if reply_obligation == "AMBIGUOUS":
        return "Ambiguous — Review"
    if process_state in ("INTERVIEW_PIPELINE", "CV_REQUESTED"):
        return "Active Interview Pipeline"
    if process_state == "AWAITING_RECRUITER_UPDATE":
        return "Awaiting Recruiter Update"
    if process_state == "ACTIVE_OPPORTUNITY":
        # Recent positive-keyword mention but no specific active step (no
        # interview/CV request/recruiter-update signal) — genuinely warm, not
        # a confirmed active pipeline step.
        return "Warm reactivation"
    if process_state == "REJECTED_CLOSED":
        return "Reactivate This Month" if cooldown == "REACTIVATION_ELIGIBLE" else "Rejected / Closed"
    if process_state in ("LOCATION_ELIGIBILITY_BLOCKED", "GEOGRAPHIC_HIRING_RESTRICTION", "WORK_AUTHORIZATION_BLOCKED"):
        return "Location / Eligibility Blocked"
    if process_state in ("TALENT_POOL_REDIRECT", "CAREER_SITE_REDIRECT"):
        return "Talent Pool / Career Site"
    if process_state == "WARM_RELATIONSHIP_NO_ACTIVE_ROLE":
        return "Warm reactivation"
    if process_state == "DORMANT_WARM":
        return "Reactivate This Month" if cooldown == "REACTIVATION_ELIGIBLE" else "Dormant warm"
    if process_state == "NO_RESPONSE":
        return "No response"
    if process_state == "GENERIC_ACKNOWLEDGEMENT":
        return "Closed / no action"
    return "Ignore"


_INTENT_LABEL_BY_STATE = {
    "REJECTED_CLOSED": "Process closed",
    "LOCATION_ELIGIBILITY_BLOCKED": "Location/residency constraint",
    "GEOGRAPHIC_HIRING_RESTRICTION": "Geographic hiring restriction",
    "WORK_AUTHORIZATION_BLOCKED": "Work authorization constraint",
    "TALENT_POOL_REDIRECT": "Talent pool redirect",
    "CAREER_SITE_REDIRECT": "Auto reply",
    "AUTO_REPLY_ONLY": "Auto reply",
    "AWAITING_RECRUITER_UPDATE": "Awaiting recruiter update",
}


def _sanitized_intent_label(ri: dict, has_interview_signal: bool, has_cv_signal: bool,
                             messages_from_other: int, process_state: str = "") -> str:
    """Very short, sanitized intent label for the Lead Reactivation table (Part 7)."""
    state_label = _INTENT_LABEL_BY_STATE.get(process_state)
    if state_label:
        return state_label
    if ri["last_message_is_process_closure"]:
        return "Process closed"
    if ri["last_message_is_auto_reply"]:
        return "Auto reply"
    if has_interview_signal and (ri["last_message_is_question"] or ri["last_message_is_request"]):
        return "Interview scheduling"
    if has_cv_signal:
        return "Asked for CV"
    if ri["last_message_is_request"] and ("available" in ri["needs_response_reason"] or ri["last_message_is_question"]):
        return "Asked for availability"
    if ri["last_message_is_generic_ack"]:
        return "Generic acknowledgement"
    if ri["last_message_is_opportunity_signal"]:
        return "Opportunity discussion"
    if messages_from_other == 0:
        return "No response"
    return "General message"


def _lead_category_v6(
    conversation_status: str,
    lead_temperature: str,
    has_opportunity: bool,
    ri: dict,
) -> str:
    """
    Refined Lead Reactivation category taxonomy (Part 5). Built on top of the
    legacy conversation_status (kept unchanged for outreach_adjusted_scoring.py)
    plus the new response-intelligence decision.
    """
    if ri["needs_my_response"]:
        return "Needs my response — Confirmed" if ri["needs_response_confidence"] == "HIGH" else "Needs my response — Likely"

    if ri["manual_review_required"] and conversation_status not in (
        "Rejected / closed process", "Auto-reply / career site redirect",
    ):
        return "Ambiguous — Review"

    if conversation_status == "Follow-up due":
        return "Follow-up candidate"
    if conversation_status == "Warm lead":
        return "Hot reactivation" if lead_temperature == "Hot" else "Warm reactivation"
    if conversation_status == "Dormant warm lead":
        return "Dormant warm"
    if conversation_status == "Auto-reply / career site redirect":
        return "Career site follow-up"
    if conversation_status == "Rejected / closed process":
        return "Previous process reusable" if has_opportunity else "Closed / no action"
    if conversation_status == "No response":
        return "No response"
    return "Ignore"


def _determine_status(
    last_sender_is_me: bool,
    days_since: int,
    signals: dict,
    is_valuable_persona: bool,
    other_replied: bool,
    messages_from_other: int,
    last_their_msg_substantive: bool,
    last_their_msg_is_auto: bool,
) -> str:
    """
    Strict status determination.
    'Follow-up due' requires: positive signal + recruiter persona + 7-120 days.
    'Needs my response' requires: substantive non-auto last message from them.
    """
    has_opportunity = signals["positive"] or signals["cv_request"] or signals["interview"]

    # 1. Needs my response — they sent something real and I haven't replied
    if (not last_sender_is_me
            and last_their_msg_substantive
            and not last_their_msg_is_auto):
        return "Needs my response"

    # 2. Rejected
    if signals["rejection"] and not has_opportunity:
        return "Rejected / closed process"

    # 3. Rejected + opportunity = Previous process reusable
    if signals["rejection"] and has_opportunity:
        return "Rejected / closed process"

    # 4. Follow-up due (genuinely actionable: I sent last, there's value, recent-ish)
    if (last_sender_is_me
            and 7 <= days_since <= 120
            and has_opportunity
            and (is_valuable_persona or signals["interview"])
            and not signals["rejection"]):
        return "Follow-up due"

    # 5. Warm lead (mutual exchange, positive, recent)
    if (has_opportunity
            and other_replied
            and days_since <= 30
            and not signals["rejection"]):
        return "Warm lead"

    # 6. Dormant warm lead (positive but old)
    if (has_opportunity
            and other_replied
            and 30 < days_since <= 365
            and not signals["rejection"]):
        return "Dormant warm lead"

    # 7. Auto-reply / career site (no positive signal)
    if signals["auto_reply"] and not has_opportunity:
        return "Auto-reply / career site redirect"

    # 8. Auto-reply + applied/positive = Career site with submission
    if signals["auto_reply"] and has_opportunity:
        return "Auto-reply / career site redirect"

    # 9. No response (I sent, recruiter, they never replied, not too old)
    if (messages_from_other == 0
            and is_valuable_persona
            and 14 <= days_since <= 180):
        return "No response"

    # 10. Everything else
    return "Low value / ignore"


def _determine_temperature(status: str, has_opportunity: bool,
                            is_valuable_persona: bool, days_since: int) -> str:
    if status == "Needs my response" and (has_opportunity or is_valuable_persona):
        return "Hot"
    if status == "Needs my response":
        return "Warm"
    if status == "Follow-up due" and has_opportunity and is_valuable_persona:
        return "Warm"
    if status == "Follow-up due":
        return "Neutral"
    if status == "Warm lead":
        return "Warm"
    if status == "Dormant warm lead":
        return "Warm"
    if status == "Auto-reply / career site redirect" and has_opportunity:
        return "Neutral"
    if status == "Auto-reply / career site redirect":
        return "Cold"
    if status == "Rejected / closed process":
        return "Cold"
    if status == "No response":
        return "Cold"
    return "Ignore"


# NOTE: lead_category is now computed by _lead_category_v6() (see Part 5 of the
# V6 patch), which builds on conversation_status + the new response-intelligence
# decision instead of temperature/has_opportunity alone.


def _recommended_action(status: str, has_cv: bool, has_interview: bool, is_auto: bool) -> tuple:
    if status == "Needs my response":
        return (
            "Reply now — they were the last sender",
            "Reply directly based on their last message. Prioritize this before any new outreach.",
        )
    if status == "Follow-up due":
        if has_interview:
            return (
                "Follow up on interview / screening discussion",
                "Quick follow-up: I wanted to reconnect about the opportunity we discussed. I remain very interested in Data Engineering roles — happy to chat.",
            )
        if has_cv:
            return (
                "Follow up after CV submission",
                "Quick follow-up on my application. I'm available for a call if you'd like to discuss my Data Engineering background.",
            )
        return (
            "Follow up with updated availability",
            "Quick follow-up: I'm currently open to remote Data Engineering roles across LATAM/US time zones. Happy to share my updated profile.",
        )
    if status == "Warm lead":
        return (
            "Ask if they have new Data Engineering roles",
            "We spoke previously about opportunities. I wanted to reconnect — I'm currently open to remote USD/LATAM Data Engineering roles.",
        )
    if status == "Dormant warm lead":
        return (
            "Reactivate with updated availability",
            "We spoke previously about Data Engineering opportunities. I wanted to reconnect because I'm currently open to remote USD/LATAM roles.",
        )
    if status == "Auto-reply / career site redirect":
        return (
            "Submit CV to talent database, then follow up in 2 weeks",
            "I reviewed the career site and submitted my profile. If any Data Engineering / Cloud Data role opens, I'd be happy to be considered.",
        )
    if status == "Rejected / closed process":
        return (
            "Reconnect in 60 days for future roles",
            "Thanks again for the previous process. I'd be happy to stay in touch for future Data Engineering roles (Azure, AWS, Databricks, SQL, ETL).",
        )
    if status == "No response":
        return (
            "Send soft reactivation message",
            "Quick follow-up in case this is relevant: I'm open to remote Data Engineering roles across LATAM/US time zones.",
        )
    return (
        "Do not prioritize — no actionable signal",
        "Low priority — skip unless you have spare outreach capacity.",
    )


def _compute_reactivation_score(
    days_since: int,
    signals: dict,
    status: str,
    temperature: str,
    persona_score: float,
    is_valuable_persona: bool,
    other_replied: bool,
    market_value: bool,
) -> int:
    score = 0
    has_opportunity = signals["positive"] or signals["cv_request"] or signals["interview"]

    # Base from existing connection priority (max 20 pts)
    score += min(20, persona_score * 0.20)

    # Opportunity signals
    if has_opportunity:        score += 25
    if signals["cv_request"]:  score += 8
    if signals["interview"]:   score += 12

    # Penalty
    if signals["rejection"]:            score -= 15
    if signals["auto_reply"] and not has_opportunity: score -= 25

    # Recency
    if days_since < 7:      score += 12
    elif days_since < 14:   score += 8
    elif days_since < 30:   score += 4
    elif days_since > 180:  score -= 8

    # Engagement
    if other_replied:          score += 8
    if is_valuable_persona:    score += 8
    if market_value:           score += 6

    # Status boost
    if status == "Needs my response":  score += 15
    if status == "Follow-up due":      score += 8
    if temperature == "Hot":           score += 5

    return max(0, min(100, int(score)))


def _build_url_name_lookup(classified_df: pd.DataFrame | None) -> tuple:
    """Build O(1) lookup dicts from classified_df."""
    url_lookup  = {}
    name_lookup = {}
    if classified_df is None or classified_df.empty:
        return url_lookup, name_lookup
    for _, row in classified_df.iterrows():
        url  = str(row.get("url", "") or "").rstrip("/").lower()
        name = str(row.get("full_name", "") or "").lower().strip()
        if url:
            url_lookup[url] = row
        if name and name not in name_lookup:
            name_lookup[name] = row
    return url_lookup, name_lookup


def build_conversation_intelligence(
    msgs: pd.DataFrame,
    classified_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Builds one row per conversation. Join with classified_df first, then status."""
    today = date.today()

    # Build O(1) lookup dicts
    url_lookup, name_lookup = _build_url_name_lookup(classified_df)

    rows = []

    for conv_id, group in msgs.groupby("conversation_id"):
        group = group.sort_values("date_parsed").reset_index(drop=True)
        if len(group) == 0:
            continue

        my_msgs   = group[group["is_me_sender"]]
        them_msgs = group[~group["is_me_sender"]]

        # Identify other person
        if not them_msgs.empty:
            other_name        = them_msgs.iloc[0]["from"]
            other_profile_url = them_msgs.iloc[0]["sender_profile_url"]
        elif not my_msgs.empty:
            other_name        = my_msgs.iloc[0]["to"]
            other_profile_url = _first_url(my_msgs.iloc[0].get("recipient_profile_urls", ""))
        else:
            continue

        if _is_me(other_name, other_profile_url):
            other_name = group.iloc[0]["to"] if not group.empty else ""

        first_date = group.iloc[0]["date_parsed"]
        last_date  = group.iloc[-1]["date_parsed"]
        last_sender_is_me = group.iloc[-1]["is_me_sender"]

        days_since = 9999
        if last_date:
            days_since = (today - last_date.date()).days

        total_messages      = len(group)
        messages_from_me    = len(my_msgs)
        messages_from_other = len(them_msgs)
        other_replied       = messages_from_other > 0

        # Join with classified_df (O(1) lookup)
        url_clean = (other_profile_url or "").rstrip("/").lower()
        match_row = (url_lookup.get(url_clean)
                     if url_clean else None)
        if match_row is None and other_name:
            match_row = name_lookup.get(other_name.lower().strip())

        persona          = ""
        company_clean    = ""
        position_clean   = ""
        strategic_market = ""
        priority_score   = 0.0
        is_valuable      = False
        market_value     = False

        if match_row is not None:
            persona          = str(match_row.get("persona", "") or "")
            company_clean    = str(match_row.get("company_clean", "") or "")
            position_clean   = str(match_row.get("position_clean", "") or "")
            strategic_market = str(match_row.get("market_v2", match_row.get("strategic_market", "")) or "")
            priority_score   = float(match_row.get("priority_score", 0) or 0)
            is_valuable      = persona in RECRUITER_PERSONAS
            market_value     = strategic_market in {
                "LATAM_USD", "US_CANADA_NEARSHORE", "SPAIN_EU",
                "EUROPE", "GLOBAL_STAFFING",
            }

        # Compute signals
        all_content = " ".join(group["content"].fillna("").tolist())
        signals = _score_content(all_content)
        has_opportunity = signals["positive"] or signals["cv_request"] or signals["interview"]

        # Signals on their LAST message specifically
        last_their_content   = (them_msgs.iloc[-1].get("content", "") if not them_msgs.empty else "") or ""
        last_their_plain     = strip_html(last_their_content)
        last_their_is_auto   = _kw_match(last_their_plain, AUTO_REPLY_KW)
        last_their_substantive = _is_substantive(last_their_content, min_chars=40)

        # Direction
        if messages_from_me == 0:
            direction = "Inbound"
        elif messages_from_other == 0:
            direction = "Outbound"
        else:
            direction = "Mutual"

        # Status (strict)
        status = _determine_status(
            last_sender_is_me     = last_sender_is_me,
            days_since            = days_since if days_since < 9999 else 9999,
            signals               = signals,
            is_valuable_persona   = is_valuable,
            other_replied         = other_replied,
            messages_from_other   = messages_from_other,
            last_their_msg_substantive = last_their_substantive,
            last_their_msg_is_auto     = last_their_is_auto,
        )

        temperature = _determine_temperature(status, has_opportunity, is_valuable, days_since)

        # V6 response intelligence — classifies the TRUE last message (whoever sent it).
        # Kept for its last_message_is_* booleans / response_intent_score, but its
        # needs_my_response/confidence/reason are now OVERRIDDEN by the V8 engine
        # below (see module docstring for why last-message-only was insufficient).
        last_message_raw = group.iloc[-1].get("content", "") or ""
        resp_intel = _response_intelligence(
            last_message_raw     = last_message_raw,
            last_sender_is_me    = last_sender_is_me,
            days_since           = days_since if days_since < 9999 else 9999,
            is_valuable_persona  = is_valuable,
            has_interview_signal = signals["interview"],
            has_cv_signal        = signals["cv_request"],
        )

        # V8 — multi-dimensional conversation state (CLAUDE_MESSAGE_STATE_V8.md)
        turns = _build_meaningful_turns(group)
        conv_state = _analyze_conversation_state(
            turns                 = turns,
            is_valuable_persona   = is_valuable,
            has_interview_hist    = signals["interview"],
            has_cv_hist           = signals["cv_request"],
            priority_score        = priority_score,
            market_value          = market_value,
            messages_from_other   = messages_from_other,
            days_since_last       = days_since if days_since < 9999 else 9999,
        )
        cooldown = _cooldown_state(conv_state["process_state"], days_since if days_since < 9999 else 9999)

        # Legacy fields are now DERIVED from the V8 engine — this is the actual
        # fix: reply_obligation correctly accounts for terminal/blocking states
        # and resolved requests, instead of only ever looking at the last message.
        _OBLIGATION_TO_CONFIDENCE = {"CONFIRMED": "HIGH", "LIKELY": "MEDIUM", "AMBIGUOUS": "LOW", "NONE": "NONE"}
        needs_my_response         = conv_state["reply_obligation"] in ("CONFIRMED", "LIKELY")
        needs_response_confidence = _OBLIGATION_TO_CONFIDENCE[conv_state["reply_obligation"]]
        manual_review_required    = conv_state["reply_obligation"] == "AMBIGUOUS"
        needs_response_reason = (
            f"{conv_state['process_state'].replace('_', ' ').title()} — reply_obligation={conv_state['reply_obligation']}"
            f" ({conv_state['state_evidence_codes'] or 'no evidence codes'})"
        )

        lead_category = _lead_category_v8(conv_state["process_state"], conv_state["reply_obligation"], cooldown)
        sanitized_intent_label = _sanitized_intent_label(
            resp_intel, signals["interview"], signals["cv_request"], messages_from_other,
            process_state=conv_state["process_state"],
        )

        action, angle = _recommended_action(
            status, signals["cv_request"], signals["interview"], signals["auto_reply"]
        )

        follow_up_date = ""
        if status == "Follow-up due" and last_date:
            import datetime as _dt
            follow_up_date = str(last_date.date() + _dt.timedelta(days=7))


        reactivation_score = _compute_reactivation_score(
            days_since        = days_since if days_since < 9999 else 9999,
            signals           = signals,
            status            = status,
            temperature       = temperature,
            persona_score     = priority_score,
            is_valuable_persona = is_valuable,
            other_replied     = other_replied,
            market_value      = market_value,
        )

        rows.append({
            "conversation_id":               conv_id,
            "other_person_name":             other_name,
            "other_person_profile_url":      other_profile_url,
            "company_clean":                 company_clean,
            "position_clean":                position_clean,
            "persona":                       persona,
            "strategic_market":              strategic_market,
            "first_message_date":            str(first_date.date()) if first_date else "",
            "last_message_date":             str(last_date.date()) if last_date else "",
            "total_messages":                total_messages,
            "messages_from_me":              messages_from_me,
            "messages_from_other_person":    messages_from_other,
            "last_sender":                   "me" if last_sender_is_me else "them",
            "days_since_last_message":       days_since if days_since < 9999 else "",
            "conversation_direction":        direction,
            "conversation_status":           status,
            "lead_temperature":              temperature,
            "lead_category":                 lead_category,
            "has_positive_signal":           signals["positive"],
            "has_rejection_signal":          signals["rejection"],
            "has_cv_signal":                 signals["cv_request"],
            "has_interview_signal":          signals["interview"],
            "is_auto_reply":                 signals["auto_reply"],
            "recommended_next_action":       action,
            "follow_up_due_date":            follow_up_date,
            "reactivation_priority_score":   reactivation_score,
            "message_angle":                 angle,
            "connection_priority_score":     priority_score,
            # V6 response intelligence (Parts 1-5) — needs_my_response/confidence/
            # reason/manual_review_required are now DERIVED from the V8 engine
            # (see above); the last_message_is_* booleans are still the V6
            # single-message signals, kept for backward compatibility.
            "needs_my_response":             needs_my_response,
            "needs_response_confidence":     needs_response_confidence,
            "needs_response_reason":         needs_response_reason,
            "response_intent_score":         resp_intel["response_intent_score"],
            "last_message_is_substantive":   resp_intel["last_message_is_substantive"],
            "last_message_is_question":      resp_intel["last_message_is_question"],
            "last_message_is_request":       resp_intel["last_message_is_request"],
            "last_message_is_auto_reply":    resp_intel["last_message_is_auto_reply"],
            "last_message_is_generic_ack":   resp_intel["last_message_is_generic_ack"],
            "last_message_is_process_closure": resp_intel["last_message_is_process_closure"],
            "last_message_is_opportunity_signal": resp_intel["last_message_is_opportunity_signal"],
            "manual_review_required":        manual_review_required,
            "last_sender_type":              resp_intel["last_sender_type"],
            "conversation_recency_band":     resp_intel["conversation_recency_band"],
            "sanitized_intent_label":        sanitized_intent_label,
            # V8 — multi-dimensional conversation state (CLAUDE_MESSAGE_STATE_V8.md Part 1)
            "process_state":                 conv_state["process_state"],
            "relationship_state":             conv_state["relationship_state"],
            "reply_obligation":               conv_state["reply_obligation"],
            "action_urgency":                 conv_state["action_urgency"],
            "closure_reason":                 conv_state["closure_reason"],
            "next_action_date":               conv_state["next_action_date"],
            "reactivation_window_days":       conv_state["reactivation_window_days"],
            "relationship_value_score":       conv_state["relationship_value_score"],
            "immediate_action_score":         conv_state["immediate_action_score"],
            "conversation_state_confidence":  conv_state["conversation_state_confidence"],
            "state_evidence_codes":           conv_state["state_evidence_codes"],
            "external_action_type":           conv_state["external_action_type"],
            "request_resolved":               conv_state["request_resolved"],
            "last_actionable_request_date":   conv_state["last_actionable_request_date"],
            "last_actionable_response_date":  conv_state["last_actionable_response_date"],
            "cooldown_state":                 cooldown,
        })

    if not rows:
        return pd.DataFrame()

    df_out = pd.DataFrame(rows)
    df_out = df_out.sort_values("reactivation_priority_score", ascending=False).reset_index(drop=True)
    return df_out


def load_messages(path: Path = MESSAGES_CSV) -> pd.DataFrame | None:
    if not path.exists():
        logger.warning(f"messages.csv not found at {path}")
        return None
    try:
        df = pd.read_csv(path, dtype=str, low_memory=False, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(path, dtype=str, low_memory=False, encoding="latin-1")

    df = _normalize_cols(df)
    df = df.fillna("")
    df["date_parsed"] = df["date"].apply(_parse_date)
    df["is_me_sender"] = df.apply(
        lambda r: _is_me(r.get("from", ""), r.get("sender_profile_url", "")),
        axis=1,
    )

    if "folder" in df.columns:
        df = df[df["folder"].str.upper().isin(["INBOX", "SENT", ""])]
    if "conversation_title" in df.columns:
        df = df[~df["conversation_title"].str.contains("Sponsored", case=False, na=False)]

    logger.info(f"  Loaded {len(df):,} messages from {df['conversation_id'].nunique():,} conversations")
    return df


def run_message_intelligence(classified_df: pd.DataFrame | None = None) -> pd.DataFrame:
    msgs = load_messages()
    if msgs is None or msgs.empty:
        return pd.DataFrame()
    return build_conversation_intelligence(msgs, classified_df=classified_df)
