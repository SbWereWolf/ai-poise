# Именованные проверки проекта

## Назначение и границы

`poise checks` выбирает и выполняет настроенные проверки, пакеты и наборы выбранного
проекта. Операция доступна без Task или Sprint. Вызывающий, конфигурация проекта,
зарегистрированная рабочая копия и политика проверок выбираются явно.

Именованная проверка сохраняет фактический запуск, полный доступный вывод, материалы
и эксплуатационную историю. Эта история помогает выбрать ограничения следующего
запуска; она сама по себе не является доказательством приёмки Task на новом коммите.
`CheckRegistry`, Task evidence и специализированный кэш пакетов AI poise/C018 имеют
[своих владельцев](../architecture/boundaries.md#именованные-проверки-проекта).
Команда `poise check` обслуживает окружение и имеет другой контракт.

Реальный путь исполнения `exec` поддерживает POSIX Linux/Darwin при совпадении
настроенных платформ. Linux/Ubuntu проверен настоящими процессами. Данные адаптера
`windows_cmd` и его argv можно описать и предварительно просмотреть; данный
исполнитель не объявляет Windows-доставку работоспособной: запуск получает
`runtime_unavailable`. Наличие профиля PHP/npm или примера драйвера также не
подтверждает установку соответствующих инструментов.

## Настройка вне Git

Сначала подготовьте проект через [публичный владелец настройки
проекта](project-setup.md) с выбранным шаблоном. Используйте его конфигурацию и
зарегистрированный checkout; справочник проверок не создаёт проект, не регистрирует
чужую Git-копию и не редактирует managed project settings.

В поставке находятся четыре обезличенных шаблона:

- [Политика](../../config/examples/project-checks.example.json).
- [Профили команд](../../config/examples/project-check-profiles.example.toml).
- [Пакеты](../../config/examples/project-check-packages.example.toml).
- [Наборы](../../config/examples/project-check-suites.example.json).

Скопируйте их в выбранный операторский каталог вне Git. Заполните абсолютные пути
к Python, каталогам и файлам проекта; сохраните рабочие значения вне source commits.
Путь `storage.directory` должен находиться внутри настроенного `paths.state`
выбранного проекта. Пути политики, каталогов профилей и отчётов выбираются независимо
от checkout. Само заполнение шаблона не устанавливает или обновляет Poise.

Для установленной поставки используйте её Python:

```bash
poise=( "${poise_python:?Абсолютный Python выбранной установленной поставки}" -I -B -m poise )
"${poise[@]}" checks --help
"${poise[@]}" checks --config "$project_json" --checkout "$checkout" \
  --policy "$checks_policy" --view expanded < "$packet"
```

`project_json`, `checkout`, `checks_policy` — заполненные абсолютные пути;
`packet` — файл одного запроса из следующего раздела. Если установленная версия не
имеет `checks`, её обновляет владелец поставки потребителя. `--help` проверяет наличие
входа, а не готовность проекта и команд.

При проверке исходников другой копии явно выберите её import path и cwd:

```bash
cd "${poise_checkout:?Абсолютная проверяемая копия Poise}"
PYTHONPATH="$poise_checkout/src" "$poise_python" -B -m poise checks \
  --config "$project_json" --checkout "$checkout" --policy "$checks_policy" \
  --view full < "$packet"
```

Здесь `-I` не используется: он исключает выбранный `PYTHONPATH`. Это отдельный
source-bound запуск, который не переключает live hooks, launcher или установленный
пакет. Проектные argv и cwd остаются данными явно выбранной политики.

### Параметры политики

Все перечисленные поля обязательны. Неизвестные конфигурационные поля сохраняются
в исходных данных и не влияют на поведение; известные поля проверяются строго.
JSON `null` ниже является явным значением, а не отсутствующим полем.

| Поле | Значение и назначение |
|---|---|
| `schema` | `poise-project-checks-policy-1`. |
| `profiles_path` | Абсолютный путь TOML-каталога профилей. |
| `packages_path`, `suites_path` | Абсолютные пути TOML-пакетов/JSON-наборов; явный `null` отключает соответствующий каталог. |
| `storage.directory` | Абсолютный корень обычных проверок внутри state проекта. |
| `storage.ledger_file`, `storage.lock_file` | Относительные имена авторитетного SQLite-реестра и его lock. |
| `storage.runs_directory`, `storage.cache_directory`, `storage.response_directory` | Относительные пути запусков, необязательных reuse payloads и полных ответов. |
| `timing.history_multiplier` | Положительный множитель к пригодной истории. |
| `timing.poll_seconds`, `timing.cleanup_seconds` | Положительные параметры наблюдения и завершения процесса. |
| `timing.diagnostic_override_max_seconds` | Положительная верхняя граница явного диагностического увеличения. |
| `environment.clear_keys` | Список переменных, удаляемых из наследуемой среды перед разрешением и запуском команды. |
| `publication.report_directory` | Абсолютный каталог отчётов явной калибровки. |
| `runtime_adapters` | Список уникальных адаптеров: `id`, непустые `platforms`, `invocation`, `command_processor`. |
| `drivers` | Список уникальных драйверов: `id`, непустой `argv_prefix`, `cwd`, `path_prefix`. |

Платформы: `linux`, `darwin`, `windows`. Для `exec` значение `command_processor`
равно `null`; для `windows_cmd` задаётся конкретный обработчик. Неявного `cmd`,
`COMSPEC` или shell fallback нет. Драйвер соединяет `argv_prefix`, `arguments`
шага и `test_paths` в этом порядке. `cwd` и `path_prefix` — `.` либо канонические
относительные пути внутри checkout.

### Профили команд

TOML содержит `schema_version = 1`, непустой список `commands` и явный список
`evidence_contracts`. В TOML нет `null`: необязательное поле опускается. Ключ профиля —
`command_id + workload_class + runtime_class`; он уникален.

Профиль `native-hook-regression / hooks-runner-named-checks / python` из
`config/examples/project-check-profiles.example.toml` проверяет исходники самого
Poise: целиком `tests/hook_transport`, `tests/named_checks` и файл
`tests/runner/test_registered_check_runner.py`. Он использует CPython 3.15 из
`tools/.venv` выбранной рабочей копии. Первый запуск не ограничен по времени;
после полного успешного запуска `calibrate` публикует измерения и следующий
предел в выбранном TOML-реестре. Сохраните опубликованный реестр вместе с
настройками инструментов: очистка временных логов не должна удалять будущий
предел. Сбои в дополнительных контекстах фиксируются отдельно и не считаются
успешным измерением этого профиля.

| Поля профиля | Значение |
|---|---|
| `command_id`, `workload_class`, `runtime_class` | Явные идентификаторы команды, нагрузки и адаптера. |
| `platforms`, `invocation`, `argv`, `cwd` | Поддержанные платформы, способ вызова, непустой argv без shell-пересборки и рабочая папка. |
| `parser`, `require_parser_confirmation` | Выбранный парсер и boolean требования распознанной сводки. |
| `execution_timeout_seconds` | Неотрицательный целочисленный начальный предел. `0` означает запуск без начального ограничения времени. После успешного измерения следующий предел рассчитывается по истории успешных запусков с настроенным множителем; при `1.1` он равен максимальной длительности плюс 10%, с округлением вверх до секунды. Операция `calibrate` сохраняет измеренный предел в профиле. |
| `observed_max_success_seconds`, `observed_max_progress_gap_seconds` | Неотрицательные опубликованные наблюдения; начальные нули не являются измерениями. |
| `stall_timeout_seconds` | Неотрицательный целочисленный предел простоя; `0` при unavailable progress. |
| `initial_limit_reason`, `last_calibrated_at` | Причина исходного предела и ISO timestamp с временной зоной. При первоначальной настройке явно укажите её происхождение. |
| `progress` | `mode = "unavailable"` либо `mode = "regex"`, `pattern`, `completed_group`, необязательные `total_group`, `stage_group`. |
| `output` | Положительные integer `success_bytes`, `failure_bytes`, `timeout_bytes`, `max_failures`. |
| `prerequisites` | Явные списки `executables`, `files`, `environment`. |
| `cache` | Boolean `enabled`, списки `input_paths`, `environment`, явный `input_selection`. |
| `evidence_id` | Необязательная ссылка на объявленный контракт материалов. |
| `subject` | Необязательный явный документационный предмет, описанный ниже. |

`input_selection` равен `filesystem-glob` или `git-tracked-markdown`. Второй режим
требует пустого `input_paths` и положительных integer `max_input_files`,
`max_input_bytes`; в первом эти пределы не задаются. Regex-группы должны существовать
в pattern. `unavailable` не принимает regex-поля; его stall и опубликованный gap равны нулю.

В текущем механизме `output.success_bytes`, `failure_bytes`, `timeout_bytes`
читаются и сохраняются как данные профиля; они **не обрезают** полный вывод и не
выбирают отдельный бюджет представления. `max_failures` ограничивает список
семантических сообщений парсера. Бюджет агентского представления задаёт общий
`limits.output_chars` проекта. `cache.environment` также читается и сохраняется;
оно не добавляет ось environment в content key. Эти различия важны при выборе
параметров: сохранённое поле не доказывает дополнительного эффекта.

## Вызывающий и окружение

Native-вызов использует подлинный `CODEX_THREAD_ID`/`CODEX_SESSION_ID`. Если обе
переменные присутствуют, они должны совпадать. `POISE_CALLER_BINDING` одновременно
с native identity не передаётся.

Отдельный прямой процесс использует постоянный абсолютный `POISE_CALLER_BINDING`;
genuine caller owner создаёт файл привязки. Для изолированной проверки с такой
привязкой задайте среду только этого процесса:

```bash
env -u CODEX_THREAD_ID -u CODEX_SESSION_ID \
  POISE_CALLER_BINDING="$caller_binding" \
  "${poise[@]}" checks --config "$project_json" --checkout "$checkout" \
  --policy "$checks_policy" --view full < "$packet"
```

Другой допустимый вариант — сохранить native identity и убрать binding из прямого
процесса. Сессионный launcher и глобальная identity не заменяются ради теста.
Привязка вызывающего и служебная телеметрия могут создаваться без Task; это не
создание, захват или изменение задачи/Sprint.

Относительный argv с разделителем пути, например `./local-check`, разрешается
от фактического cwd команды. Для имени из PATH относительные и пустые компоненты
PATH также относятся к этому cwd. Проверка исполнимости и `runtime.executable`
используют ту же очищенную среду, что запуск; исходный argv сохраняется.
При отсутствии PATH действует стандартный поиск самого subprocess/ОС, а не
добавленный Poise путь. Абсолютный executable остаётся абсолютным.
`prerequisites.files` относятся к checkout, а `prerequisites.executables` —
к контексту исполнения. Наличие файла без права исполнения не удовлетворяет
предпосылке executable. Предпосылки повторно наблюдаются перед настоящим запуском.

## Запросы и выбор

Один stdin-пакет имеет ровно `schema`, `operation`, `items`:

```json
{
  "schema": "poise-project-checks-request-1",
  "operation": "list",
  "items": [
    {"kind": "profiles", "platform": "linux"}
  ]
}
```

`items` непуст и ограничен `batch.max_items` проекта; вход ограничен
`batch.max_input_bytes`. Повторные JSON-ключи, неизвестные поля запроса, повторные
элементы и неверные типы получают объяснённый отказ. Boolean не считается числом.
До первого functional effect проверяется весь пакет, включая поздний ошибочный элемент.

| Операция | Элементы `items` |
|---|---|
| `list` | `{kind: profiles|packages|suites, platform: null|linux|darwin|windows}`. |
| `describe` | Один или несколько selectors. |
| `preview`, `run` | `{selection: selector, options: options}`. |
| `result` | `{result_id: ID}` сохранённого execution result. |
| `calibrate` | `{profile: profile_selector, evidence_run_ids: [ID…], expected_profile_sha256: SHA256}`. |
| `package_impact` | `{changed_paths: [конкретные относительные пути…]}`. |

Таблица показывает форму, а JSON ниже — копируемый синтаксис. Selector профиля
содержит `kind = profile`, `id`, `workload_class`, `runtime_class`. Последние две
оси могут быть `null`; неизвестное необязательное значение даёт warning, после
чего всё равно требуется единственный кандидат. Неоднозначность имеет свой отказ
с вариантами выбора. Selector пакета/набора: `{kind: package|suite, id}`.
`suite_set` имеет другую форму, указанную в разделе наборов.

Пример `describe`:

```json
{
  "schema": "poise-project-checks-request-1",
  "operation": "describe",
  "items": [
    {"kind": "profile", "id": "example-python", "workload_class": "example", "runtime_class": "python"}
  ]
}
```

Пример `preview`; для настоящего запуска замените только `operation` на `run`:

```json
{
  "schema": "poise-project-checks-request-1",
  "operation": "preview",
  "items": [
    {
      "selection": {"kind": "profile", "id": "example-python", "workload_class": "example", "runtime_class": "python"},
      "options": {"fresh": false, "scope_paths": null, "timeout_override": null}
    }
  ]
}
```

`options` всегда содержит boolean `fresh`, nullable список `scope_paths` и
nullable `timeout_override`. Необязательный `documentation_scope` передаётся
только в описанном документационном сценарии. `preview` имеет `planned`, а
`available` отражает планирование/доступность платформенного адаптера, а не
подтверждённую установку executable, выполнение предпосылок или запуск ребёнка.

Для `result` возьмите именно `items[…].result_id` из фактического execution:

```json
{
  "schema": "poise-project-checks-request-1",
  "operation": "result",
  "items": [
    {"result_id": "REPLACE_WITH_ACTUAL_RESULT_ID"}
  ]
}
```

Это чтение возвращает сохранённый исход без нового ребёнка. `run_id` принадлежит
ProcessRun и используется для истории/калибровки; он не заменяет `result_id`.

## Выполнение и результаты

Зависимости и шаги выполняются в объявленном порядке. Неподтверждённый или
неуспешный шаг останавливает последующие; результат показывает выполненные шаги
и `not_executed`. Если поздняя предпосылка стала недоступной, прежние реальные
результаты сохраняются. Ошибка допуска всего пакета возвращает `items: []` без
новых functional records, samples, cache или Task effects.

| Представление | Смысл |
|---|---|
| `execution` | Сохранённый ExecutionItem с result ID, provenance, шагами и cache metadata. |
| `steps[].record` | Настоящий ProcessRun: argv/cwd/runtime, actual exit, Clock-пара, длительность, полнота, parser/evidence и run ID. |
| `reuse` | Неизменённый исходный execution в `source` и ссылка на его result ID; нового процесса/Clock/sample нет. |
| `not_executed` | Явно оставшаяся работа после остановки. |

Исходы процесса различают `passed`, `failed`, `timed_out`, `stalled`, `cancelled`,
`unconfirmed`. Наблюдённый `actual_exit_code` и полнота capture сохраняются отдельно.
Код выхода CLI: `0` для ready/planned/passed/published/unchanged, `2` для invalid,
`3` для unavailable, `1` для остальных известных неуспехов и ошибки доставки.
У чтения `result` корневой статус может быть `ready`, а внутри остаётся исходный
статус execution. Это разные уровни, а не новый successful run.

Распознаются `generic`, `phpunit`, `vitest`, `playwright`, `phpstan`, `deptrac`,
`build`. `confirmed` означает распознанную сводку выбранного парсера. Exit 0 с
распознанными failure counts сохраняет фактический исход по действующему контракту;
сами counts/failures доступны для оценки. Требование parser confirmation с
нераспознанной сводкой, неполные обязательные материалы или capture дают
`unconfirmed`, а не выдуманный успех.

Python API `poise.interfaces.project_checks.execute` принимает ровно
`config_path, checkout, policy_path, stream, output, error_output, clock` и выдаёт
полный canonical JSON. Production CLI передаёт `SystemClock`. CLI-представления
`brief`, `expanded`, `full` используют общий владелец: даже `--view full` обычно
возвращает ссылку `response_path` на сохранённый полный файл. При раннем отказе до
готовности хранилища полный адрес может отсутствовать и выводится доступный ответ.
Читайте реальные stdout/stderr и saved response по ссылкам; preview не является
машинным полным результатом. [Чтение сохранённого
ответа](../workflows/runner.md#чтение-сохранённого-результата-без-повторного-выполнения)
не повторяет операцию.

`operation: null` допустим в раннем отказе, когда известная операция ещё не
получена/распознана; это не подразумеваемый `run`. После распознавания известная
операция сохраняется в ответе. При отсутствии пригодного state ссылки остаются
null/пустыми, а не указывают на воображаемые файлы.

## Прогресс и длительность

Длительность/история используют сравнимые монотонные наблюдения публичного Clock.
UTC служит аудиту. Разные comparison domains или обратный порядок дают
неподтверждённую длительность; она не исправляется clamp и не подменяется временем
другого источника. Внутренние реальные deadline/cancel/cleanup исполнителя имеют
своего владельца и не являются вторым источником эксплуатационных samples.

Regex progress распознаёт реальные полные строки из capture. `completed_group`
выбирает число выполненного, `total_group` — необязательный общий объём,
`stage_group` — этап. Сохранены progression events и максимальный сравнимый gap.
Действующий прогресс обновляет предел отсутствия прогресса; произвольный байт
не считается предметным продвижением. Для `unavailable` такой прогресс не заявлен.
Это отличается от [специализированной byte-policy Task
runner](check-runner-timeouts.md#выбор-лимита).

## История, калибровка и диагностика

Без пригодной истории действуют начальные пределы профиля. Пригодные samples
принадлежат тому же проекту/command/workload/runtime, реально выполнены и имеют
verified success, полный capture и сравнимую Clock-пару. Failed, incomplete и
incomparable записи сохраняются как факты, но не расширяют такую историю.

Обычный hard limit равен `ceil(max(duration) × history_multiplier)` по пригодной
истории. У named profiles нет дополнительного initial floor. Для regex stall
аналогично используется максимальный пригодный progress gap; при его отсутствии
остаётся явно настроенный stall. Обычный запуск не переписывает TOML.

Явная `calibrate` использует реальные successful `run_id`, профиль и SHA256
актуальных исходных байтов файла профилей:

```json
{
  "schema": "poise-project-checks-request-1",
  "operation": "calibrate",
  "items": [
    {
      "profile": {"kind": "profile", "id": "example-python", "workload_class": "example", "runtime_class": "python"},
      "evidence_run_ids": ["REPLACE_WITH_ACTUAL_RUN_ID"],
      "expected_profile_sha256": "REPLACE_WITH_SHA256_OF_CURRENT_PROFILE_FILE"
    }
  ]
}
```

Замените оба значения до отправки: SHA256 должен содержать ровно 64 hex-символа.
Например, `sha256sum "$profiles_file"` читает нужные исходные байты. Запрос с
устаревшим SHA получает `calibration_conflict`. Публикуются выбранные значения
и отдельный отчёт; прочие профили и неизвестные raw values сохраняются. Если
профиль записан, а отчёт не записан, результат честно показывает частичный исход.
Исправьте место отчёта и повторите явный запрос с текущим SHA/теми же реальными
evidence IDs; уже опубликованный профиль не требуется переписывать.

`timeout_override` содержит `seconds`, `evidence_run_id`, `reason`. Основание —
реальное исходное timeout/stall того же профиля без диагностического override.
Запрошенные секунды превышают обычный предел и не превышают настроенный максимум.
Основание используется один раз; цепочка диагностических оснований не создаётся.
Потребление сохраняется после committed run даже при потере вывода вызывающему.
Если владелец процесса доказал, что ребёнок не начат, неиспользованная reservation
освобождается. Потеря ответа после выполненного ребёнка не является основанием
повторить диагностический запуск.

## Повторное использование и свежий запуск

Обычный ключ имеет namespace выбранных inputs, command и workload; пакетный ключ
использует package/content и полный исполняемый closure зависимостей. Сами по себе
смена HEAD, абсолютного checkout, runtime/profile bytes или `cache.environment`
не добавляют новые оси content key. Admission, cache.enabled, prerequisites,
исходная identity записи и retained evidence проверяются отдельно.

Обычный filesystem input учитывает порядок declared patterns, missing patterns,
отсортированные path/kind/hash/size/mode. Tracked Markdown учитывает объявленный
выбор, пределы и scope. Пакет учитывает отсортированные относительные пути и content
hash исходников, тестов и fixtures всех выполняемых зависимостей, без filemode.
Root provenance текущего запроса отличается от provenance исходного reuse.

`fresh: true` выполняет проверку заново. Явно отключённый cache также выполняет её
заново; имя команды со словом `final` само по себе не является release gate.
Failed/unconfirmed/incomplete результаты непригодны для reuse. Повреждённый
необязательный payload или утраченный retained source даёт miss и реальный запуск.
Если выбранные inputs изменились во время ребёнка, известный исход и operational
history сохраняются, но store исключается с `declared_inputs_changed_during_run`.

Payload готовится до единственной окончательной записи execution result. Reuse
допускается лишь после фактического ledger result и проверки retained bytes;
ранний payload сам по себе не подтверждает завершение. Отказ optional store
сохраняет известный passed/actual exit/history, `cache.stored: false`,
`reason: cache_store_failed` и warning с путём, причиной ОС и восстановлением.
Исправьте configured cache destination либо явно отключите reuse; чтение уже
сохранённого результата не повторяет ребёнка.

## Пакеты, наборы и область изменений

Каталог пакетов TOML имеет `schema_version = 1`, `packages`, `inventory` и
`shared_tests`. Каждый пакет задаёт следующие данные:

| Поля | Назначение |
|---|---|
| `package_id`, `kind`, `description`, `contract` | Уникальный ID, `unit/component/integration/acceptance`, описание и контракт. |
| `owners`, `dependencies` | Непустые владельцы и явные зависимости. |
| `source_paths`, `fixture_paths` | Явные списки входов, включая пустые при их отсутствии. |
| `triggering_input_changes`, `triggering_output_changes` | Описание входных/выходных изменений. |
| `triggering_input_paths`, `triggering_output_paths` | Пути соответствующих отношений. |
| `shared_libraries`, `shared_databases`, `shared_library_paths`, `shared_database_paths` | Объявленные общие зависимости и их пути. |
| `bidirectional`, `producer`, `consumer` | Направленность; integration требует обе стороны. В остальных пакетах отсутствующие стороны нормализуются в null. |
| `steps` | Непустой упорядоченный список `driver`, `test_paths`, `arguments`. |

`inventory.candidate_globs` и `ignored_paths` задаются явно. `shared_tests` содержит
точный `path`, `packages` минимум из двух объявленных пакетов и `reason`.
Выполнение проверяет отсутствующие/cyclic dependencies, распределение тестов,
противоречия и необъявленные shared tests. Для каждого исполняемого пакета нужен
профиль `command_id = "test-package"`, `workload_class = package_id`; его runtime
выбирается однозначно. Драйвер и argv-prefix не угадываются из языка приложения.

`package_impact` читает только прямые объявленные отношения:

```json
{
  "schema": "poise-project-checks-request-1",
  "operation": "package_impact",
  "items": [
    {"changed_paths": ["src/example.py"]}
  ]
}
```

Пути конкретные, канонические, относительные и уникальные; удалённый файл тоже
может быть входом. Результат содержит direct packages в порядке каталога,
matches и unmapped paths. Здесь нет запуска, вывода транзитивных соседей или
замены отсутствующего совпадения полной suite. Inventory execution и impact
имеют разные обязанности.

JSON-каталог наборов имеет `schema_version: 1` и `suites` с `id`, непустыми
уникальными `profiles` из fast/release, `sources`, непустыми `commands` selectors
профиля/пакета. Прямой selector suite выбирает его ID. Selector `suite_set`:

```json
{
  "schema": "poise-project-checks-request-1",
  "operation": "run",
  "items": [
    {
      "selection": {"kind": "suite_set", "profile": "release", "changed_paths": ["tests/test_example.py"]},
      "options": {"fresh": true, "scope_paths": null, "timeout_override": null}
    }
  ]
}
```

`changed_paths: null` выбирает все members данного профиля; `[]` — ни одного.
Непустой список сопоставляется с literal source равенством/предком компонента
пути, а не поиском подстроки. Сохраняются порядок каталога, `source_matches`,
`unmatched_paths`, `reason`. Release membership не означает fast-only.
Нет совпадений — явный empty ready. `scope_paths` отдельно задаёт scope inputs,
а не фильтр memberships. Набор сохраняет исходные source reuse/fresh outcomes
и останавливается на реальном nonpass.

## Материалы и документационный предмет

Контракт evidence задаёт `evidence_id`, `required_globs`, `optional_globs`,
`max_files`, `max_total_bytes`, `retention_class = task|release|diagnostic`.
Ребёнок получает фактический `REPO_CHECK_EVIDENCE_DIR`. Сохраняются manifest и
file references с hash/size; отсутствие, превышение пределов и неполнота
отличаются от complete. Название retention class не создаёт Task и не разрешает
публикацию. В примере контракт `example-artifacts` объявлен, но не присоединён
к печатающей команде: `evidence_id` добавляется только к команде, реально
производящей заявленный `report.json`.

`subject` профиля задаёт смысловые настройки документационного предмета:
`kind = "documentation"`, `version = 1`, `mode = "working"|"committed"`,
`mode_argument`, `environment_key`. Оба режима требуют `cache.enabled = false`;
argv содержит ровно один выбранный mode argument и соответствующее значение.

Working-запрос опускает `documentation_scope`; `scope_paths` задаёт Markdown
либо null выбирает фактические изменённые/новые Markdown. Список передаётся
ребёнку через `REPO_CHECK_SCOPE_PATHS_JSON`. Committed-запрос содержит
`documentation_scope` ровно из семи полей:

- `version: 1`.
- `repository_root`: фактический абсолютный корень.
- `baseline_commit`: полный существующий commit ID.
- `candidate_commit`: полный фактический HEAD.
- `candidate_tree`: полный фактический tree ID.
- `paths`: выбранные Markdown-пути.
- `required_paths`: обязательные пути внутри выбранных.

У committed `scope_paths` равен null. Декларация передаётся в настроенный
`environment_key`. Harness проверяет вид Git objects, selected root/HEAD/tree,
чистоту кандидата, реальный delta, обе стороны Markdown rename, удаления и
required membership; после выполнения проверяет неизменность предмета.
Изменение сохраняет известный исход ребёнка и отмечает subject changed/unconfirmed.

Проверяющий документов принадлежит проекту: он должен поддерживать выбранный
mode argument и транспорт предмета. Внешние пути `bin/check-documents` в
примере — заполняемые предпосылки. Собственный
[link checker Poise](../workflows/documentation-checks.md#проверка-ссылок-и-точных-заголовков)
принимает `--checkout`/`--changed`; он не принимает показанный внешний `--mode`.
Схема или preview такого профиля не доказывают выполнения внешнего проверяющего.

## Ошибки и восстановление

Отказ содержит `code`, точный `field`, `cause`, `reason`, конкретное `recovery`
и ссылки только на реально доступные материалы. Прикладные состояния различаются:

| Ситуация | Действие по фактам |
|---|---|
| Нет/неоднозначен selector | Прочитать list/describe, явно выбрать оси/ID. |
| Не включён каталог | Настроить явный файл вместо null либо выбрать доступный вид проверки. |
| `prerequisites_unavailable`, `runtime_unavailable` | Подготовить перечисленные средства/платформу/права/cwd; preview не подменяет проверку. |
| `registry_invalid` | Сохранить исходный авторитетный реестр; восстановить его через владельца данных или явно выбрать реестр нужного проекта. |
| Optional payload miss | Получить новый фактический запуск. |
| `cache_store_failed` warning | Исправить cache destination; результат/история ребёнка уже сохранены отдельно. |
| `result_save_failed` | Сохранить доступные capture paths и выяснить исход авторитетной записи; это unconfirmed, а не optional cache miss. |
| `output_write` | Прочитать уже сохранённый response/result по фактическим references, исправить доставку; не повторять выполненную команду. |
| `calibration_conflict` | Прочитать текущие profile bytes и осмысленно повторить явный запрос с их SHA. |
| Частичный отчёт калибровки | Восстановить report destination, передать текущий SHA/исходные real evidence IDs. |

Повреждённые JSON/типы/ID в consumed run/result rows дают `registry_invalid`
с table/record/field context, без нового ребёнка и без переписывания повреждённых
данных. Это отличается от необязательного cache miss. Автоматического ремонта
авторитетной БД нет. Владелец результата различает сохранение, доставку и
последующее независимое принятие; ни одна из этих стадий не выводится из другой.

Если регрессионные тесты нужно независимо проверить до исправления продукта,
используйте [передачу незавершённых материалов
исправления](../workflows/local-handoff.md#незавершённые-материалы-исправления),
а не фиктивные resolutions для ещё открытых findings.

## Проверяемые примеры

После заполнения путей обычный `example-python` печатает `named-check example`.
Пакет `unit-example` требует настоящий `tests/test_example.py`, исполняемый
стандартным `python -m unittest`, и объявленные inputs/inventory выбранного проекта.
Например, минимальный тест в **отдельном демонстрационном checkout**:

```python
import unittest

class Example(unittest.TestCase):
    def test_sum(self):
        self.assertEqual(2 + 3, 5)
```

Это тест для проверки заполнения примера, а не доказательство поведения приложения
пользователя. Убедитесь, что `src/example.py` либо ваши объявленные source inputs
существуют; initial reason/timestamp и числовые пределы выберите явно.

Порядок демонстрации: list → describe → preview → run → чтение result по actual ID;
повторный обычный run может reuse, `fresh: true` создаёт новый ребёнок. Для
calibrate берутся actual run IDs и SHA неизменённых текущих профилей; для impact
и suite_set — конкретные пути данного checkout. Операции публикации калибровки
выполняются только на рабочих копиях примеров, когда это разрешено оператором.

Схемы четырёх шаблонов, ссылки между профилями/драйверами/пакетами/наборами и
согласованность subject modes проверяет существующий
`tests/named_checks/test_documentation_examples.py`. Проверка схем не исполняет
команды. Реальное выполнение требует заполненных путей, выбранной поставки,
подлинного caller и подтверждённых средств. Проверка Markdown-ссылок существующим
инструментом также не доказывает истинность эксплуатационных утверждений.
