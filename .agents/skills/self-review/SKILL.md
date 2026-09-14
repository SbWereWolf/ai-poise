---
name: self-review
description: Inspect the current role's own result and evidence before handoff, without creating another role or replacing independent review.
version: 1.0.0
---

# Self-review

The current executor or reviewer performs self-review before handoff. It does not create a third role, require a new user stop, or replace independent inspection or acceptance.

## Inputs

The current role's result, requirements/DoD, all Task-related diff, applicable loaded skills, source/result revision, and current evidence from the [Poise workflow](../poise/SKILL.md). Resolve commands and any project routing/settings through the selected Poise-owned configuration. Read target-worktree facts only for the active scenario; missing inputs remain explicit limitations.

## Procedure

1. Confirm the result belongs to the current stage/revision and the diff includes every Task-related change, not only the last edited file. Check stage output and early required artifacts against the next gate.
2. After test work, inspect real RED, the requirement-to-test mapping, independent expected values, meaningful negative cases, and justified tested-class allowlists. Setup/import/wrong-source errors are not RED. Show sensitivity to at least one realistic incorrect implementation; a full mutation suite is not required.
3. Count canonical JSON by Unicode characters, including line breaks and control characters: more than 99 goes to a fixture, exactly 99 may stay inline. Do not generate expectations with production algorithms or mandate a serialization trait/interface/package.
4. After code work, inspect ownership and reuse/extract/separate decisions, the complete diff, [both data-flow directions](../../references/data-flow-review.md), relevant contracts, direct side effects, failure cases, documentation and required evidence. Do not add unrelated improvements.
5. During remediation, map every correction to its original finding and criterion; confirm tests were not weakened and the targeted plus fast smoke checks describe the current revision.
6. As reviewer, inspect your own findings for a confirmed obligation, reproducible condition/consequence, source accuracy, evidence, severity, correction criterion and false-positive risk. A preference is not a defect.
7. Resolve in-scope self-review defects before handoff. Otherwise preserve an explicit blocker/limitation rather than a success assertion. Use [checkpoint guidance](../../references/self-review-gates.md) only for the active work.
8. Save a compact scope/findings/fixes/evidence/limitations result in existing Task sections and artifacts with the current `verify` packet. Follow [public handoff](../../../docs/workflows/local-handoff.md#передача-между-исполнителем-и-ревьюером) when the route reaches a role boundary; no separate report registry or per-item calls are needed.

## Output

One compact result covering inspected revision/scope, confirmed findings, fixes, checks and limitations. Poise validates mechanical structure and stores the material; substantive judgment remains with the role. Do not introduce manual self-review IDs, symbol registries, another lifecycle, or fabricate evidence for work not performed.
