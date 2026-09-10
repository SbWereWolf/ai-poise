# Библиотеки, API и общий код инструментов

Создано: **2026-09-06T16:04:53+05:00**.

Статус: **проект архитектуры для реализации; код продукта не изменён**.

## 1. Структура поставки

Библиотека здесь — импортируемый Python-пакет с публичным контрактом, а не отдельный PyPI-проект, сервис или отдельная БД. Сначала одна установка и один CLI. Новое физическое разбиение не требует дополнительных инструментальных вызовов LLM.

```text
src/harness/
  composition.py                  # единственное место сборки конкретных adapters
  interfaces/
    cli/
    runtime_hooks/
  application/
    api.py                        # общая библиотека инструментов
    command_pipeline.py
    query_pipeline.py
    use_cases/
    runner/
    stage_handlers/
  modules/
    foundation/
    content/
    processes/
    verification/
    tasks/
    sprints/
    sessions/
    workspaces/
    executions/
    artifacts/
    metrics/
    operations/
    configuration/
  infrastructure/
    sqlite/
    filesystem/
    git/
    commands/
    parsers/
    runtime_adapters/
    transfer/
    observability/
```

Имена — целевая структура кода. Наличие каталога не повод писать пустые слои: малый модуль может быть одним файлом. Новые numeric limits, путь БД, названия пользовательских представлений и команды проекта в этих файлах не фиксируются; их передаёт проверенная конфигурация.

В модуле с бизнес-логикой достаточно `domain`, `contracts`, `ports`, `api`. Реализации SQL и process execution не помещаются в domain. Вся связка создаётся обычными явными constructors; DI-фреймворк, динамический import произвольного Python из конфигов и глобальный service locator не нужны.

## 2. Библиотека инструментов

`HarnessApplication` — одна точка входа из CLI, runtime hook adapter, demo и будущих интеграционных тестов. Она предоставляет типизированные команды/запросы. Публичный CLI сохраняет существующую компактную грамматику; нижеприведённые методы не означают новую CLI-команду для каждой операции.

```text
CLI / runtime adapter
        ↓
HarnessApplication
        ↓
CommandPipeline или QueryPipeline
        ↓
BootstrapWork / VerifyStage / ApplyUserDecision / Handoff ...
        ↓
доменные API + ports
```

`CommandPipeline` повторно использует: разрешение текущей сессии и project, проверку входного schema, authority/scope, operation identity, лимит, захват короткого UoW, фиксацию outcome/Incident, формирование компактного ответа. Конкретный use case решает, где нужны внешние действия и следующий короткий UoW. Pipeline не держит один lock весь verify.

`QueryPipeline` использует read snapshot и ограниченный renderer. Он не снимает/создаёт claim, не делает refresh-запись задачи и не изменяет current task. Технические метрики чтения — отдельный явно разрешённый побочный учёт, не изменение результата запроса.

## 3. Высокоуровневые команды

| Команда библиотеки | Основной вход от агента/пользователя | Доменные библиотеки | Единый результат |
|---|---|---|---|
| `BootstrapWork` | Project и scope при первом выборе; смысл следующей инструкции | sessions, tasks/sprints, processes | Контекст, текущая работа/eligible set, result template, roots и необходимые capabilities |
| `VerifyStage` | Содержимое одного предусмотренного result file; ID уже в binding | tasks, verification, artifacts, processes + execution ports | Сохранённый результат, checks, доклад или конкретная работа той же стадии |
| `ContinueStage` | Предусмотренный продолжением payload | те же | Продолжение той же операции без повторения уже выполненных внешних шагов |
| `ApplyUserDecision` | Принять, продолжить, rework, отменить, waive, временное исключение | tasks/sprints, processes, sessions | Записанное решение и новый актуальный контекст; может быть частью bootstrap |
| `PublishTaskPlan` | Подготовленный draft, решение о публикации | tasks, sprints при членстве | Валидная опубликованная Task |
| `PublishSprintPlan` | Полный draft спринта и задач, решение | sprints, tasks | Атомарно видимый sprint и его tasks |
| `ReviseSprintPlan` | Пакет membership/dependency/section изменений | sprints + tasks при изменении контрактов | Пересчитанные готовность и blockers |
| `HandoffWork` | Краткий содержательный итог и WIP commit message при необходимости | tasks/sessions/artifacts + workspace/transfer ports | Устойчивая передача текущего состояния и снятие binding |
| `DiscardWork` | Решение о сбросе в разрешённой области | sessions + workspace port | Сброс только собственных текущих изменений; не отмена task |
| `CancelTask` | Однозначная санкция пользователя | tasks, sprints, sessions | Cancelled; последствия для зависимостей, без обычных checks |
| `ForceCloseSprint` | Санкция пользователя и судьба сохранённой работы | sprints, tasks, sessions | Закрытый выбранный спринт и честный итог WIP/отмен |
| `CreateProjectBinding` | Полная анкета/явный ввод | configuration | Проверенный manifest, atomic write |
| `TransferWork` | Scope/режим переноса и разрешённый destination | operations + task/sprint export APIs + transfer adapter | Receipt, исходная revision и проверенный комплект |
| `IngestUsage` | Наблюдаемые usage events из поддерживаемого адаптера | metrics | Принятые события без дублирования, completeness |

