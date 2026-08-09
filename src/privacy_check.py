# -*- coding: utf-8 -*-
"""
privacy_check.py
================
Validates every public dashboard JSON for PII exposure — the lightweight
manifest (docs/assets/dashboard_data.json), every lazy-loaded page file
(docs/assets/data/*.json, Data Payload Optimization V1), and the
outputs/public_dashboard_data.json compatibility artifact if present.
Fails with exit code 1 if any forbidden field or pattern is found in ANY of them.

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

ROOT        = Path(__file__).resolve().parent.parent
JSON_PATH   = ROOT / "docs" / "assets" / "dashboard_data.json"
DATA_DIR    = ROOT / "docs" / "assets" / "data"
OUTPUTS_JSON_PATH = ROOT / "outputs" / "public_dashboard_data.json"

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
    # Data Payload Optimization V1, Part 6 — explicit key-name scans as raw
    # text, defense in depth on top of the FORBIDDEN_RAW_FIELDS record scan
    # below (catches these key names anywhere in the JSON, not just inside
    # the specific record arrays this script already knows to walk).
    (r'"content"\s*:',                "CONTENT key found in JSON"),
    (r'"attachments"\s*:',            "ATTACHMENTS key found in JSON"),
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
    # USD Contract CRM (hybrid: manual + auto-suggested) — sanitized unified
    # row schema only (see src/usd_contract_crm.py PUBLIC_ROW_FIELDS and
    # build_usd_contract_crm_public() in export_public_dashboard_data.py).
    "name", "company", "role", "source", "record_type", "status", "score",
    "priority", "recommended_action", "reason", "next_action", "profile_url",
    "role_url", "currency", "rate_range", "remote_policy",
    "timezone_required", "timezone_risk", "payment_risk", "contract_risk",
    # Opportunity History (src/opportunity_history_engine.py) — sanitized
    # unified event schema only (see EVENT_COLUMNS / MONTHLY_PIPELINE_COLUMNS
    # there and build_opportunity_history_public() in export_public_dashboard_data.py).
    "event_id", "conversation_id_hash", "contact_name", "event_month",
    "event_date", "opportunity_event_type", "opportunity_stage",
    "opportunity_signal_strength", "inbound_recruiter_contact",
    "active_talent_pool_signal", "salary_expectation_requested",
    "cv_requested", "application_requested", "interview_or_call_requested",
    "client_submission_signal", "technical_interview_signal",
    "rejected_or_closed", "soft_closed", "location_or_eligibility_blocked",
    "future_reactivation_candidate", "reactivation_date", "message_angle",
    "tech_stack_signal", "usd_signal", "latam_signal", "remote_signal",
    "reason_short",
    # Monthly Executive Queue (src/monthly_executive_queue.py) — sanitized
    # unified queue-row schema only (see QUEUE_ROW_FIELDS there and
    # build_monthly_executive_queue_public() in export_public_dashboard_data.py).
    "queue_name", "rank", "last_contact_date",
    # Company Follow Intelligence (src/company_follow_intelligence.py) —
    # company-level aggregates only (organization name the user follows +
    # counts/classification), no person-level PII, see
    # build_company_follow_public() in export_public_dashboard_data.py.
    "company_name", "company_follow_key", "followed_on", "days_since_followed",
    "matched_connection_count", "matched_recruiters", "matched_talent_acquisition",
    "matched_hiring_managers", "matched_data_leaders", "matched_top_contacts",
    "matched_untapped_contacts", "matched_lead_reactivation_contacts",
    "matched_opportunity_history_events", "matched_inbound_opportunities",
    "matched_soft_closed_leads", "matched_usd_crm_leads",
    "likely_company_category", "likely_opportunity_bucket",
    "follow_signal_confidence", "company_follow_reason", "signals",
    # Company Mapping Workbench (src/company_mapping_workbench.py) —
    # company-level aggregates/classification only, no person-level PII, see
    # build_company_mapping_workbench_public() in export_public_dashboard_data.py.
    "normalized_company", "current_bucket", "suggested_bucket", "suggested_category",
    "confidence", "manual_review_required", "matched_soft_closed_leads",
    "recently_followed_signal", "message_history_signal", "opportunity_history_signal",
    "usd_signal", "latam_signal", "us_canada_signal", "spain_eu_signal",
    "global_staffing_signal", "global_consulting_signal", "reason_short",
    "impact_if_mapped", "priority_score", "yaml_suggestion",
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
        list(usd_crm.get("manual_opportunities", []) or []) +
        list(usd_crm.get("auto_suggested_usd_leads", []) or []) +
        list(usd_crm.get("recruiter_pipeline", []) or []) +
        list(usd_crm.get("first_outreach_queue", []) or []) +
        list(usd_crm.get("follow_up_queue", []) or []) +
        list(usd_crm.get("active_process_pipeline", []) or []) +
        list(usd_crm.get("manual_applications", []) or []) +
        list((usd_crm.get("contingency_risk", {}) or {}).get("high_risk", []) or []) +
        list((usd_crm.get("contingency_risk", {}) or {}).get("backup", []) or [])
    )
    oh = usd_crm.get("opportunity_history", {}) or {}
    opportunity_history_records = (
        list(oh.get("events", []) or []) +
        list(oh.get("inbound_opportunities", []) or []) +
        list(oh.get("soft_closed_future_leads", []) or []) +
        list(oh.get("reactivation_calendar", []) or [])
    )
    meq = usd_crm.get("monthly_executive_queue", {}) or {}
    monthly_queue_records = (
        list(meq.get("inbound_top20", []) or []) +
        list(meq.get("reactivation_top20", []) or []) +
        list(meq.get("soft_closed_top20", []) or []) +
        list(meq.get("usd_followups_top20", []) or []) +
        list(meq.get("monthly_backlog_top50", []) or []) +
        list(meq.get("all_monthly_queue_records", []) or [])
    )
    cf = data.get("company_follow_intelligence", {}) or {}
    company_follow_records = (
        list(cf.get("companies", []) or []) +
        list(cf.get("needs_review", []) or []) +
        list(cf.get("mapping_candidates", []) or [])
    )
    mw = data.get("company_mapping_workbench", {}) or {}
    mapping_workbench_records = list(mw.get("companies", []) or [])
    all_records = [(i, c, "contact") for i, c in enumerate(contacts)] + \
                  [(i, c, "lead") for i, c in enumerate(leads)] + \
                  [(i, c, "untapped") for i, c in enumerate(untapped)] + \
                  [(i, c, "mapping") for i, c in enumerate(mapping)] + \
                  [(i, c, "gap_drilldown") for i, c in enumerate(gap_drilldown)] + \
                  [(i, c, "weekly_delta") for i, c in enumerate(weekly_delta)] + \
                  [(i, c, "opp_segment") for i, c in enumerate(opp_segments)] + \
                  [(i, c, "usd_crm") for i, c in enumerate(usd_crm_records)] + \
                  [(i, c, "opportunity_history") for i, c in enumerate(opportunity_history_records)] + \
                  [(i, c, "monthly_queue") for i, c in enumerate(monthly_queue_records)] + \
                  [(i, c, "company_follow") for i, c in enumerate(company_follow_records)] + \
                  [(i, c, "mapping_workbench") for i, c in enumerate(mapping_workbench_records)]

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

    return violations


def check_manifest_structure(manifest_path: Path) -> list[str]:
    """Structure check specific to the lightweight manifest (dashboard_data.json)
    — 'top_contacts'/etc. now live in docs/assets/data/*.json instead, so this
    check no longer applies to every file, only to the manifest itself."""
    if not manifest_path.exists():
        return [f"File not found: {manifest_path}"]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"JSON parse error: {e}"]

    violations = []
    if "meta" not in data:
        violations.append("Missing 'meta' section in manifest")
    if "page_manifest" not in data:
        violations.append("Missing 'page_manifest' section in manifest")
    else:
        for page_id, info in data["page_manifest"].items():
            page_path = DATA_DIR / Path(info.get("file", "")).name
            if info.get("available") and not page_path.exists():
                violations.append(f"page_manifest references missing file for '{page_id}': {info.get('file')}")
    return violations


# Company Follow Intelligence — these five CSVs get committed to the public
# repo (see the weekly "safe commit" file list), so scan their raw text too,
# not just the JSON payload built from them.
COMPANY_FOLLOW_CSV_OUTPUTS = [
    ROOT / "outputs" / "company_follow_intelligence.csv",
    ROOT / "outputs" / "company_follow_company_matches.csv",
    ROOT / "outputs" / "company_follow_mapping_candidates.csv",
    ROOT / "outputs" / "followed_companies_needing_review.csv",
    ROOT / "outputs" / "company_follow_resolution_summary.csv",
]

# Company Mapping Workbench — same rationale: these outputs (including the
# YAML suggestions file) get committed to the public repo, so scan them too.
MAPPING_WORKBENCH_OUTPUTS = [
    ROOT / "outputs" / "company_mapping_workbench.csv",
    ROOT / "outputs" / "company_mapping_priority_queue.csv",
    ROOT / "outputs" / "company_mapping_impact_estimate.csv",
    ROOT / "outputs" / "company_mapping_yaml_suggestions.yml",
]


def check_csv_text(path: Path) -> list[str]:
    """Raw-text scan of a committed CSV output for the same forbidden
    patterns/fields checked in the JSON payload — defense in depth for
    outputs that ship straight to the public repo without going through
    export_public_dashboard_data.py's allowlisting."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as e:
        return [f"Could not read {path.name}: {e}"]

    violations = []
    for pattern, description in FORBIDDEN_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        filtered = [m for m in matches if "linkedin.com" not in m.lower()]
        if filtered:
            violations.append(
                f"Pattern violation ({description}): found {len(filtered)} matches in {path.name}"
            )

    if path.suffix.lower() == ".csv":
        header = text.splitlines()[0].lower().replace('"', '') if text.splitlines() else ""
        header_fields = {f.strip() for f in header.split(",")}
        for forbidden in (FORBIDDEN_RAW_FIELDS | FORBIDDEN_FIELDS):
            if forbidden in header_fields:
                violations.append(f"Forbidden field '{forbidden}' found in header of {path.name}")
    return violations


def _collect_target_files() -> list[Path]:
    files = [JSON_PATH]
    if DATA_DIR.exists():
        files += sorted(DATA_DIR.glob("*.json"))
    if OUTPUTS_JSON_PATH.exists():
        files.append(OUTPUTS_JSON_PATH)
    return files


def main():
    print("=" * 60)
    print("  Privacy Check — public dashboard JSON (Data Payload Optimization V1)")
    print("=" * 60)

    targets = _collect_target_files()
    print(f"  Scanning {len(targets)} file(s):")
    for p in targets:
        print(f"    - {p.relative_to(ROOT)}")
    print()

    all_violations: list[str] = []
    file_stats: dict[Path, dict] = {}
    for path in targets:
        violations = check_json(path)
        if violations:
            all_violations += [f"[{path.relative_to(ROOT)}] {v}" for v in violations]
        else:
            file_stats[path] = {"size_kb": path.stat().st_size // 1024}

    all_violations += [f"[manifest] {v}" for v in check_manifest_structure(JSON_PATH)]

    csv_targets = [p for p in (COMPANY_FOLLOW_CSV_OUTPUTS + MAPPING_WORKBENCH_OUTPUTS) if p.exists()]
    if csv_targets:
        print(f"  Scanning {len(csv_targets)} committed CSV/YAML output(s):")
        for p in csv_targets:
            print(f"    - {p.relative_to(ROOT)}")
        print()
    for path in csv_targets:
        violations = check_csv_text(path)
        if violations:
            all_violations += [f"[{path.relative_to(ROOT)}] {v}" for v in violations]

    snapshot_violations = check_no_tracked_raw_snapshots()
    if snapshot_violations:
        all_violations += snapshot_violations
    else:
        print("  [OK] No raw LinkedIn export or dated snapshot folder is tracked in git.")

    if all_violations:
        print(f"\n  [FAIL] {len(all_violations)} violation(s) found:\n")
        for v in all_violations:
            print(f"    • {v}")
        print("\n  Fix these issues before publishing to GitHub Pages.")
        print("=" * 60)
        sys.exit(1)
    else:
        print(f"\n  [PASS] All {len(targets)} file(s) clean.\n")
        for path, stats in file_stats.items():
            print(f"     {path.relative_to(ROOT)}  ({stats['size_kb']} KB)")
        # Extra detail on the two most commonly inspected files
        try:
            contacts_path = DATA_DIR / "top_contacts.json"
            if contacts_path.exists():
                contacts = json.loads(contacts_path.read_text(encoding="utf-8")).get("top_contacts", [])
                print(f"\n     top_contacts.json: {len(contacts)} contacts")
                if contacts:
                    print(f"     Fields: {list(contacts[0].keys())}")
        except Exception:
            pass

    print("=" * 60)


if __name__ == "__main__":
    main()
