# Самостоятельные события и внешние вызовы Poise

`apps/poise/bin/poise --config PATH` обслуживает `hook.event` и `hook.intercept`
в самостоятельной установке. Нужны POSIX/Linux, CPython 3.13 со стандартной
библиотекой, явная конфигурация и собственное подготовленное состояние версии `1`.
Операции работают с собственными Session и событиями. Управление задачами,
их базы данных и жизненный цикл принадлежат отдельным приложениям.

Это действующий договор hooks. Общие поля настройки, семь полей Session и прямой
caller описаны в [руководстве Session](shell-poi.md), подготовка состояния — в
[руководстве poise-state](state-poi.md), настройки наблюдений — в
[руководстве poise-generic](generic-poi.md). Самостоятельные асинхронные команды/MCP имеют
[действующий договор](poi-command-contract.md); общий gateway остаётся
[проектной областью](contract-poi.md).

## Подготовка и конфигурация

Подготовьте выбранный `session_store` через `bin/poise-state`. Используйте
[poise.example.json](../config/poise.example.json) как шаблон своего рабочего файла.
Для операций hooks дополнительно обязательна секция `hooks`:

| Поле | Значение |
| --- | --- |
| `id` | Постоянная непустая строка определения hooks |
| `events` | Уникальный список поддержанных событий, включающий `SessionStart` |
| `context_template` | Непустой шаблон контекста с полями `{session_id}` и `{workspace}` |
| `stop_template` | Непустой шаблон сообщения Stop с теми же двумя полями |
| `generic_config` | `null` либо путь к настройкам `poise-generic-config/v1` |
| `external` | `null` либо объект `argv`, `cwd`, `environment` внешней команды |
| `probes` | Явный список полных определений read-only проб; пустой список допустим |

Используйте в шаблонах ровно два указанных имени без спецификаторов форматирования
и преобразований. Значимые ID и пути — непустые строки UTF-8 без NUL.
Дополнительные параметры определения и внешней команды сохраняются в исходном
конфиге и игнорируются при выборе известных настроек.

