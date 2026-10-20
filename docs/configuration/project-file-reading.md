# Чтение частей файлов выбранного проекта

## Назначение и явные входы

`content-read` возвращает выбранное содержимое UTF-8 файлов зарегистрированного checkout,
его фактические границы и состояние в контексте caller/native episode. Task и Sprint для вызова
не нужны. Инструмент поставляется версионированным пакетом `ai-poise`; рабочая политика,
реестры и настройки native owner принадлежат конкретной установке.

Требуется выбранная установка Python 3.15 (`>=3.15,<3.16`) с явно установленной версией пакета.
CLI получает обязательные абсолютные `--config`, `--checkout`, `--policy` и обязательный
`--format json|text`; аргументы выбора не повторяются. Config связывает проект с его
зарегистрированным checkout. Рабочая папка процесса не выбирает другой проект автоматически.

Пакет передаётся JSON на stdin, без оболочки Work `input/messages`. Например, после заполнения
реальных путей и при действующем native episode:

```sh
/path/to/selected/venv/bin/python -I -B -m poise content-read \
  --config /absolute/project/config.json \
  --checkout /absolute/project/checkout \
  --policy /absolute/operator/reader.json \
  --format json <<'JSON'
{"operation":"read","reads":[{"key":"intro","path":"docs/sample.txt","selection":{"kind":"lines","start":1,"end":10}}]}
JSON
```

