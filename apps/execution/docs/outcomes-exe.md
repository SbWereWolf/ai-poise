# Итог выполнения и адресуемые факты

## Подготовка и настройка

Используйте Python 3.13, POSIX/Linux (включая Ubuntu в WSL) и собственный
конфигурационный файл Execution. Команда и обязательные параметры описаны
в [оболочке](shell-exe.md#запуск). Приложение принимает один JSON через stdin:

```sh
python -B apps/execution/bin/execution --config "$EXECUTION_CONFIG"
```

Для нового решения добавьте в рабочую конфигурацию компонент:

```json
{"outcomes": {"roles": ["writer"]}}
```

Это фрагмент полного файла, а не отдельная конфигурация. Образец
[execution.example.json](../config/execution.example.json) уже содержит этот
компонент. `outcomes` имеет ровно поле `roles`: непустой список уникальных
непустых идентификаторов ролей из значений `lifecycle.actors`. Для показанного
образца участник `executor-A` имеет роль `writer`. Настройте собственные
участники и роли по [договору lifecycle](lifecycle-exe.md#явная-настройка-участников-и-политики).
Храните рабочий файл отдельно от версионного примера.

| Поле | Как заполнить |
| --- | --- |
| `outcomes.roles` | Явно перечислите роли, которым поручено принимать итоговое решение |
| `lifecycle.actors` | Сопоставьте передаваемый `actor_id` его действующей роли |
| `paths`, лимиты и process/gate/lifecycle | Заполните по руководствам существующих владельцев в полном файле |

Политика исходов проверяется при создании нового решения. Историческое чтение и
точное воспроизведение уже сохранённого запроса доступны через их действующие
контракты и при отсутствии компонента `outcomes`.

## Правильный порядок решения

1. Настройте исполнение с каноническим `task_id`, опубликованным снимком
   Requirements и выбранным процессом, затем выполните
   [freeze и enter](lifecycle-exe.md#правильный-порядок-работы).
2. Выполните этапы и независимые проверки по процессу. Представьте действительный
   Gate и передайте реальные evidence в [next либо fast_forward](advance-exe.md#пакет-продвижения).
3. После `route_end` уполномоченный участник явно принимает итог через `outcome`
   с `intent: "accept"`. Передайте source допущенного терминального результата
   и актуальные revision, generation и редакцию контракта.
4. Прочитайте сохранённый факт по `task_id` и `outcome_id`. Вызывающая сторона
   явно передаёт необходимые проекции получателям через настроенный Orchestrator.

`route_end` и состояние `verified` относятся к прохождению процесса. Решение
`outcome` использует фактически допущенный терминальный визит, его обработчик,
оригинальное доказательство и текущие Gate/evidence. Source должен совпадать
с исходным принятым и текущим доказанным результатом. Автор решения может
отличаться от участников, выполнявших и проверявших этапы.

Для явной отмены текущей работы уполномоченный участник передаёт `intent: "cancel"`,
содержательную причину и явные `source: null`, `result_commit: null`.
Отмена применима и до входа в первый этап; терминальное доказательство для неё
не требуется. Это отдельное решение участника, а не вывод из ошибки проверки.

## Пакет и результат

Корень пакета содержит ровно `schema: "stride-execution/v1"`,
`operation: "outcome"`, `input`. В `input` обязательны все 11 полей:

| Поле | Значение |
| --- | --- |
| `request_id` | Непустой идентификатор этого обращения |
| `expected_revision` | Текущая целая revision собственного store, от нуля |
| `task_id` | Каноническая identity по настроенной политике |
| `expected_generation` | Текущее поколение, целое от единицы |
| `expected_contract_revision` | Текущая непустая редакция договора исполнения |
| `actor_id` | Известный участник с ролью из `outcomes.roles` |
| `outcome_id` | Непустой адрес нового факта, уникальный среди исходов Execution |
| `intent` | `accept` либо `cancel` |
| `reason` | Непустое содержательное объяснение решения |
| `source` | Для accept объект ровно `{commit, tree}` допущенного результата; для cancel null |
| `result_commit` | Для положительного исхода явный null либо commit из source; для остальных null |

Значения `commit` и `tree` — фактические непустые идентификаторы источника
доказательства. Их происхождение сохраняется вместе с результатом.
Тип исхода и удовлетворённые ключи определяет Execution по процессу и снимку.

Успешный ответ имеет ровно шесть полей:
`schema`, `status: "outcome_recorded"`, `revision`, `replayed`, `changed`, `fact`.
Новая запись возвращает `changed: true`, `replayed: false`. В `fact` ровно 14 полей:

| Поле | Происхождение и смысл |
| --- | --- |
| `outcome_id`, `task_id` | Адрес факта и каноническая задача |
| `generation`, `contract_revision` | Поколение и договор принятой работы |
| `requirements_snapshot_revision` | Опубликованный снимок из frozen execution contract |
| `kind` | `positive`, `negative` либо `cancelled` |
| `stage_id`, `iteration` | Фактическая позиция процесса при решении |
| `handler_outcome` | Настоящий исход обработчика; для отмены null |
| `source` | Принятые commit/tree; для отмены null |
| `provenance` | Исходное и текущее доказательства по полям ниже |
| `satisfied_task_requirements` | Отсортированные ключи current-требований положительного снимка; для остальных [] |
| `result_commit` | Явно переданная допустимая ссылка результата либо null |
| `decision` | Участник, его роль, intent и reason |

`provenance` содержит ровно `visit_id`, `source_generation`, `source_actor_ids`,
`gate_id`, `evidence_id`, `current_gate_id`, `current_evidence_id`. Первые пять
описывают исходный принятый визит; последние два — действительное текущее
подтверждение. После перемотки это могут быть разные поколения и участники.
Для отмены все эти ссылки равны null, а `source_actor_ids` равен `[]`.
`decision` имеет ровно `actor_id`, `role_id`, `intent`, `reason`.

Положительность определяется положительным исходом действительного обработчика
терминального этапа. Отрицательный факт сохраняет настоящий `not_satisfied` либо
`inconclusive` и пустой список удовлетворённых требований. У реального
опубликованного снимка с одними future-требованиями положительный список тоже
пуст: удовлетворённость связанных требований определяет Requirements.

## Примеры обращения к CLI

Следующие Python-блоки составляют один пример; загрузите их в указанном порядке.
Они определяют функции для реальных настроенных команд. Перед вызовом получите
актуальные данные из своих публичных чтений и принятого доказательства:

| Аргумент | Источник |
| --- | --- |
| `command` | Объект с `argv` выбранного Python, абсолютного app-owned CLI, `--config`, абсолютного рабочего файла и явным `cwd` |
| `revision`, `generation`, `contract_revision` | Текущее `query/execution` выбранной Task |
| `source` | Commit/tree фактически допущенного терминального доказательства |
| `task_id`, `actor_id` | Каноническая задача и настроенный участник |
| `request_id`, `outcome_id` | Выбранные новые идентификаторы обращения и факта |

Функция запускает реальный процесс, читает полный JSON и проверяет exit/stderr.

```python
import json
import subprocess


def owner_call(command, packet):
    completed = subprocess.run(
        command["argv"], cwd=command["cwd"],
        input=json.dumps(packet, ensure_ascii=False) + "\n",
        text=True, capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout
    assert completed.stderr == "", completed.stderr
    return json.loads(completed.stdout)
```

Для принятия передайте source и явный `result_commit`: commit для результата,
доступного последующим result-зависимостям, либо null. Для отрицательного исхода
передайте null. Вызов возвращает полный факт в `response["fact"]`.

```python
def accept_outcome(command, *, request_id, revision, task_id, generation,
                   contract_revision, actor_id, outcome_id, reason,
                   source, result_commit):
    packet = {
        "schema": "stride-execution/v1", "operation": "outcome",
        "input": {
            "request_id": request_id, "expected_revision": revision,
            "task_id": task_id, "expected_generation": generation,
            "expected_contract_revision": contract_revision,
            "actor_id": actor_id, "outcome_id": outcome_id,
            "intent": "accept", "reason": reason, "source": source,
            "result_commit": result_commit,
        },
    }
    response = owner_call(command, packet)
    assert response["status"] == "outcome_recorded"
    return packet, response
```

Для явной отмены используйте ту же текущую identity и договор:

```python
def cancel_outcome(command, *, request_id, revision, task_id, generation,
                   contract_revision, actor_id, outcome_id, reason):
    packet = {
        "schema": "stride-execution/v1", "operation": "outcome",
        "input": {
            "request_id": request_id, "expected_revision": revision,
            "task_id": task_id, "expected_generation": generation,
            "expected_contract_revision": contract_revision,
            "actor_id": actor_id, "outcome_id": outcome_id,
            "intent": "cancel", "reason": reason,
            "source": None, "result_commit": None,
        },
    }
    response = owner_call(command, packet)
    assert response["status"] == "outcome_recorded"
    return packet, response
```

## Адресное чтение и повтор

Запрос содержит ровно `id`, `kind: "outcome"`, `task_id`, `outcome_id`.
Корень ответа — `schema`, `status: "read_only"`, текущая `revision`, `results`.
Элемент — ровно `id`, `kind`, `result`; `result` содержит полный сохранённый факт.

```python
def read_outcome(command, *, task_id, outcome_id):
    response = owner_call(command, {
        "schema": "stride-execution/v1", "operation": "query",
        "input": {"queries": [{
            "id": "outcome-read", "kind": "outcome",
            "task_id": task_id, "outcome_id": outcome_id,
        }]},
    })
    assert response["status"] == "read_only"
    return response["results"][0]["result"]
```

Для точного повтора сохраните и повторно передайте весь исходный пакет, включая
его `request_id` и `expected_revision`. Возвращаются исходные fact/revision и
`replayed: true`; текущее значение revision заново к такому повтору не применяется.
Для нового обращения с тем же адресом и всеми девятью прежними деловыми полями
используйте новый request_id и свежую revision: ответ содержит прежний факт,
`changed: false`, `replayed: false`, а store сохраняет новое обращение и revision+1.

Новое решение закрывает текущую работу. Позиция процесса, поколение, рабочие
материалы и данные получателей сохраняются. Для следующего решения сначала
начните действительную новую работу через допустимый enter/rework/restart.
Согласованный rework в том же поколении и restart в новом допускают новый исход.
Чтение, freeze, present и воспроизведение истории сохраняют границу принятого
решения. Старый факт остаётся адресуемым; его повтор относится к старой работе.

Факт, граница решения, ответ по request_id и новая revision записываются одной
локальной транзакцией. При отказе записи вызывающий читает фактическое состояние
через владельца, устраняет указанную причину и повторяет исходное обращение,
если результат ещё не был сохранён. Для содержательно исправленного обращения
выберите новый request_id и актуальную revision.

## Явная передача отдельным приложениям

Вызывающая сторона получает fact и сама передаёт две проекции. В Orchestrator
настройте команды Requirements и Sequencing по
[действующему договору оболочки](../../orchestrator/docs/shell-orc.md#command-and-configuration):
`owner-json-v1`, явные argv/cwd/env, время ожидания и допустимые exit codes.
Сами приложения используют свои конфигурации и хранилища.

Requirements получает snapshot revision и ключи через
[record_outcome](../../requirements/docs/contract-req.md#57-record_outcome).
Sequencing получает kind, ссылку результата и адрес источника через
[record_execution_outcome](../../sequencing/docs/contract-seq.md#512-record_execution_outcome).
Зарегистрируйте ту же Task у получателя. Перед новой передачей Task claim должен
быть свободен; при существующем claim завершите его работу и освободите через
владельца. Workspace необязателен; если он есть, подготовка должна выйти из
reserved/release_pending. Ready, failed и released контексты сохраняются.

Функции строят точные проекции. `revision` берётся из публичного чтения получателя;
`request_id` выбирается отдельно для каждого нового обращения.

```python
def requirements_outcome(fact, *, request_id, revision):
    return {"operation": "apply", "input": {
        "request_id": request_id, "expected_revision": revision,
        "operations": [{"kind": "record_outcome", "outcome": {
            "outcome_id": fact["outcome_id"], "task_id": fact["task_id"],
            "snapshot_revision": fact["requirements_snapshot_revision"],
            "kind": fact["kind"],
            "satisfied_task_requirements": fact["satisfied_task_requirements"],
        }}],
    }}


def sequencing_outcome(fact, *, request_id, revision):
    return {"schema": "stride-sequencing/v1", "operation": "apply", "input": {
        "request_id": request_id, "expected_revision": revision,
        "operations": [{"id": "outcome", "kind": "record_execution_outcome",
            "outcome": {
                "outcome_id": fact["outcome_id"], "task_id": fact["task_id"],
                "kind": fact["kind"], "result_commit": fact["result_commit"],
                "source_ref": {"owner": "execution", "id": fact["outcome_id"]},
            }}],
    }}


def deliver_outcome(orchestrator, *, command_id, request_id, packet):
    response = owner_call(orchestrator, {
        "schema": "stride-orchestrator/v1", "operation": "invoke",
        "input": {"request_id": request_id,
                  "command_id": command_id, "packet": packet},
    })
    assert response["status"] == "delivered"
    child = response["result"]
    assert child["exit_code"] == 0, child
    return child["packet"]
```

Вызовите deliver_outcome отдельно для каждой проекции с фактическим command_id
и своим request_id доставки. Проверьте деловой результат дочернего ответа и
прочитайте `current_satisfaction` у Requirements, `task`/`execution_facts` у
Sequencing. `delivered` подтверждает транспорт, а принятие факта определяется
дочерним ответом. Positive даёт completed, negative — available, cancelled —
cancelled; ранее завершённая Task сохраняет своё решение. Positive с null commit
позволяет логическое завершение, а result-зависимость остаётся
`missing_predecessor_result`; completion-зависимости достаточно completed.

Если принял только один получатель, сохраните его успех и тот же полный факт.
После исправления конкретного условия у второго получателя передайте ему тот же
outcome_id с новым request_id, свежей его revision и новым request_id Orchestrator.
Точное воспроизведение старой доставки читает её прежний ответ. Создание исхода
и доставки получателям имеют отдельные локальные транзакции.

## Продолжение и проверки

Обычный отказ имеет `schema`, `status: "rejected"`, содержательный `reason`,
exit 2 и пустой stderr. Прочитайте актуальное исполнение либо известный адрес
факта, приведите вход и явную конфигурацию к описанному договору через их владельцев
и отправьте новое корректное обращение. При принятом решении используйте
существующий факт; новую работу начинайте через допустимый lifecycle.

Из корня checkout выбранным тестовым Python выполните:

```sh
python -B apps/execution/tests/test_outcome_facts.py
python -B apps/execution/tests/test_shell.py --suite behavior
```

Семь групп проверяют полный факт, реальные получатели, отмену, адреса и повторы,
полномочия и существенные входы, атомарность и частичную передачу; smoke содержит
восемь проверок оболочки. Полные подробности исходов сохраняются в
`POISE_RUN_OUTPUT_DIR/outcome-facts-details.json`, когда этот каталог задан
запускающим владельцем. Самостоятельные [runner и reuse](verification-exe.md) имеют действующие отдельные
команды; физическая интеграция сохраняет своего владельца.

Для выбранного сохранённого итога оператор отдельно регистрирует
[обязанность очистки](cleanup-exe.md#владельцы-и-правильный-порядок).
Execution выдаёт адресуемую работу, вызывающий передаёт её физическому владельцу
и возвращает фактическое наблюдение. Решение о сохранении, подтверждённой интеграции
либо разрешённом удалении задаётся явно через самостоятельный API очистки.
Его полномочия, историческое чтение и продолжение описаны в собственном руководстве.

## Принятый результат reuse

[Повторное использование](verification-exe.md#повторное-использование-принятого-результата)
предоставляет prepare/verify/accept поверх принятого связанного исхода и свежей
локальной проверки. Явное собственное accept создаёт positive-факт с обычными
полями и дополнительным `reuse: {source_task_id,source_outcome_id,reuse_id}`.
Provenance сохраняет исходные visit/source_generation/source_actor_ids/gate_id/
evidence_id и текущие current_gate_id/current_evidence_id.

Обычное решение accept/cancel и reuse/accept относятся к одной области текущей
работы. Используйте сохранённый итог этой области; последующая действительная
работа открывается через lifecycle. Reuse сохраняет текущий курсор процесса и
handler_outcome; его полный факт передаётся существующим
[получателям](#явная-передача-отдельным-приложениям) явно.
