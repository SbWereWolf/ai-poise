---
name: task-domain
description: Change Poise Task contracts and lifecycle behavior through the Task domain owner; ordinary target-project Task execution uses exec-task instead.
---

# Task domain

Own changes to the Task aggregate, commands, drafts and lifecycle. The delivered
working skill has level 1 and maps to narrow responsibility `tasks`.

Trace the actual command, aggregate, repository and consumers; reuse TaskCommands
and the existing model. Keep transport and presentation out of domain decisions.
Follow the [shared contract](../../../docs/workflows/task-execution-contract.md#назначение):
preserve the registered goal, identity and history through draft edits and restart.
Keep stage purpose, criteria authority and independent roles distinct. Design
only the [imminent stage](../../../docs/workflows/task-execution-contract.md#проектирование-непосредственно-перед-этапом);
ordinary future-stage refinement uses its criteria owner rather than restart.

Reuse transactional [Task/worktree ownership](../../../docs/governance/development-rules.md#владение-task-и-worktree)
with the actual caller. Preserve unrelated claims and WIP; use the
[direct handoff](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим).
Saving, verification, acceptance and integration remain separate operations.

Check changed transitions through public commands and persisted results with
independent expected values, refusal-before-effects and exact replay controls.
Distinguish current projections from historical snapshots. Use the owning
recovery API rather than editing live Task database rows.
