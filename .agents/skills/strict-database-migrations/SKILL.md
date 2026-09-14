---
name: strict-database-migrations
description: Design and verify self-contained historical schema/data migrations using literal local values, schema APIs and parameterized SQL.
version: 1.0.0
---

# Self-contained database migrations

## Inputs

Required source and target schema/data, accepted change contract and invariants, actual DBMS/version, migration/verification commands, deployment concurrency and applicable recovery conditions. Resolve commands/runtime/version through the selected Poise project; read target-worktree schema/manifests/runbooks as facts without storing Poise configuration there. Obtain current Task/stage/allowed paths through [poise-workflow](../poise/SKILL.md).

## Procedure

1. Treat each migration as a self-contained historical artifact that must still run after production code evolves. Table/column/index/constraint/status/discriminator/data semantics are literals or migration-local constants. The [migration policy](../../references/migration-policy.md) is mandatory for this boundary.
2. Use permitted framework migration/schema/connection APIs and parameterized SQL. Never import, resolve, name or invoke production models, enums, constants, services, repositories, DTOs, helpers, factories, seeders, configuration providers, container bindings, ORM events/casts/scopes or external services—even via strings, callbacks or dynamic lookup. A test allowlist is not a migration allowlist.
3. Parameterize data values even when they are literal. Validate/quote structural identifiers through the actual DBMS/schema API; value parameters do not magically bind identifiers. Enumerate necessary selected columns: SELECT * and alias.* are prohibited. Do not depend on production defaults or algorithms that may later change historical execution.
4. State preconditions, failure behavior and the real transaction/DDL capabilities of the specified engine/version. Where relevant, design lock/table-scan behavior, data volume strategy, resumable backfill and expand/backfill/contract compatibility. Use the existing project migration tool and owner, not a new migration lifecycle or engine.
5. Specify recovery at the actual irreversible boundary. A down method alone is not proof of restored data or safe rollback. For irreversible changes state forward-repair/restore prerequisites and validation; destructive operations need their actual authorization and protected environment. Do not claim reversibility without evidence.
6. Design the affected behavior/policy tests and inspect them before migration code under the existing two-role process. Start from the **required source state**, not only a fresh target schema. Apply the migration through the real project tool, then verify exact schema/data/constraints by independent parameterized direct SQL. Test failures/replay/locking/recovery only where applicable and in an environment capable of observing them.
7. Run existing language checks that distinguish permitted framework APIs from forbidden production references. Do not widen a test subject declaration to permit production code inside migrations. Independent reviewer inspection still checks semantic isolation beyond the guard.
8. Compare exact independent values; canonical JSON longer than 99 Unicode characters, including newlines/control characters, belongs in a test-owned fixture, while exactly 99 may remain inline. Use only targeted checks plus maintained fast smoke in ordinary work; missing selection never falls back to all tests.

## Output

The historical migration, explicit source→target contract, preconditions/locks/recovery decisions, independent direct-SQL evidence and exact command/source/DBMS/runtime identity in current Task/evidence owners. Report untested production-volume/concurrency conditions. Public handoff is not deployment authority, and direct migration/test SQL never authorizes editing the managed Poise Task DB.
