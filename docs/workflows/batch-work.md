# Пакетный инструмент работы — DDD-04B

Обновлено: **2026-09-13T05:25:07+05:00**. Контракт реализованного API и явно отмеченных согласованных расширений, а не дополнительный workflow DSL.

## Ответственность
`WorkTools.invoke(packet)` — один прикладной вход. Чистая грамматика проверяет пакет; Task принимает содержательные изменения; ArtifactFactory создаёт файлы; InteractionLedger проверяет уникальные события. Нативные операции над кодом/тестами приложения не заменены.

Пакет всегда содержит ровно `operation`, `input`, `messages`. Неизвестные поля/operation отклоняются. JSON CLI отклоняет повторные ключи, NaN и превышение явно заданного размера до начала task-work. Пустые коллекции/null допустимы только в описанных позициях; это явные значения, не попытка найти старый result-файл.

| operation | Обязательные поля input | Эффект |
|---|---|---|
| bootstrap | task, decision, feedback, rework_stage | Для создания — intent с `request_id` и полным task без `id`; для выбора существующей работы — объект только с `id`; затем null |
| verify | result, artifacts | Весь результат этапа + любое разрешённое количество генерируемых файлов |
| artifacts | items | Создать и зарегистрировать несколько файлов без завершения этапа |
| show | queries | Прочитать коллекцию объектов текущей задачи |
| sprint | Поля выбранного action | Создать/изменить Sprint либо безопасно заменить его незавершённую Task |
| accept | пустой объект | Принять verified результат без автоматического продолжения |
| integrate | request_id, task_id, expected_source_commit, expected_target_commit, authorization, resolutions | Интегрировать окончательно принятую Task и убрать её worktree/локальную ветку |
| cancel | reason | Санкционированно отменить текущую задачу без gate completeness |

`cancel` в этом API отменяет текущую standalone Task, сохраняет её историю, результаты и worktree, освобождает claim и записывает явную причину пользователя. Выбранные участники Sprint и Sprint целиком отменяются через пакетный Sprint API, а не серией standalone-вызовов.

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
        "artifact_requirements": [],
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "evidence_plan": {}
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
`content_contract` и `evidence_plan` должны быть полным контрактом выбранного `goal_type`;
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

Код изменён при прежнем payload: создаётся новое исполнение checks, не повторный содержательный слой. Неизменный verified пакет: возвращается прежний результат, удалённые runtime-файлы не создаются заново. Новый содержательный результат после доклада требует явного rework.

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
автоматически. Неизвестные ссылки отклоняются. Для процесса без такого маршрута поле не
разрешено и скрытая классификация не создаётся.

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

`feedback` обязателен и сохраняется в истории. `rework_stage` должен входить в `rework_targets` текущего этапа; null означает сам текущий этап. Цель с обработчиком `revise` допустима только при наличии открытых findings: иначе Task отклоняет переход до изменения route, lifecycle или execution state, потому что такому этапу нечего исправлять. Task применяет обычный `Route.enter`: история переходов и посещений увеличивается, но не ограничивает исполнение; `allowed_paths` целевого этапа продолжает действовать. Проверка `pending`, доменный переход Task и очистка execution state согласованы в одном UoW: отказ предшествует любому изменению lifecycle или execution.

Успех сохраняет Task ID, worktree, branch, task/process contracts, прежние submissions, историю и failed receipts. Создаётся новый visit целевого этапа с его iteration, записываются feedback и событие `user_failed_check_rework`, а заброшенное retry-состояние очищается: `attempts=0`, `publication=null`, `pending=null`, `entry_tree` становится текущим деревом. Операция не отменяет и не пересоздаёт Task и не является SQL-восстановлением или обходом конфигурационного hash. Для verified/accepted/completed результатов действует прежний rework предъявленного результата; этот путь относится именно к активному submitted этапу с точным failed batch.

## Интеграция принятого результата

`integrate` — одна публичная операция для локальной интеграции окончательно принятой Task и последующей уборки. Она доступна только когда Task имеет статус `completed` и сохранённый итоговый commit. Сохранённый `accepted commit` неизменяем; последующие commits изменяемого `integration head` создаются на уже существующей task branch в уже существующем task worktree. Операция не создаёт отдельные integration branch или worktree и запускается из текущего установленного исходного кода AI poise.

