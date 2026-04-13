# Demo Runbook

This runbook is the operator script for the UI/UX sprint demo.

## Product hierarchy

1. **TUI** (`research-copilot`) — primary observation surface
2. **Workflow CLI** (`research-copilot workflow ...`) — primary action surface
3. **Runtime Codex CLI** (`research-copilot runtime codex-*`) — advanced supervision / recovery only

## Demo goal

Show that a human can read the TUI, pick up the recommended next action, and start or reconcile autonomous research through the workflow CLI without ambiguity.

## Recommended demo sequence

### 1) Bootstrap the workspace

```bash
research-copilot init
research-copilot workflow onboard \
  --goal "Demo the recommended next action flow" \
  --success-criteria "One reviewed run plus a clear next action" \
  --active-profile goal-chaser \
  --autonomy-level bounded
```

### 2) Show the human-readable readiness screen

```bash
research-copilot status
```

Narrate:

- status is the readiness screen
- it now prints the **recommended next action**
- the TUI is read-only, so actions stay in the workflow CLI

### 3) Show the workflow handoff

```bash
research-copilot workflow triage --json
```

Narrate:

- triage is the agent-safe state summary
- the payload exposes `suggested_next_action`
- human-facing copy calls the same idea **recommended next action**

### 4) Seed one local experiment

```bash
research-copilot workflow run-experiment \
  --name "Demo smoke baseline" \
  --command "python -c \"import json; print(json.dumps({'train_loss': 0.11, 'val_loss': 0.14, 'test_loss': 0.16}))\"" \
  --json
```

Narrate:

- `run-experiment` is for bounded local execution
- `launch-experiment` is for tracked job submission

### 5) Open the TUI

```bash
research-copilot
```

Show:

- selected row stays visible
- focus/detail panes stay synchronized
- runtime panel stays readable in a constrained terminal
- the TUI surfaces a **recommended next action** back to the workflow CLI

### 6) Review the result from the workflow CLI

```bash
research-copilot workflow review-results <experiment-id> --json
research-copilot workflow next-step <experiment-id> --json
```

## Optional advanced runtime segment

Only show this if the audience asks about expert supervision:

```bash
research-copilot workflow autonomous-start --brain-driver codex --json
research-copilot workflow autonomous-continue --json
research-copilot runtime codex-attach --session-id demo --pane-id %42 --json
```

Narrate:

- `workflow autonomous-start` / `autonomous-continue` are the canonical managed autonomy path
- `runtime codex-*` is the advanced supervision / recovery path
