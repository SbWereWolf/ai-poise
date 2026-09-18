---
name: debugging-and-recovery
description: Diagnose terminal failures from saved evidence and recover authorized work without blind retries or competing observers.
version: 1.0.0
---

# Evidence-driven recovery

## Inputs

The failed operation and its stable identity, terminal result or confirmed loss of its owner, saved complete logs/artifacts, current Task/stage and worktree, exact source/runtime identity, required outcome and selected Poise project configuration. Obtain these through [poise-workflow](../poise/SKILL.md) and the [existing batch reads](../../../docs/workflows/batch-work.md#пакетное-чтение), not reconstructed lifecycle state.

## Procedure

1. Let the owning runner be the sole continuous observer while a registered operation is active. Do not tail its log, count output lines/tests, inspect its process tree, start another monitor or rerun it to ask whether it is alive. Use only its documented cancellation control when cancellation is authorized. Diagnose after a terminal failure, timeout/stall/cancellation, or evidence that the owner disappeared without a terminal record.
2. Read complete saved evidence in bounded batches: terminal status, relevant log spans, progress/artifact record, runtime/source identity and the owner's configured limits. Follow subsequent spans to the end rather than treating a truncated preview as the full log. Inspect processes or external state only after the active-owner boundary is resolved.
3. Establish the last completed step and whether a side effect may have committed. For database, queue, filesystem, remote request or publication operations, use the authoritative operation/transaction/build identity and actual state. Prove whether continuation is idempotent before retrying; a client error does not prove the server did nothing.
4. State a falsifiable cause hypothesis and the observation that confirms or rejects it. Narrow the command, input, filter or boundary; change one condition. Add at most one focused temporary diagnostic signal only when existing evidence cannot distinguish the causes. Remove it after proving the cause unless a permanent diagnostic requirement justifies it.
5. Preserve runner-owned C033 timing policy. Do not hard-code skill timeouts or increase a limit without evidence of useful progress and authorization through its owner. A progress-supported diagnostic rerun is not a new successful maximum until it passes. Current Poise verification-method contracts do not declare per-method timeouts; do not reintroduce one through this skill. Infrastructure-probe limits remain owned by their current runner/configuration.
6. Classify a missing binary, service, credential, configuration or runtime precisely. Fix a non-destructive in-scope development dependency through its normal owner when permitted; otherwise record the exact missing input, blocked stage and setup required. Never silently substitute host tooling, a sample configuration or a different runtime. Do not repeat discovery for a capability already proved unavailable.
7. For a post-merge regression, first recover the previously verified code and evidence through the owning recovery/integration mechanism, compare that code with the actual requirements, then isolate the new difference. Never reset another worktree or lose unique work to obtain a clean-looking result.
8. Use N01 generic public phase recovery only when the installed API implements it. Use the currently documented [work/rework operations](../../../docs/workflows/batch-work.md#rework-при-нерассмотренных-исправлениях) otherwise, respecting pending resolution inspection. Never edit the Task DB, lifecycle state, claim or evidence to move a Task forward.
9. Run the smallest diagnostic or regression check justified by the changed cause, then the maintained smoke where required. A deterministic failure needs a changed cause before rerun. A flaky pass alone is not validation; preserve the failing signal and repair or formally track the unrelated defect without silently passing its blocked obligation.

## Output

Save symptom/command, terminal identity, last completed step, side-effect state, inspected evidence, hypothesis, diagnostic change, actual result, justified retry/recovery and regression evidence through the existing Task/evidence/artifact owners. Distinguish recovered execution from unverified behavior. Release or transfer through public handoff; a blocker is not successful validation, and a conversation note is not durable state.

## Checkpoint recovery

Determine which store owns the failed operation using the current [storage matrix](../../../docs/architecture/storage-lifecycle.md#владельцы-и-версии). Follow only the [limited ownership upgrade](../../../docs/architecture/storage-lifecycle.md#ограниченный-переход-task-db-12-в-13) for known v12 Task data, and preserve external databases using the [explicit path mapping](../../../docs/architecture/storage-lifecycle.md#явные-пути-и-переносимая-поставка). A Task-only backup is not a full repository backup.

For lost execution environments use [checkpoint and recovery](../../../docs/workflows/checkpoint-recovery.md#контрольная-точка-и-восстановление). Read the saved state and inspect existing attachments before repeating work or requesting an upload. Restore into a new directory; never use a rejected branch or an email summary as source code.
