---
name: vue-pinia-best-practices
description: Pinia state architecture for route-spanning draft forms, typed actions/getters, frontend DDD, navigation continuity, and exact tests.
version: 4.0.0
---

# Pinia

Load this skill only when the work changes or diagnoses a Pinia store,
store action/getter, persistence policy, or state shared across
components/routes. Do not load it for component-local state.

Select shared models, routes, refresh loading, persistence and lifecycle from
the target project’s accepted documentation. Use Pinia for state whose owner
is explicitly shared across components/routes; do not copy local or URL-owned
state into a store. There is no prescribed editor or fixed number of steps.

- define typed setup stores by bounded context/responsibility;
- keep one source of truth for draft data and route-spanning state;
- expose state transitions as actions and derived values as
  computed/getters;
- keep framework-independent domain/interaction rules outside Pinia;
- use `storeToRefs` when destructuring reactive state;
- do not duplicate store state in components or synchronize copies with
  watchers;
- inject typed host/API/socket ports;
- do not persist to browser storage without an explicit
  lifecycle/version/privacy requirement;
- preserve the target contract’s state continuity on internal back/forward
  navigation; derive authoritative refresh loading, reset and persistence
  behavior from that target contract instead of an ERP lifecycle default.

Test actions/getters directly and route components with real actions
when integration is the subject. Run test and code self-review.

### Store Setup

- Getting "getActivePinia was called" error at startup → See
  [pinia-no-active-pinia-error](reference/pinia-no-active-pinia-error.md)
- Setup stores missing state in DevTools or SSR → See
  [pinia-setup-store-return-all-state](reference/pinia-setup-store-return-all-state.md)

### Reactivity

- Store destructuring stops updating UI reactively → See
  [pinia-store-destructuring-breaks-reactivity](reference/pinia-store-destructuring-breaks-reactivity.md)
- Store methods lose context in template calls → See
  [store-method-binding-parentheses](reference/store-method-binding-parentheses.md)

### State Patterns

- Filters reset on refresh or can't be shared → See
  [state-url-for-ephemeral-filters](reference/state-url-for-ephemeral-filters.md)
- Building production app without DevTools or conventions → See
  [state-use-pinia-for-large-apps](reference/state-use-pinia-for-large-apps.md)


## Inputs

Current Poise Task/stage, permitted state boundary, target model/routes and accepted lifecycle/refresh/persistence/privacy requirements. Resolve skill routing, commands, versions and runtime parameters from the selected Poise-owned project; read target-worktree facts without writing Harness configuration there. Use [poise-workflow](../poise/SKILL.md).

## Procedure

Apply the strict one-owner and no-copied-state/no-watcher-synchronization rules above. URL-owned filters remain router state with computed views and explicit navigation actions; Pinia may own different private/shared application data, not synchronized copies of those filters. The method-binding restriction for hand-written reactive objects does not apply to Pinia actions, which remain callable/destructurable. Read only a relevant reference. Persistence plugins, package installation and Vuex migration examples require a separately authorized need; they never authorize unrelated migrations. Test real actions/getters and the affected navigation/lifecycle behavior, plus maintained fast smoke. Version and source facts must be explicit; do not claim target browser/SSR behavior from Node-only checks.

## Output

State-owner map, route-versus-store boundary, typed actions/getters, exact focused checks and unresolved lifecycle/runtime assumptions in existing Poise Task content/evidence. Use the current public handoff; independent review and automatic routing are not self-certified.
