# Bootstrap public contract

Статус: target design для `PS-DEV-002-T014` / `M01.T014`.
Версия внешнего результата: `stride-bootstrap/v1`.

Документ определяет самостоятельный Bootstrap после разделения приложений.
Это не утверждение, что существующий `poise infra/deps/check` уже реализует
multi-target CLI ниже. T014 изменяет только этот контракт. Реализация executable,
операционной конфигурации и provider/consumer qualification — последующие Tasks.

## 1. Владелец и проверенный baseline

Bootstrap владеет обслуживанием среды и операционным, не версионируемым в Git
каталогом `projects/`: выбором целей, связыванием их maintenance-каталогов,
параметров и публичных адаптеров ремонта, последовательным обходом и отчётом.
Он не владеет Task identity, Session, claim, Sprint, requirements satisfaction,
Execution process/evidence или принятием результата разработки.

Команды `infra`, `deps`, `check` доступны без Task, Session и рабочего харнеса.
Наличие `available` или `active` Tasks само по себе не запрещает диагностику или
исправление политики. Конкретный adapter проверяет реальные ресурсы, которые
затронул бы ремонт: например, чужой занятый worktree, БД или lock binding.

Baseline — inventory T007 `inventory-bst.md`, каталог из framing T014,
`src/poise/interfaces/maintenance.py`, `src/poise_environment/profile.py` и
закреплённый `environment-maintenance 0.1.1`. Переиспользуется его единственный
executor `environment_maintenance.engine.execute`, parser/parameter resolver и
RepairRouter, а не копия движка внутри Bootstrap. Существующий внутренний
результат `environment-maintenance/result/v1` сохраняется с собственной версией.
Новая версия внешней оболочки нужна для target selection и полного multi-target
отчёта; она не переименовывает или приписывает новые возможности старому core.

Requirements, Sequencing, Execution и Poise не вызываются скрыто. Когда ремонт
требует их предметного знания, Bootstrap вызывает **настроенный публичный adapter**
со значениями, а не импортирует private store/domain object или читает чужую БД.
Создание отсутствующей БД допустимо только через её владельца. Повреждённую БД
нельзя пересоздать под видом успешного ремонта. Broker, распределённая транзакция
и второй источник canonical state не вводятся.

Соседний контракт: [Orchestrator](../../orchestrator/docs/contract-orc.md).

## 2. Публичный вызов

Исполняемая программа и её начальный `argv` поступают из конфигурации установки.
Обозначение `BOOTSTRAP_ARGV` ниже означает массив аргументов, не имя executable
и не обязательную переменную окружения:

```text
BOOTSTRAP_ARGV infra [--target TARGET] [--dry-run] [--set NAME=VALUE ...]
BOOTSTRAP_ARGV deps  [--target TARGET] [--dry-run] [--set NAME=VALUE ...]
BOOTSTRAP_ARGV check [--target TARGET] [--dry-run] [--set NAME=VALUE ...]
```

`TARGET` — один логический ID или зарезервированный selector `all`; это не путь.
Повтор `--target`, неизвестные flags, неизвестная команда, позиционные
лишние аргументы и отсутствие значения flag отклоняются `invalid_request`, exit 2.
Явная пустая строка TARGET отклоняется `invalid_selector`, exit 2.
`--set` сохраняет порядок, duplicate NAME использует последнее значение: это
существующая semantics core. Неизвестный параметр не игнорируется.

В v1 нет публичного mutation/query JSON packet: вход — CLI argv и явно разрешённые
configuration/environment values. JSON-примеры invocation в §10 являются
независимыми test vectors для построения argv, не новым wire endpoint.
stdin не используется. Shell parsing, shell interpolation и исполнение строки
целевого ID запрещены. Команда не получает `task_id`, session token или claim.

