# External Research Notes — TUI Viewport and Navigation

This note captures external research used to ground the Research Copilot TUI redesign.

## Executive synthesis

Across multiple mature TUI frameworks, the same patterns repeat:

1. **Keep viewport state explicit**
   - visible area, selection, and offset/window are treated as first-class state
2. **Separate sticky chrome from scrolling content**
   - headers/footers/sidebar regions should not participate in content scrolling
3. **Use real scrolling/windowing containers, not top-slicing**
   - lists and long content should track the active selection or viewport offset
4. **Truncate in dense list views; wrap/scroll in detail views**
   - scan surfaces and reading surfaces need different overflow behavior
5. **Compose from an explicit space budget**
   - terminal UI layout works best when every region gets a rectangle/row budget before render

## Source-backed findings

### 1) Textual: sticky chrome and explicit scroll containers

Textual’s layout guide recommends working “outside in,” docking fixed widgets, and using containers for scrolling content. It explicitly notes that docked widgets stay fixed and do not scroll out of view, making them a good model for sticky headers, footers, and sidebars. It also recommends containers such as `HorizontalScroll` and `VerticalScroll` when content needs scrolling behavior instead of relying on ad hoc layout overflow.

Relevant sources:
- Textual layout guide: <https://textual.textualize.io/guide/layout/>
- Textual layout how-to: <https://textual.textualize.io/how-to/design-a-layout/>
- Textual dock docs: <https://textual.textualize.io/styles/dock/>
- Textual overflow docs: <https://textual.textualize.io/styles/overflow/>

### 2) Textual: chrome should compress under pressure

Textual’s `Footer` widget supports compact mode and hiding parts of footer behavior, which is a concrete precedent for shrinking UI chrome when space is limited instead of sacrificing body content first.

Relevant source:
- Textual footer docs: <https://textual.textualize.io/widgets/footer/>

### 3) Ratatui: selection and visible offset belong together

Ratatui’s `ListState` is especially relevant. Its docs explicitly say that when a list is rendered as a stateful widget, the selected item is highlighted **and the list is shifted to ensure that the selected item is visible**. Ratatui also documents `StatefulWidget` as the pattern for widgets that need state like selection and scroll position, keeping widget configuration separate from persistent application state.

This is the clearest external confirmation that our current `items[:limit]` approach is structurally wrong for navigable terminal lists.

Relevant sources:
- Ratatui `ListState`: <https://docs.rs/ratatui/latest/ratatui/widgets/struct.ListState.html>
- Ratatui widgets / `StatefulWidget`: <https://docs.rs/ratatui/latest/ratatui/widgets/>
- Ratatui list example: <https://ratatui.rs/examples/widgets/list/>

### 4) Ratatui: explicit rectangular layout contracts

Ratatui’s layout system is based on splitting the terminal into rectangles via explicit constraints (`Length`, `Min`, `Max`, `Ratio`, `Percentage`, `Fill`). This reinforces the need for our own explicit screen budget contract instead of composing a big body and only clipping afterward.

Relevant source:
- Ratatui layout concepts: <https://ratatui.rs/concepts/layout/>

### 5) Bubble Tea / Bubbles: viewport and list are separate, reusable primitives

Bubble Tea’s `viewport` component exposes width, height, and vertical offset directly. The Bubbles component library also treats `viewport`, `list`, `table`, `help`, and `paginator` as distinct UI primitives, with the list component handling browsing and the help component truncating gracefully when the terminal is too narrow.

This supports a design where:
- list navigation and windowing are handled explicitly
- help/footer chrome can degrade gracefully
- scrolling should be a dedicated primitive, not an emergent side effect

Relevant sources:
- Bubbles viewport package: <https://pkg.go.dev/github.com/charmbracelet/bubbles/viewport>
- Bubbles component overview: <https://github.com/charmbracelet/bubbles>

### 6) Rich: dense tables should ellipsize instead of wrap

Rich tables support per-column overflow policies such as `ellipsis`, along with width, min/max width, ratio, and `no_wrap`. This is strong evidence that list-like tables should clamp aggressively for readability and width safety, while more verbose detail content can use wrapping and/or scrolling elsewhere.

Relevant source:
- Rich table docs: <https://rich.readthedocs.io/en/latest/reference/table.html>

## Cross-framework convergence

The external sources converge on these practical rules:

### Rule A — Sticky UI chrome should be structurally separated
- Keep header/tabs/footer outside the scrollable body
- Treat them as reserved space, not content siblings that happen to render first

### Rule B — Lists need selection-follow behavior
- Selection without visible-window tracking is broken navigation
- The list’s visible window must respond immediately when selection moves beyond the current slice

### Rule C — Tables are not detail views
- In lists/tables:
  - truncate
  - ellipsize
  - stabilize columns
- In details:
  - wrap
  - scroll
  - expose richer content

### Rule D — Viewport math should happen before composition
- Determine how many rows the body actually has
- Then determine which composition mode fits
- Then determine how many list rows can be shown

### Rule E — Layout should respond to terminal shape, not just width
- A short wide terminal and a tall wide terminal are materially different
- Aspect ratio and body-row pressure should influence composition mode

## Implications for Research Copilot

These sources strongly support the repo design decisions already captured in `docs/tui-viewport-design.md`:

1. **Add deterministic list windowing**
   - replace static top slices
2. **Add an explicit screen budget contract**
   - header/tabs/footer/body rows
3. **Add concrete per-screen composition modes**
   - especially for `overview` and `research`
4. **Compress chrome on short terminals**
   - show less chrome before harming list readability
5. **Keep body clipping as emergency fallback only**
   - not the main layout strategy

## Reuse decision

The research supports a reusable pattern, but not a reusable skill yet.

Reason:
- the pattern is now well grounded conceptually
- but it still needs one successful implementation cycle in this repo before abstraction is worth the overhead

For now, the right reusable artifact is:
- repo design note(s)
- PRD/test spec
- implementation helpers once stabilized
