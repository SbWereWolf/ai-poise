# Сохранённые результаты инструментов

## Назначение и границы

`tool-result` — общий механизм ограничения вывода для явно настроенных инструментов.
Парсер получает полный доступный источник и один раз готовит краткую и подробную формы.
Подключённый `PostToolUse` передаёт агенту краткую форму; файлы подробного и полного
результата сохраняются в его сессии, в каталоге запуска с UUID. Сессия хранит UUID
последнего готового результата. Команды `expanded` и `full` читают его сохранённые файлы.

Обычный ответ Poise обрабатывается тем же механизмом, что Bash и объявленный MCP.
`result-view` не является другим способом доставки F08. Внутренние сохранённые ответы
Poise и низкоуровневые capture/read API остаются источниками данных своего владельца.
Они не требуют запускать инструмент повторно ради отображения.

Машинный потребитель получает исходный ответ своего инструмента полностью. Замена
модельного представления не должна сокращать stdout/stderr или структурированные
поля между программными вызовами. Фактическую доставку и сохранность nested return
проверяют отдельно от установки конфигурации и изолированных тестовых событий.
Поддержаны только объявленные профилем формы входа; автоматического каталога всех
инструментов или восстановления недоступного upstream полного вывода нет.

## Подготовка и вызов

Нужны выбранный Python поставки (`>=3.15,<3.16`), рабочая policy и явно разрешённый
корень хранения. `--policy` — обязательный абсолютный путь, `--audience` — обязательный
`agent` либо `machine`. Для session-механизма `--config` и `--checkout` можно опустить;
при наличии они передаются **оба**, абсолютными путями, и выбирают зарегистрированный
checkout проекта. Выбор checkout не меняет executable/import source Poise.

Низкоуровневый режим policy **без `session`** требует выбранного проекта и checkout.
Его пример ниже сохраняет прямые capture/read операции прежнего API, а не добавляет
альтернативный автоматический перехват.

```bash
poise=( "${poise_python:?Абсолютный путь установленного Python}" -I -B -m poise )
"${poise[@]}" tool-result --policy "$tool_results_policy" --audience machine < "$request"
```

