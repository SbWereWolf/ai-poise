---
name: poise-development
description: Change AI poise itself using its DDD, TDD, declarative-tool and project-local configuration rules.
---

# AI poise development

Read the repository `AGENTS.md` and `src/AGENTS.md` first. Work in a dedicated `tasks/<task-id>` worktree.

## Design boundaries

Identify the domain owner before implementation. Task, Sprint, content, evidence, artifacts, project configuration and runtime state change only through their owning APIs. Reuse the common runner and stage-handler families rather than creating a goal-specific engine. Read [Architecture boundaries → owners and dependencies](../../../docs/architecture/boundaries.md#ddd-04b--новые-владельцы-и-зависимости).

Keep caller-to-session establishment in `SessionEstablisher` for every public work composition.
Use native identity when available and an explicit persistent `POISE_CALLER_BINDING` otherwise;
callers and agents must not substitute an arbitrary session value. Identity origin is provenance,
not authorization, and read-only establishment must not create a business subject.

## Development method

For repository changes follow TDD: contract/check design → tests → honest RED → test inspection → implementation → GREEN → code inspection → fixes → reinspection → documentation. Read [Development rules → TDD and inspection](../../../docs/governance/development-rules.md#tdd-и-осмотр) when working on implementation behaviour.

For review, apply the incident-derived [evidence-based checklist](../../../docs/workflows/evidence-based-review.md#что-считать-находкой). Every actionable finding identifies the confirmed missing obligation, its producer stage, the later gate, an exact reproducible check and the evidenced cause class. Distinguish an AI poise runtime/task-contract defect, executor omission, reviewer omission, ordinary protective rejection and unresolved ownership. A historical repaired incident is regression provenance, not a current defect without new post-fix evidence. Batch independent Task/section/evidence/registry reads through the existing `show.queries`; never add another reader or claim unmeasured token savings.

For a development Task, pass the complete initial checks schedule explicitly; `{}` is valid and must not trigger template defaults. Design and register the actual RED/GREEN methods, schedules and future-output provenance at `verification_planning`, once their source paths are known.

Public non-newborn `bootstrap` and current-Task `show` projections expose the exact current Task version as `version`. The private `_version` name is never part of the public DTO. Taskless responses omit `version`, and newborn Tasks use `revision`. Pass this value unchanged as `expected_version` for guarded `restart` or stage-contract repair; on a version conflict, refresh through public `bootstrap`/`show` instead of guessing.

Tools are batch-oriented and declarative: if two or more required mechanical actions have no reasoning decision between them, expose one operation that ensures the requested result through owning APIs. Do not add hidden defaults, compatibility readers or migrations without direct user authorization. Read [Declarative tools → architectural invariant](../../../docs/architecture/declarative-tools.md#1-архитектурный-инвариант) for the corresponding contract section available in the current revision.

Keep stale observation recovery inside the existing verification owner. An `observe` stage may
replace only one of its current `evidence_plan.subject_methods` through the guarded registry
change contract. Preserve its schedule, executable classification and coverage, keep historical
definitions and proof records immutable, and bind the first mutation/replay to one durable audit
identity. Structural registry edits remain owned by `test_registry`. Read the exact invariants in
[Evidence → stale observation replacement](../../../docs/workflows/evidence.md#замена-устаревшего-метода-наблюдения).

Keep the pending-resolution rework guard in the Task aggregate, before route or execution mutation.
Its error must identify only unresolved resolution IDs and the exact next inspection stage; clean
rework remains unchanged. Do not replace this invariant with a workaround Task or direct Task DB
repair. Read [Batch work → rework with pending resolutions](../../../docs/workflows/batch-work.md#rework-при-нерассмотренных-исправлениях) (Task 0063 RD-013).

## JetBrains MCP and structural fallback

For code-semantic work, treat configured endpoints, listed tools, worktree-bound observations,
and proof of a concrete operation as separate states. Use an applicable worktree-bound JetBrains
MCP capability for semantic navigation, call hierarchy, IDE inspections, semantic rename, or
IDE-owned formatting; select by tool semantics rather than a stored name map. Follow the exact
[selection rule](../../../docs/governance/jetbrains-mcp-policy.md#правило-выбора-инструмента) and
[boundaries](../../../docs/governance/jetbrains-mcp-policy.md#границы-и-исключения).

If the required IDE capability is unavailable or inapplicable, record the exact reason before
using ast-index for an operation it can replace. Use ast-index directly for repository-wide graph,
batch, and structural search. Do not claim that ast-index performed IDE inspection, semantic
refactoring, or IDE formatting. Follow [Fallback to ast-index](../../../docs/governance/jetbrains-mcp-policy.md#fallback-на-ast-index)
and preserve the [capability evidence](../../../docs/governance/jetbrains-mcp-policy.md#проверка-и-evidence) separately from configuration intent.

## Project-local configuration

AI poise is a separate application. Each configured project owns its Task DB and its copied process catalogue initialized from AI poise reference templates. Changing a reference template must not silently change an existing project's process configuration. For the WSL delivery model, read [Local installation → Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Project setup → Publication and replay](../../../docs/configuration/project-setup.md#публикация-и-повтор).

When another authorized owner has replaced a managed process file, diagnose the exact head
and live revisions with `goal-config-status-1`, then adopt validated content only with an exact
`goal-config-reconcile-1` request carrying reason and authority. Do not create a fresh editor database
to evade a stale managed head. Read [Goal config → revision reconciliation](../../../docs/configuration/goal-config.md#сверка-управляемой-revision-с-live-конфигурацией) before acting.

## Verification and delivery

An idle reviewer starts on a received handoff. If several handoffs arrive, finish the review already started, save its result, release the Task and notify its executor, then immediately take the next already-received actionable Task in the same turn. Incoming handoffs do not interrupt the current review. Follow the canonical [direct-handoff rule](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим).

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks and the maintained bounded `tests/smoke.sh`; never register unfiltered repository-wide test discovery as a task method. Preserve failed diagnostic workspaces and do not replace a failed run with a retry. Package code, tasks/configs, documentation and verification evidence as one self-contained delivery.

Follow the canonical [executor/reviewer stage policy](../../../docs/governance/development-rules.md#роли-этапов-и-непрерывность-поручения). User Task start authorizes executor and reviewer to continue their respective stages, including checks and remediation, without a separate command for each review; honor explicit user limits and AI poise gates. At a role boundary, save results, confirm public handoff/release, stop modifying the Task and [directly notify the known counterpart](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим). The receiver claims/resumes before work. Self-inspection is not independent review, and publication/integration retains its separate authorization.

After release and notification, continue actionable received assignments or the authorized stage/Task chain in the same turn. Track assignments until completed, cancelled or explicitly transferred, including pending work and blockers in checkpoints and compaction context. New messages add work unless they explicitly change an assignment; acknowledgment is not completion. Before a final answer, account for every pending assignment; end only when no authorized actionable work remains, preserving each remaining blocker and next action. Finish your own already-started operations. Checking your own pending work is required; do not poll the other agent's status, read its conversation for progress or schedule background monitoring.

Treat the accepted commit as immutable. Complete delivery by advancing the existing task branch in its existing task worktree: update from current `master`, merge, resolve conflicts there, and rerun checks. Do not create a separate integration branch or worktree. Stop automation on conflicts for agent resolution in the task worktree. Under the shared target lock, recheck `master` and repeat the cycle on drift. Publish only with `git merge --ff-only <task-branch>` in the main checkout; never directly update or force-update the target ref.

Do not prepare, edit, or resolve conflicts in the main checkout or foreign WIP, and never `stash`, `reset`, `restore`, `checkout`, `clean`, stage, commit, or delete their state. The serialized fast-forward is the only publication effect. If it is blocked, require persisted proof that main `HEAD`, binding, index, tracked/untracked content, types, modes, and operation state are unchanged. After confirmed publication, remove only the task worktree, task branch, and registered temporary backups from the scoped runtime directory. Preserve foreign, operator, deliverable, and unfinished-recovery backups. Persist phases so current installed source can replay safely without rewriting the accepted commit. Read the canonical [finish and integration rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

Repository snapshots use an invocation-owned temporary Git index and must clean only that exact owned directory. Never delete `snapshot.index.lock` manually or remove another invocation's directory; preserve real Git conflict evidence and use the owning public recovery path. Read the canonical [snapshot index contract](../../../docs/workflows/batch-work.md#изоляция-временного-git-index).

When a skill relies on canonical documentation, link to the smallest exact normative section needed by the operational rule.
