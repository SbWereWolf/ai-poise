# Библиотечный API — DDD-04B

Обновлено: **2026-09-06T22:58:21+05:00**. Срез **HARNESS-DDD-04B**.

## Доменные библиотеки
- `Task`: create/submit/assess_content/record_observations/assess_evidence/mark_verified/accept/rework/cancel. Владелец stage, итераций, submissions и оценки evidence. Никакого I/O.
- `SectionBook`, `ContentPolicy`: стандартные/дополнительные секции, трассировка, pre/post gates. Контракты DDD-02 сохранены.
- `RouteDefinition`, `RouteProgress`, `FeedbackBook`: graph и внутренний цикл осмотра/исправления DDD-03.
- `CheckRegistry`: неизменные точные командные методы и явное расписание.
- `EvidencePlan.parse(raw, handlers, checks)`: полный план каждой стадии, references, writers, фазовая применимость.
- `EvidenceBook.record_batch(stage, iteration, tree, key, receipts)`: добавить immutable наблюдение, повтор идентичного batch — no-op.
- `EvidenceBook.precheck(...)`: структурные предусловия, обязательные prepare-аргументы и решения.
- `EvidenceBook.assess(...)`: оценка доступности текущих доказательств и предлагаемого handler outcome. Возвращает новый snapshot, не меняет Task самостоятельно.

## Прикладные API
`TaskCommands` — краткие UoW для создания, submission, content gates, workflow/evidence context, observation batch, assessment, mark_verified, accept/rework/cancel. Runtime не обращается к таблицам Task напрямую.

`StageRunner` делегирует текущую механику в TaskCommands; новые методы record_observations и assess_evidence не переключают этап.

`EvidenceCommands.record_receipt(task_id, actor, stage, iteration, receipt)` проверяет владельца, позицию, известные IDs методов и передаёт запись через EvidenceRepository. Это вход для наблюдающего адаптера, не для LLM. Идентичный ID не перезаписывается другим содержимым или другой задачей.

## Чтение
`TaskQueries.section/result/content/trace_point/history/summary/evidence_view`. Тексты остаются в SQLite. `evidence_view` читает snapshot задачи, не весь store. В данном срезе история evidence внутри задачи ещё не вынесена в постраничное чтение; raw text никогда не включается в snapshot.

## CLI
`harness work` принимает прямой JSON stdin. `operation` выбирает bootstrap/verify/show/artifacts/accept/cancel. Для известной работы task ID не передаётся повторно. Полный контракт и примеры: [batch-work](../workflows/batch-work.md).

`evidence_work` подаётся частью прямого result object, не редактируемым файлом. `awaiting_continuation` возвращает факты и новый шаблон объекта, а не требует повторного запуска команд. `accept` принимает/останавливается; `continue` выражает новую пользовательскую инструкцию.

## Конфигурация типа цели
`GoalTypeDefinition.parse/build`: общий чистый владелец полного process. `GoalConfigCommands.apply_batch(request)`: один декларативный пакет, создание из явного template либо update по revision. Порт `GoalConfigRepository.edit` реализует короткий controlled edit; FileGoalConfigRepository и FileTemplates — infrastructure.

CLI: `harness goal-config --settings <file>` с одним JSON в stdin. Не требует активной task и не открывает её DB. Стандартные/дополнительные sections, content rules, trace routes, stages/transitions редактируются одним кандидатом. Точный контракт: [goal-config](../configuration/goal-config.md).

`exclusive_lock` общий для Task и editor store. Runtime pack loader делегирует в тот же доменный validator. Конкретные EvidencePlan и command methods задачи не перемещены в pack и не заполняются по догадке.

## DDD-04B — пакетный вход
Обновлено: **2026-09-06T22:58:21+05:00**.

- `WorkTools.invoke(packet)` — общий прикладной вход через порты WorkRuntime, InteractionPort, WorkResourcesPort.
- `ArtifactPlan.parse(items, config)` — чистый полный план text/template-файлов.
- `FileArtifactFactory.prepare/materialize` — предвалидация и confined файловая публикация.
- `UserMessage.parse` / `InteractionLedger.merge` — identity и конфликт/дедупликация.
- `InteractionStore.prepare/record/delivered/summary` — SQLite-факты и проекция без lifecycle writes.
- `TaskCommands.validate_submission` — проверка кандидата без сохранения, перед файловыми side effects.
- `Harness.bootstrap(task_object, decision, feedback, rework_stage)` / `verify(payload)` — явный новый transport; обязательного result_path нет.
- `TaskQueries.latest_submission` возвращает пакет вместе с section layers, а не только envelope.

Public CLI: `harness work` (stdin JSON) и `harness goal-config --settings ...`. Прежние CLI-команды из исторических отчётов не являются действующим API. Полная грамматика: [batch-work](../workflows/batch-work.md).


# DDD-05: Sprint / Task publication

Обновлено: **2026-09-06T22:58:21+05:00**.

