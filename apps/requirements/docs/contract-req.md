# Requirements public contract

Статус: target design for `PS-DEV-002-T010` (`M01.T010`).

Этот документ определяет публичную границу будущего самостоятельного приложения
Requirements после разделения монолита. Контракт фиксирует протокол и смысл данных,
но не утверждает, что описанные target-операции уже реализованы в текущем `poise`.
Реализация CLI/конфига принадлежит последующим Tasks, начиная с T018.

## 1. Граница владельца

Requirements является единственным владельцем:

- System и Application requirements и их M:N связей;
- Requirements-среза Task: канонического `task_id`, `goal`, Task requirements и
  `definition_of_done`;
- ревизий Task statement и неизменяемых опубликованных snapshots;
- фактов outcome, которые влияют на satisfaction requirements;
- истории и transfer slice этих данных;
- собственного request replay и собственной store revision.

Requirements **не** владеет Sprint membership, dependency/eligibility, claims,
Workspace, process/stage graph, checks, execution attempts, evidence admission или
runtime stage outcome. Эти данные остаются за Sequencing/Execution. Создание Task в
Requirements не создаёт никаких соседних записей. После публикации statement
потребитель явно передаёт полученные `task_id` и `snapshot_revision` в публичный
интерфейс соответствующего владельца.

Приложение не читает приватные БД Sequencing/Execution/Poise и не импортирует их
внутренние domain objects. Общей транзакции, обязательного broker и скрытого
fan-out нет.

## 2. Транспорт и версия

Публичный wire protocol — newline-terminated UTF-8 JSON с точной версией:

```json
{
  "schema": "stride-requirements/v1",
  "operation": "query",
  "input": {}
}
```

Обязательные поля верхнего уровня: только `schema`, `operation`, `input`.
`input` всегда JSON object. Неизвестные поля отклоняются.

Команда запуска не является частью wire protocol. Вызывающая сторона получает
`argv` Requirements из своей явной конфигурации и передаёт один packet через
stdin. Контракт не содержит абсолютного installation path и не требует
фиксированного имени executable или переменной окружения.

stdout содержит ровно один JSON object и завершающий `\n`. Для успешного ответа
exit code равен `0`, для синтаксического, contract, domain или owner-local storage
отказа — `2`. Диагностический текст не заменяет JSON error.

Каждый ответ содержит ту же `schema`:

```json
{
  "schema": "stride-requirements/v1",
  "status": "read_only",
  "revision": 17,
  "results": []
}
```

## 3. Конфигурационные зависимости

Формат файла конфигурации определяется T018, но resolved configuration MUST явно
предоставлять Requirements как минимум:

- путь собственной БД и собственного lock/state;
- `batch.max_input_bytes` и `batch.max_items`;
- lock timeout/poll limits;
- Task ID allocation policy: namespace, width, first и step.

Пути разрешаются из конфигурации текущей установки. Протокол не содержит прежних
абсолютных путей и не знает список других приложений. Requirements не использует
чужой config как внутреннее состояние.

## 4. Модель данных

### 4.1 System/Application requirement

```json
{
  "id": "APP-TRACE",
  "level": "application",
  "status": "current",
  "text": "Task statements remain traceable."
}
```

- `id`: непустой стабильный identifier без `/`, `\\`, NUL, `.` и `..`;
- `level`: `system` или `application`; после создания неизменяем;
- `status`: `current`, `future` или `obsolete`;
- `text`: непустой UTF-8 text, кроме явного `future` placeholder с `text: ""`.

System↔Application link — множество пар `{system, application}`. Обе стороны M:N;
повтор того же link идемпотентен внутри целевого состояния.

### 4.2 Task identity и statement

Requirements выдаёт канонический `task_id`. Task ID allocator сохраняет
сконфигурированные namespace/width/progression, collision avoidance и стабильный
replay creation request. Sequencing/Execution принимают этот ID и не заменяют его.

Requirements-owned statement имеет вид:

```json
{
  "task_id": "0042",
  "statement_revision": 3,
  "goal": "Separate Requirements without cross-store writes.",
  "task_requirements": [
    {
      "key": "R01",
      "status": "current",
      "text": "Requirements owns the canonical Task identity.",
      "applications": ["APP-TRACE", "APP-IDENTITY"]
    }
  ],
  "definition_of_done": [
    "The public contract has literal success and rejection fixtures."
  ]
}
```

