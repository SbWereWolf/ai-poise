# Наблюдения, измерения и телеметрия Poise

## Назначение и окружение

`apps/poise/bin/poise --config PATH` предоставляет самостоятельные наблюдения
`observations.prepare/record/read`, измерение `content.measure` и необязательную
телеметрию `telemetry.invoke/status/replay`. Используйте CPython 3.13 со стандартной
библиотекой. Автоматический сбор телеметрии рассчитан на Linux с `fork`, `/proc`
и локальной файловой системой; проверенное окружение включает Ubuntu в WSL.
Обычные наблюдения используют собственное SQLite-хранилище версии `1`.

Общий вход, caller и Session описаны в [договоре оболочки](shell-poi.md).
Передавайте один пакет UTF-8 с полями `schema: "stride-poise/v1"`, `operation`,
`input` на stdin и EOF. Ответ содержит `schema`, `status`, `result`, `error`,
`evidence`; успешный вызов имеет exit `0`, `rejected` — `2`, `unavailable` — `1`.
Стандартные ошибки JSON и конфигурации сохраняют договор оболочки.

Наблюдения сохраняют факты и происхождение; их полнота не является решением
об успешности Task. Для работы этих операций достаточно собственных настроек
и данных Poise. Действующий [договор вывода](poi-output-contract.md) сохраняет
доступ к метаданным и исходным байтам независимо от необязательной телеметрии.

## Настройка

Возьмите [базовую конфигурацию](../config/poise.example.json). Общие шесть полей
оболочки обязательны. Добавьте только используемые разделы `observations`,
`measurement`, `telemetry`; их полные значения приведены в самостоятельном примере.
Рабочий конфиг принадлежит оператору и хранится вне исходников приложения.

| Раздел и поле | Значение и единицы |
| --- | --- |
| `observations.schema` | `poise-observations-config/v1` |
| `observations.database` | Путь к собственному SQLite; относительный путь считается от каталога конфига |
| `observations.file_mode` | Целое от `0` до `511`, режим файла SQLite; `384` соответствует `0600` |
| `observations.sqlite_timeout_seconds` | Конечное число от нуля, ожидание записи и явной подготовки; чтение использует нулевое ожидание |
| `observations.sources` | Непустой объект: имя источника → `reported` или `observed`; для автоматических операций задайте `"poise": "observed"` |
| `observations.limits` | Положительные целые `max_events`, `max_event_bytes`, `max_results`, `max_read_response_bytes` |
| `measurement.schema` | `poise-measurement-config/v1` |
| `measurement.max_content_bytes` | Положительный предел суммы UTF-8 байтов входных `before` и `after` |
| `measurement.max_response_bytes` | Положительный предел сериализованного ответа измерения |
| `measurement.tokenizer` | Все три поля `kind`, `identity`, `command`; идентичность — непустое имя фактического измерителя |
| `telemetry.schema` | `poise-telemetry-config/v1` |
| `telemetry.mode` | `best_effort` или `durable` |
| `telemetry.max_pending` | Положительный предел незавершённых capture/квитанций в одном экземпляре диспетчера |
| `telemetry.max_event_bytes` | Положительный предел метаданных capture и целого события в UTF-8 байтах |
| `telemetry.spool` | `null` для `best_effort`, полная политика сохраняемой очереди для `durable` |

Для `telemetry.spool` обязательны `directory`, `file_mode`, `max_entries`,
`max_bytes`, `max_attempts`, `batch_items`, `retry_seconds`, `lock_seconds`,
`lock_poll_seconds`. Каталог разрешается относительно конфига; режим файла имеет
тот же диапазон, что выше. Счётчики положительные, `max_attempts <= 1000000`,
`batch_items <= max_entries`. Времена — конечные положительные секунды.
`max_bytes >= telemetry.max_event_bytes + 2048`; резерв `2048` на запись
участвует в бюджете приёма. Фактический размер файлов отдельно виден в статусе.

`tokenizer.kind="unavailable"` с `command=null` явно сохраняет неизвестные токены.
Для своего настроенного измерителя выберите `kind="command"` и задайте все поля
`command`: `argv` (непустой массив), абсолютный `cwd`, явный объект `env`,
положительные `timeout_seconds` и `max_output_bytes`. Команда получает одну строку
JSON `{"texts":["добавленный текст","удалённый текст"]}` и возвращает
`{"counts":[число_добавленных_токенов,число_удалённых_токенов]}` с двумя целыми
неотрицательными значениями и exit `0`. Это протокол выбранного вами измерителя;
приложение не подменяет его оценкой по длине строки. Предел вывода учитывает stdout
и stderr вместе. Для приведённого ниже примера отдельный измеритель не нужен.

