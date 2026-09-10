# Осмотр тестов HARNESS-DDD-01

Создано: 2026-09-06T16:38:23+05:00

Проверяющий: тот же агент разработки, не независимый reviewer.

Тесты пишутся до реализации. RED по новым доменным методам — NotImplementedError публичного контрактного skeleton; это не выдаётся за воспроизведённый дефект существующего domain. Сквозные тесты запускают существующий runtime и реальные временные Git/SQLite, а не mock runner.

Проверены happy path, повтор текущего payload, возврат A→B→A, accept без advance, обязательность повторного verify перед accept, reopen, cancel без обычных проверок, optional section type validation, адресный исторический query, отказ при чужой задаче/этапе, rollback после реального SQLite trigger failure и version conflict.

Добавлены архитектурные guards: в domain запрещены I/O imports/calls; runtime не пишет Task-owned tables и не присваивает lifecycle fields. Это ограниченный AST guard, не доказательство всех возможных способов обхода Python.

Отклонено искусственное тестирование 13 повторов одного и того же обработчика. Второй независимый маршрут с другим именем этапа/секции проверяет параметризацию без goal_type branch.

Не покрываются этим срезом: Sprint DAG, все семь handlers, автоматический осмотр моделью, recovery неизвестного external effect, cloud connector. Это не prerequisites для извлечения Task/content.
