# Осмотр кода и исправлений DDD-04B

Обновлено: **2026-09-06T21:22:42+05:00**. Самоосмотр; независимый reviewer не участвовал.

## Проверенные границы
- Pure domains work/artifact_factory/interactions не используют filesystem, SQL или processes.
- Application WorkTools вызывает порты; lifecycle остаётся Task, observers не принимают результат вместо inspection.
- Новые таблицы событий/receipts имеют отдельного инфраструктурного владельца и короткие transaction/lock.
- Генератор не принимает произвольный absolute destination или скрытые defaults шаблона.
- SQL transaction не объявляется атомарной вместе с файловыми эффектами; сохранившийся файл после неуспешного content gate допускает повтор.
- Metric source, coverage, delivery observation названы честно; unknown history не равна нулевому расходу.

## Обнаруженные и исправленные дефекты
1. **Resume submission потерял sections.** Старый latest_submission читал только envelope, секции находились в отдельной таблице. При переходе на result_template object это стало видимым: следующие payload не содержали report. Исправлено объединением envelope с соответствующими section layers. Проверено прямым resume и прежними content/evidence маршрутами.
2. **Недостаточный output budget позволял начать bootstrap.** Создавался worktree, после чего ответ нельзя было уложить. Новый тест воспроизвёл дефект; minimum receipt проверяется до action. Непригодный config отклоняется, а не используется fallback.
3. **Batch content query терял позицию процесса.** Четыре старых runner demo ожидали stage; новый фасад возвращал содержимое без него. Отдельный RED (`content-context-red.txt`) подтвердил KeyError; runtime.show_content теперь включает текущие stage/iteration. Проверка повторена на том же API.

Прежние demo сами переведены на direct CLI. Их транспортные несоответствия не выдаются за скрытую совместимость; продуктовые aliases `harness verify` и result-file reader удалены.

## Ограничения без расширения среза
All-or-nothing несколько файлов+DB, автоматический final Chat reply capture, full metric intervals, Sprint domain и binary template generators не реализованы. При неизвестном внешнем исходе/конфликте не используется магическое восстановление. Вердикты command/evidence не превращаются в успех от генерации документа. Изменение package input после доклада требует rework.

## Основания технических решений
Строгий JSON stdin и UTF-8; шаблоны используют Python string.Template без выполнения кода. SQLite transaction относится к DB, а не к файловой системе. Справочные API: https://docs.python.org/3.13/library/json.html , https://docs.python.org/3.13/library/string.html , https://www.sqlite.org/lang_transaction.html . Эти источники не являются свидетельством прохождения наших тестов.

## Тестовый запуск
Collection без `tests` захватила учебные test_double.py в временных папках отчёта. Не объявляется продуктовой регрессией. Временные workspaces вынесены из выдаваемого дерева; helper создаёт свои pytest basetemp в отдельном OS temporary directory и очищает после получения результата. Логи и JUnit сохраняются.
