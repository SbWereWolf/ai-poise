# Хранилище Sequencing: формат 2

## Реализованная граница

Sequencing хранит свои Task references, Sprint membership/dependencies, ownership,
Workspace roots и историю в собственной SQLite БД. Канонический Task ID приходит
от Requirements и сохраняется как строка. Содержимое Requirements и состояние
этапов Execution остаются у своих владельцев.

Текущий CLI записывает `register_task`, `register_sprint`, `claim_task`,
`switch_claim`, `release_claim`, `resume_claim`, `recover_claim`, подготавливает
Workspace через `workspace/prepare` и читает `query` с
`kind: "task"`, `kind: "sprint"`, `kind: "eligibility"` и `kind: "next"`.
Текущий формат — 2. Зарегистрированная Task
сохраняет ровно одну исходную ссылку: редакцию постановки либо опубликованного
снимка Requirements. Независимая работа и работа в Sprint используют одну
идентичность; membership задаётся явно. Изменение Sprint, независимое
владение и освобождение Workspace, terminal/transfer остаются будущими возможностями [целевого
контракта](contract-seq.md).
[Конфигурация и запуск](shell-seq.md) используют прежние обязательные параметры;
новых параметров для подготовки схемы нет.

## Согласованное чтение доступности

`Store.query` открывает одну read-транзакцию до чтения общей revision. Все
результаты packet используют один снимок своих таблиц. Для подготовленного
формата 2 чтение сохраняет бизнес-данные, receipts, историю, ownership и revision.
Первоначальная подготовка схемы остаётся отдельной процедурой
[перехода к формату 2](#переход-к-формату-2).

Доступность использует состояние самой Task, действующий Task claim и все
входящие рёбра с состояниями предшественников. Ссылка результата берётся из
сохранённого `terminal.result_commit` завершённого предшественника. Workspace
binding — самостоятельный ресурс: его владелец и наличие рабочего дерева
не заменяют владельца Task claim. Освобождённые claims остаются историей.

`next` выбирает все Task, Independent без membership либо members указанного
Sprint и применяет то же правило, что `eligibility`. Первоначально
зарегистрированный Sprint участвует сразу. Идентификаторы сортируются как строки.
Полные поля, причины и правильный исполняемый пример принадлежат
[руководству CLI](shell-seq.md#доступность-и-следующая-работа),
точные wire shapes — [контракту](contract-seq.md#72-eligibility).
Проверки этого чтения находятся в `apps/sequencing/tests/test_eligibility.py`.

## Атомарное владение и история

Все пять операций владения используют существующую `Store.apply` транзакцию
`BEGIN IMMEDIATE` и локальный lock собственного state на время операции.
Уникальные active индексы `task_claims` обеспечивают одну Session на Task и одну
Task на Session одновременно для Independent и Sprint. Полная проверка источника
и доступности цели switch выполняется до освобождения прежнего claim. Отказ
в любом месте пакета откатывает его claims, Task state, историю, receipt и revision.
Формат 2, миграции и обязательная конфигурация остаются прежними.

Чистые permissions принадлежат `domain.ownership`, последовательность действий —
`application.ownership.OwnershipCommands`, явное чтение/запись собственных SQL
фактов — `infrastructure.ownership_store.OwnershipRepository`. Adapter работает
на переданном connection, транзакцию фиксирует существующий `Store.apply`.
Зависимости проверяются общей проекцией доступности. Session передаётся как
каноническая непрозрачная строка caller; её собственное хранилище остаётся у владельца.

История append-only, с actual Task/Sprint/Workspace references, actor и временем:

| Event | Содержимое `data` |
| --- | --- |
| `claim.created` | `claim_request_id`, полный `intent` без operation `id`, исходный `result` |
| `claim.switched` | То же полное намерение и результат нового claim с ссылкой на handoff прежней Task |
| `claim.released` | Канонический `handoff`, `previous_claim`, `reason`; также при switch |
| `claim.resumed` | `consumed_handoff_id`, новый `claim` |
| `claim.recovered` | `handoff`, `previous_claim`, `reason`, `decision`, `writers_stopped: true`; actor — оператор |

Канонический `data.handoff` содержит `handoff_id`, `task_id`,
`previous_claim_id`, ровно одну первоначальную statement/snapshot revision и
`workspace_id` либо null. Сохранённый switch `data.result` ссылается на этот
handoff. Resume добавляет consumption отдельным событием; прежние строки истории
и handoff остаются неизменными. Смена Task ownership сохраняет независимые
Workspace binding, metadata, roots и WIP. Release/recovery оставляют Task
`available`, terminal прежним; recovery освобождает только старое владение.

Новый `claim_request_id` сохраняет полное намерение. Его точный повтор новым
внешним request возвращает прежние ID с `changed: false`, `claim_replayed: true`
и сохраняет текущие бизнес-данные; новый outer receipt увеличивает revision на один.
Точный повтор исходного outer packet возвращает прежний ответ с `replayed: true`
до проверки revision и сохраняет всю БД. Текущие данные читаются через `query`.
Подробные поля и порядок проверок находятся в [контракте](contract-seq.md#54-claim_task),
правильная последовательность — в [руководстве
владения](shell-seq.md#владение-переключение-и-восстановление).
[71 проверка владения](../tests/test_ownership.py) использует оба вида требований,
обе формы работы, реальные конкурентные CLI, rollback, replay и независимые ресурсы.

## Таблицы и ограничения

| Таблица | Содержимое и основные ограничения |
| --- | --- |
| `meta` | Локальная revision владельца, ключ `revision` |
| `tasks` | Канонический `task_id`, ровно одна Requirements statement/snapshot revision, state, terminal JSON |
| `requests` | Общий request ID и fingerprint: незавершённое намерение Workspace либо окончательный replay receipt |
| `sprints` | Текущие revision и state Sprint |
| `sprint_revisions` | История: составной ключ Sprint/revision, FK на Sprint |
| `sprint_members` | Составной ключ Sprint/Task, одна membership на Task, FK на обоих владельцев |
| `sprint_dependencies` | Ключ Sprint/predecessor/successor; обе Task принадлежат этому Sprint; виды `completion` и `result` |
| `task_claims` | Исторические claims; одновременно одна active claim на Task и одна на claimant |
| `workspaces` | Repository, base commit, branch, status и read-only признак; один unreleased Workspace на Task |
| `workspace_roots` | Ключ Workspace/kind/position; виды `code`, `main`, `artifact`; путь каждого root |
| `workspace_bindings` | Одна session binding на Workspace, одна Workspace binding на session |
| `sequencing_history` | Порядковый `seq`, ссылки на Task/Sprint/Workspace, actor, event, data и at |

Заполненная Requirements statement/snapshot revision, текущая и историческая Sprint revision имеют
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
Этот исторический шаг принимает SQLite connection с включённым
`PRAGMA foreign_keys=ON` и без открытой транзакции вызывающей стороны.
Самостоятельный вызов возвращает `None`. Текущий `Store.connect` выполняет шаг
для источника версии 0, затем подготавливает текущий формат 2 следующим шагом.

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

## Переход к формату 2

Исторический владелец — `apps.sequencing.migrations.v2.migrate(connection)`.
Вызов принимает действительное собственное хранилище формата 1, idle connection
и включённые foreign keys. Формат 2 возвращается без изменения схемы и данных.
`Store.connect` подготавливает свой connection и последовательно вызывает
нужные исторические шаги; моделей и конфигурации приложения внутри миграций нет.

Шаг 1 → 2 перестраивает только `tasks`. Существующий
`requirements_snapshot_revision` сохраняется точно; новый
`requirements_statement_revision` для этих строк содержит SQL NULL. Для новой
ссылки на постановку заполнен statement-столбец, snapshot-столбец содержит NULL.
CHECK требует ровно один заполненный столбец с положительным SQLite integer.
Task ID, state и terminal JSON сохраняются. Остальные 11 таблиц, членство,
зависимости, claims, Workspace, roots, bindings, история, owner revision и
сохранённые bytes receipts остаются прежними. Номер постановки из номера снимка
не вычисляется.

Перед транзакцией миграция временно отключает foreign keys только на своём
connection. Внутри `BEGIN IMMEDIATE` создаёт новую таблицу, переносит явные
столбцы, удаляет прежнюю `tasks` и переименовывает замену в `tasks`. Исходная
таблица перед переносом не переименовывается, поэтому имена ссылок дочерних
таблиц сохраняются. Затем проверяются итоговые FK, последним записывается
`user_version=2`, выполняется commit. Foreign keys включаются при выходе.

Ошибка шага 1 → 2 откатывает его DDL и данные до исходного формата 1, закрывает
транзакцию; CLI закрывает свой connection и возвращает обычный `reason`.
Для источника версии 0 шаги 0 → 1 и 1 → 2 имеют отдельные commit: успешно
созданный формат 1 остаётся после отказа следующего шага. Оператор устраняет
указанную причину и повторяет обычную команду; последующий вызов продолжает
с действительного промежуточного формата. Прямой вызов требует сначала
завершить транзакцию вызывающей стороны и включить foreign keys.

Миграция меняет собственную БД: физические рабочие деревья, файлы WIP и каталоги
roots сохраняются. Проверки включают заполненный формат 1, реальные отказы SQLite
после изменения схемы перед последним маркером, откат, закрытие connection и
успешный повтор после удаления причины отказа.

## Текущее значение Task

`query` возвращает `task_id`, ровно одно поле `requirements_statement_revision`
либо `requirements_snapshot_revision`, а также `state`, `sprint_id`, `claim`,
`workspace`, `terminal`. Вид и номер ссылки совпадают с исходной регистрацией.
Прежняя ссылка на снимок сохраняет прежнюю полную форму ответа. Membership берётся из
`sprint_members`, claim — только active. Workspace выбирается из всех состояний,
кроме `released`; при его отсутствии возвращается null.

Claim содержит `claim_id`, `task_id`, `claimant`, `status`. Workspace содержит
`workspace_id`, `task_id`, `repository_id`, `base_commit`, `branch`, `status`,
`read_only`, `roots`, `path`, `owner_session_id`. Каждый root содержит `kind`,
`position`, `path`; roots отсортированы по kind, затем position. Поле `path`
равно первому code-root в этом порядке, либо null у зарезервированного Workspace
без code-root. `owner_session_id` берётся из независимой Workspace binding,
либо равен null. Terminal возвращается как сохранённое JSON значение, либо null.

## Текущее значение Sprint

Чтение возвращает `sprint_id`, `sprint_revision`, `members`, `dependencies`.
Members отсортированы лексикографически; зависимости — по predecessor,
successor, kind. Первая регистрация создаёт revision 1 и её запись в
`sprint_revisions`. Новый пакет с тем же составом и рёбрами возвращает
`changed: false`; порядок входных списков не меняет desired set.

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

Следующий исторический пример вызывает только шаг 0 → 1, а не текущий CLI.
Из корня рабочей копии он создаёт временную SQLite БД с опубликованной исходной схемой. Независимые
SQL значения показывают сохранение
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
[31 проверка регистрации](../tests/test_registration.py) дополнительно проверяет
реальный Requirements producer, оба вида ссылок, членство, граф, повтор и
переход к формату 2. Процедура работы по выданным ID находится в
[руководстве CLI](shell-seq.md#регистрация-выданной-работы).

## Постоянная подготовка Workspace

`workspace/prepare` сохраняет постоянную принадлежность контекста Task. Внешний
пакет и ответ принадлежат [текущему договору](contract-seq.md#60-реализованная-подготовка).
Формат SQLite остаётся 2. Task claim, независимые Workspace bindings и прежние
корни/история других контекстов сохраняются. Release/resume Task claim оставляет
тот же workspace_id, исходный base_commit, режим, ветку и roots. Новый владелец
использует свой действующий claim для нового prepare request; завершённый старый
request сохраняет исторический ответ.

| Сохранённое состояние | Результат подготовки |
| --- | --- |
| Контекст отсутствует вместе с историей | Выделяется один новый Workspace |
| `ready` | Совместимый режим и roots возвращают существующий контекст |
| `reserved` | Исходный незавершённый запрос продолжает свою подготовку; другой получает `workspace_pending` |
| `failed` | `workspace_failed`, сохранённые материалы остаются |
| `release_pending` | `workspace_release_pending`, сохранённые материалы остаются |
| Только `released` | `workspace_released`, историческая принадлежность остаётся |

При наличии исторического released и текущего контекста выбирается текущий;
история остаётся прежней. Совместимость — точное совпадение режима и разрешённых
упорядоченных roots. Встроенный code-root определяется сохранённым контекстом.
`ready` используется без проверки изменения HEAD и без нового Git add.

### Транзакции и незавершённый запрос

Перед внешним Git одна короткая транзакция сохраняет `reserved` Workspace, roots,
событие истории и slot в общей таблице `requests`, увеличивая revision на один.
В response_json этого slot находится внутреннее намерение с schema
`stride-sequencing-workspace-pending/v1`, workspace_id и полным исходным packet.
Это незавершённое намерение, а не успешная публичная квитанция. Общий первичный
ключ request_id резервируется уже здесь: тот же ID с другим prepare либо apply
пакетом получает `request_reuse_conflict`. Другие ID и Task продолжают работу.

Git выполняется после фиксации и освобождения SQLite-транзакции и локального lock.
Затем короткая транзакция повторно проверяет claim/состояние/свой slot, переводит
тот же Workspace в ready, добавляет событие и заменяет своё намерение полным
окончательным ответом. Используется последняя общая revision плюс один, включая
успевшие изменения других Task. Окончательная квитанция сохраняется неизменной;
точный replay возвращает её без записи и Git. Повтор незавершённого намерения
продолжает работу с сохранёнными base/path/branch, сохраняя первоначальный выбор
даже после изменения настроенной базовой ветки.

Read-only сразу сохраняет ready, roots, историю и окончательный ответ одной
транзакцией с увеличением revision на один. Новый request для совместимого ready
тоже добавляет только свою окончательную квитанцию и одну revision.

### Продолжение физической подготовки

Перед повтором оператор останавливает прежнего писателя и сохраняет действующий
Task claim. Нормальный фильтр Git должен завершать свою работу. После timeout
также остановите исходного писателя перед повтором. Исходный полный пакет храните
до завершения; повтор передаёт тот же request_id и все его поля.

| Фактическое состояние Git | Продолжение исходного намерения |
| --- | --- |
| Сохранённые путь и ветка свободны | Обычный worktree add с созданием ветки от сохранённого base |
| Осталась собственная ветка точно на сохранённом base, пути нет | Обычный worktree add с существующей веткой |
| Зарегистрировано рабочее дерево с сохранёнными путём и веткой, checkout завершён | Сохраняется ready; собственные коммиты и WIP остаются |
| Та же регистрация помечена `locked initializing` | `workspace_checkout_incomplete:` с path/branch/base; Workspace остаётся reserved |
| Путь, ветка или регистрация заняты иным контекстом | `workspace_conflict`; материалы сохраняются |

После прерванного initializing оператор завершает checkout в сохранённом рабочем
дереве, проверяет нужные файлы, затем вызывает `git worktree unlock` для этого пути
и повторяет исходный prepare. Завершение checkout учитывает используемые фильтры
и фактические сохранённые изменения. Приложение оставляет это операторской процедурой.
Ошибка внешнего шага сохраняет reserved и незавершённый request без успешной
квитанции. Самостоятельные acquire/release Workspace, cleanup и transfer остаются
будущими возможностями; prepare создаёт контекст, независимое владение сохраняет
прежнего владельца либо null.
