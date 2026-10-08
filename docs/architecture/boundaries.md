# Границы ответственности: DDD-04B

Обновлено: **2026-10-04**. Срез **POISE-DDD-04B**.

| Владелец | Обязанность | Запрещено |
|---|---|---|
| Task | кандидатный результат, доказательства, transition, приёмка и rework | SQL/Git/команды |
| workflow/handlers | produce/inspect/revise/observe/check | goal-type branching, присваивание lifecycle |
| verification/CheckRegistry | точные команды, current revision/история, расписание, `source_under_test`, executable-классификация/покрытие и семантические конфликты | изобретение команд или разрешение filesystem paths |
| evidence | план, immutable observations/arguments/decisions, механическая достаточность | I/O, оценка истинности вместо агента |
| application/TaskCommands + EvidenceCommands | общий вход, авторизация позиции, короткая UoW | таблицы и внешние команды |
| SQLite repositories/UoW | запись с FK/optimistic version и внешний lock | сами принимать proof или переходить этап |
| runtime/execution/artifacts | реальные наблюдения Git/команд/files; publication и transport | прямые UPDATE Task/evidence state |
| Optional telemetry | неизменяемый `TelemetryEnvelope`, отдельные SQLite database/lock, асинхронная best-effort запись и partial coverage | брать authoritative Task DB lock, задерживать либо отменять результат WorkTools |

`EvidenceBook` используется композиционно; это не BaseTask и не отдельный движок каждого goal type.
Runner делегирует
чистым handler/domain, не держит lock во время subprocess. Generic observe/check отличаются способом
интерпретации
заданных наблюдений, не названием цели.

Правила происхождения, аргумента и осмотра остаются раздельными. `passed` — исход predicate,
`interpretable` —
распознанность наблюдения, `accepted/rejected` — последующий смысловой осмотр. Их нельзя сворачивать
в один status.

`CheckRegistry` владеет чистым контрактом provenance и сравнением методов. Runtime владеет
разрешением repository-relative binding против текущего task worktree, проверкой границ и
формированием digest/receipt. Execution adapter получает уже связанный cwd/environment и не
угадывает язык, layout проекта или источник импорта. Evidence хранит наблюдение и provenance,
но не исправляет неверный method после запуска.

`CheckRegistry` также владеет атомарными `add`/`replace`/`reschedule`/`remove`, optimistic
revision и idempotent request digest текущей проекции. Task разрешает такой пакет только на
этапе с секцией `test_registry` и проверяет полноту текущего GREEN executable-покрытия только
на явно выведенных test-inspection границах. Immutable snapshots и Evidence receipts остаются
историей, а SQLite repository сохраняет и восстанавливает current registry state без их
перезаписи. Классификация `executable_obligations` задаётся явно при создании и каждой
мутации; при восстановлении единственным текущим источником является валидированный registry
state, без migration или fallback к immutable creation metadata.

Runtime строит invocation и `execution_key` для verify и failed-check rework одним
каноническим путём; отдельная identity без source provenance не является поддержанным
вариантом. Evidence repository владеет полной неизменяемой записью command receipt, а batch в
Task лишь ссылается на то же наблюдение. Перед reuse или failed-check rework runtime требует
точного равенства этих записей, привязки к текущим invocation/tree и целостности output.
Task применяет переход только после этих проверок. Это разделение не создаёт второго writer,
совместимого ключа или миграции evidence.

Общий output/async parser слой и runtime integrations ещё относятся к будущим срезам. В DDD-04 raw
capture синхронный,
адресуемый и durable у задачи. Не объявлять новый слой полностью реализованным на основании формы
EvidenceBook.


## WorkOwnership

