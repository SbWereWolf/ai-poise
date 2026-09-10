# Runner, транзакции и основные прохождения

Создано: **2026-09-06T16:04:53+05:00**.

Статус: **контракты и псевдокод; не реализация runner**.

## 1. Один runner, разные доменные владельцы

Runner координирует текущий этап, но не становится новым агрегатом со всеми полями Task/Sprint. Он использует ProcessDefinition, Task, текущую StageIteration и библиотечные сервисы. Переходы выбираются RoutePolicy по точной конфигурации; применяет переход Task через собственный метод, а не присваивание строкового state.

Семь stage handlers — стратегии приложений. Они не хранят бизнес-историю у себя и не имеют собственного event loop. Контекст и receipts передаются им явно. `revise` использует тело `produce` либо `apply_plan`; не запускает вложенный runner.

Внутренний результат handler может быть: требуется input агента, требуется внешнее действие, требуется продолжение по новым фактам, проверки не удовлетворены, готов к finalization. Внешние коды/названия и представление определены явным CLI/output config. Неизвестный handler — ошибка конфигурации, не переход к общему fallback.

## 2. Ключевые типы входа и результата

| Контракт | Содержимое | Кто формирует |
|---|---|---|
| `WorkBinding` | Project, session, task/sprint/ad-hoc scope, cycle, iteration, phase | sessions + bootstrap; агент не переписывает IDs каждый раз |
| `StageContract` | Inputs/output schema, handler, разрешённые изменения, exact method refs, limits, переходы, report | Независимый process snapshot + captured execution config |
| `StageSubmission` | Содержательные результаты разрешённой фазы, список artifact paths, commit message где нужен | Агент через один result file |
| `CandidateTaskView` | Текущее состояние с применённым submission и происхождением каждого нового слоя | Task/content |
| `SubjectSnapshot` | Пин проверяемого объекта, деревьев codebases и явно значимых execution facts | Workspace/Execution adapters |
| `VerificationPlan` | Конкретные точные методы, expectations, dedup execution keys, obligations | verification из task/project/stage contracts |
| `ExecutionReceipt` | Фактические argv/cwd/env identity, исход, raw refs, timestamps, parser identity | Executor; без секретных значений |
| `GateAssessment` | Какие структурные/исполнительные требования выполнены/не выполнены; references | verification + соответствующий owner domain |
| `DomainChangeSet` | Изменения root, новые children/links и события, подготовленные доменными методами | Task/Sprint/session models |
| `CommandOutcome` | Business outcome, operation health, новый context/report, available details | application + presenter |

`StageSubmission` не содержит фактических exit codes от Harness, hashes workspace, текущих IDs или счётчиков времени. Ручное evidence содержит только требуемый аргумент/наблюдение и ссылки; машина не выдаёт его наличие за содержательную истинность.

## 3. ENTER → PREPARE → OBSERVE/APPLY → CONTINUE → FINALIZE

Эти фазы уже обсуждались и сохраняются. Они не являются новыми пользовательскими этапами.

```text
ENTER:
  разрешить один порученный этап;
  получить Task/Sprint context и effective contract;
  подготовить worktree при необходимости и result template;
  вернуть агенту работу.

PREPARE:
  прочитать один result package;
  применить его к кандидатному состоянию;
  проверить структурные preconditions и точные ссылки;
  сохранить кандидат без объявления verified.

OBSERVE / APPLY:
  выполнить только уже определённые механические действия;
  сохранить их фактические receipts.

CONTINUE:
  когда по новым фактам требуется рассуждение,
  вернуть их агенту и принять разрешённый результат той же итерации;
  не повторять уже выполненную внешнюю operation.

FINALIZE:
  проверить нужные evidence/изменения;
  фиксировать verified result, commit/push и сохранность;
  сформировать доклад и остановиться.
```

В `verification` логический аргумент по существующим фактам подаётся в PREPARE; по новым наблюдениям — в CONTINUE. В integration merge сначала выдаёт конфликты, затем агент их разрешает. Эти различия задаёт контракт метода и этапа, а не специальная ветка `if goal_type == ...`.

## 4. Псевдокод общего verify

