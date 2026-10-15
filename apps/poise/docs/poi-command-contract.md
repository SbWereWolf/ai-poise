# Контракт команды Poise

Это согласуемый проект интерфейса T068, выбранный вариант B+O1. Провайдер,
асинхронный исполнитель, MCP-адаптер и чтение потоков ещё требуют реализации
и настоящей проверки в T069/T070. Нынешняя [оболочка](shell-poi.md) поддерживает
свой опубликованный срез; этот документ не включает в неё новые операции.
[Общий контракт Poise](contract-poi.md), особенно §5–7, владеет Session,
допуском инструмента и внешним конвертом `stride-poise/v1`.

## 1. Граница и владельцы

Команду можно запускать в самостоятельной установке Poise с действительной
Session и разрешённым инструментом. Task, Stage, Workspace, task-Orchestrator
и БД Stride для этого не нужны. Первый поддерживаемый транспорт — локальный
Linux CLI. Доступность MCP публикуется после проверки настоящего адаптера,
его ожидания и бюджетов, а не по наличию записи в конфигурации.

| Владелец | Ответственность |
| --- | --- |
| Допуск команды | Сохранить identity, исходный запрос и ревизию определения до dispatch; исключить второй dispatch при replay |
| Исполнитель | Один раз запустить child, наблюдать deadline/exit, закончить capture и сохранить фактический исход |
| Completion waiter | Ждать сохранённый terminal либо явно установленную неопределённость; сам child не запускает |
| Output owner | Сохранить полные отдельные stdout/stderr и полные поля результата; предоставить minimal/ranges |
| Gateway Poise | Проверить Session/permission/schema, доставить точный ответ провайдера и сохранить свой request outcome |

Существующие execution/output primitives используются или выделяются через их
владельцев. Здесь не назначаются будущие классы, worker loop или способ IPC.
Task policy остаётся у своего приложения. Нет общей транзакции с child или
с чужой БД. Один executor обслуживает одну identity; независимые команды
могут выполняться параллельно. Протокол не требует брокера или кластера.

## 2. Версии, личности и внешний конверт

Новый provider schema — `poise-command/v1`; это явный новый tool binding,
а не дополнительный формат старого payload. Зарегистрированное определение
инструмента предоставляет полные input/output JSON schemas этого договора.
`tool_id` выбирается явно из конфигурации. Ни alias, ни автоматический выбор
старого провайдера в этот договор не входят.

Каждый вызов сохраняет внешний `stride-poise/v1`, `operation="tool.invoke"`
и четыре поля input: `request_id`, `tool_id`, `tool_schema`, `arguments`.
`tool_schema="poise-command/v1"`; arguments содержит ровно `operation` и `input`.
Допустимые provider operations: `request`, `completion`, `minimal`, `ranges`.
Внутри arguments второй schema не дублируется: её уже выбрало определение.

Внешняя identity `(session_id, request_id)` обозначает один вызов gateway.
Командная identity `(session_id, command_id)` обозначает один запуск у
провайдера. Session передаётся доверенным адаптером из проверенного контекста;
provider input не принимает желаемую Session или смену роли. Ожидание использует
новый внешний request_id и прежний command_id. Менять arguments под прежним
внешним request_id нельзя: общий `request_reuse_conflict` сохраняет смысл.

`request.status` читает только запись своего gateway-вызова. Для вызова request
это может быть успешно доставленный admission, а не terminal child. Для вызова
completion это сохранённый ответ ожидания. Он не становится скрытым waiter.
Закрытая Session читает уже имеющиеся gateway receipts согласно общему договору;
новый tool.invoke выполняется при открытой Session. Штатный сценарий завершает
completion до закрытия Session.

## 3. Точные входы

Объекты имеют ровно указанные поля. Известные версии строгие: лишнее поле,
неверный тип, повторный JSON key и неограниченное/nonfinite число дают явный
отказ. Булево значение не является целым числом. Строки argv/env — без NUL.
command_id — непустой непрозрачный идентификатор в явном `id_bytes` лимите;
строки identity не содержат NUL. Потребитель не извлекает из них путь, время
или порядок. Все digests в этом договоре — 64 строчные hex-цифры SHA-256.

