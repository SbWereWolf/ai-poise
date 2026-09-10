# Источники архитектурных решений

Создано: **2026-09-06T16:04:53+05:00**. Проверены в этой работе через официальные страницы авторов/проектов. Не использованы вторичные пересказы и схемы микросервисов как обязательная архитектура.

## Внешние первичные источники

| ID | Источник | Что использовано и чего он не доказывает |
|---|---|---|
| S1 | [Martin Fowler — Bounded Context](https://martinfowler.com/bliki/BoundedContext.html) | Явные границы моделей и их отношений. Конкретное деление нашего Harness на contexts — собственное проектное решение |
| S2 | [Martin Fowler — DDD Aggregate](https://martinfowler.com/bliki/DDD_Aggregate.html) | Root как граница доступа и целостности. Документ рекомендует не пересекать агрегаты транзакциями; локальные многокорневые publication cases Harness явно обозначены как сознательная адаптация для одной SQLite, не как универсальная норма DDD |
| S3 | [Repository — Patterns of Enterprise Application Architecture](https://martinfowler.com/eaaCatalog/repository.html) | Разделение доменных объектов и отображения в хранилище. Это не обоснование generic CRUD к каждой таблице |
| S4 | [Unit of Work — Patterns of Enterprise Application Architecture](https://martinfowler.com/eaaCatalog/unitOfWork.html) | Координация изменений в бизнес-транзакции. Наши external actions не помещаются в долгую SQL-транзакцию |
| S5 | [Martin Fowler — CQRS](https://martinfowler.com/bliki/CQRS.html) | Отдельные модели команд/чтения и предупреждение о лишней сложности. Выбран простой read/write split над одной БД, не отдельная CQRS-платформа |
| S6 | [Python 3.13 — sqlite3](https://docs.python.org/3.13/library/sqlite3.html) | Параметризованные запросы, явное управление транзакциями, SQLite backup connection API. Это не подтверждение доступности всех внешних инструментов ChatGPT |
| S7 | [SQLite — Foreign Key Support](https://www.sqlite.org/foreignkeys.html) | Включение FK на соединении, composite keys и индексы дочерних ссылок. FK не проверяет истинность evidence и наличие файла |
| S8 | [SQLite — Transaction](https://www.sqlite.org/lang_transaction.html) | Транзакционное поведение и ограничение writer concurrency. Межпроцессная координация Harness остаётся внешней |
| S9 | [Python 3.13 — fcntl](https://docs.python.org/3.13/library/fcntl.html) | Unix flock shared/exclusive/nonblocking. Не обещается корректность неизвестного сетевого filesystem |
| S10 | [SQLite Backup API](https://sqlite.org/backup.html) | Создание согласованной копии SQLite; внешние artifacts требуют отдельного согласованного набора |
| S11 | [SQLite CREATE TABLE](https://www.sqlite.org/lang_createtable.html) | PK, UNIQUE, CHECK и FK в физическом DDL. Логический каталог этого комплекта ещё не является DDL |
| S12 | [SQLite Atomic Commit](https://www.sqlite.org/atomiccommit.html) | Гарантии commit относятся к механизму SQLite при оговорённых предпосылках, а не автоматически к Git/произвольным внешним файлам |
| S13 | [Git — git-worktree](https://git-scm.com/docs/git-worktree) | Отдельные working trees и разделяемые части repository. Сам worktree не изолирует тестовые сервисы или неправильный IDE binding |

## Входные материалы проекта

- `harness-shared-handlers_2026-09-06T15-39-15+05-00.zip`: карта 98 узлов, 34 feedback-перехода, контракт runner и handlers.
- `harness-workflow-routes_2026-09-06T15-06-56+05-00.zip`: описательные короткие и полные маршруты 13 целей.
- `harness-happy-path_2026-09-06T14-30-44+05-00.zip`: существующий прототип; прочитаны src/harness/*.py, извлечён список методов и актуальное path-only решение.
- Последнее прямое указание пользователя: DDD — инвариант проектирования; Task и Sprint — отдельные библиотеки; общий код инструментов, явное проектирование доступа и изменения данных.

Для предыдущих материалов сохранены [исходные hashes](evidence/input-hashes-before.json); использованные карты и решения находятся в `reference/`. Рабочий код продукта по ним не запускался и не изменялся. Отсутствие нового прогона его тестов не маскируется успешной проверкой архитектуры.

## Граница вывода

DDD, Repository, Unit of Work и простое разделение чтения/записи дают язык и проверенные формы разделения ответственности. Они не доказывают оптимальность конкретных имён библиотек, числа таблиц или производительность будущего Harness. Это проектные решения, которые должны проверяться уже согласованными реальными маршрутами и метриками; без добавления новых обязательных шагов LLM ради самого паттерна.
