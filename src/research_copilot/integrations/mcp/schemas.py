"""Schema helpers and tool metadata for the MCP transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

MCP_PROTOCOL_VERSION = "2024-11-05"

JsonObject = dict[str, Any]
ToolHandler = Callable[[JsonObject], Awaitable[JsonObject]]


def string_field(description: str, *, enum: list[str] | None = None, default: str | None = None) -> JsonObject:
    schema: JsonObject = {"type": "string", "description": description}
    if enum:
        schema["enum"] = enum
    if default is not None:
        schema["default"] = default
    return schema


def integer_field(description: str, *, default: int | None = None, minimum: int | None = None) -> JsonObject:
    schema: JsonObject = {"type": "integer", "description": description}
    if default is not None:
        schema["default"] = default
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


def array_field(
    description: str,
    *,
    items: JsonObject | None = None,
    default: list[Any] | None = None,
) -> JsonObject:
    schema: JsonObject = {
        "type": "array",
        "description": description,
        "items": items or {"type": "string"},
    }
    if default is not None:
        schema["default"] = default
    return schema


def object_or_string_field(description: str) -> JsonObject:
    return {
        "description": description,
        "anyOf": [
            {"type": "object"},
            {"type": "array"},
            {"type": "string"},
        ],
    }


def object_schema(
    properties: dict[str, JsonObject],
    *,
    required: list[str] | None = None,
    additional_properties: bool = False,
) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional_properties,
    }


@dataclass(frozen=True, slots=True)
class McpToolDefinition:
    """Stable MCP tool metadata."""

    name: str
    description: str
    input_schema: JsonObject
    handler: ToolHandler

    def as_mcp_tool(self) -> JsonObject:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


APPROVED_V1_TOOL_NAMES = (
    "rc_status",
    "rc_triage",
    "rc_onboard",
    "rc_run_experiment",
    "rc_review_results",
    "rc_next_step",
    "rc_get_context",
    "rc_set_context",
    "rc_autonomous_run",
    "rc_autonomous_status",
    "rc_autonomous_stop",
    "rc_autonomous_resume",
    "rc_codex_attach",
    "rc_codex_status",
    "rc_codex_report_turn",
    "rc_codex_steer",
    "rc_codex_drain_nudges",
    "rc_codex_apply_nudges",
)
