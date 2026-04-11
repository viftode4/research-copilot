"""Read-oriented adapters that normalize backend state for the TUI."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from research_copilot.services.research_ops import (
    ACTIVE_JOB_STATUSES,
    ResearchOpsService,
    ResearchOpsState,
)


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_timestamp(value: str) -> str:
    parsed = _parse_timestamp(value)
    if not parsed:
        return "—"
    return parsed.strftime("%Y-%m-%d %H:%M")


def summarize_mapping(data: dict[str, Any]) -> str:
    if not data:
        return "No results captured yet."
    return "\n".join(f"{key}: {value}" for key, value in sorted(data.items()))


def tail_text(value: str, max_lines: int = 8, empty_message: str = "(no logs yet)") -> str:
    if not value:
        return empty_message
    return "\n".join(value.splitlines()[-max_lines:])


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    name: str
    status: str
    partition: str
    gpus: int
    submitted_at: str
    started_at: str | None
    completed_at: str | None
    time_limit: str
    log_tail: str
    error_tail: str


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    name: str
    status: str
    hypothesis: str
    description: str
    dataset: str
    model_type: str
    tags: tuple[str, ...]
    updated_at: str
    results_summary: str
    wandb_run_id: str
    slurm_job_id: str


@dataclass(frozen=True)
class InsightRecord:
    insight_id: str
    title: str
    category: str
    confidence: str
    content: str
    created_at: str


@dataclass(frozen=True)
class PaperRecord:
    paper_id: str
    title: str
    authors: tuple[str, ...]
    year: str
    relevance_notes: str
    added_at: str


@dataclass(frozen=True)
class ContextRecord:
    context_id: str
    key: str
    context_type: str
    value: str
    updated_at: str


@dataclass(frozen=True)
class DashboardSnapshot:
    jobs: tuple[JobRecord, ...]
    experiments: tuple[ExperimentRecord, ...]
    insights: tuple[InsightRecord, ...]
    papers: tuple[PaperRecord, ...]
    context_entries: tuple[ContextRecord, ...]
    experiment_status_counts: dict[str, int]

    @property
    def active_jobs(self) -> int:
        return sum(1 for job in self.jobs if job.status in ACTIVE_JOB_STATUSES)

    @property
    def completed_experiments(self) -> int:
        return self.experiment_status_counts.get("completed", 0)

    @property
    def running_experiments(self) -> int:
        return self.experiment_status_counts.get("running", 0)


def _load_state(
    *,
    service: ResearchOpsService | None = None,
    job_limit: int | None = None,
    experiment_limit: int | None = None,
    insight_limit: int | None = None,
    paper_limit: int | None = None,
    context_limit: int | None = None,
) -> ResearchOpsState:
    return (service or ResearchOpsService()).snapshot(
        job_limit=job_limit,
        experiment_limit=experiment_limit,
        insight_limit=insight_limit,
        paper_limit=paper_limit,
        context_limit=context_limit,
    )


def load_job_records(limit: int = 20, *, service: ResearchOpsService | None = None) -> tuple[JobRecord, ...]:
    state = _load_state(service=service, job_limit=limit)
    return tuple(
        JobRecord(
            job_id=job.job_id,
            name=job.name,
            status=job.status,
            partition=job.partition,
            gpus=job.gpus,
            submitted_at=job.submitted_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            time_limit=job.time_limit,
            log_tail=tail_text(job.stdout),
            error_tail=tail_text(job.stderr, empty_message="(no stderr)"),
        )
        for job in state.jobs
    )


def load_experiment_records(
    limit: int = 20, *, service: ResearchOpsService | None = None
) -> tuple[ExperimentRecord, ...]:
    state = _load_state(service=service, experiment_limit=limit)
    return tuple(
        ExperimentRecord(
            experiment_id=experiment.experiment_id,
            name=experiment.name,
            status=experiment.status,
            hypothesis=experiment.hypothesis,
            description=experiment.description,
            dataset=experiment.dataset,
            model_type=experiment.model_type,
            tags=experiment.tags,
            updated_at=experiment.updated_at,
            results_summary=summarize_mapping(experiment.results),
            wandb_run_id=experiment.wandb_run_id,
            slurm_job_id=experiment.linked_job_id or "",
        )
        for experiment in state.experiments
    )


def load_insight_records(
    limit: int = 10, *, service: ResearchOpsService | None = None
) -> tuple[InsightRecord, ...]:
    state = _load_state(service=service, insight_limit=limit)
    return tuple(
        InsightRecord(
            insight_id=insight.insight_id,
            title=insight.title,
            category=insight.category,
            confidence=(
                f"{insight.confidence:.2f}" if isinstance(insight.confidence, float) else "—"
            ),
            content=insight.content,
            created_at=insight.created_at,
        )
        for insight in state.insights
    )


def load_paper_records(
    limit: int = 10, *, service: ResearchOpsService | None = None
) -> tuple[PaperRecord, ...]:
    state = _load_state(service=service, paper_limit=limit)
    return tuple(
        PaperRecord(
            paper_id=paper.paper_id,
            title=paper.title,
            authors=paper.authors,
            year=str(paper.year or "—"),
            relevance_notes=paper.relevance_notes,
            added_at=paper.added_at,
        )
        for paper in state.papers
    )


def load_context_records(
    limit: int = 10, *, service: ResearchOpsService | None = None
) -> tuple[ContextRecord, ...]:
    state = _load_state(service=service, context_limit=limit)
    return tuple(
        ContextRecord(
            context_id=context.context_id,
            key=context.key,
            context_type=context.context_type,
            value=context.value,
            updated_at=context.updated_at,
        )
        for context in state.context_entries
    )


def build_dashboard_snapshot(*, service: ResearchOpsService | None = None) -> DashboardSnapshot:
    state = _load_state(
        service=service,
        job_limit=20,
        experiment_limit=20,
        insight_limit=10,
        paper_limit=10,
        context_limit=10,
    )
    experiments = tuple(
        ExperimentRecord(
            experiment_id=experiment.experiment_id,
            name=experiment.name,
            status=experiment.status,
            hypothesis=experiment.hypothesis,
            description=experiment.description,
            dataset=experiment.dataset,
            model_type=experiment.model_type,
            tags=experiment.tags,
            updated_at=experiment.updated_at,
            results_summary=summarize_mapping(experiment.results),
            wandb_run_id=experiment.wandb_run_id,
            slurm_job_id=experiment.linked_job_id or "",
        )
        for experiment in state.experiments
    )
    status_counts = Counter(experiment.status for experiment in experiments)
    return DashboardSnapshot(
        jobs=tuple(
            JobRecord(
                job_id=job.job_id,
                name=job.name,
                status=job.status,
                partition=job.partition,
                gpus=job.gpus,
                submitted_at=job.submitted_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                time_limit=job.time_limit,
                log_tail=tail_text(job.stdout),
                error_tail=tail_text(job.stderr, empty_message="(no stderr)"),
            )
            for job in state.jobs
        ),
        experiments=experiments,
        insights=tuple(
            InsightRecord(
                insight_id=insight.insight_id,
                title=insight.title,
                category=insight.category,
                confidence=f"{insight.confidence:.2f}"
                if isinstance(insight.confidence, float)
                else "—",
                content=insight.content,
                created_at=insight.created_at,
            )
            for insight in state.insights
        ),
        papers=tuple(
            PaperRecord(
                paper_id=paper.paper_id,
                title=paper.title,
                authors=paper.authors,
                year=str(paper.year or "—"),
                relevance_notes=paper.relevance_notes,
                added_at=paper.added_at,
            )
            for paper in state.papers
        ),
        context_entries=tuple(
            ContextRecord(
                context_id=context.context_id,
                key=context.key,
                context_type=context.context_type,
                value=context.value,
                updated_at=context.updated_at,
            )
            for context in state.context_entries
        ),
        experiment_status_counts=dict(status_counts),
    )
