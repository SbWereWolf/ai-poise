# Испытания HARNESS-DDD-04

Обновлено: **2026-09-06T19:20:28+05:00**. Срез **HARNESS-DDD-04**.

## Итог
**193 уникальные проверки прошли** на одинаковых Python-исходниках, тестах, конфигурациях и исполняемых примерах. Из них 42 новые проверки Evidence/observe/check; прежний один тест четырёх CLI-маршрутов разложен на четыре параметризованных случая без сокращения сценариев (ещё +3 к прежним 148). Поэтому 148 + 3 + 42 = 193, а не 45 новых возможностей.

Продуктовые маршруты всего нормативного каталога (653 случая) не объявлены выполненными. Проверены текущая реализация и явно названные демонстрационные процессы. Осмотр проводил тот же агент; логические аргументы и решения reviewer/пользователя в тестах — фиксированные входные данные, не независимая модель.

## Непересекающиеся финальные группы

| JUnit/log в evidence/ddd-step04/final | Проверок | Результат |
|---|---:|---|
| domain | 77 | PASS |
| content | 15 | PASS |
| application | 14 | PASS |
| feedback | 13 | PASS |
| legacy | 28 | PASS |
| evidence | 38 | PASS |
| evidence-demo | 4 | PASS |
| runner-short | 2 | PASS |
| runner-development-feedback | 1 | PASS |
| runner-documentation-feedback | 1 | PASS |
| **Всего** | **193** | **PASS** |

[Итог и состав групп](../evidence/ddd-step04/final/summary.json), [collection](../evidence/ddd-step04/final/inventory.txt), [SHA проверенных файлов](../evidence/ddd-step04/final/tested-files.json). JUnit с именами каждого test лежат рядом. Все collected cases учтены ровно один раз.

## Исполняемые новые пути
- Command-only check: отрицательный распознанный результат продукта → not_satisfied → отдельный осмотр → успешное завершение проверки. Это не failure Harness.
- Logical-only: PREPARE аргумент → сохранение → inspect exact revision → принятие; ни одного вымышленного command receipt.
- Mixed: PREPARE запускает команду → awaiting_continuation → CONTINUE со ссылкой на наблюдение → без повторного запуска → inspect.
- Observe feedback: наблюдение/аргумент → осмотр отклоняет → новая итерация → новое наблюдение/аргумент → осмотр принимает. Две отдельные версии и два решения сохраняются.
- Отсутствующий обязательный аргумент, чужой/stale observation ID, неправильный review revision, timeout/неожиданный формат результата не дают verified.
- Совпавшая автоматическая project-check не превращается в неблокирующий subject-check.
- Обновление explicit environment после наблюдений делает continuation устаревшим. После сбоя push тот же env переиспользует receipt; новый env требует новой проверки.
- SQLite trigger прерывает сохранение оценки: Task/proof/event откатываются вместе; уже завершённый command receipt остаётся сохранён как факт.
- Raw stdout/stderr после завершения остаются task-owned; временные ссылки не выдаются как долговременные.

## TDD и осмотр
Начальная tests-first Git-фиксация: `9a8552b` — до реализации Evidence API. Начальный RED (`01-red.txt`) — отсутствие нового модуля; он не выдаётся за runtime-дефект. Отдельные воспроизведённые поведенческие RED/GREEN:

| Проблема | RED | GREEN |
|---|---|---|
| Возврат аргумента A→B→A оставлял B текущим | 11-review-red.txt | 12-evidence-review-green.txt |
| Нераспознанный output засчитывался как отрицание критерия | 13-recognition-red.txt | 14-recognition-green.txt |
| Изменённый target уходил в continuation до проверки согласованности | 20-target-review-red.txt | 22-review-green.txt |
| Повтор push использовал receipt после изменения explicit env | 21-publication-review-red.txt | 22-review-green.txt |

Полные логи находятся в `evidence/ddd-step04/`. [Осмотр и принятые исправления](ddd-step04-code-review.md).

## Отрицательные проверки тестов
Пять одноразовых копий исходников с внесёнными дефектами отклонены соответствующими тестами: историческая дедупликация аргумента; timeout как наблюдение; resume push без execution signature; исключение env из signature; I/O в domain. **5/5 обнаружены.** Это отдельные mutation probes, не прибавленные к 193 продуктовым test cases.

[Результаты и вывод упавшего теста для каждой копии](../evidence/ddd-step04/23-negative-probes.json).

## Незавершённые промежуточные запуски
`05-regression-first.txt`, `08-runner-regressions.txt`, `09-demo-isolated.txt` остановлены лимитом инструмента выполнения; они не PASS. `03-integration-red.txt` отражает смену явного fixture-контракта. `10-runner-core.txt` выявил ошибку механического обновления самого fixture; лишнее присваивание удалено, исходная проверка сохранена. Финальная успешная совокупность — только десять таблиц выше.

## Воспроизведение

```bash
export PYTHONPATH="$PWD/src"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
python -m pytest -q
python tools/probe_step04_mutations.py --output /tmp/ddd04-mutations.json
```

В среде с коротким пределом одного tool call pytest выполняется пакетами по указанным JUnit-группам. Исходный полный пример runner_demo по-прежнему содержит четыре маршрута.

Новый документированный JSON-пример отдельно структурно проверен CheckRegistry/EvidencePlan; interpreter в нём требует явной замены и не запущен как скрытый default. Изменения упаковки/Markdown после итоговой suite не меняют проверенные исполняемые файлы.

## Ограничения доказательности
Нет проверки реальных ChatGPT/Codex hooks, JetBrains, Gmail или внешнего remote. Git-тесты используют локальные bare remotes. Hash явно заданного execution context не доказывает неизменность любого внешнего сервиса. Полной миграции/recovery/асинхронной материализации нет. Содержательную истинность аргумента оценивает reviewer, библиотека проверяет структуру, ссылки и решения.

## Проверка распакованной поставки

Завершена: **2026-09-06T19:23:20+05:00**. Из промежуточного архива извлечена отдельная копия; проверены 38 core/API/SQLite/architecture cases и четыре CLI-сценария DDD-04. **42/42 PASS**, это повтор уже включённых в 193 проверок, не 42 новых случая. Все 74 файла финальной тестовой фиксации совпали с распакованной копией. [Логи и результаты](../evidence/ddd-step04/archive-smoke/summary.json). После этой проверки исполняемые исходники не менялись; финальный ZIP проверяется контрольными суммами.
