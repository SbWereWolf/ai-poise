# Stride UAT public contract

Status: target design for `PS-DEV-002-T015` (`M01.T015`).

This is the public boundary of the standalone acceptance-program runner after
extraction. It is not a claim that the target CLI is implemented. The existing
`acceptance/run_pmi.py` and `acceptance/release_evidence.py` supply the baseline
verdict/provenance rules. Application-owned tests remain application-owned.

## 1. Ownership and applicability

Stride UAT owns programme validation, ordered external test execution, its own run
journal/protocol, exact-input attribution, selected-graph qualification records and
readback of its own evidence. It does not own application business assertions,
Requirements statements, Sequencing claims/Workspace, Execution graphs/stages or
Poise Sessions. A UAT run ID is not a Task ID, claim or Execution attempt.

Configured application identifiers are arbitrary. Neither the name `Stride`, a
seven-application roster, twelve legacy cases nor two Python distributions is a
selection rule. An application supplies its acceptance commands and invokes its
own public CLI against isolated test state. UAT never reads a private application
DB to determine PASS. It does not install a release, accept a development Task,
advance another process, or update production configuration as a hidden effect.

A protocol is evidence for its exact programme and subject inputs, not general
approval of a product or of every possible Execution graph. Installation and
publication consume that evidence through their own explicitly authorized owners.

## 2. Invocation and wire protocol

The caller obtains UAT `argv`, environment and its own configuration path from
explicit installation settings. No fixed executable name or mandatory environment
variable is part of the wire protocol. Send one UTF-8 JSON object on stdin and EOF;
stdout contains one JSON object followed by `\n`. Diagnostics use stderr and do
not replace the response. Duplicate keys, nonfinite numbers, invalid UTF-8 and
trailing second objects are `invalid_json`.

Every request has exactly `schema`, `operation`, `input`; `input` is an object.
Unknown fields at any defined object boundary are rejected, not ignored. No
implicit aliases, default profiles or absent/null substitution exist. Booleans are
not integers. IDs match `[A-Za-z0-9][A-Za-z0-9._-]*` within configured byte limits.
A digest is exactly 64 lower-case hexadecimal characters containing SHA-256.

```json
{
  "schema": "stride-uat/v1",
  "operation": "run",
  "input": {
    "run_id": "qualification-42",
    "program_path": "/configured/programs/service-x.json",
    "profile_id": "isolated-linux",
    "step_limit": 0
  }
}
```

`schema` is exactly `stride-uat/v1`. Operations are `validate`, `run`, `resume` and
`inspect`. `step_limit` is an integer >= 0: zero means the whole remaining ordered
programme, a positive value caps newly executed steps for this invocation. It
cannot truncate the programme's expected membership or turn a prefix into PASS.

Input fields, all required and with no others:

| Operation | Input |
| --- | --- |
| `validate` | `program_path: string`, `profile_id: ID` |
| `run` | `run_id: ID`, `program_path: string`, `profile_id: ID`, `step_limit: integer >= 0` |
| `resume` | run fields plus `expected_revision: integer >= 1` |
| `inspect` | `run_id: ID` |

Input paths refer to allowed UAT inputs, not foreign DBs. `validate` reads and
validates programme/configured bindings without running commands, creating a run,
repairing storage or claiming the environment has passed execution preflight.
`inspect` reads a consistent saved protocol and verifies retained evidence digests;
it performs no repair, re-execution, installation or lifecycle transition.

## 3. Configuration and programme

### 3.1 Resolved configuration

The implementation Task owns the physical configuration file format. Its resolved
value MUST include these typed fields; none is inferred from another app's DB or
from inherited host environment:

- `state_root`, `workspace_root`, `input_roots`: absolute paths, with the first two
  distinct and outside declared production resources; `input_roots` is a nonempty
  list of approved read-only input roots.
