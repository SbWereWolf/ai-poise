---
name: poise-development
description: Change AI poise itself using its DDD, TDD, declarative-tool and project-local configuration rules.
---

# AI poise development

Read the repository `AGENTS.md` and `src/AGENTS.md` first. Work in a dedicated `tasks/<task-id>` worktree.

## Design boundaries

Identify the domain owner before implementation. Task, Sprint, content, evidence, artifacts, project configuration and runtime state change only through their owning APIs. Reuse the common runner and stage-handler families rather than creating a goal-specific engine. Read [Architecture boundaries → owners and dependencies](../../../docs/architecture/boundaries.md#ddd-04b--новые-владельцы-и-зависимости).

Keep caller-to-session establishment in `SessionEstablisher` for every public work composition.
Use native identity when available and an explicit persistent `POISE_CALLER_BINDING` otherwise;
callers and agents must not substitute an arbitrary session value. Identity origin is provenance,
not authorization, and read-only establishment must not create a business subject.

## Development method

For repository changes follow TDD: contract/check design → tests → honest RED → test inspection → implementation → GREEN → code inspection → fixes → reinspection → documentation. Read [Development rules → TDD and inspection](../../../docs/governance/development-rules.md#tdd-и-осмотр) when working on implementation behaviour.

For a development Task, pass the complete initial checks schedule explicitly; `{}` is valid and must not trigger template defaults. Design and register the actual RED/GREEN methods, schedules and future-output provenance at `verification_planning`, once their source paths are known.

Tools are batch-oriented and declarative: if two or more required mechanical actions have no reasoning decision between them, expose one operation that ensures the requested result through owning APIs. Do not add hidden defaults, compatibility readers or migrations without direct user authorization. Read [Declarative tools → architectural invariant](../../../docs/architecture/declarative-tools.md#1-архитектурный-инвариант) for the corresponding contract section available in the current revision.

## Project-local configuration

AI poise is a separate application. Each configured project owns its Task DB and its copied process catalogue initialized from AI poise reference templates. Changing a reference template must not silently change an existing project's process configuration. For the WSL delivery model, read [Local installation → Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Project setup → Publication and replay](../../../docs/configuration/project-setup.md#публикация-и-повтор).

## Verification and delivery

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks and the maintained bounded `tests/smoke.sh`; never register unfiltered repository-wide test discovery as a task method. Preserve failed diagnostic workspaces and do not replace a failed run with a retry. Package code, tasks/configs, documentation and verification evidence as one self-contained delivery.

Follow the canonical [executor/reviewer stage policy](../../../docs/governance/development-rules.md#роли-этапов-и-непрерывность-поручения). User Task start authorizes executor and reviewer to continue their respective stages, including checks and remediation, without a separate command for each review; honor explicit user limits and harness gates. At a role boundary, save results, confirm public handoff/release, stop modifying the Task and [directly notify the known counterpart](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим). The receiver claims/resumes before work. Self-inspection is not independent review, and publication/integration retains its separate authorization.

After release and successful notification, end the turn: neither role waits for replies, polls the other agent's status, reads its conversation to track progress, or schedules background monitoring. Resume when a new assignment arrives. Finish your own already-started commands and save/handoff operations before ending the turn; see the linked direct-handoff rule.

Treat the accepted commit as immutable. Complete delivery by advancing the existing task branch in its existing task worktree: update from current `master`, merge, resolve conflicts there, and rerun checks. Do not create a separate integration branch or worktree. Stop automation on conflicts for agent resolution in the task worktree. Under the shared target lock, recheck `master` and repeat the cycle on drift. Publish only with `git merge --ff-only <task-branch>` in the main checkout; never directly update or force-update the target ref.

Do not prepare, edit, or resolve conflicts in the main checkout or foreign WIP, and never `stash`, `reset`, `restore`, `checkout`, `clean`, stage, commit, or delete their state. The serialized fast-forward is the only publication effect. If it is blocked, require persisted proof that main `HEAD`, binding, index, tracked/untracked content, types, modes, and operation state are unchanged. After confirmed publication, remove only the task worktree, task branch, and registered temporary backups from the scoped runtime directory. Preserve foreign, operator, deliverable, and unfinished-recovery backups. Persist phases so current installed source can replay safely without rewriting the accepted commit. Read the canonical [finish and integration rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

When a skill relies on canonical documentation, link to the smallest exact normative section needed by the operational rule.
