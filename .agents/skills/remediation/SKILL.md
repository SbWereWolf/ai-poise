---
name: remediation
description: Correct confirmed findings in the current executor stage using Poise feedback, evidence, and batched resolutions.
version: 1.0.0
---

# Remediation

Use this skill when the current Poise route authorizes the executor to repair a result. It does not create a separate task lifecycle or grant a wider write scope.

## Inputs

Obtain the current Task, stage, allowed paths, required skills, original findings, pending resolutions, exact result revision, diff boundary, evidence, and result template through the [Poise workflow](../poise/SKILL.md) and [batch reads](../../../docs/workflows/batch-work.md#пакетное-чтение). Do not rely on a predecessor's conversation or reconstruct the current finding contract from an old report.

Resolve project commands, versions, runtime parameters, file maps, and any routing profile from the selected Poise-owned configuration. Target-worktree documentation is a read-only source of facts, not a place for Poise configuration. If the required routing capability or inputs are absent, record the exact dependency rather than inventing a profile or a command.

## Procedure

1. Confirm executor ownership and the writable stage returned by bootstrap. Use the existing workflow and FeedbackBook; do not open another remediation lifecycle or reset prior evidence.
2. Batch-read all findings and proposed corrections for the same revision. Map each intended change to its confirmed obligation and correction criterion. A new defect is recorded separately; it does not silently expand allowed paths.
3. Before changing executable behavior, reproduce the defect with the smallest adequate regression check. Distinguish a behavior failure from an environment, import, fixture, or wrong-worktree failure. Inspect test expectations independently of production code and respect the route's independent test-review gate.
4. Repair only the authorized code, tests, and documentation. Preserve original finding IDs, immutable evidence, and prior submissions. Trace any changed requirement through the existing Task content/trace owners.
5. Run only targeted checks and the maintained fast smoke checks for the current revision. Do not weaken assertions or widen a language-boundary allowlist merely to pass. Verify direct effects and the expected-value source; move canonical JSON longer than 99 Unicode characters to a fixture, with exactly 99 permitted inline.
6. Perform [self-review](../../references/self-review-gates.md#remediation) on every Task-related changed hunk. Check ownership, reused behavior, forward/reverse data flow, documentation, and any artifacts required before the next gate.
7. Submit all resolutions, reasons, evidence links, and required artifacts in one current `verify` packet. Use the returned `stage_work` shape; do not issue one bookkeeping call per finding. Follow any explicit continuation without inventing receipts or claim transitions.
8. Save the result, confirm public release, and follow the [direct handoff](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим). The independent reviewer inspects the corrections; saving a resolution is not its acceptance.

## Output

One persisted executor result covering finding → correction → changed paths → targeted evidence, a compact self-review, and unresolved dependencies. Poise stores and transfers this material through its existing Task, FeedbackBook, artifact, and evidence owners. Mechanical validation checks structure, required fields/links, scope, and applicable executable checks; the stage agent and reviewer remain responsible for substantive judgment.

No manual self-review registry, old ERP finding prefix, synthetic skipped-role result, mandatory review commit, or extra role is introduced.
