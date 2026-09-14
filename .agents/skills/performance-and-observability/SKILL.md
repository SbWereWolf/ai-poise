---
name: performance-and-observability
description: Diagnose and improve measured target-application performance and operational visibility at the actual stack boundary without inventing measurements or importing agent accounting.
version: 3.0.0
---

# Performance and observability

Apply to a concrete target-application latency/resource/throughput or diagnostic-visibility problem. Define the observable user/runtime outcome and budget before optimization. This skill does not add AI-agent API usage accounting, asynchronous Poise Accounting or interval-calculation requirements; those belong to N10/C028, not application performance.

## Inputs

Current Poise Task/stage/scope and session-selected worktree, symptom and affected user/runtime scenario, real stack/runtime/build/environment identity, accepted budget and measurement protocol, relevant telemetry/diagnostic owner and authorized data access. Resolve commands, versions, environments and area/skill/file maps through the selected Poise project. Target versioned docs/configuration supply read-only facts; Poise configuration stays inside Poise. Use [poise-workflow](../poise/SKILL.md).

## Procedure

1. Choose only the boundary supported by the actual symptom and stack. CPU, memory, query/lock behavior, latency distributions, bundle/rendering cost, queue depth/age/failures and traces are conditional tools—not a checklist forcing Vue, SQL and queues into every Task.
2. Establish a reproducible baseline: exact revision/build/runtime, dataset/workload, relevant concurrency, warm/cold state, sampling method and accepted metric/budget. Separate observations from hypotheses. If access or instrumentation is absent, state the missing measurement; do not invent numbers, extrapolate one sample into a distribution or call an unexecuted benchmark a result.
3. Diagnose before optimizing. For actual Vue rendering issues inspect component/route/store updates after correct behavior; for actual SQL problems inspect plans, rows, transactions/locks; for actual queues inspect retries/failures/age/depth. Other stacks use their own owner tools. Compare matched before/after runs and expose variance/confounders instead of cherry-picking.
4. Add stable, useful logs/metrics/traces with correlation and bounded safe context only at the owning boundary. Do not leak secrets or high-cardinality/private payloads, duplicate a telemetry engine or make observability obscure a main operation failure. Preserve exact error and failure semantics.
5. Keep Tasks focused by responsibility/specialty; use a separate real integration obligation for combined outcomes where needed. Do not use a performance label to authorize unrelated architecture/dependency/infrastructure changes. Correctness, single-owner state and exact independent tests are not traded away for apparent speed.
6. Run targeted behavior/performance checks relevant to the change plus maintained fast smoke. Full suites belong only to explicit release preparation. Production diagnostics, load generation and environment changes require their actual access/effect authority; a passing local test does not prove production impact.
7. Perform executor self-review, document the observable contract/instrumentation owner and preserve repeatable evidence through existing Poise owners. No agent-cost accounting, unrelated interval engine or fabricated benchmark is introduced.

## Output

Measured baseline and matched result when available, exact protocol/runtime/workload identity, uncertainty and confounders, explained change, correctness evidence and remaining limits. Use current Poise Task artifacts/evidence and public handoff. Explicitly say when performance was not measured or a required environment was unavailable; do not self-certify independent review, acceptance or automatic routing.
