# Ретроспектива миграции ERP → AI poise

Обновлено: **2026-09-15**.

Этот документ фиксирует исправленную семантику путей и область ERP → AI poise migration перед следующими спринтами.

## Разрешение путей и рабочие копии

AI poise сохраняет обычное разрешение путей для своих hooks, tools, конфигурации и runtime-кода. Task, сессия или выбранная рабочая копия это поведение не переопределяют.

Рабочая копия — обычный путь файловой системы, который конкретная операция получает как вход. Он может быть абсолютным или относительным в соответствии с контрактом операции и может указывать на произвольный каталог. Если subprocess должен собирать или проверять код из этой рабочей копии, его `cwd` задаётся конкретным путём.

Если отдельный Git worktree не используется, операция получает путь к тому checkout, где выполняется работа. Дополнительной глобальной настройки для этого не требуется.

`AGENTS.md`, skills и их reference-файлы разрешено читать из явно выбранной рабочей копии, когда задача должна учитывать branch-local инструкции. Их локальные ссылки разрешаются по обычным правилам соответствующего документа/файла. Чтение branch-local инструкций не меняет расположение исполняемого кода, hooks или конфигурации AI poise.

## Область ERP migration

Переносимые из ERP механизмы становятся возможностями **AI poise**. Они не образуют обязательный runtime, cache, runner, hook layer, boundary analyzer или layout для каждого репозитория, который когда-либо разрабатывается через AI poise.

Внешняя кодовая база сохраняет собственные test runners, cache layout, fixtures, lockfiles, browser/runtime topology и правила сборки. AI poise может выполнить явно зарегистрированную команду, передать ей конкретные пути/`cwd` и сохранить результат через собственные API, но не переопределяет внутренние механизмы чужого проекта только потому, что похожая возможность существовала в ERP.

Это особенно важно для C018: мигрируется семантика test-package cache, принадлежащего самому AI poise. Для проверки AI poise в любой его рабочей копии package membership задаёт исходники, тесты, support и fixtures; пути участников нормализуются относительно каталога проверяемого checkout. Другие кодовые базы используют свои cache-механизмы и свои способы вычисления ключей.

## Решение по C026.1

C026.1 **исключён из migration plan**.

Связь Task с optional worktree уже является существующей возможностью AI poise и не требует отдельного переноса. Предлагавшаяся C026.1 автоматическая подмена разрешения путей tool adapters путём рабочей копии отклонена. Все прежние hard dependencies на C026.1 удаляются; конкретные задачи явно принимают нужный filesystem path там, где действительно работают с кодом рабочей копии.

## Исправленный состав спринтов

### SPRINT-ERP-MIGRATION-SKILL-CATALOG

Без изменения состава: C005.1 → C012. Спринт уже реализован и является базой дальнейшей работы.

### SPRINT-ERP-MIGRATION-F2-RUNNER-EVIDENCE

Следующий исполняемый спринт содержит семь задач и относится только к проверкам самого AI poise:

1. **C002** — scoped rules и архитектурные границы AI poise; без policy engine для произвольных target repositories.
2. **C015** — один зарегистрированный runner проверок AI poise; exact argv и явный конкретный filesystem `cwd`/path на каждый запуск.
3. **C016** — именованные test packages кодовой базы AI poise и их membership.
4. **C019** — declared outputs/evidence конкретного запуска runner AI poise.
5. **C033** — timeout/progress/cancel policy runner AI poise.
6. **C018** — package cache тестов AI poise: membership/content fingerprint вычисляется для проверяемой рабочей копии AI poise, а пути участников нормализуются относительно каталога этой копии.
7. **C020** — host-hook контроль зарегистрированных запусков AI poise через того же runner.

Hard dependencies внутри спринта: C002 → C016; C002 → C033; C015 → C019; C015 → C033; C015 → C018; C016 → C018; C033 → C020.

C017 перенесён из F2 в F3: он зависит от C004 и относится к routing/impact selection, а не к runner сам по себе.

### SPRINT-ERP-MIGRATION-F1-ROUTING-TOOLS

После F2 выполняется tooling-спринт для самого AI poise:

- C026.2 — IDE/AST navigation получает конкретный путь анализируемой рабочей копии AI poise; без изменения обычного разрешения путей Poise.
- C027.1 — каталог проверок инструментария AI poise.
- C027.2 — проверка достижимости references поставляемых skills AI poise.
- C027.3 — проверка ссылок и anchors документации AI poise.
- C025 — runtime/environment helper разработки AI poise на заявленной платформе; без runtime-policy для внешних кодовых баз.
- C027.4 — fallback Markdown formatter репозитория AI poise с явными file paths; IDE остаётся предпочтительным способом.

Hard dependency внутри спринта: C025 → C027.4. C002 из завершённого F2 используется как внешний результат и не дублируется новой Task.

**C051 исключён:** Skill Catalog уже зафиксировал, что Playwright не входит в текущую поставку, а у AI poise нет собственной browser application/test surface. ERP Playwright skill не является основанием создавать универсальный browser runner для проектов, разрабатываемых через Poise. Если у самого AI poise появится browser-owned поверхность, это будет новая самостоятельная потребность, а не продолжение этой миграции.

### SPRINT-ERP-MIGRATION-F3-ROUTING-HOOKS

Этот Sprint теперь ограничен routing и boundary integration самого AI poise:

