"""Minimal stdio MCP adapter for Research Copilot."""

from research_copilot.integrations.mcp.schemas import APPROVED_V1_TOOL_NAMES, MCP_PROTOCOL_VERSION
from research_copilot.integrations.mcp.server import ResearchCopilotMcpServer, serve_stdio

__all__ = [
    "APPROVED_V1_TOOL_NAMES",
    "MCP_PROTOCOL_VERSION",
    "ResearchCopilotMcpServer",
    "serve_stdio",
]
