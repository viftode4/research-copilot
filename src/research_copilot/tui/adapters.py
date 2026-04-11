"""Read-oriented adapters that normalize backend state for the TUI."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from research_copilot.mcp_servers.knowledge_base import _store
from research_copilot.mcp_servers.slurm import MockJob, _mock_jobs

ACTIVE_JOB_STATUSES = frozenset({"PENDING", "RUNNING"})


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


def _job_sort_key(job: MockJob) -> tuple[int, str]:
    parsed = _parse_timestamp(job.submitted_at)
    return (int(parsed.timestamp()) if parsed else 0, job.job_id)


def _experiment_sort_key(record: dict[str, Any]) -> tuple[int, str]:
    updated = _parse_timestamp(record.get("updated_at", "") or record.get("created_at", ""))
    return (int(updated.timestamp()) if updated else 0, record.get("id", ""))


def load_job_records(limit: int = 20) -> tuple[JobRecord, ...]:
    jobs = sorted(_mock_jobs.values(), key=_job_sort_key, reverse=True)[:limit]
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
            log_tail=tail_text(job.output),
            error_tail=tail_text(job.error, empty_message="(no stderr)"),
        )
        for job in jobs
    )


def load_experiment_records(limit: int = 20) -> tuple[ExperimentRecord, ...]:
    experiments = sorted(_store["experiments"], key=_experiment_sort_key, reverse=True)[:limit]
    return tuple(
        ExperimentRecord(
            experiment_id=experiment["id"],
            name=experiment["name"],
            status=experiment.get("status", "planned"),
            hypothesis=experiment.get("hypothesis", ""),
            description=experiment.get("description", ""),
            dataset=experiment.get("dataset", ""),
            model_type=experiment.get("model_type", ""),
            tags=tuple(experiment.get("tags", [])),
            updated_at=experiment.get("updated_at", experiment.get("created_at", "")),
            results_summary=summarize_mapping(experiment.get("results", {})),
            wandb_run_id=experiment.get("wandb_run_id", ""),
            slurm_job_id=experiment.get("slurm_job_id", ""),
        )
        for experiment in experiments
    )


def load_insight_records(limit: int = 10) -> tuple[InsightRecord, ...]:
    insights = list(_store["insights"])[-limit:]
    insights.reverse()
    return tuple(
        InsightRecord(
            insight_id=insight["id"],
            title=insight["title"],
            category=insight.get("category", "observation"),
            confidence=(
                f"{insight['confidence']:.2f}" if isinstance(insight.get("confidence"), float) else "—"
            ),
            content=insight.get("content", ""),
            created_at=insight.get("created_at", ""),
        )
        for insight in insights
    )


def load_paper_records(limit: int = 10) -> tuple[PaperRecord, ...]:
    papers = list(_store["papers"])[-limit:]
    papers.reverse()
    return tuple(
        PaperRecord(
            paper_id=paper["id"],
            title=paper["title"],
            authors=tuple(paper.get("authors", [])),
            year=str(paper.get("year") or "—"),
            relevance_notes=paper.get("relevance_notes", ""),
            added_at=paper.get("added_at", ""),
        )
        for paper in papers
    )


def load_context_records(limit: int = 10) -> tuple[ContextRecord, ...]:
    context_entries = list(_store["context"])[-limit:]
    context_entries.reverse()
    return tuple(
        ContextRecord(
            context_id=context["id"],
            key=context["key"],
            context_type=context.get("context_type", "note"),
            value=context.get("value", ""),
            updated_at=context.get("updated_at", ""),
        )
        for context in context_entries
    )


def build_dashboard_snapshot() -> DashboardSnapshot:
    experiments = load_experiment_records()
    status_counts = Counter(experiment.status for experiment in experiments)
    return DashboardSnapshot(
        jobs=load_job_records(),
        experiments=experiments,
        insights=load_insight_records(),
        papers=load_paper_records(),
        context_entries=load_context_records(),
        experiment_status_counts=dict(status_counts),
    )
