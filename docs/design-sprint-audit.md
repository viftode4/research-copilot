# Design Sprint Audit

## Summary diagnosis

Research Copilot currently behaves like three overlapping products:

1. bootstrap / research CLI
2. read-only dashboard
3. live runtime supervisor / steering plane

The core UX problem is that users must mentally stitch these together instead of following one obvious copilot journey.

## P0 problems

1. **No single operator journey**
   - users cannot immediately tell where to start, what to do next, or which surface is primary
2. **TUI usability risk**
   - selection-follow, viewport handling, dense pane composition, and runtime readability are not demo-safe
3. **Autonomy/control-plane ambiguity**
   - `workflow autonomous-*` and `runtime codex-*` feel like competing products

## P1 problems

4. **Command ambiguity**
   - `launch-experiment` vs `run-experiment`
   - `status` vs `triage`
5. **Cross-surface wording inconsistency**
   - docs/help/status/TUI do not yet converge on one recommendation model
6. **Result-review fragmentation**
   - monitoring, review, overfitting checks, next-step planning, and context persistence feel scattered

## P2 problems

7. **Migration/workspace mental load**
8. **Onboarding too heavy before first value**

## Audit conclusion

The design sprint should be **contract-first**:

- define the product/control-plane hierarchy first
- then fix TUI usability and cross-surface guidance
- then refine runtime clarity
- then rehearse the AI Congress demo path
