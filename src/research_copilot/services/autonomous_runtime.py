"""Persistent local file-backed autonomous runtime service."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from uuid import uuid4

from research_copilot.research_state import (
    AUTONOMOUS_RUNTIME_ACTIVE_STATUSES,
    append_autonomous_runtime_event,
    archive_autonomous_runtime,
    autonomous_runtime_is_active,
    autonomous_runtime_is_resumable,
    autonomous_runtime_is_stale,
    autonomous_runtime_is_terminal,
    build_provenance,
    claim_autonomous_runtime_lease,
    load_autonomous_runtime,
    load_autonomous_runtime_history,
    load_named_artifact,
    load_onboarding_contract,
    mark_autonomous_runtime_stale,
    mint_owner_token,
    save_autonomous_runtime,
    utc_now_iso,
)
from research_copilot.services.workflow_snapshot import build_workflow_snapshot
from research_copilot.services.workflows import (
    next_step,
    overfitting_check,
    review_results,
    run_experiment,
)

DEFAULT_AUTONOMOUS_PROFILE = "goal-chaser"
DEFAULT_AUTONOMY_LEVEL = "bounded"
DEFAULT_LEASE_DURATION_SECONDS = 30
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
LOCAL_FILE_BACKED_ACTIONS = frozenset(
    {"run-experiment", "review-results", "next-step", "overfitting-check"}
)
ACTION_ALIASES = {
    "create experiments": "run-experiment",
    "launch runs": "run-experiment",
    "run experiments": "run-experiment",
    "run experiment": "run-experiment",
    "review results": "review-results",
    "review result": "review-results",
    "next step": "next-step",
    "next steps": "next-step",
    "overfitting check": "overfitting-check",
    "overfitting checks": "overfitting-check",
}
FAILED_RUNS_PATTERN = re.compile(r"(\d+)\s+failed\s+(?:run|runs|experiment|experiments)", re.I)
ITERATIONS_PATTERN = re.compile(r"(\d+)\s+iteration", re.I)
PLACEHOLDER_PATTERN = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\$\{([A-Za-z_][A-Za-z0-9_]*)\}"
)


def _string(value: Any) -> str:
    return str(value or "").strip()


def _string_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return slug or "autonomous-run"


def _normalize_action_name(value: str) -> str:
    normalized = _string(value).lower().replace("_", "-")
    return ACTION_ALIASES.get(normalized, normalized)


def _normalize_allowed_actions(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw_value in _string_list(values):
        action = _normalize_action_name(raw_value)
        if action in LOCAL_FILE_BACKED_ACTIONS and action not in normalized:
            normalized.append(action)
    return normalized


def _extract_threshold(values: list[str], pattern: re.Pattern[str], default: int) -> int:
    for raw_value in values:
        match = pattern.search(raw_value)
        if match:
            return max(1, int(match.group(1)))
    return default


def _resolve_failure_limit(stop_conditions: list[str], explicit_limit: int | None = None) -> int:
    if explicit_limit is not None and explicit_limit > 0:
        return explicit_limit
    return _extract_threshold(stop_conditions, FAILED_RUNS_PATTERN, DEFAULT_MAX_CONSECUTIVE_FAILURES)


def _resolve_max_iterations(explicit_limit: int, stop_conditions: list[str]) -> int:
    if explicit_limit > 0:
        return explicit_limit
    return _extract_threshold(stop_conditions, ITERATIONS_PATTERN, 0)


def _public_runtime(runtime: dict[str, Any], *, include_owner_token: bool = False) -> dict[str, Any]:
    payload = dict(runtime)
    if not include_owner_token:
        payload.pop("owner_token", None)
    return payload


def _persist_runtime(
    runtime: dict[str, Any],
    *,
    event_type: str = "",
    event_details: dict[str, Any] | None = None,
    archive: bool = False,
) -> dict[str, Any]:
    saved = save_autonomous_runtime(runtime)
    if event_type:
        append_autonomous_runtime_event(
            _string(saved.get("run_id")),
            {
                "event_type": event_type,
                "status": _string(saved.get("status")),
                "phase": _string(saved.get("current_phase")),
                "summary": _string(saved.get("summary")),
                **_dict(event_details),
            },
        )
    if archive or _string(saved.get("status")).lower() not in AUTONOMOUS_RUNTIME_ACTIVE_STATUSES:
        archive_autonomous_runtime(saved)
    return saved


def _load_runtime_for_run(run_id: str = "") -> dict[str, Any]:
    active = load_autonomous_runtime()
    if active and (not run_id or _string(active.get("run_id")) == _string(run_id)):
        return active
    if run_id:
        archived = load_autonomous_runtime_history(run_id)
        if archived:
            return archived
    return {}


def _mark_stale_if_needed(runtime: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    if not runtime or not autonomous_runtime_is_stale(runtime):
        return runtime
    if _string(runtime.get("status")).lower() == "stale":
        return runtime
    stale = mark_autonomous_runtime_stale(runtime)
    if not persist:
        return stale
    return _persist_runtime(
        stale,
        event_type="runtime.stale",
        event_details={"reason": _string(stale.get("stop_reason"))},
        archive=True,
    )


def _assert_runtime_owner(
    runtime: dict[str, Any],
    owner_token: str,
    owner_instance_id: str = "",
) -> None:
    current_token = _string(runtime.get("owner_token"))
    if not current_token or current_token != _string(owner_token):
        raise RuntimeError("Autonomous runtime ownership validation failed.")
    if owner_instance_id:
        current_instance_id = _string(runtime.get("owner_instance_id"))
        if current_instance_id and current_instance_id != _string(owner_instance_id):
            raise RuntimeError("Autonomous runtime worker instance validation failed.")


def _allowed_action_set(runtime: dict[str, Any]) -> set[str]:
    allowed_actions = _normalize_allowed_actions(runtime.get("allowed_actions"))
    return set(allowed_actions or LOCAL_FILE_BACKED_ACTIONS)


def _constraints_block_action(action: str, constraints: list[str]) -> str:
    lowered_constraints = [item.lower() for item in constraints]
    if action == "run-experiment":
        blocking_terms = ("review only", "results only", "no new experiments", "do not run")
        if any(term in constraint for term in blocking_terms for constraint in lowered_constraints):
            return "constraints block new local experiments"
    if action in {"review-results", "next-step", "overfitting-check"}:
        blocking_terms = ("no reviews", "run only", "experiments only")
        if any(term in constraint for term in blocking_terms for constraint in lowered_constraints):
            return "constraints block review actions"
    return ""


def _review_artifact(kind: str, experiment_id: str) -> dict[str, Any]:
    return load_named_artifact("reviews", f"{kind}-{experiment_id}")


def _render_command(command_template: str, template_vars: dict[str, Any]) -> str:
    template = _string(command_template)
    if not template:
        return ""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or ""
        value = template_vars.get(key)
        if value in (None, ""):
            return match.group(0)
        return str(value)

    return PLACEHOLDER_PATTERN.sub(replace, template)


def _build_run_inputs(runtime: dict[str, Any]) -> dict[str, Any]:
    template_vars = _dict(runtime.get("template_vars"))
    next_iteration = int(runtime.get("iteration") or 0) + 1
    dynamic_vars = {
        "goal": _string(runtime.get("goal")),
        "profile_name": _string(runtime.get("profile_name")),
        "iteration": next_iteration,
        "run_id": _string(runtime.get("run_id")),
    }
    resolved_vars = {**template_vars, **dynamic_vars}
    command = _render_command(_string(runtime.get("command_template")), resolved_vars)
    default_name = f"{_slugify(_string(runtime.get('goal')))}-iter-{next_iteration}"
    return {
        "command": command,
        "name": _string(template_vars.get("name")) or default_name,
        "hypothesis": _string(template_vars.get("hypothesis")) or _string(runtime.get("goal")),
        "dataset": _string(template_vars.get("dataset")),
        "model_type": _string(template_vars.get("model_type")),
    }


def _normalize_action_envelope(
    *,
    profile_name: str,
    command_template: str,
    template_vars: dict[str, Any],
    action_envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    existing = _dict(action_envelope)
    latest_action = _dict(existing.get("latest_suggested_action"))
    default_action = "run-experiment" if _string(command_template) else _string(
        latest_action.get("action")
    )
    return {
        "schema_version": "1.0",
        "mode": "local-file-backed-v1",
        "profile_name": profile_name,
        "mutation_policy": {
            "local_only": True,
            "job_backed_monitoring": False,
            "supported_actions": sorted(LOCAL_FILE_BACKED_ACTIONS),
        },
        "seed_command_template": _string(existing.get("seed_command_template"))
        or _string(command_template),
        "seed_template_vars": template_vars,
        "last_reviewed_experiment_id": _string(existing.get("last_reviewed_experiment_id")),
        "last_next_step_experiment_id": _string(existing.get("last_next_step_experiment_id")),
        "last_overfitting_check_experiment_id": _string(
            existing.get("last_overfitting_check_experiment_id")
        ),
        "latest_suggested_action": {
            "action": default_action,
            "inputs": _dict(latest_action.get("inputs")),
            "reason": _string(latest_action.get("reason")),
        },
    }


def _initial_latest_action(runtime: dict[str, Any]) -> dict[str, Any]:
    if _string(runtime.get("command_template")):
        return {
            "action": "run-experiment",
            "inputs": {
                key: value
                for key, value in _build_run_inputs(runtime).items()
                if key != "command"
            },
            "reason": "No experiments exist yet; start from the persisted local command template.",
        }
    return {
        "action": "",
        "inputs": {},
        "reason": "No executable action is available until the operator provides a command template.",
    }


def _latest_experiment(snapshot: dict[str, Any]) -> dict[str, Any]:
    experiments = _dict(snapshot.get("experiments"))
    items = experiments.get("items") if isinstance(experiments, dict) else []
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            return first
    return {}


def _success_criteria_satisfied(
    runtime: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[bool, str]:
    success_criteria = _string(runtime.get("success_criteria")).lower()
    latest_experiment = _latest_experiment(snapshot)
    experiment_id = _string(latest_experiment.get("id"))
    if not success_criteria:
        return False, ""
    if experiment_id and ("diagnosis" in success_criteria or "overfitting" in success_criteria):
        if _review_artifact("overfitting", experiment_id):
            return True, f"Persisted an overfitting diagnosis for experiment {experiment_id}."
    if experiment_id and ("next step" in success_criteria or "recommendation" in success_criteria):
        if _review_artifact("next-step", experiment_id):
            return True, f"Persisted a next-step recommendation for experiment {experiment_id}."
    completed_total = int(_dict(snapshot.get("experiments")).get("by_status", {}).get("completed", 0))
    if completed_total > 0 and any(
        phrase in success_criteria
        for phrase in ("completed experiment", "completed run", "one completed", "one diagnosis")
    ):
        return True, f"Observed {completed_total} completed experiment(s)."
    return False, ""


def _decide_next_action(runtime: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    allowed_actions = _allowed_action_set(runtime)
    constraints = _string_list(runtime.get("constraints"))
    latest_experiment = _latest_experiment(snapshot)
    envelope = _dict(runtime.get("action_envelope"))
    profile_name = _string(runtime.get("profile_name")) or DEFAULT_AUTONOMOUS_PROFILE
    command_template = _string(runtime.get("command_template"))

    def decision(action: str, reason: str, *, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        if action:
            if action not in allowed_actions:
                return {
                    "action": "",
                    "inputs": {},
                    "reason": f"Policy blocked {action}: it is outside allowed_actions.",
                    "blocked": True,
                }
            blocked_reason = _constraints_block_action(action, constraints)
            if blocked_reason:
                return {
                    "action": "",
                    "inputs": {},
                    "reason": f"Policy blocked {action}: {blocked_reason}.",
                    "blocked": True,
                }
        return {
            "action": action,
            "inputs": inputs or {},
            "reason": reason,
            "blocked": False,
        }

    if not latest_experiment:
        if command_template:
            return decision(
                "run-experiment",
                "No experiments are recorded yet; execute the persisted local command template.",
                inputs=_build_run_inputs(runtime),
            )
        latest_suggested = _dict(envelope.get("latest_suggested_action"))
        fallback_action = _normalize_action_name(_string(latest_suggested.get("action")))
        if fallback_action in {"review-results", "next-step", "overfitting-check"}:
            fallback_inputs = _dict(latest_suggested.get("inputs"))
            return decision(
                fallback_action,
                _string(latest_suggested.get("reason"))
                or "Resume from the persisted executable action envelope.",
                inputs=fallback_inputs,
            )
        return decision(
            "",
            "No executable next action exists inside the persisted envelope; operator input is required.",
        )

    experiment_id = _string(latest_experiment.get("id"))
    experiment_status = _string(latest_experiment.get("status")).lower()

    if experiment_status in {"running", "planned", "queued"}:
        return decision(
            "",
            "The latest experiment is still active; local autonomous v1 does not monitor job-backed runs.",
        )

    if experiment_status == "completed":
        if _string(envelope.get("last_reviewed_experiment_id")) != experiment_id:
            return decision(
                "review-results",
                f"Review completed experiment {experiment_id} before choosing the next bounded step.",
                inputs={"experiment_id": experiment_id},
            )
        if (
            profile_name == "overfit-hunter"
            and _string(envelope.get("last_overfitting_check_experiment_id")) != experiment_id
        ):
            return decision(
                "overfitting-check",
                f"Run the profile-specific overfitting check for experiment {experiment_id}.",
                inputs={"experiment_id": experiment_id},
            )
        if _string(envelope.get("last_next_step_experiment_id")) != experiment_id:
            return decision(
                "next-step",
                f"Persist a next-step recommendation for experiment {experiment_id}.",
                inputs={"experiment_id": experiment_id},
            )
        if command_template:
            return decision(
                "run-experiment",
                "The latest experiment already has review artifacts; continue the bounded loop.",
                inputs=_build_run_inputs(runtime),
            )
        return decision(
            "",
            "The latest experiment has been fully reviewed and no persisted command template remains to continue.",
        )

    if experiment_status == "failed":
        if _string(envelope.get("last_next_step_experiment_id")) != experiment_id:
            return decision(
                "next-step",
                f"Persist a bounded recommendation for failed experiment {experiment_id}.",
                inputs={"experiment_id": experiment_id},
            )
        if command_template:
            return decision(
                "run-experiment",
                "Retry the bounded local command after capturing a next-step recommendation.",
                inputs=_build_run_inputs(runtime),
            )
        return decision(
            "",
            "The latest experiment failed and there is no persisted command template for a bounded retry.",
        )

    if command_template:
        return decision(
            "run-experiment",
            "Fallback to the persisted local command template for the next bounded action.",
            inputs=_build_run_inputs(runtime),
        )
    return decision(
        "",
        "No executable next action exists inside the persisted envelope; operator input is required.",
    )


def _action_status(action: str, result: dict[str, Any]) -> str:
    if action == "run-experiment":
        return _string(_dict(result.get("run")).get("status")) or "failed"
    return "completed"


def _action_experiment_id(action: str, result: dict[str, Any], inputs: dict[str, Any]) -> str:
    experiment = _dict(result.get("experiment"))
    if experiment.get("id"):
        return _string(experiment.get("id"))
    return _string(inputs.get("experiment_id"))


def _action_event_details(action: str, result: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {
        "action": action,
        "experiment_id": _action_experiment_id(action, result, inputs),
    }
    if action == "run-experiment":
        run_payload = _dict(result.get("run"))
        details["run_id"] = _string(run_payload.get("run_id"))
        details["action_status"] = _string(run_payload.get("status"))
        details["exit_code"] = run_payload.get("exit_code")
        details["metric_keys"] = sorted(_dict(result.get("metrics")).keys())
    elif action == "review-results":
        details["result_keys"] = list(result.get("result_keys") or [])
    elif action == "next-step":
        details["suggestions"] = list(_dict(result.get("review")).get("suggestions") or [])[:2]
    elif action == "overfitting-check":
        details["diagnostics"] = list(_dict(result.get("review")).get("diagnostics") or [])[:2]
    return details


def _update_action_envelope(
    runtime: dict[str, Any],
    *,
    action: str,
    inputs: dict[str, Any],
    result: dict[str, Any],
    next_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    envelope = _dict(runtime.get("action_envelope"))
    experiment_id = _action_experiment_id(action, result, inputs)
    if action == "review-results" and experiment_id:
        envelope["last_reviewed_experiment_id"] = experiment_id
    if action == "next-step" and experiment_id:
        envelope["last_next_step_experiment_id"] = experiment_id
    if action == "overfitting-check" and experiment_id:
        envelope["last_overfitting_check_experiment_id"] = experiment_id
    if next_decision is not None:
        envelope["latest_suggested_action"] = {
            "action": _string(next_decision.get("action")),
            "inputs": _dict(next_decision.get("inputs")),
            "reason": _string(next_decision.get("reason")),
        }
    return envelope


async def _execute_action(action: str, inputs: dict[str, Any]) -> dict[str, Any]:
    if action == "run-experiment":
        return await run_experiment(
            command=_string(inputs.get("command")),
            name=_string(inputs.get("name")),
            hypothesis=_string(inputs.get("hypothesis")),
            dataset=_string(inputs.get("dataset")),
            model_type=_string(inputs.get("model_type")),
            actor_type="system",
            created_by="research-copilot",
        )
    if action == "review-results":
        return await review_results(
            experiment_id=_string(inputs.get("experiment_id")),
            actor_type="system",
        )
    if action == "next-step":
        return await next_step(
            experiment_id=_string(inputs.get("experiment_id")),
            actor_type="system",
        )
    if action == "overfitting-check":
        return await overfitting_check(
            experiment_id=_string(inputs.get("experiment_id")),
            actor_type="system",
        )
    raise ValueError(f"Unsupported autonomous action '{action}'.")


def _completed_runtime(
    runtime: dict[str, Any],
    *,
    status: str,
    phase: str,
    summary: str,
    stop_reason: str = "",
) -> dict[str, Any]:
    completed_at = utc_now_iso()
    normalized = dict(runtime)
    normalized["status"] = status
    normalized["current_phase"] = phase
    normalized["summary"] = summary
    normalized["updated_at"] = completed_at
    normalized["completed_at"] = completed_at if status in {"completed", "failed"} else ""
    if stop_reason:
        normalized["stop_reason"] = stop_reason
    return normalized


def _runtime_response(runtime: dict[str, Any], *, include_owner_token: bool = False) -> dict[str, Any]:
    payload = _public_runtime(runtime, include_owner_token=include_owner_token)
    return {
        "run_id": _string(runtime.get("run_id")),
        "status": _string(runtime.get("status")),
        "summary": _string(runtime.get("summary")),
        "owner_token": _string(runtime.get("owner_token")) if include_owner_token else "",
        "runtime": payload,
    }


def _merge_runtime_control_state(runtime: dict[str, Any]) -> dict[str, Any]:
    """Merge operator-controlled runtime fields from the latest persisted state."""

    latest = load_autonomous_runtime()
    if not latest or _string(latest.get("run_id")) != _string(runtime.get("run_id")):
        return runtime
    merged = dict(runtime)
    for key in ("stop_requested_at", "stop_reason"):
        if latest.get(key) not in ("", None):
            merged[key] = latest.get(key)
    if _string(latest.get("status")) == "stopping":
        merged["status"] = "stopping"
    return merged


def _build_runtime_contract(
    *,
    goal: str,
    success_criteria: str,
    profile_name: str,
    autonomy_level: str,
    allowed_actions: list[str],
    constraints: list[str],
    stop_conditions: list[str],
    command_template: str,
    template_vars: dict[str, Any],
    action_envelope: dict[str, Any],
    max_iterations: int,
    failure_limit: int,
    actor_type: str,
) -> dict[str, Any]:
    timestamp = utc_now_iso()
    run_id = f"autonomous-{uuid4().hex[:12]}"
    owner_token = mint_owner_token()
    runtime = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "running",
        "goal": goal,
        "profile_name": profile_name,
        "autonomy_level": autonomy_level,
        "allowed_actions": allowed_actions,
        "constraints": constraints,
        "stop_conditions": stop_conditions,
        "success_criteria": success_criteria,
        "command_template": command_template,
        "template_vars": template_vars,
        "action_envelope": action_envelope,
        "max_iterations": max_iterations,
        "failure_limit": failure_limit,
        "iteration": 0,
        "current_phase": "launching",
        "summary": "Autonomous runtime created; worker launch pending.",
        "last_action": {},
        "last_action_status": "",
        "last_experiment_id": "",
        "owner_token": owner_token,
        "owner_pid": None,
        "lease_expires_at": "",
        "last_heartbeat_at": "",
        "updated_at": timestamp,
        "started_at": timestamp,
        "completed_at": "",
        "stop_requested_at": "",
        "stop_reason": "",
        "consecutive_failures": 0,
        "provenance": build_provenance(
            {
                "actor_type": actor_type,
                "workflow_name": "autonomous-run",
                "profile_name": profile_name,
                "linked_run_id": run_id,
                "content_kind": "inferred",
            },
            content_kind="inferred",
            timestamp=timestamp,
        ),
    }
    runtime["action_envelope"]["latest_suggested_action"] = _initial_latest_action(runtime)
    return runtime


async def autonomous_run(
    *,
    goal: str = "",
    success_criteria: str = "",
    profile_name: str = "",
    active_profile: str = "",
    autonomy_level: str = "",
    allowed_actions: list[str] | None = None,
    constraints: list[str] | None = None,
    stop_conditions: list[str] | None = None,
    command_template: str = "",
    template_vars: dict[str, Any] | None = None,
    action_envelope: dict[str, Any] | None = None,
    max_iterations: int | None = None,
    created_by: str = "codex",
    actor_type: str = "codex",
    actor: str = "",
    spawn_worker: bool = False,
    launch_worker: bool = False,
    detach: bool = False,
) -> dict[str, Any]:
    """Create a persistent autonomous runtime contract for detached execution."""

    del created_by, spawn_worker, launch_worker, detach
    existing = _mark_stale_if_needed(load_autonomous_runtime())
    if existing and autonomous_runtime_is_active(existing):
        raise ValueError("An autonomous runtime is already active.")

    onboarding = load_onboarding_contract()
    resolved_profile = _string(profile_name or active_profile or onboarding.get("active_profile")) or DEFAULT_AUTONOMOUS_PROFILE
    resolved_goal = _string(goal or onboarding.get("goal"))
    resolved_success = _string(success_criteria or onboarding.get("success_criteria"))
    resolved_autonomy = _string(autonomy_level or onboarding.get("autonomy_level")) or DEFAULT_AUTONOMY_LEVEL
    resolved_allowed = _normalize_allowed_actions(allowed_actions or onboarding.get("allowed_actions"))
    if not resolved_allowed:
        resolved_allowed = ["run-experiment", "review-results", "next-step"]
        if resolved_profile == "overfit-hunter":
            resolved_allowed.append("overfitting-check")
    resolved_constraints = _string_list(constraints or onboarding.get("constraints"))
    resolved_stop_conditions = _string_list(stop_conditions or onboarding.get("stop_conditions"))
    resolved_failure_limit = _resolve_failure_limit(resolved_stop_conditions)
    resolved_max_iterations = _resolve_max_iterations(max_iterations or 0, resolved_stop_conditions)
    resolved_template_vars = _dict(template_vars)
    normalized_envelope = _normalize_action_envelope(
        profile_name=resolved_profile,
        command_template=command_template,
        template_vars=resolved_template_vars,
        action_envelope=action_envelope,
    )

    if existing:
        archive_autonomous_runtime(existing)

    runtime = _build_runtime_contract(
        goal=resolved_goal,
        success_criteria=resolved_success,
        profile_name=resolved_profile,
        autonomy_level=resolved_autonomy,
        allowed_actions=resolved_allowed,
        constraints=resolved_constraints,
        stop_conditions=resolved_stop_conditions,
        command_template=_string(command_template),
        template_vars=resolved_template_vars,
        action_envelope=normalized_envelope,
        max_iterations=resolved_max_iterations,
        failure_limit=resolved_failure_limit,
        actor_type=actor or actor_type,
    )
    saved = _persist_runtime(
        runtime,
        event_type="runtime.created",
        event_details={"status": "running", "phase": "launching"},
    )
    return _runtime_response(saved, include_owner_token=True)


async def autonomous_status(*, run_id: str = "", runtime_id: str = "") -> dict[str, Any]:
    """Inspect the persisted runtime state without mutating workflow state."""

    resolved_run_id = _string(run_id or runtime_id)
    runtime = _mark_stale_if_needed(_load_runtime_for_run(resolved_run_id), persist=False)
    if not runtime:
        return {"run_id": resolved_run_id, "status": "unavailable", "runtime": {}, "available": False}
    payload = _runtime_response(runtime)
    payload["available"] = True
    payload["resumable"] = autonomous_runtime_is_resumable(runtime)
    payload["active"] = autonomous_runtime_is_active(runtime)
    payload["stale"] = autonomous_runtime_is_stale(runtime)
    return payload


async def autonomous_stop(
    *,
    run_id: str = "",
    runtime_id: str = "",
    reason: str = "",
    stop_reason: str = "",
    owner_token: str = "",
    token: str = "",
    created_by: str = "codex",
    actor_type: str = "codex",
    actor: str = "",
) -> dict[str, Any]:
    """Request a graceful stop for the persisted autonomous runtime."""

    del created_by
    runtime = _mark_stale_if_needed(_load_runtime_for_run(run_id or runtime_id))
    if not runtime:
        raise ValueError("No autonomous runtime was found.")
    resolved_token = _string(owner_token or token)
    if not resolved_token:
        raise ValueError("owner_token is required to stop the autonomous runtime.")
    _assert_runtime_owner(runtime, resolved_token)
    if autonomous_runtime_is_terminal(runtime):
        return _runtime_response(runtime)
    resolved_reason = _string(stop_reason or reason) or "Graceful stop requested."
    runtime["stop_requested_at"] = utc_now_iso()
    runtime["stop_reason"] = resolved_reason
    runtime["summary"] = resolved_reason
    if autonomous_runtime_is_active(runtime):
        runtime["status"] = "stopping"
        runtime["current_phase"] = "stopping"
    saved = _persist_runtime(
        runtime,
        event_type="runtime.stop_requested",
        event_details={"reason": resolved_reason},
    )
    return _runtime_response(saved)


async def autonomous_resume(
    *,
    run_id: str = "",
    runtime_id: str = "",
    owner_token: str = "",
    token: str = "",
    created_by: str = "codex",
    actor_type: str = "codex",
    actor: str = "",
    spawn_worker: bool = False,
    launch_worker: bool = False,
    detach: bool = False,
) -> dict[str, Any]:
    """Resume a stopped or stale runtime with a fresh owner token."""

    del created_by, spawn_worker, launch_worker, detach
    runtime = _mark_stale_if_needed(_load_runtime_for_run(run_id or runtime_id))
    if not runtime:
        raise ValueError("No autonomous runtime was found.")
    resolved_token = _string(owner_token or token)
    if not resolved_token:
        raise ValueError("owner_token is required to resume the autonomous runtime.")
    _assert_runtime_owner(runtime, resolved_token)
    if not autonomous_runtime_is_resumable(runtime):
        raise ValueError(f"Runtime status '{_string(runtime.get('status'))}' is not resumable.")

    runtime["status"] = "running"
    runtime["current_phase"] = "launching"
    runtime["summary"] = "Autonomous runtime resumed; worker launch pending."
    runtime["owner_token"] = mint_owner_token()
    runtime["owner_instance_id"] = ""
    runtime["owner_pid"] = None
    runtime["lease_expires_at"] = ""
    runtime["last_heartbeat_at"] = ""
    runtime["updated_at"] = utc_now_iso()
    runtime["completed_at"] = ""
    runtime["stop_requested_at"] = ""
    runtime["stop_reason"] = ""
    saved = _persist_runtime(
        runtime,
        event_type="runtime.resumed",
        event_details={"status": "running", "phase": "launching"},
    )
    return _runtime_response(saved, include_owner_token=True)


async def run_autonomous_worker(
    run_id: str,
    owner_token: str,
    token: str = "",
    owner_instance_id: str = "",
    worker_instance_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Detached worker entrypoint for the persistent autonomous runtime."""

    resolved_token = _string(owner_token or token)
    resolved_instance_id = _string(owner_instance_id or worker_instance_id or session_id)
    runtime = _mark_stale_if_needed(_load_runtime_for_run(run_id))
    if not runtime:
        raise RuntimeError(f"Autonomous runtime '{run_id}' was not found.")
    runtime = claim_autonomous_runtime_lease(
        runtime,
        owner_token=resolved_token,
        owner_instance_id=resolved_instance_id,
        owner_pid=os.getpid(),
        lease_duration_seconds=DEFAULT_LEASE_DURATION_SECONDS,
        allow_takeover=autonomous_runtime_is_resumable(runtime),
    )
    runtime["status"] = "running"
    runtime["current_phase"] = "loop"
    runtime["summary"] = "Autonomous runtime worker started."
    runtime = _persist_runtime(runtime, event_type="runtime.worker_started")

    while True:
        runtime = _mark_stale_if_needed(load_autonomous_runtime())
        if not runtime:
            raise RuntimeError("Autonomous runtime state disappeared during worker execution.")
        _assert_runtime_owner(runtime, resolved_token, resolved_instance_id)
        runtime = claim_autonomous_runtime_lease(
            runtime,
            owner_token=resolved_token,
            owner_instance_id=resolved_instance_id,
            owner_pid=os.getpid(),
            lease_duration_seconds=DEFAULT_LEASE_DURATION_SECONDS,
        )

        if _string(runtime.get("stop_requested_at")):
            stopped = _completed_runtime(
                runtime,
                status="stopped",
                phase="stopped",
                summary=_string(runtime.get("stop_reason")) or "Graceful stop completed.",
                stop_reason=_string(runtime.get("stop_reason")) or "Graceful stop completed.",
            )
            stopped = _persist_runtime(stopped, event_type="runtime.stopped", archive=True)
            return _runtime_response(stopped)

        max_iterations = int(runtime.get("max_iterations") or 0)
        if max_iterations > 0 and int(runtime.get("iteration") or 0) >= max_iterations:
            stopped = _completed_runtime(
                runtime,
                status="stopped",
                phase="stopped",
                summary="Reached the configured max iteration limit.",
                stop_reason="Reached the configured max iteration limit.",
            )
            stopped = _persist_runtime(stopped, event_type="runtime.max_iterations", archive=True)
            return _runtime_response(stopped)

        snapshot = build_workflow_snapshot(max_items=5)
        satisfied, reason = _success_criteria_satisfied(runtime, snapshot)
        if satisfied:
            completed = _completed_runtime(
                runtime,
                status="completed",
                phase="completed",
                summary=reason,
                stop_reason=reason,
            )
            completed = _persist_runtime(completed, event_type="runtime.completed", archive=True)
            return _runtime_response(completed)

        decision = _decide_next_action(runtime, snapshot)
        action = _string(decision.get("action"))
        inputs = _dict(decision.get("inputs"))
        reason = _string(decision.get("reason"))
        if not action:
            stopped = _completed_runtime(
                runtime,
                status="stopped",
                phase="blocked",
                summary=reason,
                stop_reason=reason,
            )
            stopped["action_envelope"] = _update_action_envelope(
                stopped,
                action="",
                inputs={},
                result={},
                next_decision=decision,
            )
            stopped = _persist_runtime(stopped, event_type="runtime.blocked", archive=True)
            return _runtime_response(stopped)

        runtime["current_phase"] = f"executing:{action}"
        runtime["summary"] = reason
        runtime = _persist_runtime(
            runtime,
            event_type="runtime.action_selected",
            event_details={"action": action, "reason": reason, "inputs": inputs},
        )

        try:
            result = await _execute_action(action, inputs)
        except Exception as exc:
            runtime = _merge_runtime_control_state(runtime)
            runtime["consecutive_failures"] = int(runtime.get("consecutive_failures") or 0) + 1
            runtime["last_action"] = {"action": action, "inputs": inputs, "reason": reason}
            runtime["last_action_status"] = "failed"
            runtime["summary"] = str(exc)
            runtime["updated_at"] = utc_now_iso()
            failed = _persist_runtime(
                runtime,
                event_type="runtime.action_failed",
                event_details={"action": action, "error": str(exc)},
            )
            if int(failed.get("consecutive_failures") or 0) >= int(failed.get("failure_limit") or DEFAULT_MAX_CONSECUTIVE_FAILURES):
                terminal = _completed_runtime(
                    failed,
                    status="failed",
                    phase="failed",
                    summary=f"Repeated failure threshold reached: {exc}",
                    stop_reason=f"Repeated failure threshold reached: {exc}",
                )
                terminal = _persist_runtime(terminal, event_type="runtime.failed", archive=True)
                return _runtime_response(terminal)
            if _string(failed.get("stop_requested_at")):
                stopped = _completed_runtime(
                    failed,
                    status="stopped",
                    phase="stopped",
                    summary=_string(failed.get("stop_reason")) or "Stop requested by operator.",
                    stop_reason=_string(failed.get("stop_reason")) or "Stop requested by operator.",
                )
                stopped = _persist_runtime(stopped, event_type="runtime.stopped", archive=True)
                return _runtime_response(stopped)
        else:
            runtime = _merge_runtime_control_state(runtime)
            action_status = _action_status(action, result)
            experiment_id = _action_experiment_id(action, result, inputs)
            runtime["iteration"] = int(runtime.get("iteration") or 0) + 1
            runtime["last_action"] = {"action": action, "inputs": inputs, "reason": reason}
            runtime["last_action_status"] = action_status
            runtime["last_experiment_id"] = experiment_id or _string(runtime.get("last_experiment_id"))
            runtime["summary"] = reason
            runtime["consecutive_failures"] = 0 if action_status != "failed" else 1
            next_snapshot = build_workflow_snapshot(max_items=5)
            next_decision = _decide_next_action(runtime, next_snapshot)
            runtime["action_envelope"] = _update_action_envelope(
                runtime,
                action=action,
                inputs=inputs,
                result=result,
                next_decision=next_decision,
            )
            runtime = _persist_runtime(
                runtime,
                event_type="runtime.action_completed",
                event_details=_action_event_details(action, result, inputs) | {"reason": reason},
            )
            if action_status == "failed" and int(runtime.get("failure_limit") or DEFAULT_MAX_CONSECUTIVE_FAILURES) <= 1:
                terminal = _completed_runtime(
                    runtime,
                    status="failed",
                    phase="failed",
                    summary=f"Run action {action} failed.",
                    stop_reason=f"Run action {action} failed.",
                )
                terminal = _persist_runtime(terminal, event_type="runtime.failed", archive=True)
                return _runtime_response(terminal)

        await asyncio.sleep(DEFAULT_POLL_INTERVAL_SECONDS)