| Операция | Поля input |
| --- | --- |
| request | `command_id:string`, `argv:string[]` непустой, `cwd:string`, `env:object<string,string>`, `execution_budget_ms:integer>0`, `cleanup_budget_ms:integer>0` |
| completion | `command_id:string` |
| minimal | `command_id:string` |
| ranges | `command_id:string`, `stream:"stdout"|"stderr"`, `source_id:string`, `prefix_bytes:integer>=0`, `offset:integer>=0`, `limit_bytes:integer>0` |

argv[0] и cwd — явные абсолютные Linux paths. argv передаётся без shell
интерполяции. env — полный явный child environment; скрытого объединения с
окружением агента нет. Если программа использует PATH, он входит в env.
Разрешение инструмента и ограничения его определения проверяются до dispatch.
Нет автоматического создания cwd или исправления входной конфигурации.

request сохраняет canonical input и выбранные definition revision/digest,
исполняемый адаптер и конфигурацию бюджетов как provenance. Exact replay того
же command_id с тем же canonical input возвращает имеющуюся identity и её
текущее сохранённое состояние, `replayed=true`; второй worker не создаётся.
Другой payload даёт `command_reuse_conflict`. Admission не гарантирует
восстановление child после падения. Неустановленный dispatch/exit сохраняет
UNKNOWN и требует отдельной явной сверки владельцем.

Это provider replay, когда вызов действительно дошёл до провайдера. Exact
повтор всего внешнего пакета с прежним gateway request_id возвращает прежний
gateway receipt: внешнее replayed=true, исходный provider payload неизменен,
включая его собственный replayed и прежний admission snapshot. Новый внешний
request_id с тем же точным provider request позволяет прочитать текущий provider
snapshot с внутренним replayed=true. Ни один из вариантов не повторяет dispatch.

## 4. Состояние и ответы

Ответ request/completion/minimal имеет ровно поля:

`schema`, `operation`, `command_id`, `revision`, `state`, `termination`,
`deadline_exceeded`, `stdout`, `stderr`, `full_result_ref`, `error`, `replayed`.

schema равна `poise-command/v1`; operation повторяет вызов. revision — целое
от 1, увеличивается при изменении сохранённого командного наблюдения, включая
публикацию нового prefix. Чтение его не увеличивает. После TERMINAL исход,
revision и полные источники неизменны; replay читает тот же результат. state:

| Состояние | Значение |
| --- | --- |
| ADMITTED | Identity сохранена; dispatch ещё не подтверждён |
| RUNNING | Единственный executor подтвердил запуск; terminal ещё не сохранён |
| TERMINAL | Подтверждены завершение/отсутствие запуска, исход capture и запись результата |
| UNKNOWN | Полнота наблюдения исхода не установлена; сохранены все известные факты и bytes |

Обычный ход: ADMITTED → RUNNING → TERMINAL. Ошибка spawn до запуска даёт
TERMINAL с not_started и явной ошибкой. Потерянное наблюдение даёт UNKNOWN;
PID, отсутствие процесса и истечение waiter budget не доказывают child exit.
UNKNOWN меняется только через отдельно разрешённую сверку владельца, которая
сохраняет прежние receipts и не запускает команду повторно автоматически.

termination — null до установленного наблюдения либо один из точных объектов:

- `{ "kind": "exit", "exit_code": integer }`, exit_code от 0 до 255;
- `{ "kind": "signal", "signal": integer>0 }`;
- `{ "kind": "not_started" }`.

deadline_exceeded — boolean, факт наблюдения execution deadline владельцем.
Если deadline замечен раньше exit, executor инициирует штатное завершение
своего child и сохраняет реально наблюдённую termination. Даже последующий
exit0 не превращает этот timeout в успешную команду. Если exit наблюдён первым,
он является исходом команды. Неустановленное завершение после cleanup оставляет
UNKNOWN с null termination или с ранее достоверно сохранённым наблюдением.
В поддерживаемом Linux-сценарии остановка начинается с SIGTERM собственному
child; после explicit terminate_grace_ms применяется SIGKILL, если child ещё
не завершён. Остаток cleanup budget предназначен наблюдению завершения/capture.
Завершение не установлено — UNKNOWN, а не предположительный signal receipt.

