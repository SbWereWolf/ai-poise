# Runner и фазы DDD-04

Обновлено: **2026-09-14**. Срез **POISE-DDD-04**.

Пять реализованных семейств: produce, inspect, revise, observe, check. `apply_plan/publish` по полному архитектурному контракту ещё DDD-06; обычный commit/push прототипа уже работает как финализация write-stage.

Route содержит только явный `entry`; переходы задаются `outcomes→targets`, а пользовательский возврат — `rework_targets`. Порядок списка stages не имеет семантики исполнения. Поддержаны короткие пути и циклы DDD-03. Счётчики переходов и посещений сохраняются только как история и не ограничивают исполнение.

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

Если предыдущий вызов успел сохранить полный набор terminal receipts, но его execution-marker
остался `pending=checks`, точный повтор того же `verify` восстанавливает выполнение без нового
запуска команд. Runner повторно строит текущие tree, invocation и execution key, сверяет
submission digest, полноту receipts, ledger provenance и digest сохранённых output. Затем один
application UoW записывает доменное событие восстановления и очищает только `pending`; уже
неуспешный batch снова даёт `checks_failed`, а успешный продолжает обычную оценку evidence.
Неполный, изменённый, дублированный либо повреждённый batch остаётся неизвестным исходом:
повтор не запускается вслепую и состояние не меняется.

Незаполненная ручная часть до команды — evidence_requirements_failed. После новых фактов — awaiting_continuation. Неизвестные ссылки/невалидный формат — входная ошибка. Сбой или нераспознанный результат команды — checks_failed. Негативный predicate subject внутри check — нормальный not_satisfied, если процедура выполнена корректно. Guard failures никогда не становятся нормальным негативным subject.

После `checks_failed` активный submitted этап не требуется отменять или пересоздавать. Только новый явный пользовательский `bootstrap(decision=rework, feedback, rework_stage)` может провести ту же Task через объявленный `rework_targets`, если сохранился точный доступный failed batch текущих stage, iteration, submission, tree и execution key и нет неизвестного `pending`. Цель `revise` без открытых findings отклоняется до изменения состояния. Переход сохраняет contracts, worktree, историю и receipts, применяет семантические правила route и scope целевого этапа и очищает только заброшенное retry-состояние. Полные условия и пакет запроса определены в [пакетном контракте](batch-work.md#явный-rework-после-checks_failed).

Следующий inspect решает accepted/rejected для конкретной revision аргумента. Rejected направляет route в явно заданный changes_requested edge. Исправление проходит новую iteration и снова проверяется; старые revisions/decisions не теряются. Командные и логические evidence не подменяют друг друга.
