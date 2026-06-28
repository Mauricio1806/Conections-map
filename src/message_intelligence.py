# -*- coding: utf-8 -*-
"""
message_intelligence.py
========================
Reads messages.csv and builds per-conversation intelligence:
  - Who is me vs. the other person
  - Conversation status, lead temperature, recommended action
  - Reactivation priority score

Returns a DataFrame with one row per conversation.
"""

import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from src.message_sanitizer import sanitize_excerpt, strip_html

logger = logging.getLogger(__name__)

ROOT        = Path(__file__).resolve().parent.parent
MESSAGES_CSV = ROOT / "messages.csv"

MY_NAMES = {
    "mauricio esquivel de siqueira behrens",
    "mauricio behrens",
    "mauricio esquivel",
    "mauricio",
}
MY_URL = "https://www.linkedin.com/in/mauricio-behrens"

# ── Keyword lists ─────────────────────────────────────────────────────────────

AUTO_REPLY_KW = [
    "automatic reply", "auto reply", "auto-reply", "high volume",
    "profiles can't be reviewed here", "profiles cannot be reviewed",
    "apply via our career site", "career site", "talent database",
    "submit your cv", "thanks for reaching out", "due to the high volume",
    "no role fits now", "we will keep your profile", "automated message",
    "apply through our", "apply on our", "submit your resume",
    "volume of messages", "cannot respond to everyone", "please apply",
]

POSITIVE_KW = [
    "opportunity", "vaga", "role", "job", "hiring", "data engineer",
    "remote", "contractor", "usd", "salary", "interview", "entrevista",
    "cv", "resume", "apply", "applied", "candidatura", "perfil",
    "processo", "recruiter", "talent acquisition", "technical interview",
    "screening", "open position", "open role",
]

REJECTION_KW = [
    "not selected", "unfortunately", "decided to move forward",
    "rejected", "não avançamos", "nao avancamos", "não seguiremos",
    "nao seguiremos", "encerramos", "outra pessoa", "posição fechada",
    "posicao fechada", "process closed", "role closed", "position closed",
    "moved forward with other", "decided not to proceed",
]

CV_KW = [
    "send your cv", "send me your resume", "curriculum", "currículo",
    "curriculo", "i applied", "realizei minha candidatura",
    "application submitted", "candidatura realizada", "talent database",
    "career site", "submit your profile",
]

