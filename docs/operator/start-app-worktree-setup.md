# Настройка start-app для новых деревьев Poise и ERP

## Подключение установленного инструмента

Эта инструкция настраивает автоматическую подготовку обоих проектов.
[Протокол подготовки](worktree-preparation.md#опции-рабочего-json) описывает все
поля, placeholders и результат. Рабочие файлы создаёт оператор один раз;
профиль отдельной Task затем материализуется из её действительных координат.

Проверенный маршрут использует самостоятельное приложение environment-maintenance
с командой `start-app`, установленное отдельно от Poise и ERP. В этой установке
выбран релиз приложения 1.0.0; `catalog/v2` и `result/v2` — версии его протоколов,
а не приложение 2.0. Версии поставки выбирает оператор средствами установки;
Poise запускает настроенный argv. Разработка ENV2 к этому маршруту не относится.

### Условия

Нужны Linux/WSL, Bash, jq, Git, подготовленные проекты `poise-op` и `erp`
с выделяемым Task worktree и доступной базовой ревизией. Python действий Poise
выбирается явно: текущая поставка использует >=3.15,<3.16 и её установленные
зависимости. Python самого внешнего приложения — отдельный runtime его установки.
Для ERP заранее выберите доступные PHP с нужными расширениями, Node, npm,
Composer и согласованные expectedToolchainIdentity. Эти значения не подбираются
лаунчером вместо оператора. Полные условия ресурсов — в
[руководстве ERP](maintenance-profile-v2.md#ресурсы-и-обслуживание-erp).

Примеры ниже используют обезличенные абсолютные пути. Замените их во всех
рабочих JSON и командах на пути своей установки и укажите своё имя/email автора.
`/srv/poise/app` — выбранный код Poise, `/srv/environment-maintenance` —
самостоятельный корень установленного внешнего приложения со своим runtime.
Основной ERP checkout остаётся Git-источником базы, не целью установки ресурсов.

```bash
poise_root=/srv/poise/app
poise_python=/srv/poise/app/tools/.venv/bin/python
start_app=/srv/environment-maintenance/start-app
operator_root="$poise_root/operator/profile/start-app-worktrees"
mkdir -p -- "$operator_root"
cp -- "$poise_root/config/worktree-preparation/poise-op.example.json" "$operator_root/poise-op.json"
cp -- "$poise_root/config/worktree-preparation/erp.example.json" "$operator_root/erp.json"
cp -- "$poise_root/config/worktree-preparation/poise-op-values.example.json" "$operator_root/poise-op-values.json"
cp -- "$poise_root/config/worktree-preparation/erp-values.example.json" "$operator_root/erp-values.json"
```

Это установка в новый выбранный рабочий каталог. При обслуживании существующей
установки сохраните её файлы и согласуйте конкретное изменение вместо замены
заполненных настроек примерами. Здесь используется уже игнорируемый Poise путь
`operator/profile/`; проверьте его в выбранной поставке через `git check-ignore`.
Только санитарные `config/worktree-preparation/*.example.json` отслеживаются Git.
Рабочие JSON, values, `prepare.sh`, manifests и настройки Stride остаются вне
коммитов. Можно выбрать другой явно исключённый operator каталог внутри Poise.

### Заполнение двух конфигов подготовки

Откройте рабочие `poise-op.json` и `erp.json`. Все значения перечислены ниже;
данные-примеры —
[Poise](../../config/worktree-preparation/poise-op.example.json) и
[ERP](../../config/worktree-preparation/erp.example.json).

| Поле | Что указать |
|---|---|
| `parameters.source_root` | Выбранный абсолютный корень кода Poise с профилями и action modules. |
| `parameters.python` | Абсолютный Python действий Poise с зависимостями этой поставки. |
| `parameters.start_app` | Абсолютная команда установленного независимого приложения `start-app`. |
| `parameters.operator` | Абсолютный постоянный рабочий каталог с `prepare.sh` и двумя values. |
| `catalog_dir` | `${task_root}/environment-maintenance/profile`: новый профиль внутри собственной Task. |
| `inputs` | Снимки рабочего script и values выбранного проекта с destinations под `${task_root}`. |
| `operations` | `prepare`, затем `check`: исполнение и отдельное подтверждение готовности. |
| `commands.prepare` | Bash и script из Task snapshot, далее девять явных аргументов из примера. |
| `commands.check` / `verify` | Установленный `start-app check` с отдельными `--catalog-dir` и `--values`. |
| `commands.repair` | Явная команда `start-app run`, доступная интерфейсу handler. |
| `handler.callable` | Для Poise `apps.bootstrap.environment_result:handle`, для ERP `apps.bootstrap.erp_result:handle`. |
| `handler.options` | Явные schema/коды/статусы; `repair_operations=[]` этого маршрута оставляет обслуживание `run`, без дополнительного plugin repair. |
| ERP `configuration_root` / `configuration_targets` | Текущий `${worktree}` и три его JSON settings, совпадающие с destinations в ERP values. |

Полный справочник handler options и стандартного `string.Template.substitute` —
[протокол](worktree-preparation.md#обработчик-и-одна-попытка-исправления).
Процесс запускается с готовым argv без shell-разбора; shell здесь выбран явно
первым аргументом `/bin/bash`. Values передаются готовыми значениями, без второго
прохода подстановок. Координаты `${worktree}`, `${task_root}`, `${base}`,
`${project_id}` поставляет сам Poise после допустимого выделения области.
Не нужно вручную вычислять будущий путь каждого Task.

### Заполнение values

Примеры:
[Poise values](../../config/worktree-preparation/poise-op-values.example.json),
[ERP values](../../config/worktree-preparation/erp-values.example.json).
Они задают именно операторские входы. Остальные параметры каталога берутся
из выбранной полной спецификации; script добавляет действительные координаты
Task. Рабочие каталоги, БД и caches принадлежат новой области и её Task root.

| Поле | Назначение |
|---|---|
| `profile_spec` | Абсолютная спецификация Poise `config/maintenance/profile.example.json` либо ERP `config/projects/erp/profile.example.json`. |
| `author_name`, `author_email` | Выбранная оператором Git identity. В примерах только нейтральные Operator/operator@example.invalid. |
| `python_minimum`, `python_maximum` | Границы Python действий, согласованные с выбранным runtime; здесь 3.15 и 3.16. |
| ERP `consumer_python` | Python самостоятельной установки environment-maintenance, отдельно от Python действий. |
| ERP `php_executable`, `node_executable`, `composer_executable` | Доступные абсолютные исполняемые файлы. Пути `/opt/...` являются местами заполнения. |
| ERP `npm_entry_point`, `npm_executable` | Вход npm для выбранного Node и его executable, согласованные с одной установкой. |
| ERP `expected_toolchain_identity` | Явные PHP majorMinor, Node majorMinor/modulesAbi для проверок ERP. Пример соответствует PHP8.4/Node22.23/ABI127, а не требованию Poise к версии внешнего start-app. |
| ERP `environment_file` | Общий локальный `.env`, относительно нового repository: `platform-infrastructure/config/dev/local-development.env`. |
| ERP `local_settings_path`, `host_runtime_settings_path`, `cjm_settings_path` | Три JSON назначения относительно того же repository. |
| ERP `local_settings_source`, `host_runtime_settings_source`, `cjm_settings_source` | Три исходных документа относительно собственного profile_root. |

ERP-профиль содержит все56ресурсов, Poise —14. Script согласует npm install/probe
и host-runtime Playwright entryPoint под корневым `var/test/node-modules/` нового
ERP дерева, копируя туда его собственные package.json/package-lock.json.
Зависимости не берутся из основной установки. Общий локальный env создаётся из
собственного tracked example только при отсутствии; существующий файл сохраняется.
Три потребителя Playwright связываются с одной подготовленной зависимостью.
Подтверждение их semantic directory identity сохраняет чужой несовместимый путь
и возвращает диагностируемую ошибку вместо его удаления.

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

Сохраните следующий текст целиком в `$operator_root/prepare.sh`.
Это проекция уже проверенной рабочей конфигурации, не новый встроенный движок.
Он исполняется через Bash из Task snapshot; executable bit для этого не требуется.
Его аргументы: source_root, action_python, start_app, project_id, repository,
task_root, profile, base, selected_values. Вход `selected_values` также является
сохранённым Task snapshot. stdout остаётся JSON результата, полные служебные
потоки сохраняются Poise отдельно.

```bash
#!/usr/bin/env bash
set -euo pipefail

source_root=$1
action_python=$2
start_app=$3
project_id=$4
repository=$5
task_root=$6
profile=$7
base=$8
selected_values=$9

author_name=$(jq -er '.author_name' "$selected_values")
author_email=$(jq -er '.author_email' "$selected_values")
profile_spec=$(jq -er '.profile_spec' "$selected_values")
mkdir -p -- "$(dirname -- "$profile")"
PYTHONPATH="$source_root/src:$source_root" "$action_python" -P -s -B \
  -m poise_environment.profile --source "$source_root" \
  --profile-spec "$profile_spec" --output "$profile" \
  --state "$task_root/environment-maintenance/state" \
  --repository "$repository" --project "$project_id" --base-ref "$base" \
  --author-name "$author_name" --author-email "$author_email" \
  --module-path "$source_root/src" >&2

jq --slurpfile selected "$selected_values" \
  --arg repository "$repository" --arg profile "$profile" \
  --arg state "$task_root/environment-maintenance/state" \
  --arg project "$project_id" --arg base "$base" \
  '(.parameters | with_entries(.value = .value.default)) +
   ($selected[0] | del(.profile_spec, .npm_executable, .expected_toolchain_identity)) +
   {repository:$repository, profile_root:$profile, state_root:$state,
    project_id:$project, base_ref:$base}' \
  "$profile/catalog.json" > "$profile/values.json.pending"
mv -- "$profile/values.json.pending" "$profile/values.json"

if [[ "$project_id" == erp ]]; then
  dependency_project="$repository/var/test/node-modules/host-platform-playwright"
  mkdir -p -- "$dependency_project"
  cp -- "$repository/platform-infrastructure/tests/playwright/package.json" \
    "$repository/platform-infrastructure/tests/playwright/package-lock.json" \
    "$dependency_project/"
  jq '
    .resources["erp-node-playwright"].cwd =
      "${repository}/var/test/node-modules/host-platform-playwright" |
    .resources["erp-playwright"].required_files[0] =
      "${repository}/var/test/node-modules/host-platform-playwright/node_modules/@playwright/test/cli.js" |
    .resources["erp-runtime-directories"].paths +=
      ["${repository}/var/test/node-modules/host-platform-playwright"]
  ' "$profile/resources.json" > "$profile/resources.json.pending"
  mv -- "$profile/resources.json.pending" "$profile/resources.json"
  jq --slurpfile values "$profile/values.json" --slurpfile selected "$selected_values" '
    .nodeExecutable = $values[0].node_executable |
    .phpExecutable = $values[0].php_executable |
    .composerExecutable = $values[0].composer_executable |
    .npmEntryPoint = $values[0].npm_entry_point |
    .npmExecutable = $selected[0].npm_executable |
    .expectedToolchainIdentity = $selected[0].expected_toolchain_identity |
    .testRunners.playwright.entryPoint =
      "var/test/node-modules/host-platform-playwright/node_modules/@playwright/test/cli.js" |
    .localBootstrap.nodeApplications =
      ["document-ui", "var/test/node-modules/host-platform-playwright"]
  ' "$profile/documents/host-runtime-settings.json" \
    > "$profile/documents/host-runtime-settings.json.pending"
  mv -- "$profile/documents/host-runtime-settings.json.pending" \
    "$profile/documents/host-runtime-settings.json"
  environment_relative=$(jq -er '.environment_file' "$profile/values.json")
  if [[ ! -e "$repository/$environment_relative" ]]; then
    cp -- "$repository/platform-infrastructure/config/dev/local-development.example.env" \
      "$repository/$environment_relative"
  fi
  if [[ ! -e "$repository/platform-infrastructure/test-execution.json" ]]; then
    jq '{schemaVersion:1,runners:{playwright:{driver:"playwright",workers:4}}}' \
      "$selected_values" > "$repository/platform-infrastructure/test-execution.json"
  fi
fi

run_result="$task_root/environment-maintenance/run-result.json"
run_exit=0
"$start_app" run --catalog-dir "$profile" --values "$profile/values.json" \
  > "$run_result" || run_exit=$?
cat -- "$run_result" >&2
if [[ "$run_exit" != 0 ]]; then
  cat -- "$run_result"
  exit "$run_exit"
fi
if [[ "$project_id" == erp ]]; then
  for owner in platform-infrastructure planning-workspace system-admin; do
    consumer="$repository/$owner/tests/playwright/node_modules"
    if [[ ! -e "$consumer" ]]; then
      ln -s -- "$dependency_project/node_modules" "$consumer"
    fi
    if [[ ! "$consumer" -ef "$dependency_project/node_modules" ]]; then
      printf 'Playwright consumer does not select the prepared dependency directory: %s\n' "$consumer" >&2
      exit 4
    fi
  done
fi
exec "$start_app" check --catalog-dir "$profile" --values "$profile/values.json"
```

Проверьте сохранённый текст и заполненные файлы перед подключением:

```bash
bash -n "$operator_root/prepare.sh"
"$poise_python" -m json.tool "$operator_root/poise-op.json"
"$poise_python" -m json.tool "$operator_root/erp.json"
"$poise_python" -m json.tool "$operator_root/poise-op-values.json"
"$poise_python" -m json.tool "$operator_root/erp-values.json"
```

Проверка синтаксиса не выполняет команды обслуживания и не доказывает готовность.
Сам Poise валидирует контракт рабочего JSON при действительной подготовке.

## Привязка проектов и первый запуск

Через [владельца конфигурации проекта](../configuration/project-setup.md#обновление-действующего-проекта)
задайте каждому manifest собственный абсолютный путь. Для ERP:

```json
{
  "worktree_preparation": {
    "configuration": "/srv/poise/app/operator/profile/start-app-worktrees/erp.json"
  }
}
```

В poise-op выберите соседний `poise-op.json`. Полный контракт и остальные поля
manifest остаются у своего owner. Для полного ERP capture в проверенной установке
явно выбран `batch.max_artifact_bytes=4194304`; учитывается размер отдельного
материала, а не preview. Конфигурационный editor проверяет наблюдённую revision.
Если первое добавление отсутствующей optional секции отклонено, используйте
[описанное ограничение и согласованный recovery](../operations/known-bugs.md#poise-project-optional-section-first-add-001).
Рабочий manifest не превращается в source-пример.

Выберите корни Stride так, чтобы `data_root` охватывал все реальные stores
выбранных проектов, включая Requirements. Если нужен их общий родитель,
это изменение только конфигурации запуска, не перенос БД. Условия —
[независимые корни](../configuration/stride-launcher.md#независимые-корни).
Изменение data_root не выдаёт caller identity или native binding.

Подготовка срабатывает по допустимому `work/bootstrap`: после выделения Task
ветки/папки Poise создаёт snapshots, исполняет `prepare`, затем `check` и сохраняет
receipt. Здесь нет фонового автозапуска, привязки к любому произвольному процессу
ERP или автоматического запуска бизнес-приложений. Новый профиль materializer
выбирает действительный repository; каждый Task получает отдельные profile/state.

Task должна быть ready и законно назначена. Сохраните пакет в
`$operator_root/bootstrap.json` со своим существующим Task ID и передайте
текущему выбранному entry:

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

Приоритет имеет собственный выданный native session entry соответствующего
проекта. Если для этой установки отдельно разрешён selected Stride route,
используется действительная native identity того же caller:

```bash
project_id=erp
packet="$operator_root/bootstrap.json"
bash "$poise_root/stride.sh" --poise-root "$poise_root" --project "$project_id" \
  "$poise_root/config/stride.env" work < "$packet"
```

Для poise-op выберите его project ID и Task. Разрешённый конкретной установке
маршрут не создаёт hook delivery и не является универсальной заменой чужому
launcher. Сессионные идентификаторы и binding не подставляются оператором
вместо настоящих host данных. Одновременно приобретается только текущая Task.

## Результат, повтор и продолжение

Ожидается `preparation.status=ready`, полный receipt с точными Task/project/
worktree/base/configuration/inputs и командами; у всех14или56ресурсов ready.
Прочитайте полный сохранённый ответ/потоки, а не только preview. Готовность среды,
приёмка Task и интеграция — разные результаты.

Обычный replay bootstrap возвращает уже подтверждённый attempt. Для настоящей
новой проверки той же области задайте новый `preparation_request_id`:

```json
{
  "operation": "bootstrap",
  "input": {
    "task": {"id": "ERP-TASK-001"},
    "decision": null,
    "feedback": null,
    "rework_stage": null,
    "preparation_request_id": "erp-task-001-preparation-2"
  },
  "messages": []
}
```

При готовых ресурсах повтор проверяет их состояние. При подтверждённом error
прочитайте конкретную диагностику и исправьте известную причину в рабочей
конфигурации соответствующей области, затем запросите новый attempt.
При unconfirmed сначала установите исход предыдущего действия по
[протоколу согласования](worktree-preparation.md#продолжение-после-отказа).
Внешний `run` обслуживает ресурсы; `repair_operations=[]` этого подключения
не добавляет второй автоматический plugin repair. Неразрешённую ошибку получает
агент. Общий plugin contract допускает одну настроенную попытку с повторной
проверкой; неизвестный результат и повторная ошибка возвращаются агенту.

Для ERP consumer без запуска браузера используйте штатный план:

```bash
bash platform-infrastructure/tools/configured-test-runtime.sh \
  --plan playwright --profile local-development --list
```

Запускайте из полученного child. JSON.executable и JSON.arguments/entryPoint
должны выбирать подготовленный CLI под его `var/**`; `executed=false` означает
только план. Реальное выполнение именно выбранного CLI с `--version` проверяет
его доступность. Готовность56ресурсов и CLI не доказывают запуск полного CJM.

## Сохранение результатов и освобождение

Полные native receipts/снимки/потоки и независимые решения сохраняют своих
истинных владельцев, commits и digests. До завершения передайте требуемые файлы
следующему этапу/получателю и прочитайте постоянные назначения обратно.
Применяется [договор поставки](../workflows/terminal-materials.md#публичный-договор-поставки).
Известный preterminal разрыв и ограниченный вариант продолжения описаны в
[реестре](../operations/known-bugs.md#poise-preterminal-file-delivery-001).

Для принятой completed Task используйте штатный
[integrate](../workflows/batch-work.md#интеграция-принятого-результата)
с отдельным разрешением и точными observed commits. Владелец завершает интеграцию,
убирает только свои worktree/branch и согласует delivery/retirement.
`cleanup` для cancelled Task не заменяет этот completed маршрут.
Проверьте состояние владельцев, физическое освобождение собственных ресурсов,
полные SHA постоянных outputs и сохранение operator файлов/common Git ignore.
Рабочие конфиги — оставляемый результат подключения, а не временная Task копия.
