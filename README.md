# Agent Harness — локальная WSL-поставка

Версия: **0.16.1**. Harness запускается как отдельное приложение. Его project configuration, Task DB, Requirements DB, process catalogue и task/sprint artifacts принадлежат проекту Harness и не размещаются в обслуживаемой кодовой базе.

## Что находится в комплекте

- исходный код Harness и 13 reference process templates;
- один WSL project template `wsl-harness`;
- один Sprint `SPRINT-0001` с задачами `0001`–`0003`;
- bootstrap согласованных future System/Application requirements;
- seed-инструмент, создающий `tasks.sqlite` через публичный Sprint API без ручного SQL;
- skills для обычной работы и разработки Harness;
- русскоязычная инструкция локальной работы в WSL.

Полная инструкция: [Локальная поставка Harness для WSL](docs/wsl-local-delivery.md#архитектура-локальной-установки).

## Минимальный запуск

```bash
export PYTHONPATH="$PWD/src"
python -m harness project-init \
  --settings "$PWD/config/project-setup.json" \
  --template wsl-harness \
  --destination projects/harness \
  --request-id HARNESS-WSL-SETUP-1 \
  --probe-repository yes

python tools/seed_wsl_tasks.py \
  --harness-config "$PWD/projects/harness/project.json"
```

В анкете `paths.state` укажите отдельный project-data каталог Harness вне Git checkout обслуживаемой кодовой базы. Из него текущая версия размещает `tasks.sqlite`, task/sprint artifacts, runtime и worktrees по явно заданным путям project config. Task `0001` добавит отдельный конфигурируемый путь project-local `requirements.sqlite`.

## Первый Sprint

- `0001` — Requirements DB + System → Application → Task traceability + immutable snapshot;
- `0002` — IDE MCP capability discovery в обычном preflight/bootstrap;
- `0003` — исследование JetBrains MCP и каноническая policy работы AI-агентов.

Зависимости: `0001 → 0002 → 0003`, тип `result`.

## Работа агента

Обычный агент читает [Harness workflow skill](skills/harness/SKILL.md). При изменении Harness используется [Harness development skill](skills/harness-development/SKILL.md) и `src/AGENTS.md`.

Task SQLite: `user_version=12`. Project schema: `ddd-accounting-11`. Существующие stores автоматически не мигрируются.
