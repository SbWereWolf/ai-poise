> Обновлено **2026-09-06T19:39:04+05:00**. Целевой контракт интерфейса дополнен HR-098…HR-106: пакетные декларативные инструменты, генерация артефактов и учёт пользовательских сообщений. Эти нормы уточняют прежние упоминания файлового ввода результата: stage-result — содержательный пакет, а не обязательная ручная правка файла агентом. Артефакты, уже созданные внешним инструментом, по-прежнему регистрируются только путями.
>
> Эта редакция фиксирует целевой контракт, а не статус реализации: [декларативные инструменты](../architecture/declarative-tools.md) и [текущий статус](../architecture/implementation-status.md). Детальные матрицы полного продукта остаются целевыми требованиями, а не отчётом об исполнении.

# Технические требования AI poise

Обновлено: 2026-09-06T14:30:44+05:00.

Основание: согласованное ТЗ 2026-09-06T10:07:45+05:00 и последующее решение пользователя об упрощении артефактов и переходе к реализации happy path.

**Это целевой контракт полного продукта, а не перечень уже реализованных функций.** Реализованный срез и его ограничения указаны в [implementation-status.md](../architecture/implementation-status.md). Артефакты передаются только путями; прежняя обязанность писать их смысловые декларации отменена. Неавтоматизированная содержательная классификация не блокирует первый рабочий срез.

## 01. Назначение и нормативная граница

<a id="HR-001"></a>

### HR-001. Предмет и границы продукта

AI poise — отдельное CLI-приложение и кодовая база для одного пользователя. Приоритет: ChatGPT cloud с Linux execution, вторая среда: Codex в WSL/Linux. Work, нативные Windows/macOS, запуск/назначение моделей и оркестратор агентов вне объёма. Пользователь выбирает исполнителя. AI poise планирует работу задач через зависимости, а не запускает агентов. Git, IDE MCP, shell и тестовые frameworks сохраняются; AI poise объединяет механические действия и обрабатывает результаты, а не заменяет их.

<a id="HR-002"></a>

### HR-002. Главный результат и административная стоимость

Цель — качественный конечный код/документация при минимуме токенов и времени на администрирование. Обязательная последовательность двух и более механических действий без нового инженерного решения является composite action. Любой инструмент AI poise принимает коллекцию изменений/объектов одним декларативным пакетом; одиночное действие — пакет из одного элемента. Обычный этап использует bootstrap и verify с прямым содержательным payload; физическое stage-result представление создаёт инструмент, ручное редактирование файла не требуется. Дополнительные show/явные business actions нужны для содержательного выбора, не для bookkeeping каждого элемента. Отчёт показывает измеренный расход, не контрфактическую экономию; сообщения пользователя — отдельная метрика, не оценка tokens. CPU, RAM, диск и невостребованные parser views не KPI.

<a id="HR-003"></a>

### HR-003. Нормативность и ответственность

ОБЯЗАН/ЗАПРЕЩЕНО — приёмочные нормы; РАЗРЕШЕНО — точно обозначенная ветвь, не отсутствующий default. Все численные параметры конфигурации поставляются явно; значения в тестовых fixtures не являются runtime defaults. Пользователь задаёт цель/проект и санкционирует отмены; агент классифицирует намерение, планирует, пишет результат, осматривает и интерпретирует evidence; kernel проверяет структуру, запускает точные команды, хранит состояния; runtime adapter подтверждает доступные capabilities и факты исполнения. Документ исправляет требования, но не является разрешением писать продукт. Неутверждённой остаётся редакция целиком, а не скрытые альтернативы внутри норм.

<a id="HR-004"></a>

### HR-004. Объём конкурентности

Поддерживаются разные агенты на разных задачах одной codebase, включая один logical файл, в разных worktrees, и разные ad-hoc sessions в одном cwd. Пользователь исключает одновременную работу двух агентов над одной task и cancel/force-close во время verify. Эти гонки не создают отдельную функциональность scheduler. Binding/claims, короткий DB lock и безопасный recovery остаются обязательными. Совпадение путей не создаёт semantic dependency и не вызывает автоматический merge.

<a id="HR-005"></a>

### HR-005. Комплект и трассировка документации

Все договорённости имеют нормативные IDs, документацию, независимые process matrices и приёмочные связи. package-model.json перечисляет канонические файлы и содержит canonical_formats: для каждого файла указан формат, реальная метка schema (если есть), верхние поля, адреса коллекций, нормативное описание и проверяющий контракт. Поле schema — точный идентификатор версии формата данных этой редакции, НЕ JSON Schema и не доказательство валидации. Форматы и ограничения определены текстом ТЗ, field contracts, lifecycle/metrics contracts и этим реестром; validation/check_package.py исполняет перечисленные статические проверки, не заменяя продуктовую приёмку. Markdown — воспроизводимое представление канонических данных. Расхождение представлений — ошибка поставки. sources/support не нормативны. Дата, время и offset обязательны. Прежние ID сохранены; новые IDs не означают реализацию. Проверка документации отдельно от NOT_RUN испытаний AI poise.

## 02. Среда, загрузка и возможности

<a id="HR-010"></a>

### HR-010. Linux runtime и воспроизводимый дистрибутив

Язык — CPython 3.13; другие minor не поддерживаются и автоматически не выбираются. Используется venv именно этого interpreter. Linux/POSIX, Git worktree, sqlite3 и flock на локальной файловой системе обязательны; Docker/Node/Redis/HTTP/MCP server не зависимости ядра. Выпуск ОБЯЗАН содержать release manifest с точными build/patch, версиями и hashes зависимостей, schemas, parser/adapter implementations, проверенных Git/SQLite builds, offline wheelhouse и tokenizer data. Команда установки работает --no-index --require-hashes по поставленному lock; переход к сети запрещён без выбранного online installation profile. Набор версий конкретного ещё не созданного выпуска не считается уже протестированным. До его приёмки release manifest и offline smoke обязательны.

<a id="HR-011"></a>

### HR-011. Первичная загрузка проекта

Поддержанные входы: полный repository с каталогом .git; самостоятельный bare/.git object store с явно выбранным commit; Git bundle с проверенными prerequisites. Linked-worktree .git pointer без доступного common-dir, missing objects и незавершённый shallow graph для требуемого base отклоняются до начала задачи. Архив распаковывается в staging после проверки путей, типов и symlinks, затем проверяются refs/object closure и только потом публикуется checkout. Для submodules и LFS manifest явно задаёт disabled либо точную локальную object/materialization процедуру; missing required content блокирует без auto-fetch. Внешние ignored configs доставляются по allowlist отдельно с соблюдением secret policy; они не становятся Git impact автоматически.

<a id="HR-012"></a>

### HR-012. Матрица адаптеров и честная доступность

У каждого runtime profile явно заданы CLI transport, session/cycle binding, tool inventory transport, hook surface, executor, IDE binding, remote Git transport, durable transport, token/time telemetry. Статус capability: observed-ready, observed-unavailable или unknown; required неизвестная/недоступная capability блокирует соответствующий этап до записи кода. ChatGPT: Linux CLI через доступный execution tool, generated scoped launcher при отсутствии hooks; model-visible integration вызывается только агентом через реально предоставленный connector, а не из Python по предположению. Codex WSL: explicit CLI/hooks adapter и scoped launcher с проверенным payload. Ни один профиль не обещает native ChatGPT hooks, сеть или Gmail attachments без smoke. Core actions и controlled command output работают через CLI в обеих средах; недоступные native hooks не эмулируются скрыто. Недоступность required push/IDE не превращается в успешный этап или автоматическую замену инструментом.

<a id="HR-013"></a>

### HR-013. Внешнее действие через агента

Для model-only capability composite action создаёт pending external action: operation ID, неизменный package/commit digest, exact target, ожидаемый receipt и probe, replay policy. Один ответ содержит весь запрос действия. Агент выполняет его имеющимся tool и возвращает receipt в том же submission/continuation; kernel проверяет receipt/probe и продолжает pending operation. Слова агента «отправлено/запушено» не являются receipt. Если tool не принимает нужный attachment/path/remote, действие blocked с исполнимой инструкцией пользователю. Gmail — только backup/handoff transport, не DB; его доступность и ограничения проверяются фактически. Сохраняющий копию транспорт не заменяет обязательный push без scoped user waiver.

<a id="HR-014"></a>

### HR-014. Идентичность живого executor

Bootstrap и каждый execution после reconnect получают handshake работающего runner/parser worker/IDE adapter: implementation digest, schema version, effective config digest, instance generation, start time, bound project/worktree. Hash файла на диске не заменяет handshake. Несовпадение с выбранным release/execution snapshot блокирует использование как новой версии; выдаётся точная инструкция restart и повторного probe. При разработке AI poise рабочая копия изменения не подменяет установленный исполняющий release. Нельзя автоматически перезапускать неизвестный чужой процесс по PID.

<a id="HR-015"></a>

### HR-015. IDE и внешние ресурсы

Project config задаёт требуемые semantic capabilities (поиск символа, rename, diagnostics, formatting и явно доступные остальные). Для каждого IDE вызова verified projectPath/root identity совпадает с worktree текущей работы; сервер, открытый на main/соседнем дереве, не используется. Capability inventory не гарантирует availability duplicate extraction/rename для любого языка: обязательны operation probe и контракт адаптера. БД тестов, Redis namespaces, порты, Docker project и другие внешние ресурсы перечислены с mode=isolated или serialized и exact namespace/identity. Isolated A не может чистить B; shared resources захватываются отдельным resource lock с timeout, не глобальным DB lock. Нет orchestration агентов; сериализуется только конфликтующий ресурс.

<a id="HR-016"></a>

### HR-016. Недостающая среда и исполнимая помощь

Если required interpreter, tool, библиотека, external input, credential или endpoint недоступен, bootstrap/readiness возвращает blocked, конкретные missing facts и инструкцию установки/запуска/передачи архива для пользователя с exact commands, target и критериями проверки. Не требуется заново подтвердить наблюдаемую версию на каждом preflight. Только wizard candidate требует принятия как значения конфигурации. Local smoke не доказывает работу WSL пользователя или Git/Gmail/IDE; приёмка целевых adapter profiles выполняется отдельно и её результат явно записывается.

## 03. Конфигурация и правила

<a id="HR-020"></a>

### HR-020. Конфигурация без неявных значений

Все конфиги AI poise находятся в его repository и создаются/редактируются инструментом через GoalConfig API одним пакетом желаемых изменений. Target codebases содержат собственные AGENTS, не machine configs AI poise. Обязательное значение, unknown key, неоднозначный matcher или неразрешимая ссылка дают configuration_error до публикации. Запрещены hidden defaults/fallback, implicit inheritance, угадывание команд по AGENTS/cwd и broad suite «на всякий случай». Явно выбранный шаблон содержит принятые значения и фиксированную revision; missing в шаблоне и запросе остаётся ошибкой. Unknown-command parser — обязательный явный профиль. Отсутствующий auto-check mapping — invalid project config для выбранного класса; явный no_auto_check с причиной разрешён и не отключает task-registered tests. Конфиги разных goal types независимы. Имена классов и policies задаются configs, не ветвлением kernel.

<a id="HR-021"></a>

### HR-021. Начальный протокол без рекурсивных fallback

Граница B0 — явный вызов установленного launcher с абсолютным путём bootstrap descriptor; путь не ищется от cwd и не берётся из origin/.poise по умолчанию. Descriptor и launcher являются частью поставки, содержат явные корень, config schema, encoding, emergency channel и mapping numeric exit outcomes. Синтаксис входа/структурные имена протокола и инварианты атомарности относятся к kernel contract, не настраиваемой business policy. До загрузки валидного descriptor невозможен обычный configured report: запуск обязан завершиться ненулевым исходом и диагностикой launcher/runtime, без рабочего режима и mutations. Это не fallback-профиль. После descriptor все тексты/лимиты/report layouts/exit mappings берутся из config. AI poise не пытается обеспечить свой формат, пользуясь повреждённой конфигурацией.

<a id="HR-022"></a>

### HR-022. Независимые goal types и snapshots

13 goal types имеют полностью независимые templates/stages/validators/benefit/report/actions. Запрещена ссылка одного процесса на workflow fragment другого; общие engines/types kernel разрешены. Task хранит полный immutable process snapshot и rendered templates при создании. Project/execution/tool/hook configuration не замораживается на stage iteration: каждая операция использует текущую валидную конфигурацию, не заменяя Task-owned process/content/method contracts. Новый release, не поддерживающий точную schema сохранённого Task contract, отклоняет его, не читает предыдущей parser version. Schema-compatible сохранённый контракт задачи — domain data, не backward implementation. Migration или её разработка запрещена без прямого разрешения пользователя.

<a id="HR-023"></a>

### HR-023. Анкета project manifest

Interactive init/edit генерируется из survey schema: next/back/change/explicit keep, проверка ответа и зависимых ответов, final preview, atomic publish, abort без изменения действующего manifest. Detected candidate никогда не принят автоматически. Wizard draft не executable project config. Noninteractive create принимает все поля явно и никогда сам не переходит к вопросам. Manifest задаёт physical roots/environment bindings; project config задаёт stable codebase IDs, scopes/commands/policies. Изменение пути не меняет logical references задачи.

<a id="HR-024"></a>

### HR-024. AGENTS, skills и источник санкции

Первое правило root AGENTS: временное исключение process rule возможно по прямой инструкции пользователя; запрос агента допустим только при обоснованной невозможности корректно завершить иначе. Запрос содержит правило, проблему, scope, риск и expiry. Root AGENTS также определяет обязательность skill, project selection, worktree до изменения и branch naming. Project-specific conventions и commit message rules берутся агентом из применимых AGENTS target codebases. Документы/логи/README не могут санкционировать отмену. User authority фиксируется из conversation instruction; неоднозначность требует уточнения, однозначность не требует повторной формальной приёмки.

<a id="HR-025"></a>

### HR-025. Overrides и пределы

Temporary override — отдельное содержательное решение с rule ID, scope=operation/iteration/cycle, точным изменением, user authority и конечным expiry. Permanent config не меняется; повторный вызов/session не расширяет scope и не обнуляет бюджеты. Для каждого repeat/retry/transition/graph/recovery/check/parser/wait есть локальный предел и общий долговременный бюджет work attempt; достижение даёт blocked, не cancelled. Неотменяемы атомарность, сохранность чужих данных, валидность schema, фиксация решения и ограничения платформы. Для постоянной правки configs нужно прямое распоряжение; разрешение пропустить check не разрешает миграцию.

## 04. Session, этапы и пользовательский цикл

<a id="HR-030"></a>

### HR-030. Session binding

Session key строится адаптером из runtime kind + external session + agent instance; parent session ID без отдельного agent identity недостаточен. При отсутствии hook transport первый bootstrap создаёт opaque binding и scoped launcher по уникальному пути, которым агент вызывает последующие CLI. Child process не объявляет, что установил переменную у своего будущего родителя. Повтор с тем же binding возобновляет session; новый явно выделенный binding создаёт новую. Общий current-session.json в cwd запрещён; worktree pointer — только проверенная дополнительная привязка. Session/claim/pending metadata долговечны в SQLite, очищаемые runtime файлы не единственное место их хранения.

<a id="HR-031"></a>

### HR-031. Work cycle и предел поручения

