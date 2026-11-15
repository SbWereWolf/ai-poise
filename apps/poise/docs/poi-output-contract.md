# Чтение сохранённого вывода Poise

## Назначение и поддержанное использование

Самостоятельный `apps/poise/bin/poise --config PATH` предоставляет
`output.minimal` и `output.range`. Нужны Linux/POSIX, CPython 3.13 со стандартной
библиотекой, явная конфигурация и доступные сохранённые обычные файлы. Во время
чтения сохраняйте содержимое источников неизменным. Один вызов получает один JSON
UTF-8 на stdin и EOF, возвращает один JSON UTF-8 с LF на stdout.

Обе операции работают без текущей Task, Stage, Workspace и баз Stride.
Общие поля конфигурации остаются обязательными по
[договору оболочки](shell-poi.md#явная-конфигурация), но для этих операций достаточно
их корректных значений: Session и её SQLite-хранилище, caller binding и состояние
команд подготавливать не требуется. Чтение выполняется до обращения к этим владельцам;
идентичность сессии или роль в пакет чтения не передаются.

Это действующий договор представлений вывода. Запись потоков, исполнение и удаление
команд принадлежат [владельцу команд](poi-command-contract.md).
[Reader/2](poi-file-contract.md) отдельно читает текст, ведёт курсоры и ACK;
`output.range` возвращает именно байты сохранённого файла.

## Адреса и сохранённые потоки

Используйте абсолютный `path` из сохранённой ссылки производителя stdout/stderr.
Если ссылка содержит записанное число байтов, передайте его как `captured_bytes`.
Для ссылки только с путём передайте `captured_bytes: null`. Файл каждого потока
адресуется отдельно, включая пустой stderr. Полный результат исполнения и его
исход принадлежат производителю; метаданные размера сами по себе описывают файл.

Источник имеет ровно поля `path` и `captured_bytes`. `path` — непустой абсолютный
путь без NUL; `captured_bytes` — целое от нуля или `null`. Порядок элементов
сохраняется, повторные обращения к тому же пути допустимы. Все поля и лимиты
пакета проверяются перед первым обращением к источникам. Хранилище команд,
сохранённые ссылки и исходные потоки продолжают принадлежать прежним владельцам;
нового индекса для чтения этих путей нет.

Каждый элемент результата содержит:

| Поле | Смысл |
| --- | --- |
| `path` | Переданный абсолютный адрес |
| `captured_bytes` | Переданный записанный размер или `null` |
| `bytes` | Наблюдённый текущий размер файла |
| `extent` | `same_size` при равных размерах; `shorter`/`longer` при меньшем/большем текущем размере; `unknown` при `captured_bytes: null` |

`same_size` означает совпадение размеров. `unknown` оставляет прежний размер
неизвестным. Это сведения о размере, а не подтверждение подлинности или исхода
команды. Источник читается по адресу без расчёта хэшей и восстановления содержимого.
При известном отличающемся размере доступные метаданные или выбранные байты
возвращаются с явным `output_extent_differs`, по правилам ответа ниже.

## Конфигурация и пределы

Скопируйте [полный пример](../config/output.example.json) в рабочий каталог вне
исходников. Общая схема — `stride-poise-config/v1`; раздел `output` имеет вид:

```json
{"output":{"schema":"poise-output-config/v1","limits":{"max_items":8,"max_total_range_bytes":65536,"max_response_bytes":131072}}}
```

Все три предела задаются явно положительными целыми. Значения в примере — выбор
оператора для примера. Boolean отличается от целого. Неизвестные опции раздела
и `limits` игнорируются и сохраняются в исходном файле; известные поля проверяются.
Настройки `output` выбираются только для `output.*`; другие семейства используют
свои настройки. Чтение сохраняет файл конфигурации без перезаписи.

| Предел | Единица и область действия |
| --- | --- |
| `max_items` | Число элементов `sources` или `reads` в одном непустом пакете |
| `max_total_range_bytes` | Сумма запрошенных `count` всех `reads` до уменьшения порций на границе EOF |
| `max_response_bytes` | Байты всего сериализованного ответа с непустым `result`: оболочка, JSON, UTF-8 и завершающий LF |

Отдельный общий `limits.packet_bytes` ограничивает входной пакет. Выбирайте
`max_response_bytes` с учётом метаданных, длины путей и base64: три исходных байта
занимают четыре знака base64. Метаданные не включают тела потоков;
порции читаются только в запрошенных пределах текущего файла.

Диагностические ответы с `result: null` освобождены от `max_response_bytes`.
Даже предел `1` допустим: вместо результата, который в него не помещается,
возвращается фиксированный `output_response_limit`. Размер этого диагностического
ответа может превышать `1`. Ошибки запроса и недоступного источника сохраняют
свои диагностики; этот предел не заменяет их ошибкой размера ответа.

## Метаданные output.minimal

Внешний пакет имеет ровно `schema`, `operation`, `input`; схема — `stride-poise/v1`.
Для `output.minimal` поле `input` содержит ровно непустой массив `sources`:

```json
{"schema":"stride-poise/v1","operation":"output.minimal","input":{"sources":[{"path":"/capture/run/stdout.bin","captured_bytes":6},{"path":"/capture/run/stderr.bin","captured_bytes":0}]}}
```

`result` имеет ровно `schema: "poise-output-minimal/v1"` и `sources`.
Элементы содержат четыре поля из таблицы адресов. Это ограниченные метаданные
без текстов stdout/stderr и без утверждения об успешности исходной команды.
Для получения содержимого выберите явные порции.

## Байтовые порции output.range

Для `output.range` поле `input` содержит ровно непустой массив `reads`.
Каждый элемент имеет ровно `source`, `start`, `count`:

```json
{"schema":"stride-poise/v1","operation":"output.range","input":{"reads":[{"source":{"path":"/capture/run/stdout.bin","captured_bytes":6},"start":1,"count":3}]}}
```

`start` — целое от нуля, смещение в байтах; `count` — положительное целое число
запрошенных байтов. `result` имеет ровно `schema: "poise-output-range/v1"` и `ranges`.
Каждый элемент содержит четыре поля адреса и:

| Поле | Смысл |
| --- | --- |
| `start` | Запрошенное смещение |
| `count` | Фактическое число возвращённых байтов |
| `data` | Ровно `{encoding: "base64", value: "..."}` с точными исходными байтами |
| `next_start` | Следующее смещение либо `null` при EOF |
| `eof` | Достигнут ли конец текущего файла |

NUL и произвольные байты сохраняются base64 без декодирования UTF-8.
Порция у конца файла уменьшается до оставшихся байтов; при `start == bytes`
возвращаются пустая base64-строка, `count: 0`, `next_start: null`, `eof: true`.
Для продолжения передайте `next_start` в новом запросе с выбранным `count`.
EOF относится к текущему файлу, а `extent` отдельно сообщает наблюдение о размере
относительно записи производителя.

## Ответы и продолжение

Внешний ответ содержит ровно `schema`, `status`, `result`, `error`, `evidence`.
Схема — `stride-poise/v1`. Успех: `status: "ok"`, `error: null`, `evidence: []`,
exit `0`. В `result` находится описанный результат выбранной операции.
Диагностика имеет `error: {code, details, action}`, `evidence: []` и обычно
`result: null`. `rejected` соответствует exit `2`, `unavailable` — exit `1`.

| Код | status / exit | details / action | Продолжение |
| --- | --- | --- | --- |
| `output_not_configured` | unavailable / 1 | `{}` / `configure:output` | Добавить явный раздел `output` |
| `invalid_config` | rejected / 2 | `{path}` / `correct:config` | Указать корректное известное значение в конфигурации |
| `invalid_request` | rejected / 2 | `{path}` / `correct:request` | Сформировать точный пакет по выбранной схеме |
| `output_read_limit` | rejected / 2 | `{limit}` / `reduce:output` | Выбрать число элементов и сумму порций в заданных пределах |
| `output_source_unavailable` | unavailable / 1 | `{index,reason,errno}` / `select:output` | Выбрать доступный сохранённый обычный файл; `reason` равен `io` с фактическим errno либо `not_regular` с errno `null` |
| `output_range_bounds` | rejected / 2 | `{index,start,bytes}` / `correct:request` | Выбрать смещение от нуля до текущего размера включительно |
| `output_extent_differs` | unavailable / 1 | `{indices}` / `inspect:output` | Использовать доступный результат и уточнить размер по данным производителя |
| `output_response_limit` | rejected / 2 | `{limit: "max_response_bytes"}` / `reduce:output` | Выбрать меньший пакет или увеличить явный предел с учётом размера JSON |

Индексы отсчитываются от нуля. При `output_extent_differs` возвращается полезный
результат целого пакета: метаданные либо прочитанные порции с `shorter`/`longer`.
Он также подчиняется пределу ответа. При ошибке доступа или границ одного элемента
возвращается соответствующая первая ошибка без результата частичного пакета.
Общие ошибки JSON и оболочки описаны в [договоре оболочки](shell-poi.md).

Для операционного сопровождения сохраняйте полные потоки и ссылки производителя
до окончания всех потребителей. Перед плановым исправлением конфигурации сохраните
её копию; используйте явные значения этого договора. Чтение само не изменяет
конфигурацию, источники или состояние приложений. Повторное получение отсутствующего
вывода выполняет владелец исходного исполнения; удаление — его штатная операция.

## Самостоятельный пример

Из корня checkout задайте `POISE_PYTHON` абсолютным путём установленного CPython
3.13, а `POISE_OUTPUT_EXAMPLE_ROOT` — абсолютным путём нового отдельного каталога
вне исходников. Пример создаёт два сохранённых потока и рабочую копию конфигурации,
после чего выполняет четыре чтения. Родитель `unused-state` остаётся отсутствующим.

```bash
"${POISE_PYTHON:?Укажите абсолютный путь CPython 3.13}" -I -S -B - \
  "${POISE_OUTPUT_EXAMPLE_ROOT:?Укажите абсолютный путь нового каталога}" <<'PY'
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
assert root.is_absolute()
root.mkdir()
entry = Path('apps/poise/bin/poise').resolve()
config = root / 'poise.json'
config.write_bytes(Path('apps/poise/config/output.example.json').read_bytes())
stdout = root / 'stdout.bin'
stderr = root / 'stderr.bin'
stdout.write_bytes(bytes.fromhex('4142ff0a0043'))
stderr.write_bytes(b'')
originals = {p: p.read_bytes() for p in (config, stdout, stderr)}
assert not (root / 'unused-state').exists()
source = {'path': str(stdout), 'captured_bytes': 6}
empty = {'path': str(stderr), 'captured_bytes': 0}
meta = {**source, 'bytes': 6, 'extent': 'same_size'}
empty_meta = {**empty, 'bytes': 0, 'extent': 'same_size'}
observations = []

def invoke(name, operation, payload, expected):
    request = {'schema': 'stride-poise/v1', 'operation': operation, 'input': payload}
    raw = (json.dumps(request, separators=(',', ':')) + '\n').encode()
    argv = [sys.executable, '-I', '-S', '-B', str(entry), '--config', str(config)]
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    child = subprocess.run(argv, input=raw, capture_output=True, env=env, check=False)
    (root / (name + '.request.json')).write_bytes(raw)
    (root / (name + '.stdout')).write_bytes(child.stdout)
    (root / (name + '.stderr')).write_bytes(child.stderr)
    observations.append({'name': name, 'argv': argv, 'environment': env,
                         'request': request, 'exit_code': child.returncode,
                         'stdout': child.stdout.decode('utf-8'),
                         'stderr': child.stderr.decode('utf-8')})
    (root / 'observations.json').write_text(json.dumps(observations, indent=2) + '\n')
    assert child.returncode == 0 and child.stderr == b''
    actual = json.loads(child.stdout)
    outer = {'schema': 'stride-poise/v1', 'status': 'ok', 'result': expected,
             'error': None, 'evidence': []}
    assert json.dumps(actual, sort_keys=True) == json.dumps(outer, sort_keys=True)
    assert child.stdout.endswith(b'\n') and child.stdout.count(b'\n') == 1
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert not (root / 'unused-state').exists()
    print(child.stdout.decode('utf-8'), end='')
    return actual['result']

invoke('minimal', 'output.minimal', {'sources': [source, empty]},
       {'schema': 'poise-output-minimal/v1', 'sources': [meta, empty_meta]})
middle = invoke('middle', 'output.range',
    {'reads': [{'source': source, 'start': 1, 'count': 3}]},
    {'schema': 'poise-output-range/v1', 'ranges': [{**meta, 'start': 1, 'count': 3,
     'data': {'encoding': 'base64', 'value': 'Qv8K'}, 'next_start': 4, 'eof': False}]})
assert base64.b64decode(middle['ranges'][0]['data']['value']) == bytes.fromhex('42ff0a')
end = invoke('end', 'output.range',
    {'reads': [{'source': source, 'start': 5, 'count': 4}]},
    {'schema': 'poise-output-range/v1', 'ranges': [{**meta, 'start': 5, 'count': 1,
     'data': {'encoding': 'base64', 'value': 'Qw=='}, 'next_start': None, 'eof': True}]})
assert base64.b64decode(end['ranges'][0]['data']['value']) == bytes.fromhex('43')
unknown = {'path': str(stdout), 'captured_bytes': None}
invoke('unknown', 'output.minimal', {'sources': [unknown]},
       {'schema': 'poise-output-minimal/v1', 'sources': [
        {**unknown, 'bytes': 6, 'extent': 'unknown'}]})
PY
```

Все четыре процесса завершаются exit `0` с пустым stderr. Первый возвращает размеры
`6` и `0` без тел потоков; второй — base64 `Qv8K`, три байта `42 ff 0a` и продолжение
`4`; третий — один байт `43`, base64 `Qw==` и EOF; четвёртый — размер `6` с
`extent: "unknown"`. Полные запросы, ответы и коды сохраняются в выбранном каталоге.
