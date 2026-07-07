# -*- coding: utf-8 -*-
"""
validate_static_dashboard_runtime.py
=====================================
Validates that the static dashboard is ready to run in a browser.
Run after generate_static_dashboard.py + privacy_check.py.

Checks:
  1. Required files exist
  2. dashboard_data.json is valid JSON
  3. JSON has at least one known data key
  4. index.html references app.js and style.css
  5. JS syntax is clean (via node --check if available)

Exit code 0 = all checks pass. Non-zero = failure.
"""

import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT       = Path(__file__).resolve().parent.parent
DOCS_DIR   = ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"
INDEX_HTML = DOCS_DIR / "index.html"
APP_JS     = ASSETS_DIR / "app.js"
STYLE_CSS  = ASSETS_DIR / "style.css"
DATA_JSON  = ASSETS_DIR / "dashboard_data.json"

REQUIRED_DATA_KEYS = {
    "metrics", "kpis", "top_contacts",
    "lead_reactivation", "opportunity_bucket_distribution",
    "opportunity_market_v5", "market_distribution",
}

PASS = "[PASS]"
FAIL = "[FAIL]"


def check(label, ok, detail=""):
    mark = PASS if ok else FAIL
    line = f"  {mark}  {label}"
    if detail and not ok:  # only show detail on failure
        line += f"\n         {detail}"
    print(line)
    return ok


def main():
    print("=" * 60)
    print("  Runtime Validation — Static Dashboard")
    print("=" * 60)

    failures = 0

    # 1. Required files exist
    for path in [INDEX_HTML, APP_JS, STYLE_CSS, DATA_JSON]:
        ok = path.exists()
        if not check(f"File exists: {path.relative_to(ROOT)}", ok):
            failures += 1

    # 2. Valid JSON — STRICT (matches browser JSON.parse(), not Python's
    # lenient default). Python's json.loads() accepts bare NaN/Infinity/
    # -Infinity as a non-standard extension; real browsers reject them
    # outright, which would break dashboard load in production while this
    # check kept reporting "valid JSON". Reject them here too.
    def _reject_non_finite(x):
        raise ValueError(f"dashboard_data.json contains non-finite JSON token: {x!r} "
                          f"(NaN/Infinity are not valid JSON and will fail JSON.parse() in every browser)")

    data = None
    try:
        data = json.loads(DATA_JSON.read_text(encoding="utf-8"), parse_constant=_reject_non_finite)
        kb = DATA_JSON.stat().st_size // 1024
        check(f"dashboard_data.json is valid JSON ({kb} KB)", True)
    except Exception as e:
        check("dashboard_data.json is valid JSON", False, str(e))
        failures += 1

    # 3. At least one known data key
    if data is not None:
        found_keys = set(data.keys()) & REQUIRED_DATA_KEYS
        ok = len(found_keys) > 0
        if not check("JSON has at least one known data key", ok,
                     f"found: {found_keys or 'NONE'} (expected one of {REQUIRED_DATA_KEYS})"):
            failures += 1

    # 4. index.html references app.js and style.css (with cache-bust ?v=)
    if INDEX_HTML.exists():
        html = INDEX_HTML.read_text(encoding="utf-8")
        for ref in ["app.js", "style.css"]:
            ok = ref in html
            if not check(f"index.html references {ref}", ok):
                failures += 1
        # Cache-bust check (warn only, not a hard failure)
        if "app.js?v=" in html:
            check("index.html has cache-bust ?v= on app.js", True)
        else:
            check("index.html has cache-bust ?v= on app.js (warn)", True,
                  "No ?v= found — run generate_static_dashboard.py to inject cache-buster")

    # 5. app.js safety checks — global handlers must NOT call showBootError directly
    if APP_JS.exists():
        js = APP_JS.read_text(encoding="utf-8")

        # Required: isExtensionError and fatalAppError must exist
        for symbol in ["isExtensionError", "fatalAppError", "Non-fatal unhandled rejection"]:
            ok = symbol in js
            if not check(f"app.js contains '{symbol}'", ok,
                         "Missing stability guard — rerun the failsafe patch"):
                failures += 1

        # Forbidden: onerror must not directly call showBootError
        import re
        onerror_block = re.search(r'window\.onerror\s*=.*?(?=window\.addEventListener|function \w)', js, re.S)
        if onerror_block:
            bad = "showBootError" in onerror_block.group(0)
            if not check("window.onerror does NOT call showBootError directly", not bad,
                         "onerror still calls showBootError — this will surface extension errors as fatal UI"):
                failures += 1

        # Forbidden: unhandledrejection must not directly call showBootError
        uhr_block = re.search(r'addEventListener\([\'"]unhandledrejection[\'"]\).*?(?=window\.addEventListener|function \w|\Z)', js, re.S)
        if uhr_block:
            bad = "showBootError" in uhr_block.group(0)
            if not check("unhandledrejection does NOT call showBootError directly", not bad,
                         "unhandledrejection still calls showBootError — extension errors become fatal"):
                failures += 1

    # 6. JS syntax check via node
    try:
        result = subprocess.run(
            ["node", "--check", str(APP_JS)],
            capture_output=True, text=True, timeout=15
        )
        ok = result.returncode == 0
        detail = result.stderr.strip() if not ok else ""
        if not check("app.js JavaScript syntax is valid", ok, detail):
            failures += 1
    except FileNotFoundError:
        check("app.js JavaScript syntax check (skipped — node not found)", True)
    except Exception as e:
        check("app.js JavaScript syntax check", False, str(e))
        failures += 1

    print("=" * 60)
    if failures:
        print(f"  RESULT: {failures} check(s) FAILED — dashboard is NOT ready")
        sys.exit(1)
    else:
        print("  RESULT: All checks PASSED — dashboard is ready for GitHub Pages")
    print("=" * 60)


if __name__ == "__main__":
    main()
