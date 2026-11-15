# Poise public contract

Действующие создание Session, первое назначение роли и чтение статуса через
`bin/poise` описаны в [операторском договоре Session](shell-poi.md).
Он владеет текущими конфигурацией, caller, схемами пакетов и ответов, ошибками
и порядком использования. Подготовка собственного состояния через `bin/poise-state`
описана в [договоре состояния](state-poi.md). Общие контекст, навыки, проверки
возможностей и навигация через отдельный `bin/poise-generic` используют собственные
конфигурацию и схему `poise-generic/v1` из [своего руководства](generic-poi.md).

События и синхронный публичный внешний вызов через `bin/poise` имеют
[действующий договор hooks](hooks-poi.md), включая собственное состояние,
идентичность события, повтор, advisory наблюдения и полный результат.

Самостоятельные `reader.context/reset/read/next` через `bin/poise` имеют
[действующий договор reader/2](poi-file-contract.md): собственные Session, конфигурация,
порции, продолжения, ACK и метаданные версии.

Прямые `paths.evaluate`, `files.write` и подключение общей политики к reader
имеют [действующий файловый договор](poi-fs-contract.md). Их конфигурация, точные
ошибки, частичная запись и самостоятельный запуск определены там.

Прямые `command.request/completion/delete`, локальные команды и MCP stdio имеют
[действующий договор команд](poi-command-contract.md): собственные настройки,
сохранённая идентичность, полные потоки и фактические исходы.

Самостоятельные `output.minimal` и `output.range` имеют
[действующий договор чтения сохранённого вывода](poi-output-contract.md):
метаданные и точные байтовые порции по сохранённым адресам без текущей Task.

Ниже сохранён исторический целевой проект T016. Его более широкий договор включает
ещё отдельные области generic gateway и закрытия Session; его прежний проект
представлений вывода также остаётся историческим материалом.
Эти описания сами по себе не подтверждают поставку исполняемых операций.
Для реализованных Session, роли, статуса, hooks, reader, файловых, командных операций
и чтения сохранённого вывода
текущие операторские договоры выше
заменяют соответствующие ранние предложения этого проекта, включая отличающиеся
поля, условия допуска и проверки. Существующие нумерованные разделы сохранены
как проектный материал для оставшихся областей.

## 1. Ownership and independence

Poise owns caller-to-Session establishment, the Session's first role assignment,
Session status and provenance, generic tool admission/result references, its own
configuration/store and optional generic observations. It is usable without an
installed/running Stride, Task, Stage, Workspace or any Stride application DB.

Requirements owns canonical Task statements; Sequencing owns Task claims and
workspaces; Execution owns process stages/checks and their outcomes. Those owners
are not dependencies of Session creation or a generic file/command tool. Poise does
not invent a Task for a tool call, use a stage as its Session role, read a private
Task DB to decide permission, or synthesize acceptance/completion from tool output.

The caller may invoke Poise tools directly. An orchestrator is an optional caller,
not a mandatory gateway. A Poise Session or matching role string is not proof of
an independent reviewer, a native host event or authorization over a foreign app.
A configured `agent_id` is a profile, not a new actor. Changing a copied launcher,
role label or transport name cannot create a distinct effective Session.

## 2. Caller identity and trust boundary

Reuse the common Session establishment rules, independent of Task composition:

- A validated host-native identity or owner-generated persistent caller binding
  selects one effective Session within a configured Poise installation namespace.
- Validate conflicting native signals before any mutation. The agent cannot supply
  a replacement `session_id` in a work packet, impersonate another native caller,
  or reuse an archived binding as authority in another host session.
- An existing generated assignment is reused, even if a native ID becomes available
  later for that same binding. Do not silently replace Session identity or role.
- Generation/reservation is atomic with collision detection; two different callers
  cannot share a generated ID merely because their generators collide.
- Identity origin is immutable provenance, not a grant of a different tool surface.
  Native and generated callers are subject to the same configured policies.

An opaque Session ID is not a bearer credential. Every operation below acts on the
Session resolved from the current authenticated caller. Operator-authorized role
assignment uses a configured trusted authorization adapter; an arbitrary string
saying “operator approved” is insufficient. The adapter verifies the reference for
the exact subject Session, role and operation and returns bounded explicit proof.
Its transport/configuration belongs to the installation; no mandatory global
variable or hard-coded approval service is introduced by this wire contract.

