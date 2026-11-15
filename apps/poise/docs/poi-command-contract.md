# Контракт команды Poise

## Назначение и поддержанное использование

Самостоятельный `apps/poise/bin/poise --config PATH` исполняет
`command.request`, `command.completion` и `command.delete`. Нужны Linux/POSIX,
CPython 3.13 со стандартной библиотекой, подготовленная собственная Session и явная
конфигурация. Поддержаны локальная команда и один вызов MCP через stdio; выбранная
и проверенная версия MCP — `2024-11-05`. Запрос и последующее чтение могут приходить
из разных процессов CLI одной Session. Независимые команды выполняются параллельно.

Это действующий договор исполнения, заменяющий прежний проект T068. Общий запуск,
caller, подготовка и статус Session принадлежат [руководству оболочки](shell-poi.md),
подготовка её состояния — [poise-state](state-poi.md). Task, Stage, Workspace,
оркестратор и базы Stride для команды не требуются. Синхронный внешний вызов hooks
и пробы доступности имеют [собственный договор](hooks-poi.md).

Полные потоки и результат сохраняются отдельными файлами. Метаданные
`output.minimal` и байтовые порции `output.range` имеют
[действующий договор чтения вывода](poi-output-contract.md). Передайте абсолютный
путь из сохранённой ссылки stdout/stderr и её число байтов как `captured_bytes`;
полный результат и исход исполнения продолжают принадлежать команде. Более широкий
[исторический проект gateway](contract-poi.md) остаётся проектным материалом;
действующий вход команд определён на этой странице.

## Подготовка и конфигурация

Скопируйте [полный пример](../config/command.example.json) в рабочий каталог вне
исходников. Все известные поля секции `commands` обязательны. Значения примера
выбраны явно: приложение не подставляет их как значения по умолчанию. Общие поля
`schema`, `installation_id`, `session_store`, `caller`, `roles` и `limits` описаны
в [справочнике оболочки](shell-poi.md). Для локального примера используйте отдельный
generated caller; для native caller следуйте тому же руководству.

Подготовьте родительский каталог `session_store`, родительский каталог
`commands.request_store` и каталог `commands.output_root`. Пути внутри конфига
разрешаются относительно каталога самого конфига. Подготовьте Session-хранилище
версии `1` через `bin/poise-state`, затем вызовите `session.ensure` с тем же
`session_store` и caller. Назначение роли для исполнения команды не требуется.

`request_store` выбирает отдельный от Session SQLite-файл. При первом запросе
владелец команд создаёт отсутствующий каталог записей формата `1`, затем допускает
запрос. Здесь «каталог записей» означает базу команд; её родительская директория
уже подготовлена оператором. Существующий файл должен иметь совместимый формат.
Сохраните исходные данные перед операторским исправлением несовместимой установки
и выберите подготовленное хранилище. Длительное исполнение и ожидание работают вне
коротких транзакций записи.

| Поле `commands` | Смысл и явное значение примера |
| --- | --- |
| `schema` | `poise-command-config/v1` |
| `request_store` | `command-state/commands.sqlite`; отдельный SQLite-каталог идентичностей и результатов |
| `output_root` | `command-output`; подготовленная директория полных файлов |
| `file_mode` | Целое `384` (`0600`); режим stdout, stderr и полного результата устанавливается явно, независимо от umask |
| `files.stdout` | `stdout.bin`; отдельное имя файла stdout |
| `files.stderr` | `stderr.bin`; отдельное имя файла stderr |
| `files.result` | `result.json`; имя полного JSON-результата |

Три имени файлов — разные непустые имена без `/`, отличные от `.` и `..`.
`file_mode` — целое от `0` до `511` (`0777`). Неизвестные поля конфигурации
сохраняются в рабочем файле и не меняют поведение известных полей. Настройки
`commands` проверяются при выбранной командной операции; другие семейства
операций используют свои настройки.

