"""CLI entry point for the Research Copilot."""

from __future__ import annotations

import json
import os

import click

from research_copilot.config import load_config
from research_copilot.services.ultrawork import (
    build_ultrawork_run_plan,
    list_ultrawork_profiles,
)
from research_copilot.tui import launch_tui
from research_copilot.tui.adapters import build_dashboard_snapshot


@click.group(invoke_without_command=True)
@click.version_option(version="0.1.0")
@click.pass_context
def cli(ctx: click.Context):
    """Research Copilot — terminal workflow dashboard for ML research labs."""
    if ctx.invoked_subcommand is None:
        launch_tui()


@cli.command()
def tui():
    """Open the full-screen terminal workflow dashboard."""
    launch_tui()


@cli.command()
def status():
    """Show the current configuration and connection status."""
    config = load_config()
    snapshot = build_dashboard_snapshot()

    click.echo("Research Copilot Configuration")
    click.echo("=" * 40)
    click.echo(f"Model:        {config.model}")
    click.echo(f"Budget:       ${config.max_budget_usd:.2f}")
    click.echo(f"API key set:  {'Yes' if os.environ.get('ANTHROPIC_API_KEY') else 'No'}")
    click.echo()

    click.echo("Integrations:")
    click.echo(f"  W&B:              {'Configured' if config.wandb.api_key else 'Not configured'}")
    click.echo(f"  Slurm:            {'Configured' if config.slurm.host else 'Mock mode'}")
    click.echo(f"  Semantic Scholar:  {'API key set' if config.literature.semantic_scholar_api_key else 'Public (rate limited)'}")
    click.echo("  arXiv:             Available")
    click.echo()
    click.echo("Workflow Snapshot:")
    click.echo(f"  Active jobs:       {snapshot.active_jobs}")
    click.echo(f"  Tracked jobs:      {len(snapshot.jobs)}")
    click.echo(f"  Experiments:       {len(snapshot.experiments)}")
    click.echo(f"  Saved papers:      {len(snapshot.papers)}")
    click.echo(f"  Stored insights:   {len(snapshot.insights)}")
    click.echo()
    click.echo("Run 'research-copilot' or 'research-copilot tui' to open the terminal dashboard.")


def _run_async(coro):
    return asyncio.run(coro)


def _emit_result(payload: dict, as_json: bool, summary: str | None = None) -> None:
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    if summary:
        click.echo(summary)
    else:
        click.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


@cli.group()
def workflow():
    """Run named research workflow commands."""


@workflow.command()
@click.option("--limit", default=5, show_default=True, type=click.IntRange(1, 20))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def triage(limit: int, as_json: bool):
    """Inspect current lab state and suggest the next workflow."""

    payload = _run_async(triage_workflow(max_items=limit))
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

    payload = _run_async(
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

    payload = _run_async(monitor_run_workflow(identifier=identifier, kind=kind, lines=lines))
    experiment = payload.get("experiment") or {}
    job = payload["job"]
    summary = (
        f"Job {job['job_id']} is {job['status']}"
        + (f" for experiment {experiment.get('name')}" if experiment else "")
    )
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

    payload = _run_async(
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
        f"Reviewed experiment {payload['experiment']['name']} "
        f"with result keys: {', '.join(payload['result_keys']) or 'none'}"
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

    payload = _run_async(
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
    summary = f"Found {payload['papers_total']} paper(s) for '{query}'."
    _emit_result(payload, as_json, summary)


@cli.command()
def init_db():
    """Initialize the PostgreSQL database schema."""
    import os

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
        click.echo(json.dumps({"profiles": profiles}, indent=2))
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
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
def ultrawork_run(profile_name: str, goal: str, as_json: bool):
    """Emit the execution contract for a named ultrawork profile."""
    try:
        contract = build_ultrawork_run_plan(profile_name, goal=goal)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(contract, indent=2))
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
