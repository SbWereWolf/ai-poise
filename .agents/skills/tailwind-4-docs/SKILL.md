---
name: tailwind-4-docs
description: Use the selected project's actual Tailwind version and build chain, with utility-first styling and custom CSS only for confirmed capability gaps.
version: 1.0.0
---

# Tailwind styling and build boundaries

## Inputs

The intended style/behavior, actual installed Tailwind version, CSS entry point, source detection, tokens/components, real build chain, current Task scope and project-owned browser/runtime commands. Resolve Poise routing/configuration inside Poise; target manifests and styles are read-only source facts for that profile. Use [poise-workflow](../poise/SKILL.md). The skill name preserves catalogue identity, not authority to install or upgrade Tailwind.

## Procedure

1. Apply only when the project actually uses Tailwind. Inspect its installed-version APIs, existing tokens, utilities, variants, reusable components and formatting conventions. Do not mandate Tailwind 4, Vue, Vite or dependency changes for an unrelated stack.
2. Choose semantic structure and accessibility before styling. Use supported utilities directly in templates, then existing tokens/custom utilities/variants as appropriate. Keep class tokens statically discoverable; dynamic choices map to complete strings. Inspect the generated DOM/CSS, not just source placeholders.
3. Custom CSS is permitted **only after confirmed Tailwind insufficiency**. Stable components, repeated markup, rich text or third-party markup alone are not exceptions. Extract reusable markup for repeated supported utilities; use a shared CSS primitive only for a repeated confirmed gap. Follow [CSS policy](../../references/tailwind-css-policy.md) and [custom-gap detail](references/custom-css.md).
4. Read only the matching [v4 gotcha](references/gotchas.md) or [engineering playbook](references/engineering-playbook.md) section. The former missing reference entry remains absent. Use v4-specific examples only with a compatible installed version; validate changed API claims through that package and its primary documentation. Do not restore C065 or a mandatory frontend skill trio.
5. Check real rendered states: relevant responsive/container sizes, overflow/long text, keyboard/focus, disabled/error/loading states, contrast and reduced motion. Use the target project's browser policy through Poise-owned configuration, not an ERP default browser.
6. Run targeted component/style/build checks plus maintained fast smoke. Vite build or host asset publication is required only when that project's actual chain and accepted requirement need it. A deployment still requires separate authority. For served-asset defects, prove the host loaded the exact intended bundle; source-only success is insufficient for that boundary.

## Output

Changed styles/components and ownership, actual version/build/browser inputs, confirmed gap reason if custom CSS is used, class-discovery/rendered-state evidence and focused check results in existing Poise Task/evidence owners. Report target-browser/build checks not run. Public handoff and skill discovery are not automatic routing or publication authority.