Сохраняйте настоящую caller identity среды запуска. Generated persistent caller также проходит
общую границу идентичности, но сам binding не доказывает активный native episode. Admission
проверяет действующую связь у настроенного native owner. Не подставляйте session ID/события,
чтобы имитировать эту связь. См. [допуск и жизненный цикл](../workflows/file-intake-lifecycle.md#допуск-и-владение).

Это отдельный интерфейс от [специализированного reader](../workflows/source-reader.md#явные-диапазоны-и-поколения-контекста):
у него whole-file SHA и явные acknowledgements. Общий reader использует SHA выбранного текста,
а факт регистрации не доказывает доставку клиенту или память модели.

## Подготовка политики и примеры

Поставлены два полных обезличенных профиля:

- [project-file-reading.example.json](../../config/examples/project-file-reading.example.json) — общий явный вызов;
- [project-file-reading-session.example.json](../../config/examples/project-file-reading-session.example.json) — отдельно размещённые session-ресурсы того же API.

Скопируйте выбранный файл в рабочую область оператора вне source Git. Замените каждое `@ROOT@`
на свой абсолютный Linux root. Это маркер примера: runtime не раскрывает его как переменную.
Задайте `admission.hook_settings` и `admission.definition_path` по фактической установке
[существующего native owner](runtime-hooks.md#доставка-launcher-при-старте), а не по фиктивным
путям `definition-pending.json` из примера. Проверьте права на выбранные корни и свободный адрес
шаблона; затем выполните [подготовку и восстановление](../workflows/file-intake-lifecycle.md#подготовка-шаблона).

Числа в обоих файлах — явные значения примера, а не встроенные defaults: complete 4096 байтов/200
строк, bounded 4096/160, output 32768/512, SQLite busy timeout 40 ms, gate wait 30 s,
recovery/End budgets по 60 s, polling по 0.01 s. Выберите подходящие значения для своей нагрузки.
Оба профиля требуют одинаковую настоящую admission; session-профиль не разрешает пропуск Start.

## Справочник опций

Все следующие поля обязательны. Целые пределы исключают `bool`; длительности исключают `bool`,
NaN и бесконечность. Компонент пути непустой, не `.`/`..`, без разделителей и NUL.

| Поле | Допустимое значение | Назначение |
| --- | --- | --- |
| `schema` | строка, точно `poise-content-reader-policy-1` | Версия формата политики. |
| `version` | непустая строка | Версия выбранного профиля; входит в SHA исходных байтов. |
| `complete.max_bytes` | целое > 0 | Предел размера полного источника и полученного UTF-8 текста. |
| `complete.max_lines` | целое > 0 | Предел строк полного текста. |
| `bounded.max_bytes` | целое > 0 | Предел UTF-8 байтов выбранного диапазона. |
| `bounded.max_lines` | целое > 0 | Предел запрошенного количества строк диапазона. |
| `output.max_bytes` | целое > 0 | Предел полного представления JSON/text до регистрации. |
| `output.max_lines` | целое > 0 | Предел строк полного представления. |
| `registry.schema_version` | целое, точно `2` | Схема подготовленного SQLite-реестра. |
| `registry.busy_timeout_ms` | целое > 0 | Ожидание SQLite lock, миллисекунды. |
| `registry.filename` | один компонент пути | Имя реестра внутри собственного каталога. |
| `session.temporary_root` | абсолютный путь | Корень удаляемых ресурсов caller/episode. |
| `session.coordination_root` | абсолютный путь | Внешние gate и запись владельца. |
| `session.diagnostic_file` | абсолютный путь | Внешний журнал восстановления. |
| `session.registry_template` | абсолютный путь | Адрес пустого подготовленного шаблона. |
| `session.registry_directory` | один компонент пути | Каталог реестра внутри ресурса. |
| `session.response_directory` | один компонент пути | Каталог полных ответов чтения внутри ресурса. |
| `session.ownership_filename` | один компонент пути | Имя записи владельца. |
| `admission.hook_settings` | абсолютный путь | Фактические настройки существующего native owner. |
| `admission.definition_path` | абсолютный путь | Фактическое неизменяемое определение установленного hook. |
| `coordination.wait_seconds` | конечное число > 0 | Бюджет ожидания обычного gate, секунды. |
| `coordination.poll_seconds` | конечное число > 0 | Интервал повторной попытки gate, секунды. |
| `coordination.recovery_budget_seconds` | конечное число > 0 | Общий бюджет восстановления, секунды. |
| `coordination.host_event_budget_seconds` | конечное число > 0 | Бюджет наблюдения настоящего End, секунды. |
| `coordination.end_observation_poll_seconds` | конечное число > 0 | Интервал наблюдения End, секунды. |
| `storage.control_response_root` | абсолютный путь | Полные управляющие ответы и ошибки вне удаляемого ресурса. |

Неизвестные опции сохраняются в raw политике и игнорируются. Проверка обязательных известных
полей остаётся строгой. `policy_sha256` — SHA-256 исходных байтов файла целиком: даже изменение
форматирования или неизвестной опции меняет идентичность политики.

Coordination root, diagnostic file, registry template и control-response root должны находиться
вне temporary root; иначе `lock_resources_conflict`. Операционное размещение и сохраняемые данные
описаны в [размещении ресурсов](../workflows/file-intake-lifecycle.md#размещение-ресурсов).
`batch.max_input_bytes`, `batch.max_items`, `batch.file_mode` и `paths.response` берутся из
существующей конфигурации проекта; это не дополнительные reader-policy поля. Подготовка самого
проекта принадлежит [существующему владельцу](project-setup.md).

## Семь операций

| Операция | Полный пакет | Эффект |
| --- | --- | --- |
| prepare_template | `{"operation":"prepare_template"}` | Публикация нового пустого schema-2 шаблона по свободному адресу. |
| prepare | `{"operation":"prepare"}` | Допуск активного эпизода, подготовка своего ресурса и реестра. |
| read | См. полный пример выше | Выборки, budget preflight, регистрация и полный ответ. |
| context | `{"operation":"context"}` | Текущие generation и summary своего checkout. |
| advance_context | `{"operation":"advance_context","request_id":"manual-transition-1","reason":"manual"}` | Новый явный переход контекста или повтор сохранённого перехода. |
| recover | `{"operation":"recover"}` | Проверенное завершение/повтор очистки выбранного собственного эпизода. |
| adopt | `{"operation":"adopt"}` | Явное подтверждение уже существующего собственного активного ресурса. |

Управляющие пакеты содержат только показанные поля. `read.reads` — непустой список в пределах
`batch.max_items`; каждый item содержит ровно `key`, `path`, `selection`, ключи непустые и уникальные.
Неизвестные поля запроса отклоняются до регистрации. Для полного файла:

```json
{"operation":"read","reads":[{"key":"whole","path":"docs/sample.txt","selection":{"kind":"complete","start":null,"end":null}}]}
```

Новый `advance_context` поддерживает только `reason="manual"`; иная непустая причина даёт
`invalid_request`. `request_id` непустой. Повтор того же ID/причины возвращает поколение
сохранённого перехода. После более позднего перехода это может быть прежний номер в ответе
`advance_context`; живое состояние не откатывается, `context` покажет текущее поколение.
Повтор ID с иной причиной — `event_conflict`.

## Выборки и границы содержимого

Относительный путь разрешается от выбранного checkout, абсолютный также должен принадлежать ему.
Возвращаемый `path` — разрешённый относительный POSIX path. Поддержаны обычные UTF-8 файлы.
Для `lines` границы положительные целые, с 1, включительно; `start <= end`. Конец после EOF
сокращается до реально полученной строки и отражается в `range`, начало после EOF отклоняется.
`complete` требует обоих null и возвращает `range="complete"`; пустой полный файл допустим.

Полное чтение нормализует universal newlines в LF. Диапазон сохраняет исходные окончания строк,
включая CRLF. Тело без завершающего LF не получает добавленный LF. `hash` — SHA-256 выбранного
UTF-8 текста после соответствующей обработки, `content_bytes` — его UTF-8 байты,
`content_lines` — фактически полученные строки. Это не SHA всех исходных байтов файла.

## Контекст и регистрация чтения

Namespace определяется проектом, checkout, caller и native episode. Schema-2 SQLite хранит
метаданные поколений/кеша/событий; текст выбранного файла не кладётся в cache/event rows.
Полные сохранённые ответы содержат текст отдельно в собственном response directory.

| classification | Наблюдаемое условие |
| --- | --- |
| first_read | Для этой выборки в реестре ещё нет подходящего факта. |
| exact_repeat | Тот же путь, SHA, диапазон и reader_kind уже есть в текущем generation. |
| content_changed | Последний подходящий факт имеет иной SHA выбранного текста. |
| new_context_generation | Тот же текст/диапазон известен в прежнем generation. |
| new_range | Этот путь известен с другим диапазоном. |

Только exact current-generation repeat имеет `status="unchanged"`, `previous_read_available=true`
и `content_follows=false` без `content`. Остальные успешные items имеют `status="content"`,
`previous_read_available=false`, `content_follows=true` и полный `content`. При совпадении нескольких
условий применяется порядок: exact → changed → прежний generation → другой диапазон → first.

`summary` содержит `cache_entries`, `event_count`, `content_events`, `unchanged_events`,
`model_content_bytes`, `avoided_bytes` по этому namespace. Названия обозначают факты регистрации,
а не provider billing, tokens, подтверждённую доставку или содержимое памяти модели.
Переход контекста здесь явный. Для обычных Bash-чтений существует отдельно включаемый
[адаптер событий](#обычные-команды-чтения). Универсального перехвата инструментов и автоматического
reset при compaction этот интерфейс не выполняет.

## Полный JSON и text framing

JSON имеет ровно top-level поля `schema`, `status`, `operation`, `project`, `checkout`,
`policy_sha256`, `context`, `items`, `summary`, `details`, `error`, `response_path`.
`schema="poise-content-read-result-1"`; недоступные сведения имеют null. `context` содержит
`caller_key`, `checkout_key`, `generation`, `admission_mode="native_hook"`, `native_episode`.
`response_path`, когда сохранение удалось, указывает полный canonical JSON, а не усечённый preview.

Успешный item: `schema_version=1`, `key`, `status`, `classification`, `path`, `hash`, `range`,
`reader_kind="repository-content-read"`, `context_generation`, `previous_read_available`,
`content_bytes`, `content_lines`, `content_follows`; только content-item добавляет `content`.
Failed item содержит только `key`, `status="failed"`, `error`. Error содержит ровно
`code`, `key`, `field`, `message`, `action`. При item failures top-level `error=null`; пакетная
ошибка находится в top-level error. Mixed status — `partial`.

Для text-ответа чтения каждый item имеет canonical JSON header+LF с полями общего ответа,
кроме `items`, и полем `item` без `content`. За ним идут ровно `content_bytes` UTF-8 байтов,
только если `content_follows=true`. Failed/unchanged header имеет нулевое тело. После тела без LF
следующий header начинается немедленно: разделяющий LF не добавляется. Потребитель читает header
до LF и затем точное число байтов; он не ищет границу рамки по строкам тела. Все поля header
сохраняются, в том числе общие `details/error/response_path`. Управляющие ответы и ошибки пакета
без items — полный canonical JSON и в text-режиме.

## Лимиты и частичные эффекты

Whole-packet validation и полный preflight выполняются до регистрации чтений. Размер измеряется
для выбранного JSON/text представления с полными headers, UTF-8, счётчиками и текущим generation
в согласованном gate snapshot. Для exact repeat полное private тело учитывается консервативно
при допуске бюджета; публичная unchanged рамка всё равно не содержит тела.

`output_limit` не добавляет read cache/events. После допуска каждый успешный item регистрируется
своей транзакцией, поэтому поздний отказ другого элемента сохраняет реальные предыдущие эффекты.
Сначала сохраняется полный ответ, затем stdout. `result_save_failed`/`output_write` не отменяют
уже зарегистрированные чтения и не означают ack. При отказе записи stdout exit 1, stderr содержит
полный late error JSON; переданный prefix может уже находиться в stdout. При ошибке основного
сохранения ответ сохраняет `status="unconfirmed"`, `error.code="result_save_failed"` и exit 1.
Если контрольный ответ успешно сохранён во внешнем `storage.control_response_root`,
`response_path` указывает на этот существующий полный JSON ошибки. Если сохранить контрольный
ответ также не удалось, `response_path=null`. Контрольная диагностика не превращает исход
в успех и не означает rollback уже зарегистрированных чтений.

## Ошибки и коды завершения

Exit 0 — успешная операция; exit 1 — эксплуатационный отказ или mixed item result; exit 2 —
неподдержанный ввод/границы; exit 3 — отсутствующий/непригодный ресурс. Если ошибочны все items,
exit берётся у первого failed item; если часть успешна, exit 1. Поэтому status и error нужно
читать вместе с items и кодом, а не выводить из одного числа.

Top status: `ready` при успехе, `partial` при mixed items, `failed` при всех item failures или
обычном отказе, `rejected` при соответствующем packet validation exit 2. Explicit recovery stale
имеет `stale`, незавершённая очистка — `incomplete`; `result_save_failed` и неподтверждённая
публикация шаблона — `unconfirmed`. Подробности template/recovery — у
[владельца жизненного цикла](../workflows/file-intake-lifecycle.md#завершение-и-восстановление).

В таблице `@KEY@` в поле заменяется фактическим ключом item. Для пакетной ошибки key может быть null.

| Код | Поле | Exit одного ошибочного элемента/пакета | Сообщение | Действие |
| --- | --- | --- | --- | --- |
| `invalid_request` | `request` | 2 | Неверная структура пакета. | Передайте поддержанный пакет без лишних или повторных полей. |
| `invalid_selection` | `reads:@KEY@:selection` | 2 | Неверные границы чтения. | Укажите полный режим либо включительный положительный диапазон. |
| `invalid_path` | `reads:@KEY@:path` | 2 | Неверный путь файла. | Укажите непустой путь обычного файла выбранной копии. |
| `path_outside_project` | `reads:@KEY@:path` | 2 | Путь находится вне выбранной рабочей копии. | Выберите файл внутри зарегистрированного checkout. |
| `invalid_caller` | `caller` | 2 | Не подтверждён подлинный вызывающий. | Используйте действительную native identity либо явный допустимый binding. |
| `invalid_project` | `config` | 2 | Не удалось выбрать проект. | Укажите действительный manifest проекта. |
| `project_binding` | `checkout` | 2 | Рабочая копия не принадлежит выбранному проекту. | Выберите зарегистрированный checkout этого репозитория. |
| `policy_missing` | `policy` | 3 | Нужная политика отсутствует. | Подготовьте и явно выберите рабочую политику. |
| `invalid_policy` | `policy:@FIELD@` | 2 | Обязательное известное значение политики недопустимо. | Исправьте указанное поле политики и повторите допустимую операцию. |
| `source_missing` | `reads:@KEY@:path` | 3 | Файл отсутствует. | Восстановите файл или выберите доступный путь. |
| `source_unreadable` | `reads:@KEY@:path` | 3 | Файл недоступен для чтения. | Восстановите доступ к файлу и повторите этот элемент. |
| `source_not_file` | `reads:@KEY@:path` | 2 | Путь не является обычным файлом. | Выберите обычный UTF-8 файл. |
| `source_not_utf8` | `reads:@KEY@:path` | 2 | Выбранный файл не является допустимым UTF-8 текстом. | Выберите поддержанный текстовый файл. |
| `range_after_eof` | `reads:@KEY@:selection` | 2 | Начало диапазона находится после конца файла. | Выберите существующие строки файла. |
| `complete_limit` | `reads:@KEY@:selection` | 2 | Полное чтение превышает настроенный предел. | Запросите допустимый диапазон строк. |
| `bounded_limit` | `reads:@KEY@:selection` | 2 | Диапазон превышает настроенный предел. | Уменьшите выбранный диапазон. |
| `output_limit` | `output` | 2 | Полное представление превышает настроенный предел. | Уменьшите диапазон или число выбранных элементов. |
| `template_missing` | `session.registry_template` | 3 | Подготовленный шаблон отсутствует. | Подготовьте пустой шаблон публичной операцией prepare_template. |
| `template_invalid` | `session.registry_template` | 3 | Подготовленный шаблон не соответствует схеме. | Сохраните текущий файл и подготовьте шаблон по новому свободному адресу. |
| `template_exists` | `session.registry_template` | 2 | Адрес шаблона уже занят. | Выберите новый свободный адрес; существующий файл не заменяется. |
| `template_publication_unconfirmed` | `session.registry_template` | 1 | Долговечность публикации шаблона не подтверждена. | Сохраните текущие bytes и подготовьте новый шаблон по свободному адресу. |
| `registry_missing` | `registry` | 3 | Принадлежащий контексту реестр отсутствует. | Подготовьте ресурсы активного эпизода через их владельца. |
| `registry_schema` | `registry` | 3 | Версия подготовленного реестра не поддержана. | Сохраните реестр и восстановите его через подготовленный совместимый шаблон. |
| `registry_invalid` | `registry` | 3 | Подготовленный реестр повреждён либо имеет неверное устройство. | Сохраните данные и восстановите ресурс через его владельца. |
| `registry_read` | `registry` | 1 | Не удалось прочитать реестр. | Восстановите доступ и повторите допустимую операцию. |
| `registry_write` | `registry` | 1 | Не удалось зарегистрировать чтение. | Прочитайте сохранённый исход и восстановите доступ перед повтором. |
| `admission_unavailable` | `admission` | 1 | Native authority недоступна. | Восстановите выбранную native конфигурацию и повторите наблюдение. |
| `admission_dead` | `admission` | 1 | Native authority подтверждает окончание эпизода. | Завершите восстановление его временных ресурсов через recover. |
| `admission_uncertain` | `admission` | 1 | Активный native эпизод не подтверждён. | Сохраните данные и подтвердите действительную сессию. |
| `authority_mismatch` | `admission` | 1 | Выбрана другая native authority. | Верните конфигурацию действительного владельца ресурса. |
| `episode_mismatch` | `admission` | 1 | Наблюдаемый эпизод отличается от владельца ресурса. | Продолжите восстановление через действительного владельца эпизода. |
| `ownership_unknown` | `session` | 1 | Владение существующим каталогом не подтверждено. | Сохраните каталог; используйте допустимое явное принятие либо восстановление владельцем. |
| `event_conflict` | `request_id` | 2 | Идентичность доменного перехода уже использована с другим смыслом. | Прочитайте первоначальный исход или задайте другую действительную операцию. |
| `context_missing` | `context` | 3 | Подготовленный контекст отсутствует. | Подготовьте ресурсы подтверждённого активного эпизода. |
| `context_changed` | `context` | 1 | Контекст изменился до завершения допустимой операции. | Прочитайте актуальный контекст и сохранённые результаты элементов. |
| `output_write` | `output` | 1 | Не удалось полностью записать поток результата. | Прочитайте доступный сохранённый полный результат; регистрация не доказывает доставку. |
| `result_save_failed` | `response_path` | 1 | Полный результат не удалось подтвердить в хранилище. | Восстановите доступ и прочитайте известные сохранённые материалы без выдуманного успеха. |
| `cleanup_incomplete` | `session` | 1 | Очистка завершённого эпизода не закончена. | Прочитайте сохранённую причину и повторите допустимый recover. |
| `adoption_unconfirmed` | `session` | 1 | Принятие существующего ресурса не подтверждено. | Сохраните его и проверьте действительного владельца. |
| `lock_resources_conflict` | `policy:session` | 2 | Временные данные и управляющие ресурсы имеют несовместимое размещение. | Разделите удаляемый subtree, координацию и постоянную диагностику. |
| `template_preparation_failed` | `session.registry_template` | 1 | Не удалось подготовить шаблон до публикации. | Устраните причину сбоя и повторите подготовку по свободному адресу. |

## Проверка поставленных примеров

Оба профиля проверяет неизменённый
`tests/content_reader/test_documentation_example.py::test_shipped_reader_policy_example_is_executable`.
Проверка отдельно материализует каждый профиль, подставляет фактические пути изолированного SDK,
собирает offline wheel, устанавливает его не-editable и запускает CLI через Python `-I`.
Она проверяет template, prepare, первое/повторное чтение, advance/text framing, context и очистку
после настоящего изолированного End. Это не инструкция выдавать тестовые события live-сессии.

Объявленный aggregate `reader.documented-example.json` содержит оба имени/SHA профилей и оба
полных installed proofs с source commit/tree, wheel SHA/version/module path и 15 command receipts
каждый. Успешный link checker сам по себе не подтверждает выполнение примеров. Полные машинные
результаты сохраняются владельцем проверки, а не сокращаются до текста для агента.


## Обычные команды чтения

Явный `content-read read` и обычные поддержанные Bash-команды используют один ресурс эпизода,
поколение и реестр F04. Обычная команда читает файл сама; Poise перед выполнением проверяет
запрос, а после пригодного результата регистрирует его покрытие. Task/Sprint не требуются.
Оператор отдельно подключает `PreToolUse` и `PostToolUse`; установка только четырёх lifecycle
событий не включает этот учёт. Проверен поставляемый обработчик через изолированные CLI/SDK
и установленный пакет. Доставка этих событий настоящим хостом не наблюдалась; универсального
перехвата всех инструментов, автоматического compaction reset и acknowledgement здесь нет.

## Подключение учёта обычных команд

Четыре полных файла образуют один пример установки:

- [Политика команд](../../config/examples/command-intake-policy.example.json).
- [Профиль reader](../../config/examples/command-intake-reader.example.json).
- [Определение шести событий](../../config/examples/command-intake-hooks.example.json).
- [Настройки hook owner](../../config/examples/command-intake-settings.example.json).

Скопируйте их в рабочие операторские пути вне Git. Значения чисел — выбранные значения примера,
не встроенные defaults. Общие параметры reader описаны [выше](#справочник-опций), настройки
установки — [у hook owner](runtime-hooks.md#настройки-установки). Сохраните соответствие:
`settings.tool_intake.reader_policy` → полный reader profile → `command_intake.policy` →
полная command policy. Обе новые ссылки требуют абсолютных путей. `settings.project_config`
остаётся относительным путём внутри `settings.root`: пример использует `project.json`.

| Маркер примера | Чем заменить |
| --- | --- |
| `@ROOT@` | Существующий абсолютный корень установки/данных; оператор проверяет все относительные пути settings. |
| `@PYTHON@` | Абсолютный Python выбранной установленной версии, сейчас 3.15. |
| `@SOURCE_ROOT@` | Абсолютный корень установленного пакета, содержащий `poise/__main__.py`; не checkout читаемого проекта. |
| `@CHECKOUT@` | Зарегистрированный абсолютный checkout, разрешённый `cwd_roots`. |
| `@READER_POLICY@` | Абсолютный адрес рабочей копии reader profile. |
| `@COMMAND_POLICY@` | Абсолютный адрес рабочей копии command policy. |

Это явные текстовые маркеры; runtime их не раскрывает. Аргумент CLI `--config` задаёт фактический
абсолютный manifest, соответствующий относительному `settings.project_config`; условное
`/absolute/project/config.json` в командах ниже нужно заменить на этот адрес.

1. Установите явно выбранную версию пакета и заполните policy/profile/settings/definition.
   Укажите в профиле фактический `admission.hook_settings`; исходный `definition-pending.json`
   является только временным адресом до установки. Проверьте root, project association,
   свободный template path и разделение temporary/control ресурсов.
2. До нового настоящего Start установите полное определение шести событий через
   [существующий hook owner](runtime-hooks.md#подключение-событий-чтения). Для существующего
   native файла получите действительный revision, затем передайте его как `expected_revision`.
   Сохраните фактические ID установки/профиля существующего владельца: другое имя не присваивает
   его группы. Не устанавливайте второй конкурирующий набор поверх первого.
3. Запишите возвращённый неизменяемый `definition_path` в `admission.definition_path` профиля;
   он и hook settings должны совпадать с фактической authority. Подготовьте пустой шаблон
   операцией `prepare_template`. Эта операция не требует активного эпизода.
4. После настоящего SessionStart того же установленного определения выполните `prepare`.
   Затем используйте обычное чтение и `coverage`. Сначала полностью подготовьте authority;
   замена определения после Start/создания ресурса не является активацией существующего эпизода.

Оператор устанавливает адаптер; агент сохраняет действительную caller identity и получает
нативные события от хоста. Он не фабрикует Start/End или tool IDs для допуска. Схемы событий
и порядок восстановления принадлежат [жизненному циклу](../workflows/file-intake-lifecycle.md#ожидание-и-завершение-обычного-чтения).

## Параметры политики обычных команд

`command_intake.policy` у reader profile указывает полный JSON нижеуказанной схемы.
Все известные leaves обязательны; неизвестные сохраняются в raw и игнорируются.
SHA политики вычисляется по исходным UTF-8 bytes, включая форматирование/неизвестные значения.
Целые положительны и исключают `bool`, если не указан иной диапазон. Списки строк содержат
уникальные непустые строки; parser допускает пустой список readers, а stream/exit lists непусты.
Длительность — положительное конечное число, исключая `bool`, NaN и бесконечность.

| Поле | Тип, допустимое значение и фактическое назначение |
| --- | --- |
| `schema` | Точно `poise-command-intake-policy-1`. |
| `version` | Непустая строка; пример `command-intake-1`. |
| `classifier.mode` | `audit` или `enforce`: применение нарушения разбора/объёма. |
| `classifier.complete_reads.max_files_per_call` | Целое >0; число полных/смешанных входов, допускаемое основной веткой, пример 1. Это не безусловный byte cap. |
| `classifier.complete_reads.small_bundle_max_files` | Целое >0; условный предел числа нескольких полных файлов, пример 3. |
| `classifier.complete_reads.small_bundle_max_bytes` | Целое >0; суммарные bytes small bundle, пример 200. |
| `classifier.complete_reads.small_bundle_max_lines` | Целое >0; суммарные строки small bundle, пример 40. Small bundle требует доказанных размеров всех полных входов и отсутствия bounded входов. |
| `classifier.bounded_reads.max_files_per_call` | Целое >0; число различных файлов ограниченного чтения, пример 4. |
| `classifier.bounded_reads.max_lines_per_file` | Целое >0; предел выдаваемых строк вклада reader, пример 200. |
| `classifier.bounded_reads.max_total_lines` | Целое >0; суммарный предел выдаваемых строк, пример 400. |
| `classifier.bounded_reads.max_bytes_per_file` | Целое >0; доказанный предел bytes вклада reader, пример 4096. |
| `classifier.bounded_reads.max_total_bytes` | Целое >0; суммарный доказанный byte предел, пример 8192. Неизвестная проекция не становится точным числом. |
| `classifier.unknown_expansion.deny_globs` | Bool; запрет неизвестного набора файлов glob/brace expansion. |
| `classifier.unknown_expansion.deny_reader_loops` | Bool; запрет циклов с reader. |
| `classifier.unknown_expansion.deny_find_exec_readers` | Bool; запрет reader через `find -exec`. |
| `classifier.unknown_expansion.deny_xargs_readers` | Bool; запрет reader через `xargs`. |
| `classifier.unknown_expansion.deny_nested_shell_readers` | Bool; запрет чтения вложенной shell; `false` разрешает разбор в пределах глубины. |
| `classifier.unknown_expansion.deny_obvious_interpreter_bypass` | Bool; запрет совпавшего content-read pattern интерпретатора. |
| `classifier.unknown_expansion.deny_command_substitutions` | Bool; запрет подстановок команд с reader. |
| `classifier.unknown_expansion.deny_eval_readers` | Bool; запрет `eval` с reader. |
| `classifier.unknown_expansion.unclassified_behavior` | `allow`, `deny`, `allow_with_audit`; неразобранное чтение. `allow` и `allow_with_audit` текущий classifier возвращает как audit. |
| `classifier.readers.complete` | Список имён; пример `bat`, `batcat`, `cat`, `nl`, `tac`. |
| `classifier.readers.range_capable` | Список имён; пример `head`, `tail`, `sed`. |
| `classifier.readers.conservative` | Список имён; пример `awk`, `perl`. |
| `classifier.readers.nested_shells` | Список имён; пример `bash`, `dash`, `ksh`, `sh`, `zsh`. |
| `classifier.readers.interpreter_bypass_patterns` | Список компилируемых regex content-read patterns. |
| `classifier.readers.interpreter_metadata_patterns` | Список компилируемых regex metadata patterns. |
| `classifier.parser.max_nested_shell_depth` | Целое >0, пример 2; предел рекурсивного разбора shell. |
| `classifier.parser.max_reader_invocations_per_call` | Целое >0, пример 4; число reader-вызовов. |
| `classifier.parser.max_reader_arguments_per_invocation` | Целое >0, пример 12; число reader-аргументов. |
| `registry.mode` | `audit` или `enforce`: применение решения повторного чтения. |
| `registry.versioning.hash_max_bytes` | Целое >0, пример 1024; SHA всего файла до этого размера включительно; выше — size/mtime_ns. |
| `registry.same_generation.deny_exact_repeats` | Bool; exact complete/operation repeat текущего поколения. |
| `registry.same_generation.deny_fully_covered_ranges` | Bool; запрет полностью покрытого диапазона текущего поколения. |
| `registry.same_generation.min_new_lines` | Целое >0, пример 8; порог новых строк при частичном пересечении. |
| `registry.same_generation.min_new_fraction_percent` | Целое 0..100, пример 20; порог доли новых строк. Mostly-redundant решение возникает, когда новые строки меньше обоих порогов. |
| `registry.post_generation.allow_targeted_reread` | Bool; разрешение повторного targeted диапазона из прежних поколений. |
| `registry.post_generation.deny_complete_reread` | Bool; запрет complete reread ранее полностью учтённого материала. |
| `registry.post_generation.max_lines_per_file` | Целое >0, пример 4; число всех строк повторного targeted запроса, а не только пересечение. |
| `registry.post_generation.max_total_lines` | Целое >0, пример 8; суммарный предел таких повторных targeted запросов в вызове. |
| `registry.post_generation.unknown_repeat_behavior` | `deny`, `allow`, `allow_with_audit`; повтор operation с неизвестным диапазоном после смены поколения. |
| `registry.post_result.minimum_response_bytes` | Целое >0, пример 1; минимум UTF-8 bytes пригодного ответа. |
| `registry.post_result.unknown_exit_code_is_success` | Bool; пригодность достаточного текста при отсутствии известного exit code. |
| `registry.post_result.output_field` | Непустая строка, пример `output`; текст этого поля имеет приоритет. |
| `registry.post_result.stream_fields` | Непустой список строк, пример `stdout`, `stderr`; при отсутствии output строки соединяются в этом порядке. Нестроковое значение ошибочно. |
| `registry.post_result.exit_fields` | Непустой список строк, пример `exit_code`, `exitCode`; значения — согласованные целые, исключая bool. |
| `registry.retention.max_pending_age_seconds` | Положительное конечное число секунд, пример 300; допустимый возраст pending по сравнимому Clock. |
| `context.max_documents` | Целое >0, пример 20; число видимых documents. |
| `context.max_ranges_per_document` | Целое >0, пример 20; число видимых ranges каждого документа. |
| `context.max_pending_records` | Целое >0, пример 20; число видимых pending records. |
| `context.max_bytes` | Целое >0, пример 16000; предел canonical UTF-8 bytes объекта coverage details. |
| `feedback.max_bytes` | Целое >0, пример 1800; предел краткой native reason/action строки, дополнительно ограниченный event.context_limit. |
| `feedback.max_lines` | Целое >0, пример 20; предел строк краткой native строки, также ограниченный event.context_limit. |
| `audit.record_allowed_readers` | Bool; сейчас валидируется и сохраняется в policy snapshot. Отдельной ветки записи аудита по этому флагу runtime не реализует. |

Parser проверяет `max_lines_per_file <= max_total_lines` у bounded/post-generation и
`max_bytes_per_file <= max_total_bytes` у bounded. Другие предполагаемые соотношения
не заменяют явную политику. `classifier.mode` и `registry.mode` независимы: audit разрешает
соответствующее нарушение с причиной, enforce запрещает его. Ошибка допуска/настроек/реестра
не превращается в разрешение от выбора audit.

## Поддержанные запросы и рабочий каталог

Поддержан конфигурируемый инвентарь readers, явные полные входы, ограниченные `head`/`tail`,
числовые диапазоны `sed`, консервативно распознаваемые `awk`/`perl`, конвейеры полного reader с
поддержанным фильтром. Для `awk`/`perl` определяется полное чтение либо неизвестная выборка,
учитываемая как операция без точных диапазонов строк. Это разбор объявленной грамматики,
не выполнение произвольной shell.
Пути вне выбранного checkout не создают покрытие; stdin-only reader не читает файл проекта.
Discovery/metadata не означает доставку содержимого и не создаёт выдуманный диапазон.

Для относительных reader paths требуется доказанный внешний префикс:

```sh
cd -- /absolute/registered/checkout && sed -n '3,4p' docs/sample.txt
```

Литерал абсолютного пути и успешный `&&` задают base. Сам event `cwd` допускает native checkout,
но не доказывает каталог произвольного shell reader. Последующий `cd`, а также первый `cd`
дочерней/внучатой shell разрушает inherited relative base: `unresolved_execution_cwd` следует
classifier mode без ложных ROOT ranges. Абсолютный operand имеет самостоятельную базу;
здоровые вложенные команды разбираются, когда это явно разрешено policy и глубиной.

`sed` по умолчанию адресует конкатенированный поток нескольких входов; `-s` адресует каждый
отдельно. Переданный диапазон не копируется на каждый файл. Для `tac | head/tail/sed` проекция
возвращается к исходным source lines, когда наблюдение доказывает EOF и последний разделитель.
Файл с bytes `A\nB` выдаёт через `tac | head -n 1` bytes `BA\n`: 3 bytes, одна выходная строка,
покрытие исходных строк 1–2. У terminated `A\nB\n` тот же фильтр выдаёт `B\n`, покрытие строки 2.
Повторная выдача строки увеличивает byte/line объём; ledger объединяет диапазоны.
Если ограниченное наблюдение не доказывает преобразование, `unresolved_source_projection`
применяет настроенный unclassified путь; неизвестный record boundary не считается newline.
Byte-bounded/неизвестные диапазоны учитываются как operation, не точные строки.
Неподдержанные transform flags/произвольные программы не получают обещания точной проекции.

## Сводка покрытия

Включённый command profile добавляет к семи базовым операциям восьмую, без новых CLI-аргументов:

```sh
/path/to/selected/venv/bin/python -I -B -m poise content-read \
  --config /absolute/project/config.json \
  --checkout /absolute/registered/checkout \
  --policy /absolute/operator/command-reader.json \
  --format json <<'JSON'
{"operation":"coverage"}
JSON
```

Python JSON-порт `poise.interfaces.content_reader.execute` получает те же config/checkout/policy,
бинарный stdin, текстовые stdout/stderr и явный Clock. Запрос содержит только `operation`.
Результат имеет прежний `poise-content-read-result-1`, `items=[]`, context своего checkout и
`details.schema="poise-read-coverage-1"`; обычный context/read summary остаётся отдельным.

| Поле details | Значение |
| --- | --- |
| `documents` | Текущие версии/поколение: `path`, `version_key`, `complete`, объединённые `ranges`, `operation_count`, `last_generation`. |
| `pending` | Ожидающие результаты: `tool_use_id`, `pre_turn_id`, `generation`. Истечение возраста проверяется при Post, а не таймером сводки. |
| `counts` | Полные `current_documents`, `stale_documents`, `pending`, `settled` до усечения списков. Stale — изменившиеся известные версии, не их source text. |
| `omitted` | Число опущенных `documents`, `ranges`, `pending`, а не число несуществующих фактов. |

Сначала ограничиваются documents/pending/ranges соответствующими count limits. Если canonical
`details` ещё больше `context.max_bytes`, удаляются pending с конца, затем documents с конца;
учитываются omissions. Если даже пустые списки с counters не помещаются —
`coverage_budget_too_small`. Этот byte limit относится к details; полный envelope/control не объявляется ограниченным
этим пределом или бюджетом content-read read.
Сводка передаёт только метаданные учёта; содержимое, память модели, ACK и billing не доказывает.

## Решения и восстановление учёта команд

Полный управляющий файл имеет `schema="poise-tool-intake-result-1"`, `phase=pre|post`, `status`,
`context`, `call`, `decision`, `counts`, `error`, `response_path`. `call` различает tool_use_id,
tool_name, pre_turn_id и post_turn_id. `decision` содержит action, reason_code, message,
action_hint. Сохранённые machine JSON не обрезаются лимитами feedback; краткая native строка
указывает причину, действие и путь полного ответа, когда его публикация подтверждена.

| Условие | Наблюдаемый исход и продолжение |
| --- | --- |
| Допустимый Pre с известными планами | `pending`/allow либо audit; покрытие ещё не добавлено. Выполнить разрешённое чтение, получить настоящий Post. |
| Полный/точный/покрытый/преимущественный повтор | `duplicate_complete_document`, `duplicate_document_operation`, `duplicate_document_range`, `mostly_redundant_document_range`; запросить coverage, выбрать нужные новые диапазоны. |
| Новый generation | `complete_reread_after_generation`, `post_generation_reread_disabled`, `post_generation_file_budget_exceeded`, `post_generation_total_budget_exceeded`, `unbounded_reread_after_generation` по соответствующей policy. Targeted reread в пределах разрешения/обоих бюджетов допустим. |
| Некорректный/неполный вызов, конфликт ID/input/turn | `invalid_event` или `call_conflict`; сохранить реальный ID и исправить источник метаданных, не выдумывать другой session. |
| Нет либо неверны ссылки/поля policy | `policy_missing`/`invalid_policy`, конкретный field; исправить рабочую конфигурацию у владельца. |
| Несовпадающая installed authority/checkout/episode | `authority_mismatch` либо существующая admission диагностика; восстановить соответствие выбранному настоящему owner. |
| Недоступный/повреждённый реестр или cached control/path | `registry_invalid` либо инфраструктурная ошибка; Pre deny, Post unconfirmed/warning. Сохранить bytes, диагностировать owner, не сбрасывать данные. |
| Пригодный Post | `settled`, reason `usable_result`; зарегистрированы только ещё действительные планы. |
| Известная ошибка, слишком малый/неверный ответ, неизвестный exit по policy | `discarded`: `known_failure`, `insufficient_response`, `invalid_response`, `unknown_exit_code`; ожидание снято, coverage не выдумывается. |
| Просроченный pending, другое поколение/версия | `pending_expired`, `generation_changed`; версия проверяется для каждого плана, изменившиеся пропускаются. При несравнимом Clock — `incomparable_clock`, без выдуманного времени/учёта. |
| Post без Pre или другое завершение уже settled ID | `unmatched_call` либо `call_conflict`; Post не блокирует уже выполненную команду. Повтор идентичного завершения возвращает сохранённый control. |
| Ошибка публикации полного control | `result_save_failed`, response_path может быть null; учитывать фактические частичные эффекты, прочитать сохранённые diagnostics. |

Проверка версии использует SHA маленького файла либо size/mtime большого; это ограниченная
идентичность, не доказательство неизменности всех больших bytes. Переход generation выполняет
прежний явный `advance_context(reason="manual")`; повтор старого request_id не откатывает живое
поколение. Повтор доставки Pre/Post идемпотентен по действительному call и fingerprint; изменённое
завершение конфликтует. `eligible`, `skipped`, `registered` не обязаны складываться как partition:
eligible считает пригодные планы, registered — уникальные `(path, operation_digest)`.

Pre отказ означает решение до выполнения. Ошибка Post сообщает о неподтверждённом учёте уже
исполненного чтения; тело команды не отзывается. End/recover выполняет прежний
[владелец ресурса](../workflows/file-intake-lifecycle.md#завершение-и-восстановление).
Удаление lock/owner/БД и синтетический End не являются способом восстановления.

Все четыре command примера исполняет неизменённый
`tests/command_intake/test_documented_examples.py::test_actual_command_intake_examples`:
offline wheel, noneditable install, `-I` CLI/SDK, isolated Start/End, direct/ordinary read,
pending/settled coverage, recovery и полные source/wheel/command receipts. Это изолированный
consumer; его события не являются доказательством доставки live-хостом. Generic Python probes
проверяются по фактическим определениям выбранным интерпретатором без установки live hooks.
