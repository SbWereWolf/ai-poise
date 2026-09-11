# Пакетный инструмент работы — DDD-04B

Обновлено: **2026-09-06T22:58:21+05:00**. Контракт реализованного API, а не дополнительный workflow DSL.

## Ответственность
`WorkTools.invoke(packet)` — один прикладной вход. Чистая грамматика проверяет пакет; Task принимает содержательные изменения; ArtifactFactory создаёт файлы; InteractionLedger проверяет уникальные события. Нативные операции над кодом/тестами приложения не заменены.

Пакет всегда содержит ровно `operation`, `input`, `messages`. Неизвестные поля/operation отклоняются. JSON CLI отклоняет повторные ключи, NaN и превышение явно заданного размера до начала task-work. Пустые коллекции/null допустимы только в описанных позициях; это явные значения, не попытка найти старый result-файл.

| operation | Обязательные поля input | Эффект |
|---|---|---|
| bootstrap | task, decision, feedback, rework_stage | Полный объект task при первом выборе, затем null; связывание и единый контекст |
| verify | result, artifacts | Весь результат этапа + любое разрешённое количество генерируемых файлов |
| artifacts | items | Создать и зарегистрировать несколько файлов без завершения этапа |
| show | queries | Прочитать коллекцию объектов текущей задачи |
| accept | пустой объект | Принять verified результат без автоматического продолжения |
| integrate | request_id, task_id, expected_source_commit, expected_target_commit, authorization, resolutions | Интегрировать окончательно принятую Task и убрать её worktree/локальную ветку |
| cancel | reason | Санкционированно отменить текущую задачу без gate completeness |

Из bootstrap возвращается готовый `result_template`, включающий sections, content additions, trace updates, verification methods, stage_work, evidence_work, commit_message и artifact_paths по действующему контракту. Вычисляемые ID/task/stage/hash агент повторно не передаёт. Прежний transport через редактируемый result_path удалён.

## Verify

