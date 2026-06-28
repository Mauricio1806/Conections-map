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
    (r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "Email address pattern"),
]

# Keys in contact records that are explicitly allowed
ALLOWED_CONTACT_FIELDS = {
    "full_name", "company_clean", "position_clean",
    "persona", "area", "seniority",
    "market_v2", "strategic_market", "market_type", "market_confidence_v2",
    "priority_score",
    "action_type", "message_angle", "why_priority",
    "recommended_action", "company_category", "url",
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
    for i, contact in enumerate(contacts):
        for field in contact:
            if field.lower() in FORBIDDEN_FIELDS:
                violations.append(
                    f"Contact #{i+1} ({contact.get('full_name','?')}): "
                    f"forbidden field '{field}'"
                )
            if field.lower() not in ALLOWED_CONTACT_FIELDS and "email" in field.lower():
                violations.append(
                    f"Contact #{i+1}: suspicious field '{field}'"
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
