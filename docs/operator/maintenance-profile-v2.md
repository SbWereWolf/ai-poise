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

Выберите поставку Poise с её `src/` и `config/`, Python 3.15 для Poise и окружение
внешнего инструмента с поддержкой `catalog/v2`. Устанавливайте внешний инструмент
пакетным менеджером по процедуре его поставки. Каждый Python, выполняющий действия,
должен иметь доступ к модулям `poise_environment` выбранной поставки.
В рабочих values согласуйте выбранный интерпретатор действий и диапазон:
для собственного Python3.15 задайте minimum3.15 включительно и maximum3.16
исключительно через `python_minimum`/`python_maximum`.

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

Для текущей поставки Poise выбирайте Python3.15 и задайте в рабочих values
`python_minimum="3.15"`, `python_maximum="3.16"`: нижняя граница включительна,
верхняя исключительна. Это явно выбранный рабочий диапазон, а не автоматическая
правка исходного примера. Пути семи ресурсов каталогов задаются выбранным
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

## Профиль ERP

Это отдельный выбранный каталог: **56 ресурсов в 21 группе**, а не базовый
14-ресурсный профиль выше. Его владелец — поставляемая
[спецификация ERP](../../config/projects/erp/profile.example.json).
Каталог задаёт команды и порядок обслуживания; внешний engine исполняет действия,
предметные адаптеры наблюдают ресурсы, подключаемый обработчик анализирует результат.
Готовность окружения не заменяет приёмку Task, Sprint или эксплуатационный переход.

### Подготовка ERP-профиля

Используйте Linux, Python 3.15 выбранной поставки Poise и отдельный выбранный
Python внешнего инструмента с поддержкой `catalog/v2`. Пакетный менеджер выбирает
версии зависимостей. Python действий должен видеть модули выбранной поставки и
объявленную в её `pyproject.toml` зависимость `psutil`. Для source-запуска доступ
к этим модулям задаёт `module_path`; сам внешний consumer устанавливается отдельно.

