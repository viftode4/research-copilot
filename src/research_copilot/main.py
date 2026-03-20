"""CLI entry point for the Research Copilot."""

from __future__ import annotations

import click

from research_copilot.config import load_config


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Research Copilot — AI-powered research assistant for ML research labs."""
    pass


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind to")
@click.option("--port", default=8000, type=int, help="Port to serve on")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def serve(host: str, port: int, reload: bool):
    """Start the web server (default mode)."""
    import uvicorn

    click.echo(f"Starting Research Copilot on http://{host}:{port}")
    click.echo("Press Ctrl+C to stop.\n")
    uvicorn.run(
        "research_copilot.web.app:app",
        host=host,
        port=port,
        reload=reload,
    )


@cli.command()
def status():
    """Show the current configuration and connection status."""
    import os

    config = load_config()

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
    click.echo(f"  arXiv:             Available")
    click.echo()
    click.echo("Run 'research-copilot serve' to start the web interface.")


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
