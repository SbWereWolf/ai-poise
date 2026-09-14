# Custom CSS after Tailwind capability checks

Use custom CSS only after confirming that the installed Tailwind version cannot express
the required behavior clearly. Custom CSS must solve a real gap; it is
not a second parallel styling system.

## Ownership and reuse

- Keep one-off component behavior in the owning component.
- Promote repeated behavior to a shared token, documented utility,
  primitive, or component instead of copying the rule.
- Use namespaced shared utilities so their ownership and collision
  boundary are explicit.
- Prefer CSS custom properties for reusable design/runtime values.
- Do not duplicate the same custom declaration block across components.

## Modern CSS choices

When the repository build and supported-browser policy allow them,
prefer:

- logical properties for direction-safe spacing and positioning;
- modern grid/flex layout rather than imperative size calculations;
- container queries when behavior depends on component space rather than
  the global viewport;
- native nesting and `@supports` for bounded progressive enhancement;
- typed runtime CSS variables for genuine dynamic values.

Avoid inline style except for typed runtime CSS custom properties whose
value must be computed at runtime. Do not place ordinary static styling
in inline attributes.

## Required verification

Verify the actual risk surface, including as applicable:

- overflow and long/unbroken content;
- container and viewport resizing;
- focus visibility and high-contrast behavior;
- reduced-motion behavior;
- print output when the requirement includes printing;
- supported-browser behavior and any required fallback;
- the actual project production build and served asset identity only when
  required by its chain and the authorized acceptance boundary.
