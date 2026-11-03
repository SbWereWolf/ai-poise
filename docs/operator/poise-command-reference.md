# Команды Poise

Справочник CLI этой поставки. Каждый пример предполагает явно выбранные
настройки, входной JSON и права на соответствующую операцию. Описание команды
само по себе не разрешает публикацию, интеграцию, восстановление или миграцию.

## Запуск и общие аргументы

Используйте установленный Python выбранной поставки:

```bash
poise=( "${poise_python:?Укажите абсолютный путь установленного Python Poise}" -I -B -m poise )
"${poise[@]}" --help
```

В примерах ниже оператор заранее задаёт абсолютные пути: `project_json` —
конфигурация проекта; `settings` — настройки владельца вызываемой операции;
`packet` — файл одного корректного JSON-пакета по указанной документации.
Одноимённая переменная в разных разделах означает соответствующий файл
конкретного владельца, а не универсальный settings-файл.

Все команды поддерживают `-h/--help`. Значения после опций передаются отдельным
аргументом. Команды с `< "$packet"` читают один JSON из stdin. Пакеты и права
определяет связанная документация; CLI-справочник описывает аргументы запуска.
Рабочие настройки относятся к конкретной установке и сохраняются вне VCS.
Текущая установленная версия потребителя может отличаться от этой поставки;
её обновляют отдельно пакетным менеджером.

## Работа и чтение результатов