Оператор заполняет переменные до вызова; stdin — один UTF-8 JSON запроса операции,
без Work-полей `input`/`messages`. `-I` подходит установленному noneditable пакету.
При явно выбранном source через `PYTHONPATH` используются его штатные `-P -B` флаги,
а не игнорирующий этот путь `-I`; см. [контракт запуска](stride-launcher.md#выполнение-команды).
Пример выше показывает прямой API. Для служебного ответа через hook поддержана
[более узкая буквальная форма команды](#чтение-последнего-результата).

Caller устанавливает [существующий владелец
сессии](runtime-services.md#rd-002-единая-граница-сессии):
настоящие native ID процесса либо постоянный `POISE_CALLER_BINDING` у отдельного
процесса без native identity. Сигналы не смешивают, чужие ID не подставляют.
Общий session route не открывает Task/Sprint; Task adapter ему не требуется.

Рабочие policy/данные находятся вне VCS. Политика размещения разрешает все пути
`storage.root` как ignored с явными purpose/recipient/lifetime. Session route
проверяет пути существующим placement owner, не создавая отдельный Task ledger.
[Placement](result-placement.md#политика-размещения) не задаёт TTL или автоматическое
удаление. Managed-конфигурацию проекта/установки меняют её публичным владельцем.

## Политика и заполнение примера

Базовый [низкоуровневый пример](../../config/examples/tool-results.example.json)
содержит обязательные общие опции. Полный
[session-пример](../../config/examples/tool-results-session.example.json)
добавляет явно заданные session, parsers и selection. Все числа ниже — целые, boolean
числом не считается. Неизвестные опции конфигурации игнорируются; их исходный
рабочий файл команда не переписывает. Запросы и сохранённые схемы остаются
строгими. Версия оператора — непустая строка, без автоматического сравнения версий.

| Поле | Значение и назначение |
|---|---|
| `schema`, `version` | Точно `poise-tool-result-policy-1` и явная версия оператора |
| `storage.root` | Абсолютный корень, разрешённый placement как ignored |
| `storage.response`, `stdout`, `stderr`, `text` | Четыре разных непустых basename; без `/`, `.` и `..`. JSON record, два raw-потока и полный читаемый источник: исходный текст для выбранного native-text либо JSON для структурированных форматов |
| `storage.file_mode` | Число от0 до511 (например384 =0600), права создаваемых файлов |
| `storage.chunk_bytes` | Положительный размер порции чтения/копирования потоков |
| `storage.retention` | Ровно `operator-managed`; срок и удаление обслуживает оператор |
| `placement.policy` | Абсолютный путь заполненной placement policy |
| `placement.purpose`, `recipient`, `lifetime` | Непустые значения, совпадающие с выбранным правилом placement |
| `input.max_bytes` | Положительный предел JSON stdin; при выборе проекта применяется минимум с его `batch.max_input_bytes` |
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
| `hook.mode` | `audit` или `enforce` для низкоуровневого hook без session; общий builtin использует подготовленные формы |
| `hook.max_response_bytes`, `max_response_lines` | Положительные пределы короткого читаемого JSON ответа в enforce |

Скопируйте пример в разрешённое рабочее место и явно замените `@RESULTS@`
абсолютным корнем результатов, `@PLACEMENT@` — путём рабочей placement policy.
В session/handler примерах также замените `@POISE_RESPONSE_ROOT@` абсолютным разрешённым
корнем настоящих SavedResponse, `@POISE_COMMAND_REGEX@` полным regex действительного
Poise entry, `@TOOL_RESULTS_POLICY@` абсолютным путём заполненной policy. Имя
`mcp__example__records` и `mcp__example__projects` — обозначения MCP с показанными
структурами, не установленные endpoints: реальные имена меняются согласованно в
parsers, selection и native matcher.
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

## Сессия и два сохранённых представления

| Поле session | Договор |
|---|---|
| `root` | Абсолютный корень сессий внутри разрешённого `storage.root` |
| `runs` | Имя каталога запусков |
| `last` | Имя JSON-указателя последнего готового UUID |
| `expanded`, `full` | Имена сохранённого подробного текста и полного источника выбранного формата |

Все четыре имени — различные непустые basename, без `/`, `.` и `..`.
Файлы принадлежат настоящему caller: `session.root/<caller-key>/runs/<uuid>/`.
В last хранится `{"uuid":"<run-uuid>"}`. Для выбранного hook-профиля `native-text`
полный файл содержит точные UTF-8 байты исходной строки, включая физические переводы
строк, пустой текст, Unicode и NUL. Это текст, а не однострочная JSON-сериализация
строки. JSON record отдельно сохраняет эту же строку как значение `response`.
Для структурированных форматов полный JSON сохраняет все значения; JSON whitespace
не является побайтовым договором. Raw streams при наличии копируются точно.
Подробный текст записывается один раз вместе с полной формой.

Session-пример явно задаёт `full="full"`: такое имя подходит исходному тексту Bash
и полному JSON других профилей. `storage.response="result.json"` по-прежнему обозначает
JSON record. Сохранённые пути прежних запусков не переименовываются при смене policy.

Готовая публикация и last согласованы существующим storage owner. Pending не заменяет
последний готовый run. Ошибка записи не выдаёт старый успех с identity нового вызова.
Обычный completed replay сохраняет UUID/формы и не запускает prepare повторно;
новый намеренный запуск инструмента с новым native tool-use ID получает новый run.
Гарантия конкурентного однократного исполнения обработчика здесь не предоставляется.
`operator-managed` означает явное управление сроком хранения, а не GC по статусу Task.

## Выбор профиля для native-события

`parsers` — непустая таблица явно настроенных профилей. `selection` необязательна:
при её отсутствии native имя выбирает одноимённый профиль. При наличии используется
только её таблица, без переключения на старое сопоставление после ошибки.

| Поле правила selection | Договор |
|---|---|
| `id` | Уникальная непустая строка |
| `tool_name` | Настоящее имя native инструмента |
| `profile` | Существующий ключ `parsers` |
| `priority` | Явное целое от 0; boolean не подходит |
| `command_pattern` | `null` для безусловного правила этого инструмента либо корректный regex полного command |

Таблица непустая. Сравнение command — `fullmatch`, не поиск подстроки и не shell
интерпретатор. Если правила инструмента используют pattern, нужна настоящая строка
`tool_input.command`. Побеждает единственное совпадение наибольшего приоритета;
ничего не совпало — `unmapped_input`, равный максимум — `ambiguous_parser`.
Ошибка выбранного декодера не запускает другой декодер. Неизвестные raw options
сохраняются; обязательные известные поля, ссылки профилей и regex проверяются.

Обычный Poise и shell могут оба приходить с именем `Bash`. Session-пример выбирает
Poise по **полному объявленному command**, обычный Bash — отдельным безусловным
правилом меньшего приоритета. Оператор заменяет `@POISE_COMMAND_REGEX@` шаблоном
своего действительного entry и поддержанного quoting/redirection. Слова `poise work`
внутри `printf` сами не выбирают Poise. Native descriptor matcher выбирает события
для цепочки; selection внутри policy выбирает источник/парсер и не предоставляет
ни permission, ни session identity, ни исключение для service.

## Подготовка форм и поля Poise

У каждого профиля обязательны `parser`, `format`, `brief_template`,
`expanded_template`, `overflow="reject"`, а также положительные целые `chars`/`bytes`
для каждой из `brief`/`expanded`. Ограничение проверяется после подготовки текста;
обязательный внешний envelope дополнительно должен помещаться в display budget.

| Профиль | Полный вход и явные настройки |
|---|---|
| `bash` / `native-text` | Строка `tool_response`, включая пустую и JSON-подобную. Исходный код завершения и отдельные stdout/stderr недоступны; `exit_code=null`, streams отсутствуют. Префиксы summary/detail и бюджеты задаются явно; шаблоны не требуют недоступного exit. |
| `bash` / `native-exec-json` | Native object с `output` и terminal integer `exit_code`; `summary_prefix`, `detail_prefix`, `summary_missing`, `brief_without_summary` и шаблоны. Summary берётся по объявленному префиксу; `summary_missing="exit-only"` использует явный шаблон без summary. |
| `mcp` / `records-json` | Именно объявленная форма с `content` и `structuredContent.count` (integer), `structuredContent.names` (список строк); шаблоны `{count}`/`{names}`. Это не универсальный decoder любого MCP payload. |
| `mcp` / `mcp-content-json` | Целый MCP envelope с `content` и boolean `isError`; JSON или error text выбирается только по явному `content_index`. Полный [договор профиля](#профиль-mcp-content-json) задаёт paths/version/templates и сохраняет original. |
| `poise` / `saved-work-text` | Строка native Bash с JSON envelope настоящего Poise, содержащим абсолютный `response_path` под явным `source_root`. Парсер читает весь сохранённый body; исходная строка остаётся у delivery/machine consumer. Код процесса из body не выводится. |
| `poise` / `saved-work-json` | Native shell object `output`/`exit_code`; stdout содержит настоящий `response_path` полного SavedResponse под явным абсолютным `source_root`. Парсер получает весь body, original native envelope остаётся у delivery/machine consumer. |
| `poise` / `work-json` | Уже предоставленный полный объект с действительным строковым `status`; отдельный декодируемый envelope не требуется. |

У структурированного native object наличие `session_id` без terminal integer exit
означает pending. Строка не содержит такого подтверждения состояния процесса.
Объявленные native truncation markers дают partial либо unknown; exit 0 и наличие
счётчика токенов сами не доказывают полноту. Отдельные stdout/stderr не изобретаются
из merged native output. Если upstream обрезал данные, полный service не обещает
восстановить их из preview.

Poise требует `status:string`; остальные строковые поля нужны только при использовании
шаблоном. `expanded_template="{brief}"` принимает настоящие show/release без `next_work`.
Поддержаны простые именованные поля, без attribute/index expressions, conversions
или format-spec. Используемое отсутствующее/нестроковое значение даёт `parser_failed`.

Необязательные `expanded_fields` и `expanded_separator` задаются **парой**:

| Поле элемента expanded_fields | Договор |
|---|---|
| `path` | Непустой список непустых object keys от корня полного ответа |
| `required` | Явный boolean |
| `format` | `json` для настоящего JSON значения либо `count` для длины настоящего list |
| `template` | Строка с простым `{value}`; без traversal/conversion/spec |

Порядок элементов сохраняется; separator задаётся строкой явно. Отсутствующее
optional поле пропускает всю строку; required missing или неверная промежуточная
структура/тип дают `parser_failed`. Настоящий null в JSON печатается `null`, а не
подставляется вместо отсутствующего поля. Новых defaults нет.

Session-пример показывает один Poise профиль: brief по status, expanded по brief
и optional task/stage/results/after claims. Для реального fixture T1 формы имеют вид:

| Ответ | Кратко | Дополнительные строки expanded |
|---|---|---|
| bootstrap | `Poise: active` | `Task: "T1"`, `Stage: "tests"` |
| show с одним query | `Poise: read_only` | `Queries: 1` |
| release | `Poise: ownership_released` | `Task: "T1"`, `Task claim: null`, `Worktree claim: null` |

Это примеры настоящих изолированных CLI ответов, не инструкция создать T1 в рабочем
проекте. Используйте уже разрешённые операции своего настоящего entry; результат
не запускают повторно для восстановления вывода.

### Строковый native-вход

Настоящий Bash callback может передавать `tool_response` как строку, тогда как вложенный
программный вызов инструмента возвращает объект с известным ему кодом процесса и потоками.
Это разные границы. Полный session-пример выбирает `Bash/native-text` и
`Poise/saved-work-text` для строковой границы. Установленная поставка должна поддерживать
выбранные форматы; публикация policy сама по себе не обновляет пакет.

Для `native-text` строка сохраняется без декодирования напечатанного JSON. Например,
текст `{"exit_code":0}` остаётся stdout-текстом и не доказывает exit 0. Пустая строка —
полученный пустой результат; отсутствующее поле или значение другого типа даёт
`unmapped_input/tool_response/configure:input`. Native `exit_code` остаётся `null`,
оба отдельных потока — `null`; успешный код операции сохранения не является кодом Bash.

Шаблон `brief_template` допускает только `{summary}`, `brief_without_summary` —
постоянный текст, `expanded_template` — `{brief}` и `{details}`. В примере это
`Bash: {summary}; exit unavailable` либо `Bash: output received; exit unavailable`.
Историческое имя `summary_missing="exit-only"` выбирает второй явный шаблон;
оно не подставляет код завершения. Требование `{exit_code}` в этих текстовых шаблонах
отклоняется как `invalid_policy`.

Полнота полученной строки и исход выполнения независимы. Распознанный оформленный
маркер обрезки даёт `partial/native_output_truncated`, неоднозначный текстовый маркер —
`unknown/native_output_ambiguous`. `complete` описывает полученный источник в пределах
объявленного транспорта, а не подтверждает не наблюдавшиеся потоки или отсутствие
скрытой обрезки upstream. Проверка большого результата требует сравнения фактических
native байтов с независимо сохранённым источником производителя. Для partial/unknown
полный service отвечает `source_incomplete`; новый запуск не получает полный файл
предыдущего успешного результата.

`saved-work-text` выбирается только явным правилом команды Poise. Его строка должна
содержать корректный JSON envelope с настоящим абсолютным `response_path` в
`source_root`. Недостающий файл даёт `source_missing/response_path/provide:source`,
неверный envelope или адрес вне корня — `source_invalid/response_path/restore:source`.
Читается существующий полный ответ; повторного work-вызова нет. Обычный Bash с тем же
JSON-подобным текстом остаётся `native-text`. Статус сохранённого body не подменяет
неизвестный код процесса.

Если ваш транспорт действительно предоставляет объект `output`/integer `exit_code`,
выберите явно `Bash.format="native-exec-json"` и `Poise.format="saved-work-json"`.
Для Bash задайте соответствующие шаблоны `Bash: exit {exit_code}; {summary}` и
`Bash: exit {exit_code}`. Остальные общие опции полного примера сохраняются.
Прежние объектные форматы требуют объект и отвергают строку; текстовые требуют строку
и отвергают объект. Ошибка выбранного формата не запускает другой decoder.
Оба MCP-профиля остаются самостоятельными объектными контрактами.

## Профиль MCP content JSON

Профиль `mcp` / `mcp-content-json` обрабатывает явно объявленный MCP-ответ с JSON
внутри выбранного `content.text`. Полный session-пример содержит профиль
`mcp__example__projects` и правило `declared-mcp-projects`. Это обезличенное имя,
не установленный сервер. Замените его настоящим именем согласованно в ключе
`parsers`, правиле `selection` и обоих native matcher. Существующий `records-json`
с `structuredContent` остаётся отдельным объявленным форматом.

| Поле нового профиля | Договор |
|---|---|
| `parser`, `format` | Ровно `mcp` и `mcp-content-json` |
| `content_index` | Явное целое от 0; boolean не подходит. Используется только этот элемент `content`, без поиска другого блока |
| `version_path` | Непустой список непустых строковых ключей от корня JSON, декодированного из выбранного text |
| `expected_version` | Явное целое от 0; версия входа должна быть целой и точно равной ему. Boolean, строка или другое целое значение не подходят |
| `items_path` | Непустой список непустых строковых ключей от корня декодированного JSON к списку элементов |
| `name_path` | Такой же путь от каждого элемента к строковому имени; пустое имя допускается |
| `brief_template` | Непустая строка, только простое `{count}` либо постоянный текст |
| `expanded_template` | Непустая строка, простые `{brief}` и `{names}` либо постоянный текст |
| `error_brief_template` | Обязательная непустая строка, только простое `{error}` либо постоянный текст |
| `error_expanded_template` | Обязательная непустая строка, простые `{brief}` и `{error}` либо постоянный текст |
| `brief`, `expanded`, `overflow` | Прежние положительные `chars`/`bytes` и ровно `reject`; оба лимита каждой формы действуют вместе |

Все пути проходят только объекты по буквальным ключам: это не индексы массива,
выражения или `eval`. Путь, состоящий из одной строки вместо списка, пустой путь
или пустой ключ отклоняется. Подстановки шаблона не поддерживают attribute/index
traversal, conversion, format-spec или другие имена. Известные обязательные опции
проверяются до I/O; неизвестные исходные опции сохраняются и игнорируются. Новые
поля не становятся обязательными для старых форматов.

Целый MCP envelope содержит `content` — список и `isError` — boolean. Выбранный
блок является объектом с `type="text"` и строковым `text`. При `isError=false`
этот text декодируется как JSON-объект; `NaN` и `Infinity` не принимаются как JSON.
Версия проверяется по типу и значению, `items_path` выбирает список, `name_path` —
строку каждого элемента. `count` равен длине списка, `names` сохраняет порядок
через запятую. Пустой список даёт 0 и пустую строку имён. Отсутствующее поле,
настоящий null и значение неверного типа не подменяются друг другом.

При `isError=true` выбранный text является настоящим сообщением ошибки и
передаётся в error-шаблоны без требования version/projects JSON. Пустой text
допустим: с примерными шаблонами подробная форма равна `MCP: error` и переводу
строки, без выдуманного сообщения. `isError=true` и MCP `exit_code=null` остаются
в исходном результате; ошибка не превращается в успешный список или Bash exit 0.

Ни декодированный body, ни вычисленные count/names не записываются как
синтетический `structuredContent`. Полный исходный envelope, неизвестные значения
и невыбранные блоки сохраняются для full/machine потребителя. Подробная форма
содержит только выбранные шаблонами сведения; полный файл читают обычными
диапазонами строк. Это тот же session/run/last механизм, без нового хранилища.

### Нормальный ответ MCP

Следующий обезличенный вход соответствует полному профилю из session-примера:

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"schemaVersion\":2,\"projects\":[{\"projectId\":\"project-a\",\"label\":\"Alpha\"},{\"projectId\":\"project-b\",\"label\":\"Бета\"}],\"unselected\":{\"marker\":\"FULL_ONLY_EXAMPLE\",\"zero\":0,\"flag\":false,\"nullable\":null}}"
    },
    {
      "type": "image",
      "data": "opaque-unselected-example",
      "mimeType": "image/example"
    }
  ],
  "isError": false,
  "transport_extra": {
    "marker": "FULL_ONLY_EXAMPLE",
    "keep": [
      0,
      false,
      null
    ]
  }
}
```

Краткая форма:

```text
MCP: 2 projects
```

Подробная форма:

```text
MCP: 2 projects
Projects: Alpha,Бета
```

Маркер `FULL_ONLY_EXAMPLE`, дополнительные значения и image-блок остаются в полном
ответе, хотя они не выбраны для этих форм. Пример не проверяет доступность
реального MCP-сервера или доставку через hooks конкретного хоста.

### Ошибка MCP

Ошибка передаётся тем же профилем с настоящим текстовым сообщением:

```json
{
  "content": [
    {
      "type": "text",
      "text": "permission denied"
    }
  ],
  "isError": true,
  "transport_extra": {
    "marker": "FULL_ONLY_EXAMPLE",
    "keep": [
      0,
      false,
      null
    ]
  }
}
```

Краткая форма:

```text
MCP: error
```

Подробная форма:

```text
MCP: error
permission denied
```

Неверные известные настройки дают `invalid_policy` / `policy` / `correct:policy`: исправьте
объявленный профиль. Некорректные JSON, selected block, `isError`, path/type/version
дают `parser_failed` / `source` / `inspect:source`; сначала сравните полный реальный
вход с объявленной структурой. Например, целая версия 7 при `expected_version=2`
отклоняется, даже если остальная структура верна. Неподходящий целый envelope может
раньше дать `unmapped_input` / `tool_response` / `configure:input`. Другой text-блок
или парсер после ошибки не выбирается.

Если краткий или подробный текст не помещается в свои chars/UTF-8 bytes, отказ —
`budget_too_small` / `brief` либо `expanded` / `increase:budget`. Пересмотрите
явный бюджет или выбор значимых сведений. Ошибка подготовки не публикует готовые
формы и не заменяет last предыдущего успешного запуска. Исправленный новый вызов
не получает старый успех под новым идентификатором; инструмент не запускают
повторно лишь для получения его вывода.

## Чтение последнего результата

Запросы не требуют ручного UUID/response_path и не содержат Work envelope:

```json
{"schema":"poise-tool-result-request-1","operation":"expanded"}
```

```json
{"schema":"poise-tool-result-request-1","operation":"full"}
```

Expanded читает сохранённые text bytes, full возвращает абсолютный `full_path`.
Оба результата содержат run identity и owner metadata; они не исполняют инструмент,
не готовят формы заново и не меняют last. Для partial/unknown full возвращает
`source_incomplete`. Полный файл читают обычными ограниченными диапазонами строк;
его текст не вставляется в ответ целиком даже при небольшом размере.

Для читающих команд через Bash настройте отдельный профиль того же `bash` /
`native-text`, если обычный профиль отбирает только строки RESULT/DETAIL. Явные
`summary_prefix=""` и `detail_prefix=""` выбирают первую полученную строку для summary
и все полученные строки для details. Например, `brief_template="Lines: {summary}"`,
`brief_without_summary="Lines: empty"`, `summary_missing="exit-only"` и
`expanded_template="{details}"` сохраняют содержимое небольшого прочитанного диапазона
в подробной форме. Конечный LF в подготовленной форме не добавляется; полный источник
сохраняет его. Объявите положительные chars/bytes для обеих форм и выбирайте диапазон,
помещающийся в них. Ограничьте `selection.command_pattern` буквальной командой чтения
и корнем сохранённых full; приоритет должен выбирать этот профиль, сохраняя остальные
правила. Само чтение файла создаёт новый инструментальный результат и новый last.
Для следующих диапазонов используйте ранее полученный `full_path` исходного запуска.

Для распознавания настоящего служебного ответа через hook используйте поддержанную
простую команду с **буквальными абсолютными путями**, совпадающими с установленным
интерпретатором и той же policy. Например, после замены показанных путей:

```bash
/absolute/venv/bin/python -B -m poise tool-result --policy /absolute/tool-policy.json --audience agent <<< '{"schema":"poise-tool-result-request-1","operation":"expanded"}'
/absolute/venv/bin/python -B -m poise tool-result --policy /absolute/tool-policy.json --audience agent <<< '{"schema":"poise-tool-result-request-1","operation":"full"}'
```

Допускаются поддержанные flags интерпретатора `-B`, `-I`, `-P`, `-s`; выбор source
должен соответствовать их смыслу. CLI может принимать stdin отдельно. Ассоциация
native command не обещает разбирать shell variables/arrays/pipeline или произвольное
`<file`: её поддержанная форма — явный argv и optional JSON here-string. Если переданы
config/checkout, они совпадают с выбранным проектом и native cwd. Не копируйте чужие
caller/session ID.

До обычного capture владелец проверяет эту реальную команду, policy/digest,
caller/run и точное совпадение ответа с сохранённым результатом. Одних полей
`purpose="harness-service"`/`capability="tool-result"` недостаточно. Подлинный service
передаётся агенту как сохранённый текст либо путь, не становится новым tool run.
Поддержана фактическая строка native Bash с этим ответом; код исходного процесса
из неё не выводится. Для структурированного ответа с `output` проверяется фактический
exit: 0 для успешного service, 2 для подтверждённого отказа. В обоих случаях действуют
те же проверки команды и владельца.

Типизированный отказ того же чтения также доставляется как ответ харнесса. Например,
`source_incomplete` при запросе full для partial-результата сохраняет исходную причину,
UUID, last и файлы запуска; новый capture и повторная подготовка форм не выполняются.
Владелец сверяет весь отказ с действительным результатом своего сохранённого чтения
и применяет обычное ограничение служебного ответа. Одного `status="rejected"`
недостаточно: обычный Bash, напечатавший такой JSON, проходит capture и обновляет last,
как и соседний обычный ответ Poise.

## Сохранение и повтор

Ниже описан прямой capture/read API того же владельца. В session-режиме capture
использует явно названный профиль и записывает готовые формы; обычный native route
подаёт полный event через hook. Без session остаётся project-bound сохранение
и явное чтение по адресу.

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
Исключение с явно выбранным представлением — native hook `native-text`: его source
и полный файл содержат точный исходный UTF-8 текст, а JSON record сохраняет эту
строку как значение. Проверка целостности полного файла использует ту же фактическую
форму источника, а не хеш JSON-экранированной строки. У других форматов сериализация
остаётся прежней.

В режиме без session адрес `response_path` находится в пространстве project/checkout/caller
и ключа request. В session-режиме используется namespace caller и run UUID выше. Файлы копируются и
сверяются до публикации готового каталога; временный
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

Session route передаёт готовый brief/expanded и full path по описанному выше договору.
Следующий алгоритм фрагментов относится к низкоуровневой policy **без session**;
он не является вторым механизмом автоматической доставки. Capture с `agent`,
`view` и этот explicit hook используют своего общего владельца выбора фрагмента/бюджета. Для
обычного текста primary — stdout, если он существует,
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

`operation=hook` принимает ровно schema/operation/request_id/event/streams/capture/exit_code.
Event содержит настоящие session_id/turn_id/tool_use_id/hook_event_name/tool_name/
tool_input/tool_response/cwd; PostToolUse tool_use_id совпадает с request_id.
При session policy поддержанные имена/формы определяют parsers/selection. Без неё
старый explicit hook принимает Bash и использует base audit/enforce presentation.
Синтетический вызов не регистрирует настоящее native-событие и не создаёт authority.

Для автоматической доставки установите существующий sync builtin `tool-result`
с абсолютным options.policy_path; [полный обезличенный
handler](../../config/examples/tool-results-handler.example.json)
включается в routes настоящей definition2. Общий descriptor matcher может выбирать
Bash и объявленные MCP имена; command-intake при их совместном подключении сохраняет
собственную Bash область. Нового execution server или dispatcher нет.

Builtin сначала проверяет service association, иначе классифицирует полный native
input и публикует формы. Готовый brief добавляется в additionalContext, `continue:false`
заменяет модельное представление. Настоящий typed отказ добавляет error.code/field/action
и доступный исходный exit, сохраняет текущий raw event и выполняет штатный error/stop
контракт цепочки. Pending не выдаётся за готовую публикацию.

Настройка handler не равна факту доставки модели. Полный путь установки/immutable
resources/Start и диагностики — у [существующего владельца
hooks](runtime-hooks.md#вывод-инструмента-через-общую-цепочку).
Для каждой поддержанной actual host формы нужны настоящие PostToolUse receipt/input,
источник и model-visible brief. Для code mode дополнительно сравнивают полный original
return до model presentation; успешное Promise само по себе не доказывает сохранность.
Официальный [протокол](https://learn.chatgpt.com/docs/hooks) не заменяет эти измерения.
Hosted инструменты, не проходящие данный hook, не объявляются покрытыми.

## Четыре исполняемых сценария

Сохранены четыре проверяемых примера прямого capture/read API: базовая
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
| `unmapped_input` / `configure:input` | Проверить объявленные tool/profile/форму command и полный вход; автоматического decoder fallback нет |
| `ambiguous_parser` / `configure:input` | Устранить равенство максимальных приоритетов совпавших правил |
| `parser_failed` / `inspect:source` | Сверить действительный полный вход и шаблоны/проекцию выбранного профиля |
| `source_incomplete` / `inspect:source` | Учесть partial/unknown upstream; full service не восстанавливает утраченные данные |
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
