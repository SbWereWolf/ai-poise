# Явные вызовы приложений через stride

Orchestrator передаёт одну выбранную пользователем операцию публичной команде
(`public command`) Requirements, Sequencing или Execution. Каждое следующее
действие выбирает вызывающая сторона. Эта инструкция описывает действующую
синхронную оболочку; полный [целевой контракт](contract-orc.md) описывает также
дальнейшие возможности Role, Session, `yield`, resume/deferred и асинхронного журнала.

<a id="command-and-configuration"></a>

## Запуск и настройка

Используйте подготовленный CPython 3.13.15 и явный путь к конфигурации Orchestrator.
Корневой `stride` и прикладной `apps/orchestrator/bin/stride` вызывают один обработчик.
Прямой вход `apps/orchestrator/bin/orchestrator` сохраняется. Для запуска исполняемых
файлов добавьте каталог выбранного Python в `PATH` текущей команды:

```bash
PATH="$(dirname "$ORCHESTRATOR_PYTHON"):$PATH" \
  "$ORCHESTRATOR_CHECKOUT/stride" --config /absolute/path/config/orchestrator.json
```

На stdin передайте один JSON object с завершающим переводом строки. Ответ — один
JSON object и перевод строки на stdout. Отдельный установленный `stride.sh` служит
запуску проектных операций Poise по его собственной конфигурации;
[настройка launcher](../../../docs/configuration/stride-launcher.md) имеет своего владельца.

Скопируйте [пример Orchestrator](../config/orchestrator.example.json) в рабочий
каталог конфигурации вне исходников и заполните реальные значения вместо
`/absolute/path/...`. Схема — `stride-orchestrator-config/v1`. Поля `paths.state`,
`database`, `lock` задают собственное состояние Orchestrator; канонические данные
постановок, планирования и исполнения остаются в хранилищах своих приложений.

`commands` содержит независимые назначения `requirements`, `sequencing`, `execution`.
Это имена в примере: собственные CommandId задаются конфигурацией. Каждый descriptor
`owner-json-v1` содержит:

- `argv`: абсолютный путь выбранного Python, путь публичного входа владельца,
  `--config` и путь его рабочей конфигурации;
- `cwd`: рабочий каталог дочерней команды;
- `env`: полное окружение дочерней команды, включая выбранный `PATH`;
- `timeout_seconds`: время ожидания; `accepted_exit_codes`: возвращаемые коды владельца.

Значения передаются буквально, без shell-подстановок. `env` задаётся целиком,
наследование окружения для дочернего владельца настраивается явно. `cwd` разрешается
относительно каталога конфигурации; элементы `argv` передаются команде как записаны.
Абсолютные пути в примере позволяют хранить конфигурацию независимо от исходников.
Заполните также действующие ограничения `batch.max_input_bytes`, `batch.max_items`,
`read.max_queries` и параметры собственного `lock` из поставленного примера.

## Подготовка примера

Выберите путь к этой рабочей копии, подготовленный интерпретатор и **новый** каталог
данных. Пример создаёт в нём четыре независимых хранилища и рабочие конфигурации.
Для поставленного шестизначного пространства идентификаторов Requirements пример
задаёт такую же политику Task ID в Sequencing и Execution.

```bash
export ORCHESTRATOR_CHECKOUT=/absolute/path/ai-poise
export ORCHESTRATOR_PYTHON=/absolute/path/python3.13
export ORCHESTRATOR_DATA='/absolute/path/новый каталог примера'
```

Сохраните следующий Python-блок в отдельный файл и запустите его выбранным интерпретатором,
подставив путь к сохранённому файлу:

```bash
"$ORCHESTRATOR_PYTHON" -B /absolute/path/example.py
```

Его ответы — фактический stdout каждого обращения; ожидаемое отсутствие исполнения
возвращается как результат корректного чтения владельца.

## Самостоятельные шаги

В этом примере каждое обращение записано отдельно: чтение каталога, создание
постановки, её чтение, чтение редакции планирования, регистрация и чтение
планирования, чтение исполнения и повторное чтение исходной постановки.
Requirements принимает `{operation,input}`; Sequencing и Execution добавляют свои
`schema`. Orchestrator передаёт `input.packet` выбранному владельцу без преобразования.

