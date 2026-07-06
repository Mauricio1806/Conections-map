# -*- coding: utf-8 -*-
"""
weekly_snapshot_refresh.py
===========================
Reusable weekly LinkedIn snapshot ingestion + delta layer.

Usage (repeats every week with a new dated folder):

    python src/weekly_snapshot_refresh.py --snapshot-folder "05-07" --snapshot-date "2026-07-05"
    python src/weekly_snapshot_refresh.py --snapshot-folder "05-07" --snapshot-date "2026-07-05" \
        --previous-folder "28-05" --previous-date "2026-05-28"

If --previous-folder is omitted, the most recent OTHER dated snapshot folder
in the project root (matching DD-MM or YYYY-MM-DD, excluding known
non-snapshot directories) is used automatically.

What this script does (and does NOT do):
  1. Resolves the four LinkedIn export files inside the snapshot folder,
     case/spacing/underscore-insensitively, and prints a safe validation
     summary (row/column counts, headers — never raw content/PII).
  2. Compares the current snapshot's schema against the previous snapshot's
     schema per dataset and warns/fails on critical identifier column loss.
  3. Updates data/snapshot_manifest.json (metadata only, append-only history).
  4. Activates the snapshot as the pipeline's input by COPYING (never
     moving/mutating) the four files into the locations load_data.py /
     message_intelligence.py already search: data/raw/ for Connections /
     Invitations / Company Follows, and the project root for messages.csv.
     Both locations are gitignored, so this cannot leak raw exports.
  5. Computes raw week-over-week deltas for connections, messages,
     invitations, and company follows, writing sanitized CSVs under outputs/.

It does NOT run the classification/strategy pipeline itself — run
build_network_heatmap.py / build_strategy_layer.py / generate_static_dashboard.py
afterward exactly as before. It also does NOT compute the post-classification
KPI delta (persona/opportunity-bucket movement) — see
src/weekly_kpi_delta.py, which runs AFTER the pipeline once enriched
fields exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import sys
from datetime import datetime, date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ROOT_DIR, DATA_RAW_DIR, OUTPUTS_DIR
from src.load_data import _read_csv_flexible
from src.company_normalizer import normalize as normalize_company

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

MANIFEST_PATH = ROOT_DIR / "data" / "snapshot_manifest.json"

# Directories that must never be mistaken for a dated snapshot folder.
NON_SNAPSHOT_DIRS = {
    "docs", "outputs", "reports", "config", "data", "src", "app", ".github",
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".idea",
    ".vscode",
}

SNAPSHOT_DIR_RE = re.compile(r"^(?:\d{2}-\d{2}|\d{4}-\d{2}-\d{2})$")

# ── Logical dataset -> filename-matching rules (case/space/underscore-insensitive) ──
LOGICAL_DATASETS = {
    "connections":      {"tokens": ["connection"]},
    "invitations":       {"tokens": ["invitation"]},
    "messages":          {"tokens": ["message"]},
    "company_follows":   {"tokens": ["company", "follow"]},
}

ACTIVE_TARGET = {
    "connections":     DATA_RAW_DIR / "Connections.csv",
    "invitations":      DATA_RAW_DIR / "Invitations.csv",
    "company_follows":  DATA_RAW_DIR / "Company Follows.csv",
    "messages":         ROOT_DIR / "messages.csv",
}

CRITICAL_COLUMNS = {
    "connections": ["First Name", "Last Name", "URL", "Company", "Position", "Connected On"],
    "messages":    ["CONVERSATION ID", "FROM", "TO", "DATE", "CONTENT"],
}


# ══════════════════════════════════════════════════════════════════════════
# PART 1 — Discover and validate the four snapshot files
# ══════════════════════════════════════════════════════════════════════════

def _norm_name(name: str) -> str:
    return re.sub(r"[\s_]+", " ", name.strip().lower()).replace(".csv", "").strip()


def discover_snapshot_files(folder: Path) -> dict[str, Path]:
    """Case/space/underscore-insensitively resolve the 4 logical datasets in `folder`."""
    if not folder.is_dir():
        raise FileNotFoundError(f"Snapshot folder not found: {folder}")

    csv_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".csv"]
    resolved: dict[str, Path] = {}

    for logical, rule in LOGICAL_DATASETS.items():
        match = None
        for f in csv_files:
            norm = _norm_name(f.name)
            if all(tok in norm for tok in rule["tokens"]):
                match = f
                break
        if match:
            resolved[logical] = match

    missing = [k for k in LOGICAL_DATASETS if k not in resolved]
    if missing:
        raise FileNotFoundError(
            f"Snapshot folder '{folder.name}' is missing required dataset(s): {missing}. "
            f"Found CSV files: {[f.name for f in csv_files]}"
        )
    return resolved


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _raw_row_count(path: Path) -> int:
    """Fast line-count based estimate (accounts for the LinkedIn preamble on Connections.csv)."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        lines = f.readlines()
    header_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("First Name,") or line.startswith('"First Name"'):
            header_idx = i
            break
        if i == 0 and "," in line and not line.lower().startswith("notes"):
            header_idx = 0
            break
    return max(0, len(lines) - header_idx - 1)


