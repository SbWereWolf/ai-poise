---
name: poise-development
description: Change AI poise itself using its DDD, TDD, declarative-tool and project-local configuration rules.
---

# AI poise development

Read the repository `AGENTS.md` and `src/AGENTS.md` first. Work in a dedicated `tasks/<task-id>` worktree.

## Design boundaries

Identify the domain owner before implementation. Task, Sprint, content, evidence, artifacts, project configuration and runtime state change only through their owning APIs. Reuse the common runner and stage-handler families rather than creating a goal-specific engine. Read [Architecture boundaries → owners and dependencies](../../../docs/architecture/boundaries.md#ddd-04b--новые-владельцы-и-зависимости).

## Development method

For repository changes follow TDD: contract/check design → tests → honest RED → test inspection → implementation → GREEN → code inspection → fixes → reinspection → documentation. Read [Development rules → TDD and inspection](../../../docs/governance/development-rules.md#tdd-и-осмотр) when working on implementation behaviour.

Tools are batch-oriented and declarative: if two or more required mechanical actions have no reasoning decision between them, expose one operation that ensures the requested result through owning APIs. Do not add hidden defaults, compatibility readers or migrations without direct user authorization. Read [Declarative tools → architectural invariant](../../../docs/architecture/declarative-tools.md#1-архитектурный-инвариант) for the corresponding contract section available in the current revision.

## Project-local configuration

AI poise is a separate application. Each configured project owns its Task DB and its copied process catalogue initialized from AI poise reference templates. Changing a reference template must not silently change an existing project's process configuration. For the WSL delivery model, read [Local installation → Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Project setup → Publication and replay](../../../docs/configuration/project-setup.md#публикация-и-повтор).

## Verification and delivery

Run focused checks while developing, then the complete configured regression runner for a delivery boundary. Preserve failed diagnostic workspaces and do not replace a failed run with a retry. Package code, tasks/configs, documentation and verification evidence as one self-contained delivery.

Treat the accepted commit as immutable. Complete delivery through a child task/integration worktree with a separate mutable integration head: update from current `master`, merge, resolve conflicts there, and rerun checks. Stop automation on conflicts for agent resolution in that worktree. Recheck `master` immediately before publication and automatically repeat the cycle on drift until a safe fast-forward is possible; never force-update or require manual supervision.

Do not edit, merge, resolve conflicts, `stash`, `reset`, `restore`, `checkout`, `clean`, stage, commit, or delete in the main checkout or foreign WIP as part of completion. After confirmed publication, the same finisher removes task/integration worktrees, child branches, and registered temporary backups from its task/integration-scoped runtime-root directory; success requires that directory to be empty. Preserve pre-existing, foreign, durable operator, deliverable, and unfinished-recovery backups until their own terminal decision. Persist phases so crash recovery and idempotent replay continue the incomplete integration without rewriting the accepted commit. Read the canonical [finish and integration rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

When a skill relies on canonical documentation, link to the smallest exact normative section needed by the operational rule.