| Поле `commands.limits` | Единица и явное значение примера |
| --- | --- |
| `id_bytes` | `128` байтов UTF-8 для `command_id` |
| `sqlite_timeout_seconds` | `1` секунда ожидания занятого каталога; допустимо конечное число от нуля |
| `poll_ms` | `10` мс между наблюдениями; ближайший deadline или момент эскалации наступает раньше этого интервала |
| `capture_chunk_bytes` | `4096` байтов за одно чтение потока |
| `terminate_grace_ms` | `100` мс между TERM и KILL при execution deadline |
| `completion_budget_ms` | `5000` мс на ожидание результата текущим `command.completion` |
| `delivery_margin_ms` | `100` мс запаса при согласовании бюджетов запроса |
| `transport_budget_ms` | `6000` мс бюджета внешней доставки, который учитывает вызывающий транспорт |
| `transport_margin_ms` | `100` мс запаса внешнего транспорта |
| `mcp_max_message_bytes` | `1048576` байтов UTF-8 одного входящего кадра без завершающего LF |
| `mcp_max_messages` | `16` входящих кадров, включая уведомления, до конца потока |

Все лимиты, кроме `sqlite_timeout_seconds`, — положительные целые. Выберите
`transport_budget_ms >= completion_budget_ms + transport_margin_ms`.
Для каждого нового `command.request` также требуется:

```text
execution_budget_ms + cleanup_budget_ms + delivery_margin_ms <= completion_budget_ms
cleanup_budget_ms > terminate_grace_ms
```

Внешний вызывающий устанавливает свой реальный timeout с учётом
`transport_budget_ms`: это настройка согласованной доставки, а не отдельный таймер,
которым Poise прерывает свой CLI. `completion_budget_ms` ограничивает ожидание
результата после получения Session; ожидания SQLite внутри этого чтения расходуют
тот же оставшийся бюджет. Для последующего `command.completion` можно выбрать
более короткое ожидание в текущем конфиге, сохранив согласованное соотношение
transport/completion. Это не меняет сохранённые настройки самого запуска.

## Точный публичный вход

Один JSON UTF-8 на stdin и EOF, один JSON с LF на stdout. Внешний пакет содержит
ровно `schema`, `operation`, `input`; `schema` — `stride-poise/v1`.
JSON использует уникальные ключи и конечные числа. Типы проверяются строго;
булево значение отличается от числа. Общий `limits.packet_bytes` ограничивает
входной пакет, а не размер сохранённых потоков или MCP-ответа.

| `operation` | Точные поля `input` |
| --- | --- |
| `command.request` | `schema`, `command_id`, `transport`, `argv`, `cwd`, `env`, `execution_budget_ms`, `cleanup_budget_ms`, `mcp` |
| `command.completion` | `command_id` |
| `command.delete` | `command_id` |

У `command.request` поле `schema` равно `poise-command-request/v1`.
`command_id` — непустая непрозрачная строка без NUL в пределах `id_bytes` UTF-8.
`transport` — `command` или `mcp_stdio`. `argv` — непустой список строк без NUL;
`argv[0]` — абсолютный путь исполняемого файла, последующие аргументы могут быть
пустыми. `cwd` — абсолютный путь существующей рабочей директории.

`env` — полный словарь строковых переменных дочернего процесса. Ключи непустые,
без `=` и NUL; значения без NUL. Если программе нужен `PATH`, укажите его явно.
Передача argv выполняется непосредственно, без интерполяции shell. Вход stdin
обычной команды — EOF. Для MCP stdin содержит сообщения протокола.

Оба бюджета запроса — положительные целые миллисекунды с соотношениями выше.
Для `transport="command"` задайте `mcp:null`. Для `mcp_stdio` задайте ровно:

```json
{
  "protocol_version": "2024-11-05",
  "client_info": {"name": "example-client", "version": "1"},
  "tool_name": "echo",
  "arguments": {"text": "готово"}
}
```