```text
VerifyStage(binding, result_file):
    command_context = resolve_binding_and_explicit_contract(binding)
    submission = decode_by_current_phase_schema(result_file)
    observed_subject = workspace.observe_if_required(command_context)
    observed_paths = artifacts.inspect_declared_paths(submission.paths, binding)

    with short_uow():
        task = tasks.load_for_change(binding.task)
        operation = operations.resolve_request(command_context)
        candidate = task.apply_submission(submission, contract)
        candidate.attach_artifact_facts(observed_paths)
        check candidate fields, typed refs and exact methods
        assessment = verification.plan(candidate, observed_subject, contract)
        save candidate + operation intent; NOT verified

    while operation has allowed mechanical work and budget remains:
        phase_result = handler.advance(contract, candidate_view, saved_receipts)

        if phase_result requires external effect:
            persist step intent in short_uow
            receipt = execution_port.execute_exact_or_probe(step)
            persist receipt in short_uow
            continue only if next step needs no new reasoning

        if phase_result requires reasoning:
            return fresh context and CONTINUE contract of the same iteration

        if phase_result is ordinary not-satisfied or blocked:
            return exact unmet requirements with saved observations

        if phase_result ready for finalization:
            finalizer.perform_configured_checks_and_publication()
            with short_uow():
                reload relevant roots and compare relevant versions
                task.mark_verified(assessment, finalization_receipts)
                save Task ChangeSet + durable operation result + noncritical jobs
            finalize required details and cleanup boundary
            return report; no automatic next substantive stage
```

Это описание ответственности, не готовая функция. `candidate_view` после каждого committed update пересобирается из зафиксированных данных, а не хранится как вечный mutable dict между вызовами.

## 5. Повтор: submission, операция и check attempt различаются

Простое равенство digest result file не означает повторно использовать старые тесты. Экономим повторную запись содержания, но не отказываемся проверять исправленный код.

| Ситуация | Решение |
|---|---|
| Тот же phase payload, тот же subject, все эффекты подтверждены | Вернуть сохранённый результат без новых слоёв/публикаций |
| Тот же phase payload, дерево изменилось H1→H2 после failed check | Тот же содержательный слой, новый check attempt для H2 |
| Новый предусмотренный payload той же активной фазы | Новый candidate layer; related evidence переоценивается |
| Новый CONTINUE по уже полученным фактам | Дополнение текущей итерации; OBSERVE/APPLY receipts не теряются |
| Git push не завершён, но проверенный commit неизменен | Продолжить доставку/проверку remote receipt; не перезапускать тест без причины |
| Предыдущая внешняя операция имеет неизвестный исход | Probe/receipt reconciliation; при невозможности — конкретная остановка, не слепой повтор |
| Результат уже verified и предъявлен, затем код изменился | Сначала санкционированный rework; старый результат не переписывается |

Ключ исполнения учитывает exact invocation, codebase/cwd, captured method definition, subject, существенные execution inputs и связанные expectations. Дедупликация одного запуска не удаляет отдельные obligations. Несогласованные ожидания выявляются до запуска; RED не означает любое ненулевое завершение.

## 6. Пример: заполнить секции и зарегистрировать артефакты

Агент заполнил план и создал `task-root/design.svg`. Result file содержит нужные секции, список путей и решения. Других метаданных от агента не требуется.

```text
VerifyStage
  → TaskCommands.submit_stage_result
    → Task.apply_submission
      → SectionBook.append разрешённых новых слоёв
      → VerificationPlan.validate_links
    → ArtifactRegistry.inspect_paths [без DB lock]
    → Task.attach_artifacts [только refs разрешённых owner scopes]
  → UnitOfWork сохраняет candidate sections + artifact refs + operation
  → общие проверки
  → Task.mark_verified
  → доклад
```

Физическое владение определяется расположением файла. Путь внутри sprint текущей task не перемещается в task root: ArtifactEntry принадлежит sprint, текущая Task хранит ссылку. Путь другого sprint или чужой runtime отклоняется.

Read-only inspection не запрещает сохранить новый отчёт/находку в Task: запрет относится к **осматриваемому предмету**, а не к служебному бизнес-результату осмотра. Это различие выражено в subject contract.

## 7. Пример: принять этап и продолжить

