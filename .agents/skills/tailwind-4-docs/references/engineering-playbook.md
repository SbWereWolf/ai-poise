# Tailwind Engineering Playbook

Use this reference for implementation, refactor, and review tasks where
you need practical engineering judgment in addition to the official
Tailwind docs. Its purpose is to help you make good architectural
decisions quickly when you are writing, reviewing or refactoring
Tailwind code.

The v4 directives below apply only to a compatible installed version. Use the actual project browser/build policy; no installation, upgrade or deployment is implied. Component stability alone never permits custom CSS.

## Default workflow

1. Inspect the repo first.
2. Find the Tailwind entrypoint CSS and any split files.
3. Identify existing theme tokens, breakpoints, component classes,
   custom utilities, and formatting conventions.
4. Prefer using the project's existing design language over inventing a
   new one.
5. Keep the implementation as close to markup as possible.
6. Only add new abstraction when repetition or lack of a design
   primitive actually justifies it.

## Core mindset

- The default move is to compose UI in markup with utilities.
- Use Tailwind theme tokens, utilities, and variants before ordinary
  custom CSS. Custom CSS is allowed only for a confirmed gap those
  mechanisms cannot express clearly. Stable components, rich text, or
  third-party markup do not by themselves establish such a gap.

## The abstraction ladder

Use this order by default:

1. Compose with existing utilities in markup.
2. If markup repeats, extract the markup into the project's native
   reusable abstraction, such as a component, partial, include, or
   template.
3. If repeated values are missing from the system, add tokens with
   `@theme`.
4. If a repeated low-level behavior is missing, add a custom utility
   with `@utility`.
5. Only for a confirmed Tailwind gap, encapsulate repeated custom CSS
   in a small component class in `@layer components`. Stability or
   repetition alone calls for markup reuse, not custom CSS.
6. Use `@apply` only as a narrow adapter, not as the main architecture.

## What good reuse looks like

The first level of reuse is the Tailwind design system itself:

- spacing scale
- color system
- type scale
- radius scale
- shadow scale
- breakpoint scale
- container behavior

The second level of reuse is markup reuse:

- shared cards
- buttons
- hero sections
- CTA blocks
- pagination items
- list items
- nav items

The third level of reuse is CSS abstraction:

- tokens
- custom utilities
- custom variants
- small component classes

## Tokens first

Create a token when a value is part of the design language and should be
reusable:

- brand colors
- semantic surface or text colors
- typography scale
- font families
- radii
- shadows
- spacing decisions that should be global
- container widths
- breakpoints

Do not create tokens for one-off values too early.

Use an arbitrary value first when the value:

- is a real exception
- appears only once
- is unlikely to become part of the system

Promote it into a token when:

- it appears repeatedly
- it has product meaning
- design wants it governed centrally
- a later redesign should update all usages together

Prefer semantic token names where semantics matter, especially for
colors:

- `--color-primary`
- `--color-surface-muted`
- `--color-danger`

Use `@theme` when the token should generate utilities or variants. Use
`:root` only for regular CSS variables that are not supposed to create
utility classes.

## Arbitrary values

Use them for:

- one-off alignment or layout tuning
- design details that are not system-level
- third-party or generated markup constraints

Do not let repeated arbitrary values accumulate. If the same value shows
up several times, it is usually time for a token or a custom utility.

## Custom utilities

Use `@utility` when you need a low-level reusable behavior that Tailwind
does not already provide.

Good candidates:

- a project container helper
- a focus-ring preset
- a custom text wrap helper
- a low-level layout helper
- a transition preset

A custom utility should still feel like a utility:

- one job
- low level
- composable
- not semantic

## Component classes

Only after confirming a Tailwind gap, consider a component class for
a stable, intentional API. The following names illustrate ownership,
not permission to reimplement supported utilities:

- `btn`
- `card`
- `badge`
- `field-input`
- `callout`
- `rich-text`

For markup you do not control, first check available utilities, tokens
and variants. Use a component class only for a confirmed gap, including
when inspecting these surfaces:

- CMS-rendered content
- third-party widgets
- generated framework markup

Do not create component classes just to hide utilities from templates.

Good component classes are:

- small
- stable
- easy to override
- tied to real repeated primitives

Bad component classes are:

- page-specific
- giant
- bundles of unrelated concerns
- substitutes for component extraction

## Variant strategy for component classes

When a component has likely variants, keep the base class neutral where
possible.

Example of good separation:

- base class owns layout, spacing, and shared behavior
- separate variant classes own tone or intent
- size variants are separate classes

