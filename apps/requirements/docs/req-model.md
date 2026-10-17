# Модель Requirements, auxiliary и перенос идентичности Task

Обновлено: **2026-10-18**.

Статус: целевое архитектурное решение `PS-DEV-002-T031` (`M04.T031`).
Этот документ уточняет модель, которую последующие Tasks должны реализовать.
Архитектурный проект отделён от фактически реализованного самостоятельного
[хранилища формата 1](storage-req.md) и [CLI](shell-req.md). Новое хранилище
создаёт основную модель и предметный каталог; [текущий graph-профиль](graph-req.md)
реализует создание Task и ID, чтение, применимость и изменение постановки.
[Профиль исходов](outcome-operations.md) реализует публикацию, приём фактов,
явный выбор подтверждения и текущий каскад. Purpose-aware готовность остаётся
отдельной проектной возможностью. Данные 044A ниже — исторический источник, а не
инструкция развёртывания новой установки.

В действующем профиле отдельная будущая запись сохраняется независимо от состава
Task, а явное включение создаёт следующую постановку и нижнюю трассировку.
[Текущая процедура](graph-req.md#текущее-обязательство-и-будущая-потребность)
показывает сохранение старого Task-текста, актуальные Application-значения и
структурную оценку полного будущего основания либо пустого placeholder.
Исторический снимок и готовность структуры сохраняют собственный смысл;
публикация и учёт выполнения относятся к указанным выше отдельным возможностям.

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

Текущий `apps/requirements/shell.py` компонует общий `RequirementsCommands` с
собственной фабрикой `apps.requirements.storage.RequirementsStore`. Публичный
`standalone(...,registry_type=RequirementsGraph)` выбирает backend самостоятельного
формата и трёхуровневую модель. Новая база содержит 21 таблицу: 16 основной модели,
включая явные решения о подтверждении, и пять предметного каталога. Текущий
[формат и порядок подготовки](storage-req.md) описаны владельцем хранилища.

Shell принимает `{operation,input}` и предоставляет `create_task`, `apply`, `query`,
`import_bootstrap`. [Graph-профиль](graph-req.md#создание-task-и-канонический-id)
описывает создание по цели, канонический ID, редактирование, `task_creation` и
`task_basis`. `TaskIdPolicy` выбирает автоматический ID; текущая конфигурация
проверяется и при replay. [Публикация, исходы и текущая оценка](outcome-operations.md)
доступны в этом профиле; purpose-aware готовность развивается отдельно.
[Target contract](contract-req.md#2-транспорт-и-версия) описывает будущий расширенный
envelope `{schema,operation,input}` с `stride-requirements/v1` и остальные операции.

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

В реализованном профиле отдельный `create_task` принимает `request_id` и `goal`,
сохраняет полный исходный input и возвращает канонический ID. Явный `task_id`
сохраняется как строка, автоматический выдаётся по настроенному `TaskIdPolicy`.
`task_creation` читает первоначальное намерение и ответ даже после редактирования.
Точные текущие поля и ответы приведены в [руководстве](graph-req.md#создание-task-и-канонический-id).
Остальная часть этого раздела описывает целевой reserve/create/import и его
расширенную модель, включая имя `creation_request_id` целевого протокола.


Только Requirements reserve/create/import владеет Task ID. Для нового Task
используется явно сконфигурированный `TaskIdPolicy`: namespace bounds, width,
first, положительный step. Boolean не считается integer. Некорректные bounds,
width или progression отклоняются до записи. Исчерпание даёт
`id_namespace_exhausted`, без расходования ID в частично неуспешном batch.

Запрос `creation_request_id` связывается с полным creation intent и первым
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

Текущее создание по одной цели сохраняет черновик с пустыми `task_requirements`/`definition_of_done`.
`task_basis` оценивает именно текущие сохранённые требования через действующий
предикат реальных Application/System цепочек. Это структурная полнота оснований;
описанная далее публикация и purpose-aware policy остаются целевой моделью.
Выбор `work_choice` сохраняется в первоначальном input как декларация оператора
или агента; фактическое членство/исполнение ведут их отдельные владельцы.


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
5. Statement, выбранный graph и все выбранные purpose revisions читаются в одной
   owner-local consistent view. Полная basis из §5.1 фиксируется той же publication;
   snapshot без нужного назначения/rationale не публикуется.

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

### Текущее поведение и целевая классификация

Текущий перенос исходного Poise поддерживает осмысленное Application-основание
обслуживания, связанное с настоящим действующим System-требованием и требованиями
известной Task. Оператор задаёт смысл обслуживания в тексте основания. При
выявлении нового системного смысла он явно создаёт отличные System/Application
требования и связи, сохраняя прежнее основание, его текст и identity. Правильные
пакеты и исходные условия приведены в [практической инструкции](graph-req.md#обслуживание-и-новый-системный-смысл).

Это граница переноса `T034-EXISTING-SOURCE-SCOPE-DECISION-1`: исходный владелец
хранит четырёхпольные требования и явные связи. Специальные purpose/rationale,
неизменяемое структурированное назначение и публикация auxiliary без System-родителя
остаются предложением целевой модели. Приведённые сценарии подтверждают текущий
перенос; полное структурированное назначение из REQ-04 остаётся отдельным пробелом
возможностей исходного продукта. Далее раздел описывает именно эту целевую модель.

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

Таблица описывает предметные роли основной самостоятельной модели. Действующие
поля и физическая схема определены в [описании хранилища](storage-req.md) и
[schema.sql](../schema.sql). Сохраняются полные намерения, origins и payload;
для развёртывания используется новая установка собственного формата.

| Таблица / ключ | Содержимое и инвариант |
| --- | --- |
| `requirements_meta` / singleton | Общая ревизия и объявленная schema |
| `requirements` / `id` | Стабильная SR/AR identity, неизменяемый level, текущая версия |
| `requirement_revisions` / `(id,revision)` | Полный status/text, owner revision и origin; сохранённые редакции |
| `application_purposes` / `application_id` | Выбранное назначение Application и текущая редакция |
| `application_purpose_revisions` / `(application_id,revision)` | Сохранённые rationale/origin решения; операции назначения развиваются отдельно |
| `system_application_versions` / `(system_id,application_id,valid_from)` | Периоды membership; снятие связи закрывает период |
| `task_identities` / `task_id` | Канонический ID, origin и текущий указатель постановки |
| `task_creation_requests` / `creation_request_id` | Полное намерение, выданный ID и исходный ответ; producer развивает создание отдельно |
| `task_statements` / `(task_id,statement_revision)` | Полные goal, ordered DoD и origin |
| `task_requirement_keys` / `(task_id,key)` | Устойчивая identity; снятые из текущей постановки keys остаются сохранёнными |
| `task_requirement_versions` / `(task_id,statement_revision,key)` | Position, status и полный text |
| `application_task_links` / `(task_id,statement_revision,key,application_id)` | Основания требований выбранной постановки |
| `task_snapshots` / `(task_id,snapshot_revision)` | Полный frozen payload и точные statement/registry revisions; publication развивает запись отдельно |
| `requirement_outcomes` / `outcome_id` | Exact Task/snapshot, kind и keys; поведение outcomes развивается отдельно |
| `requirement_requests` / `(protocol_namespace,request_id)` | Полные канонические intent и response |

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
Собственная история неиспользуемой записи завершается вместе с её удалением;
сохранённые внешние основания остаются обязательными ссылками. Явное удаление и
следующее создание того же ключа образуют новую запись с локальной редакцией 1,
новым происхождением и явно выбранным уровнем, включая совпадающие конечные поля.
Точные операции реализованного CLI и его описанный отказ определяет
[руководство графа](graph-req.md#удаление-неиспользуемого-требования).

Statement, snapshot и source origin имеют разные номера. Task runtime `version`,
`stage_index`, submission sequence и Requirements `registry_revision` не являются
взаимозаменяемыми. Snapshot хранит собственную принятую basis; последующие изменения
текста, edge или purpose не переписывают этот payload.

### 5.1 Frozen Application basis как часть snapshot identity

Реализованное чтение возвращает сохранённый plain JSON object снимка целиком.
В текущей основной модели он содержит `task_id`, `snapshot_revision`,
`statement_revision`, `registry_revision`, `statement`, `requirements`, `links`.
Чтение использует значения выбранного снимка, сохраняя их при изменении live graph.

Будущая purpose-aware публикация должна сохранить выбранные предметные значения
назначения Application, редакции, rationale и origin вместе с соответствующим
снимком. `behavioral` опирается на его реальное System-основание;
`auxiliary` имеет явное содержательное rationale. Источником такого основания
служит выбранная редакция, а его чтение опирается на сохранённые значения.
Точную форму поля и protocol/schema определяет владелец publication на этапе её
реализации. Этот раздел сохраняет предметное решение, а не объявляет новый wrapper
или реализованную purpose-aware публикацию.

Для каждого Application выбранного снимка сохраняется одно полное основание с его
точным ID, выбранными purpose/revision, rationale и origin. Набор соответствует
Application records и отношениям снимка; оба перехода M:N описывают ту же постановку.
Публикация использует классифицированные основания. Purpose сохраняется за identity
Application, изменение rationale получает следующую редакцию. Для behavioral
сохраняется действительное System-основание, для auxiliary — его собственное
содержательное rationale. Эти значения остаются частью выбранного снимка.

Происхождение описывает принятое решение. Например, текущая явная запись требования
сохраняет `{"type":"explicit","request_id":"purpose-request-1"}`. Полное сохранённое
значение origin возвращается при чтении его редакции; оно не является токеном authority.

Полные intent/payload хранятся как обычные значения. Контрольная сумма, подпись,
проверка подмены и обязательный digest-wrapper не входят в этот контракт.
Для exact replay сравниваются полные намерения с сохранением типов и порядка
списков. Конфликт содержимого одной identity/revision относится к конкретной
операции записи. Чтение выбранного значения использует его данные; восстановление
нечитаемых значений выполняет отдельный инструмент оператора.

Новая постановка и её отдельная publication могут сохранить новую basis. Старый
снимок продолжает описывать принятую редакцию. История читается из собственного
payload и сохраняет значения после последующих изменений live projection.

## 6. Перенос 044A: карта источников и отказов

Этот раздел относился к прежнему проекту переноса данных. Развёртывание текущего
Requirements использует [новое самостоятельное хранилище](shell-req.md#создание-и-повторное-открытие)
и его собственную конфигурацию. Исходные наблюдения 044A в §2 — исторические
материалы анализа, а не инструкция запуска. Действующая схема и операции
перечислены в [storage-профиле](storage-req.md) и [graph-профиле](graph-req.md).

## 7. Применимость, поиск и satisfaction

`current`, `future`, `obsolete` сохраняются как статус каждой revision, а не как
утверждение о выполнении. `future` с пустым текстом — projected placeholder;
`obsolete` исключается из текущей applicability, но не из immutable history.
Current query и snapshot query явно имеют разные области наблюдения.

Реализованные `chain`, `task_chain`, `application_tasks`, `coverage`, `gaps`,
`get_task`, `task_history`, `history_requirement`, `snapshot` и `search_requirements`
описаны в [graph-профиле](graph-req.md). `current_satisfaction` и `outcome_state`
описаны в [профиле исходов](outcome-operations.md). Purpose-aware готовность и
`export_slice` относятся к будущему расширению владельца Requirements. Один query
batch читает одну owner revision и не делает repair. Поиск фильтрует значения
согласно contract-req; наличие результата поиска не создаёт link или outcome.
Условия `applicable/satisfied` используют ту же полную текущую оценку, что и
`current_satisfaction`, и одновременно сочетаются с уровнем, статусом и текстом.
Структурные `coverage/gaps` и выбранный сохранённый snapshot отвечают на собственные
вопросы о распределении ответственности и прежнем основании.
Reverse trace отвечает из тех же сохранённых edges, что и forward traversal.

Для snapshot positive outcome может удовлетворить только перечисленные exact
TaskRequirement keys. Negative/cancelled не добавляют удовлетворения. Application
и System требуют непустого множества applicable детей и удовлетворения всех таких
детей в соответствующем основании. Для глобального решения учитываются все
текущие связанные Task; frozen отношения отдельного snapshot сохраняют локальный
исторический смысл. Отсутствие System parent у auxiliary не создаёт
несуществующий System satisfaction. Структурная полнота цепочки, ready и satisfied
не взаимозаменяемы.

Согласованная предметная [таблица пересчёта](outcome-recalculation.md) описывает
restart, повтор факта, замену применяемого подтверждения, пустые множества и всех
прежних/новых родителей изменённых связей. Глобальная оценка охватывает текущие
обязательства всех Task с их отдельными публикациями либо ожиданием публикации;
локальный Task snapshot не доказывает глобальное выполнение AR/SR. Одинаковые
local keys разных Task сохраняют полную identity. При смене основания одной Task
обязательства и подтверждения остальных остаются в оценке.

Предметная политика реализована в текущем профиле исходов. `unsupported_policy`
относится к расширенному проектному public contract; действующий shell использует собственный
описанный формат отказа. Outcome привязан к exact Task/snapshot/key; runtime version
не определяет attempt, а исторический факт и текущий выбор подтверждения различны.

### 7.1 Проверяемый пример M:N

Это изолированные вымышленные данные для анализа модели, а не результат запуска.
У показанных Application нет других текущих Task обязательств; глобальный случай
нескольких Task приведён в [канонических примерах](outcome-recalculation.md#независимые-проверяемые-примеры).

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

### 7.2 Старое и новое rationale: перенос в пустого владельца

Это предметный сценарий будущей purpose-aware публикации. Снимок 1 сохраняет
выбранное auxiliary-основание Application с редакцией rationale 1. Исправление
rationale создаёт редакцию 2 того же назначения. Новая постановка и её отдельная
публикация могут создать снимок 2 с новым основанием; снимок 1 продолжает описывать
первое rationale и его origin. Точные payload-поля определяет владелец publication
при реализации своей публичной операции.

Полный переносимый срез должен содержать связанные постановки, снимки, outcomes
и принятые предметные основания. Чтение сохранённой истории использует эти
значения, поэтому собственная live projection не становится внешним источником
старого rationale. Сравнение повторного импорта относится к полному содержимому
одной identity/revision; новая basis сохраняется отдельной публикацией.
Реализованные сейчас чтения и изменение известной Task приведены в
[graph-профиле](graph-req.md).

## 8. Контракты, конфигурация и ошибки реализации

### 8.1 Что остаётся неизменным

Текущий executable `apps/requirements/bin/requirements` использует
[shell contract](shell-req.md) и [graph-профиль](graph-req.md): пакет `operation/input`,
три внешние операции и описанные в профиле виды запросов/изменений.
`application_task` изменяется полной следующей постановкой `revise_task`.
Полные намерения и ответы сохраняются для exact replay.

Расширенный target `stride-requirements/v1`, публикация, auxiliary-операции,
outcomes и transfer остаются проектом [public contract](contract-req.md).
Точную форму purpose-aware snapshot/transfer определяет владелец их реализации,
сохраняя предметные значения §5.1. Развёртывание текущего приложения использует
полную явную конфигурацию из [справочника параметров](shell-req.md#конфигурация-приложения)
и новую самостоятельную базу. Примеры настроек принадлежат этому справочнику;
рабочие параметры отдельной установки остаются у оператора.

### 8.2 Уточнение target auxiliary операций

T031 предлагает два явных discriminated operation/query variants внутри
уже описанного target v1. Они не меняют набор полей существующего
`put_requirement` или `get_requirement`. До реализации variant отклоняется
`unknown_operation`; зависимая ещё не реализованная policy — `unsupported_policy`.
Текущий T034 переносит поддержанные исходным продуктом сценарии, описанные в
[практической инструкции](graph-req.md#обслуживание-и-новый-системный-смысл).
Предлагаемые ниже variants относятся к отдельной будущей реализации структурированного
назначения с собственными shapes, fixtures и явно объявленной capability.

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

Прежний проект legacy import относится к историческому анализу §2/§6.
Текущая установка работает с самостоятельной моделью, а выбранная история
возвращает реально сохранённые значения. `publish_task` принимает текущую
постановку и полное предметное основание через публичного владельца Requirements;
purpose-aware расширение остаётся отдельным проектом.

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
| Несогласованные значения/links одной snapshot identity или неизвестная объявленная schema выбранного среза | `store_incompatible` / `referential_conflict` | Прежние значения сохраняются |
| Неизвестная attempt policy | `unsupported_policy` | Satisfaction не пересчитывается по догадке |
| Lock не получен за configured timeout | `store_busy` | Нет partial owner writes; bounded retry допустим вызывающей стороне |
| Crash после commit до ответа | Exact request/import replay | Возвращается сохранённый результат, новых ID/snapshots нет |

Детали содержат конкретную identity/source locator и ожидаемую/наблюдаемую revision,
без содержимого приватной соседней БД. Message предназначен человеку, branching —
по code. Rejection не публикует успех для удачной половины batch.

### 8.5 Явный совместимый snapshot/transfer variant

Предметное содержание будущего transfer — точный Task ID, полные сохранённые
постановки/снимки/outcomes и их действительные связи. Срез описывает действительно
включённые identities и revisions. Из него восстанавливается тот же читаемый
граф истории, включая замороженные основания, без обращения к прежним live stores.

При записи выбранного среза owner сопоставляет identities, типы и предметные
references с заявленным содержимым. Совпадающие записи сравниваются как полные
значения; конфликт одной identity/revision разрешается в рамках операции, сохраняя
прежнюю историю. Весь согласованный срез сохраняется атомарно. Контрольные суммы,
подписи и проверки подмены файлов не являются admission-условиями этого контракта.
Точную версию wire-пакета, поля, результаты и границы поддержанного среза
определяет этап реализации transfer; новый вариант здесь не объявляется.

## 9. Concurrency, безопасность и наблюдаемость

Изменение через `apply` выполняется в короткой транзакции своего хранилища с явно
выбранной `expected_revision`. `create_task` выбирает свободный ID и сохраняет всю
первую постановку и первоначальный ответ одной такой транзакцией; общую ревизию
перед созданием вызывающая сторона не запрашивает. Новый outer request сохраняет итоговые значения и исходный
ответ, точный replay возвращает этот ответ. Согласованные данные одного чтения
относятся к одной owner revision. Выдача ID реализована в `create_task`;
публикация развивается отдельно. Точное повторное создание возвращает первоначальный
ID/receipt независимо от текущей ревизии постановки.
Регистрация у следующего владельца — отдельное публичное действие.

Протокол передаёт предметные JSON-значения. Каждый owner работает со своим
состоянием и выбранной конфигурацией, описывает результат и причину обычного отказа.
Для наблюдения используются request ID, ревизия до/после, изменения и сохранённый
ответ. Происхождение данных описывает принятое решение, а текущая авторизация
изменения относится к его владельцу. Рабочие секреты остаются в конфигурации
оператора; автоматическая отправка raw payload во внешние сервисы не предусмотрена.

Пределы пакета/страницы и ожидания локальной записи задаются явно. Обещания
производительности основываются на измерениях целевой реализации. Восстановление
данных и конфигурации выполняется отдельным инструментом оператора; выбранный
нечитаемый record даёт ошибку его операции, остальные читаемые данные используются.

## 10. Границы последующих работ и доказательство дизайна

Самостоятельное хранение, оба перехода M:N, применение статусов, чтение/изменение
Task, создание ID, исходный creation intent, оценка оснований, история, снимки и поиск описаны в действующих
[storage](storage-req.md) и [graph](graph-req.md) профилях. Эти инструкции относятся
к реализованному коду.

Публикация постановок, outcomes/satisfaction и явный выбор реализованы в
[текущем профиле](outcome-operations.md). Предметная политика attempts и пересчёта
сохраняется в [канонической таблице](outcome-recalculation.md); данные исполнения
остаются у Execution. Purpose-aware готовность и transfer сохраняют отдельный
проектный статус; их интерфейсы уточняются у соответствующих владельцев.
Настоящий документ сохраняет предметные решения трёх уровней и самостоятельных
оснований, сохраняя границу между реализованным профилем и проектом.
