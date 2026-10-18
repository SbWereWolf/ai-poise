# Подготовка рабочей области

## Назначение и условия

Poise подготавливает выделенную рабочую область Task после создания её ветки и
папки. Внешний инструмент выполняет команды выбранного каталога; подключаемый
обработчик анализирует результат. Последовательность подготовки, входы и команды
задаёт оператор. Подтверждённая готовность окружения отличается от приёмки Task
и интеграции её результата.

Нужны Linux, выбранная поставка Poise с CPython3.15 и объявленными зависимостями,
подготовленный проект с выделенным worktree, доступная базовая Git-ревизия и полный
набор входов. Внешнее приложение устанавливается его пакетным менеджером.
Версию этого приложения выбирает установка, Poise исполняет настроенную команду.
Собственные runtime условия: [лаунчер](../configuration/stride-launcher.md#установка-и-выбор-версии).

Для ERP сначала подготовьте [профиль конкретной задачи](#профиль-конкретной-задачи).
Спецификация и полный состав профиля описаны в [руководстве ERP](maintenance-profile-v2.md#подготовка-erp-профиля).
Нужны каталог из выбранной поставки, заполненные рабочие values, shared environment
и test-execution с согласованными значениями. Проверенный полный профиль содержит
56 ресурсов в21группе, а не только минимальные маркеры готовности. Его действия
подготавливают зависимости и настройки; запуск бизнес-приложений и полный
пользовательский CJM — отдельная проверка, не доказанная одним результатом ready.

## Рабочий файл и подключение проекта

Отслеживаемый пример: [worktree-preparation.example.json](../../config/projects/erp/worktree-preparation.example.json).
Создайте из него рабочий `operator/worktree-preparation.json` внутри выбранной
папки Poise и заполните пути этой установки. Рабочий файл исключён из Git;
сам пример и инструкция отслеживаются. Публикация примера не настраивает
действующую установку автоматически.

Через [владельца конфигурации проекта](../configuration/project-setup.md)
подключите необязательный раздел manifest:

```json
{
  "worktree_preparation": {
    "configuration": "/srv/poise/app/operator/worktree-preparation.json"
  }
}
```

`configuration` — обязательный непустой путь рабочего JSON. Абсолютный путь
используется непосредственно; относительный считается от папки project manifest,
а не от cwd и не от создаваемого worktree. Для выбранного режима проект должен
выделять Task worktree. Обычный bootstrap проекта без этого раздела сохраняет
обычное поведение без подготовки окружения.

Перед запуском задайте достаточный `batch.max_artifact_bytes` через того же владельца
конфигурации. Это предел отдельного сохраняемого материала, включая полный capture
с результатами обработчика, а не только stdout команды. Для проверенного полного
ERP сценария выбран4MiB (`4194304`): capture около1.77MB при суммарных сырых потоках
около778KB. Это измерение конкретного сценария, не универсальный размер и не скрытое
повышение лимита приложением. Полные stdout/stderr сохраняются независимо от preview.

## Профиль конкретной задачи

Профиль Task выбирает её рабочую область, а самостоятельный ERP-профиль — явно
указанный существующий checkout. `repository` сохраняется в values и каталоге;
cwd внешней команды и корень наблюдения обработчика сами его не меняют.
Для каждой Task создайте отдельные profile и maintenance state.

До первого bootstrap установите точный Task ID и фактически настроенный корень
`paths.worktrees` выбранного проекта. Абсолютный корень используется непосредственно,
относительный разрешается от configured state. В текущем контракте путь новой
области равен этому разрешённому корню плюс Task ID. В примере ниже оператор уже
выбрал `/srv/projects/erp/.task-worktrees` в конфигурации проекта; это значение
установки, не универсальный путь. `base_ref` также берётся из выбранного проекта.

```bash
poise_root=/srv/poise/app
poise_python=/srv/poise/poise-venv/bin/python
project_id=erp
task_id=ERP-TASK-001
task_worktrees=/srv/projects/erp/.task-worktrees
task_worktree="$task_worktrees/$task_id"
profiles_root="$poise_root/operator/erp-task-profiles"
task_profile="$profiles_root/$task_id"
maintenance_state="/srv/poise/data/erp-task-maintenance/$task_id"
base_ref=main
mkdir -p "$profiles_root"
PYTHONPATH="$poise_root/src" "$poise_python" -B -m poise_environment.profile \
  --source "$poise_root" \
  --profile-spec "$poise_root/config/projects/erp/profile.example.json" \
  --output "$task_profile" --state "$maintenance_state" \
  --repository "$task_worktree" --project "$project_id" --base-ref "$base_ref" \
  --author-name Operator --author-email operator@example.invalid \
  --module-path "$poise_root/src"
```

Материализатор допускает ещё несуществующий `task_worktree`: он публикует профиль,
но не создаёт и не обслуживает целевой репозиторий. Саму ветку и область создаст
штатный bootstrap. До него заполните `task_profile/values.json` выбранными
инструментами и рабочими границами Python, а `task_profile/documents/*.json` —
настройками по [ERP-инструкции](maintenance-profile-v2.md#параметры-и-корни-erp).
Сохраните `values.repository` равным `task_worktree`, `profile_root` равным
`task_profile`, `state_root` равным отдельному `maintenance_state`.

Четыре назначения `environment_file`, `local_settings_path`,
`host_runtime_settings_path`, `cjm_settings_path` разрешаются от этого repository;
три `*_settings_source` — от этого profile_root. Материализованные относительные
пути сохраняют такое разрешение. Если выбраны абсолютные пути, заполните их
конкретными значениями именно этой области/профиля. Значения values передаются
готовыми строками без повторного рендера.
Projected documents подготовлены для выбранного repository; при смене цели
материализуйте отдельный профиль для новой цели.

В ignored working `operator/worktree-preparation.json` из примера заполните
следующие поля; остальные обязательные parameters, inputs, operations и handler
остаются заполненными по инструкции ниже:

```json
{
  "parameters": {
    "profile": "/srv/poise/app/operator/erp-task-profiles"
  },
  "catalog_dir": "${profile}/${task_id}"
}
```

Здесь `parameters.profile` — буквальный корень профилей, а не вложенный шаблон.
В argv каждой команды используйте `--catalog-dir`, `${catalog_dir}`,
`--values`, `${catalog_dir}/values.json` отдельными элементами:

| commands | Действие внешнего инструмента | Файл после `--values` |
|---|---|---|
| `infra`, `deps`, `repair` | `run` | `${catalog_dir}/values.json` |
| `check`, `verify` | `check` | `${catalog_dir}/values.json` |

Так выбираются и каталог, и values именно текущей Task; `parameters.tool_config`
в этом варианте не используется. Inputs и `handler.options.configuration_root`
продолжают использовать `${worktree}`. Три handler targets должны совпасть с
тремя JSON destinations выбранных values. Профили и state других Task независимы.

После подготовки этих входов выполняйте [обычный bootstrap](#запуск-и-чтение-результата).
Его фактический `worktree` сверяется с выбранным `task_worktree`; квитанция связывает
его с profile/configuration. Все обслуживаемые назначения выбраны в новой области
и её отдельном state, основной checkout остаётся источником Git-базы.

Для уже выделенной Task возьмите путь из её сохранённого bootstrap, а не вычисляйте
новый. Материализуйте отдельный профиль с этим `--repository` и тем же Task ID.
Если прежняя подготовка завершилась подтверждённым error, после исправления
профиля используйте [новый preparation_request_id](#продолжение-после-отказа).
При unconfirmed сначала согласуйте прежний исход по той же инструкции.

## Опции рабочего JSON

| Поле | Значение и разрешение |
|---|---|
| `parameters` | Явный объект строковых параметров установки. В примере: `python`, `profile`, `tool_config`, `shared_environment`, `cjm_execution`. |
| `catalog_dir` | Непустой шаблон пути выбранного каталога. Вычисляется из parameters и координат Task; результат доступен командам как `${catalog_dir}`. Для рабочего каталога задавайте абсолютный путь. |
| `inputs` | Явный список пар `source`/`destination`, допустим пустой. Каждый исходный файл читается, его bytes/digest сохраняются, затем файл записывается в назначение. |
| `inputs[].source` | Шаблон пути входа; относительный результат считается от папки рабочего configuration. |
| `inputs[].destination` | Шаблон пути назначения; относительный результат считается от фактического worktree. |
| `operations` | Непустой упорядоченный список имён из commands. ERP пример выбирает `infra`, `deps`, `check` в этом порядке. |
| `commands` | Объект именованных непустых списков строк argv. Первый элемент — executable; остальные — отдельные аргументы. Процесс запускается без shell, cwd равен worktree. |
| `handler.callable` | Импортируемый `module:callable` обработчика. ERP пример выбирает `apps.bootstrap.erp_result:handle`. |
| `handler.options` | Явный объект опций выбранного обработчика; строковые значения рекурсивно подставляются тем же стандартным шаблонизатором. |

Шаблоны рендерит стандартный `string.Template.substitute`: `$name`, `${name}`,
`$$` для буквального `$`. Один проход заменяет значения, а не исполняет shell
или шаблоны внутри самих подставленных значений. Полностью заполните parameters.
Координаты фактической области имеют приоритет над одноимёнными parameters:

| Placeholder | Источник |
|---|---|
| `${worktree}` | Абсолютная папка выделенной рабочей области. |
| `${task_id}` / `${project_id}` | Действительные идентификаторы Task и проекта. |
| `${branch}` / `${base}` | Выделенная ветка и закреплённая базовая ревизия Task. |
| `${task_root}` | Служебная папка Task, удаляемая после поставки и завершения. |
| `${catalog_dir}` | Вычисленный выбранный каталог для commands и handler options. |

Например, `${python}` передаётся отдельным argv-элементом; путь с пробелами
остаётся одним аргументом. Пути executable/каталога выбирайте явно для установки.
В ERP inputs поставляют shared environment в
`${worktree}/platform-infrastructure/config/dev/local-development.env`
и CJM execution в `${worktree}/platform-infrastructure/test-execution.json`.
Управляемые ресурсы и их значения описаны в [ERP-параметрах](maintenance-profile-v2.md#параметры-и-корни-erp).

## Обработчик и одна попытка исправления

Poise зависит от интерфейса ResultHandler: callable получает исходный
`CompletedProcess[bytes]`, `invoke(name)` для настроенных команд, options и context
с operation/dry_run. Возвращает объект результата с целым `exit_code`;
0 позволяет перейти к следующей операции, ненулевой итог прекращает подготовку.
Другой обработчик подключается через configuration без изменения оркестратора.
Парсинг и решение об исправлении принадлежат выбранному плагину.

Опции поставляемого ERP обработчика:

| Опция | Назначение |
|---|---|
| `schema` | Ожидаемая схема JSON внешнего stdout, в примере `environment-maintenance/result/v2`. |
| `status_exit_codes` | Соответствие внешних статусов фактическим кодам процесса: ready0, action_required2, unavailable3, failed4 в примере. |
| `success_statuses` | Распознанные статусы готовности. |
| `repairable_statuses` | Распознанные статусы с разрешённым исправлением. |
| `repair_operations` | Операции, где разрешена попытка; пример infra/deps. |
| `repair_command` / `verification_command` | Имена commands для единственного исправления и последующей проверки. |
| `failure_exit_code` | Явный ненулевой целый итоговый код ошибки обработчика. |
| `configuration_root` | Корень наблюдения созданных настроек; пример `${worktree}`. |
| `configuration_targets` | Непустой список целей с уникальным id, kind и path; относительный path считается от configuration_root. |

Плагин проверяет полный stdout, UTF-8 потоков, схему и согласованность статуса
с exit code. Для распознанной разрешённой ошибки он вызывает repair один раз,
затем verification и возвращает оба результата. Вторая ошибка возвращается агенту;
повторного автоматического исправления в этом вызове нет. Неизвестный результат
сразу возвращается с диагностикой. Счётчик попыток во внешнем хранилище не нужен:
это линейный поток одного вызова плагина.

ERP плагин дополнительно наблюдает три JSON цели: local-development settings,
host-runtime settings и CJM settings. Для каждой сохраняет id/kind, состояние
present/absent/unavailable и SHA-256 доступных bytes; готовность требует наличия
всех выбранных целей. Их атомарная публикация не доказывает атомарность отдельного
генератора `.env`. Условия и пределы генераторов: [ресурсы ERP](maintenance-profile-v2.md#ресурсы-и-обслуживание-erp).

## Запуск и чтение результата

Передавайте пакет родному session launcher с правильным проектом и caller:

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

```bash
bash "${native_launcher:?Путь выданного session launcher}" < "$packet"
```

После допустимого выделения области bootstrap выполняет preparation. Его ответ
содержит `preparation`: status, attempt_id, request_id, receipt_path,
receipt_sha256, reason. `ready` означает подтверждённую готовность;
`error` — подтверждённую неуспешную попытку с квитанцией;
`unconfirmed` — полный исход ещё не подтверждён, receipt отсутствует.
Для двух последних верхний bootstrap status — action_failed;
task_status показывает сохранённое состояние Task. Рабочая ветка и область сохраняются.
Терминальная Task читается штатно без новой подготовки.

По receipt_path прочитайте полный `poise/worktree-preparation/v1` JSON и проверьте
receipt_sha256. В нём координаты, attempt/request, исходный configuration path,
его снимок/SHA, поставленные inputs и их снимки/SHA, последовательность операций,
результаты обработчика и все реально выполненные команды. У команды сохранены
argv, cwd, returncode и пути/SHA обоих полных сырых потоков. Configuration snapshot
содержит исходные bytes выбранного файла, а не повторное чтение после исполнения.
Служебные материалы принадлежат Task; постоянные результаты поставляются через
[delivery owner](../workflows/terminal-materials.md#публичный-договор-поставки).

## Продолжение после отказа

1. Прочитайте полный сохранённый ответ, причину, квитанцию и оба потока. Сохраните
   Task/worktree и установите, завершена ли прежняя попытка.
2. Для unconfirmed устраните установленную причину публикации и повторите bootstrap
   той же Task с тем же запросом. Poise сначала согласует прежние материалы,
   проверяя координаты, bytes/SHA и действительные регистрации; команды прежней
   попытки для этого повторно не исполняются.
3. После подтверждённого error исправьте рабочие настройки или установленную
   причину. Передайте новый непустой preparation_request_id для отдельной попытки
   в той же области. Прежний результат и его настройки сохраняются; новая
   попытка заново читает актуальный configuration и входы.

Пример третьего шага:

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

Повтор завершённого request возвращает его результат. Bootstrap без явного нового
ID возвращает последнюю попытку. Если прежний исход неопределён, даже новый ID
сначала согласует его; новая внешняя команда допускается только после подтверждения
прежнего завершения. Исправление настроек само по себе новую попытку не запускает.

Task-owned `capture.pending.json` и сырые command sidecars обеспечивают восстановление
полной исполненной попытки после отказа публикации. Они являются источниками
согласования, не публичной квитанцией или успехом. Итоговая регистрация квитанции
и завершение попытки фиксируются одной UoW. При недоступном полном capture либо
исчезнувших/изменённых уже зарегистрированных bytes остаётся честный unconfirmed:
агент получает диагностику и разбирает конкретную причину. Всеобщая автоматическая
починка прерванного процесса или потерянного хранилища этим механизмом не заявлена.

## Повтор принятого набора проверок

Два дополнительных неизменяемых pytest файла поставляются отдельно существующим
Task delivery owner: `acceptance/capture-recovery/test_capture_public_recovery.py`
SHA256 `5f604e045befd6b2ef4f26b000ec79cdb0e3359da957d6573e84cc4576b8eff8`
и `acceptance/stream-preflight/test_stream_evidence_preflight.py`
SHA256 `a6fbec5003ae861de9e33971781237971e9643536b922eb11ee01748fd0fc40c`.
Готовность declaration отличается от фактической поставки: проверьте settled
delivery и bytes в постоянном назначении до удаления Task-root.

Совместимый исходный комплект — commit
`9171517fb983f4804a396d3e7e9707a2d0885e6f` этого репозитория. Он содержит
`tests/conftest.py`, `tests/batch/helpers.py`,
`tests/runtime_services/worktree_preparation_support.py` и
`tests/runtime_services/fixtures/worktree_preparation/` с исходными фикстурами. Выберите checkout
этого commit и установленный Python3.15 с зависимостями выбранной поставки.
`acceptance_root` — постоянная папка двух поставленных подкаталогов, не Task-root.

```bash
cd "$checkout"
PYTHONPATH="$checkout/tests:$checkout/src" "$poise_python" -B -m pytest \
  -p conftest --rootdir "$checkout" -q \
  "$acceptance_root/capture-recovery/test_capture_public_recovery.py" \
  "$acceptance_root/stream-preflight/test_stream_evidence_preflight.py"
```

Ожидаются8 случаев: восстановление полного capture, сохранение неопределённого
исхода при отсутствии полных материалов, публикация обоих потоков до новой команды
и ранний отказ явно неподключённой подготовки до выделения ресурсов. Эта инструкция
не подменяет новый результат исполнения старой квитанцией.