- `SprintPolicy.parse`, `SprintPlan.create/apply`, `Sprint.draft/revise/publish/waive/update_dependencies/cancellation_scope/record_cancellation/overview` — чистый домен.
- `SprintCommands.apply(packet)` — draft/publish/graph/cancellation, единый UnitOfWork.
- `SprintCommands.read/select` — адресные проекции, snapshots и session scope.
- `validate_creation(contract, process, automatic_checks)` и `build_task(metadata, actor)` — общая проверка и сборка отдельной/планируемой Task, без I/O.
- `Task.planned`, `Task.start`, `TaskCommands.start` — незанятая задача и её начало через владельца.
- `SqliteSprintRepository` — только свои таблицы, layers/receipts и составные FK. Публикация Task через TaskRepository.
- `SprintWork` — инфраструктурный port adapter для Git-readiness и существующего runtime; не SQL-editor Task.
- WorkTools принимает `operation: sprint`; `bootstrap` принимает ID зарегистрированной сущности; `show` читает несколько Sprint views. Остальные work methods сохраняются.

[Точные входы и границы](../workflows/sprints.md). Кандидаты из переписки не превращены в новые обязательные правила без классификации пользователя.

## DDD-06: библиотека планов и публикации
`PlanSpec.parse(raw,max_steps)` валидирует весь явный план. `ActionRun` владеет cursor/status/immutable prefix, `start/record/retry_publication` не исполняют I/O. `Publication.parse` требует полный ref/expected SHA/authorization.

`PlanCommands.obtain/update/snapshot/record_assessment/restart` — прикладной API через ActionRepository/UoW. `RuntimePlanActions.apply/publish` — внешняя механика. `ApplyPlanHandler/PublishHandler` — чистый приём намерения, не самоудостоверение результата. `Task.record_action_result` связывает trusted receipt с текущей итерацией и деревом.

Все действия подключены к прежнему `WorkTools.invoke` и `verify`: отдельная цепочка register/merge/receipt/refresh для модели не требуется. Точные примеры пакетов и продолжения: [Actions](../workflows/actions.md).

## DDD-07: runtime/output/handoff

Обновлено: **2026-09-07T00:07:53+05:00**.
- `RuntimeIdentity.parse` — чистая идентичность; `validate_inventory` — явные capability факты.
- `RuntimeAdapter.invoke` — transport composition над WorkTools, а не новый domain API.
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
`HookService` связывает native event с session и InteractionStore, но не меняет lifecycle. Generated launcher вызывает прежний WorkTools с текущим сообщением. Public interfaces: runtime-setup (полный settings + install + probe), runtime-config (install/probe), hook (native event), hook-work (bound packet). `HookSettings.proposed` проверяет кандидат настроек до его записи; `setup_runtime` использует тот же FileHookRepository и не переписывает активные bindings. См. [полный контракт](../configuration/runtime-hooks.md).


## Accounting API — DDD-08

Updated: 2026-09-07T02:34:02+05:00.

`MetricPolicy` и `BenefitDefinition` — чистые явные контракты. `AccountingCommands` работает через `AccountingPort`. `RuntimeAccounting` связывает исходную Task, clock и измеритель; `SqliteAccounting` хранит ledger и receipts. `AccountingQueries` строит read-only итоговые отчёты. `PayloadMeasurer` читает закреплённые Git blobs и final section layers; он не управляет Task lifecycle.

Production default `RuntimeAccounting` использует `AnchoredUtcClock`: одна UTC-метка
фиксируется при создании адаптера, а последующие метки продвигаются по `time.monotonic()`.
Инъекция callable `clock` остаётся поддерживаемой границей для детерминированных тестов.
Это защищает один процесс от коррекции wall clock назад; сравнимость сохранённого состояния
между загрузками по-прежнему определяется accounting contract, а не скрытым clamp.

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


# Project setup — 2026-09-07T14:15:55+05:00

| API | Контракт |
|---|---|
| `ProjectBlueprint.parse(raw)` | Полный явный шаблон, вопросы и process references |
| `ProjectBlueprint.build(edits, max_edits)` | Один кандидат; нет изменения исходного шаблона и разрешения конфликтов порядком |
| `Survey.answer/keep/back` | Явное изменение позиции/ответов; нет файлов или публикации |
| `ProjectCommands.apply(request)` | Проверка пакетного намерения и вызов port |
| `ProjectCommands.questionnaire(request)` | Тот же contract для CLI анкеты |
| `project_tools(settings_path)` | Явная composition библиотеки |
| `FileProjectSetup.apply` | Независимые snapshots, общий load_config, локальные probes, atomic create, receipt |

CLI `project` принимает один JSON stdin. CLI `project-init` получает ответы через bounded
questionnaire и применяет один тот же пакет при publish. Обе операции не изменяют Task/Sprint.

## Verification source provenance — task 0037

`CheckRegistry.from_task/extend` требует у нового метода `source_under_test`, проверяет форму
repository/external provenance и отклоняет semantic duplicates и конфликтующие ожидания до
I/O. `resolve_source_under_test(method, worktree, cwd, environment)` — runtime API разрешения
binding внутри текущего worktree. Результат запуска включает `source_provenance`,
`provenance_digest` и `expectation_digest`; replay identity учитывает эти значения. Старые
сохранённые определения читаются без обратной миграции, но новые и расширяющие методы обязаны
передавать контракт явно.


## HARNESS-PILOT-02: граница вспомогательных инструментов — 2026-09-07T15:04:48+05:00

Task/Sprint/domain API не изменяется. `tools/run_test_packages.py` — development-инструмент,
использующий существующий `harness.execution.run_command`. `run_package(...)` возвращает
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
  --output /tmp/harness-regression \
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
через `WorkTools`; пример не восстанавливает состояние ручным редактированием SQLite и не
принимает следующий этап без пользовательского решения.

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
команда не создаёт задачу и не подменяет требуемое решение пользователя.