- `limits`: positive integers `packet_bytes`, `program_bytes`, `steps`, `id_bytes`,
  `argv_items`, `argument_bytes`, `result_bytes`, `stdout_bytes`, `stderr_bytes`,
  `step_timeout_seconds`, `lock_timeout_seconds` and `total_timeout_seconds`.
- `profiles`: ID-keyed objects with `os_ids: nonempty string list`,
  `os_versions: nonempty string list`, `python_versions: list of [major, minor]`,
  `allow_root: boolean`, `isolation_id: ID`, `environment: string-to-string map`.
  Requirements can explicitly include any supported OS/interpreter; no Ubuntu or
  Python version is silently preferred. Observe actual runtime, not profile name.
- `commands`: ID-keyed objects containing `application_id: ID`,
  `argv: nonempty string list`, and `source_inputs: nonempty ID list`.
  The command is an app-owned test adapter; execution is argv-based, not a shell
  command string. No shell expansion, eval or host-shell interpolation occurs.
- `applications`: ID-keyed objects with `public_argv: nonempty string list` and
  `delivery_inputs: nonempty ID list`. This is a caller-supplied public invocation,
  not permission to open that application's private configuration or storage.
- `inputs`: ID-keyed objects containing `kind: file|tree|delivery`, `path: string`
  and declared inventory membership. The selected programme's source/installed
  subject and test-command inputs must all be covered; a version label alone is
  never a content fingerprint.
- `isolations`: ID-keyed approved deployment descriptions with the permitted test
  resource roots, explicit forbidden production resources and launcher invocation.
  The launcher has a documented public interface for argv execution and isolation
  capability checks; UAT does not invent or silently substitute a launcher.

Limits govern mechanism bounds, not case PASS conditions. Changes to relevant
limits/configuration are part of the execution fingerprint. Metadata unrelated to
selected commands/apps/profile does not enlarge the subject's qualification scope.

### 3.2 Programme schema

The programme has exactly these fields:

```json
{
  "schema": "stride-uat-program/v1",
  "program_id": "service-x-acceptance",
  "version": "1",
  "application_ids": ["service-x"],
  "graphs": [
    {
      "id": "approval-only",
      "input_id": "selected-graph",
      "required_step_ids": ["graph-check"]
    }
  ],
  "steps": [
    {
      "id": "graph-check",
      "application_id": "service-x",
      "command_id": "service-x-graph-cases",
      "arguments": ["--context", "${context}"],
      "graph_id": "approval-only",
      "timeout_seconds": 30
    }
  ]
}
```

`version` is a nonempty human version string, not a replacement for the programme
byte digest. `application_ids` is a nonempty unique ID list, each present in
configuration. `steps` is a nonempty ordered list, bounded by `limits.steps`, with
unique IDs. Each step's app belongs to the programme; its command binding must
belong to that exact app. No implicit cross-app command substitution is allowed.
`arguments` is a string list (empty allowed); the only expansion token is the
entire element `${context}`, replaced by the saved absolute input-context file
path. Embedded tokens and unknown tokens are invalid, not shell-expanded.
`timeout_seconds` is positive and cannot exceed the configured ceiling.

`graphs` is an explicitly present list, possibly empty for non-graph programmes.
Each graph has a unique `id`, an `input_id` selecting a declared read-only graph
file, and a nonempty unique `required_step_ids` list. Each required step exists and
names that graph. Every step with non-null `graph_id` names a declared graph and
is included in that graph's required list. A graph file must not be selected under
two IDs. `graph_id: null` is required for a non-graph step. Graph case ordering is
the same as programme ordering; the reference list is a membership declaration.

The graph document is opaque to UAT's domain. Its syntax and runtime behaviour are
validated by app-owned cases through the public Execution contract. UAT validates
its selection, byte identity, required cases and corresponding returned evidence;
it does not become a second process parser or graph execution engine.

## 4. Frozen input identity and run preparation

