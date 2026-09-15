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