error — null либо ровно `{code:string,message:string,details:object}`.
Ненулевой child exit сам по себе не является ошибкой протокола: он находится
в termination. Ошибка capture может дать известный TERMINAL failure с partial
источниками; она не превращает неполный поток в complete. Неподтверждённая
полнота наблюдения даёт UNKNOWN. UTC служит аудиту; deadlines и длительности
используют обязательный Clock и сопоставимые monotonic observations.

stdout/stderr — null, если источник ещё не создан, либо Source (§6).
full_result_ref — null до публикации полного receipt либо ровно
`{path:string,sha256:string,bytes:integer>=0}`. Это immutable UTF-8 JSON schema
`poise-command-result/v1` с точными полями `schema`, `command_id`, `revision`,
`state`, `termination`, `deadline_exceeded`, `stdout`, `stderr`, `error`,
`provenance`. Первые значения совпадают с сохранённым командным наблюдением;
provenance содержит ровно `session_id`, `command_input_digest`,
`definition_revision`, `definition_digest`, `configuration_digest`,
`admitted_at_utc`, `observed_at_utc`. Digests — SHA-256, даты — RFC3339 UTC;
исходные command/env значения сохранены у владельца, секретные env значения
не включаются в публичную provenance. Receipt ссылается на Source bytes,
не содержит самоссылки full_result_ref или пересказа. Terminal receipt
обязателен; UNKNOWN сохраняет доступный полный receipt, если его публикация
подтверждена. Отсутствие receipt не подменяется синтетическим JSON успеха.
replayed=true относится только к exact повтору request; у чтений false.

Отказ до допуска/чтения имеет отдельную точную форму:
`{schema:"poise-command/v1",status:"rejected",error:{code,message,details}}`.
Он не создаёт новый запуск. Неизвестный command_id даёт command_not_found,
а не новый admission. Чтение чужой Session не разрешается по одному ID.

## 5. Ожидание, CLI и границы исходов

completion ждёт внутри провайдера до TERMINAL/UNKNOWN. Один request → один
completion — обычный сценарий; модель не ведёт цикл request.status/RUNNING.
Waiter использует существующего executor, не создаёт второй и не удерживает
DB transaction на время child. Completion deadline не является child deadline.

Конфигурация `poise-command-config/v1` задаёт явно `state_root`, `request_store`,
`lock_path`, `output_root` и limits: `packet_bytes`, `id_bytes`, `lock_wait_ms`,
`lock_poll_ms`, `range_bytes`, `minimal_bytes`, `terminate_grace_ms`, `completion_budget_ms`,
`delivery_margin_ms`, `transport_budget_ms`, `transport_margin_ms`.
Все limits — положительные целые; terminate_grace_ms меньше cleanup_budget_ms.
Известные обязательные значения проверяются строго; неизвестные опции
игнорируются с сохранением их исходных значений. Пути явные; относительные относятся к
каталогу этого provider config. Работающие файлы конфигурации не коммитятся.
Новый schema не утверждает существование этого config parser в нынешнем CLI.

До dispatch/wait проверяются:

`completion_budget_ms >= execution_budget_ms + cleanup_budget_ms + delivery_margin_ms`

`transport_budget_ms >= completion_budget_ms + transport_margin_ms`.

Gateway provider_timeout_seconds и реальный host transport также вмещают
этот срок. Gateway provider_output_bytes/result limits вмещают полный provider
metadata response; большие child streams остаются полными у output owner,
а не помещаются в этот транспортный лимит. Нет молчаливого увеличения бюджетов.
Неподдерживаемый бюджет даёт configuration_error до dispatch/wait. Реальная
совместимость host/MCP проверяется в T069 до объявления адаптера доступным.

Полный пример будущего provider config для сценариев §8:

