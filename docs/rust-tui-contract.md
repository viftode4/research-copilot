# Rust TUI Contract

This document freezes the initial backend contract for the experimental Rust
TUI scaffold.

## Contract source

The Rust TUI consumes the existing machine-readable CLI:

```bash
research-copilot --workspace <path> status --json
```

For repo-local development, the same contract can be reached through:

```bash
PYTHONPATH=src python -m research_copilot.main --workspace <path> status --json
```

## Contract boundary

The Rust TUI only depends on the JSON envelope plus `data.snapshot`.

### Required top-level fields

- `ok`
- `schema_version`
- `timestamp`
- `workspace`
- `data`

### Required `data` fields

- `config`
- `integrations`
- `workspace`
- `snapshot`

### Required `data.snapshot` fields

- `schema_version`
- `snapshot_owner`
- `snapshot_state`
- `jobs`
- `experiments`
- `insights`
- `papers`
- `context_entries`
- `experiment_status_counts`
- `runtime`

## Compatibility rules

- Additive fields are allowed.
- Missing optional fields should degrade gracefully in the Rust UI.
- Missing required fields are a contract error.
- `data.snapshot.schema_version` must be checked by the Rust bridge before rendering.

## Bridge assumptions

The current Rust scaffold supports three backend selection modes:

- `auto` — use the installed CLI only; it does **not** auto-execute workspace-local Python
- `module` — run `python -m research_copilot.main --workspace <path> status --json` and prepend `<workspace>/src` to `PYTHONPATH`
- `cli` — run `research-copilot --workspace <path> status --json`

## Trust boundary

`module` mode executes workspace-local Python code and is therefore treated as a trusted-developer path only.

- `auto` intentionally refuses to choose module mode
- `module` requires `RC_RUST_TUI_TRUST_LOCAL_CODE=1`
- if the installed CLI fails in `auto`, the bridge fails closed instead of silently falling back to module execution

Bridge fixture layouts live under `rust-tui/tests/fixtures/workspaces/`:

- `module_workspace/` includes `src/research_copilot/main.py` and exists to model an explicit trusted `module` path
- `cli_workspace/` intentionally omits that marker and reflects the default `auto` / `cli` path

These fixtures validate the bridge-selection assumptions without widening the read-only v1 scope.

## Verification fixtures

The Rust test fixtures document four contract cases:

- `rust-tui/tests/fixtures/status_minimal.json` — minimum valid envelope for read-only v1
- `rust-tui/tests/fixtures/status_additive.json` — additive/future fields that must remain harmless
- `rust-tui/tests/fixtures/status_missing_snapshot.json` — invalid example proving the contract guard fails loudly when required keys disappear
- `rust-tui/tests/fixtures/status_polished.json` — richer snapshot used to verify the polished Experiments and Research screens, including multiple experiments plus mixed insight/paper/context feed items

## Render-oriented verification

The current polished screen verification is intentionally read-only and fixture-backed:

- `rust-tui/tests/render_screens.rs` renders the Experiments screen with a queued ablation selected and verifies the queue, detail, and summary panes stay visible together
- the same test module renders the Research screen with a context entry selected and verifies the combined knowledge feed, selected-record pane, and guidance pane stay coherent
- these tests exercise the current screen titles and operator-facing copy without requiring a live backend or widening the bridge contract

Run the focused verification suite with:

```bash
cargo test --manifest-path rust-tui/Cargo.toml contract_
cargo test --manifest-path rust-tui/Cargo.toml bridge_fixture_layouts
cargo test --manifest-path rust-tui/Cargo.toml polished_fixture
cargo test --manifest-path rust-tui/Cargo.toml render_screens
```

Or run the full Rust TUI verification bundle with:

```bash
cargo fmt --manifest-path rust-tui/Cargo.toml --all
cargo test --manifest-path rust-tui/Cargo.toml
cargo check --manifest-path rust-tui/Cargo.toml
```

## Phase-one scope

- Observational/read-only only
- Python workflow/runtime commands remain authoritative
- Rust owns rendering, layout, event handling, and local UI state
