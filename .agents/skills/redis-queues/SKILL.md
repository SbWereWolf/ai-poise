---
name: redis-queues
description: Verify real Redis/queue delivery, retry, idempotency and lock behavior without adding unrelated infrastructure.
version: 1.0.0
---

# Redis and queue semantics

## Inputs

Work that actually uses Redis or a queue: affected message/use case, schema/version, owner, delivery/idempotency/ordering contract, retry/backoff/timeouts, lock ownership/lifecycle and selected project environment/commands. Read target queue configuration and runbooks as facts; keep Poise routing/runtime mapping in its own project configuration. Use [poise-workflow](../poise/SKILL.md) for scope and evidence.

## Procedure

1. Identify the actual infrastructure and domain effect. Loading this skill does not authorize adding Redis, a queue, dead-letter storage, locks or a financial model for checklist completeness. It does not select queue technology for Poise Accounting.
2. Define the relevant successful and failed transitions: message acceptance, claim/visibility, processing, durable effect, acknowledgement, retry or terminal failure. State which owner controls idempotency and lock release. Use actual configured service semantics rather than assuming exactly-once delivery or a particular lease implementation.
3. Where applicable test duplicate delivery, worker failure and re-execution, concurrent workers, expiry/reclaim and lock release. Observe whether required effects occur once and forbidden effects remain absent. A client failure does not prove the server side effect did not commit; establish state before replay.
4. Use isolated target-compatible test infrastructure and independent direct protocol observations for keys/messages/counters/status/effects. Expected values are literal/test-owned, not computed by the production wrapper/algorithm. Invoke the real application entry point for the changed business behavior.
5. Interpret prior business-balance wording as the generic requirement to avoid forbidden side effects under technical failure. Only an actual financial domain requires balance/accounting scenarios. Keep retry/timeout/lock parameters in their real project owner; do not copy ERP worker commands or create Poise Accounting components.
6. Use targeted checks plus maintained fast smoke, with realistic concurrency/timing only where needed. Preserve command/source/runtime identity and terminal logs, diagnose before retries and use owning cancellation/recovery. Unavailable services are explicit missing evidence, not a mock-proven integration claim.

## Output

The actual delivery/idempotency/lock contract, focused positive/failure/concurrency observations, forbidden-effect assertions and remaining environment assumptions in existing Poise Task/evidence owners. Public handoff retains incomplete results honestly; no additional infrastructure or deployment authority is implied.
