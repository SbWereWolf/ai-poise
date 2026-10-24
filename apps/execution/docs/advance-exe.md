# Продвижение по актуальным доказательствам

## Назначение и подготовка

Execution предоставляет `advance/next`, `advance/fast_forward` и
`query/progression`. `next` принимает выполненный результат текущего этапа;
`fast_forward` после restart проходит ранее принятые этапы по свежим проверкам,
сохраняя исходных исполнителей и решения проверяющих. Роль инициатора перемотки
может отличаться от роли пройденного этапа. Новую работу выполняет участник
с ролью, указанной в процессе.

Используйте Python 3.13 и Linux, явную конфигурацию и существующий CLI
`apps/execution/bin/execution --config`. Настройки принадлежат
[оболочке](shell-exe.md#конфигурация-приложения): настройте собственный store,
процесс и отображение участников `lifecycle.actors`. Дополнительных настроек
продвижения нет. Идентификатор задачи и снимок требований приходят из Requirements,
размещение и исходное состояние Git — от Workspace; вызывающий явно передаёт
результаты между приложениями.

1. Опубликуйте процесс и выполните `apply/setup_execution` по
   [руководству хранилища](store-exe.md). Заморозьте договор через `lifecycle/freeze`
   и войдите в начальный этап через `lifecycle/enter`.
2. Подготовьте текущий контекст и успешную оценку кандидата через
   [Gate](gate-exe.md), представьте именно этот результат через `lifecycle/present`.
3. Выполните объявленные проверки фактической командой, сохраните полные наблюдения
   и обоснование через публичный `Store.write_records`. Независимый проверяющий
   выполняет предусмотренный процессом осмотр и сохраняет своё решение.
4. Прочитайте актуальные execution/lifecycle и отправьте `advance/next` с текущими
   ссылками. Следующий участник начинает работу на возвращённом этапе по его роли.

После согласованного [restart](lifecycle-exe.md) снова выполните freeze/enter.
Для каждого проходимого этапа подготовьте свежие Gate и evidence в текущем
поколении, с соответствующей итерацией и актуальными проверками. Передайте эти
ссылки в `fast_forward`. Исходные принятые аргументы и решения осмотра берутся
из сохранённой истории; свежие наблюдения подтверждают их применимость сейчас.

## Пакет продвижения

Корень ровно `{schema,operation,input}`, где `schema: "stride-execution/v1"`,
`operation: "advance"`. Общие поля `input`:

| Поле | Источник и значение |
| --- | --- |
| `action` | `next` или `fast_forward`. |
| `request_id` | Стабильный идентификатор одной операции вызывающего. |
| `expected_revision` | Текущая revision собственного store, целое от нуля. |
| `task_id` | Канонический идентификатор задачи Requirements. |
| `expected_generation` | Текущее поколение execution, целое от единицы. |
| `expected_contract_revision` | Текущая revision замороженного договора execution. |
| `actor_id` | Реальный участник, объявленный в `lifecycle.actors`. |
| `source` | Ровно `{commit,tree}` актуального исходного состояния от владельца Git. |
| `entry_tree` | Дерево текущего входа из `query/lifecycle.result.entry_tree`. |

Для `next` дополнительно требуется ровно `proof` с полями
`{context_id,candidate_id,outcome,evidence_id}`. Контекст и кандидат принадлежат
фактически представленному текущему Gate; `outcome` — объявленный процессом
результат, подтверждённый оценкой доказательств; `evidence_id` — текущая запись
своего store. Execution должен находиться в `verified`; роль участника совпадает
с ролью текущего этапа.

Для `fast_forward` дополнительно требуются `target_stage` и `proofs`.
`target_stage: null` означает продвижение до первой требующей работы позиции
либо конца маршрута. Явный идентификатор обозначает достижимую границу остановки;
текущий этап допустим и даёт остановку без переходов.
`proofs` — массив уникальных по `stage_id` объектов ровно
`{stage_id,context_id,candidate_id,evidence_id}`. Каждый относится к свежему Gate
и evidence этого этапа в текущем поколении. Состояние допускает `active` и
`verified`; историческое исполнение этапа сохраняет исходных участников.

Все идентификаторы и source-поля — непустые строки. Версии читаются у владельца,
а source передаётся как результат наблюдения Git. Это привязка проверки
к проверенному коду; отдельные хэши содержимого конфигурации не нужны.

## Факты проверки и обоснование

Общий формат записи и операции чтения/записи принадлежат
[Store](store-exe.md#формат-факта). Для продвижения нужны две записи своего
`task_id` и текущего `generation`: `kind: attempt` и `kind: evidence`.
Сохраняйте их одним публичным `write_records` с текущей `expected_revision`.
Запись произвольного факта в store сама по себе ещё не означает приёмку этапа.

| Запись | Обязательные поля `data` |
| --- | --- |
| `attempt` | `status`, `stage_id`, `iteration`, `context_id`, `candidate_id`, `source`, `methods`, `execution_key`, `receipts`. |
| `evidence` | `attempt_id`, `evidence_work`. |

`attempt_id` ссылается на свою попытку того же поколения; `status: "completed"`.
Этап, итерация, контекст и кандидат совпадают с проверяемым Gate. `source`
совпадает с текущим пакетом продвижения. `methods` содержит полные определения
объявленных для этапа методов: выберите их из `contract.methods` по
`stage_expectations.methods`, сохранив порядок `contract.methods`.
`execution_key` обозначает реальное выполнение, `receipts` содержит его наблюдения.
`Store.read_records(task_id)` читает записи, `Store.read_attempt(task_id,attempt_id)`
читает попытку; собственные revision берутся из актуальных ответов.

Каждое наблюдение содержит следующие поля:

| Поля | Значение |
| --- | --- |
| `id`, `method`, `obligations` | Идентификатор наблюдения, объявленный метод и список ровно `[method]`. |
| `argv`, `cwd`, `environment` | Фактический запуск, совпадающий с объявлением метода. |
| `actual_exit_code`, `stdout`, `stderr` | Действительный код и полные текстовые потоки команды. |
| `capture_complete`, `interpretable`, `timed_out` | Булевы признаки полного, интерпретируемого захвата и превышения времени. |
| `guard`, `passed` | Булев признак обязательной проверки и вычисленный результат наблюдения. |
| `commit`, `tree`, `source_before`, `source_after`, `source_unchanged` | Проверенный исходный код до и после команды; оба source имеют `{commit,tree}`. |

Производитель наблюдения действительно выполняет команду, сохраняет её вывод
и определяет признаки по результату. Для `passed` требуются полный и
интерпретируемый захват, завершение в срок, ожидаемый exit code, все объявленные
`stdout_contains`/`stderr_contains` и неизменившийся source. Все объявленные
методы этапа должны иметь наблюдения. `source_before`, `source_after`, `commit`
и `tree` соответствуют актуальному source; `source_unchanged: true`.

Методы вне `evidence_plan.subject_methods` всегда обязательны. `guard: true`
дополнительно делает обязательным и предметный метод. Для предметного наблюдения
допустимые `exit_codes`, `stdout_contains`, `stderr_contains` задаются явным
договором: пригодный отрицательный результат может подтверждать
`not_satisfied`. Пригодность наблюдения и удовлетворение предметного требования
оцениваются отдельно; текущий outcome определяется канонической оценкой.

`evidence_work` имеет ровно `{phase,arguments,decisions}`. `phase` принимает
`prepare` или `continue` по объявленному плану этапа; обе коллекции — массивы.
Аргумент содержит ровно
`{id,kind,facts,assumptions,inference,conclusion,verdict,observation_ids}`:

- `id` и `kind` (`logical` или `inspection`) соответствуют плану этапа;
- `facts` — непустой массив фактов, `assumptions` — массив допущений;
- `inference` и `conclusion` — содержательные непустые строки;
- `verdict` принимает `proved`, `disproved` или `inconclusive`;
- `observation_ids` — уникальные ссылки на реальные наблюдения.

Решение проверяющего содержит ровно
`{argument_id,revision,decision,reason}`: точный аргумент и его текущая revision,
`decision: "accepted"|"rejected"`, содержательная причина. Revision аргумента
читайте из `query/progression.result.accepted_arguments`, а не из версии store.
Оценка применяет объявленный план аргументов и независимого осмотра.
Эта схема относится к самостоятельному Execution; текущий интерфейс доказательств
операторской установки Poise имеет собственного владельца.

## Ответ и история

Успешная обработка возвращает exit `0` и ровно поля:
`{schema,status,revision,replayed,task_id,generation,contract_revision,state,
current_stage,iteration,traversed,required_role,stop_reason}`.

| `status` | Смысл |
| --- | --- |
| `advanced` | `next` принял текущий результат и вошёл в следующий этап; причина `new_work`. |
| `work_required` | Текущая позиция требует работы или актуальных доказательств. |
| `target_reached` | Достигнута указанная граница, включая исходную текущую позицию. |
| `route_end` | Подтверждён последний этап маршрута; execution остаётся `verified`. |

`required_role` сообщает роль достигнутого этапа; на конце маршрута — `null`.
Исполнитель новой работы назначается через владельца передачи полномочий.
Последний `current_stage` сохраняется и при `route_end`; этот ответ ещё
не означает завершения задачи или её интеграции.

Каждый элемент `traversed` имеет ровно
`{stage_id,iteration,visit_id,source_generation,source_actor_ids,gate_id,
evidence_id,current_evidence_id,outcome}`. `iteration` относится к текущему
прохождению; `source_generation`, `source_actor_ids`, `gate_id`, `evidence_id`
и `outcome` сохраняют исходное принятое исполнение. `visit_id` обозначает тот
принятый визит; `current_evidence_id` ссылается на свежие доказательства.

Публичный запрос:

```json
{"schema":"stride-execution/v1","operation":"query","input":{"queries":[{"id":"progress","kind":"progression","task_id":"0042"}]}}
```

`task_id` здесь — реально зарегистрированная задача примера; используйте свой
идентификатор. Обычный query возвращает
`{schema,status:"read_only",revision,results}`. Элемент progression имеет
`{id,kind,result}`; `result` содержит `execution`, `accepted_visits`, `traversals`,
`accepted_arguments`. История показывает исходные принятые визиты и последующие
прохождения отдельно. Для `query/execution` элемент имеет `{id,value}`;
для `query/lifecycle` — `{id,kind,result}`, а `entry_tree` находится
непосредственно в `result`. Отдельные файловые query описаны у своих владельцев.

## Остановки и правильное продолжение

Перемотка сопоставляет договор конкретного этапа, source, свежие наблюдения,
исходное обоснование и принятый осмотр. `rework` делает соответствующий прежний
результат и его последующую цепочку неприменимыми для перемотки. Повторное
потребление того же исторического визита останавливается: цикл конечен.

Перед входом в следующий этап заново оцениваются его предусловия по текущим
секциям, известным файлам и разрешениям чтения. При объявленной файловой политике
используется эта политика и целевой этап, при обычном каталоге — его наблюдатель.
Актуальный результат Gate текущего этапа и допустимость входа в следующий этап
являются отдельными условиями. Ранее пройденный допустимый префикс сохраняется;
заблокированный переход ещё не создаёт принятый визит и не меняет курсор.

| `stop_reason` | Продолжение |
| --- | --- |
| `missing_source`, `superseded_source`, `visited_source` | Выполните текущий этап и получите новый принятый результат. |
| `missing_current_evidence`, `changed_contract` | Подготовьте актуальный договор/контекст и предусмотренные свежие доказательства. |
| `stale_source`, `missing_observation` | Запустите объявленные проверки на текущем коде и сохраните полный набор наблюдений. |
| `failed_observation` | Исправьте причину неуспеха обязательной проверки и выполните её заново. |
| `incomplete_capture`, `uninterpretable_observation` | Обеспечьте полное интерпретируемое наблюдение команды и сохраните фактический результат. |
| `missing_review`, `missing_argument` | Подготовьте предусмотренное обоснование и независимый осмотр его точной revision. |
| `entry_blocked` | Подготовьте требуемые входы следующего этапа с разрешённым чтением; актуализируйте Gate и доказательства при изменении условий. |
| `new_work`, `target_reached`, `route_end` | Начните достигнутую работу, сохраните достижение цели перемотки либо передайте проверенный результат владельцу завершения. |

Обычное продолжение использует текущие данные и новый `request_id`.
При необходимости нового представления выполните допустимый `lifecycle/rework`
на активный этап по [его правилам](lifecycle-exe.md), затем подготовьте свежий
Gate и `present`. Оператор восстанавливает необходимые материалы внешним
инструментом, приложение продолжает обычную работу с корректными входами.

Каждая новая успешно обработанная операция, включая штатную остановку, увеличивает
revision store на единицу. Точный повтор того же пакета с тем же `request_id`
возвращает сохранённый ответ с `replayed: true`: проверки и переходы заново
не выполняются. Для неопределённого исхода сохраните исходный пакет и повторите
его точно; после получения ответа новую работу начинайте с актуального чтения.
При изменении исходных условий выполните новые наблюдения и новую операцию.

Отказ ввода, версии, состояния или роли возвращает
`{schema,status:"rejected",reason}`, exit `2`, пустой stderr. Собственные изменения
операции откатываются; устраните указанную причину, прочитайте текущие данные
и продолжите корректным запросом.

## Исполняемые примеры

Следующие фрагменты вызывают существующий CLI. Перед каждым новым продвижением
выполните общий фрагмент после сохранения всех его входов. Значения готовит
вызывающий из действительных ответов соответствующих владельцев:

| Переменная | Откуда получить |
| --- | --- |
| `execution_python`, `execution_cli`, `execution_config` | Абсолютные пути выбранного Python 3.13, `apps/execution/bin/execution` и рабочей конфигурации. |
| `task_id`, `actor_id`, `request_id` | Своя зарегистрированная задача, настоящий настроенный участник и идентификатор новой операции. |
| `source` | Актуальный `{commit,tree}` от владельца рабочего Git checkout. |
| `accepted_gate` | Ответ успешного `gate/evaluate` для фактически представленного кандидата: `context_id`, `candidate_id`. |
| `current_evidence`, `outcome` | Собственная запись evidence из `Store.read_records` и подтверждённый объявленный результат этапа. |
| `fresh_stage_proofs`, `target_stage` | Свежие ссылки Gate/evidence для каждого проходимого этапа; достижимый ID или `None`. |

### Общие актуальные данные

<!-- example: common -->
```python
import json
import subprocess


def call_execution(packet):
    completed = subprocess.run(
        [execution_python, "-B", execution_cli, "--config", execution_config],
        input=json.dumps(packet, ensure_ascii=False) + "\n",
        text=True, capture_output=True,
    )
    assert completed.stderr == "", completed.stderr
    return completed.returncode, json.loads(completed.stdout)


query_packet = {
    "schema": "stride-execution/v1", "operation": "query",
    "input": {"queries": [
        {"id": "execution", "kind": "execution", "task_id": task_id},
        {"id": "lifecycle", "kind": "lifecycle", "task_id": task_id},
    ]},
}
query_exit, query_response = call_execution(query_packet)
assert query_exit == 0, query_response
items = {item["id"]: item for item in query_response["results"]}
execution = items["execution"]["value"]
lifecycle = items["lifecycle"]["result"]
common = {
    "request_id": request_id,
    "expected_revision": query_response["revision"],
    "task_id": task_id,
    "expected_generation": execution["generation"],
    "expected_contract_revision": execution["contract_revision"],
    "actor_id": actor_id,
    "source": source,
    "entry_tree": lifecycle["entry_tree"],
}
```
<!-- end example: common -->

### Принятие текущего результата

После реальной проверки и `present` выполните общий фрагмент, затем:

<!-- example: next -->
```python
next_packet = {
    "schema": "stride-execution/v1", "operation": "advance",
    "input": {
        **common, "action": "next",
        "proof": {
            "context_id": accepted_gate["context_id"],
            "candidate_id": accepted_gate["candidate_id"],
            "outcome": outcome,
            "evidence_id": current_evidence["id"],
        },
    },
}
next_exit, next_response = call_execution(next_packet)
assert next_exit == 0, next_response
```
<!-- end example: next -->

Прочитайте `next_response.status`, `current_stage`, `required_role` и
`stop_reason` перед следующей работой.

### Перемотка после согласованного restart

После нового freeze/enter и записи свежих доказательств выполните общий фрагмент,
затем:

<!-- example: fast_forward -->
```python
fast_forward_packet = {
    "schema": "stride-execution/v1", "operation": "advance",
    "input": {
        **common, "action": "fast_forward", "target_stage": target_stage,
        "proofs": [
            {key: proof[key] for key in (
                "stage_id", "context_id", "candidate_id", "evidence_id",
            )}
            for proof in fresh_stage_proofs
        ],
    },
}
fast_forward_exit, fast_forward_response = call_execution(fast_forward_packet)
assert fast_forward_exit == 0, fast_forward_response
```
<!-- end example: fast_forward -->

Прочитайте `traversed` и причину остановки; следующую работу выполняет
участник с возвращённой ролью. Точный повтор сохраняет историю исходных авторов.

## Границы поставки и проверки

Продвижение использует уже выполненные внешние наблюдения. Самостоятельный runner
команд, terminal outcome задачи, межзадачное повторное использование результатов
и физическая интеграция имеют отдельных владельцев поставки. Подготовка коммита
этапа через `Store.prepare_stage_commit` уже доступна и описана в
[сравнении изменений](diff-exe.md).

Целевые проверки из корня checkout выбранным Python:

```sh
python -B apps/execution/tests/test_advance_replay.py
python -B apps/execution/tests/test_advance_replay.py --suite corrections
python -B apps/execution/tests/test_advance_replay.py --suite followup
python -B apps/execution/tests/test_shell.py --suite behavior
```

Они проверяют актуальность наблюдений и обоснований, происхождение переходов,
границы ролей, rework/restart, достижимую цель, конечность цикла и вход следующего
этапа с реальными файлами. Примеры выше дополнительно выполняются на действительных
публичных данных подготовленного окружения.
