# -*- coding: utf-8 -*-
"""
tests/test_payload_split.py
=============================
Regression fixtures for the USD Contract CRM sub-split
(PAGE_PARTS / split_page_parts / merge_page_parts in
src/export_public_dashboard_data.py) and the matching frontend merge in
docs/assets/app.js. All data below is synthetic.

Run: python -m pytest tests/test_payload_split.py -q
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import export_public_dashboard_data as ex


def _usdcrm_page():
    return {"usd_contract_crm": {
        "available": True,
        "summary": {"high_fit_usd_remote_leads": 3},
        "auto_suggested_usd_leads": [{"name": "A"}, {"name": "B"}],
        "recruiter_pipeline": [{"name": "A"}],
        "follow_up_queue": [{"name": "C"}],
        "first_outreach_queue": [{"name": "D"}],
        "opportunity_history": {"available": True, "events": [{"event_id": "e1"}]},
        "monthly_executive_queue": {"available": True},
    }}


def test_split_then_merge_round_trips_exactly():
    pages = {"usdcrm": _usdcrm_page(), "heatmap": {"heatmaps": {"x": 1}}}
    split_pages, parts = ex.split_page_parts(pages)
    merged = ex.merge_page_parts(split_pages["usdcrm"], "usdcrm", parts["usdcrm"])
    assert merged == pages["usdcrm"]
    # Other pages untouched, original input not mutated.
    assert split_pages["heatmap"] == pages["heatmap"]
    assert "auto_suggested_usd_leads" in pages["usdcrm"]["usd_contract_crm"]


def test_each_sub_key_lives_in_exactly_one_file():
    split_pages, parts = ex.split_page_parts({"usdcrm": _usdcrm_page()})
    core_keys = set(split_pages["usdcrm"]["usd_contract_crm"])
    seen = list(core_keys)
    for part in parts["usdcrm"].values():
        seen += list(part)
    assert len(seen) == len(set(seen))
    assert {"summary", "monthly_executive_queue", "available"} <= core_keys
    assert "opportunity_history" not in core_keys
    assert set(parts["usdcrm"]) == set(ex.PAGE_PARTS["usdcrm"]["parts"])


def test_missing_sub_keys_produce_no_empty_part():
    page = {"usd_contract_crm": {"available": True, "summary": {}}}
    _, parts = ex.split_page_parts({"usdcrm": page})
    assert parts["usdcrm"] == {}


def test_write_split_emits_parts_in_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(ex, "PUBLIC_JSON_DOCS", tmp_path / "dashboard_data.json")
    payload = {"meta": {"report_date": "2026-11-08"}, "kpis": {},
               "usd_contract_crm": _usdcrm_page()["usd_contract_crm"]}
    report = ex.write_split_dashboard_data(payload)
    manifest = json.loads((tmp_path / "dashboard_data.json").read_text(encoding="utf-8"))
    parts = manifest["page_manifest"]["usdcrm"]["parts"]
    assert {p["file"] for p in parts} == {"data/" + f for f in ex.PAGE_PARTS["usdcrm"]["parts"]}
    for p in parts:
        assert p["merge_into"] == "usd_contract_crm"
        data = json.loads((tmp_path / p["file"]).read_text(encoding="utf-8"))
        assert set(data) == set(p["keys"])
    assert set(report["parts"]) == set(ex.PAGE_PARTS["usdcrm"]["parts"])
    # Local reader reassembles the full object.
    full = ex.load_page_with_parts("usdcrm")["usd_contract_crm"]
    assert full == payload["usd_contract_crm"]


def test_frontend_loader_merges_parts():
    js = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "info.parts" in js
    assert "D[pt.merge_into] = Object.assign(D[pt.merge_into] || {}, partJsons[i])" in js
