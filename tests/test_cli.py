"""CLI tests for the terminal-first workflow dashboard and agent-safe surfaces."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

import research_copilot.main as main_module
from research_copilot.main import cli
from research_copilot.mcp_servers.knowledge_base import _store
from research_copilot.mcp_servers.slurm import MockJob, _mock_jobs
from research_copilot.research_state import (
    load_codex_active_session,
    load_codex_turn_summary,
    list_autonomous_runtime_events,
    load_autonomous_runtime,
    load_autonomous_runtime_history,
    save_autonomous_runtime,
)


def _workflow_command(name: str):
    return cli.commands["workflow"].commands.get(name)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch) -> None:
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()
    for variable in ("RC_RESEARCH_ROOT", "RC_WORKING_DIR", "RC_GLOBAL_HOME"):
        monkeypatch.delenv(variable, raising=False)
    yield
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()


def test_default_cli_invocation_renders_tui_snapshot():
    runner = CliRunner()
    with runner.isolated_filesystem():
        init_result = runner.invoke(cli, ["init"])
        assert init_result.exit_code == 0, init_result.output

        result = runner.invoke(cli, [])

    assert result.exit_code == 0
    assert "Research Copilot" in result.output
    assert "schema 1.0.0" in result.output
    assert "No runs yet" in result.output


def test_default_cli_invocation_in_uninitialized_workspace_shows_bootstrap_not_tui(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    def fail_launch_tui() -> None:
        raise AssertionError("launch_tui should not run before workspace init")

    monkeypatch.setattr(main_module, "launch_tui", fail_launch_tui)

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert "Research Copilot bootstrap" in result.output
    assert "research-copilot init" in result.output


def test_init_command_creates_canonical_state_and_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first = runner.invoke(cli, ["init"])
    second = runner.invoke(cli, ["init"])

    research_root = tmp_path / ".research-copilot"

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert research_root.exists()
    assert (research_root / "workspace.json").is_file()
    assert (research_root / "onboarding").is_dir()
    assert (research_root / "experiments").is_dir()
    assert (research_root / "runs").is_dir()


def test_default_cli_invocation_opens_tui_in_initialized_workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    launch_calls: list[Path] = []

    runner.invoke(cli, ["init"])

    def fake_launch_tui() -> None:
        launch_calls.append(Path.cwd())

    monkeypatch.setattr(main_module, "launch_tui", fake_launch_tui)

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert launch_calls == [tmp_path]


def test_workspace_option_can_reopen_last_initialized_workspace(monkeypatch, tmp_path):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    runner = CliRunner()
    launch_calls: list[Path] = []

    monkeypatch.chdir(workspace_a)
    init_result = runner.invoke(cli, ["init"])
    assert init_result.exit_code == 0, init_result.output

    def fake_launch_tui() -> None:
        launch_calls.append(Path(os.environ["RC_WORKING_DIR"]))

    monkeypatch.setattr(main_module, "launch_tui", fake_launch_tui)
    monkeypatch.chdir(workspace_b)

    result = runner.invoke(cli, ["--workspace", str(workspace_a)])

    assert result.exit_code == 0, result.output
    assert launch_calls == [workspace_a.resolve()]


def test_read_only_json_commands_can_use_legacy_workspace_without_forcing_migration(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "legacy-workspace"
    legacy_root = workspace / ".omx" / "research"
    (legacy_root / "onboarding").mkdir(parents=True)
    (legacy_root / "workspace.json").write_text(
        json.dumps({"schema_version": "1.0", "workspace_root": str(workspace)}),
        encoding="utf-8",
    )
    (legacy_root / "onboarding" / "current.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "goal": "Read legacy workspace",
                "active_profile": "goal-chaser",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    result = runner.invoke(cli, ["workflow", "onboard-show", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["configured"] is True
    assert payload["data"]["contract"]["goal"] == "Read legacy workspace"
    assert (workspace / ".research-copilot").exists() is False


def test_mutating_json_commands_on_legacy_only_workspace_require_migration(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "legacy-workspace"
    legacy_root = workspace / ".omx" / "research"
    (legacy_root / "onboarding").mkdir(parents=True)
    (legacy_root / "workspace.json").write_text(
        json.dumps({"schema_version": "1.0", "workspace_root": str(workspace)}),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "workflow",
            "onboard",
            "--goal",
            "Mutating legacy workspace",
            "--success-criteria",
            "Require migration first",
            "--active-profile",
            "goal-chaser",
            "--autonomy-level",
            "bounded",
            "--allowed-action",
            "run local experiments",
            "--constraint",
            "single-user only",
            "--stop-condition",
            "stop on repeated failure",
            "--json",
        ],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "MIGRATION_REQUIRED"
    assert payload["workspace"] == str(workspace)


def test_migrate_command_promotes_legacy_workspace_to_canonical_root(monkeypatch, tmp_path):
    workspace = tmp_path / "legacy-workspace"
    legacy_root = workspace / ".omx" / "research"
    (legacy_root / "onboarding").mkdir(parents=True)
    (legacy_root / "workspace.json").write_text(
        json.dumps({"schema_version": "1.0", "workspace_root": str(workspace)}),
        encoding="utf-8",
    )
    (legacy_root / "onboarding" / "current.json").write_text(
        json.dumps({"goal": "Migrate me", "active_profile": "goal-chaser"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    result = runner.invoke(cli, ["migrate", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["workspace"] == str(workspace)
    assert (workspace / ".research-copilot" / "workspace.json").is_file()
    assert json.loads((workspace / ".research-copilot" / "onboarding" / "current.json").read_text())["goal"] == "Migrate me"


def test_workspace_option_wins_over_current_directory_discovery(monkeypatch, tmp_path):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    subdir = workspace_b / "nested" / "child"
    workspace_a.mkdir()
    subdir.mkdir(parents=True)
    (workspace_a / ".research-copilot" / "onboarding").mkdir(parents=True)
    (workspace_a / ".research-copilot" / "workspace.json").write_text(
        json.dumps({"schema_version": "1.0", "workspace_root": str(workspace_a)}),
        encoding="utf-8",
    )
    (workspace_a / ".research-copilot" / "onboarding" / "current.json").write_text(
        json.dumps({"goal": "Workspace A", "active_profile": "goal-chaser"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(subdir)
    runner = CliRunner()

    result = runner.invoke(cli, ["--workspace", str(workspace_a), "workflow", "onboard-show", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["contract"]["goal"] == "Workspace A"


def test_json_mode_keeps_stdout_machine_parseable_even_when_warnings_fire(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    async def warn_and_return(*_args, **_kwargs):
        import warnings

        warnings.warn("noise that must not pollute stdout", UserWarning)
        return {"configured": False, "message": "No onboarding contract saved yet.", "contract": {}}

    monkeypatch.setattr(main_module, "onboarding_summary_workflow", warn_and_return)

    result = runner.invoke(cli, ["workflow", "onboard-show", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["configured"] is False



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
    assert "Active runs" in result.output
    assert "Recent experiments" in result.output



def test_status_and_top_level_help_reflect_terminal_first_surface(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    status_result = runner.invoke(cli, ["status"])
    help_result = runner.invoke(cli, ["--help"])

    assert status_result.exit_code == 0
    assert "research-copilot tui" in status_result.output
    assert "Workflow Snapshot" in status_result.output
    assert "Onboarding:" in status_result.output
    assert "research-copilot init" in status_result.output
    assert help_result.exit_code == 0
    assert "workflow" in help_result.output
    assert "jobs" in help_result.output
    assert "experiments" in help_result.output
    assert "context" in help_result.output
    assert "insights" in help_result.output
    assert "mcp" in help_result.output
    assert "papers" in help_result.output
    assert "snapshot" in help_result.output
    assert "init" in help_result.output
    assert "cli-scenario.md" in help_result.output


def test_status_surfaces_saved_onboarding_contract(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    onboard_result = runner.invoke(
        cli,
        [
            "workflow",
            "onboard",
            "--goal",
            "Probe random-data baseline behavior",
            "--success-criteria",
            "Persist one reviewed run",
            "--active-profile",
            "goal-chaser",
            "--autonomy-level",
            "bounded",
            "--allowed-action",
            "launch runs",
            "--constraint",
            "single-user only",
            "--stop-condition",
            "stop after one reviewed run",
            "--json",
        ],
    )

    assert onboard_result.exit_code == 0, onboard_result.output

    status_result = runner.invoke(cli, ["status"])

    assert status_result.exit_code == 0
    assert "State:            Configured" in status_result.output
    assert "Probe random-data baseline behavior" in status_result.output
    assert "goal-chaser" in status_result.output
    assert "Recommended next action: research-copilot workflow triage" in status_result.output
    assert "read-only TUI" in status_result.output



def test_workflow_help_lists_named_commands():
    runner = CliRunner()

    result = runner.invoke(cli, ["workflow", "--help"])

    assert result.exit_code == 0
    assert "Primary action surface for the solo research loop." in result.output
    assert "triage" in result.output
    assert "launch-experiment" in result.output
    assert "monitor-run" in result.output
    assert "review-results" in result.output
    assert "research-context" in result.output
    assert "onboard" in result.output
    assert "onboard-show" in result.output
    assert "run-experiment" in result.output
    assert "overfitting-check" in result.output
    assert "next-step" in result.output
    assert "Start with: research-copilot init" in result.output
    assert "Solo proof:" in result.output


def test_workflow_help_lists_autonomous_lifecycle_commands_when_runtime_lane_is_available():
    if _workflow_command("autonomous-status") is None:
        pytest.skip("Lane 2 CLI lifecycle commands are not available in this checkout yet.")

    runner = CliRunner()
    result = runner.invoke(cli, ["workflow", "--help"])

    assert result.exit_code == 0
    assert "autonomous-run" in result.output
    assert "autonomous-status" in result.output
    assert "autonomous-stop" in result.output
    assert "autonomous-resume" in result.output


def test_workflow_triage_human_output_uses_recommended_next_action(monkeypatch):
    async def fake_triage_workflow(*, max_items: int = 5):
        return {
            "workflow": "triage",
            "snapshot": {"jobs": {"active": 0}, "experiments": {"total": 1}, "knowledge": {}},
            "onboarding": None,
            "blockers": ["No active blockers detected."],
            "suggested_next_action": "review-results",
        }

    monkeypatch.setattr("research_copilot.main.triage_workflow", fake_triage_workflow)
    runner = CliRunner()

    result = runner.invoke(cli, ["workflow", "triage"])

    assert result.exit_code == 0
    assert "Recommended next action: review-results" in result.output
    assert "Blockers: No active blockers detected." in result.output


def test_autonomous_status_json_stays_read_only_when_runtime_lane_is_available(monkeypatch, tmp_path):
    if _workflow_command("autonomous-status") is None:
        pytest.skip("Lane 2 autonomous-status command is not available in this checkout yet.")

    workspace = tmp_path / "legacy-workspace"
    legacy_root = workspace / ".omx" / "research"
    (legacy_root / "onboarding").mkdir(parents=True)
    (legacy_root / "workspace.json").write_text(
        json.dumps({"schema_version": "1.0", "workspace_root": str(workspace)}),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    result = runner.invoke(cli, ["workflow", "autonomous-status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["workspace"] == str(workspace)
    assert (workspace / ".research-copilot").exists() is False


def test_autonomous_status_json_reports_stale_without_mutating_runtime(monkeypatch, tmp_path):
    if _workflow_command("autonomous-status") is None:
        pytest.skip("Lane 2 autonomous-status command is not available in this checkout yet.")

    monkeypatch.chdir(tmp_path)
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
    runner = CliRunner()

    result = runner.invoke(cli, ["workflow", "autonomous-status", "--run-id", "run-1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["status"] == "stale"
    assert json.dumps(load_autonomous_runtime(), sort_keys=True) == before
    assert load_autonomous_runtime_history("run-1") == {}
    assert list_autonomous_runtime_events("run-1") == []


def test_autonomous_worker_argv_uses_auth_file_not_owner_token() -> None:
    argv = main_module._autonomous_worker_argv("run-1", "C:\\temp\\worker-auth.json")

    assert "--auth-file" in argv
    assert "C:\\temp\\worker-auth.json" in argv
    assert "--owner-token" not in argv


def test_autonomous_stop_json_requires_owner_token(monkeypatch, tmp_path):
    if _workflow_command("autonomous-stop") is None:
        pytest.skip("Lane 2 autonomous-stop command is not available in this checkout yet.")

    monkeypatch.chdir(tmp_path)
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
            "owner_token": "secret-token",
        }
    )
    runner = CliRunner()

    result = runner.invoke(cli, ["workflow", "autonomous-stop", "--run-id", "run-1", "--json"])

    assert result.exit_code != 0
    assert "owner_token is required" in result.output


def test_runtime_help_lists_codex_runtime_commands():
    runner = CliRunner()

    result = runner.invoke(cli, ["runtime", "--help"])

    assert result.exit_code == 0
    assert "Advanced runtime supervision for Codex-managed sessions." in result.output
    assert "codex-attach" in result.output
    assert "codex-status" in result.output
    assert "codex-report" in result.output
    assert "codex-nudge" in result.output
    assert "codex-drain-nudges" in result.output
    assert "codex-apply-nudges" in result.output
    assert "codex-run" in result.output
    assert "codex-stop" in result.output
    assert "codex-supervisor-resume" in result.output


def test_runtime_codex_commands_attach_report_and_drain(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("research_copilot.services.codex_runtime._tmux_pane_exists", lambda pane_id: pane_id == "%71")
    monkeypatch.setattr(
        "research_copilot.services.codex_runtime._tmux_pane_metadata",
        lambda pane_id: {
            "pane_id": "%71",
            "session_name": "codex-1",
            "window_name": "brain",
            "workspace": str(tmp_path),
        },
    )
    runner = CliRunner()

    attach_result = runner.invoke(
        cli,
        [
            "runtime",
            "codex-attach",
            "--session-id",
            "codex-1",
            "--pane-id",
            "%71",
            "--window-name",
            "brain",
            "--json",
        ],
    )
    report_result = runner.invoke(
        cli,
        [
            "runtime",
            "codex-report",
            "--session-id",
            "codex-1",
            "--turn-number",
            "1",
            "--summary",
            "Reviewed the latest results.",
            "--action",
            "review-results",
            "--experiment-id",
            "exp-1",
            "--json",
        ],
    )
    nudge_result = runner.invoke(
        cli,
        [
            "runtime",
            "codex-stop-after-turn",
            "--session-id",
            "codex-1",
            "--message",
            "Stop after the current turn.",
            "--json",
        ],
    )
    drain_result = runner.invoke(
        cli,
        [
            "runtime",
            "codex-drain-nudges",
            "--session-id",
            "codex-1",
            "--json",
        ],
    )
    status_result = runner.invoke(
        cli,
        [
            "runtime",
            "codex-status",
            "--session-id",
            "codex-1",
            "--include-nudges",
            "--json",
        ],
    )

    assert attach_result.exit_code == 0, attach_result.output
    assert report_result.exit_code == 0, report_result.output
    assert nudge_result.exit_code == 0, nudge_result.output
    assert drain_result.exit_code == 0, drain_result.output
    assert status_result.exit_code == 0, status_result.output
    assert json.loads(attach_result.output)["data"]["session_id"] == "codex-1"
    assert json.loads(report_result.output)["data"]["accepted"] is True
    assert json.loads(nudge_result.output)["data"]["nudge"]["kind"] == "stop_after_turn"
    assert len(json.loads(drain_result.output)["data"]["drained"]) == 1
    assert json.loads(status_result.output)["data"]["pending_nudge_count"] == 0
    assert load_codex_active_session()["last_experiment_id"] == "exp-1"
    assert load_codex_turn_summary("codex-1", 1) == "Reviewed the latest results."


def test_runtime_codex_apply_nudges_command_routes_to_live_pane(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("research_copilot.services.codex_runtime._tmux_pane_exists", lambda pane_id: pane_id == "%71")
    monkeypatch.setattr(
        "research_copilot.services.codex_runtime._tmux_pane_metadata",
        lambda pane_id: {
            "pane_id": "%71",
            "session_name": "codex-1",
            "window_name": "brain",
            "workspace": str(tmp_path),
        },
    )
    runner = CliRunner()

    runner.invoke(
        cli,
        [
            "runtime",
            "codex-attach",
            "--session-id",
            "codex-1",
            "--pane-id",
            "%71",
            "--window-name",
            "brain",
            "--json",
        ],
    )
    runner.invoke(
        cli,
        [
            "runtime",
            "codex-nudge",
            "--session-id",
            "codex-1",
            "--kind",
            "request_summary",
            "--message",
            "Need a tighter recap.",
            "--json",
        ],
    )

    sent: list[tuple[str, ...]] = []

    def fake_run_tmux_command(*args: str):
        sent.append(args)
        return None

    monkeypatch.setattr("research_copilot.services.codex_runtime._run_tmux_command", fake_run_tmux_command)

    apply_result = runner.invoke(
        cli,
        [
            "runtime",
            "codex-apply-nudges",
            "--session-id",
            "codex-1",
            "--json",
        ],
    )

    assert apply_result.exit_code == 0, apply_result.output
    assert json.loads(apply_result.output)["data"]["pending_nudge_count"] == 0
    assert any(args[:3] == ("send-keys", "-t", "%71") for args in sent)


def test_runtime_codex_run_starts_detached_supervisor(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("research_copilot.services.codex_runtime._tmux_pane_exists", lambda pane_id: pane_id == "%71")
    monkeypatch.setattr(
        "research_copilot.services.codex_runtime._tmux_pane_metadata",
        lambda pane_id: {
            "pane_id": "%71",
            "session_name": "codex-1",
            "window_name": "brain",
            "workspace": str(tmp_path),
        },
    )
    launched: list[dict[str, object]] = []

    monkeypatch.setattr(
        main_module,
        "_launch_codex_worker",
        lambda payload: launched.append(payload),
    )
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "runtime",
            "codex-run",
            "--session-id",
            "codex-1",
            "--pane-id",
            "%71",
            "--workspace-path",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["data"]
    assert payload["owner_token"]
    assert launched and launched[0]["owner_token"] == payload["owner_token"]



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
    submit_payload = json.loads(submit_result.output)["data"]

    list_result = runner.invoke(cli, ["jobs", "list", "--json"])
    get_result = runner.invoke(cli, ["jobs", "get", submit_payload["job_id"], "--json"])
    logs_result = runner.invoke(cli, ["jobs", "logs", submit_payload["job_id"], "--json"])
    snapshot_result = runner.invoke(cli, ["snapshot", "--json"])
    cancel_result = runner.invoke(cli, ["jobs", "cancel", submit_payload["job_id"], "--json"])

    assert submit_result.exit_code == 0
    assert submit_payload["job_id"] in _mock_jobs
    assert list_result.exit_code == 0
    assert json.loads(list_result.output)["data"]["total"] == 1
    assert get_result.exit_code == 0
    assert json.loads(get_result.output)["data"]["name"] == "CLI job"
    assert logs_result.exit_code == 0
    assert json.loads(logs_result.output)["data"]["job_id"] == submit_payload["job_id"]
    assert snapshot_result.exit_code == 0
    assert json.loads(snapshot_result.output)["data"]["jobs"]["total"] == 1
    assert cancel_result.exit_code == 0
    assert json.loads(cancel_result.output)["data"]["status"] == "CANCELLED"



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
    create_payload = json.loads(create_result.output)["data"]
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
    assert json.loads(update_result.output)["data"]["message"] == "Updated"
    assert get_result.exit_code == 0
    assert json.loads(get_result.output)["data"]["status"] == "running"
    assert list_result.exit_code == 0
    assert json.loads(list_result.output)["data"]["total"] == 1
    assert context_set.exit_code == 0
    assert json.loads(context_get.output)["data"]["value"] == "Ship CLI surface"
    assert json.loads(context_list.output)["data"]["total"] == 1
    assert insight_add.exit_code == 0
    assert json.loads(insight_list.output)["data"]["total"] == 1



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
    assert json.loads(save_result.output)["data"]["id"]
    assert list_result.exit_code == 0
    assert json.loads(list_result.output)["data"]["total"] == 1
    assert search_result.exit_code == 0
    assert json.loads(search_result.output)["data"]["total"] == 1



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
    assert payload["data"]["workflow"] == "launch-experiment"
    assert payload["data"]["experiment"]["name"] == "CLI launch"
    assert payload["data"]["job"]["job_id"] in _mock_jobs


def test_onboard_workflow_can_prompt_and_show_saved_contract(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["workflow", "onboard"],
        input=(
            "Test whether random.Random() shows simple patterns\n"
            "Store one completed experiment with a written review\n"
            "overfit-hunter\n"
            "bounded\n"
            "create experiments, launch runs, review results\n"
            "single-user only, local machine only\n"
            "stop after repeated failures, ask when unsure\n"
            "Use this as the onboarding baseline.\n"
        ),
    )

    assert result.exit_code == 0, result.output
    assert "Onboarding saved" in result.output

    show_result = runner.invoke(cli, ["workflow", "onboard-show", "--json"])
    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.output)
    assert show_payload["data"]["configured"] is True
    assert show_payload["data"]["contract"]["goal"] == "Test whether random.Random() shows simple patterns"
    assert show_payload["data"]["contract"]["active_profile"] == "overfit-hunter"


def test_onboard_workflow_json_mode_requires_explicit_fields(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["workflow", "onboard", "--json"])

    assert result.exit_code != 0
    assert "JSON mode requires explicit values" in result.output


@pytest.mark.parametrize(
    ("argv", "expected_key"),
    [
        (["workflow", "triage", "--json"], "workflow"),
        (
            [
                "workflow",
                "onboard",
                "--goal",
                "Probe random baseline behavior",
                "--success-criteria",
                "Persist one reviewed run",
                "--active-profile",
                "goal-chaser",
                "--autonomy-level",
                "bounded",
                "--allowed-action",
                "launch runs",
                "--constraint",
                "single-user only",
                "--stop-condition",
                "stop after one reviewed run",
                "--json",
            ],
            "workflow",
        ),
        (["workflow", "onboard-show", "--json"], "configured"),
    ],
)
def test_noninteractive_commands_never_open_tui(monkeypatch, tmp_path, argv, expected_key):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    def fail_launch_tui() -> None:
        raise AssertionError("launch_tui should not run for noninteractive commands")

    monkeypatch.setattr(main_module, "launch_tui", fail_launch_tui)

    result = runner.invoke(cli, argv)

    assert result.exit_code == 0, result.output
    assert expected_key in json.loads(result.output)["data"]


def test_run_experiment_and_reasoning_commands_emit_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    run_result = runner.invoke(
        cli,
        [
            "workflow",
            "run-experiment",
            "--name",
            "Local run",
            "--command",
            "python -c \"import json; print(json.dumps({'train_loss': 0.1, 'val_loss': 0.14, 'test_loss': 0.16}))\"",
            "--json",
        ],
    )
    assert run_result.exit_code == 0, run_result.output
    run_payload = json.loads(run_result.output)["data"]
    experiment_id = run_payload["experiment"]["id"]
    assert run_payload["run"]["status"] == "completed"

    overfit_result = runner.invoke(
        cli,
        ["workflow", "overfitting-check", experiment_id, "--json"],
    )
    next_step_result = runner.invoke(
        cli,
        ["workflow", "next-step", experiment_id, "--json"],
    )

    assert overfit_result.exit_code == 0
    assert next_step_result.exit_code == 0
    assert json.loads(overfit_result.output)["data"]["review"]["score_gaps"]["validation_gap"] == pytest.approx(0.04)
    assert json.loads(next_step_result.output)["data"]["review"]["suggestions"]


def test_ultrawork_run_can_execute_active_onboarding_profile(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    onboard_result = runner.invoke(
        cli,
        [
            "workflow",
            "onboard",
            "--goal",
            "Diagnose overfitting",
            "--success-criteria",
            "Persist one diagnosis and next step",
            "--active-profile",
            "overfit-hunter",
            "--autonomy-level",
            "bounded",
            "--allowed-action",
            "launch runs",
            "--allowed-action",
            "review results",
            "--constraint",
            "single-user only",
            "--stop-condition",
            "stop when diagnosis exists",
            "--json",
        ],
    )
    assert onboard_result.exit_code == 0, onboard_result.output

    run_result = runner.invoke(
        cli,
        [
            "ultrawork",
            "run",
            "active",
            "--execute",
            "--name",
            "auto-run",
            "--command",
            "python -c \"import json; print(json.dumps({'train_loss': 0.1, 'val_loss': 0.18, 'test_loss': 0.2}))\"",
            "--json",
        ],
    )

    assert run_result.exit_code == 0, run_result.output
    payload = json.loads(run_result.output)["data"]
    assert payload["status"] == "completed"
    assert payload["profile"]["name"] == "overfit-hunter"
    assert payload["steps"][1]["step"] == "overfitting-check"
