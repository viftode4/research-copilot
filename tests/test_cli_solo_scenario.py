"""Seeded CLI end-to-end scenario tests for a solo operator flow."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner, Result

from research_copilot.main import cli
from research_copilot.mcp_servers.knowledge_base import _store
from research_copilot.mcp_servers.slurm import _mock_jobs

SEEDED_PAPER_RESULTS = {
    "total": 1,
    "papers": [
        {
            "title": "Learning Curves with PFNs",
            "authors": ["Alice Smith"],
            "abstract": "Short abstract",
            "full_abstract": "Full abstract with solo-operator notes.",
            "arxiv_id": "2401.12345",
            "year": 2024,
            "url": "https://example.com/paper",
            "categories": ["cs.LG", "stat.ML"],
        }
    ],
}


@pytest.fixture(autouse=True)
def clean_state() -> None:
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()


async def _fake_search(args: dict[str, object]) -> dict[str, object]:
    assert args["query"] == "PFN learning curves"
    return {"content": [{"type": "text", "text": json.dumps(SEEDED_PAPER_RESULTS)}]}


def _invoke_json(runner: CliRunner, args: list[str]) -> dict[str, object]:
    result: Result = runner.invoke(cli, [*args, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_seeded_solo_cli_flow_covers_research_launch_monitor_and_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("research_copilot.services.workflows.handle_search_papers", _fake_search)
    runner = CliRunner()

    empty_triage = _invoke_json(runner, ["workflow", "triage"])
    assert empty_triage["suggested_next_action"] == "launch-experiment"

    context_payload = _invoke_json(
        runner,
        [
            "workflow",
            "research-context",
            "PFN learning curves",
            "--save-first",
            "--relevance-notes",
            "Relevant to the next PFN ablation run.",
            "--context-key",
            "literature_focus",
            "--context-value",
            "Prioritize PFN extrapolation papers.",
        ],
    )
    assert context_payload["papers_total"] == 1
    assert context_payload["saved_paper"]["id"]
    assert context_payload["stored_context"]["key"] == "literature_focus"

    launch_payload = _invoke_json(
        runner,
        [
            "workflow",
            "launch-experiment",
            "--name",
            "Solo PFN run",
            "--script",
            "#!/bin/bash\npython train.py --dataset lcdb --seed 7",
            "--hypothesis",
            "PFNs should stabilize on the LCDB slice.",
            "--dataset",
            "LCDB",
            "--model-type",
            "PFN",
            "--tag",
            "solo",
            "--created-by",
            "solo-operator",
        ],
    )
    experiment_id = launch_payload["experiment"]["id"]
    job_id = launch_payload["job"]["job_id"]
    assert launch_payload["workflow"] == "launch-experiment"
    assert launch_payload["experiment"]["slurm_job_id"] == job_id
    assert launch_payload["job"]["submitted_by"] == "solo-operator"

    active_triage = _invoke_json(runner, ["workflow", "triage"])
    assert active_triage["suggested_next_action"] == "monitor-run"
    assert active_triage["snapshot"]["jobs"]["active"] == 1

    first_monitor = _invoke_json(
        runner,
        ["workflow", "monitor-run", experiment_id, "--kind", "experiment", "--lines", "10"],
    )
    second_monitor = _invoke_json(
        runner,
        ["workflow", "monitor-run", experiment_id, "--kind", "experiment", "--lines", "10"],
    )
    assert first_monitor["job"]["status"] == "RUNNING"
    assert second_monitor["job"]["status"] == "COMPLETED"
    assert second_monitor["experiment"]["status"] == "completed"
    assert second_monitor["metrics"]["val_loss"] == pytest.approx(0.342)
    assert second_monitor["metrics"]["val_accuracy"] == pytest.approx(0.891)

    review_payload = _invoke_json(
        runner,
        [
            "workflow",
            "review-results",
            experiment_id,
            "--insight-title",
            "Keep the PFN baseline",
            "--insight-content",
            "The seeded mock run finished with acceptable validation loss.",
            "--context-key",
            "next_step",
            "--context-value",
            "Compare against the LightGBM baseline.",
        ],
    )
    assert review_payload["stored_insight"]["id"]
    assert review_payload["stored_context"]["key"] == "next_step"
    assert {"test_nll", "val_accuracy", "val_loss"}.issubset(set(review_payload["result_keys"]))

    snapshot_payload = _invoke_json(runner, ["snapshot", "--limit", "5"])
    assert snapshot_payload["jobs"]["total"] == 1
    assert snapshot_payload["jobs"]["active"] == 0
    assert snapshot_payload["experiments"]["total"] == 1
    assert snapshot_payload["experiments"]["items"][0]["status"] == "completed"
    assert snapshot_payload["knowledge"]["papers_total"] == 1
    assert snapshot_payload["knowledge"]["insights_total"] == 1
    assert snapshot_payload["knowledge"]["context_total"] == 2

    job_payload = _invoke_json(runner, ["jobs", "get", job_id])
    experiment_payload = _invoke_json(runner, ["experiments", "get", experiment_id])
    papers_payload = _invoke_json(runner, ["papers", "list", "--limit", "5"])
    insights_payload = _invoke_json(runner, ["insights", "list", "--limit", "5"])
    context_list_payload = _invoke_json(runner, ["context", "list"])

    assert job_payload["workflow_name"] == "launch-experiment"
    assert experiment_payload["workflow_name"] == "monitor-run"
    assert experiment_payload["results"]["val_loss"] == pytest.approx(0.342)
    assert papers_payload["total"] == 1
    assert insights_payload["total"] == 1
    assert context_list_payload["total"] == 2

