"""Helpers for rendering local MCP installation/configuration snippets."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MCP_SERVER_NAME = "research-copilot"
DEFAULT_MCP_COMMAND = "research-copilot"
DEFAULT_MCP_ARGS = ("mcp", "serve")


def _workspace_hint() -> str:
    return Path.cwd().resolve().as_posix()


def render_codex_config(
    *,
    server_name: str = DEFAULT_MCP_SERVER_NAME,
    command: str = DEFAULT_MCP_COMMAND,
) -> str:
    """Render a copy/pasteable Codex MCP setup snippet."""
    workspace = _workspace_hint()
    args = ", ".join(f'"{part}"' for part in DEFAULT_MCP_ARGS)
    agents_hint = render_agents_snippet(server_name=server_name)
    return (
        "Codex MCP quickstart\n"
        "====================\n"
        "1. Add the local stdio server:\n"
        f"   codex mcp add --env RC_WORKING_DIR=\"{workspace}\" {server_name} -- {command} mcp serve\n"
        "2. Verify it is registered:\n"
        "   codex mcp list\n"
        "\n"
        "Equivalent ~/.codex/config.toml entry:\n"
        f"[mcp_servers.{server_name}]\n"
        f'command = "{command}"\n'
        f"args = [{args}]\n"
        f'env = {{ RC_WORKING_DIR = "{workspace}" }}\n'
        "\n"
        "Suggested AGENTS.md reminder:\n"
        f"{agents_hint}\n"
    )


def render_claude_config(
    *,
    server_name: str = DEFAULT_MCP_SERVER_NAME,
) -> str:
    """Render a project-scoped Claude Code `.mcp.json` example."""
    payload = {
        "mcpServers": {
            server_name: {
                "command": "${RESEARCH_COPILOT_BIN:-research-copilot}",
                "args": list(DEFAULT_MCP_ARGS),
                "env": {
                    "RC_WORKING_DIR": "${RESEARCH_COPILOT_WORKSPACE:-.}",
                },
            }
        }
    }
    config_json = json.dumps(payload, indent=2)
    return (
        "Claude Code project-scoped MCP config\n"
        "=====================================\n"
        "Save this as .mcp.json at the repository root.\n"
        "Claude Code will prompt for approval before using a project-scoped server from .mcp.json.\n"
        "This example keeps machine-specific values behind env placeholders.\n"
        "If you remove the default from ${VAR:-default}, Claude Code will fail to parse the file when VAR is missing.\n"
        "\n"
        f"{config_json}\n"
    )


def render_agents_snippet(*, server_name: str = DEFAULT_MCP_SERVER_NAME) -> str:
    """Render a compact AGENTS.md snippet that nudges tool use."""
    return (
        f"Always use the `{server_name}` MCP server for research workflow context in this repo.\n"
        "Prefer `rc_status`, `rc_triage`, `rc_onboard`, `rc_run_experiment`, "
        "`rc_review_results`, and `rc_next_step` over shelling out to "
        "`research-copilot ... --json`.\n"
        "Use the CLI JSON commands only when the MCP server is unavailable or a needed tool has not shipped yet."
    )
