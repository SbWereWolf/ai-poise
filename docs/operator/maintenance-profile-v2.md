# Рабочие профили обслуживания Poise

## Назначение и условия

Собственный экземпляр обслуживается через самостоятельный `environment-maintenance`:
`infra`, `deps`, `check`, `run` используют конфиги одинакового формата.
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
tool_prefix=/srv/tools/environment-maintenance
tool_python="$tool_prefix/node_modules/environment-maintenance/.venv/bin/python"
wheelhouse=/srv/packages/poise-wheels
state_root=/srv/poise/data/own-instance
profile="$repository/operator/profile"
project_id=own-instance
base_ref=master
author_name=Operator
author_email=operator@example.invalid

"$python_runtime" -m venv "$repository/.venv"
poise_python="$repository/.venv/bin/python"
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

Для новой установки внешнего инструмента заранее выберите принятый npm-пакет
с operation-интерфейсами и выполните его установку отдельно, например:

```bash
tool_package=/srv/packages/environment-maintenance-2.0.0.tgz
npm install --prefix "$tool_prefix" --no-audit --no-fund "$tool_package"
```

Это установка выбранного артефакта, а не утверждение о публикации версии в registry.
Для уже подготовленного `tool_python` повторная установка не требуется.
Подготовка runtime внешнего приложения принадлежит его инструкции поставки.

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
принадлежат Bootstrap. ERP, действующий catalog/v2 и подготовка Task-worktree
сохраняют отдельные command config и свои операции. Выбирайте их собственные
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

## Профиль ERP

