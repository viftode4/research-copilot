"""Research Copilot MCP tool registry backed by shared services."""

from __future__ import annotations

import json
from typing import Any

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
from research_copilot.services.workflows import (
    next_step as next_step_workflow,
    onboard as onboard_workflow,
    review_results as review_results_workflow,
    run_experiment as run_experiment_workflow,
    triage as triage_workflow,
)


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
