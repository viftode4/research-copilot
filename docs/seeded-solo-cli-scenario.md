# Seeded Solo CLI Scenario

This example documents the single-operator CLI flow covered by `tests/test_cli_solo_scenario.py`.
It stays CLI-only, uses the in-memory mock backends, and proves the current MVP flow without a DB,
web surface, or multi-user coordination. The canonical workspace state is `.research-copilot/`;
legacy `.omx/research/` workspaces can be migrated with `research-copilot migrate`.

Start every workspace with the canonical bootstrap command:

```bash
research-copilot init
```

If this is an older MVP workspace, migrate it once before continuing:

```bash
research-copilot migrate
```

Before running the seeded mock flow, the recommended onboarding command for the local
random-data Python case is:

```bash
research-copilot workflow onboard \
  --goal "Test whether random.Random() shows simple patterns" \
  --success-criteria "Persist one completed experiment with a written review" \
  --active-profile result-reasoner \
  --autonomy-level bounded \
  --allowed-action "run local experiments" \
  --allowed-action "review results" \
  --constraint "single-user only" \
  --stop-condition "stop on repeated failure" \
  --json
```
This agent-safe form stays noninteractive: it does not open the TUI or prompt for input.

For humans, the bare `research-copilot` command is the interactive bootstrap/dashboard entrypoint:
it opens the TUI when initialized, or shows bootstrap guidance when the workspace is empty.

## Scenario

0. Capture the solo-user operating contract:
   ```bash
   research-copilot workflow onboard \
     --goal "Validate the seeded solo PFN flow" \
     --success-criteria "Persist one reviewed run and next step" \
     --active-profile goal-chaser \
     --autonomy-level bounded \
     --allowed-action "launch runs" \
     --constraint "single-user only" \
     --stop-condition "stop after one reviewed run"
   research-copilot status
   ```

1. Triage the empty workspace:
   ```bash
   research-copilot workflow triage --json
   ```
   Expected next action: `launch-experiment`

2. Seed supporting literature/context:
   ```bash
   research-copilot workflow research-context "PFN learning curves" \
     --save-first \
     --relevance-notes "Relevant to the next PFN ablation run." \
     --context-key literature_focus \
     --context-value "Prioritize PFN extrapolation papers." \
     --json
   ```

3. Launch a tracked experiment:
   ```bash
   research-copilot workflow launch-experiment \
     --name "Solo PFN run" \
     --script "#!/bin/bash\npython train.py --dataset lcdb --seed 7" \
     --hypothesis "PFNs should stabilize on the LCDB slice." \
     --dataset LCDB \
     --model-type PFN \
     --tag solo \
     --created-by solo-operator \
     --json
   ```

4. Monitor the run until completion:
   ```bash
   research-copilot workflow monitor-run <experiment-id> --kind experiment --json
   research-copilot workflow monitor-run <experiment-id> --kind experiment --json
   ```
   Expected transition: `RUNNING` -> `COMPLETED`

5. Review the finished run and persist follow-up notes:
   ```bash
   research-copilot workflow review-results <experiment-id> \
     --insight-title "Keep the PFN baseline" \
     --insight-content "The seeded mock run finished with acceptable validation loss." \
     --context-key next_step \
     --context-value "Compare against the LightGBM baseline." \
     --json
   ```

6. Confirm the combined snapshot:
   ```bash
   research-copilot snapshot --json
   ```
   Expected artifacts:
   - 1 completed experiment
   - 1 linked job
   - 1 saved paper
   - 1 saved insight
   - 2 context entries

## Verification

Run the focused end-to-end proof:

```bash
pytest tests/test_cli_solo_scenario.py -q
```
