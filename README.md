# Research Copilot

Research Copilot is pivoting from a prototype web UI to a terminal-first workflow app for ML research operations.

## Product direction

The primary user experience is a full-screen TUI inspired by tools such as lazygit and lazydocker:

- overview of active runs and job health
- job and log inspection
- experiment status, configuration, and results
- knowledge-base and saved-paper context in secondary views

The `research-copilot` command remains the main entrypoint. Non-interactive utilities such as configuration inspection and database bootstrap stay available for scripting and admin tasks.

## Workflow command reference

Planned agent-facing workflow commands and ultrawork profile contracts are documented in
`docs/cli-workflows.md`.

## Web surface removal

The old FastAPI chat/dashboard surface has been removed. The supported workflow is now the terminal UI launched by `research-copilot`.

## Development verification

Run the standard verification loop before shipping changes:

```bash
python -m pytest
python -m ruff check .
python -m research_copilot.main --help
```

The final integrated CLI/TUI pivot should also verify that:

- `research-copilot` launches the terminal UI
- active jobs/runs can be inspected from the terminal flow
- the legacy web app package is gone from the shipped workflow
