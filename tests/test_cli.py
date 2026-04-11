"""CLI tests for the terminal-first workflow dashboard."""

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


def test_status_and_help_reflect_terminal_first_surface():
    runner = CliRunner()

    status_result = runner.invoke(cli, ["status"])
    help_result = runner.invoke(cli, ["--help"])

    assert status_result.exit_code == 0
    assert "research-copilot tui" in status_result.output
    assert "Workflow Snapshot" in status_result.output
    assert help_result.exit_code == 0
    assert "serve" not in help_result.output
    assert "tui" in help_result.output
    assert "ultrawork" in help_result.output


def test_ultrawork_profile_list_json_emits_registry():
    runner = CliRunner()

    result = runner.invoke(cli, ["ultrawork", "profile", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [profile["name"] for profile in payload["profiles"]] == [
        "ops-triage",
        "experiment-launch",
        "run-review",
        "literature-context",
        "incident-recovery",
    ]


def test_ultrawork_run_json_emits_selected_contract():
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["ultrawork", "run", "incident-recovery", "--goal", "restore failed job", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["profile"]["name"] == "incident-recovery"
    assert payload["goal"] == "restore failed job"
    assert payload["lane_count"] == 3
