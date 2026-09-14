---
name: laravel
description: Change Laravel applications using their actual version, domain boundaries and explicit HTTP/CLI/container/transaction contracts.
version: 1.0.0
---

# Laravel in the selected project

## Inputs

The affected application/use case, actual installed Laravel/PHP version and stack, accepted interface/behavior, current Task scope and project-owned architecture/commands/runtime. Read target Composer manifests/lock and architecture documents from the session-selected worktree. Resolve Poise's profile/configuration through Poise, not a new file in the target repository; use [poise-workflow](../poise/SKILL.md).

## Procedure

1. Establish the real domain/application/delivery/infrastructure owners. Keep controllers, commands, jobs, listeners and providers thin unless they own an explicitly accepted application responsibility. Reusable business decisions belong at their canonical owner, according to this project's architecture—not a copied ERP application map.
2. Define request/response, validation, authorization, transaction/idempotency, queue and failure/logging semantics only for the affected boundary. Use the installed framework's APIs; do not require Laravel 12, Deptrac, MoonShine, a new queue component or dependency upgrades simply because this skill is loaded.
3. Judge DI aliases/bindings by the substituted contract: result, failure, side effects and intended identity. An ordinary container alias is not inherently a compatibility defect. Reject an alias that silently keeps an explicitly replaced legacy contract alive or changes substitution semantics; do not ban all aliases or invent fallback bindings.
4. Select adjacent skills by actual stage: [phpunit](../phpunit/SKILL.md) for PHP tests, SQL for query/persistence decisions, strict-database-migrations for schema/data transitions, [moonshine-native](../moonshine-native/SKILL.md) only for installed MoonShine UI, and [redis-queues](../redis-queues/SKILL.md) for actual queue/worker behavior. Do not preload a fixed specialist set. A true integration Task may span specialties for one declared combined result.
5. Work only in the session-selected Task worktree. Prefer available IDE semantic navigation/refactoring under the [project IDE policy](../../../docs/governance/jetbrains-mcp-policy.md#правило-выбора-инструмента), using its documented fallback when unavailable. Resolve the real Composer/runtime scripts; no copied ERP wrappers or silent host substitutions.
6. Test the real changed boundary with independent expectations and meaningful denial/failure cases. Keep migration and test policy in their existing owners. Run targeted checks plus maintained smoke; full suites are release work. Formatter/static/architecture checks follow actual project requirements, not mandatory foreign tools.

## Output

The explicit application/interface/DI/transaction decisions, actual version/runtime, selected specialist rationale, changed code and focused evidence in current Poise owners. Record unsupported API/environment assumptions and public handoff; this skill does not grant dependency upgrade, deployment, acceptance or master integration authority.
