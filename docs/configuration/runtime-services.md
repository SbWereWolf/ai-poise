# Runtime services: DDD-07, локальный исполняемый срез

Обновлено: **2026-09-13**. Работает Linux/CPython 3.13 CLI. Описанные автоматические операции реализованы; живой Codex, JetBrains, ChatGPT connector и Gmail не выдаются за протестированные подключения.

## RD-002: единая граница сессии

Каждая публичная операция `work`, независимо от прямого CLI, runtime adapter или native hook,
проходит через один application-owner `SessionEstablisher`, а затем вызывает тот же
`WorkTools`. Источник identity не создаёт отдельный Task API и не предоставляет особых прав:
native и generated origin равноправны. Ни transport, ни origin не участвуют в авторизации,
выборе этапа или разрешении мутаций.

Если доступен `CODEX_SESSION_ID` (либо `CODEX_THREAD_ID`), прямой CLI использует нативный ID.
Без нативного ID оператор задаёт абсолютный путь `POISE_CALLER_BINDING` к сохраняемому
служебному JSON-файлу. Родительский каталог должен существовать; AI poise атомарно создаёт
сам файл с generated caller ID. Повторный процесс с тем же файлом получает прежнюю сессию без
предварительной проверки и без повторной инициализации. Файл является обычной долговременной
привязкой, а не секретом: дополнительный anti-tamper протокол не добавлен.

Соответствие caller → session хранится в `runtime_bindings`. Уже назначенная identity никогда
не заменяется, в том числе если нативный ID стал доступен позднее. Для generated identity
кандидат создаётся вне блокировки, а уникальность резервируется транзакционно; при коллизии
кандидат генерируется заново. `read-only` вход может создать binding и session metadata, но не
Task, Sprint или другой бизнес-субъект.

Прямой launcher-independent запуск имеет вид:

```bash
POISE_CONFIG=/absolute/project.json \
POISE_CALLER_BINDING=/absolute/persistent/caller.json \
python -B -m poise work
```

Caller не выбирает внутреннюю сессию произвольной переменной. Агент не подменяет нативную или
сохранённую identity самостоятельно.

## Один существующий API, два транспорта

`poise runtime --settings <path>` принимает один JSON из stdin и устанавливает identity через
ту же границу. Затем он вызывает `WorkTools.invoke`, не второй Task API. Идентификатор проекта
берётся из явно выбранного project config, не угадывается по cwd.

```json
{
  "identity": {"kind":"external","session_id":"conversation-17","agent_id":"main"},
  "capabilities": [],
  "transcript": null,
  "work": {
    "operation":"bootstrap",
    "input":{"task":null,"decision":null,"feedback":null,"rework_stage":null},
    "messages":[]
  }
}
```

Для external identity ключ включает проект, adapter_id, session_id и agent_id. Поэтому два агента одного разговора не получают общий runtime. Для среды без внешнего session ID используется явное `{"kind":"generated","request_id":"launch-17","agent_id":"main"}`: UUID создаёт инструмент, а повтор того же launch request возвращает прежнюю сессию. У нового request отдельная сессия. Ни один общий current-session.json не применяется.

Settings имеет точные поля:
```json
{
  "schema":"runtime-adapter-1",
  "project_config":"/absolute/poise/project.json",
  "adapter_id":"codex-local",
  "transcript_roots":["/absolute/rollouts"],
  "max_scan_bytes":1048576,
  "max_line_bytes":65536,
  "max_events":100,
  "required_capabilities":[]
}
```
Значения и пути — пример явных входов, не defaults кода. В исполняемом `examples/runtime_demo.py` весь settings-файл и проект создаёт setup-драйвер. Специальный product-level wizard установки runtime adapter пока не добавлен. Текущая рабочая задача не редактирует файлы settings или БД вручную.

## Возможности: явный inventory, не воображаемый discovery
Каждый элемент capabilities содержит `id,status,tool_ref,project_path,source,version`. Статус available требует непустой tool_ref; source — runtime_manifest или agent_reported. Для unavailable допускается явный null. Отсутствие требуемой capability останавливает начало работы и возвращает конкретную причину.

Адаптер сохраняет и возвращает inventory, но не проверяет здоровье внешнего MCP endpoint и не вызывает IDE. В частности, `project_path` пока является входным фактом; end-to-end handshake соответствия IDE worktree остаётся интеграционной задачей. Нельзя считать supplied available доказательством выполнения реального refactoring.

## Наблюдаемые пользовательские сообщения из локального JSONL
Опциональный transcript содержит только `path` и явный `initial_offset` в байтах на границе записи. Project batch.message_source обязан точно совпадать с `{"id":adapter_id,"mode":"runtime_event"}`. Одновременно подавать reported messages и тот же transcript нельзя.

Поддержан наблюдавшийся формат Codex: `type=event_msg`, `payload.type=user_message`, строковое `payload.message`. Счётчик не читает дублирующий response_item/user как второе сообщение. Текст пользовательского сообщения не копируется в AI poise DB/journal; сохраняются идентичность, timestamp и привязка через существующий InteractionLedger. Причина остаётся неизвестной, пока нет отдельного содержательного источника.

