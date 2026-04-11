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


def _render_text(renderable) -> str:
    console = Console(record=True, width=120)
    console.print(renderable)
    return console.export_text()


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


def test_tui_footer_uses_current_screen_when_no_selection_exists():
    app = ResearchCopilotTUI(snapshot_loader=_empty_snapshot)

    assert "focus: overview" in _render_text(app._render_footer())

    app.handle_command("2")
    assert "focus: jobs" in _render_text(app._render_footer())

    app.handle_command("3")
    assert "focus: experiments" in _render_text(app._render_footer())

    app.handle_command("4")
    assert "focus: knowledge" in _render_text(app._render_footer())
