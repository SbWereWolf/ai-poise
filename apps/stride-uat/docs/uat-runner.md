# Stride UAT: конфигурируемый исполнитель программы

## Реализованная граница

Stride UAT запускает команды проверок, принадлежащие выбранным приложениям, в порядке
существующей программы. Идентификаторы приложений задаёт конфигурация: ни префикс
`Stride`, ни перечень из четырёх или семи имён не ограничивает выбор. Например,
`widget-external` — обычное допустимое имя, а не специальный встроенный adapter.

Эта страница описывает реализованный в T029 **исполнитель**, а не полный механизм
приёмки. `COMPLETED` означает завершение всех команд с нулевыми кодами в пределах
наблюдаемых условий. Оно **не является PASS** продукта, графа или программы приёмки.
Ответ и журнал всегда содержат `qualification: "not_evaluated"`; поле `verdict`
исполнитель не формирует. Даже созданный командой `result.json` с `{"status":"PASS"}`
не меняет этого правила: исполнитель не интерпретирует этот файл.

[Полный контракт приёмки](contract-uat.md#1-ownership-and-applicability) описывает
целевой механизм, включающий квалификацию, изоляцию и resume в T030, а не уже
реализованные гарантии. [Описание shell](shell-uat.md#ownership-boundary) сохраняет
историю фундамента T023. Текущие `validate` и `execute` описаны именно здесь.

## Запуск и подготовка среды

Provider расположен в `apps/stride-uat/bin/stride-uat`. Его единственный обязательный
CLI-параметр — `--config`. Рабочий каталог вызывающего процесса не выбирает конфиг.
Можно явно выбрать установленный Python, не полагаясь на shebang `python3` и PATH:

```sh
"$UAT_PYTHON" apps/stride-uat/bin/stride-uat --config "$UAT_CONFIG" < "$UAT_REQUEST"
```

Здесь `UAT_PYTHON` — абсолютный путь к выбранному интерпретатору, `UAT_CONFIG` — путь
к заранее подготовленному конфигу, `UAT_REQUEST` — файл одного запроса. Это переменные
примера shell, а не обязательные переменные среды протокола Stride UAT. При прямом
запуске `apps/stride-uat/bin/stride-uat --config ...` интерпретатор выбирает shebang.

Используются стандартная библиотека Python 3.11+ и POSIX-механизмы process groups,
`killpg`, `O_NOFOLLOW`, `O_DIRECTORY` и `fsync` каталогов. Для выполнения нужен Linux
с доступными `ID` и `VERSION_ID` через `freedesktop_os_release`. Проверяются фактические
OS ID, OS version, major/minor интерпретатора и root-права, а не название профиля.
Сам профиль не устанавливает Python, зависимости или проверяемое приложение.

Поставляемый [пустой конфиг](../config/uat.json) задаёт физическую схему и лимиты, но
не содержит пригодной программы: карты профилей, команд, приложений, входов и
изоляций пусты. Для `execute` нужны явные выбранные bindings. [Тестовые
fixtures](../fixtures/runner/README.md)
содержат шаблонные `@...@` значения; тестовый владелец заменяет их реальными путями
и наблюдаемой платформой. Provider такую подстановку не выполняет.

## Запросы и ответы

На stdin передаётся один UTF-8 JSON object и EOF. Верхний уровень содержит ровно
`schema`, `operation`, `input`; `schema` равна `stride-uat/v1`. Успешный ответ и
обработанный отказ — один JSON object с завершающим LF на stdout. Duplicate keys,
нечисловые JSON-константы, некорректный UTF-8 и второй JSON object не принимаются.
Неизвестные поля в определённых объектах не игнорируются. CLI-помощь, ошибки
аргументов и необработанные ошибки среды не являются ответом этого wire-протокола.

### Validate без выполнения

```json
{
  "schema": "stride-uat/v1",
  "operation": "validate",
  "input": {
    "program_path": "/srv/uat/inputs/program.json",
    "profile_id": "local-checks"
  }
}
```

`input` содержит ровно `program_path` и `profile_id`. Проверяются структура
программы, выбранные bindings, поддержанная capability, пути, читаемость и лимиты
входных файлов. Ответ имеет ровно поля `schema`, `status: "validated"`, `program_id`,
`program_sha256`, `profile_id`, `step_count`, `graph_count: 0`; код завершения — 0.
SHA-256 относится к исходным байтам файла программы, не к её нормализованному JSON.

`validate` не создаёт state/workspace, не запускает команды и не проверяет совпадение
фактической платформы с профилем. Он не доказывает исполнимость binary, наличие всех
зависимостей, доступность записываемого хранилища или будущий успех тестов.

### Execute: один новый запуск

```json
{
  "schema": "stride-uat/v1",
  "operation": "execute",
  "input": {
    "run_id": "external-check-001",
    "program_path": "/srv/uat/inputs/program.json",
    "profile_id": "local-checks"
  }
}
```

`input` содержит ровно `run_id`, `program_path`, `profile_id`. `step_limit` и
`expected_revision` здесь не поддерживаются; это не сокращённый запрос `run`.
Исполнитель обрабатывает всю программу последовательно до первого отказа.

Ответ содержит ровно `schema`, `status: "executed"`, `run_id`, `execution_status`,
`qualification: "not_evaluated"`, `completed_steps`, `total_steps`, `protocol_path`,
`stop_reason`. `protocol_path` указывает на `state_root/run_id/execution.json`.
Код процесса — 0 для `COMPLETED`, 1 для `FAILED`. `completed_steps` считает записи
`EXITED`, включая команду с ненулевым exit code; это **не число пройденных тестов**.
Не запущенная команда в состоянии `ERROR` в этот счётчик не входит.

`run` и `resume` возвращают `capability_unavailable` и не запускают проверки:
квалификация и возобновление принадлежат T030. Совместимый `inspect` принимает только
`run_id`, проверяет наличие `state_root/run_id/protocol.json` и возвращает
`not_found` при отсутствии либо `store_incompatible` при наличии. Это пока не
читатель нового `execution.json` и не операция подтверждения целостности.

## Конфигурация и выбор владельцев

Физическая схема — `stride-uat-config/v1`. На верхнем уровне обязательны ровно
`schema`, `state_root`, `workspace_root`, `input_roots`, `limits`, `profiles`,
`commands`, `applications`, `inputs`, `isolations`.

Относительные корни, путь программы и пути выбранных входных файлов разрешаются
от каталога переданного конфигурационного файла. Для воспроизводимости используйте
явный абсолютный путь к нему без symlink. `state_root`, `workspace_root` и каждый
элемент непустого `input_roots` не должны совпадать, содержать друг друга или
пересекаться с выбранными `forbidden_roots`. Входные корни должны существовать.
Создание собственных state/workspace откладывается до `execute`.

Все ключи карт — корректные IDs. Глубокая проверка содержимого относится к выбранному
профилю, выбранной изоляции, перечисленным приложениям, использованным командам и
их объявленным входам; это не аудит всех неиспользуемых записей конфигурации.

| Карта | Точные поля выбранной записи |
| --- | --- |
| `profiles` | `os_ids`, `os_versions`, `python_versions`, `allow_root`, `isolation_id`, `environment` |
| `commands` | `application_id`, `argv`, `source_inputs` |
| `applications` | `public_argv`, `delivery_inputs` |
| `inputs` | `kind`, `path` |
| `isolations` | `kind`, `forbidden_roots` |

`os_ids` и `os_versions` — непустые списки строк; `python_versions` — непустой список
пар целых неотрицательных `[major, minor]`; `allow_root` — boolean. На `execute`
фактические значения должны принадлежать этим спискам. `allow_root: false` запрещает
root; `true` разрешает, но не требует его. Допуски ОС и версий проверяются отдельными
списками, не парами «ID + version».

`environment` — явная карта строковых значений без NUL; имена соответствуют
`[A-Za-z_][A-Za-z0-9_]*`. В `Popen` передаётся именно эта карта, без добавления
унаследованных PATH, HOME или секретов. Сам запущенный runtime может затем изменить
свою среду. В журнале сохраняются только имена переменных и SHA-256 канонического
JSON этой карты, не её значения.

`commands[command_id].application_id` должен совпадать с приложением шага.
Исполнитель запускает `commands[command_id].argv`, а `applications[application_id].public_argv`
передаёт adapter в контексте. Уже adapter вызывает публичный интерфейс приложения
и владеет бизнес-проверками; исполнитель не читает его приватную БД.

`source_inputs` и `delivery_inputs` — непустые списки уникальных IDs выбранных
входов. Поддерживается только `inputs[id].kind: "file"`: обычные файлы под
`input_roots`, без symlink в пути и без FIFO/device. Абсолютные пути вне этих корней,
недоступные файлы и неподдержанный `kind` отвергаются. Программа и каждый выбранный
файл читаются с ограничением `program_bytes` и хешируются. Наличие ссылки на input
не означает автоматического обнаружения всех зависимостей приложения.

### Пример структуры конфига

Это самостоятельный пример физических полей, не установленная среда. Пути
`/opt/uat/python/bin/python3`, `/srv/uat/inputs/owner.py` и `program.json` должны быть
подготовлены владельцем; профиль `debian/12/Python 3.13` не является скрытым default.
Для другой фактической платформы нужен другой явно выбранный профиль.

```json
{
  "schema": "stride-uat-config/v1",
  "state_root": "/srv/uat/state",
  "workspace_root": "/srv/uat/workspace",
  "input_roots": ["/srv/uat/inputs"],
  "limits": {
    "packet_bytes": 1048576,
    "program_bytes": 1048576,
    "steps": 32,
    "id_bytes": 128,
    "argv_items": 32,
    "argument_bytes": 8192,
    "result_bytes": 1048576,
    "stdout_bytes": 8192,
    "stderr_bytes": 8192,
    "step_timeout_seconds": 3,
    "lock_timeout_seconds": 1,
    "total_timeout_seconds": 10
  },
  "profiles": {
    "local-checks": {
      "os_ids": ["debian"],
      "os_versions": ["12"],
      "python_versions": [[3, 13]],
      "allow_root": false,
      "isolation_id": "trusted",
      "environment": {"LANG": "C.UTF-8", "CASE_FLAG": "declared"}
    }
  },
  "commands": {
    "adapter": {
      "application_id": "widget-external",
      "argv": ["/opt/uat/python/bin/python3", "-I", "/srv/uat/inputs/owner.py", "probe"],
      "source_inputs": ["owner-source"]
    }
  },
  "applications": {
    "widget-external": {
      "public_argv": ["/opt/uat/python/bin/python3", "-I", "/srv/uat/inputs/owner.py", "subject"],
      "delivery_inputs": ["owner-source"]
    }
  },
  "inputs": {
    "owner-source": {"kind": "file", "path": "/srv/uat/inputs/owner.py"}
  },
  "isolations": {
    "trusted": {"kind": "trusted-local", "forbidden_roots": ["/srv/production"]}
  }
}
```

## Программа, argv и контекст шага

Схема программы — `stride-uat-program/v1`; обязательны ровно `schema`, `program_id`,
`version`, `application_ids`, `graphs`, `steps`. Версия — непустая строка.
`application_ids` — непустой список уникальных IDs, `steps` — непустой упорядоченный
список шагов с уникальными IDs. Все ссылки должны разрешаться в выбранном конфиге.

```json
{
  "schema": "stride-uat-program/v1",
  "program_id": "external-program",
  "version": "1",
  "application_ids": ["widget-external"],
  "graphs": [],
  "steps": [
    {
      "id": "first",
      "application_id": "widget-external",
      "command_id": "adapter",
      "arguments": ["${context}"],
      "graph_id": null,
      "timeout_seconds": 2
    },
    {
      "id": "second",
      "application_id": "widget-external",
      "command_id": "adapter",
      "arguments": ["${context}"],
      "graph_id": null,
      "timeout_seconds": 2
    }
  ]
}
```

Поля каждого шага — ровно показанные шесть полей. `timeout_seconds` — положительное
целое, не boolean и не больше `step_timeout_seconds`. В этом исполнителе `graphs`
может быть только пустым списком, `graph_id` — только `null`; иначе получается
`capability_unavailable`, а не пропуск неподдержанного условия.

Фактический argv равен `command.argv + step.arguments`. Первый элемент каждого
настроенного `argv`/`public_argv` — непустой абсолютный lexical path; остальные
аргументы могут быть пустыми строками. Shell не запускается (`shell=False`), `$HOME`,
`*` и `;` не получают shell-смысла. Единственная подстановка — целый аргумент
`${context}` в `step.arguments`: он заменяется абсолютным путём `context.json`.
Встроенный в строку или иной `${...}` отвергается; в настроенном executable argv
подстановок нет. Symlink исполняемого файла может использоваться как lexical argv;
это не отменяет запрет symlink для объявленных входных файлов.

Шаг получает отдельный cwd `workspace_root/run_id/step_id`, stdin подключён к
`DEVNULL`. `context.json` имеет ровно поля:

```json
{
  "schema": "stride-uat-case-input/v1",
  "run_id": "external-check-001",
  "step_id": "first",
  "application_id": "widget-external",
  "public_argv": ["/opt/uat/python/bin/python3", "-I", "/srv/uat/inputs/owner.py", "subject"],
  "workspace": "/srv/uat/workspace/external-check-001/first",
  "result_path": "/srv/uat/workspace/external-check-001/first/result.json",
  "graph": null
}
```

`result_path` — место, куда adapter может записать свой результат. Его наличие,
отсутствие или содержимое не оценивается как приёмочный verdict. Порядок запуска
задаётся только списком `steps`; после ошибки следующая команда не начинается.

## Журнал, вывод и сохранность намерения

Каждый `run_id` эксклюзивно резервирует `state_root/run_id` и `workspace_root/run_id`.
Если любой путь уже существует, возвращается `run_conflict`; его файлы не заменяются.
Новый ID сам по себе не разрешает повторить неизвестные внешние эффекты.

`state_root/run_id/inputs.json` содержит `stride-uat-input-observations/v1` и список
`files` с `path`/`sha256`. Это наблюдения объявленных файлов, **не копии их содержимого**
и не полный snapshot конфигурации/релиза. Исходные bytes/config и выбранный runtime
нужны для воспроизведения отдельно. Перед каждым шагом и после команды без иной
ошибки перечитываются программа и выбранные файлы; изменение останавливает запуск
с `inputs_changed`. Это точечные проверки, не защита от злонамеренного TOCTOU.

`execution.json` имеет схему `stride-uat-execution-protocol/v1`, отличную от целевого
протокола квалификации. Его поля: `schema`, `run_id`, `program_id`, `program_version`,
`profile_id`, `program_sha256`, `qualification`, `execution_status`, `environment`,
`steps`, `remaining_step_ids`, `stop_reason`. Наблюдение `environment` содержит
`os_id`, `os_version`, `python_version`, `root`, `environment_keys`, `environment_sha256`.

Запись шага сохраняет `step_id`, `application_id`, `command_id`, `state`, фактические
`argv`, `cwd`, `environment_keys`, `environment_sha256`, `exit_code`, `signal`,
`timed_out`, `duration_seconds`, `effects`, `reason`, `stdout`, `stderr`.
`state` — `RUNNING`, `EXITED` или `ERROR`; отрицательный exit code означает сигнал,
его положительный номер записан в `signal`. `duration_seconds` измеряется
монотонными часами внутри сбора вывода, а не как длительность всей квалификации.

`stdout` и `stderr` — записи `{path, bytes, sha256}` для отдельных файлов
`stdout.bin` и `stderr.bin` в каталоге шага. Сырые bytes не встраиваются в JSON и
могут не быть UTF-8. При переполнении сохраняется только ограниченный префикс;
его размер и digest относятся к сохранённым, а не потерянным bytes. Команда при
этом не считается успешно завершённой.

Сначала сохраняется журнал `RUNNING`, затем перед конкретным запуском — запись
намерения этого шага. Для создаваемых каталогов roots/run/step запрашивается режим
0700 и выполняется `fsync` родительского каталога **до запуска команды**, включая
новые промежуточные каталоги. JSON записывается через уникальный файл режима 0600,
flush/fsync, атомарную замену и `fsync` каталога. Выходные файлы создаются в режиме
0600; после сбора их содержимое flush/fsync. Режимы уже существующих каталогов
исполнитель не ужесточает: их доступность и права остаются обязанностью оператора.

Ошибка синхронизации нового root/run не разрешает запуск. Ошибка подготовки
step-каталога/контекста оставляет выполненный префикс и не начатый суффикс, статус
`FAILED`, причину `io_failed`. Эти проверки подтверждают порядок операций и реакцию
на I/O-отказ, а не проведённый эксперимент с отключением питания и не атомарность
всей системы. Файловая система, процессы и облачные резервные копии не образуют
одну транзакцию.

## Лимиты и остановка

Все двенадцать полей `limits` обязательны, положительны и целочисленны; boolean
не заменяет integer. Значения timeout должны быть представимы для вычисления
длительности. Скрытых числовых defaults нет.

| Лимит | Что ограничено |
| --- | --- |
| `packet_bytes` | Входной JSON-пакет на stdin |
| `program_bytes` | Файл программы и каждый выбранный объявленный файл отдельно |
| `steps` | Число шагов, выбранных apps/input IDs и размеры проверяемых списков IDs |
| `id_bytes` | UTF-8 длина IDs с формой `[A-Za-z0-9][A-Za-z0-9._-]*` |
| `argv_items` | Списки argv/arguments и их итоговая конкатенация |
| `argument_bytes` | Каждый аргумент, включая раскрытый путь `${context}` |
| `result_bytes` | Контексты, input observations и сериализуемый журнал; объём резервируется до запуска |
| `stdout_bytes`, `stderr_bytes` | Отдельные сохранённые потоки каждой команды во время чтения |
| `step_timeout_seconds` | Верхняя граница timeout, заданного в шаге |
| `total_timeout_seconds` | Бюджет последовательного исполнения после начальной записи журнала |
| `lock_timeout_seconds` | Обязательное поле совместимого конфига; в T029 нет ожидания lock, повтор ID отклоняется сразу |

На команду передаётся минимум её timeout и остатка общего бюджета. Предел не
означает жёсткий wall-clock deadline для валидации, операций диска, завершения
процессов и записи последнего журнала. Отдельных ограничений размера конфигурации,
полного дискового расхода adapter или размера его произвольного `result.json`
этот исполнитель не вводит.

При timeout или переполнении потока исполнитель посылает SIGKILL своей process
group и ожидает прямого ребёнка. На обычном завершении он не превращается в
универсальный supervisor всех потомков; отделившиеся процессы могут выйти из
этой группы. Внешняя отмена/авария самого runner также не даёт подтверждения, что
все эффекты прекращены. При любых таких условиях автоматического rollback нет.

`effects: "none"` используется только там, где команда не была попытана
(например, заранее обнаружен отсутствующий executable). После запуска сохраняется
`possible`, при неопределённой попытке — `unknown`; даже exit 0 не означает
отсутствия побочных эффектов. Запись `RUNNING` после прерывания нельзя считать
доказательством отсутствия вызова.

## Отказы и восстановление

Обработанный отказ имеет вид `{schema, status: "rejected", error}`; в `error`
находятся ровно `code`, `message`, `retryable`, `details`. Код процесса — 2.
Причину отличайте от `stop_reason` уже начатого исполнения:

| Code | Причина отказа до выполнения выбранных команд |
| --- | --- |
| `invalid_json` | Невалидный пакет или JSON программы |
| `invalid_packet` | Неверный конфиг, input, профиль, bindings или пересекающиеся roots |
| `unsupported_schema` | Неподдержанная схема пакета или программы |
| `program_invalid` | Неверная структура/ссылки/порядковая спецификация программы |
| `input_invalid` | Недоступный, неподходящий или вышедший за разрешённые roots файл |
| `limit_exceeded` | Превышение проверяемого лимита или бюджета до запуска |
| `environment_mismatch` | Фактическая OS/Python/root-среда не соответствует профилю |
| `capability_unavailable` | `run`, `resume`, graphs, иной input kind или неподдержанная изоляция |
| `run_conflict` | Уже занят run или workspace ID, повтор не выполняется |
| `store_unavailable` | Не удалось подготовить собственный root/run |

`not_found` и `store_incompatible` относятся к описанному выше shell `inspect`.
Для перечисленных обычных отказов `retryable` равно false. Само поле не даёт
разрешения повторять команду; текущий исполнитель не ждёт store lock и не
производит `store_busy` как штатную ветку выполнения.

Причины остановки уже созданного журнала: `command_failed`, `signal`, `spawn_failed`,
`timeout`, `output_limit`, `inputs_changed`, `io_failed`. Предыдущие шаги и их
реальные эффекты сохраняются; не начатые шаги остаются в `remaining_step_ids`.
Не каждый системный отказ преобразуется в этот результат: ошибка записи самого
журнала, авария процесса или хранилища может оборвать ответ и оставить старое
намерение. В таком случае отсутствует подтверждённый terminal result.

После неопределённого исхода остановите writers и сохраните оба собственных roots,
исходную программу, объявленные входы, фактический конфиг, argv/runtime и исходный
stderr/exit code. Сверьте реальные процессы и эффекты приложения через его владельца.
Не удаляйте каталог запуска ради повтора, не переписывайте журнал и не принимайте
ручное редактирование `execution.json` за `resume`. Доставка копии и digest не
превращают сохранённое намерение в PASS. Встроенного ремонта, resume или
квалифицирующего readback до T030 нет.

## Trusted-local и приватные данные

`isolations[id].kind` поддерживает только `trusted-local`; `forbidden_roots` —
непустой список абсолютных путей. Сопоставление корней отсекает ошибочное размещение
собственных ресурсов, но **не является sandbox** и не запрещает произвольному
дочернему коду читать или изменять иные доступные процессу файлы. Выполнять можно
только доверенные локальные команды в отдельно подготовленном окружении. Имя
профиля, отдельный cwd и отсутствие унаследованной среды не доказывают OS-изоляцию.

Аргументы, контекст и сырые stdout/stderr могут содержать секреты приложения.
Скрытие значений environment в журнале не является редактированием его вывода или
криптографической защитой: digest низкоэнтропийного значения тоже не делает его
секретным. Не публикуйте эти файлы как безопасно обезличенный отчёт. Права 0700/0600
относятся к создаваемым runner объектам, не к любым файлам самого adapter.

Исполнитель не устанавливает приложение, не принимает Task, не меняет чужой graph,
не выдаёт release approval и не проверяет полноту не объявленной владельцем поставки.

## Узкие проверки

Из корня этой поставки с явно выбранным совместимым Python:

```sh
"$UAT_PYTHON" -B apps/stride-uat/tests/test_runner.py --suite behavior
"$UAT_PYTHON" -B apps/stride-uat/tests/test_shell.py --suite behavior
"$UAT_PYTHON" -B apps/stride-uat/tests/test_runner.py --suite documentation
```

Первый метод содержит 24 группы внешнего CLI-поведения, включая порядок/cwd/env,
конфигурации, границы потоков, timeout, drift, конфликты и отказ от ложной
квалификации. В группе conflict дополнительно проверяются порядок реальных I/O
операций и отказ `fsync` до первого и следующего шага. Второй метод сохраняет
8 проверок shell. Третий содержит 4 документационные группы: наличие command/config/
protocol/boundary описаний, русский текст и существование относительных targets.
Он не доказывает сам по себе истинность текста или исполнимость примеров.

Anchors и входящие ссылки проверяет существующий владелец документации отдельно:

```sh
"$UAT_PYTHON" tools/check_documentation.py --checkout "$UAT_CHECKOUT" \
  --changed apps/stride-uat/docs/uat-runner.md
```

Для этой команды выбранный интерпретатор должен иметь установленный Poise; shell
исполнителя от этой установки не зависит. На границах проверки/интеграции применяется
поддерживаемый `bash tests/smoke.sh "$UAT_PYTHON"` с подготовленными зависимостями
самого проекта: это шесть выбранных smoke-проверок, не полный regression suite.
Воспроизведение уже принятого RED, полный suite и публикация по сети не требуются.
