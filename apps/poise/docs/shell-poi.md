# Самостоятельная оболочка Poise

`bin/poise` устанавливает собственную Session для вызывающего, закрепляет её первую
роль и читает текущий статус. Для этих операций достаточно явной конфигурации,
CPython 3.13 со стандартной библиотекой и подготовленного собственного SQLite-хранилища.

Этот же вход обслуживает [события hooks и публичный внешний вызов](hooks-poi.md)
и [порционное чтение reader/2](poi-file-contract.md).
Эта страница — действующий договор трёх Session-операций. Подготовка состояния описана в
[договоре poise-state](state-poi.md), общие контекст, навыки, проверки возможностей
и навигация — в [договоре poise-generic](generic-poi.md). Более широкий
[проект Poise](contract-poi.md) сохраняет будущие возможности в своей области.

## Запуск

Из корня поставки запускайте существующий `apps/poise/bin/poise --config PATH`.
Исполняемый файл использует `python3` из `PATH`; для явного выбора интерпретатора
используйте `"$POISE_PYTHON" -I -S -B apps/poise/bin/poise --config PATH`.
Задайте `POISE_PYTHON` абсолютным путём выбранного CPython 3.13.
Относительный `PATH` относится к текущему рабочему каталогу; пути внутри конфига —
к каталогу этого конфига. Один вызов получает один JSON-пакет UTF-8 на stdin и EOF.
Ответ — один JSON-объект UTF-8 с завершающим LF и пустым stderr.

### Самостоятельный пример

Следующая последовательность создаёт отдельную временную установку. Обе конфигурации
из поставки выбирают один `../state/sessions.sqlite`. Выполняйте команды из корня
checkout. Выбранный `POISE_PYTHON` должен быть доступен в текущей оболочке.

```bash
POISE_EXAMPLE="$(mktemp -d)"
mkdir -p "$POISE_EXAMPLE/config"
cp apps/poise/config/poise.example.json "$POISE_EXAMPLE/config/poise.json"
cp apps/poise/config/state.example.json "$POISE_EXAMPLE/config/state.json"
"$POISE_PYTHON" - "$POISE_EXAMPLE/config/poise.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = json.loads(path.read_text())
config['caller'] = {'mode': 'generated', 'binding_file': '../caller.json'}
path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
PY
printf '%s\n' '{"schema":"poise-state/v1","operation":"prepare","input":{}}' |
  "$POISE_PYTHON" -I -S -B apps/poise/bin/poise-state \
    --config "$POISE_EXAMPLE/config/state.json"

poise_example() {
  env -u CODEX_SESSION_ID -u CODEX_THREAD_ID \
    "$POISE_PYTHON" -I -S -B apps/poise/bin/poise \
    --config "$POISE_EXAMPLE/config/poise.json"
}
printf '%s\n' '{"schema":"stride-poise/v1","operation":"session.ensure","input":{}}' |
  poise_example
printf '%s\n' '{"schema":"stride-poise/v1","operation":"session.assign_role","input":{"request_id":"assign-1","role":"author","authorization_ref":"operator-decision-1"}}' |
  poise_example
printf '%s\n' '{"schema":"stride-poise/v1","operation":"session.assign_role","input":{"request_id":"assign-1","role":"author","authorization_ref":"operator-decision-1"}}' |
  poise_example
printf '%s\n' '{"schema":"stride-poise/v1","operation":"session.status","input":{}}' |
  poise_example
```

`env -u` относится только к отдельному дочернему процессу этого примера: он использует
собственный generated caller. Native identity текущей оболочки остаётся прежней.
Подготовка возвращает версию `1`. Первый `ensure` возвращает `created=true`,
`origin="generated"`, `status="open"`, `revision=1`, `role=null`,
`role_assignment=null`, `locked=false`. Назначение возвращает `changed=true`,
`role="author"`, `revision=2`, `locked=true` и происхождение решения:

```json
{"request_id":"assign-1","authorization_ref":"operator-decision-1","assigned_revision":2}
```

Повтор возвращает `changed=false` с теми же сохранёнными фактами; `status` возвращает
ту же Session. Все четыре Session-вызова имеют `status="ok"` и exit `0`.
В рабочей установке сохраните каталог состояния и caller binding между запусками;
идентификатор Session берётся из ответа приложения.

## Явная конфигурация

Используйте [config/poise.example.json](../config/poise.example.json) как шаблон
собственного рабочего файла. Обязательны шесть полей верхнего уровня:

| Поле | Действующий смысл |
| --- | --- |
| `schema` | Ровно `stride-poise-config/v1` |
| `installation_id` | Постоянная непустая строка имени своей установки |
| `session_store` | Путь к собственному SQLite-хранилищу версии `1` |
| `caller` | Объект с обязательным `mode`: `native` или `generated` |
| `roles` | Непустой список уникальных непустых строк допустимых ролей |
| `limits` | Объект с обязательными `packet_bytes` и `sqlite_timeout_seconds` |

