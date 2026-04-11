"""CLI tests for the terminal-first workflow dashboard and agent-safe surfaces."""

from __future__ import annotations

import json

from click.testing import CliRunner

from research_copilot.main import cli
from research_copilot.mcp_servers.knowledge_base import _store
from research_copilot.mcp_servers.slurm import MockJob, _mock_jobs


def setup_function() -> None:
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()


def test_default_cli_invocation_renders_tui_snapshot():
    runner = CliRunner()

    result = runner.invoke(cli, [])

    assert result.exit_code == 0
    assert "Research Copilot" in result.output
    assert "Terminal workflow dashboard" in result.output
    assert "No jobs yet" in result.output



def test_tui_command_renders_seeded_workflow_views():
    _mock_jobs["10001"] = MockJob(
        job_id="10001",
        name="pfn-train",
        status="RUNNING",
        script="#!/bin/bash\npython train.py",
        partition="gpu",
        gpus=2,
        time_limit="08:00:00",
        submitted_at="2026-04-11T13:00:00+00:00",
        started_at="2026-04-11T13:05:00+00:00",
        output="epoch=1\nval_loss=0.42",
    )
    _store["experiments"].append(
        {
            "id": "exp-1",
            "name": "PFN mu-weighting",
            "status": "running",
            "hypothesis": "Mu-weighting improves convergence.",
            "description": "",
            "dataset": "LCDB",
            "model_type": "PFN",
            "tags": ["pfn", "mu-weighting"],
            "results": {"val_loss": 0.42},
            "wandb_run_id": "wandb-1",
            "created_at": "2026-04-11T12:00:00+00:00",
            "updated_at": "2026-04-11T13:10:00+00:00",
        }
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["tui"])

    assert result.exit_code == 0
    assert "pfn-train" in result.output
    assert "PFN mu-weighting" in result.output
    assert "Active jobs" in result.output



def test_status_and_top_level_help_reflect_terminal_first_surface():
    runner = CliRunner()

    status_result = runner.invoke(cli, ["status"])
    help_result = runner.invoke(cli, ["--help"])

    assert status_result.exit_code == 0
    assert "research-copilot tui" in status_result.output
    assert "Workflow Snapshot" in status_result.output
    assert help_result.exit_code == 0
    assert "serve" not in help_result.output
    assert "workflow" in help_result.output
    assert "jobs" in help_result.output
    assert "experiments" in help_result.output
    assert "context" in help_result.output
    assert "insights" in help_result.output
    assert "papers" in help_result.output
    assert "snapshot" in help_result.output



def test_workflow_help_lists_named_commands():
    runner = CliRunner()

    result = runner.invoke(cli, ["workflow", "--help"])

    assert result.exit_code == 0
    assert "triage" in result.output
    assert "launch-experiment" in result.output
    assert "monitor-run" in result.output
    assert "review-results" in result.output
    assert "research-context" in result.output



def test_snapshot_and_job_commands_emit_json():
    runner = CliRunner()

    submit_result = runner.invoke(
        cli,
        [
            "jobs",
            "submit",
            "--name",
            "CLI job",
            "--script",
            "#!/bin/bash\necho hi",
            "--submitted-by",
            "worker-2",
            "--json",
        ],
    )
    submit_payload = json.loads(submit_result.output)

    list_result = runner.invoke(cli, ["jobs", "list", "--json"])
    get_result = runner.invoke(cli, ["jobs", "get", submit_payload["job_id"], "--json"])
    logs_result = runner.invoke(cli, ["jobs", "logs", submit_payload["job_id"], "--json"])
    snapshot_result = runner.invoke(cli, ["snapshot", "--json"])
    cancel_result = runner.invoke(cli, ["jobs", "cancel", submit_payload["job_id"], "--json"])

    assert submit_result.exit_code == 0
    assert submit_payload["job_id"] in _mock_jobs
    assert list_result.exit_code == 0
    assert json.loads(list_result.output)["total"] == 1
    assert get_result.exit_code == 0
    assert json.loads(get_result.output)["name"] == "CLI job"
    assert logs_result.exit_code == 0
    assert json.loads(logs_result.output)["job_id"] == submit_payload["job_id"]
    assert snapshot_result.exit_code == 0
    assert json.loads(snapshot_result.output)["jobs"]["total"] == 1
    assert cancel_result.exit_code == 0
    assert json.loads(cancel_result.output)["status"] == "CANCELLED"



