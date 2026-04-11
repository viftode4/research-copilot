"""Shared read service for research ops state used by CLI, TUI, and workflows."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from research_copilot.mcp_servers.knowledge_base import _store
from research_copilot.mcp_servers.slurm import MockJob, _mock_jobs

ACTIVE_JOB_STATUSES = frozenset({"PENDING", "RUNNING"})
ACTIVE_EXPERIMENT_STATUSES = frozenset({"planned", "queued", "running"})


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _timestamp_sort_key(record: Mapping[str, Any], *fields: str) -> tuple[int, str]:
    for field in fields:
        parsed = _parse_timestamp(str(record.get(field, "") or ""))
        if parsed:
            return (int(parsed.timestamp()), str(record.get("id", "")))
    return (0, str(record.get("id", "")))


def _job_sort_key(job: MockJob) -> tuple[int, str]:
    parsed = _parse_timestamp(job.submitted_at)
    return (int(parsed.timestamp()) if parsed else 0, job.job_id)


@dataclass(frozen=True)
class JobState:
    job_id: str
    name: str
    status: str
    partition: str
    gpus: int
    submitted_at: str
    started_at: str | None
    completed_at: str | None
    time_limit: str
    stdout: str
    stderr: str

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_JOB_STATUSES


@dataclass(frozen=True)
class ExperimentState:
    experiment_id: str
    name: str
    status: str
    hypothesis: str
    description: str
    dataset: str
    model_type: str
    tags: tuple[str, ...]
    results: dict[str, Any]
    linked_job_id: str | None
    linked_job_status: str | None
    wandb_run_id: str
    created_at: str
    updated_at: str

    @property
    def is_active(self) -> bool:
        return self.status.lower() in ACTIVE_EXPERIMENT_STATUSES


@dataclass(frozen=True)
class InsightState:
    insight_id: str
    title: str
    category: str
    confidence: Any
    content: str
    created_at: str


@dataclass(frozen=True)
class PaperState:
    paper_id: str
    title: str
    authors: tuple[str, ...]
    year: Any
    relevance_notes: str
    tags: tuple[str, ...]
    added_at: str


@dataclass(frozen=True)
class ContextState:
    context_id: str
    key: str
    context_type: str
    value: str
    updated_at: str


@dataclass(frozen=True)
class ResearchOpsState:
    jobs: tuple[JobState, ...]
    experiments: tuple[ExperimentState, ...]
    insights: tuple[InsightState, ...]
    papers: tuple[PaperState, ...]
    context_entries: tuple[ContextState, ...]

    @property
    def active_jobs(self) -> int:
        return sum(1 for job in self.jobs if job.is_active)

    @property
    def experiment_status_counts(self) -> dict[str, int]:
        return dict(Counter(experiment.status for experiment in self.experiments))


class ResearchOpsService:
    """Read-only service boundary over the current backing stores."""

    def __init__(
        self,
        *,
        store: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        jobs: Mapping[str, MockJob] | None = None,
    ) -> None:
        self._store = store or _store
        self._jobs = jobs or _mock_jobs

    def list_jobs(self, *, limit: int | None = None) -> tuple[JobState, ...]:
        jobs = sorted(self._jobs.values(), key=_job_sort_key, reverse=True)
        if limit is not None:
            jobs = jobs[:limit]

        return tuple(
            JobState(
                job_id=job.job_id,
                name=job.name,
                status=job.status,
                partition=job.partition,
                gpus=job.gpus,
                submitted_at=job.submitted_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                time_limit=job.time_limit,
                stdout=job.output,
                stderr=job.error,
            )
            for job in jobs
        )

    def list_experiments(self, *, limit: int | None = None) -> tuple[ExperimentState, ...]:
        experiments = sorted(
            self._store.get("experiments", []),
            key=lambda record: _timestamp_sort_key(record, "updated_at", "created_at"),
            reverse=True,
        )
        if limit is not None:
            experiments = experiments[:limit]

        return tuple(self._build_experiment_state(experiment) for experiment in experiments)

    def list_insights(self, *, limit: int | None = None) -> tuple[InsightState, ...]:
        insights = sorted(
            self._store.get("insights", []),
            key=lambda record: _timestamp_sort_key(record, "created_at"),
            reverse=True,
        )
        if limit is not None:
            insights = insights[:limit]

        return tuple(
            InsightState(
                insight_id=str(insight.get("id", "")),
                title=str(insight.get("title", "")),
                category=str(insight.get("category", "observation")),
                confidence=insight.get("confidence"),
                content=str(insight.get("content", "")),
                created_at=str(insight.get("created_at", "")),
            )
            for insight in insights
        )

    def list_papers(self, *, limit: int | None = None) -> tuple[PaperState, ...]:
        papers = sorted(
            self._store.get("papers", []),
            key=lambda record: _timestamp_sort_key(record, "added_at"),
            reverse=True,
        )
        if limit is not None:
            papers = papers[:limit]

        return tuple(
            PaperState(
                paper_id=str(paper.get("id", "")),
                title=str(paper.get("title", "")),
                authors=tuple(str(author) for author in paper.get("authors", [])),
                year=paper.get("year"),
                relevance_notes=str(paper.get("relevance_notes", "")),
                tags=tuple(str(tag) for tag in paper.get("tags", [])),
                added_at=str(paper.get("added_at", "")),
            )
            for paper in papers
        )

    def list_context_entries(self, *, limit: int | None = None) -> tuple[ContextState, ...]:
        entries = sorted(
            self._store.get("context", []),
            key=lambda record: _timestamp_sort_key(record, "updated_at"),
            reverse=True,
        )
        if limit is not None:
            entries = entries[:limit]

        return tuple(
            ContextState(
                context_id=str(entry.get("id", "")),
                key=str(entry.get("key", "")),
                context_type=str(entry.get("context_type", "note")),
                value=str(entry.get("value", "")),
                updated_at=str(entry.get("updated_at", "")),
            )
            for entry in entries
        )

    def snapshot(
        self,
        *,
        job_limit: int | None = None,
        experiment_limit: int | None = None,
        insight_limit: int | None = None,
        paper_limit: int | None = None,
        context_limit: int | None = None,
    ) -> ResearchOpsState:
        return ResearchOpsState(
            jobs=self.list_jobs(limit=job_limit),
            experiments=self.list_experiments(limit=experiment_limit),
            insights=self.list_insights(limit=insight_limit),
            papers=self.list_papers(limit=paper_limit),
            context_entries=self.list_context_entries(limit=context_limit),
        )

    def _build_experiment_state(self, experiment: Mapping[str, Any]) -> ExperimentState:
        linked_job_id = str(experiment.get("slurm_job_id") or "").strip() or None
        linked_job = self._jobs.get(linked_job_id) if linked_job_id else None

        raw_results = experiment.get("results")
        if isinstance(raw_results, Mapping):
            results = dict(raw_results)
        elif raw_results:
            results = {"raw": raw_results}
        else:
            results = {}

        return ExperimentState(
            experiment_id=str(experiment.get("id", "")),
            name=str(experiment.get("name", "")),
            status=str(experiment.get("status", "unknown")),
            hypothesis=str(experiment.get("hypothesis", "")),
            description=str(experiment.get("description", "")),
            dataset=str(experiment.get("dataset", "")),
            model_type=str(experiment.get("model_type", "")),
            tags=tuple(str(tag) for tag in experiment.get("tags", [])),
            results=results,
            linked_job_id=linked_job_id,
            linked_job_status=linked_job.status if linked_job else None,
            wandb_run_id=str(experiment.get("wandb_run_id", "")),
            created_at=str(experiment.get("created_at", "")),
            updated_at=str(experiment.get("updated_at", experiment.get("created_at", ""))),
        )