def print_validation_summary(logical: str, path: Path) -> dict:
    df = _read_csv_flexible(path)
    size_kb = path.stat().st_size // 1024
    summary = {
        "dataset": logical,
        "resolved_filename": path.name,
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "size_kb": size_kb,
    }
    logger.info(f"  [{logical}] file={path.name}  rows={len(df):,}  cols={len(df.columns)}  size={size_kb}KB")
    logger.info(f"  [{logical}] schema: {list(df.columns)}")
    return summary


# ══════════════════════════════════════════════════════════════════════════
# PART 2 — Schema compatibility check
# ══════════════════════════════════════════════════════════════════════════

def compare_schema(logical: str, current_cols: list[str], previous_cols: list[str]) -> dict:
    cur, prev = set(current_cols), set(previous_cols)
    added, removed = sorted(cur - prev), sorted(prev - cur)
    report = {"dataset": logical, "columns_added": added, "columns_removed": removed}

    critical = CRITICAL_COLUMNS.get(logical, [])
    lost_critical = [c for c in critical if c in prev and c not in cur]
    if lost_critical:
        raise ValueError(
            f"CRITICAL schema break in '{logical}': identifier column(s) {lost_critical} "
            f"disappeared vs previous snapshot. Refusing to continue silently."
        )
    if added or removed:
        logger.info(f"  [{logical}] schema drift — added={added} removed={removed} (non-critical, continuing)")
    else:
        logger.info(f"  [{logical}] schema unchanged vs previous snapshot")
    return report


# ══════════════════════════════════════════════════════════════════════════
# PART 3 — Snapshot folder auto-detection (for future weeks)
# ══════════════════════════════════════════════════════════════════════════

def _parse_snapshot_date(folder_name: str, explicit_date: str | None, ref_year: int) -> date | None:
    if explicit_date:
        try:
            return datetime.strptime(explicit_date, "%Y-%m-%d").date()
        except ValueError:
            pass
    if re.match(r"^\d{4}-\d{2}-\d{2}$", folder_name):
        try:
            return datetime.strptime(folder_name, "%Y-%m-%d").date()
        except ValueError:
            return None
    if re.match(r"^\d{2}-\d{2}$", folder_name):
        dd, mm = folder_name.split("-")
        try:
            return date(ref_year, int(mm), int(dd))
        except ValueError:
            return None
    return None


def find_previous_snapshot_folder(current_folder_name: str, ref_year: int) -> Path | None:
    """Auto-detect the most recent OTHER dated snapshot folder in the project root."""
    candidates = []
    for p in ROOT_DIR.iterdir():
        if not p.is_dir() or p.name in NON_SNAPSHOT_DIRS or p.name.startswith("."):
            continue
        if p.name == current_folder_name or not SNAPSHOT_DIR_RE.match(p.name):
            continue
        d = _parse_snapshot_date(p.name, None, ref_year)
        if d:
            candidates.append((d, p))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


# ══════════════════════════════════════════════════════════════════════════
# PART 4 — Snapshot manifest (metadata only)
# ══════════════════════════════════════════════════════════════════════════