## Подготовка и запись

Сначала явно подготовьте собственное хранилище:

```json
{"schema":"stride-poise/v1","operation":"observations.prepare","input":{}}
```

Результат: `schema="poise-observation-state/v1"`, `database`, `version=1`,
`created`. Повтор сохраняет существующие данные, проверяет выбранную версию
и установку и применяет настроенный режим файла. При подготовке новой схемы
используется WAL. Приложение не изменяет общую конфигурацию.

Для записи заранее подготовьте [состояние Session](state-poi.md) и выполните
`session.ensure` в своём контексте. `observations.record` принимает
`input={"events":[событие,...]}`. Событие имеет ровно `source`, `stream`,
`event_id`, `observed_at`, `kind`, `value`. Имена источника, потока и события —
непустые строки; источник объявлен в конфиге. `observed_at` содержит время источника
с часовым смещением. Ключ события — четвёрка `(kind, source, stream, event_id)`.

Пакет записывается одной транзакцией собственного хранилища. Точный повтор
возвращает прежние `seq`, принадлежность Session, вклад и длительность;
одинаковое событие внутри пакета также учитывается один раз. Для закрытой Session
доступен точный повтор существующих фактов. Для новых событий используйте открытую
Session. Общий поток источника сохраняет порядок независимо от Session;
принадлежность отдельного события сохраняется при первой записи.

Результат `poise-observations-record/v1` содержит `count` и `entries` в порядке
входа. Каждая запись имеет `index`, `seq`, `kind`, `source`, `stream`, `event_id`,
`session_id`, `replayed`, `contribution`, `duration_ns`. Индексы начинаются с нуля.
Для нового события `replayed=false`, для точного повтора — `true`.
Виды событий и значения описаны далее; `source` принимает
`value={"parameters":{...}}` для явно переданных сведений об источнике.

## Счётчики и повторы

Для `kind="usage"` значение содержит `sequence`, `mode`, `counters`.
`sequence` — целое от нуля; новые отсчёты одного источника/потока имеют строго
возрастающий номер и неубывающее время источника. Счётчики:
`input_tokens`, `output_tokens`, `total_tokens`, `cached_input_tokens`,
`reasoning_tokens`. Первые три — целые от нуля, сумма входных и выходных равна
`total_tokens`. Последние два — целое от нуля либо `null`; известные детализации
не превышают соответственно входные и выходные токены.

| Режим | Фактический вклад |
| --- | --- |
| `delta` | Переданная порция; такой поток продолжайте порциями `delta` |
| `baseline` | Первый накопительный отсчёт; известные значения дают нулевой вклад |
| `cumulative` | Разность с предыдущим `baseline` или `cumulative` того же потока |

Накопительные значения и их приращения соблюдают смысл счётчиков. Для нового
исходного отсчёта выбирайте новый поток. Если детализация текущего или предыдущего
накопительного значения неизвестна, её вклад остаётся `null`. Переданный ноль
остаётся известным нулём. Точный повтор сохраняет уже вычисленный вклад, даже
если в потоке после него появились новые отсчёты.

## Измерение времени

`kind="timing"` принимает `value={"started":наблюдение,"finished":наблюдение}`.
Каждое наблюдение — `null` либо объект с тремя полями: `audit_utc` (UTC-время),
`monotonic_ns` (целое от нуля), `comparison_domain` (непустое имя области сравнения).
Когда обе точки принадлежат одной области и монотонное значение конца не меньше
начала, `duration_ns` равен их разности. Иначе он `null`. Известный ноль сохраняется.

`kind="interval"` принимает `value={"started_at":строка,"ended_at":строка}`
со временем источника и явным часовым смещением; конец не раньше начала.
Интервалы одного источника/потока не перекрываются; смежные интервалы допустимы.
Исходные строки, включая смещения, сохраняются. Расчёт сравнения и длительности
ведётся в целых наносекундах; задавайте время с точностью, представимой целыми
наносекундами. Дробь до девяти разрядов после секунд поддержана;
дополнительные завершающие нули сохраняют то же значение. Смещения учитываются
при сравнении, а исходное представление отдельно доступно в событии.

