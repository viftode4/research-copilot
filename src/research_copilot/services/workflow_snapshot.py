"""Canonical workflow snapshots and legacy projections for terminal UI surfaces."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from research_copilot.mcp_servers.slurm import MockJob
from research_copilot.research_state import resolve_workspace, utc_now_iso
from research_copilot.services.research_ops import (
    ACTIVE_EXPERIMENT_STATUSES,
    ContextState,
    InsightState,
    JobState,
    PaperState,
    ResearchOpsService,
)

SNAPSHOT_SCHEMA_VERSION = "1.0.0"
SNAPSHOT_OWNER = "research_copilot.services.workflow_snapshot.build_canonical_snapshot"
MISSING_VALUE_KINDS = ("missing", "absent", "empty")
SNAPSHOT_STATES = ("complete", "partial", "loading", "error")
JOB_STATUS_MAP = {
    "PENDING": "queued",
    "RUNNING": "running",
    "COMPLETED": "succeeded",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
}
EXPERIMENT_STATUS_MAP = {
    "planned": "queued",
    "queued": "queued",
    "running": "running",
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "blocked": "blocked",
}


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
    """Normalize experiment state for the legacy workflow snapshot projection."""

    linked_job_id = str(experiment.get("slurm_job_id") or "")
    linked_job = (jobs or {}).get(linked_job_id)
    results = experiment.get("results") or {}

    return {
        "id": experiment.get("id", ""),
        "name": experiment.get("name", ""),
        "status": experiment.get("status", "unknown"),
        "is_active": str(experiment.get("status", "")).lower() in ACTIVE_EXPERIMENT_STATUSES,
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


def _normalize_job_status(status: str) -> str:
    return JOB_STATUS_MAP.get(status.upper(), "unknown")


def _normalize_experiment_status(status: str) -> str:
    return EXPERIMENT_STATUS_MAP.get(status.lower(), "unknown")


def _workspace_entity(service: ResearchOpsService, state: Any) -> dict[str, Any]:
    resolved = resolve_workspace()
    return {
        "id": f"workspace:{resolved.workspace_dir}",
        "type": "workspace",
        "name": resolved.workspace_dir.name or "workspace",
        "title": resolved.workspace_dir.name or str(resolved.workspace_dir),
        "updated_at": utc_now_iso(),
        "status": "complete",
        "summary": "Research Copilot workspace state",
        "attributes": {
            "workspace_dir": str(resolved.workspace_dir),
            "research_root": str(resolved.canonical_root),
            "mode": resolved.mode,
            "counts": {
                "jobs": len(state.jobs),
                "experiments": len(state.experiments),
                "papers": len(state.papers),
                "insights": len(state.insights),
                "context": len(state.context_entries),
            },
        },
    }


def _profile_entities() -> list[dict[str, Any]]:
    # Profiles are not yet backed by a first-class service projection in this repo.
    return []


def _job_entities(
    state_jobs: Sequence[JobState],
    *,
    max_log_lines: int,
    max_log_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    job_entities: list[dict[str, Any]] = []
    run_entities: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for job in state_jobs:
        summary = summarize_job(job, max_log_lines=max_log_lines, max_log_chars=max_log_chars)
        job_entity_id = f"job:{job.job_id}"
        run_entity_id = f"run:{job.job_id}"
        normalized_status = _normalize_job_status(job.status)
        log_summary = {
            "stdout_preview": summary["stdout_preview"],
            "stderr_preview": summary["stderr_preview"],
            "log_available": bool(job.stdout or job.stderr),
        }
        job_entities.append(
            {
                "id": job_entity_id,
                "type": "job",
                "name": job.name,
                "title": job.name,
                "status": normalized_status,
                "created_at": job.submitted_at,
                "updated_at": job.completed_at or job.started_at or job.submitted_at,
                "summary": f"{job.partition} • {job.gpus} GPU(s)",
                "attributes": {
                    "job_id": job.job_id,
                    "partition": job.partition,
                    "gpus": job.gpus,
                    "time_limit": job.time_limit,
                    "workflow_name": job.workflow_name,
                    "experiment_id": job.experiment_id,
                    "submitted_by": job.submitted_by,
                    "log_summary": log_summary,
                },
            }
        )
        run_entities.append(
            {
                "id": run_entity_id,
                "type": "run",
                "name": job.name,
                "title": job.name,
                "status": normalized_status,
                "created_at": job.submitted_at,
                "updated_at": job.completed_at or job.started_at or job.submitted_at,
                "summary": f"Operational run for job {job.job_id}",
                "attributes": {
                    "job_id": job.job_id,
                    "partition": job.partition,
                    "gpus": job.gpus,
                    "time_limit": job.time_limit,
                    "submitted_at": job.submitted_at,
                    "started_at": job.started_at,
                    "completed_at": job.completed_at,
                    "log_summary": log_summary,
                },
            }
        )
        links.append(
            {
                "source_id": run_entity_id,
                "target_id": job_entity_id,
                "link_type": "run_to_job",
                "directionality": "directed",
            }
        )
        for entity_id, label in ((run_entity_id, "Open links"), (job_entity_id, "Inspect details")):
            actions.append(
                {
                    "action_id": f"inspect:{entity_id}",
                    "label": label,
                    "tier": 0,
                    "safety_level": "read_only",
                    "enabled": True,
                    "disabled_reason": "",
                    "preconditions": {
                        "required_statuses": [],
                        "required_links": [],
                        "required_capabilities": [],
                        "notes": "",
                    },
                    "target_entity_id": entity_id,
                    "scope": "tui_affordance",
                }
            )
        actions.append(
            {
                "action_id": f"log-summary:{run_entity_id}",
                "label": "Open log summary",
                "tier": 0,
                "safety_level": "read_only",
                "enabled": True,
                "disabled_reason": "",
                "preconditions": {
                    "required_statuses": [],
                    "required_links": ["run_to_job"],
                    "required_capabilities": [],
                    "notes": "Uses in-snapshot log summaries only in v1a.",
                },
                "target_entity_id": run_entity_id,
                "scope": "tui_affordance",
            }
        )
    return job_entities, run_entities, links, actions


def _experiment_entities(state_experiments: Sequence[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entities: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for experiment in state_experiments:
        experiment_entity_id = f"experiment:{experiment.experiment_id}"
        linked_run_entity_id = f"run:{experiment.linked_job_id}" if experiment.linked_job_id else ""
        entities.append(
            {
                "id": experiment_entity_id,
                "type": "experiment",
                "name": experiment.name,
                "title": experiment.name,
                "status": _normalize_experiment_status(experiment.status),
                "created_at": experiment.created_at,
                "updated_at": experiment.updated_at,
                "summary": experiment.hypothesis or experiment.description or "No hypothesis recorded.",
                "metrics": dict(experiment.results),
                "attributes": {
                    "experiment_id": experiment.experiment_id,
                    "dataset": experiment.dataset,
                    "model_type": experiment.model_type,
                    "tags": list(experiment.tags),
                    "wandb_run_id": experiment.wandb_run_id,
                    "linked_job_id": experiment.linked_job_id,
                    "linked_job_status": experiment.linked_job_status,
                },
            }
        )
        if linked_run_entity_id:
            links.append(
                {
                    "source_id": experiment_entity_id,
                    "target_id": linked_run_entity_id,
                    "link_type": "experiment_to_run",
                    "directionality": "directed",
                }
            )
        actions.append(
            {
                "action_id": f"open-links:{experiment_entity_id}",
                "label": "Open linked research",
                "tier": 0,
                "safety_level": "read_only",
                "enabled": True,
                "disabled_reason": "",
                "preconditions": {
                    "required_statuses": [],
                    "required_links": [],
                    "required_capabilities": [],
                    "notes": "",
                },
                "target_entity_id": experiment_entity_id,
                "scope": "tui_affordance",
            }
        )
    return entities, links, actions


def _knowledge_entity(
    *,
    prefix: str,
    entity_type: str,
    state_items: Sequence[InsightState | PaperState | ContextState],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    entities: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    link_type = {
        "paper": "experiment_to_paper",
        "insight": "experiment_to_insight",
        "context": "experiment_to_context",
    }[entity_type]
    for item in state_items:
        item_id = getattr(item, f"{entity_type}_id", None) or getattr(item, "paper_id", None) or getattr(item, "context_id", None)
        entity_id = f"{prefix}:{item_id}"
        title = getattr(item, "title", "") or getattr(item, "key", "") or entity_id
        updated_at = getattr(item, "updated_at", "") or getattr(item, "created_at", "") or getattr(item, "added_at", "")
        entity: dict[str, Any] = {
            "id": entity_id,
            "type": entity_type,
            "name": title,
            "title": title,
            "updated_at": updated_at,
            "status": "complete",
            "attributes": {
                "linked_experiment_id": item.linked_experiment_id,
                "linked_job_id": item.linked_job_id,
                "provenance": item.provenance,
            },
        }
        if entity_type == "paper":
            entity["summary"] = item.relevance_notes or "Saved paper"
            entity["attributes"] |= {
                "authors": list(item.authors),
                "year": item.year,
                "tags": list(item.tags),
            }
        elif entity_type == "insight":
            entity["summary"] = item.content
            entity["attributes"] |= {"category": item.category, "confidence": item.confidence}
        else:
            entity["summary"] = item.value
            entity["attributes"] |= {"context_type": item.context_type, "key": item.key}
        entities.append(entity)
        if item.linked_experiment_id:
            links.append(
                {
                    "source_id": f"experiment:{item.linked_experiment_id}",
                    "target_id": entity_id,
                    "link_type": link_type,
                    "directionality": "directed",
                    "provenance": item.provenance or {},
                }
            )
        actions.append(
            {
                "action_id": f"inspect:{entity_id}",
                "label": "Inspect item",
                "tier": 0,
                "safety_level": "read_only",
                "enabled": True,
                "disabled_reason": "",
                "preconditions": {
                    "required_statuses": [],
                    "required_links": [],
                    "required_capabilities": [],
                    "notes": "",
                },
                "target_entity_id": entity_id,
                "scope": "tui_affordance",
            }
        )
    return entities, links, actions


def build_canonical_snapshot(
    *,
    store: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    jobs: Mapping[str, MockJob] | None = None,
    max_items: int = 20,
    max_log_lines: int = 8,
    max_log_chars: int = 400,
) -> dict[str, Any]:
    """Build the canonical normalized workflow snapshot used by the v1a TUI."""

    service = ResearchOpsService(store=store, jobs=jobs)
    state = service.snapshot(
        job_limit=max_items,
        experiment_limit=max_items,
        insight_limit=max_items,
        paper_limit=max_items,
        context_limit=max_items,
    )

    entities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    links: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    entities["workspace"].append(_workspace_entity(service, state))
    entities["profile"].extend(_profile_entities())

    job_entities, run_entities, job_links, job_actions = _job_entities(
        state.jobs,
        max_log_lines=max_log_lines,
        max_log_chars=max_log_chars,
    )
    entities["job"].extend(job_entities)
    entities["run"].extend(run_entities)
    links.extend(job_links)
    actions.extend(job_actions)

    experiment_entities, experiment_links, experiment_actions = _experiment_entities(state.experiments)
    entities["experiment"].extend(experiment_entities)
    links.extend(experiment_links)
    actions.extend(experiment_actions)

    paper_entities, paper_links, paper_actions = _knowledge_entity(
        prefix="paper",
        entity_type="paper",
        state_items=state.papers,
    )
    entities["paper"].extend(paper_entities)
    links.extend(paper_links)
    actions.extend(paper_actions)

    insight_entities, insight_links, insight_actions = _knowledge_entity(
        prefix="insight",
        entity_type="insight",
        state_items=state.insights,
    )
    entities["insight"].extend(insight_entities)
    links.extend(insight_links)
    actions.extend(insight_actions)

    context_entities, context_links, context_actions = _knowledge_entity(
        prefix="context",
        entity_type="context",
        state_items=state.context_entries,
    )
    entities["context"].extend(context_entities)
    links.extend(context_links)
    actions.extend(context_actions)

    entities["review"] = []

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "snapshot_owner": SNAPSHOT_OWNER,
        "workspace": {
            "id": entities["workspace"][0]["id"],
            "directory": entities["workspace"][0]["attributes"]["workspace_dir"],
            "mode": entities["workspace"][0]["attributes"]["mode"],
        },
        "entities": dict(entities),
        "links": links,
        "actions": actions,
        "state_semantics": {
            "snapshot_state": "complete",
            "missing_value_kinds": list(MISSING_VALUE_KINDS),
            "supported_states": list(SNAPSHOT_STATES),
        },
    }


def build_workflow_snapshot(
    *,
    store: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    jobs: Mapping[str, MockJob] | None = None,
    max_items: int = 5,
    max_log_lines: int = 8,
    max_log_chars: int = 400,
) -> dict[str, Any]:
    """Build the legacy overview payload consumed by CLI/workflow surfaces."""

    canonical = build_canonical_snapshot(
        store=store,
        jobs=jobs,
        max_items=max_items,
        max_log_lines=max_log_lines,
        max_log_chars=max_log_chars,
    )

    run_entities = canonical["entities"].get("run", [])
    experiment_entities = canonical["entities"].get("experiment", [])
    paper_entities = canonical["entities"].get("paper", [])
    insight_entities = canonical["entities"].get("insight", [])
    context_entities = canonical["entities"].get("context", [])
    experiment_links = {
        link["source_id"]: link
        for link in canonical["links"]
        if link["link_type"] == "experiment_to_run"
    }

    job_items = [
        {
            "job_id": entity["attributes"]["job_id"],
            "name": entity["name"],
            "status": entity["attributes"]["log_summary"]["stdout_preview"] and entity["status"] or entity["status"],
            "is_active": entity["status"] in {"queued", "running"},
            "submitted_at": entity["attributes"]["submitted_at"],
            "started_at": entity["attributes"]["started_at"],
            "completed_at": entity["attributes"]["completed_at"],
            "partition": entity["attributes"]["partition"],
            "gpus": entity["attributes"]["gpus"],
            "time_limit": entity["attributes"]["time_limit"],
            "stdout_preview": entity["attributes"]["log_summary"]["stdout_preview"],
            "stderr_preview": entity["attributes"]["log_summary"]["stderr_preview"],
        }
        for entity in run_entities[:max_items]
    ]
    for item in job_items:
        status_map = {
            "queued": "PENDING",
            "running": "RUNNING",
            "succeeded": "COMPLETED",
            "failed": "FAILED",
            "cancelled": "CANCELLED",
        }
        item["status"] = status_map.get(item["status"], "UNKNOWN")

    experiment_items: list[dict[str, Any]] = []
    for entity in experiment_entities[:max_items]:
        link = experiment_links.get(entity["id"])
        target_id = link["target_id"].removeprefix("run:") if link else None
        result_keys = sorted((entity.get("metrics") or {}).keys())
        experiment_items.append(
            {
                "id": entity["attributes"]["experiment_id"],
                "name": entity["name"],
                "status": {
                    "queued": "planned",
                    "running": "running",
                    "succeeded": "completed",
                    "failed": "failed",
                    "cancelled": "cancelled",
                    "blocked": "blocked",
                }.get(entity["status"], "unknown"),
                "is_active": entity["status"] in {"queued", "running"},
                "hypothesis": entity["summary"],
                "dataset": entity["attributes"]["dataset"],
                "model_type": entity["attributes"]["model_type"],
                "tags": list(entity["attributes"]["tags"]),
                "result_keys": result_keys,
                "has_results": bool(result_keys),
                "linked_job_id": target_id,
                "linked_job_status": entity["attributes"]["linked_job_status"],
                "updated_at": entity["updated_at"],
                "created_at": entity["created_at"],
            }
        )

    experiment_items.sort(
        key=lambda item: (item["is_active"], item["updated_at"], item["id"]),
        reverse=True,
    )

    return {
        "jobs": {
            "total": len(job_items),
            "active": sum(1 for item in job_items if item["is_active"]),
            "items": job_items[:max_items],
        },
        "experiments": {
            "total": len(experiment_items),
            "active": sum(1 for item in experiment_items if item["is_active"]),
            "by_status": {
                item["status"]: sum(1 for candidate in experiment_items if candidate["status"] == item["status"])
                for item in experiment_items
            },
            "items": experiment_items[:max_items],
        },
        "knowledge": {
            "insights_total": len(insight_entities),
            "papers_total": len(paper_entities),
            "context_total": len(context_entities),
            "recent_papers": [
                {
                    "id": entity["id"].removeprefix("paper:"),
                    "title": entity["title"],
                    "year": entity["attributes"]["year"],
                    "tags": list(entity["attributes"]["tags"]),
                }
                for entity in paper_entities[-max_items:]
            ],
            "recent_context": [
                {
                    "id": entity["id"].removeprefix("context:"),
                    "key": entity["attributes"]["key"],
                    "context_type": entity["attributes"]["context_type"],
                    "value": entity["summary"],
                }
                for entity in context_entities[-max_items:]
            ],
        },
        "selection": {
            "default_job_id": job_items[0]["job_id"] if job_items else None,
            "default_experiment_id": experiment_items[0]["id"] if experiment_items else None,
        },
    }