Under the run lock, validate input/configuration, programme membership, declared
paths and bounds before invoking any command. Record an immutable input snapshot
including programme bytes, profile and observed environment, selected effective
command/app bindings, command source inventories, subject source and installed
bytes, declared delivery inventory, isolation identity and graph bytes/digests.
Resolve all input paths against explicit roots; follow approved input symlinks
only for containment checks. Preserve the actual configured lexical executable
path in argv: resolving a venv Python symlink must not switch its environment.

The fingerprint is SHA-256 of canonical UTF-8 JSON for this snapshot's identity
records: dictionary keys sorted, arrays in contractual order, no extra spaces,
`ensure_ascii=false`, no NaN/Infinity. Tree inventory records are sorted by
relative path and cover file content, ordinary mode bits and symlink targets;
exclusion rules are explicit and themselves frozen. Application version strings
or Git HEAD alone cannot hide modified/untracked source or changed installed
files. Do not hash the mutable run directory into its own subject identity.

Capture these inputs before a new run and re-observe them before each next command
and before publishing programme PASS. Drift fails the current run with
`input_drift`; an already observed external result remains recorded, not relabelled
or erased. Unknown/missing input or incomplete inventory is BLOCKED before command
execution, never a claim of qualification. New code, programme, graph or effective
profile requires a new run ID, not relabelling an old PASS.

A new run creates only its own exclusive workspace and journal. An existing
non-owned workspace is `workspace_conflict`, with no deletion/reset/overwrite.
`run_id` uniquely binds the programme path selection, profile and observed
fingerprint. Exact `run` retry for an existing run returns its saved report with
`replayed: true` and executes nothing. Reusing it for different inputs is
`run_conflict`, including a changed graph. Continuing requires explicit `resume`.
`step_limit` controls invocation work only and is not a subject fingerprint input;
an exact run retry with a different limit still cannot execute more cases.

## 5. Isolation and external case contract

### 5.1 Execution boundary

Allocate each step's test workspace exclusively under the run workspace. Confine
all UAT-written result/stdout/stderr/context paths and reject symlink/path escapes.
The app-owned command receives only explicit configured environment and the saved
context; it must build/use isolated test configuration through the app's public
interfaces. Never copy test DBs over production DBs, replace production config or
fall back to a production resource when a test resource is missing.

Before the first command, the configured deployment/launcher must establish the
selected isolation capability and prove that declared writable bindings do not
alias/overlap forbidden production resources. If that prerequisite is unavailable,
report BLOCKED `isolation_unavailable` with zero commands executed. No `safe:true`
field or arbitrary command claiming PASS is an isolation proof. A plain process
with a different cwd is not an OS sandbox. Hostile test programmes and hidden
network/credential effects require externally enforced confinement; UAT does not
claim it can sandbox arbitrary code by inspecting argv. Selected programme and
adapters are explicitly trusted inputs whose identities are frozen.

### 5.2 Case input

UAT writes exactly this input schema and passes its path as `${context}`:

```json
{
  "schema": "stride-uat-case-input/v1",
  "run_id": "qualification-42",
  "step_id": "graph-check",
  "application_id": "service-x",
  "public_argv": ["/configured/venv/bin/service-x"],
  "workspace": "/isolated/qualification-42/graph-check",
  "result_path": "/isolated/qualification-42/graph-check/result.json",
  "graph": {
    "id": "approval-only",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "path": "/isolated/qualification-42/inputs/approval-only.json"
  }
}
```

`graph` is explicitly null for a non-graph step. Paths/argv above are illustrative
resolved output values, never installed-path constants. Graph input copies are
read-only; neither the adapter nor UAT may silently replace the chosen graph with
the standard development process. Step invocation is configured command argv plus
expanded `arguments`, at the new step workspace, via the configured launcher.

### 5.3 Case output

The app writes one bounded UTF-8 JSON result at the exact `result_path`, using this
schema; required fields are exactly:

