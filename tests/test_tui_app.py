"""Smoke tests for the terminal workflow UI state machine."""

from __future__ import annotations

from rich.console import Console

from research_copilot.tui.app import ResearchCopilotTUI
from research_copilot.tui.adapters import (
    ContextRecord,
    DashboardSnapshot,
    ExperimentRecord,
    InsightRecord,
    JobRecord,
    LinkedRecord,
    PaperRecord,
)


def _seeded_snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        jobs=(
            JobRecord(
                entity_id="run:job-1",
                job_id="job-1",
                run_id="job-1",
                name="LCDB baseline",
                status="RUNNING",
                partition="gpu",
                gpus=1,
                submitted_at="2026-04-11T13:00:00+00:00",
                started_at="2026-04-11T13:05:00+00:00",
                completed_at="",
                time_limit="04:00:00",
                log_tail="epoch=1\nval_loss=0.42",
                error_tail="(no stderr)",
            ),
        ),
        experiments=(
            ExperimentRecord(
                entity_id="experiment:exp-1",
                experiment_id="exp-1",
                name="PFN baseline",
                status="running",
                hypothesis="PFNs should stabilize on LCDB.",
                description="",
                dataset="LCDB",
                model_type="PFN",
                tags=("pfn",),
                updated_at="2026-04-11T13:10:00+00:00",
                results_summary="val_loss: 0.42",
                wandb_run_id="wandb-1",
                slurm_job_id="job-1",
            ),
        ),
        insights=(
            InsightRecord(
                entity_id="insight:ins-1",
                insight_id="ins-1",
                title="Warmup helps",
                category="finding",
                confidence="0.91",
                content="Warmup keeps loss stable.",
                created_at="2026-04-11T13:11:00+00:00",
            ),
        ),
        papers=(
            PaperRecord(
                entity_id="paper:pap-1",
                paper_id="pap-1",
                title="PFNs for Learning Curves",
                authors=("Alice", "Bob"),
                year="2024",
                relevance_notes="Relevant to the PFN baseline.",
                added_at="2026-04-11T13:12:00+00:00",
            ),
        ),
        context_entries=(
            ContextRecord(
                entity_id="context:ctx-1",
                context_id="ctx-1",
                key="next_step",
                context_type="goal",
                value="Compare against LightGBM baseline.",
                updated_at="2026-04-11T13:13:00+00:00",
            ),
        ),
        experiment_status_counts={"running": 1},
        links_by_entity={
            "run:job-1": (
                LinkedRecord(
                    entity_id="experiment:exp-1",
                    title="PFN baseline",
                    entity_type="experiment",
                    relation="linked_from:experiment_to_run",
                    status="running",
                ),
            ),
            "experiment:exp-1": (
                LinkedRecord(
                    entity_id="paper:pap-1",
                    title="PFNs for Learning Curves",
                    entity_type="paper",
                    relation="experiment_to_paper",
                    status="complete",
                ),
                LinkedRecord(
                    entity_id="insight:ins-1",
                    title="Warmup helps",
                    entity_type="insight",
                    relation="experiment_to_insight",
                    status="complete",
                ),
                LinkedRecord(
                    entity_id="context:ctx-1",
                    title="next_step",
                    entity_type="context",
                    relation="experiment_to_context",
                    status="complete",
                ),
            ),
        },
        actions_by_entity={
            "run:job-1": ("Open log summary",),
            "experiment:exp-1": ("Open linked research",),
        },
        schema_version="1.0.0",
        snapshot_owner="workflow_snapshot_service",
        snapshot_state="complete",
    )


def _render_text(renderable) -> str:
    console = Console(record=True, width=120)
    console.print(renderable)
    return console.export_text()


def test_tui_navigation_commands_cycle_views():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    assert app.current_screen == "overview"

    app.handle_command("2")
    assert app.current_screen == "runs"

    app.handle_command("]")
    assert app.current_screen == "experiments"

    app.handle_command("[")
    assert app.current_screen == "runs"

    app.handle_command("4")
    assert app.current_screen == "research"


def test_tui_quit_command_stops_loop():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    assert app.handle_command("q") is False


def test_tui_footer_uses_current_focus_label():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    footer = _render_text(app._render_footer())
    assert "focus:" in footer
    assert "overview/runs" in footer

    app.handle_command("2")
    footer = _render_text(app._render_footer())
    assert "runs/runs" in footer

    app.handle_command("4")
    footer = _render_text(app._render_footer())
    assert "research/insights" in footer


def test_tui_help_and_links_modals_toggle():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_command("?")
    assert "v1b key bindings" in _render_text(app.render())

    app.handle_command("q")
    assert "v1b key bindings" not in _render_text(app.render())

    app.handle_command("2")
    app.handle_command("g")
    assert "PFN baseline" in _render_text(app.render())


def test_tui_can_jump_from_run_to_experiment_to_linked_research():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_command("2")
    app.handle_command("enter")
    assert app.current_screen == "experiments"

    app.handle_command("p")
    assert app.current_screen == "research"
    assert app.current_pane == "papers"
    assert app._selected_paper().title == "PFNs for Learning Curves"


def test_tui_open_focused_item_on_experiment_opens_links_modal():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_command("3")
    app.handle_command("enter")

    assert app.show_links_modal is True


def test_tui_search_filter_sort_and_palette_behaviors(monkeypatch):
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_command("/")
    for key in "pfn":
        app.handle_key(key)
    app.handle_command("enter")
    assert app.search_queries["runs"] == "pfn"

    app.handle_command("3")
    app.handle_command("f")
    assert app.filter_modes["experiments"] == "running"

    app.handle_command("s")
    assert app.sort_modes["experiments"] == "name"

    app.handle_key("\x10")
    assert app.show_palette is True
    assert "Refresh snapshot" in _render_text(app.render())


def test_tui_logs_modal_loads_full_logs(monkeypatch):
    monkeypatch.setattr(
        "research_copilot.tui.app.fetch_full_entity_log",
        lambda entity_id: type(
            "FullLogRecord",
            (),
            {
                "entity_id": "run:job-1",
                "job_id": "job-1",
                "stdout": "full stdout for job-1",
                "stderr": "full stderr for job-1",
            },
        )(),
    )
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_command("2")
    app.handle_command("l")

    assert app.show_logs_modal is True
    assert "full stdout for job-1" in _render_text(app.render())


def test_tui_palette_can_execute_actions():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_key("\x10")
    app.handle_command("j")
    app.handle_command("j")
    app.handle_command("enter")

    assert app.input_mode == "search"
