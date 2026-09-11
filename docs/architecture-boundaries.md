# Границы ответственности: DDD-04B

Обновлено: **2026-09-11T05:57:22+05:00**. Срез **HARNESS-DDD-04B**.

| Владелец | Обязанность | Запрещено |
|---|---|---|
| Task | кандидатный результат, доказательства, transition, приёмка и rework | SQL/Git/команды |
| workflow/handlers | produce/inspect/revise/observe/check | goal-type branching, присваивание lifecycle |
| verification/CheckRegistry | точные команды, ID и расписание | изобретение команд |
| evidence | план, immutable observations/arguments/decisions, механическая достаточность | I/O, оценка истинности вместо агента |
| application/TaskCommands + EvidenceCommands | общий вход, авторизация позиции, короткая UoW | таблицы и внешние команды |
| SQLite repositories/UoW | запись с FK/optimistic version и внешний lock | сами принимать proof или переходить этап |
| runtime/execution/artifacts | реальные наблюдения Git/команд/files; publication и transport | прямые UPDATE Task/evidence state |

`EvidenceBook` используется композиционно; это не BaseTask и не отдельный движок каждого goal type. Runner делегирует чистым handler/domain, не держит lock во время subprocess. Generic observe/check отличаются способом интерпретации заданных наблюдений, не названием цели.

Правила происхождения, аргумента и осмотра остаются раздельными. `passed` — исход predicate, `interpretable` — распознанность наблюдения, `accepted/rejected` — последующий смысловой осмотр. Их нельзя сворачивать в один status.

Общий output/async parser слой и runtime integrations ещё относятся к будущим срезам. В DDD-04 raw capture синхронный, адресуемый и durable у задачи. Не объявлять новый слой полностью реализованным на основании формы EvidenceBook.


## Пакетный декларативный вход

Обновлено: **2026-09-06T22:58:21+05:00**. Task/config/artifact/interaction-вход реализован в DDD-04A/04B; Sprint использует тот же вход в DDD-05.

| Владелец | Новая обязанность | Что остаётся вне него |
|---|---|---|
| GoalTypeDefinition / GoalConfigCommands | Кандидат независимого pack, целостность итоговых ссылок, явные шаблоны, создание/изменение конфигурации одним пакетом | Доступ к чужой Task DB, миграции активных задач |
| BatchCommandPipeline | Один envelope, binding, request identity, пакетная validation, общий отчёт и журнал | Бизнес-решения всех доменов, произвольный raw update |
| Task/Sprint commands | Пакетные содержательные изменения через владельца и UoW | Ручное присваивание lifecycle из CLI |
| ArtifactFactory/ArtifactRegistry | Генерация из шаблона/содержания, path-only регистрация, roots и техническая идентичность | Изобретение содержания за агента, произвольная запись вне roots |
| InteractionLedger (accounting) | Различимые message events и единственный основной binding, дедупликация | Считать tool calls за сообщения, оценивать токены по сообщениям |
| Runtime/CLI adapter | Передать непосредственный payload и наблюдённый user event | Требовать заранее ручной JSON-файл или выдавать reported events за полный поток |
| SQLite/filesystem adapters | Сохранить валидный кандидат, staging и receipt, согласованная активация | Бизнес-валидация истинности аргумента |

Реализация общего pipeline не подменяет API Task/Sprint. Все конфиги Harness остаются в Harness; generated project/process файлы не редактируются агентом напрямую. Код приложения, тесты и документация приложения — через нативные инструменты согласно правилам codebase. Полный контракт: [declarative-tools](declarative-tools.md).

## DDD-04A — 2026-09-06T20:26:43+05:00

Новый доменный владелец `modules/goal_config` использует RouteDefinition, SectionRule и ContentPolicy без I/O. Application `GoalConfigCommands` делегирует файл/DB через ports; infrastructure размещена в `infrastructure/goal_config.py`. CLI adapter `interfaces/goal_config.py` принимает stdin, формирует bounded response и не обходит доменную validation. Общий `infrastructure/locking.py` используется двумя хранилищами.

Текущий Task process snapshot не зависит от актуальной копии pack; его project execution config остаётся отдельной границей. Версия store изменена явно. Новый editor не наследует чужой goal-type процесс: явный template копируется в независимое определение.

Список [кандидатов правил](rule-candidates/rule-candidates.md) не является новым источником policy до решения пользователя.

