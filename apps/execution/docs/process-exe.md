# Редактор процессов Execution

## Возможности и владельцы

Execution самостоятельно создаёт процесс из полного определения или выбранного
шаблона, применяет именованные изменения, читает состояние и принимает явно
согласованную отдельную публикацию через `reconcile`. Это выделенные возможности
существующего редактора Poise. Структуру всех этих входов проверяет одна собственная
модель `GoalTypeDefinition`; установка исходного монолита для запуска не требуется.

Точные поля запросов и ответов находятся в
[действующем process-контракте](contract-exe.md#4-processcatalogue--без-нового-planner).
[Оболочка](shell-exe.md#запуск) задаёт общий запуск и обязательную базовую конфигурацию,
[store](store-exe.md#подготовка-собственного-store) — хранение фактов исполнения.
Последующие операции жизненного цикла в целевом контракте имеют отдельный статус
проекта; действующий CLI предоставляет `process`, `apply/setup_execution`, `query/execution`.

## Явная конфигурация

Используйте Python 3.13 и POSIX/Linux, включая Ubuntu в WSL. SQLite и `fcntl`
предоставляет стандартная библиотека. Выберите отдельный корень собственных данных.
За основу возьмите [полный пример](../config/process.example.json). Сохраните рабочую
копию вне исходников, заполните базовые параметры по
[таблице оболочки](shell-exe.md#конфигурация-приложения) и компонент `process`:

```json
{
  "database": "process-editor.sqlite",
  "lock": "process-editor.lock",
  "workflows": {"development": "processes/development.json"},
  "templates": {"development": {"path": "templates/development.json"}},
  "max_changes": 128,
  "file_mode": 420,
  "json_indent": 2
}
```

| Параметр внутри `process` | Тип и назначение |
| --- | --- |
| `database` | Непустой путь к собственной SQLite БД редактора, отдельной от основной БД |
| `lock` | Непустой путь к отдельному файлу доступа редактора |
| `workflows` | Непустой объект: выбранный `goal_type` → путь текущего JSON процесса |
| `templates` | Объект: ID шаблона → объект с обязательным непустым `path`; полный ввод может использовать пустой каталог шаблонов |
| `max_changes` | Положительное целое ограничение числа изменений одного create/edit |
| `file_mode` | Целое от `0` до `511`, POSIX permissions опубликованного файла; `420` соответствует `0644` |
| `json_indent` | Неотрицательное целое: отступ публикуемого JSON |

Все семь параметров задаются явно. Относительные пути компонента разрешаются от
`paths.state`; абсолютные пути используются как заданы. Для файлов текущих процессов,
шаблонов, обеих БД и обоих lock выберите разные пути. Текущий файл содержит
`goal_type`, соответствующий своему ключу `workflows`. Шаблон хранит полное определение;
create назначает ему явно выбранный `goal_type` и сохраняет отдельный текущий файл.

Неизвестные расширения конфигурации игнорируются и остаются в исходных файлах.
Именованное редактирование сохраняет также неизвестные расширения самого процесса;
замороженный снимок Task содержит поддержанную модель. Обязательные известные
параметры и публичные пакеты проверяются строго.

`lock.seconds` и `lock.poll_seconds` базовой конфигурации задают ожидание каждого
собственного ресурса. Операция редактора получает доступ к core, затем к editor;
доступ освобождается после одного вызова. Работа этапов и внешние тесты выполняются
вне этой границы.

## Полное определение и шаблон

Сохраните следующий полный JSON в выбранный файл шаблона
`paths.state/templates/development.json`:

```json
{
  "goal_type": "development",
  "worktree_required": false,
  "benefit": {"git_categories": [], "sections": ["result"]},
  "route": {"entry": "implementation"},
  "content_contract": {"sections": [], "routes": [], "requirements": []},
  "stages": [
    {
      "id": "implementation",
      "instruction": "Подготовить указанный результат.",
      "read_only": false,
      "allowed_paths": ["src/**"],
      "normalization": "exact",
      "sections": {"result": ""},
      "required_sections": ["result"],
      "artifact_requirements": [],
      "handler": "produce",
      "role": "executor",
      "transitions": {"complete": null},
      "rework_targets": [],
      "purpose": "execution",
      "criteria_authority": {"propose": [], "decide": []}
    }
  ]
}
```

Это один допустимый граф, а не обязательная форма рабочего процесса. Модель
проверяет обязательные поля, уникальность и ссылки этапов, вход, достижимость,
путь завершения, виды обработчиков, purpose/criteria authority и content/trace
ссылки. Имена ссылок, включая `route`, `point`, `section` и `stage_id`, передаются
непустыми строками. Роль этапа задаётся явно.

Именованные `put` сначала объявляют объекты, затем применяется остальная часть
пакета и проверяется конечный граф. Поэтому ссылка может предшествовать объявлению
в одном пакете. `patch` меняет перечисленные свойства по ID; `remove` удаляет
выбранный объект. Для одного объекта укажите непересекающиеся изменения.

## Правильное создание, изменение и настройка исполнения

Из корня checkout задайте `EXECUTION_CONFIG` — абсолютный путь к полной рабочей
конфигурации нового собственного store. Сохраните шаблон из предыдущего раздела.
Этот пример создаёт из полного определения, затем изменяет процесс и настраивает
Task. Для другого нового store выберите `source_kind = "template"`: остальная
последовательность та же. `0042`, ссылки на требования/admission и политика
перезапуска — явно переданные значения вызывающей стороны.

```python
import json
import os
from pathlib import Path
import subprocess
import sys

config_path = Path(os.environ["EXECUTION_CONFIG"])
provider = Path("apps/execution/bin/execution").resolve()
configuration = json.loads(config_path.read_text())
state = Path(configuration["paths"]["state"])
if not state.is_absolute():
    state = config_path.parent / state
source_kind = "definition"
template_path = Path(configuration["process"]["templates"]["development"]["path"])
if not template_path.is_absolute():
    template_path = state / template_path
definition = json.loads(template_path.read_text())


def call(value):
    packet = {"schema": "stride-execution/v1", "operation": "process", "input": value}
    return invoke(packet)


def invoke(packet):
    run = subprocess.run([sys.executable, "-B", str(provider), "--config", str(config_path)],
                         input=json.dumps(packet) + "\n", text=True, capture_output=True, check=True)
    assert run.stderr == ""
    return json.loads(run.stdout)


source = ({"kind": "definition", "definition": definition} if source_kind == "definition"
          else {"kind": "template", "template": {"id": "development"}})
create_input = {"action": "create", "request_id": "create-demo", "expected_revision": 0,
                "goal_type": "development", "source": source, "changes": []}
created = call(create_input)
contract = {"task_id": "0042", "requirements_snapshot_revision": 1,
            "process_ref": {"goal_type": "development", "revision": created["process_revision"]},
            "admission_id": "admit-0042", "workspace_id": None,
            "stage_expectations": [{"stage_id": "implementation", "values": [], "files": [], "methods": []}],
            "restart_policy_id": "restart-v1", "methods": [],
            "evidence_plan": {"implementation": {"subject_methods": {}, "arguments": [], "review_arguments": []}}}
setup = invoke({"schema": "stride-execution/v1", "operation": "apply", "input": {
    "request_id": "setup-demo", "expected_revision": created["revision"],
    "operations": [{"id": "setup", "kind": "setup_execution", "contract": contract}]}})
observed = call({"action": "status", "goal_type": "development"})
assert observed["revision"] == setup["revision"]
edited = call({"action": "edit", "request_id": "edit-demo",
               "expected_revision": observed["revision"], "goal_type": "development",
               "expected_process_revision": observed["live_revision"], "changes": [
                   {"op": "patch_stage", "id": "implementation",
                    "set": {"instruction": "Подготовить уточнённый результат."}}]})
replay = call(create_input)
assert replay == dict(created, replayed=True)
print(json.dumps({"created": created, "setup": setup, "edited": edited, "replay": replay}, ensure_ascii=False))
```

`revision` — целая общая редакция store: её используют process, setup и запись
фактов. `process_revision` — возвращённый идентификатор содержания поддержанного
процесса. Передавайте полученные значения; вычислять файловый хэш для запроса
не требуется. Даже пустой edit с новым request увеличивает owner revision один раз;
при неизменном содержании `changed` равно `false`, process revision сохраняется.

Process-enabled setup сохраняет сначала `contract_snapshot`, затем
`process_snapshot` с `data = {process_ref, process}` в той же транзакции Task/generation.
Читайте их через `Store.read_records("0042")`. В примере Task сохраняет первоначальный
процесс, а edit меняет только текущий файл. Для следующей Task передайте новую
возвращённую process revision. `workspace_id: null` соответствует процессу с
`worktree_required: false`; для процесса, требующего Workspace, передайте его ID.
Ожидания этапов и ключи evidence plan адресуют этапы выбранного графа.

Для exact replay отправляйте тот же исходный пакет с прежним `request_id` и
`expected_revision`. Сохранённый ответ возвращается до нового чтения текущего
процесса/шаблона, с `replayed: true`. Новому действию назначайте новый request ID
и актуальную целую revision. Изменение выбранного шаблона сохраняет ранее созданные
текущие процессы и снимки Task.

После process-enabled setup используйте возвращённые `contract_revision`,
`generation`, `process_ref` и сохранённый полный процесс для
[объявления контекста и приёмки результата](gate-exe.md#объявление-контекста).
Дополнительный компонент [gates.roots](gate-exe.md#настройка-и-исходные-данные)
задаёт три собственных корня файлов. Рабочий контекст получает слой Task, IDs
требований и точные stage contracts явно; приёмка использует замороженный снимок
и сохраняет собственное происхождение решения.

## Явная сверка отдельной публикации

Для согласованного изменения текущего файла отдельным инструментом сохраняйте
полную валидную структуру и выбранный `goal_type`. Прочитайте `status`, возьмите
его `revision`, `managed_revision`, `live_revision`. Добавьте следующий фрагмент
к предыдущему Python-примеру после согласованной отдельной публикации:

```python
observed = call({"action": "status", "goal_type": "development"})
adopted = call({"action": "reconcile", "request_id": "adopt-demo",
                "expected_revision": observed["revision"], "goal_type": "development",
                "expected_managed_revision": observed["managed_revision"],
                "expected_live_revision": observed["live_revision"],
                "authorization_id": "accepted-publication", "reason": "Принята согласованная публикация процесса."})
print(json.dumps(adopted, ensure_ascii=False))
```

`reconcile` принимает актуальную редакцию в head и журнал повторов, увеличивает
owner revision и сохраняет байты текущего файла. `authorization_id` и `reason`
передаются как непустые текстовые значения; редактор проверяет их форму и связывает
весь запрос с сохранённым request digest. Полное решение, эти значения и исходный
пакет сохраняет вызывающая сторона или оператор: из записей редактора их исходный
текст восстановить нельзя. Проверка прав/ролей по внешнему resolver остаётся частью
целевого lifecycle.

## Публикация и продолжение

Редактор сначала фиксирует собственный `pending` candidate, затем публикует файл
через временный файл и atomic replace, после чего в одной обычной attached SQLite
транзакции завершает editor head/intent и core revision/receipt. При обычной ошибке
файловой записи или SQL-оператора финализации до COMMIT возвращается `pending`, exit `3`,
`phase: "process_publication"` и тот же `intent_id`. Завершённый результат имеет exit `0`.

Восстановите условие записи внешним инструментом и **сразу повторите исходный пакет
целиком до любых других изменений**. Повтор использует сохранённый candidate и
завершает его один раз, возвращая `replayed: true`. Третий точный повтор читает
сохранённый результат. `status` показывает `pending_request`, managed/live revision;
при первоначальном создании читайте его после появления текущего файла.

Поддержаны обычные режимы SQLite DELETE и WAL с сохранением выбранного режима.
Проверенный откат относится к SQL-ошибке до COMMIT при работающем узле; SQLite и
файловая публикация имеют отдельные границы. Успех возвращается после завершения
COMMIT. После неизвестного исхода COMMIT или прерывания узла оператор отдельно
проверяет данные и выполняет необходимое восстановление внешним инструментом.
Условия межфайловой атомарности описаны в
[SQLite ATTACH](https://www.sqlite.org/lang_attach.html#details).

Обычный структурный отказ имеет exit `2`, `status: "rejected"`, непустой `reason`
и пустой stderr. Уточните указанный вход и актуальную редакцию, затем передайте
новый корректный пакет с новым request ID. Уже завершённую операцию повторяют
точным исходным пакетом.

## Проверки

Адресные проверки действующего редактора:

```sh
python -B -m pytest -q \
  apps/execution/tests/test_process_definition.py \
  apps/execution/tests/test_process_publication.py \
  apps/execution/tests/test_process_materialization.py \
  apps/execution/tests/test_process_inputs.py \
  apps/execution/tests/test_process_input_shapes.py \
  apps/execution/tests/test_process_storage_upgrade.py \
  apps/execution/tests/test_process_finalization.py \
  apps/execution/tests/test_process_regressions.py \
  apps/execution/tests/test_process_trace_references.py
```

Они проверяют реальные файлы, SQL-записи, значения/типы и публичный CLI, включая
pending/retry и сохранение снимков. Существующий анализатор границ подтверждает
отсутствие конкретных нарушений Execution в проверяемом срезе; полнота его статического
анализа ограничена. [Проверки store и ссылок](store-exe.md#проверки) дополняют этот набор.
