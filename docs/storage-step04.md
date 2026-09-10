# SQLite и сохранение доказательств DDD-04

Обновлено: **2026-09-06T19:15:45+05:00**. Срез **HARNESS-DDD-04**.

Новый store: user_version=5; прежние версии отклоняются без записи данных или миграции.

К таблицам DDD-03 добавлены:
- `task_proofs(task_id FK PRIMARY KEY, data)` — текущий snapshot evidence: book, текущий input и assessment;
- `task_proof_layers(task_id FK, version, data, PRIMARY KEY(task_id,version))` — сохранённые изменения этого snapshot.

`submissions.data` содержит evidence_work без наблюдаемых полей. Секции и трассировка сохраняются по-прежнему отдельными слоями. Raw receipt лежит в таблице evidence с привязкой task/stage/iteration, stdout/stderr — внутри task-owned runs. Полный текст вывода не дублируется в БД.

`SqliteTaskRepository` — единственный writer task_proofs/task_proof_layers. Изменение Task, snapshot proof, слой и событие фиксируются одной транзакцией. `SqliteEvidenceRepository` пишет immutable command receipts через отдельный application API. Он не меняет lifecycle.

Результаты реально выполненной команды сохраняются независимо от последующего acceptance: если регистрация аргумента не удалась, command receipt не стирается. Rollback одной semantic mutation не откатывает уже произошедший внешний мир.

Чистый EvidenceBook сохраняет snapshots метаданных наблюдений/аргументов/решений. Это сознательно простой текущий срез; оптимизация размерности персистентных proof_layers по фактической статистике, без хранения raw logs в snapshot. Не реализован полный каталог из архитектурного дизайна заранее.

Внешний Linux file lock охватывает только короткие SQL транзакции. Git, tests, поиск маркеров stdout и проверки файлов выполняются снаружи. Контекст сверяет Task version; внешние операции не объявляются атомарными с SQLite.

При cleanup удаляется runtime; raw evidence уже находится в задаче. Для неизвестного исхода прерванной команды сохраняется `pending` и блокировка до выяснения, а не автоматический второй запуск.