- C003 — условия выбора поставляемых AI-poise skills/profile metadata по фактам о работе над AI poise; без универсальной схемы профилей для внешних репозиториев.
- C004 — единый router AI poise. Он получает нужные paths/diff/context как явные входы; branch-local `AGENTS.md` и skills разрешено читать из явно указанной рабочей копии.
- C017 — выбор зарегистрированных проверок AI poise по requirements/diff/impact с результатами C015/C016 и C004.
- C043 — сверка существующего IDE skill AI poise после C004 и C026.2; второй skill/роутер не создаётся.
- C029.4 — автоматический gate архитектурных границ AI poise через существующие или явно зарегистрированные проверки Python-кодовой базы. Универсальный multi-language analyzer для чужих репозиториев не создаётся.

Hard dependencies: C003 → C004; C004 → C017; C004 → C043; C004 → C029.4. Дополнительные готовые predecessors: C002 для C003, C015/C016 для C017 и C026.2 для C043.

### SPRINT-ERP-MIGRATION-F4-HOOKS-TELEMETRY

Создаётся отдельный завершающий спринт для оставшихся механизмов AI poise:

- C021 — bounded/confirmed document reader AI poise, принимающий явные filesystem paths/ranges.
- C023 — восстановление Task/route/read context после compaction без копирования Task DB.
- C028.4 — асинхронность и устойчивость telemetry/accounting AI poise.
- C028.5 — полнота API metrics и source timestamps.
- C031 — явная диагностика собственных hooks/runtime effects AI poise; не observer соблюдения правил и без сравнения расположения runtime и рабочей копии.

Hard dependencies: C021 → C023; C028.4 → C028.5; C023 → C031; C028.5 → C031. Внешние готовые predecessors C004 и C020 также входят в sprint-level integration checks.

**C093 не является отдельной Task.** Исходная migration row прямо относит полезное покрытие hooks к существующим владельцам C020/C021/C023/C028.* и говорит не создавать новую migration-task для C093. Поэтому соответствие подтверждённых native host events проверяется на уровне integration/Definition of Done этого спринта, без второго hook layer.

## Ревизия migration nodes

| Node | Решение после ретроспективы |
|---|---|
| C026.1 | **Исключить полностью**: попытка подменять им обычное разрешение путей отклонена. |
| C002 | Оставить; только scoped rules/boundaries самого AI poise и точная документация path semantics. |
| C015 | Оставить; runner проверок AI poise, concrete argv + concrete filesystem `cwd`/paths. |
| C016 | Добавить в F2; test-package model только для кодовой базы AI poise. |
| C019 | Оставить; evidence runner AI poise. |
| C033 | Оставить; timeout/progress/cancel runner AI poise. |
| C018 | Оставить; cache только test packages AI poise, ключ относительно проверяемой копии кода. |
| C020 | Добавить в F2; hooks контроля runner AI poise. |
| C026.2 | Оставить; IDE/AST над явно переданным путём к копии AI poise. |
| C027.1 | Оставить; проверки собственного tooling. |
| C027.2 | Оставить; references собственных поставляемых skills. |
| C027.3 | Оставить; документация AI poise. |
| C025 | Оставить; runtime helper разработки AI poise, не universal project runtime. |
| C027.4 | Оставить; Markdown fallback для репозитория AI poise. |
| C051 | **Исключить**: browser-specific capability не принадлежит текущей кодовой базе AI poise. |
| C003 | Оставить; routing metadata поставляемых AI-poise skills. |
| C004 | Оставить; единый router AI poise, явные входные paths/facts. |
| C017 | Оставить в F3; impact selection для проверок AI poise. |
| C043 | Оставить в F3; gap-audit существующего IDE skill, без второй копии. |
| C029.4 | Оставить; gate архитектуры AI poise, без generic multi-language layer C029.2/C029.3. |
| C021 | Оставить; reader AI poise с явными paths/ranges. |
| C023 | Оставить; восстановление внутреннего context AI poise. |
| C028.4 | Оставить; асинхронная telemetry AI poise. |
| C028.5 | Оставить; source metrics/timestamps AI poise. |
| C093 | **Не создавать отдельную Task**; интеграционное покрытие принадлежит владельцам функций и sprint DoD. |
| C031 | Оставить; диагностика собственных hook effects AI poise. |

## Исключённые и заменённые элементы старого графа

- Все три копии **C026.1** исключаются и остаются только историческими записями исходного плана.
- Дубли C002 заменены одной канонической задачей F2.
- Дубли C043 заменены одной канонической задачей F3.
- Дубли C017 заменены одной канонической задачей F3.
- C051 исключён из исполняемого плана как browser-specific capability, не принадлежащий текущей кодовой базе AI poise.
- C093 исключён как отдельная Task; hook coverage становится integration-критерием F4.
- Старые F3 C031/C093 выводятся из F3; новый C031 создаётся в F4 после реальных prerequisites.
- C029.2/C029.3 не добавляются как скрытые prerequisites C029.4: универсальные multi-language analyzers и ERP-specific allowlist не мигрируют в AI poise.

## Порядок дальнейшего выполнения

1. Завершённый `SPRINT-ERP-MIGRATION-SKILL-CATALOG` остаётся базой.
2. Выполнить исправленный `SPRINT-ERP-MIGRATION-F2-RUNNER-EVIDENCE` из семи задач.
3. Выполнить `SPRINT-ERP-MIGRATION-F1-ROUTING-TOOLS`.
4. Выполнить исправленный `SPRINT-ERP-MIGRATION-F3-ROUTING-HOOKS`.
5. Выполнить новый `SPRINT-ERP-MIGRATION-F4-HOOKS-TELEMETRY`.

Каждая реализация должна проверять, что переносится возможность AI poise, а не неявная политика для всех target repositories.
