# Execution standalone CLI

## Entry point

Execution is launched through its app-owned provider:

```text
apps/execution/bin/execution --config <path-to-execution.json>
```

The provider accepts one newline-terminated UTF-8 JSON packet on stdin and writes one JSON result on stdout. The wire schema is `stride-execution/v1`. The current extraction slice implements `apply` with `setup_execution` and `query` with `kind: "execution"`; the broader Execution lifecycle remains defined by [Execution public contract](contract-exe.md) and later migration tasks.

## Execution-owned config

The standalone config schema is `stride-execution-config/v1`. All fields are required and unknown fields are rejected.

```json
{
  "schema": "stride-execution-config/v1",
  "paths": {"state": "../state", "database": "execution.sqlite", "lock": "execution.lock"},
  "batch": {"max_input_bytes": 65536, "max_items": 256},
  "read": {"max_queries": 256},
  "lock": {"seconds": 5.0, "poll_seconds": 0.05},
  "identifiers": {"task": {"pattern": "^[0-9]{4}$"}}
}
```

`paths.state` is resolved from the config directory. `database` and `lock` must be relative to the resolved Execution state root, remain inside it, and be distinct. The provider therefore owns its SQLite database and lock file independently of the shared POISE runtime.

`max_input_bytes`, `max_items`, `max_queries`, `lock.seconds`, `lock.poll_seconds`, and `identifiers.task.pattern` are mandatory operational policy. The lock settings control the real nonblocking acquisition deadline and retry interval; missing values never fall back to hidden defaults.

## Ownership and isolation

The standalone Execution shell opens only its configured Execution state/database/lock. It does not instantiate the shared Runtime and does not read Requirements or Sequencing configuration or databases. Invalid neighboring project/process files and foreign database sentinels are outside the provider boundary.

A second invocation with another config resolves another state root and uses an independent Execution database and lock.

`task_id` is a foreign canonical identity supplied by the caller. `setup_execution` validates it against the configured `identifiers.task.pattern`, stores the same value, and never allocates or rewrites it.

## Current setup/query slice

`setup_execution` accepts the Execution contract identity/provenance fields required for the extracted shell boundary, creates generation 1 in `draft` state, stores a canonical SHA-256 contract revision, and returns the unchanged canonical `task_id`.

`query` with `kind: "execution"` returns the owner-local record: canonical `task_id`, generation, contract revision, state/current stage/iteration, workspace ID, and admission ID.

This task does not migrate the full process/gate/restart/outcome/transfer lifecycle; those operations remain governed by `contract-exe.md` and their dedicated migration tasks.

## Fail-closed behavior

Configuration and packet validation are fail-closed. Ordinary contract rejection writes a `stride-execution/v1` result with `status: "rejected"`, exits with **exit 2**, and leaves stderr empty.

Examples include missing required config fields, unsupported schema, state-path escape, invalid limits, invalid `identifiers`, oversized input beyond `max_input_bytes`, unsupported operation/query kinds, invalid canonical `task_id`, or lock acquisition exceeding configured timeout. Config validation occurs before Execution state is created.

## Replay and revision

A successful apply stores a replay receipt and advances the owner-local revision. Repeating the exact same `request_id` and payload returns the saved result with `replayed: true` without another mutation. Reusing that request ID with different content is rejected.

## Verification

Behavior and isolation are verified through the real standalone executable:

```text
python -B apps/execution/tests/test_shell.py --suite behavior
```

The behavior suite covers provider discovery, poisoned-neighbor isolation, independent configs, fail-closed missing config, configured input limits, required identifier policy, canonical `task_id` preservation, and exact replay.

Documentation literals are checked with:

```text
python -B apps/execution/tests/test_shell.py --suite documentation
```

These checks map the standalone Execution boundary to GEN-03, GEN-04, and EXE-01 while preserving co-delivery of command/config documentation in T020.