Все значимые строки содержат UTF-8 без NUL. `packet_bytes` — положительное целое
число, `sqlite_timeout_seconds` — конечное число от нуля включительно; булевы значения
для этих настроек не используются. В примере предел пакета равен `65536` байтам,
ожидание SQLite — `0` секунд. Для обычной совместной записи можно явно выбрать
подходящее время ожидания в своём конфиге.

Для `generated` дополнительно задайте непустой `caller.binding_file`. Неизвестные
опции, включая вложенные, игнорируются; приложение сохраняет исходный файл конфига
без перезаписи. Рабочие файлы конфигурации принадлежат оператору.

### Caller и постоянная идентичность

Для Session-операций, reader и `hook.intercept` в native-режиме запускайте Poise
в настоящем контексте хоста, который предоставляет
`CODEX_SESSION_ID` или `CODEX_THREAD_ID`. При наличии обеих переменных значения должны
совпадать и быть непустыми. Хост владеет этими значениями. Native-значение становится
первоначальным ID Session; уже сохранённая привязка имеет приоритет.

В generated-режиме используйте отдельный процесс без native-сигналов и явно выбранный
постоянный `binding_file`. Подготовьте его родительский каталог. Первый `session.ensure`
сначала проверяет подготовленное состояние, затем публикует полный файл привязки с
двумя полями: `schema="poise-caller-binding-1"` и непустым `caller_id`.
Сохранённый файл используется при следующих вызовах. Созданный ID Session непрозрачен
и устойчив для этой привязки. Для другого caller выберите собственный binding file.

Одновременное первое обращение сохраняет одну привязку и одну Session. Новая Session
возникает без роли. Чтение статуса и назначение роли используют существующую привязку;
для первоначального создания вызывайте `session.ensure`.

## Пути и владение состоянием

`session_store` и `caller.binding_file` разрешаются относительно каталога конфига;
абсолютные пути используются прямо. Постоянные `installation_id`, store и binding
определяют вашу установку и caller. Два конфига могут использовать независимые корни;
смена cwd при том же абсолютном `--config` сохраняет выбор установки.

Подготовьте тот же store через `bin/poise-state` с `target_version=1`, как в примере.
Операции Session читают подготовленную схему; её подготовка остаётся отдельной командой.
Poise хранит Session, её caller binding и первое назначение роли в своей БД.
Requirements владеет требованиями и формулировками задач; Sequencing — claims и
workspaces; Execution — этапами, проверками и результатами. Их конфигурации и БД
для этих трёх Session-операций не требуются.

Короткая SQLite-транзакция объединяет создание Session и привязки либо первое
назначение роли и увеличение редакции. Результат записи возвращается после COMMIT.
При обычном отказе записи изменения откатываются, затем запрос можно повторить.
Статус, существующий `ensure` и повтор уже назначенной роли читаются без допуска
SQLite-писателя. У Session сохраняются исходные inventory и происхождение назначения,
а также данные hooks, reader и cursor соответствующих владельцев.

## Операции и ответы

Пакет содержит ровно `schema`, `operation`, `input`; схема равна `stride-poise/v1`.
Вход целиком ограничен числом байтов UTF-8 в `limits.packet_bytes`, включая пробелы
и завершающий LF. Используйте три операции:

| Операция | `input` | Поля успешного `result` |
| --- | --- | --- |
| `session.ensure` | `{}` | `session`, `created` |
| `session.assign_role` | Ровно строки `request_id`, `role`, `authorization_ref` | `session`, `changed` |
| `session.status` | `{}` | `session` |

Каждая строка назначения непустая, `role` берётся из настроенного `roles`.
`authorization_ref` — ссылка оператора на решение; приложение сохраняет её как
происхождение назначения. Операция применяется к собственной Session caller.

Первое назначение открытой Session сохраняет роль и увеличивает редакцию на один.
Повтор той же роли, в том числе с новым `request_id` или `authorization_ref`, возвращает
`changed=false` и первоначальное происхождение решения. Для другой роли используйте
другую Session. `ensure` существующей Session возвращает `created=false` и её текущие
факты, включая закрытое состояние. Историческая роль остаётся читаемой после изменения
списка `roles`; новое назначение использует текущий список.

Session содержит ровно семь полей:

| Поле | Значение |
| --- | --- |
| `id` | Непустой ID сохранённой Session |
| `origin` | Сохранённое `native` или `generated` |
| `status` | `open` или сохранённое `closed` |
| `revision` | Целое число от `1` |
| `role` | Первая роль либо `null` |
| `role_assignment` | Сохранённый объект происхождения решения либо `null` |
| `locked` | `true` при наличии первого назначения, иначе `false` |

`locked` описывает закреплённую роль, а не общую блокировку работы.
Публичное закрытие Session относится к будущим возможностям. Уже сохранённое закрытое
состояние доступно для чтения; назначение выполняется для открытой Session.

Ответ всегда содержит ровно `schema`, `status`, `result`, `error`, `evidence`.
При успехе `error=null`, `result` содержит описанные поля. Обработанный отказ возвращает
`result=null` и ровно `error.code`, `error.details`, `error.action`.
В текущем интерфейсе `evidence=[]`. Соответствие статуса и exit code:
`ok` → `0`, `unavailable` → `1`, `rejected` → `2`.
Кодировка ответа UTF-8 сохраняется при любом `PYTHONIOENCODING` вызывающего.

