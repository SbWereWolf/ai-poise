# Восстановление контекста работы

Обновлено: **2026-10-21**.

Общая механическая сводка и специализированное восстановление Task/SourceReader — разные
контракты. Первая работает и без Task; второе явно запрашивает сведения своих владельцев.
Ни короткая карточка, ни прежняя квитанция не доказывают память модели или прочтение текста.

## Общая механическая сводка

Необязательный `general_context` в hook settings включает наблюдение текущей работы после
сокращения контекста. Настройка, полные примеры и ограничения установки принадлежат
[hook owner](../configuration/runtime-hooks.md#общая-сводка-после-сокращения-контекста).
При отсутствии раздела прежние интерфейсы продолжают работать; прямой `content-read` сам
эту функцию не включает.

Подлинный `PreCompact` сохраняет сведения текущего проекта, checkout и native episode.
`PostCompact` и последующий `SessionStart` с `source=compact` используют один переход
поколения общего чтения. Если Post не доставлен, настоящий compact Start также обрабатывает
переход; отсутствие checkpoint при этом не становится доказательством capture. Нельзя
подменять эти факты явным `advance_context` с выдуманной причиной или отправкой synthetic Start.

В `hookSpecificOutput.additionalContext` приходит одна строка `Work context: ` с JSON схемы
`poise-work-context-1`. Полные тексты файлов и требований Task в карточку не копируются.

| Поле | Что действительно наблюдено |
| --- | --- |
| `status` | `ready` при доступных текущих сведениях; `partial` при неполноте. Ready не означает, что изменившуюся инструкцию уже прочитали. |
| `scope` | Проект, текущий checkout и собственный native episode. |
| `checkpoint` | `state` и адрес owned checkpoint: `missing`, `invalid`, `unavailable`, `pending`, `restored` или `used`. |
| `generation` | Сохранённое `before` либо `null`, фактическое `current` общего reader. |
| `git` | Текущий HEAD, SHA-256 porcelain-status, ограниченный список путей и `changed_since_capture`. Это не хеш содержимого каждого файла: изменение bytes уже untracked файла может не изменить porcelain. |
| `instructions` | Доступность применимых материалов и обнаруженные изменения их metadata/SHA. |
| `coverage` | Текущее поколение, ссылка и ограниченные сведения прежнего общего владельца чтения. |
| `work`, `check` | Необязательные сведения текущих владельцев, если они явно включены. |
| `omitted`, `actions` | Реальные количества опущенных элементов и конкретные продолжения работы. |

При изменении инструкции действие указывает текущий readable материал. Для навыка адрес
строится от настроенного instruction root, который может отличаться от checkout; checkout
rules используют checkout. Для отсутствующих или опущенных материалов остаётся refresh у
текущего владельца. Тексты инструкций получает
[ProjectInstructions/skills prepare](../configuration/development-routing.md#подготовка-инструкций-по-файлам),
в том числе из внешнего instruction root. Safe checkpoint metadata не являются полным
`previous` этого интерфейса. Общий reader файлов зарегистрированного checkout не получает
допуска к внешнему каталогу навыков только из наличия такой ссылки.

При изменении Git действие направляет к текущему HEAD/worktree. При изменении generation
нужно получить необходимые актуальные сведения через прежний общий reader; `coverage.ref`
остаётся ссылкой владельца, а не разрешением редактировать или читать его SQLite напрямую.
Для проектных текстов используйте
[явное чтение и coverage](../configuration/project-file-reading.md#сводка-покрытия).
Историческое покрытие и неизменность SHA не доказывают доставку в новый контекст.

`work_owner.enabled` и `check_owner.enabled` задаются явно. При обоих `false` Task owner не
запрашивается. Включённый work owner без выбранной Task возвращает `task=null`; check может
быть `not_observed`. Его `pending` относится к текущему descriptor запуска, а `completed`
подтверждает завершение процесса/захвата, не успешность тестов или приёмку результата.
Полный Task/route context запрашивается отдельно через его существующего владельца.

### Checkpoint и повтор доставки

Checkpoint схемы `poise-work-context-checkpoint-1` лежит внутри прежнего temporary episode
resource по `checkpoint_directory/<checkout_key>/checkpoint_filename`. Он содержит safe
Git/instruction/coverage/optional-owner facts и consumption; instruction bodies и копия Task DB
не сохраняются. Используется прежняя schema-2 registry, не второй ledger.

Первое продолжение собственного pending checkpoint даёт `restored`. Новый настоящий resume
после consumption даёт `used` и заново наблюдает текущие факты. Точный retry уже завершённой
доставки возвращает сохранённые bytes той доставки, а не свежий снимок после новых изменений.
Если capture или consumption успел сохраниться, а завершение delivery — нет, повторяют настоящую
сохранённую delivery: эффект не выполняется заново. Старый поздний Post не откатывает живое
поколение и не добавляет запоздалый второй переход.

Недоступность отдельного источника Git/instruction/coverage/work/check оставляет доступные
сведения и `partial` с причиной и действием. При обычном resume отсутствующий, повреждённый
или нечитаемый checkpoint не объявляется восстановленным
и не стирается ради успеха. При неподтверждаемой обязательной session/generation вместо
выдуманной карточки сохраняются native launcher, предупреждение и точная команда retry.
Неудавшееся сохранение consumption оставляет checkpoint pending и явное продолжение.

Карточка, launcher, capabilities, specialized-reader pointer и контекст внешнего consumer
делят один `SessionStart.context_limit` в UTF-8. Списки ограничены настройками и доступным
остатком; `omitted` сообщает опущенные элементы. Если обязательные identity/ref/actions не
помещаются, возвращается явный отказ с продолжением, а не обрезанный JSON. Значение
`summary.max_bytes` не резервирует весь native budget для одной карточки.

### Clear и окончание эпизода

Настоящий clear изменяет shared generation и удаляет только checkpoint своего checkout.
Чужой namespace, Git и данные Task не откатываются. SessionEnd фиксирует факт окончания и
освобождает live binding по прежнему контракту; само наличие End не означает удаления всего
reader resource. Checkpoint удаляется вместе с собственным временным ресурсом через
[reader recover после подтверждённого End](file-intake-lifecycle.md#завершение-и-восстановление).
Unknown, active и foreign resources сохраняются. Не подменяйте этот порядок ручным удалением
файлов, отзывом чужого claim или выдуманным SessionEnd.

### Два исполнимых примера

Ниже — полные объекты добавляемой возможности: две event definitions и весь
`general_context`. Это не Work envelope и не весь packet `runtime-setup`. Полные
[settings](../../config/examples/context-recovery-settings.example.json) и
[восемь event definitions](../../config/examples/context-recovery-hooks.example.json)
даются отдельно. Маркеры заменяют явными значениями, runtime их не раскрывает.
В этих component-примерах instruction root выбран равным checkout; это не обязательное
равенство для другой установки.

Для запуска и сокращения: после настоящего Start владелец получает PreCompact, PostCompact
и compact Start. Карточка показывает `before=g`, `current=g+1`, `restored`; следующий новый
resume показывает `used`. Значение `g` читается у владельца, не выбирается агентом.

<!-- f06-example: start-compact -->
```json
{
  "events": [
    {
      "event": "PreCompact",
      "matcher": "",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": null,
      "status_message": "Observe general context PreCompact"
    },
    {
      "event": "PostCompact",
      "matcher": "",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": null,
      "status_message": "Observe general context PostCompact"
    }
  ],
  "general_context": {
    "reader_policy": "@READER@",
    "checkpoint_directory": "context",
    "checkpoint_filename": "work-context.json",
    "summary": {
      "max_bytes": 4096,
      "max_changed_paths": 2,
      "max_instruction_changes": 2,
      "max_coverage_items": 2
    },
    "instructions": {
      "catalog": "@CATALOG@",
      "root": "@CHECKOUT@",
      "policy": "@INSTRUCTIONS@",
      "paths": [
        "src/billing/invoice.py",
        "docs/sample.txt"
      ]
    },
    "work_owner": {
      "enabled": false
    },
    "check_owner": {
      "enabled": false
    }
  }
}
```

Для resume и clear применяется тот же полный configuration component. После восстановления
настоящий clear даёт новое поколение и `checkpoint.state=missing`, собственный checkpoint
отсутствует. После настоящего End явный reader recover возвращает `cleaned` и удаляет свой
temporary resource.

<!-- f06-example: resume-clear -->
```json
{
  "events": [
    {
      "event": "PreCompact",
      "matcher": "",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": null,
      "status_message": "Observe general context PreCompact"
    },
    {
      "event": "PostCompact",
      "matcher": "",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": null,
      "status_message": "Observe general context PostCompact"
    }
  ],
  "general_context": {
    "reader_policy": "@READER@",
    "checkpoint_directory": "context",
    "checkpoint_filename": "work-context.json",
    "summary": {
      "max_bytes": 4096,
      "max_changed_paths": 2,
      "max_instruction_changes": 2,
      "max_coverage_items": 2
    },
    "instructions": {
      "catalog": "@CATALOG@",
      "root": "@CHECKOUT@",
      "policy": "@INSTRUCTIONS@",
      "paths": [
        "src/billing/invoice.py",
        "docs/sample.txt"
      ]
    },
    "work_owner": {
      "enabled": false
    },
    "check_owner": {
      "enabled": false
    }
  }
}
```

Принятые примеры исполняются изолированными native/reader consumers, включая End/recover.
Тестовая подготовка явно сеет generation 1 и наблюдает 1→2; это не боевой default: новый
пустой ресурс начинает с 0. Успех этих проверок и установленного пакета не доказывает
настоящую доставку событий конкретным Codex-хостом. Trust и host delivery проверяются отдельно.

## Возобновление после compaction и resume

Этот раздел относится к специализированному `ContextRecovery` и его `SourceReader`,
а не к общей механической карточке. `ContextRecovery` собирает текущее состояние через прежних владельцев Task,
`DevelopmentRouting` и `SourceReader`. Это не копия Task DB, новая стадия или claim.
Возвращаются ID/version/status/stage/iteration, требования результата, pending и
указание рабочей копии; полный текст документов не копируется в состояние сессии.
Без текущей задачи ответ явно taskless. Для newborn/terminal Task автоматическая
маршрутизация не применяется. Не настроенный router возвращает `not_configured`.

Маршрут **вычисляется заново** из текущих контрактов и файлов: сохранённый route snapshot
не считается доказательством актуальности. При конкурентной смене Task/version или
поколения reader ответ отвергается; lifecycle и данные задачи не переписываются.
Консервативный reset reader может уже сохраниться — повтор с тем же последним event ID
идемпотентен и не скрывает необходимость перечитать текст.

Пути к live tools/hooks/config разрешаются прежним способом. `cwd` — конкретный путь
для относительных аргументов файлового чтения; абсолютные пути не переопределяются.
При отсутствии worktree прежний workflow использует настроенный checkout.

## Подключение и явная операция

Необязательный раздел manifest `source_reader` подключается через существующий
`poise project` редактор, а не прямую запись managed configuration:

```json
{"policy":"/path/to/poise/config/development/source-reader.json","receipt_file":"reader.json"}
```

`policy` — явный путь, разрешаемый как остальные пути конфигурации. `receipt_file` —
относительный путь метаданных внутри существующей директории сессии; escape запрещён.
Без `source_reader` явная операция сообщает отказ; доставка native launcher и отдельно
подключённая общая карточка от этого не становятся настроенным SourceReader.

Через уже привязанный `work` launcher можно выполнить пакет:

```json
{"operation":"restore_context","input":{"reason":"resume","event_id":"resume-1","facts":[],"cwd":"/path/to/code","reads":[]},"messages":[]}
```

`reason` принимает `startup`, `resume`, `compact`, `clear`, `manual`. `event_id`
выбирается один раз для явной операции и сохраняется для повторения. `reads` содержит
только необходимые конкретные диапазоны по [контракту SourceReader](source-reader.md#явные-диапазоны-и-поколения-контекста).
После сброса acknowledgement прежнего поколения не переносится. `remembered_text=false`
означает отсутствие доказательства памяти; неизменность SHA-256 не заменяет перечитывание.
Пустой `reads` восстанавливает только метаданные, а не утверждает загрузку инструкций.

## Нативное событие и доказательство эффекта

Для специализированного `SourceReader` поддерживаемый `SessionStart`
с `source=compact/resume/clear/startup` сохраняет
source в прежнем HookRegistry. Его настоящий порядковый ID становится `hook-event:<id>`
для reader reset. Настроенный reader сбрасывается у своего владельца без произвольного
обхода и без загрузки текстов. В ограниченный native context попадают только поколение,
идентичность события и указание вызвать `restore_context` с тем же reason/event ID.
Следующий явный пакет получает Task/route metadata и только запрошенные тексты.

Доставки без нативного event ID не объявляются дедуплицированными: каждое записанное
SessionStart консервативно создаёт новое поколение специализированного reader.
Это отдельный счётчик от общего native compaction cycle. Это может вызвать лишнее чтение,
но не ложное сохранение памяти. Отсутствие события нельзя выводить из конфигурации
как успешную доставку; живое окружение пользователя проверяется отдельно.