def update_manifest(snapshot_id: str, snapshot_date: str, folder: str, row_counts: dict,
                     schema_hashes: dict, file_hashes: dict, previous_snapshot_id: str | None) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"snapshots": []}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("  Existing manifest unreadable — starting a fresh one (history preserved on disk).")

    manifest["snapshots"] = [s for s in manifest.get("snapshots", []) if s.get("snapshot_id") != snapshot_id]
    manifest["snapshots"].append({
        "snapshot_id": snapshot_id,
        "snapshot_date": snapshot_date,
        "folder": folder,
        "ingested_at": datetime.now().isoformat(timespec="seconds"),
        "connections_rows": row_counts.get("connections", 0),
        "invitations_rows": row_counts.get("invitations", 0),
        "messages_rows": row_counts.get("messages", 0),
        "company_follows_rows": row_counts.get("company_follows", 0),
        "schema_hashes": schema_hashes,
        "file_hashes": file_hashes,
        "previous_snapshot_id": previous_snapshot_id,
    })
    manifest["snapshots"].sort(key=lambda s: s["snapshot_date"])
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"  Manifest updated: {MANIFEST_PATH.relative_to(ROOT_DIR)} ({len(manifest['snapshots'])} snapshot(s) on record)")


# ══════════════════════════════════════════════════════════════════════════
# PART 5 — Activate snapshot as the pipeline's active input (copy, never mutate source)
# ══════════════════════════════════════════════════════════════════════════

def activate_snapshot(resolved: dict[str, Path]) -> None:
    DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for logical, target in ACTIVE_TARGET.items():
        src = resolved[logical]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        logger.info(f"  Activated {logical}: {src} -> {target}")


# ══════════════════════════════════════════════════════════════════════════
# Connections helpers (rename map mirrors load_data.py's load_connections())
# ══════════════════════════════════════════════════════════════════════════

_CONN_RENAME = {
    "First Name": "first_name", "Last Name": "last_name", "URL": "url",
    "Email Address": "email", "Company": "company", "Position": "position",
    "Connected On": "connected_on",
}


def _load_connections_raw(path: Path) -> pd.DataFrame:
    df = _read_csv_flexible(path)
    df = df.rename(columns={k: v for k, v in _CONN_RENAME.items() if k in df.columns})
    for col in _CONN_RENAME.values():
        if col not in df.columns:
            df[col] = ""
    df["url_norm"] = df["url"].fillna("").str.strip().str.rstrip("/").str.lower()
    df["name_company_key"] = (
        df["first_name"].fillna("").str.strip().str.lower() + " " +
        df["last_name"].fillna("").str.strip().str.lower() + " @ " +
        df["company"].fillna("").apply(normalize_company)
    )
    return df


def _identity_key(row) -> str:
    return row["url_norm"] if row["url_norm"] else row["name_company_key"]


# ══════════════════════════════════════════════════════════════════════════
# PART 6 — Week-over-week connection delta
# ══════════════════════════════════════════════════════════════════════════

