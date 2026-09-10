# Хранение среза 01

Обновлено: 2026-09-06T16:55:38+05:00.

Store `user_version=2` создаётся только на пустом файле/отсутствующей БД. Вариант v1 отклоняется без изменения исходного файла — это проверяется сравнением байтов. Неизвестная непустая БД также не инициализируется как наша.

| Таблица | Владелец записи | Данные |
|---|---|---|
| tasks | SqliteTaskRepository | lifecycle, position, claim, version, current submission, снимок metadata/process |
| submissions | SqliteTaskRepository | ordered immutable submission, digest и envelope без копии текстов |
| section_layers | SqliteTaskRepository через Task/content | TEXT секций и content_state; FK на submission своего owner |
| task_events | SqliteTaskRepository | содержательные события и user feedback |
| task_results | SqliteTaskRepository | неизменный report каждого verified submission |
| task_execution | SqliteExecutionRepository | Git/workspace, попытки, pending/publication, current report projection |
| sessions | прежний Store | привязка текущей задачи |
| evidence | прежний Store | receipts команд |
| artifacts/task_artifacts | прежний Store | path-only артефакты и связи |
| journal | прежний Store | операционный журнал |

Текст секции хранится в одном месте: `section_layers.content`. Digest и metadata не заменяют текст. Старые candidate/verified слои не удаляются. Статус задачи нельзя записать через `Store.save`: execution writer сверяет lifecycle snapshot и отклоняет подмену.

## UoW и locking

`Database.transaction`: внешний Linux flock с явными wait/poll → соединение SQLite с foreign_keys ON → явные BEGIN/COMMIT либо ROLLBACK → освобождение ресурсов. Timeout ожидания конечный. Внутренние SQLite-механизмы обеспечивают целостность файла; монопольный доступ между Harness-вызовами координирует внешний lock.

`SqliteUnitOfWork` связывает оба repository с одним connection. Репозиторий не фиксирует транзакцию отдельно. Task state, submission, layers и events атомарны; mark_verified также атомарно пишет TaskResult и report projection. Ошибка INSERT события в тесте приводит к rollback и root, и текстов.

Git, test runner и filesystem операции не входят в ACID БД и выполняются вне UoW. Их полное crash-recovery ещё не реализовано. В этом срезе сохранена существующая остановка при неизвестном исходе.

## Индексы и чтение

`submissions_owner_stage(task_id,stage,seq)`; `layers_owner_section(task_id,section_id,submission_id)`; `task_events_owner`; `tasks_status`; индексы evidence/journal. Составные FK связывают слой и verified result с submission именно той же задачи.

`TaskQueries.record` — переходный DTO для прежнего runner. Это не разрешение менять dict и сохранить его как агрегат. История текстов при загрузке для mutation не читается: домен получает текущий digest и политику.

## Ограничения

Защита от намеренной внешней порчи SQL/файла пользователем не является sandbox/security boundary. DDD ограничения действуют для кода Harness; никто не обещает, что public Python constructor невозможно намеренно обойти. Архитектурные guards и осмотр проверяют нормальные границы разработки.

Технические основания: [sqlite3 Python 3.13](https://docs.python.org/3.13/library/sqlite3.html), [fcntl](https://docs.python.org/3.13/library/fcntl.html), [SQLite FK](https://sqlite.org/foreignkeys.html). Проверено на доступной среде CPython 3.13.5, SQLite 3.46.1.
