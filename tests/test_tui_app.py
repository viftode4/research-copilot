"""Smoke tests for the terminal workflow UI state machine."""

from __future__ import annotations

from dataclasses import replace

import pytest
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
            "run:job-1": ("Open links", "Open full logs"),
            "experiment:exp-1": ("Open linked research", "Open full logs"),
        },
        schema_version="1.0.0",
        snapshot_owner="workflow_snapshot_service",
        snapshot_state="complete",
    )


def _runtime_field_name() -> str | None:
    for candidate in ("runtime", "runtime_status", "autonomous_runtime"):
        if candidate in DashboardSnapshot.__dataclass_fields__:
            return candidate
    return None


def _render_text(renderable) -> str:
    console = Console(record=True, width=120)
    console.print(renderable)
    return console.export_text()


def _render_text_with_width(renderable, width: int) -> str:
    console = Console(record=True, width=width)
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
                "entity_id": entity_id,
                "job_id": entity_id.removeprefix("run:"),
                "stdout": "full stdout for " + entity_id.removeprefix("run:"),
                "stderr": "full stderr for " + entity_id.removeprefix("run:"),
            },
        )(),
    )
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_command("2")
    app.handle_command("l")

    assert app.show_logs_modal is True
    assert "full stdout for job-1" in _render_text(app.render())


def test_tui_logs_modal_supports_scroll_paging(monkeypatch):
    stdout = "\n".join(f"stdout line {index}" for index in range(40))
    stderr = "\n".join(f"stderr line {index}" for index in range(20))

    monkeypatch.setattr(
        "research_copilot.tui.app.fetch_full_entity_log",
        lambda entity_id: type(
            "FullLogRecord",
            (),
            {
                "entity_id": entity_id,
                "job_id": entity_id.removeprefix("run:"),
                "stdout": stdout,
                "stderr": stderr,
            },
        )(),
    )
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.viewport_width = 100
    app.viewport_height = 20

    app.handle_command("2")
    app.handle_command("l")
    initial = _render_text(app.render())
    app.handle_key("\x04")
    scrolled = _render_text(app.render())

    assert "ctrl+u/d scroll" in initial
    assert app.scroll_offsets["logs_modal"] > 0
    assert initial != scrolled


def test_tui_palette_executes_read_only_action(monkeypatch):
    seen: list[str] = []

    def fake_fetch(entity_id: str):
        seen.append(entity_id)
        return type(
            "FullLogRecord",
            (),
            {
                "entity_id": entity_id,
                "job_id": "job-1",
                "stdout": f"full stdout for {entity_id}",
                "stderr": f"full stderr for {entity_id}",
            },
        )()

    monkeypatch.setattr("research_copilot.tui.app.fetch_full_entity_log", fake_fetch)
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_command("3")
    app.handle_key("\x10")
    assert "Open full logs" in _render_text(app.render())

    app.handle_command("j")
    app.handle_command("enter")

    assert seen == ["experiment:exp-1"]
    assert app.show_logs_modal is True
    assert "full stdout for experiment:exp-1" in _render_text(app.render())


def test_tui_palette_can_execute_direct_shortcuts():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    app.handle_command("3")
    app.handle_key("\x10")
    app.handle_command("p")

    assert app.show_palette is False
    assert app.current_screen == "research"
    assert app.current_pane == "papers"


def test_runs_screen_stacks_panels_in_narrow_viewport():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.viewport_width = 100
    app.viewport_height = 28
    app.handle_command("2")

    rendered = _render_text(app.render())

    assert "Runs" in rendered
    assert "Run focus" in rendered


def test_experiments_screen_stacks_panels_in_narrow_viewport():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.viewport_width = 100
    app.viewport_height = 28
    app.handle_command("3")

    rendered = _render_text(app.render())

    assert "Experiments" in rendered
    assert "Experiment focus" in rendered


def test_research_screen_uses_single_active_list_in_narrow_viewport():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.viewport_width = 100
    app.viewport_height = 28
    app.handle_command("4")

    rendered = _render_text(app.render())
    assert "Research navigation" in rendered
    assert "Research list — Insights" in rendered
    assert "Research focus" in rendered

    app.handle_command("tab")
    rendered = _render_text(app.render())
    assert "Research list — Papers" in rendered


def test_footer_switches_to_compact_hint_in_narrow_or_short_viewport():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.viewport_width = 100
    app.viewport_height = 28

    footer = _render_text(app._render_footer())

    assert "1-4 • Tab • j/k • Ctrl+U/D • Enter" in footer


def test_runs_screen_fits_short_viewport_with_compact_header_and_body():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.viewport_width = 100
    app.viewport_height = 28
    app.handle_command("2")

    rendered = _render_text_with_width(app.render(), width=100)

    assert len(rendered.splitlines()) <= 28
    assert "Run focus" in rendered


def test_experiments_screen_fits_short_viewport_with_compact_header_and_body():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.viewport_width = 100
    app.viewport_height = 28
    app.handle_command("3")

    rendered = _render_text_with_width(app.render(), width=100)

    assert len(rendered.splitlines()) <= 28
    assert "Experiment focus" in rendered


def test_research_screen_fits_short_viewport_with_compact_header_and_body():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.viewport_width = 100
    app.viewport_height = 28
    app.handle_command("4")

    rendered = _render_text_with_width(app.render(), width=100)

    assert len(rendered.splitlines()) <= 28
    assert "Research focus" in rendered