def build_connection_delta(current_path: Path, previous_path: Path | None) -> dict:
    cur = _load_connections_raw(current_path)
    cur["identity_key"] = cur.apply(_identity_key, axis=1)

    if previous_path is None or not previous_path.exists():
        cur.assign(status="new")[["first_name", "last_name", "company", "position", "connected_on", "url"]].to_csv(
            OUTPUTS_DIR / "weekly_new_connections.csv", index=False, encoding="utf-8-sig"
        )
        pd.DataFrame(columns=["first_name", "last_name", "company", "position", "url"]).to_csv(
            OUTPUTS_DIR / "weekly_missing_connections.csv", index=False, encoding="utf-8-sig"
        )
        pd.DataFrame(columns=["identity_key", "field", "previous_value", "current_value"]).to_csv(
            OUTPUTS_DIR / "weekly_connection_changes.csv", index=False, encoding="utf-8-sig"
        )
        return {"previous_total": 0, "current_total": len(cur), "new_count": len(cur), "missing_count": 0, "changed_count": 0}

    prev = _load_connections_raw(previous_path)
    prev["identity_key"] = prev.apply(_identity_key, axis=1)

    cur_keys, prev_keys = set(cur["identity_key"]), set(prev["identity_key"])
    new_keys = cur_keys - prev_keys
    missing_keys = prev_keys - cur_keys
    common_keys = cur_keys & prev_keys

    new_df = cur[cur["identity_key"].isin(new_keys)][
        ["first_name", "last_name", "company", "position", "connected_on", "url"]
    ].copy()
    new_df.to_csv(OUTPUTS_DIR / "weekly_new_connections.csv", index=False, encoding="utf-8-sig")

    missing_df = prev[prev["identity_key"].isin(missing_keys)][
        ["first_name", "last_name", "company", "position", "url"]
    ].copy()
    missing_df["status"] = "not present in current snapshot"
    missing_df.to_csv(OUTPUTS_DIR / "weekly_missing_connections.csv", index=False, encoding="utf-8-sig")

    # Field-level changes for contacts present in both snapshots
    changes = []
    cur_idx = cur.set_index("identity_key")
    prev_idx = prev.set_index("identity_key")
    for key in common_keys:
        c, p = cur_idx.loc[key], prev_idx.loc[key]
        if isinstance(c, pd.DataFrame):
            c = c.iloc[0]
        if isinstance(p, pd.DataFrame):
            p = p.iloc[0]
        for field in ["company", "position"]:
            cv, pv = str(c.get(field, "") or "").strip(), str(p.get(field, "") or "").strip()
            if cv != pv and (cv or pv):
                changes.append({
                    "identity_key_hash": hashlib.sha1(key.encode()).hexdigest()[:10],
                    "full_name": f"{c.get('first_name','')} {c.get('last_name','')}".strip(),
                    "field": field, "previous_value": pv, "current_value": cv,
                })
    pd.DataFrame(changes, columns=["identity_key_hash", "full_name", "field", "previous_value", "current_value"]).to_csv(
        OUTPUTS_DIR / "weekly_connection_changes.csv", index=False, encoding="utf-8-sig"
    )

    logger.info(f"  Connections: previous={len(prev):,} current={len(cur):,} new={len(new_keys):,} "
                f"not_present_in_current={len(missing_keys):,} field_changes={len(changes):,}")
    return {
        "previous_total": len(prev), "current_total": len(cur),
        "new_count": len(new_keys), "missing_count": len(missing_keys), "changed_count": len(changes),
    }


# ══════════════════════════════════════════════════════════════════════════
# PART 7 — Weekly message delta
# ══════════════════════════════════════════════════════════════════════════

def build_message_delta(current_path: Path, previous_path: Path | None) -> dict:
    from src.message_intelligence import load_messages

    cur = load_messages(current_path)
    cur = cur if cur is not None else pd.DataFrame()

    def _msg_id(row) -> str:
        raw = f"{row.get('conversation_id','')}|{row.get('from','')}|{row.get('to','')}|{row.get('date','')}"
        content = str(row.get("content", "") or "")
        return hashlib.sha1((raw + "|" + hashlib.sha1(content.encode('utf-8', 'ignore')).hexdigest()).encode('utf-8', 'ignore')).hexdigest()

    if previous_path is None or not previous_path.exists():
        prev = pd.DataFrame()
    else:
        prev = load_messages(previous_path)
        prev = prev if prev is not None else pd.DataFrame()

    cur_ids = set(cur.apply(_msg_id, axis=1)) if not cur.empty else set()
    prev_ids = set(prev.apply(_msg_id, axis=1)) if not prev.empty else set()
    new_ids = cur_ids - prev_ids

    cur_convs = set(cur["conversation_id"]) if not cur.empty and "conversation_id" in cur.columns else set()
    prev_convs = set(prev["conversation_id"]) if not prev.empty and "conversation_id" in prev.columns else set()

    summary_df = pd.DataFrame([{
        "previous_message_count": len(prev), "current_message_count": len(cur),
        "new_message_count": len(new_ids),
        "previous_conversation_count": len(prev_convs), "current_conversation_count": len(cur_convs),
        "new_conversation_count": len(cur_convs - prev_convs),
    }])
    summary_df.to_csv(OUTPUTS_DIR / "weekly_new_message_count_summary.csv", index=False, encoding="utf-8-sig")

    changed_convs = []
    for conv_id in (cur_convs & prev_convs):
        cur_n = int((cur["conversation_id"] == conv_id).sum())
        prev_n = int((prev["conversation_id"] == conv_id).sum())
        if cur_n != prev_n:
            changed_convs.append({"conversation_id_hash": hashlib.sha1(str(conv_id).encode()).hexdigest()[:10],
                                   "previous_message_count": prev_n, "current_message_count": cur_n,
                                   "delta": cur_n - prev_n})
    pd.DataFrame(changed_convs, columns=["conversation_id_hash", "previous_message_count", "current_message_count", "delta"]).to_csv(
        OUTPUTS_DIR / "weekly_conversation_changes.csv", index=False, encoding="utf-8-sig"
    )

    logger.info(f"  Messages: previous={len(prev):,} current={len(cur):,} new={len(new_ids):,} "
                f"conversations current={len(cur_convs):,} new_conversations={len(cur_convs - prev_convs):,}")
    return {
        "previous_message_count": len(prev), "current_message_count": len(cur),
        "new_message_count": len(new_ids), "current_conversation_count": len(cur_convs),
    }


