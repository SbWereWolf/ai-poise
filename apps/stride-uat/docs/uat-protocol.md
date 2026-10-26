# Квалифицирующий протокол Stride UAT

## Реализованная граница T030

Stride UAT выполняет явно выбранную программу приёмочных испытаний, сохраняет её
связь с исходниками, поставкой, конфигурацией и наблюдаемой средой, проверяет
доказательства перед публикацией результата и продолжает только незапущенный
суффикс программы. Provider находится в `apps/stride-uat/bin/stride-uat`.

Эта страница описывает реализованные `run`, `resume`, `inspect` и квалифицирующий
`validate`. [Контракт UAT](contract-uat.md) задаёт требования к этой границе.
[Исполнитель T029](uat-runner.md) остаётся описанием совместимого `execute`:
его `COMPLETED` и `qualification: "not_evaluated"` **не означают PASS**. Исторические
утверждения той страницы об отсутствии `run`/`resume` и читателя `inspect`
относятся к состоянию до T030; для этих операций действует настоящая страница.
История [shell T023](shell-uat.md) также не подменяет текущий протокол.

Бизнес-проверками владеет adapter выбранного приложения. UAT передаёт ему
публичный argv приложения и изолированный тестовый workspace, а не открывает
приватную БД приложения. Идентификаторы приложений конфигурируются, не ограничены
префиксом `Stride` или встроенным перечнем. Успех запуска процесса сам по себе
не квалифицирует приложение, программу или граф.

## Вызов и подготовка

```sh
"$UAT_PYTHON" apps/stride-uat/bin/stride-uat --config "$UAT_CONFIG" < run.json
```

`UAT_PYTHON` и `UAT_CONFIG` здесь — переменные примера shell: абсолютные пути к
подготовленному Python и конфигу. Provider не выбирает конфиг по текущему каталогу
и не устанавливает runtime, зависимости или приложение. При прямом исполнении
файла вместо явного Python действует shebang `python3` и PATH вызывающего.

[Поставляемый конфиг](../config/uat.example.json) содержит схему и лимиты, но пустые карты
не образуют готовое развёртывание. [Конфиг fixture](../fixtures/protocol/config.json)
и [программа fixture](../fixtures/protocol/program.json) — входы постоянных тестов,
а не production-конфигурация. Их `@...@` заменяет владелец теста; provider не
подставляет эти значения. Для принятого `linux-setpriv/v1` нужны Linux, root
supervisor, доступный `setpriv` и отдельные ненулевые UID/GID.

До запуска владелец развёртывания готовит только свои тестовые ресурсы. Входы и
поставка должны быть читаемы, но не изменяемы выделенной идентичностью; рабочие
БД и их родительские каталоги не должны быть ей доступны или заменяемы. UAT не
исправляет права чужих каталогов и не превращает рабочую БД в fixture.

## Точный wire-протокол

На stdin поступает один UTF-8 JSON object и EOF. Верхний уровень содержит ровно
`schema`, `operation`, `input`; `schema` равна `stride-uat/v1`. Обработанный ответ
— один JSON object с LF на stdout. Duplicate keys, NaN/Infinity, неправильный
UTF-8, второй JSON object и неизвестные поля не являются допустимым расширением.
Идентификаторы соответствуют ASCII `[A-Za-z0-9][A-Za-z0-9._-]*` и `id_bytes`;
boolean не принимается вместо целого числа. Произвольный stderr/traceback,
CLI-помощь и ошибка запуска не являются корректным JSON-результатом UAT.

### Run: новый запуск или точное чтение прежнего

```json
{
  "schema": "stride-uat/v1",
  "operation": "run",
  "input": {
    "run_id": "widget-acceptance-001",
    "program_path": "/srv/uat/inputs/program.json",
    "profile_id": "isolated",
    "step_limit": 1
  }
}
```

Это полный набор полей `input`. `step_limit` — целое неотрицательное число:
`0` разрешает весь оставшийся суффикс; положительное значение ограничивает число
новых шагов в данном вызове. После одного успешного шага из двух результат будет
`RUNNING`, а не PASS всей программы.

Повторный `run` с тем же `run_id` и тем же fingerprint **не продолжает программу**.
Он проверяет сохранённый журнал и возвращает его с `replayed: true`, не меняя
revision и не создавая новый attempt. Изменение одного `step_limit` не запускает
дополнительную работу и не меняет fingerprint. Для продолжения служит `resume`.
Другие выбранные входы с занятым `run_id` дают `run_conflict`.

