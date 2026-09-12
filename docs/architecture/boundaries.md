# Границы ответственности: DDD-04B

Обновлено: **2026-09-12T09:06:11+05:00**. Срез **POISE-DDD-04B**.

| Владелец | Обязанность | Запрещено |
|---|---|---|
| Task | кандидатный результат, доказательства, transition, приёмка и rework | SQL/Git/команды |
| workflow/handlers | produce/inspect/revise/observe/check | goal-type branching, присваивание lifecycle |
| verification/CheckRegistry | точные команды, current revision/история, расписание, `source_under_test`, executable-классификация/покрытие и семантические конфликты | изобретение команд или разрешение filesystem paths |
| evidence | план, immutable observations/arguments/decisions, механическая достаточность | I/O, оценка истинности вместо агента |
| application/TaskCommands + EvidenceCommands | общий вход, авторизация позиции, короткая UoW | таблицы и внешние команды |
| SQLite repositories/UoW | запись с FK/optimistic version и внешний lock | сами принимать proof или переходить этап |
| runtime/execution/artifacts | реальные наблюдения Git/команд/files; publication и transport | прямые UPDATE Task/evidence state |

`EvidenceBook` используется композиционно; это не BaseTask и не отдельный движок каждого goal type. Runner делегирует чистым handler/domain, не держит lock во время subprocess. Generic observe/check отличаются способом интерпретации заданных наблюдений, не названием цели.

Правила происхождения, аргумента и осмотра остаются раздельными. `passed` — исход predicate, `interpretable` — распознанность наблюдения, `accepted/rejected` — последующий смысловой осмотр. Их нельзя сворачивать в один status.

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

Реализация общего pipeline не подменяет API Task/Sprint. Все конфиги AI poise остаются в AI poise; generated project/process файлы не редактируются агентом напрямую. Код приложения, тесты и документация приложения — через нативные инструменты согласно правилам codebase. Полный контракт: [declarative-tools](declarative-tools.md).

## DDD-04A — 2026-09-06T20:26:43+05:00

Новый доменный владелец `modules/goal_config` использует RouteDefinition, SectionRule и ContentPolicy без I/O. Application `GoalConfigCommands` делегирует файл/DB через ports; infrastructure размещена в `infrastructure/goal_config.py`. CLI adapter `interfaces/goal_config.py` принимает stdin, формирует bounded response и не обходит доменную validation. Общий `infrastructure/locking.py` используется двумя хранилищами.

Текущий Task process snapshot не зависит от актуальной копии pack; его project execution config остаётся отдельной границей. Версия store изменена явно. Новый editor не наследует чужой goal-type процесс: явный template копируется в независимое определение.

Список [кандидатов правил](../governance/rule-candidates/rule-candidates.md) не является новым источником policy до решения пользователя.

## DDD-04B — новые владельцы и зависимости
Обновлено: **2026-09-06T22:58:21+05:00**.

WorkTools — application facade, не новый агрегат. Task остаётся владельцем секций, evidence и lifecycle. ArtifactPlan — чистая модель генерации; FileArtifactFactory — инфраструктура; существующий ArtifactRegistry — регистрация owner-scoped файлов. InteractionLedger владеет identity/user-event rules, SQLite InteractionStore — таблицами observations и bindings.

`interfaces/work.py` читает stdin и оформляет bounded JSON/file response. Он не пишет таблицы Task. Application/work не импортирует SQL/filesystem. Composition root Runtime подключает порты. Чтение и запись пользовательских событий не поручаются LLM SQL-командами. Ledger transaction независима от Task verification: отрицательный test не отменяет реальное сообщение.

Общий API не реализует 13 разных engines. DDD-04A editor и DDD-04B work entry остаются отдельными предметными сценариями. Конфигурационные шаблоны и артефакты имеют разные contracts; никакого универсального произвольного `write_file` для служебных данных.


## DDD-05 — 2026-09-11T16:25:00+05:00
Sprint владеет планом/графом/решениями; Task владеет доступностью, началом и состоянием работы. Published Sprint создаёт Task через общий доменный builder и TaskRepository в одной UoW. Клиент не подменяет исходные шаблоны/таблицы рабочими JSON patch.