# ══════════════════════════════════════════════════════════════════════════
# PART 8 — Invitations delta
# ══════════════════════════════════════════════════════════════════════════

def build_invitation_delta(current_path: Path, previous_path: Path | None) -> dict:
    cur = _read_csv_flexible(current_path)
    prev = _read_csv_flexible(previous_path) if (previous_path and previous_path.exists()) else pd.DataFrame()

    def _counts(df: pd.DataFrame) -> dict:
        if df.empty or "Direction" not in df.columns:
            return {"total": len(df), "outgoing": None, "incoming": None}
        vc = df["Direction"].value_counts()
        return {"total": len(df), "outgoing": int(vc.get("OUTGOING", 0)), "incoming": int(vc.get("INCOMING", 0))}

    cur_c, prev_c = _counts(cur), _counts(prev)
    row = {
        "previous_total": prev_c["total"], "current_total": cur_c["total"],
        "total_delta": cur_c["total"] - prev_c["total"],
        "previous_outgoing": prev_c["outgoing"], "current_outgoing": cur_c["outgoing"],
        "previous_incoming": prev_c["incoming"], "current_incoming": cur_c["incoming"],
        "note": "Accepted/pending status not inferred — LinkedIn's Invitations export does not reliably indicate acceptance.",
    }
    pd.DataFrame([row]).to_csv(OUTPUTS_DIR / "weekly_invitation_summary.csv", index=False, encoding="utf-8-sig")
    logger.info(f"  Invitations: previous={prev_c['total']} current={cur_c['total']} "
                f"(out={cur_c['outgoing']} in={cur_c['incoming']})")
    return row


# ══════════════════════════════════════════════════════════════════════════
# PART 9 — Company Follows delta
# ══════════════════════════════════════════════════════════════════════════