### Resume: проверяемое продолжение

```json
{
  "schema": "stride-uat/v1",
  "operation": "resume",
  "input": {
    "run_id": "widget-acceptance-001",
    "program_path": "/srv/uat/inputs/program.json",
    "profile_id": "isolated",
    "step_limit": 0,
    "expected_revision": 3
  }
}
```

`expected_revision` — положительное целое из последнего фактического ответа;
`3` в примере не является универсальным стартовым значением. Сначала проверяется
ограниченный заголовок сохранённого журнала и revision, затем текущая выбранная
идентичность, затем полное evidence. Поэтому устаревшая revision вместе с
повреждённым stdout даёт `revision_conflict`, а не запускает очередной шаг.
Структурно нечитаемый заголовок остаётся повреждённым evidence.

Для штатного `RUNNING` после `step_limit` должны быть завершены и подтверждены
все шаги префикса. Они не исполняются повторно, их записи и файлы не переписываются.
Все будущие имена workspace и `.control/<step_id>` проверяются до изменения
lease/probe; занятый путь даёт `workspace_conflict`, без присвоения чужого содержимого.
Новые каталоги выделяются исключительно, не очищаются для повторного использования.

Незавершённый durable attempt в состоянии `RUNNING` даёт `unknown_attempt`.
Даже правдоподобный `result.json` после аварии не доказывает, завершился ли процесс
и какие внешние эффекты произошли. Автоматического повторения такого шага нет.
`FAIL` и `BLOCKED` не возобновляются (`run_not_resumable`). Исправленный вход или
окружение проверяется новым `run_id`. Совпадающий завершённый PASS возвращается
без повторного исполнения с `replayed: true`.

### Inspect: историческая проверка evidence

```json
{
  "schema": "stride-uat/v1",
  "operation": "inspect",
  "input": {
    "run_id": "widget-acceptance-001"
  }
}
```

Здесь нет профиля, программы или expected revision. `inspect` читает принадлежащий
этому хранилищу `protocol.json`, frozen-записи и raw evidence, проверяет их
согласованность и возвращает `replayed: false`. Selected source/config/environment
identity повторно не наблюдается: исторический PASS не является подтверждением
новой поставки. Отсутствие прежних live program/source или пустая текущая карта
profiles не препятствуют чтению целостного исторического протокола. Для
квалификации новой поставки нужен новый run либо разрешённый resume с проверкой
текущих входов. Журнал T029 `execution.json` не становится квалифицирующим протоколом.

Текущая управляющая конфигурация всё же нужна для определения корней хранилища и
лимитов чтения. Общий вход проверяет конечно представимые
`step_timeout_seconds`, `lock_timeout_seconds`, `total_timeout_seconds` также для
`inspect`: некорректный лимит отклоняется до lock и изменения состояния. Это
валидация управляющего ввода, а не новое наблюдение selected identity прошлого run.

### Validate

```json
{
  "schema": "stride-uat/v1",
  "operation": "validate",
  "input": {
    "program_path": "/srv/uat/inputs/program.json",
    "profile_id": "isolated"
  }
}
```

При выбранном квалифицирующем descriptor проверяются структура программы,
выбранные bindings, графовые ссылки и представимые лимиты. Возвращаются
`status: "validated"`, `program_id`, `program_sha256`, `profile_id`, `step_count`
и `graph_count`. `validate` не исполняет adapter, не приобретает изоляцию и не
сертифицирует будущие runtime-условия. Для старого `trusted-local` сохраняется
неквалифицирующая ветка валидации T029; её граница описана в `uat-runner.md`.

## Ответ, verdict и коды процесса

Ответ квалифицирующей операции содержит ровно:
`schema`, `status: "reported"`, `run_id`, `revision`, `verdict`, `protocol_path`,
`fingerprint`, `completed_steps`, `total_steps`, `graph_results`, `replayed`.
`protocol_path` — `state_root/run_id/protocol.json`.

| Verdict | Код процесса | Значение |
| --- | --- | --- |
| `PASS` | 0 | Все шаги программы завершены PASS и evidence проверено. |
| `FAIL` | 1 | Наблюдался отказ процесса, результата, графовых проверок или drift. |
| `BLOCKED` | 3 | Предусловия или сам испытательный case сообщили блокировку. |
| `RUNNING` | 3 | Остался незапущенный суффикс либо неизвестный durable attempt. |

