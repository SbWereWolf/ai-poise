# Проверка локальной WSL-поставки

Обновлено: **2026-09-09**. Версия: **0.16.1**.

## Полный regression boundary

Финальная версия поставки проверена штатным `tools/run_test_packages.py` в последовательном режиме без автоматических повторов:

```text
packages:             78
passed unique tests:  723
skipped:              0
automatic retries:    false
workers:              1
package timeout:      900 s
source/config/tool fingerprint unchanged: true
success:              true
```

Runner начал с 318 fingerprinted source/test/example/config/tool файлов и подтвердил, что они не изменились до завершения проверки. Полный машинный результат сохранён в `delivery/full-test-result.json`.

До этого выполнялся параллельный полный запуск, который дал `722/723`: единственный `tests/hook_transport/test_mcp.py` превысил собственный 3-секундный handshake deadline под параллельной нагрузкой и затем проходил изолированно. Этот промежуточный результат не используется как поставочная граница и не скрывается. Поставочной границей является последующий полный single-worker run `723/723` на тех же исходниках.

## Структура стартового Sprint

Reference Task DB создана публичным Sprint API Harness без ручного SQL. В ней один проект Harness и один опубликованный `SPRINT-0001`:

1. `0001` — project-local Requirements DB + System → Application → Task traceability + immutable requirement snapshot;
2. `0002` — IDE MCP capability discovery в обычном preflight/bootstrap;
3. `0003` — исследование JetBrains MCP и каноническая политика его применения AI-агентами.

Зависимости:

```text
0001 --result--> 0002 --result--> 0003
```

SQLite `user_version=12`. Reference snapshot: `delivery/reference-databases/harness-tasks.sqlite`.

Reference DB предназначена только для проверки структуры. Рабочая Task DB создаётся после публикации окончательного WSL project manifest, чтобы task snapshots были связаны с фактическим project configuration.

## Requirements DB

`requirements.sqlite` в bootstrap-поставке намеренно отсутствует. Её создание, project-local storage и domain API являются результатом задачи `0001`. Согласованные будущие требования первого Sprint находятся в `delivery/requirements-bootstrap.json` и после выполнения `0001` должны быть загружены через новый Requirements API.

## Materialization

После настройки project manifest:

```bash
export PYTHONPATH="$PWD/src"
python tools/seed_wsl_tasks.py \
  --harness-config "$PWD/projects/harness/project.json"
```

Seed использует публичные Sprint/Artifact API и создаёт все три задачи в одной project-local `tasks.sqlite`.
