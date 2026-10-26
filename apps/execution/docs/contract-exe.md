# Execution public contract

Статус: действующие process/gate/files/lifecycle/advance/outcome/attempt/reuse API и отдельно отмеченные
проектные материалы.
Версия публичного протокола: `stride-execution/v1`.

Текущая поставка реализует `process` из §4, `apply/setup_execution`, `gate` из §7.2,
`files/check|diff`, `query/execution|gate_context|gate_result|file_result|file_diff_result`,
Python-интерфейсы записи/чтения фактов и `Store.prepare_stage_commit`,
`advance/next|fast_forward`, `outcome`, `attempt/begin|run|recover|finish|abandon`,
`reuse/prepare|verify|accept` и `query/progression|outcome|attempt|verification|reuse`.
[Итог выполнения](outcomes-exe.md) описывает текущие полномочия, пакет решения,
адресуемый факт, повторы и явную передачу отдельным получателям.
[Продвижение](advance-exe.md) описывает действующие пакеты, доказательства и историю.
Действующий [lifecycle](lifecycle-exe.md) предоставляет семь действий
freeze/enter/present/propose_restart/decide_restart/restart/rework и чтение
query/lifecycle|restart_history. Точные текущие пакеты и результаты описаны
в этом руководстве; таблицы проектного apply из §6 сохраняют отдельный статус.
Действующий договор описывают [оболочка](shell-exe.md),
[редактор процессов](process-exe.md), [приёмка](gate-exe.md) и
[файловые границы этапа](files-exe.md) и [собственный store](store-exe.md).
Файловый интерфейс проверяет объявленные read/write allow/deny нескольких корней
по выбранному этапу; gate самостоятельно проверяет чтение реального файла.
Решение о записи в main определяется договором конкретного этапа, включая integration.
Физические файловые операции и Git выполняются своими владельцами. В §12 F1/F2 относятся к process
API,
F3/F4 ссылаются на действующую приёмку.

Остальные схемы §1–3, §5–7, §8.2–11 и F1a/F5/F7–F9/F11 — проектные материалы T012, если явно
не отмечены в действующих руководствах. Они сами по себе не объявляют возможности
поставленного provider или новые обязательства реализации. В частности,
`ValueRule`/`FileRule`, проектный structured `error` и authority resolver
сохраняют статус проектных схем. Действующие runner и reuse описаны в
[руководстве проверок](verification-exe.md). Фактический текущий отказ
содержит `reason`, exit `2`, пустой stderr. Реальные секции, trace и файловые
счётчики описаны у владельца [gate](gate-exe.md).