Синхронизация с текущим `master`, merge и разрешение конфликтов происходят только в task worktree. При конфликте операция сохраняет его точное состояние и возвращает `awaiting_resolution`; агент редактирует и разрешает только перечисленные конфликты там. Продолжение выполняет проверки на разрешённом integration head. Непосредственно перед публикацией операция захватывает общий для target lock и повторно читает `master`. Если target сдвинулся, она автоматически повторяет update → agent resolution при конфликте → checks. Стабильный target публикуется в основном checkout только командой `git merge --ff-only <task-branch>`; прямое обновление target ref и force update запрещены.

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
Обновлено: **2026-09-11T16:25:00+05:00**. `operation: sprint` принимает actions `draft`, `publish`, `dependencies`, `replace_task`, `cancel_tasks`, `cancel` и `waive_dependencies`; точные поля и примеры описаны в [Sprint API](sprints.md). Bootstrap с `task: {"id": "..."}` определяет существующий Task/Sprint по registry, не по префиксу. Прямая новая задача вне Sprint содержит `sprint_id: null`; задачи Sprint создаются публикацией и выбираются по ID. Остальной прямой stage-result/артефактный интерфейс сохранён.

`replace_task` требует `sprint_id`, `request_id`, `expected_revision`, `source_task`, полный `replacement`, `reason` и `authorization`. Успешный receipt содержит новую revision, `replacements` с old/new relation и safety facts, а также актуальные `tasks`, `eligible` и `blocked`. Идентичный replay возвращает тот же receipt без повторного preflight/мутации; конфликтующий intent с тем же `request_id` отклоняется.

Поддержанные read projections не требуют SQL или чтения managed-файлов: `show` с `kind: sprint` и view `current`, `plan` либо `history` показывает текущий граф, сохранённый process/plan snapshot и revision layers; при отсутствии другой активной Task адресный bootstrap старого ID возвращает terminal read-only context `superseded`, после чего `show` с `kind: evidence` читает его прежнее evidence. Старые submissions, artifacts и handoff receipts остаются у прежнего Task owner.

## DDD-06: внешние планы в том же пакете
`stage_work` обработчика apply_plan принимает plan, phase, resolutions и finding_resolutions. Publish принимает target_ref, expected_commit и authorization. `awaiting_action_continuation` возвращает template для CONTINUE; `action_failed/action_blocked` возвращают nonzero business outcome, а не фиктивный PASS. Подробности: [Actions](actions.md).

Пакет messages не фильтруется по read-only show или reason. Все события текущей работы учитываются с дедупликацией. Это изменение привязки, не автоматический доступ CLI к сообщениям модели.

## DDD-07: новые операции и транспорт

Обновлено: **2026-09-07T00:07:53+05:00**. `handoff` в общем work packet принимает request_id/reason/result/commit_message/artifact_paths. Только paths для готовых файлов. Resume — обычный bootstrap существующей задачи другим actor. `show.queries` принимает kind=tool_result с receipt_id/representation/range; доступ ограничен текущей задачей.

`poise runtime --settings <file>` добавляет внешний envelope identity/capabilities/transcript/work, автоматически устанавливает внутреннюю session и передаёт work в тот же прикладной API. Никаких отдельных record-message или set-session вызовов. Полное описание в [runtime](../configuration/runtime-services.md) и [handoff](local-handoff.md).


## Transfer — DDD-07B (2026-09-07T00:50:18+05:00)
`operation=transfer` принимает один export/import пакет. Export явно задаёт список самостоятельных task IDs либо целый sprint ID, request_id и handoff/null. Import задаёт request_id, абсолютный путь и SHA-256 пакета. Это тот же WorkTools: не требуется ручное копирование таблиц, подготовка worktree или исправление путей. [Полный контракт](transfer.md). Импорт не меняет активную задачу агента; bootstrap импортированной задачи остаётся осмысленным выбором новой работы.
