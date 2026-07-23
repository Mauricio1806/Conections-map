# -*- coding: utf-8 -*-
"""
privacy_check.py
================
Validates docs/assets/dashboard_data.json for PII exposure.
Fails with exit code 1 if any forbidden field or pattern is found.

Run after generate_static_dashboard.py:
    python src/privacy_check.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT      = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "docs" / "assets" / "dashboard_data.json"

# ─── Part 18 (weekly snapshot refresh) — raw snapshot/export tracking check ───
# Root-level raw LinkedIn export filenames that must never be committed.
ROOT_RAW_EXPORT_NAMES = {
    "connections.csv", "invitations.csv", "company follows.csv",
    "messages.csv",
}
# Dated weekly snapshot folders (DD-MM or YYYY-MM-DD), root-anchored only —
# matches src/weekly_snapshot_refresh.py's own folder convention.
SNAPSHOT_DIR_RE = re.compile(r"^(?:\d{2}-\d{2}|\d{4}-\d{2}-\d{2})/")

# Part 27 (Untapped Network Intelligence) — private person-level backlog
# files that must never be tracked, however they got there.
PRIVATE_OUTPUT_PREFIXES = ("outputs/private/",)
PRIVATE_OUTPUT_NAMES = {
    "outputs/untapped_ambiguous_review.csv",
    "outputs/untapped_outreach_backlog.csv",
    "outputs/untapped_weekly_queue.csv",
    "outputs/untapped_high_value.csv",
}


def check_no_tracked_raw_snapshots() -> list[str]:
    """Assert `git ls-files` contains no raw LinkedIn export, dated snapshot
    folder, or private untapped-network backlog file. Returns a list of
    violations (empty = clean)."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except Exception as e:
        return [f"Could not run 'git ls-files' to check tracked files: {e}"]

    violations = []
    for path in out.splitlines():
        p = path.strip()
        if not p:
            continue
        name_lower = p.lower()
        # Root-level raw export (no directory separator before the filename)
        if "/" not in p and name_lower in ROOT_RAW_EXPORT_NAMES:
            violations.append(f"Raw LinkedIn export is tracked at repo root: {p}")
        elif SNAPSHOT_DIR_RE.match(p):
            violations.append(f"File inside a dated snapshot folder is tracked: {p}")
        elif name_lower.startswith(PRIVATE_OUTPUT_PREFIXES) or name_lower in PRIVATE_OUTPUT_NAMES:
            violations.append(f"Private untapped-network backlog file is tracked: {p}")
    return violations

# Exact field names that must NOT appear in any contact record
FORBIDDEN_FIELDS = {
    "email address",
    "email_address",
    "email",
    "phone",
    "phone_number",
    "mobile",
    "whatsapp",
    # USD Contract CRM — private manual-input fields that must never reach
    # the public JSON (see src/usd_contract_crm.py PRIVATE_FIELDS).
    "notes_private",
}