```json
{
  "schema": "poise-command-config/v1",
  "state_root": "/work/command-example/state",
  "request_store": "/work/command-example/state/commands.sqlite",
  "lock_path": "/work/command-example/state/commands.lock",
  "output_root": "/work/command-example/output",
  "limits": {
    "packet_bytes": 1048576,
    "id_bytes": 128,
    "lock_wait_ms": 2000,
    "lock_poll_ms": 10,
    "range_bytes": 1024,
    "minimal_bytes": 4096,
    "terminate_grace_ms": 100,
    "completion_budget_ms": 17000,
    "delivery_margin_ms": 1000,
    "transport_budget_ms": 19000,
    "transport_margin_ms": 2000
  }
}
```

Этот файл выбирает настроенное tool definition; Poise Session/caller/permission
задаются своим общим config. Для примера provider_timeout_seconds=30,
provider_output_bytes=8388608, result_bytes=8388608 вмещают срок и metadata.
Это документированная целевая конфигурация, не записанный рабочий файл.

При непредвиденном истечении waiter budget возвращается тот же snapshot
формата §4 с error.code=completion_budget_expired. state остаётся фактическим
ADMITTED/RUNNING/UNKNOWN; child автоматически не останавливается. Это не
terminal receipt. После устранения причины владелец допускает новое чтение
той же identity; повторный dispatch не является способом получить результат.

CLI провайдера возвращает: rejected=2; UNKNOWN/expiry=3; ошибка инфраструктуры
при известном исходе=1; наблюдённый deadline=124; обычный exit — его exit_code;
signal — 128+signal. У успешно доставленного ADMITTED/RUNNING и minimal/ranges
чтения exit0. Приоритет для completion и request с уже сохранённым исходом:
UNKNOWN/expiry, infrastructure error, deadline, затем обычная termination.
Typed payload сохраняется при nonzero.

Это сохраняет §5–7 общего gateway: provider_exit_code — настоящий exit CLI
провайдера, SUCCEEDED/FAILED — исход данного provider invocation. Completion
для child exit7 даёт provider_exit_code7/FAILED и сохраняет termination exit7
(F6). Request с доставленным admission может иметь gateway SUCCEEDED, пока
команда RUNNING. Minimal успешно читает даже command failure и имеет exit0.
Потеря самого transport без наблюдённого provider exit даёт gateway UNKNOWN
и provider_exit_code=null. Эти уровни не подменяют друг друга. Business verdict
в child stdout остаётся исходными bytes: exit0 с business FAIL не переписывается
в PASS и не вычисляет Task verdict. Gateway request.status возвращает receipt
именно соответствующего вызова, включая completion failure.

## 6. Полный источник, minimal и ranges

Source имеет ровно `source_id:string`, `path:string`, `prefix_bytes:integer>=0`,
`final_bytes:integer>=0|null`, `sha256:string|null`, `complete:boolean`.
source_id — непрозрачная стабильная identity одного stream данной команды.
path — явный owner-published путь bytes, не доступ к private DB. Пока capture
идёт, prefix_bytes означает уже опубликованный неизменяемый префикс;
final_bytes/sha256 равны null, complete=false. После полного закрытия capture
complete=true, final_bytes=prefix_bytes и sha256 — digest всех bytes. Известная
ошибка capture сохраняет partial prefix и complete=false даже при TERMINAL.

Output owner сохраняет streams отдельно, без вымышленного общего порядка.
Полные provider fields сохраняются без перефразирования. Machine consumer
читает полный metadata receipt и ровно опубликованные bytes обоих Source;
в terminal complete источнике это весь поток. Presentation budget не изменяет
эти данные. Active prefix identity — `(source_id,prefix_bytes)`; добавление bytes
не меняет уже опубликованный префикс. Это snapshot чтения, не обещание EOF child.

minimal возвращает фиксированную проекцию §4, без body streams и без обязательной
summary. Она ссылается на полный receipt и Source. Если целая metadata проекция
в виде фактически выдаваемого UTF-8 JSON с завершающим LF не помещается в
explicit minimal_bytes, возвращается view_budget_too_small;
поля/источник не обрезаются. Общий presentation owner применяет бюджет один раз;
provider не добавляет competing internal clipper к чужому полному ответу.

