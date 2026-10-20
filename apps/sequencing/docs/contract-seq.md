# Sequencing public contract

Статус: целевой публичный контракт самостоятельного Sequencing.

Реализованный срез включает собственный CLI/config, хранилище формата 2,
`register_task`, `register_sprint`, чтение полного текущего значения Task/Sprint,
доступности `eligibility` и кандидатов `next` относительно явной Session,
а также `claim_task`, `switch_claim`, `release_claim`, `resume_claim`, `recover_claim`
и `workspace` с `action: "prepare"`, `record_stage_label`,
`record_execution_outcome`, `register_duplicate`, чтение `execution_facts`
и `duplicate_family`.
Процедуры описаны в [руководстве CLI](shell-seq.md) и
[руководстве хранилища](storage-seq.md). Независимая работа — Task вне Sprint;
Sprint использует ту же выданную identity с явно зарегистрированным membership.

Текущий отказ CLI содержит `schema`, `status: "rejected"`, строку `reason` и
exit 2. Обычные смысловые конфликты используют `identity_conflict`,
`already_exists`, `dependency_conflict`, `request_reuse_conflict`,
`revision_conflict`; диагностика формы и типов содержит причину.
Текущие операции владения используют также `task_not_found`, `claim_conflict`,
`session_claim_conflict`, `claim_owner_mismatch`, `switch_target_conflict`,
`not_eligible`, `handoff_not_found`. Их порядок описан в §5.4–5.6, §5.9–5.10;
детали blockers читаются через `eligibility`. Последующие разделы описывают
также целевые команды revise, независимого владения и освобождения Workspace,
`record_terminal`, `cancel_task`, transfer и расширенный профиль структурированных ошибок. Эти
будущие команды и профиль не заявлены
как реализованные нынешним CLI. Текущие операции фактов описаны в §5.11–5.12,
их чтение — в §7.5; регистрация и чтение семейства — в §5.13 и §7.6.
Целевые `record_terminal` и `cancel_task` остаются будущими
операциями. Примеры ниже с условными claim/handoff ID
показывают строки: реально выделенные идентификаторы непрозрачны, caller
сохраняет возвращённые значения без предположений об их формате.

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
  "input": {"queries": [{"id": "q1", "kind": "resumable", "sprint_id": null}]}
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

### 2.1 Типы, canonical payload и порядок обработки

Все перечисленные поля request обязательны, кроме явно отмеченных optional; explicit
`null` не равен отсутствию поля. Неизвестные поля отклоняются на каждом уровне request.
Идентификатор — непустая строка, удовлетворяющая configured identifier policy; это не
filesystem path. Revision — integer >= 0, не boolean; revision существующего statement
или snapshot >= 1. Commit — 40 lowercase hexadecimal characters. Списки ID не содержат
дубликатов. Время не используется для неявного истечения claim.

Canonical payload для replay — UTF-8 JSON: object keys лексикографически отсортированы
по Unicode code point; нет insignificant whitespace; Unicode не нормализуется и не
заменяется ASCII escapes; порядок array сохраняется. Типы — object/array/string/integer/
boolean/null; floating-point числа контракт не принимает. Digest — SHA-256 этих bytes.
Digest request включает `schema`, `operation` и все поля logical input, включая
`expected_revision`, но не
transport newline. Повторяющиеся object keys, неверные типы и лишние поля дают
`invalid_packet`; синтаксически неверный JSON или UTF-8 — `invalid_json`.

Порядок отказов для `apply`:

1. Проверить byte bound, JSON, schema, operation и полный shape/bounds packet.
2. Под локальной write reservation проверить outer request replay: exact возвращает
   прежний receipt; changed payload даёт `request_reuse_conflict`.
3. Для нового request сравнить `expected_revision`; stale даёт `revision_conflict`
   **до** любых проверок eligibility/claim. Это определяет F3/F8.
4. Выполнить operations в порядке request, проверяя их preconditions на текущем
   transaction-local состоянии; первый отказ откатывает **весь** batch.

Каждый новый успешный owner-only batch увеличивает store revision ровно на один,
включая batch только с `changed: false`: сохранение нового replay receipt является
owner write. Exact outer replay не увеличивает revision. Result items следуют порядку
operations; read results — порядку queries. Множества Task/edge/blocker в read outputs
упорядочены лексикографически по ID (edges: predecessor, successor, kind).

Receipt exact replay — исторический результат, а не свежая гарантия владения.
Повтор старого успешного claim после release не активирует его снова. Перед операцией,
требующей текущего claim, caller использует current query; сама mutation заново
проверяет current authority. Diagnostics не заменяет эту проверку.

### 2.2 Полные result variants для `apply`

Каждый result item имеет обязательные `id`, `kind`, `changed`; `id` — ID operation.
К ним добавляются **ровно** поля следующего variant:

| kind | Дополнительные поля результата |
| --- | --- |
| `register_task` | `task_id` и ровно одно из `requirements_statement_revision`, `requirements_snapshot_revision` |
| `register_duplicate` | `task_id`, `parent_id`, `sprint_id`, `sprint_revision` |
| `register_sprint`, `revise_sprint` | `sprint_id`, `sprint_revision` |
| `claim_task`, `resume_claim` | `task_id`, `claim_id`, `claimant`, `claim_replayed` |
| `switch_claim` | `task_id`, `claim_id`, `claimant`, `claim_replayed`, `previous_task_id`, `previous_claim_id`, `handoff_id` |
| `release_claim`, `recover_claim` | `task_id`, `claim_id`, `handoff_id` |
| `record_stage_label` | `task_id`, `label_id`, `fact_replayed` |
| `record_execution_outcome` | `task_id`, `outcome_id`, `state`, `fact_replayed` |
| `record_terminal`, `cancel_task` (целевые) | `task_id`, `terminal_id`, `state` |

`changed`, `claim_replayed` и `fact_replayed` — boolean.
Поле `state` результата `record_execution_outcome` — `available|completed|cancelled`;
при inner replay это состояние исходного факта. У целевых `record_terminal` и
`cancel_task` диапазон поля `state` — `completed|cancelled`.
`handoff_id` непуст: release всегда сохраняет
owner handoff, включая Task без Workspace.
Новая Sprint имеет `sprint_revision=1`; accepted revise увеличивает её на один.
Эта таблица и envelope §5 определяют полный wire result; последующие literal
responses включают обязательные поля своих variants.

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
Ссылка содержит ровно один вид происхождения: `requirements_statement_revision`
для текущей постановки либо `requirements_snapshot_revision` для опубликованного
снимка. Оба номера — положительные integer; они принадлежат разным счётчикам.
Для StatementRef в показанной форме вместо snapshot-поля возвращается только
statement-поле с переданной редакцией. Requirements выдаёт ID и statement revision
уже для постановки только с целью, с `snapshot_revision: null`; такая работа
регистрируется до публикации снимка. Caller передаёт фактически выбранный вид и
номер ссылки. Sequencing сохраняет исходную ссылку неизменной и не копирует
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
  "sprint_revision": 1,
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
в Sprint. При нескольких predecessors eligibility требует выполнения **всех** edges:
один cancelled или non-terminal predecessor достаточен для `eligible=false`, независимо
от успешности остальных. Eligible non-terminal Task без predecessors не имеет blockers.

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
claim creation происходят в одной owner-local transaction/revision boundary.
`claimant` — **каноническая непрозрачная identity Session**, переданная публичным
владельцем Session, а не имя агента, процесса, роль или новый alias для каждой Task.
Примеры `agent-a`/`agent-b` обозначают две разные Session. Sequencing не создаёт Session
и не читает её приватную БД; доступ к локальному CLI/config/store ограничивается
настроенной доверенной вызывающей стороной. Сравнение строк claimant не объявляется
аутентификацией произвольного удалённого клиента.

