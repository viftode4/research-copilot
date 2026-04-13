"""Minimal stdio MCP transport for Research Copilot."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, BinaryIO

from research_copilot import __version__
from research_copilot.integrations.mcp.schemas import MCP_PROTOCOL_VERSION
from research_copilot.integrations.mcp.tools import ToolArgumentError, call_tool, list_mcp_tools


class JsonRpcError(Exception):
    """Structured JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _jsonrpc_success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _jsonrpc_failure(message_id: Any, error: JsonRpcError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {
            "code": error.code,
            "message": error.message,
        },
    }
    if error.data is not None:
        payload["error"]["data"] = error.data
    return payload


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True, default=str)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def write_framed_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stream.write(header)
    stream.write(body)
    stream.flush()


def write_delimited_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """Write one newline-delimited JSON-RPC message for stdio MCP transport."""

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    stream.write(body)
    stream.write(b"\n")
    stream.flush()


def read_framed_message(stream: BinaryIO) -> dict[str, Any] | None:
    first_line = stream.readline()
    if not first_line:
        return None

    if first_line.startswith(b"Content-Length:"):
        header_bytes = bytearray(first_line)
        while True:
            line = stream.readline()
            if not line:
                raise EOFError("Unexpected EOF while reading MCP headers.")
            header_bytes.extend(line)
            if header_bytes.endswith(b"\r\n\r\n") or header_bytes.endswith(b"\n\n"):
                break

        headers: dict[str, str] = {}
        for raw_line in header_bytes.decode("ascii").splitlines():
            if not raw_line:
                continue
            name, _, value = raw_line.partition(":")
            headers[name.lower()] = value.strip()

        content_length = headers.get("content-length")
        if not content_length:
            raise ValueError("Missing Content-Length header.")

        body = stream.read(int(content_length))
        if len(body) != int(content_length):
            raise EOFError("Unexpected EOF while reading MCP body.")
        return json.loads(body.decode("utf-8"))

    raw = first_line.strip()
    if not raw:
        return read_framed_message(stream)
    return json.loads(raw.decode("utf-8"))


class ResearchCopilotMcpServer:
    """Small MCP server that exposes the approved v1 Research Copilot tool surface."""

    def __init__(self) -> None:
        self._initialized = False

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params", {})

        try:
            if method == "initialize":
                return _jsonrpc_success(message_id, self._handle_initialize())
            if method == "notifications/initialized":
                self._initialized = True
                return None
            if method == "ping":
                return _jsonrpc_success(message_id, {})
            if method == "tools/list":
                self._require_initialized()
                return _jsonrpc_success(message_id, {"tools": list_mcp_tools()})
            if method == "tools/call":
                self._require_initialized()
                return _jsonrpc_success(message_id, await self._handle_tool_call(params))
            if method is None:
                raise JsonRpcError(-32600, "Invalid Request", {"reason": "Missing method."})
            if message_id is None:
                return None
            raise JsonRpcError(-32601, f"Method not found: {method}")
        except JsonRpcError as error:
            if message_id is None:
                return None
            return _jsonrpc_failure(message_id, error)

    def _handle_initialize(self) -> dict[str, Any]:
        self._initialized = False
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {
                    "listChanged": False,
                }
            },
            "serverInfo": {
                "name": "research-copilot",
                "version": __version__,
            },
        }

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise JsonRpcError(-32002, "Server not initialized. Send notifications/initialized first.")

    async def _handle_tool_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(-32602, "Invalid params", {"reason": "tools/call requires a tool name."})

        try:
            payload = await call_tool(name, params.get("arguments") or {})
        except ToolArgumentError as exc:
            return _tool_result({"ok": False, "error": {"code": "INVALID_ARGUMENTS", "message": str(exc)}}, is_error=True)
        except ValueError as exc:
            return _tool_result({"ok": False, "error": {"code": "TOOL_EXECUTION_FAILED", "message": str(exc)}}, is_error=True)

        return _tool_result(payload)


async def run_stdio_server(
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    *,
    server: ResearchCopilotMcpServer | None = None,
) -> None:
    server = server or ResearchCopilotMcpServer()
    source = input_stream or sys.stdin.buffer
    sink = output_stream or sys.stdout.buffer

    while True:
        try:
            message = read_framed_message(source)
        except EOFError:
            break
        if message is None:
            break
        response = await server.handle_message(message)
        if response is not None:
            write_delimited_message(sink, response)


def serve_stdio(input_stream: BinaryIO | None = None, output_stream: BinaryIO | None = None) -> None:
    """Run the Research Copilot MCP server over stdio until EOF."""

    asyncio.run(run_stdio_server(input_stream=input_stream, output_stream=output_stream))