Read-only operations resolve an existing Session and never create one implicitly.
Only `session.ensure` can establish it. A genuinely new host/caller Session starts
with no role; it does not inherit role, Task, provider credentials or approvals
from the preceding conversation or a recovery archive.

## 3. Transport and configured dependencies

The caller obtains Poise executable argv and Poise configuration path explicitly.
Send exactly one UTF-8 JSON object on stdin and EOF; return exactly one JSON object
and a terminating newline on stdout. Diagnostics belong to stderr. The envelope
has exactly `schema`, `operation` and `input`, with `schema="stride-poise/v1"` and
object input. Unknown fields, missing fields, duplicate JSON keys, nonfinite
numbers and ambiguous aliases are rejected, not coerced. Boolean is not integer.

A configured public tool's own schema remains its owner's contract. Poise validates
its envelope and the exact advertised tool binding; it does not replace provider
inputs/results with a new Task-specific model. Generic tool payloads cannot be
silently interpreted as Session identity, role approval or Task completion.

Resolved Poise configuration must explicitly provide:

| Field | Type and meaning |
| --- | --- |
| `installation_id` | Nonempty installation namespace, not a Task/project requirement |
| `state_root`, `session_store`, `lock_path`, `results_root` | Own absolute local paths; no peer-store fallback |
| `caller` | Validated native adapter or persistent generated-binding adapter configuration |
| `roles` | Unique nonempty role ID list; the initial unknown role is JSON null, never a default list entry |
| `authorization` | Trusted role-authorization adapter binding and its timeout/output limits |
| `tools` | ID-keyed versioned tool descriptors as section 6; arbitrary configured IDs |
| `limits` | Positive packet/result/input bytes, ID bytes, batch count, lock timeout/poll and provider invocation limits |
| `paths` | Configured read/write allow/deny policy delegated to the generic path owner, with exact public policy version |
| `environment` | Explicit allowed environment/binding policy; no implicit Stride variables |

The configuration owner validates full candidate/revision before publication.
Ordinary policy repair is not forbidden merely because an unfinished Task exists.
Real storage or repository rebinding retains its corresponding owner guard. No
Poise operation below performs implicit schema migration, installation or repair.

Identifiers are nonempty configured-bounded UTF-8 strings excluding NUL, slash,
backslash, `.` and `..`. IDs are values, not user-chosen filesystem paths. Revision
is an integer >= 1. Digest means exactly 64 lower-case SHA-256 hex characters.
Request payload digests use canonical UTF-8 JSON: sorted object keys, contractual
array order, no extra whitespace, no ASCII escaping, no nonfinite numbers.

## 4. Session model and public operations

### 4.1 Session value

Every Session response contains this exact object:

```json
{
  "id": "session-a",
  "revision": 1,
  "status": "open",
  "role": null,
  "origin": "generated",
  "role_assignment": null
}
```

Fields are `id: ID`, `revision`, `status: open|closed`, `role: ID|null`,
`origin: native|generated`, `role_assignment: object|null`. Both role fields are
null together. After assignment, `role_assignment` has exactly
`request_id: ID`, `authorization_ref: ID`, `assigned_revision: integer` and
`proof_digest: digest`. No private credential or authorization secret is returned.
`origin` and ID never change. First role assignment and close increment the Session
revision; tool result progress does not overwrite its role or Session revision.

### 4.2 `session.ensure`

```json
{"schema":"stride-poise/v1","operation":"session.ensure","input":{}}
```

Create the resolved caller's Session once, with revision1/open/null role. Existing
assignment returns the same Session without mutation; even a closed Session is
returned closed, not reopened or replaced. A new Session requires a genuinely new
validated caller, not a caller-selected desired ID. This operation does not create
Task, Sprint, Stage or Workspace records and cannot require their stores.

Response fields exactly: `schema`, `status: session_ready`, `session`,
`created: boolean`. Exit0 means the own Session was returned, not that a role or
Task is available. A query can subsequently observe newer Session state.