[Сравнение изменений этапа](diff-exe.md) описывает действующие `files/diff`,
`query/file_diff_result` и физическую подготовку коммита через `Store.prepare_stage_commit`:
два сравнения, сохранение незавершённой работы и собственное решение допуска.
Эти возможности имеют собственные интерфейсы наряду с действующим lifecycle;
последующая интеграция результата имеет отдельного владельца.

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
Действующие operation перечислены во введении и [оболочке](shell-exe.md#запуск);
transfer/integration в проектных разделах имеют свой явно отмеченный статус.

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

Этот раздел описывает реализованный самостоятельный редактор. Настройки и
правильные процедуры находятся в [операторском руководстве](process-exe.md).
Выбранные имена процессов/шаблонов адресуют собственную явную конфигурацию.

### 4.1 ProcessDefinition и существующий редактор

Полная поддержанная модель имеет ровно поля
`goal_type,stages,content_contract,route,benefit,worktree_required`.
Каждый stage содержит
`id,instruction,read_only,allowed_paths,normalization,sections,required_sections,
artifact_requirements,handler,role,transitions,rework_targets,purpose,criteria_authority`.
`criteria_authority` содержит явные списки `propose` и `decide`, `purpose` —
`design|execution|observation|other`. Доступные виды обработчиков/семантика связей
проверяются одной извлечённой моделью `GoalTypeDefinition`.
Полный [корректный пример](process-exe.md#полное-определение-и-шаблон) включает все поля.

Модель проверяет форму, уникальность и разрешимость ссылок, достижимость,
путь завершения, content/trace и права проектирования критериев целевых этапов.
Role задаётся явно. Текстовые ссылки `route`, `point`, `section`, `stage_id`
передаются строками; диагностика структурного отказа указывает неверное поле/ссылку.

`changes` использует существующие ID-addressed операции:
`put_stage,patch_stage,remove_stage,patch_route,put_section,remove_section,
put_trace_route,remove_trace_route,put_requirement,remove_requirement,
set_benefit,set_worktree_required`. `put` принимает `{op,value}`, `remove` —
`{op,id}`, `patch_stage` — `{op,id,set}`, `patch_route` — `{op,set}`,
два `set_…` — `{op,value}`. Полные объекты и конечный граф проверяются той же моделью.
Пустой changes допустим; непересекающиеся patches одного объекта объединяются.
Шаблон и полное определение создают отдельный current выбранного `goal_type`;
редактирование сохраняет неизвестные raw configuration extensions.

### 4.2 `process` inputs

Envelope точно `{schema:"stride-execution/v1",operation:"process",input:object}`.
В input mutation обязательны `action,request_id,expected_revision,goal_type`.
`expected_revision` — целое `>=0`; ID — явно заданные непустые строки, выбранные
процессы и шаблоны зарегистрированы в `process.workflows/templates`.
Process revision — возвращённая непустая строка версии содержания; передавайте
её из ответа, отдельно вычислять файловые хэши не требуется.

| action | Остальные обязательные поля input | Результат |
| --- | --- | --- |
| `create` | `source,changes` | Создаёт выбранный новый current |
| `edit` | `expected_process_revision,changes` | Изменяет current при совпавшей process revision |
| `reconcile` | `expected_managed_revision,expected_live_revision,authorization_id,reason` | Принимает согласованную текущую публикацию в editor head/receipt |
| `status` | Только `goal_type` после `action` | Читает current/managed/live revision и pending request |

`source` имеет ровно одну форму:
`{kind:"definition",definition:ProcessDefinition}` либо
`{kind:"template",template:{id:string}}`. `changes` — список в пределах
явного `max_changes`. Остальные текстовые поля mutation непустые; пути берутся
из конфигурации. `status` input точно `{action,goal_type}`.
Обязательные известные поля и wire shapes проверяются строго.
Текущий файл status/edit/reconcile содержит тот же `goal_type`, что выбран в запросе.

Успех mutation точно:
`{schema,status:"published",revision,replayed,goal_type,
previous_process_revision:string|null,process_revision:string,changed:boolean}`.
Успех status точно:
`{schema,status:"read_only",revision,goal_type,
managed_revision:string|null,live_revision:string,pending_request:string|null}`.
Current для status должен существовать и быть структурно корректным.
Текущий отказ точно `{schema,status:"rejected",reason:string}`, exit `2`, пустой stderr.

Публикация сначала сохраняет pending candidate, затем выполняет fsync/atomic replace
файла, затем завершает editor/core transaction. При обычном отказе записи или
SQL-финализации до COMMIT:
`{schema,status:"pending",revision,intent_id:string,phase:"process_publication"}`,
exit `3`. Восстановите условие записи внешним инструментом и сразу повторите точный
исходный пакет до других изменений. Повтор завершает сохранённый candidate один раз.
Завершённый запрос возвращает прежний результат с `replayed:true` до чтения текущего
процесса/шаблона, включая его прежнюю целую revision.

Обычная attached transaction поддержана с DELETE/WAL без переключения режима.
Граница проверенного pre-COMMIT отката, продолжения и неизвестного COMMIT описана
в [процедуре публикации](process-exe.md#публикация-и-продолжение).

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

Здесь `expected_generation/generation` — Ordinal; `expected_contract_revision`,
`proposal_digest` — Digest; ID и reason непусты; `decision=approved|rejected`.
Итоговое решение имеет самостоятельную действующую операцию `outcome`,
описанную в [руководстве исходов](outcomes-exe.md#пакет-и-результат).
Терминальный переход и положительный итог различаются.

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

Этот подраздел сохраняет проектный договор T012 для более широкого apply/runner.
Действующие согласование, restart/rework и сохранение WIP описаны в
[руководстве lifecycle](lifecycle-exe.md#пакеты-и-результаты): используется
операция `lifecycle`, явный `actor_id` и сохранённые ID proposal/approval.
Для последующего freeze передаётся полный согласованный снимок Requirements.
Названия freeze_execution/revise_execution, proposal_digest и ограничения
будущего runner ниже относятся к проектной схеме, а не к текущему CLI.


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

Действующий CLI предоставляет `begin`, `run`, `recover`, `finish`, `abandon`.
Общие поля — `action,request_id,expected_revision,task_id,expected_generation,
expected_contract_revision,actor_id`; каждое действие дополнительно принимает
`attempt_id` и свои поля. Точные пакеты, 16-полевая попытка, полный 28-полевый
receipt, бюджет, реальные outputs/termination и продолжение после отказа записи
описаны у [канонического владельца](verification-exe.md#обычная-попытка).

Начало резервирует упорядоченные методы текущего этапа, выполнение сохраняет
реальные наблюдения, finish создаёт evidence. Read-only `query/attempt` читает
полную попытку, `query/verification` — бюджет текущего прохождения. Pending
продолжается по прежним адресам через действительный run/recover;
[восстановление](verification-exe.md#продолжение-после-сбоя) сохраняет историю
и различает известное отсутствие запуска и реально работающую команду.

### 7.2 `gate`

Действующая реализация имеет `action: declare|evaluate`. Точные входы, ответы,
настройки и правильный исполняемый пример находятся в едином
[руководстве gate](gate-exe.md). Контекст получает сохранённый полный процесс,
явный слой Task и stage contracts; кандидат передаёт sections/trace и абсолютные
пути реальных файлов. Счётчики этапа, выбранные pre/post и ошибки наблюдений
совместно определяют результат.

Положительный ответ имеет `status: evaluated`, `accepted: true`, собственный
`result_id`; отрицательная оценка — `evaluated/accepted: false` и сохранённое
решение с происхождением. Штатный отказ команды имеет `rejected/reason`.
Точный повтор возвращает сохранённый итог; новый кандидат наблюдает факты заново.
Приёмка оставляет канонические Task state/current_stage/generation/iteration
своим владельцам и сохраняет различие факта команды, результата этапа и Task outcome.

### 7.3 `reuse`

Действующие prepare/verify/accept используют собственные принятые факты Execution,
публичные family/eligibility/claim/Workspace от Sequencing, совместимые полные
контракты и Requirements snapshots, доставленные локальные файлы и свежую проверку.
Полные пакеты, состояния кандидата и явное собственное принятие описаны в
[руководстве reuse](verification-exe.md#повторное-использование-принятого-результата).
Полученный positive outcome передаётся Requirements/Sequencing существующим
вызывающим; курсор процесса и handler_outcome сохраняют свой договор.

## 8. Переходы, перемотка и outcome

Действующие `advance/next`, `advance/fast_forward`, `query/progression` описаны
в [руководстве продвижения](advance-exe.md). Обе команды используют актуальные
revision/generation/contract, настоящего участника, source и entry_tree.
`next` принимает фактически представленный Gate текущего этапа по его роли;
ссылки proof связывают контекст, кандидат, outcome и evidence.
Следующий участник выполняет новую работу согласно `required_role` ответа.

### 8.1 EXE-08: fast-forward после restart

После нового freeze/enter вызывающий передаёт свежие Gate/evidence для каждого
проходимого этапа. Execution сопоставляет текущий договор этапа, source,
наблюдения, исходное обоснование и решение независимого осмотра. Роль инициатора
перемотки не ограничивает подтверждённые исторические переходы.
История сохраняет исходных авторов, поколение, Gate и evidence; отдельная ссылка
указывает актуальные доказательства. Первый неподтверждённый этап требует работы.

Штатные результаты: `advanced`, `work_required`, `target_reached`, `route_end`.
Полные поля пакетов/ответов, причины остановки и правильное продолжение принадлежат
[текущему API](advance-exe.md#пакет-продвижения). Перед переходом заново проверяются
предусловия следующего этапа. Повторное потребление одного исторического визита
останавливает цикл; точный request replay возвращает сохранённый результат.

`route_end` оставляет execution в `verified`. Уполномоченный участник затем
явно вызывает действующий [outcome](outcomes-exe.md#правильный-порядок-решения),
а вызывающая сторона отдельно передаёт сохранённый факт получателям.
Далее §8.2 описывает проектный интерфейс интеграции; подготовка коммита этапа
уже реализована отдельно в [Store.prepare_stage_commit](diff-exe.md).

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

Текущее чтение `attempt|verification|reuse` возвращает полный `result` по
[договору проверок](verification-exe.md#бюджет-чтение-и-повтор).
Текущее чтение `outcome` возвращает полный сохранённый факт в поле `result`
по [действующему договору](outcomes-exe.md#адресное-чтение-и-повтор).
Текущее чтение `execution|gate_context|gate_result` определено в
[оболочке](shell-exe.md#настройка-исполнения-и-чтение) и
[руководстве gate](gate-exe.md#чтение-и-повторы). Раздел ниже сохраняет проектный
набор остальных queries и их будущих схем; фактические gate queries имеют
собственные result envelopes и возвращают null для отсутствующей записи.

Input точно `{queries:Query[]}`, nonempty bounded, `id` непусты и уникальны.
Один read batch видит одну owner revision, не выполняет repair/migration,
не возобновляет pending attempts и не читает foreign state.

| kind | Поля после `id,kind` | `value` |
| --- | --- | --- |
| `execution` | `task_id` | `{task_id,generation,contract_revision,state,current_stage,iteration,workspace_id,admission_id}` |
| `contract` | `task_id,generation` | `{contract_revision,contract:ExecutionContract,process:ProcessDefinition}` |
| `candidate` | `task_id,candidate_id` | `{candidate:Candidate,candidate_digest,state,gate_id:Id|null}` |
| `gate` | `task_id,gate_id` | `{gate_id,candidate_id,candidate_digest,source,artifact_ids,receipt_ids,verified:boolean}` |
| `attempt` (действующий) | `task_id,attempt_id` | Полная попытка в `result` |
| `verification` (действующий) | `task_id` | Бюджет текущего прохождения в `result` |
| `reuse` (действующий) | `task_id,reuse_id` | Полный кандидат в `result` |
| `outcome` (действующий) | `task_id,outcome_id` | Полный `result` из [руководства исходов](outcomes-exe.md#пакет-и-результат), отдельный от проектного `value` |
| `restart_history` | `task_id` | `{entries:[{proposal_id,proposal_digest,approval_id,decision,source_generation,target_generation:Ordinal|null,actor_id,role_id}]}` |
| `history` | `task_id,after_event:Id|null,limit:Ordinal` | `{events:[{id,type,generation,subject_id,revision}],next_after:Id|null}` |
| `artifact` | `task_id,artifact_id` | `{artifact_id,root_id,path,sha256,bytes,capture_id}` |

Для проектных строк этой таблицы `state` execution = `draft|ready|active|terminal`; candidate =
`submitted|verified|superseded`. Gate record `verified:true` только для выполненной
проверки владельцем, не caller input. Все identities принадлежат указанной Task.
Проектный unknown identity даёт `not_found`; действующий outcome query
использует обычный rejected/reason из руководства исходов. Частичный успех
смешанного query-пакета не возвращается.

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

F1/F2 относятся к действующему process API; F3/F4 — к текущему gate.
F1a/F5–F11 остаются отдельными проектными lifecycle fixtures.
Все JSON ниже — literal данные, не вывод production serializer. Где краткая
fixture задаёт условие словами, оно является обязательным precondition, а не
пропущенным обязательным wire field. Реальные Digest считаются из указанных bytes;
повторяющиеся hex в кратких fixtures обозначают уже существующие literal IDs
в initial owner store, не обещают hash произвольного нового текста.

### F1 — новый граф без навязанного pipeline

Для действующего F1 используйте новый собственный store revision0,
зарегистрируйте `proof_note` в `process.workflows` с отдельным current path.
Полная definition — отдельный stage с явной ролью, не фиксированная последовательность:

```json
{"schema":"stride-execution/v1","operation":"process","input":{"action":"create","request_id":"new-proof-note","expected_revision":0,"goal_type":"proof_note","source":{"kind":"definition","definition":{"goal_type":"proof_note","worktree_required":false,"benefit":{"git_categories":[],"sections":[]},"route":{"entry":"write"},"content_contract":{"sections":[],"routes":[],"requirements":[]},"stages":[{"id":"write","instruction":"Return the declared note.","read_only":false,"allowed_paths":[],"normalization":"exact","sections":{"report":"Provide the result."},"required_sections":["report"],"artifact_requirements":[],"handler":"produce","role":"author","transitions":{"complete":null},"rework_targets":["write"],"purpose":"execution","criteria_authority":{"propose":[],"decide":[]}}]}},"changes":[]}}
```

Expected `published`, revision1, goal_type `proof_note`, previous revision null,
changed true; используйте возвращённый process_revision для выбранного процесса.
Полная definition принимается текущей собственной моделью GoalTypeDefinition.
Последующая Task сохраняет её фактический snapshot; целая owner revision и
идентификатор содержания процесса имеют разные назначения.

### F1a — полный setup и submission source-free этапа

Это самостоятельная историческая target fixture последующего lifecycle, а не
продолжение действующего F1: её сохранённая прежняя definition и вычисленные revision
зафиксированы отдельно. Literal process/contract revisions ниже относятся только
к этой исторической fixture. Для текущего setup берите возвращённую revision F1
и [реальную процедуру](process-exe.md#правильное-создание-изменение-и-настройка-исполнения).
В целевом окружении store revision1; canonical Task0042/snapshot1, trusted admission
`admit-0042` и restart policy `rp-1` уже существуют. Полный target contract:

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

Для действующего F2 store имеет revision1 после F1. Зарегистрируйте
`template_note` в workflows и `note-template` в templates с отдельным path;
сохраните полную definition F1 в этом файле шаблона, включая purpose/criteria_authority.

```json
{"schema":"stride-execution/v1","operation":"process","input":{"action":"create","request_id":"template-note","expected_revision":1,"goal_type":"template_note","source":{"kind":"template","template":{"id":"note-template"}},"changes":[{"op":"patch_stage","id":"write","set":{"instruction":"Return a materialized note."}}]}}
```

Ожидается `published`, revision2. Шаблон сохраняет исходные байты;
материализованный процесс получает выбранный goal_type и instruction.
Точный исходный повтор возвращает тот же process_revision, `replayed:true`, revision2.
Именованные изменения и конечная проверка выполняются существующей моделью редактора.

### F3 — matching value и file проходят gate

Действующий [исполняемый пример](gate-exe.md#правильный-исполняемый-пример)
создаёт полный процесс, собственное исполнение, контекст и настоящий файл.
Он получает реальные ссылки из ответов, проверяет `evaluated/accepted: true`,
сохранённый `stage_result`, происхождение и точный повтор. Каноническая Task
остаётся `draft`; приёмка результата не выполняет её переход.

### F4 — mismatch не становится принятым результатом

Читайте [сохранённое решение](gate-exe.md#проверка-кандидата-и-сохранённое-решение)
и различайте `evaluated/accepted: false` с assessment от `rejected/reason`.
Первое сохраняет фактический кандидат и отрицательное решение; второе сохраняет
исходное бизнес-состояние. Собственный `stage_result` появляется после успешного
выполнения всех объявленных условий. Корректное чтение и повторы описаны в
[едином договоре](gate-exe.md#чтение-и-повторы).

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

Действующий сценарий: после принятого выполнения и согласованного restart
подготовить свежие доказательства текущего поколения, затем вызвать
`advance/fast_forward`. Подтверждённые этапы исполнителя и проверяющего проходят
независимо от роли инициатора; исходные авторы и решения остаются в истории.
На первом этапе без пригодных доказательств ответ `work_required` указывает
`current_stage`, `required_role` и конкретную причину. Это новая работа своей роли.

Полный [правильный пример](advance-exe.md#перемотка-после-согласованного-restart)
использует настоящие ответы Gate/Store и текущие версии из публичного query.

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

Действующий сценарий: после допущенного терминального `not_satisfied` либо
`inconclusive` уполномоченный участник передаёт `outcome` с intent accept,
актуальными revision/generation/contract, source принятого доказательства
и явным result_commit null. Execution сохраняет kind negative, настоящий
handler_outcome и пустой satisfied_task_requirements. Примеры и полные поля
даны в [руководстве исходов](outcomes-exe.md#примеры-обращения-к-cli).

После создания факта данные Requirements/Sequencing сохраняются. Их обновляет
явная передача проекций вызывающей стороной; Sequencing принимает negative
как available, а Requirements сохраняет отрицательный факт без satisfaction.

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
| R02 / EXE-04 | §7.2, F3/F4 и gate-exe: реальные секции/trace/файловые счётчики, собственные сохранённые assessment/решение/результат и происхождение |
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