`key` — стабильный, непустой identifier, уникальный внутри `task_id`.
Каноническая identity Task requirement — пара `{task_id, key}`. Поле
`applications` содержит уникальный непустой список Application requirement IDs.
Тем самым Application↔TaskRequirement также является M:N и виден в обе стороны.

`goal` и каждый DoD item непусты. Task requirement допускает `future` placeholder
с пустым text, но такой placeholder не является evidence satisfaction и делает
publication непригодной для текущей обязательной работы, пока gap не разрешён.

Каждое содержательное изменение statement создаёт новую `statement_revision`.
Прошлые revisions не переписываются и доступны через history query.

### 4.3 Immutable snapshot

Publication создаёт новый immutable snapshot из конкретной statement revision и
наблюдаемой Requirements registry revision:

```json
{
  "task_id": "0042",
  "snapshot_revision": 2,
  "statement_revision": 3,
  "registry_revision": 17,
  "statement": {},
  "requirements": {},
  "links": {
    "system_application": [],
    "application_task": []
  }
}
```

Snapshot содержит полные тексты выбранных System/Application/Task requirements и
обе adjacency M:N. Изменение live registry или последующая новая statement revision
не меняет старый snapshot. Downstream registration/setup использует конкретную пару
`task_id` + `snapshot_revision`.

### 4.4 Outcome и satisfaction

Outcome — внешний факт, переданный владельцем выполнения через публичный контракт;
Requirements не читает Execution DB:

```json
{
  "outcome_id": "exec-0042-17",
  "task_id": "0042",
  "snapshot_revision": 2,
  "kind": "positive",
  "satisfied_task_requirements": ["R02"]
}
```

`kind` равен `positive`, `negative` или `cancelled`.

Правила v1:

1. Только `positive` может добавлять satisfaction.
2. Для `negative` и `cancelled` список `satisfied_task_requirements` обязан быть
   пустым. Terminal сам по себе не означает completion.
3. Каждый указанный key обязан существовать в конкретном immutable snapshot.
4. Task requirement считается satisfied после хотя бы одного принятого positive
   outcome для этого exact `{task_id, snapshot_revision, key}`.
5. Application requirement считается satisfied для данного snapshot только когда
   существует хотя бы один applicable Task requirement и satisfied **все**
   applicable Task requirements, связанные с ним в snapshot.
6. System requirement считается satisfied для данного snapshot только когда
   существует хотя бы один applicable Application requirement и satisfied **все**
   applicable Application requirements, связанные с ним в snapshot.
7. Пустое множество детей не даёт vacuous satisfaction. `future` placeholder и
   `obsolete` не используются как положительное evidence.
8. `applicability` и `satisfaction` — разные поля query result; отсутствие
   applicability не кодируется как `satisfied: true`.

Полная политика повторных attempts/restart/recalculation и physical auxiliary schema
не определяется T010. Запрос, требующий ещё не определённой политики T031/T037,
отклоняется `unsupported_policy`, а не угадывается.

## 5. Mutation batch: `apply`

Request:

```json
{
  "schema": "stride-requirements/v1",
  "operation": "apply",
  "input": {
    "request_id": "req-2026-09-26-001",
    "expected_revision": 17,
    "operations": []
  }
}
```

`request_id` непуст и immutable по payload. `expected_revision` — неотрицательное
целое. `operations` непустой список, ограниченный `batch.max_items`. Batch выполняется
в одной owner-local transaction: либо применены все operations, либо ни одна.

Каждая operation имеет уникальный в batch `id` для корреляции result.

Успех:

```json
{
  "schema": "stride-requirements/v1",
  "status": "applied",
  "revision": 18,
  "replayed": false,
  "results": [
    {"id": "op-1", "kind": "put_requirement", "changed": true}
  ]
}
```

Exact replay того же `request_id` и того же canonical JSON request возвращает
сохранённый success payload с `replayed: true`, не выполняет side effects и не
увеличивает revision. Тот же `request_id` с другим payload отклоняется целиком.

### 5.1 `put_requirement`

```json
{
  "id": "op-1",
  "kind": "put_requirement",
  "requirement": {
    "id": "SYS-1",
    "level": "system",
    "status": "current",
    "text": "Durable state remains explicit."
  }
}
```

