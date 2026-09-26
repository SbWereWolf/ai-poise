# Sequencing public contract

Статус: target design for `PS-DEV-002-T011` (`M01.T011`).

Этот документ определяет публичную границу будущего самостоятельного приложения
Sequencing после разделения монолита. Контракт фиксирует protocol, ownership и смысл
данных, но не утверждает, что описанный standalone CLI уже реализован текущим `poise`.
Реализация executable/config/store принадлежит последующим Tasks.

## 1. Граница владельца

Sequencing является единственным владельцем:

- регистрации внешней канонической Task identity в sequencing state;
- Sprint membership и dependency graph;
- вычисляемой eligibility и blocker projection;
- claim/release/resume ownership для Sequencing scope;
- Workspace/worktree reservation, allocation binding и release state;
- result-provenance references, нужных dependency graph;
- собственного request replay и собственной store revision;
- explicit export/import transfer slice перечисленных данных.

Sequencing **не** владеет Requirements statement/snapshot contents, Execution process/stage
state, checks/evidence, execution attempts, outcome calculation, Poise session/role protocol
или Bootstrap diagnostics. Канонический `task_id` приходит из Requirements и не
перевыделяется Sequencing. Execution outcome/acceptance не читается из private DB: нужный
terminal/result fact передаётся явно через публичный контракт уполномоченным caller.

Регистрация Task, claim или Workspace не создают скрытых записей в Requirements/Execution.
Нет обязательного broker, distributed transaction или private foreign DB read. Caller
последовательно использует публичные owner contracts и передаёт stable identities/revisions.

## 2. Транспорт и версия

