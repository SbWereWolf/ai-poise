# Vue target architecture

## Target

All new and substantially changed interactive browser application code
belongs to the Vue component system and uses:

- Vue 3 SFC;
- Composition API;
- `<script setup lang="ts">`;
- strict TypeScript;
- declarative `<template>` markup;
- Tailwind CSS 4 at build time;
- Vitest and Vue Test Utils;
- Vue Router for route-level pages;
- Pinia for state shared across route-level forms/components.

The application is a component tree. A component may contain other
components, slots, composables, stores, and typed adapters. “Logic lives
in a component” means logic belongs to the Vue application/component
boundary, not that it must be placed in one
`.vue` file.

## Layers

```text
Route/root components
        -> feature components
        -> composables / Pinia application state
        -> frontend application services
        -> frontend domain / interaction domain
        -> typed ports
        <- infrastructure adapters
```

- Presentation/components own markup, local interaction state, props,
  emits, slots, focus, and accessibility.
- Composables own reusable reactive behavior and lifecycle-aware side
  effects.
- Pinia owns explicitly shared state and actions; component-local state
  remains local.
- Frontend domain owns deterministic client-side product and interaction
  rules without Vue, Pinia, Router, DOM, or transport imports.
- Typed adapters own HTTP, host callbacks, WebSocket/EventSource,
  storage, and browser APIs.

## Architecture decisions

Vue SFC templates are the default for the target application. A task may
select standalone JavaScript, render functions, JSX, HTML strings, or
direct DOM interaction when it records the reason, the ownership
boundary and the observable test coverage. Keep derived state computed,
keep server-owned business invariants outside the browser, and introduce
browser storage only through an explicit lifecycle requirement.