async def _run_iteration(
    *,
    run_id: str,
    owner_token: str,
    owner_instance_id: str = "",
    owner_pid: int,
    lease_duration_seconds: int,
) -> dict[str, Any]:
    runtime = _mark_stale_if_needed(_load_runtime_for_run(run_id))
    if not runtime:
        raise ValueError(f"Autonomous runtime '{run_id}' was not found.")
    _assert_runtime_owner(runtime, owner_token, owner_instance_id)
    if autonomous_runtime_is_terminal(runtime):
        return runtime

    runtime = claim_autonomous_runtime_lease(
        runtime,
        owner_token=owner_token,
        owner_instance_id=owner_instance_id,
        owner_pid=owner_pid,
        lease_duration_seconds=lease_duration_seconds,
    )
    runtime["status"] = "stopping" if _string(runtime.get("stop_requested_at")) else "running"
    runtime["current_phase"] = "triage"
    _persist_runtime(runtime)

    if _string(runtime.get("stop_requested_at")):
        return _persist_runtime(
            _completed_runtime(
                runtime,
                status="stopped",
                phase="stopped",
                summary=_string(runtime.get("stop_reason")) or "Stop requested by operator.",
                stop_reason=_string(runtime.get("stop_reason")) or "Stop requested by operator.",
            ),
            event_type="runtime.stopped",
            archive=True,
        )

    snapshot = build_workflow_snapshot(max_items=5)
    success, success_summary = _success_criteria_satisfied(runtime, snapshot)
    if success:
        return _persist_runtime(
            _completed_runtime(
                runtime,
                status="completed",
                phase="completed",
                summary=success_summary,
                stop_reason=success_summary,
            ),
            event_type="runtime.completed",
            archive=True,
        )

    max_iterations = int(runtime.get("max_iterations") or 0)
    if max_iterations > 0 and int(runtime.get("iteration") or 0) >= max_iterations:
        reason = f"Reached the configured max_iterations limit ({max_iterations})."
        return _persist_runtime(
            _completed_runtime(
                runtime,
                status="stopped",
                phase="stopped",
                summary=reason,
                stop_reason=reason,
            ),
            event_type="runtime.stopped",
            archive=True,
        )

    decision = _decide_next_action(runtime, snapshot)
    runtime["action_envelope"] = _update_action_envelope(
        runtime,
        action="",
        inputs={},
        result={},
        next_decision=decision,
    )
    if not _string(decision.get("action")):
        summary = _string(decision.get("reason"))
        return _persist_runtime(
            _completed_runtime(
                runtime,
                status="stopped",
                phase="stopped",
                summary=summary,
                stop_reason=summary,
            ),
            event_type="runtime.stopped",
            event_details={"policy_blocked": bool(decision.get("blocked"))},
            archive=True,
        )

    action = _string(decision.get("action"))
    inputs = _dict(decision.get("inputs"))
    runtime["current_phase"] = f"executing:{action}"
    runtime["last_action"] = action
    runtime["summary"] = _string(decision.get("reason"))
    _persist_runtime(
        runtime,
        event_type="action.started",
        event_details={"action": action, "inputs": inputs},
    )

    try:
        result = await _execute_action(action, inputs)
        action_status = _action_status(action, result)
        runtime["iteration"] = int(runtime.get("iteration") or 0) + 1
        runtime["last_action_status"] = action_status
        runtime["last_experiment_id"] = _action_experiment_id(action, result, inputs)
        runtime["consecutive_failures"] = (
            int(runtime.get("consecutive_failures") or 0) + 1 if action_status != "completed" else 0
        )
        post_snapshot = build_workflow_snapshot(max_items=5)
        next_decision = _decide_next_action(runtime, post_snapshot)
        runtime["action_envelope"] = _update_action_envelope(
            runtime,
            action=action,
            inputs=inputs,
            result=result,
            next_decision=next_decision,
        )
        runtime = claim_autonomous_runtime_lease(
            runtime,
            owner_token=owner_token,
            owner_instance_id=owner_instance_id,
            owner_pid=owner_pid,
            lease_duration_seconds=lease_duration_seconds,
        )
        failure_limit = int(runtime.get("repeated_failure_limit") or DEFAULT_MAX_CONSECUTIVE_FAILURES)
        if _string(runtime.get("stop_requested_at")):
            return _persist_runtime(
                _completed_runtime(
                    runtime,
                    status="stopped",
                    phase="stopped",
                    summary=_string(runtime.get("stop_reason")) or "Stop requested by operator.",
                    stop_reason=_string(runtime.get("stop_reason")) or "Stop requested by operator.",
                ),
                event_type="runtime.stopped",
                event_details=_action_event_details(action, result, inputs),
                archive=True,
            )
        if runtime["consecutive_failures"] >= failure_limit:
            reason = f"Stopped after {runtime['consecutive_failures']} consecutive failures on {action}."
            return _persist_runtime(
                _completed_runtime(
                    runtime,
                    status="failed",
                    phase="failed",
                    summary=reason,
                    stop_reason=reason,
                ),
                event_type="runtime.failed",
                event_details=_action_event_details(action, result, inputs),
                archive=True,
            )
        success, success_summary = _success_criteria_satisfied(runtime, post_snapshot)
        if success:
            return _persist_runtime(
                _completed_runtime(
                    runtime,
                    status="completed",
                    phase="completed",
                    summary=success_summary,
                    stop_reason=success_summary,
                ),
                event_type="runtime.completed",
                event_details=_action_event_details(action, result, inputs),
                archive=True,
            )
        max_iterations = int(runtime.get("max_iterations") or 0)
        if max_iterations > 0 and int(runtime.get("iteration") or 0) >= max_iterations:
            reason = f"Reached the configured max_iterations limit ({max_iterations})."
            return _persist_runtime(
                _completed_runtime(
                    runtime,
                    status="stopped",
                    phase="stopped",
                    summary=reason,
                    stop_reason=reason,
                ),
                event_type="runtime.stopped",
                event_details=_action_event_details(action, result, inputs),
                archive=True,
            )
        runtime["status"] = "running"
        runtime["current_phase"] = "waiting"
        return _persist_runtime(
            runtime,
            event_type="action.completed",
            event_details=_action_event_details(action, result, inputs),
        )
    except Exception as exc:
        runtime["iteration"] = int(runtime.get("iteration") or 0) + 1
        runtime["last_action_status"] = "failed"
        runtime["consecutive_failures"] = int(runtime.get("consecutive_failures") or 0) + 1
        runtime["summary"] = f"{action} failed: {exc}"
        runtime = claim_autonomous_runtime_lease(
            runtime,
            owner_token=owner_token,
            owner_instance_id=owner_instance_id,
            owner_pid=owner_pid,
            lease_duration_seconds=lease_duration_seconds,
        )
        failure_limit = int(runtime.get("repeated_failure_limit") or DEFAULT_MAX_CONSECUTIVE_FAILURES)
        if runtime["consecutive_failures"] >= failure_limit:
            return _persist_runtime(
                _completed_runtime(
                    runtime,
                    status="failed",
                    phase="failed",
                    summary=runtime["summary"],
                    stop_reason=runtime["summary"],
                ),
                event_type="runtime.failed",
                event_details={"action": action, "error": str(exc)},
                archive=True,
            )
        runtime["status"] = "running"
        runtime["current_phase"] = "waiting"
        return _persist_runtime(
            runtime,
            event_type="action.failed",
            event_details={"action": action, "error": str(exc)},
        )
