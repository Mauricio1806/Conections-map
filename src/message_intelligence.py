# -*- coding: utf-8 -*-
"""
message_intelligence.py  (V2 — corrective patch)
==================================================
Reads messages.csv and builds per-conversation intelligence.

Key fixes vs V1:
  - Join with classified_df BEFORE status determination (persona matters)
  - "Follow-up due" requires positive signal + recruiter/TA persona + 7-120 days
  - "Needs my response" requires substantive non-auto-reply last message
  - Old low-value conversations → "Low value / ignore" (not "Follow-up due")
  - Adds lead_category for cleaner segmentation
  - Uses O(1) lookup dict for classified_df join
"""

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.message_sanitizer import sanitize_excerpt, strip_html

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
]

CV_KW = [
    "send your cv", "send me your resume", "curriculum", "currículo",
    "curriculo", "i applied", "realizei minha candidatura",
    "application submitted", "candidatura realizada",
    "talent database", "career site", "submit your profile",
]

INTERVIEW_KW = [
    "interview", "entrevista", "screening", "technical interview",
    "call", "agenda", "calendário", "calendario", "schedule",
    "meeting", "recruiter call", "video call", "video interview",
    "bate-papo", "conversa",
]


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


def _determine_lead_category(status: str, temperature: str, has_opportunity: bool) -> str:
    if status == "Needs my response":
        return "Needs my response"
    if temperature == "Hot" and has_opportunity:
        return "Hot reactivation lead"
    if temperature == "Warm" and has_opportunity:
        return "Warm reactivation lead"
    if status == "Auto-reply / career site redirect":
        return "Career site follow-up"
    if status == "Rejected / closed process":
        return "Previous process reusable"
    if status == "No response":
        return "No-response low priority"
    return "Ignore"


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
        lead_category = _determine_lead_category(status, temperature, has_opportunity)

        action, angle = _recommended_action(
            status, signals["cv_request"], signals["interview"], signals["auto_reply"]
        )

        follow_up_date = ""
        if status == "Follow-up due" and last_date:
            import datetime as _dt
            follow_up_date = str(last_date.date() + _dt.timedelta(days=7))

        last_content = group.iloc[-1].get("content", "") or ""
        excerpt = sanitize_excerpt(last_content, max_len=120)

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
            "sanitized_last_message_excerpt": excerpt,
            "connection_priority_score":     priority_score,
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