`ContinueStage` может транспортно быть тем же `verify` с разрешённой фазой. Это не дополнительное правило LLM «всегда вызвать ещё одну команду». Он нужен только там, где появились новые факты и требуется новое рассуждение.

## 4. Task API: добавление информации

Все методы проходят через Task-owned mutation context. `TaskId`, stage, iteration, user scope, процесс и revision разрешаются библиотекой, а не повторяются агентом в каждом поле.

| Метод | Вход | Изменение | Особенность |
|---|---|---|---|
| `TaskCommands.submit_stage_result` | Один `StageSubmission` | Кандидатные слои, findings/proposals, точные methods, ручные evidence, решения | Обычный путь одной пакетной записи через verify |
| `TaskCommands.append_sections` | Map `section_id → content` | Новые слои разрешённых секций | Для отдельных осмысленных сохранений; не обязателен перед verify |
| `TaskCommands.register_methods` | Полные definitions и связи с obligations | Версии methods/registry | Никаких вымышленных команд по именам тестов |
| `TaskCommands.record_findings` | Список findings | Findings и их refs | Пакетно; не отдельный вызов на каждую находку |
| `TaskCommands.propose_resolutions` | Finding refs + proposal + evidence refs | Отдельные предложения | Не закрывает finding как принятое исправление |
| `TaskCommands.record_manual_evidence` | Проверяемый аргумент/наблюдение по разрешённой фазе | Manual evidence proposals | Без фиктивного exit code; не private reasoning transcript |
| `TaskCommands.record_inspection` | Coverage, findings, решения по предложениям | Inspection result и evidence decisions | Subject pin/read-only проверяет общий finalizer |
| `TaskCommands.record_decisions` | Смысл, authority, scope, явная применимость | Неизменные decisions | Изменение полномочий не выводится из самого факта наличия текста |
| `TaskCommands.link_artifact_paths` | Только список путей | Artifact refs и связь текущей работы | Path validation/ID делает artifacts; task принимает допустимые refs |
| `TaskCommands.request_rework` | Основание + целевой этап | Новая итерация и invalidation relations | Предыдущие результаты сохраняются |

Эти функции не означают десяток обязательных инструментальных вызовов. `submit_stage_result` вызывает соответствующие внутренние методы **одним ChangeSet и UoW**. Частичные публичные методы нужны для действительно отдельного действия, но не обходят ограничения стадий.

У них нет универсального `extra_fields` или «обновить любой JSON». Новое поле результата определяется в process schema и обрабатывается библиотекой, которая владеет его смыслом. Структурно корректная, но недопустимая для текущей фазы запись отклоняется.

## 5. Sprint API

| Метод | Вход | Выход |
|---|---|---|
| `SprintCommands.submit_plan` | Draft sprint + draft task contracts | Сохранённый кандидат без публикации незавершённых задач |
| `SprintCommands.publish_plan` | Validated plan + publication decision | Полный опубликованный sprint |
| `SprintCommands.append_sections` | Разрешённые изменения sprint sections | Новые слои; общая механика content |
| `SprintCommands.update_dependencies` | Пакет add/remove/waive с readiness contracts | Проверенный DAG и готовность |
| `SprintCommands.add_tasks` | Полные draft tasks | Членство после создания собственными Task validators |
| `SprintCommands.record_decisions` | Решения по плану/зависимостям/ветвям | Business record спринта |
| `SprintCommands.link_artifact_paths` | Только пути текущего sprint | Shared artifact refs |
| `SprintCommands.resolve_cancelled_branch` | Явная судьба зависимых задач | Изменение только вычисленной области и согласованных tasks |
| `SprintCommands.force_close` | Санкция и dispositions WIP | Терминальный исход выбранного спринта |

Не предоставляется `set_eligible_tasks`: eligible set является вычислением, а не ручным вводом агента. Нет `assign_model`, `launch_codex`, межспринтовых dependencies и автоматического выбора исполнителя.

## 6. Запросы и доменные repositories

Запросы возвращают DTO, а не mutable агрегаты:

