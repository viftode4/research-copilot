# CLI Workflows and Ultrawork Profiles

This document captures the CLI-facing workflow contract from `.omx/plans/prd-cli-research-ops-control-plane.md`.
It is the operator and agent reference for the planned workflow commands and ultrawork profile names.

## Workflow model

Research Copilot keeps a split interface:

- `research-copilot` opens the human TUI dashboard.
- `research-copilot ... --json` is the stable agent-facing surface.
- Named workflows package common multi-step research-ops tasks into explicit commands.
- Ultrawork profiles package repeatable parallel execution patterns with named lanes and expected outputs.

## Workflow command summary

| Workflow | Purpose | Primary CLI surface | Expected outcome |
| --- | --- | --- | --- |
| `onboard` | Capture the single-user research contract | `research-copilot workflow onboard [--json]` | Saved goal/profile/constraints plus suggested next steps |
| `onboard-show` | Inspect the current onboarding contract | `research-copilot workflow onboard-show [--json]` | Current contract and recommended next commands |
| `triage` | Inspect current state quickly | `research-copilot workflow triage [--json]` | Current-state summary with blockers and next action |
| `launch-experiment` | Register and launch a tracked job safely | `research-copilot workflow launch-experiment ... [--json]` | Experiment created, job submitted, tracking linked |
| `monitor-run` | Follow a running job or experiment | `research-copilot workflow monitor-run <id> [--json]` | Fresh status, logs, and notable events |
| `review-results` | Interpret completed work | `research-copilot workflow review-results <id> [--json]` | Result summary, insights, and next-step context |
| `research-context` | Search and persist literature/context | `research-copilot workflow research-context <query> [--json]` | Reading list, saved papers, and context updates |
| `run-experiment` | Execute a local experiment command | `research-copilot workflow run-experiment ... [--json]` | Persisted run artifact, experiment record, and parsed metrics |
| `overfitting-check` | Diagnose simple train/validation/test gaps | `research-copilot workflow overfitting-check <id> [--json]` | Review artifact with score gaps and diagnostics |
| `next-step` | Suggest the next action after a run | `research-copilot workflow next-step <id> [--json]` | Lightweight recommendation artifact |

## Named workflows

### `onboard`

**Purpose:** capture the current single-user research contract before running work.

**When to run:** use first for a new local project, fresh random-data investigation, or when the
operator wants explicit limits before the MVP starts taking actions.

**Primary actions:**
- persist the goal and success criteria
- record the active autonomous profile
- capture allowed actions, constraints, and stop conditions
- seed the current goal/profile into research context

**Representative command flow:**
```bash
research-copilot workflow onboard
research-copilot workflow onboard --json ...
research-copilot workflow onboard-show --json
```

**Expected output:**
- saved onboarding contract
- current profile and autonomy level
- immediate next commands for the solo loop

### `onboard-show`

**Purpose:** inspect the saved onboarding contract without mutating it.

**When to run:** use before triage or manual review when you need to confirm the goal, profile, or
operator limits currently in effect.

**Primary actions:**
- load the saved contract
- report whether onboarding exists
- suggest the next command sequence

**Representative command flow:**
```bash
research-copilot workflow onboard-show --json
```

**Expected output:**
- configured/unconfigured status
- saved contract fields
- recommended next steps

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
- current-state summary
- top blockers or anomalies
- suggested next action

### `launch-experiment`

**Purpose:** register and launch a new experiment safely.

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
- refreshed run status
- recent log highlights
- saved notes or operator follow-up items

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
- short reading list
- saved papers
- updated context or notes

### `run-experiment`

**Purpose:** execute a real local command and store its output as a tracked run.

**When to run:** use for the single-user random-data Python path when the experiment can run on
the local machine and emit structured JSON metrics.

**Primary actions:**
- create or reuse an experiment record
- execute a local shell command
- parse structured metrics from stdout
- persist the run artifact under `.omx/research/runs/`

