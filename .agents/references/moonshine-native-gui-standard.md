# MoonShine native GUI standard

## Capability order and ownership

For the installed version, inspect native layout/menu/resources/pages/fields/tables/filters/actions/forms/modals/notifications/themes first, documented extension points second, then custom UI only with a material requirement-based reason. Save that decision in existing Task content, not a new component registry. Normal native composition needs no exception record.

Resolve code locations, UI/host boundaries, supported browsers and styling from actual target documentation and Poise project configuration. Do not copy ERP application paths or editor/viewer exceptions. Keep reusable domain/application rules separate from page/resource presentation; UI visibility is not authorization.

## Composition and interactions

Use the active application shell and native navigation/layout/notification mechanisms where they satisfy the scenario. Keep one obvious page identity, logical headings and a readable hierarchy. Distinguish primary, secondary, destructive and bulk actions. Show empty/error/success/warning/loading states near their affected content. Avoid decorative/nested containers without a real structural purpose.

Tables contain data in data columns and actions in native action areas. Use the project's native row/detail navigation pattern where required. Bulk actions have their own action area, not text cells. Preserve the actual confirmation/validation/notification/audit contract for destructive actions; do not invent a universal audit component.

Use installed-version native fields/forms/buttons/modals before custom markup. Every control has an accessible label; buttons perform actions, links navigate. Keep errors adjacent to relevant fields/sections. Placeholder text alone is not a label. Check native extension contracts before hand-written HTML or custom render boundaries; document only material divergence.

## Styling, host boundaries and accessibility

Use actual framework/theme/palette/extension facilities before a custom styling system. Select themes, icons and tokens from the target project; a foreign ERP palette or browser is not a requirement. Maintain readable contrast, logical headings, keyboard operation, visible focus and text alongside meaningful color states.

For embedded custom UI, preserve the documented host/child responsibilities and required mount dimensions. No generic rule requires or forbids a wrapper merely because an ERP editor had that restriction. Verify the actual mount/host behavior where changed. Framework-native widgets do not establish accessibility of the composed page by themselves.

## Verification and policy reuse

Select focused page/resource/feature tests for the changed contract. Browser evidence is required where actual acceptance needs layout, responsive, focus or interaction observation; use the configured browser/runtime and actual served build. Targeted static/markup checks support but do not replace visible behavior. Maintained fast smoke complements focused checks; full suites belong only to release preparation.

Reuse the strict migration owner for historical database isolation. Reuse the test owner for independent expectations and direct SQL when ORM is not the subject; ORM tests may invoke real ORM behavior when that is the declared boundary. Do not restate a competing migration engine, test lifecycle or mandatory serializer rule here. Preserve exact requirements/checks and missing evidence through current Poise Task/content/evidence owners.
