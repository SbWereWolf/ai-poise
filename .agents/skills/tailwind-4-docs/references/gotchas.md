# Tailwind CSS v4 gotchas (quick scan)

- Browser support is modern-only: Safari 16.4+, Chrome 111+, Firefox
  128+.
- PostCSS plugin moved to `@tailwindcss/postcss`.
- CLI moved to `@tailwindcss/cli`.
- Vite plugin `@tailwindcss/vite` is recommended.
- Import Tailwind with `@import "tailwindcss";` (no `@tailwind`
  directives).
- Prefix syntax is `@import "tailwindcss" prefix(tw);` and classes use
  `tw:` at the start.
- Important modifier goes at the end: `bg-red-500!`.
- Default border and ring color now use `currentColor`; ring width
  default is 1px.
- `space-*` and `divide-*` selectors changed; use flex/grid with `gap`
  if layouts break.
- Custom utilities should use `@utility` instead of `@layer utilities`
  or `@layer components`.
- `@theme` is for design tokens that should create utilities or
  variants; use `:root` only for plain CSS variables that should not
  generate Tailwind APIs.
- `@theme` variables must be top-level, not nested under selectors or
  media queries.
- Stacked variants apply left-to-right (reverse order from v3).
- Arbitrary CSS variable syntax is `bg-(--brand-color)` (not
  `bg-[--brand-color]`).
- Transform reset uses `scale-none`, `rotate-none`, `translate-none`
  (not `transform-none`).
- `hover:` now only applies on devices that support hover; override if
  needed.
- Tailwind scans source files as plain text, so dynamically concatenated
  class fragments are not detected.
- Use `@source` for external or unusual source locations, and
  `@source inline()` only when safelisting is truly necessary.
- For `@apply` or `@variant` in isolated CSS modules/component `<style>`
  processing, use `@reference` to the main stylesheet to expose theme/custom
  utility/variant definitions without duplicating CSS. Plain runtime
  `var(--token)` usage does not itself require `@reference`.


## Version and verification boundary

These are Tailwind v4 reference facts, not installation instructions. The source project's lock/package snapshot contains Tailwind 4.3.2; AI poise itself is not a Tailwind application. Core directive examples were checked against that archived package, not a newly installed latest release. Browser minimums remain reference requirements, not a browser test result. Select the actual target version and browser policy before application.

Primary API sources: [v4 upgrade guide](https://tailwindcss.com/docs/upgrade-guide), [directives](https://tailwindcss.com/docs/functions-and-directives), [class detection](https://tailwindcss.com/docs/detecting-classes-in-source-files). A current documentation page does not prove a different installed patch version; keep exact package/runtime evidence in the Task result.
