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
historical replacement relations only for reading.

For every new trace requirement, ensure that its due stages intersect the referenced point's `write_stages`. Apply this rule to new goal-type and Task candidates, Sprint publication, and new active-Task additions before persistence or external effects. Restore stored contracts without retroactive rejection or rewriting, but validate every genuinely new requirement added to them. Keep immutable early evidence writable only at its owning stage; include that stage among the requirement's due stages instead of making the evidence writable later.

Keep domain code independent of I/O. Application services coordinate domain objects and ports; infrastructure implements those ports. Reuse transaction, execution and presentation mechanics without creating a universal raw-data editor.

`ResultIntegration` keeps the accepted commit immutable and advances only the existing task branch in its existing task worktree. Run update, conflict resolution, and checks there; do not create an integration branch or worktree. Serialize publication per target, reread it under that lock, repeat update and checks on drift, and publish only through `git merge --ff-only <task-branch>` in the main checkout. On a blocked fast-forward, record proof that the checkout state is unchanged. The hook route for `integrate` is installation-owned so the finisher runs current installed source. Persist replay phases and limit cleanup to the task worktree, task branch, and scoped temporary backups.

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
branch and configured Task path, and never change Task ownership or lifecycle in that recovery.

Each goal type has its own complete, self-contained process configuration. Define its task template and rules for creation, stages, checks and completion. Reusing library code does not imply inheritance between goal-type business configurations.

Keep all AI poise configuration in the AI poise codebase and select the project explicitly. Do not infer executable commands from a target application's prose instructions; register the exact invocation in the task or project configuration.

## Development workflow

Use a dedicated Git worktree and the repository branch-naming rule before modifying code. Do not complicate read-only inspection with worktree creation.

Follow the [TDD rules](../docs/governance/development-rules.md), [library boundaries](../docs/architecture/boundaries.md) and [declarative tool contract](../docs/architecture/declarative-tools.md). Write and inspect tests before implementation, verify the completed path, review fixes, and update tool, code and storage documentation with a timestamp.

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks and the maintained bounded `tests/smoke.sh`; do not register unfiltered repository-wide test discovery as a task method.

Every new Task worktree starts from the current configured base ref observed for that start. Sprint result dependencies expose predecessor commits through `result_provenance`; they do not select or merge a successor branch base.

Every new verification method must declare `source_under_test`. Bind repository sources to
paths inside the current task worktree; never infer a language layout, overwrite an existing
environment value, or add a fallback checkout. External methods require an explicit reason.
Verification methods never declare execution timeouts; after launch, wait for the process to
reach a terminal result. Capability probes, hooks and other bounded infrastructure retain their
own explicit timeout contracts.
Reject semantic duplicates and conflicting methods before execution. Accept RED evidence only
when the declared failure predicate matches and the recorded source provenance is valid; an
import error or execution against another checkout is not a valid RED.
Keep `verification_plan` validation in `CheckRegistry` and route scope ownership
in `RouteDefinition`. New methods declare `change_surface`, `red_stages`, and
`green_stages`; every required GREEN path must be covered by configured
`allowed_paths`. RED predicates compare complete output exactly, so additional
failures do not pass. Preserve pre-plan snapshot restoration without adding a
default, migration, or recursive runner-specific source inference.
Keep current-registry mutation, revision/idempotency guards, immutable snapshots,
and executable-obligation validation in `CheckRegistry`. Task creation and every
stage-authorized `test_registry` mutation require explicit `executable_obligations`;
never infer them from all requirements/DoD. Restore the current classification only
from validated registry state, without rewriting historical methods or receipts and
without a migration or fallback to immutable creation metadata.
