---
name: poise-workflow
description: Execute project-local AI poise task stages and hand off work between executor and reviewer through declarative batch tools.
---

# AI poise task workflow

Use the explicitly selected project configuration. Each project owns its Task DB and its copied process catalogue; never edit managed SQLite, process JSON, or task artifacts as bookkeeping.

For WSL project selection and storage layout, read only [Local installation → Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Local installation → Mutable storage paths](../../../docs/configuration/wsl-local-delivery.md#пути-project-local-storage).

For this local ai-poise installation, use [the concrete launcher and configuration](../../../docs/configuration/project-setup.md#локальный-проект-ai-poise). `bootstrap` and `verify` are operations of AI poise `work`, not separate skills. For ordinary work invoke the session-scoped `work.sh` supplied by the native hook explicitly through Bash, keep `messages=[]`, and reuse the same launcher across calls. Direct `.venv/bin/poise work` uses a native Codex identity when available; otherwise it requires an absolute persistent `POISE_CALLER_BINDING` whose parent already exists. An agent must not substitute a caller-chosen session value, and a direct diagnostic call does not establish native event delivery. The user explicitly authorized state inside this repository.

Treat launcher availability and configured interpreter capability separately. Managed hooks and
launchers execute the interpreter selected by installation settings directly; they do not provide
an alternate task-work runtime or a special recovery protocol. If that interpreter is unavailable,
report or restore the installation through ordinary operator procedures, then repeat the native
event. Do not improvise a system-Python `poise work` invocation as a task-work fallback or claim
that AI poise emitted a recovery command.

When a runtime preflight reports an environment/configuration failure, preserve its
exact interpreter/launcher path and the existing native binding. Follow only
[ordinary operator restoration](../../../docs/configuration/runtime-hooks.md#действия-оператора-при-потере-среды).
If Poise blocks otherwise executable subject work explicitly authorized by the user,
continue only that subject work manually and record actual changes, checks, WIP and
the incident. Do not repair Poise or invent a replacement harness without a separate
assignment; do not fabricate Poise completion, native delivery or a new identity.

When native hooks and the registry carry different historical installation IDs,
use the existing `runtime-config` `reconcile` operation with the observed live-file
revision, explicit old ID, exact live immutable definition path and full new
definition. Do not append another installation, delete foreign groups, or edit the
hook SQLite registry manually. Reuse the same request after an interrupted write.
Read [installation identity reconciliation](../../../docs/configuration/runtime-hooks.md#согласование-installation-identity-после-переименования).

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
Sprint draft materialization creates each newborn member without a claim, so the planning
session keeps ownership of only its current Task while it edits the draft graph.
`create`, `edit`, `ready`, and `restart` require stable request IDs; preserve exact
replay and reject foreign live ownership. A ready standalone Task becomes available, while a
Sprint member remains newborn until publication. Use Sprint `materialize_tasks` to convert
legacy embedded definitions while preserving partial edits, history, graph aliases, membership,
and source Task traceability. Do not introduce a broad migration or replace direct complete
Task creation.

Read task_decomposition from the selected project's project-specific routing. Declare every process phase before an ordinary or integration Task is made ready. Each phase names its
skills and areas; the phase set must exactly match the selected process snapshot. Meta and general skills do not split a Task; peer narrow responsibilities do. Phase boundaries cannot
hide a cross-responsibility ordinary Task. Across every phase, each area routed to a policy-owned
narrow responsibility must match a declared narrow-skill responsibility; an area whose route is
not owned by a narrow skill does not become narrow through phase placement. Use integration only when its declaration names
component inputs, one combined result, integration checks, and allowed paths covering all
phase areas. Treat validation as declaration consistency, not proof that the inventory or
declared scope is factually complete.

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
action. Historical relations exist only as opaque revision/audit records, not a current replacement projection.

Task restart atomically invalidates every mutable current `work-packet identity` for that
Task in the same Unit of Work as the newborn lifecycle reset. This does not rewrite or delete
immutable `submissions`, `task_results`, `evidence`, or Task `history`; failure of invalidation
or any later restart step rolls the whole transaction back. After `edit`/`ready`, save a fresh
result normally even when the restarted route reuses the same stage and iteration. For a current
verified result, an exact packet replay remains idempotent, while different result/artifact input
must follow the domain `PoiseError` rework path and must never surface an internal `NameError`.

When an interrupted invocation leaves `pending=checks`, retry only the exact current submitted
`verify` packet. The runtime may reconcile it without rerunning checks only when every terminal
receipt is present and exactly matches the current stage, iteration, submission, tree, execution
key, invocation, ledger record, and persisted output digests. Treat incomplete, mismatched,
duplicated, unknown, or output-corrupt receipts as unresolved external outcomes and make no Task
or execution mutation.

Public non-newborn `bootstrap` and current-Task `show` projections expose the exact current Task version as `version`. The private `_version` name is never part of the public DTO. Taskless responses omit `version`, and newborn Tasks use `revision`. Pass this value unchanged as `expected_version` for guarded `restart` or stage-contract repair; on a version conflict, refresh through public `bootstrap`/`show` instead of guessing.

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

When `bootstrap` explicitly addresses a `completed` or `cancelled` Task, consume the returned `terminal inspection snapshot` with its preserved context, content, evidence, and history. Do not expect or create a current-task binding, and do not issue a follow-up `show` to recover terminal data. A cancelled Task may legitimately have no evidence. After inspection, taskless bootstrap must return `read_only`; only then may null-result verify return `read_only_verified`.

Do not create a worktree for read-only queries. For repository-changing work, use the worktree supplied by AI poise and read the target repository's applicable `AGENTS.md` before edits.

## Stage skills: draft contract and current reporting duty

The [stage-skill proposal](../../../docs/workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
describes two Task-owned assignments per stage: meta and subject skills, initially
copied from the selected stage template and adjusted by the planner. It is not yet
implemented in Task/template schemas or bootstrap/verify enforcement. Do not send
invented fields or block existing Tasks on absent draft fields. Use actual current
stage assignments; router recommendations do not silently replace them. Report any
missing or insufficient specialist skill in the final answer with its stage, impact
and proposed planner action. Escalate real blockers immediately and preserve gaps
in existing allowed result/handoff sections, without inventing a new result schema.

## Complete the current stage

Submit the stage result and related sections/findings/evidence/artifacts in one logical package. Use `verify`; do not hand-edit intermediate result files or the database. Read [Batch work → Verify](../../../docs/workflows/batch-work.md#verify) and, when files are required, [Batch work → File creation](../../../docs/workflows/batch-work.md#создание-файлов).

Batch independent reads that are known before the call into one public `show` packet with
multiple `queries`. Give each query a stable unique `id` and correlate the returned items by
that ID. Use the existing `task`, `section`, `content`, `evidence`, `verification_registry`,
`trace` and other supported query kinds; do not create another reader or inspect managed SQLite.
Do not batch a dependent read until its identifier or range is known. One packet is not a
cross-read-model SQL snapshot, and no token saving has been measured or may be claimed. Follow
[Evidence-based review → Batched subject reads](../../../docs/workflows/evidence-based-review.md#пакетное-чтение-предмета).

Let the exact process schema decide whether Task creation contains
`executable_obligations`. A schema with a `test_registry` inspection route requires the field,
including an explicit empty list; a schema without it must omit the field and receives an empty
public registry classification without requirement/DoD inference. For repository verification,
an empty `change_surface` represents only a pre-existing baseline guard with no RED and sole
GREEN at the route-entry `baseline`. Produced-result GREEN methods require a non-empty surface
covered by their stage. Follow [Batch work → Current verification registry](../../../docs/workflows/batch-work.md#текущий-реестр-методов-проверки).

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

Never execute `git push`. This prohibition is absolute for every agent, AI poise publication handler, Git adapter, and configured verification, action, tokenizer, or capability-probe runner; user publication authority does not waive it. Reject `push_required=true` before any Git command, keep `push_required=false` remote-free, and direct accepted results to the public local `integrate` operation and its exact `git merge --ff-only` publication.

Never use the main checkout or foreign WIP for preparation or conflict resolution, and never `stash`, `reset`, `restore`, `checkout`, `clean`, stage, commit, or delete their state. A dirty or unfinished main checkout may block `merge --ff-only`; require recorded before/after proof that its full state is unchanged. After confirmed publication, the same lifecycle removes only the task worktree, task branch, and registered task-scoped temporary backups. Preserve pre-existing, foreign, durable operator, deliverable, and unfinished-recovery backups. Resume persisted incomplete phases idempotently after a crash, using current installed AI poise source. Read the canonical [finish and integration rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

## Sprint work

Bootstrap the sprint to get the eligible set instead of calculating dependencies manually. `verified` is not `completed`; predecessor completion follows the task's acceptance policy. Read [Sprint API → Work selection](../../../docs/workflows/sprints.md#выбор-работы-и-один-пакет-контекста) and [Sprint API → Dependency kinds](../../../docs/workflows/sprints.md#два-явных-вида-зависимостей).

Treat result dependencies as readiness plus `result_provenance`, not Git ancestry. Every new successor worktree starts from the current configured base ref; AI poise does not use or merge predecessor result commits as its branch base.

Inter-task dependencies exist only inside one Sprint; a standalone Task or a member of another
Sprint cannot be an endpoint. Use draft `adopt_tasks` atomically only for Tasks with
`status=available, claimed_by=null, worktree=null, pending=null, last_report=null, and attempts=0`.
Use published `extract_tasks` only for the same eligible member with no incoming or outgoing dependency.
Identity, immutable history, goal, contract, and readiness are preserved. A
successful conversion changes membership, graph, revision, and the request receipt together;
rejection changes none of them, and a retry after rejection is not a replay. Draft
`remove_tasks` remains the pre-publication correction route, and draft cancellation atomically
detaches both newborn and adopted Tasks.

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


## Inputs for migrated skills

The project-local `.agents/skills/` tree supplies discoverable instruction files, not a second managed routing policy. Use the selected Poise project and actual Task/stage inputs. Read skill classes and area declarations from the project's existing `task_decomposition`; resolve other project settings through their current owner. Target-worktree files remain read-only sources of facts for this configuration boundary.

## Procedure for migrated skill selection

For current Task discipline load [exec-task](../exec-task/SKILL.md); for preparing focused Tasks or Sprint graphs load [sprint-design](../sprint-design/SKILL.md). Keep the exact packets, launcher, lifecycle and evidence semantics in this skill and its canonical documentation. Load other catalogue skills only when relevant to the current stage and real project stack, not as a blanket catalogue read.

The current decomposition validator checks declarations; it does not by itself return a complete ordered area/stage skill-and-input packet. Do not label instruction discovery as implemented automatic routing, synthesize undocumented profile fields, or place a Harness config in the target repository. An absent required routing/aggregation capability is a concrete dependency for the owning Poise work, not a reason to invent a parallel router.

## Output boundary for migrated skills

Persist the current skill's result through the existing Task/content/evidence/artifact owners and release via public handoff. Saving remains distinct from acceptance. Planned universal phase recovery and cross-project next selection are not activated by these instruction files; connect them only when the corresponding public capability actually exists.

## Accidental cancellation recovery

Use the public `task` action `recover_cancelled` only with real explicit user
permission, exact `task_id`/`expected_version`, a stable `request_id`, nonempty
`reason` and `authorization`. This restores the exact prior unfinished state;
it is not `restart`, does not acquire a claim and never edits Git/WIP. Preserve
its audit and use ordinary bootstrap afterwards. Completed/integrated work,
foreign/ambiguous ownership, pending external effects or cleanup with a chosen
commit disposition must not be bypassed. An old cancellation without a precise
recovery point is not guessed. Do not issue direct Task DB edits.
Read [the exact packet and guards](../../../docs/workflows/batch-work.md#восстановление-ошибочно-отменённой-task).


### Registered artifacts after owner-root migration

Updated: 2026-09-17. With explicit user authorization, recover one released Task's
registered artifact collection through `recover_artifacts` from a taskless caller.
Use observed version, existing IDs and exact old owner roots; do not edit registry
rows, claim another Task or relax path/digest validation to get past this defect.
Current configuration determines destinations. Missing historical bundle bytes
must come from a real backup; a receipt is not a substitute. Read the
[public root-recovery contract](../../../docs/workflows/batch-work.md#восстановление-зарегистрированных-артефактов).

### Ambiguous legacy ownership

Use `show` with `kind: ownership_conflicts` and then the explicitly authorized
`recover_ownership` decision for one exact connected component. Copy its actual
snapshot and Task/session identities. Keep existing values or release to null;
never infer Task ownership from a worktree binding, invent a claimant, or select
between live/uncertain owners. Only authoritative DEAD permits foreign release;
explicit self-release is supported. Unrelated Tasks remain usable while v12
uniqueness installation is pending. Repair never mutates Git/WIP and exact replay
never repeats the mutation. Read the [public recovery contract](../../../docs/workflows/batch-work.md#восстановление-неоднозначного-владения).

A Git `publish` stage with `push_required=false` records local-only publication
after accepted clear inspection. Submit the exact target ref, expected local
commit and explicit authorization. No remote is required or contacted; the
receipt does not move the target and is not integration. Resume the same intent
or use lawful rework when blocked; see [local publication](../../../docs/workflows/batch-work.md#локальная-фиксация-публикации).


Native caller isolation: a session launcher is not caller identity. Use only the launcher
for the host-observed CODEX_THREAD_ID/CODEX_SESSION_ID; both must agree when present.
Never set these values to impersonate a stored binding or combine native identity with
POISE_CALLER_BINDING. Configured agent_id is a profile, not a separate actor. A child with
parent-only signals must not mutate the parent's Task. The sender uses public handoff;
a genuinely separate native session obtains its own SessionStart launcher and bootstraps
the exact Task. Preserve WIP and diagnose missing/conflicting signals instead of renaming
roles, overriding native identity or editing Task DB to get past the guard.
