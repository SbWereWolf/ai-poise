---
name: exec-task
description: Execute the authorized current Poise Task with bounded context, exact stage evidence, and durable executor/reviewer handoff.
version: 1.0.0
---

# Execute the current Task

Use when an existing Task is active or the current project's skill selection requires task-execution discipline. This skill does not own lifecycle, schema, routing, evidence, history or acceptance.

## Inputs

Get the exact installation launcher, packets and current Task/stage through [poise-workflow](../poise/SKILL.md). Use bootstrap's actual identity, requirements, allowed paths, explicit worktree path when present, result template, findings and evidence. Read the selected Poise-owned routing/command/runtime/version/file-map configuration; target-project documentation is only a read-only source of facts. Do not copy another project's command or reconstruct state from every historical artifact.

## Current-stage skills and gaps

Use the skills assigned to the current stage, both its method of work and the
required subject expertise; do not replace the planner's selection with router
suggestions or silently remove obligations. The [two-set template model](../../../docs/workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
is still a draft: it does not add Task fields or automatic enforcement today.
Consume only inputs actually supported by the installation; absence of proposed
fields is not a new blocker. Load needed instructions, not all future-stage references.
If expertise is missing or an assigned skill is unavailable/insufficient, report it
in the final answer with stage, consequence and proposed planner action. Raise actual
blockers immediately; do not invent skill use or grant yourself a contract edit.

## Procedure

1. Establish the authorized outcome, DoD and current stage. For a Sprint-Duplicate Task, consume the harness-supplied [automatic family context](../../../docs/workflows/sprints.md#задача-спринта-дубль-автоматическая-проверка-семейства): ahead relatives and same-noninitial-stage peers with their owning session IDs. Do not search similar Tasks or manually enumerate the family. If an equivalent implementation is ahead, wait rather than reimplement. At the same noninitial stage, determine with the identified executors who continues before duplicating work; do not infer live activity solely from an owner ID. A completed result also needs main-base integration and availability in the local branch before reuse. This is a specified runtime capability, not yet implemented; do not fabricate its response or replace missing automation with a new mandatory manual search. Acquire through the existing owner; at most one Task and one worktree are owned, with independent claims governed by the [ownership rule](../../../docs/governance/development-rules.md#владение-task-и-worktree). Work only in the supplied worktree and preserve unrelated WIP.
2. Batch independent reads with current `show.queries`. Load only rules, skills and reference sections needed for the current decision. Use [artifact boundaries](../../references/task-artifact-contract.md), not manually maintained task files or changed-path registries; Git owns the diff.
3. Use the current result template and owning APIs for Task content, trace, verification methods, observations, feedback and artifacts. Do not edit SQLite, managed process files, Task history or copied state directly. A saved section or structural receipt is not substantive success.
4. For behavior changes, follow the current test-first route and inspect the tests before implementation. Distinguish test failures from environment failures. Verify only targeted checks plus maintained fast smoke at ordinary completion/integration; never fall back to full discovery when a target cannot be selected.
5. Save one complete `verify` packet, including related content, evidence and artifacts. Inspect terminal status, checks, stage outcome and explicit continuations. An exit code or `awaiting_continuation` is not proof of completion; exact replay preserves request identity.
6. Continue already-authorized executor stages with the public `advance` operation. At a role boundary, preserve the result, confirm public release, then follow the [handoff contract](../../references/context-handoff-contract.md). There are only executor and reviewer; self-review does not impersonate the other role.
7. Diagnose failures from saved evidence. Before retrying an effectful action, determine what completed and how the owning API resumes it idempotently. Use only recovery operations actually exposed by the current installation. Do not simulate a planned universal phase-recovery capability or mutate lifecycle state manually.
8. After release, continue only an already-authorized actionable Task/stage chain. Do not create a new Task or expand the assignment merely because one finished. Use the implemented [cross-project `next` overview](../../../docs/configuration/project-setup.md#доступные-задачи-во-всех-проектах) for discovery; it is not an atomic selection/claim substitute and does not resume already-started Tasks.

## Output

A durable current-stage result with requirement coverage, exact checks, unresolved findings/dependencies, source/result identity and next authorized action. Existing Poise owners store, verify and transfer it. The [handoff protocol](../../../docs/workflows/local-handoff.md#пакет) preserves incomplete work honestly. Acceptance, publication and integration retain their separate gates and authority; this skill never grants push permission or a second lifecycle.
