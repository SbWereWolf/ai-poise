---
name: vue-best-practices
description: Mandatory Vue 3 SFC, strict TypeScript, semantic accessibility, nested component, composable, store, adapter, and reactive architecture for all Vue application work.
version: 4.1.0
---

# Vue component architecture

## Triggered architecture references

- Changing entry/root/view/component boundaries, Vue application
  composition, or approved host/application ownership → [Vue target
  architecture](../../references/vue-target-architecture.md)
- Placing frontend domain/application behavior, ports/adapters, Pinia
  ownership, Router policy, or dependency direction → [frontend DDD
  reference](../../references/frontend-ddd.md)

Use Vue 3 SFC, `<script setup lang="ts">`, declarative templates, nested
and reusable components, typed props/emits/slots, component-local state,
`computed` for derived state, watchers only for side effects,
composables for reusable reactive behavior, Pinia for shared
route-spanning state, and typed adapters for
host/API/socket/storage/browser boundaries.

“All logic lives in a component” means it belongs to the Vue component
system. A route/root component composes feature components; reusable
behavior may live in child components, composables, Pinia actions, or
frontend-domain/application modules. Domain/interaction rules do not
depend on a concrete component.

Keep state predictable and avoid duplicated manually synchronized state.
Vue SFC templates are the default UI form; use another form only when
the active task records its architectural reason. When a composable
accepts plain values, refs, computed refs, or getters, apply the
adaptable-input and ownership rules in
`references/composables.md`; there is no separate composable-routing
skill. Run test and code self-review.

## Core Principles

- **Keep state predictable:** one source of truth, derive everything
  else.
- **Make data flow explicit:** Props down, Events up for most cases.
- **Favor small, focused components:** easier to test, reuse, and
  maintain.
- **Avoid unnecessary re-renders:** use computed properties and watchers
  wisely.
- **Readability counts:** write clear, self-documenting code.

## 1) Confirm architecture before coding (required)

- Default stack: Vue 3 + Composition API + `<script setup lang="ts">`.
- If the project explicitly uses Options API, load
  `vue-options-api-best-practices` skill if available.
- If the project explicitly uses JSX, load `vue-jsx-best-practices`
  skill if available.

### 1.1 Triggered core references

Open only the reference matching the current Vue design decision:

- Choosing source/derived state, watchers, effects, or expensive
  template derivation → [reactivity](references/reactivity.md)
- Structuring an SFC, template expression, list/conditional rendering,
  `v-html`, or component split → [SFC](references/sfc.md)
- Designing props, emits, `v-model`, provide/inject, or typed component
  ownership → [component data
  flow](references/component-data-flow.md)
- Extracting reusable/stateful/side-effect logic or designing adaptable
  composable inputs and writable ownership →
  [composables](references/composables.md)

### 1.2 Plan component boundaries before coding (required)

Create a brief component map before implementation for any non-trivial
feature.

- Define each component's single responsibility in one sentence.
- Keep entry/root and route-level view components as composition
  surfaces by default.
- Move feature UI and feature logic out of entry/root/view components
  unless the task is intentionally a tiny single-file demo.
- Define props/emits contracts for each child component in the map.
- Prefer a feature folder layout (`components/<feature>/...`,
  `composables/use<Feature>.ts`) when adding more than one component.

## 2) Apply essential Vue foundations (required)

These are essential foundations. Apply the rules in this core section;
open a section `1.1` reference only when its concrete trigger matches.

### Reactivity

- Keep source state minimal (`ref`/`reactive`), derive everything
  possible with `computed`.
- Use watchers for side effects if needed.
- Avoid recomputing expensive logic in templates.

### SFC structure and template safety

- Keep SFC sections in this order: `<script>` → `<template>` →
  `<style>`.
- Keep SFC responsibilities focused; split large components.
- Keep templates declarative; move branching/derivation to script.
- Apply Vue template safety rules (`v-html`, list rendering, conditional
  rendering choices).

### Semantic HTML and accessibility

Native semantics are mandatory before ARIA, styling, or custom
interaction.

- Use real `button`, `a`, `form`, `fieldset`, `legend`, `label`,
  `table`, headings, lists, dialogs, and status regions whenever their
  native contract matches the behavior.
- Give every interactive control an accessible name. Associate labels,
  descriptions, constraints, and errors with the owning control.
- Link validation errors both to their fields and to the top problem
  list when the workflow has one.
- Define keyboard and focus behavior for every action, dialog, route
  step, and error-navigation transition.
- Keep DOM order aligned with reading and interaction order.
- Keep table headers, selection controls, bulk actions,
  loading/empty/error states, and validation understandable without
  visual styling.
- Tailwind changes appearance, not semantics. Do not replace native
  behavior with styled `div`/`span` elements or ARIA when a native
  element is correct.

When Vue/JavaScript semantic tests are needed, load `vitest` and verify
with Vitest plus Vue Test Utils. When an automated browser test is
required, load
`playwright`; otherwise collect only the browser observation required by
an accepted acceptance/CJM plan.

### Keep components focused

Split a component when it has **more than one clear responsibility**
(e.g. data orchestration + UI, or multiple independent UI sections).

- Prefer **smaller components + composables** over one “mega component”
- Move **UI sections** into child components (props in, events out).
- Move **state/side effects** into composables (`useXxx()`).

Apply objective split triggers. Split the component if **any**
condition is true:

- It owns both orchestration/state and substantial presentational markup
  for multiple sections.
- It has 3+ distinct UI sections (for example: form, filters, list,
  footer/status).
