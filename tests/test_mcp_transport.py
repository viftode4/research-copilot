"""Tests for the Research Copilot MCP transport and tool schemas."""

from __future__ import annotations

import asyncio
import json
from io import BytesIO

import pytest

from research_copilot.integrations.mcp.schemas import APPROVED_V1_TOOL_NAMES, MCP_PROTOCOL_VERSION
from research_copilot.integrations.mcp.server import (
    ResearchCopilotMcpServer,
    read_framed_message,
    run_stdio_server,
    write_delimited_message,
)
from research_copilot.integrations.mcp.tools import call_tool
from research_copilot.mcp_servers.knowledge_base import _store
from research_copilot.mcp_servers.slurm import _mock_jobs
from research_copilot.research_state import initialize_workspace


@pytest.fixture(autouse=True)
def clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()
    for variable in ("RC_RESEARCH_ROOT", "RC_WORKING_DIR", "RC_GLOBAL_HOME"):
        monkeypatch.delenv(variable, raising=False)
    yield
    _mock_jobs.clear()
    for key in _store:
        _store[key].clear()


def _collect_framed_messages(raw: bytes) -> list[dict[str, object]]:
    stream = BytesIO(raw)
    messages: list[dict[str, object]] = []
    while True:
        payload = read_framed_message(stream)
        if payload is None:
            return messages
        messages.append(payload)


def test_read_framed_message_accepts_newline_delimited_jsonrpc() -> None:
    stream = BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')

    payload = read_framed_message(stream)

    assert payload == {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def test_write_delimited_message_emits_single_json_line() -> None:
    stream = BytesIO()

    write_delimited_message(stream, {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})

    assert stream.getvalue() == b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'


def test_stdio_server_initializes_and_lists_only_the_approved_v1_tools() -> None:
    input_stream = BytesIO()
    write_delimited_message(
        input_stream,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}},
        },
    )
    write_delimited_message(
        input_stream,
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
    )
    write_delimited_message(
        input_stream,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
    )
    input_stream.seek(0)
    output_stream = BytesIO()

    asyncio.run(run_stdio_server(input_stream=input_stream, output_stream=output_stream))

    messages = [
        json.loads(line)
        for line in output_stream.getvalue().decode("utf-8").splitlines()
        if line.strip()
    ]
    assert messages[0]["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert messages[0]["result"]["serverInfo"]["name"] == "research-copilot"

    tools = messages[1]["result"]["tools"]
    assert [tool["name"] for tool in tools] == list(APPROVED_V1_TOOL_NAMES)
    assert "Side effects:" in next(tool["description"] for tool in tools if tool["name"] == "rc_onboard")
    assert "Read-only" in next(tool["description"] for tool in tools if tool["name"] == "rc_status")


@pytest.mark.asyncio
async def test_tool_calls_route_through_shared_services(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"workflow": "triage", "blockers": ["none"], "suggested_next_action": "review-results"}

    async def fake_triage(*, max_items: int = 5) -> dict[str, object]:
        assert max_items == 3
        return expected

    monkeypatch.setattr("research_copilot.integrations.mcp.tools.triage_workflow", fake_triage)

    result = await call_tool("rc_triage", {"max_items": 3})

    assert result == expected


@pytest.mark.asyncio
async def test_context_tools_share_workspace_state(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    initialize_workspace()

    set_payload = await call_tool(
        "rc_set_context",
        {
            "key": "current_goal",
            "value": "Ship MCP transport",
            "context_type": "goal",
        },
    )
    get_payload = await call_tool("rc_get_context", {"key": "current_goal"})
    status_payload = await call_tool("rc_status", {})

    assert set_payload["key"] == "current_goal"
    assert get_payload["value"] == "Ship MCP transport"
    assert status_payload["workspace"]["initialized"] is True
    assert status_payload["workspace"]["directory"] == str(tmp_path)


@pytest.mark.asyncio
async def test_tools_call_returns_structured_error_for_invalid_arguments() -> None:
    server = ResearchCopilotMcpServer()
    await server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "rc_set_context", "arguments": {"key": "missing-value"}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["error"]["code"] == "INVALID_ARGUMENTS"
