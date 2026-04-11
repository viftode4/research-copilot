# Research Copilot

Research Copilot is pivoting from a prototype web UI to a terminal-first workflow app for ML research operations.

## Product direction

The primary user experience is a full-screen TUI inspired by tools such as lazygit and lazydocker:

- overview of active runs and job health
- job and log inspection
- experiment status, configuration, and results
- knowledge-base and saved-paper context in secondary views

The `research-copilot` command remains the main entrypoint. Non-interactive utilities such as configuration inspection and database bootstrap stay available for scripting and admin tasks.

## Legacy web surface

The old FastAPI chat/dashboard surface is no longer the intended workflow. During the transition, the legacy import path is kept only as a compatibility shim that explains the terminal-first pivot.

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
- the legacy web app is no longer the shipped primary interface