```json
{
  "schema": "stride-uat-case-result/v1",
  "run_id": "qualification-42",
  "step_id": "graph-check",
  "application_id": "service-x",
  "status": "PASS",
  "reason": null,
  "graph": {
    "id": "approval-only",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "checks": [
      {"id": "public-graph-setup", "status": "PASS", "evidence_paths": ["setup.json"]},
      {"id": "declared-scenario", "status": "PASS", "evidence_paths": ["scenario.json"]}
    ]
  }
}
```

`status` is `PASS|FAIL|BLOCKED`. `reason` is null for PASS and a nonempty diagnostic
string otherwise. IDs must match the input exactly. `graph` is null iff input graph
is null; otherwise its ID/digest match exactly and `checks` is a nonempty list of
unique `{id,status,evidence_paths}` objects. Check status is `PASS|FAIL|BLOCKED` and
paths form a nonempty unique list of regular files relative to the step workspace,
confined there. Graph PASS requires all supplied checks PASS and the app adapter's
recorded public Execution setup/qualification evidence for that selected graph.
A copied digest is attribution, not evidence that assertions were actually run.
Programme-owned cases specify the necessary scenarios; the runner must not infer
coverage from the name of a check. Provider/consumer qualification of these
adapters remains implementation/UAT work, not a property of this design document.

UAT retains exact result/stdout/stderr and graph evidence bytes and their digests;
output limits are enforced while collecting, not after unlimited buffering. Never
truncate away a failure and then report PASS. The following order is normative:

1. Timeout, signal, output overflow or failed spawn cannot PASS, regardless of a
   leftover result file. Record `FAIL` with the corresponding runner reason.
2. Missing/malformed/oversized/wrong-identity result or graph evidence produces
   `FAIL` (`invalid_case_result`, `evidence_missing` or `graph_mismatch`).
3. `exit_code=0` and valid result `PASS` produce PASS only if graph checks also PASS.
4. `exit_code=3` and valid result `BLOCKED` produce BLOCKED.
5. Every other exit/verdict combination produces FAIL `case_failed`. A result FAIL
   with exit zero is never success; a PASS result with nonzero exit is never PASS.

A terminated process may have changed isolated test resources. Record effects as
`none` only when spawn was never attempted; otherwise use `possible` or `unknown`
as appropriate. Killing a process group is not proof of transaction rollback.
No retry automatically reuses a failed step workspace or unknown prior attempt.
Stable recorded runner reasons are `spawn_failed`, `timeout`, `signal`,
`output_limit`, `invalid_case_result`, `evidence_missing`, `graph_mismatch`,
`graph_checks_failed`, `case_failed` and `input_drift`. A PASS result whose
matched graph contains a non-PASS check is FAIL `graph_checks_failed`. A total
run-time limit terminates any owned active process group and records FAIL
`timeout`; it does not mark a partial programme PASS.

## 6. Protocol, aggregation and resume

### 6.1 Persisted protocol

Own journal schema is `stride-uat-protocol/v1`. Required top-level fields:
`schema`, `run_id`, `revision`, `program_id`, `program_version`, `fingerprint`,
`input_snapshot`, `profile_id`, `environment_observation`, `verdict`, `steps`,
`remaining_step_ids`, `graph_results`, `stop_reason` and `diagnostics`.
`revision` starts at 1 and increments for every durable run transition. No update
mutates another run's protocol or frozen evidence. Journal/pointers are published
atomically under a per-run lock; command side effects are not journal-atomic.

`steps` is an ordered executed prefix of programme steps, without duplicates. Each
entry contains `step_id`, `state: RUNNING|PASS|FAIL|BLOCKED`, `attempt_id`, exact
`argv`, `context_sha256`, `exit_code: integer|null`, `signal: integer|null`,
`timed_out: boolean`, `duration_seconds: number>=0|null`, `effects: none|possible|unknown`,
`reason: string|null`, `outputs` and `graph`. `outputs` is an object with exactly
`result`, `stdout`, `stderr`; each is null until captured, otherwise
`{path:string,sha256:digest,bytes:integer>=0}`. `graph` is null or the captured
result graph with corresponding evidence file records. A RUNNING entry contains
no invented exit code, duration or final output hashes.