Подготовка выделенной области после `bootstrap`, полный рабочий конфиг,
`preparation_request_id`, квитанции и повтор после отказа описаны в
[инструкции подготовки](worktree-preparation.md#подготовка-рабочей-области).
Автоматическое подключение установленного `start-app` к обоим проектам:
[полная настройка и примеры](start-app-worktree-setup.md#подключение-установленного-инструмента).
Это не отдельная CLI-команда: используется operation=bootstrap в обычном work-пакете.
Для чтения текущей привязанной Task корректный show query имеет форму
`{"id":"current-task","kind":"task"}`; выбор рабочей задачи выполняется отдельным bootstrap.
Для адресного чтения без выбора рабочей Task используйте query
`{"id":"selected","kind":"task","task_id":"EXAMPLE-TASK-001"}` при наличии
этого ID в выбранном проекте. Полный ID, буквальный фрагмент, исходы поиска
и применимый контекст описаны в
[договоре адресного чтения](../workflows/batch-work.md#адресное-чтение-задачи).

| Команда | Аргументы | Назначение |
|---|---|---|
| `work` | CLI-аргументов нет; JSON stdin; выбранный `POISE_CONFIG` и родной контекст caller. | Task/Sprint, результаты этапов, проверки, передача роли и артефакты. |
| `handoff-proxy` | Обязательный абсолютный `--settings PATH`; JSON stdin, настоящий native caller. | Полная техническая передача готового результата и уведомление назначенного чата одним вызовом. |
| `result-view` | Обязательные `--response PATH --view brief\|expanded\|full --output-chars INT`. | Представление уже сохранённого полного ответа с явным бюджетом. |
| `tool-result` | Обязательные абсолютный `--policy PATH` и `--audience agent\|machine`; JSON stdin. Для session route `--config PATH`/`--checkout PATH` необязательны парой; non-session project-bound route требует оба абсолютных пути. | Общий вывод/сохранённые expanded/full последнего run и прямые capture/read операции без обязательной Task. |
| `next` | Обязательный `--settings PATH`, необязательный `--project EXACT_ID`. | Доступные задачи настроенных проектов без захвата. |
| `requirements` | Обязательный `--config PATH`; JSON stdin. | Query/Command реестра требований выбранного проекта. |
| `source-index` | Обязательные абсолютные `--config PATH --checkout PATH --policy PATH`; JSON stdin; необязательный `--handler`. | [Поиск и обновление индекса выбранной копии](../configuration/source-index.md#запросы-и-первоначальный-индекс), Git callback и optional sync handler без обязательной Task. |
| `placement` | Обязательные абсолютные `--config PATH --checkout PATH --policy PATH`; JSON stdin. | Выбор, закрепление и проверка места рабочих результатов по политике проекта. |
| `skills` | Обязательные `--catalog PATH --root PATH`; JSON stdin. | Метаданные, проверка декомпозиции и prepare инструкций по файлам без Task. |
| `checks` | Обязательные абсолютные `--config PATH --checkout PATH --policy PATH` и явный `--view brief\|expanded\|full`; JSON stdin. Необязательные `--transport-policy PATH` и `--invocation ID` включают coordination подготовленного вызова. | Именованные проверки, пакеты, наборы, чтение результата, калибровка и impact без Task. |
| `checks-mcp` | Обязательные абсолютные `--config PATH --checkout PATH --policy PATH --transport-policy PATH`; JSON-RPC stdio. | Настроенные run/preview/cancel с ожиданием конечного результата и полным машинным ответом. |

### Прокси передачи результата

`handoff-proxy --settings PATH` читает `deliver` либо `resolve_notification`
из stdin. Exit 0 подтверждает `delivered`; exit 2 сохраняет препятствие или
неизвестный исход. Полные настройки, источник каждого обязательного значения и
различие source/package запусков принадлежат
[операторской подготовке](agent-handoff-proxy.md#подготовка-и-настройки).
Готовый native packet, сообщение и получатель задаются до
[одного вызова](agent-handoff-proxy.md#пакет-и-один-вызов);
неизвестное уведомление продолжается по
[явному подтверждению](agent-handoff-proxy.md#продолжение-и-явное-подтверждение).

### Результаты инструментов


`tool-result` принимает capture/view/summary/lines/bytes/search/hook и
[expanded/full последнего
результата](../configuration/tool-results.md#чтение-последнего-результата).
Настройка, полные обезличенные session/handler и прежние четыре capture/read примера,
caller, machine/agent, исходный exit и восстановление принадлежат
[единому справочнику](../configuration/tool-results.md#подготовка-и-вызов).
Config/checkout передаются вместе при выборе проекта; отсутствие пары поддержано
session route, не project-bound policy без session. Сам CLI получает готовый source,
не запускает производителя и не устанавливает native hooks. Самостоятельный result-view
в таблице остаётся reference API сохранённого JSON, а не способом F08.

### Именованные проверки проекта

```bash
"${poise[@]}" checks --config "$project_json" --checkout "$checkout" \
  --policy "$checks_policy" --view expanded < "$packet"
```

`checks_policy` — заполненный абсолютный путь отдельной политики обычных проверок.
Семь JSON-операций: `list`, `describe`, `preview`, `run`, `result`, `calibrate`,
`package_impact`. Их selectors/options и схемы, заполнение четырёх примеров,
подлинный native/generated caller, предпосылки и восстановление принадлежат
[каноническому справочнику](../configuration/named-project-checks.md#запросы-и-выбор).
`preview.available` не подтверждает executable или исполнение. `--view full`
обращает к сохранённому полному ответу; чтение не повторяет ребёнка.
Команда `check` ниже относится к обслуживанию окружения и не является этим API.

Для MCP-клиента запускается процесс выбранной поставки:

```bash
"${poise[@]}" checks-mcp --config "$project_json" --checkout "$checkout" \
  --policy "$checks_policy" --transport-policy "$checks_transport_policy"
```

`checks_transport_policy` — отдельная заполненная policy транспорта. Имена tools,
версии протокола, run/preview/cancel arguments, ограничения, собственный invocation,
результаты и восстановление описаны в
[каноническом справочнике MCP](../configuration/named-project-checks.md#mcp-stdio-настройка-и-вызов).
Обычный `checks` сохраняет полный named-check договор. При явном
`--transport-policy` он использует ту же coordination; `--invocation` потребляет
настоящую подготовленную Pre-ссылку соответствующего запроса и требует
`--transport-policy`. Этот ID не выбирают
произвольно и не заимствуют у другого caller или проекта. Подключение не требует Task.

Работа над Task использует launcher, выданный нативным hook для этой сессии:

```bash
bash "${AI_POISE_WORK:?Путь родного session work.sh}" < "$packet"
"${poise[@]}" result-view --response "$response" --view expanded --output-chars 3000
"${poise[@]}" next --settings "$settings"
"${poise[@]}" next --settings "$settings" --project "$project_id"
"${poise[@]}" requirements --config "$project_json" < "$packet"
"${poise[@]}" skills --catalog "$skill_catalog" --root "$skill_root" < "$packet"
```

`response` — существующий response.json; `project_id` — точный настроенный ID;
`skill_catalog` и `skill_root` — явно выбранные каталог метаданных и корень навыков.
Full view возвращает ссылку на полный файл. Чтение результата не повторяет операцию.

Для skills/prepare packet содержит ровно operation=prepare, project_config,
checkout, policy, paths, previous. Первые три пути абсолютные, paths — непустой
набор конкретных относительных файлов, previous=null при первом запросе.
--catalog и --root выбирают metadata и корень инструкций независимо от checkout.
Полная форма, заполнение обезличенного примера, BODY/готовность/диагностика/refresh:
[подготовка инструкций по
файлам](../configuration/development-routing.md#подготовка-инструкций-по-файлам).
Это отдельный режим того же dispatcher; вызов не требует Task и не подключает
автоматическое получение инструкций к native hooks.

Для placement задайте абсолютные `checkout`, `placement_policy` и путь
`placement_packet` к JSON с operation/items/authorizations:

```bash
"${poise[@]}" placement --config "$project_json" --checkout "$checkout" \
  --policy "$placement_policy" < "$placement_packet"
```

Нужен настоящий native caller либо разрешённый persistent binding. Task не
обязательна. Полные prerequisites, policy options, response/exit и recovery:
[размещение результатов](../configuration/result-placement.md#подготовка-проекта-и-вызов).

Владельцы: [API работы](../workflows/batch-work.md),
[представления сохранённого
результата](../workflows/runner.md#чтение-сохранённого-результата-без-повторного-выполнения),
[доступные задачи](../configuration/project-setup.md#доступные-задачи-во-всех-проектах),
[реестр требований](../workflows/requirements-registry.md#декларативный-api-и-согласование),
[метаданные навыков](../configuration/skill-catalog.md#чтение-без-task-и-harness).
Прямой `work` и происхождение identity описаны в
[границе сессии](../configuration/runtime-services.md#rd-002-единая-граница-сессии).

## Проект и его настройки

| Команда | Аргументы | Назначение |
|---|---|---|
| `project` | `--settings PATH`; creation JSON stdin. | Создание настроенного проекта одним пакетом. |
| `project list` | `--settings PATH`; без stdin. | Список проектов. |
| `project check` | `--settings PATH`; check JSON stdin. | Проверка согласованности выбранного проекта. |
| `project-config` | `--settings PATH`; JSON stdin. | Публичное обновление действующего проекта. |
| `project-init` | Все обязательны: `--settings PATH --template ID --destination PATH --request-id ID --probe-repository yes\|no`. | Интерактивная подготовка выбранного шаблона. |
| `goal-config` | `--settings PATH`; JSON stdin. | Декларативный редактор конфигурации целей и процессов. |
| `catalogue` | `--settings PATH`; JSON stdin. | Установка процессов или подготовка типизированной задачи. |

```bash
"${poise[@]}" project --settings "$settings" < "$packet"
"${poise[@]}" project list --settings "$settings"
"${poise[@]}" project check --settings "$settings" < "$packet"
"${poise[@]}" project-config --settings "$settings" < "$packet"
"${poise[@]}" project-init --settings "$settings" --template "$template_id" \
  --destination "$destination" --request-id "$request_id" --probe-repository yes
"${poise[@]}" goal-config --settings "$settings" < "$packet"
"${poise[@]}" catalogue --settings "$settings" < "$packet"
```

`template_id` — зарегистрированный шаблон; `destination` — целевой путь;
`request_id` — стабильный идентификатор намерения. В интерактивном режиме
используются `set <JSON>`, `keep`, `back`, `publish`, `abort`.

Владельцы: [пакетное создание](../configuration/project-setup.md#пакетный-путь-для-агента),
[проверка проекта](../configuration/project-setup.md#проверка-согласованности-проекта),
[интерактивная подготовка](../configuration/project-setup.md#интерактивный-путь),
[обновление проекта](../configuration/project-setup.md#обновление-действующего-проекта),
[goal-config](../configuration/goal-config.md#пакет),
[каталог процессов](../configuration/workflow-catalogue.md#cli-и-api).

## Runtime и native hooks

Для общей доставки launcher из рабочих деревьев используйте
`runtime-config` с `operation: reconfigure`: полный целевой settings и definition,
наблюдённые редакции исходных settings и обоих native-файлов, стабильный request ID.
Настройте одну область репозитория и общий writer lock, затем выполните штатный
review/trust hooks в Codex. Поля, повтор после частичной записи и проверка реального
startup/fork описаны в [общей
установке](../configuration/runtime-hooks.md#общая-установка-для-рабочих-деревьев).
Целевые settings содержат обязательную `capability_summary`; её проверка выполняется
до intent и побочных эффектов публикации. `reconfigure` меняет `hooks_file`,
`hooks_lock`, `repository_scope`, `source_root`, `project_launcher` и `capability_summary`,
сохраняя остальные значения. Добавление секции сводки передаётся явно полным целевым
объектом settings. На прежнем пути native hooks локальная установка не требует
`repository_scope`/`hooks_lock`; перенос в другой файл требует общей области и lock.

После сбоя повторяйте тот же пакет: проверенные новые native-сессии включаются
в незавершённый перенос с сохранением их сообщений и истории.

| Команда | Аргументы | Назначение |
|---|---|---|
| `runtime` | `--settings PATH`; JSON stdin. | Identity, capabilities, transcript и один work-пакет через общий владелец сессии. |
| `runtime-setup` | `--settings PATH --max-input-bytes INT`; JSON stdin. | Создание настроек hook-установки одним пакетом; явный лимит входа. |
| `runtime-config` | `--settings PATH`; JSON stdin. | Установка hook-конфигурации, probes, сохранённое чтение `summary` и явное обновление `refresh`. |
| `hook` | `--settings PATH --definition PATH`; native event JSON stdin. | Доставка события хоста по неизменяемому определению установки. |
| `hook-work` | `--settings PATH --binding PATH [--project EXACT_ID]`; work JSON stdin. | Выполнение по созданной hook привязке сессии. |

```bash
"${poise[@]}" runtime --settings "$settings" < "$packet"
"${poise[@]}" runtime-setup --settings "$settings" --max-input-bytes 262144 < "$packet"
"${poise[@]}" runtime-config --settings "$settings" < "$packet"
"${poise[@]}" hook --settings "$settings" --definition "$definition" < "$native_event"
"${poise[@]}" hook-work --settings "$settings" --binding "$binding" < "$packet"
```

`definition` — фактический неизменяемый файл определения; `native_event` —
настоящий вход события; `binding` — фактически созданная hook привязка.
Хост вызывает hook, а выданный launcher использует hook-work.
Оператор настраивает установку; агент сохраняет её настоящий контекст.

Для чтения последней сводки пакет содержит только свою фактическую binding:

```json
{"operation": "summary", "input": {"binding_path": "/absolute/path/to/own/binding.json"}}
```

Для повторной проверки изменившейся инфраструктуры:

```json
{"operation": "refresh", "input": {"binding_path": "/absolute/path/to/own/binding.json"}}
```

Оба пакета передаются той же командой `runtime-config`. `summary` не выполняет probes;
`refresh` использует workspace настоящего SessionStart. Ответ содержит `status` и
`summary`; значения кодов задаёт `exit_codes` установки. После успешной инициализации `HookService`
отсутствующая/повреждённая сводка или чужая привязка дают `rejected`. Точные результаты,
отказы инициализации сервиса (settings, связанная конфигурация проекта, runtime association)
с exit `2`/stderr без JSON и восстановление —
[сводка готовности сессии](../configuration/runtime-hooks.md#сводка-готовности-сессии).

Владельцы: [runtime
adapter](../configuration/runtime-services.md#один-существующий-api-два-транспорта),
[интерфейсы hooks](../configuration/runtime-hooks.md#интерфейсы),
[настройки установки](../configuration/runtime-hooks.md#настройки-установки),
[первоначальная
настройка](../configuration/runtime-hooks.md#первоначальная-настройка-без-ручного-json-файла).

## Обслуживание окружения

[Полная инструкция собственного
operation-профиля](maintenance-profile-v2.md#развёртывание-из-примера)
содержит подготовку, заполнение рабочих файлов и последовательность `infra → deps → check → run`.
[Все аргументы материализатора](maintenance-profile-v2.md#аргументы-материализатора) и
[контракт Bootstrap](bootstrap-catalog-v2.md#конфигурация-команды) имеют своих владельцев.
`poise_environment.profile` публикует файлы, отдельный SDK выполняет действия.

### Установленный start-app2.0

[Настройка рабочих деревьев](start-app-worktree-setup.md#подключение-установленного-инструмента)
выбирает standalone SDK2. Полный [профиль ERP](maintenance-profile-v2.md#профиль-erp)
и [обновление всех потребителей](maintenance-profile-v2.md#согласованное-обновление-потребителей)
имеют собственные явные конфиги. Каждая операция исполняет одну выбранную цепочку. `adjust` ниже выбирает ERP
`erp_profile`/`erp_repository`, где операция объявлена; собственный Poise app
содержит `infra`, `deps`, `check`, `run`:

```bash
"$start_app" infra --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository" --requirements all
"$start_app" deps --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository" --requirements all
"$start_app" adjust --config "$erp_profile/app.json" --parameters "$erp_profile/parameters.json" --base-dir "$erp_repository" --requirements all
"$start_app" check --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository"
"$start_app" run --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository" --requirements all
```

| Аргумент | Назначение |
|---|---|
| `infra`, `deps`, `adjust`, `check`, `run` | Явная операция выбранного app-конфига. |
| `--config PATH` | App JSON с путями операций и выбранными интерпретаторами. |
| `--parameters PATH` | JSON значений стандартных placeholders команд. |
| `--base-dir PATH` | Явный корень обслуживаемого экземпляра. |
| `--requirements all` | Обязательный выбор для `infra`/`deps`/`adjust`/`run`; все требования операции либо JSON-массив ID. `check` наблюдает весь ресурсный набор без этого аргумента. |
| `--version` | Версия установленной поставки; применяется при её приёмке/обновлении. |
| `-h`, `--help` | Справка выбранного entry/операции. |

Схема команд/ресурсов описана [у владельца профиля](maintenance-profile-v2.md#каталог-и-параметры).
`run` выполняет только запуск. Рабочий env и SDK могут быть выбраны consumer wrapper
ERP; runtime selector и параметры профиля должны соответствовать одной поставке.
Следующий делегированный CLI является отдельным сохранённым requirements/v1
контрактом, выбранное2.0 подключение его не использует.

### Действующий делегированный CLI

`poise infra/deps/check` — отдельное делегирование выбранному внешнему
`environment_maintenance`, сохраняющее его requirements/v1 контракт.
Выберите совместимую установленную поставку и её собственные входы:

| Аргумент внешнего CLI | Назначение |
|---|---|
| `infra`, `deps`, `check` | Операция выбранного делегированного инструмента. |
| `--catalog PATH` | Обязательные требования этого отдельного профиля. |
| `--repairs PATH` | Необязательное описание его исправлений. |
| `--values PATH` | Повторяемый файл значений. |
| `--set NAME=VALUE` | Повторяемое переопределение. |
| `--dry-run` | Пробный режим выбранного контракта. |
| `-h/--help` | Справка установленного приложения. |

```bash
"${poise[@]}" check --catalog "$requirements_catalog" --values "$values_file"
"${poise[@]}" infra --catalog "$requirements_catalog" --repairs "$repairs_file" --dry-run
"${poise[@]}" deps --catalog "$requirements_catalog" --values "$values_file" --set python="$actions_python" --dry-run
```

Эти входы относятся к выбранному делегированному оператору. Новый собственный
operation-профиль и ниже описанный Bootstrap выбираются отдельно.

### Подготовленный standalone Bootstrap

После [подготовки собственного профиля](maintenance-profile-v2.md#развёртывание-из-примера)
используйте опубликованный `profile/bootstrap.json`, Python Poise и выбранный repository:

```bash
"$poise_python" -I -B "$poise_source/bootstrap" infra --config "$profile/bootstrap.json" --base-dir "$repository"
"$poise_python" -I -B "$poise_source/bootstrap" deps --config "$profile/bootstrap.json" --base-dir "$repository"
"$poise_python" -I -B "$poise_source/bootstrap" check --config "$profile/bootstrap.json" --base-dir "$repository"
"$poise_python" -I -B "$poise_source/bootstrap" run --config "$profile/bootstrap.json" --base-dir "$repository"
```

Каждая строка выполняет одну операцию. `run` только запускает заданную цепочку.
Для этого профиля обязательны непустой `--base-dir` и `--config`; отдельный каталожный
потребитель вместо `--base-dir` выбирает `--catalog-dir` и свою совместимую рабочую
команду. [Все флаги, контексты и
результаты](bootstrap-catalog-v2.md#подготовка-и-безопасная-проверка)
включают справку, порядок передаваемых опций и единственную разрешённую попытку
исправления. [ERP-профиль](maintenance-profile-v2.md#профиль-erp) выбирается явно
своей спецификацией и конфигами; автоматическая Task-подготовка описана
[отдельно](worktree-preparation.md#подготовка-рабочей-области).

### Обслуживание catalog/v2 через Stride

Заполненный [пример launcher](../configuration/stride-launcher.md#выполнение-команды)
направляет `infra`, `deps`, `check` в корневой Bootstrap. Это другой маршрут,
чем прямой делегированный `poise check --catalog` выше. Перед его выбором пройдите
[процедуру перехода и возврата](maintenance-profile-v2.md#переход-на-новый-маршрут-и-возврат).
Значения ниже — явно выбранные пути существующего каталожного оператора;
`catalog-bootstrap.json` — его заранее подготовленная совместимая рабочая команда,
а не копия нового operation-примера. Рабочие настройки остаются вне Git:

```bash
stride_entry=/srv/poise/app/stride.sh
poise_root=/srv/poise/app
project_id=example
stride_settings="$poise_root/config/stride.env"
profile="$poise_root/operator/poise-profile"
bootstrap_config="$profile/catalog-bootstrap.json"
bash "$stride_entry" --poise-root "$poise_root" --project "$project_id" \
  "$stride_settings" check --config "$bootstrap_config" --catalog-dir "$profile"
bash "$stride_entry" --poise-root "$poise_root" --project "$project_id" \
  "$stride_settings" infra --config "$bootstrap_config" --catalog-dir "$profile" \
  --values "$profile/values.json" --set python=/srv/poise/actions/bin/python --dry-run
bash "$stride_entry" --poise-root "$poise_root" --project "$project_id" \
  "$stride_settings" deps --config "$bootstrap_config" --catalog-dir "$profile"
```

Префикс уже содержит имя операции; launcher заменяет только первый её token,
остальные аргументы передаёт буквально. Для ERP выбирают его собственные
`profile` и `bootstrap_config="$profile/bootstrap.json"`.
`--config` выбирает рабочую команду и плагин, `--catalog-dir` — полный профиль,
`--values` — значения каталога, `--set` — явное переопределение, `--dry-run` —
наблюдение без исправления. Повторяемость, приоритет и все аргументы описаны
в [контракте Bootstrap](bootstrap-catalog-v2.md#конфигурация-команды).

## Локальные файлы, резервные копии и миграции

Эти операции выполняются по отдельному операторскому поручению.
Для backup create/restore и миграций подготовьте согласованное окно остановленных
writers по процедуре владельца.

| Команда | Аргументы | Назначение |
|---|---|---|
| `local-assets` | `--manifest PATH`; JSON stdin. | Восстановление объявленных локальных файлов из явного snapshot. |
| `backup help` | Аргументов нет. | Справка и требования операторского окна. |
| `backup list` | `--config PATH`. | Список имеющихся копий Task DB. |
| `backup create` | `--config PATH`. | Создание копии Task DB. |
| `backup restore` | `--config PATH NAME`. | Восстановление явно названной копии. |
| `route-migrate` | `--config PATH`; без stdin. | Явная миграция снятых ограничений route. |
| `task-workflow-migrate` | `--config PATH`, необязательный `--project-settings PATH`; JSON stdin. | Явная миграция выбранных workflow snapshots. |

```bash
"${poise[@]}" local-assets --manifest "$manifest" < "$packet"
"${poise[@]}" backup help
"${poise[@]}" backup list --config "$project_json"
"${poise[@]}" backup create --config "$project_json"
"${poise[@]}" backup restore --config "$project_json" "$backup_name"
"${poise[@]}" route-migrate --config "$project_json"
"${poise[@]}" task-workflow-migrate --config "$project_json" < "$packet"
"${poise[@]}" task-workflow-migrate --config "$project_json" \
  --project-settings "$settings" < "$packet"
```

`manifest` объявляет нужные файлы и существующий snapshot; `backup_name` —
реальное имя из backup list. `--project-settings` применяется к
`task-stage-criteria-migration-1` для выбора владельца публикации
`project-setup-settings-1`; эта опция не расширяет полномочия на другие задачи.

Владельцы: [локальные файлы](../configuration/local-assets.md#восстановление-и-результаты),
[условие резервного копирования](../task-db-backups.md#обязательное-условие),
[восстановление копии](../task-db-backups.md#восстановление),
[route migration](../route-count-limit-migration.md),
[workflow snapshot
migration](../task-workflow-snapshot-migration.md#миграция-типов-и-критериев-этапов).

Явный выбор через выданный своей сессии entry:

```bash
bash "$native_launcher" --project "$project_id" < "$packet"
```

Настройка, сообщения выбранного проекта и отказы описаны в
[контракте выбора](../configuration/runtime-hooks.md#явный-выбор-проекта-через-session-launcher).
Без селектора сохраняется исходный проект.

## Общее чтение частей файлов

| Команда | Обязательные аргументы | Результат |
| --- | --- | --- |
| `content-read` | Абсолютные `--config PATH --checkout PATH --policy PATH` и явный `--format json` либо `--format text`; JSON stdin. | Полный результат общего чтения без Task/Sprint; точные рамки и сохранённый JSON. |

Используйте выбранный установленный Python: `python -I -B -m poise content-read` с указанными
аргументами. Это пакет операций reader, без Work `input/messages`. Полные примеры и все поля —
[в контракте](../configuration/project-file-reading.md#семь-операций), заполнение политики —
[у её владельца](../configuration/project-file-reading.md#подготовка-политики-и-примеры), admission
и actual End/recover — [в жизненном
цикле](../workflows/file-intake-lifecycle.md#завершение-и-восстановление).
Специализированный `tools/source_read.py` с acknowledgement остаётся отдельным интерфейсом.


### Обычное чтение и состояние покрытия

Новых верхнеуровневых команд не требуется. После явного
[подключения шести событий](../configuration/runtime-hooks.md#подключение-событий-чтения)
обычный поддержанный Bash-вызов проходит установленный `hook` adapter до и после выполнения.
Агент не вызывает synthetic Start/End для допуска; hook CLI принимает настоящие host события.

`content-read --config ABS_CONFIG --checkout ABS_CHECKOUT --policy ABS_COMMAND_READER
--format json` принимает отдельный пакет `{"operation":"coverage"}` на stdin, без Work
input/messages. JSON-SDK execute имеет тот же пакет. Это metadata-only состояние своего
поколения: documents/ranges/operations, stale/pending/settled counts и omissions; items пустой.
Полный JSON, response_path, reason/action и error читаются без clipping. Exit/error различает
успех от admission/configuration/registry отказа; Post warning не отзывает выполненное чтение.

[Полная исполняемая форма](../configuration/project-file-reading.md#сводка-покрытия),
[options/examples](../configuration/project-file-reading.md#параметры-политики-обычных-команд) и
[продолжение после
отказов](../configuration/project-file-reading.md#решения-и-восстановление-учёта-команд)
принадлежат каноническому reader owner. Рабочий каталог доказан literal absolute cd prefix
либо независимым absolute operand; ambient cwd не угадывается.

## Запись телеметрии

Команда `telemetry` читает сохранённые наблюдения и управляет повторной доставкой.
[Политика, примеры и диагностика](../configuration/agent-metrics.md#чтение-и-повторная-доставка).
