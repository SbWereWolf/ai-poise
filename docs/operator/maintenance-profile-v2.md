# Рабочие профили обслуживания Poise

## Обычные команды start-app

[Настройка постоянного входа](#постоянный-вход-start-app) выполняется один раз оператором.
Из каталога обслуживаемого приложения запускайте установленный пакет:

```bash
start-app infra
start-app deps
start-app adjust
start-app check
start-app run
```

`infra` готовит инфраструктуру, включая библиотеки инструмента миграций перед созданием БД;
`deps` устанавливает зависимости приложения; `adjust` применяет подготовленный пакет
согласованной настройки; `check` проверяет ресурсы; `run` выполняет только запуск.
Каждая операция исполняет собственную конфигурационную цепочку.

### Постоянный вход start-app

1. Установите выбранную пакетную поставку `environment-maintenance` с её собственным
   runtime и поставляемым обработчиком `interpreters:exit_result`; предоставьте её
   `start-app` через PATH. Python приложения выбирается отдельно.
2. Подготовьте профиль существующим владельцем публикации из
   [profile.example.json](../../config/maintenance/start-app/profile.example.json).
   Он публикует [entry.example.json](../../config/maintenance/start-app/entry.example.json)
   как `entry.json`, файлы операций и `parameters.json`. Существующая
   [цепочка подготовки](../../config/worktree-preparation/poise-op-supervisor-infra.example.json)
   связывает параметры и копирует этот вход в корень приложения как `start-app.json`.
3. Заполните отдельный рабочий файл, указанный параметром `adjust_request`, по
   [контракту владельца конфигурации](../configuration/project-setup.md#обновление-действующего-проекта):
   выбранный config_path, точная известная revision из результата владельца,
   желаемые manifest_edits, отдельный request_id и receipt_path. Для нового намерения
   настройте новый пакет; точный повтор использует сохранённую квитанцию.
4. Храните рабочие main, параметры, операции и пакет adjust в постоянной области
   экземпляра, исключённой из Git. `/start-app.json` исключён отслеживаемым правилом
   проекта; штатный путь запуска использует установленный SDK и эти JSON.

`start-app.json` задаёт абсолютные пути к операциям, `invocation.parameters`,
`invocation.base_dir` и выбор requirements для каждой изменяющей операции.
Явные `--config`, `--parameters`, `--base-dir` и `--requirements` переопределяют
только текущий вызов. Из другого каталога выберите абсолютный `--config`.
SDK загружает конфиг по текущему каталогу; значения операций рендерит стандартный
`string.Template`. В конфигурационных Bash-аргументах буквальный `$` записывается `$$`.

Новые параметры профиля: `bash` и `jq` выбирают стандартные инструменты,
`resource_result_filter` проверяет schema/state/outcome, `project_list_result_filter`
проверяет массивы projects/errors и минимальное число проектов, `adjust_request`
выбирает рабочий пакет изменения. Bash pipefail сохраняет ошибку внешней команды;
jq сохраняет исходный stdout перед проверкой. Полные stdout/stderr входят в результат.
SDK-процесс использует собственный runtime; PYTHONPATH приложения задаётся только
его внешним командам. Рабочие экземпляры сохраняют собственные пути и параметры.

Wheelhouse обслуживаемого Poise содержит зависимости его инструментов из pyproject:
`yoyo-migrations==9.0.0`, `sqlparse==0.6.0` с разрешёнными пакетным менеджером
зависимостями и `psutil`. Подготовка wheelhouse выполняется настроенной цепочкой
поставки; установка выполняется в dependency_target обслуживаемого экземпляра.
Схема БД применяется из DDL приложения, данные — из выбранных DML проекта.
Проверка телеметрии использует канонического владельца чтения поддерживаемых форматов.

Следующий раздел описывает также сохранённый явный Bootstrap API. Его `app.json` и
`run-compat` сохраняют прежний контракт, а обычный пятикомандный вход использует
`entry.json` и собственный `common-run.json`.

## Назначение и условия

Собственный экземпляр обслуживается через самостоятельный `environment-maintenance`:
`infra`, `deps`, `adjust`, `check`, `run` используют конфиги одинакового формата.
`poise_environment.profile` только публикует рабочие файлы из выбранных примеров.
Команду внешнего приложения и обработчик результата задаёт
[Bootstrap](bootstrap-catalog-v2.md#конфигурация-команды).

Выберите существующий Git-репозиторий обслуживаемого Poise с доступной базовой
ревизией, его Python 3.15, совместимые исходники с материализатором/шаблонами и
подготовленную установку SDK. Источник шаблонов и обслуживаемый репозиторий могут
быть разными копиями. Исполняемые модули действий берутся из обслуживаемой копии.
Данные разместите отдельно от заменяемой папки профиля и Task-worktree.
Родитель профиля должен существовать; при публикации закройте других его писателей.

Рабочий профиль в примере — `operator/profile/` внутри выбранного репозитория:
он уже исключён из Git. Для своего расположения настройте исключение до записи.
Исходные заполненные `.example.json` отслеживаются. Пути `/srv/...` ниже —
явно выбранные значения примера, а не зашитые расположения установки.

## Развёртывание из примера

Подготовьте исходную и обслуживаемую копии, доступный Python3.15, установленный
SDK и локальный wheelhouse с зависимостями обслуживаемого Poise. Шаблон `deps`
использует обычный pip с явными `--no-index --find-links --target`; пакетный
менеджер разрешает пакет `psutil`, а приложение не сравнивает внешние версии.

```bash
poise_source=/srv/poise/source
repository=/srv/poise/instance
python_runtime=/srv/runtime/python3.15
tool_prefix=/srv/environment-maintenance/2.0.0
tool_python="$tool_prefix/lib/node_modules/environment-maintenance/.venv/bin/python"
wheelhouse=/srv/packages/poise-wheels
state_root=/srv/poise/data/own-instance
profile="$repository/operator/profile"
project_id=own-instance
base_ref=master
author_name=Operator
author_email=operator@example.invalid

poise_python="$python_runtime"
mkdir -p "$repository/operator"
PYTHONPATH="$poise_source/src:$poise_source" "$poise_python" -P -s -B -m poise_environment.profile \
  --source "$poise_source" \
  --profile-spec "$poise_source/config/maintenance/start-app/profile.example.json" \
  --output "$profile" --state "$state_root" --repository "$repository" \
  --project "$project_id" --base-ref "$base_ref" \
  --author-name "$author_name" --author-email "$author_email" \
  --module-path "$poise_source/src"

"$poise_python" -I -B - "$profile" "$tool_python" "$wheelhouse" <<'PYCONFIG'
import json
from pathlib import Path
import sys
profile, tool_python, wheelhouse = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
command = json.loads((profile / 'bootstrap.json').read_text())
command['parameters']['python'] = tool_python
(profile / 'bootstrap.json').write_text(json.dumps(command, ensure_ascii=False, indent=2) + '\n')
parameters = json.loads((profile / 'parameters.json').read_text())
parameters['wheelhouse'] = wheelhouse
(profile / 'parameters.json').write_text(json.dumps(parameters, ensure_ascii=False, indent=2) + '\n')
PYCONFIG

"$poise_python" -I -B "$poise_source/bootstrap" infra --config "$profile/bootstrap.json" --base-dir "$repository"
"$poise_python" -I -B "$poise_source/bootstrap" deps --config "$profile/bootstrap.json" --base-dir "$repository"
"$poise_python" -I -B "$poise_source/bootstrap" check --config "$profile/bootstrap.json" --base-dir "$repository"
"$poise_python" -I -B "$poise_source/bootstrap" run --config "$profile/bootstrap.json" --base-dir "$repository"
```

При публикации stdout содержит `status=created` либо `replaced`, абсолютный
`profile`, число ресурсов `requirements` и `state_created=false`, exit0.
Публикация не создаёт обслуживаемые state и БД. `infra` создаёт настроенный проект,
каталоги и три БД; `deps` устанавливает зависимость в
`repository/.runtime/dependencies`; `check` подтверждает ресурсы.
`run` выполняет только заданную цепочку — здесь публичный `poise project list`.
Подготовленный экземпляр возвращает выбранный проект и его manifest без ошибок;
вложенные полные потоки и exit находятся в результате SDK внутри ответа Bootstrap.
Успех Bootstrap — `status=ready`, `exit_code=0`.

Task-деревья размещаются в `repository/.runtime/worktrees`: именно этот абсолютный
путь записан в project manifest, parameters и командах `infra`/`check`. Выбранный
state должен быть вне этого каталога и отдельно от профиля. Изменение cwd запуска
не перепривязывает ранее сохранённый repository.

Установка самого SDK выполняется выбранным пакетным менеджером в постоянный prefix.
Для обновления используйте настроенную SDK-цепочку [общей поставки](#согласованное-обновление-потребителей):
она вызывает npm с выбранным архивом и обязательную проверку всех потребителей.
Исходное наличие работоспособного исполнителя SDK и Python действий — условия
запуска этой цепочки. Это отдельная локальная поставка, не обещание публикации
артефакта в npm registry. Подготовка ресурсных зависимостей обслуживаемого приложения
выполняется его конфигами `infra`/`deps`.

### Аргументы материализатора

| Аргумент | Назначение |
|---|---|
| `--source PATH` | Обязательный источник materializer, примеров, templates/assets. |
| `--profile-spec PATH` | Явная спецификация. Для собственного operation-профиля — `config/maintenance/start-app/profile.example.json`; без опции выбирается отдельный прежний `config/maintenance/profile.example.json`. |
| `--output PATH` | Обязательная выделенная папка публикуемых рабочих файлов. |
| `--state PATH` | Обязательный отдельный корень данных обслуживаемого экземпляра. |
| `--repository PATH` | Обязательный корень выбранного обслуживаемого Git checkout. |
| `--project ID` | Обязательная идентичность публикуемой конфигурации проекта. |
| `--base-ref REF` | Обязательная доступная локальная базовая ревизия выбранного checkout. |
| `--author-name NAME` | Обязательное имя автора проекта. |
| `--author-email EMAIL` | Обязательная почта автора проекта. |
| `--template ID` | ID из спецификации; исходное значение CLI — `common`. |
| `--module-path PATH` | Публикуемое значение `module_path`; исходное значение — пустая строка. Новый operation-профиль отдельно задаёт исполняемый `runtime_module_path`. |
| `-h`, `--help` | Стандартная справка без публикации. |

Source/output/state/repository становятся абсолютными от cwd. Относительный
`--profile-spec` относится к cwd; пути входов спецификации — к `--source`.
Публикация выбирает объявленную schema: новый `poise-start-app-profile-1` или
сохраняемый отдельный `poise-maintenance-profile-1`; неизвестная schema — ошибка.
Публичный Bootstrap допускает `-I`, исходный `-m poise_environment.profile` выше
получает импорт через явный `PYTHONPATH` и `-P -s -B`.

## Поставляемая спецификация

Канонические заполненные входы — [спецификация
публикации](../../config/maintenance/start-app/profile.example.json),
[app](../../config/maintenance/start-app/app.example.json),
[infra](../../config/maintenance/start-app/infra.example.json),
[deps](../../config/maintenance/start-app/deps.example.json),
[check](../../config/maintenance/start-app/check.example.json),
[run](../../config/maintenance/start-app/run.example.json),
[Bootstrap](../../config/maintenance/start-app/bootstrap.example.json).

| Поле спецификации | Назначение |
|---|---|
| `schema` | `poise-start-app-profile-1`, выбор этого контракта публикации. |
| `settings`, `recommendations` | Пути примера project-setup и рекомендаций от source. |
| `templates` | ID → путь исходного blueprint. |
| `parameters` | Явные исходные значения параметров. |
| `derived_parameters` | Упорядоченные стандартные шаблоны производных значений после привязки CLI. |
| `operation_documents` | Список `source`/`destination`: полные JSON-документы операций копируются с неизвестными полями. |
| `bootstrap` | Исходный шаблон JSON команды Bootstrap. |
| `resource_inventory` | Список документов ресурсов для подсчёта `requirements` публикации. |
| `outputs` | Имена `settings`, `registry`, `edits`, `recommendations`, `bootstrap`, `parameters` в профиле. |
| `asset_directories` | Объекты `source`, `destination`, `patterns` для поставляемых assets. |
| `documents` | JSON-проекции `source`, `destination`, `edits` с массивом `path` и `value`. |
| `workflow_source_prefix` | Явный префикс исходных workflow references, снимаемый при проекции. |
| `project_edits` | Правки blueprint проекта через существующего владельца. |
| `settings_overrides` | Явные root/registry/lock настройки project-setup. |
| `preserve_paths` | Рабочие пути, переносимые из существующего профиля при повторе. |

Назначения задаются корректными относительными путями внутри профиля; объявленные
источники и поля документов должны существовать. Подстановка публикации использует
стандартный `string.Template.substitute`. В исходном Bootstrap-шаблоне `$$` сохраняет
`$` для более поздней подстановки команды. Документы действий копируются целиком:
их placeholders исполняет SDK при запросе операции. Классы/методы исполнения
не зашивают пути этой установки.

Проект получает `setup.json`, `configured-projects.json`, `project-edits.json`,
`recommendations.json`, необходимые catalogue/workflow assets и рабочие конфиги
приложения. Сам `selected-project/project.json` создаёт отдельная команда `infra`
через `ProjectCommands/FileProjectSetup`; [их контракт](../configuration/project-setup.md)
сохраняется. Данные и схемы БД остаются у своих владельцев.

### Каталог и параметры

Новый app содержит `operations` (имя операции → путь файла от app) и
`interpreters` (имя → `module`/`attribute`). Каждый файл операции имеет
`requirements`: список объектов с `id` и цепочкой `commands`.
Команда содержит `argv`, `cwd`, `env` и `interpreter` с `name`/`options`.
Для ресурсов `infra`/`deps` поле `checks.presence` задаёт обязательную проверку
наличия; `checks.availability` — необязательную проверку доступности.
Каждая проверка использует такую же цепочку `commands`. Дополнительные проверки
согласованности задаются требованиями собственного `check.json`; в заполненном
собственном примере их список пуст. `run.json` задаёт обычную цепочку запуска.
Схему, CRUD и исполнение этих документов предоставляет внешний SDK.

| Параметр рабочего parameters.json | Привязка/назначение |
|---|---|
| `python` | `sys.executable` материализатора: Python обслуживаемого Poise. |
| `module_path` | Сохранённый `--module-path`; отдельные прежние профили используют его для действий. |
| `profile_root` | Абсолютный `--output`. |
| `state_root` | Абсолютный `--state`. |
| `repository` | Абсолютный `--repository`, исполняемая копия Poise. |
| `project_id` | `--project`. |
| `base_ref` | `--base-ref`. |
| `author_name`, `author_email` | Соответствующие аргументы CLI. |
| `template_id` | Выбранный `--template`. |
| `git` | Исполняемый Git, в примере `git`. |
| `directory_mode` | Режим каталогов, в примере `0750`. |
| `python_minimum`, `python_maximum` | Диапазон собственного Python Poise: `3.15` включительно, `3.16` исключительно. |
| `remote` | Явное значение примера `disabled`. |
| `env_executable` | Настроенный исполнитель установки environment для команды SDK; здесь `env`. |
| `wheelhouse` | Явный локальный источник pip-зависимостей; заполняется оператором. |
| `dependency_target` | Производное `${repository}/.runtime/dependencies`. |
| `path_separator` | Разделитель путей платформы материализатора. |
| `runtime_module_path` | Производное repository/src, dependency_target, repository с этим разделителем. |
| `worktree_root` | Производное `${repository}/.runtime/worktrees`, общее для проекта и действий. |
| `task_database_lock_executable` | Исполнитель `flock`, например `/usr/bin/flock`. |
| `task_database_lock_wait_seconds` | Время ожидания блокировки; выбранное `2.0` означает секунды ожидания lock, а не всей SDK-операции. |
| `task_database_lock_relative` | Путь Task lock относительно state; у нового профиля `state.lock`, у действующей установки задаётся её настоящий `tasks/tasks.lock`. |
| `task_database_lock` | Производное `${state_root}/${task_database_lock_relative}`. |

В `bootstrap.json` отдельно опубликованы строковые `python`, `env_executable`,
`runtime_module_path`, `tool_config`, `parameters_file`: команда внешнего SDK,
его импортный контекст, app и параметры. Контекст `base_dir` поступает из
обязательного аргумента Bootstrap. Поля команд и плагина описаны в
[контракте Bootstrap](bootstrap-catalog-v2.md#конфигурация-команды).

## Ресурсы и действия

| Ресурс | Проверка и владелец эффекта |
|---|---|
| `python-runtime` | Исполняемый Python и его собственный диапазон; существующий адаптер Python. |
| `git-executable` | Реальный Git; существующий адаптер Git. |
| `target-repository` | Выбранный корень и доступная base ref; адаптер репозитория. |
| `state-root` | Отдельный state_root; адаптер каталога. |
| `runtime-directory` | state_root/runtime. |
| `task-directory` | state_root/standalone. |
| `sprint-directory` | state_root/sprints. |
| `worktree-directory` | repository/.runtime/worktrees вне state, согласованный с manifest. |
| `requirements-directory` | state_root/database. |
| `telemetry-directory` | state_root/telemetry. |
| `poise-project-config` | selected-project/project.json через владельца публикации проекта. |
| `poise-task-database` | База Task, существующий Database. |
| `poise-requirements-database` | База Requirements, существующий RequirementsStore. |
| `poise-telemetry-database` | База accounting, существующий TelemetryDatabase. |
| `poise-python-dependencies` | Обычный pip install psutil в явный target, реальный import/Process для presence и availability. |

`infra` и `deps` выполняют явно объявленные команды установки/настройки по порядку.
`check` использует проверки обоих файлов и собственные дополнительные цепочки.
Если availability отсутствует, SDK сообщает `not_configured` и сохраняет результат
presence. Отрицательный предметный state при exit0 интерпретируется как неготовность,
а не как успех по одному exit. Конкретные проверки доступности и согласованности
пишет обслуживаемое приложение своими инструментами и подключает через ту же схему.

Точные обязательные ключи, типы, значения примеров и диагностика трёх поставляемых
интерпретаторов приведены в [справочнике опций
SDK-интерпретаторов](bootstrap-catalog-v2.md#опции-sdk-интерпретаторов).

### Аргументы предметных адаптеров

Все существующие адаптеры принимают обязательные `--requirement-id`,
`--action check|install|repair`, `--schema`, `--recommendations`.
Дополнительные параметры передаются явно:

| Адаптер | Обязательные параметры |
|---|---|
| `python_runtime` | `--minimum`, `--maximum`. |
| `git_executable` | `--git`. |
| `repository` | `--repository`, `--base-ref`, `--git`. |
| `directory` | `--path`, `--permissions` (восьмеричный режим). |
| `project_config` | `--config`, `--settings`, `--edits`, `--destination`, `--template`. |
| `task_database`, `requirements_database`, `telemetry_database` | `--config`, `--state-root`. |

`check` адаптера возвращает schema/requirement_id/action/state; фактический exit0
включает отрицательные состояния. Успешные install/repair возвращают
`outcome=completed`, exit0, отказ — outcome/cause/recommendations и exit2.
SDK-интерпретатор оценивает и тело, и exit. Существующие БД с несовместимой схемой,
занятостью или повреждением не пересоздаются этими действиями.

## Обслуживание через Bootstrap

Полная последовательность приведена [выше](#развёртывание-из-примера).
Каждую её команду можно запросить отдельно с тем же JSON и `--base-dir`.
Повторные `infra`, `deps`, `check` поддерживают подготовленный экземпляр.
`run` не дополняет цепочку подготовкой: он исполняет ровно объявленный запуск.
Интерпретатор собственного запуска требует непустой корректный `projects` и пустой
`errors`, поэтому неподготовленный выбранный экземпляр получает диагностируемый
отказ даже при успешном exit самой команды чтения реестра.

[Все аргументы и обработчики](bootstrap-catalog-v2.md#подготовка-и-безопасная-проверка)
принадлежат Bootstrap. ERP и подготовка Task-worktree используют свои operation-конфиги SDK2.
Исторический catalog/v2 имеет отдельный совместимый оператор и собственные входы. Выбирайте их собственные
профили, не меняя входы другого оператора. Для автоматической Task-подготовки
используйте [её инструкцию](worktree-preparation.md#подготовка-рабочей-области).

## Повторная публикация и диагностика

Повторите материализацию в той же выделенной папке при остановленных писателях.
Сохраняются объявленные `preserve_paths`: выбранный проект, реестр, настройки
каталога/редактора и их рабочие данные. JSON команд и параметров создаются заново
из примеров и аргументов: после повторной публикации заполните свои `tool_python`
и `wheelhouse` тем же шагом инструкции. Изменение параметров не переписывает
молча уже существующий managed project: рассогласование сообщается как ошибка,
корректируется через его владельца.

Подготовка входов выполняется в соседней временной папке до замены профиля.
При обычной ошибке второй rename восстанавливается прежняя папка. Это не контракт
конкурентного редактирования или восстановления после любого падения процесса.
Нужные входы, parent directory и отсутствие других писателей — условия использования.
Перехваченная ошибка чтения/JSON/подстановки/подготовки возвращает
`status=invalid`, `error`, exit2. Справка и синтаксические отказы — argparse.

При обслуживании читайте полный ответ Bootstrap и вложенные потоки SDK.
Распознанный и явно разрешённый ремонт выполняется один раз с проверкой;
неизвестная или последующая ошибка передаётся агенту. Исправьте соответствующий
рабочий конфиг или причину ресурса и выполните нужную отдельную проверку.
[Коды и полный результат](bootstrap-catalog-v2.md#результаты-и-ошибки) не заменяются preview.

### Недоступные Python и каталоги

Выберите Python по собственному диапазону Poise и корректные пути каталогов
этого экземпляра. `unavailable/version`, `unavailable/type` и другие предметные
причины сохраняются с рекомендациями. SDK отличает успешное исполнение диагностической
команды от готовности ресурса. Существующий файл по пути требуемого каталога
не заменяется каталогом. После устранения причины повторите самостоятельную `check`.

## Границы проверенной поставки

Подтверждены две независимые пары source/repository с пробелами/Unicode, настоящая
подготовка трёх БД и независимое SQL-чтение их необходимой схемы/состояния,
установка и работа зависимости из выбранного target, проверки ресурсов и
реальный выбранный запуск. Task-worktree manifest/parameter/физический каталог
согласованы с repository/.runtime/worktrees вне state. Пустой raw `--base-dir`
отклоняется до дочерней команды; явный `.` и перенесённые пути поддерживаются.

Проверены отсутствие подготовки при run, отсутствие создания state при check,
объявленные availability/coherence, полный большой и нетекстовый вывод и одна
разрешённая попытка исправления. Ссылки и синтаксис инструкции — отдельные проверки.
Готовность экземпляра по этим ресурсам не означает принятую Task/Sprint, настроенные
native hooks, редактор или проверенный бизнес-маршрут ERP. Поставка нового профиля
не переключает уже работающий launcher. Готовность других ОС отдельно не заявляется.

## Выбор экземпляра и действия

[Инструкция настройки рабочих файлов и запуска](start-app-worktree-setup.md#подключение-установленного-инструмента)
применяется к Poise и ERP. Выберите repository, постоянные SDK/profile/state,
явный environment и ресурсы конкретной среды. `infra` подготавливает инфраструктуру,
`deps` — зависимости, `adjust` — объявленное согласование, `check` — presence,
availability и дополнительные проверки, `run` — только запуск. Рабочее дерево,
разработка, тестирование и production используют этот же механизм с собственными
конфигами и условиями; конкретные production credentials/topology заполняет владелец.
Проверенные локальные сценарии не являются испытанием каждой production-площадки.

## Профиль ERP

Текущий маршрут2.0 публикует конфиги из **самого целевого ERP checkout** по
[спецификации проекции](../../config/worktree-preparation/erp-profile.example.json).
Предметные инструменты и цепочки принадлежат ERP; Poise выбирает поставку SDK и
действительные координаты. Прежний catalog/v2 с56ресурсами — исторический контракт
отдельной поставки, не текущий2.0 профиль. Его прежние тексты доступны в Git.

### Подготовка ERP-профиля

Заполните [values и supervisor](start-app-worktree-setup.md#заполнение-values),
выбрав repository и отдельный profile/state. `worktree_profile.py publish` принимает
`--spec`, `--source`, `--output`, `--parameters`: все обязательны. Полный argv
публикации и SDK операций задан [ERP supervisor](../../config/worktree-preparation/erp-supervisor-infra.example.json).
Для самостоятельного окружения подготовьте JSON тех же координат и выполните его:

```bash
"$start_app" infra --config "$operator_root/erp-supervisor.json" \
  --parameters "$supervisor_parameters" --base-dir "$repository" --requirements all
"$start_app" check --config "$profile/app.json" \
  --parameters "$profile/parameters.json" --base-dir "$repository"
```

`supervisor_parameters` содержит девять [координат](start-app-worktree-setup.md#полный-рабочий-preparesh)
и явно заполненные/производные values. Его при Task-вызове создаёт thin bridge.
В самостоятельном запуске оператор выбирает те же поля непосредственно. SDK
возвращает полный JSON результатов; сохраните stdout, stderr и фактический exit.

### Параметры и корни ERP

| Вход | Владелец/значение |
|---|---|
| `application_source` | Целевой checkout ERP, источник `platform-infrastructure/config/dev/start-app/*.example.json`. |
| `profile_root` | Новые app/infra/deps/adjust/check/run/processes/parameters JSON; переносимая конфигурация этого окружения. |
| `environment_source` | Операторская ссылка на утверждённые значения; consumer читает подготовленный `operator_source/erp-environment.example.env`. Полученный environment.env переносится в target create-only. |
| `consumer_root` | Working `.runtime/environment-consumer` с `bootstrap.env`, `runtime.env`, `environment.env`. |
| `sdk_python` | Python выбранной установленной версии SDK. |
| `console_tool` | Пакетный launcher видимых консолей. |
| `terminal`, `display`, `xauthority`, `terminal_font` | Явное консольное окружение конкретного запуска. |
| PHP/Composer/Node/npm | Заполненные [values](start-app-worktree-setup.md#заполнение-values). |
| Profile `parameters.json` | Полная проекция собственного tracked ERP parameters example, включая пути, порты, URLs и выбранные инструменты. |

При публикации JSON edits задают точный массив `path` и `value`; тексты
публикуются стандартным шаблонизатором. Неизвестные поля копируемых документов
сохраняются. Исполняемый consumer получает выбранный SDK из quoted literal
`ERP_ENVIRONMENT_PYTHON_BIN` в `runtime.env`; путь с пробелами передаётся целиком.
Общий environment и согласованные адреса/порты заполняются явно до запуска.
Для параллельной проверки выберите свободные собственные порты и обновите все
связанные URLs; работающие экземпляры не требуется останавливать.

### Ресурсы и обслуживание ERP

[Полная цепочка](../../config/worktree-preparation/erp-supervisor-infra.example.json)
сначала публикует inputs/consumer, затем выполняет `infra` для environment/base,
`deps`, полную `infra`, `adjust`, `check`. Для каждой зависимости используется
объявленная цепочка внешних команд и проверки ресурса. Миграции данных относятся
к инфраструктуре. Post-deps `adjust` использует уже установленный PHP dotenv parser.

Для поддержания подготовленного окружения и отдельного запуска:

```bash
"$start_app" adjust --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository" --requirements all
"$start_app" check --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository"
"$start_app" run --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository" --requirements all
```

В установленном ERP wrapper `bash "$repository/start-app" ...` использует собственный
runtime selector; аргументы operation SDK передаются ему по выбранному контракту.
`run` исполняет ровно конфиг запуска, подготовку он не добавляет. `check` использует
обязательный presence каждого ресурса, объявленный availability и отдельный
coherence-конфиг. Отсутствующая availability отражается как `not_configured`;
это не выдуманное подтверждение доступности.

### Дополнительные CLI-зависимости ERP

Зависимости CLI расчётчика `erp-calendar-optimizer` входят в подготовку ERP до
запуска приложения. В рабочей установке этот шаг задаётся исходной операционной
спецификацией `application_spec` из values подготовки. Связь источника с новыми
деревьями описана в [настройке values](start-app-worktree-setup.md#заполнение-values).

В её `documents` выберите проекцию исходного
`platform-infrastructure/config/dev/start-app/deps.example.json` в `deps.json`.
Настройте две правки, сохраняя исходные команды и остальные ресурсы:

1. Для `path: ["requirements", 0, "commands"]` значение `value` содержит исходную
   цепочку Node `deps`, затем приведённую ниже команду Composer.
2. Для `path: ["requirements", 0, "checks", "presence", "commands"]` значение
   `value` содержит исходную проверку Node `dependencies`, затем проверку autoload.

Команда установки в исходной спецификации:

```json
{
  "argv": [
    "${composer_executable}",
    "install",
    "--no-interaction",
    "--prefer-dist",
    "--no-progress",
    "--no-scripts"
  ],
  "cwd": "$${instance}/erp-calendar-optimizer",
  "env": {
    "PATH": "$${path}",
    "COMPOSER_HOME": "$${composer_home}",
    "COMPOSER_MIRROR_PATH_REPOS": "1"
  },
  "interpreter": {
    "name": "exit",
    "options": {"accept": [0]}
  }
}
```

Проверка наличия установленного autoload в той же спецификации:

```json
{
  "argv": [
    "/usr/bin/test",
    "-s",
    "$${instance}/erp-calendar-optimizer/vendor/autoload.php"
  ],
  "cwd": "$${instance}",
  "env": {"PATH": "$${path}"},
  "interpreter": {
    "name": "exit",
    "options": {"accept": [0]}
  }
}
```

`composer_executable` выбирается в values оператором и подставляется при
публикации. Запись `$$` сохраняет placeholders для последующего исполнения SDK;
так же оформите placeholders сохраняемых исходных Node-команд. Параметры
`instance`, `path`, `composer_home` заполняются действительными координатами
нового дерева. Composer устанавливает пакеты по его собственному lock-файлу.

Повторная публикация и подготовка новых Task получают эту цепочку из постоянной
рабочей спецификации. Проверьте свежую подготовку, наличие autoload, штатный
`check` и прикладной сценарий расчёта. Повторная подготовка сохраняет данные
экземпляра. Рабочая спецификация и заполненные values хранятся вне Git;
версии инструмента и пакетов выбираются средствами установки.

### Конфигурация и результаты ERP

Рабочие env/settings создаются при отсутствии; пользовательские bytes повторной
подготовкой не заменяются. JSON результата SDK и actual exit должны одновременно
подтверждать успех. Наличие рабочего файла, успешный запуск команды диагностики или
отдельный tooling smoke сами по себе не подтверждают готовность всего приложения.
Для `run` дополнительно проверьте реальные HTTP/process endpoints выбранной среды.
Полные записи сохраняют версии поставки, source commit, конфиги/digests и результаты.

### Сохранённые настройки ERP

Старое сохранённое дерево может иметь local-development.settings.json без девяти
дескрипторов units, хотя новый projected `documents/local-settings.json` уже их
содержит. Create-only публикация сохраняет этот старый файл. Владелец дерева
выполняет точную миграцию working data через SDK с утверждённым environment:

1. Выберите свой repository, его projected settings, установленный native PHP dotenv
   parser, утверждённый environment и собственный audit. Проверьте все входы до записи.
2. Используйте принятый SDK envelope из постоянного журнала: дело
   `ENV2-LIVE-CUTOVER-001/existing-unit-recovery`, `README.ru.md`, `app.json`,
   `infra.json`, `empty.json` и собственный parameters JSON. Helper — соседний
   `executor-legacy-unit-configuration-recovery.php`. Расположение общего журнала:
   [постоянное хранилище](../operations/harness-incident-journal.md#постоянное-хранилище).
3. Передайте `start-app infra --config ENVELOPE/app.json --parameters PARAMETERS
   --base-dir REPOSITORY --requirements all`. Wrapper выделяет ровно утверждённые
   units assignments; native parser проверяет обе dotenv до любых target writes.
4. Подтвердите сохранение прежних raw bytes/modes, неизвестных настроек и audit.
   Миграция добавляет отсутствующие metadata descriptors; согласованный полный
   набор values сохраняет, отсутствующий набор дополняет. Повтор не меняет результат.
5. Сохраните необходимые материалы аудита в постоянном журнале до очистки Task,
   соблюдая правило хранения секретов. Выполните новый штатный preparation intent
   с новым `preparation_request_id`, затем получите полный check/READY от владельца.

Девять ключей: `QUANTITY_UNITS_LENGTH`, `QUANTITY_UNITS_WIDTH`,
`QUANTITY_UNITS_HEIGHT`, `QUANTITY_UNITS_PIECES`, `QUANTITY_UNITS_CALENDAR_DAYS`,
`PRICE_UNITS_WIRE`, `PRICE_UNITS_CONCRETE`, `PRICE_UNITS_SETUP`, `COST_UNITS`.
Они берутся из собственного утверждённого источника, не из предположений инструмента.
Envelope — проверенный ограниченный ручной ремонт рабочих данных, не новая
автоматическая функция ERP или SDK. Его результат `configuration_migrated` либо
`configuration_unchanged` содержит `native_preparation_verified=false`. Саму готовность доказывает
следующая полная preparation. Этот порядок сохраняет исторический отказ и данные.

#### Обязательные таблицы рабочего runtime

Список `localBootstrap.requiredTables` задаётся
[шаблоном runtime](../../config/projects/erp/host-runtime-settings.example.json);
для `planning-workspace` он включает `execution_work`. При первичной подготовке
опубликованный `documents/host-runtime-settings.json` переносится в
`platform-infrastructure/config/dev/local-dev-host-runtime.settings.json`
через `cp --update=none`, сохраняя уже существующий рабочий файл.

Для сохранённого runtime владелец выполняет отдельную согласованную коррекцию
через SDK и выбранный JSON-инструмент: сверяет исходный SHA-256, добавляет только
`execution_work` после `migrations`, сохраняет остальные настройки и режим файла,
затем проверяет результат. Повтор принимает согласованный правильный результат
без записи. Изменение списка не выполняет SQL или миграции; фактическую готовность
источников подтверждает последующая проверка ERP.

### Процессы и диагностика ERP

Конфиг `run` запускает процессы в явно выбранных видимых консолях; пользователь
управляет каждым процессом в его консоли. Автоматические проверки используют
собственный display/консоли и собственные порты, сохраняя пользовательский focus.
В проверенном маршруте bwrap/Xvfb изолируют проверочный display. Это настройка
проверочной среды, а не скрытая обязательная консоль пользователя.
Завершение проверки останавливает только её собственные процессы. Групповой
менеджер пользовательских серверов этим подключением не вводится.

## Согласованное обновление потребителей

[Заполненные примеры цепочки](../../config/maintenance/consumer-update-app.example.json)
и [операций](../../config/maintenance/consumer-update-infra.example.json) вызывают
стандартный npm, проверенную публикацию selectors и **обязательный** gate всех
действующих зарегистрированных потребителей. Наблюдатель
[consumer_updates.py](../../src/poise_environment/consumer_updates.py) только читает
inventory и исполняет явные проверки через общий runner; установку он не выполняет.

### Рабочие файлы обновления

Создайте рабочие копии внутри ignored operator/update, заполнив пути установки:

| Рабочее имя | Отслеживаемый источник |
|---|---|
| `app.json` | [update app](../../config/maintenance/consumer-update-app.example.json) |
| `consumer-update-infra.json` | [цепочка npm/publication/gate](../../config/maintenance/consumer-update-infra.example.json) |
| `parameters.json` | [параметры обновления](../../config/maintenance/consumer-update-parameters.example.json) |
| `publication.json` | [план selectors](../../config/maintenance/consumer-update-publication.example.json) |
| `gate-app.json` | [gate app](../../config/maintenance/consumer-update-gate-app.example.json) |
| `check.json` | [gate check](../../config/maintenance/consumer-update-gate-check.example.json) |
| `empty.json` | [пустая операция](../../config/worktree-preparation/empty.example.json) |
| `gate-parameters.json` | [параметры gate](../../config/maintenance/consumer-update-gate-parameters.example.json) |
| `rollout.json` | [инвентаризация и проверки](../../config/maintenance/consumer-update-rollout.example.json) |
| `consumer_updates.py` | Постоянная копия принятого владельца наблюдения, ссылка выше. |

Проверьте относительные имена `operations` в каждом app: они относятся к его
папке. В частности gate выбирает `check.json`, а update — `consumer-update-infra.json`.
Все файлы примеров — declarations; реальные inputs заполняются под выбранное
окружение. Их публикация сама не устанавливает новую версию.

| Параметр обновления | Назначение |
|---|---|
| `base`, `path`, `home`, `module_path` | cwd и явное окружение выбранных инструментов/Poise. |
| `node`, `npm_entry_point` | Стандартный npm CLI в согласованном Node. |
| `bootstrap_python` | Явный исходный Python установки SDK. |
| `supply_prefix`, `archive` | Постоянный prefix и принятый локальный npm archive; до установки подтвердите его источник/digest. |
| `start_app`, `sdk_python`, `console_tool` | Публикуемые entry/SDK Python/console launcher принятого prefix. |
| `publication` | Полный JSON плана правок selectors. |
| `gate_config`, `gate_parameters` | app и параметры обязательной проверки после публикации. |

`publication.json` содержит два массива `json` и `shell` (пустые допустимы).
JSON запись: `file` и `edits[]` с точным массивом `path` и `parameter`.
Допустимые значения `parameter` в обеих формах: только `start_app`, `sdk_python`,
`console_tool`; значения берутся из соответствующих parameters. Shell запись: `file`, `key`, `parameter`. Сначала стандартный jq читает
и проверяет весь план/записи, затем выполняются правки. Сохраняются неизвестные
target fields и file modes; повтор той же поставки сохраняет selectors. Это
не транзакция возврата всех файлов при любой возможной ошибке записи.
Рабочий shell input доверенный и заполняется владельцем своей установки.

`gate-parameters.json` задаёт `python` действий, постоянный `observer`, путь
`consumer_config` к rollout, `base`, `module_path`, `path`, `home`. Gate check
запускает observer с единственным обязательным `--config PATH`; `-h/--help` даёт
справку. Наблюдатель не хранит второй реестр проектов.

### Инвентаризация и подтверждение совместимости

| Поле rollout.json | Назначение |
|---|---|
| `inventory.argv`, `cwd`, `env` | Команда существующего публичного `project_tools.list`, которая возвращает `status=listed`, `projects`, `errors`. |
| `expected_entry_stdout` | Полная ожидаемая строка `start-app --version` с переводом строки для выбранной поставки. |
| `expected_sdk_version` | Ожидаемая версия именно принятой пакетной поставки для runtime observations. |
| `consumers` | Все действующие потребители фактического inventory, по одному на identity. |
| `excluded_projects` | Только явно классифицированные непродуктовые регистрации: `project` и непустая `reason`. |
| `capture_directory` | Постоянный выбранный рабочий каталог captures этого обновления; не Git. |
| `timeout_seconds` | Предел отдельной наблюдаемой команды. |

Каждый consumer содержит `project`, `preparation_config` с выбранным
`parameters.start_app`, `runtime` и `compatibility`. Runtime равен `null` при
отсутствии отдельного selector либо объекту `path` (доверенный Bash env) и
`python_key` (имя выбранного SDK Python). Compatibility — `argv`, `cwd`, `env`,
`expected_exit_code`, `expected_status`; команда должна вернуть полный JSON со
`status`. Настройки явно выбирают как готовность ресурсных проверок, так и
собственную прикладную диагностику окружения, когда она требуется для совместимости.

Пример перечисляет пять потребителей одной установки; перед каждым обновлением
получите актуальный inventory и заполните **все** его реальные приложения.
Пример `diagnostic-fixture` заменяется только действительной диагностической
регистрацией либо удаляется из списка вместе с отсутствующей регистрацией.
Каждый проект имеет ровно одну классификацию; пропуск, дублирование, пересечение,
ошибочный inventory или неуспешная совместимость возвращают `status=error`/exit1.
`ready`/exit0 содержит готовность каждого consumer и пустой `errors`.

### Выполнение обновления

Подготовьте реальные compatibility inputs и конфиги выбранной версии до запуска.
Все потребители получают принятую поставку в одном согласованном обновлении:

```bash
"$start_app" infra --config "$update/app.json" --parameters "$update/parameters.json" --base-dir "$poise_source" --requirements all
"$start_app" check --config "$update/gate-app.json" --parameters "$update/gate-parameters.json" --base-dir "$poise_source"
```

Внешняя операция `infra` уже включает обязательный gate; отдельный `check` полезен
как повторное наблюдение завершённой поставки. Запросы потребителей сохраняют полный
stdout/stderr/exit в отдельных captures. Версия здесь — предмет принятия конкретной
поставки, а не встроенный запрет запуска произвольной patch-версии зависимости.

Готовность фиксируется только после успеха всех потребителей. Исправление известной
ошибки выполняется владельцем её inputs и последующей полной проверкой; ни публикация
части selectors, ни успешный npm install по отдельности не завершают обновление.
Для нового обновления создайте свежие compatibility inputs. До очистки Task
отведите использованные временные rollout/parameters от рабочих вызовов и сохраните
доказательства постоянно; selected SDK, manifests и постоянные profiles сохраняются.

## Переход на новый маршрут и возврат

Сохраните прежнюю согласованную поставку, рабочие configs/selector bytes/modes,
исходные ревизии и данные до переключения. Исторически подтверждён отдельный
изолированный переход1→2→1→2; это не испытание возврата каждого действующего
приложения с текущей версией его данных. Возврат согласуется по реальным операциям
и совместимости конкретного экземпляра.

### Пять ворот до переключения

1. Принятый package archive, его digest и постоянный выбранный prefix подтверждены.
2. Рабочие профили и значения соответствуют выбранному SDK и target revision.
3. Независимое развёртывание Poise проходит infra/deps/check и выбранный запуск.
4. Независимое ERP проходит подготовку/adjust/check и реальный HTTP/process запуск
   на собственных свободных портах с согласованными URLs.
5. Полный фактический inventory принятой поставки проходит обязательный all-consumer gate.

### Подготовка и выбор selector

Выбор команды принадлежит рабочим конфигам оператора и manifests проектов;
настройка Bootstrap собственного экземпляра — его рабочему launcher config.
Выберите их через соответствующих владельцев, сохраняя реальные locks/revisions.
Переключение не означает принятие Task или интеграцию её кода. При working-data
исправлении сохраните before/after/digest/mode и проверенный outcome в журнале.

### Явный возврат и конечный выбор

Остановите только собственные писатели возвращаемого профиля. Восстановите
совместимый сохранённый комплект SDK entry/Python, configs, profiles и selectors
через их владельцев. Сохраните существующие application databases и историю;
если их схема менялась, используйте согласованный владельцем данных способ возврата.
Выполните тот же обязательный consumer gate и реальные проверки запуска. Зафиксируйте
конечный выбор; возвращённые bytes сами по себе не подтверждают работоспособность.

### Полный комплект доказательств и постоянная поставка

Сохраняйте принятый package/digest, source commit, входы/полные captures/exit,
реальные readiness observations и независимую приёмку до удаления временных областей.
Терминальный статус Task не доказывает доставку или физическое удаление ресурсов.
Постоянные отказы и ручные обходы хранятся у
[владельца журнала](../operations/harness-incident-journal.md#постоянное-хранилище),
актуальные дефекты — в [versioned реестре](../operations/known-bugs.md#ведение-реестра).

## Выбор экземпляра и действия

До запуска выбирают конкретный обслуживаемый экземпляр, его рабочий профиль,
установленный SDK, исходники и явные параметры. ID ресурсов и поддерживаемые
действия берут из действующего профиля, а не из ERP-примера. Сначала выполняют
нужную подготовку, затем проверяют соответствующие условия и запускают выбранный
процесс. Проверка не подменяется установкой или ремонтом; запуск сам по себе
не доказывает доступности сервиса. Для выравнивания используют только реально
поддержанные профилем действия.

Примеры версионируются, рабочие значения хранятся вне Git. Ошибка указывает
недостающее условие и допустимое продолжение; сохраняют настоящий статус и полные
потоки, затем выполняют относящуюся к нему повторную проверку. Не угадывают
недостающий dotenv, не меняют чужие данные и не копируют ERP start-app, Composer
или состав приложений в Poise. Агентская проекция:
[infrastructure](../../.agents/skills/infrastructure/SKILL.md).
