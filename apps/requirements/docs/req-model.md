# Модель Requirements, auxiliary и перенос идентичности Task

Статус: целевое архитектурное решение `PS-DEV-002-T031` (`M04.T031`).
Этот документ уточняет модель, которую последующие Tasks должны реализовать.
Он не устанавливает новую БД, не запускает миграцию и не объявляет target API
реализованным. Текущий shell, данные 044A и предлагаемая модель описаны раздельно.

Канонический владелец публичной границы — [Requirements contract](contract-req.md).
Текущий запуск и конфигурация описаны в [Requirements shell](shell-req.md).
T031 владеет этим уточнением модели; транспортная совместимость не выводится из
совпадения названий старых и новых операций.

## 1. Решение и границы ответственности

Выбран один Requirements owner, а не общий граф всех приложений. В домене ровно
три уровня: SystemRequirement, ApplicationRequirement и TaskRequirement.
Task statement, snapshot, outcome и import provenance — связанные сущности, но
не дополнительные уровни требований. Между первыми двумя уровнями и между
ApplicationRequirement и TaskRequirement действуют отношения M:N.

Requirements владеет каноническим `task_id`, Requirements-срезом Task, ревизиями
постановки, обоими отношениями, неизменяемыми snapshots, фактами satisfaction,
своим replay и историей. Он не создаёт Sprint membership, claim, Workspace,
Execution attempt или результат этапа. ID соседнего приложения не заменяет
Requirements Task ID. Получив опубликованный snapshot, вызывающая сторона сама
передаёт его точную identity соответствующему публичному владельцу.

Внутренние зависимости направлены к domain. Тонкий CLI разбирает JSON/config;
application use case проверяет намерение, начинает owner-local transaction и
выбирает domain policy; storage adapter сохраняет результат. Правило готовности
не принадлежит SQL callback, сериализатору, migration helper или Presentation.
Публичный клиент соседнего приложения не импортирует приватные domain objects.

| Кандидат | Решение | Причина |
| --- | --- | --- |
| Старый split: Requirements хранит SR/AR, Task выдаёт ID и хранит постановку | Не сохранять как target | Нарушает единое владение Task identity; без изменения старого ready gate auxiliary требует вымышленного System parent |
| Один Requirements owner, три typed levels, явное назначение Application | Выбран | Сохраняет оба M:N, локальную транзакцию, историю и самостоятельное auxiliary-основание |
| Auxiliary как четвёртый уровень с последующей конверсией в System | Отклонён | Меняет число уровней и исторический смысл identity |
| Универсальный граф и общая БД Requirements/Task/Execution | Отклонён | Требует чужих таблиц и общей транзакции вместо независимых владельцев |

Повторно используются смысл текущих System/Application identifiers, validated
Task ID policy, exact request replay и frozen-context validation. Legacy Task
metadata не становится публичным API Requirements: нужен отдельный явный import
adapter. Evidence admission, execution history и full attempt policy остаются
отдельными, поскольку это другой владелец и другая семантика.

## 2. Что фактически находится в 044A

Источник — checked-in архив
`mailbox/inbox/AI-POISE-MAINTENANCE-044A-QUALIFICATION.tar.gz`.
Его размер — 29 251 391 байт, SHA256:

```text
b871c4892084a020ff07c6c436022dadb7f094c65329c37f67cb012fe3f0e1d0
```

`RELEASE.json` указывает исходный commit
`142c60a6ed56d24a44f5126bb88166ac3a7a6841`,
`state_kind: factory-empty-operational` и
`imported_development_history: false`. Это поставка с пустым operational state,
а не заполненный экспорт истории разработки. Выбранные файлы source и SQLite
сверены с `PAYLOAD-MANIFEST.json` по размеру и SHA256. БД открывались read-only,
`integrity_check` вернул `ok`, `foreign_key_check` — пустой список; после чтения
хэши не изменились. Эти проверки подтверждают вход, не успешную миграцию продукта.

### 2.1 Requirements schema 1

`installation/state/database/requirements.sqlite`: 32 768 байт,
`user_version=1`, SHA256:

```text
d4e7c808ab5d057c0e929657e4293466c4b8d404a2489fa6b2d1d61bfc2b1b89
```

| Реальная таблица | Поля | Наблюдаемые строки |
| --- | --- | --- |
| `requirements` | `id`, `level`, `status`, `text` | 0 |
| `requirement_links` | `system_id`, `application_id`; составной PK, оба FK к requirements | 0 |
| `requirement_requests` | `request_id`, `digest`, `response` | 0 |
| `requirements_meta` | `revision` | 1, значение revision — 0 |

В этой схеме нет третьего уровня с устойчивыми keys, канонических Task statements,
таблиц auxiliary или истории всех редакций требований. Старый store сохраняет
текущий graph целиком; его текущая строка не доказывает существование предыдущих
версий текста. Из номера общей revision нельзя восстановить отсутствующую историю.

