# Ретроспектива миграции ERP → AI poise

Обновлено: **2026-09-15**.

Этот документ фиксирует исправленную семантику путей и область ERP → AI poise migration перед следующими спринтами.

## Разрешение путей и рабочие копии

AI poise сохраняет обычное разрешение путей для своих hooks, tools, конфигурации и runtime-кода. Task, сессия или выбранная рабочая копия это поведение не переопределяют.

Рабочая копия — обычный путь файловой системы, который конкретная операция получает как вход. Он может быть абсолютным или относительным в соответствии с контрактом операции и может указывать на произвольный каталог. Если subprocess должен собирать или проверять код из этой рабочей копии, его `cwd` задаётся конкретным путём.

Если отдельный Git worktree не используется, операция получает путь к тому checkout, где выполняется работа. Дополнительной глобальной настройки для этого не требуется.

`AGENTS.md`, skills и их reference-файлы разрешено читать из явно выбранной рабочей копии, когда задача должна учитывать branch-local инструкции. Их локальные ссылки разрешаются по обычным правилам соответствующего документа/файла. Чтение branch-local инструкций не меняет расположение исполняемого кода, hooks или конфигурации AI poise.

Git worktree относится к репозиторию/кодовой базе, которую изменяет Task. Он не является частью mutable state AI poise: state хранит управление, receipts и артефакты, а сам worktree размещается вместе с целевой кодовой базой. Если отдельный worktree не нужен, рабочей копией является настроенный repository checkout этой кодовой базы.

## Отдельная продуктовая задача: размещение Git worktree

Изменение места, где AI poise создаёт управляемый Git worktree, **не является ERP migration prerequisite** и не восстанавливает C026.1. Это самостоятельная продуктовая работа без межзадачной зависимости: реализация должна перенести создание worktree из mutable state AI poise в область worktrees, принадлежащую выбранному репозиторию/кодовой базе, и сохранить обычную работу прямо в настроенном repository checkout, когда отдельный worktree не требуется. Поэтому такая работа остаётся standalone и не добавляется ни в F1, ни в F2, ни в F3, ни в F4 только ради общей темы.

## Область ERP migration

Переносимые из ERP механизмы становятся возможностями **AI poise**. Они не образуют обязательный runtime, cache, runner, hook layer, boundary analyzer или layout для каждого репозитория, который когда-либо разрабатывается через AI poise.

Внешняя кодовая база сохраняет собственные test runners, cache layout, fixtures, lockfiles, browser/runtime topology и правила сборки. AI poise может выполнить явно зарегистрированную команду, передать ей конкретные пути/`cwd` и сохранить результат через собственные API, но не переопределяет внутренние механизмы чужого проекта только потому, что похожая возможность существовала в ERP.

Это особенно важно для C018: мигрируется семантика test-package cache, принадлежащего самому AI poise. Для проверки AI poise в любой его рабочей копии package membership задаёт исходники, тесты, support и fixtures; пути участников нормализуются относительно каталога проверяемого checkout. Другие кодовые базы используют свои cache-механизмы и свои способы вычисления ключей.

## Решение по C026.1

C026.1 **исключён из migration plan**.

Связь Task с optional worktree уже является существующей возможностью AI poise и не требует отдельного переноса. Предлагавшаяся C026.1 автоматическая подмена разрешения путей tool adapters путём рабочей копии отклонена. Все прежние hard dependencies на C026.1 удаляются; конкретные задачи явно принимают нужный filesystem path там, где действительно работают с кодом рабочей копии.

## Правило состава Sprint

размер Sprint не является целью. В Sprint находятся только Task, связанные межзадачными dependencies; полностью изолированная Task остаётся standalone. Каждый Sprint обязан содержать локальные копии всех prerequisites своих участников и не опираться на dependency edge в другой Sprint.

Если один и тот же смысловой prerequisite нужен нескольким Sprint, в каждом используется отдельная conditional-duplicate Task с уникальным ID. Перед реализацией такой дубль проверяет, удовлетворён ли уже эквивалентный функционал результатом другой Task. Если удовлетворён, повторная реализация запрещена: локальная Task подтверждает пригодность существующего результата и фиксирует reuse/no-change result. Если нет — реализует недостающее. Это позволяет держать Sprint независимыми, не объединяя все ветви графа в один огромный Sprint.

## Исправленный состав спринтов

### SPRINT-ERP-MIGRATION-SKILL-CATALOG

Без изменения: C005.1 → C012. Граф замкнут внутри Sprint.

### SPRINT-ERP-MIGRATION-F2-RUNNER-EVIDENCE

Состав определяется зависимостями, а не требованием иметь фиксированное число задач:

- C002 → C016;
- C002 → C033;
- C015 → C019;
- C015 → C033;
- C015 → C018;
- C016 → C018;
- C033 → C020.

Этот граф уже замкнут внутри F2 после исключения C026.1. C002 и C015 являются локальными корневыми prerequisites, но оба имеют downstream-зависимости, поэтому остаются участниками Sprint.

C018 относится только к test-package cache AI poise: package membership и content fingerprint считаются по файлам проверяемой рабочей копии AI poise и нормализуются относительно каталога этой копии. Механизм не становится cache adapter для других кодовых баз.

