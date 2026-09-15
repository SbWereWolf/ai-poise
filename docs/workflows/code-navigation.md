# Навигация IDE и AST при разработке AI poise

Обновлено: **2026-09-15**.

## Конкретная папка и подтверждение индекса

`tools/navigate_code.py --receipts /path/to/observations` принимает один JSON-пакет
через stdin. `checkout` — конкретный абсолютный или относительный путь к копии
AI-poise; `files` — непустой список путей проверяемых файлов относительно этой копии.
Путь может находиться вне установки Poise. Это объект операции, а не новая политика
разрешения путей. Инструмент не переключает cwd, конфигурацию, executable или импорты
работающего Poise. Передавать `cwd` и команды провайдеров нужно явно.

`operation`: `symbol`, `usages`, `hierarchy`, `inspection`, `documentation` или
`structural`. `providers` — упорядоченный список объектов с полями:

- `id`, `kind` (`ide`/`ast`), `operations` — объявленная применимость;
- `probe` — существующий полный контракт `ProbeSpec` (command либо read-only MCP
  stdio). Tool name, argv, cwd, environment, лимиты и predicates задаются явно;
- `identity_path`, `digests_path` — непустые пути JSON-ключей к фактической папке
  индекса и словарю `relative file -> SHA-256` **индексированных** байтов.

Для command пути assertions относятся к stdout JSON; для MCP — к результату
`tools/call`, например к `structuredContent`. До запуска валидируется весь пакет.
Сервис добавляет exact assertions папки и содержимого заявленных файлов и использует
существующий `LocalProbeExecutor`: не добавляет MCP transport или собственный indexer.
Его существующий `${workspace}` token — только binding argument конкретного probe;
это не источник executable или конфигурации. Он не добавляется в cwd автоматически.

Провайдер должен сообщить идентичность реально обслуживаемого индекса, а не просто
вернуть переданный `projectPath` или заново хэшировать диск вместо индексированных
байтов. Провайдер без такого read-only доказательства неприменим к подтверждению
freshness: отсутствие полей и несовпадение digest дают `unavailable`. Локальное изменение
файла во время запроса даёт `inconclusive`, без попытки выдать старый результат за новый.
Проверка ограничена `files`, не доказывает актуальность всего индекса.

## Выбор и результат

Для semantic navigation используется первый успешно подтверждённый IDE provider;
неудачные ответы с причинами и receipts сохраняются перед AST fallback. Для
repository-wide `structural` AST вызывается прямо. AST не заменяет IDE inspections,
quick documentation, rename или formatting; мутирующих операций здесь вообще нет.
Объявленная применимость — не live evidence. MCP inventory и ответ проверяются
существующим probe owner. Command adapters считаются явно выбранными доверенными
read-only инструментами: API не является sandbox для произвольного shell-кода.

`proved` и exit 0 означают успешную конкретную операцию и declared-scope proof.
`unavailable`/`inconclusive` и exit 1 сохраняют причины; неверный пакет даёт `rejected`
и exit 2 до запуска. Вывод содержит `scope`, `provider`, receipts всех попыток.
Полный ответ/результат навигации находится в stdout/protocol receipt существующего
probe owner. Temporary global index config в проверяемом репозитории не записывается.

В этой поставке протокол проверен локальными процессами с независимо подготовленными
ответами. Подключение живой JetBrains IDE/ast-index, её язык и конкретный индекс
подтверждаются отдельно в установленном окружении. Не следует автоматически создавать
несуществующие команды или объявлять `operation_proved` по наличию конфигурации.

Действует [правило выбора IDE](../governance/jetbrains-mcp-policy.md#правило-выбора-инструмента).