def test_experiments_context_and_insights_commands_emit_json():
    runner = CliRunner()

    create_result = runner.invoke(
        cli,
        [
            "experiments",
            "create",
            "--name",
            "CLI experiment",
            "--hypothesis",
            "Works from CLI",
            "--dataset",
            "LCDB",
            "--tag",
            "smoke",
            "--json",
        ],
    )
    create_payload = json.loads(create_result.output)
    experiment_id = create_payload["id"]

    update_result = runner.invoke(
        cli,
        [
            "experiments",
            "update",
            experiment_id,
            "--status",
            "running",
            "--results",
            '{"val_loss": 0.42}',
            "--json",
        ],
    )
    get_result = runner.invoke(cli, ["experiments", "get", experiment_id, "--json"])
    list_result = runner.invoke(cli, ["experiments", "list", "--json"])
    context_set = runner.invoke(
        cli,
        ["context", "set", "current_goal", "--value", "Ship CLI surface", "--json"],
    )
    context_get = runner.invoke(cli, ["context", "get", "current_goal", "--json"])
    context_list = runner.invoke(cli, ["context", "list", "--json"])
    insight_add = runner.invoke(
        cli,
        [
            "insights",
            "add",
            "--title",
            "CLI insight",
            "--content",
            "Useful note",
            "--experiment-id",
            experiment_id,
            "--json",
        ],
    )
    insight_list = runner.invoke(cli, ["insights", "list", "--json"])

    assert create_result.exit_code == 0
    assert update_result.exit_code == 0
    assert json.loads(update_result.output)["message"] == "Updated"
    assert get_result.exit_code == 0
    assert json.loads(get_result.output)["status"] == "running"
    assert list_result.exit_code == 0
    assert json.loads(list_result.output)["total"] == 1
    assert context_set.exit_code == 0
    assert json.loads(context_get.output)["value"] == "Ship CLI surface"
    assert json.loads(context_list.output)["total"] == 1
    assert insight_add.exit_code == 0
    assert json.loads(insight_list.output)["total"] == 1



def test_paper_commands_emit_json(monkeypatch):
    runner = CliRunner()

    async def fake_search(*, query: str, max_results: int = 10, sources: str = "both"):
        return {
            "total": 1,
            "papers": [
                {
                    "title": "PFN Paper",
                    "authors": ["Alice"],
                    "source": sources,
                }
            ],
        }

    monkeypatch.setattr("research_copilot.main.search_papers_service", fake_search)

    save_result = runner.invoke(
        cli,
        [
            "papers",
            "save",
            "--title",
            "Saved paper",
            "--author",
            "Alice",
            "--tag",
            "pfn",
            "--json",
        ],
    )
    list_result = runner.invoke(cli, ["papers", "list", "--json"])
    search_result = runner.invoke(cli, ["papers", "search", "pfn", "--json"])

    assert save_result.exit_code == 0
    assert json.loads(save_result.output)["id"]
    assert list_result.exit_code == 0
    assert json.loads(list_result.output)["total"] == 1
    assert search_result.exit_code == 0
    assert json.loads(search_result.output)["total"] == 1



def test_launch_experiment_command_emits_json_and_updates_state():
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "workflow",
            "launch-experiment",
            "--name",
            "CLI launch",
            "--script",
            "#!/bin/bash\npython train.py",
            "--hypothesis",
            "CLI flow works",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["workflow"] == "launch-experiment"
    assert payload["experiment"]["name"] == "CLI launch"
    assert payload["job"]["job_id"] in _mock_jobs
