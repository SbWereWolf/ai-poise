# Самостоятельный CLI Sequencing

## Запуск

Из корня выбранной рабочей копии с настроенным Python 3.13:

```sh
apps/sequencing/bin/sequencing --config /absolute/path/sequencing.json
```

Команда получает один UTF-8 JSON request с завершающим переводом строки через stdin
и возвращает один JSON response через stdout. Wire schema — `stride-sequencing/v1`.
Провайдер находит код относительно собственного файла; `--config` задаётся явно.

Реализованы `apply` с `register_task`, `register_sprint`, `claim_task`,
`switch_claim`, `release_claim`, `resume_claim`, `recover_claim` и `query` с
`kind: "task"`, `kind: "sprint"`, `kind: "eligibility"`, `kind: "next"`.
Независимая работа — зарегистрированная Task вне Sprint. Работа в Sprint
использует ту же Task identity и явное membership. Обе формы доступны одновременно.
Ссылка ведёт на постановку Requirements либо на её опубликованный снимок;
содержимое остаётся у Requirements. Реализован `workspace/prepare` для постоянного
контекста Task. Изменение Sprint, независимое владение и освобождение Workspace,
terminal и transfer описаны в [целевом контракте](contract-seq.md) как будущие команды.
Точный реализованный формат, миграция и исполняемые примеры находятся в
[руководстве по хранилищу](storage-seq.md).

## Конфигурация владельца

Скопируйте `apps/sequencing/config/sequencing.example.json` в рабочий файл.
Укажите собственный `paths.state`, repository и worktree пути. Все поля схемы
`stride-sequencing-config/v1` обязательны:

```json
{
  "schema": "stride-sequencing-config/v1",
  "paths": {
    "state": "../state",
    "database": "sequencing.sqlite",
    "lock": "sequencing.lock"
  },
  "batch": {
    "max_input_bytes": 65536,
    "max_items": 256
  },
  "read": {
    "max_queries": 256
  },
  "lock": {
    "seconds": 5.0,
    "poll_seconds": 0.05
  },
  "repository": {
    "id": "ai-poise",
    "path": "../../..",
    "base_ref": "refs/heads/poise-stride-v2"
  },
  "worktree": {
    "root": "../../../projects/ai-poise/worktrees",
    "branch_prefix": "tasks/",
    "fs_timeout_seconds": 30.0,
    "git_timeout_seconds": 30.0
  },
  "transfer": {
    "root": "../transfer",
    "max_package_bytes": 67108864,
    "max_files": 4096,
    "max_file_bytes": 16777216,
    "file_mode": 384
  },
  "identifiers": {
    "task": {"pattern": "^[0-9]{4}$"},
    "sprint": {"pattern": "^[A-Z0-9-]+$"}
  }
}
```

| Поле | Назначение |
| --- | --- |
| `paths.state` | Абсолютный путь либо путь относительно каталога config |
| `paths.database`, `paths.lock` | Разные относительные пути внутри state |
| `batch.max_input_bytes`, `batch.max_items` | Положительные целые пределы запроса и операций |
| `read.max_queries` | Положительный целый предел запросов чтения |
| `lock.seconds`, `lock.poll_seconds` | Положительные конечные timeout и интервал повторного захвата локального lock |
| `repository.id`, `repository.path`, `repository.base_ref` | Идентичность repository, его путь и исходная ссылка writable Workspace |
| `worktree.root`, `worktree.branch_prefix` | Каталог и префикс веток writable рабочих деревьев |
| `worktree.fs_timeout_seconds`, `worktree.git_timeout_seconds` | Положительные конечные значения: Git timeout применяется; fs timeout зарезервирован |
| `transfer.root`, `transfer.max_package_bytes`, `transfer.max_files`, `transfer.max_file_bytes`, `transfer.file_mode` | Каталог, положительные лимиты и режим файлов будущего transfer (целое 0–511) |
| `identifiers.task.pattern`, `identifiers.sprint.pattern` | Регулярные выражения идентификаторов |

Repository, worktree и transfer пути разрешаются относительно каталога config.
Их параметры проверяются при запуске, даже когда текущая команда их не использует.
Рабочая конфигурация принадлежит конкретной установке; в Git хранится пример.

## Изоляция и результат

Sequencing использует только свой `database` и `lock`. Requirements и Execution
передают идентичности через публичные договоры; их конфигурации и БД этим CLI
не читаются. Разные state roots дают независимые хранилища. Lock принадлежит
одному Sequencing state. Apply удерживает его на время транзакции; prepare
использует только короткие транзакции до и после Git. Во время Git lock освобождён.

