# Standalone Orchestrator shell

T021 introduces an app-owned Orchestrator process boundary. It does **not** make
`poise work` the Orchestrator and it does not move future Role/Session/yield
semantics into this task.

## Command and configuration

Run the provider directly:

```text
apps/orchestrator/bin/orchestrator --config apps/orchestrator/config/orchestrator.json
```

The configuration schema is `stride-orchestrator-config/v1`. The configuration
is strict and fail-closed: required keys must be present, unknown keys are
rejected, and operational limits are explicit. In particular the shell reads
`max_input_bytes`, bounded query/item limits, lock timing, and the `commands`
map from its own configuration; it does not fall back to a shared project
configuration.

Each configured public command is an `owner-json-v1` descriptor with explicit
`argv`, `cwd`, `env`, `timeout_seconds`, and accepted exit codes. Replacing a
peer command therefore requires a config change, not an Orchestrator source
edit.

## State ownership and isolation

Orchestrator owns only its configured state directory, database, and lock. The
example config uses `orchestrator.sqlite` and `orchestrator.lock`. It does not
open the private databases of Requirements, Sequencing, or Execution.

Cross-application work is performed only through a configured **public command**
descriptor. The provider starts that command as a child process and exchanges
one JSON packet over the declared `owner-json-v1` protocol; it does not import
peer application internals.

Missing required config, invalid descriptors, input/query limits, command
startup failures, timeouts, unexpected child exit codes, and replay conflicts
are explicit rejected results. They are not replaced by hidden defaults.

## Current T021 slice

The T021 shell slice implements synchronous `invoke` plus local `query` of the
configured command catalogue. Successful invocations are recorded in the
Orchestrator-owned ledger. Repeating the same request produces exact replay
without invoking the child again; conflicting reuse of the request id is
rejected.

This slice deliberately does **not** implement the later Role × Command matrix,
Session/Role resolution, `yield`/resume/deferred scheduling, or async ordering.
Those remain future Orchestrator work and are specified in `contract-orc.md`.

## Verification

The app-local checks are:

```text
python -B apps/orchestrator/tests/test_shell.py --suite behavior
python -B apps/orchestrator/tests/test_shell.py --suite documentation
```

The behavior suite proves standalone config/state ownership, configured public
command replacement, fail-closed validation, isolation from peer private state,
and replay. The documentation suite checks the shell command, config contract,
ownership/public-command boundary, `owner-json-v1`, and the explicit future
Role, Session, and yield slices.
