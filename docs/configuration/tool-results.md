# Сохранённые результаты инструментов

## Назначение и границы

`poise tool-result` сохраняет предоставленный результат инструмента полностью и
выдаёт агенту ограниченное представление с действительным `response_path`.
Поддержаны явные входы `bash`, `mcp`, `generic` и отдельный адаптер полученного
Bash `PostToolUse`. Task и Sprint для этих операций не требуются.
Команда получает уже завершённые файлы производителя и JSON-значение ответа;
самого производителя она не запускает. Программный потребитель выбирает
`--audience machine` и получает полный ответ запрошенной операции.

Прежний Poise [`result-view`](../workflows/runner.md#чтение-сохранённого-результата-без-повторного-выполнения)
продолжает обслуживать собственные сохранённые ответы. Это самостоятельные
поддержанные входы, а не автоматический перехват всех инструментов host.

## Подготовка и вызов

Нужны выбранная установленная версия пакета `ai-poise` и её Python
(`>=3.15,<3.16` для этой поставки), подготовленный
[проект](project-setup.md#пакетный-путь-для-агента), зарегистрированный Git checkout
и отдельная рабочая политика. `--config`, `--checkout`, `--policy` принимают
обязательные абсолютные пути. Checkout должен соответствовать Git repository
выбранного проекта; он не выбирает другую установленную версию кода.
`--audience` обязателен: `agent` или `machine`.

```bash
poise=( "${poise_python:?Абсолютный путь Python установленного пакета}" -I -B -m poise )
"${poise[@]}" tool-result --config "$project_json" --checkout "$checkout" \
  --policy "$tool_results_policy" --audience agent < "$request"
```

Оператор задаёт все переменные: `project_json` — конфигурация проекта,
`checkout` — корень его рабочей копии, `tool_results_policy` — заполненная
политика ниже, `request` — файл одного UTF-8 JSON-запроса. Это прямой запрос
операции, без Work-полей `input` и `messages`.

Используется существующий [владелец caller](runtime-services.md#rd-002-единая-граница-сессии):
подлинные native ID текущего процесса либо отдельный процесс без native identity
с операторским постоянным файлом `POISE_CALLER_BINDING`. Его родительская папка
должна существовать. Native identity и binding одновременно не задают; чужую
сессию не подставляют. Допуск может создать caller binding/служебные metadata,
даже когда Task отсутствует. Наличие Task-плагина в поставке сохраняется.

Политика и файлы результатов относятся к локальным данным вне VCS. Manifest
проекта меняют его публичным владельцем; это не инструкция ручной правки
managed-конфигурации. Рабочая политика размещения должна разрешать все итоговые
файлы в одном явно выбранном ignored-корне `storage.root` по одинаковым
`purpose`, `recipient`, `lifetime`; см.
[политику размещения](result-placement.md#политика-размещения).
`prepare` проверяет и закрепляет эти пути перед сохранением. Разрешение пути
не запускает автоматическую очистку.

## Политика и заполнение примера

Полный [обезличенный пример](../../config/examples/tool-results.example.json)
содержит все обязательные известные опции. Все числа ниже — целые, boolean
числом не считается. Неизвестные опции конфигурации игнорируются; их исходный
рабочий файл команда не переписывает. Запросы и сохранённые схемы остаются
строгими. Версия оператора — непустая строка, без автоматического сравнения версий.

| Поле | Значение и назначение |
|---|---|
| `schema`, `version` | Точно `poise-tool-result-policy-1` и явная версия оператора |
| `storage.root` | Абсолютный корень, разрешённый placement как ignored |
| `storage.response`, `stdout`, `stderr`, `text` | Четыре разных непустых basename; без `/`, `.` и `..`. JSON record, два raw-потока и читаемый JSON source |
| `storage.file_mode` | Число от0 до511 (например384 =0600), права создаваемых файлов |
| `storage.chunk_bytes` | Положительный размер порции чтения/копирования потоков |
| `storage.retention` | Ровно `operator-managed`; срок и удаление обслуживает оператор |
| `placement.policy` | Абсолютный путь заполненной placement policy |
| `placement.purpose`, `recipient`, `lifetime` | Непустые значения, совпадающие с выбранным правилом placement |
| `input.max_bytes` | Положительный предел JSON stdin; применяется минимум с project `batch.max_input_bytes` |
| `display.output_chars`, `output_bytes` | Положительные пределы всего agent JSON, включая завершающий LF, в Unicode-символах и UTF-8байтах |
| `display.passthrough.max_bytes`, `max_lines` | Положительные пределы короткого суммарного исходного текста |
| `display.success.tail_lines`, `max_excerpt_bytes` | Положительное число последних строк и предел выбранного фрагмента успешного результата |
| `display.failure.match_patterns` | Явный список корректных непустых regex; список может быть пустым |
| `display.failure.match_limit`, `tail_lines`, `max_excerpt_bytes` | Положительные пределы совпадений, последних строк и фрагмента диагностики |
| `display.failure.context_lines` | Контекст до/после найденных строк, целое от0 |
| `display.max_line_bytes` | Положительный предел UTF-8байтов одной выбранной длинной строки |
| `display.binary.nul_byte_is_binary` | Явный boolean: считать ли NUL признаком двоичных данных; неверный UTF-8 всегда binary |
| `inspection.max_lines`, `max_matches`, `max_excerpt_bytes` | Положительные пределы адресной выборки |
| `inspection.default_context_lines`, `max_context_lines` | Целые от0, default не превышает max; `search.context` всё равно передаётся явно, автоматической подстановки нет |
| `hook.mode` | `audit` или `enforce` |
| `hook.max_response_bytes`, `max_response_lines` | Положительные пределы короткого читаемого JSON ответа в enforce |

Скопируйте пример в разрешённое рабочее место и явно замените `@RESULTS@`
абсолютным корнем результатов, `@PLACEMENT@` — путём рабочей placement policy.
В capture/failure запросах замените `@STDOUT@`, `@STDERR@` абсолютными путями
уже полученных полных потоков. Маркеры — обозначения инструкции; CLI их сам
не подставляет. Если поток недоступен, передайте `null`, а не фиктивный пустой
файл. Пустой существующий файл и отсутствующий поток имеют разные значения.

Например, согласованный с policy ignored placement-корень имеет `id=results`,
`owner=harness`, `path` равный `storage.root`, `versioning=ignored` и непустую
`authority` согласованного локального хранения. Правило с `root=results`,
`directory=""`, `paths=["**"]`, `category=agent-work`, `priority=1` выбирает
`purposes=["tool-result"]`, `recipients=["agent"]`,
`lifetimes=["operator-managed"]`. `owner_provider=null` оставляет эти операции
без привязки к Task/Sprint. Полный формат roots/rules/state и заполненный пример
принадлежат [существующему справочнику](result-placement.md#политика-размещения).
Placement state и его lock получают собственные разрешённые локальные пути.

## Сохранение и повтор

Запрос `capture` содержит ровно `schema`, `operation`, `request_id`, `tool`,
`response`, `streams`, `capture`, `exit_code`:

| Поле | Договор |
|---|---|
| `schema`, `operation` | `poise-tool-result-request-1`, `capture` |
| `request_id` | Непустой стабильный ключ одного результата |
| `tool` | Ровно `kind` (`bash`, `mcp`, `generic`) и непустой `name` |
| `response` | Полное доступное JSON-значение ответа, включая дополнительные вложенные поля |
| `streams` | Ровно `stdout` и `stderr`: абсолютный путь или `null` у каждого |
| `capture` | Ровно `state` (`complete`, `partial`, `unknown`) и `reason` (непустая строка либо `null`) |
| `exit_code` | Исходный целочисленный код либо `null`, когда он неизвестен/неприменим |

Статус полноты и причина задаются источником. Успешный exit_code не доказывает
полноту; частичный/неизвестный результат не становится complete при чтении.
CLI exit0 означает успешную операцию сохранения/чтения, даже если исходный
инструмент завершился с exit7. Его код сохраняется отдельным полем.

JSON сохраняется целиком по значениям; исходное форматирование JSON не является
побайтовым договором. Raw stdout/stderr копируются точно, включая CRLF, Unicode,
NUL и пустой файл. Читаемый source `response` — отдельная сериализация именно
сохранённого JSON-значения, а не строка preview и не произвольный внешний JSON.

Адрес `response_path` находится в пространстве project/checkout/caller и ключа
request. Файлы копируются и сверяются до публикации готового каталога; временный
каталог принадлежит этому сохранению. Точный повтор capture с теми же входами
и доступными исходными потоками проверяет и возвращает существующий результат.
Изменённый запрос/поток с прежним ключом даёт `request_conflict`.
Для последующего чтения готового адреса используйте операции ниже: исходные
файлы производителя уже не требуются, производитель не запускается повторно.

Сохранённый record имеет schema `poise-tool-result-1`, status `stored`, исходные
tool/response/capture/exit_code, metadata потоков и служебную привязку источника.
Metadata содержат path/bytes/lines/sha256/binary. Строки считаются по LF с учётом
непустой последней строки без LF. Полный record и каждый выбранный источник
проверяются на форму/принадлежность и целостность. Перенос адреса другой сессии
не является поддержанным способом сменить caller.

## Представления и программный потребитель

Capture с `agent`, `view` и controlled hook используют один владелец выбора
фрагмента и бюджета. Для обычного текста primary — stdout, если он существует,
иначе читаемый response; stderr участвует дополнительно. Короткий суммарный
текст передаётся в пределах passthrough. Для exit0 выбирается настроенный хвост;
для остальных исходов — контекст найденных ошибок и хвост без дублирования строк.
Отсутствие совпадений отмечается явным текстом. При binary передаётся навигация
без текстового декодирования выбранных двоичных данных.

Длинные строки ограничиваются `max_line_bytes`. При дальнейшем сокращении
выбранного excerpt сохраняются начало и конец с меткой
`[tool-result] excerpt truncated by output policy` и `truncated=true`.
В очень тесном бюджете может поместиться лишь часть метки. Preview не является
непрерывным диапазоном исходника; метка не вставляется в сохранённый источник.

Для `view` задаются ровно schema/operation/response_path/view:

```json
{"schema":"poise-tool-result-request-1","operation":"view","response_path":"/absolute/saved/result.json","view":"expanded"}
```

`brief` выдаёт ограниченный итог/фрагмент и адрес. `expanded` добавляет metadata
источников, если они помещаются; при тесном бюджете дополнительных полей может
не быть. `full` для агента всегда выдаёт навигацию к файлу, даже для короткого
ответа. Бюджет включает весь компактный JSON и LF, оба лимита действуют вместе.
Если обязательные итог/адрес не помещаются, возвращается `budget_too_small`;
для capture это проверяется до сохранения результата.

При `--audience machine` capture/view/hook возвращают полный сохранённый record,
а не agent preview. Программный потребитель читает raw-потоки по полным путям
record; большие/двоичные потоки не встраиваются в JSON. Display limits к этому
ответу не применяются. Machine адресные операции возвращают полную запрошенную
выборку с действующими inspection limits. Указатель или preview не заменяет
передачу полного файла/record следующему программному инструменту.

## Адресное чтение и поиск

Все запросы содержат `schema=poise-tool-result-request-1`, операцию,
абсолютный `response_path` из настоящего результата и `channel`:
`stdout`, `stderr` либо `response`. Дополнительные поля зависят от операции:

| Операция | Дополнительные поля и границы |
|---|---|
| `summary` | Полная metadata выбранного source: bytes/lines/sha256/binary |
| `lines` | `from_line`, `to_line`: целые с1, обе границы включены; верхняя граница ограничивается EOF и inspection.max_lines |
| `bytes` | `start`, `end`: целые с0, полуинтервал [start,end); обе границы должны совпадать с UTF-8границами, end ограничивается EOF |
| `search` | `pattern` — regex; обязательные `context` от0 до max_context_lines и `max_matches` от1 до inspection.max_matches |

Примеры после подстановки настоящего адреса в рабочие JSON-файлы:

```json
{"schema":"poise-tool-result-request-1","operation":"summary","response_path":"/absolute/saved/result.json","channel":"response"}
{"schema":"poise-tool-result-request-1","operation":"lines","response_path":"/absolute/saved/result.json","channel":"stdout","from_line":1,"to_line":4}
{"schema":"poise-tool-result-request-1","operation":"bytes","response_path":"/absolute/saved/result.json","channel":"stdout","start":0,"end":4}
{"schema":"poise-tool-result-request-1","operation":"search","response_path":"/absolute/saved/result.json","channel":"response","pattern":"structuredContent","context":0,"max_matches":1}
```

Каждая строка — отдельный запрос/вызов, не общий stdin-пакет. Конкретная byte
граница должна подходить содержимому выбранного source. Пустой source допускает
lines1..N с `text=""`, returned_lines[0,0] и returned_bytes[0,0]. Начало после EOF
и неверный порядок диапазона отклоняются; end за EOF ограничивается EOF.
У bytes `returned_lines=null`. Для binary metadata доступна; text-операции
возвращают явный `binary=true`, пустые text/matches/lines без имитации текста.

`lines`/`bytes` возвращают text, actual returned_bytes, returned_lines,
total_bytes/total_lines и truncated. Дополнительное agent сокращение — непрерывный
UTF-8prefix; координаты пересчитываются по реально переданному text, включая
частичную последнюю строку и пустой фрагмент. Inspection limits сохраняются и
для machine. Search возвращает упорядоченные matches и уникальный контекст lines
с номерами с1; caps/сокращение обозначаются truncated. Если весь search envelope
не помещается в agent display, возможен budget_too_small вместо ложного успеха.
Нулевое число совпадений отличается от отказа чтения. Чтение и поиск сохраняют
исходные файлы неизменными и не повторяют capture/производителя.

## Контролируемый hook-вход

`operation=hook` принимает ровно schema/operation/request_id/event/streams/capture/
exit_code. Вместо tool/response передаётся настоящий полученный `event`:
обязательны session_id/turn_id/tool_use_id/hook_event_name/tool_name/tool_input/
tool_response/cwd; hook_event_name равен PostToolUse, tool_name равен Bash,
tool_use_id совпадает с request_id. Полный tool_response проходит тот же владелец
сохранения; новые результаты сохраняются до представления. Событие не создают
для имитации native authority и session ID не подменяют.

`audit` возвращает action=would_replace, bounded preview и адрес. `enforce`
возвращает action=enforced и protocol с continue=false, stopReason,
hookSpecificOutput.hookEventName=PostToolUse и additionalContext с адресом и
помещающимся фрагментом. Для небольшого response, удовлетворяющего обоим hook
пределам, используется его читаемое JSON-представление; остальное проходит общий
выбор excerpt. Весь возвращённый envelope остаётся в display limits.

Это явный контролируемый адаптер, а не инструкция установки hooks или доказательство
автоматической доставки protocol модели. Обычный MCP capture поддержан; MCP hook
перехват и новый MCP execution server здесь не поставляются. Подключённый host,
его настоящие события и доставка требуют своих наблюдений. Действующие native
hooks сохраняют [существующий договор](runtime-hooks.md#доставка-launcher-при-старте).

## Четыре исполняемых сценария

Поставляются четыре файла: общая
[policy](../../config/examples/tool-results.example.json),
[Bash capture](../../config/examples/tool-result-capture.example.json),
[Bash failure](../../config/examples/tool-result-failure.example.json) и
[streamless MCP capture](../../config/examples/tool-result-mcp.example.json).
Они дают четыре сценария: короткий и большой успешный stdout используют один
capture template с разными исходными файлами; failure сохраняет исходный exit7;
MCP сохраняет целиком content/structuredContent/isError и дополнительные поля.
В MCP оба raw streams равны null, exit_code неизвестен; isError не подменяет его.

В Bash response — отдельное полное JSON-значение производителя. При использовании
шаблона замените демонстрационное response фактическим ответом своего инструмента,
а streams — его настоящими потоками; не восстанавливайте response из preview.
При проверке примеров response остаётся известным демонстрационным значением,
а stdout меняется между коротким/большим/неуспешным полным входом.
Каждый новый самостоятельный результат получает собственный request_id; попытка
сохранить другой stdout под тем же ключом закономерно конфликтует.

Для точного поставляемого MCP response line25 содержит structuredContent;
lines25..28 соответствуют bytes[474,558) читаемого response source. Это результат
этого конкретного примера, не универсальная нумерация MCP. Исполняемый consumer
проверяет literal response, точный фрагмент и совпадение line25, а также view,
повторное чтение/поиск и неизменность полного результата. Четыре сценария
принадлежат `tests/runtime_services/test_tool_results_examples.py`; проверка
синтаксиса ссылок не заменяет их исполнение.

## Отказы, восстановление и срок хранения

Предметный отказ — JSON со status=rejected, error.code/field/action и доступным
исходным exit_code; CLI завершается с2. Ошибки аргументов argparse также дают2,
но это stderr/usage до предметного JSON-протокола. Бюджет успешного представления
не является обещанием обрезать диагностический отказ или полный machine ответ.

| code / action | Действие |
|---|---|
| `invalid_request` / `provide:request` | Исправить форму/типы обязательного запроса или выбор config/checkout; лимит входа тоже проверяется |
| `policy_unavailable` / `provide:policy` | Предоставить доступный файл политики |
| `invalid_policy` / `correct:policy` | Исправить известные опции/regex/границы либо согласовать ignored placement paths |
| `unmapped_input` / `configure:input` | Выбрать поддержанный explicit kind/event; произвольный hook не интерпретируется автоматически |
| `budget_too_small` / `increase:budget` | Задать достаточный display бюджет для обязательного envelope; полный machine запрос сохраняет отдельный договор |
| `invalid_range` / `choose:range` | Выбрать допустимые порядок/EOF/UTF-8границы |
| `invalid_pattern` / `correct:pattern` | Исправить поисковый regex |
| `source_missing` / `provide:source` | Предоставить отсутствующий source или выбрать сохранённый доступный канал |
| `source_unreadable`, `source_invalid` / `restore:source` | Восстановить действительные полные bytes/metadata/принадлежность источника; повреждённый preview не подменяет оригинал |
| `request_conflict` / `choose:new_key` | Для действительно нового результата задать новый ключ; прежние сохранённые данные остаются прежними |
| `storage_write` / `retry:capture` | Проверить доступность хранения и уже опубликованный результат, затем продолжить тем же capture/input через владельца |

При неясном исходе сначала выясните, существует ли готовый response по прежнему
адресу и читается ли он; чтение не повторяет producer. Отказ после внешнего I/O
не доказывает отсутствие всех эффектов. Повтор capture проверяет тот же ключ и
источники; не запускайте дорогой/изменяющий инструмент ради восстановления вывода.
Изменённые или недоступные оригиналы не исправляются автоматически. Служебный
record вручную не редактируют для обхода целостности.

`operator-managed` задаёт ответственность оператора, а не TTL, lease или GC.
Сама команда не удаляет результаты при завершении Task, которой могло вообще
не быть. Срок доступности зависит также от выбранного корня: файлы, необходимые
после удаления Task/runtime-материалов, размещают в согласованном постоянном
локальном корне. Оператор сохраняет нужные полные файлы до окончания их
использования. Эта команда не выдаёт приёмку Task, право интеграции или push.
