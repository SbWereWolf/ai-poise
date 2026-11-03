# DDD-07C — Настройка hook-транспорта и проверки доступности

## Явный вход результата Bash

Прямой `operation=hook` сохранён как явный вход [того же tool-result
owner](tool-results.md#контролируемый-hook-вход).
Действующая session policy также подключается к существующей общей sync цепочке.
Сам JSON explicit-вызова не создаёт native authority и не доказывает доставку.

## Вывод инструмента через общую цепочку

Установите полный [handler configuration](../../config/examples/tool-results-handler.example.json)
с builtin `tool-result` и абсолютным `options.policy_path` на заполненный
[session policy](../../config/examples/tool-results-session.example.json).
Policy/placement resources проверяются до установки; handler сохраняет свою
объявленную execution/on_error политику. Его подключение показано в
[фрагменте events/routes](#фрагмент-подключения-tool-result); остальные routes,
Start/admission, foreign groups и текущие handlers сохраняет существующий владелец установки.

Native descriptor matcher выбирает поддержанные имена, например Bash и точный
объявленный MCP. Это не выбор parser: внутри policy его делает
[явная selection](tool-results.md#выбор-профиля-для-native-события).
Command-intake сохраняет собственную Bash границу; общего Bash-only ограничения
для tool-result нет. Настройка не добавляет MCP server или второй dispatcher.

Для фактической строки `tool_response` Bash заполненный session-пример выбирает
`native-text`, для обычного Poise через Bash — `saved-work-text`. Код завершения и
раздельные stdout/stderr, которых нет в native callback, остаются недоступными;
вложенный программный ответ инструмента проверяется отдельно. Форматы требуют
явного выбора и поддерживающей их поставки; подробнее —
[строковый native-вход](tool-results.md#строковый-native-вход). Объектный транспорт
с `output`/integer `exit_code` выбирает прежние структурированные профили явно.

Handler сначала проверяет настоящую [service
association](tool-results.md#чтение-последнего-результата).
Иначе один selected profile определяет native source/snapshot/подготовку форм;
полный original event/машинный результат сохраняется. Prepared brief добавляется
в native additionalContext и `continue:false` заменяет только модельное представление.
Ошибки source/parser/budget идут через прежний typed rejection/stop контракт, без
повторного decoder, старого успешного ответа или нового запуска инструмента.

Для существующей установки используйте [публичный runtime-config owner](#интерфейсы)
с фактической полной revision; при необходимости разрешённого source/settings изменения —
[reconfigure](#общая-установка-для-рабочих-деревьев). Рабочие settings/definitions/policy
не коммитят. Полные object schemas, обязательные поля и immutable resource ownership
остаются у следующих разделов этой инструкции; пример handler не является полным
replacement install packet и не восполняет пропущенную definition.

Контракты специализированного thread-профиля, generic fallback, pending-снимков
и асинхронного реестра принадлежат
[tool results](tool-results.md#неизвестный-формат-и-асинхронная-регистрация).
[Переносимый компонент](tool-results.md#переносимый-компонент-и-независимый-потребитель)
может иметь отдельную development revision; это не установка hooks у потребителя.

Successful install/diagnose и isolated fixture не доказывают host delivery.
Нужны настоящий PostToolUse receipt/input, full source/UUID/files, полученный
моделью brief; для code mode — независимое сравнение полного original returned value
до model presentation и проверка saved services/last. До этих наблюдений установленная
доставка остаётся неподтверждённой. [Поддержка host-протокола](https://learn.chatgpt.com/docs/hooks)
не гарантирует наличие полного upstream source или покрытие каждого hosted инструмента.

## Доставка launcher при старте

В профиле с подключённым Task adapter выберите `task-context` для `SessionStart`:
его `options.context_template` содержит `{launcher}` и `{state}`. Тогда поддерживаемые
`startup`, `resume`, `clear`, `compact` и `fork` передают путь session-scoped launcher
в `hookSpecificOutput.additionalContext`. Переменная `AI_POISE_WORK` не создаётся.
Агент использует точный доставленный путь; launcher другой сессии не подтверждает identity.

При `task_adapter: null` общая сессия не создаёт Task launcher или Task runtime.
Общие команды получают явный профиль и сохраняют собственные интерфейсы.
Обработчики и их порядок выбираются в [конфигурации цепочки](#настраиваемая-цепочка-обработчиков).
Транспорт проверяет конфигурацию и native identity до эффектов, сохраняет binding и
настоящий Start; затем исполняет выбранные handlers. Ошибка конфигурации или handler
имеет собственный наблюдаемый исход. Полученный entry сам по себе не доказывает
готовность Task: обычный рабочий запрос проверяет полный договор и допуск.

Полные результаты и диагностика сохраняются существующим владельцем
ответов в `observations/<идентификатор>/<response_file>` из hook settings.
Native JSON и сохранённый ответ имеют корректный UTF-8; одиночные суррогаты
передаются JSON-экранированием с сохранением исходного значения. Если ответ содержит
короткое предупреждение, оно передаётся в `systemMessage`. При превышении бюджета
общее представление сохраняет сформированный контекст и выводит настроенный
`unknown_outcome_message` со ссылкой на полный файл. Полные данные для программных
потребителей не сокращаются. Бюджет должен вмещать контекст и адрес диагностики.

В поставляемом профиле matcher `SessionStart` пустой: Codex передаёт все причины
старта, а транспорт проверяет поддерживаемый native-протокол. Наличие файла
launcher или успешный синтетический вызов hook не доказывают его доставку модели.
Для такой проверки нужны настоящий запуск, полученный агентом контекст и запись
его native-события с той же session identity.

## Общая установка для рабочих деревьев

Для доставки из любого рабочего дерева выбранного репозитория native hooks
размещаются в пользовательском `hooks.json` активного Codex config home.
Точное расположение задаёт оператор. В settings явно задаются:

- `hooks_file` — абсолютный путь общего native-файла;
- `hooks_lock` — отдельный абсолютный путь его общего writer lock;
- `repository_scope.repository` — абсолютный путь основного Git checkout;
- `repository_scope.timeout_seconds` — положительный предел отдельного Git-наблюдения.

Полный [пример общей установки](../../config/shared-hook-settings.example.json)
содержит все обязательные поля. Маркеры заменяются явными рабочими значениями
до вызова владельца; шаблон не выполняет подстановку окружения.

При обработке native-события общий Git directory выбранного репозитория
сравнивается с directory, наблюдённым из native `cwd`. Так распознаются и рабочие
деревья вне основного checkout. События другого репозитория или каталога вне Git
возвращают пустой native-ответ до открытия Task runtime. Ошибка запуска Git или
истечение его предела возвращаются явно, без создания binding. Отдельный
`.codex/hooks.json` в каждом дочернем дереве для общей установки не требуется.
Эти локальные наблюдения исключают унаследованные переменные `GIT_*`: выбор
репозитория определяется настроенным checkout и native `cwd`, а не окружением
вызывающего процесса. Остальное окружение сохраняется.

Все профили, использующие один native-файл, задают один и тот же `hooks_lock`.
Обычные `install`, `reconcile`, перенос ресурсов и перенастройка используют этот
сохранённый lock. Настройка разных lock для одного общего файла не является
поддерживаемой конфигурацией.

Существующая установка переносится одним публичным пакетом
`poise runtime-config --settings <существующий-settings>`:

```json
{
  "operation": "reconfigure",
  "input": {
    "request_id": "share-project-session-entry-1",
    "expected_settings_digest": "<наблюдённая редакция исходных settings>",
    "expected_revision": "<наблюдённая полная revision исходной установки>",
    "target_revision": null,
    "settings": "<полный объект целевых hook-settings-1>",
    "definition": "<полный объект hook-definition-2 того же профиля>"
  }
}
```

Строковые места для `settings` и `definition` заменяются полными JSON-объектами.
`target_revision: null` означает отсутствие целевого файла; для существующего
файла передаётся его наблюдённая редакция. Полный объект settings сохраняет
неизвестные параметры и все остальные значения. Эта операция разрешает изменить
только `hooks_file`, `hooks_lock`, `repository_scope`, явно выбранный
`source_root`, необязательный `project_launcher` для
[явного выбора проекта](#явный-выбор-проекта-через-session-launcher)
и исторические `capability_summary`/`lifecycle_consumer`. Активные возможности выбираются
через routes и handler options; изменение старого поля settings не подключает handler.
Рабочая установка сохраняет настроенный installation source; кандидатный код
проверяется в явно отделённой тестовой среде.

До native-мутаций владелец проверяет definition2 и весь выбранный план, включая
полные файлы конфигураций и политики builtin. Summary проверяется по выбранному
`capability-summary.options.summary`; отсутствие такого handler не включает сводку
неявно. Согласованные bindings и прежние foreign groups сохраняются владельцем.

Обновление на прежнем пути native hooks поддерживает локальную установку без
`repository_scope` и `hooks_lock`. Перенос в другой native-файл требует этих явных
настроек общей установки. После локального обновления probes используют настроенный
Git-репозиторий проекта, после общей установки — её `repository_scope.repository`.

При согласовании сохраняются ID существующих сессий, сообщения и история native
событий. Ранее выданный launcher с проверкой source root и импортом только из этого
каталога обновляется текущим владельцем при native resume или публичном `reconfigure`.
Идентичность сессии, путь launcher, последнее сообщение и прежняя история сохраняются.
Проверьте фактический результат через `diagnose`: установка и привязка
должны соответствовать выбранной конфигурации. Сохранённое настоящее событие остаётся
историей; новый `SessionStart` возникает при настоящем native resume, а операция
настройки сама его не создаёт. Обновление launcher не является приёмкой или интеграцией Task.

`settings_digest` — служебная редакция, а не идентичность сессии. Его отличие в
файле или записи реестра, как и изменение необязательных параметров settings,
не запрещает чтение и штатный resume. При совпадении обязательных данных владельцем
остаётся существующая запись реестра; чтение не переписывает файлы и реестр.
Должны совпадать сама сессия, внешний caller, проект, agent, settings/definition и
пути принадлежащих привязке файлов. Реальное расхождение этих данных требует выбора
соответствующей конфигурации или восстановления конкретной привязки.

`diagnose` показывает наблюдённое событие и коррелированный эффект независимо от
текущего формата и исполнимости launcher. Это не утверждение готовности к выполнению
работы: раздел `runtime` описывает только текущий процесс диагностики. Рабочий запрос
проверяет настроенную среду отдельно. Native resume может воссоздать отсутствующий
собственный launcher и исправить его права; чужое содержимое не исполняется и не
перезаписывается этим восстановлением. Редакции полного исходного файла в пакете
`reconfigure` защищают от потери параллельной правки при публикации, а не удостоверяют
личность сессии.

Владелец сначала сохраняет intent `prepared` в существующем Hook Registry.
Затем публикует целевые группы, удаляет прежние собственные группы из исходного
файла и обновляет settings и файлы действующих binding. Чужие группы в обоих файлах
сохраняются. В действующих binding меняются только редакция settings и путь
актуального definition; native identity, launcher path, messages и прежние
события остаются прежними. Task lifecycle, claims и история не переписываются.
Не зарегистрированная владельцем группа того же профиля требует явной сверки.

После частичной записи повторяется тот же пакет с тем же `request_id`.
Сохранённый intent допускает только исходные и уже опубликованные собственные
значения; более новое стороннее изменение возвращает явный отказ. После
завершения временные снимки файлов убираются из pending intent, остаются
квитанция и операционный audit. Повтор завершённого запроса возвращает его
квитанцию. Для машинной проверки CLI читается полный файл `response_path`,
а не сокращённый preview.

Если между попытками пришли новые native-сессии через исходный или уже
опубликованный целевой entry, повтор того же запроса проверяет их через
существующего владельца binding. Сверяются identity, связь SQL/файла, текущие
settings, definition данного переноса и собственные байты launcher. Проверенные
новые действующие binding добавляются в прежний intent до первой записи их файлов; сообщения,
события и пути сохраняются. Изменённые прежние binding, чужое содержимое и
несогласованные файлы по-прежнему вызывают отказ. Удалять новые сессии или
создавать другой запрос для обхода незавершённого intent не требуется.

Новая общая установка требует штатного review/trust в Codex. Квитанция
`requires_user_review` с `live_codex: not_observed` описывает публикацию
конфигурации. Реальная доставка подтверждается отдельно: настоящий startup и
fork из рабочего дерева, полученный каждым агентом собственный launcher,
коррелирующие native binding/event и сохранённый контекст запуска.

Начало: **2026-09-07T01:17:28+05:00**. Linux, один явно выбранный проект.
Описание сводки готовности обновлено 2026-10-16.

## Независимые события при подготовке настройки

Обновлено: **2026-10-19**.

Во время длительной подготовки `install`, `reconcile` и `reconfigure` независимая
сессия может завершить `UserPromptSubmit`, `Stop`, `SessionEnd` или новый
`SessionStart`. Реестр сохраняет события, сообщения и новые привязки до окончания
этой подготовки. Подготовка кандидата установки и сверка групп выполняются вне
пишущей транзакции реестра hooks. Короткие транзакции получают снимок затронутой
конфигурации, повторно проверяют её и сохраняют intent. При этом изменение
независимых событий или последнего сообщения не считается изменением установки.

Перед сохранением нового intent `install` и `reconcile` сверяют собственные
записи установки, предшествующую установку при согласовании и фактическую
редакцию native-файла. Публикация, завершившаяся за время подготовки другого
запроса, сохраняется; устаревший запрос получает отказ до записи своего intent
и файлов. Повтор уже сохранённого запроса использует прежний кандидат и проверяет
совпадение намерения, а не строит новый результат под прежним `request_id`.

Общий `hooks_lock` сохраняет ответственность за запись общего native-файла.
Фактическая публикация файлов и завершение записи реестра сериализованы;
независимость длительной подготовки не обещает одновременную публикацию одного
файла. Пишущие границы используют настроенные пределы ожидания. Файловая система
и SQLite не образуют одной транзакции: частичная публикация продолжается через
сохранённый pending intent с проверкой фактических значений before/after.

Для `reconfigure` новые действующие привязки между попытками проверяются и
добавляются в тот же intent. Завершённая привязка исключается из pending-публикации
только после штатного `SessionEnd` и завершённой очистки её владельцем. Проверяются
последнее сохранённое событие, точный снимок привязки и отсутствие её уже удалённых
binding/launcher-файлов; эти сведения повторно сверяются перед записью intent.
Публикация учитывает только оставшиеся действующие привязки и сохраняет сообщения
и историю. Она не воссоздаёт файлы завершённой сессии и не выполняет её очистку
вместо владельца. Простое отсутствие, незавершённая очистка, новый `SessionStart`
или чужое содержимое файлов не подтверждают освобождение прежнего эпизода.

Продолжение зависит от того, что уже сохранено:

| Наблюдённое состояние | Продолжение |
|---|---|
| До intent изменились ожидаемые редакции или собственная запись установки | Получить актуальное состояние и отправить новое намерение с новым `request_id` и соответствующими ожидаемыми входами. |
| До intent `reconfigure` изменился только состав сессий, а редакции settings и native-файлов прежние | Повторить исходный пакет без изменения `request_id`; владелец подготовит его по текущему составу привязок. |
| Сохранён pending intent, включая частично записанные файлы | Повторить тот же пакет с тем же `request_id`; владелец проверит before/after, учтёт новые и штатно освобождённые привязки и завершит недостающие действия. |
| Обнаружены более новые сторонние байты или неподтверждённая принадлежность | Сохранить текущее содержимое, разрешить конкретный конфликт через его владельца и продолжить допустимую попытку; новый ID не заменяет восстановление pending intent. |

Операция настройки сама не создаёт native-событий. Факт сохранения события,
привязки и согласованного повтора проверяется
[сценариями независимых операций](../../tests/hook_transport/test_independent_operations.py).
Trust и реальная доставка в Codex остаются отдельными условиями, описанными
в [общей установке](#общая-установка-для-рабочих-деревьев).

## Что поставлено
Пакетная установка выбранной конфигурации Codex command hooks; session-scoped launcher к
существующему WorkTools; непосредственное наблюдение UserPromptSubmit; проверки реальных команд и
MCP stdio. Настройки hooks создаёт инструмент. Он не изменяет trust/permissions и не устанавливает
Codex/IDE.

При выбранном для SessionStart builtin `capability-summary` агент получает краткую сводку
настроенных возможностей.
Полная последняя сводка сохраняется в данных сессии; её можно прочитать без новых
проверок либо явно обновить. Порядок описан в [сводке готовности](#сводка-готовности-сессии).

Поддерживаются SessionStart, UserPromptSubmit, Stop, SessionEnd и явно подключённые
PreCompact/PostCompact основного агента;
при явном включении учёта чтения — парные PreToolUse/PostToolUse для Bash.
SubagentStart/SubagentStop и скрытая телеметрия ChatGPT не поддерживаются этим адаптером. Configured
`agent_id` выбирает профиль hook, но не создаёт отдельного исполнителя. Файлы launcher учитывают
проект, источник, внешнюю сессию и профиль; Task caller определяется реальной native session через
общего SessionEstablisher. Переименование профиля не отделяет делегата от отправителя.

## Настраиваемая цепочка обработчиков

Активная установка использует `hook-definition-2`, полный общий профиль
[команд и сессии](project-setup.md#общий-профиль-команд-и-сессии) и явные конфигурации
`hook-handler-configuration-1`. Исторический `hook-definition-1` читается владельцем
старых квитанций и диагностики; новые install/reconcile/reconfigure и активная доставка
требуют definition2. Прежние шаблоны с top-level `probes`, `gate_operations`,
`context_template`, `tool_intake` или `general_context` сами по себе новую цепочку
не подключают. Рабочие значения и файлы остаются вне source Git.

Необязательный command/sync handler может добавлять состояние индекса выбранной копии;
его [настройка и связь с Git callback](source-index.md#git-callback-и-цепочка-hooks)
описаны отдельно. Он использует существующую цепочку и сохраняет её порядок.

### Определение и порядок конфигураций

У definition обязательны `schema`, `id`, `agent_id`, `message_source`, `events`,
`routes`, `transport`. `routes` имеет ровно ключи настроенных событий; каждое значение —
явный список абсолютных путей конфигураций. Порядок — сначала позиции этого списка,
затем handlers каждой конфигурации. Сортировка по имени и удаление повторных command-позиций
не выполняются. Один effectful builtin name нельзя повторить в маршруте события:
preflight отвергает такой дубль до native-коммита и эффектов.

Ниже полный общий пример definition. До установки создайте оба указанных файла
конфигурации по следующим примерам, заполните реальный общий профиль и зарегистрированную
связь проекта/установки. Замените пути, ID и числовые настройки осознанно: это значения
примера, а не defaults. Источник `batch.message_source` профиля должен точно совпадать
с `message_source` definition при режиме `runtime_event`. `SessionStart` обязателен;
`SessionEnd` имеет timeout от 1 до 3 секунд. Пустой маршрут явно выбирает отсутствие handlers.

```json
{
  "schema": "hook-definition-2",
  "id": "demo-hooks",
  "agent_id": "primary",
  "message_source": "codex-hook-main",
  "events": [
    {
      "event": "SessionStart",
      "matcher": "",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": 8192,
      "status_message": "Запустить выбранные обработчики"
    },
    {
      "event": "SessionEnd",
      "matcher": "",
      "timeout_seconds": 3,
      "async": false,
      "context_limit": null,
      "status_message": "Завершить native-сессию"
    }
  ],
  "routes": {
    "SessionStart": [
      "/srv/poise/config/capabilities.json",
      "/srv/poise/config/commands.json"
    ],
    "SessionEnd": []
  },
  "transport": {
    "unknown_outcome_message": "Прочитайте сохранённый полный ответ по указанному пути."
  }
}
```

### Фрагмент подключения tool-result

Ниже **MERGE-фрагмент**, а не самостоятельная definition или пакет установки.
Сохраните полный текущий `hook-definition-2`: его schema/id/agent_id/message_source/
transport, обязательный SessionStart и все остальные events/routes. Сначала заполните
[полный handler](../../config/examples/tool-results-handler.example.json), замените
`@TOOL_RESULTS_POLICY@` абсолютным путём session policy и сохраните его по абсолютному
пути `/srv/poise/config/tool-results-handler.json` либо осознанно выбранному своему пути.
Тот же путь укажите в routes ниже.

```json
{
  "events": [
    {
      "event": "PreToolUse",
      "matcher": "Bash|mcp__example__records|mcp__example__projects",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": 8192,
      "status_message": "Проверить native-вход инструмента"
    },
    {
      "event": "PostToolUse",
      "matcher": "Bash|mcp__example__records|mcp__example__projects",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": 8192,
      "status_message": "Подготовить краткий вывод инструмента"
    }
  ],
  "routes": {
    "PreToolUse": [],
    "PostToolUse": [
      "/srv/poise/config/tool-results-handler.json"
    ]
  }
}
```

Владелец требует **пару PreToolUse/PostToolUse**, даже если tool-result работает только
на PostToolUse. При отсутствии пары добавьте оба descriptor и оба route. Пустой
PreToolUse route явно означает отсутствие handlers на этом событии; он не запускает
tool-result заранее. Если события уже есть, не создавайте повторные descriptor и
не заменяйте их routes пустыми списками: сохраните прежние handlers и их порядок,
добавьте путь tool-result к существующему PostToolUse route ровно один раз. Остальные
маршруты остаются прежними. Matcher существующих событий согласуйте с их обработчиками;
command-intake сохраняет собственную Bash границу.

`mcp__example__records` и `mcp__example__projects` — имена из примера policy, не
установленные endpoints. Второе показывает явный [профиль MCP content
JSON](tool-results.md#профиль-mcp-content-json).
Перед публикацией согласованно замените имена настоящими объявленными именами
в matcher, parsers и selection. Числа30/8192 — явные значения примера, не defaults; timeout
должен учитывать execution/cleanup подключённых handlers, context_limit — их вывод.
Передайте **полную объединённую definition** существующему install/reconfigure owner
с фактической revision по [публичному интерфейсу](#интерфейсы). Он выполняет preflight
всей конфигурации и ресурсов до публикации; наличие фрагмента само по себе ничего
не устанавливает и не подтверждает доставку хука на действующем хосте.

### Конфигурация и встроенные возможности

`schema`, `id`, `handlers` обязательны. В одной конфигурации handler ID уникальны.
Каждый handler имеет `id`, `implementation`, `mode`, `options`, `execution`, `on_error`.
`implementation.kind` — `command` с полным непустым `argv` или `builtin` с именем из
таблицы. `mode` — `sync` либо `async`; встроенные handlers поддерживают только `sync`.
`execution.run_seconds` и `cleanup_seconds` — положительные конечные секунды;
для async дополнительно обязателен `launch_seconds`. Текущий `on_error` равен `stop`.

| Builtin | События | Обязательные options |
|---|---|---|
| `capability-summary` | SessionStart | `probes` и `summary` с `file`, `header_template`, `item_template`. |
| `general-context` | SessionStart, PreCompact, PostCompact, SessionEnd | Абсолютный `policy_path` к полной политике общей сводки; End сам не восстанавливает ресурс reader. |
| `command-intake` | PreToolUse, PostToolUse | Абсолютный `policy_path` к полному reader profile, содержащему `command_intake.policy`. |
| `check-invocation` | PreToolUse, PostToolUse | Абсолютные `policy_path` транспорта и `checks_policy_path`, точные `tool_selectors.run`/`cancel`, непустой `cli_argv`. [Подключение](#подключение-инструмента-проверок). |
| `tool-result` | PostToolUse | Абсолютный `options.policy_path` к полной [session policy](tool-results.md#сессия-и-два-сохранённых-представления). |
| `lifecycle-consumer` | SessionStart, SessionEnd | Полный `consumer` по [договору потребителя](#настроенный-потребитель-lifecycle). |
| `task-context` | SessionStart, UserPromptSubmit, Stop | `context_template` с полями ровно `{launcher}`, `{state}`; `stop_template` с `{state}`; явный `gate_operations` из bootstrap/verify/artifacts. Нужен Task adapter. |
| `task-runtime-cleanup` | SessionEnd | Явный объект `options`; используется существующий Task cleanup owner. Нужен Task adapter. |

Подтверждённый отказ [чтения сохранённого результата](tool-results.md#чтение-последнего-результата)
доставляется как служебный ответ без нового capture и замены last. Произвольный JSON
со статусом `rejected` остаётся обычным результатом инструмента.

Полный файл `/srv/poise/config/capabilities.json` для примера выше:

```json
{
  "schema": "hook-handler-configuration-1",
  "id": "common-capabilities",
  "handlers": [
    {
      "id": "readiness",
      "implementation": {
        "kind": "builtin",
        "name": "capability-summary"
      },
      "mode": "sync",
      "options": {
        "probes": [],
        "summary": {
          "file": "capabilities.json",
          "header_template": "Готовность: {ready}; {checked_at}; {workspace}; {snapshot_path}",
          "item_template": "{id}: {status}; required={required}; reason={reason}; remediation={remediation}"
        }
      },
      "execution": {
        "run_seconds": 30,
        "cleanup_seconds": 1
      },
      "on_error": "stop"
    }
  ]
}
```

Пустой `probes` в этом примере не проверяет доступность средств. Заполните его реальными
[command/MCP probes](#проверки-capabilities) выбранной установки. `summary.file` —
относительный путь внутри каталога binding, отличный от binding/launcher.
Поля `summary.header_template` — ровно `ready`, `checked_at`, `workspace`, `snapshot_path`;
поля `summary.item_template` — ровно `id`, `status`, `required`, `reason`, `remediation`.
Таблицы ниже описывают содержимое этих options, а не неявное подключение через settings.

### Подключение инструмента проверок

`check-invocation` связывает настоящий native-вызов с
[MCP/CLI проверками](named-project-checks.md#mcp-stdio-настройка-и-вызов).
Он использует существующую упорядоченную цепочку handlers и тот же runner;
Task adapter для этого builtin не нужен. Встроенный handler синхронный.
Полный обезличенный
[шаблон handler configuration](../../config/examples/project-checks-hook-handler.example.json)
не является полной installation definition.

Заполните рабочую копию шаблона вне Git:

| Option | Назначение |
|---|---|
| `policy_path` | Абсолютный путь заполненной transport policy `poise-checks-mcp-policy-1`. |
| `checks_policy_path` | Абсолютный путь обычной policy именованных проверок выбранного проекта. |
| `tool_selectors.run`, `tool_selectors.cancel` | Точные native имена инструментов настроенного MCP-клиента; пример использует server `repo_checks` и имена `run`/`cancel`. |
| `cli_argv` | Непустой список строк: действительный префикс CLI выбранной установленной поставки, например абсолютный Python и `-I -B -m poise checks`. |

Неизвестные options, включая дополнительные ключи tool_selectors, сохраняются
и не участвуют в выборе run/cancel. Известные обязательные значения проверяются.
Рабочие пути и имена выбирает оператор; заполнение примера не устанавливает server.

Добавьте путь handler configuration к существующим PreToolUse и PostToolUse routes
один раз. В обоих descriptor matcher должны попадать Bash и точные MCP имена
вашей установки. Ниже **фрагмент**: существующие lifecycle events, настройки,
SessionStart и остальные routes сохраняются.

```json
{
  "events": [
    {
      "event": "PreToolUse",
      "matcher": "Bash|mcp__repo_checks__run|mcp__repo_checks__cancel",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": 8192,
      "status_message": "Проверить обращение к проверкам"
    },
    {
      "event": "PostToolUse",
      "matcher": "Bash|mcp__repo_checks__run|mcp__repo_checks__cancel",
      "timeout_seconds": 30,
      "async": false,
      "context_limit": 8192,
      "status_message": "Сопоставить фактический результат проверки"
    }
  ],
  "routes": {
    "PreToolUse": ["/absolute/operator/checks-handler.json"],
    "PostToolUse": ["/absolute/operator/checks-handler.json"]
  }
}
```

Применяйте заполненную definition через существующий публичный owner установки
с действительной revision; сгенерированные native settings вручную не заменяются.
В PostToolUse route общий tool-result может идти после check-invocation:
сначала сопоставляется invocation, затем готовится
[представление MCP-проверок](tool-results.md#профиль-mcp-проверок).
Все прочие handlers сохраняют объявленный порядок и свои контракты.

Pre сохраняет обновления input от предыдущих handlers и добавляет `_invocation`
только к подходящему MCP run. Связь проверяется по настоящему caller,
проекту/зарегистрированной копии, policy и неизменённому запросу. Исполнение
допускается по действительному итоговому receipt цепочки: конечный permission deny
не разрешает запуск; control.stop сам по себе не является permission deny.
Post относится к собственному tool-use/invocation и наблюдает фактический результат.
Промежуточное yielded-сообщение не освобождает выполняющийся run, поздний Post
не заменяет другой текущий запуск.

Для явно включённого CLI пути у `checks` задаётся `--transport-policy`.
Распознаваемая форма содержит настроенный `cli_argv`, пары
`--config`, `--checkout`, `--policy`, `--view`, `--transport-policy`
и одно конечное `< packet.json`. Pre добавляет `--invocation` подготовленной
связи, сохраняя остальные параметры. Неизменённый запрос потребляется тем же
владельцем; обычный CLI без transport policy остаётся самостоятельным путём.
Известный прямой или обёрнутый запуск зарегистрированной проверки получает
адресное указание MCP selector. Точное совпадение может быть отклонено этой
политикой, слабое совпадение остаётся предупреждением; произвольный shell
не объявляется зарегистрированной проверкой по одному сходному слову.

Изолированные install/native/MCP проверки подтверждают конкретный сценарий
поставки; они не устанавливают hooks или MCP-клиент в действующей сессии
оператора. Её подключение проверяется отдельно владельцем этой установки.

### Контракты синхронной и асинхронной команды

Полный второй файл `/srv/poise/config/commands.json`:

```json
{
  "schema": "hook-handler-configuration-1",
  "id": "configured-commands",
  "handlers": [
    {
      "id": "echo-response",
      "implementation": {
        "kind": "command",
        "argv": [
          "/srv/poise/venv/bin/python",
          "-c",
          "import json,sys; p=json.load(sys.stdin); print(json.dumps({'schema':'poise-hook-handler-result-1','invocation_id':p['invocation_id'],'response':p['response'],'control':{'action':'continue','reason':None}}))"
        ]
      },
      "mode": "sync",
      "options": {},
      "execution": {
        "run_seconds": 20,
        "cleanup_seconds": 1
      },
      "on_error": "stop"
    },
    {
      "id": "async-observation",
      "implementation": {
        "kind": "command",
        "argv": [
          "/srv/poise/venv/bin/python",
          "-c",
          "import json,sys; p=json.load(sys.stdin); print(json.dumps({'schema':'poise-hook-async-result-1','invocation_id':p['invocation_id'],'effects':{'observed':True}}))"
        ]
      },
      "mode": "async",
      "options": {},
      "execution": {
        "run_seconds": 20,
        "cleanup_seconds": 1,
        "launch_seconds": 3
      },
      "on_error": "stop"
    }
  ]
}
```

Команды примера читают пакет со stdin. Первая возвращает полученный response без изменения;
вторая записывает только собственный факт обработки. Это иллюстрация протокола, не проверка
бизнес-эффекта и не native-событие. Путь Python заменяется фактическим установленным executable.

Вход `poise-hook-handler-input-1` содержит `invocation_id`, `mode`, `event`,
`event_receipt`, `configuration`, `response`. Синхронный результат
`poise-hook-handler-result-1` содержит тот же `invocation_id`, объект `response`
и `control`: `continue` с `reason: null` либо `stop` с непустой причиной.
Следующий sync-handler получает текущий response. Stop завершает оставшуюся цепочку;
уже запущенный async-процесс сохраняет собственный срок и результат.

Async-команда получает snapshot response на своей позиции и возвращает
`poise-hook-async-result-1` с тем же `invocation_id` и объектом `effects`.
`response`/`control` не являются async-выходом. Порядок запуска задан конфигурацией;
порядок завершения независимых процессов не обещается. Поздний async-результат не меняет
выданный native-ответ. Отделённый worker сохраняет свои execution/stdout/stderr и terminal
result после завершения callback; capture и очистка принадлежат существующим владельцам.

### Сохранённый результат, ошибки и повтор

Доставка хранится как `poise-hook-event-delivery-2`: настоящий event receipt,
configuration revisions, запланированные UUID, invocation outcomes, текущий response,
`chain_complete`, `chain_failed`. Неизвестные опции рабочих конфигураций сохраняются raw,
игнорируются при исполнении и участвуют в revision; обязательные известные поля и строгие
handler input/result/control остаются проверяемыми контрактами.

Некорректная конфигурация/неподключённая Task-возможность отвергается до коммита события
и handler effects. Ошибка sync-команды записывает outcome/error/execution, завершает цепочку
и сохраняет текущий response. Возврат этого response не превращает failed delivery в
успешный бизнес-результат. Ошибка async сохраняется в его собственном результате.
Полные файлы читаются по сохранённым путям; короткий ответ не заменяет их исход.

Обычный повтор завершённой доставки возвращает сохранённый response, а известный launched
async не запускается снова. `hook --retry` выбирает настоящую сохранённую доставку,
проверяет её definition/settings/receipt и сохраняет identity. Это операция продолжения,
а не универсальное read-only чтение или гарантия нового исполнения. Не-launchable/unknown
исход требует установленного результата и восстановления у его владельца.

Гарантия атомарного допуска двух конкурирующих callers одной незавершённой delivery
здесь не предоставляется. Поддержанный обычный replay нельзя выдавать за concurrent
exactly-once. Осознанный новый запуск команды агентом остаётся новой операцией.

## Интерфейсы
- `poise runtime-setup --settings <new-file> --max-input-bytes <explicit-limit>`: stdin `{settings:
  <full settings>, installation: {request_id, expected_revision, definition}}`. Создаёт settings,
  все native hooks и выполняет выбранные probes одним запросом. При повторе те же настройки
  принимаются;
  другое содержимое существующего settings не перезаписывается. Начальный лимит задан аргументом,
  потому что settings ещё нет.
- `poise runtime-config --settings <file>`: stdin `{operation: install, input: {request_id,
  expected_revision, definition}}` устанавливает весь definition и проверяет probes выбранного
  capability handler одним
  вызовом. `operation: probe` принимает `definition_path` и `workspace` для явной повторной
  диагностики. Ошибка проверки доступности не отменяет факт создания настроек.
- `operation: summary` читает сохранённую сводку своей сессии; `operation: refresh`
  выполняет проверки заново и сохраняет результат. Обе операции `runtime-config`
  принимают только `binding_path`; [точные пакеты и результаты](#сводка-готовности-сессии).
- `poise hook --settings <file> --definition <immutable-definition>`: внешний протокол Codex, один
  native event на stdin; не агентский lifecycle API.
- `poise hook --settings <file> --definition <definition> --retry <saved-response>` продолжает
  либо возвращает сохранённую доставку по её действительному состоянию. Новое native-событие не
  создаётся.
- Сгенерированный `work.sh`: принимает обычный пакет WorkTools на stdin. Повторять task ID/session
  ID не требуется.
- Старые `work`, `runtime` и `goal-config` остаются самостоятельными явными интерфейсами.
  Hook-события и JSONL нельзя одновременно использовать как источник одних и тех же сообщений.

Bootstrap-настройки устанавливают root, project_config, interpreter, source_root, native hooks file,
storage paths, modes и лимиты. Это не настройки из AGENTS.md и не угадывание по cwd.
`config/hook-templates` содержит исторические исходные шаблоны; активную definition2
составляют по текущему договору цепочки. Шаблон не восполняет отсутствующие поля.

## Настройки установки

Полный обезличенный [пример hook settings](../../config/hook-settings.example.json)
предназначен для заполнения оператором. Рабочая копия не коммитится. Проектные БД
размещают по [схеме нового проекта](storage-layout.md#новый-проект-шаблоны-и-заполнение);
краткий порядок — [агентская
инструкция](../../.agents/references/project-storage-setup.md#project-storage-setup).

| Параметр | Назначение и допустимое значение |
|---|---|
| `schema` | Ровно `hook-settings-1`. |
| `root` | Существующий общий файловый корень; относительное значение разрешается от файла settings. |
| `project_config` | Путь общего профиля `poise-harness-project-1` внутри `root`, относительный либо абсолютный в той же границе. |
| `python`, `source_root` | Явные абсолютные пути исполняемого Python и единственного каталога исходников Poise с `poise/__init__.py` и `poise/__main__.py`. |
| `hooks_file`, `definitions` | Пути native hooks и неизменяемых определений относительно `root`. Общий native-файл может иметь абсолютный путь при явном `repository_scope`; definitions и operational state разделены. |
| `repository_scope`, `hooks_lock` | Необязательный режим общей установки: объект с абсолютным `repository` и положительным `timeout_seconds`, плюс отдельный абсолютный writer lock. Все писатели общего native-файла используют один lock. |
| `state` | Проектный корень Hook Registry относительно `root`, например `poise-project-data/demo/codex-hook`. |
| `database` | Файл БД относительно `state`, например `sessions-runtime.sqlite`. |
| `lock` | Lock-файл относительно `root`, рядом с БД. |
| `bindings`, `observations` | Каталоги привязок и диагностических наблюдений относительно `state`. |
| `launcher`, `binding_file`, `response_file` | Непустые относительные имена выходных файлов; launcher и binding различаются. |
| `cwd_roots` | Непустой список явных абсолютных разрешённых рабочих каталогов. |
| `file_mode`, `executable_mode` | Целые POSIX modes `0..511`; executable mode содержит бит исполнения владельцем. JSON записывает числа десятично: `384` = `0600`, `448` = `0700`. |
| `lock_seconds`, `lock_poll_seconds` | Явные положительные числовые интервалы ожидания и опроса блокировки. |
| `max_input_bytes`, `output_chars`, `max_probes` | Явные положительные целые лимиты входа, отображения и числа проб; output budget вмещает адрес receipt. |
| `probe_files` | Объект с различными непустыми относительными именами `stdout`, `stderr`, `receipt`. |
| `exit_codes` | Объект с различными целыми `success`, `incomplete`, `rejected` в диапазоне `0..255`. |

Строковые пути обязательны, не содержат NUL и разрешаются владельцем конфигурации.
Settings не выбирает другой интерпретатор при отказе и не устанавливает пакет.
`source_root` указывает выбранные оператором исходники. В этом пути не допускается
разделитель списка путей Python (`:` в Ubuntu): одно значение задаёт один каталог.
Изменение settings действующей установки требует согласованной публикации через владельца;
существующие binding/launcher не переписываются произвольно.

Опции возможностей задаются выбранными handler configurations, а не автоматическим
разбором прежних top-level capability полей settings. Для `summary`/`refresh` необходим
один согласованный `capability-summary` в маршруте SessionStart; его `options.summary`
проверяется соответствующим владельцем. Install без этого builtin не запускает чужие
capability probes. Неизвестные рабочие настройки сохраняются raw; известные транспортные
поля остаются обязательными.

Новая установка получает полный settings/definition2 intent; для действующей используется
разрешённый `reconfigure`. Он сохраняет project/stores и собственные bindings. Выбор нового
общего профиля вместо прежнего `project_config` не является разрешённым полем этого вызова;
такой переход требует конкретного согласованного owner-плана с проверкой действующей
установки. Успех изолированных примеров не подтверждает live cutover.

## Установка

При обновлении установленного пакета с `1.1.1` на `2.0.0` явно подготовьте
`limits.sqlite_read_seconds` в выбранной конфигурации проекта и тестовых конфигурациях
потребителя. Это обязательный положительный конечный лимит чтения SQLite в секундах;
полный пример находится в [конфигурации проекта](../../config/project.example.json).
Отсутствующее значение не подставляется автоматически. Версия `2.0.0` обозначает
несовместимое изменение этой конфигурации; протокол наблюдения native-сессии остаётся прежним.

Сначала проверяются весь definition и настройки; обязательные поля не дополняются defaults.
Существующие чужие группы hooks сохраняются. Заменяются только ранее установленные целые группы
данного definition. Изменённая вручную owned-группа вызывает отказ, а не перезапись.

`expected_revision` — наблюдённая полная revision установки из
`HookService(settings_path).revision()`
либо explicit null при отсутствии native-файла. Она учитывает raw settings/профиль/связь проекта,
Task-источник при подключённом adapter и выбранные конфигурации/политики. В новой квитанции
`revision` — эта полная версия, `native_file_revision` — digest native-файла. Историческая квитанция
возвращается в своём исходном формате. Повтор request_id с тем же намерением возвращает receipt;
другое намерение под тем же ID отвергается.
Незавершённая запись использует сохранённый кандидат и проверяет состояние before/after.

Сначала публикуется неизменяемый definition в config root, потом одним atomic replace обновляется
hooks.json. Operational SQLite сохраняет pending/completed receipt. Одна SQL-транзакция НЕ делает
файл частью SQLite; повтор сверяет фактический digest.

Перед запуском Python управляемые native-команды и session launcher проверяют,
что выбранный путь задаёт один каталог с `poise/__init__.py` и `poise/__main__.py`.
Отсутствующий или неполный каталог и разделитель списка путей приводят к отказу
с настроенным `exit_codes.rejected` и указанием восстановить исходники либо
исправить `source_root` в названном файле settings. Установленный в Python пакет
не подменяет недоступные выбранные исходники; зависимости Python остаются доступны.
После этой проверки команда выполняет явно настроенный Python с
`-P -B -X pycache_prefix=<os.devnull>`: текущий каталог не подменяет настроенные
исходники, а чтение старого timestamp bytecode не скрывает их изменения.
`os.devnull` — стандартный нулевой путь платформы (`/dev/null` в Ubuntu),
не каталог кэша; `-B` запрещает запись bytecode. Новые каталоги не создаются.
Точная ранее управляемая форма launcher (включая снятую shell-обёртку и прежнюю форму без
`-B`, а также выданная прямая форма с `-P -B -X`) при следующем native event атомарно
заменяется текущей командой с проверкой исходников; любое другое
изменение launcher считается внешним и вызывает отказ без перезаписи. Рабочий `hooks.json`
обновляется через `runtime-config`, а не одной лишь заменой генератора команд.

Если содержимое собственного launcher точно совпадает с выданной командой, повторный
native event восстанавливает также настроенные исполняемые права. Изменённое содержимое
не перезаписывается. Проверка доступности установленного `poise/__main__.py` выполняется
перед native/work/install вместе с проверкой Python; диагностика называет точный путь.
Нельзя подставлять Task checkout вместо исчезнувшего installation source.

Наличие launcher и доступность настроенного интерпретатора — разные условия. AI poise не
добавляет shell-preflight или специальный протокол восстановления: если путь из hook settings
отсутствует либо не исполняется, команда не запускает AI poise. Оператор проверяет этот путь и
обычным способом восстанавливает выбранное окружение, после чего повторяет нативное событие.
Task DB и сохранённая binding для такой диагностики не редактируются.

Новый/изменённый hook требует пользовательского review/trust в Codex. Инструмент возвращает
`requires_user_review` и `live_codex: not_observed`; не включает `--dangerously-bypass-hook-trust`,
не правит config.toml и не выдаёт генерацию файла за активацию в приложении.

## Native события и счётчик
`SessionEstablisher` является владельцем перехода от caller identity к сессии Task-runtime
для hook, runtime и прямого `work`. Его назначение сохраняется в Task DB `runtime_bindings`.
Native-реестр независимо фиксирует binding и факт `SessionStart`/`SessionEnd` до обращения
к контексту задачи или потребителю:
новая hook binding использует native ID, а уже записанная binding не переименовывается при
повторном входе. Origin не даёт дополнительных lifecycle-прав. Существующая проверка
неизменности managed hook-файла сохраняется, но отдельный anti-tamper протокол для generated
caller binding не вводится.

Настоящий SessionStart создаёт или восстанавливает binding; при подключённом Task adapter
создаётся launcher, который выдаёт выбранный task-context handler. UserPromptSubmit использует
уже существующую настоящую Start binding и фиксирует
source/session/turn_id без хранения prompt; все причины, включая неизвестные, считаются. Повтор
этого turn_id не увеличивает число. Два разных turn_id с одинаковым текстом — два события. До выбора
задачи факт остаётся известным проекту; текущий пользовательский ход связывается при выборе/работе
через launcher. Старые неопределённые taskless ходы не приписываются новой задаче задним числом.

Stop сообщает текущую позицию. Он НЕ возвращает `decision: block`, НЕ создаёт synthetic user turn,
НЕ принимает этап и НЕ запускает следующую работу. SessionEnd фиксирует факт окончания,
а выбранный lifecycle-consumer доставляет этот факт приложению без восстановления Task-runtime.
Общий work-context использует этот же committed End, не отдельный lifecycle или cleanup owner.
После завершения обработанной End-цепочки transport owner освобождает временный runtime
сессии и operational binding. Запись failed invocation сама по себе не удерживает live binding;
действительные End receipt и сохранённые результаты остаются историей. Освобождаются:
строку рабочего реестра,
binding-файл, launcher и настроенную capabilities-сводку. Пустой каталог binding удаляется;
посторонние файлы в нём сохраняются. Результаты нужно выдать до завершения сессии.
Переходы Task и обычное освобождение владения остаются операциями своих владельцев.

Повторный Stop или SessionEnd после освобождения не создаёт binding заново. Настоящий
SessionStart восстанавливает рабочую привязку той же сессии. Prompt без предшествующей
живой Start binding даёт явный отказ; агент не создаёт поддельный старт ради допуска.
Диагностические наблюдения остаются доступными без binding.
Освобождение одной Task в продолжающейся сессии снимает Task/worktree ownership, сохраняя
доступ сессии для следующей разрешённой работы.

Очистка повторно проверяет, что последним native-фактом остаётся именно выбранный End.
Более новый Start сохраняет свой runtime и привязку. Для очистки читаются настроенные
пути; восстановление контекста Task не запускается. Незавершённый эффект сохраняет свои исходные
данные для восстановления владельцем;
его результат и историческая End authority не подменяются наличием live binding. Зафиксированный End
хранит собственную запись привязки
в истории события: её освобождение не лишает настоящую квитанцию возможности проверки
и повторной доставки. Start проверяется по действующей operational binding.
Диагностика End сопоставляет сохранённый native-факт с точными квитанциями запусков.
После освобождения привязки результат по-прежнему подтверждается только при совпадении
сессии, настоящих Start/End и фактического отменённого завершения выбранного запуска.

Hook-события синхронны там, где требуется готовая binding, согласованный счётчик или доклад.
SessionEnd синхронен по внешнему протоколу. Никакой новой фоновой бизнес-мутации нет; асинхронные
представления результатов DDD-07 продолжают работать через прежний механизм.

Счётчик отражает наблюдённые hook events, не биллинг OpenAI. Coverage остаётся partial: транспорт не
доказывает, что платформа предоставила весь разговор. Прямой пакет messages через hooked-work
запрещён, чтобы не создать второй источник того же хода.

## Общая сводка после сокращения контекста

Общая механическая сводка выбирается builtin `general-context` с абсолютным
`options.policy_path` к полному JSON политики. Она наблюдает PreCompact/PostCompact
и продолжение SessionStart, использует прежние general-reader resources и выдаёт
короткую карточку без обязательной Task. При `task_adapter: null` её `work_owner.enabled`
и `check_owner.enabled` должны быть `false`; включённый Task owner требует adapter.
[Сценарии и восстановление](../workflows/context-recovery.md#общая-механическая-сводка)
принадлежат каноническому workflow guide. Полный текст инструкций читает
[ProjectInstructions](development-routing.md#подготовка-инструкций-по-файлам);
специализированный SourceReader сохраняет отдельный acknowledged-reader контракт.

Прежние обезличенные [settings](../../config/examples/context-recovery-settings.example.json)
и [definition](../../config/examples/context-recovery-hooks.example.json) показывают историческое
расположение данных. Для новой установки вынесите содержимое политики в отдельный файл
и создайте актуальные definition2/handler configuration по
[договору цепочки](#настраиваемая-цепочка-обработчиков). Подключите нужные восемь событий
явно: lifecycle, парные Bash tool events и PreCompact/PostCompact. Последние сами не
выполняют произвольную команду чтения. Числовые значения примеров не являются defaults.

### Первичное подключение общей сводки

1. Выберите установленную совместимую версию Poise/CPython3.15, проект и зарегистрированный
   checkout. Заполните полный reader profile с command policy по
   [существующему порядку](project-file-reading.md#подключение-учёта-обычных-команд),
   instruction catalogue/policy, отдельную политику сводки и handler configurations. Совпадение
   project message source
   и definition проверяет существующий владелец конфигурации проекта.
2. Передайте полный settings и `hook-definition-2`, ссылающийся на подготовленные
   handler configurations, в один
   [runtime-setup пакет](#первоначальная-настройка-без-ручного-json-файла). Settings destination —
   новый путь внутри configured root вне operational state. `expected_revision` описывает
   настоящий native-файл: null только для отсутствующего файла. Не устанавливайте второй
   конкурирующий профиль поверх уже принадлежащих другому владельцу групп.
3. Получите возвращённый неизменяемый `definition_path` и закончите заполнение operator reader
   profile: `admission.hook_settings` и `admission.definition_path` должны соответствовать
   фактической выбранной установке. Временный `definition-pending.json` не доказывает authority.
   Выполните `prepare_template` до первого настоящего Start; это не требует активного episode.
4. После настоящего Start проверьте native entry/предупреждения и фактическую короткую карточку.
   Первый resource имеет generation0; отсутствующий checkpoint означает `missing`, а не память
   предыдущей работы. Общий reader и ordinary command adapter используют прежний admission и
   одну shared registry. Получайте необходимые тексты явно через их действующих владельцев.
5. Сопоставьте настоящие Pre/Post/compact Start, новую карточку и владельческие записи. Такая
   проверка конкретного хоста выполняется отдельно от schema validation, probes и isolated
   installed-package tests. SessionEnd/recover и incomplete retry имеют порядок workflow guide;
   настройка не отменяет активные проверки и не выполняет переходы Task.

Runtime раскрывает только свои документированные runtime placeholders; маркеры этих файлов
`@...@` — явные места для заполнения, не environment interpolation.

| Маркер | Явное значение |
| --- | --- |
| `@ROOT@` | Абсолютный корень выбранной установки; settings/project paths разрешаются по существующим правилам. |
| `@PYTHON@` | Абсолютный Python выбранного установленного пакета. |
| `@SOURCE_ROOT@` | Единственный корень выбранного Poise с `poise/__init__.py` и `poise/__main__.py`, а не читаемый checkout. |
| `@CHECKOUT@` | Зарегистрированный абсолютный checkout; в примере одновременно выбран instruction root. Для внешних навыков задайте другое явное `instructions.root` в политике сводки. |
| `@READER@` | Абсолютный reader profile; его выбирает `command-intake.options.policy_path`, а политика сводки ссылается на него через `reader_policy`. |
| `@CATALOG@`, `@INSTRUCTIONS@` | Абсолютные instruction catalogue и policy для выбранных файлов. |

Reader profile и command policy переиспользуют собственные
[маркеры и параметры](project-file-reading.md#подключение-учёта-обычных-команд).
Согласуйте `reader_policy` политики общей сводки, `command-intake.options.policy_path`,
reader `command_intake.policy`, настоящую native authority и подготовленный schema-2 template.
Пути файлов `instructions.paths` выбраны явно для примера и заменяются файлами вашего проекта.
Рабочие settings/policies и state не коммитятся; versioned templates остаются обезличенными.

### Параметры general_context

В отдельном JSON политики по `general-context.options.policy_path` все перечисленные поля
обязательны. Неизвестные опции сохраняются raw и игнорируются; файл не добавляется автоматически.

| Поле | Допустимое значение и смысл |
| --- | --- |
| `reader_policy` | Абсолютный адрес полного профиля прежнего general reader. |
| `checkpoint_directory` | Непустой относительный путь внутри owned resource; без пустых, `.`/`..` компонентов и escape. |
| `checkpoint_filename` | Непустое имя одного файла внутри собственного checkout checkpoint directory. |
| `summary.max_bytes` | Положительный integer UTF-8 budget карточки с её prefix; должен вмещать обязательный протокол и фактические identity/ref/actions. |
| `summary.max_changed_paths` | Положительный integer предела Git changed paths. |
| `summary.max_instruction_changes` | Положительный integer предела списка instruction changes и конкретных read addresses. |
| `summary.max_coverage_items` | Положительный integer общего предела coverage documents/pending; прежний coverage owner сохраняет собственные limits. |
| `instructions.catalog` | Абсолютный адрес полного выбранного skill catalogue. |
| `instructions.root` | Абсолютный корень навыков; checkout rules продолжают разрешаться от checkout. |
| `instructions.policy` | Абсолютный адрес ProjectInstructions policy. |
| `instructions.paths` | Явный непустой список project-relative файлов для выбора применимых правил/навыков. |
| `work_owner.enabled` | Boolean; запрашивать текущие work metadata существующего владельца. |
| `check_owner.enabled` | Boolean; запрашивать metadata текущего check descriptor. `completed` не означает test PASS. |

Boolean не является integer budget. При отключённых work/check владельцах Task не
запрашивается. Отсутствие выбранной Task при включённом work owner допустимо; произвольные
Task requirements, snapshots и исторические receipts в карточку не копируются.

`PreCompact` и `PostCompact` требуют настоящие непустые `turn_id` и `trigger`. Сохранённые
workspace/order и matching cycle удостоверяет прежний native owner. `SessionStart` поддерживает
настроенные причины startup/resume/compact/clear/fork; один записанный факт нельзя выдать
за другой, изменив envelope. Для общего reader native owner применяет typed transition;
публичный reader `advance_context` по-прежнему принимает только `reason=manual`.
Специализированный reader имеет отдельные причины/поколение и не получает расширение своего
протокола из наличия общей карточки.

Все настроенные части Start-контекста делят один UTF-8 `context_limit`; JSON-вывод отдельно
проверяется по `output_chars`. Ограничение карточки `max_bytes` не заменяет остаток общего
бюджета. При нехватке места списки получают честные omissions; обязательные refs и структура
не обрезаются. Подберите явные бюджеты для реальной длины адресов и остальных producers.
Факт успешного заполнения/установки/probe не доказывает получение событий моделью.

### Изменение уже существующей установки

`runtime-setup` повторяет прежний полный intent и сохраняет настройки, а существующую
установку перенастраивает `runtime-config reconfigure`. Политика общей сводки выбирается
новой handler configuration через `options.policy_path`; её путь включён в definition2/routes,
а её полные байты входят в текущую configuration revision. Предварительно подготовьте полный
набор согласованных reader/instruction policies; владелец проверяет его до публикации.

`reconfigure` не меняет произвольные transport settings, project или stores. В частности,
старое поле settings.general_context не является каналом изменения активной сводки.
Сохраните действительные identity/association/revisions; изменения существующих consumers
и source/config cutover требуют настоящего согласованного owner-плана. Чужие группы,
активные binding и unknown outcomes не исправляются подстановкой session/profile ID.

## Настроенный потребитель lifecycle

Потребитель приложения подключается builtin `lifecycle-consumer`. Общий Poise работает
без него. Для выбранного handler `options.consumer` содержит все известные поля:

| Поле | Значение |
| --- | --- |
| `argv` | Непустой массив точных аргументов потребителя; без добавленного `--dry-run` |
| `cwd` | Абсолютный рабочий каталог |
| `environment` | Явный словарь строковых переменных окружения дочернего процесса |
| `timeout_seconds` | Положительный конечный предел выполнения потребителя |
| `cleanup_seconds` | Положительный конечный резерв завершения принадлежащей группы процессов |
| `poll_seconds` | Положительный конечный интервал существующего process owner |
| `completion_reserve_seconds` | Положительный конечный резерв формирования ответа |
| `max_output_bytes` | Положительный целочисленный предел принимаемых stdout и stderr вместе |

Неизвестные параметры не применяются. Невалидные известные поля отклоняются до native-мутаций.
Подлинная identity дочернего процесса сохраняется; `options.consumer.environment`
не может подменить полученные CODEX_THREAD_ID/CODEX_SESSION_ID/POISE_CALLER_BINDING.

Выбор handler и его полной конфигурации публикуется в definition2/routes через
существующий `runtime-config` owner. Старое settings.lifecycle_consumer не заменяет
`options.consumer` в активной цепочке.

Внутри общего event delivery выбранный consumer сохраняет собственное наблюдение
`poise-hook-lifecycle-delivery-1`, затем
потребитель получает на stdin объект `poise-hook-lifecycle-1` с полями `native_event`,
`event_receipt` и `deadline_monotonic`. `native_event` содержит исходное событие. Квитанция:

```json
{
  "schema_version": 1,
  "event": "SessionStart",
  "event_id": 122,
  "project_id": "example-project",
  "session_id": "external-native-session",
  "binding_id": "internal-session-id",
  "native_start_event_id": 122
}
```

`session_id` — внешняя native identity, `binding_id` — внутренняя привязка Poise;
они не обязаны совпадать и не заменяются друг другом.

Для Start `native_start_event_id` равен самому событию, для End — реальному предшествующему
Start. Это факт доставки, а не замена канонического anchor приложения. Read-only метод
`HookService.bind_lifecycle_receipt(definition_path, event_receipt, episode)` проверяет
реальные native-строки, binding, проект и отсутствие End между каноническим Start и событием.
Возвращается `{schema_version, event, event_id, episode}` с сохранённым anchor приложения.
Выбранное определение и определение текущего binding должны принадлежать одной
установке: сравниваются их `id`. Штатный `reconfigure` может создать новую версию
определения этой установки, сохранив `id`, `agent_id` и `message_source`.
Такое обновление не меняет эпизод приложения и не требует нового native Start;
определение другой установки не подходит для его квитанции.
Наблюдение установки сверяет сохранённую публикацию с фактическим native-файлом.
Новый формат команд текущего генератора сам по себе не меняет опубликованный факт
и не лишает действующую сессию её эпизода. Диагностика и первая привязка проекта
используют одного владельца этого read-only наблюдения.

Перед эффектами потребитель дополнительно вызывает read-only
`HookService.require_lifecycle_event(definition_path, event_receipt, native_event)`.
Владелец сверяет вид события, сессию и выбранный репозиторий, затем сохранённые в native-строке
`source` и абсолютный workspace для Start либо `reason` для End. Эти значения определяют
операцию и её объект: событие startup нельзя выполнить как clear, заменив поле конверта.
Несовпадение даёт причину и требование повторить настоящую сохранённую доставку; локальные
данные не изменяются. Публичный retry использует ту же проверку до обновления квитанции
и запуска потребителя. Привязка эпизода без эффектов остаётся отдельным наблюдением.
Read-only метод `HookService.current_definition_path(definition_path)` выбирает текущее
определение зарегистрированной установки по её `id`. Он не создаёт реестр и не
захватывает блокировку выполнения Task. Клиент после `reconfigure` создаёт авторитетный
источник с этим текущим путём для наблюдений, сохраняя исходную идентичность своей
конфигурации. Сохранённый эпизод и ID сессии при этом остаются прежними. Отсутствующая
установка или определение другой установки дают явную ошибку; исходный путь не
подставляется вместо текущего.
Resume, clear и compact не переименовывают уже выбранный эпизод; старый End не связывается
с новым эпизодом. End без наблюдённого Start сохраняется, но не передаётся потребителю
с выдуманным anchor.

`deadline_monotonic` — общий абсолютный срок на том же хосте, отсчитанный от входа native-hook
и timeout выбранного события. Выполнение потребителя использует минимум его timeout и
остатка после резервов. Исчерпание бюджета не запускает дочерний процесс. Используется
`RegisteredCheckRunner`: анонимный stdin-файл, учёт общего вывода, отмена при превышении
предела, завершение группы, bounded drain и reap. Nonzero, timeout, превышение объёма, невалидный
JSON или незавершённый teardown оставляют явное incomplete-наблюдение и сохранённый
native-факт; stderr потребителя не копируется в автоматический контекст.

Task-контекст и общая сводка — отдельные явно выбранные handlers.
`task-context` использует подключённый adapter и прежний ContextRecovery при наличии
reader; `general-context` использует свою политику и независимые work/check metadata.
Последние не равны полному Task ContextRecovery. Их порядок относительно lifecycle
consumer задаётся routes/configurations, а не условным скрытым fallback.
Pre/PostCompact обрабатывает выбранный общий handler; внешний consumer поддерживает Start/End.

Потребитель возвращает JSON-объект: для Start допустим `hookSpecificOutput`
с `hookEventName: SessionStart` и строкой `additionalContext`, для End достаточно `{}`;
`systemMessage` при наличии — строка. Применяются существующие native-лимиты контекста и
вывода. Другие управляющие поля потребителя не становятся командами платформе.

Исторический `HookService.probe_lifecycle_consumer()` читает consumer из старого
settings.lifecycle_consumer. Его результат не доказывает готовность другого `options.consumer`
из активной цепочки. Действующий consumer проверяют по его фактическим argv/cwd/environment
через существующую диагностику владельца приложения; если он поддерживает `--dry-run`,
это отдельный явный probe без native-события. Выбранный capability command probe также
может вызывать эту настоящую команду с полными параметрами.
Смысл готовности проверяет владелец приложения перед удалением своих raw Start/End-групп.
Он проверяет выбранный проект, корень, native-настройки и definition, сохраняет чужие группы
и повторяет собственную операцию после прерывания. Готовность потребителя не подтверждает
публикацию producer. Перед удалением прежних групп приложение под существующими блокировками
native-файла и настроек вызывает
`HookService.require_lifecycle_publication(definition_path, document)`.
Существующий владелец публикации проверяет выбранную регистрацию и фактическое наличие
обеих настроенных producer-групп SessionStart/SessionEnd в текущем файле hooks.
Отсутствующая или изменённая группа сохраняет прежние ERP-группы; сначала повторите
публикацию native-определения через его владельца, затем установку hooks приложения.
Изменившиеся за время подготовки настройки producer или consumer также требуют повторения.
Эти два владельца не обещают общей
атомарной публикации или отсутствия событий между шагами. Промежуточный producer-only режим
сохраняет native-факты; приложение использует своё публичное activate/recover.

## Проверки capabilities
Все probes выбранной возможности задаются массивом `capability-summary.options.probes`;
сначала валидируется весь массив. Ошибка одного внешнего
инструмента не прячет результаты остальных. Каждый probe имеет exact argv/cwd/environment, срок
выполнения, ограничения вывода, признак required, tool_ref и исполнимую рекомендацию восстановления.
Окружение не наследуется неявно; секреты не следует записывать в параметры/вывод.

Command probe подтверждает ровно наблюдаемое: код выхода, признаки stdout/stderr, при необходимости
точные JSON predicates. Версия не подставляется из expected config. Непонятный ответ, отсутствующий
executable и timeout означают unavailable, не успешную capability и не автоматически AI poise
Incident.

MCP stdio: initialize → exact negotiated protocol version → initialized → bounded tools/list →
optional explicit read-only tools/call. Pagination и notifications ограничены общим временем, числом
сообщений/страниц и байтами. Неподдерживаемая версия не запускает другой parser или transport.
Запрашиваемые имена должны реально присутствовать в tools/list.

`mcp_tools_listed` не равен выполненной операции. `mcp_smoke_passed` подтверждает только конкретный
read-only smoke. Например, наличие rename в каталоге не доказывает корректность самого rename. Для
project_bound обязателен JSON predicate с точным текущим workspace; чужой открытый проект не
считается подходящим. Используются explicit endpoints/команды, не сканирование портов.

В этом срезе поддержан STDIO, включая явно настроенный прокси существующего сервера. Прямые HTTP
Stream/SSE и OAuth не реализованы. Для JetBrains используются команды из Copy Stdio Config и явные
имена exposed tools; подключение живой IDE требует пользовательской среды.

## Сводка готовности сессии

Выбранный `capability-summary` на SessionStart с причиной startup/resume/compact/clear/fork
синхронно проверяет свой `options.probes`. Агент получает заголовок и строки по
`options.summary`; остальные части контекста производят их явно выбранные handlers. У каждой
возможности указаны `status`, `required`, `reason` и `remediation`. Готовность `ready`
определяется обязательными условиями: недоступное необязательное средство видно в сводке,
но при выполненных обязательных условиях готовность сохраняется. Обычный отказ команды,
timeout или несовместимый ответ MCP сохраняет результаты остальных проверок и конкретную
причину восстановления. Неисправный план проверок отклоняется до их запуска.

Полный JSON находится по `capability-summary.options.summary.file` внутри каталога binding.
Формат `capability-summary-1` содержит `schema`, `session_id`, `workspace`, `checked_at`,
`ready`, `missing_required`, `observations`. Время `checked_at` — UTC-наблюдение завершённой
проверки. Список сохраняет полные результаты существующих probes, включая MCP `tools`,
`version`, `observation` и путь receipt, с добавленными `required` и nullable `reason`.
У доступного средства `reason` равен `null`; пример текстового шаблона показывает булевы
значения как `True`/`False`, а отсутствие причины как `None`.

Оператор задаёт явные значения в [конфигурации capability
handler](#конфигурация-и-встроенные-возможности).
Там перечислены поля `header_template` и `item_template`;
шаблоны не заменяют полную сохранённую сводку. Ограничение `max_input_bytes` применяется
к её чтению и записи. `context_limit` события и `output_chars` ограничивают показ агенту,
а не обрезают данные файла.

Обычное чтение своей привязки:

```json
{
  "operation": "summary",
  "input": {
    "binding_path": "/absolute/path/to/own/binding.json"
  }
}
```

Явное повторное наблюдение:

```json
{
  "operation": "refresh",
  "input": {
    "binding_path": "/absolute/path/to/own/binding.json"
  }
}
```

Каждый пакет передаётся через `poise runtime-config --settings <actual-settings>`.
Путь берётся из реально созданной своей binding; native caller сохраняется и проверяется
тем же владельцем, что и у `work.sh`. Другую сессию не выбирают параметром запроса.
Результат чтения: `{status: "summary", summary: <saved-value>}`; обновления:
`{status: "refreshed", summary: <completed-value>}`. При `ready=true` используется
настроенный `exit_codes.success`, при `ready=false` — `exit_codes.incomplete`. Неудачная
операция после успешной инициализации `HookService` возвращает `status: rejected`, причину
и `exit_codes.rejected`. Ошибка при создании сервиса, включая чтение/проверку settings,
связанной конфигурации проекта или runtime association, завершает `runtime-config`
с exit `2` и stderr; структурированного ответа ещё нет.
Полный ответ сохраняется существующим владельцем вывода; если краткое представление возвращает
`response_path`, полный JSON читают из этого файла.

`summary` не запускает probes, не меняет время/байты сводки и возвращает последнее
завершённое наблюдение, даже если инфраструктура уже изменилась. `refresh` выполняет
новую проверку и сохраняет результат. Его workspace берётся из последнего настоящего
SessionStart этой сессии, отдельно от файла сводки и выбранной Task. UserPromptSubmit,
Stop и SessionEnd не запускают эту сводку; проверки `gate_operations` остаются отдельным
механизмом допуска работы и не подменяют сессионный файл.

При отсутствующем, повреждённом или структурно непригодном файле обычное чтение даёт
`rejected` без probes. Явный `refresh` восстанавливает файл при наличии наблюдённого
workspace. Для старой сессии, чьи события ещё не содержат workspace, требуется настоящий
SessionStart хоста; путь не угадывается из Task, cwd команды или испорченного JSON.
При отказе записи до атомарной замены остаются предыдущие завершённые байты; успешное
обновление не объявляется. Единой транзакции файловой системы и SQLite не обещается.

Завершённая сводка записывается до проверки бюджета native-контекста/ответа. При превышении
бюджета hook сообщает ошибку, а сохранённые данные доступны отдельным чтением. Отказ
native hook имеет exit `1` и stderr, сохраняя протокол хоста; это отличается от кодов
`runtime-config`. Корректная сводка с недоступной обязательной возможностью сама по себе
не означает ошибку доставки native hook. Проверки наблюдают инфраструктуру; установка
зависимостей, ремонт служб и фоновое обновление в эту операцию не входят.

## Рабочие границы
Task capability gate включается в `task-context.options.gate_operations`, без дополнительного model
tool call на каждую
capability. При bootstrap сначала подготавливается реально выбранный worktree, затем проверяется
привязка к нему; при недоступности возвращается blocked context, а не успешное разрешение писать
код. При verify проверка выполняется до запуска task methods. Read-only show, cancel и release не
блокируются отсутствием IDE.

Generated launcher связан с неизменяемым definition. Изменённые settings не подхватываются молча
существующей binding. Обычный повтор SessionStart не согласует изменённый digest с уже
записанной привязкой; подготовьте настройки через владельца с сохранением согласованности
файла и Hook Registry. Не переключать источник сообщений в конфиге уже
работающей задачи без отдельного решения: project execution contract защищён предыдущими срезами.

### Разрешение source root для native launcher

`native binding` сохраняет владельца идентичности вызывающей сессии. Для исходного
hooked проекта источник событий `codex-hook-main` владеет пользовательскими сообщениями;
явно выбранный проект использует собственный контракт источника сообщений. Каждый пакет выполняется
реализацией
AI poise из настроенного `installation source`; Task worktree не участвует в выборе runtime.
Это относится к taskless и адресному bootstrap, рабочим переходам, обычному release, проверке,
приёмке, интеграции и recovery.

В hook settings задайте `source_root` как один каталог установленного Poise, содержащий
`poise/__init__.py` и `poise/__main__.py`. Launcher проверяет этот источник перед запуском
и сам формирует import path из него и его родительского каталога, чтобы встроенный
обработчик `apps.bootstrap` был доступен вместе с Poise. Оператор задаёт только одиночный
`source_root`; пара import roots формируется владельцем launcher. Для работы с Task
передаётся её рабочая папка, а runtime остаётся настроенной установкой.

Task worktree остаётся рабочей областью кода и workspace для project-bound capability probes.
Его собственные `poise.__main__`, `HookService.work` и поддерживаемые схемы не являются
предусловиями lifecycle-вызова. Launcher не запускает дочерний интерпретатор из Task source и
не использует fallback: единственный runtime source уже задан в hook settings. Поэтому старая
Task может быть продолжена после обновления конфигурационной схемы AI poise runtime без
изменения её проверенного commit/tree. Параллельные bindings разделяют установленный код, но
сохраняют разные session identity, ownership и worktrees.

Уточнено: **2026-09-12**. Согласно контракту задачи 0026, изменение валидной конфигурации
проекта не блокирует ни рабочие переходы, ни чтение, ни отмену Task/Sprint. Сохранённый
`config_hash` — диагностический provenance, а не lifecycle gate. Операция использует текущую
конфигурацию и сохранённые Task-owned workflow/contract/method snapshots; изменение конфига
не переписывает эти снимки и не перезапускает задачу. Проверки native binding и настроенного
installation source выполняются независимо от этого правила.
Для Sprint явная отмена представлена операциями `cancel_tasks` и `cancel`.

### Явный выбор проекта через session launcher

Выданный своей нативной сессии launcher принимает `--project EXACT_ID`:

```bash
bash "$native_launcher" --project "$project_id" < "$packet"
```

Без селектора сохраняется проект исходной binding. Селектор задаётся один раз;
пустое, неизвестное или повторное значение отклоняется до выполнения операции.
Аргументы `--settings` и `--binding` принадлежат выданному entry: повторная передача
отклоняется, включая совпадающие значения. Рабочий каталог сам по себе проект не выбирает.

Для включения выбора оператор добавляет в hook settings необязательный объект
`project_launcher` с двумя обязательными полями:

| Поле | Значение |
|---|---|
| `settings_path` | Абсолютный путь к рабочим настройкам `stride-launcher-1`. |
| `context_template` | Инструкция агенту с единственным именованным полем `{launcher}`; должна объяснять `--project`. |

Образцы: [обычные настройки](../../config/hook-settings.example.json) и
[общая установка](../../config/shared-hook-settings.example.json).
Заполненные рабочие настройки не коммитятся. Без объекта явный выбор отклоняется;
выданный entry без селектора продолжает работать. Изменение hook settings публикуется
через `runtime-config` → `reconfigure`, который обновляет собственные bindings и точный
прежний entry без передачи аргументов. Чужое содержимое launcher блокирует обновление.
Пояснение использования `--project` включите явно в `task-context.options.context_template`
для Start/Prompt. Поле settings.project_launcher.context_template сохраняет свой обязательный
синтаксис настроек, но представление цепочки не добавляет его автоматически; действуют
обычные лимиты native context и явно выбранный handler.

Каждый явный вызов заново читает настройки запуска и реестр через общего владельца
[выбора проекта](stride-launcher.md#выполнение-команды). Он проверяет точный ID,
действующий manifest и размещение хранилищ внутри `data_root`. Изменённый адрес manifest
учитывается следующей операцией. Здесь используются только разрешение проекта и проверка
размещения: `command` и `overrides` общего launcher не подменяют session WorkTools.
Runtime остаётся из выбранного installation source.

Сначала проверяется настоящая идентичность вызывающей сессии. Исходная binding не
перепривязывается. При выборе своего hooked проекта сохраняется его обычный путь,
включая capability gate. Другой hooked проект требует собственную подлинную binding
той же нативной сессии в связанной установке; профиль берётся из этой binding.
Отсутствующая либо неоднозначная binding приводит к отказу. Для hookless проекта
существующий владелец сессии устанавливает идентичность в выбранном проекте, а WorkTools
проверяет его пакет. Источник сообщений определяется выбранным проектом: исходный
UserPromptSubmit не переносится в hookless ledger; объявленные сообщения
`agent_reported` сохраняются по собственному контракту выбранного проекта.

Селектор не расширяет `repository_scope` доставки hooks. Установка с scope основного
репозитория обслуживает его main checkout и Git worktrees; старт в другом репозитории
требует отдельно настроенной доставки. Проверка выбора проекта из принятого scope
не доказывает доставку вне него.

Краткая [инструкция
настройки](../../.agents/references/project-storage-setup.md#session-launcher-project-selection)
ссылается на этот полный контракт. CLI описан в
[справочнике](../operator/poise-command-reference.md#runtime-и-native-hooks).

### Привязка проекта и перенос установки

Manifest связывает существующие установки с проектом через `runtime_resources.hooks`:
каждая связь содержит `installation_id` и абсолютный `settings_path`. Связанный
`runtime_resources.project_setup` выбирает владельца реестра проектов и общего writer fence.
Наличие файла с похожим именем не доказывает authority. Проверяются зарегистрированная
установка, её settings, immutable definition и фактические native groups.

Первую связь публикует `project-config` через отдельный bootstrap из явно выбранных setup
settings. Перенос опубликованной связи выполняет
[владелец переноса состояния](project-setup.md#активные-задачи-и-перенос-state): он получает
полный целевой объект hook settings и вызывает существующего владельца установки.
Тот публикует свои целевые settings и native groups с прежним installation ID, сохраняя
чужие группы. Сам перенос не добавляет второй параллельный native handler.
Исходные и целевые settings должны выбирать те же `definitions` и `hooks_file`:
одновременная смена этих владельцев этим маршрутом не поддерживается. Целевой Hook Registry
должен быть копией существующего реестра с прежней installation identity, а не новой пустой БД.
Если уже расходятся registered и live installation ID, сначала применяется
[согласование identity](#согласование-installation-identity-после-переименования),
а не ручная правка Hook Registry или дописывание ещё одного handler.

Допуск переноса проверяет состояние bindings в обоих существующих registries
исходного и целевого размещения, включая возобновление прерванной публикации. Переносимая
native установка требует `DEAD`: `LIVE` или `UNCERTAIN` дают отказ. Hook Registry определяет
состояние по последнему записанному для него событию: `SessionEnd` означает `DEAD`, в том
числе после освобождения operational binding. Другое событие при действующей binding
означает `LIVE`; отсутствие binding без последнего SessionEnd или отсутствие события —
`UNCERTAIN`. Это критерий по событиям реестра; проверки процесса или PID в этом допуске нет.
Физическое завершение процесса без зарегистрированного `SessionEnd` не даёт `DEAD`.
Освобождение Task или сохранённый флаг также не заменяют этот критерий.
Завершение native сеанса выполняется его действительным владельцем. Подделка события,
отзыв чужого claim или удаление lock-файла не заменяют этот допуск.

После публикации проверяются целевые настройки и группы; перед удалением исходного state
authority и liveness проверяются снова. Сохранённый installation source продолжает выбирать
исполняемый код, Task worktree остаётся только рабочей областью результата. Проверка
кандидата в отдельной среде не является переключением установленных hooks или подтверждением
доставки native события на этой установке.

### Terminal cleanup через native launcher

Standalone `operation: cancel`, Sprint actions `cancel_tasks` и `cancel` выполняют terminal-
переход через владельца Task и сохраняют cleanup obligation. Они не удаляют worktree/ветку и
не превращают terminal status в решение о commit. После этого тот же session-scoped launcher
маршрутизирует публичный `operation: cleanup` с точными `task_id`, `request_id`, authorization,
`expected_commit` и disposition `preserved` либо `discard_authorized`.

Launcher возвращает `disposition_required`, `cleanup_blocked`, `cleanup_pending` или
`cleanup_complete`, а также точные `remaining_resources`, blocker и историю подтверждённых
шагов. Dirty worktree остаётся заблокированным даже после фиксации disposition: его можно
довести до clean fast-forward checkpoint той же task-ветки и продолжить новым request ID либо
вернуть к исходному commit вне cleanup и повторить прежний пакет. Принятый checkpoint
фиксируется в persisted cleanup history событием `worktree_checkpointed` со старым и новым
commit и `request_id`. Основной checkout, чужие ресурсы, отдельно поставленные постоянные результаты
и operator backups
не очищаются. Файлы внутри Task-root, включая зарегистрированные артефакты, подчиняются
[договору поставки и удаления](../workflows/terminal-materials.md#договор-и-граница-хранения).
Resource cleanup не заменяет этот договор; незавершённый consumer удерживает материалы.

Сохранённые ownership identity, commit/digest и версия проверяются перед каждым эффектом.
Поэтому конкурентное изменение ветки, файла или cleanup-state даёт actionable blocker/отказ,
а не удаление по одному имени. Повтор неизменного пакета после снятия блокировки продолжает
persisted шаги; скрытого фонового cleanup ресурсов Task при событии hook нет. Полный payload и
правила
commit disposition приведены в
[пакетном workflow](../workflows/batch-work.md#интеграция-принятого-результата).

## Данные
Task SQLite остаётся user_version=11. Новый отдельный operational hook registry имеет
user_version=1: installations, operations, bindings, hook_events. Он не содержит Task/Sprint
lifecycle и не экспортируется как рабочая история задачи. Сообщения сохраняет существующий
InteractionStore. Определения находятся в config root, рабочие bindings/launcher и receipts —
в настроенном state root. Operational binding и её генерируемые файлы принадлежат сессии
и освобождаются при SessionEnd; их расположение не делает их архивом завершённых сессий.
Подлинный факт окончания остаётся доступен владельцу восстановления dead-owner claims.

Перенастройка установки выбирает только `LIVE` bindings до чтения их файлов. Завершённые и
неопределённые привязки не перенастраиваются, их отсутствующие файлы не мешают обновлению
действующей среды. Очистка накопленных ранее operational bindings выполняется по точному
согласованному списку через владельца реестра с повторной проверкой записи и события окончания.
Изменившаяся или возобновлённая привязка пропускается с явным результатом; чужое содержимое
launcher требует разбора причины. Отсутствующие файлы уже выбранной завершённой привязки
не требуют восстановления перед освобождением.

Source JSON/полные протокольные ответы не отправляются в model context целиком. CLI возвращает
ограниченный ответ с адресом подробностей при необходимости. Нативный hook возвращает только
компактную binding/state информацию. Конкретные имена результатов probes задаются probe_files.

## Проверка на реальном хосте
1. Выбрать установленный interpreter и доступный путь AI poise; определить project и отдельный
   message_source типа runtime_event.
2. Установить пакет hooks инструментом и выполнить проверки. Не редактировать сгенерированные
   definitions/hooks вручную.
3. В Codex открыть `/hooks`, вычитать и разрешить конкретные новые handlers. Не обходить trust.
4. Начать новую сессию из AI poise project; проверить появление session-scoped launcher и один
   UserPromptSubmit на видимый ход.
5. Для IDE явно включить MCP, взять Stdio Config, указать требуемые tools и read-only smoke для
   нужного worktree; проверить available только после настоящего ответа.

Без доступного Codex и endpoint IDE эти пункты остаются NOT_RUN. Локальный native JSON replay и MCP
fixture этого не заменяют.

## Первоначальная настройка без ручного JSON-файла
Полный явный `settings` и выбранный `definition` передаются как значения одного stdin-пакета
`runtime-setup`. Агент не создаёт промежуточный request-файл. Шаблоны в `config/hook-templates` —
исходные образцы для составления пакета; каждое значение, включая interpreter/source/project paths,
выбирается явно. Содержимое базового project manifest уже должно быть настроено как в прочих runtime
interfaces.

Невалидный settings/definition отклоняется до записи новых файлов. Если после создания settings
произошёл внешний сбой установки, повтор того же пакета использует этот settings и продолжает
сохранённую установку; успех всех файлов как одной SQL-транзакции не обещается. Изменение рабочего
definition выполняется через `runtime-config`, не через ручную правку `.codex/hooks.json`.

Taskless read-only bootstrap не блокируется отсутствующей IDE: возвращает сводку и честные
результаты probes. Для подготовки активной задачи, verify и создания артефактов применяются явно
заданные gates. Чтение, release и отмена не заперты за этими gates.

## Прямая проверка на WSL
1. В своём WSL убедиться, что выбранный CPython 3.15 и Git запускаются. Подготовить project manifest
   с явным `batch.message_source` текущего hook-источника (`mode=runtime_event`). Не включать
   одновременно JSONL importer для тех же сообщений.
2. В одном пакете `runtime-setup` указать свои фактические пути и definition. Для JetBrains взять
   **реальную** команду из Copy Stdio Config IDE, сохранить точные argv/environment. В
   `required_tools` указывать только инструментальные имена реально используемого IDE; для проверки
   привязки задавать read-only call и JSON predicate с `${workspace}`. Не вставлять инструменты из
   тестового mcp_fixture.
3. Запустить Codex из AI poise root и через его `/hooks` проверить и разрешить собственные команды.
   Инструмент не меняет trust/permissions и не устанавливает разрешение вместо пользователя.
4. В новом основном разговоре проверить получение launcher, запустить из него пакет bootstrap и один
   этап. Действующее поручение исполнителю использовать для последовательных `executor`-этапов без
   отдельных команд «дальше»; переход к `reviewer`, acceptance, publication и integration разрешать
   отдельной явной инструкцией. Считать фактические UserPromptSubmit, а не количество WorkTools
   calls.
5. После этого сохранить receipt реального probe и результат короткого маршрута. До такого прогона
   поддержка провайдера имеет статус `not_observed`, даже когда локальный протокольный стенд прошёл.

Установка выбранных hooks не перехватывает все native инструменты Codex и не создаёт hooks в
ChatGPT. Read-only smoke доказывает исполнение только конкретной команды/операции. Он не доказывает
correctness rename/format без их отдельных испытаний. Live JetBrains endpoint, OAuth/HTTP/SSE и
Gmail находятся вне текущего среза.


Пакет тестов `hooks-source-runtime` проверяет архитектуру, выбор настроенных
исходников, команды hooks и согласование установки четырьмя полными модулями:
`test_architecture.py`, `test_runtime_recovery.py`, `test_hooks.py` и
`test_installation_reconciliation.py` из `tests/hook_transport/`. Он не заменяет
общий пакет `hooks`. Состав исходников, тестов и фикстур объявлен в
`config/testing/test-packages.json`; прямые входы архитектурных проверок и
`src/poise/__main__.py` включены в отпечаток пакета.

## Предварительная проверка настроенной среды

До установки hooks и до запуска содержательного `bootstrap` существующий владелец
`HookSettings` проверяет точный `python` из settings. `HookService` повторяет проверку
при операции, даже если экземпляр сервиса был создан до исчезновения интерпретатора.
Для работы через существующую binding дополнительно проверяется точный путь `launcher`.
Отсутствующий файл и неисполняемый файл различаются; ошибка содержит
`environment/configuration`, роль файла и полный настроенный путь. Отказ происходит до
создания runtime/claim/worktree и изменений Task. Это предварительное наблюдение, а не
гарантия от последующего удаления файла другим процессом.

После проверки выбранных исходников выполняются настроенный интерпретатор, `-P -B`, стандартный
параметр
`-X pycache_prefix=<os.devnull>`, настроенный `PYTHONPATH`,
`-m poise`, точные settings и native binding. Ни `recover_runtime.sh`, ни
`poise-runtime-guard`, ни запасной системный Python для исполнения Task не используются.
Если сам интерпретатор отсутствует, не запустившийся native workflow не может вывести
диагностику Python-кода. В этом случае оператор видит ошибку ОС/host; нельзя утверждать,
что host показал сообщение Poise или выполнил native hook.

### Действия оператора при потере среды

1. Прочитать ровно выбранные installation settings, сохранить полный путь `python`,
   `source_root`, путь выданного `launcher` и существующий `binding.json`. Проверить
   существование, тип файла, права исполнения и целевой файл символической ссылки
   интерпретатора. Не выбирать произвольный другой installation root или новую сессию.
2. Восстановить именно выбранное окружение обычными средствами установки проекта
   либо исправить подтверждённые права исполнения. Изменение settings — отдельная
   явная операция настройки; служебный digest не заменяет проверку объекта привязки.
   Не запускать предметную работу через случайный system Python.
3. После восстановления интерпретатора повторить штатный native `SessionStart`/resume
   с той же native session identity и definition. Если собственный launcher был удалён,
   существующий binding owner воссоздаёт его. Не удалять registry, чужие bindings и WIP,
   не сочинять session ID. Для существующего изменённого launcher сначала разобраться
   с внешним изменением, а не перезаписывать его вслепую.
4. Проверить полученный точный launcher, затем выполнить обычный `bootstrap`. Записать
   фактический результат, настройки и ошибку/обход в текущую Task или существующий
   incident. Taskless `read_only` доказывает только доступность чтения, не исправность
   live IDE/host и не завершение предметной задачи.

Если Poise блокирует отдельно разрешённую пользователем предметную работу, агент может
выполнить только эту работу вручную и сохранить реальные изменения, проверки, WIP и
описание инцидента. Это не разрешение чинить Poise в чужом проекте, создавать второй
harness или подделывать его статусы: исправление Poise требует отдельного поручения.

Историческое основание `POISE-RUNTIME-PREFLIGHT-FEEDBACK`: при работах 0152/0155 исчезал
настроенный интерпретатор, исходный запуск launcher завершался с exit 127. Добавленная
в 0152 shell-диагностика не имела доказанного показа пользователю; 0155 убрала избыточную
обёртку. Сохранённые тогда `read_only`/`ready` и наличие Python относятся к тем запускам,
не к текущему host. Новая предварительная проверка не возвращает удалённую обёртку.

## Согласование installation identity после переименования

Обычный `install` не присваивает себе группу, которая уже запускает definition того же
ID, но отсутствует в его ownership receipt. Вместо добавления дублирующего вызова он
требует явный `runtime-config reconcile`. Например, старый registry владеет
`harness-main`, а native-файл уже содержит прямые команды `poise-main`.

Оператор читает текущий `hooks.json`, его SHA-256 и точный `--definition` из существующей
управляемой команды. Immutable definition должен лежать в настроенном `definitions`,
иметь правильный digest и содержать тот же текущий ID, что и новый полный definition.
Исторический ID берётся из известной предыдущей установки, не угадывается по похожему
имени. Затем через обычный `poise runtime-config --settings <тот-же-settings>` передаётся:

```json
{
  "operation": "reconcile",
  "input": {
    "request_id": "rename-harness-main-to-poise-main-1",
    "expected_revision": "<sha256-текущего-hooks.json>",
    "previous_installation_id": "harness-main",
    "live_definition_path": "<абсолютный-путь-immutable-live-definition.json>",
    "definition": "<полный-объект-hook-definition-2-с-id-poise-main>"
  }
}
```

Значения в угловых скобках заменяются наблюдёнными путями/хешем и объектом definition;
последнее поле — объект, а не строка или имя файла. Старый и новый installation ID
различаются. Для каждого event нужна ровно одна неизменённая live-группа, доказанная
полным definition и прямой командой этой установки (текущая либо прежняя без `-B`).
Операция также удаляет присутствующие точные группы прежнего и текущего registered owner,
поэтому исправляет уже возникшую пару старого/current вызовов. Чужие группы и поля
native-документа сохраняются. Неизвестная группа не считается нашей только по названию.

Используются прежний installer lock, pending/completed receipt и atomic replace файла.
Receipt содержит старый/новый ID, live definition и fingerprints заменённых групп.
Историческая installation сохраняется с `migrated_to`, а не удаляется; старый ID нельзя
повторно установить обычным запросом или его replay. Старые bindings и Task DB не меняются.
Повтор того же запроса возвращает receipt; изменённый запрос под тем же ID отвергается.
Старая revision, изменённая/пропавшая/дублированная live-группа или чужой definition дают
отказ без замены файла. После сбоя между заменой файла и фиксацией registry нужно повторить
тот же пакет: сохранённый pending candidate сверяет before/after. Ручной SQL для ремонта
не нужен. После установки по-прежнему требуется обычное решение host о trust; наличие
одной группы подтверждает конфигурацию, не факт доставки конкретного native event.


## Изоляция native caller и передача делегату

`work.sh` не является удостоверением вызывающего агента. До открытия Task DB транспорт
сверяет наблюдаемый `CODEX_THREAD_ID` или `CODEX_SESSION_ID` с внешней сессией launcher.
Если обе переменные присутствуют, их значения должны точно совпадать. Пустое, недопустимое,
отсутствующее или чужое значение приводит к отказу без смены current Task и без освобождения
ownership. Launcher не экспортирует и не подставляет сохранённый ID вместо наблюдаемого.

У прямого `work` действуют те же проверки: наличие native identity вместе с
`POISE_CALLER_BINDING` отвергается до создания файла. Generated binding допустима только
при отсутствии native identity, для явно выделенного ненативного caller. Один файл нельзя
разделять между исполнителями. Существующие внутренние назначения сессий не переименовываются.

Основной hook-профиль отвергает payload с `agent_id`: у событий subagent `session_id`
может обозначать родителя, а `agent_id` — дочернего агента. Эти сигналы нельзя сворачивать
в binding родителя. Поддерживаемый путь — отдельная настоящая native session, её собственный
SessionStart и launcher. Отправитель сохраняет результат, выполняет обычный release
и напрямую уведомляет получателя; получатель своим bootstrap захватывает ту же Task
в существующей рабочей области по
[инструкции передачи](../workflows/local-handoff.md#передача-между-исполнителем-и-ревьюером).

Если платформа передаёт родителю и делегату полностью одинаковые сигналы, транспорт не может
доказать их различие. Проверка окружения не является защитой от намеренно подделанных данных
того же OS-пользователя. Не выдумывать ID и не редактировать окружение ради обхода проверки:
сохранить WIP через launcher настоящего владельца и продолжить в отдельной native session.
При отсутствии такой сессии delegated Task mutation не выполняется; обычная работа текущего
владельца остаётся доступной. Описание native payload: [Codex
hooks](https://learn.chatgpt.com/docs/hooks).


## Подключение событий чтения

[Полный command-enabled комплект](project-file-reading.md#подключение-учёта-обычных-команд)
сохраняет существующие четыре lifecycle события и добавляет ровно пару `PreToolUse`/`PostToolUse`.
У обоих `matcher="Bash"`, `async=false`, явные положительные `timeout_seconds`/`context_limit`
и непустой `status_message`. Пример задаёт15 секунд / 2000 контекстных единиц; это значения
установки,
не скрытые defaults. Один event без второго либо другой matcher отклоняются.

Оба события выбирают builtin `command-intake` с абсолютным `options.policy_path`
к полному reader profile. В нём задан абсолютный `command_intake.policy`; эти политики
проверяются до установки definition2 с tool events. Остальные обязательные настройки сохраняются по
[основному справочнику](#настройки-установки). Generic четырёхсобытийные шаблоны остаются
отдельным выбором и сами учёт обычных команд не включают. Их пробы и command example
согласованы с выбранным package runtime Python 3.15 (`>=3.15,<3.16`).

Для существующего native файла прочитайте `HookService(settings_path).revision()`,
передайте наблюдённое значение в установленный `HookService.install` либо
`runtime-config` packet:

```json
{"operation":"install","input":{"request_id":"operator-selected-install-id","expected_revision":"OBSERVED_REVISION","definition":{}}}
```

Здесь `{}` обозначает место полного объекта `hook-definition-2` с реальными routes, а не готовое
определение для отправки. `OBSERVED_REVISION` заменяется фактическим значением; null
допустим только при реальном отсутствии файла. Пакет `runtime-setup` для новой установки
содержит полные settings/installation по [основному интерфейсу](#интерфейсы).
Установка возвращает immutable `definition_path`; именно его заполните в reader admission
вместе с фактическим settings path до нового настоящего Start/prepare ресурса.
Не заменяйте authority внутри действующего ресурса и не переименовывайте profile/session
для обхода revision/admission. Рабочие настройки/привязки не копируются в source Git.

Native transport остаётся `poise hook --settings PATH --definition IMMUTABLE_PATH`,
один настоящий host event на stdin. Tool payload содержит `session_id`, `cwd`,
`hook_event_name`, `turn_id`, `tool_use_id`, `tool_name="Bash"`, `tool_input.command`;
Post дополнительно содержит `tool_response`. Метаданные определяются настоящим вызовом,
а не назначаются агентом ради допуска. SessionStart/End и проектная binding принадлежат
прежнему owner. Доказанный внешний `cd` может выбрать другой зарегистрированный checkout,
но native event checkout допускается независимо; произвольный ambient cwd не подставляется.

Pre возвращает `hookSpecificOutput` с `hookEventName="PreToolUse"`,
`permissionDecision=allow|deny` и ограниченной `permissionDecisionReason`. Post не выдаёт
разрешение задним числом: при ошибке учёта возвращает неблокирующий `systemMessage`.
Полный control JSON сохраняется отдельно, когда это удалось; краткий feedback и полные
machine bytes имеют разные бюджеты. Нет Task acquisition/advance из этих событий.
Семантика pending, пригодного результата, replay и recovery —
[у владельца чтений](../workflows/file-intake-lifecycle.md#ожидание-и-завершение-обычного-чтения).

Поставляемый adapter проверен установленным пакетом и изолированным SDK lifecycle.
Подтверждения доставки этой пары событий настоящим хостом эта проверка не даёт.
