# Контрольные точки разработки и восстановление

Обновлено: **2026-09-15**.

## Контрольная точка и восстановление

После каждой выполненной задачи и перед рискованным переходом сохраняется одна
самодостаточная контрольная точка. Из неё восстанавливаются **код, точный Git HEAD,
снимок Task DB, принятые требования, состояние работ и результаты проверок**.
Переписка и сведения об отправленном письме не заменяют проверенный архив.

Инструмент: `tools/work_checkpoint.py`. Это инструмент разработки AI poise, а не новый
Task API, runner или механизм определения путей. Все входы — конкретные файловые пути.
Он не меняет конфигурацию, lifecycle, текущий checkout или расположение worktree.
Снимок БД заранее создаётся существующей операцией `poise backup create`; упаковщик
проверяет и копирует только **закрытый автономный snapshot**, не пишет в рабочую БД.

### Состав и достоверность

`create` создаёт Git bundle с полной историей выбранного HEAD, исходный snapshot БД,
`state.json`, cumulative binary-aware patch, commit mail series, приложенные логи и
SHA-256 каждого файла. Нормативные требования и следующий шаг задаются явно. Список
`rejected_heads` запрещает восстановление отклонённой линии как текущей базы.

Поставляются одновременно:

- `checkpoint.tar.xz` — полный двоичный архив;
- `checkpoint.recovery.txt` — **текстовый транспорт того же полного архива**, с явным
  заголовком формата, SHA-256 и ASCII Base64. Это не Markdown preview и не переименованный
  двоичный файл. Он нужен для коннектора, умеющего читать `text/plain`, но не архивы;
- `CHECKPOINT.json` и `changes.patch.txt` — читаемые сведения о состоянии и изменениях.

`state.json` содержит `current_task`, `next_action`, `requirements`, `completed`,
`blocked`; дополнительно — IDs спринтов/задач, известные ограничения, результаты тестов,
предыдущие Gmail receipts и отклонённые коммиты. Состояния «код реализован», «проверено»,
«принято пользователем», «интегрировано» и lifecycle Task DB не смешиваются.

### Создание и проверка

```bash
python tools/work_checkpoint.py create \
  --repository /absolute/path/to/ai-poise \
  --base EXACT_ACCEPTED_BASE_COMMIT \
  --database-snapshot /absolute/path/to/backup.sqlite \
  --state /absolute/path/to/state.json \
  --evidence /absolute/path/to/targeted-tests.log \
  --output /absolute/path/to/new-checkpoint
python tools/work_checkpoint.py verify \
  --checkpoint /absolute/path/to/new-checkpoint/checkpoint.recovery.txt
```

Dirty checkout отвергается **без stash/reset/clean**. Перед checkpoint исполнитель
явно сохраняет относящийся к поручению кандидатный commit; промежуточный commit не
означает завершения задачи. Неизвестные untracked файлы нельзя молча терять или
упаковывать вместе с секретами. Игнорируемые файлы не входят в Git bundle: необходимые
настройки/логи передаются явно, после проверки отсутствия credentials. Виртуальные
окружения и установленные зависимости переустанавливаются по сохранённым manifests.

### Gmail и подтверждение доставки

После каждой задачи отправляются полный архив, текстовый транспорт, manifest и patch.
Нужно не только получить ID письма: следует прочитать отправленный текстовый attachment
обратно и проверить **полный файл**, а не snippet. Затем:

```bash
python tools/work_checkpoint.py seal-delivery \
  --checkpoint /absolute/path/to/new-checkpoint \
  --message-id ACTUAL_RETURNED_MESSAGE_ID \
  --readback /absolute/path/to/downloaded/checkpoint.recovery.txt
```

Операция сверяет SHA-256, повторно восстанавливает Git/БД в изолированном временном
каталоге и только после успеха создаёт `DELIVERY.json` с `readback_verified=true`.
Сам инструмент не авторизуется в Gmail; transport выполняется подключённым почтовым
инструментом. Оператор передаёт именно полученный через него файл. Receipt не доказывает
происхождение файла криптографически — он подтверждает равенство прочитанного содержания.
Невозможность readback остаётся явным ограничением, а не «подтверждённым восстановлением».

Receipt хранится снаружи запечатанного payload: включить в письмо его собственный
message ID до отправки нельзя. Следующий checkpoint включает уже полученные receipts.

### Возобновление после сбоя

Сначала проверить **уже приложенные пользователем файлы**: их произвольные имена не
свидетельствуют об отсутствии checkpoint. Читать manifest/содержимое и сверять hash.
Не запрашивать повторную загрузку, пока имеющиеся вложения не проверены.

```bash
python tools/work_checkpoint.py restore \
  --checkpoint /absolute/path/to/checkpoint.recovery.txt \
  --destination /absolute/path/to/new-restoration
```

Восстановление создаёт `repository/` и `payload/` только в **новом** каталоге, проверяет
состав, hashes, SQLite integrity, точный Git HEAD, ancestry и `git fsck`. Существующий
каталог не перезаписывается. Код из восстановленного архива не исполняется автоматически,
и рабочая Task DB не заменяется. Активную установку и конфигурацию подключают отдельно
обычным способом, без изменения политики разрешения путей AI poise.

Затем читается `payload/state.json`, повторяются указанные bounded checks, и работа
продолжается с `next_action`. Отдельный F3 с отклонённой моделью путей не является базой.
