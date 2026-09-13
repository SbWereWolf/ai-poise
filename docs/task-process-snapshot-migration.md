# Явная миграция Task process snapshot

Обновлено: **2026-09-13**. Реализованный срез: Task 0084.

`task-process-migrate` — публичная одноразовая операция для сохранённых Task process
snapshots, созданных до обязательного поля `worktree_required`. Это не compatibility read,
не fallback и не общий редактор metadata. Оператор запускает её только по прямому
разрешению пользователя для точного набора Task:

1. `0077`;
2. `0081`;
3. `0079`;
4. `0078`;
5. `0074`;
6. `0063`.

Порядок входит в intent запроса. Подмножество, надмножество, замена, повтор ID или иной
порядок отклоняются. Это узкое расширение первоначального плана Task 0084 прямым
разрешением пользователя; оно не разрешает менять остальные требования или другие Task.

## Подготовка

Явно выберите `PROJECT_JSON`. Остановите всех writers Task DB, затем создайте публичную
резервную копию:

```bash
poise backup create --config PROJECT_JSON
```

Сохраните выданное имя файла. Проверьте его наличие через `poise backup list --config
PROJECT_JSON`. Не копируйте и не заменяйте SQLite-файл вручную. Целевые Task должны
существовать, не быть terminal и содержать совместимый process без поля
`worktree_required`. Их неизменяемый `goal_type` должен ссылаться на настроенный process.
Существующий claim не нужно предварительно освобождать: операция сохраняет его точное
значение и не принимает решений о liveness, передаче или замене владельца. При этом сам
владелец не должен писать в Task DB во время backup и миграции.

## Запрос

Передайте один JSON document в stdin. Поля и их набор точные:

```json
{
  "schema": "task-process-migration-1",
  "request_id": "migrate-authorized-six-processes-1",
  "backup_name": "tasks-backup-20260913T120000000000Z.sqlite",
  "task_ids": ["0077", "0081", "0079", "0078", "0074", "0063"],
  "authorization": "User explicitly authorized this exact Task process migration."
}
```

Запуск:

```bash
poise task-process-migrate --config PROJECT_JSON <<'JSON'
{
  "schema": "task-process-migration-1",
  "request_id": "migrate-authorized-six-processes-1",
  "backup_name": "BACKUP_NAME",
  "task_ids": ["0077", "0081", "0079", "0078", "0074", "0063"],
  "authorization": "User explicitly authorized this exact Task process migration."
}
JSON
```

Операция проверяет целиком названную копию и рабочую DB, все цели и lifecycle,
process shape и соответствие `goal_type`. Значение `worktree_required` выводится из
настроенного process definition. После внешнего preflight состояние повторно сверяется
под writer lock. Все шесть snapshots записываются **одной транзакцией** либо не меняется
ни один. Task IDs, lifecycle, прочее содержимое process, history, submissions, evidence,
dependencies, claims, results и artifacts сохраняются. Стабильный existing claim допустим;
его создание, снятие или замена после названного backup либо после preflight считается
drift и отклоняет весь пакет.

## Receipt, повтор и восстановление работы

Успех возвращает статус `migrated`, `request_id`, `backup_name`, ordered Task summaries,
старый и новый digest каждого process и общий `receipt`. Тот же точный запрос можно
повторить: валидированный audit вернёт тот же receipt с `replayed: true`. Тот же
`request_id` с другим intent отклоняется. Повреждённый audit, несовпадение backup/live DB
или drift также отклоняются, без частичной записи. Audit UTC получается только через
обязательный `Clock`; production использует явно предоставленный `SystemClock`.

После успеха сохраните backup и оба JSON receipt, сравните публичные read-only проекции
до и после, затем выполните native bootstrap целевых Task. Успешный bootstrap доказывает,
что runtime читает новый обязательный contract без fallback. Для отката прекратите все
writes и используйте только `poise backup restore --config PROJECT_JSON BACKUP_NAME` по
[контракту резервных копий](task-db-backups.md); восстановление является отдельным
разрушительным решением и не подразумевается самой миграцией.