```python
import json
import os
from pathlib import Path
import subprocess
import sys

repository = Path(os.environ["ORCHESTRATOR_CHECKOUT"]).resolve()
data = Path(os.environ["ORCHESTRATOR_DATA"]).resolve()
python = Path(sys.executable).resolve()
data.mkdir(parents=True)
path = str(python.parent) + os.pathsep + os.defpath
owner_configs = {}

for owner in ("requirements", "sequencing", "execution", "orchestrator"):
    source = repository / "apps" / owner / "config" / f"{owner}.example.json"
    settings = json.loads(source.read_text(encoding="utf-8"))
    settings["paths"]["state"] = str(data / owner / "state")
    if owner in ("sequencing", "execution"):
        settings["identifiers"]["task"]["pattern"] = "^[0-9]{6}$"
    if owner == "sequencing":
        settings["repository"]["path"] = str(repository)
        settings["worktree"]["root"] = str(data / owner / "worktrees")
        settings["transfer"]["root"] = str(data / owner / "transfer")
    if owner == "orchestrator":
        for target in ("requirements", "sequencing", "execution"):
            command = settings["commands"][target]
            command["argv"] = [str(python), str(repository / "apps" / target / "bin" / target),
                               "--config", str(owner_configs[target])]
            command["cwd"] = str(repository)
            command["env"] = {"PATH": path}
    config = data / owner / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    owner_configs[owner] = config

environment = dict(os.environ)
environment["PATH"] = path


def send(packet):
    process = subprocess.run(
        [str(repository / "stride"), "--config", str(owner_configs["orchestrator"])],
        input=json.dumps(packet, ensure_ascii=False) + "\n",
        text=True, capture_output=True, env=environment, check=False,
    )
    print(process.stdout, end="")
    assert process.returncode == 0, (process.returncode, process.stderr)
    assert process.stderr == "", process.stderr
    return json.loads(process.stdout)


def invoke(request_id, command_id, packet, child_exit=0):
    response = send({"schema": "stride-orchestrator/v1", "operation": "invoke", "input": {
        "request_id": request_id, "command_id": command_id, "packet": packet}})
    assert response["status"] == "delivered"
    assert response["result"]["exit_code"] == child_exit
    return response["result"]["packet"]


catalogue = send({"schema": "stride-orchestrator/v1", "operation": "query", "input": {
    "queries": [{"id": "commands", "kind": "commands"}]}})
assert catalogue["results"][0]["value"]["command_ids"] == ["execution", "requirements", "sequencing"]

created = invoke("example-create", "requirements", {"operation": "create_task", "input": {
    "request_id": "example-owner-create", "goal": "Подготовить отчёт."}})
assert created["status"] == "created"
task_id = created["task_id"]
statement = created["statement_revision"]
statement_query = {"operation": "query", "input": {"queries": [
    {"id": "task", "kind": "get_task", "task_id": task_id}]}}
original = invoke("example-read-statement", "requirements", statement_query)
assert original["results"][0]["value"]["goal"] == "Подготовить отчёт."

view = invoke("example-planning-view", "sequencing", {
    "schema": "stride-sequencing/v1", "operation": "query", "input": {"queries": [
        {"id": "view", "kind": "next", "claimant": "example-operator", "scope": {"kind": "all"}}]}})
registered = invoke("example-register", "sequencing", {
    "schema": "stride-sequencing/v1", "operation": "apply", "input": {
        "request_id": "example-owner-register", "expected_revision": view["revision"], "operations": [
            {"id": "register", "kind": "register_task", "task_id": task_id,
             "requirements_statement_revision": statement}]}})
assert registered["status"] == "applied"
planning = invoke("example-read-planning", "sequencing", {
    "schema": "stride-sequencing/v1", "operation": "query", "input": {"queries": [
        {"id": "task", "kind": "task", "task_id": task_id}]}})
assert planning["results"][0]["value"]["state"] == "available"

execution = invoke("example-read-execution", "execution", {
    "schema": "stride-execution/v1", "operation": "query", "input": {"queries": [
        {"id": "task", "kind": "execution", "task_id": task_id}]}}, child_exit=2)
assert execution["status"] == "rejected" and execution["reason"] == "execution_not_found"
after = invoke("example-read-statement-again", "requirements", statement_query)
assert after == original
```