INTERVIEW_KW = [
    "interview", "entrevista", "screening", "technical interview",
    "call", "agenda", "calendário", "calendario", "schedule",
    "meeting", "recruiter call", "video call", "video interview",
]


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {
        "CONVERSATION ID":       "conversation_id",
        "CONVERSATION TITLE":    "conversation_title",
        "FROM":                  "from",
        "SENDER PROFILE URL":    "sender_profile_url",
        "TO":                    "to",
        "RECIPIENT PROFILE URLS":"recipient_profile_urls",
        "DATE":                  "date",
        "SUBJECT":               "subject",
        "CONTENT":               "content",
        "FOLDER":                "folder",
        "ATTACHMENTS":           "attachments",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    # lowercase the remaining headers that weren't mapped
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


def _recommended_action_and_angle(status: str, temperature: str,
                                   last_sender_is_me: bool,
                                   has_rejection: bool, has_cv: bool,
                                   has_interview: bool, is_auto_reply: bool) -> tuple:
    if status == "Needs my response":
        return (
            "Reply now — they were the last sender",
            "Reply directly based on their last message. Prioritize this before sending new outreach."
        )
    if status == "Auto-reply / career site redirect":
        return (
            "Submit CV to talent database, then follow up",
            "I reviewed the career site and submitted my profile. If any Data Engineering / Cloud Data role opens, I'd be happy to be considered."
        )
    if status == "Dormant warm lead":
        return (
            "Reactivate with updated availability",
            "We spoke previously about Data Engineering opportunities. I wanted to reconnect because I'm currently open to remote USD/LATAM roles."
        )
    if status == "Rejected / closed process":
        return (
            "Reconnect after 60 days — previous process closed",
            "Thanks again for the previous process. I'd be happy to stay in touch for future Data Engineering roles aligned with Azure, AWS, Databricks, SQL and ETL."
        )
    if status == "No response":
        return (
            "Send soft reactivation message",
            "Quick follow-up in case this is relevant: I'm open to remote Data Engineering roles across LATAM/US time zones."
        )
    if status == "Follow-up due":
        return (
            "Follow up after application",
            "Quick follow-up in case this is relevant: I'm open to remote Data Engineering roles across LATAM/US time zones."
        )
    if status == "Warm lead":
        return (
            "Ask if they have new Data Engineering roles",
            "We spoke previously about Data Engineering opportunities. I wanted to reconnect because I'm currently open to remote USD/LATAM roles."
        )
    return (
        "Do not prioritize — automatic reply only and no role signal",
        "Low priority — no actionable signal."
    )


def _compute_reactivation_score(
    days_since: int,
    has_positive: bool,
    has_rejection: bool,
    has_cv: bool,
    has_interview: bool,
    is_auto_reply: bool,
    other_replied: bool,
    last_sender_is_me: bool,
    status: str,
    temperature: str,
    persona_score: float,      # from classified_connections
    is_recruiter_persona: bool,
    market_value: bool,
) -> int:
    score = 0

    # Base from existing connection priority
    score += min(25, persona_score * 0.25)

    # Positive opportunity signal
    if has_positive:
        score += 30
    if has_cv:
        score += 10
    if has_interview:
        score += 15

    # Rejection penalty
    if has_rejection:
        score -= 20
    if is_auto_reply and not has_positive:
        score -= 30

    # Recency bonus
    if days_since < 7:
        score += 15
    elif days_since < 14:
        score += 10
    elif days_since < 30:
        score += 5
    elif days_since > 90:
        score -= 10

    # Response signal
    if other_replied:
        score += 10
    if status == "Needs my response":
        score += 15
    if status == "Follow-up due":
        score += 10

    # Persona / market bonus
    if is_recruiter_persona:
        score += 10
    if market_value:
        score += 10

    return max(0, min(100, int(score)))


def build_conversation_intelligence(
    msgs: pd.DataFrame,
    classified_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Builds one row per conversation with intelligence fields.
    classified_df is the classified_connections DataFrame for joining.
    """
    today = date.today()
    rows = []

    for conv_id, group in msgs.groupby("conversation_id"):
        group = group.sort_values("date_parsed").reset_index(drop=True)

        # Identify all messages and who is me
        my_msgs   = group[group["is_me_sender"]]
        them_msgs = group[~group["is_me_sender"]]

        if len(group) == 0:
            continue

        # Other person
        if not them_msgs.empty:
            other_name        = them_msgs.iloc[0]["from"]
            other_profile_url = them_msgs.iloc[0]["sender_profile_url"]
        elif not my_msgs.empty:
            other_name        = my_msgs.iloc[0]["to"]
            other_profile_url = _first_url(my_msgs.iloc[0].get("recipient_profile_urls", ""))
        else:
            continue

        # Skip conversations with no identifiable other person
        if _is_me(other_name, other_profile_url):
            other_name = group.iloc[0]["to"] if not group.empty else ""

        first_msg   = group.iloc[0]
        last_msg    = group.iloc[-1]
        first_date  = first_msg["date_parsed"]
        last_date   = last_msg["date_parsed"]
        last_sender_is_me = last_msg["is_me_sender"]

        days_since = 9999
        if last_date:
            delta = today - last_date.date()
            days_since = delta.days

        total_messages        = len(group)
        messages_from_me      = len(my_msgs)
        messages_from_other   = len(them_msgs)
        other_replied_at_all  = messages_from_other > 0

        # Aggregate signal flags across all messages
        all_content = " ".join(group["content"].fillna("").tolist())
        signals = _score_content(all_content)

        # Conversation direction
        if messages_from_me == 0:
            direction = "Inbound"
        elif messages_from_other == 0:
            direction = "Outbound"
        else:
            direction = "Mutual"

        # Status
        is_auto = signals["auto_reply"] and not signals["positive"]
        if is_auto and messages_from_other > 0 and messages_from_me == 0:
            status = "Auto-reply / career site redirect"
        elif last_sender_is_me and days_since > 7 and not signals["rejection"]:
            status = "Follow-up due"
        elif not last_sender_is_me:
            status = "Needs my response"
        elif signals["rejection"]:
            status = "Rejected / closed process"
        elif signals["positive"] and days_since > 30:
            status = "Dormant warm lead"
        elif signals["positive"] and days_since <= 30:
            status = "Warm lead"
        elif is_auto:
            status = "Auto-reply / career site redirect"
        elif messages_from_other == 0 and days_since > 7:
            status = "No response"
        else:
            status = "Low value / ignore"

        # Lead temperature
        if status == "Needs my response" and signals["positive"]:
            temperature = "Hot"
        elif status in ("Warm lead", "Follow-up due") and signals["positive"]:
            temperature = "Warm"
        elif status == "Dormant warm lead":
            temperature = "Warm"
        elif status == "Needs my response":
            temperature = "Warm"
        elif status in ("Auto-reply / career site redirect",):
            temperature = "Neutral"
        elif signals["rejection"] or status == "Rejected / closed process":
            temperature = "Cold"
        elif status == "No response":
            temperature = "Cold"
        elif status == "Low value / ignore":
            temperature = "Ignore"
        else:
            temperature = "Neutral"

        # Join with classified_connections
        persona          = ""
        company_clean    = ""
        position_clean   = ""
        strategic_market = ""
        priority_score   = 0.0
        is_recruiter     = False
        market_value_bool= False

        if classified_df is not None and not classified_df.empty:
            match = None
            url_clean = other_profile_url.rstrip("/").lower() if other_profile_url else ""
            if url_clean:
                url_mask = classified_df["url"].str.rstrip("/").str.lower() == url_clean
                match = classified_df[url_mask].head(1)
            if (match is None or match.empty) and other_name:
                name_clean = other_name.lower().strip()
                name_mask = classified_df["full_name"].str.lower().str.strip() == name_clean
                match = classified_df[name_mask].head(1)
            if match is not None and not match.empty:
                row_ = match.iloc[0]
                persona          = row_.get("persona", "")
                company_clean    = row_.get("company_clean", "")
                position_clean   = row_.get("position_clean", "")
                strategic_market = row_.get("market_v2", row_.get("strategic_market", ""))
                priority_score   = float(row_.get("priority_score", 0) or 0)
                is_recruiter     = persona in {
                    "Recruiter", "Talent Acquisition", "Sourcer",
                    "Hiring Manager", "Engineering Manager",
                    "Data Engineering Manager", "Head of Data", "Director",
                }
                market_value_bool = strategic_market in {
                    "LATAM_USD", "US_CANADA_NEARSHORE", "SPAIN_EU", "EUROPE",
                    "GLOBAL_STAFFING",
                }

        reactivation_score = _compute_reactivation_score(
            days_since        = days_since,
            has_positive      = signals["positive"],
            has_rejection     = signals["rejection"],
            has_cv            = signals["cv_request"],
            has_interview     = signals["interview"],
            is_auto_reply     = is_auto,
            other_replied     = other_replied_at_all,
            last_sender_is_me = last_sender_is_me,
            status            = status,
            temperature       = temperature,
            persona_score     = priority_score,
            is_recruiter_persona = is_recruiter,
            market_value      = market_value_bool,
        )

        action, angle = _recommended_action_and_angle(
            status, temperature, last_sender_is_me,
            signals["rejection"], signals["cv_request"],
            signals["interview"], is_auto,
        )

        # Follow-up due date
        follow_up_date = ""
        if status == "Follow-up due":
            import datetime as _dt
            fu = last_date.date() + _dt.timedelta(days=7) if last_date else None
            follow_up_date = str(fu) if fu else ""

        # Sanitized excerpt
        last_content = last_msg.get("content", "") or ""
        excerpt = sanitize_excerpt(last_content, max_len=120)

        rows.append({
            "conversation_id":              conv_id,
            "other_person_name":            other_name,
            "other_person_profile_url":     other_profile_url,
            "company_clean":                company_clean,
            "position_clean":               position_clean,
            "persona":                      persona,
            "strategic_market":             strategic_market,
            "first_message_date":           str(first_date.date()) if first_date else "",
            "last_message_date":            str(last_date.date()) if last_date else "",
            "total_messages":               total_messages,
            "messages_from_me":             messages_from_me,
            "messages_from_other_person":   messages_from_other,
            "last_sender":                  "me" if last_sender_is_me else "them",
            "days_since_last_message":      days_since if days_since < 9999 else "",
            "conversation_direction":       direction,
            "conversation_status":          status,
            "lead_temperature":             temperature,
            "has_positive_signal":          signals["positive"],
            "has_rejection_signal":         signals["rejection"],
            "has_cv_signal":                signals["cv_request"],
            "has_interview_signal":         signals["interview"],
            "is_auto_reply":                is_auto,
            "recommended_next_action":      action,
            "follow_up_due_date":           follow_up_date,
            "reactivation_priority_score":  reactivation_score,
            "message_angle":                angle,
            "sanitized_last_message_excerpt": excerpt,
            "priority_score":               priority_score,
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        "reactivation_priority_score", ascending=False
    ).reset_index(drop=True)


def load_messages(path: Path = MESSAGES_CSV) -> pd.DataFrame | None:
    """Load and preprocess messages.csv. Returns None if file not found."""
    if not path.exists():
        logger.warning(f"messages.csv not found at {path} — skipping message intelligence.")
        return None

    try:
        df = pd.read_csv(path, dtype=str, low_memory=False, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(path, dtype=str, low_memory=False, encoding="latin-1")

    df = _normalize_cols(df)
    df = df.fillna("")

    # Parse dates
    df["date_parsed"] = df["date"].apply(_parse_date)

    # Identify sender
    df["is_me_sender"] = df.apply(
        lambda r: _is_me(r.get("from", ""), r.get("sender_profile_url", "")),
        axis=1,
    )

    # Filter out spam/irrelevant folders
    if "folder" in df.columns:
        df = df[df["folder"].str.upper().isin(["INBOX", "SENT", ""])]

    # Filter out "Sponsored Conversation" if title is present
    if "conversation_title" in df.columns:
        df = df[~df["conversation_title"].str.contains("Sponsored", case=False, na=False)]

    logger.info(f"  Loaded {len(df):,} messages from {df['conversation_id'].nunique():,} conversations")
    return df


def run_message_intelligence(classified_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Main entry point. Returns enriched conversation DataFrame.
    Pass classified_df for join enrichment. Returns empty DataFrame if no messages.csv.
    """
    msgs = load_messages()
    if msgs is None or msgs.empty:
        return pd.DataFrame()
    return build_conversation_intelligence(msgs, classified_df=classified_df)