`protocol_version`, `tool_name`, `client_info.name/version` — непустые строки без
NUL. `arguments` — произвольный JSON-объект аргументов выбранного инструмента;
его значения сохраняются целиком.

## Идентичность, повтор и сохранение

Идентичность запуска — `(текущая Session, command_id)`. Запрос не принимает
идентичность Session или роль от вызывающего: их определяет существующий caller.
Новый запрос сохраняет полный вход и выбранные настройки исполнения перед
запуском отдельного worker. CLI допуска может завершиться раньше команды;
последующее чтение той же Session получает её сохранённое состояние.

Точное повторение `command.request` возвращает текущий сохранённый результат
с `replayed=true`. Второго запуска нет. Порядок ключей JSON-объекта незначим,
порядок списка значим, булево и число различаются; численно равные JSON-числа
сравниваются равными. Для этой же identity сохраняйте исходный запрос и настройки
исполнения: выходной root, режим и имена файлов, `poll_ms`, размер чтения, grace
и MCP-лимиты. Их изменение под прежней identity даёт `command_reuse_conflict`.
Лимиты чтения результата, внешнего транспорта, длины ID и SQLite-ожидания
используются текущим вызовом и не заменяют сохранённые настройки worker.

В другой Session такой же `command_id` обозначает независимый запуск.
Данные хранятся до явного удаления оператором. Закрытая Session может прочитать
сохранённый результат, выполнить точный повтор и удалить его; новый запуск
производится в открытой Session. У текущего CLI нет операции `session.close`.
Существующее закрытое состояние принадлежит владельцу Session.

## Состояния и точные ответы

Внешний ответ всегда содержит `schema`, `status`, `result`, `error`, `evidence`.
Для команд `schema="stride-poise/v1"`, `evidence=[]`.

| `status` и exit CLI | Смысл |
| --- | --- |
| `ok`, `0` | Доставлено сохранённое состояние без инфраструктурной ошибки; реальный ненулевой exit child или `isError=true` MCP читаются отдельно |
| `unavailable`, `1` | Сохранённая ошибка запуска/протокола/capture либо текущая недоступность/исчерпанное ожидание; `result` содержит метаданные, когда они доступны |
| `rejected`, `2` | Вход, конфигурация, Session или повтор не соответствуют договору |

У успешного ответа `error=null`; у отказа `error` содержит ровно
`code`, `details`, `action`. Пример конверта допуска:

```json
{"schema":"stride-poise/v1","status":"ok","result":{"schema":"poise-command/v1","operation":"request","command_id":"cli-1","transport":"command","state":"ADMITTED","termination":null,"deadline_exceeded":false,"business_error":null,"stdout":null,"stderr":null,"full_result_ref":null,"error":null,"replayed":false},"error":null,"evidence":[]}
```

Фактически первый ответ может уже содержать `RUNNING` или завершённый результат.
`request/completion` возвращают ровно 13 полей приведённых метаданных;
`operation` — `request` или `completion`. `completion` имеет `replayed=false`.

| `state` | Смысл |
| --- | --- |
| `ADMITTED` | Идентичность и настройки сохранены, результат ещё не наблюдён |
| `RUNNING` | Worker принял единственный запуск; окончательный результат ещё не опубликован |
| `TERMINAL` | Наблюдён реальный exit, signal либо невозможность старта |
| `UNKNOWN` | Сохранена неопределённость наблюдения, которую нужно учитывать вызывающему |

`termination` — `null` до наблюдения или ровно один объект:
`{"kind":"exit","exit_code":N}`, `{"kind":"signal","signal":N}`,
`{"kind":"not_started"}`. `deadline_exceeded` — отдельное булево значение;
`business_error` — `null` для обычной команды или ещё не полученного MCP-результата,
булево для полученного ответа инструмента. Эти факты независимы.