Публичный wire protocol — newline-terminated UTF-8 JSON с точной версией:

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "query",
  "input": {}
}
```

Обязательные поля верхнего уровня: только `schema`, `operation`, `input`.
`input` всегда JSON object. Неизвестные поля отклоняются.

Команда запуска не является частью wire protocol. Вызывающая сторона получает `argv`
Sequencing из resolved configuration. Контракт не содержит абсолютного installation path,
фиксированного имени executable или обязательной environment variable.

stdout содержит ровно один JSON object и завершающий `\n`. Успех — exit `0`; contract,
domain или owner-local storage rejection — exit `2`. Диагностика не заменяет JSON error.

Каждый ответ содержит ту же schema. Пример read response:

```json
{
  "schema": "stride-sequencing/v1",
  "status": "read_only",
  "revision": 31,
  "results": []
}
```

## 3. Конфигурационные зависимости

Физический формат config определяется implementation Task, но resolved configuration MUST
явно предоставлять Sequencing как минимум:

- путь собственной sequencing DB и собственного lock/state;
- `batch.max_input_bytes`, `batch.max_items` и read/search limits;
- owner-local lock timeout/poll limits;
- repository identity и configured `base_ref` для новых Workspace;
- configured worktree root и branch naming policy;
- Workspace filesystem/Git timeouts;
- transfer operation root, package size/file/count limits и file mode;
- deterministic policy разрешённых Task/Sprint identifier formats.

Пути разрешаются из текущей installation configuration. Wire packet не содержит пути к
Requirements/Execution DB и не знает их config. Repository/worktree paths могут
возвращаться как resolved operational output; они никогда не встроены в protocol schema.

## 4. Модель данных

### 4.1 Registered Task

Минимальная owner record после регистрации:

```json
{
  "task_id": "0042",
  "requirements_snapshot_revision": 2,
  "state": "available",
  "sprint_id": null,
  "claim": null,
  "workspace": null,
  "terminal": null
}
```

`task_id` — foreign canonical identity, уже выданная Requirements. Sequencing проверяет
синтаксическую допустимость/уникальность, но не меняет namespace/width и не выделяет новый ID.
`requirements_snapshot_revision` — explicit provenance reference; Sequencing не копирует
Requirements statement как второй canonical store.

Sequencing task state v1:

- `available` — зарегистрирована и не terminal;
- `claimed` — есть активный Sequencing claim;
- `completed` — explicit accepted terminal fact зарегистрирован;
- `cancelled` — explicit terminal cancellation зарегистрирована.

`blocked` не является сохранённым task state: это вычисляемая eligibility projection по
dependency graph/terminal facts. Workspace state хранится отдельно от logical Task state.

### 4.2 Sprint membership и dependency

Sprint owner record:

```json
{
  "sprint_id": "PS-DEV-002",
  "members": ["0042", "0043"],
  "dependencies": [
    {
      "predecessor": "0042",
      "successor": "0043",
      "kind": "result"
    }
  ]
}
```

В v1 `kind` равен `completion` или `result`.

- `completion`: successor eligible только после `completed` predecessor;
- `result`: требует того же terminal completion и additionally exposes predecessor
  `result_commit` в result provenance.

Endpoints одного edge принадлежат одному Sprint; cycles запрещены. Standalone Task не
участвует в межзадачном dependency edge. Изолированная Task не обязана искусственно входить
в Sprint.

### 4.3 Claim

Claim — owner-local exclusive lease identity без time-based silent expiry:

```json
{
  "claim_id": "claim-0042-agent-a-1",
  "task_id": "0042",
  "claimant": "agent-a",
  "status": "active"
}
```

Claim создаётся только для currently eligible non-terminal Task. Eligibility re-check и
claim creation происходят в одной owner-local transaction/revision boundary. Два concurrent
claim на одну Task не могут оба succeed.

Release удаляет active exclusivity, но не уничтожает saved handoff/workspace evidence.
Resume создаёт новый active claim только через explicit request и exact saved state.

### 4.4 Workspace

Workspace — Sequencing-owned operational binding Task к configured repository/worktree:

```json
{
  "workspace_id": "ws-0042-1",
  "task_id": "0042",
  "repository_id": "ai-poise",
  "base_commit": "0123456789abcdef0123456789abcdef01234567",
  "branch": "tasks/0042",
  "path": "/resolved/configured/worktrees/0042",
  "status": "ready"
}
```

`path` — resolved output текущей среды, не schema constant. Для нового Workspace
`base_commit` наблюдается на configured `base_ref` после eligibility check и сохраняется до
filesystem mutation. Replay/recovery использует этот exact commit, даже если `base_ref`
сдвинулся позже.

Workspace status v1: `reserved`, `ready`, `release_pending`, `released`, `failed`.
Filesystem/Git side effect не объявляется SQLite-atomic. Sequencing использует durable
owner intent: reservation фиксируется до side effect; exact replay проверяет уже созданный
workspace и завершает/возвращает тот же result. Чужой path/branch не reset/delete автоматически.

### 4.5 Terminal/result provenance fact

Sequencing принимает только явный fact, уже решённый соответствующим owner/caller:

```json
{
  "terminal_id": "accept-0042-1",
  "task_id": "0042",
  "kind": "completed",
  "result_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "source_ref": {
    "owner": "orchestrator",
    "id": "acceptance-0042"
  }
}
```

`kind` — `completed` или `cancelled`. Для `completed` `result_commit` MAY быть null, если ни
один dependency kind `result` не требует Git provenance; если result-edge должен быть
удовлетворён, commit обязателен. Для `cancelled` `result_commit` обязан быть null.

Sequencing не вычисляет Execution success и не принимает log/evidence как completion.
`terminal_id` immutable по canonical payload; conflicting reuse отклоняется.

## 5. Mutation batch: `apply`

Request:

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "apply",
  "input": {
    "request_id": "seq-2026-09-27-001",
    "expected_revision": 31,
    "operations": []
  }
}
```

`request_id` непуст и immutable по canonical payload. `expected_revision` —
неотрицательное integer. `operations` — nonempty bounded list. Owner-state operations одного
batch выполняются all-or-nothing в одной sequencing transaction. External Workspace/transfer
filesystem effects используют durable intent и не маскируются как DB transaction.

Успех owner-only batch:

```json
{
  "schema": "stride-sequencing/v1",
  "status": "applied",
  "revision": 32,
  "replayed": false,
  "results": [
    {"id": "op-1", "kind": "register_task", "changed": true}
  ]
}
```