В рабочих values согласуйте Python действий и его диапазон: для текущего собственного
runtime выбраны `python_minimum="3.15"` и `python_maximum="3.16"`.
Исходные примеры не заменяют заполнения рабочих значений; их старые значения
в таблицах ниже описывают сами примеры, не текущую рекомендацию для установки.
Оставшееся исправление несовместимых границ в примере общей установки учитывается
отдельно в [известных ограничениях](../operations/known-bugs.md#aip-operator-upgrade-compatibility-001--согласование-runtime-после-обновления).

После подготовки полного профиля подключите его к [подготовке новой Task-worktree](worktree-preparation.md#подготовка-рабочей-области).
Рабочий configuration, shared environment и test-execution поставляются явно;
готовность56ресурсов/21группы не подтверждает запуск полного бизнес-CJM.

Выберите существующий ERP-репозиторий и доступную локальную базовую ревизию.
Публикуйте в выделенную папку внутри выбранного Poise, с отдельным state вне
заменяемого профиля и остановленными владельцами записей. Значения `/srv/...`
в примере заменяются выбранными путями установки:

```bash
poise_root=/srv/poise/app
poise_python=/srv/poise/poise-venv/bin/python
consumer_python=/srv/poise/environment-venv/bin/python
catalog_dir="$poise_root/operator/profile"
state_root=/srv/poise/data/erp-maintenance
repository=/srv/projects/erp
cd "$poise_root"
mkdir -p "$poise_root/operator"
PYTHONPATH="$poise_root/src" "$poise_python" -B -m poise_environment.profile \
  --source "$poise_root" --profile-spec config/projects/erp/profile.example.json \
  --output "$catalog_dir" --state "$state_root" --repository "$repository" \
  --project erp --base-ref main --author-name Operator \
  --author-email operator@example.invalid --module-path "$poise_root/src"
```

Все [аргументы материализатора](#аргументы-материализатора) сохраняют своё
назначение. Команда публикует профиль, но ещё не обслуживает ERP.
В `config/projects/erp/` отслеживаются девять заполненных примеров: `profile`,
`catalog`, `resources`, `bootstrap`, `values`, `recommendations`, `local-settings`,
`host-runtime-settings`, `cjm-settings` с окончанием `.example.json`.
Из них и объявленных assets появляются рабочие `catalog.json`, `resources.json`,
`bootstrap.json`, `values.json`, `recommendations.json`, `documents/*.json`,
`runtime/` и настройки project setup. `operator/profile/` исключён из Git.

После публикации заполните рабочие файлы:

1. В `values.json` задайте выбранные инструменты и параметры ERP из таблицы ниже;
   `python` — Python действий Poise, `consumer_python` — Python внешнего engine.
2. В `bootstrap.json` задайте `parameters.python` равным фактическому
   `consumer_python`, а `parameters.tool_config` — абсолютному пути `values.json`.
   Это отдельная настройка команды: изменение одного values-файла не меняет argv
   уже опубликованного Bootstrap.
3. В `documents/local-settings.json`, `documents/host-runtime-settings.json` и
   `documents/cjm-settings.json` заполните параметры своей установки. Это источники
   публикуемых JSON; их рабочие назначения определены значениями `*_path`.

Для повторной публикации выполните ту же команду с той же папкой. Поставляемые
assets и документы заменяются; сохраняются только объявленные `preserve_paths`
из [спецификации](../../config/projects/erp/profile.example.json), включая
project/registry/editor настройки. Обслуживаемые данные остаются в отдельном state.
После повтора вновь согласуйте рабочие values, Bootstrap и источники JSON со своей
установкой. Публикация не перенастраивает существующий проект скрытно.

### Параметры и корни ERP

В каталоге 27 строковых параметров. Значения, выбранные аргументами материализации,
попадают в каталог и рабочие документы; остальные исходно берутся из выбранного
примера. Рабочие переопределения задаются values-файлом или внешними `--set`.

| Параметр | Источник и назначение |
|---|---|
| `python` | `sys.executable` материализатора; Python предметных действий. |
| `module_path` | `--module-path`; доступ действий к выбранным source-модулям. |
| `profile_root` | Абсолютный `--output`; корень опубликованных assets. |
| `state_root` | Абсолютный `--state`; отдельные обслуживаемые данные и caches. |
| `repository` | Абсолютный `--repository`; выбранный ERP checkout. |
| `project_id` | `--project`; идентификатор публикуемой конфигурации проекта. |
| `base_ref` | `--base-ref`; доступная выбранная ревизия. |
| `author_name` | `--author-name`; имя автора конфигурации проекта. |
| `author_email` | `--author-email`; почта автора. |
| `git` | В примере `git`; выбранная команда Git. |
| `directory_mode` | В примере `0750`; права создаваемых каталогов. |
| `python_minimum` | В примере `3.13`; включительная нижняя граница Python. |
| `python_maximum` | В примере `3.14`; исключительная верхняя граница Python. |
| `template_id` | `--template`, исходно `linux-reference`. |
| `remote` | В примере `disabled`; значение конфигурации проекта. |
| `php_executable` | В примере `php`; выбранный PHP для проверок и Composer. |
| `node_executable` | В примере `node`; выбранный Node для проверок и npm. |
| `composer_executable` | В примере `composer`; выбранный Composer. |
| `npm_entry_point` | В примере `/usr/share/nodejs/npm/bin/npm-cli.js`; фактический npm CLI для выбранного Node. |
| `consumer_python` | В примере `python3`; выбранный Python внешнего engine, используемый при публикации команды Bootstrap. |
| `environment_file` | В примере `platform-infrastructure/config/dev/local-development.env`; рабочий файл окружения. |
| `local_settings_path` | В примере `platform-infrastructure/config/dev/local-development.settings.json`; локальная конфигурация. |
| `host_runtime_settings_path` | В примере `platform-infrastructure/config/dev/local-dev-host-runtime.settings.json`; конфигурация host runtime. |
| `cjm_settings_path` | В примере `platform-infrastructure/tests/playwright/cjm.local-development.settings.json`; конфигурация пользовательского маршрута проверки. |
| `local_settings_source` | В примере `documents/local-settings.json`; источник локальной конфигурации. |
| `host_runtime_settings_source` | В примере `documents/host-runtime-settings.json`; источник host runtime. |
| `cjm_settings_source` | В примере `documents/cjm-settings.json`; источник конфигурации пользовательского маршрута. |

Авторские шаблоны команд и проекций рендерятся стандартным `string.Template`.
Выбранные значения — данные: строка с буквальным `$` сохраняется буквально,
без повторной подстановки её содержимого. Для буквального доллара в самом
авторском шаблоне применяется стандартное `$$`.

Обязательный `path_roots` в `resources.json` явно задаёт разрешение путей:

| Параметры | Корень относительного значения |
|---|---|
| `environment_file`, `local_settings_path`, `host_runtime_settings_path`, `cjm_settings_path` | `repository` |
| `local_settings_source`, `host_runtime_settings_source`, `cjm_settings_source` | `profile_root` |

Абсолютное переопределение остаётся абсолютным. Пути принадлежат конфигурации,
а не угадываются из cwd предметного адаптера. У ERP-обработчика в `bootstrap.json`
обязателен `handler.options.configuration_root`: материализатор задаёт `repository`.
Его `configuration_targets` содержит `id`, `kind`, `path` трёх наблюдаемых JSON.
Относительный target разрешается от этого корня, абсолютный используется как задан.
При изменении рабочей цели согласуйте values и соответствующий target обработчика.
При выборе другого `repository` согласуйте также `configuration_root`.

### Ресурсы и обслуживание ERP

Каждая из 56 записей имеет самостоятельные `check`, `install`, `repair` с
объявленными argv/cwd/env, схемой ответа и пределами исполнения/вывода. Конкретные
команды и dependencies заданы [каталогом](../../config/projects/erp/catalog.example.json),
предметные параметры — [ресурсами](../../config/projects/erp/resources.example.json).

| Группа | Что наблюдается или обслуживается |
|---|---|
| `git` | Git-репозиторий и выбранная базовая ревизия. |
| `service_tools` | Команды обслуживания, включая jq и остальные объявленные инструменты. |
| `php` | Выбранный исполняемый PHP. |
| `php_extensions` | Объявленные PHP-расширения. |
| `node` | Выбранный Node и сведения его runtime/ABI. |
| `managers` | Доступность выбранных Composer и npm. |
| `local_settings` | Локальная и host-runtime JSON-конфигурации. |
| `cjm_settings` | JSON-настройки выбранного пользовательского маршрута проверки. |
| `environment` | Файл окружения, его разбор и проверка действующим владельцем ERP. |
| `installation_units` | Канонические единицы измерения приложения. |
| `directories` | Рабочие каталоги приложений и browser-home. |
| `application_databases` | Наличие и читаемость баз приложений. |
| `queues` | Схемы объявленных очередей. |
| `composer_dependencies` | Lock/install/autoload трёх PHP-приложений. |
| `mirrored_packages` | Содержимое зеркальных пакетов. |
| `node_dependencies` | Установленные зависимости document-ui и проекта Playwright. |
| `playwright` | Объявленные Playwright CLI и конфигурационный файл. |
| `artifacts` | CLI, verifier и XSD приложения. |
| `discovery` | Кэши обнаружения пакетов приложений. |
| `initialization` | Миграции приложений и начальные настройки. |
| `readiness` | Итоговое наблюдение готовности баз и настроек. |

Объявленные связи ставят каталоги перед установкой зависимостей, Composer перед
discovery и единицами измерения, базы перед миграциями, миграции перед начальными
настройками, а итоговую проверку — после необходимых владельцев. Успешное наблюдение
одной записи не заменяет результат остальных.

Для Node недостаточно наличия lock-файлов. После `required_files` выполняются
обязательные `check_argvs`: реальный `npm ls --json --depth=0` и импорт нужного entry
с проверкой функции (`vite.createServer` или `chromium.launch`). Проверка функции
не запускает сервер или браузер. Выбранный Node также ставится первым в PATH действия.
При неготовности изменяющее действие выполняет один `install_argv` и прямо проверяет
результат ещё раз. Уже готовый ресурс повторной установки не требует.

Из выбранного Poise запустите последовательное обслуживание и явную проверку:

```bash
"$poise_python" -I -B "$poise_root/bootstrap" infra \
  --config "$catalog_dir/bootstrap.json" --catalog-dir "$catalog_dir"
"$poise_python" -I -B "$poise_root/bootstrap" deps \
  --config "$catalog_dir/bootstrap.json" --catalog-dir "$catalog_dir"
"$poise_python" -I -B "$poise_root/bootstrap" check \
  --config "$catalog_dir/bootstrap.json" --catalog-dir "$catalog_dir"
"$poise_python" -I -B "$poise_root/bootstrap" infra \
  --config "$catalog_dir/bootstrap.json" --catalog-dir "$catalog_dir" --dry-run
```

В ERP-примере первоначальные команды infra/deps вызывают внешний `run`, check —
внешний `check`. Операции не фильтруют группы каталога по своему имени. Это отличается
от исходного check-first базового профиля. Dry-run передаётся engine; плагин не
предпринимает исправление при dry-run. Все [аргументы Bootstrap и контракт сменного
обработчика](bootstrap-catalog-v2.md#интерфейс-подключаемого-обработчика) остаются общими.
Распознанное разрешённое исправление плагина ограничено одной попыткой с проверкой
в текущем вызове; неизвестная или последующая ошибка возвращается агенту.

### Конфигурация и результаты ERP

Локальные JSON, host-runtime JSON и выбранные CJM JSON публикуются владельцем
по одному файлу: валидное целое содержимое подготавливается рядом с целью и
заменяет её через `os.replace`. Для первой и повторной публикации при обычном
прерывании остаётся исходное отсутствие/прежний файл либо целое новое содержимое.
Это per-file атомарность, а не транзакция нескольких файлов и не гарантия при
произвольном падении системы или конкурентных изменениях.

ERP-плагин дополняет результат массивом `configuration_observations`:

| Поле | Наблюдаемый факт |
|---|---|
| `id`, `kind` | Идентификатор и вид из выбранного target. |
| `state` | `present`, `absent` или `unavailable` по фактическому чтению цели. |
| `sha256` | SHA-256 точных прочитанных bytes при `present`, иначе `null`. |
| `cause` | Пустая строка при чтении, `not-found`, `permission-denied` либо причина файловой ошибки. |

Эти факты не заменяют полный исходный ответ engine и реальный exit. Ошибка команды
остаётся ошибкой, даже если JSON уже опубликован. При отсутствии/недоступности
обязательного JSON наблюдаемая ранее готовность также возвращается как ошибка.
Сохраните полный результат, stdout/stderr, exit, причину и идентичности конфигураций;
последующее решение принимает агент/оператор.

Application `.env` остаётся самостоятельной записью каталога. Его действующий
генератор ERP пишет по существующей процедуре; атомарность этого генератора при
прерывании **не обеспечена данной поставкой**. Доказательства атомарной публикации
трёх JSON к `.env` не относятся. После исправления его владельцем нужна отдельная
проверка соответствующего результата.

### Процессы и диагностика ERP

Предметное действие выполняется отдельным последовательным Linux-процессом,
у которого перед запуском команды нет иных дочерних процессов. Python действий
содержит `psutil`; Linux предоставляет `prctl` child-subreaper. До spawn включается
временное владение осиротевшими потомками. После ожидания корневой команды
принадлежащие действию потомки завершаются и ожидаются, исходный флаг восстанавливается.
Команды сохраняют унаследованную process group. Это не универсальный менеджер процессов.

`bound_seconds` ограничивает ожидание команды и отдельный срок завершения её
дочерних процессов; внешний предел действия задан `execution_bound_seconds`.
Полные внутренние stdout/stderr захватываются в файловые потоки и читаются независимо.
При ошибке чтения одного сохраняются доступные bytes другого; capture/spawn/timeout/
settlement ошибки остаются ошибками с доступной диагностикой. Полный результат нужен
для анализа; preview не заменяет его. Неизвестная ошибка возвращается агенту, а
восстановление установки выполняет её владелец по рекомендациям.
