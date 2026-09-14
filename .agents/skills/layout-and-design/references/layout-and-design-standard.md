# Layout and design standard

This agent-facing reference is the skill-local source of truth. It
supports the selected skill and does not override root or nested
`AGENTS.md` files.

## Stable conventions

This document defines stable layout, semantics, and accessibility
conventions for user-visible UI in this repository.

It is a reference for layout- and accessibility-oriented work. It does
not override:

1. root `AGENTS.md`;
2. the nearest nested `AGENTS.md`;
3. explicit validation, commit, task-record, or code-change-loop rules.

Use this document together with the `layout-and-design` skill.

Resolve framework composition, state and browser behavior from the actual project and the current selected skill. This reference does not create a frontend router or require a fixed set of companion skills. The illustrative Vue markup below is not a requirement to use Vue. UI semantics and accessibility apply to the actual target surface regardless of framework.

## 1. Scope

This standard owns:

- semantic HTML choice (elements that match their role);
- page landmarks and heading hierarchy;
- form labeling and association of errors with fields;
- keyboard operability and visible, non-broken focus;
- alignment with WCAG and WAI-ARIA Authoring Practices where they apply
  to structure and interaction.

It does not own backend contracts, routing-only concerns, or generic
code style outside the UI surface.

## 2. Base rule

Choose correct HTML first. Then accessibility. Then behavior. Then
styling.

## 3. Semantics

Map roles to elements:

- `button` — triggers an action (submit, open dialog, toggle).
- `a` — navigation to another URL or in-page target with a real
  `href`.
- `form` — grouping controls for submission.
- `label` — visible name for a single control (or group per pattern).
- `table` — tabular data only, not general layout grids.
- `header`, `nav`, `main`, `aside`, `footer`, `section` — document
  structure when they reflect real regions.

### 3.1. Preferred patterns

```vue
<button type="button" @click="save">Сохранить</button>
<a href="/catalog">Каталог</a>
```

### 3.2. Anti-patterns

```vue
<div @click="save">Сохранить</div>
<a href="#" @click.prevent="save">Сохранить</a>
```

If the control performs an action and does not navigate, it must be a
`button` (or native equivalent). If it changes location, use `a` with a
meaningful `href`. Do not use `div role="button"` when a real
`button`
works (first rule of ARIA: prefer native semantics).

## 4. Landmarks and headings

- Provide exactly one visible `main` landmark per view where a document
  shell exists.
- Use `nav` with an accessible name when navigation is repeated or
  structural (`aria-label` or visible heading tied with
  `aria-labelledby`).
- Keep heading levels sequential: one logical `h1` per view, then `h2`
  for major sections, without skipping levels for styling.

### 4.1. Preferred structure

```vue
<template>
  <header>...</header>
  <nav aria-label="Основная навигация">...</nav>
  <main>
    <h1>Редактирование плана</h1>
    <section>
      <h2>Основные данные</h2>
    </section>
  </main>
</template>
```

### 4.2. Avoid

Replacing headings with unlabeled `div` text solely for visual style, or
multiple `main` regions without a clear single primary surface.

## 5. Forms

- Every control must have an **explicit** accessible name: preferably
  `label` with `for` / wrapped control, not placeholder-only text.
- Validation errors must be programmatically associated: e.g.
  `aria-describedby` pointing at the error element’s stable `id`.
- Use appropriate `autocomplete` and `type` where they help users and
  match the data.

### 5.1. Preferred pattern

```vue
<label for="plan-name">Название плана</label>
<input
  id="plan-name"
  v-model="form.name"
  name="plan_name"
  type="text"
  autocomplete="organization"
  :aria-describedby="errors.planName ? 'plan-name-error' : undefined"
/>
<p v-if="errors.planName" id="plan-name-error" role="alert">{{ errors.planName }}</p>
```

### 5.2. Avoid

Using placeholder as the only label, or error text with no link to the
field.

## 6. Keyboard and focus

- All interactive UI must be usable without a pointer: Tab order follows
  reading order; custom widgets follow APG keyboard patterns.
- Do not remove the default focus outline without a **visible** custom
  focus style that meets contrast and visibility requirements.
- Avoid positive `tabindex` values; prefer natural DOM order and
  `tabindex="0"` only when restoring focus to a composite widget per
  APG.

## 7. Normative checklist (standards alignment)

When changing interactive or structural UI, validate against:

- **W3C HTML / document conventions:** landmarks, logical headings,
  buttons vs links, forms with labels and names.
- **WHATWG HTML** semantics for chosen elements.
- **WCAG 2.x (relevant levels as required by the product target):**
    - text contrast (e.g. 1.4.3);
    - non-text contrast for UI parts and focus (e.g. 1.4.11);
    - target size for pointers (e.g. 2.5.8);
    - focus visible, not obscured, and focus appearance where applicable
      (e.g. 2.4.7, 2.4.11, 2.4.13);
    - content on hover or focus dismissible/hoverable/persistent (e.g.
      1.4.13).
- **WAI-ARIA APG:** first rule of ARIA; dialog and modal patterns when
  implementing overlays; do not duplicate native roles unnecessarily.

If a task must intentionally depart from these requirements, record the
exception in planning and review material; do not treat shortcuts as
silent.

## 8. Anti-patterns

Avoid these layout-and-design anti-patterns:

- clickable `div`/`span` for actions that should be `button`;
- `a href="#"` for actions that are not navigation;
- missing or placeholder-only labels on form fields;
- focus styles removed with no accessible replacement;
- positive `tabindex` to “fix” order instead of fixing DOM structure;
- decorative-only color or shape conveying required meaning;
- custom dialogs without focus trap, Escape, and return focus per APG;
- declaring UI work complete without checking keyboard path and focus
  visibility for the changed surface.
