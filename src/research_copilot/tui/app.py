"""Rich-powered terminal workflow dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from research_copilot.tui.adapters import (
    DashboardSnapshot,
    ExperimentRecord,
    JobRecord,
    build_dashboard_snapshot,
    format_timestamp,
)

SCREEN_ORDER = ("overview", "jobs", "experiments", "knowledge")
SCREEN_TITLES = {
    "overview": "Overview",
    "jobs": "Jobs / Logs",
    "experiments": "Experiments",
    "knowledge": "Knowledge",
}
COMMAND_HINT = "1-4 switch views • h/l cycle tabs • j/k move • r refresh • q quit"


@dataclass
class ResearchCopilotTUI:
    """Stateful, testable terminal dashboard."""

    snapshot_loader: Callable[[], DashboardSnapshot] = build_dashboard_snapshot

    def __post_init__(self) -> None:
        self.screen_index = 0
        self.selected_job_index = 0
        self.selected_experiment_index = 0
        self.snapshot = self.snapshot_loader()
        self.refresh()

    @property
    def current_screen(self) -> str:
        return SCREEN_ORDER[self.screen_index]

    def refresh(self) -> DashboardSnapshot:
        self.snapshot = self.snapshot_loader()
        if self.snapshot.jobs:
            self.selected_job_index = min(self.selected_job_index, len(self.snapshot.jobs) - 1)
        else:
            self.selected_job_index = 0
        if self.snapshot.experiments:
            self.selected_experiment_index = min(
                self.selected_experiment_index, len(self.snapshot.experiments) - 1
            )
        else:
            self.selected_experiment_index = 0
        return self.snapshot

    def set_screen(self, name: str) -> None:
        self.screen_index = SCREEN_ORDER.index(name)

    def cycle_screen(self, step: int) -> None:
        self.screen_index = (self.screen_index + step) % len(SCREEN_ORDER)

    def move_selection(self, step: int) -> None:
        if self.current_screen == "jobs" and self.snapshot.jobs:
            self.selected_job_index = (self.selected_job_index + step) % len(self.snapshot.jobs)
        elif self.current_screen == "experiments" and self.snapshot.experiments:
            self.selected_experiment_index = (
                self.selected_experiment_index + step
            ) % len(self.snapshot.experiments)

    def handle_command(self, command: str) -> bool:
        normalized = (command or "").strip().lower()
        if normalized in {"q", "quit", "exit"}:
            return False
        if normalized in {"1", "overview"}:
            self.set_screen("overview")
        elif normalized in {"2", "jobs"}:
            self.set_screen("jobs")
        elif normalized in {"3", "experiments"}:
            self.set_screen("experiments")
        elif normalized in {"4", "knowledge"}:
            self.set_screen("knowledge")
        elif normalized in {"h", "left"}:
            self.cycle_screen(-1)
        elif normalized in {"l", "right"}:
            self.cycle_screen(1)
        elif normalized in {"j", "down", "next"}:
            self.move_selection(1)
        elif normalized in {"k", "up", "prev"}:
            self.move_selection(-1)
        elif normalized in {"r", "refresh"}:
            self.refresh()
        return True

    def run(self, console: Console | None = None) -> None:
        console = console or Console()
        self.refresh()
        if not console.is_interactive:
            console.print(self.render())
            return

        with console.screen(style="black on default"):
            while True:
                console.clear()
                console.print(self.render())
                if not self.handle_command(console.input("\n[bold cyan]Command[/] > ")):
                    break

    def render(self) -> RenderableType:
        return Group(
            self._render_header(),
            self._render_tabs(),
            self._render_body(),
            self._render_footer(),
        )

    def _render_header(self) -> RenderableType:
        metrics = Columns(
            [
                self._metric_panel("Active jobs", str(self.snapshot.active_jobs), "cyan"),
                self._metric_panel("Total jobs", str(len(self.snapshot.jobs)), "blue"),
                self._metric_panel(
                    "Running experiments", str(self.snapshot.running_experiments), "magenta"
                ),
                self._metric_panel(
                    "Completed experiments", str(self.snapshot.completed_experiments), "green"
                ),
                self._metric_panel("Insights", str(len(self.snapshot.insights)), "yellow"),
                self._metric_panel("Papers", str(len(self.snapshot.papers)), "white"),
            ],
            equal=True,
            expand=True,
        )
        return Panel(metrics, title="Research Copilot", subtitle="Terminal workflow dashboard")

    def _render_tabs(self) -> RenderableType:
        tabs = Text()
        for index, name in enumerate(SCREEN_ORDER, start=1):
            label = f" {index}:{SCREEN_TITLES[name]} "
            style = "bold black on cyan" if name == self.current_screen else "bold white on grey23"
            tabs.append(label, style=style)
            tabs.append("  ")
        return tabs

    def _render_body(self) -> RenderableType:
        if self.current_screen == "overview":
            return self._render_overview()
        if self.current_screen == "jobs":
            return self._render_jobs_screen()
        if self.current_screen == "experiments":
            return self._render_experiments_screen()
        return self._render_knowledge_screen()

    def _render_overview(self) -> RenderableType:
        layout = Layout()
        layout.split_column(
            Layout(name="top", ratio=2),
            Layout(name="bottom", ratio=1),
        )
        layout["top"].split_row(
            Layout(Panel(self._render_jobs_table(limit=8), title="Recent jobs"), name="jobs"),
            Layout(
                Panel(self._render_experiments_table(limit=8), title="Recent experiments"),
                name="experiments",
            ),
        )
        layout["bottom"].split_row(
            Layout(
                Panel(self._render_job_detail(self._selected_job()), title="Selected job"),
                name="job_detail",
            ),
            Layout(
                Panel(
                    self._render_experiment_detail(self._selected_experiment()),
                    title="Selected experiment",
                ),
                name="experiment_detail",
            ),
        )
        return layout

    def _render_jobs_screen(self) -> RenderableType:
        layout = Layout()
        layout.split_row(
            Layout(Panel(self._render_jobs_table(limit=20), title="Jobs"), ratio=2),
            Layout(Panel(self._render_job_detail(self._selected_job()), title="Job detail"), ratio=3),
        )
        return layout

    def _render_experiments_screen(self) -> RenderableType:
        layout = Layout()
        layout.split_row(
            Layout(Panel(self._render_experiments_table(limit=20), title="Experiments"), ratio=2),
            Layout(
                Panel(
                    self._render_experiment_detail(self._selected_experiment()),
                    title="Experiment detail",
                ),
                ratio=3,
            ),
        )
        return layout

    def _render_knowledge_screen(self) -> RenderableType:
        layout = Layout()
        layout.split_column(
            Layout(Panel(self._render_insights_table(), title="Insights"), ratio=2),
            Layout(name="bottom", ratio=2),
        )
        layout["bottom"].split_row(
            Layout(Panel(self._render_papers_table(), title="Saved papers"), ratio=2),
            Layout(Panel(self._render_context_table(), title="Research context"), ratio=2),
        )
        return layout

    def _render_jobs_table(self, limit: int) -> RenderableType:
        if not self.snapshot.jobs:
            return Text("No jobs yet. Submit or sync a run to monitor it here.", style="dim")

        table = Table(expand=True)
        table.add_column("Sel", width=3)
        table.add_column("Job", style="bold")
        table.add_column("Status")
        table.add_column("GPU", justify="right", width=5)
        table.add_column("Submitted", width=16)
        for index, job in enumerate(self.snapshot.jobs[:limit]):
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
        if not self.snapshot.experiments:
            return Text("No experiments tracked yet. Store one to populate this view.", style="dim")

        table = Table(expand=True)
        table.add_column("Sel", width=3)
        table.add_column("Experiment", style="bold")
        table.add_column("Status")
        table.add_column("Dataset")
        table.add_column("Updated", width=16)
        for index, experiment in enumerate(self.snapshot.experiments[:limit]):
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
            return Text("No job selected.", style="dim")

        info = Table.grid(expand=True)
        info.add_column(style="bold cyan", width=16)
        info.add_column()
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
            Text("\nLog tail", style="bold"),
            Text(job.log_tail, style="white"),
            Text("\nStderr", style="bold"),
            Text(job.error_tail, style="dim"),
        )

    def _render_experiment_detail(self, experiment: ExperimentRecord | None) -> RenderableType:
        if experiment is None:
            return Text("No experiment selected.", style="dim")

        info = Table.grid(expand=True)
        info.add_column(style="bold magenta", width=16)
        info.add_column()
        info.add_row("Experiment ID", experiment.experiment_id)
        info.add_row("Status", experiment.status)
        info.add_row("Dataset", experiment.dataset or "—")
        info.add_row("Model", experiment.model_type or "—")
        info.add_row("Tags", ", ".join(experiment.tags) if experiment.tags else "—")
        info.add_row("Updated", format_timestamp(experiment.updated_at))
        info.add_row("W&B run", experiment.wandb_run_id or "—")
        info.add_row("Slurm job", experiment.slurm_job_id or "—")
        hypothesis = experiment.hypothesis or experiment.description or "No experiment notes yet."
        return Group(
            info,
            Text("\nHypothesis / notes", style="bold"),
            Text(hypothesis),
            Text("\nResult snapshot", style="bold"),
            Text(experiment.results_summary),
        )

    def _render_insights_table(self) -> RenderableType:
        if not self.snapshot.insights:
            return Text("No insights captured yet.", style="dim")

        table = Table(expand=True)
        table.add_column("Title", style="bold")
        table.add_column("Category")
        table.add_column("Confidence", justify="right")
        table.add_column("Summary")
        for insight in self.snapshot.insights:
            table.add_row(
                insight.title,
                insight.category,
                insight.confidence,
                insight.content[:70] + ("…" if len(insight.content) > 70 else ""),
            )
        return table

    def _render_papers_table(self) -> RenderableType:
        if not self.snapshot.papers:
            return Text("No saved papers yet.", style="dim")

        table = Table(expand=True)
        table.add_column("Title", style="bold")
        table.add_column("Authors")
        table.add_column("Year", width=6)
        for paper in self.snapshot.papers:
            authors = ", ".join(paper.authors[:2]) if paper.authors else "—"
            if len(paper.authors) > 2:
                authors += " +"
            table.add_row(paper.title, authors, paper.year)
        return table

    def _render_context_table(self) -> RenderableType:
        if not self.snapshot.context_entries:
            return Text("No research context stored yet.", style="dim")

        table = Table(expand=True)
        table.add_column("Key", style="bold")
        table.add_column("Type")
        table.add_column("Value")
        for context in self.snapshot.context_entries:
            value = context.value[:80] + ("…" if len(context.value) > 80 else "")
            table.add_row(context.key, context.context_type, value)
        return table

    def _render_footer(self) -> RenderableType:
        selected = (
            f"job {self.selected_job_index + 1}/{len(self.snapshot.jobs)}"
            if self.current_screen == "jobs" and self.snapshot.jobs
            else f"experiment {self.selected_experiment_index + 1}/{len(self.snapshot.experiments)}"
            if self.current_screen == "experiments" and self.snapshot.experiments
            else "overview"
        )
        return Panel(
            Text(f"{COMMAND_HINT} • focus: {selected}", style="bold cyan"),
            border_style="cyan",
        )

    def _selected_job(self) -> JobRecord | None:
        if not self.snapshot.jobs:
            return None
        return self.snapshot.jobs[self.selected_job_index]

    def _selected_experiment(self) -> ExperimentRecord | None:
        if not self.snapshot.experiments:
            return None
        return self.snapshot.experiments[self.selected_experiment_index]

    def _metric_panel(self, label: str, value: str, style: str) -> RenderableType:
        return Panel(Text(value, justify="center", style=f"bold {style}"), title=label)

    def _status_style(self, value: str) -> str:
        lowered = value.lower()
        if lowered in {"running", "pending"}:
            return "bold yellow"
        if lowered in {"completed"}:
            return "bold green"
        if lowered in {"failed", "cancelled"}:
            return "bold red"
        return "bold white"


def launch_tui(console: Console | None = None) -> ResearchCopilotTUI:
    """Create and run the terminal dashboard."""
    app = ResearchCopilotTUI()
    app.run(console=console)
    return app