`completed_steps` считает завершённые записи, включая FAIL/BLOCKED, а не число
успешных проверок. Поле `stop_reason` находится в журнале, не в кратком `reported`.
Обработанный отказ запроса имеет `status: "rejected"`, объект `error` с
`code`, `message`, `retryable`, `details` и код процесса 2. Ветвление выполняется
по `code` и структурным `details`, не по человекочитаемому `message`.

## Конфигурация и явные зависимости

Физическая схема `stride-uat-config/v1` содержит ровно `schema`, `state_root`,
`workspace_root`, `input_roots`, `limits`, `profiles`, `commands`, `applications`,
`inputs`, `isolations`. Относительные корни, пути входов и программы разрешаются
от каталога файла конфигурации. Для воспроизводимости путь самого конфига задаётся
абсолютно и без symlink. Корни собственных mutable-ресурсов не пересекаются с
input/forbidden roots или друг с другом.

Выбранный профиль содержит `os_ids`, `os_versions`, `python_versions`, `allow_root`,
`isolation_id`, `environment`. Допустимые ID и version ОС задаются отдельными
списками; Python задаётся парами `[major, minor]`. Root supervisor должен быть
разрешён профилем, но `allow_root: true` само по себе не предоставляет root-права.

Команда задаёт `application_id`, `argv`, `source_inputs`; приложение —
`public_argv`, `delivery_inputs`. IDs списков уникальны и явно указывают на inputs.
Абсолютные пути в выбранных argv должны быть покрыты объявленными зависимостями.
UAT не обнаруживает импорты, плагины, shared libraries, сетевые ответы или
доступы adapter автоматически: полноту source/delivery объявления обеспечивает
владелец. Неиспользованные записи карт не становятся частью выбранного fingerprint.

| Вид input | Точные поля | Что связывается |
| --- | --- | --- |
| `file` | `kind`, `path` | Выбранный файл и наблюдаемая файловая идентичность. |
| `tree` | `kind`, `path`, `excludes` | Состав source-дерева и записи каждого включённого объекта. |
| `delivery` | `kind`, `path`, `excludes` | Состав реально выбранной поставки, отдельно от source. |

`excludes` — явные нормализованные относительные пути поддеревьев; не glob,
не автоматический `.gitignore` и не список строк для substring-поиска. Пустой
список ничего не исключает. Исключённые файлы не покрываются доказательством,
а прямую argv-зависимость нельзя скрыть исключением. Наблюдаются байты, типы,
режимы, состав и разрешённые ссылки/их цели в пределах input roots. Смена режима
или установленной поставки значима даже при прежнем имени программы.

### Пример развёртывания

Ниже полная форма конфигурации для заранее подготовленного владельцем окружения.
`debian/13`, Python 3.13, UID/GID 62030 и все пути — конкретные значения примера,
не скрытые defaults и не команда изменения чужого хоста. Нужны существующие
исполняемые файлы, доступный source/delivery и закрытый production-каталог.

```json
{
  "schema": "stride-uat-config/v1",
  "state_root": "/srv/uat/test/state",
  "workspace_root": "/srv/uat/test/workspace",
  "input_roots": ["/srv/uat/inputs", "/opt/uat/python", "/usr/bin"],
  "limits": {
    "packet_bytes": 1048576,
    "program_bytes": 16777216,
    "steps": 32,
    "id_bytes": 128,
    "argv_items": 32,
    "argument_bytes": 8192,
    "result_bytes": 1048576,
    "stdout_bytes": 32768,
    "stderr_bytes": 32768,
    "step_timeout_seconds": 8,
    "lock_timeout_seconds": 1,
    "total_timeout_seconds": 30
  },
  "profiles": {
    "isolated": {
      "os_ids": ["debian"],
      "os_versions": ["13"],
      "python_versions": [[3, 13]],
      "allow_root": true,
      "isolation_id": "dedicated",
      "environment": {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    }
  },
  "commands": {
    "widget-cases": {
      "application_id": "widget",
      "argv": ["/opt/uat/python/bin/python3.13", "-I", "/srv/uat/inputs/source/adapter.py"],
      "source_inputs": ["adapter-source", "python", "launcher"]
    }
  },
  "applications": {
    "widget": {
      "public_argv": ["/opt/uat/python/bin/python3.13", "-I", "/srv/uat/inputs/delivery/widget.py"],
      "delivery_inputs": ["installed-widget", "python"]
    }
  },
  "inputs": {
    "adapter-source": {"kind": "tree", "path": "/srv/uat/inputs/source", "excludes": []},
    "installed-widget": {"kind": "delivery", "path": "/srv/uat/inputs/delivery", "excludes": []},
    "python": {"kind": "file", "path": "/opt/uat/python/bin/python3.13"},
    "launcher": {"kind": "file", "path": "/usr/bin/setpriv"}
  },
  "isolations": {
    "dedicated": {
      "kind": "linux-setpriv/v1",
      "launcher": "/usr/bin/setpriv",
      "uid": 62030,
      "gid": 62030,
      "test_roots": ["/srv/uat/test"],
      "forbidden_roots": ["/srv/widget/production"],
      "lease_root": "/srv/uat/test/leases"
    }
  }
}
```

