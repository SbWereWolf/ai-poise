# Хранение DDD-07C

Обновлено: **2026-09-07T01:42:01+05:00**.

## Владение
Новый HookRegistry хранит только transport state в отдельной явно настроенной SQLite с user_version=1. Общий Task store остаётся version 11. Нет DDL/UPDATE к Task/Sprint в transport registry.

| Таблица | Содержание | Ключ |
|---|---|---|
| installations | owned native hook groups и receipt | definition id |
| operations | request digest, before/candidate, pending/completed | request id |
| bindings | project/source/external-session/configured-agent identity, launcher, settings fingerprint | harness session id |
| hook_events | только native event, time, turn id; prompt text отсутствует | append-only integer |

Каждое изменение — короткая SQLite-транзакция под общим bounded Linux file lock. Файловая публикация защищена той же установленной блокировкой, но не называется SQL-атомарной: pending intent фиксируется до atomic replace hooks.json; повтор сопоставляет before/after.

## Файлы
Settings и content-addressed definitions находятся в кодовой базе Harness. Binding/launcher — операционная привязка сессии, не результат задачи и не файл, который удаляет turn cleanup. Каталоги не общие `current`: каждый ключ сессии отдельный.

Каждый probe пишет stdout/stderr и receipt под уникальным operational call root с именами из settings. Парсинг обоих потоков и JSON assertions не меняет прежнее test evidence. Registry/receipts не включаются автоматически в task transfer, так как это данные принимающей машины; бизнес-данные сообщений по-прежнему у InteractionStore.

Счётчик событий передаёт существующему InteractionStore source/session/turn; первый известный task binding сохраняется. Повтор одного turn и несколько WorkTools calls не дают повторного расхода. Полнота остаётся partial; сборщик не доказывает что Codex действительно вызвал каждый hook.

## Settings setup
Один setup-пакет проверяется до записи. Settings устанавливаются атомарно как один файл. Повтор идентичного settings продолжает install; отличающиеся settings не заменяют уже связанные сессии. Ошибка поздней публикации может оставить валидный settings и pending intent — это сохранённое незавершённое действие, а не завершённая активация.