Каждый `stdout/stderr` — `null` до публикации или объект ровно с полями
`source_id`, `path`, `bytes`, `complete`. `source_id` — непрозрачная сохранённая
строка, `path` — абсолютный путь отдельного бинарного файла, `bytes` — число
сохранённых байтов. `complete` означает полученный EOF и успешное сохранение этого
потока. Файлы могут содержать произвольные байты, включая NUL и не-UTF-8.
`full_result_ref` — `null` или ровно `{"path":"/absolute/result.json","bytes":N}`.
Полный файл публикуется перед метаданными, которые на него ссылаются.

Полный результат — JSON с LF и ровно 11 полями: `schema`, `command_id`, `transport`,
`state`, `termination`, `deadline_exceeded`, `business_error`, `stdout`, `stderr`,
`error`, `mcp_response`. `schema="poise-command-result/v1"`; остальные общие поля
совпадают с опубликованным состоянием. `mcp_response` — полный сопоставленный
JSON-RPC-ответ поставщика или `null`; все дополнительные поля поставщика сохранены.
Тела stdout/stderr читаются из отдельных файлов, не из краткого конверта CLI.

## Бюджеты и фактическое завершение

Execution budget начинается непосредственно перед попыткой запуска child.
При его исчерпании отправляется SIGTERM группе процесса и начинается один общий
cleanup budget. После `terminate_grace_ms` отправляется SIGKILL, если ещё нужен
переход эскалации. Ожидание учитывает ближайший момент эскалации независимо от
величины `poll_ms`. Итоговый signal/exit берётся из реального наблюдения child.

Естественное завершение child или полученный окончательный MCP-ответ также
начинает cleanup. В этом общем бюджете дочитываются оба потока и ожидается child.
Получение MCP-ответа закрывает stdin; проверка входящих уведомлений и лимитов
продолжается до EOF. При исчерпании cleanup публикуется реально известный исход
и сохранённые префиксы; неполный поток имеет `complete=false` и ошибку capture.
Если исход child не наблюдён, `termination=null`, состояние — `UNKNOWN`.
При естественном завершении с ещё открытым у потомка потоком cleanup тоже конечен.

Ненулевой exit команды сам по себе сохраняется как `ok` без инфраструктурной
ошибки. Достигнутый execution deadline с полностью наблюдённым signal/exit и
полными потоками также сохраняет свои факты при `ok`. Чтение completion не
запускает worker. По окончании своего бюджета оно сообщает текущую ошибку
занятого хранилища либо `command_wait_expired` с последним прочитанным состоянием.
Последующее чтение использует прежнюю identity.

## Обмен MCP

Адаптер отправляет `initialize` с выбранной версией, пустыми `capabilities` и
`clientInfo`, получает сопоставленный ответ, затем отправляет
`notifications/initialized` и один `tools/call`. У ответа initialize обязательны
совпадающий `protocolVersion`, объект `capabilities` и `serverInfo` со строками
`name/version`. Идентификаторы запросов сопоставляются по значению и типу.

У tool-result обязателен массив `content`; `isError` при наличии — булево,
при отсутствии результат считается успешным. Поддержаны элементы `text` со
строкой `text`, `image` со строками `data/mimeType`, `resource` с объектом
`resource`, строкой `uri` и строкой `text` либо `blob`. Дополнительные поля
поставщика сохраняются в полном ответе. `isError=true` задаёт
`business_error=true`; exit сервера остаётся самостоятельным фактом.
JSON-RPC error инструмента сохраняется полным с `business_error=true` и
инфраструктурной диагностикой `mcp_response_error`.

Каждое входящее сообщение — JSON-объект `jsonrpc="2.0"` и LF. Лимит размера
считает все байты до LF; лимит количества включает ответы и уведомления,
в том числе после tool-result. Граница включительна. Уведомление имеет непустой
`method`, необязательный объект `params` и не содержит `id`, `result`, `error`.
Несопоставленный ответ, неверная структура/версия, превышенный лимит или
незавершённый кадр дают явную диагностику. После ошибки протокола semantic parsing
заканчивается, а полные raw-потоки продолжают сохраняться до конца cleanup.
Поздняя ошибка сохраняет уже полученный ответ и его `business_error`.
EOF до ответа инструмента сохраняется как неопределённость `mcp_response`.

