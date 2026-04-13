# TUI Viewport Design Principles

## Purpose

Design rules for the Research Copilot terminal UI so it remains legible, navigable, and viewport-safe across varying terminal sizes and aspect ratios.

## Root Causes Observed

1. **Invisible selection**
   - List panes used static top slices (`items[:limit]`), so selection could move below the visible window.
2. **Width-dominant responsiveness**
   - Layout decisions relied too much on width breakpoints and not enough on height, body budget, or aspect ratio.
3. **Post-hoc clipping**
   - Whole-body clipping protected bounds but did not guarantee a usable composition or visible active row.
4. **Chrome pressure**
   - Header, tabs, and footer could consume too much of the viewport under tighter terminal conditions.

## Core Principles

1. **Selection must stay visible**
   - Navigation must always keep the active row visible through deterministic list windowing.
2. **Viewport fit is structural**
   - Header, tabs, footer, and body rows are budgeted before body composition starts.
3. **Aspect ratio matters**
   - Terminal layout must respond to width, height, and vertical pressure together.
4. **Legibility beats density**
   - On constrained terminals, render fewer panes more clearly.
5. **Lists scan, details explain**
   - Lists should ellipsize early; detail panes can wrap and scroll.

## State Boundaries

### 1) Selection state
- current screen
- current pane
- selected index per list

### 2) Deterministic list windowing
- derive visible rows from:
  - total item count
  - selected index
  - visible row capacity
  - context rows
- avoid separate mutable list-scroll state for normal navigation

### 3) Viewport/layout classification
- derive from:
  - width
  - height
  - aspect ratio
  - body-row budget after chrome

### 4) Screen composition
- consumes:
  - layout classification
  - body-row budget
  - selected item
  - visible list window

## Screen Budget Contract

Each render pass computes:

- `header_rows`
- `tabs_rows`
- `footer_rows`
- `body_rows = viewport_height - (header_rows + tabs_rows + footer_rows)`

Rules:

- body rows are computed before composing panes
- body clipping is emergency-only fallback
- normal rendering must fit without hiding the active list row

## Visible Capacity Contract

Each list uses one source of truth for visible capacity:

- `visible_capacity = panel_body_rows - table_header_rows - list_hint_rows`
- clamp to at least 1 visible row
- `_windowed_range(total, selected, visible_capacity, context_rows=1)` returns the slice to render

## Navigation Invariants

- The active selection is always visible after up/down navigation.
- Moving selection updates the visible list window immediately.
- Focus detail always matches the selected visible item.
- No invisible-selection state is allowed.

## Viewport-Fit Invariants

- Rendered line count never exceeds viewport height in normal rendering.
- Rendered line width never exceeds viewport width.
- Static render obeys width and height bounds too.
- If emergency clipping happens, the active list row remains visible.

## Composition Modes

### Overview

| Mode | When | Composition |
| --- | --- | --- |
| `overview_focus` | tight/short viewport | primary list + focused detail |
| `overview_split` | standard viewport | primary list + secondary pane |
| `overview_dashboard` | roomy viewport | richer multi-pane dashboard |

### Research

| Mode | When | Composition |
| --- | --- | --- |
| `research_single_column` | tight/short viewport | active list over focused detail |
| `research_split_focus` | balanced viewport | list + detail split |
| `research_multi_panel` | roomy viewport | multiple research panes + detail |

## Implementation Order

1. Add deterministic list windowing.
2. Add explicit screen budget helpers.
3. Add per-screen layout classifiers and composition modes.
4. Compress chrome under tight viewport pressure.
5. Keep whole-body clipping as emergency fallback only.

## Skill Decision

Do **not** create a reusable skill yet.

Reason:
- this guidance is still being stabilized in one repo
- it should become a skill only after proving reusable across multiple TUI/dashboard efforts