ERP использует отдельный выбранный каталог: **56 ресурсов в 21 группе**.
Сохранённый самостоятельный каталог Poise содержит 14 ресурсов; собственный
operation-профиль основной инструкции содержит 15 ресурсов (12 infra и 3 deps).
Владелец ERP-профиля — поставляемая
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
отдельно в [известных
ограничениях](../operations/known-bugs.md#aip-operator-upgrade-compatibility-001--согласование-runtime-после-обновления).

Для автоматической подготовки новых Task подключите
[установленный start-app](start-app-worktree-setup.md#подключение-установленного-инструмента)
один раз для каждого проекта. Действительные repository/profile/state выбираются
из native координат новой области; предварительная ручная материализация каждой
Task не требуется. ERP-вариант согласует Playwright install/probe и consumer
под корневым `var/**`, сохраняя все56ресурсов.

При выборе явного заранее материализованного каталога выполните
[Task-specific подготовку профиля](worktree-preparation.md#профиль-конкретной-задачи):
отдельные profile/state и `--repository`, равный её фактическому или выбранному
будущему worktree. Связаны и catalog_dir, и values, и все settings destinations.
Рабочий configuration, shared environment и test-execution поставляются явно;
готовность56ресурсов/21группы не подтверждает запуск полного бизнес-CJM.

Ниже — самостоятельное обслуживание существующего ERP checkout, а не автоматическое
переназначение профиля для Task. Для этого маршрута выберите существующий
ERP-репозиторий и доступную локальную базовую ревизию.
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
| `template_id` | `--template`, исходно `common`. |
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

## Переход на новый маршрут и возврат

Ниже сохранена операторская процедура перехода отдельно выбранных catalog/v2
профилей Poise и ERP. Их resource counts, values и команды относятся к этим
профилям. Новый собственный operation-профиль использует описанную выше
[инструкцию](#развёртывание-из-примера) и `--base-dir`; его публикация не выполняет
это переключение. Для существующего каталожного оператора выбирайте его сохранённую
совместимую поставку, source и рабочую команду.

Это операторская процедура выбора команды обслуживания, а не новый автоматический
координатор ворот. Версии внешних зависимостей выбирает пакетный менеджер.
Poise исполняет явно настроенную команду и передаёт полный результат выбранному
сменному [плагину Bootstrap](bootstrap-catalog-v2.md#интерфейс-подключаемого-обработчика).
Он обрабатывает распознанную ошибку с одной разрешённой попыткой исправления
в текущем вызове; неизвестную либо повторную ошибку возвращает агенту.

Выберите общую поставку Poise с `src/`, `config/` и корневым `bootstrap`,
поддерживаемый Python Poise, отдельное окружение внешнего инструмента,
целевые репозитории и доступные локальные ревизии. Запишите отдельно commit/tree
проверяемого кандидата и commit фактически выбранного runtime: готовность одной
копии не доказывает готовность другой. Пути поставок и профилей задаются явно,
не зашиваются в продуктовый код. Для текущего Python действий3.15 заполните
рабочие `python_minimum="3.15"` и `python_maximum="3.16"`.

Подготовьте [полный отдельный каталог Poise](bootstrap-catalog-v2.md#входы-универсального-каталога)
и
[полный профиль ERP](#подготовка-erp-профиля) в разные выделенные папки внутри
выбранного Poise. Для каждого задайте отдельные state/cache и заполните values,
рабочую команду Bootstrap и источники конфигураций. Согласуйте Python действий,
consumer и пути по инструкциям профилей. Для Task используйте
[автоматическое подключение](start-app-worktree-setup.md#подключение-установленного-инструмента)
либо явно подготовьте [её собственный профиль](worktree-preparation.md#профиль-конкретной-задачи).
Рабочие configs/values/selectors и retained supply разместите в ignored области
вне удаляемых Task-root и worktree. В Git остаются санитарные примеры и инструкции.

### Пять ворот до переключения

Выполните последовательно все пять проверок на фактически выбранных входах:

| Ворота | Достаточный наблюдаемый результат |
|---|---|
| `supply` | Принятые wheel/manifest/исходная ревизия и предусмотренные поставкой входы совпали по digest; пакетный менеджер успешно установил consumer в выбранное отдельное окружение. |
| `profiles` | Оба полных профиля опубликованы из выбранной поставки; сохранены их файлы, значения и digests:14ресурсов Poise и56ресурсов ERP в21группе. |
| `poise-clean-room` | В отдельном окружении реально выполнены `deps`, `infra`, `check` полного профиля Poise; подтверждена готовность всех14ресурсов. |
| `erp-clean-room` | В отдельном окружении реально выполнены `infra`, `deps`, `check` полного профиля ERP; подтверждена готовность всех56ресурсов. |
| `erp-task-worktree` | Штатный bootstrap создал отдельную Task worktree и подтвердил её собственную preparation; сохранена полная квитанция и все её входы/потоки. |

До замены текущего launcher выполняйте проверки непосредственно через абсолютный
`bootstrap`, как в инструкциях обоих профилей. Необходимые ERP настройки и
test-execution задаются по [инструкции подготовки
области](worktree-preparation.md#рабочий-файл-и-подключение-проекта).
Изолированная Task должна иметь законное поручение и собственные данные;
её подготовка не подменяет приёмку рабочей задачи или бизнес-CJM ERP.

При предварительном отказе сохраните прежний selector и поставку без изменения,
а также причину и полные результаты неуспешной проверки. Агент решает следующую
проблему по диагностике. После изменения входа заново докажите затронутую проверку.
Одного напечатанного PASS, корректного JSON либо наличия всех записей недостаточно.

### Подготовка и выбор selector

Заранее сохраните три явно различимых файла:

- `S0` — точные исходные bytes действующего `STRIDE_CONFIG` и их SHA-256.
- `Slegacy` — полностью заполненный selector явного возврата: retained Python/CLI
  и его собственные старые входы, например `--catalog` и `--repairs`.
- `Snew` — полностью заполненный новый selector с тремя префиксами абсолютного
  Bootstrap из [примера launcher](../../config/stride-launcher.example.json).

Проверьте фактическую работоспособность retained consumer. Если исходный selector
не обслуживает окружение, его неизменная копия `S0` всё равно сохраняет исходный
факт; работоспособный `Slegacy` готовится отдельно и не называется теми же bytes.
Проверка `check --help` подтверждает выбор consumer и его синтаксис, но не здоровье
всех старых ресурсов. Для обещания готовности старого окружения нужна его реальная
проверка с собственными входами.

Сохраните прочие поля selector, включая неизвестные опции, default/work/next,
roots и код отказа. Не меняйте старые установленные файлы и рабочие входы.
В `Snew` три override имеют вид выбранный Python, `-I`, `-B`, абсолютный
`$poise_root/bootstrap`, имя `infra`/`deps`/`check`. Source-команда `-m poise`
в default/next сохраняет свои `-P -s -B`. Подробности разрешения путей и аргументов
принадлежат [launcher](../configuration/stride-launcher.md#выполнение-команды).

Только после пяти успешных ворот замените selector целым подготовленным файлом.
В примере `selector` — действующий `STRIDE_CONFIG`; `prepared_new` и `prepared_legacy`
— проверенные неизменяемые копии `Snew` и `Slegacy`. Оператор заранее выбрал их пути,
закрыл других писателей selector и сверил текущие bytes с сохранённым `S0`:

```bash
selector="$poise_root/config/stride-launcher.json"
prepared_new="$poise_root/operator/selectors/Snew.json"
prepared_legacy="$poise_root/operator/selectors/Slegacy.json"
cp -- "$prepared_new" "$selector.next" &&
  mv -T -- "$selector.next" "$selector"
```

Соседний `selector.next` находится на той же файловой системе: стандартный rename
выбирает целое содержимое одного файла. Это не транзакция установки, профилей и БД.
Связка `&&` допускает переименование только после успешного полного копирования.
При ошибке копирования команда возвращает ненулевой код, действующий selector
остаётся прежним; перед повтором оператор устраняет указанную причину staging-ошибки.
Шаблон команды рендерится существующим стандартным механизмом Bootstrap;
для перехода не нужен собственный renderer, gate engine или менеджер версий.

После выбора выполните через
[Stride](poise-command-reference.md#обслуживание-catalogv2-через-stride)
реальные `infra`, `deps`, `check` с явными `--config` и `--catalog-dir` каждого
профиля. Сохраните полные результаты и проверьте фактические bytes selector.
`--values`, `--set` и `--dry-run` передаются тем же буквальным suffix по
[контракту Bootstrap](bootstrap-catalog-v2.md#конфигурация-команды).

### Явный возврат и конечный выбор

Возврат выполняется тем же выбором целого файла, без удаления новой поставки:

```bash
cp -- "$prepared_legacy" "$selector.next" &&
  mv -T -- "$selector.next" "$selector"
```

Проверьте реальный retained consumer с его документированными входами.
Сопоставьте старые installed files и рабочие входы с сохранёнными digest.
Если окончательный выбор — новый маршрут, повторите замену из `prepared_new`
и выполните свежий `check` обоих новых профилей. Запишите конечный выбранный
selector/digest, runtime и наблюдаемый результат. Возврат команды не откатывает
изменения бизнес-данных, а готовность изолированного окружения не означает
проверенного пользовательского маршрута ERP.

### Полный комплект доказательств и постоянная поставка

Сохраните вместе точные source commit/tree кандидата, runtime commit,
идентичности внешней поставки и установки, оба полных профиля/values/inventories,
все команды с argv/cwd/env, exit, полными stdout/stderr и digest. Зафиксируйте
порядок пяти ворот, исходный и выбранные selectors, результаты возврата,
конечный выбор и сохранность retained входов. Тестовый отказ остаётся отказом
проверяемого случая, а не подменяется общим успешным итогом.

Для [Task preparation](worktree-preparation.md#запуск-и-чтение-результата) нужен
не только верхний DTO: прочитайте полную receipt по SHA и сохраните configuration
snapshot, input snapshots, captures и все referenced command streams. При публикации
копий через [artifact owner](../workflows/batch-work.md#создание-файлов) сохраните
mapping producer path → фактический зарегистрированный путь с bytes/digest.
Оригинальные receipt bytes и ссылки внутри них не переписываются.

Опубликуйте полный комплект до перезаписи basetemp и удаления Task материалов.
Запись выполняется во время действительного ACTIVE этапа; если результат уже
VERIFIED, передайте сохранённые файлы следующему ACTIVE владельцу по
[правилу комплекта при передаче](../workflows/local-handoff.md#постоянный-комплект-при-передаче).
Это не требует фиктивной смены статуса или перезапуска Task. Не повторяйте уже
выполненную мутацию только ради получения её сохранённого вывода.

Измерьте самый большой полный файл, включая вложенные capture/receipt.
Если `batch.max_artifact_bytes` недостаточен, измените его через существующего
[владельца конфигурации](../configuration/project-setup.md#назначение), затем
проверьте реальную полную публикацию. Для проверенных preparation receipts
около1.75–1.89MB выбран предел4MiB; это измеренное значение установки, не новый
универсальный default. Preview не заменяет полные bytes.

Task/Sprint artifact и его зарегистрированный путь сами по себе не являются
согласованной постоянной поставкой. До terminal retirement задайте нужные
результаты через [delivery/agree и
delivery/settle](../workflows/terminal-materials.md#публичный-договор-поставки),
проверьте фактические постоянные файлы, digests, mapping и всех потребителей
ссылок. Приёмка, интеграция Git и удаление служебных материалов имеют отдельные
результаты. Новый commit документации или интеграционного кандидата получает
собственное применимое доказательство; старый PASS не переименовывается.


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
