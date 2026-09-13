# Пакетный инструмент работы — DDD-04B

Обновлено: **2026-09-14T00:00:00+05:00**. Контракт реализованного API и явно отмеченных согласованных расширений, а не дополнительный workflow DSL.

## Ответственность
`WorkTools.invoke(packet)` — один прикладной вход. Чистая грамматика проверяет пакет; Task принимает содержательные изменения; ArtifactFactory создаёт файлы; InteractionLedger проверяет уникальные события. Нативные операции над кодом/тестами приложения не заменены.

Пакет всегда содержит ровно `operation`, `input`, `messages`. Неизвестные поля/operation отклоняются. JSON CLI отклоняет повторные ключи, NaN и превышение явно заданного размера до начала task-work. Пустые коллекции/null допустимы только в описанных позициях; это явные значения, не попытка найти старый result-файл.

| operation | Обязательные поля input | Эффект |
|---|---|---|
| bootstrap | task, decision, feedback, rework_stage | Для создания — intent с `request_id` и полным task без `id`; для выбора существующей работы — объект только с `id`; затем null |
| task | Поля выбранного action `create`/`edit`/`ready`/`restart` | Создать, изменить, подготовить либо вернуть сломанную незавершённую Task в newborn |
| verify | result, artifacts | Весь результат этапа + любое разрешённое количество генерируемых файлов |
| artifacts | items | Создать и зарегистрировать несколько файлов без завершения этапа |
| show | queries | Прочитать коллекцию объектов текущей задачи |
| sprint | Поля выбранного action | Создать или изменить Sprint и его зависимости/отмены |
| accept | пустой объект | Принять verified результат без автоматического продолжения |
| advance | request_id, task_id, target_stage | Сохранить цель продвижения и пройти обычные переходы до работы, границы ролей, ворот или целевого этапа |
| recover_empty_rework | task_id, reason | Вернуть освобождённую пустую rework-итерацию к непосредственно предшествующему verified result |
| recover_empty_advance | task_id, reason | Вернуть освобождённый пустой этап после ошибочного `continue` к предшествующему verified result |
| recover_missing_worktree | task_id, reason | Восстановить точный удалённый worktree verified/accepted Task из уже интегрированного commit |
| integrate | request_id, task_id, expected_source_commit, expected_target_commit, authorization, resolutions | Интегрировать окончательно принятую Task и убрать её worktree/локальную ветку |
| cancel | reason | Санкционированно отменить текущую задачу без gate completeness |

`cancel` в этом API отменяет текущую standalone Task, сохраняет её историю, результаты и worktree, освобождает claim и записывает явную причину пользователя. Выбранные участники Sprint и Sprint целиком отменяются через пакетный Sprint API, а не серией standalone-вызовов.

### Продвижение к этапу

`advance` принимает устойчивый `request_id`, существующий `task_id` и точный
`target_stage` сохранённого route. Операция сохраняет progression intent в журнале и использует
обычный доменный переход Task; отдельного lifecycle и скрытого autoaccept нет. Точный replay
после остановки процесса или исправления продолжает ту же цель. Изменённая цель с тем же
`request_id`, неизвестный или недостижимый этап, чужой owner и неизвестный `pending` outcome
отклоняются до изменения Task, execution, ownership и progression-журнала.

```json
{"operation":"advance","input":{"request_id":"reach-code-review-1","task_id":"0078","target_stage":"code_review"},"messages":[]}
```

Verified этапы одной роли переходятся последовательно. После каждого перехода
`progression_work_required` останавливает вызов на реальной работе нового этапа. Перед сменой
`executor`/`reviewer` возвращается `role_handoff_required`: текущий этап и ownership не
изменяются. Отправитель сохраняет результат и выполняет публичный `handoff`, получатель из
другой сессии захватывает Task через `bootstrap` и повторяет тот же пакет; только подтверждённая
передача разрешает следующий переход. Когда цель становится текущей, возвращается
`progression_target_reached`; replay не увеличивает версию.

Подтверждение смены роли не привязано к точному приросту версии Task. После сохранённого
handoff допустим только непрерывный суффикс доменных событий владения, начинающийся с
`handed_off`, `handoff_resumed` и при необходимости продолжающийся
`ownership_released`/`ownership_acquired`. Такой суффикс возникает, например, когда
возобновившая handoff сессия проверяющего завершилась, а следующая сессия того же
проверяющего законно захватила освобождённую Task. Он не меняет verified-кандидат,
evidence, Git commit/tree или progression identity и не заставляет `advance` снова
возвращать `role_handoff_required`. Пропуск версии, содержательное событие, другой этап,
не возобновлённый handoff или совпадение отправителя с текущим владельцем не подтверждают
смену роли.

Для Task, уже застрявшей на повторном `role_handoff_required`, не создают новый progression
request и не исправляют БД вручную. Текущий законный владелец освобождает Task штатным API,
если это требуется; назначенный получатель захватывает её через `bootstrap` и повторяет
исходные `request_id` и `target_stage`. После установки исправленной версии тот же replay
проверяет сохранённый ownership-only суффикс и либо выполняет переход, либо возвращает
конкретный действующий gate. Повторный handoff нужен только если сохранённой подтверждённой
передачи действительно нет; он не используется для маскировки постороннего события.

Обычный переход записывает `stage_progressed`, не меняет verified report на `accepted` и не
создаёт событий `user_accept*`. Перед входом в `publish` операция возвращает
`user_acceptance_required` и сохраняет текущий verified этап: продолжить может только отдельное
публичное решение пользователя. Этот статус, как и остановки на работе и передаче роли,
является business-incomplete, а не успехом.

Перед переходом проверяется `pre`-контракт целевого этапа. Неуспех возвращает `broken` с
`blocked_transition` и сохраняет progression target, но не меняет этап, execution или
ownership. После законного rework/restart и исправления входа тот же request продолжает путь.
Операция не исполняет работу этапа, не отправляет сообщения другому агенту и не выдаёт
полномочия на отдельно регулируемые приёмку, публикацию или интеграцию.

Из bootstrap возвращается готовый `result_template`, включающий sections, content additions, trace updates, verification methods, stage_work, evidence_work, commit_message и artifact_paths по действующему контракту. Вычисляемые ID/task/stage/hash агент повторно не передаёт. Прежний transport через редактируемый result_path удалён.

### Просмотр терминальной Task

