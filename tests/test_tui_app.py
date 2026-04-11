"""Smoke tests for the terminal workflow UI state machine."""

from __future__ import annotations

from rich.console import Console

from research_copilot.tui.app import ResearchCopilotTUI
from research_copilot.tui.adapters import DashboardSnapshot


def _empty_snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        jobs=(),
        experiments=(),
        insights=(),
        papers=(),
        context_entries=(),
        experiment_status_counts={},
    )


def test_tui_navigation_commands_cycle_views():
    app = ResearchCopilotTUI(snapshot_loader=_empty_snapshot)

    assert app.current_screen == "overview"

    app.handle_command("2")
    assert app.current_screen == "jobs"

    app.handle_command("l")
    assert app.current_screen == "experiments"

    app.handle_command("h")
    assert app.current_screen == "jobs"

    app.handle_command("4")
    assert app.current_screen == "knowledge"


def test_tui_quit_command_stops_loop():
    app = ResearchCopilotTUI(snapshot_loader=_empty_snapshot)

    assert app.handle_command("q") is False


def test_empty_overview_render_shows_getting_started_guidance():
    app = ResearchCopilotTUI(snapshot_loader=_empty_snapshot)
    console = Console(record=True, width=120)

    console.print(app.render())
    rendered = console.export_text()

    assert "Getting started" in rendered
    assert "workflow onboard" in rendered
    assert "workflow triage --json" in rendered
