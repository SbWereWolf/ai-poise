---
name: requirements-db
description: Read project requirements and persist changes by creating and applying literal DML migrations through the Poise requirements API.
---

# Requirements database

## Authority and inputs

Read the [canonical requirements
contract](../../../docs/workflows/requirements-registry.md#декларативный-api-и-согласование)
and the [storage paths](../../../docs/workflows/requirements-registry.md#владение-и-явные-пути).
Use the selected installed interpreter, explicit project config and existing dependencies.
For writes, continue the actual claimed mutable Task from its backend-managed worktree and branch.
Every affected migration/bootstrap file must belong to the current stage's allowed paths.
Read-only query does not require a Task claim and must not create storage.

## Procedure

1. Read the actual requirements, domain records and relations through `query`.
   Preserve exact source values needed for rollback; do not infer missing business meaning.
2. Prepare primitive literal DML and its inverse. Use `command` with exactly
   `name`, `sql`, `rollback_sql`: it writes a standard yoyo migration and applies the data history.
   Changes to existing requirements and links use this same algorithm.
3. Keep schema migration history in the task-management application's configured directory,
   data migration history in the project's documentation, and one current schema bootstrap
   alongside that documentation. Runtime SQLite and sidecars belong to application data outside Git.
   DML contains no DDL, application callbacks, dynamic business logic or transaction control.
4. Use `apply`/`rollback` with explicit `kind: schema|data` and the same yoyo owner.
   For ordinary `squash`, retain original forward order and inverse rollback order.
   The Task completion boundary consolidates Task-added DML before source freeze and verifies
   actual up/down/up; an unapplied single file blocks completion.
5. After schema changes, use explicit `rebase` to replace obsolete-schema DML with a full
   current-schema data migration, preserving every application row and administrative value.
   Update the single bootstrap; never mix DDL into the data history.
6. Use `export` with `source`, `requirements: null|[ids]`, `name` to generate a migration
   from an existing read-only source, then apply it. Compare complete reconstructed rows/schema,
   not only counts. Keep a finite recovery copy only until verified cutover and old writer
   cessation.

## Verification and recovery

Follow the [migration
procedures](../../../docs/workflows/requirements-registry.md#изменение-требований-через-миграции)
and [error recovery](../../../docs/workflows/requirements-registry.md#ошибки-и-восстановление).
Use standard yoyo histories and SQL constraints. Inspect file, data and actual applied IDs after
failure; a rejected apply does not imply that `command` never created its file.
Keep query filters, cursors and immutable Task snapshots under their existing public owners.
Publish truthful source-bound evidence through the actual Task lifecycle. Ordinary writes use
the migration API and standard yoyo accounting, without direct persistent CRUD or manual ledger
edits.
For rare verified data/journal divergence, the canonical recovery rule permits explicitly
operator-authorized manual repair using standard tools: capture actual state/cause, exclude
other writers, declare the necessary finite backup, audit actions and verify data/history and
replay.
Authority names the exact objects/actions; this exception never permits Task DB, lifecycle,
claim or verification-record patches or unapproved destructive operations.
Do not add a second engine, automatic reconciliation or compatibility modes.
