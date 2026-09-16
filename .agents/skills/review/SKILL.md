---
name: review
description: Independently review a specific result revision against confirmed obligations and store reproducible findings through Poise.
version: 1.0.0
---

# Independent review

Use this skill only as the reviewer on the existing route. [Evidence-based review](../../../docs/workflows/evidence-based-review.md#что-считать-находкой) owns finding criteria; this skill applies them without adding a lifecycle.

## Inputs

Acquire the Task through the [Poise workflow](../poise/SKILL.md). Batch-read the requirements/DoD, result revision, exact diff boundary, current evidence, relevant content/trace, applicable rules and required skills. Resolve project routing, commands, browser policy, versions, runtime parameters and file maps from Poise-owned configuration. Target-worktree configuration/docs may establish facts but do not store Poise settings.

If the runtime does not supply required routing or stage inputs, name the missing capability explicitly. Do not silently load the whole catalogue, invent another session, or derive a command from an ERP example.

## Stage-specific review skills

Inspect the result with both the assigned review-method skills and the concrete
subject skills for this inspection stage. A general review skill does not replace
test-framework or domain expertise. The planner owns additions/removals; do not
silently weaken the assignment. The [two-set stage proposal](../../../docs/workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
is a draft for later Task/template tooling, not an existing runtime guarantee or a
new role. Report missing or insufficient specialist skills in the final answer,
including the stage, affected conclusions and requested planner action. State review
limits rather than inferring correctness from a skill name or acknowledgement.

## Procedure

1. Confirm the inspected revision and reviewer ownership. Keep the inspection read-only. Mechanical receipts, task completeness and the executor's self-review are evidence inputs, not proof of correctness.
2. Compare the actual scope and changed paths with requirements and applicable area/skill selection. Batch known reads; expand investigation through real callers, consumers, contracts, persistence, and failure paths without expanding write scope.
3. Trace important behavior [forward and backward](../../references/data-flow-review.md). Identify the owner of every invariant, normalization, authorization decision, transaction and side effect; inspect reuse/extraction decisions and in-scope duplicates.
4. Inspect tests at the real boundary, declared tested-class allowlists, independent expected values, negative cases, and appropriate ordering. Check real RED where required. Canonical JSON longer than 99 Unicode characters, including newlines/control characters, belongs in a file; exactly 99 may remain inline. No serializer trait, interface, or runtime package is mandatory merely for comparison.
5. Inspect evidence freshness, fixture provenance, source/build identity when relevant, documentation and early required artifacts. Load only the applicable [risk reference](../../references/review-risk-checklist.md) and [finding procedure](references/code-review.md).
6. Reproduce material claims with exact project-configured targeted checks and maintained fast smoke checks. Do not substitute a full suite for focused proof. Use only the configured browsers; no blanket browser or deployment obligation is implied.
7. For each genuine finding, name the confirmed requirement, triggering condition, consequence, reproducible evidence, responsible producer stage, later gate, and verifiable correction criterion. Do not turn personal taste, speculation, or optional improvement into a blocker.
8. Challenge your own findings using [self-review](../../references/self-review-gates.md#reviewer-results). Distinguish runtime/contract defects, executor defects, reviewer mistakes, protective rejection, and unresolved causes from evidence.
9. Submit one current `verify` packet with coverage, findings and applicable resolution/evidence decisions. A bounded no-finding result names the inspected scope and limits; it creates no synthetic findings or skipped-role evidence.

## Output

A durable review result for the executor: exact revision and scope, batched findings or bounded no-finding conclusion, supporting checks and limitations. Existing Poise FeedbackBook, evidence and handoff own storage and transfer. Saving is not acceptance; independent judgment cannot be replaced by a structural validator. Release and hand off through the [canonical protocol](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим); neither the review nor this skill grants publication authority.
