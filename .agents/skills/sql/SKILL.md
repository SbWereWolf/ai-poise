---
name: sql
description: Verify affected query, schema and transaction contracts with necessary fields, independent direct SQL and relevant engine-specific evidence.
version: 1.0.0
---

# Query and transaction contracts

## Inputs

The affected query/schema/transaction, required observable result, actual DBMS/version, ownership, constraints and project commands/runtime. Read existing schema/query/runbook facts from the session-selected worktree and selected Poise configuration. Use [poise-workflow](../poise/SKILL.md) for the current Task/scope/evidence; never infer authority to modify another managed database.

## Procedure

1. Define only the properties affected by the requirement: data types, nullability/defaults, uniqueness/foreign-key constraints, necessary indexes, transaction/isolation and result semantics. Require ordering only when contractual; use explicit ordering then, never accidental engine order.
2. Enumerate only necessary fields in every applicable SELECT, including setup/assertion and subqueries. SELECT * and SELECT alias.* are prohibited. Review each selected field's necessity rather than replacing a wildcard with every known column. Parameterize data values; handle structural identifiers through the actual owner's validated/quoted schema mechanism.
3. For a non-ORM subject, arrange and inspect state through parameterized direct SQL with independent test-owned values. Do not use production models/repositories/factories/scopes/casts/helpers as the setup or oracle. Invoke the behavior through its real application public path so an isolated query test cannot falsely claim full use-case coverage. ORM behavior may use its real APIs when it is genuinely the declared subject.
4. Assert exact affected rows/counts/values/constraint failures and contractual order. Expected values never come from the production algorithm/constants/configuration. Canonical JSON above 99 Unicode characters, including control characters, moves to a fixture; exactly 99 may remain inline. Preserve object/array ordering according to the contract, not convenience.
5. Investigate query plans, lock behavior, deadlocks/isolation anomalies, backfill and performance **only when relevant**. Choose a suitable engine/version, representative data distribution/volume and reproducible competing transactions when required. Record plans/timing conditions and limits. A tiny fixture proves functional behavior, not production performance or lock safety.
6. For schema/data migrations only, load [strict-database-migrations](../strict-database-migrations/SKILL.md) and its self-contained historical policy. Ordinary read/query work does not trigger an unrelated migration ceremony. Do not introduce a migration engine or route around managed owners.
7. Run targeted property/boundary tests plus maintained fast smoke for ordinary Task completion/integration. Full suites belong only to release preparation. Use the existing project runner/evidence cache unchanged, preserve real terminal errors and diagnose before a side-effecting retry.

## Output

The explicit query/schema/transaction contract and fields, independent SQL arrangement/assertions, real application-boundary observations and relevant engine/plan/lock evidence with limitations. Save requirements, commands/source/runtime identity and results through current Poise Task/evidence/artifact owners and public handoff. Direct test SQL is never permission to edit Poise Task DB, lifecycle, claims or verification records.