`bootstrap` с адресом существующей Task в статусе `completed`, `cancelled` или
`superseded` возвращает `terminal inspection snapshot`: обычный контекст с
`result_template=null` и сохранённые проекции `content`, `evidence`, `history`. Это чтение не
возобновляет и не захватывает Task, не требует её worktree и не оставляет её текущей для
сессии. Если сессия уже владеет Task в статусе `active`, `verified` или `accepted`, просмотр
другой терминальной Task отклоняется до изменения binding.

Native hook выполняет такой адресный просмотр через installation source. Сразу после него
безадресный `bootstrap` возвращает `read_only`, а `verify` с `result=null` и `artifacts=[]` —
`read_only_verified`. Дополнительный `show` для получения terminal data не нужен: полный
snapshot уже возвращён адресным `bootstrap`.

До исправления от **2026-09-12** адресный просмотр мог оставить `stale current-task binding`:
следующий taskless bootstrap снова показывал завершённую Task, а null-result verify
отклонялся. Инцидент воспроизведён и закрыт задачей 0050. Исправление не вводит критериев
успеха для аварийной отмены: `cancelled` может не иметь evidence, а просмотр возвращает ровно
то, что было сохранено до отмены.

## Автоматическое создание Task

Новый Task создаётся одним `bootstrap`-пакетом. Вызывающая сторона задаёт устойчивый
`request_id` и полный creation contract без поля `id`; она не читает список файлов, веток или
задач, не вычисляет максимум и не повторяет попытки с самостоятельно увеличенным номером.

```json
{
  "operation": "bootstrap",
  "input": {
    "task": {
      "request_id": "customer-import-creation-1",
      "task": {
        "sprint_id": null,
        "goal_type": "development",
        "goal": "Добавить импорт клиентов.",
        "requirements": ["Импорт использует проверенный формат."],
        "definition_of_done": ["Целевые проверки проходят."],
        "methods": [],
        "method_inputs": [],
        "checks": {},
        "executable_obligations": ["requirements[0]"],
        "decomposition": {
          "kind": "ordinary",
          "phases": [
            {
              "stage": "planning",
              "skills": ["task-planning"],
              "areas": ["docs/plans/**"]
            }
          ],
          "integration": null
        },
        "artifact_requirements": [],
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "evidence_plan": {},
        "stage_contracts": []
      }
    },
    "decision": null,
    "feedback": null,
    "rework_stage": null
  },
  "messages": []
}
```

Это точная форма envelope и creation intent, но вложенные `methods`, `checks`,
`content_contract`, `evidence_plan` и `stage_contracts` должны быть полным контрактом выбранного `goal_type`;
пустые значения примера не объявляются универсально исполнимой задачей. Успешный ответ
содержит фактический `task`, task/worktree roots с тем же ID и квитанцию; persisted branch
формируется из этого ID по `git.branch_template`:

Для каждого executable method полный creation contract также содержит соответствующий
`method_inputs`: существующие baseline-пути перечисляются в `repository_inputs`, а каждый
разрешённый `future output` — в `future_outputs` вместе с producer stage. Preflight сверяет
эти объявления с выбранным base tree, `allowed_paths`, порядком stages и явным
runner/parser profile до allocation Task ID и подготовки worktree. Отсутствующий,
необъявленный или недостижимый путь отклоняет весь creation intent без частичных эффектов;
исправленный контракт отправляется с новым `request_id`.

При ошибке формы ответ указывает положение объекта и отдельно перечисляет отсортированные
`отсутствуют` и `неизвестные поля`; значения полей в сообщение не попадают. Если вместо
объекта передано значение другого типа, ответ сообщает `ожидается объект`. Для automatic
intent этот список относится уже к материализованному контракту, но вызывающая сторона
по-прежнему передаёт его без поля `id`: идентификатор добавляет AI poise. Поэтому исправлять
ошибку нужно по списку отсутствующих полей, а не копированием полного внутреннего контракта.

### Декомпозиция и фокус Task

Каждая фаза процесса явно объявляет skills и areas в `decomposition`; набор фаз должен точно
совпадать с этапами выбранного process snapshot. Классы skills — meta, general и narrow —
задаёт `task_decomposition` выбранного проекта. `meta` и `general` сами по себе не создают
границу ответственности, а `narrow` связывается с одной project-specific responsibility.

Вид Task — `ordinary` или `integration` — определяется до проверки фокуса. Проверка
гарантирует, что обычная Task не объединяет разные narrow responsibilities или несвязанные
маршрутизированные области, даже если они разнесены по разным фазам. Каждая объявленная area,
маршрут которой принадлежит narrow responsibility, должна соответствовать responsibility
объявленного narrow skill; фаза без narrow skill не скрывает такую area. Поэтому разделение
работы по этапам не является способом обойти границу ответственности.

Если цель состоит именно в сборке результатов нескольких компонентов, integration Task обязана объявить component_inputs, combined_result, integration_checks и allowed_paths.
`allowed_paths` покрывает все области её фаз, а остальные поля описывают входы компонентов,
единый результат и проверки их совместной работы. `meta` и `general` Task не требуют
искусственного разделения.

Неизвестные skills и areas без маршрута отклоняются до готовности Task или Sprint. Порядок деклараций не влияет на решение или диагностику: конфликтующие идентификаторы, области и
responsibilities нормализуются и выводятся детерминированно. Проверка устанавливает только
внутреннюю согласованность декларации и не доказывает, что исполнитель перечислил все
фактически необходимые навыки или затрагиваемые области.

```json
{"allocation":{"request_id":"customer-import-creation-1","task_id":"0029","replayed":false}}
```

`request_id` уникален в проекте и неизменно связан с нормализованным creation intent.
Точный повтор из другой сессии возвращает тот же `task_id` с `replayed: true`, не расходует
следующий номер и не перехватывает активный claim. Тот же `request_id` с изменённым intent
отклоняется как digest conflict.

Номер выбирает `TaskRepository` внутри той же UoW, которая создаёт Task и durable reservation
рабочего дерева. Занятые Task/Sprint IDs и explicit IDs полного публикуемого пакета пропускаются;
порядок automatic и explicit элементов не меняет результат. Поддержанный прежний контракт с
явным `id` остаётся допустимым, но агент при обычном создании нового Task использует automatic
intent и никогда не выбирает номер сам.

После ошибки подготовки worktree Task и allocation остаются сохранены с
`pending.kind = worktree_setup`. Точный повтор того же intent сверяет branch, base, worktree и
чистоту: совпавшее частичное состояние завершается, неизвестное изменённое состояние
отклоняется и сохраняется без reset/удаления. Missing/invalid `task_ids` и исчерпанный namespace
дают явную ошибку; скрытого диапазона или fallback к caller-side нумерации нет.

## Жизненный цикл newborn Task

