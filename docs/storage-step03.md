Обновлено: 2026-09-06T18:12:15+05:00. Срез HARNESS-DDD-03.

# Хранение DDD-03

SQLite `user_version=4`; старые БД отклоняются, не изменяются. Миграции отсутствуют. Для нового запуска нужен новый пустой state root.

Добавлены две таблицы:

| Таблица | Владелец и назначение |
|---|---|
| task_workflows(task_id PK/FK tasks, data) | Текущие счётчики visits/transitions, outcome, stage_work и последняя проверенная FeedbackBook |
| workflow_layers(submission_id, task_id, data) | Неизменный кандидатный контекст обработчика при каждом содержательном submission; составной FK на submissions |

Это не новая БД и не копия task Markdown. Секции остаются в section_layers; reports — в task_results; все предыдущие слои доступны адресно. Поля feedback хранятся структурированным JSON snapshot внутри SQLite; отдельные SQL-таблицы внешних findings/evidence не вводятся раньше следующего среза.

TaskRepository — единственный writer обеих таблиц. В одной короткой UoW-транзакции сохраняются root/version, текущий workflow, submission/layers, policy/methods и domain events. Тест с настоящим SQLite trigger прерывает запись workflow_layers: root, sections и submission откатываются вместе. Git/тесты не исполняются под этой блокировкой.

При загрузке RouteDefinition берётся из сохранённого process snapshot; посещения берутся из task_workflows, а не выводятся из stage_index. stage_index остаётся внутренним адресом stage в массиве, не определяет следующий шаг. ID и структура новых полей — новый формат, не попытка совместимости с DDD-02.

Прежние task_results остаются неизменны. Смена кандидатного решения не удаляет исторический submission; принятие исправления не переписывает прежний finding/resolution.