Create или replace mutable fields существующего System/Application requirement.
`level` существующего ID неизменяем. История сохраняется owner-side и доступна query.

### 5.2 `delete_requirement`

```json
{"id": "op-2", "kind": "delete_requirement", "requirement_id": "APP-OLD"}
```

Удаление разрешено только если ID не участвует в live links и не нужен для
сохранения immutable history/snapshots. Иначе весь batch получает
`referential_conflict`. Исторические snapshots никогда не переписываются.

### 5.3 `link` / `unlink`

```json
{
  "id": "op-3",
  "kind": "link",
  "relation": "system_application",
  "parent": "SYS-1",
  "child": "APP-1"
}
```

```json
{
  "id": "op-4",
  "kind": "unlink",
  "relation": "system_application",
  "parent": "SYS-1",
  "child": "APP-1"
}
```

В v1 прямые registry link/unlink применяются к `system_application`.
Application↔TaskRequirement links принадлежат statement revision и изменяются через
`revise_task`; это не создаёт второй источник истины.

### 5.4 `create_task`

```json
{
  "id": "op-create",
  "kind": "create_task",
  "creation_request_id": "plan-item-alpha",
  "statement": {
    "goal": "Make Requirements independently callable.",
    "task_requirements": [
      {
        "key": "R01",
        "status": "current",
        "text": "Requirements uses its own store.",
        "applications": ["APP-REQ"]
      }
    ],
    "definition_of_done": ["No foreign DB read is required."]
  }
}
```

`creation_request_id` имеет собственную immutable digest/replay semantics внутри
Requirements. При первом применении allocator выдаёт `task_id`; при exact replay
возвращает тот же ID и не расходует новый. Изменённый intent с тем же
`creation_request_id` отклоняется `request_reuse_conflict`.

Result item:

```json
{
  "id": "op-create",
  "kind": "create_task",
  "task_id": "0042",
  "statement_revision": 1,
  "creation_replayed": false
}
```

Операция изменяет только Requirements store. В ней нет Sprint, claim, Workspace,
process или Execution setup.

### 5.5 `revise_task`

```json
{
  "id": "op-revise",
  "kind": "revise_task",
  "task_id": "0042",
  "expected_statement_revision": 1,
  "statement": {
    "goal": "Make Requirements independently callable.",
    "task_requirements": [
      {
        "key": "R01",
        "status": "current",
        "text": "Requirements uses its own store.",
        "applications": ["APP-REQ"]
      },
      {
        "key": "R02",
        "status": "current",
        "text": "Replay remains stable.",
        "applications": ["APP-REQ", "APP-AUDIT"]
      }
    ],
    "definition_of_done": ["No foreign DB read is required."]
  }
}
```

Новая полная statement revision заменяет live statement, но не старую revision и не
старые snapshots. Keys, удалённые из новой revision, остаются в истории. Требование,
добавленное к текущей Task, поэтому не может молча изменить ранее опубликованный
snapshot.

### 5.6 `publish_task`

```json
{
  "id": "op-publish",
  "kind": "publish_task",
  "task_id": "0042",
  "statement_revision": 2
}
```

Requirements читает registry и statement в одной consistent transaction view,
проверяет обе adjacency chains, отсутствие unresolved current gaps и создаёт следующий
immutable `snapshot_revision`.

Result item:

```json
{
  "id": "op-publish",
  "kind": "publish_task",
  "task_id": "0042",
  "statement_revision": 2,
  "snapshot_revision": 2
}
```

Повтор в рамках exact outer `request_id` replay возвращает тот же result. Публикация
того же statement отдельным новым mutation request не создаёт дубликат: возвращает
существующую пару revisions с `changed: false`.

### 5.7 `record_outcome`

```json
{
  "id": "op-outcome",
  "kind": "record_outcome",
  "outcome": {
    "outcome_id": "exec-0042-17",
    "task_id": "0042",
    "snapshot_revision": 2,
    "kind": "positive",
    "satisfied_task_requirements": ["R02"]
  }
}
```

`outcome_id` immutable по payload. Exact повтор не создаёт второй факт. Другой payload
с тем же ID получает `outcome_conflict`. Requirements не вызывает Execution в ответ.