На новом наборе данных Requirements возвращает созданную постановку и её
`statement_revision`. В Sequencing появляется только отдельно зарегистрированная
Task со `state=available`. Чтение Execution возвращает `execution_not_found`:
исполнение оформляется отдельной операцией по
[договору Execution](../../execution/docs/shell-exe.md). Исходная постановка
остаётся доступной у Requirements. Полные самостоятельные команды описаны у
[Requirements](../../requirements/docs/shell-req.md) и
[Sequencing](../../sequencing/docs/shell-seq.md).

## Смена команды и повтор обращения

Измените `argv` выбранного назначения и требуемые `cwd`/`env` в собственной рабочей
конфигурации на значения другой совместимой публичной команды. Следующее обращение
с новым внешним `request_id` использует это назначение без правки Orchestrator.
`command_id` остаётся выбранным пользователем именем шага.

Для точного повтора сохраните исходный внешний `request_id`, packet и назначение.
Orchestrator возвращает сохранённый ответ с `replayed=true`, повторного вызова
владельца нет. Внешний идентификатор Orchestrator и внутренний `request_id` владельца
относятся к разным операциям; сохраняйте оба для соответствующего повтора.

## Результаты и продолжение работы

Для `owner-json-v1` ответ `status=delivered` и exit 0 означают, что команда вернула
допустимый объект. Результат предметного действия находится в `result.packet`,
фактический код владельца — в `result.exit_code`. Поэтому допустимый код владельца 2
с предметным отказом возвращается внутри доставленного ответа, как чтение Execution
в примере. Orchestrator сохраняет собственный журнал ответов, а канонические факты
принадлежат владельцам.

Отказ отдельного последующего шага не отменяет ранее созданную постановку. Например,
после изменения планирования другой командой владелец может вернуть
`revision_conflict` на обращение, основанное на прежнем корректном чтении.
Прочитайте актуальное состояние у владельца, выберите следующий самостоятельный шаг
и передайте текущую редакцию с новой идентичностью содержательно нового обращения.

При неопределённом результате транспорта сначала прочитайте фактический результат
у владельца. Для повтора используйте его штатный публичный договор и прежний
внутренний `request_id`; выполняйте следующий шаг по подтверждённым фактам.
Настройка включает `timeout_seconds` и разрешённые коды команд; ошибки запуска,
ожидания и формата результата возвращаются действующим Orchestrator как `rejected`
с exit 2. Диагностика параметров CLI сохраняет правила существующего обработчика.

## Источники и проверки

| Возможность | Исходный и действующий владельцы | Проверка |
| --- | --- | --- |
| Общая маршрутизация | [WorkTools](../../../src/poise/application/work.py) → [Orchestrator shell](../shell.py) | `test_routing.py`: C1, C3 |
| Заданные команды и их замена | [Пример настроек](../config/orchestrator.example.json), один loader в Orchestrator | `test_routing.py`: C2, C4 |
| Самостоятельные факты и сохранение первого результата | Публичные Requirements/Sequencing/Execution | `test_routing.py`: C3 |
| Повтор и различимость исходов | Журнал Orchestrator и публичные ответы владельцев | `test_routing.py`: C4; `test_shell.py` |

[Проверки маршрутизации](../tests/test_routing.py) и
[проверки оболочки](../tests/test_shell.py) запускаются выбранным Python из корня копии:

```bash
"$ORCHESTRATOR_PYTHON" -B apps/orchestrator/tests/test_routing.py --suite behavior --entry stride
"$ORCHESTRATOR_PYTHON" -B apps/orchestrator/tests/test_shell.py --suite behavior
"$ORCHESTRATOR_PYTHON" -B apps/orchestrator/tests/test_shell.py --suite documentation
```

Для исполняемых дочерних файлов добавьте каталог выбранного Python в `PATH` текущей
команды, как в начале инструкции. Проверка `--suite documentation` подтверждает
четыре структурных договора текста; фактическая работа описанного процесса
подтверждается выполнением приведённого примера. Матрица Role × Command,
установление Session, `yield`/resume/deferred и глобальный порядок асинхронного
журнала остаются отдельными частями целевого контракта.
