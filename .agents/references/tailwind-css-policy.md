# Tailwind CSS and confirmed gaps

Apply to the target's actual Tailwind version and browser/build policy, not a mandatory Tailwind 4/Vue stack. Use supported utilities directly in templates for layout, spacing, typography, responsive/container behavior, state/focus/disabled/error variants and approved tokens. Choose semantic HTML and accessibility before styling.

Custom CSS is allowed only for a confirmed gap that supported utilities, tokens, custom utilities or variants cannot express clearly. Stable shared components or repeated markup alone do not establish a gap: extract reusable markup instead. Rich text or third-party markup still requires capability inspection. A repeated confirmed gap may have one explicit shared CSS owner and consumers.

Do not recreate available Tailwind capabilities, hide ordinary utility bundles behind `@apply`, or build concatenated class fragments that source detection cannot see. Use complete class strings. Reuse tokens and low-level utilities where they reflect the actual design system, not a competing CSS framework.

No inline style for ordinary static application styling. A genuinely runtime-computed typed CSS custom property may be supplied at its owner; it is not permission to move static rules inline. Component-specific confirmed-gap CSS belongs at the component style owner; shared rules must have explicit ownership and consumers rather than duplicate blocks.

Inspect generated DOM/styles and applicable long-content, resizing, keyboard/focus, contrast, reduced-motion and print states. Use the configured target browser. Run focused behavior/style checks plus maintained smoke. Actual build and served identity are required only by the project's chain/acceptance boundary, and do not grant deployment authority. Do not restore a second frontend router or C065 workflow.