`input_snapshot` is the immutable identity object from section 4, with exactly
`program_sha256`, `config_sha256`, `environment_sha256`, `isolation_id`, `inputs`
and `graphs`. The three hashes cover the complete applicable records, not human
version labels. `inputs` is a sorted list of `{input_id, kind, inventory_sha256}`;
`graphs` is a sorted list of `{graph_id, input_id, sha256}`. Full referenced input
inventories are retained under the run and integrity-checked before reuse.
`environment_observation` has exactly `os_id`, `os_version`, `python_version`
(two integers), `uid` (integer or null on an OS without that observation), and
`configured_environment_sha256`. Secret environment values are not published;
the actual explicitly selected values still affect the private fingerprint.
The captured step `graph` uses the case graph shape, replacing each evidence path
with `{path,sha256,bytes}` so retention never relies on a path alone.

Readback verifies schema, exact prefix membership/order, unique attempt IDs, graph
bindings and outcome consistency as well as file hashes. A corrupted duplicate
step cannot replace a missing step; return `evidence_mismatch` without rewriting
the prior journal.

Save a RUNNING intent **before** starting each subprocess, then save observed
completion. If the runtime disappears in between, that entry remains unknown,
even if a plausible result file exists. A reported failure never erases the last
known successful prefix. Start the next case only after a saved PASS and fresh
input check. Stop after FAIL or BLOCKED; unexecuted steps remain explicit.

Programme verdict:
- PASS only when the exact full ordered membership is present, every step is PASS,
  inputs are unchanged, all referenced output bytes match and graph coverage holds.
- FAIL when a case/runner/input-drift failure has occurred; no later case is started.
- BLOCKED for an established environment/isolation prerequisite failure or a case
  with the matching BLOCKED exit/result; this never authorizes release.
- RUNNING for a deliberately paused all-PASS prefix or an unresolved RUNNING intent;
  distinguish these through `stop_reason` (`step_limit` versus `unknown_attempt`).

`remaining_step_ids` is the exact unexecuted suffix in programme order.
`stop_reason` is null only for PASS, otherwise the stable reason. `diagnostics` is
a string list, empty allowed, redacted. Evidence damage discovered by `inspect`
is returned as a rejection, not silently rewritten into historical PASS/FAIL.

### 6.2 Resume contract

`resume` holds the run lock and compares `expected_revision` before mutation. It
revalidates current programme/profile and entire input fingerprint, confirms the
same owned workspace/step identities and re-hashes all retained completed evidence.
A complete unchanged PASS returns the existing report without execution. A known
quiescent RUNNING prefix stopped at `step_limit`, with every completed step PASS,
continues at the first unexecuted step. It never re-executes the completed prefix.

An old RUNNING step means `unknown_attempt`: refuse without invoking the command
again. Any prior FAIL/BLOCKED means `run_not_resumable`; corrected inputs use a new
run ID. A changed input means `input_mismatch`; altered evidence means
`evidence_mismatch`. Preserve both old protocol and current inputs on rejection.
No clone/rename of PASS is qualification of new inputs. Restoring an exact stopped
run to another installation requires an explicit compatible binding; arbitrary
argv/environment/path drift is not accepted as equality.

### 6.3 Graph qualification

Graph result object contains exactly `graph_id`, `graph_sha256`,
`required_step_ids`, `passed_step_ids`, `verdict`. Graph verdict is PASS only when
the programme itself is complete PASS and every declared required step contributes
matching graph evidence with passing checks. If the programme FAILs/BLOCKs, graph
verdict is FAIL/BLOCKED respectively; an unfinished prefix gives RUNNING, even if
one graph case has already passed. This conservative whole-program policy avoids
publishing a graph certificate from incomplete acceptance.