Завершившийся управляемый вызов печатает один UTF-8 JSON object и `\n` в stdout.
Диагностика идёт в stderr; stdout дочерних adapters захватывается в core report,
не смешивается с envelope. Принудительное завершение процесса может не дать
полного JSON; caller не должен считать такой вызов успехом или завершённым repair.

## 3. Bootstrap configuration и projects

Resolved Bootstrap configuration имеет ровно эти поля; её загрузка из файла/argv
определяется implementation Task, но нижеследующая модель уже является контрактом:

```json
{"schema":"stride-bootstrap-config/v1","projects_root":"/configured/runtime/projects","default_target":"all","target_env":"EXAMPLE_BOOTSTRAP_TARGET","limits":{"max_targets":32,"max_target_bytes":1048576,"max_output_bytes":8388608,"target_timeout_seconds":120,"run_timeout_seconds":3600}}
```

Значения в примере — fixture configuration, не defaults реализации. Все limits
обязательны, конечны, positive; bool не integer. Counts/bytes — integers, seconds —
positive finite numbers. `projects_root` — явно заданный абсолютный каталог либо
путь, разрешённый относительно Bootstrap config. `target_env` — непустое имя
environment variable или `null` (env default отключён). Никакое встроенное имя
variable не обязательно. `default_target` — `all` или допустимый target ID.

`projects_root` принадлежит Bootstrap; рабочие target configs, а не только ссылки
на них, должны входить в резервную копию Bootstrap. Примеры поставляются отдельно
в versioned templates и не заменяют существующие operational configs при установке
или обновлении. Никакого автоматического scan соседних application stores нет.

### 3.1 Discovery и target record

Discovery рассматривает только обычные непосредственные файлы `<target_id>.json`
в `projects_root`, отсортированные по target ID (Unicode code-point order).
Подкаталоги и файлы с другим расширением не являются целями. Файл `<id>.json`
содержит exact record:

```json
{"schema":"stride-bootstrap-project/v1","id":"alpha","catalog":"alpha-assets/requirements.json","repairs":"alpha-assets/repairs.json","values":{},"parameter_values_files":[]}
```

