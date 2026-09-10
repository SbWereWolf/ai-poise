# Осмотр кода и исправлений HARNESS-DDD-06

Дата: **2026-09-06T22:58:21+05:00**. Осмотр выполнял тот же агент; независимый peer review не заявляется.

## Границы
Чистые PlanSpec, Publication, ActionRun не вызывают Git/SQL. PlanCommands использует UoW/Repository и Task methods. RuntimePlanActions реализует побочные эффекты; он не пишет Task/action-таблицы напрямую и не выбирает бизнес-логику по goal_type. Общая проверка дерева, commands/evidence, private commit/push и ContentPolicy не дублируются отдельным integration runner.

## Воспроизведено и исправлено
- Message binding раньше исключал show из текущей задачи. `messages-red.log` показал 1 вместо 6. Привязка теперь зависит от активной работы, не operation/reason. Последующий отдельный запрос после cancellation не начисляется закрытой задаче.
- Известный pending merge после потерянного ответа останавливался после восстановления промежуточного источника. Теперь ограниченное продолжение законченного prefix выполняется в том же вызове, не требует лишнего polling.
- Failed commands-план не имел разрешённого повторного подхода. Добавлено явное user rework для known failed/blocked, отдельная итерация сохраняет предыдущее действие. Неизвестный эффект не повторяется вслепую.
- Неподтверждённый target push оставался заблокированным даже после восстановления транспорта при неизменном expected ref. Теперь remote сначала наблюдается и доставка повторяется ограниченно, без повторения неизменных тестов.
- Probe требовал явного различения «желаемого состояния ещё нет» и «наблюдение сломано». `probe_false_exit_codes` обязательны; другой код/timeout/неожиданный вывод блокирует apply.
- Механический publish увеличивал число содержательных этапов. Добавлен RED/GREEN regression, handler publish исключён из planned/delivered content counts, сообщения самой операции сохраняются.
- Первоначальная реализация вызвала общий validate_method без обязательного контекстного аргумента; исправлено после первого исполняемого теста. Ошибка не скрыта как успешный запуск.

## Осмотр сохранности
Ни target drift, ни dirty worktree не исправляются reset/force автоматически. Exact lease сопровождается ancestry check. Промежуточные merge checkpoint commits явно UNVERIFIED, не публикуют target. Перед target publication проверяются final content/artifact gates. Complete run не заменяет актуальную проверку дерева.

Транзакционный fault-test прерывает запись action event и проверяет, что current run не остался частично созданным. Recovery-тесты вводят потерю ответа после реального внешнего действия и проверяют отсутствие второго эффекта.

## Известные границы
Одно приложение, sequential merge, controlled external command plans. Нет универсального rollback, разрешения неизвестного чужого MERGE_HEAD, rebase/cherry-pick, production installer, реального runner service supervisor или полноценного offline handoff. Task/inspect-переходы сохраняют нынешний report/accept lifecycle. Механическая публикация адресуется как handler графа и требует явного разрешения; она исключена из метрики содержательных этапов.
