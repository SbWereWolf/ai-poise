# HARNESS-DDD-08 — контракт среза

Редакция: **2026-09-07T02:34:02+05:00**.

Цель: наблюдаемые tokens/time/messages и конечный полезный payload, без подмены расходов оценками.

Владелец: AccountingPolicy/Ledger; отдельные application ports и SQLite adapter. Task/Sprint lifecycle не изменяется метриками. Обычный batch work принимает optional telemetry как факты, не требует отдельного вызова на событие. Отсутствие telemetry не считается нулевым usage. Конфигурация измерителя и benefit каждого goal type явные.

DoD среза:
- delta и cumulative counters проверяются и дедуплицируются; cached/reasoning — детали, не дополнительное total;
- closed tool-cycle intervals исключают ожидание пользователя; источник и неполнота честные;
- финальный baseline→accepted-result diff считает добавленные и удалённые строки/байты, tests вне benefit development;
- финальные section outputs измеряются только по заданным секциям; tokenizer без настройки даёт unavailable, не выдуманные tokens;
- completed начисляет, reopen/cancel отзывают; повтор не удваивает;
- один query даёт task/sprint/project/global и goal_type/day/week breakdown, причины rework только явные;
- данные выбранных задач переносятся существующим transfer;
- TDD, авторский осмотр, docs, полный регрессионный набор и full/changes archives.

Не заявлять тарифы/деньги, скрытый Chat token counter, line-volume как бизнес-ценность, FTS/CPU/disk KPI или качество reviewer-модели.