Поля обязательны. `repairs` — путь или `null`; `parameter_values_files` — ordered
list путей (может быть пустым); `values` — object значений declared core parameters.
`catalog` обязателен. Относительные asset paths разрешаются относительно
`projects_root`, не cwd вызывающего. Они должны разрешаться внутри этого корня,
без `..`, symlink components, NUL и неявных внешних roots. `catalog`/`repairs`/value-file paths обязаны быть относительными. Подкаталоги для assets
разрешены; сами subprocess argv/cwd/operation targets задаются maintenance catalogue.
Target ID — непустая строка без `/`, `\`, NUL, `.`/`..` как целого ID; `all`
зарезервирован. ID внутри record должен совпасть с именем файла. Exact schema и
набор полей проверяются; duplicate JSON keys, non-finite numbers и неверный UTF-8
недопустимы. Отсутствующий `repairs` не равен явному `null`.

Количество/имена целевых приложений не зашиты в программу. Новый корректный
`extra.json` включается следующим вызовом `--target all` без изменения кода.
Каталог без целей возвращает `not_required`; отсутствующий/нечитаемый корень —
`projects_unavailable`, а не пустой успешный результат. Необычный тип записи или
symlink с выбранным `.json` именем даёт per-target `unsafe_target_config`.

### 3.2 Freeze выбранного набора

До первого check/apply Bootstrap фиксирует имена выбранных целей и считывает
bounded bytes каждого выбранного record и его assets. `max_target_bytes`
ограничивает их совокупный размер для одной цели, не только project record. Для каждого target формирует
immutable invocation snapshot и `config_digest`: SHA-256 canonical JSON object
`{project, catalog, repairs, values_files}` с parsed values, отсортированными object
keys, UTF-8, без пробелов, без NaN; `repairs:null` сохраняется. Сами секретные
значения в журнал не выводятся. Digest включает исходные config values, но не
выводится, если хотя бы один catalogue parameter имеет `secret:true`; тогда
`config_digest:null`, а фактическая конфигурация остаётся защищённым operational input.
Resolved environment/CLI parameters редактируются только до запуска core.

Изменение `projects/` после freeze не добавляет цель и не меняет текущий snapshot;
оно учитывается новым вызовом. Это не межфайловая distributed transaction и не
обещание атомарного редактирования чужим процессом. Publication target configs
должна быть atomic file replacement; digest обозначает реально прочитанный набор.
Не удалось прочитать один target — его error фиксируется до effects, остальные
уже выбранные цели продолжаются. Не удалось получить сам bounded список либо
превышен `max_targets` — весь вызов rejected до запуска любой цели.

### 3.3 Parameters

Target values начинаются с `values` record; `parameter_values_files` накладываются
по порядку. Затем existing core resolver применяет declaration defaults, эти
values, declared environment bindings и CLI `--set` в порядке:

```text
catalog default < merged target values < declared environment < explicit --set
```

Bootstrap передаёт отдельный, неизменяемый snapshot host environment каждому core
вызову; core пропускает дочерним командам только declared `inherit_env`. Секреты и
их origins redacted по core policy. Expansion однопроходный и типизированный,
без shell evaluation. Параметр, не объявленный выбранной целью, даёт отказ именно
этой цели `invalid_target_parameters`; он не меняет список целей. При `all` override
не «угадывается» для несовместимых каталогов: затронутые цели отклоняются явно.

## 4. Target precedence

Bootstrap вычисляет selector один раз:

1. Явный единственный `--target` имеет приоритет над любым env/default.
2. Иначе берётся присутствующее значение variable, имя которой задаёт `target_env`.
3. Иначе используется `default_target`.

Пустое присутствующее env value — `invalid_selector`, не молчаливый fallback.
Selector `all` раскрывается через §3.1. Явный неизвестный ID — `target_not_found`,
exit 2, без fallback к env/all и без вызовов adapters. `selection.source` равен
`argument`, `environment` или `configuration`; origin не выводит секреты.
При env=`all` и `--target alpha` обслуживается только alpha. Без аргумента при
том же env обслуживаются все обнаруженные цели, а не фиксированная семёрка.

## 5. Execution и границы записи

Для каждого target независимо используется существующий core executor. Target
обрабатываются последовательно в frozen order; внутри target сохраняется порядок
catalogue. Перед effects **вся** его catalogue и все repair routes валидируются.
Ошибка одной цели не прерывает остальные. Это выбранный подход B/B2, а не
общая транзакция и не остановка на первой неисправной конфигурации.

| Command/mode | Выбранные требования | Допустимые actions |
| --- | --- | --- |
| `check`, с dry-run или без | infra и deps | initial check, read-only recommendation; никогда apply |
| `infra --dry-run` | только infra | initial check, planned_apply в report, read-only recommendation |
| `deps --dry-run` | только deps | то же для deps |
| `infra` | только infra | initial check; при непрохождении apply, затем повторный check |
| `deps` | только deps | то же для deps |

Read-only — обязательство публичного check/recommend adapter, не предположение,
что произвольный shell script безопасен. Конфигурация команды — доверенный вход
уполномоченного владельца. Bootstrap не даёт способа выбрать произвольный foreign
DB или вставить shell string через target ID. До квалификации adapter нельзя
называть его check read-only на основании имени. Fixtures проверяют отсутствие
изменений файлов/БД в disposable установке.

### 5.1 Success repair требует двух наблюдений

Initial check passed → requirement `satisfied`, без apply.
Initial check не passed и check/dry-run → `action_required`, без apply.
Для разрешённого infra/deps после failed check: выполнить apply и **повторный check**.
Повторный check выполняется также после неуспешного apply, как в baseline core.
Requirement `repaired` только когда **оба** apply и final_check имеют `passed`.
Apply passed + final_check failed → `failed`; apply failed + final_check passed
тоже `failed`. Это не успешный repair. Reports сохраняют initial/apply/final,
recommendation и точный failure stage; previous failure не затирается.

### 5.2 Concurrency, timeout и повтор

В v1 Bootstrap не запускает цели параллельно. Одновременные отдельные вызовы могут
конкурировать; integrity/lock/revision checks выполняют публичные resource owners.
Bootstrap не вводит общего distributed lock. Нельзя обходить owner rejection
reset/delete/пересозданием БД. Восстановление отсутствующей конфигурации не
перезаписывает повреждённую или изменённую существующую конфигурацию.

Все subprocess actions ограничены declared timeout; target и весь run также
bounded. Target timeout даёт `target_timeout` и effects=`unknown`; последующие
цели обрабатываются, пока общий deadline позволяет. Истечение run deadline
формирует по оставшимся frozen IDs `not_started`, effects=`none`, без запуска;
результат run=`failed`. Прерванный apply не повторяется автоматически.

Новый invocation сначала снова проверяет фактическую среду. Внешние repairs не
объявляются atomic/exactly-once; допустимый retry зависит от public adapter,
а не от нового `run_id`. Нет blanket replay cache, который выдаёт старый `ready`
вместо свежей диагностики. После неполного transport output caller выполняет check
и проверяет owner recovery intent до повторного разрешения apply.

## 6. Точная форма результата

Пример использует alpha record из §3.1, но с `repairs:null`; `values` и
`parameter_values_files` пусты. Catalogue равен
`{"schema":"environment-maintenance/requirements/v1","parameters":{},"requirements":[]}`.
Digest ниже вычислен из этого полного config snapshot по §3.2.
Обычный outer result имеет только:

```json
{"schema":"stride-bootstrap/v1","command":"check","dry_run":false,"selection":{"requested":"all","source":"configuration","resolved":["alpha"]},"status":"not_required","exit_code":0,"targets":[{"target":"alpha","config_digest":"dbbea48f607930988c72b7fc18e3272fa3d8fb2d725d54454385789032d61445","status":"not_required","exit_code":0,"effects":"none","core":{"schema":"environment-maintenance/result/v1","command":"check","dry_run":false,"status":"not_required","exit_code":0,"parameters":{},"parameter_sources":{},"requirements":[]},"error":null}]}
```

В примере каталог пуст: core корректно вернул `not_required`. Непустой
`requirements` сохраняет полные action responses existing core без потери полей.
Fixtures в §10 с сокращёнными projections явно не являются полным runtime report.

Outer fields: `schema`, `command`, `dry_run`, `selection`, `status`, `exit_code`,
`targets`. `command` — infra/deps/check; `dry_run` — bool, exact input flag.
`selection` имеет exact `requested`, `source`, `resolved`; resolved — unique sorted
array. Каждый resolved ID имеет ровно один target result, в том же порядке.

Target result exact fields:
- `target`: выбранный ID;
- `config_digest`: lowercase SHA-256 или `null` по §3.2/при ошибке чтения;
- `status`: `ready|repaired|not_required|action_required|failed|not_started`;
- `exit_code`: 0, 3 или 4, согласованный со status;
- `effects`: `none|applied|unknown` (не обещание общей транзакции);
- `core`: полный `environment-maintenance/result/v1` или `null`, когда core не
  был запущен/не вернул достоверный результат;
- `error`: `null` либо `{code,message,retryable}`.

`effects=none` означает apply не вызывался; диагностика и recommendation по
контракту read-only. `applied` означает хотя бы один apply был вызван и завершён,
**не** что все repairs успешны. Неполный/прерванный apply даёт `unknown`. Нельзя
понижать unknown до none по отсутствию успешного результата. Target core invalid
result (exit 2) сохраняется в `core`, target нормализуется к failed/4 и
`invalid_target_catalogue`, effects none.

### 6.1 Aggregation

Aggregate вычисляется из **всех** target states по приоритету:

```text
failed или not_started > action_required > repaired > ready > not_required
```

Top-level `failed` → exit4; `action_required` → exit3; `repaired|ready|not_required`
→ exit0. Если targets пуст или все not_required, итог not_required. Один repaired
и другой action_required → итог action_required, exit3; успешный target не скрывает
failure другого. Суммарный статус не является Task completion или Session admission.

### 6.2 Global rejection

Ошибка до выбора/планирования целей имеет другую, exact форму:

```json
{"schema":"stride-bootstrap/v1","status":"rejected","exit_code":2,"error":{"code":"target_not_found","message":"Requested target does not exist","retryable":false},"targets":[]}
```

Нет частичного effect при global rejection. Bootstrap preflights configuration,
selectors, discovery cardinality и bounded output planning до первого apply.
Runtime overflow вывода после effects — `failed`/4 с сохранением bounded error
`output_limit_exceeded` и effects unknown для неполного результата, не rejected/2.
Реализация обязана зарезервировать место для result/error каждого frozen target и
не обрезать JSON молча. При физическом невозможном transport (disk/stdout closed)
caller видит non-success process termination, не fabricated complete JSON.

## 7. Stable errors

Machine discriminator — `error.code`; текст message human-oriented.

| Code | Scope | Effects/продолжение |
| --- | --- | --- |
| `invalid_request` | global | неправильный argv; none, exit2 |
| `invalid_bootstrap_config` | global | version/fields/limits; none, exit2 |
| `invalid_selector` | global | malformed/empty target; none, exit2 |
| `target_not_found` | global | explicit/default ID отсутствует; none, exit2 |
| `projects_unavailable` | global | нельзя перечислить configured root; none, exit2 |
| `limit_exceeded` | global | max_targets/невозможный bounded report; none, exit2 |
| `unsafe_target_config` | target | unsafe file/path/symlink; none, остальные идут |
| `invalid_target_config` | target | JSON/schema/id/record; none, остальные идут |
| `invalid_target_catalogue` | target | core catalogue/repair contract; none, остальные идут |
| `invalid_target_parameters` | target | resolver/type/unknown parameter; none, остальные идут |
| `target_input_unavailable` | target | asset missing/read/size limit; none, остальные идут |
| `maintenance_unavailable` | target | core отсутствует; none, action_required/3 |
| `target_timeout` | target | interrupted core/action; unknown, failed/4 |
| `run_deadline_exceeded` | remaining targets | not_started/4, none |
| `output_limit_exceeded` | target | runtime bounded output; unknown, failed/4 |
| `maintenance_failed` | target | core failed; applied/none по фактическим actions |

Для read-only input/contract ошибок retryable=false. Timeout/availability MAY
помечаться retryable=true только для повторной **диагностики**; это не разрешение
повторять uncertain apply. Baseline core code/recommendation и exit code adapter
сохраняются внутри core; Bootstrap не изобретает успешный foreign domain outcome.
Глобальное отсутствие core допускается обнаружить до запуска; внешний результат
всё равно перечисляет выбранные цели как maintenance_unavailable, а не молча
пропускает их. No repair catalogue при requirement repair route — invalid_target_catalogue.

## 8. Backup и восстановление операционных проектов

Поставка Bootstrap включает полный operational `projects/` (target records,
catalogues, repair routes, value files) и Bootstrap config либо явный protected
secret-reference manifest. Examples-only архив недостаточен. Для каждого asset
сохраняются relative path, byte size, SHA-256 и permission policy. Секретные файлы
копируются только в авторизованное закрытое хранилище, не в readable Gmail body.

Restore проверяет inventory/digests до публикации, раскладывает данные в явно
переданный root, не перезаписывает отличающиеся live configs без явного решения.
После установки dependency/core выполняется check; archive delivery сама по себе
не утверждает ready environment. Перенос физического root требует явного binding
через configuration owner, не скрытой текстовой замены во всех private stores.
Это recovery boundary будущей реализации, не новый четвёртый repair verb.
Bootstrap backup не является snapshot БД Requirements/Execution/Sequencing; их
owners имеют независимые переносы. Восстановитель не создаёт Task или Session.

## 9. Compatibility и ограничения дизайна

Legacy `poise infra/deps/check --catalog ... --repairs ... --values ... --set ...`
может оставаться compatibility adapter. Новый target wrapper не должен превращать
его одиночный catalogue в скрытый `all`; legacy forms и новый target API нельзя
молча смешивать. Exact migration/version negotiation определяется implementation
Task. Здесь гарантируется сохранение core algorithm и distinction versions.

Impact: будущий `apps/bootstrap/**`, operational config/delivery и standalone
wrapper. Не меняются методы расчёта требований core, foreign DB schema, Task
process/claims или способы независимой приёмки. Риски: readonly adapters,
межфайловая config publication, output/time limits, partial external effects,
потеря operational projects при переносе. Они явно входят в qualification, а не
маскируются наличием красивого JSON или прохождением design-stage.

## 10. Независимые literal fixtures

JSON test vectors — authored inputs/expected projections, не production serializer.
`invocation` нормализует command/target/dry_run/overrides до argv. `target:null`
означает flag отсутствует, не передан string null. `environment` отражает выбранное
declared env значение до selection; `null` означает отсутствует. Условия existing
core check/apply результатов заданы fixture-ом отдельно от новой оболочки.

### F1 — explicit target over env all

```json
{"fixture":"F1","invocation":{"command":"check","target":"alpha","dry_run":false,"overrides":[]},"environment":"all","configuration_default":"all","discovered":["alpha","beta","extra"],"expected":{"resolved":["alpha"],"source":"argument","apply_calls":0}}
```

### F2 — omission and discovery of an additional target

```json
{"fixture":"F2","invocation":{"command":"check","target":null,"dry_run":false,"overrides":[]},"environment":"all","configuration_default":"alpha","discovered":["extra","beta","alpha"],"expected":{"resolved":["alpha","beta","extra"],"source":"environment","apply_calls":0}}
```

### F3 — unknown explicit ID must not fall back

```json
{"fixture":"F3","invocation":{"command":"infra","target":"absent","dry_run":false,"overrides":[]},"environment":"all","configuration_default":"all","discovered":["alpha"],"expected":{"schema":"stride-bootstrap/v1","status":"rejected","exit_code":2,"error":{"code":"target_not_found","message":"Requested target does not exist","retryable":false},"targets":[]},"apply_calls":0}
```

### F4 — check and dry-run do not apply

For each vector the initial check fails; adapters are read-only and disposable
filesystem/database inventories must be byte-identical before/after:

```json
{"fixture":"F4","cases":[{"command":"check","dry_run":false,"kind":"infra"},{"command":"check","dry_run":true,"kind":"deps"},{"command":"infra","dry_run":true,"kind":"infra"},{"command":"deps","dry_run":true,"kind":"deps"}],"initial_check":"failed","expected":{"requirement_status":"action_required","apply_calls":0,"exit_code":3,"effects":"none"}}
```

### F5 — apply is not sufficient and failed apply stays failed

All cases: command=infra, dry_run=false, initial check failed. Exactly one apply
and one final check follow the initial check:

```json
{"fixture":"F5","cases":[{"apply":"passed","final_check":"passed","status":"repaired","exit_code":0},{"apply":"passed","final_check":"failed","status":"failed","exit_code":4},{"apply":"failed","final_check":"passed","status":"failed","exit_code":4},{"apply":"failed","final_check":"failed","status":"failed","exit_code":4}],"expected_counts":{"initial_check":1,"apply":1,"final_check":1}}
```

### F6 — one invalid target does not block another

Frozen order alpha,beta,extra. alpha has invalid project JSON, beta needs successful
repair, extra is already ready. No alpha subprocess runs. beta and extra both run:

```json
{"fixture":"F6","targets":[{"target":"alpha","status":"failed","exit_code":4,"effects":"none","error_code":"invalid_target_config"},{"target":"beta","status":"repaired","exit_code":0,"effects":"applied","error_code":null},{"target":"extra","status":"ready","exit_code":0,"effects":"none","error_code":null}],"expected":{"visited":["alpha","beta","extra"],"status":"failed","exit_code":4,"applied_targets":["beta"]}}
```

### F7 — aggregate does not hide outstanding repair

```json
{"fixture":"F7","cases":[{"states":[],"status":"not_required","exit_code":0},{"states":["not_required","not_required"],"status":"not_required","exit_code":0},{"states":["not_required","ready"],"status":"ready","exit_code":0},{"states":["repaired","action_required"],"status":"action_required","exit_code":3},{"states":["ready","not_started"],"status":"failed","exit_code":4}]}
```

### F8 — no Task/Session and no hard-coded root

Run each of infra/deps/check with two different configured installations, an empty
catalogue and **no** Task/Session environment. Both produce not_required/0; the
logical result has the same schema/command/selection. Configuration paths differ,
no Task/claim/Session write occurs. Add `extra.json`: next all run includes extra.
This is a requirement for the new wrapper; existing core independence tests prove
only the preserved baseline, not the multi-target implementation.

### F9 — preserve config and data under unsafe repair

A selected target points to an existing corrupted owner DB. The public adapter
rejects replacement. infra result is non-success, recommendation retained, original
DB bytes unchanged; a missing **different** owner's DB may be created only by its
public adapter. A projects backup containing only templates fails completeness
verification. Existing operational target config must not be replaced by templates.

### F10 — snapshot, limits and interruption are explicit

```json
{"fixture":"F10","cases":[{"condition":"max_targets_exceeded","expected_code":"limit_exceeded","status":"rejected","exit_code":2,"apply_calls":0},{"condition":"target_timeout","expected_code":"target_timeout","target_status":"failed","target_effects":"unknown","later_targets":"continue_within_deadline"},{"condition":"run_deadline_before_beta","remaining":["beta","extra"],"target_status":"not_started","expected_code":"run_deadline_exceeded","target_effects":"none","exit_code":4}]}
```

A config written after selection does not enter the current resolved list or
change its parsed snapshots; it appears on the next invocation. An unknown
outcome never triggers blind apply retry or reports ready based on archive presence.

## 11. Traceability и design verification

| Task requirement | Evidence |
| --- | --- |
| R01 / BST-01 | §1–2, §5, F4/F5/F8: retained core and three session-free commands |
| R02 / BST-02 | §3, §8, F9: operational projects are owner state; templates separated and backup complete |
| R03 / BST-03 | §4, F1/F2/F3: explicit > environment > config, no silent fallback |
| R04 / BST-04 | §3.1–3.2, F2/F8: discover arbitrary projects, no built-in app list |
| R05 / BST-05 | §5, §6.1, F4/F5/F7: readonly diagnosis, apply-plus-recheck and complete outcomes |
| R06 / GEN-03 | §1, §5.2, §8, F8/F9: independent owner and explicit public repair adapters |
| R07 / GEN-04 | §2–4, §7, F8/F10: configured argv/env/paths/limits, no installation constants |

Both DoD clauses require exact literal success/rejection and co-delivered command/
configuration semantics above. This design does not add executable or schema
migration, so no such implementation change is left undocumented.

Verification must read this complete boundary and its saved requirements, parse
all JSON, check error and result consistency, independently inspect the literal
vectors, and run only task-focused baseline tests and maintained smoke. Preserve
negative check outputs. Source-author self-review is not independent inspection.
No passing contract checker proves an unimplemented provider or release readiness.