Ширина `input_roots` разрешает чтение выбранных input-путей, но не означает, что
весь `/usr/bin` заморожен. В примере объявлен только файл launcher. Полнота
поставки интерпретатора и adapter-зависимостей требует дополнительных явных inputs,
если проверяемый результат зависит от них; приведённая форма не обещает их
автоматического обнаружения.

## Программа и интерфейс adapter

Программа имеет схему `stride-uat-program/v1`, `program_id`, строковую `version`,
`application_ids`, `graphs` и упорядоченный непустой `steps`. Шаг содержит ровно
`id`, `application_id`, `command_id`, `arguments`, `graph_id`, `timeout_seconds`.
Application ID шага и выбранной команды должны совпадать. `graph_id` — корректный
ID либо null; произвольный JSON вместо ID не допускается.

```json
{
  "schema": "stride-uat-program/v1",
  "program_id": "widget-acceptance",
  "version": "1",
  "application_ids": ["widget"],
  "graphs": [],
  "steps": [
    {"id": "first", "application_id": "widget", "command_id": "widget-cases", "arguments": ["${context}"], "graph_id": null, "timeout_seconds": 5},
    {"id": "second", "application_id": "widget", "command_id": "widget-cases", "arguments": ["${context}"], "graph_id": null, "timeout_seconds": 5}
  ]
}
```

Команда исполняется как список argv без shell. Только самостоятельный элемент
`${context}` заменяется путём JSON-контекста; произвольная интерполяция строк,
переменных среды и команд не выполняется. Контекст `stride-uat-case-input/v1`
имеет `run_id`, `step_id`, `application_id`, `public_argv`, `workspace`, `result_path`
и `graph`. Graph равен null либо содержит `id`, SHA256 и путь к frozen-байтам.
Рабочий каталог каждого шага отдельный; контекст находится вне writable workspace
в supervisor-owned `.control/<step_id>`.

Adapter вызывает публичный интерфейс приложения и записывает `result.json` по
выданному `result_path`. Результат содержит ровно `schema`, `run_id`, `step_id`,
`application_id`, `status`, `reason`, `graph`; IDs должны совпасть с контекстом.
Пример результата **после фактической успешной проверки**, а не готовое evidence:

```json
{
  "schema": "stride-uat-case-result/v1",
  "run_id": "widget-acceptance-001",
  "step_id": "first",
  "application_id": "widget",
  "status": "PASS",
  "reason": null,
  "graph": null
}
```

PASS шага требует одновременно exit 0, корректного результата PASS, совпадающих
IDs, допустимой graph-привязки, успешных graph checks и полного raw evidence.
Exit 0 с отсутствующим, неправильным или чужим результатом даёт FAIL.
`BLOCKED` case требует exit 3 и непустой строковой reason; прочие несовпадения
exit/result дают `case_failed`. Timeout, signal, spawn failure и output limit
имеют приоритет над написанным adapter результатом. Последовательность fail-fast:
следующие шаги после отказа не запускаются.

## Provenance и immutable input snapshot

`program_sha256` относится к исходным байтам программы, включая форматирование.
`config_sha256` относится к канонической **выбранной эффективной** конфигурации:
разрешённым корням, лимитам, профилю, выбранным командам/приложениям/inputs/изоляции.
Это не digest произвольного полного файла с неиспользованными bindings.

Наблюдение среды содержит OS ID/version, major/minor Python, UID supervisor и
`configured_environment_sha256`. `environment_sha256` — SHA256 канонического
наблюдения, не всех переменных хоста. Заданный environment передаётся adapter
явно, без наследования секретов родительского процесса.

