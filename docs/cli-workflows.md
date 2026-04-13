# CLI Workflows and Ultrawork Profiles

This document captures the CLI-facing workflow contract for the standalone Research Copilot MVP.
It is the operator and agent reference for the workflow commands and ultrawork profile names.

`research-copilot` is the canonical command. Install it once, run `research-copilot init` in any folder, and keep the workspace-local `.research-copilot/` state authoritative. Existing `.omx/research/` workspaces are compatible during the transition and can be migrated with `research-copilot migrate`. Humans can open the TUI with the bare command; Codex and Claude should use `--json` subcommands against the same workspace.

## Workflow model

Research Copilot keeps a split interface:

- `research-copilot init` bootstraps the current folder for local use.
- `research-copilot migrate` moves legacy `.omx/research/` state into the standalone layout.
- `research-copilot` opens the human TUI dashboard or bootstrap view.
- `research-copilot status` is the human-readable readiness check plus the recommended next action.
- `research-copilot ... --json` is the stable agent-facing surface and never opens the TUI.
- Named workflows package common multi-step research-ops tasks into explicit commands.
- `research-copilot workflow onboard` persists the current single-user operating contract before autonomous work begins.
- `research-copilot workflow autonomous-start` / `autonomous-continue` are the canonical managed autonomy path.
- `research-copilot runtime codex-*` is advanced supervision / recovery for a managed Codex session, not the default operator path.
- Ultrawork profiles package repeatable parallel execution patterns with named lanes and expected outputs.

## Control-plane hierarchy

Use the surfaces in this order:

1. **TUI (`research-copilot`)** — primary observation surface, always read-only
2. **Workflow CLI (`research-copilot workflow ...`)** — primary action surface, including managed autonomy start/continue
3. **Runtime Codex CLI (`research-copilot runtime codex-*`)** — advanced supervision / recovery only

Two wording rules should stay stable across help, docs, and demos:

- **Recommended next action** is the human-facing phrase for what to do next.
- The JSON payload key remains `suggested_next_action` for backward compatibility.

## Install and workspace bootstrap

### Install once

```bash
pipx install research-copilot
```

### Initialize a workspace

Run this in an empty folder or an existing project:

```bash
research-copilot init
```

If you are upgrading from the earlier MVP, migrate legacy state once:

```bash
research-copilot migrate
```

### Open the terminal UI

```bash
research-copilot
```

## Dashboard interaction contract

The terminal dashboard is intentionally human-facing and read-only. The current interaction split is:

- **v1a core navigation:** `1-4` screen jumps, `[` / `]` screen cycling, `Tab` pane focus, `j` / `k` selection movement, `Enter` drill-down, `g` linked-research modal, `?` help, and `r` refresh.
- **v1b read-only expansion:** pane-local search/filter/sort, an actionable read-only palette, richer contextual inspection, and deeper log drill-down for the selected run or experiment.
- **Mutation boundary:** no workflow-mutating actions are introduced through the TUI in v1a or v1b; operators inspect in the dashboard and use explicit CLI/JSON commands for actual state changes.

The canonical snapshot remains overview-only. It carries stable entity identity, typed links, and log summaries, but it must not embed full log bodies. When the dashboard needs deeper inspection, it resolves the selected run or linked experiment by stable entity ID and uses a dedicated retrieval surface for the full log payload.

### Companion usage

- **Human operator:** use `research-copilot` for the TUI and `research-copilot status` for a quick readiness check.
- **Codex / Claude companion:** use `research-copilot workflow ... --json` from a second terminal or agent pane.
- **Shared state:** both paths read and write the same local `.research-copilot/` files; `.omx/research/` is a legacy compatibility input.

Unless a section says otherwise, the examples below assume the workspace has already been initialized once with `research-copilot init`.

## Bootstrap command summary

| Command | Purpose | Primary CLI surface | Expected outcome |
| --- | --- | --- | --- |
| `init` | Bootstrap the current folder for local use | `research-copilot init` | Created or confirmed `.research-copilot/` workspace state |
| `migrate` | Move legacy state into the standalone layout | `research-copilot migrate` | Legacy `.omx/research/` imported into `.research-copilot/` |

## Workflow command summary

