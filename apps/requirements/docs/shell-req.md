# Requirements standalone CLI

## Entry point

Requirements is launched through its app-owned provider:

```text
apps/requirements/bin/requirements --config <path-to-requirements.json>
```

There is no root alias for this provider. The executable resolves its installation-relative repository and `src` locations from its own file location; callers supply the Requirements config explicitly with `--config`. Installation paths are not embedded in the config or command contract.

The provider accepts one UTF-8 JSON request on stdin and writes one newline-terminated JSON result on stdout. Current operations are `apply`, `query`, and `import_bootstrap`; their packet and result contracts remain defined by [Requirements public contract](contract-req.md).

## Requirements-owned config

The config schema is `stride-requirements-config/v1`. The top-level object is exact: every field is required and unknown fields are rejected.

```json
{
  "schema": "stride-requirements-config/v1",
  "paths": {
    "state": "../state",
    "database": "requirements.sqlite",
    "lock": "requirements.lock"
  },
  "batch": {
    "max_input_bytes": 8192,
    "max_items": 64
  },
  "lock": {
    "seconds": 1.0,
    "poll_seconds": 0.01
  },
  "task_ids": {
    "namespace": {"minimum": 1, "maximum": 999999},
    "width": 6,
    "progression": {"first": 1, "step": 1}
  }
}
```

`paths.state` may be absolute or relative to the config file directory. `paths.database` and `paths.lock` are relative to the resolved Requirements state root, must remain inside it, and must be distinct. The database and lock are therefore owned by Requirements and can be relocated by relocating the config/state paths; no repository-wide absolute path is required.

`batch.max_input_bytes` and `batch.max_items` are required positive integers. `lock.seconds` and `lock.poll_seconds` are required positive finite numbers. `task_ids` is required and validated as a Task ID allocation policy even when the current operation does not allocate a Task ID. There are no hidden defaults for missing required values.

## Ownership and isolation

The standalone shell composes the existing Requirements command/store boundary directly. It does not load the shared project configuration and does not read a neighbor application's config or database. In particular, its configuration does not require `config/processes`, Tasks state, Execution state, or Sequencing state.

The entire Requirements config is validated before the Requirements database or lock is opened. A second provider invocation with another config therefore uses that config's own Requirements state and does not inherit the first invocation's paths.

## Failure behavior

Configuration and packet validation failures are fail-closed. The provider writes a JSON result with `"status": "rejected"` and exits with `exit 2`. Examples include a missing required top-level field, an unsupported schema, invalid path containment, invalid limits, invalid `task_ids`, oversized stdin beyond `max_input_bytes`, or an unknown operation.

A missing/invalid config is rejected before creation of Requirements database/lock state. It does not fall back to shared configuration or inspect neighbor state.

## Verification

Behavior and isolation are verified through the real executable:

```text
python -B apps/requirements/tests/test_shell.py --suite behavior
```

The behavior suite covers executable discovery, isolated storage with poisoned neighbor config/database sentinels, independent configs, fail-closed required fields, input bounds, `task_ids`, replay stability, and requirement-chain preservation.

Documentation literals are checked with:

```text
python -B apps/requirements/tests/test_shell.py --suite documentation
```

These checks map the standalone Requirements boundary to GEN-03, GEN-04, and REQ-06 and preserve the task DoD: independent configuration/state and co-delivered command/config documentation.
