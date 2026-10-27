# Границы ответственности: DDD-04B

Обновлено: **2026-10-20**. Срез **POISE-DDD-04B**.

## Явные результаты инструментов

Самостоятельный [tool-result](../configuration/tool-results.md#назначение-и-границы)
использует существующий модуль result_views: чистый domain владеет policy/request,
выбором фрагмента, общим UTF-8 head/tail и agent envelope, точными координатами
адресного текста. Application ToolResults нормализует явный controlled Bash вход
и координирует единственный ResultStore; inspection selection общая для agent и
machine, display budget применяется только к agent.

Порт ResultStore отделяет полный record/источники от filesystem I/O. Infrastructure
отвечает за scoped адрес, raw-copy/readback, атомарную публикацию, source integrity
и прежний placement owner; interface — за JSON/CLI, обязательный выбор audience и
предметную диагностику. Composition выбирает существующий project/checkout/caller
без Task lifecycle и без запуска производителя. Полные stdout/stderr/JSON остаются
доступны программным потребителям; краткое отображение не меняет сохранённые данные.

Прежний Poise result-view сохраняет свой контракт. Этот вход не создаёт отдельный
runner, evidence ledger, универсальный host hook или MCP execution server.

## Управление задачами и доказательствами

| Владелец | Обязанность | Запрещено |
|---|---|---|
| Task | кандидатный результат, доказательства, transition, приёмка и rework | SQL/Git/команды |
| workflow/handlers | produce/inspect/revise/observe/check | goal-type branching, присваивание lifecycle |
| verification/CheckRegistry | точные команды, current revision/история, расписание, `source_under_test`, executable-классификация/покрытие и семантические конфликты | изобретение команд или разрешение filesystem paths |
| evidence | план, immutable observations/arguments/decisions, механическая достаточность | I/O, оценка истинности вместо агента |
| application/TaskCommands + EvidenceCommands | координация снимка, доменной подготовки, авторизации и короткой записи | прямое управление таблицами и исполнение внешних команд |
| SQLite repositories/UoW | согласованный raw capture, проверка источников, подготовленная запись с FK/optimistic version и внешний lock writer | сами принимать proof или переходить этап |
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
формированием invocation, source provenance, execution key и receipt. Execution adapter
получает уже связанный cwd/environment и не
угадывает язык, layout проекта или источник импорта. Evidence хранит наблюдение и provenance,
но не исправляет неверный method после запуска.

Чистая `verification.domain.method_expectation_digest` задаёт единую идентичность
ожиданий метода. `runtime.Poise._invocations` использует её при создании квитанции,
а SQLite `accepted_sources` — при распознавании исторического определения.
`artifact_inputs` включается только при присутствии ключа, с точными вложенными
значениями. SQLite читает сохранённую историю, не вводит собственную проекцию ожиданий
и не переписывает прежние receipts. Digest полного определения для устранения повторов
истории остаётся отдельной идентичностью; он не заменяет digest ожиданий.

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

Универсальный перехват вывода произвольных инструментов, async parser integrations
и MCP-перехват остаются отдельными срезами. Явный named-check API уже использует
общий сохранённый output owner и выбранные семантические парсеры; это не реализация
универсального перехвата. В DDD-04 capture адресуемый и durable у задачи;
сама форма EvidenceBook не доказывает дополнительные runtime integrations.

### Выбранная сдача результата

[Выбранная форма verify](../workflows/batch-work.md#выбранная-сдача-результата)
использует прежний результат этапа и его владельцев. `modules/work/selection`
проверяет чистое намерение и формирует фактическое продолжение;
`application/result_selection` координирует выбор. Runtime предоставляет один
актуальный шаблон для контекста и сдачи. `WorkResources` читает выбранные
источники, вычисляет digest и охраняет факты файлов, делегируя подготовку и
материализацию существующей ArtifactFactory.

Admission идёт через TaskCommands и существующий WorkPacketRepository/UoW:
записываются исходная идентичность, нормализованный результат и факты источников.
После этого используется прежний explicit verify, CheckAttempts, Evidence и
финальная запись результата. Проверка source guard выполняется до регистрации;
подтверждённая смена источника получает свой отказ, прочие ошибки сохраняют
своего владельца. Журнал выбранного запроса не является новым lifecycle,
реестром доказательств или отдельной БД.

Обратная связь сохраняет происхождение: sections/metadata идут из явного выбора
и актуального шаблона, байты — из выбранных файлов, commit/tree/checks/outcome —
из native результата и квитанций. Continuation отражает сохранённое состояние
и не выполняет следующий этап. JSON interface сохраняет typed отказ как при
разборе до runtime, так и после исполнения, через общего владельца полного вывода.
Полномочия, повтор и независимый осмотр остаются за прежними владельцами;
transport не принимает содержательных решений.

## Подготовка независимых записей

`TaskReadSet` объявляет нужные источники операции. SQLite UoW читает их согласованно
и возвращает `SqliteTaskInputs` после закрытия собственного снимка и project fence.
Полное восстановление доменных объектов, проверка намерения, внешние наблюдения через
существующие порты и кодирование подготовленных значений выполняются вне этого контекста.
В окончательном writer сначала проверяется квитанция запроса, затем выбранные источники
и полномочия; сохраняются готовые изменения. Domain владеет переходом, application —
координацией, SQLite adapter — сравнением сохранённых фактов и атомарностью записи.

`task_snapshot` сохраняет прежнее полное восстановление Task, `task_record` — собственную
проекцию record, `task_writes` — подготовленные SQL-значения. Публичные TaskQueries
`record/content/evidence_view/verification_registry` закрывают снимок перед соответствующим
восстановлением/проекцией; content, evidence и активный registry сохраняют полную
Task-валидацию. Record, Store и accounting сохраняют
собственные проверки, без добавления чужой полной проверки Task. Locations, methods и
история захватываются в том же снимке там, где их использует конкретный DTO.

При restart текущий execution очищает attempts, publication, pending и last_report в
общей транзакции с перезапуском, а immutable submissions/results и реестр сохраняют
историю. Getter newborn распознаёт объявленное отсутствие Task и возвращает предметный
отказ с ID до записи; существующая проверка newborn status сохраняется.

Requirements использует отдельный SQL provider, а Task сохраняет собственный исторический
requirements snapshot. Проверка двух файлов не становится общей транзакцией. Пакетный
Requirements CRUD сохраняет свою [описанную границу](../workflows/requirements-registry.md#конкурентная-запись).
Явно транзакционные библиотечные helpers продолжают работать в контексте вызывающего кода.
Закрытие снимка не меняет Task/worktree ownership и не даёт полномочий на приёмку.
Подробный алгоритм, ожидания и повторы принадлежат [контракту SQLite-записи](sqlite-write-boundary-design.md#подготовка-и-короткая-запись).

## Подготовка Task-worktree

`Runtime.bootstrap` сохраняет владельца допуска/выделения Git-области, затем
передаёт действительные координаты в `application.worktree_preparation`.
Явная неподключённая подготовка проверяется до изменяющего допуска; терминальное
чтение остаётся у существующего bootstrap. Проект без выбранного раздела
конфигурации использует прежний маршрут.

Чистый `PreparationAttempt` в `modules.worktree_preparation` различает begun,
captured и completed; проекция показывает ready/error только по подтверждённой
квитанции, иначе unconfirmed. Application выбирает прежнюю попытку и согласует её
до новой внешней команды. `SqlitePreparationRepository` сохраняет состояние через
существующий journal и optimistic revision; владение Task проверяется существующим
owner. Нового writer жизненного цикла Task здесь нет.

`FilePreparationEffects` читает выбранный конфиг один раз, применяет стандартный
Template и поставляет inputs. Общие CommandConfiguration/CommandExecutor и порт
ResultHandler обеспечивают заменяемые команды/плагин. Плагин отвечает за анализ
полного результата и одну разрешённую попытку исправления; оркестратор не содержит
семантики конкретного внешнего пакета. Команды и файловые эффекты выполняются вне
коротких UoW. Настройки/версии внешнего приложения принадлежат установке.

Полная metadata выполненной команды и оба сырых Task-owned sidecar сохраняются до
immutable публикации streams. Полный capture.pending сохраняется до публикации
capture. Общий publisher повторно использует существующий artifact factory для
недостающих регистраций; строгая проверка bytes/digests/координат предшествует
подтверждению. Sidecar — источник восстановления, не публичный успешный receipt.
Уже зарегистрированные недоступные/изменённые bytes не подменяются; неполный исход
остаётся unconfirmed. Application одним итоговым UoW связывает квитанцию и completed.
Операционная инструкция: [подготовка области](../operator/worktree-preparation.md#подготовка-рабочей-области).

## Именованные проверки проекта

`modules.project_checks` владеет чистыми контрактами запросов, профилей, пакетов,
наборов, исходов, сохранённых DTO, парсеров, прогресса и пригодности истории.
`application.project_checks.ProjectChecks` координирует допуск, выполнение и чтение.
`infrastructure.project_checks` реализует файловую конфигурацию, Git inputs и subject,
наблюдение executable/cwd/environment, отдельный ledger/cache, capture adapter,
материалы и optimistic publication. JSON/SQLite остаются в infrastructure;
ClockObservation и строгие типы сохранённых run/result проверяются чистым владельцем.

`NamedRunLedger` — авторитетные operational run/result rows. Непригодная consumed
запись даёт contextual `registry_invalid` и сохраняется. `NamedResultCache` —
необязательные payloads: подготовленный payload становится reuse только после
проверки действительного ledger result, original run и retained bytes. Отказ store
не меняет известный исход процесса. Application сохраняет единственный окончательный
execution result с фактическим stored metadata.

Повторно используются `RegisteredCheckRunner` и общий `SavedResponse`. Смысловой
progress observer не меняет byte-policy существующих потребителей. Operational
history/calibration используют сравнимый публичный Clock, а реальные deadline/cleanup
остаются у процесса. Task/C018/CheckRegistry/evidence не читают generic payloads
как собственные receipts и не получают новых lifecycle writers.
Полный [пользовательский контракт](../configuration/named-project-checks.md#назначение-и-границы)
владеет настройкой и процедурами; этот раздел фиксирует направление зависимостей.


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

Workflow snapshot владеет обязательным boolean `worktree_required`; runtime, SprintWork
компонуют API владельца, не реализуют отдельные алгоритмы захвата. Native hook
registry предоставляет подтверждённые события liveness: `SessionEnd` означает DEAD,
незавершённая наблюдаемая сессия — LIVE, недостающая запись — UNCERTAIN. Runtime без
наблюдателя не получает скрытого разрешения на перехват. Сами claims не выполняют Git,
filesystem cleanup, chdir или смену launch root.

Для Task без worktree runtime использует настроенный repository как среду проверок,
сохраняет закреплённую базу и результат без ветки. Обычное освобождение не создаёт
worktree или bundle; bootstrap читает сохранённые факты, не создаёт искусственное дерево.
Accounting не измеряет несуществующий Git delta, но продолжает измерять объявленные
полезные секции относительно их baseline. Это не изменение Clock или полноты телеметрии.

Существующие terminal cleanup (0048) и result integration (0057) остаются у своих
владельцев; освобождение claims не удаляет worktree и не публикует коммиты. Интеграция
принятого результата по-прежнему использует текущую установку и существующее дерево Task.
Атомарность SQL claims не обещает транзакцию, охватывающую Git или внешние команды.

## Task workflow snapshot migration

Явной миграцией сохранённых Task workflow snapshots владеет Task workflow persistence
boundary. Чистый `WorkflowSnapshotMigration` проверяет авторизованный набор и строит
изменения из неизменяемого `goal_type` и настроенных workflow definitions. Application
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

Schema `task-workflow-migration-2` имеет отдельный compile-time scope ровно из Task `0082` и
не меняет авторизованный набор schema 1. Она сохраняет опубликованный контракт первой операции:
domain добавляет к legacy workflow только отсутствующий `worktree_required`, а её старый receipt
остаётся replayable. Корректирующая schema `task-workflow-migration-3` имеет тот же точный scope,
сохраняет уже добавленный совпадающий boolean и строит отсутствующий stage contract каждого route
stage: scope берётся из сохранённого stage, gates — из сохранённых content requirements по phase.
Storage
меняет только workflow и contract snapshots внутри metadata; Task version, lifecycle/result/
content/evidence/ownership/Git поля и released handoff остаются прежними. После commit
работоспособность доказывается обычным reviewer bootstrap сохранённой verified Task.

Для `task-stage-criteria-migration-1` координатор Task проверяет полный набор
процессов и незавершённых Task, сохраняет intent и выполняет финальную транзакцию
metadata/audit. Чистое историческое преобразование отделено от публикации.
Полные договоры переносит неизменённый `stage_criteria_history`; постановки
до подготовки договоров — чистый `stage_criteria_drafts.draft_candidate`, который
повторно использует тот же канонический digest и преобразование процесса.
Он проверяет исходную версию, metadata и выбранный тип, сохраняя отсутствие
будущих договоров и критериев. Файловую систему и SQLite этот преобразователь
не обслуживает. Общий координатор учитывает идентичности всех незавершённых
задач; вид ресурса проверяется по её фактическому состоянию.

В финальной транзакции metadata постановки без процесса не перезаписывается,
а readback сравнивает исходный текст. Для постановки с выбранным процессом
записывается только кандидат с явной классификацией существующих этапов.
Итоговые summaries постановок принадлежат завершённому intent; до commit
ответ восстановления отражает только подтверждённые публикации процессов.

Явный `--project-settings` передаёт файловую часть существующему
`FileProjectConfigUpdate`: он владеет проверкой реестра и native receipts,
подтверждением исходной revision, полным кандидатом процессов, pending,
квитанцией и её readback. Координатор сохраняет выбранное подтверждение источника
и вызывает его повторную проверку; CLI/composition не исправляют документы.
Производитель `FileProjectSetup._probe` и общий чистый предикат
`valid_project_probe_readiness` принадлежат `infrastructure/projects.py`.
Предикат проверяет сохранённые
[формы `readiness`](../configuration/project-setup.md#подтверждение-проверки-git);
оба читателя setup/update квитанций в `FileProjectConfigUpdate` используют его
в составе проверки своих оболочек. Предикат возвращает `bool`, не обращается к Git,
файлам или БД и не изменяет вход. Привязка источника, canonical digest и ошибки
конкретной квитанции остаются у прежних потребителей.
Порядок блокировок этого режима — project, затем Task. Файловая публикация и
финальная SQLite-транзакция имеют отдельные результаты восстановления.
Без выбора project settings сохраняется редактор каталога. Операторский контракт
и условия повтора находятся в [руководстве по
миграции](../task-workflow-snapshot-migration.md#публикация-атомарность-и-восстановление).

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
project/workflow файлы не редактируются агентом напрямую. Код приложения, тесты и документация
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

Текущий Task workflow snapshot не зависит от актуальной копии pack; его project execution config
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
с тем же ID, Sprint membership, workflow/contract snapshot и append-only restart audit. Task и
execution repositories одной UoW сохраняют смену состояния и сбрасывают только текущие attempts,
publication, pending и last_report, не изменяя worktree/branch/base/entry tree и Git WIP.

Pending external outcome блокирует restart до выполнения его явного recovery protocol. Replay
receipt проверяется до мутации; conflicting intent отклоняется. `ready` повторно валидирует
сохранённый workflow, а `start` возобновляет прежнее execution/worktree. Public Sprint correction
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

RuntimeRegistry владеет binding/cursor, не Task. OwnershipCommands.release проверяет
собственный claim и expected_version, закрывает accounting cycle и снимает зависимое владение
в одной UoW с receipt. Существующий WIP остаётся на месте; Git/files transport удалён.
Sprint вычисляет доступность участников через существующий repository/overview.

Парсер не исполняет исходную команду повторно и не записывает бизнес-таблицы. Async worker создаёт
лишь представления;
Task verdict и Incident имеют разные источники. Разделение поддержано архитектурными тестами импорта
и обхода, но они не
заменяют осмотр произвольного будущего кода.


## DDD-07B — scoped Transfer

Scoped transport удалён: нет текущих TransferCommands, snapshot export/import или
копирования рабочей области при смене участника. Обычный ownership owner освобождает
свой Task и зависимый binding в одной UoW, сохраняя существующие WIP и историю;
получатель захватывает ту же область через bootstrap.

`CheckConfigurationSystems` владеет явным необязательным `check_runner.systems`,
а material-location owner разрешает механические пути исторических receipts.
Он не переписывает содержательный текст/argv и не создаёт новый перенос.
Постоянная доставка, интеграция и TaskCleanup сохраняют отдельные полномочия.
[Протокол смены участника](../workflows/local-handoff.md#пакет).

## DDD-07C — 2026-09-07T01:17:28+05:00
- `modules/capabilities` и `modules/hook_transport`: pure contracts; нет subprocess, времени и
  filesystem.
- `application/CapabilityChecks`, `HookCommands`: пакетные сценарии через порты.
- `CapabilitySummary` задаёт полный завершённый результат сессионной проверки;
  `SummaryStore` — порт его сохранения. `SessionReadiness` компонует прежний
  `CapabilityChecks`: обычное чтение возвращает сохранённое, явное обновление наблюдает
  capabilities и получает UTC через переданный `Clock`. Готовность и requiredness
  остаются решением существующего владельца проверок.
- `FileCapabilitySummary` сохраняет полный файл через прежний `atomic_write` и явные
  режим/лимит; короткие файловые эффекты защищены `ProjectRuntimeFence`. Новые probes
  выполняются вне длительной общей блокировки native события. `HookRegistry` сохраняет
  workspace настоящего SessionStart в существующих event metadata для обновления,
  в том числе после потери файла сводки. Новая схема БД или привязка к Task не вводится.
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

Настройки, показ и отказы описаны в [сводке готовности
сессии](../configuration/runtime-hooks.md#сводка-готовности-сессии).

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
предусмотрено задачей 0026. Task-owned workflow, contract и verification snapshots сохраняются;
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
workflow-files публикуются через инструментарий. Внешние fixtures/сценарии не являются ядром.


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

`ProjectConfigCommands` — application route для обычного revision-aware изменения
действующего проекта. Он проверяет форму bounded-пакета и зависит от
`ProjectConfigUpdatePort`; filesystem, SQL и subprocess в application layer не входят.
`FileProjectConfigUpdate` владеет сборкой полного кандидата, общей `load_config` validation,
публикацией, component digests, pending/durable receipts и recovery. CLI и composition только
передают пакет этому route и не редактируют managed файлы самостоятельно.
Явная историческая миграция вызывает тот же файловый владелец после проверки
собственного запроса; она не ослабляет обычный вход `ProjectConfigCommands`.

`GoalTypeDefinition` остаётся владельцем workflow candidate. Project updater координирует
несколько независимых workflow snapshots, но не вводит второй редактор их внутренней модели.
Generic manifest edits не могут менять `project`, `schema`, `workflows` или `paths`:
перемещение state принадлежит отдельному relocation protocol.

Task остаётся владельцем workflow snapshot и lifecycle. Обновление живого workflow не меняет
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
progression, creation intent и request digest. `TaskCommands` получает закрытый снимок
пространства ID и выполняет preflight/creation validation до окончательной UoW.
Preview распределения не резервирует ID. Runtime, planning и Sprint используют того же
чистого владельца allocation; они не вычисляют номера через Git или filesystem.

Пакетные application services передают владельцу полный набор зарезервированных explicit IDs,
поскольку только они знают ещё не опубликованный состав Task/Sprint intent. Окончательный
SQLite writer проверяет квитанцию и фактическую доступность выбранного ID перед insert.
Изменение всего namespace само по себе не служит общим veto. При коллизии автоматически
распределяемого ID application повторяет только выбор и чистую компиляцию зависимых значений
из сохранённого preflight, без повторения внешней подготовки. Явный занятый ID отклоняется;
допустимое пространство задаётся policy. Отдельный sequence/counter не вводится. Группа
публикации сохраняет детей, родительский результат, Sprint и receipts атомарно.

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

Граница ролей берётся из явных `role` соседних узлов сохранённого route. Чистый
`crossed_role_boundary` подтверждает передачу текущего проверенного результата другому участнику
по его истории владения: обычные release/acquire и исторические события прежних передач проверяются
по их реальному происхождению. Уже доказанная передача сохраняется при повторном получении тем же получателем;
цикл только исходного автора не создаёт её. Событие освобождения хранит `actor=None`, поэтому
отправитель определяется из предыдущего доказанного владельца. Конечный владелец сверяется с Task.

Read port `TaskRepository.verified_ownership_history` связывает текущие submission ID/digest,
этап, итерацию и версию с точным `verified` событием и полной непрерывной последовательностью
до текущей версии. SQLite adapter читает эти факты; чистая policy решает, подтверждена ли передача.
`TaskCommands` запрашивает историю на действительной границе ролей и применяет policy в той же
UoW, что прежние identity/content/report/tree/unknown/duplicate gates, `Task.progress_stage` и
`execution.patch`. Уже разрешённый `user_accept` того же результата сохраняет существующую
`accepted` цепочку, но classifier не принимает результат и не создаёт полномочий публикации.

Общий приватный `_event_range` обслуживает также прежний `ownership_event_suffix`, сохраняя
его отдельный ownership-only tuple contract для recovery. `review_identity` по-прежнему определяет
последнего non-inspect производителя для независимости проверки, а не автора текущего review.
Исторические строки прежнего transport сохраняются неизменными; новый
`release` не создаёт transport receipt или bundle.
Новые таблицы, миграция или сохраняемая привилегия не вводятся. Runtime и transport не получают
второго lifecycle; операция не отправляет сообщения и не исполняет работу этапа. Публичная
процедура принадлежит [контракту продолжения](../workflows/batch-work.md#продолжение-проверенного-результата-после-освобождения).

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
В metadata режимах infrastructure проверяет явно выбранный каталог и пути;
получение тел выбранных инструкций принадлежит отдельному prepare.
Task/Sprint продолжают владеть готовностью и жизненным циклом; чистый владелец
FocusedDecomposition потребляет классификацию, а не поддерживает второй inventory.
Контракт и отображение классов: [каталог skills](../configuration/skill-catalog.md).

Общий file-selection имеет следующих владельцев:

| Владелец | Ответственность |
|---|---|
| modules/skills/instructions | Чистые policy, profile matching, scopes/reasons, identity, availability diagnostics и delta. |
| modules/skills/instruction_ports | InstructionSource: узкий порт наблюдения контекста и материалов. |
| application/ProjectInstructions | Композиция request → project context → policy/catalogue → paths/previous → selection → observation → result. |
| infrastructure/FileInstructionSource | Read-only manifest/Git binding, metadata, exact ancestor discovery и UTF-8 byte observation. |
| interfaces.skills | Единственный operation dispatcher, полное JSON представление и exit code. |

Обратная проверка content/SHA ведёт к одному чтению исходных bytes;
required/scopes/reasons — к policy и конкретным рабочим путям; delta — к текущим
наблюдениям и проверенному previous. Domain не читает диск, application не создаёт
Task/runner, CLI не ведёт второй lifecycle. Общий prepare выбирается перед eager
legacy loader; специализированный DevelopmentRouting сохраняет stage/facts/checks
и не заменяется общим file-selection.

ERP donor: commit16c5b906615e57a5b5f89b495c42b79f51eab87e,
ai-assistant/agent/effective-policy-manifest.py. Повторно использованы
OrderedValues/ReasonMap, universal/profile selection, ancestor rules и file_delta;
адаптации ограничены явными file scopes, обязательностью, identity и шестью
сравниваемыми полями. ERP runtime/Task policy не перенесены.
Действующий load_config читает полный project manifest; отсутствие Task API/DB
в этом потоке не означает полного удаления Task plugins.
Автоматическая nativehook интеграция и доказательство применения инструкций моделью
не реализованы. Операторский договор:
[подготовка инструкций по
файлам](../configuration/development-routing.md#подготовка-инструкций-по-файлам).


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
## Автоматическая повторная приёмка этапов

Обновлено: **2026-10-08**.

Task владеет историей принятых посещений, действующим договором и журналом
промотки. Чистая политика повторного использования сопоставляет текущие
проверочные обязательства с историческими определениями и планом доказательств.
Application координирует эту проверку до применения принятого результата;
transport не подставляет старый договор и не принимает решения о совместимости.
Evidence и общий runner сохраняют новые наблюдения при повторном запуске
исторических тестов. Изменение текста требований само по себе не создаёт
новой проверки применимости доказательства.

Исторический метод с сохранёнными входами распознаётся через общего verification-владельца
идентичности ожиданий. Затем действующий договор и фактическая целостность комплекта
проверяются до потребителя. Повторный запуск использует постоянные байты Task, сохраняет
новые настоящие доказательства и не изменяет исходные квитанции и файлы. Подмена текущего
договора историческим, альтернативный digest и исправление истории при чтении не применяются.

Git workspace owner проверяет исходное зарегистрированное дерево, ветку,
сохранность работы и пересечения игнорируемых файлов перед каждым
потенциально разрушительным checkout. Возобновление использует тот же
владелец и повторяет проверку фактических файлов, а не доверяет прошлому
preflight. Recovery-ссылка сохраняет исходный коммит; она не содержит
новые игнорируемые файлы, поэтому их появление требует отказа до перезаписи.
Списки путей Git передаются в машинном формате с разделителем NUL; отображаемые
экранированные имена не используются для проверки области или пересечений путей.
Машинный stdout принимается как байты и декодируется как имя файла без преобразования
переводов строк: CR, LF и CRLF внутри имени обозначают разные пути.
См. [операционный контракт](../workflows/batch-work.md#автоматическая-промотка-после-перезапуска).

При объединении native Git и общего транспорта владелец `run_git_receipt`
сохраняет вывод команд с `-z` через декодирование файловой системы без
преобразования переводов строк и потери байтов имён. Для обычного текстового
вывода сохраняется строгая проверка кодировки. Все потребители получают полные
stdout и stderr; ограничения отображения не применяются внутри транспорта.

## Поставка и удаляемые материалы Task

Договор хранения и ответственности владельцев описан в
[поставке результата](../workflows/terminal-materials.md#владельцы-механики).
TaskDelivery координирует намерение и минимальный журнал без присвоения lifecycle,
интеграции, экспортирования и публикации файлов. Общий material resolver сохраняет
логическую identity артефакта, выбирая явно поставленный физический источник.
Исторические DB-результаты не дают права воскресить удалённые raw evidence.

## Размещение рабочих результатов

`modules/placement` владеет чистыми решениями о текущем размещении: строгими
входами, выбором правила по смысловым осям и приоритету, scoped согласованием,
baseline и совместимостью деклараций. Правила путей используют общий
`foundation.paths.matches_component_path`; impact matching делегирует тому же
владельцу вместо второй реализации component glob.

`application/placement.PlacementCommands` координирует решение, текущего владельца
и порт хранения. `infrastructure/placement.FilePlacementWorkspace` отвечает за
чтение baseline/байтов, существующий project resource fence, lock, atomic writer
и fsync/readback. Равенство байтов допускает подтверждение без replace; ошибка
подтверждения сохраняет неопределённость эффекта в ответе.

`infrastructure/project_binding` переиспользуется общими инструментами для
проверки configured project/зарегистрированного checkout. Caller предоставляет
существующий `direct_caller`; идентичность не дублируется. Необязательный
`PoiseCurrentPlacementOwners` читает текущие Task/Sprint через существующие
repositories в защищённом read-only snapshot только при необходимости выбранного
правила. Он не приобретает Task, не создаёт DB и не выполняет lifecycle.

CLI transport разбирает полный JSON, composition root соединяет владельцев.
Placement декларации не заменяют immutable artifact publication, transfers или
управление Task. Их владельцы и полномочия сохранены. Полный операторский контракт:
[пакеты](../configuration/result-placement.md#пакет-prepare-и-check),
[эффекты и повтор](../configuration/result-placement.md#отказы-и-повтор).

## Общее чтение файлов и ресурсы эпизода

`modules.document_intake` владеет выборкой, идентичностью выбранного текста, read decisions,
строгими request/policy contracts и ports; домен не обращается к SQLite/filesystem/hooks.
`application.ContentReader` согласует подготовку текста, short gate, generation/budget snapshot,
поэлементную регистрацию и сохранение полного ответа. `IntakeSessions` связывает session операции.

Инфраструктурные `ProjectContentSource`, `PreparedRegistry`, `PreparedTemplate`, `EpisodeResources`
и rendering реализуют ports. Пустая schema-2 — versioned ресурс пакета; чтение live registry не
мигрирует схему. Настоящий native episode/End остаётся у существующего HookService. Общие
SessionEstablisher/CallerIdentity и SavedResponse переиспользуются с другими потребителями;
новый Task/Sprint/evidence lifecycle не создаётся.

`CoordinationStore` отвергает существующие non-object записи до обращения к полям.
`EpisodeResources` проверяет owner/path/episode и перечитывает его под gate перед восстановлением:
старый A не перезаписывает допущенный B. Exact End перепроверяется перед удалением своего
temporary tree, внешние control/diagnostic/coordination/template сохраняются. Source code,
настройки live hooks и пути работающей установки не загружаются из читаемого checkout.

[Публичный контракт](../configuration/project-file-reading.md#назначение-и-явные-входы) и
[жизненный цикл](../workflows/file-intake-lifecycle.md#допуск-и-владение) отделены от
[специализированного acknowledged reader](../workflows/source-reader.md#явные-диапазоны-и-поколения-контекста).
Поддержанные обычные Bash-чтения подключаются отдельным event adapter к тому же ресурсу.
Универсальный перехват инструментов и автоматический compaction reset не заявлены.


### Общий учёт обычных команд чтения

Чистые CommandPolicy/CommandClassifier/SourceProjection/coverage decisions принадлежат
`modules.document_intake`. Они определяют поддержанную грамматику, исходные вклады
и emitted line volume, range union/novelty/generation, пригодность ответа и строгий cached
control codec. Файловая система, время, SQL и HookService не импортируются в домен.

`CommandSource` наблюдает доказанные bytes/EOF/final separator/version и передаёт неизменяемые
факты classifier. Одна SourceProjection задаёт фактические byte contributions и source ranges;
повторные emissions увеличивают объём, ledger объединяет покрытие. Effective cwd требует
доказательства внешнего literal cd; nested cd инвалидирует inherited relative base.
Неразрешённая проекция/база следует явной policy без выдуманных планов.

`application.CommandIntake` согласует Pre pending, Post settlement, current-source eligibility,
replay/conflict, Clock и bounded coverage. Он использует существующие IntakeSessions,
EpisodeResources/PreparedRegistry и общий generation; нового ledger, schema upgrade, Task
или session lifecycle не создаёт. Direct ContentReader добавляет сведения в тот же ledger;
его фактические partial effects сохраняются при позднем отказе supplemental учёта.

`infrastructure.document_intake.command_transport` связывает настоящие HookService/binding,
явные project/checkout/policy, caller и установленное immutable definition. Полный control
публикует существующий SavedResponse; только native feedback ограничивается отдельно.
Обратный поток coverage/решения содержит metadata/reason/action, а не повторное source body.
Pre может запретить выполнение, Post только сообщает о состоянии уже совершённого чтения.

[Настройки и границы](../configuration/project-file-reading.md#обычные-команды-чтения),
[event wiring](../configuration/runtime-hooks.md#подключение-событий-чтения) и
[settlement/recovery](../workflows/file-intake-lifecycle.md#ожидание-и-завершение-обычного-чтения)
принадлежат своим каноническим страницам. Поставляемый adapter доказан isolated installed
consumer; live host delivery, model memory, ACK и universal compaction reset не следуют из этого.
