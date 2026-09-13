# Sprint API — DDD-05

Обновлено: **2026-09-13T08:10:00+05:00**. Исполняемый срез; не заявление о выполнении всех нормативных процессов.

## Владелец и основные гарантии
Sprint владеет планом, принадлежностью задач, внутриспринтовым DAG, решениями об отмене/waiver. Task владеет своим содержимым и жизненным циклом. `SprintCommands` координирует их в одной короткой UoW. Отдельного процесса/scheduler и отдельной базы для Sprint нет.

Draft ещё не рабочий Sprint. Неполный дочерний контракт сохраняется с точными ошибками и может быть исправлен следующим пакетом. При публикации каждый контракт проходит **тот же** `validate_creation`, что и отдельная задача: маршрут, секции/ContentPolicy, EvidencePlan, реестр точных команд и расписание. Пакет переводит готовые реальные newborn Task в `available` и публикует весь граф одной транзакцией; worktrees, команды и тесты в ней не запускаются.

Прежние слои планирования хранятся в SQLite. Публикация не переписывает уже существующий task ID. Published process snapshots не заменяются от редактирования файлов процесса. В этом срезе полный опубликованный план не переписывается; изменять зависимости можно только для ещё не начатых successors.

Изменение валидной project configuration не инвалидирует и не перезапускает уже созданные задачи. Каждая операция использует текущую конфигурацию проекта вместе с неизменными Task-owned process/contract/method snapshots. Сохранённый `config_hash` остаётся диагностическим provenance и не является lifecycle gate. Параллельные задачи изолируются отдельными worktree, Task ownership, optimistic revisions и короткими resource-scoped locks; общий конфиг на iteration не замораживается.

## Конфигурация
`project.schema = ddd-actions-8`; обязательное поле `sprint` описывает:
- `max_tasks`, `max_dependencies`: явные пределы;
- `acceptable_terminal_states`: явные допустимые для нормального закрытия состояния;
- `templates`: именованные шаблоны с версией, целью, требованиями, DoD, секциями, задачами и зависимостями;
- `section_rules`: обычные SectionRule (`name`, `template`, `normalization`, `required`).

Образец: `config/sprint.example.json`. Это явные значения примера, не defaults кода. Выбор шаблона обязателен при создании; при изменении передаётся `template: null`, используется сохранённый контракт. Missing values не изобретаются.

## Пакетные изменения
Один вызов `poise work`, JSON в stdin. Общий envelope: `operation`, `input`, `messages`. Для Sprint — `operation: sprint`. Структурные имена команд являются протоколом инструмента, а не названием goal type.

### Создать/доработать draft
```json
{
  "operation": "sprint",
  "input": {
    "action": "draft", "sprint_id": "S-1", "request_id": "plan-1",
    "expected_revision": null, "template": {"id": "basic", "version": "1"},
    "changes": [
      {"kind": "purpose", "goal": "Код и инструкция", "requirements": ["R1"], "definition_of_done": ["Результаты приняты"]},
      {"kind": "sections", "values": {"plan": "A создаёт результат, B использует его."}},
      {"kind": "upsert_tasks", "tasks": []},
      {"kind": "dependencies", "items": []}
    ]
  },
  "messages": []
}
```
Этот неполный пример **сохраняет draft, но не проходит публикацию**: список tasks нужно заполнить полными контрактами. Готовые исполняемые контракты автоматически создаёт пример `examples/sprint_demo.py`; пользователь не обслуживает файлы/БД вручную.

Поддерживаемые виды changes:

| kind | Данные |
|---|---|
| `purpose` | Полные goal, requirements, definition_of_done |
| `sections` | Пакет текстов `values`; можно добавить именованную дополнительную секцию |
| `upsert_tasks` | Пакет полных creation intents: automatic `{request_id, task без id}` либо поддержанный explicit task; объект заменяет прежнюю draft-редакцию того же alias |
| `remove_tasks` | Список `ids`; оставшиеся зависимости проверяются по конечному состоянию |
| `dependencies` | Полный желаемый список `items` |