| Query API | Задача |
|---|---|
| `TaskQueries.context(binding, section_policy)` | Текущая задача/этап/слои, активные findings, недостающее evidence |
| `TaskQueries.summary(task_ref)` | Короткий человеческий статус |
| `TaskQueries.section(ref, revision, range_spec)` | Адресное чтение current или старого слоя, строки/байты |
| `TaskQueries.iteration(ref)` | Состав результата конкретного подхода |
| `TaskQueries.evidence(ref)` | Метод, предмет, актуальность, observation и решение осмотра |
| `TaskQueries.trace(ref, filters)` | Требование → метод → evidence → verdict |
| `SprintQueries.overview(sprint_ref)` | Цель, итоги, все eligible tasks, существенные blockers |
| `SprintQueries.next_work(sprint_ref)` | Библиотечная часть bootstrap, не обязательная новая CLI-команда |
| `ArtifactQueries.resolve(ref)` | Готовый путь и гарантированный scope/lifetime |
| `MetricsQueries.aggregate(scope, period, types)` | Затраты/польза по типу цели и в целом с completeness |
| `IncidentQueries.for_work_cycle(binding)` | Инциденты текущего доклада |

Repositories используются **для изменения домена**, а не для всех видов UI-чтения:

```text
TaskRepository.load_for_change(task_id, required_slice)
TaskRepository.persist(task_change_set, expected_version)
SprintRepository.load_for_change(sprint_id, required_slice)
SprintRepository.persist(sprint_change_set, expected_version)
SessionRepository.load_for_change(session_id)
OperationRepository.load(operation_id)
```

`required_slice` не позволяет произвольную lazy SQL-загрузку внутри Task. Repository заранее получает данные, необходимые заявленной команде. История не загружается целиком; доменная проверка, требующая дополнительного факта, явно расширяет slice либо получает соответствующий факт от read port.

Реализация `persist` не делает собственный commit. Все repositories конкретной операции используют один connection/UoW. Отдельного repository на каждую строку секции/находки как публичного CRUD API нет.

## 7. Что общее технически

| Общий компонент | Используют | Что он НЕ решает |
|---|---|---|
| `CommandPipeline` | Все мутирующие входы | Бизнес-правила конкретного Task/Sprint |
| `QueryPipeline` | Все read-only запросы | Переходы/claim |
| `SectionBook` + SQL mapper | Task, Sprint, ad-hoc record | Какие секции обязательны в development |
| `MutationBatch / ChangeSet` | Пакетные доменные изменения | Не разрешает запись произвольных колонок |
| `UnitOfWork` | Repositories одного короткого commit | Не охватывает Git и filesystem в ACID |
| `OperationCoordinator` | Verify, handoff, publication, transfer | Не подменяет route policy |
| `ArtifactRegistry` | Все три scope | Не придумывает purpose/type артефакта |
| `ExecutionService` | observe, check, apply_plan, finalizer | Не синтезирует команды проекта |
| `ContextAssembler` | Bootstrap, verify, recovery, user decision | Не оценивает содержательную истинность evidence |
| `ResultPresenter` | CLI и hooks | Не меняет verdict команды при ошибке renderer |

Предпочтительна композиция. Общий базовый код — инфраструктурный pipeline и небольшие reusable value objects/алгоритмы, не `BaseHandler`, который знает SQLite, Git, метрики, задачи, спринты и все 13 бизнес-целей.

## 8. Где находятся семь обработчиков

В `application/stage_handlers/` — стратегии текущей разрешённой фазы. Они получают контекст и immutable stage contract, возвращают типизированный outcome и, при необходимости, план внешней операции. Доменное решение о применении результата остаётся у Task/RoutePolicy.

| Handler | Переиспользуемые библиотеки | Уникальная механика |
|---|---|---|
| `produce` | tasks, content, verification | Принять созданный агентом результат |
| `observe` | executions, artifacts, verification | Получить факты и при необходимости продолжение агента |
| `check` | verification + executions | Исполнить методы и сопоставить факты обязательствам |
| `inspect` | tasks, content, verification | Сохранить read-only осмотр и отдельные решения |
| `revise` | produce/apply_plan + tasks/verification | Добавить активные findings и обязательность resolution proposals |
| `apply_plan` | executions, workspaces, artifacts | Выполнить согласованные внешние изменения и остановиться для решения при новых фактах |
| `publish` | application coordinators + tasks/sprints или workspace port | Механическая публикация с разрешением и receipt |

Ни один из семи не делает прямой `UPDATE tasks SET state=...`. Внутри `inspect` нет собственного Git CLI: он передаёт проверяемый предмет и требуемую read-only гарантию в общий цикл, который получает фактический snapshot через WorkspacePort.
