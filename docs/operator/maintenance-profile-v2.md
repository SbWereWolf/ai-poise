# Рабочий профиль обслуживания catalog/v2

## Назначение и условия

Материализатор `poise_environment.profile` публикует полный каталог обслуживания
Poise из выбранной поставки и параметров оператора. Само обслуживание выполняет
внешний инструмент по действиям каталога. Команду запуска и обработчик результата
задаёт [Bootstrap](bootstrap-catalog-v2.md#настройка-команды-и-рабочего-файла).

Выделите отдельную папку профиля, используйте её же при повторной публикации.
Её родитель должен существовать. Обслуживаемые базы и пользовательские данные
размещайте вне заменяемого дерева профиля. Публикуйте при остановленных владельцах
записей профиля и обслуживаемых данных. Используйте существующий Git-репозиторий
и доступную локальную базовую ревизию. Эти условия относятся и к повтору.

В примере рабочая папка — `operator/profile/` внутри выбранной копии Poise;
она исключена из Git. Для другого расположения внутри репозитория настройте
соответствующее исключение до записи рабочих файлов. Исходные примеры остаются
отслеживаемыми. Пути `/srv/...` ниже — явно выбранные значения примера установки.

## Развёртывание из примера

Выберите поставку Poise с её `src/` и `config/`, Python 3.13 для Poise и окружение
внешнего инструмента с поддержкой `catalog/v2`. Устанавливайте внешний инструмент
пакетным менеджером по процедуре его поставки. Каждый Python, выполняющий действия,
должен иметь доступ к модулям `poise_environment` выбранной поставки.

```bash
poise_root=/srv/poise/app
poise_python=/srv/poise/poise-venv/bin/python
consumer_python=/srv/poise/environment-venv/bin/python
catalog_dir="$poise_root/operator/profile"
state_root=/srv/poise/data/maintenance-example
repository=/srv/projects/example
cd "$poise_root"
mkdir -p "$poise_root/operator"
PYTHONPATH="$poise_root/src" "$poise_python" -B -m poise_environment.profile \
  --source "$poise_root" --output "$catalog_dir" --state "$state_root" \
  --repository "$repository" --project example --base-ref main \
  --author-name Operator --author-email operator@example.invalid \
  --module-path "$poise_root/src"
```

Для этого source-запуска `PYTHONPATH` обеспечивает импорт материализатора;
`--module-path` задаёт доступ к модулям будущих действий, а не самого контроллера.
Здесь Python запускается без `-I`. Публичный корневой `bootstrap` ниже поддерживает
`-I`. Сама публикация не создаёт обслуживаемый state и не запускает engine.

### Аргументы материализатора

| Аргумент | Назначение |
|---|---|
| `--source PATH` | Обязательный корень выбранной поставки с примерами и assets. |
| `--output PATH` | Обязательная выделенная папка рабочего профиля. |
| `--state PATH` | Обязательный отдельный корень обслуживаемых ресурсов. |
| `--repository PATH` | Обязательный существующий корень целевого Git-репозитория. |
| `--project ID` | Обязательный идентификатор создаваемой конфигурации проекта. |
| `--base-ref REF` | Обязательная доступная локальная Git-ревизия. |
| `--author-name NAME` | Обязательное имя автора в конфигурации проекта. |
| `--author-email EMAIL` | Обязательная почта автора. |
| `--template ID` | Выбор из `templates` спецификации; по умолчанию `linux-reference`. |
| `--module-path PATH` | Значение `PYTHONPATH` действий; по умолчанию пустая строка для установленного доступного модуля. |
| `--profile-spec PATH` | Явная спецификация публикации; по умолчанию `config/maintenance/profile.example.json` внутри `--source`. |
| `-h`, `--help` | Стандартная справка argparse, без публикации. |

Source/output/state/repository приводятся к абсолютным путям от cwd запуска.
Явный относительный `--profile-spec` относится к cwd; ссылки на входы внутри
спецификации относятся к `--source`. Для воспроизводимости выбирайте абсолютные
аргументы, как в примере. Template должен быть объявлен выбранной спецификацией.

При успехе stdout содержит JSON со `status=created` либо `replaced`, абсолютным
`profile`, числом `requirements=14` и `state_created=false`; exit=0.
Перехваченная ошибка чтения, JSON, подстановки или подготовки возвращает
`status=invalid`, `error` и exit=2. Ошибки CLI и справка имеют стандартный вывод
argparse, а не этот JSON-контракт. Сохранность прежнего профиля при ошибке
чтения/разбора/подстановки входов проверена до публикации.

## Поставляемая спецификация

Отслеживаются три самостоятельных входа:

- [profile.example.json](../../config/maintenance/profile.example.json) — публикация;
- [environment-maintenance.example.json](../../config/maintenance/environment-maintenance.example.json) — полный каталог действий;
- [recommendations.example.json](../../config/maintenance/recommendations.example.json) — предметные рекомендации.

`profile.example.json` имеет schema `poise-maintenance-profile-1`:

| Поле | Назначение |
|---|---|
| `schema` | Идентичность спецификации публикации. |
| `catalog`, `settings`, `recommendations` | Пути входного каталога, примера project-setup и рекомендаций от source. |
| `templates` | Соответствие ID выбранного шаблона пути его blueprint от source. |
| `asset_directories` | Список объектов `source`, `destination`, `patterns`: исходная папка, назначение в профиле и явно поставляемые glob patterns. |
| `documents` | Проекции JSON: `source`, `destination`, список `edits` с `path` и `value`. |
| `workflow_source_prefix` | Явный префикс source references в blueprint, удаляемый при публикации assets. |
| `outputs` | Имена `catalog`, `settings`, `registry`, `edits`, `recommendations` внутри профиля. Выбранный v2 consumer читает `catalog.json`. |
| `settings_overrides` | Явные значения root/registry/lock публикуемого project-setup. |
| `preserve_paths` | Рабочие пути, переносимые из существующего профиля при повторе. |
| `project_edits` | Список явных правок project blueprint: `path` — массив ключей, `value` — значение. |

Задавайте destinations, outputs и preserve paths корректными относительными путями
внутри выбранного профиля. Источники должны содержать объявленные assets и поля
редактируемых документов. Значения правок и выходов рендерятся стандартным
`string.Template.substitute`: `$name`, `${name}`, `$$`. Поздние placeholders
в самих Task/workflow templates сохраняются как данные.

Рабочие настройки каталога и редактора создаются из sanitized примеров. Они
ссылаются на отдельные поставляемые templates и на проектные рабочие workflows.
Проект публикует существующий владелец `ProjectCommands/FileProjectSetup`;
его [конфигурация и параметры](../configuration/project-setup.md) остаются отдельным
контрактом. Профиль получает `setup.json`, `configured-projects.json`,
`project-edits.json`, `recommendations.json` и `catalog.json`.

### Каталог и параметры

Каталог имеет schema `environment-maintenance/catalog/v2`, `parameters`,
`inherit_env` и упорядоченные `requirements`. У каждой записи — `id` и три
действия `check`, `install`, `repair`. Каждое объявляет `argv`, `cwd`, `env`,
`output.schema`, `max_output_bytes` и `execution_bound_seconds`. В примере пределы
составляют 65536 байт и 60 секунд на действие; наследуются `PATH`, `HOME`, `LANG`.
Конкретное исполнение и рендер действий принадлежат внешнему потребителю.

Все 15 параметров примера строковые:

| Параметр | Источник значения опубликованного профиля |
|---|---|
| `python` | `sys.executable` материализатора; при обслуживании переопределяется выбранным Python действий через values. |
| `module_path` | `--module-path`. |
| `profile_root` | Абсолютный `--output`. |
| `state_root` | Абсолютный `--state`. |
| `repository` | Абсолютный `--repository`. |
| `project_id` | `--project`. |
| `base_ref` | `--base-ref`; исходный пример содержит `main`. |
| `author_name` | `--author-name`. |
| `author_email` | `--author-email`. |
| `git` | Выбранный пример: `git`. |
| `directory_mode` | Выбранный пример: `0750`. |
| `python_minimum` | Выбранный пример: `3.13`, включительно. |
| `python_maximum` | Выбранный пример: `3.14`, исключительно. |
| `template_id` | `--template`, по умолчанию `linux-reference`. |
| `remote` | Выбранный пример: `disabled`. |

Дополнительные значения принадлежат выбранному примеру, а не угадываются из
установки. Собственные схемы и диапазон Python характеризуют эту поставку;
версию внешнего пакета при запуске материализатор не проверяет.

## Ресурсы и действия

Пути в таблице — значения поставляемого примера, а не встроенные пути ядра.

| ID | Реальное наблюдение и владелец эффекта |
|---|---|
| `python-runtime` | Диапазон выбранного исполняемого Python; адаптер `python_runtime`. |
| `git-executable` | Реальный `git --version`; адаптер `git_executable`. |
| `target-repository` | Корень репозитория и доступность base ref; адаптер `repository`. |
| `state-root` | Сам `state_root`; адаптер `directory`. |
| `runtime-directory` | `state_root/runtime`; адаптер `directory`. |
| `task-directory` | `state_root/standalone`; адаптер `directory`. |
| `sprint-directory` | `state_root/sprints`; адаптер `directory`. |
| `worktree-directory` | `state_root/worktrees`; адаптер `directory`. |
| `requirements-directory` | `state_root/database`; адаптер `directory`. |
| `telemetry-directory` | `state_root/telemetry`; адаптер `directory`. |
| `poise-project-config` | `profile_root/project/project.json`; существующий project owner. |
| `poise-task-database` | `state_root/state.sqlite`; `Database`, схема этой поставки 13. |
| `poise-requirements-database` | `state_root/database/requirements.sqlite`; `RequirementsStore`, схема 2. |
| `poise-telemetry-database` | `state_root/telemetry/events.sqlite`; `TelemetryDatabase`, схема 13. |

Для всех 14 записей объявлены самостоятельные check/install/repair. Внешний
`check` только наблюдает; `run` для missing выбирает install, для invalid — repair
и затем вновь проверяет ресурс. Готовый ресурс идемпотентен. Каталоги создаются
и получают объявленный mode. Отсутствующие БД создают настоящие schema owners.
Существующие занятые/повреждённые/несовместимые БД диагностируются без пересоздания.
Недоступные Python/Git/repository требуют действий оператора по рекомендациям.

### Аргументы предметных адаптеров

Engine запускает модули `poise_environment.repairs.<module>` из объявленных argv.
Общие обязательные аргументы: `--requirement-id` — ID записи; `--action` —
check/install/repair; `--schema` — объявленная schema ответа; `--recommendations` —
файл рекомендаций. `-h/--help` показывает справку выбранного модуля.

| Модуль | Дополнительные обязательные аргументы |
|---|---|
| `python_runtime` | `--minimum`, `--maximum`: границы версии Python. |
| `git_executable` | `--git`: исполняемый Git. |
| `repository` | `--repository`, `--base-ref`, `--git`: выбранный репозиторий, ревизия и Git. |
| `directory` | `--path`, `--permissions`: каталог и восьмеричный mode. |
| `project_config` | `--config`, `--settings`, `--edits`, `--destination`, `--template`: manifest, setup, правки, назначение проекта от setup root и ID шаблона. |
| `task_database`, `requirements_database`, `telemetry_database` | `--config`, `--state-root`: выбранный manifest и ожидаемый отдельный state. |

Файл рекомендаций имеет schema `poise-maintenance-recommendations-1` и `entries`:
ID требования → cause → список строк. По объявленной cause выбирается этот список,
подстановка в строках принадлежит `string.Template` и использует именованные
аргументы выбранного адаптера (`config`, `path`, `git` и другие из таблицы).
Неизвестная причина имеет явно заданную запись `unknown`; диагностический stderr
и ненулевой результат не превращаются в успех.

## Обслуживание через Bootstrap

Подготовьте рабочие файлы внутри выбранного Poise:

```bash
cd "$poise_root"
cp config/bootstrap.example.json config/bootstrap.json
cp config/environment-maintenance.example.json config/environment-maintenance.json
```

Заполните `config/bootstrap.json`: `parameters.python` — фактический
`consumer_python`, `parameters.tool_config` — абсолютный путь рабочего
`config/environment-maintenance.json`. В рабочем values-файле задайте, например:

```json
{"python": "/srv/poise/environment-venv/bin/python"}
```

`${PYTHON_EXECUTABLE}` в sanitized примере заменяется оператором реальным значением
в рабочем файле. Values — объект значений параметров внешнего каталога; это не
полный каталог действий. `catalog.json` уже находится в `catalog_dir`.
Оба рабочих файла исключены из Git. Полный [контракт команды, плагина и
аргументов Bootstrap](bootstrap-catalog-v2.md#конфигурация-команды) остаётся его владельцем.

```bash
"$poise_python" -I -B "$poise_root/bootstrap" deps \
  --config "$poise_root/config/bootstrap.json" --catalog-dir "$catalog_dir"
"$poise_python" -I -B "$poise_root/bootstrap" infra \
  --config "$poise_root/config/bootstrap.json" --catalog-dir "$catalog_dir"
"$poise_python" -I -B "$poise_root/bootstrap" check \
  --config "$poise_root/config/bootstrap.json" --catalog-dir "$catalog_dir"
"$poise_python" -I -B "$poise_root/bootstrap" infra \
  --config "$poise_root/config/bootstrap.json" --catalog-dir "$catalog_dir" --dry-run
```

В этом примере deps/infra/check сначала вызывают внешний check всего переданного
каталога. Названия deps/infra не фильтруют 14 записей. Плагин для разрешённого
action_required вызывает внешний run, затем check. Dry-run сохраняет наблюдение
без изменения ресурсов. Этот порядок deps→infra→check проверен на реальных
ресурсах в изолированной установке. Готовность требует satisfied всех 14 записей.

## Повторная публикация и диагностика

Для повтора выполните ту же команду материализации с той же выделенной output
папкой. Supply assets заменяются. Поставляемый `preserve_paths` сохраняет bytes:

- `project/` — существующий manifest и рабочие workflow packs;
- `configured-projects.json` — реестр владельца project setup;
- `setup.lock`;
- `editor-state/`;
- `config/catalogue/editor.json`;
- `config/catalogue/settings.json`.

Обслуживаемые БД остаются в отдельном state и не заменяются публикацией профиля.
Существующий project config не перенастраивается повтором. Если новые выбранные
параметры с ним расходятся, последующая проверка возвращает configuration_drift;
согласованные изменения существующего проекта выполняет его владелец. После
работы владельца выполните явный check. Это не скрытая миграция настроек.

У check-адаптера schema `poise/maintenance-action/v2`, `requirement_id`, `action`,
`state` (satisfied/missing/invalid/unavailable). При unavailable присутствуют
`cause` и `recommendations`. Корректное наблюдение имеет exit=0, даже когда
готовность не достигнута. Для изменяющего действия подтверждённый успех —
`outcome=completed`, exit=0; невозможность — `outcome=unavailable`, cause,
recommendations и exit=2. Ошибка конфигурации самого адаптера диагностируется
в stderr с exit=2. Внешний engine сопоставляет полный ответ и реальный exit.

Занятость, повреждение, несовместимая версия/структура БД и drift возвращаются
агенту/оператору с рекомендациями. Сохраните полный JSON, stderr и exit.
Итоговый внешний ответ имеет schema `environment-maintenance/result/v2`;
Bootstrap-плагин фиксирует ready/error и [одну попытку исправления в текущем
вызове](bootstrap-catalog-v2.md#результаты-и-ошибки). Следующую проблему решает агент.

### Недоступные Python и каталоги

Для поставляемого примера выбирайте Python 3.13: нижняя граница включительна,
верхняя `3.14` исключительна. Пути семи ресурсов каталогов задаются выбранным
профилем; используйте корректные пути каталогов для этой установки.

| Ресурс и причина | Рекомендация оператора |
|---|---|
| `python-runtime`, `unavailable/version` | Выберите поддерживаемый интерпретатор в параметрах профиля. |
| Семь ресурсов каталогов, `unavailable/type` | Укажите каталог для выбранного ресурса вместо файла и повторите проверку. |

При этих состояниях прямой `check` возвращает наблюдение с exit=0, а прямые
`install` и `repair` возвращают `outcome=unavailable` с exit=2. Существующий файл
по выбранному пути каталога сохраняет свои байты. Внешний ответ со статусом
`unavailable` имеет exit=3; Bootstrap-плагин представляет его как `error` с exit=4.
Полный [контракт статусов и повторной проверки](bootstrap-catalog-v2.md#состояния-и-повторная-проверка)
принадлежит компоненту и обработчику Bootstrap.

## Границы проверенной поставки

Проверены первая/повторная публикация, сохранность значимых строк и bytes трёх БД,
реальные самостоятельные действия, отказы БД, Bootstrap и чтение опубликованных
ссылок настоящим FileCatalogue. Полнота чтения каталога не означает готовности
native Task runtime, editor DB или hooks. Действующий requirements/v1 CLI остаётся
отдельным маршрутом; переключение live-установки принадлежит её процедуре поставки.

Проверены также неподдерживаемая версия Python, сохранность файла при отказе
обслуживания каждого из семи ресурсов каталогов и сохранность рабочих настроек
каталога, редактора и реестра при повторной публикации профиля.

Текущая публикация использует подготовленный соседний временный каталог и откат
при обычной ошибке второго rename. Это не гарантия атомарного восстановления
при произвольном падении процесса или конкурентных записях. Описанные выше
условия выделенного профиля, отдельного state и остановленных writers обязательны.
