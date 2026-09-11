# Happy path и внутреннее устройство

Обновлено: 2026-09-06T14:30:44+05:00.

## Пользовательский цикл

`bootstrap` выбирает/возобновляет задачу, создаёт изолированный worktree, возвращает цель, требования, DoD, root AGENTS целевой codebase, разрешённый следующий этап, roots артефактов и один result file. Агент читает AGENTS приложения и работает его нативными инструментами.

Агент заполняет только содержательный результат, сообщение коммита и пути своих артефактов. Он не записывает Git status, timestamps, hashes или test exit codes. `verify` использует кандидатный результат, механически проверяет его, сам запускает точные tests, сохраняет evidence, создаёт/push-ит commit при Git-изменениях, очищает runtime и возвращает доклад. Статус verified сам по себе не начинает следующий этап.

`accept` соответствует «принял, остановись». `bootstrap --decision continue` соответствует явной следующей инструкции и объединяет принятие с подготовкой следующего этапа. `bootstrap --decision rework --feedback ...` открывает новую iteration того же этапа. `cancel` требует содержательной инструкции пользователя и не запускает acceptance checks.

## Данные

SQLite: task contract, process snapshot, session binding, stage/iteration, submissions с текстовыми sections, execution evidence, id/path артефактов и внутренние digests, append-only operational journal. Долгие команды запускаются вне внешнего DB-lock.

Filesystem под state root: runtime/session; tasks/task-id; sprints/sprint-id; worktrees/task-id. Только первые три являются допустимыми roots для agent artifacts. Code/test deliverables внутри worktree учитываются через Git, а не artifact_paths.

## Модули

`common.py` — явная конфигурация и проверка точных методов. `storage.py` — SQLite и внешний flock. `artifacts.py` — только пути, ID, confinement и количества. `execution.py` — конечный запуск процесса и file-backed output. `runtime.py` — простой последовательный happy path. `__main__.py` — тонкий CLI и краткий вывод с адресом полного ответа.

## Пример требования к файлам

```json
{"scope":"task","pattern":"*.json","minimum":2,"maximum":2}
```

Это требование двух уникальных зарегистрированных JSON-файлов в текущей task, а не двух JSON-объектов или двух тест-кейсов. Проверка содержания должна быть отдельной точной командой проекта. Значения — пример конфигурации, не значения по умолчанию в коде.

## Правило повторов

FAILED tests → исправление → verify: новая execution attempt, даже при прежнем semantic payload. Успешный verify без изменений → сохранённый report. Commit уже существует, push не прошёл → повторить только публикацию при прежнем проверенном дереве. Изменения после предъявленного verified требуют явного rework. Неизвестный исход прерванной внешней операции не запускается второй раз автоматически.