| Workflow | Purpose | Primary CLI surface | Expected outcome |
| --- | --- | --- | --- |
| `onboard` | Capture the current solo-user operating contract | `research-copilot workflow onboard ... [--json]` | Persisted goal, autonomy bounds, and next-step hints |
| `onboard-show` | Review the saved contract | `research-copilot workflow onboard-show [--json]` | Current goal/profile plus suggested next commands |
| `autonomous-start` | Start managed autonomy with the selected brain driver | `research-copilot workflow autonomous-start ... [--json]` | One active managed runtime created |
| `autonomous-continue` | Reuse or reconcile managed autonomy safely | `research-copilot workflow autonomous-continue ... [--json]` | Healthy runtime reused or stale/orphaned runtime reconciled |
| `triage` | Inspect current state quickly | `research-copilot workflow triage [--json]` | Current-state summary with blockers and the recommended next action |
| `launch-experiment` | Register an experiment and submit a tracked job | `research-copilot workflow launch-experiment ... [--json]` | Experiment created, job submitted, tracking linked |
| `monitor-run` | Follow a running job or experiment | `research-copilot workflow monitor-run <id> [--json]` | Fresh status, logs, and notable events |
| `review-results` | Interpret completed work | `research-copilot workflow review-results <id> [--json]` | Result summary, insights, and next-step context |
| `research-context` | Search and persist literature/context | `research-copilot workflow research-context <query> [--json]` | Reading list, saved papers, and context updates |
| `run-experiment` | Execute a bounded local experiment command | `research-copilot workflow run-experiment ... [--json]` | Persisted run artifact linked to an experiment |
| `overfitting-check` | Review metric gaps for a finished experiment | `research-copilot workflow overfitting-check <id> [--json]` | Diagnostics about train/validation/test divergence |
| `next-step` | Propose the most likely follow-up action | `research-copilot workflow next-step <id> [--json]` | Suggested experiment or evaluation move |

## Common command distinctions

### `status` vs `triage`

- `status` is the human-readable readiness screen plus active-runtime health.
- `triage` is the machine-safe workflow summary and the place to fetch the recommended next action programmatically.

### `launch-experiment` vs `run-experiment`

- `launch-experiment` creates a tracked experiment and submits a job into the managed run path.
- `run-experiment` executes a bounded local command and persists the resulting run artifact.
- Prefer `run-experiment` for smoke tests, local baselines, and demos.
- Prefer `launch-experiment` for tracked job submission and longer-running managed work.

## Named workflows

### `onboard`

**Purpose:** capture the single-user research contract before running autonomous loops.

**When to run:** use first in a fresh workspace, when the goal changes, or when a new operator takes over the local machine.

**Primary actions:**
- persist the current goal and success criteria
- record autonomy limits and stop conditions
- select the preferred ultrawork profile
- seed immediate next-step commands

**Representative command flow:**
```bash
research-copilot init
research-copilot workflow onboard \
  --goal "Validate the random-data smoke baseline" \
  --success-criteria "Persist one reviewed run and a next step" \
  --active-profile goal-chaser \
  --autonomy-level bounded
research-copilot workflow onboard-show --json
research-copilot status
```

**Expected output:**
- initialized local workspace state under `.research-copilot/`
- saved onboarding contract
- clear goal/profile summary
- next command suggestions such as `workflow triage`

### `triage`

**Purpose:** inspect what is happening now.

**When to run:** use first when a researcher or agent needs a fast picture of jobs, experiments, logs, and saved context.

**Primary actions:**
- list jobs
- list experiments
- tail recent logs
- read the latest workflow snapshot

**Representative command flow:**
```bash
research-copilot snapshot --json
research-copilot jobs list --json
research-copilot experiments list --json
research-copilot workflow triage --json
```

**Expected output:**
- initialized local workspace state under `.research-copilot/`
- current-state summary
- top blockers or anomalies
- recommended next action

**TUI follow-up:** after triage seeds the workspace, the human operator can open `research-copilot`, move through runs/experiments/research panes, and keep the CLI as the mutating control surface.

### `launch-experiment`

**Purpose:** register a new experiment and submit its tracked job safely.

**When to run:** use when a new hypothesis/config should become a tracked experiment with linked execution metadata.

**Primary actions:**
- create experiment metadata
- attach config and hypothesis notes
- submit a job
- link experiment and job identifiers

**Representative command flow:**
```bash
research-copilot experiments create ... --json
research-copilot jobs submit ... --json
research-copilot experiments update <experiment-id> ... --json
research-copilot workflow launch-experiment ... --json
```

**Expected output:**
- initialized local workspace state under `.research-copilot/`
- registered experiment
- submission artifact or job identifier
- verification notes for the launch

### `monitor-run`

**Purpose:** follow a running job or experiment.

**When to run:** use during active execution when an operator needs current status, logs, and noteworthy events.

**Primary actions:**
- inspect job status
- tail logs
- refresh workflow snapshot
- record notable observations

**Representative command flow:**
```bash
research-copilot jobs get <job-id> --json
research-copilot jobs logs <job-id> --json
research-copilot snapshot --json
research-copilot workflow monitor-run <id> --json
```

**Expected output:**
- initialized local workspace state under `.research-copilot/`
- refreshed run status
- recent log highlights
- saved notes or operator follow-up items

**TUI follow-up:** the dashboard should show log summaries directly in the runs detail pane; deeper full-log inspection remains an on-demand path and must stay outside the canonical snapshot payload.

### `review-results`

**Purpose:** analyze completed work and compare outcomes.

**When to run:** use after a job or experiment finishes and the team needs an interpretation pass before deciding what to keep, rerun, or discard.

**Primary actions:**
- inspect experiment details
- review summaries and outputs
- attach insights
- persist next-step context

**Representative command flow:**
```bash
research-copilot experiments get <experiment-id> --json
research-copilot insights add ... --json
research-copilot context set <key> ... --json
research-copilot workflow review-results <id> --json
```

**Expected output:**
- initialized local workspace state under `.research-copilot/`
- result summary
- keep/drop recommendation
- saved insight or context record

