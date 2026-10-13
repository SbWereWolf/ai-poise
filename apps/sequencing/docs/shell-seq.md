# Sequencing standalone CLI

## Entry point

Sequencing is launched through its app-owned provider:

```text
apps/sequencing/bin/sequencing --config <path-to-sequencing.json>
```

The executable resolves its repository-relative Python location from its own file. The caller supplies the Sequencing configuration explicitly with `--config`; the command does not load the shared project configuration.

The provider accepts one newline-terminated UTF-8 JSON request on stdin and writes one newline-terminated JSON result on stdout. The wire schema is `stride-sequencing/v1`. This implementation slice exposes `apply` with `register_task` and `query` with `kind: "task"`; the broader owner contract remains defined by [Sequencing public contract](contract-seq.md).

## Sequencing-owned config

The standalone config schema is `stride-sequencing-config/v1`. Its top-level object is exact: every field is required and unknown fields are rejected.

```json
{
  "schema": "stride-sequencing-config/v1",
  "paths": {
    "state": "../state",
    "database": "sequencing.sqlite",
    "lock": "sequencing.lock"
  },
  "batch": {
    "max_input_bytes": 65536,
    "max_items": 256
  },
  "read": {
    "max_queries": 256
  },
  "lock": {
    "seconds": 5.0,
    "poll_seconds": 0.05
  },
  "repository": {
    "id": "ai-poise",
    "path": "../../..",
    "base_ref": "refs/heads/poise-stride-v2"
  },
  "worktree": {
    "root": "../../../projects/ai-poise/worktrees",
    "branch_prefix": "tasks/",
    "fs_timeout_seconds": 30.0,
    "git_timeout_seconds": 30.0
  },
  "transfer": {
    "root": "../transfer",
    "max_package_bytes": 67108864,
    "max_files": 4096,
    "max_file_bytes": 16777216,
    "file_mode": 384
  },
  "identifiers": {
    "task": {"pattern": "^[0-9]{4}$"},
    "sprint": {"pattern": "^[A-Z0-9-]+$"}
  }
}
```

`paths.state` may be absolute or relative to the config directory. `paths.database` and `paths.lock` are relative to the resolved Sequencing state root, must remain inside it, and must be distinct. The `database` and `lock` are therefore Sequencing-owned and move with the configured state root.

`batch.max_input_bytes`, `batch.max_items`, and `read.max_queries` are required positive integers. `lock.seconds` and `lock.poll_seconds` are required positive finite numbers and control the actual nonblocking lock acquisition deadline and retry interval; there is no hidden blocking-lock default. Repository, worktree, transfer, and `identifiers` policies are required and validated even when the current operation does not consume every configured capability.

## Ownership and isolation

The standalone Sequencing shell opens only its configured Sequencing `database` and `lock`. It does not load Requirements or Execution configuration and does not read or mutate their databases. A poisoned neighboring `config/processes/analysis.json`, Requirements database, or Execution database is outside this provider boundary and does not affect a valid Sequencing request.

A second invocation with another Sequencing config resolves a different state root and therefore uses an independent Sequencing database and lock. The provider does not instantiate the shared Runtime to obtain those paths.

`task_id` is a foreign canonical identity supplied by the caller. `register_task` validates it against the configured `identifiers.task.pattern`, stores the same value, and never allocates or rewrites a Task identity. A task query returns the preserved canonical `task_id` and its explicit Requirements snapshot provenance.

## Failure behavior

Configuration and packet validation are fail-closed. A rejected request writes a JSON result with `"status": "rejected"` and exits with `exit 2`; stderr remains empty for ordinary contract rejections.

Examples include a missing required config field, unsupported config schema, path escape outside the Sequencing state root, invalid limits, invalid `identifiers`, oversized stdin beyond `max_input_bytes`, invalid wire JSON/shape, unsupported operation/query kind, or lock acquisition exceeding configured `lock.seconds`.

Config validation happens before Sequencing state is created. Missing required values therefore do not fall back to the shared project configuration or silently open a neighboring database.

## Replay and revision behavior

A new successful `apply` request stores a replay receipt and advances the owner-local revision. Repeating the exact same `request_id` and payload returns the saved result with `"replayed": true` without another mutation or revision increase. Reusing the same request ID with different content is rejected.

The implemented `register_task` operation creates only the Sequencing record required by this slice; it does not create Requirements or Execution records, claims, worktrees, or Sprint membership.

## Verification

Behavior and isolation are verified through the real standalone executable:

```text
python -B apps/sequencing/tests/test_shell.py --suite behavior
```

The behavior suite covers provider discovery, isolated storage with poisoned neighboring Requirements/Execution state, independent configs, fail-closed required fields, configured input limits, required `identifiers`, canonical `task_id` preservation, and exact replay behavior.

Documentation literals are checked with:

```text
python -B apps/sequencing/tests/test_shell.py --suite documentation
```

These checks map the standalone Sequencing boundary to GEN-03, GEN-04, and SEQ-01 and preserve the task DoD: independent configuration/state plus co-delivered command/config documentation.