Обязательный порядок работы агента: [bootstrap в начале и verify перед завершением](development-rules.md#начало-и-завершение-работы). `read_only_verified` завершает сессию без содержательных проверок и не удостоверяет правильность ответа.

1. Проверить форму пакета и событий; наблюдение сообщения сохранить как факт взаимодействия.
2. Построить весь план файлов, проверить scope/ссылки/template/content существующих файлов.
3. Проверить семантическую форму кандидатного Task submission до создания новых файлов.
4. Материализовать файлы, объединить их пути с явно переданными существующими путями без повторов.
5. Передать кандидат штатному Task/Content/Evidence pipeline. Новые методы и секции уже участвуют в этом verify.
6. Выполнить проверки/CONTINUE/публикацию по прежним правилам. Записать verified packet receipt, когда результат verified.
7. Вернуть контекст сообщения и результата; не начинать следующий содержательный этап.

Content pre-gate может отклонить уже сохранённый кандидат после генерации файлов. Это ожидаемая неполнота работы, не фиктивная атомарность файловой системы и БД. Файлы принадлежат правильному владельцу, не считаются verified evidence сами по себе; одинаковый повтор использует те же файлы. Сломанный payload/недопустимый исходный путь отбрасывается до генерации.

Код изменён при прежнем payload: создаётся новое исполнение checks, не повторный содержательный слой. Неизменный verified пакет: возвращается прежний результат, удалённые runtime-файлы не создаются заново. Новый содержательный результат после доклада требует явного rework.

## Интеграция принятого результата

`integrate` — одна публичная операция для локальной интеграции окончательно принятой Task и последующей уборки. Она доступна только когда Task имеет статус `completed` и сохранённый итоговый commit. Операция использует `git.repository` и локальную ветку `git.base_ref` выбранной конфигурации; эта ветка должна быть checkout текущего repository worktree.

Исходный task worktree и target worktree должны быть чистыми, записанная task-ветка должна указывать на `expected_source_commit`, а текущий target HEAD — точно совпадать с `expected_target_commit`. Проверки выполняются до первого Git-эффекта. `authorization` хранит явное пользовательское основание и используется как сообщение merge commit, поэтому обязано соответствовать `git.commit_pattern`.

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

Команда выполняется через уже выбранные `HARNESS_CONFIG` и `HARNESS_SESSION`:

```bash
harness work <<'JSON'
{"operation":"integrate","input":{"request_id":"integrate-0016-1","task_id":"0016","expected_source_commit":"0123456789abcdef0123456789abcdef01234567","expected_target_commit":"89abcdef0123456789abcdef0123456789abcdef","authorization":"Integrate accepted task 0016","resolutions":[]},"messages":[]}
JSON
```

Точные commit IDs берутся из проверенного результата Task и наблюдаемого target HEAD; примерные SHA выше нельзя копировать как фактические значения. Одинаковый пакет повторяется безопасно. После `integrated` допускается также передать выданный `target_after` как наблюдаемый `expected_target_commit`: это terminal replay, а не новая интеграция. Другие изменения intent с тем же `request_id` отклоняются.

Статусы и восстановление:

- `integrated`: target содержит source commit, task worktree удалён, локальная task-ветка удалена через безопасный `git branch -d`; повтор возвращает сохранённый результат с `replayed: true`;
- `awaiting_resolution`: Git оставлен в точном conflict state, source worktree и ветка сохранены; исправьте только перечисленные файлы обычным редактором и повторите тот же пакет, заменив `resolutions` на один объект для каждого conflict path;
- `blocked`: merge или merge commit не завершился; receipt сохранён. Тот же пакет повторяет только безопасную незавершённую фазу, если HEAD/MERGE_HEAD/cleanliness не изменились;
- `cleanup_pending`: target уже содержит source, но уборка заблокирована. Source не удаляется вслепую; после устранения причины тот же пакет продолжает только уборку.

Пример продолжения конфликта — все остальные поля исходного intent остаются прежними:

```json
"resolutions": [
  {
    "path": "src/example.py",
    "resolution": "Сохранено принятое поведение source и совместимый target API."
  }
]
```

Операция сама выполняет Git staging, commit, worktree removal и branch deletion. Пользователю не требуется запоминать служебные Git-команды. Посторонние unstaged/untracked изменения target блокируют продолжение и уборку; force-delete не используется.

Состояние и полная история переходов читаются пакетным query после перезапуска процесса:

```json
{"operation":"show","input":{"queries":[{"id":"result","kind":"integration","task_id":"0016","request_id":"integrate-0016-1"}]},"messages":[]}
```

Ответ сохраняет `source_commit`, `target_before`, `target_after`, merge/failure receipts, conflict resolutions, поэтапный `cleanup` и `history`. Task/Sprint history и прежний проверенный результат не переписываются.

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
Каждый query имеет собственный `id`. Поддержаны `task`, `messages`, `content`, `evidence`, `section`, `trace`.
```json
{"operation":"show","input":{"queries":[{"id":"state","kind":"task"},{"id":"report","kind":"section","name":"report","stage":null,"submission":null,"range":{"unit":"lines","start":1,"end":10}},{"id":"cost","kind":"messages"}]},"messages":[]}
```

Section query явно содержит name/stage/submission/range. Null stage/submission означает текущую проекцию; null range — явно настроенный initial_read_lines. Строки — 1-based inclusive; байты — 0-based half-open по UTF-8. Границы не разрезают символы; такой byte-запрос отклоняется с предложением использовать строки. Конец диапазона ограничивается реальным концом данных. В ответе total_lines/total_bytes, returned_lines/returned_bytes. Пустая секция — пустой текст и [0,0].

Это единый запрос чтения, но не обещание одного SQL snapshot всех разных read-models. Одна задача одновременно не изменяется несколькими агентами по принятой модели оркестрации.

## Пользовательское сообщение
```json
{"conversation_id":"conversation-1","message_id":"turn-7","occurred_at":null,"reason":"continue","subject":null}
```
Источник задаётся конфигом. IDs должны быть стабильны у источника, даже при повторных вызовах и restart. В `agent_reported` режиме агент назначает стабильное имя видимому сообщению и повторно использует его; инструмент не умеет восстановить ненаблюдаемую историю ChatGPT.

Причина — одно явное значение словаря либо null. Subject — известная ссылка либо null. Неизвестная причина не угадывается. Повтор события с другими данными отклоняется до применения пакета. Полный transcript не записывается. Task/goal/stage/iteration назначаются текущим binding, а не из повторно введённых агентом IDs.

Событие сохраняется даже если последующий verify не прошёл: это фактический расход взаимодействия. Поэтому отказ Task не откатывает ledger. Первичное связывание сообщения с task неизменно; read/retry не переносит расход на другую task. Подготовительное ad-hoc-событие может получить первый task binding в последующем bootstrap.

## Выдача
`work` возвращает валидный JSON, не обрезанный посреди объекта. Лимит включает весь ответ. Действующий Task response сохраняется под её root; краткий ответ содержит ссылку на полное содержание. Слишком маленький output budget отклоняется до создания worktree. Exit 0 — получен штатный результат, в том числе `awaiting_continuation`; exit 1 — проверки/обязательства ещё не выполнены; exit 2 — неправильный вход/конфигурация. Это не вывод о наличии бага Harness по любому non-zero.

Обычный read-only taskless verify принимает result=null/artifacts=[] и очищает runtime. Taskless создание постоянного deliverable не реализовано этим срезом; formal task нужна для durable task-result.


## Sprint в DDD-05
Обновлено: **2026-09-06T22:58:21+05:00**. Новый `operation: sprint` и пакетное чтение `kind: sprint` описаны в [Sprint API](sprints.md). Bootstrap с `task: {"id": "..."}` определяет существующий Task/Sprint по registry, не по префиксу. Прямая новая задача вне Sprint содержит `sprint_id: null`; задачи Sprint создаются публикацией и выбираются по ID. Остальной прямой stage-result/артефактный интерфейс сохранён.

## DDD-06: внешние планы в том же пакете
`stage_work` обработчика apply_plan принимает plan, phase, resolutions и finding_resolutions. Publish принимает target_ref, expected_commit и authorization. `awaiting_action_continuation` возвращает template для CONTINUE; `action_failed/action_blocked` возвращают nonzero business outcome, а не фиктивный PASS. Подробности: [Actions](actions.md).

Пакет messages не фильтруется по read-only show или reason. Все события текущей работы учитываются с дедупликацией. Это изменение привязки, не автоматический доступ CLI к сообщениям модели.

## DDD-07: новые операции и транспорт

Обновлено: **2026-09-07T00:07:53+05:00**. `handoff` в общем work packet принимает request_id/reason/result/commit_message/artifact_paths. Только paths для готовых файлов. Resume — обычный bootstrap существующей задачи другим actor. `show.queries` принимает kind=tool_result с receipt_id/representation/range; доступ ограничен текущей задачей.

`harness runtime --settings <file>` добавляет внешний envelope identity/capabilities/transcript/work, автоматически устанавливает внутреннюю session и передаёт work в тот же прикладной API. Никаких отдельных record-message или set-session вызовов. Полное описание в [runtime](runtime-services.md) и [handoff](local-handoff.md).


## Transfer — DDD-07B (2026-09-07T00:50:18+05:00)
`operation=transfer` принимает один export/import пакет. Export явно задаёт список самостоятельных task IDs либо целый sprint ID, request_id и handoff/null. Import задаёт request_id, абсолютный путь и SHA-256 пакета. Это тот же WorkTools: не требуется ручное копирование таблиц, подготовка worktree или исправление путей. [Полный контракт](transfer.md). Импорт не меняет активную задачу агента; bootstrap импортированной задачи остаётся осмысленным выбором новой работы.