`modules/sprints` и `modules/tasks/definition` не имеют I/O. `application/sprints` не импортирует SQL/filesystem. `infrastructure/sprint_work` вычисляет eligibility, формирует `result_provenance`, наблюдает текущий commit явно настроенного Git `base_ref` для новой Task и делегирует Task API. Result commits предшественников остаются provenance и не выбирают branch base. Точный наблюдённый base SHA сохраняется в reservation до Task start; recovery использует его повторно, а resume/handoff существующего worktree не наблюдают ref заново и не пересоздают workspace. База сериализует короткие записи; проверки/commit/push параллельных worktrees не держат общий DB lock.

Замена ошибочной незавершённой Task остаётся координацией этих владельцев, а не новым lifecycle writer. `SprintWork` до транзакции наблюдает pending/ownership/handoff и чистоту worktree. `SprintCommands` в короткой UoW повторно сверяет Sprint revision, Task version и снимок preflight, валидирует полный replacement через сохранённый process, вызывает `Sprint.replace_task` для графа/relation и `Task.supersede` для состояния источника. Инфраструктурный адаптер не присваивает lifecycle напрямую.

Replay receipt проверяется до внешнего preflight. При новом intent весь Task/Sprint write-set — создание replacement, supersession источника, новый plan/layer, membership/dependencies, relation и receipt — фиксируется одной UoW; fault injection подтверждает общий rollback. Старый membership нужен для принадлежности исторического Task, но current facts фильтруются сохранённым Sprint plan. Contract/submissions/evidence/artifacts/handoff старой Task не переносятся и не переписываются.

Relation хранится в Sprint decision snapshot и revision layers, а idempotency — в существующем `sprint_requests`; отдельная таблица или миграция схемы не понадобились. Чтение идёт через прежние Sprint/Task projections. Accounting лишь отображает `superseded` как terminal outcome и не становится владельцем Task state.

### Публичный обзор работ — 2026-09-11

`WorkTools` композиционно строит `work_overview` через два узких read-порта: Sprint предоставляет идентификаторы опубликованного реестра и полные dependency-aware overview, Task предоставляет summary только самостоятельных задач. Application-слой проверяет разные словари статусов, применяет независимые фильтры к готовым owner-проекциям и собирает транспортный объект `{"sprints": ..., "standalone_tasks": ...}`.