def test_auto_refresh_runs_when_interval_elapses():
    calls = {"count": 0}
    current_time = {"value": 0.0}

    def loader() -> DashboardSnapshot:
        calls["count"] += 1
        return _seeded_snapshot()

    app = ResearchCopilotTUI(snapshot_loader=loader, time_source=lambda: current_time["value"], timestamp_source=lambda: "12:00:00")
    assert calls["count"] == 2  # initial seed + first refresh in __post_init__

    current_time["value"] = 1.0
    assert app._maybe_auto_refresh() is False
    assert calls["count"] == 2

    current_time["value"] = 2.1
    assert app._maybe_auto_refresh() is True
    assert calls["count"] == 3
    assert app.last_refresh_label == "12:00:00"


def test_auto_refresh_skips_when_refresh_in_progress():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.refresh_in_progress = True
    app.auto_refresh_enabled = True
    app.last_refresh_completed_at = 0.0
    app.time_source = lambda: 5.0

    assert app._maybe_auto_refresh() is False


def test_auto_refresh_failure_keeps_previous_snapshot_and_sets_error():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot, timestamp_source=lambda: "12:00:00")
    previous = app.snapshot
    app.snapshot_loader = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    app.last_refresh_completed_at = 0.0
    app.time_source = lambda: 5.0

    assert app._maybe_auto_refresh() is True
    assert app.snapshot is previous
    assert app.last_refresh_error == "boom"


def test_manual_refresh_clears_error_and_updates_timestamp():
    current_time = {"value": 0.0}
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot, time_source=lambda: current_time["value"], timestamp_source=lambda: "12:34:56")
    app.last_refresh_error = "boom"
    current_time["value"] = 10.0

    app.refresh()

    assert app.last_refresh_error == ""
    assert app.last_refresh_label == "12:34:56"


def test_toggle_auto_refresh_shortcut():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)

    assert app.auto_refresh_enabled is True
    app.handle_command("a")
    assert app.auto_refresh_enabled is False
    app.handle_command("a")
    assert app.auto_refresh_enabled is True


def test_overview_renders_runtime_panel_when_runtime_lane_is_available():
    runtime_field = _runtime_field_name()
    if runtime_field is None:
        pytest.skip("Lane 3 runtime snapshot field is not available in this checkout yet.")
    if not any("runtime" in name for name in dir(ResearchCopilotTUI)):
        pytest.skip("Lane 3 runtime render hooks are not available in this checkout yet.")

    snapshot = _seeded_snapshot()
    snapshot = replace(
        snapshot,
        **{
            runtime_field: {
                "source": "codex",
                "status": "running",
                "iteration": 2,
                "last_action": "review-results",
                "last_heartbeat_at": "2026-04-13T01:05:00+00:00",
            }
        },
    )
    app = ResearchCopilotTUI(snapshot_loader=lambda: snapshot)

    rendered = _render_text(app.render())

    assert "Live Codex Runtime" in rendered
    assert "running" in rendered.lower()
    assert "review-results" in rendered


def test_runtime_card_shows_summary_separately_from_last_action():
    runtime_field = _runtime_field_name()
    if runtime_field is None:
        pytest.skip("Runtime snapshot field is not available in this checkout yet.")

    snapshot = _seeded_snapshot()
    snapshot = replace(
        snapshot,
        **{
            runtime_field: {
                "source": "codex",
                "status": "running",
                "iteration": 2,
                "current_phase": "thinking",
                "last_action": "review-results",
                "summary": "First monitored Codex turn.",
                "goal": "Live monitor validation",
                "operator_mode": "steerable",
                "pending_nudge_count": 2,
                "last_heartbeat_at": "2026-04-13T01:05:00+00:00",
            }
        },
    )
    app = ResearchCopilotTUI(snapshot_loader=lambda: snapshot)

    rendered = _render_text(app.render())

    assert "Last action: review-results" in rendered
    assert "Summary: First monitored Codex turn." in rendered
    assert "Goal: Live monitor validation" in rendered
    assert "Pending nudges: 2" in rendered
    assert "Operator: steerable" in rendered


def test_research_detail_supports_scroll_paging():
    snapshot = _seeded_snapshot()
    long_content = "\n".join(f"detail line {index}" for index in range(60))
    snapshot = replace(
        snapshot,
        insights=(
            InsightRecord(
                entity_id="insight:ins-1",
                insight_id="ins-1",
                title="Long insight",
                category="finding",
                confidence="0.91",
                content=long_content,
                created_at="2026-04-11T13:11:00+00:00",
            ),
        ),
    )
    app = ResearchCopilotTUI(snapshot_loader=lambda: snapshot)
    app.viewport_width = 100
    app.viewport_height = 40
    app.handle_command("4")
    initial = _render_text(app.render())
    app.handle_key("\x04")
    scrolled = _render_text(app.render())

    assert "ctrl+u/d scroll" in initial
    assert app.scroll_offsets["research_detail"] > 0
    assert initial != scrolled


def test_runtime_empty_state_mentions_codex_attach_and_autonomous_run():
    app = ResearchCopilotTUI(snapshot_loader=_seeded_snapshot)
    app.snapshot = replace(app.snapshot, runtime=None)

    rendered = _render_text(app._render_runtime_card(compact=True))

    assert "No live runtime detected." in rendered
    assert "runtime codex-attach" in rendered
    assert "workflow autonomous-run" in rendered