Автоматические события `kind="operation"` создаёт только сама телеметрия
источника `poise`, потока `operations`: имя основной операции, статус, exit-код
и две точки времени. Clock использует UTC для аудита и `monotonic_ns` с областью
загрузки Linux для сравнения. Обе точки наблюдаются в отдельном работнике;
это время наблюдателя, а не обещание точных границ исполнения основной операции.

## Чтение и полнота

```json
{"schema":"stride-poise/v1","operation":"observations.read","input":{"scope":{"kind":"installation"},"source":null,"after":0,"limit":20}}
```

`scope.kind` выбирает `installation` либо `session`. Для первого читается только
собственное хранилище наблюдений; подготовка Session/caller binding не требуется.
Второй использует фактическую текущую Session. `source=null` выбирает все сохранённые
источники, строка — один источник. `after` — целое от нуля, `limit` — положительное
целое в пределах `max_results`.

`poise-observations-read/v1` содержит `scope`, `source`, `as_of_seq`, `events`,
`observed_events`, `usage_events`, `totals`, `next_after`, `has_more`,
`availability`, `coverage`. Снимок фиксируется одной транзакцией чтения;
`as_of_seq` — максимальный номер в собственном хранилище на этом снимке.
Каждый элемент `events` имеет `seq`, `session_id`, `source_mode`, исходный `event`,
`contribution`, `duration_ns`. Страница выбирает номера больше `after`.
Для следующей страницы передайте `next_after`; `has_more` показывает продолжение.
Каждый новый вызов получает свой снимок.

Счётчики `observed_events`/`usage_events` и `totals` относятся ко всему выбранному
scope/источнику на снимке, а не только странице. В `totals` суммируется понесённый
расход; исходный `baseline` исключён. При отсутствии расходных событий или при
неизвестном вкладе выбранного счётчика его итог равен `null`. Покрытие чтения
остаётся `partial`: наличие сохранённых событий не доказывает полноту источника.

Успешное чтение имеет `availability="observed"`. При недоступном хранилище
возвращается `unavailable`/exit `1` с полезным `result`: прежний результат того же
запроса/Session может иметь `availability="cached"`, `coverage="partial"`.
Это один последний ответ в памяти того же экземпляра сервиса, не межпроцессный
кэш командной строки. Без такого ответа значения событий/итогов неизвестны,
`availability="unavailable"`, `coverage="unavailable"`. Ошибки входа и пределы
ответа сохраняют собственную диагностику; они не превращаются в cached-успех.

## Измерение содержимого

```json
{"schema":"stride-poise/v1","operation":"content.measure","input":{"before":"A\n","after":"A\nёж\r\n"}}
```

Session, caller binding и хранилище наблюдений для этой операции не нужны.
Строки сравниваются с сохранением окончаний строк, `autojunk` отключён.
`poise-content-measure/v1` возвращает `added_lines`, `removed_lines`,
`changed_lines`, `added_bytes`, `removed_bytes`, `changed_bytes`,
`added_tokens`, `removed_tokens`, `changed_tokens`, `tokenizer`, `coverage`.
Суммарные изменения равны сумме добавленного и удалённого. Байты считаются
в UTF-8; замена окончания строки также является изменением строки.

Для примера выше добавлена одна строка и шесть байтов, ничего не удалено.
С явно недоступным tokenizer результат успешен, `coverage="partial"`,
`tokenizer={"identity":настроенное_имя,"available":false}`, токены — `null`.
Настроенный работающий измеритель даёт реальные токены и `coverage="complete"`.
Если его процесс недоступен, превышает время/вывод или не возвращает результат
своего протокола, ответ имеет `measurement_unavailable`, exit `1`;
уже измеренные строки/байты сохраняются, токены неизвестны. Лимиты входа и ответа
проверяются независимо. Это измерение переданных текстов, не оценка полезности Task.

## Наблюдение основной операции

`telemetry.invoke` принимает ровно `input={"operation":имя,"input":данные}`.
Имя выбирается из действующих Session, hooks, reader, файловых, командных,
`output.minimal/range`, `observations.record/read`, `content.measure` операций
оболочки. Подготовка наблюдений, сами операции `telemetry.*` и `poise-generic`
имеют отдельные входы; выполняйте их непосредственно по их договору.