Этот query не становится новым владельцем lifecycle, membership или readiness. Sprint по-прежнему определяет факт публикации, состояние графа, `eligible`/`blocked` и доступность result objects; Task определяет принадлежность и lifecycle standalone Task. SQLite-адаптеры реализуют параметризованные project-scoped reads, но клиент не получает SQL-интерфейс. Последовательное чтение двух read-models не объявляется единым cross-domain snapshot. Публичный пакет и ограничения результата описаны в [batch work](../workflows/batch-work.md#обзор-опубликованных-спринтов-и-самостоятельных-задач), семантика Sprint — в [Sprint API](../workflows/sprints.md#реестр-опубликованных-sprint).

## DDD-06: внешние планы
Владелец плана — Actions: PlanSpec/ActionRun/Publication. Task остаётся единственным владельцем verified/accepted и переходов. Application PlanCommands связывает их коротким UoW; Git/command адаптер не выполняет SQL. Репозиторий actions сохраняет run+event, а не весь Task обходным путём.

apply_plan/publish — обработчики общего runner. Sequential merge и command/probe различаются адаптерной стратегией plan.kind, не названием business goal. Источники/команды явные; нет автоматического чтения AGENTS.md как командного DSL. Метрика сообщений привязывается к фактической активной работе независимо от имени операции.

## Дополнение DDD-07

Обновлено: **2026-09-07T00:07:53+05:00**. RuntimeIdentity/OutputPolicy — чистые модели. WorkTools остаётся единым прикладным входом. CLI `runtime` только получает packet, вызывает adapter и общий presenter.

RuntimeRegistry владеет binding/cursor, не Task. HandoffCommands меняет жизненный цикл только через Task API и UoW. LocalHandoff — инфраструктурная последовательность Git/files с receipts; session release согласован с Task и receipt в одной короткой транзакции. Sprint читает подтверждённое release через Repository и вычисляет resumable как часть собственного overview.

Парсер не исполняет исходную команду повторно и не записывает бизнес-таблицы. Async worker создаёт лишь представления; Task verdict и Incident имеют разные источники. Разделение поддержано архитектурными тестами импорта и обхода, но они не заменяют осмотр произвольного будущего кода.


## DDD-07B — scoped Transfer
Обновлено: **2026-09-07T00:25:38+05:00**. Transfer владеет протоколом пакета, выбором владельцев, локальными receipts и location mapping. Он не выбирает следующий этап и не подтверждает evidence. Application TransferCommands зависит от порта, не SQLite/файлов. Rehydration существующего same-schema snapshot проходит через специальные Task/Sprint repository методы и валидируется существующей доменной моделью. Совпадающие owner IDs не перезаписываются. Короткая транзакция публикует весь выбранный набор, внешние файлы/Git подготавливаются заранее.

Тексты секций и исходные evidence неизменны; machine paths читаются через location adapter. Artifact ID основан на owner-relative location и переживает перенос. Runtime sessions/cursors источника не переносятся. Подробно: [transfer](../workflows/transfer.md) и [библиотечный API](library-api.md).

## DDD-07C — 2026-09-07T01:17:28+05:00
- `modules/capabilities` и `modules/hook_transport`: pure contracts; нет subprocess, времени и filesystem.
- `application/CapabilityChecks`, `HookCommands`: пакетные сценарии через порты.
- `infrastructure/HookService`: composition с прежним WorkTools/InteractionStore; native hook не принимает и не завершает Task.
- `HookRegistry`: собственные operational таблицы, без SQL к tasks/sprints.
- `LocalProbeExecutor`/`StdioProbe`: реальные read-only наблюдения, не интерпретация пользовательского intent. Наличие inventory не выдаётся за успешный probe.
- Сохранение native hooks — отдельный конфигурационный effect; не меняет Codex trust и не выполняет скрыто приёмку AI poise этапа.

### session-scoped source resolution

Доменный `BoundSourceRoute` выбирает только владельца source: installation, существующую
целевую Task или текущую Task. Application `WorkTools.prepare_bound_source` координирует
подготовку существующей Task через её публичные владельцы, не потребляя сообщение и не
исполняя пакет. Infrastructure `HookService` проверяет зарегистрированный `Task worktree`,
Git- и filesystem-границы, после чего запускает отдельный интерпретатор из выбранного source.

`native binding` остаётся владельцем session/message provenance. Выбранный source и его
проверочные факты не записываются в binding, Task DB или общую конфигурацию; поэтому две
сессии могут конкурентно использовать разные worktrees без общей мутации. Installation
interface только переносит результат выбранного дочернего `WorkTools` и не становится вторым
исполнителем операции. Уточнено **2026-09-12**: все операции текущей Task, включая рабочие
переходы, используют валидную текущую конфигурацию без общего `config_hash` gate, как
предусмотрено задачей 0026. Task-owned snapshots сохраняются; хеш остаётся диагностическим
provenance. Это не отменяет проверок binding, source, ownership и revisions.

Адресный `terminal inspection snapshot` для `completed`, `cancelled` и `superseded` проходит
через installation source: терминальная Task не обязана иметь доступную worktree и не может
становиться владельцем session source. `BoundSourceRoute` выбирает эту транспортную границу,
а `WorkTools` координирует вызов; ни один из них не меняет Task lifecycle.

Task остаётся владельцем terminal status и освобождённого claim. Runtime составляет проекцию
из сохранённых Task context, content, evidence и audit history через существующие owner/query
API, после чего гарантирует отсутствие current-task binding. Активные `active`, `verified` и
`accepted` ownership guards проверяются до такого чтения. Пустая evidence-проекция
аварийно отменённой Task допустима и не интерпретируется как незавершённая проверка.


## Accounting ownership — DDD-08

Updated: 2026-09-12T00:25:00+05:00.

Accounting owns raw usage, time cycles, benefit credits and explicit prior-result finding attribution, not Task/Sprint state. Domain is I/O-free; application uses the accounting port. SQLite mutations remain accounting-owned, external measurement executes outside the state lock. Task outcome precedes measuring benefit, never follows a fabricated metric. User-message ledger stays the source of message identity; no second counter duplicates it. Query projections may read other owners through existing query contracts.

`Clock` is the accounting observation port. `Poise` and `RuntimeAccounting` require it at composition; production CLI, native hook and runtime adapters explicitly supply `SystemClock`, while tests supply deterministic adapters. The domain observation type carries audit UTC, a monotonic value and its comparison domain; filesystem and system-clock access remain infrastructure responsibilities. Accounting alone validates persisted clock continuity and stores measured/unmeasured cycle state. Query projection may combine audit start with a known monotonic duration for calendar allocation, but no layer derives elapsed time from wall-clock subtraction.

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
использует terminal-набор Task `completed`/`cancelled`/`superseded`, поэтому исторический
superseded snapshot не становится активной работой. Детерминированный relocation staging
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


## DDD-10 — интеграция принятого результата

Обновлено: **2026-09-12T21:36:11+05:00**.

`ResultIntegration` владеет неизменным intent, состояниями merge/conflict/cleanup, receipts и idempotent replay. Task остаётся единственным владельцем verified/accepted/completed lifecycle и содержательного результата: операция интеграции читает окончательный Task result, но не меняет его статус, секции или evidence. Sprint продолжает вычислять состояние из Task и не выполняет скрытый auto-merge.

`ResultIntegrationCommands` — прикладная граница одного декларативного пакета. `RuntimeResultIntegration` реализует Git-наблюдения и эффекты, а сохранение выполняет через execution repository. WorkTools только маршрутизирует `integrate` и `show integration`; domain не импортирует filesystem, subprocess или SQLite.

Intent до эффекта фиксирует task ID, request ID, accepted source commit, наблюдавшийся target commit и пользовательскую authorization. Adapter проверяет окончательный Task result и чистый task worktree. Существующие task branch/worktree и task-scoped temporary-backup directory сохраняются до первого Git-эффекта; отдельные integration branch/worktree не создаются. Новый вызов с тем же request ID и изменённым intent отклоняется.

Внешняя блокировка БД не удерживается во время Git. `IntegrationRun` сохраняет accepted commit, последний включённый target, mutable integration head, конфликты и решения, check receipts, publication receipt, cleanup outcomes и историю фаз. Незавершённый merge восстанавливается по task worktree, `MERGE_HEAD` и conflict set. Разрешение принимает ровно один rationale для каждого сохранённого conflict path. Точная legacy-форма blocked-запроса `integrate-0048-1` имеет узкий adapter, сохраняющий старую историю и accepted commit; остальные неизвестные формы отклоняются.

Объявленные проверки запускаются на готовом integration head в task worktree. Публикация сериализуется общим lock по target. Под lock adapter снова читает target ref: drift обновляет ту же task branch и повторяет проверки. Стабильный candidate публикуется в основном checkout только `git merge --ff-only <task-branch>`; crash после эффекта распознаётся по текущему ref и не повторяет публикацию. При отказе сохраняется before/after fingerprint `HEAD`, binding, index, tracked/untracked content, types, modes и operation state.

Только после подтверждённой публикации начинается монотонная уборка: task worktree removal предшествует task branch deletion, scoped temporary directory удаляется последним. Текущий target обязан содержать integration head и accepted commit; descendant допускается, переписанная история сохраняет recovery state. Ref удаляется compare-and-delete. Чужие worktrees и operator/deliverable/recovery backups не затрагиваются. Hook route относит `integrate` к installation source, поэтому устаревший accepted код не управляет завершителем. Состояние остаётся в Task execution snapshot; новая таблица не требуется.

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
