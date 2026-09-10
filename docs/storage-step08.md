# Хранение DDD-08

Редакция: **2026-09-07T02:34:02+05:00**.

Project `ddd-accounting-11`; store `user_version=12`. Обновление формата намеренное, для запуска нужен новый пустой store. Прежние данные не мигрируются.

| Таблица | Владелец / смысл | Ключи и индексы |
|---|---|---|
| accounting_accounts | Закреплённый measurement baseline и policy задачи | task PK/FK |
| accounting_usage | Исходные usage samples и одна вычисленная contribution | identity PK; unique source/stream/sequence; task/date index |
| accounting_cycles | Наблюдённый tool interval либо явно сообщённый закрытый interval | identity PK; task/date; один открытый интервал session |
| accounting_credits | Append-only события зачёта/отзыва конечного результата | seq, identity unique, task/date |
| accounting_findings | Явная связь finding с прежним предъявленным результатом | task/finding PK |

Секции, runtime bindings, findings и пользовательские сообщения не копируются в новый конкурирующий business store. Отчёт читает их существующие проекции. Accounting не исполняет UPDATE задач/спринтов. Реализация SQLite находится за accounting port; domain не импортирует I/O.

Входной пакет usage/intervals/finding_targets применяется короткой транзакцией и откатывается целиком при некорректном элементе или ошибке записи. Дедупликация проверяет содержимое, не делает INSERT OR IGNORE для конфликтующих usage. Cached/reasoning остаются деталями общего расхода.

Git blobs и optional tokenizer читаются вне блокировки записи. Перед зачётом сверяется версия задачи. Метрики не используют статус completed как разрешение менять Task; ошибка measurement не снимает принятое состояние.

Transfer экспортирует выбранные account rows вместе с их Task. Credits получают локальный порядок вставки в destination, но сохраняют идентичность, исходные timestamps и delta. Незавершённые циклы требуется закрыть handoff, а не копировать как завершённые. Повтор импорта уже опубликованного пакета обрабатывается существующим transfer receipt.

Текущее чтение строит проекцию одного store, не отдельный аналитический сервис. Индексы предусмотрены для владельца и даты; новая FTS, event-sourcing платформа и распределённая аналитика не добавляются.