`input_snapshot` содержит ровно `program_sha256`, `config_sha256`,
`environment_sha256`, `isolation_id`, `inputs`, `graphs`. Inputs упорядочены по ID
и связывают `input_id`, `kind`, `inventory_sha256`; graphs связывают `graph_id`,
`input_id`, `sha256`. `fingerprint` — SHA256 канонического JSON этого объекта:
UTF-8, сортировка ключей, компактные разделители, без NaN/Infinity. Поля не
заполняются выдуманными хэшами недоступных файлов: неполное наблюдение сохраняет
явную недоступность и не становится сертификатом.

В `state_root/run_id/frozen` сохраняются программа, выбранная конфигурация,
наблюдение среды, полные inventories, графовые записи и выбранные байты графов.
Читатель сверяет digest, состав и владельца/режимы этих записей, затем независимо
сопоставляет frozen-идентичность с журналом. В начале каждого нового шага
наблюдаются текущие selected inputs; изменение даёт `input_drift`, без запуска
следующего шага. После шага также проверяется drift. Успешный старый run не
переносит PASS на новый source, config, environment, programme или delivery.

Конфигурация может содержать чувствительный environment. Публичный ответ не
выдаёт его значения, но frozen configuration и внутренний `launch.json` содержат
необходимые для исполнения данные. State приватен supervisor; launch-spec
читаем выделенным UID. Доступ других локальных пользователей зависит также от
прав предков test roots: это не универсальное шифрование секретов. Архивы с
такой конфигурацией требуют соответствующего режима доступа.

## Проверяемая изоляция и её пределы

`linux-setpriv/v1` требует root supervisor и выделенные `uid`/`gid`, отличные от
его UID/GID. `launcher`, `lease_root`, `test_roots`, `forbidden_roots` задаются явно
абсолютными путями. Собственные state/workspace/lease располагаются внутри test
roots, не пересекаются между собой или с входами/защищёнными ресурсами. Launcher
также должен входить в замороженные объявленные inputs.

Перед запуском удерживается отдельная advisory lease в `lease_root` на выбранный
UID. Чужой lease или уже активный процесс с этой идентичностью блокирует запуск.
Через `setpriv` применяются `--reuid`, `--regid`, `--clear-groups`, обнуление
bounding/inheritable/ambient capabilities и `--no-new-privs`. Публичное
наблюдение проверяет реальные UID/EUID/GID/EGID, пустые supplementary groups,
нулевые kernel capability sets и `NoNewPrivs=1`, а не только имя профиля.

Проверяются фактические права чтения/записи/поиска и возможность замены объектов
через родительский каталог. Выбранные входы и retained state нельзя менять;
protected roots нельзя читать, изменять, обходить или заменять. Недоказуемое
предусловие даёт `BLOCKED/isolation_unavailable`, не fallback к trusted-local.
Неподдержанная платформа/root-policy, недоступные inputs или несовпадение среды
также не превращаются в PASS; причины различаются как `input_unavailable`,
`environment_mismatch`, `isolation_unavailable`.

Control/context принадлежат supervisor; завершённый workspace рекурсивно
закрывается от выделенного UID без следования symlink, затем проверяются
retained ownership и режимы. State/frozen/evidence имеют приватные режимы,
а права control обеспечивают чтение adapter без его права изменения. Выделенный
UID не получает доступ к fixture предыдущего завершённого шага для его подмены.

**Это граница DAC и ограничение привилегий для доверенного deployment, а не
универсальная песочница враждебного кода.** Здесь нет обещания network isolation,
отдельных mount/PID namespaces, seccomp или запрета всех неуказанных ресурсов
хоста. Администратор отвечает за безопасную политику всего выделенного UID,
полноту forbidden roots и declared inputs. Advisory lease не останавливает
злонамеренного внешнего администратора. SHA256 и modes не аутентифицируют данные
против root, переписавшего сразу журнал и все frozen/evidence записи. Для более
сильной threat model нужна внешняя граница, не скрытая имитация в UAT.

## Журнал, durable intent и авария

`stride-uat-protocol/v1` содержит `run_id`, `revision`, `program_id`,
`program_version`, `fingerprint`, `input_snapshot`, `profile_id`,
`environment_observation`, `verdict`, `steps`, `remaining_step_ids`,
`graph_results`, `stop_reason`, `diagnostics`. Revision увеличивается при
публикации следующего собственного состояния, не при простом чтении.

