"""Tests for the Research Copilot MCP transport and tool schemas."""

from __future__ import annotations

import asyncio
import importlib
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
from research_copilot.integrations.mcp.tools import call_tool, list_mcp_tools
from research_copilot.integrations.mcp.tools import ToolArgumentError
from research_copilot.mcp_servers.knowledge_base import _store
from research_copilot.mcp_servers.slurm import _mock_jobs
from research_copilot.research_state import (
    load_codex_active_session,
    load_codex_turn_summary,
    initialize_workspace,
    list_autonomous_runtime_events,
    load_autonomous_runtime,
    load_autonomous_runtime_history,
    save_autonomous_runtime,
)


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


def test_autonomous_runtime_tools_are_listed_when_runtime_lane_is_available() -> None:
    expected = {
        "rc_autonomous_run",
        "rc_autonomous_status",
        "rc_autonomous_stop",
        "rc_autonomous_resume",
    }
    available = set(APPROVED_V1_TOOL_NAMES)
    if not expected.issubset(available):
        pytest.skip("Lane 2 MCP autonomous runtime tools are not available in this checkout yet.")

    tools = {tool["name"]: tool for tool in list_mcp_tools()}

    assert expected.issubset(tools)
    assert "Read-only" in tools["rc_autonomous_status"]["description"]
    assert "Side effects:" in tools["rc_autonomous_run"]["description"]


def test_codex_runtime_tools_are_listed() -> None:
    expected = {
        "rc_codex_attach",
        "rc_codex_status",
        "rc_codex_report_turn",
        "rc_codex_steer",
        "rc_codex_drain_nudges",
        "rc_codex_apply_nudges",
    }
    tools = {tool["name"]: tool for tool in list_mcp_tools()}

    assert expected.issubset(tools)
    assert "Read-only" in tools["rc_codex_status"]["description"]
    assert "Side effects:" in tools["rc_codex_report_turn"]["description"]


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
async def test_autonomous_status_tool_routes_through_shared_service_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if "rc_autonomous_status" not in APPROVED_V1_TOOL_NAMES:
        pytest.skip("Lane 2 MCP autonomous runtime tools are not available in this checkout yet.")

    tools_module = importlib.import_module("research_copilot.integrations.mcp.tools")
    target_name = next(
        (
            name
            for name in (
                "autonomous_status_workflow",
                "autonomous_status_service",
                "get_autonomous_runtime_status",
            )
            if hasattr(tools_module, name)
        ),
        None,
    )
    if target_name is None:
        pytest.skip("Autonomous MCP tool exists, but its shared-service binding is not discoverable yet.")

    expected = {"run_id": "run-123", "status": "running", "iteration": 2}

    async def fake_status(*_args, **_kwargs):
        return expected

    monkeypatch.setattr(f"research_copilot.integrations.mcp.tools.{target_name}", fake_status)

    result = await call_tool("rc_autonomous_status", {})

    assert result == expected


@pytest.mark.asyncio
async def test_autonomous_status_tool_reports_stale_without_mutating_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    if "rc_autonomous_status" not in APPROVED_V1_TOOL_NAMES:
        pytest.skip("Lane 2 MCP autonomous runtime tools are not available in this checkout yet.")

    monkeypatch.chdir(tmp_path)
    initialize_workspace()
    save_autonomous_runtime(
        {
            "schema_version": "1.0",
            "run_id": "run-1",
            "status": "running",
            "goal": "proof",
            "profile_name": "goal-chaser",
            "iteration": 1,
            "updated_at": "2026-04-13T00:00:00+00:00",
            "started_at": "2026-04-13T00:00:00+00:00",
            "last_heartbeat_at": "2026-04-13T00:00:00+00:00",
            "lease_expires_at": "2026-04-13T00:00:01+00:00",
            "owner_pid": 999999,
            "owner_token": "secret-token",
        }
    )
    before = json.dumps(load_autonomous_runtime(), sort_keys=True)

    payload = await call_tool("rc_autonomous_status", {"run_id": "run-1"})

    assert payload["status"] == "stale"
    assert json.dumps(load_autonomous_runtime(), sort_keys=True) == before
    assert load_autonomous_runtime_history("run-1") == {}
    assert list_autonomous_runtime_events("run-1") == []


