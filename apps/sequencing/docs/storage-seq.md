# Хранилище Sequencing: формат 1

## Реализованная граница

Sequencing хранит свои Task references, Sprint membership/dependencies, ownership,
Workspace roots и историю в собственной SQLite БД. Канонический Task ID приходит
от Requirements и сохраняется как строка. Содержимое Requirements и состояние
этапов Execution остаются у своих владельцев.

Текущий CLI записывает `register_task` и читает `query` с `kind: "task"`.
Чтение уже собирает полное значение из таблиц формата 1. Команды изменения Sprint,
eligibility, claim/release/resume, физического Workspace и terminal/transfer
остаются будущими возможностями [целевого контракта](contract-seq.md).
[Конфигурация и запуск](shell-seq.md) используют прежние обязательные параметры;
новых параметров для подготовки схемы нет.

## Таблицы и ограничения

| Таблица | Содержимое и основные ограничения |
| --- | --- |
| `meta` | Локальная revision владельца, ключ `revision` |
| `tasks` | Канонический `task_id`, Requirements snapshot revision, state, terminal JSON |
| `requests` | Request ID и сохранённый replay receipt с прежним fingerprint |
| `sprints` | Текущие revision и state Sprint |
| `sprint_revisions` | История: составной ключ Sprint/revision, FK на Sprint |
| `sprint_members` | Составной ключ Sprint/Task, одна membership на Task, FK на обоих владельцев |
| `sprint_dependencies` | Ключ Sprint/predecessor/successor; обе Task принадлежат этому Sprint; виды `completion` и `result` |
| `task_claims` | Исторические claims; одновременно одна active claim на Task и одна на claimant |
| `workspaces` | Repository, base commit, branch, status и read-only признак; один unreleased Workspace на Task |
| `workspace_roots` | Ключ Workspace/kind/position; виды `code`, `main`, `artifact`; путь каждого root |
| `workspace_bindings` | Одна session binding на Workspace, одна Workspace binding на session |
| `sequencing_history` | Порядковый `seq`, ссылки на Task/Sprint/Workspace, actor, event, data и at |

Requirements snapshot revision, текущая и историческая Sprint revision имеют
SQLite storage class `integer` и значение больше нуля. Position root имеет
storage class `integer` и значение от нуля. Применяется обычная INTEGER affinity
SQLite: значение, которое SQLite точно преобразует в целое, хранится как целое.
`read_only` хранится как 0/1 и возвращается в JSON как boolean.

Task state: `available`, `claimed`, `completed`, `cancelled`. Claim status:
`active`, `released`. Workspace status: `reserved`, `ready`, `release_pending`,
`released`, `failed`. Dependency соединяет разные Task одного Sprint.
Foreign keys связывают references, membership, claims, roots, bindings и history
с соответствующими записями. Поля истории сохраняют переданные данные; чтение
текущего состояния не удаляет историю.

Claim Task и binding Workspace независимы. Одна session может владеть Task и
Workspace той же либо другой Task; каждая отдельная уникальность сохраняется.
Отсутствие одного владения не означает отсутствие другого.

## Переход к формату 1

Исторический владелец — `apps.sequencing.migrations.v1.migrate(connection)`.
Он принимает SQLite connection с включённым `PRAGMA foreign_keys=ON` и без
открытой транзакции вызывающей стороны. `Store.connect` выполняет эти условия
перед каждой CLI операцией. Самостоятельный вызов возвращает `None`.

Поддержаны следующие исходные состояния:

1. Пустой пользовательский каталог БД, `PRAGMA user_version=0`: создаются
   12 таблиц и `meta.revision=0`.
2. Предшествующая самостоятельная оболочка, `user_version=0`: ровно таблицы
   `meta`, `tasks`, `requests` с исходными определениями столбцов, типов, ключей
   и обязательности значений. Служебные индексы SQLite принадлежат своим таблицам.
   `meta.revision` — целое
   от нуля; Task ID — непустая строка, snapshot revision — целое больше нуля,
   state — `available`, поля `sprint_id`, `claim_json`, `workspace_json`,
   `terminal_json` содержат SQL NULL. Идентичности, provenance, meta и bytes
   replay receipts сохраняются. Новые области создаются пустыми.
3. Уже подготовленный формат, `user_version=1`: повторный вызов сохраняет схему,
   данные и revision, в том числе заполненные Sprint, ownership, roots и history.

Исходная схема предшественника зафиксирована в
[SQL fixture](../fixtures/precursor-store.sql). При распознавании этой схемы
эквивалентны различия ASCII-регистра, пробельных разделителей SQLite, комментариев
и обычного цитирования имён таблиц и столбцов двойными кавычками, обратными
кавычками или квадратными скобками. Значимые элементы SQL и различия символов
сохраняются; распознавание проверяет исходные определения до изменения схемы.
Это переход формата собственного
приложения. Частные хранилища монолита, восстановление рабочей установки оператора
и перенумерация Task в него не входят.

Миграция использует одну транзакцию `BEGIN IMMEDIATE`, повторно читает исходное
состояние внутри неё, создаёт схему и переносит references. `user_version=1`
записывается последним. SQLite поддерживает откат использованного DDL вместе с
данными. Ошибка внутри транзакции откатывает её; connection остаётся без открытой
транзакции. Проверенная схема и данные до отказа сохраняются, повторный вызов
может выполнить переход заново после устранения причины.