Один kind в пакете не повторяется: вместо множества однотипных записей используется его список/словарь. Повторные Task IDs и одновременные remove/upsert отклоняются. Порядок разных kinds не влияет на разрешимость конечного графа. Частичная активная публикация не допускается.

## Реальные newborn Task в Sprint draft

Sprint draft хранит участников как реальные newborn Task с постоянными ID, собственной
историей, revision и ownership, а не только как вложенные определения. Поэтому частичный
контракт можно сохранять и редактировать до выбора типа и полной готовности, не теряя
планирование графа и связь со Sprint. Чужой живой claim блокирует изменение; удаление
участника или отмена draft отсоединяет его от Sprint, сохраняя Task identity и историю.

`materialize_tasks` преобразует legacy embedded definitions текущего draft в такие newborn
Task. Automatic intents получают постоянные ID один раз, сохраняют creation request, а alias
dependencies переписываются на эти ID. Повтор с тем же `request_id` не выделяет новые ID.
Неполный или пока невалидный legacy embedded contract остаётся редактируемым newborn draft;
type-specific readiness проверяется позднее через `ready` или публикацию.

После успешной публикации Sprint plan содержит канонические строковые IDs участников,
newborn Task атомарно становятся `available`, а dependency graph и result projection используют
ту же identity. Отдельная прямая полная creation Task остаётся поддержанным путём и не требует
предварительного Sprint draft.

Редактирование: `sprint_id: null` означает выбранный Sprint; `expected_revision` — полученная revision, новый `request_id`, `template: null`. Полный список ошибок публикационной готовности возвращается вместе с draft. Ошибка draft ещё не превращается в исполняемые задачи.

### Опубликовать
```json
{"operation":"sprint","input":{"action":"publish","sprint_id":null,"request_id":"publish-1","expected_revision":2},"messages":[]}
```
Одна ошибка дочерней задачи, графа, обязательной секции или конфликт ID отменяет всю публикацию. Действие вызывается после разрешения пользователя; сам инструмент не проводит смысловую приёмку вместо пользователя.

Для automatic-участника зависимости draft используют его `request_id` как временный alias.
При публикации Task repository атомарно выделяет ID, Sprint plan и все dependency endpoints
переписываются на фактические ID, а ответ содержит массив `allocations`. Полный пакет заранее
резервирует explicit Task IDs и ID создаваемого Sprint, поэтому automatic-кандидат не занимает
их и результат не зависит от порядка automatic/explicit элементов. Ошибка любого участника
откатывает Task-агрегаты, membership, зависимости, Sprint layer и allocation receipt вместе.

```json
{
  "request_id": "member-import-1",
  "task": {
    "sprint_id": "S-1",
    "goal_type": "development",
    "goal": "Добавить импорт.",
    "requirements": ["R1"],
    "definition_of_done": ["D1"],
    "methods": [],
    "checks": {},
    "artifact_requirements": [],
    "content_contract": {"sections": [], "routes": [], "requirements": []},
    "evidence_plan": {}
  }
}
```

Пустые process-зависимые коллекции здесь показывают только форму automatic intent и должны
быть заменены полным контрактом выбранного `goal_type`. После публикации дальнейшие Sprint
операции и `bootstrap` адресуются выданным Task ID, не alias.

`request_id` имеет область Sprint. Тот же ID можно использовать у другого Sprint, но внутри одного ID связан с одним точным пакетом. Повтор оригинального пакета не повторяет публикацию/слой. Результат содержит свежую проекцию; operation receipt хранит сам факт применения. Устаревшая revision не перезаписывает новую.

## Выбор работы и один пакет контекста
```json
{"operation":"bootstrap","input":{"task":{"id":"S-1"},"decision":null,"feedback":null,"rework_stage":null},"messages":[]}
```
Поле выбора уже существующего scope принимает **ID**, а не повтор всего task JSON. Registry определяет, задача это или спринт; имена/префиксы не угадываются. Sprint bootstrap возвращает цель, текущую revision, все eligible IDs, компактные сведения о задачах, blockers, текущую formal task и `result_provenance`. Для каждого действующего result-ребра эта проекция содержит ID предшественника и его `result_commit`, включая явный `null` до появления результата. Bootstrap обзора не выбирает задачу вместо агента, не создаёт worktree и не берёт эксклюзивный claim спринта.

