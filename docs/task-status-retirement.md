# Снятие устаревшего исхода Task

Обновлено: 2026-09-17. Текущие состояния Task: `newborn`, `available`, `active`,
`verified`, `accepted`, `completed`, `cancelled`. Терминальны только два последних.
Устаревший исход `superseded` и процесс замены Task не поддерживаются runtime:
нет enum, alias, новой записи или типизированной проекции Sprint `replacements`.
Неподдержанный статус отклоняется при восстановлении и чтении Task; приложение
не пытается автоматически угадать его смысл.

## Однонаправленная миграция

Оператор заранее сохраняет полную проверенную резервную копию и останавливает
всех писателей исходной БД. Только явный инструмент
`recovery-tools/retire_task_status.py` обрабатывает старый формат. Он требует schema13
и JSON-план с `schema: ai-poise-retired-task-status-1`, `request_id`, SHA-256 исходной
БД в `source_sha256`, полным отображением ID→version старых Task в `task_versions`,
ID→revision затронутых Sprint в `sprint_revisions`, `reason` и `authorization`.
Версии — числа, контрольная сумма — 64 шестнадцатеричных символа.

```sh
python recovery-tools/retire_task_status.py \
  --source /absolute/tasks.sqlite \
  --candidate /absolute/new-candidate.sqlite \
  --plan /absolute/retirement-plan.json
```

Исходная БД не изменяется. Кандидат должен отсутствовать. Все выбранные старые Task
должны быть освобождены, без неопределённых claim/session binding. Неизвестная схема,
неполный список, drift hash/версии, live WAL/journal, symlink, существующий кандидат
и ошибка SQL отклоняют публикацию. Инструмент создаёт согласованный SQLite backup,
применяет одну транзакцию и проверяет integrity/FK. Непосредственно перед публикацией
повторно проверяется SHA исходника; внешнее окно без писателей обязательно и не
заменяется этой проверкой.

Старый исход переводится в `cancelled`, **не в `completed`**; версия Task увеличивается,
добавляется событие `retired_status_migrated`. Исходные metadata, execution, submissions,
evidence, связи и WIP не переписываются. Старые typed replacement decisions убираются
только из текущего Sprint, revision увеличивается, добавляется новый слой. Полные
прежние строки Task/Sprint и разрешение сохраняются в journal-событии
`migration.retired_task_status`; прежние revision layers и история не удаляются.
Кандидат публикуется без перезаписи чужого файла. После проверки оператор отдельно
переключает рабочую БД в том же остановленном окне, повторно сверяя исходный SHA.
Не следует копировать кандидата поверх продолжающего работать SQLite-процесса.

## Чтение истории

Для всех `completed` и `cancelled` Task адресный bootstrap возвращает одинаковый
`poise-terminal-inspection-1`: исходные metadata, сохранённые контракты/секции/trace,
evidence/proof layers, execution/workflow и audit history. Это не исполняемый контракт;
отсутствующие historical registry или stage contracts не фабрикуются. Чтение не
запускает checks, не захватывает Task и не требует доступного worktree.
См. [терминальный просмотр](workflows/batch-work.md#просмотр-терминальной-task).
