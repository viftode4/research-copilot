"""Read-only workflow snapshots for terminal UI surfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from research_copilot.mcp_servers.slurm import MockJob
from research_copilot.services.research_ops import JobState, ResearchOpsService


def _truncate_log(text: str, *, max_lines: int, max_chars: int) -> str:
    if not text:
        return "(no output yet)"

    lines = text.splitlines()
    tail = "\n".join(lines[-max_lines:])
    if len(tail) <= max_chars:
        return tail
    return "…" + tail[-(max_chars - 1) :]


def summarize_job(
    job: JobState | MockJob,
    *,
    max_log_lines: int = 8,
    max_log_chars: int = 400,
) -> dict[str, Any]:
    """Normalize a Slurm job into a TUI-friendly summary row."""

    return {
        "job_id": job.job_id,
        "name": job.name,
        "status": job.status,
        "is_active": job.status in {"PENDING", "RUNNING"},
        "submitted_at": job.submitted_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "partition": job.partition,
        "gpus": job.gpus,
        "time_limit": job.time_limit,
        "stdout_preview": _truncate_log(
            job.stdout if isinstance(job, JobState) else job.output,
            max_lines=max_log_lines,
            max_chars=max_log_chars,
        ),
        "stderr_preview": _truncate_log(
            job.stderr if isinstance(job, JobState) else job.error,
            max_lines=max_log_lines,
            max_chars=max_log_chars,
        )
        if (job.stderr if isinstance(job, JobState) else job.error)
        else "",
    }


def summarize_experiment(
    experiment: Mapping[str, Any],
    *,
    jobs: Mapping[str, MockJob] | None = None,
) -> dict[str, Any]:
    """Normalize experiment state for the TUI experiments pane."""

    linked_job_id = str(experiment.get("slurm_job_id") or "")
    linked_job = (jobs or {}).get(linked_job_id)
    results = experiment.get("results") or {}

    return {
        "id": experiment.get("id", ""),
        "name": experiment.get("name", ""),
        "status": experiment.get("status", "unknown"),
        "is_active": str(experiment.get("status", "")).lower()
        in ACTIVE_EXPERIMENT_STATUSES,
        "hypothesis": experiment.get("hypothesis", ""),
        "dataset": experiment.get("dataset", ""),
        "model_type": experiment.get("model_type", ""),
        "tags": list(experiment.get("tags") or []),
        "result_keys": sorted(results.keys()),
        "has_results": bool(results),
        "linked_job_id": linked_job_id or None,
        "linked_job_status": linked_job.status if linked_job else None,
        "updated_at": experiment.get("updated_at", ""),
        "created_at": experiment.get("created_at", ""),
    }


def _status_counts(experiments: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for experiment in experiments:
        status = str(experiment.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_workflow_snapshot(
    *,
    store: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    jobs: Mapping[str, MockJob] | None = None,
    max_items: int = 5,
    max_log_lines: int = 8,
    max_log_chars: int = 400,
) -> dict[str, Any]:
    """Build the overview payload consumed by the terminal workflow UI."""

    service = ResearchOpsService(store=store, jobs=jobs)
    state = service.snapshot(
        job_limit=max_items,
        experiment_limit=max_items,
        paper_limit=max_items,
        context_limit=max_items,
    )

    job_items = sorted(
        (
            summarize_job(
                job,
                max_log_lines=max_log_lines,
                max_log_chars=max_log_chars,
            )
            for job in state.jobs
        ),
        key=lambda item: (
            not item["is_active"],
            item["submitted_at"],
            item["job_id"],
        ),
        reverse=True,
    )
    experiment_items = [
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
        for experiment in state.experiments
    ]
    experiment_items.sort(
        key=lambda item: (item["is_active"], item["updated_at"], item["id"]),
        reverse=True,
    )

    papers = list(reversed(state.papers))
    context_entries = list(reversed(state.context_entries))

    return {
        "jobs": {
            "total": len(job_items),
            "active": sum(1 for item in job_items if item["is_active"]),
            "items": job_items[:max_items],
        },
        "experiments": {
            "total": len(experiment_items),
            "active": sum(1 for item in experiment_items if item["is_active"]),
            "by_status": state.experiment_status_counts,
            "items": experiment_items[:max_items],
        },
        "knowledge": {
            "insights_total": len(state.insights),
            "papers_total": len(papers),
            "context_total": len(context_entries),
            "recent_papers": [
                {
                    "id": paper.paper_id,
                    "title": paper.title,
                    "year": paper.year,
                    "tags": list(paper.tags),
                }
                for paper in papers[-max_items:]
            ],
            "recent_context": [
                {
                    "id": entry.context_id,
                    "key": entry.key,
                    "context_type": entry.context_type,
                    "value": entry.value,
                }
                for entry in context_entries[-max_items:]
            ],
        },
        "selection": {
            "default_job_id": job_items[0]["job_id"] if job_items else None,
            "default_experiment_id": experiment_items[0]["id"] if experiment_items else None,
        },
    }
