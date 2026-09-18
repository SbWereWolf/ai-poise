---
name: release-gate
description: Verify an explicitly assigned release candidate and its required gates with project-owned evidence, without granting publication or task acceptance.
version: 3.0.0
---

# Release gate

Use only for an **explicitly assigned release/prerelease/readiness or production-like validation scenario**. Merely installing or reading this skill does not authorize a full suite. Ordinary Tasks finish with targeted checks plus maintained fast smoke. Product release acceptance, Task lifecycle acceptance and permission to publish are separate decisions.

## Inputs

Current Poise Task/stage/scope and release authorization, exact candidate source/build identity, explicit filesystem path of the candidate checkout, target environments, actual required checks/browsers/Playwright command, deployment/recovery requirements and explicitly permitted exceptions. Resolve commands, versions, runtime, file/evidence maps and skill routing from the selected Poise-owned project using target versioned docs/settings as read-only facts. Do not add AI poise configuration to the target. Use [poise-workflow](../poise/SKILL.md) and the [existing evidence owner](../../../docs/workflows/evidence.md).

## Procedure

1. Establish the immutable candidate and required release gates before running anything. Read actual target prerequisites and versioned templates; do not create ERP release paths, environment filenames, application names, route scenarios or browser defaults. Environment preparation does not authorize access, deployment or secret disclosure.
2. Plan each required check once with its owning command, accepted environment, expected coverage and retained evidence. Run the assigned release-wide suite only in this explicit release scenario. A fast smoke pass cannot replace release acceptance. Do not repeat one identical suite under both “full” and “QA” without distinct accepted obligations; where checks overlap, record exact coverage and freshness rather than renaming/replaying evidence.
3. Run applicable unit/static/build/runtime/application/integration/browser gates against the exact candidate. Reuse the application's actual Playwright command and output owner; do not invent a parallel browser runner, new routes or a separate evidence ceremony. Where build or host assets are part of acceptance, prove what was actually loaded, not merely what exists in source.
4. Keep runner-produced evidence at its declared owned location and use its supported manifest/reference/ingestion path into Poise. Preserve exact commands, exits, stdout/stderr, source/build/runtime identity, observations and timestamps. A manifest records evidence; it is not proof the evidence exists or the command passed. Do not silently substitute stale runs, hand-copy a second truth source or reclassify parser/partial failures as success.
5. Validate migrations, recovery/rollback, backup/restore, queue health, security and observability only where required by the target's release contract. State prerequisites and destructive-effect authority. Mark an unperformed required scenario unproven; never imply a restore, rollout, browser flow or measured performance happened from a unit-test pass.
6. Treat every failed/skipped/unproven required gate as blocking unless its specific release requirement permits a recorded, authorized exception. Name the gate, reason, risk, scope/expiry and accepting owner. A broad waiver, retry or changed expected value is not an automatic exception.
7. Record the candidate-to-gate coverage and readiness decision through existing Poise Task/result/trace/evidence owners. Do not migrate ERP TRACEABILITY.md or FINAL-REPORT.md forms. Preserve independent reviewer/acceptance boundaries. Release readiness **never grants publication authority**, and user Task acceptance does not prove a release.

## Output

Exact candidate identity, required-gate coverage, fresh check and immutable evidence references, failure/skip/exception details, remaining risks and an explicit ready/not-ready/unproven assessment. Transfer through public handoff. No deployment, publication, Task acceptance, independent review or automatic routing is implied by this skill or its report.