`operation: task` создаёт и редактирует реальную Task до её готовности к запуску. Действия
`create`, `edit`, `ready` и `restart` всегда получают уникальный `request_id`; точный повтор возвращает
тот же результат, а другой intent с тем же ID отклоняется. `create` немедленно закрепляет
постоянный `task_id`, создаёт историю и при необходимости членство в Sprint, но оставляет
статус `newborn`. `edit` требует `expected_revision` и применяет patch к сохраняемому draft.
`ready` проверяет ту же полную creation/DoR-схему и repository inputs, что и обычное создание.

Публичная форма `edit` всегда содержит оба явных поля `patch` и `remove`. `patch` — объект
заменяемых значений, `remove` — список уникальных имён известных полей; один из них может быть
пустым, но не оба одновременно. `null` не означает удаление, отсутствующее поле не получает
скрытого default. Полный intent, включая `remove`, входит в digest `request_id`:

```json
{
  "operation": "task",
  "input": {
    "action": "edit",
    "request_id": "change-0110-to-integration",
    "task_id": "0110",
    "expected_revision": 4,
    "patch": {"goal_type": "integration"},
    "remove": ["executable_obligations"]
  },
  "messages": []
}
```

При смене `goal_type` допустимые и обязательные поля определяет целевой process snapshot.
Поле, запрещённое целевым типом, вызывающая сторона удаляет в том же запросе; без явного
`remove` edit отклоняется и draft не меняется. Неизвестные и повторные имена, отсутствующее
поле, пересечение `patch`/`remove`, удаление `id` или `sprint_id` и удаление поля, обязательного
для целевого типа, отклоняются до сохранения. Успех одной транзакцией сохраняет прежние Task
identity, Sprint membership и history, выбирает целевой process, увеличивает revision и
оставляет обычные ownership/optimistic guards. Точный replay возвращает исходный результат;
другой список `remove` с тем же `request_id` считается другим intent и отклоняется.

Создатель получает newborn Task через общий ownership API. Переключение на другую Task
освобождает прежний Task claim, но не снимает независимо принадлежащий worktree; запись
чужого живого владельца отклоняется; до выбора `goal_type` маршрут отсутствует. После выбора
сохраняется snapshot соответствующего процесса и появляется его entry route, но Task остаётся
`newborn`, пока контракт не пройдёт readiness. Standalone Task после `ready` становится
`available`; готовый участник draft Sprint остаётся newborn до атомарной публикации Sprint.

Если сохранённый контракт исполнения не позволяет достичь DoD либо следующий stage не
проходит собственный DoR, результат обозначается `status: broken`, а не успешным завершением.
Ответ содержит точную failure phase и два явных пути: reviewer исправляет только дефектный
stage contract через `repair_stage_contract`, либо владелец выполняет `operation: task` с
`action: restart`, `expected_version`, непустыми `reason` и `authorization`. Restart разрешён
только для незавершённой неинтегрированной Task (`available`, `active`, `verified`, `accepted`),
сохраняет её ID, Sprint membership, immutable history, branch, worktree и tracked,
staged/untracked WIP. Текущее исполнение сбрасывается согласованно: attempts становится нулём,
publication и last_report очищаются. Task возвращается в `newborn`, после `edit`/`ready`
возобновляется в прежнем worktree. Чужой живой owner, terminal status, stale version и pending
external outcome отклоняют операцию до мутации; pending сначала завершается своим явным
recovery protocol. Идентичный replay возвращает первоначальный receipt.

Перезапуск атомарно инвалидирует все текущие записи `work-packet identity` этой Task в той же
Unit of Work, которая возвращает lifecycle в `newborn`. Эти записи — изменяемый
индекс текущей доставки, а не аудит: ранее сохранённые `submissions`, `task_results`, `evidence`
и Task `history` не удаляются и не перезаписываются. Если invalidation или любой последующий
шаг restart завершается ошибкой, вся транзакция откатывается. После `edit`/`ready`
свежий результат можно сохранить, даже если новый маршрут снова использует те же `stage`
и `iteration`, что и до перезапуска.

Существующая non-newborn Task возвращает точное текущее поле `version` через `bootstrap` и текущую проекцию `show`. Приватное имя `_version` не входит в публичный DTO. Taskless-ответы не содержат `version`, а newborn Task использует `revision`. Передавайте это значение без изменений как `expected_version` для защищённых `restart` и исправления stage contract; при конфликте версии заново прочитайте публичный `bootstrap`/`show`, а не угадывайте значение.

```json
{
  "operation": "task",
  "input": {
    "action": "restart",
    "request_id": "restart-0079-v1",
    "task_id": "0079",
    "expected_version": 12,
    "reason": "The saved execution contract cannot reach the next stage.",
    "authorization": "The user authorized recovery of this unfinished Task."
  },
  "messages": []
}
```

Это дополнительный путь подготовки, а не второй вид Task: одна identity, история и ownership
сохраняются при переходе. Поддержанная прямая полная creation остаётся без изменений и сразу
создаёт `available` Task после type-specific DoR. Массовой миграции прежних записей нет.
Legacy embedded Sprint definitions превращаются в реальные newborn Task только явной
Sprint-операцией `materialize_tasks`; сохранённые historical replacement relations и creation
request остаются читаемой частью traceability, но новые replacement relations не создаются.

## Verify