## Ошибки и правильное продолжение

Общие `invalid_config`, `invalid_json`, `invalid_request`, `unsupported_schema`,
`packet_limit` и ошибки Session описаны в [руководстве оболочки](shell-poi.md).
Следующая таблица уточняет командные ошибки; `errno` может быть `null`, если
источник ошибки не предоставляет код POSIX.

| `code` | `details`, `action` и правильное действие |
| --- | --- |
| `commands_not_configured` | `{}`, `configure:commands`: подготовить полную секцию `commands` |
| `command_budget_incompatible` | Два бюджета запроса и текущие completion/delivery; `configure:commands`: согласовать бюджеты по формулам выше |
| `command_reuse_conflict` | `command_id`, `use:new-command`: для нового намерения выбрать новый ID |
| `command_not_found` | `command_id`, `use:command`: выбрать сохранённый ID своей Session |
| `command_store_incompatible` | `version` (целое либо `null`), `select:command-store`: выбрать совместимое подготовленное состояние после сохранения прежних данных |
| `command_store_busy` | `{}`, `retry:command`: повторить тот же запрос после завершения текущей короткой операции с каталогом |
| `command_store_io` | `errno`, `configure:command-store`: подготовить доступные каталог и права хранилища |
| `command_wait_expired` | `command_id`, `budget_ms`, `wait:command`: повторить completion той же identity с выбранным конечным ожиданием |
| `command_start_failed` | `path`, `errno`, `configure:command`: подготовить исполняемый файл/cwd и для новой попытки выбрать новый ID |
| `command_capture_failed` | `stream`, `errno`, `inspect:command`: прочитать сохранённый префикс и фактический исход, восстановить доступность назначения перед новым запуском |
| `command_protocol_failed` | `phase` (`initialize` или `tools_call`), `reason` (`invalid_response`, `protocol_version`, `message_limit`), `configure:command`: согласовать сервер, выбранную версию и лимиты перед новым запуском |
| `mcp_response_error` | `code` ответа JSON-RPC, `inspect:command`: прочитать полный ответ инструмента |
| `command_observation_lost` | `phase` (`dispatch`, `execution`, `publication`, `mcp_response`), `inspect:command`: проверить сохранённые файлы и фактическую активность исполнителя |
| `command_delete_failed` | `command_id`, `path`, `errno`, `inspect:command`: устранить причину доступа и повторить удаление того же ID |

Удаление выполняют после завершения всех писателей и читателей результата.
`command.delete` удаляет stdout, stderr, полный результат, пустую директорию
данного исполнения, затем запись своей Session. Ответ:

```json
{"schema":"stride-poise/v1","status":"ok","result":{"schema":"poise-command-delete/v1","command_id":"cli-1","state":"absent"},"error":null,"evidence":[]}
```

Повтор удаления отсутствующего ID имеет тот же ответ. При частичной ошибке
удалённые файлы остаются удалёнными, запись сохраняется для продолжения.
Удаление — процедура окончания хранения после остановки участников, а не
команда отмены процесса. После удаления тот же ID снова может обозначить новый
запуск, поэтому для самостоятельных намерений выбирайте новые ID.

Допуск SQLite и запуск процесса — отдельные действия. Неожиданная остановка
между ними или во время публикации может оставить `ADMITTED/RUNNING` без нового
результата либо наблюдённый `UNKNOWN`. Точный повтор сохраняет одну identity;
автоматического повторного запуска и восстановления процесса нет. После проверки
сохранённых данных и завершения участников оператор может закончить хранение
и явно создать новое намерение с новым ID. При необходимости предварительно
сохраните нужные полные файлы обычной операторской копией.

## Пример: подготовка и команда

