# Historical migration isolation

A migration must remain executable after current production code changes. Every semantic table, column, index, constraint, status, discriminator and data value is a literal or constant declared in that migration. Keep it self-contained; do not replace historical semantics with imports from today's application.

## Allowed boundary

Use the actual framework's migration/schema/connection APIs and parameterized SQL. Literal semantic values remain parameters, not unsafe concatenation. Structural identifiers use the engine/schema owner's correct validated and quoted mechanism. Enumerate necessary selected fields; SELECT * and alias.* are prohibited.

## Forbidden dependencies

Do not import, invoke, resolve or name production models/repositories/services/use cases/DTOs/value objects, enums/constants, factories/seeders, configuration providers/feature flags, container bindings, ORM events/casts/scopes/accessors/mutators, external services or mutable production algorithms. Strings, callbacks, reflection and dynamic lookup are not bypasses. A framework schema API is distinct from application logic; the language checker must preserve that distinction. Allowing a class in a test does not allow it in a migration.

## Transition and recovery

Specify the actual source and target schema/data, engine/version, preconditions and accepted invariant. Establish real DDL/transaction capability and relevant locks/scans, live compatibility, data volume/backfill strategy and expand/backfill/contract steps. Reuse the project's migration owner and runner; no additional lifecycle is created.

Verify from the required source state through the actual migration entry point, then inspect exact results with independent direct SQL. Starting only from an empty database is insufficient when the contract includes existing data. Exercise failure, interruption/resume, concurrency and recovery where the requirement makes them relevant and the environment can observe them.

Irreversible data loss cannot be undone merely by re-adding a column. State actual recovery/forward repair/restore conditions, authorization and post-recovery checks; do not promise rollback without proof. Keep secret or live production data out of fixtures and ordinary test output.

## Test and evidence discipline

Expected values are independent literals/test-owned data. Canonical JSON strictly longer than 99 Unicode characters, including newline/control characters, uses a fixture; 99 may remain inline. Inspect tests before implementation, retain negative contracts and use targeted checks plus maintained smoke in ordinary work. Save source→target evidence, engine/runtime identity, exact command and recovery limits through [existing Poise evidence](../../docs/workflows/evidence.md#один-пакет-результата). Direct SQL is not authorization to edit the managed Task DB.