**Representative command flow:**
```bash
research-copilot workflow run-experiment \
  --name "random baseline" \
  --command "python -c \"import json, random; rng=random.Random(7); print(json.dumps({'train_loss': round(rng.random(), 3), 'val_loss': round(rng.random(), 3), 'test_loss': round(rng.random(), 3)}))\"" \
  --json
```

**Expected output:**
- persisted run artifact
- updated experiment status/results
- raw stdout/stderr for follow-up review

### `overfitting-check`

**Purpose:** inspect one completed experiment for simple overfitting signals.

**When to run:** use after `run-experiment`, `launch-experiment`, or any other completed path that
produces train/validation/test metrics.

**Primary actions:**
- read the experiment result payload
- compute validation/test gaps where possible
- persist a review artifact under `.omx/research/reviews/`

**Representative command flow:**
```bash
research-copilot workflow overfitting-check <experiment-id> --json
```

**Expected output:**
- score-gap summary
- diagnostics list
- saved review artifact

### `next-step`

**Purpose:** generate a lightweight follow-up recommendation for one experiment.

**When to run:** use after `review-results` or `overfitting-check` when the operator needs the next
safe command to run.

**Primary actions:**
- inspect the current experiment state/results
- generate simple suggestions
- persist a review artifact

**Representative command flow:**
```bash
research-copilot workflow next-step <experiment-id> --json
```

**Expected output:**
- ordered suggestions
- saved review artifact

## Ultrawork profile summary

| Profile | Use when | Lanes | Expected output |
| --- | --- | --- | --- |
| `ops-triage` | Fast diagnosis of current state | jobs/logs, experiment/context, anomalies | State summary, blockers, next action |
| `experiment-launch` | Preparing a new run | metadata/config, submission, safety checks | Registered experiment, submission artifact, verification notes |
| `run-review` | Reviewing a completed run | metrics/logs, comparisons/context, insight drafting | Result summary, recommendation, saved insight |
| `literature-context` | Gathering papers and historical context | search, relevance triage, persistence | Reading list, saved papers, context updates |
| `incident-recovery` | Diagnosing failures | log extraction, recent deltas, rollback/fix path | Root cause summary, safe recovery options, next command |

## Ultrawork profiles

### `ops-triage`

**Use when:** a researcher wants a fast diagnosis of the current lab state.

**Lanes:**
1. jobs and logs collection
2. experiment and context summary
3. issues and anomalies summary

**Expected output:**
- current-state summary
- top blockers
- suggested next action

### `experiment-launch`

**Use when:** preparing a new run with code/config plus tracking.

**Lanes:**
1. experiment metadata and config validation
2. submission script or job command preparation
3. regression and safety checks

**Expected output:**
- registered experiment
- submission artifact
- verification notes

### `run-review`

**Use when:** a completed run needs structured interpretation.

**Lanes:**
1. metrics and log analysis
2. experiment comparison and context lookup
3. insight extraction and note drafting

**Expected output:**
- result summary
- keep/drop recommendation
- saved insight or context

### `literature-context`

**Use when:** a workflow needs related papers and historical context.

**Lanes:**
1. literature search
2. relevance triage
3. paper and context persistence

**Expected output:**
- short reading list
- saved papers
- context updates

### `incident-recovery`

**Use when:** job failures or broken workflows need immediate diagnosis.

**Lanes:**
1. logs and error extraction
2. recent code or config delta review
3. rollback or fix recommendation

**Expected output:**
- root-cause summary
- safe recovery options
- next command to run

## Help-text guidance

These workflow and profile names should stay stable across CLI help, docs, and future MCP parity.

Recommended command group labels:
- `workflow` for the five named workflow commands
- `ultrawork profile` for listing named presets
- `ultrawork run <profile>` for executing a preset

Recommended help-text themes:
- describe the operator goal first
- show the expected artifact or decision produced by the command
- advertise `--json` for agent-safe automation
- point first-time users to `workflow onboard`
- keep TUI references human-facing and read-only where possible
