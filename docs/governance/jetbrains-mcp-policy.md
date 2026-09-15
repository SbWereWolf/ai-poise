# Политика применения JetBrains MCP

Обновлено: **2026-09-13**. Каноническое правило для AI-агентов, работающих с кодом в проектах AI poise.

## Назначение и источник истины

JetBrains MCP дополняет нативные инструменты редактирования и проверки, когда IDE может дать семантический ответ о коде или безопасно выполнить IDE-owned преобразование. Политика определяется этим документом. `AGENTS.md` и project skills содержат краткую англоязычную проекцию и ссылаются на минимальные нормативные разделы; исторические Task definitions, планы, отчёты и списки имён tools не являются самостоятельной политикой.

Действующая конфигурация Codex сообщает только намерение подключить endpoint. Описание доступного model tool сообщает только выданный интерфейс. Само по себе поле `projectPath` в запросе не доказывает, что endpoint его применил: нужен ответ о факте, уникальном для этого project/worktree, либо точный workspace predicate. Capability inventory AI poise является отдельным источником evidence: если JetBrains probe не включён в текущий hook definition, успешный прямой model tool call не следует выдавать за пройденный Harness preflight.

## Модель состояния capability

Для каждой IDE, операции и project/worktree агент различает четыре состояния:

1. `configured` — endpoint явно включён в пользовательской или project configuration;
2. `listed` — текущая сессия получила описание соответствующего tool;
3. `project_bound` — read-only вызов подтвердил уникальный факт нужного project/worktree, а не только принял переданный `projectPath`;
4. `operation_proved` — конкретная операция дала интерпретируемый результат; для мутации отдельно проверен итоговый diff и проектные checks.

Позднее состояние не выводится из более раннего. Наличие `rename`, `reformat` или универсального executor в каталоге не доказывает корректность изменения. Timeout, ошибка индексации, неподдерживаемый тип файла, чужой открытый проект или неоднозначный символ сохраняются как `unavailable`, `inapplicable` либо `inconclusive`, а не как PASS.

### Наблюдение 2026-09-13 для AI poise

| IDE endpoint | Configured | Listed текущей сессией | Project-bound наблюдение | Подтверждённая область |
|---|---:|---:|---:|---|
| PyCharm | да | да, 8 tools | не доказано | Ответы для общих с main checkout Python-путей; новый файл Task `0104` не найден, поэтому worktree binding и языковая применимость не подтверждены |
| PhpStorm | да | нет | не запускалось | не подтверждена |
| WebStorm | да | нет | не запускалось | не подтверждена |

В repository snapshot Task `0104` программные исходники представлены Python-файлами. Применимость к PHP, JavaScript, TypeScript и другим языкам не выводится из способности IDE открыть файл и остаётся `not_observed`, пока representative file в правильном проекте не пройдёт соответствующую code-aware операцию. AI poise bootstrap этой Task сообщил только обязательный probe CPython; JetBrains MCP не входил в его hook capability gate.

## Правило выбора инструмента

Для операции над программной семантикой агент сначала проверяет доступный inventory и выбирает применимую JetBrains MCP capability, если она `listed` и может быть `project_bound` для текущего worktree. Выбор основан на смысле операции и описании tool, а не на захардкоженной карте имён:

- semantic symbol lookup и quick documentation — для определения декларации, типа и контекста символа;
- IDE call hierarchy — для входящих и исходящих вызовов callable symbol;
- IDE inspections/diagnostics — для проблем поддерживаемого файла и пакетной проверки изменённых файлов;
- semantic rename — только для переименования программного символа со ссылками;
- IDE formatting — только для поддерживаемого изменённого типа исходника, когда форматирование принадлежит IDE/project policy.

PhpStorm и WebStorm не имеют общего приоритета друг над другом: при одинаково применимой capability выбирается endpoint, привязанный к нужному проекту. Для repository-wide graph, batch и structural поиска `ast-index` остаётся самостоятельным прямым инструментом, а не запасным вариантом после обязательного IDE-вызова.

## Границы и исключения

JetBrains MCP не заменяет владельцев workflow и данных. Агент не использует IDE MCP для:

- `build_project`, IDE build/run или запуска проектных проверок вместо зарегистрированных команд и project scripts;
- debugger;
- generic file read/write/patch и terminal execution;
- VCS/Git, Task/Sprint lifecycle, managed configuration или database operations;
- утверждения поддержки языка только по факту открытия файла;
- обхода approval, trust, sandbox, stage `allowed_paths` или пользовательской авторизации.

Read-only semantic вызовы допустимы для диагностики и исследования. Мутирующие `rename_refactoring`, `reformat_file` и dynamically dispatched команды применяются только внутри текущего Task worktree и разрешённого stage scope. Перед вызовом агент фиксирует точную цель; после вызова инспектирует полный diff и выполняет применимые узкие project checks. Универсальный executor не расширяет полномочия и используется только когда выбранная операция явно read-only либо отдельно разрешена текущим stage contract.

## Fallback на ast-index

Если требуемая IDE capability не `listed`, не привязана к текущему project/worktree, неприменима к языку/символу или завершилась неинтерпретируемо, агент фиксирует конкретную причину и продолжает через `ast-index`, когда тот способен заменить операцию. silent fallback без зафиксированной причины запрещён.

Fallback не превращает `ast-index` в универсальный эквивалент IDE. Он подходит для индексируемого symbol/file search, usages, implementations, hierarchy, callers и repository-wide structural анализа. IDE inspections, quick documentation, semantic refactoring и IDE formatting не объявляются выполненными на основании одного результата `ast-index`. Для обычного текстового содержимого, комментариев и regex после правил `ast-index` применим `rg`.

## Проверка и evidence

Capability считается доказанной только в границах сохранённого observation:

- источник конфигурации и его revision/digest;
- фактический tool inventory текущей сессии;
- точный `projectPath`/worktree и representative file;
- операция, параметры, результат и ограничения ответа;
- для мутации — diff и terminal results узких project checks.

Harness preflight и прямой model tool inventory записываются раздельно. Fixture или локальный protocol peer подтверждает протокол, но не живую JetBrains IDE. Ошибка защитного контракта (например, class вместо callable FQN) является наблюдением ограничения, а не дефектом endpoint. Непроверенные IDE, типы файлов и операции явно остаются `not_observed`.

При изменении политики обновляют этот документ и согласованные English-проекции в одной Task. Точные имена tools и временная capability matrix остаются наблюдением; устойчивые правила выбора, границ, fallback и evidence изменяются только вместе с канонической документацией.

## Проверяемая навигационная операция AI poise

Для разработки самой AI-poise доступна [операция с конкретной папкой и доказательством
индекса](../workflows/code-navigation.md#конкретная-папка-и-подтверждение-индекса).
Она использует существующий capability probe owner и сохраняет receipts; не вводит
новую IDE, indexer, глобальную конфигурацию или альтернативное разрешение путей.