## Диагностируемые отказы

Используйте `error.code`, существенные `details` и `action` для выбора следующего
правильного действия. Таблица описывает обработанные исходы текущего входа.

| `error.code` | Статус; данные; `action` | Правильное действие |
| --- | --- | --- |
| `state_not_found` | `unavailable`; `database`; `prepare:state` | Подготовить выбранный store через poise-state |
| `state_not_prepared` | `unavailable`; `version=0`; `prepare:state` | Выполнить подготовку версии `1` |
| `state_incompatible` | `unavailable`; фактическая `version`; `select:state` | Выбрать собственный store поддержанной версии `1` |
| `state_busy` | `unavailable`; `database`; `retry:state` | Дождаться завершения текущей SQLite-транзакции и повторить запрос |
| `state_io` | `unavailable`; фактический `reason`; `configure:state` | Настроить доступ к выбранному состоянию |
| `session_not_found` | `unavailable`; `{}`; `ensure:session` | Выполнить `session.ensure` для своего caller |
| `caller_io` | `unavailable`; фактический `reason`; `configure:caller` | Подготовить каталог binding file и доступ к нему |
| `invalid_caller` | `rejected`; `reason`; `correct:caller` | Выбрать согласованный native-контекст либо подготовленный generated binding |
| `identity_conflict` | `rejected`; `session_id`; `select:caller` | Использовать отдельный действительный caller-контекст |
| `role_not_allowed` | `rejected`; `role`; `select:role` | Выбрать роль из текущего настроенного списка |
| `role_locked` | `rejected`; `current_role`, `requested_role`; `select:session` | Использовать другую Session для другой роли |
| `session_closed` | `rejected`; `status="closed"`; `select:session` | Использовать открытую Session для назначения |
| `invalid_config` | `rejected`; `path`; `correct:config` | Заполнить известные настройки по этому руководству |
| `invalid_request` | `rejected`; `path`; `correct:request` | Сформировать точный пакет выбранной операции |
| `invalid_json` | `rejected`; `{}`; `provide:json` | Передать один корректный JSON-объект UTF-8 и EOF |
| `unsupported_schema` | `rejected`; строковая `schema` либо `null`; `select:schema` | Использовать схему `stride-poise/v1` |
| `packet_limit` | `rejected`; `limit="packet_bytes"`; `reduce:packet` | Передать пакет в настроенном байтовом пределе |

`invalid_caller.details.reason` различает `native_missing`, `native_invalid`,
`native_conflict`, `native_generated_conflict` и `binding_invalid`. Настройте один
выбранный способ идентичности. Для generated используйте целый постоянный файл
указанного формата, для native — значения настоящего контекста хоста.

## Поставленные операции и последующие задачи

Поставленный вход обслуживает три описанные Session-операции,
[hooks/interception](hooks-poi.md) и `reader.context/reset/read/next` по
[действующему договору reader/2](poi-file-contract.md). Для reader используйте
[полный пример конфигурации](../config/reader.example.json) и подготовленное
состояние той же Session. Общие контекст, навыки, пробы и навигация работают через
`bin/poise-generic`; подготовка схемы — через `bin/poise-state`.

Файловая политика, допуск инструментов, асинхронные команды и публичное закрытие
Session сохраняют свои границы разработки. Их проекты доступны в
[историческом общем договоре](contract-poi.md), [файловом договоре](poi-fs-contract.md)
и [договоре команд](poi-command-contract.md).

## Узкая проверка

Из корня checkout, с выбранным `POISE_PYTHON` и собственным каталогом
`POISE_SESSION_CHECK_OUTPUT` для полных отчётов:

```bash
"$POISE_PYTHON" -B apps/poise/tests/test_session.py --suite behavior \
  --output-root "$POISE_SESSION_CHECK_OUTPUT/session"
"$POISE_PYTHON" -B apps/poise/tests/test_shell.py --suite behavior \
  --output-root "$POISE_SESSION_CHECK_OUTPUT/shell"
"$POISE_PYTHON" -B apps/poise/tests/test_state.py --suite behavior \
  --output-root "$POISE_SESSION_CHECK_OUTPUT/state"
"$POISE_PYTHON" -B apps/poise/tests/test_shell.py --suite documentation \
  --output-root "$POISE_SESSION_CHECK_OUTPUT/documentation"
```

Шесть групп Session проверяют native/generated, изоляцию, конкурентные обращения,
обычные границы и сохранение существующих фактов. Успех: `status="passed"`,
`groups=6`, `executed=6`, `errors=0`, `failures=0`. Восемь shell-проверок сохраняют
точку входа, изоляцию, конфиг и байтовый/JSON-договор. Пять групп состояния проверяют
подготовку схемы и сохранение фактов. Четыре проверки документации подтверждают наличие
опорных терминов; смысл текста и исполнимость примера проверяются отдельно.
Полные отчёты — `session-details.json`, `shell-details.json`, `state-details.json`
в соответствующих явно выбранных каталогах.