`graphs: []` yields `graph_results: []` and explicitly **no graph qualification**.
A new or edited graph requires a new fingerprint/run even with the same logical
graph ID. Each required app-owned scenario must actually exercise the supplied
public Execution graph; fixed development-process tests are not substitutes.
UAT does not mutate graph definitions or execute stages on the app's behalf.

## 7. Response and stable errors

Run/resume/inspect reported response has exactly these fields:

```json
{
  "schema": "stride-uat/v1",
  "status": "reported",
  "run_id": "qualification-42",
  "revision": 4,
  "verdict": "PASS",
  "protocol_path": "/configured/uat-state/qualification-42/protocol.json",
  "fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "completed_steps": 1,
  "total_steps": 1,
  "graph_results": [
    {
      "graph_id": "approval-only",
      "graph_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "required_step_ids": ["graph-check"],
      "passed_step_ids": ["graph-check"],
      "verdict": "PASS"
    }
  ],
  "replayed": false
}
```

`completed_steps` counts entries no longer RUNNING, not successful assertions;
`total_steps` is the complete frozen programme size. `replayed` is false for new
work/inspect and true for an exact run retry or already-complete resume.
Reported process exit: PASS `0`, FAIL `1`, BLOCKED/RUNNING `3`. `inspect` uses that
same mapping, so a consumer must not equate a successfully read FAIL with exit 0.
`protocol_path` is resolved operational output, not a globally portable identity.

`validate` success is exactly `{schema, status:"validated", program_id,
program_sha256, profile_id, step_count, graph_count}` with exit0. It does not issue
PASS or a run fingerprint based on unobserved environment.

```json
{
  "schema": "stride-uat/v1",
  "status": "rejected",
  "error": {
    "code": "input_mismatch",
    "message": "Frozen inputs differ; use a new run",
    "retryable": false,
    "details": {"run_id": "qualification-42", "changed": ["graph:approval-only"]}
  }
}
```

Rejections have exactly `schema,status,error`; error has exactly
`code,message,retryable,details`. Exit is `2`. Branch on `code`, not diagnostic
message. `details` is a JSON object whose following code-specific fields are
required; no secrets/foreign DB contents are exposed.

| Code | Required details | Meaning / effects of rejected invocation |
| --- | --- | --- |
| `invalid_json` | `{}` | Invalid UTF-8/JSON before command work |
| `unsupported_schema` | `{schema:string|null}` | Unknown wire or programme version |
| `invalid_packet` | `{path:string}` | Unknown/missing/type-invalid packet field |
| `program_invalid` | `{issues:[{path:string,code:string}]}` | Programme/graph membership or binding invalid |
| `limit_exceeded` | `{limit:string}` | Input/configured size limit before command work |
| `not_found` | `{run_id:ID}` | Unknown run; no repair/create |
| `run_conflict` | `{run_id:ID}` | Existing identity selects different frozen inputs |
| `workspace_conflict` | `{path:string}` | Existing/foreign/escaping output binding |
| `revision_conflict` | `{expected:integer,actual:integer}` | Stale resume revision |
| `input_mismatch` | `{run_id:ID,changed:string[]}` | Changed frozen inputs on resume |
| `evidence_mismatch` | `{run_id:ID,step_ids:ID[]}` | Retained evidence missing or altered |
| `unknown_attempt` | `{run_id:ID,step_id:ID}` | Prior RUNNING step has unresolved effects |
| `run_not_resumable` | `{run_id:ID,verdict:string}` | Existing failed/blocked run |
| `store_busy` | `{run_id:ID}` | Bounded run lock acquisition failed |
| `store_incompatible` | `{schema:string|null}` | Unknown UAT journal; no automatic migration |

