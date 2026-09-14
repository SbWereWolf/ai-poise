---
title: One URL Owner for Shareable Filters
impact: MEDIUM
impactDescription: Shareable view state belongs to the router URL, not mirrored mutable component or Pinia copies.
type: best-practice
tags: [vue3, pinia, state-management, url, router, filters]
---

# One URL owner for shareable filters

Choose state ownership from the target's accepted model and lifecycle. URL-visible non-sensitive filters/search/sort/page may need deep links, reload continuity and back/forward history. Private form drafts, credentials and non-shareable workflow state do not belong in the URL. A modal/tab is shareable only if its product contract says so. Do not infer storage persistence from a generic cart example.

## Required pattern

The URL is the **only mutable owner** of these fields. Derive typed values using `computed`; explicit actions navigate through the existing Router. Do not copy query fields into `ref`/Pinia state, initialize a second state copy from the URL, or synchronize URL/store copies with watchers. Other domain data may remain Pinia-owned; that is composition of different owners, not a synchronized hybrid filter store.

The complete [typed example](../examples/url-filter-state.ts) has one Router input, computed projections and explicit async navigation results. It normalizes missing/null/array query values and invalid page numbers, preserves unrelated query parameters and the hash, omits defaults, resets pagination when filters change, and selects push/replace from the target history policy. Callers must await navigation, inspect navigation failures and preserve real errors; a rejected/aborted navigation is not a successful filter update. Serialize or intentionally replace rapid navigation according to the target interaction contract; do not add a second local mirror to hide races.

Bind rendered values to those projections. UI events invoke the explicit actions and handle their returned result. An unsubmitted search draft is a separate state only when an explicit submit/cancel contract requires it; it is not an automatically synchronized copy. Do not install VueUse just to read this skill. A compatible, already-approved URL composable can be used only if it preserves the same single-owner and error/history semantics; converting `"false"` with `Boolean` is not valid boolean query parsing.

## Focused verification

Verify exact normalized defaults, valid/invalid/duplicate parameters, preservation of unrelated URL fields, filter/page actions, back/forward and direct-entry continuity, no mirrored state/watchers, and honest aborted/rejected navigation. A Node memory-history test does not establish actual browser reload, host rewrite or SSR behavior. Test those only in the target's real accepted runtime and evidence plan.

## Sources

[Vue Router programmatic navigation](https://router.vuejs.org/guide/essentials/navigation.html), [query types](https://router.vuejs.org/api/interfaces/RouteLocationNormalizedLoadedGeneric.html#query). These APIs do not authorize weakening the Task's single-owner policy or changing target routes/history/persistence.