## DDD-04B — новые владельцы и зависимости
Обновлено: **2026-09-06T22:58:21+05:00**.

WorkTools — application facade, не новый агрегат. Task остаётся владельцем секций, evidence и lifecycle. ArtifactPlan — чистая модель генерации; FileArtifactFactory — инфраструктура; существующий ArtifactRegistry — регистрация owner-scoped файлов. InteractionLedger владеет identity/user-event rules, SQLite InteractionStore — таблицами observations и bindings.

`interfaces/work.py` читает stdin и оформляет bounded JSON/file response. Он не пишет таблицы Task. Application/work не импортирует SQL/filesystem. Composition root Runtime подключает порты. Чтение и запись пользовательских событий не поручаются LLM SQL-командами. Ledger transaction независима от Task verification: отрицательный test не отменяет реальное сообщение.

Общий API не реализует 13 разных engines. DDD-04A editor и DDD-04B work entry остаются отдельными предметными сценариями. Конфигурационные шаблоны и артефакты имеют разные contracts; никакого универсального произвольного `write_file` для служебных данных.


## DDD-05 — 2026-09-06T22:06:40+05:00
Sprint владеет планом/графом/решениями; Task владеет доступностью, началом и состоянием работы. Published Sprint создаёт Task через общий доменный builder и TaskRepository в одной UoW. Клиент не подменяет исходные шаблоны/таблицы рабочими JSON patch.

`modules/sprints` и `modules/tasks/definition` не имеют I/O. `application/sprints` не импортирует SQL/filesystem. `infrastructure/sprint_work` выполняет только доступ к наблюдаемым Git-объектам и делегирует Task API. База сериализует короткие записи; проверки/commit/push параллельных worktrees не держат общий DB lock. Readiness графа не равна auto-merge: разные result revisions возвращаются как integration_required.

### Публичный обзор работ — 2026-09-11

`WorkTools` композиционно строит `work_overview` через два узких read-порта: Sprint предоставляет идентификаторы опубликованного реестра и полные dependency-aware overview, Task предоставляет summary только самостоятельных задач. Application-слой проверяет разные словари статусов, применяет независимые фильтры к готовым owner-проекциям и собирает транспортный объект `{"sprints": ..., "standalone_tasks": ...}`.