Неподдержанный формат или данные источника дают `ValueError` с причиной;
ошибки SQLite передаются вызывающей стороне. CLI оформляет обычный отказ по
[своему контракту](shell-seq.md#изоляция-и-результат). Оператор выбирает
поддержанное исходное хранилище и завершает свои транзакции перед прямым вызовом.
Миграция изменяет только SQLite: рабочие деревья, файлы WIP и каталоги roots
сохраняются. Литералы схемы и данных принадлежат историческому модулю; он
самодостаточен относительно текущих моделей и конфигурации приложения.

## Текущее значение Task

`query` возвращает `task_id`, `requirements_snapshot_revision`, `state`,
`sprint_id`, `claim`, `workspace`, `terminal`. Membership берётся из
`sprint_members`, claim — только active. Workspace выбирается из всех состояний,
кроме `released`; при его отсутствии возвращается null.

Claim содержит `claim_id`, `task_id`, `claimant`, `status`. Workspace содержит
`workspace_id`, `task_id`, `repository_id`, `base_commit`, `branch`, `status`,
`read_only`, `roots`, `path`, `owner_session_id`. Каждый root содержит `kind`,
`position`, `path`; roots отсортированы по kind, затем position. Поле `path`
равно первому code-root в этом порядке, либо null у зарезервированного Workspace
без code-root. `owner_session_id` берётся из независимой Workspace binding,
либо равен null. Terminal возвращается как сохранённое JSON значение, либо null.

## Правильный запуск на отдельном state

Выполните этот Python пример из корня рабочей копии. Он использует временную
конфигурацию и трижды запускает настоящий CLI: регистрация, точный replay,
чтение. После завершения временные данные удаляются.

```python
import json
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix='sequencing-guide-') as directory:
    root = Path(directory)
    config = json.loads(Path('apps/sequencing/config/sequencing.example.json').read_text())
    config['paths']['state'] = str(root / 'state')
    config['repository']['path'] = str(Path.cwd())
    config['worktree']['root'] = str(root / 'worktrees')
    config['transfer']['root'] = str(root / 'transfer')
    config_path = root / 'sequencing.json'
    config_path.write_text(json.dumps(config))
    packet = {
        'schema': 'stride-sequencing/v1', 'operation': 'apply',
        'input': {'request_id': 'guide-register', 'expected_revision': 0,
                  'operations': [{'id': 'reg', 'kind': 'register_task',
                                  'task_id': '0042', 'requirements_snapshot_revision': 2}]},
    }
    query = {
        'schema': 'stride-sequencing/v1', 'operation': 'query',
        'input': {'queries': [{'id': 'task', 'kind': 'task', 'task_id': '0042'}]},
    }
    for request in (packet, packet, query):
        result = subprocess.run(
            [sys.executable, '-B', 'apps/sequencing/bin/sequencing',
             '--config', str(config_path)],
            input=json.dumps(request) + '\n', text=True, capture_output=True, check=True,
        )
        assert result.stderr == ''
        print(result.stdout, end='')
```

Полные ответы в порядке выполнения:

```json
{"schema":"stride-sequencing/v1","status":"applied","revision":1,"replayed":false,"results":[{"id":"reg","kind":"register_task","changed":true,"task_id":"0042","requirements_snapshot_revision":2}]}
```

```json
{"schema":"stride-sequencing/v1","status":"applied","revision":1,"replayed":true,"results":[{"id":"reg","kind":"register_task","changed":true,"task_id":"0042","requirements_snapshot_revision":2}]}
```

```json
{"schema":"stride-sequencing/v1","status":"read_only","revision":1,"results":[{"id":"task","value":{"task_id":"0042","requirements_snapshot_revision":2,"state":"available","sprint_id":null,"claim":null,"workspace":null,"terminal":null}}]}
```

## Прямой вызов на поддержанном предшественнике

Следующий пример из корня рабочей копии создаёт только временную SQLite БД
с опубликованной исходной схемой. Независимые SQL значения показывают сохранение
Task, provenance, revision и receipt, затем повторный вызов.

```python
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from apps.sequencing.migrations.v1 import migrate

with tempfile.TemporaryDirectory(prefix='sequencing-precursor-guide-') as directory:
    with closing(sqlite3.connect(Path(directory) / 'sequencing.sqlite')) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(Path('apps/sequencing/fixtures/precursor-store.sql').read_text())
        db.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('revision', 7))
        db.execute('INSERT INTO tasks(task_id,requirements_snapshot_revision,state) VALUES(?,?,?)',
                   ('0042', 2, 'available'))
        db.execute('INSERT INTO requests(request_id,digest,response_json) VALUES(?,?,?)',
                   ('earlier-request', 'preserved-fingerprint', '{"saved":true}'))
        db.commit()
        for attempt in range(2):
            assert migrate(db) is None
            assert not db.in_transaction
            assert db.execute('PRAGMA user_version').fetchone() == (1,)
            assert db.execute('SELECT task_id,requirements_snapshot_revision,state,terminal_json '
                              'FROM tasks').fetchall() == [('0042', 2, 'available', None)]
            assert db.execute('SELECT key,value FROM meta').fetchall() == [('revision', 7)]
            assert db.execute('SELECT request_id,digest,response_json FROM requests').fetchall() == [
                ('earlier-request', 'preserved-fingerprint', '{"saved":true}')]
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        print('format=1 task=0042 provenance=2 revision=7 receipt=preserved repeat=unchanged')
```

Проверки новых схем, заполненного формата 1, независимого ownership, полных
CLI проекций, SQL типов/уникальностей/FK, частичного отказа и сохранности WIP
находятся в [60 проверках хранилища](../tests/test_store_migration.py).
