# Runner и фазы DDD-04

Обновлено: **2026-09-06T19:15:45+05:00**. Срез **POISE-DDD-04**.

Пять реализованных семейств: produce, inspect, revise, observe, check. `apply_plan/publish` по полному архитектурному контракту ещё DDD-06; обычный commit/push прототипа уже работает как финализация write-stage.

Route: явный entry, outcomes→targets, rework_targets, max_transitions/max_stage_visits. Порядок списка stages не имеет семантики исполнения. Поддержаны короткие пути и циклы DDD-03.

`verify`:
1. Установить session/task/stage и наблюдаемое дерево.
2. Принять один полный result: секции, trace, новые методы, paths, stage_work и evidence_work.
3. Проверить content pre и required PREPARE evidence.
4. Выбрать task checks + project auto guards, сформировать точный execution key.
5. Использовать пригодный batch или выполнить команды без lock; записать receipts.
6. Убедиться, что предмет не изменился при проверках.
7. Оценить evidence; при CONTINUE вернуть факты и остановить механику этого вызова.
8. После аргумента — использовать те же наблюдения, оценить predicate/аргумент, проверить content post.
9. Согласовать commit tree, выполнить требуемый push, сохранить verified result.
10. Очистить runtime, вернуть доклад. Следующий этап ждёт пользовательского решения.

Незаполненная ручная часть до команды — evidence_requirements_failed. После новых фактов — awaiting_continuation. Неизвестные ссылки/невалидный формат — входная ошибка. Сбой или нераспознанный результат команды — checks_failed. Негативный predicate subject внутри check — нормальный not_satisfied, если процедура выполнена корректно. Guard failures никогда не становятся нормальным негативным subject.

Следующий inspect решает accepted/rejected для конкретной revision аргумента. Rejected направляет route в явно заданный changes_requested edge. Исправление проходит новую iteration и снова проверяется; старые revisions/decisions не теряются. Командные и логические evidence не подменяют друг друга.