### 2.2 Task schema 13 и snapshot metadata

`installation/state/state.sqlite`: 446 464 байта, `user_version=13`, SHA256:

```text
fb09c8d59146e44770b4c9145a8eb221b10164b6a4c3f366ffa64e61676f77f1
```

Все business tables пусты. `tasks` действительно содержит `id`, `status`,
`stage_index`, `iteration`, `claimed_by`, `version`, `current_submission_id`,
`metadata`; это не таблица Requirements statements. `submissions`, `task_events`,
`content_contracts` и связанные execution/sprint/runtime tables также не содержат
пользовательских строк. Таблицы `task_requirements_snapshots` здесь нет.

Проверенный source 044A даёт следующие пути, а не выдуманные записи:

- `modules/tasks/definition.py` сохраняет полный контракт в `metadata.contract`;
  Requirements-срез — его `id`, `goal`, ordered `requirements` и `definition_of_done`.
- `TaskRequirementsMetadataStore` использует
  `uow.tasks.publish_requirements_context` и `restart_context`. Frozen context
  размещается в `metadata.requirements_snapshot` и
  `metadata.requirements_agreement`; его нельзя брать из live registry задним числом.
- Старый snapshot имеет ровно `requirements`, `links`, `task_requirements`.
  Его Task requirements — ordered entries с `text`, `applications`, `status`, без
  стабильного target `key`. Agreement содержит `accepted` и `chains`.
- `TaskRequirementsSnapshotStore` с отдельной таблицей — изолированный adapter
  для другой storage shape. Он не является схемой versioned Task DB 044A.
  Нельзя создавать его таблицу внутри Task schema13 как способ миграции.

`installation/state/telemetry/events.sqlite` в этой поставке побайтово совпадает
со `state.sqlite`: тот же hash и пустая Task schema13. Имя `telemetry` не доказывает
другой набор событий. Два пути с одинаковыми данными не создают два набора Task IDs.

### 2.3 Текущая реализация и граница наблюдения

Текущий `apps/requirements/shell.py` использует legacy `RequirementsCommands` и
`RequirementsStore`, принимает envelope `{operation,input}` и предоставляет
`apply`, `query`, `import_bootstrap`. `TaskIdPolicy` проверяется в config, но это
не означает, что shell уже создаёт все target Task statements.

