# Читающие проекции: анализ конкуренции и подготовка решения

Задача: `REVIEW-ARCH-A02`. База исследования: `429fc1486978578dcc76afc24219c28b1268d540`.
Это завершённый анализ, **не внедрение нового режима транзакций**.

## Обследованная граница

[TaskQueries](../../src/poise/infrastructure/sqlite/queries.py) использует
`database.transaction()` и для читающих проекций. [Database](../../src/poise/infrastructure/sqlite/database.py)
делегирует её [write_transaction](../../src/poise/infrastructure/sqlite/transaction.py):
application flock, `BEGIN IMMEDIATE`, тело, `COMMIT`. Это правильно для команды,
но чистый SELECT тоже занимает очередь писателей. Обозначение «query» само по себе
не доказывает отсутствие побочных эффектов: необходим отдельный аудит тела.

| Путь | Фактические операции и требуемая согласованность | Решение анализа |
|---|---|---|
| `section`, `trace_point`, `resolve_path` | По одному SELECT; исторический submission/trace или mapping должны соответствовать одному наблюдаемому состоянию. | Подходят для ограниченного read snapshot. |
| `record` / `record_in` | SELECT Task/execution, затем workflow/events/methods; формирует текущую проекцию, не исходный договор. Ветви newborn и terminal также читающие. | Все SELECT одного ответа должны видеть одну snapshot, не отдельные autocommit-чтения. |
| `latest_submission`, `result` | Submission/sections/result и mapping relocation читаются раздельно. | Нельзя смешивать старый результат с новым mapping; единая snapshot. |
| `content`, `evidence_view`, current verification registry | Восстановление агрегата через `SqliteTaskRepository.load`: SELECT и domain restore, включая ownership ambiguity gate, без save. | Сохранить валидацию и отказ неоднозначного ownership, не подменять их сырым SELECT. Перед переключением тестировать все ветви. |
| `terminal_snapshot`, history, summary | Несколько проекций исторических данных. | Snapshot ограничивает смешение версий, но не гарантирует актуальность после возврата. |
| `Database.__init__` | mkdir, создание schema/проверка версии, специальный ownership upgrade. | **Не чистое чтение.** Инициализация остаётся отдельной writer-операцией. |
| Command UoW / `save`, ownership claim, migration, bootstrap/materialization | Изменяют authoritative state; часть использует прочитанное для решения о записи. | Остаются guarded writer transaction; запрещено повышать уже начатую read snapshot до записи. |

Исходники агрегата: [SqliteTaskRepository](../../src/poise/infrastructure/sqlite/tasks.py).
Классификация описывает обследованные пути; не объявляет все вызовы runtime read-only.
Проверка полномочий в domain restore сохраняется даже без резервирования writer.

## Воспроизводимое измерение

Скрипт — `../../projects/ai-poise/standalone/REVIEW-ARCH-A02/artifacts/execution-20260918/measure.py` (исторический артефакт; не включён в исходную поставку 044A)
запускался из корня checkout командой `PYTHONPATH=src python -B PATH/measure.py`.
Полные результаты — `../../projects/ai-poise/standalone/REVIEW-ARCH-A02/artifacts/execution-20260918/measurements.json` (исторический артефакт; не включён в исходную поставку 044A)
содержат Python/SQLite версии, все 120 наблюдений и максимумы.

100 синтетических completed Tasks, один submission и report. Writer изменяет только
T099 и удерживает блокировку 120 ms; читается T000. Сравниваются реальные публичные
`record`/`section` и эквивалентные SELECT в `query_only` connection с `BEGIN` на **уже
инициализированной** БД. Пять повторов в каждой ячейке. Проверяются независимый SQLite
writer и сотрудничающий writer, использующий тот же flock. DELETE и WAL созданы только
временными синтетическими БД; рабочие файлы и режим проекта не менялись.

Медианы, миллисекунды (первый столбец текущая writer-query, второй кандидат snapshot):