### 5.8 `import_slice`

`import_slice` — owner-only recovery/migration operation, а не способ синхронизации
между приложениями:

```json
{
  "id": "op-import",
  "kind": "import_slice",
  "slice": {
    "schema": "stride-requirements-slice/v1",
    "task_id": "0042",
    "statements": [],
    "snapshots": [],
    "outcomes": []
  }
}
```

Import проверяет schema, identity, revision continuity и digest-equivalence всех уже
существующих записей до записи. Любой конфликт отклоняет весь batch. Он не импортирует
Sequencing/Execution state и не пишет в их stores.

## 6. Consistent read batch: `query`

Request:

```json
{
  "schema": "stride-requirements/v1",
  "operation": "query",
  "input": {
    "queries": [
      {"id": "q1", "kind": "coverage", "mode": "current"},
      {"id": "q2", "kind": "get_task", "task_id": "0042"}
    ]
  }
}
```

Query IDs непусты и уникальны; count ограничен `batch.max_items`. Все queries одного
packet наблюдают одну `revision`. Query не выполняет repair/migration и не меняет
owner state.

Успех:

```json
{
  "schema": "stride-requirements/v1",
  "status": "read_only",
  "revision": 18,
  "results": [
    {"id": "q1", "value": {}},
    {"id": "q2", "value": {}}
  ]
}
```

### 6.1 Registry CRUD/read queries

- `get_requirement`: `{id, kind, requirement_id}` → exact current record.
- `search_requirements`: `{id, kind, filter, limit, after_id}` → sorted records;
  `filter` может содержать только `level`, `status`, `text_contains`; `limit` positive
  и не больше configured max; `after_id` string или `null`.
- `registry`: `{id, kind}` → current requirements + System/Application links.
- `history_requirement`: `{id, kind, requirement_id}` → ordered immutable revisions.

### 6.2 Graph queries

- `chain`: `{id, kind, level, identity}`. Для `system` identity — requirement ID;
  для `application` — requirement ID; для `task` — `{task_id, key, statement_revision}`.
  Result содержит exact node и все соседние non-obsolete parents/children, не один
  произвольно выбранный parent.
- `coverage`: `{id, kind, mode}` where mode=`analysis|current`; result отдельно
  перечисляет `system_without_application` и `application_without_task`.
- `gaps`: `{id, kind}` → unresolved links + projected placeholders. Future placeholder
  остаётся gap/evidence absence и не считается satisfaction.

### 6.3 Task/snapshot/outcome queries

- `get_task`: `{id, kind, task_id}` → current statement + latest revisions.
- `task_history`: `{id, kind, task_id}` → все statement revision headers.
- `snapshot`: `{id, kind, task_id, snapshot_revision}` → immutable full snapshot.
- `satisfaction`: `{id, kind, task_id, snapshot_revision}` → per Task/Application/System
  `{applicable, satisfied, evidence_outcome_ids}`.
- `export_slice`: `{id, kind, task_id}` → canonical
  `stride-requirements-slice/v1`; это owner read, без foreign state.

Неизвестная identity получает отказ всего query packet `not_found`; v1 не возвращает
частично успешный mixed packet, чтобы caller не смешивал разные наблюдения/ошибки.

## 7. Stable rejection envelope

Любой отказ имеет точную форму:

```json
{
  "schema": "stride-requirements/v1",
  "status": "rejected",
  "error": {
    "code": "revision_conflict",
    "message": "Requirements revision changed",
    "retryable": false,
    "details": {
      "expected_revision": 17,
      "actual_revision": 18
    }
  }
}
```

`message` предназначен человеку и не является машинным discriminator. Caller обязан
ветвиться по `error.code`.

Коды v1:

