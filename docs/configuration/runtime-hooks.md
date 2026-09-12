# DDD-07C — Настройка hook-транспорта и проверки доступности

Начало: **2026-09-07T01:17:28+05:00**. Linux, один явно выбранный проект.

## Что поставлено
Пакетная установка выбранной конфигурации Codex command hooks; session-scoped launcher к существующему WorkTools; непосредственное наблюдение UserPromptSubmit; проверки реальных команд и MCP stdio. Настройки hooks создаёт инструмент. Он не изменяет trust/permissions и не устанавливает Codex/IDE.

Поддерживаются SessionStart, UserPromptSubmit, Stop и SessionEnd основного агента. SubagentStart/SubagentStop и скрытая телеметрия ChatGPT не поддерживаются этим адаптером. Для каждого отдельного исполнителя задаётся собственный agent_id. Нативный session_id не является единственным ключом: используются проект, источник, внешняя сессия и configured actor.

## Интерфейсы
- `poise runtime-setup --settings <new-file> --max-input-bytes <explicit-limit>`: stdin `{settings: <full settings>, installation: {request_id, expected_revision, definition}}`. Создаёт settings, все native hooks и выполняет probes одним запросом. При повторе те же настройки принимаются; другое содержимое существующего settings не перезаписывается. Начальный лимит задан аргументом, потому что settings ещё нет.
- `poise runtime-config --settings <file>`: stdin `{operation: install, input: {request_id, expected_revision, definition}}` устанавливает весь definition и проверяет все probes одним вызовом. `operation: probe` принимает `definition_path` и `workspace` для явной повторной диагностики. Ошибка проверки доступности не отменяет факт создания настроек.
- `poise hook --settings <file> --definition <immutable-definition>`: внешний протокол Codex, один native event на stdin; не агентский lifecycle API.
- Сгенерированный `work.sh`: принимает обычный пакет WorkTools на stdin. Повторять task ID/session ID не требуется.
- Старые `work`, `runtime` и `goal-config` остаются самостоятельными явными интерфейсами. Hook-события и JSONL нельзя одновременно использовать как источник одних и тех же сообщений.

Bootstrap-настройки устанавливают root, project_config, interpreter, source_root, native hooks file, storage paths, modes и лимиты. Это не настройки из AGENTS.md и не угадывание по cwd. `config/hook-templates` — явно выбираемые исходные шаблоны примера, не скрытый источник отсутствующих полей.

## Установка
Сначала проверяются весь definition и настройки; обязательные поля не дополняются defaults. Существующие чужие группы hooks сохраняются. Заменяются только ранее установленные целые группы данного definition. Изменённая вручную owned-группа вызывает отказ, а не перезапись.

`expected_revision` — digest фактического native файла либо explicit null для нового файла. Повтор request_id с тем же намерением возвращает receipt; другое намерение под тем же ID отвергается. Незавершённая запись использует сохранённый кандидат и проверяет состояние before/after.

Сначала публикуется неизменяемый definition в config root, потом одним atomic replace обновляется hooks.json. Operational SQLite сохраняет pending/completed receipt. Одна SQL-транзакция НЕ делает файл частью SQLite; повтор сверяет фактический digest.

Новый/изменённый hook требует пользовательского review/trust в Codex. Инструмент возвращает `requires_user_review` и `live_codex: not_observed`; не включает `--dangerously-bypass-hook-trust`, не правит config.toml и не выдаёт генерацию файла за активацию в приложении.

## Native события и счётчик
SessionStart создаёт/восстанавливает отдельную binding и launcher. UserPromptSubmit фиксирует source/session/turn_id без хранения prompt; все причины, включая неизвестные, считаются. Повтор этого turn_id не увеличивает число. Два разных turn_id с одинаковым текстом — два события. До выбора задачи факт остаётся известным проекту; текущий пользовательский ход связывается при выборе/работе через launcher. Старые неопределённые taskless ходы не приписываются новой задаче задним числом.

Stop сообщает текущую позицию. Он НЕ возвращает `decision: block`, НЕ создаёт synthetic user turn, НЕ принимает этап и НЕ запускает следующую работу. SessionEnd только записывает факт окончания; handoff остаётся явной операцией, поскольку внешний event не является надёжным удерживающим барьером.

Hook-события синхронны там, где требуется готовая binding, согласованный счётчик или доклад. SessionEnd синхронен по внешнему протоколу. Никакой новой фоновой бизнес-мутации нет; асинхронные представления результатов DDD-07 продолжают работать через прежний механизм.

Счётчик отражает наблюдённые hook events, не биллинг OpenAI. Coverage остаётся partial: транспорт не доказывает, что платформа предоставила весь разговор. Прямой пакет messages через hooked-work запрещён, чтобы не создать второй источник того же хода.

