---
name: ponytail-review
description: Review a diff specifically for unnecessary complexity, speculative abstractions, unused flexibility or reinvention of native capabilities; report concrete simplifications without applying them.
---

# Review for unnecessary complexity

Use for a requested simplification or over-engineering review. Follow the
[review boundary](../../../docs/workflows/evidence-based-review.md#осмотр-избыточной-сложности).
This complements correctness review and does not authorize changing code.

Inspect the actual diff and its callers, accepted requirements and responsibility
boundaries. Give one concise finding per justified simplification:
`file:line: tag: what to remove; what replaces it and why behavior is preserved`.

- `delete`: demonstrably unused code or speculative flexibility; replacement may be none.
- `stdlib`: an existing standard-library function preserves the required contract.
- `native`: the selected platform already supplies the required capability.
- `yagni`: an abstraction adds no current responsibility or accepted requirement.
- `shrink`: a shorter expression preserves observable behavior and readability.

A single implementation or caller alone does not make an ownership boundary,
configured value or accepted recovery behavior unnecessary. Do not remove required
validation, checks or evidence to reduce line count. Avoid examples that trade away
the accepted contract for a shorter expression.

Name a concrete replacement rather than a preference for less code. Estimate line
savings only from an inspected change and label estimates. If there are no supported
findings, state that directly; do not infer release readiness or Task acceptance.
Route separately discovered correctness/security defects to the ordinary
[review](../review/SKILL.md) with their real evidence.
