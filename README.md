# Research Copilot

Research Copilot is a terminal-first local research loop.

It gives you:
- a TUI for runs, experiments, research context, and live runtime state
- workflow commands for triage, experiments, review, and next-step planning
- a live Codex runtime that can attach to a tmux pane, report turns, accept steering, and continue autonomously through a supervisor worker

State lives in `.research-copilot/` inside the workspace.

## Quick Start

Prerequisites:

- Python 3.11+
- `tmux` on PATH
- `codex` on PATH if you want a live Codex brain

Install:

```bash
pipx install research-copilot
```

For local development from a checkout:

```bash
pip install -e .
```

Initialize a workspace:

```bash
research-copilot init
```

Open the TUI:

```bash
research-copilot
```

If you are operating from another shell against an already-initialized workspace, set the workspace explicitly:

```bash
research-copilot --workspace "G:\Projects\my-research" status
```

Run JSON-safe workflow commands:

```bash
research-copilot workflow triage --json
research-copilot workflow run-experiment --command "<local command>" --json
research-copilot workflow review-results <experiment-id> --json
research-copilot workflow next-step <experiment-id> --json
```

## Live Codex Runtime

Attach a Codex pane as the live research brain:

```bash
research-copilot runtime codex-attach \
  --session-id my-live-session \
  --pane-id %42 \
  --workspace-path "G:\Projects\my-research" \
  --json
```

Report a bounded turn:

```bash
research-copilot runtime codex-report \
  --session-id my-live-session \
  --turn-number 1 \
  --summary "Finished the first bounded turn." \
  --action review-results \
  --experiment-id exp-1 \
  --json
```

Start the detached supervisor so the Codex pane keeps going without manual prompting:

```bash
research-copilot runtime codex-run \
  --session-id my-live-session \
  --pane-id %42 \
  --workspace-path "G:\Projects\my-research" \
  --json
```

Inspect live runtime state:

```bash
research-copilot runtime codex-status \
  --session-id my-live-session \
  --include-nudges \
  --json
```

## Steering

Queue a steering request:

```bash
research-copilot runtime codex-nudge \
  --session-id my-live-session \
  --kind request_summary \
  --message "Need a tighter recap for the next turn." \
  --json
```

Apply queued nudges into the live tmux pane:

```bash
research-copilot runtime codex-apply-nudges \
  --session-id my-live-session \
  --json
```

Pause or resume:

```bash
research-copilot runtime codex-pause --session-id my-live-session --json
research-copilot runtime codex-resume --session-id my-live-session --json
```

Stop the supervisor:

```bash
research-copilot runtime codex-stop --session-id my-live-session --json
```

## TUI Notes

The TUI is read-only.

It shows:
- live runtime state
- freshness
- last action
- latest summary
- goal
- operator mode
- pending nudges

For overflow content:
- `j` / `k` move selection
- `Ctrl+U` / `Ctrl+D` scroll oversized detail panels and modals
- `Tab` changes panes

## Core Workflow Commands

```bash
research-copilot workflow onboard --json
research-copilot workflow triage --json
research-copilot workflow research-context "<query>" --json
research-copilot workflow run-experiment --command "<local command>" --json
research-copilot workflow review-results <experiment-id> --json
research-copilot workflow next-step <experiment-id> --json
research-copilot workflow autonomous-run --json
```

## Verify

```bash
pytest
ruff check .
research-copilot --help
research-copilot workflow --help
research-copilot runtime --help
```

## MVP Limitations

- The TUI is read-only. Mutating actions still go through CLI workflow/runtime commands.
- The live Codex runtime can continue autonomously, but it still depends on the Codex pane accepting and following the injected bounded-turn prompt.
- The supervisor currently targets tmux panes only.
- Steering is applied as text injected into the registered Codex pane; it is not a model-native control channel.
- The runtime/dashboard path is local and single-user. It is not a multi-user remote control plane.
- Very long content now pages in the main detail/modals, but this is still a terminal UI, not a full rich-scroll application.