Основная операция вызывается один раз. Внешний `result` имеет
`schema="poise-observed-invoke/v1"`, `primary` и `telemetry`.
`primary` — весь исходный пятикомпонентный ответ операции, включая результат,
ошибку и evidence. Внешние статус, ошибка и exit-код сохраняют её исход;
внешнее `evidence=[]`, а доказательства основной операции доступны в `primary`.
Исходные файлы и байты чтения принадлежат своим прежним владельцам.

`telemetry` содержит `state`, `event_id`, `coverage`, `stats`, `error`.
`state="admitted"` означает только передачу финальных метаданных работнику;
это не квитанция записи в SQLite. `dropped` означает отсутствие такого приёма,
`unavailable` — известную недоступность настройки/приёмника телеметрии.
Идентификатор автоматического события создаётся для этого вызова; он не является
идентичностью повтора основной команды. `stats` содержит `submitted`, `processed`,
`duplicates`, `failed`, `dropped`, `pending`; это локальные наблюдения диспетчера,
не полная история доставки между процессами.

Автоматическое сохранение, Clock и доставка вынесены в отдельный процесс;
готовый основной результат не ждёт завершения необязательного получателя,
занятого хранилища или доставки. Финальные метаданные передаются одной
неблокирующей записью в ограниченный pipe; неполная передача означает потерю
наблюдения. Запуск/завершение короткого промежуточного процесса остаётся обычной
частью запуска capture, не ожиданием завершения работника.

При успешной настройке `coverage="partial"`, при известной недоступности —
`"unavailable"`. Неизвестные будущие итоги работника не меняют уже выданный статус.
Session наблюдения — реально наблюдённая основной операцией Session, либо `null`,
если операция её не использует. Для native hook учитывается Session входного
события, в том числе сохранённого повтора; она может отличаться от Session
родительского вызывающего. Телеметрия не создаёт Session ради заполнения поля.

## Доставка и повтор

В `best_effort` работник пробует сохранить событие сразу в собственное хранилище;
постоянная очередь отсутствует. `telemetry.status` с `input={}` возвращает
`schema="poise-telemetry-status/v1"`, `mode`, `coverage="partial"`, `spool=null`.

В `durable` работник сначала сохраняет событие в свою очередь, затем делает
ограниченную попытку доставки. Статус содержит объект `spool` с полями
`pending`, `exhausted`, `corrupt`, `failed_attempts`, `bytes`, `records`.
Элемент `records` содержит `event_id`, `attempts`, `next_attempt_at`, `last_error`.
`pending` — записи с оставшимися попытками, `exhausted` — достигшие лимита;
отложенная до `next_attempt_at` запись также может быть pending.
Статус читает опубликованные записи; временные файлы атомарной записи
не считаются опубликованными событиями. При одновременном изменении очереди
статус остаётся оперативным наблюдением, не транзакционным общим снимком.

Для явного повтора выберите настроенный `durable` и выполните
`telemetry.replay` с `input={}`. Ответ `poise-telemetry-replay/v1` содержит
`attempted`, `delivered`, `failed`, `corrupt`, `pending`, `coverage="partial"`.
За вызов обрабатывается не более `batch_items`; учитываются время следующей попытки
и `max_attempts`. Число попыток сохраняется до обращения к получателю, событие
удаляется из очереди после подтверждённой записи. Точная повторная доставка
получателю сохраняет прежний факт и вклад. Запись, сохранённая до перезапуска,
остаётся доступна явному повтору. `failed_attempts` статуса показывает сохранённую сумму счётчиков попыток
остающихся записей; это не отдельный счётчик подтверждённых ошибок.

Явный повтор выполняет собственные ограниченные операции ввода-вывода и ожидания
доступа к своей очереди. Короткие locks принадлежат этой очереди, не всем приложениям
или текущей Task. При доставке `queue.lock` не удерживается на время получателя.
Сервис не является постоянно работающим daemon: после выхода приложения
планируйте следующий явный `replay` по своим правилам сопровождения.

## Пределы и сопровождение

Пределы числа событий, размера записи/ответа и результата чтения задаются явно
в конфигурации. Автоматический диспетчер сохраняет не более `max_pending`
незавершённых квитанций и не более `2 * max_pending` недавних идентификаторов.
Сохраняемая очередь ограничена `max_entries` и `max_bytes`, одна запись —
`max_event_bytes` для содержимого события плюс служебная оболочка очереди;
один вызов повтора ограничен `batch_items`, число попыток записи — `max_attempts`.
При достижении пределов основной результат остаётся доступен, а наблюдения
могут быть потеряны. `max_pending` относится к экземпляру диспетчера, не задаёт
общесистемный предел числа одновременно запущенных отдельных Poise-процессов.