Exact replay того же outer `request_id` возвращает saved success payload с
`replayed: true`, не повторяет allocation/claim/mutation и не увеличивает revision. Тот же
ID с другим payload получает `request_reuse_conflict`.

### 5.1 `register_task`

```json
{
  "id": "reg-1",
  "kind": "register_task",
  "task_id": "0042",
  "requirements_snapshot_revision": 2
}
```

Создаёт только Sequencing record. Не пишет Requirements/Execution, не создаёт claim,
Workspace или Sprint membership. Exact already-present identity с теми же immutable refs
возвращает `changed: false`; conflicting canonical provenance — `identity_conflict`.

### 5.2 `register_sprint`

```json
{
  "id": "sprint-1",
  "kind": "register_sprint",
  "sprint_id": "PS-DEV-002",
  "members": ["0042", "0043"],
  "dependencies": [
    {"predecessor": "0042", "successor": "0043", "kind": "result"}
  ]
}
```

Все members уже зарегистрированы Sequencing. Operation задаёт полный desired membership и
dependency graph новой Sprint identity. Existing Sprint изменяется только через
`revise_sprint`; implicit replace запрещён.

### 5.3 `revise_sprint`

```json
{
  "id": "sprint-revise",
  "kind": "revise_sprint",
  "sprint_id": "PS-DEV-002",
  "expected_sprint_revision": 4,
  "members": ["0042", "0043", "0044"],
  "dependencies": []
}
```

Изменение graph запрещено, если оно меняет prerequisites уже claimed/terminal successor или
пытается вывести Task с active claim/workspace. Graph проходит endpoint/connectedness/cycle
validation до записи; rejection не оставляет partial membership.

### 5.4 `claim_task`

```json
{
  "id": "claim-1",
  "kind": "claim_task",
  "task_id": "0042",
  "claimant": "agent-a",
  "claim_request_id": "work-0042-agent-a"
}
```

Sequencing внутри той же transaction:

1. читает current dependency/terminal state;
2. подтверждает Task eligibility;
3. убеждается в отсутствии active claim;
4. создаёт claim identity и меняет owner state на `claimed`.

Result:

```json
{
  "id": "claim-1",
  "kind": "claim_task",
  "task_id": "0042",
  "claim_id": "claim-0042-agent-a-1",
  "claimant": "agent-a"
}
```

`claim_request_id` immutable. Exact replay возвращает тот же claim. Второй claimant получает
`claim_conflict`; blocked Task — `not_eligible` с explicit blockers.

### 5.5 `release_claim`

```json
{
  "id": "release-1",
  "kind": "release_claim",
  "task_id": "0042",
  "claim_id": "claim-0042-agent-a-1",
  "claimant": "agent-a",
  "reason": "handoff"
}
```

Claimant/claim ID должны совпасть. Release не удаляет Task/Workspace history и не объявляет
completion. Если Workspace ready и WIP должен сохраниться, release оставляет resumable owner
record; destructive cleanup является отдельной явной Workspace operation.

### 5.6 `resume_claim`

```json
{
  "id": "resume-1",
  "kind": "resume_claim",
  "task_id": "0042",
  "claimant": "agent-b",
  "handoff_id": "handoff-0042-1"
}
```

Resume допустим только для unclaimed non-terminal Task с валидным saved handoff/Workspace.
Sequencing проверяет сохранённые branch/head/workspace identities и создаёт новый claim без
перевычисления Workspace baseline.

### 5.7 `record_terminal`

```json
{
  "id": "terminal-1",
  "kind": "record_terminal",
  "terminal": {
    "terminal_id": "accept-0042-1",
    "task_id": "0042",
    "kind": "completed",
    "result_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "source_ref": {"owner": "orchestrator", "id": "acceptance-0042"}
  }
}
```

Active claim должен быть released/reconciled согласно explicit policy до terminal mutation.
Exact terminal replay idempotent; changed payload with same `terminal_id` получает
`terminal_conflict`. Completion автоматически влияет на future eligibility projection, но не
создаёт claim successor и не запускает Execution.

### 5.8 `cancel_task`

