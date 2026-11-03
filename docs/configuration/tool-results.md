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

Готовая публикация и last согласованы существующим storage owner. Pending сохраняется
как отдельный неизменяемый снимок с `capture.state=unknown` и причиной
`native_process_pending`; last указывает на этот новый снимок, а чтение полного
результата через service отвечает `source_incomplete`. Предыдущий завершённый run
остаётся доступен по собственному адресу. Терминальный callback с тем же внешним
`tool_use_id` сохраняет отдельный завершённый снимок; pending не перезаписывается.
Ошибка записи не выдаёт старый успех с identity нового вызова.
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
| `file_read` | Необязательный boolean, по умолчанию `false`; `true` исключает выбранное native-чтение файла из сокращения |
| `command_pattern` | `null` для безусловного правила этого инструмента либо корректный regex полного command |

Таблица непустая. Сравнение command — `fullmatch`, не поиск подстроки и не shell
интерпретатор. Если правила инструмента используют pattern, нужна настоящая строка
`tool_input.command`. Побеждает единственное совпадение наибольшего приоритета;
На уровне selector отсутствие совпадения даёт `unmapped_input`, равный максимум —
`ambiguous_parser`. В session capture/native route отсутствие профиля или неподдержанная
wire-форма получает generic-представление, описанное
[ниже](#неизвестный-формат-и-асинхронная-регистрация); остальные отказы сохраняются.
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
| `mcp` / `mcp-checks-json` | Полный согласованный ответ настроенного инструмента проверок: один JSON text в `content`, тот же `structuredContent` и `isError`. [Контракт профиля](#профиль-mcp-проверок) различает run/preview и cancel. |
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
полученный пустой результат. Значение другого типа не соответствует native-text:
низкоуровневый decoder даёт `unmapped_input`, а session route сохраняет полученное
значение через generic unknown_format. Отсутствие обязательного источника остаётся
отказом, а не пустым результатом. Native `exit_code` строки остаётся `null`,
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

## Профиль MCP-проверок

Для [настроенного инструмента проверок](named-project-checks.md#mcp-stdio-настройка-и-вызов)
используется профиль `parser="mcp"`, `format="mcp-checks-json"` того же
владельца tool-result. Он получает **полный** MCP tool response. В `content`
должен быть ровно один text с JSON, совпадающим со `structuredContent`.
Поддержаны `poise-mcp-check-result-1` с вложенным `poise-project-checks-result-1`
и `poise-mcp-check-cancel-result-1`. Это контракт этих конкретных ответов,
а не распознавание любого MCP JSON по сходным ключам.

Добавьте профиль в `parsers` заполненной session policy. Ниже один **фрагмент
значения профиля**, не полная policy:

```json
{
  "parser": "mcp",
  "format": "mcp-checks-json",
  "brief_template": "Проверки: {status}",
  "expanded_template": "{brief}\nЗапуски: {names}",
  "error_brief_template": "Проверки: ошибка",
  "error_expanded_template": "{brief}\n{error}",
  "overflow": "reject",
  "brief": {"chars": 128, "bytes": 256},
  "expanded": {"chars": 4096, "bytes": 8192}
}
```

| Поле | Разрешённые значения шаблона |
|---|---|
| `brief_template` | `{status}` фактического вложенного результата или cancel status. |
| `expanded_template` | `{brief}` и `{names}` — выбранные ID проверок, включая вложенные наборы; для cancel — target invocation ID, если известен. |
| `error_brief_template` | `{error}` либо фиксированная строка; error содержит фактический JSON ошибки, если он доступен. |
| `error_expanded_template` | `{brief}` и `{error}`. |
| `brief`, `expanded` | Явные положительные целые `chars` и `bytes`; пример не заменяет выбор бюджетов установки. |

`isError:true` выбирает error templates. Неуспех самой выполненной проверки
может передаваться при `isError:false`: тогда виден её настоящий status,
а подробные причины остаются в полном результате и streams. Несогласованные
payloads или неподдержанная схема дают `parser_failed`; превышение выбранного
бюджета — `budget_too_small`. Другой parser автоматически не подставляется.

При ключе профиля `checks` добавьте в существующую `selection` правила для
**действительных** native имён своего MCP server. Пример для server `repo_checks`:

```json
[
  {
    "id": "checks-run",
    "tool_name": "mcp__repo_checks__run",
    "profile": "checks",
    "priority": 10,
    "command_pattern": null
  },
  {
    "id": "checks-cancel",
    "tool_name": "mcp__repo_checks__cancel",
    "profile": "checks",
    "priority": 10,
    "command_pattern": null
  }
]
```

Существующие profiles/rules сохраняются; для этих же имён расширяются matcher
обоих native descriptor по
[договору подключения](runtime-hooks.md#подключение-инструмента-проверок).
`tool-result` остаётся PostToolUse handler. Конфигурация session policy выбирает
форматтер, но не создаёт server, не предоставляет разрешение исполнения и не
меняет полноту ответа, потребляемого программой.

Обе формы готовятся один раз из полного источника. Краткая выдаётся через hook,
расширенная и полный source сохраняются в папке запуска с тем же UUID;
сессионный last указывает на этот запуск. Применяются команды
[expanded/full последнего результата](#чтение-последнего-результата): expanded
выдаёт сохранённый текст, full — путь для чтения диапазонами строк. Служебный
ответ harness не захватывается как следующий tool result и не заменяет last.
Полные MCP поля и stdout/stderr проверки читаются по сохранённым source refs;
ограниченное представление не используется для машинного разбора и решения
о фактическом завершении. Повторно запускать проверку ради вывода не требуется.

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
раньше дать `unmapped_input` в низкоуровневом decoder; session route сохраняет
неподдержанную форму через generic unknown_format. Другой text-блок
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

Для запрошенного содержимого файла используйте исключение
[чтения файлов](#чтение-файлов-и-граница-ответственности), а не подбор размера
brief/expanded. Ссылка на сохранённый полный результат остаётся штатным ответом
операции `full`; отдельный запрос содержимого обслуживает выбранный reader.

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

## Чтение файлов и граница ответственности

Запрошенный файл кода, тестов, правил или документации выдаётся целиком либо в
запрошенном диапазоне через выбранный reader. Ограничения отображения обычного
вывода инструментов к этому содержимому не применяются. Собственные условия
допуска, пределы запроса и понятные отказы reader сохраняются; его документированная
нормализация текста также сохраняется. Автоматическая вставка документов не нужна:
агент сам решает, какие данные запрашивать.

Для native-чтения задайте `file_read: true` в точном правиле `selection`.
[Session-пример](../../config/examples/tool-results-session.example.json) содержит
`@FILE_READ_COMMAND_REGEX@`: замените его regex полного фактического вызова reader.
Приоритет 40 выше обычного Poise20 и Bash10. `profile` остаётся обязательной ссылкой
на существующий профиль, но выбранное чтение не запускает его parser, storage или
регистрацию неизвестного формата. Handler передаёт накопленный ответ цепочки без
замены содержимого; порядок и управление остальных handlers сохраняются.

Отсутствующий либо `false` флаг сохраняет обычную обработку. Используются те же
fullmatch, наибольший приоритет и отказ при равенстве максимумов. Неподходящая
команда остаётся обычным выводом, включая generic для неизвестной формы.
Заголовок в возвращённом тексте не объявляет чтение. Низкоуровневая явная операция
`hook` и machine API сохраняют свои договоры: исключение относится к native handler.

Ответственность компонента — предоставить данные через поддерживаемый API и
сохранить его договор в нашей hook-цепочке. Доказательство получения или понимания
данных моделью, финальный запрос OpenAI и серверная сохранность байтов не требуются.

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

Builtin сначала исключает явно выбранное чтение файла. Для обычного вывода
проверяет service association, иначе классифицирует полный native
input и публикует формы. Готовый brief добавляется в additionalContext, `continue:false`
заменяет модельное представление. Настоящий typed отказ добавляет error.code/field/action
и доступный исходный exit, сохраняет текущий raw event и выполняет штатный error/stop
контракт цепочки. Pending сохраняется как незавершённый снимок, а не результат
успешного завершения исходной операции.

Подключение и диагностика принадлежат [владельцу
hooks](runtime-hooks.md#вывод-инструмента-через-общую-цепочку). Проверяют ответ нашего
API и поведение установленной цепочки: обычный результат сокращается, явное чтение
сохраняет запрошенное содержимое, программный потребитель получает полный ответ.
Локальный PostToolUse обслуживает события, которые передаёт поддерживаемый host;
недоступный upstream источник не восстанавливается.

## Профиль чтения чата

[Session-пример](../../config/examples/tool-results-session.example.json) содержит
отдельный профиль `mcp-thread-json` и правило для `mcp__example__read_thread`.
Оператор меняет имя инструмента в профиле и selection на фактическое MCP-имя.
Профиль использует `parser=mcp`, `content_index`, `expected_version`, шаблоны
`brief_template`/`expanded_template`, пределы `brief`/`expanded` и `overflow=reject`.
Полный состав приведён в примере; существующие профили Bash, Poise и MCP сохраняются.

`content[content_index].text` содержит JSON с `schemaVersion`, `thread.id`,
`thread.status.type`, `page.hasMore` и массивом `turns`. Первый элемент `turns` считается
последним: берутся его `status` и сообщение `error.message`, если ошибка задана;
иначе — последний элемент `items` типа `agentMessage` с полем `text`.
Пустой `turns` даёт статус `none` и пустое сообщение. Шаблон brief получает
`thread_id`, `status`, `turn_status`; expanded дополнительно получает
`brief`, `message` и `has_more`. Сообщение сокращается в общем владельце представлений до
обоих пределов expanded. Команды и reasoning не включаются в это представление;
полный JSON и программный результат остаются целыми.

Несовпадение версии переводит полученное значение в generic как неизвестный формат.
Повреждённый известный ответ, включая пустой `content`, возвращает `parser_failed`,
а не успешное generic-представление. Невместимый обязательный текст шаблона сохраняет
обычный отказ бюджета.

## Неизвестный формат и асинхронная регистрация

В session-режиме неизвестное имя инструмента получает причину `unknown_tool`,
неподдержанная wire-форма выбранного маршрута — `unknown_format`. Общий formatter
готовит bounded brief с именем, классификацией и доступным exit, а expanded добавляет
выдержку в пределах `inspection.max_excerpt_bytes`. Это классификация представления,
не значение `capture.state`: полное сохранение полученного JSON само по себе не
доказывает завершение исходной операции. Pending отдельно имеет причину
`native_process_pending`. Неоднозначность selector, ошибочный parser в конфигурации,
повреждение источника и malformed известного контракта остаются типизированными отказами.

Полное принятое значение и доступные потоки сохраняются до регистрации. Запись
результата содержит `unknown_observation` с ровно десятью полями:

| Поле | Значение |
|---|---|
| `schema` | `poise-tool-result-unknown-1` |
| `tool_name`, `format` | Имя инструмента и `json-object`, `json-array`, `native-text`, `json-null`, `json-boolean` либо `json-number` |
| `reason`, `policy_version` | `unknown_tool`/`unknown_format` и версия policy |
| `session_id`, `turn_id`, `tool_use_id` | Настоящие native идентификаторы либо `null` при прямом capture |
| `request_id` | Ключ исходного запроса |
| `full_source` | Объект ровно с абсолютным `path` полного источника и его `sha256` |

По умолчанию registry находится в `session.root/<caller-key>/unknown-inputs`.
Отдельный процесс выбранного provider Python получает только observation на stdin;
агентский ответ не ждёт завершения регистрации. Worker проверяет источник внутри
`storage.root` и его SHA256, затем сохраняет неизменяемый JSON по хешу канонической
observation. Точный повтор даёт ту же запись. Это хранилище для последующего анализа
неизвестных форматов; автоматическая разработка новых парсеров этим кодом не выполняется.

Необязательный `unknown_inputs`, если задан, содержит ровно `registry_root`,
`registration_command` и `registration_cwd`. Оба пути абсолютные; команда — непустой
argv-массив без shell-интерпретации. Команда получает observation на stdin и должна
выполнить тот же контракт регистрации. Session-пример оставляет объект отсутствующим,
чтобы использовать штатный worker. Его запуск задаётся provider runtime и внешними
корнями, а не интерпретатором ERP-клиента.

stdout/stderr worker сохраняются рядом с полным источником в
`unknown-registration.stdout`/`unknown-registration.stderr`. Успешный запуск процесса
не является подтверждением регистрации. При сбое запуска сохранённая observation
позволяет явный повтор через ту же публичную операцию `tool-result`:
`schema=poise-tool-result-request-1`, `operation=register_unknown`,
`observation=<сохранённый объект>`. Поле `request_id` у этой операции отсутствует.
Ответ — `status=registered` и `observation_path`. Повтор не требует нового исполнения
исходного инструмента. Ошибки источника возвращают `source_invalid`/`source_unreadable`,
ошибка записи — `registry_write` с действием `retry:register_unknown`.

## Переносимый компонент и независимый потребитель

Provider требует Python `>=3.15,<3.16`. Клиент ERP на Python 3.12 вызывает явно
выбранный provider executable через публичный JSON `tool-result`; импорт модуля,
требующего 3.15, внутрь 3.12 не поддерживается. Нужны независимые явные code/settings/data
roots; checkout аргумент выбирает целевой проект, а не код выполняемого provider.
Синтаксис CLI и обязательные аргументы описаны в
[подготовке и вызове](#подготовка-и-вызов), операции чтения — в
[чтении последнего результата](#чтение-последнего-результата) и
[адресном чтении](#адресное-чтение-и-поиск).

Прямой библиотечный потребитель выбирает конкретную development revision или release
через свой package manager и фиксирует реально установленную версию/lock согласно
[владельцу библиотечных потребителей](stride-launcher.md#библиотечные-потребители-и-версии).
ERP использует другой существующий маршрут: stdlib-only клиент Python 3.12 вызывает
настроенный launcher; его `working_directory` и `project_config` выбирают окружение
и проект. Source revision и Python provider выбирает оператор установки Poise.
Установка Poise SDK или wheel в ERP-клиент для этого маршрута не требуется.
Task checkpoint идентифицирует принятые исходники, тесты и документацию; это не
публикация, установка или интеграция. Постоянная поставка не зависит от disposable
Task worktree. Корни, provider executable, регистрационная команда и policy задаются
потребителем; пути текущей установки не вшиты в компонент. ERP адаптирует существующий
proxy и не копирует семантику Task, Requirements или lifecycle.

При переносе проверяют выбранную provider revision и независимые корни, полный
программный ответ, ограниченные agent views, exact-repeat регистрацию и клиентский
вызов 3.12 → выбранный 3.15. Принятый пакет проверок находится в
[universal scenarios](../../tests/runtime_services/test_tool_results_universal.py),
[фикстуре ERP-клиента](../../tests/runtime_services/fixtures/tool_results/erp_wire_consumer.py)
и [контракте источника](../../tests/runtime_services/test_tool_results_source_contract.py).
Проверка клиента на изолированных корнях не доказывает установку в рабочую ERP-среду.

При установке проверяют доступные API и настроенный hook-протокол потребителя.
Проверка окончательного запроса OpenAI, серверной доставки байтов или понимания
данных моделью не входит в ответственность этого компонента и не является
условием интеграции. Аналитику расхода строят по существующей телеметрии;
сравнение экономии не задерживает поставку исправного компонента.

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
| `unmapped_input` / `configure:input` | Проверить низкоуровневый selector/decoder; session route переводит неподдержанную форму в generic, а не выбирает другой специализированный parser |
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