### 4.3 `session.assign_role`

```json
{
  "schema": "stride-poise/v1",
  "operation": "session.assign_role",
  "input": {
    "request_id": "assign-author-1",
    "expected_revision": 1,
    "role": "author",
    "authorization_ref": "operator-decision-91"
  }
}
```

All four input fields are required. `role` must be one configured role and cannot
be null. The authorization adapter must affirm the current Session/role/operation;
Poise stores only its verified provenance/digest. A missing/denied/unknown proof
cannot establish the role. Native identity, agent profile, selected tool or
foreign Task's stage is not implicit operator direction.

Under one own-store lock/transaction:

1. Resolve/authenticate the caller, validate fields and the configured role, and
   validate authorization for the exact request subject.
2. Check request replay. Same Session/request ID with different canonical payload
   is `request_reuse_conflict`; exact saved success returns its original response
   with `replayed:true` and performs no second assignment.
3. Compare `expected_revision`. A stale value is `revision_conflict` before checking
   the requested role against the current locked role.
4. Require open Session. Closed is `session_closed`.
5. If current role is null, set it once, store the assignment proof and advance
   revision. If the role already equals the requested role, return `changed:false`
   without replacing the original assignment proof or advancing the revision.
6. A different existing role is `role_locked`. Preserve role, revision, original
   proof and identity; no hidden reset, rename, new Session or role migration.

No command can clear/reassign the role. Config changes cannot silently remap a saved
role. An absent old role in a newer vocabulary remains readable as historical
identity; admission uses explicit current policy and does not rename it.

```json
{
  "schema": "stride-poise/v1",
  "status": "role_assigned",
  "session": {
    "id": "session-a",
    "revision": 2,
    "status": "open",
    "role": "author",
    "origin": "generated",
    "role_assignment": {
      "request_id": "assign-author-1",
      "authorization_ref": "operator-decision-91",
      "assigned_revision": 2,
      "proof_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  },
  "changed": true,
  "replayed": false
}
```

Two first-assignment requests at revision1 cannot both win. After the first commits,
the serialized loser gets `revision_conflict`. A later current-revision request
for another role gets `role_locked`. A lock timeout is independently `store_busy`,
not a second successful assignment. Exact replay responses describe their saved
revision; use `session.status` for the current value.

### 4.4 `session.status`

Input exactly `{}`. Response exactly `{schema,status:"read_only",session,
active_request_ids:ID[]}`. Request IDs refer only to this caller's generic Poise
requests, sorted lexicographically; they are not Task IDs. Read one consistent
own-store snapshot. A missing Session gives `session_not_found`, without creation.
No foreign Task status, process stage, Workspace path or claim is fabricated.

### 4.5 `session.close`

Input exactly `{request_id:ID, expected_revision:integer, reason:nonempty string}`.
Close only the current Session with known revision, subject to configured caller
permission. The same replay/conflict and revision rules apply. Response exactly
`{schema,status:"session_closed",session,changed:boolean,replayed:boolean}`.
First close advances revision; already-closed current-revision close is unchanged.
The first role remains immutable and visible after close.

Close prevents new role/tool admission; it does not kill a provider, delete results,
release a Sequencing claim, accept a Task or invent a host SessionEnd event. Already
admitted generic requests may finish and record their result under the closed
Session. Existing request status/readback remains available. Admission and closing
serialize under the same Session lock so a new request is either admitted before
close or rejected, never silently started afterward. Cancellation/reconciliation
of actual external work belongs to its explicit provider operation, not this call.

## 5. Direct tools and request status

### 5.1 `tools.list`

Input exactly `{}`. Returns exactly `{schema,status:"read_only",session,tools:[...]}`
with descriptors sorted by `tool_id`. It requires the existing caller Session, not
Stride or a Task. The list is availability/configuration evidence, not a claim a
provider invocation has succeeded. A disabled/unavailable tool is distinguishable
from a forbidden tool; availability never grants permission.

### 5.2 `tool.invoke`

```json
{
  "schema": "stride-poise/v1",
  "operation": "tool.invoke",
  "input": {
    "request_id": "read-direct-1",
    "tool_id": "source.reader",
    "tool_schema": "source-reader-cli/1",
    "arguments": {
      "operation": "context",
      "input": {}
    }
  }
}
```

