# Осмотр реализации HARNESS-DDD-01

Обновлено: 2026-09-06T16:55:38+05:00. Осмотр выполнил тот же агент.

## Найденное и исправленное

| ID | Наблюдение | Исправление / evidence |
|---|---|---|
| CR-01 | Mapping StageSpec повторялся при создании и загрузке | Один pure mapper tasks.contracts; оба вызывают его |
| CR-02 | Повтор mark_verified мог подменить сохранённый report при том же digest | Replay сверяет прежний report, не меняет receipt; тест сначала RED, после fix GREEN |
| CR-03 | Execution writer молча игнорировал изменение status в DTO | Сверка всех lifecycle полей и версии с domain; обход отклонён; RED→GREEN |
| CR-04 | Standalone demo не задавал новую normalization | Пример теперь явный; добавлен smoke-test настоящего examples/demo.py; RED→GREEN |
| CR-05 | Старый verified report мог исчезнуть из current projection после rework | task_results хранит каждый report отдельно с FK; оба доступны через Query API; RED→GREEN |

Для CR-02/03/04 исходные падения сохранены в `evidence/ddd-step01/review-red.txt`, для CR-05 — `reports-red.txt`. Дополнительно finite limits проверяются одинаково в config/Database, без специальных fallback.

Повторный осмотр: root lifecycle SQL находится в TaskRepository; runner не присваивает статусы. В domain нет файлов, subprocess/SQLite/clock. Репозитории не commit отдельно. Tests и Git не находятся в UoW. Секция — TEXT без второго JSON-экземпляра. Версия v1 отклоняется без изменений.

## Сознательные границы

Это не независимый peer review и не полное достижение целевой архитектуры. Session/execution/artifact фасад и текущий линейный runner остаются переходной частью прототипа. Исправления выше ограничены реальными путями текущего среза; неизвестные сочетания не проектировались.
