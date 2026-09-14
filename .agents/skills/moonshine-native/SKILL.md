---
name: moonshine-native
description: Build MoonShine interfaces with installed-version native capabilities and documented extension points before justified custom UI.
version: 1.0.0
---

# MoonShine-native GUI

## Inputs

A project that actually uses MoonShine, its installed version, affected user scenario/observable states, real UI/architecture/style/browser boundaries and current Task scope. Resolve commands/runtime/routing through the selected Poise project; read target manifests and UI docs as facts. Use [poise-workflow](../poise/SKILL.md), not another GUI task registry.

## Procedure

1. Check installed-version resources, pages, fields, tables, actions, forms, layouts, policies, notifications and themes before custom UI. Then inspect documented extension points. Follow [UI policy](../../references/moonshine-ui-policy.md) and the [native GUI standard](../../references/moonshine-native-gui-standard.md), selecting only relevant sections.
2. Choose the smallest native or extension solution that satisfies the actual scenario. A material limitation may justify custom UI: record the unmet requirement, options checked, concrete reason and chosen boundary in current Task content. Normal native components require no catalogue/ADR/registry ceremony. Do not copy ERP-only Vue allowlists or host-page restrictions.
3. Keep reusable business decisions in domain/application owners. Let the actual project decide host navigation, permissions, presentation and custom mount boundaries; a UI component must not become the sole authorization guard. Use the installed framework's documented extension, not guessed current APIs.
4. Preserve labels, action/navigation semantics, keyboard/focus, readable states and contractual table/form behavior. Resolve styling and supported browsers from target facts and Poise-owned configuration, not a mandatory Firefox/Playwright stack. A framework-native widget does not prove its composed page is accessible.
5. Reuse existing migration and test owners. Historical migration code remains independent of production logic. Tests arrange/assert via direct SQL when ORM is not the subject; do not ban real ORM APIs when ORM behavior is itself the declared test subject. Use independent exact public contract expectations and safe serialization, not a mandatory serializer package.
6. Run checks targeted to the actual page/resource/field/action/state change plus maintained fast smoke. Add browser evidence only where the change/acceptance plan requires layout/focus/interaction observation, using the configured runtime and actual served build. Smoke alone does not prove GUI quality; no full-suite fallback or implicit deployment.

## Output

Selected native/extension capability, material divergence reason only where needed, actual UI/host/data boundaries and focused feature/browser evidence with version/build identity. Persist in existing Task/evidence/content and public handoff; native preference is not permission to rewrite unrelated existing UI.
