# Явная миграция Task process snapshot

Обновлено: **2026-10-11**. Исторические схемы ниже относятся к указанным точечным
операциям. Отдельный текущий контракт описан в разделе
[миграции типов и критериев этапов](#миграция-типов-и-критериев-этапов).

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
что runtime читает новый обязательный contract без fallback.

## Точечное восстановление Task 0082

`task-process-migration-2` — отдельное прямое разрешение только для Task 0082. Оно принимает
точно `task_ids: ["0082"]` и не расширяет разрешение `task-process-migration-1`: смешанный
набор, пустой набор, другой ID или несколько Task отклоняются до изменения данных. Этот путь
добавляет в сохранённый process snapshot 0082 только отсутствующий boolean
`worktree_required`, взятый из настроенного process того же immutable `goal_type`. Уже выданный
старый receipt schema 2 остаётся точно replayable; этот опубликованный контракт не меняется.

Корректирующая `task-process-migration-3` принимает только `0082`. Если schema 2 уже
добавила совпадающий boolean, schema 3 сохраняет process без изменений; если boolean ещё нет,
она добавляет его, не заменяя остальные поля старого snapshot актуальным process целиком.
Та же атомарная операция инициализирует отсутствующий `stage_contracts` в Task contract. Она
создаёт один contract на каждый сохранённый process stage в исходном порядке, переносит
его `allowed_paths` и назначает entry/exit gates из сохранённых process/task content requirements
по их stage и phase. Уже существующий `stage_contracts` либо несовпадающий boolean означает,
что точечный recovery неприменим, и весь запрос отклоняется. Обе правки выполняются одним
metadata update без изменения lifecycle columns, Task version и released handoff.

До вызова создайте публичную резервную копию через `poise backup create --config PROJECT_JSON`
в период без writers и передайте её точное имя в `backup_name`:

```json
{
  "schema": "task-process-migration-3",
  "request_id": "migrate-task-0082-stage-contracts-2",
  "backup_name": "BACKUP_NAME",
  "task_ids": ["0082"],
  "authorization": "User authorized process snapshot recovery for Task 0082."
}
```

Успех должен вернуть `receipt`; точный повтор того же запроса возвращает тот же receipt и
`replayed: true`. Миграция сохраняет lifecycle, verified result, stage, iteration, content, feedback, evidence, history, ownership, Git binding и config identity. Summary также фиксирует,
был ли добавлен process boolean, факт инициализации stage contracts и
old/new digests обоих изменяемых snapshots. После сравнения публичных
проекций передайте сохранённую released Task назначенному проверяющему и выполните native bootstrap Task `0082`. Ожидаемый восстановленный контекст — исходная verified итерация
`test_remediation`, а не новая Task и не пустой результат.

## Восстановление текущей задачи планирования

`task-process-migration-4` — одноразовая точечная операция только для
`ERP-HARNESS-COMPARISON-PLAN`. Она исправляет legacy Task, которой не хватает
`stage_contracts`, без restart: сохраняет ID, lifecycle, текущие stage/iteration, историю,
ownership, worktree, branch и WIP. Контракты выводятся исключительно из сохранённых process и
content snapshots этой Task: для каждого stage сохраняется его `allowed_paths`, а entry/exit
gates строятся по сохранённым requirements и их phase. Процесс не подменяется текущим
конфигом проекта.

Перед запуском остановите writers и создайте public backup. Запрос имеет точную форму:

```json
{
  "schema": "task-process-migration-4",
  "request_id": "migrate-current-task-stage-contracts-1",
  "backup_name": "BACKUP_NAME",
  "task_ids": ["ERP-HARNESS-COMPARISON-PLAN"],
  "authorization": "User authorized recovery of the current unfinished planning Task."
}
```

Успешный receipt фиксирует old/new digests process и contract, а затем native `bootstrap`
должен вернуть сохранённый этап `framing`. Другая Task, повторный другой intent, уже
инициализированный contract или drift базы отклоняются до записи.

Для отката прекратите все
writes и используйте только `poise backup restore --config PROJECT_JSON BACKUP_NAME` по
[контракту резервных копий](task-db-backups.md); восстановление является отдельным
разрушительным решением и не подразумевается самой миграцией.

## Миграция типов и критериев этапов

`task-stage-criteria-migration-1` — отдельная схема того же публичного
`poise task-process-migrate --config PROJECT_JSON`. Она не является пятой версией
исторических точечных схем выше и не расширяет их списки Task.
Операция требует прямой авторизации, исключительного окна без writers и публичной
резервной копии всей выбранной Task DB. Она классифицирует сохранённые этапы
явно, без угадывания по роли, имени или handler.

### Запрос и инвентаризация

Запрос содержит ровно пять полей: `schema`, `request_id`, `backup_name`,
`mapping`, `authorization`. `backup_name` — реальное имя проверенной копии
в штатном каталоге backups, не путь к произвольному файлу.
`mapping` непуст и покрывает **все** настроенные определения процессов и
**все** незавершённые Task, включая newborn. Подмножество не является частичной
активацией. Инвентаризацию, сравнение с backup и проверку привязок выполняет
владелец операции до записи intent; отдельный публичный preflight endpoint
этим контрактом не вводится.

| `kind` ресурса | Поля, кроме `kind` и `stages` |
|---|---|
| `process_definition` | `goal_type`, `expected_revision` — точная строка managed revision |
| `executable_task` | `task_id`, `expected_version` — целое число, `expected_process_digest` |
| `newborn_task` | `task_id`, `expected_revision` — целое число, `expected_process_digest` |

В `stages` каждый исторический этап указан ровно один раз в исходном порядке.
Для process definition элемент содержит ровно `stage_id`, `purpose`,
`criteria_authority`. Для Task добавляются `criteria` и `execution`.
Формы назначения, полномочий и критериев описаны в
[основном протоколе](workflows/stage-criteria.md#договор-и-критерии).
Отсутствующие исходные значения не заменяются скрытыми defaults.

`execution` содержит ровно `state`, `source_revision`, `source_refs`.
Исторический источник без редакций критериев требует `source_revision: null`;
положительное число нельзя выдавать за существовавшую редакцию.
`source_refs` — непустой список подлинных ссылок:

- `{"kind":"contract","digest":"OBSERVED-CONTRACT-DIGEST"}`;
- `{"kind":"submission","id":123}`;
- `{"kind":"task_event","id":456}`.

Маркеры и числа здесь иллюстрируют форму, а не существующие записи.
Contract digest должен совпадать с исходным контрактом. Для `started` нужны
подлинные submission или события начала, отправки либо проверки именно этого
этапа. Для `unstarted` нужны события создания newborn или перехода в available
и отсутствие фактического начала/результатов целевого этапа. При недостаточных
сведениях используют `unknown`, а не выдуманную историю. `unknown` не разрешает
менять критерии как ещё не начатые.

Весь запрос подаётся одним JSON в stdin выбранного CLI. Сначала создайте backup
через [его публичного владельца](task-db-backups.md), затем соберите фактический
mapping и авторизацию. Проверка одной JSON-формы не доказывает полноту mapping,
готовность установки или подлинность происхождения.

### Публикация, атомарность и восстановление

До intent проверяются целостность backup, равенство снимка исходной DB,
полнота ресурсов, точные версии, process bytes/digests, managed heads,
зарегистрированные targets и привязка project config к DB, lock, catalogue и
редактору. Перед intent и последующими эффектами конфигурация перечитывается.
Drift не разрешает переключить владельца или продолжить запись в новое место.

После сохранения durable intent процессы публикуются через существующий
редактор. Каждое завершение подтверждается точными receipt, документом,
current head и отсутствием pending-публикации. Только известная публикация
этого intent может быть продолжена; чужая или неопределённая не объявляется
завершённой. При восстановлении подлинного результата допустима read-only
сверка через исходного владельца и его точную привязку.

Публикация файлов не является частью SQLite-транзакции. Финальные изменения
Task metadata и запись завершённой миграции выполняются одной транзакцией
после повторной проверки исходных строк; сбой откатывает эту транзакцию.
Уже доказанные файловые публикации при последующем drift не стираются и
не подменяются другим владельцем.

Миграция создаёт редакцию критериев `0` с источником `historical`, исходным
digest и migration receipt, а не фиктивное согласование. Сохраняются ID,
lifecycle, версии Task, владение, текущие этапы/итерации, подлинная история,
результаты, WIP и рабочие деревья. Состояние выполнения сохраняет доказанное
происхождение; разрешение начала восстанавливает общий владелец допуска
по завершённому intent/receipt, а не по одному наличию новых полей.

Результат `migrated` содержит `request_id`, `resources`, `receipt`, `replayed`.
Точный повтор завершённого запроса возвращает исторический результат с
`replayed: true`; это не новая проверка текущей установки.
До intent отказ возвращает `rejected`. После intent неопределённость либо
незавершённый эффект возвращает `recovery_required` с reason и доказанными
ресурсами. CLI завершает `migrated` кодом 0, остальные исходы — кодом 2.

При `recovery_required` сохраните backup, intent и receipts, остановите writers
и восстанавливайте именно этот intent через владельца операции. Новый request ID,
ручная правка DB, удаление журнала и автоматическое восстановление только DB
после частичной файловой публикации не являются безопасным продолжением.
Удаление backup или откат требует отдельного решения после проверки целостности
всей установки. Эта документация не разрешает live-активацию.