## Проверки capabilities
Все probes задаются одним массивом, сначала валидируется весь массив. Ошибка одного внешнего инструмента не прячет результаты остальных. Каждый probe имеет exact argv/cwd/environment, срок выполнения, ограничения вывода, признак required, tool_ref и исполнимую рекомендацию восстановления. Окружение не наследуется неявно; секреты не следует записывать в параметры/вывод.

Command probe подтверждает ровно наблюдаемое: код выхода, признаки stdout/stderr, при необходимости точные JSON predicates. Версия не подставляется из expected config. Непонятный ответ, отсутствующий executable и timeout означают unavailable, не успешную capability и не автоматически AI poise Incident.

MCP stdio: initialize → exact negotiated protocol version → initialized → bounded tools/list → optional explicit read-only tools/call. Pagination и notifications ограничены общим временем, числом сообщений/страниц и байтами. Неподдерживаемая версия не запускает другой parser или transport. Запрашиваемые имена должны реально присутствовать в tools/list.

`mcp_tools_listed` не равен выполненной операции. `mcp_smoke_passed` подтверждает только конкретный read-only smoke. Например, наличие rename в каталоге не доказывает корректность самого rename. Для project_bound обязателен JSON predicate с точным текущим workspace; чужой открытый проект не считается подходящим. Используются explicit endpoints/команды, не сканирование портов.

В этом срезе поддержан STDIO, включая явно настроенный прокси существующего сервера. Прямые HTTP Stream/SSE и OAuth не реализованы. Для JetBrains используются команды из Copy Stdio Config и явные имена exposed tools; подключение живой IDE требует пользовательской среды.

## Рабочие границы
Проверка включается в configured gate_operations, без дополнительного model tool call на каждую capability. При bootstrap сначала подготавливается реально выбранный worktree, затем проверяется привязка к нему; при недоступности возвращается blocked context, а не успешное разрешение писать код. При verify проверка выполняется до запуска task methods. Read-only show, cancel и handoff не блокируются отсутствием IDE.

Generated launcher связан с неизменяемым definition. Изменённые settings не подхватываются молча существующей binding; нужен явный новый запуск. Не переключать источник сообщений в конфиге уже работающей задачи без отдельного решения: project execution contract защищён предыдущими срезами.

### Разрешение source root для native launcher

`native binding` и источник событий `codex-hook-main` остаются единственными владельцами
идентичности сессии и пользовательских сообщений. Выбор реализации AI poise выполняется
отдельно для каждого пакета как `session-scoped` решение и не создаёт новую сессию.

Для taskless bootstrap и создания новой Task используется настроенный `installation source`.
Bootstrap существующей зарегистрированной Task сначала связывает её через публичные API
владельцев, не вызывая installation `WorkTools`, а затем исполняет исходный пакет из AI poise
source внутри её `Task worktree`. Продолжение уже назначенной Task использует тот же маршрут.
Параллельные bindings могут выбирать разные worktrees конкурентно: launcher, binding и общая
конфигурация проекта не переписываются.

Перед дочерним исполнением и повторно внутри него проверяются зарегистрированный worktree,
ветка, общий Git repository, отсутствие symlink-компонентов, package и entrypoints
`poise.__main__` и `HookService.work`, а также digest binding. Изменившийся, отсутствующий,
вышедший за разрешённую границу или незарегистрированный кандидат отклоняется **без fallback**
на installation source. Дочерний процесс получает только проверенный source через собственный
`PYTHONPATH`; внутренние факты маршрута удаляются из окружения до вызова `WorkTools`.

Уточнено: **2026-09-12**. Согласно контракту задачи 0026, изменение валидной конфигурации
проекта не блокирует ни рабочие переходы, ни чтение, ни отмену Task/Sprint. Сохранённый
`config_hash` — диагностический provenance, а не lifecycle gate. Операция использует текущую
конфигурацию и сохранённые Task-owned process/contract/method snapshots; изменение конфига
не переписывает эти снимки и не перезапускает задачу. Проверки native binding и границ
выбранного source выполняются независимо от этого правила.

### Terminal cleanup через native launcher

Standalone `operation: cancel`, Sprint actions `cancel_tasks` и `cancel` выполняют terminal-
переход через владельца Task и сохраняют cleanup obligation. Они не удаляют worktree/ветку и
не превращают terminal status в решение о commit. После этого тот же session-scoped launcher
маршрутизирует публичный `operation: cleanup` с точными `task_id`, `request_id`, authorization,
`expected_commit` и disposition `preserved` либо `discard_authorized`.

Launcher возвращает `disposition_required`, `cleanup_blocked`, `cleanup_pending` или
`cleanup_complete`, а также точные `remaining_resources`, blocker и историю подтверждённых
шагов. Dirty worktree остаётся заблокированным даже после фиксации disposition: его можно
довести до clean fast-forward checkpoint той же task-ветки и продолжить новым request ID либо
вернуть к исходному commit вне cleanup и повторить прежний пакет. Основной checkout, чужие
ресурсы, durable artifacts и operator backups не очищаются.