- A template block is repeated or could become reusable (item rows,
  cards, list entries).

Entry/root and route view rule:

- Keep entry/root and route view components thin: app shell/layout,
  provider wiring, and feature composition.
- Do not place full feature implementations in entry/root/view
  components when those features contain independent parts.
- For CRUD/list features (todo, table, catalog, inbox), split at least
  into:
    - feature container component
    - input/form component
    - list (and/or item) component
    - footer/actions or filter/status component
- Allow a single-file implementation only for very small throwaway
  demos; if chosen, explicitly justify why splitting is unnecessary.

### Component data flow

- Use props down, events up as the primary model.
- Use `v-model` only for true two-way component contracts.
- Use provide/inject only for deep-tree dependencies or shared context.
- Keep contracts explicit and typed with `defineProps`, `defineEmits`,
  and `InjectionKey` as needed.

### Composables

- Extract logic into composables when it is reused, stateful, or
  side-effect heavy.
- Keep composable APIs small, typed, and predictable.
- For adaptable read-only inputs, distinguish values/refs/getters from
  callbacks and make writable ownership explicit; follow the dedicated
  section in `references/composables.md`.
- Separate feature logic from presentational components.

## 3) Consider optional features only when requirements call for them

### 3.1 Standard optional features

Do not add these by default. Load the matching reference only when the
requirement exists.

- Slots: parent needs to control child content/layout ->
  [component-slots](references/component-slots.md)
- Fallthrough attributes: wrapper/base components must forward
  attrs/events safely ->
  [component-fallthrough-attrs](references/component-fallthrough-attrs.md)
- Built-in component `<KeepAlive>` for stateful view caching ->
  [component-keep-alive](references/component-keep-alive.md)
- Built-in component `<Teleport>` for overlays/portals ->
  [component-teleport](references/component-teleport.md)
- Built-in component `<Suspense>` for async subtree fallback
  boundaries -> [component-suspense](references/component-suspense.md)
- Animation-related features: pick the simplest approach that matches
  the required motion behavior.
    - Built-in component `<Transition>` for enter/leave effects ->
      [transition](references/component-transition.md)
    - Built-in component `<TransitionGroup>` for animated list
      mutations ->
      [transition-group](references/component-transition-group.md)
    - Class-based animation for non-enter/leave effects ->
      [animation-class-based-technique](references/animation-class-based-technique.md)
    - State-driven animation for user-input-driven animation ->
      [animation-state-driven-technique](references/animation-state-driven-technique.md)

### 3.2 Less-common optional features

Use these only when there is explicit product or technical need.

- Directives: behavior is DOM-specific and not a good
  composable/component fit -> [directives](references/directives.md)
- Async components: heavy/rarely-used UI should be lazy loaded ->
  [component-async](references/component-async.md)
- Render functions only when templates cannot express the requirement ->
  [render-functions](references/render-functions.md)
- Plugins when behavior must be installed app-wide ->
  [plugins](references/plugins.md)
- State management patterns: app-wide shared state crosses feature
  boundaries -> [state-management](references/state-management.md)

## 4) Run performance optimization after behavior is correct

Performance work is a post-functionality pass. Do not optimize before
core behavior is implemented and verified.

- Large list rendering bottlenecks ->
  [perf-virtualize-large-lists](references/perf-virtualize-large-lists.md)
- Static subtrees re-rendering unnecessarily ->
  [perf-v-once-v-memo-directives](references/perf-v-once-v-memo-directives.md)
- Over-abstraction in hot list paths ->
  [perf-avoid-component-abstraction-in-lists](references/perf-avoid-component-abstraction-in-lists.md)
- Expensive updates triggered too often ->
  [updated-hook-performance](references/updated-hook-performance.md)

## 5) Final self-check before finishing

- Core behavior works and matches requirements.
- Every reference whose trigger matched was read; unrelated references
  were not loaded.
- Reactivity model is minimal and predictable.
- SFC structure and template rules are followed.
- Components are focused and well-factored, splitting when needed.
- Entry/root and route view components remain composition surfaces
  unless there is an explicit small-demo exception.
- Component split decisions are explicit and defensible (responsibility
  boundaries are clear).
- Data flow contracts are explicit and typed.
- Composables are used where reuse/complexity justifies them.
- Moved state/side effects into composables if applicable
- Optional features are used only when requirements demand them.
- Performance changes were applied only after functionality was
  complete.


## Inputs

The current Poise Task/stage/scope, session-selected target worktree, component responsibility map and actual Vue/toolchain/host facts. Read target documentation/configuration only as source facts. Poise-owned routing, area/skill selection, versions, commands, browser policy and file maps remain inside the selected Poise project, never in an added target Harness file. Use [poise-workflow](../poise/SKILL.md).

## Procedure

Apply the complete architecture, decomposition, accessibility and verification methodology above without relaxation. In particular, three or more UI sections require splitting, and CRUD/list features require the prescribed container/form/list-or-item/footer-or-filter components. Preserve the conditional optional-skill references: availability does not authorize forced installation. Read only triggered references. Keep the selected project's existing Task content, evidence and handoff owners; do not introduce a second registry or routing mechanism. Run the actual targeted semantic checks and maintained fast smoke, not an ordinary full-suite fallback. Record required browser/build checks that could not be performed.

## Output

Component and state ownership decisions, changed typed contracts, exact focused test/browser observations and limitations through existing Poise result/artifact/evidence APIs, followed by public handoff. The source Vue methodology and vue-target-architecture reference are retained, not generalized into a weaker policy. Skill file discovery and these workflow links do not prove the still-required automatic routing owner.
