---
name: poise-workflow
description: Run one project-local task stage through AI poise declarative batch tools.
---

# AI poise task workflow

Use the explicitly selected project configuration. Each project owns its Task DB and its copied process catalogue; never edit managed SQLite, process JSON, or task artifacts as bookkeeping.

For WSL project selection and storage layout, read only [Local installation → Architecture](../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Local installation → Mutable storage paths](../../docs/configuration/wsl-local-delivery.md#пути-project-local-storage).

For this local ai-poise installation, use [the concrete CLI and configuration](../../docs/configuration/project-setup.md#локальный-проект-ai-poise). `bootstrap` and `verify` are operations of `.venv/bin/poise work`, not separate skills. Preserve `POISE_SESSION` across calls. The user explicitly authorized state inside this repository.

## Start or resume work

Use one `bootstrap` package to obtain the current task/sprint, stage, process snapshot, required content, worktree, findings/evidence and available capabilities. For the WSL invocation, read [Local installation → Start work](../../docs/configuration/wsl-local-delivery.md#начало-работы-над-задачей).

Do not create a worktree for read-only queries. For repository-changing work, use the worktree supplied by AI poise and read the target repository's applicable `AGENTS.md` before edits.

## Complete the current stage

Submit the stage result and related sections/findings/evidence/artifacts in one logical package. Use `verify`; do not hand-edit intermediate result files or the database. Read [Batch work → Verify](../../docs/workflows/batch-work.md#verify) and, when files are required, [Batch work → File creation](../../docs/workflows/batch-work.md#создание-файлов).

Use native coding/IDE tools for source changes. AI poise owns task state, evidence registration, execution receipts, Git lifecycle boundaries and managed artifacts. After a verified stage, report the result and stop unless the user has explicitly delegated multi-stage autonomous continuation.

## Sprint work

Bootstrap the sprint to get the eligible set instead of calculating dependencies manually. `verified` is not `completed`; predecessor completion follows the task's acceptance policy. Read [Sprint API → Work selection](../../docs/workflows/sprints.md#выбор-работы-и-один-пакет-контекста) and [Sprint API → Dependency kinds](../../docs/workflows/sprints.md#два-явных-вида-зависимостей).

## Capabilities

Treat configured inventory and actually probed capability as different facts. Use only capabilities reported for the current project/worktree. For the current probe semantics, read [Runtime hooks → Capability checks](../../docs/configuration/runtime-hooks.md#проверки-capabilities).

## Handoff and transfer

For another agent in the same store, use one `handoff` package; never manually commit/copy/release task state. Read [Local handoff → Package](../../docs/workflows/local-handoff.md#пакет).

For another store/environment, use `transfer`; do not merge task databases manually. Read [Transfer → Batch API](../../docs/workflows/transfer.md#пакетный-api).

## Observed usage

Record only real observed user-message/token events and preserve source/coverage. Do not estimate unavailable usage. For metric semantics, read [Accounting → Observed telemetry](../../docs/operations/accounting.md#пакет-наблюдённой-телеметрии).

## Documentation references

When an operational rule is owned by canonical documentation, link to and read the smallest normative section that is sufficient for that rule. Do not require an agent to load an entire large document when one section is authoritative.
