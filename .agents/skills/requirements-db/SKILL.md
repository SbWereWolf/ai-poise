---
name: requirements-db
description: Read, analyze and change Requirements DB entities and relations through public declarative Query and Command packets, not raw SQL or Task snapshot edits.
---

# Requirements DB

Select the actual installation's interpreter and explicit project manifest before
calling `python -B -m poise requirements --config /absolute/project.json`.
This API does not require a Task claim or work bootstrap. It does require the
configured Requirements storage, schema and packet bounds; never borrow another
project's paths or create an installation to avoid a configuration failure.

Read the [canonical contract](../../../docs/workflows/requirements-registry.md#пакетный-crud)
for entity columns, exact packet shapes and filters. Send separate Query and
Command packets. Analyze returned rows, retain the observed revision, and write
only the authorized intent. Follow `next` unchanged while the revision is stable;
a stale cursor requires a new read, not an invented continuation.

For relations select exactly one A entity and zero, one or many B entities.
Omitting B's filter selects all eligible B entities. Multiple A matches are an
error, not permission for an N:M operation. Use explicit unlink operations before
deleting referenced entities; do not infer cascades.

Use a unique request_id and the observed expected_revision. Inspect every
affected_rows value. A successful no-op still advances revision once. Retry an
uncertain committed write only with the exact original request: historical replay
returns its original counts and revision. A changed intent requires a new ID and
a new decision based on current data, never a blind retry after revision conflict.

Read [errors and
recovery](../../../docs/workflows/requirements-registry.md#ошибки-crud-и-восстановление)
before reacting to rejection; confirmed-COMMIT cleanup can fail after data was
saved. Do not delete locks or edit databases/receipts directly. Service tables and
Task requirement snapshots are outside this CRUD surface.

Use the [read–analyze–write
example](../../../docs/workflows/requirements-registry.md#пример-чтение-анализ-изменение)
with explicit interpreter/configuration/selected entity/change/request identity.
Documentation examples are not authorization to mutate a working registry.