[Target contract](contract-req.md#2-транспорт-и-версия) описывает другой точный
wire envelope — `{schema,operation,input}` с `stride-requirements/v1`.
Ни схема SQLite 1, ни текущий shell не объявляются реализацией всей target-модели.
Ниже все новые таблицы, операции назначения и переносы — требования к реализации,
а не отчёт о выполненных migrations или тестах.

## 3. Идентичности и инварианты

### 3.1 Три уровня и два отношения

System/Application identity — точный непустой `id` и неизменяемый `level`.
Один requirement ID не используется одновременно для разных уровней.
Идентификатор TaskRequirement — пара `{task_id,key}`. `key` уникален внутри Task,
не зависит от порядка в новой постановке и после создания не переиспользуется для
другого смысла. Удаление key из текущего statement не удаляет его прошлые версии.

`system_application` — множество пар System ID → Application ID. Не допускаются
System→System, Application→Application, обратные типы или отсутствующие endpoints.
Повтор той же пары не создаёт второй edge. У одного Application могут быть два
System parents, у одного System — несколько Applications.

`application_task` принадлежит statement revision: пары Application ID →
`{task_id,key}`. Именно `applications` каждого TaskRequirement в statement является
каноническим входом; SQL relation table и reverse index выводятся из него в той же
транзакции. Отдельная mutation `link` не редактирует этот второй источник заново.
Множество Applications уникально, но порядок текстов и DoD в statement сохраняется.

Чтение `chain` возвращает все подходящие связи в обе стороны. Текущая проекция
фильтрует `obsolete`; исторический snapshot не теряет прежние edges из-за фильтра
live graph. Структурное наличие пути не является proof of satisfaction.

### 3.2 Канонический Task ID

Только Requirements reserve/create/import владеет Task ID. Для нового Task
используется явно сконфигурированный `TaskIdPolicy`: namespace bounds, width,
first, положительный step. Boolean не считается integer. Некорректные bounds,
width или progression отклоняются до записи. Исчерпание даёт
`id_namespace_exhausted`, без расходования ID в частично неуспешном batch.

Запрос `creation_request_id` связывается с digest полного creation intent и первым
выданным ID. Exact повтор возвращает тот же ID; изменённое намерение с тем же
request ID — `request_reuse_conflict`. Creation replay не возвращает чужой новый ID
и не создаёт вторую Task, даже если текущий statement первой Task уже изменён.
Outer mutation replay сохраняется отдельно и проверяется до повторного применения
первоначального expected revision.

Downstream ID — opaque exact string. `000042` и `42` не нормализуются друг в друга.
Импортированный ID сохраняется даже если не соответствует формату новых numeric
IDs; allocator проверяет занятость фактического namespace. Коллизия разных
канонических записей — явный отказ, не автоматическое переименование или alias.
Дополнительная исходная provenance не меняет самого Task ID.

### 3.3 Draft, готовность и публикация

Task statement содержит canonical ID, возрастающую `statement_revision`, goal,
Task requirements и DoD согласно
[публичной модели](contract-req.md#42-task-identity-и-statement).
Goal и каждый DoD item непусты; keys валидны и уникальны. Даже неполная Task имеет
явно обозначенный draft status/gaps, а не скрытый признак «готова».

Редактируемый draft может сохранять отсутствие требуемого основания как gap.
Ready/publication требует одновременно:

1. Есть как минимум один TaskRequirement, и у каждого публикуемого применимого
   требования есть непустой набор существующих Application IDs.
2. Нет неизвестных/obsolete оснований и неразрешённых current gaps.
3. Назначение каждого Application определено явно по правилам §4. Behavioral
   basis имеет реального non-obsolete System parent; auxiliary basis имеет
   explicit rationale и не требует вымышленного System parent.
4. Пустой `future` placeholder не подтверждает текущую обязательную работу и не
   становится evidence. Пока такой unresolved placeholder входит в публикуемый
   statement, публикация не готова.
5. Statement и выбранный graph читаются в одной owner-local consistent view.

Если применимые основания отсутствуют, результат — `unresolved_gaps`, а не пустой
готовый Task. Структурно неверный JSON/key/type отклоняется как contract error;
редактируемый gap не маскирует структурную ошибку.

`create_task` создаёт Requirements draft и ID, но не execution-ready соседние
записи. `revise_task` с новым request и верным expected statement revision явно
создаёт следующую полную revision; это также путь явного повторного подтверждения
той же формулировки на новой basis. Exact request replay новой revision не создаёт.
Ключи сохраняются; свежая review/edit не переписывает уже опубликованный snapshot.

`publish_task` публикует только выбранную statement revision. Пара
`{task_id,statement_revision}` имеет не более одного snapshot. Новый request для
уже опубликованной revision возвращает существующий snapshot с `changed:false`;
изменившийся live graph не подменяет его. Чтобы принять новую graph basis, сначала
нужна явная следующая statement revision, затем её отдельная publication.

## 4. Auxiliary — назначение основания, не четвёртый уровень

Application requirement может иметь purpose `behavioral` или `auxiliary`.
Назначение — отдельная owner-side характеристика Application identity; базовый
четырёхпольный requirement record из contract-req не дополняется скрытыми полями.
`auxiliary` требует непустой rationale: какая maintenance/bugfix работа нужна и
почему она не вводит новое System behavior.

Неопределённое назначение в импортированных данных называется
`legacy_unclassified`. Это provenance/classification state, не третий purpose и
не waiver ready gate. Для нового ещё не классифицированного Application виден
`purpose:null` и gap `purpose_unclassified`; его нельзя использовать для новой
готовой постановки до явного назначения. Текст, префикс ID и слово «bug» не являются
автоматическим классификатором.

Назначение устанавливается явным решением владельца один раз. Последующее
`behavioral`↔`auxiliary` изменение существующей identity отклоняется
`immutable_field`. Исправление rationale имеет новую owner-side revision без
изменения purpose и прошлого текста. Для ещё unclassified legacy identity можно
явно назначить purpose с зафиксированным происхождением решения; старый snapshot
при этом не дополняется задним числом.

Отсутствие System parent у auxiliary является разрешённым состоянием модели.
Не создаётся System requirement «починить код» только ради зелёной цепочки.
Если реальные System links уже имеются, они сохраняются; они не выводятся из
одного факта auxiliary completion. Ready Task всё равно должен ссылаться на
конкретный Application requirement и сформулированный TaskRequirement.

Новое System behavior требует новых настоящих System/Application требований и
новой постановки или новой явно принятой basis. Старый auxiliary ID не становится
System ID, не переименовывается и не получает новую историю. Связь между старой
maintenance работой и новой задачей может быть объяснена provenance, но не
заменяет typed requirements edges и не переносит satisfaction автоматически.

Пример целевых данных, не выгрузка 044A:

```json
{
  "requirement": {
    "id": "APP-MAINT-INDEX",
    "level": "application",
    "status": "current",
    "text": "Восстановить целостность существующего индекса без нового поведения."
  },
  "purpose_record": {
    "application_id": "APP-MAINT-INDEX",
    "purpose": "auxiliary",
    "rationale": "Исправление повреждённого индекса в существующей операции чтения."
  },
  "system_parents": []
}
```

Это composite illustration из трёх частей, **не готовый wire packet** и не новый
тип System requirement. Конкретные planned operations описаны в §8.

## 5. Owner-local storage и неизменяемая история

Ниже — минимальная целевая схема ответственности для реализации T032. Имена
таблиц нормативны для этой модели; SQL DDL, storage version и миграционные guards
должны быть реализованы и квалифицированы вместе. T031 не меняет `user_version`.
Schema1 нельзя молча открыть как новую schema или «исправить» на read query.

| Target table / ключ | Содержимое и инвариант |
| --- | --- |
| `requirements_meta` / singleton | Owner store revision и объявленная schema; новая установка отлична от неизвестной существующей БД |
| `requirements` / `id` | Стабильная SR/AR identity, immutable level, pointer на текущую версию |
| `requirement_revisions` / `(id,revision)` | Полный status/text, owner revision, origin; append-only, старый текст не удаляется |
| `application_purposes` / `application_id` | Неизменяемый выбранный purpose или отсутствие классификации; FK только к Application |
| `application_purpose_revisions` / `(application_id,revision)` | Rationale и origin решения; прошлые строки неизменяемы |
| `system_application_versions` / `(system_id,application_id,valid_from)` | История membership со start/end owner revision; текущие пары уникальны, удаление edge закрывает период |
| `task_identities` / `task_id` | Канонический ID, creation/import origin, current statement pointer; ID не переиспользуется |
| `task_creation_requests` / `creation_request_id` | Intent digest, canonical ID и original creation response; immutable replay binding |
| `task_statements` / `(task_id,statement_revision)` | Полный goal, ordered DoD, origin и digest постановки; append-only |
| `task_requirement_keys` / `(task_id,key)` | Резерв устойчивой identity; удалённые из live statement keys остаются reserved |
| `task_requirement_versions` / `(task_id,statement_revision,key)` | Position, status, полный text; FK к identity и statement |
| `application_task_links` / `(task_id,statement_revision,key,application_id)` | Нормализованная проекция `applications`; не независимо редактируемый источник |
| `task_snapshots` / `(task_id,snapshot_revision)` | Полный frozen payload + digest и exact statement/registry revisions; unique `(task_id,statement_revision)` |
| `requirement_outcomes` / `outcome_id` | Exact task/snapshot, kind, keys, digest; не копия execution attempts |
| `requirement_requests` / `(protocol_namespace,request_id)` | Exact canonical request digest и response; legacy replay изолирован от target replay |
| `legacy_imports` / `source_digest` | Fingerprints, format, manifest, diagnostics, disposition и immutable completion receipt |
| `legacy_records` / `(source_digest,source_locator)` | Исходные bytes/JSON и hash, mapping к target либо explicit unresolved status; не fabricated target history |
| `legacy_identity_map` / `(source_digest,source_locator,source_identity)` | Exact source→target correspondence с provenance; никакого guessing по тексту |

Все FK и уникальности принадлежат Requirements store. FK к таблицам Execution,
Sequencing, Task process или telemetry запрещён. Уровни endpoints проверяет domain
и storage constraint; строки typed relations нельзя импортировать как произвольные
пары. Reverse indexes производны от этих же таблиц и восстанавливаются без изменения
истории; отдельного mutable `task_to_applications` ledger нет.

Публичная история использует сохранённые полные revisions/snapshots. Current
проекции допускают indexes, но cache miss не превращается в исчезновение старого
текста. Физическое удаление requirement разрешено только при отсутствии live
relations и всех исторических ссылок; иначе `referential_conflict`. Обычное снятие
с применения — новая revision со `status:obsolete`, не удаление доказательств.

Statement, snapshot и source origin имеют разные номера. Task runtime `version`,
`stage_index`, submission sequence и Requirements `registry_revision` не являются
взаимозаменяемыми. Snapshot хранит собственную принятую basis; последующие изменения
текста, edge или purpose не переписывают этот payload.

## 6. Перенос 044A: карта источников и отказов

### 6.1 Что переносится и что остаётся у прежнего владельца

| Source 044A | Target owner / disposition | Что не выводится автоматически |
| --- | --- | --- |
| `requirements` schema1 | Requirements identity и первая наблюдаемая импортированная revision полного record | Редакции до наблюдаемой строки и auxiliary-purpose неизвестны |
| `requirement_links` schema1 | Requirements typed System↔Application membership + source origin | Вторые родители не теряются; отсутствующие связи не восстанавливаются по тексту |
| `requirements_meta.revision` | `legacy_imports` source metadata; новая owner revision остаётся самостоятельной | Старое число не выдаётся за историю всех target revisions |
| `requirement_requests` schema1 | Immutable legacy namespace replay в Requirements import history | Старый digest/response не используется для нового wire schema/request ID |
| `tasks.id` и `metadata.contract.id` | Requirements canonical Task identity после exact equality check | Никакой нормализации, перенумерации или создания другой Task |
| `metadata.contract.goal`, `requirements`, `definition_of_done` | Requirements immutable source statement и доказуемая target projection | Process/stage fields не становятся полями Requirements statement |
| `metadata.requirements_snapshot` + `metadata.requirements_agreement` | Requirements immutable legacy snapshot/agreement bytes и source-bound mapping | Отсутствующие registry revisions, keys и agreement не придумываются |
| Исторические Requirements-срезы `content_contracts.data`, metadata в других явно распознанных source records | Requirements сохраняет только подтверждённый срез и original locator/hash | Не всякая JSON-строка объявляется snapshot; формат проверяется явно |
| `tasks.status/stage_index/iteration/claimed_by/version/current_submission_id`, `task_execution`, `task_workflows`, workflow/content/trace layers | Execution/process owner; оригинальные FK/order/identity сохраняются в его собственном переносе | Task runtime outcome не становится Requirements satisfaction |
| `submissions`, `task_results`, `task_events`, `task_proofs`, `task_proof_layers`, `task_methods`, `evidence`, `action_events`, `action_runs`, `work_packets`, `handoffs` | Execution/evidence/history owner, не импортируются как operational Requirements rows | Из accepted stage не выводится positive business outcome |
| `sprints`, `sprint_members`, `sprint_dependencies`, `sprint_layers`, `sprint_requests`, `session_sprints` | Sequencing owner | Membership/dependency не создают requirement basis |
| `sessions`, `runtime_bindings`, `runtime_cursors` | Runtime/session owner | Archived caller не активируется при импорте |
| `artifacts`, `task_artifacts`, `sprint_artifacts`, `transfer_imports`, `transfer_locations`, `transfer_requests` | Соответствующий evidence/transfer owner; Requirements получает только свой явно выделенный slice | Путь или одинаковый digest не доказывает domain ownership |
| `accounting_*`, `interaction_*`, `journal` | Accounting/interaction/audit owner, вне данной миграции | Audit occurrence не доказывает Task completion |
| `telemetry/events.sqlite` этой поставки | Идентифицируется по фактической schema/hash; duplicate empty Task source | Не второй набор requirements, snapshots или событий |

Если встречается неизвестная таблица или неизвестная source shape, план содержит
явный unresolved item; применение не выбрасывает её молча. T031 определяет
Requirements-срез и границы остальных данных, но не мигрирует соседние БД.

### 6.2 Ordered legacy Task requirements

Сначала сравниваются `tasks.id` и `metadata.contract.id`, затем ordered requirement
texts с `snapshot.task_requirements[].text`, и полный agreement с подтверждённой
цепочкой snapshot. Unknown fields/format не обходятся перехватом исключения.
Отсутствие frozen context нельзя компенсировать чтением текущего registry.

Source identity legacy entry —
`{source_digest, task_id, source_record_locator, index}`. Это адрес наблюдаемого
entry, не устойчивая старая domain identity. Для доказуемой target projection
импорт резервирует отдельный deterministic key, сохраняет полный mapping и исходный
index. Нельзя объявлять один key тем же требованием в двух старых ревизиях только
по совпадению текста или позиции. Явное последующее reconciliation может связать
происхождение новой постановки, но не переименовывает уже сохранённые snapshots.

Если целевая projection не может быть однозначно доказана, исходные bytes остаются
в `legacy_records` со статусом unresolved/historical-only. Такая запись доступна
в истории импорта и не становится готовой новой Task или обычным target snapshot.
Нельзя присваивать ей выдуманный `registry_revision` только ради v1 serializer.
Обычный `snapshot` query возвращает лишь настоящие target snapshots; исторический
legacy read имеет отдельный явно версионированный формат из §8.3.

Точный ID уже существующей Task с отличающимся statement/snapshot — conflict всей
публикации. Равный payload можно признать exact duplicate только после сравнения
domain identity и полного canonical content; hash без проверки форматной identity
не разрешает коллизию. Независимые source origins сохраняются как provenance, а
не объединяются в одну выдуманную историческую запись.

### 6.3 Явный протокол миграции

Migration adapter принимает только переданные read-only export copies и manifest,
а не подключается к live приватным БД соседей. Use case принадлежит Requirements.
Пути inputs и target заданы явно; source файлы не могут совпадать с target DB/lock.
Открытие обычного query не вызывает migrate/repair.

1. Зафиксировать source bytes/hashes, schema versions и exact member inventory.
   Проверить SQLite integrity/FK; не продолжать на другом файле с тем же названием.
2. Построить dry-run map всех source records, IDs, snapshot origins и dispositions.
   Отдельно отразить пустые business tables и unresolved history. Dry-run не
   изменяет target, не резервирует ID и не потребляет request IDs.
3. Сверить при apply тот же source digest/manifest, expected target revision и
   весь план. Полный набор коллизий, missing endpoints, невалидных references,
   несовместимых format/purpose и неоднозначных mappings блокирует публикацию.
4. В одной Requirements transaction записать только свой validated slice,
   immutable source provenance, stable mapping и import/replay receipt.
   Любая ошибка до commit откатывает весь batch и ID reservation.
5. Прочитать receipt и опубликованный slice обратно, сопоставить exact counts,
   identities, edges, текст, source hashes и отсутствие чужих writes. Только эта
   проверка позволяет заявить completed import, но не Task satisfaction.

Source classification historical-only сохраняет исходные данные, но не устраняет
ready gaps. Она должна быть явно включена в принятый import plan; нельзя спрятать
потерю domain projection за общим `success`. При несовместимой identity — rejection,
а не «best effort» частичный перенос.

Owner crash до commit не публикует половину graph. Crash после commit и до ответа
обрабатывается exact replay сохранённого receipt. `store_busy` ограничен config
seconds/poll; неограниченного retry нет. После publication rollback не выполняется
удалением history: восстановление точного backup или следующая явная корректирующая
операция обязаны сохранять origin и не переписывать принятые old snapshots.

Для имеющейся пустой 044A поставки реально квалифицируются schema/inventory и
пустой перенос. Положительный тест заполненной legacy migration потребует
самостоятельных literal fixtures и/или отдельного реального input с точными
fingerprints. Такие fixtures не выдаются за исторические строки 044A.

## 7. Применимость, поиск и satisfaction

`current`, `future`, `obsolete` сохраняются как статус каждой revision, а не как
утверждение о выполнении. `future` с пустым текстом — projected placeholder;
`obsolete` исключается из текущей applicability, но не из immutable history.
Current query и snapshot query явно имеют разные области наблюдения.

Registry CRUD/history, `chain`, `coverage`, `gaps`, `get_task`, `task_history`,
`snapshot`, `satisfaction`, `export_slice` принадлежат Requirements. Один query
batch читает одну owner revision и не делает repair. Поиск фильтрует значения
согласно contract-req; наличие результата поиска не создаёт link или outcome.
Reverse trace отвечает из тех же сохранённых edges, что и forward traversal.

Для snapshot positive outcome может удовлетворить только перечисленные exact
TaskRequirement keys. Negative/cancelled не добавляют удовлетворения. Application
и System требуют непустого множества applicable детей и удовлетворения всех таких
детей по frozen relations. Отсутствие System parent у auxiliary не создаёт
несуществующий System satisfaction. Структурная полнота цепочки, ready и satisfied
не взаимозаменяемы.

Full restart/attempt replacement/recalculation table принадлежит отдельному design
T037. До явно реализованной политики запрос, которому она нужна, получает
`unsupported_policy`. Этот документ фиксирует, что outcome всегда связан с exact
Task/snapshot/key; он не выводит новую попытку из Task runtime version и не
считает мигрированный execution result автоматически допущенным outcome.

### 7.1 Проверяемый пример M:N

Это вымышленные данные для анализа модели, а не результат запуска:

```json
{
  "system_application": [
    {"system": "SYS-A", "application": "APP-X"},
    {"system": "SYS-B", "application": "APP-X"},
    {"system": "SYS-A", "application": "APP-Y"}
  ],
  "task_id": "000042",
  "statement_revision": 1,
  "task_requirements": [
    {"key": "R1", "status": "current", "text": "Первое проверяемое обязательство.", "applications": ["APP-X", "APP-Y"]},
    {"key": "R2", "status": "current", "text": "Второе проверяемое обязательство.", "applications": ["APP-X"]}
  ]
}
```

Forward: SYS-A → APP-X/APP-Y → R1/R2; SYS-B → APP-X → R1/R2.
Backward: R1 → APP-X/APP-Y → SYS-A/SYS-B; R2 → APP-X → SYS-A/SYS-B.
Уникальные identities устраняют дубли, но не второго родителя. Если positive outcome
подтверждает только R1, APP-Y satisfied, APP-X не satisfied; значит ни SYS-A, ни
SYS-B ещё не satisfied. После positive R2 оба Application satisfied, затем оба
System satisfied. Ни один вывод не вычисляется по количеству совпавших текстов.

Если live registry затем удаляет SYS-B→APP-X, старый snapshot продолжает показывать
этот путь. Новая публикация другой statement revision может иметь другой graph;
она не наследует satisfaction автоматически от старой.

## 8. Контракты, конфигурация и ошибки реализации

### 8.1 Что остаётся неизменным

Точный target `stride-requirements/v1`, `apply/query`, request replay, базовый
requirement record, task statement/snapshot/outcome формы и коды из
[public contract](contract-req.md#7-stable-rejection-envelope) сохраняются.
`application_task` изменяется только через `revise_task`. Публичный target `import_slice`
принимает Requirements slice, а не пути приватных БД других приложений.

Текущий executable `apps/requirements/bin/requirements` продолжает подчиняться
[shell contract](shell-req.md); новые операции ниже пока не являются исполнимыми.
Нельзя отправить target envelope в legacy shell и считать его отказ дефектом этой
документальной Task. Изменение transport adapter и fixture qualification выполняется
в соответствующей implementation Task, без невидимого fallback между schemas.

Файл `apps/requirements/config/requirements.json` не меняется. Наблюдаемые defaults:
`stride-requirements-config/v1`; state относительно config — `../state`, database —
`requirements.sqlite`, lock — `requirements.lock`; max input 8192 bytes,
max items 64; seconds 1.0, poll_seconds 0.01; task IDs min1/max999999, width6,
first1/step1. Это текущие значения файла, а не обязательные production paths.
Target-модель использует явно resolved config и не читает profile соседа.
Новые migration limits/inputs должны быть явными и версионированными; T031 не
придумывает недействующий CLI flag или скрытую переменную окружения.

### 8.2 Уточнение target auxiliary операций

T031 предлагает два явных discriminated operation/query variants внутри
уже описанного target v1. Они не меняют набор полей существующего
`put_requirement` или `get_requirement`. До реализации variant отклоняется
`unknown_operation`; зависимая ещё не реализованная policy — `unsupported_policy`.
Реализация T034 обязана co-deliver exact shapes/fixtures и указать capability,
а не silently принять дополнительные поля в старом shell.

Proposed apply item — первый выбор purpose или новая revision rationale при том же
purpose. `application_id` должен существовать и иметь level application;
`rationale` строка, непустая для auxiliary. Историческая смена purpose запрещена:

```json
{
  "id": "purpose-1",
  "kind": "set_application_purpose",
  "application_id": "APP-MAINT-INDEX",
  "purpose": "auxiliary",
  "rationale": "Ремонт существующего индекса без нового System behavior."
}
```

Этот item передаётся внутри target `apply.input.operations` с outer request/revision,
а не как отдельный top-level packet. Result коррелируется по `id` и возвращает
`kind`, `application_id`, `purpose_revision`, `changed`; эти поля относятся только
к новому variant. Exact request replay следует обычной owner policy.

Proposed read item:

```json
{"id": "purpose-read", "kind": "application_purpose", "application_id": "APP-MAINT-INDEX"}
```

`value` содержит `application_id`, `purpose`, `purpose_revision`, `rationale`,
`origin`. Для ещё не выбранного назначения purpose/revision равны `null`, rationale
пуст, origin различает new-unclassified и imported legacy; query не классифицирует
сам. Неизвестный ID даёт `not_found`, System ID — `invalid_packet`. Для выбранного
purpose revision — positive integer. `origin` содержит type и source locator для
imported records; это provenance, не чужой active caller.

### 8.3 Legacy history не маскируется под target snapshot

Для T032 import adapter требуется отдельный versioned input/output
`stride-requirements-legacy-import/v1`, явно отделённый от `import_slice`.
Его machine-readable plan содержит source fingerprints/schema inventory,
ordered source locators, target identities или unresolved dispositions,
expected target revision и plan digest. Точный adapter argv не определён этим
дизайном и не объявляется установленной командой; T032 должен выбрать его внутри
своего frozen scope, реализовать и документировать вместе с тестами.

Legacy history возвращает exact original bytes/hash и typed mapping, а не объект
с выдуманным target `snapshot_revision`. Минимальный target read variant для этой
истории — `legacy_history` с `source_digest` и `task_id`; result format
`stride-requirements-legacy-history/v1` содержит ordered `records` с
`source_locator`, `source_sha256`, `source_format`, `original`, `mapping`,
`disposition`. Это target specification, не существующий read endpoint.

`mapping` может отсутствовать (`null`) при historical-only записи, но причина в
`disposition` обязательна. При подготовке готовой новой Task такой источник не
обходит обычный ready gate. Exact original JSON семантически сохраняется; если
сохраняются исходные bytes, hash относится к ним, а не к произвольной повторной
сериализации. Неизвестные поля/версии input не игнорируются.

### 8.4 Матрица отказов и побочных эффектов

| Ситуация | Результат target | Побочные эффекты |
| --- | --- | --- |
| Неверная schema, JSON, лишние поля или неверный тип | `unsupported_schema` / `invalid_json` / `invalid_packet` | Нет |
| Другой payload у outer/creation request | `request_reuse_conflict` | Нет; прежний ID и response сохраняются |
| Устаревшая store/statement revision | `revision_conflict` | Нет |
| Нет ID, endpoint или source identity | `not_found` либо explicit unresolved import item | Публикации нет |
| Изменение level/canonical ID/purpose/history | `immutable_field` | Нет |
| Неоднозначный или конфликтующий imported ID | `already_exists` / `referential_conflict` с exact source locator | Нет частичного import или rename |
| Missing Application, behavioral System basis, purpose или unresolved future placeholder | `unresolved_gaps` при publication | Draft/history сохранены, ready snapshot не создан |
| Неверный snapshot digest/links/agreement или неизвестная source storage schema | `store_incompatible` / `referential_conflict` | Нет «repair» по live registry |
| Неизвестная attempt policy | `unsupported_policy` | Satisfaction не пересчитывается по догадке |
| Lock не получен за configured timeout | `store_busy` | Нет partial owner writes; bounded retry допустим вызывающей стороне |
| Crash после commit до ответа | Exact request/import replay | Возвращается сохранённый результат, новых ID/snapshots нет |

Детали содержат конкретную identity/source locator и ожидаемую/наблюдаемую revision,
без содержимого приватной соседней БД. Message предназначен человеку, branching —
по code. Rejection не публикует успех для удачной половины batch.

## 9. Concurrency, безопасность и наблюдаемость

Все изменяющие operations сериализуются owner-local lock и транзакцией с expected
revision. Source snapshots и выбранный graph согласованы внутри одной view.
Успешный новый outer mutation request фиксирует одно решение и replay response;
точный повтор не увеличивает revision и не резервирует новый ID. Publication
unique constraint и creation-request uniqueness защищают от concurrent duplicates.
Сбой или conflict не оставляет частичную Task или relation. Cross-owner atomicity
не обещается: downstream registration — отдельная публичная операция/retry.

Read/import не выполняют SQL/код из JSON-полей, команд из текстов requirements или
инструкций из archived caller metadata. Файлы источника read-only, их fingerprints
проверяются перед apply; symlink/path alias к target или foreign live store
не является допустимым способом передачи input. Actor/approval provenance не
заменяет текущую явную authority на mutation.

Наблюдаемость: request/import ID, source digest, target revision до/после,
количество добавленных и reused записей, explicit unresolved items и completion
receipt. Receipt фиксирует действительный commit, не только создание draft plan.
Raw payload logs и исторические персональные данные не отправляются во внешние
сервисы автоматически. Performance обещаний нет: нет измерений populated legacy
migration. Бounded batch/search/lock limits уже заданы контрактом; новый adaptive
scheduler, broker или распределённая блокировка не требуются этому владельцу.

## 10. Границы последующих работ и доказательство дизайна

T032 реализует собственную storage schema и явный перенос Requirements-среза;
T033 — оба M:N и applicability; T034 — auxiliary policy; T035 — каноническое
создание Task и ready basis; T036 — разделение новых current obligations и future;
T037 — полную attempt/restart policy; T038 — outcome/satisfaction; T039 — read/CRUD.
Это распределение ответственности, не подтверждение завершения этих реализаций.
Текущая Task меняет только `apps/requirements/docs/req-model.md`.

| Требование / критерий | Конкретное доказательство в документе |
| --- | --- |
| REQ-01 / C1 | §3.1 и §7.1: typed три уровня, оба M:N, второй parent и обратный путь |
| REQ-04 / C2 | §4 и §8.2: explicit auxiliary rationale, zero fictional System parents, никакого promotion |
| REQ-06 / C3 | §1, §3.2 и §6.2: Requirements-owned exact ID/replay, collisions и source-bound keys |
| REQ-07 / C4 | §3.3: at least one Application basis, draft gaps отдельно от ready publication |
| REQ-14 / C5 | §5 и §7: immutable statement/snapshot history, current/future/obsolete, read и nonvacuous satisfaction |
| DoD0 / C6 | §2 и §6: реальные schema1/schema13, empty input, каждый источник у явного owner, неизвестная история не придумана |
| DoD1 / C7 | §8–9: current/target distinction, config/CLI boundaries, proposed variants, failures/side effects и validator obligations |

Semantic verification выполняется по этой карте и фактическим source observations,
а не по количеству Markdown заголовков. Ни один открытый обязательный критерий
нельзя закрыть одной удачной проверкой ссылок. Существующий documentation owner
проверяет ссылки/anchors только выбранного изменённого файла; maintained smoke
проверяет прежний harness. Они не доказывают миграцию или новые endpoint behaviors.
Исполнимые новые контракты потребуют постоянных focused tests в implementation Tasks,
включая M:N second-parent, auxiliary-without-System, exact ID/replay, failed ready,
immutable old snapshot, collision rejection и populated source provenance.

Никакое принятие этого текста не даёт права переписать исторические snapshots,
активировать archived callers, выполнить незаявленную миграцию или принять Sprint
Task без её публичных verification/inspection/acceptance операций.