ranges читает выбранный ранее опубликованный prefix. source_id должен
принадлежать указанным command_id/stream; prefix_bytes не превышает текущий
published prefix. offset не превышает выбранный prefix; limit_bytes не больше
range_bytes. Ответ имеет ровно:

`schema`, `operation:"ranges"`, `command_id`, `stream`, `source_id`, `prefix_bytes`,
`offset`, `length_bytes`, `data_base64`, `next_offset`, `eof`, `source_complete`, `sha256`.

length_bytes=min(limit_bytes,prefix_bytes-offset); base64 хранит точные bytes,
next_offset=offset+length_bytes, eof=(next_offset==prefix_bytes). Пустой конечный
range при offset==prefix_bytes корректен.
source_complete=(Source.complete && prefix_bytes==Source.final_bytes); тогда sha256 — его полный digest,
иначе null. eof выбранного active/старого prefix не означает завершение команды.
UTF-8 может пересекать границу диапазона; consumer декодирует последовательность
инкрементально. Замена повреждённых или неполных символов не входит в протокол.

Повторное чтение того же prefix/range возвращает те же bytes. Отсутствующий
источник, несоответствие source_id или потеря подтверждённого prefix дают явный
отказ. Ошибка digest существующего immutable terminal source сохраняется как
evidence_mismatch; инструмент не чинит файл и не запускает child заново.

## 7. Ошибки и правильное продолжение

| Код | Наблюдение и действие владельца |
| --- | --- |
| configuration_error | Исправить явную конфигурацию/совместимый транспорт, затем выполнить корректный допуск |
| command_reuse_conflict | Использовать исходный сохранённый пакет для чтения его результата; отдельный запуск получает новую identity после разрешения |
| command_not_found | Использовать identity из действительного admission этой Session |
| provider_start_failed | Сохранён not_started и actual error; новый запуск требует отдельного намерения |
| completion_budget_expired | Сохранить фактический snapshot; установить причину ожидания и продолжить чтение существующей identity |
| observation_lost | UNKNOWN с известными bytes; явная отдельная сверка владельца |
| capture_failed | Сохранить partial bytes и настоящую termination/неопределённость; устранить причину storage failure |
| source_not_found / source_conflict | Выбрать Source из действительного ответа указанной команды и stream |
| range_invalid / range_limit_exceeded | Читать опубликованный prefix последовательными диапазонами в указанном лимите |
| view_budget_too_small | Явно настроить достаточный размер целой metadata проекции |
| evidence_mismatch | Сохранить исходный receipt и передать восстановление владельцу данных |
| store_busy / store_incompatible | Освободить удерживаемый ресурс его владельцем / выбрать поддерживаемый store через оператора |

details имеет ровно следующие поля для соответствующего code. ID/stream/state
используют типы выше; числа бюджетов/координат — целые; reason/option/path —
строки. Объект values у configuration_error допускает произвольные имена
с известными scalar string/integer/null значениями; в нём записываются все
фактические значения нарушенного бюджетного неравенства.

| Code | Поля details |
| --- | --- |
| configuration_error | `option`, `reason`, `values:object` |
| command_reuse_conflict / command_not_found | `command_id` |
| provider_start_failed | `command_id`, `reason` |
| completion_budget_expired | `command_id`, `completion_budget_ms`, `observed_state` |
| observation_lost | `command_id`, `phase:"dispatch"|"execution"|"capture"|"publication"`, `reason` |
| capture_failed | `command_id`, `stream`, `reason` |
| source_not_found | `command_id`, `stream` |
| source_conflict | `command_id`, `stream`, `source_id` |
| range_invalid | `command_id`, `stream`, `prefix_bytes`, `offset`, `limit_bytes` |
| range_limit_exceeded | `command_id`, `stream`, `prefix_bytes`, `offset`, `limit_bytes`, `range_bytes` |
| view_budget_too_small | `command_id`, `minimal_bytes`, `required_bytes` |
| evidence_mismatch | `command_id`, `source_id`, `expected_sha256`, `observed_sha256:string|null` |
| store_busy | `lock_wait_ms` |
| store_incompatible | `schema_version:integer|null` |

