# Библиотечный API — DDD-04B

Обновлено: **2026-10-17**. Исходный срез **POISE-DDD-04B** и последующие API.

## Локальные assets — 2026-10-04

`RestoreRequest.parse`, `AssetDeclaration.parse` и `AssetSpec.parse` в
`poise.modules.local_assets.domain` владеют формой, текстовыми инвариантами,
ролями, коллизиями и идентичностью. Чистый `validate_utf8_text` используется
общим `text` и отдельной проверкой optional repair; ошибки не выводят
непригодное текстовое значение.

`LocalAssetsRestore` координирует полную предварительную проверку и публикацию
через `LocalAssetsPort`/`LocalAssetsStorage`. `FileLocalAssets` реализует
nofollow наблюдения, реальный выбранный Git root и create-only публикацию.
Окружение только Git subprocess исключает `GIT_`; окружение вызывающего процесса
сохраняется. `local_assets_tools()` связывает этих владельцев, а
`interfaces.local_assets.execute` обрабатывает строгий JSON transport публичной
команды `poise local-assets`. Task lifecycle и repair ей не принадлежат.
Точные входы, отказы и границы эффектов описаны в
[каноническом контракте](../configuration/local-assets.md).

## Доменные библиотеки

Requirements CRUD: чистая политика `modules.requirements_registry.crud` проверяет
пакеты, строки и курсоры; `infrastructure.requirements_crud` выполняет параметризованный
SQL в соединении RequirementsStore. Application координирует прежних владельцев,
interfaces обслуживает CLI. [Wire-контракт и
восстановление](../workflows/requirements-registry.md#пакетный-crud)
принадлежат руководству реестра, а не второму описанию схемы API.

## Самостоятельное хранилище Requirements

`poise.infrastructure.requirements_registry.RequirementsStore.standalone(database,
lock,lock_seconds,poll_seconds,backend,*,registry_type)` создаёт store самостоятельного
формата с явно выбранными backend и моделью. Фабрика приложения Requirements передаёт
`registry_type=RequirementsGraph`. Существующий constructor `RequirementsStore(...)`
сохраняет прежнюю двухуровневую композицию другого потребителя.

Выбранная модель объявляет `query_kinds` — свои специализированные виды запросов —
и `restore(**rows)`. Результат восстановления предоставляет `query(query,max_items)`,
`apply(operations,max_items)` и `to_dict()`. Аргументы `restore` возвращает backend;
модель владеет предметными решениями. Общий store сохраняет транзакцию, общую ревизию,
CAS, запросы типизированных страниц, сравнение намерений и сохранённый ответ.
Дополнительные виды проходят общую проверку размера/корреляции, затем точную проверку
полей своей моделью. Прежние типизированные виды сохраняют свой строгий validator.

Backend приложения реализует пять методов:

| Метод | Вход и результат |
| --- | --- |
| `initialize(connection)` | Подготовить новую схему или прочитать обязательную metadata своего формата. Вернуть `None`. |
| `registry_rows(connection)` | Вернуть именованные входы выбранной модели. Для RequirementsGraph: `requirements`, `links`, `task_identities`, `task_requirement_versions`, `application_task_links`, `revisions`, `owner_revision`, `outcomes`, `decisions`, `reference_ids`. |
| `prepare_queries(connection)` | Подготовить TEMP-представления `requirements(id,level,status,text)` и `requirement_links(system_id,application_id)` для общего SQL-владельца. Вернуть `None`. |
| `previous_request(connection,namespace,request_id)` | Вернуть `None` либо dict с декодированными JSON objects `intent` и `response`. Ошибка необходимых сохранённых данных описывается как `PoiseError`. |
| `persist_apply(connection,before,after,revision,request_id,intent,response)` | Сохранить итоговые значения, следующую общую ревизию и точный запрос/ответ. Вернуть `None`. |

`requirements` содержит records `id/level/status/text`; `links` — пары
`system/application`. Входы Task содержат известные IDs/текущие указатели,
упорядоченные версии keys/status/text и их Application IDs. `revisions` —
отсоединённый порт сохранённых значений: `has_statement`, `statement`, `task_history`,
`requirement_history`, `latest_snapshot_revision`, `snapshot_for_statement`, `snapshot`,
`task_creation`, `references_requirement(requirement_id)`. Последний метод возвращает
bool по структурному членству ID в `requirements` сохранённых полных снимков,
декодируя необходимые данные при обращении. `reference_ids` содержит отсоединённые
IDs всех периодов верхних связей, назначений и их истории, а также присутствующих
связей предметного каталога. Нижние исторические и новые ссылки принадлежат входам
Task; новые замороженные ссылки — подготовленным публикациям. Адаптер `SavedRevisions`
владеет чтением выбранного JSON и переводом его ошибок в `PoiseError`; домен
работает со значениями без SQL, файловой системы и конкретного backend.

`apply` формирует отдельное предполагаемое состояние. Для backend `before/after`
содержат `requirements`, `links` и новые полные `statements` с `task_id` и
`statement_revision`, а также новые `publications`, `outcomes`, `decisions` и
отсортированный `deleted_requirements` — явные разрешённые завершения записей.
Этот временный список сохраняет смысл удаления и нового создания одного ключа,
даже когда конечные поля совпадают. Backend удаляет прежние собственные версии и
identity; итоговый record того же ключа вставляется заново с выбранным уровнем и
версией 1/новым origin. Для остальных записей действует прежнее добавление версии.
Backend дописывает версии/ключи/связи и текущий указатель,
закрывает снятые периоды и сохраняет ответ в переданной транзакции. Это обычные
входы сохранения; отдельного mutable журнала модели нет. Публичный запрос
`registry` сохраняет свой прежний ответ `requirements/links`; Task читается
специализированными запросами.

Соединение передано во временное пользование: транзакцию открывает и завершает
общий store, backend выполняет SQL в ней. Фабрика компонует модель и adapter;
SQL не принимает предметные решения о применимости. Когда TEMP-представление
совпадает с именем таблицы, backend указывает исходные таблицы через `main`.

Backend содержит единственную `requirements_meta.revision`, которую читают общие
методы. Пространство повторов `apply` — `registry.apply`. Полное намерение сравнивается
по каноническому JSON с сохранением типов и порядка списков; порядок ключей object
свободен. Композиция хранит намерение и ответ без digest. SQLite/filesystem ошибки
выходят как `PoiseError` после rollback. JSON выбранной истории декодируется для
этой операции; startup-проверки подмены или контрольных сумм отсутствуют.

Самостоятельная композиция использует `revision`, `registry`, `read`, `query`,
`apply`, `read_bootstrap`, `task_drafts`. `RequirementsCommands` предоставляет shell операции
`apply/query/import_bootstrap`. `command` и `migrate` принадлежат другому формату.

[Requirements](../../apps/requirements/docs/storage-req.md) владеет собственной
схемой и backend; [graph-профиль](../../apps/requirements/docs/graph-req.md) описывает
реализованные команды. Примеры относятся к текущему checkout. Потребитель общего
пакета выбирает и обновляет версию отдельно; исходники не подтверждают публикацию
пакета.

### Создание самостоятельного черновика Task — 2026-10-17

`RequirementsStore.task_drafts()` — публичная owner-local UoW-граница явного
standalone backend. Она открывает существующую короткую транзакцию и передаёт
узкий repository через `backend.task_drafts(connection)`; прикладной клиент
работает с портом создания, а SQL остаётся в adapter. В другой композиции
эта возможность требует явно выбранного соответствующего backend.

`apps.requirements.commands.DraftTaskCommands.create_task(request)` начинает
эту границу, использует чистый `DraftTask`, проверяет прежнее намерение запроса
и выбирает identity через явно переданный `TaskIdPolicy`. `DraftTask` сохраняет
полный исходный input, различает переданные и отсутствующие поля, проверяет явный
выбор формы; `allocate` выбирает свободный ID. Общая конфигурация читается заново
при каждом вызове shell, включая replay.

`TaskDraftRepository` реализует `creation(request_id)`, `identifiers()` и
`create(task_id,draft,max_items)`. Создание переиспользует
`RequirementsGraph.validate_statement/create_draft` и существующий
`StandaloneBackend._persist_statement`. Identity, statement, ключи/версии/связи,
owner revision и первоначальный receipt сохраняются одной транзакцией. Возврат
наружу происходит после её успешного завершения. Новых таблиц или миграции для
этого пути нет. Пространство `task_creation_requests` независимо от `registry.apply`.

`RequirementsGraph.task_basis` читает текущие `current/future` entries, исключает
obsolete и вызывает исходный `RequirementsRegistry.plan_task` для каждого
применимого требования. Значение строится из `status:ready/gaps` и исходных gaps;
общее `basis_ready` требует непустого набора и полноты каждого основания.
Оценка принадлежит домену, не SQL или сериализатору. `SavedRevisions.task_creation`
читает исходные intent/receipt; текущие statement/history сохраняют своих владельцев.
`work_choice` остаётся декларацией создания в исходном intent. Соседние membership,
claim и execution записи создают их отдельные публичные владельцы.

Полные [текущие
CLI-пакеты](../../apps/requirements/docs/graph-req.md#создание-task-и-канонический-id)
показывают оригинальное создание и последующую постановку. Shell компонует новый
app use case для `create_task`; общий `RequirementsCommands` продолжает обслуживать
`apply/query/import_bootstrap`, включая реализованную `publish_task` внутри `apply`.
Расширенный schema-envelope и purpose-aware правила относятся к отдельно
обозначенному целевому интерфейсу.

### Исходы и полный текущий каскад — 2026-10-18

`RequirementsGraph.restore` получает явную общую `owner_revision`, сырые строки
`outcomes` и упорядоченные `decisions`. Доменный `OutcomeLedger` различает факты
и явные решения для Task/публикации/key; `null` — собственное решение, отличное
от его отсутствия. `record` сохраняет новый факт и допускает первый positive
только при отсутствии решения текущей публикации; `select` меняет конкретный
выбор, `state` возвращает полную историю, `pending` — только новые строки записи.
Сохранённые keys JSON читаются при обращении к нужному факту, без SQL в домене.

`SavedRevisions.snapshot_for_statement(task_id,statement_revision)` выбирает
публикацию по заголовкам; её тело декодируется отдельно через `snapshot`.
`RequirementsGraph` сочетает сохранённые и подготовленные в пакете публикации.
`publish_task` использует имеющийся `RequirementsRegistry.plan_task` и сохраняет
полное основание в позиции операции с фиксируемой общей ревизией. Текущая оценка
не подменяет публикацию текущей постановки последним историческим snapshot.

`current_satisfaction` обходит все текущие постановки и полные текущие наборы
родителей в собственном графе. Старые и новые родители после изменения связей
получают оценку по тем же данным; кеш или чтение Execution store для этого
не нужны. `outcome_state` читает выбранную публикацию с фактами и решениями.
Backend сохраняет публикации/факты/решения и общую ревизию/ответ в переданной
транзакции; общий store сохраняет собственный порядок replay и revision checks.
[Рабочие пакеты](../../apps/requirements/docs/outcome-operations.md) определяют
фактический транспорт; будущий target-envelope остаётся отдельным проектом.

## Остальные доменные библиотеки

- `Task`:
  create/submit/assess_content/record_observations/assess_evidence/mark_verified/accept/rework/cancel.
  Владелец stage, итераций, submissions и оценки evidence. Никакого I/O.
- `SectionBook`, `ContentPolicy`: стандартные/дополнительные секции, трассировка, pre/post gates.
  Контракты DDD-02 сохранены.
- `RouteDefinition`, `RouteProgress`, `FeedbackBook`: graph и внутренний цикл осмотра/исправления
  DDD-03.
- `CheckRegistry`: точные командные методы, явное расписание, версионируемая current
  projection, immutable history и явная executable-классификация/покрытие.
- `EvidencePlan.parse(raw, handlers, checks)`: полный план каждой стадии, references, writers,
  фазовая применимость.
- `EvidenceBook.record_batch(stage, iteration, tree, key, receipts)`: добавить immutable наблюдение,
  повтор идентичного batch — no-op.
- `EvidenceBook.precheck(...)`: структурные предусловия, обязательные prepare-аргументы и решения.
- `EvidenceBook.assess(...)`: оценка доступности текущих доказательств и предлагаемого handler
  outcome. Возвращает новый snapshot, не меняет Task самостоятельно.

## Прикладные API
`TaskCommands` — краткие UoW для создания, submission, content gates, workflow/evidence context,
observation batch, assessment, mark_verified, accept/rework/cancel. Runtime не обращается к таблицам
Task напрямую.

`StageRunner` делегирует текущую механику в TaskCommands; новые методы record_observations и
assess_evidence не переключают этап.

`CheckRegistry.apply_change` принимает один пакет
`request_id/expected_revision/operations/executable_obligations`, атомарно меняет current
projection и сохраняет прежнюю revision в истории. `CheckRegistry.restore_state` проверяет
сохранённую классификацию против полного obligation catalogue. Task авторизует изменение
только через stage-owned `test_registry`, а inspection gate требует совокупное покрытие
текущих executable obligations непустым GREEN-набором. Persistence не переписывает methods,
receipts или submissions и не восстанавливает current classification из immutable creation
metadata.

`EvidenceCommands.record_receipt(task_id, actor, stage, iteration, receipt)` проверяет владельца,
позицию, известные IDs методов и передаёт запись через EvidenceRepository. Это вход для наблюдающего
адаптера, не для LLM. Идентичный ID не перезаписывается другим содержимым или другой задачей.

## Чтение
`TaskQueries.section/result/content/trace_point/history/summary/evidence_view`. Тексты остаются в
SQLite. `evidence_view` читает snapshot задачи, не весь store. В данном срезе история evidence
внутри задачи ещё не вынесена в постраничное чтение; raw text никогда не включается в snapshot.

## CLI
`poise work` принимает прямой JSON stdin. `operation` выбирает
bootstrap/verify/show/artifacts/accept/cancel. Для известной работы task ID не передаётся повторно.
Полный контракт и примеры: [batch-work](../workflows/batch-work.md).

`evidence_work` подаётся частью прямого result object, не редактируемым файлом.
`awaiting_continuation` возвращает факты и новый шаблон объекта, а не требует повторного запуска
команд. `accept` принимает/останавливается; `continue` выражает новую пользовательскую инструкцию.

## Конфигурация типа цели
`GoalTypeDefinition.parse/build`: общий чистый владелец полного workflow.
`GoalConfigCommands.apply_batch(request)`: один декларативный пакет, создание из явного template
либо update по revision. Порт `GoalConfigRepository.edit` реализует короткий controlled edit;
FileGoalConfigRepository и FileTemplates — infrastructure.

`GoalConfigCommands.status(request)` наблюдает managed head, live revision и pending request
без recovery и иных записей. `GoalConfigCommands.reconcile(request)` при отсутствии pending
сверяет exact managed/live revisions, валидирует полный live workflow тем же доменным parser и
атомарно принимает его как новый managed head с durable audit receipt. Adoption не
перезаписывает workflow-файл или Task snapshots; неизвестный drift остаётся явным конфликтом.

CLI: `poise goal-config --settings <file>` с одним JSON в stdin. Не требует активной task и не
открывает её DB. Стандартные/дополнительные sections, content rules, trace routes,
stages/transitions редактируются одним кандидатом. Точный контракт:
[goal-config](../configuration/goal-config.md).

`exclusive_lock` общий для Task и editor store. Runtime pack loader делегирует в тот же доменный
validator. Конкретные EvidencePlan и command methods задачи не перемещены в pack и не заполняются по
догадке.

## DDD-04B — пакетный вход
Обновлено: **2026-09-06T22:58:21+05:00**.

- `WorkTools.invoke(packet)` — общий прикладной вход через порты WorkRuntime, InteractionPort,
  WorkResourcesPort.
- `ArtifactPlan.parse(items, config)` — чистый полный план text/template-файлов.
- `FileArtifactFactory.prepare/materialize` — предвалидация и confined файловая публикация.
- `UserMessage.parse` / `InteractionLedger.merge` — identity и конфликт/дедупликация.
- `InteractionStore.prepare/record/delivered/summary` — SQLite-факты и проекция без lifecycle
  writes.
- `TaskCommands.validate_submission` — проверка кандидата без сохранения, перед файловыми side
  effects.
- `AI poise.bootstrap(task_object, decision, feedback, rework_stage)` / `verify(payload)` — явный
  новый transport; обязательного result_path нет.
- `TaskQueries.latest_submission` возвращает пакет вместе с section layers, а не только envelope.

Public CLI: `poise work` (stdin JSON) и `poise goal-config --settings ...`. Прежние CLI-команды из
исторических отчётов не являются действующим API. Полная грамматика:
[batch-work](../workflows/batch-work.md).


# DDD-05: Sprint / Task publication

Обновлено: **2026-09-06T22:58:21+05:00**.

- `SprintPolicy.parse`, `SprintPlan.create/apply`,
  `Sprint.draft/revise/publish/waive/update_dependencies/cancellation_scope/record_task_cancellation/cancel/overview`
  — чистый домен.
- `SprintCommands.apply(packet)` — draft/publish/graph/cancellation, единый UnitOfWork.
- `SprintCommands.read/select` — адресные проекции, snapshots и session scope.
- `validate_creation(contract, workflow, automatic_checks)` и `build_task(metadata, actor)` — общая
  проверка и сборка отдельной/планируемой Task, без I/O.
- `Task.planned`, `Task.start`, `TaskCommands.start` — незанятая задача и её начало через владельца.
- `SqliteSprintRepository` — только свои таблицы, layers/receipts и составные FK. Публикация Task
  через TaskRepository.
- `SprintWork` — инфраструктурный port adapter для Git-readiness и существующего runtime; не
  SQL-editor Task.
- WorkTools принимает `operation: sprint`; `bootstrap` принимает ID зарегистрированной сущности;
  `show` читает несколько Sprint views. Остальные work methods сохраняются.

Публичная cancellation-модель различает текущую standalone Task, выбранных незавершённых участников
Sprint и Sprint целиком. Последний вариант одной UoW сохраняет completed/cancelled участников и
durable work, отменяет остальные Task через их доменный API и освобождает claims; unresolved pending
блокирует весь пакет до первой мутации.

[Точные входы и границы](../workflows/sprints.md). Кандидаты из переписки не превращены в новые
обязательные правила без классификации пользователя.

## DDD-06: библиотека планов и публикации
`PlanSpec.parse(raw,max_steps)` валидирует весь явный план. `ActionRun` владеет
cursor/status/immutable prefix, `start/record/retry_publication` не исполняют I/O.
`Publication.parse` требует полный ref/expected SHA/authorization.

`PlanCommands.obtain/update/snapshot/record_assessment/restart` — прикладной API через
ActionRepository/UoW. `RuntimePlanActions.apply/publish` — внешняя механика.
`ApplyPlanHandler/PublishHandler` — чистый приём намерения, не самоудостоверение результата.
`Task.record_action_result` связывает trusted receipt с текущей итерацией и деревом.

Все действия подключены к прежнему `WorkTools.invoke` и `verify`: отдельная цепочка
register/merge/receipt/refresh для модели не требуется. Точные примеры пакетов и продолжения:
[Actions](../workflows/actions.md).

## DDD-07: runtime/output/handoff

Обновлено: **2026-09-07T00:07:53+05:00**.
- `RuntimeIdentity.parse` — чистая идентичность; `validate_inventory` — явные capability факты.
- `SessionEstablisher.establish` — общий application-owner caller → session для direct work,
  runtime adapter и hook transport. Он повторно использует неизменяемую запись
  `runtime_bindings`, атомарно резервирует уникальную сессию и повторяет generated-кандидат при
  коллизии. Origin сохраняет provenance, но не предоставляет дополнительных прав; generated
  binding не является anti-tamper credential.
- `RuntimeAdapter.invoke` — transport composition над `SessionEstablisher` и WorkTools, а не новый
  domain API.
- `CodexTranscript.read` — bounded JSONL источник и позиция, без хранения prompt body.
- `OutputPolicy.parse/select` — чистая схема известных/unknown parser profiles.
- `OutputParser.render(primary|materialize)` — один парсер над неизменным receipt.
- `ResultViews.capture/finish` — запуск subprocess worker и финализационный barrier.
- `HandoffCommands.prepare/release/resume` — короткие транзакции с Task.handoff/Task.resume_handoff.
- `LocalHandoff.preserve/resume` — Git/file adapter; не присваивает lifecycle.
- `EvidenceCommands.list_for` — адресный список receipts текущей задачи через UoW.
- `Sprint.overview(states,resumable)` — явное множество подтверждённых handoff, без неизвестных task
  claims.

Протоколы: [runtime](../configuration/runtime-services.md),
[handoff](../workflows/local-handoff.md), [перенос](../workflows/transfer.md).


## DDD-07B: перенос сохранённой работы

Обновлено: **2026-10-09**. `WorkTools.invoke` передаёт `operation=transfer` через
`TransferCommands`/`TransferRequest` в `RuntimeTransfers`. Существующий локальный
handoff сохраняет владение/WIP; экспорт компактной копии не переносит его bundle.

`TransferPolicy`/`RecoveryPolicy` владеют явным контрактом конфигурации;
`placement_plan` — решением версии, `material_source`/`relocate_path` — чистыми
привязками. `SqliteTransferRepository.capture/install` сохраняет scoped snapshot
и публикует только отсутствующих владельцев. `SqliteTransferContext` получает
импортную доставку отдельно по Task/Sprint; только Task-контекст обогащается
`verification_opened`/`verification_origin`. Авторитет пропуска не угадывается по
членству Task или совпавшему ID из другого scope.

`WorkspaceRecoveryFlow` материализует явный flow, сохраняет запуск до эффекта и
terminal-факты после ожидания. `RecoveryDelivery` размещает файлы/код, готовит
компоненты и проверяет состояние. Неизвестный исход не порождает новый запуск;
SQL-публикация не объявляет filesystem/Git атомарными. `TaskQueries` разрешает
подтверждённые пути, сохраняя текст и происхождение. Текущий этап открывается через
существующего владельца handoff/Task; импорт этапные проверки не выполняет.

[Формат/API/конфигурация](../workflows/transfer.md#конфигурация) ·
[Ошибки и исходы](../workflows/transfer.md#согласованность-и-ошибки).

## DDD-07C — 2026-09-07T01:17:28+05:00
`HookCommands.install` принимает целый definition; `HookRepository` сохраняет candidate/receipt и
только свои groups. `HookDefinition` проверяет явность native-контракта. `CapabilityChecks.run`
валидирует пакет ProbeSpec и получает независимые результаты через ProbeExecutor.
`LocalProbeExecutor`/`StdioProbe` — внешние адаптеры.
`HookService` связывает native event с session и InteractionStore, но не меняет lifecycle. Generated
launcher вызывает прежний WorkTools с текущим сообщением и запрещает запись Python bytecode в source
checkout; точная предыдущая управляемая форма launcher обновляется при native rebind, а произвольное
изменение не перезаписывается. Public interfaces: runtime-setup (полный settings + install + probe),
runtime-config (install/reconcile/probe/diagnose/summary/refresh), hook (native event),
hook-work (bound packet).
`HookSettings.proposed` проверяет кандидат настроек до его записи; `setup_runtime` использует тот же
FileHookRepository и не переписывает активные bindings. См. [полный
контракт](../configuration/runtime-hooks.md).

`CapabilitySummary.from_mapping` проверяет строгую структуру сохранённого результата;
`to_dict` возвращает независимое представление. Порт `SummaryStore.load/save` хранит
этот результат. `SessionReadiness.read(session_id)` возвращает сводку своей сессии без
probes и наблюдения часов. `refresh(session_id, workspace, probe_values, clock)` повторно
использует `CapabilityChecks`, получает время от явного Clock и сохраняет завершённую
сводку. `FileCapabilitySummary` реализует bounded JSON I/O через существующие владельцы.
`HookService.read_summary/refresh_summary` проверяют прежнюю native ownership, а
`HookRegistry.session_workspace` читает наблюдённый workspace независимо от файла сводки.
Контракт и команды — [сводка
готовности](../configuration/runtime-hooks.md#сводка-готовности-сессии).


## Accounting API — DDD-08

Updated: 2026-09-07T02:34:02+05:00.

`MetricPolicy` и `BenefitDefinition` — чистые явные контракты. `AccountingCommands` работает через
`AccountingPort`. `RuntimeAccounting` связывает исходную Task, clock и измеритель;
`SqliteAccounting` хранит ledger и receipts. `AccountingQueries` строит read-only итоговые отчёты.
`PayloadMeasurer` читает закреплённые Git blobs и final section layers; он не управляет Task
lifecycle.

Batch work получил optional `telemetry`, а batch show — kind `accounting`. Определение полезных
categories/sections редактируется одной `set_benefit` action goal-config. Существующие API сохранены
по смыслу, но старые неподдерживаемые schema не читаются. Подробности:
[accounting.md](../operations/accounting.md).


# DDD-09 — каталог и planning publication

Обновлено: **2026-09-07T07:34:23+05:00**.

- `TaskBlueprint.parse(raw).instantiate(parameters,workflow,automatic_checks)` — чистая
  типизированная подстановка и штатная проверка создаваемого контракта.
- `CatalogueCommands.install(items)` — один явный пакет установки/редактирования независимых
  процессов через GoalConfigCommands; проверка итоговых кандидатов до публикации.
- `CatalogueCommands.tasks(items,workflows,automatic_checks)` — пакет полных контрактов задач.
- `PlanningPublications.publish(task_id,actor,intent)` — публикация сохранённого принятого
  planning-результата через Task/Sprint repositories в одном UoW.
- `FileCatalogue` — проверяемые файловые шаблоны; не пишет бизнес-таблицы.
- `validate_batch_request(request,max_changes)` — единая форма запроса одиночного редактора
  и многопроцессного каталога, без второй трактовки обязательных полей.

Общий WorkTools verify принимает tagged publication `kind=tasks|sprint`, section и authorization.
Другая ветвь PublishHandler — существующая Git target publication. Отдельного CLI-комплекта
для каждой цели нет. Подробные поля, ограничения и пример:
[workflow-catalogue](../configuration/workflow-catalogue.md).


# Project setup — 2026-09-14T00:00:00+05:00

| API | Контракт |
|---|---|
| `ProjectBlueprint.parse(raw)` | Полный явный шаблон, вопросы и workflow references |
| `ProjectBlueprint.build(edits, max_edits)` | Один кандидат; нет изменения исходного шаблона и разрешения конфликтов порядком |
| `Survey.answer/keep/back` | Явное изменение позиции/ответов; нет файлов или публикации |
| `ProjectCommands.apply(request)` | Проверка пакетного намерения и вызов port |
| `ProjectCommands.list()` | Read-only выдача настроенных проектов через тот же port |
| `ProjectCommands.check(raw)` | Явная конфигурация → существующий Git probe → commit policy → опциональный поиск Task; известный отказ и неожиданная ошибка различаются |
| `ProjectCommands.input_failure(raw, reason)` | Полный диагностический ответ при невалидном вводе, без вызова проверяемых владельцев |
| `ProjectPreflightRequest.parse(raw)` | Ровно шесть полей `project-preflight-1`, без неявных селекторов |
| `ProjectPreflightReport` | Один владелец verdict, четырёх checks и recovery; готовность не даёт claim |
| `ConfigurationObservation` | Наблюдённый context, configuration либо `null`, status и reason; известные отказы не становятся `unknown` |
| `FileProjectContext` | Двенадцать фактических полей установки, запроса, manifest и разрешённых путей, без выбора другого проекта |
| `FileProjectPreflight` | Одно чтение manifest и общая валидация; композиция существующих Git и availability владельцев |
| `ReadOnlyProjectAvailability.startable/require_task` | Общий защищённый доступ к выбранной Task DB; обычные Task/Sprint repositories и правила доступности без повторного SQL |
| `ProjectCommands.questionnaire(request)` | Тот же contract для CLI анкеты |
| `project_tools(settings_path)` | Явная composition библиотеки |
| `FileProjectSetup.apply` | Независимые snapshots, общий load_config, локальные probes, atomic create, receipt и регистрация |
| `FileProjectSetup.list` | Строгий registry, общий load_config, стабильная сортировка usable/error записей без mutation |

CLI `project` без действия принимает один JSON stdin; его действие `list` использует тот же
`ProjectCommands` и не читает stdin. CLI `project-init` получает ответы через bounded
questionnaire и применяет один тот же пакет при publish. Создание и анкета публикуют запись
через registry-владельца, а список остаётся read-only. Эти операции не изменяют Task/Sprint.

Публичные поля, verdict, CLI и маршруты исправления определены в
[проверке согласованности
проекта](../configuration/project-setup.md#проверка-согласованности-проекта).
`FileProjectPreflight.observe_configuration` сохраняет уже прочитанные факты при известном
отказе валидации и неожиданной ошибке. Прикладной слой использует это наблюдение и прекращает
последующие проверки; он не повторяет валидацию конфигурации, Git или commit policy.
Разрешение Requirements DB/lock — проверка размещения, не открытие этой базы.

## Защищённое чтение хранилища задач

`poise.infrastructure.sqlite.readonly.readonly_snapshot(path, *, lock_seconds)` — общий
владелец защищённого rollback-снимка для `ReadOnlyProjectAvailability._with_store`.
Его используют поиск точного Task ID и обзор доступных задач `next`. Consumer получает
SQLite connection в памяти. `_with_store` задаёт `row_factory`, `query_only=ON`, начинает
транзакцию, проверяет текущую версию схемы и вызывает существующие repositories.
`Database`/`Runtime` для такого чтения не создаются: их инициализация выполняет записи.

Исходный обычный файл открывает только отдельный stdlib-процесс `readonly_worker.py`.
Он удерживает shared OFD-блокировку всего файла до EOF, проверяет заголовок, отсутствие
sidecar-файлов, inode/размер/режим/mtime/ctime, длину образа и неизменность заголовка. Родитель
получает полный образ через pipe и использует `deserialize` в `:memory:` с
`temp_store=MEMORY`; SQLite connection к исходному файлу не открывается. Родитель не
открывает исходный inode даже для чтения образа, поэтому его закрытие не снимает уже
существующие POSIX SQLite-блокировки вызывающего процесса.

Защита дочернего процесса живёт до закрытия memory consumer, включая выход по исключению.
После этого родитель закрывает stdin, дочитывает stdout до ожидания завершения, закрывает
pipe и забирает результат процесса. Ошибка consumer/транспорта остаётся первичной;
вторичная ошибка cleanup добавляется к ней как примечание. Без первичной ошибки сбой
cleanup возвращается как `SnapshotWorkerError`. Ожидаемый отказ передаётся как
`PoiseError` с исходной причиной, неожиданный сбой worker/протокола остаётся неопределённостью,
а не ошибкой конфигурации по предположению. Решение о `rejected`/`unknown` принимает
существующая композиция проверки проекта.

Дочерний процесс запускается текущим `sys.executable` с `-I -B`, без shell, подмены
окружения, другого runtime или файлового IPC. `-I` исключает влияние переменных Python,
user-site и пути скрипта на поиск модулей; он **не отключает** установленные system-site
`.pth` и `sitecustomize`. Полная изоляция установленной Python-среды не обещается.

Границы поддержанного чтения:

- Linux LP64 OFD ABI для `x86_64` и `aarch64`, с явной проверкой платформы и раскладки
  `struct flock`; альтернативы через POSIX/flock нет. Текущие проверки выполнены на
  `x86_64`; это не свидетельство запуска на каждом поддержанном ABI.
- Нужен `sqlite3.Connection.deserialize`. При его отсутствии — явный отказ без чтения
  исходного файла через SQLite.
- Поддержан согласованный rollback-файл с текущей Task DB schema. Отсутствующий,
  повреждённый, несовместимый файл и конфликтующий писатель отклоняются.
- WAL-заголовок отклоняется даже без sidecar-файлов. Любой `-wal`, `-shm`, `-journal`,
  включая broken symlink, вызывает отказ до unsafe main-only чтения. Наличие journal
  не объявляется доказательством, что он hot; checkpoint, recovery и удаление журнала
  не выполняются.
- Образ и memory SQLite требуют память порядка размера БД; временная файловая копия,
  скрытый лимит размера и иной способ чтения не подставляются. Недостаток памяти — отказ.
  `lock_seconds` приходит из конфигурации для memory connection, не задаёт retry worker.
- Гарантия относится к взаимодействию с SQLite-писателями, соблюдающими файловые
  блокировки. Защита от произвольной намеренной подмены файла, custom VFS и восстановления
  `journal_mode=OFF` не подтверждена этим контрактом.

Чтение не меняет исходные файлы, schema, Task history, claim или pending и не выделяет
новые Git-ресурсы. Успешный снимок одного проекта не создаёт общий snapshot нескольких
проектов и не резервирует последующий старт Task.

## Verification source provenance — task 0037

`CheckRegistry.from_task/extend` требует у нового метода `source_under_test`, проверяет форму
repository/external provenance и отклоняет semantic duplicates и конфликтующие ожидания до
I/O. `resolve_source_under_test(method, worktree, cwd, environment)` — runtime API разрешения
binding внутри текущего worktree. Результат запуска включает `source_provenance`,
`provenance_digest` и `expectation_digest`; replay identity учитывает эти значения. Старые
сохранённые определения читаются без обратной миграции, но новые и расширяющие методы обязаны
передавать контракт явно.

Composition root предоставляет тот же `resolve_source_under_test` интеграционному
владельцу через `Runtime.source_under_test_resolver`. `RuntimeResultIntegration`
использует его при подготовке фактического запуска и при проверке пригодности
сохранённой партии перед публикацией. Контроллер сохраняет собственные исходники;
путь кандидата передаётся явно только проверяемой операции. Второго resolver или
неявной подстановки установленного пакета нет.


## POISE-PILOT-02: граница вспомогательных инструментов — 2026-09-07T15:04:48+05:00

Task/Sprint/domain API не изменяется. `tools/run_test_packages.py` — development-инструмент,
использующий существующий `poise.execution.run_command`. `run_package(...)` возвращает
terminal receipt одного pytest-модуля; `parse_junit(...)` отделяет missing/invalid/empty
report от нулевого кода процесса. Он не изменяет бизнес-состояние приложения.

`examples/project_execution.continue_execution(...)` — пример для конкретной verification-
программы. Все изменения Task, handoff и transfer выполняются через WorkTools. См.
[рабочий путь](../workflows/happy-path.md) и [пакетную работу](../workflows/batch-work.md).

Для изолированного полного прогона pytest-пакетов используется development-инструмент:

```bash
export PYTHONPATH="$PWD/src"
python tools/run_test_packages.py \
  --root "$PWD" \
  --output /tmp/poise-regression \
  --workers 2 \
  --timeout 900 \
  --success-workspaces delete
```

Значения здесь являются явным примером, а не скрытыми defaults. `--timeout` ограничивает
один pytest-процесс; автоматических повторов нет. При `--success-workspaces delete`
удаляются только успешно завершённые временные каталоги. Каталоги отказов, таймаутов,
пустого или повреждённого JUnit сохраняются вместе с stdout, stderr и JSON-описанием
команды. Успех требует кода 0, корректного непустого JUnit без failure/error и неизменности
проверяемых исходников; skip учитывается отдельно.

`examples/project_execution.py` продолжает только уже подготовленную verification-задачу и
не является общим route engine. Он принимает существующий project config, session, Task ID,
явное решение `continue` и ID операции экспорта. Изменения Task, handoff и transfer проходят
через `WorkTools`; пример не восстанавливает состояние ручным редактированием SQLite. Для
последовательного `executor`-этапа решением служит действующее поручение исполнителю; пример
не выдаёт полномочий на `reviewer`, приёмку, публикацию или интеграцию.

Запуск для уже подготовленной verification-задачи:

```bash
export PYTHONPATH="$PWD/src"
python examples/project_execution.py \
  --config /absolute/generated-project/project.json \
  --session CURRENT_SESSION \
  --task-id PLANNED_VERIFICATION_TASK \
  --user-decision continue \
  --export-request-id EXECUTION-CHECKPOINT-1
```

Пути и идентификаторы в примере нужно заменить фактическими значениями существующей задачи;
команда не создаёт задачу и не подменяет требуемое поручение пользователя.
`CheckRegistry.from_task` и `CheckRegistry.extend` требуют `verification_plan`
для новых методов и выполняют раннюю route-проверку. `from_items` восстанавливает
исторические снимки без плана; автоматическая migration и двойная запись не
вводятся.


## Самостоятельное хранилище Sequencing

`apps.sequencing.migrations.v1.migrate(connection)` владеет историческим шагом
пустого либо поддержанного исходного хранилища к формату 1;
`apps.sequencing.migrations.v2.migrate(connection)` — шагом 1 → 2 с точным
сохранением прежних Snapshot references и отдельными Statement references.
Каждый исторический модуль самодостаточен. Текущий `Store.connect` последовательно
подготавливает собственную БД до формата 2. Каждый шаг имеет отдельную транзакцию.

`apps.sequencing.domain.registration` владеет равенством происхождения и
правилами первоначального графа Sprint. SQLite adapter
`apps.sequencing.infrastructure.registration_store` читает и сохраняет факты
в транзакции существующего `Store.apply`; он сам не фиксирует транзакцию.
Requirements выдаёт канонический ID и сохраняет постановку/снимки; Sequencing
сохраняет переданный ID и точный вид/номер ссылки. Execution сохраняет свой процесс.

`apps.sequencing.domain.eligibility.project_eligibility` владеет чистым правилом
доступности относительно caller: состояние, действующий Task claim и все
предшественники применяются совместно, с сохранением имеющихся ссылок результата.
`apps.sequencing.infrastructure.eligibility_read.EligibilityReadRepository`
читает необходимые собственные Task/edge/terminal факты и кандидатов выбранной
области через переданное соединение. Транзакцией владеет `Store.query`.
`Store._eligibility` объединяет эти факты с существующим `Store._active_claim`
и одной domain-проекцией для обоих запросов.

`apps.sequencing.domain.ownership` содержит чистые permissions источника,
цели, handoff и immutable intent. `apps.sequencing.application.ownership.OwnershipCommands`
получает repository, общую availability-функцию, генератор ID и часы явно;
`execute(operation)` выполняет claim/switch/release/resume/recover внутри
существующей транзакции `Store.apply`.
`apps.sequencing.infrastructure.ownership_store.OwnershipRepository`
читает Task/claims/Session capacity и сохраняет immutable intent/handoff/consumption
в собственном SQLite connection. Shell владеет строгими wire fieldsets,
композицией и outer receipt/revision/rollback. Переключение сначала проверяет
обе стороны, затем одной локальной транзакцией меняет claims. Recovery использует
явное операторское решение после остановки писателя и только освобождает claim;
следующая Session возобновляет работу обычным resume. Task ownership и Workspace
binding остаются независимыми. Точные поля, replay и порядок отказов принадлежат
[контракту владения](../../apps/sequencing/docs/contract-seq.md#54-claim_task).

Текущий CLI выполняет `register_task`, `register_sprint`, `claim_task`,
`switch_claim`, `release_claim`, `resume_claim`, `recover_claim`,
`workspace/prepare` и четыре query-варианта:
Task, Sprint, `eligibility`, `next`. Явные caller и области выбора описаны в
[руководстве доступности](../../apps/sequencing/docs/shell-seq.md#доступность-и-следующая-работа).
Independent и явное Sprint membership используют одну Task identity. `Store.query`
открывает одну read-транзакцию до чтения revision и собирает
происхождение, членство, active claim, текущий Workspace, упорядоченные
roots, независимую binding и terminal value только из своих таблиц. Изменение
Sprint и независимого владения/освобождения Workspace остаётся последующим срезом.
Подробные предусловия, ограничения SQL, пошаговый откат и правильные примеры
принадлежат [руководству хранилища](../../apps/sequencing/docs/storage-seq.md),
запуск и рабочие параметры — [руководству CLI](../../apps/sequencing/docs/shell-seq.md).

### Подготовка постоянного рабочего пространства

`apps.sequencing.application.workspace.WorkspaceCommands.prepare(packet, digest)`
получает короткую transaction-фабрику, Git adapter, resolver корней, генератор ID,
часы и публичную schema. Он координирует проверку request/claim, сохранение
постоянного контекста и незавершённого намерения, внешний Git и окончательный ответ.
Публичный вход — `Store.prepare_workspace`; точные поля и полный Workspace DTO
принадлежат [текущему
контракту](../../apps/sequencing/docs/contract-seq.md#60-реализованная-подготовка).

`apps.sequencing.domain.workspace` владеет чистыми правилами состояния и
совместимости контекста. `WorkspaceRepository` в infrastructure.workspace_store
владеет явными SQL-фактами и единственной проекцией Workspace для prepare/query;
Task и claim читает через существующий OwnershipRepository. Транзакцию задаёт
shell через `Store.workspace_transaction`, Git туда не входит.
`GitWorkspace` в infrastructure.workspace_git наблюдает начальный commit и
выполняет обычные Git операции по сохранённым path/branch/base. Код application
и domain получает эти зависимости явно. Полный ответ содержит текущую независимую
binding, подготовка её сохраняет. Read-only использует существующий repository.

Подробности slot незавершённого запроса, окончательных квитанций, revision и
операторского продолжения принадлежат [модели
хранения](../../apps/sequencing/docs/storage-seq.md#постоянная-подготовка-workspace).
