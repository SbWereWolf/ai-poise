# Хранение DDD-04B

Обновлено: **2026-09-06T21:22:42+05:00**. Task SQLite **user_version=7**, новый пустой store обязателен. Миграции не выполняются. Editor DB остаётся версии 1.

Task/content/evidence прежних срезов сохраняют владельцев. Добавлены четыре технические таблицы:

| Таблица | Ключ и назначение |
|---|---|
| interaction_events | source-scoped hash identity, project, immutable JSON события, фактическое received_at |
| interaction_bindings | один основной task binding на event_id, FK event/task, sprint/goal/stage/iteration/session/project |
| interaction_reports | PK task+stage+iteration, timestamp и источник `tool_result_returned` |
| work_packets | PK task+stage+iteration, digest успешного публичного verify-пакета |

Фактический DDL находится в `infrastructure/sqlite/database.py`.

События записывает только InteractionStore, который использует чистые UserMessage/InteractionLedger. Весь batch сверяется с имеющимися событиями до INSERT; конфликт не оставляет половину новых message events. Полученное сообщение не откатывается при последующем отказе проверки задачи. Повтор verified report не создаёт вторую delivery-запись.

WorkResources фиксирует digest полного успешного пакета verify. Это transport receipt, а не замена состояния дерева/command identity. Task/runner по-прежнему проверяют фактическое дерево перед replay. Runtime артефакт, исчезнувший после успешного verify, не материализуется снова от повтора того же пакета.

## Файлы
Factory получает roots текущих владельцев, explicit artifact_directories, имя lock, wait/poll и file_mode из config. Для текущего примера это `runtime/<session>/generated`, `tasks/<id>/artifacts`, `sprints/<id>/artifacts`; kernel не выводит бизнес-смысл по этим именам.

Существующий файл с тем же содержимым переиспользуется. Новое содержимое создаётся временным файлом рядом с destination, fsync и non-overwriting публикацией; symlink-назначения не допускаются. Batch preflight не является распределённой транзакцией файлов и SQLite. При I/O-сбое уже созданные owner-scoped файлы остаются для безопасного идентичного повтора, не считаются автоматически verified результатом.

Прежняя ArtifactRegistry выполняет path-only регистрацию и FK-link к task/sprint. Factory не изменяет lifecycle. Runtime-файлы не публикуются как долговременные ссылки в проверенном отчёте. CLI сохраняет полные ответы формальной task у неё, не в удаляемом runtime.

## Метрика
Отчёт task вычисляется по unique message events и первичному binding. Если событий нет, `user_messages_count=null`, coverage=unavailable; `observed_messages_count=0` означает лишь ноль наблюдённых записей. При наличии событий coverage=partial: этот срез не принимает подтверждение полного интервала. Day/week/sprint aggregation и интеграция source streams — DDD-07/08.

Taskless summary сейчас project-wide по известным событиям; это не отдельный session counter. Не представлять его как статистику только текущего разговора. Перенос связей/поздняя классификация и cross-store import — последующая работа, не неявный UPDATE существующего события.
