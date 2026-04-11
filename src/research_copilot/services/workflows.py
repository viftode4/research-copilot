"""Named workflow orchestration for terminal research-ops tasks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from research_copilot.mcp_servers.knowledge_base import (
    handle_get_experiment,
    handle_set_research_context,
    handle_store_experiment,
    handle_store_insight,
    handle_store_paper,
    handle_update_experiment,
)
from research_copilot.mcp_servers.literature import handle_search_papers
from research_copilot.mcp_servers.slurm import handle_check_job_status, handle_get_job_logs, handle_submit_job
from research_copilot.research_state import (
    build_provenance,
    load_onboarding_contract,
    save_onboarding_contract,
    save_review_artifact,
    save_run_artifact,
    utc_now_iso,
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


def _parse_metrics(log_text: str) -> dict[str, float | str]:
    metrics: dict[str, float | str] = {}
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        if not key:
            continue
        try:
            metrics[key] = float(value)
        except ValueError:
            if value:
                metrics[key] = value
    return metrics


def _parse_structured_metrics(stdout: str) -> dict[str, Any]:
    stripped = stdout.strip()
    if not stripped:
        return {}
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return _parse_metrics(stdout)
    return payload if isinstance(payload, dict) else {}


async def triage(*, max_items: int = 5) -> dict[str, Any]:
    """Summarize the current state and suggest the next explicit workflow."""

    snapshot = build_workflow_snapshot(max_items=max_items)
    jobs = snapshot["jobs"]
    experiments = snapshot["experiments"]
    knowledge = snapshot["knowledge"]
    onboarding = load_onboarding_contract()

    blockers: list[str] = []
    if jobs["active"]:
        blockers.append(f"{jobs['active']} active job(s) need monitoring.")
    if experiments["by_status"].get("failed", 0):
        blockers.append(f"{experiments['by_status']['failed']} failed experiment(s) need review.")
    if not blockers:
        blockers.append("No active blockers detected.")

    if jobs["active"]:
        suggested_next_action = "monitor-run"
    elif onboarding and experiments["total"] == 0:
        suggested_next_action = "run-experiment"
    elif onboarding and onboarding.get("active_profile") == "overfit-hunter" and experiments["total"] > 0:
        suggested_next_action = "overfitting-check"
    elif onboarding and onboarding.get("active_profile") == "result-reasoner" and experiments["total"] > 0:
        suggested_next_action = "review-results"
    elif experiments["total"] == 0:
        suggested_next_action = "launch-experiment"
    elif knowledge["papers_total"] == 0 and knowledge["context_total"] == 0:
        suggested_next_action = "research-context"
    else:
        suggested_next_action = "review-results"

    return {
        "workflow": "triage",
        "snapshot": snapshot,
        "onboarding": onboarding,
        "blockers": blockers,
        "suggested_next_action": suggested_next_action,
    }


async def launch_experiment(
    *,
    name: str,
    script: str,
    hypothesis: str = "",
    description: str = "",
    config: str = "",
    dataset: str = "",
    model_type: str = "",
    tags: list[str] | None = None,
    created_by: str = "codex",
    actor_type: str = "codex",
    partition: str = "gpu",
    gpus: int = 1,
    time_limit: str = "04:00:00",
) -> dict[str, Any]:
    """Register an experiment, submit its job, and link the resulting records."""

    stored = await handle_store_experiment(
        {
            "name": name,
            "hypothesis": hypothesis,
            "description": description,
            "config": config,
            "status": "planned",
            "dataset": dataset,
            "model_type": model_type,
            "tags": json.dumps(tags or []),
            "created_by": created_by,
            "actor_type": actor_type,
            "workflow_name": "launch-experiment",
        }
    )
    experiment_id = _decode_response(stored)["id"]

    submitted = await handle_submit_job(
        {
            "job_name": name,
            "script": script,
            "partition": partition,
            "gpus": gpus,
            "time_limit": time_limit,
            "submitted_by": created_by,
            "workflow_name": "launch-experiment",
            "experiment_id": experiment_id,
        }
    )
    job = _decode_response(submitted)

    await handle_update_experiment(
        {
            "experiment_id": experiment_id,
            "status": "running",
            "slurm_job_id": job["job_id"],
            "actor_type": actor_type,
            "workflow_name": "launch-experiment",
        }
    )
    experiment = _decode_response(await handle_get_experiment({"experiment_id": experiment_id}))

    return {
        "workflow": "launch-experiment",
        "experiment": experiment,
        "job": job,
        "summary": f"Experiment '{name}' registered and linked to job {job['job_id']}.",
    }


async def monitor_run(
    *,
    identifier: str | None = None,
    kind: str = "auto",
    lines: int = 20,
    actor_type: str = "codex",
) -> dict[str, Any]:
    """Refresh a job or experiment and return the latest execution context."""

    experiment: dict[str, Any] | None = None
    job_id: str | None = None
    experiment_id: str | None = None

    if not identifier:
        raise ValueError("monitor-run requires a job or experiment identifier")

    if kind in {"auto", "experiment"}:
        experiment_candidate = _decode_response(
            await handle_get_experiment({"experiment_id": identifier})
        )
        if experiment_candidate.get("id"):
            experiment = experiment_candidate
            experiment_id = identifier
            job_id = experiment.get("slurm_job_id") or None

    if kind in {"auto", "job"} and not job_id:
        job_id = identifier

    if not job_id:
        raise ValueError("No linked job was found for the supplied identifier")

    job = _decode_response(await handle_check_job_status({"job_id": job_id}))
    logs = _decode_response(await handle_get_job_logs({"job_id": job_id, "lines": lines}))
    metrics = _parse_metrics(logs.get("stdout", ""))

    if experiment_id and job.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
        await handle_update_experiment(
            {
                "experiment_id": experiment_id,
                "status": job["status"].lower(),
                "results": json.dumps(metrics) if metrics else "",
                "slurm_job_id": job_id,
                "actor_type": actor_type,
                "workflow_name": "monitor-run",
            }
        )
        experiment = _decode_response(await handle_get_experiment({"experiment_id": experiment_id}))

    return {
        "workflow": "monitor-run",
        "job": job,
        "logs": logs,
        "experiment": experiment,
        "metrics": metrics,
        "snapshot": build_workflow_snapshot(),
    }


async def review_results(
    *,
    experiment_id: str,
    insight_title: str = "",
    insight_content: str = "",
    context_key: str = "",
    context_value: str = "",
    context_type: str = "note",
    created_by: str = "codex",
    actor_type: str = "codex",
) -> dict[str, Any]:
    """Inspect a finished experiment and optionally persist insight/context notes."""

    experiment = _decode_response(await handle_get_experiment({"experiment_id": experiment_id}))
    if not experiment.get("id"):
        raise ValueError(f"Experiment '{experiment_id}' was not found")

    stored_insight: dict[str, Any] | None = None
    if insight_title and insight_content:
        stored_insight = _decode_response(
            await handle_store_insight(
                {
                    "title": insight_title,
                    "content": insight_content,
                    "category": "finding",
                    "experiment_id": experiment_id,
                    "created_by": created_by,
                    "actor_type": actor_type,
                    "workflow_name": "review-results",
                    "linked_job_id": experiment.get("slurm_job_id", ""),
                }
            )
        )

    stored_context: dict[str, Any] | None = None
    if context_key and context_value:
        stored_context = _decode_response(
            await handle_set_research_context(
                {
                    "key": context_key,
                    "value": context_value,
                    "context_type": context_type,
                    "actor_type": actor_type,
                    "workflow_name": "review-results",
                    "linked_experiment_id": experiment_id,
                    "linked_job_id": experiment.get("slurm_job_id", ""),
                }
            )
        )

    return {
        "workflow": "review-results",
        "experiment": experiment,
        "stored_insight": stored_insight,
        "stored_context": stored_context,
        "result_keys": sorted((experiment.get("results") or {}).keys()),
    }


async def research_context(
    *,
    query: str,
    max_results: int = 5,
    save_first: bool = False,
    relevance_notes: str = "",
    context_key: str = "",
    context_value: str = "",
    context_type: str = "note",
    actor_type: str = "codex",
) -> dict[str, Any]:
    """Search literature and optionally persist a paper/context note."""

    search_result = _decode_response(
        await handle_search_papers({"query": query, "max_results": max_results, "sources": "both"})
    )
    papers = [
        paper
        for paper in search_result.get("papers", [])
        if isinstance(paper, dict) and not paper.get("error")
    ]

    saved_paper: dict[str, Any] | None = None
    if save_first and papers:
        first = papers[0]
        saved_paper = _decode_response(
            await handle_store_paper(
                {
                    "title": first.get("title", query),
                    "authors": json.dumps(first.get("authors", [])),
                    "abstract": first.get("full_abstract", first.get("abstract", "")),
                    "arxiv_id": first.get("arxiv_id", ""),
                    "year": first.get("year"),
                    "url": first.get("url", first.get("pdf_url", "")),
                    "relevance_notes": relevance_notes,
                    "tags": json.dumps(first.get("categories", [])),
                    "actor_type": actor_type,
                    "workflow_name": "research-context",
                }
            )
        )

    stored_context: dict[str, Any] | None = None
    if context_key and context_value:
        stored_context = _decode_response(
            await handle_set_research_context(
                {
                    "key": context_key,
                    "value": context_value,
                    "context_type": context_type,
                    "actor_type": actor_type,
                    "workflow_name": "research-context",
                }
            )
        )

    return {
        "workflow": "research-context",
        "query": query,
        "papers_total": len(papers),
        "papers": papers,
        "saved_paper": saved_paper,
        "stored_context": stored_context,
    }


async def onboard(
    *,
    goal: str,
    success_criteria: str,
    active_profile: str,
    autonomy_level: str,
    allowed_actions: list[str] | None = None,
    constraints: list[str] | None = None,
    stop_conditions: list[str] | None = None,
    notes: str = "",
    actor_type: str = "human",
) -> dict[str, Any]:
    """Persist the current solo research onboarding contract."""

    allowed = [item for item in (allowed_actions or []) if item]
    limits = [item for item in (constraints or []) if item]
    stops = [item for item in (stop_conditions or []) if item]
    contract = save_onboarding_contract(
        goal=goal,
        success_criteria=success_criteria,
        active_profile=active_profile,
        autonomy_level=autonomy_level,
        allowed_actions=allowed,
        constraints=limits,
        stop_conditions=stops,
        notes=notes,
        actor=actor_type,
    )

    await handle_set_research_context(
        {
            "key": "current_goal",
            "value": goal,
            "context_type": "goal",
            "actor_type": actor_type,
            "workflow_name": "onboard",
        }
    )
    await handle_set_research_context(
        {
            "key": "active_profile",
            "value": active_profile,
            "context_type": "plan",
            "actor_type": actor_type,
            "workflow_name": "onboard",
        }
    )

    return {
        "workflow": "onboard",
        "contract": contract,
        "next_steps": [
            "workflow triage",
            "workflow launch-experiment",
            f"ultrawork run {active_profile}",
        ],
    }


async def onboarding_summary() -> dict[str, Any]:
    """Return the persisted onboarding contract and its immediate next step."""
    contract = load_onboarding_contract()
    if not contract:
        return {
            "workflow": "onboard-show",
            "configured": False,
            "message": "No onboarding contract found yet.",
        }

    return {
        "workflow": "onboard-show",
        "configured": True,
        "contract": contract,
        "next_steps": [
            "workflow triage",
            "workflow launch-experiment",
            f"ultrawork run {contract.get('active_profile', '')}".strip(),
        ],
    }


async def run_experiment(
    *,
    command: str,
    experiment_id: str = "",
    name: str = "",
    hypothesis: str = "",
    description: str = "",
    config: str = "",
    dataset: str = "",
    model_type: str = "",
    tags: list[str] | None = None,
    created_by: str = "human",
    actor_type: str = "human",
) -> dict[str, Any]:
    """Run a local experiment command and persist a file-backed run artifact."""
    if not experiment_id:
        stored = await handle_store_experiment(
            {
                "name": name or f"local-run-{uuid4().hex[:8]}",
                "hypothesis": hypothesis,
                "description": description,
                "config": config,
                "status": "running",
                "dataset": dataset,
                "model_type": model_type,
                "tags": json.dumps(tags or []),
                "created_by": created_by,
                "actor_type": actor_type,
                "workflow_name": "run-experiment",
            }
        )
        experiment_id = _decode_response(stored)["id"]

    run_id = f"run-{uuid4().hex[:12]}"
    command_result = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
        cwd=Path.cwd(),
        check=False,
    )
    metrics = _parse_structured_metrics(command_result.stdout)
    status = "completed" if command_result.returncode == 0 else "failed"
    updated_at = utc_now_iso()
    run_payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "experiment_id": experiment_id,
        "command": command,
        "status": status,
        "exit_code": command_result.returncode,
        "updated_at": updated_at,
        "provenance": build_provenance(
            {
                "actor_type": actor_type,
                "workflow_name": "run-experiment",
                "linked_experiment_id": experiment_id,
                "linked_run_id": run_id,
            },
            timestamp=updated_at,
        ),
    }
    await handle_update_experiment(
        {
            "experiment_id": experiment_id,
            "status": status,
            "results": json.dumps(metrics) if metrics else "",
            "actor_type": actor_type,
            "workflow_name": "run-experiment",
        }
    )
    experiment = _decode_response(await handle_get_experiment({"experiment_id": experiment_id}))
    return {
        "workflow": "run-experiment",
        "run": save_run_artifact(
            run_id,
            run_payload,
            stdout=command_result.stdout,
            stderr=command_result.stderr,
            metrics=metrics,
        ),
        "experiment": experiment,
        "metrics": metrics,
        "stdout": command_result.stdout,
        "stderr": command_result.stderr,
    }


async def overfitting_check(
    *,
    experiment_id: str,
    train_key: str = "train_loss",
    validation_key: str = "val_loss",
    test_key: str = "test_loss",
    actor_type: str = "codex",
) -> dict[str, Any]:
    """Inspect an experiment result payload for simple overfitting signals."""
    experiment = _decode_response(await handle_get_experiment({"experiment_id": experiment_id}))
    if not experiment.get("id"):
        raise ValueError(f"Experiment '{experiment_id}' was not found")
    results = experiment.get("results") or {}
    diagnostics: list[str] = []
    score_gaps: dict[str, float] = {}

    if train_key in results and validation_key in results:
        try:
            gap = float(results[validation_key]) - float(results[train_key])
            score_gaps["validation_gap"] = gap
            diagnostics.append(
                "Potential overfitting detected." if gap > 0.05 else "No strong overfitting signal in validation gap."
            )
        except (TypeError, ValueError):
            diagnostics.append("Could not parse train/validation metrics.")
    else:
        diagnostics.append("Missing train/validation metrics for direct overfitting diagnosis.")

    if validation_key in results and test_key in results:
        try:
            test_gap = float(results[test_key]) - float(results[validation_key])
            score_gaps["test_gap"] = test_gap
        except (TypeError, ValueError):
            diagnostics.append("Could not parse validation/test metrics.")

    review_payload = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "kind": "overfitting-check",
        "diagnostics": diagnostics,
        "score_gaps": score_gaps,
        "provenance": build_provenance(
            {
                "actor_type": actor_type,
                "workflow_name": "overfitting-check",
                "linked_experiment_id": experiment_id,
            },
            content_kind="inferred",
        ),
    }
    save_review_artifact(f"overfitting-{experiment_id}", review_payload)
    return {"workflow": "overfitting-check", "experiment": experiment, "review": review_payload}


async def next_step(
    *,
    experiment_id: str,
    actor_type: str = "codex",
) -> dict[str, Any]:
    """Generate and persist a simple next-step recommendation for an experiment."""
    experiment = _decode_response(await handle_get_experiment({"experiment_id": experiment_id}))
    if not experiment.get("id"):
        raise ValueError(f"Experiment '{experiment_id}' was not found")

    results = experiment.get("results") or {}
    suggestions: list[str] = []
    if "val_loss" in results and "test_nll" in results:
        suggestions.append("Compare this run against the current baseline and repeat with a different seed.")
    else:
        suggestions.append("Collect richer metrics before deciding on the next experiment.")
    if experiment.get("status") == "failed":
        suggestions.insert(0, "Inspect logs and reduce scope before rerunning.")

    review_payload = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "kind": "next-step",
        "suggestions": suggestions,
        "provenance": build_provenance(
            {
                "actor_type": actor_type,
                "workflow_name": "next-step",
                "linked_experiment_id": experiment_id,
            },
            content_kind="inferred",
        ),
    }
    save_review_artifact(f"next-step-{experiment_id}", review_payload)
    return {"workflow": "next-step", "experiment": experiment, "review": review_payload}
