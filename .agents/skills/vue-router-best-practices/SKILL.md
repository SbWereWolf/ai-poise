---
name: vue-router-best-practices
description: Vue Router navigation, target-owned history and route contracts, problem/focus targets, strict return-based guards and focused route tests.
version: 4.0.0
---

# Vue Router

Load this skill only when the work changes or diagnoses page/route
transitions, route configuration, navigation guards, history,
direct-entry URLs, or route-owned focus/problem navigation. Do not load
it for ordinary component work that remains on one page.

Derive the named routes, route-level responsibilities, host integration,
history mode and transition/lifecycle contract from the target project's
accepted documentation and actual configuration. Do not install a fixed
editor model, route set or hash-history default.

Navigation between all steps is always available; validation does not
trap the user. Pinia preserves unsaved in-memory state across internal
navigation. Define route order/meta, current/completed/error indicators,
direct-entry, back/forward, refresh, dirty-leave, and scroll behavior
explicitly.

Every problem has a stable route, owning component, and element target.
Activating a problem pushes the route, awaits mount/
`nextTick`, resolves the target, scrolls/focuses it, and applies visible
highlighting. Missing targets are explicit contract defects, not silent
fallbacks.

Test real router history, route components, problem navigation, cleanup,
and focus. Run test and code self-review.

## Focused failure-mode references

Open only the reference matching the changed route or observed symptom:

- [Production router navigation](reference/router-use-vue-router-for-production.md)
- [Simple-routing cleanup](reference/router-simple-routing-cleanup.md)
- [Parameter changes without remount](reference/router-param-change-no-lifecycle.md)
- [
  `beforeEnter` and param-only changes](reference/router-beforeenter-no-param-trigger.md)
- [`beforeRouteEnter` without
  `this`](reference/router-beforerouteenter-no-this.md)
- [Single async/await completion path](reference/router-guard-async-await-pattern.md)
- [Navigation-guard redirect loops](reference/router-navigation-guard-infinite-loop.md)
- [Deprecated mixed `next`
  control](reference/router-navigation-guard-next-deprecated.md)


## Inputs

Current Poise Task/stage/scope, target routes/history/host/transition requirements and actual Vue Router/runtime versions. Poise owns routing, skill selection, commands, browser/runtime policy and file maps inside its selected project configuration; target docs/configuration are read-only facts. Use [poise-workflow](../poise/SKILL.md).

## Procedure

Keep the navigation/problem/focus and testing rules above. **Eliminate `next()` from navigation guards; use a single return-based completion path.** Official support for `next()` and preserved legacy examples do not override this Task rule. The withdrawn proposal to rewrite guard ordering is not applied. Read only the matching failure-mode reference and the [recorded discrepancies](reference/poise-migration-discrepancies.md) when it contains a disputed example. Do not use a discrepancy as permission to change the settled methodology or to claim the legacy example was validated. Run targeted real-router/component checks plus maintained smoke; browser/host/build identity checks follow the actual accepted target contract.

## Output

Route/state/focus ownership, accepted history/host facts, exact focused navigation results and unresolved discrepancies through existing Poise Task/evidence/handoff APIs. No target AI poise configuration, alternate router, independent review or automatic skill routing is implied by file discovery.