Обновлено: 2026-09-13. Реализованный срез Task 0076,
[HR-037](../governance/requirements.md#HR-037).

`OwnershipState`/`OwnershipSet` в `modules/ownership` владеют чистыми правилами полного
набора, замены, эксклюзивности и зависимого освобождения. `OwnershipCommands` координирует
preflight, liveness и повторную проверку в UoW. SQLite adapter сохраняет `tasks.claimed_by`
(владелец Task) и `sessions.task_id` (независимая привязка worktree) одной транзакцией под
существующим writer lock и `BEGIN IMMEDIATE`. Частичный unique index на непустой
`tasks.claimed_by` физически ограничивает сессию одной Task; `sessions.id` ограничивает
сессию одним worktree, а уникальный непустой `sessions.task_id` — worktree одной сессией.
Нельзя выводить владение Task только из `sessions.task_id`.
При неоднозначных v12 claims установка индексов откладывается, но весь проект
не блокируется: `OwnershipCommands` и его SQLite adapter предоставляют
[локальное публичное
согласование](../workflows/batch-work.md#восстановление-неоднозначного-владения)
по точному снимку связанного компонента. Task repository сохраняет версионное
освобождение claimant; ownership repository — bindings и receipt той же UoW.
Наблюдение до транзакции не разрешает запись без проверки актуального полного набора.

Process snapshot владеет обязательным boolean `worktree_required`; runtime, SprintWork
и handoff компонуют API владельца, не реализуют отдельные алгоритмы захвата. Native hook
registry предоставляет подтверждённые события liveness: `SessionEnd` означает DEAD,
незавершённая наблюдаемая сессия — LIVE, недостающая запись — UNCERTAIN. Runtime без
наблюдателя не получает скрытого разрешения на перехват. Сами claims не выполняют Git,
filesystem cleanup, chdir или смену launch root.

Для Task без worktree runtime использует настроенный repository как среду проверок,
сохраняет закреплённую базу и результат без ветки. Handoff receipt имеет null worktree
и bundle; resume проверяет сохранённые факты, не создаёт искусственное дерево.
Accounting не измеряет несуществующий Git delta, но продолжает измерять объявленные
полезные секции относительно их baseline. Это не изменение Clock или полноты телеметрии.

Существующие terminal cleanup (0048) и result integration (0057) остаются у своих
владельцев; освобождение claims не удаляет worktree и не публикует коммиты. Интеграция
принятого результата по-прежнему использует текущую установку и существующее дерево Task.
Атомарность SQL claims не обещает транзакцию, охватывающую Git или внешние команды.

## Task process snapshot migration

Явной миграцией сохранённых Task process snapshots владеет Task process persistence
boundary. Чистый `ProcessSnapshotMigration` проверяет авторизованный набор и строит
изменения из неизменяемого `goal_type` и настроенных process definitions. Application
service принимает один декларативный запрос. SQLite adapter сверяет названную публичную
резервную копию, повторно проверяет состояние под writer lock, записывает весь набор и
audit event одной транзакцией. CLI только декодирует запрос и представляет результат.
Существующий claim является сохраняемым состоянием Task, а не причиной отказа: migration
boundary не получает ownership authority. Полное сравнение snapshot до транзакции
отклоняет создание, снятие или замену claim во время операции.

Эта граница не является универсальным редактором Task metadata и не добавляет
compatibility read. Одинаковый `request_id` с тем же intent возвращает проверенный receipt;
изменённый intent, повреждённый audit или drift отклоняются. Время audit поступает через
обязательный `Clock`; production composition явно предоставляет `SystemClock`.

Schema `task-process-migration-2` имеет отдельный compile-time scope ровно из Task `0082` и
не меняет авторизованный набор schema 1. Она сохраняет опубликованный контракт первой операции:
domain добавляет к legacy process только отсутствующий `worktree_required`, а её старый receipt
остаётся replayable. Корректирующая schema `task-process-migration-3` имеет тот же точный scope,
сохраняет уже добавленный совпадающий boolean и строит отсутствующий stage contract каждого route
stage: scope берётся из сохранённого stage, gates — из сохранённых content requirements по phase.
Storage
меняет только process и contract snapshots внутри metadata; Task version, lifecycle/result/
content/evidence/ownership/Git поля и released handoff остаются прежними. После commit
работоспособность доказывается обычным reviewer bootstrap сохранённой verified Task.

## Пакетный декларативный вход

Обновлено: **2026-09-06T22:58:21+05:00**. Task/config/artifact/interaction-вход реализован в
DDD-04A/04B; Sprint
использует тот же вход в DDD-05.

| Владелец | Новая обязанность | Что остаётся вне него |
|---|---|---|
| GoalTypeDefinition / GoalConfigCommands | Кандидат независимого pack, целостность итоговых ссылок, явные шаблоны, создание/изменение конфигурации одним пакетом | Доступ к чужой Task DB, миграции активных задач |
| BatchCommandPipeline | Один envelope, binding, request identity, пакетная validation, общий отчёт и журнал | Бизнес-решения всех доменов, произвольный raw update |
| Task/Sprint commands | Пакетные содержательные изменения через владельца и UoW | Ручное присваивание lifecycle из CLI |
| ArtifactFactory/ArtifactRegistry | Генерация из шаблона/содержания, path-only регистрация, roots и техническая идентичность | Изобретение содержания за агента, произвольная запись вне roots |
| InteractionLedger (accounting) | Различимые message events и единственный основной binding, дедупликация | Считать tool calls за сообщения, оценивать токены по сообщениям |
| Runtime/CLI adapter | Передать непосредственный payload и наблюдённый user event | Требовать заранее ручной JSON-файл или выдавать reported events за полный поток |
| SQLite/filesystem adapters | Сохранить валидный кандидат, staging и receipt, согласованная активация | Бизнес-валидация истинности аргумента |

Реализация общего pipeline не подменяет API Task/Sprint. Все конфиги AI poise остаются в AI poise;
generated
project/process файлы не редактируются агентом напрямую. Код приложения, тесты и документация
приложения — через
нативные инструменты согласно правилам codebase. Полный контракт:
[declarative-tools](declarative-tools.md).

## DDD-04A — 2026-09-06T20:26:43+05:00

Новый доменный владелец `modules/goal_config` использует RouteDefinition, SectionRule и
ContentPolicy без I/O.
Application `GoalConfigCommands` делегирует файл/DB через ports; infrastructure размещена в
`infrastructure/goal_config.py`. CLI adapter `interfaces/goal_config.py` принимает stdin, формирует
bounded response и
не обходит доменную validation. Общий `infrastructure/locking.py` используется двумя хранилищами.

Текущий Task process snapshot не зависит от актуальной копии pack; его project execution config
остаётся отдельной
границей. Версия store изменена явно. Новый editor не наследует чужой goal-type процесс: явный
template копируется в
независимое определение.

Список [кандидатов правил](../governance/rule-candidates/rule-candidates.md) не является новым
источником policy до
решения пользователя.

## DDD-04B — новые владельцы и зависимости
Обновлено: **2026-09-06T22:58:21+05:00**.

WorkTools — application facade, не новый агрегат. Task остаётся владельцем секций, evidence и
lifecycle. ArtifactPlan —
чистая модель генерации; FileArtifactFactory — инфраструктура; существующий ArtifactRegistry —
регистрация owner-scoped
файлов. InteractionLedger владеет identity/user-event rules, SQLite InteractionStore — таблицами
observations и
bindings.

`interfaces/work.py` читает stdin и оформляет bounded JSON/file response. Он не пишет таблицы Task.
Application/work не
импортирует SQL/filesystem. Composition root Runtime подключает порты. Чтение и запись
пользовательских событий не
поручаются LLM SQL-командами. Ledger transaction независима от Task verification: отрицательный test
не отменяет
реальное сообщение.

Общий API не реализует 13 разных engines. DDD-04A editor и DDD-04B work entry остаются отдельными
предметными
сценариями. Конфигурационные шаблоны и артефакты имеют разные contracts; никакого универсального
произвольного
`write_file` для служебных данных.


## DDD-05 — 2026-09-11T16:25:00+05:00
Sprint владеет планом/графом/решениями; Task владеет доступностью, началом и состоянием работы.
Published Sprint создаёт
Task через общий доменный builder и TaskRepository в одной UoW. Клиент не подменяет исходные
шаблоны/таблицы рабочими
JSON patch.

`modules/sprints` и `modules/tasks/definition` не имеют I/O. `application/sprints` не импортирует
SQL/filesystem.
`infrastructure/sprint_work` вычисляет eligibility, формирует `result_provenance`, наблюдает текущий
commit явно
настроенного Git `base_ref` для новой Task и делегирует Task API. Result commits предшественников
остаются provenance и
не выбирают branch base. Точный наблюдённый base SHA сохраняется в reservation до Task start;
recovery использует его
повторно, а resume/handoff существующего worktree не наблюдают ref заново и не пересоздают
workspace. База сериализует
короткие записи; проверки и локальные commits параллельных worktrees не держат общий DB lock. Git
push запрещён.

Восстановлением ошибочной незавершённой Task владеет общий Task lifecycle. Application-команда
`restart` проверяет optimistic version, ownership и terminal boundary; Task domain строит newborn
с тем же ID, Sprint membership, process/contract snapshot и append-only restart audit. Task и
execution repositories одной UoW сохраняют смену состояния и сбрасывают только текущие attempts,
publication, pending и last_report, не изменяя worktree/branch/base/entry tree и Git WIP.

Pending external outcome блокирует restart до выполнения его явного recovery protocol. Replay
receipt проверяется до мутации; conflicting intent отклоняется. `ready` повторно валидирует
сохранённый process, а `start` возобновляет прежнее execution/worktree. Public Sprint correction
action `replace_task` удалён. Типизированная проекция замен Task удалена. Прежние решения читаются
как
неизменяемые revision layers и аудит явной миграции, не как текущий процесс.

### Публичный обзор работ — 2026-09-11

`WorkTools` композиционно строит `work_overview` через два узких read-порта: Sprint предоставляет
идентификаторы
опубликованного реестра и полные dependency-aware overview, Task предоставляет summary только
самостоятельных задач.
Application-слой проверяет разные словари статусов, применяет независимые фильтры к готовым
owner-проекциям и собирает
транспортный объект `{"sprints": ..., "standalone_tasks": ...}`.

Этот query не становится новым владельцем lifecycle, membership или readiness. Sprint по-прежнему
определяет факт
публикации, состояние графа, `eligible`/`blocked` и доступность result objects; Task определяет
принадлежность и
lifecycle standalone Task. SQLite-адаптеры реализуют параметризованные project-scoped reads, но
клиент не получает
SQL-интерфейс. Последовательное чтение двух read-models не объявляется единым cross-domain snapshot.
Публичный пакет и
ограничения результата описаны в [batch
work](../workflows/batch-work.md#обзор-опубликованных-спринтов-и-самостоятельных-задач), семантика
Sprint — в [Sprint
API](../workflows/sprints.md#реестр-опубликованных-sprint).

## DDD-06: внешние планы
Владелец плана — Actions: PlanSpec/ActionRun/Publication. Task остаётся единственным владельцем
verified/accepted и
переходов. Application PlanCommands связывает их коротким UoW; Git/command адаптер не выполняет SQL.
Репозиторий actions
сохраняет run+event, а не весь Task обходным путём.

apply_plan/publish — обработчики общего runner. Sequential merge и command/probe различаются
адаптерной стратегией
plan.kind, не названием business goal. Источники/команды явные; нет автоматического чтения AGENTS.md
как командного DSL.
Метрика сообщений привязывается к фактической активной работе независимо от имени операции.

### Привязка действия к циклу Task

Обновлено: **2026-10-08**.

Task владеет идентичностью своего цикла. Чистый `TaskLifecycle`
проверяет текущую
версию, возрастающие `restart_history.from_version` и сохранённые
native
`restarted_newborn` events. `TaskRepository.action_lifecycle` читает
эти данные
через ту же SQLite connection внутри UoW. Начальный цикл имеет границу
`None`,
реальный перезапуск из версии ноль — границу `0`. Неверный тип,
недопустимый порядок,
граница не раньше текущей версии или противоречие сохранённому native
audit
отклоняются с `Invalid Task lifecycle audit: ` и конкретной причиной.
Отсутствие
следов не разрешает придумывать перезапуск.

Actions владеет `ActionBinding`: Task ID, `TaskLifecycle`, исходный
stage ID и
положительная логическая iteration. `ActionRun` удерживает эту
привязку только в
памяти; его сохранённый payload по-прежнему содержит ровно `plan`,
`cursor`,
`status`, `steps`, `before`, `current`, `version`, `attempts`. Граница
не выводится
из сессии, текущей Task version, плана или результата.

SQLite action repository единожды определяет физический ключ для run и
event:
начальный цикл использует `(task_id, raw_stage, iteration)`,
перезапущенный —
`(task_id, compact_JSON([from_version, raw_stage]), -iteration)`. JSON
сохраняет
Unicode и не содержит пробелов-разделителей. Знак iteration отделяет
все начальные
ключи от всех перезапущенных, включая произвольные имена этапов. Это
внутреннее
представление прежней схемы, не поле публичного пакета и не миграция
старых строк.
`load`, `create`, `save` используют один mapper; прежние строки не
переименовываются.
Optimistic version check и запись run вместе с event остаются одной
транзакцией.

`PlanCommands.obtain` и `publish_local` после обычных проверок
владельца и текущего
этапа связывают новый либо восстановленный run с актуальной привязкой.
`update`
проверяет равенство старой, текущей и новой привязок, неизменность
плана и затем
сохраняет run с optimistic version. `snapshot` читает текущее действие
через тот же
репозиторий. `RuntimePlanActions` исполняет внешние эффекты вне UoW;
`PlanningPublications` использует этот репозиторий при атомарной
публикации
дочерних владельцев. Отдельного resolver или второго писателя
lifecycle у
потребителей нет. Публичное поведение и восстановление описаны в
[контракте действий](../workflows/actions.md#текущий-цикл-task-и-неизменность-плана).

## Дополнение DDD-07

Обновлено: **2026-09-07T00:07:53+05:00**. RuntimeIdentity/OutputPolicy — чистые модели. WorkTools
остаётся единым
прикладным входом. CLI `runtime` только получает packet, вызывает adapter и общий presenter.

RuntimeRegistry владеет binding/cursor, не Task. HandoffCommands меняет жизненный цикл только через
Task API и UoW.
LocalHandoff — инфраструктурная последовательность Git/files с receipts; session release согласован
с Task и receipt в
одной короткой транзакции. Sprint читает подтверждённое release через Repository и вычисляет
resumable как часть
собственного overview.

Парсер не исполняет исходную команду повторно и не записывает бизнес-таблицы. Async worker создаёт
лишь представления;
Task verdict и Incident имеют разные источники. Разделение поддержано архитектурными тестами импорта
и обхода, но они не
заменяют осмотр произвольного будущего кода.


## DDD-07B — scoped Transfer
Обновлено: **2026-09-07T00:25:38+05:00**. Transfer владеет протоколом пакета, выбором владельцев,
локальными receipts и
location mapping. Он не выбирает следующий этап и не подтверждает evidence. Application
TransferCommands зависит от
порта, не SQLite/файлов. Rehydration существующего same-schema snapshot проходит через специальные
Task/Sprint
repository методы и валидируется существующей доменной моделью. Совпадающие owner IDs не
перезаписываются. Короткая
транзакция публикует весь выбранный набор, внешние файлы/Git подготавливаются заранее.

Тексты секций и исходные evidence неизменны; machine paths читаются через location adapter. Artifact
ID основан на
owner-relative location и переживает перенос. Runtime sessions/cursors источника не переносятся.
Подробно:
[transfer](../workflows/transfer.md) и [библиотечный API](library-api.md).

## DDD-07C — 2026-09-07T01:17:28+05:00
- `modules/capabilities` и `modules/hook_transport`: pure contracts; нет subprocess, времени и
  filesystem.
- `application/CapabilityChecks`, `HookCommands`: пакетные сценарии через порты.
- `infrastructure/HookService`: composition с прежним WorkTools/InteractionStore; native hook не
  принимает и не
  завершает Task.
- `HookRegistry`: собственные operational таблицы, без SQL к tasks/sprints.
- `LocalProbeExecutor`/`StdioProbe`: реальные read-only наблюдения, не интерпретация
  пользовательского intent. Наличие
  inventory не выдаётся за успешный probe.
- Сохранение native hooks — отдельный конфигурационный effect; не меняет Codex trust и не выполняет
  скрыто приёмку AI
  poise этапа.

### session-scoped source resolution

Уточнено **2026-09-13**: session-scoped launcher исполняет каждый work-пакет только из явно
настроенного `installation source`. `Task worktree` является предметом разработки и проверок,
но не поставщиком исполняемого AI poise runtime. Иначе старая или изменяемая вместе с продуктом
копия
AI poise должна была прочитать текущую конфигурацию и Task DB, что создавало циклическую
зависимость: обновление AI poise runtime могло заблокировать собственные `bootstrap`, `verify` и
`handoff` ещё до выполнения публичной операции.

`native binding` остаётся владельцем session/message provenance. Launcher не записывает
выбранный source в binding или Task DB, не запускает дочерний `WorkTools` из Task worktree и
не имеет fallback-маршрута. Параллельные сессии используют одну установленную реализацию,
собственные bindings и независимые Task ownership; их worktrees не становятся runtime-кодом.
Все операции используют валидную текущую конфигурацию без общего `config_hash` gate, как
предусмотрено задачей 0026. Task-owned process, contract и verification snapshots сохраняются;
хеш остаётся диагностическим provenance. Lifecycle ownership и revision guards не меняются.

Адресный `terminal inspection snapshot` для `completed` и `cancelled` также
проходит через installation source. Терминальная Task не обязана иметь доступную worktree;
чтение снимка не меняет её lifecycle.

Task остаётся владельцем terminal status и освобождённого claim. Runtime составляет проекцию
из сохранённых Task context, content, evidence и audit history через существующие owner/query
API, после чего гарантирует отсутствие current-task binding. Активные `active`, `verified` и
`accepted` ownership guards проверяются до такого чтения. Пустая evidence-проекция
аварийно отменённой Task допустима и не интерпретируется как незавершённая проверка.


## Accounting ownership — DDD-08

Updated: 2026-09-12T00:25:00+05:00.

Accounting owns raw usage, time cycles, benefit credits and explicit prior-result finding
attribution, not Task/Sprint
state. Domain is I/O-free; application uses the accounting port. SQLite mutations remain
accounting-owned, external
measurement executes outside the state lock. Task outcome precedes measuring benefit, never follows
a fabricated metric.
User-message ledger stays the source of message identity; no second counter duplicates it. Query
projections may read
other owners through existing query contracts.

`Clock` is the accounting observation port. `Poise` and `RuntimeAccounting` require it at
composition; production CLI,
native hook and runtime adapters explicitly supply `SystemClock`, while tests supply deterministic
adapters. The domain
observation type carries audit UTC, a monotonic value and its comparison domain; filesystem and
system-clock access
remain infrastructure responsibilities. Accounting alone validates persisted clock continuity and
stores
measured/unmeasured cycle state. Query projection may combine audit start with a known monotonic
duration for calendar
allocation, but no layer derives elapsed time from wall-clock subtraction.

No hidden numerical defaults, provider tariff assumptions or goal-name dispatch. Missing
counters/instrument stay
unavailable. Sources, causes, categories, selected sections, calendar and limits are explicit
config.


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


# Project setup boundary — 2026-09-14T00:00:00+05:00

`modules/projects` владеет project blueprint и ответами questionnaire. Application зависит
только от порта, не от pathlib/Git/SQLite. Infrastructure разрешает выбранные sources и
назначения, проверяет полную конфигурацию существующим load_config, выполняет локальные
readiness probes и публикует новый каталог. Это не universal file editor и не новый Task API.
Общая механика atomic_write и exclusive_lock переиспользована; все business значения settings
и выбранного шаблона явные. Этапы или имена целей не встроены в код project tool.

`ProjectSettings.registry` выбирает единственный authoritative registry настроенных проектов.
`FileProjectSetup` владеет его строгой схемой, регистрацией после публикации, проверкой
идентичности и exact-replay recovery; создание и `project-init` не имеют отдельного writer.
Read-only `FileProjectSetup.list` повторно использует общий `load_config`, отделяет
missing/invalid записи от пригодных и не исправляет registry или manifests. Application и CLI
проходят через существующие `ProjectSetupPort` и `ProjectCommands`; отдельного discovery
сервиса или команды управления проектами нет.


## Live project configuration boundary — 2026-09-12

`ProjectConfigCommands` — единственный application route для revision-aware изменения
действующего проекта. Он проверяет форму bounded-пакета и зависит от
`ProjectConfigUpdatePort`; filesystem, SQL и subprocess в application layer не входят.
`FileProjectConfigUpdate` владеет сборкой полного кандидата, общей `load_config` validation,
публикацией, component digests, pending/durable receipts и recovery. CLI и composition только
передают пакет этому route и не редактируют managed файлы самостоятельно.

`GoalTypeDefinition` остаётся владельцем process candidate. Project updater координирует
несколько независимых process snapshots, но не вводит второй редактор их внутренней модели.
Generic manifest edits не могут менять `project`, `schema`, `processes` или `paths`:
перемещение state принадлежит отдельному relocation protocol.

Task остаётся владельцем process snapshot и lifecycle. Обновление живого process не меняет
уже созданную Task. Manifest/state mutation требует quiescence; project setup lock и lock
исходного state удерживаются от проверки до durable receipt, а при relocation до переключения
manifest добавляется lock destination. Поэтому Task creation/claim не проходит между
проверкой активной работы и публикацией. Перенос не удаляет source до подтверждённой копии и
переключения manifest; overlapping roots и отсутствующий source отклоняются. Quiescence
использует terminal-набор Task `completed`/`cancelled`; исходный статус старых записей сохраняется
только
в журнале явной миграции, не как исполняемый alias. Детерминированный relocation staging
принадлежит операции только после проверки его отсутствия и публикации matching pending
receipt: первый запрос не удаляет уже существующий staging, а очистка частичной копии
разрешена только exact replay того же request digest.


## Task identity allocation — 2026-09-11T06:55:21+05:00

`modules/tasks/allocation` владеет чистой моделью numeric namespace, display width,
progression, creation intent и request digest. `TaskCommands` координирует allocation,
creation validation, Task aggregate и execution reservation в одной короткой UoW.
`TaskRepository.allocate` — единственная граница, которая сопоставляет policy с уже занятыми
Task/Sprint IDs; runtime, planning и Sprint adapters не вычисляют номер и не пробуют Git/paths.

Пакетные application services передают владельцу полный набор зарезервированных explicit IDs,
поскольку только они знают ещё не опубликованный состав Task/Sprint intent. Это не второй
allocator: они не обходят candidates и не повторяют запросы с увеличенным номером. SQLite
adapter сериализует allocation и insert прежним внешним lock; отдельный sequence/counter и
новый lifecycle writer не вводятся.

Git и filesystem начинаются после durable `worktree_setup` reservation. Runtime adapter может
только сверить сохранённые task ID, branch, base и worktree и завершить совпавшее состояние;
он не выбирает другой ID, не удаляет конфликтующее внешнее состояние и не выполняет reset.
Replay identity имеет project scope и не равен session binding: другая сессия может получить
тот же allocation receipt, но не крадёт существующий claim.


## Граница продвижения по этапам

`Task` остаётся единственным владельцем lifecycle и route transitions. Чистый
`progression_step` определяет достижение цели, необходимость реальной работы и границу ролей;
`TaskCommands` координирует persisted progression intent, проверку `pending`, preflight
следующего stage, отдельный `Task.progress_stage` без пользовательской приёмки и сохранение в одной
UoW. `WorkTools`
только разбирает
точный пакет `advance`, runtime наблюдает Git tree и формирует публичный ответ.

Цель и request identity сохраняются событиями существующего Task journal; отдельная таблица,
reader migration и второй lifecycle не нужны. До начала progression неизвестная либо
недостижимая цель, чужой owner и pending outcome не оставляют записи. После начала непройденный
entry gate сохраняет цель для correction/restart, но не меняет Task, execution и ownership.
Достигнутая цель идемпотентна.

Обычный переход записывает `stage_progressed`, сохраняет предыдущий report как `verified` и
никогда не пишет `user_accept*`. Вход в `publish` этим путём запрещён: после необходимой смены
роли `advance` возвращает `user_acceptance_required`, а этап открывается только отдельным
публичным решением пользователя.

Граница `executor`/`reviewer` выводится только из handler сохранённого route: `inspect` принадлежит
reviewer, остальные семейства — executor. Переход через неё разрешён лишь после public handoff,
release и bootstrap другой сессии; операция не отправляет сообщение и не исполняет работу этапа.

## DDD-10 — интеграция принятого результата

Обновлено: **2026-10-04**.

`ResultIntegration` владеет неизменным intent, состояниями merge/conflict/cleanup, receipts и
idempotent replay. Task
остаётся единственным владельцем verified/accepted/completed lifecycle и содержательного результата:
операция интеграции
читает окончательный Task result, но не меняет его статус, секции или evidence. Sprint продолжает
вычислять состояние из
Task и не выполняет скрытый auto-merge.

`ResultIntegrationCommands` — прикладная граница одного декларативного пакета.
`RuntimeResultIntegration` реализует
Git-наблюдения и эффекты, а сохранение выполняет через execution repository. WorkTools только
маршрутизирует `integrate`
и `show integration`; domain не импортирует filesystem, subprocess или SQLite.

`IntegrationIntent` до эффекта фиксирует task ID, request ID, accepted source commit, наблюдавшийся
target commit, пользовательскую `authorization` и отдельное полное `commit_message`. Domain
проверяет
обязательность и допустимый тип сообщения, сохраняет исходный текст в идентичности, storage и
replay.
Runtime проверяет полное сообщение по настроенному `git.commit_pattern` до Git и загрузки
сохранённой
интеграции. Одна реализация создания коммита передаёт этот текст с `--cleanup=verbatim` при обычном
слиянии, разрешении конфликтов и продолжении `MERGE_HEAD`; разрешение остаётся основанием уборки.
Точный внешний контракт принадлежит
[пакетной интеграции](../workflows/batch-work.md#разрешение-и-сообщение-интеграционного-коммита).
Adapter проверяет окончательный Task result и чистый task worktree. Существующие task
branch/worktree и
task-scoped temporary-backup directory сохраняются до первого Git-эффекта; отдельные integration
branch/worktree не
создаются. Новый вызов с тем же request ID и изменённым intent отклоняется.

Внешняя блокировка БД не удерживается во время Git. `IntegrationRun` сохраняет accepted commit,
последний включённый
target, mutable integration head, конфликты и решения, check receipts, publication receipt, cleanup
outcomes и историю
фаз. Незавершённый merge восстанавливается по task worktree, `MERGE_HEAD` и conflict set. Разрешение
принимает ровно
один rationale для каждого сохранённого conflict path. Точная legacy-форма blocked-запроса
`integrate-0048-1` имеет узкий adapter, сохраняющий старую историю и accepted commit, только если
сообщение уже записано. Runtime сравнивает входящую идентичность до сохранения восстановления.
Отсутствующее сообщение даёт явный отказ без Git или изменения истории; новый вызов не дописывает
его. Остальные неизвестные формы отклоняются. Это ограничение восстановления, не новая миграция.

На готовом integration head в task worktree запускаются текущие produced-result GREEN методы
реестра: `green_stages` и
`change_surface` непусты. Текущий терминальный stage не фильтрует этот набор; RED и baseline-only
методы исключены.
Публикация сериализуется общим lock по target. Под lock adapter снова читает target ref: drift
обновляет ту же task
branch и повторяет проверки. Стабильный candidate публикуется в основном checkout только `git merge
--ff-only
<task-branch>`; crash после эффекта распознаётся по текущему ref и не повторяет публикацию. При
отказе сохраняется
before/after fingerprint `HEAD`, binding, index, tracked/untracked content, types, modes и operation
state.

Отказ проверки рабочего дерева при сверке сохранённого доказательства в фазе `publishing`
проходит через существующий доменный переход
`publication_blocked`: публикация сохраняется как заблокированная, чужой WIP остаётся на месте.
Та же ошибка при повторе уже `publication_failed` отклоняет вызов без новой записи состояния.
Неполное доказательство продолжает прежний путь повторных проверок; проверки чистоты, ancestry,
receipt и раннего retry после `checks_failed` не обходятся.

Только после подтверждённой публикации начинается монотонная уборка: task worktree removal
предшествует task branch
deletion, scoped temporary directory удаляется последним. Текущий target обязан содержать
integration head и accepted
commit; descendant допускается, переписанная история сохраняет recovery state. Ref удаляется
compare-and-delete. Чужие
worktrees и operator/deliverable/recovery backups не затрагиваются. Hook route относит `integrate` к
installation
source, поэтому устаревший accepted код не управляет завершителем. Состояние остаётся в Task
execution snapshot; новая
таблица не требуется.

## Terminal cleanup ресурсов Task — 2026-09-12

`CleanupRun` владеет неизменным cleanup intent, явным `CommitDisposition`, точным набором
`TaskOwnedResource`, прогрессом, blocker и replay-history. `TaskResourceCleanup` координирует
один общий путь standalone cancellation, Sprint cancellation/supersession и cleanup после
интеграции. `RuntimeTaskResourceCleanup` только наблюдает и изменяет Git/filesystem, проверяет
ownership и хранит registry временных ресурсов; WorkTools и Sprint/ResultIntegration являются
composition/transport, а не вторыми владельцами уборки.

Terminal lifecycle, commit disposition, publication authorization и resource cleanup — четыре
разных решения. Публичный cleanup принимает только `preserved` или `discard_authorized`;
`integrated` появляется только внутри подтверждённого integration lifecycle, а `no_resources`
выводится владельцем из наблюдаемого пустого набора. Ни cancellation, ни supersession не могут
неявно публиковать или уничтожать последнюю ссылку на уникальный commit.

Ownership задаётся сохранёнными identity: точный worktree и его ветка/commit, а также только
зарегистрированные temporary/temporary_backup пути под task-scoped runtime root и их digest.
Основной checkout, чужой WIP, durable artifacts/history и operator backups находятся вне этой
границы. Worktree удаляется до ветки; preserved commit получает проверенный durable bundle до
удаления ссылок. Dirty worktree требует явного clean checkpoint в той же ветке и только как
fast-forward terminal commit либо восстановления прежнего дерева вне cleanup.

Cleanup state хранится в существующем Task execution snapshot. Каждый внешний эффект следует
за сохранённым intent и завершается compare-and-save прогрессом; повтор продолжает только
оставшиеся ресурсы. Existing terminal Task без state инициализируется явной cleanup-командой,
без reader migration, новой таблицы или fallback. Изменение identity, commit, digest или версии
останавливает операцию; force-delete, push и отдельный lifecycle writer запрещены.


## Проверка пилота — 2026-09-07T15:04:48+05:00

В POISE-PILOT-02 не добавлены новые домены, таблицы или прикладной runner. Development-
супервизор использует существующий command executor. Пример continuation вызывает WorkTools,
не пишет SQL/config и не присваивает lifecycle-поля. Контроль источников включает tools.
Статус процесса и факт наличия XML отделены от содержательного итога проверок.
`CheckRegistry` владеет структурой `verification_plan` и сопоставляет
`change_surface` с route-путями. `RouteDefinition` предоставляет настроенные
`allowed_paths`; отдельного планировщика или анализа исходников теста нет.


## Метаданные skills (C005.1)

Обновлено: 2026-09-15. `modules/skills` владеет неизменяемыми дескрипторами навыков.
Infrastructure проверяет явно выбранный каталог и пути, но не читает тела инструкций.
Task/Sprint продолжают владеть готовностью и жизненным циклом; чистый владелец
FocusedDecomposition потребляет классификацию, а не поддерживает второй inventory.
Контракт и отображение классов: [каталог skills](../configuration/skill-catalog.md).


## Проверенное повторное использование семейного результата

`DuplicateReuseCommands` координирует существующие владельцы Task, Sprint, ownership,
CheckAttempts и handoff. `DuplicateReuseWorkspace` наблюдает Git/файлы и вызывает тот же
runner. Domain Task хранит собственный кандидат/проверку в существующем workflow JSON
и выполняет отдельные переходы `duplicate_reuse_prepared`, `duplicate_reuse_verified`,
`duplicate_reuse_accepted`; обычные этапы и чужие evidence не имитируются. Новой таблицы,
миграции, глобальной блокировки или второго движка проверок нет. Нормальная submission
identity неизменна: общий `check_candidate_digest` выбирает её либо собственный reuse-кандидат.
См. [публичный контракт](../workflows/sprints.md#готовый-семейный-результат-и-база-ветки).

### Доставка артефактов при reuse (2026-09-19)

`DuplicateReuseCommands` выбирает зарегистрированный набор принятого семейного результата и
координирует локальное подтверждение через существующий Task UoW. `DuplicateReuseWorkspace`
получает корни владельцев, читает и проверяет файлы; artifact-часть ContentPolicy оценивает
чистый метод Task `assess_artifact_delivery`, без фиктивной записи content/trace прошлых этапов.
`FileArtifactFactory.plan_registered_copy` выполняет preflight без записи, `copy_registered`
переиспользует бинарную публикацию `recover_registered`, но создаёт идентичности нового
владельца, не перепривязывает исходную регистрацию. `SqliteArtifactRepository.link_reused`
отклоняет конфликт зарегистрированной идентичности и фиксирует только локальные ссылки в
той же UoW, что `reuse_verified`. Файлы и SQLite не образуют общей транзакции: частичные
идентичные файлы сохраняются для точного повтора, но не объявляются принятым результатом.


## Сохранность комплекта приёмки

Обновлено: **2026-10-04**.

| Владелец | Ответственность |
|---|---|
| `verification/ArtifactInputs` | Чистая явная декларация сохранённых входов и связывания окружения. |
| `verification/AcceptanceManifest` | Точная схема и конкретные типы реального манифеста, затем сравнение с настоящим receipt, кандидатом и полным inventory. |
| `application/AcceptanceRetention` и `AcceptanceRetentionPort` | Композиция предварительной проверки, входов, proof, публикации и повторного чтения без собственной Task lifecycle. |
| `RuntimeAcceptanceRetention` | Наблюдение фактических постоянных байтов, digest, registry и манифеста через существующих владельцев. |
| `FileArtifactFactory` | Один механизм наблюдения назначения файла/каталога: configured prefix, корень, родители, отсутствие links, конечный тип; действительная неизменяемая публикация и её lock. |
| `RuntimeResultIntegration` | Набор текущих методов, предварительная проверка всего набора до эффектов кандидата и первого потребителя, сохранение terminal replay и очистка. |
| Task и application UoW | Владение, transitions, результаты и приёмка; файловая публикация не объявляется частью SQLite-транзакции. |

Поток: декларация → наблюдение каталога и точных входов → runner → настоящий
receipt и захваченные доказательства → публикация → чтение фактических байтов и
digest → доменная схема/типы/соответствие → исходная verified-доставка. Обратная
проверка каждого поля ведёт к исходному принятому файлу, объявленному производителю,
реальным байтам результата, receipt и точному commit/tree/definition метода.

Filesystem-правило остаётся у владельца артефактов; consumers используют тот же
узкий порт. Приложение не создаёт второй валидатор путей, domain не читает диск,
а интеграция сохраняет существующего владельца выбора проверок. Каталог манифеста
повторно наблюдается при публикации, реальный файл проверяется под существующим
lock. Это проверка известных границ и точных байтов при штатном жизненном цикле;
позднее произвольное внешнее изменение остаётся диагностируемым отказом.

Публичные декларации и восстановление имеют одного
[канонического владельца](../workflows/evidence.md#сохраняемые-входы-и-приёмочный-манифест).
