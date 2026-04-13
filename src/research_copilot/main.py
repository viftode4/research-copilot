"""CLI entry point for the Research Copilot."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import warnings
from dataclasses import asdict
from typing import Any
from uuid import uuid4

import click

try:
    from requests import RequestsDependencyWarning
except Exception:  # pragma: no cover - optional compatibility guard
    RequestsDependencyWarning = Warning

from research_copilot.config import load_config
from research_copilot.integrations.mcp.install import (
    render_agents_snippet,
    render_claude_config,
    render_codex_config,
)
from research_copilot.research_state import (
    get_last_workspace,
    get_research_root,
    get_workspace_dir,
    get_workspace_mode,
    initialize_workspace,
    is_legacy_workspace,
    is_workspace_initialized,
    load_onboarding_contract,
    migrate_workspace,
    remember_workspace,
    resolve_workspace,
    utc_now_iso,
)
from research_copilot.services.research_ops import (
    add_insight as add_insight_service,
    cancel_job as cancel_job_service,
    create_experiment as create_experiment_service,
    get_context as get_context_service,
    get_experiment as get_experiment_service,
    get_job as get_job_service,
    get_job_logs as get_job_logs_service,
    get_snapshot as get_snapshot_service,
    list_context as list_context_service,
    list_experiments as list_experiments_service,
    list_insights as list_insights_service,
    list_jobs as list_jobs_service,
    list_papers as list_papers_service,
    save_paper as save_paper_service,
    search_papers as search_papers_service,
    set_context as set_context_service,
    submit_job as submit_job_service,
    update_experiment as update_experiment_service,
)
from research_copilot.services.codex_runtime import (
    attach_codex_session,
    codex_runtime_status,
    drain_codex_nudges,
    enqueue_codex_nudge,
    ingest_codex_turn_report,
)
from research_copilot.services.ultrawork import (
    build_ultrawork_run_plan,
    execute_ultrawork_profile,
    list_ultrawork_profiles,
)
from research_copilot.services.workflows import (
    launch_experiment as launch_experiment_workflow,
    monitor_run as monitor_run_workflow,
    onboard as onboard_workflow,
    onboarding_summary as onboarding_summary_workflow,
    overfitting_check as overfitting_check_workflow,
    research_context as research_context_workflow,
    review_results as review_results_workflow,
    run_experiment as run_experiment_workflow,
    next_step as next_step_workflow,
    triage as triage_workflow,
)
from research_copilot.tui import launch_tui
from research_copilot.tui.adapters import build_dashboard_snapshot

warnings.filterwarnings("ignore", category=RequestsDependencyWarning)


CLI_EPILOG = """
Start with: research-copilot init
Solo proof: docs/seeded-solo-cli-scenario.md
"""

WORKFLOW_EPILOG = """
Start with: research-copilot init
Then: research-copilot workflow triage --json
Solo proof: docs/seeded-solo-cli-scenario.md
"""

MCP_EPILOG = """
Print Codex setup: research-copilot mcp print-codex-config
Print Claude config: research-copilot mcp print-claude-config
Print AGENTS snippet: research-copilot mcp print-agents-snippet
"""

JSON_SCHEMA_VERSION = "1.0"
AUTONOMOUS_RUNTIME_MODULE = "research_copilot.services.autonomous_runtime"
_AUTONOMOUS_START_CANDIDATES = (
    "autonomous_run",
    "start_autonomous_runtime",
    "start_runtime",
)
_AUTONOMOUS_STATUS_CANDIDATES = (
    "autonomous_status",
    "get_autonomous_runtime_status",
    "get_runtime_status",
)
_AUTONOMOUS_STOP_CANDIDATES = (
    "autonomous_stop",
    "stop_autonomous_runtime",
    "request_autonomous_stop",
)
_AUTONOMOUS_RESUME_CANDIDATES = (
    "autonomous_resume",
    "resume_autonomous_runtime",
    "resume_runtime",
)
_AUTONOMOUS_WORKER_CANDIDATES = (
    "run_autonomous_worker",
    "autonomous_worker",
    "run_worker",
)

def _configure_workspace(workspace: str | None) -> tuple[str, str | None]:
    target = workspace or os.getcwd()
    resolved = os.path.abspath(target)
    previous = os.environ.get("RC_WORKING_DIR")
    os.environ["RC_WORKING_DIR"] = resolved
    return resolved, previous


def _restore_workspace(previous_workspace: str | None) -> None:
    if previous_workspace is None:
        os.environ.pop("RC_WORKING_DIR", None)
        return
    os.environ["RC_WORKING_DIR"] = previous_workspace


def _emit_bootstrap_screen(workspace: str) -> None:
    last_workspace = get_last_workspace()
    click.echo("Research Copilot bootstrap")
    click.echo("=" * 40)
    click.echo(f"Workspace:    {workspace}")
    click.echo("State:        Not initialized")
    click.echo()
    click.echo("Next steps:")
    click.echo("  1. research-copilot init")
    if last_workspace and last_workspace != workspace:
        click.echo(f'  2. research-copilot --workspace "{last_workspace}"')
    else:
        click.echo("  2. research-copilot --help")
    click.echo("  3. research-copilot workflow onboard --json")
    click.echo()
    click.echo("Noninteractive commands stay noninteractive; they never open the TUI unexpectedly.")


def _emit_legacy_workspace_screen() -> None:
    resolved = resolve_workspace()
    click.echo("Research Copilot migration required")
    click.echo("=" * 40)
    click.echo(f"Workspace:    {resolved.workspace_dir}")
    click.echo(f"Legacy root:  {resolved.legacy_root}")
    click.echo("State:        Legacy compatibility workspace detected")
    click.echo()
    click.echo("Next steps:")
    click.echo("  1. research-copilot migrate")
    click.echo("  2. research-copilot status")
    click.echo("  3. research-copilot workflow triage --json")
    click.echo()
    click.echo("Interactive commands may guide migration; machine mode stays explicit.")


def _json_envelope(*, ok: bool, data: Any | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = resolve_workspace()
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "ok": ok,
        "timestamp": utc_now_iso(),
        "workspace": str(resolved.workspace_dir),
        "data": data if ok else None,
        "error": error if not ok else None,
    }


def _emit_error_json(*, code: str, message: str, exit_code: int = 2) -> None:
    click.echo(json.dumps(_json_envelope(ok=False, error={"code": code, "message": message}), indent=2))
    raise SystemExit(exit_code)


def _guard_machine_mutation(as_json: bool) -> None:
    if not is_legacy_workspace():
        return
    message = "Legacy .omx/research workspace detected. Run `research-copilot migrate` before mutating commands."
    if as_json:
        _emit_error_json(code="MIGRATION_REQUIRED", message=message, exit_code=4)
    raise click.ClickException(message)


@click.group(invoke_without_command=True, epilog=CLI_EPILOG.strip())
@click.version_option(version="0.1.0")
@click.option("--workspace", type=click.Path(file_okay=False, dir_okay=True, path_type=str), default=None, help="Optional workspace directory override.")
@click.pass_context
def cli(ctx: click.Context, workspace: str | None):
    """Research Copilot — terminal workflow dashboard for ML research labs."""
    resolved_workspace, previous_workspace = _configure_workspace(workspace)
    ctx.call_on_close(lambda: _restore_workspace(previous_workspace))
    ctx.obj = {"workspace": resolved_workspace}
    if ctx.invoked_subcommand is None:
        if is_legacy_workspace():
            _emit_legacy_workspace_screen()
            return
        if not is_workspace_initialized():
            _emit_bootstrap_screen(resolved_workspace)
            return
        remember_workspace(get_workspace_dir())
        launch_tui()


@cli.command()
def tui():
    """Open the full-screen terminal workflow dashboard."""
    remember_workspace(get_workspace_dir())
    launch_tui()


@cli.command(name="init")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def init_workspace(as_json: bool):
    """Initialize the current workspace for Research Copilot."""
    if is_legacy_workspace() and not is_workspace_initialized():
        message = "Legacy .omx/research workspace detected. Run `research-copilot migrate` instead of `init`."
        if as_json:
            _emit_error_json(code="MIGRATION_REQUIRED", message=message, exit_code=4)
        raise click.ClickException(message)
    payload = initialize_workspace()
    summary = "Workspace already initialized." if payload["already_initialized"] else "Workspace initialized."
    _emit_result(payload, as_json, summary)


@cli.command(name="migrate")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def migrate_command(as_json: bool):
    """Migrate a legacy workspace into the standalone canonical root."""
    try:
        payload = migrate_workspace()
    except ValueError as exc:
        if as_json:
            _emit_error_json(code="WORKSPACE_NOT_INITIALIZED", message=str(exc), exit_code=3)
        raise click.ClickException(str(exc)) from exc
    summary = "Workspace already uses canonical state." if payload["already_migrated"] else "Workspace migrated."
    _emit_result(payload, as_json, summary)


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def status(as_json: bool):
    """Show the current configuration and connection status."""
    config = load_config()
    snapshot = build_dashboard_snapshot()
    onboarding = load_onboarding_contract()
    initialized = is_workspace_initialized()
    workspace_mode = get_workspace_mode()
    invocation_dir = os.path.abspath(os.getenv("RC_WORKING_DIR", os.getcwd()))
    resolved_workspace_dir = str(resolve_workspace().workspace_dir)
    workspace_root = get_research_root()

    if as_json:
        payload = {
            "config": {
                "model": config.model,
                "budget_usd": config.max_budget_usd,
                "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
            },
            "integrations": {
                "wandb": bool(config.wandb.api_key),
                "slurm": bool(config.slurm.host),
                "semantic_scholar_api_key": bool(config.literature.semantic_scholar_api_key),
                "arxiv": True,
            },
            "snapshot": asdict(snapshot),
            "workspace": {
                "directory": resolved_workspace_dir,
                "invocation_directory": invocation_dir,
                "research_root": str(workspace_root),
                "mode": workspace_mode,
            },
            "onboarding": onboarding,
        }
        _emit_result(payload, True)
        return

    click.echo("Research Copilot Configuration")
    click.echo("=" * 40)
    click.echo(f"Model:        {config.model}")
    click.echo(f"Budget:       ${config.max_budget_usd:.2f}")
    click.echo(f"API key set:  {'Yes' if os.environ.get('ANTHROPIC_API_KEY') else 'No'}")
    click.echo()

    click.echo("Integrations:")
    click.echo(f"  W&B:              {'Configured' if config.wandb.api_key else 'Not configured'}")
    click.echo(f"  Slurm:            {'Configured' if config.slurm.host else 'Mock mode'}")
    click.echo(
        "  Semantic Scholar:  "
        f"{'API key set' if config.literature.semantic_scholar_api_key else 'Public (rate limited)'}"
    )
    click.echo("  arXiv:             Available")
    click.echo()
    click.echo("Workflow Snapshot:")
    click.echo(f"  Active jobs:       {snapshot.active_jobs}")
    click.echo(f"  Tracked jobs:      {len(snapshot.jobs)}")
    click.echo(f"  Experiments:       {len(snapshot.experiments)}")
    click.echo(f"  Saved papers:      {len(snapshot.papers)}")
    click.echo(f"  Stored insights:   {len(snapshot.insights)}")
    click.echo()
    click.echo("Workspace:")
    click.echo(f"  Directory:        {resolved_workspace_dir}")
    if invocation_dir != resolved_workspace_dir:
        click.echo(f"  Invoked from:     {invocation_dir}")
    click.echo(f"  Research root:    {workspace_root}")
    state_label = {
        "canonical": "Initialized",
        "legacy": "Legacy compatibility",
        "uninitialized": "Not initialized",
    }.get(workspace_mode, "Unknown")
    click.echo(f"  State:            {state_label}")
    click.echo()
    click.echo("Onboarding:")
    if onboarding:
        click.echo("  State:            Configured")
        click.echo(f"  Goal:             {onboarding.get('goal', 'Unknown')}")
        click.echo(f"  Active profile:   {onboarding.get('active_profile', 'Unknown')}")
        click.echo("  Next step:        research-copilot workflow triage")
    else:
        click.echo("  State:            Not configured")
        if workspace_mode == "legacy":
            next_step = "research-copilot migrate"
        else:
            next_step = "research-copilot workflow onboard" if initialized else "research-copilot init"
        click.echo(f"  Next step:        {next_step}")
    click.echo()
    click.echo("Run 'research-copilot' or 'research-copilot tui' to open the terminal dashboard.")


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _run_command(coro: Any) -> Any:
    try:
        return _run_async(coro)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _emit_result(payload: Any, as_json: bool, summary: str | None = None) -> None:
    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=payload), indent=2, sort_keys=True, default=str))
    elif summary:
        click.echo(summary)
    else:
        click.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _load_autonomous_runtime_callable(*candidate_names: str) -> Any:
    try:
        module = importlib.import_module(AUTONOMOUS_RUNTIME_MODULE)
    except ModuleNotFoundError as exc:
        raise click.ClickException("Autonomous runtime services are not available in this build.") from exc

    for name in candidate_names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate

    raise click.ClickException(
        "Autonomous runtime services are missing an expected lifecycle entrypoint: "
        + ", ".join(candidate_names)
    )


def _invoke_autonomous_runtime(*candidate_names: str, **kwargs: Any) -> Any:
    handler = _load_autonomous_runtime_callable(*candidate_names)
    signature = inspect.signature(handler)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        bound_kwargs = kwargs
    else:
        bound_kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    result = handler(**bound_kwargs)
    return _run_command(result) if inspect.isawaitable(result) else result


def _parse_key_value_pairs(values: tuple[str, ...], *, option_name: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_item in values:
        key, separator, raw_value = raw_item.partition("=")
        if not separator or not key.strip():
            raise click.ClickException(f"`--{option_name}` entries must use KEY=VALUE format.")
        parsed[key.strip()] = raw_value
    return parsed


def _parse_json_object(raw_value: str, *, option_name: str) -> dict[str, Any]:
    if not raw_value.strip():
        return {}
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"`--{option_name}` must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise click.ClickException(f"`--{option_name}` must decode to a JSON object.")
    return payload


def _runtime_payload_view(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    runtime = payload.get("runtime")
    return runtime if isinstance(runtime, dict) else payload


def _runtime_value(payload: Any, key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    if key in payload:
        return payload.get(key)
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        return runtime.get(key)
    return None


def _runtime_summary(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        summary = payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary
        runtime = payload.get("runtime")
        if isinstance(runtime, dict):
            nested_summary = runtime.get("summary")
            if isinstance(nested_summary, str) and nested_summary.strip():
                return nested_summary
    return fallback


def _autonomous_status_unavailable_payload(run_id: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "unavailable",
        "available": False,
        "summary": "Autonomous runtime services are not available in this build.",
    }


def _autonomous_worker_auth_dir() -> Path:
    path = resolve_workspace().canonical_root / "runtime" / "auth"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _create_autonomous_worker_auth_file(run_id: str, owner_token: str) -> Path:
    auth_path = _autonomous_worker_auth_dir() / f"{run_id}-{uuid4().hex}.json"
    payload = {
        "run_id": run_id,
        "owner_token": owner_token,
        "owner_instance_id": uuid4().hex,
        "created_at": utc_now_iso(),
    }
    auth_path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(auth_path, 0o600)
    except OSError:
        pass
    return auth_path


def _read_autonomous_worker_auth_file(auth_file: str) -> dict[str, Any]:
    path = Path(auth_file)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)
    return payload if isinstance(payload, dict) else {}


def _autonomous_worker_argv(run_id: str, auth_file: str) -> list[str]:
    workspace = str(resolve_workspace().workspace_dir)
    return [
        sys.executable,
        "-m",
        "research_copilot.main",
        "--workspace",
        workspace,
        "workflow",
        "autonomous-worker",
        "--run-id",
        run_id,
        "--auth-file",
        auth_file,
    ]


def _autonomous_worker_env() -> dict[str, str]:
    workspace = str(resolve_workspace().workspace_dir)
    env = os.environ.copy()
    env["RC_WORKING_DIR"] = workspace
    src_root = str(Path(__file__).resolve().parent.parent)
    existing_pythonpath = env.get("PYTHONPATH", "")
    paths = [path for path in existing_pythonpath.split(os.pathsep) if path]
    if src_root not in paths:
        env["PYTHONPATH"] = os.pathsep.join([src_root, *paths]) if paths else src_root
    return env


def _should_launch_autonomous_worker(payload: Any) -> bool:
    runtime = _runtime_payload_view(payload)
    if not runtime:
        return False
    if runtime.get("worker_spawned") or runtime.get("worker_started"):
        return False
    if runtime.get("owner_pid"):
        return False
    return bool(_runtime_value(payload, "run_id") and _runtime_value(payload, "owner_token"))


def _launch_autonomous_worker(payload: Any) -> None:
    if not _should_launch_autonomous_worker(payload):
        return

    run_id = str(_runtime_value(payload, "run_id") or "").strip()
    owner_token = str(_runtime_value(payload, "owner_token") or "").strip()
    if not run_id or not owner_token:
        raise click.ClickException("Autonomous runtime start did not return a run_id/owner_token pair.")
    auth_file = _create_autonomous_worker_auth_file(run_id, owner_token)

    popen_kwargs: dict[str, Any] = {
        "args": _autonomous_worker_argv(run_id, str(auth_file)),
        "cwd": str(resolve_workspace().workspace_dir),
        "env": _autonomous_worker_env(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = 0
        for flag_name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_NO_WINDOW"):
            creationflags |= int(getattr(subprocess, flag_name, 0))
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(**popen_kwargs)


def _load_mcp_server_entrypoint() -> Any:
    try:
        module = importlib.import_module("research_copilot.integrations.mcp.server")
    except ModuleNotFoundError as exc:
        raise click.ClickException(
            "MCP server transport is not available in this build yet."
        ) from exc

    serve = getattr(module, "serve_stdio", None) or getattr(module, "serve_stdio_server", None)
    if serve is None:
        raise click.ClickException(
            "Expected `serve_stdio` in research_copilot.integrations.mcp.server."
        )
    return serve


@cli.command()
@click.option("--limit", default=5, show_default=True, type=click.IntRange(1, 50))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def snapshot(limit: int, as_json: bool):
    """Show the current service-backed snapshot."""
    payload = _run_command(get_snapshot_service(max_items=limit))
    summary = (
        f"Snapshot: {payload['jobs']['active']} active job(s), "
        f"{payload['experiments']['total']} experiment(s)."
    )
    _emit_result(payload, as_json, summary)


@cli.group(epilog=MCP_EPILOG.strip())
def mcp():
    """Run the MCP server and render agent install/config snippets."""


@mcp.command("serve")
def mcp_serve():
    """Run the stdio MCP server for coding-agent workflows."""
    entrypoint = _load_mcp_server_entrypoint()
    result = entrypoint()
    if asyncio.iscoroutine(result):
        _run_command(result)


@mcp.command("print-codex-config")
def mcp_print_codex_config():
    """Render a usable Codex MCP setup snippet."""
    click.echo(render_codex_config())


@mcp.command("print-claude-config")
def mcp_print_claude_config():
    """Render a project-scoped Claude Code `.mcp.json` example."""
    click.echo(render_claude_config())


@mcp.command("print-agents-snippet")
def mcp_print_agents_snippet():
    """Render an AGENTS.md hint that nudges Research Copilot MCP usage."""
    click.echo(render_agents_snippet())


@cli.group()
def runtime():
    """Inspect and steer the Codex-managed runtime."""


@runtime.command("codex-attach")
@click.option("--session-id", required=True, help="Stable Codex session identifier.")
@click.option("--goal", default="", help="Optional operator-visible goal.")
@click.option("--constraint", "constraints", multiple=True, help="Repeatable session constraint.")
@click.option(
    "--allowed-action",
    "allowed_actions",
    multiple=True,
    help="Repeatable allowed action.",
)
@click.option("--status", default="running", show_default=True)
@click.option("--current-turn", type=int, default=None)
@click.option("--turn-state", default="", help="Current Codex turn state.")
@click.option(
    "--operator-mode",
    type=click.Choice(["hands_off", "steerable", "paused"], case_sensitive=False),
    default="steerable",
    show_default=True,
)
@click.option(
    "--transport",
    "transport_type",
    type=click.Choice(["tmux-pane", "managed-process"], case_sensitive=False),
    default="tmux-pane",
    show_default=True,
)
@click.option("--pane-id", default="")
@click.option("--window-name", default="")
@click.option("--session-name", default="")
@click.option("--profile-name", default="")
@click.option("--autonomy-level", default="")
@click.option("--actor-type", default="codex", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_attach(
    session_id: str,
    goal: str,
    constraints: tuple[str, ...],
    allowed_actions: tuple[str, ...],
    status: str,
    current_turn: int | None,
    turn_state: str,
    operator_mode: str,
    transport_type: str,
    pane_id: str,
    window_name: str,
    session_name: str,
    profile_name: str,
    autonomy_level: str,
    actor_type: str,
    as_json: bool,
):
    """Register or reattach a Codex session as the active runtime."""
    _guard_machine_mutation(as_json)
    payload = attach_codex_session(
        session_id=session_id,
        goal=goal,
        constraints=list(constraints),
        allowed_actions=list(allowed_actions),
        status=status,
        current_turn=current_turn,
        turn_state=turn_state,
        operator_mode=operator_mode,
        transport=transport_type,
        pane_id=pane_id,
        window_name=window_name,
        session_name=session_name,
        workspace=str(resolve_workspace().workspace_dir),
        profile_name=profile_name,
        autonomy_level=autonomy_level,
        actor_type=actor_type,
    )
    _emit_result(payload, as_json, f"Codex session {payload['session_id']} attached.")


@runtime.command("codex-status")
@click.option("--session-id", default="", help="Optional session identifier; defaults to the active session.")
@click.option("--include-nudges", is_flag=True, help="Include queued steering nudges.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_status(session_id: str, include_nudges: bool, as_json: bool):
    """Inspect Codex-managed runtime state without mutating it."""
    payload = codex_runtime_status(session_id=session_id, include_nudges=include_nudges)
    summary = (
        f"Codex session {payload['session_id']} is {payload['status']} "
        f"({payload['freshness_label']})."
        if payload.get("available")
        else payload["summary"]
    )
    _emit_result(payload, as_json, summary)


@runtime.command("codex-report")
@click.option("--session-id", required=True, help="Stable Codex session identifier.")
@click.option("--turn-number", type=int, required=True, help="Bounded Codex turn number.")
@click.option("--summary", required=True, help="Visible summary for this Codex turn.")
@click.option("--action", default="", help="Latest visible action label.")
@click.option("--artifact", "artifacts", multiple=True, help="Repeatable artifact identifier.")
@click.option("--status", default="", help="Optional reported status override.")
@click.option("--turn-state", default="", help="Optional reported turn state.")
@click.option("--experiment-id", default="", help="Optional experiment id touched by this turn.")
@click.option("--review-id", default="", help="Optional review id touched by this turn.")
@click.option("--context-update", default="", help="Optional context key/value label updated by this turn.")
@click.option("--reported-at", default="", help="Optional report timestamp override.")
@click.option("--heartbeat-at", default="", help="Optional heartbeat timestamp override.")
@click.option("--pane-id", default="")
@click.option("--window-name", default="")
@click.option("--session-name", default="")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_report(
    session_id: str,
    turn_number: int,
    summary: str,
    action: str,
    artifacts: tuple[str, ...],
    status: str,
    turn_state: str,
    experiment_id: str,
    review_id: str,
    context_update: str,
    reported_at: str,
    heartbeat_at: str,
    pane_id: str,
    window_name: str,
    session_name: str,
    as_json: bool,
):
    """Ingest one bounded Codex turn report into the shared runtime state."""
    _guard_machine_mutation(as_json)
    payload = ingest_codex_turn_report(
        session_id=session_id,
        turn_number=turn_number,
        summary=summary,
        action=action,
        artifacts=list(artifacts),
        status=status,
        turn_state=turn_state,
        experiment_id=experiment_id,
        review_id=review_id,
        context_update=context_update,
        reported_at=reported_at,
        heartbeat_at=heartbeat_at,
        pane_id=pane_id,
        window_name=window_name,
        session_name=session_name,
        workspace=str(resolve_workspace().workspace_dir),
    )
    outcome = "conflict" if payload.get("conflict") else "duplicate" if payload.get("duplicate") else "accepted"
    _emit_result(payload, as_json, f"Turn {turn_number} for session {session_id} {outcome}.")


@runtime.command("codex-nudge")
@click.option("--session-id", required=True, help="Stable Codex session identifier.")
@click.option(
    "--kind",
    type=click.Choice(["nudge", "pause", "resume", "stop_after_turn", "request_summary"], case_sensitive=False),
    required=True,
)
@click.option("--message", default="", help="Optional operator steering message.")
@click.option("--payload", default="", help="Optional JSON payload for richer steering metadata.")
@click.option("--actor-type", default="human", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_nudge(
    session_id: str,
    kind: str,
    message: str,
    payload: str,
    actor_type: str,
    as_json: bool,
):
    """Enqueue one steering nudge for a Codex-managed session."""
    _guard_machine_mutation(as_json)
    result = enqueue_codex_nudge(
        session_id=session_id,
        kind=kind,
        message=message,
        payload=_parse_json_object(payload, option_name="payload"),
        actor_type=actor_type,
    )
    _emit_result(result, as_json, f"Queued {kind} nudge for session {session_id}.")


def _runtime_codex_nudge_wrapper(kind: str, session_id: str, message: str, as_json: bool) -> None:
    _guard_machine_mutation(as_json)
    result = enqueue_codex_nudge(session_id=session_id, kind=kind, message=message)
    _emit_result(result, as_json, f"Queued {kind} nudge for session {session_id}.")


@runtime.command("codex-pause")
@click.option("--session-id", required=True, help="Stable Codex session identifier.")
@click.option("--message", default="", help="Optional pause reason.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_pause(session_id: str, message: str, as_json: bool):
    """Pause a steerable Codex session after the current bounded turn."""
    _runtime_codex_nudge_wrapper("pause", session_id, message, as_json)


@runtime.command("codex-resume")
@click.option("--session-id", required=True, help="Stable Codex session identifier.")
@click.option("--message", default="", help="Optional resume note.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_resume(session_id: str, message: str, as_json: bool):
    """Resume a paused Codex session."""
    _runtime_codex_nudge_wrapper("resume", session_id, message, as_json)


@runtime.command("codex-stop-after-turn")
@click.option("--session-id", required=True, help="Stable Codex session identifier.")
@click.option("--message", default="", help="Optional operator stop reason.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_stop_after_turn(session_id: str, message: str, as_json: bool):
    """Request a graceful stop after the current bounded Codex turn."""
    _runtime_codex_nudge_wrapper("stop_after_turn", session_id, message, as_json)


@runtime.command("codex-request-summary")
@click.option("--session-id", required=True, help="Stable Codex session identifier.")
@click.option("--message", default="", help="Optional request detail.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_request_summary(session_id: str, message: str, as_json: bool):
    """Request an immediate visible Codex turn summary refresh."""
    _runtime_codex_nudge_wrapper("request_summary", session_id, message, as_json)


@runtime.command("codex-drain-nudges")
@click.option("--session-id", required=True, help="Stable Codex session identifier.")
@click.option("--limit", type=click.IntRange(1, None), default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runtime_codex_drain_nudges(session_id: str, limit: int | None, as_json: bool):
    """Drain and return pending steering nudges for a Codex-managed session."""
    _guard_machine_mutation(as_json)
    payload = drain_codex_nudges(session_id=session_id, limit=limit)
    _emit_result(payload, as_json, f"Drained {len(payload['drained'])} nudge(s) for session {session_id}.")


@cli.group()
def jobs():
    """Inspect and mutate jobs via the shared service boundary."""


@jobs.command("list")
@click.option("--status-filter", default="", help="Optional status filter.")
@click.option("--limit", default=20, show_default=True, type=click.IntRange(1, 100))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def jobs_list(status_filter: str, limit: int, as_json: bool):
    payload = _run_command(list_jobs_service(status_filter=status_filter, limit=limit))
    _emit_result(payload, as_json, f"Listed {payload['total']} job(s).")


@jobs.command("get")
@click.argument("job_id")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def jobs_get(job_id: str, as_json: bool):
    payload = _run_command(get_job_service(job_id=job_id))
    _emit_result(payload, as_json, f"Job {payload['job_id']} is {payload['status']}." )


@jobs.command("logs")
@click.argument("job_id")
@click.option("--lines", default=100, show_default=True, type=click.IntRange(1, 500))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def jobs_logs(job_id: str, lines: int, as_json: bool):
    payload = _run_command(get_job_logs_service(job_id=job_id, lines=lines))
    _emit_result(payload, as_json, f"Fetched logs for job {job_id}.")


@jobs.command("submit")
@click.option("--name", "job_name", required=True, help="Job name.")
@click.option("--script", required=True, help="Submission script content.")
@click.option("--partition", default="gpu", show_default=True)
@click.option("--gpus", default=1, show_default=True, type=int)
@click.option("--time-limit", default="04:00:00", show_default=True)
@click.option("--submitted-by", default="codex", show_default=True)
@click.option("--workflow-name", default="", help="Optional workflow provenance.")
@click.option("--experiment-id", default="", help="Optional linked experiment id.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def jobs_submit(
    job_name: str,
    script: str,
    partition: str,
    gpus: int,
    time_limit: str,
    submitted_by: str,
    workflow_name: str,
    experiment_id: str,
    as_json: bool,
):
    _guard_machine_mutation(as_json)
    payload = _run_command(
        submit_job_service(
            job_name=job_name,
            script=script,
            partition=partition,
            gpus=gpus,
            time_limit=time_limit,
            submitted_by=submitted_by,
            workflow_name=workflow_name,
            experiment_id=experiment_id,
        )
    )
    _emit_result(payload, as_json, f"Submitted job {payload['job_id']}." )


@jobs.command("cancel")
@click.argument("job_id")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def jobs_cancel(job_id: str, as_json: bool):
    _guard_machine_mutation(as_json)
    payload = _run_command(cancel_job_service(job_id=job_id))
    _emit_result(payload, as_json, f"Cancelled job {job_id}.")


@cli.group()
def experiments():
    """Inspect and mutate experiments via the shared service boundary."""


@experiments.command("list")
@click.option("--status", default="", help="Optional status filter.")
@click.option("--dataset", default="", help="Optional dataset filter.")
@click.option("--model-type", default="", help="Optional model filter.")
@click.option("--tag", default="", help="Optional tag filter.")
@click.option("--search-text", default="", help="Optional free-text search.")
@click.option("--limit", default=20, show_default=True, type=click.IntRange(1, 100))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def experiments_list(
    status: str,
    dataset: str,
    model_type: str,
    tag: str,
    search_text: str,
    limit: int,
    as_json: bool,
):
    payload = _run_command(
        list_experiments_service(
            status=status,
            dataset=dataset,
            model_type=model_type,
            tag=tag,
            search_text=search_text,
            limit=limit,
        )
    )
    _emit_result(payload, as_json, f"Listed {payload['total']} experiment(s).")


@experiments.command("get")
@click.argument("experiment_id")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def experiments_get(experiment_id: str, as_json: bool):
    payload = _run_command(get_experiment_service(experiment_id=experiment_id))
    _emit_result(payload, as_json, f"Loaded experiment {payload['id']}." )


@experiments.command("create")
@click.option("--name", required=True)
@click.option("--hypothesis", default="")
@click.option("--description", default="")
@click.option("--config", default="", help="JSON config string.")
@click.option("--status", default="planned", show_default=True)
@click.option("--dataset", default="")
@click.option("--model-type", default="")
@click.option("--tag", "tags", multiple=True)
@click.option("--created-by", default="codex", show_default=True)
@click.option("--actor-type", default="codex", show_default=True)
@click.option("--workflow-name", default="")
@click.option("--results", default="", help="JSON results string.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def experiments_create(
    name: str,
    hypothesis: str,
    description: str,
    config: str,
    status: str,
    dataset: str,
    model_type: str,
    tags: tuple[str, ...],
    created_by: str,
    actor_type: str,
    workflow_name: str,
    results: str,
    as_json: bool,
):
    _guard_machine_mutation(as_json)
    payload = _run_command(
        create_experiment_service(
            name=name,
            hypothesis=hypothesis,
            description=description,
            config=config,
            status=status,
            dataset=dataset,
            model_type=model_type,
            tags=list(tags),
            created_by=created_by,
            actor_type=actor_type,
            workflow_name=workflow_name,
            results=results,
        )
    )
    _emit_result(payload, as_json, f"Created experiment {payload['id']}." )


@experiments.command("update")
@click.argument("experiment_id")
@click.option("--status", default="")
@click.option("--results", default="", help="JSON results string.")
@click.option("--wandb-run-id", default="")
@click.option("--wandb-run-url", default="")
@click.option("--slurm-job-id", default="")
@click.option("--actor-type", default="codex", show_default=True)
@click.option("--workflow-name", default="")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def experiments_update(
    experiment_id: str,
    status: str,
    results: str,
    wandb_run_id: str,
    wandb_run_url: str,
    slurm_job_id: str,
    actor_type: str,
    workflow_name: str,
    as_json: bool,
):
    _guard_machine_mutation(as_json)
    payload = _run_command(
        update_experiment_service(
            experiment_id=experiment_id,
            status=status,
            results=results,
            wandb_run_id=wandb_run_id,
            wandb_run_url=wandb_run_url,
            slurm_job_id=slurm_job_id,
            actor_type=actor_type,
            workflow_name=workflow_name,
        )
    )
    _emit_result(payload, as_json, f"Updated experiment {experiment_id}." )


@cli.group()
def context():
    """Inspect and mutate research context entries."""


@context.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def context_list(as_json: bool):
    payload = _run_command(list_context_service())
    _emit_result(payload, as_json, f"Listed {payload['total']} context entrie(s).")


@context.command("get")
@click.argument("key")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def context_get(key: str, as_json: bool):
    payload = _run_command(get_context_service(key=key))
    _emit_result(payload, as_json, f"Loaded context '{key}'.")


@context.command("set")
@click.argument("key")
@click.option("--value", required=True)
@click.option(
    "--context-type",
    default="note",
    show_default=True,
    type=click.Choice(["goal", "plan", "note", "reference", "constraint"], case_sensitive=False),
)
@click.option("--actor-type", default="codex", show_default=True)
@click.option("--workflow-name", default="")
@click.option("--linked-experiment-id", default="")
@click.option("--linked-job-id", default="")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def context_set(
    key: str,
    value: str,
    context_type: str,
    actor_type: str,
    workflow_name: str,
    linked_experiment_id: str,
    linked_job_id: str,
    as_json: bool,
):
    _guard_machine_mutation(as_json)
    payload = _run_command(
        set_context_service(
            key=key,
            value=value,
            context_type=context_type,
            actor_type=actor_type,
            workflow_name=workflow_name,
            linked_experiment_id=linked_experiment_id,
            linked_job_id=linked_job_id,
        )
    )
    _emit_result(payload, as_json, f"Updated context '{key}'.")


@cli.group()
def insights():
    """Inspect and mutate research insights."""


@insights.command("list")
@click.option("--category", default="")
@click.option("--tag", default="")
@click.option("--search-text", default="")
@click.option("--limit", default=20, show_default=True, type=click.IntRange(1, 100))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def insights_list(category: str, tag: str, search_text: str, limit: int, as_json: bool):
    payload = _run_command(
        list_insights_service(category=category, tag=tag, search_text=search_text, limit=limit)
    )
    _emit_result(payload, as_json, f"Listed {payload['total']} insight(s).")


@insights.command("add")
@click.option("--title", required=True)
@click.option("--content", required=True)
@click.option(
    "--category",
    default="observation",
    show_default=True,
    type=click.Choice(["finding", "failure", "hypothesis", "technique", "observation"], case_sensitive=False),
)
@click.option("--experiment-id", default="")
@click.option("--confidence", type=float, default=None)
@click.option("--tag", "tags", multiple=True)
@click.option("--created-by", default="codex", show_default=True)
@click.option("--actor-type", default="codex", show_default=True)
@click.option("--workflow-name", default="")
@click.option("--linked-job-id", default="")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def insights_add(
    title: str,
    content: str,
    category: str,
    experiment_id: str,
    confidence: float | None,
    tags: tuple[str, ...],
    created_by: str,
    actor_type: str,
    workflow_name: str,
    linked_job_id: str,
    as_json: bool,
):
    _guard_machine_mutation(as_json)
    payload = _run_command(
        add_insight_service(
            title=title,
            content=content,
            category=category,
            experiment_id=experiment_id,
            confidence=confidence,
            tags=list(tags),
            created_by=created_by,
            actor_type=actor_type,
            workflow_name=workflow_name,
            linked_job_id=linked_job_id,
        )
    )
    _emit_result(payload, as_json, f"Stored insight {payload['id']}." )


@cli.group()
def papers():
    """Search and persist literature references."""


@papers.command("search")
@click.argument("query")
@click.option("--max-results", default=10, show_default=True, type=click.IntRange(1, 50))
@click.option(
    "--sources",
    default="both",
    show_default=True,
    type=click.Choice(["arxiv", "semantic_scholar", "both"], case_sensitive=False),
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def papers_search(query: str, max_results: int, sources: str, as_json: bool):
    payload = _run_command(search_papers_service(query=query, max_results=max_results, sources=sources))
    _emit_result(payload, as_json, f"Found {payload['total']} paper(s).")


@papers.command("save")
@click.option("--title", required=True)
@click.option("--author", "authors", multiple=True)
@click.option("--abstract", default="")
@click.option("--arxiv-id", default="")
@click.option("--year", type=int, default=None)
@click.option("--url", default="")
@click.option("--relevance-notes", default="")
@click.option("--tag", "tags", multiple=True)
@click.option("--actor-type", default="codex", show_default=True)
@click.option("--workflow-name", default="")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def papers_save(
    title: str,
    authors: tuple[str, ...],
    abstract: str,
    arxiv_id: str,
    year: int | None,
    url: str,
    relevance_notes: str,
    tags: tuple[str, ...],
    actor_type: str,
    workflow_name: str,
    as_json: bool,
):
    _guard_machine_mutation(as_json)
    payload = _run_command(
        save_paper_service(
            title=title,
            authors=list(authors),
            abstract=abstract,
            arxiv_id=arxiv_id,
            year=year,
            url=url,
            relevance_notes=relevance_notes,
            tags=list(tags),
            actor_type=actor_type,
            workflow_name=workflow_name,
        )
    )
    _emit_result(payload, as_json, f"Saved paper {payload['id']}." )


@papers.command("list")
@click.option("--limit", default=20, show_default=True, type=click.IntRange(1, 100))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def papers_list(limit: int, as_json: bool):
    payload = _run_command(list_papers_service(limit=limit))
    _emit_result(payload, as_json, f"Listed {payload['total']} paper(s).")


@cli.group(epilog=WORKFLOW_EPILOG.strip())
def workflow():
    """Run named research workflow commands for the solo research loop."""


@workflow.command()
@click.option("--limit", default=5, show_default=True, type=click.IntRange(1, 20))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def triage(limit: int, as_json: bool):
    """Inspect current lab state and suggest the next workflow."""
    payload = _run_command(triage_workflow(max_items=limit))
    summary = (
        f"Suggested next action: {payload['suggested_next_action']}\n"
        f"Blockers: {'; '.join(payload['blockers'])}"
    )
    _emit_result(payload, as_json, summary)


@workflow.command("launch-experiment")
@click.option("--name", required=True, help="Experiment name.")
@click.option("--script", required=True, help="Submission script content.")
@click.option("--hypothesis", default="", help="Experiment hypothesis.")
@click.option("--description", default="", help="Experiment description.")
@click.option("--config", default="", help="JSON config string.")
@click.option("--dataset", default="", help="Dataset name.")
@click.option("--model-type", default="", help="Model family/type.")
@click.option("--tag", "tags", multiple=True, help="Repeatable experiment tag.")
@click.option("--created-by", default="codex", show_default=True, help="Actor identifier.")
@click.option("--partition", default="gpu", show_default=True)
@click.option("--gpus", default=1, show_default=True, type=int)
@click.option("--time-limit", default="04:00:00", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def launch_experiment(
    name: str,
    script: str,
    hypothesis: str,
    description: str,
    config: str,
    dataset: str,
    model_type: str,
    tags: tuple[str, ...],
    created_by: str,
    partition: str,
    gpus: int,
    time_limit: str,
    as_json: bool,
):
    """Register an experiment and launch its job."""
    _guard_machine_mutation(as_json)
    payload = _run_command(
        launch_experiment_workflow(
            name=name,
            script=script,
            hypothesis=hypothesis,
            description=description,
            config=config,
            dataset=dataset,
            model_type=model_type,
            tags=list(tags),
            created_by=created_by,
            partition=partition,
            gpus=gpus,
            time_limit=time_limit,
        )
    )
    _emit_result(payload, as_json, payload["summary"])


@workflow.command("monitor-run")
@click.argument("identifier")
@click.option(
    "--kind",
    type=click.Choice(["auto", "job", "experiment"], case_sensitive=False),
    default="auto",
    show_default=True,
)
@click.option("--lines", default=20, show_default=True, type=click.IntRange(1, 200))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def monitor_run(identifier: str, kind: str, lines: int, as_json: bool):
    """Refresh a job or experiment and show the latest run state."""
    payload = _run_command(monitor_run_workflow(identifier=identifier, kind=kind, lines=lines))
    experiment = payload.get("experiment") or {}
    job = payload["job"]
    summary = f"Job {job['job_id']} is {job['status']}"
    if experiment:
        summary += f" for experiment {experiment.get('name')}"
    _emit_result(payload, as_json, summary)


@workflow.command("review-results")
@click.argument("experiment_id")
@click.option("--insight-title", default="", help="Optional insight title to persist.")
@click.option("--insight-content", default="", help="Optional insight body to persist.")
@click.option("--context-key", default="", help="Optional context key to update.")
@click.option("--context-value", default="", help="Optional context value to update.")
@click.option(
    "--context-type",
    default="note",
    show_default=True,
    type=click.Choice(["goal", "plan", "note", "reference", "constraint"], case_sensitive=False),
)
@click.option("--created-by", default="codex", show_default=True, help="Actor identifier.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def review_results(
    experiment_id: str,
    insight_title: str,
    insight_content: str,
    context_key: str,
    context_value: str,
    context_type: str,
    created_by: str,
    as_json: bool,
):
    """Inspect an experiment and optionally save follow-up insight/context."""
    _guard_machine_mutation(as_json)
    payload = _run_command(
        review_results_workflow(
            experiment_id=experiment_id,
            insight_title=insight_title,
            insight_content=insight_content,
            context_key=context_key,
            context_value=context_value,
            context_type=context_type,
            created_by=created_by,
        )
    )
    summary = (
        f"Reviewed experiment {payload['experiment']['name']} with result keys: "
        f"{', '.join(payload['result_keys']) or 'none'}"
    )
    _emit_result(payload, as_json, summary)


@workflow.command("research-context")
@click.argument("query")
@click.option("--max-results", default=5, show_default=True, type=click.IntRange(1, 20))
@click.option("--save-first", is_flag=True, help="Persist the top search result.")
@click.option("--relevance-notes", default="", help="Why the saved paper matters.")
@click.option("--context-key", default="", help="Optional context key to update.")
@click.option("--context-value", default="", help="Optional context value to update.")
@click.option(
    "--context-type",
    default="note",
    show_default=True,
    type=click.Choice(["goal", "plan", "note", "reference", "constraint"], case_sensitive=False),
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def research_context(
    query: str,
    max_results: int,
    save_first: bool,
    relevance_notes: str,
    context_key: str,
    context_value: str,
    context_type: str,
    as_json: bool,
):
    """Search literature and update research memory."""
    _guard_machine_mutation(as_json)
    payload = _run_command(
        research_context_workflow(
            query=query,
            max_results=max_results,
            save_first=save_first,
            relevance_notes=relevance_notes,
            context_key=context_key,
            context_value=context_value,
            context_type=context_type,
        )
    )
    _emit_result(payload, as_json, f"Found {payload['papers_total']} paper(s) for '{query}'.")


@workflow.command("run-experiment")
@click.option("--command", required=True, help="Local shell command to execute.")
@click.option("--experiment-id", default="", help="Optional existing experiment id.")
@click.option("--name", default="", help="Experiment name if creating a new one.")
@click.option("--hypothesis", default="")
@click.option("--description", default="")
@click.option("--config", default="", help="JSON config string.")
@click.option("--dataset", default="")
@click.option("--model-type", default="")
@click.option("--tag", "tags", multiple=True)
@click.option("--created-by", default="human", show_default=True)
@click.option("--actor-type", default="human", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_run_experiment(
    command: str,
    experiment_id: str,
    name: str,
    hypothesis: str,
    description: str,
    config: str,
    dataset: str,
    model_type: str,
    tags: tuple[str, ...],
    created_by: str,
    actor_type: str,
    as_json: bool,
):
    """Execute a real local experiment command and persist a run artifact."""
    _guard_machine_mutation(as_json)
    payload = _run_command(
        run_experiment_workflow(
            command=command,
            experiment_id=experiment_id,
            name=name,
            hypothesis=hypothesis,
            description=description,
            config=config,
            dataset=dataset,
            model_type=model_type,
            tags=list(tags),
            created_by=created_by,
            actor_type=actor_type,
        )
    )
    _emit_result(
        payload,
        as_json,
        f"Run {payload['run']['run_id']} finished with status {payload['run']['status']}.",
    )


@workflow.command("overfitting-check")
@click.argument("experiment_id")
@click.option("--train-key", default="train_loss", show_default=True)
@click.option("--validation-key", default="val_loss", show_default=True)
@click.option("--test-key", default="test_loss", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_overfitting_check(
    experiment_id: str,
    train_key: str,
    validation_key: str,
    test_key: str,
    as_json: bool,
):
    """Inspect an experiment for simple overfitting signals."""
    payload = _run_command(
        overfitting_check_workflow(
            experiment_id=experiment_id,
            train_key=train_key,
            validation_key=validation_key,
            test_key=test_key,
        )
    )
    _emit_result(payload, as_json, "; ".join(payload["review"]["diagnostics"]))


@workflow.command("next-step")
@click.argument("experiment_id")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_next_step(experiment_id: str, as_json: bool):
    """Propose the next step after reviewing an experiment."""
    payload = _run_command(next_step_workflow(experiment_id=experiment_id))
    _emit_result(payload, as_json, "; ".join(payload["review"]["suggestions"]))


@workflow.command("autonomous-run")
@click.option("--goal", default="", help="Optional goal override for the persistent runtime.")
@click.option("--profile", "profile_name", default="", help="Optional autonomous profile override.")
@click.option("--success-criteria", default="", help="Optional success criteria override.")
@click.option(
    "--autonomy-level",
    type=click.Choice(["guided", "bounded", "aggressive"], case_sensitive=False),
    default=None,
    help="Optional autonomy-level override.",
)
@click.option("--allowed-action", "allowed_actions", multiple=True, help="Repeatable allowed action.")
@click.option("--constraint", "constraints", multiple=True, help="Repeatable constraint.")
@click.option("--stop-condition", "stop_conditions", multiple=True, help="Repeatable stop condition.")
@click.option(
    "--command-template",
    default="",
    help="Reusable local command template for persistent run-experiment steps.",
)
@click.option(
    "--template-var",
    "template_vars",
    multiple=True,
    help="Persisted template variable in KEY=VALUE form.",
)
@click.option(
    "--action-envelope",
    default="",
    help="Optional JSON object override for the normalized executable action envelope.",
)
@click.option("--max-iterations", type=click.IntRange(1, None), default=None)
@click.option("--created-by", default="human", show_default=True)
@click.option("--actor-type", default="human", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_autonomous_run(
    goal: str,
    profile_name: str,
    success_criteria: str,
    autonomy_level: str | None,
    allowed_actions: tuple[str, ...],
    constraints: tuple[str, ...],
    stop_conditions: tuple[str, ...],
    command_template: str,
    template_vars: tuple[str, ...],
    action_envelope: str,
    max_iterations: int | None,
    created_by: str,
    actor_type: str,
    as_json: bool,
):
    """Start the persistent autonomous runtime and detach its worker."""
    _guard_machine_mutation(as_json)
    payload = _invoke_autonomous_runtime(
        *_AUTONOMOUS_START_CANDIDATES,
        goal=goal,
        success_criteria=success_criteria,
        profile_name=profile_name,
        active_profile=profile_name,
        autonomy_level=autonomy_level or "",
        allowed_actions=list(allowed_actions),
        constraints=list(constraints),
        stop_conditions=list(stop_conditions),
        command_template=command_template,
        template_vars=_parse_key_value_pairs(template_vars, option_name="template-var"),
        action_envelope=_parse_json_object(action_envelope, option_name="action-envelope"),
        max_iterations=max_iterations,
        created_by=created_by,
        actor_type=actor_type,
        actor=actor_type,
        spawn_worker=False,
        launch_worker=False,
        detach=False,
    )
    _launch_autonomous_worker(payload)
    run_id = str(_runtime_value(payload, "run_id") or "unknown")
    status_value = str(_runtime_value(payload, "status") or "running")
    _emit_result(
        payload,
        as_json,
        _runtime_summary(payload, f"Autonomous runtime {run_id} started with status {status_value}."),
    )


@workflow.command("autonomous-status")
@click.option("--run-id", default="", help="Optional runtime id; defaults to the active runtime.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_autonomous_status(run_id: str, as_json: bool):
    """Inspect persisted autonomous runtime state without mutating it."""
    try:
        payload = _invoke_autonomous_runtime(
            *_AUTONOMOUS_STATUS_CANDIDATES,
            run_id=run_id,
            runtime_id=run_id,
        )
    except click.ClickException as exc:
        if "Autonomous runtime services are not available" not in str(exc):
            raise
        payload = _autonomous_status_unavailable_payload(run_id)
    resolved_run_id = str(_runtime_value(payload, "run_id") or run_id or "active")
    status_value = str(_runtime_value(payload, "status") or "unknown")
    _emit_result(
        payload,
        as_json,
        _runtime_summary(payload, f"Autonomous runtime {resolved_run_id} is {status_value}."),
    )


@workflow.command("autonomous-stop")
@click.option("--run-id", default="", help="Optional runtime id; defaults to the active runtime.")
@click.option("--owner-token", default="", help="Required runtime capability token for stop.")
@click.option("--reason", default="", help="Optional operator stop reason.")
@click.option("--created-by", default="human", show_default=True)
@click.option("--actor-type", default="human", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_autonomous_stop(
    run_id: str,
    owner_token: str,
    reason: str,
    created_by: str,
    actor_type: str,
    as_json: bool,
):
    """Request a graceful stop for the persistent autonomous runtime."""
    _guard_machine_mutation(as_json)
    payload = _invoke_autonomous_runtime(
        *_AUTONOMOUS_STOP_CANDIDATES,
        run_id=run_id,
        runtime_id=run_id,
        reason=reason,
        stop_reason=reason,
        owner_token=owner_token,
        token=owner_token,
        created_by=created_by,
        actor_type=actor_type,
        actor=actor_type,
    )
    resolved_run_id = str(_runtime_value(payload, "run_id") or run_id or "active")
    status_value = str(_runtime_value(payload, "status") or "stopping")
    _emit_result(
        payload,
        as_json,
        _runtime_summary(payload, f"Autonomous runtime {resolved_run_id} is now {status_value}."),
    )


@workflow.command("autonomous-resume")
@click.option("--run-id", default="", help="Optional runtime id; defaults to the active runtime.")
@click.option("--owner-token", default="", help="Required runtime capability token for resume.")
@click.option("--created-by", default="human", show_default=True)
@click.option("--actor-type", default="human", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_autonomous_resume(
    run_id: str,
    owner_token: str,
    created_by: str,
    actor_type: str,
    as_json: bool,
):
    """Resume a stopped or stale persistent autonomous runtime."""
    _guard_machine_mutation(as_json)
    payload = _invoke_autonomous_runtime(
        *_AUTONOMOUS_RESUME_CANDIDATES,
        run_id=run_id,
        runtime_id=run_id,
        owner_token=owner_token,
        token=owner_token,
        created_by=created_by,
        actor_type=actor_type,
        actor=actor_type,
        spawn_worker=False,
        launch_worker=False,
        detach=False,
    )
    _launch_autonomous_worker(payload)
    resolved_run_id = str(_runtime_value(payload, "run_id") or run_id or "active")
    status_value = str(_runtime_value(payload, "status") or "running")
    _emit_result(
        payload,
        as_json,
        _runtime_summary(payload, f"Autonomous runtime {resolved_run_id} resumed with status {status_value}."),
    )


@workflow.command("autonomous-worker", hidden=True)
@click.option("--run-id", required=True, help="Persisted autonomous runtime id.")
@click.option("--auth-file", default="", help="Protected worker auth file for the detached worker.")
@click.option("--owner-token", default="", help="Backward-compatible direct owner token.")
def workflow_autonomous_worker(run_id: str, auth_file: str, owner_token: str):
    """Internal detached worker entrypoint for the autonomous runtime."""
    auth_payload = _read_autonomous_worker_auth_file(auth_file) if auth_file else {}
    resolved_owner_token = str(auth_payload.get("owner_token") or owner_token or "")
    owner_instance_id = str(auth_payload.get("owner_instance_id") or "")
    _invoke_autonomous_runtime(
        *_AUTONOMOUS_WORKER_CANDIDATES,
        run_id=run_id,
        runtime_id=run_id,
        owner_token=resolved_owner_token,
        token=resolved_owner_token,
        owner_instance_id=owner_instance_id,
        worker_instance_id=owner_instance_id,
    )


def _prompt_csv(label: str, default: str = "") -> list[str]:
    raw = click.prompt(label, default=default, show_default=bool(default))
    return [item.strip() for item in raw.split(",") if item.strip()]


@workflow.command("onboard")
@click.option("--goal", default="", help="Current research goal.")
@click.option("--success-criteria", default="", help="How success will be judged.")
@click.option("--active-profile", default="", help="Preferred autonomous profile.")
@click.option(
    "--autonomy-level",
    type=click.Choice(["guided", "bounded", "aggressive"], case_sensitive=False),
    help="How much autonomy the tool may use inside workflows.",
)
@click.option("--allowed-action", "allowed_actions", multiple=True, help="Repeatable allowed action.")
@click.option("--constraint", "constraints", multiple=True, help="Repeatable constraint.")
@click.option("--stop-condition", "stop_conditions", multiple=True, help="Repeatable stop condition.")
@click.option("--notes", default="", help="Free-form notes from the onboarding interview.")
@click.option("--actor-type", default="human", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_onboard(
    goal: str,
    success_criteria: str,
    active_profile: str,
    autonomy_level: str | None,
    allowed_actions: tuple[str, ...],
    constraints: tuple[str, ...],
    stop_conditions: tuple[str, ...],
    notes: str,
    actor_type: str,
    as_json: bool,
):
    """Run the solo onboarding interview and persist the current research contract."""
    _guard_machine_mutation(as_json)
    if as_json:
        missing = [
            name
            for name, value in (
                ("goal", goal),
                ("success_criteria", success_criteria),
                ("active_profile", active_profile),
                ("autonomy_level", autonomy_level),
            )
            if not value
        ]
        if missing:
            raise click.ClickException(
                "JSON mode requires explicit values for: " + ", ".join(missing)
            )
    if not goal:
        goal = click.prompt("Goal")
    if not success_criteria:
        success_criteria = click.prompt("Success criteria")
    if not active_profile:
        active_profile = click.prompt("Active profile", default="goal-chaser")
    if not autonomy_level:
        autonomy_level = click.prompt("Autonomy level", default="bounded")
    if not allowed_actions:
        allowed_actions = tuple(
            _prompt_csv(
                "Allowed actions (comma separated)",
                "create experiments, launch runs, review results, save context",
            )
        )
    if not constraints:
        constraints = tuple(_prompt_csv("Constraints (comma separated)", "single-user only"))
    if not stop_conditions:
        stop_conditions = tuple(
            _prompt_csv("Stop conditions (comma separated)", "ask when unsure, stop on repeated failure")
        )
    if not notes and not as_json:
        notes = click.prompt("Notes", default="", show_default=False)

    payload = _run_command(
        onboard_workflow(
            goal=goal,
            success_criteria=success_criteria,
            active_profile=active_profile,
            autonomy_level=autonomy_level,
            allowed_actions=list(allowed_actions),
            constraints=list(constraints),
            stop_conditions=list(stop_conditions),
            notes=notes,
            actor_type=actor_type,
        )
    )
    summary = (
        f"Onboarding saved for goal '{payload['contract']['goal']}' "
        f"with profile '{payload['contract']['active_profile']}'."
    )
    _emit_result(payload, as_json, summary)


@workflow.command("onboard-show")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def workflow_onboard_show(as_json: bool):
    """Show the persisted onboarding contract, if present."""
    payload = _run_command(onboarding_summary_workflow())
    summary = payload.get("message") or (
        f"Current goal: {payload['contract']['goal']} "
        f"(profile: {payload['contract']['active_profile']})"
    )
    _emit_result(payload, as_json, summary)


@cli.command()
def init_db():
    """Initialize the PostgreSQL database schema."""
    migration_path = os.path.join(
        os.path.dirname(__file__), "db", "migrations", "001_initial.sql"
    )

    if not os.path.exists(migration_path):
        click.echo(f"Migration file not found: {migration_path}", err=True)
        raise SystemExit(1)

    config = load_config()
    click.echo(f"Database: {config.db.name} on {config.db.host}:{config.db.port}")
    click.echo()
    click.echo("Run:")
    click.echo(f"  createdb {config.db.name}")
    click.echo(f"  psql -d {config.db.name} -f {migration_path}")


@cli.group()
def ultrawork():
    """Agent-safe ultrawork profile registry and execution contracts."""


@ultrawork.group(name="profile")
def ultrawork_profile():
    """Inspect registered ultrawork profiles."""


@ultrawork_profile.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
def ultrawork_profile_list(as_json: bool):
    """List approved ultrawork profiles."""
    profiles = [profile.as_dict() for profile in list_ultrawork_profiles()]

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data={"profiles": profiles}), indent=2))
        return

    for profile in profiles:
        click.echo(profile["name"])
        click.echo(f"  Summary: {profile['summary']}")
        click.echo(f"  When:    {profile['when_to_use']}")
        click.echo("  Lanes:")
        for lane in profile["lanes"]:
            click.echo(f"    - {lane['name']}: {lane['focus']}")
        click.echo(f"  Output:  {', '.join(profile['expected_output'])}")
        click.echo()


@ultrawork.command(name="run")
@click.argument("profile_name")
@click.option("--goal", default="", help="Optional operator goal to attach to the run contract.")
@click.option("--execute", is_flag=True, help="Execute the bounded profile loop instead of only printing its contract.")
@click.option("--command", default="", help="Optional local shell command for executable profiles.")
@click.option("--experiment-id", default="", help="Optional existing experiment id.")
@click.option("--name", default="", help="Experiment name for executable profiles.")
@click.option("--hypothesis", default="")
@click.option("--dataset", default="")
@click.option("--model-type", default="")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
def ultrawork_run(
    profile_name: str,
    goal: str,
    execute: bool,
    command: str,
    experiment_id: str,
    name: str,
    hypothesis: str,
    dataset: str,
    model_type: str,
    as_json: bool,
):
    """Emit the execution contract for a named ultrawork profile."""
    if execute:
        _guard_machine_mutation(as_json)
    try:
        if execute:
            contract = _run_command(
                execute_ultrawork_profile(
                    profile_name,
                    goal=goal,
                    command=command,
                    experiment_id=experiment_id,
                    name=name,
                    hypothesis=hypothesis,
                    dataset=dataset,
                    model_type=model_type,
                )
            )
        else:
            contract = build_ultrawork_run_plan(profile_name, goal=goal)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(_json_envelope(ok=True, data=contract), indent=2))
        return

    profile = contract["profile"]
    click.echo(f"Profile: {profile['name']}")
    click.echo(f"Summary: {profile['summary']}")
    if contract["goal"]:
        click.echo(f"Goal:    {contract['goal']}")
    click.echo("Lanes:")
    for lane in profile["lanes"]:
        click.echo(f"  - {lane['name']}: {lane['focus']}")
    click.echo(f"Output:  {', '.join(profile['expected_output'])}")
    click.echo("Notes:")
    for note in contract["notes"]:
        click.echo(f"  - {note}")


if __name__ == "__main__":
    cli()