Сохранённые ownership identity, commit/digest и версия проверяются перед каждым эффектом.
Поэтому конкурентное изменение ветки, файла или cleanup-state даёт actionable blocker/отказ,
а не удаление по одному имени. Повтор неизменного пакета после снятия блокировки продолжает
persisted шаги; скрытого фонового cleanup при событии hook нет. Полный payload и правила
commit disposition приведены в
[пакетном workflow](../workflows/batch-work.md#уборка-ресурсов-terminal-task).

## Данные
Task SQLite остаётся user_version=11. Новый отдельный operational hook registry имеет user_version=1: installations, operations, bindings, hook_events. Он не содержит Task/Sprint lifecycle и не экспортируется как рабочая история задачи. Сообщения сохраняет существующий InteractionStore. Определения находятся в config root, bindings/launcher/receipts — в настроенном долговременном state root, не в удаляемом turn runtime.

Source JSON/полные протокольные ответы не отправляются в model context целиком. CLI возвращает ограниченный ответ с адресом подробностей при необходимости. Нативный hook возвращает только компактную binding/state информацию. Конкретные имена результатов probes задаются probe_files.

## Проверка на реальном хосте
1. Выбрать установленный interpreter и доступный путь AI poise; определить project и отдельный message_source типа runtime_event.
2. Установить пакет hooks инструментом и выполнить проверки. Не редактировать сгенерированные definitions/hooks вручную.
3. В Codex открыть `/hooks`, вычитать и разрешить конкретные новые handlers. Не обходить trust.
4. Начать новую сессию из AI poise project; проверить появление session-scoped launcher и один UserPromptSubmit на видимый ход.
5. Для IDE явно включить MCP, взять Stdio Config, указать требуемые tools и read-only smoke для нужного worktree; проверить available только после настоящего ответа.

Без доступного Codex и endpoint IDE эти пункты остаются NOT_RUN. Локальный native JSON replay и MCP fixture этого не заменяют.

## Первоначальная настройка без ручного JSON-файла
Полный явный `settings` и выбранный `definition` передаются как значения одного stdin-пакета `runtime-setup`. Агент не создаёт промежуточный request-файл. Шаблоны в `config/hook-templates` — исходные образцы для составления пакета; каждое значение, включая interpreter/source/project paths, выбирается явно. Содержимое базового project manifest уже должно быть настроено как в прочих runtime interfaces.

Невалидный settings/definition отклоняется до записи новых файлов. Если после создания settings произошёл внешний сбой установки, повтор того же пакета использует этот settings и продолжает сохранённую установку; успех всех файлов как одной SQL-транзакции не обещается. Изменение рабочего definition выполняется через `runtime-config`, не через ручную правку `.codex/hooks.json`.

Taskless read-only bootstrap не блокируется отсутствующей IDE: возвращает сводку и честные результаты probes. Для подготовки активной задачи, verify и создания артефактов применяются явно заданные gates. Чтение, handoff и отмена не заперты за этими gates.

## Прямая проверка на WSL
1. В своём WSL убедиться, что выбранный CPython 3.13 и Git запускаются. Подготовить project manifest с явным `batch.message_source` текущего hook-источника (`mode=runtime_event`). Не включать одновременно JSONL importer для тех же сообщений.
2. В одном пакете `runtime-setup` указать свои фактические пути и definition. Для JetBrains взять **реальную** команду из Copy Stdio Config IDE, сохранить точные argv/environment. В `required_tools` указывать только инструментальные имена реально используемого IDE; для проверки привязки задавать read-only call и JSON predicate с `${workspace}`. Не вставлять инструменты из тестового mcp_fixture.
3. Запустить Codex из AI poise root и через его `/hooks` проверить и разрешить собственные команды. Инструмент не меняет trust/permissions и не устанавливает разрешение вместо пользователя.
4. В новом основном разговоре проверить получение launcher, запустить из него пакет bootstrap и один этап. Принятие результата и следующую работу разрешать отдельной инструкцией. Считать фактические UserPromptSubmit, а не количество WorkTools calls.
5. После этого сохранить receipt реального probe и результат короткого маршрута. До такого прогона поддержка провайдера имеет статус `not_observed`, даже когда локальный протокольный стенд прошёл.

Установка выбранных hooks не перехватывает все native инструменты Codex и не создаёт hooks в ChatGPT. Read-only smoke доказывает исполнение только конкретной команды/операции. Он не доказывает correctness rename/format без их отдельных испытаний. Live JetBrains endpoint, OAuth/HTTP/SSE и Gmail находятся вне текущего среза.
