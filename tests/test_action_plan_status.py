# -*- coding: utf-8 -*-
"""
tests/test_action_plan_status.py
==================================
USD/LATAM remote is the primary objective; Spain/EU is exploratory. A low
Spain/EU share (which implies a >95% LATAM/USD share) must stay advisory and
never produce REBALANCE_NEEDED on its own.

Run: python -m pytest tests/test_action_plan_status.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.action_plan_progress import overall_diagnosis_status

_NET_OK = {"new_connections_status": "AHEAD"}


def test_low_spain_eu_share_is_not_rebalance():
    rules = [{"rule": "europe_share_below_5"}, {"rule": "classification_backlog_increased"}]
    assert overall_diagnosis_status(rules, _NET_OK, True) == "ON_TRACK"


def test_real_allocation_drift_still_rebalances():
    assert overall_diagnosis_status([{"rule": "primary_share_below_80"}], _NET_OK, True) == "REBALANCE_NEEDED"
    assert overall_diagnosis_status([{"rule": "europe_share_above_20"}], _NET_OK, True) == "REBALANCE_NEEDED"
