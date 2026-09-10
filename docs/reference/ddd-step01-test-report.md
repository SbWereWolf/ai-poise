# Испытания HARNESS-DDD-01

Обновлено: 2026-09-06T16:55:38+05:00.

## Фактически выполнено

Исходный прототип: 28 PASS (baseline.txt). После тест-first контракта: 32 RED — отсутствующее поведение/API, не ошибка импорта коллекции. Чистые domain/content после реализации: 24 PASS. Первая сквозная интеграция: 62 PASS. После self-review добавлены конкретные regressions, всего: **69 PASS**.

В финальном наборе: 64 поведенческих unit/integration/CLI checks, 2 архитектурных checks, 3 прежние статические проверки документации. Это не полный приёмочный каталог Harness.

Команда: `PYTHONPATH=src python -m pytest -q --durations=5`. Результат: `evidence/ddd-step01/all-green.txt`. Отдельно `python -m compileall -q src` и `git diff --check`.

Один промежуточный общий запуск был прерван внешним timeout инструмента на 60 секундах. Он сохранён как interrupted-suite.txt и НЕ засчитан успешным; последующий полный запуск завершился: 69 passed in 31.44s.

## RED/GREEN и осмотры

До реализации сохранены tests + минимальные публичные skeleton без поведения отдельным Git-коммитом. Начальный RED по новым domain методам использует NotImplementedError контрактного skeleton; это не доказательство RED приложения/TDD-regression. Настоящий RED приложения доказывается отдельно demo: исполнился конкретный тест, найден ожидаемый AssertionError, затем тот же тест GREEN.

Осмотр тестов: [протокол](ddd-step01-test-review.md). Осмотр кода и исправления: [протокол](ddd-step01-code-review.md).

## Проверенные пути

1. Полный прежний TDD demo, worktree, RED/GREEN, commit и private push в локальный bare remote, исходная main неизменна.
2. Проверенный отчёт → замечание пользователя → rework → новый отчёт; обе секции и оба verified reports доступны после runtime cleanup.
3. Неверный код → неуспешный check → исправленный код при прежнем payload → новая проверка без дублирования слоя.
4. A→B→A → три содержательных слоя; ещё один A → replay.
5. Принял/остановись отличается от принял/продолжай.
6. SQL failure при записи task event откатывает root, submission и sections. FK отклоняет чужого owner. Stale version не перезаписывает Task.
7. Старый store v1 отклоняется, его bytes остаются прежними.

## Проверка архитектурных guards

В отдельных временных копиях внесены domain import sqlite3, runtime assignment status и SQL UPDATE tasks из runtime. Все три копии отклонены конкретными архитектурными assertions (architecture-negative-checks.json). Это демонстрация этих guards, не доказательство невозможности любого динамического Python-обхода.

## Границы

Не измерялся фактический LLM token usage; нет доступа к надёжному счётчику этой сессии. Не проверялись cloud hooks, JetBrains, Gmail, production remote, все 13 процессов. В demo ответы осмотра задаёт fixture. Миграции не выполнялись. Испытания старого полного каталога по-прежнему NOT_RUN.

## Проверка поставки

2026-09-06T17:01:28+05:00: независимая копия исходников из поставки проверена заново, `69 passed in 32.05s`, exit code 0. Все Python-исходники совпадают с проверенной копией; см. package-verification.json и package-suite.txt.
