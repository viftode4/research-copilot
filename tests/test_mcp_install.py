"""Tests for MCP install/config rendering helpers and CLI commands."""

from __future__ import annotations

from click.testing import CliRunner

from research_copilot.integrations.mcp.install import (
    render_agents_snippet,
    render_claude_config,
    render_codex_config,
)
from research_copilot.main import cli


def test_render_codex_config_includes_stdio_install_and_config_toml(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    rendered = render_codex_config()

    assert "codex mcp add" in rendered
    assert "research-copilot mcp serve" in rendered
    assert "[mcp_servers.research-copilot]" in rendered
    assert 'RC_WORKING_DIR = "' in rendered


def test_render_claude_config_uses_project_scoped_json_and_env_placeholders():
    rendered = render_claude_config()

    assert "Save this as .mcp.json" in rendered
    assert '"mcpServers"' in rendered
    assert '"research-copilot"' in rendered
    assert "${RESEARCH_COPILOT_BIN:-research-copilot}" in rendered
    assert "${RESEARCH_COPILOT_WORKSPACE:-.}" in rendered
    assert "prompt for approval" in rendered
    assert "fail to parse" in rendered


def test_render_agents_snippet_prefers_mcp_tools_over_cli_fallback():
    rendered = render_agents_snippet()

    assert "Always use the `research-copilot` MCP server" in rendered
    assert "rc_triage" in rendered
    assert "research-copilot ... --json" in rendered


def test_mcp_cli_help_and_render_commands_are_available(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    help_result = runner.invoke(cli, ["mcp", "--help"])
    codex_result = runner.invoke(cli, ["mcp", "print-codex-config"])
    claude_result = runner.invoke(cli, ["mcp", "print-claude-config"])
    agents_result = runner.invoke(cli, ["mcp", "print-agents-snippet"])

    assert help_result.exit_code == 0, help_result.output
    assert "serve" in help_result.output
    assert "print-codex-config" in help_result.output
    assert "print-claude-config" in help_result.output
    assert "print-agents-snippet" in help_result.output

    assert codex_result.exit_code == 0, codex_result.output
    assert "codex mcp add" in codex_result.output

    assert claude_result.exit_code == 0, claude_result.output
    assert '".mcp.json"' not in claude_result.output
    assert '"mcpServers"' in claude_result.output

    assert agents_result.exit_code == 0, agents_result.output
    assert "Always use the `research-copilot` MCP server" in agents_result.output
