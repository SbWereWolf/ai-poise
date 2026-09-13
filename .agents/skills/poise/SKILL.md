---
name: poise-workflow
description: Execute project-local AI poise task stages and hand off work between executor and reviewer through declarative batch tools.
---

# AI poise task workflow

Use the explicitly selected project configuration. Each project owns its Task DB and its copied process catalogue; never edit managed SQLite, process JSON, or task artifacts as bookkeeping.

For WSL project selection and storage layout, read only [Local installation → Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Local installation → Mutable storage paths](../../../docs/configuration/wsl-local-delivery.md#пути-project-local-storage).

For this local ai-poise installation, use [the concrete launcher and configuration](../../../docs/configuration/project-setup.md#локальный-проект-ai-poise). `bootstrap` and `verify` are operations of AI poise `work`, not separate skills. For ordinary work invoke the session-scoped `work.sh` supplied by the native hook explicitly through Bash, keep `messages=[]`, and reuse the same launcher across calls. Direct `.venv/bin/poise work` uses a native Codex identity when available; otherwise it requires an absolute persistent `POISE_CALLER_BINDING` whose parent already exists. An agent must not substitute a caller-chosen session value, and a direct diagnostic call does not establish native event delivery. The user explicitly authorized state inside this repository.

## Start or resume work

Apply the canonical [ownership rule](../../../docs/governance/development-rules.md#владение-task-и-worktree):
at most one Task and one worktree per session, independently owned. The process snapshot
defines a dependent worktree; release it with its Task but preserve independent ownership.
Acquire the complete set through the public owner, recognizing self-ownership and replacing
old same-kind claims atomically. Never steal an uncertain live claim. Claims do not change
WIP, cwd or launch roots and do not authorize cleanup or integration. At a role boundary,
the sender saves results, confirms public release, then directly messages the known counterpart;
the recipient acquires through bootstrap before working, under existing Task authorization.

Use one `bootstrap` package to obtain the current task/sprint, stage, process snapshot, required content, worktree, findings/evidence and available capabilities. For the WSL invocation, read [Local installation → Start work](../../../docs/configuration/wsl-local-delivery.md#начало-работы-над-задачей).

Use public `operation: task` actions to prepare a real newborn Task. A real newborn Task has
a permanent identity and history, uses the shared ownership API, and preserves no route entry before goal_type selection.
`create`, `edit`, `ready`, and `restart` require stable request IDs; preserve exact
replay and reject foreign live ownership. A ready standalone Task becomes available, while a
Sprint member remains newborn until publication. Use Sprint `materialize_tasks` to convert
legacy embedded definitions while preserving partial edits, history, graph aliases, membership,
and source Task traceability. Do not introduce a broad migration or replace direct complete
Task creation.

Every newborn Task `edit` request explicitly supplies both `patch` and `remove`; at least one
is nonempty. Use `remove` in the same optimistic request when a goal-type change makes a saved
draft field invalid. Never encode deletion with null, silently clean the draft, or omit `remove`
as a compatibility path. Preserve exact replay and reject unknown, absent, duplicate, required,
immutable, or patch-conflicting removals before mutation.

When a saved execution contract makes DoD unattainable or the next stage fails its own DoR,
treat the outcome as `broken`, never as successful completion. A reviewer may repair only the
defective stage contract, or an authorized owner may restart the same Task to newborn through
the public `task` action. Preserve Task identity, immutable history, Sprint membership,
worktree/branch and all WIP; reject terminal work, a foreign live owner, stale version, or a
pending external outcome before mutation. Resolve pending uncertainty through its explicit
recovery protocol first. Do not use or recreate the removed Sprint `replace_task` correction
action. Historical replacement relations remain read-only provenance.

Use the installation-owned `recover_missing_worktree` operation only for a nonterminal
verified/accepted Task whose registered worktree was removed. It must prove the saved report
commit is integrated into the configured base and has the exact verified tree before restoring
the saved branch/worktree. Reject a path that is not a registered worktree of the configured
repository. On failure, remove only the worktree and branch created by that invocation; it never
changes Task ownership, lifecycle, result, or handoff. An idle session or the current owner of
that same Task may invoke it; an owner of another Task may not.

If a lawful external process publication leaves the goal-config editor head stale, first use
the read-only `goal-config-status-1` diagnostic and establish the provenance of the live file.
Adopt it only through an exact `goal-config-reconcile-1` request with the observed managed and
live revisions, reason and authority. Do not create a fresh editor database to bypass stale
ownership metadata. Follow [Goal config → revision reconciliation](../../../docs/configuration/goal-config.md#сверка-управляемой-revision-с-live-конфигурацией).

When `bootstrap` explicitly addresses a `completed`, `cancelled`, or `superseded` Task, consume the returned `terminal inspection snapshot` with its preserved context, content, evidence, and history. Do not expect or create a current-task binding, and do not issue a follow-up `show` to recover terminal data. A cancelled Task may legitimately have no evidence. After inspection, taskless bootstrap must return `read_only`; only then may null-result verify return `read_only_verified`.

Do not create a worktree for read-only queries. For repository-changing work, use the worktree supplied by AI poise and read the target repository's applicable `AGENTS.md` before edits.

## Complete the current stage

Submit the stage result and related sections/findings/evidence/artifacts in one logical package. Use `verify`; do not hand-edit intermediate result files or the database. Read [Batch work → Verify](../../../docs/workflows/batch-work.md#verify) and, when files are required, [Batch work → File creation](../../../docs/workflows/batch-work.md#создание-файлов).

Batch independent reads that are known before the call into one public `show` packet with
multiple `queries`. Give each query a stable unique `id` and correlate the returned items by
that ID. Use the existing `task`, `section`, `content`, `evidence`, `verification_registry`,
`trace` and other supported query kinds; do not create another reader or inspect managed SQLite.
Do not batch a dependent read until its identifier or range is known. One packet is not a
cross-read-model SQL snapshot, and no token saving has been measured or may be claimed. Follow
[Evidence-based review → Batched subject reads](../../../docs/workflows/evidence-based-review.md#пакетное-чтение-предмета).

When a current observation command has become stale, do not recreate the Task or edit its
database. Read the current registry with one `show` query of kind `verification_registry`, then
submit one guarded `method_additions` change from the current `observe` stage. Replace only a
method owned by that stage's `evidence_plan.subject_methods`; provide the exact current revision,
a new idempotent request ID and unchanged stages, `evidence_kind`, `covers` and
`executable_obligations`. Add/remove/reschedule and guard or classification changes still require
a `test_registry` stage. Preserve the returned audit receipt; a replay must return its original
identity without another event. Follow [Evidence → stale observation replacement](../../../docs/workflows/evidence.md#замена-устаревшего-метода-наблюдения).

Use native coding/IDE tools for source changes. AI poise owns task state, evidence registration, execution receipts, Git lifecycle boundaries and managed artifacts. Follow the canonical [executor/reviewer stage policy](../../../docs/governance/development-rules.md#роли-этапов-и-непрерывность-поручения). A user instruction to start a Task authorizes its executor and reviewer to continue ordinary execution, review and remediation through AI poise gates without a new user command for each stage or review. Honor an explicit stage-only assignment or other user limit. Continue stages of your own role; at a role boundary follow the handoff procedure below. Do not bypass gates or act as your own independent reviewer. Stop for a real blocker, a new required decision, or separately controlled acceptance/publication/integration.

When a requested stage is beyond the current stage, use the public `advance` operation with a stable `request_id`, the exact Task ID and exact `target_stage`. Replay that exact request after completing and verifying each current-role stage, after a lawful correction or restart, and after the receiving session bootstraps a public handoff. Treat `progression_work_required`, `role_handoff_required` and `user_acceptance_required` as pauses, not success. At a role boundary, include the active progression identity in the direct handoff message. Never change its target under the same request ID or use it to cross gates, perform stage work, send the handoff message, record user acceptance, or infer publication authority. A publish stage opens only through the separately authorized public acceptance path.

Poise isolates the temporary Git index for every repository snapshot invocation and cleans only that invocation's owned directory. Never delete `snapshot.index.lock` manually or remove a sibling snapshot directory: preserve a genuine Git conflict diagnostic and let the owning lifecycle recover it. See the canonical [snapshot index contract](../../../docs/workflows/batch-work.md#изоляция-временного-git-index).

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks and the maintained bounded `tests/smoke.sh`; never register unfiltered repository-wide test discovery as a task method.

For final result integration, keep the accepted commit immutable. The public finisher advances the existing task branch in its existing task worktree; all updates from current `master`, merges, conflict resolution, and checks happen there, with no separate integration branch or worktree. If it returns a conflict, pause automation for agent resolution in that task worktree, then resume checks. The finisher rechecks `master` under the shared target lock and repeats update, resolution, and checks on drift. It publishes only through `git merge --ff-only <task-branch>` in the main checkout and never updates the target ref directly or by force.

Never use the main checkout or foreign WIP for preparation or conflict resolution, and never `stash`, `reset`, `restore`, `checkout`, `clean`, stage, commit, or delete their state. A dirty or unfinished main checkout may block `merge --ff-only`; require recorded before/after proof that its full state is unchanged. After confirmed publication, the same lifecycle removes only the task worktree, task branch, and registered task-scoped temporary backups. Preserve pre-existing, foreign, durable operator, deliverable, and unfinished-recovery backups. Resume persisted incomplete phases idempotently after a crash, using current installed AI poise source. Read the canonical [finish and integration rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

## Sprint work

Bootstrap the sprint to get the eligible set instead of calculating dependencies manually. `verified` is not `completed`; predecessor completion follows the task's acceptance policy. Read [Sprint API → Work selection](../../../docs/workflows/sprints.md#выбор-работы-и-один-пакет-контекста) and [Sprint API → Dependency kinds](../../../docs/workflows/sprints.md#два-явных-вида-зависимостей).

Treat result dependencies as readiness plus `result_provenance`, not Git ancestry. Every new successor worktree starts from the current configured base ref; AI poise does not use or merge predecessor result commits as its branch base.

## Capabilities

Treat configured inventory and actually probed capability as different facts. Use only capabilities reported for the current project/worktree. For the current probe semantics, read [Runtime hooks → Capability checks](../../../docs/configuration/runtime-hooks.md#проверки-capabilities).

## Task DB backups

Discover the public backup surface with `poise backup help`. Use `poise backup list --config PROJECT_JSON`, `poise backup create --config PROJECT_JSON`, and `poise backup restore --config PROJECT_JSON BACKUP_NAME`; do not substitute manual filesystem copies or database replacement. Create and restore require an exclusive operator window in which no agent or process writes the Task DB. Read the exact storage, integrity and recovery contract in [Task DB backups](../../../docs/task-db-backups.md).

## Handoff and transfer

User Task start authorizes routine reviewer work as well as execution; no separate command is needed for each review. At every executor/reviewer boundary, the sender preserves the result or findings and required evidence, uses one public `handoff` package, verifies that ownership was released, and stops modifying the Task. The receiver claims/resumes it through public bootstrap before mutation. A chat message is not release; report a failed handoff as failed. Read [Local handoff → Role transfer](../../../docs/workflows/local-handoff.md#передача-между-исполнителем-и-ревьюером).

When the Task's counterpart executor/reviewer is known, apply [Direct role handoff](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим) in both directions:

1. Save the result, evidence and findings, complete your required checks, and confirm that public `handoff` actually released the Task.
2. Stop Task/worktree changes, then use the available direct-message tool to notify that known counterpart. Include project/Task ID, stage and receiving role, result/findings and handoff receipt links, next action and any limits of the user's Task-start instruction. Do not ask the user to relay the message or authorize each ordinary review.
3. After successful notification, check your own pending assignments and immediately continue actionable work or the authorized stage/Task chain in the same turn. Keep received assignments in the working plan until completed, cancelled or explicitly transferred; preserve them and their blockers across checkpoints and compaction. Acknowledging a message does not discharge it. New messages add work unless they explicitly change the assignment. Do not poll the counterpart's status, read its conversation for progress or schedule background monitoring.
4. The recipient must acquire through `bootstrap` and check current state before working. A message neither transfers ownership nor substitutes for a review decision. Keep messages actionable, not repetitive status chatter.

If the recipient or messaging tool is unavailable, report the saved handoff to the user; do not contact unrelated agents. If handoff fails, do not announce a completed transfer. If notification fails after release, retry the notification against the same saved result or report the blocker; do not silently reclaim or repeat the work. Repeated messages require checking current Poise state, not replaying a finished stage. Stop for an AI poise runtime blocker or a decision outside the existing authorization, not for a routine executor/reviewer boundary.

For multiple incoming review handoffs, follow the linked direct-handoff rule: if idle, acquire and start immediately; otherwise finish the review already started, save, release and notify, then take the next already-received actionable Task in the same turn. A new handoff does not interrupt the current review or claim a second Task. For example, after reviewing 0082, start pending 0076 immediately rather than ending with "0076 is next".

Before a final answer, account for every received assignment and continue any authorized actionable work. End the turn only when none remains; preserve the next action and concrete blocker for each pending assignment. A blocker on one Task does not stop other assigned work when ownership permits switching. Complete your own already-started operations. Never rely on another message to restart work that has already been assigned; duplicate notifications do not require repeating completed work.

For another store/environment, use `transfer`; do not merge task databases manually. Read [Transfer → Batch API](../../../docs/workflows/transfer.md#пакетный-api).

## Observed usage

Record only real observed user-message/token events and preserve source/coverage. Do not estimate unavailable usage. For metric semantics, read [Accounting → Observed telemetry](../../../docs/operations/accounting.md#пакет-наблюдённой-телеметрии).

## Documentation references

When an operational rule is owned by canonical documentation, link to and read the smallest normative section that is sufficient for that rule. Do not require an agent to load an entire large document when one section is authoritative.

## Route semantics

Route definitions contain an explicit `entry`; stage outcomes, targets and rework targets own transition semantics. Preserve visits and transitions only for history, identity and audit. Never impose an execution limit through route counts, depth, watchdogs, timeouts or recursion bounds. A finite graph-reachability check is structural validation, not an execution budget.

Do not request rework while a pending resolution still requires independent inspection. Follow the exact inspection stage reported by Poise, decide every pending resolution there, and only then retry an authorized rework target. The rejection is state-preserving; do not create a workaround Task or edit the Task DB. Use `recover_empty_rework` only for a legacy task already stranded by the former defect. Recovery requires an unowned Task and an ownership-only event suffix, not a synthetic handoff. A cleaned worktree is recoverable only when Poise proves the saved commit is integrated into the configured base and has the exact verified tree; never recreate that worktree manually. Read [Batch work → rework with pending resolutions](../../../docs/workflows/batch-work.md#rework-при-нерассмотренных-исправлениях) (Task 0063 RD-013) and [empty rework recovery](../../../docs/workflows/batch-work.md#восстановление-ошибочно-открытой-пустой-rework-итерации) (Task 0063 RD-008).
