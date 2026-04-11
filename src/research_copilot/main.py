"""CLI entry point for the Research Copilot."""

from __future__ import annotations

import os

import click

from research_copilot.config import load_config
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


if __name__ == "__main__":
    cli()
