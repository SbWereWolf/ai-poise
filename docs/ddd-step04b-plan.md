# HARNESS-DDD-04B — план и контракт среза

Начало: 2026-09-06T20:51:31+05:00.

Один пакет work передаёт operation/input/messages. verify принимает result и массив создания artifacts. Нет ручного служебного result JSON; bootstrap возвращает объект result_template. Артефакты — explicit template+parameters или ready text; существующие — path-only. Factory ограничена текущими roots, не перезаписывает прежний файл и проверяет весь пакет до публикации. Ошибка внешней записи не изображается SQL-атомарной: повтор того же содержимого возобновляет создание.

Task принимает собственные изменения через существующий API. InteractionLedger отдельно сохраняет факты пользовательских сообщений (это затрата даже при неуспешном verify), без transcript/token guesses. Первый основной binding не переносится между задачами при повторах. В текущем срезе partial/unavailable coverage; автоматическое наблюдение всего Chat — не реализовано.

TDD: ArtifactFactory и negative roots/templates → batch API/direct result → message identity/cancel/repeat → адресное batch reading → CLI и end-to-end mixed evidence. Старые сценарии сохраняются, test clients переходят с файлов на значения. Никакого backwards input, schema migration. Новые schema явно обязательны.