SEQ-05 требует два независимых ограничения одновременно:

- не более одного active claim на `task_id`;
- не более одного active Task claim на `claimant` во всём Sequencing store, включая
  разные Sprint и standalone Tasks.

Оба ограничения проверяются и сохраняются в **той же** owner-local transaction.
Два concurrent claim на одну Task или от одной Session на разные Task не могут
оба succeed. Уже занятая Task даёт `claim_conflict`; свободная eligible Task при
другой active Task у claimant — `session_claim_conflict`. `resume_claim` не обходит
эти ограничения. Release не переносит чужую Task и не означает смену Session identity.

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

`kind` этой проекции — `completed` или `cancelled`. Текущий
`record_execution_outcome` принимает positive с явным `result_commit: null` и
сохраняет completed. Для удовлетворения result-edge требуется переданный commit;
при null проекция доступности возвращает `missing_predecessor_result`. Completion-edge
требует только completed. Для cancelled ссылка результата равна null.
`terminal_id` текущей проекции равен принятому `outcome_id` из §5.12.

Sequencing не вычисляет Execution success и не принимает log/evidence как completion.
`terminal_id` immutable по canonical payload; conflicting reuse отклоняется.

### 4.6 Семейство задач

Requirements готовит постановку новой задачи и выдаёт её канонический ID и ссылку.
Sequencing регистрирует этот ID как самостоятельную Task с явным исходным родителем
и выбранным черновиком Sprint. Источник может быть независимой Task, участником того
же или другого Sprint, в том числе уже завершённым. При указании дочерней Task
родителем новой записи становится исходный корень семейства.

Каждая Task сохраняет собственные постановку, состояние, членство, claim, Workspace
и результат. Новая дочерняя Task получает `available`, переданную ссылку требований
и выбранный Sprint; её claim, Workspace, terminal и полученные факты равны null.
Чтение семейства показывает текущие сведения родственников. У его участников
полная карточка Task дополнена одним полем `duplicate`:

```json
{
  "kind": "duplicate",
  "role": "parent",
  "parent_id": "0042",
  "children": ["0043"]
}
```

Для дочерней Task того же семейства:

```json
{
  "kind": "duplicate",
  "role": "child",
  "parent_id": "0042",
  "siblings": []
}
```

`children` и `siblings` отсортированы по ID; siblings содержит остальных детей.
Обычная Task сохраняет форму §4.1. Родство неизменно, а состояния и владельцы
читаются актуальными. Task claim и Workspace binding независимы: карточка сохраняет
действительного владельца каждого ресурса, включая null binding при active claim.

Execution владеет процессом, сравнением стадий и решением о принятии результата.
Sequencing показывает переданные метки/исходы как факты. Для dependency `result`
применяется собственный результат указанного predecessor после успешного выполнения
всех необходимых предшественников (§7.2); родство сохраняет отдельность этих результатов.
Регистрация семейства добавляет только явное членство нового ребёнка.

Сохранено исходное решение о выводе активного замещения (`replace_task`) из
эксплуатации. Текущая работа использует регистрацию семейства и явные зависимости;
исторические решения остаются у прежнего владельца истории. Эта поставка описывает
перенос родства, а не перенос исторических данных или новый механизм замещения.

## 5. Mutation batch: `apply`

