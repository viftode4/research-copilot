"""Research Copilot MCP tool registry backed by shared services."""

from __future__ import annotations

import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from uuid import uuid4

from research_copilot.integrations.mcp.schemas import (
    McpToolDefinition,
    array_field,
    integer_field,
    object_or_string_field,
    object_schema,
    string_field,
)
from research_copilot.research_state import (
    get_research_root,
    get_workspace_mode,
    is_workspace_initialized,
    load_onboarding_contract,
    resolve_workspace,
)
from research_copilot.services.research_ops import (
    get_context as get_context_service,
    get_snapshot as get_snapshot_service,
    list_context as list_context_service,
    set_context as set_context_service,
)
from research_copilot.services.codex_runtime import (
    apply_codex_nudges,
    attach_codex_session,
    codex_runtime_status,
    drain_codex_nudges,
    enqueue_codex_nudge,
    ingest_codex_turn_report,
)
from research_copilot.services.workflows import (
    next_step as next_step_workflow,
    onboard as onboard_workflow,
    review_results as review_results_workflow,
    run_experiment as run_experiment_workflow,
    triage as triage_workflow,
)

AUTONOMOUS_RUNTIME_MODULE = "research_copilot.services.autonomous_runtime"
_AUTONOMOUS_START_CANDIDATES = ("autonomous_run",)
_AUTONOMOUS_STATUS_CANDIDATES = ("autonomous_status",)
_AUTONOMOUS_STOP_CANDIDATES = ("autonomous_stop",)
_AUTONOMOUS_RESUME_CANDIDATES = ("autonomous_resume",)
_AUTONOMOUS_WORKER_CANDIDATES = ("run_autonomous_worker", "autonomous_worker")


class ToolArgumentError(ValueError):
    """Raised when MCP tool arguments fail lightweight validation."""