This avoids a common problem where the base component class hardcodes
colors or state behavior, then every exception has to fight the CSS.

If you create a shared component abstraction, it should own its shared
behavior consistently:

- hover transitions
- focus treatment
- icon motion
- spacing between label and icon
- disabled or active states

Do not leave half the behavior in the abstraction and the other half
duplicated ad hoc in templates.

## `@apply`

Use `@apply` only as a narrow adapter where the required behavior cannot
be expressed clearly through utilities in markup, tokens, custom
utilities, or variants. Do not hide ordinary utilities behind it;
repeated markup alone is not sufficient justification.

Good uses:

- styling third-party classes you don't control
- adapting Tailwind styles to generated markup
- tiny repeated patterns where markup extraction would be worse

Bad uses:

- hiding all utilities in CSS
- creating giant semantic wrappers that are harder to reason about than
  the original markup

If you are using `@apply` heavily to shorten templates, step back and
reconsider the abstraction ladder.

## Custom variants

Use `@custom-variant` when a selector pattern is truly repeated and
deserves to become a styling primitive.

Good candidates:

- app theme wrappers
- data-attribute driven states
- repeated container context selectors
- CMS-specific context wrappers (e.g. dark page section, highlighted
  block)

Do not create custom variants for one-off selector tricks unless the
repetition is real.

## Rich text and uncontrolled markup

Rich text is a special case because you usually do not control the inner
HTML.

First use the project's supported Tailwind utilities, variants or
Typography plugin where they express the required behavior. Only for a
confirmed remaining gap, add custom CSS in a scoped wrapper such as
`.rich-text`. Uncontrolled markup alone does not authorize custom CSS.

Do not globally style every `h1`, `p`, `ul`, or `table` in the whole app
just to fix one content region.

Scope the styling to the content container.

## Generated DOM and JS-replaced markup

When styling icons or widgets that are transformed by JavaScript, target
the rendered DOM, not only the placeholder markup.

Examples:

- icon libraries that replace `<i>` with `<svg>`
- component libraries that inject wrappers
- widgets that rewrite class names or structure

If a shared interaction depends on a specific child element, verify that
the final rendered DOM still matches the selector.

This matters a lot for:

- transitions
- hover effects
- focus styles
- icon animation
- nested selectors in component classes

## Responsive strategy

Prefer mobile-first styling:

- define the base case first
- add larger breakpoint changes progressively

Use breakpoint tokens intentionally. In v4, `--breakpoint-*` theme
variables define which responsive variants exist.

Do not blindly use every default breakpoint if the project intentionally
removed or replaced some of them.

## File organization

For a CSS-first Tailwind v4 setup, this structure is usually sensible:

- entrypoint CSS (`app.css` or `site.css`)
- `theme.css` for tokens
- `base.css` for minimal element defaults
- `utilities.css` for low-level project utilities and variants
- `components.css` for a small stable component API
    - Bigger components, e.g. `rich-text.css` for scoped uncontrolled
      HTML

## Refactor heuristics

When refactoring an existing Tailwind codebase:

1. Remove dead CSS before adding new CSS.
2. Remove redundant utilities before extracting abstractions.
3. Normalize obvious repeated primitives first.
4. Keep the number of component classes intentionally small.
5. Push page structure back into markup if CSS started owning too much
   layout.
6. Merge inconsistent implementations of the same affordance.

Watch for these smells:

- several versions of the same button or card
- repeated arbitrary shadows, radii, or spacing
- identical hover interactions implemented in different ways
- state logic split between a shared class and page-specific one-offs
- component classes that are really page fragments in disguise

## Review checklist

Before finalizing a Tailwind change, check:

- Are utilities sufficient here, or did I abstract too early?
- If repetition exists, would extracting markup be better than adding
  CSS?
- Should repeated values become tokens?
- Are custom utilities truly low-level?
- Are component classes small, stable, and override-friendly?
- Are state and motion rules consistent across similar UI?
- Will Tailwind detect every class I used?
- Are dynamic classes mapped to full strings?
- Is uncontrolled markup scoped instead of styled globally?
- Is the responsive behavior mobile-first and intentional?
- Can any CSS be deleted now?

## Practical defaults

When in doubt:

- keep styling in markup first
- reuse markup before reusing CSS
- use tokens before arbitrary repetition
- use custom utilities before semantic CSS wrappers
- keep component classes few and durable
- keep state behavior inside the shared abstraction if the abstraction
  exists
- verify that rendered DOM matches your selectors
- prefer deleting complexity over introducing a clever abstraction