Required input fields are exactly `request_id`, `tool_id`, `tool_schema` and
`arguments: object`. The tool ID/schema are matched to one configured descriptor;
the example names a target adapter binding, not a claim of an already installed
standalone adapter. For that binding, the app-independent current source reader
has public operations `context`, `read`, `reset`; its own parser validates them.
No `task_id`, `stage_id`, `workspace_id`, desired Session ID or role override is
required or interpreted by Poise's gateway. A provider may use business identifiers
inside its own public arguments; that does not give Poise private app ownership.

Admission checks the current Session, tool schema/availability, explicit permission
and provider limits before any invocation. `role_policy:any` admits null role if
all other permissions allow it. `role_policy:one_of` requires a non-null saved
role in the declared list. No implicit first-role selection occurs on tool use.
An authorization reference for role assignment is not tool execution permission.

Own request identity is `(session_id, request_id)` bound to the canonical input and
selected provider definition revision/digest. Reusing it with another payload gives
`request_reuse_conflict`. Exact retry returns the existing admission/result state,
not another file write/process launch. If the first invocation is unresolved, return
its RUNNING/UNKNOWN state; do not infer success or retry external effects. A new
request is not permission to repeat an unresolved side effect without its owner.

The own request record is persisted before external dispatch; a crash between
admission, execution and response leaves explicit uncertainty. Provider I/O is not
an atomic Session DB transaction. Admission never directly reads or writes a peer
application DB; selected providers operate through their declared public boundary.

### 5.3 `request.status`

Input exactly `{request_id:ID}`. Reads the current caller's request and retained
result references without launching a tool or creating a request. Unknown identity
is `request_not_found`; another Session's ID is not a cross-session access token.
A closed Session can read its existing requests. It does not reuse archived native
identity to acquire another caller's work.

Both invocation and status use the same exact response fields:

```json
{
  "schema": "stride-poise/v1",
  "status": "tool_response",
  "session_id": "session-a",
  "request_id": "read-direct-1",
  "request_revision": 2,
  "tool_id": "source.reader",
  "tool_schema": "source-reader-cli/1",
  "state": "SUCCEEDED",
  "provider_exit_code": 0,
  "result": {
    "generation": 1,
    "reason": "startup",
    "event_id": "startup-1"
  },
  "result_ref": null,
  "error": null,
  "replayed": false
}
```

State is `ADMITTED|RUNNING|SUCCEEDED|FAILED|UNKNOWN`. `request_revision` starts1 and
increases for durable own-request updates. `provider_exit_code` is integer or null;
null means unobserved, never zero. `result` is provider JSON object or null;
`result_ref` is null or `{path:string,sha256:digest,bytes:integer>=0}` pointing to
Poise-owned full output. At most one of inline result or full-result reference is
non-null. The provider's exact payload is preserved; no mandatory rewritten
summary replaces it. `error` is null or `{code,message,details}` with string code,
string message and object details. `replayed` indicates invocation replay, not
provider re-execution or proof of idempotency of an external command.

A provider's declared successful transport/completion yields SUCCEEDED only if its
response validates against the exact registered schema. Nonzero exit, malformed
result or execution failure yields FAILED. Timeout or transport loss after possible
dispatch yields UNKNOWN when terminal effects cannot be observed; a timeout alone
must not fabricate a provider exit code. Partial output is retained with error
provenance. Failed spawn before dispatch is FAILED `provider_start_failed` and
cannot be reported as an executed command. Provider-owned asynchronous, cancellation,
output-range and reconciliation details remain T068–T070; this envelope does not
invent a second scheduler or claim those target APIs are already implemented.

Poise delivery success is not business success: a well-delivered provider JSON
payload containing an app verdict FAIL remains visible as FAIL. Consumers check
the provider's declared outcome, not merely the gateway status or process exit.
This is particularly important for UAT protocols and Execution checks.

## 6. Tool descriptor and generic boundary examples