Обязательный порядок работы агента: [bootstrap в начале и verify перед завершением](../governance/development-rules.md#начало-и-завершение-работы). `read_only_verified` завершает сессию без содержательных проверок и не удостоверяет правильность ответа.

1. Проверить форму пакета и событий; наблюдение сообщения сохранить как факт взаимодействия.
2. Построить весь план файлов, проверить scope/ссылки/template/content существующих файлов.
3. Проверить семантическую форму кандидатного Task submission до создания новых файлов.
4. Материализовать файлы, объединить их пути с явно переданными существующими путями без повторов.
5. Передать кандидат штатному Task/Content/Evidence pipeline. Новые методы и секции уже участвуют в этом verify.
6. Выполнить проверки/CONTINUE/публикацию по прежним правилам. Записать verified packet receipt, когда результат verified.
7. Вернуть контекст сообщения и результата; не начинать следующий содержательный этап.

Content pre-gate может отклонить уже сохранённый кандидат после генерации файлов. Это ожидаемая неполнота работы, не фиктивная атомарность файловой системы и БД. Файлы принадлежат правильному владельцу, не считаются verified evidence сами по себе; одинаковый повтор использует те же файлы. Сломанный payload/недопустимый исходный путь отбрасывается до генерации.

Код изменён при прежнем payload: создаётся новое исполнение checks, не повторный содержательный слой. Точный
replay текущего verified пакета идемпотентно возвращает прежний результат; удалённые runtime-файлы
не создаются заново. Другой result или другой набор artifacts после доклада отклоняется через ожидаемый
`PoiseError` и требует явного rework; внутреннее исключение типа `NameError` не является доменным отказом.

### Изоляция временного Git index

При вычислении текущего дерева временный Git index каждого snapshot-вызова изолирован:
AI poise создаёт принадлежащий только этому вызову каталог, передаёт его дочерний index
через `GIT_INDEX_FILE` и после синхронного завершения удаляет ровно этот каталог. Поэтому
последовательные и одновременные вызовы одной сессии не делят один lock, а реальный index
репозитория не изменяется.

Оставшийся после аварии каталог отдельного вызова не блокирует следующий snapshot. Агент
не удаляет `snapshot.index.lock` вручную и не пытается определять его владельца по inode:
старый фиксированный lock и каталоги других вызовов не принадлежат текущей операции и
сохраняются. Если Git сообщает настоящий внешний конфликт, сохраняют диагностику и
устраняют владельца через его штатный lifecycle, а не удалением чужого lock-файла.

### Текущий реестр методов проверки

Создание Task передаёт полный объект начальных `checks`, а не скрытое шаблонное
расписание. Пустое начальное расписание `{}` — завершённый явный контракт, а не запрос
на автоподстановку. Оно не требует при создании знать будущие test methods, файлы-артефакты
или RED/GREEN расписание. Непустое начальное расписание при создании валидируется целиком.

На `verification_planning` вызывающая сторона формирует фактические методы и их расписание, после
чего один `method_additions` пакет защищённо меняет current projection. Только тогда команды и
их source provenance могут ссылаться на созданные до этого этапа тесты и иные future outputs.

Для процесса с исправлением `test_registry` creation contract Task обязан содержать
`executable_obligations`: явный список уникальных ссылок вида `requirements[N]` и
`definition_of_done[N]`. Это только обязательства, которым действительно нужно исполняемое
доказательство; документационные, review- и операционные пункты не попадают в список
автоматически. Неизвестные ссылки отклоняются. Пустой список является явной пустой
классификацией только в схеме, где это поле существует.

Наличие поля определяет закреплённый process schema, а не имя goal type и не содержимое
requirements/DoD. Для процесса без inspection-маршрута, владеющего `test_registry`, явное
представление пустой классификации в creation contract — отсутствие поля:
`executable_obligations: []` там является неизвестным полем и отклоняется. Task не выводит
скрытые обязательства, а создаёт пустое состояние реестра; поэтому публичный
`verification_registry` возвращает `executable_obligations: []`. Отсутствие поля в Task schema
и пустой список в registry projection относятся к разным слоям одного явного контракта, а не
к compatibility fallback.

У repository-метода пустой `verification_plan.change_surface` допустим только для guard
предсуществующего состояния: `red_stages` пуст, `green_stages` точно равен `["baseline"]`, а
`baseline` является entry текущего route. Такой метод запускается до изменений и не заявляет
ложную произведённую поверхность. Repository-метод GREEN результата после route entry обязан
иметь непустую поверхность, которую покрывает `allowed_paths` его GREEN-этапа. Отдельное
разрешение пустой поверхности для явно объявленного external source сохраняется.

На этапах, владеющих секцией `test_registry`, поле `method_additions` принимает один
защищённый пакет текущей проекции с точными полями `request_id`, `expected_revision`,
`operations`, `executable_obligations`. Операции `add`, `replace`, `reschedule` и `remove`
применяются атомарно; неизвестный метод, повтор одной цели, semantic duplicate, конфликт
ожиданий, dangling relation или неверная revision отклоняют весь пакет. Полный payload,
включая текущую классификацию обязательств, участвует в digest идемпотентного `request_id`.
На этапах без `test_registry` та же мутация запрещена.

Изменяется только current projection. Перед каждой принятой мутацией реестр сохраняет
неизменяемый snapshot прежней revision; execution identity, observations, receipts и
предыдущие submissions не удаляются и не переписываются. `show` с
`kind=verification_registry` возвращает текущую revision, `current`, `history`, `requests` и
`executable_obligations`.

Выход из test-inspection разрешён только при непустом текущем наборе GREEN-методов с
`evidence_kind=executable_test`, который совокупно покрывает все текущие
`executable_obligations` через явные `covers`. Один узкий тест достаточен, если он реально
проверяет все объявленные исполняемые обязательства; устаревший метод и неограниченный полный
набор тестов не требуются.

Текущая классификация сохраняется в registry state и при загрузке валидируется тем же
`CheckRegistry`, что создание и мутация. Для уже активной Task без нового поля в неизменяемом
creation contract используется только явно сохранённый registry state; отсутствующее или
некорректное состояние завершается ошибкой. Миграции, совместимого fallback и ручного
редактирования Task DB нет.

### Явный rework после `checks_failed`

Перед rework `pending=checks` не всегда означает действительно неизвестный исход. Если для
текущих submission, stage, iteration, tree и execution key уже сохранён полный набор
terminal receipts, публичным recovery служит точный replay исходного `verify` packet. Replay не выполняет
checks повторно: он требует точного payload digest, полного однозначного соответствия методов и
receipt ledger, существующих stdout/stderr с совпавшими digest и известных terminal exit codes.
При успешной сверке Task и execution меняются атомарно: добавляется
`pending_checks_recovered`, а `pending` очищается. Failed batch возвращает обычный
`checks_failed`; passing batch продолжает штатный evidence pipeline. Последующий точный replay
идемпотентно возвращает сохранённый результат.

Если хотя бы один receipt отсутствует, дублируется, относится к другой provenance, имеет
неизвестный outcome либо изменённый output, recovery отклоняется до мутации и checks не
запускаются. Сначала требуется установить внешний исход; `rework` не используется как обход.

`checks_failed` оставляет Task с submitted результатом активной и сохраняет неизменяемый batch receipts. Следующий пользовательский ход может явно вернуть ту же Task на разрешённый этап через существующий `bootstrap`:

```json
{
  "operation": "bootstrap",
  "input": {
    "task": null,
    "decision": "rework",
    "feedback": "Исправить причину неуспешной проверки.",
    "rework_stage": "test_remediation"
  },
  "messages": []
}
```

Такой переход допустим только для текущих stage, iteration, submission digest, Git tree и
`execution_key`. Verify и этот поиск failed batch получают invocation и ключ через один
канонический builder: точный метод с ожиданиями и observation rules, cwd/env и разрешённая
source provenance участвуют в одной identity. Source-less или совместимого второго ключа нет.

Сохранённый batch должен содержать хотя бы один неуспешный guard либо один
неинтерпретируемый observation receipt. Каждый receipt обязан
полностью совпадать со своей неизменяемой записью evidence ledger, точно соответствовать
текущему invocation и tree, иметь известный неотрицательный exit code, не быть timeout и
иметь логически корректные control flags; его `stdout`/`stderr` должны существовать с сохранёнными digest.
`pending` должен быть null. Поэтому отсутствующий batch или ledger receipt, batch без
неуспешного guard или неинтерпретируемого receipt, прерванный/неизвестный исход, изменённые method/expectations/provenance,
повреждённый receipt, удалённый либо изменённый output и evidence другого stage, iteration,
submission, tree или запуска не дают права на rework. Отказ происходит до изменения route,
lifecycle и execution state.

Поиск не использует fallback, dual key, legacy alias или tolerant reconstruction. Старые
неканонические receipts и ключи не мигрируются и не получают совместимое прочтение: они явно
не подходят под точный контракт.

`feedback` обязателен и сохраняется в истории. `rework_stage` должен входить в `rework_targets` текущего этапа; null означает сам текущий этап. Точный terminal failed batch сам является основанием для возврата на любой разрешённый этап, в том числе с обработчиком `revise`, и не требует фиктивной открытой reviewer finding. На таком `revise` пустой список `resolutions` допустим только при отсутствии открытых findings; если findings есть, сохраняется требование предложить ровно по одному исправлению для каждой. Task применяет обычный `Route.enter`: история переходов и посещений увеличивается, но не ограничивает исполнение; `allowed_paths` целевого этапа продолжает действовать. Проверка `pending`, доменный переход Task и очистка execution state согласованы в одном UoW: отказ предшествует любому изменению lifecycle или execution.

Успех сохраняет Task ID, worktree, branch, task/process contracts, прежние submissions, историю и failed receipts. Создаётся новый visit целевого этапа с его iteration, записываются feedback и событие `user_failed_check_rework`, а заброшенное retry-состояние очищается: `attempts=0`, `publication=null`, `pending=null`, `entry_tree` становится текущим деревом. Операция не отменяет и не пересоздаёт Task и не является SQL-восстановлением или обходом конфигурационного hash. Для verified/accepted/completed результатов действует прежний rework предъявленного результата; этот путь относится именно к активному submitted этапу с точным failed batch.

### Rework при нерассмотренных исправлениях

Ни обычный rework проверенного результа, ни failed-check rework активного submitted этапа нельзя использовать, чтобы обойти независимый осмотр
уже предложенных исправлений. Если `FeedbackBook.pending_resolutions` не пуст, Task отклоняет
rework до изменения status, stage, iteration, visits, transitions, feedback, submission,
execution state или ownership. Ошибка перечисляет только ещё не рассмотренные resolution ID и
называет обязательный inspection-этап штатного маршрута; если failed batch относится к уже submitted inspection-этапу, этим этапом остаётся он сам. Ранее принятые решения остаются историей
и в перечень не попадают. Точный повтор даёт тот же отказ без новых событий и посещений.

Агент продолжает Task обычным `decision=continue`, проверяющий принимает или отклоняет каждое
ожидающее исправление на названном inspection-этапе, и только после этого можно запросить
разрешённый rework. При пустом `pending_resolutions` прежняя семантика rework не меняется. Это
устраняет корневой дефект Task 0063 RD-013; аварийное восстановление ниже остаётся только для
состояний, созданных старой версией, а не штатным обходом нового запрета.

## Восстановление ошибочно открытой пустой rework-итерации

Перед этим узким lifecycle recovery доступна независимая installation-owned операция
`recover_missing_worktree`. Она нужна, если незавершённая Task остаётся `verified` или `accepted`,
но её зарегистрированные branch и worktree были ошибочно удалены после интеграции проверенного
commit. Операция не меняет ownership, Task version, stage, iteration, result или handoff.

```json
{
  "operation": "recover_missing_worktree",
  "input": {
    "task_id": "0082",
    "reason": "Восстановить точный интегрированный verified source для продолжения проверки."
  },
  "messages": []
}
```

Вызов выполняется из текущего installation source idle-сессией либо текущим владельцем ровно этой
Task. Idle-сессия может восстановить source при другом владельце, потому что ownership не
передаётся и не освобождается; владелец другой Task получает отказ. Сохранённый `last_report.commit`
обязан существовать, быть предком текущего настроенного `base_ref`, а tree commit обязан точно
совпасть с сохранённым `verified_tree`. Путь обязан быть точным task path под настроенным state,
а сохранённая branch — валидной и либо отсутствовать, либо указывать на тот же commit. Только
после этих проверок создаются branch/worktree; итог дополнительно проверяется как clean exact
commit/tree и как зарегистрированный worktree именно настроенного repository. Отдельный clone или
embedded repository в том же path отклоняется. При сбое финальной проверки или audit операция
удаляет только созданный ею worktree и только созданную ею branch; предсуществующая branch
сохраняется. Повтор над уже точным worktree возвращает `replayed: true`. Любой конфликт пути,
ветки, commit, tree или integration ancestry отклоняет операцию без изменения Task. После
восстановления текущий владелец может выполнить обычный handoff, а получатель — native bootstrap.

`recover_empty_rework` — узкая публичная аварийная операция, а не общий rollback. Она нужна,
когда после verified result по ошибке уже открыт следующий `user_rework`, но в новой итерации
ещё нет submission или иных результатов и Task освобождена штатным handoff. Операция запускается
из текущего installation source до выбора старой Task, поэтому не зависит от исходного кода в её
worktree.

```json
{
  "operation": "recover_empty_rework",
  "input": {
    "task_id": "0082",
    "reason": "Пользователь разрешил убрать ошибочно открытую пустую итерацию и вернуть предыдущий verified result к осмотру."
  },
  "messages": []
}
```

Вызов допустим только из сессии без текущей Task. Адресованная Task должна оставаться `active`,
не иметь владельца, а весь event suffix после исходного пустого перехода должен состоять только
из разрешённых ownership events. Освобождение может быть выполнено handoff либо обычной
ownership-операцией; отдельный handoff пустой итерации для recovery не обязателен.
`pending` и `publication` равны null, `attempts=0`, `entry_tree` и неизменившееся фактическое Git
tree совпадают с `verified_tree` непосредственно предшествующего сохранённого отчёта.

Если worktree существует, его фактический tree проверяется как прежде. Если worktree отсутствует
после уже завершённой интеграции и штатной уборки ресурсов, recovery не создаёт его заново и не
доверяет одной записи в Task DB. Сохранённый `last_report.commit` должен существовать в Git, быть
предком текущего настроенного `base_ref`, а tree этого commit обязан точно совпасть с сохранённым
`verified_tree`. Только это доказательство заменяет снимок удалённого worktree. Отсутствующий,
неинтегрированный или не совпадающий commit приводит к отказу до изменения Task.

Предыдущая точка восстанавливается только из неизменяемых записей одного latest verified
submission: result, workflow layer и proof layer. Исходным переходом служит последнее точное событие
`user_rework`; после него и до текущей версии Task разрешены только `handed_off`,
`handoff_resumed`, `ownership_acquired` и `ownership_released`. Поэтому повторные циклы передачи
не мешают восстановлению, но любой другой lifecycle event после исходного перехода приводит к
отказу без изменения Task. Сохранённые feedback и evidence также не могли измениться. Обработчики
внешних действий `apply_plan` и `publish` этим путём не восстанавливаются. Уже созданный submission,
изменённое дерево, занятая Task, потерянный proof, несколько одинаковых submission digest или любое
другое неоднозначное состояние дают отказ до записи.

Успех атомарно возвращает прежние stage, iteration, status=`verified`, route progress, feedback,
evidence и ссылку на точный current submission. Новая пустая итерация не удаляется из истории:
добавляется событие `empty_rework_recovered`. Если пустой итерации соответствует сохранённый
handoff со статусом `released` или `resumed`, он получает состояние `recovered`, чтобы следующий
проверяющий не пытался возобновить устаревшую передачу. Несовпадающий более ранний handoff не
используется как источник истины и не блокирует recovery. Прежние submissions, task results, receipts, findings и resolutions не
переписываются. Повтор после подтверждённого успеха ничего не откатывает ещё раз и получает отказ
из-за уже восстановленного состояния Task; фактический статус следует проверить публичным чтением Task.

Для уже застрявшей Task 0077 сначала применяется этот узкий legacy-recovery: Task должна быть
освобождена, её ошибочно открытая rework-итерация должна оставаться пустой, а
`recover_empty_rework` вызывается с `task_id="0077"` и явной причиной. После ответа
`status=recovered` агент выбирает 0077 обычным `bootstrap`, выполняет `decision=continue` и
передаёт сохранённый resolution на указанный inspection-этап. Прямое чтение или изменение Task
DB не требуется и запрещено. Если любое предусловие recovery не выполнено, операция ничего не
меняет и сообщает точную причину; создавать ещё одну обходную Task не следует.

### Пустой переход после `continue`

`recover_empty_advance` использует те же запреты на занятую Task, submission, изменение tree,
pending execution, внешнее действие и неоднозначную историю, но требует точное исходное событие
`user_accept_and_continue`. Операции не взаимозаменяемы: rework-origin отвергается advance-вызовом,
а continue-origin — rework-вызовом. Поиск выбирает последнее такое содержательное событие, а после
него допускает только тот же точный набор ownership events. Поэтому повторные `resume` и `handoff`
без новой работы разрешены, но любой иной lifecycle event приводит к отказу без изменения Task.

```json
{
  "operation": "recover_empty_advance",
  "input": {
    "task_id": "0088",
    "reason": "Ошибочно выбран continue; замечание требует другого writable remediation stage."
  },
  "messages": []
}
```

При обычном `continue` execution-проекция предыдущего отчёта получает status=`accepted`, хотя
неизменяемый `task_result` остаётся verified. Recovery требует их точного совпадения с этой
единственной разницей, затем атомарно возвращает status отчёта и Task в `verified`, восстанавливает
current submission и добавляет `empty_advance_recovered`. После этого агент может применить уже
известное reviewer feedback через обычный verified-result `rework` в правильный разрешённый stage.

## Интеграция принятого результата

`integrate` — одна публичная операция для локальной интеграции окончательно принятой Task и последующей уборки. Она доступна только когда Task имеет статус `completed` и сохранённый итоговый commit. Сохранённый `accepted commit` неизменяем; последующие commits изменяемого `integration head` создаются на уже существующей task branch в уже существующем task worktree. Операция не создаёт отдельные integration branch или worktree и запускается из текущего установленного исходного кода AI poise.

Синхронизация с текущим `master`, merge и разрешение конфликтов происходят только в task worktree. При конфликте операция сохраняет его точное состояние и возвращает `awaiting_resolution`; агент редактирует и разрешает только перечисленные конфликты там. На разрешённом integration head операция выполняет все текущие registry methods с непустыми `verification_plan.green_stages` и `verification_plan.change_surface`, независимо от пустого расписания терминального этапа; RED и baseline-only guards исключены. Непосредственно перед публикацией операция захватывает общий для target lock и повторно читает `master`. Если target сдвинулся, она автоматически повторяет update → agent resolution при конфликте → checks. Стабильный target публикуется в основном checkout только командой `git merge --ff-only <task-branch>`; прямое обновление target ref и force update запрещены.

Основной checkout и любой чужой WIP не используются для подготовки или разрешения конфликтов. Операция не выполняет над ними `stash`, `reset`, `restore`, `checkout`, `clean`, staging, commit или удаление; единственный публикационный эффект — `merge --ff-only`. Грязное либо незавершённое состояние checkout может заблокировать его. Тогда receipt содержит доказательство совпадения до и после для `HEAD`, branch binding, index, tracked/untracked paths, содержимого, типов, режимов и Git operation state. `expected_source_commit` закрепляет accepted commit, а `expected_target_commit` — исходное наблюдение вызывающей стороны.

Persisted integration state обеспечивает crash recovery и идемпотентный повтор: отдельно сохраняются accepted commit, mutable integration head, наблюдавшийся target, conflict state, проверки, публикация и уборка. Повтор с тем же intent продолжает подтверждённую незавершённую фазу и не воспроизводит вслепую уже подтверждённые Git-эффекты.

После подтверждённого fast-forward или terminal no-op эта же операция удаляет task worktree, task branch и зарегистрированные временные backup-файлы задачи. Безопасность удаления owned-ветки проверяется относительно текущего target, а ref удаляется compare-and-delete по наблюдавшемуся commit. Потомок опубликованного integration head не блокирует уборку, но переписанная либо посторонняя история target сохраняет recovery state. Временные backup разрешено создавать только в task/integration-scoped каталоге настроенного runtime root; `integrated` недоступен, пока каталог не пуст. Предсуществующие, чужие, операторские, deliverable и unfinished-recovery backups не удаляются.

Полный локальный цикл выше проверен на повторном использовании task branch/worktree, конфликтах, drift target, checks rerun, отказе `ff-only` без изменения checkout, crash/replay и cleanup. Точная сохранённая legacy-форма blocked-запроса `integrate-0048-1` переводится в этот цикл с сохранением accepted commit и прежней истории; другие старые формы отклоняются. Контракт не включает push, удалённую публикацию или multi-codebase integration.

```json
{
  "operation": "integrate",
  "input": {
    "request_id": "integrate-0016-1",
    "task_id": "0016",
    "expected_source_commit": "0123456789abcdef0123456789abcdef01234567",
    "expected_target_commit": "89abcdef0123456789abcdef0123456789abcdef",
    "authorization": "Integrate accepted task 0016",
    "resolutions": []
  },
  "messages": []
}
```

Команда выполняется через уже выбранный session-scoped launcher; прямой CLI при отсутствии
native Codex identity использует `POISE_CONFIG` и сохраняемый `POISE_CALLER_BINDING`:

```bash
bash "${AI_POISE_WORK:?Set the launcher path supplied by the native hook}" <<'JSON'
{"operation":"integrate","input":{"request_id":"integrate-0016-1","task_id":"0016","expected_source_commit":"0123456789abcdef0123456789abcdef01234567","expected_target_commit":"89abcdef0123456789abcdef0123456789abcdef","authorization":"Integrate accepted task 0016","resolutions":[]},"messages":[]}
JSON
```

Точные commit IDs берутся из проверенного результата Task и наблюдаемого target HEAD; примерные SHA выше нельзя копировать как фактические значения. Одинаковый пакет повторяется безопасно. После `integrated` допускается также передать выданный `target_after` как наблюдаемый `expected_target_commit`: это terminal replay, а не новая интеграция. Другие изменения intent с тем же `request_id` отклоняются.

Статусы и восстановление:

- `integrated`: `master` безопасно fast-forward-нут либо terminal no-op доказан; task worktree, task branch и scoped temporary-backup directory удалены; повтор возвращает сохранённый результат с `replayed: true`;
- `awaiting_resolution`: task worktree оставлен в точном conflict state; исправьте только перечисленные файлы в нём и повторите тот же пакет с одним `resolutions`-объектом для каждого conflict path;
- `blocked`: `candidate_failed` сохраняет receipt неудачного update/commit; `checks_failed` сохраняет check receipts без публикации; `publication_failed` сохраняет причину отказа `ff-only` и доказательство неизменности main checkout. Тот же intent повторяет только допустимую фазу;
- `cleanup_pending`: публикация подтверждена, но уборка task worktree, task branch или scoped temporary backups не завершена. Тот же пакет повторяет только недостающие шаги. Текущий target должен содержать опубликованный integration head и accepted commit; безопасный потомок допускается, переписанная история отклоняется.

Пример продолжения конфликта — все остальные поля исходного intent остаются прежними:

```json
"resolutions": [
  {
    "path": "src/example.py",
    "resolution": "Сохранено принятое поведение source и совместимый target API."
  }
]
```

Операция сама управляет staging, integration commit, повторной проверкой target и поэтапной уборкой внутри task worktree. Пользователю не требуется обслуживать его index или основной checkout. При конфликте повтор допускает только сохранённое состояние интеграции и объявленные conflict paths. Если Git не смог безопасно продолжить, accepted commit и восстановимое integration state сохраняются, успех не записывается. Публикация использует сериализованный `merge --ff-only`, а удаление task branch — compare-and-delete; force-update и безусловное удаление ref не используются.

Состояние и полная история переходов читаются пакетным query после перезапуска процесса:

```json
{"operation":"show","input":{"queries":[{"id":"result","kind":"integration","task_id":"0016","request_id":"integrate-0016-1"}]},"messages":[]}
```

Ответ сохраняет `source_commit`, `accepted_commit`, `task_branch`, `task_worktree`, `last_included_target`, `integration_head`, `target_before`, `target_after`, check/failure/publication receipts, conflict resolutions, поэтапный `cleanup` и `history` переходов. Task/Sprint history и прежний проверенный результат не переписываются.

## Создание файлов
```json
{"scope":"task","path":"result.md","source":{"kind":"template","id":"note","version":"1","values":{"title":"Результат","body":"Проверенный итог"}}}
```
```json
{"scope":"sprint","path":"notes/shared.md","source":{"kind":"text","text":"Общий результат"}}
```

`path` — относительный путь внутри явно заданного для scope artifact subdirectory. Пользователь выбирает уровень, инструмент разрешает его в текущий owner root. Нет task/sprint binding — соответствующий уровень недоступен. Полный фактический путь возвращается в ответе. Runtime предназначен только для временной работы.

Создание и регистрация отдельной `artifacts`-операцией — тоже один пакет, IDs/hash не вводятся агентом. В `verify` тот же список передаётся в `input.artifacts`; отдельный generate/register call не требуется. Готовый файл другого инструмента передаётся только путём в `result.artifact_paths`.

Точное совпадение пути/содержимого повторно использует файл; другое содержимое по тому же пути отклоняется. Для новой версии результата использовать новый явный путь. Конфликтующие элементы пакета, escape и symlink запрещены. Все destinations preflight-ятся до первого нового файла. В I/O-сбое сохранённые файлы не удаляются вслепую: сообщение перечисляет известные пути, одинаковый повтор безопасен. Это не гарантия all-or-nothing публикации многотомного filesystem.

Текст и шаблоны UTF-8 реализованы. Готовые бинарные файлы поддерживаются прежней path-only регистрацией; binary генератор/семантическая классификация не добавлены. Требование количества считает уникальные файлы по scope/pattern, не test-cases внутри файла.

## Пакетное чтение
Каждый query имеет собственный `id`. Поддержаны `task`, `messages`, `content`, `evidence`, `section`, `trace`, `sprint`, `work_overview` и другие явно описанные ниже виды.
```json
{"operation":"show","input":{"queries":[{"id":"state","kind":"task"},{"id":"report","kind":"section","name":"report","stage":null,"submission":null,"range":{"unit":"lines","start":1,"end":10}},{"id":"cost","kind":"messages"}]},"messages":[]}
```

Section query явно содержит name/stage/submission/range. Null stage/submission означает текущую проекцию; null range — явно настроенный initial_read_lines. Строки — 1-based inclusive; байты — 0-based half-open по UTF-8. Границы не разрезают символы; такой byte-запрос отклоняется с предложением использовать строки. Конец диапазона ограничивается реальным концом данных. В ответе total_lines/total_bytes, returned_lines/returned_bytes. Пустая секция — пустой текст и [0,0].

Это единый запрос чтения, но не обещание одного SQL snapshot всех разных read-models. Одна задача одновременно не изменяется несколькими агентами по принятой модели оркестрации.

### Обзор опубликованных спринтов и самостоятельных задач

`work_overview` возвращает две независимые проекции через публичный `show`, без доступа клиента к SQLite или managed-файлам:

```json
{"operation":"show","input":{"queries":[{"id":"all-work","kind":"work_overview","sprint_statuses":null,"standalone_task_statuses":null}]},"messages":[]}
```

У пустого проекта значение этого query имеет точную форму:

```json
{"sprints":[],"standalone_tasks":[]}
```

`sprints` содержит полные DTO опубликованных Sprint в том же формате, что `kind: sprint`, `view: current`; `standalone_tasks` содержит объекты точной формы `{"id":"...","status":"...","goal":"..."}` только для Task с `sprint_id: null`. Участники Sprint во второй список не попадают. Каждый список упорядочен по своему ID по возрастанию.

Фильтры обязательны как поля query и действуют независимо после построения владельцами своих проекций:

- `null` — вернуть все элементы соответствующего списка;
- `[]` — вернуть пустой соответствующий список;
- непустой список — оставить статусы из явно переданного уникального набора.

Допустимые статусы Sprint: `planned`, `active`, `completed`, `cancelled`, `blocked`. Допустимые статусы standalone Task: `available`, `active`, `verified`, `accepted`, `completed`, `cancelled`. Неизвестный статус, повтор статуса, отсутствующее или лишнее поле отклоняют весь пакет.

Query не поддерживает pagination и не обещает один cross-domain SQL snapshot: Sprint и Task читаются через разные owner read-models. Входные коллекции ограничены настроенным `batch.max_items`. Полный результат сохраняется в task receipt; когда он не помещается в `limits.output_chars`, CLI возвращает ограниченный JSON с `response_path` и `details: "full_result"`, а не обрезанный список.

## Пользовательское сообщение
```json
{"conversation_id":"conversation-1","message_id":"turn-7","occurred_at":null,"reason":"continue","subject":null}
```
Источник задаётся конфигом. IDs должны быть стабильны у источника, даже при повторных вызовах и restart. В `agent_reported` режиме агент назначает стабильное имя видимому сообщению и повторно использует его; инструмент не умеет восстановить ненаблюдаемую историю ChatGPT.

Причина — одно явное значение словаря либо null. Subject — известная ссылка либо null. Неизвестная причина не угадывается. Повтор события с другими данными отклоняется до применения пакета. Полный transcript не записывается. Task/goal/stage/iteration назначаются текущим binding, а не из повторно введённых агентом IDs.

Событие сохраняется даже если последующий verify не прошёл: это фактический расход взаимодействия. Поэтому отказ Task не откатывает ledger. Первичное связывание сообщения с task неизменно; read/retry не переносит расход на другую task. Подготовительное ad-hoc-событие может получить первый task binding в последующем bootstrap.

## Выдача
`work` возвращает валидный JSON, не обрезанный посреди объекта. Лимит включает весь ответ. Действующий Task response сохраняется под её root; краткий ответ содержит ссылку на полное содержание. Слишком маленький output budget отклоняется до создания worktree. Exit 0 — получен штатный результат, в том числе `awaiting_continuation`; exit 1 — проверки/обязательства ещё не выполнены; exit 2 — неправильный вход/конфигурация. Это не вывод о наличии бага AI poise по любому non-zero.

Обычный read-only taskless verify принимает result=null/artifacts=[] и очищает runtime. Taskless создание постоянного deliverable не реализовано этим срезом; formal task нужна для durable task-result.


## Sprint в DDD-05
Обновлено: **2026-09-13T08:10:00+05:00**. `operation: sprint` принимает actions `draft`, `materialize_tasks`, `publish`, `dependencies`, `cancel_tasks`, `cancel` и `waive_dependencies`; точные поля и примеры описаны в [Sprint API](sprints.md). Bootstrap с `task: {"id": "..."}` определяет существующий Task/Sprint по registry, не по префиксу. Прямая новая задача вне Sprint содержит `sprint_id: null`; задачи Sprint materialize получают ID до публикации и выбираются по ID. Сломанная опубликованная Task исправляется под тем же ID через `operation: task`, `action: restart`.

Поддержанные read projections не требуют SQL или чтения managed-файлов: `show` с `kind: sprint` и view `current`, `plan` либо `history` показывает текущий граф, сохранённый process/plan snapshot, revision layers и historical replacement relations. Публичный action `replace_task` удалён; старые `superseded` Task и их evidence по-прежнему доступны для read-only inspection.

## DDD-06: внешние планы в том же пакете
`stage_work` обработчика apply_plan принимает plan, phase, resolutions и finding_resolutions. Publish принимает target_ref, expected_commit и authorization. `awaiting_action_continuation` возвращает template для CONTINUE; `action_failed/action_blocked` возвращают nonzero business outcome, а не фиктивный PASS. Подробности: [Actions](actions.md).

Пакет messages не фильтруется по read-only show или reason. Все события текущей работы учитываются с дедупликацией. Это изменение привязки, не автоматический доступ CLI к сообщениям модели.

## DDD-07: новые операции и транспорт

Обновлено: **2026-09-07T00:07:53+05:00**. `handoff` в общем work packet принимает request_id/reason/result/commit_message/artifact_paths. Только paths для готовых файлов. Resume — обычный bootstrap существующей задачи другим actor. `show.queries` принимает kind=tool_result с receipt_id/representation/range; доступ ограничен текущей задачей.

`poise runtime --settings <file>` добавляет внешний envelope identity/capabilities/transcript/work, автоматически устанавливает внутреннюю session и передаёт work в тот же прикладной API. Никаких отдельных record-message или set-session вызовов. Полное описание в [runtime](../configuration/runtime-services.md) и [handoff](local-handoff.md).


## Transfer — DDD-07B (2026-09-07T00:50:18+05:00)
`operation=transfer` принимает один export/import пакет. Export явно задаёт список самостоятельных task IDs либо целый sprint ID, request_id и handoff/null. Import задаёт request_id, абсолютный путь и SHA-256 пакета. Это тот же WorkTools: не требуется ручное копирование таблиц, подготовка worktree или исправление путей. [Полный контракт](transfer.md). Импорт не меняет активную задачу агента; bootstrap импортированной задачи остаётся осмысленным выбором новой работы.