Парсер потоковый. Он читает только завершённые строки до explicit byte/event limits. Незавершённый хвост не потребляется. Следующий вызов продолжает курсор; достижения лимита не приводят к потере непрочитанного остатка. Текущий batch всегда имеет coverage=partial: даже достижение EOF не доказывает, что доступен весь разговор.

Курсор хранится для конкретной сессии и файла: offset, device/inode, начало и hash последней прочитанной записи. Замена, усечение или изменение проверяемого якоря не приводят к сбросу счётчика. Это модель append-only источника, не архивная проверка каждого ранее прочитанного байта. Переход на другой формат/источник автоматически не выполняется.

Начальный offset обязателен: при присоединении к длинной существующей сессии источник должен обозначить начало относящегося к работе диапазона, а не приписывать новой задаче всю историю. При handoff той же задачи повтор уже полученных событий не увеличивает её счётчик. Сообщения чтения при активной задаче тоже учитываются. Cursor фиксируется после обработки пакета; если транспорт повторил источник, InteractionLedger исключает повтор.

В ChatGPT без локального rollout используется `transcript:null` и явные наблюдённые сообщения обычного Work envelope. CLI не получает скрытую историю разговора. Здесь нет подсчёта токенов по символам или предположения о списанных кредитах.

## Post-command parser hook
Project config содержит обязательный `runtime_services.output` (исходный пример в ../config/runtime.example.json). Профили команд имеют matcher из точных argv tokens, parser, правила выбора, limits и имена файлов. Поддержаны два режима одного класса OutputParser:

1. `primary` синхронно формирует ограниченный preview над зафиксированным stdout/stderr;
2. `materialize` в отдельном subprocess создаёт полный и дополнительные варианты над тем же receipt.

Для известных команд применяется `lines` с явным regex, например unittest. Если совпадений нет, выбранный результат пуст, а не заменяется скрыто другой стратегией. Неизвестная команда использует обязательный явно заданный профиль `tail`. Неоднозначные known matchers отклоняются; parser никогда не выбирает бизнес-вердикт.

В текущем срезе hook подключён к `_execute_checks`: штатные команды проверок и Evidence-наблюдения. Служебные Git-команды и apply/probe-команды Plan adapter пока сохраняют прежний raw receipt без новых представлений. Произвольные нативные инструменты Chat/Codex не перехватываются.

Full является точной конкатенацией stdout затем stderr; отдельные исходные потоки сохраняются. Это не хронологическое объединение потоков. Extended — ограниченный byte tail. Selected — ограниченные совпадающие фрагменты. В manifest записаны paths, размеры, количество строк и digest. Для extremely long line выбор производится bounded фрагментами: selected не заменяет полный исходный файл.

## Асинхронность и конечная граница
Worker запускается после каждой завершённой проверки и может работать одновременно со следующей проверкой/финализацией. Перед выдачей итогового verify или CONTINUE-контекста результат материализации дожидается явной границы `finish`. Модели не надо делать polling. Срок ожидания задаётся конфигом; timeout завершает process group worker, фиксирует Incident, но не переписывает test verdict.

Это конкретный post-command hook и финализационный barrier, а не произвольный конфигурируемый hook engine. Неблокирующий worker не меняет task/stage, findings или acceptance. Его PID не входит в API результата.

## Ошибки и срок жизни ссылок
stdout/stderr остаются у задачи; representations находятся под её run root, а не под удаляемым runtime. Перед публикацией manifest=ready все файлы завершены. Частичное представление не выдаётся как готовое. Raw digests проверяются до и после materialization.

Внутренний сбой парсера регистрируется как Incident, сохраняется в проверенном отчёте и виден при повторном чтении. Тест может остаться passed при parser error. Ошибка создания каталога представлений не уничтожает фактическое evidence команды; manifest в ответе имеет null, а не ложную ссылку.

Первичный response содержит короткий результат и адрес каталога/manifest. Полные данные доступны нативным чтением либо пакетным show:
```json
{"id":"details","kind":"tool_result","receipt_id":"receipt-id","representation":"full","range":{"unit":"lines","start":1,"end":20}}
```
Это один элемент массива queries обычного `show`. Диапазоны строк — включительные с 1; byte ranges — по контракту batch-read. Null range использует явные batch.read limits. Ответ содержит total_lines/bytes и фактический диапазон. Сначала проверяется принадлежность receipt текущей задаче. Хэш проверяется потоково; выбранный range не требует read_text всего full-файла. Разрез внутри UTF-8 последовательности для byte read отклоняется, не повреждается молча.

## Не реализовано в этом срезе
Установка реальных Codex hooks; автоматический список скрытых ChatGPT tools; проверяемое соединение с JetBrains; универсальная очередь фоновых заданий после crash; перехват всего native tool output; внешний backup/Gmail; автоматическое восстановление движущегося transcript источника. Никакое из них не обозначается PASS по локальному fixture.

[Local handoff](../workflows/local-handoff.md) · [API](../architecture/library-api.md) · [Источники](runtime-sources.md).
