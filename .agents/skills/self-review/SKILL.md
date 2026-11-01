---
name: self-review
description: Inspect the current role's own result and evidence before handoff, without creating another role or replacing independent review.
metadata:
  version: "1.0.0"
---

# Self-review

The current executor or reviewer performs self-review before handoff. It does not create a third role, require a new user stop, or replace independent inspection or acceptance.

## Inputs

Load this skill at the applicable checkpoint rather than at every work start.
Include [ponytail-review](../ponytail-review/SKILL.md) for the current result or
proposed design, under the [shared review contract](../../../docs/workflows/evidence-based-review.md#осмотр-избыточной-сложности).
Keep that pass additive and within this checkpoint. Its ending does not skip
requirements, correctness or evidence checks. If there is no applicable code or
proposed complexity, state that briefly and do not invent a reduction.

During remediation the executor checks only its changed repair code and defects caused
by those changes. It may send one consolidated observation message per stage including
rework; the reviewer alone recognizes findings. Mandatory check outputs remain complete.
Follow [correction boundaries](../../../docs/workflows/evidence-based-review.md#границы-исправлений-и-наблюдений-исполнителя).

The current role's result, requirements/DoD, all Task-related diff, applicable loaded skills, source/result revision, and current evidence from the [Poise workflow](../poise/SKILL.md). Resolve commands and any project routing/settings through the selected Poise-owned configuration. Read target-worktree facts only for the active scenario; missing inputs remain explicit limitations.

## Procedure

1. Confirm the result belongs to the current stage/revision and the diff includes every Task-related change, not only the last edited file. Check stage output and early required artifacts against the next gate.
2. After test work, inspect real RED, the requirement-to-test mapping, independent expected values, meaningful negative cases, and justified tested-class allowlists. Setup/import/wrong-source errors are not RED. Show sensitivity to at least one realistic incorrect implementation; a full mutation suite is not required.
3. Count canonical JSON by Unicode characters, including line breaks and control characters: more than 99 goes to a fixture, exactly 99 may stay inline. Do not generate expectations with production algorithms or mandate a serialization trait/interface/package.
4. After code work, inspect ownership and reuse/extract/separate decisions, the complete diff, [both data-flow directions](../../references/data-flow-review.md), relevant contracts, direct side effects, failure cases, documentation and required evidence. Do not add unrelated improvements.
5. During remediation, map every correction to its original finding and criterion; confirm tests were not weakened and the targeted plus fast smoke checks describe the current revision.
6. As reviewer, inspect your own findings for a confirmed obligation, reproducible condition/consequence, source accuracy, evidence, severity, correction criterion and false-positive risk. A preference is not a defect.
7. Correct errors within the accepted stage work; during remediation keep the repair boundary and once-per-stage observation limit above. Do not recognize new findings or expand requirements as executor. Preserve failed checks and unresolved limitations rather than asserting success. Use [checkpoint guidance](../../references/self-review-gates.md) only for the active work.
8. Save a compact scope/findings/fixes/evidence/limitations result in existing Task sections and artifacts with the current `verify` packet. Follow [ordinary public release](../../../docs/workflows/local-handoff.md#передача-между-исполнителем-и-ревьюером) when the route reaches a role boundary; no separate report registry or per-item calls are needed.

## Output

One compact result covering inspected revision/scope, executor observations or reviewer-recognized findings according to the current role, fixes, checks and limitations. Poise validates mechanical structure and stores the material; substantive judgment remains with the role. Do not introduce manual self-review IDs, symbol registries, another lifecycle, or fabricate evidence for work not performed.
