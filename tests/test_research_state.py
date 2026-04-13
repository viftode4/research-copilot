"""Tests for the file-backed research state contract."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

import research_copilot.research_state as research_state_module
from research_copilot.research_state import (
    FileBackedCollection,
    build_provenance,
    ensure_research_root,
    get_last_workspace,
    get_recent_workspaces_registry_path,
    get_research_root,
    load_onboarding_contract,
    load_recent_workspaces,
    process_is_running,
    remember_workspace,
    save_record,
    save_onboarding_contract,
    autonomous_runtime_is_stale,
)


@pytest.fixture(autouse=True)
def clean_research_state_env(monkeypatch) -> None:
    for variable in ("RC_RESEARCH_ROOT", "RC_WORKING_DIR", "RC_GLOBAL_HOME"):
        monkeypatch.delenv(variable, raising=False)


def test_save_record_uses_canonical_root_and_storage_contract(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)

    saved = save_record(
        "experiments",
        {
            "id": "exp-1",
            "name": "seeded experiment",
            "status": "planned",
            "created_at": "2026-04-11T15:00:00+00:00",
            "updated_at": "2026-04-11T15:00:00+00:00",
        },
        args={"actor_type": "codex", "workflow_name": "launch-experiment"},
    )

    artifact = get_research_root() / "experiments" / "exp-1.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    assert artifact.exists()
    assert not list(artifact.parent.glob("*.tmp"))
    assert payload["schema_version"] == "1.0"
    assert payload["provenance"]["actor"] == "codex"
    assert payload["provenance"]["workflow_name"] == "launch-experiment"
    assert payload["provenance"]["content_kind"] == "observed"
    assert saved["schema_version"] == "1.0"


def test_file_backed_collection_uses_keyed_context_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    collection = FileBackedCollection("context", default_content_kind="inferred")
    collection.append(
        {
            "id": "ctx-1",
            "key": "Current Goal",
            "value": "Ship the CLI MVP",
            "context_type": "goal",
            "updated_at": "2026-04-11T15:00:00+00:00",
        }
    )

    artifact = get_research_root() / "context" / "current-goal.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    assert len(collection) == 1
    assert artifact.exists()
    assert payload["key"] == "Current Goal"
    assert payload["provenance"]["content_kind"] == "inferred"


def test_build_provenance_captures_related_ids() -> None:
    provenance = build_provenance(
        {
            "actor_type": "claude",
            "workflow_name": "review-results",
            "linked_experiment_id": "exp-123",
            "linked_job_id": "job-456",
        },
        content_kind="inferred",
        timestamp="2026-04-11T15:00:00+00:00",
    )

    assert provenance == {
        "actor": "claude",
        "timestamp": "2026-04-11T15:00:00+00:00",
        "workflow_name": "review-results",
        "profile_name": "",
        "related_goal_id": "",
        "related_experiment_id": "exp-123",
        "related_run_id": "",
        "related_job_id": "job-456",
        "content_kind": "inferred",
    }


def test_save_onboarding_contract_persists_json_and_markdown(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    saved = save_onboarding_contract(
        goal="Reduce overfitting on the baseline model",
        success_criteria="Validation gap below 2%",
        active_profile="overfit-hunter",
        autonomy_level="bounded",
        allowed_actions=["create experiments", "launch runs"],
        constraints=["single-user only", "local machine only"],
        stop_conditions=["stop after 3 failed runs"],
        notes="Start with regularization ablations.",
        actor="human",
    )

    root = get_research_root()
    json_path = root / "onboarding" / "current.json"
    md_path = root / "onboarding" / "interview.md"

    assert json_path.exists()
    assert md_path.exists()
    assert saved["goal"] == "Reduce overfitting on the baseline model"
    assert saved["provenance"]["actor"] == "human"
    assert load_onboarding_contract()["active_profile"] == "overfit-hunter"
    assert "Validation gap below 2%" in md_path.read_text(encoding="utf-8")


def test_ensure_research_root_creates_canonical_directories(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RC_WORKING_DIR", str(tmp_path))

    root = ensure_research_root()

    assert root == tmp_path / ".research-copilot"
    for directory in (
        "onboarding",
        "goals",
        "experiments",
        "runs",
        "reviews",
        "notes",
        "papers",
        "context",
        "profiles",
        "insights",
    ):
        assert (root / directory).is_dir()


def test_get_research_root_honors_working_dir_override_for_workspace_isolation(
    monkeypatch, tmp_path
) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    monkeypatch.setenv("RC_WORKING_DIR", str(workspace_a))
    root_a = ensure_research_root()

    monkeypatch.setenv("RC_WORKING_DIR", str(workspace_b))
    root_b = ensure_research_root()

    assert root_a == workspace_a / ".research-copilot"
    assert root_b == workspace_b / ".research-copilot"
    assert root_a != root_b


def test_recent_workspace_registry_stays_global_and_does_not_change_local_roots(
    monkeypatch, tmp_path
) -> None:
    global_home = tmp_path / "global-home"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    monkeypatch.setenv("RC_GLOBAL_HOME", str(global_home))

    monkeypatch.setenv("RC_WORKING_DIR", str(workspace_a))
    root_a = ensure_research_root()
    remember_workspace(root_a)

    monkeypatch.setenv("RC_WORKING_DIR", str(workspace_b))
    root_b = ensure_research_root()
    registry = remember_workspace(root_b)

    registry_path = get_recent_workspaces_registry_path()

    assert registry_path == global_home / "recent-workspaces.json"
    assert load_recent_workspaces()["last_workspace"] == str(workspace_b)
    assert get_last_workspace() == str(workspace_b)
    assert registry["workspaces"][0] == str(workspace_b)
    assert str(workspace_a) in registry["workspaces"]
    assert root_a == workspace_a / ".research-copilot"
    assert root_b == workspace_b / ".research-copilot"


def test_canonical_research_root_uses_standalone_product_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RC_WORKING_DIR", str(tmp_path))

    root = ensure_research_root()

    assert root == tmp_path / ".research-copilot"
    assert (tmp_path / ".omx" / "research").exists() is False


def test_workspace_metadata_uses_workspace_dir_not_internal_state_root(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RC_WORKING_DIR", str(tmp_path))

    ensure_research_root()
    workspace_metadata = json.loads((tmp_path / ".research-copilot" / "workspace.json").read_text())

    assert workspace_metadata["workspace_root"] == str(tmp_path)


def test_process_is_running_reports_current_process_alive() -> None:
    assert process_is_running(os.getpid()) is True


def test_autonomous_runtime_is_stale_ignores_live_owner_with_future_lease() -> None:
    payload = {
        "status": "running",
        "owner_pid": os.getpid(),
        "lease_expires_at": "2999-01-01T00:00:00+00:00",
    }

    assert autonomous_runtime_is_stale(payload) is False


def test_recent_workspace_registry_tracks_workspace_dirs_even_for_new_root(
    monkeypatch, tmp_path
) -> None:
    global_home = tmp_path / "global-home"
    workspace = tmp_path / "workspace-a"
    workspace.mkdir()
    monkeypatch.setenv("RC_GLOBAL_HOME", str(global_home))
    monkeypatch.setenv("RC_WORKING_DIR", str(workspace))

    root = ensure_research_root()
    registry = remember_workspace(root)

    assert root == workspace / ".research-copilot"
    assert registry["last_workspace"] == str(workspace)
    assert registry["workspaces"][0] == str(workspace)


def test_remember_workspace_uses_collision_safe_temp_files_for_concurrent_writes(
    monkeypatch, tmp_path
) -> None:
    global_home = tmp_path / "global-home"
    workspace = tmp_path / "workspace-a"
    workspace.mkdir()
    monkeypatch.setenv("RC_GLOBAL_HOME", str(global_home))
    monkeypatch.setenv("RC_WORKING_DIR", str(workspace))

    ensure_research_root()
    registry_path = get_recent_workspaces_registry_path()
    original_replace = research_state_module.Path.replace
    release_replacements = threading.Event()
    replacement_calls = 0
    replacement_lock = threading.Lock()
    errors: list[Exception] = []

    def synchronized_replace(self, target):
        nonlocal replacement_calls
        if Path(target) == registry_path:
            with replacement_lock:
                replacement_calls += 1
                if replacement_calls == 2:
                    release_replacements.set()
            assert release_replacements.wait(timeout=2)
        return original_replace(self, target)

    monkeypatch.setattr(research_state_module.Path, "replace", synchronized_replace)

    def remember() -> None:
        try:
            remember_workspace(workspace)
        except Exception as exc:  # pragma: no cover - failure path asserted below
            errors.append(exc)

    first = threading.Thread(target=remember)
    second = threading.Thread(target=remember)
    first.start()
    second.start()
    first.join()
    second.join()

    assert errors == []
    assert replacement_calls >= 2
    assert load_recent_workspaces()["last_workspace"] == str(workspace)
    assert list(registry_path.parent.glob("recent-workspaces.json*.tmp")) == []


def test_legacy_omx_root_can_be_read_without_rewriting_during_transition(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "legacy-workspace"
    legacy_root = workspace / ".omx" / "research"
    (legacy_root / "onboarding").mkdir(parents=True)
    (legacy_root / "workspace.json").write_text(
        json.dumps({"schema_version": "1.0", "workspace_root": str(workspace)}),
        encoding="utf-8",
    )
    (legacy_root / "onboarding" / "current.json").write_text(
        json.dumps({"goal": "Legacy goal", "active_profile": "goal-chaser"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("RC_WORKING_DIR", str(workspace))

    contract = load_onboarding_contract()

    assert contract["goal"] == "Legacy goal"
    assert (workspace / ".research-copilot").exists() is False