Выполняйте три блока по порядку из корня поставки. Выберите абсолютный
`POISE_PYTHON` CPython 3.13 и существующий `POISE_EXAMPLE_PARENT` для рабочих
материалов; родительский путь может быть абсолютным или относительным. Помощник
приводит рабочий каталог примера к абсолютному пути до создания запросов.
Пример сохраняет конфиги, полные ответы и результаты в отдельной
директории; его `client.py` — локальный помощник примера, не часть API.

```bash
export POISE_PYTHON
export POISE_EXAMPLE_PARENT
export POISE_EXAMPLE="$(mktemp -d "${POISE_EXAMPLE_PARENT:?}/command-example.XXXXXX")"
cat > "$POISE_EXAMPLE/client.py" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(os.environ['POISE_EXAMPLE']).resolve()
APP = Path.cwd() / 'apps/poise'
ENV = dict(os.environ)
# This isolated generated caller belongs only to example child processes.
ENV.pop('CODEX_SESSION_ID', None)
ENV.pop('CODEX_THREAD_ID', None)

def call(operation, value, entry='poise', config='command.json'):
    schema = 'poise-state/v1' if entry == 'poise-state' else 'stride-poise/v1'
    packet = {'schema': schema, 'operation': operation, 'input': value}
    number = len(list((ROOT / 'calls').glob('*.request.json')))
    stem = ROOT / 'calls' / f'{number:03d}'
    command = [sys.executable, '-I', '-S', '-B', str(APP / 'bin' / entry),
               '--config', str(ROOT / config)]
    stem.with_suffix('.request.json').write_text(json.dumps(packet) + '\n')
    result = subprocess.run(command, input=(json.dumps(packet) + '\n').encode(),
                            capture_output=True, env=ENV, timeout=10)
    stem.with_suffix('.stdout').write_bytes(result.stdout)
    stem.with_suffix('.stderr').write_bytes(result.stderr)
    stem.with_suffix('.execution.json').write_text(json.dumps({
        'argv': command, 'cwd': str(Path.cwd()), 'env': ENV,
        'exit_code': result.returncode}) + '\n')
    assert result.returncode == 0 and result.stderr == b''
    response = json.loads(result.stdout)
    assert response['status'] == 'ok' and response['error'] is None
    return response['result']

def request(identity, argv, transport='command', mcp=None):
    return {'schema': 'poise-command-request/v1', 'command_id': identity,
            'transport': transport, 'argv': argv, 'cwd': str(ROOT),
            'env': {'LANG': 'C.UTF-8', 'EXAMPLE_VALUE': 'example'},
            'execution_budget_ms': 1000, 'cleanup_budget_ms': 500, 'mcp': mcp}

if __name__ == '__main__':
    for name in ('state', 'command-state', 'command-output', 'calls'):
        (ROOT / name).mkdir()
    command = json.loads((APP / 'config/command.example.json').read_text())
    (ROOT / 'command.json').write_text(json.dumps(command) + '\n')
    state = json.loads((APP / 'config/state.example.json').read_text())
    state['database'] = command['session_store']
    (ROOT / 'state.json').write_text(json.dumps(state) + '\n')
    call('prepare', {}, 'poise-state', 'state.json')
    call('session.ensure', {})
    source = ("import os,sys;from pathlib import Path;"
              "assert sys.argv[1]=='';assert os.environ['EXAMPLE_VALUE']=='example';"
              "assert set(os.environ)=={'LANG','EXAMPLE_VALUE'};"
              "Path('cli.effects').open('ab').write(b'one\\n');"
              "os.write(1,b'hello\\x00world\\n');os.write(2,b'note\\n');sys.exit(7)")
    packet = request('cli-1', [sys.executable, '-I', '-S', '-B', '-c', source, ''])
    (ROOT / 'cli.request.json').write_text(json.dumps(packet) + '\n')
    first = call('command.request', packet)
    assert first['command_id'] == 'cli-1' and first['replayed'] is False
    final = call('command.completion', {'command_id': 'cli-1'})
    assert final['state'] == 'TERMINAL' and final['termination'] == {'kind': 'exit', 'exit_code': 7}
    assert final['deadline_exceeded'] is False and final['business_error'] is None
    full_path = Path(final['full_result_ref']['path'])
    full_bytes = full_path.read_bytes()
    assert len(full_bytes) == final['full_result_ref']['bytes']
    full = json.loads(full_bytes)
    assert full['schema'] == 'poise-command-result/v1' and full['mcp_response'] is None
    assert Path(full['stdout']['path']).read_bytes() == b'hello\x00world\n'
    assert Path(full['stderr']['path']).read_bytes() == b'note\n'
    assert full['stdout']['complete'] and full['stderr']['complete']
    assert (ROOT / 'cli.effects').read_bytes() == b'one\n'
    replay = call('command.request', packet)
    assert replay == dict(final, operation='request', replayed=True)
    assert full_path.read_bytes() == full_bytes
    assert (ROOT / 'cli.effects').read_bytes() == b'one\n'
    (ROOT / 'cli.final.json').write_text(json.dumps(final) + '\n')
    print('CLI: exit 7, complete separate streams, exact replay, one execution')
PY
"${POISE_PYTHON:?}" -B "$POISE_EXAMPLE/client.py"
```