| code | Смысл | Запись |
| --- | --- | --- |
| `invalid_json` | stdin не является допустимым UTF-8 JSON | нет |
| `unsupported_schema` | schema не равна `stride-requirements/v1` | нет |
| `invalid_packet` | неверные/лишние поля envelope или input | нет |
| `unknown_operation` | неизвестная operation/kind | нет |
| `limit_exceeded` | превышены input/batch/search limits | нет |
| `revision_conflict` | store/statement expected revision устарела | нет |
| `request_reuse_conflict` | request ID уже связан с другим payload | нет |
| `not_found` | canonical identity отсутствует | нет |
| `already_exists` | explicit create конфликтует с существующей identity | нет |
| `immutable_field` | попытка изменить level/identity/history | нет |
| `referential_conflict` | delete/unlink нарушает текущую целостность | нет |
| `unresolved_gaps` | publication не имеет полной applicable chain | нет |
| `id_namespace_exhausted` | allocator не имеет свободного ID | нет |
| `outcome_conflict` | outcome ID повторён с другим payload | нет |
| `unsupported_policy` | требуется политика T031/T037, ещё не определённая v1 | нет |
| `store_busy` | owner-local store не получен в configured bounded timeout | нет |
| `store_incompatible` | schema/migration owner store неизвестна | нет |

`store_busy` может иметь `retryable: true`; остальные перечисленные deterministic
contract/domain errors — `false`. Никакой rejection после начала batch не оставляет
частичные owner writes или потреблённый новый Task ID.

## 8. Literal fixtures

Эти fixtures являются contract examples и не зависят от production serializer.

### F1 — M:N сохраняет всех родителей

Initial revision `4` содержит `SYS-A`, `SYS-B`, `APP-X`.

Request:

```json
{"schema":"stride-requirements/v1","operation":"apply","input":{"request_id":"f1","expected_revision":4,"operations":[{"id":"l1","kind":"link","relation":"system_application","parent":"SYS-A","child":"APP-X"},{"id":"l2","kind":"link","relation":"system_application","parent":"SYS-B","child":"APP-X"}]}}
```

Expected success: `status=applied`, `revision=5`, оба result item успешны. Следующий
`chain` для `APP-X` возвращает **оба** System parent. Добавление второго не заменяет
первый.

### F2 — Task ID выдаётся только Requirements

Request на revision `5`:

```json
{"schema":"stride-requirements/v1","operation":"apply","input":{"request_id":"f2","expected_revision":5,"operations":[{"id":"c","kind":"create_task","creation_request_id":"draft-77","statement":{"goal":"Contract owner allocates identity.","task_requirements":[{"key":"R1","status":"current","text":"Use canonical identity.","applications":["APP-X"]}],"definition_of_done":["Consumers use returned ID."]}}]}}
```

Expected success includes one configured canonical `task_id` and
`statement_revision=1`. Observable side effect is only Requirements revision `6`;
Sequencing/Execution are not contacted. Exact replay of the entire packet returns
that same `task_id`, revision `6`, `replayed=true`.

Changing `goal` while keeping outer `request_id=f2` is rejected with
`request_reuse_conflict`; revision remains `6` and no allocator candidate is consumed.

### F3 — новое обязательство не переписывает snapshot

Given Task `0042`, statement 1 и published snapshot 1. `revise_task` with
`expected_statement_revision=1` adds `R2` and succeeds as statement 2. Reading
`snapshot_revision=1` still returns only old Task requirements. Only explicit
`publish_task(statement_revision=2)` creates snapshot 2 containing `R2`.

### F4 — positive outcome требует все applicable Task requirements

Snapshot 2 has `R1` and `R2`, both linked to `APP-X`.

1. Positive outcome `o1` satisfying only `R1` → `R1.satisfied=true`,
   `R2.satisfied=false`, `APP-X.satisfied=false`.
2. Positive outcome `o2` satisfying `R2` → both Task requirements and
   `APP-X.satisfied=true`.
3. A `cancelled` outcome with empty satisfied list changes no satisfaction.
4. A `cancelled` outcome naming `R2` is rejected with `invalid_packet`; no state
   changes.

### F5 — stale revision is all-or-nothing

Given store revision `12`, request says `expected_revision=11` and contains a valid
`put_requirement` plus `create_task`. Expected response:

```json
{"schema":"stride-requirements/v1","status":"rejected","error":{"code":"revision_conflict","message":"Requirements revision changed","retryable":false,"details":{"expected_revision":11,"actual_revision":12}}}
```

Requirement is not changed, Task is not created, allocator position is unchanged.

### F6 — future placeholder не является coverage/satisfaction

A `future` Application requirement with empty text may be stored for projection, but
`gaps` returns it as `projected_placeholder`. `publish_task` for a current Task that
uses it rejects `unresolved_gaps`. Ни coverage evidence, ни satisfaction из пустого
placeholder не выводятся.