Request:

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "apply",
  "input": {
    "request_id": "seq-2026-09-27-001",
    "expected_revision": 31,
    "operations": [{"id": "op-1", "kind": "register_task", "task_id": "0042", "requirements_snapshot_revision": 2}]
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
    {"id": "op-1", "kind": "register_task", "changed": true, "task_id": "0042", "requirements_snapshot_revision": 2}
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

Для постановки до публикации снимка вместо `requirements_snapshot_revision`
передаётся ровно одно поле `requirements_statement_revision` с фактической
положительной редакцией постановки. Goal-only постановка уже пригодна для такой
регистрации. ID передаётся точно, включая ведущие нули; configured identifier policy
согласуется с политикой выдачи Requirements.

Создаёт только Sequencing record. Requirements сохраняет постановку, Execution —
свой процесс; регистрация этих объектов не создаёт. Новая Task доступна как
Independent вне Sprint. Повтор с тем же видом и номером ссылки даёт
`changed: false`; другой вид или номер — `identity_conflict`. Имеющееся членство
в Sprint при таком повторе сохраняется. Для нового успешного outer request
owner revision увеличивается и при `changed: false`; точный повтор исходного
outer request возвращает сохранённый receipt и revision с `replayed: true`.

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

Все members уже зарегистрированы Sequencing, в том числе предыдущими operations
того же атомарного пакета. Operation задаёт полный desired membership и dependency
graph новой Sprint identity. Одна Task входит в один Sprint; другая одновременно
может оставаться Independent. Первая регистрация создаёт `sprint_revision: 1`
и её историю. Members и dependencies сравниваются как упорядоченные канонические
множества: новый outer request с тем же составом и рёбрами даёт `changed: false`.
Чтение сортирует members лексикографически, рёбра — по predecessor/successor/kind.

Другой состав или граф под существующим Sprint ID даёт `already_exists`.
Ссылка на незарегистрированную Task, чужое членство, ребро вне состава,
повтор направленной пары или цикл дают `dependency_conflict`. Несколько корней
и пустой список рёбер допустимы при первоначальной регистрации. Отказ откатывает
весь пакет, включая предшествующую регистрацию Task, revision и receipt.
Последующее изменение существующего Sprint относится к целевому `revise_sprint`.

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

Sequencing внутри той же transaction сначала проверяет known `claim_request_id`:
exact inner replay возвращает historical result по правилу ниже; changed intent
отклоняется. Только для нового inner intent выполняет:

1. находит Task; неизвестная identity даёт `task_not_found`;
2. проверяет terminal state: `completed`/`cancelled` дают `not_eligible`;
3. убеждается в отсутствии active claim у выбранной Task; иначе `claim_conflict`;
4. применяет общее правило всех зависимостей; отказ даёт `not_eligible`;
5. убеждается в отсутствии другой active Task у этого claimant; иначе
   `session_claim_conflict`;
6. создаёт claim identity и меняет owner state на `claimed`.

При одновременной применимости domain отказов действует этот порядок. Stale outer
revision отклоняется ещё раньше по §2.1. Отказ не расходует claim identity и не
меняет уже принадлежащую claimant Task.

Result:

```json
{
  "id": "claim-1",
  "kind": "claim_task",
  "changed": true,
  "task_id": "0042",
  "claim_id": "claim-0042-agent-a-1",
  "claimant": "agent-a",
  "claim_replayed": false
}
```

`claim_request_id` общий для `claim_task` и `switch_claim`. Он навсегда связывается
с полным operation, кроме correlation-поля `id`: `kind` и все бизнес-поля входят
в намерение. Exact inner replay возвращает прежние ID с `changed: false`,
`claim_replayed: true`; новое внешнее `request_id` сохраняет один новый receipt
и увеличивает revision на один. Текущие claims, Task, history и ресурсы остаются
прежними, в том числе после release, нового владельца или terminal. Changed
intent даёт `request_reuse_conflict` до проверки текущего владения. Первый claim
имеет `changed: true`, `claim_replayed: false`. Для нового владения после release
используется `resume_claim`. Подробности доступности читаются через `eligibility`.
Exact outer replay возвращает весь прежний ответ с `replayed: true`, включая
прежние `changed`/`claim_replayed`, до проверки revision и без новой записи.

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

Claimant/claim ID должны совпасть с текущим active claim; иначе
`claim_owner_mismatch`. `reason` — непустой текст основания, не terminal outcome.
Release атомарно меняет Task на `available`, claim на `released` и создаёт immutable
owner handoff; result возвращает его `handoff_id`. Handoff содержит `handoff_id`,
`task_id`, `previous_claim_id`, ровно одно исходное поле
`requirements_statement_revision` либо `requirements_snapshot_revision`, и `workspace_id`.
Последнее поле explicit null для Task без unreleased Workspace. Каноническая
запись находится в `sequencing_history.data.handoff`; ссылка из результата switch
обозначает эту же запись. Task claim и Workspace binding — независимые ресурсы:
все Workspace bindings, статусы, roots и файлы остаются прежними.

Это owner-state запись, **не** Git snapshot, commit или backup: release не изменяет
файлы и не утверждает, что текущий WIP уже помещён в durable storage. Экспорт Git/WIP
выполняется отдельным явным transfer. При ready Workspace файлы остаются на месте;
destructive cleanup — отдельная Workspace operation. Exact outer replay возвращает
тот же handoff, а новый request с уже released claim отклоняется
`claim_owner_mismatch`. Handoff после успешного resume помечен consumed; он остаётся
в history и не может повторно передать владение.

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

Resume допустим только для unclaimed currently eligible non-terminal Task с exact
не consumed `handoff_id` этой Task и неизменной Requirements provenance. Claimant не
должен владеть другой Task; действуют те же `claim_conflict`, `not_eligible` и
`session_claim_conflict`, что при claim. После поиска Task (`task_not_found`)
сначала проверяется handoff: неизвестный даёт `handoff_not_found`, consumed,
чужой либо с другим исходным Requirements reference — `claim_owner_mismatch`.
Затем выполняются terminal/Task claim/зависимости/Session capacity в порядке §5.4.

Sequencing атомарно создаёт новый claim и добавляет `claim.resumed` с
`consumed_handoff_id`. Исходный handoff сохраняется неизменным; факт consumption
следует из новой записи истории. `claim_replayed: false`; exact outer replay
возвращает historical result. Все Workspace metadata, binding, roots и файлы
сохраняются; эти Task-операции выполняются только в собственном хранилище.
Подготовка постоянного физического контекста реализована в §6.0.
Независимое владение и освобождение Workspace описаны как будущие команды §6.1–6.3.

### 5.7 `record_terminal`

Целевой будущий интерфейс. Текущий приём исходов выполняется через §5.12
`record_execution_outcome` с правилами приёмника и проекции §4.5.

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

Целевой будущий интерфейс. Текущий переданный исход отмены принимается
через `record_execution_outcome` с `kind: "cancelled"` из §5.12.

```json
{
  "id": "cancel-1",
  "kind": "cancel_task",
  "task_id": "0042",
  "terminal_id": "cancel-0042-1",
  "source_ref": {"owner": "orchestrator", "id": "cancellation-0042"}
}
```

Точные поля перечислены в example. `source_ref` имеет ровно непустые `owner` и `id`;
это provenance доверенного локального caller, а не инструкция вызвать другое
приложение. Operation эквивалентна `record_terminal` с `kind=cancelled` и
`result_commit=null`; действует тот же immutable terminal ledger. Active claim
даёт `claim_conflict`; pending Workspace intent — `workspace_conflict` до явного
reconciliation. Нет скрытого release, deletion или acceptance. Result имеет
`task_id`, `terminal_id`, `state: "cancelled"` и общие поля §2.2.

Cancelled predecessor не удовлетворяет completion/result edge; successor остаётся
blocked, пока graph policy явно не изменена. У уже terminal Task допускается только
exact immutable fact; conflicting fact получает `terminal_conflict`. В `record_terminal`
те же запреты на active claim/pending Workspace и тот же validation order.

### 5.9 `switch_claim`

Реализованная атомарная передача одной Session с Task A на Task B:

```json
{
  "id": "switch-1",
  "kind": "switch_claim",
  "from_task_id": "0042",
  "from_claim_id": "claim-0042-agent-a-1",
  "task_id": "0043",
  "claimant": "agent-a",
  "claim_request_id": "switch-0042-0043-agent-a",
  "reason": "Перейти к следующей доступной работе"
}
```

Inner intent проверяется первым по §5.4. Для нового намерения находятся A и её
current active claim: несовпадение `from_claim_id`/`claimant` даёт
`claim_owner_mismatch`. Одинаковые A/B дают `switch_target_conflict`. Затем B
проходит полный порядок §5.4; текущая A разрешена как единственная уже занятая
Task этой Session. Все эти проверки завершаются перед первым изменением.

Одна существующая транзакция освобождает claim A, сохраняет её handoff и создаёт
новый claim B с историей полного switch intent/result. Result содержит
`id`, `kind`, `changed: true`, `task_id` B, её `claim_id`, `claimant`,
`claim_replayed: false`, `previous_task_id` A, `previous_claim_id`, `handoff_id` A.
Отказ любой операции всего пакета откатывает обе Task, claims, историю, receipt
и revision. Workspace bindings и файлы остаются прежними. Применяются те же
правила исторического inner/outer replay, что в §5.4.

### 5.10 `recover_claim`

Оператор сначала останавливает прежнего писателя и читает текущее владение.
Затем передаёт собственное явное решение, основание и точный active claim:

```json
{
  "id": "recover-1",
  "kind": "recover_claim",
  "task_id": "0043",
  "claim_id": "claim-0043-agent-b-1",
  "operator": "operator-session",
  "decision": "Освободить владение остановленной Session",
  "writers_stopped": true,
  "reason": "Оператор завершил прежний процесс"
}
```

`writers_stopped` — JSON boolean `true`; остальные поля строковые и непустые.
Оператор подтверждает фактическую остановку; Sequencing использует это явное
решение. После поиска Task сверяется точный current claim, иначе
`claim_owner_mismatch`. Операция освобождает только этот claim, делает Task
`available` и сохраняет handoff с `claim.recovered`, оператором, решением,
основанием и подтверждением остановки. Result имеет тот же полный набор полей,
что `release_claim`, с `kind: "recover_claim"`.

Оператор сохраняет своё уже существующее владение другой Task, если оно есть.
Для продолжения работы выбранная Session отдельно вызывает обычный
`resume_claim` с возвращённым handoff; все её gates применяются вновь.
Exact outer replay recovery возвращает прежний результат с сохранением текущего
владения. Полная правильная последовательность исполняется в
[руководстве владения](shell-seq.md#владение-переключение-и-восстановление).

### 5.11 `record_stage_label`

Текущая операция передаётся в обычном `apply` с `request_id`,
`expected_revision` и массивом `operations`. Полный operation:

```json
{
  "id": "label-1",
  "kind": "record_stage_label",
  "task_id": "0042",
  "claim_id": "returned-current-claim",
  "claimant": "SESSION-A",
  "label_id": "stage-label-0042-1",
  "label": "Подготовка результата",
  "source_ref": {"owner": "execution", "id": "stage-reference-0042-1"}
}
```

Все показанные поля обязательны, наборы полей точные. Task ID соответствует
настроенной policy. `claim_id`, `claimant`, `label_id`, `label` и оба поля source_ref
содержат непустые строки без NUL, сохраняемые целиком. Метка — непрозрачные данные.
Граф процесса, смысл gate и артефактов принадлежат Execution.

Новая метка требует известную Task и её точный active claim указанной Session.
Success item содержит ровно `id`, `kind`, `changed`, `task_id`, `label_id`,
`fact_replayed`. Новый факт имеет `changed: true`, `fact_replayed: false`.
Полный неизменяемый факт — `task_id`, `label_id`, `label`, `source_ref`;
operation id и переданные поля текущего владения относятся к команде.

`label_id` уникален среди меток всех Task. Точный повтор факта с новым outer
request возвращает `changed: false`, `fact_replayed: true` до проверки текущего
владения. Старый факт сохраняет своё место в истории: последнее принятое значение,
Task state, claim, Workspace и доступность остаются текущими. Изменение полного
факта, включая только owner или id происхождения, даёт `label_conflict`.

### 5.12 `record_execution_outcome`

Текущий приёмник получает явный исход от доверенного локального вызывающего.
Решение об исходе и его основании принимает соответствующий владелец. Caller
передаёт фактическое происхождение и заранее освобождает Task claim после
завершения подготовки Workspace.

```json
{
  "id": "outcome-1",
  "kind": "record_execution_outcome",
  "outcome": {
    "outcome_id": "outcome-0042-1",
    "task_id": "0042",
    "kind": "positive",
    "result_commit": null,
    "source_ref": {"owner": "orchestrator", "id": "accepted-outcome-0042-1"}
  }
}
```

Поля operation и outcome обязательны и точны. `source_ref` имеет ровно owner/id;
это непрозрачные непустые строки без NUL. Owner сохраняется как переданное
происхождение записи.
`outcome_id` — непрозрачная непустая строка без NUL; Task ID следует policy.

| Переданный kind | result_commit | Task state | Task terminal |
| --- | --- | --- | --- |
| `positive` | Явный null либо полный 40-символьный Git ID из строчных hex-символов | `completed` | Проекция §4.5 с kind completed |
| `negative` | Явный null | `available` | null |
| `cancelled` | Явный null | `cancelled` | Проекция §4.5 с kind cancelled |

Это формат текущего потребителя. Формат GitObject производителя и целевой
`record_terminal` имеют собственный договор. Приёмник сохраняет ссылку как
переданные данные; получение реального результата и решение об acceptance
выполняются вызывающей стороной и Execution. Для result-edge передайте ссылку
результата: положительный исход с null принимается, а такое ребро сохраняет
`missing_predecessor_result`.

Новый исход требует известную Task, свободный Task claim и Workspace вне
`reserved`/`release_pending`. Ready, failed и released контекст сохраняются.
Success item содержит ровно `id`, `kind`, `changed`, `task_id`, `outcome_id`,
`state`, `fact_replayed`. После negative возможны новое законное владение и новый
positive с другим outcome_id. Completed/cancelled сохраняют своё терминальное
решение; следующий новый исход получает `outcome_conflict`.

`outcome_id` уникален среди исходов всех Task; пространство ID меток независимо.
Точный полный факт возвращает исторический результат с `changed: false`,
`fact_replayed: true` до проверки текущих условий. Изменение факта, включая только
source_ref.owner/id или допустимый result_commit, даёт `outcome_conflict`.
Историческое поле state в item относится к исходному факту; текущие значения
читаются через Task и execution_facts. Полное source_ref сохраняется и в terminal.

После проверки формы apply сначала проверяет общий outer request, затем revision.
В операции порядок такой: inner replay/conflict, известная Task, terminal state,
claim, подготовка Workspace; у метки после known Task проверяется claim.
Точное повторение outer request возвращает старый ответ с `replayed: true` и
исходной revision без записи. Новый outer request с историческим фактом сохраняет
свою квитанцию и увеличивает revision один раз. Отказ любой операции откатывает
весь пакет. Текущие причины: `label_conflict`, `outcome_conflict`, `task_not_found`,
`claim_owner_mismatch`, `claim_conflict`, `workspace_conflict`; форма/типы дают
диагностический reason в текущем трёхпольном rejected/exit 2.

### 5.13 `register_duplicate`

Используйте обычный `apply` (§5), выданный Requirements новый `task_id`, существующий
`parent_id` и существующий Sprint в состоянии `draft`. Полный operation item:

```json
{
  "id": "duplicate",
  "kind": "register_duplicate",
  "task_id": "0043",
  "parent_id": "0042",
  "sprint_id": "SPR-FAMILY",
  "claimant": "SESSION-A",
  "requirements_statement_revision": 7
}
```

Для опубликованного снимка замените только поле ссылки на
`requirements_snapshot_revision` с его фактическим положительным integer-номером.
Используется ровно один вид ссылки. `task_id`, `parent_id`, `sprint_id` следуют
настроенной политике идентификаторов; `claimant` — переданная непустая непрозрачная
строка без NUL, контекст вызова, а не автоматически создаваемый claim новой Task.
Полный результат operation:

```json
{
  "id": "duplicate",
  "kind": "register_duplicate",
  "changed": true,
  "task_id": "0043",
  "parent_id": "0042",
  "sprint_id": "SPR-FAMILY",
  "sprint_revision": 2
}
```

В одной существующей транзакции создаются новая Task, членство, текущая и полная
историческая редакция назначения и один факт родства. Sprint revision увеличивается
на один; прежние участники, рёбра и снимки сохраняются. Общая store revision и
outer receipt изменяются по §2.1 ровно один раз на новый успешный пакет.
Ошибка последующей операции откатывает весь пакет.

Business identity состоит из нового `task_id`, нормализованного исходного родителя,
точного вида/номера Requirements reference и Sprint назначения. Новый outer request
с той же identity возвращает первоначальные `parent_id`, `sprint_id`,
`sprint_revision` и `changed: false`. ID operation и claimant относятся к контексту
запроса. В качестве источника допустим любой участник того же семейства.
Такое повторение сохраняет членов, историю родства и Sprint revision; новая outer
квитанция увеличивает только общую store revision. Сохранённый номер Sprint в
результате регистрации относится к моменту регистрации, текущий состав читается
через `sprint` и `duplicate_family`.

Для точного outer replay сохраняйте весь исходный packet. Он возвращает исходный
ответ с `replayed: true` до проверки текущей revision и сохраняет всю БД. Перед
следующим решением читайте текущее семейство (§7.6), как и после других historical
replay (§2.1).

Диагностика неизвестного источника — `task_not_found`, назначения —
`sprint_not_found`; для существующего назначения требуется `draft`
(`sprint_not_draft`). Новый ID принадлежит новой Task: занятая обычная identity
даёт `identity_conflict`; изменение сохранённой identity семейства —
`duplicate_conflict`. Общие проверки формы и типов действуют по §2.1.
[Правильная процедура](shell-seq.md#регистрация-семейства-задач) описывает запуск.

## 6. Workspace operation: `workspace`

Workspace имеет отдельную operation, потому что Git/filesystem side effects не должны
маскироваться как owner-only SQLite batch.

### 6.0 Реализованная подготовка

Текущая команда `prepare` создаёт постоянный контекст Task. Следующие §6.1–6.3
описывают будущие acquire/release/inspect/reconcile с отдельным владением Workspace;
они сохраняются как целевой договор. Реализованный `prepare` использует этот пакет:

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "workspace",
  "input": {
    "action": "prepare",
    "request_id": "prepare-0042-1",
    "task_id": "0042",
    "claim_id": "returned-current-claim",
    "claimant": "SESSION-A",
    "read_only": false,
    "roots": {
      "code": ["../shared-code"],
      "main": ["../main-context"],
      "artifact": ["../task-artifacts", "../sprint-artifacts"]
    }
  }
}
```

Все показанные поля обязательны, fieldsets точные. `read_only` — boolean.
Три группы roots — массивы путей; каждый массив может быть пустым. Строки путей
содержат непустой путь без NUL. Task ID соответствует настроенной policy;
request/claim/claimant — непустые непрозрачные строки без NUL. Сохраняйте выданные
идентификаторы целиком. Пути разрешаются относительно каталога конфигурации.

Встроенный `code[0]` — физическое рабочее дерево либо repository в read-only
режиме. `roots.code` задаёт дополнительные корни с position 1…N; main/artifact
имеют position 0…N−1. Порядок внутри каждой группы сохраняется; DTO сортирует
записи по kind,position. Полный успешный ответ имеет ровно поля `schema`,
`status: "workspace_ready"`, `revision`, `workspace`, `replayed`.
`workspace` содержит `workspace_id`, `task_id`, `repository_id`, `base_commit`,
`branch`, `status`, `read_only`, `roots`, `path`, `owner_session_id`.
Последнее поле отражает существующую независимую binding; новый контекст имеет null.

Writable подготовка наблюдает настроенный base_ref один раз, сохраняет этот commit,
путь и ветку перед Git. Read-only использует фактический HEAD repository и пустую
строку branch. Освобождение Task claim и следующий законный claim сохраняют этот
контекст. Новый совместимый запрос возвращает его с новой revision и receipt.

Проверка формы предшествует данным. Затем общий request slot проверяет точное
намерение: изменённый пакет с тем же ID получает `request_reuse_conflict`.
Завершённый точный replay возвращает исходный ответ с `replayed: true` до проверки
текущего claim, сохраняя данные и revision. Новый либо незавершённый запрос требует
известную Task и точный действующий claim указанной Session. Порядок последующих
проверок: состояние Workspace, затем совпадение режима и корней.

Причины текущего отказа: `task_not_found`, `claim_owner_mismatch`,
`workspace_pending`, `workspace_failed`, `workspace_release_pending`,
`workspace_released`, `workspace_conflict`. Ошибки Git используют диагностические
строки с префиксами `workspace_git_failed:`, `workspace_git_timeout:` либо
`workspace_checkout_incomplete:`. Действует текущий трёхпольный rejected/exit 2,
а не будущий расширенный error profile. Точное хранение и операторское продолжение
описаны в [модели подготовки](storage-seq.md#постоянная-подготовка-workspace),
правильный запуск — в [инструкции](shell-seq.md#подготовка-постоянного-workspace).

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
    "repository_id": "ai-poise",
    "base_commit": "0123456789abcdef0123456789abcdef01234567",
    "branch": "tasks/0042",
    "path": "/resolved/configured/worktrees/0042",
    "status": "ready"
  },
  "replayed": false
}
```

Crash after reservation is recoverable by exact replay: Sequencing verifies expected
branch/path/head before completing. It never resets a dirty/foreign workspace to force success.

### 6.2 Release

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "workspace",
  "input": {
    "action": "release",
    "request_id": "release-ws-0042-1",
    "task_id": "0042",
    "workspace_id": "ws-0042-1",
    "authority": {"kind": "claim", "claim_id": "claim-0042-agent-a-1", "claimant": "agent-a"}
  }
}
```

`authority` — tagged union: exact поля `kind=claim, claim_id, claimant` для текущего
claim либо `kind=terminal, terminal_id` для exact сохранённого terminal fact и
разрешённого configuration terminal cleanup. Других полей/режимов нет. Несовпадение
active authority — `claim_owner_mismatch`; terminal fact — `terminal_conflict`.

Release маркирует `release_pending`, выполняет configured bounded removal после
проверок и сохраняет `released`. Этот v1 release **не принимает** произвольный флаг
«discard WIP». Dirty/untracked WIP всегда даёт `workspace_dirty`; сначала требуется
явное сохранение/перенос и clean working tree. Чужой path/branch — `workspace_conflict`.
Удаление worktree не удаляет Task, Git branch или retained commit history и не
освобождает claim. Логический workspace ID никогда не назначается другой Task.

Успех имеет ровно `schema`, `status: "workspace_released"`, `revision`, `workspace`,
`replayed`. `workspace` — полная модель §4.4 со `status: "released"`; `replayed` boolean.
Ready/acquire success аналогичен, но `status: "workspace_ready"` и Workspace `status=ready`.
Пример §6.1 содержит полный wire output, включая `task_id`, `repository_id` и `status`.

### 6.3 Inspect/reconcile

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "workspace",
  "input": {"action": "inspect", "task_id": "0042", "workspace_id": "ws-0042-1"}
}
```

Inspect — read-only проверка одной известной reservation и её физического состояния.
Не имеет `request_id`, не пишет observation в store и не создаёт claim. Success имеет
ровно `schema`, `status: "workspace_inspected"`, `revision`, `workspace`, `observation`.
Workspace — §4.4. Observation имеет ровно `exists` (boolean), `branch` (string|null),
`head_commit` (commit|null), `dirty` (boolean|null), `matches_binding` (boolean).
При отсутствии пути branch/head/dirty равны null. Наблюдение Git не объявляется
атомарным snapshot с SQLite: revision относится к прочитанной owner binding;
перед последующей mutation owner повторяет необходимые external safety checks.

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "workspace",
  "input": {
    "action": "reconcile",
    "request_id": "reconcile-ws-0042-1",
    "intent_request_id": "workspace-0042-1",
    "task_id": "0042",
    "workspace_id": "ws-0042-1",
    "authority": {"kind": "claim", "claim_id": "claim-0042-agent-a-1", "claimant": "agent-a"}
  }
}
```

Reconcile — явно вызванное восстановление exact owner intent в
`reserved/release_pending/failed`. `authority` — union §6.2; `intent_request_id` —
исходный acquire/release request, не новый basename или path. Новый `request_id`
отличается от ID исходного intent: changed payload не выдаётся за его exact replay.
Operation проверяет фактический path/branch/repository и завершает **прежний** intent,
не создаёт новый baseline, claim или произвольный filesystem import. Unknown intent
даёт `not_found`; mismatch Task/Workspace/intent — `workspace_conflict`.

Success — `workspace_ready` или `workspace_released` по исходному intent, с полями §6.2.
Каждая новая durable owner phase увеличивает revision ровно на один; read-only inspect
и replay уже завершённого request её не увеличивают. Reconcile завершённого intent
возвращает его binding без внешней мутации; новый reconcile receipt — отдельная owner
запись. Crash/retry не выдаёт незавершённый external intent за success: ошибки содержат
`error.details.intent_request_id`, `workspace_id` и `workspace_status`.

Acquire для Task с ready Workspace возвращает **тот же** workspace после проверки
binding; claim release/resume не разрешает создавать замену. Explicit физическое
release сохраняет tombstone binding; восстановление требует сохранённого Git/WIP
материала, а не переиспользования ID для другой Task.

Для non-terminal Task с новым законным claim и `released` binding acquire выполняет
`released → reserved → ready` **с теми же** workspace_id, Task, branch и base_commit.
Источник восстановления — retained собственная Git branch, содержащая base_commit;
её точный наблюдаемый HEAD записывается как target commit нового restore intent до
filesystem mutation. Конфигурационный base_ref не перечитывается. Отсутствующий или
чужой retained branch/material даёт `workspace_conflict`, а не создание пустой замены.
Exact replay использует записанный target commit, а не сдвинувшийся позже HEAD.
Dirty/untracked WIP не мог быть удалён через v1 release и не реконструируется из
предположений. Ни acquire, ни reconcile не сбрасывает изменившийся законный Task HEAD
к base_commit.

## 7. Consistent read batch: `query`

Request:

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "query",
  "input": {
    "queries": [
      {"id": "q1", "kind": "eligibility", "task_id": "0043", "claimant": "SESSION-GUIDE"},
      {"id": "q2", "kind": "sprint", "sprint_id": "PS-DEV-002"}
    ]
  }
}
```

