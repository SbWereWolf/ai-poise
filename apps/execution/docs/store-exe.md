# Собственное хранилище Execution

## Назначение и интерфейсы

Execution хранит факты исполнения отдельно от постановки требований и допуска
к работе. Каноническая Task, её поколения, процессы, попытки, доказательства и
история сохраняют переданные идентификаторы и JSON-значения.

[Оболочка](shell-exe.md) предоставляет настройку исполнения и чтение его текущей
записи через CLI. [Приёмка](gate-exe.md) самостоятельно получает оценку и сохраняет
кандидата, решение и принятый результат по объявленным условиям. Python-интерфейс `apps.execution.shell.Store` предоставляет
запись и чтение фактов. Более широкий жизненный цикл описан в
[целевом контракте](contract-exe.md). Запись произвольного факта сохраняет переданный результат его владельца.
Решение о приёмке вычисляет собственный gate по полному контексту.

## Подготовка собственного store

Выберите конфигурацию по [инструкции оболочки](shell-exe.md#конфигурация-приложения).
Без компонента `process` первое подключение подготавливает собственную схему `1`:
новый store либо прежний собственный формат с `meta`, `executions`, `requests`.
Для каждого прежнего исполнения добавляется снимок фактически сохранённого контракта;
сохраняются исходные строки, JSON-значения, IDs, revision и результаты повторов.

При включённом [компоненте process](process-exe.md#явная-конфигурация) собственная
схема `1` подготавливается до версии `2`: `workspace_id` допускает null согласно
выбранному процессу. Сохраняются прежние исполнения, факты/их порядок, requests,
revision, ссылки и индексы. Подготовленный формат `2` открывается повторно и через
core API. Базовый путь нового store без process остаётся форматом `1`.
Схема собственного редактора — `1`, в отдельной явно выбранной БД.

Process-enabled `setup_execution` в одной транзакции создаёт canonical Task/generation,
`contract_snapshot`, затем `process_snapshot`. Его data точно содержит
`{process_ref, process}`: выбранную ссылку и поддержанную полную definition на момент
настройки. Последующее изменение current или шаблона сохраняет ранее записанные
снимки. Для необязательного Workspace передайте null только процессу с
`worktree_required: false`. Базовый core-only setup сохраняет свой contract snapshot;
полный фактический процесс в таком использовании передают отдельно через record API.

Подготовка каждого формата выполняется транзакционно. При обычном отказе после
DDL/копирования строк исходные схема и данные сохраняются. Устраните причину внешним
инструментом и снова откройте тот же store. Это подготовка собственных данных
Execution в новой поставке, не перенос старой установки Poise.

## Формат факта

Общий writer принимает пять видов фактов. Их записи содержат ровно следующие поля:

| Поле | Значение |
| --- | --- |
| `kind` | Один из пяти видов в следующей таблице |
| `id` | Непустой строковый идентификатор факта |
| `task_id` | Канонический идентификатор уже настроенного исполнения |
| `generation` | Положительное целое поколение, представимое SQLite INTEGER |
| `revision` | Положительная целая версия факта, представимая SQLite INTEGER |
| `data` | JSON-объект с переданным содержимым факта |

| Вид | Содержимое и использование |
| --- | --- |
| `process_snapshot` | Полный разрешённый процесс и его исходная ссылка; документ передаёт вызывающая сторона |
| `contract_snapshot` | Снимок контракта и его редакция; canonical `data.contract.task_id` соответствует внешнему `task_id` |
| `attempt` | Последовательные факты одной попытки проверки, включая её состояние и полученный результат |
| `evidence` | Полученные доказательства; заполненный `data.attempt_id` ссылается на попытку той же Task и generation |
| `history` | События и согласования, включая перезапуск; исходные участники, причины, состояния до и после сохраняются в `data` |

Для нового факта используйте revision `1`. Виды снимков, доказательств и истории
сохраняются как отдельные факты. Для следующего поколения выбирайте новый `id`.
У попытки следующие версии имеют тот же `id` и generation и последовательные
revision. Предыдущие версии остаются доступны в общем чтении.

Идентификаторы фактов, Task, попыток и запросов задавайте непустыми строками без NUL.

Идентичность записи определяется Task, видом, `id` и revision. Повторное сохранение
той же идентичности с теми же значениями сохраняет исходный факт. Если в `data`
задан собственный `task_id`, он соответствует Task записи. В пакете размещайте
попытку перед доказательством, которое на неё ссылается; подходит и попытка,
сохранённая ранее в этом же store.

Используйте конечные JSON-числа, строки корректного UTF-8, объекты со строковыми
ключами, массивы, boolean и null. Типы значений и порядок массивов сохраняются;
поля объектов сохраняются канонически. Для generation и revision допустим
диапазон положительного SQLite INTEGER: от `1` до `9223372036854775807`.
Числа внутри `data` кодируются как JSON и могут быть больше SQLite INTEGER;
выбирайте значения, обрабатываемые JSON-кодировщиком выбранного Python.

Приёмка дополнительно создаёт `gate_context`, `gate_candidate`, `gate_decision`
и `stage_result` с тем же общим envelope; они видны в `read_records`. Их
[точные данные и происхождение](gate-exe.md#чтение-и-повторы) имеют своего producer —
команды `gate`; общий writer сохраняет прежний набор пяти видов. Для приёмки
используйте полный структурно корректный процесс из process-enabled setup.
Проверка структуры применяется при использовании снимка; хранение JSON-факта
сохраняет свой отдельный договор. Приёмка использует существующую схему записей
и общий журнал requests/revision, без отдельного формата БД.

## Запись, чтение и повтор

```python
store.write_records(request_id=request_id, expected_revision=current_revision, records=records)
store.read_records(task_id)
store.read_attempt(task_id, attempt_id)
```

`write_records` принимает непустой список. Один новый успешный пакет увеличивает
общую revision на `1`, включая пакет уже существующих идентичных фактов. Результат
содержит `revision`, `changed` — число новых версий фактов, и `replayed`.

Для точного повтора используйте тот же `request_id`, список записей и исходный
`expected_revision`: сохранённый результат возвращается с `replayed: true`.
Для следующего нового пакета получите текущую revision через запрос исполнения
и выберите новый `request_id`.

`read_records` читает все версии фактов одной Task в порядке добавления, включая
ранние поколения и историю. Один вызов получает согласованный снимок данных.
`read_attempt` возвращает последнюю версию указанной попытки в данной Task.
Повторное открытие приложения сохраняет значения и порядок чтения.

## Пример правильного использования

В корне checkout выберите Python для приложения и задайте `EXECUTION_CONFIG` —
абсолютный путь к собственному файлу конфигурации из руководства оболочки.
Для этого примера используйте новый собственный core-only store с базовым
[execution.example.json](../config/execution.example.json), без компонента process.
Пример полного process-enabled использования находится в
[руководстве редактора](process-exe.md#правильное-создание-изменение-и-настройка-исполнения).
Каноническая Task `0042`,
её ссылки на требования, процесс, допуск и Workspace передаются вызывающей стороной.

```sh
python -B apps/execution/bin/execution --config "$EXECUTION_CONFIG" <<'JSON'
{"schema":"stride-execution/v1","operation":"apply","input":{"request_id":"setup-demo-1","expected_revision":0,"operations":[{"id":"setup","kind":"setup_execution","contract":{"task_id":"0042","requirements_snapshot_revision":1,"process_ref":{"goal_type":"development","revision":"process-v1"},"admission_id":"admit-0042","workspace_id":"ws-0042","stage_expectations":[{"stage_id":"implementation","values":[],"files":[],"methods":[]}],"restart_policy_id":"restart-v1","methods":[],"evidence_plan":{}}}]}}
JSON
```

Настройка возвращает revision `1` и создаёт фактический снимок контракта.
Текущую запись исполнения можно прочитать тем же CLI:

```sh
python -B apps/execution/bin/execution --config "$EXECUTION_CONFIG" <<'JSON'
{"schema":"stride-execution/v1","operation":"query","input":{"queries":[{"id":"current","kind":"execution","task_id":"0042"}]}}
JSON
```

Следующий пример сохраняет уже полученные факты успешной проверки: её начальное
и конечное состояния, доказательство и событие истории. Вызывающая сторона
передаёт результаты; после сохранения можно читать их через новое подключение.

```python
import json
import os
from pathlib import Path

from apps.execution.shell import Store, load_config

config_path = Path(os.environ["EXECUTION_CONFIG"])
store = Store(load_config(config_path))
query = {
    "schema": "stride-execution/v1",
    "operation": "query",
    "input": {"queries": [{"id": "current", "kind": "execution", "task_id": "0042"}]},
}
current_revision = store.query(query)["revision"]
records = [
    {"kind": "attempt", "id": "check-1", "task_id": "0042", "generation": 1,
     "revision": 1, "data": {"status": "running", "stage_id": "implementation", "iteration": 1}},
    {"kind": "attempt", "id": "check-1", "task_id": "0042", "generation": 1,
     "revision": 2, "data": {"status": "completed", "stage_id": "implementation", "iteration": 1, "exit_code": 0}},
    {"kind": "evidence", "id": "receipt-1", "task_id": "0042", "generation": 1,
     "revision": 1, "data": {"attempt_id": "check-1", "exit_code": 0, "report": "Проверка выполнена"}},
    {"kind": "history", "id": "event-1", "task_id": "0042", "generation": 1,
     "revision": 1, "data": {"event": "check_recorded", "attempt_id": "check-1"}},
]
result = store.write_records(request_id="facts-demo-1", expected_revision=current_revision, records=records)
replay = store.write_records(request_id="facts-demo-1", expected_revision=current_revision, records=records)
reopened = Store(load_config(config_path))
latest = reopened.read_attempt("0042", "check-1")
history = reopened.read_records("0042")
assert result == {"revision": 2, "changed": 4, "replayed": False}
assert replay == {"revision": 2, "changed": 4, "replayed": True}
assert latest == records[1]
assert history[1:] == records
print(json.dumps({"result": result, "replay": replay, "latest": latest, "records": history}, ensure_ascii=False))
```

## Атомарность и продолжение

Пакет записи сохраняется одной транзакцией: новые факты, общая revision и результат
повтора доступны вместе. При отказе сохраняются прежние факты, revision и журнал
повторов. Python-вызов сообщает `ExecutionError` с причиной и действием для продолжения.

После исправления входных данных получите актуальную revision и отправьте новый
пакет с новым `request_id`. Для повторного получения результата уже выполненной
операции используйте её точный исходный пакет. Подготовка собственного store и
чтение выполняются через те же настройки приложения.

События перезапуска передавайте с прежним `task_id`, новым явно выбранным поколением
и исходными деталями согласования. Политика этапов и решение о перезапуске остаются
у владельца исполнения; storage сохраняет переданные факты и их связи.

## Проверки

Целевые проверки хранилища:

```sh
python -B -m pytest -q \
  apps/execution/tests/test_store_preparation.py \
  apps/execution/tests/test_store_records.py \
  apps/execution/tests/test_store_transactions.py \
  apps/execution/tests/test_store_boundaries.py \
  apps/execution/tests/test_store_json_integer.py
```

Они проверяют подготовку из пустого и непустого собственного store, пять групп
фактов, точные типы и связи, порядок, повтор, повторное открытие и реальный
транзакционный откат. [Проверки оболочки](shell-exe.md#проверки) подтверждают её
текущий интерфейс. Проверка локальных ссылок и входящих targets:

```sh
PYTHONPATH=src python -B tools/check_documentation.py --checkout . --changed apps/execution/docs
```
