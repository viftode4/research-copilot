"""Read-oriented adapters that normalize backend state for the v1a TUI."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from research_copilot.services.codex_runtime import (
    CODEX_LAGGING_THRESHOLD_SECONDS,
    CODEX_STALE_THRESHOLD_SECONDS,
)
from research_copilot.services.research_ops import ACTIVE_JOB_STATUSES, ResearchOpsService
from research_copilot.services.workflow_snapshot import build_canonical_snapshot


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


def load_full_job_logs(
    job_id: str,
    *,
    service: ResearchOpsService | None = None,
) -> tuple[str, str]:
    """Load full logs for a run/job on demand by stable job id."""

    state = (service or ResearchOpsService()).get_job(job_id)
    stdout = state.stdout or "(no stdout)"
    stderr = state.stderr or "(no stderr)"
    return stdout, stderr


@dataclass(frozen=True)
class LinkedRecord:
    entity_id: str
    title: str
    entity_type: str
    relation: str
    status: str


@dataclass(frozen=True)
class JobRecord:
    entity_id: str
    job_id: str
    run_id: str
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
    entity_id: str
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
    entity_id: str
    insight_id: str
    title: str
    category: str
    confidence: str
    content: str
    created_at: str


@dataclass(frozen=True)
class PaperRecord:
    entity_id: str
    paper_id: str
    title: str
    authors: tuple[str, ...]
    year: str
    relevance_notes: str
    added_at: str


@dataclass(frozen=True)
class ContextRecord:
    entity_id: str
    context_id: str
    key: str
    context_type: str
    value: str
    updated_at: str


@dataclass(frozen=True)
class FullLogRecord:
    entity_id: str
    job_id: str
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RuntimeRecord:
    source: str
    session_id: str
    run_id: str
    status: str
    current_phase: str
    iteration: int
    max_iterations: int | None
    goal: str
    profile_name: str
    autonomy_level: str
    summary: str
    last_action: str
    last_action_status: str
    last_experiment_id: str
    started_at: str
    updated_at: str
    last_heartbeat_at: str
    lease_expires_at: str
    completed_at: str
    stop_requested_at: str
    stop_reason: str
    consecutive_failures: int
    operator_mode: str
    pending_nudge_count: int
    transport: str
    pane_id: str
    window_name: str
    session_name: str
    workspace: str
    freshness_label: str
    freshness_state: str
    is_stale: bool
    is_active: bool


def fetch_full_run_log(
    entity_id: str,
    *,
    service: ResearchOpsService | None = None,
) -> FullLogRecord:
    """Resolve full logs from a stable run entity id."""

    if not entity_id.startswith("run:"):
        raise ValueError(f"Unsupported log entity id: {entity_id}")
    job_id = entity_id.removeprefix("run:")
    stdout, stderr = load_full_job_logs(job_id, service=service)
    return FullLogRecord(entity_id=entity_id, job_id=job_id, stdout=stdout, stderr=stderr)


def fetch_full_entity_log(
    entity_id: str,
    *,
    service: ResearchOpsService | None = None,
) -> FullLogRecord:
    """Resolve full logs from a supported entity id."""

    resolved_service = service or ResearchOpsService()
    if entity_id.startswith("run:"):
        return fetch_full_run_log(entity_id, service=resolved_service)
    if entity_id.startswith("experiment:"):
        experiment_id = entity_id.removeprefix("experiment:")
        experiment = resolved_service.get_experiment(experiment_id)
        if not experiment.linked_job_id:
            raise ValueError(f"Experiment {experiment_id} does not have a linked job and has no linked run logs")
        stdout, stderr = load_full_job_logs(experiment.linked_job_id, service=resolved_service)
        return FullLogRecord(
            entity_id=entity_id,
            job_id=experiment.linked_job_id,
            stdout=stdout,
            stderr=stderr,
        )
    raise ValueError(f"Unsupported log entity id: {entity_id}")


def fetch_full_log(
    entity_id: str,
    *,
    service: ResearchOpsService | None = None,
) -> FullLogRecord:
    """Backward-compatible alias for full-log resolution by stable entity id."""

    return fetch_full_entity_log(entity_id, service=service)


@dataclass(frozen=True)
class DashboardSnapshot:
    jobs: tuple[JobRecord, ...]
    experiments: tuple[ExperimentRecord, ...]
    insights: tuple[InsightRecord, ...]
    papers: tuple[PaperRecord, ...]
    context_entries: tuple[ContextRecord, ...]
    experiment_status_counts: dict[str, int]
    links_by_entity: dict[str, tuple[LinkedRecord, ...]]
    actions_by_entity: dict[str, tuple[str, ...]]
    schema_version: str
    snapshot_owner: str
    snapshot_state: str
    runtime: RuntimeRecord | None = None

    @property
    def active_jobs(self) -> int:
        return sum(1 for job in self.jobs if job.status in ACTIVE_JOB_STATUSES)

    @property
    def completed_experiments(self) -> int:
        return self.experiment_status_counts.get("completed", 0)

    @property
    def running_experiments(self) -> int:
        return self.experiment_status_counts.get("running", 0)

    @property
    def has_runtime(self) -> bool:
        return self.runtime is not None


def _normalized_job_status(status: str) -> str:
    return {
        "queued": "PENDING",
        "running": "RUNNING",
        "succeeded": "COMPLETED",
        "failed": "FAILED",
        "cancelled": "CANCELLED",
    }.get(status, "UNKNOWN")


def _normalized_experiment_status(status: str) -> str:
    return {
        "queued": "planned",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "blocked": "blocked",
    }.get(status, "unknown")


def _relative_age_label(value: str, *, now: datetime | None = None) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return "unknown"
    reference = now or datetime.now(timezone.utc)
    delta_seconds = max(0, int((reference - parsed).total_seconds()))
    if delta_seconds < 60:
        return f"{delta_seconds}s ago"
    if delta_seconds < 3600:
        return f"{delta_seconds // 60}m ago"
    if delta_seconds < 86400:
        return f"{delta_seconds // 3600}h ago"
    return f"{delta_seconds // 86400}d ago"


def _build_runtime_record(snapshot: dict[str, Any]) -> RuntimeRecord | None:
    runtime = snapshot.get("runtime") or {}
    if not isinstance(runtime, dict) or not runtime:
        return None

    now = datetime.now(timezone.utc)
    status = str(runtime.get("status") or "unknown")
    heartbeat_at = str(runtime.get("last_heartbeat_at") or "")
    updated_at = str(runtime.get("updated_at") or "")
    completed_at = str(runtime.get("completed_at") or "")
    lease_expires_at = str(runtime.get("lease_expires_at") or "")
    lease_expires = _parse_timestamp(lease_expires_at)
    heartbeat = _parse_timestamp(heartbeat_at)

    freshness_reference = heartbeat_at or updated_at or completed_at
    freshness_label = (
        f"last update { _relative_age_label(freshness_reference, now=now) }"
        if status in {"completed", "failed", "stopped"} and freshness_reference
        else (
            f"heartbeat { _relative_age_label(heartbeat_at, now=now) }"
            if heartbeat_at
            else "no heartbeat"
        )
    )
    freshness_state = "unknown"
    if status == "stale":
        freshness_state = "stale"
        if heartbeat_at:
            freshness_label = f"stale • heartbeat {_relative_age_label(heartbeat_at, now=now)}"
        else:
            freshness_label = "stale • no heartbeat"
    elif lease_expires is not None and lease_expires < now:
        freshness_state = "stale"
        freshness_label = (
            f"lease expired • heartbeat {_relative_age_label(heartbeat_at, now=now)}"
            if heartbeat_at
            else "lease expired"
        )
    elif heartbeat is not None:
        age_seconds = max(0, int((now - heartbeat).total_seconds()))
        if age_seconds >= CODEX_STALE_THRESHOLD_SECONDS:
            freshness_state = "stale"
            freshness_label = f"stale • heartbeat {_relative_age_label(heartbeat_at, now=now)}"
        elif age_seconds >= CODEX_LAGGING_THRESHOLD_SECONDS:
            freshness_state = "lagging"
            freshness_label = f"lagging • heartbeat {_relative_age_label(heartbeat_at, now=now)}"
        else:
            freshness_state = "fresh"
    elif status in {"completed", "failed", "stopped"}:
        freshness_state = "terminal"

    return RuntimeRecord(
        source=str(runtime.get("source") or ""),
        session_id=str(runtime.get("session_id") or ""),
        run_id=str(runtime.get("run_id") or ""),
        status=status,
        current_phase=str(runtime.get("current_phase") or ""),
        iteration=int(runtime.get("iteration") or 0),
        max_iterations=int(runtime["max_iterations"]) if runtime.get("max_iterations") not in ("", None) else None,
        goal=str(runtime.get("goal") or ""),
        profile_name=str(runtime.get("profile_name") or ""),
        autonomy_level=str(runtime.get("autonomy_level") or ""),
        summary=str(runtime.get("summary") or ""),
        last_action=str(runtime.get("last_action") or ""),
        last_action_status=str(runtime.get("last_action_status") or ""),
        last_experiment_id=str(runtime.get("last_experiment_id") or ""),
        started_at=str(runtime.get("started_at") or ""),
        updated_at=updated_at,
        last_heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
        completed_at=completed_at,
        stop_requested_at=str(runtime.get("stop_requested_at") or ""),
        stop_reason=str(runtime.get("stop_reason") or ""),
        consecutive_failures=int(runtime.get("consecutive_failures") or 0),
        operator_mode=str(runtime.get("operator_mode") or ""),
        pending_nudge_count=int(runtime.get("pending_nudge_count") or 0),
        transport=str(runtime.get("transport") or ""),
        pane_id=str(runtime.get("pane_id") or ""),
        window_name=str(runtime.get("window_name") or ""),
        session_name=str(runtime.get("session_name") or ""),
        workspace=str(runtime.get("workspace") or ""),
        freshness_label=freshness_label,
        freshness_state=freshness_state,
        is_stale=freshness_state == "stale" or status == "stale",
        is_active=status in {"running", "stopping", "paused"},
    )


def _build_links_index(snapshot: dict[str, Any]) -> dict[str, tuple[LinkedRecord, ...]]:
    entity_lookup = {
        entity["id"]: entity
        for entities in snapshot["entities"].values()
        for entity in entities
    }
    links_by_entity: dict[str, list[LinkedRecord]] = defaultdict(list)
    for link in snapshot["links"]:
        source = entity_lookup.get(link["source_id"])
        target = entity_lookup.get(link["target_id"])
        if not source or not target:
            continue
        links_by_entity[source["id"]].append(
            LinkedRecord(
                entity_id=target["id"],
                title=target.get("title") or target.get("name") or target["id"],
                entity_type=target["type"],
                relation=link["link_type"],
                status=str(target.get("status", "")),
            )
        )
        links_by_entity[target["id"]].append(
            LinkedRecord(
                entity_id=source["id"],
                title=source.get("title") or source.get("name") or source["id"],
                entity_type=source["type"],
                relation=f"linked_from:{link['link_type']}",
                status=str(source.get("status", "")),
            )
        )
    return {entity_id: tuple(items) for entity_id, items in links_by_entity.items()}


def _build_actions_index(snapshot: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    actions_by_entity: dict[str, list[str]] = defaultdict(list)
    for action in snapshot["actions"]:
        if (
            action.get("enabled")
            and action.get("safety_level") == "read_only"
            and action.get("scope") == "tui_affordance"
        ):
            actions_by_entity[action["target_entity_id"]].append(action["label"])
    return {entity_id: tuple(labels) for entity_id, labels in actions_by_entity.items()}


def build_dashboard_snapshot(*, service: ResearchOpsService | None = None) -> DashboardSnapshot:
    """Build the v1a dashboard snapshot from the canonical workflow snapshot."""

    store = service._store if service is not None else None  # noqa: SLF001
    jobs = service._jobs if service is not None else None  # noqa: SLF001
    snapshot = build_canonical_snapshot(store=store, jobs=jobs, max_items=20)
    links_by_entity = _build_links_index(snapshot)
    actions_by_entity = _build_actions_index(snapshot)
    runtime = _build_runtime_record(snapshot)

    run_entities = snapshot["entities"].get("run", [])
    experiment_entities = snapshot["entities"].get("experiment", [])
    insight_entities = snapshot["entities"].get("insight", [])
    paper_entities = snapshot["entities"].get("paper", [])
    context_entities = snapshot["entities"].get("context", [])

    jobs_records = tuple(
        JobRecord(
            entity_id=entity["id"],
            job_id=str(entity["attributes"]["job_id"]),
            run_id=str(entity["id"].removeprefix("run:")),
            name=str(entity["name"]),
            status=_normalized_job_status(str(entity["status"])),
            partition=str(entity["attributes"]["partition"]),
            gpus=int(entity["attributes"]["gpus"]),
            submitted_at=str(entity["attributes"]["submitted_at"]),
            started_at=str(entity["attributes"]["started_at"] or ""),
            completed_at=str(entity["attributes"]["completed_at"] or ""),
            time_limit=str(entity["attributes"]["time_limit"]),
            log_tail=str(entity["attributes"]["log_summary"]["stdout_preview"]),
            error_tail=str(entity["attributes"]["log_summary"]["stderr_preview"] or "(no stderr)"),
        )
        for entity in run_entities
    )

    experiments = tuple(
        ExperimentRecord(
            entity_id=entity["id"],
            experiment_id=str(entity["attributes"]["experiment_id"]),
            name=str(entity["name"]),
            status=_normalized_experiment_status(str(entity["status"])),
            hypothesis=str(entity["summary"]),
            description=str(entity["summary"]),
            dataset=str(entity["attributes"]["dataset"]),
            model_type=str(entity["attributes"]["model_type"]),
            tags=tuple(entity["attributes"]["tags"]),
            updated_at=str(entity["updated_at"]),
            results_summary=summarize_mapping(dict(entity.get("metrics") or {})),
            wandb_run_id=str(entity["attributes"]["wandb_run_id"]),
            slurm_job_id=str(entity["attributes"]["linked_job_id"] or ""),
        )
        for entity in experiment_entities
    )

    status_counts = Counter(experiment.status for experiment in experiments)
    insights = tuple(
        InsightRecord(
            entity_id=entity["id"],
            insight_id=str(entity["id"].removeprefix("insight:")),
            title=str(entity["title"]),
            category=str(entity["attributes"]["category"]),
            confidence=str(entity["attributes"]["confidence"] if entity["attributes"]["confidence"] != "" else "—"),
            content=str(entity["summary"]),
            created_at=str(entity["updated_at"]),
        )
        for entity in insight_entities
    )
    papers = tuple(
        PaperRecord(
            entity_id=entity["id"],
            paper_id=str(entity["id"].removeprefix("paper:")),
            title=str(entity["title"]),
            authors=tuple(entity["attributes"]["authors"]),
            year=str(entity["attributes"]["year"]),
            relevance_notes=str(entity["summary"]),
            added_at=str(entity["updated_at"]),
        )
        for entity in paper_entities
    )
    context_entries = tuple(
        ContextRecord(
            entity_id=entity["id"],
            context_id=str(entity["id"].removeprefix("context:")),
            key=str(entity["attributes"]["key"]),
            context_type=str(entity["attributes"]["context_type"]),
            value=str(entity["summary"]),
            updated_at=str(entity["updated_at"]),
        )
        for entity in context_entities
    )

    return DashboardSnapshot(
        jobs=jobs_records,
        experiments=experiments,
        insights=insights,
        papers=papers,
        context_entries=context_entries,
        experiment_status_counts=dict(status_counts),
        links_by_entity=links_by_entity,
        actions_by_entity=actions_by_entity,
        schema_version=str(snapshot["schema_version"]),
        snapshot_owner=str(snapshot["snapshot_owner"]),
        snapshot_state=str(snapshot["state_semantics"]["snapshot_state"]),
        runtime=runtime,
    )