Одна пользовательская инструкция на formal work задаёт роль и предел поручения одной задачи. Поручение исполнителю разрешает непрерывный участок этапов `executor`; для каждого этапа сохраняется отдельный work_cycle с instruction/binding, project, scope, task/stage/iteration при наличии, временем и причинами затрат. Повтор bootstrap/verify внутри iteration не создаёт ещё этап, а переход выполняется публичным bootstrap и не подделывает новое пользовательское сообщение. Обычные этапы `reviewer` и исправления разрешены началом Task, но выполняются отдельным агентом после публичной передачи. Новое содержательное решение и отдельно регулируемая приёмка/публикация/интеграция требуют соответствующего разрешения пользователя. Финальный отчёт предъявляет immutable delivered-result перед границей роли или полномочия. Нельзя незаметно выполнить будущий reviewer-stage; read-only сводка во время task не переключает formal work. Полная карта ролей и правила передачи определены в [правилах разработки](development-rules.md#роли-этапов-и-непрерывность-поручения).

<a id="HR-032"></a>

### HR-032. Bootstrap одним пакетом

Bootstrap получает выбранный project/config, необязательный task/sprint scope и explicit instruction binding/intent. Без scope существующая formal work возвращается, иначе ad-hoc. Task bootstrap/resume создаёт binding/claim, проверяет creation/prerequisites/worktree/capabilities и выдаёт текущие layers и прямой контракт пакетного результата, без обязательной ручной правки result file. Sprint bootstrap сразу вычисляет весь eligible set и blockers; выбор задачи агентом возвращает её capsule в том же start-вызове. Отдельные next/status/refresh не требуются. После binding task ID не требуется для verify/show/handoff/discard. Факт пользовательского сообщения учитывается в этом же входе при наличии configured источника, без отдельного call. Stale mutation не применяется и возвращает свежий контекст в том же ответе.

<a id="HR-033"></a>

### HR-033. Состояния и принятие

Task: available, active, blocked, completed, cancelled; failed отсутствует. Содержательная iteration: active→verified→accepted; superseded/abandoned не удаляют результаты. Operation и execution_attempt — отдельные объекты: phase может ожидать agent reasoning, external receipt либо recovery и не является verified. Успешный итоговый verify даёт verified и завершает текущий stage-вызов; accept-only принимает результат и не разрешает новый содержательный этап или публикацию. Accept+continue открывает ровно один следующий содержательный этап. Для следующего `executor`-этапа authority берётся из действующего поручения исполнителю; следующий вызов повторяет этот публичный путь без нового сообщения «дальше». Обычный `reviewer`-этап разрешён началом Task и требует публичной передачи отдельному проверяющему. Отдельно регулируемая publication требует собственного разрешения. Publication — user-authorized mechanical gate, а не дополнительная iteration: accepted inspection+explicit publish выполняют gate в том же composite call, затем обычное closure без второго acceptance механики. При accept-only перед gate задача остаётся незавершённой с причиной publication_not_authorized. Последний accepted содержательный результат без pending publication закрывается автоматически. Неполученный ответ пользователя не считается acceptance или новым publication authority.

<a id="HR-034"></a>

### HR-034. Rework, reopen и отмена

Rework сохраняет feedback, предъявленный result и решения; новая iteration создаётся только для указанного этапа. Повтор неуспешной проверки и содержательное continuation после наблюдений остаются в той же iteration. Инвалидация вычисляется только по объявленным типизированным validity relations HR-095, никогда по всем refs подряд. Reopen completed task атомарно отзывает current benefit credit, переоценивает sprint/successor prerequisites, но не удаляет полученное. Старое evidence остаётся доказательством старого subject, а не нового. Cancel/reinstate/force-close — по authority пользователя; обычные limit/test/env failures означают blocked, не самостоятельную отмену. При cancel нет обычных DoD/tests; сохраняются решение, безопасная судьба WIP, dependencies/claims и обязательная доставка.

<a id="HR-035"></a>

### HR-035. Задача без спринта и ad-hoc

Standalone formal task имеет sprint=None явно, собственный root/process и не может иметь dependency edge в sprint. Допускает планирование первого sprint без рекурсии уже существующих спринтов. Ad-hoc read содержит компактные goal/requirements/DoD/verification contract в session DB: verify обязателен, проверяет корректность запрошенной сводки/ссылок, отсутствие собственных target changes, сохранение значимого результата, incidents и cleanup; worktree, claim и application tests не нужны. Ad-hoc write требует worktree, exact registered/project tests, Git commit/push и reusable result в session-owned durable deliverable root вне runtime. Формальная task не создаётся автоматически из длины запроса; превращение требует пользовательского решения.

<a id="HR-036"></a>

### HR-036. Handoff и discard

Начало другой formal task допускается только после завершения, successful handoff либо safe discard текущей. Handoff сохраняет process position, WIP/verified vector, sections/findings/decisions/evidence и обязательный backup, затем освобождает claim. WIP commit требует сообщения агента и явно unverified, не выдаёт acceptance. Discard может удалить только несохранённый draft/WIP текущей попытки относительно последнего durable checkpoint; уже принятые layers/decisions и чужой/user/unattributed WIP не стираются. Чужая работа требует отдельной санкции; неопределимый scope блокирует reset. Discard не добавляет фиктивных бизнес-результатов, но operational event, отказ от draft и метрики затрат сохраняются. Отсутствие процесса не доказывает orphan claim: recovery требует bind/receipt и явного resume либо решения пользователя, без таймаута-захвата.

<a id="HR-037"></a>

### HR-037. Владение Task и worktree

Обновлено: 2026-09-13. Источник: Task 0076, требования 0–6;
принятые решения 0072/0073 о продолжении ролей и прямой передаче.
Сессия может владеть одной Task и одним worktree независимо: ни одним, только Task,
только worktree или обоими. Явный `worktree_required` process snapshot определяет
зависимый worktree; отсутствие или неверный тип поля в новом process — ошибка.
Полный набор захватывается после preflight и повторной проверки одной транзакцией,
с идемпотентным узнаванием собственной сессии и автоматической заменой прежнего
владения того же вида. Отказ не оставляет частичного нового набора.
Чужой живой владелец или неопределённая liveness блокируют захват; подтверждённое
завершение нативной сессии позволяет восстановить её устаревшие claims.
Освобождение Task освобождает только зависимый worktree, сохраняя независимый.
Операция не изменяет WIP, cwd, launch roots, HEAD и index, не коммитит и не удаляет данные.

Правило уточняет HR-030/032/036 и отделяет замену claims от сохранения и передачи
незавершённой работы. По принятым решениям 0072/0073 начало Task разрешает обычное
продолжение обеих ролей: прежнее требование новой команды для каждого reviewer-этапа
заменено [канонической политикой ролей](development-rules.md#роли-этапов-и-непрерывность-поручения).
Отдельный проверяющий получает работу лишь после публичного освобождения и bootstrap.
Существующие Task guards, handoff, terminal cleanup (0048) и result integration (0057)
переиспользуются, не являются новой функциональностью 0076 и не получают дополнительной
авторизации из операции владения. Исполнимый контракт и ограничения:
[правила владения](development-rules.md#владение-task-и-worktree).

Сохранённый process snapshot без обязательного `worktree_required` не читается через
совместимость или скрытое значение. Для явно названного закрытого набора Task применяется
только пользовательски авторизованная **явная миграция** `task-process-migrate` по
[операторскому контракту](../task-process-snapshot-migration.md), **без fallback** и без
прямого SQL. Она выводит значение из неизменяемого `goal_type` и настроенного process,
проверяет весь набор до записи и изменяет snapshots атомарно.

## 05. Хранение, artifacts и sprint

<a id="HR-040"></a>

### HR-040. Task/sprint в SQLite

Canonical task/sprint structured state, весь текст секций/слоёв, findings/resolutions/decisions/evidence metadata/traceability/session/claims/metrics находятся в SQLite. У записей stable globally unique IDs, owner, schema и monotonic revision; timestamps с timezone. Content layers immutable после submission, current pointers обновляются transaction. Содержательная работа не остаётся только в log. Внешние ключи включаются и проверяются на КАЖДОМ connection до transaction; indices поддерживают owner/status/sprint/stage queries без чтения всех section bodies. Section applicability/empty/template/populated — состояние содержания, не доказательство его истинности.

<a id="HR-041"></a>

### HR-041. Внешний монопольный lock

Все AI poise business writers используют один внешний OS flock по canonical DB identity; alias пути к той же БД не создаёт второй lock. Lock на постоянном файле рядом с DB, существование файла не признак занятости. Процесс освобождает OS lock при exit/crash. Short SQLite transactions под lock, foreign key/check constraints и SQLite внутреннюю атомарность не отключать. Checks, IDE, backup copy больших файлов и сеть не удерживают глобальный writer lock. Продолжение повторно проверяет revisions. DB на общей сетевой/непроверенной lock filesystem не принимается. Timeout/wait/retry явно config; контенция разных task не означает баг.

<a id="HR-042"></a>

### HR-042. Artifacts и публикация

Валидная текущая project configuration является live operational input, а не lifecycle identity задачи. Изменение общего конфига не инвалидирует, не перезапускает и не пересоздаёт существующие Task. Их process/content/method contracts остаются неизменными, а сохранённый `config_hash` используется только как диагностический provenance и не блокирует операции. Одновременную работу обеспечивают отдельные worktree, Task ownership, optimistic revisions и короткие resource-scoped locks; общий конфиг на iteration не замораживается.

Все task artifacts внутри task root, sprint-shared внутри sprint root, ad-hoc durable внутри session deliverable root, runtime-only внутри cycle root; roots — явно заданные пути относительно AI poise codebase, gitignored. Artifact: owner, logical path, media type, bytes, digest, provenance, retention, publication state. Файл пишется staging в той же filesystem, проверяется digest, fsync и atomic rename публикуют immutable файл до DB reference. DB transaction не ссылается на partial. Crash до DB даёт unreferenced staged/published artifact для bounded recovery, не ложное evidence; после DB файл существует. Export pin/reference предотвращает удаление файла во время snapshot/copy. Потеря/искажение existing artifact обнаруживается по digest и даёт corruption Incident без подмены.

<a id="HR-043"></a>

### HR-043. Sections, resolutions и содержательная история

Task хранит все semantic layers, proposals, evidence и решения. ResolutionProposal содержит finding/ref, explanation, proposed result и запланированный/полученный метод доказательства, но не обязательный accepted/rejected. Отдельный ResolutionReviewDecision создаётся осмотром после доступности именно result/evidence этого proposal; отказ не стирает proposal. Артефактный индекс с bytes/digest/provenance, execution receipts и timestamps создаёт AI poise; агент передаёт artifact_paths — только пути файлов, уже размещённых внутри разрешённых корней текущих runtime, task или sprint. Внутренний ID назначает AI poise; purpose, role и requirement_refs от агента не требуются. Manual evidence подаётся по своему контракту. Подсчёт артефактов выполняется по явно заданным файловым признакам типа и границам количества; отсутствие надёжного проектного парсера не блокирует запуск первого рабочего среза. Evidence-view и traceability — generated projections, не редактируемые агентом индексы. Historical refs не становятся несуществующими при замене current результата. Сравнение template и поля содержательного результата не заменяют семантический осмотр.

<a id="HR-044"></a>

### HR-044. Sprint graph и доступность результата

Зависимости — DAG только между tasks одного sprint. Eligibility требует accepted completion predecessor и успешного MaterializationContract на точном стартовом snapshot successor. Contract имеет явный kind: exact_history (ancestry/identity только для требования истории), result_availability (source-result→materialized-result receipt и exact declared availability predicates) либо artifact_identity (digest/owner ref). Ancestry не доказывает наличие поведения. Cherry-pick/rebase разрешены с recorded mapping и проверкой результата; после revert/drift прежний receipt сам по себе не обеспечивает readiness. Все predicates имеют точные команды или digest checks; AI poise не угадывает смысл кода. Unavailable prerequisite даёт blocker и receipt, не автоматическую интеграцию или foreign-task mutation. Межспринтовые refs допускаются как immutable inputs, не dependency edges.

<a id="HR-045"></a>

### HR-045. Cancel dependencies и sprint closure

Cancel predecessor удаляет его из работы, но не удовлетворяет edge; successors blocked до решения keep+waive/remove prerequisite либо cascade по вычисленным transitive dependents. Cancelling child не расширяет scope до root/siblings. Sprint states planned/active/blocked/completed/cancelled — производные tasks плюс closure receipt. Normal completion запускает configured structural/artifact/decision gates, не full application suite; сбой closure даёт closing operation needs_resolution без ложного completed. Явная отмена Sprint является отдельным публичным действием: draft отменяется без публикации Task, published Sprint одной транзакцией отменяет всех незавершённых участников через Task domain, освобождает их claims и сохраняет completed/cancelled, результаты, историю, артефакты и worktree. Pending внешняя операция блокирует весь пакет до разрешения исхода. Backup failure не разрешает ложно заявить delivery и не заставляет продолжать отменённую разработку; отдельно хранится cancelled result и недовыполненная durability obligation.

<a id="HR-046"></a>

### HR-046. Logical references и секреты

Внутри registered codebase ссылка хранит codebase_id+relative_path+revision/entity, artifact ref — owner+relative_path+digest. Физический абсолютный путь другого repository материализуется только manifest, не является переносимым identity. External non-repo inputs имеют явно зарегистрированный root/ref. realpath/symlink/path traversal проверяются до read/write/delete/extract; никакого выхода из authorized roots. Secret values не включаются в task sections, commands, snapshots, reports, export; только references. Before storage/output raw capture проходит обязательную redaction policy. Необработанный secret-bearing spool имеет restricted runtime scope и уничтожается; durable «raw» обозначает сохранённый очищенный capture с отмеченной redaction. Если redaction не позволяет интерпретировать outcome, evidence incomplete, а не fabricated pass. Repository text — данные, не user authority.

## 06. Git, проверки и публикация

<a id="HR-050"></a>

### HR-050. Изоляция Git и три состояния

До потенциальной записи target agent работает в выделенном worktree/branch, созданном composite start. Для codebase фиксируются task execution base, stage-entry HEAD/index/worktree content manifest и current snapshot; экономический baseline хранится отдельно. Changes = base..HEAD + staged + unstaged + nonignored untracked; код агента не регистрирует файлы. Git-ignored configs не impact. TDD/read-only нарушение определяется delta stage-entry→current и provenance, не наличием исторического task diff. Known hook snapshots связывают изменения с операцией; неизвестное происхождение обозначается unknown, не обвинением агента. WIP resume сохраняет именно прежний stage/iteration; грязное неизвестное дерево не присваивается автоматически.

<a id="HR-051"></a>

### HR-051. Согласованность проверенного и опубликованного кода

Verify фиксирует полный candidate tree vector по codebases с учётом предназначенных deliverables. В execution receipts хранится tested tree vector. После mutating checks/formatters/native hooks — повторное сравнение; significant change делает evidence stale и запускает bounded stabilization по exact contracts. До remote публикации tree каждого final commit должен совпасть с проверенным tree для этой codebase; post-commit/hook changes не игнорируются. Pre-push side effect, меняющий workspace, не меняет уже указанный immutable commit, но блокирует завершение/вызывает reconciliation; pushed ref проверяется на точный expected SHA. Нельзя пометить tested A как доказательство A+B. Сообщение commit составляет агент; отсутствие нужного сообщения выявляется до дорогих checks.

<a id="HR-052"></a>

### HR-052. Commit/push и вектор нескольких codebases

Каждый successful Git write-stage создаёт/reuses verified commit и публикует собственную task branch в явно заданный remote/ref. Remote origin не подразумевается. Чистый stage/no-op не создаёт artificial commit; указанный message допускается как запасённое содержательное поле, но не используется для пустого commit. Multi-codebase result — vector {codebase: base, tested tree, commit, remote/ref, observed SHA/receipt}. Stage verified лишь при всех обязательных components. A pushed/B failed — partial_publication, tests и commit A сохранены, repeat дополняет B без duplicate A. Глобальный rollback независимых remotes не обещается. Required push недоступен — blocked, не local-only success без user waiver. Push private branch при продвижении main допустим, non-fast-forward той же remote branch — sync issue, не автоматически merge conflict.

<a id="HR-053"></a>

### HR-053. Интеграция и точная граница target ref

Integration объединяет объявленные exact sources с target baseline в private worktree. Git operation создаёт conflict observations до агентских resolution proposals; при конфликте возвращается awaiting_agent в той же stage iteration, и continuation не повторяет начатый merge/cherry-pick. Interim private commit/push не меняют target ref. Target publication — механический gate после принятых verification/inspection, при отдельной однозначной publish authority. Accept-and-stop запрещает publication. Gate повторно проверяет tested tree, current evidence, expected target old SHA и выполняет exact FF update или доказанный no-op; создаёт receipt и closure без новой содержательной iteration/второго acceptance. Drift не исправляется force push и не выполняет незаявленный rebase. Source→integrated mapping хранится для materialization, но не заменяет проверку availability successor.

<a id="HR-054"></a>

### HR-054. Точный executable contract

Executable method разделён на PlannedInvocation, BoundExecution и ExecutionReceipt. При планировании обязателен exact codebase/environment target, cwd, mode, полный argv/shell binary+command, explicit env/secret references и inheritance policy, stdin, declared input paths/IDs, resource requirements, expected/collection predicates, stage applicability, limits/retry/reuse policy и references нужных runner/parser profiles. Эти значения могут быть явными resolvable config refs; команда не выводится из AGENTS. Для будущего теста точный путь/команда уже определены, а observed input digest, worktree identity, live generation, фактические resource bindings и timestamps ещё НЕ требуются. AI poise сам получает их при binding/readiness и сохраняет immutable BoundExecution. Missing будущий input допустим на planning phase по explicit deadline, но блокирует его execution. Exit, collection, raw hashes и duration появляются только в Receipt. Поддельные наблюдения от модели не принимаются как факты.

<a id="HR-055"></a>

### HR-055. Выбор проверок без знаний о проекте

Verify сам объединяет task-registered commands applicable к current stage, project auto-checks по code/tests/fixtures и stage machine validators. Code/docs/fixtures classification для метрик отдельно; configs/dependencies/migrations/assets не добавляются в auto-impact. Никакой синтез команды, framework discovery и выбор full suite вместо missing mapping. Explicit no_auto_check не отменяет registered tests. Full verification допускается только по user authorization с exact full procedure; нормальный task/sprint/integration closure её не запускает. Auto-check applicability к TDD stage задаётся явно. Проверки могут иметь пустой список, заданный в stage contract; structural evidence checks тогда остаются.

<a id="HR-056"></a>

### HR-056. Execution identity, reuse и expected RED

Execution key включает BoundExecution vector: codebase/worktree, tested trees, cwd/invocation/stdin, declared input/environment facts без secrets, bound resources, live runner generation/config и capture policy. Parser/predicate identities дополнительно определяют derived evidence validity. Дедупликация только при равном полном ключе и совместимых expectations. Expected RED требует запуска нужных collected test IDs и нужного assertion, не любого nonzero. Reuse PASS/FAIL receipts разрешён только при доказанной freshness и explicit reuse policy; unknown state даёт probe/readiness или bounded новый запуск по explicit policy. Повтор с тем же semantic payload и новым tree/environment создаёт новую execution_attempt, не новый semantic layer. Неизвестный незавершённый эффект сначала probe; identical receipt replay не выполняет side effect второй раз. Same known failed subject возвращает прежний результат, кроме заранее разрешённой bounded transient-retry policy с записанной причиной; от агента не нужен nonce или изменённый commit message.

<a id="HR-057"></a>

### HR-057. Candidate submission до precheck

SemanticSubmission, OperationIntent и ExecutionAttempt имеют разную идентичность. Submission dedup: iteration+semantic_digest сохраняет ровно одну содержательную revision; изменённый payload создаёт следующую revision без overwrite. Это НЕ ключ replay проверок. Verify сначала валидирует submission/authority, формирует candidate projection и сохраняет валидную попытку, затем получает актуальные наблюдаемые binding/freshness facts и решает, что можно reuse. После H1→FAIL, правки до H2 и прежнего payload новый attempt проверяет H2 в той же active iteration; прежние слои не копируются. После known PASS H2 неизменный повтор возвращает receipt/report. После потери ACK operation продолжает только недостающие шаги, проверив receipts. Unknown running attempt не перезапускается вслепую. Изменение уже предъявленного verified/accepted предмета требует разрешённого rework, а не молчаливого переписывания его отчёта.

<a id="HR-058"></a>

### HR-058. Полный verify pipeline

Verify — фазовый протокол одной iteration. PREPARE: submission/candidate/authority, required agent-before fields и exact method readiness. OBSERVE: bind current subject, run/reuse declared preparations/observations и сохранить факты. Если process требует решение по новым фактам, вернуть весь evidence/conflict context и awaiting_agent; не verified, не user acceptance, не новая task/iteration. CONTINUE: принять только declared agent-after-observation поля с refs на receipt/subject. CHECK: missing proposals/fields обнаружить до final expensive checks, затем выполнить недостающие exact predicates, сохранить evidence. SEAL: typed invalidation/stabilization, tree=commit equality, private publication receipts, durable detail set, Incidents, report, runtime cleanup. Только SEAL даёт verified/STOP. Ветви, не требующие post-observation reasoning, проходят без дополнительного LLM call. Число continuations/attempts bounded общим budget iteration, не сбрасывается новым вызовом.

<a id="HR-059"></a>

### HR-059. Что kernel может доказать

Structural validators проверяют required typed fields, cardinality, applicability, references/digests, command predicates и наличие accepted/rejected inspection decisions. Не утверждают, что текст истинный, полезный, constraint содержательно соблюдён или scope осмотрен только из факта ссылок. Logical evidence хранит claim, fact refs, assumptions, проверяемое краткое inference, conclusion и reviewer verdict, не private chain of thought. Добавление символа к template меняет digest, но не заполняет типизированные пункты. При semantic rejection review record блокирует completion независимо от структурной полноты. Agent-evaluation/manual cases проверяют reasoning отдельно от deterministic kernel. Некомандный метод не создаёт ExecutionReceipt: агент предоставляет EvidenceProposal, AI poise регистрирует evidence и отдельно структурный validation receipt. Решение reviewer принадлежит последующему осмотру. Proposal не включает придуманную коллекцию тестов, exit code или собственное accepted/rejected решение. Проверка существования фактов/источников не доказывает истинность аргумента.

## 07. Вывод, hooks и завершение цикла

<a id="HR-060"></a>

### HR-060. Парсеры и неизменный capture

Известная команда: один parser, modes sync primary/async materialize; неизвестная: явно configured generic profile. Оба читают один immutable sanitized capture. Все named views создаются независимо от использования. Capture outcome, predicate verdict, parser health независимы. Partial files не ready. Каждый view имеет representation ID, content digest, owner, lifetime policy, готовность; file name config-driven. Ошибка parser как баг — Incident независимо от восстановления; underlying test outcome не изменяется.

<a id="HR-061"></a>

### HR-061. Чтение и ограничение envelope

Чтение TEXT: UTF-8, представление содержит объявленные bytes/newlines; lines 1-based inclusive, bytes [start,end). Два диапазона вместе invalid. Split codepoint расширяется до boundaries с actual range и учётом envelope; long line выдаётся chunks, не reader refusal. Binary только явный binary profile. Первичный ответ сообщает totals, actual ranges, truncation и все ready/declared views. Готовая ссылка имеет immutable target, digest и гарантированный lifetime HR-096. Не выдавать native runtime path как ready после его удаления. Outcome/next-work/Incident presence входят в explicit budget; слишком маленький минимум — configuration error, не скрытый fallback.

<a id="HR-062"></a>

### HR-062. Полный eligible set и дополнительные секции

Bootstrap вычисляет полный eligible set за один запрос. При помещении inline выдаёт весь набор. При превышении configured response bound одновременно сохраняет полный immutable view с total rows/bytes и возвращает decision-useful первую порцию и blockers; show читает диапазон уже вычисленного результата, не вызывает новый next. Agent не обязан угадывать лимит. Для section каталога даются content_state (empty/template/populated/generated), size class и layer; token estimates не выводятся. Stage matrix определяет inline и on-demand sections явно. Optional historical layers не загружаются в каждый context. Exact sizes используются в подробном чтении, а не для навязывания модели экономии токенов.

<a id="HR-063"></a>

### HR-063. Hooks и граница финализации

Blocking hooks нужны для разрешений/обязательных evidence/invariants, остальные async с durable jobs и immutable inputs. Async не меняет workflow-critical state. Show ожидает pending materialization в пределах бюджета без polling LLM. Перед terminal report finalization barrier завершает обязательные views/jobs, публикует durable ResultSet со ВСЕМИ адресами возвращаемых views, pins и digest, продвигает evidence, собирает Incidents. Лишь после этого удаляются runtime-only copies/drafts и формируется ответ со stable refs/native durable paths. До SEAL при awaiting_agent временные ready refs действуют до окончания данной iteration и явно помечены. Независимые jobs после barrier имеют другой reporting cycle. Неполная materialization блокирует verified, но не стирает test outcome.

<a id="HR-064"></a>

### HR-064. Минимальный отчёт и runtime cleanup

Доклад содержит цель/результат, evidence/verdict, Git/delivery receipts, findings/waivers/risks и ВСЕ AI poise Incidents текущего cycle, включая recovered; обычные tests/environment outcomes не выдаются за баги AI poise. Report immutable в DB и адресует durable ResultSet, не удаляемый runtime. Для task/sprint result views принадлежат соответствующему owner; ad-hoc — durable session deliverable root вне runtime с явной retention. Cleanup удаляет временные копии после pins/publication, не target ready refs. Binding, usage/report pointers сохраняются. Native detail reads после verify допустимы без дополнительной formal work и не запускают commands. Runtime user message/report boundary фиксирует адаптер; неизвестные границы метрик partial.

<a id="HR-065"></a>

### HR-065. Диагностирование собственного отказа

Operational journal — append-only файлы на выделенном configured AI poise journal root, а SQLite хранит их индекс. Запись имеет event ID, operation/parent ID, session/cycle, process sequence, storage transaction sequence когда есть, monotonic/wall timestamps, input/output digest, outcome/attempt/retry. Business facts остаются в DB. При отказе DB журнал должен сохранять причину независимо; при отказе journal используется заранее configured emergency descriptor/channel из boot contract. Если оба storage недоступны, stderr/launcher failure с operation locator — последний наблюдаемый исход, а не обещание несуществующего log. Destructive external actions не начинают без durable intent record. Incident registry содержит invariant, scope, impact, recovery, references; новый баг не маскируется обычным test failure. После восстановления аварийные записи индексируются один раз.

## 08. Восстановление и перенос

<a id="HR-070"></a>

### HR-070. Replay и неизвестный внешний исход

Сначала сохраняется immutable OperationIntent; каждый внешний effect имеет effect ID, exact input snapshot, receipt/probe и bounded retry policy. Повтор submission не равен повтору external effect. Recovery при неизвестном исходе получает receipt/probe до нового запуска; non-idempotent unknown требует user decision. При известном окончании повтор возвращает результат; при changed subject после terminal failed attempt создаётся новый ExecutionAttempt в той же iteration, не новая копия semantic результата. Config/retry budgets не обнуляются session/call. Сохранённое negative evidence не теряется и не выдаётся за актуальный verdict иного дерева.

<a id="HR-071"></a>

### HR-071. Crash на границах и atomic receipts

Bootstrap/verify/handoff/publication состоят из durable steps с idempotent operation+submission keys. Crash до DB commit не публикует половину business mutation; после commit response loss возвращает тот же receipt. Worktree/commit/raw artifact уже существует, а DB marker нет — recovery сверяет identity/digest/expected parent/tree и связывает существующий объект, не создаёт дубликат. Recovery не верит произвольному совпадению времени/message. Partial parser files не published. Process/source generation mismatch не исправляется «на лету» новой реализацией внутри старой pending operation; требуется явное безопасное восстановление в поддержанном contract.

<a id="HR-072"></a>

### HR-072. Два точных режима transfer

Transfer сохраняет привычный комплект SQLite DB+owner files+integrity manifest. Whole-store restore разрешён только в пустой/эксклюзивно передаваемый store с exact expected generation; не заменяет живую расходящуюся БД. Owner-scoped handoff/export выбранных task переносит SQLite subset той же schema с immutable origin IDs, exact owner base revision, full task closure и readonly pinned sprint/prerequisite references. Destination под внешним lock применяет только owners, не изменившиеся от declared base; независимые изменения B сохраняются при импорте A. Никакого semantic merge одного owner, schema migration или last-writer-wins. Новые immutable events дедуплицируются по origin ID; derived sprint counts/eligibility пересчитываются. Изменение общей sprint topology требует отдельного owner revision match, не перезаписывается task package. Активная работа передаваемого owner прекращена; другие tasks могут работать.

<a id="HR-073"></a>

### HR-073. Согласованный snapshot DB и artifacts

Перед export под коротким внешним lock фиксируются consistent SQLite snapshot поддержанным backup методом, owner revisions и pinned список immutable artifact digests. Длинное копирование выполняется после release lock; retention не удаляет pinned файлы. Manifest содержит exact schema/release/config identities, owner bases/results, file sizes/digests, closure refs и package digest. Package ready только после проверки полного набора. Import сначала валидирует schema/closure/digests и разрешённые реальные пути в staging, затем публикует файлы и одной DB transaction применяет records. Failure до transaction оставляет лишь очищаемые unreferenced artifacts, не полуприменённую задачу. Повтор receipt не применяет пакет второй раз.

<a id="HR-074"></a>

### HR-074. Git objects и внешний backup

DB+files сохраняют business state, но для checkout нужны сами Git objects. Handoff предоставляет exact commit vector в проверенно доступном remote либо Git bundle с требуемой closure; одного SHA недостаточно. Ignored secret inputs передаются отдельным явным каналом и не попадают в общий backup. Gmail transport получает только разрешённый package или подтверждённые ссылки; upload/send/read receipt и limits определены adapter profile. Error backup не удаляет canonical state и не фальсифицирует delivery. Receipt, digest и source revisions сохраняются в task/sprint data. Никакой распределённой синхронизации SQLite через Gmail.

## 09. Метрики результата и стоимости

<a id="HR-080"></a>

### HR-080. Usage events и полнота затрат

Usage event имеет globally unique source event ID, source/runtime/schema, session/agent/cycle, project/task/stage/iteration, interval, reported counters и semantics=delta/cumulative. Source total authoritative; input/output суммируются только по объявленной схеме без повторного cached/reasoning (это subsets при соответствующем source contract). Cumulative snapshots превращаются в delta один раз в пределах source generation; reset/gap помечается. Повтор/import одного ID не меняет расходы, conflicting payload даёт Incident. Unavailable/partial не ноль; estimates из символов не actual tokens. На каждом aggregate есть coverage (known/unknown cycles) и denominator policy; при неполных расходах efficiency=N/A. Известный расход можно показать отдельно как наблюдённую неполную сумму, но не выдавать отношение к нему за эффективность всей работы.

<a id="HR-081"></a>

### HR-081. Время, доклад и причинная атрибуция

Work cycle интервалы начинаются фактическим user instruction/start событием адаптера и заканчиваются report-delivered/stop, включая reasoning, tools, tests и обязательное ожидание. User wait после доклада исключён. При отсутствии полного источника фиксируются наблюдаемые границы и completeness; точное время до bootstrap/после verify не выдумывается. Поздний usage/stop receipt дополняет тот же cycle идемпотентно. Active agent time — сумма непересекающихся интервалов по каждому agent; wall time — объединение интервалов. Handoff суммирует стоимость разных agents в одной task. Канонический словарь primary_cause задан только metrics-contract.json:primary_causes. Причина поздней переделки — delivered_rework; формулы ссылаются на идентификатор словаря, не распознают синонимы. У segment ровно одна primary cause. Несегментируемая смесь имеет явное значение mixed; точные доли не выдумываются. Неизвестная причина отклоняется до проводки, а не становится нулевой стоимостью.

<a id="HR-082"></a>

### HR-082. Находки после предъявления

Quality finding считается один раз по distinct defect ID против immutable delivered-result, если обнаружено в последующем work_cycle. Недостаток, найденный и устранённый до предъявления того же результата, — internal QA, не quality finding независимо от названия действия. Отдельный inspection stage на следующем подходе имеет право найти quality finding предыдущей implementation; слово self не исключает его. Post-completion — подмножество, не добавляется ещё раз в общий count. Неправильные/rejected/duplicate findings исключаются из active delivered quality с сохранением решений; waived остаётся обнаруженным дефектом с отдельным outcome, не repaired. User requirement change не finding. Remediation group связывает все findings и затраты цикла; нет искусственного per-finding деления.

<a id="HR-083"></a>

### HR-083. Accounting baseline и конечный diff

Accounting baseline неизменен для lifetime task; execution/task/stage baseline различаются. Польза считается own final accepted baseline→result, не сумма правок/коммитов и не foreign changes rebase. Additions и removals учитываются раздельно и суммой. Полезные классы определяет goal_type (development=code+docs, tests исключены). Перемещение с тем же content digest в том же полезном классе даёт ноль content delta; cross-class transfer не автоматически создаёт новую пользу. Explicit origin identity map для move/rebase; ambiguous mapping означает incomplete measure, не догадку. Git diff flags не являются свободным выбором реализатора: применяется measurement profile HR-097. Integration не дублирует content benefit источников.

<a id="HR-084"></a>

### HR-084. Полезный payload и tokenizer

Полезный payload измеряет объём результата, не реальную бизнес-ценность. Измерительный профиль явно фиксирует input projection, own-unit selection, UTF-8/line-ending/Unicode policy, line-diff tie-break, единицу токенизации, structured serialization, tokenizer identity+data digest и exclusions generated/raw/template. Для TEXT единица сравнения — полная логическая строка с её исходным terminator; bytes удалённых/добавленных строк, а не character edit distance. Tokens считаются по каждой полной удалённой/добавленной строке отдельно, включая terminator; не по склеенному diff с +/- headers. Выбор другой версии профиля не смешивается в aggregate. Предметные counters сохраняются для review/verification/environment, где payload не отражает ценность. Golden corpus с численными lines/bytes/tokens обязателен; тестовый tokenizer явно обозначен и не выдаётся за usage модели.

<a id="HR-085"></a>

### HR-085. Кредиты, reopen, cancel и календарь

Benefit ledger хранит credit/reversal с unique task result revision и as-of. Accepted completion K1=100 даёт +100; reopen отзывает текущий provisional credit -100, WIP сохраняет last accepted history; completion K2=120 даёт +120, lifetime=120, не220. Cancel отзывает любой действующий credit и benefit=0, все расходы сохраняются как cancelled loss; только пользователь санкционирует cancel. Cost events начисляются по фактической дате, correction/reversal по дате решения, previous as-of отчёты воспроизводимы. Day/week используют одну явно указанную IANA timezone/ISO Monday boundary; интервалы времени делятся на границах суток, token event — по timestamp источника если более точного распределения нет. WIP cost ещё не credited benefit и не окончательный убыток. Отдельный failed outcome не вводится.

<a id="HR-086"></a>

### HR-086. Словарь формул и агрегация

Обязательные метрики определены metrics-contract.json: exact source events/projection, единицы, сериализация/diff, cohorts, denominator, overlap policy и zero/unknown semantics. Total cost разделяется по одной primary cause на cycle; cancelled — исход задачи, не слагаемое поверх total; remediation groups не делятся произвольно. First-pass stage — первый предъявленный результат ключа task+stage+requirement-epoch: denominator только с первым user disposition в периоде, numerator accepted без defect rework до этого решения; requirement change цензурирует предыдущую epoch, не объявляет дефект. Amplification = сумма последовательных own useful accepted snapshots / конечный own useful delta, одинаковые классы и profile, numerator включает initial delta; denominator 0 даёт undefined, не infinity/0. Cost ledger начисляется в event time; benefit signed reversals/replacements не удваивают результат. Daily/weekly/all-work и by-goal-type считают суммы числителей/знаменателей, не средние ratios. Все golden примеры воспроизводимы из canonical bytes, без вычислений из форматированного stdout. rework_cost имеет машинный selector primary_cause=delivered_rework из канонического словаря. Повтор одного source+generation+event_id с теми же данными не добавляет проводку; повтор ID с изменёнными данными — конфликт, не last-writer-wins. Идентичное событие даёт одну стоимость в каждом срезе task/sprint/day/week/goal_type/all; эти срезы не складываются друг с другом. Расход времени хранится целыми микросекундами. Golden fixtures проверяют 12000 tokens/600000000 microseconds, replay, ошибочный cause и неполную телеметрию.

## 10. Процессы и приёмочная методика

<a id="HR-090"></a>

### HR-090. Полные независимые process contracts

Каждый из 13 типов имеет независимый полный process definition. Нормативные узлы разделены kind=content_stage и kind=mechanical_gate; сохранены 98 GS IDs, из них три publication gates не создают содержательную iteration. Для каждого stage указаны creation inputs, PREPARE agent fields, automatic OBSERVE/CHECK outputs, CONTINUE agent fields, final fields, read-only scope, transitions с guards и rework targets. Producer/phase каждого field однозначны, generated view не обязательный агентский ввод. Переходы ссылаются только на собственные объявленные nodes/closure; произвольный next не допустим. Publication выполняется после accepted inspection и explicit authority одним механическим действием. Разрешённый method kind должен иметь достижимого производителя evidence во всех заявленных ветвях. Для planning конечный draft валидируется не фактом наличия planned_task/task_contracts, а вложенными creation fields выбранного независимого goal_type; общие views и шаблоны не добавляют missing business values.

<a id="HR-091"></a>

### HR-091. Секция как проверяемый контракт

Section text/typed fields в SQLite. Для каждого field/schema path объявлены producer (user/config/agent/kernel/adapter/reviewer), earliest availability и required phase, empty/not_applicable policy. Содержательная applicability/plan — агентские данные, actual digests/versions/receipt — наблюдения AI poise. Разрешённая будущая команда точна до создания файла, её observed digest только на execution. Manual evidence proposal не включает автоматические observations и не заменяет reviewer decision. Required-before валидирует только доступные на PREPARE поля; post-observation проверяется после фактов; обязательное final — перед verified. Все ранее принятые layers сохраняются, current view не создаёт второй источник.

<a id="HR-092"></a>

### HR-092. Приёмочные уровни и запрет ложного доказательства

Приёмка разделяет kernel K, adapter A, end-to-end E, agent evaluation V и live smoke M. Наличие refs не доказывает истинность вывода. Все product cases NOT_RUN до запуска продукта. Существующие isolated STG остаются contract tests; к ним обязательны stateful create→все content stages→authorized gates→completed для 13 типов и ветви finding/fix/reinspection, waiver, return earlier. Нельзя вставлять будущие stage inputs прямым SQL или preloaded completed layers. Fixtures, шаги, expected states и effect counts конкретны; сам прогон модели спецификации не PASS продукта. Документный checker валидирует graph targets, producers/phases, достижимость входов и точное равенство производных представлений, отдельно проверяется отрицательными мутациями. Сквозные planning fixtures обязаны содержать явные поэтапные значения каждого creation field дочерней задачи, а не контракт родителя или обещание будущего заполнения. Published child проходит собственный creation validator с типизированными полями и exact/deferred method obligations, затем bootstrap без подстановки недостающего business content. Неизвестный goal_type или отсутствующая обязательная applicability отклоняются. При зависимости отсутствие materialized predecessor допустимо только как declared blocker, не как недостающий контракт. Ordered events не допускают следующий content bootstrap без user_accept текущего verified результата и явного разрешения продолжать.

<a id="HR-093"></a>

### HR-093. Регрессии и live build

F01–F14 исторического анализа связаны с конкретными REG cases, requirement IDs, session file/line locator и типом свидетельства (raw event либо объяснение агента). Включён F09: старый живой runner после обновления файла. Regression corpus в комплекте — компактные extracts/manifest/агрегаты, не весь 1.68-ГБ архив. Исторические документы неизменны и явно nonnormative. Review package проверяет разрешимость их относительных ссылок, hashes и полноту. Перед программной приёмкой replay fixtures строятся из этих локаторов; старый log не доказывает актуальный дефект.

## 11. Независимые профили целей

<a id="GP-DEV"></a>

### GP-DEV. Полный профиль development

Независимый process development: Production-код/поведение приложения; тесты и документация — средства качества и сопутствующий результат. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Все обязательные stages accepted, applicable tests удовлетворяют final predicates, требования/DoD связаны с current accepted evidence, task-quality findings resolved/waived, docs obligation выполнена, весь Git vector проверен/опубликован. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-DEV-01"></a>

### GS-DEV-01. development.baseline

development.baseline (content_stage): Установить исходное поведение или обоснованную неприменимость. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: baseline, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-02"></a>

### GS-DEV-02. development.solution_planning

development.solution_planning (content_stage): Спланировать изменение компонентов, интерфейсов и документации. PREPARE: solution_plan, decisions, assumptions, documentation_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-03"></a>

### GS-DEV-03. development.verification_planning

development.verification_planning (content_stage): Спроектировать доказательства до реализации. PREPARE: verification_plan, test_registry. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-04"></a>

### GS-DEV-04. development.test_implementation

development.test_implementation (content_stage): Написать тесты/fixtures до production change. PREPARE: test_registry. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-05"></a>

### GS-DEV-05. development.test_inspection

development.test_inspection (content_stage): Read-only осмотр oracle, покрытия, чувствительности и fixtures. PREPARE: inspection_coverage, findings, inspection_verdict. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-06"></a>

### GS-DEV-06. development.test_remediation

development.test_remediation (content_stage): Исправить зарегистрированные дефекты тестов. PREPARE: finding_resolutions, test_registry. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-07"></a>

### GS-DEV-07. development.test_remediation_inspection

development.test_remediation_inspection (content_stage): Проверить исправление тестов без правки target. PREPARE: inspection_coverage, inspection_verdict, findings, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-08"></a>

### GS-DEV-08. development.implementation

development.implementation (content_stage): Реализовать production change по принятым тестам. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-09"></a>

### GS-DEV-09. development.implementation_inspection

development.implementation_inspection (content_stage): Read-only осмотр корректности, сложности и соответствия. PREPARE: inspection_coverage, findings, inspection_verdict. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-10"></a>

### GS-DEV-10. development.implementation_remediation

development.implementation_remediation (content_stage): Исправить находки реализации. PREPARE: finding_resolutions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-11"></a>

### GS-DEV-11. development.implementation_remediation_inspection

development.implementation_remediation_inspection (content_stage): Осмотреть каждое исправление read-only. PREPARE: inspection_coverage, findings, inspection_verdict, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DEV-12"></a>

### GS-DEV-12. development.documentation

development.documentation (content_stage): Выполнить прямо предусмотренную документацию. PREPARE: result. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-TST"></a>

### GP-TST. Полный профиль test_development

Независимый process test_development: Тесты и тестовые fixtures как самостоятельный конечный продукт. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Coverage complete, final test predicates выполнены, sensitivity handled, fixtures valid, production delivered delta=0, required inspection accepted, task-quality findings closed, commits/push подтверждены. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-TST-01"></a>

### GS-TST-01. test_development.baseline

test_development.baseline (content_stage): Установить существующее покрытие/известное поведение. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: baseline, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TST-02"></a>

### GS-TST-02. test_development.coverage_planning

test_development.coverage_planning (content_stage): Связать поведение с будущими тестами. PREPARE: coverage_matrix. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TST-03"></a>

### GS-TST-03. test_development.test_planning

test_development.test_planning (content_stage): Спроектировать oracle, fixtures, sensitivity и команды. PREPARE: verification_plan, test_registry, fixture_plan, sensitivity_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TST-04"></a>

### GS-TST-04. test_development.test_implementation

test_development.test_implementation (content_stage): Написать только тестовый deliverable. PREPARE: test_registry. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TST-05"></a>

### GS-TST-05. test_development.sensitivity_verification

test_development.sensitivity_verification (content_stage): Подтвердить различение правильного и неправильного поведения. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TST-06"></a>

### GS-TST-06. test_development.test_inspection

test_development.test_inspection (content_stage): Осмотреть coverage/oracles/isolation/fixtures. PREPARE: inspection_coverage, findings, inspection_verdict. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TST-07"></a>

### GS-TST-07. test_development.remediation

test_development.remediation (content_stage): Исправить дефекты тестового результата. PREPARE: finding_resolutions, test_registry. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TST-08"></a>

### GS-TST-08. test_development.remediation_inspection

test_development.remediation_inspection (content_stage): Проверить исправления read-only. PREPARE: inspection_coverage, findings, inspection_verdict, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-VER"></a>

### GP-VER. Полный профиль verification

Независимый process verification: Доказать или опровергнуть соответствие зафиксированного результата требованиям; не исправлять продукт. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Программа исполнена или user waivers, критерии имеют current evidence/verdict, task-quality objections устранены/waived, target unchanged, temporary probes cleaned. Product findings могут оставаться open и product verdict negative. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-VER-01"></a>

### GS-VER-01. verification.planning

verification.planning (content_stage): Определить программу, критерии и точные способы доказательства. PREPARE: verification_criteria, verification_program, verification_methods, environment. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-VER-02"></a>

### GS-VER-02. verification.execution

verification.execution: PREPARE принимает manual_evidence для методов по имеющимся фактам и проверяет их наличие до executable OBSERVE. OBSERVE исполняет только явно объявленные точные команды. Если план требует reasoning по новым receipts, возвращает awaiting_agent с полным контекстом; CONTINUE принимает observed_manual_evidence и не повторяет команды. CHECK регистрирует manual/observed proposals и command receipts как разные evidence, проверяет покрытие methods/subject и неизменность target. SEAL требует все обязательные proposals, но не придумывает reviewer verdict. Logical-only имеет 0 external invocations; отсутствующее доказательство блокирует verified. Подробный независимый контракт — process-matrices.json:verification и lifecycle-contracts.json:noncommand_evidence.

<a id="GS-VER-03"></a>

### GS-VER-03. verification.analysis

verification.analysis (content_stage): Интерпретировать evidence и сформировать несоответствия. PREPARE: findings, verdict, result. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-VER-04"></a>

### GS-VER-04. verification.self_inspection

verification.self_inspection (content_stage): Осмотреть качество самой проверки. PREPARE: inspection_coverage, inspection_verdict, findings. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-REV"></a>

### GP-REV. Полный профиль review

Независимый process review: Найти доказанные дефекты/риски в зафиксированном результате, не исправляя его. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Scope/criteria coverage и evidence полны, review quality accepted, target unchanged. Open subject findings не блокируют; unresolved task-quality defects блокируют. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-REV-01"></a>

### GS-REV-01. review.planning

review.planning (content_stage): Определить scope, критерии и единицы осмотра. PREPARE: review_criteria, review_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-REV-02"></a>

### GS-REV-02. review.inspection

review.inspection (content_stage): Read-only изучить target и оформить доказанные findings. PREPARE: inspection_coverage, findings, manual_evidence, verdict, result. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-REV-03"></a>

### GS-REV-03. review.self_inspection

review.self_inspection (content_stage): Проверить качество собственного review. PREPARE: inspection_verdict, decisions, findings. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-DES"></a>

### GP-DES. Полный профиль design

Независимый process design: Спроектировать техническое решение/архитектуру/контракт без production реализации. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Контракт полного design, alternatives/decision зафиксированы, DoD/proofs current, все task-quality findings resolved/waived, allowed artifacts сохранены и Git receipt при необходимости. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-DES-01"></a>

### GS-DES-01. design.framing

design.framing (content_stage): Зафиксировать исходную архитектуру и критерии выбора. PREPARE: baseline, assumptions, decision_criteria, verification_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DES-02"></a>

### GS-DES-02. design.alternatives

design.alternatives (content_stage): Сравнить допустимые решения. PREPARE: alternatives. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DES-03"></a>

### GS-DES-03. design.decision

design.decision (content_stage): Выбрать решение и обосновать отказ от других. PREPARE: selected_approach, decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DES-04"></a>

### GS-DES-04. design.detailed_design

design.detailed_design (content_stage): Описать конкретные contracts и failure modes. PREPARE: design, interfaces_contracts, impact_analysis. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DES-05"></a>

### GS-DES-05. design.design_verification

design.design_verification (content_stage): Проверить design против требований. PREPARE: нет новых полей модели. CONTINUE: manual_evidence, verdict. Автоматические поля: traceability, artifacts, evidence. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DES-06"></a>

### GS-DES-06. design.design_inspection

design.design_inspection (content_stage): Осмотреть consistency, неопределённости и лишнюю сложность. PREPARE: inspection_coverage, findings, inspection_verdict. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DES-07"></a>

### GS-DES-07. design.remediation

design.remediation (content_stage): Исправить design findings. PREPARE: design, interfaces_contracts, finding_resolutions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DES-08"></a>

### GS-DES-08. design.remediation_inspection

design.remediation_inspection (content_stage): Проверить design corrections read-only. PREPARE: inspection_coverage, findings, inspection_verdict, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-ANA"></a>

### GP-ANA. Полный профиль analysis

Независимый process analysis: Доказательный аналитический вывод по заданным вопросам без изменения предмета исследования. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Все mandatory questions имеют обоснованный final conclusion, challenge/limitations выполнены, факты traced, task-quality objections resolved/waived, inputs не изменены, final report durable. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-ANA-01"></a>

### GS-ANA-01. analysis.framing

analysis.framing (content_stage): Сформулировать вопросы и метод до сбора огромного массива. PREPARE: source_plan, method. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ANA-02"></a>

### GS-ANA-02. analysis.evidence_collection

analysis.evidence_collection (content_stage): Обработать данные инструментами, извлечь факты. PREPARE: нет новых полей модели. CONTINUE: sources, facts, assumptions. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ANA-03"></a>

### GS-ANA-03. analysis.reasoning

analysis.reasoning (content_stage): Сформировать проверяемые выводы. PREPARE: analysis, conclusions, recommendations. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ANA-04"></a>

### GS-ANA-04. analysis.challenge

analysis.challenge (content_stage): Проверить альтернативы и опровергающие данные. PREPARE: alternative_explanations, counterevidence, limitations. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ANA-05"></a>

### GS-ANA-05. analysis.self_inspection

analysis.self_inspection (content_stage): Осмотреть достоверность собственного анализа. PREPARE: inspection_coverage, findings, inspection_verdict, result. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-PRO"></a>

### GP-PRO. Полный профиль profiling

Независимый process profiling: Измерить performance/resources характеристики и подтвердить выводы; постоянная оптимизация не входит. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Measurements/baseline/confirmation complete, target/environment traced, temporary instrumentation removed, выводы осмотрены; неудовлетворительная производительность продукта не мешает завершению profiling. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-PRO-01"></a>

### GS-PRO-01. profiling.framing

profiling.framing (content_stage): Спланировать исходное измерение и критерии воспроизводимости. PREPARE: environment, workload, verification_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-PRO-02"></a>

### GS-PRO-02. profiling.baseline_measurement

profiling.baseline_measurement (content_stage): Получить исходные measurements. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: baseline, evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-PRO-03"></a>

### GS-PRO-03. profiling.experiment_planning

profiling.experiment_planning (content_stage): Спланировать различающие эксперименты после baseline. PREPARE: experiment_plan, instrumentation_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-PRO-04"></a>

### GS-PRO-04. profiling.measurement

profiling.measurement (content_stage): Выполнить эксперименты. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: measurements, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-PRO-05"></a>

### GS-PRO-05. profiling.analysis

profiling.analysis (content_stage): Отделить измерения от интерпретаций. PREPARE: analysis, findings, alternative_explanations, recommendations, limitations, confirmation_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-PRO-06"></a>

### GS-PRO-06. profiling.confirmation

profiling.confirmation (content_stage): Независимо/повторно подтвердить существенные выводы. PREPARE: нет новых полей модели. CONTINUE: confirmation. Автоматические поля: evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-PRO-07"></a>

### GS-PRO-07. profiling.self_inspection

profiling.self_inspection (content_stage): Осмотреть качество измерений и выводов. PREPARE: inspection_coverage, findings, inspection_verdict, result. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-DIA"></a>

### GP-DIA. Полный профиль environment_diagnostics

Независимый process environment_diagnostics: Установить причину сбоя среды/tooling и дать воспроизводимую рекомендацию, не постоянное исправление. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Причина доказана в границах DoD или разрешённый inconclusive verdict, required confirmation exists, рекомендация проверяема, temporary state restored, task-quality objections closed. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-DIA-01"></a>

### GS-DIA-01. environment_diagnostics.framing

environment_diagnostics.framing (content_stage): Определить симптом и необходимые факты среды. PREPARE: verification_plan. CONTINUE: facts. Автоматические поля: environment_snapshot, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DIA-02"></a>

### GS-DIA-02. environment_diagnostics.reproduction

environment_diagnostics.reproduction (content_stage): Проверить исходный симптом. PREPARE: нет новых полей модели. CONTINUE: reproduction. Автоматические поля: evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DIA-03"></a>

### GS-DIA-03. environment_diagnostics.hypothesis_planning

environment_diagnostics.hypothesis_planning (content_stage): Составить проверяемые альтернативные причины. PREPARE: hypotheses, diagnostic_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DIA-04"></a>

### GS-DIA-04. environment_diagnostics.experiments

environment_diagnostics.experiments (content_stage): Выполнить диагностические experiments. PREPARE: нет новых полей модели. CONTINUE: facts. Автоматические поля: experiments, evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DIA-05"></a>

### GS-DIA-05. environment_diagnostics.root_cause_analysis

environment_diagnostics.root_cause_analysis (content_stage): Обосновать причину и отвергнутые альтернативы. PREPARE: root_cause, recommendations, confirmation_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DIA-06"></a>

### GS-DIA-06. environment_diagnostics.confirmation

environment_diagnostics.confirmation (content_stage): Проверить причинность контролируемым изменением. PREPARE: нет новых полей модели. CONTINUE: confirmation. Автоматические поля: evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DIA-07"></a>

### GS-DIA-07. environment_diagnostics.self_inspection

environment_diagnostics.self_inspection (content_stage): Проверить диагноз и рекомендацию. PREPARE: inspection_coverage, findings, inspection_verdict, result. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-ENV"></a>

### GP-ENV. Полный профиль environment_remediation

Независимый process environment_remediation: Изменить среду/tooling и доказать достижение явно заданного desired state. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Desired criteria proved, permanent required changes остаются, temporary effects removed, task-quality findings resolved/waived, exact action/verification evidence complete; Git commit обязателен только для Git deliverables. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-ENV-01"></a>

### GS-ENV-01. environment_remediation.framing

environment_remediation.framing (content_stage): Определить разрешённый target и desired state. PREPARE: scope, constraints, verification_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ENV-02"></a>

### GS-ENV-02. environment_remediation.baseline

environment_remediation.baseline (content_stage): Снять исходное состояние до изменения. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: baseline, evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ENV-03"></a>

### GS-ENV-03. environment_remediation.change_planning

environment_remediation.change_planning (content_stage): Определить actions, verification и безопасное прерывание. PREPARE: change_plan, rollback_plan, verification_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ENV-04"></a>

### GS-ENV-04. environment_remediation.application

environment_remediation.application (content_stage): Применить подготовленные механические действия. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: execution_results, observed_state, evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ENV-05"></a>

### GS-ENV-05. environment_remediation.verification

environment_remediation.verification (content_stage): Подтвердить desired state и отсутствие побочных нарушений. PREPARE: нет новых полей модели. CONTINUE: verdict. Автоматические поля: evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ENV-06"></a>

### GS-ENV-06. environment_remediation.inspection

environment_remediation.inspection (content_stage): Read-only осмотреть достигнутое состояние. PREPARE: inspection_coverage, findings, inspection_verdict, result. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ENV-07"></a>

### GS-ENV-07. environment_remediation.correction

environment_remediation.correction (content_stage): Исправить замечания по среде с новым exact action plan. PREPARE: change_plan, finding_resolutions. CONTINUE: не требуется. Автоматические поля: observed_state, artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-ENV-08"></a>

### GS-ENV-08. environment_remediation.correction_inspection

environment_remediation.correction_inspection (content_stage): Проверить corrections read-only. PREPARE: inspection_coverage, findings, inspection_verdict, result, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-DOC"></a>

### GP-DOC. Полный профиль documentation

Независимый process documentation: Создать/обновить документацию как основной deliverable. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Content requirements covered, required examples checked, sources traced, docs durable/published по target, inspection accepted и task-quality findings closed. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-DOC-01"></a>

### GS-DOC-01. documentation.framing

documentation.framing (content_stage): Определить аудиторию и источники истины. PREPARE: source_inventory, content_requirements, verification_plan, publication_target. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DOC-02"></a>

### GS-DOC-02. documentation.outline

documentation.outline (content_stage): Спроектировать структуру по content requirements. PREPARE: outline. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DOC-03"></a>

### GS-DOC-03. documentation.drafting

documentation.drafting (content_stage): Написать documentation deliverable. PREPARE: content, examples_commands. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DOC-04"></a>

### GS-DOC-04. documentation.validation

documentation.validation (content_stage): Проверить примеры/ссылки и источники. PREPARE: нет новых полей модели. CONTINUE: verdict. Автоматические поля: evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DOC-05"></a>

### GS-DOC-05. documentation.inspection

documentation.inspection (content_stage): Read-only вычитать по audience и completeness. PREPARE: inspection_coverage, findings, inspection_verdict, result. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DOC-06"></a>

### GS-DOC-06. documentation.remediation

documentation.remediation (content_stage): Исправить документацию по findings. PREPARE: content, finding_resolutions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-DOC-07"></a>

### GS-DOC-07. documentation.remediation_inspection

documentation.remediation_inspection (content_stage): Повторно вычитать corrections. PREPARE: inspection_coverage, findings, inspection_verdict, result, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-TPL"></a>

### GP-TPL. Полный профиль task_planning

Независимый process task_planning: Опубликовать полноценный contract одной будущей/пересматриваемой задачи. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Будущая task опубликована атомарно, валидна по собственному independent pack, references/methods/dependencies корректны; planning result inspection accepted. При revise предыдущие layers сохранены и invalidations рассчитаны. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision. Publication проверяет вложенный creation_payload каждого child его независимым goal_type contract; недостающие поля не создаёт. Exact methods разрешимы, future observation digests не требуются на creation. Post-publication bootstrap использует только опубликованные значения и явные dependency receipts.

<a id="GS-TPL-01"></a>

### GS-TPL-01. task_planning.framing

task_planning.framing (content_stage): Определить конечную цель планируемой task. PREPARE: constraints, assumptions, deliverables. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-02"></a>

### GS-TPL-02. task_planning.classification

task_planning.classification (content_stage): Выбрать один target goal_type по основному результату. PREPARE: goal_classification. CONTINUE: не требуется. Автоматические поля: planned_task, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-03"></a>

### GS-TPL-03. task_planning.requirements

task_planning.requirements (content_stage): Уточнить требования в draft target task. PREPARE: planned_task. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-04"></a>

### GS-TPL-04. task_planning.acceptance_design

task_planning.acceptance_design (content_stage): Спроектировать DoD и deliverables будущей task. PREPARE: acceptance_design, planned_task. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-05"></a>

### GS-TPL-05. task_planning.work_planning

task_planning.work_planning (content_stage): Заполнить process-specific planning inputs. PREPARE: work_plan, planned_task. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-06"></a>

### GS-TPL-06. task_planning.verification_planning

task_planning.verification_planning (content_stage): Зафиксировать способы доказательства будущей задачи. PREPARE: verification_plan, planned_task. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-07"></a>

### GS-TPL-07. task_planning.task_inspection

task_planning.task_inspection (content_stage): Read-only проверить draft contract. PREPARE: inspection_coverage, findings, inspection_verdict. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-08"></a>

### GS-TPL-08. task_planning.remediation

task_planning.remediation (content_stage): Исправить draft по findings. PREPARE: planned_task, finding_resolutions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-09"></a>

### GS-TPL-09. task_planning.remediation_inspection

task_planning.remediation_inspection (content_stage): Осмотреть исправленный contract. PREPARE: inspection_coverage, findings, inspection_verdict, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-TPL-10"></a>

### GS-TPL-10. task_planning.publication

task_planning.publication (mechanical_gate): Механически опубликовать принятый результат по явному разрешению пользователя; не отдельный содержательный этап. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: result, evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-SPL"></a>

### GP-SPL. Полный профиль sprint_planning

Независимый process sprint_planning: Опубликовать валидный sprint с tasks и same-sprint DAG единым согласованным результатом. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Sprint и все tasks опубликованы единым валидным set, no cross-sprint edges, methods/coverage complete, inspection accepted; execution не начинается автоматически. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision. Publication проверяет вложенный creation_payload каждого child его независимым goal_type contract; недостающие поля не создаёт. Exact methods разрешимы, future observation digests не требуются на creation. Post-publication bootstrap использует только опубликованные значения и явные dependency receipts.

<a id="GS-SPL-01"></a>

### GS-SPL-01. sprint_planning.framing

sprint_planning.framing (content_stage): Задать цель/границы/DoD sprint и основания. PREPARE: constraints, assumptions. CONTINUE: не требуется. Автоматические поля: planned_sprint, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-02"></a>

### GS-SPL-02. sprint_planning.decomposition

sprint_planning.decomposition (content_stage): Выделить самостоятельные конечные результаты. PREPARE: decomposition. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-03"></a>

### GS-SPL-03. sprint_planning.task_classification

sprint_planning.task_classification (content_stage): Классифицировать каждый candidate. PREPARE: goal_classification. CONTINUE: не требуется. Автоматические поля: task_contracts, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-04"></a>

### GS-SPL-04. sprint_planning.task_contracts

sprint_planning.task_contracts (content_stage): Заполнить все draft tasks. PREPARE: task_contracts. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-05"></a>

### GS-SPL-05. sprint_planning.dependency_design

sprint_planning.dependency_design (content_stage): Спроектировать semantic prerequisites и materialization. PREPARE: dependency_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-06"></a>

### GS-SPL-06. sprint_planning.execution_planning

sprint_planning.execution_planning (content_stage): Определить priorities и shared context/resources. PREPARE: execution_plan, shared_context_plan, shared_artifacts_plan, risks. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-07"></a>

### GS-SPL-07. sprint_planning.coverage_analysis

sprint_planning.coverage_analysis (content_stage): Проверить полноту пути от цели sprint к испытаниям. PREPARE: manual_evidence, planned_sprint. CONTINUE: не требуется. Автоматические поля: traceability, artifacts, evidence. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-08"></a>

### GS-SPL-08. sprint_planning.sprint_inspection

sprint_planning.sprint_inspection (content_stage): Read-only осмотреть весь план. PREPARE: inspection_coverage, findings, inspection_verdict. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-09"></a>

### GS-SPL-09. sprint_planning.remediation

sprint_planning.remediation (content_stage): Исправить draft sprint без перезаписи выполненной истории. PREPARE: planned_sprint, task_contracts, dependency_plan, finding_resolutions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-10"></a>

### GS-SPL-10. sprint_planning.remediation_inspection

sprint_planning.remediation_inspection (content_stage): Осмотреть изменения плана. PREPARE: inspection_coverage, findings, inspection_verdict, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-SPL-11"></a>

### GS-SPL-11. sprint_planning.publication

sprint_planning.publication (mechanical_gate): Механически опубликовать принятый результат по явному разрешению пользователя; не отдельный содержательный этап. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: result, evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GP-INT"></a>

### GP-INT. Полный профиль integration

Независимый process integration: Объединить существующие результаты с target codebase и доказать integrated result без новой самостоятельной функциональности. Creation fields, content stages и mechanical gates, field ownership, phased checks, named transitions, rework и completion полностью заданы соответствующей матрицей. Никакого наследования другого goal_type. Нет unresolved merge entries/conflicts, requirements source preserved по evidence/inspection, target publication observed (FF/no-op явно), all components receipts, последний этап accepted. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

<a id="GS-INT-01"></a>

### GS-INT-01. integration.framing

integration.framing (content_stage): Зафиксировать source/target и нужные требования источников. PREPARE: constraints. CONTINUE: не требуется. Автоматические поля: baseline, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-INT-02"></a>

### GS-INT-02. integration.integration_planning

integration.integration_planning (content_stage): Задать стратегию и exact checks. PREPARE: integration_plan, verification_plan. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-INT-03"></a>

### GS-INT-03. integration.integration

integration.integration (content_stage): Выполнить объединение и решить semantic conflicts. PREPARE: нет новых полей модели. CONTINUE: conflict_resolutions. Автоматические поля: conflicts, artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-INT-04"></a>

### GS-INT-04. integration.verification

integration.verification (content_stage): Подтвердить совместный результат. PREPARE: нет новых полей модели. CONTINUE: verdict. Автоматические поля: evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-INT-05"></a>

### GS-INT-05. integration.inspection

integration.inspection (content_stage): Read-only проверить сохранность source intentions. PREPARE: inspection_coverage, findings, inspection_verdict. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-INT-06"></a>

### GS-INT-06. integration.remediation

integration.remediation (content_stage): Исправить дефекты объединения без новой функциональности. PREPARE: finding_resolutions, conflict_resolutions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability, resolution_bindings. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-INT-07"></a>

### GS-INT-07. integration.remediation_inspection

integration.remediation_inspection (content_stage): Осмотреть corrections. PREPARE: inspection_coverage, findings, inspection_verdict, resolution_review_decisions. CONTINUE: не требуется. Автоматические поля: artifacts, evidence, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

<a id="GS-INT-08"></a>

### GS-INT-08. integration.publication

integration.publication (mechanical_gate): Механически опубликовать принятый результат по явному разрешению пользователя; не отдельный содержательный этап. PREPARE: нет новых полей модели. CONTINUE: не требуется. Автоматические поля: result, evidence, artifacts, traceability. Полный контракт ownership/guards/transitions обязателен в матрице, это не значения defaults.

## 10. Процессы и приёмочная методика

<a id="HR-094"></a>

### HR-094. Фазы результата и авторство данных

Для каждой stage field_ownership перечисляет section/path, producer, phase и evidence dependencies; PREPARE, OBSERVE, CONTINUE, CHECK, SEAL не пользовательские стадии. Поле с двумя producers без явного разбиения paths invalid. Publication gate не имеет agent submission/self-check. Exact контракты и decision tables приведены в lifecycle-contracts.json и data-contracts. Изменённые semantic additions continuation связываются с observation receipt; сохранённый merge/effect не переисполняется. В verification.execution каждой logical_argument/inspection obligation назначены stage_id, evidence_phase=PREPARE или CONTINUE, поле submission и source_refs. Аргумент по уже доступным данным подаётся через manual_evidence на PREPARE. Аргумент по объявленным новым наблюдениям — через observed_manual_evidence на CONTINUE после соответствующих receipts. Пустой список допустим только когда для этой фазы нет обязательных некомандных методов; поле не опускается как default. Нет аргумента PREPARE — нет дорогих запусков; нет аргумента CONTINUE — нет SEAL. Logical-only не вызывает команды; command-only не вызывает лишний CONTINUE. Generated evidence индексирует оба канала; self_inspection принимает/отклоняет предложения. Новых stage/task не возникает.

<a id="HR-095"></a>

### HR-095. Типизированная актуальность связей

Relations: provenance (историческое происхождение, не инвалидирует); subject_snapshot (evidence об exact immutable subject, не переносится на новый subject); requires_current (инвалидирующая зависимость от current selector/version); supersedes (меняет current pointer, не dependency edge); control_prerequisite (eligibility, не удаление результатов). Вычисление validity идёт по DAG requires_current, ограничено budget. Предыдущий review current только для своего subject; исправление с provenance старой версии не становится stale от этой provenance. Неизвестный relation kind/invalidating self-cycle отклоняется. Current report не может использовать review старого subject для нового.

## 07. Вывод, hooks и завершение цикла

<a id="HR-096"></a>

### HR-096. Время жизни выдаваемых адресов

Ready result reference означает bytes уже опубликованы и доступны до explicit expires_at/retention event, pin имеет приоритет. Terminal verify сохраняет все referenced views в durable owner ResultSet, возвращает его native path/logical ref/digest/availability, затем runtime удалён. Task/sprint evidence pinned до явной retention owner; ad-hoc deliverable сохраняется вне runtime до указанного config срока, показанного в ответе. Show после expiry возвращает expired+tombstone, не ready/dangling path и не иную content под старым ID. Native reading немедленно после verify гарантировано. Cleanup не завершает pins и не меняет contents.

## 09. Метрики результата и стоимости

<a id="HR-097"></a>

### HR-097. Числовые эталоны измерений

metrics-contract.json и fixtures/metrics-golden.json задают все bytes, line boundaries, token units/IDs, serialization и ledger events. Fixture tokenizer ETALON-BPE-1 — фиксированный измерительный тестовый прибор с собственными ranks, не approximation model usage и не runtime fallback. Для выбранного production measurement tokenizer release содержит его точный digest и тот же corpus с собственными golden token IDs/counts. Числа tests не становятся defaults runtime. В этой документации эталонный профиль самодостаточен, его расчёт проверен независимо от AI poise. Иные measurement profiles не подменяют эти значения.


# Независимые процессы типов целей

## development — независимый процесс

<a id="GP-DEV"></a>

**GP-DEV. Назначение:** Production-код/поведение приложения; тесты и документация — средства качества и сопутствующий результат.

**Создание:** goal, scope, product/task requirements, DoD, constraints, baseline applicability, documentation applicability, exact commands уже заявленных tests/checks; deferred методы только с указанным verification_planning deadline. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `product_requirements`, `task_requirements`, `definition_of_done`, `constraints`, `documentation_plan`, `baseline_plan`.

**Первый этап:** `baseline`. **Baseline:** Обязательная applicability section required/not_applicable+reason; при required exact observations до изменения.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| baseline | applicability required/not_applicable + reason; при required exact target revision/state и evidence refs. В профилях с mandatory baseline not_applicable запрещён. |
| solution_plan | затрагиваемые компоненты; proposed change/contracts; риски; порядок реализации; решения и assumptions refs. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| test_registry | Test IDs, exact codebase/cwd/mode/argv/environment/input selectors, expected collection/assertion and before/after outcomes, stage applicability, requirement refs. Future test declared at planning has exact command but no observed hash; AI poise binds created file at execution. |
| documentation_plan | required/not_required с причиной; при required deliverables/paths, content requirements и exact validation methods/DoD refs. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| baseline_plan | Creation input: explicit applicability+reason, target identity, exact declared baseline invocation или documented observation method. Не требует будущих execution digests; mandatory для запуска baseline stage. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Точный контракт полей создаваемой задачи

```json
{
  "goal": {
    "type": "object",
    "required": [
      "statement",
      "audience"
    ]
  },
  "scope": {
    "type": "object",
    "required": [
      "codebases",
      "included",
      "excluded",
      "allowed_deliverable_paths"
    ]
  },
  "product_requirements": {
    "type": "object",
    "required": [
      "applicability",
      "refs",
      "reason"
    ],
    "applicability": [
      "required",
      "not_applicable"
    ]
  },
  "task_requirements": {
    "type": "array",
    "min_items": 1,
    "item_required": [
      "id",
      "statement",
      "source_ref",
      "mandatory"
    ]
  },
  "definition_of_done": {
    "type": "array",
    "min_items": 1,
    "item_required": [
      "id",
      "requirement_refs",
      "criterion",
      "expected_deliverable",
      "verification_obligation_refs"
    ]
  },
  "constraints": {
    "type": "object",
    "required": [
      "entries",
      "reason"
    ],
    "item_required": [
      "id",
      "statement",
      "source"
    ]
  },
  "baseline_plan": {
    "type": "object",
    "required": [
      "applicability",
      "reason",
      "target",
      "method_refs"
    ],
    "applicability": [
      "required",
      "not_applicable"
    ]
  },
  "documentation_plan": {
    "type": "object",
    "required": [
      "applicability",
      "reason",
      "deliverables",
      "method_refs"
    ],
    "applicability": [
      "required",
      "not_required"
    ]
  }
}
```

### Этапы и автоматические действия

<a id="GS-DEV-01"></a>

#### 1. `baseline` — содержательный этап

**GS-DEV-01.** Установить исходное поведение или обоснованную неприменимость.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, baseline_plan |
| По запросу | product_requirements, constraints, baseline, solution_plan, verification_plan, test_registry, documentation_plan, assumptions, decisions, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | baseline, artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | При required выполнить exact baseline methods; сверить target/tree и expected observation; при not_applicable проверить reason, не запускать фиктивные тесты. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | baseline observation receipts либо explicit applicability decision; no full suite. |
| Final fields / conditions | {"baseline":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["baseline","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.baseline","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| baseline | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | solution_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-02"></a>

#### 2. `solution_planning` — содержательный этап

**GS-DEV-02.** Спланировать изменение компонентов, интерфейсов и документации.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, constraints, baseline |
| По запросу | product_requirements, solution_plan, verification_plan, test_registry, documentation_plan, assumptions, decisions, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | solution_plan, decisions, assumptions, documentation_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Required typed fields, references/DoD coverage; тестов приложения нет. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | candidate plan и self-check record. |
| Final fields / conditions | {"solution_plan":"always","decisions":"always","assumptions":"always","documentation_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["solution_plan","decisions","assumptions","documentation_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.solution_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| solution_plan | agent | PREPARE | before_observe | task_requirements, definition_of_done, constraints, baseline |
| decisions | agent | PREPARE | before_observe | task_requirements, definition_of_done, constraints, baseline |
| assumptions | agent | PREPARE | before_observe | task_requirements, definition_of_done, constraints, baseline |
| documentation_plan | agent | PREPARE | before_observe | task_requirements, definition_of_done, constraints, baseline |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | verification_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-03"></a>

#### 3. `verification_planning` — содержательный этап

**GS-DEV-03.** Спроектировать доказательства до реализации.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, solution_plan, baseline |
| По запросу | product_requirements, constraints, verification_plan, test_registry, documentation_plan, assumptions, decisions, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | verification_plan, test_registry, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Каждый DoD имеет method; every declared executable exact; RED/GREEN applicability не конфликтует; readiness по записанным probes. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | validated obligations/methods и plan inspection. |
| Final fields / conditions | {"verification_plan":"always","test_registry":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["verification_plan","test_registry","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.verification_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| verification_plan | agent | PREPARE | before_observe | task_requirements, definition_of_done, solution_plan, baseline |
| test_registry | agent | PREPARE | before_observe | task_requirements, definition_of_done, solution_plan, baseline |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | test_implementation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-04"></a>

#### 4. `test_implementation` — содержательный этап

**GS-DEV-04.** Написать тесты/fixtures до production change.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, verification_plan, test_registry, baseline |
| По запросу | product_requirements, task_requirements, definition_of_done, constraints, solution_plan, documentation_plan, assumptions, decisions, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | test_registry, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | tests, fixtures; production delta текущего этапа запрещена |
| Проверки | Precheck новых exact commands в candidate; stage-entry delta только tests/fixtures; все applicable registered RED/unchanged и applicable auto checks; HR-056. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | test tree+selected IDs+collection+failure signatures; verified commit/push. |
| Final fields / conditions | {"test_registry":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["test_registry","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.test_implementation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| test_registry | agent | PREPARE | before_observe | verification_plan, test_registry, baseline |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | test_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-05"></a>

#### 5. `test_inspection` — содержательный этап

**GS-DEV-05.** Read-only осмотр oracle, покрытия, чувствительности и fixtures.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, verification_plan, test_registry, evidence |
| По запросу | product_requirements, constraints, baseline, solution_plan, documentation_plan, assumptions, decisions, artifacts, inspection_coverage, findings, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data; test/production target read-only |
| Проверки | Read-only target; валидность coverage/findings/semantic verdict; inherited evidence current; test rerun только по freshness. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | осмотр exact test result; найденные дефекты предъявленных тестов quality findings. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.test_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | task_requirements, definition_of_done, verification_plan, test_registry, evidence |
| findings | agent | PREPARE | before_observe | task_requirements, definition_of_done, verification_plan, test_registry, evidence |
| inspection_verdict | agent | PREPARE | before_observe | task_requirements, definition_of_done, verification_plan, test_registry, evidence |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | implementation | continue |
| accepted_with_blocking_findings_and_selected_target | test_remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-06"></a>

#### 6. `test_remediation` — содержательный этап

**GS-DEV-06.** Исправить зарегистрированные дефекты тестов.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, test_registry, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, constraints, baseline, solution_plan, documentation_plan, assumptions, decisions, artifacts, evidence, inspection_coverage, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | finding_resolutions, test_registry, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | tests, fixtures |
| Проверки | Resolution precheck; affected exact RED methods и mapped tests; no production delta. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | resolution evidence и test commit/push. |
| Final fields / conditions | {"finding_resolutions":"always","test_registry":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["finding_resolutions","test_registry","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.test_remediation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| finding_resolutions | agent | PREPARE | before_observe | findings, test_registry, verification_plan |
| test_registry | agent | PREPARE | before_observe | findings, test_registry, verification_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | test_remediation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-07"></a>

#### 7. `test_remediation_inspection` — содержательный этап

**GS-DEV-07.** Проверить исправление тестов без правки target.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, finding_resolutions, evidence, test_registry, resolution_bindings |
| По запросу | product_requirements, task_requirements, definition_of_done, constraints, baseline, solution_plan, verification_plan, documentation_plan, assumptions, decisions, artifacts, inspection_coverage, findings, inspection_verdict, traceability, result, user_feedback, handoff, manual_evidence, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | inspection_coverage, inspection_verdict, findings, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Каждое claimed resolution accepted/rejected с evidence; target unchanged. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | решения по каждому исправлению. |
| Final fields / conditions | {"inspection_coverage":"always","inspection_verdict":"always","findings":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","inspection_verdict","findings","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.test_remediation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | finding_resolutions, evidence, test_registry, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | finding_resolutions, evidence, test_registry, resolution_bindings |
| findings | agent | PREPARE | before_observe | finding_resolutions, evidence, test_registry, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | finding_resolutions, evidence, test_registry, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | implementation | continue |
| accepted_with_blocking_findings_and_selected_target | test_remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-08"></a>

#### 8. `implementation` — содержательный этап

**GS-DEV-08.** Реализовать production change по принятым тестам.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, solution_plan, verification_plan, test_registry, task_requirements |
| По запросу | product_requirements, definition_of_done, constraints, baseline, documentation_plan, assumptions, decisions, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | production code; изменение тестового oracle требует user-directed возврата к test stage |
| Проверки | Stage-entry changes code; тесты/fixtures не переписываются здесь; registered after-GREEN+applicable auto checks; trees равны commit/push. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | exact executions, tree vector, commit/push receipts. |
| Final fields / conditions | {"artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.implementation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | implementation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-09"></a>

#### 9. `implementation_inspection` — содержательный этап

**GS-DEV-09.** Read-only осмотр корректности, сложности и соответствия.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, solution_plan, evidence, traceability |
| По запросу | product_requirements, constraints, baseline, verification_plan, test_registry, documentation_plan, assumptions, decisions, artifacts, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Target unchanged, diagnostics из exact methods; coverage and evidence links; semantic verdict records. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | осмотр предъявленного implementation result. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.implementation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | task_requirements, definition_of_done, solution_plan, evidence, traceability |
| findings | agent | PREPARE | before_observe | task_requirements, definition_of_done, solution_plan, evidence, traceability |
| inspection_verdict | agent | PREPARE | before_observe | task_requirements, definition_of_done, solution_plan, evidence, traceability |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean_and_documentation_required | documentation | continue |
| accepted_clean_and_documentation_not_required | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | implementation_remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-10"></a>

#### 10. `implementation_remediation` — содержательный этап

**GS-DEV-10.** Исправить находки реализации.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, solution_plan, verification_plan, test_registry |
| По запросу | product_requirements, task_requirements, definition_of_done, constraints, baseline, documentation_plan, assumptions, decisions, artifacts, evidence, inspection_coverage, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | finding_resolutions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | production code; неверный test contract возвращается на test stage |
| Проверки | Resolution record до checks; registered applicable tests+project impact; tree/push equality. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | каждое исправление имеет exact evidence и commit. |
| Final fields / conditions | {"finding_resolutions":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["finding_resolutions","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.implementation_remediation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| finding_resolutions | agent | PREPARE | before_observe | findings, solution_plan, verification_plan, test_registry |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | implementation_remediation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-11"></a>

#### 11. `implementation_remediation_inspection` — содержательный этап

**GS-DEV-11.** Осмотреть каждое исправление read-only.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, finding_resolutions, evidence, task_requirements, resolution_bindings |
| По запросу | product_requirements, definition_of_done, constraints, baseline, solution_plan, verification_plan, test_registry, documentation_plan, assumptions, decisions, artifacts, inspection_coverage, findings, inspection_verdict, traceability, result, user_feedback, handoff, manual_evidence, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Resolution acceptance/rejection, target immutable. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | решения по исправлениям. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.implementation_remediation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | finding_resolutions, evidence, task_requirements, resolution_bindings |
| findings | agent | PREPARE | before_observe | finding_resolutions, evidence, task_requirements, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | finding_resolutions, evidence, task_requirements, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | finding_resolutions, evidence, task_requirements, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean_and_documentation_required | documentation | continue |
| accepted_clean_and_documentation_not_required | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | implementation_remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DEV-12"></a>

#### 12. `documentation` — содержательный этап

**GS-DEV-12.** Выполнить прямо предусмотренную документацию.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, documentation_plan, task_requirements, evidence |
| По запросу | product_requirements, definition_of_done, constraints, baseline, solution_plan, verification_plan, test_registry, assumptions, decisions, artifacts, inspection_coverage, findings, inspection_verdict, finding_resolutions, traceability, result, user_feedback, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | result, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | declared docs; код не меняется |
| Проверки | Только declared docs paths; exact documentation validators; unchanged code не вызывает придуманный full suite. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | validated docs artifact/revision, self-check и commit/push. |
| Final fields / conditions | {"result":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["result","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of development.documentation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| result | agent | PREPARE | before_observe | documentation_plan, task_requirements, evidence |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Все обязательные stages accepted, applicable tests удовлетворяют final predicates, требования/DoD связаны с current accepted evidence, task-quality findings resolved/waived, docs obligation выполнена, весь Git vector проверен/опубликован. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Только конечные task-owned code и declared docs additions+removals; tests/fixtures исключены.

Ветви remediation существуют только при findings, не новые task. Если документация не нужна, decision not_required закреплено, не implicit skip.

## test_development — независимый процесс

<a id="GP-TST"></a>

**GP-TST. Назначение:** Тесты и тестовые fixtures как самостоятельный конечный продукт.

**Создание:** goal/scope, product/task requirements, DoD, exact declared test commands, baseline required/not_applicable decision, test_scope через coverage requirements; required final expected outcomes. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `product_requirements`, `task_requirements`, `definition_of_done`, `constraints`, `baseline_plan`.

**Первый этап:** `baseline`. **Baseline:** Explicit required/not_applicable; причина обязательна. Test sensitivity applicability определяется отдельно, не подменяет baseline.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| baseline | applicability required/not_applicable + reason; при required exact target revision/state и evidence refs. В профилях с mandatory baseline not_applicable запрещён. |
| coverage_matrix | requirement→scenario→case→oracle→method, обязательность и причина explicit exclusion. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| test_registry | Test IDs, exact codebase/cwd/mode/argv/environment/input selectors, expected collection/assertion and before/after outcomes, stage applicability, requirement refs. Future test declared at planning has exact command but no observed hash; AI poise binds created file at execution. |
| fixture_plan | required/not_applicable+reason; при required id,purpose,consumers,input invariants,создание/cleanup,метод validation. |
| sensitivity_plan | case_id, negative-control/known-bad revision/mutation method, exact commands/expected detectable error, clean target restoration; not_applicable только с обоснованием и inspection verdict. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| baseline_plan | Creation input: explicit applicability+reason, target identity, exact declared baseline invocation или documented observation method. Не требует будущих execution digests; mandatory для запуска baseline stage. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-TST-01"></a>

#### 1. `baseline` — содержательный этап

**GS-TST-01.** Установить существующее покрытие/известное поведение.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, baseline_plan |
| По запросу | product_requirements, baseline, coverage_matrix, verification_plan, test_registry, fixture_plan, sensitivity_plan, constraints, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, decisions, user_feedback, traceability, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | baseline, artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Exact baseline commands либо explicitly not_applicable с причиной; no full. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | baseline coverage/results. |
| Final fields / conditions | {"baseline":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["baseline","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of test_development.baseline","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| baseline | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | coverage_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TST-02"></a>

#### 2. `coverage_planning` — содержательный этап

**GS-TST-02.** Связать поведение с будущими тестами.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, baseline |
| По запросу | product_requirements, coverage_matrix, verification_plan, test_registry, fixture_plan, sensitivity_plan, constraints, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, decisions, user_feedback, traceability, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | coverage_matrix, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Required behavior IDs покрыты cases; orphan/missing links rejected. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | coverage contract и self-check. |
| Final fields / conditions | {"coverage_matrix":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["coverage_matrix","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of test_development.coverage_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| coverage_matrix | agent | PREPARE | before_observe | task_requirements, definition_of_done, baseline |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | test_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TST-03"></a>

#### 3. `test_planning` — содержательный этап

**GS-TST-03.** Спроектировать oracle, fixtures, sensitivity и команды.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, coverage_matrix, task_requirements, baseline |
| По запросу | product_requirements, definition_of_done, verification_plan, test_registry, fixture_plan, sensitivity_plan, constraints, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, decisions, user_feedback, traceability, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | verification_plan, test_registry, fixture_plan, sensitivity_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Full invocation каждого planned test; final expected outcomes; sensitivity applicability; fixture create/cleanup methods; no conflicting predicates. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | план воспроизводимых позитивных/негативных controls. |
| Final fields / conditions | {"verification_plan":"always","test_registry":"always","fixture_plan":"always","sensitivity_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["verification_plan","test_registry","fixture_plan","sensitivity_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of test_development.test_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| verification_plan | agent | PREPARE | before_observe | coverage_matrix, task_requirements, baseline |
| test_registry | agent | PREPARE | before_observe | coverage_matrix, task_requirements, baseline |
| fixture_plan | agent | PREPARE | before_observe | coverage_matrix, task_requirements, baseline |
| sensitivity_plan | agent | PREPARE | before_observe | coverage_matrix, task_requirements, baseline |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | test_implementation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TST-04"></a>

#### 4. `test_implementation` — содержательный этап

**GS-TST-04.** Написать только тестовый deliverable.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, verification_plan, test_registry, fixture_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, coverage_matrix, sensitivity_plan, constraints, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, decisions, user_feedback, traceability, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | test_registry, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | tests, fixtures |
| Проверки | Только tests/fixtures; exact final outcome (включая ожидаемый RED) и auto checks; collection проверена. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | run evidence и commit/push tests/fixtures. |
| Final fields / conditions | {"test_registry":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["test_registry","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of test_development.test_implementation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| test_registry | agent | PREPARE | before_observe | verification_plan, test_registry, fixture_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | sensitivity_verification | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TST-05"></a>

#### 5. `sensitivity_verification` — содержательный этап

**GS-TST-05.** Подтвердить различение правильного и неправильного поведения.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, sensitivity_plan, test_registry, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, coverage_matrix, fixture_plan, constraints, artifacts, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, decisions, user_feedback, traceability, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | evidence, artifacts, traceability |
| Разрешённые изменения | temporary isolated controls, no persistent production edits |
| Проверки | Run exact negative control; match designated failure; temporary mutation полностью восстановлена. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | sensitivity result на known state, restoration receipt. |
| Final fields / conditions | {"evidence":"always","artifacts":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["evidence","artifacts","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of test_development.sensitivity_verification","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | test_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TST-06"></a>

#### 6. `test_inspection` — содержательный этап

**GS-TST-06.** Осмотреть coverage/oracles/isolation/fixtures.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, coverage_matrix, test_registry, sensitivity_plan, evidence |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, verification_plan, fixture_plan, constraints, artifacts, inspection_coverage, findings, inspection_verdict, finding_resolutions, decisions, user_feedback, traceability, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Coverage, semantic verdict, unchanged target. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | review каждой обязательной категории. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of test_development.test_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | coverage_matrix, test_registry, sensitivity_plan, evidence |
| findings | agent | PREPARE | before_observe | coverage_matrix, test_registry, sensitivity_plan, evidence |
| inspection_verdict | agent | PREPARE | before_observe | coverage_matrix, test_registry, sensitivity_plan, evidence |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TST-07"></a>

#### 7. `remediation` — содержательный этап

**GS-TST-07.** Исправить дефекты тестового результата.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, verification_plan, test_registry |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, coverage_matrix, fixture_plan, sensitivity_plan, constraints, artifacts, evidence, inspection_coverage, inspection_verdict, finding_resolutions, decisions, user_feedback, traceability, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | finding_resolutions, test_registry, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | tests, fixtures |
| Проверки | Resolution data before run; exact affected final/sensitivity methods, tests/fixtures only. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | resolution runs+commit/push. |
| Final fields / conditions | {"finding_resolutions":"always","test_registry":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["finding_resolutions","test_registry","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of test_development.remediation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| finding_resolutions | agent | PREPARE | before_observe | findings, verification_plan, test_registry |
| test_registry | agent | PREPARE | before_observe | findings, verification_plan, test_registry |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | remediation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TST-08"></a>

#### 8. `remediation_inspection` — содержательный этап

**GS-TST-08.** Проверить исправления read-only.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, finding_resolutions, evidence, resolution_bindings |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, coverage_matrix, verification_plan, test_registry, fixture_plan, sensitivity_plan, constraints, artifacts, inspection_coverage, findings, inspection_verdict, decisions, user_feedback, traceability, result, handoff, manual_evidence, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Disposition каждой resolution; target unchanged. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | accepted/rejected resolution evidence. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of test_development.remediation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | finding_resolutions, evidence, resolution_bindings |
| findings | agent | PREPARE | before_observe | finding_resolutions, evidence, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | finding_resolutions, evidence, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | finding_resolutions, evidence, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Coverage complete, final test predicates выполнены, sensitivity handled, fixtures valid, production delivered delta=0, required inspection accepted, task-quality findings closed, commits/push подтверждены. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Final tests+fixtures delta; production=0.

Тест, воспроизводящий существующий баг, может завершиться expected final RED по точному assertion, не по любому nonzero.

## verification — независимый процесс

<a id="GP-VER"></a>

**GP-VER. Назначение:** Доказать или опровергнуть соответствие зафиксированного результата требованиям; не исправлять продукт.

**Создание:** goal/scope, target revision/digest, product/task requirements, DoD проверки (не требование обязательно получить положительный product verdict), declared exact commands/criteria. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts. Для successor допустим явный accepted_result_dependency locator: pending-result reference блокирует старт, но не публикацию валидного draft. До первого stage bootstrap receipt материализации фиксирует точный revision; не используется придуманная будущая SHA.

**Обязательные данные при создании:** `goal`, `scope`, `target`, `product_requirements`, `task_requirements`, `definition_of_done`, `constraints`.

**Первый этап:** `planning`. **Baseline:** Pinned target revision/state вместо отдельного baseline stage.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| target | codebase/revision или immutable artifact digest, base для diff, paths/components; внешняя среда имеет target identity. При создании successor допустима typed binding accepted_result_dependency; это явный известный prerequisite, не default revision. Pinned revision обязателен до active stage; milestone pin_deadline=before_first_stage. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| verification_criteria | id, subject requirement/DoD, expected predicate/вердикт, applicable scope. |
| verification_program | ordered methods, exact inputs/environment/readiness, обязательность, stop/continue-on-outcome, ресурсные границы и limits. |
| verification_methods | Typed methods: command с точным PlannedInvocation; logical_argument/inspection с обязательными submission_stage=execution, evidence_phase PREPARE/CONTINUE, source_refs, criteria/obligation refs, observation_method_refs (явно [] для PREPARE), required=true/false. Никакого определения kind по отсутствию команды. CONTINUE references указывают только на реально исполняемые объявленные command observations; циклический/неизвестный reference недопустим. |
| environment | relevant facts/readiness, versions/input generation, resource identities, no secret values; required facts и observation methods явно заданы. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| execution_results | generated immutable receipts: method,target,key,status,expected/actual,evidence/output refs; unknown отдельно от failed. |
| verdict | по каждому criterion/вопросу: proved/disproved/inconclusive/waived из явного набора; evidence и основания; product verdict отдельно от качества исполнения задачи. |
| evidence | Generated evidence projection объединяет command ExecutionReceipts и отдельно зарегистрированные manual_evidence/observed_manual_evidence. Method kind, subject, provenance, outcome, validity раздельны; некомандному аргументу не создаётся command receipt. Решения self_inspection хранятся отдельными reviewer records. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Список EvidenceProposal для назначенных PREPARE logical_argument/inspection методов. Обязательные id, method_ref, obligation_refs, subject_ref, claim, facts с source_ref, assumptions, inference_summary, conclusion, source_refs. Пустой список явно разрешён только при отсутствии таких обязанностей; reviewer verdict и execution observations не поля агента. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| observed_manual_evidence | Список EvidenceProposal для CONTINUE по новым observation_receipts. Все поля manual_evidence плюс непустые observation_refs. Источники должны реально появиться в OBSERVE; это не новая задача или слой чужого command output. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Точный контракт полей создаваемой задачи

```json
{
  "goal": {
    "type": "object",
    "required": [
      "statement",
      "audience"
    ]
  },
  "scope": {
    "type": "object",
    "required": [
      "codebases",
      "included",
      "excluded",
      "allowed_deliverable_paths"
    ]
  },
  "product_requirements": {
    "type": "object",
    "required": [
      "applicability",
      "refs",
      "reason"
    ],
    "applicability": [
      "required",
      "not_applicable"
    ]
  },
  "task_requirements": {
    "type": "array",
    "min_items": 1,
    "item_required": [
      "id",
      "statement",
      "source_ref",
      "mandatory"
    ]
  },
  "definition_of_done": {
    "type": "array",
    "min_items": 1,
    "item_required": [
      "id",
      "requirement_refs",
      "criterion",
      "expected_deliverable",
      "verification_obligation_refs"
    ]
  },
  "constraints": {
    "type": "object",
    "required": [
      "entries",
      "reason"
    ],
    "item_required": [
      "id",
      "statement",
      "source"
    ]
  },
  "target": {
    "type": "object",
    "required": [
      "codebase",
      "binding"
    ],
    "binding_variants": {
      "pinned_revision": {
        "required": [
          "kind",
          "revision"
        ]
      },
      "accepted_result_dependency": {
        "required": [
          "kind",
          "source_task_ref",
          "dependency_ref",
          "pin_deadline"
        ]
      }
    }
  }
}
```

### Этапы и автоматические действия

<a id="GS-VER-01"></a>

#### 1. `planning` — содержательный этап

**GS-VER-01.** Определить программу, критерии и точные способы доказательства.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, task_requirements, definition_of_done, constraints |
| По запросу | product_requirements, verification_criteria, verification_program, verification_methods, environment, assumptions, execution_results, verdict, evidence, findings, result, traceability, inspection_coverage, inspection_verdict, decisions, user_feedback, finding_resolutions, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | verification_criteria, verification_program, verification_methods, environment, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Каждый mandatory criterion связан с method; exact invocations/expected tests; readiness bindings. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | полная программа/методика. |
| Final fields / conditions | {"verification_criteria":"always","verification_program":"always","verification_methods":"always","environment":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["verification_criteria","verification_program","verification_methods","environment","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of verification.planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | planning, execution, analysis, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| verification_criteria | agent | PREPARE | before_observe | target, task_requirements, definition_of_done, constraints |
| verification_program | agent | PREPARE | before_observe | target, task_requirements, definition_of_done, constraints |
| verification_methods | agent | PREPARE | before_observe | target, task_requirements, definition_of_done, constraints |
| environment | agent | PREPARE | before_observe | target, task_requirements, definition_of_done, constraints |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | execution | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-VER-02"></a>

#### 2. `execution` — содержательный этап

**GS-VER-02.** Выполнить программу и получить evidence.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, verification_program, verification_methods, environment |
| По запросу | product_requirements, task_requirements, definition_of_done, verification_criteria, assumptions, constraints, execution_results, verdict, evidence, findings, result, traceability, inspection_coverage, inspection_verdict, decisions, user_feedback, finding_resolutions, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, observed_manual_evidence |
| Агент на PREPARE | manual_evidence, artifact_paths |
| OBSERVE | После PREPARE coverage выполнить/reuse только exact command methods. Для logical-only executable count=0; structural registration receipt не command receipt. После новых observations выдать их refs/data одним context packet, если требуются CONTINUE proposals; повтор не исполняет OBSERVE заново. |
| Агент на CONTINUE | observed_manual_evidence |
| Условие continuation | CONTINUE обязателен только если план содержит required logical_argument/inspection с evidence_phase=CONTINUE, их declared observation_methods завершены и соответствующие proposals ещё не представлены. command-only и PREPARE logical-only не требуют дополнительного вызова. Отсутствующий или недействительный proposal не считается закрытием obligation. |
| Автоматические выходы | execution_results, evidence, artifacts, traceability |
| Разрешённые изменения | task artifacts и temporary isolated probes; target неизменен |
| Проверки | Проверить полную таблицу method→phase→input field и coverage PREPARE proposals до дорогих запусков. Для CONTINUE дождаться реальных declared observation receipts, принять proposals, затем проверить subject/source links и coverage всего метода. Не выполнять reasoning вместо агента и не оценивать истинность по наличию ссылок. Pinned target unchanged, transient outputs восстановлены. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | По command — действительный ExecutionReceipt. По logical_argument/inspection — EvidenceProposal+registration/provenance, но не command receipt. Покрытие всех обязательных методов до verified; содержательная проверка и отдельные решения — self_inspection. |
| Final fields / conditions | {"manual_evidence":"explicit array; one valid proposal per required PREPARE noncommand method; [] iff none","execution_results":"always","evidence":"always","artifacts":"always","traceability":"always","observed_manual_evidence":"required iff any required CONTINUE noncommand method; absent branch closed explicitly by configured guard (not an invented proposal)"} |
| Когда нужен CONTINUE | pending_noncommand_obligations_after_observe |
| Условие результата | {"required_final":["manual_evidence","execution_results","evidence","artifacts","traceability","observed_manual_evidence"],"subject":"exact candidate subject vector","evidence":"current required obligations of verification.execution","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | planning, execution, analysis, self_inspection |
| Method → фаза → input | [{"kind":"logical_argument","phase":"PREPARE","field":"manual_evidence"},{"kind":"logical_argument","phase":"CONTINUE","field":"observed_manual_evidence"},{"kind":"inspection","phase":"PREPARE","field":"manual_evidence"},{"kind":"inspection","phase":"CONTINUE","field":"observed_manual_evidence"}] |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| manual_evidence | agent | PREPARE | required_PREPARE_noncommand_obligations | current_method_plan, existing_pinned_sources |
| execution_results | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| observed_manual_evidence | agent | CONTINUE | required_CONTINUE_noncommand_obligations | current_method_plan, observation_receipt, pinned_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | analysis | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-VER-03"></a>

#### 3. `analysis` — содержательный этап

**GS-VER-03.** Интерпретировать evidence и сформировать несоответствия.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, verification_criteria, execution_results, evidence |
| По запросу | target, product_requirements, task_requirements, definition_of_done, verification_program, verification_methods, environment, assumptions, constraints, verdict, findings, result, traceability, inspection_coverage, inspection_verdict, decisions, user_feedback, finding_resolutions, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, observed_manual_evidence |
| Агент на PREPARE | findings, verdict, result, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Все criteria имеют evidence/verdict; факты failed не подменяются unknown/passed; findings структурно валидны. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | negative/positive/inconclusive обоснования по каждому criterion. |
| Final fields / conditions | {"findings":"always","verdict":"always","result":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["findings","verdict","result","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of verification.analysis","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | planning, execution, analysis, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| findings | agent | PREPARE | before_observe | verification_criteria, execution_results, evidence |
| verdict | agent | PREPARE | before_observe | verification_criteria, execution_results, evidence |
| result | agent | PREPARE | before_observe | verification_criteria, execution_results, evidence |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | self_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-VER-04"></a>

#### 4. `self_inspection` — содержательный этап

**GS-VER-04.** Осмотреть качество самой проверки.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, verification_criteria, verdict, findings, traceability, evidence |
| По запросу | target, product_requirements, task_requirements, definition_of_done, verification_program, verification_methods, environment, assumptions, constraints, execution_results, result, inspection_coverage, inspection_verdict, decisions, user_feedback, finding_resolutions, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, observed_manual_evidence |
| Агент на PREPARE | inspection_coverage, inspection_verdict, findings, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Target и осматриваемый результат не меняются; review decisions фиксируются отдельно; task-quality objection даёт rework. По каждому некомандному proposal record осмотра указывает accepted/rejected и основание; это не автоматическое принятие AI poise по ссылкам. Rejected proposal создаёт task-quality objection и блокирует completion. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | полнота/непротиворечивость методики и вывода. |
| Final fields / conditions | {"inspection_coverage":"always","inspection_verdict":"always","findings":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","inspection_verdict","findings","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of verification.self_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | planning, execution, analysis, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | verification_criteria, verdict, findings, traceability, evidence |
| inspection_verdict | agent | PREPARE | before_observe | verification_criteria, verdict, findings, traceability, evidence |
| findings | agent | PREPARE | before_observe | verification_criteria, verdict, findings, traceability, evidence |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | planning | continue |
| accepted_with_blocking_findings_and_selected_target | execution | continue |
| accepted_with_blocking_findings_and_selected_target | analysis | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Программа исполнена или user waivers, критерии имеют current evidence/verdict, task-quality objections устранены/waived, target unchanged, temporary probes cleaned. Product findings могут оставаться open и product verdict negative. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision. Каждый обязательный некомандный method имеет EvidenceProposal в назначенной фазе и отдельное принятое reviewer решение; отрицательный product verdict допустим по критерию, а неполный аргумент — дефект выполнения проверки.

**Польза:** Final verification report+accepted verdict statements; raw stdout исключён. Предметная мера — criteria resolved по outcomes.

Negative product verdict не даёт cancelled verification. По каждой процедуре stop/continue-on-failure указаны явно.

## review — независимый процесс

<a id="GP-REV"></a>

**GP-REV. Назначение:** Найти доказанные дефекты/риски в зафиксированном результате, не исправляя его.

**Создание:** goal/scope, exact target/base revision или artifact digest, task requirements/DoD review, применимые product requirements либо explicit not_applicable. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `target`, `product_requirements`, `task_requirements`, `definition_of_done`.

**Первый этап:** `planning`. **Baseline:** Exact reviewed revision/base; работа на snapshot обязательна, worktree не нужен для чистого чтения immutable objects.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| target | codebase/revision или immutable artifact digest, base для diff, paths/components; внешняя среда имеет target identity. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| review_criteria | id, inspected property, required scope, method, requirement refs при наличии; не обязательно исчерпывающий product spec. |
| review_plan | revision/diff bounds, units, criteria allocation, inspection sequence, excluded scope+reason. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| verdict | по каждому criterion/вопросу: proved/disproved/inconclusive/waived из явного набора; evidence и основания; product verdict отдельно от качества исполнения задачи. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| constraints | Явные ограничения review scope; absent не заменяется значениями другого process pack. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-REV-01"></a>

#### 1. `planning` — содержательный этап

**GS-REV-01.** Определить scope, критерии и единицы осмотра.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, task_requirements, definition_of_done |
| По запросу | product_requirements, review_criteria, review_plan, inspection_coverage, verdict, assumptions, findings, evidence, result, inspection_verdict, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, constraints |
| Агент на PREPARE | review_criteria, review_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Pinned target exists, scope valid, критерии/обязательные units заданы. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | inspection contract. |
| Final fields / conditions | {"review_criteria":"always","review_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["review_criteria","review_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of review.planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | planning, inspection, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| review_criteria | agent | PREPARE | before_observe | goal, target, scope, task_requirements, definition_of_done |
| review_plan | agent | PREPARE | before_observe | goal, target, scope, task_requirements, definition_of_done |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-REV-02"></a>

#### 2. `inspection` — содержательный этап

**GS-REV-02.** Read-only изучить target и оформить доказанные findings.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, review_criteria, review_plan, task_requirements |
| По запросу | product_requirements, definition_of_done, inspection_coverage, verdict, assumptions, findings, evidence, result, inspection_verdict, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, constraints |
| Агент на PREPARE | inspection_coverage, findings, manual_evidence, verdict, result, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Target unchanged; coverage links; каждый finding subject/evidence/impact; exact optional checks только по plan. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | finding set и рассмотренные scope units. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","manual_evidence":"always","verdict":"always","result":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","manual_evidence","verdict","result","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of review.inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | planning, inspection, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | target, review_criteria, review_plan, task_requirements |
| findings | agent | PREPARE | before_observe | target, review_criteria, review_plan, task_requirements |
| manual_evidence | agent | PREPARE | before_observe | target, review_criteria, review_plan, task_requirements |
| verdict | agent | PREPARE | before_observe | target, review_criteria, review_plan, task_requirements |
| result | agent | PREPARE | before_observe | target, review_criteria, review_plan, task_requirements |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | self_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-REV-03"></a>

#### 3. `self_inspection` — содержательный этап

**GS-REV-03.** Проверить качество собственного review.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, review_criteria, inspection_coverage, findings, evidence, verdict |
| По запросу | product_requirements, task_requirements, definition_of_done, review_plan, assumptions, result, inspection_verdict, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, constraints |
| Агент на PREPARE | inspection_verdict, decisions, findings, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Отдельные accept/reject/duplicate decisions для findings, не удаление; target unchanged. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | review качества доказательств и полноты. |
| Final fields / conditions | {"inspection_verdict":"always","decisions":"always","findings":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_verdict","decisions","findings","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of review.self_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | planning, inspection, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_verdict | agent | PREPARE | before_observe | target, review_criteria, inspection_coverage, findings, evidence, verdict |
| decisions | agent | PREPARE | before_observe | target, review_criteria, inspection_coverage, findings, evidence, verdict |
| findings | agent | PREPARE | before_observe | target, review_criteria, inspection_coverage, findings, evidence, verdict |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Scope/criteria coverage и evidence полны, review quality accepted, target unchanged. Open subject findings не блокируют; unresolved task-quality defects блокируют. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Final delivered finding texts+review result; raw outputs и self-QA issues исключены, subject findings count отдельно.

Solution rationale не inline в независимом inspection; доступен on-demand. Product findings связываются с original target task без копирования/создания remediation child task автоматически.

## design — независимый процесс

<a id="GP-DES"></a>

**GP-DES. Назначение:** Спроектировать техническое решение/архитектуру/контракт без production реализации.

**Создание:** goal/scope, product/task requirements, DoD, constraints, allowed design deliverables, baseline applicability, required alternatives count явно в task/project contract, declared validation commands. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `product_requirements`, `task_requirements`, `definition_of_done`, `constraints`, `baseline_plan`.

**Первый этап:** `framing`. **Baseline:** required/not_applicable с причиной; constraints/source state всегда определены.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| baseline | applicability required/not_applicable + reason; при required exact target revision/state и evidence refs. В профилях с mandatory baseline not_applicable запрещён. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| decision_criteria | id, statement, priority/weights только при явно выбранной scoring model, evidence/predicate для сравнения. |
| alternatives | id, description, requirements/constraints coverage, pros/cons, risk/assumption refs, evaluation по каждому критерию. |
| selected_approach | alternative_id, rationale, criteria comparisons, constraints/evidence refs, rejected alternatives причины. |
| design | components/responsibilities, данные, состояния/переходы, interfaces, errors/failures, concurrency/security/observability applicability, requirement mapping; каждый неприменимый блок явно помечен с причиной. |
| interfaces_contracts | applicability; API/CLI/data contract, inputs/outputs/errors, side effects, invariants, versioned target references, schema validators. |
| impact_analysis | affected/unchanged codebases/components/contracts; prerequisites/risks; downstream requirements, не самостоятельная реализация. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| verdict | по каждому criterion/вопросу: proved/disproved/inconclusive/waived из явного набора; evidence и основания; product verdict отдельно от качества исполнения задачи. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| baseline_plan | Creation input: explicit applicability+reason, target identity, exact declared baseline invocation или documented observation method. Не требует будущих execution digests; mandatory для запуска baseline stage. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-DES-01"></a>

#### 1. `framing` — содержательный этап

**GS-DES-01.** Зафиксировать исходную архитектуру и критерии выбора.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, constraints, baseline_plan |
| По запросу | product_requirements, baseline, assumptions, decision_criteria, alternatives, selected_approach, design, interfaces_contracts, impact_analysis, verification_plan, decisions, artifacts, evidence, verdict, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, user_feedback, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | baseline, assumptions, decision_criteria, verification_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Baseline applicability, sources constraints, criteria completeness, method каждого DoD до основного design. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | framing facts и доказательный план. |
| Final fields / conditions | {"baseline":"always","assumptions":"always","decision_criteria":"always","verification_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["baseline","assumptions","decision_criteria","verification_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of design.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| baseline | agent | PREPARE | before_observe | task_requirements, definition_of_done, constraints, baseline_plan |
| assumptions | agent | PREPARE | before_observe | task_requirements, definition_of_done, constraints, baseline_plan |
| decision_criteria | agent | PREPARE | before_observe | task_requirements, definition_of_done, constraints, baseline_plan |
| verification_plan | agent | PREPARE | before_observe | task_requirements, definition_of_done, constraints, baseline_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | alternatives | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DES-02"></a>

#### 2. `alternatives` — содержательный этап

**GS-DES-02.** Сравнить допустимые решения.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, baseline, constraints, decision_criteria, task_requirements |
| По запросу | product_requirements, definition_of_done, assumptions, alternatives, selected_approach, design, interfaces_contracts, impact_analysis, verification_plan, decisions, artifacts, evidence, verdict, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, user_feedback, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | alternatives, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Число вариантов равно/больше явно required; каждый рассмотрен по всем criteria; структурное наличие не доказывает лучший вариант. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | comparison evidence и self-check. |
| Final fields / conditions | {"alternatives":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["alternatives","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of design.alternatives","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| alternatives | agent | PREPARE | before_observe | baseline, constraints, decision_criteria, task_requirements |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | decision | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DES-03"></a>

#### 3. `decision` — содержательный этап

**GS-DES-03.** Выбрать решение и обосновать отказ от других.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, alternatives, decision_criteria, constraints |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, assumptions, selected_approach, design, interfaces_contracts, impact_analysis, verification_plan, decisions, artifacts, evidence, verdict, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, user_feedback, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | selected_approach, decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Selected ID существует, criteria/constraints refs полны; rejected semantic predicate блокирует решение. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | decision rationale+evidence. |
| Final fields / conditions | {"selected_approach":"always","decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["selected_approach","decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of design.decision","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| selected_approach | agent | PREPARE | before_observe | alternatives, decision_criteria, constraints |
| decisions | agent | PREPARE | before_observe | alternatives, decision_criteria, constraints |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | detailed_design | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DES-04"></a>

#### 4. `detailed_design` — содержательный этап

**GS-DES-04.** Описать конкретные contracts и failure modes.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, selected_approach, task_requirements, definition_of_done, verification_plan |
| По запросу | product_requirements, baseline, constraints, assumptions, decision_criteria, alternatives, design, interfaces_contracts, impact_analysis, decisions, artifacts, evidence, verdict, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, user_feedback, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | design, interfaces_contracts, impact_analysis, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | declared design documents/schemas/diagram sources, не production |
| Проверки | Typed design dimensions resolved; allowed design paths; declared schema check commands. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | design content+contract validation; commit/push при Git artifacts. |
| Final fields / conditions | {"design":"always","interfaces_contracts":"always","impact_analysis":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["design","interfaces_contracts","impact_analysis","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of design.detailed_design","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| design | agent | PREPARE | before_observe | selected_approach, task_requirements, definition_of_done, verification_plan |
| interfaces_contracts | agent | PREPARE | before_observe | selected_approach, task_requirements, definition_of_done, verification_plan |
| impact_analysis | agent | PREPARE | before_observe | selected_approach, task_requirements, definition_of_done, verification_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | design_verification | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DES-05"></a>

#### 5. `design_verification` — содержательный этап

**GS-DES-05.** Проверить design против требований.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, design, interfaces_contracts, verification_plan, task_requirements |
| По запросу | product_requirements, definition_of_done, baseline, constraints, assumptions, decision_criteria, alternatives, selected_approach, impact_analysis, decisions, artifacts, evidence, verdict, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, user_feedback, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: manual_evidence, verdict. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | manual_evidence, verdict |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | traceability, artifacts, evidence |
| Разрешённые изменения | task-data |
| Проверки | Execute exact validators; logical proofs имеют facts/assumptions/conclusion; all DoD linked. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | criterion verdicts по exact design digest. |
| Final fields / conditions | {"traceability":"always","artifacts":"always","evidence":"always","manual_evidence":"always","verdict":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["traceability","artifacts","evidence","manual_evidence","verdict"],"subject":"exact candidate subject vector","evidence":"current required obligations of design.design_verification","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| manual_evidence | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| verdict | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | design_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DES-06"></a>

#### 6. `design_inspection` — содержательный этап

**GS-DES-06.** Осмотреть consistency, неопределённости и лишнюю сложность.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, design, interfaces_contracts, constraints, verdict, evidence |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, assumptions, decision_criteria, alternatives, selected_approach, impact_analysis, verification_plan, decisions, artifacts, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, user_feedback, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Read-only design target; findings/evidence links; required semantic verdict. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | осмотр design и contracts. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of design.design_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | design, interfaces_contracts, constraints, verdict, evidence |
| findings | agent | PREPARE | before_observe | design, interfaces_contracts, constraints, verdict, evidence |
| inspection_verdict | agent | PREPARE | before_observe | design, interfaces_contracts, constraints, verdict, evidence |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DES-07"></a>

#### 7. `remediation` — содержательный этап

**GS-DES-07.** Исправить design findings.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, design, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, constraints, assumptions, decision_criteria, alternatives, selected_approach, interfaces_contracts, impact_analysis, decisions, artifacts, evidence, verdict, traceability, inspection_coverage, inspection_verdict, finding_resolutions, user_feedback, result, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | design, interfaces_contracts, finding_resolutions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | declared design artifacts |
| Проверки | Affected proofs stale; required exact checks/logic обновлены, allowed paths. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | resolution evidence и новые design layers/commit. |
| Final fields / conditions | {"design":"always","interfaces_contracts":"always","finding_resolutions":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["design","interfaces_contracts","finding_resolutions","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of design.remediation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| design | agent | PREPARE | before_observe | findings, design, verification_plan |
| interfaces_contracts | agent | PREPARE | before_observe | findings, design, verification_plan |
| finding_resolutions | agent | PREPARE | before_observe | findings, design, verification_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | remediation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DES-08"></a>

#### 8. `remediation_inspection` — содержательный этап

**GS-DES-08.** Проверить design corrections read-only.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, finding_resolutions, design, evidence, resolution_bindings |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, constraints, assumptions, decision_criteria, alternatives, selected_approach, interfaces_contracts, impact_analysis, verification_plan, decisions, artifacts, verdict, traceability, inspection_coverage, findings, inspection_verdict, user_feedback, result, handoff, manual_evidence, resolution_review_decisions, baseline_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Disposition каждой resolution; no target edit. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | resolution verdicts. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of design.remediation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | finding_resolutions, design, evidence, resolution_bindings |
| findings | agent | PREPARE | before_observe | finding_resolutions, design, evidence, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | finding_resolutions, design, evidence, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | finding_resolutions, design, evidence, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Контракт полного design, alternatives/decision зафиксированы, DoD/proofs current, все task-quality findings resolved/waived, allowed artifacts сохранены и Git receipt при необходимости. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Final design/contracts sections и declared design files по content identity один раз; diagrams binary отдельно bytes.

Для UI/API/DB design applicability dimensions определяется явно, а не универсальный обязательный API. Config count alternatives — параметр, отсутствие invalid, не hardcoded 2/3.

## analysis — независимый процесс

<a id="GP-ANA"></a>

**GP-ANA. Назначение:** Доказательный аналитический вывод по заданным вопросам без изменения предмета исследования.

**Создание:** goal/scope, task requirements/DoD, questions, product requirements либо explicit not_applicable, source access и declared exact processing commands. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `product_requirements`, `task_requirements`, `definition_of_done`, `questions`.

**Первый этап:** `framing`. **Baseline:** Source revisions/time window и method snapshot; Git baseline для входных данных только если они в Git.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| questions | id, точный вопрос, scope, relation to requirement/DoD, acceptable answer form. |
| source_plan | required source classes, time window, sample/coverage policy, parsers/aggregation, exclusions, completeness criteria. |
| sources | id, origin locator, revision/digest/time, relevance, allowed use; extracted claims имеют refs. |
| facts | id, statement, source/evidence refs, measured scope/time; невыясненное утверждение маркируется assumption. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| method | аналитическая/измерительная процедура, inputs, steps, reproducible calculations/exact commands, assumptions, expected information; не private chain of thought. |
| analysis | claims с fact/evidence refs, assumptions, inference summary, alternative explanations; охват questions/metrics. |
| alternative_explanations | id, объяснение, supporting/counter facts, проверка различия с основным выводом и disposition. |
| counterevidence | source/fact, какой claim оспаривает, disposition/изменение вывода; explicit search performed+none если не найдено. |
| limitations | что не установлено, ограничения данных/метода, влияние на conclusions/DoD; explicit none с основанием. |
| conclusions | question/criterion refs, claim, facts+assumptions, inference summary, ограничения и verdict. |
| recommendations | предлагаемое действие, основание conclusion/evidence, expected effect, риски и способ последующей проверки; не выполняется автоматически. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-ANA-01"></a>

#### 1. `framing` — содержательный этап

**GS-ANA-01.** Сформулировать вопросы и метод до сбора огромного массива.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_requirements, definition_of_done, questions |
| По запросу | product_requirements, source_plan, sources, facts, assumptions, method, analysis, alternative_explanations, counterevidence, limitations, conclusions, recommendations, artifacts, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, evidence, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | source_plan, method, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Required questions explicit; sample/period/source classes/processing strategy; exact declared commands. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | план источников и вычислений. |
| Final fields / conditions | {"source_plan":"always","method":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["source_plan","method","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of analysis.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, evidence_collection, reasoning, challenge, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| source_plan | agent | PREPARE | before_observe | goal, scope, task_requirements, definition_of_done, questions |
| method | agent | PREPARE | before_observe | goal, scope, task_requirements, definition_of_done, questions |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | evidence_collection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ANA-02"></a>

#### 2. `evidence_collection` — содержательный этап

**GS-ANA-02.** Обработать данные инструментами, извлечь факты.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, source_plan, method, questions |
| По запросу | product_requirements, task_requirements, definition_of_done, sources, facts, assumptions, analysis, alternative_explanations, counterevidence, limitations, conclusions, recommendations, artifacts, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, evidence, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: sources, facts, assumptions. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | sources, facts, assumptions |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task artifacts; исследуемые inputs immutable |
| Проверки | Source refs/digests exist, facts имеют provenance, required coverage измерена; no raw corpus inline. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | extraction receipts и compact fact registry. |
| Final fields / conditions | {"artifacts":"always","evidence":"always","traceability":"always","sources":"always","facts":"always","assumptions":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["artifacts","evidence","traceability","sources","facts","assumptions"],"subject":"exact candidate subject vector","evidence":"current required obligations of analysis.evidence_collection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, evidence_collection, reasoning, challenge, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| sources | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| facts | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| assumptions | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | reasoning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ANA-03"></a>

#### 3. `reasoning` — содержательный этап

**GS-ANA-03.** Сформировать проверяемые выводы.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, questions, facts, assumptions, method |
| По запросу | product_requirements, task_requirements, definition_of_done, source_plan, sources, analysis, alternative_explanations, counterevidence, limitations, conclusions, recommendations, artifacts, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, evidence, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | analysis, conclusions, recommendations, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Каждый claim связан с facts/assumptions; required question answered; semantic истинность не вычисляется общей schema. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | короткие inspectable arguments и self-check. |
| Final fields / conditions | {"analysis":"always","conclusions":"always","recommendations":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["analysis","conclusions","recommendations","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of analysis.reasoning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, evidence_collection, reasoning, challenge, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| analysis | agent | PREPARE | before_observe | questions, facts, assumptions, method |
| conclusions | agent | PREPARE | before_observe | questions, facts, assumptions, method |
| recommendations | agent | PREPARE | before_observe | questions, facts, assumptions, method |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | challenge | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ANA-04"></a>

#### 4. `challenge` — содержательный этап

**GS-ANA-04.** Проверить альтернативы и опровергающие данные.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, analysis, conclusions, facts, assumptions |
| По запросу | product_requirements, task_requirements, definition_of_done, questions, source_plan, sources, method, alternative_explanations, counterevidence, limitations, recommendations, artifacts, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, evidence, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | alternative_explanations, counterevidence, limitations, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Required claims covered challenge; explicit evidence searched/no counterevidence, no fake source. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | контрпримеры, ограничения, disposition claims. |
| Final fields / conditions | {"alternative_explanations":"always","counterevidence":"always","limitations":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["alternative_explanations","counterevidence","limitations","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of analysis.challenge","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, evidence_collection, reasoning, challenge, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| alternative_explanations | agent | PREPARE | before_observe | analysis, conclusions, facts, assumptions |
| counterevidence | agent | PREPARE | before_observe | analysis, conclusions, facts, assumptions |
| limitations | agent | PREPARE | before_observe | analysis, conclusions, facts, assumptions |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | self_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ANA-05"></a>

#### 5. `self_inspection` — содержательный этап

**GS-ANA-05.** Осмотреть достоверность собственного анализа.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, questions, conclusions, analysis, alternative_explanations, counterevidence, limitations |
| По запросу | product_requirements, task_requirements, definition_of_done, source_plan, sources, facts, assumptions, method, recommendations, artifacts, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, evidence, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, result, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | No unreviewed mandatory conclusions; target data unchanged; semantic objections recorded. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | review причинности/выборки/поддержки. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","result":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","result","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of analysis.self_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, evidence_collection, reasoning, challenge, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | questions, conclusions, analysis, alternative_explanations, counterevidence, limitations |
| findings | agent | PREPARE | before_observe | questions, conclusions, analysis, alternative_explanations, counterevidence, limitations |
| inspection_verdict | agent | PREPARE | before_observe | questions, conclusions, analysis, alternative_explanations, counterevidence, limitations |
| result | agent | PREPARE | before_observe | questions, conclusions, analysis, alternative_explanations, counterevidence, limitations |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | evidence_collection | continue |
| accepted_with_blocking_findings_and_selected_target | reasoning | continue |
| accepted_with_blocking_findings_and_selected_target | challenge | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Все mandatory questions имеют обоснованный final conclusion, challenge/limitations выполнены, факты traced, task-quality objections resolved/waived, inputs не изменены, final report durable. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Последние accepted analysis/conclusions/recommendations; сырьё, неудачные гипотезы и прежние версии исключены.

Разрешён inconclusive conclusion только при DoD, требующем честную оценку данных, а не выдуманное доказательство. Отрицательный результат анализа не отменяет успешно выполненную задачу.

## profiling — независимый процесс

<a id="GP-PRO"></a>

**GP-PRO. Назначение:** Измерить performance/resources характеристики и подтвердить выводы; постоянная оптимизация не входит.

**Создание:** goal/scope, target, task requirements/DoD, метрики/единицы, baseline=required, exact declared measurement commands, workload/resources и допустимые temporary effects. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `target`, `product_requirements`, `task_requirements`, `definition_of_done`, `metrics`.

**Первый этап:** `framing`. **Baseline:** Обязателен: фактическое исходное measurement, не только нормативный threshold.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| target | codebase/revision или immutable artifact digest, base для diff, paths/components; внешняя среда имеет target identity. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| metrics | id, unit, observable/aggregation, exact measuring method, acceptable/expected result policy, requirement refs. |
| baseline | applicability required/not_applicable + reason; при required exact target revision/state и evidence refs. В профилях с mandatory baseline not_applicable запрещён. |
| environment | relevant facts/readiness, versions/input generation, resource identities, no secret values; required facts и observation methods явно заданы. |
| workload | input/data digests, concurrency/cache/warmup/measurement schedule, exact generation/execution commands, resource namespaces. |
| experiment_plan | id, question/hypothesis, controlled/changed variables, exact actions/measurements, expected discriminatory information, order and limits. |
| instrumentation_plan | tools/exact commands, temporary changes/allowed paths, before state, restoration/check commands, observer-effect risks. |
| measurements | generated experiment/run IDs, metrics+units, target/environment/workload identity, timestamps, raw refs, uncertainty/repeatability data. |
| analysis | claims с fact/evidence refs, assumptions, inference summary, alternative explanations; охват questions/metrics. |
| alternative_explanations | id, объяснение, supporting/counter facts, проверка различия с основным выводом и disposition. |
| limitations | что не установлено, ограничения данных/метода, влияние на conclusions/DoD; explicit none с основанием. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| recommendations | предлагаемое действие, основание conclusion/evidence, expected effect, риски и способ последующей проверки; не выполняется автоматически. |
| confirmation | critical claim/root_cause/finding refs, independent/repeat procedure, exact command либо reasoned method, actual evidence и verdict. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| confirmation_plan | Claims to confirm; exact confirmation methods, input selectors, repetitions/limits, independent observation criterion. Формируется в analysis до confirmation execution. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-PRO-01"></a>

#### 1. `framing` — содержательный этап

**GS-PRO-01.** Спланировать исходное измерение и критерии воспроизводимости.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, task_requirements, definition_of_done, metrics |
| По запросу | product_requirements, baseline, environment, workload, experiment_plan, instrumentation_plan, measurements, analysis, alternative_explanations, limitations, findings, recommendations, confirmation, verification_plan, evidence, artifacts, inspection_coverage, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | environment, workload, verification_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Метрики/units/target, точные baseline commands, повторения/warmup/limits/stability predicates заданы; readiness. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | baseline measurement method до execution. |
| Final fields / conditions | {"environment":"always","workload":"always","verification_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["environment","workload","verification_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of profiling.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| environment | agent | PREPARE | before_observe | target, task_requirements, definition_of_done, metrics |
| workload | agent | PREPARE | before_observe | target, task_requirements, definition_of_done, metrics |
| verification_plan | agent | PREPARE | before_observe | target, task_requirements, definition_of_done, metrics |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | baseline_measurement | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-PRO-02"></a>

#### 2. `baseline_measurement` — содержательный этап

**GS-PRO-02.** Получить исходные measurements.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, metrics, environment, workload, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, baseline, experiment_plan, instrumentation_plan, measurements, analysis, alternative_explanations, limitations, findings, recommendations, confirmation, evidence, artifacts, inspection_coverage, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | baseline, evidence, artifacts, traceability |
| Разрешённые изменения | task-data |
| Проверки | Run exact baseline sequence; validate collection/units/environment/stability; unstable даёт bounded rework/blocked, не random retry. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | исходные measured values и repeatability. |
| Final fields / conditions | {"baseline":"always","evidence":"always","artifacts":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["baseline","evidence","artifacts","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of profiling.baseline_measurement","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| baseline | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | experiment_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-PRO-03"></a>

#### 3. `experiment_planning` — содержательный этап

**GS-PRO-03.** Спланировать различающие эксперименты после baseline.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, baseline, metrics, task_requirements |
| По запросу | target, product_requirements, definition_of_done, environment, workload, experiment_plan, instrumentation_plan, measurements, analysis, alternative_explanations, limitations, findings, recommendations, confirmation, verification_plan, evidence, artifacts, inspection_coverage, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | experiment_plan, instrumentation_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Controlled/changed variables и exact commands, confirmation plans, restoration policies; no unknown side effect replay. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | experiment contract. |
| Final fields / conditions | {"experiment_plan":"always","instrumentation_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["experiment_plan","instrumentation_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of profiling.experiment_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| experiment_plan | agent | PREPARE | before_observe | baseline, metrics, task_requirements |
| instrumentation_plan | agent | PREPARE | before_observe | baseline, metrics, task_requirements |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | measurement | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-PRO-04"></a>

#### 4. `measurement` — содержательный этап

**GS-PRO-04.** Выполнить эксперименты.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, experiment_plan, instrumentation_plan, environment, workload |
| По запросу | target, product_requirements, task_requirements, definition_of_done, metrics, baseline, measurements, analysis, alternative_explanations, limitations, findings, recommendations, confirmation, verification_plan, evidence, artifacts, inspection_coverage, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | measurements, artifacts, evidence, traceability |
| Разрешённые изменения | temporary declared instrumentation в isolated target с restoration; permanent code не меняется |
| Проверки | Exact execution, target/resource identity, restoration of temporary changes; full data сохраняется не inline. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | measurement receipts/dumps/values. |
| Final fields / conditions | {"measurements":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["measurements","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of profiling.measurement","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| measurements | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | analysis | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-PRO-05"></a>

#### 5. `analysis` — содержательный этап

**GS-PRO-05.** Отделить измерения от интерпретаций.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, baseline, measurements, metrics, experiment_plan |
| По запросу | target, product_requirements, task_requirements, definition_of_done, environment, workload, instrumentation_plan, analysis, alternative_explanations, limitations, findings, recommendations, confirmation, verification_plan, evidence, artifacts, inspection_coverage, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | analysis, findings, alternative_explanations, recommendations, limitations, confirmation_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Каждый bottleneck claim имеет measurement refs; scope conclusions не проверяется одной ссылкой. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | facts→claim trace и self-check. |
| Final fields / conditions | {"analysis":"always","findings":"always","alternative_explanations":"always","recommendations":"always","limitations":"always","confirmation_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["analysis","findings","alternative_explanations","recommendations","limitations","confirmation_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of profiling.analysis","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| analysis | agent | PREPARE | before_observe | baseline, measurements, metrics, experiment_plan |
| findings | agent | PREPARE | before_observe | baseline, measurements, metrics, experiment_plan |
| alternative_explanations | agent | PREPARE | before_observe | baseline, measurements, metrics, experiment_plan |
| recommendations | agent | PREPARE | before_observe | baseline, measurements, metrics, experiment_plan |
| limitations | agent | PREPARE | before_observe | baseline, measurements, metrics, experiment_plan |
| confirmation_plan | agent | PREPARE | before_observe | baseline, measurements, metrics, experiment_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | confirmation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-PRO-06"></a>

#### 6. `confirmation` — содержательный этап

**GS-PRO-06.** Независимо/повторно подтвердить существенные выводы.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, analysis, experiment_plan, measurements, confirmation_plan |
| По запросу | target, product_requirements, task_requirements, definition_of_done, metrics, baseline, environment, workload, instrumentation_plan, alternative_explanations, limitations, recommendations, confirmation, verification_plan, evidence, artifacts, inspection_coverage, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: confirmation. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | confirmation |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | evidence, artifacts, traceability |
| Разрешённые изменения | temporary declared measurement only |
| Проверки | Exact confirmation methods per mandatory finding, target/workload comparability. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | independent/repeat receipts и verdicts. |
| Final fields / conditions | {"evidence":"always","artifacts":"always","traceability":"always","confirmation":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["evidence","artifacts","traceability","confirmation"],"subject":"exact candidate subject vector","evidence":"current required obligations of profiling.confirmation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| confirmation | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | self_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-PRO-07"></a>

#### 7. `self_inspection` — содержательный этап

**GS-PRO-07.** Осмотреть качество измерений и выводов.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, metrics, baseline, measurements, analysis, confirmation, limitations |
| По запросу | target, product_requirements, task_requirements, definition_of_done, environment, workload, experiment_plan, instrumentation_plan, alternative_explanations, findings, recommendations, verification_plan, evidence, artifacts, inspection_coverage, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, result, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Required confirmation и restoration evidence; target без persistent instrumentation; semantic verdict. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | review bias/uncertainty/reproducibility. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","result":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","result","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of profiling.self_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | metrics, baseline, measurements, analysis, confirmation, limitations |
| findings | agent | PREPARE | before_observe | metrics, baseline, measurements, analysis, confirmation, limitations |
| inspection_verdict | agent | PREPARE | before_observe | metrics, baseline, measurements, analysis, confirmation, limitations |
| result | agent | PREPARE | before_observe | metrics, baseline, measurements, analysis, confirmation, limitations |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | experiment_planning | continue |
| accepted_with_blocking_findings_and_selected_target | measurement | continue |
| accepted_with_blocking_findings_and_selected_target | analysis | continue |
| accepted_with_blocking_findings_and_selected_target | confirmation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Measurements/baseline/confirmation complete, target/environment traced, temporary instrumentation removed, выводы осмотрены; неудовлетворительная производительность продукта не мешает завершению profiling. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Final compact measurement summary+analysis/findings/recommendations, confirmed metric/criteria count; raw profiler dumps не payload benefit.

Количество runs/статистическая модель и thresholds не придуманы kernel; если task их не задала, expensive stage blocked до дополнения.

## environment_diagnostics — независимый процесс

<a id="GP-DIA"></a>

**GP-DIA. Назначение:** Установить причину сбоя среды/tooling и дать воспроизводимую рекомендацию, не постоянное исправление.

**Создание:** goal/scope, environment target, symptoms/expected_behavior, task requirements/DoD, allowed temporary effects и exact declared probes; reproduction applicability. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `target`, `product_requirements`, `task_requirements`, `definition_of_done`, `symptoms`, `expected_behavior`.

**Первый этап:** `framing`. **Baseline:** Environment snapshot обязателен; reproduction required/not_applicable отдельно.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| target | codebase/revision или immutable artifact digest, base для diff, paths/components; внешняя среда имеет target identity. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| symptoms | наблюдаемый результат, expected behavior reference, scope/time, evidence либо user observation attribution. |
| expected_behavior | id, корректное наблюдаемое состояние, source requirement и comparison method. |
| environment_snapshot | generated baseline facts, exact observation commands, time/generation, resources/config refs без secrets. |
| reproduction | required/not_applicable+reason; exact method and expected symptom, observed outcome и evidence. |
| facts | id, statement, source/evidence refs, measured scope/time; невыясненное утверждение маркируется assumption. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| hypotheses | id, statement, supporting/contradicting facts, exact test, expected true/false observations, unresolved/supported/refuted. |
| diagnostic_plan | ordered hypothesis checks, exact commands, allowed temporary effects, restoration и budget. |
| experiments | generated/action receipts и observations с references на plan/hypotheses и before/after state. |
| root_cause | cause statement, symptoms/facts/experiments links, rejected alternatives, confirmation refs, limitations; inconclusive только если разрешён DoD задачи. |
| confirmation | critical claim/root_cause/finding refs, independent/repeat procedure, exact command либо reasoned method, actual evidence и verdict. |
| recommendations | предлагаемое действие, основание conclusion/evidence, expected effect, риски и способ последующей проверки; не выполняется автоматически. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| confirmation_plan | Root cause refs, точный controlled confirmation method, expected distinguishing observations, restore operation/probe и limits. Формируется root_cause_analysis. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-DIA-01"></a>

#### 1. `framing` — содержательный этап

**GS-DIA-01.** Определить симптом и необходимые факты среды.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, symptoms, expected_behavior, target, task_requirements, definition_of_done |
| По запросу | product_requirements, environment_snapshot, reproduction, facts, assumptions, hypotheses, diagnostic_plan, experiments, root_cause, confirmation, recommendations, verification_plan, evidence, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | verification_plan, artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: facts. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | facts |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | environment_snapshot, artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Exact observation commands, secret redaction, resource scope; no giant uncontrolled dump. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | snapshot/observations. |
| Final fields / conditions | {"verification_plan":"always","environment_snapshot":"always","artifacts":"always","evidence":"always","traceability":"always","facts":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["verification_plan","environment_snapshot","artifacts","evidence","traceability","facts"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_diagnostics.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| verification_plan | agent | PREPARE | before_observe | symptoms, expected_behavior, target, task_requirements, definition_of_done |
| environment_snapshot | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| facts | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | reproduction | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DIA-02"></a>

#### 2. `reproduction` — содержательный этап

**GS-DIA-02.** Проверить исходный симптом.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, environment_snapshot, symptoms, verification_plan |
| По запросу | target, product_requirements, task_requirements, definition_of_done, expected_behavior, reproduction, facts, assumptions, hypotheses, diagnostic_plan, experiments, root_cause, confirmation, recommendations, evidence, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: reproduction. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | reproduction |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | evidence, artifacts, traceability |
| Разрешённые изменения | task-data |
| Проверки | Execute exact reproduction либо explicit not_applicable reason; distinguish not_reproduced/blocked/observed; no invented result. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | repro receipt. |
| Final fields / conditions | {"evidence":"always","artifacts":"always","traceability":"always","reproduction":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["evidence","artifacts","traceability","reproduction"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_diagnostics.reproduction","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| reproduction | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | hypothesis_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DIA-03"></a>

#### 3. `hypothesis_planning` — содержательный этап

**GS-DIA-03.** Составить проверяемые альтернативные причины.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, facts, reproduction, symptoms |
| По запросу | target, product_requirements, task_requirements, definition_of_done, expected_behavior, environment_snapshot, assumptions, hypotheses, diagnostic_plan, experiments, root_cause, confirmation, recommendations, verification_plan, evidence, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | hypotheses, diagnostic_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Каждая H имеет exact probe/true-false observation, temporary effect/restore; budgets. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | hypothesis→experiment plan. |
| Final fields / conditions | {"hypotheses":"always","diagnostic_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["hypotheses","diagnostic_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_diagnostics.hypothesis_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| hypotheses | agent | PREPARE | before_observe | facts, reproduction, symptoms |
| diagnostic_plan | agent | PREPARE | before_observe | facts, reproduction, symptoms |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | experiments | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DIA-04"></a>

#### 4. `experiments` — содержательный этап

**GS-DIA-04.** Выполнить диагностические experiments.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, hypotheses, diagnostic_plan, environment_snapshot |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, expected_behavior, reproduction, facts, assumptions, experiments, root_cause, confirmation, recommendations, verification_plan, evidence, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: facts. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | facts |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | experiments, evidence, artifacts, traceability |
| Разрешённые изменения | temporary declared environment effects with restoration |
| Проверки | Run exact bundle, capture before/after, restore; no PID polling модели. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | наблюдения по H, external receipts. |
| Final fields / conditions | {"experiments":"always","evidence":"always","artifacts":"always","traceability":"always","facts":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["experiments","evidence","artifacts","traceability","facts"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_diagnostics.experiments","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| experiments | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| facts | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | root_cause_analysis | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DIA-05"></a>

#### 5. `root_cause_analysis` — содержательный этап

**GS-DIA-05.** Обосновать причину и отвергнутые альтернативы.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, experiments, facts, hypotheses, symptoms |
| По запросу | target, product_requirements, task_requirements, definition_of_done, expected_behavior, environment_snapshot, reproduction, assumptions, diagnostic_plan, root_cause, confirmation, recommendations, verification_plan, evidence, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | root_cause, recommendations, confirmation_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Причина имеет факты/experiments/assumptions; не считать любой успешный workaround объяснением. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | cause argument. |
| Final fields / conditions | {"root_cause":"always","recommendations":"always","confirmation_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["root_cause","recommendations","confirmation_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_diagnostics.root_cause_analysis","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| root_cause | agent | PREPARE | before_observe | experiments, facts, hypotheses, symptoms |
| recommendations | agent | PREPARE | before_observe | experiments, facts, hypotheses, symptoms |
| confirmation_plan | agent | PREPARE | before_observe | experiments, facts, hypotheses, symptoms |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | confirmation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DIA-06"></a>

#### 6. `confirmation` — содержательный этап

**GS-DIA-06.** Проверить причинность контролируемым изменением.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, root_cause, diagnostic_plan, environment_snapshot, confirmation_plan |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, expected_behavior, reproduction, facts, assumptions, hypotheses, experiments, confirmation, recommendations, verification_plan, evidence, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: confirmation. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | confirmation |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | evidence, artifacts, traceability |
| Разрешённые изменения | temporary environment correction only |
| Проверки | Exact confirmation method и возврат исходной среды; permanent repair не публикуется. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | symptom исчезает/возвращается по контролю или другой заявленный oracle. |
| Final fields / conditions | {"evidence":"always","artifacts":"always","traceability":"always","confirmation":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["evidence","artifacts","traceability","confirmation"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_diagnostics.confirmation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| confirmation | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | self_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DIA-07"></a>

#### 7. `self_inspection` — содержательный этап

**GS-DIA-07.** Проверить диагноз и рекомендацию.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, root_cause, confirmation, recommendations, experiments |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, expected_behavior, environment_snapshot, reproduction, facts, assumptions, hypotheses, diagnostic_plan, verification_plan, evidence, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, finding_resolutions, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions, confirmation_plan |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, result, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Confirmation current; all temporary effects restored; diagnosis scope и evidence verdict. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | review причинности/альтернатив. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","result":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","result","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_diagnostics.self_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | root_cause, confirmation, recommendations, experiments |
| findings | agent | PREPARE | before_observe | root_cause, confirmation, recommendations, experiments |
| inspection_verdict | agent | PREPARE | before_observe | root_cause, confirmation, recommendations, experiments |
| result | agent | PREPARE | before_observe | root_cause, confirmation, recommendations, experiments |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | reproduction | continue |
| accepted_with_blocking_findings_and_selected_target | hypothesis_planning | continue |
| accepted_with_blocking_findings_and_selected_target | experiments | continue |
| accepted_with_blocking_findings_and_selected_target | root_cause_analysis | continue |
| accepted_with_blocking_findings_and_selected_target | confirmation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Причина доказана в границах DoD или разрешённый inconclusive verdict, required confirmation exists, рекомендация проверяема, temporary state restored, task-quality objections closed. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Confirmed cause+support summary+recommendations; desired diagnostic questions resolved; не raw stdout.

Если нужен постоянный fix, пользователь заводит environment_remediation. Обычная временная диагностическая корректировка допустима только с restore proof.

## environment_remediation — независимый процесс

<a id="GP-ENV"></a>

**GP-ENV. Назначение:** Изменить среду/tooling и доказать достижение явно заданного desired state.

**Создание:** goal/scope, environment target, desired_state/task requirements/DoD, baseline required, authority/privileges, exact declared change/verification commands; диагностика входом только если существует. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `target`, `desired_state`, `product_requirements`, `task_requirements`, `definition_of_done`.

**Первый этап:** `framing`. **Baseline:** Baseline обязателен; без него environment mutation запрещена.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| target | codebase/revision или immutable artifact digest, base для diff, paths/components; внешняя среда имеет target identity. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| symptoms | наблюдаемый результат, expected behavior reference, scope/time, evidence либо user observation attribution. |
| desired_state | id, exact target property/criterion, requirement/DoD refs, verification command/method and expected condition. |
| baseline | applicability required/not_applicable + reason; при required exact target revision/state и evidence refs. В профилях с mandatory baseline not_applicable запрещён. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| change_plan | desired-state obligations, exact ordered actions, prerequisites, privileges, observable receipts, replay/rollback policy, risks. |
| rollback_plan | reversible steps exact undo+verification, irreversible effects явно и основание пользовательской санкции, interruption policy. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| execution_results | generated immutable receipts: method,target,key,status,expected/actual,evidence/output refs; unknown отдельно от failed. |
| observed_state | generated after facts, desired criteria comparisons, persistent changes/temporary residue, evidence refs. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| verdict | по каждому criterion/вопросу: proved/disproved/inconclusive/waived из явного набора; evidence и основания; product verdict отдельно от качества исполнения задачи. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-ENV-01"></a>

#### 1. `framing` — содержательный этап

**GS-ENV-01.** Определить разрешённый target и desired state.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, desired_state, task_requirements, definition_of_done |
| По запросу | product_requirements, symptoms, baseline, constraints, assumptions, change_plan, rollback_plan, verification_plan, execution_results, observed_state, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | scope, constraints, verification_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Target scope/privilege constraints, exact baseline probes, DoD state predicates. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | разрешённый effect scope. |
| Final fields / conditions | {"scope":"always","constraints":"always","verification_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["scope","constraints","verification_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_remediation.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| scope | agent | PREPARE | before_observe | target, desired_state, task_requirements, definition_of_done |
| constraints | agent | PREPARE | before_observe | target, desired_state, task_requirements, definition_of_done |
| verification_plan | agent | PREPARE | before_observe | target, desired_state, task_requirements, definition_of_done |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | baseline | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ENV-02"></a>

#### 2. `baseline` — содержательный этап

**GS-ENV-02.** Снять исходное состояние до изменения.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, target, desired_state, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, symptoms, baseline, constraints, assumptions, change_plan, rollback_plan, execution_results, observed_state, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | baseline, evidence, artifacts, traceability |
| Разрешённые изменения | task-data |
| Проверки | Execute exact probes, secrets redacted, required baseline facts present. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | before-state receipts. |
| Final fields / conditions | {"baseline":"always","evidence":"always","artifacts":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["baseline","evidence","artifacts","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_remediation.baseline","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| baseline | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | change_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ENV-03"></a>

#### 3. `change_planning` — содержательный этап

**GS-ENV-03.** Определить actions, verification и безопасное прерывание.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, baseline, desired_state, constraints |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, assumptions, change_plan, rollback_plan, verification_plan, execution_results, observed_state, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | change_plan, rollback_plan, verification_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Exact argv/cwd/env/probes, replay class, rollback либо explicit irreversible authorization, time/retry budgets. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | validated plan и user decisions. |
| Final fields / conditions | {"change_plan":"always","rollback_plan":"always","verification_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["change_plan","rollback_plan","verification_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_remediation.change_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| change_plan | agent | PREPARE | before_observe | baseline, desired_state, constraints |
| rollback_plan | agent | PREPARE | before_observe | baseline, desired_state, constraints |
| verification_plan | agent | PREPARE | before_observe | baseline, desired_state, constraints |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | application | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ENV-04"></a>

#### 4. `application` — содержательный этап

**GS-ENV-04.** Применить подготовленные механические действия.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, change_plan, rollback_plan, baseline |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, desired_state, constraints, assumptions, verification_plan, execution_results, observed_state, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | execution_results, observed_state, evidence, artifacts, traceability |
| Разрешённые изменения | declared external environment effects; declared Git config/scripts |
| Проверки | Execute only next safe deterministic bundle; при неизвестном исходе probe/needs_resolution, не повтор. Verify selected state probes; Git config scripts commit/push при наличии. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | before/after/action receipts. |
| Final fields / conditions | {"execution_results":"always","observed_state":"always","evidence":"always","artifacts":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["execution_results","observed_state","evidence","artifacts","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_remediation.application","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| execution_results | poise | CHECK | before_seal | bound_subject, operation_receipts |
| observed_state | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | verification | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ENV-05"></a>

#### 5. `verification` — содержательный этап

**GS-ENV-05.** Подтвердить desired state и отсутствие побочных нарушений.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, desired_state, observed_state, verification_plan |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, baseline, constraints, assumptions, change_plan, rollback_plan, execution_results, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: verdict. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | verdict |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | evidence, artifacts, traceability |
| Разрешённые изменения | task-data |
| Проверки | Exact task methods+applicable auto checks для code/tests/fixtures, not auto config impact; no guessed full suite. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | criteria accepted/negative evidence. |
| Final fields / conditions | {"evidence":"always","artifacts":"always","traceability":"always","verdict":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["evidence","artifacts","traceability","verdict"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_remediation.verification","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| verdict | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ENV-06"></a>

#### 6. `inspection` — содержательный этап

**GS-ENV-06.** Read-only осмотреть достигнутое состояние.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, desired_state, baseline, observed_state, evidence, verdict |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, constraints, assumptions, change_plan, rollback_plan, verification_plan, execution_results, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, result, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | No mutation during inspection; persistence/side effects/rollback obligations covered. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | semantic inspection. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","result":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","result","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_remediation.inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | desired_state, baseline, observed_state, evidence, verdict |
| findings | agent | PREPARE | before_observe | desired_state, baseline, observed_state, evidence, verdict |
| inspection_verdict | agent | PREPARE | before_observe | desired_state, baseline, observed_state, evidence, verdict |
| result | agent | PREPARE | before_observe | desired_state, baseline, observed_state, evidence, verdict |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | correction | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ENV-07"></a>

#### 7. `correction` — содержательный этап

**GS-ENV-07.** Исправить замечания по среде с новым exact action plan.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, observed_state, rollback_plan |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, desired_state, baseline, constraints, assumptions, change_plan, verification_plan, execution_results, evidence, verdict, inspection_coverage, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | change_plan, finding_resolutions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | observed_state, artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | declared external target / Git config-script deliverables |
| Проверки | Новый plan до effect; exact corrections+checks, no side-effect duplicate. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | correction receipts/evidence. |
| Final fields / conditions | {"change_plan":"always","finding_resolutions":"always","observed_state":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["change_plan","finding_resolutions","observed_state","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_remediation.correction","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| change_plan | agent | PREPARE | before_observe | findings, observed_state, rollback_plan |
| finding_resolutions | agent | PREPARE | before_observe | findings, observed_state, rollback_plan |
| observed_state | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | correction_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-ENV-08"></a>

#### 8. `correction_inspection` — содержательный этап

**GS-ENV-08.** Проверить corrections read-only.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, finding_resolutions, observed_state, evidence, resolution_bindings |
| По запросу | target, product_requirements, task_requirements, definition_of_done, symptoms, desired_state, baseline, constraints, assumptions, change_plan, rollback_plan, verification_plan, execution_results, verdict, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, result, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Resolution verdict каждого finding; persistent desired changes остаются, временные остатки удалены. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | accepted corrections. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","result":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","result","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of environment_remediation.correction_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | finding_resolutions, observed_state, evidence, resolution_bindings |
| findings | agent | PREPARE | before_observe | finding_resolutions, observed_state, evidence, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | finding_resolutions, observed_state, evidence, resolution_bindings |
| result | agent | PREPARE | before_observe | finding_resolutions, observed_state, evidence, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | finding_resolutions, observed_state, evidence, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | correction | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Desired criteria proved, permanent required changes остаются, temporary effects removed, task-quality findings resolved/waived, exact action/verification evidence complete; Git commit обязателен только для Git deliverables. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Основное — desired-state criteria achieved; final concise result text supplemental; не считать весь installed software payload созданным агентом.

User-executed instruction фиксируется как external action и последующий наблюдаемый state receipt, не как будто command исполнил AI poise.

## documentation — независимый процесс

<a id="GP-DOC"></a>

**GP-DOC. Назначение:** Создать/обновить документацию как основной deliverable.

**Создание:** goal/scope, audience, content/task requirements, DoD, source identities, publication target/allowed docs paths, declared executable examples/check commands. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `audience`, `product_requirements`, `task_requirements`, `definition_of_done`.

**Первый этап:** `framing`. **Baseline:** Source-of-truth revisions/digests обязательны; отдельное baseline measurement не нужно.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| audience | целевой читатель, уровень предпосылок, решаемая задача документа, criteria понятности. |
| source_inventory | source-of-truth refs с revision/digest, coverage content claims, конфликтующие/устаревшие источники. |
| content_requirements | id, требуемая информация/пример/инструкция, source и DoD refs, required section. |
| outline | section IDs, purpose, content_requirement refs, последовательность. |
| content | final document refs/digests либо Markdown content; requirement→section mapping; не полный duplicate Git file в DB. |
| examples_commands | document locator, exact executable method, sandbox/mutation bounds, expected outcome, evidence refs. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| publication_target | codebase/path или task artifact, формат и правила delivery; явная push/transport policy. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| verdict | по каждому criterion/вопросу: proved/disproved/inconclusive/waived из явного набора; evidence и основания; product verdict отдельно от качества исполнения задачи. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-DOC-01"></a>

#### 1. `framing` — содержательный этап

**GS-DOC-01.** Определить аудиторию и источники истины.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, audience, task_requirements, definition_of_done |
| По запросу | product_requirements, source_inventory, content_requirements, outline, content, examples_commands, verification_plan, publication_target, assumptions, constraints, artifacts, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | source_inventory, content_requirements, verification_plan, publication_target, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Source revisions resolved, content criteria/DoD mapped, exact declared checks; source_inventory не отдельная пауза. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | source/content contract. |
| Final fields / conditions | {"source_inventory":"always","content_requirements":"always","verification_plan":"always","publication_target":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["source_inventory","content_requirements","verification_plan","publication_target","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of documentation.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| source_inventory | agent | PREPARE | before_observe | goal, scope, audience, task_requirements, definition_of_done |
| content_requirements | agent | PREPARE | before_observe | goal, scope, audience, task_requirements, definition_of_done |
| verification_plan | agent | PREPARE | before_observe | goal, scope, audience, task_requirements, definition_of_done |
| publication_target | agent | PREPARE | before_observe | goal, scope, audience, task_requirements, definition_of_done |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | outline | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DOC-02"></a>

#### 2. `outline` — содержательный этап

**GS-DOC-02.** Спроектировать структуру по content requirements.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, content_requirements, audience, source_inventory |
| По запросу | product_requirements, task_requirements, definition_of_done, outline, content, examples_commands, verification_plan, publication_target, assumptions, constraints, artifacts, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | outline, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Every required content ID covered; no orphan section without purpose. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | outline/self-check. |
| Final fields / conditions | {"outline":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["outline","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of documentation.outline","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| outline | agent | PREPARE | before_observe | content_requirements, audience, source_inventory |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | drafting | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DOC-03"></a>

#### 3. `drafting` — содержательный этап

**GS-DOC-03.** Написать documentation deliverable.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, outline, audience, source_inventory, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, content_requirements, content, examples_commands, publication_target, assumptions, constraints, artifacts, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | content, examples_commands, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | declared documentation paths или task artifacts |
| Проверки | Allowed docs paths only; exact новых examples в candidate; syntax/refs checks по plan, no code modification. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | document digest/revision и commit/push. |
| Final fields / conditions | {"content":"always","examples_commands":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["content","examples_commands","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of documentation.drafting","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| content | agent | PREPARE | before_observe | outline, audience, source_inventory, verification_plan |
| examples_commands | agent | PREPARE | before_observe | outline, audience, source_inventory, verification_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | validation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DOC-04"></a>

#### 4. `validation` — содержательный этап

**GS-DOC-04.** Проверить примеры/ссылки и источники.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, content, examples_commands, source_inventory, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, audience, content_requirements, outline, publication_target, assumptions, constraints, artifacts, evidence, verdict, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: verdict. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | verdict |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | evidence, artifacts, traceability |
| Разрешённые изменения | task-data |
| Проверки | Execute exact sandboxed commands/link/schema checks; source semantic match оценивает агент отдельно. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | example execution и claim/source validation evidence. |
| Final fields / conditions | {"evidence":"always","artifacts":"always","traceability":"always","verdict":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["evidence","artifacts","traceability","verdict"],"subject":"exact candidate subject vector","evidence":"current required obligations of documentation.validation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| verdict | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DOC-05"></a>

#### 5. `inspection` — содержательный этап

**GS-DOC-05.** Read-only вычитать по audience и completeness.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, content, audience, content_requirements, evidence, verdict |
| По запросу | product_requirements, task_requirements, definition_of_done, source_inventory, outline, examples_commands, verification_plan, publication_target, assumptions, constraints, artifacts, inspection_coverage, findings, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, result, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Coverage+references; нет правок document target в inspection. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | review корректности/понятности. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","result":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","result","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of documentation.inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | content, audience, content_requirements, evidence, verdict |
| findings | agent | PREPARE | before_observe | content, audience, content_requirements, evidence, verdict |
| inspection_verdict | agent | PREPARE | before_observe | content, audience, content_requirements, evidence, verdict |
| result | agent | PREPARE | before_observe | content, audience, content_requirements, evidence, verdict |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DOC-06"></a>

#### 6. `remediation` — содержательный этап

**GS-DOC-06.** Исправить документацию по findings.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, content, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, audience, source_inventory, content_requirements, outline, examples_commands, publication_target, assumptions, constraints, artifacts, evidence, verdict, inspection_coverage, inspection_verdict, result, finding_resolutions, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | content, finding_resolutions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | declared documentation |
| Проверки | Required resolution records; affected exact validations; docs scope only. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | new document + evidence + commit/push. |
| Final fields / conditions | {"content":"always","finding_resolutions":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["content","finding_resolutions","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of documentation.remediation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| content | agent | PREPARE | before_observe | findings, content, verification_plan |
| finding_resolutions | agent | PREPARE | before_observe | findings, content, verification_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | remediation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-DOC-07"></a>

#### 7. `remediation_inspection` — содержательный этап

**GS-DOC-07.** Повторно вычитать corrections.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, content, finding_resolutions, evidence, resolution_bindings |
| По запросу | product_requirements, task_requirements, definition_of_done, audience, source_inventory, content_requirements, outline, examples_commands, verification_plan, publication_target, assumptions, constraints, artifacts, verdict, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, result, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Resolution decisions, unchanged reviewed document. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | resolution verdicts. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","result":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","result","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of documentation.remediation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | content, finding_resolutions, evidence, resolution_bindings |
| findings | agent | PREPARE | before_observe | content, finding_resolutions, evidence, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | content, finding_resolutions, evidence, resolution_bindings |
| result | agent | PREPARE | before_observe | content, finding_resolutions, evidence, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | content, finding_resolutions, evidence, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_completion_gate | accept |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Content requirements covered, required examples checked, sources traced, docs durable/published по target, inspection accepted и task-quality findings closed. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Final documentation add+remove, не сумма черновиков; examples как часть docs один раз.

Документация побочным DoD development остаётся в development. Классы docs для metrics не включаются в code/tests/fixtures impact автоматически.

## task_planning — независимый процесс

<a id="GP-TPL"></a>

**GP-TPL. Назначение:** Опубликовать полноценный contract одной будущей/пересматриваемой задачи.

**Создание:** goal/scope, planning_target=create/revise с revision для revise, product/task requirements планирования, DoD планирования, target project/sprint либо explicit standalone. Планируемая goal classification определяется этапом classification, не смешивается с goal_type planner. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `planning_target`, `product_requirements`, `task_requirements`, `definition_of_done`.

**Первый этап:** `framing`. **Baseline:** Для revise exact target revision; для create planning input snapshot, не фиктивный Git baseline.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| planning_target | create/revise, target ID/revision для revise, destination project/sprint либо standalone, expected publication boundary. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| goal_classification | один goal_type, основной deliverable, grounds по skill, независимые вторичные цели вынесены в отдельные candidates. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| deliverables | id, type, owner/path, expected content/schema, acceptance method и requirement refs. |
| work_plan | ordered work items и dependencies; outcome каждого; required inputs; scope/риски; без копирования mechanical lifecycle steps. |
| acceptance_design | target requirements→DoD→deliverables/obligations/methods, applicability и разрешённые поздние design steps. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| planned_task | полный draft target task по собственному goal_type schema, creation validation receipt, origin planning owner, canonical publish receipt после publication. |
| risks | id, condition, impact, mitigation/контроль, owner decision; explicit none допускается. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-TPL-01"></a>

#### 1. `framing` — содержательный этап

**GS-TPL-01.** Определить конечную цель планируемой task.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planning_target, product_requirements, task_requirements, definition_of_done |
| По запросу | goal_classification, constraints, assumptions, deliverables, work_plan, acceptance_design, verification_plan, planned_task, risks, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | constraints, assumptions, deliverables, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Target exists/not exists согласно create/revise; нет редактирования active чужой task; requirements refs. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | planning scope. |
| Final fields / conditions | {"constraints":"always","assumptions":"always","deliverables":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["constraints","assumptions","deliverables","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| constraints | agent | PREPARE | before_observe | planning_target, product_requirements, task_requirements, definition_of_done |
| assumptions | agent | PREPARE | before_observe | planning_target, product_requirements, task_requirements, definition_of_done |
| deliverables | agent | PREPARE | before_observe | planning_target, product_requirements, task_requirements, definition_of_done |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | classification | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-02"></a>

#### 2. `classification` — содержательный этап

**GS-TPL-02.** Выбрать один target goal_type по основному результату.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, deliverables, constraints, planning_target |
| По запросу | product_requirements, task_requirements, definition_of_done, goal_classification, assumptions, work_plan, acceptance_design, verification_plan, planned_task, risks, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | goal_classification, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | planned_task, artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Один известный type; create draft из его полного template; mixed независимые цели → невалидный draft/декомпозиция. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | classification rationale + draft identity. |
| Final fields / conditions | {"goal_classification":"always","planned_task":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["goal_classification","planned_task","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.classification","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| goal_classification | agent | PREPARE | before_observe | deliverables, constraints, planning_target |
| planned_task | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | requirements | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-03"></a>

#### 3. `requirements` — содержательный этап

**GS-TPL-03.** Уточнить требования в draft target task.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, goal_classification, product_requirements, planned_task |
| По запросу | planning_target, task_requirements, definition_of_done, constraints, assumptions, deliverables, work_plan, acceptance_design, verification_plan, risks, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | planned_task, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Target creation-phase predicates requirements/scope; current stage permissions; draft не eligible. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | target requirement links. |
| Final fields / conditions | {"planned_task":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["planned_task","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.requirements","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| planned_task | agent | PREPARE | before_observe | goal_classification, product_requirements, planned_task |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | acceptance_design | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-04"></a>

#### 4. `acceptance_design` — содержательный этап

**GS-TPL-04.** Спроектировать DoD и deliverables будущей task.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_task, deliverables |
| По запросу | planning_target, product_requirements, task_requirements, definition_of_done, goal_classification, constraints, assumptions, work_plan, acceptance_design, verification_plan, risks, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | acceptance_design, planned_task, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Every target requirement covered DoD; artifacts/expected output declared. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | DoD→deliverable mapping. |
| Final fields / conditions | {"acceptance_design":"always","planned_task":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["acceptance_design","planned_task","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.acceptance_design","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| acceptance_design | agent | PREPARE | before_observe | planned_task, deliverables |
| planned_task | agent | PREPARE | before_observe | planned_task, deliverables |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | work_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-05"></a>

#### 5. `work_planning` — содержательный этап

**GS-TPL-05.** Заполнить process-specific planning inputs.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_task, acceptance_design, constraints |
| По запросу | planning_target, product_requirements, task_requirements, definition_of_done, goal_classification, assumptions, deliverables, work_plan, verification_plan, risks, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | work_plan, planned_task, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Applicability baseline/docs/fixtures по target pack; future-stage fields permitted only explicitly; no mechanical duplicated checklist. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | process input readiness. |
| Final fields / conditions | {"work_plan":"always","planned_task":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["work_plan","planned_task","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.work_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| work_plan | agent | PREPARE | before_observe | planned_task, acceptance_design, constraints |
| planned_task | agent | PREPARE | before_observe | planned_task, acceptance_design, constraints |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | verification_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-06"></a>

#### 6. `verification_planning` — содержательный этап

**GS-TPL-06.** Зафиксировать способы доказательства будущей задачи.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_task, work_plan, acceptance_design |
| По запросу | planning_target, product_requirements, task_requirements, definition_of_done, goal_classification, constraints, assumptions, deliverables, verification_plan, risks, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | verification_plan, planned_task, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Every declared executable test имеет exact command; target-specific creation validators; deferred methods only named future method-design stage. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | validated exact methods/coverage. |
| Final fields / conditions | {"verification_plan":"always","planned_task":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["verification_plan","planned_task","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.verification_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| verification_plan | agent | PREPARE | before_observe | planned_task, work_plan, acceptance_design |
| planned_task | agent | PREPARE | before_observe | planned_task, work_plan, acceptance_design |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-07"></a>

#### 7. `task_inspection` — содержательный этап

**GS-TPL-07.** Read-only проверить draft contract.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_task, verification_plan, acceptance_design |
| По запросу | planning_target, product_requirements, task_requirements, definition_of_done, goal_classification, constraints, assumptions, deliverables, work_plan, risks, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Target full creation validator+semantic review; no modification reviewed draft. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | scope/type/requirements/DoD/commands inspection. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.task_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | planned_task, verification_plan, acceptance_design |
| findings | agent | PREPARE | before_observe | planned_task, verification_plan, acceptance_design |
| inspection_verdict | agent | PREPARE | before_observe | planned_task, verification_plan, acceptance_design |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | publication | publish |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-08"></a>

#### 8. `remediation` — содержательный этап

**GS-TPL-08.** Исправить draft по findings.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, planned_task |
| По запросу | planning_target, product_requirements, task_requirements, definition_of_done, goal_classification, constraints, assumptions, deliverables, work_plan, acceptance_design, verification_plan, risks, inspection_coverage, inspection_verdict, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | planned_task, finding_resolutions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | task-data |
| Проверки | Target validators повторно; current owner/target revision unchanged; no partial canonical update. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | resolved draft/evidence. |
| Final fields / conditions | {"planned_task":"always","finding_resolutions":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["planned_task","finding_resolutions","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.remediation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| planned_task | agent | PREPARE | before_observe | findings, planned_task |
| finding_resolutions | agent | PREPARE | before_observe | findings, planned_task |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | remediation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-09"></a>

#### 9. `remediation_inspection` — содержательный этап

**GS-TPL-09.** Осмотреть исправленный contract.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_task, finding_resolutions, resolution_bindings |
| По запросу | planning_target, product_requirements, task_requirements, definition_of_done, goal_classification, constraints, assumptions, deliverables, work_plan, acceptance_design, verification_plan, risks, inspection_coverage, findings, inspection_verdict, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Resolution acceptance и target validators; read-only draft. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | correction verdicts. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of task_planning.remediation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | planned_task, finding_resolutions, resolution_bindings |
| findings | agent | PREPARE | before_observe | planned_task, finding_resolutions, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | planned_task, finding_resolutions, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | planned_task, finding_resolutions, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | publication | publish |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-TPL-10"></a>

#### 10. `publication` — автоматический gate

**GS-TPL-10.** Механически опубликовать принятый результат по явному разрешению пользователя; не отдельный содержательный этап.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_task, inspection_verdict |
| По запросу | planning_target, product_requirements, task_requirements, definition_of_done, goal_classification, constraints, assumptions, deliverables, work_plan, acceptance_design, verification_plan, risks, inspection_coverage, findings, finding_resolutions, result, evidence, decisions, user_feedback, traceability, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | Нет новых обязательных полей модели. |
| OBSERVE | Проверить сохранённый accepted candidate, authority и preconditions; выполнить публикацию idempotently с receipt/probe. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | result, evidence, artifacts, traceability |
| Разрешённые изменения | task-data |
| Проверки | Exact target revision guard, all target creation validators, no open draft findings; одна transaction target+links. Draft до commit не eligible. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | Exact candidate publication receipt, target identity/digest; operation эффект выполнен не более одного раза при известном receipt. |
| Final fields / conditions | {"result":"always","evidence":"always","artifacts":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["result","evidence","artifacts","traceability"],"subject":"accepted immutable candidate","evidence":"publication receipt and all prior required current evidence","phase_terminal":"GATE_SUCCEEDED","on_success":"task_completion_gate","negative_subject_outcome":"not allowed; blocked/error сохраняет candidate и не считает target опубликованным"} |
| Rework targets | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| Authority и отсутствие нового этапа | Однозначная publish instruction с exact candidate/target scope после принятия latest valid inspection. Accept-only запрещает запуск. Нет нового stage-result, self-check, content iteration или второго acceptance. |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| result | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| evidence | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| artifacts | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| traceability | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| gate_succeeded | task_completion_gate | already_authorized |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Будущая task опубликована атомарно, валидна по собственному independent pack, references/methods/dependencies корректны; planning result inspection accepted. При revise предыдущие layers сохранены и invalidations рассчитаны. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Только опубликованный target contract delta от исходного target при revise, а не текст planner iterations.

Публикация target не запускает её исполнение. Для active target требуется завершение/handoff до изменения; root первого sprint можно планировать standalone.

## sprint_planning — независимый процесс

<a id="GP-SPL"></a>

**GP-SPL. Назначение:** Опубликовать валидный sprint с tasks и same-sprint DAG единым согласованным результатом.

**Создание:** goal/scope, planning_target=create/revise, product/sprint requirements, planning DoD, planning inputs/constraints, target project. Standalone planning допустим. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `planning_target`, `planning_inputs`, `product_requirements`, `sprint_requirements`, `definition_of_done`.

**Первый этап:** `framing`. **Baseline:** Pinned planning inputs; revise с exact исходной sprint revision и сохранёнными completed task results.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| planning_target | create/revise, target ID/revision для revise, destination project/sprint либо standalone, expected publication boundary. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| sprint_requirements | id, statement, product requirement refs и scope текущего sprint; минимум одно. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| planning_inputs | зафиксированные требования/исходные результаты, revisions/artifacts, constraints и assumptions. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| assumptions | список id, statement, justification, impact_if_false, evidence/ref status; explicit none. |
| decomposition | candidate work item ID, одна цель/result/scope, вклад в sprint requirements, need rationale. |
| goal_classification | один goal_type, основной deliverable, grounds по skill, независимые вторичные цели вынесены в отдельные candidates. |
| task_contracts | draft tasks с goal_type, полным template/creation fields/exact methods, origin draft ID; индивид. validation receipts. |
| dependency_plan | same-sprint directed edges, required predecessor result SHA/artifact/materialization, justification; без циклов. |
| execution_plan | priorities/risk ordering, checkpoints, ресурсные ограничения, shared preparation; eligible order выводится из DAG, не ручная вторая очередь. |
| shared_context_plan | общие факты/sections с owners и потребляющими task refs, что не дублировать. |
| shared_artifacts_plan | artifact IDs/types/expected producers/consumers, exact paths/format/criteria, ownership sprint. |
| risks | id, condition, impact, mitigation/контроль, owner decision; explicit none допускается. |
| planned_sprint | sprint draft+task drafts+edges+coverage единым объектом, publication target/revision, validation и publish receipts. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-SPL-01"></a>

#### 1. `framing` — содержательный этап

**GS-SPL-01.** Задать цель/границы/DoD sprint и основания.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planning_inputs, product_requirements, sprint_requirements, definition_of_done, planning_target |
| По запросу | task_requirements, constraints, assumptions, decomposition, goal_classification, task_contracts, dependency_plan, execution_plan, shared_context_plan, shared_artifacts_plan, risks, planned_sprint, evidence, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | constraints, assumptions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | planned_sprint, artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Sources pinned, requirements/DoD coverage, draft identity; revise target revision guard. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | sprint draft framing. |
| Final fields / conditions | {"constraints":"always","assumptions":"always","planned_sprint":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["constraints","assumptions","planned_sprint","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| constraints | agent | PREPARE | before_observe | planning_inputs, product_requirements, sprint_requirements, definition_of_done, planning_target |
| assumptions | agent | PREPARE | before_observe | planning_inputs, product_requirements, sprint_requirements, definition_of_done, planning_target |
| planned_sprint | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | decomposition | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-02"></a>

#### 2. `decomposition` — содержательный этап

**GS-SPL-02.** Выделить самостоятельные конечные результаты.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, sprint_requirements, definition_of_done, constraints |
| По запросу | planning_target, product_requirements, task_requirements, planning_inputs, assumptions, decomposition, goal_classification, task_contracts, dependency_plan, execution_plan, shared_context_plan, shared_artifacts_plan, risks, planned_sprint, evidence, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | decomposition, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Every candidate maps sprint requirement; нет пустых целей; independent tasks не обязательные review/fix child tasks. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | work-item rationale. |
| Final fields / conditions | {"decomposition":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["decomposition","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.decomposition","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| decomposition | agent | PREPARE | before_observe | sprint_requirements, definition_of_done, constraints |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_classification | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-03"></a>

#### 3. `task_classification` — содержательный этап

**GS-SPL-03.** Классифицировать каждый candidate.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, decomposition |
| По запросу | planning_target, product_requirements, task_requirements, sprint_requirements, definition_of_done, planning_inputs, constraints, assumptions, goal_classification, task_contracts, dependency_plan, execution_plan, shared_context_plan, shared_artifacts_plan, risks, planned_sprint, evidence, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | goal_classification, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | task_contracts, artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Для каждого один известный goal_type; batch create target-specific draft templates, no inheritance. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | classification registry/draft IDs. |
| Final fields / conditions | {"goal_classification":"always","task_contracts":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["goal_classification","task_contracts","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.task_classification","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| goal_classification | agent | PREPARE | before_observe | decomposition |
| task_contracts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | task_contracts | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-04"></a>

#### 4. `task_contracts` — содержательный этап

**GS-SPL-04.** Заполнить все draft tasks.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_contracts, decomposition, constraints |
| По запросу | planning_target, product_requirements, task_requirements, sprint_requirements, definition_of_done, planning_inputs, assumptions, goal_classification, dependency_plan, execution_plan, shared_context_plan, shared_artifacts_plan, risks, planned_sprint, evidence, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | task_contracts, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Каждый target creation validator, exact declared commands/DoD/applicability; вся группа draft ещё не eligible. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | per-task validation receipts. |
| Final fields / conditions | {"task_contracts":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["task_contracts","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.task_contracts","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| task_contracts | agent | PREPARE | before_observe | task_contracts, decomposition, constraints |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | dependency_design | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-05"></a>

#### 5. `dependency_design` — содержательный этап

**GS-SPL-05.** Спроектировать semantic prerequisites и materialization.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, task_contracts, decomposition |
| По запросу | planning_target, product_requirements, task_requirements, sprint_requirements, definition_of_done, planning_inputs, constraints, assumptions, goal_classification, dependency_plan, execution_plan, shared_context_plan, shared_artifacts_plan, risks, planned_sprint, evidence, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | dependency_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Same-sprint/no self/no cycle; exact needed result/artifact contract; не добавлять edge только по file overlap. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | DAG и delivery prerequisites. |
| Final fields / conditions | {"dependency_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["dependency_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.dependency_design","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| dependency_plan | agent | PREPARE | before_observe | task_contracts, decomposition |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | execution_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-06"></a>

#### 6. `execution_planning` — содержательный этап

**GS-SPL-06.** Определить priorities и shared context/resources.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, dependency_plan, task_contracts |
| По запросу | planning_target, product_requirements, task_requirements, sprint_requirements, definition_of_done, planning_inputs, constraints, assumptions, decomposition, goal_classification, execution_plan, shared_context_plan, shared_artifacts_plan, risks, planned_sprint, evidence, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | execution_plan, shared_context_plan, shared_artifacts_plan, risks, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Shared artifact owners/producers/consumers и resource modes; нет model scheduler. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | execution constraints. |
| Final fields / conditions | {"execution_plan":"always","shared_context_plan":"always","shared_artifacts_plan":"always","risks":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["execution_plan","shared_context_plan","shared_artifacts_plan","risks","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.execution_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| execution_plan | agent | PREPARE | before_observe | dependency_plan, task_contracts |
| shared_context_plan | agent | PREPARE | before_observe | dependency_plan, task_contracts |
| shared_artifacts_plan | agent | PREPARE | before_observe | dependency_plan, task_contracts |
| risks | agent | PREPARE | before_observe | dependency_plan, task_contracts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | coverage_analysis | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-07"></a>

#### 7. `coverage_analysis` — содержательный этап

**GS-SPL-07.** Проверить полноту пути от цели sprint к испытаниям.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, sprint_requirements, definition_of_done, task_contracts, dependency_plan |
| По запросу | planning_target, product_requirements, task_requirements, planning_inputs, constraints, assumptions, decomposition, goal_classification, execution_plan, shared_context_plan, shared_artifacts_plan, risks, planned_sprint, evidence, traceability, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | manual_evidence, planned_sprint, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | traceability, artifacts, evidence |
| Разрешённые изменения | task-data |
| Проверки | Every requirement→task→DoD→method, every task contribution; semantic sufficiency отдельно reviewed. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | coverage graph, validation receipts. |
| Final fields / conditions | {"manual_evidence":"always","planned_sprint":"always","traceability":"always","artifacts":"always","evidence":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["manual_evidence","planned_sprint","traceability","artifacts","evidence"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.coverage_analysis","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| manual_evidence | agent | PREPARE | before_observe | sprint_requirements, definition_of_done, task_contracts, dependency_plan |
| planned_sprint | agent | PREPARE | before_observe | sprint_requirements, definition_of_done, task_contracts, dependency_plan |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | sprint_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-08"></a>

#### 8. `sprint_inspection` — содержательный этап

**GS-SPL-08.** Read-only осмотреть весь план.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_sprint, task_contracts, dependency_plan, traceability, execution_plan |
| По запросу | planning_target, product_requirements, task_requirements, sprint_requirements, definition_of_done, planning_inputs, constraints, assumptions, decomposition, goal_classification, shared_context_plan, shared_artifacts_plan, risks, evidence, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Нет active unpublished mutations, все draft validators; review scope/false dependencies/coverage. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | semantic sprint review. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.sprint_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | planned_sprint, task_contracts, dependency_plan, traceability, execution_plan |
| findings | agent | PREPARE | before_observe | planned_sprint, task_contracts, dependency_plan, traceability, execution_plan |
| inspection_verdict | agent | PREPARE | before_observe | planned_sprint, task_contracts, dependency_plan, traceability, execution_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | publication | publish |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-09"></a>

#### 9. `remediation` — содержательный этап

**GS-SPL-09.** Исправить draft sprint без перезаписи выполненной истории.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_sprint, findings |
| По запросу | planning_target, product_requirements, task_requirements, sprint_requirements, definition_of_done, planning_inputs, constraints, assumptions, decomposition, goal_classification, task_contracts, dependency_plan, execution_plan, shared_context_plan, shared_artifacts_plan, risks, evidence, traceability, inspection_coverage, inspection_verdict, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | planned_sprint, task_contracts, dependency_plan, finding_resolutions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | task-data |
| Проверки | All changed target validators/DAG/coverage; current owner revisions; accepted results прошлого сохраняются. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | corrected draft evidence. |
| Final fields / conditions | {"planned_sprint":"always","task_contracts":"always","dependency_plan":"always","finding_resolutions":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["planned_sprint","task_contracts","dependency_plan","finding_resolutions","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.remediation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| planned_sprint | agent | PREPARE | before_observe | planned_sprint, findings |
| task_contracts | agent | PREPARE | before_observe | planned_sprint, findings |
| dependency_plan | agent | PREPARE | before_observe | planned_sprint, findings |
| finding_resolutions | agent | PREPARE | before_observe | planned_sprint, findings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | remediation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-10"></a>

#### 10. `remediation_inspection` — содержательный этап

**GS-SPL-10.** Осмотреть изменения плана.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_sprint, finding_resolutions, resolution_bindings |
| По запросу | planning_target, product_requirements, task_requirements, sprint_requirements, definition_of_done, planning_inputs, constraints, assumptions, decomposition, goal_classification, task_contracts, dependency_plan, execution_plan, shared_context_plan, shared_artifacts_plan, risks, evidence, traceability, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Resolution decisions и no silent unrelated changes. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | corrected sprint verdict. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of sprint_planning.remediation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | planned_sprint, finding_resolutions, resolution_bindings |
| findings | agent | PREPARE | before_observe | planned_sprint, finding_resolutions, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | planned_sprint, finding_resolutions, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | planned_sprint, finding_resolutions, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | publication | publish |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-SPL-11"></a>

#### 11. `publication` — автоматический gate

**GS-SPL-11.** Механически опубликовать принятый результат по явному разрешению пользователя; не отдельный содержательный этап.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, planned_sprint, task_contracts, dependency_plan, inspection_verdict |
| По запросу | planning_target, product_requirements, task_requirements, sprint_requirements, definition_of_done, planning_inputs, constraints, assumptions, decomposition, goal_classification, execution_plan, shared_context_plan, shared_artifacts_plan, risks, evidence, traceability, inspection_coverage, findings, finding_resolutions, result, decisions, user_feedback, artifacts, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | Нет новых обязательных полей модели. |
| OBSERVE | Проверить сохранённый accepted candidate, authority и preconditions; выполнить публикацию idempotently с receipt/probe. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | result, evidence, artifacts, traceability |
| Разрешённые изменения | task-data |
| Проверки | All target creation gates + DAG + coverage + revisions, одна transaction; любой invalid child — no publication. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | Exact candidate publication receipt, target identity/digest; operation эффект выполнен не более одного раза при известном receipt. |
| Final fields / conditions | {"result":"always","evidence":"always","artifacts":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["result","evidence","artifacts","traceability"],"subject":"accepted immutable candidate","evidence":"publication receipt and all prior required current evidence","phase_terminal":"GATE_SUCCEEDED","on_success":"task_completion_gate","negative_subject_outcome":"not allowed; blocked/error сохраняет candidate и не считает target опубликованным"} |
| Rework targets | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| Authority и отсутствие нового этапа | Однозначная publish instruction с exact candidate/target scope после принятия latest valid inspection. Accept-only запрещает запуск. Нет нового stage-result, self-check, content iteration или второго acceptance. |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| result | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| evidence | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| artifacts | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| traceability | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| gate_succeeded | task_completion_gate | already_authorized |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Sprint и все tasks опубликованы единым валидным set, no cross-sprint edges, methods/coverage complete, inspection accepted; execution не начинается автоматически. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Final published sprint+task contract delta, shared graph content один раз; не считать их повторно за каждую iteration.

Начатый sprint можно revise только для разрешённых неактивных owners; unrelated task changes не перезаписываются. Force-close user может прекратить draft без обычных gates.

## integration — независимый процесс

<a id="GP-INT"></a>

**GP-INT. Назначение:** Объединить существующие результаты с target codebase и доказать integrated result без новой самостоятельной функциональности.

**Создание:** goal/scope, integration_target exact old SHA/ref и private branch, integration_sources exact SHAs/result refs, task requirements/DoD сохранения source behavior, baseline required, exact declared targeted commands и push authority. Нормативный required creation field set приведён ниже; только он и явные применимости, без будущих stage outputs. Exact future invocations не требуют ещё не полученных hashes/receipts.

**Обязательные данные при создании:** `goal`, `scope`, `integration_target`, `integration_sources`, `product_requirements`, `task_requirements`, `definition_of_done`.

**Первый этап:** `framing`. **Baseline:** Target old SHA и все source SHAs обязательны, immutable per integration iteration.

### Секции и структуры

| Section | Содержимое / кто его создаёт |
| --- | --- |
| goal | statement; ожидаемый конечный результат; адресат результата. |
| scope | included codebases/subjects; excluded subjects; разрешённые deliverable paths/внешние targets; ограничения полномочий. |
| product_requirements | список requirement_id, codebase/path/entity/revision, формулировка относящейся части; explicit not_applicable+reason разрешено при отсутствии продуктового требования. |
| task_requirements | список id, statement, source_ref, обязательность, область; хотя бы одно требование. |
| definition_of_done | список id, requirement_refs, measurable criterion, expected deliverable, verification_obligation_refs; хотя бы один критерий. |
| integration_target | codebase, exact target old SHA/ref/remote, publication policy (FF expected-old guard), private integration ref. |
| integration_sources | список exact source commits и result/task refs, required delivered requirements, preserve/rewrite policy. |
| baseline | applicability required/not_applicable + reason; при required exact target revision/state и evidence refs. В профилях с mandatory baseline not_applicable запрещён. |
| integration_plan | explicit merge/rebase/cherry-pick strategy, order, private refs, conflicts policy, exact targeted checks и target publication predicate. |
| verification_plan | Obligations: requirement/DoD refs, exact PlannedInvocation HR-054 либо logical/inspection method, before/after predicates и phase applicability, reuse/retry/limits. BoundExecution digests/live identities добавляет AI poise при запуске; future test path известен, future hash не нужен. |
| conflicts | generated conflict ID, file/object, base/ours/theirs refs, source obligations; conflict не defect finding. |
| conflict_resolutions | После реального conflict receipt: conflict ID, rationale, retained source requirement refs, resolution proposal. При actual empty conflict set AI poise сам фиксирует no_conflicts; agent не выдумывает пустой verdict заранее. |
| constraints | список id, predicate/statement, source, область и способ проверки; explicit empty с причиной. |
| artifacts | Generated AI poise index: artifact id, owner, native/logical ref, bytes, digest, provenance, lifetime/retention. Агент не вводит эти наблюдения; смысловые требования — artifact_declarations. |
| evidence | Generated current evidence projection: execution receipts + зарегистрированные manual_evidence; outcome/validity/subject отдельны. Агент не перезаписывает индекс. |
| verdict | по каждому criterion/вопросу: proved/disproved/inconclusive/waived из явного набора; evidence и основания; product verdict отдельно от качества исполнения задачи. |
| inspection_coverage | subject result/revision, inspected units/criteria, skipped units+reason, supporting references; полнота scope проверяется структурно и отдельным semantic verdict. |
| findings | id, category subject_defect/task_quality/internal_QA, defect-of immutable result, origin cycle/iteration, subject, observed/expected, significance, evidence, requirement refs, current disposition; explicit empty с coverage/verdict. |
| inspection_verdict | accepted/rework_required; subject result refs, identified objections/findings, required follow-up stage; не изменяет осмотренный результат. |
| finding_resolutions | ResolutionProposal: finding ref, explanation, proposed substantive result, method refs и доступные manual evidence. Не содержит обязательного accepted/rejected будущего осмотра. AI poise resolution_bindings привязывает proposal к наблюдаемому result/receipts. |
| result | final substantive summary, выполненные DoD/verdicts, deliverables, limitations, Git/delivery vector, incidents; schema дополняется конкретным профилем. |
| decisions | Содержательные решения с authority/scope/source; пользовательские acceptance/publish хранит AI poise после semantic interpretation, а не выполняет автоматически из текста файла. |
| user_feedback | instruction reference, смысл, classify=accept/rework/requirement_change/cancel, target result/stage; создаётся AI poise из semantic input агента. |
| traceability | Generated view типизированных relations HR-095; historical provenance не распространяет stale. |
| handoff | current stage/iteration/submission, last accepted result, WIP/verified vector, outstanding findings/evidence/decisions, bundle/receipt; generated. |
| manual_evidence | Проверяемое агентское рассуждение: claim, facts, assumptions, inference summary, conclusion, source/subject refs. Не raw execution и не придуманная коллекция tests. |
| resolution_bindings | Generated proposal→result tree/section revision→actual evidence relations после CHECK; не агентские hashes. |
| resolution_review_decisions | Отдельные immutable records reviewer: proposal ref, reviewed exact result/evidence refs, accepted/rejected, rationale. Создаются только осмотром после proposal/bindings; rejected не удаляет прошлое. |
| artifact_paths | Вход агента: список путей файлов; пустой список передаётся явно. Файлы заранее размещены только в текущих runtime/task/sprint roots. Идентификатор присваивает AI poise. Дополнительные декларации, назначение, тип и hashes от агента не требуются. |

### Этапы и автоматические действия

<a id="GS-INT-01"></a>

#### 1. `framing` — содержательный этап

**GS-INT-01.** Зафиксировать source/target и нужные требования источников.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, integration_target, integration_sources, task_requirements, definition_of_done |
| По запросу | product_requirements, baseline, integration_plan, verification_plan, conflicts, conflict_resolutions, constraints, artifacts, evidence, verdict, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | constraints, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | baseline, artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Objects/commits/required source acceptance существуют; codebase identities согласованы; no accidental target mutation. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | target/source vector snapshot. |
| Final fields / conditions | {"constraints":"always","baseline":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["constraints","baseline","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of integration.framing","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| constraints | agent | PREPARE | before_observe | integration_target, integration_sources, task_requirements, definition_of_done |
| baseline | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | integration_planning | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-INT-02"></a>

#### 2. `integration_planning` — содержательный этап

**GS-INT-02.** Задать стратегию и exact checks.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, baseline, integration_sources, constraints, task_requirements |
| По запросу | product_requirements, definition_of_done, integration_target, integration_plan, verification_plan, conflicts, conflict_resolutions, artifacts, evidence, verdict, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | integration_plan, verification_plan, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Explicit merge/rebase/cherry-pick/private refs, target old guard, all required exact tests и resources. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | integration procedure. |
| Final fields / conditions | {"integration_plan":"always","verification_plan":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["integration_plan","verification_plan","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of integration.integration_planning","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| integration_plan | agent | PREPARE | before_observe | baseline, integration_sources, constraints, task_requirements |
| verification_plan | agent | PREPARE | before_observe | baseline, integration_sources, constraints, task_requirements |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | integration | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-INT-03"></a>

#### 3. `integration` — содержательный этап

**GS-INT-03.** Выполнить объединение и решить semantic conflicts.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, integration_plan, integration_sources, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, integration_target, baseline, conflicts, conflict_resolutions, constraints, artifacts, evidence, verdict, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить запланированную Git integration operation один раз по effect_id; сохранить actual conflicts/indices. При nonempty conflicts вернуть awaiting_agent с точными source/base/current refs. При empty зафиксировать no_conflicts автоматически. |
| Агент на CONTINUE | conflict_resolutions |
| Условие continuation | actual conflict set nonempty; proposals ссылаются только на IDs этого receipt; дерево может измениться при разрешении, это ожидаемый continuation, а не повтор merge. |
| Автоматические выходы | conflicts, artifacts, evidence, traceability |
| Разрешённые изменения | private integration worktree; code/tests/docs only to preserve source contracts |
| Проверки | После решений: unmerged index пуст, каждый conflict имеет proposal+rationale; exact targeted checks относятся к разрешённому дереву; private commit/push при успехе; target ref не изменять. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | integrated tree, conflict decisions, exact tests/private publication. |
| Final fields / conditions | {"conflicts":"always","artifacts":"always","evidence":"always","traceability":"always","conflict_resolutions":"actual_conflicts_nonempty"} |
| Когда нужен CONTINUE | actual_conflicts_nonempty |
| Условие результата | {"required_final":["conflicts","artifacts","evidence","traceability","conflict_resolutions"],"subject":"exact candidate subject vector","evidence":"current required obligations of integration.integration","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions","conditional_fields":{"conflict_resolutions":"actual_conflicts_nonempty; actual empty receipt satisfies no-conflict applicability without agent input"}} |
| Rework targets | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| conflicts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| conflict_resolutions | agent | CONTINUE | after_observation_if_actual_conflicts_nonempty | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | verification | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-INT-04"></a>

#### 4. `verification` — содержательный этап

**GS-INT-04.** Подтвердить совместный результат.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, verification_plan, integration_sources, evidence |
| По запросу | product_requirements, task_requirements, definition_of_done, integration_target, baseline, integration_plan, conflicts, conflict_resolutions, constraints, artifacts, verdict, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | artifact_paths |
| OBSERVE | Выполнить объявленные exact observation/check methods этой стадии, сохранить immutable receipts и compact facts до запроса полей: verdict. Неполученные результаты не предзаполнять. |
| Агент на CONTINUE | verdict |
| Условие continuation | Observation receipt terminal и доступен; агентский результат ссылается на этот receipt/subject. Повторный verify с этим receipt не повторяет command ради записи вывода. |
| Автоматические выходы | evidence, artifacts, traceability |
| Разрешённые изменения | task-data |
| Проверки | Exact integrated tests на новом tree/resource fingerprint; source evidence не reused как merged proof, no auto full suite. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | integrated verification receipts. |
| Final fields / conditions | {"evidence":"always","artifacts":"always","traceability":"always","verdict":"always"} |
| Когда нужен CONTINUE | post_observation_fields_declared |
| Условие результата | {"required_final":["evidence","artifacts","traceability","verdict"],"subject":"exact candidate subject vector","evidence":"current required obligations of integration.verification","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| evidence | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| artifacts | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| traceability | poise | OBSERVE | before_seal | bound_subject, operation_receipts |
| verdict | agent | CONTINUE | after_observation | observation_receipt, bound_subject |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-INT-05"></a>

#### 5. `inspection` — содержательный этап

**GS-INT-05.** Read-only проверить сохранность source intentions.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, integration_sources, conflict_resolutions, verdict, evidence, task_requirements |
| По запросу | product_requirements, definition_of_done, integration_target, baseline, integration_plan, verification_plan, conflicts, constraints, artifacts, inspection_coverage, findings, inspection_verdict, finding_resolutions, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Coverage source requirements/conflicts; target/private tree unchanged. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | semantic integration inspection. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of integration.inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | integration_sources, conflict_resolutions, verdict, evidence, task_requirements |
| findings | agent | PREPARE | before_observe | integration_sources, conflict_resolutions, verdict, evidence, task_requirements |
| inspection_verdict | agent | PREPARE | before_observe | integration_sources, conflict_resolutions, verdict, evidence, task_requirements |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | publication | publish |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-INT-06"></a>

#### 6. `remediation` — содержательный этап

**GS-INT-06.** Исправить дефекты объединения без новой функциональности.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, findings, integration_plan, verification_plan |
| По запросу | product_requirements, task_requirements, definition_of_done, integration_target, integration_sources, baseline, conflicts, conflict_resolutions, constraints, artifacts, evidence, verdict, inspection_coverage, inspection_verdict, finding_resolutions, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | finding_resolutions, conflict_resolutions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability, resolution_bindings |
| Разрешённые изменения | private integration result |
| Проверки | Affected checks+new tree private commit/push, no target publication; если нужна новая цель — отдельный development по пользователю. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | resolution evidence/receipts. |
| Final fields / conditions | {"finding_resolutions":"always","conflict_resolutions":"always","artifacts":"always","evidence":"always","traceability":"always","resolution_bindings":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["finding_resolutions","conflict_resolutions","artifacts","evidence","traceability","resolution_bindings"],"subject":"exact candidate subject vector","evidence":"current required obligations of integration.remediation","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| finding_resolutions | agent | PREPARE | before_observe | findings, integration_plan, verification_plan |
| conflict_resolutions | agent | PREPARE | before_observe | findings, integration_plan, verification_plan |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| resolution_bindings | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | remediation_inspection | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-INT-07"></a>

#### 7. `remediation_inspection` — содержательный этап

**GS-INT-07.** Осмотреть corrections.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, finding_resolutions, evidence, integration_sources, resolution_bindings |
| По запросу | product_requirements, task_requirements, definition_of_done, integration_target, baseline, integration_plan, verification_plan, conflicts, conflict_resolutions, constraints, artifacts, verdict, inspection_coverage, findings, inspection_verdict, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_review_decisions |
| Агент на PREPARE | inspection_coverage, findings, inspection_verdict, resolution_review_decisions, artifact_paths |
| OBSERVE | Получить binding/readiness и выполнить только exact методы, перечисленные данной stage/task/project applicability. При отсутствии методов сохранить структурный validation receipt, а не запускать приложение. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | artifacts, evidence, traceability |
| Разрешённые изменения | task-data |
| Проверки | Every resolution accepted/rejected, read-only target. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | resolution verdicts. |
| Final fields / conditions | {"inspection_coverage":"always","findings":"always","inspection_verdict":"always","resolution_review_decisions":"always","artifacts":"always","evidence":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["inspection_coverage","findings","inspection_verdict","resolution_review_decisions","artifacts","evidence","traceability"],"subject":"exact candidate subject vector","evidence":"current required obligations of integration.remediation_inspection","phase_terminal":"SEAL","on_success":"verified_stop","negative_subject_outcome":"allowed only if exact stage expectation permits; task-quality objections route via named transitions"} |
| Rework targets | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| inspection_coverage | agent | PREPARE | before_observe | finding_resolutions, evidence, integration_sources, resolution_bindings |
| findings | agent | PREPARE | before_observe | finding_resolutions, evidence, integration_sources, resolution_bindings |
| inspection_verdict | agent | PREPARE | before_observe | finding_resolutions, evidence, integration_sources, resolution_bindings |
| resolution_review_decisions | reviewer | PREPARE | before_observe | finding_resolutions, evidence, integration_sources, resolution_bindings |
| artifacts | poise | CHECK | before_seal | bound_subject, operation_receipts |
| evidence | poise | CHECK | before_seal | bound_subject, operation_receipts |
| traceability | poise | CHECK | before_seal | bound_subject, operation_receipts |
| artifact_paths | agent | PREPARE | explicit_list_empty_allowed | current runtime/task/sprint roots |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| accepted_clean | publication | publish |
| accepted_with_blocking_findings_and_selected_target | remediation | continue |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

<a id="GS-INT-08"></a>

#### 8. `publication` — автоматический gate

**GS-INT-08.** Механически опубликовать принятый результат по явному разрешению пользователя; не отдельный содержательный этап.

| Контракт | Требование |
| --- | --- |
| Входы / inline | goal, scope, integration_target, integration_plan, evidence, inspection_verdict |
| По запросу | product_requirements, task_requirements, definition_of_done, integration_sources, baseline, verification_plan, conflicts, conflict_resolutions, constraints, artifacts, verdict, inspection_coverage, findings, finding_resolutions, result, decisions, user_feedback, traceability, handoff, manual_evidence, resolution_bindings, resolution_review_decisions |
| Агент на PREPARE | Нет новых обязательных полей модели. |
| OBSERVE | Проверить сохранённый accepted candidate, authority и preconditions; выполнить публикацию idempotently с receipt/probe. |
| Агент на CONTINUE | Отсутствует; дополнительный вызов модели не нужен. |
| Условие continuation | none: post-observation fields отсутствуют; механические фазы проходят одним вызовом. |
| Автоматические выходы | result, evidence, artifacts, traceability |
| Разрешённые изменения | только exact target ref publication; рабочий tree уже проверен |
| Проверки | Accepted inspection, all required predicates current, expected target old SHA; FF update verified new SHA либо exact no-op containment. Drift → blocked/new user-authorized iteration; no force push. Артефакты: проверить существование файлов и принадлежность разрешённым текущим roots; при явном требовании посчитать уникальные файлы по настроенному признаку типа. Не выводить количество тест-кейсов из количества файлов. |
| Доказательства | Exact candidate publication receipt, target identity/digest; operation эффект выполнен не более одного раза при известном receipt. |
| Final fields / conditions | {"result":"always","evidence":"always","artifacts":"always","traceability":"always"} |
| Когда нужен CONTINUE | never |
| Условие результата | {"required_final":["result","evidence","artifacts","traceability"],"subject":"accepted immutable candidate","evidence":"publication receipt and all prior required current evidence","phase_terminal":"GATE_SUCCEEDED","on_success":"task_completion_gate","negative_subject_outcome":"not allowed; blocked/error сохраняет candidate и не считает target опубликованным"} |
| Rework targets | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |
| Authority и отсутствие нового этапа | Однозначная publish instruction с exact candidate/target scope после принятия latest valid inspection. Accept-only запрещает запуск. Нет нового stage-result, self-check, content iteration или второго acceptance. |

**Авторство и фаза обязательности:**

| Поле | Автор | Фаза появления | Обязательно | Основания |
| --- | --- | --- | --- | --- |
| result | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| evidence | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| artifacts | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |
| traceability | poise | CHECK | before_gate_success | accepted_inspection, publish_authority, immutable_candidate |

**Переходы:**

| Условие | Следующий узел | Требуемая инструкция |
| --- | --- | --- |
| gate_succeeded | task_completion_gate | already_authorized |

Переход выбирается после решения пользователя; `continue` не выводится из молчания. `awaiting_agent` — продолжение фактов/решения в той же iteration, не пользовательская приёмка. Указание `task_completion_gate` не создаёт содержательный этап.

### Повторный осмотр и завершение

Только typed requires_current edges распространяют stale; subject_snapshot evidence применимо своему exact subject. provenance/supersedes не инвалидируют correction через историю. Rework target выбирается явно; список allowed_rework_targets не разрешает автоматически запускать все перечисленные этапы.

Все outputs разделены producer/phase; отсутствующее поле не получает default. Generated views существуют с explicit empty result, но обязательное evidence по obligation не заменяется пустой view.

**Условный контракт исправления:**

```json
{
  "scope": "Only task-quality findings, not product findings delivered by review/verification",
  "proposal_input": {
    "field": "finding_resolutions",
    "producer": "agent",
    "phase": "PREPARE",
    "when": "active rework targets a blocking finding"
  },
  "binding_output": {
    "field": "resolution_bindings",
    "producer": "poise",
    "phase": "CHECK",
    "when": "proposal exists and resulting subject observed"
  },
  "inspection_input": {
    "field": "resolution_review_decisions",
    "producer": "reviewer",
    "phase": "PREPARE",
    "when": "inspection reviews existing bound proposals"
  },
  "history": "Each new proposal/decision immutable; no final review verdict inside proposal",
  "waiver": "user authority records disposition; waived proposal needs no fix evidence; supplied target findings still preserved"
}
```

**Completion:** Нет unresolved merge entries/conflicts, requirements source preserved по evidence/inspection, target publication observed (FF/no-op явно), all components receipts, последний этап accepted. Closure не требует второй приёмки механической publication; все обязательные reviews относятся к последней версии предмета. Inspecting a resolved proposal требует отдельного ResolutionReviewDecision.

**Польза:** Ноль content-volume benefit в общем суммировании; integrated source results/resolved conflicts как предметные measures; все tokens/time — integration cost.

Private publication на промежуточных этапах не означает target integrated. Conflicts/drift не являются source defect findings. Public source history не переписывается.



## 11. Декларативные инструменты и пользовательские сообщения

Обновлено: **2026-09-06T19:39:04+05:00**. Прикладная детализация: [пакетные инструменты](../architecture/declarative-tools.md) и [метрика сообщений](../operations/user-message-metrics.md).

<a id="HR-098"></a>

### HR-098. Декларативный пакет вместо ручного обслуживания

Каждый AI poise tool принимает множество однородных/связанных изменений одним логическим пакетом. Агент задаёт желаемое содержание, инструмент выполняет создание, форматирование, validation, регистрацию, сохранение и обновление проекций. Агенту запрещено напрямую изменять обслуживаемые config/task/sprint/runtime-файлы и SQLite. Общие pipelines не обходят доменные владельцы. Нативные IDE/patch tools исходников, тестов и документации целевой codebase сохраняются; Git остаётся источником их изменений.

<a id="HR-099"></a>

### HR-099. Пакетный редактор goal-type конфигурации

GoalConfig API поддерживает create/change самодостаточного process pack по устойчивым ID объектов. При change используется известный исходный snapshot и явные изменения; итоговый кандидат валидируется целиком, ссылки могут создаваться в том же пакете. При ошибке действующий конфиг не публикуется частично. Полный файл генерирует инструмент. Повтор возвращает результат существующей операции; active task/process snapshots не мигрируют. Отдельные generate/validate/publish вызовы не обязательны.

<a id="HR-100"></a>

### HR-100. Явные шаблоны без скрытых defaults

Стандартные конфиги и артефакты создаются из явно выбранного шаблона с revision/digest. Его выбор принимает содержимое шаблона; placeholders остаются незаполненными и блокируют необходимую публикацию. Отсутствующее required значение нельзя взять из fallback. Где шаблон бессмыслен, инструмент принимает готовое содержание и сам создаёт файл. Новые goal-type packs полностью материализуются и независимы от других packs.

<a id="HR-101"></a>

### HR-101. Пакетная фабрика артефактов

Для новых артефактов один пакет задаёт scope runtime/task/sprint, относительное имя/явное правило имени и template parameters или готовое содержание. Инструмент вычисляет разрешённый root, создаёт файлы, назначает ID и регистрирует фактические свойства. Уже существующий артефакт по-прежнему принимается только путём; смысловые декларации, hashes и timestamps от агента не требуются. Неизменные evidence не перезаписываются. Повтор не умножает артефакты.

<a id="HR-102"></a>

### HR-102. Прямой результат этапа и пакетное чтение

Verify принимает содержательный пакет непосредственно из tool payload/stdin; при необходимости сам создаёт техническое stage-result представление. Секции, методы, трассировка, findings/resolutions, доказательства и создание/регистрация артефактов могут поступить вместе. Кандидат проверяется с учётом всех новых данных. Show/read принимает коллекцию адресуемых объектов/диапазонов. Получение новых наблюдений по-прежнему может потребовать нового аргумента CONTINUE, но не механической серии register-вызовов.

<a id="HR-103"></a>

### HR-103. Границы применения пакета

Batch transport/UoW/reporting общий, но Config/Task/Sprint/Artifacts/Accounting сохраняют владельцев правил. Запрещён универсальный raw SQL/file editor. Лимиты явные; внутреннюю обработку порциями делает инструмент, не агент. Отказ не называется частичным успехом. Файлы/Git/SQLite не объявляются одной транзакцией; активируется только согласованный результат, внешние эффекты имеют receipt. Пакетность не разрешает самовольное изменение policy или полный suite.

<a id="HR-104"></a>

### HR-104. Пользовательские сообщения — отдельная метрика

user_messages_count считает различные сообщения пользователя, относящиеся к работе, независимо от доступности token usage. Учитываются поручения, продолжения, приёмка, замечания, уточнения, данные, разрешения и отмена. Несколько вложений одного сообщения не создают новые сообщения. Tool calls, system/assistant сообщения и автоматические повторы не входят. Нельзя преобразовывать этот счётчик в приблизительные токены или число дефектов.

<a id="HR-105"></a>

### HR-105. Источник и дедупликация сообщений

Событие сообщения поступает из явно configured runtime_event либо agent_reported через общий рабочий вход, без отдельного обязательного вызова. Один source-scoped message/turn key используется bootstrap/verify/CONTINUE и повторным импортом. Неизвестная история не оценивается по числу команд. Source/coverage/event-time provenance сохраняются; reported/partial/unavailable не выдаются за полный автоматический счётчик. Полный текст сообщений и вложения для подсчёта не требуются.

<a id="HR-106"></a>

### HR-106. Атрибуция и отчёты сообщений

Каждое сообщение имеет один основной work binding, который может быть разрешён после выбора task; связанные ссылки не добавляют события. Отчёты task/sprint/goal_type/project/day/week/all считают unique events и показывают остаток scope-only/unattributed. Затраты отменённой задачи сохраняются при нулевой пользе; handoff/reopen не сбрасывает счётчик. Периоды используют явную timezone и надёжное время источника либо обозначенное ограничение времени приёма. Неполные данные не превращаются в ноль.