Все queries одного packet наблюдают одну Sequencing `revision` в общей read-транзакции.
Подготовленное хранилище формата 2 сохраняет данные и revision при чтении.
Первоначальная подготовка схемы описана в [руководстве
хранилища](storage-seq.md#переход-к-формату-2).
Caller передаёт уже выданный публичным владельцем Session канонический ID в `claimant`;
Sequencing использует его как непрозрачную непустую строку.

Успех:

```json
{
  "schema": "stride-sequencing/v1",
  "status": "read_only",
  "revision": 33,
  "results": [
    {"id": "q1", "value": {"task_id": "0043", "eligible": false, "blockers": [{"predecessor": "0042", "kind": "result", "reason": "predecessor_not_completed"}], "result_provenance": []}},
    {"id": "q2", "value": {"sprint_id": "PS-DEV-002", "sprint_revision": 1, "members": ["0042", "0043"], "dependencies": [{"predecessor": "0042", "successor": "0043", "kind": "result"}]}}
  ]
}
```

### 7.1 Task/Sprint reads

- `task`: `{id, kind, task_id}` → current owner record, claim/workspace/terminal refs.
- `sprint`: `{id, kind, sprint_id}` → members, dependencies, sprint revision.
- `eligibility`: `{id, kind, task_id, claimant}` → доступность для указанной Session (§7.2).
- `next`: `{id, kind, claimant, scope}` → `{task_ids: [...]}` доступных кандидатов (§7.2).

Следующие варианты остаются целевыми:

- `work_overview`: `{id, kind, sprint_id}` → eligible, blocked, active/resumable,
  result provenance and current counts derived from authoritative owner records.
- `dependencies`: `{id, kind, task_id}` → exact predecessors/successors and kinds.

### 7.2 Eligibility

`eligibility` возвращает детерминированную проекцию относительно обязательного
`claimant`. Обе формы работы используют одно правило: собственная Task
не завершена и не отменена, действующий Task claim принадлежит caller либо
отсутствует, все предшественники успешно завершены. Для `result`-ребра также
требуется сохранённый `result_commit`. Собственный claim учитывается совместно
со всеми зависимостями. Самостоятельный Workspace binding и занятость Session
другой Task сохраняют своё назначение; ограничения приобретения ресурсов
относятся к будущим mutators.

Проекция имеет ровно четыре поля:

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

Собираются все применимые причины в порядке:

1. `{"reason": "task_terminal", "state": "completed"}` либо состояние `cancelled`.
2. `{"reason": "claimed_by_other", "claim_id": "CLAIM-A", "claimant": "SESSION-OTHER"}`.
3. Причины предшественников по строковому `predecessor` и `kind`:
   `{predecessor, kind, reason: "predecessor_not_completed"}` для любого
   незавершённого предшественника, включая cancelled; либо
   `{predecessor, kind: "result", reason: "missing_predecessor_result"}`
   для completed без сохранённого результата.

`eligible` истинно ровно при пустом `blockers`. `result_provenance` содержит
все имеющиеся ссылки завершённых result-предшественников в порядке их ID,
включая случай, когда другая причина делает Task недоступной. Completion-ребро
требует только успешного завершения и сохраняет своё значение при отсутствии
commit. Чтение сохраняет claims, Workspace и остальные собственные данные.

`next` принимает ровно `{id, kind: "next", claimant, scope}`. Область задаётся
одним из точных объектов:

- `{"kind": "all"}` — все зарегистрированные Task;
- `{"kind": "independent"}` — Task вне Sprint;
- `{"kind": "sprint", "sprint_id": "PS-DEV-002"}` — members указанного Sprint.

Значение — ровно `{"task_ids": [...]}`: уникальные доступные ID в строковом
порядке, с сохранением ведущих нулей. Используется то же правило доступности.
Собственная захваченная работа остаётся видна владельцу при выполненных
зависимостях; чужая захваченная работа получает причину владения. Первоначальная
регистрация Sprint достаточна для участия в выборке; отдельная публикация
или подготовка процесса для чтения не требуется.

### 7.3 Claim/Workspace reads

- `claim`: exact current/saved claim/handoff projection;
- `workspace`: exact reservation/status/path/branch/base commit;
- `resumable`: tasks с released claim + valid preserved workspace/handoff, без нового claim.

Для реализованных `task`/`eligibility` неизвестная Task возвращает
`task_not_found`; для `sprint` и `next` с именованной областью неизвестный Sprint —
`sprint_not_found`. Весь packet получает текущий `status: "rejected"`, `reason`
и exit 2, даже если более ранний query был успешен. Успешный packet имеет
`status: "read_only"`. Перечисленные выше отдельные Claim/Workspace reads
остаются целевыми возможностями.

### 7.4 Точные shapes query и `value`

Query item всегда имеет непустой уникальный `id` и `kind`. Остальные обязательные поля:

| kind | Поля после id/kind | `value` |
| --- | --- | --- |
| `task` | `task_id` | Полная Task record §4.1, у участника семейства также `duplicate` §4.6; claim/workspace/terminal — модель или null |
| `sprint` | `sprint_id` | Полная Sprint record §4.2, включая `sprint_revision` |
| `dependencies` | `task_id` | `{task_id, predecessors, successors}`; arrays edges §4.2 |
| `eligibility` | `task_id`, `claimant` | `{task_id, eligible, blockers, result_provenance}` из §7.2 |
| `next` | `claimant`, `scope` | `{task_ids}` из §7.2 |
| `execution_facts` | `task_id` | `{task_id, stage_label, outcome}` из §7.5 |
| `duplicate_family` | `task_id` | Полное семейство §7.6 либо null для обычной Task |
| `claim` | `task_id` | `{task_id, active_claim, latest_handoff}`; последние два — модель или null |
| `workspace` | `task_id` | `{task_id, workspace}`; reservation §4.4 или null |
| `resumable` | `sprint_id` | Array `{task_id, handoff_id, workspace_id}` для unclaimed non-terminal Tasks с не consumed handoff |
| `work_overview` | `sprint_id` | `{sprint_id, tasks, counts}` по правилам ниже |

Текущий CLI поддерживает строки `task`, `sprint`, `eligibility`, `next`,
`execution_facts`, `duplicate_family`;
остальные строки таблицы описывают будущие варианты. Для `next` обязательны
точные tagged scopes §7.2. В двух последних целевых запросах `sprint_id=null` явно означает все
Sprint и standalone Tasks;
строка выбирает ровно одну существующую Sprint. Для остальных queries null ID недопустим.
Число текущих queries ограничено `read.max_queries`; список непустой,
`id` уникальны в пределах packet. Точные обязательные поля каждого текущего
варианта проверяются до исполнения; caller и scope передаются явно.

`active_claim` — Claim §4.3 или null. `latest_handoff` — immutable поля §5.5 плюс
`consumed` (boolean); это projection изменяемого consumption state, а не переписанный
исторический release receipt. `workspace_id` в resumable может быть null.

Для будущего `work_overview` точный контракт caller и групп подсчёта
уточняется при реализации этого варианта. Текущая caller-relative доступность,
все варианты причин, их порядок и происхождение результата определены §7.2.
Конфликты будущих acquisition-команд §5.4 сохраняют собственный смысл.

Успех §7 содержит типизированные `value` для Task, заблокированной единственным
незавершённым result predecessor, и соответствующей Sprint revision 1.

### 7.5 Текущее чтение полученных фактов

Полный query item: `{"id":"facts","kind":"execution_facts","task_id":"0042"}`.
Он использует обычный query packet и общий read-only ответ с текущей revision.
`value` имеет ровно `task_id`, `stage_label`, `outcome`. Значения двух фактов равны
null до их первого приёма. Stage label содержит ровно `task_id`, `label_id`, `label`,
`source_ref`; outcome — ровно все поля принятого outcome из §5.12. Неизвестная Task
даёт `task_not_found`.

Последний факт выбирается по принятому порядку записи отдельно для меток и исходов.
Точный replay сохраняет текущее последнее значение. Чтение использует один снимок
Sequencing и сохраняет данные. Обычная Task сохраняет прежний DTO; её terminal
отражает positive/cancelled, а отрицательный исход виден в execution_facts.

### 7.6 Текущее чтение семейства

Полный запрос использует общую read-only оболочку:

```json
{
  "schema": "stride-sequencing/v1",
  "operation": "query",
  "input": {
    "queries": [
      {"id": "family", "kind": "duplicate_family", "task_id": "0043"}
    ]
  }
}
```

`value` имеет ровно `parent_id` и отсортированный по `task.task_id` массив `members`.
Каждый элемент содержит ровно `task` — полную текущую карточку §4.1 с `duplicate`
из §4.6 — и `execution_facts` из §7.5. Запрос исходного родителя и любого ребёнка
возвращает одно полное семейство в пределах того же снимка. Отдельный `task`
возвращает ту же карточку участника, отдельный `execution_facts` — те же его факты.
Для зарегистрированной обычной Task `duplicate_family` возвращает null;
неизвестный ID даёт `task_not_found`. Envelope содержит текущую store revision.

Чтение использует одну транзакцию для родства, Task, membership, active claims,
Workspace/независимых bindings/roots и последних принятых меток/исходов. В данных
отражается фактический текущий ресурс каждого участника. Переданные метки остаются
непрозрачными; процесс и правила допуска стадий принадлежат Execution. Номер Sprint
из исторического результата регистрации читается отдельно от текущего семейства.

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

`handoff` — explicit null, если все выбранные Tasks unclaimed, иначе array с exact
`{task_id, claim_id, claimant, reason}` для **всех** выбранных active claims. Чужие или
лишние Task IDs, недостаточная authority или pending Workspace intent отклоняют
preflight до release/export. После preflight локальный release + export intent
атомарно сохраняются в Sequencing DB. Затем создаётся snapshot/package; это external
phase, не distributed transaction. Если эта phase отказала, нет опубликованного
пакета, но release и handoff могут уже быть сохранены: `transfer_incomplete` содержит
`error.details.request_id` и `handoff_ids`. Exact replay продолжает тот же intent,
не захватывает Task обратно и не имитирует успех. WIP не удаляется.

Экспортируемое состояние quiescent: после release writer больше не меняет выбранный
worktree; нарушение этого precondition даёт `transfer_incomplete`, не непроверяемый
смешанный snapshot. Export unclaimed Task не создаёт нового claim.

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

### 8.3 Версия и публикация transfer payload

Контракт пакета v1 — ZIP с единственным `manifest.json` и перечисленными regular
payload files. Manifest имеет ровно `schema`, `owner_store_version`, `task_ids`,
`sprint_ids`, `files`; `schema="stride-sequencing-transfer/v1"`. `owner_store_version`
— положительный integer из standalone Sequencing implementation; неизвестная версия
отклоняется, legacy monolith version не подставляется. `files` — непустой array:
`{path, size_bytes, sha256, kind}`, где kind — `owner_slice`, `git_bundle`, `wip_patch`
или `workspace_metadata`. Owner slice ровно один; Git/WIP части привязаны к exact Task
через Workspace metadata и включают staged/unstaged/untracked материал при наличии.

Manifest не хеширует сам себя. SHA-256 всего ZIP из внешнего `package_digest` покрывает
и manifest, и payload; формат digest — `sha256:` + 64 lowercase hexadecimal characters.
`path` — уникальный относительный POSIX path без empty/dot/dotdot components, backslash,
absolute prefix, NUL и symlinks. ZIP duplicate names, unlisted payload, special files,
conflicting Git refs, size/count/digest mismatch дают `package_invalid`. Output root
и intake root берутся из config; `package_path` import обязан быть внутри разрешённого
intake root. Распаковка не может произвольно писать пути из caller input.

Import не использует байты раньше проверки outer digest и всех payload limits/digests.
Publication в own store и Git/filesystem подготовка разделены durable intent: до
полного validation ни одна Task не становится доступной. Повтор после crash проверяет
свою staged подготовку, не считает её чужим успешным import. Existing опубликованная
Task/Sprint/branch identity другого intent даёт `transfer_conflict`; автоматического
merge/replace нет. Ошибка оставляет только диагностируемый unpublished intent, не
частично доступную Task. Requirements/Execution data не принимаются в owner slice.

Import success содержит ровно `schema`, `status: "imported"`, `revision`,
`package_digest`, `task_ids`, `sprint_ids`, `replayed`. IDs отсортированы; `revision`
относится к публикации в destination store. Export success §8.1 не имеет `revision`,
поскольку является receipt отдельного transport intent; owner slice хранит исходную
revision. Replaying import/export возвращает прежний receipt, а не новый claim или
свежее подтверждение существования package_path.

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

`message` human-oriented; machine branch uses `error.code`. `details` всегда object;
неизвестные будущие diagnostic keys не меняют control flow. Обязательные details для
`revision_conflict` — `expected_revision`, `actual_revision`; для `claim_conflict` —
`task_id`, `claim_id`, `claimant` текущего owner; для `session_claim_conflict` — exact
три поля из F3b. Dynamic messages не используются как schema discriminator.
Unknown Task проверяется перед Task-state domain predicates. Во всех failures
`retryable` — boolean; отсутствие error code/details не является допустимым отказом.

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
| `claim_conflict` | Task уже имеет active claim | нет |
| `session_claim_conflict` | claimant уже владеет другой Task | нет |
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

Exact expected success:

```json
{"schema":"stride-sequencing/v1","status":"applied","revision":11,"replayed":false,"results":[{"id":"r","kind":"register_task","changed":true,"task_id":"0042","requirements_snapshot_revision":2}]}
```

Task `0042` state `available`. Observable writes —
только Sequencing store. Requirements/Execution не вызываются. Exact replay возвращает
revision `11`, same Task identity, `replayed=true`.

### F2 — result dependency сохраняет explicit provenance

Sprint `S` содержит `0042 -> 0043` kind `result`. Пока `0042` не completed,
`eligibility(0043)` возвращает `eligible=false` и blocker `0042`.

После текущего `record_execution_outcome` с positive для `0042` и полным result commit,
тот же query на новой
revision возвращает `eligible=true`, no blockers и result provenance
`{predecessor:"0042", result_commit:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}`. Sequencing не
merge-ит этот commit в
Workspace successor автоматически.

### F2b — несколько predecessors требуют всех completion

Revision `30`: Task `0044` имеет completion edges от `0042` и `0043`. Первый completed,
второй cancelled. `eligibility(0044)` возвращает `eligible=false`, единственный blocker
`{predecessor:"0043", kind:"completion", reason:"predecessor_not_completed"}`.
Если вместо cancelled второй non-terminal, reason — `predecessor_not_completed`.
Только completed **обоих** predecessors убирает blockers. Result-edge дополнительно
требует exact result commit. Ни один из этих query не создаёт claim/Workspace.

### F3 — concurrent claim имеет ровно одного победителя

Task `0043` eligible, no active claim. Два request с разными `claim_request_id`/claimant
конкурируют на одной исходной revision `20`. Ровно один owner transaction succeeds,
возвращает claim и повышает revision до `21`. Второй request всё ещё несёт
`expected_revision=20`, поэтому после serialization получает **ровно** `revision_conflict`;
никогда не два active claims.

Отдельный later request на текущей revision `21` с новым `claim_request_id`, когда active
claim уже существует, получает **ровно** `claim_conflict`. Таким образом stale revision и
active-owner conflict имеют разные детерминированные failure paths.

### F3b — одна Session не может владеть двумя Task

Revision `21`: Session `agent-a` уже владеет Task `0043`; Task `0044` eligible,
свободна и может принадлежать другой Sprint или быть standalone.

Request:

```json
{"schema":"stride-sequencing/v1","operation":"apply","input":{"request_id":"f3b","expected_revision":21,"operations":[{"id":"claim-second","kind":"claim_task","task_id":"0044","claimant":"agent-a","claim_request_id":"work-0044-agent-a"}]}}
```

Exact expected response:

```json
{"schema":"stride-sequencing/v1","status":"rejected","error":{"code":"session_claim_conflict","message":"Session already owns another Task","retryable":false,"details":{"claimant":"agent-a","active_task_id":"0043","requested_task_id":"0044"}}}
```

Revision остаётся `21`. Claim `0043` не меняется, claim `0044` не создаётся. Тот же
запрет действует для `resume_claim(0044)` с валидным handoff. Два concurrent request
одной свободной Session на разные Task с `expected_revision=20`: ровно один success,
второй `revision_conflict`; новый current-revision request проигравшего —
`session_claim_conflict`. Это вторая половина SEQ-05, не только гонка за одну Task.

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
claim после exact handoff validation и не пересоздаёт Workspace baseline. Result release
возвращает конкретный `handoff_id`, который передаётся resume; successful resume consumes
его. Replaying старый successful claim/release возвращает historical receipt, но не
восстанавливает прежнее владение. Session, уже владеющая другой Task, получает
`session_claim_conflict` и не consumes handoff. Existing ready workspace ID, path, branch
и baseline сохраняются; следующий acquire возвращает тот же binding. Попытка использовать
тот же workspace ID для другой Task отклоняется `workspace_conflict`.

### F6 — cancelled predecessor не удовлетворяет dependency

`0042 -> 0043` kind completion. Explicit cancellation `0042` переводит terminal state в
cancelled. `eligibility(0043)` остаётся false с reason `predecessor_not_completed`; caller должен
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
- eligibility + обе cardinality constraints (Task→Session и Session→Task)
  проверяются и сохраняются атомарно owner-local;
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
| R02 / SEQ-03 | §4.2–4.3, §5.4–5.6, §7.2–7.3, F2/F2b/F3/F5/F6: eligibility всех predecessors, claim/release/resume and exact blockers |
| R03 / SEQ-05 | §4.3, §5.4–5.6, §7.3, F3/F3b/F5: one owner per Task AND one Task per Session; deterministic conflicts; release/resume |
| R04 / SEQ-07 | §4.4, §6, F4/F5: configured Workspace reservation/allocation/release/recovery with stable baseline and lifecycle |
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
3. concurrent claim cannot produce two owners per Task OR two Tasks per Session;
   release/resume cannot imply completion or reactivate historical receipts;
4. Workspace baseline/path/branch lifecycle is configured, replayable and crash-recoverable;
5. terminal/cancel facts are explicit and do not read Execution private state;
6. transfer preserves Sequencing-owned work without synchronization or foreign state capture;
7. stale revisions/request reuse/conflicts have stable codes and no partial owner writes;
8. identical logical packets work across different installation paths/configured roots;
9. each R01–R06 and both DoD clauses map to literal contract evidence.

Executable provider/consumer qualification belongs to implementation/UAT Tasks and cannot be
claimed complete by this design-only Task.
