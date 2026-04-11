"""Shared service boundary for CLI/TUI research-ops state access."""

from __future__ import annotations

import json
from typing import Any

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
    _mock_jobs,
    handle_cancel_job,
    handle_get_job_logs,
    handle_submit_job,
)
from research_copilot.services.workflow_snapshot import build_workflow_snapshot


def _decode_response(response: dict[str, Any]) -> Any:
    content = response.get("content", [])
    if not content:
        return {}
    text = content[0].get("text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text}


async def get_snapshot(*, max_items: int = 5) -> dict[str, Any]:
    return build_workflow_snapshot(max_items=max_items)


async def list_jobs(*, status_filter: str = "", limit: int = 20) -> dict[str, Any]:
    jobs = list(_mock_jobs.values())
    if status_filter:
        jobs = [job for job in jobs if job.status == status_filter.upper()]
    jobs = jobs[-limit:]
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
    job = _mock_jobs.get(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
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
        "output": job.output,
        "error": job.error,
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


async def list_insights(*, category: str = "", tag: str = "", search_text: str = "", limit: int = 20) -> dict[str, Any]:
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
