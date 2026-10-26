# -*- coding: utf-8 -*-
"""
tests/test_untapped_activation_consistency.py
===============================================
The Weekly Evolution "Activated This Week" card must equal the size of the
identity-level activated_this_week drilldown it opens — not the aggregate
-Δ never_contacted estimate, which is net of new uncontacted connections.
Activation Pattern Learning's weekly window is anchored to the snapshot date.

All data below is synthetic.

Run: python -m pytest tests/test_untapped_activation_consistency.py -q
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.untapped_network_intelligence as uni
from src.weekly_kpi_delta import _reconcile_untapped_activation


def test_activation_card_matches_identity_drilldown():
    evolution = {"untapped_network_movement": {
        "available": True, "tracked_since_last_snapshot": True,
        "never_contacted_confirmed_delta": 15,   # gross inflow hides activations
        "untapped_contacts_activated_this_week_estimate": 0,
    }}
    segments = [{"segment": "activated_this_week"}] * 7 + [{"segment": "new_this_week"}] * 190
    _reconcile_untapped_activation(evolution, segments)
    um = evolution["untapped_network_movement"]
    assert um["untapped_contacts_activated_this_week_estimate"] == 7
    assert um["untapped_contacts_activated_this_week"] == 7
    assert um["never_contacted_net_change_estimate"] == 0   # aggregate kept under its own name


def test_activation_reconcile_skips_untracked_baseline():
    evolution = {"untapped_network_movement": {"available": True, "tracked_since_last_snapshot": False}}
    _reconcile_untapped_activation(evolution, [{"segment": "activated_this_week"}])
    assert "untapped_contacts_activated_this_week" not in evolution["untapped_network_movement"]


def test_pattern_learning_reference_date_is_snapshot_date(tmp_path, monkeypatch):
    (tmp_path / "data" / "processed").mkdir(parents=True)
    (tmp_path / "data" / "processed" / "connection_freshness.json").write_text(
        json.dumps({"current_snapshot_date": "2026-10-25"}), encoding="utf-8")
    monkeypatch.setattr(uni, "ROOT", tmp_path)
    assert uni._snapshot_reference_date() == date(2026, 10, 25)


def test_pattern_learning_reference_date_falls_back_to_today(tmp_path, monkeypatch):
    monkeypatch.setattr(uni, "ROOT", tmp_path)
    assert uni._snapshot_reference_date() == date.today()