Недоступное наблюдение не заполняется нулём. При отказе provider parser до
чтения command применяются invalid_json с details `{}`, invalid_packet с
`{path:string}` или unsupported_schema с `{schema:string|null}`; эти три
случая также имеют rejected форму §4 и exit2.
Ошибки внешнего JSON/caller/Session/permission/schema принадлежат gateway §7.
Доступность инструмента не является разрешением запуска. Специальная защита от
враждебного администратора, восстановление конфигурации и автоматический ремонт
не являются предпосылками этого обычного локального протокола.

## 8. Независимые literal сценарии

Эти данные задают будущие проверки, а не отчёт о выполненных командах. Во всех
сценариях installation изолирована от Stride; Session открыта, tool разрешён,
cwd `/work/command-example` подготовлен, выбран Python `/usr/bin/python3`.
env в примерах ровно `{}`. Output roots задаются собственным config. Для L1
execution=15000, cleanup=1000, delivery_margin=1000, completion=17000,
transport_margin=2000, transport=19000 ms; terminate_grace=100 ms. Host CLI держит этот срок. Для остальных
используется тот же достаточный config; range_bytes=1024, minimal_bytes=4096.

L1 — один request и один completion для десятисекундной команды:

```json
{
  "schema": "stride-poise/v1",
  "operation": "tool.invoke",
  "input": {
    "request_id": "command-long-start",
    "tool_id": "command.local",
    "tool_schema": "poise-command/v1",
    "arguments": {
      "operation": "request",
      "input": {
        "command_id": "long-1",
        "argv": ["/usr/bin/python3", "-c", "import time; time.sleep(10); print('done')"],
        "cwd": "/work/command-example",
        "env": {},
        "execution_budget_ms": 15000,
        "cleanup_budget_ms": 1000
      }
    }
  }
}
```

```json
{
  "schema": "stride-poise/v1",
  "operation": "tool.invoke",
  "input": {
    "request_id": "command-long-wait",
    "tool_id": "command.local",
    "tool_schema": "poise-command/v1",
    "arguments": {
      "operation": "completion",
      "input": {"command_id": "long-1"}
    }
  }
}
```

После реализации/квалификации провайдера оператор явно регистрирует binding
`command.local`, открывает Session и сохраняет эти два пакета как request.json
и completion.json. Из корня поставки, с этим выбранным рабочим Poise config:

```bash
apps/poise/bin/poise --config /work/command-example/poise.json < request.json
apps/poise/bin/poise --config /work/command-example/poise.json < completion.json
```

До реализации T069 это образец целевого использования, а не команда нынешнего
среза. Два внешних request_id различны, внутренняя command identity одна.

Ожидаются одна durable command identity/один dispatch, terminal termination
`{"kind":"exit","exit_code":0}`, deadline_exceeded=false, stdout ровно UTF-8
`done\n` (5 bytes), stderr пустой (0 bytes), complete=true обоих Source и полный
immutable receipt. Между запросами нет модельного status polling. Exact повтор
первого внешнего пакета имеет gateway replayed=true и прежний provider payload;
число dispatch остаётся1. Новый gateway request_id с тем же provider request
проверяет внутреннее replayed=true и ту же command identity без второго запуска.

L2 — корректная команда с exit7 использует тот же request input, command_id
`exit-7`, argv `["/usr/bin/python3","-c","raise SystemExit(7)"]`, execution=15000,
cleanup=1000. Completion ожидает TERMINAL, termination exit7, пустые complete
streams, provider exit7 и gateway FAILED. Для business-result отдельно команда
`print('{"verdict":"FAIL"}')` имеет child exit0, неизменный stdout с FAIL,
provider exit0/gateway SUCCEEDED; consumer сохраняет business FAIL.

L3 — наблюдённый timeout: command_id `timeout-1`, execution=1000, cleanup=1000.
Python устанавливает SIGTERM handler с выходом23, публикует `ready\n` и ожидает:

```python
import signal
import sys
import time
signal.signal(signal.SIGTERM, lambda *_: sys.exit(23))
print('ready', flush=True)
time.sleep(10)
```