До успешного сохранения наблюдение может быть потеряно при сбое/завершении
процесса или отказе приёма. Уже выданный основной результат не подтверждает
сохранение события. Полное покрытие, восстановление потерянных событий,
автоматическое исправление настройки и непрерывная фоновая доставка не обещаны.
Сохраняйте каталог очереди и SQLite между запусками; выберите локальные доступные
пути. Для новой установки подготовьте собственную совместимую схему явно.
Перед плановым изменением рабочих настроек сохраните их копию.

| Диагностика | Правильное продолжение |
| --- | --- |
| `observations_unavailable`, `prepare:observations` | Выполнить явную подготовку выбранного собственного хранилища; повтор также завершает применение настроенного режима файла |
| `observations_unavailable`, `select:observations` | Выбрать хранилище совместимой версии и той же установки |
| `observations_unavailable`, `retry:read` | Повторить самостоятельное чтение после освобождения занятого хранилища |
| `observations_unavailable`, `retry:same-events` | После устранения причины повторить тот же пакет с теми же идентификаторами |
| `observation_conflict` | Согласовать новый факт с порядком/режимом потока и выбрать новую идентичность события |
| `observation_limit` | Выбрать пакет в заданных пределах либо явно изменить соответствующий предел |
| `measurement_unavailable` | Указать доступный измеритель с его действующим протоколом и пределами; использовать уже доступные строки/байты |
| `telemetry_unavailable` | Проверить выбранный режим, источник `poise`, свою очередь/получателя и явные настройки; для повторной доставки использовать `durable` |

Код и действие продолжения находятся в `error={code,details,action}`.
`observation_conflict` и `observation_limit` относятся к `rejected`/exit `2`;
недоступность собственных observations/measurement/telemetry — `unavailable`/`1`.
Внутри `telemetry.invoke` необязательная ошибка остаётся отдельной от исхода primary.
Для отсутствующего выбранного файла наблюдений подготовка требуется явно.
Проверка режима файла выполняется в явном `prepare`; штатный запуск не чинит конфиг.

## Самостоятельный пример

Из корня checkout выберите `POISE_PYTHON` абсолютным путём CPython 3.13.
Создайте отдельный каталог примера и экспортируйте обе переменные:

```bash
export POISE_EXAMPLE="$(mktemp -d)"
export POISE_PYTHON
```

Следующий блок — полный пример. Он использует собственный generated caller
только в дочерних процессах; native identity исходной оболочки сохраняется.
Все конфиги, исходный вывод и отчёт остаются в `POISE_EXAMPLE` для чтения;
сохраняйте их до завершения всех потребителей и работников.

