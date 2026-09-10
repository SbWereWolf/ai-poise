---
name: harness-development
description: Change Harness itself using its DDD, TDD, declarative-tool and project-local configuration rules.
---

# Harness development

Read the repository `AGENTS.md` and `src/AGENTS.md` first. Work in a dedicated `tasks/<task-id>` worktree.

## Design boundaries

Identify the domain owner before implementation. Task, Sprint, content, evidence, artifacts, project configuration and runtime state change only through their owning APIs. Reuse the common runner and stage-handler families rather than creating a goal-specific engine. Read [Architecture boundaries → owners and dependencies](../../docs/architecture-boundaries.md#ddd-04b--новые-владельцы-и-зависимости).

## Development method

For repository changes follow TDD: contract/check design → tests → honest RED → test inspection → implementation → GREEN → code inspection → fixes → reinspection → documentation. Read [Development rules → TDD and inspection](../../docs/development-rules.md#tdd-и-осмотр) when working on implementation behaviour.

Tools are batch-oriented and declarative: if two or more required mechanical actions have no reasoning decision between them, expose one operation that ensures the requested result through owning APIs. Do not add hidden defaults, compatibility readers or migrations without direct user authorization. Read [Declarative tools → architectural invariant](../../docs/declarative-tools.md#1-архитектурный-инвариант) for the corresponding contract section available in the current revision.

## Project-local configuration

Harness is a separate application. Each configured project owns its Task DB and its copied process catalogue initialized from Harness reference templates. Changing a reference template must not silently change an existing project's process configuration. For the WSL delivery model, read [Local installation → Architecture](../../docs/wsl-local-delivery.md#архитектура-локальной-установки) and [Project setup → Publication and replay](../../docs/project-setup.md#публикация-и-повтор).

## Verification and delivery

Run focused checks while developing, then the complete configured regression runner for a delivery boundary. Preserve failed diagnostic workspaces and do not replace a failed run with a retry. Package code, tasks/configs, documentation and verification evidence as one self-contained delivery.

When a skill relies on canonical documentation, link to the smallest exact normative section needed by the operational rule.
