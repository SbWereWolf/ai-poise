---
name: infrastructure
description: Change actual runtime, deployment, environment, health, backup/restore and rollback contracts through their project owners with focused operational evidence.
version: 3.0.0
---

# Infrastructure

Infrastructure remains a separate skill. Treat runtime wiring, configuration, deployment, health, backups, restore, rollback, queues and observability as explicit user-affecting contracts—not invisible support code.

## Inputs

The current Poise Task/stage/scope and session-selected worktree, changed operational scenario, exact target environment and authorization, actual runtime/build identity, owners/runbooks and explicit success/failure/recovery requirements. Resolve paths, commands, versions, runtime and file maps through the selected Poise-owned configuration using the target's versioned documentation as read-only facts. Never add an AI poise configuration to the target repository. Use [poise-workflow](../poise/SKILL.md).

## Procedure

1. Identify the existing operational owner and its actual scripts/wrappers/configuration, environment templates and runbook through the [documentation map](../../references/repo-doc-map.md). Follow applicable root/nested agent rules. Do not copy deployment topology, product paths, service names, compose layouts or browser assumptions from ERP.
2. Define explicit inputs, intended effects, output/error/exit semantics, credentials boundary, target identity and verification before mutation. Pin source/build identity through promotion. An absent fact is a setup dependency, not permission to silently select another runtime or environment.
3. Preserve argument clarity, deterministic behavior, correct error statuses, meaningful operation diagnostics and safe replay. Use existing owners, passing the exact filesystem path/target and argv required by the operation. Validate preconditions and outputs; an empty/partial/parser failure must never become success. Do not call external services from a database migration.
4. Keep the C028 separation: main operation validation/results/errors are authoritative; optional telemetry is dispatched asynchronously and does not block or alter them. Record a bounded telemetry diagnostic through its owner when available. Correctness-critical business evidence is not optional telemetry. Do not add a second telemetry/accounting engine.
5. Separate unrelated specialties into focused Tasks. Use an integration Task with actual dependencies and a joint observable scenario for cross-boundary runtime/browser/queue/database/application behavior, rather than hiding all work inside one infrastructure label.
6. Follow the [infrastructure standard](references/infrastructure-standard.md). Exercise the changed scenario with targeted checks plus maintained fast smoke; full suites belong only to an explicitly scoped release-preparation Task. Investigate resource/timeouts with evidence instead of widening limits or treating skips as success.
7. Check relevant failure, second-run and recovery paths. For irreversible effects state the rollback limit, restore/forward-recovery plan and exact evidence; do not imply a backup exists or restoration was tested without doing it. Production-like checks and actual deployment each require their own target/access authority.
8. Update the real owner documentation when invocation, environment, operation results/errors or recovery changes. Record impact through existing Poise content/evidence. Do not create DOC-IMPACT.md. Perform executor self-review and public handoff; stale operational guidance or an unexercised required boundary prevents a full completion claim.

## Output

Changed operational contract and owner/runbook, exact target/source/build identity, focused success/failure/replay/recovery results, truthful command/error statuses, documentation impact and remaining limitations in existing Poise Task/result/artifact/evidence owners. A library entry or smoke pass does not prove target deployment, restored data, automatic routing, independent review or publication authority.