```bash
"$POISE_PYTHON" - <<'PY'
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(os.environ['POISE_EXAMPLE']).absolute()
app = Path('apps/poise').absolute()
config_dir = root / 'config'
config_dir.mkdir()
config_path = config_dir / 'poise.json'
config = json.loads((app / 'config/poise.example.json').read_text())
config['caller'] = {'mode': 'generated', 'binding_file': '../caller.json'}
config['observations'] = {
    'schema': 'poise-observations-config/v1',
    'database': '../state/observations.sqlite', 'file_mode': 384,
    'sqlite_timeout_seconds': 0.2,
    'sources': {'reported-example': 'reported', 'poise': 'observed'},
    'limits': {'max_events': 8, 'max_event_bytes': 8192,
               'max_results': 20, 'max_read_response_bytes': 65536}}
config['measurement'] = {
    'schema': 'poise-measurement-config/v1', 'max_content_bytes': 4096,
    'max_response_bytes': 4096,
    'tokenizer': {'kind': 'unavailable', 'identity': 'unavailable-example',
                  'command': None}}
config['telemetry'] = {
    'schema': 'poise-telemetry-config/v1', 'mode': 'durable',
    'max_pending': 2, 'max_event_bytes': 8192,
    'spool': {'directory': '../queue', 'file_mode': 384, 'max_entries': 8,
              'max_bytes': 131072, 'max_attempts': 3, 'batch_items': 4,
              'retry_seconds': 1, 'lock_seconds': 0.2,
              'lock_poll_seconds': 0.01}}
config['output'] = json.loads(
    (app / 'config/output.example.json').read_text())['output']
config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
state = config_dir / 'state.json'
state.write_bytes((app / 'config/state.example.json').read_bytes())
env = {k: v for k, v in os.environ.items()
       if k not in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID')}
calls = []

def invoke(operation, value, *, state_entry=False):
    packet = {'schema': 'poise-state/v1' if state_entry else 'stride-poise/v1',
              'operation': operation, 'input': value}
    argv = [sys.executable, '-I', '-S', '-B',
            str(app / 'bin' / ('poise-state' if state_entry else 'poise')),
            '--config', str(state if state_entry else config_path)]
    run = subprocess.run(argv, input=json.dumps(packet).encode(),
                         capture_output=True, env=env)
    response = json.loads(run.stdout)
    calls.append({'argv': argv, 'packet': packet, 'exit': run.returncode,
                  'stdout': run.stdout.decode(), 'stderr': run.stderr.decode()})
    (root / 'calls.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2))
    assert run.returncode == 0 and not run.stderr and response['status'] == 'ok'
    return response

invoke('prepare', {}, state_entry=True)
session = invoke('session.ensure', {})['result']['session']['id']
prepared = invoke('observations.prepare', {})['result']
assert prepared['version'] == 1 and prepared['created'] is True
repeat_prepare = invoke('observations.prepare', {})['result']
assert repeat_prepare['created'] is False

def usage(identity, sequence, incoming, outgoing):
    return {'source': 'reported-example', 'stream': 'portions',
            'event_id': identity, 'observed_at': '2026-01-01T00:00:00+00:00',
            'kind': 'usage', 'value': {'sequence': sequence, 'mode': 'delta',
            'counters': {'input_tokens': incoming, 'output_tokens': outgoing,
                         'total_tokens': incoming + outgoing,
                         'cached_input_tokens': None, 'reasoning_tokens': None}}}

first, second = usage('portion-1', 1, 80, 20), usage('portion-2', 2, 15, 5)
record = invoke('observations.record', {'events': [first, second]})['result']
assert record['count'] == 2 and all(not x['replayed'] for x in record['entries'])
repeated = invoke('observations.record', {'events': [first]})['result']['entries'][0]
assert repeated['replayed'] is True and repeated['seq'] == record['entries'][0]['seq']
assert repeated['session_id'] == session
for scope in ('installation', 'session'):
    read = invoke('observations.read', {'scope': {'kind': scope},
                  'source': 'reported-example', 'after': 0, 'limit': 20})['result']
    assert read['totals'] == {'input_tokens': 95, 'output_tokens': 25,
            'total_tokens': 120, 'cached_input_tokens': None, 'reasoning_tokens': None}
    assert read['usage_events'] == 2 and read['coverage'] == 'partial'
interval = {'source': 'reported-example', 'stream': 'intervals', 'event_id': 'ns-1',
            'observed_at': '2026-01-01T00:00:00+00:00', 'kind': 'interval',
            'value': {'started_at': '2026-01-01T03:00:00.000000000+03:00',
                      'ended_at': '2026-01-01T03:00:00.000000001+03:00'}}
assert invoke('observations.record', {'events': [interval]})['result']['entries'][0]['duration_ns'] == 1
measure = invoke('content.measure', {'before': 'A\n', 'after': 'A\nёж\r\n'})['result']
assert (measure['added_lines'], measure['added_bytes'], measure['removed_bytes']) == (1, 6, 0)
assert measure['changed_tokens'] is None and measure['coverage'] == 'partial'
status = invoke('telemetry.status', {})['result']
assert status['mode'] == 'durable' and status['spool']['pending'] == 0
replay = invoke('telemetry.replay', {})['result']
assert (replay['attempted'], replay['delivered'], replay['pending']) == (0, 0, 0)
raw = root / 'stdout.bin'
raw.write_bytes('ёж\n'.encode())
value = {'reads': [{'source': {'path': str(raw), 'captured_bytes': 5},
                    'start': 0, 'count': 5}]}
direct = invoke('output.range', value)
assert base64.b64decode(direct['result']['ranges'][0]['data']['value']) == 'ёж\n'.encode()
wrapped = invoke('telemetry.invoke', {'operation': 'output.range', 'input': value})
assert wrapped['result']['primary'] == direct
assert wrapped['status'] == direct['status'] and wrapped['error'] == direct['error']
assert wrapped['result']['telemetry']['coverage'] == 'partial'
summary = {'status': 'passed', 'usage_total': 120, 'interval_ns': 1,
           'added_bytes': 6, 'primary_preserved': True,
           'automatic_delivery_confirmed_by_this_example': False, 'calls': len(calls)}
(root / 'example-result.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary))
PY
```

