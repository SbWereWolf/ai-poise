# Файловые границы этапа Execution

## Назначение

Execution проверяет допустимость объявленного чтения или записи по правилам
выбранного этапа и сохраняет собственное решение. При приёмке файлового
доказательства gate дополнительно наблюдает существующий обычный файл и проверяет
его чтение. Используйте [оболочку](shell-exe.md#запуск) `stride-execution/v1`.

Физическими рабочими корнями владеет Sequencing: его
[подготовка Workspace](../../sequencing/docs/contract-seq.md#60-реализованная-подготовка)
возвращает `workspace_id` и корни с `kind`, `position`, `path`. Вызывающая сторона
передаёт эти публичные факты в Execution. Правила относятся к выбранному этапу,
роль берётся из сохранённого процесса. Дополнительная роль процесса используется
на тех же условиях. Этап `integration` — обычный этап: запись в основном checkout
проверяется по его явно заданному правилу, как запись любого другого этапа.

Проверка пути описывает допуск операции. Фактическое чтение, запись, Git merge
и продвижение Task выполняются через соответствующих владельцев. Сохранённое
решение и принятый файл сами по себе сохраняют каноническое состояние исполнения,
его текущий этап, generation и iteration.

## Настройка и объявление политики

Подготовьте Python 3.13 и собственную установку по
[конфигурации оболочки](shell-exe.md#конфигурация-приложения), включив `process`
и `gates`. Создайте процесс и выполните
[process-enabled setup](process-exe.md#правильное-создание-изменение-и-настройка-исполнения).
Составьте `process_ref` из `process/create`: `goal_type` и
`revision: process_revision`. Передайте его в setup и сохраните у клиента.
Ответ setup выдаёт `generation` и `contract_revision`. Ссылки на требования,
admission и Workspace задаёт вызывающая сторона из результатов их владельцев.

Добавьте к [объявлению gate](gate-exe.md#объявление-контекста) явное поле
`file_policy`. Оно содержит ровно следующие поля:

| Поле | Значение |
| --- | --- |
| `workspace_id` | Непустая строка, совпадающая с Workspace в каноническом setup |
| `roots` | Непустой список объектов ровно `{kind,position,path}` |
| `stages` | Список объектов ровно `{stage_id,roots}` для всех этапов сохранённого процесса |

`kind` — непустая строка вида корня, `position` — неотрицательное целое.
Пара `(kind,position)` однозначно выбирает корень; `path` — абсолютный путь его
каталога. Передавайте каждую пару один раз. Для наблюдения файла выбирайте корни,
дающие единственный самый глубокий содержащий корень.

Каждый этап объявляется один раз и содержит правила для каждого объявленного корня.
Правило имеет ровно `kind`, `position`, `read`, `write`. Каждая операция содержит
ровно `allow`, `deny`: явные списки уникальных непустых строк шаблонов; пустые
списки допустимы. Имя этапа соответствует сохранённому процессу.

Для проверки используется регистрозависимый `fnmatchcase` по относительному пути;
шаблон, оканчивающийся на `/**`, также покрывает сам его префикс. Допуск требует
совпадения с `allow` и отсутствия совпадения с `deny`. Пустой `allow` даёт
отрицательное решение. `read_only` сохранённого этапа даёт отрицательное решение
записи. Результат определяется отдельным правилом выбранной операции и корня.
Политика задаётся полностью; существующий `stage_contracts.allowed_paths` сохраняет
свой договор и автоматически в `file_policy` не преобразуется.

Контекст сохраняет политику вместе с договором и снимком процесса. Для другой
политики объявляйте новый контекст в рамках согласованного договора. Контекст без
этого поля продолжает использовать [приёмку областей артефактов](gate-exe.md).

## Проверка чтения и записи

Верхний пакет содержит ровно `schema: "stride-execution/v1"`, `operation: "files"`,
`input`. Для `input` задайте все поля:

| Поле | Значение |
| --- | --- |
| `action` | `check` |
| `request_id`, `expected_revision` | Новый непустой ID и актуальная неотрицательная целая revision store |
| `task_id`, `generation`, `context_id` | Каноническая Task, её положительное поколение и объявленный контекст с политикой |
| `stage_id`, `mode` | Этап сохранённого процесса, `read` либо `write` |
| `paths` | Явный список объектов ровно `{root: {kind,position}, path}` |

Путь в `paths` задавайте канонически относительно выбранного корня: непустые
компоненты разделены `/`, каждый обозначает имя внутри корня. Используйте
обычные относительные имена, например `src/result.py`. Проверка scope применяет
правило к имени; существование файла для этой операции не требуется.

Ответ содержит ровно `schema`, `status: "checked"`, `revision`, `replayed`,
`task_id`, `generation`, `context_id`, `stage_id`, `mode`, `role`, `allowed`,
`decisions`, `result_id`. Порядок `decisions` совпадает с `paths`; элемент имеет
ровно `root`, `path`, `allowed`, `reason`. `allowed` всего ответа означает допуск
всех элементов; пустой список даёт true. `result_id` — непрозрачный ID собственной
сохранённой записи. Положительное решение имеет exit `0`, отрицательное — exit `2`.

| `reason` | Смысл отрицательного решения scope |
| --- | --- |
| `read_only` | Выбранный этап сохраняет режим чтения при запросе записи |
| `denied` | Путь совпал с исключением выбранной операции |
| `outside_allow` | Путь находится вне разрешающих шаблонов выбранной операции |
| null | Операция по этому пути разрешена |

Структурный отказ возвращает `status: "rejected"`, `reason`, exit `2` и сохраняет
исходное бизнес-состояние. Для следующего запроса используйте исправленные явные
данные и актуальную revision. Отрицательный `checked` — полноценное сохранённое
решение, которое читается и повторяется на общих условиях.

## Сохранённое решение и чтение

`files/check` записывает `file_decision` в собственное хранилище в одной транзакции
с request и общей revision. Запись имеет ровно
`{task_id,kind,id,revision,generation,data}`. Revision этого неизменного факта — `1`.
`data` содержит ровно `context_id`, `request_id`, `stage_id`, `mode`, `role`,
`allowed`, `decisions`, `contract_revision`, `process_ref`, `workspace_id`.
Ссылки соответствуют собственному сохранённому договору. Общий writer сохраняет
[пять прежних видов фактов](store-exe.md#формат-факта); файловое решение создаёт
его отдельный producer.

Для чтения включите в `query.input.queries` объект ровно:

```json
{"id":"file","kind":"file_result","task_id":"0042","generation":1,"context_id":"files-example","request_id":"read-example"}
```

Элемент ответа содержит ровно `id`, `kind`, `result`; `result` — собственный
`file_decision` либо null. Выборка ограничена Task/generation/context/request.
В batch с `file_result` или gate-запросом к существующему контексту с `file_policy`
корень ответа имеет `{schema,status: "read_only",revision,replayed: false,results}`.
Остальные query сохраняют прежние четыре поля корня. Все элементы видят один
read snapshot. Чтение сохранённого результата сохраняет его исходное наблюдение.

Для точного повтора мутации передайте прежний пакет целиком, включая старую
`expected_revision`. Возвращается исходный ответ с `replayed: true`, без новых
записей и наблюдения файлов. Новая операция получает новый request ID и актуальную
revision. Другая Task или контекст сохраняет отдельную историю решений.

## Приёмка файлового доказательства

Передайте в `gate/evaluate.files` абсолютные пути существующих обычных файлов.
Остальные поля и правила содержимого сохраняют [договор
gate](gate-exe.md#проверка-кандидата-и-сохранённое-решение).
Для каждого нового подготовленного кандидата Execution наблюдает переданные файлы
и известные адреса этого контекста, затем применяет READ-правила выбранного этапа.
Предыдущее разрешение другого этапа используется только как исторический результат.

`assessment.file_admission` содержит ровно `passed`, `files`; элементы `files`
имеют `root`, `path`, `allowed`, `reason`. Для обычного файла в однозначном рабочем
корне путь относительный, корень — `{kind,position}`. Вне такого корня `root: null`,
путь абсолютный, причина `outside_roots`; это также результат неоднозначного самого
глубокого корня. У переданного отсутствующего файла внутри известного корня причина
`missing_file`, у каталога — `not_regular_file`. Ошибка наблюдения внутри корня
передаётся как `unavailable_file: ...`. Диагностический относительный путь для самого
каталога корня равен `.`; это представление отрицательного наблюдения, тогда как
вход `files/check` задаётся каноническими именами файлов внутри корня.

Рабочие корни `file_policy.roots` и области артефактов `gates.roots` имеют разные
назначения. Счётчики по runtime/task/sprint наблюдают реальный файл в своей области
независимо от допуска чтения этапом. Файл кода может получить допустимость чтения,
имея нулевой вклад в эти счётчики. Все условия gate участвуют совместно: содержимое,
файловые ошибки, счётчики, `pre`, `post` и `file_admission`.

Подготовленный кандидат, включая отрицательное решение, сохраняет содержимое,
наблюдения и каталог. Каталог содержит `{path}` с абсолютными адресами реальных
обычных файлов; наблюдения — `{root,path}`. Отсутствующий исторический адрес остаётся
в каталоге, но исключается из текущего доказательства; его появление наблюдается
при новом кандидате. Новые отсутствующие файлы и каталоги адресами каталога не
становятся. Ошибка подготовки содержимого сохраняет прежний подготовленный снимок
и даёт `file_admission: {passed: false, files: []}`.

Приёмка проверяет вид файла и допустимость чтения. Содержимое файла и его хэш
не являются входами этого решения; результат команды или квитанция Poise также
не требуются. Для нового наблюдения используйте новый candidate ID и request ID.
Сохранённый точный повтор остаётся историческим ответом, даже когда файл изменён
или отсутствует. Собственное решение и происхождение доступны через
`query/gate_result`; `stage_result` создаётся при успешной совместной приёмке.

## Правильный исполняемый пример

Из корня checkout выберите Python 3.13 и задайте каталог временных установок:

```sh
export EXECUTION_EXAMPLE_ROOT=/absolute/path/to/execution-examples
mkdir -p "$EXECUTION_EXAMPLE_ROOT"
```

Выполните Python-блок целиком. Он создаёт отдельную временную установку и удаляет
только её. Ссылки requirements/admission/Workspace/restart — явные входы клиента
для этой самостоятельной демонстрации. В постоянной установке используйте результаты
соответствующих владельцев. Пример выполняет разрешённое чтение кода, разрешённую
запись main на этапе интеграции, приёмку файла, собственное чтение и точный повтор.

<!-- file-demo:start -->
```python
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

provider = Path("apps/execution/bin/execution").resolve()
example_root = Path(os.environ["EXECUTION_EXAMPLE_ROOT"])
with TemporaryDirectory(prefix="execution-files-example-", dir=example_root) as folder:
    root = Path(folder)
    code, main = root / "code", root / "main"
    code.mkdir()
    main.mkdir()
    output = code / "src/result.py"
    output.parent.mkdir()
    output.write_text("result = 42\n", encoding="utf-8")
    config = root / "execution.json"
    settings = {
        "schema": "stride-execution-config/v1",
        "paths": {"state": "state", "database": "execution.sqlite", "lock": "execution.lock"},
        "batch": {"max_input_bytes": 65536, "max_items": 256},
        "read": {"max_queries": 256}, "lock": {"seconds": 5.0, "poll_seconds": 0.05},
        "identifiers": {"task": {"pattern": "^[0-9]{4}$"}},
        "process": {"database": "process-editor.sqlite", "lock": "process-editor.lock",
                    "workflows": {"file_demo": "processes/file_demo.json"}, "templates": {},
                    "max_changes": 128, "file_mode": 420, "json_indent": 2},
        "gates": {"roots": {"runtime": "runtime", "task": "task", "sprint": "sprint"}}}
    config.write_text(json.dumps(settings), encoding="utf-8")

    def invoke(operation, value):
        run = subprocess.run([sys.executable, "-B", str(provider), "--config", str(config)],
                             input=json.dumps({"schema": "stride-execution/v1", "operation": operation,
                                               "input": value}) + "\n",
                             text=True, capture_output=True, check=True)
        assert run.stderr == "" and run.stdout.count("\n") == 1
        return json.loads(run.stdout)

    definition = {
        "goal_type": "file_demo", "worktree_required": False,
        "benefit": {"git_categories": [], "sections": ["report"]},
        "route": {"entry": "build"},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "stages": [
            {"id": sid, "instruction": "Подготовить результат.",
             "read_only": False, "allowed_paths": ["src/**"], "normalization": "strip",
             "sections": {"report": ""}, "required_sections": ["report"],
             "artifact_requirements": [], "handler": "produce", "role": role,
             "transitions": {"complete": target}, "rework_targets": [sid], "purpose": "execution",
             "criteria_authority": {"propose": [], "decide": []}}
            for sid, role, target in [("build", "author", "integration"),
                                      ("integration", "integrator", None)]]}
    created = invoke("process", {"action": "create", "request_id": "process-example",
                                "expected_revision": 0, "goal_type": "file_demo",
                                "source": {"kind": "definition", "definition": definition}, "changes": []})
    process_ref = {"goal_type": "file_demo", "revision": created["process_revision"]}
    stages = ["build", "integration"]
    contract = {
        "task_id": "0042", "requirements_snapshot_revision": 1, "process_ref": process_ref,
        "admission_id": "admit-example", "workspace_id": "workspace-example",
        "stage_expectations": [{"stage_id": sid, "values": [], "files": [], "methods": []} for sid in stages],
        "restart_policy_id": "restart-example", "methods": [],
        "evidence_plan": {sid: {"subject_methods": {}, "arguments": [], "review_arguments": []} for sid in stages}}
    setup = invoke("apply", {"request_id": "setup-example", "expected_revision": created["revision"],
                             "operations": [{"id": "setup", "kind": "setup_execution", "contract": contract}]})
    identity = setup["results"][0]
    policy = {
        "workspace_id": "workspace-example",
        "roots": [{"kind": "code", "position": 0, "path": str(code)},
                  {"kind": "main", "position": 0, "path": str(main)}],
        "stages": [
            {"stage_id": sid, "roots": [
                {"kind": "code", "position": 0, "read": {"allow": ["src/**"], "deny": []},
                 "write": {"allow": ["src/**"], "deny": []}},
                {"kind": "main", "position": 0, "read": {"allow": ["src/**"], "deny": []},
                 "write": {"allow": ["src/**"] if sid == "integration" else [], "deny": []}}]}
            for sid in stages]}
    declared = invoke("gate", {
        "action": "declare", "request_id": "context-example", "expected_revision": setup["revision"],
        "task_id": "0042", "generation": identity["generation"], "context_id": "files-example",
        "contract_revision": identity["contract_revision"], "requirement_ids": ["R1"],
        "task_content": {"sections": [], "routes": [], "requirements": []},
        "stage_contracts": [{"stage_id": sid, "allowed_paths": ["src/**"],
                             "entry_requirements": [], "exit_requirements": [],
                             "criteria": {"requirements": [], "definition_of_ready": [], "definition_of_done": []}}
                            for sid in stages], "file_policy": policy})
    read_scope = invoke("files", {
        "action": "check", "request_id": "read-example", "expected_revision": declared["revision"],
        "task_id": "0042", "generation": identity["generation"], "context_id": "files-example",
        "stage_id": "build", "mode": "read",
        "paths": [{"root": {"kind": "code", "position": 0}, "path": "src/result.py"}]})
    write_scope = invoke("files", {
        "action": "check", "request_id": "write-example", "expected_revision": read_scope["revision"],
        "task_id": "0042", "generation": identity["generation"], "context_id": "files-example",
        "stage_id": "integration", "mode": "write",
        "paths": [{"root": {"kind": "main", "position": 0}, "path": "src/result.py"}]})
    assert read_scope["allowed"] is True and read_scope["role"] == "author"
    assert write_scope["allowed"] is True and write_scope["role"] == "integrator"
    candidate = {
        "action": "evaluate", "request_id": "candidate-example", "expected_revision": write_scope["revision"],
        "task_id": "0042", "generation": identity["generation"], "context_id": "files-example",
        "candidate_id": "candidate-example", "stage_id": "build", "iteration": 1,
        "sections": {"report": "Результат подготовлен."}, "trace": {}, "files": [str(output)]}
    accepted = invoke("gate", candidate)
    assert accepted["accepted"] is True
    assert accepted["assessment"]["file_admission"] == {
        "passed": True, "files": [{"root": {"kind": "code", "position": 0},
                                  "path": "src/result.py", "allowed": True, "reason": None}]}
    read = invoke("query", {"queries": [
        {"id": "file", "kind": "file_result", "task_id": "0042", "generation": identity["generation"],
         "context_id": "files-example", "request_id": "read-example"},
        {"id": "gate", "kind": "gate_result", "task_id": "0042", "generation": identity["generation"],
         "context_id": "files-example", "candidate_id": "candidate-example"},
        {"id": "task", "kind": "execution", "task_id": "0042"}]})
    file, gate, task = read["results"]
    assert read["replayed"] is False and read["revision"] == accepted["revision"]
    assert file["result"]["id"] == read_scope["result_id"]
    assert file["result"]["kind"] == "file_decision"
    assert file["result"]["data"]["decisions"] == read_scope["decisions"]
    assert file["result"]["data"]["process_ref"] == process_ref
    assert gate["accepted_result"]["id"] == accepted["result_id"]
    assert gate["accepted_result"]["data"]["files"] == [
        {"root": {"kind": "code", "position": 0}, "path": "src/result.py"}]
    assert task["value"]["state"] == "draft" and task["value"]["current_stage"] is None
    assert task["value"]["generation"] == identity["generation"] and task["value"]["iteration"] == 1
    replay = invoke("gate", candidate)
    assert replay == dict(accepted, replayed=True)
    print(json.dumps({"read_allowed": read_scope["allowed"], "main_write_allowed": write_scope["allowed"],
                      "accepted": accepted["accepted"], "replayed": replay["replayed"]}))
```
<!-- file-demo:end -->

## Проверки

Из корня checkout выполните проверки настоящего CLI, собственного store и файлов:

```sh
python -B -m pytest -q apps/execution/tests/test_file_scope.py apps/execution/tests/test_file_admission.py
```

Проверка scope и наблюдение доказательства имеют отдельные независимые ожидания.
Пример выше исполняет фактически документированную последовательность без импорта
тестов и фикстур. Ссылки проверяются существующим инструментом:

```sh
PYTHONPATH=src python -B tools/check_documentation.py --checkout . --changed apps/execution/docs
```
