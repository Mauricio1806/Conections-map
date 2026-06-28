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
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT      = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "docs" / "assets" / "dashboard_data.json"

# Exact field names that must NOT appear in any contact record
FORBIDDEN_FIELDS = {
    "email address",
    "email_address",
    "email",
    "phone",
    "phone_number",
    "mobile",
    "whatsapp",
}

# Regex patterns that must NOT appear anywhere in the JSON text
FORBIDDEN_PATTERNS = [
    (r"@gmail\.com",                   "Gmail address found"),
    (r"@hotmail\.com",                 "Hotmail address found"),
    (r"@outlook\.com",                 "Outlook address found"),
    (r"@yahoo\.com",                   "Yahoo address found"),
    (r"@protonmail\.com",              "ProtonMail address found"),
    (r"\b\d{10,15}\b",                 "Possible phone number (10-15 digits)"),
    # Email pattern — exclude LinkedIn URLs to avoid false positives
    (r"(?<!linkedin\.com)[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "Email address pattern"),
]

# Raw message content field names that must NOT appear in top_contacts
FORBIDDEN_RAW_FIELDS = {"content", "attachments", "raw_content", "raw_message"}

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
    all_records = [(i, c, "contact") for i, c in enumerate(contacts)] + \
                  [(i, c, "lead") for i, c in enumerate(leads)]

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
