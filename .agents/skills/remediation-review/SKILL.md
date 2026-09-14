---
name: remediation-review
description: Independently inspect every proposed correction against original findings, the exact diff, and current evidence without editing product code.
version: 1.0.0
---

# Remediation inspection

This is reviewer work in the existing Poise route, not another lifecycle. The executor's self-review is input, not independent acceptance.

## Inputs

Use the [Poise workflow](../poise/SKILL.md) to acquire the released Task. Batch-read the original findings, proposed resolutions, requirements, exact pre-fix/current revision and diff, current test/evidence records, relevant skills, and result template. Ensure that evidence describes the revision being inspected. Resolve commands, browser/runtime settings, versions and file maps from the selected Poise-owned configuration; read target-worktree facts only as needed.

## Procedure

1. Confirm reviewer ownership and a read-only inspection stage. Do not modify product code, fix the executor's implementation, or require a new review commit.
2. For every original finding, compare its obligation and correction criterion with the proposed resolution, actual diff, and reproducible evidence. Include pending resolutions rather than inspecting only the latest narrative summary.
3. Inspect tests and their language-boundary allowlists. Establish independent expected-value provenance, meaningful negative cases, required ordering, and sensitivity to the reported defect. Reject weakened tests, broadened allowlists that hide dependencies, or regenerated oracles that merely follow production output.
4. Inspect all Task-related changes for scope expansion, owner/contract drift, documentation gaps, stale artifacts, and missing direct effects. Use the [data-flow method](../../references/data-flow-review.md) and [risk checklist](../../references/review-risk-checklist.md) only for relevant boundaries.
5. Reproduce material claims with project-configured targeted checks plus fast smoke checks. A smoke pass alone is not proof that a finding is corrected. Do not run a full regression suite or assume a browser/deployment environment exists.
6. Produce a decision for every pending resolution, with a concrete reason and evidence for this revision. Reuse original finding IDs; submit new genuine defects separately in the same batch using the current API shape.
7. [Self-review the review](../../references/self-review-gates.md#reviewer-results) for wrong sources, unproven claims, taste-only blockers, and false positives. Do not invent findings to fill a quota.
8. Submit one `verify` result containing all `stage_work.resolution_decisions`, any `stage_work.findings`, and evidence decisions required by the returned template. Poise stores the decisions; mechanical validity does not substitute for your judgment.

## Output

A persisted batch of per-resolution decisions and reasons/evidence, new findings where substantiated, inspected scope, checks actually run, and limitations. Use the [existing handoff](../../../docs/workflows/local-handoff.md#передача-между-исполнителем-и-ревьюером) for remaining executor work. A clear inspection is not independent authority to publish or integrate.

There are exactly two roles. Do not copy ERP task-control commands, finding prefixes, no-op ceremonies, manual self-review IDs, or per-item bookkeeping. Missing routing/input is an explicit dependency, never permission to create configuration in the target repository.
