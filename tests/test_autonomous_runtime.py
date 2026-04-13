"""Contract harness for the persistent autonomous runtime lane."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json

import pytest

from research_copilot.research_state import (
    list_autonomous_runtime_events,
    load_autonomous_runtime,
    load_autonomous_runtime_history,
    save_autonomous_runtime,
)

MODULE_NAME = "research_copilot.services.autonomous_runtime"
LIFECYCLE_EXPORT_CANDIDATES = {
    "start": ("start_autonomous_runtime", "run_autonomous_runtime", "autonomous_run"),
    "status": ("get_autonomous_runtime_status", "autonomous_status", "read_autonomous_runtime"),
    "stop": ("stop_autonomous_runtime", "autonomous_stop"),
    "resume": ("resume_autonomous_runtime", "autonomous_resume"),
}
REQUIRED_STATUS_VALUES = {"running", "stopping", "stopped", "completed", "failed", "stale"}


def _load_runtime_module():
    if importlib.util.find_spec(MODULE_NAME) is None:
        pytest.skip("Lane 1 autonomous runtime service is not available in this checkout yet.")
    return importlib.import_module(MODULE_NAME)


def _find_lifecycle_exports(module) -> dict[str, object | None]:
    return {
        phase: next((getattr(module, name) for name in names if hasattr(module, name)), None)
        for phase, names in LIFECYCLE_EXPORT_CANDIDATES.items()
    }


def test_autonomous_runtime_service_exposes_v1_lifecycle_entrypoints():
    module = _load_runtime_module()

    exports = _find_lifecycle_exports(module)
    missing = [phase for phase, candidate in exports.items() if candidate is None]

    assert not missing, (
        "Autonomous runtime service should expose v1 lifecycle entrypoints for: "
        + ", ".join(missing)
    )


def test_autonomous_runtime_service_mentions_v1_lifecycle_states():
    module = _load_runtime_module()

    source = inspect.getsource(module).lower()

    for status in REQUIRED_STATUS_VALUES:
        assert status in source


@pytest.mark.asyncio
async def test_autonomous_status_reports_stale_without_mutating_runtime(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = _load_runtime_module()

    save_autonomous_runtime(
        {
            "schema_version": "1.0",
            "run_id": "run-1",
            "status": "running",
            "goal": "proof",
            "profile_name": "goal-chaser",
            "iteration": 1,
            "updated_at": "2026-04-13T00:00:00+00:00",
            "started_at": "2026-04-13T00:00:00+00:00",
            "last_heartbeat_at": "2026-04-13T00:00:00+00:00",
            "lease_expires_at": "2026-04-13T00:00:01+00:00",
            "owner_pid": 999999,
            "owner_token": "secret-token",
        }
    )
    before = json.dumps(load_autonomous_runtime(), sort_keys=True)

    payload = await module.autonomous_status(run_id="run-1")

    after = json.dumps(load_autonomous_runtime(), sort_keys=True)
    assert payload["status"] == "stale"
    assert after == before
    assert load_autonomous_runtime_history("run-1") == {}
    assert list_autonomous_runtime_events("run-1") == []


@pytest.mark.asyncio
async def test_autonomous_resume_clears_previous_owner_instance_id(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = _load_runtime_module()

    save_autonomous_runtime(
        {
            "schema_version": "1.0",
            "run_id": "run-1",
            "status": "stopped",
            "goal": "proof",
            "profile_name": "goal-chaser",
            "iteration": 1,
            "updated_at": "2026-04-13T00:00:00+00:00",
            "started_at": "2026-04-13T00:00:00+00:00",
            "last_heartbeat_at": "2026-04-13T00:00:00+00:00",
            "lease_expires_at": "2026-04-13T00:00:01+00:00",
            "owner_pid": 1234,
            "owner_token": "old-token",
            "owner_instance_id": "old-instance",
        }
    )

    payload = await module.autonomous_resume(run_id="run-1", owner_token="old-token")

    assert payload["status"] == "running"
    assert payload["runtime"]["owner_instance_id"] == ""
    assert payload["runtime"]["owner_token"] != "old-token"


@pytest.mark.asyncio
async def test_autonomous_start_creates_single_active_generation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = _load_runtime_module()

    payload = await module.autonomous_run(
        goal="proof",
        brain_driver="codex",
        profile_name="goal-chaser",
        command_template='python -c "print(1)"',
        max_iterations=2,
        actor_type="assistant",
    )

    runtime = load_autonomous_runtime()

    assert payload["status"] == "running"
    assert runtime["generation_id"]
    assert runtime["brain_driver"] == "codex"
    assert runtime["health_state"] == "managed_degraded"
    assert runtime["runtime_id"] == runtime["run_id"]
    assert load_autonomous_runtime_history(runtime["run_id"]) == {}


@pytest.mark.asyncio
async def test_autonomous_continue_reuses_healthy_active_generation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = _load_runtime_module()

    created = await module.autonomous_run(
        goal="proof",
        brain_driver="workflow",
        profile_name="goal-chaser",
        command_template='python -c "print(1)"',
        max_iterations=2,
        actor_type="assistant",
    )
    active = load_autonomous_runtime()
    active["last_report_at"] = "2026-04-13T00:00:10+00:00"
    active["health_state"] = "managed_healthy"
    save_autonomous_runtime(active)

    payload = await module.autonomous_continue(run_id=created["run_id"], brain_driver="workflow")
    current = load_autonomous_runtime()

    assert payload["run_id"] == created["run_id"]
    assert current["generation_id"] == active["generation_id"]
    assert current["brain_driver"] == "workflow"
    assert current["status"] == "running"


@pytest.mark.asyncio
async def test_autonomous_worker_preserves_stop_requested_during_inflight_action(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    module = _load_runtime_module()

    started = asyncio.Event()
    finish = asyncio.Event()

    async def fake_execute_action(action: str, inputs: dict[str, object]) -> dict[str, object]:
        started.set()
        await finish.wait()
        return {
            "experiment": {"id": "exp-1", "name": "exp-1"},
            "run": {"run_id": "run-1", "status": "completed"},
            "metrics": {},
        }

    monkeypatch.setattr(module, "_execute_action", fake_execute_action)
    monkeypatch.setattr(
        module,
        "_decide_next_action",
        lambda runtime, snapshot: {
            "action": "run-experiment",
            "inputs": {
                "command": "python -c \"print(1)\"",
                "name": "exp-1",
                "hypothesis": "proof",
                "dataset": "",
                "model_type": "",
            },
            "reason": "test action",
            "blocked": False,
        },
    )
    monkeypatch.setattr(
        module,
        "_success_criteria_satisfied",
        lambda runtime, snapshot: (False, ""),
    )
    monkeypatch.setattr(
        module,
        "build_workflow_snapshot",
        lambda max_items=5: {"experiments": {"items": [], "by_status": {}}},
    )

    created = await module.autonomous_run(
        goal="proof",
        profile_name="goal-chaser",
        command_template='python -c "print(1)"',
        max_iterations=3,
        actor_type="assistant",
    )
    run_id = created["run_id"]
    owner_token = created["owner_token"]

    worker_task = asyncio.create_task(
        module.run_autonomous_worker(run_id, owner_token, owner_instance_id="worker-instance-1")
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    await module.autonomous_stop(
        run_id=run_id,
        reason="stop-during-action",
        owner_token=owner_token,
        actor_type="assistant",
    )
    finish.set()
    payload = await asyncio.wait_for(worker_task, timeout=5)

    assert payload["status"] == "stopped"
    assert payload["runtime"]["stop_reason"] == "stop-during-action"


@pytest.mark.asyncio
async def test_autonomous_stop_and_resume_require_owner_token(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module = _load_runtime_module()

    created = await module.autonomous_run(
        goal="proof",
        profile_name="goal-chaser",
        command_template='python -c "print(1)"',
        max_iterations=2,
        actor_type="assistant",
    )
    run_id = created["run_id"]

    with pytest.raises(ValueError, match="owner_token is required"):
        await module.autonomous_stop(run_id=run_id, reason="nope")

    with pytest.raises(ValueError, match="owner_token is required"):
        await module.autonomous_resume(run_id=run_id)