Ожидаемый последний вывод — `CLI: exit 7, complete separate streams, exact replay, one execution`.
Успешная доставка результата имеет exit CLI `0`, а результат child — `7`.
Полные stdout/stderr и JSON пока сохранены по ссылкам в `cli.final.json`.

## Пример: действительный MCP stdio

Продолжите в той же оболочке. Сервер примера отвечает на настоящий обмен,
сохраняет один вызов инструмента и завершает процесс после EOF stdin.

```bash
cat > "$POISE_EXAMPLE/peer.py" <<'PY'
import json
from pathlib import Path
import sys

def receive():
    return json.loads(sys.stdin.buffer.readline())

def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)

initialize = receive()
assert initialize['method'] == 'initialize'
send({'jsonrpc': '2.0', 'id': initialize['id'], 'result': {
    'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
    'serverInfo': {'name': 'example-peer', 'version': '1'}}})
assert receive()['method'] == 'notifications/initialized'
call = receive()
assert call['method'] == 'tools/call' and call['params']['name'] == 'echo'
assert call['params']['arguments'] == {'text': 'готово'}
with Path('mcp.effects').open('ab') as effects:
    effects.write(b'one\n')
send({'jsonrpc': '2.0', 'id': call['id'], 'result': {
    'content': [{'type': 'text', 'text': 'готово'}], 'isError': False,
    'example': {'retained': True}}})
assert sys.stdin.buffer.read() == b''
PY
"$POISE_PYTHON" -B - <<'PY'
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ['POISE_EXAMPLE'])
from client import ROOT, call, request

mcp = {'protocol_version': '2024-11-05',
       'client_info': {'name': 'example-client', 'version': '1'},
       'tool_name': 'echo', 'arguments': {'text': 'готово'}}
packet = request('mcp-1', [sys.executable, '-I', '-S', '-B', str(ROOT / 'peer.py')],
                 'mcp_stdio', mcp)
call('command.request', packet)
final = call('command.completion', {'command_id': 'mcp-1'})
assert final['state'] == 'TERMINAL' and final['termination'] == {'kind': 'exit', 'exit_code': 0}
assert final['business_error'] is False and final['deadline_exceeded'] is False
full_path = Path(final['full_result_ref']['path'])
full_bytes = full_path.read_bytes()
assert len(full_bytes) == final['full_result_ref']['bytes']
full = json.loads(full_bytes)
response = full['mcp_response']
assert response['jsonrpc'] == '2.0' and type(response['id']) is int
assert response['result'] == {'content': [{'type': 'text', 'text': 'готово'}],
                              'isError': False, 'example': {'retained': True}}
frames = [json.loads(line) for line in Path(full['stdout']['path']).read_bytes().splitlines()]
assert len(frames) == 2 and frames[-1] == response
assert Path(full['stderr']['path']).read_bytes() == b''
assert full['stdout']['complete'] and full['stderr']['complete']
assert call('command.request', packet) == dict(final, operation='request', replayed=True)
assert (ROOT / 'mcp.effects').read_bytes() == b'one\n'
assert full_path.read_bytes() == full_bytes
(ROOT / 'mcp.final.json').write_text(json.dumps(final) + '\n')
print('MCP: real exchange, full provider result, one tool call')
PY
```