### `research-context`

**Purpose:** search, save, and maintain research context.

**When to run:** use when a workflow needs supporting literature, prior experiment context, or persistent notes for future runs.

**Primary actions:**
- search papers
- save relevant papers
- inspect saved papers/context
- update notes and context entries

**Representative command flow:**
```bash
research-copilot papers search <query> --json
research-copilot papers save ... --json
research-copilot context list --json
research-copilot workflow research-context <query> --json
```

**Expected output:**
- initialized local workspace state under `.research-copilot/`
- short reading list
- saved papers
- updated context or notes

### `run-experiment`

**Purpose:** execute a bounded local command and persist the resulting artifact into the single-user workflow.

**When to run:** use for local smoke experiments, quick baselines, or bounded command execution that should be tracked like any other experiment.

**Primary actions:**
- create or reuse an experiment record
- execute the local command
- parse the emitted result payload
- persist the run artifact for later review

**Representative command flow:**
```bash
research-copilot workflow run-experiment \
  --name "Random baseline smoke" \
  --command "python -c \"import json, random; rng = random.Random(7); print(json.dumps({'train_loss': 0.11, 'val_loss': 0.14, 'test_loss': 0.16}))\"" \
  --json
research-copilot workflow overfitting-check <experiment-id> --json
research-copilot workflow next-step <experiment-id> --json
```

**Expected output:**
- initialized local workspace state under `.research-copilot/`
- persisted run artifact
- metrics ready for review
- explicit next-step recommendation

### `overfitting-check`

**Purpose:** inspect a completed experiment for obvious train/validation/test drift.

**When to run:** use after `run-experiment`, `launch-experiment`, or any finished run that emitted comparable loss metrics.

**Expected output:**
- metric gap diagnostics
- quick overfitting signal summary

### `next-step`

**Purpose:** turn a finished experiment into a concrete follow-up action.

**When to run:** use after `review-results` or `overfitting-check` when the operator needs the next bounded move.

**Expected output:**
- ranked suggestions for the next run or review step

## Ultrawork profile summary

| Profile | Use when | Lanes | Expected output |
| --- | --- | --- | --- |
| `goal-chaser` | Drive a bounded loop toward a named research goal | goal/current state, experiment/run execution, review/next-step loop | Goal progress summary, blockers, next bounded action |
| `baseline-improver` | Try to beat an existing baseline in a controlled way | baseline/context, improvement experiment path, comparison/recommendation | Comparison summary, improvement delta, keep/drop recommendation |
| `overfit-hunter` | Diagnose and reduce overfitting | train/val/test gap inspection, regularization hypothesis path, diagnosis/next test | Overfitting diagnosis, candidate fixes, next bounded experiment |
| `result-reasoner` | Interpret existing runs and turn them into explicit next steps | metrics/log analysis, context/baseline comparison, decision/review artifact | Result summary, decision rationale, next-step artifact |
| `explore-improver` | Explore bounded improvements when the target is still loose | current-state exploration, candidate improvement path, result reasoning | What changed, what helped, next experiment to try |

## Ultrawork profiles

### `goal-chaser`

**Use when:** a solo researcher has a concrete target and wants disciplined iteration.

**Lanes:**
1. goal + current-state summary
2. experiment/run execution path
3. review + next-step loop

**Expected output:**
- goal progress summary
- current blockers
- next bounded action

### `baseline-improver`

**Use when:** there is already a baseline and the next work is focused on measurable improvement.

**Lanes:**
1. baseline/context summary
2. improvement experiment path
3. comparison + recommendation

**Expected output:**
- comparison summary
- improvement delta
- keep/drop recommendation

### `overfit-hunter`

**Use when:** generalization problems are suspected and the next work should focus on overfitting signals.

**Lanes:**
1. train/val/test gap inspection
2. regularization/data hypothesis path
3. overfitting diagnosis + next test

**Expected output:**
- overfitting diagnosis
- candidate fixes
- next bounded experiment

### `result-reasoner`

**Use when:** runs already exist and the main need is synthesis rather than launch.

**Lanes:**
1. metrics/log analysis
2. context + baseline comparison
3. decision + review artifact

**Expected output:**
- result summary
- decision rationale
- next-step artifact

### `explore-improver`

**Use when:** the user wants bounded exploratory improvement for learning value, not only for a strict metric target.

**Lanes:**
1. current-state exploration
2. candidate improvement path
3. result reasoning + next exploration

**Expected output:**
- what changed
- what helped
- next experiment to try

## Help-text guidance

These workflow and profile names should stay stable across CLI help, docs, and future MCP parity.

Recommended command group labels:
- `workflow` for the named workflow commands
- `ultrawork profile` for listing named presets
- `ultrawork run <profile>` for executing a preset

Recommended help-text themes:
- describe the operator goal first
- show the expected artifact or decision produced by the command
- point new users to onboarding before autonomous execution
- advertise `--json` for agent-safe automation
- point first-time users to `workflow onboard`
- prefer the phrase `recommended next action` in human-facing help/status copy
- keep TUI references human-facing and read-only where possible
