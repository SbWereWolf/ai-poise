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

| Команда | Аргументы | Назначение |
|---|---|---|
| `work` | CLI-аргументов нет; JSON stdin; выбранный `POISE_CONFIG` и родной контекст caller. | Task/Sprint, результаты этапов, проверки, передача роли и артефакты. |
| `result-view` | Обязательные `--response PATH --view brief\|expanded\|full --output-chars INT`. | Представление уже сохранённого полного ответа с явным бюджетом. |
| `next` | Обязательный `--settings PATH`, необязательный `--project EXACT_ID`. | Доступные задачи настроенных проектов без захвата. |
| `requirements` | Обязательный `--config PATH`; JSON stdin. | Query/Command реестра требований выбранного проекта. |
| `skills` | Обязательные `--catalog PATH --root PATH`; JSON stdin. | Метаданные, проверка декомпозиции и prepare инструкций по файлам без Task. |

Обычная работа использует launcher, выданный нативным hook для этой сессии:

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
[подготовка инструкций по файлам](../configuration/development-routing.md#подготовка-инструкций-по-файлам).
Это отдельный режим того же dispatcher; вызов не требует Task и не подключает
автоматическое получение инструкций к native hooks.

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
до intent и побочных эффектов публикации. `reconfigure` меняет только `hooks_file`,
`hooks_lock`, `repository_scope`, `source_root`, сохраняя остальные значения.
Добавление или изменение настроек сводки этой операцией не поддерживается;
[граница
публикации](../operations/known-bugs.md#hook-settings-continuity-001--изменение-settings-нарушает-согласованность-привязок)
остаётся отдельной операторской обязанностью.

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

[Инструкция публикации полного рабочего профиля
catalog/v2](maintenance-profile-v2.md#развёртывание-из-примера).
[Инструкция настройки команды и рабочего файла
Bootstrap](bootstrap-catalog-v2.md#настройка-команды-и-рабочего-файла).
[ERP-профиль](maintenance-profile-v2.md#профиль-erp) выбирается через
`--profile-spec`: его подготовка, параметры, обслуживание и диагностика описаны
отдельно от базового 14-ресурсного каталога и исторического requirements/v1.

`poise_environment.profile` публикует выбранный профиль. Полная команда и [все аргументы
материализатора](maintenance-profile-v2.md#аргументы-материализатора) находятся в инструкции.

### Действующий делегированный CLI

`poise infra`, `poise deps` и `poise check` передают управление установленному
внешнему `environment_maintenance` через выбранный Python. Этот маршрут
`requirements/v1` отделён от корневого Bootstrap данной поставки.

| Аргумент внешнего CLI | Назначение |
|---|---|
| `infra`, `deps`, `check` | Выбранная операция внешнего обслуживания. |
| `--catalog PATH` | Обязательный каталог требований. |
| `--repairs PATH` | Необязательное описание исправлений. |
| `--values PATH` | Файл значений параметров; допускается повторение. |
| `--set NAME=VALUE` | Переопределение параметра; допускается повторение. |
| `--dry-run` | Пробный запуск внешней операции. |
| `-h/--help` | Справка установленного внешнего CLI. |

```bash
"${poise[@]}" check --catalog "$requirements_catalog" --values "$values_file"
"${poise[@]}" infra --catalog "$requirements_catalog" --repairs "$repairs_file" --dry-run
"${poise[@]}" deps --catalog "$requirements_catalog" --values "$values_file" \
  --set python=/srv/poise/tool-venv/bin/python --dry-run
```

`requirements_catalog`, `repairs_file`, `values_file` выбираются оператором
по контракту установленного внешнего приложения.

### Подготовленный standalone Bootstrap

```bash
cd "$poise_root"
"$bootstrap_python" -I -B "$poise_root/bootstrap" check \
  --config "$poise_root/config/bootstrap.json" --catalog-dir "$catalog_dir"
"$bootstrap_python" -I -B "$poise_root/bootstrap" infra \
  --config "$poise_root/config/bootstrap.json" --catalog-dir "$catalog_dir" --dry-run
"$bootstrap_python" -I -B "$poise_root/bootstrap" deps \
  --config "$poise_root/config/bootstrap.json" --catalog-dir "$catalog_dir" --dry-run
```

Все аргументы, настройки, интерфейс плагина, результаты и порядок исправления
описаны в [руководстве Bootstrap](bootstrap-catalog-v2.md).
Оператор отдельно выбирает и устанавливает профиль эксплуатационного каталога.

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