Steps образуют точный упорядоченный префикс программы. Запись содержит `step_id`,
`state`, уникальный `attempt_id`, фактический `argv`, `context_sha256`, exit/signal,
`timed_out`, `duration_seconds`, `effects`, `reason`, `outputs`, `graph`.
Перед созданием дочернего процесса синхронно публикуется durable RUNNING intent
с неизвестными эффектами. После реального завершения сохраняются raw stdout,
stderr, result и графовое evidence, затем публикуется проверенный outcome.

Каждый output — запись абсолютного пути, числа байт и SHA256; исходные байты
хранятся отдельно, не заменяются preview. До **любой** публикации нового verdict,
особенно PASS, проверяется весь кандидат и ранее сохранённый префикс. Повреждение
предыдущего результата во время следующего шага не оставляет ложный durable PASS:
последним остаётся RUNNING intent, а операция отвергается с `evidence_mismatch`.
Права, владельцы, формы, последовательность, hashes и итоговая конъюнкция
проверяются снова на inspect/resume, без автоматического ремонта evidence.

Run-level lock сериализует журнал. Он не делает внешние эффекты приложения
транзакционными. После аварии запрещено удалять RUNNING запись или угадывать
успех по result-файлу. Сохраняются состояние и доказательства, отдельно
разбираются внешние эффекты; новая попытка имеет новый run ID. Условный resume
разрешён только для однозначного завершённого префикса и незапущенного суффикса.

## Графы: привязка не заменяет бизнес-проверки

Каждый элемент `graphs` программы содержит ровно `id`, `input_id`,
`required_step_ids`. Input — выбранный `file`; bytes/hash графа фиксируются.
Список обязательных шагов и `graph_id` самих шагов взаимно согласованы, без
неизвестных, дублирующихся или неоднозначно приписанных шагов.

Graph-результат adapter содержит `id`, `sha256`, непустой `checks`. Каждый check
содержит уникальный `id`, `status` (`PASS`, `FAIL`, `BLOCKED`) и непустой список
`evidence_paths`. Пути нормализованные, относительные к workspace шага, без `..`
и абсолютных ссылок; файлы ограничены лимитами и сохраняются как raw hash/size
records. Несовпадающие ID/hash дают `graph_mismatch`; exit 0 и case PASS с
неуспешным graph check дают `graph_checks_failed`, а не частичный сертификат.

`graph_results` содержит `graph_id`, `graph_sha256`, `required_step_ids`,
`passed_step_ids`, `verdict`. Verdict зависит от всей программы: даже успешно
пройденное подмножество не квалифицирует граф при общем FAIL/BLOCKED/RUNNING.
`graphs: []` означает отсутствие квалификации графов. UAT проверяет привязку и
достоверность предоставленных доказательств. Реализованные сценарии создания,
шаблона и изменения через внешний CLI Execution описаны в
[руководстве графовой приёмки](graph-acceptance.md); смысловые наблюдения выполняет
принадлежащий приложению адаптер, а общий verdict вычисляет UAT.

## Лимиты и устойчивые отказы

Все конфигурационные лимиты — положительные целые, не boolean. Временные значения
должны быть конечно представимыми; неограниченный integer не превращается в
бесконечный timeout. Границы формы и размера проверяются до запуска команд там,
где размер известен заранее.

Проверка трёх конфигурационных временных лимитов является общей для `run`,
`resume`, `inspect` и квалифицирующего `validate`. Например, integer `10**400`
в одном из этих полей не участвует в арифметике ожидания lock: ответ отклонён с
exit 2, `program_invalid` и issue `invalid_binding` по точному пути
`/config/limits/<имя_лимита>`. Это не результат UAT; lock, журнал и workspace
таким запросом не изменяются.

| Лимит | Граница |
| --- | --- |
| `packet_bytes`, `program_bytes` | Входной пакет, программа и ограниченно читаемые selected input/graph bytes. |
| `steps`, `id_bytes`, `argv_items`, `argument_bytes` | Число/форма выбранных элементов и исполняемого argv после `${context}`. |
| `result_bytes` | Case result, внутренние JSON и retained evidence-записи; чрезмерный бюджет не допускает эффектов. |
| `stdout_bytes`, `stderr_bytes` | Раздельный ограниченный захват потоков процесса. |
| `step_timeout_seconds`, `timeout_seconds` | Верхняя граница шага и выбранное программой ограничение. |
| `total_timeout_seconds` | Общий бюджет исполнения шагов **одного вызова** run/resume, не wall-clock всего многодневного run. |
| `lock_timeout_seconds` | Ограниченное ожидание run-level lock. |