A descriptor returned by `tools.list` has exactly:
`tool_id:ID`, `tool_schema:nonempty string`, `definition_revision:nonempty string`,
`definition_digest:digest`, `input_schema:object`, `output_schema:object`,
`effects:read_only|local_write|external`, `role_policy:any|one_of`,
`allowed_roles:ID[]`, `available:boolean`, `availability_reason:string|null`.
Schemas are complete declared JSON-schema objects, not a serializer import or
undocumented ad-hoc payload shape. `one_of` requires a nonempty unique role list;
`any` requires an empty list. An unavailable descriptor has a nonempty reason;
available has null. Per-tool executable argv/env, resource restrictions and bounds
come from the validated own configuration and are frozen in request provenance;
secret values are never exposed by descriptor/status output.

A file read binding delegates the existing bounded reader rather than copying it:
explicit cwd/range/generation, acknowledgement of actual receipts, all-input
validation before receipt mutation, and no false suppression of unacknowledged
text. Current `source-read-result-1` remains a provider result.
Действующий [reader/2](poi-file-contract.md) исполняет строки, байты, продолжение
и ACK через собственный `bin/poise`. Описанный здесь широкий gateway остаётся
проектным материалом; его подключение имеет отдельную область разработки.
A Poise Session does not imply
model memory or authorization over every filesystem root.

A generic command binding uses explicit argv/cwd/env and configured budgets, writes
results under its own request storage and retains actual exit/output metadata.
It does not require a Task/Stage/Workspace or compute a Task verdict. Bounds and
real process cancellation belong to the provider; a timeout is not transaction
rollback. No shell interpolation is performed by the gateway. A programme choosing
a shell does so as an explicit permitted provider input, not hidden interpolation.

These two capabilities must be reachable directly on an isolated Poise installation
where Stride is absent. Test them through the real provider boundary at extraction;
a mock echoing its own fixture does not prove independence. This design specifies
that qualification obligation without asserting it has already been implemented.

## 7. Errors, concurrency and safety

Non-tool Session/list responses have exit0. A rejected request has exit2 and exactly:

```json
{
  "schema": "stride-poise/v1",
  "status": "rejected",
  "error": {
    "code": "role_locked",
    "message": "Session role was already assigned",
    "retryable": false,
    "details": {"current_role": "author", "requested_role": "reviewer"}
  }
}
```

Error object fields are exactly `code,message,retryable,details`. Messages are
human-facing; callers branch on code. Rejection does not assign a role, launch a
provider, alter a foreign store or fabricate a native event.

Tool response exit: SUCCEEDED `0`, FAILED `1`, ADMITTED/RUNNING/UNKNOWN `3`.
These are transport/request outcomes, never substituted for provider business
verdicts. Unavailable tools reject before dispatch; a tool provider returning a
business denial may be a successfully delivered own-schema denial.

| Code | Details required | Meaning |
| --- | --- | --- |
| `invalid_json` | `{}` | Invalid UTF-8/JSON, duplicate keys or nonfinite values |
| `unsupported_schema` | `{schema:string|null}` | Unknown Poise envelope version |
| `invalid_packet` | `{path:string}` | Missing/extra/type-invalid field |
| `caller_unavailable` | `{}` | No validated native or persistent caller binding |
| `caller_conflict` | `{}` | Conflicting signals or identity collision |
| `session_not_found` | `{}` | No Session; read-only call does not create it |
| `session_closed` | `{session_id:ID}` | New role/tool admission forbidden |
| `authorization_denied` | `{operation:string}` | Role direction or operation permission not valid |
| `authorization_unavailable` | `{operation:string}` | Required proof could not be established |
| `role_unknown` | `{role:ID}` | Requested role is not configured |
| `role_locked` | `{current_role:ID,requested_role:ID}` | Attempt to replace first role |
| `role_required` | `{tool_id:ID}` | Null role for a role-restricted tool |
| `tool_denied` | `{tool_id:ID}` | Configured permission denies the call |
| `tool_not_found` | `{tool_id:ID}` | Unknown configured tool |
| `tool_schema_mismatch` | `{tool_id:ID,expected:string,actual:string}` | Wrong provider version |
| `tool_unavailable` | `{tool_id:ID,reason:string}` | Capability unavailable before invocation |
| `request_not_found` | `{request_id:ID}` | Unknown own request |
| `request_reuse_conflict` | `{request_id:ID}` | Same own request ID, different payload |
| `revision_conflict` | `{expected:integer,actual:integer}` | Stale Session revision |
| `limit_exceeded` | `{limit:string}` | Configured bound exceeded before dispatch |
| `store_busy` | `{}` | Bounded own-store lock timeout |
| `store_incompatible` | `{schema:string|null}` | Unknown own-store format, no silent migration |
| `evidence_mismatch` | `{request_id:ID}` | Retained provider result differs from its digest |