Only `store_busy` is retryable. The code order before execution is decoding,
envelope/version/type checks, programme/binding checks, lock, existing-run/revision,
fingerprint and evidence, resume eligibility, execution. Environment/isolation
failures after valid new-run preparation produce a persisted BLOCKED report, not a
preparation rejection. Zero commands in an isolation-blocked report means zero test
commands, not a fabricated absence of read-only capability probes; any probe is
explicitly recorded. A wrong configured binding is `program_invalid` with a
`config.*` issue path. Failures after a subprocess starts are persisted outcomes
or recoverable unknown intents, never a claim that no external effects occurred.
A journal I/O loss that prevents a normal response may end abnormally; recovery
must inspect saved intent instead of assuming an absent response means no work.

## 8. Independent literal fixtures

These expected vectors are hand-authored, not produced by application serializers.
They test the stated contract, not a target implementation that does not yet exist.

### F1 — verdict/exit conjunction

```json
[
  {"exit": 0, "result": "PASS", "valid": true, "timeout": false, "expected": "PASS"},
  {"exit": 0, "result": "FAIL", "valid": true, "timeout": false, "expected": "FAIL"},
  {"exit": 1, "result": "PASS", "valid": true, "timeout": false, "expected": "FAIL"},
  {"exit": 3, "result": "BLOCKED", "valid": true, "timeout": false, "expected": "BLOCKED"},
  {"exit": 0, "result": "BLOCKED", "valid": true, "timeout": false, "expected": "FAIL"},
  {"exit": 0, "result": "PASS", "valid": false, "timeout": false, "expected": "FAIL"},
  {"exit": 0, "result": "PASS", "valid": true, "timeout": true, "expected": "FAIL"}
]
```

### F2 — complete ordered membership, never a renamed prefix

```json
[
  {"expected_ids": ["A", "B"], "observed_ids": ["A"], "states": ["PASS"], "verdict": "RUNNING", "remaining": ["B"]},
  {"expected_ids": ["A", "B"], "observed_ids": ["A", "B"], "states": ["PASS", "PASS"], "verdict": "PASS", "remaining": []},
  {"expected_ids": ["A", "B"], "observed_ids": ["A", "B"], "states": ["PASS", "BLOCKED"], "verdict": "BLOCKED", "remaining": []},
  {"expected_ids": ["A", "B"], "observed_ids": ["A", "A"], "states": ["PASS", "PASS"], "error": "evidence_mismatch"}
]
```

### F3 — resume is exact and does not repeat completed steps

```json
[
  {"inputs_equal": true, "evidence_equal": true, "states": ["PASS"], "stop_reason": "step_limit", "expected": "CONTINUE_SUFFIX"},
  {"inputs_equal": false, "evidence_equal": true, "states": ["PASS"], "stop_reason": "step_limit", "expected": "input_mismatch"},
  {"inputs_equal": true, "evidence_equal": false, "states": ["PASS"], "stop_reason": "step_limit", "expected": "evidence_mismatch"},
  {"inputs_equal": true, "evidence_equal": true, "states": ["RUNNING"], "stop_reason": "unknown_attempt", "expected": "unknown_attempt"},
  {"inputs_equal": true, "evidence_equal": true, "states": ["FAIL"], "stop_reason": "case_failed", "expected": "run_not_resumable"}
]
```

### F4 — selected graph is not the default graph

Given graph `approval-only` with digest `aaa...` and required case `graph-check`,
returning PASS for graph `development`/`ccc...` yields case FAIL `graph_mismatch`.
No certificate for `approval-only` exists. Editing bytes while retaining its logical
ID changes the fingerprint: resume rejects `input_mismatch`, leaving old PASS intact.
A non-graph programme can PASS but must return an empty graph-results list.

### F5 — arbitrary application and explicit command

The programme example selects `service-x`; no Stride-named application exists.
Configured command `service-x-graph-cases` receives the actual saved context path
and public argv. It can PASS with valid evidence. Referencing a command bound to
`another-service` is `program_invalid` before execution; no implicit name fallback.
Changing public argv or adapter source between runs changes frozen inputs.

### F6 — isolation failure never touches production