def build_company_follows_delta(current_path: Path, previous_path: Path | None) -> dict:
    cur = _read_csv_flexible(current_path)
    org_col = "Organization" if "Organization" in cur.columns else cur.columns[0]
    cur["_key"] = cur[org_col].fillna("").apply(normalize_company)

    if previous_path is None or not previous_path.exists():
        out = cur[[org_col]].rename(columns={org_col: "organization"}).copy()
        out["status"] = "newly followed"
        out.to_csv(OUTPUTS_DIR / "weekly_company_follows_delta.csv", index=False, encoding="utf-8-sig")
        return {"previous_total": 0, "current_total": len(cur), "new_count": len(cur), "no_longer_present_count": 0}

    prev = _read_csv_flexible(previous_path)
    prev_org_col = "Organization" if "Organization" in prev.columns else prev.columns[0]
    prev["_key"] = prev[prev_org_col].fillna("").apply(normalize_company)

    cur_keys, prev_keys = set(cur["_key"]), set(prev["_key"])
    new_keys, gone_keys = cur_keys - prev_keys, prev_keys - cur_keys

    rows = []
    for _, r in cur[cur["_key"].isin(new_keys)].iterrows():
        rows.append({"organization": r[org_col], "status": "newly followed"})
    for _, r in prev[prev["_key"].isin(gone_keys)].iterrows():
        rows.append({"organization": r[prev_org_col], "status": "no longer present in current export"})
    pd.DataFrame(rows, columns=["organization", "status"]).to_csv(
        OUTPUTS_DIR / "weekly_company_follows_delta.csv", index=False, encoding="utf-8-sig"
    )
    logger.info(f"  Company Follows: previous={len(prev)} current={len(cur)} new={len(new_keys)} gone={len(gone_keys)}")
    return {
        "previous_total": len(prev), "current_total": len(cur),
        "new_count": len(new_keys), "no_longer_present_count": len(gone_keys),
        "net_change": len(cur) - len(prev),
    }


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Weekly LinkedIn snapshot ingestion + delta refresh")
    ap.add_argument("--snapshot-folder", required=True)
    ap.add_argument("--snapshot-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--previous-folder", default=None)
    ap.add_argument("--previous-date", default=None, help="YYYY-MM-DD")
    args = ap.parse_args()

    current_folder = ROOT_DIR / args.snapshot_folder
    ref_year = datetime.strptime(args.snapshot_date, "%Y-%m-%d").year

    logger.info("=" * 70)
    logger.info(f"  WEEKLY SNAPSHOT REFRESH — {args.snapshot_folder} ({args.snapshot_date})")
    logger.info("=" * 70)

    # ── Part 1: discover + validate current snapshot ────────────────────────
    logger.info("Step 1: Discovering and validating current snapshot files ...")
    current_resolved = discover_snapshot_files(current_folder)
    current_summaries = {k: print_validation_summary(k, v) for k, v in current_resolved.items()}

    # ── Previous snapshot resolution (explicit or auto-detected) ─────────────
    if args.previous_folder:
        previous_folder = ROOT_DIR / args.previous_folder
    else:
        previous_folder = find_previous_snapshot_folder(args.snapshot_folder, ref_year)
        if previous_folder:
            logger.info(f"  Auto-detected previous snapshot folder: {previous_folder.name}")
        else:
            logger.warning("  No previous snapshot folder found — treating this as the first snapshot.")

    previous_resolved = {}
    if previous_folder and previous_folder.exists():
        logger.info(f"Step 1b: Discovering previous snapshot files in '{previous_folder.name}' ...")
        previous_resolved = discover_snapshot_files(previous_folder)
        for k, v in previous_resolved.items():
            print_validation_summary(k, v)

    # ── Part 2: schema compatibility ─────────────────────────────────────────
    logger.info("Step 2: Validating schema compatibility ...")
    schema_hashes = {}
    for logical, summary in current_summaries.items():
        schema_hashes[logical] = hashlib.sha1(",".join(summary["columns"]).encode()).hexdigest()[:12]
        if logical in previous_resolved:
            prev_df_cols = list(_read_csv_flexible(previous_resolved[logical]).columns)
            compare_schema(logical, summary["columns"], prev_df_cols)

    # ── Part 4: manifest ──────────────────────────────────────────────────────
    logger.info("Step 3: Updating snapshot manifest ...")
    row_counts = {k: v["row_count"] for k, v in current_summaries.items()}
    file_hashes = {k: _file_hash(v) for k, v in current_resolved.items()}
    snapshot_id = f"snap-{args.snapshot_date}"
    previous_snapshot_id = f"snap-{args.previous_date}" if args.previous_date else (
        f"snap-{previous_folder.name}" if previous_folder else None
    )
    update_manifest(snapshot_id, args.snapshot_date, args.snapshot_folder, row_counts,
                     schema_hashes, file_hashes, previous_snapshot_id)

    # ── Part 5: activate as pipeline input ────────────────────────────────────
    logger.info("Step 4: Activating snapshot as active pipeline input (copy only) ...")
    activate_snapshot(current_resolved)

    # ── Parts 6-9: raw snapshot deltas ────────────────────────────────────────
    logger.info("Step 5: Computing week-over-week deltas ...")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    conn_delta = build_connection_delta(
        current_resolved["connections"], previous_resolved.get("connections")
    )
    msg_delta = build_message_delta(
        current_resolved["messages"], previous_resolved.get("messages")
    )
    inv_delta = build_invitation_delta(
        current_resolved["invitations"], previous_resolved.get("invitations")
    )
    cf_delta = build_company_follows_delta(
        current_resolved["company_follows"], previous_resolved.get("company_follows")
    )

    logger.info("=" * 70)
    logger.info("  SNAPSHOT REFRESH COMPLETE")
    logger.info(f"  Connections: {conn_delta}")
    logger.info(f"  Messages:    {msg_delta}")
    logger.info(f"  Invitations: {inv_delta}")
    logger.info(f"  Company Follows: {cf_delta}")
    logger.info("=" * 70)
    logger.info("  Next: run the full pipeline —")
    logger.info("    python src/build_network_heatmap.py")
    logger.info("    python src/build_strategy_layer.py")
    logger.info("    python src/weekly_kpi_delta.py")
    logger.info("    python src/generate_static_dashboard.py")


if __name__ == "__main__":
    main()
