# Библиотечный API — DDD-04B

Обновлено: **2026-10-08**. Исходный срез **POISE-DDD-04B** и последующие API.

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
- `Task`: create/submit/assess_content/record_observations/assess_evidence/mark_verified/accept/rework/cancel. Владелец stage, итераций, submissions и оценки evidence. Никакого I/O.
- `SectionBook`, `ContentPolicy`: стандартные/дополнительные секции, трассировка, pre/post gates. Контракты DDD-02 сохранены.
- `RouteDefinition`, `RouteProgress`, `FeedbackBook`: graph и внутренний цикл осмотра/исправления DDD-03.
- `CheckRegistry`: точные командные методы, явное расписание, версионируемая current
  projection, immutable history и явная executable-классификация/покрытие.
- `EvidencePlan.parse(raw, handlers, checks)`: полный план каждой стадии, references, writers, фазовая применимость.
- `EvidenceBook.record_batch(stage, iteration, tree, key, receipts)`: добавить immutable наблюдение, повтор идентичного batch — no-op.
- `EvidenceBook.precheck(...)`: структурные предусловия, обязательные prepare-аргументы и решения.
- `EvidenceBook.assess(...)`: оценка доступности текущих доказательств и предлагаемого handler outcome. Возвращает новый snapshot, не меняет Task самостоятельно.

## Прикладные API
`TaskCommands` — краткие UoW для создания, submission, content gates, workflow/evidence context, observation batch, assessment, mark_verified, accept/rework/cancel. Runtime не обращается к таблицам Task напрямую.

`StageRunner` делегирует текущую механику в TaskCommands; новые методы record_observations и assess_evidence не переключают этап.

`CheckRegistry.apply_change` принимает один пакет
`request_id/expected_revision/operations/executable_obligations`, атомарно меняет current
projection и сохраняет прежнюю revision в истории. `CheckRegistry.restore_state` проверяет
сохранённую классификацию против полного obligation catalogue. Task авторизует изменение
только через stage-owned `test_registry`, а inspection gate требует совокупное покрытие
текущих executable obligations непустым GREEN-набором. Persistence не переписывает methods,
receipts или submissions и не восстанавливает current classification из immutable creation
metadata.

`EvidenceCommands.record_receipt(task_id, actor, stage, iteration, receipt)` проверяет владельца, позицию, известные IDs методов и передаёт запись через EvidenceRepository. Это вход для наблюдающего адаптера, не для LLM. Идентичный ID не перезаписывается другим содержимым или другой задачей.

## Чтение
`TaskQueries.section/result/content/trace_point/history/summary/evidence_view`. Тексты остаются в SQLite. `evidence_view` читает snapshot задачи, не весь store. В данном срезе история evidence внутри задачи ещё не вынесена в постраничное чтение; raw text никогда не включается в snapshot.

## CLI
`poise work` принимает прямой JSON stdin. `operation` выбирает bootstrap/verify/show/artifacts/accept/cancel. Для известной работы task ID не передаётся повторно. Полный контракт и примеры: [batch-work](../workflows/batch-work.md).

`evidence_work` подаётся частью прямого result object, не редактируемым файлом. `awaiting_continuation` возвращает факты и новый шаблон объекта, а не требует повторного запуска команд. `accept` принимает/останавливается; `continue` выражает новую пользовательскую инструкцию.

## Конфигурация типа цели
`GoalTypeDefinition.parse/build`: общий чистый владелец полного process. `GoalConfigCommands.apply_batch(request)`: один декларативный пакет, создание из явного template либо update по revision. Порт `GoalConfigRepository.edit` реализует короткий controlled edit; FileGoalConfigRepository и FileTemplates — infrastructure.

`GoalConfigCommands.status(request)` наблюдает managed head, live revision и pending request
без recovery и иных записей. `GoalConfigCommands.reconcile(request)` при отсутствии pending
сверяет exact managed/live revisions, валидирует полный live process тем же доменным parser и
атомарно принимает его как новый managed head с durable audit receipt. Adoption не
перезаписывает process-файл или Task snapshots; неизвестный drift остаётся явным конфликтом.

CLI: `poise goal-config --settings <file>` с одним JSON в stdin. Не требует активной task и не открывает её DB. Стандартные/дополнительные sections, content rules, trace routes, stages/transitions редактируются одним кандидатом. Точный контракт: [goal-config](../configuration/goal-config.md).

`exclusive_lock` общий для Task и editor store. Runtime pack loader делегирует в тот же доменный validator. Конкретные EvidencePlan и command methods задачи не перемещены в pack и не заполняются по догадке.

## DDD-04B — пакетный вход
Обновлено: **2026-09-06T22:58:21+05:00**.

