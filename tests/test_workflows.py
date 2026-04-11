"""Tests for named workflow orchestration commands."""

from __future__ import annotations

import json

import pytest

from research_copilot.mcp_servers.knowledge_base import _store
from research_copilot.mcp_servers.slurm import _mock_jobs
from research_copilot.services.workflows import (
    launch_experiment,
    monitor_run,
    onboard,
    onboarding_summary,
    overfitting_check,
    next_step,
    research_context,
    review_results,
    run_experiment,
    triage,
)


@pytest.fixture(autouse=True)
def clean_state():
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()
    yield
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()


@pytest.mark.asyncio
async def test_triage_reports_active_blockers_and_suggested_action():
    await launch_experiment(
        name="PFN triage",
        script="#!/bin/bash\npython train.py",
        hypothesis="test hypothesis",
    )

    result = await triage()

    assert result["workflow"] == "triage"
    assert result["snapshot"]["jobs"]["active"] == 1
    assert result["suggested_next_action"] == "monitor-run"
    assert "active job" in result["blockers"][0].lower()


@pytest.mark.asyncio
async def test_launch_experiment_links_job_and_records_provenance():
    result = await launch_experiment(
        name="PFN launch",
        script="#!/bin/bash\npython train.py --epochs 5",
        hypothesis="PFN should converge faster",
        dataset="LCDB",
        model_type="PFN",
        tags=["pfn", "smoke"],
        created_by="worker-2",
    )

    experiment = result["experiment"]
    job = result["job"]

    assert experiment["slurm_job_id"] == job["job_id"]
    assert experiment["workflow_name"] == "launch-experiment"
    assert experiment["actor_type"] == "codex"
    assert _mock_jobs[job["job_id"]].workflow_name == "launch-experiment"
    assert _mock_jobs[job["job_id"]].submitted_by == "worker-2"


@pytest.mark.asyncio
async def test_monitor_run_updates_completed_experiment_results():
    launched = await launch_experiment(
        name="PFN monitor",
        script="#!/bin/bash\npython train.py --epochs 5",
    )
    experiment_id = launched["experiment"]["id"]

    first = await monitor_run(identifier=experiment_id, kind="experiment")
    second = await monitor_run(identifier=experiment_id, kind="experiment")

    assert first["job"]["status"] == "RUNNING"
    assert second["job"]["status"] == "COMPLETED"
    assert second["experiment"]["status"] == "completed"
    assert second["experiment"]["workflow_name"] == "monitor-run"
    assert second["experiment"]["results"]["val_loss"] == pytest.approx(0.342)


@pytest.mark.asyncio
async def test_review_results_stores_insight_and_context_with_metadata():
    launched = await launch_experiment(
        name="PFN review",
        script="#!/bin/bash\npython train.py --epochs 5",
    )
    experiment_id = launched["experiment"]["id"]
    await monitor_run(identifier=experiment_id, kind="experiment")
    await monitor_run(identifier=experiment_id, kind="experiment")

    result = await review_results(
        experiment_id=experiment_id,
        insight_title="Keep PFN baseline",
        insight_content="Loss stayed below the current threshold.",
        context_key="next_step",
        context_value="Compare against LightGBM baseline.",
        created_by="worker-2",
    )

    assert result["stored_insight"]["id"]
    assert result["stored_context"]["key"] == "next_step"
    assert _store["insights"][0]["workflow_name"] == "review-results"
    assert _store["context"][0]["workflow_name"] == "review-results"
    assert _store["context"][0]["linked_experiment_id"] == experiment_id


@pytest.mark.asyncio
async def test_research_context_can_save_first_paper_and_context(monkeypatch: pytest.MonkeyPatch):
    async def fake_search(args: dict[str, object]) -> dict[str, object]:
        payload = {
            "total": 1,
            "papers": [
                {
                    "title": "Learning Curves with PFNs",
                    "authors": ["Alice Smith"],
                    "abstract": "Short abstract",
                    "full_abstract": "Full abstract",
                    "arxiv_id": "2401.12345",
                    "year": 2024,
                    "url": "https://example.com/paper",
                    "categories": ["cs.LG"],
                }
            ],
        }
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}

    monkeypatch.setattr("research_copilot.services.workflows.handle_search_papers", fake_search)

    result = await research_context(
        query="PFN learning curves",
        save_first=True,
        relevance_notes="Relevant to upcoming ablations.",
        context_key="literature_focus",
        context_value="Prioritize PFN extrapolation papers.",
    )

    assert result["papers_total"] == 1
    assert result["saved_paper"]["id"]
    assert _store["papers"][0]["workflow_name"] == "research-context"
    assert _store["context"][0]["workflow_name"] == "research-context"


@pytest.mark.asyncio
async def test_onboard_persists_contract_and_updates_context(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    payload = await onboard(
        goal="Find overfitting in the baseline",
        success_criteria="Validation gap under 2%",
        active_profile="overfit-hunter",
        autonomy_level="bounded",
        allowed_actions=["create experiments", "launch runs"],
        constraints=["single-user only"],
        stop_conditions=["ask when unsure"],
        notes="Start with regularization changes.",
        actor_type="human",
    )
    summary = await onboarding_summary()

    assert payload["workflow"] == "onboard"
    assert payload["contract"]["goal"] == "Find overfitting in the baseline"
    assert summary["configured"] is True
    assert summary["contract"]["active_profile"] == "overfit-hunter"
    assert any(ctx["key"] == "current_goal" for ctx in _store["context"])


@pytest.mark.asyncio
async def test_run_experiment_executes_local_command_and_persists_run_artifact(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    payload = await run_experiment(
        command='python -c "import json; print(json.dumps({\'train_loss\': 0.10, \'val_loss\': 0.13, \'test_loss\': 0.14}))"',
        name="local-run",
        hypothesis="Local runner works",
        actor_type="human",
        created_by="solo",
    )

    assert payload["workflow"] == "run-experiment"
    assert payload["run"]["status"] == "completed"
    assert payload["metrics"]["val_loss"] == pytest.approx(0.13)
    assert payload["experiment"]["status"] == "completed"


@pytest.mark.asyncio
async def test_overfitting_check_and_next_step_emit_review_artifacts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    launched = await run_experiment(
        command='python -c "import json; print(json.dumps({\'train_loss\': 0.05, \'val_loss\': 0.20, \'test_loss\': 0.22}))"',
        name="overfit-check",
        actor_type="human",
        created_by="solo",
    )
    experiment_id = launched["experiment"]["id"]

    overfit = await overfitting_check(experiment_id=experiment_id)
    next_payload = await next_step(experiment_id=experiment_id)

    assert overfit["workflow"] == "overfitting-check"
    assert "validation_gap" in overfit["review"]["score_gaps"]
    assert next_payload["workflow"] == "next-step"
    assert next_payload["review"]["suggestions"]