Only store_busy and authorization_unavailable are retryable pre-dispatch errors.
Same role/replay is not an exception to authenticating the current caller or
operator direction. Request parsing/caller authorization precedes own replay;
exact successful Session replay precedes revision check; current revision precedes
role-lock comparison. For tool calls, existing exact request readback must remain
possible after close and never require dispatch. New admission checks closed status
and permissions before recording a request. Serialize own mutations under own locks;
never hold a distributed transaction with Sequencing/Execution or a tool provider.

Действующие файловые операции используют [общую политику](poi-fs-contract.md),
а reader — обычные метаданные версии из [своего договора](poi-file-contract.md#версия-ack-и-эффекты).
Проверка хэшей или подмены не является условием чтения этих операций.
Секретные значения в диагностике принадлежат соответствующему владельцу вывода.
Optional telemetry/duration/formatting cannot grant a role, change a request outcome
or decide Task success. Preserve incomplete observation coverage and unobserved
exit/duration as unknown; do not synthesize full logs after a crash.

## 8. Independent literal fixtures

Fixtures are specification vectors, not generated production expectations.

### F1 — first assignment and lock

All rows use valid operator authorization and a known open Session at revision1
except where explicitly marked otherwise. `current` is the saved role before the
request; `stale` means the expected revision differs from the current revision.

```json
[
  {"current": null, "requested": "author", "stale": false, "expected": "ASSIGNED"},
  {"current": "author", "requested": "author", "stale": false, "expected": "UNCHANGED"},
  {"current": "author", "requested": "reviewer", "stale": false, "expected": "role_locked"},
  {"current": "author", "requested": "reviewer", "stale": true, "expected": "revision_conflict"},
  {"current": null, "requested": "author", "stale": true, "expected": "revision_conflict"}
]
```

### F2 — identity replay does not manufacture a reviewer

A generated binding already maps to Session A with role author. Native information
later appears for the same binding: `session.ensure` still returns A/author. Merely
setting agent_id=reviewer or copying another launcher does not produce Session B.
A genuinely separate validated native caller receives its own role-null Session;
its configured role still does not prove review quality or prior host-event delivery.

### F3 — concurrent initial assignment is deterministic

Two authorized requests request author and reviewer at revision1. One atomic
assignment commits revision2; the serialized loser gets revision_conflict. A new
request at revision2 for another role gets role_locked. Original proof is retained.
Exact replay of the winning request returns the same revision2 assignment without
another write; changed payload under its request ID gets request_reuse_conflict.

### F4 — operator text is not authorization

```json
[
  {"proof": "verified_for_exact_session_and_role", "role_write_allowed": true},
  {"proof": "caller_text_operator_approved", "role_write_allowed": false},
  {"proof": "verified_for_other_session", "role_write_allowed": false},
  {"proof": "verified_for_other_role", "role_write_allowed": false},
  {"proof": "unavailable", "role_write_allowed": false}
]
```

A denied/unavailable proof leaves role null and revision1 unchanged. No Task stage
or successful previous tool call fills the missing authority.

### F5 — file reading with no Stride

Start an installation containing only Poise, its own config/store and a configured
reader. `session.ensure` creates a null-role Session. `tool.invoke` with an `any`
reader binding first obtains its context, then reads an explicit file range through
its exact provider schema. The result retains the real range/text digest/receipt.
No Task/Stage/Workspace input or Stride DB lookup occurs. A missing Session on
`tools.list` or `session.status` gives session_not_found with zero Session creation.

### F6 — direct generic command does not need an orchestrator

Same isolated installation, configured command provider and allowed cwd/argv/env.
Invoke a command through Poise directly. Its real exit0/stdout is retained and can
be read with request.status; a deliberate exit7 yields FAILED with
provider_exit_code7, not Task success. A correct provider response that itself says
business FAIL remains visible and is not rewritten to PASS. No role is assigned by
this call. Unknown or forbidden provider selection produces the exact pre-dispatch
rejection, no hidden fallback to Stride work.

### F7 — role-independent versus role-restricted tool

```json
[
  {"role": null, "policy": "any", "allowed": [], "expected": "ADMIT"},
  {"role": null, "policy": "one_of", "allowed": ["author"], "expected": "role_required"},
  {"role": "author", "policy": "one_of", "allowed": ["author"], "expected": "ADMIT"},
  {"role": "reviewer", "policy": "one_of", "allowed": ["author"], "expected": "tool_denied"}
]
```

All other configured permissions must also pass; these rows test only the role
predicate and do not imply arbitrary path/command permission.

### F8 — close is not foreign completion

Close revision2/author → revision3/closed/author. New tool admission is rejected
session_closed. An already admitted request may finish; its retained result remains
readable. Exact old invocation replay reads its stored state and does not dispatch.
Close does not release a Task claim, delete a worktree, cancel a provider, publish a
result or reopen under another role. No private foreign DB is consulted.

### F9 — unknown external effects are not repeated

Crash after recording tool admission, before a terminal result can be verified.
The own request is UNKNOWN, provider_exit_code null and no fabricated complete
result. Exact invocation retry and request.status return that uncertainty, not a
second process launch. The provider's explicit recovery contract decides how to
reconcile it. Missing/corrupt retained output yields evidence_mismatch, not a repaired
success or silently rerun command.

### F10 — installation portability and missing state

Two installations use different explicit roots/executable argv but the same logical
Session/tool schemas. Neither requires Stride configuration. Unknown own-store
schema gives store_incompatible; missing caller gives caller_unavailable. The tool
surface does not change merely because identity origin is native versus generated.
A path returned by one installation is a resolved location, not global authority.

## 9. Impact, limitations and mapping

Future implementation affects `apps/poise/**` independent config/store, common
identity composition, role-lock owner, direct tool transport and own request result
projection. Preserve current SessionEstablisher collision/reuse invariants and the
bounded reader/output owners. Do not transplant the shared Task DB or interpret a
stage's current role as Poise's immutable Session role.

Действующие байтовые порции и продолжение описаны в
[руководстве reader/2](poi-file-contract.md); реализованная файловая политика
и запись — в [своём договоре](poi-fs-contract.md). Остальные проектные области:
асинхронные запросы/отмена/результаты провайдера
T068–T070, общий учёт T071. The T009 accounting SQLite-lock finding remains a known downstream issue,
not fixed by this design. These owners must preserve the minimum direct-use and
immutable-role boundary specified here, and version any richer public contract.

| Requirement | Contract evidence |
| --- | --- |
| R01 / POI-01 | §2, §4.1–4.3, F1–F4: null initial role, operator-directed once-only assignment, atomic lock |
| R02 / POI-02 | §1–3, §5–6, F5/F6/F10: Session/file/command use without Task, Stage, Workspace or Stride DB |
| R03 / POI-03 | §1, §5.2, F5/F6: direct configured Poise tools, no mandatory task orchestrator |
| R04 / GEN-03 | §1, §4.4–4.5, §7, F8/F9: own Session/request authority, no private peer store or hidden lifecycle |
| R05 / GEN-04 | §2–3, §6, F7/F10: explicit configured roles/argv/env/paths/bounds, no fixed application roster |

Both DoD clauses are co-delivered: exact input/output/version/error semantics and
independent success/rejection fixtures appear in this document; no second authority,
broker or hidden fan-out is introduced.

Этот исходный проект был документацией. Поставленные операции описаны текущими
операторскими договорами в начале страницы; оставшиеся проектные области
сохраняют собственные условия разработки.

Related accepted public owners: [Sequencing](../../sequencing/docs/contract-seq.md),
[Execution](../../execution/docs/contract-exe.md),
[Orchestrator](../../orchestrator/docs/contract-orc.md).