- `WorkTools.invoke(packet)` — общий прикладной вход через порты WorkRuntime, InteractionPort, WorkResourcesPort.
- `ArtifactPlan.parse(items, config)` — чистый полный план text/template-файлов.
- `FileArtifactFactory.prepare/materialize` — предвалидация и confined файловая публикация.
- `UserMessage.parse` / `InteractionLedger.merge` — identity и конфликт/дедупликация.
- `InteractionStore.prepare/record/delivered/summary` — SQLite-факты и проекция без lifecycle writes.
- `TaskCommands.validate_submission` — проверка кандидата без сохранения, перед файловыми side effects.
- `AI poise.bootstrap(task_object, decision, feedback, rework_stage)` / `verify(payload)` — явный новый transport; обязательного result_path нет.
- `TaskQueries.latest_submission` возвращает пакет вместе с section layers, а не только envelope.

Public CLI: `poise work` (stdin JSON) и `poise goal-config --settings ...`. Прежние CLI-команды из исторических отчётов не являются действующим API. Полная грамматика: [batch-work](../workflows/batch-work.md).


# DDD-05: Sprint / Task publication

Обновлено: **2026-09-06T22:58:21+05:00**.

- `SprintPolicy.parse`, `SprintPlan.create/apply`, `Sprint.draft/revise/publish/waive/update_dependencies/cancellation_scope/record_task_cancellation/cancel/overview` — чистый домен.
- `SprintCommands.apply(packet)` — draft/publish/graph/cancellation, единый UnitOfWork.
- `SprintCommands.read/select` — адресные проекции, snapshots и session scope.
- `validate_creation(contract, process, automatic_checks)` и `build_task(metadata, actor)` — общая проверка и сборка отдельной/планируемой Task, без I/O.
- `Task.planned`, `Task.start`, `TaskCommands.start` — незанятая задача и её начало через владельца.
- `SqliteSprintRepository` — только свои таблицы, layers/receipts и составные FK. Публикация Task через TaskRepository.
- `SprintWork` — инфраструктурный port adapter для Git-readiness и существующего runtime; не SQL-editor Task.
- WorkTools принимает `operation: sprint`; `bootstrap` принимает ID зарегистрированной сущности; `show` читает несколько Sprint views. Остальные work methods сохраняются.

Публичная cancellation-модель различает текущую standalone Task, выбранных незавершённых участников Sprint и Sprint целиком. Последний вариант одной UoW сохраняет completed/cancelled участников и durable work, отменяет остальные Task через их доменный API и освобождает claims; unresolved pending блокирует весь пакет до первой мутации.

[Точные входы и границы](../workflows/sprints.md). Кандидаты из переписки не превращены в новые обязательные правила без классификации пользователя.

## DDD-06: библиотека планов и публикации
`PlanSpec.parse(raw,max_steps)` валидирует весь явный план. `ActionRun` владеет cursor/status/immutable prefix, `start/record/retry_publication` не исполняют I/O. `Publication.parse` требует полный ref/expected SHA/authorization.

`PlanCommands.obtain/update/snapshot/record_assessment/restart` — прикладной API через ActionRepository/UoW. `RuntimePlanActions.apply/publish` — внешняя механика. `ApplyPlanHandler/PublishHandler` — чистый приём намерения, не самоудостоверение результата. `Task.record_action_result` связывает trusted receipt с текущей итерацией и деревом.

Все действия подключены к прежнему `WorkTools.invoke` и `verify`: отдельная цепочка register/merge/receipt/refresh для модели не требуется. Точные примеры пакетов и продолжения: [Actions](../workflows/actions.md).

## DDD-07: runtime/output/handoff

Обновлено: **2026-09-07T00:07:53+05:00**.
- `RuntimeIdentity.parse` — чистая идентичность; `validate_inventory` — явные capability факты.
- `SessionEstablisher.establish` — общий application-owner caller → session для direct work,
  runtime adapter и hook transport. Он повторно использует неизменяемую запись
  `runtime_bindings`, атомарно резервирует уникальную сессию и повторяет generated-кандидат при
  коллизии. Origin сохраняет provenance, но не предоставляет дополнительных прав; generated
  binding не является anti-tamper credential.
- `RuntimeAdapter.invoke` — transport composition над `SessionEstablisher` и WorkTools, а не новый domain API.
- `CodexTranscript.read` — bounded JSONL источник и позиция, без хранения prompt body.
- `OutputPolicy.parse/select` — чистая схема известных/unknown parser profiles.
- `OutputParser.render(primary|materialize)` — один парсер над неизменным receipt.
- `ResultViews.capture/finish` — запуск subprocess worker и финализационный barrier.
- `HandoffCommands.prepare/release/resume` — короткие транзакции с Task.handoff/Task.resume_handoff.
- `LocalHandoff.preserve/resume` — Git/file adapter; не присваивает lifecycle.
- `EvidenceCommands.list_for` — адресный список receipts текущей задачи через UoW.
- `Sprint.overview(states,resumable)` — явное множество подтверждённых handoff, без неизвестных task claims.