`generic_config` разрешается относительно каталога основного конфига. Пути внутри
него, включая `results_root`, относятся к каталогу самого generic-конфига.
При заданных `external` или непустых `probes` укажите этот файл явно. Его поля,
права файлов и единицы лимитов определены в
[настройках generic](generic-poi.md#настройки-и-пути).

У внешней команды `argv` — непустой список аргументов, `cwd` — существующий
абсолютный каталог, `environment` — явный полный набор строковых переменных
дочернего процесса. Команда получает только переданный `request`. Для probes
используйте полный [договор проверки возможностей](generic-poi.md#проверки-возможностей).
Оператор выбирает read-only действия и их явные бюджеты; каждая проба укладывается
в общие `provider_timeout_seconds` и `provider_output_bytes`, пачка — в `batch_count`.
Настройки всей пачки проверяются до вызова внешней команды и проб.

`limits.packet_bytes` основного конфига ограничивает весь входной пакет в байтах
UTF-8, включая LF. `sqlite_timeout_seconds` задаёт ожидание короткой собственной
транзакции. Для конкурентной записи выберите явное положительное время ожидания;
значение `0` означает немедленный диагностируемый отказ при занятости SQLite.
Приложение использует выбранное значение и сохраняет рабочие конфиги без перезаписи.

## Идентичность и собственное состояние

Для `hook.event` выберите `caller.mode="native"`. Источник идентичности —
`event.session_id` настоящего события хоста. Фоновое окружение диспетчера,
включая `CODEX_SESSION_ID` и `CODEX_THREAD_ID`, на эту идентичность не влияет.
Интеграция передаёт событие одного native caller без поля `agent_id`.

Прямые `session.*` и `hook.intercept` используют другой источник: действительный
native-контекст вызывающего либо постоянный generated binding по
[договору caller](shell-poi.md#caller-и-постоянная-идентичность).
Для `hook.intercept` сначала установите свою Session через `session.ensure`.
Поля Session берутся из собственного состояния; `request` остаётся данными внешней
команды. Роль caller и роль этапа стороннего приложения имеют своих владельцев.

`SessionStart` сохраняет Session, её caller binding, привязку определения hooks и
первое событие одной короткой транзакцией. Уже существующая Session сохраняет
роль, редакцию, происхождение назначения и inventory. Данные других привязок,
событий, reader и cursor остаются у соответствующих владельцев.
Результат записи возвращается после COMMIT. Обычный отказ записи откатывает
целый результат первой доставки; после завершения конкурирующей транзакции можно
повторить ту же доставку с прежним `event_id`.

## Доставка событий

Конверт содержит ровно `schema`, `operation`, `input`; используйте
`schema="stride-poise/v1"`, `operation="hook.event"`.
`input` содержит ровно непустой `event_id` и объект `event`.
Хост сохраняет один ID для повторной доставки того же события.
В `event` обязательны `hook_event_name`, непустой `session_id` и `cwd` —
существующий абсолютный рабочий каталог.

| Событие | Дополнительные поля |
| --- | --- |
| `SessionStart` | `source`: `startup`, `resume`, `clear`, `compact` или `fork` |
| `UserPromptSubmit` | Непустой `turn_id` и строка `prompt`, в том числе пустая |
| `Stop` | Непустой `turn_id` |
| `SessionEnd` | `reason="other"` |

Используйте событие из настроенного `hooks.events`. Дополнительные метаданные
хоста игнорируются. Текст prompt и сообщения ассистента в состояние событий
не записываются. Сохраняются только `workspace`, `turn_id`, `source`, `reason`;
неприменимые значения представлены `null`.

Успешный `result` содержит:

| Поле | Смысл |
| --- | --- |
| `session` | Текущая собственная Session со всеми семью полями |
| `binding` | Непрозрачный `id`, собственный `session_id`, настроенный `definition_id` |
| `event` | Исходный `id`, целая `sequence`, имя `event`, выбранные `data` |
| `native_response` | Ответ для события хоста |
| `replayed` | Булев признак повторной доставки |

Для Start и Prompt `native_response.hookSpecificOutput` содержит
`hookEventName` и `additionalContext` из `context_template`. Для Stop возвращается
`systemMessage` из `stop_template`, для End — пустой объект.

Повтор того же `event_id` в привязке возвращает прежние событие, последовательность
и binding, `replayed=true` и **текущую** Session, включая уже назначенную роль.
Этот путь читает состояние без допуска SQLite-писателя. Одновременные доставки
с настроенным ожиданием SQLite создают один согласованный результат; второй вызов читает
его как повтор. Для дальнейших событий используйте уже начатую привязку.
Stop и SessionEnd записывают наблюдение; статус Session остаётся `open`,
завершение чужой Task выполняет её собственное приложение.

### Пакет из настоящего события хоста

Выберите `POISE_PYTHON` абсолютным путём CPython 3.13, `POISE_APP` — каталогом
поставленного приложения. Задайте `POISE_NATIVE_CONFIG` абсолютным путём native-конфига,
`POISE_NATIVE_EVENT_FILE` — файлом фактического JSON-события хоста,
`POISE_DELIVERY_ID` — устойчивым ID этой доставки. После подготовки состояния:

```bash
"$POISE_PYTHON" - "$POISE_DELIVERY_ID" "$POISE_NATIVE_EVENT_FILE" <<'PY' |
import json
from pathlib import Path
import sys

print(json.dumps({'schema': 'stride-poise/v1', 'operation': 'hook.event',
                  'input': {'event_id': sys.argv[1],
                            'event': json.loads(Path(sys.argv[2]).read_text())}},
                 ensure_ascii=False))
PY
  "$POISE_PYTHON" -I -S -B "$POISE_APP/bin/poise" --config "$POISE_NATIVE_CONFIG"
```

Этот адаптер собирает конверт из переданных хостом данных. Повтор используйте
с прежним ID и тем же исходным событием. Настройка доставки в конкретном хосте
принадлежит оператору. Проверка формы пакета на отдельной fixture подтверждает
локальный протокол и исполнение приложения; настоящую установку hooks подтверждает
фактическая доставка выбранного хоста.

## Публичный внешний вызов

Для `operation="hook.intercept"` передайте в `input` ровно `workspace` и `request`.
`workspace` — существующий абсолютный каталог для наблюдений; `request` — любой
JSON-объект публичного протокола выбранной внешней команды.
Приём использует существующую собственную Session, явно настроенные `external`
и generic-конфиг. До dispatch вход, состояние и вся конфигурация проверены;
предварительный отказ сохраняет прежние факты и каталог результатов.

Внешняя команда запускается один раз с точными `argv`, `cwd`, `environment`.
Её stdin получает полный `request` как UTF-8 JSON и LF, затем EOF.
Успешный вызов сохраняет фактический код и любой строгий JSON-ответ:
объект, массив, строку, число, `null` или `false`.
Значение `null` внутри `external.response` отличается от отсутствующего
`external` при ошибке наблюдения.

`result` содержит `session`, `external`, `capability_checks`.
При разобранном JSON `external` имеет ровно `exit_code` и `response`.
Код процесса `0` даёт верхний `status="ok"`, включая собственный отрицательный
business verdict команды. При другом коде сохраняется тот же `external`,
верхний статус — `unavailable`, ошибка — `external_unavailable`.
При timeout, недоступной команде, превышении размера или неразбираемом ответе
`external=null`; попытка сохраняет полный частичный `result` и фактические материалы.

Каждый вызов `hook.intercept` выполняет внешний запрос заново. ID внутри `request`
принадлежит внешнему публичному протоколу. После возможного внешнего эффекта
сначала установите его исход по публичному механизму вызываемого приложения;
продолжайте по его договору повторения. Poise не создаёт собственный request ledger
для этого среза и не читает закрытую базу внешней стороны.

## Наблюдения возможностей

После завершения основного вызова выполняются все настроенные command/MCP probes,
включая пути timeout и неразбираемого основного JSON. Сохранение наблюдений следует
за фактическим завершением и ожиданием дочернего процесса основного вызова.
Используйте [полное определение read-only проб](generic-poi.md#проверки-возможностей)
и [MCP stdio](generic-poi.md#mcp-stdio) из действующего generic-договора.

`capability_checks` содержит `ready`, `missing_required`, `observations`.
Недоступная обязательная проба делает `ready=false` и добавляет свой ID в
`missing_required`; необязательная сохраняет готовность остальных возможностей.
Полное наблюдение сохраняет доступность, причину, инструменты и квитанцию.
Пустая пачка возвращает `ready=true`, пустые `missing_required` и `observations`.

Верхние status/error и основной ответ остаются исходом внешней команды.
Готовность возможностей — отдельное наблюдение. Вход generic `capabilities`
имеет собственную политику допуска и может вернуть `capabilities_unavailable`;
`hook.intercept` сохраняет этот же смысл доступности внутри результата.

## Полный результат и правильное продолжение

Ответ — один полный UTF-8 JSON-объект `stride-poise/v1` с LF и пустым stderr.
Поля: `schema`, `status`, `result`, `error`, `evidence`.
Exit `0` соответствует `ok`, `1` — `unavailable`, `2` — `rejected`.
У ошибки ровно `code`, `details`, `action`. При отказе допуска `hook.intercept` перед dispatch
`result=null`; после попытки обычная ошибка сохраняет описанный частичный результат.

Каждый `evidence` содержит `kind` и абсолютный `path` фактического файла.
Прочитайте полные `stdout`, `stderr` и JSON `receipt`: там точные argv, cwd,
окружение, вход, фактический exit, timeout и пути потоков. Сохраните эти файлы вместе
с результатом внешней операции. При timeout остаются реально полученные байты;
процесс завершается и ожидается. `provider_output_bytes` проверяет полную сумму
stdout и stderr после сохранения потоков. Предел `result_bytes` generic CLI относится
к его собственному ответу; hooks возвращает полный результат без этого сокращения.

| Ошибка | Данные, действие и правильное продолжение |
| --- | --- |
| `hook_not_found` | `{}`; `start:hook`: доставьте SessionStart для своей привязки |
| `external_not_configured` | `{}`; `configure:external`: задайте публичную команду и generic-конфиг |
| `external_response` | `reason`; `configure:external`: проверьте полный stdout и согласованный JSON-договор команды |
| `external_unavailable` | Фактический `exit_code`; `configure:external`: прочитайте ответ команды и продолжите по её публичному договору |
| `provider_timeout` | `id="external"`, настроенный `limit`; `adjust:provider`: проверьте внешний исход, затем выберите подходящее время |
| `provider_unavailable` | `id="external"`, фактический `reason`; `configure:provider`: подготовьте выбранную команду |
| `provider_output_limit` | `id="external"`, настроенный `limit`; `adjust:provider`: прочитайте полные потоки и выберите подходящий предел |
| `io_unavailable` | Фактический `reason`; `configure:files`: подготовьте доступный каталог результатов |

Ошибки состояния, caller, конфигурации и входа имеют прежние точные коды и actions
из [руководства Session](shell-poi.md#диагностируемые-отказы).
Для события при `state_busy` дождитесь короткой транзакции и повторите прежнюю
доставку. Для внешней операции после начатого dispatch сначала выясните внешний
исход; приложение сохраняет факты, а рабочие настройки восстанавливает оператор.

## Правильные примеры и проверка

Следующий пример выполняйте из корня поставки. Задайте `POISE_PYTHON` абсолютным
путём CPython 3.13, а `POISE_EXAMPLE_PARENT` — своим существующим абсолютным
каталогом для отдельного примера. Команды создают рабочие файлы вне исходников,
готовят generated Session, вызывают собственный пример публичной команды и одну
read-only пробу. Скрипты примера — отдельная внешняя сторона, не новые API Poise.

```bash
POISE_APP="$(pwd -P)/apps/poise"
POISE_EXAMPLE="$(mktemp -d "$POISE_EXAMPLE_PARENT/hooks-example.XXXXXX")"
mkdir -p "$POISE_EXAMPLE/config"
cp "$POISE_APP/config/poise.example.json" "$POISE_EXAMPLE/config/poise.json"
cp "$POISE_APP/config/poise.example.json" "$POISE_EXAMPLE/config/native.json"
cp "$POISE_APP/config/state.example.json" "$POISE_EXAMPLE/config/state.json"
cp "$POISE_APP/config/generic.example.json" "$POISE_EXAMPLE/config/generic.json"
cat > "$POISE_EXAMPLE/target.py" <<'PY'
import json
import sys

request = json.load(sys.stdin)
print(json.dumps({'verdict': 'accepted', 'received': request}, ensure_ascii=False))
PY
cat > "$POISE_EXAMPLE/probe.py" <<'PY'
import json
import os

print(json.dumps({'checkout': os.getcwd(), 'message': 'READY'}, ensure_ascii=False))
PY
"$POISE_PYTHON" - "$POISE_EXAMPLE" "$POISE_PYTHON" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
python = sys.argv[2]
path = root / 'config/poise.json'
config = json.loads(path.read_text())
config['caller'] = {'mode': 'generated', 'binding_file': '../caller.json'}
config['limits']['sqlite_timeout_seconds'] = 1
config['hooks']['generic_config'] = 'generic.json'
config['hooks']['external'] = {
    'argv': [python, '-I', '-S', '-B', str(root / 'target.py')],
    'cwd': str(root),
    'environment': {'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
}
config['hooks']['probes'] = [{
    'id': 'example-readonly', 'kind': 'command', 'required': True,
    'argv': [python, '-I', '-S', '-B', str(root / 'probe.py')],
    'cwd': '${workspace}',
    'environment': {'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
    'timeout_seconds': 1, 'max_output_bytes': 4096, 'expected_exit_code': 0,
    'stdout_contains': [], 'stderr_contains': [],
    'json_assertions': [
        {'path': ['checkout'], 'equals': '${workspace}'},
        {'path': ['message'], 'equals': 'READY'},
    ],
    'mcp': None, 'tool_ref': 'example.readonly', 'project_bound': True,
    'remediation': 'Подготовьте доступный read-only пример.',
}]
path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
PY
printf '%s\n' '{"schema":"poise-state/v1","operation":"prepare","input":{}}' |
  "$POISE_PYTHON" -I -S -B "$POISE_APP/bin/poise-state" \
    --config "$POISE_EXAMPLE/config/state.json"
poise_example() {
  env -u CODEX_SESSION_ID -u CODEX_THREAD_ID \
    "$POISE_PYTHON" -I -S -B "$POISE_APP/bin/poise" \
    --config "$POISE_EXAMPLE/config/poise.json"
}
printf '%s\n' '{"schema":"stride-poise/v1","operation":"session.ensure","input":{}}' |
  poise_example
"$POISE_PYTHON" - "$POISE_EXAMPLE" <<'PY' | poise_example
import json
import sys

print(json.dumps({'schema': 'stride-poise/v1', 'operation': 'hook.intercept',
                  'input': {'workspace': sys.argv[1], 'request': {'id': 'example-1'}}}))
PY
```

Подготовка возвращает версию `1`. `session.ensure` возвращает generated Session
с `created=true`, пустой ролью и редакцией `1`. Последний ответ имеет `status="ok"`,
exit `0`; `external` содержит `exit_code=0` и `response` с `verdict="accepted"`,
`received={"id":"example-1"}`. `capability_checks.ready=true`, список обязательных
отсутствующих ID пуст, наблюдение `example-readonly` имеет `status="available"`.
Его `project_path` совпадает с каталогом примера. Конверт содержит ссылки `evidence` на реальные полные потоки и квитанции
под выбранным `results_root`; наблюдение содержит собственный `receipt`.
`env -u` изменяет только отдельный generated-процесс примера; внешний native caller
сохраняет свою идентичность. `native.json` остаётся отдельным native-конфигом для
доставки настоящего события по предыдущему разделу, с тем же подготовленным store.
Сохраните выбранные конфиги, caller binding и собственное состояние между вызовами.

Для проверки поставки из checkout с исходными тестами задайте отдельный
`POISE_HOOK_CHECK_OUTPUT`:

```bash
"$POISE_PYTHON" -B apps/poise/tests/test_hook.py --suite behavior \
  --output-root "$POISE_HOOK_CHECK_OUTPUT/behavior"
"$POISE_PYTHON" -B apps/poise/tests/test_hook.py --suite documentation \
  --output-root "$POISE_HOOK_CHECK_OUTPUT/documentation"
```

Шесть групп проверяют реальные отдельные процессы и собственные SQL-факты:
события, повтор/изоляцию/конкуренцию, публичный вызов, advisory пробы, обычные границы
и атомарность, сохранение прежних данных. Успех: `groups=6`, `executed=6`,
`errors=0`, `failures=0`. Четыре DOC-проверки — структурные сигналы наличия терминов;
смысл текста, исполнение примеров и локальные ссылки проверяются отдельно.
Полный отчёт каждого запуска — `hook-details.json` в выбранном каталоге.

## Владельцы реализации

`domain/hooks.py` содержит чистые определение, событие и binding.
`application/hooks.py` координирует доставку через собственные Session/state,
`application/interception.py` — один внешний вызов и последующие наблюдения.
`infrastructure/hooks.py` хранит собственные hook-строки через существующую
Session-транзакцию; `infrastructure/hook_configuration.py` читает выбранные настройки.
Команды, MCP и полный вывод используют прежних владельцев generic.
`application/capabilities.py` сохраняет общую агрегацию доступности, а каждый вход
применяет свою политику результата. `shell.py` связывает эти владельцы в один
публичный вход. Функции и состояния Stride остаются у соответствующих приложений.