Explicit owner terminal cancellation для Sequencing record. Active external/pending Workspace
operation блокирует cancellation до reconciliation. Cancelled predecessor не удовлетворяет
completion/result edge; successor остаётся blocked, пока graph policy явно не изменена.

## 6. Workspace operation: `workspace`

Workspace имеет отдельную operation, потому что Git/filesystem side effects не должны
маскироваться как owner-only SQLite batch.

### 6.1 Acquire

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "workspace",
  "input": {
    "action": "acquire",
    "request_id": "workspace-0042-1",
    "task_id": "0042",
    "claim_id": "claim-0042-agent-a-1",
    "claimant": "agent-a"
  }
}
```

Sequencing resolves repository/base_ref/worktree policy from config, stores reservation with
observed exact base commit, performs bounded external Git/filesystem step, then publishes
`ready` owner state. Response:

```json
{
  "schema": "stride-sequencing/v1",
  "status": "workspace_ready",
  "revision": 33,
  "workspace": {
    "workspace_id": "ws-0042-1",
    "task_id": "0042",
    "base_commit": "0123456789abcdef0123456789abcdef01234567",
    "branch": "tasks/0042",
    "path": "/resolved/configured/worktrees/0042"
  },
  "replayed": false
}
```

Crash after reservation is recoverable by exact replay: Sequencing verifies expected
branch/path/head before completing. It never resets a dirty/foreign workspace to force success.

### 6.2 Release

`action=release` requires exact workspace/claim or terminal-cleanup authority. It marks
`release_pending`, performs configured bounded removal only after safety checks, then marks
`released`. Dirty WIP without an accepted preservation/disposition contract rejects
`workspace_dirty`; foreign branch/path rejects `workspace_conflict`.

### 6.3 Inspect/reconcile

`action=inspect` is read-only external verification of one known reservation. `action=reconcile`
is explicitly authorized recovery for a known `reserved/release_pending/failed` intent;
it cannot claim another Task or import an arbitrary filesystem path.

## 7. Consistent read batch: `query`

Request:

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "query",
  "input": {
    "queries": [
      {"id": "q1", "kind": "eligibility", "task_id": "0043"},
      {"id": "q2", "kind": "sprint", "sprint_id": "PS-DEV-002"}
    ]
  }
}
```

Все queries одного packet наблюдают одну Sequencing `revision`. Query не repair/migrate owner
state и не читает foreign stores.

Успех:

```json
{
  "schema": "stride-sequencing/v1",
  "status": "read_only",
  "revision": 33,
  "results": [
    {"id": "q1", "value": {}},
    {"id": "q2", "value": {}}
  ]
}
```

### 7.1 Task/Sprint reads

- `task`: `{id, kind, task_id}` → current owner record, claim/workspace/terminal refs.
- `sprint`: `{id, kind, sprint_id}` → members, dependencies, sprint revision.
- `work_overview`: `{id, kind, sprint_id}` → eligible, blocked, active/resumable,
  result provenance and current counts derived from authoritative owner records.
- `dependencies`: `{id, kind, task_id}` → exact predecessors/successors and kinds.

### 7.2 Eligibility

`eligibility` returns a deterministic projection:

```json
{
  "task_id": "0043",
  "eligible": false,
  "blockers": [
    {
      "predecessor": "0042",
      "kind": "result",
      "reason": "predecessor_not_completed"
    }
  ],
  "result_provenance": []
}
```

После completion predecessor result-edge добавляет:

```json
{
  "predecessor": "0042",
  "result_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

Eligibility query не создаёт claim и не резервирует Workspace.

### 7.3 Claim/Workspace reads

- `claim`: exact current/saved claim/handoff projection;
- `workspace`: exact reservation/status/path/branch/base commit;
- `resumable`: tasks с released claim + valid preserved workspace/handoff, без нового claim.

Неизвестная identity получает packet-level `not_found`; v1 не возвращает mixed partial
success, чтобы caller не объединял observations разных revisions/errors.

## 8. Transfer operation

Transfer сохраняет Sequencing-owned state; это portability, не synchronization.

### 8.1 Export

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "transfer",
  "input": {
    "action": "export",
    "request_id": "export-seq-1",
    "task_ids": ["0042"],
    "sprint_id": null,
    "handoff": null
  }
}
```