Пример возвращает `status="passed"`, итог `120`, интервал `1` нс, добавленные
`6` байтов и `primary_preserved=true`. Он подтверждает приём обёртки,
а не завершение асинхронной доставки. Для своего сохранённого потока используйте
его фактический путь и исходный `captured_bytes` по договору производителя.

## Происхождение и проверки

Выделенные операции сохраняют назначенные Poise общие механизмы исходного приложения.
Наблюдения отделены от результатов проверок и полезности Task. Исходная карта
разделения и точные квитанции переноса принадлежат задаче поставки; таблица ниже
указывает устойчивые владельцы кода и проверок текущего приложения.

| Возможность и источник | Текущий владелец | Проверка |
| --- | --- | --- |
| Общие usage/источник: `src/poise/modules/accounting/domain.py` | `domain/observations.py`, `application/observations.py`, `infrastructure/observation_store.py`: prepare/record/read | `tests/test_observations.py`: источники, реальные Session, режимы и точные повторы |
| Clock: `src/poise/modules/accounting/clock.py` | `domain/observations.py`, `infrastructure/observation_clock.py`: timing/interval/автоматическое событие | Тот же пакет: сопоставимость, смещения, целые наносекунды, порядок источника |
| Общее измерение: `src/poise/infrastructure/accounting_measurement.py` | `domain/measurement.py`, `infrastructure/tokenizer.py`: content.measure | Тот же пакет: UTF-8/окончания строк, реальный instrument и его недоступность |
| Необязательные capture/delivery: `src/poise/application/telemetry.py`, `src/poise/infrastructure/telemetry.py`, `src/poise/infrastructure/telemetry_spool.py` | `application/telemetry.py`, `infrastructure/telemetry_dispatch.py`, `infrastructure/telemetry_spool.py`: invoke/status/replay | Тот же пакет: отдельный primary, границы очереди/приёма/повторов, реальная незавершённость работника |
| Сохранённые результаты/ranges: `src/poise/infrastructure/result_views.py` | Действующие [команды](poi-command-contract.md) и [вывод](poi-output-contract.md) | `tests/test_command.py`, `tests/test_output.py`; исходные потоки/адреса переиспользованы |

Для семи операций конкретные сценарии в `tests/test_observations.py`:

| Операция | Опорный сценарий |
| --- | --- |
| `observations.prepare` | Самостоятельная повторная подготовка: `prepare-independent-repeat` |
| `observations.record` | Порции и точный повтор: `delta-and-exact-repeat` |
| `observations.read` | Страница и полный итог scope: `read-page-and-whole-scope` |
| `content.measure` | Измерение независимых текстовых значений: `measurement-literal-texts` |
| `telemetry.invoke` | Сохранение полного primary: `invoke-preserves-primary` |
| `telemetry.status` | Чтение сохраняемой очереди: `durable-status-read-only` |
| `telemetry.replay` | Единственный вклад при повторной доставке: `saved-replay-one-contribution` |

Пути текущих владельцев в таблице относительны к `apps/poise`. Начальные `src/poise`
указывают исходное приложение; их наличие не является зависимостью запуска Poise.
Из корня checkout запускайте проверки выбранным `POISE_PYTHON` (CPython 3.13).
Задайте `POISE_OBSERVATIONS_CHECK_OUTPUT` отдельным абсолютным путём для отчётов:

```bash
"$POISE_PYTHON" -B apps/poise/tests/test_observations.py --suite behavior \
  --output-root "$POISE_OBSERVATIONS_CHECK_OUTPUT/observations"
"$POISE_PYTHON" -B apps/poise/tests/test_shell.py --suite behavior \
  --output-root "$POISE_OBSERVATIONS_CHECK_OUTPUT/shell"
```

Первый пакет содержит 60 целевых сценариев, второй — восемь поддерживаемых
проверок входа. Полные результаты — `observations-details.json` и
`shell-details.json` в соответствующих выбранных каталогах.
Эти проверки включают недоступность/границы и фактические потоки данных;
исполняемый пример выше отдельно показывает правильное использование семи API.
