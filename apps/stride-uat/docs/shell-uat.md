# Standalone Stride UAT shell

T023 establishes a standalone **Stride UAT** process boundary. It does not turn
the legacy acceptance scripts or another application's runtime into the UAT owner.

## Command and configuration

Run the app-owned provider directly:

```text
apps/stride-uat/bin/stride-uat --config apps/stride-uat/config/uat.json
```

The physical configuration schema is `stride-uat-config/v1`. The configuration is
strict and fail-closed. It owns `state_root`, `workspace_root`, `input_roots`, the
explicit `limits` object (including `packet_bytes`), and the configured maps for
profiles, commands, applications, inputs, and isolations. Relative roots resolve
from the config file rather than process cwd. Missing/unknown fields, invalid IDs,
invalid JSON, and configured input-limit violations are rejected explicitly; no
hidden project configuration or host default fills them in.

## Ownership boundary

The provider owns only its configured UAT **state** and workspace/input roots.
It does not open the private databases or configuration of Requirements,
Sequencing, Execution, Poise, or another app. Cross-application acceptance work
must use the relevant public interfaces described by `contract-uat.md`.

The current T023 slice supplies strict packet/config decoding plus the read-only
`inspect`/`not_found` shell foundation. It deliberately does not claim that a
programme has run or passed. Existing protocol material is not silently accepted
as PASS by this foundation.

## Future slices

**T029** adds the configurable programme/test-command runner for arbitrary
application-owned commands. **T030** adds isolation, resume and evidence/protocol
integrity. Those tasks extend this shell instead of being hidden inside T023.

## Verification

The app-local checks are:

```text
python -B apps/stride-uat/tests/test_shell.py --suite behavior
python -B apps/stride-uat/tests/test_shell.py --suite documentation
```

The behavior suite checks the standalone provider, two independent configurations,
UAT-owned roots, peer-private poison preservation, required config fields, packet
bounds and fail-closed packet parsing. The documentation suite checks this command,
configuration/ownership boundary and the explicit T029/T030 deferral.
