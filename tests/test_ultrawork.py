"""Tests for ultrawork profile registry and execution contracts."""

from __future__ import annotations

import json

import pytest

from research_copilot.research_state import save_onboarding_contract
from research_copilot.services.ultrawork import (
    build_ultrawork_run_plan,
    execute_ultrawork_profile,
    get_ultrawork_profile,
    list_ultrawork_profiles,
)


def test_registry_lists_all_approved_profiles():
    profiles = list_ultrawork_profiles()

    assert [profile.name for profile in profiles] == [
        "goal-chaser",
        "baseline-improver",
        "overfit-hunter",
        "result-reasoner",
        "explore-improver",
    ]
    assert all(len(profile.lanes) == 3 for profile in profiles)


def test_run_plan_returns_agent_safe_contract():
    contract = build_ultrawork_run_plan("goal-chaser", goal="diagnose active runs")

    assert contract["profile"]["name"] == "goal-chaser"
    assert contract["goal"] == "diagnose active runs"
    assert contract["status"] == "ready"
    assert contract["lane_count"] == 3
    assert "--execute" in contract["notes"][1]
    assert "persistent" not in json.dumps(contract).lower()
    assert "runtime_id" not in contract


def test_unknown_profile_returns_none_and_run_plan_errors():
    assert get_ultrawork_profile("missing-profile") is None

    with pytest.raises(KeyError):
        build_ultrawork_run_plan("missing-profile")


def test_run_plan_can_resolve_active_profile_from_onboarding(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    save_onboarding_contract(
        goal="Diagnose overfitting quickly",
        success_criteria="One completed diagnosis",
        active_profile="overfit-hunter",
        autonomy_level="bounded",
        allowed_actions=["launch runs"],
        constraints=["single-user only"],
        stop_conditions=["stop on repeated failure"],
        notes="",
    )

    contract = build_ultrawork_run_plan("active")

    assert contract["profile"]["name"] == "overfit-hunter"
    assert contract["goal"] == "Diagnose overfitting quickly"


@pytest.mark.asyncio
async def test_execute_ultrawork_profile_runs_bounded_local_loop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    save_onboarding_contract(
        goal="Check for overfitting",
        success_criteria="Persist one diagnosis",
        active_profile="overfit-hunter",
        autonomy_level="bounded",
        allowed_actions=["launch runs", "review results"],
        constraints=["single-user only"],
        stop_conditions=["stop when diagnosis exists"],
        notes="",
    )

    payload = await execute_ultrawork_profile(
        "active",
        command='python -c "import json; print(json.dumps({\'train_loss\': 0.05, \'val_loss\': 0.20, \'test_loss\': 0.25}))"',
        name="bounded-run",
        hypothesis="Generalization gap is present",
    )

    assert payload["status"] == "completed"
    assert payload["profile"]["name"] == "overfit-hunter"
    assert payload["steps"][0]["step"] == "run-experiment"
    assert payload["steps"][1]["step"] == "overfitting-check"
    assert payload["steps"][2]["step"] == "next-step"
    serialized = json.dumps(payload).lower()
    assert "autonomous" not in serialized
    assert "resume" not in serialized
