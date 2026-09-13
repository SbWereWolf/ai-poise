# Локальная поставка AI poise для WSL

Обновлено: **2026-09-13**.

Для текущего локального проекта `ai-poise` действует [конкретная настройка](project-setup.md#локальный-проект-ai-poise): по поручению пользователя состояние находится внутри репозитория в исключённом из Git `projects/ai-poise/`. Описанная ниже исходная поставка `poise` не заменяет эту настройку.

## Архитектура локальной установки

AI poise — отдельное приложение и точка входа в работу AI-агентов. Для каждого проекта AI poise используется собственный project manifest, собственный каталог process configs, собственная Task DB, собственная Requirements DB после реализации задачи `0001` и собственные task/sprint artifacts. Эти operational данные не являются частью обслуживаемой кодовой базы.

Текущая поставка создаёт один проект `poise` и один Sprint `SPRINT-0001`. Идентификаторы Task локальны для проекта и имеют формат `0001`, `0002`, ... .

13 process configs проекта создаются из reference templates AI poise и после публикации проекта являются самостоятельными конфигурациями проекта: изменение reference template не меняет их автоматически.

## Пути project-local storage

Пути задаёт только project configuration. В WSL template `wsl-poise` поле `paths.state` — абсолютный project-data root вне Git checkout обслуживаемой кодовой базы. Остальные operational paths заданы относительно него:

```text
paths.database         = tasks.sqlite
paths.lock             = tasks.lock
paths.standalone_tasks = artifacts/standalone
paths.sprints          = artifacts/sprints
paths.runtime          = runtime
paths.worktrees        = worktrees
```

Standalone Task хранит файлы в
`artifacts/standalone/<task-id>/`. Task, принадлежащая Sprint, использует
`artifacts/sprints/<sprint-id>/task/<task-id>/`; её файлы не дублируются в standalone
namespace. Поле `paths.standalone_tasks` обязательно. Удалённое `paths.tasks` не является
alias и отклоняется при загрузке конфигурации.

Задача `0001` добавляет отдельный явный путь Requirements DB в project configuration. Каноническая `requirements.sqlite` должна находиться в project-data AI poise, отдельно от `tasks.sqlite` и отдельно от обслуживаемой кодовой базы. Код AI poise не должен иметь скрытого универсального пути Requirements DB.

## Настройка проекта в WSL

Из распакованного каталога:

```bash
export PYTHONPATH="$PWD/src"
python -m poise project-init \
  --settings "$PWD/config/project-setup.json" \
  --template wsl-poise \
  --destination projects/poise \
  --request-id POISE-WSL-SETUP-1 \
  --probe-repository yes
```

В анкете явно задаются repository исходников AI poise, base ref, remote, Git author, push policy, абсолютный project-data root и наследуемые environment variables.

## Создание Task DB

После публикации project manifest:

```bash
python tools/seed_wsl_tasks.py \
  --poise-config "$PWD/projects/poise/project.json"
```

Seed использует публичные Sprint/Artifact API AI poise и не редактирует SQLite напрямую. Создаются один `SPRINT-0001` и три задачи:

1. `0001` — project-local Requirements DB, граф требований, task-planning traceability и immutable Task snapshot;
2. `0002` — discovery IDE MCP capabilities в bootstrap preflight;
3. `0003` — исследование JetBrains MCP и документирование policy для AI-агентов.

Зависимости имеют вид `0001 → 0002 → 0003` с `kind=result`. Поэтому только `0001` является eligible сразу после seed.

Approved future requirements находятся в `delivery/requirements-bootstrap.json`. До реализации `0001` это bootstrap-артефакт первого Sprint. После импорта через новый Requirements API он не является вторым каноническим хранилищем.

## Начало работы над задачей

Выберите project config и сохраняемую caller identity (родительский каталог создаётся явно):

```bash
export POISE_CONFIG="$PWD/projects/poise/project.json"
mkdir -p "$PWD/projects/poise/caller-bindings"
export POISE_CALLER_BINDING="$PWD/projects/poise/caller-bindings/agent.json"
```

Bootstrap Sprint:

```bash
printf '%s\n' '{"operation":"bootstrap","input":{"task":{"id":"SPRINT-0001"},"decision":null,"feedback":null,"rework_stage":null},"messages":[]}' \
  | python -m poise work
```

Далее bootstrap конкретной eligible Task выполняется тем же API. Для repository-changing work AI poise выдаёт отдельный worktree.

## Skills для агента

Для обычной работы: [AI poise task workflow](../../.agents/skills/poise/SKILL.md#poise-task-workflow). Для изменения AI poise: [AI poise development](../../.agents/skills/poise-development/SKILL.md#poise-development).

Skill, который опирается на нормативную документацию, должен ссылаться на минимальный конкретный section, достаточный для данного operational rule.

## Резервирование

Task DB, Requirements DB и task/sprint artifacts не являются Git history обслуживаемого продукта. Незавершённая работа передаётся штатными `handoff`/`transfer`; БД не объединяются вручную.
