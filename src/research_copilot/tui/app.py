"""Rich-powered v1a terminal workflow dashboard."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from research_copilot.tui.adapters import (
    ContextRecord,
    DashboardSnapshot,
    ExperimentRecord,
    InsightRecord,
    JobRecord,
    LinkedRecord,
    PaperRecord,
    RuntimeRecord,
    build_dashboard_snapshot,
    fetch_full_entity_log,
    format_timestamp,
)

SCREEN_ORDER = ("overview", "runs", "experiments", "research")
SCREEN_TITLES = {
    "overview": "Overview",
    "runs": "Runs",
    "experiments": "Experiments",
    "research": "Research",
}
PANE_ORDER = {
    "overview": ("runs", "experiments"),
    "runs": ("runs", "links"),
    "experiments": ("experiments", "links"),
    "research": ("insights", "papers", "context"),
}
COMMAND_HINT = "1-4 views • [/] cycle • Tab panes • j/k move • / search • f filter • s sort • l logs • g links • Ctrl+P palette • ? help • r refresh • q back • Q quit"
COMPACT_COMMAND_HINT = "1-4 • Tab • j/k • Enter • g • l • / • r • q/Q"
NARROW_WIDTH = 120
MEDIUM_WIDTH = 160
SHORT_HEIGHT = 32
DEFAULT_AUTO_REFRESH_INTERVAL_SECONDS = 2.0

FILTER_CYCLES = {
    "runs": ("all", "active", "completed", "failed"),
    "experiments": ("all", "running", "completed", "failed"),
    "insights": ("all", "finding", "observation"),
    "papers": ("all", "recent"),
    "context": ("all", "goal", "note"),
}

SORT_CYCLES = {
    "runs": ("recent", "name", "status"),
    "experiments": ("recent", "name", "status"),
    "insights": ("recent", "title", "confidence"),
    "papers": ("recent", "title", "year"),
    "context": ("recent", "key", "type"),
}


def _first_link_of_type(links: tuple[LinkedRecord, ...], entity_type: str) -> LinkedRecord | None:
    for link in links:
        if link.entity_type == entity_type:
            return link
    return None


@dataclass
class ResearchCopilotTUI:
    """Stateful, testable terminal dashboard."""

    snapshot_loader: Callable[[], DashboardSnapshot] = build_dashboard_snapshot
    time_source: Callable[[], float] = time.monotonic
    timestamp_source: Callable[[], str] = lambda: datetime.now().strftime("%H:%M:%S")

    def __post_init__(self) -> None:
        self.screen_index = 0
        self.pane_indexes = {screen: 0 for screen in SCREEN_ORDER}
        self.selected_job_index = 0
        self.selected_experiment_index = 0
        self.selected_insight_index = 0
        self.selected_paper_index = 0
        self.selected_context_index = 0
        self.show_help = False
        self.show_links_modal = False
        self.show_palette = False
        self.palette_index = 0
        self.show_logs_modal = False
        self.filter_modes = {pane: "all" for pane in FILTER_CYCLES}
        self.sort_modes = {pane: cycle[0] for pane, cycle in SORT_CYCLES.items()}
        self.search_queries = {pane: "" for pane in SORT_CYCLES}
        self.input_mode = ""
        self.input_buffer = ""
        self.logs_modal_title = ""
        self.logs_modal_stdout = ""
        self.logs_modal_stderr = ""
        self.viewport_width: int | None = None
        self.viewport_height: int | None = None
        self.auto_refresh_enabled = True
        self.auto_refresh_interval_seconds = DEFAULT_AUTO_REFRESH_INTERVAL_SECONDS
        self.refresh_in_progress = False
        self.last_refresh_started_at: float | None = None
        self.last_refresh_completed_at: float | None = None
        self.last_refresh_label = "never"
        self.last_refresh_error = ""
        self.snapshot = self.snapshot_loader()
        self.refresh()

    @property
    def current_screen(self) -> str:
        return SCREEN_ORDER[self.screen_index]

    @property
    def current_pane(self) -> str:
        panes = PANE_ORDER[self.current_screen]
        return panes[self.pane_indexes[self.current_screen] % len(panes)]

    def refresh(self) -> DashboardSnapshot:
        return self._perform_refresh(raise_on_error=True)

    def set_screen(self, name: str) -> None:
        self.screen_index = SCREEN_ORDER.index(name)
        self.pane_indexes[name] = min(self.pane_indexes[name], len(PANE_ORDER[name]) - 1)
        self.show_links_modal = False
        self.show_palette = False
        self.show_logs_modal = False

    def cycle_screen(self, step: int) -> None:
        self.screen_index = (self.screen_index + step) % len(SCREEN_ORDER)
        self.show_links_modal = False

    def cycle_pane(self, step: int) -> None:
        panes = PANE_ORDER[self.current_screen]
        self.pane_indexes[self.current_screen] = (self.pane_indexes[self.current_screen] + step) % len(panes)

    def move_selection(self, step: int) -> None:
        pane = self.current_pane
        if pane == "runs" and self.snapshot.jobs:
            self.selected_job_index = (self.selected_job_index + step) % len(self.snapshot.jobs)
        elif pane == "experiments" and self.snapshot.experiments:
            self.selected_experiment_index = (self.selected_experiment_index + step) % len(self.snapshot.experiments)
        elif pane == "insights" and self.snapshot.insights:
            self.selected_insight_index = (self.selected_insight_index + step) % len(self.snapshot.insights)
        elif pane == "papers" and self.snapshot.papers:
            self.selected_paper_index = (self.selected_paper_index + step) % len(self.snapshot.papers)
        elif pane == "context" and self.snapshot.context_entries:
            self.selected_context_index = (self.selected_context_index + step) % len(self.snapshot.context_entries)

    def handle_command(self, command: str) -> bool:
        return self.handle_key(command)

    def handle_key(self, key: str) -> bool:
        raw = key or ""
        if raw == "\x10":
            raw = "ctrl+p"
        if raw == "Q":
            return False
        normalized = raw.strip().lower()
        if self.input_mode == "search":
            return self._handle_search_input(raw, normalized)
        if normalized == "":
            return True
        if normalized == "q":
            if self.show_help or self.show_links_modal or self.show_palette or self.show_logs_modal:
                self.show_help = False
                self.show_links_modal = False
                self.show_palette = False
                self.palette_index = 0
                self.show_logs_modal = False
                return True
            return False
        if normalized in {"?", "help"}:
            self.show_help = not self.show_help
            self.show_links_modal = False
            self.show_palette = False
            self.show_logs_modal = False
            return True
        if normalized == "g":
            self.show_links_modal = bool(self._selected_links())
            self.show_help = False
            self.show_palette = False
            self.show_logs_modal = False
            return True
        if normalized in {"ctrl+p"}:
            self.show_palette = not self.show_palette
            self.palette_index = 0
            self.show_help = False
            self.show_links_modal = False
            self.show_logs_modal = False
            return True
        if normalized == "/":
            self.input_mode = "search"
            self.input_buffer = self.search_queries.get(self._active_list_pane(), "")
            return True
        if normalized == "f":
            self._cycle_filter()
            return True
        if normalized == "s":
            self._cycle_sort()
            return True
        if normalized == "l":
            self._open_logs_modal()
            return True
        if self.show_palette:
            return self._handle_palette_input(raw, normalized)
        if self.show_help or self.show_links_modal or self.show_logs_modal:
            return True

        if normalized in {"1", "overview"}:
            self.set_screen("overview")
        elif normalized in {"2", "runs", "jobs"}:
            self.set_screen("runs")
        elif normalized in {"3", "experiments"}:
            self.set_screen("experiments")
        elif normalized in {"4", "research", "knowledge"}:
            self.set_screen("research")
        elif normalized in {"[", "left"}:
            self.cycle_screen(-1)
        elif normalized in {"]", "right"}:
            self.cycle_screen(1)
        elif normalized == "tab":
            self.cycle_pane(1)
        elif normalized == "shift+tab":
            self.cycle_pane(-1)
        elif normalized in {"j", "down", "next"}:
            self.move_selection(1)
        elif normalized in {"k", "up", "prev"}:
            self.move_selection(-1)
        elif normalized == "a":
            self.auto_refresh_enabled = not self.auto_refresh_enabled
        elif normalized in {"r", "refresh"}:
            self.refresh()
        elif normalized in {"enter", "o"}:
            self._open_focused_item()
        elif normalized == "e":
            self._jump_to_linked("experiment")
        elif normalized == "p":
            self._jump_to_linked_research("paper")
        elif normalized == "i":
            self._jump_to_linked_research("insight")
        elif normalized == "c":
            self._jump_to_linked_research("context")
        return True

    def run(self, console: Console | None = None) -> None:
        console = console or Console()
        self.viewport_width = console.size.width
        self.viewport_height = console.size.height
        self.refresh()
        if not console.is_interactive:
            console.print(self.render_static())
            return

        with console.screen(style="black on default"):
            while True:
                self.viewport_width = console.size.width
                self.viewport_height = console.size.height
                self._maybe_auto_refresh()
                console.clear()
                console.print(self.render())
                key = self._read_key(timeout=self._next_poll_timeout())
                if not self.handle_key(key):
                    break

    def render(self) -> RenderableType:
        return Group(
            self._render_header(),
            self._render_tabs(),
            self._render_body(),
            self._render_footer(),
        )

    def render_static(self) -> RenderableType:
        return Group(
            self._render_header(),
            self._render_tabs(),
            self._render_static_body(),
            self._render_footer(),
        )

    def _render_header(self) -> RenderableType:
        runtime_summary = self._runtime_header_summary()
        if self._is_short_layout():
            summary = Text(
                "Active runs: "
                f"{self.snapshot.active_jobs} • "
                f"Tracked runs: {len(self.snapshot.jobs)} • "
                f"Experiments: {len(self.snapshot.experiments)} • "
                f"Insights: {len(self.snapshot.insights)} • "
                f"Papers: {len(self.snapshot.papers)}",
                style="bold cyan",
            )
            subtitle = f"schema {self.snapshot.schema_version} • {self.snapshot.snapshot_state}{runtime_summary}"
            return Panel(summary, title="Research Copilot", subtitle=subtitle)
        metrics = Columns(
            [
                self._metric_panel("Active runs", str(self.snapshot.active_jobs), "cyan"),
                self._metric_panel("Tracked runs", str(len(self.snapshot.jobs)), "blue"),
                self._metric_panel("Running experiments", str(self.snapshot.running_experiments), "magenta"),
                self._metric_panel("Completed experiments", str(self.snapshot.completed_experiments), "green"),
                self._metric_panel("Insights", str(len(self.snapshot.insights)), "yellow"),
                self._metric_panel("Papers", str(len(self.snapshot.papers)), "white"),
            ],
            equal=True,
            expand=True,
        )
        subtitle = (
            f"Terminal workflow dashboard • schema {self.snapshot.schema_version} • "
            f"{self.snapshot.snapshot_state}{runtime_summary}"
        )
        return Panel(metrics, title="Research Copilot", subtitle=subtitle)

    def _render_tabs(self) -> RenderableType:
        tabs = Text()
        for index, name in enumerate(SCREEN_ORDER, start=1):
            label = f" {index}:{SCREEN_TITLES[name]} "
            style = "bold black on cyan" if name == self.current_screen else "bold white on grey23"
            tabs.append(label, style=style)
            tabs.append("  ")
        return tabs

    def _render_body(self) -> RenderableType:
        if self.show_help:
            return self._render_help_modal()
        if self.show_links_modal:
            return self._render_links_modal()
        if self.show_palette:
            return self._render_palette_modal()
        if self.show_logs_modal:
            return self._render_logs_modal()
        if self.current_screen == "overview":
            return self._render_overview()
        if self.current_screen == "runs":
            return self._render_runs_screen()
        if self.current_screen == "experiments":
            return self._render_experiments_screen()
        return self._render_research_screen()

    def _render_static_body(self) -> RenderableType:
        if self.current_screen != "overview":
            return self._render_body()
        panels: list[RenderableType] = []
        if self.snapshot.has_runtime:
            panels.append(
                Panel(
                    self._render_runtime_card(compact=self._use_compact_runtime_card()),
                    title="Autonomous Runtime",
                    border_style=self._runtime_border_style(),
                )
            )
        panels.extend(
            [
            Panel(self._render_jobs_table(limit=8), title="Recent runs"),
            Panel(self._render_experiments_table(limit=8), title="Recent experiments"),
            Panel(
                self._render_overview_focus(compact=self._is_narrow_layout() or self._is_short_layout()),
                title="Selected focus" if (self.snapshot.jobs or self.snapshot.experiments) else "Getting started",
            ),
            ]
        )
        return Group(*panels)

    def _render_overview(self) -> RenderableType:
        if not self.snapshot.jobs and not self.snapshot.experiments and not self.snapshot.has_runtime:
            return Panel(self._render_getting_started(), title="Getting started")

        runs_panel = Panel(
            self._render_jobs_table(limit=4 if self._is_short_layout() else 8),
            title="Runs",
            border_style=self._pane_style("runs"),
        )
        experiments_panel = Panel(
            self._render_experiments_table(limit=4 if self._is_short_layout() else 8),
            title="Experiments",
            border_style=self._pane_style("experiments"),
        )
        focus_panel = Panel(
            self._render_overview_focus(compact=self._is_narrow_layout() or self._is_short_layout()),
            title="Selected focus" if (self.snapshot.jobs or self.snapshot.experiments) else "Getting started",
            border_style="cyan",
        )
        runtime_panel = Panel(
            self._render_runtime_card(compact=self._use_compact_runtime_card()),
            title="Autonomous Runtime",
            border_style=self._runtime_border_style(),
        )

        if self._is_short_layout():
            panels: list[RenderableType] = []
            if self.snapshot.has_runtime:
                panels.append(runtime_panel)
            panels.extend([runs_panel, experiments_panel, focus_panel])
            return Group(*panels)

        if self._is_narrow_layout():
            panels = [runs_panel, experiments_panel]
            if self.snapshot.has_runtime:
                panels.append(runtime_panel)
            panels.append(focus_panel)
            return Group(*panels)

        if self._is_medium_layout():
            layout = Layout()
            if self.snapshot.has_runtime:
                layout.split_column(Layout(name="top", ratio=2), Layout(name="bottom", ratio=1))
                layout["top"].split_row(Layout(runs_panel, ratio=2), Layout(experiments_panel, ratio=2))
                layout["bottom"].split_row(Layout(runtime_panel, ratio=2), Layout(focus_panel, ratio=3))
                return layout
            layout.split_row(Layout(runs_panel, ratio=2), Layout(experiments_panel, ratio=2), Layout(focus_panel, ratio=3))
            return layout

        if self.snapshot.has_runtime:
            layout = Layout()
            layout.split_row(
                Layout(runs_panel, ratio=2),
                Layout(experiments_panel, ratio=2),
                Layout(name="side", ratio=3),
            )
            layout["side"].split_column(Layout(runtime_panel, ratio=2), Layout(focus_panel, ratio=3))
            return layout

        layout = Layout()
        layout.split_row(Layout(runs_panel, ratio=2), Layout(experiments_panel, ratio=2), Layout(focus_panel, ratio=3))
        return layout

    def _render_runs_screen(self) -> RenderableType:
        if self._is_short_layout():
            return Group(
                Panel(
                    self._render_jobs_table(limit=8),
                    title="Runs",
                    border_style=self._pane_style("runs"),
                ),
                Panel(self._render_compact_job_detail(self._selected_job()), title="Run focus"),
            )
        if self._is_narrow_layout():
            return Group(
                Panel(
                    self._render_jobs_table(limit=20),
                    title="Runs",
                    border_style=self._pane_style("runs"),
                ),
                Panel(self._render_job_detail(self._selected_job()), title="Run detail"),
                Panel(
                    self._render_links_summary(self._selected_job_entity_id()),
                    title="Linked research",
                    border_style=self._pane_style("links"),
                ),
            )
        if self._is_medium_layout():
            layout = Layout()
            layout.split_column(Layout(name="top", ratio=2), Layout(name="bottom", ratio=1))
            layout["top"].split_row(
                Layout(
                    Panel(
                        self._render_jobs_table(limit=20),
                        title="Runs",
                        border_style=self._pane_style("runs"),
                    ),
                    ratio=2,
                ),
                Layout(Panel(self._render_job_detail(self._selected_job()), title="Run detail"), ratio=3),
            )
            layout["bottom"].update(
                Panel(
                    self._render_links_summary(self._selected_job_entity_id()),
                    title="Linked research",
                    border_style=self._pane_style("links"),
                )
            )
            return layout
        layout = Layout()
        layout.split_row(
            Layout(
                Panel(
                    self._render_jobs_table(limit=20),
                    title="Runs",
                    border_style=self._pane_style("runs"),
                ),
                ratio=2,
            ),
            Layout(Panel(self._render_job_detail(self._selected_job()), title="Run detail"), ratio=3),
            Layout(
                Panel(
                    self._render_links_summary(self._selected_job_entity_id()),
                    title="Linked research",
                    border_style=self._pane_style("links"),
                ),
                ratio=2,
            ),
        )
        return layout

    def _render_experiments_screen(self) -> RenderableType:
        if self._is_short_layout():
            return Group(
                Panel(
                    self._render_experiments_table(limit=8),
                    title="Experiments",
                    border_style=self._pane_style("experiments"),
                ),
                Panel(
                    self._render_compact_experiment_detail(self._selected_experiment()),
                    title="Experiment focus",
                ),
            )
        if self._is_narrow_layout():
            return Group(
                Panel(
                    self._render_experiments_table(limit=20),
                    title="Experiments",
                    border_style=self._pane_style("experiments"),
                ),
                Panel(
                    self._render_experiment_detail(self._selected_experiment()),
                    title="Experiment detail",
                ),
                Panel(
                    self._render_links_summary(self._selected_experiment_entity_id()),
                    title="Linked research",
                    border_style=self._pane_style("links"),
                ),
            )
        if self._is_medium_layout():
            layout = Layout()
            layout.split_column(Layout(name="top", ratio=2), Layout(name="bottom", ratio=1))
            layout["top"].split_row(
                Layout(
                    Panel(
                        self._render_experiments_table(limit=20),
                        title="Experiments",
                        border_style=self._pane_style("experiments"),
                    ),
                    ratio=2,
                ),
                Layout(
                    Panel(self._render_experiment_detail(self._selected_experiment()), title="Experiment detail"),
                    ratio=3,
                ),
            )
            layout["bottom"].update(
                Panel(
                    self._render_links_summary(self._selected_experiment_entity_id()),
                    title="Linked research",
                    border_style=self._pane_style("links"),
                )
            )
            return layout
        layout = Layout()
        layout.split_row(
            Layout(
                Panel(
                    self._render_experiments_table(limit=20),
                    title="Experiments",
                    border_style=self._pane_style("experiments"),
                ),
                ratio=2,
            ),
            Layout(Panel(self._render_experiment_detail(self._selected_experiment()), title="Experiment detail"), ratio=3),
            Layout(
                Panel(
                    self._render_links_summary(self._selected_experiment_entity_id()),
                    title="Linked research",
                    border_style=self._pane_style("links"),
                ),
                ratio=2,
            ),
        )
        return layout

    def _render_research_screen(self) -> RenderableType:
        if self._is_short_layout():
            current_pane = self.current_pane
            return Group(
                Panel(
                    Text(
                        "Tab panes to switch: Insights • Papers • Context",
                        style="bold cyan",
                    ),
                    title="Research navigation",
                    border_style="cyan",
                ),
                Panel(
                    self._render_current_research_table(limit=6),
                    title=f"Research list — {current_pane.title()}",
                    border_style=self._pane_style(current_pane),
                ),
                Panel(self._render_compact_research_detail(), title="Research focus"),
            )
        if self._is_narrow_layout():
            current_pane = self.current_pane
            return Group(
                Panel(
                    Text(
                        "Tab panes to switch: Insights • Papers • Context",
                        style="bold cyan",
                    ),
                    title="Research navigation",
                    border_style="cyan",
                ),
                Panel(
                    self._render_current_research_table(),
                    title=f"Research list — {current_pane.title()}",
                    border_style=self._pane_style(current_pane),
                ),
                Panel(self._render_selected_research_detail(), title="Research detail"),
            )
        if self._is_medium_layout():
            current_pane = self.current_pane
            layout = Layout()
            layout.split_column(Layout(name="top", ratio=2), Layout(name="bottom", ratio=1))
            layout["top"].split_row(
                Layout(
                    Panel(
                        self._render_current_research_table(),
                        title=f"Research list — {current_pane.title()}",
                        border_style=self._pane_style(current_pane),
                    ),
                    ratio=3,
                ),
                Layout(
                    Panel(
                        self._render_research_pane_overview(),
                        title="Pane guide",
                        border_style="cyan",
                    ),
                    ratio=2,
                ),
            )
            layout["bottom"].update(Panel(self._render_selected_research_detail(), title="Research detail"))
            return layout
        layout = Layout()
        layout.split_column(Layout(name="lists", ratio=2), Layout(name="detail", ratio=1))
        layout["lists"].split_row(
            Layout(
                Panel(
                    self._render_insights_table(),
                    title="Insights",
                    border_style=self._pane_style("insights"),
                ),
                ratio=2,
            ),
            Layout(
                Panel(
                    self._render_papers_table(),
                    title="Papers",
                    border_style=self._pane_style("papers"),
                ),
                ratio=2,
            ),
            Layout(
                Panel(
                    self._render_context_table(),
                    title="Context",
                    border_style=self._pane_style("context"),
                ),
                ratio=2,
            ),
        )
        layout["detail"].update(Panel(self._render_selected_research_detail(), title="Research detail"))
        return layout

    def _render_jobs_table(self, limit: int) -> RenderableType:
        jobs = self._visible_jobs()
        if not jobs:
            return Text("No runs yet. Submit or sync a run to monitor it here.", style="dim")

        table = Table(expand=True)
        table.add_column("Sel", width=3)
        table.add_column("Run", style="bold")
        table.add_column("Status")
        table.add_column("GPU", justify="right", width=5)
        table.add_column("Submitted", width=16)
        for index, job in enumerate(jobs[:limit]):
            selected = "▶" if index == self.selected_job_index else " "
            table.add_row(
                selected,
                job.name,
                Text(job.status, style=self._status_style(job.status)),
                str(job.gpus),
                format_timestamp(job.submitted_at),
            )
        return table

    def _render_experiments_table(self, limit: int) -> RenderableType:
        experiments = self._visible_experiments()
        if not experiments:
            return Text("No experiments tracked yet. Store one to populate this view.", style="dim")

        table = Table(expand=True)
        table.add_column("Sel", width=3)
        table.add_column("Experiment", style="bold")
        table.add_column("Status")
        table.add_column("Dataset")
        table.add_column("Updated", width=16)
        for index, experiment in enumerate(experiments[:limit]):
            selected = "▶" if index == self.selected_experiment_index else " "
            table.add_row(
                selected,
                experiment.name,
                Text(experiment.status, style=self._status_style(experiment.status)),
                experiment.dataset or "—",
                format_timestamp(experiment.updated_at),
            )
        return table

    def _render_job_detail(self, job: JobRecord | None) -> RenderableType:
        if job is None:
            return Text("No run selected.", style="dim")

        info = Table.grid(expand=True)
        info.add_column(style="bold cyan", width=16)
        info.add_column()
        info.add_row("Run ID", job.run_id)
        info.add_row("Job ID", job.job_id)
        info.add_row("Name", job.name)
        info.add_row("Status", job.status)
        info.add_row("Partition", job.partition)
        info.add_row("GPUs", str(job.gpus))
        info.add_row("Time limit", job.time_limit)
        info.add_row("Submitted", format_timestamp(job.submitted_at))
        info.add_row("Started", format_timestamp(job.started_at or ""))
        info.add_row("Completed", format_timestamp(job.completed_at or ""))
        return Group(
            info,
            Text("\nLog summary", style="bold"),
            Text(job.log_tail, style="white"),
            Text("\nStderr summary", style="bold"),
            Text(job.error_tail or "(no stderr)", style="dim"),
        )

    def _render_experiment_detail(self, experiment: ExperimentRecord | None) -> RenderableType:
        if experiment is None:
            return Text("No experiment selected.", style="dim")

        info = Table.grid(expand=True)
        info.add_column(style="bold magenta", width=16)
        info.add_column()
        info.add_row("Experiment ID", experiment.experiment_id)
        info.add_row("Name", experiment.name)
        info.add_row("Status", experiment.status)
        info.add_row("Dataset", experiment.dataset or "—")
        info.add_row("Model", experiment.model_type or "—")
        info.add_row("Tags", ", ".join(experiment.tags) if experiment.tags else "—")
        info.add_row("Updated", format_timestamp(experiment.updated_at))
        info.add_row("W&B run", experiment.wandb_run_id or "—")
        info.add_row("Linked job", experiment.slurm_job_id or "—")
        return Group(
            info,
            Text("\nHypothesis / notes", style="bold"),
            Text(experiment.hypothesis or experiment.description or "No experiment notes yet."),
            Text("\nResult snapshot", style="bold"),
            Text(experiment.results_summary),
        )

    def _render_insights_table(self, limit: int | None = None) -> RenderableType:
        insights = self._visible_insights()
        if not insights:
            return Text("No insights captured yet.", style="dim")
        table = Table(expand=True)
        table.add_column("Sel", width=3)
        table.add_column("Title", style="bold")
        table.add_column("Category")
        table.add_column("Confidence", justify="right")
        visible = insights if limit is None else insights[:limit]
        for index, insight in enumerate(visible):
            selected = "▶" if index == self.selected_insight_index else " "
            table.add_row(selected, insight.title, insight.category, insight.confidence)
        return table

    def _render_papers_table(self, limit: int | None = None) -> RenderableType:
        papers = self._visible_papers()
        if not papers:
            return Text("No saved papers yet.", style="dim")
        table = Table(expand=True)
        table.add_column("Sel", width=3)
        table.add_column("Title", style="bold")
        table.add_column("Authors")
        table.add_column("Year", width=6)
        visible = papers if limit is None else papers[:limit]
        for index, paper in enumerate(visible):
            selected = "▶" if index == self.selected_paper_index else " "
            authors = ", ".join(paper.authors[:2]) if paper.authors else "—"
            if len(paper.authors) > 2:
                authors += " +"
            table.add_row(selected, paper.title, authors, paper.year)
        return table

    def _render_context_table(self, limit: int | None = None) -> RenderableType:
        context_entries = self._visible_context_entries()
        if not context_entries:
            return Text("No research context stored yet.", style="dim")
        table = Table(expand=True)
        table.add_column("Sel", width=3)
        table.add_column("Key", style="bold")
        table.add_column("Type")
        table.add_column("Value")
        visible = context_entries if limit is None else context_entries[:limit]
        for index, context in enumerate(visible):
            selected = "▶" if index == self.selected_context_index else " "
            value = context.value[:48] + ("…" if len(context.value) > 48 else "")
            table.add_row(selected, context.key, context.context_type, value)
        return table

    def _render_links_summary(self, entity_id: str | None) -> RenderableType:
        if not entity_id:
            return Text("No linked entity selected.", style="dim")
        links = self.snapshot.links_by_entity.get(entity_id, ())
        if not links:
            return Text("No linked research or runs for this selection.", style="dim")
        table = Table(expand=True)
        table.add_column("Type", width=10)
        table.add_column("Relation", width=24)
        table.add_column("Target", style="bold")
        for link in links[:8]:
            table.add_row(link.entity_type, link.relation, link.title)
        actions = self.snapshot.actions_by_entity.get(entity_id, ())
        if actions:
            return Group(table, Text("\nActions: " + " • ".join(actions), style="dim"))
        return table

    def _render_selected_summary(self) -> RenderableType:
        if self.current_pane == "experiments":
            return self._render_experiment_detail(self._selected_experiment())
        return Group(
            self._render_job_detail(self._selected_job()),
            Text("\nLinked research", style="bold"),
            self._render_links_summary(self._selected_job_entity_id()),
        )

    def _render_overview_focus(self, *, compact: bool) -> RenderableType:
        if not (self.snapshot.jobs or self.snapshot.experiments):
            return self._render_getting_started()
        if compact:
            if self.current_pane == "experiments":
                return self._render_compact_experiment_detail(self._selected_experiment())
            return self._render_compact_job_detail(self._selected_job())
        return self._render_selected_summary()

    def _render_runtime_card(self, *, compact: bool) -> RenderableType:
        runtime = self._runtime_record()
        if runtime is None:
            return Text(
                "No autonomous runtime detected. Start one with workflow autonomous-run.",
                style="dim",
            )

        iteration_value = (
            f"{runtime.iteration}/{runtime.max_iterations}"
            if runtime.max_iterations is not None
            else str(runtime.iteration)
        )
        header = Text.assemble(
            (self._runtime_status_label(runtime.status), self._status_style(runtime.status)),
            (" • ", "dim"),
            (runtime.current_phase or "phase unknown", "dim"),
        )
        freshness = Text(f"Freshness: {runtime.freshness_label}", style=self._freshness_style(runtime))
        last_action = runtime.last_action or runtime.summary or "No bounded action recorded yet."
        summary_line = runtime.summary.strip()
        stop_note = runtime.stop_reason or (
            "Graceful stop requested." if runtime.stop_requested_at and runtime.status == "stopping" else ""
        )

        if compact:
            compact_lines: list[RenderableType] = [
                header,
                freshness,
                Text(
                    f"Iteration: {iteration_value} • Profile: {runtime.profile_name or '—'}",
                    style="dim",
                ),
                Text(f"Last action: {self._truncate_inline(last_action, limit=72)}"),
            ]
            if summary_line and summary_line != last_action:
                compact_lines.append(
                    Text(f"Summary: {self._truncate_inline(summary_line, limit=72)}")
                )
            if runtime.goal:
                compact_lines.append(
                    Text(f"Goal: {self._truncate_inline(runtime.goal, limit=72)}", style="dim")
                )
            if stop_note:
                compact_lines.append(
                    Text(f"Stop: {self._truncate_inline(stop_note, limit=72)}", style="bold red")
                )
            return Group(*compact_lines)

        info = Table.grid(expand=True)
        info.add_column(style="bold cyan", width=14)
        info.add_column()
        info.add_row("Status", Text(self._runtime_status_label(runtime.status), style=self._status_style(runtime.status)))
        info.add_row("Freshness", Text(runtime.freshness_label, style=self._freshness_style(runtime)))
        info.add_row("Phase", runtime.current_phase or "—")
        info.add_row("Iteration", iteration_value)
        info.add_row("Profile", runtime.profile_name or "—")
        info.add_row("Autonomy", runtime.autonomy_level or "—")
        info.add_row("Heartbeat", format_timestamp(runtime.last_heartbeat_at))
        info.add_row("Updated", format_timestamp(runtime.updated_at))
        info.add_row("Last action", self._truncate_inline(last_action, limit=48))
        if runtime.last_action_status:
            info.add_row("Action state", runtime.last_action_status)
        if runtime.last_experiment_id:
            info.add_row("Experiment", runtime.last_experiment_id)
        if runtime.consecutive_failures:
            info.add_row("Failures", str(runtime.consecutive_failures))

        details: list[RenderableType] = [info]
        if runtime.goal:
            details.extend([Text("\nGoal", style="bold"), Text(runtime.goal)])
        if stop_note:
            details.extend([Text("\nStop reason", style="bold red"), Text(stop_note, style="red")])
        return Group(*details)

    def _render_selected_research_detail(self) -> RenderableType:
        if self.current_pane == "papers":
            return self._render_paper_detail(self._selected_paper())
        if self.current_pane == "context":
            return self._render_context_detail(self._selected_context())
        return self._render_insight_detail(self._selected_insight())

    def _render_insight_detail(self, insight: InsightRecord | None) -> RenderableType:
        if insight is None:
            return Text("No insight selected.", style="dim")
        return Group(
            Text(insight.title, style="bold"),
            Text(f"Category: {insight.category} • Confidence: {insight.confidence}", style="dim"),
            Text(""),
            Text(insight.content),
            Text(""),
            self._render_links_summary(insight.entity_id),
        )

    def _render_paper_detail(self, paper: PaperRecord | None) -> RenderableType:
        if paper is None:
            return Text("No paper selected.", style="dim")
        authors = ", ".join(paper.authors) if paper.authors else "—"
        return Group(
            Text(paper.title, style="bold"),
            Text(f"Authors: {authors} • Year: {paper.year}", style="dim"),
            Text(""),
            Text(paper.relevance_notes or "No relevance notes stored."),
            Text(""),
            self._render_links_summary(paper.entity_id),
        )

    def _render_context_detail(self, context: ContextRecord | None) -> RenderableType:
        if context is None:
            return Text("No context selected.", style="dim")
        return Group(
            Text(context.key, style="bold"),
            Text(f"Type: {context.context_type}", style="dim"),
            Text(""),
            Text(context.value),
            Text(""),
            self._render_links_summary(context.entity_id),
        )

    def _render_help_modal(self) -> RenderableType:
        help_lines = Group(
            Text("v1b key bindings", style="bold"),
            Text("1-4 switch screens"),
            Text("[ / ] cycle screens"),
            Text("Tab cycle panes"),
            Text("j / k move selection"),
            Text("Enter or o open focused item"),
            Text("/ start search"),
            Text("f cycle filter"),
            Text("s cycle sort"),
            Text("l open full logs on runs/experiments"),
            Text("Ctrl+P open action palette"),
            Text("g open links modal"),
            Text("a toggle auto-refresh"),
            Text("e / p / i / c jump to linked experiment / papers / insights / context"),
            Text("r refresh"),
            Text("q close help or exit"),
            Text("Q quit"),
        )
        return Panel(help_lines, title="Help", border_style="cyan")

    def _render_links_modal(self) -> RenderableType:
        links = self._selected_links()
        if not links:
            return Panel(Text("No links available for the current selection.", style="dim"), title="Links")
        table = Table(expand=True)
        table.add_column("Type", width=10)
        table.add_column("Relation", width=24)
        table.add_column("Target", style="bold")
        table.add_column("Status", width=12)
        for link in links:
            table.add_row(link.entity_type, link.relation, link.title, link.status or "—")
        return Panel(table, title="Links modal", border_style="cyan")

    def _render_palette_modal(self) -> RenderableType:
        entries = self._palette_entries()
        table = Table(expand=True)
        table.add_column("Sel", width=3)
        table.add_column("Key", width=5)
        table.add_column("Action", style="bold")
        table.add_column("Description")
        for index, entry in enumerate(entries):
            selected = "▶" if index == self.palette_index else " "
            table.add_row(selected, entry["key"], entry["label"], entry["description"])
        return Panel(table, title="Palette", border_style="cyan")

    def _render_logs_modal(self) -> RenderableType:
        content = Group(
            Text(self.logs_modal_title, style="bold"),
            Text("\nStdout", style="bold"),
            Text(self.logs_modal_stdout or "(no stdout)"),
            Text("\nStderr", style="bold"),
            Text(self.logs_modal_stderr or "(no stderr)", style="dim"),
        )
        return Panel(content, title="Full logs", border_style="cyan")

    def _render_footer(self) -> RenderableType:
        selected = self._focus_label()
        search_hint = ""
        command_hint = COMMAND_HINT
        if self.input_mode == "search":
            search_hint = f" • search> {self.input_buffer}"
        else:
            pane = self._active_list_pane()
            filter_mode = self.filter_modes.get(pane, "all")
            sort_mode = self.sort_modes.get(pane, "recent")
            query = self.search_queries.get(pane, "")
            search_hint = f" • filter: {filter_mode} • sort: {sort_mode}"
            if query:
                search_hint += f" • query: {query}"
        if self._is_narrow_layout() or self._is_short_layout():
            command_hint = COMPACT_COMMAND_HINT
        auto_state = "on" if self.auto_refresh_enabled else "off"
        refresh_state = "error" if self.last_refresh_error else ("refreshing" if self.refresh_in_progress else "idle")
        return Panel(
            Text(
                f"{command_hint} • focus: {selected}{search_hint} • auto: {auto_state} "
                f"({self.auto_refresh_interval_seconds:.0f}s) • updated: {self.last_refresh_label} • {refresh_state}",
                style="bold cyan",
            ),
            border_style="cyan",
        )

    def _render_getting_started(self) -> RenderableType:
        return Group(
            Text("Use the solo workflow commands to seed the dashboard:", style="bold"),
            Text("  1. research-copilot workflow onboard"),
            Text("  2. research-copilot workflow triage --json"),
            Text('  3. research-copilot workflow run-experiment --command "python ..." --json'),
            Text("  4. research-copilot workflow review-results <experiment-id> --json"),
            Text("Proof script: docs/seeded-solo-cli-scenario.md", style="dim"),
        )

    def _selected_job(self) -> JobRecord | None:
        jobs = self._visible_jobs()
        return jobs[self.selected_job_index] if jobs else None

    def _selected_job_entity_id(self) -> str | None:
        job = self._selected_job()
        return job.entity_id if job else None

    def _selected_experiment(self) -> ExperimentRecord | None:
        experiments = self._visible_experiments()
        return experiments[self.selected_experiment_index] if experiments else None

    def _selected_experiment_entity_id(self) -> str | None:
        experiment = self._selected_experiment()
        return experiment.entity_id if experiment else None

    def _selected_insight(self) -> InsightRecord | None:
        insights = self._visible_insights()
        return insights[self.selected_insight_index] if insights else None

    def _selected_paper(self) -> PaperRecord | None:
        papers = self._visible_papers()
        return papers[self.selected_paper_index] if papers else None

    def _selected_context(self) -> ContextRecord | None:
        context_entries = self._visible_context_entries()
        return context_entries[self.selected_context_index] if context_entries else None

    def _selected_entity_id(self) -> str | None:
        if self.current_screen == "overview":
            return self._selected_experiment_entity_id() if self.current_pane == "experiments" else self._selected_job_entity_id()
        if self.current_screen == "experiments":
            return self._selected_experiment_entity_id()
        if self.current_screen == "research":
            if self.current_pane == "papers":
                selected = self._selected_paper()
                return selected.entity_id if selected else None
            if self.current_pane == "context":
                selected = self._selected_context()
                return selected.entity_id if selected else None
            selected = self._selected_insight()
            return selected.entity_id if selected else None
        return self._selected_job_entity_id()

    def _selected_links(self) -> tuple[LinkedRecord, ...]:
        entity_id = self._selected_entity_id()
        return self.snapshot.links_by_entity.get(entity_id, ()) if entity_id else ()

    def _active_list_pane(self) -> str:
        if self.current_screen == "overview":
            return "experiments" if self.current_pane == "experiments" else "runs"
        if self.current_pane == "links":
            return "experiments" if self.current_screen == "experiments" else "runs"
        return self.current_pane

    def _visible_jobs(self) -> tuple[JobRecord, ...]:
        jobs = list(self.snapshot.jobs)
        filter_mode = self.filter_modes["runs"]
        query = self.search_queries["runs"].lower()
        sort_mode = self.sort_modes["runs"]
        if filter_mode == "active":
            jobs = [job for job in jobs if job.status in {"RUNNING", "PENDING"}]
        elif filter_mode == "completed":
            jobs = [job for job in jobs if job.status == "COMPLETED"]
        elif filter_mode == "failed":
            jobs = [job for job in jobs if job.status in {"FAILED", "CANCELLED"}]
        if query:
            jobs = [job for job in jobs if query in f"{job.name} {job.job_id} {job.partition}".lower()]
        if sort_mode == "name":
            jobs.sort(key=lambda job: job.name.lower())
        elif sort_mode == "status":
            jobs.sort(key=lambda job: (job.status, job.name.lower()))
        else:
            jobs.sort(key=lambda job: (job.submitted_at, job.job_id), reverse=True)
        self.selected_job_index = self._bounded_index(self.selected_job_index, len(jobs))
        return tuple(jobs)

    def _visible_experiments(self) -> tuple[ExperimentRecord, ...]:
        experiments = list(self.snapshot.experiments)
        filter_mode = self.filter_modes["experiments"]
        query = self.search_queries["experiments"].lower()
        sort_mode = self.sort_modes["experiments"]
        if filter_mode == "running":
            experiments = [item for item in experiments if item.status == "running"]
        elif filter_mode == "completed":
            experiments = [item for item in experiments if item.status == "completed"]
        elif filter_mode == "failed":
            experiments = [item for item in experiments if item.status in {"failed", "cancelled"}]
        if query:
            experiments = [
                item
                for item in experiments
                if query in f"{item.name} {item.dataset} {item.model_type} {item.hypothesis}".lower()
            ]
        if sort_mode == "name":
            experiments.sort(key=lambda item: item.name.lower())
        elif sort_mode == "status":
            experiments.sort(key=lambda item: (item.status, item.name.lower()))
        else:
            experiments.sort(key=lambda item: (item.updated_at, item.experiment_id), reverse=True)
        self.selected_experiment_index = self._bounded_index(self.selected_experiment_index, len(experiments))
        return tuple(experiments)

    def _visible_insights(self) -> tuple[InsightRecord, ...]:
        insights = list(self.snapshot.insights)
        filter_mode = self.filter_modes["insights"]
        query = self.search_queries["insights"].lower()
        sort_mode = self.sort_modes["insights"]
        if filter_mode != "all":
            insights = [item for item in insights if item.category == filter_mode]
        if query:
            insights = [item for item in insights if query in f"{item.title} {item.content} {item.category}".lower()]
        if sort_mode == "title":
            insights.sort(key=lambda item: item.title.lower())
        elif sort_mode == "confidence":
            insights.sort(key=lambda item: item.confidence, reverse=True)
        else:
            insights.sort(key=lambda item: item.created_at, reverse=True)
        self.selected_insight_index = self._bounded_index(self.selected_insight_index, len(insights))
        return tuple(insights)

    def _visible_papers(self) -> tuple[PaperRecord, ...]:
        papers = list(self.snapshot.papers)
        filter_mode = self.filter_modes["papers"]
        query = self.search_queries["papers"].lower()
        sort_mode = self.sort_modes["papers"]
        if filter_mode == "recent":
            papers = sorted(papers, key=lambda item: item.added_at, reverse=True)[:5]
        if query:
            papers = [item for item in papers if query in f"{item.title} {' '.join(item.authors)} {item.year}".lower()]
        if sort_mode == "title":
            papers.sort(key=lambda item: item.title.lower())
        elif sort_mode == "year":
            papers.sort(key=lambda item: item.year, reverse=True)
        else:
            papers.sort(key=lambda item: item.added_at, reverse=True)
        self.selected_paper_index = self._bounded_index(self.selected_paper_index, len(papers))
        return tuple(papers)

    def _visible_context_entries(self) -> tuple[ContextRecord, ...]:
        context_entries = list(self.snapshot.context_entries)
        filter_mode = self.filter_modes["context"]
        query = self.search_queries["context"].lower()
        sort_mode = self.sort_modes["context"]
        if filter_mode != "all":
            context_entries = [item for item in context_entries if item.context_type == filter_mode]
        if query:
            context_entries = [item for item in context_entries if query in f"{item.key} {item.value} {item.context_type}".lower()]
        if sort_mode == "key":
            context_entries.sort(key=lambda item: item.key.lower())
        elif sort_mode == "type":
            context_entries.sort(key=lambda item: (item.context_type, item.key.lower()))
        else:
            context_entries.sort(key=lambda item: item.updated_at, reverse=True)
        self.selected_context_index = self._bounded_index(self.selected_context_index, len(context_entries))
        return tuple(context_entries)

    def _metric_panel(self, label: str, value: str, style: str) -> RenderableType:
        return Panel(Text(value, justify="center", style=f"bold {style}"), title=label)

    def _focus_label(self) -> str:
        return f"{self.current_screen}/{self.current_pane}"

    def _runtime_record(self) -> RuntimeRecord | None:
        runtime = self.snapshot.runtime
        if runtime is None or isinstance(runtime, RuntimeRecord):
            return runtime
        if not isinstance(runtime, dict):
            return None
        status = str(runtime.get("status") or "unknown")
        freshness_label = str(
            runtime.get("freshness_label")
            or ("heartbeat recorded" if runtime.get("last_heartbeat_at") else "no heartbeat")
        )
        freshness_state = str(runtime.get("freshness_state") or "unknown")
        return RuntimeRecord(
            run_id=str(runtime.get("run_id") or ""),
            status=status,
            current_phase=str(runtime.get("current_phase") or ""),
            iteration=int(runtime.get("iteration") or 0),
            max_iterations=(
                int(runtime["max_iterations"])
                if runtime.get("max_iterations") not in ("", None)
                else None
            ),
            goal=str(runtime.get("goal") or ""),
            profile_name=str(runtime.get("profile_name") or ""),
            autonomy_level=str(runtime.get("autonomy_level") or ""),
            summary=str(runtime.get("summary") or ""),
            last_action=str(runtime.get("last_action") or ""),
            last_action_status=str(runtime.get("last_action_status") or ""),
            last_experiment_id=str(runtime.get("last_experiment_id") or ""),
            started_at=str(runtime.get("started_at") or ""),
            updated_at=str(runtime.get("updated_at") or ""),
            last_heartbeat_at=str(runtime.get("last_heartbeat_at") or ""),
            lease_expires_at=str(runtime.get("lease_expires_at") or ""),
            completed_at=str(runtime.get("completed_at") or ""),
            stop_requested_at=str(runtime.get("stop_requested_at") or ""),
            stop_reason=str(runtime.get("stop_reason") or ""),
            consecutive_failures=int(runtime.get("consecutive_failures") or 0),
            freshness_label=freshness_label,
            freshness_state=freshness_state,
            is_stale=bool(runtime.get("is_stale")) or status == "stale",
            is_active=bool(runtime.get("is_active")) or status in {"running", "stopping"},
        )

    def _runtime_header_summary(self) -> str:
        runtime = self._runtime_record()
        if runtime is None:
            return ""
        return (
            f" • runtime {self._runtime_status_label(runtime.status).lower()}"
            f" • {runtime.freshness_label}"
        )

    def _runtime_status_label(self, status: str) -> str:
        return status.replace("_", " ") if status else "unknown"

    def _runtime_border_style(self) -> str:
        runtime = self._runtime_record()
        if runtime is None:
            return "grey35"
        if runtime.status in {"completed"}:
            return "green"
        if runtime.status in {"failed", "stale"} or runtime.is_stale:
            return "red"
        if runtime.status in {"running", "stopping"}:
            return "yellow"
        if runtime.status == "stopped":
            return "cyan"
        return "grey35"

    def _freshness_style(self, runtime: RuntimeRecord) -> str:
        if runtime.freshness_state == "fresh":
            return "green"
        if runtime.freshness_state == "lagging":
            return "yellow"
        if runtime.freshness_state == "stale":
            return "red"
        if runtime.freshness_state == "terminal":
            return "cyan"
        return "dim"

    def _truncate_inline(self, value: str, limit: int = 96) -> str:
        return value if len(value) <= limit else value[: limit - 1] + "…"

    def _viewport_dimensions(self) -> tuple[int, int]:
        if self.viewport_width is not None and self.viewport_height is not None:
            return self.viewport_width, self.viewport_height
        fallback = shutil.get_terminal_size((120, 40))
        return fallback.columns, fallback.lines

    def _is_narrow_layout(self) -> bool:
        width, _ = self._viewport_dimensions()
        return width < NARROW_WIDTH

    def _is_medium_layout(self) -> bool:
        width, _ = self._viewport_dimensions()
        return NARROW_WIDTH <= width < MEDIUM_WIDTH

    def _is_short_layout(self) -> bool:
        _, height = self._viewport_dimensions()
        return height < SHORT_HEIGHT

    def _use_compact_runtime_card(self) -> bool:
        width, _ = self._viewport_dimensions()
        return self._is_short_layout() or width < MEDIUM_WIDTH

    def _render_current_research_table(self, limit: int | None = None) -> RenderableType:
        pane = self.current_pane
        if pane == "papers":
            return self._render_papers_table(limit=limit)
        if pane == "context":
            return self._render_context_table(limit=limit)
        return self._render_insights_table(limit=limit)

    def _render_research_pane_overview(self) -> RenderableType:
        rows = Table.grid(expand=True)
        rows.add_row(
            Text(f"Insights: {len(self.snapshot.insights)}", style=self._pane_style("insights"))
        )
        rows.add_row(
            Text(f"Papers: {len(self.snapshot.papers)}", style=self._pane_style("papers"))
        )
        rows.add_row(
            Text(
                f"Context: {len(self.snapshot.context_entries)}",
                style=self._pane_style("context"),
            )
        )
        rows.add_row(Text("Use Tab / Shift+Tab to switch the active research pane.", style="dim"))
        return rows

    def _perform_refresh(self, *, raise_on_error: bool) -> DashboardSnapshot:
        if self.refresh_in_progress:
            return self.snapshot
        self.refresh_in_progress = True
        self.last_refresh_started_at = self.time_source()
        try:
            self.snapshot = self.snapshot_loader()
            self.selected_job_index = self._bounded_index(self.selected_job_index, len(self.snapshot.jobs))
            self.selected_experiment_index = self._bounded_index(
                self.selected_experiment_index, len(self.snapshot.experiments)
            )
            self.selected_insight_index = self._bounded_index(
                self.selected_insight_index, len(self.snapshot.insights)
            )
            self.selected_paper_index = self._bounded_index(self.selected_paper_index, len(self.snapshot.papers))
            self.selected_context_index = self._bounded_index(
                self.selected_context_index, len(self.snapshot.context_entries)
            )
            self.last_refresh_completed_at = self.time_source()
            self.last_refresh_label = self.timestamp_source()
            self.last_refresh_error = ""
            return self.snapshot
        except Exception as exc:
            self.last_refresh_error = str(exc)
            if raise_on_error:
                raise
            return self.snapshot
        finally:
            self.refresh_in_progress = False

    def _next_poll_timeout(self) -> float | None:
        if not self.auto_refresh_enabled:
            return None
        if self.last_refresh_completed_at is None:
            return 0.0
        elapsed = self.time_source() - self.last_refresh_completed_at
        return max(0.0, self.auto_refresh_interval_seconds - elapsed)

    def _maybe_auto_refresh(self) -> bool:
        if not self.auto_refresh_enabled or self.refresh_in_progress:
            return False
        if self.last_refresh_completed_at is None:
            self._perform_refresh(raise_on_error=False)
            return True
        if (self.time_source() - self.last_refresh_completed_at) < self.auto_refresh_interval_seconds:
            return False
        self._perform_refresh(raise_on_error=False)
        return True

    def _render_compact_job_detail(self, job: JobRecord | None) -> RenderableType:
        if job is None:
            return Text("No run selected.", style="dim")
        return Group(
            Text(f"{job.name}", style="bold"),
            Text(
                f"{job.status} • GPU {job.gpus} • {format_timestamp(job.submitted_at)}",
                style="dim",
            ),
            Text(self._truncate_inline(job.log_tail or "(no stdout)")),
        )

    def _render_compact_experiment_detail(self, experiment: ExperimentRecord | None) -> RenderableType:
        if experiment is None:
            return Text("No experiment selected.", style="dim")
        return Group(
            Text(experiment.name, style="bold"),
            Text(
                f"{experiment.status} • {experiment.dataset or '—'} • {experiment.model_type or '—'}",
                style="dim",
            ),
            Text(self._truncate_inline(experiment.hypothesis or experiment.description or "No notes yet.")),
            Text(self._truncate_inline(experiment.results_summary or "No result snapshot yet."), style="dim"),
        )

    def _render_compact_research_detail(self) -> RenderableType:
        if self.current_pane == "papers":
            paper = self._selected_paper()
            if paper is None:
                return Text("No paper selected.", style="dim")
            authors = ", ".join(paper.authors[:2]) if paper.authors else "—"
            return Group(
                Text(paper.title, style="bold"),
                Text(f"{authors} • {paper.year}", style="dim"),
                Text(self._truncate_inline(paper.relevance_notes or "No relevance notes stored.")),
            )
        if self.current_pane == "context":
            context = self._selected_context()
            if context is None:
                return Text("No context selected.", style="dim")
            return Group(
                Text(context.key, style="bold"),
                Text(f"{context.context_type}", style="dim"),
                Text(self._truncate_inline(context.value)),
            )
        insight = self._selected_insight()
        if insight is None:
            return Text("No insight selected.", style="dim")
        return Group(
            Text(insight.title, style="bold"),
            Text(f"{insight.category} • {insight.confidence}", style="dim"),
            Text(self._truncate_inline(insight.content)),
        )

    def _pane_style(self, pane: str) -> str:
        return "cyan" if self.current_pane == pane else "grey35"

    def _status_style(self, value: str) -> str:
        lowered = value.lower()
        if lowered in {"running", "pending", "planned", "queued", "stopping"}:
            return "bold yellow"
        if lowered in {"completed", "succeeded"}:
            return "bold green"
        if lowered in {"failed", "cancelled", "stale", "blocked"}:
            return "bold red"
        if lowered in {"stopped"}:
            return "bold cyan"
        return "bold white"

    def _cycle_filter(self) -> None:
        pane = self._active_list_pane()
        cycle = FILTER_CYCLES.get(pane)
        if not cycle:
            return
        current = self.filter_modes[pane]
        index = (cycle.index(current) + 1) % len(cycle)
        self.filter_modes[pane] = cycle[index]

    def _cycle_sort(self) -> None:
        pane = self._active_list_pane()
        cycle = SORT_CYCLES.get(pane)
        if not cycle:
            return
        current = self.sort_modes[pane]
        index = (cycle.index(current) + 1) % len(cycle)
        self.sort_modes[pane] = cycle[index]

    def _handle_search_input(self, raw: str, normalized: str) -> bool:
        if raw in {"\r", "\n"} or normalized == "enter":
            self.search_queries[self._active_list_pane()] = self.input_buffer.strip()
            self.input_mode = ""
            return True
        if raw in {"\x08", "\x7f"} or normalized == "backspace":
            self.input_buffer = self.input_buffer[:-1]
            return True
        if normalized == "escape" or raw == "\x1b":
            self.input_mode = ""
            self.input_buffer = ""
            return True
        if len(raw) == 1 and raw.isprintable():
            self.input_buffer += raw
            return True
        return True

    def _handle_palette_input(self, raw: str, normalized: str) -> bool:
        command = normalized
        if command in {entry["key"] for entry in self._palette_entries()}:
            self.show_palette = False
            return self.handle_key(command)
        if command in {"j", "down", "next", "tab"}:
            self._move_palette_selection(1)
            return True
        if command in {"k", "up", "prev", "shift+tab"}:
            self._move_palette_selection(-1)
            return True
        if raw in {"\r", "\n"} or normalized in {"enter", "o"}:
            self._run_palette_action()
            return True
        return True

    def _open_logs_modal(self) -> None:
        entity_id = self._selected_log_entity_id()
        if entity_id is None:
            return
        log_record = fetch_full_entity_log(entity_id)
        if entity_id.startswith("experiment:"):
            experiment = self._selected_experiment()
            title = experiment.name if experiment is not None else entity_id
        else:
            display_job = self._selected_job()
            for candidate in self.snapshot.jobs:
                if display_job is None and candidate.job_id == log_record.job_id:
                    display_job = candidate
                    break
            title = display_job.name if display_job is not None else entity_id
        self.logs_modal_title = f"{title} ({log_record.job_id})"
        self.logs_modal_stdout = log_record.stdout
        self.logs_modal_stderr = log_record.stderr
        self.show_logs_modal = True
        self.show_help = False
        self.show_links_modal = False
        self.show_palette = False

    def _open_focused_item(self) -> None:
        if self.current_screen == "overview":
            self.set_screen("experiments" if self.current_pane == "experiments" else "runs")
            return
        if self.current_screen == "runs":
            linked_experiment = _first_link_of_type(self._selected_links(), "experiment")
            if linked_experiment is not None:
                self.set_screen("experiments")
                self._select_experiment_entity(linked_experiment.entity_id)
                return
        if self._selected_links():
            self.show_links_modal = True

    def _palette_entries(self) -> list[dict[str, str]]:
        pane = self._active_list_pane()
        links = self._selected_links()
        entries: list[dict[str, str]] = []
        if self._can_open_focused_item():
            entries.append(
                {
                    "key": "o",
                    "action": "open_focused",
                    "label": "Open focused item",
                    "description": "Use the focused item's primary read-only action",
                }
            )
        if self._can_open_logs():
            entries.append({"key": "l", "action": "open_logs", "label": "Open full logs", "description": "Fetch full logs for the selected entity"})
        if links:
            entries.append({"key": "g", "action": "toggle_links", "label": "Open links modal", "description": "Inspect linked entities"})
        if _first_link_of_type(links, "experiment") is not None:
            entries.append({"key": "e", "action": "jump_experiment", "label": "Jump to linked experiment", "description": "Focus the linked experiment"})
        if _first_link_of_type(links, "paper") is not None:
            entries.append({"key": "p", "action": "jump_paper", "label": "Jump to linked paper", "description": "Focus the linked paper"})
        if _first_link_of_type(links, "insight") is not None:
            entries.append({"key": "i", "action": "jump_insight", "label": "Jump to linked insight", "description": "Focus the linked insight"})
        if _first_link_of_type(links, "context") is not None:
            entries.append({"key": "c", "action": "jump_context", "label": "Jump to linked context", "description": "Focus the linked context"})
        entries.extend(
            [
                {"key": "r", "action": "refresh", "label": "Refresh snapshot", "description": "Reload the current snapshot"},
                {"key": "/", "action": "start_search", "label": "Search current pane", "description": "Type a query for the active pane"},
                {
                    "key": "f",
                    "action": "cycle_filter",
                    "label": f"Cycle filter ({self.filter_modes.get(pane, 'all')})",
                    "description": "Rotate the active filter mode",
                },
                {
                    "key": "s",
                    "action": "cycle_sort",
                    "label": f"Cycle sort ({self.sort_modes.get(pane, 'recent')})",
                    "description": "Rotate the active sort mode",
                },
            ]
        )
        return entries

    def _can_open_focused_item(self) -> bool:
        if self.current_screen == "overview":
            return True
        if self.current_screen == "runs":
            return _first_link_of_type(self._selected_links(), "experiment") is not None
        return bool(self._selected_links())

    def _can_open_logs(self) -> bool:
        return self._selected_log_entity_id() is not None

    def _selected_log_entity_id(self) -> str | None:
        entity_id = self._selected_entity_id()
        if entity_id is None:
            return None
        if entity_id.startswith("run:"):
            return entity_id if self._selected_job() is not None else None
        if entity_id.startswith("experiment:"):
            experiment = self._selected_experiment()
            return entity_id if experiment is not None and bool(experiment.slurm_job_id) else None
        return None

    def _move_palette_selection(self, step: int) -> None:
        entries = self._palette_entries()
        if not entries:
            self.palette_index = 0
            return
        self.palette_index = (self.palette_index + step) % len(entries)

    def _run_palette_action(self) -> None:
        entries = self._palette_entries()
        if not entries:
            return
        action = entries[self.palette_index]["action"]
        if action == "refresh":
            self.refresh()
        elif action == "toggle_links":
            self.show_links_modal = bool(self._selected_links())
        elif action == "start_search":
            self.input_mode = "search"
            self.input_buffer = self.search_queries.get(self._active_list_pane(), "")
        elif action == "cycle_filter":
            self._cycle_filter()
        elif action == "cycle_sort":
            self._cycle_sort()
        elif action == "open_logs":
            self._open_logs_modal()
        elif action == "open_focused":
            self._open_focused_item()
        elif action == "jump_experiment":
            self._jump_to_linked("experiment")
        elif action == "jump_paper":
            self._jump_to_linked_research("paper")
        elif action == "jump_insight":
            self._jump_to_linked_research("insight")
        elif action == "jump_context":
            self._jump_to_linked_research("context")
        self.show_palette = False

    def _jump_to_linked(self, entity_type: str) -> None:
        linked = _first_link_of_type(self._selected_links(), entity_type)
        if linked is None:
            return
        if entity_type == "experiment":
            self.set_screen("experiments")
            self._select_experiment_entity(linked.entity_id)

    def _jump_to_linked_research(self, entity_type: str) -> None:
        linked = _first_link_of_type(self._selected_links(), entity_type)
        if linked is None:
            return
        self.set_screen("research")
        if entity_type == "paper":
            self.pane_indexes["research"] = PANE_ORDER["research"].index("papers")
            self._select_paper_entity(linked.entity_id)
        elif entity_type == "context":
            self.pane_indexes["research"] = PANE_ORDER["research"].index("context")
            self._select_context_entity(linked.entity_id)
        else:
            self.pane_indexes["research"] = PANE_ORDER["research"].index("insights")
            self._select_insight_entity(linked.entity_id)

    def _select_experiment_entity(self, entity_id: str) -> None:
        for index, experiment in enumerate(self.snapshot.experiments):
            if experiment.entity_id == entity_id:
                self.selected_experiment_index = index
                return

    def _select_insight_entity(self, entity_id: str) -> None:
        for index, insight in enumerate(self.snapshot.insights):
            if insight.entity_id == entity_id:
                self.selected_insight_index = index
                return

    def _select_paper_entity(self, entity_id: str) -> None:
        for index, paper in enumerate(self.snapshot.papers):
            if paper.entity_id == entity_id:
                self.selected_paper_index = index
                return

    def _select_context_entity(self, entity_id: str) -> None:
        for index, context in enumerate(self.snapshot.context_entries):
            if context.entity_id == entity_id:
                self.selected_context_index = index
                return

    def _bounded_index(self, value: int, length: int) -> int:
        return min(value, length - 1) if length else 0

    def _read_key(self, timeout: float | None = None) -> str:
        try:
            import msvcrt

            if timeout is not None:
                deadline = self.time_source() + timeout
                while not msvcrt.kbhit():
                    if self.time_source() >= deadline:
                        return ""
                    time.sleep(0.05)
            first = msvcrt.getwch()
            if first in {"\x00", "\xe0"}:
                second = msvcrt.getwch()
                return {
                    "H": "up",
                    "P": "down",
                    "K": "left",
                    "M": "right",
                    "\x0f": "shift+tab",
                }.get(second, "")
            if first == "\r":
                return "enter"
            if first == "\t":
                return "tab"
            return first
        except ImportError:
            import select
            import sys
            import termios
            import tty

            stream = sys.stdin.fileno()
            original = termios.tcgetattr(stream)
            try:
                tty.setraw(stream)
                if timeout is not None:
                    ready, _, _ = select.select([sys.stdin], [], [], timeout)
                    if not ready:
                        return ""
                first = sys.stdin.read(1)
                if first == "\x1b":
                    ready, _, _ = select.select([sys.stdin], [], [], 0.01)
                    if ready:
                        second = sys.stdin.read(1)
                        if second == "[":
                            third = sys.stdin.read(1)
                            return {
                                "A": "up",
                                "B": "down",
                                "C": "right",
                                "D": "left",
                                "Z": "shift+tab",
                            }.get(third, "")
                    return "escape"
                if first == "\r":
                    return "enter"
                if first == "\t":
                    return "tab"
                return first
            finally:
                termios.tcsetattr(stream, termios.TCSADRAIN, original)


def launch_tui(console: Console | None = None) -> ResearchCopilotTUI:
    """Create and run the terminal dashboard."""

    app = ResearchCopilotTUI()
    app.run(console=console)
    return app