### F7 — owner-only transfer не захватывает чужое состояние

`export_slice(task_id=0042)` returns only `stride-requirements-slice/v1` statement,
snapshot and outcome history owned by Requirements. В slice нет Sprint membership,
claim, Workspace, process graph или evidence. `import_slice` с изменённым уже
существующим snapshot digest отклоняет весь batch и не выполняет foreign writes.

## 9. Concurrency, security и observability

Concurrency:

- mutation batches сериализуются owner-local store transaction/lock;
- optimistic `expected_revision` предотвращает lost update;
- read batch получает один consistent revision;
- replay проверяется до нового allocation/mutation внутри той же transaction;
- нет distributed lock/transaction между приложениями.

Security boundary:

- публичный protocol принимает значения, но не пути к чужим DB;
- `import_slice` ограничен owner recovery/migration policy и не является произвольным
  filesystem import;
- diagnostics не должны выводить secrets из конфигурации;
- неизвестная store schema не мигрируется автоматически.

Observability:

- machine evidence — response `schema/status/revision/replayed/error.code` и immutable
  owner history;
- логирование не является authority и не меняет результат операции;
- consumer подтверждает конкретный `task_id`/`snapshot_revision`, а не “latest” скрытым
  чтением.

## 10. Impact analysis и downstream contracts

Затрагивается будущий `apps/requirements/**`: standalone adapter, domain/store,
Requirements config и owner tests. При extraction должны быть сохранены текущие
инварианты `RequirementsCommands`, `RequirementsRegistry`, `RequirementsStore`,
`TaskRequirementsGate` и Task ID allocator, но без переноса объединённого Task UoW.

Не затрагиваются семантически:

- Sequencing ownership of registration, membership, dependency, eligibility, claim,
  release and Workspace;
- Execution ownership of process/stages/checks/restart/evidence/outcome generation;
- Poise session/role protocol;
- Bootstrap/UAT responsibilities.

Downstream T011/T012 должны принимать canonical ID/revision явно. T018 реализует
самостоятельный Requirements CLI/config. T031/T037 владеют отложенной physical
auxiliary schema и полной attempt/restart recalculation policy. До появления этих
контрактов Requirements отвечает `unsupported_policy`, а не читает соседний store.

Главные риски реализации: перенос старого allocator без shared Task/Sprint DB,
сохранение exact replay при генерации ID, миграция immutable snapshots из Task
metadata и недопущение второго canonical store. Это prerequisites будущей реализации,
не повод расширять публичную границу T010.

## 11. Requirement mapping

| Task requirement | Contract evidence |
| --- | --- |
| R01 / REQ-01 | §4.1–4.2, §6.2, F1: обе adjacency M:N, reverse chain сохраняет всех родителей |
| R02 / REQ-06 | §4.2, §5.4, F2: Requirements allocates canonical `task_id`; downstream fan-out отсутствует |
| R03 / REQ-09 | §4.2–4.3, §5.5–5.6, F3: statement revision + immutable snapshot publication |
| R04 / REQ-11 | §4.4, §5.7, §6.3, F4: positive outcomes aggregate all applicable Task requirements; cancelled не удовлетворяет |
| R05 / REQ-14 | §5–6, F1/F3/F6/F7: CRUD/search/chains/coverage/gaps/history/snapshots/transfer |
| R06 / GEN-03 | §1, §3, §5.8, §9: independent config/store, no private DB reads or foreign writes |
| R07 / GEN-04 | §2–3, §9: configured invocation/paths/limits; no old absolute paths or built-in target list |

## 12. Design verification oracle

Design считается согласованным только если независимые literal fixtures могут
проверить одновременно:

1. success, rejection и exact replay без импорта production serializers;
2. M:N на обеих adjacency и неизменяемость исторического snapshot;
3. выдачу и replay canonical Task identity без hidden Sequencing/Execution writes;
4. outcome semantics без vacuous или cancelled satisfaction;
5. CRUD/search/chains/coverage/gaps/snapshots/transfer через owner boundary;
6. configured invocation/store без installation-path assumptions;
7. stable rejection codes и отсутствие partial writes на отказе.

Executable provider/consumer qualification относится к последующей реализации и не
может быть объявлена выполненной этим design-only Task.
