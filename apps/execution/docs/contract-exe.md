# Execution public contract

Статус: target design для `PS-DEV-002-T012` / `M01.T012`.
Версия публичного протокола: `stride-execution/v1`.

Это целевой контракт самостоятельного Execution. Реализованные интерфейсы
запуска, конфигурации, настройки исполнения и чтения описаны в
[руководстве оболочки](shell-exe.md), а хранение снимков, попыток, доказательств
и истории — в [руководстве собственного store](store-exe.md). Остальной документ
описывает целевой жизненный цикл и границы дальнейшего извлечения.

## 1. Владелец и существующий baseline

Execution владеет executable process: графом и материализованным шаблоном,
замороженным контрактом этапов, submission/candidate, file/value gate,
admitted evidence и durable check attempts, restart/rework и историей согласования,
перемоткой по актуальным доказательствам, результатом выполнения и owner-only
transfer. Он хранит собственные revision, request replay и publication intents.

Execution **не** выделяет канонический `task_id`, не правит requirement statement
или satisfaction, Sprint membership/dependencies, claim, Session или физическую
привязку Workspace. Requirements передаёт явный `task_id` и immutable snapshot;
Sequencing — admission/Workspace/claim references. Poise предоставляет установленную
идентичность вызывающего и роли. Вызвавший явно переносит результаты между
публичными интерфейсами. Нет private foreign DB reads, скрытого fan-out, broker,
общей транзакции приложений или второго владельца этих данных.

Baseline T005 — `execution-inventory.md`, зафиксированный на `bd430a63`;
исследованная база T012 — `d55ade74bca87981231b825dfdd7d8127d2624e5`.
Переиспользуются следующие владельцы, а не копия всего `TaskCommands`:

| Обязанность | Текущий владелец | Решение при извлечении |
| --- | --- | --- |
| Process/catalogue/template editing | `GoalTypeDefinition`, `GoalConfigCommands` | Один parser и предметные batch changes; новый transport adapter, не второй редактор |
| Stage/result lifecycle | `TaskStageContracts`, Execution-срез `TaskCommands` | Отделить внешние Requirements/Sequencing references |
| Evidence | `EvidenceBook`, `EvidenceCommands` | Сохранить immutable binding и полный batch admission |
| External checks | `CheckAttempts`, `RegisteredCheckRunner` | Сохранить reserve/start/record/finish, не повторять uncertain run |
| Restart | `restart_newborn`, `NewbornTask` | Та же Task, новые contract generation и явное согласование |
| Result integration/cleanup | `ResultIntegrationCommands`, `TaskResourceCleanup` | Сохранить отдельные intents и полномочия, не превратить acceptance в push/удаление |

Нормативные исходные формы: [редактор process](../../../docs/configuration/goal-config.md),
[evidence](../../../docs/workflows/evidence.md),
[Task lifecycle](../../../docs/workflows/task-planning.md),
[пакетная работа](../../../docs/workflows/batch-work.md).
Соседние границы: [Requirements](../../requirements/docs/contract-req.md) и
[Sequencing](../../sequencing/docs/contract-seq.md).

## 2. Wire envelope и скалярные типы

Один вызов принимает ровно один newline-terminated UTF-8 JSON object через stdin;
stdout содержит ровно один JSON response и `\n`. `argv`, окружение и пути берутся
из явной конфигурации установки, не из констант этого документа. Диагностика —
отдельный stderr, не подмена response. Успех exit `0`, отказ exit `2`, незавершённый
известный external intent — status `pending`, exit `3`.

```json
{"schema":"stride-execution/v1","operation":"query","input":{"queries":[{"id":"q1","kind":"execution","task_id":"0042"}]}}
```

Точный набор полей envelope — `schema`, `operation`, `input`. Версия точная;
неизвестная schema/operation/поле отклоняется. `input` — object, не строковый JSON.
Дубликаты ключей, NaN/Infinity, неверный UTF-8 и несколько JSON values недопустимы.
`operation` принадлежит `process|apply|gate|attempt|advance|query|transfer|integration`.

Общие обозначения типов далее — определения, не поля wire:

| Тип | Значение |
| --- | --- |
| `Id` | Непустая строка по configured identifier policy; без NUL, `/`, `\`, `.` и `..` как целого ID |
| `Revision` | Integer `>=0`, bool не integer; owner-store revision |
| `Ordinal` | Integer `>=1`, bool не integer; generation/iteration/snapshot revision |
| `Digest` | 64 lowercase hexadecimal символа; SHA-256 |
| `GitObject` | Полный object ID в явно configured repository format; не короткий ref и не `latest` |
| `RelPath` | Непустой нормализованный относительный путь без `..`, NUL и absolute/drive prefix |
| `JsonValue` | Конечное JSON value; сравнение по типу и значению, без string coercion |

В каждой таблице operation поля перечислены полностью. Поля без явно указанного
`optional` обязательны, nullable пишется явно. Вложенные object также не принимают
неизвестных полей. Ordered arrays сохраняют порядок; ID-наборы не допускают повторов.

Canonical request digest: UTF-8 JSON без пробелов, object keys lexicographically
sorted, arrays в исходном порядке, Unicode без ASCII-экранирования, без NaN.
Поэтому перестановка object keys — тот же payload, перестановка operations — другой.
Digest вычисляет Execution; caller не присылает собственный вердикт digest equality.

### 2.1 Успех и отказ

```json
{"schema":"stride-execution/v1","status":"applied","revision":8,"replayed":false,"results":[{"id":"r1","kind":"freeze_execution","changed":true,"task_id":"0042","generation":1}]}
```

Для owner `apply` точные success fields: `schema,status,revision,replayed,results`.
`query` имеет ту же форму со status `read_only`, `replayed:false` и result items
`{id,value}`. Остальные responses описаны у операций ниже. `revision` — snapshot
Execution store, не версия Requirements или Sequencing.

```json
{"schema":"stride-execution/v1","status":"rejected","error":{"code":"revision_conflict","message":"Execution revision changed","retryable":false,"details":{"expected_revision":7,"actual_revision":8}}}
```

Точные error fields: `schema,status,error`; `error` содержит только
`code,message,retryable,details`. `message` не machine discriminator. `details` —
object со схемой конкретного кода (§11), не произвольный authority token.

## 3. Конфигурация и доверенная граница

Execution config разрешает собственные DB/lock, process catalogue и immutable
artifact storage, response/attempt/transfer roots; byte/count/time/output limits;
command/method registry и явные env/cwd; поддерживаемые роли и authority resolver;
source/object formats; allowed roots и pattern policy. Изменение операционных путей
не меняет logical wire packets. Протокол не содержит список семи targets,
фиксированные stage/role names, installation paths или обязательную env variable.

Actor и effective roles поступают через настроенный invocation boundary;
`authorization_id`, `admission_id`, `workspace_id` в запросе — только references.
Они не предоставляют права сами по себе. Execution проверяет соответствие actor,
Task, stage/generation, разрешённому действию и scope ранее подтверждённого
authority record. Отсутствующий/устаревший admission отклоняется `admission_conflict`,
не заменяется чтением private Sequencing/Poise DB. Политика доставки/проверки
внешнего authority reference должна быть явно настроена; без неё `authorization_required`.

Замороженный `ExecutionContract.admission_id` сохраняет **первоначальную
provenance**, а не бессрочную текущую lease. Каждая mutation получает актуальный
admission в trusted invocation context; смена разрешённого actor/claim при
передаче между ролями не требует переписывать frozen contract. Старый reference
в импортированном контракте сам не даёт права возобновить работу.

User intent интерпретирует публичный вызывающий owner, а не произвольный текст
`reason` внутри JSON. В примерах роль — **условие trusted invocation fixture**, не
возможность прислать строку `reviewer` и назначить себе полномочия.

## 4. Process/catalogue — без нового Planner

### 4.1 ProcessDefinition и существующий редактор

`ProcessDefinition` — полный current `GoalTypeDefinition`:
`goal_type,stages,content_contract,route,benefit,worktree_required`.
Каждый stage содержит точные поля
`id,instruction,read_only,allowed_paths,normalization,sections,required_sections,
artifact_requirements,handler,role,transitions,rework_targets`.
Форма, ссылки/достижимость, terminal path, content/trace references и допустимые
handlers проверяются тем же parser, что при исполнении. Role задаётся на этапе,
не выводится из handler. Workflow не обязан повторять пример ниже.

`changes` переиспользует существующие ID-addressed editor operations:
`put_stage,patch_stage,remove_stage,patch_route,put_section,remove_section,
put_trace_route,remove_trace_route,put_requirement,remove_requirement,
set_benefit,set_worktree_required`. Точные формы — нормативный current editor
и его `GoalTypeDefinition.build`; финальная validation общая, а не новый набор
псевдополей. Полный process schema остаётся содержимым versioned process input,
не результатом сериализации приватной Task/Session.

Для явно нового графа target adapter принимает полную definition и передаёт её
тому же parser/publication owner. Legacy `goal-config-batch-1` create требует
template: это **расширение target входа**, не утверждение обратного о legacy CLI.
Шаблон выбирается только явно по `{id,version,digest}`; он материализуется в
независимый desired process, затем применяется `changes`. Пустой changes разрешён.

### 4.2 `process` inputs

Общие mutation fields: `action,request_id,expected_revision,goal_type`.

| action | Дополнительные поля | Результат |
| --- | --- | --- |
| `create` | `source,changes` | Новый process, конфликт existing goal_type не implicit replace |
| `edit` | `expected_process_revision:Digest,changes` | Новый current revision; активные frozen Tasks не меняются |
| `reconcile` | `expected_managed_revision:Digest,expected_live_revision:Digest,authorization_id:Id,reason:string` | Принятие exact внешней валидной публикации редактором, не перепись файла |
| `status` | Только `goal_type`; общие mutation fields **не** передаются | Read-only current/managed/live revision и pending request |

`source` — tagged union ровно одной формы:
`{kind:"definition",definition:ProcessDefinition}` либо
`{kind:"template",template:{id:Id,version:Id,digest:Digest}}`.
`edit` не имеет source/template. Target paths берутся из catalogue, не из запроса.
`status` schema input точно `{action,goal_type}`.

Process mutation success: `{schema,status:"published",revision,replayed,goal_type,
previous_process_revision:Digest|null,process_revision:Digest,changed:boolean}`.
Status success: `{schema,status:"read_only",revision,goal_type,
managed_revision:Digest|null,live_revision:Digest|null,pending_request:Id|null}`.

Файловая публикация сохраняет существующий pending intent, exact managed revision,
полную validation, fsync/atomic replace и immutable receipt. Crash не объявляется
DB/filesystem atomicity. Pending response:
`{schema,status:"pending",revision,intent_id:Id,phase:"process_publication"}`.
Exact retry завершает только этот intent при old/new bytes; чужой drift — отказ.

## 5. Execution contract и candidate

### 5.1 Contract

`ExecutionContract` — точный object:

| Поле | Тип / смысл |
| --- | --- |
| `task_id` | `Id`, canonical Requirements identity, неизменяем |
| `requirements_snapshot_revision` | `Ordinal`, явная provenance reference |
| `process_ref` | `{goal_type:Id,revision:Digest}` |
| `admission_id` | `Id`, explicit admitted work scope, не claim mutation |
| `workspace_id` | `Id|null`; null только если resolved process не требует Workspace |
| `stage_expectations` | Непустой список `{stage_id:Id,values:ValueRule[],files:FileRule[],methods:Id[]}`; ровно все stages |
| `restart_policy_id` | `Id`, configured approving authority/policy reference |
| `methods` | `MethodDefinition[]`, уникальные IDs существующего task-specific registry |
| `evidence_plan` | Object stage ID → `StageEvidencePlan`, ровно все stages |

Process snapshot и expectations сохраняются Execution. Requirement texts/history
не становятся вторым live registry; полный immutable input snapshot, необходимый
выполнению, обозначается происхождением и не может быть изменён здесь. Методы
и их schedules/inputs должны существовать в task-specific registry/evidence plan,
а не угадываться из пустого списка. Форма methods/handlers переиспользует текущие
валидаторы. Каноническая Task создаётся Requirements **до** `setup_execution`.

`ValueRule` точно `{name:Id,type,required:boolean,match}`;
`type` = `string|number|integer|boolean|object|array|null`;
`match` = `{mode:"any"}` либо `{mode:"equals",value:JsonValue}`.
`equals` сравнивает именно JSON type/value; строка `"2"` не число `2`.
Отсутствующее необязательное value не превращается в `null`. Необъявленные values
отклоняются. Нормализация строк берётся из замороженного stage (`exact|strip`),
применяется одинаково к expected и submitted values до comparison.

`FileRule` точно `{id:Id,root_id:Id,pattern:string,minimum:Revision,maximum:Revision,
sha256:Digest|null}`; `minimum<=maximum`, pattern ограничен declared root по
configured pattern policy. Exact digest, если задан, применяется к каждому
matched artifact. Expected absent result может иметь `minimum=maximum=0`.
Файл проходит и stage path restrictions, и configured source-root boundary.

### 5.2 Candidate и captured files

`Candidate` точно `{candidate_id:Id,task_id:Id,generation:Ordinal,stage_id:Id,
iteration:Ordinal,contract_revision:Digest,source:SourceIdentity,
values:object,files:FileRef[],evidence_work:EvidenceWork,stage_work:StageWork}`.
`SourceIdentity` точно `{repository_id:Id,commit:GitObject,tree:GitObject}` либо
`null` для явно source-free stage. `FileRef` точно
`{artifact_id:Id,root_id:Id,path:RelPath,sha256:Digest,bytes:Revision}`.
Artifact IDs и bytes подтверждает Execution artifact port, не caller flags.

Submission сохраняет данные кандидата и вычисленный `candidate_digest`, но не
объявляет результат проверенным. До gate Execution получает immutable captured
files через существующий artifact owner, сверяет реальные content hash/size/root
и source/contract identity, затем условно публикует verdict на той же актуальной
owner revision. Изменение файла между capture и verdict не может заменить
сохранённые bytes. Reuse/read вернут именно captured result, не поздний live path.

Недостающие значения/файлы, неверный hash, чужой path, incomplete/stale receipts,
открытые findings и несовместимый source не дают положительного gate. Failed
observations остаются историей, не accepted result.

### 5.3 Методы, аргументы и осмотр

`MethodDefinition` переиспользует current public method schema: точные обязательные
`id,argv,cwd,environment,expected_exit_code,stdout_contains,stderr_contains`,
а также explicit `source_under_test,verification_plan,outputs` для target v1.
`argv` — nonempty string array, `environment` — string map, cwd — configured
repository-relative root; не shell source. `source_under_test` точно
`{kind:"external",reason:string}` либо `{kind:"repository",bindings:[
{kind:"cwd",path:RelPath}|{kind:"environment",name:string,path:RelPath}]}`.
`verification_plan` точно `{responsibility:string,change_surface:RelPath[],
red_stages:Id[],green_stages:Id[],red_failure}`; failure null либо
`{exit_code:integer,stdout_equals:string,stderr_equals:string}`.
`outputs` — список `{id:Id,path:RelPath,required:boolean}`. Exact phase/source
семантика, запрет расходящихся schedules и разрешения запуска остаются в существующем
CheckRegistry/validator; env/run-output paths назначаются owner runner.
`stage_expectations.methods` является полным ordered check schedule этой stage.

`StageEvidencePlan` точно `{subject_methods,arguments,review_arguments}`.
`subject_methods` отображает method ID на
`{exit_codes:integer[],stdout_contains:string[],stderr_contains:string[]}`;
`arguments` — список `{id,kind:"logical"|"inspection",phase:"prepare"|"continue",
observation_methods:Id[]}`; `review_arguments` — Id[]. Сохраняется current
EvidencePlan parser: current subject methods только observe/check; inspect
принимает существующий argument, не создаёт предмет собственного осмотра;
неизвестные ссылки и PREPARE с требованием будущего observation отклоняются.

`Candidate` дополнительно содержит обязательные `evidence_work` и `stage_work`.
`evidence_work` точно `{phase:"prepare"|"continue",arguments:Argument[],
decisions:ArgumentDecision[]}`. `Argument` — exact variant current EvidenceBook:
для logical это `{id,kind:"logical",facts:string[],assumptions:string[],
inference:string,conclusion:string,verdict:"proved"|"disproved"|"inconclusive",
observation_ids:Id[]}`; для `kind:"inspection"` используется **тот же** набор
`facts,assumptions,inference,conclusion,verdict,observation_ids`, не отдельная
выдуманная структура. `facts` nonempty, observation IDs уникальны.
Передача argument сама не доказывает его истинность:
owner проверяет shape/binding, required review сохраняет отдельное решение.
`ArgumentDecision` точно `{argument_id,revision:Digest,decision:"accepted"|"rejected",
reason:string}`; exact immutable argument revision обязательна, роль должна
соответствовать configured inspect stage. Source actor identity хранит owner.

`stage_work` зависит от declared handler: для produce/check/observe — `{}`;
для inspect — `{coverage:string,findings:Finding[],resolution_decisions:Decision[]}`;
для revise — `{resolutions:Resolution[]}`. `Finding` точно
`{id,subject,description,evidence}`, `Resolution` точно
`{id,finding_id,description,evidence}`, `Decision` точно
`{resolution_id,decision:"accepted"|"rejected",reason}`. Все их строки непусты.
Новая inspection/revision хранится как отдельный candidate; изменённый candidate_id
не перезаписывает прежний. Gate проверяет required arguments/decisions вместе с
file/value expectations и текущими findings. Это существующие формы work/evidence,
а не разрешение вызвать другой handler из payload.

## 6. Owner-local mutation batch: `apply`

Input точно `{request_id:Id,expected_revision:Revision,operations:Operation[]}`.
Operations nonempty, configured bounded, уникальные `id`; результат того же порядка.
Все owner-state операции атомарны в одном store; недопустимая operation отменяет
весь batch. Данные чужих владельцев не меняются. Exact request replay проверяется
**до** stale revision проверки; другой payload с тем же ID — `request_reuse_conflict`.
Содержательное изменение всего batch увеличивает store revision на один; no-op
не увеличивает semantic revision, но сохраняет replay receipt.

| kind | Точные поля после общих `id,kind` | Success item после `id,kind,changed` |
| --- | --- | --- |
| `setup_execution` | `contract:ExecutionContract` | `task_id,generation,contract_revision` |
| `revise_execution` | `task_id,expected_generation,expected_contract_revision,contract:ExecutionContract` | `task_id,generation,contract_revision` |
| `freeze_execution` | `task_id,expected_generation,expected_contract_revision` | `task_id,generation` |
| `submit` | `candidate:Candidate` | `task_id,candidate_id,candidate_digest` |
| `propose_restart` | `task_id,expected_generation,proposal_id,proposed_contract:ExecutionContract,reason` | `task_id,proposal_id,proposal_digest` |
| `decide_restart` | `task_id,proposal_id,proposal_digest,decision,reason` | `task_id,approval_id,decision` |
| `restart` | `task_id,expected_generation,proposal_id,approval_id` | `task_id,generation,contract_revision` |
| `record_outcome` | `task_id,generation,outcome_id,kind_of_outcome,result_ref,authorization_id` | `task_id,outcome_id,kind_of_outcome` |

Здесь `expected_generation/generation` — Ordinal; `expected_contract_revision`,
`proposal_digest` — Digest; ID и reason непусты; `decision=approved|rejected`.
`kind_of_outcome=positive|negative|cancelled`; `result_ref` —
`{candidate_id,gate_id,commit:GitObject|null}` либо null. Positive требует exact
verified terminal candidate и настроенную acceptance authority; для negative и
cancelled result_ref null. Terminal не означает автоматически positive/completed.

`setup_execution` создаёт draft generation1 только в Execution. Другая уже
имеющаяся canonical identity/contract — `identity_conflict`, exact same setup
идемпотентен. `revise_execution` разрешён только в draft и в рамках frozen restart
approval после restart; current ready/active contract не редактируется на месте.
`freeze_execution` валидирует весь graph/expectations/registry и делает ready.
Process edit после freeze не меняет эту generation.

`submit` разрешён текущему author-role этапа на exact generation/iteration/contract.
Same candidate_id immutable по payload. Есть pending external attempt —
`attempt_pending`, а не уничтожение незавершённого запуска новым submission.

### 6.1 Restart: полномочия и история

`propose_restart` полностью валидирует желаемый contract и его references перед
сохранением proposal/digest; approval не может узаконить структурно неверный
graph или неизвестный method. Task ID и
Workspace binding не могут быть подменены. Изменённая requirement expectation
сначала публикуется Requirements и передаётся как новая explicit snapshot revision.
Execution не редактирует registry/statement своего соседа.

`decide_restart` вызывается actor с **настроенной согласующей ролью** по
restart_policy_id. Receipt связывает его реальную identity/role, proposal digest,
старую generation, reason и решение. Указание слова `approved` от другой роли
отклоняется `authorization_required`. Approval не означает Task completion.

`restart` требует approved exact proposal текущей generation. Terminal accepted
history не переписывается и не переименовывается в unaccepted. Для неподтверждённого
external attempt нужен текущий quiescence proof от владельца runner; неизвестный
живой процесс не abandoned автоматически. Результат — та же Task в draft,
generation+1, прежняя история/WIP/source binding, invalidated work packets/current
candidate. Согласованный contract применяется полностью; `revise_execution` не
может расширить согласованный patch без нового proposal/approval. `freeze_execution`
затем фиксирует проверенную generation. Это не replacement Task и не cancellation
в Sequencing. Старые PASS не превращаются автоматически в PASS нового source.

## 7. Проверки и file/value gate

### 7.1 `attempt`

Common mutation input: `{action,request_id,expected_revision,task_id,generation,
candidate_id,candidate_digest}` плюс поля таблицы.

| action | Дополнительные поля | Условие |
| --- | --- | --- |
| `begin` | `method_ids:Id[]` | Exact полный ordered schedule текущего этапа; список не выбирается произвольно caller |
| `run` | `attempt_id:Id,run_id:Id` | Разрешить один ещё не started run существующего intent; реальное исполнение owner runner |
| `finish` | `attempt_id:Id` | Все required immutable receipts уже записаны runner; caller не присылает `passed:true` |
| `status` | Только `task_id,attempt_id`; common mutation fields не передаются | Read-only pending/finished projection |

`begin` возвращает `{schema,status:"attempt_reserved",revision,replayed,attempt_id,
runs:[{run_id,method_id,started:false}]}`. `run` возвращает
`{schema,status:"run_recorded",revision,replayed,attempt_id,run_id,receipt_id}` либо
`{schema,status:"pending",revision,intent_id,phase:"check_run"}` при неопределённом
внешнем результате. `finish` возвращает
`{schema,status:"observed",revision,replayed,attempt_id,receipt_ids:Id[]}`.
Read-only status: `{schema,status:"read_only",revision,attempt_id,runs,
complete:boolean,uncertain:boolean}`. Каждый runs item содержит
`run_id,method_id,started:boolean,receipt_id:Id|null`.

Перед запуском сохранены method definition/version, exact argv/env/cwd/input,
source/contract/candidate digests, actual actor, execution key, timeout policy и
run ID. Started flag выдаёт однократное разрешение запуска; uncertain result не
повторяется автоматически и не конструирует receipt из exit текста. Полный
owner-bound receipt содержит actual exit/signal/timed_out и log hashes. Повтор
`run` при уже started без подтверждённого receipt возвращает `attempt_uncertain`.
Configured retries создают новую разрешённую attempt, не переписывают старую.
Нет физической атомарности между subprocess и SQLite.

### 7.2 `gate`

Input точно `{request_id,expected_revision,task_id,generation,stage_id,iteration,
candidate_id,candidate_digest}`. Gate сам читает current frozen expectations,
captured artifacts, полный набор admitted current evidence и findings/decisions.
Он не выполняет скрытый новый check-run и не принимает внешний `accepted` boolean.

```json
{"schema":"stride-execution/v1","operation":"gate","input":{"request_id":"g1","expected_revision":12,"task_id":"0042","generation":1,"stage_id":"draft","iteration":1,"candidate_id":"c1","candidate_digest":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}}
```

Success fields точно `{schema,status:"verified",revision,replayed,task_id,
candidate_id,gate_id,source,artifact_ids:Id[],receipt_ids:Id[]}`.
Gate records immutable verified candidate. Это **не** Task acceptance,
не stage transition и не вызов Requirements/Sequencing. Отказ не публикует
verified/accepted candidate; реальные failed attempts/audit сохраняются.

Совпадение file/value необходимо, но не заменяет evidence обязательства этапа.
Отсутствующий требуемый логический аргумент, отрицательный актуальный аргумент,
нерассмотренное исправление или required check receipt — `evidence_incomplete`.
Role separation инспекции сохраняется в source evidence; одинаковая configured
роль означает self-review и не называется независимой проверкой.

## 8. Переходы, перемотка и outcome

`advance` input точно `{request_id,expected_revision,task_id,generation,action,
target_stage}`. `action=next|fast_forward`; `target_stage=Id|null`.
Null означает ограниченное продвижение до первого этапа, требующего работы, либо
terminal graph node; все budgets берутся из current resolved process/config.
Явный target обязан принадлежать reachable graph; иначе `invalid_transition`.

`next` использует exact gate текущего stage/iteration и declared handler outcome.
Когда следующий этап не имеет требуемых доказательств, результат `work_required`,
а не исполнение за его роль. Если пришёл другой actor, transition не назначает ему
новую роль/claim; публичный вызывающий организует реальную передачу полномочий.

### 8.1 EXE-08: fast-forward после restart

Для каждой очередной stage Execution проверяет admissibility сохранённых receipt
и candidate против **актуальных** contract/source/inputs/expectations/methods,
актуальности required review decision и source actor separation. Неизменённость
целой старой generation не предполагается: сопоставляется конкретная stage
projection, а новая generation остаётся отдельной audit identity.

Если доказательства актуальны, новый traversal record ссылается на исходные
receipt IDs, исходные роли/actors, old/new contract-stage digests и доказанную
эквивалентность. Он **не** создаёт новый успешный check run или новую reviewer
identity. Роль инициатора не фильтрует такие подтверждённые переходы. Как только
данных для очередного этапа недостаточно, остановка на нём. Missing evidence
нельзя заменить совпадением текста отчёта, старым commit PASS или ролью инициатора.

Success fields точно `{schema,status:"advanced"|"work_required"|"terminal",
revision,replayed,task_id,generation,traversed:Traversal[],current_stage:Id|null,
required_role:Id|null,stop_reason}`.
`Traversal` точно `{stage_id,iteration,gate_id,evidence_origin_generation,
receipt_ids,source_actor_ids,contract_stage_digest}`.
`stop_reason=null|target_reached|unproven_stage|terminal|budget_exhausted`.
Budget stop не выдаётся за unsupported stage success; каждый traversal уже записан
в истории и exact replay возвращает тот же результат без повторных переходов.

Terminal graph node ещё не сам по себе positive accepted outcome. Публичная
acceptance authority явно вызывает `record_outcome`. Outcome immutable по ID и
payload, доступен query/transfer. `negative` или `cancelled` не удовлетворяют
Requirements и не создают `completed` факт в Sequencing. Полученный positive
outcome переносит публичный вызывающий; Execution не пишет соседний store.

### 8.2 `integration`: Git-публикация не равна acceptance

Сохраняется existing accepted-result owner, не реализуется другая Git-транзакция.
Input точно `{action,request_id,expected_revision,task_id,generation,outcome_id,
source_commit,target_ref,authorization_id}`; `action=prepare|publish|status`.
Для read-only status input точно `{action,task_id,request_id}`.
`target_ref` — configured target ID/ref, не arbitrary shell argv; `source_commit`
полный exact GitObject accepted positive result.

Prepare сохраняет intent и проверяет exact accepted source. Publish требует
отдельную authority, target lock и свежие узкие configured produced-result checks
на реально подготовленном source/target. Публикация только local fast-forward,
никакого `git push`. Dirty target, drift, несогласованный non-fast-forward или
незавершённая external operation сохраняют fail-closed intent, а не force reset.

Response точно `{schema,status:"integration_prepared"|"integrated"|"pending",
revision,replayed,intent_id,source_commit,target_commit:GitObject|null}`;
status query `{schema,status:"read_only",revision,intent_id,phase,
source_commit,target_commit:GitObject|null}`.
Physical Workspace removal принадлежит Sequencing; отдельная Execution cleanup
obligation/receipt передаётся ему явно. Интеграция не является скрытым разрешением
удалить грязный/чужой Workspace или чужую ветку.

## 9. Consistent queries

Input точно `{queries:Query[]}`, nonempty bounded, `id` непусты и уникальны.
Один read batch видит одну owner revision, не выполняет repair/migration,
не возобновляет pending attempts и не читает foreign state.

| kind | Поля после `id,kind` | `value` |
| --- | --- | --- |
| `execution` | `task_id` | `{task_id,generation,contract_revision,state,current_stage,iteration,workspace_id,admission_id}` |
| `contract` | `task_id,generation` | `{contract_revision,contract:ExecutionContract,process:ProcessDefinition}` |
| `candidate` | `task_id,candidate_id` | `{candidate:Candidate,candidate_digest,state,gate_id:Id|null}` |
| `gate` | `task_id,gate_id` | `{gate_id,candidate_id,candidate_digest,source,artifact_ids,receipt_ids,verified:boolean}` |
| `outcome` | `task_id,outcome_id` | `{outcome_id,task_id,generation,kind_of_outcome,result_ref,authorization_id}` |
| `restart_history` | `task_id` | `{entries:[{proposal_id,proposal_digest,approval_id,decision,source_generation,target_generation:Ordinal|null,actor_id,role_id}]}` |
| `history` | `task_id,after_event:Id|null,limit:Ordinal` | `{events:[{id,type,generation,subject_id,revision}],next_after:Id|null}` |
| `artifact` | `task_id,artifact_id` | `{artifact_id,root_id,path,sha256,bytes,capture_id}` |

`state` execution = `draft|ready|active|terminal`; candidate =
`submitted|verified|superseded`. Gate record `verified:true` только для выполненной
проверки владельцем, не caller input. Все identities принадлежат указанной Task.
Unknown identity => весь packet `not_found`; mixed partial query success нет.

## 10. Owner-only transfer

`transfer` input:

| action | Полный input после `action` |
| --- | --- |
| `export` | `request_id,expected_revision,task_ids:Id[],destination_id:Id` |
| `import` | `request_id,expected_revision,source_id:Id,package_digest:Digest` |
| `status` | `request_id` |

Source/destination IDs выбирают разрешённые configured transfer endpoints; это не
путь к чужой DB, не shell, не произвольный filesystem import.
Export snapshot содержит `stride-execution-slice/v1`: выбранные canonical task_ids,
Execution contracts/frozen processes, candidates/captured artifacts, accepted and
failed evidence, attempts/approval/restart/traversal/outcome/integration histories,
manifest и exact checksums. Requirements/Sequencing/Poise stores, claim authority,
Session credentials и private config secrets в него не входят.

Активный/неопределённый external check/integration/publication intent не
экспортируется как законченный: `transfer_incomplete`. Сам экспорт не освобождает
Sequencing claim и не меняет Session. Потребитель отдельно подтверждает owner handoff.

Import проверяет весь digest, точный inventory, schema/references, размеры/лимиты,
нормализованные пути без ссылок/escape, уникальность IDs и неизменяемость истории
**до** публикации. Коллизия с существующей Task identity — `transfer_conflict`;
auto-merge/overwrite нет. Staging и commit local import intent crash-recoverable.
После импорта saved results — история с original provenance, не новая приёмка,
claim или Session. Для нового выполнения нужен свежий explicit admission и
актуальная проверка environment/source; сохранённый actor не impersonated.

Export/import response точно `{schema,status:"exported"|"imported",revision,
replayed,package_id:Id,package_digest:Digest,task_ids:Id[]}`; pending
`{schema,status:"pending",revision,intent_id,phase:"transfer"}`; read-only status
`{schema,status:"read_only",revision,request_id,phase,package_id:Id|null}`.

### 10.1 Slice и file inventory

Логический manifest пакета имеет точные поля
`schema:"stride-execution-slice/v1",export_id:Id,source_revision:Revision,
task_ids:Id[],records:Record[],files:ExportFile[]`.
`Record` точно `{kind,task_id,generation:Ordinal,id:Id,revision:Revision,
sha256:Digest,body:object}`. `kind` перечисляет
`contract|candidate|gate|artifact|argument|argument_decision|attempt|restart|
traversal|outcome|integration|event`.
`body` — immutable public snapshot данного kind: соответствующий record из
§5–9; contract содержит definition вместе с frozen ProcessDefinition, argument
содержит exact Argument плюс owner-recorded actor/generation/stage/iteration,
attempt — owner-recorded run plan/receipts из §7.1. Это записи для валидации
и восстановления **истории**, не команды для повторного исполнения.

Record IDs уникальны по `{kind,task_id,generation,id}`. `sha256` проверяет canonical
body; generation и foreign reference continuity должны быть согласованы всеми
записями. Для argument/attempt/traversal нельзя импортировать новый текущий actor
или permission: их исходный subject/role — только source provenance. Неизвестный
kind или body, который не проходит соответствующий публичный validator, отвергает
весь пакет; private SQL row dump не является public body.

`ExportFile` точно `{path:RelPath,sha256:Digest,bytes:Revision,mode:Revision,
artifact_id:Id,task_id:Id}`. Пути — внутри package root, mode только regular-file
permissions без special bits; declared bytes/entry counts ограничены конфигурацией.
Каждый сохранённый artifact должен иметь ровно один inventory entry, ни один
необъявленный файл не принимается. Export включает все references, необходимые
для истории выбранных Tasks; недостающая связанная запись даёт `package_invalid`,
а не частичное восстановление. Никакие import hooks или archive scripts не выполняются.

## 11. Отказы и порядок проверки

Общий порядок: transport/JSON bounds → exact envelope/schema → typed packet →
trusted invocation/authority → exact request replay → current owner revision →
operation-specific identity/contract/evidence → mutation/intent. После exact
same-request replay старое expected_revision не превращает сохранённый успех в отказ.
Replay всё равно требует текущие read/replay полномочия caller; оно не выдаёт секреты
неавторизованному вызывающему. Request ID namespace относится к configured caller scope.

| code | Условие | `details` |
| --- | --- | --- |
| `invalid_json` | Невалидный UTF-8/JSON/duplicate keys/nonfinite | `{}` |
| `unsupported_schema` | Неизвестная wire или slice версия | `{supported:string}` |
| `invalid_packet` | Неверное/лишнее поле, тип или bounded shape | `{path:string}` |
| `unknown_operation` | Неизвестная operation/action/kind | `{value:string}` |
| `limit_exceeded` | Превышен configured bound | `{limit:string}` |
| `revision_conflict` | Owner revision устарела | `{expected_revision,actual_revision}` |
| `request_reuse_conflict` | Изменён payload существующего request ID | `{request_id}` |
| `not_found` | Объект не существует в owner scope | `{kind,identity}` |
| `identity_conflict` | Canonical Task/process ID занят другим содержимым | `{kind,identity}` |
| `process_invalid` | Shared process parser отверг graph/changes | `{issues:[{path,code,message}]}` |
| `process_revision_conflict` | Process/managed/live revision не совпала | `{expected:Digest|null,actual:Digest|null}` |
| `contract_conflict` | Generation/contract/candidate binding устарела | `{task_id,field}` |
| `authorization_required` | Нет configured approving/acceptance authority | `{task_id,action}` |
| `admission_conflict` | Admission/Workspace scope отсутствует или устарел | `{task_id,admission_id}` |
| `role_mismatch` | Caller не имеет требуемой роли для новой работы | `{task_id,stage_id,required_role}` |
| `result_mismatch` | File/value не соответствует frozen expectation | `{task_id,candidate_id,path,reason}` |
| `artifact_invalid` | File identity/hash/root/path capture неверен | `{task_id,artifact_id,reason}` |
| `evidence_incomplete` | Required current proof/decision отсутствует/отрицателен | `{task_id,candidate_id,missing:Id[]}` |
| `attempt_pending` | У кандидата уже есть внешний intent | `{task_id,attempt_id}` |
| `attempt_uncertain` | Started run не имеет достоверного final receipt | `{task_id,attempt_id,run_id}` |
| `restart_conflict` | Proposal/approval/current generation несовместимы | `{task_id,proposal_id,reason}` |
| `invalid_transition` | Нет допустимого перехода/target | `{task_id,stage_id,target_stage:Id|null}` |
| `outcome_conflict` | Outcome ID/payload противоречит immutable history | `{task_id,outcome_id}` |
| `integration_conflict` | Source/target/authority/precondition не совпали | `{task_id,intent_id,reason}` |
| `package_invalid` | Digest/inventory/path/schema invalid | `{reason}` |
| `transfer_conflict` | Import identity уже есть | `{task_ids:Id[]}` |
| `transfer_incomplete` | Выбранное состояние содержит uncertain intent | `{task_ids:Id[],intent_ids:Id[]}` |
| `store_busy` | Lock не получен в configured bounded time | `{}` |
| `store_incompatible` | Owner storage schema неизвестна | `{}` |
| `storage_failure` | I/O error до или после durable intent | `{intent_id:Id|null,phase:string}` |

`retryable:true` разрешён только `store_busy` или `storage_failure` с безопасным
same-intent recovery; остальные коды false. Никакой refusal не означает частично
принятый result. Owner apply отказ не оставляет частичные operations; внешние
observations/attempts/known intents сохраняются как audit и указываются явно.
Timeout/failed check — observed failed evidence, не подмена его `store_busy`.

## 12. Literal fixtures

Все JSON ниже — literal данные, не вывод production serializer. Где краткая
fixture задаёт условие словами, оно является обязательным precondition, а не
пропущенным обязательным wire field. Реальные Digest считаются из указанных bytes;
повторяющиеся hex в кратких fixtures обозначают уже существующие literal IDs
в initial owner store, не обещают hash произвольного нового текста.

### F1 — новый граф без навязанного pipeline

Initial store revision0, trusted process-editor actor, `proof_note` отсутствует.
Полная definition — отдельный stage с явной ролью, не фиксированная последовательность:

```json
{"schema":"stride-execution/v1","operation":"process","input":{"action":"create","request_id":"new-proof-note","expected_revision":0,"goal_type":"proof_note","source":{"kind":"definition","definition":{"goal_type":"proof_note","worktree_required":false,"benefit":{"git_categories":[],"sections":[]},"route":{"entry":"write"},"content_contract":{"sections":[],"routes":[],"requirements":[]},"stages":[{"id":"write","instruction":"Return the declared note.","read_only":false,"allowed_paths":[],"normalization":"exact","sections":{"report":"Provide the result."},"required_sections":["report"],"artifact_requirements":[],"handler":"produce","role":"author","transitions":{"complete":null},"rework_targets":["write"]}]}},"changes":[]}}
```

Expected `published`, revision1, goal_type `proof_note`, previous revision null,
changed true; process_revision equals canonical Digest всей definition. Shared
current GoalTypeDefinition parser обязан принимать эту complete definition.
Последующая Task использует revision1 snapshot, не скрытый template.

### F1a — полный setup и submission source-free этапа

После F1 store revision1; canonical Task0042/snapshot1 уже выданы Requirements,
trusted admission `admit-0042` и restart policy `rp-1` существуют. Source-free
process явно не требует Workspace. Полный target contract:

```json
{"schema":"stride-execution/v1","operation":"apply","input":{"request_id":"setup-note","expected_revision":1,"operations":[{"id":"setup","kind":"setup_execution","contract":{"task_id":"0042","requirements_snapshot_revision":1,"process_ref":{"goal_type":"proof_note","revision":"801e4104879807bc5d961c4f3bfe7d9ba05a97896d93621b1f4781419b63b9de"},"admission_id":"admit-0042","workspace_id":null,"stage_expectations":[{"stage_id":"write","values":[{"name":"report","type":"string","required":true,"match":{"mode":"equals","value":"Done"}}],"files":[],"methods":[]}],"restart_policy_id":"rp-1","methods":[],"evidence_plan":{"write":{"subject_methods":{},"arguments":[],"review_arguments":[]}}}}]}}
```

```json
{"schema":"stride-execution/v1","status":"applied","revision":2,"replayed":false,"results":[{"id":"setup","kind":"setup_execution","changed":true,"task_id":"0042","generation":1,"contract_revision":"682fabf2185d84f691913f9df863bb0036d0e5cb1a8c9250068868006eed0dd0"}]}
```

Contract revision вычислена независимым canonical JSON SHA-256 из literal
contract выше. После отдельного разрешённого `freeze_execution` этой exact
revision store revision3/generation1 ready. Полный submission:

```json
{"schema":"stride-execution/v1","operation":"apply","input":{"request_id":"submit-note","expected_revision":3,"operations":[{"id":"submit","kind":"submit","candidate":{"candidate_id":"note-c1","task_id":"0042","generation":1,"stage_id":"write","iteration":1,"contract_revision":"682fabf2185d84f691913f9df863bb0036d0e5cb1a8c9250068868006eed0dd0","source":null,"values":{"report":"Done"},"files":[],"evidence_work":{"phase":"prepare","arguments":[],"decisions":[]},"stage_work":{}}}]}}
```

Expected applied revision4 с exact computed candidate_digest; это submitted,
не verified/accepted. FileRef/SourceIdentity отсутствуют здесь **явно** как empty
files/source null, потому что данный stage source-free. Gate затем сверяет
`values.report == "Done"` и отсутствие незаполненных evidence obligations.

### F2 — материализованный шаблон редактируется независимо

Initial revision1; catalogue содержит exact `note-template/version=1/digest=aaa…`.
Он имеет stage `write`, и это подтверждённое fixture input, не автопоиск template.

```json
{"schema":"stride-execution/v1","operation":"process","input":{"action":"create","request_id":"template-note","expected_revision":1,"goal_type":"template_note","source":{"kind":"template","template":{"id":"note-template","version":"1","digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}},"changes":[{"op":"patch_stage","id":"write","set":{"instruction":"Return a materialized note."}}]}}
```

Expected `published` revision2. Original template bytes/digest неизменны;
materialized process отличается только explicit goal_type/instruction.
Exact replay returns same process_revision, `replayed:true`, revision2.
Два patch одной instruction в одном packet => `process_invalid`, issues.code
`conflicting_change`, нет публикации. Нет нового Planner/скрытых stages.

### F3 — matching value и file проходят gate

Initial revision12; Execution Task0042 generation1 stage `draft` iteration1,
contract revision `bbb…`, candidate `c1` digest `ccc…`. Frozen values правило:
`count` integer required equals2; file rule требует один `result.txt` в root`out`
с SHA-256 bytes `ok
`. Exact captured artifact`a1`, known receipt`r1` и gate authority
имеются; source-free stage разрешён. Candidate source null. Matching value `2`
и real captured bytes проходят gate запрос §7.2.

```json
{"schema":"stride-execution/v1","status":"verified","revision":13,"replayed":false,"task_id":"0042","candidate_id":"c1","gate_id":"gate-c1","source":null,"artifact_ids":["a1"],"receipt_ids":["r1"]}
```

Verified candidate хранит captured bytes и value. Изменение позднего live
`result.txt` не изменяет artifact`a1`. Никакой Sequencing terminal fact не записан.

### F4 — mismatch не становится принятым результатом

Та же отдельная initial fixture revision12, candidate`c2` содержит строку`"2"`
вместо integer2 и корректный файл. Запрос:

```json
{"schema":"stride-execution/v1","operation":"gate","input":{"request_id":"bad-value","expected_revision":12,"task_id":"0042","generation":1,"stage_id":"draft","iteration":1,"candidate_id":"c2","candidate_digest":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}}
```

```json
{"schema":"stride-execution/v1","status":"rejected","error":{"code":"result_mismatch","message":"Value type does not match the frozen expectation","retryable":false,"details":{"task_id":"0042","candidate_id":"c2","path":"values.count","reason":"type"}}}
```

Revision и accepted candidates не изменяются. Для missing required file вместо
этого `result_mismatch`, path `files.output`, reason `minimum`; для уже заявленного
FileRef с другими реальными bytes — `artifact_invalid`, reason `digest`.
Это разные точно описанные initial cases, не два допустимых ответа одному случаю.

### F5 — restart требует настроенного согласования

Initial revision20, unaccepted Task0042 generation1, proposal`p1/digest=eee…`
с ожидаемым новым contract уже сохранён. Invoker имеет только `author`; configured
approving role — `approver` (fixture value, не глобальная константа).

```json
{"schema":"stride-execution/v1","operation":"apply","input":{"request_id":"unauthorized-approval","expected_revision":20,"operations":[{"id":"a","kind":"decide_restart","task_id":"0042","proposal_id":"p1","proposal_digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","decision":"approved","reason":"Correct the unaccepted expectation."}]}}
```

```json
{"schema":"stride-execution/v1","status":"rejected","error":{"code":"authorization_required","message":"The configured approving role is required","retryable":false,"details":{"task_id":"0042","action":"decide_restart"}}}
```

Никакого approval создано не было. Тот же logical proposal, новый request ID и
реальный trusted `approver` дают immutable approval`ap1`, revision21. Author затем
применяет explicit restart с этим approval:

```json
{"schema":"stride-execution/v1","operation":"apply","input":{"request_id":"restart-approved","expected_revision":21,"operations":[{"id":"r","kind":"restart","task_id":"0042","expected_generation":1,"proposal_id":"p1","approval_id":"ap1"}]}}
```

Expected applied revision22, same Task0042, generation2/draft; WIP/Workspace,
неудачные evidence и actor согласования сохранены. Нет новой Requirements Task
или cancellation в Sequencing. Повтор после same restart не создаёт generation3.

### F6 — перемотка через этапы разных ролей

Initial restarted generation2/ready, revision30. Graph `write(author)` →
`inspect(approver)` → `publish(publisher)`. Exact актуальные accepted evidence
первых двух stages имеют original actors`a` и`b`, original generation1 и
эквивалентные current contract-stage/source digests. У `publish` доказательств нет.
Trusted initiator — `author`; конфигурация разрешает fast-forward read/reuse.

```json
{"schema":"stride-execution/v1","operation":"advance","input":{"request_id":"reuse-to-first-gap","expected_revision":30,"task_id":"0042","generation":2,"action":"fast_forward","target_stage":null}}
```

```json
{"schema":"stride-execution/v1","status":"work_required","revision":31,"replayed":false,"task_id":"0042","generation":2,"traversed":[{"stage_id":"write","iteration":1,"gate_id":"gw","evidence_origin_generation":1,"receipt_ids":["rw"],"source_actor_ids":["a"],"contract_stage_digest":"1111111111111111111111111111111111111111111111111111111111111111"},{"stage_id":"inspect","iteration":1,"gate_id":"gi","evidence_origin_generation":1,"receipt_ids":["ri"],"source_actor_ids":["b"],"contract_stage_digest":"2222222222222222222222222222222222222222222222222222222222222222"}],"current_stage":"publish","required_role":"publisher","stop_reason":"unproven_stage"}
```

Нет новых check runs, reviewers или роли publisher у инициатора. Для другого
допущенного initiator при тех же evidence traversal/stop тот же; private Session
stores не читаются. Изменение current source commit делает старые source-bound
receipts неактуальными: остановка уже на `write`, а не reuse старого PASS.

### F7 — stale revision и replay имеют разный порядок

Initial revision8, request`done1` ранее успешно применён на7, receipt сохранён.
Exact повтор`done1` с его expected7 возвращает saved success revision8/replayedtrue.
Новый request`other` с expected7 получает именно `revision_conflict`, до любой
operation/domain mutation; даже если в batch есть valid setup и invalid submit,
ни одна операция не применена. Existing request ID с изменённым payload получает
`request_reuse_conflict` раньше stale revision.

### F8 — uncertain subprocess не запускается второй раз

Known attempt`at1/run1`: started true, durable receipt absent; Task/source/candidate
не изменились. Exact retry `attempt/run` либо read status возвращает неопределённость;
`run` отказ:

```json
{"schema":"stride-execution/v1","status":"rejected","error":{"code":"attempt_uncertain","message":"Started run has no confirmed terminal receipt","retryable":false,"details":{"task_id":"0042","attempt_id":"at1","run_id":"run1"}}}
```

Subprocess count остаётся1. Authorized restart может abandon его только после
quiescence proof от runner, без придуманного successful receipt.

### F9 — импорт не создаёт foreign authority

Package`pkg1/digest=fff…` содержит завершённые owner histories0042 без uncertain
external intents. Initial destinationrevision0, identity0042 отсутствует.

```json
{"schema":"stride-execution/v1","operation":"transfer","input":{"action":"import","request_id":"import-0042","expected_revision":0,"source_id":"inbox-1","package_digest":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"}}
```

```json
{"schema":"stride-execution/v1","status":"imported","revision":1,"replayed":false,"package_id":"pkg1","package_digest":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","task_ids":["0042"]}
```

Imported source actor остаётся provenance, не текущий caller. Нет claim, Workspace
creation, Requirements writes или новой acceptance. Та же identity из другого
import request => `transfer_conflict`. Entry`../outside` => `package_invalid`,
reason`path`; destination не изменён, файл снаружи не создан.

### F10 — отрицательный outcome не completion

Initial revision40, Task0042 generation2 имеет configured terminal negative route,
trusted cancellation/outcome authority`auth40` существует.

```json
{"schema":"stride-execution/v1","operation":"apply","input":{"request_id":"negative-outcome","expected_revision":40,"operations":[{"id":"o","kind":"record_outcome","task_id":"0042","generation":2,"outcome_id":"o40","kind_of_outcome":"negative","result_ref":null,"authorization_id":"auth40"}]}}
```

Expected applied revision41; query outcome.kind_of_outcome = negative, result_ref
null. Requirements satisfaction и Sequencing dependencies не меняются. Попытка
подать positive без required verified terminal candidate => `evidence_incomplete`,
не изготовленный completed result.

### F11 — переносимость и consistent read

Две установки имеют разные DB/lock/roots/commands/role catalogue. Обе получают
одинаковый logical query packet при equivalent admitted identity0042:

```json
{"schema":"stride-execution/v1","operation":"query","input":{"queries":[{"id":"state","kind":"execution","task_id":"0042"},{"id":"history","kind":"history","task_id":"0042","after_event":null,"limit":10}]}}
```

Все results одного packet наблюдают одну owner revision. Физические пути могут
различаться, но протокол не содержит старых installation constants. Query unknown
candidate в том же packet => полный `not_found`, не смешанный partial success.

## 13. Verification, impact и requirement mapping

| Frozen Task requirement | Обязательное доказательство дизайна |
| --- | --- |
| R01 / EXE-01 | §4, F1/F2: explicit graph и template materialization/edit через существующий parser/editor; no fixed pipeline |
| R02 / EXE-04 | §5/7, F3/F4: matching file/value captured и verified, mismatching rejected самим Execution |
| R03 / EXE-07 | §6.1, F5/F8: exact approved restart, unauthorized refusal, immutable history/WIP, no replacement identity |
| R04 / EXE-08 | §8.1, F6: актуальные proof разных ролей, роль инициатора не ограничивает reuse, stop first gap |
| R05 / GEN-03 | §1/3/6/10, F9/F10: собственные config/store и нет foreign private state/fan-out |
| R06 / GEN-04 | §2/3, F11: configured argv/roots/roles/limits, no old paths/target list |

DoD: literal success/rejection/replay fixtures independent of serializers;
публичные schemas, side effects и config/command boundaries задокументированы здесь
в той же Task. Snapshot/authority references не являются вторым владельцем.

Предметная проверка T012: parse всех literal JSON; проверить discriminator/required
fields и exact error fields отдельным bounded checker; F1 compatibility через
существующий GoalTypeDefinition parser (это compatibility evidence, не независимый
wire oracle); сверить F1–F11 и R01–R06 вручную; existing local Markdown-link checker,
`git diff --check`, maintained application smoke. Не запускать broad/full discovery.

Design verification проверяет полноту и отсутствие противоречий на exact candidate
commit/tree. Same-role inspection остаётся самопроверкой. Ни одно из этих наблюдений
не означает, что target provider реализован или production migration прошла.

Impact: новый `apps/execution/**` adapter/config/store; выделение существующих
владельцев без потери evidence/restart/approval/attempt/integration histories;
public orchestration adapters должны передавать identities и результаты явно.
Не изменяются meanings Requirements identity/satisfaction, Sequencing eligibility/
claim/physical Workspace и Poise Session establishment. Legacy complete-Task
transfer не выдаётся за owner-only slice без отдельной extraction qualification.

Риски реализации: current monolith смешивает Task и claim checks; runtime extraction
должна заменить только внешние admission dependencies, сохранив проверки Execution.
File capture и external runner имеют независимые failure windows; их нельзя
свернуть в SQLite transaction. Approval provenance и evidence equivalence требуют
точных scope/digest, а не совпадения человеческих текстов. Никакая неопределённая
межвладельческая политика не заполняется hidden DB read или fabricated verdict.
