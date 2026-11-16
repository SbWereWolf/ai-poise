# Orchestrator public contract

Статус: целевой контракт `PS-DEV-002-T013` / `M01.T013`.
Протокол: `stride-orchestrator/v1`. Действующая синхронная оболочка `stride`,
её фактический формат настроек и самостоятельные команды описаны в
[инструкции Orchestrator](shell-orc.md#запуск-и-настройка).
Ниже сохраняется полный целевой договор, включая дальнейшие Role/Session,
`yield`/resume/deferred и асинхронный журнал; он не объявляет эти части реализованными.

## 1. Граница владельца и baseline

Orchestrator владеет адресами публичных команд приложений в собственном config,
единственной матрицей Role × Command, координацией публичных вызовов и собственным
журналом request/invocation/yield/result. Он выбирает inline/deferred выполнение,
сохраняет исходного вызывающего и возвращает ему точный результат инструмента.

Он не владеет канонической Task/requirement statement, satisfaction, Sprint,
claim, Workspace, process/evidence/acceptance или Session establishment. Эти
объекты принадлежат [Requirements](../../requirements/docs/contract-req.md),
[Sequencing](../../sequencing/docs/contract-seq.md),
[Execution](../../execution/docs/contract-exe.md) и Poise. Их публичные результаты
могут быть сохранены как immutable transport evidence, не как второй live store.
Приложения не получают доступ к чужой DB/config и не вызывают друг друга напрямую.
Нет обязательного broker, распределённой транзакции, автопланировщика или скрытого
создания Task во всех приложениях.

Исследованная база: `41a26c1ed3eac58b1428f7d4d7c395861f3f9b14`.
T006 `orchestrator-inventory.md` на `bd430a63` фиксирует reusable baseline:

| Существующий механизм | Использование при извлечении |
| --- | --- |
| `WorkTools.invoke`, `parse_request` | Строгие envelope/limits/error semantics; прямой импорт application internals заменяется configured command |
| Runtime/composition | Отдельные composition roots; не копировать монолит как cross-app authority |
| Work packet replay и immutable results | Сохранить точную корреляцию и конфликт reuse; не выдавать старую запись за новый запуск |
| `Session establishment`, `RuntimeAdapter` | Остаются в Poise; Orchestrator вызывает одну настроенную публичную команду Session/Role |
| Progression/handoff | Execution сохраняет решения этапов; progression или transcript cursor не считаются interprocess yield |
| Owner-local atomic writes | Intent/result записываются атомарно локально; внешние subprocess не объявляются SQLite-транзакцией |

Нормативные existing surfaces: [публичная пакетная работа](../../../docs/workflows/batch-work.md)
и [Task lifecycle](../../../docs/workflows/task-planning.md). Исторические тесты
инвентаризации не являются квалификацией будущего protocol. Контракт глобального
порядка асинхронного журнала ORC-05 остаётся T076/T077; здесь нет нового глобального
счётчика, tie-breaking clock или распределённого timestamp authority.

## 2. Типы и внешние packets

Один newline-terminated UTF-8 JSON object на stdin, один JSON object и `\n` на
stdout. Stderr — диагностика. Никакого shell source в protocol. Повторные JSON
ключи, nonfinite numbers, неверный UTF-8, лишнее value после packet недопустимы.
Верхние поля ровно `schema,operation,input`; schema точно `stride-orchestrator/v1`.

```json
{"schema":"stride-orchestrator/v1","operation":"invoke","input":{"request_id":"request-A","command_id":"requirements.query","packet":{"schema":"stride-requirements/v1","operation":"query","input":{"queries":[{"id":"q","kind":"get_task","task_id":"0042"}]}}}}
```

`Id` — непустая bounded строка без NUL; Task/path синтаксис валидирует его владелец.
`Digest` — 64 lowercase hex SHA-256. `Version` — integer>=1, bool запрещён.
`JsonObject` — конечный JSON object; вложенные keys принадлежат выбранному public
owner protocol, не полям Orchestrator. Неизвестные поля координационных форм
отклоняются. Canonical digest использует UTF-8 JSON с отсортированными object keys,
без пробелов и NaN, с порядком arrays без изменений. Payload request ID immutable.

| operation | Полные поля input | Назначение |
| --- | --- | --- |
| `invoke` | `request_id:Id,command_id:Id,packet:JsonObject` | Начать ровно одну явную команду |
| `resume` | `request_id:Id,job_id:Id,expected_version:Version` | Продолжить известную координацию, не прислать выдуманный ответ |
| `query` | `queries:Query[]` | Consistent read собственного ledger/разрешённого каталога |

Внешний packet **не** содержит actor/roles/Session для самоназначения, argv,
DB path, произвольный recipient, новый SessionProvider или Task creation plan.
Session/Role получается заново из configured command (§3), не из этих полей.

### 2.1 Ответ invoke/resume

Успешная координационная форма всегда имеет ровно:
`schema,status,request_id,job_id,version,origin,replayed,result,pending`.
`status=delivered|waiting|blocked`; `result` и `pending` nullable:

- `delivered`: `result:CommandResult`, `pending:null`; реальный owner response
  доставлен, это не Task acceptance и не обязательно business success;
- `waiting`: `result:null`, `pending:Pending`; сохранена работа без конечного result;
- `blocked`: `result:null`, `pending:Pending`; нужен внешний разбор неопределённого
  исхода/изменившейся relevant binding, не имитация завершения.

`Origin` ровно `{session_id:Id,caller_id:Id,request_id:Id,command_id:Id}`.
Это неизменный root call, в том числе когда отвечает внешний `resume` с другим
request_id. `job_id` и `version` принадлежат Orchestrator. Query не выдаёт чужие jobs.

`CommandResult` ровно `{invocation_id:Id,command_id:Id,exit_code:integer|null,
packet:JsonObject|null,disposition,diagnostic:DispatchError|null}`.
`disposition=returned|owner_rejected|owner_pending|transport_failed|outcome_unknown`.
Для первых трёх packet/exit_code обязательны и сохраняют фактический ответ owner;
для двух последних packet null, diagnostic обязателен, exit_code — реальный код
процесса либо null, когда он неизвестен. Неизвестный результат не становится отказом
без side effects. `diagnostic` не подменяет чужой error packet.

`Pending` ровно `{invocation_id:Id,command_id:Id,reason,continuation_version:Version}`,
`reason=deferred|yield_chain|budget_exhausted|outcome_unknown|command_binding_changed|limit_exceeded`.
Opaque app continuation token не возвращается произвольному вызывающему через
этот public response; он хранится с исходной invocation в ledger.

Exit Orchestrator: `0` для delivered/returned; `2` для coordinator rejection,
delivered/owner_rejected либо confirmed transport_failed; `3` для waiting/blocked
и owner_pending/outcome_unknown. Таким образом последующий отказ приложения не
маскируется успешным exit. Consumer обязан показывать disposition и packet/error,
а не трактовать `delivered` как «все бизнес-операции завершены».

## 3. Configured commands и Session/Role

Физический формат config реализует его будущий owner. Resolved contract обязан
предоставить собственные DB/lock/ledger, версии и атомарную публикацию конфигурации,
input/output/depth/step/time limits, sync/deferred scheduling policy и следующие
значения. Отсутствие обязательных настроек — `configuration_invalid`, не fallback
на старые пути или встроенный список приложений.

### 3.1 Один каталог команд

`commands` отображает CommandId на ровно:
`{protocol,argv,cwd,environment,timeout_ms,max_input_bytes,max_output_bytes,
returned_exit_codes,rejected_exit_codes,pending_exit_codes,resume_argv}`.

- `protocol=owner-json-v1|yield-json-v1`;
- argv nonempty string array; cwd/env/limits resolved из конфигурации; запуск без
  shell, input не интерполируется в argv/env/cwd;
- exit code arrays disjoint, integer и bounded; непредусмотренный code —
  transport failure, а не автоматически owner business refusal;
- `resume_argv` null для owner-json-v1, nonempty array для yield-json-v1;
  это возврат **той же команды**, не второй сервис с отдельной role matrix;
- конкретный descriptor получает digest resolved значений; secret env не выводится
  в user logs. Требуемые публичные схемы приложений проверяются их adapters/owners,
  не через импорт private model classes.

Для owner-json-v1 public packet передаётся приложению без чужого envelope. Ответ
и exit сохраняются без изготовления полей и interpretation Task-состояния. Напр.,
Requirements `apply/create_task` завершает именно эту операцию и ничего больше.
Для yield-json-v1 используется явный межпроцессный transport §5. Режим opt-in;
случайный ответ похожей формы от обычной команды не запускает другие приложения.

Новая совместимая реализация CLI заменяется в commands argv/config без изменения
кода Orchestrator. Новый вызов использует новый descriptor. Текущая continuation
не переносит старый token молча в другую command binding (§6).

### 3.2 Ровно одна матрица Role × Command

`role_commands` — RoleId → множество CommandId. Роли/команды произвольные configured
IDs, не фиксированные `author/reviewer` в коде. Команда разрешена, если хотя бы
одна текущая роль Session разрешает её в этой матрице. Unknown command и denied
command различны. Отсутствующая запись не даёт wildcard-доступ.

Проверка выполняется **перед каждым фактическим вызовом** root, yielded child и
resume original command, а также перед replay/read его сохранённого результата.
Новая role matrix задаётся явно владельцем конфигурации вне выполняемой Task,
не изменяется самообслуживающим packet. Вторая Role × Service/Tool матрица внутри
Orchestrator не вводится. Проверки Task/claim/approval внутри соответствующих
приложений остаются их предметной целостностью, не второй dispatch policy.

### 3.3 Одна настраиваемая команда Session/Role

`session_command` содержит явные argv/cwd/env/limits одной публичной команды,
первоначально Poise. Нет обязательного дополнительного приложения/класса
SessionProvider и нет чтения Poise DB/файла caller как частного внутреннего store.

Команда получает отдельный минимальный request:

```json
{"schema":"stride-session-context/v1","operation":"current","input":{}}
```

и возвращает точно:

```json
{"schema":"stride-session-context/v1","status":"current","session_id":"session-A","caller_id":"caller-A","roles":["worker"],"task_id":"0042","revision":"context-7"}
```

Физический exit Session-command обязан быть 0; stdout содержит ровно этот JSON
и newline, размеры/время ограничены configured limits. IDs/role array валидны
и без повторов, task_id nullable. Это **consumer port** к
публичному источнику Session, не новый владелец Session. Источник разрешает native
caller по своему trusted launch context; внешний invoke packet не выбирает
чужой caller_id. Совместимая команда может заменить Poise по этой форме только
настройкой. Ошибка/отсутствие контекста — `session_unavailable`, ни одного
application command не запущено. Эта bootstrap query не требует рекурсивной
Session query и не объявляется второй role matrix.

Приложения получают лишь установленные публичные provenance references через
configured invocation adapter; actor/authorization grant не возникает из строки
role в JSON. Они проверяют собственные authority records своим публичным boundary.
Correlating origin поля transport не являются bearer credential.

## 4. Ledger и внешние эффекты

Orchestrator хранит Job, Invocation, CallStep, YieldLink и неизменяемые results:

| Record | Обязательные поля/инварианты |
| --- | --- |
| Job | job_id, origin, initial_context_revision, current_version, root_invocation_id, phase, final_result_ref; root identity immutable |
| Invocation | invocation_id, job_id, parent_invocation_id nullable, command_id, descriptor_digest, input_digest, start_state, result_ref nullable |
| CallStep | step_id, invocation_id, action, yield_id nullable, descriptor_digest, exact_packet_digest, state, response_digest nullable; отдельная запись для каждого start/resume subprocess |
| YieldLink | parent_invocation_id, yield_id, continuation_digest/token, child_invocation_id; одно exact соответствие |
| Result | result_id, invocation_id, step_id, response_digest, CommandResult; final result только завершившейся invocation, yield response остаётся в CallStep |
| Request replay | current Session + request_id + canonical full packet digest → exact saved response |

`Job.phase=queued|running|waiting|blocked|delivered`; `Invocation.start_state=
reserved|started|yielded|returned|uncertain`; CallStep.state=`reserved|started|returned|uncertain`.
Job.version увеличивается при каждой атомарной смысловой записи Job/связанных
invocation/step; no-op и exact replay её не меняют. Общая coordinator revision
увеличивается один раз на mutating transaction, это не логический порядок ORC-05.
History/events здесь фиксируют координацию;
не воспроизводят приватные Task tables и не управляют чужой acceptance.

Перед внешним процессом атомарно сохраняются exact descriptor/input/caller binding
и reserved invocation/CallStep. Для start и каждого parent resume назначается
разный step_id, даже при неизменном invocation_id. Started intent конкретного
CallStep публикуется до запуска. Один Job не имеет двух параллельно исполняемых
шагов одной invocation; при ожидании внешнего процесса общий DB lock не удерживается. После него
успех
нельзя вывести из отсутствия процесса. Реальные stdout/exit/signal/timeout и их
hashes сохраняются в immutable owner result; limits проверяются до передачи/чтения.
Невалидный/truncated output не выдаётся за нормальный JSON result.

Atomicity только для собственных ledger writes. Crash после side effect и до
сохранения результата означает uncertain invocation. `resume` не повторяет такой
started call вслепую: `blocked/outcome_unknown`, никаких новых subprocess. В v1
этот исход требует отдельного явного owner/operator reconciliation; protocol не
принимает caller-придуманный result/receipt. Нужная позже import-receipt operation
должна получить отдельную спецификацию, не добавляется тайно к query/resume.
Это блокирует конкретный Job, не все Tasks, конфигурацию или другие requests.

Raw owner packet со своим request_id передаётся неизменным. Orchestrator replay
не отменяет требования owner idempotency и не даёт обещания глобального exactly-once.
Ответ owner-json-v1 с disposition owner_pending доставляется как есть, без скрытого
poll или запуска следующей stage. Новый public owner status request выбирает caller.

## 5. Межпроцессный start → yield → resume

Этот transport используется только CommandId с protocol yield-json-v1. Он не
pickle/generator state, не RPC к private Python объектам и не разрешение программе
самой вызвать peer executable. Новые schema принадлежат target transport, а не
объявляются существующим legacy WorkTools/advance.

### 5.1 Start и return

Configured argv получает:

```json
{"schema":"stride-call/v1","action":"start","invocation_id":"inv-A","step_id":"step-A-start","origin":{"session_id":"session-A","caller_id":"caller-A","request_id":"request-A","command_id":"analysis.run"},"input":{"task_id":"0042"}}
```

Точные поля: schema,action,invocation_id,step_id,origin,input. input — исходный JsonObject.
Origin и invocation ID устанавливает Orchestrator. Успешный terminal transport
response команды имеет точно:

```json
{"schema":"stride-call/v1","status":"returned","invocation_id":"inv-A","step_id":"step-A-start","result":{"exit_code":0,"packet":{"answer":"ready"}}}
```

result точно `{exit_code:integer,packet:JsonObject}`; semantic exit классифицируется
по descriptor. Физический exit transport при returned/yield — 0. Неожиданный
invocation_id/step_id/schema/field/physical exit даёт `protocol_error`; чужой Job
не меняется. step_id обязан совпасть с ожидающим CallStep, а не только с invocation.
При успехе returned физического шага с status=yield logical invocation становится
yielded, не returned; final CommandResult ещё отсутствует. status=returned даёт
final CommandResult и logical returned. Start-response, пришедший после начала
resume той же invocation, не принимается как resume-response.
Для owner-json-v1 эта обёртка не применяется.

### 5.2 Yield

Команда может вернуть ровно:

```json
{"schema":"stride-call/v1","status":"yield","invocation_id":"inv-A","step_id":"step-A-start","yield_id":"yield-A-1","continuation":"opaque-A-1","call":{"command_id":"requirements.query","packet":{"schema":"stride-requirements/v1","operation":"query","input":{"queries":[{"id":"q","kind":"get_task","task_id":"0042"}]}}}}
```

`yield_id` unique within invocation; `continuation` — nonempty bounded opaque string,
не путь/код для исполнения. call точно `{command_id,packet}`. Orchestrator сохраняет
YieldLink до child invocation, проверяет текущую Role × Command matrix для child,
назначает child ID и делает публичный вызов по собственной config. Parent process
не выбирает child argv, не читает соседний store и не знает его installation path.

Denied child не вызывается. Он получает coordinator-generated CommandResult с
disposition transport_failed и diagnostic command_denied; этот **отказ**, а не
выдуманный ответ приложения, может быть передан parent resume. Неявного повышения
роли вслед за yield нет. Перед resume также проверяется право на parent command.

### 5.3 Resume исходного обращения

После записи exact child result Orchestrator вызывает исходный resume_argv:

```json
{"schema":"stride-call/v1","action":"resume","invocation_id":"inv-A","step_id":"step-A-resume-1","origin":{"session_id":"session-A","caller_id":"caller-A","request_id":"request-A","command_id":"analysis.run"},"yield_id":"yield-A-1","continuation":"opaque-A-1","result":{"invocation_id":"inv-child-A","command_id":"requirements.query","exit_code":0,"packet":{"schema":"stride-requirements/v1","status":"read_only","revision":17,"results":[{"id":"q","value":{"task_id":"0042"}}]},"disposition":"returned","diagnostic":null}}
```

Точные fields: schema,action,invocation_id,step_id,origin,yield_id,continuation,result.
Приложение echo-ит этот новый step_id в своём returned/yield response. Повтор
точного response уже сохранённого шага является no-op; другой response digest
с тем же step_id — protocol_error, не новая версия финального результата.
result принадлежит **только** child данной parent/yield связи. И parent, и child
IDs проверены по неизменяемому ledger. Повтор того же yield с тем же digest не
создаёт нового child. Повтор ID с другим call/token — `continuation_conflict`.

Parent снова возвращает returned или новый yield; шаги сохраняются отдельно.
Budget/depth/time bounds ограничивают цепочку; превышение не означает успешное
завершение. Исчерпание per-dispatch step/time budget до следующего reserved шага
возвращает waiting/budget_exhausted. Hard max_depth/max_total_steps останавливает
цепочку с limit_exceeded; новые шаги, в том числе error-resume, сверх hard bound
не запускаются, Job становится blocked (pending reason limit_exceeded), outcome
ранее запущенных владельцев не откатывается. Timeout уже started шага может
иметь side effects и рассматривается как outcome_unknown, не safe budget pause. Возобновляется
конкретная recorded continuation, не другой native
session и не обращение, которое просто было последним в общей очереди.

## 6. Sync/deferred, replay и изменение конфигурации

Inline/deferred выбирается Orchestrator из своей explicit scheduling config.
Приложение может yield, но не выбирает другой scheduler, скрытый background daemon
или прямой peer call. При inline Orchestrator делает разрешённые шаги в пределах
budget; при deferred сохраняет queued Job и возвращает waiting. `resume` из исходного caller context
выполняет дальнейшие шаги. Отдельный
worker не получает authorization из сохранённой строки роли: он допустим только
при явном публичном источнике актуального контекста исходного caller. При
невозможности получить такой контекст ничего не запускается; v1 не требует
автономного worker и не специфицирует делегирование Session. Наличие worker/broker
не является обязательным требованием public contract.

```json
{"schema":"stride-orchestrator/v1","operation":"resume","input":{"request_id":"resume-A-1","job_id":"job-A","expected_version":3}}
```

Resume требует тот же origin Session/caller, актуальную роль и ожидаемую Job
version. Это не перенос Task между ролями/Session: реальный Task handoff выполняют
его владельцы, затем новый caller начинает свой root public request. Чужая Session
не получает старую Job по одному знанию job_id. Exact replay request возвращает
ранее сохранённую response до version check и не запускает работу снова;
новый request со stale expected_version отклоняется version_conflict.
Query показывает current Job, тогда как replay может вернуть старую saved waiting
response. Ни один из них не выдаётся за newly executed result.

Каждая invocation binds descriptor_digest; job origin не зависит от порядка
ответов. При resume повторно получают current Session/Role. Если изменились только
несвязанные config entries, это не причина блокировать Job. Если relevant command
binding изменилась, старый continuation не передаётся новой программе молча:
blocked/command_binding_changed. Fresh вызовы используют новую совместимую команду.
Отмена её permission в единственной matrix имеет силу перед следующим dispatch.
Нет общего требования завершить все Tasks для обычной конфигурационной правки.

## 7. Явность операций и частичный отказ

Создание Requirements statement/Task — одна explicit invoke. Оно не подразумевает
Sequencing registration, Execution setup, Workspace acquisition или acceptance.
Отдельная следующая команда может завершиться отказом. В этом случае первый
immutable response/Task ID остаётся, второй отказ возвращается отдельно; нет
скрытого rollback, компенсации, retry repair или «успешного комплексного создания».

Пользователь/агент сам выбирает дальнейшую операцию через публичный интерфейс.
Orchestrator не превращает текст цели в план, не добавляет tasks или дополнительные
owner calls из эвристики. Opt-in coroutine может запрашивать только явные public
commands; leaf Requirements.create не переименовывается в composite workflow.

Приложение на своём уровне может отказать из-за claim/approval/reference validity,
даже когда Orchestrator разрешил CommandId. Этот отказ не заменяется прямой записью
в чужой store и не считается второй Role × Service матрицей маршрутизатора.

## 8. Query и bounded status

Query input содержит nonempty bounded queries с уникальными Id. Все items видят
один coordinator store revision, без foreign read/repair/launch. Полные формы:

| kind | Поля после id,kind | value |
| --- | --- | --- |
| `job` | `job_id` | `{job_id,version,origin,phase,result:CommandResult|null,pending:Pending|null}` |
| `invocations` | `job_id` | `{items:[{invocation_id,parent_invocation_id:Id|null,command_id,start_state,current_step_id:Id|null,result:CommandResult|null}]}` |
| `commands` | нет | `{config_revision:Digest,allowed_command_ids:Id[]}` |

Response точно `{schema,status:"read_only",revision:integer,results:[{id,value}]}`.
Только current origin Session и разрешённые команды видят Job/results. Один
forbidden/unknown query item отклоняет весь packet, mixed partial success нет.
Query commands не раскрывает argv/env/secret context, только разрешённые IDs.
Конфигурационный источник ролей читается явно, а не дублируется локальным Session DB.

## 9. Ошибки и приоритет

Координационный rejection имеет форму:

```json
{"schema":"stride-orchestrator/v1","status":"rejected","error":{"code":"command_denied","message":"The current role cannot invoke this command","retryable":false,"details":{"command_id":"sequencing.register"}}}
```

`DispatchError` — exact `{code,message,retryable,details}`. Machine discriminator
только code; message предназначен человеку. Порядок: bounded JSON → exact packet →
configured Session/Role → command/Job authorization → exact request replay →
expected Job version/binding → local intent/public command. Таким образом denial
до child start, replay до stale version, разные payload reuse не пересекаются.
Нельзя получить owner result чужого request, обойдя авторизацию через replay.

| code | Условие | details |
| --- | --- | --- |
| `invalid_json` | UTF-8/JSON/duplicate/nonfinite | `{}` |
| `unsupported_schema` | Version mismatch | `{supported:string}` |
| `invalid_packet` | Unknown field/type/missing field | `{path:string}` |
| `limit_exceeded` | Admission input bound; runtime hard bound uses blocked/pending.limit_exceeded after a Job already exists | `{limit:string}` |
| `configuration_invalid` | Missing/incompatible required setting | `{field:string}` |
| `session_unavailable` | Configured context command failed/invalid | `{reason:string}` |
| `command_unknown` | CommandId отсутствует | `{command_id}` |
| `command_denied` | Single matrix не разрешает command | `{command_id}` |
| `origin_mismatch` | Current Session/caller не origin Job | `{job_id}` |
| `request_reuse_conflict` | ID занят другим canonical packet | `{request_id}` |
| `version_conflict` | Current Job version не expected | `{expected_version,actual_version}` |
| `job_unknown` | Job не существует в caller scope | `{job_id}` |
| `continuation_conflict` | Yield ID/token/call/result association отличается | `{invocation_id,yield_id}` |
| `protocol_error` | Unexpected application envelope/invocation/exit | `{invocation_id,reason}` |
| `command_failed` | Confirmed launch/output failure | `{invocation_id,reason}` |
| `store_busy` | Coordinator lock timeout | `{scope:string}` |

`store_busy` может быть retryable=true; deterministic contract/authority conflicts —
false. После started invocation `command_failed` не обещает отсутствие чужих side
effects. Неизвестный outcome — blocked с pending reason, а не fabricated safe failure.
`command_failed` с
disposition transport_failed применяется только когда известно, что процесс не
был запущен либо получен определённый отказ транспорта; неполный/несопоставимый
ответ после возможного side effect сохраняет diagnostic, но требует
blocked/outcome_unknown. Hard-limit blocked не даёт caller обходить bound
новым request_id; новый root не объявляется retry уже начатого effect.
Записи реального subprocess и его ошибки сохраняются; rejected routing не создаёт
Task-slices и не откатывает уже опубликованные independent owner results.

## 10. Literal fixtures

Fixtures ниже задают независимые исходные условия, invocations и exact predicates.
Configured role/command names — только значения примеров, не встроенная политика.
Сокращённые symbolic IDs — existing fixture records, не hash от нового текста.

### F1 — смена реализации команды только настройкой

Два config snapshots различаются только `commands.requirements.query.argv`:
`["/installation-A/requirements"]` и `["/installation-B/req-compatible"]`.
Применяется одинаковый первый invoke из §2. Session source возвращает worker,
единственная matrix разрешает requirements.query. В каждой установке запущен
ровно configured argv, одинаковый owner packet передан без изменений; никакого
Python import из Requirements и никакого изменения кода Orchestrator. Physical
paths здесь fixture parameters, не protocol constants.

### F2 — одна Role × Command matrix

Current roles `["reader"]`; matrix reader разрешает только requirements.query.
Для invoke command_id=sequencing.register routing rejects **command_denied** с
`details.command_id=sequencing.register`, ни одного application subprocess.
Тот же command при явно изменённой вне Task matrix reader→sequencing.register
допускается. Дополнительной Role × Service проверки нет. Owner может отказать
по своим Task references; это отдельный сохранённый application result.

### F3 — смена источника Session без нового приложения

Configured session_command сначала Poise, затем совместимая другая команда;
обе возвращают exact §3.3 context Session-A/worker. F1 dispatch одинаков. При
malformed/missing roles context — session_unavailable, не роль по умолчанию.
Добавление поля roles в внешний invoke — invalid_packet до запуска Session source.

### F4 — два одновременных yield не меняются результатами

Job-A/session-A/request-A запускает inv-A/yield-A-1 и child-A с Task0042;
Job-B/session-B/request-B запускает inv-B/yield-B-1 и child-B с Task0099.
Child-B возвращается первым, затем child-A. Resume для inv-A использует **только**
origin-A, token opaque-A-1, yield-A-1 и result invocation_id=child-A. Resume inv-B
использует только B. Последовательность завершения не является ключом корреляции.
Попытка привязать child-B result к A отклоняется continuation_conflict; ни один
parent не вызван с чужим результатом. Повтор exact A yield не создаёт child-C.

Для inv-A start step-A-start и resume step-A-resume-1 — разные физические calls.
Запоздалый start response с step_id=step-A-start не завершает ожидание
step-A-resume-1: protocol_error, current resume/result не изменены. Exact duplicate
уже записанного step-A-start response только подтверждает старую запись и не
порождает новый child. Новый ответ того же step_id с другим digest отклоняется.
При нормальном завершении resume возвращается exact новый step_id:

```json
{"schema":"stride-call/v1","status":"returned","invocation_id":"inv-A","step_id":"step-A-resume-1","result":{"exit_code":0,"packet":{"task_id":"0042","answer":"analysis complete"}}}
```


### F5 — Requirements success и отдельный Sequencing rejection

Первая explicit команда requirements.create получила успешный result:

```json
{"schema":"stride-orchestrator/v1","status":"delivered","request_id":"create-42","job_id":"job-create","version":2,"origin":{"session_id":"session-A","caller_id":"caller-A","request_id":"create-42","command_id":"requirements.create"},"replayed":false,"result":{"invocation_id":"inv-create","command_id":"requirements.create","exit_code":0,"packet":{"schema":"stride-requirements/v1","status":"applied","revision":6,"replayed":false,"results":[{"id":"create","kind":"create_task","task_id":"0042","statement_revision":1,"creation_replayed":false}]},"disposition":"returned","diagnostic":null},"pending":null}
```

Новый caller request отдельно вызывает sequencing.register с полученным0042.
Owner отвечает canonical identity conflict; Orchestrator возвращает:

```json
{"schema":"stride-orchestrator/v1","status":"delivered","request_id":"register-42","job_id":"job-register","version":2,"origin":{"session_id":"session-A","caller_id":"caller-A","request_id":"register-42","command_id":"sequencing.register"},"replayed":false,"result":{"invocation_id":"inv-register","command_id":"sequencing.register","exit_code":2,"packet":{"schema":"stride-sequencing/v1","status":"rejected","error":{"code":"identity_conflict","message":"Canonical provenance conflicts","retryable":false,"details":{}}},"disposition":"owner_rejected","diagnostic":null},"pending":null}
```

Первый response/ID/Requirements state не изменены; второй outer exit2 и error
видимы. Нет delete0042, повторного create, Execution setup или automatic repair.

### F6 — создание только Requirements не fan-out

Initial independent stores пусты, Session/Role допускает requirements.create.
Caller выполняет **только первую** операцию F5. Domain dispatch count=1,
Requirements выдаёт ID0042. Sequencing/Execution stores остаются пустыми.
Session context query учитывается отдельно и не создаёт domain Task slice.
Никакой post-create hook не выводится из command name или текста цели.

### F7 — сохранённый started без response

Job-U содержит inv-U start_state=started, итоговый response отсутствует после crash.
Публичный resume с правильной Session и version возвращает:

```json
{"schema":"stride-orchestrator/v1","status":"blocked","request_id":"resume-U","job_id":"job-U","version":3,"origin":{"session_id":"session-A","caller_id":"caller-A","request_id":"request-U","command_id":"requirements.create"},"replayed":false,"result":null,"pending":{"invocation_id":"inv-U","command_id":"requirements.create","reason":"outcome_unknown","continuation_version":3}}
```

Subprocess count остаётся1; coordinator не выдумывает safe retry/rollback.
Другие новые root requests могут исполняться. Query-U не меняет результат.

### F8 — stale resume и immutable replay

Job-A version4. New resume expected_version3 rejects version_conflict exactly
`{expected_version:3,actual_version:4}`. Exact ранее принятого resume ID/payload
возвращает его saved response/replayed=true и не запускает parent ещё раз.
Тот же ID с другим job_id/expected_version — request_reuse_conflict, если текущая
Session имеет право на заявленный target; чужой target раньше отклоняет origin check.

### F9 — context и relevant descriptor проверяются заново

Job-A принадлежит Session-A; resume из Session-B получает origin_mismatch без
parent/child calls. Если A больше не имеет role permission исходной команды —
command_denied. Смена несвязанного `commands.other` не блокирует A. Смена именно
A.resume_argv/descriptor даёт blocked/command_binding_changed, старый token не
передаётся новой реализации. Новый invoke использует новый descriptor (F1).

### F10 — denied child возвращён исходному parent

Parent analysis.run разрешён worker; yielded sequencing.register запрещён.
Child command не запущен. Parent resume получает CommandResult:

```json
{"invocation_id":"inv-child-denied","command_id":"sequencing.register","exit_code":null,"packet":null,"disposition":"transport_failed","diagnostic":{"code":"command_denied","message":"The current role cannot invoke this command","retryable":false,"details":{"command_id":"sequencing.register"}}}
```

Только origin parent/yield получает этот routing failure. Если и parent permission
отозван, он не вызывается: current external resume отклоняется command_denied;
история уже полученного child failure сохраняется.

### F11 — deferred и budget не создают ложное completion

Configured deferred mode, new invoke analysis.run создаёт queued Job-D без
application launch и возвращает waiting/deferred.

```json
{"schema":"stride-orchestrator/v1","status":"waiting","request_id":"request-D","job_id":"job-D","version":1,"origin":{"session_id":"session-A","caller_id":"caller-A","request_id":"request-D","command_id":"analysis.run"},"replayed":false,"result":null,"pending":{"invocation_id":"inv-D","command_id":"analysis.run","reason":"deferred","continuation_version":1}}
```
 Explicit resume исполняет
разрешённые шаги. Если depth/step budget исчерпан между сохранёнными yield links,
waiting/budget_exhausted, result null. Exact resume retry возвращает saved response,
новый resume с актуальной version продолжает только не started шаг. Брокер и
второй Task не требуются.

### F12 — consistent query не является repair

```json
{"schema":"stride-orchestrator/v1","operation":"query","input":{"queries":[{"id":"a","kind":"job","job_id":"job-A"},{"id":"b","kind":"invocations","job_id":"job-A"}]}}
```

Для известного waiting Job и единственной видимой invocation ответ:

```json
{"schema":"stride-orchestrator/v1","status":"read_only","revision":9,"results":[{"id":"a","value":{"job_id":"job-A","version":3,"origin":{"session_id":"session-A","caller_id":"caller-A","request_id":"request-A","command_id":"analysis.run"},"phase":"waiting","result":null,"pending":{"invocation_id":"inv-A","command_id":"analysis.run","reason":"yield_chain","continuation_version":3}}},{"id":"b","value":{"items":[{"invocation_id":"inv-A","parent_invocation_id":null,"command_id":"analysis.run","start_state":"yielded","current_step_id":"step-A-start","result":null}]}}]}
```

Обе projection видят один coordinator revision; query не стартует inv-A и не
читает Task DB. Unknown/foreign Job делает весь packet rejected без частичного
возврата чужого result. Command list query выдаёт только single-matrix visible IDs.

## 11. Проверки, impact и соответствие

| Task requirement | Design evidence |
| --- | --- |
| R01 / ORC-01 | §3.1, F1: configured public argv replacement, no internal imports |
| R02 / ORC-02 | §3.2, F2/F10: одна Role × Command, каждый root/yield/resume dispatch |
| R03 / ORC-03 | §3.3, F3: configured Session/Role command, без SessionProvider слоя |
| R04 / ORC-04 | §4–6, F4/F7/F11: start/yield/call/resume, immutable original caller, scheduling owned by coordinator |
| R05 / ORC-06 | §2.1/7, F5: точный видимый поздний отказ, ранний результат не откатан |
| R06 / GEN-05 | §1/7, F6: одна явная Requirements create, никаких скрытых Task slices |
| R07 / GEN-03 | §1/4/8, F12: own ledger/config, no foreign private store |
| R08 / GEN-04 | §2/3/6, F1/F9: явные argv/env/limits/roles, нет старого installation path в wire |

Оба DoD: literal success/error/partial/yield/replay fixtures independent of production
serializer; target config, invocation и error semantics документированы этой же
Task. Изменения executable/runtime config здесь нет и они не скрываются за README.

Узкая проверка: независимый parser literal JSON и exact discriminator/field sets;
маленькая fixture model для one Role × Command и parent/yield/child correlation;
проверка F1–F12 по всем восьми requirements; current local changed-document link
checker, git diff --check, шесть maintained app smoke. Связать evidence с точным
commit/tree; повторить после коммита. Broad/full discovery — только релизный этап.
Static/model checks не объявляют target provider реализованным. Configured same-role
inspection не называется независимым reviewer.

Извлечение затрагивает будущий apps/orchestrator adapter/config/ledger и вызовы
существующих owners вместо монолитной composition. Отдельно нужна реализация
public Session-context port и opt-in coroutine transport; legacy progression не
считается их готовой заменой. Не меняются канонические Requirements identities,
Sequencing/Workspace ownership, Execution gates/approvals и Poise native identity.

Основные риски: незавершённый subprocess и повтор side effect; stale role/descriptor;
перепутанные concurrent yield responses; ложный rollback cross-owner operations;
перенос private DB в coordinator. Они адресованы explicit intents/correlation,
current command authorization и видимыми unknown/partial outcomes, а не новой
распределённой инфраструктурой. Глобальная логическая сортировка журналов вне T013.