```text
Пользователь: «Дальше»
  → агент передаёт однозначный UserDecision и разрешение продолжения
  → BootstrapWork
     short_uow:
       Task.accept_iteration(decision)
       RoutePolicy.next_transition(task facts, explicit process)
       Task.begin_iteration(transition, entry snapshot, authority)
       Session.bind_cycle(...)
       save
     подготовить предусмотренные ресурсы
     вернуть контекст нового этапа
```

Если пользователь сказал «принял, остановись», фиксируется только принятие. Механическая target-публикация, когда она требует отдельной санкции, не выполняется по умолчанию. При последнем принятом этапе Task completion и recompute sprint выполняются библиотекой; модель не вызывает `recalculate`, `release_claim` и `complete_task` вручную.

Если решение пользователя неоднозначно, агент уточняет смысл; библиотека не делает inference по prose. В текущей модели authority — зафиксированная интерпретация команды пользователя доверенным adapter/агентом. Без runtime-данных нельзя обещать криптографически независимую проверку, что такой текст действительно был сказан человеком.

## 8. Пример: осмотр → исправление → повторный осмотр

`InspectHandler` принимает findings/coverage по pinned subject через Task API. Он не изменяет предмет. Task сохраняет findings и предложенный по route edge следующий вид работы. После пользовательской инструкции `RoutePolicy` выбирает `revise`.

`ReviseHandler` добавляет к produce/apply_plan активные findings и требуемые evidence. Task сохраняет ResolutionProposal, не выставляя ему самостоятельное accepted. После успешного verify, доклада и следующей инструкции `InspectHandler` принимает/отклоняет исправление. Новый цикл создаёт новый слой внутри той же task.

Стоимость доработки связывается с конкретными предъявленными результатами и work cycles. Внутренняя самопроверка до выдачи не превращается в quality finding только из-за имени handler. `Metrics` получает классифицированное доменное событие; не выводит её по строке `inspect`.

## 9. Пример: task завершена — sprint актуализирован

Порядок:

```text
принят последний требуемый результат Task
→ Task.complete
→ сохранить task и её immutable final result
→ Sprint.evaluate_progress получает свежие компактные facts
→ готовность successors проверяется по readiness contracts
→ при необходимости начать controlled sprint closure
→ вернуть updated task + sprint summary одним ответом
```

Для чистой локальной finalization допустим один WorkManagement UoW с методами обоих roots. Если требуются внешние проверки/backup, Task completion не откатывается искусственно: Sprint closure остаётся конкретной pending/blocked operation с известной причиной. Нельзя сообщить об успешном закрытии Sprint раньше её завершения.

Отмена Task не означает satisfied dependency. Sprint определяет затронутую ветвь и требуемое решение. Пользователь может отменить ветвь или оставить часть задач с явным изменением зависимости. Автоматический поиск новых моделей/назначение агентов отсутствует.

## 10. Пример: публикация спринта

`PublishSprintPlan` сначала загружает draft и вызывает creation validators каждой будущей Task. Точные methods и обязательные applicability берутся из самих дочерних contracts, не дописываются из полей planner.

Затем в коротком локальном UoW:

```text
проверить исходные revisions и разрешение публикации
→ Task.create(...) для каждого validated contract
→ Sprint.publish(...) с identities созданных задач
→ сохранить roots, owners, initial sections/records, membership/DAG
→ COMMIT
```

Ни одна частично опубликованная Task не видна как eligible. Это одна команда с общей транзакцией, не N вызовов агента и не N последовательно публикуемых task.

Если один контракт невалиден, нет половины опубликованного спринта. Дорогих внешних операций внутри этого UoW нет. Установка среды или Git materialization происходят при работе над выбранной задачей, а не при создании всего плана.

## 11. Пример: проверенный Git-результат

`WorkspacePort` знает точную codebase/worktree из current binding и исполняет только предусмотренную механику. Изменения определяются Git; агент их не регистрирует.

```text
stage-entry/current subject
→ точные проверки
→ receipts для проверенного tree
→ before-commit hooks согласно policy
→ сверка фактического дерева
→ commit при требуемом Git delta
→ сверка commit tree
→ private branch push при требовании project
→ проверка remote receipt
→ Task.mark_verified
```

