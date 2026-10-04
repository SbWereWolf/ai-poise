# Локальная поставка AI poise для WSL

Обновлено: **2026-09-18**.

Для текущего локального проекта `ai-poise` действует [конкретная настройка](project-setup.md#локальный-проект-ai-poise): по поручению пользователя состояние находится внутри репозитория в исключённом из Git `projects/ai-poise/`. Описанная ниже исходная поставка `poise` не заменяет эту настройку.

## Архитектура локальной установки

AI poise — отдельное приложение и точка входа в работу AI-агентов. Для каждого проекта AI poise используется собственный project manifest, собственный каталог process configs, собственная Task DB, собственная Requirements DB и собственные task/sprint artifacts. Эти рабочие данные отделены от версионируемых файлов продукта; физическое размещение внутри checkout допустимо по правилам соответствующего хранилища.

Настройка создаёт проект, но не создаёт задачи и Sprint автоматически. Идентификаторы Task локальны для проекта и выделяются штатным allocator при публикации постановки.

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

Поля `paths.requirements_database` и `paths.requirements_lock` обязательны и явно заданы в project configuration. Requirements DB и lock должны быть отдельны друг от друга и от Task DB и lock. Внешнее размещение в этом шаблоне — конкретный выбор; допустимость внутренних игнорируемых и неотслеживаемых путей определяет [правило размещения](../workflows/requirements-registry.md#владение-и-явные-пути). Код AI poise не должен иметь скрытого универсального пути Requirements DB.

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

В анкете явно задаются repository исходников AI poise, base ref, неиспользуемое имя remote, Git author, обязательное `push_required=false`, абсолютный project-data root и наследуемые environment variables. `push_required=true` запрещён и отклоняется до Git-команды.

## Реестры и постановка задач

Для новой установки требования регистрируются и согласуются через
[Requirements API](../workflows/requirements-registry.md#декларативный-api-и-согласование),
затем задачи и Sprint публикуются по [штатному workflow](../workflows/batch-work.md).
Установка не повторяет историческую миграцию и не создаёт демонстрационные задачи
в рабочей Task DB.

Для восстановления этой поставки использовать существующие снимки Task DB и
Requirements DB из полного архива. Утверждённые 6 требований и 4 связи уже импортированы;
[порядок восстановления](../workflows/requirements-registry.md#однократный-импорт-утверждённой-поставки)
описывает их явные пути. Пустой реестр не заменяет сохранённый снимок.

## Начало работы над задачей

Выберите project config и сохраняемую caller identity (родительский каталог создаётся явно):

```bash
export POISE_CONFIG="$PWD/projects/poise/project.json"
mkdir -p "$PWD/projects/poise/caller-bindings"
export POISE_CALLER_BINDING="$PWD/projects/poise/caller-bindings/agent.json"
```

Список доступных к запуску задач читается без захвата:

```bash
python -m poise next --settings "$PWD/config/project-setup.json"
```

После выбора точного Task ID использовать `bootstrap` публичного `work` API.
Не подставлять вымышленный Sprint ID. Для repository-changing работы AI poise
выдаёт отдельный worktree. В native-среде launcher привязан к фактической сессии;
нельзя выдавать чужой launcher или ручную переменную за эту идентичность.

## Skills для агента

Для обычной работы: [AI poise task workflow](../../.agents/skills/poise/SKILL.md#ai-poise-task-workflow). Для изменения AI poise: [AI poise development](../../.agents/skills/poise-development/SKILL.md#ai-poise-development).

Skill, который опирается на нормативную документацию, должен ссылаться на минимальный конкретный section, достаточный для данного operational rule.

## Резервирование

Task DB, Requirements DB и task/sprint artifacts не являются Git history обслуживаемого продукта. Незавершённая работа передаётся штатными `handoff`/`transfer`; БД не объединяются вручную.

## Выбор операторской установки и перенос состояния

Перед записью Task проверьте выбранные launcher, project manifest и Task DB.
Checkout исходников не определяет операторскую установку: одинаковые ID в разных
базах не делают их одной задачей. Для локальной операторской работы пользователь
выбрал `/home/sbwerewolf/workdata/poise-op/poise.sh`; исследовательский checkout
`ai-poise` не является местом создания операторских задач. Рабочая конфигурация
определяет конкретные пути; не копируйте их в код инструментов как константы.

При ошибочной записи сначала повторите создание через публичный API нужной установки,
прочитайте результат и сверьте содержимое. Только после этого по полномочию пользователя
отмените исходные дубли с указанием назначения. Не подменяйте перенос ручной записью БД.

При переносе установки проверьте interpreter, manifest, базы, привязки native hooks
и существование настроенного Git base_ref. Не удаляйте базу хуков ради переустановки.
Для поддерживаемого изменения installation identity используйте публичный reconcile,
описанный в [контракте хуков](runtime-hooks.md#согласование-installation-identity-после-переименования).
Это не обещание поддержки переноса корня с прежним ID. Если соответствующей публичной
операции нет, фиксируйте ограничение и задачу исправления; не создавайте старые-path
symlinks и не объявляйте ручной обход штатной процедурой.

Продуктовые исправления и разрешение конфликтов выполняйте в дочернем рабочем дереве.
Основной checkout и чужие изменения сохраняйте. Ошибка расположения дерева требует
исправления через владельца workspace, а не отмены исходной бизнес-задачи.