Этот query не становится новым владельцем lifecycle, membership или readiness. Sprint по-прежнему определяет факт публикации, состояние графа, `eligible`/`blocked` и доступность result objects; Task определяет принадлежность и lifecycle standalone Task. SQLite-адаптеры реализуют параметризованные project-scoped reads, но клиент не получает SQL-интерфейс. Последовательное чтение двух read-models не объявляется единым cross-domain snapshot. Публичный пакет и ограничения результата описаны в [batch work](batch-work.md#обзор-опубликованных-спринтов-и-самостоятельных-задач), семантика Sprint — в [Sprint API](sprints.md#реестр-опубликованных-sprint).

## DDD-06: внешние планы
Владелец плана — Actions: PlanSpec/ActionRun/Publication. Task остаётся единственным владельцем verified/accepted и переходов. Application PlanCommands связывает их коротким UoW; Git/command адаптер не выполняет SQL. Репозиторий actions сохраняет run+event, а не весь Task обходным путём.

apply_plan/publish — обработчики общего runner. Sequential merge и command/probe различаются адаптерной стратегией plan.kind, не названием business goal. Источники/команды явные; нет автоматического чтения AGENTS.md как командного DSL. Метрика сообщений привязывается к фактической активной работе независимо от имени операции.

## Дополнение DDD-07

Обновлено: **2026-09-07T00:07:53+05:00**. RuntimeIdentity/OutputPolicy — чистые модели. WorkTools остаётся единым прикладным входом. CLI `runtime` только получает packet, вызывает adapter и общий presenter.

RuntimeRegistry владеет binding/cursor, не Task. HandoffCommands меняет жизненный цикл только через Task API и UoW. LocalHandoff — инфраструктурная последовательность Git/files с receipts; session release согласован с Task и receipt в одной короткой транзакции. Sprint читает подтверждённое release через Repository и вычисляет resumable как часть собственного overview.

Парсер не исполняет исходную команду повторно и не записывает бизнес-таблицы. Async worker создаёт лишь представления; Task verdict и Incident имеют разные источники. Разделение поддержано архитектурными тестами импорта и обхода, но они не заменяют осмотр произвольного будущего кода.


## DDD-07B — scoped Transfer
Обновлено: **2026-09-07T00:25:38+05:00**. Transfer владеет протоколом пакета, выбором владельцев, локальными receipts и location mapping. Он не выбирает следующий этап и не подтверждает evidence. Application TransferCommands зависит от порта, не SQLite/файлов. Rehydration существующего same-schema snapshot проходит через специальные Task/Sprint repository методы и валидируется существующей доменной моделью. Совпадающие owner IDs не перезаписываются. Короткая транзакция публикует весь выбранный набор, внешние файлы/Git подготавливаются заранее.

Тексты секций и исходные evidence неизменны; machine paths читаются через location adapter. Artifact ID основан на owner-relative location и переживает перенос. Runtime sessions/cursors источника не переносятся. Подробно: [transfer](transfer.md), [storage](storage-step07b.md).

## DDD-07C — 2026-09-07T01:17:28+05:00
- `modules/capabilities` и `modules/hook_transport`: pure contracts; нет subprocess, времени и filesystem.
- `application/CapabilityChecks`, `HookCommands`: пакетные сценарии через порты.
- `infrastructure/HookService`: composition с прежним WorkTools/InteractionStore; native hook не принимает и не завершает Task.
- `HookRegistry`: собственные operational таблицы, без SQL к tasks/sprints.
- `LocalProbeExecutor`/`StdioProbe`: реальные read-only наблюдения, не интерпретация пользовательского intent. Наличие inventory не выдаётся за успешный probe.
- Сохранение native hooks — отдельный конфигурационный effect; не меняет Codex trust и не выполняет скрыто приёмку Harness этапа.


## Accounting ownership — DDD-08

Updated: 2026-09-07T02:34:02+05:00.

Accounting owns raw usage, time cycles, benefit credits and explicit prior-result finding attribution, not Task/Sprint state. Domain is I/O-free; application uses the accounting port. SQLite mutations remain accounting-owned, external measurement executes outside the state lock. Task outcome precedes measuring benefit, never follows a fabricated metric. User-message ledger stays the source of message identity; no second counter duplicates it. Query projections may read other owners through existing query contracts.

No hidden numerical defaults, provider tariff assumptions or goal-name dispatch. Missing counters/instrument stay unavailable. Sources, causes, categories, selected sections, calendar and limits are explicit config.


# DDD-09 — границы каталога

Обновлено: **2026-09-07T07:34:23+05:00**.

Catalogue — библиотека подготовки определений, не владелец живого Task lifecycle.
TaskBlueprint и DataPublication не знают ОС, файлов и SQL. CatalogueCommands использует
порты шаблонов и прежний редактор. PlanningPublications координирует Task/Sprint/ActionRun
и repositories через один UoW; бизнес-статусы не присваиваются адаптерами.

Handler произвольного goal_type не создаётся. Все 98 узлов исполняются прежними семействами.
Пакет `resolutions` расширяет обычную работу тем же FeedbackBook; решение осмотра отдельно.
Publish читает сохранённый план и не принимает незаметно новый draft вместо осмотренного.
Собственные исходники/templates можно редактировать как разработку продукта; рабочие
process-files публикуются через инструментарий. Внешние fixtures/сценарии не являются ядром.


# Project setup boundary — 2026-09-07T14:15:55+05:00

`modules/projects` владеет project blueprint и ответами questionnaire. Application зависит
только от порта, не от pathlib/Git/SQLite. Infrastructure разрешает выбранные sources и
назначения, проверяет полную конфигурацию существующим load_config, выполняет локальные
readiness probes и публикует новый каталог. Это не universal file editor и не новый Task API.
Общая механика atomic_write и exclusive_lock переиспользована; все business значения settings
и выбранного шаблона явные. Этапы или имена целей не встроены в код project tool.


## Проверка пилота — 2026-09-07T15:04:48+05:00

В HARNESS-PILOT-02 не добавлены новые домены, таблицы или прикладной runner. Development-
супервизор использует существующий command executor. Пример continuation вызывает WorkTools,
не пишет SQL/config и не присваивает lifecycle-поля. Контроль источников включает tools.
Статус процесса и факт наличия XML отделены от содержательного итога проверок.
