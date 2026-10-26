# Проверки, попытки и повторное использование результата

## Подготовка и настройка

Используйте Python 3.13, POSIX/Linux (включая Ubuntu в WSL), Git и отдельную
рабочую конфигурацию Execution. Из корня checkout приложение вызывается так:

```sh
python -B apps/execution/bin/execution --config "$EXECUTION_CONFIG"
```

`EXECUTION_CONFIG` здесь обозначает путь, выбранный для примера запуска.
Один вызов читает один UTF-8 JSON с завершающим переводом строки из stdin
и возвращает один JSON в stdout. Полный базовый файл и параметры хранилища
описаны в [оболочке](shell-exe.md#конфигурация-приложения).

В полном рабочем файле задайте компонент `verification` из пяти полей:

```json
{
  "verification": {
    "roles": ["writer", "reviewer"],
    "max_attempts": 8,
    "repository": "/work/project",
    "base_ref": "main",
    "output_root": "/work/execution-runs"
  }
}
```

Это полный компонент, добавляемый к базовой конфигурации. Заполните пути
своей установки и существующий Git ref принятой интеграционной базы.
Рабочий файл храните отдельно от версионных примеров.

| Параметр | Как заполнить |
| --- | --- |
| `roles` | Непустой список уникальных ролей из значений `lifecycle.actors`, которым поручены проверки |
| `max_attempts` | Явное положительное целое число новых попыток на одно прохождение этапа |
| `repository` | Корень подготовленного Git checkout, в котором выполняются методы |
| `base_ref` | Существующий локальный ref интеграционной базы для принятого исходного коммита при reuse |
| `output_root` | Каталог полных результатов запусков, доступный приложению для записи и последующего recover |

Относительные `repository` и `output_root` отсчитываются от каталога
конфигурационного файла. `cwd` метода отсчитывается от `repository`;
абсолютный `cwd` обозначает явно выбранный каталог. `argv`, `environment`,
ожидаемый exit code, маркеры stdout/stderr и объявления `outputs` поступают
из замороженного метода. Схемы метода и evidence plan описаны в
[договоре методов](contract-exe.md#53-методы-аргументы-и-осмотр).
Каталог конкретного запуска передаётся команде
в `POISE_RUN_OUTPUT_DIR`. Отдельный параметр deadline у метода отсутствует.
Параметры ожидания доступа к store описаны в оболочке; внешняя команда
выполняется за пределами транзакции и блокировки собственного store.

Для начала работы подготовьте исполнение через
[process-enabled setup](process-exe.md#правильное-создание-изменение-и-настройка-исполнения),
[freeze и enter](lifecycle-exe.md#правильный-порядок-работы),
затем получите принятый текущий [контекст и кандидат Gate](gate-exe.md).
Для `reuse/accept` дополнительно настройте
[outcomes.roles](outcomes-exe.md#подготовка-и-настройка).
Существующие core-чтения и setup сохраняют свой договор независимо от наличия
необязательного компонента `verification`. Новые команды и их чтения используют
этот компонент при обращении к соответствующему владельцу.

## Пакеты и результаты

Envelope содержит ровно `schema`, `operation`, `input`;
`schema` равна `stride-execution/v1`. Для мутаций `attempt` и `reuse` обязательны
следующие общие поля `input`:

| Поле | Источник значения |
| --- | --- |
| `action` | Действие из таблицы выбранной операции ниже |
| `request_id` | Идентификатор данного обращения; точный повтор использует весь исходный пакет |
| `expected_revision` | Свежая общая revision Execution из `query` |
| `task_id` | Канонический идентификатор текущей задачи |
| `expected_generation` | Текущая generation из `query/execution` |
| `expected_contract_revision` | Текущая contract_revision из того же чтения |
| `actor_id` | Участник из `lifecycle.actors` с разрешённой ролью |

Новая попытка соответствует роли текущего узла процесса. Продолжайте её тем же
`actor_id`, который зарезервировал попытку. Source — объект ровно
`{commit, tree}` с действительными `HEAD` и `HEAD^{tree}` выбранного checkout.

Успешный ответ `attempt` содержит ровно
`schema,status,revision,replayed,changed,attempt`; `reuse` имеет тот же набор
с полем `reuse` вместо `attempt`. `changed` описывает изменение собственного
бизнес-результата. Запись только нового ответа request_id сама по себе
не означает новый результат. Точный завершённый повтор сохраняет исходные
revision и changed, устанавливая `replayed: true`.

| Результат | Exit | Продолжение |
| --- | --- | --- |
| `attempt_reserved`, `run_recorded`, `attempt_finished`, `attempt_abandoned` | `0` | Прочитать возвращённую попытку и выполнить следующий согласованный шаг |
| `reuse_prepared`, `reuse_verified`, `reuse_accepted` | `0` | Использовать сохранённый кандидат или принятый факт |
| `checks_failed` | `1` | Прочитать реальные результаты методов, исправить проверяемый результат и согласовать новую попытку в действующей области работы |
| `pending` | `3` | Продолжить существующий intent по сохранённым attempt_id/run_id |
| `rejected` | `2` | Прочитать `reason` и текущее состояние, выполнить описанное владельцем исправление условия |

Отказ содержит ровно `schema,status,reason`; stderr пустой. Вызов внешней команды
может уже сохранить intent или историю известного отказа до такого ответа.
Поэтому после ошибки запуска или записи сначала читайте состояние и revision.
Фактический exit самой команды находится в её receipt; `run_recorded` при этом
остаётся успешной записью факта с exit `0`. Итог `checks_failed` относится
к оценке локальной проверки `reuse`.

## Обычная попытка

Во всех строках таблицы к общим полям добавляются `attempt_id` и поля строки.
Идентификатор новой попытки выбирает вызывающий; run_id выдаёт Execution.

| action | Дополнительные поля | Действие |
| --- | --- | --- |
| `begin` | `context_id,candidate_id,source,method_ids` | Зарезервировать весь упорядоченный список методов текущего этапа |
| `run` | `run_id` | Выполнить очередной зарезервированный метод и записать реальное наблюдение |
| `recover` | `run_id` | Завершить запись уже известного результата этого запуска |
| `finish` | `evidence_work` | Завершить полную попытку и сохранить её evidence |
| `abandon` | `reason` | Завершить спокойную попытку с сохранением её истории |

`method_ids` точно повторяет список в `stage_expectations` текущего этапа;
порядок `runs` сохраняет этот список. Пустое объявленное расписание тоже создаёт
одну попытку и завершается через `finish`, без подпроцесса. `evidence_work`
проверяется существующим владельцем доказательств для текущего этапа.
Аргументы и независимые решения описаны в
[договоре продвижения](advance-exe.md#факты-проверки-и-обоснование).

Правильный порядок: `begin` → последовательные `run` → `finish` → передача
полученного evidence в существующий `next` или `fast_forward`.
Возвращаемая попытка имеет 16 полей:

| Поля | Значение |
| --- | --- |
| `attempt_id,task_id,generation,contract_revision` | Адрес попытки и её исходный договор |
| `stage_id,iteration,context_id,candidate_id` | Прохождение этапа и принятый Gate |
| `source,method_ids` | Проверяемый коммит/дерево и упорядоченное расписание |
| `purpose,owner_id` | `stage` и candidate_id для обычной проверки; `reuse` и reuse_id для повторного использования |
| `budget,state,runs,evidence_id` | Расход при резервировании, состояние, реальные запуски и адрес созданного evidence |

`budget` содержит `used,maximum`, зафиксированные при резервировании.
Состояние — `reserved`, `running`, `finished` или `abandoned`.
Каждый run содержит ровно `run_id,method_id,started,receipt,termination`.
До реального результата receipt и termination равны null. Finished означает,
что все наблюдения сохранены; пригодность результата для продвижения оценивает
существующий владелец evidence. Неуспешное наблюдение сохраняется как факт.

### Полный последовательный пример

Перед выполнением подготовьте настоящий текущий Gate и расписание его этапа.
Пример рассчитан на `evidence_work` с `phase: prepare` и пустыми списками
аргументов/решений, разрешёнными evidence plan этого этапа. Выбранные методы
в данном примере успешно завершаются. Для своего evidence plan передайте
объявленные им аргументы и решения.

Выполните следующий Python из корня checkout, передав семь аргументов:
путь конфигурации, task_id, actor_id, context_id, candidate_id, новый attempt_id
и JSON-массив объявленных method_ids. Значения context_id/candidate_id берутся
из подготовленного Gate, а не назначаются этим примером.

```python
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

config, task, actor, context, candidate, attempt_id, methods_json = sys.argv[1:]
config = Path(config).resolve()
provider = Path.cwd() / "apps/execution/bin/execution"
settings = json.loads(config.read_text(encoding="utf-8"))
repository = (config.parent / settings["verification"]["repository"]).resolve()

def call(operation, value):
    packet = {"schema": "stride-execution/v1", "operation": operation, "input": value}
    result = subprocess.run(
        [sys.executable, "-B", str(provider), "--config", str(config)],
        input=json.dumps(packet) + "\n", text=True, capture_output=True,
    )
    response = json.loads(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(response)
    return response

def common():
    read = call("query", {"queries": [{"id": "current", "kind": "execution", "task_id": task}]})
    current = read["results"][0]["value"]
    return {"request_id": str(uuid4()), "expected_revision": read["revision"],
            "task_id": task, "expected_generation": current["generation"],
            "expected_contract_revision": current["contract_revision"], "actor_id": actor}

def source():
    def git(ref):
        return subprocess.check_output(
            ["git", "rev-parse", ref], cwd=repository, text=True,
        ).strip()
    return {"commit": git("HEAD"), "tree": git("HEAD^{tree}")}

attempt = call("attempt", {**common(), "action": "begin", "attempt_id": attempt_id,
    "context_id": context, "candidate_id": candidate, "source": source(),
    "method_ids": json.loads(methods_json)})["attempt"]
for run in attempt["runs"]:
    attempt = call("attempt", {**common(), "action": "run", "attempt_id": attempt_id,
        "run_id": run["run_id"]})["attempt"]
attempt = call("attempt", {**common(), "action": "finish", "attempt_id": attempt_id,
    "evidence_work": {"phase": "prepare", "arguments": [], "decisions": []}})["attempt"]
print(json.dumps(attempt, ensure_ascii=False))
```

Пример выполняет только обычный успешный путь. При незавершённом результате
сохраните полученный ответ и продолжите по порядку восстановления ниже.

## Реальные результаты команды

Receipt содержит 28 полей, сгруппированных здесь по назначению:

| Поля | Что сохранено |
| --- | --- |
| `id,method,obligations` | id равен run_id; obligations содержит идентификатор фактически выполненного метода |
| `argv,cwd,environment` | Объявленная команда и её явные параметры |
| `actual_exit_code,stdout,stderr` | Действительный exit и полные текстовые потоки |
| `capture_complete,interpretable,timed_out,guard,passed` | Оценка захвата, интерпретации и результата по объявленным ожиданиям |
| `commit,tree,source_before,source_after,source_unchanged` | Исходный source и реальные Git-наблюдения до и после команды |
| `attempt_id,run_id,execution_key` | Связь с попыткой, запуском и полным набором наблюдений |
| `stdout_bytes_hex,stderr_bytes_hex` | Полные исходные байты потоков в hex |
| `resolved_cwd,output_directory,outputs,termination` | Реальные рабочий каталог, каталог результатов, выходы и завершение |

Текст декодируется как UTF-8 с заменой непредставимых байтов; исходные байты
сохраняются полностью. Для такого потока `interpretable` равно false.
`guard` определяется существующим evidence plan: методы из `subject_methods`
получают false, остальные — true. Их допуск оценивается тем же владельцем
доказательств по объявленным правилам.

Каждый элемент `outputs` содержит ровно
`id,declared_path,required,status,path,size`. Статус — `captured`, `missing`
или `invalid`; для captured записываются абсолютный путь и размер в байтах,
для остальных path/size равны null. Обязательный отсутствующий выход делает
receipt.capture_complete равным false. Необязательный отсутствующий выход
сохраняется как missing.

`termination` содержит ровно
`schema,child_reaped,process_group_stopped,capture_complete`;
`schema` равна `run-termination-1`. Захват termination относится к потокам
фактически завершённой команды: при отсутствующем обязательном output он может
быть полным, а receipt.capture_complete — false. Успешное завершение требует
подтверждённого окончания процесса и его группы.

## Бюджет, чтение и повтор

Новая попытка расходует единицу `max_attempts`. Счётчик принадлежит generation,
этапу, iteration и фактическому открытию работы через enter/rework/restart.
Новый допущенный проход получает собственный бюджет. Завершённые и abandoned
попытки остаются в расходе. Чтение, точный повтор и продолжение той же попытки
сохраняют счётчик. При исчерпанном бюджете продолжайте существующую попытку
либо согласуйте действительный rework/restart по договору lifecycle.

Для собственного addressed-чтения передайте:

```json
{
  "schema": "stride-execution/v1",
  "operation": "query",
  "input": {
    "queries": [
      {"id": "budget", "kind": "verification", "task_id": "0042"},
      {"id": "attempt", "kind": "attempt", "task_id": "0042", "attempt_id": "check-0042"}
    ]
  }
}
```

В этом примере task_id и attempt_id — адреса существующей подготовленной попытки.
Корень ответа содержит `schema,status: read_only,revision,results`.
Эти элементы results имеют ровно `id,kind,result`.
`query/verification` возвращает
`task_id,generation,stage_id,iteration,used,maximum,pending_attempt_id`;
`query/attempt` — полную сохранённую попытку по адресу, включая прошлую generation.
`query/reuse` принимает `id,kind,task_id,reuse_id` и возвращает полный кандидат.
Чтение сохраняет бизнес-состояние.

Для точного завершённого повтора отправьте весь исходный пакет с прежним
expected_revision. Для следующего обращения используйте свежую revision и новый
request_id. Pending — текущее продолжение, а не закэшированное завершение:
после появления реального результата продолжайте сохранённые адреса.

## Продолжение после сбоя

1. Прочитайте `query/attempt` и `query/verification`, сохранив attempt_id/run_id.
   Для кандидата reuse дополнительно прочитайте `query/reuse`.
2. Если результат команды уже получен, а запись в БД прервалась, устраните
   причину записи внешним инструментом и вызовите `attempt/recover` для того же
   run_id со свежей revision. Execution сохраняет реальный захват из каталога
   запуска. Затем завершите оставшиеся методы и `finish`.
3. Если владелец установил, что команда ещё не запускалась, устраните указанную
   причину подготовки/запуска. При сохранённом started вызовите recover того же
   запуска: он запишет известный факт отсутствия команды и вернёт осмысленный
   rejected/exit2. Повторное чтение покажет возвращённое разрешение запуска.
   Для reserved с started=false выполните явный новый `run` либо завершите
   спокойную попытку через `abandon` с причиной. Единица бюджета остаётся
   израсходованной, история ошибки сохраняется.
4. Для фактически работающей команды сохраните текущую попытку и дождитесь
   действительного завершения у её владельца. Recover до появления результата
   возвращает pending. Поздняя запись старой ошибки относится к исходному
   запросу и сохраняет разрешение новой работающей команды.
5. После сохранения действительных результатов и завершения попытки продолжайте
   Gate/evidence/lifecycle либо тот же `reuse/verify` по текущему состоянию.

`abandon` применяйте к попытке без незавершённых запущенных команд.
Завершение процесса подтверждают его реальные receipt/termination.
Для результата старого source согласуйте текущую работу через lifecycle,
сохраняя исходные попытки и наблюдения. Полные каталоги запусков сохраняйте
до завершения потребителей recover/evidence; место хранения выбирает оператор.

## Повторное использование принятого результата

Execution сохраняет собственное решение о повторном использовании. Семейство,
допуск, claim и Workspace поступают как публичные факты Sequencing; опубликованные
требования — от Requirements. Полномочия этих приложений остаются у их владельцев.
Действительный source outcome хранится в Execution вместе с его замороженными
контрактом, процессом и снимком требований исходной generation.

Правильный порядок:

1. Выберите другую completed-задачу того же семейства и её принятый positive
   outcome с result_commit. Публичный terminal источника указывает именно этот
   outcome через `source_ref: {owner: execution, id: outcome_id}` и содержит
   соответствующие task_id/kind/result_commit. Исторический принятый outcome
   сохраняет значение и после нового restart исходной задачи.
2. Подготовьте локальный checkout цели с исходным коммитом среди предков
   `verification.base_ref` и HEAD. Получите текущий принятый Gate цели.
   Требования и процесс цели соответствуют совместимому принятому результату.
3. Получите актуальные публичные `family` и `eligibility` для цели и передайте
   `admission` ровно из `claimant,family,eligibility`. Цель eligible, available
   либо claimed тем же claimant. Если публичный факт содержит Workspace,
   его состояние — ready.
4. Вызывающая сторона физически доставляет необходимые файлы, объявляет их
   в Gate цели и задаёт явный artifact_mapping. Выполните prepare, затем verify
   с новым attempt_id; Execution выполнит свежие локальные методы и создаст evidence.
5. После reuse_verified выполните собственный accept с новым outcome_id,
   причиной и локальным result_commit. Передайте полученный полный факт
   [получателям](outcomes-exe.md#явная-передача-отдельным-приложениям) явно.

### Совместимость и файлы

Сравниваются полные замороженные контракт, процесс и публикация Requirements.
В контракте различаться могут локальные task_id/workspace_id/admission_id и
requirements_snapshot_revision. В публикации нормализуются её локальные
корневые task_id/snapshot_revision/statement_revision/registry_revision,
statement.task_id/statement_revision и ссылки application_task на собственную
задачу соответствующей публикации. Остальные идентификаторы, содержимое
требований, цель, DoD, статусы, связи, порядок и количество элементов сохраняют
смысл. Содержательные изменения требуют своей проверки и принятого результата.

`artifact_mapping` — список объектов ровно
`source_scope,source_path,target_scope,target_path`. Scope — `task` или `sprint`,
пути относительны соответствующему корню Gate. Полные файлы обеих сторон
присутствуют в принятых Gate и доступны по этим путям. Например:

```json
[
  {
    "source_scope": "task",
    "source_path": "0042/result.txt",
    "target_scope": "task",
    "target_path": "0045/result.txt"
  }
]
```

Пример предполагает уже доставленные и объявленные текстовые результаты
0042 и 0045; бинарные файлы проверяются тем же путём. Пустой список соответствует
случаю без сопоставляемых файлов. Требуемые собственным Gate файлы цели
учитываются и при таком списке. Физическую доставку выполняет вызывающий;
Execution наблюдает фактические локальные файлы через действующий Gate.

### Команды reuse

К общим полям каждой строки добавляются `reuse_id,admission` и поля строки:

| action | Дополнительные поля |
| --- | --- |
| `prepare` | `source_task_id,source_outcome_id,source,context_id,candidate_id,artifact_mapping` |
| `verify` | `attempt_id` |
| `accept` | `outcome_id,reason,result_commit` |

Source при prepare — текущий локальный source цели. Verify выполняет все методы
контракта, у которых `verification_plan.green_stages` непустой, сохраняя порядок
методов контракта; это собственное расписание проверки готового результата.
Применяется тот же владелец попыток и общий бюджет текущего этапа.
При pending продолжайте тот же reuse_id и attempt_id с актуальными фактами
admission: завершённый префикс сохраняется, оставшиеся run продолжаются через
run/recover. После checks_failed новый согласованный check использует новый
attempt_id и расходует следующую единицу бюджета.

Кандидат имеет 14 полей:
`reuse_id,task_id,generation,contract_revision,source_task_id,source_outcome_id,
source,context_id,candidate_id,artifact_mapping,state,attempt_id,evidence_id,fact`.
State — `prepared`, `verifying`, `checks_failed`, `verified`, `accepted`.
При явном accept создаётся обычный positive-факт с дополнительным
`reuse: {source_task_id,source_outcome_id,reuse_id}`. Сохраняются исходные
visit/source_generation/source_actor_ids/gate_id/evidence_id и текущие
current_gate_id/current_evidence_id в provenance. Полный набор полей обычного
факта описан у [владельца исхода](outcomes-exe.md#пакет-и-результат).

Обычное решение outcome и reuse/accept относятся к одной области текущей работы.
После принятия используйте сохранённый итог; новое прохождение открывается
через настоящий lifecycle. Принятие reuse создаёт свой итог без продвижения
курсора процесса или назначения handler_outcome. Передача результата отдельным
приложениям остаётся явной операцией вызывающего.

### Последовательный пример reuse

Продолжите с функциями `call`, `common` и `source` предыдущего Python-примера,
выбрав целью подготовленную задачу `0045` и её собственные context/candidate.
Её исходная обычная попытка уже finished; область работы ещё открыта.
В файле `admission.json` вызывающая сторона сохраняет публичные claimant/family/
eligibility для этой цели. Выбранный источник — задача `0042` с принятым outcome;
необходимый исходный commit уже локален и интегрирован, контракт совместим.
Этот пример рассчитан на результат без файлового сопоставления.

```python
admission = json.loads(Path("admission.json").read_text(encoding="utf-8"))
source_task_id = "0042"
source_card = next(member["task"] for member in admission["family"]["members"]
                   if member["task"]["task_id"] == source_task_id)
source_outcome_id = source_card["terminal"]["source_ref"]["id"]
reuse_id = str(uuid4())
local_source = source()
reused = call("reuse", {**common(), "action": "prepare", "reuse_id": reuse_id,
    "source_task_id": source_task_id, "source_outcome_id": source_outcome_id,
    "source": local_source, "context_id": context, "candidate_id": candidate,
    "artifact_mapping": [], "admission": admission})["reuse"]
reused = call("reuse", {**common(), "action": "verify", "reuse_id": reuse_id,
    "attempt_id": str(uuid4()), "admission": admission})["reuse"]
reused = call("reuse", {**common(), "action": "accept", "reuse_id": reuse_id,
    "outcome_id": str(uuid4()), "reason": "Принят проверенный локальный результат.",
    "result_commit": local_source["commit"], "admission": admission})["reuse"]
print(json.dumps(reused["fact"], ensure_ascii=False))
```

## Проверки поставленного поведения

Из корня checkout выбранным тестовым Python выполните:

```sh
python -B apps/execution/tests/test_verification_reuse.py --suite behavior
python -B apps/execution/tests/test_shell.py --suite behavior
python -B apps/execution/tests/test_shell.py --suite documentation
```

Основная проверка включает девять групп и 56 именованных проверок реальных
команд, повторов, бюджета, совместимости, файлов, неуспеха и продолжения.
Подробный результат находится в
`POISE_RUN_OUTPUT_DIR/verification-reuse-details.json`, когда этот каталог задан
запускающим владельцем. Smoke проверяет восемь сценариев существующей оболочки.
Локальные ссылки и входящие targets проверяет существующий инструмент:

```sh
PYTHONPATH=src python -B tools/check_documentation.py \
  --checkout . --changed apps/execution/docs
```

Поставленный runner и reuse относятся к описанным текущим командам.
Другие будущие поверхности интеграции/transfer в
[проектных разделах контракта](contract-exe.md) сохраняют отдельный статус.
Перенос функций сам по себе не изменяет статус прежних известных ограничений
Poise в их [существующем реестре](../../../docs/operations/known-bugs.md).
