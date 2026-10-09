# Статус реализации

Обновлено: **2026-09-20**.

Текущее устройство хранения описано в [матрице владельцев и версий](storage-lifecycle.md#владельцы-и-версии). Числа проверок и сведения о review в датированных разделах ниже относятся к соответствующим историческим задачам, а не к новой проверке всей поставки.

## Восстановление файлов локальной конфигурации

Обновлено: **2026-10-04**. Реализован `poise local-assets`: явные project/root,
декларация и source snapshot, полная предварительная проверка, create-only
публикация и сохранение существующих значений. Непарные Unicode surrogate
отклоняются до эффектов; Git root проверяется независимо от унаследованных
`GIT_` переменных с поддержкой linked worktrees. Значения repair не генерируются.
Это восстановление файлов, а не БД или managed проекта; многофайловый I/O-отказ
не обещает rollback. Контракт и исполняемые примеры находятся в
[руководстве](../configuration/local-assets.md).

## Текущее хранение и восстановление

Конфигурационный контракт `ddd-accounting-12` не является номером SQLite schema. Текущая Task DB имеет schema **13**, отдельная Requirements DB — **1**. Optional telemetry использует отдельные database/lock и ленивую инициализацию общей SQLite schema; её доступность не является условием успешности основной работы.

Поддержан только [ограниченный переход Task DB 12 в 13](storage-lifecycle.md#ограниченный-переход-task-db-12-в-13). Неоднозначные legacy claims не разрешаются догадкой и не превращают upgrade в универсальную миграцию. Исходные данные и историю сначала сохраняют полной копией. Пути внешних БД при восстановлении сопоставляют явно.


## Task planning flexibility — согласованный следующий срез

Runtime-часть проектирования реализована: goal type материализует Task-owned draft,
planner меняет его границы и process, ready фиксирует результат, авторизованный restart
открывает только разрешённые прежней policy изменения. Используются существующие
Task, catalogue, process validator и транзакции; новая SQLite schema не введена.
Полный [контракт runtime](../workflows/task-planning.md) отделяет работающие действия
от оставшихся ограничений. Историческое имя раздела сохранено для входящих ссылок.

System → Application → Task поддерживает явно пустые future-узлы без новых уровней;
они сохраняют связи и отображаются как gaps. Доставка артефактов при reuse сохранена.
Обычный verify теперь создаёт локальный commit-кандидат **до** проверок и сохраняет
exact-commit identity в execution key, receipt и report. Приёмка, replay и продвижение
повторно проверяют hash и дерево; сохранённый legacy report без этой привязки требует
штатного restart вместо приписывания старому evidence новой достоверности.

Сохранённые maintenance Task 0159–0163 используются для штатного restart/re-edit/ready
после поставки; чужое владение не снимается догадкой о завершении сессии.

## Explicit newborn field removal — task 0110

Публичный `operation: task`, `action: edit` принимает обязательные `patch` и `remove` в одном
optimistic/idempotent intent. При смене `goal_type` caller явно удаляет сохранённые draft-поля,
которые запрещены целевым процессом; например, переход development → integration удаляет
`executable_obligations` без отмены или замены Task. `null`, скрытая очистка, compatibility
reader и ручное исправление БД не используются.

Domain определяет точный набор полей через тот же владеющий creation contract, что и `ready`.
До мутации отклоняются неизвестные, повторные, отсутствующие, обязательные, immutable и
одновременно patched/removed поля. Digest включает `remove`; точный replay, identity, Sprint
membership, append-only history, process snapshot, ownership и revision guards сохраняются.
Путь проверен focused lifecycle 5/5, bounded smoke 6/6 и независимым inspection.

## Broken Task recovery — task 0079

Публичный Task action `restart` возвращает ту же незавершённую неинтегрированную Task в
`newborn`. Он сохраняет ID, append-only history, Sprint membership, contract/process snapshot,
worktree, branch, base, entry tree и Git WIP; сбрасывает только состояние текущей попытки.
Optimistic version, immutable request identity, foreign ownership, terminal status и pending
external outcome проверяются до фиксации изменений. Участник уже опубликованного Sprint после
повторного `ready` снова становится `available` под тем же ID.

Невыполнимый контракт и отказ следующего DoR возвращаются как `broken` с точной причиной и
явными recovery routes: локальная правка дефектного inspection gate проверяющим либо restart
для изменения immutable Task contract. Старый публичный Sprint action `replace_task` удалён;
ранее сохранённые replacement relations остаются историческими метаданными. Действующий lifecycle не содержит статуса `superseded`; старые записи не возобновляются как новый способ замены Task.
Путь проверен профильными runtime, Task, Sprint, ownership и terminal-inspection сценариями;
полный набор репозитория этим утверждением не объявляется пройденным.

## Current verification registry — task 0055

Реализована атомарная замена текущего реестра проверок на этапах, владеющих
`test_registry`: `add`, `replace`, `reschedule`, `remove`, точная revision и idempotent
`request_id`. Старые definitions, snapshots, execution identities, observations, receipts и
submissions остаются адресуемыми; SQLite восстанавливает current projection отдельно.

Для процессов с test-inspection gate Task creation и каждая мутация требуют явный
`executable_obligations`. `CheckRegistry` отклоняет неизвестные refs и при выходе из
test-inspection требует непустой текущий GREEN executable-набор с полным совокупным `covers`.
Документационные и иные неисполняемые пункты не классифицируются скрыто; один настоящий узкий
тест может покрыть несколько обязательств. Сохранённая классификация валидируется при
восстановлении, migration/fallback и требование полного repository-wide suite не добавлены.

Публичный маршрут и persistence/replay проверены шестью task-specific сценариями; bounded
smoke и смежные Task/verification проверки прошли на зафиксированном дереве задачи. Это не
объявление полного набора репозитория выполненным.

## Verification source provenance — task 0037

Новые методы требуют явный `source_under_test`: repository bindings разрешаются внутри
текущего task worktree, external methods содержат причину. До subprocess проверяются
отсутствующие/выходящие пути, коллизии окружения, semantic duplicates, конфликтующие
ожидания и несвязанный RED. Receipt и replay identity содержат `provenance_digest` и
`expectation_digest`. Сохранённые прежние методы не переписываются миграцией; строгий
контракт действует при создании и расширении реестра.

Интеграционный runner также использует общего владельца разрешения исходников:
проверяет фактический cwd и bindings до subprocess, сохраняет provenance и её digest,
сверяет их с текущим кандидатом перед публикацией. Неполная историческая партия
требует новых проверок; непригодное доказательство только что выполненной партии
возвращает `blocked/candidate_proof_invalid`, сохраняя историю и worktree.

Изменение устраняет класс ошибки подтверждённого incident 0035, где один method прошёл над
неподтверждённым источником, а task-owned regression в том же запуске сообщил 7 failures.
Ограничение: external provenance подтверждает декларацию отсутствия repository source, но
не fingerprint внешнего сервиса; такие доказательства требуют собственных наблюдений.

DDD-01…08: Task/Sprint, секции и поэтапное содержимое, графовый runner, Evidence,
FeedbackBook, семь семейств handlers, конфиг-редактор, декларативные пакеты, ArtifactFactory,
точные проверки и Git, runtime/JSONL/парсеры/handoff/scoped transfer, локальные hooks/probes,
accounting — сохранены. Текущие утверждения проверяются профильными тестами репозитория;
числа из удалённых исторических отчётов не считаются актуальной регрессией.

DDD-09 добавляет 13 независимых исполняемых reference configurations и typed task templates,
пакетную их установку/разворачивание, публикацию reviewed Task/Sprint планов через существующие
репозитории и продолжение feedback на обычных этапах сбора/создания/проверки.
Все исходные 98 узлов и 34 именованных возврата имеют отображение в конфигурации и тестах.

Это не обещание production-ready качества всех предметных документов. Поля, аргументы,
reviewer и пользовательские решения эталонных маршрутов — authored fixtures. Реальны Git,
SQLite, subprocess, конфликты, публикации, изоляция и проверки состояний. Исторические
нормативные тест-кейсы не переназначаются в PASS по совпадению идентификаторов.

Актуальные версии и точные границы upgrade указаны в [текущем хранении и восстановлении](#текущее-хранение-и-восстановление); schema 11 и произвольные автоматические миграции не поддерживаются.
Live Codex trust, JetBrains, Gmail-доставка, полные usage/latency источники и многомашинная
синхронизация существующих владельцев всё ещё требуют отдельного доступа/работы.

## Automatic Task ID allocation — 2026-09-11T06:55:21+05:00

Реализован public `bootstrap` creation intent `{request_id, task без id}`. Task domain/application
и repository/UoW атомарно выбирают явно настроенный numeric ID, создают aggregate и durable
worktree reservation; replay возвращает стабильный ID. Reviewed standalone и Sprint
publication используют тот же allocator, возвращают allocation receipts и резервируют весь
explicit состав независимо от порядка. Explicit-ID path сохранён. В момент этой задачи использовалась SQLite schema 12; актуальная версия указана в [текущем хранении и восстановлении](#текущее-хранение-и-восстановление).

Проверены последовательность/progression, конкурентность, replay другой сессии, digest
conflict, occupied/exhausted namespace, конфигурация, идентичность downstream surfaces,
matching/conflicting worktree recovery, reviewed publication, rollback, Sprint alias rewrite,
legacy row и архитектурные границы. Исполняемые сценарии находятся в
[`tests/ddd/test_task_id_allocation.py`](../../tests/ddd/test_task_id_allocation.py).

Проекты без `task_ids` продолжают работать только с explicit IDs и получают явную ошибку на
automatic intent. `project-config` изменяет существующие manifest fields, но не добавляет
отсутствующую numeric policy; автоматического выбора диапазона по-прежнему нет.


## BUG-CONFIG-001 — 2026-09-12

Реализованы `ProjectConfigCommands` и bounded CLI `project-config` для изменения действующего
project manifest/process snapshots. Полный кандидат проходит прежние project/process
validators; известная общая revision и revision каждого изменяемого process проверяются до
публикации. Durable receipt обеспечивает idempotent replay, pending receipt — продолжение
точно известной частичной публикации после interruption.

Process updates не меняют snapshots существующих Task. Manifest update и безопасный перенос
state требуют quiescent проекта и сериализуются с Task creation/claim через state locks.
Relocation проверяет source/destination, сохраняет всё дерево, поддерживает `retain` и
`delete_after_publish`, удерживает locks обоих state roots до receipt и не допускает пустой
замены. В действующем коде терминальными являются `completed` и `cancelled`; исторические сведения о замене Task не вводят дополнительный статус. Первый запрос
отклоняет занятый deterministic staging без удаления и без pending receipt; exact retry
очищает только частичную staging-копию операции с matching pending receipt. Проверяемый
контракт покрыт 27 сценариями `tests/projects/test_update.py`; это число
относится к профильному набору BUG-CONFIG-001, а не к полной регрессии репозитория.

## DDD-10 result integration — 2026-09-12T21:36:11+05:00

Публичный `integrate` запускается из текущей установки и использует существующие task branch и
task worktree. Accepted commit не переписывается; update, conflicts и checks продвигают только
integration head этой ветки. Отдельные integration branch/worktree больше не создаются.

Публикация сериализована по target и выполняется в основном checkout только через
`git merge --ff-only`. Drift повторяет update и checks. При отказе сохраняется доказательство
неизменности `HEAD`, binding, index, tracked/untracked content, типов, режимов и operation state.
Crash после fast-forward согласуется по target ref без второго эффекта. Cleanup удаляет только
task worktree, task branch и scoped temporary backups. Узкий legacy adapter восстанавливает
сохранённый blocked-запрос `integrate-0048-1`, не меняя accepted commit и прежнюю историю.

Профильные проверки task 0057 покрывают повторное использование ресурсов, conflict resolution,
target drift с повтором checks, blocked fast-forward fingerprint, crash/replay, legacy recovery
и installation-source routing. Это не сертификация push, remote publication или multi-codebase
integration.

## Durable stage progression — 2026-09-13

Реализована публичная операция `advance` с устойчивыми `request_id`, `task_id` и
`target_stage`. Она сохраняет цель в Task journal, использует обычные route transitions и
останавливается на работе этапа, entry gate, границе executor/reviewer либо достигнутой цели.
Точный replay после process loss, correction, restart и handoff продолжает тот же intent;
изменение цели конфликтует. Переход роли требует public handoff и bootstrap другой сессии.
Обычные переходы пишут `stage_progressed` без `user_accept*` и сохраняют report как `verified`;
перед `publish` возвращается `user_acceptance_required` до отдельного решения пользователя.

Операция не запускает работу этапа, проверки или сообщения и не выдаёт полномочия на приёмку,
публикацию и интеграцию. Профильные тесты покрывают последовательные этапы одной роли, реальную
смену сессий, repeated RED/GREEN correction, entry gate без частичной мутации, owner/pending и
недостижимые цели, replay и бизнес-статусы остановок.

## Terminal task-owned cleanup — 2026-09-12

Реализован единый `TaskResourceCleanup` для standalone `cancel`, Sprint `cancel_tasks`/`cancel`,
supersession и уборки после успешной интеграции. Terminal-переход сохраняет обязательство и
возвращает точные ресурсы, но не выбирает судьбу commit. Публичный `cleanup` требует отдельные
authorization, request identity, полный ожидаемый commit и disposition `preserved` либо
`discard_authorized`; publication остаётся только в `integrate`.

Состояние persisted в Task execution snapshot и содержит прогресс, blocker и историю. Реально
проверяются точные worktree/branch identity, commit, зарегистрированные temporary resources и
digest. Повтор после process loss идемпотентно продолжает недостающие шаги. Dirty worktree не
удаляется: решение фиксируется, а продолжение требует clean same-branch fast-forward checkpoint
с новым request ID либо возврата к прежнему commit. Существующая terminal Task без cleanup-state
инициализируется только явной командой, без фоновой миграции.

Реализация сохраняет main checkout, foreign resources, durable Task history/artifacts и
operator backups; не применяет force-delete, push или скрытый fallback. Профильное покрытие
находится в `tests/task_cleanup`, Sprint cancellation/replacement и result-integration cleanup;
фактический GREEN подтверждается текущим verification batch, а не зафиксированным здесь числом.


# Project listing — Task 0097 — 2026-09-14

Существующий `poise project` поддерживает read-only действие `list`, сохраняя прежнее создание
без позиционного действия. Один явный `configured-project-registry-1` используется batch и
interactive публикацией, точным replay после сбоя регистрации и списком. Выдача сортирует
пригодные проекты и явные missing/invalid ошибки; отдельного CLI, discovery-сервиса или
directory scan нет. Рабочий `config/project-setup.json` указывает на локальный registry с
проектом `ai-poise`; оба файла исключены из Git, их форму показывают examples. Актуальный GREEN подтверждается verification evidence Task, а не
зафиксированным в документе числом.


# POISE-PILOT-01 — 2026-09-07T14:15:55+05:00

Реализованы `project`/`project-init`: batch creation, явные templates, back/change/keep/abort,
публикация цельного проекта, 13 независимых process snapshots, локальные Git readiness checks.
Рабочий конфиг загружается обычным runtime; real-source pilot остаётся verified без autoaccept.
Исторический результат пилота не заменяет текущий запуск тестов. Revision-aware updates
существующих project manifests поставлены отдельным `project-config`; нового multi-codebase
engine, установки интерпретатора или live IDE/remote сертификации нет.


## POISE-PILOT-02 — 2026-09-07T15:04:48+05:00

Исправлен существующий developer runner: сохранность всех failed/incomplete workspace,
terminal JUnit как условие успеха, запрет пустой выдачи, общая bounded execution, явная
retention успешных пакетов. Это не новый runtime AI poise.

Добавлен пример продолжения реального planning-пилота на один execution stage с export
через Task/Transfer. Протоколы/схема БД не менялись; код domain/runtime остаётся прежним.
В поставке есть сама переносимая задача, не только отчёт. Execution verified, но не принят.
Расход модели и не наблюдённые события пользователя не подставлялись.


## Автономный пилот — 2026-09-09T01-11-02+05-00

Завершены analysis и self_inspection прежней POISE-SELF-VERIFY-02. Итог completed принят агентом по ограниченному делегированию пользователя. Checkpoints трёх состояний сохранены отдельно; последний реально импортирован и прочитан в третьем store. Продуктовый код не изменён, 719 исходных тестов не объявляются заново выполненными. Следующая работа — выбранный реальный проект/native runtime; входы не предоставлены.
Реализован явный `verification_plan`; его владелец — `CheckRegistry`. Проверка
использует route и `allowed_paths`, но не выполняет рекурсивный анализ зависимостей
pytest или другого runner.

## Поставка и удаляемая рабочая область Task

Поддержаны публичные `delivery/agree`, `delivery/settle` и read-only
`show/task_delivery`: явные Git/file/ignored_configuration/customer/no_result,
проверка постоянных подтверждений, consumer/resource gates и удаление всего
Task-root после terminal status без скрытого файлового архива. Незавершённый
retirement повторно наблюдает постоянный результат; completed replay читает
историю без захвата Task или восстановления raw proof. Согласованная намеренная
замена обычного каталога допустима; saved inode/device служит только аудиту.

Постоянные файлы retired-артефактов разрешаются общим material owner с прежней
логической identity. Export сохраняет независимый prepared-снимок и возобновляет
точную завершённую операцию для уборки её preparing. Новое экспортирование уже
удалённой рабочей области не поддержано. Существующим Task не назначается
неявный договор, полномочия приёмки/интеграции не выводятся из settlement.
Точные формы и ограничения — в [договоре поставки](../workflows/terminal-materials.md#публичный-договор-поставки).