Если hook изменил дерево, непроверенный результат не признаётся verified. Повторное выполнение затронутых проверок ограничено явным бюджетом. Target integration publish отличается от private push и выполняется координатором publish только после требуемых inspection/authority.

Служебные файлы целевой среды, доступность shared test DB/портов и binding IDE не обеспечиваются одним worktree. Ресурсный adapter обязан либо подтвердить отдельные resources, либо сериализовать конкретный shared resource согласно проекту. DB lock Harness для этого не удерживается. Git worktree имеет отдельные рабочие файлы, но не полностью независимые все repository refs [S13](../sources.md).

## 12. Handoff и discard

Handoff использует Task API для фиксации process position и WIP, WorkspacePort для verified/WIP commit, ArtifactRegistry для сохранных refs и TransferPort для доставки там, где она требуется. Session освобождает текущую formal work только после выполнения договорённых условий сохранности. Внешний транспорт неизвестного исхода остаётся явным pending state; не считается успешным автоматически.

Discard определяет собственный delta текущей попытки относительно durable checkpoint, восстанавливает только его и освобождает binding. Уже записанные содержательные результаты прошлого verify, решения пользователя и чужие изменения не удаляются. Технический journal фиксирует действие; task не получает фиктивный «новый результат», которого агент отказался добиваться.

Обе команды проходят тот же общий command pipeline. Никакого независимого прямого `git reset --hard` в CLI adapter как альтернативы доменному действию.

## 13. События без event-sourcing платформы

Домен может выдавать именованные факты: `IterationVerified`, `IterationAccepted`, `TaskCompleted`, `TaskCancelled`, `TaskReopened`, `FindingRecorded`, `SprintPlanPublished`. Их состав фиксирован типизированным контрактом и содержит стабильные идентификаторы/версии, не произвольный dump всех данных.

Критичные реакции применяются синхронно в текущем use case или его явно продолжаемой operation. Вспомогательные jobs на metrics/materialization/compression сохраняются вместе с intent/result и выполняются идемпотентно. Readiness следующей задачи не ждёт фоновую статистику.

Не вводим отдельный message broker, обязательный event replay всей жизни Task или новую state-machine DSL. SQL текущего состояния и immutable business records — канонические данные; journal не восстанавливает потерянный business meaning.

## 14. Operational journal и Incidents

Journal сохраняется как configured append-only JSONL технической операции вне очищаемого runtime. До внешнего эффекта пишется intent, после него известный outcome/receipt. SQLite operation record фиксирует состояние обработки. При падении между записями операция остаётся незавершённой; journal не заявляет ложный успех.

Это не второй business store: цели, findings, evidence, исправления и решения находятся в SQLite Task/Sprint. Journal содержит refs и техническую последовательность.

Если журнал невозможно записывать, новое внешнее действие не начинается; после уже состоявшегося эффекта сохраняется доступный receipt и возвращается ошибка сохранности. Невозможность писать и в DB, и в journal нельзя превращать в гарантию durable logging. Нужна честная остановка. Отсутствие прав/диска — обычная проблема среды; баг собственной записи — Incident.

Incidents о баге/согласованности Harness привязываются к operation и докладу независимо от автоматического восстановления. Не всякий failed test или отказ внешнего сервиса становится Incident.

## 15. Простые границы ошибок

| Категория | Владелец решения | Реакция |
|---|---|---|
| Неполный result/неразрешённый method ref | Task/verification | До дорогих checks; конкретное исправление input |
| Недостаточное полномочие/не тот этап | Task/Sprint/Session | Отказ без скрытого изменения state |
| Required test не прошёл | verification + Task | Сохранить observation; та же стадия либо предусмотренный feedback |
| Нет execution capability | Execution/Workspace adapter | Blocked с исполнимой инструкцией пользователю |
| Файл за пределами root | Artifacts | Отказ конкретного пути; не переносить его по догадке |
| Неизвестный внешний outcome | Operations + конкретный adapter | Probe или остановка; не универсальный auto-retry |
| Собственный bug/invariant breach | Observability + coordinator | Incident, сохранение известного факта и доклад |

После достижения лимита не заводится бесконечная новая session/operation ради обнуления бюджета. Пользователь может дать явно ограниченное исключение. Корректная остановка является достаточным начальным решением там, где автоматическое восстановление ещё не реализовано.
