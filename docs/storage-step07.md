# Хранение и транзакции DDD-07

Обновлено: **2026-09-07T00:07:53+05:00**. Project schema `ddd-runtime-9`; SQLite `user_version=10`. Нужен новый пустой store. Migration и compatibility readers не разрабатывались.

## Новые таблицы
| Таблица | Владелец и назначение | Ключи/связи |
|---|---|---|
| runtime_bindings | RuntimeRegistry: стабильная привязка внешней или generated identity к session, последний inventory | identity_key PK; session_id UNIQUE |
| runtime_cursors | RuntimeRegistry: позиция append-only локального источника | PK(session_id,path); data содержит inode/device/offset/anchor |
| handoffs | HandoffRepository: preparing→released→resumed, намерение и receipt | actor/request_id UNIQUE; task_id FK; индекс task_id |

Task lifecycle по-прежнему записывает TaskRepository. При release/resume используется общий UoW: Task change + business event + handoff + session binding находятся в одной транзакции. Секции, content requirements, evidence и Graph ранее созданных итераций сохраняются прежними библиотеками.

RuntimeRegistry не меняет Task. Курсор пишет после передачи событий в WorkTools; сбой до фиксации курсора приводит к повторному чтению, но InteractionLedger дедуплицирует одинаковые события. API не обещает одну общую атомарную транзакцию над логом и Task. Существование identity ещё не означает начало formal task.

SprintRepository.facts читает наличие released handoff; Sprint применяет правила eligible/resumable в собственной модели. Неизвестная unclaimed задача не считается автоматически переданной.

## Файлы
Под task run root остаются stdout/stderr, views/job, worker output и ready/error manifest. Имена и limits заданы runtime_services.output. Конкретный run уже имеет уникальный ID; соседние сессии не используют latest-result или общий текущий файл.

Под task root, в configured handoffs directory, создаётся каталог digest(actor,request_id): source bundle, receipt и выбранные сохранённые runtime artifacts. Пути каталогов/имена/режим файлов заданы runtime_services.handoff. Task/sprint-owned результаты не размазываются по приложению.

## Транзакционные границы
Short lock → сохранить намерение → unlock → Git/files/worker → short lock → сохранить receipt и доменный переход → unlock. SQLite transaction не объявляется атомарной вместе с git commit или копированием файла.

Материализатор не пишет Task/SQLite. Главный процесс сохраняет его Incident в operational journal и проверенном отчёте. Неизменяемый raw receipt — источник для повторного чтения; текст пользователя из rollout не записывается.

## Ограничения
Handoff работает в одном store; файлы после передачи проверяются, не перемещаются между машинами. Согласованный export/import всего store и external backup ещё не реализованы. Исчезновение filesystem может уничтожить и локальный bundle; его наличие не называется удалённым сохранением.