| Journal | Query | Blocker | Текущий режим, ms | Snapshot, ms |
|---|---|---|---:|---:|
| delete | record | none | 0.417 | 0.468 |
| delete | record | independent_sqlite_writer | 123.499 | 0.689 |
| delete | record | cooperating_owner | 123.591 | 0.621 |
| delete | section | none | 0.442 | 0.331 |
| delete | section | independent_sqlite_writer | 123.833 | 0.575 |
| delete | section | cooperating_owner | 123.336 | 0.608 |
| wal | record | none | 0.583 | 0.491 |
| wal | record | independent_sqlite_writer | 123.964 | 0.592 |
| wal | record | cooperating_owner | 123.631 | 0.598 |
| wal | section | none | 0.913 | 0.536 |
| wal | section | independent_sqlite_writer | 123.690 | 0.596 |
| wal | section | cooperating_owner | 123.724 | 0.574 |

Все 120 результатов прошли смысловые assertions, временные БД — integrity/FK.
Отдельная проверка WAL snapshot: первая версия 1, после конкурентного commit внутри
того же чтения всё ещё 1, новая транзакция видит 2. Это наблюдение данного опыта,
не гарантия межбазовой или Git/SQL согласованности.

**Подтверждено:** в выбранных двух путях текущая writer-query ждала контролируемую
блокировку около 123–124 ms; эквивалентное snapshot-чтение в этих опытах не ждало её
освобождения. При отсутствии blocker разница менее миллисекунды и не является
доказанным ускорением продукта. Не измерялись production throughput, starvation,
большие aggregates, EXCLUSIVE writers, checkpoint pressure или читатели на живом host.
Пять повторов не дают основания обещать production p95/p99.

## Подготовленное решение

Рекомендуется **отдельный явно названный read snapshot для чистых проекций**, а не
изменение смысла `transaction()` и не глобальный переход на WAL. Инициализация должна
завершиться до входа в read-путь. Соединение открывается с `query_only=ON`, явным `BEGIN`
и гарантированным rollback/close; все SELECT ответа — в одной транзакции. Чтение не
берёт application writer flock, но ограниченно обрабатывает допустимый SQLITE_BUSY.
Нельзя автоматически retry всё произвольное тело запроса: оно может перестать быть
чистым. Результат содержит имеющиеся version/submission references; последующая
команда заново проверяет полномочия и optimistic version в своей writer transaction.

Альтернатива «оставить как есть» проще и остаётся корректной для небольшого локального
потока, но сохраняет измеренное ожидание нерелевантного writer. Альтернатива отдельной
проекционной БД добавляет replication/lag и не оправдана опытом. Autocommit для каждого
SELECT отвергнут: допускает смешение частей ответа. Глобальный WAL без анализа
восстановления/расположения файлов не предлагается.

## Следующий проектный срез, риски и откат

При отдельном решении о внедрении начать с `section`/`trace_point`, затем `record` и
многооператорных проекций. Command UoW, Database init и migrations не менять. Узкий порт
read snapshot должен быть доступен только query-adapter; тест запрета записи через
него обязателен. Долгие snapshot-читатели в DELETE могут задержать commit писателя;
в WAL — удерживать старое представление. Поэтому чтение остаётся коротким и без внешних
операций/ожидания пользователя. Отдельно проверить SQLITE_BUSY при EXCLUSIVE writer,
ошибку domain restore, schema mismatch и descriptor/connection cleanup.

Критерии проектирования: идентичные ответы в штатном режиме; одновременная запись
нерелевантной Task не заставляет чистое чтение брать flock; ни один ответ не смешивает
версии; read connection отвергает DML; следующее изменение отвергает устаревшую version;
инициализация и ambiguous ownership не обходятся. Повторить текущую матрицу с большими
синтетическими aggregates и короткими/долгими читателями до оценки полезности внедрения.

Откат — вернуть query adapter на прежний writer UoW без schema/data migration. Это
рекомендация и подготовка будущего проектирования; создание ещё одного обязательного
рефакторинга или изменение production режима не является результатом этой Task.

[Общий план](review-followup-2026-09-18.md) · [Границы runtime](runtime-boundaries-analysis.md)
