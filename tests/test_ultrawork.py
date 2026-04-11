"""Tests for ultrawork profile registry and execution contracts."""

from __future__ import annotations

import pytest

from research_copilot.services.ultrawork import (
    build_ultrawork_run_plan,
    get_ultrawork_profile,
    list_ultrawork_profiles,
)


def test_registry_lists_all_approved_profiles():
    profiles = list_ultrawork_profiles()

    assert [profile.name for profile in profiles] == [
        "ops-triage",
        "experiment-launch",
        "run-review",
        "literature-context",
        "incident-recovery",
    ]
    assert all(len(profile.lanes) == 3 for profile in profiles)


def test_run_plan_returns_agent_safe_contract():
    contract = build_ultrawork_run_plan("ops-triage", goal="diagnose active runs")

    assert contract["profile"]["name"] == "ops-triage"
    assert contract["goal"] == "diagnose active runs"
    assert contract["status"] == "ready"
    assert contract["lane_count"] == 3
    assert "read-only" in contract["notes"][1]


def test_unknown_profile_returns_none_and_run_plan_errors():
    assert get_ultrawork_profile("missing-profile") is None

    with pytest.raises(KeyError):
        build_ultrawork_run_plan("missing-profile")
