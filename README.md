# Research Copilot

Research Copilot is a terminal-first CLI/TUI for single-user ML research operations. The shipped MVP keeps the human dashboard in the terminal, exposes agent-safe JSON commands, and supports local research loops without a web surface.

## Product direction

The primary user experience is a full-screen TUI inspired by tools such as lazygit and lazydocker:

- overview of active runs and job health
- job and log inspection
- experiment status, configuration, and results
- knowledge-base and saved-paper context in secondary views

The `research-copilot` command remains the main entrypoint. Non-interactive utilities such as onboarding, workflow execution, configuration inspection, and database bootstrap stay available for scripting and operator automation.

## Single-user quickstart

1. Inspect the available surface:
   ```bash
   python -m research_copilot.main --help
   research-copilot status
   ```
2. Capture the current solo-research contract:
   ```bash
   research-copilot workflow onboard \
     --goal "Check whether a random-data baseline is behaving sensibly" \
     --success-criteria "Store one reviewed run with a next step" \
     --active-profile goal-chaser \
     --autonomy-level bounded \
     --allowed-action "launch runs" \
     --allowed-action "review results" \
     --constraint "single-user only" \
     --stop-condition "stop after one reviewed run"
   ```
3. Triage the current workspace and confirm the next action:
   ```bash
   research-copilot workflow triage --json
   ```
4. Run a local random-data smoke experiment and persist its artifact:
   ```bash
   research-copilot workflow run-experiment \
     --name "Random baseline smoke" \
     --command "python -c \"import json, random; rng = random.Random(7); values = [rng.random() for _ in range(8)]; print(json.dumps({'train_loss': round(sum(values[:3]) / 3, 3), 'val_loss': round(sum(values[3:6]) / 3, 3), 'test_loss': round(sum(values[5:8]) / 3, 3)}))\"" \
     --json
   ```
5. Review the persisted run and generate the next step:
   ```bash
   research-copilot workflow overfitting-check <experiment-id> --json
   research-copilot workflow next-step <experiment-id> --json
   ```

## Workflow references

- `docs/cli-workflows.md` — workflow and ultrawork command reference, including onboarding and reasoning helpers
- `docs/seeded-solo-cli-scenario.md` — focused smoke scenario for the seeded solo CLI path

## Solo quickstart

For the single-user MVP, the smoothest path is:

1. Capture the research contract:
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
2. Check the next suggested action:
   ```bash
   research-copilot workflow triage --json
   ```
3. Execute a local Python experiment:
   ```bash
   research-copilot workflow run-experiment \
     --name "random baseline" \
     --command "python -c \"import json, random; rng=random.Random(7); print(json.dumps({'train_loss': round(rng.random(), 3), 'val_loss': round(rng.random(), 3), 'test_loss': round(rng.random(), 3)}))\"" \
     --json
   ```
4. Review and decide the next step:
   ```bash
   research-copilot workflow overfitting-check <experiment-id> --json
   research-copilot workflow next-step <experiment-id> --json
   ```

For a fuller mock-backed CLI proof, see `docs/seeded-solo-cli-scenario.md`.

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
- `research-copilot status` points new users to onboarding or triage
- active jobs/runs can be inspected from the terminal flow
- the legacy web app package is gone from the shipped workflow
