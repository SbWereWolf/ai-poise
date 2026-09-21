# Контрольные точки разработки и восстановление

Обновлено: **2026-09-17**.

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

## Полная копия для облачных агентов

Для всей очищенной рабочей копии с `.git`, всеми refs, постоянными БД и конфигами
используется [cloud recovery toolkit](../../recovery-tools/README.md#назначение-и-границы).
Он переиспользует `tools/work_checkpoint.py` для legacy-формата и Git-операций;
старый `payload/` не объявляется полной копией mutable project state. Новый формат
включает обычный архив, ASCII-представление тех же байтов и проверяемый manifest.

[Слияние cloud/local](../../recovery-tools/README.md#слияние-облачной-и-локальной-копий)
создаёт только новый кандидат, сохраняет WIP и конфликтующие данные. Разные БД
объединяются через явные INSERT/UPDATE с hashes, identity mapping и postconditions.
Почтовую доставку выполняет подключённый Gmail-инструмент; недоступный readback
не считается успешным. HANDOFF.md передаётся вне Git history.


## Полные резервные копии в Gmail при непрерывной разработке

`recovery-tools/gmail_checkpoint.py` готовит самодостаточную копию остановленного
источника через существующий `cloud_recovery.py`. Исходный формат, все Git refs,
незакоммиченный WIP, постоянные БД, авторские материалы и `WORKLOG.md` сохраняются.
Исключаются только известные воспроизводимые кэши и окружения; tracked-файлы
сохраняются даже внутри каталогов с такими именами. Перед упаковкой остановить
записывающие операции; живой SQLite WAL/journal или Git lock означает отказ,
а не разрешение удалить этот файл. Архив и staging создаются вне репозитория. Для частых копий helper явно выбирает
XZ preset 1; `--xz-preset` меняет только стоимость сжатия, не состав/проверку.
`cloud_recovery.py pack` без этого параметра сохраняет прежний preset 9e.

После значимого этапа делать новую полную копию независимо от времени. При
продолжительной работе интервал между снимками не должен превышать 600 секунд.
`due` считает возраст **снимка**, а не более позднего обратного чтения; старый
receipt без времени снимка считается требующим новой копии. Это проверка в
активной сессии, не фоновый планировщик. Запускать её также до длительных проверок.

```bash
python recovery-tools/gmail_checkpoint.py prepare \
  --project /absolute/path/to/ai-poise \
  --output /absolute/path/to/checkpoints/unique-id \
  --checkpoint-id unique-id --recipient owner@example.org \
  --next-action 'Resume the exact saved Task and stage'
```

Вызвать подключённый `Gmail.send_email` с параметрами из `SEND-REQUEST.json`.
Helper не хранит credentials и сам по себе не выполняет почтовую отправку.
Прочитать возвращённое письмо, затем получить полное `checkpoint.recovery.txt`
через `Gmail.read_attachment`; передавать в проверку именно этот отдельный файл.

```bash
python recovery-tools/gmail_checkpoint.py confirm \
  --output /absolute/path/to/checkpoints/unique-id \
  --readback /absolute/path/to/downloaded/checkpoint.recovery.txt \
  --message-id ACTUAL_GMAIL_ID --destination /absolute/path/to/new-verify-dir
python recovery-tools/gmail_checkpoint.py due \
  --receipt /absolute/path/to/checkpoints/unique-id/GMAIL-VERIFIED.json
```

`confirm` запрещает использовать исходный отправляемый файл вместо readback,
сверяет контрольные суммы полного transport и архива, восстанавливает проект
и сравнивает HEAD. Затем отправить `SEND-RECEIPT.json` через тот же коннектор.
Локальный receipt не является подтверждением хранения самого receipt в Gmail.
Журнал сохраняется и в архиве, и отдельным вложением, и в теле письма. Следующая
копия должна включать уже полученные Gmail message IDs и результаты чтения.

После потери контейнера найти последнее **полное** письмо, проверить его время,
HEAD и следующий шаг; номер или слово VERIFIED сами по себе недостаточны. Скачать
transport, `GMAIL-CHECKPOINT.json` и при необходимости три bootstrap-source
вложения `.py.txt`. Восстановить их исходную структуру `recovery-tools/` и `tools/`,
просмотреть код, затем выполнить:

```bash
python recovery-tools/gmail_checkpoint.py restore \
  --metadata /absolute/path/to/GMAIL-CHECKPOINT.json \
  --readback /absolute/path/to/downloaded/checkpoint.recovery.txt \
  --destination /absolute/path/to/new-restored-dir
```

Существующие каталоги не перезаписываются. Восстановление не запускает hooks,
не устанавливает продукт, не делает native session и не меняет Task lifecycle.
Внешние Gmail-вложения и старые архивы не помещаются внутрь новой копии проекта.

## Claims из восстановленной копии

Восстановление файлов не доказывает завершение прежней сессии. При оставшемся чужом
владельце использовать [штатный after_crash](crash-ownership-recovery.md),
а не останавливать всю работу и не подделывать caller/SessionEnd. Сначала подтвердить
изоляцию или остановку писателей, сохранить снимок, получить сгенерированный пакет
и записать реальное решение пользователя. Повтор запроса не сбрасывает нового владельца.