Для whole Sprint `sprint_id` задан, `task_ids` explicit null. Незавершённый active claim
экспортируется только если explicit handoff в том же request успешно release-ит ownership.
Пакет содержит Sequencing owner slice, preserved Workspace Git bundle/metadata при наличии,
manifest и checksums. Requirements/Execution stores/files в пакет не включаются.

Response:

```json
{
  "schema": "stride-sequencing/v1",
  "status": "exported",
  "package_path": "/configured/transfer/out/seq-export.zip",
  "package_digest": "sha256:...",
  "task_ids": ["0042"],
  "sprint_ids": [],
  "replayed": false
}
```

`package_path` — resolved environment output; destination delivery не выполняется скрыто.

### 8.2 Import

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "transfer",
  "input": {
    "action": "import",
    "request_id": "import-seq-1",
    "package_path": "/explicit/input/seq-export.zip",
    "package_digest": "sha256:..."
  }
}
```

Import проверяет digest, manifest schema, sizes, paths, SQLite slice consistency, Git bundle
metadata and absence of conflicting Task/Sprint/branch identities before publication.
Existing owner identity конфликтует; merge/replace не выполняется автоматически. Import
preserves saved release/handoff, но не создаёт active claim и не объявляет foreign acceptance.

## 9. Stable rejection envelope

Любой rejection имеет точную форму:

```json
{
  "schema": "stride-sequencing/v1",
  "status": "rejected",
  "error": {
    "code": "not_eligible",
    "message": "Task is blocked by incomplete predecessors",
    "retryable": false,
    "details": {
      "task_id": "0043",
      "blockers": ["0042"]
    }
  }
}
```

`message` human-oriented; machine branch uses `error.code`.

Коды v1:

| code | Смысл | Partial owner write |
| --- | --- | --- |
| `invalid_json` | stdin не valid UTF-8 JSON | нет |
| `unsupported_schema` | schema не `stride-sequencing/v1` | нет |
| `invalid_packet` | неверные/лишние поля | нет |
| `unknown_operation` | unknown operation/action/kind | нет |
| `limit_exceeded` | превышен configured bound | нет |
| `revision_conflict` | expected Sequencing/Sprint revision stale | нет |
| `request_reuse_conflict` | request ID связан с другим payload | нет |
| `not_found` | canonical sequencing identity отсутствует | нет |
| `already_exists` | explicit create конфликтует | нет |
| `identity_conflict` | foreign canonical Task provenance конфликтует | нет |
| `dependency_conflict` | endpoint/sprint/revision нарушает graph contract | нет |
| `dependency_cycle` | graph cyclic | нет |
| `not_eligible` | Task blocked/terminal/not startable | нет |
| `claim_conflict` | Task уже claimed другим claimant | нет |
| `claim_owner_mismatch` | release/resume authority не совпадает | нет |
| `workspace_conflict` | branch/path/repository identity конфликтует | owner intent может остаться recoverable |
| `workspace_dirty` | release уничтожил бы unpreserved WIP | нет destructive effect |
| `workspace_external_failure` | bounded Git/filesystem step failed | durable intent остаётся recoverable |
| `terminal_conflict` | terminal ID/payload/state conflict | нет |
| `result_provenance_missing` | result dependency требует commit | нет |
| `package_invalid` | transfer digest/schema/path invalid | нет publication |
| `transfer_conflict` | destination identity already exists | нет publication |
| `transfer_incomplete` | selected active/pending state нельзя snapshot | нет publication |
| `store_busy` | owner-local lock timeout | нет |
| `store_incompatible` | owner schema unknown/incompatible | нет |

`store_busy` и retryable external Workspace failures MAY иметь `retryable: true`; stable
contract/domain conflicts — `false`. Failure после Workspace intent никогда не выдаёт
filesystem mutation за полностью отсутствующую: response/details/status указывают recoverable
intent, а exact replay/reconcile проверяет реальность.

## 10. Literal fixtures

Fixtures — contract examples, независимые от production serializer.

### F1 — регистрация принимает canonical Task identity и не fan-out

Revision `10`, Requirements ранее выдал `{task_id:"0042", snapshot_revision:2}`.

Request:

```json
{"schema":"stride-sequencing/v1","operation":"apply","input":{"request_id":"f1","expected_revision":10,"operations":[{"id":"r","kind":"register_task","task_id":"0042","requirements_snapshot_revision":2}]}}
```

Expected: `status=applied`, revision `11`, Task `0042` state `available`. Observable writes —
только Sequencing store. Requirements/Execution не вызываются. Exact replay возвращает
revision `11`, same Task identity, `replayed=true`.

### F2 — result dependency сохраняет explicit provenance

Sprint `S` содержит `0042 -> 0043` kind `result`. Пока `0042` не completed,
`eligibility(0043)` возвращает `eligible=false` и blocker `0042`.

После explicit `record_terminal` для `0042` с result commit `aaa...`, тот же query на новой
revision возвращает `eligible=true`, no blockers и result provenance
`{predecessor:"0042", result_commit:"aaa..."}`. Sequencing не merge-ит этот commit в
Workspace successor автоматически.

### F3 — concurrent claim имеет ровно одного победителя

Task `0043` eligible, no active claim. Два request с разными `claim_request_id`/claimant
конкурируют на одной исходной revision. Ровно один commit owner transaction succeeds и
возвращает claim. Второй после serialization получает `revision_conflict` либо
`claim_conflict`; никогда не два active claims.

### F4 — Workspace baseline наблюдается один раз и replay stable

Configured `base_ref=poise-stride-v2` указывает на commit `bbb...`. Successful claim
`0043` выполняет `workspace acquire`. Sequencing сохраняет reservation с `base_commit=bbb...`
до Git side effect. Если process падает после `git worktree add`, replay того же request
проверяет expected path/branch/head `bbb...` и возвращает тот же `workspace_id`. Сдвиг
`base_ref` на `ccc...` не меняет сохранённый Workspace.

### F5 — claim release сохраняет resumable state, не completion

Ready Workspace Task `0043`, claim agent-a. `release_claim(reason=handoff)` removes active
claim and сохраняет validated handoff/workspace refs. `resumable` показывает Task, но
`terminal` остаётся null и successors не разблокируются. `resume_claim` agent-b получает new
claim после exact handoff validation и не пересоздаёт Workspace baseline.

### F6 — cancelled predecessor не удовлетворяет dependency

`0042 -> 0043` kind completion. Explicit cancellation `0042` переводит terminal state в
cancelled. `eligibility(0043)` остаётся false с reason `predecessor_cancelled`; caller должен
explicitly revise/waive graph policy в поддержанном owner flow. Cancellation не имитирует
completion.

### F7 — transfer не производит ownership/foreign state

Export unclaimed released Task `0043` returns package/digest. Import в пустой destination
создаёт Sequencing owner record/workspace metadata как released/resumable, `claim=null`.
В package нет Requirements statement DB или Execution process/evidence. Import into store,
где `0043` already exists, целиком rejects `transfer_conflict`.

### F8 — stale revision all-or-nothing

Store revision `20`; apply says `expected_revision=19` и содержит valid register + claim.
Expected `revision_conflict`. Task registration не создаётся, claim не появляется, request
allocator/replay state не расходует candidate side effects.

### F9 — protocol portable относительно installation path

Две установки имеют разные Sequencing DB/worktree roots и разные executable argv, но
принимают одинаковый F1 packet. Wire packet не содержит ни одного installation path.
Workspace output возвращает path текущей installation; equality physical path между
установками не является contract predicate.

## 11. Concurrency, security и observability

Concurrency:

- owner-only apply batches сериализуются одним Sequencing store transaction/lock;
- optimistic expected revision предотвращает lost update;
- eligibility + claim validation/creation атомарны owner-local;
- one read batch observes one revision;
- outer request replay проверяется до нового claim/allocation;
- Workspace/transfer external filesystem phases используют durable intent/reconcile, не
  distributed lock.

Security boundary:

- wire protocol не принимает foreign DB/config path;
- claim/release authority сверяется с exact claimant/claim identity;
- Workspace path строится из config + validated logical identity, а не произвольного caller
  destination;
- transfer extraction rejects links/path traversal/unexpected entries and enforces limits;
- diagnostics не выводят config secrets;
- unknown owner schema не auto-migrate.

Observability:

- machine evidence — schema/status/revision/replayed/error.code + claim/workspace/transfer IDs;
- owner history/replay receipts immutable для уже принятых request IDs;
- logs не являются authority;
- caller использует explicit Task/Sprint/revision IDs, не hidden “latest foreign state”.

## 12. Impact analysis и downstream contracts

Affected future implementation:

- standalone `apps/sequencing/**` adapter/domain/store;
- independent Sequencing config/database/lock;
- migration of existing Sprint membership/dependency/claim/worktree owner state;
- explicit caller integrations from Orchestrator/Poise;
- transfer packaging ограниченное Sequencing owner slice.

Semantically unchanged owners:

- Requirements owns canonical Task identity, statement/snapshot revisions and requirement
  satisfaction data;
- Execution owns process graph, stage/check/evidence/restart/outcome generation;
- Poise owns session/role/status contract;
- Bootstrap owns environment diagnostics/repair policy.

Downstream contracts MUST pass exact Task/snapshot identity to Sequencing registration and
consume explicit eligibility/claim/workspace responses. Orchestrator later defines ordering
между public owner calls; T011 does not introduce hidden fan-out.

Implementation prerequisites/risks:

1. Extract current Sprint/Task sequencing state without leaving shared DB as second authority.
2. Preserve claim race semantics and resume/handoff identity during migration.
3. Preserve configured Git `base_ref` observation-before-worktree semantics.
4. Separate DB atomicity from Git/filesystem recovery with durable intents.
5. Transfer owner slice must not absorb Requirements/Execution state.
6. Migration must prove old/new projections equivalent before retiring legacy authority.

This design Task changes only the public contract document. It does not modify executable
config or command. Therefore co-delivery DoD means this new contract documents the intended
future config/invocation in the same Task; no undocumented executable/config change exists.

## 13. Requirement mapping

| Task requirement | Contract evidence |
| --- | --- |
| R01 / SEQ-01 | §1, §4.1–4.2, §5.1–5.3, F1/F2: explicit registration, Sprint membership/dependencies and canonical foreign identity |
| R02 / SEQ-03 | §4.2–4.3, §5.4–5.6, §7.2–7.3, F2/F3/F5/F6: eligibility, claim/release/resume and exact blockers |
| R03 / SEQ-05 | §4.4, §6, F4/F5: configured Workspace reservation/allocation/release/recovery with stable baseline |
| R04 / SEQ-07 | §8, F7: explicit transfer export/import, package digest/conflicts, no manufactured claim/foreign state |
| R05 / GEN-03 | §1, §5, §8, §11–12: independent owner/store; no private Requirements/Execution reads or hidden writes |
| R06 / GEN-04 | §2–3, §6, §11, F9: configured invocation/store/repository/worktree policy; no installation-path assumption |

DoD literal fixtures: F1–F9 independently specify success/error/replay/side-effect predicates.
No second authority, broker or hidden fan-out is introduced. Config/command target behavior is
documented here; executable delivery remains downstream.

## 14. Design verification oracle

Design is coherent only if an independent reviewer can verify all of the following from this
document without importing production serializers:

1. registration consumes canonical foreign Task identity and performs no hidden fan-out;
2. dependency eligibility preserves completion/result semantics and exact result provenance;
3. concurrent claim cannot produce two owners and release/resume cannot imply completion;
4. Workspace baseline/path/branch lifecycle is configured, replayable and crash-recoverable;
5. terminal/cancel facts are explicit and do not read Execution private state;
6. transfer preserves Sequencing-owned work without synchronization or foreign state capture;
7. stale revisions/request reuse/conflicts have stable codes and no partial owner writes;
8. identical logical packets work across different installation paths/configured roots;
9. each R01–R06 and both DoD clauses map to literal contract evidence.

Executable provider/consumer qualification belongs to implementation/UAT Tasks and cannot be
claimed complete by this design-only Task.
