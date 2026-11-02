# Настройка start-app для новых деревьев Poise и ERP

## Подключение установленного инструмента

[Обычные пять команд и постоянный вход](maintenance-profile-v2.md#обычные-команды-start-app).
Этот раздел описывает настройку владельца подготовки Task-worktree.

[Заполните рабочие конфиги по инструкции ниже](#заполнение-двух-конфигов-подготовки).
Штатная подготовка новых Task-worktree запускает самостоятельный пакет
**environment-maintenance** командой `start-app`. SDK исполняет выбранные
цепочки внешних инструментов; Poise выделяет область, передаёт координаты,
сохраняет результат и вызывает настроенный плагин. SDK установлен отдельно от
исходников Poise и ERP. Обновление исходников инструмента отдельно от принятой
пакетной поставки не переключает потребителей.

### Условия

Нужны Linux/WSL, Bash, Git, jq, настроенные проекты с выделяемым Task worktree и
доступной базовой ревизией. Выберите совместимый Python действий Poise с его
зависимостями, отдельный Python установленного SDK, доступные PHP/Composer/Node/npm
для ERP и согласованный environment. В проверенной установке Python действий3.15
и Python SDK3.12 различаются. Их пути задаются явно. Все runtime, caches и данные
обслуживаемого окружения выбираются конфигом, а не текущим каталогом терминала.

Примеры `/srv/...` — значения для заполнения, не обязательные пути. Основной
checkout служит источником Git; ресурсы нового окружения создаются в выбранном
дочернем дереве и его собственном state. Подготовка независимого экземпляра без
Task использует тот же SDK с явно выбранными repository/profile/state:
[инструкция профиля](maintenance-profile-v2.md#развёртывание-из-примера).

Рабочий каталог внутри Poise сначала исключите из Git. Рабочие JSON/env, выбранные
пакеты и captures остаются вне VCS; отслеживаются только санитарные примеры и
инструкция. Существующие заполненные файлы обновляются с сохранением их данных.

### Заполнение двух конфигов подготовки

1. Выберите постоянный `operator_root`, установленный `start-app`, Python действий
   и источник принятого кода Poise. Проверьте `git check-ignore` для рабочего каталога.
2. Создайте рабочие копии [Poise configuration](../../config/worktree-preparation/poise-op.example.json)
   и [ERP configuration](../../config/worktree-preparation/erp.example.json).
3. Заполните оба файла и [values](#заполнение-values). Опубликуйте постоянные
   recipe-файлы из принятых примеров, перечисленных ниже.
4. Подключите `worktree_preparation.configuration` каждого проекта через
   [владельца конфигурации](../configuration/project-setup.md).
5. Запустите штатный bootstrap своей Task и проверьте её полную квитанцию.

| Поле configuration | Значение |
|---|---|
| `parameters.source_root` | Абсолютный источник Poise с совместимыми модулями и примерами. |
| `parameters.python` | Python действий Poise. |
| `parameters.start_app` | Entry принятой пакетной поставки, например `/srv/environment-maintenance/dist/installed/2.0.0/bin/start-app`. |
| `parameters.operator` | Постоянный каталог выбранных script/values/recipes. |
| `catalog_dir` | Шаблон `${task_root}/environment-maintenance/v2/profile`; имя поля протокола, содержимое — operation-профиль SDK2. |
| `inputs` | Снимки `prepare.sh` и values выбранного проекта в собственной Task. |
| `operations` | `prepare`, затем отдельный `check`. |
| `commands.prepare` | Bash, снимок script и девять координат из примера. |
| `commands.check` / `verify` | `start-app check --config .../app.json --parameters .../parameters.json --base-dir ...`. |
| `commands.repair` | Выбранный `start-app infra --requirements all`. |
| `handler.callable` | `apps.bootstrap.environment_result:handle` для operation-результата SDK2. |
| `handler.options` | `schema=null`, `succeeded:0`, `failed:1`, `success_statuses=[succeeded]`, `repairable_statuses=[failed]`, `repair_operations=[]`, имена repair/verify и `failure_exit_code=4` по примерам. |

Полный [справочник протокола](worktree-preparation.md#опции-рабочего-json) описывает
все поля, разрешение путей, snapshots и placeholders. Стандартный
`string.Template.substitute` рендерит значения; argv передаётся без shell-разбора.
Внутри явно выбранного Bash буквальный `$` в шаблоне записывается как `$$`.
Значения не проходят скрытый второй цикл подстановки.

### Заполнение values

[Poise values](../../config/worktree-preparation/poise-op-values.example.json),
[ERP values](../../config/worktree-preparation/erp-values.example.json) и
[ERP agent values](../../config/worktree-preparation/erp-agent-values.example.json)
имеют единый контракт. `publisher` на верхнем уровне выбирает постоянную копию
владельца публикации; `parameters` содержит явные строковые значения;
`derived_parameters` вычисляет значения по порядку стандартным шаблонизатором;
`prepare_command` задаёт argv SDK; `prepare_environment` — его явное окружение.

| Параметр values | Что заполнить |
|---|---|
| `author_name`, `author_email` | Git identity оператора. |
| `sdk_python` | Python принятого SDK, отдельно от Python действий. |
| `path` | PATH инструментов выбранного окружения. |
| `supervisor_config` | Рабочий app-конфиг цепочки подготовки выбранного приложения. |
| `profile_spec`, `patch_spec` | Спецификация Poise operation-профиля и его SDK binding. |
| `application_spec`, `consumer_spec` | Спецификации ERP operation-профиля и runtime consumer. |
| `sdk_binding_template` | Шаблон ERP `runtime.env`, публикующий выбранный SDK Python. |
| `env_executable` | Явный executable `env`. |
| `wheelhouse` | Подготовленные пакеты зависимостей Poise для configured pip. |
| `publisher`, `operator_source` | Постоянный `worktree_profile.py` и корень его выбранных operator assets. |
| ERP `node`, `php_executable`, `composer_executable` | Выбранные executables. |
| ERP `npm_entry_point`, `npm_executable` | npm CLI для выбранного Node и executable npm. |
| ERP `composer_home` | Явный cache/home Composer. |
| ERP `environment_source` | Явная операторская ссылка на утверждённый environment. Текущий consumer читает текст `erp-environment.example.env` из `operator_source`; подготовьте этот текст из утверждённого источника, как описано ниже. |
| ERP `terminal`, `display`, `xauthority`, `terminal_font` | Выбранная видимая консоль и display. Для автоматических проверок выбирается отдельный виртуальный display. |
| ERP `console_tool` | `examples/process-launch/start_console.py` из принятого пакета SDK. |
| ERP agent `base_supervisor_config` | Базовая ERP preparation chain, вызываемая wrapper. |
| ERP agent `jq_executable` | Стандартный jq для проекции настроек. |
| ERP agent `session_logs`, `archived_session_logs` | Существующие каталоги журналов агента, явно выбранные оператором. |

Derived-параметры примеров:

| Имя | Назначение |
|---|---|
| `module_path` | Источник Poise modules `${source_root}/src:${source_root}`. |
| `supervisor_parameters` | Публикуемый JSON координат под собственной Task. |
| `state_root` | Собственные данные обслуживаемого Poise под Task. |
| `consumer_root` | `${repository}/.runtime/environment-consumer`. |
| `application_source` | `${repository}`: tracked inputs самого целевого приложения. |
| `runtime_module_path` | Модули обслуживаемой копии и её `.runtime/dependencies`; для потребителя со своим layout задайте точный источник явно. |
| `template_id` | Выбранный шаблон проекта Poise. |
| Poise `adjust_request` | Рабочий пакет публичного изменения под собственным `${profile_root}/adjust-request.json`; заполните его по инструкции постоянного входа. |

Координаты вызова имеют приоритет над одноимёнными values. Для штатного Poise
supervisor выполняет публикацию профиля/binding, `infra`, `deps`, `check`.
ERP supervisor публикует собственные конфиги из целевого checkout, создаёт
отсутствующие working files, выполняет начальные `infra(environment,base)`, `deps`,
полные `infra`, `adjust`, `check`. `adjust` использует установленные зависимости.
Вызов `run` является отдельным запуском; preparation не запускает пользовательские
серверы скрыто.

Постоянные recipes копируются из соответствующих отслеживаемых примеров:

| Рабочее имя | Источник |
|---|---|
| `prepare.sh` | [prepare.sh](../../config/worktree-preparation/prepare.sh) |
| `worktree_profile.py` | [владелец публикации](../../src/poise_environment/worktree_profile.py) |
| `poise-profile.json` | [Poise profile](../../config/maintenance/start-app/profile.example.json) |
| `poise-sdk-binding.json` | [SDK binding](../../config/worktree-preparation/poise-sdk-binding.example.json) |
| `poise-op-supervisor.json`, `poise-op-supervisor-infra.json` | [app](../../config/worktree-preparation/poise-op-supervisor.example.json), [цепочка](../../config/worktree-preparation/poise-op-supervisor-infra.example.json) |
| `erp-profile.json`, `erp-consumer.json` | [profile](../../config/worktree-preparation/erp-profile.example.json), [consumer](../../config/worktree-preparation/erp-consumer.example.json) |
| `erp-runtime.example.env`, `erp-bootstrap.example.env` | [runtime](../../config/worktree-preparation/erp-runtime.example.env), [bootstrap](../../config/worktree-preparation/erp-bootstrap.example.env) |
| `erp-supervisor.json`, `erp-supervisor-infra.json` | [app](../../config/worktree-preparation/erp-supervisor.example.json), [цепочка](../../config/worktree-preparation/erp-supervisor-infra.example.json) |
| `empty.json` | [пустой список requirements](../../config/worktree-preparation/empty.example.json) |

Также подготовьте рабочий `$operator_root/erp-environment.example.env` из собственного
утверждённого environment ERP со всеми обязательными значениями, включая девять
units. Это **рабочий** текстовый вход consumer, исключённый из Git, несмотря на
имя example. Поле `environment_source` само не копирует его: точный источник
объявлен в `erp-consumer.json`. При применении стандартного текстового шаблона
координаты `${repository}` берутся из parameters; буквальный `$` сохраняется как
`$$`. Перед применением проверьте согласованные порты, URLs и полноту значений.
Санитарная форма исходного environment принадлежит самому ERP, а filled values
этой установки остаются у оператора.

Для agent-профиля добавьте [wrapper app](../../config/worktree-preparation/erp-agent-supervisor.example.json)
и [цепочку](../../config/worktree-preparation/erp-agent-supervisor-infra.example.json)
под именами из values. Wrapper после базовой подготовки создаёт отсутствующий
`session-history.json` из tracked example ERP, задаёт logs/path_roots, проверяет
конечный файл собственным consumer и наличие `planning-workspace/.env`.
Существующие корректные пользовательские настройки сохраняются.

## Постоянное правило Git для ERP

До подключения проверьте ignore для трёх путей `OWNER/tests/playwright/node_modules`,
где OWNER — `platform-infrastructure`, `planning-workspace`, `system-admin`.
Для linked worktrees владельцем локальных правил служит общий Git каталог ERP.
Определите его реальным Git, а не путь `.git` дочернего дерева:

```bash
erp_repository=/srv/projects/erp
git -C "$erp_repository" rev-parse --path-format=absolute --git-common-dir
git -C "$erp_repository" check-ignore -v --no-index -- system-admin/tests/playwright/node_modules
```

Оператор сохраняет полный существующий `info/exclude` и его права. Добавьте
недостающие точные правила в этот существующий рабочий файл под его собственным
эксклюзивным `exclude.lock`, с проверкой исходных bytes и атомарной публикацией;
сохраните прежние строки. Для принятой установки требовалась только строка:

```gitignore
/system-admin/tests/playwright/node_modules
```

Для новой установки проверьте все три пути: первое правило может уже принадлежать
tracked `.gitignore`, остальные — common `info/exclude`. Правила локальной
конфигурации не коммитятся. Подтвердите `git check-ignore -v --no-index` в основном
и дочернем деревьях, чистый child status и сохранение основного index/WIP.
Общий владелец распространяет настройку на последующие linked worktrees этой
установки; независимый новый clone требует собственного заполнения оператором.
Это обычная рабочая настройка, а не дополнительный ремонт при каждом bootstrap.

## Полный рабочий prepare.sh

Используйте полный [отслеживаемый thin entry](../../config/worktree-preparation/prepare.sh)
без встраивания цепочек установки в этот script. Он читает `publisher` выбранных
values и передаёт девять аргументов владельцу `prepare`:

| Позиция | Значение |
|---|---|
| 1 | `source_root`: выбранный источник Poise. |
| 2 | `python`: Python действий. |
| 3 | `start_app`: выбранный пакетный entry. |
| 4 | `project_id`: действительный проект. |
| 5 | `repository`: действительный worktree. |
| 6 | `task_root`: собственная папка Task. |
| 7 | `profile_root`: выбранный operation-профиль. |
| 8 | `base_ref`: закреплённая базовая ревизия. |
| 9 | `selected_values`: сохранённый снимок values. |

Публикация конфигов выполняется `worktree_profile.py publish --spec PATH
--source PATH --output PATH --parameters PATH`; все четыре аргумента обязательны.
`prepare` принимает ровно девять позиционных координат. Публикация переносит
объявленные assets/text/JSON и сохраняемые пути; исполнения инфраструктуры в ней нет.
Цепочки реально выполняет SDK через выбранные supervisor configs.

## Привязка проектов и первый запуск

В manifest каждого проекта через его владельца подключите собственный рабочий файл:

```json
{
  "worktree_preparation": {
    "configuration": "/srv/poise/operator/start-app-worktrees/erp.json"
  }
}
```

Для Poise выберите его `poise-op.json`. Относительный configuration считается от
manifest. При первом добавлении optional секции действует [известное ограничение
editor](../operations/known-bugs.md#poise-project-optional-section-first-add-001);
разрешённое восстановление сохраняет locks, исходные bytes/modes/revision и историю.

Передайте пакет своему доставленному launcher, явно выбрав нужный проект:

```json
{
  "operation": "bootstrap",
  "input": {
    "task": {"id": "ERP-TASK-001"},
    "decision": null,
    "feedback": null,
    "rework_stage": null
  },
  "messages": []
}
```

Poise сначала выделяет законную область, затем сохраняет snapshots и запускает
подготовку. Подтверждение владения и готовность окружения — разные факты.
Полный протокол чтения: [запуск и результат](worktree-preparation.md#запуск-и-чтение-результата).

## Результат, повтор и продолжение

Проверьте полный receipt: выбранные входы/digests, фактический repository/profile,
исходную ревизию, все операции, полные stdout/stderr и реальные exit codes.
Успех требует законченных команд и отдельного `check`; наличие файлов само по себе
не заменяет готовность. Preview нужен для чтения агентом, полный capture — для
машинных решений. [Неизвестная ошибка и единственная попытка
исправления](worktree-preparation.md#обработчик-и-одна-попытка-исправления)
принадлежат выбранному плагину.

После известного отказа исправьте установленную причину и передайте новый
`preparation_request_id`; пример и границы повторов —
[продолжение после отказа](worktree-preparation.md#продолжение-после-отказа).
Повтор завершённого request читает его историю. Неопределённый исход сначала
согласует владелец операции.

Новые конфиги ERP используют create-only публикацию пользовательских env/settings.
Сохранённое старое дерево обслуживается по [инструкции миграции](maintenance-profile-v2.md#сохранённые-настройки-erp),
с собственным утверждённым источником значений и последующей новой полной подготовкой.
Успешная миграция или отдельный tooling smoke не являются её квитанцией READY.

## Сохранение результатов и освобождение

Перед очисткой передайте результаты всем предусмотренным потребителям, завершите
независимую приёмку и интеграцию через их владельцев. Постоянные selected SDK,
operator recipes, рабочие manifests/configs и данные приложений находятся вне
удаляемой Task. Captures и временные тестовые конфиги принадлежат Task;
инциденты и ручные исправления до очистки сохраняются в
[общем постоянном журнале](../operations/harness-incident-journal.md#постоянное-хранилище).

После доставки остановите только собственные проверочные процессы, освободите
порты/Task/worktree claims и удалите собственные временные области через владельца.
Работающие чужие экземпляры и их процессы сохраняются. Последующие обновления
поставки выполняются по [общему порядку](maintenance-profile-v2.md#согласованное-обновление-потребителей).
