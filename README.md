# Research Copilot

Research Copilot is a terminal-first CLI/TUI for single-user ML research operations. The shipped MVP keeps the human dashboard in the terminal, exposes agent-safe JSON commands, and supports local research loops without a web surface.

## Product direction

The primary user experience is a full-screen TUI inspired by tools such as lazygit and lazydocker:

- overview of active runs and job health
- job and log inspection
- experiment status, configuration, and results
- knowledge-base and saved-paper context in secondary views

The `research-copilot` command is the canonical entrypoint. Install it once, initialize a workspace in any folder, and keep humans and agents on the same local state. Non-interactive utilities such as onboarding, workflow execution, configuration inspection, and workspace bootstrap stay available for scripting and operator automation.

## Single-user quickstart

1. Install once:
   ```bash
   pipx install research-copilot
   ```
   If you are developing from a checkout, use the editable install instead.
2. Bootstrap the current folder:
   ```bash
   research-copilot init
   ```
   This is safe in an empty folder or an existing project.
3. Open the terminal UI:
   ```bash
   research-copilot
   ```
4. Capture the current solo-research contract:
   ```bash
   research-copilot workflow onboard \
     --goal "Test whether random.Random() shows simple patterns" \
     --success-criteria "Persist one completed run with a review artifact" \
     --active-profile result-reasoner \
     --autonomy-level bounded \
     --allowed-action "run local experiments" \
     --allowed-action "review results" \
     --constraint "single-user only" \
     --stop-condition "stop on repeated failure" \
     --json
   ```
5. Triage the workspace and confirm the next action:
   ```bash
   research-copilot workflow triage --json
   ```
6. Execute a local Python experiment:
   ```bash
   research-copilot workflow run-experiment \
     --name "random baseline" \
     --command "python -c \"import json, random; rng=random.Random(7); print(json.dumps({'train_loss': round(rng.random(), 3), 'val_loss': round(rng.random(), 3), 'test_loss': round(rng.random(), 3)}))\"" \
     --json
   ```
7. Review the run and decide the next bounded move:
   ```bash
   research-copilot workflow overfitting-check <experiment-id> --json
   research-copilot workflow next-step <experiment-id> --json
   ```

## Workflow references

- `docs/cli-workflows.md` — canonical workflow, install, init, and agent-companion reference
- `docs/seeded-solo-cli-scenario.md` — focused smoke scenario for the seeded solo CLI path

## Web surface removal

The old FastAPI chat/dashboard surface has been removed. The supported workflow is now the terminal UI launched by `research-copilot`.

## Companion usage

- **Human path:** `research-copilot init` in a folder, then `research-copilot` to open the TUI.
- **Agent path:** use `research-copilot ... --json` from Codex or Claude Code against the same folder.
- **Workspace rule:** the local `.omx/research` state is authoritative; the terminal UI and agent commands both read and write that shared state.

## Development verification

Run the standard verification loop before shipping changes:

```bash
pytest
ruff check .
research-copilot --help
```

The final integrated CLI/TUI pivot should also verify that:

- `research-copilot init` works in a fresh folder and in an existing repo
- `research-copilot` launches the terminal UI
- `research-copilot status` points new users to onboarding or triage
- active jobs/runs can be inspected from the terminal flow
- the legacy web app package is gone from the shipped workflow
