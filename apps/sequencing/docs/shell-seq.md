# Самостоятельный CLI Sequencing

## Запуск

Из корня выбранной рабочей копии с настроенным Python 3.13:

```sh
apps/sequencing/bin/sequencing --config /absolute/path/sequencing.json
```

Команда получает один UTF-8 JSON request с завершающим переводом строки через stdin
и возвращает один JSON response через stdout. Wire schema — `stride-sequencing/v1`.
Провайдер находит код относительно собственного файла; `--config` задаётся явно.

Реализованы `apply` с `register_task` и `query` с `kind: "task"`. Запись создаёт
ссылку на каноническую Task и её Requirements snapshot; чтение собирает текущее
состояние из собственного нормализованного хранилища. Команды управления Sprint,
claims, физическими Workspace и transfer описаны в
[целевом контракте](contract-seq.md); их реализация относится к следующим срезам.
Точный реализованный формат, миграция и исполняемые примеры находятся в
[руководстве по хранилищу](storage-seq.md).

## Конфигурация владельца

Скопируйте `apps/sequencing/config/sequencing.example.json` в рабочий файл.
Укажите собственный `paths.state`, repository и worktree пути. Все поля схемы
`stride-sequencing-config/v1` обязательны:

```json
{
  "schema": "stride-sequencing-config/v1",
  "paths": {
    "state": "../state",
    "database": "sequencing.sqlite",
    "lock": "sequencing.lock"
  },
  "batch": {
    "max_input_bytes": 65536,
    "max_items": 256
  },
  "read": {
    "max_queries": 256
  },
  "lock": {
    "seconds": 5.0,
    "poll_seconds": 0.05
  },
  "repository": {
    "id": "ai-poise",
    "path": "../../..",
    "base_ref": "refs/heads/poise-stride-v2"
  },
  "worktree": {
    "root": "../../../projects/ai-poise/worktrees",
    "branch_prefix": "tasks/",
    "fs_timeout_seconds": 30.0,
    "git_timeout_seconds": 30.0
  },
  "transfer": {
    "root": "../transfer",
    "max_package_bytes": 67108864,
    "max_files": 4096,
    "max_file_bytes": 16777216,
    "file_mode": 384
  },
  "identifiers": {
    "task": {"pattern": "^[0-9]{4}$"},
    "sprint": {"pattern": "^[A-Z0-9-]+$"}
  }
}
```

| Поле | Назначение |
| --- | --- |
| `paths.state` | Абсолютный путь либо путь относительно каталога config |
| `paths.database`, `paths.lock` | Разные относительные пути внутри state |
| `batch.max_input_bytes`, `batch.max_items` | Положительные целые пределы запроса и операций |
| `read.max_queries` | Положительный целый предел запросов чтения |
| `lock.seconds`, `lock.poll_seconds` | Положительные конечные timeout и интервал повторного захвата локального lock |
| `repository.id`, `repository.path`, `repository.base_ref` | Идентичность repository, его путь и ветка будущих Workspace |
| `worktree.root`, `worktree.branch_prefix` | Каталог и префикс веток будущих рабочих деревьев |
| `worktree.fs_timeout_seconds`, `worktree.git_timeout_seconds` | Положительные конечные пределы файловых и Git операций |
| `transfer.root`, `transfer.max_package_bytes`, `transfer.max_files`, `transfer.max_file_bytes`, `transfer.file_mode` | Каталог, положительные лимиты и режим файлов будущего transfer (целое 0–511) |
| `identifiers.task.pattern`, `identifiers.sprint.pattern` | Регулярные выражения идентификаторов |

Repository, worktree и transfer пути разрешаются относительно каталога config.
Их параметры проверяются при запуске, даже когда текущая команда их не использует.
Рабочая конфигурация принадлежит конкретной установке; в Git хранится пример.

## Изоляция и результат

Sequencing использует только свой `database` и `lock`. Requirements и Execution
передают идентичности через публичные договоры; их конфигурации и БД этим CLI
не читаются. Разные state roots дают независимые хранилища. Lock принадлежит
одному Sequencing state и удерживается на время текущей операции.

Перед обращением к данным `Store.connect` включает foreign keys и вызывает
[историческую миграцию](storage-seq.md#переход-к-формату-1). Пустая БД получает
формат 1; поддержанный предшественник обновляется одной транзакцией. Чтение
поэтому тоже может впервые подготовить схему. Существующий формат 1 сохраняется.

Успешный новый `apply` увеличивает локальную revision на один и сохраняет receipt.
Точное повторение `request_id` и всего пакета возвращает сохранённый результат с
`replayed: true`; revision остаётся прежней. Следующий новый пакет использует
текущую `expected_revision`. Канонический `task_id` сохраняется без перенумерации.

Для обычного отказа CLI возвращает `status: "rejected"`, конкретный `reason` и
exit 2; stderr остаётся пустым. Конфигурация и пакет проверяются до создания
state. Условия поддержанного исходного хранилища и поведение отката описаны
в каноническом руководстве. Исправление рабочей конфигурации выполняет оператор.

## Проверка

Команды из корня рабочей копии:

```sh
python -B apps/sequencing/tests/test_shell.py --suite behavior
python -B apps/sequencing/tests/test_shell.py --suite documentation
python -B -m pytest -q apps/sequencing/tests/test_store_migration.py
```

Проверки выполняют реальные CLI/SQLite операции, проверяют независимые значения,
идентичности, типы, ownership, сохранность WIP и откат транзакций.
