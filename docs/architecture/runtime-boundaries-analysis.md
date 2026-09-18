# Анализ границ runtime — A01

Обновлено: 2026-09-18. Task `REVIEW-ARCH-A01`. База анализа: `947264bc062822dda9a5dfca918fff2d80375c07`.

## Решение

Сохранить модульный монолит и существующих предметных владельцев. Не считать количество
строк само по себе дефектом. Первый обоснованный срез — согласование попытки внешней
проверки и её сохранения по A03/R01, а не механическое дробление `runtime.py`.
Настоящий документ завершает анализ, но не выполняет архитектурный рефакторинг.
Новое поручение пользователя от 18 сентября разрешает выполнить зарегистрированные
задачи; прежнее registration-only ограничение относится к прошлой поставке.

## Метод и наблюдаемая структура

Прочитаны composition, конструктор Poise, verify и конкретные adapters. AST-инвентарь
включает прямые обращения и обычные локальные алиасы `h=self.h`; это статические
зависимости, не измерение частоты вызовов и не полный динамический граф.
[Инвентарь с методами и строками](../../projects/ai-poise/standalone/REVIEW-ARCH-A01/artifacts/execution-20260918/dependency-inventory.json).

| Потребитель полного runtime | Наблюдаемые зависимости | Предметный владелец и граница |
|---|---|---|
| WorkResources | store и локальный runtime alias для artifacts/results | Ресурсы work-пакета; не новый Task writer. |
| RuntimeAccounting | cfg, session, store; PayloadMeasurer(runtime) | AccountingCommands и раздельные authoritative/optional repositories. |
| SprintWork | UoW, process/config, prepare_creation, creation base; cleanup/current_task/paths | SprintCommands владеет членством/графом; адаптер связывает физические пути и cleanup. |
| RuntimePlanActions | private _git/_tree/_stage/_roots/_invocations/_verification_workspace, cfg, session, store | PlanCommands владеет сохранённой операцией; Git effects не атомарны с SQL. |
| LocalHandoff | current_task/_task, roots/tree/git, plan_actions, artifact validation, runner.submit, ownership | HandoffCommands сохраняет план и освобождение; адаптер сохраняет bundle/WIP до release. |
| RuntimeTransfers | cfg/session и store/repository snapshot через runtime | TransferCommands и SqliteTransferRepository; перенос не даёт право менять роль. |
| RuntimeResultIntegration | cfg/paths/state/store/task_queries и отдельный RuntimeTaskResourceCleanup(runtime) | IntegrationIntent и persisted phases; accepted commit не переписывается. |
| RuntimeTaskResourceCleanup | roots/config/runtime/state/store/task_queries/register_artifact_paths | TaskResourceCleanup/CleanupRun: удалить только точно принадлежащие Task ресурсы. |

`composition.task_tools` уже передаёт узкие `store`, `repository_tree`, `requirements_gate`.
`Poise.__init__`, напротив, передаёт весь `self` восьми перечисленным adapters. Это
конкретная причина риска связанности: инфраструктура может обращаться к private runtime
методам и получать несвязанные полномочия без изменения своего конструктора.
Не каждое такое обращение нарушает доменную границу: большая часть — композиция и I/O.

## Карта операций и полномочий

**Проверка.** Runtime разрешает cwd/environment/source, читает дерево и вызывает
RegisteredCheckRunner. TaskCommands/StageRunner разрешают этап, registry и evidence.
EvidenceBook сохраняет отдельный смысл наблюдения/аргумента/решения. SqliteUnitOfWork
атомарно сохраняет только SQL. Разрыв между subprocess и observation batch — R01;
добавлять бесконечные SQL-транзакции вокруг команды нельзя.

**Интеграция.** ResultIntegrationCommands принимает IntegrationIntent; инфраструктурный
адаптер содержит сам алгоритм внешних фаз. Здесь app façade тонкий, а не доказанно
полностью независимый use case. Сохранённые фазы, контроль target drift, неизменный
accepted commit и ff-only в основном checkout должны пережить любой перенос кода.

**Cleanup.** TaskResourceCleanup уже координирует load/validate/preserve/remove/finish
через adapter и чистый CleanupRun. RuntimeTaskResourceCleanup выполняет только
физические операции и persistence. Этот образец предпочтительнее добавления нового
универсального runtime interface со всеми сегодняшними методами.

**Handoff.** Сначала сохранить артефакты/точный WIP/bundle, затем release. Нельзя
заменять это освобождением claim без сохранения внешнего состояния. Независимое
владение worktree и Task остаётся у WorkOwnership.

## Альтернативы

| Вариант | Польза | Цена и решение |
|---|---|---|
| Оставить всё как есть | Нет риска массовой регрессии | Допустимо для незатронутых slices, но не устраняет R01. |
| Разбить большой файл по числу строк | Меньше каждый файл | Не сужает полномочия и не решает внешние crash boundaries; отвергнуто. |
| Выделять связные use cases с узкими портами | Ограничивает влияние изменений и делает fault injection адресным | Рекомендовано постепенно; сначала протокол проверки, потом отдельно оценивать integration. |
| Новый движок/микросервисы/общая БД | Не требуется текущим результатом | Усложняет транзакции и перенос; не предлагается. |

## Первый срез и сохранённые инварианты

A03 должен определить небольшой attempt coordinator: вход — task/stage/iteration,
submission digest, tree, execution key и точные invocations. Порты — получение/сохранение
попытки в существующем Task execution owner, сохранение immutable receipts,
выполнение уже разрешённой команды, подтверждение batch. Task сохраняет право решать
готовность, rework и переход; coordinator не принимает результат сам.

Не экспортировать `_git`, целый `Poise`, произвольные SQL или редактируемую Task metadata
как порт. Immutable request/result DTO должны содержать только необходимые значения.
`passed`, `interpretable`, `accepted/rejected` остаются отдельными величинами.

Минимальная R01-реализация может сначала устранить crash window существующими APIs.
Физическое извлечение coordinator — отдельное предложение после стабилизации протокола,
не скрытое требование переписать весь runtime для закрытия бага.

## План внедрения и отката

1. Сохранить публичную grammar и characterization tests текущего verify/rework/handoff.
2. По A03 описать точки crash и долговечную identity; реализовать только R01 внутри владельцев.
3. При отдельном решении извлечь use case без изменения DTO, версий данных и обратной связи.
4. Сравнить trace эффектов/receipts при одинаковых запросах, запретить новые broad dependencies.
5. Перенос integration/cleanup рассматривать отдельно: не менять их проверенные эффекты одновременно.

Откат чистого извлечения — revert commit при неизменной storage форме. Откат изменения
протокола попытки требует сначала завершить или явно согласовать pending по новой версии;
старый код нельзя запускать вслепую на неизвестной ему pending форме. Резервная копия
всех БД и Git обязательна до смены версии. A03 уточняет это для своего формата.

## Проверки и границы выводов

Адресно сохранять domain/application import boundaries, полный verify/replay, fault
injection после subprocess/receipt/batch, foreign-owner rejection, snapshot isolation,
переходы rework и existing integration crash-after-ff/target-drift. Не тестировать
рефакторинг сравнением только числа классов или строк. Работа A01 проверена чтением
зависимостей и существующим Markdown checker; нагрузочные свойства измеряет A02.
Размер и статический доступ не доказывают дефект исполнения, реальную нагрузку,
независимую приёмку или пригодность всех потенциальных вариантов извлечения.

[Действующие границы](boundaries.md), [общий план](review-followup-2026-09-18.md).
