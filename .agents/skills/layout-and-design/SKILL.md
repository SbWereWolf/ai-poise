---
name: layout-and-design
description: Design and review the actual user scenario, information hierarchy, responsive states and accessible interactions with focused project-owned evidence.
version: 1.0.0
---

# Layout and interaction design

## Inputs

The user goal and sequence/frequency, accepted states and observable outcome, errors/recovery, keyboard use, viewport/content constraints, existing product language and actual framework/component/style/browser policy. Resolve routing/commands from the selected Poise project and read target-worktree UI facts; keep Poise configuration inside Poise. Obtain current Task/stage/scope through [poise-workflow](../poise/SKILL.md).

## Procedure

1. Apply to information architecture, workflow, visual hierarchy, responsive or interaction changes—not every routine class edit. Define semantic structure, states and component responsibilities before styling. Use the actual native framework/components and approved design system rather than ERP-only UI exceptions.
2. Start with the user scenario and its success, loading/empty, error, recovery and destructive paths. For multi-step work check orientation/current/completed/error states, back/forward freedom, preservation of input, global problem navigation, focus transitions, long content and small viewports.
3. Use the [layout/design standard](references/layout-and-design-standard.md) for semantics, landmarks/headings, labels/error associations, keyboard/focus and applicable accessibility requirements. Screenshots support but do not replace behavioral assertions or keyboard observation. State concrete accessibility risks and focus behavior when interactions change.
4. Split design, styling and behavior implementation into focused Tasks where they are distinct specialties/responsibilities. A real integration Task checks the combined scenario and its cross-boundary effects, not a label that hides unrelated work. Preserve required inputs/outputs and dependencies in the existing Task/Sprint owner.
5. Load only references/specialists needed by the current decision: Tailwind for an actual Tailwind styling/build issue, modern-web-guidance for a concrete relevant platform/layout question, framework test tools for semantic behavior, and the configured browser tool for visible interaction. For uncertainty about the responsible specialist, consult the [frontend responsibility selection](references/frontend-standard.md). No mandatory trio and no separate frontend router are introduced; C004 remains the routing owner.
6. Verify the smallest correct markup/interaction example against the actual component/runtime and project browsers. Run focused relevant tests and maintained fast smoke; full suites are release work. Build/publish only when the accepted chain needs it and the action is authorized.
7. Perform executor design/code/visual self-review and preserve actual observations; independent review still requires the distinct reviewer. Do not claim unobserved browser behavior, measured usability or acceptance from self-review alone.

## Output

Scenario/state/structure decisions, focused component responsibilities and integration obligations, exact changed markup/style/behavior evidence, accessibility risks and remaining limits in existing Poise content/evidence owners. Public handoff preserves these decisions; it does not authorize unrelated implementation or deployment.