Агент выбирает одну задачу тем же `bootstrap` с её ID. Инструмент начинает её и сразу возвращает обычный stage context/result_template. В дальнейшем ID не повторяется. После `verify`, `accept` и `cancel` ответ автоматически включает обновлённую проекцию спринта. Новой команды `next` нет.

`verified` predecessor ещё не удовлетворяет зависимость: нужен `completed` после пользовательского принятия. При активной задаче прочитать другой спринт можно; это не переключает formal work. Начать другую task до завершения/передачи текущей нельзя.

## Два явных вида зависимостей
Каждое ребро: `predecessor`, `successor`, `kind`.

- **completion**: требуется принятый результат предшественника; его commit не включается в специальную result-provenance проекцию. Это годится для организационной предпосылки.
- **result**: также требуется завершённый результат предшественника и дополнительно сохраняется его `result_commit` в `result_provenance`. Это provenance результата, а не выбор Git ancestry successor.

Каждый **новый** Task worktree — самостоятельный, Sprint-участник или replacement — создаётся от commit, наблюдённого на текущей вершине явно настроенного Git `base_ref`. Наблюдение выполняется после проверки eligibility и до durable Task start; точный SHA сохраняется в reservation. Поэтому повтор частично выполненного worktree setup использует сохранённый SHA, даже если `base_ref` уже сдвинулся, а следующая новая Task наблюдает новую вершину. Resume и handoff существующего worktree не резолвят `base_ref` заново, не сбрасывают и не пересоздают worktree.

У одного successor поддерживаются несколько result-рёбер с одинаковыми или различными result commits: они перечисляются в `result_provenance`, но не выбирают branch base и не требуют наличия этих Git objects для создания worktree. Инструмент не переносит и не сливает код предшественников; если successor действительно зависит от их кода, требуемый код должен быть доставлен в настроенный `base_ref` отдельным явным процессом до старта successor. Точные команды successor проверяют фактический результат.

Пределы: одна codebase проекта, как в предыдущем runtime; result dependency — Git-result, не общий resolver всех типов артефактов. Точные команды successor проверяют фактическую работу; чужие tests receipts не становятся его evidence.

## Изменить зависимости, отменить, разрешить зависимую работу
Все действия пакетные; `sprint_id: null` использует текущий scope.

| action | Остальные поля |
|---|---|
| `materialize_tasks` | request_id, expected_revision; превращает legacy embedded definitions в реальные newborn Task |
| `dependencies` | request_id, expected_revision, items, reason |
| `replace_task` | request_id, expected_revision, source_task, полный replacement, reason, authorization |
| `cancel_tasks` | request_id, tasks (список ID), mode (`single`/`cascade`), reason |
| `cancel` | request_id, reason |
| `waive_dependencies` | request_id, decisions (predecessor, successor, reason) |

Изменение prerequisites уже начатого successor отклоняется. Cancel одного узла не закрывает siblings/root; cascade вычисляется по его потомкам. У cancelled predecessor незавершённый successor остаётся blocked до пользовательского waiver либо отмены ветки. Waiver и исходное ребро остаются в business data, исправление не имитируется.

`cancel_tasks` отменяет **незавершённых** выбранных участников через Task domain; уже
completed/cancelled не переписываются. `cancel` отменяет Sprint целиком: у published Sprint
отменяет всех незавершённых участников, сохраняет completed/cancelled, результаты, историю и
артефакты; у draft меняет состояние Sprint без создания Task. Для каждой затронутой Task тот
же владелец создаёт persisted cleanup obligation. Сам terminal-переход не удаляет worktree,
не публикует commit и не выдаёт WIP за проверенный результат.

```json
{"operation":"sprint","input":{"action":"cancel","sprint_id":"S-1","request_id":"cancel-sprint-1","reason":"Пользователь отменил Sprint"},"messages":[]}
```

