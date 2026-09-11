---
name: poise-workflow
description: Run one project-local task stage through AI poise declarative batch tools.
---

# AI poise task workflow

Use the explicitly selected project configuration. Each project owns its Task DB and its copied process catalogue; never edit managed SQLite, process JSON, or task artifacts as bookkeeping.

For WSL project selection and storage layout, read only [Local installation → Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Local installation → Mutable storage paths](../../../docs/configuration/wsl-local-delivery.md#пути-project-local-storage).

For this local ai-poise installation, use [the concrete launcher and configuration](../../../docs/configuration/project-setup.md#локальный-проект-ai-poise). `bootstrap` and `verify` are operations of AI poise `work`, not separate skills. For ordinary work invoke the session-scoped `work.sh` supplied by the native hook explicitly through Bash, keep `messages=[]`, and reuse the same launcher across calls. Direct `.venv/bin/poise work` with an operator-provided `POISE_SESSION` is diagnostic only and does not establish native event delivery. The user explicitly authorized state inside this repository.

## Start or resume work

Use one `bootstrap` package to obtain the current task/sprint, stage, process snapshot, required content, worktree, findings/evidence and available capabilities. For the WSL invocation, read [Local installation → Start work](../../../docs/configuration/wsl-local-delivery.md#начало-работы-над-задачей).

Do not create a worktree for read-only queries. For repository-changing work, use the worktree supplied by AI poise and read the target repository's applicable `AGENTS.md` before edits.

## Complete the current stage

Submit the stage result and related sections/findings/evidence/artifacts in one logical package. Use `verify`; do not hand-edit intermediate result files or the database. Read [Batch work → Verify](../../../docs/workflows/batch-work.md#verify) and, when files are required, [Batch work → File creation](../../../docs/workflows/batch-work.md#создание-файлов).

Use native coding/IDE tools for source changes. AI poise owns task state, evidence registration, execution receipts, Git lifecycle boundaries and managed artifacts. After a verified stage, report the result and stop unless the user has explicitly delegated multi-stage autonomous continuation.

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks and the maintained bounded `tests/smoke.sh`; never register unfiltered repository-wide test discovery as a task method.

For final result integration, keep the accepted commit immutable. Let the public finisher maintain a separate mutable integration head in a child task/integration worktree; all updates from current `master`, merges, and conflict resolution happen there. If it returns a conflict, pause automation for agent resolution in that worktree, then resume checks. The finisher must recheck `master` immediately before publication and automatically repeat update, resolution, and checks when it moved, until it can safely fast-forward `master` without force or manual supervision.

Never use the main checkout or foreign WIP as an integration surface or prerequisite, and never `stash`, `reset`, `restore`, `checkout`, `clean`, stage, commit, delete, or resolve conflicts there for completion. After confirmed publication, the same lifecycle removes task/integration worktrees, child branches, and registered task-scoped temporary backups. Such backups may exist only in a task/integration-scoped directory under the configured runtime root, which must be empty before success. Preserve pre-existing, foreign, durable operator, deliverable, and unfinished-recovery backups according to ownership and a separate terminal decision. Resume persisted incomplete phases idempotently after a crash. Read the canonical [finish and integration rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

## Sprint work

Bootstrap the sprint to get the eligible set instead of calculating dependencies manually. `verified` is not `completed`; predecessor completion follows the task's acceptance policy. Read [Sprint API → Work selection](../../../docs/workflows/sprints.md#выбор-работы-и-один-пакет-контекста) and [Sprint API → Dependency kinds](../../../docs/workflows/sprints.md#два-явных-вида-зависимостей).

Treat result dependencies as readiness plus `result_provenance`, not Git ancestry. Every new successor worktree starts from the current configured base ref; AI poise does not use or merge predecessor result commits as its branch base.

## Capabilities

Treat configured inventory and actually probed capability as different facts. Use only capabilities reported for the current project/worktree. For the current probe semantics, read [Runtime hooks → Capability checks](../../../docs/configuration/runtime-hooks.md#проверки-capabilities).

## Handoff and transfer

For another agent in the same store, use one `handoff` package; never manually commit/copy/release task state. Read [Local handoff → Package](../../../docs/workflows/local-handoff.md#пакет).

For another store/environment, use `transfer`; do not merge task databases manually. Read [Transfer → Batch API](../../../docs/workflows/transfer.md#пакетный-api).

## Observed usage

Record only real observed user-message/token events and preserve source/coverage. Do not estimate unavailable usage. For metric semantics, read [Accounting → Observed telemetry](../../../docs/operations/accounting.md#пакет-наблюдённой-телеметрии).

## Documentation references

When an operational rule is owned by canonical documentation, link to and read the smallest normative section that is sufficient for that rule. Do not require an agent to load an entire large document when one section is authoritative.