Test-owned готовность и Clock устанавливают deadline после публикации ready.
Исполнитель наблюдает deadline, завершает свой child и подтверждает точную
termination `{"kind":"exit","exit_code":23}`; state TERMINAL,
deadline_exceeded=true, stdout `ready\n` (6 bytes), provider exit124. Таким
образом проверяется настоящее завершение, а не только выставленный флаг timeout.
Если завершение
после cleanup не подтверждено, L4 требует UNKNOWN, а не этот terminal.

L4 — потеря наблюдения после admission: identity `unknown-1` уже существует,
один запуск подтверждён, terminal не квалифицирован. Детерминированное test-owned событие
потери наблюдения задаёт error observation_lost, state UNKNOWN, termination=null,
partial stdout bytes ровно `part` (4), stderr пустой, complete=false, final_bytes
и sha256 null. Completion/повтор request сохраняют uncertainty и один dispatch;
provider exit3 отличается от gateway UNKNOWN при потерянном самом transport
(provider_exit_code=null). Это моделирует обычный отказ, а не саморемонт.

L5 — большой многоязычный вывод: identity `large-1`, Python выполняет:

```python
import sys
sys.stdout.buffer.write(bytes.fromhex('d096f09f9982') * 100000)
sys.stderr.buffer.write(bytes.fromhex('6572720a') * 25000)
```

Независимый byte oracle: stdout — 100000 повторов UTF-8 `Ж🙂`, ровно600000 bytes;
stderr — 25000 повторов `err\n`, ровно100000 bytes. Complete sources и digest
проверяются по этим test-owned bytes. minimal не содержит тела; полный machine
read восстанавливает все700000 bytes, независимо от4096-byte presentation.
Последовательные ranges покрывают без пропусков/дублирования выбранный prefix.
Первый stdout range offset0/limit1 даёт data_base64=`0A==`, next_offset=1;
второй offset1/limit1 — `lg==`, next_offset=2. Их объединение декодируется в `Ж`.
На final offset600000 length0/eof=true/source_complete=true; stderr проверяется
как отдельный источник, а не часть вымышленного межпоточного порядка.

L6 — active prefix: stdout уже опубликовал bytes `part`, prefix_bytes=4,
complete=false, final_bytes/sha256=null. ranges offset0/limit4 возвращает
`cGFydA==`, next_offset4/eof=true/source_complete=false. Последующее добавление
`ial` не меняет повторное чтение выбранного prefix4. Новый minimal показывает
prefix7; terminal complete Source имеет final_bytes7 и SHA-256 bytes `partial`.

IDs, пути результата и revision получают из реального owner response и
проверяют на принадлежность/устойчивость, не назначают посредством gateway
payload. Literal expected bytes/states/exit/limits выше независимы от кода
будущего провайдера. T069/T070 переносят эти сценарии в свои test-owned fixtures
и проверяют настоящий producer и настоящего consumer, включая MCP boundary.

## 9. Проверка дизайна и последствия

| Обязательство T068 | Где оно определено |
| --- | --- |
| R01 / POI-07: command/completion/bounded stdout/stderr | §3–6, L1/L3/L5/L6: owner wait, полные sources, byte ranges |
| R02 / POI-02: самостоятельный вызов | §1–2, L1: Poise Session без Task/Stage/Workspace/Stride |
| DoD: долгий запуск/failure/timeout/large output без service polling/mandatory summary | §5–8, L1–L6, разделение provider/child/business исходов |
| DoD: договор, конфиг и команда документируются вместе | Этот документ: точные версии, поля, бюджеты, внешний invoke и правильные provider packets |

T068 изменяет только этот app-owned документ и содержательные Task sections.
Общий конверт/Session contract сохраняются. Реализация parser/store/executor/
waiter и реальная MCP qualification — T069; output preservation/minimal/ranges
и их qualification — T070. До них не заявляются executable provider capability,
background survival, выполненные long-run tests или завершение этих задач.
Дизайн проверяется чтением, literal сценариями и существующим documentation
checker с inbound links; maintained smoke относится к фактической ревизии
документа. Это статическая проверка дизайна, не end-to-end evidence провайдера.