### SPRINT-ERP-MIGRATION-F1-ROUTING-TOOLS

F1 становится самодостаточным локальным графом:

- conditional duplicate C002 → conditional duplicate C003 → conditional duplicate C004 → conditional duplicate C043;
- C026.2 → conditional duplicate C043;
- conditional duplicate C002 → C025 → C027.4.

C002/C003/C004/C043 получают отдельные F1 Task IDs и перед работой проверяют уже существующий эквивалентный результат. C027.1, C027.2 и C027.3 после удаления C026.1 не имеют ни входящих, ни исходящих hard dependencies в принятом migration graph, поэтому становятся standalone Task и исключаются из F1.

C051 остаётся исключён: текущая кодовая база AI poise не имеет browser-owned surface, а миграция не создаёт универсальный browser runner для чужих проектов.

### SPRINT-ERP-MIGRATION-F3-ROUTING-HOOKS

F3 содержит собственные conditional duplicates необходимых prerequisites:

- conditional duplicate C002 → C003 → C004;
- conditional duplicate C002 → conditional duplicate C016;
- C004 + conditional duplicate C015 + conditional duplicate C016 → C017;
- C004 + conditional duplicate C026.2 → C043;
- C004 → C029.4.

C029.4 остаётся AI-poise-specific gate поверх существующих/явно зарегистрированных Python architecture checks. C029.2/C029.3 не переносятся как generic multi-language analyzer/ERP allowlist и поэтому не являются скрытыми prerequisites этого адаптированного C029.4.

### SPRINT-ERP-MIGRATION-F4-HOOKS-TELEMETRY

F4 также замыкает prerequisites локальными дублями:

- conditional duplicate C002 → conditional duplicate C003 → conditional duplicate C004;
- conditional duplicate C002 + conditional duplicate C015 → conditional duplicate C033 → conditional duplicate C020;
- C021 + conditional duplicate C004 → C023;
- C028.4 → C028.5;
- conditional duplicate C004 + conditional duplicate C020 + C023 + C028.5 → C031.

C093 не создаётся как отдельная Task. Его полезное hook-coverage раскладывается по владельцам C020/C021/C023/C028.*; C031 проверяет наблюдаемые эффекты уже после этих локальных prerequisites.

## Ревизия migration nodes

| Node | Решение после ретроспективы |
|---|---|
| C026.1 | **Исключить полностью**: глобальный session-owned working-root не нужен. |
| C002 | Мигрировать; создавать conditional duplicate внутри каждого Sprint, которому нужен этот prerequisite. |
| C003 | Мигрировать; F1/F4 используют local duplicate, F3 содержит собственную рабочую Task. |
| C004 | Мигрировать; F1/F4 используют local duplicate, F3 содержит собственную рабочую Task. |
| C015 | Мигрировать runner AI poise; F3/F4 используют local duplicate с обязательной reuse-проверкой. |
| C016 | Мигрировать test packages AI poise; F3 использует local duplicate. |
| C017 | Оставить в F3 после локальных C004/C015/C016. |
| C018 | Оставить только для cache собственных test packages AI poise. |
| C019 | Оставить evidence одного runner invocation AI poise. |
| C020 | Мигрировать; F4 использует local duplicate. |
| C021 | Оставить reader с явными file paths/ranges. |
| C023 | Оставить после локальных C004 и C021. |
| C025 | Оставить в F1 после local C002. |
| C026.2 | Оставить IDE/AST navigation; F3 использует local duplicate. |
| C027.1 | Оставить как standalone migration Task. |
| C027.2 | Оставить как standalone migration Task. |
| C027.3 | Оставить как standalone migration Task. |
| C027.4 | Оставить в F1 после C025. |
| C028.4 | Оставить async telemetry AI poise. |
| C028.5 | Оставить после C028.4. |
| C029.2/C029.3 | Не мигрировать как generic analyzer/ERP allowlist; C029.4 использует AI-poise-owned checks. |
| C029.4 | Оставить в F3 после C004. |
| C031 | Оставить в F4 после локальных owners hook/reader/telemetry. |
| C033 | Оставить runner policy; F4 использует local duplicate. |
| C043 | Мигрировать существующий IDE skill; F1 использует local duplicate, F3 содержит рабочую Task. |
| C051 | Исключить из текущей миграции. |
| C093 | Не создавать отдельную Task. |

## Порядок дальнейшего выполнения

1. `SPRINT-ERP-MIGRATION-SKILL-CATALOG` остаётся выполненной базой.
2. Продолжить замкнутый `SPRINT-ERP-MIGRATION-F2-RUNNER-EVIDENCE`.
3. Остальные Sprint можно выполнять независимо по их локальным dependency graph; общий размер или одинаковое количество Task не является целью.
4. Standalone C027.1/C027.2/C027.3 выполняются как самостоятельные задачи, когда будут выбраны.

Каждая реализация проверяет, что переносится возможность AI poise, а не неявная политика для всех target repositories. Conditional duplicate всегда сначала проверяет существующий эквивалентный результат и не повторяет уже удовлетворённую реализацию.
