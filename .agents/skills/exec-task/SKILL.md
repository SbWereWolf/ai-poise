---
name: exec-task
description: Execute the authorized current Poise Task with bounded context, exact stage evidence, and durable executor/reviewer handoff.
version: 1.0.0
---

# Execute the current Task

Use when an existing Task is active or the current project's skill selection requires task-execution discipline. This
skill does not own lifecycle, schema, routing, evidence, history or acceptance.

## Inputs

Get the exact installation launcher, packets and current Task/stage through [poise-workflow](../poise/SKILL.md). Use
bootstrap's actual identity, requirements, allowed paths, explicit worktree path when present, result template, findings
and evidence. Read the selected Poise-owned routing/command/runtime/version/file-map configuration; target-project
documentation is only a read-only source of facts. Do not copy another project's command or reconstruct state from every
historical artifact.

## Current-stage skills and gaps

Use the skills assigned to the current stage, both its method of work and the
required subject expertise; do not replace the planner's selection with router
suggestions or silently remove obligations. The [two-set template
model](../../../docs/workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
is still a draft: it does not add Task fields or automatic enforcement today.
Consume only inputs actually supported by the installation; absence of proposed
fields is not a new blocker. Load needed instructions, not all future-stage references.
If expertise is missing or an assigned skill is unavailable/insufficient, report it
in the final answer with stage, consequence and proposed planner action. Raise actual
blockers immediately; do not invent skill use or grant yourself a contract edit.

## Procedure

1. Establish the authorized outcome, DoD and current stage. For any duplicate Task, the same library method resolves its
   original parent, lists all children and batch-reads the whole family's stages, processes, statuses and session owners
   in one consistent DB view. Only parent resolution differs by role. Consume the harness-supplied [family distance and
   stage-start decision](../../../docs/workflows/sprints.md#задача-спринта-дубль-автоматическая-проверка-семейства). The
   metric for ordinary repeated implementation is the shortest directed path from the current stage to positive termination, equivalently reverse traversal
   from the positive terminal. The harness must deny stage start if this distance exceeds the noncancelled family
   minimum by even one transition; equality is necessary but not sufficient. If two or more nearest Tasks have sessions,
   the default request is refused; agents must agree one executor, who explicitly sends force_duplicate_start=true for
   this stage-start request. Count the requesting session for an unowned current Task before persisting its claim. The
   optional bootstrap/advance flag bypasses only the tied-session refusal, never a smaller family distance, invalid
   data, foreign ownership or ordinary stage requirements. Read only the returned nearest relatives: all peers at that
   minimum, their stages/distances and session IDs, not intermediate leaders or peers tied at a lagging distance.
   Different stage names at equal distances are still ties. The multi-session refusal also applies on initial stages.
   Every request, including flagged retries, rereads the family; never auto-add the flag, persist it for later stages or
   stop another owner's work. Do not search similar Tasks, manually enumerate the family, or use the tie-specific flag
   to override any other denied gate. Check again at resume and each actual stage start; a warning alone is
   insufficient. A completed relative has distance zero, but reuse still requires main-base integration and local
   availability; do not bypass the stage gate or invent a terminal transition for reuse. The shared reader, duplicate
   action and gate are implemented. For a matching completed family result use work/reuse (task_id, source_task_id,
   request_id, expected_version) instead of ordinary stage work. It checks main integration, local availability and own
   checks; reuse_verified still needs explicitly authorized accept. Keep the original packet on retry or handoff resume;
   the preserved native handoff bundle is validated before reacquisition. A completed replay is historical, not a new
   run. Artifact-bound contracts remain rejected until local delivery is implemented. Acquire through the existing
   owner; at most one Task and one worktree are owned, with independent claims governed by the [ownership
   rule](../../../docs/governance/development-rules.md#владение-task-и-worktree). Work only in the supplied worktree and
   preserve unrelated WIP.
2. Batch independent reads with current `show.queries`. Load only rules, skills and reference sections needed for the
   current decision. Use [artifact boundaries](../../references/task-artifact-contract.md), not manually maintained task
   files or changed-path registries; Git owns the diff.
3. Use the current result template and owning APIs for Task content, trace, verification methods, observations, feedback
   and artifacts. Do not edit SQLite, managed process files, Task history or copied state directly. A saved section or
   structural receipt is not substantive success.
4. For behavior changes, follow the current test-first route and inspect the tests before implementation. Distinguish
   test failures from environment failures. Verify only targeted checks plus maintained fast smoke at ordinary
   completion/integration; never fall back to full discovery when a target cannot be selected.
5. Save one complete `verify` packet, including related content, evidence and artifacts. Inspect terminal status,
   checks, stage outcome and explicit continuations. An exit code or `awaiting_continuation` is not proof of completion;
   exact replay preserves request identity.
6. Continue already-authorized executor stages with the public `advance` operation. At a role boundary, preserve the
   result, confirm public release, then follow the [handoff contract](../../references/context-handoff-contract.md).
   There are only executor and reviewer; self-review does not impersonate the other role.
7. Diagnose failures from saved evidence. Before retrying an effectful action, determine what completed and how the
   owning API resumes it idempotently. Use only recovery operations actually exposed by the current installation. Do not
   simulate a planned universal phase-recovery capability or mutate lifecycle state manually.
8. After release, continue only an already-authorized actionable Task/stage chain. Do not create a new Task or expand
   the assignment merely because one finished. Use the implemented [cross-project `next`
   overview](../../../docs/configuration/project-setup.md#доступные-задачи-во-всех-проектах) for discovery; it is not an
   atomic selection/claim substitute and does not resume already-started Tasks.

## Output

A durable current-stage result with requirement coverage, exact checks, unresolved findings/dependencies, source/result
identity and next authorized action. Existing Poise owners store, verify and transfer it. The [handoff
protocol](../../../docs/workflows/local-handoff.md#пакет) preserves incomplete work honestly. Acceptance, publication
and integration retain their separate gates and authority; this skill never grants push permission or a second
lifecycle.

## Local recovery after imported duplicate results

An accepted relative is an input, not proof that this branch satisfies its requirements.
After importing the exact accepted commit, a failed/stale reuse candidate must not trap
the local Task behind family_ahead. Use the existing authorized task/restart, optional
contract edit, ready and normal bootstrap/verify/accept in the preserved worktree.
The Task owner records validated local_repair provenance; this local correction is
not governed by the relative's distance and never requires another repair Task.
Keep ordinary ownership, stage requirements and local Sprint dependencies. Do not
use force_duplicate_start as a repair bypass, fabricate success or remove family links.
A verifier may explicitly revise a defective check through the existing authorized
contract operations; never weaken checks automatically. Unknown checks require effects
inspection/stoppage before authorized restart archives their attempt without replay.
See [the recovery contract](../../../docs/workflows/sprints.md#локальная-доработка-через-общий-restart).
