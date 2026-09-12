---
name: poise-workflow
description: Execute project-local AI poise task stages and hand off work between executor and reviewer through declarative batch tools.
---

# AI poise task workflow

Use the explicitly selected project configuration. Each project owns its Task DB and its copied process catalogue; never edit managed SQLite, process JSON, or task artifacts as bookkeeping.

For WSL project selection and storage layout, read only [Local installation → Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Local installation → Mutable storage paths](../../../docs/configuration/wsl-local-delivery.md#пути-project-local-storage).

For this local ai-poise installation, use [the concrete launcher and configuration](../../../docs/configuration/project-setup.md#локальный-проект-ai-poise). `bootstrap` and `verify` are operations of AI poise `work`, not separate skills. For ordinary work invoke the session-scoped `work.sh` supplied by the native hook explicitly through Bash, keep `messages=[]`, and reuse the same launcher across calls. Direct `.venv/bin/poise work` with an operator-provided `POISE_SESSION` is diagnostic only and does not establish native event delivery. The user explicitly authorized state inside this repository.

## Start or resume work

Use one `bootstrap` package to obtain the current task/sprint, stage, process snapshot, required content, worktree, findings/evidence and available capabilities. For the WSL invocation, read [Local installation → Start work](../../../docs/configuration/wsl-local-delivery.md#начало-работы-над-задачей).

When `bootstrap` explicitly addresses a `completed`, `cancelled`, or `superseded` Task, consume the returned `terminal inspection snapshot` with its preserved context, content, evidence, and history. Do not expect or create a current-task binding, and do not issue a follow-up `show` to recover terminal data. A cancelled Task may legitimately have no evidence. After inspection, taskless bootstrap must return `read_only`; only then may null-result verify return `read_only_verified`.

Do not create a worktree for read-only queries. For repository-changing work, use the worktree supplied by AI poise and read the target repository's applicable `AGENTS.md` before edits.

## Complete the current stage

Submit the stage result and related sections/findings/evidence/artifacts in one logical package. Use `verify`; do not hand-edit intermediate result files or the database. Read [Batch work → Verify](../../../docs/workflows/batch-work.md#verify) and, when files are required, [Batch work → File creation](../../../docs/workflows/batch-work.md#создание-файлов).

Use native coding/IDE tools for source changes. AI poise owns task state, evidence registration, execution receipts, Git lifecycle boundaries and managed artifacts. A user instruction to start a Task authorizes its executor and reviewer to continue ordinary execution, review and remediation through the harness without a new user command for each stage or review. Honor an explicit stage-only assignment or other user limit. Continue stages of your own role; at a role boundary follow the handoff procedure below. Do not bypass gates or act as your own independent reviewer.

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

For another agent in the same store, use one `handoff` package; never manually commit/copy/release task state. Read [Local handoff → Package](../../../docs/workflows/local-handoff.md#пакет).

When the Task's counterpart executor/reviewer is known, apply [Direct role handoff](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим) in both directions:

1. Save the result, evidence and findings, complete your required checks, and confirm that public `handoff` actually released the Task.
2. Stop Task/worktree changes, then use the available direct-message tool to notify that known counterpart. Include project/Task ID, stage and receiving role, result/findings and handoff receipt links, next action and any limits of the user's Task-start instruction. Do not ask the user to relay the message or authorize each ordinary review.
3. The recipient must acquire through `bootstrap` and check current state before working. A message neither transfers ownership nor substitutes for a review decision. Keep messages actionable, not repetitive status chatter.

If the recipient or messaging tool is unavailable, report the saved handoff to the user; do not contact unrelated agents. If handoff fails, do not announce a completed transfer. If notification fails after release, retry the notification against the same saved result or report the blocker; do not silently reclaim or repeat the work. Repeated messages require checking current Poise state, not replaying a finished stage. Stop for a harness blocker or a decision outside the existing authorization, not for a routine executor/reviewer boundary.

For another store/environment, use `transfer`; do not merge task databases manually. Read [Transfer → Batch API](../../../docs/workflows/transfer.md#пакетный-api).

## Observed usage

Record only real observed user-message/token events and preserve source/coverage. Do not estimate unavailable usage. For metric semantics, read [Accounting → Observed telemetry](../../../docs/operations/accounting.md#пакет-наблюдённой-телеметрии).

## Documentation references

When an operational rule is owned by canonical documentation, link to and read the smallest normative section that is sufficient for that rule. Do not require an agent to load an entire large document when one section is authoritative.

## Route semantics

Route definitions contain an explicit `entry`; stage outcomes, targets and rework targets own transition semantics. Preserve visits and transitions only for history, identity and audit. Never impose an execution limit through route counts, depth, watchdogs, timeouts or recursion bounds. A finite graph-reachability check is structural validation, not an execution budget.
