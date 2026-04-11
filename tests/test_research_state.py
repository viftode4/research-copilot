"""Tests for the file-backed research state contract."""

from __future__ import annotations

import json

from research_copilot.research_state import (
    FileBackedCollection,
    build_provenance,
    get_research_root,
    load_onboarding_contract,
    save_record,
    save_onboarding_contract,
)


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
