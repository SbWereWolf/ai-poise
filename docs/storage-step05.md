# Хранение DDD-05

Обновлено: **2026-09-06T22:06:40+05:00**.

Task SQLite: `user_version=8`. Старый store отклоняется. Миграции не написаны и не выполняются. Для новой установки нужен отдельный пустой state root.

| Таблица | Владелец и назначение |
|---|---|
| sprints | ID, project, состояние публикации, revision, доменный snapshot |
| sprint_layers | Неизменные слои плана и пользовательских решений по revision |
| sprint_members | Membership; task_id уникален, FK к Task |
| sprint_dependencies | Предшественник, потомок, kind; оба конца имеют составной FK к membership **того же** спринта |
| sprint_requests | Receipt запроса по (sprint_id, request_id), digest и исходный результат |
| session_sprints | Текущий sprint context без эксклюзивного владения спринтом |
| sprint_artifacts | Связь Sprint с общей таблицей артефактов |

Секции Sprint — TEXT/JSON в его SQLite snapshot, не отдельные Markdown-файлы. Механика валидации секций общая SectionBook. История Task/submissions/evidence не дублируется в Sprint: текущая сводка читает индексы и компактное состояние участников. Состояние sprints.state — draft/published/cancelled; пользовательский прогресс planned/active/blocked/completed вычисляется из Tasks. Разделение явно отражено в API.

Публикация в одной UoW: validators → Task.planned → TaskRepository.create для всех участников → Sprint.publish → membership/dependencies → receipt. Триггером испытаний подтверждён полный rollback при сбое на втором участнике. Никакого commit отдельно для каждого repository. FK включены и проверяются на соединении до BEGIN. Все writers используют общий Linux file lock; долгие Git/команды выполняются после выхода из транзакции.

Available Task не имеет task_execution до старта. TaskQueries использует LEFT JOIN и не создаёт worktree от чтения. Task.start меняет lifecycle через агрегат и создаёт execution snapshot в UoW, внешнюю подготовку worktree выполняет общий runtime adapter. Сценарий аварии между созданием worktree и записью Task пока даёт явную ошибку при повторе, а не универсальное восстановление: full pending-operation recovery остаётся DDD-07.

Файлы Task/Sprint остаются в configured roots. Path-only регистрация готовых файлов и FileArtifactFactory переиспользуются. Общий артефакт можно создать пакетно из выбранного Sprint без активной Task и без worktree.

## Основания
SQLite FK и composite parent keys: https://www.sqlite.org/foreignkeys.html . Локальные транзакции: https://www.sqlite.org/lang_transaction.html . Проверено при этом срезе. Документация SQLite обосновывает способ хранения, но не заменяет наши tests и не доказывает отсутствие всех сбоев.