Ожидаемый вывод — `MCP: real exchange, full provider result, one tool call`.
Неизвестное приложению поле `example` остаётся в полном ответе поставщика.

## Пример: окончание хранения

Оба процесса уже завершились, чтение примера окончено. Сохраните нужные вам
результаты перед удалением. Следующий блок явно заканчивает хранение двух запусков
и проверяет повтор процедуры.

```bash
"$POISE_PYTHON" -B - <<'PY'
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ['POISE_EXAMPLE'])
from client import ROOT, call

for identity, saved in [('cli-1', 'cli.final.json'), ('mcp-1', 'mcp.final.json')]:
    final = json.loads((ROOT / saved).read_text())
    paths = [Path(final[key]['path']) for key in ('stdout', 'stderr', 'full_result_ref')]
    expected = {'schema': 'poise-command-delete/v1', 'command_id': identity, 'state': 'absent'}
    assert call('command.delete', {'command_id': identity}) == expected
    assert all(not path.exists() for path in paths)
    assert call('command.delete', {'command_id': identity}) == expected
print('DELETE: both results removed, repeated deletion succeeds')
PY
```

Ожидаемый вывод — `DELETE: both results removed, repeated deletion succeeds`.
Локальные журналы вызовов и конфиги примера остаются в `POISE_EXAMPLE`.

## Владельцы реализации и проверка

[CommandOperations](../application/commands.py) использует собственную Session,
допускает запрос, читает завершение и заканчивает хранение.
[Домен команд](../domain/command.py) задаёт входы, типизированный повтор и значения;
[настройки](../domain/command_config.py) — известные поля и бюджеты.
[CommandStore](../infrastructure/command_store.py) владеет короткими транзакциями
идентичности, claim и публикации. [Worker](../infrastructure/command_execution.py)
и [MCP](../infrastructure/command_mcp.py) наблюдают процесс/протокол;
[CommandFiles](../infrastructure/command_files.py) сохраняет полные файлы через
существующий [OutputStore](../infrastructure/files.py).
[Shell](../shell.py) и приватный [вход worker](../bin/poise-command-worker)
соединяют владельцев. Этот приватный вход запускает само приложение.

Из корня checkout с выбранным `POISE_PYTHON` и подготовленным рабочим
`POISE_COMMAND_CHECK_OUTPUT` выполните:

```bash
"$POISE_PYTHON" -B apps/poise/tests/test_command.py --suite delivery \
  --output-root "$POISE_COMMAND_CHECK_OUTPUT/delivery"
"$POISE_PYTHON" -B apps/poise/tests/test_command.py --suite behavior \
  --output-root "$POISE_COMMAND_CHECK_OUTPUT/behavior"
```

`delivery` проверяет доступность командного входа после настоящей подготовки Session.
`behavior` проверяет 33 сценария реальных процессов/MCP/SQLite/файлов, включая
настроенные argv/cwd/env, повтор и параллельный допуск, deadlines и cleanup,
раздельные большие и бинарные потоки, ненулевые exit, протокол и лимиты,
ошибки хранения/capture/удаления и область Session. Полные отчёты находятся в
выбранном каталоге; успешный stdout краткий. Эти проверки относятся к выбранному
локальному stdio binding, а не к доступности произвольного внешнего MCP-сервиса.
