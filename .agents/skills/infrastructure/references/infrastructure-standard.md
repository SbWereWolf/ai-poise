# Infrastructure standard

Apply to the actual changed runtime surface and its owner. This supports the infrastructure skill without overriding applicable agent rules or the target's accepted operational contract. Docker Compose, systemd, CI, queues, cloud services and other technologies are conditional on that project; none is installed or required by this skill alone.

## Locate the actual contract

Use versioned target documentation in the session-selected worktree to identify source/configuration paths, runtime wrappers, environment declarations, tests, build/promotion identity, operational owner and runbook. Resolve those facts through the selected Poise profile; store Poise-required configuration inside Poise only. Use the [documentation owner map](../../../references/repo-doc-map.md). Verify that every cited path exists; do not invent an environment template, tool, platform package or runbook.

## Reproducible runtime wiring

Define the supported local/validation/CI/deployment surfaces and the invariant that must survive promotion. Pin source revision, build inputs/output identity and target environment. Treat environment selection, build/container/bootstrap changes as product behavior if outcomes can change. No hidden local shell-history, credentials, port or binary assumptions; no fallback to a different environment after failure.

Credentials are supplied through approved owners, not copied into examples, logs, Task artifacts or fixtures. Environment variables belong to an existing authorized target configuration/template/runbook surface; that is distinct from Poise's own profile storage. The current migration skill does not authorize changing those surfaces.

## Script and operation discipline

Validate input schema, permitted action, explicit target/root, preconditions and output contract. Preserve the documented exit-code distinctions, complete durable stdout/stderr and ambiguous-partial-effect status before presentation. Passing a command through a parser must not erase its failure. Deterministic, rerunnable operations use the existing owner's operation identity/idempotency and recovery protocol; a retry first establishes which effects already occurred.

Do not run broad shell sequences with implicit compose files, working-directory jumps, migrations and wildcard deletion mixed together. Use the target's actual wrapper/CLI and argument contract with only authorized effects. Keep diagnostics close to the failed step, but preserve secret boundaries. No database migration may reach external services.

Optional C028 telemetry is asynchronous and non-blocking. Its dispatch/transport failure cannot change the main operation's real success or failure. Correctness-critical results, transaction receipts and recovery evidence remain required. Record bounded telemetry failures separately through their owner; this standard neither implements a transport nor imports an accounting engine.

## Verification and recovery

Choose the smallest relevant script/contract/health/scenario check and maintained fast smoke. The check must exercise the changed runtime boundary, not merely a broad quick gate. Full suites are release preparation only. For cross-specialty behavior split focused Tasks and define an integration Task that actually observes the combined scenario and failures.

Check success, rejected input/preconditions, operational failure and meaningful partial failure; exercise safe replay and interrupted-operation recovery where applicable. Preserve exact commands, runtime/build/target identity, exit results and observable outcomes. Investigate resource exhaustion/timeouts rather than disguising them as skips or unlimited retries.

Define the pre-change savepoint, restoration method and rollback window before an authorized destructive step. Restore/rollback claims require observed verification. For irreversible changes state the operational caveat and forward recovery explicitly. A backup command returning successfully is not proof of restored-data integrity. Never fabricate production-like validation, browser identity or performance measurements from a unit test.

## Owner documentation and anti-patterns

Update actual operator/runbook/configuration/error guidance when behavior changes; record the scope and evidence through existing Poise Task content. Do not create DOC-IMPACT.md or a parallel operational truth source. Manual checks must have reproducible steps, prerequisites and expected observations, and remain unverified until performed.

Do not add placeholder containers, fake services, decorative scripts or disconnected deployment layers. Every change must participate in the real development, test, request, monitoring, backup, rollout or recovery path. Avoid configuration magic, undocumented environment assumptions, script changes without direct checks and rollout changes without recovery notes. A release-readiness assessment is not deployment or acceptance authority.
