---
name: poise-workflow
description: Execute project-local AI poise task stages and hand off work between executor and reviewer through declarative batch tools.
---

# AI poise task workflow

AI poise is a declarative harness whose job is to remove mechanical administration and tooling implementation details from the agent's work. Templates accelerate planning; they must not become a reason to force a valid Task into a starter shape. The planner owns the newborn draft before `ready`; execution consumes the resolved Task contract.

Use the explicitly selected project configuration. Each project owns its Task DB and its copied process catalogue; never
edit managed SQLite, process JSON, or task artifacts as bookkeeping.

Before storage setup or recovery, read the current [owners and schema
versions](../../../docs/architecture/storage-lifecycle.md#владельцы-и-версии). Configuration schema and SQLite version
are distinct; the [limited Task DB 12-to-13
upgrade](../../../docs/architecture/storage-lifecycle.md#ограниченный-переход-task-db-12-в-13) does not authorize a
general migration.

For WSL project selection and storage layout, read only [Local installation →
Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Local installation
→ Mutable storage paths](../../../docs/configuration/wsl-local-delivery.md#пути-project-local-storage).

For this local ai-poise installation, use [the concrete launcher and
configuration](../../../docs/configuration/project-setup.md#локальный-проект-ai-poise). `bootstrap` and `verify` are
operations of AI poise `work`, not separate skills. For ordinary work invoke the session-scoped `work.sh` supplied by
the native hook explicitly through Bash, keep `messages=[]`, and reuse the same launcher across calls. Direct
`.venv/bin/poise work` uses a native Codex identity when available; otherwise it requires an absolute persistent
`POISE_CALLER_BINDING` whose parent already exists. An agent must not substitute a caller-chosen session value, and a
direct diagnostic call does not establish native event delivery. The user explicitly authorized state inside this
repository.

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
Read [installation identity
reconciliation](../../../docs/configuration/runtime-hooks.md#согласование-installation-identity-после-переименования).

Before an unfamiliar operation, follow [packet preparation and refusal
handling](../../../docs/workflows/batch-work.md#подготовка-пакета-и-разбор-отказа): use the actual
configured creation path, exact packet shape and current lifecycle; do not guess API fields. For an
agent-use incident, follow [instruction
clarity](../../../docs/governance/development-rules.md#ошибки-применения-и-точность-инструкций);
detailed corrections belong in canonical docs, with a short skill link.

## Harness refusal and manual continuation

Apply the canonical [failure and manual bypass rule](../../../docs/governance/development-rules.md#сбой-harness-и-ручной-обход)
whenever normal work is blocked. Classify the cause and repeatability, journal the
incident and chosen correction with alternatives, and continue authorized subject
work manually when Poise obstructs it. Recurring failures require a repair draft.
A trivial low-risk data repair does not automatically require new code. Always
verify configuration corrections, roll back unsuccessful ones, and record rejected
solutions. Preserve real evidence, independent review and foreign ownership; manual
work is never a fabricated native stage receipt or Task completion. The ordinary
managed-data prohibition below does not prohibit the narrowly scoped, documented
manual recovery authorized by this canonical rule.

## Start or resume work

Apply the canonical [ownership rule](../../../docs/governance/development-rules.md#владение-task-и-worktree):
at most one Task and one worktree per session, independently owned. The process snapshot
defines a dependent worktree; release it with its Task but preserve independent ownership.
Acquire the complete set through the public owner, recognizing self-ownership and replacing
old same-kind claims atomically. Never steal an uncertain live claim. Automatic acquisition is distinct from explicit user-authorized
[after-crash revocation](../../../docs/workflows/crash-ownership-recovery.md): restore into isolated paths,
confirm stopped writers, use the existing recover_ownership template and preserve
Task history, WIP and pending outcomes. Never fabricate SessionEnd or reuse a revoked caller. Claims do not change
WIP, cwd or launch roots and do not authorize cleanup or integration. At a role boundary,
the sender saves results, confirms public release, then directly messages the known counterpart;
the recipient acquires through bootstrap before working, under existing Task authorization.

Use one `bootstrap` package to obtain the current task/sprint, stage, process snapshot, required content, worktree,
findings/evidence and available capabilities. For the WSL invocation, read [Local installation → Start
work](../../../docs/configuration/wsl-local-delivery.md#начало-работы-над-задачей).

Use public `operation: task` actions to prepare a real newborn Task. A real newborn Task has a permanent identity and history and uses the shared ownership API. With explicit project `task_planning`, selecting a `goal_type` materializes its registered starter draft. The planner may reshape task-owned fields and the local process before `ready`; template provenance is not runtime policy. Projects without that setting retain their existing creation path. Use the [planning API](../../../docs/workflows/task-planning.md#создание-по-типу-цели); never edit managed files or databases to enable it.
Sprint draft materialization creates each newborn member without a claim, so the planning
session keeps ownership of only its current Task while it edits the draft graph.
`create`, `edit`, `ready`, and `restart` require stable request IDs; preserve exact
replay and reject foreign live ownership. A ready standalone Task becomes available, while a
Sprint member remains newborn until publication. Use Sprint `materialize_tasks` to convert
legacy embedded definitions while preserving partial edits, history, graph aliases, membership,
and source Task traceability. Do not introduce a broad migration or replace direct complete
Task creation.

Read task_decomposition from the selected project's project-specific routing. Declare every process phase before an
ordinary or integration Task is made ready. Each phase names its
skills and areas; the phase set must exactly match the selected process snapshot. Meta and general skills do not split a
Task; peer narrow responsibilities do. Phase boundaries cannot
hide a cross-responsibility ordinary Task. Across every phase, each area routed to a policy-owned
narrow responsibility must match a declared narrow-skill responsibility; an area whose route is
not owned by a narrow skill does not become narrow through phase placement. Use integration only when its declaration
names
component inputs, one combined result, integration checks, and allowed paths covering all
phase areas. Treat validation as declaration consistency, not proof that the inventory or
declared scope is factually complete.
`task_decomposition.skills` validates IDs declared in `decomposition.phases[].skills`;
it does not restrict which available relevant skills an agent may read and apply.
Use `documentation` when the work needs it even if that ID is absent from the
project list. Do not report a skill gap or change project configuration solely
because an available skill is absent there. Keep the declared narrow responsibility,
role and Task scope intact.

Every newborn Task `edit` request explicitly supplies both `patch` and `remove`; at least one
is nonempty. Use `remove` in the same optimistic request when a goal-type change makes a saved
draft field invalid. Never encode deletion with null, silently clean the draft, or omit `remove`
as a compatibility path. Preserve exact replay and reject unknown, absent, duplicate, immutable or patch-conflicting removals. Flexible drafts may remove required fields temporarily; `ready` requires the complete resolved contract. Frozen edits require authorized restart.

When a saved execution contract makes DoD unattainable, the next stage fails its own DoR, or the executor concludes from evidence that the available workaround is not adequate to the Task, do not bypass Poise or manufacture success. Preserve the evidence and agree the exact restart/Task-contract change with the independent reviewer. The agents decide and execute a restart without another user decision. An authorized owner uses the public Task action; the executor must not impersonate the reviewer. For fields outside the frozen restart-revision policy, the reviewer records the exact approved Task fields in `authorization.revision_fields`. Project/harness rule changes require separate user authority and are not mutated by Task restart. Preserve Task identity, immutable history, Sprint membership, worktree/branch and all WIP; reject terminal work, a foreign live owner, stale version, or a pending external outcome before mutation. Resolve pending uncertainty through its explicit recovery protocol first. Do not use or recreate the removed Sprint `replace_task` correction action. Historical relations exist only as opaque revision/audit records, not a current replacement projection.

Follow [post-restart handoff](../../../docs/workflows/local-handoff.md#передача-после-перезапуска):
restart preserves historical results, proof and provenance; restart alone does not make
proof inapplicable. Apply the existing source/condition/provenance rules. A historical
submission is not new work. Current templates and null-result handoff use only the Task's
authoritative current submission, never a latest historical stage/iteration match.
Without a new current result, preserve actual Task state; with one, transfer that result.
Ready preserves the feedback book, including open findings and previous review decisions;
do not reapply rejected historical corrections or manufacture finding closure.
Use public handoff to release a restarted Task, including newborn work; restart itself
retains ownership. Preserve ordinary refusal, material-integrity and exact-replay gates.

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

Public non-newborn `bootstrap` and current-Task `show` projections expose the exact current Task version as `version`.
The private `_version` name is never part of the public DTO. Taskless responses omit `version`, and newborn Tasks use
`revision`. Pass this value unchanged as `expected_version` for guarded `restart` or stage-contract repair; on a version
conflict, refresh through public `bootstrap`/`show` instead of guessing.

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
ownership metadata. Follow [Goal config → revision
reconciliation](../../../docs/configuration/goal-config.md#сверка-управляемой-revision-с-live-конфигурацией).

When `bootstrap` explicitly addresses a `completed` or `cancelled` Task, consume the returned `terminal inspection
snapshot` with its preserved context, content, evidence, and history. Do not expect or create a current-task binding,
and do not issue a follow-up `show` to recover terminal data. A cancelled Task may legitimately have no evidence. After
inspection, taskless bootstrap must return `read_only`; only then may null-result verify return `read_only_verified`.

Do not create a worktree for read-only queries. For repository-changing work, use the worktree supplied by AI poise and
read the target repository's applicable `AGENTS.md` before edits.

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

Submit the stage result and related sections/findings/evidence/artifacts in one logical package. Use `verify`; do not
hand-edit intermediate result files or the database. Read [Batch work →
Verify](../../../docs/workflows/batch-work.md#verify) and, when files are required, [Batch work → File
creation](../../../docs/workflows/batch-work.md#создание-файлов).

Batch independent reads that are known before the call into one public `show` packet with
multiple `queries`. Give each query a stable unique `id` and correlate the returned items by
that ID. Use the existing `task`, `section`, `content`, `evidence`, `verification_registry`,
`trace` and other supported query kinds; do not create another reader or inspect managed SQLite.
Do not batch a dependent read until its identifier or range is known. One packet is not a
cross-read-model SQL snapshot, and no token saving has been measured or may be claimed. Follow
[Evidence-based review → Batched subject
reads](../../../docs/workflows/evidence-based-review.md#пакетное-чтение-предмета).

Let the exact process schema decide whether Task creation contains
`executable_obligations`. A schema with a `test_registry` inspection route requires the field,
including an explicit empty list; a schema without it must omit the field and receives an empty
public registry classification without requirement/DoD inference. For repository verification,
an empty `change_surface` represents only a pre-existing baseline guard with no RED and sole
GREEN at the saved route entry, whatever its Task-owned name (for example, `baseline` or `reproduce`).
Produced-result GREEN methods require a non-empty surface
covered by their stage. Follow [Batch work → Current verification
registry](../../../docs/workflows/batch-work.md#текущий-реестр-методов-проверки).

When a current observation command has become stale, do not recreate the Task or edit its
database. Read the current registry with one `show` query of kind `verification_registry`, then
submit one guarded `method_additions` change from the current `observe` stage. Replace only a
method owned by that stage's `evidence_plan.subject_methods`; provide the exact current revision,
a new idempotent request ID and unchanged stages, `evidence_kind`, `covers` and
`executable_obligations`. Add/remove/reschedule and guard or classification changes still require
a `test_registry` stage. Preserve the returned audit receipt; a replay must return its original
identity without another event. Follow [Evidence → stale observation
replacement](../../../docs/workflows/evidence.md#замена-устаревшего-метода-наблюдения).

Use native coding/IDE tools for source changes. AI poise owns task state, evidence registration, execution receipts, Git
lifecycle boundaries and managed artifacts. Follow the canonical [executor/reviewer stage
policy](../../../docs/governance/development-rules.md#роли-этапов-и-непрерывность-поручения). A user instruction to
start a Task authorizes its executor and reviewer to continue ordinary execution, review and remediation through AI
poise gates without a new user command for each stage or review. Honor an explicit stage-only assignment or other user
limit. Continue stages of your own role; at a role boundary follow the handoff procedure below. Do not bypass gates or
act as your own independent reviewer. Stop for a real blocker, a new required decision, or separately controlled
acceptance/publication/integration.

Use public `advance` with a stable `request_id` and exact Task ID. Supply an exact
`target_stage` to stop on entry, or omit it for maximum progression. For ordinary new work, replay that exact request after completing and verifying each current-role
stage, after a lawful correction or restart, and after the receiving session bootstraps a public handoff. Treat
`progression_work_required`, `role_handoff_required` and `user_acceptance_required` as pauses, not success. At a role
boundary, include the active progression identity in the direct handoff message. Never change its target under the same
request ID or use it to cross gates, perform stage work, send the handoff message, record user acceptance, or infer
publication authority. A publish stage opens only through the separately authorized public acceptance path.

Poise isolates the temporary Git index for every repository snapshot invocation and cleans only that invocation's owned
directory. Never delete `snapshot.index.lock` manually or remove a sibling snapshot directory: preserve a genuine Git
conflict diagnostic and let the owning lifecycle recover it. See the canonical [snapshot index
contract](../../../docs/workflows/batch-work.md#изоляция-временного-git-index).

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks
and the maintained bounded `tests/smoke.sh`; never register unfiltered repository-wide test discovery as a task method.

Before project setup, changing `git.base_ref`, or integration, read the canonical
[target branch selection](../../../docs/configuration/project-setup.md#выбор-ветки-основного-checkout-для-интеграции).
For final result integration, keep the accepted commit immutable. The public finisher advances the existing task branch
in its existing task worktree; all updates from the current configured target branch, merges, conflict resolution, and checks happen there,
with no separate integration branch or worktree. If it returns a conflict, pause automation for agent resolution in that
task worktree, then resume checks. The finisher rechecks the configured target under the shared target lock and repeats update,
resolution, and checks on drift. It publishes only through `git merge --ff-only <task-branch>` in the main checkout and
never updates the target ref directly or by force.

Public `integrate` requires separate explicit `authorization` and full `commit_message`.
Keep the original permission and message unchanged across retries, conflicts, target drift,
and crash resume. The configured `git.commit_pattern` matches the complete message before
Git or saved-run loading; do not substitute permission, defaults, or a shortened subject.
The shared candidate-commit owner preserves the full message with Git's documented final-LF
framing. Authors separately check product meaning and 50/70 writing rules; these have no
new automatic validator. Follow the exact [message contract and packet
examples](../../../docs/workflows/batch-work.md#разрешение-и-сообщение-интеграционного-коммита).
Retained integration history without a recorded message cannot automatically continue,
even if a new request supplies one. Preserve history and recovery data; do not backfill
from authorization, edit managed state, or change request identity to bypass refusal.
Task inspection remains available but does not expose the full saved integration packet;
the integration projection also requires a recorded message. Follow [missing-message recovery
limits](../../../docs/workflows/batch-work.md#сохранённый-запрос-без-сообщения-коммита).

Before the first product write, distinguish a real Task from an explicitly authorized direct correction without one.
The public `integrate` operation requires a completed Task and saved final commit; it cannot accept a taskless candidate
retroactively. A reviewed taskless correction with explicit user authority may use the separate manual local delivery
route, preserving exact commit/check identity, checkpoint, target and foreign WIP. Follow the canonical
[route decision](../../../docs/governance/development-rules.md#прямое-исправление-без-task-выбор-и-поставка) and
[pre-ready procedure](../../../docs/workflows/pilot-task-preflight.md#выбор-маршрута-до-первой-записи). Never manufacture
Task stages, acceptance or receipts for earlier taskless work.

Never execute `git push`. This prohibition is absolute for every agent, AI poise publication handler, Git adapter, and
configured verification, action, tokenizer, or capability-probe runner; user publication authority does not waive it.
Reject `push_required=true` before any Git command, keep `push_required=false` remote-free, and direct completed Task
results to the public local `integrate` operation and its exact `git merge --ff-only` publication.

Never use the main checkout or foreign WIP for preparation or conflict resolution, and never `stash`, `reset`,
`restore`, `checkout`, `clean`, stage, commit, or delete their state. A dirty or unfinished main checkout may block
`merge --ff-only`; require recorded before/after proof that its full state is unchanged. After confirmed publication,
the same lifecycle removes only the task worktree, task branch, and registered task-scoped temporary backups. Preserve
pre-existing, foreign, durable operator, deliverable, and unfinished-recovery backups. Resume persisted incomplete
phases idempotently after a crash, using current installed AI poise source. Read the canonical [finish and integration
rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

## Sprint work

Plan only one connected dependency chain per Sprint. Keep independent Tasks standalone;
neither a shared topic nor execution priority creates a dependency. When splitting a long
Sprint, copy required prerequisite Tasks or chains as local conditional duplicates with new
IDs and local edges. Do not search similar Tasks or manually maintain a reciprocal family
list. Task has two axes: ordinary/duplicate, then parent/child for a duplicate. A root
with children is itself a parent duplicate. One shared library method resolves the original
parent, lists all children, then batch-reads the parent's and all children's stages, processes,
statuses and session owners in a consistent DB read. Only parent resolution differs by role.
Context and stage admission reuse this method; no per-task lookup loop or copied traversal.
The harness derives reciprocal family views from a canonical parent relation.
For each noncancelled member, d is the shortest directed transition count from its current
stage to positive termination, computed equivalently by reverse traversal from positive
terminals. Success is d=0; cancellation is not success. Rework changes current distance;
stage names, array indexes, branch position and past maximum progress do not rank Tasks.
Include the current Task in d_min. Ordinary repeated implementation MUST be rejected when d(current)>d_min,
even by one transition. Equality is necessary but not sufficient: when two or more nearest
Tasks have sessions, refuse start until agents agree one executor and that executor explicitly
supplies force_duplicate_start=true for this request. Count the requesting session prospectively
for an unclaimed current Task, without first persisting its claim; do not double-count on resume.
The optional bootstrap/advance flag clears only this tied-session refusal, never distance, ownership or stage guards.
Return only all other relatives at d_min, with Task/Sprint IDs,
stage, distance, status, iteration and owning session IDs. Do not include intermediate
leaders or tied-but-lagging peers. Equal distances on different stages are ties too.
Multiple sessions at equal minimum distance require agreement and the flag even on initial
stages; unowned initial equality alone is not a collision. A lagging initial stage is blocked.
Every request, including a flagged retry, rereads the family; never auto-add or persist the flag. Check on
acquisition/resume and every
actual stage start before execution effects; a context warning is not enforcement.
Invalid or unavailable distance is not permission. Do not seize foreign ownership or WIP.
Completed family results still require main-base integration and local availability;
reuse must not bypass a denied ordinary-stage start or forge terminal progress/acceptance.
Use work/reuse with explicit task_id, source_task_id, request_id and current expected_version for the first request. It
verifies the matching accepted family contract, base_ref integration and own local registered checks; only a separately
authorized accept completes it. Replay the original packet, including its original expected_version, without inventing a
stage result. Before acceptance replay validates local inputs; after completion it returns the historical accepted
receipt without reacquiring a Task/worktree. Native handoff resume repeats reuse and validates the preserved bundle.
Reuse automatically delivers the accepted permanent Task/Sprint artifact set into local owner roots. It preserves bytes,
assigns local identities, checks the active delivery obligations and refuses conflicting paths. Replay preserves identical
files; accept rechecks delivery. Do not copy files/receipts manually or treat foreign runtime-session artifacts as permanent
results. Optional empty artifact sets are valid only when their actual counts and delivery obligations allow them.
See [automatic family check](../../../docs/workflows/sprints.md#задача-спринта-дубль-автоматическая-проверка-семейства).
Create a duplicate with work/task action duplicate: request_id, new task_id, parent_id,
and an existing destination draft sprint_id. It starts as an unowned newborn and follows
ordinary ready/publication. The shared reader and start gate are implemented. Do not
invent fields, automatically set force_duplicate_start or perform manual similarity searches.
Follow the existing
[dependency closure and local
duplicates](../../../docs/workflows/sprints.md#замкнутость-зависимостей-и-локальные-дубли).
Do not claim graph connectivity is automatically enforced merely because DAG validation exists.

Bootstrap the sprint to get the eligible set instead of calculating dependencies manually. `verified` is not
`completed`; predecessor completion follows the task's acceptance policy. Read [Sprint API → Work
selection](../../../docs/workflows/sprints.md#выбор-работы-и-один-пакет-контекста) and [Sprint API → Dependency
kinds](../../../docs/workflows/sprints.md#два-явных-вида-зависимостей).

Treat result dependencies as readiness plus `result_provenance`, not Git ancestry. Every new successor worktree starts
from the current configured base ref; AI poise does not use or merge predecessor result commits as its branch base.

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

Treat configured inventory and actually probed capability as different facts. Use only capabilities reported for the
current project/worktree. For the current probe semantics, read [Runtime hooks → Capability
checks](../../../docs/configuration/runtime-hooks.md#проверки-capabilities).

## Task DB backups

Discover the public backup surface with `poise backup help`. Use `poise backup list --config PROJECT_JSON`, `poise
backup create --config PROJECT_JSON`, and `poise backup restore --config PROJECT_JSON BACKUP_NAME`; do not substitute
manual filesystem copies or database replacement. Create and restore require an exclusive operator window in which no
agent or process writes the Task DB. Read the exact [exclusive operator
window](../../../docs/task-db-backups.md#обязательное-условие) and [Task DB restore
contract](../../../docs/task-db-backups.md#восстановление). Task-only backup is not a full checkpoint: preserve the
other databases, Git and materials under the [full backup
boundary](../../../docs/architecture/storage-lifecycle.md#резервное-копирование-и-восстановление).

## Handoff and transfer

User Task start authorizes routine reviewer work as well as execution; no separate command is needed for each review. At
every executor/reviewer boundary, the sender preserves the result or findings and required evidence, uses one public
`handoff` package, verifies that ownership was released, and stops modifying the Task. The receiver claims/resumes it
through public bootstrap before mutation. A chat message is not release; report a failed handoff as failed. Read [Local
handoff → Role transfer](../../../docs/workflows/local-handoff.md#передача-между-исполнителем-и-ревьюером).

When the Task's counterpart executor/reviewer is known, apply [Direct role
handoff](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим) in both directions:

1. Save the result, evidence and findings, complete your required checks, and confirm that public `handoff` actually
   released the Task.
2. Stop Task/worktree changes, then use the available direct-message tool to notify that known counterpart. Include
   project/Task ID, stage and receiving role, result/findings and handoff receipt links, next action and any limits of
   the user's Task-start instruction. Do not ask the user to relay the message or authorize each ordinary review.
3. After successful notification, check your own pending assignments and immediately continue actionable work or the
   authorized stage/Task chain in the same turn. Keep received assignments in the working plan until completed,
   cancelled or explicitly transferred; preserve them and their blockers across checkpoints and compaction.
   Acknowledging a message does not discharge it. New messages add work unless they explicitly change the assignment. Do
   not poll the counterpart's status, read its conversation for progress or schedule background monitoring.
4. The recipient must acquire through `bootstrap` and check current state before working. A message neither transfers
   ownership nor substitutes for a review decision. Keep messages actionable, not repetitive status chatter.

If the recipient or messaging tool is unavailable, report the saved handoff to the user; do not contact unrelated
agents. If handoff fails, do not announce a completed transfer. If notification fails after release, retry the
notification against the same saved result or report the blocker; do not silently reclaim or repeat the work. Repeated
messages require checking current Poise state, not replaying a finished stage. Stop for an AI poise runtime blocker or a
decision outside the existing authorization, not for a routine executor/reviewer boundary.

For multiple incoming review handoffs, follow the linked direct-handoff rule: if idle, acquire and start immediately;
otherwise finish the review already started, save, release and notify, then take the next already-received actionable
Task in the same turn. A new handoff does not interrupt the current review or claim a second Task. For example, after
reviewing 0082, start pending 0076 immediately rather than ending with "0076 is next".

Before a final answer, account for every received assignment and continue any authorized actionable work. End the turn
only when none remains; preserve the next action and concrete blocker for each pending assignment. A blocker on one Task
does not stop other assigned work when ownership permits switching. Complete your own already-started operations. Never
rely on another message to restart work that has already been assigned; duplicate notifications do not require repeating
completed work.

For another store/environment, use `transfer`; do not merge task databases manually. Read [Transfer → Batch
API](../../../docs/workflows/transfer.md#пакетный-api).

## Observed usage

Record only real observed user-message/token events and preserve source/coverage. Do not estimate unavailable usage. For
metric semantics, read [Accounting → Observed
telemetry](../../../docs/operations/accounting.md#пакет-наблюдённой-телеметрии).

## Documentation references

When an operational rule is owned by canonical documentation, link to and read the smallest normative section that is
sufficient for that rule. Do not require an agent to load an entire large document when one section is authoritative.

## Route semantics

Route definitions contain an explicit `entry`; stage outcomes, targets and rework targets own transition semantics.
Preserve visits and transitions only for history, identity and audit. Never impose an execution limit through route
counts, depth, watchdogs, timeouts or recursion bounds. A finite graph-reachability check is structural validation, not
an execution budget.

Do not request rework while a pending resolution still requires independent inspection. Follow the exact inspection
stage reported by Poise, decide every pending resolution there, and only then retry an authorized rework target. The
rejection is state-preserving; do not create a workaround Task or edit the Task DB. Use `recover_empty_rework` only for
a legacy task already stranded by the former defect. Recovery requires an unowned Task and an ownership-only event
suffix, not a synthetic handoff. A cleaned worktree is recoverable only when Poise proves the saved commit is integrated
into the configured base and has the exact verified tree; never recreate that worktree manually. Read [Batch work →
rework with pending resolutions](../../../docs/workflows/batch-work.md#rework-при-нерассмотренных-исправлениях) (Task
0063 RD-013) and [empty rework
recovery](../../../docs/workflows/batch-work.md#восстановление-ошибочно-открытой-пустой-rework-итерации) (Task 0063
RD-008).


## Inputs for migrated skills

The project-local `.agents/skills/` tree supplies discoverable instruction files, not a second managed routing policy.
Use the selected Poise project and actual Task/stage inputs. Read skill classes and area declarations from the project's
existing `task_decomposition`; resolve other project settings through their current owner. Target-worktree files remain
read-only sources of facts for this configuration boundary.

## Procedure for migrated skill selection

For current Task discipline load [exec-task](../exec-task/SKILL.md); for formulating one Task load
[task-design](../task-design/SKILL.md); for Sprint graphs load
[sprint-design](../sprint-design/SKILL.md). Keep the exact packets, launcher, lifecycle and evidence semantics in this
skill and its canonical documentation. Load other catalogue skills only when relevant to the current stage and real
project stack, not as a blanket catalogue read.

The current decomposition validator checks declarations; it does not by itself return a complete ordered area/stage
skill-and-input packet. Do not label instruction discovery as implemented automatic routing, synthesize undocumented
profile fields, or place an AI poise config in the target repository. An absent required routing/aggregation capability
is a concrete dependency for the owning Poise work, not a reason to invent a parallel router.

## Output boundary for migrated skills

Persist the current skill's result through the existing Task/content/evidence/artifact owners and release via public
handoff. Saving remains distinct from acceptance. Universal phase recovery remains capability-dependent. The implemented
[cross-project next overview](../../../docs/configuration/project-setup.md#доступные-задачи-во-всех-проектах) is
read-only: it neither reserves nor starts a Task. Use the normal owning bootstrap/start path for an already-authorized
selection, with current-state validation.

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

### Editable acceptance drafts

Classify an acceptance file before its first registration. Keep ordinary registered artifacts immutable. If review and
rework must edit the same working path, declare that task-scoped path through `artifact_drafts` before registration,
submit every authorized same-path revision explicitly, and release the Task before taskless review or finalization.
Each review preserves an independently registered immutable byte snapshot. If a released nonterminal Task already froze
the path by mistake, use only explicitly authorized `artifact_drafts/recover` with the exact ID, scope, path and current
version; do not create a renamed version as a workaround. Follow the
[public draft lifecycle](../../../docs/workflows/batch-work.md#редактируемые-приёмочные-черновики).

### Ambiguous legacy ownership

Use `show` with `kind: ownership_conflicts` and then the explicitly authorized
`recover_ownership` decision for one exact connected component. Copy its actual
snapshot and Task/session identities. Keep existing values or release to null;
never infer Task ownership from a worktree binding, invent a claimant, or select
between live/uncertain owners. In this legacy reconciliation mode only authoritative DEAD permits foreign release;
explicit self-release is supported. Unrelated Tasks remain usable while v12
uniqueness installation is pending. Repair never mutates Git/WIP and exact replay
never repeats the mutation. Read the [public recovery
contract](../../../docs/workflows/batch-work.md#восстановление-неоднозначного-владения).

A Git `publish` stage with `push_required=false` records local-only publication
after accepted clear inspection. Submit the exact target ref, expected local
commit and explicit authorization. No remote is required or contacted; the
receipt does not move the target and is not integration. Resume the same intent
or use lawful rework when blocked; see [local
publication](../../../docs/workflows/batch-work.md#локальная-фиксация-публикации).


Native caller isolation: a session launcher is not caller identity. Use only the launcher
for the host-observed CODEX_THREAD_ID/CODEX_SESSION_ID; both must agree when present.
Never set these values to impersonate a stored binding or combine native identity with
POISE_CALLER_BINDING. Configured agent_id is a profile, not a separate actor. A child with
parent-only signals must not mutate the parent's Task. The sender uses public handoff;
a genuinely separate native session obtains its own SessionStart launcher and bootstraps
the exact Task. Preserve WIP and diagnose missing/conflicting signals instead of renaming
roles, overriding native identity or editing Task DB to get past the guard.

## Requirements provenance

Use the project Requirements batch API and show the full System → Application → Task
texts and statuses before agreement. Never infer acceptance from a ready plan or IDs.
Publish the exact agreed snapshot through the existing Task/Sprint owner; preserve
historical snapshots on replay/restart. Follow [agreement and
publication](../../../docs/workflows/requirements-registry.md#декларативный-api-и-согласование)
and [immutable history](../../../docs/workflows/requirements-registry.md#публикация-и-исторический-снимок).

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

A newborn Task has no route entry before goal_type selection; selecting the type
materializes its configured draft, without starting execution before ready.

## Terminal inspection

Select a completed or cancelled Task by its canonical ID to obtain the
terminal inspection snapshot. The result is read_only_verified where applicable;
it does not reclaim the task, restore its worktree or repeat acceptance.
Use the returned persisted results for read-only history, not the released
current-session binding.

## Accepted-stage replay after restart

Commit all owned WIP in the Task worktree before automatic replay. Replay uses
the same worktree and branch, preserves a durable recovery ref before reset,
and rechecks accepted stages at their exact accepted commits. Passing historical
tests and unchanged registered proof files suffice when current proof obligations
remain compatible. Restart or requirement wording alone does not invalidate proof.
Never substitute old methods, expectations, schedules or evidence plans for an
explicitly changed current contract. Revalidate ignored collisions and preservation
before every destructive checkout, including resume. Mechanical replay is role-neutral; new substantive work retains
independent review. Omit the target for maximum progress; an explicit target stops
on entry before its checks. Never rerun an unknown command outcome. A completed
request replays its saved result; use a new identity for new progression.
Follow the [canonical replay
contract](../../../docs/workflows/batch-work.md#автоматическая-промотка-после-перезапуска).