# Regex patterns that must NOT appear anywhere in the JSON text
FORBIDDEN_PATTERNS = [
    (r"@gmail\.com",                   "Gmail address found"),
    (r"@hotmail\.com",                 "Hotmail address found"),
    (r"@outlook\.com",                 "Outlook address found"),
    (r"@yahoo\.com",                   "Yahoo address found"),
    (r"@protonmail\.com",              "ProtonMail address found"),
    # Excludes digit runs directly glued to a word via hyphen (e.g. the
    # auto-generated numeric suffix LinkedIn appends to a profile URL slug
    # when the name-based slug is taken, like ".../in/jane-doe-67999813011")
    # — genuine phone numbers in free text are not written that way.
    (r"(?<!-)\b\d{10,15}\b",           "Possible phone number (10-15 digits)"),
    # Email pattern — exclude LinkedIn URLs to avoid false positives
    (r"(?<!linkedin\.com)[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "Email address pattern"),
    # USD Contract CRM — defense in depth: these must never appear anywhere
    # in the JSON text, not just as field names.
    (r'"notes_private"',              "notes_private key found in JSON"),
    (r"raw message",                  "Raw message content phrase found"),
]

# Raw message content field names that must NOT appear in top_contacts
FORBIDDEN_RAW_FIELDS = {"content", "attachments", "raw_content", "raw_message", "notes_private"}

# Keys in contact records that are explicitly allowed
ALLOWED_CONTACT_FIELDS = {
    "full_name", "company_clean", "position_clean",
    "persona", "area", "seniority",
    "market_v2", "strategic_market", "market_type", "market_confidence_v2",
    "priority_score",
    "action_type", "message_angle", "why_priority",
    "recommended_action", "company_category", "url",
    # Lead reactivation safe fields
    "other_person_name", "other_person_profile_url",
    "conversation_status", "lead_category", "lead_temperature",
    "last_message_date", "days_since_last_message", "total_messages",
    "reactivation_priority_score", "recommended_next_action",
    "has_positive_signal", "has_interview_signal", "has_cv_signal", "is_auto_reply",
    "market_v4", "market_group", "market_resolution_status",
    # V5 Opportunity Market — safe inference output fields only (no raw message data)
    "opportunity_market_v5", "opportunity_bucket",
    "opportunity_confidence", "is_actionable_opportunity",
    # Outreach adjusted scoring — computed signals, no raw content
    "outreach_adjusted_score", "outreach_status", "outreach_reason",
    "has_message_history", "replied_to_me", "ghosted_me", "auto_reply_only",
    "last_message_date", "days_since_last_message",
    "prior_positive_signal", "prior_rejection",
    # V6 response intelligence — sanitized fields only, no raw content
    "needs_my_response", "needs_response_confidence", "needs_response_reason",
    "response_intent_score",
    "manual_review_required", "last_sender_type", "conversation_recency_band",
    "sanitized_intent_label",
    # V6 company resolution — computed signals, no raw content
    "company_resolution_source", "company_resolution_confidence",
    "company_evidence_count", "company_dominant_bucket",
    "company_dominant_bucket_share", "cross_contact_propagation_used",
    "message_signal_used", "company_canonical",
    # V8 multi-dimensional conversation state — derived states/scores only, no raw content
    "process_state", "relationship_state", "reply_obligation", "action_urgency",
    "closure_reason", "next_action_date", "reactivation_window_days",
    "relationship_value_score", "immediate_action_score",
    "conversation_state_confidence", "state_evidence_codes",
    "external_action_type", "request_resolved", "cooldown_state",
    # Untapped Network Intelligence — derived states/scores only, no raw content
    "seniority", "connected_on", "days_connected", "connection_age_bucket",
    "contact_history_status", "untapped_category", "untapped_outreach_score",
    "recommended_first_action", "first_message_angle", "profile_url",
    "conversation_match_confidence", "strategic_focus", "operational_category",
    # Needs Mapping backlog person rows (needs_mapping_backlog.people)
    "suggested_opportunity_bucket", "mapping_priority_score",
    "resolution_source", "mapping_reason_short",
    # Strategic Gap people drill-down (Part 3)
    "market", "segment", "reason",
    # Weekly people delta segments (Part 4)
    "market_segment", "bucket_group", "previous_value", "current_value",
    "match_method", "is_actionable",
    # USD Contract CRM — sanitized fields only (see src/usd_contract_crm.py
    # and build_usd_contract_crm_public() in export_public_dashboard_data.py)
    "company_name", "role_title", "role_url", "source_type", "currency",
    "rate_range", "rate_type", "contract_type", "remote_policy",
    "timezone_required", "overlap_required", "tech_stack", "status",
    "next_action", "priority", "timezone_risk", "payment_risk",
    "contract_risk", "usd_pipeline_score", "recruiter_name",
    "recruiter_profile_url", "application_date", "source", "expected_rate",
    "cv_version", "recruiter_contacted", "follow_up_date", "result",
    "rejection_reason", "date", "contact_name", "profile_url", "company",
    "message_type", "last_reply_date", "usd_signal", "latam_signal",
    "timezone_signal", "name", "overdue",
}


def check_json(path: Path) -> list[str]:
    """Return list of violations. Empty list = PASS."""
    if not path.exists():
        return [f"File not found: {path}"]

    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except Exception as e:
        return [f"JSON parse error: {e}"]

    violations = []

    # ── 1. Field-level check on contact records ─────────────────────────────
    contacts = data.get("top_contacts", [])
    lr = data.get("lead_reactivation", {}) or {}
    leads = (
        list(lr.get("top_reactivation_contacts", []) or []) +
        list(lr.get("this_week_contacts", []) or []) +
        list(lr.get("needs_reply_contacts", []) or [])
    )
    un = data.get("untapped_network", {}) or {}
    untapped = (
        list(un.get("top_untapped_contacts", []) or []) +
        list(un.get("this_week_queue", []) or [])
    )
    mapping = list((data.get("needs_mapping_backlog", {}) or {}).get("people", []) or [])
    gap_drilldown = list(data.get("strategic_gap_people_drilldown", []) or [])
    weekly_delta = list(data.get("weekly_people_delta_segments", []) or [])
    opp_segments = list(data.get("opportunity_market_people_segments", []) or [])
    usd_crm = data.get("usd_contract_crm", {}) or {}
    usd_crm_records = (
        list(usd_crm.get("pipeline", []) or []) +
        list(usd_crm.get("applications", []) or []) +
        list(usd_crm.get("outreach_contacts", []) or []) +
        list(usd_crm.get("follow_up_queue", []) or []) +
        list((usd_crm.get("risk_view", {}) or {}).get("high_risk", []) or []) +
        list((usd_crm.get("risk_view", {}) or {}).get("backup", []) or [])
    )
    all_records = [(i, c, "contact") for i, c in enumerate(contacts)] + \
                  [(i, c, "lead") for i, c in enumerate(leads)] + \
                  [(i, c, "untapped") for i, c in enumerate(untapped)] + \
                  [(i, c, "mapping") for i, c in enumerate(mapping)] + \
                  [(i, c, "gap_drilldown") for i, c in enumerate(gap_drilldown)] + \
                  [(i, c, "weekly_delta") for i, c in enumerate(weekly_delta)] + \
                  [(i, c, "opp_segment") for i, c in enumerate(opp_segments)] + \
                  [(i, c, "usd_crm") for i, c in enumerate(usd_crm_records)]

    for i, record, record_type in all_records:
        for field in record:
            fl = field.lower()
            if fl in FORBIDDEN_FIELDS:
                violations.append(
                    f"{record_type.capitalize()} #{i+1}: forbidden field '{field}'"
                )
            if fl in FORBIDDEN_RAW_FIELDS:
                violations.append(
                    f"{record_type.capitalize()} #{i+1}: raw message field '{field}' — must not be in public JSON"
                )
            if fl not in ALLOWED_CONTACT_FIELDS and "email" in fl:
                violations.append(
                    f"{record_type.capitalize()} #{i+1}: suspicious email-like field '{field}'"
                )

    # ── 2. Regex scan of raw JSON text ────────────────────────────────────────
    for pattern, description in FORBIDDEN_PATTERNS:
        matches = re.findall(pattern, raw_text, re.IGNORECASE)
        # Filter out LinkedIn URLs which may look like patterns
        filtered = [m for m in matches if "linkedin.com" not in m.lower()]
        if filtered:
            sample = filtered[:3]
            violations.append(
                f"Pattern violation ({description}): "
                f"found {len(filtered)} matches, sample: {sample}"
            )

    # ── 3. Structure check — top_contacts should exist ───────────────────────
    if "top_contacts" not in data:
        violations.append("Missing 'top_contacts' section in JSON")

    if "meta" not in data:
        violations.append("Missing 'meta' section in JSON")

    return violations


def main():
    print("=" * 60)
    print("  Privacy Check — docs/assets/dashboard_data.json")
    print("=" * 60)

    violations = check_json(JSON_PATH)

    snapshot_violations = check_no_tracked_raw_snapshots()
    if snapshot_violations:
        violations = violations + snapshot_violations
    else:
        print("  [OK] No raw LinkedIn export or dated snapshot folder is tracked in git.")

    if violations:
        print(f"\n  [FAIL] {len(violations)} violation(s) found:\n")
        for v in violations:
            print(f"    • {v}")
        print("\n  Fix these issues before publishing to GitHub Pages.")
        print("=" * 60)
        sys.exit(1)
    else:
        # Load for stats
        try:
            data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
            contacts = data.get("top_contacts", [])
            print(f"\n  [PASS]")
            print(f"     File:     {JSON_PATH.relative_to(JSON_PATH.parent.parent.parent)}")
            print(f"     Contacts: {len(contacts)}")
            print(f"     Fields:   {list(contacts[0].keys()) if contacts else 'N/A'}")
            print(f"     Size:     {JSON_PATH.stat().st_size // 1024} KB")
        except Exception:
            print("\n  [PASS] (no violations found)")

    print("=" * 60)


if __name__ == "__main__":
    main()
