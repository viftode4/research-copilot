"""Shared service boundary for CLI/TUI research-ops state access."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from research_copilot.mcp_servers.knowledge_base import (
    _store,
    handle_get_experiment,
    handle_get_research_context,
    handle_query_experiments,
    handle_query_insights,
    handle_set_research_context,
    handle_store_experiment,
    handle_store_insight,
    handle_store_paper,
    handle_update_experiment,
)
from research_copilot.mcp_servers.literature import handle_search_papers
from research_copilot.mcp_servers.slurm import (
    MockJob,
    _mock_jobs,
    handle_cancel_job,
    handle_get_job_logs,
    handle_submit_job,
)

ACTIVE_JOB_STATUSES = frozenset({"PENDING", "RUNNING"})
ACTIVE_EXPERIMENT_STATUSES = frozenset({"planned", "queued", "running"})


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
    workflow_name: str = ""
    experiment_id: str = ""
    submitted_by: str = ""


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
    created_at: str
    updated_at: str
    results: dict[str, Any]
    wandb_run_id: str
    linked_job_id: str | None
    linked_job_status: str | None
    is_active: bool


@dataclass(frozen=True)
class InsightState:
    insight_id: str
    title: str
    category: str
    confidence: float | str | None
    content: str
    created_at: str
    linked_experiment_id: str
    linked_job_id: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class PaperState:
    paper_id: str
    title: str
    authors: tuple[str, ...]
    year: str
    relevance_notes: str
    tags: tuple[str, ...]
    added_at: str
    linked_experiment_id: str
    linked_job_id: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ContextState:
    context_id: str
    key: str
    context_type: str
    value: str
    updated_at: str
    linked_experiment_id: str
    linked_job_id: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ResearchOpsState:
    jobs: tuple[JobState, ...]
    experiments: tuple[ExperimentState, ...]
    insights: tuple[InsightState, ...]
    papers: tuple[PaperState, ...]
    context_entries: tuple[ContextState, ...]
    experiment_status_counts: dict[str, int]

    @property
    def active_jobs(self) -> int:
        return sum(1 for job in self.jobs if job.status in ACTIVE_JOB_STATUSES)


def _linked_reference(record: Mapping[str, Any], key: str) -> str:
    value = str(record.get(key, "") or "")
    if value:
        return value
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping):
        return str(
            provenance.get(key, "")
            or provenance.get(
                "related_experiment_id" if key == "linked_experiment_id" else "related_job_id", ""
            )
            or ""
        )
    return ""


def _record_provenance(record: Mapping[str, Any]) -> dict[str, Any]:
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping):
        return dict(provenance)
    return {}



def _decode_response(response: dict[str, Any]) -> Any:
    content = response.get("content", [])
    if not content:
        return {}
    text = content[0].get("text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text}



def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None



def _job_sort_key(job: JobState) -> tuple[int, str]:
    parsed = _parse_timestamp(job.submitted_at)
    return (int(parsed.timestamp()) if parsed else 0, job.job_id)



def _experiment_sort_key(experiment: Mapping[str, Any]) -> tuple[int, str]:
    updated = _parse_timestamp(experiment.get("updated_at", "") or experiment.get("created_at", ""))
    return (int(updated.timestamp()) if updated else 0, str(experiment.get("id", "")))


def _normalize_results(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value in (None, "", {}):
        return {}
    return {"raw": value}


class ResearchOpsService:
    """Read-mostly service boundary over current in-memory stores."""

    def __init__(
        self,
        *,
        store: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        jobs: Mapping[str, MockJob] | None = None,
    ) -> None:
        self._store = store or _store
        self._jobs = jobs or _mock_jobs

    def list_jobs(self, *, limit: int = 20, status_filter: str = "") -> tuple[JobState, ...]:
        job_states = [
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
                workflow_name=job.workflow_name,
                experiment_id=job.experiment_id,
                submitted_by=job.submitted_by,
            )
            for job in self._jobs.values()
            if not status_filter or job.status == status_filter.upper()
        ]
        job_states.sort(key=_job_sort_key, reverse=True)
        return tuple(job_states[:limit])

    def get_job(self, job_id: str) -> JobState:
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        return JobState(
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
            workflow_name=job.workflow_name,
            experiment_id=job.experiment_id,
            submitted_by=job.submitted_by,
        )

    def list_experiments(self, *, limit: int = 20) -> tuple[ExperimentState, ...]:
        raw_experiments = sorted(self._store.get("experiments", []), key=_experiment_sort_key, reverse=True)
        items: list[ExperimentState] = []
        for experiment in raw_experiments[:limit]:
            linked_job_id = str(experiment.get("slurm_job_id") or "") or None
            linked_job = self._jobs.get(linked_job_id or "")
            items.append(
                ExperimentState(
                    experiment_id=str(experiment.get("id", "")),
                    name=str(experiment.get("name", "")),
                    status=str(experiment.get("status", "unknown")),
                    hypothesis=str(experiment.get("hypothesis", "")),
                    description=str(experiment.get("description", "")),
                    dataset=str(experiment.get("dataset", "")),
                    model_type=str(experiment.get("model_type", "")),
                    tags=tuple(experiment.get("tags", [])),
                    created_at=str(experiment.get("created_at", "")),
                    updated_at=str(experiment.get("updated_at", experiment.get("created_at", ""))),
                    results=_normalize_results(experiment.get("results", {})),
                    wandb_run_id=str(experiment.get("wandb_run_id", "")),
                    linked_job_id=linked_job_id,
                    linked_job_status=linked_job.status if linked_job else None,
                    is_active=str(experiment.get("status", "")).lower() in ACTIVE_EXPERIMENT_STATUSES,
                )
            )
        return tuple(items)

    def snapshot(
        self,
        *,
        job_limit: int = 20,
        experiment_limit: int = 20,
        insight_limit: int = 10,
        paper_limit: int = 10,
        context_limit: int = 10,
    ) -> ResearchOpsState:
        experiments = self.list_experiments(limit=experiment_limit)
        insights = tuple(
            InsightState(
                insight_id=str(insight.get("id", "")),
                title=str(insight.get("title", "")),
                category=str(insight.get("category", "observation")),
                confidence=insight.get("confidence"),
                content=str(insight.get("content", "")),
                created_at=str(insight.get("created_at", "")),
                linked_experiment_id=_linked_reference(insight, "linked_experiment_id"),
                linked_job_id=_linked_reference(insight, "linked_job_id"),
                provenance=_record_provenance(insight),
            )
            for insight in list(self._store.get("insights", []))[-insight_limit:][::-1]
        )
        papers = tuple(
            PaperState(
                paper_id=str(paper.get("id", "")),
                title=str(paper.get("title", "")),
                authors=tuple(paper.get("authors", [])),
                year=str(paper.get("year") or "—"),
                relevance_notes=str(paper.get("relevance_notes", "")),
                tags=tuple(paper.get("tags", [])),
                added_at=str(paper.get("added_at", "")),
                linked_experiment_id=_linked_reference(paper, "linked_experiment_id"),
                linked_job_id=_linked_reference(paper, "linked_job_id"),
                provenance=_record_provenance(paper),
            )
            for paper in list(self._store.get("papers", []))[-paper_limit:][::-1]
        )
        context_entries = tuple(
            ContextState(
                context_id=str(entry.get("id", "")),
                key=str(entry.get("key", "")),
                context_type=str(entry.get("context_type", "note")),
                value=str(entry.get("value", "")),
                updated_at=str(entry.get("updated_at", "")),
                linked_experiment_id=_linked_reference(entry, "linked_experiment_id"),
                linked_job_id=_linked_reference(entry, "linked_job_id"),
                provenance=_record_provenance(entry),
            )
            for entry in list(self._store.get("context", []))[-context_limit:][::-1]
        )
        status_counts = Counter(experiment.status for experiment in experiments)
        return ResearchOpsState(
            jobs=self.list_jobs(limit=job_limit),
            experiments=experiments,
            insights=insights,
            papers=papers,
            context_entries=context_entries,
            experiment_status_counts=dict(status_counts),
        )



def _build_snapshot_payload(state: ResearchOpsState, *, max_items: int) -> dict[str, Any]:
    return {
        "jobs": {
            "total": len(state.jobs),
            "active": sum(1 for job in state.jobs if job.status in ACTIVE_JOB_STATUSES),
            "items": [
                {
                    "job_id": job.job_id,
                    "name": job.name,
                    "status": job.status,
                    "is_active": job.status in ACTIVE_JOB_STATUSES,
                    "submitted_at": job.submitted_at,
                    "started_at": job.started_at,
                    "completed_at": job.completed_at,
                    "partition": job.partition,
                    "gpus": job.gpus,
                    "time_limit": job.time_limit,
                    "stdout_preview": job.stdout,
                    "stderr_preview": job.stderr,
                    "workflow_name": job.workflow_name,
                    "experiment_id": job.experiment_id,
                }
                for job in state.jobs[:max_items]
            ],
        },
        "experiments": {
            "total": len(state.experiments),
            "active": sum(1 for experiment in state.experiments if experiment.is_active),
            "by_status": state.experiment_status_counts,
            "items": [
                {
                    "id": experiment.experiment_id,
                    "name": experiment.name,
                    "status": experiment.status,
                    "is_active": experiment.is_active,
                    "hypothesis": experiment.hypothesis,
                    "dataset": experiment.dataset,
                    "model_type": experiment.model_type,
                    "tags": list(experiment.tags),
                    "result_keys": sorted(experiment.results.keys()),
                    "has_results": bool(experiment.results),
                    "linked_job_id": experiment.linked_job_id,
                    "linked_job_status": experiment.linked_job_status,
                    "updated_at": experiment.updated_at,
                    "created_at": experiment.created_at,
                }
                for experiment in state.experiments[:max_items]
            ],
        },
        "knowledge": {
            "insights_total": len(state.insights),
            "papers_total": len(state.papers),
            "context_total": len(state.context_entries),
            "recent_papers": [
                {
                    "id": paper.paper_id,
                    "title": paper.title,
                    "year": paper.year,
                    "tags": list(paper.tags),
                }
                for paper in state.papers[:max_items]
            ],
            "recent_context": [
                {
                    "id": entry.context_id,
                    "key": entry.key,
                    "context_type": entry.context_type,
                    "value": entry.value,
                }
                for entry in state.context_entries[:max_items]
            ],
        },
        "selection": {
            "default_job_id": state.jobs[0].job_id if state.jobs else None,
            "default_experiment_id": state.experiments[0].experiment_id if state.experiments else None,
        },
    }


async def get_snapshot(*, max_items: int = 5) -> dict[str, Any]:
    service = ResearchOpsService()
    return _build_snapshot_payload(service.snapshot(job_limit=max_items, experiment_limit=max_items), max_items=max_items)


async def list_jobs(*, status_filter: str = "", limit: int = 20) -> dict[str, Any]:
    service = ResearchOpsService()
    jobs = service.list_jobs(limit=limit, status_filter=status_filter)
    return {
        "total": len(jobs),
        "jobs": [
            {
                "job_id": job.job_id,
                "name": job.name,
                "status": job.status,
                "partition": job.partition,
                "gpus": job.gpus,
                "time_limit": job.time_limit,
                "submitted_at": job.submitted_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "workflow_name": job.workflow_name,
                "experiment_id": job.experiment_id,
                "submitted_by": job.submitted_by,
            }
            for job in jobs
        ],
    }


async def get_job(*, job_id: str) -> dict[str, Any]:
    job = ResearchOpsService().get_job(job_id)
    return {
        "job_id": job.job_id,
        "name": job.name,
        "status": job.status,
        "partition": job.partition,
        "gpus": job.gpus,
        "time_limit": job.time_limit,
        "submitted_at": job.submitted_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "workflow_name": job.workflow_name,
        "experiment_id": job.experiment_id,
        "submitted_by": job.submitted_by,
        "output": job.stdout,
        "error": job.stderr,
    }


async def get_job_logs(*, job_id: str, lines: int = 100) -> dict[str, Any]:
    return _decode_response(await handle_get_job_logs({"job_id": job_id, "lines": lines}))


async def submit_job(
    *,
    job_name: str,
    script: str,
    partition: str = "gpu",
    gpus: int = 1,
    time_limit: str = "04:00:00",
    submitted_by: str = "",
    workflow_name: str = "",
    experiment_id: str = "",
) -> dict[str, Any]:
    return _decode_response(
        await handle_submit_job(
            {
                "job_name": job_name,
                "script": script,
                "partition": partition,
                "gpus": gpus,
                "time_limit": time_limit,
                "submitted_by": submitted_by,
                "workflow_name": workflow_name,
                "experiment_id": experiment_id,
            }
        )
    )


async def cancel_job(*, job_id: str) -> dict[str, Any]:
    return _decode_response(await handle_cancel_job({"job_id": job_id}))


async def list_experiments(
    *,
    status: str = "",
    dataset: str = "",
    model_type: str = "",
    tag: str = "",
    search_text: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    return _decode_response(
        await handle_query_experiments(
            {
                "status": status,
                "dataset": dataset,
                "model_type": model_type,
                "tag": tag,
                "search_text": search_text,
                "limit": limit,
            }
        )
    )


async def get_experiment(*, experiment_id: str) -> dict[str, Any]:
    return _decode_response(await handle_get_experiment({"experiment_id": experiment_id}))


async def create_experiment(
    *,
    name: str,
    hypothesis: str = "",
    description: str = "",
    config: str = "",
    status: str = "planned",
    dataset: str = "",
    model_type: str = "",
    tags: list[str] | None = None,
    created_by: str = "",
    actor_type: str = "",
    workflow_name: str = "",
    results: str = "",
) -> dict[str, Any]:
    return _decode_response(
        await handle_store_experiment(
            {
                "name": name,
                "hypothesis": hypothesis,
                "description": description,
                "config": config,
                "status": status,
                "dataset": dataset,
                "model_type": model_type,
                "tags": json.dumps(tags or []),
                "created_by": created_by,
                "actor_type": actor_type,
                "workflow_name": workflow_name,
                "results": results,
            }
        )
    )


async def update_experiment(
    *,
    experiment_id: str,
    status: str = "",
    results: str = "",
    wandb_run_id: str = "",
    wandb_run_url: str = "",
    slurm_job_id: str = "",
    actor_type: str = "",
    workflow_name: str = "",
) -> dict[str, Any]:
    return _decode_response(
        await handle_update_experiment(
            {
                "experiment_id": experiment_id,
                "status": status,
                "results": results,
                "wandb_run_id": wandb_run_id,
                "wandb_run_url": wandb_run_url,
                "slurm_job_id": slurm_job_id,
                "actor_type": actor_type,
                "workflow_name": workflow_name,
            }
        )
    )


async def list_context() -> dict[str, Any]:
    return _decode_response(await handle_get_research_context({}))


async def get_context(*, key: str) -> dict[str, Any]:
    return _decode_response(await handle_get_research_context({"key": key}))


async def set_context(
    *,
    key: str,
    value: str,
    context_type: str = "note",
    actor_type: str = "",
    workflow_name: str = "",
    linked_experiment_id: str = "",
    linked_job_id: str = "",
) -> dict[str, Any]:
    return _decode_response(
        await handle_set_research_context(
            {
                "key": key,
                "value": value,
                "context_type": context_type,
                "actor_type": actor_type,
                "workflow_name": workflow_name,
                "linked_experiment_id": linked_experiment_id,
                "linked_job_id": linked_job_id,
            }
        )
    )


async def list_insights(
    *,
    category: str = "",
    tag: str = "",
    search_text: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    return _decode_response(
        await handle_query_insights(
            {
                "category": category,
                "tag": tag,
                "search_text": search_text,
                "limit": limit,
            }
        )
    )


async def add_insight(
    *,
    title: str,
    content: str,
    category: str = "observation",
    experiment_id: str = "",
    confidence: float | None = None,
    tags: list[str] | None = None,
    created_by: str = "",
    actor_type: str = "",
    workflow_name: str = "",
    linked_job_id: str = "",
) -> dict[str, Any]:
    return _decode_response(
        await handle_store_insight(
            {
                "title": title,
                "content": content,
                "category": category,
                "experiment_id": experiment_id,
                "confidence": confidence,
                "tags": json.dumps(tags or []),
                "created_by": created_by,
                "actor_type": actor_type,
                "workflow_name": workflow_name,
                "linked_job_id": linked_job_id,
            }
        )
    )


async def list_papers(*, limit: int = 20) -> dict[str, Any]:
    papers = list(_store["papers"])[-limit:]
    return {"total": len(papers), "papers": papers}


async def search_papers(*, query: str, max_results: int = 10, sources: str = "both") -> dict[str, Any]:
    return _decode_response(
        await handle_search_papers(
            {"query": query, "max_results": max_results, "sources": sources}
        )
    )


async def save_paper(
    *,
    title: str,
    authors: list[str] | None = None,
    abstract: str = "",
    arxiv_id: str = "",
    year: int | None = None,
    url: str = "",
    relevance_notes: str = "",
    tags: list[str] | None = None,
    actor_type: str = "",
    workflow_name: str = "",
) -> dict[str, Any]:
    return _decode_response(
        await handle_store_paper(
            {
                "title": title,
                "authors": json.dumps(authors or []),
                "abstract": abstract,
                "arxiv_id": arxiv_id,
                "year": year,
                "url": url,
                "relevance_notes": relevance_notes,
                "tags": json.dumps(tags or []),
                "actor_type": actor_type,
                "workflow_name": workflow_name,
            }
        )
    )
