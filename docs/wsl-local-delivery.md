# Локальная поставка Harness для WSL

Обновлено: **2026-09-09**.

Для текущего локального проекта `ai-poise` действует [конкретная настройка](project-setup.md#локальный-проект-ai-poise): по поручению пользователя состояние находится внутри репозитория в исключённом из Git `projects/ai-poise/`. Описанная ниже исходная поставка `harness` не заменяет эту настройку.

## Архитектура локальной установки

Harness — отдельное приложение и точка входа в работу AI-агентов. Для каждого проекта Harness используется собственный project manifest, собственный каталог process configs, собственная Task DB, собственная Requirements DB после реализации задачи `0001` и собственные task/sprint artifacts. Эти operational данные не являются частью обслуживаемой кодовой базы.

Текущая поставка создаёт один проект `harness` и один Sprint `SPRINT-0001`. Идентификаторы Task локальны для проекта и имеют формат `0001`, `0002`, ... .

13 process configs проекта создаются из reference templates Harness и после публикации проекта являются самостоятельными конфигурациями проекта: изменение reference template не меняет их автоматически.

## Пути project-local storage

Пути задаёт только project configuration. В WSL template `wsl-harness` поле `paths.state` — абсолютный project-data root вне Git checkout обслуживаемой кодовой базы. Остальные operational paths заданы относительно него:

```text
paths.database  = tasks.sqlite
paths.lock      = tasks.lock
paths.tasks     = artifacts/tasks
paths.sprints   = artifacts/sprints
paths.runtime   = runtime
paths.worktrees = worktrees
```

Задача `0001` добавляет отдельный явный путь Requirements DB в project configuration. Каноническая `requirements.sqlite` должна находиться в project-data Harness, отдельно от `tasks.sqlite` и отдельно от обслуживаемой кодовой базы. Код Harness не должен иметь скрытого универсального пути Requirements DB.

## Настройка проекта в WSL

Из распакованного каталога:

```bash
export PYTHONPATH="$PWD/src"
python -m harness project-init \
  --settings "$PWD/config/project-setup.json" \
  --template wsl-harness \
  --destination projects/harness \
  --request-id HARNESS-WSL-SETUP-1 \
  --probe-repository yes
```

В анкете явно задаются repository исходников Harness, base ref, remote, Git author, push policy, абсолютный project-data root и наследуемые environment variables.

## Создание Task DB

После публикации project manifest:

```bash
python tools/seed_wsl_tasks.py \
  --harness-config "$PWD/projects/harness/project.json"
```

Seed использует публичные Sprint/Artifact API Harness и не редактирует SQLite напрямую. Создаются один `SPRINT-0001` и три задачи:

1. `0001` — project-local Requirements DB, граф требований, task-planning traceability и immutable Task snapshot;
2. `0002` — discovery IDE MCP capabilities в bootstrap preflight;
3. `0003` — исследование JetBrains MCP и документирование policy для AI-агентов.

Зависимости имеют вид `0001 → 0002 → 0003` с `kind=result`. Поэтому только `0001` является eligible сразу после seed.

Approved future requirements находятся в `delivery/requirements-bootstrap.json`. До реализации `0001` это bootstrap-артефакт первого Sprint. После импорта через новый Requirements API он не является вторым каноническим хранилищем.

## Начало работы над задачей

Выберите project config и session identity:

```bash
export HARNESS_CONFIG="$PWD/projects/harness/project.json"
export HARNESS_SESSION="agent-$(date +%s)"
```

Bootstrap Sprint:

```bash
printf '%s\n' '{"operation":"bootstrap","input":{"task":{"id":"SPRINT-0001"},"decision":null,"feedback":null,"rework_stage":null},"messages":[]}' \
  | python -m harness work
```

Далее bootstrap конкретной eligible Task выполняется тем же API. Для repository-changing work Harness выдаёт отдельный worktree.

## Skills для агента

Для обычной работы: [Harness task workflow](../skills/harness/SKILL.md#harness-task-workflow). Для изменения Harness: [Harness development](../skills/harness-development/SKILL.md#harness-development).

Skill, который опирается на нормативную документацию, должен ссылаться на минимальный конкретный section, достаточный для данного operational rule.

## Резервирование

Task DB, Requirements DB и task/sprint artifacts не являются Git history обслуживаемого продукта. Незавершённая работа передаётся штатными `handoff`/`transfer`; БД не объединяются вручную.
