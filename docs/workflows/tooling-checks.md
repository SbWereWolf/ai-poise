# Проверки собственного tooling AI poise

## Владельцы и ограниченные пакеты

C027.1 не создаёт новый orchestrator или каталог команд ERP. Используется существующий
`config/testing/test-packages.json` (C016), `AiPoiseTestPackages`,
`tools/run_ai_poise_test_package.py` (C018) и `RegisteredCheckRunner` (C015).
Это проверки инструментов **кодовой базы AI poise**, а не политика чужих репозиториев.

| Функция | Владелец проверки | Пакет |
|---|---|---|
| Синтаксис поставленных Python tools, безопасный `--help`, smoke shell syntax | `tests/delivery/test_tooling_catalog.py` | `tooling-fast` |
| Правила migration scope, спринтов и путей | `tests/delivery/test_migration_planning_policy.py`, `test_migration_current_state.py` | `tooling-fast` |
| Полное восстановление, hash/membership/SQLite/Git, отказ на dirty или повреждение | `tests/delivery/test_work_checkpoint.py` | `tooling-recovery` |
| Изолированное исполнение pytest-модулей и terminal JUnit evidence | `tests/test_package_execution.py` | `tooling-recovery` |
| Runner, timeout/cancel, package cache, declared outputs | существующие `tests/runner`, `tests/verification` | `verification-runner` |
| Сессионные hooks и передача наблюдений | существующие `tests/hook_transport`, `test_native_cancellation.py` | `hooks`, `verification-runner` |
| Skill metadata и decomposition | существующие `tests/skills` | `skills` |

`tooling-fast` — синтаксис/контракты, без destructive maintenance. `tooling-recovery`
запускает реальные Git/SQLite/subprocess round trips. Успешный синтаксический разбор
**не является** доказательством семантики каждого исторического mutation/upgrade script.
Их запуск на текущем пользовательском checkout не подменяется тестовым сценарием.
Для изменения такого инструмента нужны его собственные предметные fixtures/проверки.

## Запуск для конкретной копии AI poise

Из окружения, в котором установлен проверяемый Poise:

```sh
python /path/to/ai-poise/tools/run_ai_poise_test_package.py \
  --root /path/to/checked-ai-poise --package tooling-fast --timeout 30 --fresh
python /path/to/ai-poise/tools/run_ai_poise_test_package.py \
  --root /path/to/checked-ai-poise --package tooling-recovery --timeout 60 --fresh
```

Пути — конкретные filesystem arguments. Здесь `--root` обозначает существующий аргумент
проверяемой копии в C018, а не новый root запущенного Poise. Код кандидата можно проверять
с его тестовым import environment, не меняя загрузку live hooks/tools.
Кэш и диагностические файлы принадлежат `.poise-test-cache` этой копии. Для проверки
изменений tooling сначала выбирается его owning test/package, затем bounded smoke.
Не запускать unfiltered `run_test_packages.py`/`run_slice_tests.py` для этой миграции.

## Структура и достижимость skill references

C027.2 переиспользует `load_skill_catalog` и общий владелец локальных Markdown-ссылок
`poise.infrastructure.documentation_checks`, выделенный из существующей delivery-проверки.
Пакет `skill-references` проверяет поставленные skills этой копии AI poise; package cache
учитывает их Markdown, метаданные и локальный NDJSON-индекс. Нового автоматического router
или загрузчика правил целевого репозитория нет.

```sh
python /path/to/ai-poise/tools/check_skill_references.py --checkout /path/to/checked-ai-poise
python /path/to/ai-poise/tools/check_skill_references.py \
  --checkout /path/to/checked-ai-poise --skill vue-best-practices
```

Структурный граф начинается с `SKILL.md`, указанных существующим каталогом, либо с явно
выбранных ID. Он проходит вложенные локальные Markdown-ссылки и явно названные справочные
пути/NDJSON-индекс. Для каждой дуги сохраняются файл, строка и контекст условия загрузки.
Это аудит навигации, **не указание прочитать все найденные инструкции**. Наличие пути
доказывает лишь достижимость. Эвристика `always read/load all` возвращает review note,
не доказательство ошибки, не автоматическое удаление и не семантическую оценку качества.
C004 selection может служить источником явных ID после его реализации; здесь routing не заявлен.

Поддержаны обычные, reference-style и вложенные в списки ссылки. Fenced/indented code,
front matter и HTML-комментарии не считаются командами чтения. Проверка не ходит в сеть,
не выполняет содержимое ссылок; выход за каталог через относительный путь или symlink —
ошибка. Локальные отсутствующие targets и недостижимые справочники выводятся отдельно.
При явном выборе результат ограничен выбранными skills и их ссылочной связностью.

Проверка обнаружила одну действительно недостижимую owned-reference
`layout-and-design/references/frontend-standard.md` с устаревшим обязательным трио.
После ручного сопоставления с текущим skill её правило исправлено на условный выбор
специалиста, добавлена условная ссылка. Четыре Vue-ссылки уже были корректны: исправлен
разбор вложенного списка, а не переписаны справочники. Ничего автоматически не удалялось.