Протоколы: [runtime](../configuration/runtime-services.md), [handoff](../workflows/local-handoff.md), [перенос](../workflows/transfer.md).


## DDD-07B: перенос сохранённой работы
Обновлено: **2026-09-07T00:25:38+05:00**.

`WorkTools.invoke` принимает `operation=transfer` и один export/import-пакет. `TransferCommands.apply` валидирует явный `TransferRequest` и вызывает `TransferPort.export/restore`. `RuntimeTransfers` выполняет контролируемую композицию existing handoff, scoped SQLite snapshot, file/Git package и импорта.

`SqliteTransferRepository.capture` выбирает standalone batch или полный published sprint. `TaskRepository.restore_snapshot` и `SprintRepository.restore_snapshot` допускают только отсутствующих владельцев. `TaskQueries.resolve_path` и projection чтения разрешают подтверждённые relocations, не заменяя исходный текст. Формат команд и примеры: [transfer.md](../workflows/transfer.md).

## DDD-07C — 2026-09-07T01:17:28+05:00
`HookCommands.install` принимает целый definition; `HookRepository` сохраняет candidate/receipt и только свои groups. `HookDefinition` проверяет явность native-контракта. `CapabilityChecks.run` валидирует пакет ProbeSpec и получает независимые результаты через ProbeExecutor. `LocalProbeExecutor`/`StdioProbe` — внешние адаптеры.
`HookService` связывает native event с session и InteractionStore, но не меняет lifecycle. Generated launcher вызывает прежний WorkTools с текущим сообщением и запрещает запись Python bytecode в source checkout; точная предыдущая управляемая форма launcher обновляется при native rebind, а произвольное изменение не перезаписывается. Public interfaces: runtime-setup (полный settings + install + probe), runtime-config (install/probe), hook (native event), hook-work (bound packet). `HookSettings.proposed` проверяет кандидат настроек до его записи; `setup_runtime` использует тот же FileHookRepository и не переписывает активные bindings. См. [полный контракт](../configuration/runtime-hooks.md).


## Accounting API — DDD-08

Updated: 2026-09-07T02:34:02+05:00.

`MetricPolicy` и `BenefitDefinition` — чистые явные контракты. `AccountingCommands` работает через `AccountingPort`. `RuntimeAccounting` связывает исходную Task, clock и измеритель; `SqliteAccounting` хранит ledger и receipts. `AccountingQueries` строит read-only итоговые отчёты. `PayloadMeasurer` читает закреплённые Git blobs и final section layers; он не управляет Task lifecycle.

Batch work получил optional `telemetry`, а batch show — kind `accounting`. Определение полезных categories/sections редактируется одной `set_benefit` action goal-config. Существующие API сохранены по смыслу, но старые неподдерживаемые schema не читаются. Подробности: [accounting.md](../operations/accounting.md).


# DDD-09 — каталог и planning publication

Обновлено: **2026-09-07T07:34:23+05:00**.

- `TaskBlueprint.parse(raw).instantiate(parameters,process,automatic_checks)` — чистая
  типизированная подстановка и штатная проверка создаваемого контракта.
- `CatalogueCommands.install(items)` — один явный пакет установки/редактирования независимых
  процессов через GoalConfigCommands; проверка итоговых кандидатов до публикации.
- `CatalogueCommands.tasks(items,processes,automatic_checks)` — пакет полных контрактов задач.
- `PlanningPublications.publish(task_id,actor,intent)` — публикация сохранённого принятого
  planning-результата через Task/Sprint repositories в одном UoW.
- `FileCatalogue` — проверяемые файловые шаблоны; не пишет бизнес-таблицы.
- `validate_batch_request(request,max_changes)` — единая форма запроса одиночного редактора
  и многопроцессного каталога, без второй трактовки обязательных полей.

Общий WorkTools verify принимает tagged publication `kind=tasks|sprint`, section и authorization.
Другая ветвь PublishHandler — существующая Git target publication. Отдельного CLI-комплекта
для каждой цели нет. Подробные поля, ограничения и пример: [process-catalogue](../configuration/process-catalogue.md).


# Project setup — 2026-09-14T00:00:00+05:00

| API | Контракт |
|---|---|
| `ProjectBlueprint.parse(raw)` | Полный явный шаблон, вопросы и process references |
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
[проверке согласованности проекта](../configuration/project-setup.md#проверка-согласованности-проекта).
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
