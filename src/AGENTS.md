# AI poise source development

Updated: 2026-09-13.

## Declarative, reusable tools

Whenever a required workflow contains two or more mechanical actions with no reasoning decision between them, provide one tool that performs the sequence.

Make every tool batch-oriented and declarative. The agent describes the desired result and related changes in one logical request; the tool ensures that the result is applied through the owning APIs. Do not require one call per item or manual supervision of bookkeeping.

Do not duplicate existing tools. Compose their calls or simplify their inputs and result representations. Keep native IDE, Git and coding capabilities where they already provide the needed operation.

## Domain ownership

Keep one Task and one worktree independently claimable per session. Follow the canonical
[ownership rule](../docs/governance/development-rules.md#владение-task-и-worktree).
Reuse OwnershipCommands and its transactional repository for complete-set acquisition,
same-kind replacement and release; never steal an uncertain live claim. Derive the
dependent worktree from the explicit process snapshot, not a caller flag. Release it
with its Task while preserving independent worktree ownership. Ownership must not
change cwd, launch roots, Git or WIP, and must not absorb cleanup or result integration.
Treat restart-ready promotion as the same release boundary: promoting a restarted
standalone or published Sprint Task to available clears the releasing actor's
dependency-bound worktree in the same unit of work, without changing Git or WIP.

Apply domain-driven design throughout the codebase, not just to stage handlers. Identify the owner of each business rule before adding behaviour.

Document and respect the responsibility boundaries of libraries and data owners. Change Task, Sprint, content, evidence, configuration and artifact state only through their owning APIs. CLI adapters, runners and hooks must not bypass those APIs with direct lifecycle assignments or table updates.

Model early planning as a real newborn Task with permanent identity and history. Route its
create/edit/ready transitions through the shared ownership API, preserve no route entry before goal_type selection,
and apply type-specific readiness before `available`. Sprint
`materialize_tasks` owns conversion of legacy embedded definitions into real newborn Task
members while preserving partial edits, history, membership, graph aliases, immutable request
replay, and source Task traceability. Keep direct complete creation supported; do not add a
hidden broad migration.

Keep broken execution-contract recovery in the Task owner. Report an unattainable DoD or a
failed next-stage DoR as `broken`; let a reviewer revise only the defective inspection-stage
contract, or use the public Task `restart` action to return the same unfinished, unintegrated
Task to newborn. Preserve its ID, append-only history, Sprint membership, execution workspace,
branch, base and WIP. Reject foreign ownership, stale versions, terminal Tasks and unresolved
external outcomes before mutation. Sprint `replace_task` is not a correction writer; retain
historical relations only as opaque revision/audit records, never a current replacement projection.

For every new trace requirement, ensure that its due stages intersect the referenced
point's `write_stages`. A `phase=pre` requirement additionally needs a declared write
stage strictly before its earliest required stage; `phase=post` may use the same stage.
Apply this one rule to direct Task creation, newborn ready, Sprint publication, new
goal-type candidates, and new active-Task additions before persistence or external
effects. Restore stored contracts without retroactive rejection or rewriting, but
validate every genuinely new requirement added to them. Keep immutable early evidence
writable only at its owning stage and expose it through a post-gate there before later
pre-gates consume it.

Keep domain code independent of I/O. Application services coordinate domain objects and ports; infrastructure implements those ports. Reuse transaction, execution and presentation mechanics without creating a universal raw-data editor.

`ResultIntegration` keeps the accepted commit immutable and advances only the existing task branch in its existing task worktree. Run update, conflict resolution, and checks there; do not create an integration branch or worktree. Serialize publication per target, reread it under that lock, repeat update and checks on drift, and publish only through `git merge --ff-only <task-branch>` in the main checkout. On a blocked fast-forward, record proof that the checkout state is unchanged. Poise-owned executables, hooks and configuration keep their existing path resolution. Development or verification operations that act on another checkout receive its concrete filesystem path/cwd explicitly; see [path resolution and working copies](../docs/migrations/erp-runtime-migration-retrospective-2026-09-15.md#разрешение-путей-и-рабочие-копии). Persist replay phases and limit cleanup to the task worktree, task branch, and scoped temporary backups.

Stage progression belongs to Tasks: keep the durable target and replay identity in the Task journal, derive role boundaries from route handlers, and perform every move through the existing Task transition. Application services may coordinate progression, entry-gate preflight and confirmed handoff resumption; transport only parses and presents it. Never add a second lifecycle, hidden autoaccept, stage-work execution or messaging to progression.

Reuse the standard stage handlers and the common route runner across workflows. A new goal type defines its own process; it does not require a new execution engine.

## Explicit configuration

Do not embed literals that determine the workflow, task format, acceptance conditions or observable result in application logic. Supply those choices through explicit configuration. Internal implementation constants may describe mechanisms, but must not silently select business behaviour.

AI poise must be configurable without editing its source code. Missing required configuration is an error: do not supply hidden defaults, fallback values or guessed settings.

Route every public work composition through `SessionEstablisher`. Prefer the native Codex
identity; otherwise require an explicit persistent `POISE_CALLER_BINDING`. An agent must not
substitute a caller-chosen session value. Persist identity initialization without creating a
Task/Sprint for read-only work, and never derive workflow authority from identity origin.

Do not preserve backward compatibility merely to read earlier formats. Do not design or run data migrations without a direct user instruction; request permission when a migration is necessary.

For the authorized legacy Task process repair, follow
[`docs/task-process-snapshot-migration.md`](../docs/task-process-snapshot-migration.md).
Do not repair stored process snapshots through direct SQL; use the public migration and
its named public backup, atomic validation, audit receipt, and exact replay contract.
A stable existing claim does not block that authorized migration and must remain unchanged;
any claim creation, release, or replacement after the named backup or preflight is drift
that rejects the complete batch.
The separately authorized `task-process-migration-2` accepts exactly Task `0082`; it does
not widen schema 1 and its issued receipt remains exactly replayable. Corrective
`task-process-migration-3` has the same exact Task scope. It preserves an already migrated
matching `worktree_required`, or adds the missing explicit value, and initializes exact stage
contracts from that preserved process and its content requirements. Apply schema 3 metadata
changes without changing Task version, lifecycle, released handoff, result, evidence, ownership,
or Git state, then require a native reviewer bootstrap of the preserved verified Task.
Recover an accidentally cleaned nonterminal verified/accepted worktree only through the
installation-owned `recover_missing_worktree` operation. Require the saved report commit to be
integrated into the configured base with the exact verified tree; restore only the saved valid
branch and configured Task path registered to that repository. Reject independent repositories
at the same path. Roll back only Git resources created by the failed invocation, and never change
Task ownership or lifecycle in that recovery.

Each goal type has its own complete, self-contained process configuration. Define its task template and rules for creation, stages, checks and completion. Reusing library code does not imply inheritance between goal-type business configurations.

Keep all AI poise configuration in the AI poise codebase and select the project explicitly. Do not infer executable commands from a target application's prose instructions; register the exact invocation in the task or project configuration.

## Development workflow

Follow the Task process' worktree requirement before modifying code. A required worktree belongs to the repository/codebase being changed; a Task that does not require one uses the configured repository checkout. See [worktree placement](../docs/governance/development-rules.md#размещение-task-worktree). Do not complicate read-only inspection with worktree creation.

Follow the [TDD rules](../docs/governance/development-rules.md), [library boundaries](../docs/architecture/boundaries.md) and [declarative tool contract](../docs/architecture/declarative-tools.md). Write and inspect tests before implementation, verify the completed path, review fixes, and update tool, code and storage documentation with a timestamp.

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks and the maintained bounded `tests/smoke.sh`; do not register unfiltered repository-wide test discovery as a task method.

Every new Task worktree starts from the current configured base ref observed for that start. Sprint result dependencies expose predecessor commits through `result_provenance`; they do not select or merge a successor branch base.

Every new verification method must declare `source_under_test`. Bind repository sources to explicit paths inside the checkout selected for the current Task. A
Task without a dedicated worktree may bind the configured repository checkout; never infer a
language layout or overwrite an existing environment value. External methods require an
explicit reason.
Verification methods never declare execution timeouts; after launch, wait for the process to
reach a terminal result. Capability probes, hooks and other bounded infrastructure retain their
own explicit timeout contracts.
Reject semantic duplicates and conflicting methods before execution. Accept RED evidence only
when the declared failure predicate matches and the recorded source provenance is valid; an
import error or execution against another checkout is not a valid RED.
Keep `verification_plan` validation in `CheckRegistry` and route scope ownership
in `RouteDefinition`. New methods declare `change_surface`, `red_stages`, and
`green_stages`; every required GREEN path must be covered by configured
`allowed_paths`. Allow an empty repository surface only for a pre-existing baseline
guard with no RED and sole GREEN at the route-entry `baseline`; produced-result GREEN
methods require a non-empty covered surface. RED predicates compare complete output exactly, so additional
failures do not pass. Preserve pre-plan snapshot restoration without adding a
default, migration, or recursive runner-specific source inference.
Keep current-registry mutation, revision/idempotency guards, immutable snapshots,
and executable-obligation validation in `CheckRegistry`. Task creation requires explicit
`executable_obligations` when the process schema owns `test_registry`; a schema without
that field must omit it and initializes an explicitly empty registry classification.
Every stage-authorized `test_registry` mutation supplies the full explicit list; never
infer it from all requirements/DoD. Restore the current classification only
from validated registry state, without rewriting historical methods or receipts and
without a migration or fallback to immutable creation metadata.

For AI-poise's own changed-input test selection, use the existing package owner and
single router; preserve Task-assigned methods, report unmapped inputs, and never fall
back to unfiltered discovery. Read [package input impact](../docs/configuration/development-routing.md#выбор-проверок-по-входам-тестовых-пакетов).

For AI-poise's authoring boundary gate, follow [pre-submission architecture checks](../docs/workflows/architecture-boundaries.md#проверка-архитектуры-перед-авторской-сдачей).
Use the configured running tool to check concrete changed paths. Do not execute subject
code in a static check, replace assigned Task methods, or mutate Task state after a failed gate.


## Registered artifact root recovery

Updated: 2026-09-17. Use public `recover_artifacts` only for an explicitly
user-authorized root migration, from a taskless caller for one released Task.
Supply its observed version, registered artifact IDs and exact old task/sprint
owner roots. The existing artifact registry and FileArtifactFactory validate
scope/owner/relative-path identity, source digest, regular files, symlink-free
paths and non-overwriting destinations derived from current configuration.
Shared artifacts require all referencing Tasks to be released. Publish all
immutable files before atomically rebinding selected registry paths and recording
an audited receipt. Never rewrite Task lifecycle, historical submissions or
handoffs, delete legacy sources, weaken ordinary validation, or invent missing
bundle bytes. Failed DB/file publication may leave identical immutable files for
safe retry; exact receipt replay is historical, not fresh material validation.
Read [registered artifact recovery](../docs/workflows/batch-work.md#восстановление-зарегистрированных-артефактов).

## Atomic verified delivery

The existing TaskCommands final verification UnitOfWork owns Task/result/proof,
execution report, permanent artifact links, stage.verified journal and original
full work-packet identity together. Do not call a late receipt writer after
verification commits. Runtime callers pass packet_digest explicitly; null is
only for read-only retrieval, never identity repair. Keep external checks, Git
commits and file preparation outside this transaction; preserve preparatory
observations for an exact retry after rollback. Never claim filesystem/Git and
SQLite are one atomic transaction. Read the [delivery contract](../docs/workflows/batch-work.md#атомарная-доставка-verified-результата).

## Pending legacy ownership migration

Keep ambiguous v12 Task/worktree components unchanged until an explicit public
`recover_ownership` decision passes exact snapshot, scope and liveness guards.
Do not globally reject unrelated work or infer Task ownership from a worktree.
Use the existing OwnershipCommands/SQLite UoW and Task repository; recovery must
not mutate Git/WIP. Read the [canonical public contract](../docs/workflows/batch-work.md#восстановление-неоднозначного-владения).

Newborn inspection and handoff must branch before reading an execution contract or
stage, even after a process has been selected. Use the same handoff journal and
ownership transaction to save/release and resume drafts. Keep staged candidates out
of newborn handoff, preserve opaque metadata on ownership-only changes, and validate
saved WIP/bundle before reacquiring a released draft. The registry projection must
label draft methods and historical state as inactive; never synthesize readiness.
See [newborn handoff](../docs/workflows/local-handoff.md#сохранение-незавершённого-newborn).

Verification must validate the full registered artifact set, not only submitted
paths, before materializing files or saving a submission. Use the same artifact
inspection owner in the public and direct runtime paths; reject stale roots,
identity/owner/scope mismatches, symlinks and digest failures without semantic Task
writes. Never silently skip an unknown registered scope. Keep post-execution
validation: preflight is not a filesystem/SQLite transaction. See the
[artifact preflight contract](../docs/workflows/batch-work.md#предварительная-проверка-зарегистрированных-артефактов).