@pytest.mark.asyncio
async def test_autonomous_stop_tool_requires_owner_token() -> None:
    if "rc_autonomous_stop" not in APPROVED_V1_TOOL_NAMES:
        pytest.skip("Lane 2 MCP autonomous runtime tools are not available in this checkout yet.")

    with pytest.raises(ToolArgumentError, match="Missing required argument 'owner_token'"):
        await call_tool("rc_autonomous_stop", {"run_id": "run-1"})


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


@pytest.mark.asyncio
async def test_codex_runtime_tools_share_service_contract(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    initialize_workspace()
    monkeypatch.setattr("research_copilot.services.codex_runtime._tmux_pane_exists", lambda pane_id: pane_id == "%81")
    monkeypatch.setattr(
        "research_copilot.services.codex_runtime._tmux_pane_metadata",
        lambda pane_id: {
            "pane_id": "%81",
            "session_name": "codex-1",
            "window_name": "brain",
            "workspace": str(tmp_path),
        },
    )

    attach_payload = await call_tool(
        "rc_codex_attach",
        {
            "session_id": "codex-1",
            "pane_id": "%81",
            "window_name": "brain",
        },
    )
    report_payload = await call_tool(
        "rc_codex_report_turn",
        {
            "session_id": "codex-1",
            "turn_number": 1,
            "summary": "Reviewed the active experiment.",
            "action": "review-results",
            "experiment_id": "exp-1",
        },
    )
    steer_payload = await call_tool(
        "rc_codex_steer",
        {
            "session_id": "codex-1",
            "kind": "request_summary",
            "message": "Need a tighter recap.",
        },
    )
    drain_payload = await call_tool("rc_codex_drain_nudges", {"session_id": "codex-1"})
    status_payload = await call_tool(
        "rc_codex_status",
        {"session_id": "codex-1", "include_nudges": True},
    )

    assert attach_payload["session_id"] == "codex-1"
    assert report_payload["accepted"] is True
    assert steer_payload["pending_nudge_count"] == 1
    assert len(drain_payload["drained"]) == 1
    assert status_payload["pending_nudges"] == []
    assert load_codex_active_session()["last_experiment_id"] == "exp-1"
    assert load_codex_turn_summary("codex-1", 1) == "Reviewed the active experiment."


@pytest.mark.asyncio
async def test_codex_apply_nudges_tool_routes_to_tmux_consumer(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    initialize_workspace()
    monkeypatch.setattr("research_copilot.services.codex_runtime._tmux_pane_exists", lambda pane_id: pane_id == "%81")
    monkeypatch.setattr(
        "research_copilot.services.codex_runtime._tmux_pane_metadata",
        lambda pane_id: {
            "pane_id": "%81",
            "session_name": "codex-1",
            "window_name": "brain",
            "workspace": str(tmp_path),
        },
    )

    await call_tool(
        "rc_codex_attach",
        {
            "session_id": "codex-1",
            "pane_id": "%81",
            "window_name": "brain",
        },
    )
    await call_tool(
        "rc_codex_steer",
        {
            "session_id": "codex-1",
            "kind": "request_summary",
            "message": "Need a tighter recap.",
        },
    )

    sent: list[tuple[str, ...]] = []

    def fake_run_tmux_command(*args: str):
        sent.append(args)
        return None

    monkeypatch.setattr("research_copilot.services.codex_runtime._run_tmux_command", fake_run_tmux_command)

    payload = await call_tool("rc_codex_apply_nudges", {"session_id": "codex-1"})

    assert payload["pending_nudge_count"] == 0
    assert any(args[:3] == ("send-keys", "-t", "%81") for args in sent)
