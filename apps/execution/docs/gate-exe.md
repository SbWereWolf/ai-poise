# Приёмка результатов Execution

## Назначение и границы текущей поставки

Execution принимает содержимое этапа и пути файлов, применяет сохранённые ожидания
и записывает собственное решение. Используются существующие правила секций,
трассировки, покрытия требований и количества файлов. Один кандидат проверяется
целиком: подготовка содержимого, файловые наблюдения, все счётчики этапа и выбранные
условия `pre` и `post` участвуют в итоговой приёмке.

Работайте через [оболочку](shell-exe.md#запуск) схемы `stride-execution/v1`.
Текущие команды — `gate` с `action: declare|evaluate`, чтение
`query/gate_context` и `query/gate_result`. Решение относится к результату этапа.
Состояние Task, текущий этап, generation и iteration в канонической записи исполнения
сохраняются. Попытка команды остаётся наблюдением о команде; её успешный exit сам
по себе не подтверждает приёмку содержимого.

`stage_expectations` сохраняется в исходном контракте setup. Действующая приёмка
получает конкретные правила через сохранённый процесс и явный контекст ниже.
`ValueRule`, `FileRule`, запуск методов, согласование перезапуска, переходы этапов,
физические операции Workspace/Git и terminal outcome из остальных проектных разделов
[целевого контракта](contract-exe.md) относятся к отдельным возможностям.
Действующая явная политика чтения/записи рабочих корней и собственная допустимость
файлового доказательства описаны в [руководстве файловых границ](files-exe.md).

## Настройка и исходные данные

Подготовьте полный конфигурационный файл по [инструкции
оболочки](shell-exe.md#конфигурация-приложения).
Для приёмки добавьте `process` по [инструкции редактора](process-exe.md#явная-конфигурация)
и компонент `gates`:

```json
{"gates":{"roots":{"runtime":"files/runtime","task":"files/task","sprint":"files/sprint"}}}
```

| Параметр | Правильное значение и назначение |
| --- | --- |
| `gates` | Необязательный компонент базовой установки; задаётся для команд приёмки |
| `gates.roots.runtime` | Явный корень файловых результатов runtime |
| `gates.roots.task` | Явный корень файловых результатов Task |
| `gates.roots.sprint` | Явный корень файловых результатов Sprint |

При наличии компонента укажите все три непустых пути. Относительный путь отсчитывается
от `paths.state`, абсолютный используется как задан. Выберите различимые корни.
Для вложенных корней файл относится к самому глубокому содержащему корню.
Неизвестные расширения конфигурации игнорируются и сохраняются в исходном файле.

Создайте и настройте исполнение через
[process-enabled setup](process-exe.md#правильное-создание-изменение-и-настройка-исполнения).
Он сохраняет полный разрешённый процесс и контракт. Из ответа `process/create`
составьте `process_ref` с `goal_type` и `revision: process_revision`, передайте его
в `input.operations[].contract.process_ref` пакета `apply` и сохраните у клиента.
Из результата `setup_execution` используйте
`generation` и `contract_revision`. Execution сам загружает сохранённый снимок
процесса для приёмки. Для контекста передайте принадлежащие исходной Task идентификаторы требований,
её дополнительный слой содержимого и договоры этапов. Сохранённый процесс проверяется
своим структурным владельцем перед использованием. Общая запись JSON-факта и
пригодность факта в качестве полного процесса имеют разные договоры.

Файлы подготовьте под выбранными корнями и передавайте абсолютные пути. Execution
наблюдает существующие обычные файлы. Для контекста без `file_policy` наблюдение содержит scope и
относительный путь;
счётчик использует уникальные пары `(scope, relative_path)` и регистрозависимый
`fnmatchcase` для объявленного pattern. Содержимое файла не участвует в этом
счётчике. При явном `file_policy` gate дополнительно проверяет чтение каждого текущего
файла по правилам выбранного этапа. Рабочие корни и области подсчёта артефактов
различаются; допустимость diff и физические операции имеют своих владельцев.

## Объявление контекста

Верхний пакет содержит ровно `schema`, `operation: "gate"`, `input`.
Для `action: "declare"` поля `input` точные; `file_policy` добавляется при его явном использовании:

| Поля | Значение |
| --- | --- |
| `action`, `request_id`, `expected_revision` | `declare`, новый непустой ID запроса, актуальное неотрицательное целое revision store |
| `task_id`, `generation`, `context_id` | Каноническая Task по настроенной политике ID, её текущее положительное целое поколение, непустой ID контекста |
| `contract_revision` | Редакция собственного контракта из setup |
| `requirement_ids` | Явный список уникальных непустых строк — IDs требований Task |
| `task_content` | Объект ровно `sections`, `routes`, `requirements`, каждое значение — список |
| `stage_contracts` | Явный список договоров всех route stages в порядке сохранённого процесса |
| `file_policy` | Необязательное явное поле; полная [политика рабочих корней](files-exe.md#настройка-и-объявление-политики) |

Пустые группы задавайте явно. Каждый договор этапа содержит ровно `stage_id`,
`allowed_paths`, `entry_requirements`, `exit_requirements`, `criteria`.
`criteria` содержит ровно `requirements`, `definition_of_ready`, `definition_of_done`.
Все шесть списков (`allowed_paths`, два списка refs и три списка criteria) состоят
из уникальных непустых строк и могут быть пустыми. Для read-only этапа задавайте
пустую writable scope. Ссылки entry/exit выбирают из каталога предикатов правила
того же этапа и фазы `pre`/`post`; подмножество задаётся явно.

Дополнительная секция имеет `id`, `template`, `normalization: strip|exact`,
`write_stages`. Trace route имеет `id`, непустой список `requirements` и упорядоченные
`points`; point содержит `id`, `kind: text|method|record`, `fields`, `write_stages`.
Для record задаются поля и списки разрешённых строк; пустой список разрешает
произвольный текст. Для text/method `fields` — пустой объект. Значение method
выбирается из `methods[].id` сохранённого контракта.

Каждое правило имеет `id`, `kind`, `stages`, `phase` и поля своего вида:

| `kind` | Остальные точные поля |
| --- | --- |
| `section` | `section`, непустой список `states` из `empty`, `template`, `populated` |
| `trace` | `route`, `point`, явный объект `field_equals` для полей record |
| `coverage` | Явный список `points`; полнота этих точек покрывает связанные IDs требований Task |
| `artifact` | `scope: runtime|task|sprint`, `pattern`, целые `minimum`, `maximum` с `0 <= minimum <= maximum`, `source` |

`source` — ровно `{kind: preexisting}`, `{kind: stage_output, producer_stage}` либо
`{kind: declared_arrival, arrival_stage}`. Объявляйте выход на exit производителя,
его потребление — после производителя, а прибытие — к доступному потребителю.
Для trace выбирайте writable due stage; требование `pre` имеет write stage строго
раньше первого due stage. Сохраняются существующие проверки достижимости этих
объявлений. Слой процесса и слой Task образуют общий каталог без замены определений.

Ответ `declare` имеет ровно `schema`, `status: "declared"`, `revision`, `replayed`,
`task_id`, `generation`, `context_id`, `changed`; exit `0`. Контекст сохраняет
исходный слой, IDs, stage contracts, ссылки на процесс/контракт/снимок требований,
admission и исходный request ID. Повтор того же содержимого под тем же context ID
с новым request ID даёт `changed: false`. Разные context IDs имеют отдельные истории;
согласование замены рабочего договора относится к владельцу этого согласования.

## Проверка кандидата и сохранённое решение

Для `action: "evaluate"` задавайте точный набор полей:

| Поля | Значение |
| --- | --- |
| `action`, `request_id`, `expected_revision` | `evaluate`, новый ID запроса и актуальная revision store |
| `task_id`, `generation`, `context_id`, `candidate_id` | Существующий собственный контекст и непустой ID нового кандидата в нём |
| `stage_id`, `iteration` | Этап сохранённого процесса и положительная целая iteration |
| `sections` | Точные стандартные текстовые секции этапа и передаваемые объявленные дополнительные секции |
| `trace` | Явный объект `route -> point -> text|record|null` обновлений |
| `files` | Явный список абсолютных путей файловых результатов |

Стандартные секции подготавливаются по `normalization` и `required_sections`
сохранённого этапа; raw text сохраняется. Дополнительные секции и точки изменяются
в их `write_stages`. Обновление upstream trace point очищает изменившуюся downstream
цепочку; прежние кандидаты остаются в истории. Полнота record требует заполнения
всех объявленных полей. Для нового кандидата используется последний подготовленный
снимок именно его Task/generation/context; новый контекст начинается с пустого снимка.

При успешной подготовке сохраняется новый снимок, включая случай последующего
отрицательного gate. При ошибке подготовки сохраняется её причина, прежний
подготовленный снимок остаётся основой следующего кандидата. Каталог файлов
контекста без `file_policy` содержит известные scope/пути и валидные новые наблюдения. Для нового
кандидата они наблюдаются заново; учитываются присутствующие файлы. После переноса
явно настроенного корня известные относительные пути разрешаются от нового корня.

Для `file_policy` каталог хранит абсолютные адреса ранее наблюдавшихся обычных
файлов. Новый подготовленный кандидат заново проверяет существующие известные
и переданные файлы по READ-правилам своего `stage_id`. Предыдущее разрешение другого
этапа его не заменяет. Отсутствующий исторический файл остаётся адресом каталога,
но не текущим доказательством. Переданный отсутствующий путь или каталог получает
отрицательный допуск; для доказательств подавайте существующие обычные файлы.
Подробные правила наблюдения описаны в [файловом
руководстве](files-exe.md#приёмка-файлового-доказательства).

Ответ имеет ровно `schema`, `status: "evaluated"`, `revision`, `replayed`, `task_id`,
`generation`, `context_id`, `candidate_id`, `stage_id`, `iteration`, `accepted`,
`result_id`, `assessment`. Все обязательные условия участвуют в `accepted`:

| Поле `assessment` | Содержимое |
| --- | --- |
| `prepared`, `preparation_error` | Boolean подготовки и её текстовая причина либо null |
| `file_errors` | Массив `{path,reason}` наблюдений непригодных переданных файлов, в порядке входа |
| `artifact_checks` | Все правила этапа: `{scope,pattern,minimum,maximum,actual,passed}` в порядке объявления |
| `pre`, `post` | `{stage,phase,passed,requirements}` либо null при ошибке подготовки |
| `file_admission` | Только при `file_policy`: `{passed,files}`, где каждый файловый допуск содержит `root,path,allowed,reason` |
| `passed` | Подготовка успешна, file_errors пуст, каждый счётчик и обе фазы успешны; при `file_policy` также успешен file_admission |

Элемент фазового `requirements` — `{id,origin,target,passed,detail}`;
`origin` различает слой `goal` и `task`. Пустые выбранные условия дают явный пустой
массив и успешную фазу. При ошибке подготовки file_errors/artifact_checks пусты,
pre/post — null, итог отрицательный. При `file_policy` ошибка подготовки также
даёт `file_admission: {passed: false, files: []}`.

При `accepted: true` exit — `0`, `result_id` — собственный непустой ID, сохранён
`stage_result`. При `accepted: false` exit — `2`, `result_id: null`, сохраняются
кандидат и отрицательное решение с происхождением. Этот ответ имеет `evaluated`.
Отдельный штатный отказ команды имеет ровно `schema`, `status: "rejected"`, `reason`,
exit `2`, пустой stderr и сохраняет исходное бизнес-состояние. Получив результат,
читайте status/accepted и причину, выбирайте следующий корректный запрос согласно
объявленным ожиданиям. Для нового наблюдения используйте новый candidate ID.

## Чтение и повторы

В обычный `query` добавьте точные запросы:

| `kind` | Поля после `id,kind` | Поля элемента результата |
| --- | --- | --- |
| `gate_context` | `task_id,generation,context_id` | `id,kind,context` |
| `gate_result` | `task_id,generation,context_id,candidate_id` | `id,kind,candidate,decision,accepted_result` |

Корень ответа — `{schema,status: "read_only",revision,results}`. Порядок элементов
соответствует запросу; весь batch видит одну revision и один read snapshot.
При запросе существующего контекста с `file_policy` либо наличии `file_result`
в batch корень дополнительно содержит `replayed: false`.
Отсутствующая запись возвращается как null. `query/execution` сохраняет прежний
формат `id,value` и своё правило существующей Task. Сохранённые записи читаются
без повторного наблюдения файлов и загрузки текущего файла процесса.

Каждая собственная запись имеет `{task_id,kind,id,revision,generation,data}`:

| `kind` | Точные поля `data` |
| --- | --- |
| `gate_context` | `context_id,contract_revision,requirement_ids,task_content,stage_contracts,process_ref,requirements_snapshot_revision,admission_id,request_id` |
| `gate_candidate` | `provenance,submitted,snapshot,files,catalogue` |
| `gate_decision` | `provenance,assessment,accepted,result_id` |
| `stage_result` | `provenance,snapshot,files,assessment` |

`provenance` содержит `task_id,generation,context_id,candidate_id,stage_id,iteration,
request_id,contract_revision,process_ref,requirements_snapshot_revision,admission_id`.
`submitted` — `sections,trace,files`; `snapshot` — массив `sections` с элементами
`{name,content,state}` и объект `trace`. `files` и `catalogue` — массивы
`{scope,relative_path}` для контекста без `file_policy`. При явно заданной политике
`gate_context.data` дополнительно содержит `file_policy`; `files` содержит
`{root: {kind,position}|null, path}`, а `catalogue` — `{path}` с абсолютным адресом.
Известный рабочий корень даёт относительный `path` наблюдения; неразрешённый корень
даёт null и абсолютный адрес. Точные причины и диагностические пути описаны
в [файловом руководстве](files-exe.md#приёмка-файлового-доказательства).
Эти четыре вида создаёт приёмка; общий writer по-прежнему
принимает свои [пять видов фактов](store-exe.md#формат-факта).

Контекст и кандидат неизменны в своей полной принадлежности. Для нового запроса
используйте актуальную общую revision; он увеличивает её один раз, включая
сохранённую отрицательную оценку. `request_id` разделяется с остальными мутациями
Execution. Для точного повтора передайте исходный пакет целиком, включая прежнюю
revision: вернётся исходный ответ с `replayed: true`, без новых записей и чтения файлов.

Тот же семантический кандидат с новым request ID возвращает прежнее решение и
result ID, сохраняет исходное происхождение и новый запрос в общем журнале,
увеличивая revision один раз.
Семантическая идентичность включает этап/iteration и исходные sections/trace/files.
Новый candidate ID создаёт новое наблюдение. Для самостоятельных снимков выбирайте
разные context IDs; одинаковые candidate IDs в них относятся к разным результатам.
Запись кандидата, решения, принятого результата, запроса и revision атомарна внутри
собственного store. Последовательность Task и внешние команды имеют своих владельцев.

## Правильный исполняемый пример

Из корня checkout используйте Python 3.13. Выберите существующий отдельный каталог
для временных демонстрационных установок и задайте его явно:

```sh
export EXECUTION_EXAMPLE_ROOT=/absolute/path/to/execution-examples
mkdir -p "$EXECUTION_EXAMPLE_ROOT"
```

Следующий Python-пример выполняется целиком. Он создаёт временную установку под
выбранным каталогом и удаляет её после демонстрации. Все операционные параметры
заданы явно; требование/admission/restart ссылки в примере — входные данные клиента.
Для постоянной установки используйте свои сохранённые конфигурацию и process-enabled
setup из предыдущих разделов, сохраняя данные в выбранном собственном корне.

<!-- gate-demo:start -->
```python
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

provider = Path("apps/execution/bin/execution").resolve()
example_root = Path(os.environ["EXECUTION_EXAMPLE_ROOT"])
with TemporaryDirectory(prefix="execution-example-", dir=example_root) as folder:
    root = Path(folder)
    config = root / "execution.json"
    settings = {
        "schema": "stride-execution-config/v1",
        "paths": {"state": "state", "database": "execution.sqlite", "lock": "execution.lock"},
        "batch": {"max_input_bytes": 65536, "max_items": 256},
        "read": {"max_queries": 256}, "lock": {"seconds": 5.0, "poll_seconds": 0.05},
        "identifiers": {"task": {"pattern": "^[0-9]{4}$"}},
        "process": {"database": "process-editor.sqlite", "lock": "process-editor.lock",
                    "workflows": {"development": "processes/development.json"}, "templates": {},
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
        "goal_type": "development", "worktree_required": False,
        "benefit": {"git_categories": [], "sections": ["result"]},
        "route": {"entry": "implementation"},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
        "stages": [{"id": "implementation", "instruction": "Подготовить результат.",
                    "read_only": False, "allowed_paths": ["outputs/**"], "normalization": "exact",
                    "sections": {"result": ""}, "required_sections": ["result"],
                    "artifact_requirements": [{"scope": "task", "pattern": "outputs/*.txt",
                                               "minimum": 1, "maximum": 1}],
                    "handler": "produce", "role": "executor", "transitions": {"complete": None},
                    "rework_targets": [], "purpose": "execution",
                    "criteria_authority": {"propose": [], "decide": []}}]}
    created = invoke("process", {"action": "create", "request_id": "process-example",
                                "expected_revision": 0, "goal_type": "development",
                                "source": {"kind": "definition", "definition": definition}, "changes": []})
    process_ref = {"goal_type": "development", "revision": created["process_revision"]}
    contract = {"task_id": "0042", "requirements_snapshot_revision": 1, "process_ref": process_ref,
                "admission_id": "admit-example", "workspace_id": None,
                "stage_expectations": [{"stage_id": "implementation", "values": [], "files": [], "methods": []}],
                "restart_policy_id": "restart-example", "methods": [],
                "evidence_plan": {"implementation": {"subject_methods": {}, "arguments": [], "review_arguments": []}}}
    setup = invoke("apply", {"request_id": "setup-example", "expected_revision": created["revision"],
                             "operations": [{"id": "setup", "kind": "setup_execution", "contract": contract}]})
    identity = setup["results"][0]
    criteria = {"requirements": [], "definition_of_ready": [], "definition_of_done": []}
    task_content = {"sections": [], "routes": [], "requirements": [
        {"id": "result-ready", "kind": "section", "section": "result", "states": ["populated"],
         "stages": ["implementation"], "phase": "post"},
        {"id": "output-ready", "kind": "artifact", "scope": "task", "pattern": "outputs/*.txt",
         "minimum": 1, "maximum": 1, "source": {"kind": "stage_output", "producer_stage": "implementation"},
         "stages": ["implementation"], "phase": "post"}]}
    declared = invoke("gate", {"action": "declare", "request_id": "context-example",
                               "expected_revision": setup["revision"], "task_id": "0042",
                               "generation": identity["generation"], "context_id": "context-example",
                               "contract_revision": identity["contract_revision"], "requirement_ids": [],
                               "task_content": task_content, "stage_contracts": [
                                   {"stage_id": "implementation", "allowed_paths": ["outputs/**"],
                                    "entry_requirements": [], "exit_requirements": ["result-ready", "output-ready"],
                                    "criteria": criteria}]})
    output = root / "state/task/outputs/result.txt"
    output.parent.mkdir(parents=True)
    output.write_text("Готовый результат\n", encoding="utf-8")
    candidate = {"action": "evaluate", "request_id": "candidate-example", "expected_revision": declared["revision"],
                 "task_id": "0042", "generation": identity["generation"], "context_id": "context-example",
                 "candidate_id": "candidate-example", "stage_id": "implementation", "iteration": 1,
                 "sections": {"result": "Результат подготовлен."}, "trace": {}, "files": [str(output)]}
    accepted = invoke("gate", candidate)
    assert accepted["accepted"] is True and accepted["assessment"]["passed"] is True
    read = invoke("query", {"queries": [
        {"id": "task", "kind": "execution", "task_id": "0042"},
        {"id": "result", "kind": "gate_result", "task_id": "0042", "generation": identity["generation"],
         "context_id": "context-example", "candidate_id": "candidate-example"}]})
    task, result = read["results"]
    assert task["value"]["state"] == "draft" and task["value"]["current_stage"] is None
    assert task["value"]["generation"] == identity["generation"] and task["value"]["iteration"] == 1
    assert read["revision"] == accepted["revision"]
    saved = result["accepted_result"]
    assert saved["id"] == accepted["result_id"]
    assert saved["data"]["provenance"]["contract_revision"] == identity["contract_revision"]
    assert saved["data"]["provenance"]["process_ref"] == process_ref
    assert saved["data"]["files"] == [{"scope": "task", "relative_path": "outputs/result.txt"}]
    replay = invoke("gate", candidate)
    assert replay == dict(accepted, replayed=True)
    print(json.dumps({"accepted": accepted["accepted"], "replayed": replay["replayed"],
                      "state": task["value"]["state"]}, ensure_ascii=False))
```
<!-- gate-demo:end -->

## Проверки

Пять исполняемых модулей проверяют настоящий CLI, файлы и SQLite:

```sh
python -B -m pytest -q apps/execution/tests/test_gate_context.py apps/execution/tests/test_gate_values.py apps/execution/tests/test_gate_files.py apps/execution/tests/test_gate_decisions.py apps/execution/tests/test_gate_transactions.py
```

В них проверены точные значения/типы/происхождение, разделение всех обязательных
условий, независимые заполненные контексты с одинаковыми candidate IDs, повторы,
сохранённые отрицательные оценки и реальный поздний SQLite-отказ с полным откатом.
Структура сохранённого процесса проверяется через собственный публичный producer
и потребителя gate. Проверки ссылок выполняет существующий инструмент:

```sh
PYTHONPATH=src python -B tools/check_documentation.py --checkout . --changed apps/execution/docs
```