При `output_limit` сохраняются реально захваченные ограниченные байты, процесс
останавливается и результат не принимается как PASS. `timeout` и signal также
не компенсируются успешным JSON, который успел написать adapter. Точное завершение
внешних эффектов после потери supervisor не обещается.

| Код или stop reason | Действие и смысл |
| --- | --- |
| `invalid_json`, `invalid_packet`, `unsupported_schema` | Исправить форму запроса; результат тестирования отсутствует. |
| `program_invalid`, `limit_exceeded` | Исправить выбранные bindings/программу или явный бюджет; это не RED продукта. |
| `not_found`, `store_incompatible` | Неверный run ID либо чужая/несовместимая схема хранилища. |
| `store_busy` | Единственный `retryable: true`; повторяется тот же запрос после освобождения lock. |
| `run_conflict`, `input_mismatch` | Занятый ID связан с иной selected identity; прежний PASS не переиспользуется. |
| `revision_conflict` | Получить фактическую revision; не подавлять контроль версии. |
| `workspace_conflict` | Чужой или уже занятый ресурс; не удалять и не менять его права для обхода. |
| `evidence_mismatch` | Повреждение retained state/output/context/identity; остановить квалификацию, сохранить доказательства. |
| `unknown_attempt` | Неизвестные эффекты durable RUNNING; автоматическое повторение запрещено. |
| `run_not_resumable` | Завершённый FAIL/BLOCKED не возобновляется. |
| `input_drift` | Selected identity изменилась во время выполнения; дальнейшие шаги не запускаются. |

Прочие FAIL-причины включают `spawn_failed`, `invalid_case_result`,
`evidence_missing`, `case_failed`, `graph_mismatch`, `graph_checks_failed`.
BLOCKED до следующего запуска может сохранять уже завершённый PASS-префикс;
такой журнал обязан быть читаемым `inspect` и не переписывает прежние испытания.
Необработанная ошибка файловой системы может оставить неизвестный intent:
отсутствие корректного ответа нельзя интерпретировать как успешную приёмку.

## Проверка изменения и co-delivery

Постоянные тесты находятся в [test_protocol.py](../tests/test_protocol.py),
[совместимости execute](../tests/test_runner.py) и
[совместимости shell](../tests/test_shell.py). Группы protocol проверяют реальные
subprocess/UID/DAC, source/delivery/config/environment/program drift, точный replay,
revision, целостность frozen/context/raw evidence, crash intent, lock, limits и
graph binding. В существующие 32 группы также входят три отказа `inspect` при
непредставимых конфигурационных timeout и чтение исторического протокола после
удаления live program/source и очистки profiles. Независимая fixture должна
успешно собрать и проверить своё окружение: ошибка setup/decode — ERROR, не
требуемый RED.

```sh
"$UAT_PYTHON" -B apps/stride-uat/tests/test_protocol.py --suite behavior
"$UAT_PYTHON" -B apps/stride-uat/tests/test_runner.py --suite behavior
"$UAT_PYTHON" -B apps/stride-uat/tests/test_shell.py --suite behavior
"$UAT_PYTHON" -B apps/stride-uat/tests/test_protocol.py --suite documentation
```

Это точные ограниченные suites: соответственно 32, 24, 8 и 8 групп. Их перечисление
не является новым утверждением PASS. В управляемой задаче используются текущие
зарегистрированные методы, выбранный interpreter/cwd/env и полные captures,
включая `uat-protocol-observations.json` и `uat-runner-observations.json`.
Документационный suite вызывает существующий explicit-file link checker,
проверяет русскую страницу и точные поля примеров; конкурирующий сканер не нужен.
Maintained smoke запускается только в предусмотренных стадиях. Широкая suite и
повторное выполнение исторического RED для этой документации не требуются.

Код, fixtures, эта документация и проверенные evidence доставляются одной
задачей. Результат проверки, локальный commit, сохранённый checkpoint, принятие
Task и ff-only integration — разные события; `reported/PASS` приложения не
подменяет ни одно из них.