With a valid programme but test-resource binding resolving to a forbidden production
root, prepare the own run and report BLOCKED `isolation_unavailable`, `steps: []`.
Do not invoke a command, copy a DB, migrate schema, replace configuration or cancel a
Task. The same BLOCKED applies if the configured launcher capability is unavailable.
An arbitrary cwd change alone does not satisfy the isolation precondition.

### F7 — external failure is not rollback

A spawned case times out after writing in its isolated workspace. Record FAIL,
`timed_out: true`, `effects: unknown`; never PASS even if result.json says PASS.
Resume is `run_not_resumable`. Crash after saved RUNNING intent without confirmed
completion instead yields `unknown_attempt`, with no automatic retry or reset.

### F8 — immutable exact retry versus explicit resume

Exact `run` retry at a known `step_limit` pause returns the saved RUNNING report,
`replayed: true`, no command. Explicit `resume` at the saved revision executes the
unexecuted suffix. Two concurrent resumes at one expected revision cannot both
execute it: one owns the lock, the later request sees `revision_conflict` (or
`store_busy` only if it actually times out while acquiring the lock).

### F9 — retained output and installed bytes are part of proof

Change result/stdout/stderr or graph-evidence bytes after capture: inspect/resume
rejects `evidence_mismatch` without editing the old journal. Change a declared
installed file, source mode or symlink target without changing version/HEAD:
resume rejects `input_mismatch`; the old PASS still describes only old inputs.

### F10 — no hidden release or foreign authority

After a complete PASS, only the UAT journal and isolated test resources changed.
No application was deployed, no development Task accepted, no Session/claim created,
no private foreign DB read. A consumer must separately check the exact programme,
subject/graph identity and evidence before its own authorized publication action.

## 9. Impact and requirement mapping

Implementation affects the future `apps/stride-uat/**` CLI/config/runner and app-owned
acceptance adapters. Preserve the existing honest verdict, immutable-fingerprint,
ordered-membership and hashed-evidence behaviour. Remove fixed application/case
selection only through the later extraction; do not clone app domain engines.

Execution remains owner of process parsing/runtime and selected-graph setup;
Bootstrap remains owner of its infrastructure/delivery maintenance; Orchestrator
coordinates public owner calls. UAT reads their explicitly supplied public values,
not private stores. Current CLI `acceptance/run_pmi.py` is not silently aliased to
this new version. Translation of legacy programme/result schemas is explicit
adapter work with contract tests, not permissive mixed v1 parsing.

Risks: incomplete source inventory; adapter result echo without actual assertions;
launcher isolation weaker than declared; changed executable path escaping a venv;
unknown effects after interrupted RUNNING; graph scenarios insufficient for their
stated qualification. All need executable producer/consumer qualification during
implementation, not a false green based on this document.

| Requirement | Evidence |
| --- | --- |
| R01 / UAT-01 | §1–3, §5, F5: configured app-owned external commands, arbitrary app IDs |
| R02 / UAT-02 | §4–6, F1/F2/F3/F6/F7/F9: exact-input truthful protocol, isolated state, no old PASS reuse |
| R03 / EXE-02 | §3.2, §5.3, §6.3, F4: qualification of the exact supplied graph and required cases |
| R04 / GEN-03 | §1, §5.1, F10: UAT-owned protocol, no private cross-store authority |
| R05 / GEN-04 | §2–5, F5/F6: configured argv/env/paths/profiles/limits, no fixed roster |

Both DoD clauses: schemas and literal success/error/replay paths are defined here;
no broker/distributed transaction or second authority is introduced. This Task
changes only this allowed contract document, co-delivering intended configuration
and invocation semantics. No executable/config change is left undocumented.

Related accepted boundaries: [Execution](../../execution/docs/contract-exe.md),
[Orchestrator](../../orchestrator/docs/contract-orc.md),
[Bootstrap](../../bootstrap/docs/contract-bst.md).
