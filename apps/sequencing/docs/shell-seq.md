# Самостоятельный CLI Sequencing

## Запуск

Из корня выбранной рабочей копии с настроенным Python 3.13:

```sh
apps/sequencing/bin/sequencing --config /absolute/path/sequencing.json
```

Команда получает один UTF-8 JSON request с завершающим переводом строки через stdin
и возвращает один JSON response через stdout. Wire schema — `stride-sequencing/v1`.
Провайдер находит код относительно собственного файла; `--config` задаётся явно.

Реализованы `apply` с `register_task`, `register_sprint` и `query` с
`kind: "task"`, `kind: "sprint"`, `kind: "eligibility"`, `kind: "next"`. Независимая работа — зарегистрированная Task
вне Sprint. Работа в Sprint использует ту же Task identity и явное membership.
Обе формы доступны одновременно. Ссылка ведёт на постановку Requirements либо
на её опубликованный снимок; содержимое остаётся у Requirements. Последующие
claims, физические Workspace и transfer описаны в
[целевом контракте](contract-seq.md) как будущие команды.
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
| `repository.id`, `repository.path`, `repository.base_ref` | Идентичность repository, его путь и ветка будущих Workspace |
| `worktree.root`, `worktree.branch_prefix` | Каталог и префикс веток будущих рабочих деревьев |
| `worktree.fs_timeout_seconds`, `worktree.git_timeout_seconds` | Положительные конечные пределы файловых и Git операций |
| `transfer.root`, `transfer.max_package_bytes`, `transfer.max_files`, `transfer.max_file_bytes`, `transfer.file_mode` | Каталог, положительные лимиты и режим файлов будущего transfer (целое 0–511) |
| `identifiers.task.pattern`, `identifiers.sprint.pattern` | Регулярные выражения идентификаторов |

Repository, worktree и transfer пути разрешаются относительно каталога config.
Их параметры проверяются при запуске, даже когда текущая команда их не использует.
Рабочая конфигурация принадлежит конкретной установке; в Git хранится пример.

## Изоляция и результат

Sequencing использует только свой `database` и `lock`. Requirements и Execution
передают идентичности через публичные договоры; их конфигурации и БД этим CLI
не читаются. Разные state roots дают независимые хранилища. Lock принадлежит
одному Sequencing state и удерживается на время текущей операции.

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
соответствующей границы. Проверки текущего чтения выполняют реальные CLI/SQLite операции, проверяют независимые значения,
идентичности, типы, ownership, сохранность WIP и откат транзакций.