Отмена standalone Task выполняется её владельцем через `operation: cancel`. Явная
Sprint-команда является полномочием Sprint над собственными участниками и может отменить
активную Task, освободив claim, но не может затронуть задачу другого Sprint. Перед пакетной
отменой все выбранные участники проверяются на pending внешние операции; найденный pending
отклоняет весь пакет без частичного эффекта. Семантическую санкцию пользователя интерпретирует
агент; непустая reason сохраняет это решение, но не является криптографическим удостоверением.
Последующая судьба commit задаётся отдельным `cleanup` для каждой Task; статусы, dirty-worktree
recovery и точная область ресурсов описаны в
[пакетном workflow](batch-work.md#уборка-ресурсов-terminal-task).

## Заменить ошибочную незавершённую Task

`replace_task` заменяет участника внутри уже опубликованного Sprint; создавать новый Sprint не нужно. Это исправление ошибочного неизменяемого Task-контракта, а не редактирование старой Task. Клиент передаёт текущую `expected_revision`, новый `request_id`, ID источника, полный контракт новой Task, содержательную причину и явное основание полномочий пользователя:

```json
{
  "operation": "sprint",
  "input": {
    "action": "replace_task",
    "sprint_id": "S-1",
    "request_id": "replace-invalid-import-1",
    "expected_revision": 3,
    "source_task": "IMPORT-BAD",
    "replacement": {
      "id": "IMPORT-FIXED",
      "sprint_id": "S-1",
      "goal_type": "development",
      "goal": "Исправить импорт клиентов",
      "requirements": ["Импорт использует проверенный формат"],
      "definition_of_done": ["Точные проверки проходят"],
      "methods": [{
        "id": "CHECK",
        "argv": ["python", "-m", "pytest", "-q"],
        "cwd": ".",
        "environment": {},
        "expected_exit_code": 0,
        "stdout_contains": [],
        "stderr_contains": []
      }],
      "checks": {"work": ["CHECK"]},
      "artifact_requirements": [],
      "content_contract": {"sections": [], "routes": [], "requirements": []},
      "evidence_plan": {"work": {"subject_methods": {}, "arguments": [], "review_arguments": []}}
    },
    "reason": "В опубликованной задаче сохранён ошибочный неизменяемый метод",
    "authorization": "Пользователь поручил заменить незавершённую задачу"
  },
  "messages": []
}
```

Имена stages, methods, checks и evidence в примере иллюстративны: `replacement` обязан быть полным точным creation contract выбранного `goal_type` из сохранённого process snapshot Sprint. Живая конфигурация процесса, изменённая после публикации, не подменяет этот snapshot. Новый ID должен быть свободен среди Task и Sprint, `sprint_id` обязан совпадать, а устаревшая revision и неполный/невалидный контракт отклоняются до мутации.

Допустимая граница состояния источника:

| Состояние источника | Результат и обязательное восстановление |
|---|---|
| `available` без claim/worktree | Замена разрешена; фиксируется safety kind `available` |
| `active`/`verified`/`accepted`, текущая session — владелец, worktree чист | Замена разрешена; worktree не удаляется и не копируется, в relation фиксируются его путь и tree |
| Task освобождена подтверждённым handoff | Замена разрешена; relation ссылается на `handoff_request`, receipt и сохранённый WIP остаются у старой Task |
| Чужой owner, dirty/неоднозначный WIP | Замена отклоняется: владелец должен сначала выполнить handoff, сохранив WIP |
| Есть pending external outcome | Замена отклоняется: сначала нужно разрешить/завершить pending operation и повторить запрос с актуальной revision |
| `completed`, `cancelled` или уже `superseded` | Замена запрещена: источник не является незавершённой текущей Task |

Все входящие и исходящие зависимости и действующие waivers перенаправляются на новую Task с прежними kinds. До записи повторно проверяются полный конечный DAG, `max_dependencies`, self-edges, дубликаты и циклы. Если источник был predecessor уже начатого successor, замена отклоняется, потому что она изменила бы его prerequisite. Проекции `eligible` и `blocked` сразу используют новый ID.

Внешний preflight чистоты worktree выполняется без блокировки БД. Затем короткая UoW повторно сверяет Sprint revision, Task version, факты ownership/pending и последний handoff; в ней вместе сохраняются новый Task-агрегат, `superseded` старой Task, новый Sprint plan/dependencies, relation и receipt. Сбой любого writer откатывает весь этот набор. Старые contract, submissions, evidence, artifacts, handoff receipts, worktree и audit history не переписываются.

В успешном ответе `replacements` содержит `source`, `replacement`, `reason`, `authorization`, `from_revision`, `to_revision` и `safety`; `tasks`, `eligible` и `blocked` описывают уже новый текущий граф. `show` с `kind: sprint` и views `current`/`plan`/`history` возвращает relation и соответствующие слои. При отсутствии другой активной Task старый ID можно выбрать адресным `bootstrap` только для read-only контекста со статусом `superseded`; её evidence остаётся доступным через обычный query `kind: evidence`.

Повтор идентичного успешно применённого пакета с тем же `request_id` возвращает исходный receipt до нового внешнего preflight и не создаёт второй Task/слой. Другой intent с уже сохранённым ID отклоняется. После отказа по revision, контракту, графу или safety исправьте указанную причину, прочитайте актуальную Sprint revision и повторите пакет; отклонённый запрос не сохраняет operation receipt и не оставляет частичного graph/lifecycle состояния.

## Закрытие и чтение
Sprint progress — согласованная проекция из membership и Task states, а не дублируемые вручную счётчики. Нормальное закрытие определяется явным `acceptable_terminal_states` и отсутствием незавершённой работы/блокировок. Полное тестирование приложения при закрытии не запускается. Полный future closure process с артефактными post-gates пока не реализован.

Пакетное чтение:
```json
{"operation":"show","input":{"queries":[{"id":"now","kind":"sprint","sprint_id":null,"view":"current"},{"id":"layers","kind":"sprint","sprint_id":null,"view":"history"}]},"messages":[]}
```
`plan` возвращает сохранённый контракт и snapshots для адресной диагностики. В обычном context они не печатаются полностью. CLI доклады Sprint сохраняются в configured Sprint root; большой ответ заменяется ограниченным receipt на полный файл.

### Реестр опубликованных Sprint

Публичный `show` query `kind: work_overview` перечисляет только опубликованные Sprint. Живой draft исключён. Отменённый Sprint остаётся в реестре, если наличие `sprint_members` подтверждает его предшествующую публикацию; draft, отменённый через `cancel` до публикации, участников не создаёт и в реестр не попадает. Порядок стабилен по `sprint` ID.

Каждый элемент — обычная owner-проекция `SprintWork.overview`, а не сокращённая реконструкция из строк БД. Поэтому сохраняются `goal`, `tasks`, `counts`, `eligible`, `active`, `blocked` с причинами зависимостей, `resumable`, `result_provenance`, `active_task` и остальные поля текущего Sprint overview. `active_task` заполняется только когда незавершённая текущая Task вызывающей session принадлежит именно этому Sprint; standalone Task или участник другого Sprint не переносится в чужой DTO.

Статус Sprint вычисляет владелец после проверки зависимостей и наличия завершённых predecessor results:

- `planned` — доступная работа ещё не начиналась;
- `active` — есть активная/продолженная работа либо доступная работа после уже изменившихся состояний участников;
- `completed` — все участники находятся в разрешённых терминальных состояниях;
- `cancelled` — Sprint принудительно закрыт;
- `blocked` — незавершённая работа существует, но доступных или активных участников нет.

Фильтр `sprint_statuses` в `work_overview` применяется к этой готовой проекции и не удаляет сведения `eligible`/`blocked`. Точный общий пакет, независимый фильтр standalone Task и ограничения выдачи описаны в [пакетном обзоре работ](batch-work.md#обзор-опубликованных-спринтов-и-самостоятельных-задач).

## Граница метрик
Пользовательские события в Sprint-операциях сохраняются без приписывания чужой активной Task. Task messages/delivery остаются прежними. Полная атрибуция планирования к Sprint и периодные экономические агрегаты — DDD-08; не заявляются реализованными.
