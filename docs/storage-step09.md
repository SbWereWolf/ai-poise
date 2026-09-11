# Хранение DDD-09

Обновлено: **2026-09-11T06:55:21+05:00**.

Task SQLite schema не меняется: user_version=12. Новых таблиц нет. Используются существующие
Task/Sprint repositories, SectionBook, action_runs/action receipt и UnitOfWork.

TaskBlueprint хранится как выбранный файловый шаблон с версией/digest; полная task после
подстановки проходит штатный creation validator. Process definitions публикуются через
прежний FileGoalConfigRepository с его собственными receipts.

PlanningPublications загружает текущую Task, проверяет владельца, publish-handler и
неизменяемую на этом этапе секцию плана, валидирует весь набор детей. Через TaskRepository
создаются available задачи. При sprint-публикации Sprint владеет графом и составом;
SprintRepository записывает корень, слои плана и membership. ActionRun receipt входит
в ту же короткую транзакцию, что и дети. Повтор completed receipt ничего не создаёт.
Сбой при записи второго ребёнка откатывает первого и receipt.

Внутри этой транзакции нет Git/subprocess/файловых операций. Worktree создаётся позже
инфраструктурой при bootstrap конкретного опубликованного ребёнка. Уже существующие IDs
отклоняются, не пересоздаются и не мигрируют.

Batch catalogue install проверяет содержимое всех кандидатов заранее, но публикация нескольких
файлов остаётся последовательностью независимых операций с receipts. Это не объявляется
общей атомарной файловой транзакцией; операции можно повторить с прежними идентификаторами.

## Automatic Task identity

Task SQLite schema остаётся `user_version=12`: новых таблиц, sequence и миграции нет.
Automatic creation сохраняет в обычном `tasks.metadata` дополнительный объект
`creation_request` с project-wide `request_id` и digest неизменного intent. Legacy/explicit
строки этого ключа не имеют и читаются прежним loader без преобразования.

Allocator выполняется через `TaskRepository` под существующим внешним lock одной UoW. Он
сначала ищет сохранённый request: совпавший digest возвращает прежний ID, другой digest даёт
conflict. Для нового request репозиторий проходит явно настроенную progression и исключает
существующие Task/Sprint IDs и зарезервированные IDs полного пакета. Task aggregate и, для
прямого bootstrap, `task_execution` с `pending.kind=worktree_setup` фиксируются в той же
транзакции. Ошибка до commit не расходует ID; ошибка последующего Git сохраняет reservation.

В `task_execution.pending` уже существующими полями хранятся точные `worktree`, `branch` и
`base`; schema не расширяется. После успешной сверки runtime атомарно записывает `pending=null`
и `entry_tree`. Конфликтующее внешнее состояние не изменяет эти данные. Available Task из
planning/Sprint publication по-прежнему не имеет execution row до явного старта.