Перед обращением к данным `Store.connect` включает foreign keys и вызывает
[исторические переходы](storage-seq.md#переход-к-формату-2). Пустая БД и
поддержанный предшественник проходят отдельные шаги 0 → 1 → 2, формат 1 —
шаг 1 → 2. Чтение тоже может впервые подготовить схему. Существующий формат 2
сохраняется. Каждый шаг атомарен отдельно; уже завершённый шаг 0 → 1 остаётся
завершённым при последующем отказе шага 1 → 2.

Успешный новый `apply` увеличивает локальную revision на один и сохраняет receipt.
Точное повторение `request_id` и всего пакета возвращает сохранённый результат с
`replayed: true`; revision остаётся прежней. Следующий новый пакет использует
текущую `expected_revision`. Канонический `task_id` сохраняется без перенумерации.

Для обычного отказа CLI возвращает `status: "rejected"`, конкретный `reason` и
exit 2; stderr остаётся пустым. Конфигурация и пакет проверяются до создания
state. Условия поддержанного исходного хранилища и поведение отката описаны
в каноническом руководстве. Исправление рабочей конфигурации выполняет оператор.

## Регистрация выданной работы

1. Создайте постановку через Requirements с целью и выбранной формой работы.
   Используйте возвращённый ID без изменения строки.
2. Получите текущую постановку. Для неё передайте положительный
   `requirements_statement_revision`; для отдельно опубликованного снимка —
   фактический `requirements_snapshot_revision`. Это разные счётчики:
   statement revision 2 может соответствовать snapshot revision 1.
3. Зарегистрируйте Task через Sequencing. Для работы в Sprint добавьте явный
   `register_sprint` с уже зарегистрированными members и рёбрами. Это можно
   сделать следующим operation того же пакета. Читайте Task и Sprint через `query`.

Цель уже позволяет Requirements выдать ID и редакцию постановки: снимок может
оставаться `null`, основания и публикация для регистрации не требуются.
Первоначальный вид и номер ссылки сохраняются. Для того же вида и номера новый
пакет даёт `changed: false` и новую owner revision; членство остаётся прежним.
Точный повтор исходного пакета возвращает сохранённый ответ с `replayed: true`.
Перед каждым новым пакетом используйте актуальную `expected_revision`.

Согласуйте `identifiers.task.pattern` с шириной и namespace выдачи Requirements.
Основной config-пример выше рассчитан на четырёхзначные ID. Следующий отдельный
пример явно выбирает шестизначную выдачу и шестизначную consumer policy. Он
использует только временные config/state, реальные CLI и постановки только с
целью. Запустите его из корня рабочей копии с Python 3.13:

```python
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def cli(application, config, packet):
    result = subprocess.run(
        [sys.executable, '-B', 'apps/' + application + '/bin/' + application,
         '--config', str(config)],
        input=json.dumps(packet, ensure_ascii=False) + '\n',
        text=True, capture_output=True, check=True,
    )
    assert result.stderr == ''
    return json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix='sequencing-registration-guide-') as directory:
    root = Path(directory)
    req = json.loads(Path('apps/requirements/config/requirements.example.json').read_text())
    req['paths']['state'] = str(root / 'requirements-state')
    req['task_ids']['width'] = 6
    req['task_ids']['progression'] = {'first': 17, 'step': 13}
    seq = json.loads(Path('apps/sequencing/config/sequencing.example.json').read_text())
    seq['paths']['state'] = str(root / 'sequencing-state')
    seq['repository']['path'] = str(Path.cwd())
    seq['worktree']['root'] = str(root / 'worktrees')
    seq['transfer']['root'] = str(root / 'transfer')
    seq['identifiers']['task']['pattern'] = '^[0-9]{6}$'
    req_path, seq_path = root / 'requirements.json', root / 'sequencing.json'
    req_path.write_text(json.dumps(req))
    seq_path.write_text(json.dumps(seq))
    goals = ['Подготовить отчёт.', 'Собрать данные.', 'Проверить данные.']
    expected_ids = ['000017', '000030', '000043']
    issued = []
    for index, goal in enumerate(goals):
        choice = {'kind': 'standalone'} if index == 0 else {'kind': 'sequence', 'sequence_id': 'SPR-GUIDE'}
        receipt = cli('requirements', req_path, {'operation': 'create_task', 'input': {
            'request_id': 'guide-create-' + str(index), 'goal': goal, 'work_choice': choice}})
        assert receipt == {'status': 'created', 'task_id': expected_ids[index],
                           'statement_revision': 1, 'revision': index + 1, 'replayed': False}
        issued.append(receipt['task_id'])
    request = {'operation': 'query', 'input': {'queries': [
        {'id': str(index), 'kind': 'get_task', 'task_id': identifier}
        for index, identifier in enumerate(issued)]}}
    view = cli('requirements', req_path, request)
    assert view == {'status': 'read_only', 'revision': 3, 'results': [
        {'id': str(index), 'value': {'task_id': identifier, 'statement_revision': 1,
         'snapshot_revision': None, 'goal': goals[index], 'definition_of_done': [], 'task_requirements': []}}
        for index, identifier in enumerate(expected_ids)]}
    operations = [{'id': 'reg-' + str(index), 'kind': 'register_task',
                   'task_id': item['value']['task_id'],
                   'requirements_statement_revision': item['value']['statement_revision']}
                  for index, item in enumerate(view['results'])]
    operations.append({'id': 'group', 'kind': 'register_sprint', 'sprint_id': 'SPR-GUIDE',
                       'members': [issued[2], issued[1]], 'dependencies': [
                           {'predecessor': issued[1], 'successor': issued[2], 'kind': 'result'}]})
    packet = {'schema': 'stride-sequencing/v1', 'operation': 'apply',
              'input': {'request_id': 'guide-register-issued', 'expected_revision': 0, 'operations': operations}}
    expected = {'schema': 'stride-sequencing/v1', 'status': 'applied', 'revision': 1,
                'replayed': False, 'results': [
                    {'id': 'reg-' + str(index), 'kind': 'register_task', 'changed': True,
                     'task_id': identifier, 'requirements_statement_revision': 1}
                    for index, identifier in enumerate(expected_ids)] + [
                    {'id': 'group', 'kind': 'register_sprint', 'changed': True,
                     'sprint_id': 'SPR-GUIDE', 'sprint_revision': 1}]}
    registered = cli('sequencing', seq_path, packet)
    assert registered == expected
    assert cli('sequencing', seq_path, packet) == {**expected, 'replayed': True}
    queries = [{'id': 'task-' + str(index), 'kind': 'task', 'task_id': identifier}
               for index, identifier in enumerate(issued)]
    queries.append({'id': 'group', 'kind': 'sprint', 'sprint_id': 'SPR-GUIDE'})
    current = cli('sequencing', seq_path, {
        'schema': 'stride-sequencing/v1', 'operation': 'query', 'input': {'queries': queries}})
    assert current == {'schema': 'stride-sequencing/v1', 'status': 'read_only', 'revision': 1,
        'results': [{'id': 'task-' + str(index), 'value': {'task_id': identifier,
                     'requirements_statement_revision': 1, 'state': 'available',
                     'sprint_id': None if index == 0 else 'SPR-GUIDE',
                     'claim': None, 'workspace': None, 'terminal': None}}
                    for index, identifier in enumerate(expected_ids)] + [
                    {'id': 'group', 'value': {'sprint_id': 'SPR-GUIDE', 'sprint_revision': 1,
                     'members': ['000030', '000043'], 'dependencies': [
                         {'predecessor': '000030', 'successor': '000043', 'kind': 'result'}]}}]}
    assert cli('requirements', req_path, request) == view
    print(json.dumps({'registration': registered, 'read': current}, ensure_ascii=False))
```

В полном результате `000017` остаётся Independent с `sprint_id: null`, а `000030`
и `000043` входят в `SPR-GUIDE`; все три ссылки имеют statement revision 1.
Повторная регистрация того же состава и рёбер новым outer request сравнивает
множества: входной порядок списков не меняет определение Sprint. Первая Sprint
получает revision 1 с записью истории; первоначальный граф допускает несколько
корней. Изменение существующего определения относится к будущему `revise_sprint`.

При обработке обычных конфликтов используйте возвращённый `reason`:
`identity_conflict` относится к исходной ссылке Task, `already_exists` —
определению существующего Sprint, `dependency_conflict` — составу/рёбрам,
`request_reuse_conflict` — сохранённому запросу, `revision_conflict` — редакции
владельца. Новый запрос оформляется отдельным `request_id` с текущей revision;
точный повтор использует весь исходный пакет. Владелец сохраняет атомарность
пакета и его receipt.

## Доступность и следующая работа

Передайте канонический ID текущей Session, уже выданный её публичным владельцем,
как непустой `claimant`. Используйте его одинаково в `eligibility` и `next`.
Идентификаторы query уникальны внутри packet. Конфигурация остаётся прежней.

`eligibility` принимает `id`, `kind`, `task_id`, `claimant` и возвращает
`task_id`, `eligible`, `blockers`, `result_provenance`. `next` принимает
`id`, `kind`, `claimant`, `scope`; значение содержит только `task_ids`.
Область — ровно `{"kind": "all"}`, `{"kind": "independent"}` либо
`{"kind": "sprint", "sprint_id": "SPR-GUIDE"}`. Все ID сортируются как строки.

Работа доступна при выполнении всех условий: она nonterminal, действующий
Task claim свой либо отсутствует, все предшественники completed. Для
result-ребра требуется сохранённый commit результата; completion-ребру
достаточно completed. Cancelled предшественник оставляет зависимость
невыполненной. Собственная захваченная работа проверяется по тем же зависимостям;
чужая получает причину `claimed_by_other`. Владение Workspace самостоятельно
и сохраняет своё назначение при любом владельце Task. Выбор кандидатов
показывает доступность; приобретение ресурсов имеет собственный будущий контракт.

Порядок `blockers`: `task_terminal`, затем `claimed_by_other`, затем все
причины предшественников в порядке ID. Их причины — `predecessor_not_completed`
либо `missing_predecessor_result`. Ссылки `{predecessor, result_commit}`
на завершённые result-предшественники сохраняются в `result_provenance`,
даже если другое условие делает Task недоступной. Точные поля каждого варианта —
в [контракте чтения](contract-seq.md#72-eligibility).

Первоначально зарегистрированный Sprint участвует сразу. Чтение подготовленного
формата 2 сохраняет данные и revision; все queries видят один снимок.
Неизвестная Task даёт `task_not_found`, неизвестный именованный Sprint —
`sprint_not_found`; текущий отказ относится ко всему packet и возвращает exit 2.

Следующий пример запускается из корня рабочей копии с Python 3.13. Он использует
временные данные и канонические четырёхзначные ID, переданные Requirements.
`SESSION-GUIDE` обозначает уже выданный ID Session вызывающего пользователя.

```python
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def cli(config, operation, data):
    result = subprocess.run(
        [sys.executable, '-B', 'apps/sequencing/bin/sequencing', '--config', str(config)],
        input=json.dumps({'schema': 'stride-sequencing/v1', 'operation': operation,
                          'input': data}) + '\n',
        text=True, capture_output=True, check=True,
    )
    assert result.stderr == ''
    return json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix='sequencing-availability-guide-') as directory:
    root = Path(directory)
    config = json.loads(Path('apps/sequencing/config/sequencing.example.json').read_text())
    config['paths']['state'] = str(root / 'state')
    config['repository']['path'] = str(Path.cwd())
    config['worktree']['root'] = str(root / 'worktrees')
    config['transfer']['root'] = str(root / 'transfer')
    path = root / 'sequencing.json'
    path.write_text(json.dumps(config))
    operations = [
        {'id': 'register-' + identifier, 'kind': 'register_task',
         'task_id': identifier, 'requirements_statement_revision': 1}
        for identifier in ['0041', '0042', '0043']]
    operations.append({'id': 'register-group', 'kind': 'register_sprint',
        'sprint_id': 'SPR-GUIDE', 'members': ['0042', '0043'], 'dependencies': [
            {'predecessor': '0042', 'successor': '0043', 'kind': 'result'}]})
    registered = cli(path, 'apply', {'request_id': 'availability-guide-register',
                                   'expected_revision': 0, 'operations': operations})
    assert registered == {'schema': 'stride-sequencing/v1', 'status': 'applied',
        'revision': 1, 'replayed': False, 'results': [
            {'id': 'register-' + identifier, 'kind': 'register_task',
             'task_id': identifier, 'requirements_statement_revision': 1, 'changed': True}
            for identifier in ['0041', '0042', '0043']] + [
            {'id': 'register-group', 'kind': 'register_sprint', 'sprint_id': 'SPR-GUIDE',
             'sprint_revision': 1, 'changed': True}]}
    queries = [
        {'id': 'root', 'kind': 'eligibility', 'task_id': '0042', 'claimant': 'SESSION-GUIDE'},
        {'id': 'successor', 'kind': 'eligibility', 'task_id': '0043', 'claimant': 'SESSION-GUIDE'},
        {'id': 'all', 'kind': 'next', 'claimant': 'SESSION-GUIDE', 'scope': {'kind': 'all'}},
        {'id': 'independent', 'kind': 'next', 'claimant': 'SESSION-GUIDE',
         'scope': {'kind': 'independent'}},
        {'id': 'group', 'kind': 'next', 'claimant': 'SESSION-GUIDE',
         'scope': {'kind': 'sprint', 'sprint_id': 'SPR-GUIDE'}},
    ]
    expected = {'schema': 'stride-sequencing/v1', 'status': 'read_only', 'revision': 1,
        'results': [
            {'id': 'root', 'value': {'task_id': '0042', 'eligible': True,
                                    'blockers': [], 'result_provenance': []}},
            {'id': 'successor', 'value': {'task_id': '0043', 'eligible': False,
                'blockers': [{'predecessor': '0042', 'kind': 'result',
                              'reason': 'predecessor_not_completed'}], 'result_provenance': []}},
            {'id': 'all', 'value': {'task_ids': ['0041', '0042']}},
            {'id': 'independent', 'value': {'task_ids': ['0041']}},
            {'id': 'group', 'value': {'task_ids': ['0042']}},
        ]}
    current = cli(path, 'query', {'queries': queries})
    assert current == expected
    assert cli(path, 'query', {'queries': queries}) == expected
    print(json.dumps({'registration': registered, 'read': current}, ensure_ascii=False))
```

## Проверка

Команды из корня рабочей копии:

```sh
python -B apps/sequencing/tests/test_shell.py --suite behavior
python -B apps/sequencing/tests/test_shell.py --suite documentation
python -B -m pytest -q apps/sequencing/tests/test_eligibility.py
PYTHONPATH=src python -B tools/check_documentation.py --checkout . --changed apps/sequencing/docs --changed docs/architecture/library-api.md
```

Исторические проверки регистрации и переходов хранилища находятся в
`test_registration.py` и `test_store_migration.py`; их выбирают при изменении
соответствующей границы. Проверки текущего чтения выполняют реальные CLI/SQLite операции, проверяют
независимые значения,
идентичности, типы, ownership, сохранность WIP и откат транзакций.

## Владение, переключение и восстановление

Передайте `claimant` как каноническую непрозрачную identity текущей Session,
полученную от её владельца. Одна Session владеет одной Task, одна Task — одной
Session во всём state, включая Independent и разные Sprint. Используйте реальные
ID Task и исходную ссылку Requirements. Claim и handoff ID сохраняйте из ответа.

Все операции идут внутри обычного `apply` с `request_id`, текущей
`expected_revision` и `operations`. Каждое поле указанного варианта обязательно;
дополнительных полей у него нет. `id`, caller, claim, handoff, намерение, решение
и основание — непустые строки; Task ID соответствует `identifiers.task.pattern`.
`writers_stopped` передаётся JSON boolean `true` после фактической остановки писателя.

| `kind` | Поля operation, кроме общих `id`, `kind` |
| --- | --- |
| `claim_task` | `task_id`, `claimant`, `claim_request_id` |
| `switch_claim` | `from_task_id`, `from_claim_id`, `task_id`, `claimant`, `claim_request_id`, `reason` |
| `release_claim` | `task_id`, `claim_id`, `claimant`, `reason` |
| `resume_claim` | `task_id`, `claimant`, `handoff_id` |
| `recover_claim` | `task_id`, `claim_id`, `operator`, `decision`, `writers_stopped`, `reason` |

1. Прочитайте `eligibility` выбранной Task и получите claim через `claim_task`.
   Доступность при изменении проверяется заново вместе с ёмкостью Session.
2. Для перехода с A на B используйте `switch_claim` с текущим claim A. Проверка
   обеих сторон и изменение двух claims входят в одну транзакцию. Отказ сохраняет A.
3. Передайте работу через `release_claim`, сохранив возвращённый `handoff_id`.
   Получатель вызывает `resume_claim`, создающий новый claim и использующий handoff
   один раз. Release оставляет Task доступной; завершение — отдельный факт.
4. После остановки прежнего писателя оператор читает current claim и передаёт
   явное решение в `recover_claim`. Операция освобождает только названный claim.
   Следующая Session продолжает через обычный `resume_claim`.

Перед каждым новым внешним пакетом берите актуальную revision. Повторяйте
неопределённый исход тем же полным внешним пакетом и `request_id`: точный outer
replay возвращает прежний receipt с `replayed: true` до проверки revision.
Для `claim_task`/`switch_claim` `claim_request_id` сохраняет всё бизнес-намерение,
кроме operation `id`. Повтор того же намерения новым внешним request возвращает
исторические ID с `changed: false`, `claim_replayed: true`; новая revision и
receipt сохраняются один раз, текущие бизнес-данные остаются прежними. Для
следующего действия используйте current query и свежий current claim.

Task claim и Workspace binding независимы. Эти пять операций сохраняют Workspace
metadata, bindings, roots и файлы. Последовательность не требует физического
Workspace. Подготовленный контекст сохраняется при release/resume и используется
новым законным claim через prepare. Полные результаты, причины отказов и их порядок принадлежат
[контракту](contract-seq.md#54-claim_task), история —
[руководству хранилища](storage-seq.md#атомарное-владение-и-история).

Следующий пример использует две уже выданные Task identity и известную исходную
редакцию Requirements. Он запускает реальный CLI на четырёх отдельных временных
state: statement/snapshot × Independent/Sprint. Две Session и оператор представлены
явными непрозрачными строками. Все команды синхронны и завершают свой процесс;
к моменту операторского recovery прежний CLI-писатель уже остановлен.
Запустите из корня рабочей копии с Python 3.13:

```python
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def cli(path, packet):
    result = subprocess.run(
        [sys.executable, '-B', 'apps/sequencing/bin/sequencing', '--config', str(path)],
        input=json.dumps(packet, ensure_ascii=False) + '\n',
        text=True, capture_output=True, check=True,
    )
    assert result.stderr == ''
    return json.loads(result.stdout)


def claim_result(operation, result, task_id, caller, extra=None):
    claim_id = result['claim_id']
    assert isinstance(claim_id, str) and claim_id
    expected = {'id': operation['id'], 'kind': operation['kind'], 'changed': True,
                'task_id': task_id, 'claim_id': claim_id, 'claimant': caller,
                'claim_replayed': False, **(extra or {})}
    assert result == expected
    return claim_id


for reference in ['statement', 'snapshot']:
    for form in ['independent', 'sprint']:
        with tempfile.TemporaryDirectory(prefix='sequencing-ownership-guide-') as directory:
            root = Path(directory)
            config = json.loads(Path('apps/sequencing/config/sequencing.example.json').read_text())
            config['paths']['state'] = str(root / 'state')
            config['repository']['path'] = str(Path.cwd())
            config['worktree']['root'] = str(root / 'worktrees')
            config['transfer']['root'] = str(root / 'transfer')
            config['identifiers']['task']['pattern'] = '^[0-9]{4}$'
            path = root / 'sequencing.json'
            path.write_text(json.dumps(config))
            revision, sequence = 0, 0
            field = 'requirements_' + reference + '_revision'
            owner, receiver, operator = 'SESSION-A', 'SESSION-B', 'SESSION-OPERATOR'

            def apply(operation):
                global revision, sequence
                sequence += 1
                packet = {'schema': 'stride-sequencing/v1', 'operation': 'apply',
                          'input': {'request_id': 'guide-' + str(sequence),
                                    'expected_revision': revision, 'operations': [operation]}}
                response = cli(path, packet)
                assert response == {'schema': 'stride-sequencing/v1', 'status': 'applied',
                                    'revision': revision + 1, 'replayed': False,
                                    'results': [response['results'][0]]}
                revision = response['revision']
                return response['results'][0], packet, response

            def tasks():
                response = cli(path, {'schema': 'stride-sequencing/v1', 'operation': 'query',
                    'input': {'queries': [{'id': identifier, 'kind': 'task', 'task_id': identifier}
                                          for identifier in ['0042', '0043']]}})
                assert set(response) == {'schema', 'status', 'revision', 'results'}
                assert response['schema'] == 'stride-sequencing/v1'
                assert response['status'] == 'read_only' and response['revision'] == revision
                values = [item['value'] for item in response['results']]
                for identifier, item, value in zip(['0042', '0043'], response['results'], values):
                    assert item == {'id': identifier, 'value': value}
                    assert set(value) == {'task_id', field, 'state', 'sprint_id', 'claim',
                                          'workspace', 'terminal'}
                    assert value['task_id'] == identifier and value[field] == 2
                    assert value['sprint_id'] == (None if form == 'independent' else 'SPR-OWNERSHIP')
                    assert value['workspace'] is None and value['terminal'] is None
                return values

            for identifier in ['0042', '0043']:
                result, _, _ = apply({'id': 'register-' + identifier, 'kind': 'register_task',
                                      'task_id': identifier, field: 2})
                assert result == {'id': 'register-' + identifier, 'kind': 'register_task',
                                  'task_id': identifier, field: 2, 'changed': True}
            if form == 'sprint':
                result, _, _ = apply({'id': 'sprint', 'kind': 'register_sprint',
                    'sprint_id': 'SPR-OWNERSHIP', 'members': ['0042', '0043'], 'dependencies': []})
                assert result == {'id': 'sprint', 'kind': 'register_sprint', 'changed': True,
                                  'sprint_id': 'SPR-OWNERSHIP', 'sprint_revision': 1}
            claim = {'id': 'claim-a', 'kind': 'claim_task', 'task_id': '0042',
                     'claimant': owner, 'claim_request_id': 'start-a'}
            result, original_packet, original_response = apply(claim)
            claim_a = claim_result(claim, result, '0042', owner)
            switch = {'id': 'switch-b', 'kind': 'switch_claim', 'from_task_id': '0042',
                      'from_claim_id': claim_a, 'task_id': '0043', 'claimant': owner,
                      'claim_request_id': 'move-a-b', 'reason': 'Перейти к доступной Task B'}
            result, _, _ = apply(switch)
            handoff_a = result['handoff_id']
            assert isinstance(handoff_a, str) and handoff_a
            claim_b = claim_result(switch, result, '0043', owner, {
                'previous_task_id': '0042', 'previous_claim_id': claim_a, 'handoff_id': handoff_a})
            before = tasks()
            assert before[0]['state'] == 'available' and before[0]['claim'] is None
            assert before[1]['state'] == 'claimed'
            assert before[1]['claim'] == {'claim_id': claim_b, 'task_id': '0043',
                                           'claimant': owner, 'status': 'active'}
            assert cli(path, original_packet) == {**original_response, 'replayed': True}
            assert tasks() == before
            replay, _, _ = apply({**claim, 'id': 'historical-a'})
            assert replay == {**original_response['results'][0], 'id': 'historical-a',
                              'changed': False, 'claim_replayed': True}
            assert tasks() == before

            release = {'id': 'release-b', 'kind': 'release_claim', 'task_id': '0043',
                       'claim_id': claim_b, 'claimant': owner, 'reason': 'Передать работу'}
            result, _, _ = apply(release)
            handoff_b = result['handoff_id']
            assert isinstance(handoff_b, str) and handoff_b
            assert result == {'id': 'release-b', 'kind': 'release_claim', 'changed': True,
                              'task_id': '0043', 'claim_id': claim_b, 'handoff_id': handoff_b}
            resume = {'id': 'resume-b', 'kind': 'resume_claim', 'task_id': '0043',
                      'claimant': receiver, 'handoff_id': handoff_b}
            result, _, _ = apply(resume)
            resumed = claim_result(resume, result, '0043', receiver)
            assert resumed != claim_b
            recovery = {'id': 'recover-b', 'kind': 'recover_claim', 'task_id': '0043',
                        'claim_id': resumed, 'operator': operator, 'writers_stopped': True,
                        'decision': 'Освободить остановленного писателя',
                        'reason': 'Прежний процесс завершён оператором'}
            result, _, _ = apply(recovery)
            recovered_handoff = result['handoff_id']
            assert isinstance(recovered_handoff, str) and recovered_handoff
            assert result == {'id': 'recover-b', 'kind': 'recover_claim', 'changed': True,
                              'task_id': '0043', 'claim_id': resumed,
                              'handoff_id': recovered_handoff}
            assert tasks()[1]['state'] == 'available' and tasks()[1]['claim'] is None
            continuation = {'id': 'continue-b', 'kind': 'resume_claim', 'task_id': '0043',
                            'claimant': owner, 'handoff_id': recovered_handoff}
            result, _, _ = apply(continuation)
            final_claim = claim_result(continuation, result, '0043', owner)
            assert final_claim not in [claim_b, resumed]
            result, _, _ = apply({'id': 'finish-handoff', 'kind': 'release_claim', 'task_id': '0043',
                'claim_id': final_claim, 'claimant': owner, 'reason': 'Сохранить результат этапа'})
            assert result == {'id': 'finish-handoff', 'kind': 'release_claim', 'changed': True,
                'task_id': '0043', 'claim_id': final_claim, 'handoff_id': result['handoff_id']}
            assert isinstance(result['handoff_id'], str) and result['handoff_id']
            assert all(value['state'] == 'available' and value['claim'] is None for value in tasks())
            print(json.dumps({'reference': reference, 'form': form, 'revision': revision,
                              'status': 'ownership-guide-passed'}, ensure_ascii=False))
```

Успешный пример печатает четыре записи `ownership-guide-passed`. Во всех случаях
Task сохраняют исходный вид/номер Requirements reference и membership. Внешний
replay сохраняет revision, inner replay новым пакетом увеличивает её на один,
а текущее владение B остаётся прежним. После операторского решения обычный
resume создаёт новый claim; заключительный release сохраняет доступную работу.

## Подготовка постоянного Workspace

Зарегистрируйте Task и получите действующий claim своей Session. Подготовьте
дополнительные каталоги контекста, main и artifacts. Передайте полный
[пакет prepare](contract-seq.md#60-реализованная-подготовка): три массива roots
содержат дополнительные code и все main/artifact пути в нужном порядке; допускаются
пустые массивы. Относительные пути считаются от каталога рабочего config. Встроенный
code-root добавляется автоматически первым. Публичные roots сортируются по kind,
position, а порядок внутри каждой группы сохраняется.

Для writable выберите repository.base_ref и собственный worktree.root: создаются
ветка branch_prefix + task_id и дерево worktree.root/task_id. Исходный commit
сохраняется до Git и используется при продолжении. Для read_only: true контекст
указывает на существующий repository.path с его фактическим HEAD, branch — пустая
строка; дополнительное рабочее дерево и Task branch не выделяются. Read-only —
режим контекста, файловые разрешения repository сохраняются.

Одна Task использует одного писателя подготовки. Держите её claim до завершения.
Сохраните исходный пакет целиком для точного повтора. После прерывания или Git
timeout сначала остановите исходного писателя, затем повторите пакет. При
`workspace_checkout_incomplete:` завершите checkout по сохранённым path/branch/base,
проверьте файлы, снимите initializing-lock через `git worktree unlock` и повторите
пакет. [Модель продолжения](storage-seq.md#продолжение-физической-подготовки)
описывает реальные состояния Git и сохранение материалов.

Пример ниже запускается из корня рабочей копии с Python 3.13 и установленным Git.
Он создаёт отдельные временные repository/config/state и дополнительные корни,
использует реальные CLI и освобождает временное окружение после проверок.

<!-- workspace-prepare-example -->
```python
import json
from pathlib import Path
import subprocess
import sys
import tempfile


with tempfile.TemporaryDirectory(prefix='sequencing-workspace-guide-') as directory:
    root = Path(directory)
    repo = root / 'repository'
    repo.mkdir()

    def git(*arguments):
        return subprocess.run(['git', '-C', str(repo), *arguments], text=True,
                              capture_output=True, check=True).stdout.strip()

    git('init', '-q', '--initial-branch=sprint')
    git('config', 'user.name', 'Workspace guide')
    git('config', 'user.email', 'workspace@example.test')
    git('config', 'core.hooksPath', '/dev/null')
    (repo / 'source.txt').write_text('Исходный код\n')
    git('add', 'source.txt')
    git('commit', '-qm', 'Initial source')
    config = json.loads(Path('apps/sequencing/config/sequencing.example.json').read_text())
    config['paths']['state'] = str(root / 'state')
    config['repository'].update(path=str(repo), base_ref='refs/heads/sprint')
    config['worktree']['root'] = str(root / 'worktrees')
    config['transfer']['root'] = str(root / 'transfer')
    config_path = root / 'sequencing.json'
    config_path.write_text(json.dumps(config))
    revision = 0

    def cli(operation, inputs):
        response = subprocess.run(
            [sys.executable, '-B', 'apps/sequencing/bin/sequencing',
             '--config', str(config_path)],
            input=json.dumps({'schema': 'stride-sequencing/v1',
                              'operation': operation, 'input': inputs}) + '\n',
            text=True, capture_output=True, check=True)
        assert response.stderr == ''
        return json.loads(response.stdout)

    def apply(request_id, operation):
        global revision
        response = cli('apply', {'request_id': request_id, 'expected_revision': revision,
                                 'operations': [operation]})
        revision = response['revision']
        return response['results'][0]

    for task in ('0042', '0043'):
        apply('register-' + task, {'id': 'register', 'kind': 'register_task',
              'task_id': task, 'requirements_statement_revision': 1})
    claim = apply('claim-first', {'id': 'claim', 'kind': 'claim_task', 'task_id': '0042',
                  'claimant': 'SESSION-A', 'claim_request_id': 'claim-intent-first'})
    roots = {'code': ['additional-code'], 'main': ['main-context'],
             'artifact': ['task-artifacts', 'sprint-artifacts']}
    for paths in roots.values():
        for path in paths:
            (root / path).mkdir()
    inputs = {'action': 'prepare', 'request_id': 'prepare-first', 'task_id': '0042',
              'claim_id': claim['claim_id'], 'claimant': 'SESSION-A',
              'read_only': False, 'roots': roots}
    first = cli('workspace', inputs)
    revision = first['revision']
    assert first['status'] == 'workspace_ready'
    assert len(first['workspace']['roots']) == 5
    assert cli('workspace', inputs) == {**first, 'replayed': True}
    tree = Path(first['workspace']['path'])
    (tree / 'working-notes.txt').write_text('Рабочие записи\n')
    handoff = apply('release-first', {'id': 'release', 'kind': 'release_claim',
                    'task_id': '0042', 'claim_id': claim['claim_id'],
                    'claimant': 'SESSION-A', 'reason': 'Передача следующей Session'})
    resumed = apply('resume-second', {'id': 'resume', 'kind': 'resume_claim',
                    'task_id': '0042', 'claimant': 'SESSION-B',
                    'handoff_id': handoff['handoff_id']})
    second = cli('workspace', {**inputs, 'request_id': 'prepare-second',
                 'claim_id': resumed['claim_id'], 'claimant': 'SESSION-B'})
    revision = second['revision']
    assert second['workspace'] == first['workspace']
    assert (tree / 'working-notes.txt').read_text() == 'Рабочие записи\n'
    readonly_claim = apply('claim-readonly', {'id': 'claim', 'kind': 'claim_task',
                           'task_id': '0043', 'claimant': 'SESSION-A',
                           'claim_request_id': 'claim-intent-readonly'})
    readonly = cli('workspace', {'action': 'prepare', 'request_id': 'prepare-readonly',
                   'task_id': '0043', 'claim_id': readonly_claim['claim_id'],
                   'claimant': 'SESSION-A', 'read_only': True,
                   'roots': {'code': [], 'main': [], 'artifact': []}})
    assert readonly['workspace']['path'] == str(repo)
    assert readonly['workspace']['branch'] == ''
    assert len(readonly['workspace']['roots']) == 1
    assert len(git('worktree', 'list', '--porcelain').split('worktree ')) - 1 == 2
    current = cli('query', {'queries': [{'id': 'current', 'kind': 'task', 'task_id': '0042'}]})
    assert current['results'][0]['value']['workspace'] == second['workspace']
    print('workspace=ready replay=true resume=same-context readonly=repository wip=preserved')
```