def _normalize_list(value: Any, *, field_name: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise ToolArgumentError(f"'{field_name}' must be a list of strings or a comma-separated string.")


def _normalize_jsonish(value: Any, *, field_name: str) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    raise ToolArgumentError(f"'{field_name}' must be an object, array, or JSON string.")


def _normalize_objectish(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ToolArgumentError(f"'{field_name}' must be a JSON object or object-encoded string.") from exc
        if isinstance(decoded, dict):
            return decoded
        raise ToolArgumentError(f"'{field_name}' must decode to a JSON object.")
    raise ToolArgumentError(f"'{field_name}' must be a JSON object or object-encoded string.")


def _type_matches(expected_type: str, value: Any) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def validate_tool_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against the narrow schema subset used by this repo."""

    if not isinstance(arguments, dict):
        raise ToolArgumentError("Tool arguments must be a JSON object.")

    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for field_name in required:
        if field_name not in arguments or arguments[field_name] in (None, ""):
            raise ToolArgumentError(f"Missing required argument '{field_name}'.")

    for field_name, value in arguments.items():
        property_schema = properties.get(field_name)
        if property_schema is None:
            if schema.get("additionalProperties", False):
                continue
            raise ToolArgumentError(f"Unexpected argument '{field_name}'.")

        if "anyOf" in property_schema:
            if not any(_type_matches(option.get("type", ""), value) for option in property_schema["anyOf"]):
                raise ToolArgumentError(property_schema["description"])
            continue

        expected_type = property_schema.get("type")
        if expected_type and not _type_matches(expected_type, value):
            raise ToolArgumentError(f"'{field_name}' must be of type '{expected_type}'.")

        enum = property_schema.get("enum")
        if enum and value not in enum:
            raise ToolArgumentError(f"'{field_name}' must be one of: {', '.join(enum)}.")

    return arguments


def _load_autonomous_runtime_callable(*candidate_names: str) -> Any:
    try:
        module = importlib.import_module(AUTONOMOUS_RUNTIME_MODULE)
    except ModuleNotFoundError as exc:
        raise ValueError("Autonomous runtime services are not available in this build.") from exc

    for name in candidate_names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate

    raise ValueError(
        "Autonomous runtime services are missing an expected lifecycle entrypoint: "
        + ", ".join(candidate_names)
    )


def _autonomous_status_unavailable_payload(run_id: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "unavailable",
        "available": False,
        "summary": "Autonomous runtime services are not available in this build.",
    }


def _invoke_autonomous_runtime(*candidate_names: str, **kwargs: Any) -> Any:
    handler = _load_autonomous_runtime_callable(*candidate_names)
    signature = inspect.signature(handler)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        bound_kwargs = kwargs
    else:
        bound_kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return handler(**bound_kwargs)


def _runtime_payload_view(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    runtime = payload.get("runtime")
    return runtime if isinstance(runtime, dict) else payload


def _runtime_value(payload: Any, key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    if key in payload:
        return payload.get(key)
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        return runtime.get(key)
    return None


def _autonomous_worker_auth_dir() -> Path:
    path = resolve_workspace().canonical_root / "runtime" / "auth"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _create_autonomous_worker_auth_file(run_id: str, owner_token: str) -> Path:
    auth_path = _autonomous_worker_auth_dir() / f"{run_id}-{uuid4().hex}.json"
    payload = {
        "run_id": run_id,
        "owner_token": owner_token,
        "owner_instance_id": uuid4().hex,
    }
    auth_path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(auth_path, 0o600)
    except OSError:
        pass
    return auth_path


def _autonomous_worker_argv(run_id: str, auth_file: str) -> list[str]:
    workspace = str(resolve_workspace().workspace_dir)
    return [
        sys.executable,
        "-m",
        "research_copilot.main",
        "--workspace",
        workspace,
        "workflow",
        "autonomous-worker",
        "--run-id",
        run_id,
        "--auth-file",
        auth_file,
    ]


def _autonomous_worker_env() -> dict[str, str]:
    workspace = str(resolve_workspace().workspace_dir)
    env = os.environ.copy()
    env["RC_WORKING_DIR"] = workspace
    src_root = str(Path(__file__).resolve().parents[3])
    existing_pythonpath = env.get("PYTHONPATH", "")
    paths = [path for path in existing_pythonpath.split(os.pathsep) if path]
    if src_root not in paths:
        env["PYTHONPATH"] = os.pathsep.join([src_root, *paths]) if paths else src_root
    return env


def _should_launch_autonomous_worker(payload: Any) -> bool:
    runtime = _runtime_payload_view(payload)
    if not runtime:
        return False
    if runtime.get("worker_spawned") or runtime.get("worker_started"):
        return False
    if runtime.get("owner_pid"):
        return False
    return bool(_runtime_value(payload, "run_id") and _runtime_value(payload, "owner_token"))


def _launch_autonomous_worker(payload: Any) -> None:
    if not _should_launch_autonomous_worker(payload):
        return

    run_id = str(_runtime_value(payload, "run_id") or "").strip()
    owner_token = str(_runtime_value(payload, "owner_token") or "").strip()
    if not run_id or not owner_token:
        raise ValueError("Autonomous runtime start did not return a run_id/owner_token pair.")
    auth_file = _create_autonomous_worker_auth_file(run_id, owner_token)

    popen_kwargs: dict[str, Any] = {
        "args": _autonomous_worker_argv(run_id, str(auth_file)),
        "cwd": str(resolve_workspace().workspace_dir),
        "env": _autonomous_worker_env(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = 0
        for flag_name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_NO_WINDOW"):
            creationflags |= int(getattr(subprocess, flag_name, 0))
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(**popen_kwargs)


async def rc_status(arguments: dict[str, Any]) -> dict[str, Any]:
    max_items = int(arguments.get("max_items", 5))
    resolved = resolve_workspace()
    return {
        "workspace": {
            "directory": str(resolved.workspace_dir),
            "research_root": str(get_research_root()),
            "mode": get_workspace_mode(),
            "initialized": is_workspace_initialized(),
        },
        "onboarding": load_onboarding_contract(),
        "snapshot": await get_snapshot_service(max_items=max_items),
    }


async def rc_triage(arguments: dict[str, Any]) -> dict[str, Any]:
    return await triage_workflow(max_items=int(arguments.get("max_items", 5)))


async def rc_onboard(arguments: dict[str, Any]) -> dict[str, Any]:
    return await onboard_workflow(
        goal=str(arguments["goal"]),
        success_criteria=str(arguments["success_criteria"]),
        active_profile=str(arguments["active_profile"]),
        autonomy_level=str(arguments["autonomy_level"]),
        allowed_actions=_normalize_list(arguments.get("allowed_actions"), field_name="allowed_actions"),
        constraints=_normalize_list(arguments.get("constraints"), field_name="constraints"),
        stop_conditions=_normalize_list(arguments.get("stop_conditions"), field_name="stop_conditions"),
        notes=str(arguments.get("notes", "")),
        actor_type=str(arguments.get("actor_type", "codex")),
    )


async def rc_run_experiment(arguments: dict[str, Any]) -> dict[str, Any]:
    return await run_experiment_workflow(
        command=str(arguments["command"]),
        experiment_id=str(arguments.get("experiment_id", "")),
        name=str(arguments.get("name", "")),
        hypothesis=str(arguments.get("hypothesis", "")),
        description=str(arguments.get("description", "")),
        config=_normalize_jsonish(arguments.get("config"), field_name="config"),
        dataset=str(arguments.get("dataset", "")),
        model_type=str(arguments.get("model_type", "")),
        tags=_normalize_list(arguments.get("tags"), field_name="tags"),
        created_by=str(arguments.get("created_by", "codex-mcp")),
        actor_type=str(arguments.get("actor_type", "codex")),
    )


async def rc_review_results(arguments: dict[str, Any]) -> dict[str, Any]:
    return await review_results_workflow(
        experiment_id=str(arguments["experiment_id"]),
        insight_title=str(arguments.get("insight_title", "")),
        insight_content=str(arguments.get("insight_content", "")),
        context_key=str(arguments.get("context_key", "")),
        context_value=str(arguments.get("context_value", "")),
        context_type=str(arguments.get("context_type", "note")),
        created_by=str(arguments.get("created_by", "codex-mcp")),
        actor_type=str(arguments.get("actor_type", "codex")),
    )


async def rc_next_step(arguments: dict[str, Any]) -> dict[str, Any]:
    return await next_step_workflow(
        experiment_id=str(arguments["experiment_id"]),
        actor_type=str(arguments.get("actor_type", "codex")),
    )


async def rc_get_context(arguments: dict[str, Any]) -> dict[str, Any]:
    key = str(arguments.get("key", "")).strip()
    if key:
        return await get_context_service(key=key)
    return await list_context_service()


async def rc_set_context(arguments: dict[str, Any]) -> dict[str, Any]:
    return await set_context_service(
        key=str(arguments["key"]),
        value=str(arguments["value"]),
        context_type=str(arguments.get("context_type", "note")),
        actor_type=str(arguments.get("actor_type", "codex")),
        workflow_name="rc_set_context",
        linked_experiment_id=str(arguments.get("linked_experiment_id", "")),
        linked_job_id=str(arguments.get("linked_job_id", "")),
    )


async def rc_autonomous_run(arguments: dict[str, Any]) -> dict[str, Any]:
    result = _invoke_autonomous_runtime(
        *_AUTONOMOUS_START_CANDIDATES,
        goal=str(arguments.get("goal", "")),
        success_criteria=str(arguments.get("success_criteria", "")),
        profile_name=str(arguments.get("profile_name", "")),
        active_profile=str(arguments.get("profile_name", "")),
        autonomy_level=str(arguments.get("autonomy_level", "")),
        allowed_actions=_normalize_list(arguments.get("allowed_actions"), field_name="allowed_actions"),
        constraints=_normalize_list(arguments.get("constraints"), field_name="constraints"),
        stop_conditions=_normalize_list(arguments.get("stop_conditions"), field_name="stop_conditions"),
        command_template=str(arguments.get("command_template", "")),
        template_vars=_normalize_objectish(arguments.get("template_vars"), field_name="template_vars"),
        action_envelope=_normalize_objectish(
            arguments.get("action_envelope"), field_name="action_envelope"
        ),
        max_iterations=arguments.get("max_iterations"),
        created_by=str(arguments.get("created_by", "codex-mcp")),
        actor_type=str(arguments.get("actor_type", "codex")),
        actor=str(arguments.get("actor_type", "codex")),
        spawn_worker=False,
        launch_worker=False,
        detach=False,
    )
    if inspect.isawaitable(result):
        payload = await result
    else:
        payload = result
    _launch_autonomous_worker(payload)
    return payload


async def rc_autonomous_status(arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = str(arguments.get("run_id", ""))
    try:
        result = _invoke_autonomous_runtime(
            *_AUTONOMOUS_STATUS_CANDIDATES,
            run_id=run_id,
            runtime_id=run_id,
        )
    except ValueError as exc:
        if "Autonomous runtime services are not available" not in str(exc):
            raise
        return _autonomous_status_unavailable_payload(run_id)
    return await result if inspect.isawaitable(result) else result


async def rc_autonomous_stop(arguments: dict[str, Any]) -> dict[str, Any]:
    result = _invoke_autonomous_runtime(
        *_AUTONOMOUS_STOP_CANDIDATES,
        run_id=str(arguments.get("run_id", "")),
        runtime_id=str(arguments.get("run_id", "")),
        reason=str(arguments.get("reason", "")),
        stop_reason=str(arguments.get("reason", "")),
        owner_token=str(arguments.get("owner_token", "")),
        token=str(arguments.get("owner_token", "")),
        created_by=str(arguments.get("created_by", "codex-mcp")),
        actor_type=str(arguments.get("actor_type", "codex")),
        actor=str(arguments.get("actor_type", "codex")),
    )
    return await result if inspect.isawaitable(result) else result


async def rc_autonomous_resume(arguments: dict[str, Any]) -> dict[str, Any]:
    result = _invoke_autonomous_runtime(
        *_AUTONOMOUS_RESUME_CANDIDATES,
        run_id=str(arguments.get("run_id", "")),
        runtime_id=str(arguments.get("run_id", "")),
        owner_token=str(arguments.get("owner_token", "")),
        token=str(arguments.get("owner_token", "")),
        created_by=str(arguments.get("created_by", "codex-mcp")),
        actor_type=str(arguments.get("actor_type", "codex")),
        actor=str(arguments.get("actor_type", "codex")),
        spawn_worker=False,
        launch_worker=False,
        detach=False,
    )
    payload = await result if inspect.isawaitable(result) else result
    _launch_autonomous_worker(payload)
    return payload


async def rc_codex_attach(arguments: dict[str, Any]) -> dict[str, Any]:
    return attach_codex_session(
        session_id=str(arguments["session_id"]),
        goal=str(arguments.get("goal", "")),
        constraints=_normalize_list(arguments.get("constraints"), field_name="constraints"),
        allowed_actions=_normalize_list(arguments.get("allowed_actions"), field_name="allowed_actions"),
        status=str(arguments.get("status", "running")),
        current_turn=arguments.get("current_turn"),
        turn_state=str(arguments.get("turn_state", "")),
        operator_mode=str(arguments.get("operator_mode", "steerable")),
        transport=str(arguments.get("transport", "tmux-pane")),
        pane_id=str(arguments.get("pane_id", "")),
        window_name=str(arguments.get("window_name", "")),
        session_name=str(arguments.get("session_name", "")),
        workspace=str(arguments.get("workspace", "")) or str(resolve_workspace().workspace_dir),
        profile_name=str(arguments.get("profile_name", "")),
        autonomy_level=str(arguments.get("autonomy_level", "")),
        actor_type=str(arguments.get("actor_type", "codex")),
    )


async def rc_codex_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return codex_runtime_status(
        session_id=str(arguments.get("session_id", "")),
        include_nudges=bool(arguments.get("include_nudges", False)),
    )


async def rc_codex_report_turn(arguments: dict[str, Any]) -> dict[str, Any]:
    return ingest_codex_turn_report(
        session_id=str(arguments["session_id"]),
        turn_number=int(arguments["turn_number"]),
        summary=str(arguments["summary"]),
        action=str(arguments.get("action", "")),
        artifacts=_normalize_list(arguments.get("artifacts"), field_name="artifacts"),
        status=str(arguments.get("status", "")),
        turn_state=str(arguments.get("turn_state", "")),
        experiment_id=str(arguments.get("experiment_id", "")),
        review_id=str(arguments.get("review_id", "")),
        context_update=str(arguments.get("context_update", "")),
        reported_at=str(arguments.get("reported_at", "")),
        heartbeat_at=str(arguments.get("heartbeat_at", "")),
        pane_id=str(arguments.get("pane_id", "")),
        window_name=str(arguments.get("window_name", "")),
        session_name=str(arguments.get("session_name", "")),
        workspace=str(arguments.get("workspace", "")) or str(resolve_workspace().workspace_dir),
    )


async def rc_codex_steer(arguments: dict[str, Any]) -> dict[str, Any]:
    return enqueue_codex_nudge(
        session_id=str(arguments["session_id"]),
        kind=str(arguments["kind"]),
        message=str(arguments.get("message", "")),
        payload=_normalize_objectish(arguments.get("payload"), field_name="payload"),
        actor_type=str(arguments.get("actor_type", "codex")),
    )


async def rc_codex_drain_nudges(arguments: dict[str, Any]) -> dict[str, Any]:
    return drain_codex_nudges(
        session_id=str(arguments["session_id"]),
        limit=arguments.get("limit"),
    )


async def rc_codex_apply_nudges(arguments: dict[str, Any]) -> dict[str, Any]:
    return apply_codex_nudges(
        session_id=str(arguments["session_id"]),
        limit=arguments.get("limit"),
    )


TOOL_DEFINITIONS: tuple[McpToolDefinition, ...] = (
    McpToolDefinition(
        name="rc_status",
        description=(
            "Inspect the current Research Copilot workspace state. "
            "Read-only; no side effects. Reads the shared snapshot, onboarding contract, and workspace metadata."
        ),
        input_schema=object_schema(
            {
                "max_items": integer_field(
                    "Maximum jobs/experiments to include in the snapshot.",
                    default=5,
                    minimum=1,
                ),
            }
        ),
        handler=rc_status,
    ),
    McpToolDefinition(
        name="rc_triage",
        description=(
            "Inspect current workflow state and suggest the next explicit research action. "
            "Read-only; no side effects."
        ),
        input_schema=object_schema(
            {
                "max_items": integer_field(
                    "Maximum jobs/experiments to inspect while triaging.",
                    default=5,
                    minimum=1,
                ),
            }
        ),
        handler=rc_triage,
    ),
    McpToolDefinition(
        name="rc_onboard",
        description=(
            "Persist the current onboarding contract for the shared workspace. "
            "Side effects: writes onboarding state and goal/profile context. "
            "Repeat safety: safe to repeat with the same payload; later calls overwrite the active contract."
        ),
        input_schema=object_schema(
            {
                "goal": string_field("Current research goal."),
                "success_criteria": string_field("How success will be judged."),
                "active_profile": string_field("Preferred autonomous profile."),
                "autonomy_level": string_field(
                    "How much autonomy the tool may use inside workflows.",
                    enum=["guided", "bounded", "aggressive"],
                ),
                "allowed_actions": array_field("Repeatable actions the agent may take."),
                "constraints": array_field("Explicit constraints to preserve."),
                "stop_conditions": array_field("Conditions that should stop execution."),
                "notes": string_field("Free-form notes from onboarding."),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            },
            required=["goal", "success_criteria", "active_profile", "autonomy_level"],
        ),
        handler=rc_onboard,
    ),
    McpToolDefinition(
        name="rc_run_experiment",
        description=(
            "Execute a local experiment command and persist the resulting run artifact. "
            "Side effects: creates or updates experiment/run state under the shared workspace and executes the supplied command. "
            "Repeat safety: not idempotent; repeated calls can create additional runs."
        ),
        input_schema=object_schema(
            {
                "command": string_field("Local shell command to execute."),
                "experiment_id": string_field("Optional existing experiment identifier."),
                "name": string_field("Experiment name if a new experiment must be created."),
                "hypothesis": string_field("Experiment hypothesis."),
                "description": string_field("Experiment description."),
                "config": object_or_string_field("Experiment config as a JSON object/array or JSON string."),
                "dataset": string_field("Dataset name."),
                "model_type": string_field("Model family/type."),
                "tags": array_field("Experiment tags."),
                "created_by": string_field("Actor identifier for provenance.", default="codex-mcp"),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            },
            required=["command"],
        ),
        handler=rc_run_experiment,
    ),
    McpToolDefinition(
        name="rc_review_results",
        description=(
            "Inspect a completed experiment and optionally persist an insight or context note. "
            "Side effects: may write insight/context records when optional mutation fields are supplied. "
            "Repeat safety: repeatable reads are safe; repeated writes can create additional insights or overwrite context keys."
        ),
        input_schema=object_schema(
            {
                "experiment_id": string_field("Experiment identifier to inspect."),
                "insight_title": string_field("Optional insight title to persist."),
                "insight_content": string_field("Optional insight body to persist."),
                "context_key": string_field("Optional context key to update."),
                "context_value": string_field("Optional context value to update."),
                "context_type": string_field(
                    "Context entry type for any persisted note.",
                    enum=["goal", "plan", "note", "reference", "constraint"],
                    default="note",
                ),
                "created_by": string_field("Actor identifier for provenance.", default="codex-mcp"),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            },
            required=["experiment_id"],
        ),
        handler=rc_review_results,
    ),
    McpToolDefinition(
        name="rc_next_step",
        description=(
            "Generate the next-step recommendation for an experiment. "
            "Side effects: writes a review artifact for the recommendation. "
            "Repeat safety: safe to repeat; later calls refresh the recommendation artifact."
        ),
        input_schema=object_schema(
            {
                "experiment_id": string_field("Experiment identifier to inspect."),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            },
            required=["experiment_id"],
        ),
        handler=rc_next_step,
    ),
    McpToolDefinition(
        name="rc_get_context",
        description=(
            "Inspect saved research context. "
            "Read-only; no side effects. Returns one key when provided, otherwise the full saved context list."
        ),
        input_schema=object_schema(
            {
                "key": string_field("Optional context key to fetch."),
            }
        ),
        handler=rc_get_context,
    ),
    McpToolDefinition(
        name="rc_set_context",
        description=(
            "Persist a research context entry in the shared workspace. "
            "Side effects: writes or updates a context key. "
            "Repeat safety: predictable overwrite semantics for the same key/value pair."
        ),
        input_schema=object_schema(
            {
                "key": string_field("Context key to write."),
                "value": string_field("Context value/content."),
                "context_type": string_field(
                    "Context entry type.",
                    enum=["goal", "plan", "note", "reference", "constraint"],
                    default="note",
                ),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
                "linked_experiment_id": string_field("Optional linked experiment identifier."),
                "linked_job_id": string_field("Optional linked job identifier."),
            },
            required=["key", "value"],
        ),
        handler=rc_set_context,
    ),
    McpToolDefinition(
        name="rc_autonomous_run",
        description=(
            "Start the persistent autonomous runtime. "
            "Side effects: writes runtime lifecycle state, mints ownership, and launches the detached worker. "
            "Repeat safety: not idempotent; repeated calls can replace the active runtime."
        ),
        input_schema=object_schema(
            {
                "goal": string_field("Optional goal override for the persistent runtime."),
                "success_criteria": string_field("Optional success-criteria override."),
                "profile_name": string_field("Optional autonomous profile override."),
                "autonomy_level": string_field(
                    "Optional autonomy-level override.",
                    enum=["guided", "bounded", "aggressive"],
                ),
                "allowed_actions": array_field("Repeatable allowed actions."),
                "constraints": array_field("Explicit constraints to preserve."),
                "stop_conditions": array_field("Conditions that should stop execution."),
                "command_template": string_field(
                    "Reusable local command template for persistent run-experiment steps."
                ),
                "template_vars": object_or_string_field(
                    "Persisted template variables as a JSON object or object-encoded JSON string."
                ),
                "action_envelope": object_or_string_field(
                    "Optional normalized action-envelope override as a JSON object or object-encoded JSON string."
                ),
                "max_iterations": integer_field(
                    "Optional maximum number of bounded runtime iterations.",
                    minimum=1,
                ),
                "created_by": string_field("Actor identifier for provenance.", default="codex-mcp"),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            }
        ),
        handler=rc_autonomous_run,
    ),
    McpToolDefinition(
        name="rc_autonomous_status",
        description=(
            "Inspect the persisted autonomous runtime state. "
            "Read-only; no side effects. Returns the active runtime when no run id is provided."
        ),
        input_schema=object_schema(
            {
                "run_id": string_field("Optional runtime identifier; defaults to the active runtime."),
            }
        ),
        handler=rc_autonomous_status,
    ),
    McpToolDefinition(
        name="rc_autonomous_stop",
        description=(
            "Request a graceful stop for the persistent autonomous runtime. "
            "Side effects: marks stop-request metadata; the worker exits after the current bounded action."
        ),
        input_schema=object_schema(
            {
                "run_id": string_field("Optional runtime identifier; defaults to the active runtime."),
                "reason": string_field("Optional operator stop reason."),
                "owner_token": string_field("Required runtime capability token for stop."),
                "created_by": string_field("Actor identifier for provenance.", default="codex-mcp"),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            }
            ,
            required=["owner_token"],
        ),
        handler=rc_autonomous_stop,
    ),
    McpToolDefinition(
        name="rc_autonomous_resume",
        description=(
            "Resume a stopped or stale persistent autonomous runtime. "
            "Side effects: reacquires ownership and launches a fresh detached worker when the runtime is resumable."
        ),
        input_schema=object_schema(
            {
                "run_id": string_field("Optional runtime identifier; defaults to the active runtime."),
                "owner_token": string_field("Required runtime capability token for resume."),
                "created_by": string_field("Actor identifier for provenance.", default="codex-mcp"),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            }
            ,
            required=["owner_token"],
        ),
        handler=rc_autonomous_resume,
    ),
    McpToolDefinition(
        name="rc_codex_attach",
        description=(
            "Register or reattach a Codex-managed session as the active research runtime. "
            "Side effects: updates the active Codex runtime contract and transport metadata."
        ),
        input_schema=object_schema(
            {
                "session_id": string_field("Stable Codex session identifier."),
                "goal": string_field("Optional operator-visible goal."),
                "constraints": array_field("Repeatable session constraints."),
                "allowed_actions": array_field("Repeatable allowed actions."),
                "status": string_field("Optional session status override.", default="running"),
                "current_turn": integer_field("Optional current turn number.", minimum=0),
                "turn_state": string_field("Optional current turn state."),
                "operator_mode": string_field(
                    "Optional operator mode.",
                    enum=["hands_off", "steerable", "paused"],
                    default="steerable",
                ),
                "transport": string_field(
                    "Transport type for this session.",
                    enum=["tmux-pane", "managed-process"],
                    default="tmux-pane",
                ),
                "pane_id": string_field("Optional tmux pane id."),
                "window_name": string_field("Optional tmux window name."),
                "session_name": string_field("Optional tmux session name."),
                "workspace": string_field("Optional workspace path override."),
                "profile_name": string_field("Optional profile label."),
                "autonomy_level": string_field("Optional autonomy level label."),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            },
            required=["session_id"],
        ),
        handler=rc_codex_attach,
    ),
    McpToolDefinition(
        name="rc_codex_status",
        description=(
            "Inspect the active or archived Codex runtime contract. "
            "Read-only; no side effects."
        ),
        input_schema=object_schema(
            {
                "session_id": string_field("Optional session identifier; defaults to the active session."),
                "include_nudges": {
                    "type": "boolean",
                    "description": "Include queued steering nudges in the response.",
                    "default": False,
                },
            }
        ),
        handler=rc_codex_status,
    ),
    McpToolDefinition(
        name="rc_codex_report_turn",
        description=(
            "Ingest one bounded Codex turn report into the shared runtime state. "
            "Side effects: writes summary/event artifacts and refreshes active-session state."
        ),
        input_schema=object_schema(
            {
                "session_id": string_field("Stable Codex session identifier."),
                "turn_number": integer_field("Bounded Codex turn number.", minimum=0),
                "summary": string_field("Visible summary for this Codex turn."),
                "action": string_field("Optional action label."),
                "artifacts": array_field("Optional artifact identifiers."),
                "status": string_field("Optional reported status override."),
                "turn_state": string_field("Optional reported turn state."),
                "experiment_id": string_field("Optional experiment id touched by this turn."),
                "review_id": string_field("Optional review id touched by this turn."),
                "context_update": string_field("Optional context key/value label updated by this turn."),
                "reported_at": string_field("Optional report timestamp override."),
                "heartbeat_at": string_field("Optional heartbeat timestamp override."),
                "pane_id": string_field("Optional tmux pane id."),
                "window_name": string_field("Optional tmux window name."),
                "session_name": string_field("Optional tmux session name."),
                "workspace": string_field("Optional workspace path override."),
            },
            required=["session_id", "turn_number", "summary"],
        ),
        handler=rc_codex_report_turn,
    ),
    McpToolDefinition(
        name="rc_codex_steer",
        description=(
            "Enqueue one steering command for a Codex-managed session. "
            "Side effects: writes to the shared steering queue and updates pending nudge state."
        ),
        input_schema=object_schema(
            {
                "session_id": string_field("Stable Codex session identifier."),
                "kind": string_field(
                    "Steering command kind.",
                    enum=["nudge", "pause", "resume", "stop_after_turn", "request_summary"],
                ),
                "message": string_field("Optional steering message."),
                "payload": object_or_string_field("Optional JSON object for richer steering metadata."),
                "actor_type": string_field("Actor type recorded in provenance.", default="codex"),
            },
            required=["session_id", "kind"],
        ),
        handler=rc_codex_steer,
    ),
    McpToolDefinition(
        name="rc_codex_drain_nudges",
        description=(
            "Drain pending steering nudges for a Codex-managed session. "
            "Side effects: removes delivered nudges from the shared queue."
        ),
        input_schema=object_schema(
            {
                "session_id": string_field("Stable Codex session identifier."),
                "limit": integer_field("Optional maximum nudges to drain.", minimum=1),
            },
            required=["session_id"],
        ),
        handler=rc_codex_drain_nudges,
    ),
    McpToolDefinition(
        name="rc_codex_apply_nudges",
        description=(
            "Apply queued steering nudges into the registered tmux pane and then drain them. "
            "Side effects: sends steering text to the live pane and removes delivered nudges from the queue."
        ),
        input_schema=object_schema(
            {
                "session_id": string_field("Stable Codex session identifier."),
                "limit": integer_field("Optional maximum nudges to apply.", minimum=1),
            },
            required=["session_id"],
        ),
        handler=rc_codex_apply_nudges,
    ),
)

TOOL_BY_NAME = {tool.name: tool for tool in TOOL_DEFINITIONS}


def list_mcp_tools() -> list[dict[str, Any]]:
    """Return stable tool metadata for tools/list."""
    return [tool.as_mcp_tool() for tool in TOOL_DEFINITIONS]


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate and execute a named MCP tool."""

    tool = TOOL_BY_NAME.get(name)
    if tool is None:
        raise ToolArgumentError(f"Unknown tool '{name}'.")

    normalized_arguments = validate_tool_arguments(tool.input_schema, arguments or {})
    return await tool.handler(normalized_arguments)
